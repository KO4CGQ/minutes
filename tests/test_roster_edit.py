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
    r = c.post("/members/n0bbbb/edit", data=form(first="Robert", license_class="General", aliases="Bobby, Bob B"), follow_redirects=False)
    assert "tab=details&saved=1" in r.headers["location"]
    r = c.post("/members/n0bbbb/edit", data={"tab": "roles", "minutes_editor": "1", "title_new": "Net Manager"}, follow_redirects=False)
    assert "tab=roles&saved=1" in r.headers["location"]
    b = club.members["n0bbbb"]
    assert (b.name, b.license_class, b.minutes_editor, b.role, b.aliases) == ("Robert Bloggs", "General", True, "Net Manager", ["Bobby", "Bob B"])
    row = sheet_row(tmp, "N0BBBB")
    assert row["full_name"] == "Robert Bloggs" and row["Class"] == "General" and row["minutes_editor"] is True  # a checkbox
    assert row["is_officer"] is False and json.loads((tmp / "roster.json").read_text())[1]["first"] == "Robert"
    e = profile.history()[0]
    assert e["new"]["minutes_editor"] == "TRUE" and e["old"]["minutes_editor"] == "FALSE" and "officer" not in e["new"]
    assert "name" not in e["new"] and "role" in e["new"]  # the roles tab saved only its own fields
    assert any("bob@example.org" in to for to, _ in sent)  # the member is told
    profile.undo(club, e["id"], "n0aaaa")
    profile.undo(club, profile.history()[2]["id"], "n0aaaa")  # and the details change before it
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


def test_titles_are_picked_from_a_list_and_offices_stay_put(env):
    """Titles: the member's offices are kept as they are (Offices page only); other titles are ticked from those in use
    on the roster, or added; an office can't be added here."""
    club, _tmp, _sent, _who, c = env
    c.post("/members/n0bbbb/edit", data={"tab": "roles", "title_new": "Net Manager, Webmaster"})
    assert club.members["n0bbbb"].role == "Net Manager, Webmaster"
    page_ = c.get("/members/n0aaaa/edit?tab=roles").text
    assert '<span class="chip">Secretary</span>' in page_ and 'name="title" value="Secretary"' not in page_  # an office: shown, not offered
    assert 'name="title" value="Net Manager"' in page_ and 'name="title" value="Webmaster"' in page_       # titles in use: offered
    c.post("/members/n0aaaa/edit", data={"tab": "roles", "title": ["Webmaster"], "title_new": "Field Day chair"})
    assert club.members["n0aaaa"].role == "Secretary, Webmaster, Field Day chair"  # the office kept, first
    c.post("/members/n0aaaa/edit", data={"tab": "roles"})
    assert club.members["n0aaaa"].role == "Secretary"  # unticking every title leaves the office
    r = c.post("/members/n0bbbb/edit", data={"tab": "roles", "title_new": "president"}, follow_redirects=False)
    assert "is%20an%20office" in r.headers["location"] and club.members["n0bbbb"].role == "Net Manager, Webmaster"
    c.post("/members/n0bbbb/edit", data={"tab": "roles", "title": ["Secretary", "Webmaster"]})  # a forged tick of an office
    assert club.members["n0bbbb"].role == "Webmaster"
    c.post("/members/n0bbbb/edit", data=form(phone="555-0123"))  # the Details tab leaves the titles alone
    assert club.members["n0bbbb"].role == "Webmaster" and club.members["n0bbbb"].phone == "555-0123"


def test_officer_and_board_follow_the_offices_not_the_edit_page(env):
    """Roles: Officer and On the board show, greyed out, and stay as the Offices page set them; the editor roles change here."""
    club, _tmp, _sent, _who, c = env
    page_ = c.get("/members/n0bbbb/edit?tab=roles").text
    assert "<legend>Roles</legend>" in page_ and 'name="officer"' not in page_ and 'name="board"' not in page_
    c.post("/members/n0bbbb/edit", data={"tab": "roles", "officer": "1", "board": "1", "calendar_editor": "1"})
    b = club.members["n0bbbb"]
    assert (b.officer, b.board, b.calendar_editor) == (False, False, True)
    c.post("/members/n0aaaa/edit", data={"tab": "roles"})  # no ticks posted: an officer's are shown, not sent
    a = club.members["n0aaaa"]
    assert (a.officer, a.board) == (True, True)  # the Secretary stays one
    assert "included with Officer" in c.get("/members/n0aaaa/edit?tab=roles").text  # leads and edits calendars anyway
