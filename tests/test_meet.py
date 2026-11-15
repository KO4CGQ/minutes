"""Jitsi Meet sign-in (meet.py): off unless club.yaml turns it on; a short-lived HS256 token saying who joins and
whether they host (the meet_host permission), for signed-in members only."""
import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import club as clubmod
from club_minutes import meet, web
from club_minutes.auth import Auth
from club_minutes.club import Club

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
    c.yaml = {**c.yaml, "jitsi": {"domain": "meet.example.org", "app_id": "club-minutes", "room": "club"}}
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
    jwt = location.split("?jwt=")[1]
    head, body, sig = jwt.split(".")
    assert meet.sign(f"{head}.{body}".encode(), b"a-test-secret-not-a-real-one-0123456789") == sig
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_officers_host_members_join(env):
    _c, as_ = env
    r = as_("pres").get("/meet", follow_redirects=False)
    assert r.headers["location"] == "/meet/club"
    r = as_("pres").get("/meet/club", follow_redirects=False)
    assert r.headers["location"].startswith("https://meet.example.org/club?jwt=")
    t = claims(r.headers["location"])
    assert (t["aud"], t["iss"], t["sub"], t["room"], t["moderator"]) == ("jitsi", "club-minutes", "meet.example.org", "club", True)
    assert t["context"]["user"] == {"id": "pres", "name": "Jane Doe", "affiliation": "owner",
                                       "lobby_bypass": True} and t["exp"] - t["iat"] == 300
    t = claims(as_("n0call").get("/meet/net-night", follow_redirects=False).headers["location"])
    assert (t["room"], t["moderator"], t["context"]["user"]["affiliation"]) == ("net-night", False, "member")


def test_signed_out_odd_rooms_and_off(env, monkeypatch):
    c, as_ = env
    r = as_(None).get("/meet/club", follow_redirects=False)
    assert r.headers["location"] == "/login?next=/meet/club"  # sign in, then back
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
