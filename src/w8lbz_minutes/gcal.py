"""Optional: a copy of the club's calendars in Google Calendar, for people who follow them there. The app keeps
the calendars itself (events.py); this only ever writes to Google, so Google being down changes nothing in the app.
Copied: chow nights, the app's Club events / Planning / Community events (each date of a repeating one, a year
ahead), and the minutes' dated Upcoming Events.

club.yaml names the calendars (`calendars: {chow: ID, events: ID, planning: ID, community: ID}`, any of them);
each is shared with the service account
("Make changes to events"). The app marks the events it creates (a private `w8lbz` property) and only ever
changes or deletes those, from today on, so people can keep adding their own events by hand and past events
stay as they were. Each sync compares what should be there with what is, so it's safe to run any time.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time

from . import chow, events as store
from .club import DATA, PREVIEW, Club
from .meeting import EVENT_HOURS, Item

log = logging.getLogger(__name__)
API = "https://www.googleapis.com/calendar/v3/calendars/{}/events"


def _session():
    import google.auth.transport.requests as gr
    from google.oauth2 import service_account
    from .roster import KEY_FILE
    creds = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=["https://www.googleapis.com/auth/calendar.events"])
    return gr.AuthorizedSession(creds)


def _timed(start: dt.datetime, hours: float, tz: str) -> dict:
    return {"start": {"dateTime": start.isoformat(timespec="seconds"), "timeZone": tz},
            "end": {"dateTime": (start + dt.timedelta(hours=hours)).isoformat(timespec="seconds"), "timeZone": tz}}


def times(e, tz: str) -> dict:
    """Google's start and end for an Item with a `when` (and maybe `until`)."""
    start, end, all_day = e.span()
    if all_day:  # Google's all-day end is the day after the last day
        return {"start": {"date": start.date().isoformat()}, "end": {"date": (end.date() + dt.timedelta(days=1)).isoformat()}}
    return _timed(start, (end - start).total_seconds() / 3600, tz)


def wanted(club: Club, events: list, today: dt.date) -> dict[str, dict[str, dict]]:
    """Calendar kind -> {key: event} for everything from today on: chow nights, the app's own events, and the
    minutes' dated Upcoming Events (`events`) not already on the Club events calendar."""
    tz = str(club.info.get("timezone", "America/New_York"))
    at = dt.time.fromisoformat(str(club.info.get("meeting_time", "19:30")))
    out: dict[str, dict[str, dict]] = {"chow": {}, **{k: {} for k in store.CALS}}
    for date, x in chow.upcoming(chow.load(), today, n=60):
        if x.get("status") == "none":
            continue
        who = club.name(x["member"]) if x.get("member") else "open, nobody yet"
        out["chow"][f"chow:{date}"] = {"summary": f"Chow: {who}",
                                       **_timed(dt.datetime.combine(dt.date.fromisoformat(date), at), EVENT_HOURS, tz)}
    seen = set()
    store.ensure_meeting(club)
    for e in store.load():
        for d in store.dates(e, today - store.reach(e), today + dt.timedelta(days=365)):
            o = store.in_zone(store.occurrence(e, d), e.get("tz"), tz)
            if (o["end"] or o["start"])[:10] < today.isoformat():  # moved into the past
                continue
            seen.add((o["start"][:10], o["title"].lower()))
            out[e["cal"]][f"ev:{e['id']}:{d}"] = {"summary": o["title"], "location": o.get("where") or "",
                                                  "description": o.get("notes") or "",
                                                  **times(Item(o["title"], when=o["start"], until=o["end"]), tz)}
    from .calview import due_dates, elections  # business due dates on Planning, election days on Community
    for kind, xs in (("planning", due_dates(club, today, today + dt.timedelta(days=365))), ("community", elections(today, today + dt.timedelta(days=365)))):
        for x in xs:
            out[kind][x.id] = {"summary": x.title, "description": x.notes, **times(Item(x.title, when=x.start.isoformat()), tz)}
    for e in events:
        if not e.when or (e.until or e.when)[:10] < today.isoformat() or (e.when[:10], e.text.lower()) in seen:
            continue
        out["events"][f"event:{e.when}:{e.text[:60]}"] = {"summary": e.text, **times(e, tz)}
    return out


def _day_title(e: dict) -> tuple[str, str]:
    return (e["start"].get("date") or e["start"].get("dateTime", ""))[:10], " ".join(e.get("summary", "").lower().split())


def _same(have: dict, want: dict) -> bool:
    def key(e):
        return (e.get("summary"), (e["start"].get("dateTime") or "")[:19], e["start"].get("date"),
                (e["end"].get("dateTime") or "")[:19], e["end"].get("date"), e.get("location") or "", e.get("description") or "")
    return key(have) == key(want)


def sync(club: Club, events: list, today: dt.date | None = None, session=None) -> dict:
    """Create, update and delete the app's own events so each calendar matches. Returns counts per calendar."""
    ids = club.info.get("calendars") or {}
    today = today or dt.date.today()
    if not ids:
        return {}
    s = session or _session()
    counts = {}
    for kind, want in wanted(club, events, today).items():
        cid = ids.get(kind)
        if not cid:
            continue
        url = API.format(cid)
        have, by_hand, page = {}, set(), None
        while True:
            r = s.get(url, params={"timeMin": f"{today}T00:00:00Z",
                                   "singleEvents": "true", "maxResults": 250, **({"pageToken": page} if page else {})})
            r.raise_for_status()
            j = r.json()
            for e in j.get("items", []):
                # timeMin is UTC midnight, so last evening's events come back too: leave anything before today alone
                if (e["start"].get("date") or e["start"].get("dateTime", ""))[:10] < today.isoformat():
                    continue
                mine = e.get("extendedProperties", {}).get("private", {})
                if mine.get("w8lbz") == "1":
                    have[mine.get("w8lbz_key", e["id"])] = e
                else:  # someone added it by hand: never touched, and not added again
                    by_hand.add(_day_title(e))
            if not (page := j.get("nextPageToken")):
                break
        c = {"added": 0, "changed": 0, "removed": 0}
        for k, e in want.items():
            body = {**e, "extendedProperties": {"private": {"w8lbz": "1", "w8lbz_key": k}}}
            if k not in have and _day_title(e) in by_hand:
                continue
            if k not in have:
                s.post(url, json=body).raise_for_status()
                c["added"] += 1
            elif not _same(have[k], e):
                s.patch(f"{url}/{have[k]['id']}", json=body).raise_for_status()
                c["changed"] += 1
        for k, e in have.items():
            if k not in want:
                s.delete(f"{url}/{e['id']}").raise_for_status()
                c["removed"] += 1
        counts[kind] = c
    return counts


def latest_events(archive) -> list:
    """The Upcoming Events of the most recent meeting (a draft being taken counts: it's the newest list)."""
    return archive.meetings[0].items("Upcoming Events") if archive.meetings else []


def run(club: Club, archive) -> dict:
    """Sync now and record how it went (the Security page's Server tab shows it). Never raises."""
    if PREVIEW or not club.info.get("calendars"):
        return {}
    try:
        status = {"at": int(time.time()), "counts": sync(club, latest_events(archive))}
    except Exception as e:  # a calendar problem must never break a page or the daily run
        log.exception("calendar sync failed")
        status = {"at": int(time.time()), "error": str(e)[:300]}
    (DATA / "calendar-sync.json").write_text(json.dumps(status))
    return status


def status() -> dict | None:
    p = DATA / "calendar-sync.json"
    return json.loads(p.read_text()) if p.exists() else None


_pending = threading.Event()


def soon(club_fn, archive_fn, delay: float = 20) -> None:
    """Sync in the background a little after a change; a burst of changes (console autosave) makes one sync."""
    if _pending.is_set():
        return
    _pending.set()

    def go():
        time.sleep(delay)
        _pending.clear()
        run(club_fn(), archive_fn())
    threading.Thread(target=go, daemon=True).start()
