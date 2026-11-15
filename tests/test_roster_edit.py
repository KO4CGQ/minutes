"""Roster keepers edit what the sheet holds (values only, ticks as checkboxes), with undo; Membership Log events."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import membership, profile, roster, web
from club_minutes.auth import Auth
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
    monkeypatch.setattr(profile.auth, "send_email", lambda to, subject, body, **kw: sent.append((to, subject, body)))
    club = Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text()))
    monkeypatch.setattr(web, "archive", Archive(tmp_path, club))
    monkeypatch.setattr(web, "authn", Auth(club, tmp_path / "auth.sqlite"))
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
    r = c.post("/members/n0bbbb/edit", data={"tab": "titles", "minutes_editor": "1", "title_new": "Net Manager"}, follow_redirects=False)
    assert "tab=titles&saved=1" in r.headers["location"]
    b = club.members["n0bbbb"]
    assert (b.name, b.license_class, b.minutes_editor, b.role, b.aliases) == ("Robert Bloggs", "General", False, "Net Manager", ["Bobby", "Bob B"])
    row = sheet_row(tmp, "N0BBBB")
    assert row["full_name"] == "Robert Bloggs" and row["Class"] == "General" and row["minutes_editor"] is False  # roles aren't set here
    assert row["is_officer"] is False and json.loads((tmp / "roster.json").read_text())[1]["first"] == "Robert"
    e = profile.history()[0]
    assert e["new"] == {"role": "Net Manager"}  # the Titles tab saved only its own field
    assert any("bob@example.org" in to for to, *_ in sent)  # the member is told
    profile.undo(club, e["id"], "n0aaaa")
    profile.undo(club, profile.history()[2]["id"], "n0aaaa")  # and the details change before it
    assert (b.name, b.role, b.aliases) == ("Bob Bloggs", None, ["Bobby"])
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


def _link(sent, to):
    return next(b for t, _s, b in reversed(sent) if t == to).split("/email/confirm?t=")[1].split()[0]


def test_another_members_email_waits_for_the_new_address(env):
    """Stage test of 1.0.0, problem 4: a roster keeper changing another member's email no longer changes it; the new
    address gets a link, the old one is told at once, and only the link (a button, so a mail scanner can't) changes it
    and signs the member out everywhere. A made-up or used link changes nothing."""
    club, _tmp, sent, _who, c = env
    r = c.post("/members/n0bbbb/edit", data=form(email="bob@new.example.org"), follow_redirects=False)
    assert "email_sent=bob%40new.example.org" in r.headers["location"] and club.members["n0bbbb"].email == "bob@example.org"
    assert {t for t, _s, _b in sent} >= {"bob@new.example.org", "bob@example.org"}  # the new address, and the old one told
    old = next(b for t, s_, b in sent if t == "bob@example.org" and "being changed" in s_)
    assert "Amy Appleseed asked to change your email" in old and "bob@new.example.org" in old
    s1 = web.authn.new_session("n0bbbb")
    token = _link(sent, "bob@new.example.org")
    anon = TestClient(web.app)
    assert 'name="t" value="' + token in anon.get(f"/email/confirm?t={token}").text and club.members["n0bbbb"].email == "bob@example.org"
    r = anon.post("/email/confirm", data={"t": token}, follow_redirects=False)
    assert r.headers["location"].startswith("/login?note=") and club.members["n0bbbb"].email == "bob@new.example.org"
    assert s1 and not web.authn.sessions("n0bbbb")  # every session ended
    assert "invalid or expired" in anon.post("/email/confirm", data={"t": token}).text           # one use
    assert "invalid or expired" in anon.post("/email/confirm", data={"t": "made-up"}).text


def test_email_and_status_need_their_permissions_and_no_one_above_you(env, monkeypatch, tmp_path):
    """Changing another member's email or status: its own permission (Roster keeper has both, as it ships), never for
    someone who can do more than you (an admin, or a permission you lack), and admin mode may change anyone."""
    from club_minutes import club as clubmod
    club, _tmp, sent, _who, c = env
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    t = club.role_table()
    club.save_roles({**t, "roster_keeper": {**t["roster_keeper"], "perms": ["members"]},
                     "admin_helper": {"name": "Status helper", "perms": ["emails", "status"], "offices": [], "members": ["n0bbbb"]}})
    r = c.post("/members/n0bbbb/edit", data=form(status="Inactive"), follow_redirects=False)
    assert "allow%20changing%20a%20member%27s%20status" in r.headers["location"] and club.members["n0bbbb"].status == "Active"
    page_ = c.get("/members/n0bbbb/edit").text
    assert '<select name="status" disabled>' in page_ and 'name="email" value="bob@example.org" disabled' in page_
    c.post("/members/n0bbbb/edit", data=form(phone="419-555-0102") | {"status": ""})  # disabled fields aren't sent: other details save
    assert club.members["n0bbbb"].phone == "419-555-0102" and club.members["n0bbbb"].status == "Active"
    club.save_roles({**club.role_table(), "roster_keeper": {**club.role_table()["roster_keeper"], "perms": ["members", "emails", "status"]}})
    club.members["n0bbbb"].role = "Treasurer"  # Bob may now record money, which Amy (the Secretary) can't
    r = c.post("/members/n0bbbb/edit", data=form(email="mallory@example.org"), follow_redirects=False)
    assert "Locked%3A%20Bob%20can%20record%20money%20and%20change%20the%20Treasurer%20settings%2C%20which%20you%20can%27t." in r.headers["location"] and not any(t == "mallory@example.org" for t, *_ in sent)
    club.members["n0bbbb"].role = ""
    club.admins = {"n0bbbb"}
    r = c.post("/members/n0bbbb/edit", data=form(status="Inactive"), follow_redirects=False)
    assert "Only%20an%20admin" in r.headers["location"] and club.members["n0bbbb"].status == "Active"
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c.post("/members/n0bbbb/edit", data=form(email="bob@new.example.org"))
    assert any(t == "bob@new.example.org" for t, *_ in sent)  # admin mode: the link goes out (it still waits for it)


def test_titles_are_picked_from_a_list_and_offices_stay_put(env):
    """Titles: the member's offices are kept as they are (Offices page only); other titles are ticked from those in use
    on the roster, or added; an office can't be added here."""
    club, _tmp, _sent, _who, c = env
    c.post("/members/n0bbbb/edit", data={"tab": "titles", "title_new": "Net Manager, Webmaster"})
    assert club.members["n0bbbb"].role == "Net Manager, Webmaster"
    page_ = c.get("/members/n0aaaa/edit?tab=titles").text
    assert '<span class="chip">Secretary</span>' in page_ and 'name="title" value="Secretary"' not in page_  # an office: shown, not offered
    assert 'name="title" value="Net Manager"' in page_ and 'name="title" value="Webmaster"' in page_       # titles in use: offered
    c.post("/members/n0aaaa/edit", data={"tab": "titles", "title": ["Webmaster"], "title_new": "Field Day chair"})
    assert club.members["n0aaaa"].role == "Secretary, Webmaster, Field Day chair"  # the office kept, first
    c.post("/members/n0aaaa/edit", data={"tab": "titles"})
    assert club.members["n0aaaa"].role == "Secretary"  # unticking every title leaves the office
    r = c.post("/members/n0bbbb/edit", data={"tab": "titles", "title_new": "president"}, follow_redirects=False)
    assert "is%20an%20office" in r.headers["location"] and club.members["n0bbbb"].role == "Net Manager, Webmaster"
    c.post("/members/n0bbbb/edit", data={"tab": "titles", "title": ["Secretary", "Webmaster"]})  # a forged tick of an office
    assert club.members["n0bbbb"].role == "Webmaster"
    c.post("/members/n0bbbb/edit", data=form(phone="555-0123"))  # the Details tab leaves the titles alone
    assert club.members["n0bbbb"].role == "Webmaster" and club.members["n0bbbb"].phone == "555-0123"


def test_roles_are_not_set_on_the_edit_page(env):
    """Roster → Edit has no role boxes: Officer and Board follow the offices, the rest are on Admin → Site → Roles.
    Forged role fields are ignored; the Titles tab says which roles the member has."""
    club, _tmp, _sent, _who, c = env
    page_ = c.get("/members/n0bbbb/edit?tab=titles").text
    assert "<legend>Roles</legend>" not in page_ and 'name="minutes_editor"' not in page_ and "Site → Roles" in page_
    c.post("/members/n0bbbb/edit", data={"tab": "titles", "officer": "1", "board": "1", "calendar_editor": "1", "minutes_editor": "1"})
    b = club.members["n0bbbb"]
    assert (b.officer, b.board, b.calendar_editor, b.minutes_editor) == (False, False, False, False)
    c.post("/members/n0aaaa/edit", data={"tab": "titles"})
    assert (club.members["n0aaaa"].officer, club.members["n0aaaa"].board) == (True, True)  # the Secretary stays one
    assert "roles: Officer, Minutes editor, Roster keeper, Secretary." in c.get("/members/n0aaaa/edit?tab=titles").text
    assert '<span class="chip">Roster keeper</span>' in c.get("/members/n0aaaa").text  # the member's page shows them


def test_another_members_callsign_is_never_an_other_spelling(env):
    """Security (1.0.1): a roster keeper who typed an admin's callsign into their own Other spellings became that admin
    (and the admin's sessions theirs). The edit page refuses it, and a roster that has one anyway ignores it, as it
    ignores a former callsign two members both list."""
    club, _tmp, _sent, _who, c = env
    club.admins = {"n0bbbb"}
    r = c.post("/members/n0aaaa/edit", data=form(first="Amy", last="Appleseed", email="amy@example.org", aliases="N0BBBB"),
               follow_redirects=False)
    assert "another%20member%27s%20callsign" in r.headers["location"] and club.members["n0aaaa"].aliases == []
    assert club.all_admins == {"n0bbbb"} and not club.is_admin("n0aaaa")
    rows = json.loads(json.dumps(PEOPLE))
    rows[0]["aliases"] = ["N0BBBB", "N0OLDX"]  # put in the sheet by hand
    rows[1]["aliases"] = ["Bobby", "N0OLDX"]
    c2 = Club(FIX / "test-club.yaml", roster=rows)
    c2.admins = {"n0bbbb", "n0oldx"}
    assert c2.canonical("n0bbbb") == "n0bbbb" and c2.canonical("n0oldx") == "n0oldx" and not c2.is_admin("n0aaaa")
    rows[1]["aliases"] = ["N0OLDX"]  # one member's own former callsign still works
    assert Club(FIX / "test-club.yaml", roster=rows[1:]).canonical("n0oldx") == "n0bbbb"


def test_a_members_own_email_change_completes(env):
    """In 1.0 the confirmation link went to /profile/email?t=…, which is the Email settings page, so a member's own
    email change never happened. Now it's /email/confirm, and it signs them out everywhere."""
    club, _tmp, sent, _who, c = env
    r = c.post("/profile", data={"email": "amy@new.example.org"}, follow_redirects=False)
    assert "email_sent=" in r.headers["location"] and club.members["n0aaaa"].email == "amy@example.org"
    assert not any(t == "amy@example.org" and "being changed" in s_ for t, s_, _b in sent)  # her own change: no warning
    TestClient(web.app).post("/email/confirm", data={"t": _link(sent, "amy@new.example.org")})
    assert club.members["n0aaaa"].email == "amy@new.example.org"


def test_the_last_active_admin_stays(env, monkeypatch, tmp_path):
    """Stage test of 1.0.0, problem 5: the last active admin can't be set Inactive or Silent Key (even by themselves, in
    admin mode) or removed; an Inactive admin doesn't count; the Admin page warns while there's only one."""
    from club_minutes import club as clubmod
    club, _tmp, _sent, who, c = env
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    club.admins = {"n0bbbb"}
    for s in ("Inactive", "Silent Key"):
        r = c.post("/members/n0bbbb/edit", data=form(status=s), follow_redirects=False)
        assert "last%20active%20admin" in r.headers["location"] and club.members["n0bbbb"].status == "Active", s
    who["id"] = "n0bbbb"
    assert "You're the only active admin" in c.get("/admin").text
    club.save_app_admins({"n0aaaa"})
    assert "You're the only active admin" not in c.get("/admin").text
    assert c.post("/admin/security/admins/remove", data={"member": "n0aaaa"}, follow_redirects=False).status_code == 303
    club.save_app_admins({"n0aaaa"})
    c.post("/members/n0bbbb/edit", data=form(status="Inactive"))  # two active admins: one may go
    assert club.members["n0bbbb"].status == "Inactive" and club.active_admins == {"n0aaaa"}
    who["id"] = "n0aaaa"
    r = c.post("/admin/security/admins/remove", data={"member": "n0aaaa"})
    assert r.status_code == 400 and "last active admin" in r.text and club.active_admins == {"n0aaaa"}


def test_an_email_change_is_checked_again_when_confirmed(env, monkeypatch, tmp_path):
    """Security sweep: in the 15 minutes before the link is opened, the keeper may lose the permission, the member may
    gain more than the keeper, or someone else may take the address. Then nothing changes."""
    from club_minutes import club as clubmod
    club, _tmp, sent, _who, c = env
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    c.post("/members/n0bbbb/edit", data=form(email="bob@new.example.org"))
    club.members["n0bbbb"].role = "Treasurer"  # Bob may now record money; Amy can't
    r = TestClient(web.app).post("/email/confirm", data={"t": _link(sent, "bob@new.example.org")})
    assert "can record money" in r.text and "Nothing was changed" in r.text and club.members["n0bbbb"].email == "bob@example.org"
    club.members["n0bbbb"].role = ""
    c.post("/members/n0bbbb/edit", data=form(email="taken@example.org"))
    club.members["n0aaaa"].email = "taken@example.org"
    r = TestClient(web.app).post("/email/confirm", data={"t": _link(sent, "taken@example.org")})
    assert "Another member uses that address now" in r.text and club.members["n0bbbb"].email == "bob@example.org"
