"""Roster keepers edit what the sheet holds (values only, ticks as checkboxes), with undo; Membership Log events."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import membership, profile, roster, web
from club_minutes.club import Club
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "n0aaaa", "first": "Amy", "last": "Appleseed", "callsign": "N0AAAA", "email": "amy@example.org", "role": "Secretary",
           "officer": True, "board": True, "license_class": "General", "events": [{"date": "2020-03-05", "event": "Joined", "note": None}],
           "member_since": "2020-03-05"},
          {"id": "n0bbbb", "first": "Bob", "last": "Bloggs", "callsign": "N0BBBB", "email": "bob@example.org",
           "license_class": "Technician", "aliases": ["Bobby"]}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "roster.json"
    cache.write_text(json.dumps(PEOPLE))
    for mod, attr in ((roster, "CACHE"), (membership, "ROSTER_CACHE"), (profile, "ROSTER_CACHE")):
        monkeypatch.setattr(mod, attr, cache)
    monkeypatch.setattr(roster, "PREVIEW", "test")
    for mod in (roster, profile):
        monkeypatch.setattr(mod, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(profile.auth, "send_email", lambda to, subject, body, **kw: sent.append((to, subject)))
    club = Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text()))
    monkeypatch.setattr(web, "archive", Archive(tmp_path, club))
    who = {"id": "n0aaaa"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    return club, tmp_path, sent, who, TestClient(web.app)


def form(**over):
    f = {"first": "Bob", "last": "Bloggs", "license_class": "Technician", "status": "Active", "email": "bob@example.org",
         "phone": "", "address": "", "role": "", "aliases": "Bobby"}
    return {**f, **over}


def sheet_row(tmp, cs):
    rows = json.loads((tmp / "preview-sheet.json").read_text())["Member Roster"]
    return next(dict(zip(rows[0], r)) for r in rows[1:] if r[0] == cs)


def test_keeper_edits_and_undo(env):
    club, tmp, sent, _who, c = env
    r = c.post("/members/n0bbbb/edit", data=form(first="Robert", license_class="General", minutes_editor="1",
                                               role="Net Manager", aliases="Bobby, Bob B"), follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    b = club.members["n0bbbb"]
    assert (b.name, b.license_class, b.minutes_editor, b.role, b.aliases) == ("Robert Bloggs", "General", True, "Net Manager", ["Bobby", "Bob B"])
    row = sheet_row(tmp, "N0BBBB")
    assert row["full_name"] == "Robert Bloggs" and row["Class"] == "General" and row["minutes_editor"] is True  # a checkbox
    assert row["is_officer"] is False and json.loads((tmp / "roster.json").read_text())[1]["first"] == "Robert"
    e = profile.history()[0]
    assert e["new"]["minutes_editor"] == "TRUE" and e["old"]["minutes_editor"] == "FALSE" and "officer" not in e["new"]
    assert any("bob@example.org" in to for to, _ in sent)  # the member is told
    profile.undo(club, e["id"], "n0aaaa")
    assert (b.name, b.minutes_editor, b.aliases) == ("Bob Bloggs", False, ["Bobby"]) and sheet_row(tmp, "N0BBBB")["minutes_editor"] is False
    assert "Pick%20a%20license%20class" in str(c.post("/members/n0bbbb/edit", data=form(status="Retired"), follow_redirects=False).headers["location"])
    assert "isn't" not in c.get("/members/n0bbbb/edit").text and "Edit roster details" in c.get("/members/n0bbbb").text


def test_membership_log_event(env):
    club, tmp, _, _, c = env
    c.post("/members/n0bbbb/log", data={"event": "Rejoined", "date": "2026-11-05", "note": "back from Florida"})
    b = club.members["n0bbbb"]
    assert b.member_since == "2026-11-05" and b.events[-1] == {"date": "2026-11-05", "event": "Rejoined", "note": "back from Florida"}
    log = json.loads((tmp / "preview-sheet.json").read_text())["Membership Log"]
    assert log[-1] == ["N0BBBB", "Bob Bloggs", "2026-11-05", "Rejoined", "back from Florida"]
    assert profile.history()[0]["logged"] and "Membership Log" in c.get("/roster/changes").text
    assert "error=" in c.post("/members/n0bbbb/log", data={"event": "Other", "date": "2026-11-05"}, follow_redirects=False).headers["location"]


def test_only_roster_keepers(env):
    club, _, _, who, c = env
    who["id"] = "n0bbbb"
    assert c.get("/members/n0aaaa/edit").status_code == 403 and "Edit roster details" not in c.get("/members/n0aaaa").text
    assert c.post("/members/n0aaaa/edit", data=form(first="Mallory")).status_code == 403
    assert c.post("/members/n0aaaa/log", data={"event": "Left", "date": "2026-11-05"}).status_code == 403
    assert club.members["n0aaaa"].first == "Amy"


def test_admins_email_needs_admin_mode(env, monkeypatch):
    """A roster keeper can't point an admin's sign-in (and admin-mode confirmation) email at another inbox."""
    club, _tmp, _, _who, c = env
    club.admins = {"n0bbbb"}
    r = c.post("/members/n0bbbb/edit", data=form(email="mallory@example.org"), follow_redirects=False)
    assert "Only%20an%20admin" in r.headers["location"] and club.members["n0bbbb"].email == "bob@example.org"
    c.post("/members/n0bbbb/edit", data=form(phone="419-555-0102"))  # other details are fine
    assert club.members["n0bbbb"].phone == "419-555-0102"
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c.post("/members/n0bbbb/edit", data=form(email="bob@new.example.org"))
    assert club.members["n0bbbb"].email == "bob@new.example.org"
