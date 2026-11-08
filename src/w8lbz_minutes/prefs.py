"""Email preferences: what each member gets right away, in the weekly digest, or not at all.

Stored in email-prefs.json in the data directory ({member: {kind: choice, "chow_days": [7, 3, 1]}}), with what the
daily reminders run has already sent in reminders-sent.json. Sign-in links, swap requests waiting for an answer,
chow changes to your own night and security emails aren't covered: they always go.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import business, chow
from .club import DATA, Club

# kind: (label, choices, default). "roster" is for the Secretaries, who are told about roster changes.
KINDS = {
    "minutes": ("The minutes", ("now", "digest", "off"), "now"),
    "business": ("Business reminders", ("now", "digest", "off"), "now"),
    "roster": ("Roster changes", ("now", "off"), "now"),
}
CHOICES = {"now": "Right away", "digest": "Weekly digest", "off": "Off"}
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
                    f"Can't make it? Ask someone to swap on the chow page: {url}/chow\n\n{_sign(club)}"))
    return out


def digest(club: Club, meetings: list, today: dt.date, url: str) -> list[tuple[str, str, str, str]]:
    """The weekly digest, on meeting day, for everyone who chose it for anything: your next chow night,
    your open business items with due dates, and the latest minutes."""
    wd = chow.WEEKDAYS.index(str(club.info.get("meeting_day", "Thursday")).lower())
    if today.weekday() != wd:
        return []
    s, items = chow.load(), business.load()["items"]
    latest = next((m for m in sorted(meetings, key=lambda m: m.date, reverse=True) if m.status == "published"), None)
    out = []
    for m in emailable(club):
        p = get(m.id)
        if "digest" not in (p["minutes"], p["business"]):
            continue
        lines = []
        mine = next((d for d, x in chow.upcoming(s, today, n=60) if x.get("member") == m.id and x.get("status") != "none"), None)
        if mine:
            lines.append(f"Your chow night: {dt.date.fromisoformat(mine).strftime('%A, %B %-d')}" + (" (tonight)" if mine == today.isoformat() else ""))
        if p["business"] == "digest":
            due = sorted((x for x in items if x["status"] == "open" and x["owner"] == m.id), key=lambda x: x["due"] or "9999")
            if due:
                lines.append("Club business you're looking after:\n" + "\n".join(
                    f"  - {x['text']}" + (f" (due {dt.date.fromisoformat(x['due']).strftime('%b %-d')})" if x["due"] else "") for x in due)
                    + f"\n  Mark them done at {url}/business")
        if p["minutes"] == "digest" and latest:
            lines.append(f"The latest minutes ({latest.date.strftime('%B %-d')}): {url}/meetings/{latest.slug}")
        if lines:
            out.append((m.id, f"digest:{today}:{m.id}", f"{club.info.get('callsign', 'W8LBZ')} this week",
                        f"Hi {m.first},\n\n" + "\n\n".join(lines) + f"\n\nChoose what you get by email: {url}/profile/email\n\n{_sign(club)}"))
    return out


def unsent(keys: list[str]) -> set[str]:
    sent = _read("reminders-sent.json").get("keys", [])
    return set(keys) - set(sent)


def mark_sent(keys: list[str], today: dt.date) -> None:
    """Remember what went out, so a second run the same day sends nothing twice. Keeps 60 days."""
    cutoff = (today - dt.timedelta(days=60)).isoformat()
    old = [k for k in _read("reminders-sent.json").get("keys", []) if k.split(":")[1] >= cutoff]
    _write("reminders-sent.json", {"keys": sorted(set(old + keys))})
