from pathlib import Path

import pytest

from w8lbz_minutes import auth as auth_mod
from w8lbz_minutes.auth import Auth, _safe_next
from w8lbz_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def a(tmp_path, monkeypatch):
    club = Club(FIX / "test-club.yaml",
                roster=[{"id": "kd8aaa", "first": "Jane", "last": "Doe", "email": "Jane@Example.org", "officer": True},
                        {"id": "kd8bbb", "first": "Old", "last": "Timer", "email": "old@example.org", "status": "Silent Key"}])
    club.auth = {"rp_id": "localhost"}
    sent = []
    monkeypatch.setattr(auth_mod, "send_email", lambda to, subject, body, **kw: sent.append((to, body)))
    obj = Auth(club, tmp_path / "auth.sqlite")
    obj.sent = sent
    return obj


def link_token(body: str) -> str:
    return body.split("/login/link?t=")[1].split()[0]


def test_email_link_flow(a):
    a.send_link("  jane@example.ORG ", "/console", base_url="http://192.168.1.100:8000")
    (to, body), = a.sent
    assert to == "Jane@Example.org" and "http://192.168.1.100:8000/login/link?t=" in body
    t = link_token(body)
    assert a.use_link(t) == ("kd8aaa", "/console")
    assert a.use_link(t) is None  # one use only


def test_unknown_and_inactive_addresses_get_nothing(a):
    a.send_link("stranger@example.org")
    a.send_link("old@example.org")  # silent key
    assert a.sent == []


def test_rate_limit(a):
    for _ in range(8):
        a.send_link("jane@example.org")
    assert len(a.sent) == auth_mod.EMAILS_PER_HOUR


def test_sessions(a):
    tok = a.new_session("kd8aaa", "pytest")
    assert a.member_for(tok) == "kd8aaa"
    assert a.member_for("bogus") is None and a.member_for(None) is None
    a.end_session(tok)
    assert a.member_for(tok) is None
    # only hashes are stored
    assert tok not in str(a.db.execute("select * from sessions").fetchall())


def test_safe_next():
    assert _safe_next("/console") == "/console"
    assert _safe_next("https://evil.example") == "/" and _safe_next("//evil.example") == "/"


def test_passkey_options(a):
    cid, options = a.registration_options("kd8aaa")
    assert '"residentKey": "required"' in options and '"id": "localhost"' in options
    with pytest.raises(ValueError):
        a.register("kd8aaa", "wrong-challenge", {})


def test_admin_link(a):
    url = a.create_link("kd8aaa", base_url="https://minutes.example")
    assert url.startswith("https://minutes.example/login/link?t=")
    assert a.use_link(url.split("t=")[1]) == ("kd8aaa", "/")


def test_email_test_mode_withholds_other_recipients(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, user, password): calls.append(("login", password))
        def send_message(self, msg): calls.append(("send", msg["To"]))

    monkeypatch.setattr(auth_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("W8LBZ_SMTP_USER", "club@example.org")
    monkeypatch.setenv("W8LBZ_SMTP_PASSWORD", "abcd efgh ijkl mnop")
    allow = {"me@example.org"}
    assert auth_mod.send_email("someone@example.org", "s", "b", only_to=allow) == 0
    assert calls == []
    assert auth_mod.send_email("Me@Example.org", "s", "b", only_to=allow) == 1
    assert calls == [("login", "abcdefghijklmnop"), ("send", "Me@Example.org")]


def test_only_to_resolves_callsigns():
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8aaa", "first": "J", "last": "D", "email": "J@x.org"}])
    club.email = {"only_to": ["KD8AAA", "other@y.org"]}
    assert club.email_only_to() == {"j@x.org", "other@y.org"}
    club.email = {}
    assert club.email_only_to() is None


def test_rename_passkey_and_old_db_upgrade(a, tmp_path):
    a.db.execute("insert into passkeys (id, member, public_key, sign_count, label, created) values ('p1','kd8aaa',x'00',0,'Windows PC',0)")
    a.rename_passkey("kd8aaa", "p1", "  Bitwarden ")
    a.rename_passkey("kd8bbb", "p1", "not mine")  # someone else's passkey: no effect
    assert a.passkeys("kd8aaa")[0]["label"] == "Bitwarden"
    # an auth.sqlite created before the aaguid column existed gets upgraded on start
    import sqlite3
    old = tmp_path / "old.sqlite"
    con = sqlite3.connect(old)
    con.executescript("create table passkeys (id text primary key, member text not null, public_key blob not null, "
                      "sign_count integer not null, transports text, label text, created integer not null, last_used integer)")
    con.close()
    Auth(a.club, old)
    assert "aaguid" in [r[1] for r in sqlite3.connect(old).execute("pragma table_info(passkeys)")]


def test_email_links_ignore_forged_host(monkeypatch):
    """A sign-in link must never point at whatever Host header the request carried."""
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    sent = []
    monkeypatch.setattr(web.authn, "send_link", lambda email, nxt, base_url=None, **kw: sent.append(base_url))
    monkeypatch.setattr(web.authn, "base_url", "https://minutes.example.net")
    client = TestClient(web.app)
    r = client.post("/login/email", data={"email": "amy@example.org"}, headers={"host": "evil.example.com"},
                    follow_redirects=False)
    assert sent[-1] == "https://minutes.example.net"
    client.post("/login/email", data={"email": "amy@example.org"}, follow_redirects=False)
    assert sent[-1] == "http://testserver"  # the address the connection really reached
    for h in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "X-Robots-Tag"):
        assert h in r.headers


def test_failed_attempts_ban_internet_addresses_only(a):
    for _ in range(auth_mod.BAN_AFTER - 1):
        a.send_link("stranger@example.org", ip="9.9.9.9")
        a.event("bad_link", "192.168.1.50")
    assert not a.banned("9.9.9.9")
    a.send_link("nobody@example.org", ip="9.9.9.9")                  # the tenth in an hour
    assert a.banned("9.9.9.9") and a.bans()[0]["by"] == "automatic"
    a.event("bad_link", "192.168.1.50")                                 # LAN: logged, never auto-banned
    assert not a.banned("192.168.1.50") and not a.banned(None)
    a.unban("9.9.9.9")
    assert not a.banned("9.9.9.9")
    a.ban("1.0.0.1", None, "by hand", "kd8aaa")
    assert a.banned("1.0.0.1")                                       # no end date: until lifted
    a.new_session("kd8aaa", "Mozilla/5.0 (iPhone) Safari/604.1", "8.8.4.4", "passkey")
    assert [(r["member"], r["method"], r["ip"]) for r in a.signins()] == [("kd8aaa", "passkey", "8.8.4.4")]
    sid = a.all_sessions()[0]["rowid"]
    assert a.end_session_id(sid) == "kd8aaa" and not a.all_sessions()


def test_banned_address_is_blocked_and_security_page_is_admins_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8aaa", "first": "Jane", "last": "Doe", "email": "jane@example.org"}])
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("kd8aaa"))
    assert c.get("/admin/security").status_code == 404                  # not an admin
    a.ban("testclient", None, "test", "kd8aaa")                           # the test client's "address"
    r = c.get("/login")
    assert r.status_code == 403 and "blocked" in r.text
    assert web.device("Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36") == "Chrome on Windows"
    assert [web.where(x) for x in ("9.9.9.9", "192.168.1.5", "100.64.0.5", "127.0.0.1")] == \
        ["internet", "LAN", "tailnet", "this server"]


def test_security_page_tabs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Ann", "last": "Admin", "email": "ann@example.org"}])
    club.admins = {"kd8adm"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("kd8adm", "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0", "192.168.1.10", "passkey")
    a.set_admin(tok, 60)
    a.ban("9.9.9.9", 24, "test", "automatic")
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, tok)
    assert "Chrome on Windows" in c.get("/admin/security").text
    for t in ("devices", "failed", "bans", "admin", "server"):
        assert c.get(f"/admin/security/{t}").status_code == 200
    assert "9.9.9.9" in c.get("/admin/security/bans").text and "9.9.9.9" not in c.get("/admin/security/server").text
    assert c.get("/admin/security/nope").status_code == 404
    r = c.post("/admin/security/unban", data={"ip": "9.9.9.9"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/security/bans" and not a.banned("9.9.9.9")
    # devices: filter by member, sign out several at once
    other = [a.new_session("kd8adm", "Firefox/1", "192.168.1.11"), a.new_session("kd8adm", "Firefox/2", "192.168.1.12")]
    assert c.get("/admin/security/devices?member=kd8adm").text.count('name="session"') == 3
    ids = [r["rowid"] for r in a.all_sessions() if r["agent"].startswith("Firefox")]
    r = c.post("/admin/security/signout", data={"session": ids, "member": "kd8adm"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/security/devices?member=kd8adm"
    assert not any(a.member_for(t) for t in other) and a.member_for(tok) == "kd8adm"
    # admin actions read as plain words
    assert web.admin_action({"on": True, "until": 1794143748, "confirmed_by": "passkey"}).startswith("Turned on admin mode until ")
    assert web.admin_action({"on": False, "until": None}) == "Turned off admin mode"
    assert web.admin_action({"signed_out": "kd8adm"}) == "Signed out a device of Ann Admin"


def test_an_emailed_link_sign_in_offers_a_passkey_until_dismissed(tmp_path, monkeypatch):
    """The offer (base.html) shows after an emailed-link sign-in on the passkey host; the page's script checks the
    device and clears the cookie on Add or Not now, so the next emailed-link sign-in offers again."""
    from fastapi.testclient import TestClient
    from test_calview import signed_in
    from w8lbz_minutes import web
    _, club, a = signed_in(tmp_path, monkeypatch, "kd8aaa")
    monkeypatch.setattr(a, "rp_id", "minutes.example")  # the passkey host
    t = TestClient(web.app, base_url="https://minutes.example")
    r = t.get("/login/link?t=" + a.create_link("kd8aaa").split("t=")[1], follow_redirects=False)
    assert r.headers["location"] == "/" and t.cookies.get(web.PK_OFFER) == "1"
    home = t.get("/").text
    assert 'id="pkoffer"' in home and home.count("/static/auth.js") == 1
    assert t.get("/account").text.count("/static/auth.js") == 1  # loaded once, not twice
    t.cookies.delete(web.PK_OFFER)  # what Not now does in the browser
    assert 'id="pkoffer"' not in t.get("/").text
    other = TestClient(web.app)  # not the passkey host: no offer
    other.get("/login/link?t=" + a.create_link("kd8aaa").split("t=")[1], follow_redirects=False)
    assert other.cookies.get(web.PK_OFFER) is None
