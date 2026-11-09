"""Email preferences: which emails each member gets, including the meeting-day email.

Stored in email-prefs.json in the data directory ({member: {kind: choice, "chow_days": [7, 3, 1]}}), with what the
daily reminders run has already sent in reminders-sent.json. Sign-in links, swap requests waiting for an answer,
chow changes to your own night and security emails aren't covered: they always go.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import business, calview, chow
from .club import DATA, Club

# kind: (heading, {choice: what it means}, default), in the order the Email tab shows them. "roster" is for the Secretaries.
KINDS = {
    "minutes": ("Meeting minutes", {"pdf": "Email them with the PDF attached", "link": "Email a link to read them online",
                                    "off": "Don't email them"}, "pdf"),
    "digest": ("Meeting-day reminder", {"on": "Send it", "off": "Don't send it"}, "on"),
    "business": ("Club business you're looking after", {"now": "Remind me", "off": "Don't remind me"}, "now"),
    "chowlist": ("The chow list", {"pdf": "Email it with the PDF attached", "off": "Don't email it"}, "pdf"),
    "roster": ("Roster updates", {"now": "Email me", "off": "Don't email me"}, "now"),
}
HELP = {
    "minutes": "Sent the night of the meeting, once they're finished.",
    "digest": "The morning of each meeting: when it starts, whether you're bringing chow, any club business you're "
              "looking after, upcoming events, and a link to last week's minutes.",
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
    return json.loads(p.read_text()) if p.exists() else {}


def _write(name: str, d: dict) -> None:
    p = _path(name)
    tmp = p.with_suffix(".tmp")
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
    clean["chow_days"] = sorted({int(d) for d in choices.get("chow_days", []) if str(d).isdigit() and int(d) in CHOW_DAYS}, reverse=True)
    allp = _read("email-prefs.json")
    allp[mid] = clean
    _write("email-prefs.json", allp)
    return get(mid)


# ---------------- other settings (Your profile → Settings), in settings.json ----------------

SETTINGS = {"week_start": ("First day of the week", {"sunday": "Sunday", "monday": "Monday"}, "sunday")}


def settings(mid: str | None) -> dict:
    """A member's settings, defaults filled in (and the defaults for someone not signed in)."""
    saved = _read("settings.json").get(mid, {}) if mid else {}
    return {k: saved.get(k) if saved.get(k) in choices else default for k, (_, choices, default) in SETTINGS.items()}


def save_settings(mid: str, choices: dict) -> dict:
    """Keep only valid choices (they come from a form)."""
    alls = _read("settings.json")
    alls[mid] = {k: v for k, v in choices.items() if k in SETTINGS and v in SETTINGS[k][1]}
    _write("settings.json", alls)
    return settings(mid)


def wants(mid: str, kind: str) -> str:
    return get(mid)[kind]


def emailable(club: Club) -> list:
    """Members who can get email at all: an address, and not Inactive or a Silent Key."""
    return [m for m in club.members.values() if m.email and m.status.lower() not in ("inactive", "silent key")]


def _sign(club: Club) -> str:
    return f"73,\n{club.info.get('callsign', 'W8LBZ')} Minutes"


def chow_reminders(club: Club, today: dt.date, url: str) -> list[tuple[str, str, str, str]]:
    """(member, key, subject, body) for each chow reminder due today and not sent yet."""
    s, out, ok = chow.load(), [], {m.id for m in emailable(club)}
    for date, x in chow.upcoming(s, today, n=2):
        mid, days = x.get("member"), (dt.date.fromisoformat(date) - today).days
        if mid not in ok or x.get("status") == "none" or days not in get(mid)["chow_days"]:
            continue
        m = club.members[mid]
        when = "tomorrow" if days == 1 else f"in {days} days"
        out.append((mid, f"chow:{date}:{mid}:{days}", f"Your chow night is {when}: {dt.date.fromisoformat(date).strftime('%A, %B %-d')}",
                    f"Hi {m.first},\n\nA reminder that you're bringing chow on {dt.date.fromisoformat(date).strftime('%A, %B %-d')}.\n\n"
                    f"Can't make it? Ask someone to swap on the chow page: {url}/chow\n\n"
                    f"To change how many days ahead you're reminded, or stop these reminders: {url}/profile/email\n\n{_sign(club)}"))
    return out


def digest(club: Club, archive, today: dt.date, url: str) -> list[tuple[str, str, str, str]]:
    """The meeting-day email, the morning of each meeting: the time, your chow if it's tonight,
    the business you're looking after, the calendar until the next meeting, and last meeting's minutes."""
    wd = chow.WEEKDAYS.index(str(club.info.get("meeting_day", "Thursday")).lower())
    if today.weekday() != wd:
        return []
    tonight = chow.load()["nights"].get(today.isoformat(), {})
    items = business.load()["items"]
    latest = next((m for m in sorted(archive.meetings, key=lambda m: m.date, reverse=True) if m.status == "published" and m.date < today), None)
    at = dt.datetime.strptime(str(club.info.get("meeting_time", "19:30")), "%H:%M").strftime("%-I:%M %p")
    # undated Upcoming Events in the minutes (all of them before events had dates) still count, as written
    undated = [i.text for i in latest.items("Upcoming Events") if not i.when] if latest else []
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
                f"  - {x['text']}" + (f" (due {dt.date.fromisoformat(x['due']).strftime('%b %-d')})" if x["due"] else "") for x in due)
                + f"\n  Mark them done at {url}/business")
        week, _ = calview.entries(club, archive, m.id, today + dt.timedelta(days=1), today + dt.timedelta(days=6))
        events = [f"{e.start:%a %b %-d}{f' {e.time}' if e.time else ''}: {e.title}" for e in week if e.kind != "chow"][:10] + undated
        if events:
            lines.append("Coming up this week:\n" + "\n".join(f"  - {e}" for e in events) + f"\n  The calendar: {url}/calendar")
        if latest:
            lines.append(f"Last meeting's minutes ({latest.date.strftime('%B %-d')}): {url}/meetings/{latest.slug}")
        out.append((m.id, f"digest:{today}:{m.id}", f"{club.info.get('callsign', 'W8LBZ')} meeting tonight",
                    f"Hi {m.first},\n\n" + "\n\n".join(lines) + f"\n\nTo stop this email, or choose which others you get: {url}/profile/email\n\n{_sign(club)}"))
    return out


def unsent(keys: list[str]) -> set[str]:
    sent = _read("reminders-sent.json").get("keys", [])
    return set(keys) - set(sent)


def mark_sent(keys: list[str], today: dt.date) -> None:
    """Remember what went out, so a second run the same day sends nothing twice. Keeps 60 days."""
    cutoff = (today - dt.timedelta(days=60)).isoformat()
    old = [k for k in _read("reminders-sent.json").get("keys", []) if k.split(":")[1] >= cutoff]
    _write("reminders-sent.json", {"keys": sorted(set(old + keys))})
