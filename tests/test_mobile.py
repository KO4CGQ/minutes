"""Mobile first: every page fits a phone's width (no sideways scrolling), checked in a real browser at 375px.
The same visit fails on anything the Content Security Policy blocks, or a script error.

New GET pages are picked up automatically from the app's routes. Pages are served to the browser straight from
the test client, so no server runs. Run in Chromium, Firefox and WebKit; an engine that isn't installed is skipped
(`uv run playwright install chromium firefox webkit`, then `sudo .venv/bin/playwright install-deps` once).
"""
import re
from pathlib import Path

import pytest

from club_minutes.auth import Auth
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
SKIP = {"/logout", "/login/link", "/healthz"}  # not pages
EXTRA = ["/links?edit=1", "/calendar?view=month", "/members/kd8adm/edit", "/treasurer?tab=money", "/treasurer?tab=bills", "/treasurer?tab=settings"] + [f"/admin/security/{t}" for t in ("signins", "devices", "failed", "bans", "admin", "backups", "server")]
IPV6 = "2001:db8:85a3:1234:5678:8a2e:370:7334"  # real addresses can be this long
ENGINES = ["chromium", "firefox", "webkit"]  # Chrome/Edge, Firefox, and Safari's engine (close to Safari, not the same)
LONG_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def pages(app) -> list[str]:
    return [r.path for r in app.routes if "GET" in getattr(r, "methods", ()) and "{" not in r.path
            and not r.path.startswith(("/static", "/api", "/docs", "/redoc", "/openapi")) and r.path not in SKIP] + EXTRA


@pytest.mark.parametrize("engine", ENGINES)
def test_every_page_fits_a_phone(tmp_path, monkeypatch, engine):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Amy", "last": "Able", "email": "amy@example.org", "officer": True}])
    club.admins = {"kd8adm"}
    from club_minutes import treasurer
    ts = treasurer.load()
    treasurer.set_general(ts, "kd8adm", "preview", "2026-01-01", 4, 2, True)  # so Who owes what has rows
    treasurer.set_fund(ts, "kd8adm", "Drinks fund", "Cash", ["kd8adm"])
    import datetime as _dt
    for cents_, note in (("81", "Gas bill"), ("2884", "Paid insurance"), ("10", "Pizza for the fox hunt")):  # so Spending has a chart
        treasurer.record(club, ts, "kd8adm", "expense", cents_, _dt.date.today().isoformat(), "Checking", note=note)
    treasurer.save(ts)
    club._yaml_links = [{"label": "Constitution and Bylaws of the club, the long version", "url": "https://example.org/a/very/long/address/bylaws.pdf"}]
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("kd8adm", LONG_AGENT, IPV6, "passkey")
    a.set_admin(tok, 60)
    a.db.execute("insert into passkeys (id, member, public_key, sign_count, transports, label, created) values (?,?,?,?,?,?,?)",
                 ("pk1", "kd8adm", b"", 0, "[]", "Google Password Manager", 1794143748))
    a.ban(IPV6, 24, "too many failed sign-ins from this address", "automatic")
    web._admin_log(type("R", (), {"state": type("S", (), {"real_member": "kd8adm"}), "client": type("C", (), {"host": IPV6})})(),
                   on=True, until=1794143748, confirmed_by="passkey")
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, tok)

    def serve(route):
        r = c.request(route.request.method, route.request.url.replace("http://testserver", ""), follow_redirects=False)
        route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)

    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page(viewport={"width": 375, "height": 800})
        page.route("http://testserver/**", serve)
        too_wide, blocked, path = {}, [], ""
        # the security policy runs no inline handlers or unmarked scripts: anything it blocks, or any script error, fails
        page.on("console", lambda m: m.type == "error" and "Content Security Policy" in m.text and blocked.append((path, m.text[:200])))
        page.on("pageerror", lambda e: blocked.append((path, str(e)[:200])))
        for path in pages(web.app):
            r = c.get(path, follow_redirects=False)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            page.goto("http://testserver" + path)
            w = page.evaluate("document.documentElement.scrollWidth")
            if w > 375:
                too_wide[path] = w
        # each of Moved by / Seconded by leaves out whoever is in the other
        page.goto("http://testserver/business?tab=motions")
        lists = []
        for picked, other in (("mover", "seconder"), ("seconder", "mover")):
            page.fill(f"input[name={picked}] >> nth=-1", "Amy Able")
            page.focus(f"input[name={other}] >> nth=-1")
            lists.append(page.locator(f"input[name={other}] >> nth=-1 >> xpath=.. >> li").all_inner_texts())
            page.fill(f"input[name={picked}] >> nth=-1", "")
        browser.close()
    assert lists == [["(nobody)"], ["(nobody)"]], lists
    assert not too_wide, f"pages wider than a 375px phone: {too_wide}"
    assert not blocked, f"blocked by the security policy, or a script error: {blocked}"


@pytest.mark.parametrize("engine", ENGINES)
def test_view_as_shows_a_dialog_instead_of_sending_changes(tmp_path, monkeypatch, engine):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Amy", "last": "Able", "email": "amy@example.org", "officer": True},
                                               {"id": "kd8bbb", "first": "Bob", "last": "Baker", "email": "bob@example.org"}])
    club.admins = {"kd8adm"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("kd8adm")
    a.set_admin(tok, 60)
    a.set_view_as(tok, "kd8bbb")
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, tok)
    posts = []

    def serve(route):
        if route.request.method == "POST":
            posts.append(route.request.url)
        r = c.request(route.request.method, route.request.url.replace("http://testserver", ""), follow_redirects=False)
        route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)

    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page()
        page.route("http://testserver/**", serve)
        page.goto("http://testserver/business?tab=notes")
        page.fill("textarea[name=text]", "Away this week")
        page.click("text=Send note")
        assert page.is_visible("#readonly") and "Bob Baker" in page.inner_text("#readonly") and not posts
        page.click("#readonly >> text=OK")
        assert not page.is_visible("#readonly")
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_business_card_opens_its_dialog_and_comes_back_after_a_comment(tmp_path, monkeypatch, engine):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from club_minutes import business, web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Amy", "last": "Able", "email": "amy@example.org", "officer": True}])
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    s = business.load()
    x = business.add(s, "Fix the tower light", "kd8adm", owner="kd8adm")
    business.save(s)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("kd8adm"))

    def serve(route):
        r = c.request(route.request.method, route.request.url.replace("http://testserver", ""), follow_redirects=False)
        route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)

    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page(viewport={"width": 375, "height": 800})
        page.route("http://testserver/**", serve)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://testserver/business")
        d = f"#d{x['id']}"
        assert not page.is_visible(d)
        page.click(f"#i{x['id']}")  # the card
        assert page.is_visible(d) and page.is_visible(f"{d} >> text=Mark done") and page.is_hidden(f"#e{x['id']}")
        page.click(f"{d} >> text=Edit")
        assert page.is_visible(f"#e{x['id']}")
        # after a comment the app sends you back to /business#i<id> (Playwright can't follow a faked redirect, so go there)
        r = c.post(f"/business/{x['id']}/comment", data={"text": "On it this weekend"}, follow_redirects=False)
        assert r.headers["location"] == f"/business#i{x['id']}"
        page.goto("about:blank")  # a real reload, as after the redirect (same address + #i… alone wouldn't reload)
        page.goto("http://testserver" + r.headers["location"])
        assert page.is_visible(d) and page.is_visible(f"{d} >> text=On it this weekend")  # back, still open
        page.keyboard.press("Escape")
        sync_api.expect(page).not_to_have_url(re.compile("#"))  # closing it takes #i… off the address (no string eval: the CSP forbids it)
        assert not page.is_visible(d)
        assert not errors
        browser.close()
