"""Profile edits: values-only writes to the roster sheet, change log, undo, Secretary notice, sharing."""
import json
import re
from pathlib import Path

import pytest

from club_minutes import profile, roster
from club_minutes.club import Club
from club_minutes.roster import Sheet, SheetError, _col, _month_day

FIX = Path(__file__).parent / "fixtures"
HEADER = ["callsign", "full_name", "email", "Phone", "Address", "Title(s)", "Status", "birthday", "share"]


class FakeResponse:
    def __init__(self, data=None, status=200):
        self.data, self.status_code = data or {}, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSheets:
    """Stands in for the Google Sheets API: a grid of values, and a record of every write."""

    def __init__(self, rows, writable=True):
        self.rows, self.writable, self.writes = [list(r) for r in rows], writable, []

    def get(self, url, params=None):
        return FakeResponse({"values": self.rows})

    def post(self, url, json=None):
        if not self.writable:
            return FakeResponse(status=403)
        assert url.endswith(":batchUpdate") and json["valueInputOption"] == "RAW"
        for d in json["data"]:
            a1 = d["range"].split("!")[1]
            col = ord(a1[0]) - 65
            row = int(a1[1:]) - 1
            self.rows[row] += [""] * (col + 1 - len(self.rows[row]))
            self.rows[row][col] = d["values"][0][0]
            self.writes.append((a1, d["values"][0][0]))
        return FakeResponse()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "DATA", tmp_path)
    monkeypatch.setattr(profile, "ROSTER_CACHE", tmp_path / "roster.json")
    sent = []
    monkeypatch.setattr(profile.auth, "send_email", lambda to, subject, body, **kw: sent.append((to, subject, body, kw)))
    people = [{"id": "n0aaaa", "first": "Jane", "last": "Doe", "callsign": "N0AAAA", "email": "jane@example.org",
               "role": "Secretary", "officer": True},
              {"id": "n0cccc", "first": "Bob", "last": "Member", "callsign": "N0CCCC", "email": "bob@example.org",
               "phone": "419-555-0199"}]
    (tmp_path / "roster.json").write_text(json.dumps(people))
    club = Club(FIX / "test-club.yaml", roster=people)
    fake = FakeSheets([HEADER,
                       ["N0AAAA", "Jane Doe", "jane@example.org", "", "", "Secretary", "Active", "", ""],
                       ["N0CCCC", "Bob Member", "bob@example.org", "419-555-0199", "", "", "Active", "", ""]])
    return club, Sheet(fake), fake, sent


def test_change_writes_only_changed_cells_and_tells_the_secretary(env):
    club, sheet, fake, sent = env
    e = profile.change(club, "n0cccc", {"phone": "419-555-0199", "address": "9 Oak Ave", "birthday": "07-04",
                                        "share": "phone, birthday"}, by="n0cccc", sheet=sheet, url="https://x")
    assert sorted(fake.writes) == [("E3", "9 Oak Ave"), ("H3", "07-04"), ("I3", "phone, birthday")]  # phone unchanged
    assert e["old"] == {"address": "", "birthday": "", "share": ""}
    m = club.members["n0cccc"]
    assert (m.address, m.birthday, m.share) == ("9 Oak Ave", "07-04", ["phone", "birthday"])
    cached = {r["id"]: r for r in json.loads((profile.ROSTER_CACHE).read_text())}
    assert cached["n0cccc"]["address"] == "9 Oak Ave"
    (to, subject, body, kw), (to2, _subject2, body2, kw2) = sent
    assert to == ["jane@example.org"] and "Bob Member" in subject and "9 Oak Ave" in body and "https://x/roster/changes" in body
    assert to2 == ["bob@example.org"] and "You updated" in body2 and "9 Oak Ave" in body2  # the member is told too
    assert "only_to" in kw and "only_to" in kw2  # email test mode applies
    assert profile.change(club, "n0cccc", {"address": "9 Oak Ave"}, by="n0cccc", sheet=sheet) is None  # no-op


def test_only_profile_fields_can_be_changed(env):
    club, sheet, fake, _ = env
    profile.change(club, "n0cccc", {"status": "Inactive", "full_name": "Hacker", "phone": "1"}, by="n0cccc", sheet=sheet)
    assert fake.writes == [("D3", "1")]


def test_undo_restores_and_refuses_if_changed_again(env):
    club, sheet, fake, _sent = env
    e = profile.change(club, "n0cccc", {"address": "9 Oak Ave"}, by="n0cccc", sheet=sheet)
    profile.undo(club, e["id"], by="n0aaaa", sheet=sheet)
    assert fake.rows[2][4] == "" and club.members["n0cccc"].address is None
    assert [x["undone"] for x in profile.history()] == [False, True]
    with pytest.raises(ValueError):
        profile.undo(club, e["id"], by="n0aaaa", sheet=sheet)  # only once
    e2 = profile.change(club, "n0cccc", {"address": "1 New Rd"}, by="n0cccc", sheet=sheet)
    profile.change(club, "n0cccc", {"address": "2 Newer Rd"}, by="n0cccc", sheet=sheet)
    with pytest.raises(ValueError):
        profile.undo(club, e2["id"], by="n0aaaa", sheet=sheet)  # would overwrite a newer change


def test_sheet_problems_change_nothing(env):
    club, _, fake, sent = env
    old = FakeSheets([HEADER[:7]] + [r[:7] for r in fake.rows[1:]])
    with pytest.raises(SheetError, match="birthday"):
        profile.change(club, "n0cccc", {"birthday": "07-04"}, by="n0cccc", sheet=Sheet(old))
    assert club.members["n0cccc"].address is None and profile.history() == [] and sent == []


def test_helpers():
    assert [_col(i) for i in (0, 25, 26, 27)] == ["A", "Z", "AA", "AB"]
    assert _month_day("07-04") == "07-04" and _month_day("1961-07-04") == "07-04" and _month_day("") is None
    assert _month_day("25001") == "06-12"  # a Sheets date serial: the year is dropped
    assert _month_day("07") == "07" and _month_day("7") == "07"  # month only


def test_can_see():
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "o", "first": "O", "last": "Officer", "role": "President", "officer": True},
        {"id": "a", "first": "A", "last": "Member", "share": ["phone"]},
        {"id": "b", "first": "B", "last": "Member"}])
    assert club.can_see("b", "a", "phone") and not club.can_see("b", "a", "address")
    assert club.can_see("o", "a", "address") and club.can_see("a", "a", "address")
    assert not club.can_see(None, "a", "phone")


def test_email_change_needs_confirmation(tmp_path, monkeypatch):
    from club_minutes import auth as auth_mod
    sent = []
    monkeypatch.setattr(auth_mod, "send_email", lambda to, subject, body, **kw: sent.append((to, body)))
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "n0cccc", "first": "Bob", "last": "Member", "email": "bob@example.org"},
        {"id": "n0aaaa", "first": "Jane", "last": "Doe", "email": "jane@example.org"}])
    a = auth_mod.Auth(club, tmp_path / "auth.sqlite")
    assert a.start_email_change("n0cccc", "jane@example.org") is None and not sent  # the same reply; nothing sent
    assert a.start_email_change("n0cccc", "not-an-email")
    assert a.start_email_change("n0cccc", "bob@new.example", base_url="https://x") is None
    (to, body), = sent
    assert "Bob" not in body
    token = body.split("/email/confirm?t=")[1].split()[0]  # 1.0 sent /profile/email?t=, which was the Email page
    assert to == "bob@new.example"
    assert a.confirm_email_change(token) == ("n0cccc", "bob@new.example", "n0cccc")
    assert a.confirm_email_change(token) is None  # one use


def test_keeper_email_change_names_nobody_to_the_new_address(tmp_path, monkeypatch):
    from club_minutes import auth as auth_mod
    sent = []
    monkeypatch.setattr(auth_mod, "send_email", lambda to, subject, body, **kw: sent.append((to, body)))
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "n0cccc", "first": "Bob", "last": "Member", "email": "bob@example.org"},
        {"id": "n0aaaa", "first": "Jane", "last": "Doe", "email": "jane@example.org"}])
    auth_mod.Auth(club, tmp_path / "auth.sqlite").start_email_change("n0cccc", "typo@example.com", by="n0aaaa")
    got = dict(sent)
    assert not any(n in got["typo@example.com"] for n in ("Bob", "Jane", "Doe"))
    assert "Jane Doe" in got["bob@example.org"] and "typo@example.com" in got["bob@example.org"]  # the old address is told


def test_an_address_in_use_counts_toward_the_hourly_limit_like_any_other(tmp_path, monkeypatch):
    """Otherwise a member could tell a member's address from a stranger's by when "Too many tries" starts."""
    from club_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "send_email", lambda *a, **kw: None)
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "n0cccc", "first": "Bob", "last": "Member", "email": "bob@example.org"},
        {"id": "n0aaaa", "first": "Jane", "last": "Doe", "email": "jane@example.org"}])
    a, b = (auth_mod.Auth(club, tmp_path / f"{n}.sqlite") for n in "ab")
    for _ in range(auth_mod.EMAILS_PER_HOUR):
        assert a.start_email_change("n0cccc", "jane@example.org") is None and b.start_email_change("n0cccc", "x@example.com") is None
    assert a.start_email_change("n0cccc", "jane@example.org") == b.start_email_change("n0cccc", "x@example.com") == "Too many tries; please wait an hour."


def test_email_log_keeps_90_days(tmp_path, monkeypatch):
    import time

    from club_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "DATA", tmp_path)
    old = int(time.time()) - 91 * 86400
    (tmp_path / "email-log.jsonl").write_text(json.dumps({"at": old, "subject": "old"}) + "\n" + json.dumps({"at": old + 2 * 86400, "subject": "89 days"}) + "\n{torn\n5\nnull\n")
    auth_mod._email_log({"subject": "new"})
    assert [json.loads(x)["subject"] for x in (tmp_path / "email-log.jsonl").read_text().splitlines()] == ["89 days", "new"]


def test_monthday_filter():
    from club_minutes.web import monthday
    assert monthday("07-04") == "July 4" and monthday("") == "" and monthday(None) == ""
    assert monthday("1961-07-04") == "1961-07-04" and monthday("13-40") == "13-40"
    assert monthday("07") == "July" and monthday("13") == "13"


def test_club_reloads_roster_after_a_sync(tmp_path, monkeypatch):
    import os

    from club_minutes import club as club_mod
    cache = tmp_path / "roster.json"
    cache.write_text(json.dumps([{"id": "n0cccc", "first": "Bob", "last": "Member"}]))
    monkeypatch.setattr(club_mod, "ROSTER_CACHE", cache)
    cfg = tmp_path / "club.yaml"
    cfg.write_text("club: {name: T}\n")
    c = club_mod.Club(cfg)
    assert c.members["n0cccc"].address is None and not c.refresh()
    cache.write_text(json.dumps([{"id": "n0cccc", "first": "Bob", "last": "Member", "address": "9 Oak Ave"}]))
    os.utime(cache, (1, 2))  # a different mtime, as a sync would give
    assert c.refresh() and c.members["n0cccc"].address == "9 Oak Ave"


def test_multiline_addresses_keep_their_line_breaks(env, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.store import Archive
    club, sheet, fake, sent = env
    assert web.clean_address(" 100 Main St  #12 \r\n\r\nAnytown, OH 44000 ") == "100 Main St #12\nAnytown, OH 44000"
    fake.rows[2][4] = "100 Main St #12 \nAnytown, OH 44000"  # as typed in the sheet, trailing space included
    club.members["n0cccc"].address = fake.rows[2][4]
    monkeypatch.setattr(web, "archive", Archive(root=profile.DATA, club=club))  # an empty archive in tmp_path
    monkeypatch.setattr(web.authn, "member_for", lambda token: "n0cccc")
    monkeypatch.setattr(web.profile, "Sheet", lambda: sheet)
    c = TestClient(web.app)
    page = c.get("/profile").text
    assert "<textarea" in page and "#12 \nAnytown" in page  # shown on two lines
    form = {"email": "bob@example.org", "phone": "419-555-0123", "address": "100 Main St #12\r\nAnytown, OH 44000",
            "bmonth": "", "bday": ""}
    assert c.post("/profile", data=form, follow_redirects=False).status_code == 303
    assert fake.writes == [("D3", "419-555-0123")]  # the untouched address isn't rewritten
    form["address"] = "100 Main St #13\r\nAnytown, OH 44000"
    c.post("/profile", data=form, follow_redirects=False)
    assert fake.rows[2][4] == "100 Main St #13\nAnytown, OH 44000"
    assert "#13 / Anytown" in sent[-1][2]  # readable in the Secretary's email
    form["bmonth"] = "7"  # just a month
    c.post("/profile", data=form, follow_redirects=False)
    assert ("H3", "07") in fake.writes
    form.update(bmonth="", bday="4")  # a day with no month
    assert "error=" in c.post("/profile", data=form, follow_redirects=False).headers["location"]


def test_preview_mode_never_sends_or_touches_the_real_sheet(tmp_path, monkeypatch):
    from club_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "PREVIEW", "some-branch")
    monkeypatch.setattr(auth_mod, "DATA", tmp_path)
    monkeypatch.setattr(auth_mod.smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError("SMTP used")))
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "u")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "p")
    assert auth_mod.send_email(["a@example.org", "b@example.org"], "Hi", "body", only_to={"a@example.org"}) == 1
    e = json.loads((tmp_path / "preview-outbox.jsonl").read_text())
    assert e["to"] == ["a@example.org"] and e["withheld"] == ["b@example.org"] and e["subject"] == "Hi"

    monkeypatch.setattr(roster, "PREVIEW", "some-branch")
    monkeypatch.setattr(roster, "DATA", tmp_path)
    monkeypatch.setattr(roster, "CACHE", tmp_path / "roster.json")
    (tmp_path / "roster.json").write_text(json.dumps([{"id": "n0cccc", "first": "Bob", "last": "Member", "callsign": "N0CCCC"}]))
    sheet = Sheet()
    assert sheet.update_member("n0cccc", {"phone": "419-555-0111"}) == {"phone": ""}
    assert "419-555-0111" in (tmp_path / "preview-sheet.json").read_text()


def test_email_change_alerts_old_and_new_address(env):
    club, sheet, _fake, sent = env
    profile.change(club, "n0cccc", {"email": "bob@new.example"}, by="n0cccc", sheet=sheet)
    assert sent[-1][0] == ["bob@new.example", "bob@example.org"]


def test_admin_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    from club_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "n0admn", "first": "Ann", "last": "Admin", "callsign": "N0ADMN", "email": "ann@example.org"},
        {"id": "n0cccc", "first": "Bob", "last": "Member", "phone": "419-555-0199"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("n0admn"))
    assert "419-555-0199" not in c.get("/members/n0cccc").text          # off by default: an ordinary member
    assert c.get("/roster/changes").status_code == 403
    assert "Turn on admin mode" in c.get("/links").text
    turn_on_admin_mode(c, monkeypatch)
    page = c.get("/members/n0cccc").text
    assert "419-555-0199" in page and "Admin mode is on" in page          # on: officer-level view
    assert c.get("/roster/changes").status_code == 200
    c.post("/admin/mode", data={"on": 0})
    assert c.get("/roster/changes").status_code == 403
    log = [json.loads(x) for x in (tmp_path / "admin-log.jsonl").read_text().splitlines()]
    assert [(e["on"], e.get("confirmed_by")) for e in log if "on" in e] == [(True, "email"), (False, None)]

    monkeypatch.setattr(web, "PREVIEW", "some-branch")
    assert c.get("/admin/email", follow_redirects=False).headers["location"].startswith("/admin/confirm")  # admin mode first
    assert "Email log" not in c.get("/links").text and "Email outbox" not in c.get("/links").text

    c.cookies.set(web.COOKIE, a.new_session("n0cccc"))                    # not an admin: can't turn it on
    assert c.post("/admin/mode", data={"on": 1}).status_code == 404                # as if it didn't exist
    assert c.get("/admin/confirm").status_code == 404 and c.post("/admin/confirm/email").status_code == 404
    assert "Turn on admin mode" not in c.get("/links").text
    assert c.get("/admin/email").status_code == 404
    assert "Sign-in &amp; passkeys" not in c.get("/links").text           # it's a tab of Your profile now
    assert "Sign-in &amp; passkeys" in c.get("/profile").text and "Your details" in c.get("/account").text


def turn_on_admin_mode(c, monkeypatch):
    """Admin mode asks the admin to confirm it's them first: here (no passkey, not the passkey host) by email."""
    from urllib.parse import urlparse

    from club_minutes import web
    sent = []
    monkeypatch.setattr(web.authmod, "send_email", lambda to, subject, body, **kw: sent.append((to, body)))
    r = c.post("/admin/mode", data={"on": 1, "next": "/links"}, follow_redirects=False)
    assert r.headers["location"].startswith("/admin/confirm")
    assert "Admin mode is on" not in c.get("/links").text                       # not until confirmed
    assert "Email me a confirmation link" in c.get(r.headers["location"]).text
    c.post("/admin/confirm/email", data={"next": "/links"})
    assert sent and sent[-1][0] == "ann@example.org"
    link = urlparse(next(w for w in sent[-1][1].split() if "/admin/confirm/link" in w))
    assert c.get("/admin/confirm/link?t=wrong").url.path == "/admin/confirm"    # a made-up code does nothing
    assert "Admin mode is on" in c.get(f"{link.path}?{link.query}").text
    assert "expired" in c.get(f"{link.path}?{link.query}").text                 # and it works once


def test_view_as_is_read_only_and_needs_admin_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    from club_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "n0admn", "first": "Ann", "last": "Admin", "callsign": "N0ADMN", "email": "ann@example.org"},
        {"id": "tess", "first": "Tess", "last": "Treasurer", "role": "Treasurer", "officer": True, "phone": "419-555-0142"},
        {"id": "bob", "first": "Bob", "last": "Member", "phone": "419-555-0199"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("n0admn"))
    assert c.post("/admin/view-as", data={"member": "bob"}).status_code == 403   # admin mode is off
    turn_on_admin_mode(c, monkeypatch)
    assert "View the app as Bob" in c.get("/members/bob").text
    c.post("/admin/view-as", data={"member": "bob", "next": "/links"})
    page = c.get("/members/tess").text
    assert "Viewing the app as <b>Bob Member</b>" in page
    assert "419-555-0142" not in page                     # Bob can't see Tess's unshared phone, so neither can Ann now
    assert ">Bob</span>" in c.get("/links").text and ">Ann</span>" not in c.get("/links").text  # the name menu is Bob's
    assert c.post("/profile", data={"phone": "1"}).status_code == 403          # read-only
    assert c.get("/account").status_code == 403                                 # not their sign-ins or passkeys
    assert c.get("/roster/changes").status_code == 403                          # Bob's permissions, not admin's
    c.post("/admin/view-as", data={"member": ""})
    page = c.get("/members/tess").text
    assert "Viewing the app as" not in page and "419-555-0142" in page          # back to admin mode
    c.post("/admin/view-as", data={"member": "tess"})
    c.post("/admin/mode", data={"on": 0})                                        # ending admin mode ends viewing as
    assert "Viewing the app as" not in c.get("/links").text
    log = [json.loads(x) for x in (tmp_path / "admin-log.jsonl").read_text().splitlines()]
    assert [e.get("view_as") for e in log if "view_as" in e] == ["bob", None, "tess"]


def test_act_as_makes_changes_as_the_member_on_a_test_server_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import prefs, web
    from club_minutes.auth import Auth
    from club_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "n0admn", "first": "Ann", "last": "Admin", "callsign": "N0ADMN", "email": "ann@example.org"},
        {"id": "bob", "first": "Bob", "last": "Member"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(prefs, "DATA", tmp_path)
    monkeypatch.setattr(web, "STAGE", False)
    monkeypatch.setattr(web, "PREVIEW", None)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("n0admn"))
    send_email = web.authmod.send_email                # the real one (admin mode's sign-in replaces it)
    turn_on_admin_mode(c, monkeypatch)
    assert "Act as Bob" not in c.get("/members/bob").text                       # the live app: view only
    assert c.post("/admin/view-as", data={"member": "bob", "act": "1"}).status_code == 403
    monkeypatch.setattr(web, "STAGE", True)
    assert "Act as Bob" in c.get("/members/bob").text
    c.post("/admin/view-as", data={"member": "bob", "act": "1"})
    assert "Acting as <b>Bob Member</b>" in c.get("/links").text
    assert c.post("/profile/settings", data={"week_start": "monday"}, follow_redirects=False).status_code == 303
    assert prefs.settings("bob")["week_start"] == "monday" and prefs.settings("n0admn")["week_start"] == "sunday"
    assert c.get("/account").status_code == 403 and c.post("/logout").status_code == 403   # their sign-ins stay theirs
    assert c.get("/calendar/subscribe").status_code == 403                      # nor their private feed address
    logged = []                                     # acting on stage: even a sign-in link reaches only the admin
    monkeypatch.setattr(web.authmod, "PREVIEW", None)
    monkeypatch.setattr(web.authmod, "STAGE", True)
    monkeypatch.setattr(web.authmod, "_email_log", logged.append)
    monkeypatch.delenv("CLUB_MINUTES_SMTP_USER", raising=False)
    actor, acting = web.authmod.ACTOR.set("ann@example.org"), web.authmod.ACTING.set(True)
    try:
        send_email("bob@example.org", "Your sign-in link", "…", to_self=True)
    finally:
        web.authmod.ACTOR.reset(actor)
        web.authmod.ACTING.reset(acting)
    assert logged[0]["to"] == ["ann@example.org"] and logged[0]["withheld"] == ["bob@example.org"]
    monkeypatch.setattr(web, "STAGE", False)                                     # no longer a test server: read-only
    assert c.post("/profile/settings", data={"week_start": "sunday"}).status_code == 403
    monkeypatch.setattr(web, "STAGE", True)
    c.post("/admin/view-as", data={"member": "bob"})                             # plain View as stays read-only on stage
    assert c.post("/profile/settings", data={"week_start": "sunday"}).status_code == 403
    log = [json.loads(x) for x in (tmp_path / "admin-log.jsonl").read_text().splitlines()]
    assert [(e["acted_as"], e["did"]) for e in log if "acted_as" in e] == [("bob", "POST /profile/settings")]
    assert web.admin_action(next(e for e in log if e.get("acts"))) == "Acted as Bob Member"


def test_live_email_log_records_sent_and_held_back(tmp_path, monkeypatch):
    from club_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "PREVIEW", None)
    monkeypatch.setattr(auth_mod, "DATA", tmp_path)
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, m): sent.append(m["To"])
    monkeypatch.setattr(auth_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "club@example.org")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "pw")
    assert auth_mod.send_email(["ann@example.org", "amy@example.org"], "Minutes", "body", only_to={"ann@example.org"}) == 1
    assert auth_mod.send_email("amy@example.org", "Reminder", "body", only_to={"ann@example.org"}) == 0
    log = [json.loads(x) for x in (tmp_path / "email-log.jsonl").read_text().splitlines()]
    assert sent == ["ann@example.org"]
    assert [(e["subject"], e["status"], e["to"], e["withheld"]) for e in log] == [
        ("Minutes", "sent", ["ann@example.org"], ["amy@example.org"]),
        ("Reminder", "held back (test mode)", [], ["amy@example.org"])]
    assert oct((tmp_path / "email-log.jsonl").stat().st_mode)[-3:] == "600"


def test_admins_edit_club_links(tmp_path, monkeypatch):
    """Links come from club.yaml until an admin edits them; each shows on the Links page, the minutes, both or neither."""
    from fastapi.testclient import TestClient

    from club_minutes import club as clubmod
    from club_minutes import web
    from club_minutes.auth import Auth
    from club_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Ann", "last": "Admin", "email": "ann@example.org"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    for mod in (web, clubmod):
        monkeypatch.setattr(mod, "DATA", tmp_path)
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("n0admn"))
    form = {"label": ["Discord", "Net log", ""], "url": ["https://discord.example/x", "https://example.org/net", ""],
            "icon": ["chat", "", ""], "show": ["page", "minutes", "both"]}
    assert c.post("/links", data=form).status_code == 403                  # admin mode first
    turn_on_admin_mode(c, monkeypatch)
    c.post("/links", data=form)
    assert [(x["label"], x["page"], x["minutes"], x.get("icon")) for x in club.links] == [
        ("Discord", True, False, "chat"), ("Net log", False, True, None)]
    page = c.get("/links").text
    assert "discord.example" in page and "example.org/net" not in page
    bad = c.post("/links", data={**form, "url": ["javascript:alert(1)", "", ""]}, follow_redirects=False)
    assert "error=" in bad.headers["location"] and len(club.links) == 2      # rejected, nothing changed
    # brand logos (Simple Icons): searched by name, saved as si:slug, drawn on the Links page; made-up ones are dropped
    hits = c.get("/links/icons?q=Home Assistant").json()
    assert hits[0]["name"] == "si:homeassistant" and hits[0]["title"] == "Home Assistant" and hits[0]["path"].startswith("M")
    assert c.get("/links/icons?q=h").json() == []  # too short
    c.post("/links", data={"label": ["Home Assistant", "Odd"], "url": ["https://ha.example.org", "https://x.example.org"],
                           "icon": ["si:homeassistant", "si:not-a-logo"], "show": ["both", "both"]})
    assert [x.get("icon") for x in club.links] == ["si:homeassistant", None]
    assert web.brand_path("si:homeassistant") in c.get("/links").text
    assert ">Home Assistant</small>" in c.get("/links?edit=1").text  # the picker shows the logo's name


def test_email_log_and_server_log_hide_link_tokens(tmp_path, monkeypatch, caplog):
    """FIXES → problem 3: the Email log (and the server's log, without SMTP) shows sign-in, email-confirm and admin-mode
    links as `t=[hidden]`, so an admin can't open one and be signed in as the member; the email itself keeps the link."""
    from club_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "PREVIEW", None)
    monkeypatch.setattr(auth_mod, "DATA", tmp_path)
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, m): sent.append(m.get_content())
    monkeypatch.setattr(auth_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "club@example.org")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "pw")
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Ann", "last": "Admin", "callsign": "N0ADMN",
                                                  "email": "ann@example.org"}])
    a = auth_mod.Auth(club, tmp_path / "auth.sqlite")
    a.send_link("ann@example.org", base_url="https://club.example.org")                       # sign-in link
    assert a.start_email_change("n0admn", "ann2@example.org", base_url="https://club.example.org") is None  # new email
    step = a.step_up_link("n0admn")                                                            # admin mode (web.py's email)
    auth_mod.send_email("ann@example.org", "Turn on admin mode", f"https://club.example.org/admin/confirm/link?t={step}&next=%2F")
    log = [json.loads(x)["body"] for x in (tmp_path / "email-log.jsonl").read_text().splitlines()]
    assert len(sent) == len(log) == 3
    for body, mail in zip(log, sent, strict=True):
        assert "t=[hidden]" in body and "t=[hidden]" not in mail and re.search(r"[?&]t=[\w-]{20,}", mail)
        assert not re.search(r"[?&]t=[\w-]{20,}", body)
    assert "&next=%2F" in log[2]                                                               # only the token goes
    monkeypatch.delenv("CLUB_MINUTES_SMTP_PASSWORD")                                           # no SMTP: the body goes to the server log
    a.send_link("ann@example.org", base_url="https://club.example.org", ip="192.0.2.1")      # another address: not a quick repeat
    assert "ann@example.org not sent" in caplog.text and "t=" not in caplog.text  # recipients and subject only, never the body


def test_a_quiet_site_prunes_the_email_log_daily(tmp_path, monkeypatch):
    import time

    from club_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "DATA", tmp_path)
    (tmp_path / "email-log.jsonl").write_text(json.dumps({"at": int(time.time()) - 91 * 86400, "subject": "old"}) + "\n")
    auth_mod.prune_email_log()  # what the daily job runs: no email needed
    assert (tmp_path / "email-log.jsonl").read_text() == ""


def test_the_daily_job_trims_the_admin_and_api_logs_to_90_days(tmp_path, monkeypatch):
    import time

    from club_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "DATA", tmp_path)
    new = json.dumps({"at": int(time.time()), "x": "new"})
    for name in ("admin-log.jsonl", "api-log.jsonl"):
        (tmp_path / name).write_text(json.dumps({"at": int(time.time()) - 91 * 86400}) + "\n" + new + "\n")
        auth_mod.prune_old_lines(name)
        assert (tmp_path / name).read_text() == new + "\n"


def test_forget_email_log_removes_a_leaver_alone_or_from_a_list(tmp_path, monkeypatch):
    from club_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "DATA", tmp_path)
    auth_mod._email_log({"to": ["jane@example.org"], "subject": "alone"})
    auth_mod._email_log({"to": ["Jane@example.org", "john@example.org"], "withheld": [], "subject": "minutes",
                         "reply_to": "jane@example.org, john@example.org"})
    auth_mod.forget_email_log({"jane@example.org"})
    log = [json.loads(x) for x in (tmp_path / "email-log.jsonl").read_text().splitlines()]
    assert [(e["subject"], e["to"], e["reply_to"]) for e in log] == [("minutes", ["john@example.org"], "john@example.org")]
