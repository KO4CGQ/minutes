import datetime as dt
from pathlib import Path

import pytest

from w8lbz_minutes import calview, chow, events, gcal
from w8lbz_minutes.club import Club
from w8lbz_minutes.meeting import Item, Meeting
from w8lbz_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
NOV = (dt.date(2026, 11, 1), dt.date(2026, 11, 30))
TZ = "America/New_York"


def club():
    return Club(FIX / "test-club.yaml", roster=[
        {"id": "kd8aaa", "first": "Amy", "last": "Able", "officer": True},
        {"id": "kd8bbb", "first": "Bob", "last": "Baker", "birthday": "11-05", "share": ["birthday"]},
        {"id": "kd8ccc", "first": "Cal", "last": "Cole", "birthday": "11-20"},           # not shared
        {"id": "kd8ddd", "first": "Dee", "last": "Dunn", "birthday": "11", "share": ["birthday"]}])  # month only


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(chow, "DATA", tmp_path)
    monkeypatch.setattr(calview, "DATA", tmp_path)


def form(**kw):
    return events.from_form({"title": "Weekly net", "date": "2026-11-10", "time": "20:00", "end_time": "21:00", **kw})


def net(evs=None, **kw):
    evs = [] if evs is None else evs
    return evs, events.add(evs, "events", form(repeat="weekly", **kw), "kd8aaa")


def days(e, lo=NOV[0], hi=dt.date(2027, 3, 31)):
    return [d.isoformat() for d in events.dates(e, lo, hi)]


# ---------------- repeating ----------------

def test_repeat_rules_and_labels():
    _, e = net()
    assert days(e, *NOV) == ["2026-11-10", "2026-11-17", "2026-11-24"] and events.repeat_label(e["start"], e["repeat"]) == "Every Tuesday"
    bi = events.add([], "events", form(repeat="biweekly", repeat_until="2026-12-31"), "x")
    assert days(bi) == ["2026-11-10", "2026-11-24", "2026-12-08", "2026-12-22"]
    nth = events.add([], "events", form(repeat="monthly_weekday"), "x")
    assert days(nth)[:3] == ["2026-11-10", "2026-12-08", "2027-01-12"] and events.repeat_label(nth["start"], nth["repeat"]) == "Monthly on the 2nd Tuesday"
    last = events.add([], "events", form(date="2026-11-24", repeat="monthly_last"), "x")
    assert days(last)[:3] == ["2026-11-24", "2026-12-29", "2027-01-26"]
    on31 = events.add([], "events", events.from_form({"title": "x", "date": "2026-12-31", "repeat": "monthly"}), "x")
    assert days(on31, dt.date(2026, 12, 1), dt.date(2027, 5, 31)) == ["2026-12-31", "2027-01-31", "2027-03-31", "2027-05-31"]
    yearly = events.add([], "planning", events.from_form({"title": "Annual meeting", "date": "2026-11-19", "repeat": "yearly"}), "x")
    assert days(yearly, NOV[0], dt.date(2028, 12, 31)) == ["2026-11-19", "2027-11-19", "2028-11-19"]
    annual = events.add([], "planning", events.from_form({"title": "Annual meeting", "date": "2027-01-07", "repeat": "yearly_weekday"}), "x")
    assert days(annual, dt.date(2027, 1, 1), dt.date(2029, 12, 31)) == ["2027-01-07", "2028-01-06", "2029-01-04"]  # 1st Thursdays
    assert events.repeat_label(annual["start"], annual["repeat"]) == "Every year on the 1st Thursday of January"
    assert events.form_values(annual, None)["repeat"] == "yearly_weekday"
    ics = ("BEGIN:VEVENT\nUID:a1\nDTSTART;TZID=America/New_York:20270107T193000\nRRULE:FREQ=YEARLY;BYDAY=1TH;BYMONTH=1\n"
           "SUMMARY:Annual meeting\nEND:VEVENT\n")
    evs = []
    assert events.import_ics(evs, ics, "planning", "x", TZ, NOV[0])["added"] == ["Annual meeting"]
    assert evs[0]["repeat"]["freq"] == "yearly_weekday"
    fd = events.add([], "events", events.from_form({"title": "Field Day", "date": "2026-11-28", "end_date": "2026-11-29"}), "x")
    assert days(fd, dt.date(2026, 11, 29), dt.date(2026, 11, 30)) == ["2026-11-28"]  # still on, on its second day


def test_form_checks():
    with pytest.raises(ValueError, match="title"):
        events.from_form({"title": " ", "date": "2026-11-10"})
    with pytest.raises(ValueError, match="before the start"):
        form(end_time="19:00")
    with pytest.raises(ValueError, match="stops repeating before"):
        form(repeat="weekly", repeat_until="2026-11-01")
    with pytest.raises(ValueError, match="repeats"):
        form(repeat="daily")
    assert form(end_date="2026-11-11", end_time="")["end"] == "2026-11-11T20:00"  # a last day and no end time: same time that day
    assert events.from_form({"title": "x", "date": "2026-11-10", "end_date": "2026-11-10"})["end"] is None


def test_change_this_following_every_date():
    evs, e = net()
    events.change(evs, e["id"], "2026-11-17", "events", form(date="2026-11-17", time="20:30", end_time="21:30", title="Net (late)", repeat="weekly"), "this")
    assert events.occurrence(e, dt.date(2026, 11, 17))["start"] == "2026-11-17T20:30" and events.occurrence(e, dt.date(2026, 11, 24))["title"] == "Weekly net"
    evs, e = net()
    events.change(evs, e["id"], "2026-11-17", "events", form(date="2026-11-18", time="19:00", end_time="20:00", repeat="weekly"), "all")
    assert e["start"] == "2026-11-11T19:00" and e["end"] == "2026-11-11T20:00" and days(e, *NOV)[0] == "2026-11-11"  # moved to Wednesdays
    evs, e = net()
    events.change(evs, e["id"], "2026-11-24", "community", form(date="2026-11-24", title="Net, new host", repeat="weekly"), "following")
    old, new = evs
    assert old["repeat"]["until"] == "2026-11-23" and days(old, *NOV) == ["2026-11-10", "2026-11-17"]
    assert (new["cal"], new["title"], days(new, *NOV)) == ("community", "Net, new host", ["2026-11-24"])


def test_delete_this_following_every_date():
    evs, e = net()
    events.delete(evs, e["id"], "2026-11-17", "this")
    assert days(e, *NOV) == ["2026-11-10", "2026-11-24"]
    events.delete(evs, e["id"], "2026-11-24", "following")
    assert days(e, *NOV) == ["2026-11-10"]
    events.delete(evs, e["id"], "2026-11-10", "all")
    assert evs == []


# ---------------- what the page shows ----------------

def test_page_merges_events_minutes_chow_and_birthdays(tmp_path):
    with events.locked():
        evs, _ = net()
        events.add(evs, "planning", events.from_form({"title": "Annual meeting", "date": "2026-11-19"}), "x")
        events.add(evs, "events", events.from_form({"title": "VE testing", "date": "2026-11-14", "time": "10:00"}), "x")
        events.save(evs)
    s = chow.load()
    s["nights"] = {"2026-11-12": {"member": "kd8aaa"}, "2026-11-26": {"status": "none"}}
    chow.save(s)
    Meeting(date=dt.date(2026, 11, 5), status="published", sections={"Upcoming Events": [
        Item("VE testing", when="2026-11-14T10:00"), Item("Hamfest", when="2026-11-21", until="2026-11-22")]}).save(tmp_path)
    c = club()
    got, _ = calview.entries(c, Archive(tmp_path, c), "kd8bbb", *NOV)
    seen = [(e.start.isoformat(), e.kind, e.title) for e in got]
    assert [t for d, k, t in seen if t == "Weekly net"] == ["Weekly net"] * 3 and ("2026-11-19", "planning", "Annual meeting") in seen
    assert ("2026-11-12", "chow", "Chow: Amy Able") in seen and ("2026-11-01", "birthday", "November birthday: Dee Dunn") in seen
    assert [t for _, _, t in seen].count("VE testing") == 1  # on the calendar and in the minutes: shown once
    ham = next(e for e in got if e.title == "Hamfest")
    assert ham.from_minutes and ham.end.day == 22
    net_ = next(e for e in got if e.title == "Weekly net")
    assert (net_.time, net_.repeats, net_.occ, net_.ends.hour) == ("8:00 PM", True, "2026-11-10", 21)
    assert not any("Cal Cole" in t for _, _, t in seen)  # private stays private, even for officers
    assert any(e.title == "Birthday: Cal Cole" for e in calview.entries(c, Archive(tmp_path, c), "kd8ccc", *NOV)[0])  # your own
    c.info["public_calendars"] = ["events", "chow"]
    pub, _ = calview.entries(c, Archive(tmp_path, c), None, *NOV, public=True)
    assert {e.kind for e in pub} == {"events"}  # no chow, birthdays or Planning in public


# ---------------- importing .ics ----------------

ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:net-1@google.com\r
SUMMARY:Weekly net\\, all welcome\r
DTSTART;TZID=America/New_York:20261110T200000\r
DTEND;TZID=America/New_York:20261110T210000\r
RRULE:FREQ=WEEKLY;BYDAY=TU\r
EXDATE;TZID=America/New_York:20261124T200000\r
LOCATION:146.520 simplex\r
DESCRIPTION:Check in by callsign.\\nNet control: the duty officer\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:net-1@google.com\r
RECURRENCE-ID;TZID=America/New_York:20261117T200000\r
SUMMARY:Weekly net (Thanksgiving week\r
 , early)\r
DTSTART;TZID=America/New_York:20261117T190000\r
DTEND;TZID=America/New_York:20261117T200000\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:fd@google.com\r
SUMMARY:Winter Field Day\r
DTSTART;VALUE=DATE:20270130\r
DTEND;VALUE=DATE:20270201\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:hunt@google.com\r
SUMMARY:Fox hunt\r
DTSTART:20261121T190000Z\r
DURATION:PT2H\r
RRULE:FREQ=MONTHLY;BYDAY=3SA;COUNT=3\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:daily@google.com\r
SUMMARY:Daily check\r
DTSTART:20261101T120000Z\r
RRULE:FREQ=DAILY\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:twice@google.com\r
SUMMARY:Tue and Thu net\r
DTSTART:20261103T010000Z\r
RRULE:FREQ=WEEKLY;BYDAY=TU,TH\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:gone@google.com\r
SUMMARY:Called off\r
STATUS:CANCELLED\r
DTSTART;VALUE=DATE:20261128\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:old@google.com\r
SUMMARY:Long ago\r
DTSTART;VALUE=DATE:20200101\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_import_ics():
    evs = []
    r = events.import_ics(evs, ICS, "events", "kd8aaa", TZ, dt.date(2025, 11, 8))
    assert r["added"] == ["Weekly net, all welcome", "Winter Field Day", "Fox hunt", "Tue and Thu net", "Tue and Thu net"]
    assert [t for t, _ in r["skipped"]] == ["Daily check"]
    tt = [e for e in evs if e["title"] == "Tue and Thu net"]  # one weekly event per day
    assert [(e["start"], events.repeat_label(e["start"], e["repeat"])) for e in tt] == [
        ("2026-11-03T20:00", "Every Tuesday"), ("2026-11-05T20:00", "Every Thursday")]
    n = next(e for e in evs if e["uid"] == "net-1@google.com")
    assert (n["start"], n["end"], n["where"], n["notes"]) == ("2026-11-10T20:00", "2026-11-10T21:00", "146.520 simplex",
                                                            "Check in by callsign.\nNet control: the duty officer")
    assert days(n, *NOV) == ["2026-11-10", "2026-11-17"]  # the 24th is taken out
    moved = events.occurrence(n, dt.date(2026, 11, 17))
    assert (moved["start"], moved["title"]) == ("2026-11-17T19:00", "Weekly net (Thanksgiving week, early)")
    fd = next(e for e in evs if e["title"] == "Winter Field Day")
    assert (fd["start"], fd["end"]) == ("2027-01-30", "2027-01-31")  # two days: the .ics end is the day after
    hunt = next(e for e in evs if e["title"] == "Fox hunt")
    assert hunt["start"] == "2026-11-21T14:00" and hunt["end"] == "2026-11-21T16:00"  # UTC into club time
    assert days(hunt) == ["2026-11-21", "2026-12-19", "2027-01-16"]  # third Saturdays, 3 times
    # again: updated, not doubled; an event typed in by hand with the same title and day is left alone
    evs2 = [*evs]
    r2 = events.import_ics(evs2, ICS, "events", "kd8aaa", TZ, dt.date(2025, 11, 8))
    assert len(evs2) == 5 and len(r2["updated"]) == 5 and r2["added"] == []
    hand = [{"id": "h", "cal": "events", "title": "winter field day", "start": "2027-01-30", "end": None, "repeat": None}]
    r3 = events.import_ics(hand, ICS, "events", "kd8aaa", TZ, dt.date(2025, 11, 8))
    assert r3["already"] == ["Winter Field Day"]


# ---------------- the pages ----------------

def signed_in(tmp_path, monkeypatch, who):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    c = club()
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)
    monkeypatch.setattr(gcal, "soon", lambda *a, **k: None)
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, a.new_session(who))
    return t, c, a


def test_import_turns_html_descriptions_into_text():
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:h1\r\nDTSTART;VALUE=DATE:20300105\r\nSUMMARY:Exam session\r\n"
           'DESCRIPTION:<p dir="auto">Fee <strong>$15</strong> &amp\\; walk-ins.</p><a href="https://example.org">https://example.org</a>\r\n'
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    evs = []
    events.import_ics(evs, ics, "events", "KD8AAA", "America/New_York", dt.date(2029, 1, 1))
    assert evs[0]["notes"] == "Fee $15 & walk-ins.\nhttps://example.org"


def test_leaders_add_change_delete_and_import_on_the_page(tmp_path, monkeypatch):
    t, c, a = signed_in(tmp_path, monkeypatch, "kd8bbb")
    assert "Add an event" not in t.get("/calendar").text and t.get("/calendar/edit").status_code == 403  # members see; leaders edit
    t.cookies.set(web_cookie(), a.new_session("kd8aaa"))  # an officer
    r = t.post("/calendar/save", data={"cal": "events", "title": "Fox hunt", "date": "2026-11-21", "time": "14:00", "end_time": "13:00"})
    assert r.status_code == 400 and "before the start" in r.text and 'value="Fox hunt"' in r.text  # what was typed stays
    r = t.post("/calendar/save", data={"cal": "events", "title": "Weekly net", "date": "2026-11-10", "time": "20:00",
                                       "repeat": "weekly"}, follow_redirects=False)
    assert r.headers["location"] == "/calendar?start=2026-11-10"
    eid = events.load()[0]["id"]
    page = t.get("/calendar?start=2026-11-01").text
    assert f"/calendar/edit?id={eid}&d=2026-11-17" in page and "aria-label=\"Edit Weekly net\"" in page and "· repeats" not in page
    assert "Every Tuesday" in t.get(f"/calendar/edit?id={eid}&d=2026-11-17").text
    assert 'class="calkind" data-kind="planning"' in page and '<li data-kind="events">' in page  # the legend shows or hides each
    t.post("/calendar/delete", data={"id": eid, "d": "2026-11-17", "scope": "this"})
    assert "2026-11-17" in events.load()[0]["skip"]
    up = t.post("/calendar/import", data={"cal": "community", "since": "2025-01-01"}, files={"file": ("club.ics", ICS.encode(), "text/calendar")})
    assert "Added 5" in up.text and "Daily check" in up.text
    assert t.post("/calendar/import", data={"cal": "events"}, files={"file": ("x.ics", b"hello", "text/plain")}).text.count("an .ics calendar file") == 1
    assert [h["action"] for h in events.history()][:2] == ["imported 5 new and 0 updated events into", "deleted one date of"]


def web_cookie():
    from w8lbz_minutes import web
    return web.COOKIE


def test_feeds_and_public_calendar(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    today = dt.date.today()
    s = chow.load()
    s["nights"] = {(today + dt.timedelta(days=2)).isoformat(): {"member": "kd8aaa"}}
    chow.save(s)
    with events.locked():
        evs = []
        events.add(evs, "events", events.from_form({"title": "Fox hunt; bring a radio, and snacks", "where": "City park",
                                                    "date": (today + dt.timedelta(days=3)).isoformat(), "time": "14:00", "end_time": "16:00"}), "x")
        events.add(evs, "planning", events.from_form({"title": "Board dinner", "date": (today + dt.timedelta(days=4)).isoformat(),
                                                      "end_date": (today + dt.timedelta(days=5)).isoformat()}), "x")
        events.save(evs)
    m, c, a = signed_in(tmp_path, monkeypatch, "kd8bbb")
    anon = TestClient(web.app)
    assert anon.get("/calendar/public", follow_redirects=False).status_code == 404  # nothing public yet
    tok = calview.feed_token("kd8bbb")
    body = anon.get(f"/calendar/feed/{tok}.ics").text
    assert "SUMMARY:Fox hunt\; bring a radio\\, and snacks" in body and "LOCATION:City park" in body
    assert "DTSTART;TZID=America/New_York:" in body and "SUMMARY:Chow: Amy Able" in body
    assert f"DTEND;VALUE=DATE:{(today + dt.timedelta(days=6)):%Y%m%d}" in body  # Board dinner: two days, end is the day after
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    only = anon.get(f"/calendar/feed/{tok}.ics?show=chow").text
    assert "Chow: Amy Able" in only and "Fox hunt" not in only and "X-WR-CALNAME:W0TST Chow" in only
    assert anon.get("/calendar/feed/wrong.ics").status_code == 404
    assert calview.feed_token("kd8bbb", reset=True) != tok and anon.get(f"/calendar/feed/{tok}.ics").status_code == 404
    c.info["public_calendars"] = ["events", "chow"]  # chow is never public, even if listed
    page = anon.get("/calendar/public")
    assert page.status_code == 200 and "Fox hunt" in page.text and "Chow" not in page.text and "Board dinner" not in page.text
    feed = anon.get("/calendar/public.ics").text
    assert "Fox hunt" in feed and "Chow" not in feed and "Birthday" not in feed and "Board dinner" not in feed
    assert anon.get("/calendar", follow_redirects=False).status_code in (302, 303, 307)  # the members' page needs sign-in
    home = m.get("/").text  # Home's "Coming up" is the calendar's next two weeks, chow left to its own card
    coming = home[home.index("Coming up"):]
    assert "Fox hunt" in coming and "Board dinner" in coming and "Chow: Amy Able" not in coming
    (tmp_path / "meetings").mkdir()
    Meeting(date=today - dt.timedelta(days=3), status="published",
            sections={"Upcoming Events": [Item("Winter Field Day coming in Jan")]}).save(tmp_path / "meetings")
    monkeypatch.setattr(web, "archive", Archive(tmp_path / "meetings", c))
    coming = (h := m.get("/").text)[h.index("Coming up"):]
    assert "From the last minutes" in coming and "Winter Field Day coming in Jan" in coming  # undated, as written
    sub = m.get("/calendar/subscribe").text
    assert calview.feed_token("kd8bbb") in sub and "/calendar/public.ics" in sub


def test_the_weekly_meeting_is_on_the_calendar_from_the_start(tmp_path):
    (events.DATA / "events-meeting-added").unlink()
    c = club()
    c.info.update(meeting_day="Thursday", meeting_time="19:30")
    today = dt.date.today()
    got, _ = calview.entries(c, Archive(tmp_path, c), "kd8bbb", today, today + dt.timedelta(days=20))
    meets = [e for e in got if e.title == "W0TST meeting"]
    assert len(meets) == 3 and all(e.start.weekday() == 3 and e.time == "7:30 PM" for e in meets) and meets[0].repeats
    with events.locked():  # a leader deletes it: it isn't put back
        evs = events.load()
        events.delete(evs, evs[0]["id"], None)
        events.save(evs)
    assert not any(e.title == "W0TST meeting" for e in calview.entries(c, Archive(tmp_path, c), "kd8bbb", today, today + dt.timedelta(days=20))[0])
