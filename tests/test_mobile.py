"""Mobile first: every page fits a phone's width (no sideways scrolling), checked in a real browser at 375px.
The same visit fails on anything the Content Security Policy blocks, or a script error.

New GET pages are picked up automatically from the app's routes. Pages are served to the browser straight from
the test client, so no server runs. Skipped where Playwright's Chromium isn't installed (`uv run playwright install chromium`).
"""
from pathlib import Path

import pytest

from w8lbz_minutes.auth import Auth
from w8lbz_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
SKIP = {"/logout", "/login/link", "/healthz"}  # not pages
EXTRA = ["/links?edit=1", "/calendar?view=month"] + [f"/admin/security/{t}" for t in ("signins", "devices", "failed", "bans", "admin", "backups", "server")]
IPV6 = "2001:db8:85a3:1234:5678:8a2e:370:7334"  # real addresses can be this long
LONG_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def pages(app) -> list[str]:
    return [r.path for r in app.routes if "GET" in getattr(r, "methods", ()) and "{" not in r.path
            and not r.path.startswith(("/static", "/api", "/docs", "/redoc", "/openapi")) and r.path not in SKIP] + EXTRA


def test_every_page_fits_a_phone(tmp_path, monkeypatch):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Amy", "last": "Able", "email": "amy@example.org", "officer": True}])
    club.admins = {"kd8adm"}
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
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {e}")
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
        browser.close()
    assert not too_wide, f"pages wider than a 375px phone: {too_wide}"
    assert not blocked, f"blocked by the security policy, or a script error: {blocked}"
