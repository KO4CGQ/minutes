"""Email preferences: which emails each member gets, including the meeting-day email.

Stored in email-prefs.json in the data directory ({member: {kind: choice, "chow_days": [7, 3, 1]}}), with what the
daily reminders run has already sent in reminders-sent.json. Sign-in links, swap requests waiting for an answer,
chow changes to your own night and security emails aren't covered: they always go.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import logging
import os
from pathlib import Path

from . import branding, business, calview, chow, meetings
from .club import DATA, Club, num, show_date
from .richtext import plain, title

log = logging.getLogger(__name__)

# kind: (heading, {choice: what it means}, default), in the order the Email tab shows them. "roster" is for the Secretaries.
KINDS = {
    "minutes": ("Meeting minutes", {"pdf": "Email them with the PDF attached", "link": "Email a link to read them online",
                                    "off": "Don't email them"}, "pdf"),
    "digest": ("Meeting-day reminder", {"on": "Send it", "off": "Don't send it"}, "on"),
    "business": ("Club business you're looking after", {"now": "Remind me", "off": "Don't remind me"}, "now"),
    "chowlist": ("The chow list", {"pdf": "Email it with the PDF attached", "link": "Email a link to it online",
                                   "off": "Don't email it"}, "pdf"),
    "roster": ("Roster updates", {"now": "Email me", "off": "Don't email me"}, "now"),
}
HELP = {
    "minutes": "Sent the night of the meeting, once they're finished.",
    "digest": "The morning of each meeting: when it starts, {chow}any club business you're looking after, upcoming events, "
              "and a link to last week's minutes.",
    "business": "When something you took on at a meeting is due soon, or overdue.",
    "chowlist": "When the Secretary sends out the chow schedule: a new list, or one that has changed.",
    "roster": "Because you keep the roster: when someone's details change or a new member is added.",
}
CHOW_DAYS = (7, 3, 1)  # reminders before your chow night, days ahead
CHOW_DEFAULT = [3]


def _path(name: str) -> Path:
    return DATA / name


def _read(name: str) -> dict:
    p = _path(name)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        log.exception("%s is damaged; using defaults", p)  # every page reads it: a torn file mustn't stop them all
        return {}


@contextlib.contextmanager
def locked():
    """Held around read-merge-write of email-prefs.json and settings.json, by the web app and the daily job alike."""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "prefs.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _write(name: str, d: dict) -> None:
    p = _path(name)
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp")  # the daily job writes here too: never the same temp file
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
    tmp.replace(p)


def get(mid: str) -> dict:
    """A member's choices, defaults filled in."""
    saved = _read("email-prefs.json").get(mid, {})
    out = {k: saved.get(k) if saved.get(k) in choices else default for k, (_, choices, default) in KINDS.items()}
    out["chow_days"] = [d for d in saved.get("chow_days", CHOW_DEFAULT) if d in CHOW_DAYS]
    return out


def save(mid: str, choices: dict) -> dict:
    """Keep only valid choices (this is a trust boundary: it comes from a form)."""
    clean = {k: v for k, v in choices.items() if k in KINDS and v in KINDS[k][1]}
    clean["chow_days"] = sorted({num(d) for d in choices.get("chow_days", []) if num(d) in CHOW_DAYS}, reverse=True)
    with locked():
        allp = _read("email-prefs.json")
        allp[mid] = clean
        _write("email-prefs.json", allp)
    return get(mid)


# ---------------- other settings (Your profile → Settings), in settings.json ----------------

SETTINGS = {"week_start": ("First day of the week", {"sunday": "Sunday", "monday": "Monday"}, "sunday"),
            "support_link": ("Support Club Minutes in the menu", {"show": "Show it", "hide": "Hide it: I already give toward the app's development and upkeep"}, "show")}


def settings(mid: str | None) -> dict:
    """A member's settings, defaults filled in (and the defaults for someone not signed in)."""
    saved = _read("settings.json").get(mid, {}) if mid else {}
    return {k: saved.get(k) if saved.get(k) in choices else default for k, (_, choices, default) in SETTINGS.items()}


def save_settings(mid: str, choices: dict) -> dict:
    """Keep only valid choices (they come from a form); settings not in this form stay as they were."""
    with locked():
        alls = _read("settings.json")
        alls[mid] = alls.get(mid, {}) | {k: v for k, v in choices.items() if k in SETTINGS and v in SETTINGS[k][1]}
        _write("settings.json", alls)
    return settings(mid)


def wants(mid: str, kind: str) -> str:
    return get(mid)[kind]


def emailable(club: Club) -> list:
    """Members who can get email at all: an address, and not Inactive or a Silent Key."""
    return [m for m in club.members.values() if m.email and m.status.lower() not in ("inactive", "silent key")]


def _sign(club: Club) -> str:
    return f"{club.sign_off}\n{branding.name(club)}"


def chow_reminders(club: Club, today: dt.date, url: str) -> list[tuple[str, str, str, str]]:
    """(member, key, subject, body) for each chow reminder due today and not sent yet."""
    if not club.has("chow"):
        return []
    s, out, ok = chow.load(), [], {m.id for m in emailable(club)}
    for date, x in chow.upcoming(s, today, n=2):
        mid, days = x.get("member"), (dt.date.fromisoformat(date) - today).days
        if mid not in ok or x.get("status") == "none" or days not in get(mid)["chow_days"]:
            continue
        m = club.members[mid]
        when = "tomorrow" if days == 1 else f"in {days} days"
        out.append((mid, f"chow:{date}:{mid}:{days}", f"Your chow night is {when}: {show_date(date, 'weekday')}",
                    (f"Hi {m.first},\n\nA reminder that you're bringing chow on {show_date(date, 'weekday')}.\n\n"
                     f"Can't make it? Ask someone to swap on the chow page: {url}/chow\n\n"
                     f"To change how many days ahead you're reminded, or stop these reminders: {url}/profile/email\n\n{_sign(club)}")))
    return out


def digest(club: Club, archive, today: dt.date, url: str) -> list[tuple[str, str, str, str]]:
    """The meeting-day email, the morning of each meeting: the time, your chow if it's tonight,
    the business you're looking after, the calendar until the next meeting, and last meeting's minutes."""
    if not meetings.nights(club, today, today):
        return []
    tonight = chow.load()["nights"].get(today.isoformat(), {}) if club.has("chow") else {}
    items = business.load()["items"]
    latest = next((m for m in sorted(archive.meetings, key=lambda m: m.date, reverse=True) if m.status == "published" and m.date < today), None)
    at = dt.datetime.strptime(meetings.time_on(club, today), "%H:%M").strftime("%-I:%M %p")
    # undated Upcoming Events in the minutes (all of them before events had dates) still count, as written
    undated = [title(i.text) for i in latest.items("Upcoming Events") if not i.when] if latest else []
    out = []
    for m in emailable(club):
        if get(m.id)["digest"] != "on":
            continue
        lines = [f"The meeting is tonight at {at}."]
        if tonight.get("member") == m.id and tonight.get("status") != "none":
            lines.append("You're bringing chow tonight.")
        due = sorted((x for x in items if x["status"] == "open" and x["owner"] == m.id), key=lambda x: x["due"] or "9999")
        if due:
            lines.append("Club business you're looking after:\n" + "\n".join(
                f"  - {plain(x['text'])}" + (f" (due {show_date(x['due'], 'short')})" if x["due"] else "") for x in due)
                + f"\n  Mark them done at {url}/business")
        week, _ = calview.entries(club, archive, m.id, today + dt.timedelta(days=1), today + dt.timedelta(days=6))
        events = [f"{show_date(e.start, 'day')}{f' {e.time}' if e.time else ''}: {e.title}" for e in week if e.kind != "chow"][:10] + undated
        if events:
            lines.append("Coming up this week:\n" + "\n".join(f"  - {e}" for e in events) + f"\n  The calendar: {url}/calendar")
        if latest:
            lines.append(f"Last meeting's minutes ({show_date(latest.date, 'long')}): {url}/meetings/{latest.slug}")
        out.append((m.id, f"digest:{today}:{m.id}", f"{club.label} meeting tonight",
                    f"Hi {m.first},\n\n" + "\n\n".join(lines) + f"\n\nTo stop this email, or choose which others you get: {url}/profile/email\n\n{_sign(club)}"))
    return out


PURGE_DAYS = 90


def purge_note(mid: str) -> str | None:
    """For a roster keeper looking at a leaver's page: when their personal details go, or went."""
    e = _read("removed.json").get(mid)
    if not isinstance(e, dict):
        return None
    try:
        if "purged" in e:
            return f"Personal details deleted on {show_date(e['purged'], 'document')}."
        day = dt.date.fromisoformat(e["date"]) + dt.timedelta(days=PURGE_DAYS)
    except (KeyError, TypeError, ValueError):  # a damaged entry: no note rather than a broken page
        return None
    return f"Email, phone, address and photo are deleted on {show_date(day, 'document')} unless they rejoin."


def purge_removed(load_club=Club, today: dt.date | None = None) -> tuple[int, int]:
    """Drop what's kept about members who are Inactive or a Silent Key, or no longer on the roster (privacy): their
    email choices and settings, and their passkeys, sessions and sign-in records, at once. PURGE_DAYS after the job first
    saw them gone (removed.json): their roster row's email, phone, address, birthday and sharing, change-log lines, photo and
    calendar feed, once. Returns (how many members dropped at once, how many purged now)."""
    from . import profile
    from .auth import Auth, forget_email_log
    today = today or dt.date.today()
    gone = set()
    with locked():  # the roster read inside the lock: a callsign change in between would look like a member leaving
        club = load_club()
        if not club.members:  # a roster that didn't load isn't everyone leaving
            return 0, 0
        keep = {mid for mid, m in club.members.items() if m.status.lower() not in ("inactive", "silent key")}
        auth = Auth(club)
        known = auth.known_ids() | set(_read("email-prefs.json")) | set(_read("settings.json"))
        if known and len(known - keep) * 2 > len(known):  # a truncated roster would erase irreversibly
            log.error("purge_removed: %d of %d members with saved data aren't on the roster; refusing", len(known - keep), len(known))
            return 0, 0
        gone |= auth.forget_removed(keep)
        for name in ("email-prefs.json", "settings.json"):
            d = _read(name)
            if drop := set(d) - keep:
                gone |= drop
                _write(name, {k: v for k, v in d.items() if k not in drop})
        # their Email log lines too (an address is known only while they're on the roster)
        forget_email_log({m.email.strip().lower() for mid in gone if (m := club.members.get(mid)) and m.email})
        # the removal date: first seen gone; active again (or a prospect) is the undo
        left = {mid: e for mid, e in _read("removed.json").items() if mid not in keep}
        hold = _read("purge-hold.json")
        held = {mid: e for mid, e in hold.items() if mid not in keep}  # back on the roster: nothing happens to them
        new = sorted(((set(club.members) - keep) | gone) - set(left) - set(held))
        big = len(new) * 4 > len(club.members)
        new = [mid for mid in new if profile.has_details(club, mid)]  # nothing to delete (say, a Silent Key with no contact details): no clock
        if new and big:  # a bad bulk edit or import: an admin confirms before any clock starts
            log.error("purge_removed: %d of %d members became Inactive or gone in one night; their %d-day clocks wait for an admin (Admin page)",
                      len(new), len(club.members), PURGE_DAYS)
            held |= {mid: {"date": today.isoformat()} for mid in new}
        else:
            for mid in new:
                left[mid] = {"date": today.isoformat()}
        _write("removed.json", left)
        _write("purge-hold.json", held)
        purged = 0
        for mid, e in sorted(left.items()):
            try:
                if "purged" in e or dt.date.fromisoformat(e["date"]) + dt.timedelta(days=PURGE_DAYS) > today:
                    continue
                if not profile.forget_member(club, mid):  # back on the roster since the job read it: tomorrow drops the entry
                    continue
            except Exception:  # one member's failure (or a damaged entry) mustn't stop the rest; tomorrow's run finishes it
                log.exception("purge_removed: couldn't purge %s; will try again", mid)
                continue
            left[mid] = {**e, "purged": today.isoformat()}
            _write("removed.json", left)
            purged += 1
        if purged:
            log.info("purge_removed: personal details of %d member(s) deleted, %d days after they left", purged, PURGE_DAYS)
    return len(gone), purged


def purge_hold() -> list[str]:
    """Members whose 90-day clock waits for an admin to confirm (a big night of leavers)."""
    return sorted(_read("purge-hold.json"))


def confirm_hold(today: dt.date | None = None) -> int:
    """An admin confirms the held leavers really left: their clocks start now (not that night, so they get the full
    PURGE_DAYS, and the weekly list warns first). Returns how many; the next daily run drops any back on the roster."""
    with locked():
        held = purge_hold()
        left = _read("removed.json")
        for mid in held:
            left.setdefault(mid, {"date": (today or dt.date.today()).isoformat()})
        _write("removed.json", left)
        _write("purge-hold.json", {})
    return len(held)


def purge_list(club: Club, today: dt.date, url: str) -> list[tuple[str, str, str, str]]:
    """Mondays, to the roster keepers: members waiting to be purged and the date their details go (names only).
    Keyed by the week, so a second run that week sends nothing."""
    if today.weekday() != 0:
        return []
    from . import profile
    waiting = []
    for mid, e in _read("removed.json").items():
        try:
            if mid in club.members and "purged" not in e and profile.has_details(club, mid):
                waiting.append((dt.date.fromisoformat(e["date"]) + dt.timedelta(days=PURGE_DAYS), club.name(mid)))
        except (KeyError, TypeError, ValueError):  # a damaged entry: left out
            continue
    if not waiting:
        return []
    lines = "\n".join(f"  - {n}: {show_date(d, 'document')}" for d, n in sorted(waiting))
    return [(m.id, f"purge:{today}:{m.id}", f"{club.label}: members whose details will be deleted",
             (f"Hi {m.first},\n\nThese members have left. Their email, phone, address and birthday are deleted on the date shown "
              f"(unless you make them Active again before then):\n{lines}\n\n"
              f"You get this because you keep the roster. To stop it: {url}/profile/email\n\n{_sign(club)}"))
            for m in emailable(club) if club.keeps_roster(m.id) and get(m.id)["roster"] == "now"]


def unsent(keys: list[str]) -> set[str]:
    sent = _read("reminders-sent.json").get("keys", [])
    return set(keys) - set(sent)


def mark_sent(keys: list[str], today: dt.date) -> None:
    """Remember what went out, so a second run the same day sends nothing twice. Keeps 60 days."""
    cutoff = (today - dt.timedelta(days=60)).isoformat()
    old = [k for k in _read("reminders-sent.json").get("keys", []) if k.split(":")[1] >= cutoff]
    _write("reminders-sent.json", {"keys": sorted(set(old + keys))})
