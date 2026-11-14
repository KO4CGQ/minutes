"""Offices: who holds each, a change of holder written to the sheet (titles, ticks, Membership Log), the history."""
import json
from pathlib import Path

import pytest

from club_minutes import membership, offices, profile, roster
from club_minutes.club import Club
from club_minutes.roster import Sheet

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "n0aaaa", "first": "Amy", "last": "Appleseed", "callsign": "N0AAAA", "email": "amy@example.org",
           "role": "President", "officer": True, "board": True},
          {"id": "n0bbbb", "first": "Bob", "last": "Bloggs", "callsign": "N0BBBB", "email": "bob@example.org",
           "role": "Treasurer, Net Manager", "officer": True, "board": True,
           "events": [{"date": "2024-01-04", "event": "Took office", "note": "Treasurer"}]},
          {"id": "n0cccc", "first": "Cal", "last": "Citizen", "callsign": "N0CCCC", "email": "cal@example.org"},
          {"id": "n0dddd", "first": "Dee", "last": "Dummy", "callsign": "N0DDDD", "role": "Trustee", "board": True}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "roster.json"
    cache.write_text(json.dumps(PEOPLE))
    for mod, attr in ((roster, "CACHE"), (membership, "ROSTER_CACHE"), (offices, "ROSTER_CACHE")):
        monkeypatch.setattr(mod, attr, cache)
    monkeypatch.setattr(roster, "PREVIEW", "test")
    monkeypatch.setattr(roster, "DATA", tmp_path)
    monkeypatch.setattr(profile, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(offices.auth, "send_email", lambda to, subject, body, **kw: sent.append((to, subject)))
    club = Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text()))
    club.offices = ["President", "Treasurer", {"title": "Trustee", "officer": False, "seats": 2}]
    return club, tmp_path, sent


def grid(tmp, tab="Member Roster"):
    rows = json.loads((tmp / "preview-sheet.json").read_text())[tab]
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def test_change_of_holder(env):
    club, tmp, sent = env
    sheet = Sheet()
    assert offices.holders(club, "Treasurer") == ["n0bbbb"]
    offices.change(club, "Treasurer", "n0bbbb", "n0cccc", "2026-11-05", "n0aaaa", sheet=sheet)
    bob, cal = club.members["n0bbbb"], club.members["n0cccc"]
    assert (bob.role, bob.officer, bob.board) == ("Net Manager", False, False)  # other titles kept
    assert (cal.role, cal.officer, cal.board) == ("Treasurer", True, True)
    rows = {r["callsign"]: r for r in grid(tmp)}
    assert rows["N0BBBB"]["Title(s)"] == "Net Manager" and rows["N0BBBB"]["is_officer"] is False
    assert rows["N0CCCC"]["Title(s)"] == "Treasurer" and rows["N0CCCC"]["is_board"] is True
    assert [(r["callsign"], r["event"], r["note"]) for r in grid(tmp, "Membership Log")] == [
        ("N0BBBB", "Left office", "Treasurer"), ("N0CCCC", "Took office", "Treasurer")]
    assert json.loads((tmp / "roster.json").read_text())[2]["role"] == "Treasurer"   # the cache too
    assert offices.terms(club, "Treasurer") == [
        {"member": "n0cccc", "start": "2026-11-05", "end": None, "open": True},
        {"member": "n0bbbb", "start": "2024-01-04", "end": "2026-11-05", "open": False}]
    to, subject = sent[-1]
    assert "bob@example.org" in to and "cal@example.org" in to and "Cal Citizen took over as Treasurer" in subject
    assert profile.history()[0]["office"] and "can't be undone" in str(pytest.raises(ValueError, profile.undo, club,
                                                                                     profile.history()[0]["id"], "n0aaaa").value)


def test_change_refuses_mistakes(env):
    club, _, _ = env
    for args, msg in ((("Dogcatcher", None, "n0cccc"), "No such office"), (("Treasurer", "n0aaaa", "n0cccc"), "doesn't hold"),
                      (("President", None, "n0cccc"), "is full"), (("President", "n0aaaa", "n0aaaa"), "already holds"),
                      (("President", None, None), "Pick who")):
        with pytest.raises(ValueError, match=msg):
            offices.change(club, *args, "2026-11-05", "n0aaaa", sheet=Sheet())
    offices.change(club, "Trustee", None, "n0cccc", "2026-11-05", "n0aaaa", sheet=Sheet())  # a vacant seat filled
    c = club.members["n0cccc"]
    assert (c.role, c.officer, c.board) == ("Trustee", False, True)


def test_past_term_and_page(env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.store import Archive
    club, _, _ = env
    monkeypatch.setattr(web, "archive", Archive(tmp_path, club))
    monkeypatch.setattr(web.authn, "member_for", lambda token: "n0aaaa")
    admin = {"on": False}
    monkeypatch.setattr(web, "admin_mode", lambda request: admin["on"])
    c = TestClient(web.app)
    page = c.get("/offices").text
    assert "Treasurer" in page and "Bob Bloggs" in page and "vacant" in page and "Add a past term" not in page
    assert c.post("/offices/term", data={"title": "President", "member": "Cal Citizen", "start": "2020-01-02",
                                         "end": "2021-01-07"}).status_code == 403
    admin["on"] = True
    c.post("/offices/term", data={"title": "President", "member": "Cal Citizen", "start": "2020-01-02", "end": "2021-01-07"})
    assert club.members["n0cccc"].role is None  # history only
    assert {"member": "n0cccc", "start": "2020-01-02", "end": "2021-01-07", "open": False} in offices.terms(club, "President")
    r = c.post("/offices/change", data={"title": "President", "out": "n0aaaa", "new": "Cal Citizen", "date": "2026-11-05"},
               follow_redirects=False)
    assert "President%20updated" in r.headers["location"] and club.members["n0cccc"].role == "President"
    assert "error=" in c.post("/offices/change", data={"title": "President", "new": "Bob Bloggs", "date": "x"},
                              follow_redirects=False).headers["location"]
