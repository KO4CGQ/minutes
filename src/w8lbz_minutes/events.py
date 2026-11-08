"""The club calendars, kept by the app: events on the Club events, Planning and Community calendars.

events.json in the data folder: {"events": [event, ...]}. An event:
    id, cal ("events" | "planning" | "community"), title, where, notes,
    start: "YYYY-MM-DD" (all day) or "YYYY-MM-DDTHH:MM", end: the same forms or None (the first date's end),
    repeat: None or {"freq": "weekly" | "monthly" | "monthly_weekday" | "yearly" | "yearly_weekday", "interval": 1, "last": False,
                     "until": "YYYY-MM-DD" or None},
    skip: [dates taken out of a repeating event], changes: {date: {title, where, notes, start, end}} (one date changed),
    uid: the .ics UID it was imported from, if any.
Google Calendar, if set up, gets a copy (gcal.py); nothing here depends on it.
"""
from __future__ import annotations

import calendar as monthcal
import datetime as dt
import fcntl
import html
import json
import re
import secrets
import time
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from .club import DATA

CALS = {"events": "Club events", "planning": "Planning", "community": "Community"}
REPEATS = ("", "weekly", "biweekly", "monthly", "monthly_weekday", "monthly_last", "yearly", "yearly_weekday")  # the form's choices
SCOPES = {"this": "This date only", "following": "This and following dates", "all": "Every date"}
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
FIELDS = ("title", "where", "notes", "start", "end")


# ---------------- storage ----------------

def _path():
    return DATA / "events.json"


@contextmanager
def locked():
    """One change at a time (the web app has several threads)."""
    with open(DATA / "events.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def load() -> list[dict]:
    p = _path()
    return json.loads(p.read_text())["events"] if p.exists() else []


def save(evs: list[dict]) -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps({"events": evs}, indent=1))
    tmp.replace(_path())


def ensure_meeting(club) -> None:
    """Put the weekly meeting on the Club events calendar once (club.meeting_day and meeting_time), as a real
    repeating event leaders can change: skip a holiday, move a date. Not again if a leader deletes it."""
    marker = DATA / "events-meeting-added"
    if marker.exists():
        return
    with locked():
        if marker.exists():
            return
        from .chow import WEEKDAYS
        wd = WEEKDAYS.index(str(club.info.get("meeting_day", "Thursday")).lower())
        today = dt.date.today()
        first = today + dt.timedelta(days=(wd - today.weekday()) % 7)
        at = str(club.info.get("meeting_time", "19:30"))
        start = dt.datetime.fromisoformat(f"{first}T{at}")
        evs = load()
        e = add(evs, "events", {"title": f"{club.info.get('callsign', 'Club')} meeting", "where": "", "notes": "",
                                "start": _iso(start, True), "end": _iso(start + dt.timedelta(hours=2), True),
                                "repeat": {"freq": "weekly", "interval": 1, "last": False, "until": None}}, "the app")
        e["meeting"] = True
        save(evs)
        marker.write_text(e["id"])


def get(evs: list[dict], eid: str) -> dict:
    e = next((x for x in evs if x["id"] == eid), None)
    if not e:
        raise KeyError(eid)
    return e


# ---------------- dates ----------------

def _dt(v: str) -> dt.datetime:
    return dt.datetime.fromisoformat(v)


def _iso(d: dt.datetime, timed: bool) -> str:
    return d.strftime("%Y-%m-%dT%H:%M") if timed else d.date().isoformat()


def length(e: dict) -> dt.timedelta:
    """How long each date lasts (for all-day events, the extra days)."""
    return _dt(e["end"]) - _dt(e["start"]) if e.get("end") else dt.timedelta(0)


def repeat_label(start: str, r: dict | None) -> str:
    if not r:
        return "Doesn't repeat"
    d = _dt(start)
    every = {"weekly": f"Every {d:%A}" if r.get("interval", 1) == 1 else f"Every {r['interval']} weeks on {d:%A}",
             "monthly": f"Monthly on the {d.day}{'th' if 10 <= d.day % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d.day % 10, 'th')}",
             "monthly_weekday": f"Monthly on the {'last' if r.get('last') else ORDINAL.get((d.day - 1) // 7 + 1, 'last')} {d:%A}",
             "yearly": f"Every year on {d:%B} {d.day}",
             "yearly_weekday": f"Every year on the {'last' if r.get('last') else ORDINAL.get((d.day - 1) // 7 + 1, 'last')} {d:%A} of {d:%B}"}[r["freq"]]
    return every + (f", until {_dt(r['until']):%b %-d, %Y}" if r.get("until") else "")


def _nth_weekday(year: int, month: int, weekday: int, n: int, last: bool) -> dt.date | None:
    days = [d for d in range(1, monthcal.monthrange(year, month)[1] + 1) if dt.date(year, month, d).weekday() == weekday]
    if last:
        return dt.date(year, month, days[-1])
    return dt.date(year, month, days[n - 1]) if n <= len(days) else None


def dates(e: dict, lo: dt.date, hi: dt.date) -> list[dt.date]:
    """The original dates of an event that fall in lo..hi (counting multi-day events by their first day),
    skipped dates left out."""
    first = _dt(e["start"]).date()
    r = e.get("repeat")
    span = length(e).days
    if not r:
        return [first] if lo - dt.timedelta(days=span) <= first <= hi else []
    until = min(hi, dt.date.fromisoformat(r["until"])) if r.get("until") else hi
    lo = max(lo - dt.timedelta(days=span), first)
    out, n = [], r.get("interval", 1)
    if r["freq"] == "weekly":
        step = 7 * n
        k = max(0, -(-((lo - first).days) // step))
        d = first + dt.timedelta(days=k * step)
        while d <= until:
            out.append(d)
            d += dt.timedelta(days=step)
    else:
        nth, months = (first.day - 1) // 7 + 1, 12 * n if r["freq"] in ("yearly", "yearly_weekday") else n
        i = 0
        while True:
            y, m = divmod(first.month - 1 + i * months, 12)
            y, m = first.year + y, m + 1
            if dt.date(y, m, 1) > until:
                break
            if r["freq"] in ("monthly_weekday", "yearly_weekday"):
                d = _nth_weekday(y, m, first.weekday(), nth, r.get("last", False))
            else:
                d = dt.date(y, m, first.day) if first.day <= monthcal.monthrange(y, m)[1] else None
            if d and lo <= d <= until:
                out.append(d)
            i += 1
    skip = set(e.get("skip", []))
    return [d for d in out if d.isoformat() not in skip]


def occurrence(e: dict, d: dt.date) -> dict:
    """The event as it is on date d (an original date): moved to that day, with any one-date changes."""
    start = _dt(e["start"])
    timed = "T" in e["start"]
    moved = dt.datetime.combine(d, start.time())
    o = {"title": e["title"], "where": e.get("where", ""), "notes": e.get("notes", ""),
         "start": _iso(moved, timed), "end": _iso(moved + length(e), timed) if e.get("end") else None}
    return {**o, **e.get("changes", {}).get(d.isoformat(), {})}


# ---------------- changes from the form ----------------

def from_form(f: dict) -> dict:
    """title, where, notes, start, end and repeat from the form's fields. Raises ValueError for the form."""
    title = " ".join((f.get("title") or "").split())[:200]
    if not title:
        raise ValueError("Give the event a title.")
    try:
        day = dt.date.fromisoformat(f.get("date") or "")
    except ValueError:
        raise ValueError("Pick the day it starts.")
    t, ed, et = f.get("time") or "", f.get("end_date") or "", f.get("end_time") or ""
    start = f"{day}T{t}" if t else day.isoformat()
    try:
        _dt(start)
        if t:  # timed: an end time (that day, or the last day), or only a last day (same time)
            end = f"{ed or day}T{et or t}" if (ed or et) else None
        else:  # all day: only a later last day counts
            end = ed if ed and ed > day.isoformat() else None
        if end and _dt(end) <= _dt(start):
            raise ValueError("end")
    except ValueError as x:
        raise ValueError("The end is before the start." if str(x) == "end" else "That date or time isn't right.")
    rep = f.get("repeat") or ""
    if rep not in REPEATS:
        raise ValueError("Pick how it repeats.")
    repeat = None
    if rep:
        until = f.get("repeat_until") or None
        if until and until < day.isoformat():
            raise ValueError("It stops repeating before it starts.")
        repeat = {"freq": {"biweekly": "weekly", "monthly_last": "monthly_weekday"}.get(rep, rep),
                  "interval": 2 if rep == "biweekly" else 1, "last": rep == "monthly_last" or (rep == "yearly_weekday" and day.day > 28), "until": until}
    return {"title": title, "where": " ".join((f.get("where") or "").split())[:200],
            "notes": (f.get("notes") or "").strip()[:2000], "start": start, "end": end, "repeat": repeat}


def form_values(e: dict, d: str | None) -> dict:
    """The form's fields for an event (on date d, for a repeating one)."""
    o = occurrence(e, dt.date.fromisoformat(d)) if d else e
    r = e.get("repeat")
    rep = "" if not r else "biweekly" if r.get("interval") == 2 else "monthly_last" if r.get("last") and r["freq"] == "monthly_weekday" else r["freq"]
    end = o.get("end") or ""
    return {"title": o["title"], "where": o.get("where", ""), "notes": o.get("notes", ""), "cal": e["cal"],
            "date": o["start"][:10], "time": o["start"][11:16], "end_date": end[:10] if end[:10] != o["start"][:10] else "",
            "end_time": end[11:16], "repeat": rep, "repeat_until": (r or {}).get("until") or ""}


def add(evs: list[dict], cal: str, v: dict, by: str) -> dict:
    e = {"id": secrets.token_hex(5), "cal": cal, **v, "skip": [], "changes": {}, "by": by, "at": int(time.time())}
    evs.append(e)
    return e


def change(evs: list[dict], eid: str, d: str | None, cal: str, v: dict, scope: str = "all") -> None:
    """Change an event; for one date d of a repeating event, scope says this date, this and following, or every date."""
    e = get(evs, eid)
    if not e.get("repeat") or not d:
        e.update(cal=cal, **v)
        return
    first = _dt(e["start"]).date().isoformat()
    if scope == "this":
        e["changes"][d] = {k: v[k] for k in FIELDS}
        return
    # every date (or following from the first date): the series moves by as much as this date moved
    was = _dt(occurrence(e, dt.date.fromisoformat(d))["start"])
    delta = _dt(v["start"]) - was
    timed = "T" in v["start"]
    new_start = _dt(e["start"]) + delta if scope == "all" or d == first else _dt(v["start"])
    new_start = new_start if timed else dt.datetime.combine(new_start.date(), dt.time())
    dur = length({"start": v["start"], "end": v["end"]}) if v["end"] else None
    moved = {**v, "start": _iso(new_start, timed), "end": _iso(new_start + dur, timed) if dur else None, "cal": cal}
    if scope == "all" or d == first:
        e.update(moved)
        if delta:  # one-date changes and skips belonged to the old dates
            e["changes"], e["skip"] = {}, []
        return
    old_until = e["repeat"].get("until")
    e["repeat"] = {**e["repeat"], "until": (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()}
    later = {k: x for k, x in e["changes"].items() if k >= d}
    e["changes"] = {k: x for k, x in e["changes"].items() if k < d}
    rest = {"id": secrets.token_hex(5), **{k: x for k, x in e.items() if k not in ("id", "uid", "changes", "skip")}, **moved,
            "changes": later if not delta else {}, "skip": [s for s in e["skip"] if s >= d] if not delta else []}
    if rest["repeat"] and not v.get("repeat"):
        rest["repeat"] = None
    elif rest["repeat"]:
        rest["repeat"] = {**rest["repeat"], "until": v["repeat"].get("until") or old_until}
    evs.append(rest)


def delete(evs: list[dict], eid: str, d: str | None, scope: str = "all") -> dict:
    """Delete an event, or for a repeating one: this date, this and following, or every date."""
    e = get(evs, eid)
    first = _dt(e["start"]).date().isoformat()
    if e.get("repeat") and d and scope == "this":
        e.setdefault("skip", []).append(d)
        e["changes"].pop(d, None)
    elif e.get("repeat") and d and scope == "following" and d != first:
        e["repeat"] = {**e["repeat"], "until": (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()}
    else:
        evs.remove(e)
    return e


def log(by: str, action: str, e: dict) -> None:
    """Who changed what on the club calendars (calendar-log.jsonl in the data folder)."""
    with (DATA / "calendar-log.jsonl").open("a") as fh:
        fh.write(json.dumps({"at": int(time.time()), "by": by, "action": action, "calendar": e.get("cal"),
                             "title": e.get("title"), "start": e.get("start")}) + "\n")


def history(n: int = 50) -> list[dict]:
    p = DATA / "calendar-log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()][::-1][:n] if p.exists() else []


# ---------------- importing an .ics file (Google, Outlook, Apple all export these) ----------------

DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def _unfold(text: str) -> list[str]:
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def _prop(line: str) -> tuple[str, dict, str]:
    """'DTSTART;TZID=America/New_York:20261110T200000' -> ('DTSTART', {'TZID': ...}, '20261110T200000')."""
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), dict(p.split("=", 1) for p in params if "=" in p), value


def _text(v: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v).strip()


def _plain(v: str) -> str:
    """Google exports descriptions as HTML: keep the text and the line breaks, drop the tags."""
    if not re.search(r"<(p|br|a|b|i|u|em|strong|ul|ol|li|div|span)\b", v, re.I):
        return v
    v = re.sub(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", lambda m: m.group(2) if m.group(2) == m.group(1) else f"{m.group(2)} ({m.group(1)})", v, flags=re.I | re.S)
    v = re.sub(r"<br\s*/?>|</(p|div|li)>", "\n", v, flags=re.I)
    v = re.sub(r"<li\b[^>]*>", "- ", v, flags=re.I)
    v = html.unescape(re.sub(r"<[^>]+>", "", v))
    return re.sub(r"\n{3,}", "\n\n", v).strip()


def _when(value: str, params: dict, tz: ZoneInfo) -> tuple[dt.datetime, bool]:
    """(club-time datetime, timed?) from an .ics date or date-time."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v[:8], "%Y%m%d"), False
    d = dt.datetime.strptime(v.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        d = d.replace(tzinfo=dt.timezone.utc)
    else:
        try:
            d = d.replace(tzinfo=ZoneInfo(params["TZID"].strip('"'))) if "TZID" in params else d.replace(tzinfo=tz)
        except Exception:  # an unknown Windows-style TZID: take it as club time
            d = d.replace(tzinfo=tz)
    return d.astimezone(tz).replace(tzinfo=None), True


def _duration(v: str) -> dt.timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return dt.timedelta(0)
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def _repeat(rule: str, start: dt.datetime, tz: ZoneInfo) -> tuple[dict | None, str | None]:
    """(repeat, None) or (None, why it can't be kept) for an RRULE."""
    r = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    freq, n = r.get("FREQ"), int(r.get("INTERVAL", "1"))
    extra = set(r) - {"FREQ", "INTERVAL", "UNTIL", "COUNT", "BYDAY", "BYMONTHDAY", "BYMONTH", "WKST"}
    if extra:
        return None, f"repeats in a way the app doesn't handle ({', '.join(sorted(extra))})"
    by = r.get("BYDAY", "")
    out = {"interval": n, "last": False, "until": None}
    if freq == "WEEKLY":
        if by and by != DAYS[start.weekday()]:
            return None, "repeats on more than one day of the week" if "," in by else "repeats on a different weekday than it starts"
        out["freq"] = "weekly"
    elif freq == "MONTHLY":
        m = re.fullmatch(r"([+-]?\d)?(MO|TU|WE|TH|FR|SA|SU)", by) if by else None
        if by and not m:
            return None, "repeats on more than one day of the month"
        if m:
            nth = int(m.group(1) or 0)
            if m.group(2) != DAYS[start.weekday()] or nth not in (-1, (start.day - 1) // 7 + 1):
                return None, "repeats on a different day of the month than it starts"
            out.update(freq="monthly_weekday", last=nth == -1)
        else:
            out["freq"] = "monthly"
    elif freq == "YEARLY" and not by:
        out["freq"] = "yearly"
    elif freq == "YEARLY":  # the first Thursday of January
        m = re.fullmatch(r"([+-]?\d)(MO|TU|WE|TH|FR|SA|SU)", by)
        nth = int(m.group(1)) if m else 0
        if not m or m.group(2) != DAYS[start.weekday()] or nth not in (-1, (start.day - 1) // 7 + 1) \
                or r.get("BYMONTH", str(start.month)) != str(start.month):
            return None, "repeats yearly on a day the app can't match to its start"
        out.update(freq="yearly_weekday", last=nth == -1)
    else:
        return None, "repeats daily or in a way the app doesn't handle"
    if "UNTIL" in r:
        out["until"] = _when(r["UNTIL"], {}, tz)[0].date().isoformat()
    elif "COUNT" in r:
        probe = {"start": start.isoformat(), "end": None, "repeat": {**out, "until": None}}
        got = dates(probe, start.date(), start.date() + dt.timedelta(days=366 * 30))
        out["until"] = got[min(int(r["COUNT"]), len(got)) - 1].isoformat() if got else None
    return out, None


def import_ics(evs: list[dict], text: str, cal: str, by: str, tz_name: str, since: dt.date) -> dict:
    """Add the events of an .ics file to calendar `cal`. Events seen before (same UID) are updated; ones that
    look already entered (same title, same first day) are left alone; events that ended before `since` are
    skipped. Returns {"added": [...], "updated": [...], "already": [...], "skipped": [(title, why)]}."""
    tz = ZoneInfo(tz_name)
    report = {"added": [], "updated": [], "already": [], "skipped": []}
    blocks, cur = [], None
    for line in _unfold(text):
        name, params, value = _prop(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            cur = {}
        elif name == "END" and value.upper() == "VEVENT" and cur is not None:
            blocks.append(cur)
            cur = None
        elif cur is not None:
            cur.setdefault(name, []).append((params, value))
    one = lambda b, k: b[k][0] if k in b else (None, None)  # noqa: E731
    masters, overrides = [], []
    for b in blocks:
        (overrides if "RECURRENCE-ID" in b else masters).append(b)
    by_uid = {}
    for b in masters:
        title = _text(one(b, "SUMMARY")[1] or "(no title)")[:200]
        if (one(b, "STATUS")[1] or "").upper() == "CANCELLED":
            continue
        try:
            start, timed = _when(one(b, "DTSTART")[1], one(b, "DTSTART")[0], tz)
        except (TypeError, ValueError):
            report["skipped"].append((title, "has no start date the app can read"))
            continue
        if "DTEND" in b:
            end, _ = _when(one(b, "DTEND")[1], one(b, "DTEND")[0], tz)
        elif "DURATION" in b:
            end = start + _duration(one(b, "DURATION")[1])
        else:
            end = start + (dt.timedelta(days=1) if not timed else dt.timedelta(0))
        if not timed:
            end -= dt.timedelta(days=1)  # .ics all-day ends are the day after
        rule = one(b, "RRULE")[1] if "RRULE" in b else None
        weekdays = re.search(r"BYDAY=([A-Z]{2}(?:,[A-Z]{2})+)", rule or "")
        if weekdays and "FREQ=WEEKLY" in rule and "COUNT=" not in rule:
            # a net on Tuesdays and Thursdays: one weekly event per day, each starting on its first such day
            for code in weekdays.group(1).split(","):
                shift = dt.timedelta(days=(DAYS.index(code) - start.weekday()) % 7)
                part = {**b, "DTSTART": [({}, (start + shift).strftime("%Y%m%dT%H%M%S" if timed else "%Y%m%d"))],
                        "RRULE": [({}, rule.replace(weekdays.group(0), f"BYDAY={code}"))], "UID": [({}, f"{one(b, 'UID')[1]}#{code}")]}
                part.pop("DTEND", None)
                long = end - start + (dt.timedelta(days=1) if not timed else dt.timedelta(0))  # undo the all-day end shift above
                if long > dt.timedelta(0):
                    part["DURATION"] = [({}, f"PT{int(long.total_seconds()) // 60}M")]
                masters.append(part)
            continue
        repeat, why = (None, None)
        if rule:
            repeat, why = _repeat(rule, start, tz)
            if why:
                report["skipped"].append((title, why))
                continue
        last = dt.date.fromisoformat(repeat["until"]) if repeat and repeat.get("until") else end.date() if not repeat else dt.date.max
        if last < since:
            continue
        v = {"title": title, "where": _text(one(b, "LOCATION")[1] or "")[:200], "notes": _plain(_text(one(b, "DESCRIPTION")[1] or ""))[:2000],
             "start": _iso(start, timed), "end": (_iso(end, timed) if end > start else None), "repeat": repeat}
        skips = [_when(x, p, tz)[0].date().isoformat() for p, vals in b.get("EXDATE", []) for x in vals.split(",")]
        uid = one(b, "UID")[1]
        known = next((x for x in evs if uid and x.get("uid") == uid), None)
        if known:
            known.update(cal=cal, **v, skip=skips, changes={})
            report["updated"].append(title)
            by_uid[uid] = known
            continue
        if any(x["cal"] == cal and x["title"].lower() == title.lower() and x["start"][:10] == v["start"][:10] for x in evs):
            report["already"].append(title)
            continue
        e = add(evs, cal, v, by)
        e["skip"], e["uid"] = skips, uid
        report["added"].append(title)
        if uid:
            by_uid[uid] = e
    for b in overrides:  # one date of a repeating event changed (or cancelled) in the other calendar
        e = by_uid.get(one(b, "UID")[1])
        if not e or not e.get("repeat"):
            continue
        rid = _when(one(b, "RECURRENCE-ID")[1], one(b, "RECURRENCE-ID")[0], tz)[0].date().isoformat()
        if (one(b, "STATUS")[1] or "").upper() == "CANCELLED":
            e["skip"].append(rid)
            continue
        try:
            s, timed = _when(one(b, "DTSTART")[1], one(b, "DTSTART")[0], tz)
            f = _when(one(b, "DTEND")[1], one(b, "DTEND")[0], tz)[0] if "DTEND" in b else s
        except (TypeError, ValueError):
            continue
        f = f - dt.timedelta(days=1) if not timed and "DTEND" in b else f
        e["changes"][rid] = {"title": _text(one(b, "SUMMARY")[1] or e["title"])[:200], "where": _text(one(b, "LOCATION")[1] or "")[:200],
                             "notes": _plain(_text(one(b, "DESCRIPTION")[1] or ""))[:2000], "start": _iso(s, timed),
                             "end": _iso(f, timed) if f > s else None}
    return report
