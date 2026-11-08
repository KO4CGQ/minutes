"""The Calendar page: the club's Google Calendars (events, planning, community), plus chow nights and birthdays
from the app, and leaders adding, changing and deleting events on those calendars (so nobody needs the club's
Google account). Without calendars set up (or in a preview) it shows the app's own data: the weekly meeting and
the dated Upcoming Events.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass

from . import chow, gcal
from .club import DATA, PREVIEW, Club
from .meeting import Item

log = logging.getLogger(__name__)
KINDS = {"events": "Club events", "chow": "Chow", "planning": "Planning", "community": "Community", "birthday": "Birthdays"}
READ = ("events", "planning", "community")  # Google Calendars shown as they are; chow comes from the app
CACHE_SECONDS = 600
_cache: dict[tuple, tuple[float, list]] = {}


@dataclass
class Entry:
    start: dt.date
    end: dt.date  # last day, inclusive
    title: str
    kind: str
    time: str | None = None  # "7:30 PM", timed events only
    where: str | None = None
    id: str | None = None  # Google event id (editable on the page), else None
    repeats: bool = False  # one occurrence of a repeating event
    from_minutes: bool = False  # put there by the sync from the minutes' Upcoming Events

    def days(self, lo: dt.date, hi: dt.date) -> list[dt.date]:
        """The days it covers within lo..hi (a long event is listed on each day, up to two weeks)."""
        d, out = max(self.start, lo), []
        while d <= min(self.end, hi, self.start + dt.timedelta(days=13)):
            out.append(d)
            d += dt.timedelta(days=1)
        return out


def _clock(t: dt.datetime | dt.time) -> str:
    return t.strftime("%-I:%M %p")


def _google(club: Club, kind: str, lo: dt.date, hi: dt.date, session) -> list[Entry]:
    cid = (club.info.get("calendars") or {}).get(kind)
    key = (cid, lo, hi)
    if key in _cache and _cache[key][0] > time.time() - CACHE_SECONDS:
        return _cache[key][1]
    out, page = [], None
    while True:
        r = session.get(gcal.API.format(cid), params={"timeMin": f"{lo}T00:00:00Z", "timeMax": f"{hi + dt.timedelta(days=1)}T23:59:59Z",
                                                       "singleEvents": "true", "orderBy": "startTime", "maxResults": 250,
                                                       **({"pageToken": page} if page else {})})
        r.raise_for_status()
        j = r.json()
        for e in j.get("items", []):
            if e.get("status") == "cancelled":
                continue
            s, f = e["start"], e["end"]
            if "date" in s:  # all day; Google's end is the day after
                a, b, at = dt.date.fromisoformat(s["date"]), dt.date.fromisoformat(f["date"]) - dt.timedelta(days=1), None
            else:
                sa, sb = dt.datetime.fromisoformat(s["dateTime"]), dt.datetime.fromisoformat(f["dateTime"])
                a, b, at = sa.date(), max(sa.date(), (sb - dt.timedelta(seconds=1)).date()), _clock(sa)
            mine = e.get("extendedProperties", {}).get("private", {}).get("w8lbz") == "1"
            out.append(Entry(a, b, e.get("summary", "(no title)"), kind, at, e.get("location"), e["id"],
                             bool(e.get("recurringEventId")), mine))
        if not (page := j.get("nextPageToken")):
            break
    _cache[key] = (time.time(), out)
    return out


def _birthdays(club: Club, viewer: str, lo: dt.date, hi: dt.date) -> list[Entry]:
    """Only birthdays members chose to share with everyone (and your own), for every viewer, officers too: the
    calendar is where a club-wide greeting starts, so a private birthday never shows here. A month-only one
    shows on the 1st."""
    out = []
    for m in club.members.values():
        if not m.birthday or m.status.lower() in ("inactive", "silent key") or ("birthday" not in m.share and m.id != viewer):
            continue
        for year in range(lo.year, hi.year + 1):
            try:
                month = int(m.birthday[:2])
                if len(m.birthday) == 2:
                    d, title = dt.date(year, month, 1), f"{dt.date(2000, month, 1):%B} birthday: {m.name}"
                else:
                    day = int(m.birthday[3:5])
                    d = dt.date(year, month, 28 if (month, day) == (2, 29) and year % 4 else day)  # Feb 29 in other years: the 28th
                    title = f"Birthday: {m.name}"
            except ValueError:
                continue  # something odd typed into the sheet
            if lo <= d <= hi:
                out.append(Entry(d, d, title, "birthday"))
    return out


def entries(club: Club, archive, viewer: str, lo: dt.date, hi: dt.date, elevated: bool = False, session=None) -> tuple[list[Entry], str | None]:
    """Everything between lo and hi (inclusive), sorted, and a note if the Google Calendars couldn't be read."""
    out, note = [], None
    at = dt.time.fromisoformat(str(club.info.get("meeting_time", "19:30")))
    for d, x in chow.upcoming(chow.load(), lo, n=400):
        if d > hi.isoformat():
            break
        if x.get("status") != "none":
            day = dt.date.fromisoformat(d)
            out.append(Entry(day, day, f"Chow: {club.name(x['member']) if x.get('member') else 'open'}", "chow", _clock(at)))
    out += _birthdays(club, viewer, lo, hi)
    cals = club.info.get("calendars") or {}
    google = [k for k in READ if cals.get(k)] if not PREVIEW else []
    if google:
        try:
            s = session or gcal._session()
            for k in google:
                out += _google(club, k, lo, hi, s)
        except Exception as e:  # the page still shows chow and birthdays
            log.warning("couldn't read the club calendars: %s", e)
            note = "Couldn't reach the club's Google Calendars just now, so only chow nights and birthdays are shown."
    if "events" not in google:  # no club calendar to read: the weekly meeting and the minutes' dated events
        wd = chow.WEEKDAYS.index(str(club.info.get("meeting_day", "Thursday")).lower())
        d = lo + dt.timedelta(days=(wd - lo.weekday()) % 7)
        while d <= hi:
            out.append(Entry(d, d, "Club meeting", "events", _clock(at)))
            d += dt.timedelta(days=7)
        for e in gcal.latest_events(archive):
            if e.when:
                s, f, all_day = e.span()
                if s.date() <= hi and f.date() >= lo:
                    out.append(Entry(s.date(), f.date(), e.text, "events", None if all_day else _clock(s)))
    out.sort(key=lambda e: (e.start, e.time is not None, dt.datetime.strptime(e.time, "%I:%M %p").time() if e.time else dt.time(), e.title))
    return out, note


# ---------------- leaders editing the club calendars ----------------

def editable(club: Club) -> dict[str, str]:
    """Calendars leaders can edit here: kind -> label. None in a preview, or before calendars are set up."""
    cals = club.info.get("calendars") or {}
    return {} if PREVIEW else {k: KINDS[k] for k in READ if cals.get(k)}


def get(club: Club, kind: str, eid: str, session=None) -> dict:
    s = session or gcal._session()
    r = s.get(f"{gcal.API.format(club.info['calendars'][kind])}/{eid}")
    r.raise_for_status()
    return r.json()


def form_values(e: dict) -> dict:
    """A Google event as the form's fields (start/end date and time, the end being the last day)."""
    s, f = e["start"], e["end"]
    if "date" in s:
        last = dt.date.fromisoformat(f["date"]) - dt.timedelta(days=1)
        sd, st, ed, et = s["date"], "", (last.isoformat() if last.isoformat() != s["date"] else ""), ""
    else:
        a, b = dt.datetime.fromisoformat(s["dateTime"]), dt.datetime.fromisoformat(f["dateTime"])
        sd, st, ed, et = a.date().isoformat(), a.strftime("%H:%M"), b.date().isoformat(), b.strftime("%H:%M")
    return {"title": e.get("summary", ""), "where": e.get("location", ""), "notes": e.get("description", ""),
            "date": sd, "time": st, "end_date": ed, "end_time": et}


def body(club: Club, f: dict) -> dict:
    """The Google event for the form's fields. Raises ValueError with a message for the form."""
    from .console import _span
    title = " ".join((f.get("title") or "").split())[:200]
    if not title:
        raise ValueError("Give the event a title.")
    when = f.get("date", "") + ("T" + f["time"] if f.get("time") else "")
    end_day = f.get("end_date") or (f.get("date", "") if f.get("end_time") else "")
    until = end_day + ("T" + f["end_time"] if f.get("end_time") and end_day else "") if end_day else None
    when, until_ok = _span(when, until)
    if not when:
        raise ValueError("Pick the day it starts.")
    if until and not until_ok:
        raise ValueError("The end is before the start.")
    tz = str(club.info.get("timezone", "America/New_York"))
    return {"summary": title, "location": (f.get("where") or "").strip()[:200], "description": (f.get("notes") or "").strip()[:2000],
            **gcal.times(Item(title, when=when, until=until_ok), tz)}


SCOPES = {"this": "This date only", "following": "This and following dates", "all": "Every date"}
DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def _start(e: dict) -> dt.datetime:
    s = e["start"]
    return dt.datetime.fromisoformat(s["dateTime"]).replace(tzinfo=None) if "dateTime" in s else dt.datetime.fromisoformat(s["date"])


def _rules(rules: list[str], until: str | None = None, shift_days: int = 0) -> list[str]:
    """A repeating event's rules with a new end (UNTIL; COUNT dropped) and/or moved by some days, which also
    moves a single BYDAY (a weekly net moved from Tuesday to Wednesday). Raises ValueError if it can't."""
    out = []
    for r in rules:
        if r.startswith("RRULE:"):
            parts = [p for p in r[6:].split(";") if not (until and p.startswith(("UNTIL=", "COUNT=")))]
            if shift_days % 7:
                by = [p for p in parts if p.startswith("BYDAY=")]
                if by and not (len(by[0]) == 8 and by[0][6:] in DAYS):
                    raise ValueError("This event repeats on more than one day of the week; to move it to another day, "
                                     "change \"This date only\" or ask the account owner.")
                parts = [f"BYDAY={DAYS[(DAYS.index(p[6:]) + shift_days) % 7]}" if p.startswith("BYDAY=") else p for p in parts]
            r = "RRULE:" + ";".join(parts + ([f"UNTIL={until}"] if until else []))
        out.append(r)
    return out


def _until_before(instance: dict) -> str:
    """RRULE UNTIL that ends a series just before this occurrence (UTC for timed events, a date for all-day)."""
    o = instance.get("originalStartTime") or instance["start"]
    if "date" in o:
        return (dt.date.fromisoformat(o["date"]) - dt.timedelta(days=1)).strftime("%Y%m%d")
    return (dt.datetime.fromisoformat(o["dateTime"]).astimezone(dt.timezone.utc) - dt.timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")


def _shifted(master: dict, delta: dt.timedelta, b: dict, tz: str) -> dict:
    """The series' own start and end, moved as much as this date moved, keeping the new length."""
    start = _start(master) + delta
    if "date" in b["start"]:
        days = (dt.date.fromisoformat(b["end"]["date"]) - dt.date.fromisoformat(b["start"]["date"])).days
        return {"start": {"date": start.date().isoformat()}, "end": {"date": (start.date() + dt.timedelta(days=days)).isoformat()}}
    length = dt.datetime.fromisoformat(b["end"]["dateTime"]) - dt.datetime.fromisoformat(b["start"]["dateTime"])
    return gcal._timed(start, length.total_seconds() / 3600, tz)


def save(club: Club, kind: str, eid: str | None, f: dict, by: str, old_kind: str | None = None, session=None) -> dict:
    """Add (no eid) or change an event; moving it to another calendar adds it there and deletes the old one.
    For one date of a repeating event, f["scope"] says what changes: this date, this and following, or all."""
    s = session or gcal._session()
    b = body(club, f)
    url = gcal.API.format(club.info["calendars"][kind])
    scope = f.get("scope") or "this"
    inst = get(club, old_kind or kind, eid, s) if eid else None
    if inst and inst.get("recurringEventId") and scope in ("following", "all"):
        if old_kind and old_kind != kind:
            raise ValueError("Move a repeating event to another calendar one date at a time, or ask the account owner.")
        master = get(club, kind, inst["recurringEventId"], s)
        delta = _start(b) - _start({"start": inst.get("originalStartTime") or inst["start"]})
        tz = str(club.info.get("timezone", "America/New_York"))
        first = _start(master) == _start({"start": inst.get("originalStartTime") or inst["start"]})
        if scope == "all" or first:
            new = {**b, **_shifted(master, delta, b, tz), "recurrence": _rules(master.get("recurrence", []), shift_days=delta.days)}
            s.patch(f"{url}/{master['id']}", json=new).raise_for_status()
            _log(by, "changed every date of", kind, b)
        else:
            new = {**b, "recurrence": _rules(master.get("recurrence", []), shift_days=delta.days)}
            s.patch(f"{url}/{master['id']}", json={"recurrence": _rules(master.get("recurrence", []), until=_until_before(inst))}).raise_for_status()
            s.post(url, json=new).raise_for_status()
            _log(by, "changed this and following dates of", kind, b)
        return b
    if eid and old_kind and old_kind != kind:
        s.post(url, json=b).raise_for_status()
        s.delete(f"{gcal.API.format(club.info['calendars'][old_kind])}/{eid}").raise_for_status()
        action = "moved"
    elif eid:
        s.patch(f"{url}/{eid}", json=b).raise_for_status()
        action = "changed"
    else:
        s.post(url, json=b).raise_for_status()
        action = "added"
    _log(by, action, kind, b)
    return b


def delete(club: Club, kind: str, eid: str, by: str, scope: str = "this", session=None) -> None:
    s = session or gcal._session()
    url = gcal.API.format(club.info["calendars"][kind])
    old = get(club, kind, eid, s)
    if old.get("recurringEventId") and scope in ("following", "all"):
        master = get(club, kind, old["recurringEventId"], s)
        if scope == "all" or _start(master) == _start({"start": old.get("originalStartTime") or old["start"]}):
            s.delete(f"{url}/{master['id']}").raise_for_status()
            _log(by, "deleted every date of", kind, old)
        else:
            s.patch(f"{url}/{master['id']}", json={"recurrence": _rules(master.get("recurrence", []), until=_until_before(old))}).raise_for_status()
            _log(by, "deleted this and following dates of", kind, old)
        return
    s.delete(f"{url}/{eid}").raise_for_status()
    _log(by, "deleted", kind, old)


def _log(by: str, action: str, kind: str, e: dict) -> None:
    """Who changed what on the club calendars (calendar-log.jsonl in the data folder); clears the page's cache."""
    _cache.clear()
    start = e.get("start", {})
    with (DATA / "calendar-log.jsonl").open("a") as fh:
        fh.write(json.dumps({"at": int(time.time()), "by": by, "action": action, "calendar": kind,
                             "title": e.get("summary"), "start": start.get("date") or start.get("dateTime")}) + "\n")


def history(n: int = 50) -> list[dict]:
    p = DATA / "calendar-log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()][::-1][:n] if p.exists() else []
