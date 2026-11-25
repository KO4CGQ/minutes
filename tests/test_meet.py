"""Jitsi Meet sign-in (meet.py): off unless club.yaml turns it on; a short-lived HS256 token saying who joins and
whether they host (the meet_host permission), for signed-in members only."""
import base64
import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import club as clubmod
from club_minutes import meet, web
from club_minutes.auth import Auth
from club_minutes.club import Club
from club_minutes.meeting import Meeting
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
ROSTER = [{"id": "pres", "first": "Jane", "last": "Doe", "role": "President"}, {"id": "n0call", "first": "John", "last": "Doe"}]


def test_signing_matches_rfc_7515():
    """RFC 7515 A.1: its HS256 example, byte for byte (the signing input and key from the RFC)."""
    key = base64.urlsafe_b64decode("AyM1SysPpbyDfgZld3umj1qzKObwVMkoqQ-EstJQLr_T-1qS0gZH75aKtMN3Yj0iPS4hcgUuTwjAzZr1Z9CAow==")
    signing_input = bytes([101, 121, 74, 48, 101, 88, 65, 105, 79, 105, 74, 75, 86, 49, 81, 105, 76, 65, 48, 75, 73, 67, 74, 104,
                           98, 71, 99, 105, 79, 105, 74, 73, 85, 122, 73, 49, 78, 105, 74, 57, 46, 101, 121, 74, 112, 99, 51, 77, 105,
                           79, 105, 74, 113, 98, 50, 85, 105, 76, 65, 48, 75, 73, 67, 74, 108, 101, 72, 65, 105, 79, 106, 69, 122, 77,
                           68, 65, 52, 77, 84, 107, 122, 79, 68, 65, 115, 68, 81, 111, 103, 73, 109, 104, 48, 100, 72, 65, 54, 76, 121,
                           57, 108, 101, 71, 70, 116, 99, 71, 120, 108, 76, 109, 78, 118, 98, 83, 57, 112, 99, 49, 57, 121, 98, 50, 57,
                           48, 73, 106, 112, 48, 99, 110, 86, 108, 102, 81])
    assert meet.sign(signing_input, key) == "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    c = Club(FIX / "test-club.yaml", roster=ROSTER)
    c.modules["jitsi"] = True
    c.yaml = {**c.yaml, "modules": {**(c.yaml.get("modules") or {}), "jitsi": True},
              "jitsi": {"domain": "meet.example.org", "app_id": "club-minutes", "room": "club"}}
    monkeypatch.setenv("CLUB_MINUTES_JITSI_SECRET", "a-test-secret-not-a-real-one-0123456789")
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)

    def as_(who):
        t = TestClient(web.app, client=("203.0.113.6", 5000))
        if who:
            t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    return c, as_


def claims(location):
    jwt = location.split("?jwt=")[1].split("#")[0]
    head, body, sig = jwt.split(".")
    assert meet.sign(f"{head}.{body}".encode(), b"a-test-secret-not-a-real-one-0123456789") == sig
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_officers_host_members_join(env):
    _c, as_ = env
    r = as_("pres").get("/meet", follow_redirects=False)  # one room: its card too, so Join always opens a new tab
    assert r.status_code == 200 and r.text.count('aria-label="Join ') == 1
    r = as_("pres").get("/meet/club", follow_redirects=False)
    assert r.headers["location"].startswith("https://meet.example.org/club?jwt=")
    t = claims(r.headers["location"])
    assert (t["aud"], t["iss"], t["sub"], t["room"], t["moderator"]) == ("jitsi", "club-minutes", "meet.example.org", "club", True)
    assert t["context"]["user"] == {"id": "pres", "name": "Jane Doe", "affiliation": "owner",
                                       "lobby_bypass": True} and t["exp"] - t["iat"] == 300
    t = claims(as_("n0call").get("/meet/club", follow_redirects=False).headers["location"])
    assert (t["room"], t["moderator"], t["context"]["user"]["affiliation"]) == ("club", False, "member")
    assert t["context"]["room"]["club"] == {"guests": True, "guest_mic": True, "guest_camera": True, "guest_screen": True,
                                            "guest_chat": True, "last_host_leaves": "stay"}  # club.yaml's one room: no limits
    assert r.headers["referrer-policy"] == "no-referrer"
    assert as_("n0call").get("/meet/net-night", follow_redirects=False).status_code == 404  # not a club room, none of their own


def test_rooms_rules_and_rooms_of_their_own(env, monkeypatch):
    """Site → Video: named rooms with their rules for guests (in every member's token), and rooms members open
    themselves, whose opener hosts them; the calendar offers the rooms for an event's online link."""
    c, as_ = env
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c.save_app_admins({"pres"})
    room = {"name0": "Club meeting", "slug0": "club", "about0": "Tuesdays\n at 7 <b>PM</b>", "guests0": "1", "chat0": "1", "leaves0": "end",
            "name1": "Board", "slug1": "", "leaves1": "stay", "p2p1": "direct"}
    as_("pres").post("/admin/site/video", data={"rows": "2", **room, "adhoc": "1", "guests_a": "1", "leaves_a": "guests"})
    assert [r["slug"] for r in meet.rooms(c)] == ["club", "board"]
    tab = as_("pres").get("/admin/site/video").text  # each room's fields named by their room for a screen reader
    assert '<span class="sr">Room: </span><b>Club meeting</b>' in tab and 'aria-label="Room:' not in tab
    t = claims(as_("n0call").get("/meet/club", follow_redirects=False).headers["location"])
    assert t["context"]["room"]["club"] == {"guests": True, "guest_mic": False, "guest_camera": False, "guest_screen": False,
                                            "guest_chat": True, "last_host_leaves": "end"}
    assert as_("n0call").get("/meet/club", follow_redirects=False).headers["location"].endswith("#config.p2p.enabled=false")  # kept on the server
    loc = as_("n0call").get("/meet/board", follow_redirects=False).headers["location"]
    assert claims(loc.split("#")[0])["context"]["room"]["club"]["guests"] is False and "#" not in loc  # peer-to-peer allowed
    assert claims(loc.split("#")[0])["context"]["room"]["club"]["guest_mic"] is True  # members only: nobody's mic held back
    assert meet.rooms(c)[1]["guest_mic"] is False  # the tick itself is kept
    p = as_("n0call").get("/meet")  # two rooms: a page, not straight in
    assert "form-action 'self' https://meet.example.org;" in p.headers["content-security-policy"] + ";"  # "Open it" ends there
    assert "form-action 'self';" in as_("n0call").get("/calendar").headers["content-security-policy"] + ";"  # only there
    assert "Join Board" in p.text and "(members only)" in p.text and "http://testserver/meet/club" in p.text  # one address to share
    assert p.text.count('target="_blank" rel="noopener" aria-label="Join ') == 2  # each room opens in a new tab, this page stays
    assert meet.rooms(c)[0]["about"] == "Tuesdays at 7 <b>PM</b>" and "Tuesdays at 7 &lt;b&gt;PM&lt;/b&gt;" in p.text  # typed, never markup
    # a room of one's own: its opener hosts it, someone else opening it just joins
    r = as_("n0call").post("/meet", data={"name": "Antenna Project!"}, follow_redirects=False)
    assert r.headers["location"] == "/meet/antenna-project"
    t = claims(as_("n0call").get("/meet/antenna-project", follow_redirects=False).headers["location"])
    assert t["moderator"] is True and t["context"]["room"]["club"]["last_host_leaves"] == "guests"
    as_("pres").post("/meet", data={"name": "antenna project"})
    assert meet.opener("antenna-project") == "n0call"
    assert "<h2>Antenna Project!</h2>" in as_("n0call").get("/meet").text  # its card stays on the opener's page, by the name typed
    assert "antenna-project" not in as_("pres").get("/meet").text  # and no one else's
    assert 'action="/meet" target="_blank"' in as_("n0call").get("/meet").text  # Open it: a new tab, like Join
    (clubmod.DATA / "meet-rooms.json").write_text("")  # damaged (power lost mid-save): nobody's the opener, nothing breaks
    assert meet.opener("antenna-project") is None and as_("n0call").get("/meet/antenna-project", follow_redirects=False).status_code == 303
    from club_minutes import callsign
    meet.open_room("n0call", "n0call")  # a room named after its opener's callsign: a callsign change renames only the opener
    callsign.rename_id("n0call", "w0demo", clubmod.DATA)
    assert meet.opener("n0call") == "w0demo"
    r = as_("n0call").get("/meet/CLUB", follow_redirects=False)  # capitals can't dodge a named room's rules
    assert r.headers["location"].startswith("https://meet.example.org/club?jwt=") and claims(r.headers["location"])["context"]["room"]["club"]["last_host_leaves"] == "end"
    for i in range(5):
        as_("pres").post("/meet", data={"name": f"pres room {i}"})
    assert "error=" in as_("pres").post("/meet", data={"name": "one too many"}, follow_redirects=False).headers["location"]
    assert as_("pres").post("/meet", data={"name": "pres room 0"}, follow_redirects=False).headers["location"] == "/meet/pres-room-0"  # their own: fine
    assert "error=" in as_("n0call").post("/meet", data={"name": "Board"}, follow_redirects=False).headers["location"]  # a club room's name
    # what's refused on Site → Video
    for bad in ({"rows": "1", "name0": "X", "slug0": "a b"}, {"rows": "2", "name0": "A", "slug0": "a", "name1": "B", "slug1": "a"},
                {"rows": "1", "name0": ""}):
        assert "error=" in as_("pres").post("/admin/site/video", data=bad, follow_redirects=False).headers["location"]
    # a room's Delete: that room alone; nothing else on the page is saved with it
    before = meet.rooms(c)
    as_("pres").post("/admin/site/video", data={"delete": "board", "rows": "2", "name0": "Not saved", "slug0": "club"})
    assert meet.rooms(c) == [r for r in before if r["slug"] != "board"] and meet.adhoc(c) is not None
    for gone in ("board", "club"):  # already gone; the last room
        assert "error=" in as_("pres").post("/admin/site/video", data={"delete": gone}, follow_redirects=False).headers["location"]
    as_("pres").post("/admin/site/video", data={"rows": "2", **room, "adhoc": "1", "guests_a": "1", "leaves_a": "guests"})  # both back
    form = as_("pres").get("/calendar/edit").text  # the rooms: buttons that fill in the online link, and the datalist
    assert 'data-url="http://testserver/meet/board">Board</button>' in form and 'list="video-rooms"' in form
    # ad-hoc off again: an unlisted room is gone, and so is the form
    as_("pres").post("/admin/site/video", data={"rows": "2", **room})
    assert as_("n0call").get("/meet/antenna-project", follow_redirects=False).status_code == 404
    assert as_("n0call").post("/meet", data={"name": "x"}, follow_redirects=False).status_code == 404
    c.modules["jitsi"] = False
    assert as_("pres").get("/admin/site/video").status_code == 404
    assert as_("pres").post("/admin/site/video", data={"rows": "0"}).status_code == 404


def test_signed_out_odd_rooms_and_off(env, monkeypatch):
    c, as_ = env
    r = as_(None).get("/meet/club", follow_redirects=False)  # a room guests may join: sign in (then back), or join as a guest
    assert r.status_code == 200 and "/login?next=/meet/club" in r.text and 'href="https://meet.example.org/club"' in r.text
    c.yaml["jitsi"]["rooms"] = [{"slug": "club", "name": "Club meeting", "about": "Board business first"}]
    assert "Board business first" not in as_(None).get("/meet/club").text  # the description is for members
    assert as_(None).get("/meet/nobodys-room", follow_redirects=False).headers["location"] == "/login?next=/meet/nobodys-room"
    assert as_("n0call").get("/meet/..%2Fadmin", follow_redirects=False).status_code == 404
    assert as_("n0call").get("/meet/" + "x" * 65, follow_redirects=False).status_code == 404
    monkeypatch.delenv("CLUB_MINUTES_JITSI_SECRET")
    assert as_("pres").get("/meet/club", follow_redirects=False).status_code == 404  # not set up: as if it weren't there
    monkeypatch.setenv("CLUB_MINUTES_JITSI_SECRET", "too-short-to-be-safe")  # under 32: one captured token could crack it
    assert as_("pres").get("/meet/club", follow_redirects=False).status_code == 404
    monkeypatch.setenv("CLUB_MINUTES_JITSI_SECRET", "x" * 40)
    c.modules["jitsi"] = False
    assert as_("pres").get("/meet", follow_redirects=False).status_code == 404


def test_host_permission_only_shown_while_on_and_kept(env, monkeypatch):
    """Site → Roles shows "host video meetings" only while Jitsi is on; a save while it's off keeps it as it was."""
    c, as_ = env
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c.save_app_admins({"pres"})
    assert 'value="meet_host"' in as_("pres").get("/admin/site/roles").text
    c.modules["jitsi"] = False
    assert 'value="meet_host"' not in as_("pres").get("/admin/site/roles").text
    form = {"key": list(c.role_table())}
    for k, r in c.role_table().items():
        form |= {f"name.{k}": r["name"], f"perms.{k}": [p for p in r["perms"] if p != "meet_host"], f"offices.{k}": r["offices"]}
    as_("pres").post("/admin/site/roles", data=form | {"perms.officer": ["lead", "calendar", "chow", "contacts", "oversee", "moderate", "treasurer"]})
    assert "meet_host" in c.role_table()["officer"]["perms"] and "meet_host" in c.role_table()["minutes_editor"]["perms"]


def test_rooms_for_some_roles(env, monkeypatch):
    """A room can let only some roles straight in; other members knock (or can't join), and only those let in host."""
    c, as_ = env
    board = {"slug": "board", "name": "Board", "roles": ["officer"], "others": "knock"}
    c.save_site_settings({("jitsi", "rooms"): [{"slug": "club", "name": "Club meeting"}, board]})
    assert "officer" in c.roles("pres") and not c.roles("n0call")
    t = claims(as_("pres").get("/meet/board", follow_redirects=False).headers["location"])
    assert t["context"]["user"]["lobby_bypass"] is True and t["moderator"] is True
    t = claims(as_("n0call").get("/meet/board", follow_redirects=False).headers["location"])
    assert t["context"]["user"]["lobby_bypass"] is False and t["moderator"] is False  # knocks, like a guest
    assert "You wait in the lobby" in as_("n0call").get("/meet").text
    c.save_site_settings({("jitsi", "rooms"): [{"slug": "club", "name": "Club meeting"}, board | {"others": "no"}]})
    r = as_("n0call").get("/meet/board", follow_redirects=False)
    assert r.status_code == 403 and "Officer" in r.text
    p = as_("n0call").get("/meet").text  # Board hidden: the one room left, as a card
    assert 'aria-label="Join Club meeting' in p and "Board" not in p
    c.save_site_settings({("jitsi", "rooms"): [{"slug": "club", "name": "Club meeting"}, board | {"others": "no"},
                                                  {"slug": "exec", "name": "Executive", "roles": ["officer"], "others": "no"}]})
    assert as_("pres").get("/meet").text.count('aria-label="Join ') == 3  # an officer: all three
    c.save_site_settings({("jitsi", "rooms"): [board | {"others": "no"}, {"slug": "exec", "name": "Exec", "roles": ["officer"], "others": "no"}]})
    p = as_("n0call").get("/meet").text  # none they can join: the page says so, no room names
    assert "No rooms you can join" in p and "Board" not in p and "Exec" not in p
    c.save_site_settings({("jitsi", "rooms"): [{"slug": "club", "name": "Club meeting"}, board | {"roles": ["gone"]}]})
    assert as_("pres").get("/meet/board", follow_redirects=False).status_code == 303  # a removed role: nobody straight in,
    assert claims(as_("pres").get("/meet/board", follow_redirects=False).headers["location"])["moderator"] is False  # not everyone
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c.save_app_admins({"pres"})
    as_("pres").post("/admin/site/video", data={"rows": "2", "name0": "Club meeting", "slug0": "club", "name1": "Board", "slug1": "board"})
    assert meet.rooms(c)[1]["roles"] == ["gone"]  # a save of the tab doesn't open it to everyone
    c.save_site_settings({("jitsi", "rooms"): None, ("jitsi", "adhoc"): {"on": True}})
    assert "Join Club meeting" in as_("n0call").get("/meet").text  # club.yaml's one room, with own rooms on: the page


def test_turned_on_from_site_features(env, monkeypatch):
    """Site → Features: Jitsi on or off, the server's address and the app ID (the secret stays in the server's env);
    the menu shows Video meeting only once it all works."""
    c, as_ = env
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c.save_app_admins({"pres"})
    c.yaml = {**c.yaml, "modules": {k: v for k, v in c.yaml["modules"].items() if k != "jitsi"}}
    c.yaml.pop("jitsi")
    c.save_site_settings({})
    assert not c.has("jitsi") and meet.settings(c) is None and 'href="/meet"' not in as_("n0call").get("/").text
    page = as_("pres").get("/admin/site/features").text
    assert 'href="https://github.com/sponsors/jflattery"' in page and 'href="https://ko-fi.com/jflattery"' in page  # the support tick says where
    assert 'name="jitsi_domain"' in page and "set in this server's env file" in page
    r = as_("pres").post("/admin/site/features", data={"jitsi": "1", "jitsi_domain": "", "jitsi_app_id": "x"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and not c.has("jitsi")  # on, but where?
    r = as_("pres").post("/admin/site/features", data={"jitsi": "1", "jitsi_domain": "meet.example.org/<b>", "jitsi_app_id": "x"}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    as_("pres").post("/admin/site/features", data={"jitsi": "1", "jitsi_domain": "https://meet.example.org/", "jitsi_app_id": "club-minutes", "supports_app": "1"})
    assert c.info.get("supports_app") is True and 'class="kofi"' not in as_("n0call").get("/").text  # the club supports the app
    assert meet.settings(c) == {"domain": "meet.example.org", "app_id": "club-minutes", "room": "club"}
    assert 'href="/meet"' in as_("n0call").get("/").text and as_("pres").get("/admin/site/video").status_code == 200
    monkeypatch.setenv("CLUB_MINUTES_JITSI_SECRET", "")  # on, but the server's secret missing: no menu link, and the tab says so
    assert 'href="/meet"' not in as_("n0call").get("/").text and "Not working yet" in as_("pres").get("/admin/site/features").text
    as_("pres").post("/admin/site/features", data={"jitsi_domain": "meet.example.org", "jitsi_app_id": "club-minutes"})
    assert not c.has("jitsi")  # unticked: off, the address kept


def test_secret_made_on_site_features(env, monkeypatch):
    """Admins can make the secret on Site → Features: shown once, kept for the app only, never shown again; the
    server's env, if it sets one, wins and the button isn't there."""
    import stat
    c, as_ = env
    monkeypatch.delenv("CLUB_MINUTES_JITSI_SECRET")
    assert as_("pres").post("/admin/site/jitsi-secret", follow_redirects=False).status_code == 404  # not in admin mode
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c.save_app_admins({"pres"})
    assert as_("n0call").post("/admin/site/jitsi-secret").status_code == 404  # not an admin
    page = as_("pres").get("/admin/site/features").text
    assert "Not working yet" in page and ">Make a secret<" in page and meet.settings(c) is None
    r = as_("pres").post("/admin/site/jitsi-secret")
    value = meet.secret()
    assert len(value) == 64 and value in r.text and r.headers["cache-control"] == "no-store"
    assert stat.S_IMODE((clubmod.DATA / "jitsi-secret").stat().st_mode) == 0o600
    page = as_("pres").get("/admin/site/features").text
    assert value not in page and "Make a new secret" in page and meet.settings(c)["domain"] == "meet.example.org"
    as_("pres").post("/admin/site/jitsi-secret")
    assert meet.secret() != value  # a new one replaces it
    monkeypatch.setenv("CLUB_MINUTES_JITSI_SECRET", "y" * 40)
    assert meet.secret() == "y" * 40 and 'form="jitsi-secret"' not in as_("pres").get("/admin/site/features").text
    assert "error=" in as_("pres").post("/admin/site/jitsi-secret", follow_redirects=False).headers["location"]


def test_a_damaged_settings_file_breaks_no_page(env):
    """Every page reads the member's settings (the support link): a torn settings.json means defaults, not a 500."""
    from club_minutes import prefs
    _, as_ = env
    (prefs.DATA / "settings.json").write_text('{"n0call": {"supp')
    assert as_("n0call").get("/").status_code == 200


def test_guests_sent_to_sign_in_and_own_rooms_expire_when_unused(env):
    c, as_ = env
    c.yaml["jitsi"]["adhoc"] = {"on": True, "guests": False}
    as_("n0call").post("/meet", data={"name": "Board"})
    for room in ("board", "no-such-room"):  # signed out, a room that lets no guests in reads like one that doesn't exist
        r = as_(None).get(f"/meet/{room}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/login?next=/meet/{room}"
    as_("n0call").post("/meet", data={"name": "Net night"})
    d = json.loads((clubmod.DATA / "meet-rooms.json").read_text())
    d["room:net-night"]["at"] -= 23 * 3600  # almost lapsed: a join keeps it
    (clubmod.DATA / "meet-rooms.json").write_text(json.dumps(d))
    as_("pres").get("/meet/net-night", follow_redirects=False)
    assert meet.opener("net-night") == "n0call"
    d["room:net-night"]["at"] -= 25 * 3600  # a day with no join: free to take
    (clubmod.DATA / "meet-rooms.json").write_text(json.dumps(d))
    assert meet.opener("net-night") is None
    c.yaml["jitsi"]["adhoc"] = {"on": True, "guests": True}  # an own room that lets guests in: the guest page while it lasts
    as_("n0call").post("/meet", data={"name": "Net night"})
    assert 'href="https://meet.example.org/net-night"' in as_(None).get("/meet/net-night").text


def test_menu_marks_video_meeting_only_on_its_own_pages(env, tmp_path, monkeypatch):
    """/meetings/… starts with /meet too: the menu marks Minutes there, not Video meeting."""
    c, as_ = env
    Meeting(date=dt.date(2026, 10, 15), status="published", sections={}).save(tmp_path / "m")
    monkeypatch.setattr(web, "archive", Archive(tmp_path / "m", c))
    assert '<a href="/meet" class="on" aria-current="page">' in as_("n0call").get("/meet").text
    page = as_("n0call").get("/meetings/2026-10-15").text
    assert '<a href="/meet" class="">' in page and '<a href="/minutes" class="on">' in page


def test_a_bad_domain_in_club_yaml_is_not_set(env):
    c = web.archive.club
    assert meet.settings(c)["domain"] == "meet.example.org"
    c.yaml = {**c.yaml, "jitsi": {**c.yaml["jitsi"], "domain": "x.org; form-action *"}}
    assert meet.settings(c) is None
