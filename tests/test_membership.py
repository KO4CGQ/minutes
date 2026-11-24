"""Adding a member: a new roster row and Membership Log row (values only), the app updated, guests offered."""
import json
import re
from pathlib import Path

import pytest

from club_minutes import chow, membership, profile, roster
from club_minutes.club import Club
from club_minutes.roster import Sheet

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "n0aaaa", "first": "Jane", "last": "Doe", "callsign": "N0AAAA", "email": "jane@example.org",
           "role": "Secretary", "officer": True, "status": "Active"},
          {"id": "n0bbbb", "first": "Tom", "last": "Tester", "callsign": "N0BBBB", "email": "tom@example.org",
           "role": "Treasurer", "officer": True, "status": "Active"},
          {"id": "n0cccc", "first": "Bob", "last": "Member", "callsign": "N0CCCC", "status": "Active"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "roster.json"
    cache.write_text(json.dumps(PEOPLE))
    for mod, attr in ((roster, "CACHE"), (membership, "ROSTER_CACHE")):
        monkeypatch.setattr(mod, attr, cache)
    monkeypatch.setattr(roster, "PREVIEW", "test")
    monkeypatch.setattr(roster, "DATA", tmp_path)
    monkeypatch.setattr(chow, "DATA", tmp_path)
    monkeypatch.setattr(profile, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(membership.auth, "send_email", lambda to, subject, body, **kw: sent.append((to, subject)))
    club = Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text()))
    return club, tmp_path, sent


def test_add_member_writes_rows_and_updates_the_app(env):
    club, tmp, sent = env
    sheet = Sheet()
    sheet.read()  # builds the preview grid
    tabs = json.loads((tmp / "preview-sheet.json").read_text())
    tabs["Member Roster"].append(["", "", "", "", "", "", "", "Active"])  # an empty row with only a dropdown value
    (tmp / "preview-sheet.json").write_text(json.dumps(tabs))
    mid = membership.add_member(club, {"first": "Ann", "last": "New", "callsign": "n0newc", "email": "ann@example.org",
                                       "license_class": "General", "joined": "2026-11-05"}, by="n0bbbb", sheet=sheet)
    assert re.fullmatch(r"m[a-z0-9]{6}", mid) and club.members[mid].name == "Ann New" and club.members[mid].status == "Active"
    assert club.resolve("N0NEWC") == mid and club.member_url(mid) == "/members/n0newc"  # new members get a random id; pages use the callsign
    tabs = json.loads((tmp / "preview-sheet.json").read_text())
    roster_rows, log_rows = tabs["Member Roster"], tabs["Membership Log"]
    assert roster_rows[len(PEOPLE) + 1][:3] == ["N0NEWC", "Ann New", "ann@example.org"]  # into the dropdown-only row
    assert roster_rows[len(PEOPLE) + 1][roster_rows[0].index("id")] == mid
    assert len(roster_rows) == 1 + len(PEOPLE) + 1
    assert log_rows[1] == ["N0NEWC", "Ann New", "2026-11-05", "Joined", "Added in the app by Tom Tester"]
    assert json.loads((tmp / "roster.json").read_text())[-1]["member_since"] == "2026-11-05"
    assert chow.load()["order"] == [mid]                         # at the bottom of the chow rotation
    assert sent == [(["jane@example.org"], "New member: Ann New")]  # the Secretary is told
    with pytest.raises(ValueError):
        membership.add_member(club, {"first": "Ann", "last": "Again", "callsign": "N0NEWC", "license_class": "General"},
                              by="n0bbbb", sheet=sheet)
    e = profile.history()[0]
    assert e["added"] and e["member"] == mid and e["by"] == "n0bbbb" and e["new"]["callsign"] == "N0NEWC"
    with pytest.raises(ValueError):
        profile.undo(club, e["id"], by="n0aaaa")  # an addition isn't undone from Recent changes
    for bad in ({"first": "Ann"}, {"first": "A", "last": "B", "callsign": "not a call"},
                {"first": "A", "last": "B", "callsign": "N0WXYZ", "license_class": "General", "email": "nope"},
                {"first": "A", "last": "B", "callsign": "N0WXYZ", "license_class": "General", "joined": "soon"}):
        with pytest.raises(ValueError):
            membership.clean(bad)


def test_ready_guests_and_names():
    guests = [{"name": "Pat Visitor", "visits": [1, 2, 3, 4], "member": None},
              {"name": "Once Only", "visits": [1], "member": None},
              {"name": "Bob Member", "visits": [1, 2, 3, 4, 5], "member": "n0cccc"}]
    assert [g["name"] for g in membership.ready_guests(guests, 4)] == ["Pat Visitor"]
    assert membership.split_name("Mary Ann Smith") == ("Mary Ann", "Smith")


def test_only_roster_keepers_can_add(env, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.store import Archive
    club, tmp, _ = env
    monkeypatch.setattr(web, "archive", Archive(tmp, club))
    who = {"id": "n0cccc"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    c = TestClient(web.app)
    assert c.get("/roster/add").status_code == 403 and "Add a member" not in c.get("/roster").text
    who["id"] = "n0bbbb"  # the Treasurer
    assert "Add a member" in c.get("/roster").text and c.get("/roster/add").status_code == 200
    r = c.post("/roster/add", data={"first": "Ann", "last": "New", "callsign": "N0NEWC", "license_class": "Technician"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/members/n0newc?added=1"  # the callsign, not the id
    assert "Added to the roster" in c.get(r.headers["location"]).text
    assert "Added to the roster" in c.get("/roster/changes").text and "Tom Tester" in c.get("/roster/changes").text
    assert "already on the roster" in c.post("/roster/add", data={"first": "X", "last": "Y", "callsign": "N0NEWC", "license_class": "General"}).text


def test_ham_default_and_the_setting(env, monkeypatch):
    club, tmp, _ = env
    monkeypatch.setattr("club_minutes.club.DATA", tmp)
    assert club.ham and club.label == "W0TEST" and club.sign_off == "73,"  # a callsign in club.yaml: on
    no_call = tmp / "club.yaml"
    no_call.write_text("club: {name: Plain Club}\n")
    assert not Club(no_call, roster=[]).ham
    no_call.write_text("club: {name: Plain Club, callsign: W0DEMO}\n")
    assert Club(no_call, roster=[]).ham
    no_call.write_text("club: {name: Plain Club, callsign: W0DEMO, ham: false}\n")  # a saved value wins
    assert not Club(no_call, roster=[]).ham
    # on: a member needs neither callsign nor license class, and a typed callsign is still checked
    mid = membership.add_member(club, {"first": "Pat", "last": "Plain"}, by="n0bbbb", sheet=Sheet())
    assert club.members[mid].callsign is None and club.member_url(mid) == "/members/" + mid
    with pytest.raises(ValueError):
        membership.clean({"first": "A", "last": "B", "callsign": "not a call"})
    no_call.write_text(f"club: {{name: Plain Club}}\nadmins: [{mid}]\n")  # admins: takes a member id too, and the login command finds one
    plain = Club(no_call, roster=[dict(PEOPLE[0], id=mid, callsign=None)])
    assert plain.is_admin(mid) and plain.resolve(mid.upper()) == mid
    # off: nothing is asked or kept, the data already there stays, and old callsign links still resolve
    club.save_site_settings({("club", "ham"): False})
    assert not club.ham and club.label == "Test Club" and club.sign_off == "Thanks,"
    assert club.member_url("n0aaaa") == "/members/n0aaaa" and club.resolve("N0AAAA") == "n0aaaa"
    assert club.members["n0aaaa"].callsign == "N0AAAA"
    f = membership.clean({"first": "A", "last": "B", "callsign": "N0WXYZ", "license_class": "General"}, ham=False)
    assert f["callsign"] == f["license_class"] == ""
    mid2 = membership.add_member(club, {"first": "Quinn", "last": "Quiet", "callsign": "N0QUIE"}, by="n0bbbb", sheet=Sheet())
    assert club.members[mid2].callsign is None
    club.save_site_settings({("club", "ham"): None})  # back to the default: on again
    assert club.ham


def test_pages_without_callsigns(env, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.store import Archive
    club, tmp, _ = env
    monkeypatch.setattr("club_minutes.club.DATA", tmp)
    monkeypatch.setattr(web, "archive", Archive(tmp, club))
    monkeypatch.setattr(web.authn, "member_for", lambda token: "n0bbbb")  # the Treasurer keeps the roster
    c = TestClient(web.app)
    assert "N0AAAA" in c.get("/roster").text and 'name="callsign"' in c.get("/roster/add").text
    club.save_site_settings({("club", "ham"): False})
    for url in ("/roster", "/roster/add", "/members/n0aaaa", "/members/n0aaaa/edit", "/guests", "/profile"):
        r = c.get(url)
        assert r.status_code == 200 and not re.search(r"(?i:callsign|license)|N0AAAA|N0BBBB", r.text), url  # (ids are lowercase: old ids were callsigns)
    assert c.get("/members/n0aaaa/edit?tab=callsign").text.count("New callsign") == 0
    assert c.post("/members/n0aaaa/callsign", data={"callsign": "N0ZZZZ", "date": "2026-11-01"}).status_code == 404
    r = c.post("/roster/add", data={"first": "Ann", "last": "New"}, follow_redirects=False)
    assert r.status_code == 303 and re.fullmatch(r"/members/m[a-z0-9]{6}\?added=1", r.headers["location"])
    page = c.get(r.headers["location"])
    assert page.status_code == 200 and "Ann New" in page.text
    c.post("/members/n0cccc/edit", data={"tab": "details", "first": "Bob", "last": "Member", "status": "Active"})
    assert club.members["n0cccc"].callsign == "N0CCCC"  # editing someone with no callsign on the form keeps it
