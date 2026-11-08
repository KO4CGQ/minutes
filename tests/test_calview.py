import datetime as dt
import json
from pathlib import Path

import pytest

from w8lbz_minutes import calview, chow, gcal
from w8lbz_minutes.club import Club
from w8lbz_minutes.meeting import Item, Meeting
from w8lbz_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
NOV = (dt.date(2026, 11, 1), dt.date(2026, 11, 30))


def club(calendars=True):
    c = Club(FIX / "test-club.yaml", roster=[
        {"id": "kd8aaa", "first": "Amy", "last": "Able", "officer": True},
        {"id": "kd8bbb", "first": "Bob", "last": "Baker", "birthday": "11-05", "share": ["birthday"]},
        {"id": "kd8ccc", "first": "Cal", "last": "Cole", "birthday": "11-20"},           # not shared
        {"id": "kd8ddd", "first": "Dee", "last": "Dunn", "birthday": "11", "share": ["birthday"]}])  # month only
    if calendars:
        c.info["calendars"] = {"events": "club@cal", "planning": "plan@cal", "community": "area@cal", "chow": "chow@cal"}
    return c


class FakeGoogle:
    def __init__(self, cals):
        self.cals, self.n = cals, 0

    def _ok(self, j=None):
        return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: j or {}})()

    def _split(self, url):
        rest = url.split("/calendars/")[1].split("/")
        return rest[0], (rest[2] if len(rest) > 2 else None)

    def get(self, url, params=None):
        cal, eid = self._split(url)
        if eid:
            return self._ok(next(e for e in self.cals[cal] if e["id"] == eid))
        return self._ok({"items": self.cals.get(cal, [])})

    def post(self, url, json):
        self.n += 1
        self.cals.setdefault(self._split(url)[0], []).append({**json, "id": f"n{self.n}"})
        return self._ok()

    def patch(self, url, json):
        cal, eid = self._split(url)
        self.cals[cal] = [{**json, "id": eid} if e["id"] == eid else e for e in self.cals[cal]]
        return self._ok()

    def delete(self, url):
        cal, eid = self._split(url)
        self.cals[cal] = [e for e in self.cals[cal] if e["id"] != eid]
        return self._ok()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(chow, "DATA", tmp_path)
    monkeypatch.setattr(calview, "DATA", tmp_path)
    calview._cache.clear()


def google():
    return FakeGoogle({
        "club@cal": [{"id": "net1", "summary": "Weekly net", "recurringEventId": "net", "start": {"dateTime": "2026-11-10T20:00:00-05:00"},
                      "end": {"dateTime": "2026-11-10T21:00:00-05:00"}, "location": "146.520"},
                     {"id": "fd", "summary": "Winter Field Day", "start": {"date": "2026-11-21"}, "end": {"date": "2026-11-23"}},
                     {"id": "x", "summary": "Called off", "status": "cancelled", "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-04"}},
                     {"id": "app1", "summary": "VE testing", "extendedProperties": {"private": {"w8lbz": "1"}},
                      "start": {"dateTime": "2026-11-14T10:00:00-05:00"}, "end": {"dateTime": "2026-11-14T13:00:00-05:00"}}],
        "plan@cal": [{"id": "am", "summary": "Annual meeting", "start": {"date": "2026-11-19"}, "end": {"date": "2026-11-20"}}],
        "area@cal": [], "chow@cal": [{"id": "never", "summary": "read from the app, not Google", "start": {"date": "2026-11-01"}, "end": {"date": "2026-11-02"}}]})


def test_page_merges_google_chow_and_birthdays(tmp_path):
    s = chow.load()
    s["nights"] = {"2026-11-12": {"member": "kd8aaa"}, "2026-11-26": {"status": "none"}}
    chow.save(s)
    c = club()
    got, note = calview.entries(c, Archive(tmp_path, c), "kd8bbb", *NOV, session=google())
    assert note is None
    seen = [(e.start.isoformat(), e.kind, e.title) for e in got]
    assert ("2026-11-12", "chow", "Chow: Amy Able") in seen and ("2026-11-19", "planning", "Annual meeting") in seen
    assert ("2026-11-05", "birthday", "Birthday: Bob Baker") in seen and ("2026-11-01", "birthday", "November birthday: Dee Dunn") in seen
    assert not any("Cal Cole" in t or "Called off" in t or "not Google" in t for _, _, t in seen)  # private, cancelled, chow from the app
    fd = next(e for e in got if e.title == "Winter Field Day")
    assert (fd.end.isoformat(), [d.day for d in fd.days(*NOV)]) == ("2026-11-22", [21, 22])
    net = next(e for e in got if e.title == "Weekly net")
    assert (net.time, net.where, net.repeats, net.id) == ("8:00 PM", "146.520", True, "net1")
    assert next(e for e in got if e.title == "VE testing").from_minutes
    officer, _ = calview.entries(c, Archive(tmp_path, c), "kd8aaa", *NOV, session=google())
    assert any(e.title == "Birthday: Cal Cole" for e in officer)  # officers see every birthday


def test_without_calendars_it_shows_the_app_own_data(tmp_path):
    c = club(calendars=False)
    Meeting(date=dt.date(2026, 11, 5), status="published",
            sections={"Upcoming Events": [Item("Hamfest", when="2026-11-14", until="2026-11-15")]}).save(tmp_path)
    got, _ = calview.entries(c, Archive(tmp_path, c), "kd8bbb", *NOV)
    assert [e.start.day for e in got if e.title == "Club meeting"] == [5, 12, 19, 26]
    assert any(e.title == "Hamfest" and e.end.day == 15 for e in got)


def test_unreachable_google_still_shows_chow_and_birthdays(tmp_path):
    class Broken:
        def get(self, *a, **k):
            raise ConnectionError("offline")
    c = club()
    got, note = calview.entries(c, Archive(tmp_path, c), "kd8bbb", *NOV, session=Broken())
    assert "Couldn't reach" in note and any(e.kind == "birthday" for e in got)


def test_leaders_add_change_move_and_delete(tmp_path):
    c, g = club(), google()
    with pytest.raises(ValueError, match="title"):
        calview.body(c, {"title": " ", "date": "2026-11-20"})
    with pytest.raises(ValueError, match="before the start"):
        calview.body(c, {"title": "Fox hunt", "date": "2026-11-20", "time": "14:00", "end_time": "13:00"})
    b = calview.save(c, "events", None, {"title": "Fox hunt", "date": "2026-11-20", "time": "14:00", "end_time": "16:00", "where": "City park"},
                     by="kd8aaa", session=g)
    assert b["start"]["dateTime"] == "2026-11-20T14:00:00" and b["end"]["dateTime"] == "2026-11-20T16:00:00" and b["location"] == "City park"
    new = next(e for e in g.cals["club@cal"] if e["summary"] == "Fox hunt")
    assert calview.form_values(new)["end_time"] == "16:00"
    calview.save(c, "community", new["id"], {"title": "Fox hunt", "date": "2026-11-21"}, by="kd8aaa", old_kind="events", session=g)
    assert not any(e["summary"] == "Fox hunt" for e in g.cals["club@cal"]) and g.cals["area@cal"][0]["start"] == {"date": "2026-11-21"}
    calview.delete(c, "planning", "am", by="kd8aaa", session=g)
    assert g.cals["plan@cal"] == []
    assert [(h["action"], h["calendar"], h["title"]) for h in calview.history()] == [
        ("deleted", "planning", "Annual meeting"), ("moved", "community", "Fox hunt"), ("added", "events", "Fox hunt")]
    # a multi-day all-day event reads back with its last day
    assert calview.form_values(next(e for e in g.cals["club@cal"] if e["id"] == "fd"))["end_date"] == "2026-11-22"


def test_calendar_pages(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    c = club()
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)
    monkeypatch.setattr(gcal, "_session", google)
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, a.new_session("kd8bbb"))
    r = t.get("/calendar?start=2026-11-01")
    assert "Weekly net" in r.text and "Add an event" not in r.text  # members see; leaders edit
    assert t.get("/calendar?view=month&month=2026-11").status_code == 200
    assert t.get("/calendar/edit").status_code == 403
    t.cookies.set(web.COOKIE, a.new_session("kd8aaa"))  # an officer
    assert "Add an event" in t.get("/calendar?start=2026-11-01").text
    assert 'value="Weekly net"' in t.get("/calendar/edit?cal=events&id=net1").text
    assert t.get("/calendar/edit?cal=events&id=app1").status_code == 409  # from the minutes: edit in the console
