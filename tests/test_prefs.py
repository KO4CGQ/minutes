import datetime as dt
from pathlib import Path

from w8lbz_minutes import business, chow, console, prefs
from w8lbz_minutes.club import Club
from w8lbz_minutes.meeting import Meeting
from w8lbz_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
THU = dt.date(2026, 11, 12)
URL = "https://minutes.example.org"


def club():
    return Club(FIX / "test-club.yaml", roster=[
        {"id": "kd8aaa", "first": "Amy", "last": "Able", "email": "amy@example.org"},
        {"id": "kd8bbb", "first": "Bob", "last": "Baker", "email": "bob@example.org"},
        {"id": "kd8ccc", "first": "Cal", "last": "Cole", "email": "cal@example.org", "status": "Silent Key"}])


def test_defaults_and_validation():
    assert prefs.get("kd8aaa") == {"minutes": "now", "business": "now", "roster": "now", "chow_days": [3]}
    p = prefs.save("kd8aaa", {"minutes": "digest", "business": "nonsense", "roster": "digest", "evil": "x", "chow_days": ["7", "1", "5", "x"]})
    assert p == {"minutes": "digest", "business": "now", "roster": "now", "chow_days": [7, 1]}  # roster has no digest
    assert prefs.save("kd8aaa", {"chow_days": []})["chow_days"] == []  # none ticked: off


def test_minutes_go_only_to_those_who_want_them_now(tmp_path):
    c = club()
    prefs.save("kd8bbb", {"minutes": "digest"})
    assert console.recipients(Archive(tmp_path, c)) == ["amy@example.org"]  # Bob: digest; Cal: Silent Key


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


def test_weekly_digest_on_meeting_day(tmp_path, monkeypatch):
    monkeypatch.setattr(chow, "DATA", tmp_path)
    s = chow.load()
    s["nights"] = {THU.isoformat(): {"member": "kd8aaa", "status": "scheduled"}}
    chow.save(s)
    with business.locked():
        b = business.load()
        business.add(b, "Fix the tower", "kd8bbb", owner="kd8aaa", due="2026-11-20")
        business.save(b)
    meetings = [Meeting(date=dt.date(2026, 11, 5), status="published")]
    c = club()
    assert prefs.digest(c, meetings, THU, URL) == []  # nobody chose the digest
    prefs.save("kd8aaa", {"minutes": "digest", "business": "digest"})
    assert prefs.digest(c, meetings, THU - dt.timedelta(days=1), URL) == []  # not meeting day
    [(mid, key, subject, body)] = prefs.digest(c, meetings, THU, URL)
    assert mid == "kd8aaa" and key == "digest:2026-11-12:kd8aaa"
    assert "Thursday, November 12 (tonight)" in body and "Fix the tower (due Nov 20)" in body
    assert f"{URL}/meetings/2026-11-05" in body and f"{URL}/profile/email" in body


def test_email_tab(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    c = club()
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, a.new_session("kd8aaa"))
    r = t.get("/profile/email")
    assert r.status_code == 200 and "Roster changes" not in r.text  # Secretaries only
    r = t.post("/profile/email", data={"minutes": "off", "business": "digest", "chow_days": ["7", "1"]}, follow_redirects=False)
    assert r.headers["location"] == "/profile/email?saved=1"
    assert prefs.get("kd8aaa") == {"minutes": "off", "business": "digest", "roster": "now", "chow_days": [7, 1]}
