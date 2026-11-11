"""The club's offices (club.yaml `offices:`, from the bylaws), who holds each, and who held them when.

A member's offices are their titles (the roster's Title(s), comma-separated). Changing a holder writes the
new titles and the officer/board ticks to the roster sheet, and "Took office" / "Left office" rows (note: the
office) to its Membership Log; those rows are the officer history.

The officer and board ticks follow the offices a member holds: officer if any of them is an officer's
office, board if any is on the board (every office in the list is). Titles not in the list are kept as they are.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
import time

from . import branding, auth, profile
from .club import ROSTER_CACHE, Club, roster_locked
from .membership import LOG_TAB
from .roster import Sheet

log = logging.getLogger("club_minutes.offices")
TOOK, LEFT = "Took office", "Left office"
DEFAULT = ["President", "Vice President", "Secretary", "Treasurer"]


def offices(club: Club) -> list[dict]:
    """[{title, officer, seats}]: club.yaml entries are a title (an officer's office, one seat) or
    {title, officer: false, seats: 3} (e.g. the elected trustees)."""
    out = []
    for o in club.offices or DEFAULT:
        o = {"title": o} if isinstance(o, str) else dict(o)
        out.append({"title": str(o["title"]), "officer": bool(o.get("officer", True)), "seats": int(o.get("seats", 1))})
    return out


def titles(role: str | None) -> list[str]:
    return [t.strip() for t in (role or "").split(",") if t.strip()]


def holders(club: Club, title: str) -> list[str]:
    return sorted((m.id for m in club.members.values() if title in titles(m.role)), key=lambda i: club.name(i))


def terms(club: Club, title: str) -> list[dict]:
    """Who held an office when, from the Membership Log: [{member, start, end}], latest first.
    A term with no start began before the log; one with no end is current."""
    out = []
    for m in club.members.values():
        start, has = None, False
        for e in sorted((e for e in m.events if e.get("event") in (TOOK, LEFT) and e.get("note") == title),
                        key=lambda e: e.get("date") or ""):
            if e["event"] == TOOK:
                if has:  # took it twice with no Left between: the first term ended unrecorded
                    out.append({"member": m.id, "start": start, "end": None, "open": False})
                start, has = e.get("date"), True
            else:
                out.append({"member": m.id, "start": start if has else None, "end": e.get("date"), "open": False})
                start, has = None, False
        if has or title in titles(m.role):
            out.append({"member": m.id, "start": start, "end": None, "open": title in titles(m.role)})
    return sorted(out, key=lambda t: (t["end"] or "9999", t["start"] or ""), reverse=True)  # current first


def _flags(club: Club, ts: list[str]) -> tuple[bool, bool]:
    by = {o["title"]: o for o in offices(club)}
    return any(by[t]["officer"] for t in ts if t in by), any(t in by for t in ts)


def _date(v: str) -> str:
    try:
        return dt.date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValueError("Give the date the change took effect.")


@roster_locked
def change(club: Club, title: str, out_mid: str | None, in_mid: str | None, date: str, by: str,
           sheet: Sheet | None = None, url: str = "") -> list[str]:
    """Hand an office from out_mid to in_mid (either may be None: a vacancy filled, or a resignation).
    Writes the sheet first; raises ValueError or roster.SheetError, and then nothing has changed in the app.
    Returns the members whose titles changed."""
    office = next((o for o in offices(club) if o["title"] == title), None)
    if not office:
        raise ValueError("No such office.")
    date = _date(date)
    now = holders(club, title)
    if not out_mid and not in_mid:
        raise ValueError("Pick who takes the office, who leaves it, or both.")
    if out_mid and out_mid not in now:
        raise ValueError(f"{club.name(out_mid)} doesn't hold {title}.")
    if in_mid:
        m = club.members.get(in_mid)
        if not m or m.status.lower() in ("inactive", "silent key"):
            raise ValueError("Pick an active member.")
        if in_mid in now:
            raise ValueError(f"{m.name} already holds {title}.")
        if not out_mid and len(now) >= office["seats"]:
            raise ValueError(f"{title} is full: pick who {m.first} replaces.")
    sheet = sheet or Sheet()
    changed = {}
    for mid, took in ((out_mid, False), (in_mid, True)):
        if not mid:
            continue
        m = club.members[mid]
        ts = [t for t in titles(m.role) if t != title] + ([title] if took else [])
        officer, board = _flags(club, ts)
        old = sheet.update_member(m.callsign, m.name, {"title(s)": ", ".join(ts), "is_officer": officer, "is_board": board})
        sheet.add_row({"callsign": m.callsign or "", "full_name": m.name, "date": date, "event": TOOK if took else LEFT,
                       "note": title}, tab=LOG_TAB, optional=("callsign",))
        changed[mid] = (old.get("title(s)", m.role or ""), ", ".join(ts) or None, officer, board, TOOK if took else LEFT)

    rows = json.loads(ROSTER_CACHE.read_text()) if ROSTER_CACHE.exists() else []
    for r in rows:
        if r["id"] in changed:
            _, role, officer, board, event = changed[r["id"]]
            r.update(role=role, officer=officer, board=board)
            r.setdefault("events", []).append({"date": date, "event": event, "note": title})
    if rows:
        tmp = ROSTER_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=1))
        tmp.chmod(0o600)
        tmp.replace(ROSTER_CACHE)
    for mid, (old_role, role, officer, board, event) in changed.items():  # the running app, cache or not
        m = club.members[mid]
        m.role, m.officer, m.board = role, officer, board
        m.events.append({"date": date, "event": event, "note": title})
        profile._append({"id": secrets.token_hex(6), "at": int(time.time()), "member": mid, "by": by, "office": True,
                         "old": {"role": old_role}, "new": {"role": role or ""}})
    log.info("%s: %s -> %s on %s, by %s", title, out_mid, in_mid, date, by)
    _tell(club, title, out_mid, in_mid, date, by, url)
    return list(changed)


@roster_locked
def record_term(club: Club, title: str, mid: str, start: str, end: str, by: str, sheet: Sheet | None = None) -> None:
    """A past term (before the app kept the history): two Membership Log rows. Titles and ticks are untouched."""
    if title not in {o["title"] for o in offices(club)}:
        raise ValueError("No such office.")
    m = club.members.get(mid)
    if not m:
        raise ValueError("Pick a member.")
    start, end = _date(start), _date(end)
    if end < start:
        raise ValueError("The term ends before it starts.")
    sheet = sheet or Sheet()
    for date, event in ((start, TOOK), (end, LEFT)):
        sheet.add_row({"callsign": m.callsign or "", "full_name": m.name, "date": date, "event": event, "note": title},
                      tab=LOG_TAB, optional=("callsign",))
        m.events.append({"date": date, "event": event, "note": title})
    if ROSTER_CACHE.exists():
        rows = json.loads(ROSTER_CACHE.read_text())
        for r in rows:
            if r["id"] == mid:
                r["events"] = m.events
        tmp = ROSTER_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=1))
        tmp.chmod(0o600)
        tmp.replace(ROSTER_CACHE)
    log.info("past term recorded: %s %s %s..%s, by %s", mid, title, start, end, by)


def _tell(club: Club, title: str, out_mid: str | None, in_mid: str | None, date: str, by: str, url: str) -> None:
    """Every officer (the new ones included) and whoever left the office."""
    day = dt.date.fromisoformat(date).strftime("%B %-d, %Y")
    what = (f"{club.name(in_mid)} took over as {title} from {club.name(out_mid)}" if in_mid and out_mid else
            f"{club.name(in_mid)} became {title}" if in_mid else f"{club.name(out_mid)} left the office of {title}")
    ids = [m.id for m in club.members.values() if m.officer] + ([out_mid] if out_mid else [])
    to = sorted({club.members[i].email for i in ids if club.members[i].email})
    if not to:
        return
    try:
        auth.send_email(to, f"{club.info.get('callsign') or 'Club'} offices: {what}",
                        f"{what}, as of {day}. {club.name(by)} recorded it in the app; the roster sheet and its "
                        f"Membership Log are updated.\n\nThe offices: {url}/offices\n\n73,\n{branding.name(club)}",
                        only_to=club.email_only_to())
    except Exception:
        log.exception("couldn't email the officers about %s", title)
