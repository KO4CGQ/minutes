from pathlib import Path

import pytest

from club_minutes import auth as auth_mod
from club_minutes.auth import Auth, _safe_next
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def a(tmp_path, monkeypatch):
    club = Club(FIX / "test-club.yaml",
                roster=[{"id": "n0aaaa", "first": "Jane", "last": "Doe", "email": "Jane@Example.org", "officer": True},
                        {"id": "n0bbbb", "first": "Old", "last": "Timer", "email": "old@example.org", "status": "Silent Key"}])
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
    assert a.use_link(t) == ("n0aaaa", "/console")
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
    tok = a.new_session("n0aaaa", "pytest")
    assert a.member_for(tok) == "n0aaaa"
    assert a.member_for("bogus") is None and a.member_for(None) is None
    a.end_session(tok)
    assert a.member_for(tok) is None
    # only hashes are stored
    assert tok not in str(a.db.execute("select * from sessions").fetchall())


def test_safe_next():
    assert _safe_next("/console") == "/console"
    assert _safe_next("https://evil.example") == "/" and _safe_next("//evil.example") == "/"


def test_passkey_options(a):
    _cid, options = a.registration_options("n0aaaa")
    assert '"residentKey": "required"' in options and '"id": "localhost"' in options
    with pytest.raises(ValueError):
        a.register("n0aaaa", "wrong-challenge", {})


def test_admin_link(a):
    url = a.create_link("n0aaaa", base_url="https://minutes.example")
    assert url.startswith("https://minutes.example/login/link?t=")
    assert a.use_link(url.split("t=")[1]) == ("n0aaaa", "/")


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
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "club@example.org")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "abcd efgh ijkl mnop")
    allow = {"me@example.org"}
    assert auth_mod.send_email("someone@example.org", "s", "b", only_to=allow) == 0
    assert calls == []
    assert auth_mod.send_email("Me@Example.org", "s", "b", only_to=allow) == 1
    assert calls == [("login", "abcdefghijklmnop"), ("send", "Me@Example.org")]


def test_only_to_resolves_callsigns():
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0aaaa", "first": "J", "last": "D", "email": "J@example.org"}])
    club.email = {"only_to": ["N0AAAA", "other@example.net"]}
    assert club.email_only_to() == {"j@example.org", "other@example.net"}
    club.email = {}
    assert club.email_only_to() is None


def test_rename_passkey_and_old_db_upgrade(a, tmp_path):
    a.db.execute("insert into passkeys (id, member, public_key, sign_count, label, created) values ('p1','n0aaaa',x'00',0,'Windows PC',0)")
    a.rename_passkey("n0aaaa", "p1", "  Bitwarden ")
    a.rename_passkey("n0bbbb", "p1", "not mine")  # someone else's passkey: no effect
    assert a.passkeys("n0aaaa")[0]["label"] == "Bitwarden"
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

    from club_minutes import web
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
    a.ban("1.0.0.1", None, "by hand", "n0aaaa")
    assert a.banned("1.0.0.1")                                       # no end date: until lifted
    a.new_session("n0aaaa", "Mozilla/5.0 (iPhone) Safari/604.1", "8.8.4.4", "passkey")
    assert [(r["member"], r["method"], r["ip"]) for r in a.signins()] == [("n0aaaa", "passkey", "8.8.4.4")]
    sid = a.all_sessions()[0]["rowid"]
    assert a.end_session_id(sid) == "n0aaaa" and not a.all_sessions()


def test_banned_address_is_blocked_and_security_page_is_admins_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0aaaa", "first": "Jane", "last": "Doe", "email": "jane@example.org"}])
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("n0aaaa"))
    assert c.get("/admin/security").status_code == 404                  # not an admin
    a.ban("testclient", None, "test", "n0aaaa")                           # the test client's "address"
    r = c.get("/login")
    assert r.status_code == 403 and "blocked" in r.text
    assert web.device("Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36") == "Chrome on Windows"
    assert [web.where(x) for x in ("9.9.9.9", "192.168.1.5", "100.64.0.5", "127.0.0.1")] == \
        ["internet", "LAN", "tailnet", "this server"]


def test_failed_attempts_from_before_1_0_get_the_browser_column(tmp_path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "auth.sqlite")
    old.execute("create table events (at integer not null, ip text, kind text not null, member text, detail text)")
    old.execute("insert into events values (1, '203.0.113.9', 'bad_link', null, '')")
    old.commit()
    old.close()
    a = Auth(Club(FIX / "test-club.yaml", roster=[]), tmp_path / "auth.sqlite")
    a.event("bad_link", "203.0.113.9", agent="Firefox/150")
    assert [r["agent"] for r in a.events()] == ["Firefox/150", None]


def test_sign_in_from_before_the_rename_still_works(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0aaaa", "first": "Jane", "last": "Doe", "email": "jane@example.org"}])
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    c = TestClient(web.app)
    c.cookies.set(web.OLD_COOKIE, a.new_session("n0aaaa"))  # set by 0.19.0 or earlier
    assert c.get("/profile", follow_redirects=False).status_code == 200
    r = c.post("/logout", follow_redirects=False)
    assert web.OLD_COOKIE in r.headers.get("set-cookie", "") and not a.all_sessions()

def test_security_page_tabs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Ann", "last": "Admin", "email": "ann@example.org"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("n0admn", "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0", "192.168.1.10", "passkey")
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
    # failed attempts: the browser is recorded, Details shows it (escaped) and the attempt's note; a banned address
    # says so and links to the bans; this admin's own address says "You" (it can't be banned from here)
    c.get("/login/link?t=nope", headers={"user-agent": "Mozilla/5.0 (X11; Linux) Firefox/150 <script>x</script>"}, follow_redirects=False)
    a.event("passkey_failed", "203.0.113.7", detail="Signature didn't match", agent="Mozilla/5.0 (iPhone) Safari/604.1")
    a.event("bad_link", "203.0.113.8")  # recorded before the browser was
    a.ban("203.0.113.7", None, "by hand", "n0admn")
    f = c.get("/admin/security/failed").text
    assert "Firefox on Linux" in f and "&lt;script&gt;x&lt;/script&gt;" in f and "<script>x" not in f
    assert "Signature didn&#39;t match" in f and "Safari on iPhone" in f and "not recorded" in f and ">You<" in f
    assert 'href="/admin/security/bans">Banned</a>' in f and f.count("popovertarget=") == 6  # Details and its close, per row
    assert f.count('action="/admin/security/ban"') == 1  # only 203.0.113.8 can still be banned here
    # devices: filter by member, sign out several at once
    other = [a.new_session("n0admn", "Firefox/1", "192.168.1.11"), a.new_session("n0admn", "Firefox/2", "192.168.1.12")]
    assert c.get("/admin/security/devices?member=n0admn").text.count('name="session"') == 3
    ids = [r["rowid"] for r in a.all_sessions() if r["agent"].startswith("Firefox")]
    r = c.post("/admin/security/signout", data={"session": ids, "member": "n0admn"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/security/devices?member=n0admn"
    assert not any(a.member_for(t) for t in other) and a.member_for(tok) == "n0admn"
    # admin actions read as plain words
    assert web.admin_action({"on": True, "until": 1794143748, "confirmed_by": "passkey"}).startswith("Turned on admin mode until ")
    assert web.admin_action({"on": False, "until": None}) == "Turned off admin mode"
    assert web.admin_action({"signed_out": "n0admn"}) == "Signed out a device of Ann Admin"


def test_an_emailed_link_sign_in_offers_a_passkey_until_dismissed(tmp_path, monkeypatch):
    """The offer (base.html) shows after an emailed-link sign-in on the passkey host; the page's script checks the
    device and clears the cookie on Add or Not now, so the next emailed-link sign-in offers again."""
    from fastapi.testclient import TestClient
    from test_calview import signed_in

    from club_minutes import web
    _, _club, a = signed_in(tmp_path, monkeypatch, "n0aaaa")
    monkeypatch.setattr(a, "rp_id", "minutes.example")  # the passkey host
    t = TestClient(web.app, base_url="https://minutes.example")
    r = t.get("/login/link?t=" + a.create_link("n0aaaa").split("t=")[1], follow_redirects=False)
    assert r.headers["location"] == "/" and t.cookies.get(web.PK_OFFER) == "1"
    home = t.get("/").text
    assert 'id="pkoffer"' in home and home.count("/static/auth.js") == 1
    assert t.get("/account").text.count("/static/auth.js") == 1  # loaded once, not twice
    t.cookies.delete(web.PK_OFFER)  # what Not now does in the browser
    assert 'id="pkoffer"' not in t.get("/").text
    other = TestClient(web.app)  # not the passkey host: no offer
    other.get("/login/link?t=" + a.create_link("n0aaaa").split("t=")[1], follow_redirects=False)
    assert other.cookies.get(web.PK_OFFER) is None


def test_no_passkey_offer_on_a_browser_that_has_one(tmp_path, monkeypatch):
    """A browser remembers the members with a passkey on it (pk_here: set when one is added or used there); a link
    sign-in there offers none to them, but still to another member on the same browser."""
    from fastapi.testclient import TestClient
    from test_calview import signed_in

    from club_minutes import web
    _, _club, a = signed_in(tmp_path, monkeypatch, "n0aaaa")
    monkeypatch.setattr(a, "rp_id", "minutes.example")
    t = TestClient(web.app, base_url="https://minutes.example")
    t.cookies.set(web.PK_HERE, "n0aaaa")
    t.get("/login/link?t=" + a.create_link("n0aaaa").split("t=")[1], follow_redirects=False)
    assert t.cookies.get(web.PK_OFFER) is None and 'id="pkoffer"' not in t.get("/").text
    t.cookies.set(web.PK_OFFER, "1")  # an offer from before it was known: not shown either
    assert 'id="pkoffer"' not in t.get("/").text
    t.cookies.delete(web.PK_OFFER)
    t.get("/login/link?t=" + a.create_link("n0bbbb").split("t=")[1], follow_redirects=False)
    assert t.cookies.get(web.PK_OFFER) == "1"  # Bob has none here
    from starlette.requests import Request
    from starlette.responses import Response
    req = Request({"type": "http", "headers": [(b"cookie", b"pk_here=n0aaaa.a-b")], "method": "GET", "path": "/"})
    r = web.passkey_here(req, Response(), "n0bbbb")
    assert "pk_here=n0aaaa.n0bbbb;" in r.headers["set-cookie"] and "Max-Age=31536000" in r.headers["set-cookie"]
