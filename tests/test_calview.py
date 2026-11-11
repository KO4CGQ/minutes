import datetime as dt
from pathlib import Path

import pytest

from club_minutes import calview, chow, events
from club_minutes.club import Club
from club_minutes.meeting import Item, Meeting
from club_minutes.store import Archive

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
        form(repeat="hourly")
    assert form(end_date="2026-11-11", end_time="")["end"] == "2026-11-11T20:00"  # a last day and no end time: same time that day
    assert events.from_form({"title": "x", "date": "2026-11-10", "end_date": "2026-11-10"})["end"] is None


def test_change_this_following_every_date():
    evs, e = net()
    events.change(evs, e["id"], "2026-11-17", "events", form(date="2026-11-17", time="20:30", end_time="21:30", title="Net (late)", repeat="weekly"), "this")
    assert events.occurrence(e, dt.date(2026, 11, 17))["start"] == "2026-11-17T20:30"
    moved = {**e, "changes": {"2026-11-17": {**e["changes"]["2026-11-17"], "start": "2026-11-27T20:00", "end": "2026-11-27T21:00"}}}
    with events.locked():  # one date moved to another day shows there, even in a range without its original date
        events.save([moved])
    c = club()
    got = calview.entries(c, Archive(events.DATA, c), "kd8bbb", dt.date(2026, 11, 20), dt.date(2026, 11, 30))[0]
    assert sorted(x.start.isoformat() for x in got if x.id == e["id"]) == ["2026-11-24", "2026-11-27"] and events.occurrence(e, dt.date(2026, 11, 24))["title"] == "Weekly net"
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
SUMMARY:Hourly check\r
DTSTART:20261101T120000Z\r
RRULE:FREQ=HOURLY\r
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
    assert [t for t, _ in r["skipped"]] == ["Hourly check"]
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
    from club_minutes import web
    from club_minutes.auth import Auth
    c = club()
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)
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
    assert f"/calendar/edit?id={eid}&d=2026-11-17" in page and "· repeats" not in page
    assert 'class="calopen" data-ev=' in page and "Every Tuesday" in page  # rows open a dialog: when, repeats, Edit
    assert "Every Tuesday" in t.get(f"/calendar/edit?id={eid}&d=2026-11-17").text
    assert 'class="calkind" data-kind="planning"' in page and '<li data-kind="events">' in page  # the legend shows or hides each
    t.post("/calendar/delete", data={"id": eid, "d": "2026-11-17", "scope": "this"})
    assert "2026-11-17" in events.load()[0]["skip"]
    up = t.post("/calendar/import", data={"cal": "community", "since": "2025-01-01"}, files={"file": ("club.ics", ICS.encode(), "text/calendar")})
    assert "Added 5" in up.text and "Hourly check" in up.text
    assert t.post("/calendar/import", data={"cal": "events"}, files={"file": ("x.ics", b"hello", "text/plain")}).text.count("an .ics calendar file") == 1
    assert [h["action"] for h in events.history()][:2] == ["imported 5 new and 0 updated events into", "deleted one date of"]


def web_cookie():
    from club_minutes import web
    return web.COOKIE


def test_feeds_and_public_calendar(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import web
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


def test_an_event_in_another_time_zone_shows_in_club_time(tmp_path):
    # Field Day: the fourth full weekend of June (always the 4th Saturday), 1800 UTC Saturday to 2059 UTC Sunday
    v = events.from_form({"title": "Field Day", "date": "2026-06-27", "time": "18:00", "end_date": "2026-06-28", "end_time": "20:59",
                          "repeat": "yearly_weekday", "tz": "UTC"}, TZ)
    assert v["tz"] == "UTC" and events.from_form({"title": "x", "date": "2026-06-27", "time": "18:00", "tz": TZ}, TZ)["tz"] is None
    assert events.from_form({"title": "x", "date": "2026-06-27", "tz": "UTC"}, TZ)["tz"] is None  # all day: no zone
    with pytest.raises(ValueError, match="time zone"):
        events.from_form({"title": "x", "date": "2026-06-27", "time": "18:00", "tz": "Mars/Olympus"}, TZ)
    with events.locked():
        evs = []
        e = events.add(evs, "events", v, "x")
        events.save(evs)
    assert events.form_values(e, None)["tz"] == "UTC"
    c = club()
    got, _ = calview.entries(c, Archive(tmp_path, c), "kd8bbb", dt.date(2027, 6, 1), dt.date(2027, 6, 30))
    fd = next(x for x in got if x.title == "Field Day")
    assert (fd.starts, fd.ends) == (dt.datetime(2027, 6, 26, 14, 0), dt.datetime(2027, 6, 27, 16, 59)) and fd.time == "2:00 PM"
    assert fd.zone == "Sat Jun 26 18:00 – Sun Jun 27 20:59 UTC"
    evs = events.load()  # one date of a series keeps its time zone and calendar: the form says so
    one = {**v, "start": "2027-06-26T13:00", "end": "2027-06-27T16:59", "tz": None}
    with pytest.raises(ValueError, match="time zone of a single date"):
        events.change(evs, evs[0]["id"], "2027-06-26", "events", one, "this")
    with pytest.raises(ValueError, match="another calendar"):
        events.change(evs, evs[0]["id"], "2027-06-26", "planning", v, "this")
    events.change(evs, evs[0]["id"], "2027-06-26", "planning", v, "all")
    assert evs[0]["cal"] == "planning"
    # late UTC on the last day of the range is still that day in club time
    late = events.add([], "events", events.from_form({"title": "Late", "date": "2027-07-01", "time": "01:00", "tz": "UTC"}, TZ), "x")
    with events.locked():
        events.save([late])
    assert [x.start for x in calview.entries(c, Archive(tmp_path, c), "kd8bbb", dt.date(2027, 6, 1), dt.date(2027, 6, 30))[0]
            if x.title == "Late"] == [dt.date(2027, 6, 30)]


def test_search_finds_calendar_events_and_the_agenda_can_show_more_weeks(tmp_path, monkeypatch):
    from club_minutes import web
    today = dt.date.today()
    with events.locked():
        evs = []
        events.add(evs, "events", events.from_form({"title": "Fox hunt", "where": "City park", "date": (today + dt.timedelta(days=80)).isoformat()}), "x")
        events.save(evs)
    (tmp_path / "meetings").mkdir()
    monkeypatch.setattr(web, "archive", Archive(tmp_path / "meetings", club()))
    m, *_ = signed_in(tmp_path, monkeypatch, "kd8bbb")
    found = m.get("/search?q=city+PARK").text
    assert "Fox hunt" in found and f"/calendar?start={today + dt.timedelta(days=80)}" in found
    assert "Fox hunt" not in m.get("/search?q=park+bench").text
    assert "Fox hunt" not in m.get("/calendar").text and "Fox hunt" in m.get("/calendar?weeks=13").text
    assert "Fox hunt" not in m.get("/calendar?weeks=999").text  # not a choice: 8 weeks
    assert f"start={today + dt.timedelta(days=91)}&amp;weeks=13" in m.get("/calendar?weeks=13").text  # Later keeps the length


def test_imported_minutes_offer_the_original_and_new_ones_the_pdf(tmp_path, monkeypatch):
    from club_minutes import web
    root = tmp_path / "meetings"
    old = Meeting(date=dt.date(2025, 3, 6), status="published", source="6-mar-25-minutes.pdf")
    fixed = Meeting(date=dt.date(2025, 3, 13), status="published", source="13-mar-25-minutes-CORRECTED.txt")  # no original PDF
    new = Meeting(date=dt.date(2026, 11, 5), status="published")
    old.save(root), new.save(root), fixed.save(root)
    (root / old.slug / "original.pdf").write_bytes(b"%PDF")
    (root / new.slug / "original.pdf").write_bytes(b"%PDF")  # a stray file doesn't make it "imported"
    m, c, _ = signed_in(tmp_path, monkeypatch, "kd8bbb")
    monkeypatch.setattr(web, "archive", Archive(root, c))
    o, n = m.get(f"/meetings/{old.slug}").text, m.get(f"/meetings/{new.slug}").text
    assert "View original" in o and "Download PDF" not in o
    assert "View original" not in n and "Download PDF" in n
    assert "Download PDF" in m.get(f"/meetings/{fixed.slug}").text  # imported, but no original to show: the PDF instead


def test_odd_edits_found_by_poking_at_the_calendar():
    def series():
        evs, e = net()
        return evs, e
    evs, e = series()  # a series made a one-off: an old change to its first date doesn't hide the new title
    events.change(evs, e["id"], "2026-11-10", "events", form(title="Special", repeat="weekly"), "this")
    events.change(evs, e["id"], "2026-11-17", "events", form(date="2026-11-17", title="Plain"), "all")
    assert [events.occurrence(e, d)["title"] for d in events.dates(e, *NOV)] == ["Plain"]
    evs, e = series()  # Repeats changed for one date only: say so instead of ignoring it
    with pytest.raises(ValueError, match="how it repeats"):
        events.change(evs, e["id"], "2026-11-17", "events", form(date="2026-11-17", repeat="monthly"), "this")
    evs, e = series()  # a new time for every date keeps deleted dates deleted and one-date changes
    events.delete(evs, e["id"], "2026-12-01", "this")
    events.change(evs, e["id"], "2026-11-24", "events", form(date="2026-11-24", title="Thanksgiving net", repeat="weekly"), "this")
    events.change(evs, e["id"], "2026-11-17", "events", form(date="2026-11-17", time="19:00", end_time="20:00", repeat="weekly"), "all")
    assert "2026-12-01" in e["skip"] and e["changes"]["2026-11-24"]["title"] == "Thanksgiving net" and e["start"] == "2026-11-10T19:00"
    events.change(evs, e["id"], "2026-11-17", "events", form(date="2026-11-18", repeat="weekly"), "all")  # a new day: they go
    assert e["skip"] == [] and e["changes"] == {}


def test_odd_addresses_are_not_found_rather_than_errors(tmp_path, monkeypatch):
    m, *_ = signed_in(tmp_path, monkeypatch, "kd8aaa")
    with events.locked():
        evs, e = net()
        events.save(evs)
    for url in ("/calendar?start=0001-01-01", "/calendar?start=9999-12-31&weeks=52", "/calendar?view=month&month=0001-01",
                f"/calendar/edit?id={e['id']}&d=garbage"):
        assert m.get(url).status_code == 404, url
    assert m.get("/calendar?weeks=abc").status_code == 200


def test_daily_and_several_weeks_of_the_month():
    daily = events.add([], "events", events.from_form({"title": "Field Day prep", "date": "2026-11-10", "repeat": "daily", "repeat_until": "2026-11-13"}), "x")
    assert days(daily) == ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"] and events.repeat_label(daily["start"], daily["repeat"]) == "Every day, until Nov 13, 2026"
    f = {"title": "Net", "date": "2026-11-03", "time": "20:00", "repeat": "monthly_weeks", "week1": "1", "week3": "1"}
    net13 = events.add([], "events", events.from_form(f), "x")  # Nov 3 is the 1st Tuesday
    assert days(net13, NOV[0], dt.date(2027, 1, 31)) == ["2026-11-03", "2026-11-17", "2026-12-01", "2026-12-15", "2027-01-05", "2027-01-19"]
    assert events.repeat_label(net13["start"], net13["repeat"]) == "Monthly on the 1st and 3rd Tuesday"
    assert {k: events.form_values(net13, None)[k] for k in ("repeat", "week1", "week2", "week3", "week_last")} == {"repeat": "monthly_weeks", "week1": True, "week2": False, "week3": True, "week_last": False}
    last = events.add([], "events", events.from_form({**f, "week3": "", "week_last": "1"}), "x")
    assert days(last, NOV[0], dt.date(2026, 12, 31)) == ["2026-11-03", "2026-11-24", "2026-12-01", "2026-12-29"]
    with pytest.raises(ValueError, match="tick that week too"):
        events.from_form({**f, "week1": ""})  # starts on a 1st Tuesday that isn't ticked
    with pytest.raises(ValueError, match="Tick the weeks"):
        events.from_form({**f, "week1": "", "week3": ""})
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:d1\r\nDTSTART;VALUE=DATE:20300105\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Exams\r\nEND:VEVENT\r\n"
           "BEGIN:VEVENT\r\nUID:m1\r\nDTSTART:20300101T200000\r\nRRULE:FREQ=MONTHLY;BYDAY=1TU,-1TU\r\nSUMMARY:Net\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    evs = []
    report = events.import_ics(evs, ics, "events", "x", TZ, dt.date(2030, 1, 1))
    assert report["added"] == ["Exams", "Net"] and not report["skipped"]
    assert days(evs[0], dt.date(2030, 1, 1), dt.date(2030, 1, 31)) == ["2030-01-05", "2030-01-06", "2030-01-07"]
    assert days(evs[1], dt.date(2030, 1, 1), dt.date(2030, 2, 28)) == ["2030-01-01", "2030-01-29", "2030-02-05", "2030-02-26"]


def test_holidays_due_dates_editors_and_the_change_log(tmp_path, monkeypatch):
    from club_minutes import business, web
    c = club()
    got, _ = calview.entries(c, Archive(tmp_path, c), "kd8bbb", *NOV)
    hol = [(e.start.isoformat(), e.title) for e in got if e.kind == "holiday"]
    assert hol == [("2026-11-11", "Veterans Day"), ("2026-11-26", "Thanksgiving")]
    assert [e.start for e in calview.holidays(dt.date(2027, 1, 1), dt.date(2027, 12, 31)) if e.title in ("Memorial Day", "Labor Day")] == \
        [dt.date(2027, 5, 31), dt.date(2027, 9, 6)]
    assert not any(e.kind == "holiday" for e in calview.entries(c, Archive(tmp_path, c), None, *NOV, public=True)[0])
    c.info["public_calendars"] = ["holiday"]
    assert any(e.kind == "holiday" for e in calview.entries(c, Archive(tmp_path, c), None, *NOV, public=True)[0])
    s = business.load()
    business.add(s, "Order the Field Day banner", "kd8aaa", owner="kd8bbb", due="2026-11-20")
    business.add(s, "No date on this one", "kd8aaa")
    business.save(s)
    due = [e for e in calview.entries(c, Archive(tmp_path, c), "kd8bbb", *NOV)[0] if e.title.startswith("Due:")]
    assert [(e.start.isoformat(), e.kind, e.title) for e in due] == [("2026-11-20", "planning", "Due: Order the Field Day banner")]
    assert "Bob Baker" in due[0].notes
    # calendar editors: a member ticked calendar_editor may change events; others may not
    m, c2, _ = signed_in(tmp_path, monkeypatch, "kd8bbb")
    assert m.get("/calendar/edit").status_code == 403
    c2.members["kd8bbb"].calendar_editor = True
    assert m.get("/calendar/edit").status_code == 200 and "+ Add an event" in m.get("/calendar").text
    m.post("/calendar/save", data={"cal": "events", "title": "Fox hunt", "date": "2026-11-21"})
    assert m.get("/calendar/changes").status_code == 403  # the log is for officers
    o, *_ = signed_in(tmp_path, monkeypatch, "kd8aaa")
    log = o.get("/calendar/changes").text
    assert "Fox hunt" in log and "Bob Baker" in log and "Who changed what" in o.get("/calendar").text


def test_ohio_election_days_on_community():
    got = [(e.start.isoformat(), e.title) for e in calview.elections(dt.date(2024, 1, 1), dt.date(2026, 12, 31))]
    assert got == [("2024-03-19", "Ohio primary election"), ("2024-11-05", "Election Day (Ohio general election)"),  # a presidential year: March
                   ("2025-05-06", "Ohio primary election"), ("2025-11-04", "Election Day (Ohio general election)"),
                   ("2026-05-05", "Ohio primary election"), ("2026-11-03", "Election Day (Ohio general election)")]
    c = club()
    nov = calview.entries(c, Archive(events.DATA, c), "kd8bbb", *NOV)[0]
    assert [(e.kind, e.start.day) for e in nov if e.title.startswith("Election Day")] == [("community", 3)]


def test_console_reads_out_the_calendar_until_the_next_meeting(tmp_path):
    from club_minutes import console
    with events.locked():
        evs, meet = net(title="W0TST meeting", date="2026-11-12", time="19:30", end_time="21:30")
        meet["meeting"] = True  # the weekly meeting, as ensure_meeting makes it: left out unless unticked
        del meet["quiet"]
        events.add(evs, "events", form(title="Fox hunt", date="2026-11-14", time="14:00", end_time="16:00", where="the park"), "x")
        events.add(evs, "events", form(title="Board dinner", date="2026-11-15", quiet="1"), "x")
        events.add(evs, "planning", events.from_form({"title": "Budget due", "date": "2026-11-16"}), "x")
        events.add(evs, "community", events.from_form({"title": "Hamfest", "date": "2026-12-05", "end_date": "2026-12-06"}), "x")
        events.save(evs)
    Meeting(date=dt.date(2026, 11, 12), status="draft", sections={"Upcoming Events": [Item("Swap meet", when="2026-11-18")]}).save(tmp_path)
    c = club()
    got = console.calendar_ahead(Archive(tmp_path, c), dt.date(2026, 11, 12))
    titles = [x["title"] for x in got["calendar"]]
    assert got["next_meeting"] == "2026-11-19"
    assert {"Fox hunt", "Thanksgiving", "Hamfest", "Budget due"} <= set(titles)
    assert not {"W0TST meeting", "Board dinner", "Swap meet", "Birthday: Cal Cole"} & set(titles)  # meeting, ticked, already in the minutes, private
    assert next(x for x in got["calendar"] if x["title"] == "Budget due")["cal"] == "planning"
    fox = next(x for x in got["calendar"] if x["title"] == "Fox hunt")
    assert (fox["when"], fox["until"], fox["where"], fox["kind"]) == ("2026-11-14T14:00", "2026-11-14T16:00", "the park", "Club events")
    assert next(x for x in got["calendar"] if x["title"] == "Hamfest")["until"] == "2026-12-06"
    # the tick is on the event's form; the meeting shows it ticked, and unticking puts it back in the list
    assert events.form_values(meet, None)["quiet"] and not events.form_values(evs[1], None)["quiet"]
    with events.locked():
        evs = events.load()
        events.change(evs, meet["id"], None, "events", events.from_form({**events.form_values(meet, None), "quiet": ""}))
        events.save(evs)
    assert "W0TST meeting" in [x["title"] for x in console.calendar_ahead(Archive(tmp_path, c), dt.date(2026, 11, 12))["calendar"]]


def test_upcoming_events_go_on_their_calendar_once(tmp_path):
    with events.locked():
        evs = []
        events.add(evs, "events", form(title="Fox hunt", date="2026-11-14", time="14:00", end_time="16:00"), "x")
        events.save(evs)
    m = Meeting(date=dt.date(2026, 11, 12), status="draft", sections={"Upcoming Events": [
        Item("Fox hunt, the park", when="2026-11-14T14:00", until="2026-11-14T16:00"),  # added from the console's list
        Item("Thanksgiving", when="2026-11-26"),                                           # a holiday, added the same way
        Item("Budget meeting", when="2026-11-18T19:00", cal="planning"),
        Item("Road race", when="2026-11-21", cal="community")]})
    assert Meeting.from_markdown(m.to_markdown()).items("Upcoming Events")[2].cal == "planning"  # kept in the file
    m.save(tmp_path)
    c = club()
    c.info["public_calendars"] = ["events"]
    got, _ = calview.entries(c, Archive(tmp_path, c), "kd8bbb", *NOV)
    seen = [(e.kind, e.title) for e in got]
    assert [t for _, t in seen if t.startswith("Fox hunt")] == ["Fox hunt"] and [t for _, t in seen].count("Thanksgiving") == 1
    assert ("planning", "Budget meeting") in seen and ("community", "Road race") in seen
    pub, _ = calview.entries(c, Archive(tmp_path, c), None, *NOV, public=True)
    assert not {"Budget meeting", "Road race"} & {e.title for e in pub}  # Planning and Community aren't public here


def test_look_alike_events_the_same_day_are_flagged_not_hidden(tmp_path):
    assert calview.similar("VE testing", "VE Testing session at the library") and not calview.similar("VE testing", "Fox hunt")
    with events.locked():
        evs = []
        events.add(evs, "events", form(title="Fox hunt at Osborn Park", date="2026-11-14", time="14:00"), "x")
        events.add(evs, "community", events.from_form({"title": "Osborn park fox hunt", "date": "2026-11-14"}), "x")
        events.add(evs, "events", form(title="Fox hunt", date="2026-11-21"), "x")  # another day: not flagged
        events.save(evs)
    c = club()
    got, _ = calview.entries(c, Archive(tmp_path, c), "kd8bbb", *NOV)
    flagged = {(e.start.day, e.title, e.dup) for e in got if e.dup}
    assert flagged == {(14, "Fox hunt at Osborn Park", "Osborn park fox hunt"), (14, "Osborn park fox hunt", "Fox hunt at Osborn Park")}
    assert len([e for e in got if "fox hunt" in e.title.lower()]) == 3  # nothing hidden
