"""Profile edits: values-only writes to the roster sheet, change log, undo, Secretary notice, sharing."""
import json
from pathlib import Path

import pytest

from w8lbz_minutes import profile, roster
from w8lbz_minutes.club import Club
from w8lbz_minutes.roster import Sheet, SheetError, _col, _month_day

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
    people = [{"id": "kd8aaa", "first": "Jane", "last": "Doe", "callsign": "KD8AAA", "email": "jane@example.org",
               "role": "Secretary", "officer": True},
              {"id": "kd8ccc", "first": "Bob", "last": "Member", "callsign": "KD8CCC", "email": "bob@example.org",
               "phone": "419-555-0199"}]
    (tmp_path / "roster.json").write_text(json.dumps(people))
    club = Club(FIX / "test-club.yaml", roster=people)
    fake = FakeSheets([HEADER,
                       ["KD8AAA", "Jane Doe", "jane@example.org", "", "", "Secretary", "Active", "", ""],
                       ["KD8CCC", "Bob Member", "bob@example.org", "419-555-0199", "", "", "Active", "", ""]])
    return club, Sheet(fake), fake, sent


def test_change_writes_only_changed_cells_and_tells_the_secretary(env):
    club, sheet, fake, sent = env
    e = profile.change(club, "kd8ccc", {"phone": "419-555-0199", "address": "9 Oak Ave", "birthday": "07-04",
                                        "share": "phone, birthday"}, by="kd8ccc", sheet=sheet, url="https://x")
    assert sorted(fake.writes) == [("E3", "9 Oak Ave"), ("H3", "07-04"), ("I3", "phone, birthday")]  # phone unchanged
    assert e["old"] == {"address": "", "birthday": "", "share": ""}
    m = club.members["kd8ccc"]
    assert (m.address, m.birthday, m.share) == ("9 Oak Ave", "07-04", ["phone", "birthday"])
    cached = {r["id"]: r for r in json.loads((profile.ROSTER_CACHE).read_text())}
    assert cached["kd8ccc"]["address"] == "9 Oak Ave"
    (to, subject, body, kw), (to2, subject2, body2, kw2) = sent
    assert to == ["jane@example.org"] and "Bob Member" in subject and "9 Oak Ave" in body and "https://x/roster/changes" in body
    assert to2 == ["bob@example.org"] and "You updated" in body2 and "9 Oak Ave" in body2  # the member is told too
    assert "only_to" in kw and "only_to" in kw2  # email test mode applies
    assert profile.change(club, "kd8ccc", {"address": "9 Oak Ave"}, by="kd8ccc", sheet=sheet) is None  # no-op


def test_only_profile_fields_can_be_changed(env):
    club, sheet, fake, _ = env
    profile.change(club, "kd8ccc", {"status": "Inactive", "full_name": "Hacker", "phone": "1"}, by="kd8ccc", sheet=sheet)
    assert fake.writes == [("D3", "1")]


def test_undo_restores_and_refuses_if_changed_again(env):
    club, sheet, fake, sent = env
    e = profile.change(club, "kd8ccc", {"address": "9 Oak Ave"}, by="kd8ccc", sheet=sheet)
    profile.undo(club, e["id"], by="kd8aaa", sheet=sheet)
    assert fake.rows[2][4] == "" and club.members["kd8ccc"].address is None
    assert [x["undone"] for x in profile.history()] == [False, True]
    with pytest.raises(ValueError):
        profile.undo(club, e["id"], by="kd8aaa", sheet=sheet)  # only once
    e2 = profile.change(club, "kd8ccc", {"address": "1 New Rd"}, by="kd8ccc", sheet=sheet)
    profile.change(club, "kd8ccc", {"address": "2 Newer Rd"}, by="kd8ccc", sheet=sheet)
    with pytest.raises(ValueError):
        profile.undo(club, e2["id"], by="kd8aaa", sheet=sheet)  # would overwrite a newer change


def test_sheet_problems_change_nothing(env):
    club, _, fake, sent = env
    old = FakeSheets([HEADER[:7]] + [r[:7] for r in fake.rows[1:]])
    with pytest.raises(SheetError, match="birthday"):
        profile.change(club, "kd8ccc", {"birthday": "07-04"}, by="kd8ccc", sheet=Sheet(old))
    assert club.members["kd8ccc"].address is None and profile.history() == [] and sent == []


def test_helpers():
    assert [_col(i) for i in (0, 25, 26, 27)] == ["A", "Z", "AA", "AB"]
    assert _month_day("07-04") == "07-04" and _month_day("1961-07-04") == "07-04" and _month_day("") is None
    assert _month_day("25001") == "06-12"  # a Sheets date serial: the year is dropped
    assert _month_day("07") == "07" and _month_day("7") == "07"  # month only


def test_can_see():
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "o", "first": "O", "last": "Officer", "officer": True},
        {"id": "a", "first": "A", "last": "Member", "share": ["phone"]},
        {"id": "b", "first": "B", "last": "Member"}])
    assert club.can_see("b", "a", "phone") and not club.can_see("b", "a", "address")
    assert club.can_see("o", "a", "address") and club.can_see("a", "a", "address")
    assert not club.can_see(None, "a", "phone")


def test_email_change_needs_confirmation(tmp_path, monkeypatch):
    from w8lbz_minutes import auth as auth_mod
    sent = []
    monkeypatch.setattr(auth_mod, "send_email", lambda to, subject, body, **kw: sent.append((to, body)))
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "kd8ccc", "first": "Bob", "last": "Member", "email": "bob@example.org"},
        {"id": "kd8aaa", "first": "Jane", "last": "Doe", "email": "jane@example.org"}])
    a = auth_mod.Auth(club, tmp_path / "auth.sqlite")
    assert a.start_email_change("kd8ccc", "jane@example.org") == "Another member already uses that address."
    assert a.start_email_change("kd8ccc", "not-an-email")
    assert a.start_email_change("kd8ccc", "bob@new.example", base_url="https://x") is None
    (to, body), = sent
    token = body.split("/profile/email?t=")[1].split()[0]
    assert to == "bob@new.example"
    assert a.confirm_email_change(token) == ("kd8ccc", "bob@new.example")
    assert a.confirm_email_change(token) is None  # one use


def test_monthday_filter():
    from w8lbz_minutes.web import monthday
    assert monthday("07-04") == "July 4" and monthday("") == "" and monthday(None) == ""
    assert monthday("1961-07-04") == "1961-07-04" and monthday("13-40") == "13-40"
    assert monthday("07") == "July" and monthday("13") == "13"


def test_club_reloads_roster_after_a_sync(tmp_path, monkeypatch):
    import os
    from w8lbz_minutes import club as club_mod
    cache = tmp_path / "roster.json"
    cache.write_text(json.dumps([{"id": "kd8ccc", "first": "Bob", "last": "Member"}]))
    monkeypatch.setattr(club_mod, "ROSTER_CACHE", cache)
    cfg = tmp_path / "club.yaml"
    cfg.write_text("club: {name: T}\n")
    c = club_mod.Club(cfg)
    assert c.members["kd8ccc"].address is None and not c.refresh()
    cache.write_text(json.dumps([{"id": "kd8ccc", "first": "Bob", "last": "Member", "address": "9 Oak Ave"}]))
    os.utime(cache, (1, 2))  # a different mtime, as a sync would give
    assert c.refresh() and c.members["kd8ccc"].address == "9 Oak Ave"


def test_multiline_addresses_keep_their_line_breaks(env, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.store import Archive
    club, sheet, fake, sent = env
    assert web.clean_address(" 100 Main St  #12 \r\n\r\nAnytown, OH 44000 ") == "100 Main St #12\nAnytown, OH 44000"
    fake.rows[2][4] = "100 Main St #12 \nAnytown, OH 44000"  # as typed in the sheet, trailing space included
    club.members["kd8ccc"].address = fake.rows[2][4]
    monkeypatch.setattr(web, "archive", Archive(root=profile.DATA, club=club))  # an empty archive in tmp_path
    monkeypatch.setattr(web.authn, "member_for", lambda token: "kd8ccc")
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
    from w8lbz_minutes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "PREVIEW", "some-branch")
    monkeypatch.setattr(auth_mod, "DATA", tmp_path)
    monkeypatch.setattr(auth_mod.smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError("SMTP used")))
    monkeypatch.setenv("W8LBZ_SMTP_USER", "u")
    monkeypatch.setenv("W8LBZ_SMTP_PASSWORD", "p")
    assert auth_mod.send_email(["a@x.org", "b@x.org"], "Hi", "body", only_to={"a@x.org"}) == 1
    e = json.loads((tmp_path / "preview-outbox.jsonl").read_text())
    assert e["to"] == ["a@x.org"] and e["withheld"] == ["b@x.org"] and e["subject"] == "Hi"

    monkeypatch.setattr(roster, "PREVIEW", "some-branch")
    monkeypatch.setattr(roster, "DATA", tmp_path)
    monkeypatch.setattr(roster, "CACHE", tmp_path / "roster.json")
    (tmp_path / "roster.json").write_text(json.dumps([{"id": "kd8ccc", "first": "Bob", "last": "Member", "callsign": "KD8CCC"}]))
    sheet = Sheet()
    assert sheet.update_member("KD8CCC", "Bob Member", {"phone": "419-555-0111"}) == {"phone": ""}
    assert "419-555-0111" in (tmp_path / "preview-sheet.json").read_text()


def test_email_change_alerts_old_and_new_address(env):
    club, sheet, fake, sent = env
    profile.change(club, "kd8ccc", {"email": "bob@new.example"}, by="kd8ccc", sheet=sheet)
    assert sent[-1][0] == ["bob@new.example", "bob@example.org"]


def test_admin_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    from w8lbz_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "kd8adm", "first": "Ann", "last": "Admin", "callsign": "KD8ADM", "email": "ann@example.org"},
        {"id": "kd8ccc", "first": "Bob", "last": "Member", "phone": "419-555-0199"}])
    club.admins = {"kd8adm"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("kd8adm"))
    assert "419-555-0199" not in c.get("/members/kd8ccc").text          # off by default: an ordinary member
    assert c.get("/roster/changes").status_code == 403
    assert "Turn on admin mode" in c.get("/links").text
    turn_on_admin_mode(c, monkeypatch)
    page = c.get("/members/kd8ccc").text
    assert "419-555-0199" in page and "Admin mode is on" in page          # on: officer-level view
    assert c.get("/roster/changes").status_code == 200
    c.post("/admin/mode", data={"on": 0})
    assert c.get("/roster/changes").status_code == 403
    log = [json.loads(x) for x in (tmp_path / "admin-log.jsonl").read_text().splitlines()]
    assert [(e["on"], e.get("confirmed_by")) for e in log if "on" in e] == [(True, "email"), (False, None)]

    monkeypatch.setattr(web, "PREVIEW", "some-branch")
    assert c.get("/admin/email", follow_redirects=False).headers["location"].startswith("/admin/confirm")  # admin mode first
    assert "Email log" not in c.get("/links").text and "Email outbox" not in c.get("/links").text

    c.cookies.set(web.COOKIE, a.new_session("kd8ccc"))                    # not an admin: can't turn it on
    assert c.post("/admin/mode", data={"on": 1}).status_code == 403
    assert c.get("/admin/confirm").status_code == 403 and c.post("/admin/confirm/email").status_code == 403
    assert "Turn on admin mode" not in c.get("/links").text
    assert c.get("/admin/email").status_code == 404
    assert "Sign-in &amp; passkeys" not in c.get("/links").text           # it's a tab of Your profile now
    assert "Sign-in &amp; passkeys" in c.get("/profile").text and "Your details" in c.get("/account").text


def turn_on_admin_mode(c, monkeypatch):
    """Admin mode asks the admin to confirm it's them first: here (no passkey, not the passkey host) by email."""
    from urllib.parse import urlparse
    from w8lbz_minutes import web
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
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    from w8lbz_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "kd8adm", "first": "Ann", "last": "Admin", "callsign": "KD8ADM", "email": "ann@example.org"},
        {"id": "tess", "first": "Tess", "last": "Treasurer", "officer": True, "phone": "419-555-0142"},
        {"id": "bob", "first": "Bob", "last": "Member", "phone": "419-555-0199"}])
    club.admins = {"kd8adm"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("kd8adm"))
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


def test_live_email_log_records_sent_and_held_back(tmp_path, monkeypatch):
    from w8lbz_minutes import auth as auth_mod
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
    monkeypatch.setenv("W8LBZ_SMTP_USER", "club@example.org")
    monkeypatch.setenv("W8LBZ_SMTP_PASSWORD", "pw")
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
    from w8lbz_minutes import club as clubmod, web
    from w8lbz_minutes.auth import Auth
    from w8lbz_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Ann", "last": "Admin", "email": "ann@example.org"}])
    club.admins = {"kd8adm"}
    a = Auth(club, tmp_path / "auth.sqlite")
    for mod in (web, clubmod):
        monkeypatch.setattr(mod, "DATA", tmp_path)
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("kd8adm"))
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
