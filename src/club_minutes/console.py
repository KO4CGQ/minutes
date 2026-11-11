"""Leader console: start a meeting, keep its live state, and save it as draft minutes.

A draft lives in the archive like any meeting (status "draft"). The console's own
working state (report queue, who has the floor) sits beside it in console.json.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import logging
import shutil
import threading
import time

from . import ai, auth, business, chow, prefs
from .club import DATA
from .meeting import SECTIONS, Item, Meeting
from .store import Archive

log = logging.getLogger("club_minutes.console")
CARRY_FORWARD = ("Upcoming Events",)  # copied from the previous meeting as a starting point (business is tracked)


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_starting = threading.Lock()  # two leaders clicking Start at once still get one meeting

# Who is taking an open meeting's minutes: one console (browser tab) at a time, kept while it's open (it checks in every
# 5 s, and on coming back to the front) and let go HOLD_SECONDS after it closes or loses its connection. Anyone else's console is read-only, with
# Take over. Kept in memory: the app runs as one process, so a restart lets go of every hold (the next save takes it).
HOLD_SECONDS = 120
_holds: dict[str, dict] = {}
closed_by: dict[str, str] = {}  # who submitted or discarded a meeting's minutes, for consoles still open on them
_holding = threading.Lock()


def holder(slug: str, tab: str | None = None) -> dict | None:
    """Whoever else is taking these minutes right now ({member, tab, since}), or None."""
    with _holding:
        h = _holds.get(slug)
        return dict(h) if h and h["tab"] != tab and time.time() - h["at"] < HOLD_SECONDS else None


def hold(archive: Archive, slug: str, member: str, tab: str, take: bool = False, prev: str | None = None) -> dict | None:
    """Keep (or, with take, take over) the minutes for this tab. Returns whoever else holds them (nothing changes), or
    None when this tab now does. Taking over makes the new person the recorder: they take the minutes from here.
    prev: this browser tab's id before a reload (each page load gets a new id, so a duplicated tab never shares one);
    a reloaded console carries on from it without asking."""
    with _holding:
        h = _holds.get(slug)
        now = time.time()
        mine = h and (h["tab"] == tab or (prev and h["tab"] == prev and h["member"] == member))
        other = h and not mine and now - h["at"] < HOLD_SECONDS
        if other and not take:
            return dict(h)
        _holds[slug] = {"member": member, "tab": tab, "at": now, "since": h["since"] if mine else now}
    if other and member != archive.by_slug[slug].recorder:
        m = archive.by_slug[slug]
        m.recorder = member
        m.save(archive.root)
    return None


def release(slug: str, tab: str) -> None:
    with _holding:
        if _holds.get(slug, {}).get("tab") == tab:
            del _holds[slug]


def start(archive: Archive, date: dt.date, leader: str | None, recorder: str | None = None) -> Meeting:
    """Start the minutes of a meeting, or open them if that date's are already there. Only one meeting is open at a
    time: with another still in progress (say, last week's never submitted), it has to be submitted or discarded first."""
    with _starting:
        if date.isoformat() in archive.by_slug:
            return archive.by_slug[date.isoformat()]
        if (open_ := [m for m in archive.meetings if m.status == "draft"]):
            raise ValueError(f"The minutes of {open_[0].date:%B %-d} are still open: continue them, or discard them, "
                             "before starting another meeting.")
        return _start(archive, date, leader, recorder)


def _start(archive: Archive, date: dt.date, leader: str | None, recorder: str | None) -> Meeting:
    m = Meeting(date=date, status="draft", leader=leader, recorder=recorder)
    prev = previous(archive, date)
    for sec in SECTIONS:
        m.sections[sec] = []
    if prev:
        # events already past drop off; undated ones stay until a leader removes them
        m.sections["Upcoming Events"] = [Item(i.text, when=i.when, until=i.until, cal=i.cal) for i in prev.items("Upcoming Events")
                                         if not i.when or (i.until or i.when)[:10] >= date.isoformat()]
    # Old Business: every tracked item still open (business.py).
    m.sections.update(business_sections(archive, m.slug))
    # The next few chow nights after tonight, from the chow schedule.
    later = [(d, x) for d, x in chow.upcoming(chow.load(), date + dt.timedelta(days=1), n=4)]
    m.sections["Upcoming Chow"] = [Item(chow.describe(archive.club, d, x)) for d, x in later]
    m.save(archive.root)
    archive.reload()
    return archive.by_slug[m.slug]


def business_state(archive: Archive) -> dict:
    s = business.load()
    if not s["seeded"]:
        business.seed(s, archive.meetings)
        business.save(s)
    return s


def business_sections(archive: Archive, slug: str, s: dict | None = None) -> dict[str, list[Item]]:
    s = s or business_state(archive)
    return {k: [Item(t) for t in v] for k, v in business.sections(s, archive.club, slug, _since(archive, slug)).items()}


def _since(archive: Archive, slug: str) -> str | None:
    prev = previous(archive, dt.date.fromisoformat(slug))
    return prev.slug if prev else None


def business_items(archive: Archive, slug: str, s: dict | None = None) -> dict:
    """The console's Old and New Business: tracked items as of this meeting."""
    s = s or business_state(archive)
    since = _since(archive, slug)
    old, new = business.for_meeting(s, slug, since)
    return {"old": [business.view(archive.club, x, slug, since) for x in old],
            "new": [business.view(archive.club, x, slug, since) for x in new],
            "notes": [{**n, "name": archive.club.name(n["by"])} for n in business.notes_for(s, slug)]}


def sync_business(archive: Archive, slug: str, s: dict) -> None:
    """After a business item changes, rewrite that draft's Old and New Business (the live view reads them)."""
    m = archive.by_slug.get(slug)
    if m and m.status == "draft":
        m.sections.update(business_sections(archive, slug, s))
        m.save(archive.root)


# ---------------- AI "ask about" prompts for Community Reports (ai.ask_about) ----------------
REPORTS_BACK = 4    # a member's last few reports, however many meetings apart (they may come every third week)
REPORTS_DAYS = 365  # but nothing older than a year


def report_histories(archive: Archive) -> dict[str, list[tuple[str, str]]]:
    """Each active member's last few community reports from submitted minutes, oldest first."""
    out: dict[str, list[tuple[str, str]]] = {}
    cutoff = dt.date.today() - dt.timedelta(days=REPORTS_DAYS)
    for mt in archive.meetings:  # newest first
        if mt.status == "draft" or mt.date < cutoff:
            continue
        for it in mt.items("Community Reports"):
            m = archive.club.members.get(it.member or "")
            if m and m.status.lower() not in ("inactive", "silent key") and len(out.setdefault(m.id, [])) < REPORTS_BACK:
                out[m.id].append((mt.slug, it.text))
    return {mid: r[::-1] for mid, r in out.items()}


def refresh_ask_about(archive: Archive) -> int:
    """Ask the AI what's worth following up for every member; saved for the console. Returns members covered."""
    hist = report_histories(archive)
    prompts = ai.ask_about(archive.club, hist)
    data = {"at": int(time.time()), "model": archive.club.ai.get("model"),
            "members": {mid: {"prompts": p, "upto": hist[mid][-1][0]} for mid, p in prompts.items()}}
    path = DATA / "ask-about.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.chmod(0o600)
    tmp.replace(path)
    return len(prompts)


def ask_about_for(history: dict[str, list]) -> dict[str, list[str]]:
    """Saved prompts that are still current: made after the member's latest report shown in the console."""
    p = DATA / "ask-about.json"
    if not p.exists():
        return {}
    members = json.loads(p.read_text()).get("members", {})
    return {mid: e["prompts"] for mid, e in members.items()
            if history.get(mid) and e.get("upto") == history[mid][0][0]}


def previous(archive: Archive, date: dt.date) -> Meeting | None:
    return next((m for m in archive.meetings if m.date < date and m.status != "draft"), None)


def state_path(archive: Archive, slug: str):
    return archive.root / slug / "console.json"


def load_state(archive: Archive, slug: str) -> dict:
    p = state_path(archive, slug)
    return json.loads(p.read_text()) if p.exists() else {"done": [], "skipped": [], "current": None}


def context(archive: Archive, m: Meeting) -> dict:
    """Everything the console page needs, as JSON-able data."""
    club = archive.club
    members = sorted(
        (x for x in club.members.values() if x.status.lower() not in ("silent key", "inactive")),
        key=lambda x: (x.last, x.first))
    history = {}
    for x in members:
        past = [(mt.date.isoformat(), it.text) for mt in archive.meetings if mt.date < m.date
                for it in mt.items("Community Reports") if it.member == x.id][:3]
        history[x.id] = past
    guests = {}
    past = []  # earlier guests who aren't members, most recent first (for the quick-pick buttons)
    for g in archive.guests():
        if not g.get("member") and g["visits"][-1][0] < m.date:
            past.append({"name": g["name"], "callsign": g["callsign"], "visits": len(g["visits"]),
                         "last": g["visits"][-1][0].isoformat()})
        info = {"name": g["name"], "callsign": g["callsign"], "visits": len(g["visits"]),
                "last": g["visits"][-1][0].isoformat(), "member": g.get("member")}
        guests[_key(g["name"])] = info
        if g["callsign"]:
            guests[_key(g["callsign"])] = info
    return {
        "meeting": {
            "slug": m.slug, "date": m.slug, "status": m.status, "leader": m.leader,
            "in_person": m.in_person, "remote": m.remote, "guests": m.guests, "visitors": m.visitors,
            "sections": {s: [{"text": i.text, "member": i.member, "when": i.when, "until": i.until, "cal": i.cal} for i in m.items(s)] for s in SECTIONS},
        },
        "state": load_state(archive, m.slug),
        "members": [{"id": x.id, "name": x.name, "short": club.short(x.id), "callsign": x.callsign}
                    for x in members],
        "history": history,
        "guest_history": guests,
        "past_guests": sorted(past, key=lambda g: (g["last"], g["visits"]), reverse=True),
        "sections": SECTIONS,
        "invite_after": int(club.info.get("invite_after_visits", 3)),
        "events_public": "events" in (club.info.get("public_calendars") or []),  # Club events shown to anyone
        "email": {"roster": len(recipients(archive)), "only_to": club.email.get("only_to")},
        "chow": chow_tonight(archive, m),
        "business": business_items(archive, m.slug),
        "ask_about": ask_about_for(history),
        "celebrations": celebrations(club, m.date),
        "paid_since": paid_since(archive, m.date),
        **calendar_ahead(archive, m.date),
    }


def paid_since(archive: Archive, date: dt.date) -> list[str]:
    """What the Treasurer recorded as paid since the last meeting, for the leader to read under Bills (and add on a tap)."""
    from . import treasurer
    s = treasurer.load()
    if not treasurer.on(s):
        return []
    prev = previous(archive, date)
    lo = prev.date.isoformat() if prev else (date - dt.timedelta(days=7)).isoformat()
    return [f"Paid {treasurer.money(e['cents'])}: {e['note'] or treasurer.categories(s).get(e.get('category'), 'expense')}"
            for e in sorted(s["entries"], key=lambda e: e["date"]) if e["kind"] == "expense" and lo < e["date"] <= date.isoformat()]


def _on(year: int, mmdd: str) -> dt.date:
    """MM-DD (or MM, meaning the 1st) in a given year; Feb 29 falls on the 28th in other years."""
    month, day = int(mmdd[:2]), int(mmdd[3:5]) if len(mmdd) > 2 else 1
    return dt.date(year, month, 28 if (month, day) == (2, 29) and year % 4 else day)


READ_OUT = ("events", "community", "planning", "holiday", "birthday")  # chow has its own step


def calendar_ahead(archive: Archive, date: dt.date, days: int = 92) -> dict:
    """What's on the calendar from the meeting's day on, for the leader to read out under Upcoming Events: club and
    community events, Planning, holidays and birthdays shared with everyone. Left out: events ticked to leave out (the
    weekly meeting by default), business items' due dates (Old Business has them) and the minutes' own Upcoming Events. next_meeting: the meeting
    event's next date, else a week on."""
    from . import calview, events as store
    evs = store.load()
    quiet = {e["id"] for e in evs if store.quiet(e)}
    items, _ = calview.entries(archive.club, archive, "", date, date + dt.timedelta(days=days))  # "": no private birthdays
    out = [{"start": e.start.isoformat(), "end": e.end.isoformat(), "time": e.time, "title": e.title, "where": e.where,
            "kind": calview.KINDS[e.kind], "cal": e.kind if e.kind in store.CALS else "events",
            # for "Add to the minutes": the same form as an Upcoming Event's when/until
            "when": f"{e.starts:%Y-%m-%dT%H:%M}" if e.starts else e.start.isoformat(),
            "until": f"{e.ends:%Y-%m-%dT%H:%M}" if e.ends else (e.end.isoformat() if e.end > e.start else None)} for e in items
           if e.kind in READ_OUT and not e.from_minutes and e.id not in quiet and not (e.id or "").startswith("due-")]
    after = [d for e in evs if e.get("meeting") for d in store.dates(e, date + dt.timedelta(days=1), date + dt.timedelta(days=60))]
    return {"calendar": out, "next_meeting": min(after, default=date + dt.timedelta(days=7)).isoformat()}


def celebrations(club, date: dt.date) -> list[str]:
    """Birthdays (shared with members only) and membership anniversaries in the week up to tonight, so each is
    greeted at exactly one weekly meeting. A month-only birthday counts from the 1st: the month's first meeting."""
    out = []
    for m in club.members.values():
        if m.status.lower() in ("inactive", "silent key"):
            continue
        for year in {date.year, (date - dt.timedelta(days=6)).year}:
            try:
                if m.birthday and "birthday" in m.share and 0 <= (date - _on(year, m.birthday)).days < 7:
                    month = len(m.birthday) == 2 and f" ({_on(year, m.birthday):%B})" or ""
                    out.append(f"Happy birthday, {m.name}!{month}")
                since = m.member_since and dt.date.fromisoformat(m.member_since)
                if since and year > since.year and 0 <= (date - _on(year, f"{since:%m-%d}")).days < 7:
                    n = year - since.year
                    out.append(f"{m.name}: {n} year{'s' if n > 1 else ''} as a member")
            except ValueError:
                continue  # something odd typed into the sheet
    return sorted(out)


def _when(v) -> str | None:
    """An event's date, or date and time, from the browser; anything else is dropped."""
    try:
        return v if v and dt.datetime.fromisoformat(v) and len(v) in (10, 16) else None
    except (TypeError, ValueError):
        return None


def _span(when, until) -> tuple[str | None, str | None]:
    """A valid start, and an end only if it's valid and not before the start."""
    when, until = _when(when), _when(until)
    if not when or not until:
        return when, None
    if len(when) == 10:  # an all-day event ends on a day: its last day, after the first
        return when, until[:10] if until[:10] > when else None
    if len(until) == 10:  # a timed event with only an end date: the last day, on or after the start's day
        return when, until if until >= when[:10] else None
    return when, until if until > when else None


def apply(archive: Archive, slug: str, data: dict) -> Meeting:
    """Save the console's full meeting state (last write wins)."""
    m = archive.by_slug[slug]
    if m.status == "published":
        raise ValueError("Minutes already submitted")
    d = data["meeting"]
    m.leader = d.get("leader") or m.leader
    m.in_person = list(dict.fromkeys(d.get("in_person", [])))
    m.remote = [x for x in dict.fromkeys(d.get("remote", [])) if x not in m.in_person]
    m.guests = [{k: v for k, v in g.items() if k in ("name", "callsign", "notes", "online") and v}
                for g in d.get("guests", []) if g.get("name", "").strip()]
    m.guests = [g for g in m.guests if not archive.club.is_redacted(g["name"])]
    m.visitors = max(0, int(d.get("visitors") or 0))
    m.sections = {s: [Item(i["text"].strip(), i.get("member") or None, *_span(i.get("when"), i.get("until")),
                           i.get("cal") if i.get("cal") in ("community", "planning") else None)
                      for i in d.get("sections", {}).get(s, []) if i.get("text", "").strip()]
                  for s in SECTIONS}
    m.sections.update(business_sections(archive, slug))  # tracked: never from the browser
    m.save(archive.root)
    state_path(archive, slug).write_text(json.dumps(data.get("state") or {}))
    archive.reload()
    return archive.by_slug[slug]


def submit(archive: Archive, slug: str, by: str | None = None) -> Meeting:
    m = archive.by_slug[slug]
    # Upcoming Chow comes from the chow schedule as it stands now: tonight's chow, tonight's changes, what's next.
    m.sections["Upcoming Chow"] = [Item(t) for t in chow.minutes_section(archive.club, chow.load(), chow.history(), slug)]
    m.sections.update(business_sections(archive, slug))
    m.status = "published"
    m.save(archive.root)
    _holds.pop(slug, None)  # finished: nobody holds them now
    if by:
        closed_by[slug] = by
    archive.reload()
    return archive.by_slug[slug]


def discard(archive: Archive, slug: str, by: str | None = None) -> None:
    """Delete a draft (e.g. a practice run). Submitted minutes can't be discarded."""
    m = archive.by_slug[slug]
    if m.status != "draft":
        raise ValueError("Only drafts can be discarded")
    shutil.rmtree(archive.root / slug)
    _holds.pop(slug, None)
    if by:
        closed_by[slug] = by
    s = business.load()
    business.forget_meeting(s, slug)
    business.save(s)
    archive.reload()


def recipients(archive: Archive, how: str | None = None) -> list[str]:
    """Roster addresses that get the minutes: everyone with an email who isn't Inactive or a Silent Key,
    and hasn't turned them off. `how` picks those who want the PDF ("pdf") or just a link ("link")."""
    return sorted({m.email.strip() for m in prefs.emailable(archive.club)
                   if prefs.wants(m.id, "minutes") in ((how,) if how else ("pdf", "link"))}, key=str.lower)


def email_status(archive: Archive, slug: str) -> dict | None:
    p = archive.root / slug / "emailed.json"
    return json.loads(p.read_text()) if p.exists() else None


def email_minutes(archive: Archive, slug: str, make_pdf, url: str) -> dict:
    """Email submitted minutes (PDF from make_pdf() attached) to the roster, once.

    Test mode (email.only_to) applies. The outcome, or the error, is kept in emailed.json.
    """
    if (done := email_status(archive, slug)) and not done.get("error"):
        return done
    m = archive.by_slug[slug]
    club = archive.club
    to, links = recipients(archive, "pdf"), recipients(archive, "link")
    leader = club.members.get(m.leader or "")
    recorder = club.members.get(m.recorder or "") if m.recorder != m.leader else None
    writer = recorder or leader  # replies (corrections) go to whoever took the minutes
    date = m.date.strftime("%B %-d, %Y")
    body = (f"Here are the minutes of the {club.info.get('callsign', '')} meeting of {date}"
            f"{f', led and recorded by {leader.name}' if leader and m.recorder == m.leader else f', led by {leader.name}' if leader else ''}"
            f"{f', recorded by {recorder.name}' if recorder else ''}. The PDF is attached.\n\n"
            f"Read them online, along with past minutes: {url}\n\n"
            f"Replies go to {writer.first if writer and writer.email else 'the club'}.\n\n"
            f"To get a link instead of the PDF, or stop these emails: {url.split('/meetings/')[0]}/profile/email\n\n73,\n{club.info.get('callsign', '')}")
    subject, reply_to = f"{club.info.get('callsign', '')} meeting minutes, {date}", writer.email if writer and writer.email else None
    try:
        pdf = make_pdf() if to else None
        sent = auth.send_email(to, subject, body, attachments=[(pdf[0], pdf[1], "application/pdf")],
                               only_to=club.email_only_to(), reply_to=reply_to) if to else 0
        sent += auth.send_email(links, subject, body.replace(" The PDF is attached.", "").replace("a link instead of the PDF", "the PDF attached instead"), only_to=club.email_only_to(),
                                reply_to=reply_to) if links else 0
        status = {"at": int(time.time()), "sent": sent, "roster": len(to) + len(links), "test_mode": club.email_only_to() is not None}
    except Exception as e:
        log.exception("emailing the minutes of %s failed", slug)
        status = {"at": int(time.time()), "error": str(e)}
    (archive.root / slug / "emailed.json").write_text(json.dumps(status))
    return status


def chow_tonight(archive: Archive, m: Meeting) -> dict:
    """Tonight's chow for the console panel, and the members who could have covered it."""
    s = chow.load()
    x = s["nights"].get(m.slug) or {}
    nxt = chow.upcoming(s, m.date + dt.timedelta(days=1), n=1)
    club = archive.club
    return {"date": m.slug, "member": x.get("member"), "name": club.name(x["member"]) if x.get("member") else None,
            "status": x.get("status", "scheduled") if x else None, "covered_by": x.get("covered_by"),
            "note": x.get("note", ""), "next": chow.describe(club, *nxt[0]) if nxt else None,
            "section": chow.minutes_section(club, s, chow.history(), m.slug)}
