"""Membership applications: a guest who has visited enough is emailed a private link, fills in the form (the core
details, the club's own questions, the club's statement, a typed signature and a sponsor), and the sponsor confirms.

Kept in applications.json in the data folder ({"items": [...]}, mode 0600), read and changed under applications.lock
(`edit`), by the web app and the daily job alike. One item per application:
  id, guest (the name on the Guests page), email, state, updated (when it last changed),
  token_hash, sent, expires, used     the link: only a hash is kept; it works until `expires` and once
  form {name, callsign, first_licensed, license_class, expires, street, city, state, zip, phone, email, occupation}
  answers [{label, type, value}]      the club's questions as they were asked, so a later edit of them changes nothing
  statement, signed {name, at}        the club's statement shown above the signature, and the typed signature (name and time; no address is kept)
  sponsor {typed, id, asked, answer, at}  what the applicant typed; the member id (None until it matched exactly one
                                      Active member, or someone on the Guests page picked one); answer None, "yes" or "no"
  club {voted, initiated, fee_paid, bond_paid, ballot}   "For the club" on the PDF, empty until the vote and the fees
                                      (the fees are read from the Treasurer's records when the PDF is made); ballot: the
                                      vote recorded in a meeting's console {meeting, yes, no, passed} or {meeting, accepted,
                                      passed}, settled when those minutes are submitted (`settle`)
  ended, member                       when it was turned down or withdrawn; the member id it led to
  paper, paper_by                     True for one handed in on paper and recorded on the Guests page (`record_paper`), and
                                      the member who recorded it: no link, no email, the form holding only the name
state: sent (link out), submitted (waiting for the sponsor), declined (the sponsor said no; the applicant may be sent a
new link to pick another), candidate (sponsored: for the club to vote on), turned_down, withdrawn, member.
Part of this is personal: `purge` (the daily job) deletes what the settings say, and nothing here is logged.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
import time
import unicodedata
from zoneinfo import ZoneInfo

from . import auth, branding, meetings, richtext
from .club import (
    DATA,
    INVITE_WORDS,
    VOTE_PASSES,
    Club,
    must_answer,
    show_date,
    valid_email,
)
from .membership import CLASSES, split_name

log = logging.getLogger("club_minutes.applications")
NO_LICENSE = "None yet"
SHOWN = {"sent": "Application sent", "submitted": "Waiting for the sponsor", "declined": "Sponsor declined",
         "candidate": "Candidate", "turned_down": "Turned down", "withdrawn": "Withdrawn", "member": "Became a member"}
MONTH = 30.4375 * 86400
OWN_COPY_SECONDS = 3600  # the applicant may download their own PDF from the thank-you page this long after sending it
SECTION = "Membership Applications"  # the minutes' section for candidates, made by the console


def _path():
    return DATA / "applications.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text()) if _path().exists() else {"items": []}
    except ValueError:
        log.exception("%s is damaged", _path())
        raise


@contextlib.contextmanager
def edit():
    """The file read under its lock, and written back (atomically, mode 0600) when the block ends without an error."""
    DATA.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(DATA / "applications.lock", os.O_WRONLY | os.O_CREAT, 0o600), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        s = load()
        yield s["items"]
        tmp = _path().with_name("applications.tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:  # never readable by others, even briefly
            f.write(json.dumps(s, indent=1))
        tmp.replace(_path())


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def find(token: str) -> dict | None:
    h = _hash(token or "")
    return next((a for a in load()["items"] if a.get("token_hash") == h), None)


def usable(a: dict | None, now: float | None = None) -> bool:
    """A link that works: not used, not expired, and the applicant not past the form."""
    return bool(a) and a["state"] == "sent" and not a.get("used") and a["expires"] > (now or time.time())


def for_guest(items: list[dict], name: str) -> dict | None:
    """The guest's latest application."""
    mine = [a for a in items if _same(a["guest"], name)]
    return max(reversed(mine), key=lambda a: a["sent"], default=None)  # a tie (the same second): the one added last


def turned_down_recently(club: Club, name: str, email: str, now: float | None = None) -> bool:
    """Someone turned down less than `reapply_months` ago, by name or the same email."""
    window = club.membership["reapply_months"] * MONTH
    return any(a["state"] == "turned_down" and a["ended"] + window > (now or time.time())
               and (_same(a["guest"], name) or (email and a["email"].lower() == email.lower())) for a in load()["items"])  # a paper one has no email


def club_name(club: Club) -> str:
    return " ".join(str(club.info.get("name") or club.label).split())


def next_meeting(club: Club, now: float) -> str:
    """"Thursday, December 3 at 7:30 PM": the next club meeting not yet started, in the club's time zone; empty if none."""
    here = dt.datetime.fromtimestamp(now, ZoneInfo(club.timezone)).replace(tzinfo=None)
    for d in meetings.nights(club, here.date(), here.date() + dt.timedelta(days=60)):
        at = dt.datetime.combine(d, dt.time.fromisoformat(meetings.time_on(club, d)))
        if at > here:
            return f"{show_date(d, 'weekday')} at {at:%-I:%M %p}"
    return ""


def send(club: Club, guest: str, email: str, url: str, now: float | None = None, visits: int = 0) -> None:
    """Email the guest their application link, replacing an unused or declined one. ValueError says why not."""
    now = now or time.time()
    email = " ".join(email.split())
    if not valid_email(email):
        raise ValueError("That doesn't look like an email address.")
    if turned_down_recently(club, guest, email, now):
        raise ValueError(f"They applied before, and the club turned them down less than {club.membership['reapply_months']} months ago.")
    token = secrets.token_urlsafe(32)
    with edit() as items:
        a = for_guest(items, guest)
        if a and a["state"] in ("submitted", "candidate", "member"):
            raise ValueError(f"They have applied already ({SHOWN[a['state']].lower()}).")
        if not a or a["state"] in ("turned_down", "withdrawn"):
            a = {"id": "a" + secrets.token_hex(5), "guest": " ".join(guest.split())[:100], "club": dict.fromkeys(
                ("voted", "initiated", "fee_paid", "bond_paid"), "")}
            items.append(a)
        a.pop("paper", None)  # a new link after a paper one the sponsor declined: online from here
        a.update(email=email, state="sent", token_hash=_hash(token), sent=int(now), used=None, updated=int(now),
                 expires=int(now + club.membership["link_days"] * 86400))
    days = club.membership["link_days"]
    words = {"guest": guest, "first_name": split_name(guest)[0], "club": club_name(club), "short": club.label, "link": f"{url}/apply/{token}",
             "days": f"{days} day{'s' * (days != 1)}", "until": show_date(dt.datetime.fromtimestamp(now + days * 86400).date(), "document"),
             "next_meeting": next_meeting(club, now), "visits": str(visits), "bylaws": f"{url}/apply/{token}/bylaws"}
    body = re.sub(r"\{(" + "|".join(INVITE_WORDS) + r")\}", lambda m: words[m[1]], club.membership["invite"])  # one pass: a name with {link} in it stays as typed
    auth.send_email(email, f"Your application to {club_name(club)}", only_to=club.email_only_to(), to_self=True,
                    body=f"{body}\n\n{club.sign_off}\n{branding.name(club)}")


def sponsor_match(club: Club, typed: str) -> str | None:
    """The Active member's id when what was typed (full name, case and spaces ignored, or callsign) fits exactly one."""
    hits = [m.id for m in club.members.values() if m.status.lower() == "active"
            and (_same(m.name, typed) or (club.ham and m.callsign and _same(m.callsign, typed)))]
    return hits[0] if len(hits) == 1 else None


def qhash(club: Club) -> str:
    """A short hash of the club's questions and field settings, carried in the form to notice a change between showing and sending it."""
    return hashlib.sha256(json.dumps([club.membership["questions"], club.membership["fields"]], sort_keys=True).encode()).hexdigest()[:12]


def _year(v: str) -> str:
    if v and not (v.isascii() and v.isdecimal() and 1900 <= int(v) <= dt.date.today().year):
        raise ValueError("First licensed: a year, like 1998.")
    return v


def _clean(v: str, keep_newlines: bool = False) -> str:
    """Without control and format characters (bidi marks, zero-width and the like): they can hide or reorder what's shown."""
    return "".join(c for c in v if (keep_newlines and c == "\n") or unicodedata.category(c) not in ("Cc", "Cf"))


def _text(form, key: str, label: str, size: int, need: bool = True) -> str:
    v = _clean(" ".join(str(form.get(key) or "").split()))
    if need and not v:
        raise ValueError(f"{label} is needed.")
    if len(v) > size:
        raise ValueError(f"{label} is too long (at most {size} characters).")
    return v


def _answers(club: Club, form) -> list[dict]:
    out = []
    for i, q in enumerate(club.membership["questions"]):
        got, must = form.getlist(f"q{i}"), must_answer(q)
        if q["type"] in ("yesno", "one"):
            value = got[0] if got else ""
            if value not in (("Yes", "No") if q["type"] == "yesno" else q["options"]) and (must or value):
                raise ValueError(f"Please answer: {q['label']}")
        elif q["type"] == "several":
            if bad := [g for g in got if g not in q["options"]]:
                raise ValueError(f"{q['label']}: {bad[0]!r} isn't one of the choices.")
            value = [o for o in q["options"] if o in got]
        elif q["type"] == "grid":
            ticks = set(got)
            if bad := ticks - {f"{r}.{c}" for r in range(len(q["rows"])) for c in range(len(q["cols"]))}:
                raise ValueError(f"{q['label']}: that tick isn't in the grid.")
            value = {"cols": q["cols"], "rows": {row: [c for ci, c in enumerate(q["cols"]) if f"{ri}.{ci}" in ticks]
                                                  for ri, row in enumerate(q["rows"])}}
        else:
            value = _clean(str(got[0]).replace("\r\n", "\n"), True).strip() if got else ""
            if len(value) > (200 if q["type"] == "short" else 3000):
                raise ValueError(f"{q['label']} is too long.")
        if must and not (any(value["rows"].values()) if q["type"] == "grid" else value):
            raise ValueError(f"Please answer: {q['label']}")
        out.append({"label": q["label"], "type": q["type"], "value": value})
    return out


def check(club: Club, form) -> tuple[dict, list[dict], str]:
    """(the core details, the answers, the sponsor as typed) from the form, or a ValueError saying what's wrong."""
    if form.get("qh") != qhash(club):
        raise ValueError("The club changed its questions while you were filling this in. Please look over your answers below and send it again.")
    if re.search(r"://|www\.", str(form.get("name") or ""), re.IGNORECASE):  # the name goes in the minutes, which link web addresses
        raise ValueError("Your name can't include a web address.")
    modes = club.membership["fields"]

    def field(key: str, label: str, size: int) -> str:  # hidden: never read from the form
        return "" if modes[key] == "hidden" else _text(form, key, label, size, modes[key] == "required")
    f = {"name": _text(form, "name", "Your name", 100), "email": _text(form, "email", "Email", 254),
         "street": field("street", "Street address", 100), "city": field("city", "City", 60),
         "state": field("state", "State", 40), "zip": field("zip", "ZIP code", 12),
         "phone": field("phone", "Phone", 30), "occupation": field("occupation", "Occupation", 100)}
    if not valid_email(f["email"]):
        raise ValueError("That doesn't look like an email address.")
    if f["phone"] and not re.fullmatch(r"[\d\s()+.\-x]{7,30}", f["phone"]):
        raise ValueError("Phone: digits, spaces and + ( ) - . only.")
    if club.ham:
        f["callsign"] = field("callsign", "Callsign", 7).upper()
        if f["callsign"] and not re.fullmatch(r"[A-Z0-9]{3,7}", f["callsign"]):
            raise ValueError(f"{f['callsign']} doesn't look like a callsign.")
        f["first_licensed"] = _year(field("first_licensed", "First licensed", 4))
        f["license_class"] = field("license_class", "License class", 20)
        if f["license_class"] not in ("", NO_LICENSE, *CLASSES):
            raise ValueError("Pick a license class from the list.")
        f["expires"] = field("expires", "License expiry", 10)
        try:
            f["expires"] = dt.date.fromisoformat(f["expires"]).isoformat() if f["expires"] else ""
        except ValueError:
            raise ValueError("License expiry isn't a date.") from None
    answers = _answers(club, form)
    if not form.get("accept"):
        raise ValueError("Tick that you accept the conditions.")
    if not _same(_text(form, "signature", "Your signature", 100), f["name"]):
        raise ValueError("Type your full name exactly as you wrote it above, to sign.")
    return f, answers, _text(form, "sponsor", "Your sponsor", 100, False)


def submit(club: Club, token: str, form, now: float | None = None) -> dict:
    """Store a filled-in application and use up its link. Raises ValueError (what's wrong, or a link that no longer works)."""
    now = int(now or time.time())
    with edit() as items:
        a = next((x for x in items if x.get("token_hash") == _hash(token)), None)
        if not usable(a, now):
            raise ValueError("This link has expired or was already used.")
        f, answers, typed = check(club, form)
        sponsor = sponsor_match(club, typed)
        a.update(state="submitted", used=now, updated=now, form=f, answers=answers, statement=club.membership["statement"],
                 signed={"name": _text(form, "signature", "Your signature", 100), "at": now},
                 sponsor={"typed": typed, "id": sponsor, "asked": now if sponsor else None, "answer": None, "at": None}, email=f["email"])
        return json.loads(json.dumps(a))


def holders(club: Club) -> list[str]:
    """Email addresses of those who receive applications (the permission), Active members with an address."""
    return [m.email for m in club.members.values() if m.email and m.status.lower() == "active" and club.may(m.id, "applications")]


def _ask(club: Club, a: dict, url: str) -> None:
    sponsor, name = club.members[a["sponsor"]["id"]], a["form"]["name"]
    if sponsor.email:
        auth.send_email(sponsor.email, f"Will you sponsor {name}?", only_to=club.email_only_to(), body=(
            f"Hello {sponsor.first},\n\n{name} has applied to join {club_name(club)} and named you as their sponsor. "
            f"Please sign in and say whether you will:\n\n{url}/applications/{a['id']}/sponsor\n\n{club.sign_off}\n{branding.name(club)}"))


def notify_submitted(club: Club, a: dict, url: str) -> None:
    """After the form: the sponsor is asked (when the typed name matched a member); the holders are told it's on the Guests
    page and the applicant that it arrived. No PDF goes by email (it's on the Guests page). A failure to send is only logged."""
    name, only, sid = a["form"]["name"], club.email_only_to(), a["sponsor"]["id"]
    who = f"Their sponsor, {club.name(sid)}, has been asked to confirm." if sid else (
        (f"They typed \"{a['sponsor']['typed']}\" as their sponsor, which isn't one member" if a["sponsor"]["typed"] else "They named no sponsor")
        + ": pick a member on the Guests page.")
    try:
        if sid:
            _ask(club, a, url)
        if to := holders(club):
            auth.send_email(to, f"Membership application: {name}", only_to=only, body=(
                f"{name} has submitted a membership application. {who}\n\nIt's on the Guests page: {url}/guests\n\n{club.sign_off}\n{branding.name(club)}"))
        auth.send_email(a["form"]["email"], f"Your application to {club_name(club)}", only_to=only, to_self=True, body=(
            f"Hello {name},\n\nThank you for applying to {club_name(club)}: your application has arrived. "
            f"Your sponsor will be asked to confirm, and the club will be in touch.\n\n{club.sign_off}\n{branding.name(club)}"))
    except Exception:
        log.exception("couldn't send the emails for application %s", a["id"])


def record_paper(club: Club, guest: str, mid: str, signed: bool, url: str, by: str, now: float | None = None) -> dict:
    """A paper application handed in at a meeting: from here it follows the online path (the sponsor asked, or already
    confirmed when they signed the paper; then listed, voted on, added). The details stay on the paper until Add a
    member. `by`: who recorded it (kept, and on the PDF: no one confirmed a signed one in the app). ValueError says why not."""
    now = int(now or time.time())
    if mid not in club.members or club.members[mid].status.lower() != "active":
        raise ValueError("Pick an Active member as the sponsor.")
    if signed and mid == by:
        raise ValueError("Someone else records a paper application you signed as the sponsor.")
    if not signed and not club.members[mid].email:
        raise ValueError(f"{club.name(mid)} has no email address to be asked: tick that they signed the paper, or pick another sponsor.")
    if turned_down_recently(club, guest, "", now):
        raise ValueError(f"They applied before, and the club turned them down less than {club.membership['reapply_months']} months ago.")
    form = dict.fromkeys(("name", "email", "street", "city", "state", "zip", "phone", "occupation"), "") | {"name": " ".join(guest.split())[:100]}
    if club.ham:
        form |= dict.fromkeys(("callsign", "first_licensed", "license_class", "expires"), "")
    with edit() as items:
        a = for_guest(items, guest)
        if a and a["state"] in ("submitted", "candidate", "member"):
            raise ValueError(f"They have applied already ({SHOWN[a['state']].lower()}).")
        if not a or a["state"] != "sent":  # an unfilled link is replaced; one filled in online (declined, ended) is kept as it was
            a = {"id": "a" + secrets.token_hex(5), "guest": form["name"], "club": dict.fromkeys(("voted", "initiated", "fee_paid", "bond_paid"), "")}
            items.append(a)
        a.update(paper=True, paper_by=by, email="", state="candidate" if signed else "submitted", token_hash=None, sent=now, used=now, updated=now,
                 expires=now, form=form, answers=[], statement="", signed={"name": form["name"], "at": now},
                 sponsor={"typed": "", "id": mid, "asked": now, "answer": "yes" if signed else None, "at": now if signed else None, "on_paper": signed})
        a = json.loads(json.dumps(a))
    if not signed:
        try:
            _ask(club, a, url)
        except Exception:
            log.exception("couldn't send the emails for application %s", a["id"])
    return a


def ask_sponsor(club: Club, aid: str, mid: str, url: str, now: float | None = None) -> None:
    """Someone with the permission picks the member for a sponsor the applicant typed that matched no one: they are asked
    as if it had matched. ValueError says why not."""
    now = int(now or time.time())
    with edit() as items:
        a = next((x for x in items if x["id"] == aid), None)
        if not a or a["state"] != "submitted" or a["sponsor"].get("id"):
            raise ValueError("That application isn't waiting for a sponsor to be picked.")
        if mid not in club.members or club.members[mid].status.lower() != "active":
            raise ValueError("Pick an Active member.")
        a["sponsor"].update(id=mid, asked=now)
        a["updated"] = now
        a = json.loads(json.dumps(a))
    try:
        _ask(club, a, url)
    except Exception:
        log.exception("couldn't send the emails for application %s", aid)


def sponsor_answer(club: Club, aid: str, mid: str, yes: bool, now: float | None = None) -> dict:
    """The sponsor's answer. ValueError if this isn't theirs to answer (or has been answered)."""
    now = int(now or time.time())
    with edit() as items:
        a = next((x for x in items if x["id"] == aid), None)
        if not a or a["state"] != "submitted" or a["sponsor"]["id"] != mid:
            raise ValueError("This application doesn't need your answer.")
        a["sponsor"].update(answer="yes" if yes else "no", at=now)
        a.update(state="candidate" if yes else "declined", updated=now)
        return json.loads(json.dumps(a))


def notify_sponsor_answered(club: Club, a: dict, url: str) -> None:
    """The holders are told (a link to the PDF on the Guests page, not the PDF again); a no also tells the applicant."""
    name, sponsor, only = a["form"]["name"], club.name(a["sponsor"]["id"]), club.email_only_to()
    try:
        if to := holders(club):
            if a["state"] == "candidate":
                auth.send_email(to, f"{name} is sponsored", only_to=only, body=(
                    f"{sponsor} has confirmed they sponsor {name}, who is now a candidate. The application, with their confirmation, is "
                    f"on the Guests page ({url}/guests): sign in to download the PDF.\n\n{club.sign_off}\n{branding.name(club)}"))
            else:
                auth.send_email(to, f"{sponsor} can't sponsor {name}", only_to=only, body=(
                    f"{sponsor} can't sponsor {name}. The application waits: send {name} a new link on the Guests page to pick another sponsor.\n\n"
                    f"{club.sign_off}\n{branding.name(club)}"))
        if a["state"] == "declined" and a["form"]["email"]:  # a paper one has no email
            auth.send_email(a["form"]["email"], f"Your application to {club_name(club)}", only_to=only, to_self=True, body=(
                f"Hello {name},\n\nYour sponsor couldn't confirm your application to {club_name(club)}. The club will be in touch.\n\n"
                f"{club.sign_off}\n{branding.name(club)}"))
    except Exception:
        log.exception("couldn't send the emails for application %s", a["id"])


def _end(aid: str, state: str, now: float | None) -> None:
    with edit() as items:
        a = next((x for x in items if x["id"] == aid), None)
        if not a:
            raise ValueError("No such application.")
        a.update(state=state, ended=int(now or time.time()), updated=int(now or time.time()))


def turn_down(aid: str, now: float | None = None) -> None:
    """The club turned them down (for part B's vote): they can't be sent a new link for `reapply_months`."""
    _end(aid, "turned_down", now)


def withdraw(aid: str, now: float | None = None) -> None:
    """They withdrew, or the club set it aside without turning them down."""
    _end(aid, "withdrawn", now)


def _since(a: dict) -> float:
    """A candidate's clock for the purge: the day the vote passed, else the day they became a candidate."""
    voted = a["club"].get("voted")
    return dt.datetime.fromisoformat(voted).timestamp() if voted else a["sponsor"]["at"]


def _purge_one(a: dict, items: list[dict], now: float, keep: float, window: float) -> int:
    """Delete or trim one application as `purge` says; 1 if it changed."""
    s = a["state"]
    if s == "turned_down":
        if a["ended"] + max(window, keep) < now:
            items.remove(a)
        elif a["ended"] + keep < now and "form" in a:
            for k in ("form", "answers", "statement", "signed", "sponsor", "club", "token_hash"):
                a.pop(k, None)
        else:
            return 0
    elif s == "member" and a["updated"] + keep < now or s == "candidate" and _since(a) + keep < now or (s == "sent" and a["expires"] + keep < now) \
            or (s in ("submitted", "declined") and a["updated"] + keep < now) or (s == "withdrawn" and a["ended"] + keep < now):
        items.remove(a)
    else:
        return 0
    return 1


def purge(club: Club, now: float | None = None) -> int:
    """The daily job: delete what's kept too long, `keep_days` after the link ran out unused, after the sponsor said no or
    never answered, or after a withdrawal; a turn-down loses its answers and keeps only name and email until the
    reapply period ends, so the refusal can still be checked; a new member's item is deleted `keep_days`
    after joining (the roster holds their details); a candidate never added as a member goes `keep_days` after the
    vote passed, or after becoming a candidate if no vote passed. Returns how many items were changed or deleted."""
    now = now or time.time()
    keep, window = club.membership["keep_days"] * 86400, club.membership["reapply_months"] * MONTH
    n = 0
    with edit() as items:
        for a in list(items):
            try:  # one damaged item must not stop the rest
                n += _purge_one(a, items, now, keep, window)
            except Exception:
                log.exception("Purge skipped application %s", a.get("id") if isinstance(a, dict) else "?")
    return n


# ---- part B: the candidate in the minutes, the vote, becoming a member ----

def passed(rule: str, yes: int, no: int) -> bool:
    """Whether a vote passes under `vote_passes`: unanimous (no "no" and at least one yes), two_thirds (of yes + no), majority."""
    if rule == "unanimous":
        return yes > 0 and no == 0
    if rule == "two_thirds":
        return yes > 0 and yes * 3 >= 2 * (yes + no)
    return yes > no


def listed(club: Club, items: list[dict], date: dt.date, dates: list[dt.date]) -> list[tuple[dict, dt.date]]:
    """[(application, day the vote may be recorded)] for candidates not yet voted on, listed at a meeting on `date`: every
    meeting after the day they became a candidate. The vote waits `listed_days` after the first of those (`dates`: the meetings)."""
    out = []
    for a in items:
        if a["state"] != "candidate" or a["club"].get("voted") or date <= (since := dt.date.fromtimestamp(a["sponsor"]["at"])):
            continue
        out.append((a, min(d for d in {*dates, date} if d > since) + dt.timedelta(days=club.membership["listed_days"])))
    return out


def _who(club: Club, a: dict, markup: bool = False) -> str:
    """Name (and callsign) as typed by the applicant; `markup`: escaped, for a line of the minutes' richtext."""
    return (richtext.escape(a["form"]["name"]) if markup else a["form"]["name"]) + (f" ({a['form']['callsign']})" if club.ham and a["form"].get("callsign") else "")


def section_lines(club: Club, items: list[dict], slug: str, dates: list[dt.date]) -> list[str]:
    """The minutes' lines for the candidates: names only, and the vote's counts once recorded at this meeting."""
    out = []
    for a, _ in listed(club, items, dt.date.fromisoformat(slug), dates):
        b, name = a["club"].get("ballot") or {}, richtext.escape(a["form"]["name"])
        if b.get("meeting") != slug:
            out.append(f"{_who(club, a, True)} has applied for membership, sponsored by {club.name(a['sponsor']['id'])}.")
        elif b.get("accepted"):
            out.append(f"{name}'s application was accepted.")
        else:
            out.append(f"{name}'s application was voted on: {'passed' if b['passed'] else 'not passed'} ({b['yes']}\u2013{b['no']}).")
    return out


def console_view(club: Club, items: list[dict], slug: str, dates: list[dt.date]) -> dict:
    """What the console shows for this meeting: each candidate, when their vote may be recorded, and what was recorded."""
    date = dt.date.fromisoformat(slug)
    return {"vote": club.membership["vote"], "rule": VOTE_PASSES[club.membership["vote_passes"]], "lines": section_lines(club, items, slug, dates),
            "people": [{"id": a["id"], "name": _who(club, a), "sponsor": club.name(a["sponsor"]["id"]), "can_vote": date >= due,
                        "due": show_date(due, "long"),
                        "ballot": b if (b := a["club"].get("ballot") or {}).get("meeting") == slug else None}
                       for a, due in listed(club, items, date, dates)]}


def record_vote(club: Club, aid: str, slug: str, dates: list[dt.date], yes=None, no=None) -> None:
    """The count from the paper ballot (or, with `vote` off, that the officers accepted them), replacing one recorded
    earlier at this meeting. ValueError says why not."""
    date = dt.date.fromisoformat(slug)
    with edit() as items:
        found = next(((a, due) for a, due in listed(club, items, date, dates) if a["id"] == aid), None)
        if not found:
            raise ValueError("That application isn't up for a vote at this meeting.")
        a, due = found
        if date < due:
            raise ValueError(f"The vote can't be recorded until {show_date(due, 'long')}.")
        if not club.membership["vote"]:
            a["club"]["ballot"] = {"meeting": slug, "accepted": True, "passed": True}
            return
        if not all(type(n) is int and 0 <= n <= 9999 for n in (yes, no)):
            raise ValueError("Enter the yes and no counts as whole numbers (0 if none).")
        a["club"]["ballot"] = {"meeting": slug, "yes": yes, "no": no, "passed": passed(club.membership["vote_passes"], yes, no)}


def settle(slug: str) -> None:
    """When a meeting's minutes are submitted: a vote recorded there that passed (or an acceptance) sets the date voted on;
    one that didn't turns the application down."""
    failed = []
    with edit() as items:
        for a in items:
            b = a.get("club", {}).get("ballot") or {}
            if a["state"] == "candidate" and not a["club"].get("voted") and b.get("meeting") == slug:
                if b["passed"]:
                    a["club"]["voted"] = slug
                else:
                    failed.append(a["id"])
    for aid in failed:
        turn_down(aid)


def became_member(aid: str, mid: str, now: float | None = None) -> None:
    """They were added to the roster from their application: it points at the member, with today as the initiation date."""
    now = now or time.time()
    with edit() as items:
        a = next((x for x in items if x["id"] == aid), None)
        if a and a["state"] == "candidate" and a["club"].get("voted"):
            a.update(state="member", member=mid, updated=int(now))
            a["club"]["initiated"] = dt.date.fromtimestamp(now).isoformat()


def fees(a: dict) -> tuple[str, str]:
    """(initiation fee paid, certificate paid): the dates of the new member's payments in the Treasurer's records, else
    what's written on the application. Read when the PDF is made."""
    from . import treasurer
    mine = [e for e in treasurer.load()["entries"] if a.get("member") and e.get("member") == a["member"]]
    return tuple(max((e["date"] for e in mine if e["kind"] == k), default=a["club"].get(f"{f}_paid", "")) for k, f in (("initiation", "fee"), ("certificate", "bond")))


def prefill(a: dict) -> dict:
    """The new-member form from an application: what `membership.add_member` takes, nothing typed twice."""
    f = a["form"]
    first, last = split_name(f["name"])
    return {"first": first, "last": last, "callsign": f.get("callsign", ""), "email": f["email"], "phone": f["phone"],
            "license_class": f.get("license_class") if f.get("license_class") in CLASSES else "",
            "address": "\n".join(x for x in (f["street"], ", ".join(y for y in (f["city"], f"{f['state']} {f['zip']}".strip()) if y)) if x)}  # empty parts left out (paper, details not asked)


def sample(club: Club, m: dict, now: float | None = None) -> dict:
    """A made-up filled application for Site → Membership's Preview the PDF: a fictional applicant, a sample answer to
    each of m's questions, m's details asked and statement (m: the tab's settings as typed). Never stored."""
    def ask(k, v):
        return "" if m["fields"].get(k) == "hidden" else v
    f = {"name": "Jane Doe", "email": "jane.doe@example.org", "street": ask("street", "123 Main St"), "city": ask("city", "Anytown"),
         "state": ask("state", "OH"), "zip": ask("zip", "45000"), "phone": ask("phone", "555-0100"), "occupation": ask("occupation", "Nurse")}
    if club.ham:
        f |= {"callsign": ask("callsign", "N0CALL"), "first_licensed": ask("first_licensed", "2019"),
              "license_class": ask("license_class", "General"), "expires": ask("expires", "2029-06-30")}
    answers = []
    for q in m["questions"]:
        value = {"yesno": "Yes", "one": (q.get("options") or [""])[0], "several": q.get("options", [])[:2], "short": "A short sample answer",
                 "long": "A longer sample answer.\nIt can run over several lines.",
                 "grid": {"cols": q.get("cols", []), "rows": {r: q.get("cols", [])[:1] for r in q.get("rows", [])}}}[q["type"]]
        answers.append({"label": q["label"], "type": q["type"], "value": value})
    now = int(now or time.time())
    return {"form": f, "answers": answers, "statement": m["statement"], "signed": {"name": "Jane Doe", "at": now},
            "sponsor": {"typed": "", "id": None, "asked": None, "answer": None, "at": None}, "club": dict.fromkeys(("voted", "initiated", "fee_paid", "bond_paid"), "")}
