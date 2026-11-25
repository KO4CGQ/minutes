"""A leaver's personal details go 90 days after the daily job first sees them gone (prefs.purge_removed)."""
import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import avatars, calview, membership, prefs, profile, roster, web
from club_minutes.auth import Auth
from club_minutes.club import Club
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
DAY0 = dt.date(2026, 11, 1)
PEOPLE = [{"id": "n0aaaa", "first": "Amy", "last": "Appleseed", "callsign": "N0AAAA", "email": "amy@example.org", "role": "Secretary", "officer": True},
          {"id": "n0bbbb", "first": "Bob", "last": "Bloggs", "callsign": "N0BBBB", "email": "bob@example.org", "phone": "555-0101",
           "address": "123 Main St, Anytown", "birthday": "03-04", "share": ["email"], "status": "Inactive"},
          {"id": "n0cccc", "first": "Cal", "last": "Customer", "callsign": "N0CCCC"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "roster.json"
    cache.write_text(json.dumps(PEOPLE))
    for mod, attr in ((roster, "CACHE"), (membership, "ROSTER_CACHE"), (profile, "ROSTER_CACHE")):
        monkeypatch.setattr(mod, attr, cache)
    monkeypatch.setattr(roster, "PREVIEW", "test")
    for mod in (roster, profile, prefs, avatars, calview):
        monkeypatch.setattr(mod, "DATA", tmp_path)
    monkeypatch.setattr(profile.auth, "DATA", tmp_path)
    return tmp_path, cache


def load(cache):
    return lambda: Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text()))


def row(tmp, cs):
    g = json.loads((tmp / "preview-sheet.json").read_text())["Member Roster"]
    return next(dict(zip(g[0], r)) for r in g[1:] if r[0] == cs)


def test_details_go_at_90_days_not_before(env):
    tmp, cache = env
    (tmp / "avatars").mkdir()
    (tmp / "avatars" / "n0bbbb.jpg").write_bytes(b"x")
    token = calview.feed_token("n0bbbb")
    calview.feed_token("n0aaaa")
    profile._append({"id": "1", "at": 1, "member": "n0bbbb", "by": "n0aaaa", "old": {}, "new": {}})
    profile._append({"id": "2", "at": 2, "member": "n0aaaa", "by": "n0aaaa", "old": {}, "new": {}})
    roster.PreviewSheet()  # the sheet, made from the roster cache
    club = load(cache)
    assert prefs.purge_removed(club, DAY0) == (0, 0)
    assert json.loads((tmp / "removed.json").read_text()) == {"n0bbbb": {"date": "2026-11-01"}}  # noted the first night
    assert prefs.purge_removed(club, DAY0 + dt.timedelta(days=30)) == (0, 0)  # the date stays the first one
    assert prefs.purge_removed(club, DAY0 + dt.timedelta(days=89)) == (0, 0)
    assert row(tmp, "N0BBBB")["email"] == "bob@example.org" and (tmp / "avatars" / "n0bbbb.jpg").exists()
    assert prefs.purge_removed(club, DAY0 + dt.timedelta(days=90)) == (0, 1)
    r = row(tmp, "N0BBBB")
    assert [r[k] for k in ("email", "Phone", "Address", "birthday", "share")] == [""] * 5
    assert r["full_name"] == "Bob Bloggs" and r["callsign"] == "N0BBBB" and r["Status"] == "Inactive"
    assert not (tmp / "avatars" / "n0bbbb.jpg").exists() and calview.member_for_feed(token) is None
    assert calview.feed_token("n0aaaa") and [e["member"] for e in profile.history()] == ["n0aaaa"]
    assert not json.loads(cache.read_text())[1].get("email")  # the roster cache too
    before = (tmp / "removed.json").read_text()
    assert prefs.purge_removed(club, DAY0 + dt.timedelta(days=91)) == (0, 0) and (tmp / "removed.json").read_text() == before  # once


def test_active_again_clears_the_date(env):
    tmp, cache = env
    prefs.purge_removed(load(cache), DAY0)
    people = json.loads(cache.read_text())
    people[1]["status"] = "Active"
    cache.write_text(json.dumps(people))
    prefs.purge_removed(load(cache), DAY0 + dt.timedelta(days=100))
    assert json.loads((tmp / "removed.json").read_text()) == {}


def test_a_truncated_roster_notes_and_purges_nothing(env):
    tmp, _ = env
    for m in ("n0aaaa", "n0bbbb", "n0zzzz"):
        prefs.save(m, {"minutes": "link"})
    assert prefs.purge_removed(lambda: Club(FIX / "test-club.yaml", roster=[PEOPLE[0]]), DAY0) == (0, 0)
    assert not (tmp / "removed.json").exists()


def test_the_countdown_shows_to_roster_keepers_only(env, monkeypatch):
    tmp, cache = env
    club = load(cache)()
    monkeypatch.setattr(web, "archive", Archive(tmp, club))
    monkeypatch.setattr(web, "authn", Auth(club, tmp / "auth.sqlite"))
    who = {"id": "n0aaaa"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    c = TestClient(web.app)
    prefs.purge_removed(load(cache), dt.date.today())
    want = f"deleted on {(dt.date.today() + dt.timedelta(days=90)).strftime('%B')}"
    assert "Email, phone, address and photo are " + want in c.get("/members/n0bbbb").text
    who["id"] = "n0cccc"
    assert "unless they rejoin" not in c.get("/members/n0bbbb").text


def test_made_active_after_the_job_read_the_roster_is_left_alone(env):
    """The job's roster can be a moment old: forget_member checks the roster again under its lock."""
    tmp, cache = env
    roster.PreviewSheet()
    stale = load(cache)()  # read while Bob was Inactive
    people = json.loads(cache.read_text())
    people[1]["status"] = "Active"
    cache.write_text(json.dumps(people))
    assert profile.forget_member(stale, "n0bbbb") is False and row(tmp, "N0BBBB")["email"] == "bob@example.org"


def test_a_damaged_entry_breaks_neither_the_job_nor_the_page(env):
    tmp, cache = env
    (tmp / "removed.json").write_text(json.dumps({"n0bbbb": {"date": "soon"}}))
    assert prefs.purge_removed(load(cache), DAY0) == (0, 0) and prefs.purge_note("n0bbbb") is None
