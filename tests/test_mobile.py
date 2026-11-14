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
EXTRA = ["/links?edit=1", "/calendar?view=month", "/members/n0admn/edit", "/treasurer?tab=money", "/treasurer?tab=bills", "/treasurer?tab=settings"] + [f"/admin/security/{t}" for t in ("signins", "devices", "failed", "bans", "admin", "backups", "server")] + ["/help/officers", "/help/server-admin"]
IPV6 = "2001:db8:85a3:1234:5678:8a2e:370:7334"  # real addresses can be this long
ENGINES = ["chromium", "firefox", "webkit"]  # Chrome/Edge, Firefox, and Safari's engine (close to Safari, not the same)
# Text whose contrast with what's behind it is under 3:1 (WCAG's floor for large text; body text wants 4.5):
# a colour left at its light-mode value shows up here in dark mode. Site's colour previews are meant to differ.
LOW_CONTRAST = """() => {
  const rgb = (s) => { const n = s.match(/[\\d.]+/g).map(Number);
    return s.startsWith("color(") ? [n[0] * 255, n[1] * 255, n[2] * 255, n[3] ?? 1] : [n[0], n[1], n[2], n[3] ?? 1]; };
  const lum = ([r, g, b]) => [r, g, b].map((v) => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
    .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
  const behind = (el) => { for (; el; el = el.parentElement) { const c = rgb(getComputedStyle(el).backgroundColor); if (c[3] > 0.5) return c; }
    return rgb(getComputedStyle(document.documentElement).backgroundColor); };
  const out = [];
  for (const el of document.body.querySelectorAll("*")) {
    if (el.closest(".tp, svg, [hidden]") || !el.getClientRects().length) continue;
    const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
    const st = getComputedStyle(el);
    if (!text || st.visibility === "hidden" || +st.opacity === 0) continue;
    const [a, b] = [lum(rgb(st.color)), lum(behind(el))];
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (ratio < 3) out.push(`${el.tagName.toLowerCase()}.${el.className} "${text.slice(0, 30)}" ${ratio.toFixed(1)}:1`);
  }
  return out;
}"""
LONG_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def pages(app) -> list[str]:
    return [r.path for r in app.routes if "GET" in getattr(r, "methods", ()) and "{" not in r.path
            and not r.path.startswith(("/static", "/api", "/docs", "/redoc", "/openapi")) and r.path not in SKIP] + EXTRA


@pytest.mark.parametrize("engine", ENGINES)
def test_every_page_fits_a_phone(tmp_path, monkeypatch, engine):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient

    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed", "email": "amy@example.org", "officer": True}])
    club.admins = {"n0admn"}
    from club_minutes import branding, theme, treasurer
    monkeypatch.setattr(branding, "DATA", tmp_path)  # the colours saved below stay in this test
    ts = treasurer.load()
    treasurer.set_general(ts, "n0admn", "preview", "2026-01-01", 4, 2, True)  # so Who owes what has rows
    treasurer.set_fund(ts, "n0admn", "Drinks fund", "Cash", ["n0admn"])
    import datetime as _dt
    for cents_, note in (("81", "Gas bill"), ("2884", "Paid insurance"), ("10", "Pizza for the fox hunt")):  # so Spending has a chart
        treasurer.record(club, ts, "n0admn", "expense", cents_, _dt.date.today().isoformat(), "Checking", note=note)
    treasurer.save(ts)
    club._yaml_links = [{"label": "Constitution and Bylaws of the club, the long version", "url": "https://example.org/a/very/long/address/bylaws.pdf"}]
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("n0admn", LONG_AGENT, IPV6, "passkey")
    a.set_admin(tok, 60)
    a.db.execute("insert into passkeys (id, member, public_key, sign_count, transports, label, created) values (?,?,?,?,?,?,?)",
                 ("pk1", "n0admn", b"", 0, "[]", "Google Password Manager", 1794143748))
    a.ban(IPV6, 24, "too many failed sign-ins from this address", "automatic")
    a.event("passkey_failed", IPV6, "n0admn", detail="The signature didn't match the passkey on file", agent=LONG_AGENT)
    a.event("bad_link", "203.0.113.7", agent=LONG_AGENT)  # not banned: the row keeps its Ban button
    web._admin_log(type("R", (), {"state": type("S", (), {"real_member": "n0admn"}), "client": type("C", (), {"host": IPV6})})(),
                   on=True, until=1794143748, confirmed_by="passkey")
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, tok)

    def serve(route):
        r = c.request(route.request.method, route.request.url.replace("http://testserver", ""), follow_redirects=route.request.method == "POST",  # Chromium won't take a redirect handed back here
                      content=route.request.post_data_buffer, headers={"content-type": route.request.headers.get("content-type", "")})
        route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)

    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page(viewport={"width": 375, "height": 800})
        page.route("http://testserver/**", serve)
        too_wide, blocked, path, seen = {}, [], "", []
        # the security policy runs no inline handlers or unmarked scripts: anything it blocks, or any script error, fails
        page.on("console", lambda m: m.type == "error" and ("Content Security Policy" in m.text or "Pattern attribute" in m.text) and blocked.append((path, m.text[:200])))
        page.on("pageerror", lambda e: blocked.append((path, str(e)[:200])))
        for path in pages(web.app):
            r = c.get(path, follow_redirects=False)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            page.goto("http://testserver" + path)
            seen.append(path)
            w = page.evaluate("document.documentElement.scrollWidth")
            if w > 375:
                too_wide[path] = w
        # each of Moved by / Seconded by leaves out whoever is in the other
        page.goto("http://testserver/business?tab=motions")
        lists = []
        for picked, other in (("mover", "seconder"), ("seconder", "mover")):
            page.fill(f"input[name={picked}] >> nth=-1", "Amy Appleseed")
            page.focus(f"input[name={other}] >> nth=-1")
            lists.append(page.locator(f"input[name={other}] >> nth=-1 >> xpath=.. >> li").all_inner_texts())
            page.fill(f"input[name={picked}] >> nth=-1", "")
        # Site → Colours: a colour shows in the two previews only; Cancel puts back what was saved; Save keeps it,
        # and the whole site then uses the palette the preview showed
        page.goto("http://testserver/admin/site#colours")
        form, light = page.locator("form[data-colours]"), page.locator(".tp[data-mode=light]")
        was = light.get_attribute("style")
        form.locator("input[name=main]").fill("#4f6f2a")
        form.locator(".unsaved").wait_for(state="visible")
        tried = light.get_attribute("style")
        form.locator("[data-cancel]").click()
        cancelled = (form.locator(".unsaved").is_hidden(), light.get_attribute("style") == was, form.locator("input[name=main]").input_value())
        form.locator("input[name=main]").fill("#4f6f2a")
        form.locator(".unsaved").wait_for(state="visible")
        with page.expect_navigation():
            form.locator("button.btn:not([type=button])").click()
        nav_bg = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--nav-bg').trim()")
        colours = (branding.colours()["main"], "Colours saved." in page.content(), f"--nav-bg: {nav_bg}" in tried)
        branding.reset_colours()
        # Security → Failed attempts: Details opens over the page with the browser's user agent; Esc closes it
        page.goto("http://testserver/admin/security/failed")
        page.locator("button[popovertarget=ev1]").first.click()
        details = (page.locator("#ev1").evaluate("p => p.matches(':popover-open')"), "iPhone" in page.locator("#ev1").inner_text(),
                   page.evaluate("document.documentElement.scrollWidth") <= 375)
        page.keyboard.press("Escape")
        details += (not page.locator("#ev1").evaluate("p => p.matches(':popover-open')"),)
        # dark mode, as a phone set to dark: every page again, any text too faint against its background
        dark = browser.new_page(viewport={"width": 375, "height": 800}, color_scheme="dark")
        dark.route("http://testserver/**", serve)
        dim = {}
        for path in seen:
            dark.goto("http://testserver" + path)
            if low := dark.evaluate(LOW_CONTRAST):
                dim[path] = low[:4]
            if dark.evaluate("getComputedStyle(document.body).backgroundColor.match(/[\\d.]+/g).slice(0, 3).some((v) => +v > 80)"):
                dim[path] = ["the page isn't dark"]
        browser.close()
    assert tried != was and cancelled == (True, True, theme.DEFAULT) and colours == ("#4f6f2a", True, True), (cancelled, colours)
    assert not dim, f"dark mode: text too faint for its background: {dim}"
    assert details == (True, True, True, True), details
    assert lists == [["(nobody)"], ["(nobody)"]], lists
    assert not too_wide, f"pages wider than a 375px phone: {too_wide}"
    assert not blocked, f"blocked by the security policy, or a script error: {blocked}"


@pytest.mark.parametrize("engine", ENGINES)
def test_view_as_shows_a_dialog_instead_of_sending_changes(tmp_path, monkeypatch, engine):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient

    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed", "email": "amy@example.org", "officer": True},
                                               {"id": "n0bbbb", "first": "Bob", "last": "Bloggs", "email": "bob@example.org"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("n0admn")
    a.set_admin(tok, 60)
    a.set_view_as(tok, "n0bbbb")
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
        assert page.is_visible("#readonly") and "Bob Bloggs" in page.inner_text("#readonly") and not posts
        page.click("#readonly >> text=OK")
        assert not page.is_visible("#readonly")
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_business_card_opens_its_dialog_and_comes_back_after_a_comment(tmp_path, monkeypatch, engine):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient

    from club_minutes import business, web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed", "email": "amy@example.org", "officer": True}])
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    s = business.load()
    x = business.add(s, "Fix the tower light", "n0admn", owner="n0admn")
    business.save(s)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("n0admn"))

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


@pytest.mark.parametrize("engine", ENGINES)
def test_a_name_after_a_minutes_line_never_splits(engine):
    """The member's name pill after a line ("Rich S") stays whole at any width; a long web address still wraps.
    Firefox broke it inside the word ("Ric" / "h S") while links in the minutes had word-break: break-all."""
    sync_api = pytest.importorskip("playwright.sync_api")
    css = (Path(__file__).parents[1] / "src/club_minutes/static/minutes.css").read_text()
    line = ("Took his new antenna down since it got into the neighbor's TV, then put an 80m Hamstick on his jeep "
            '<a class="who" href="/members/n0rich">Rich S</a>')
    url = "https://example.org/a/very/long/address/without/any/spaces/at/all/to/break/at/minutes.pdf"
    html = f'<style>{css}</style><article class="minutes"><ul class="items reports"><li>{line}</li><li>See <a href="{url}">{url}</a></li></ul></article>'
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page()
        split, wide = [], []
        for w in range(300, 901, 3):
            page.set_viewport_size({"width": w, "height": 600})
            page.set_content(html)
            if page.evaluate("document.querySelector('a.who').getClientRects().length") > 1:
                split.append(w)
            if page.evaluate("document.documentElement.scrollWidth") > w:
                wide.append(w)
        browser.close()
    assert not split, f"the name split at widths {split[:10]}"
    assert not wide, f"the long address made the page too wide at {wide[:10]}"


@pytest.mark.parametrize("engine", ENGINES)
def test_hovering_a_member_link_leaves_the_initials_alone(engine):
    """A link with a member's initials circle (the Roster, Business, Home...) underlines its text on hover, never the
    letters in the circle: the circle looks the same hovered or not."""
    sync_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).parents[1] / "src/club_minutes"
    css = (root / "static/style.css").read_text()
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(root / "templates"), autoescape=True)
    env.globals.update(avatar_url=lambda mid: None, avatar_hue=lambda mid: 200, initials=lambda mid: "JD")
    circle = env.from_string('{% from "_avatar.html" import avatar %}{{ avatar("n0jane", "sm") }}').render()
    # a plain link (a table row would shade itself on hover, behind the circle's corners)
    html = f'<style>{css}</style><p><a href="#" class="withavatar">{circle}Jane Doe</a></p>'
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page(device_scale_factor=2)
        page.set_content(html)
        before = page.locator(".avatar").screenshot()
        page.hover("a")
        page.wait_for_timeout(100)
        hovered = page.locator(".avatar").screenshot()
        underlined = page.evaluate("getComputedStyle(document.querySelector('a')).textDecorationColor")
        browser.close()
    assert underlined and before == hovered, "the link's hover underline shows inside the initials circle"


@pytest.mark.parametrize("engine", ENGINES)
def test_optional_date_and_time_boxes_can_be_cleared(engine):
    """fields.js: an optional date or time box gets a Clear button (an iPhone fills a box in when tapped and its picker
    can't empty it), shown only while there's a value; a required one gets none; boxes added later get one too."""
    sync_api = pytest.importorskip("playwright.sync_api")
    js = (Path(__file__).parents[1] / "src/club_minutes/static/fields.js").read_text()
    html = ('<form><label>Starts <input type="date" name="start" required value="2026-11-13"></label>'
            '<label>Due <input type="date" name="due" value="2026-11-20"></label>'
            '<input type="time" name="t" aria-label="Start time"></form><div id="later"></div>')
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page()
        page.set_content(html)
        page.add_script_tag(content=js)
        page.evaluate("window.changed = []; document.addEventListener('change', (e) => changed.push(e.target.name))")
        assert page.locator("input[name=start] + .clearbox").count() == 0  # required: nothing to clear
        assert page.get_by_role("button", name="Clear Due").is_visible()
        assert not page.locator("input[name=t] + .clearbox").is_visible()  # empty: hidden until there's a time
        page.get_by_role("button", name="Clear Due").click()  # the ✕ inside the box
        assert page.input_value("input[name=due]") == "" and page.evaluate("changed") == ["due"]
        assert not page.locator("input[name=due] + .clearbox").is_visible()
        page.fill("input[name=t]", "19:30")
        assert page.get_by_role("button", name="Clear Start time").is_visible()
        page.evaluate("document.getElementById('later').innerHTML = '<input type=date name=added value=2026-12-01>'")
        page.wait_for_timeout(50)
        assert page.locator("input[name=added] + .clearbox").is_visible()  # built after the page loaded (the console)
        browser.close()
