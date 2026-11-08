import datetime as dt
from pathlib import Path

from w8lbz_minutes import chow, console, gcal
from w8lbz_minutes.club import Club
from w8lbz_minutes.meeting import Item, Meeting
from w8lbz_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
THU = dt.date(2026, 11, 12)


def club():
    c = Club(FIX / "test-club.yaml", roster=[{"id": "kd8aaa", "first": "Amy", "last": "Able"}])
    c.info["calendars"] = {"chow": "chow@cal", "events": "events@cal"}
    return c


class FakeCal:
    """Just enough of the Calendar API: list (only the app's events), insert, patch, delete."""
    def __init__(self, items=None):
        self.items, self.calls, self.n = {c: list(v) for c, v in (items or {}).items()}, [], 0

    def _cal(self, url):
        return url.split("/calendars/")[1].split("/")[0]

    def _ok(self, j=None):
        return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: j or {}})()

    def get(self, url, params):
        mine = [e for e in self.items.get(self._cal(url), []) if e.get("extendedProperties", {}).get("private", {}).get("w8lbz") == "1"]
        return self._ok({"items": mine})

    def post(self, url, json):
        self.n += 1
        self.items.setdefault(self._cal(url), []).append({**json, "id": f"e{self.n}"})
        self.calls.append(("add", json["summary"]))
        return self._ok()

    def patch(self, url, json):
        eid = url.rsplit("/", 1)[1]
        cal = self.items[self._cal(url)]
        cal[:] = [{**json, "id": eid} if e["id"] == eid else e for e in cal]
        self.calls.append(("change", json["summary"]))
        return self._ok()

    def delete(self, url):
        eid = url.rsplit("/", 1)[1]
        cal = self.items[self._cal(url)]
        self.calls.append(("remove", next(e["summary"] for e in cal if e["id"] == eid)))
        cal[:] = [e for e in cal if e["id"] != eid]
        return self._ok()


def test_event_dates_round_trip_and_show():
    m = Meeting(date=THU, sections={"Upcoming Events": [Item("VE testing at the clubhouse", when="2026-11-21T10:00"),
                                                        Item("Hamfest", when="2026-12-05"), Item("Field Day, date TBD")]})
    back = Meeting.from_markdown(m.to_markdown()).items("Upcoming Events")
    assert [(i.text, i.when) for i in back] == [("VE testing at the clubhouse", "2026-11-21T10:00"), ("Hamfest", "2026-12-05"),
                                                ("Field Day, date TBD", None)]
    assert [i.shown for i in back] == ["Sat, Nov 21, 10:00 AM: VE testing at the clubhouse", "Sat, Dec 5: Hamfest", "Field Day, date TBD"]


def test_past_events_drop_off_and_bad_dates_are_ignored(tmp_path):
    Meeting(date=dt.date(2026, 11, 5), status="published",
            sections={"Upcoming Events": [Item("Last week's net", when="2026-11-07"), Item("Hamfest", when="2026-12-05"),
                                          Item("Field Day")]}).save(tmp_path)
    a = Archive(tmp_path, club())
    m = console.start(a, THU, None)
    assert [i.text for i in m.items("Upcoming Events")] == ["Hamfest", "Field Day"]
    data = {"meeting": {"sections": {"Upcoming Events": [{"text": "A", "when": "2026-12-01T09:30"}, {"text": "B", "when": "soon"},
                                                         {"text": "C", "when": "2026-13-01"}]}}}
    assert [i.when for i in console.apply(a, THU.isoformat(), data).items("Upcoming Events")] == ["2026-12-01T09:30", None, None]


def test_sync_adds_changes_and_removes_only_its_own_events(tmp_path, monkeypatch):
    monkeypatch.setattr(chow, "DATA", tmp_path)
    s = chow.load()
    s["nights"] = {"2026-11-05": {"member": "kd8aaa"}, THU.isoformat(): {"member": "kd8aaa"},
                   "2026-11-19": {"member": None}, "2026-11-26": {"status": "none"}}
    chow.save(s)
    c = club()
    cal = FakeCal({"events@cal": [{"id": "x1", "summary": "Club picnic (added by hand)", "start": {"date": "2026-11-20"}, "end": {"date": "2026-11-21"}}]})
    events = [Item("VE testing", when="2026-11-21T10:00"), Item("Hamfest", when="2026-12-05"), Item("Field Day")]
    assert gcal.sync(c, events, THU, cal) == {"chow": {"added": 2, "changed": 0, "removed": 0},
                                             "events": {"added": 2, "changed": 0, "removed": 0}}
    chow_ev = {e["summary"]: e for e in cal.items["chow@cal"]}
    assert set(chow_ev) == {"Chow: Amy Able", "Chow: open, nobody yet"}  # last week's and the no-chow night left out
    assert chow_ev["Chow: Amy Able"]["start"] == {"dateTime": "2026-11-12T19:30:00", "timeZone": "America/New_York"}
    hamfest = next(e for e in cal.items["events@cal"] if e["summary"] == "Hamfest")
    assert hamfest["start"] == {"date": "2026-12-05"} and hamfest["end"] == {"date": "2026-12-06"}
    # nothing changed: nothing to do
    cal.calls = []
    assert gcal.sync(c, events, THU, cal)["events"] == {"added": 0, "changed": 0, "removed": 0} and cal.calls == []
    # a swap fills the open night; Hamfest is removed from the minutes; the hand-made event is never touched
    s["nights"]["2026-11-19"]["member"] = "kd8aaa"
    chow.save(s)
    gcal.sync(c, events[:1], THU, cal)
    assert sorted(cal.calls) == [("change", "Chow: Amy Able"), ("remove", "Hamfest")]
    assert any(e["id"] == "x1" for e in cal.items["events@cal"])
    # last night's chow (returned by the UTC-midnight timeMin) is history: never removed
    cal.items["chow@cal"].append({"id": "old", "summary": "Chow: Amy Able", "start": {"dateTime": "2026-11-11T19:30:00-05:00"},
                                  "end": {"dateTime": "2026-11-11T21:30:00-05:00"}, "extendedProperties": {"private": {"w8lbz": "1", "w8lbz_key": "chow:2026-11-11"}}})
    cal.calls = []
    gcal.sync(c, events[:1], THU, cal)
    assert cal.calls == []


def test_run_records_failures_and_skips_without_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(gcal, "DATA", tmp_path)
    plain = Club(FIX / "test-club.yaml")
    assert gcal.run(plain, None) == {} and gcal.status() is None  # no calendars in club.yaml
    monkeypatch.setattr(gcal, "_session", lambda: (_ for _ in ()).throw(RuntimeError("no key file")))
    st = gcal.run(club(), Archive(tmp_path, club()))
    assert "no key file" in st["error"] and gcal.status()["error"] == st["error"]
