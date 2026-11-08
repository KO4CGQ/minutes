"""The Calendar page, Home's "Coming up" and the calendar feeds: the club's own calendars (events.py: Club events,
Planning, Community), the minutes' dated Upcoming Events, chow nights and shared birthdays. Nothing here needs Google.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from . import chow, events as store, gcal
from .club import DATA, Club

KINDS = {"events": "Club events", "chow": "Chow", "planning": "Planning", "community": "Community", "birthday": "Birthdays"}


@dataclass
class Entry:
    start: dt.date
    end: dt.date  # last day, inclusive
    title: str
    kind: str
    time: str | None = None  # "7:30 PM", timed events only
    where: str | None = None
    id: str | None = None  # the app event's id (editable on the page)
    repeats: bool = False
    from_minutes: bool = False  # the minutes' Upcoming Events: changed in the leader console
    starts: dt.datetime | None = None  # timed events: exact start and end (club time), for the calendar feed
    ends: dt.datetime | None = None
    occ: str | None = None  # a repeating event's original date (which date to edit)
    notes: str = ""
    zone: str = ""  # an event kept in another time zone: its times there ("Sat Jun 27 18:00 – Sun Jun 28 20:59 UTC")

    def days(self, lo: dt.date, hi: dt.date) -> list[dt.date]:
        """The days it covers within lo..hi (a long event is listed on each day, up to two weeks)."""
        d, out = max(self.start, lo), []
        while d <= min(self.end, hi, self.start + dt.timedelta(days=13)):
            out.append(d)
            d += dt.timedelta(days=1)
        return out


def _clock(t: dt.datetime | dt.time) -> str:
    return t.strftime("%-I:%M %p")


def _entry(o: dict, kind: str, **kw) -> Entry:
    """An Entry from an occurrence's start/end strings."""
    s = dt.datetime.fromisoformat(o["start"])
    timed = "T" in o["start"]
    f = dt.datetime.fromisoformat(o["end"]) if o.get("end") else (s + dt.timedelta(hours=store_hours()) if timed else s)
    return Entry(s.date(), f.date() if not timed else max(s.date(), (f - dt.timedelta(seconds=1)).date()), o["title"], kind,
                 _clock(s) if timed else None, o.get("where") or None, starts=s if timed else None, ends=f if timed else None,
                 notes=o.get("notes", ""), **kw)


def _zone(o: dict, tz: str | None, club_tz: str) -> str:
    if not tz or tz == club_tz or "T" not in o["start"]:
        return ""
    s = dt.datetime.fromisoformat(o["start"])
    f = dt.datetime.fromisoformat(o["end"]) if o.get("end") else s + dt.timedelta(hours=store_hours())
    abbr = s.replace(tzinfo=ZoneInfo(tz)).tzname()
    return f"{s:%H:%M} – {f:%H:%M} {abbr}" if s.date() == f.date() else f"{s:%a %b %-d %H:%M} – {f:%a %b %-d %H:%M} {abbr}"


def store_hours() -> int:
    from .meeting import EVENT_HOURS
    return EVENT_HOURS


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


def public_kinds(club: Club) -> list[str]:
    """The calendars an admin made public (`club.public_calendars`): shown without signing in. Never chow or birthdays."""
    return [k for k in club.info.get("public_calendars") or [] if k in store.CALS]


def entries(club: Club, archive, viewer: str | None, lo: dt.date, hi: dt.date, public: bool = False) -> tuple[list[Entry], None]:
    """Everything between lo and hi (inclusive), sorted. public: only the public calendars, no chow or birthdays.
    (The second value is kept for callers that show a note; with no outside calendar to reach, there's none.)"""
    out: list[Entry] = []
    store.ensure_meeting(club)
    kinds = public_kinds(club) if public else list(store.CALS)
    tz = str(club.info.get("timezone", "America/New_York"))
    for e in store.load():
        if e["cal"] not in kinds:
            continue
        for d in store.dates(e, lo - dt.timedelta(days=1), hi + dt.timedelta(days=1)):  # a day either side: another zone's date can differ
            o = store.occurrence(e, d)
            x = _entry(store.in_zone(o, e.get("tz"), tz), e["cal"], id=e["id"], repeats=bool(e.get("repeat")),
                       occ=d.isoformat() if e.get("repeat") else None, zone=_zone(o, e.get("tz"), tz))
            if x.end >= lo and x.start <= hi:
                out.append(x)
    if "events" in kinds:  # the minutes' dated Upcoming Events, unless the same event is already on the calendar
        seen = {(x.start, x.title.lower()) for x in out}
        for i in gcal.latest_events(archive):
            if i.when:
                s, f, all_day = i.span()
                o = {"title": i.text, "start": i.when, "end": i.until or (None if all_day else f.strftime("%Y-%m-%dT%H:%M"))}
                x = _entry(o, "events", from_minutes=True)
                if x.end >= lo and x.start <= hi and (x.start, x.title.lower()) not in seen:
                    out.append(x)
    if not public:
        at = dt.time.fromisoformat(str(club.info.get("meeting_time", "19:30")))
        for d, x in chow.upcoming(chow.load(), lo, n=400):
            if d > hi.isoformat():
                break
            if x.get("status") != "none":
                day = dt.date.fromisoformat(d)
                t = dt.datetime.combine(day, at)
                out.append(Entry(day, day, f"Chow: {club.name(x['member']) if x.get('member') else 'open'}", "chow", _clock(at),
                                 starts=t, ends=t + dt.timedelta(hours=2)))
        out += _birthdays(club, viewer, lo, hi)
    out.sort(key=_order)
    return out, None


def _order(e: Entry):
    return e.start, e.time is not None, e.starts.time() if e.starts else dt.time(), e.title


# ---------------- subscribing from a calendar app (.ics feeds) ----------------

def feed_token(mid: str, reset: bool = False) -> str:
    """The secret in a member's private feed address (calendar-feeds.json). reset: a new one; the old link stops working."""
    import secrets
    p = DATA / "calendar-feeds.json"
    tokens = json.loads(p.read_text()) if p.exists() else {}
    if reset or mid not in tokens:
        tokens[mid] = secrets.token_urlsafe(24)
        p.write_text(json.dumps(tokens))
        p.chmod(0o600)
    return tokens[mid]


def member_for_feed(token: str) -> str | None:
    import hmac
    p = DATA / "calendar-feeds.json"
    tokens = json.loads(p.read_text()) if p.exists() else {}
    return next((m for m, t in tokens.items() if hmac.compare_digest(t, token)), None)


def _ics_text(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """iCalendar lines are at most 75 bytes; longer ones continue on lines starting with a space."""
    out, b = [], line.encode()
    while len(b) > 75:
        cut = 75 if not out else 74
        while cut and (b[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode())
        b = b[cut:]
    out.append(b.decode())
    return "\r\n ".join(out)


def ics(items: list[Entry], name: str, tz: str) -> str:
    """An iCalendar file of these entries, for Apple Calendar, Outlook, Google and phones to subscribe to."""
    import hashlib
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//W8LBZ Minutes//Club calendar//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{_ics_text(name)}", f"X-WR-TIMEZONE:{tz}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for e in items:
        uid = hashlib.sha1(f"{e.kind}|{e.id or e.title}|{e.start}".encode()).hexdigest()
        lines += ["BEGIN:VEVENT", f"UID:{uid}@w8lbz-minutes", f"DTSTAMP:{stamp}", f"SUMMARY:{_ics_text(e.title)}",
                  f"CATEGORIES:{_ics_text(KINDS[e.kind])}"]
        if e.starts:
            lines += [f"DTSTART;TZID={tz}:{e.starts:%Y%m%dT%H%M%S}", f"DTEND;TZID={tz}:{e.ends:%Y%m%dT%H%M%S}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{e.start:%Y%m%d}", f"DTEND;VALUE=DATE:{e.end + dt.timedelta(days=1):%Y%m%d}"]
        if e.where:
            lines.append(f"LOCATION:{_ics_text(e.where)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
