"""Meeting nights, from the calendar: every Club events series ticked "Club meeting" (events with `meeting: True`).

One source for Home, the notes picker, the console, the meeting-day email, the Start meeting button and chow. Any
schedule the calendar's repeat rules can say works; twice a week is two series. A date of a series can be cancelled
or rescheduled: one record per original date in the series' `changes` (events.py):
    {"cancelled": True, "reason": "..."}                                  no meeting that night
    {"moved": True, "start": "YYYY-MM-DDTHH:MM", "end": ..., "reason": "..."}   held on another day or time
A series has `chow` (default True when absent: the club's first series) to say whether chow goes with it.

Core fires, others react: `on(name, fn)` registers, `fire(name, **kw)` calls. Names (the plugin API's first hooks):
    meeting_cancelled(club, date, reason, by, url, chow)
    meeting_moved(club, date, to (a datetime), reason, by, url, chow)
`date` is the original date. chow.py registers for both."""
from __future__ import annotations

import datetime as dt
import logging

from . import events
from .club import Club, show_date

log = logging.getLogger(__name__)
_hooks: dict[str, list] = {}


def on(name: str, fn) -> None:
    _hooks.setdefault(name, []).append(fn)


def fire(name: str, **kw) -> None:
    for fn in _hooks.get(name, []):
        try:
            fn(**kw)
        except Exception:  # one listener failing mustn't undo the change or stop the others
            log.exception("%s: %s failed", name, fn.__name__)


def _all(club: Club, start: dt.date, until: dt.date, chow: bool):
    """(day it's held, "HH:MM", its record) for each meeting date held (or, cancelled, planned) from start to until."""
    events.ensure_meeting(club)
    for e in events.load():
        if not e.get("meeting") or (chow and not e.get("chow", True)):
            continue
        pad = events.reach(e)
        for d in events.dates(e, start - pad, until + pad):
            rec = e.get("changes", {}).get(d.isoformat(), {})
            o = events.in_zone(events.occurrence(e, d), e.get("tz"), club.timezone)
            day = d if rec.get("cancelled") else dt.date.fromisoformat(o["start"][:10])
            if start <= day <= until:
                yield day, o["start"][11:16] or club.meeting_time, rec


def nights(club: Club, start: dt.date, until: dt.date, with_cancelled: bool = False, chow: bool = False) -> list[dt.date]:
    """The dates of every meeting from start to until, moves applied, cancelled ones left out unless `with_cancelled`.
    chow: only the series chow goes with."""
    return sorted({d for d, _t, rec in _all(club, start, until, chow) if with_cancelled or not rec.get("cancelled")})


def time_on(club: Club, day: dt.date) -> str:
    """When the meeting held on `day` starts ("19:30"): the first one, if two series meet that day."""
    return min((t for d, t, rec in _all(club, day, day, False) if d == day and not rec.get("cancelled")), default=club.meeting_time)


# ---------------- cancel, reschedule, put back ----------------

def _record(club: Club, eid: str, d: str, by: str, url: str, make) -> bool:
    """Change the record of date d of series eid: make(e, old) returns the new record (None: remove it), or raises
    ValueError. Saves, logs, then fires and notifies. False if nothing changed. KeyError: no such event."""
    day = dt.date.fromisoformat(d)  # ValueError for the route
    with events.locked():
        evs = events.load()
        e = events.get(evs, eid)
        if not e.get("meeting") or day not in events.dates(e, day, day):
            raise ValueError("That isn't a date of a club meeting.")
        changes = e.setdefault("changes", {})
        old = changes.get(d)
        new = make(e, old or {})
        if new == old:
            return False
        if new is None:
            del changes[d]
        else:
            changes[d] = new
        events.save(evs)
    what = "restored one date of" if new is None else "cancelled one date of" if new.get("cancelled") else "moved one date of"
    events.log(by, what, {**e, "start": f"{d}{e['start'][10:]}"})
    if new is None:
        return True
    kw = {"club": club, "date": day, "reason": new.get("reason", ""), "by": by, "url": url, "chow": e.get("chow", True)}
    if new.get("cancelled"):
        fire("meeting_cancelled", **kw)
        _notify(club, day, f"{club.label}: No meeting on {show_date(day, 'weekday')}",
                f"There's no club meeting on {show_date(day, 'weekday')}.", new.get("reason", ""), url)
    else:
        to = dt.datetime.fromisoformat(new["start"])
        fire("meeting_moved", to=to, **kw)
        when = f"{show_date(to.date(), 'weekday')}" + (f" at {to:%-I:%M %p}" if "T" in new["start"] else "")
        _notify(club, day, f"{club.label}: Meeting moved: {show_date(day, 'weekday')} → {when}",
                f"The club meeting on {show_date(day, 'weekday')} has moved to {when}.", new.get("reason", ""), url)
    return True


def _reason(text: str) -> str:
    return " ".join((text or "").split())[:200]


def cancel(club: Club, eid: str, d: str, reason: str, by: str, url: str = "") -> bool:
    return _record(club, eid, d, by, url, lambda e, old: {"cancelled": True, "reason": _reason(reason)})


def move(club: Club, eid: str, d: str, day: str, at: str, reason: str, by: str, url: str = "") -> bool:
    """Hold date d on another day (and time: the meeting's own if empty)."""
    def make(e, old):
        try:
            new = dt.date.fromisoformat(day)
            t = at or e["start"][11:16]
            began = dt.datetime.fromisoformat(f"{new}T{t}" if "T" in e["start"] else new.isoformat())
        except ValueError:
            raise ValueError("Pick the new date and time.")
        if abs((new - dt.date.fromisoformat(d)).days) > 366:
            raise ValueError("Pick a date within a year of the meeting.")
        timed = "T" in e["start"]
        start = events._iso(began, timed)
        if start == events.occurrence({**e, "changes": {}}, dt.date.fromisoformat(d))["start"]:
            raise ValueError("Pick another date or time: that's when it already is.")
        end = events._iso(began + events.length(e), timed) if e.get("end") else None
        return {**{k: v for k, v in old.items() if k != "cancelled"}, "moved": True, "start": start, "end": end, "reason": _reason(reason)}
    return _record(club, eid, d, by, url, make)


def restore(club: Club, eid: str, d: str, by: str) -> bool:
    """Undo a cancel or a move. Chow isn't touched: the leader can Fill again."""
    return _record(club, eid, d, by, "", lambda e, old: None)


def _notify(club: Club, day: dt.date, subject: str, line: str, reason: str, url: str) -> None:
    """Email every member who gets club email, once per change (a change that changes nothing never gets here)."""
    if day < dt.date.today():
        return
    from . import auth, branding, prefs
    to = [m.email for m in prefs.emailable(club)]
    body = (f"{line}\n" + (f"\n{reason}\n" if reason else "")
            + f"\nThe calendar: {url}/calendar?start={day}\n\n{club.sign_off}\n{branding.name(club)}")
    try:
        auth.send_email(to, subject, body, only_to=club.email_only_to()) if to else 0
    except Exception:  # the change stands even if the email doesn't go
        log.exception("couldn't send %r", subject)
