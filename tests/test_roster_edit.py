"""Roster keepers edit what the sheet holds (values only, ticks as checkboxes), with undo; Membership Log events."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from w8lbz_minutes import membership, profile, roster, web
from w8lbz_minutes.club import Club
from w8lbz_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "kd8aaa", "first": "Amy", "last": "Able", "callsign": "KD8AAA", "email": "amy@example.org", "role": "Secretary",
           "officer": True, "board": True, "license_class": "General", "events": [{"date": "2020-03-05", "event": "Joined", "note": None}],
           "member_since": "2020-03-05"},
          {"id": "kd8bbb", "first": "Bob", "last": "Baker", "callsign": "KD8BBB", "email": "bob@example.org",
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
    who = {"id": "kd8aaa"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    return club, tmp_path, sent, who, TestClient(web.app)


def form(**over):
    f = {"first": "Bob", "last": "Baker", "license_class": "Technician", "status": "Active", "email": "bob@example.org",
         "phone": "", "address": "", "role": "", "aliases": "Bobby"}
    return {**f, **over}


def sheet_row(tmp, cs):
    rows = json.loads((tmp / "preview-sheet.json").read_text())["Member Roster"]
    return next(dict(zip(rows[0], r)) for r in rows[1:] if r[0] == cs)


def test_keeper_edits_and_undo(env):
    club, tmp, sent, who, c = env
    r = c.post("/members/kd8bbb/edit", data=form(first="Robert", license_class="General", minutes_editor="1",
                                               role="Net Manager", aliases="Bobby, Bob B"), follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    b = club.members["kd8bbb"]
    assert (b.name, b.license_class, b.minutes_editor, b.role, b.aliases) == ("Robert Baker", "General", True, "Net Manager", ["Bobby", "Bob B"])
    row = sheet_row(tmp, "KD8BBB")
    assert row["full_name"] == "Robert Baker" and row["Class"] == "General" and row["minutes_editor"] is True  # a checkbox
    assert row["is_officer"] is False and json.loads((tmp / "roster.json").read_text())[1]["first"] == "Robert"
    e = profile.history()[0]
    assert e["new"]["minutes_editor"] == "TRUE" and e["old"]["minutes_editor"] == "FALSE" and "officer" not in e["new"]
    assert any("bob@example.org" in to for to, _ in sent)  # the member is told
    profile.undo(club, e["id"], "kd8aaa")
    assert (b.name, b.minutes_editor, b.aliases) == ("Bob Baker", False, ["Bobby"]) and sheet_row(tmp, "KD8BBB")["minutes_editor"] is False
    assert "Pick%20a%20license%20class" in str(c.post("/members/kd8bbb/edit", data=form(status="Retired"), follow_redirects=False).headers["location"])
    assert "isn't" not in c.get("/members/kd8bbb/edit").text and "Edit roster details" in c.get("/members/kd8bbb").text


def test_membership_log_event(env):
    club, tmp, _, _, c = env
    c.post("/members/kd8bbb/log", data={"event": "Rejoined", "date": "2026-11-05", "note": "back from Florida"})
    b = club.members["kd8bbb"]
    assert b.member_since == "2026-11-05" and b.events[-1] == {"date": "2026-11-05", "event": "Rejoined", "note": "back from Florida"}
    log = json.loads((tmp / "preview-sheet.json").read_text())["Membership Log"]
    assert log[-1] == ["KD8BBB", "Bob Baker", "2026-11-05", "Rejoined", "back from Florida"]
    assert profile.history()[0]["logged"] and "Membership Log" in c.get("/roster/changes").text
    assert "error=" in c.post("/members/kd8bbb/log", data={"event": "Other", "date": "2026-11-05"}, follow_redirects=False).headers["location"]


def test_only_roster_keepers(env):
    club, _, _, who, c = env
    who["id"] = "kd8bbb"
    assert c.get("/members/kd8aaa/edit").status_code == 403 and "Edit roster details" not in c.get("/members/kd8aaa").text
    assert c.post("/members/kd8aaa/edit", data=form(first="Mallory")).status_code == 403
    assert c.post("/members/kd8aaa/log", data={"event": "Left", "date": "2026-11-05"}).status_code == 403
    assert club.members["kd8aaa"].first == "Amy"
