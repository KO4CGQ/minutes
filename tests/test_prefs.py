import datetime as dt
from pathlib import Path

from club_minutes import business, chow, console, events, prefs
from club_minutes.club import Club
from club_minutes.meeting import Item, Meeting
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
THU = dt.date(2026, 11, 12)
URL = "https://minutes.example.org"


def club():
    return Club(FIX / "test-club.yaml", roster=[
        {"id": "kd8aaa", "first": "Amy", "last": "Able", "email": "amy@example.org"},
        {"id": "kd8bbb", "first": "Bob", "last": "Baker", "email": "bob@example.org"},
        {"id": "kd8ccc", "first": "Cal", "last": "Cole", "email": "cal@example.org", "status": "Silent Key"}])


def test_defaults_and_validation():
    assert prefs.get("kd8aaa") == {"digest": "on", "minutes": "pdf", "business": "now", "roster": "now", "chowlist": "pdf", "chow_days": [3]}
    p = prefs.save("kd8aaa", {"minutes": "link", "business": "nonsense", "roster": "on", "evil": "x", "chow_days": ["7", "1", "5", "x"]})
    assert p == {"digest": "on", "minutes": "link", "business": "now", "roster": "now", "chowlist": "pdf", "chow_days": [7, 1]}
    assert prefs.save("kd8aaa", {"chow_days": []})["chow_days"] == []  # none ticked: off


def test_minutes_go_only_to_those_who_want_them_now(tmp_path):
    c = club()
    prefs.save("kd8bbb", {"minutes": "off"})
    assert console.recipients(Archive(tmp_path, c)) == ["amy@example.org"]  # Bob: off; Cal: Silent Key


def test_chow_reminders_on_the_chosen_days(tmp_path, monkeypatch):
    monkeypatch.setattr(chow, "DATA", tmp_path)
    s = chow.load()
    s["nights"] = {THU.isoformat(): {"member": "kd8aaa", "status": "scheduled"}}
    chow.save(s)
    c = club()
    [(mid, key, subject, body)] = prefs.chow_reminders(c, THU - dt.timedelta(days=3), URL)
    assert (mid, key) == ("kd8aaa", "chow:2026-11-12:kd8aaa:3") and "in 3 days" in subject and f"{URL}/chow" in body
    assert prefs.chow_reminders(c, THU - dt.timedelta(days=1), URL) == []  # not chosen
    c.members["kd8aaa"].email = None
    assert prefs.chow_reminders(c, THU - dt.timedelta(days=3), URL) == []  # no address
    c = club()
    prefs.save("kd8aaa", {"chow_days": ["1"]})
    assert "tomorrow" in prefs.chow_reminders(c, THU - dt.timedelta(days=1), URL)[0][2]
    # sent once, even if the daily run happens twice
    assert prefs.unsent(["chow:2026-11-12:kd8aaa:1"]) == {"chow:2026-11-12:kd8aaa:1"}
    prefs.mark_sent(["chow:2026-11-12:kd8aaa:1"], THU)
    assert prefs.unsent(["chow:2026-11-12:kd8aaa:1"]) == set()


def test_meeting_day_email(tmp_path, monkeypatch):
    monkeypatch.setattr(chow, "DATA", tmp_path)
    s = chow.load()
    s["nights"] = {THU.isoformat(): {"member": "kd8aaa", "status": "scheduled"}}
    chow.save(s)
    with business.locked():
        b = business.load()
        business.add(b, "Fix the tower", "kd8bbb", owner="kd8aaa", due="2026-11-20")
        business.save(b)
    Meeting(date=dt.date(2026, 11, 5), status="published", sections={"Upcoming Events": [Item("Hamfest in the spring")]}).save(tmp_path)
    with events.locked():
        evs = []
        events.add(evs, "events", events.from_form({"title": "VE testing", "date": "2026-11-14", "time": "10:00"}), "x")
        events.add(evs, "events", events.from_form({"title": "Next month", "date": "2026-12-14"}), "x")
        events.save(evs)
    c = club()
    arch = Archive(tmp_path, c)
    assert prefs.digest(c, arch, THU - dt.timedelta(days=1), URL) == []  # not meeting day
    prefs.save("kd8bbb", {"digest": "off"})
    [(mid, key, subject, body)] = prefs.digest(c, arch, THU, URL)  # on by default; Bob turned it off
    assert mid == "kd8aaa" and key == "digest:2026-11-12:kd8aaa" and subject.endswith("meeting tonight")
    assert "Next month" not in body and "Chow" not in body
    for want in ("tonight at 7:30 PM", "bringing chow tonight", "Fix the tower (due Nov 20)", "Sat Nov 14 10:00 AM: VE testing",
                 "Hamfest in the spring", f"{URL}/calendar", f"{URL}/meetings/2026-11-05", f"{URL}/profile/email"):
        assert want in body, want


def test_email_tab(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import web
    from club_minutes.auth import Auth
    c = club()
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, a.new_session("kd8aaa"))
    r = t.get("/profile/email")
    assert r.status_code == 200 and "Roster changes" not in r.text  # Secretaries only
    r = t.post("/profile/email", data={"digest": "off", "minutes": "off", "business": "now", "chow_days": ["7", "1"]}, follow_redirects=False)
    assert r.headers["location"] == "/profile/email?saved=1"
    assert prefs.get("kd8aaa") == {"digest": "off", "minutes": "off", "business": "now", "roster": "now", "chowlist": "pdf", "chow_days": [7, 1]}


def test_week_start_setting_and_month_grid(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import web
    from club_minutes.auth import Auth
    assert prefs.settings("kd8aaa") == prefs.settings(None) == {"week_start": "sunday"}
    assert prefs.save_settings("kd8aaa", {"week_start": "friday", "evil": "x"}) == {"week_start": "sunday"}  # not a choice
    c = club()
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, a.new_session("kd8aaa"))
    month = lambda: t.get("/calendar?view=month&month=2026-11").text  # noqa: E731  Nov 1, 2026 is a Sunday
    assert "<th>Sun</th><th>Mon</th>" in month() and 'aria-label="Sunday, November 1"' in month()
    assert 'aria-label="Saturday, December 5"' in month() and 'aria-label="Sunday, December 6"' not in month()  # ends with the week of the 30th
    r = t.post("/profile/settings", data={"week_start": "monday"}, follow_redirects=False)
    assert r.headers["location"] == "/profile/settings?saved=1" and 'value="monday" checked' in t.get("/profile/settings").text
    page = month()
    assert "<th>Mon</th><th>Tue</th>" in page and page.index("<th>Sun</th>") > page.index("<th>Sat</th>")
    assert 'aria-label="Monday, October 26"' in page and 'aria-label="Sunday, December 6"' in page
