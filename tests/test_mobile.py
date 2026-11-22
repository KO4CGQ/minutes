"""Mobile first: every page fits a phone's width (no sideways scrolling), checked in a real browser at 375px.
The same visit fails on anything the Content Security Policy blocks, or a script error.

New GET pages are picked up automatically from the app's routes. Pages are served to the browser straight from
the test client, so no server runs. Run in Chromium, Firefox and WebKit; an engine that isn't installed is skipped
(`uv run playwright install chromium firefox webkit`, then `sudo .venv/bin/playwright install-deps` once).
"""
import re
from pathlib import Path

import pytest

from club_minutes import auth as authmod
from club_minutes.auth import Auth
from club_minutes.club import Club
from club_minutes.web import SITE_TABS

FIX = Path(__file__).parent / "fixtures"
SKIP = {"/logout", "/login/link", "/healthz"}  # not pages
EXTRA = ["/links?edit=1", "/calendar?view=month", "/members/n0admn/edit", "/treasurer?tab=money", "/treasurer?tab=bills", "/treasurer?tab=settings"] + [f"/admin/security/{t}" for t in ("signins", "devices", "failed", "bans", "admin", "backups", "uploads")] + ["/help/officers", "/help/server-admin", "/help/jitsi"]
EXTRA += [f"/admin/site/{t[0]}" for t in SITE_TABS if t[0] != "look"]  # every Site tab
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
# What a person using a screen reader, a thumb or old eyes would trip on, per page (returns problem lists):
#  - a field with no label of its own (a wrapping label, for=, aria-label or aria-labelledby; hidden fields excepted)
#  - a checkbox or radio under 24px (WCAG 2.2, 2.5.8). Its label counts when it is the input's label: a tap on a label
#    switches the box, so a label of 24px or more is a big enough target, as WCAG's "same function" rule has it
#  - two buttons, or two links to different places, of one name in different rows, list items or cards ("Remove", "Edit": which one?)
#  - a .fmtgrid (Site's form grids, three columns; hidden fields take no cell) whose cells don't fill whole rows: one missing cell shifts the rest
AUDIT = """() => {
  const shown = (el) => el.getClientRects().length > 0 && !el.closest("[hidden]");
  const nm = (el) => { if (el.nodeType === 3) return el.textContent; if (el.nodeType !== 1 || el.getAttribute("aria-hidden") === "true") return "";
    const l = el.getAttribute("aria-label"); if (l) return l; if (el.tagName === "IMG") return el.alt;
    return [...el.childNodes].map(nm).join(""); };
  const name = (el) => (el.getAttribute("aria-label") || (el.getAttribute("aria-labelledby") || "").split(/\\s+/).map((i) => document.getElementById(i)?.textContent || "").join(" ")
    || [...el.childNodes].map(nm).join("") || el.title || "").replace(/\\s+/g, " ").trim().toLowerCase();
  const out = { unlabelled: [], small: [], repeated: [], grid: [] };
  const desc = (el) => `${el.tagName.toLowerCase()}[${el.name || el.id || el.type || ""}]`;
  for (const el of document.querySelectorAll("input, select, textarea")) {
    if (["hidden", "submit", "button", "reset", "image"].includes(el.type)) continue;
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\\s+/).filter((i) => document.getElementById(i));
    if (!el.getAttribute("aria-label") && !ids.length && !el.labels.length) out.unlabelled.push(desc(el));
    if (["checkbox", "radio"].includes(el.type) && shown(el) && getComputedStyle(el).opacity !== "0") {
      const big = (r) => r.width >= 24 && r.height >= 24;
      if (!big(el.getBoundingClientRect()) && ![...el.labels].some((l) => shown(l) && big(l.getBoundingClientRect()))) {
        const r = el.getBoundingClientRect(); out.small.push(`${desc(el)} ${Math.round(r.width)}x${Math.round(r.height)}`); }
    }
  }
  const byName = {};
  for (const el of document.querySelectorAll("button, a[href], input[type=submit]")) {
    const row = el.closest("tr, li, .card"), n = el.matches("input") ? el.value.toLowerCase() : name(el);
    if (row && n && shown(el)) (byName[n] ??= new Map()).set(row, el.matches("a") ? el.getAttribute("href") : el);
  }
  for (const [n, rows] of Object.entries(byName)) if (new Set(rows.values()).size > 1) out.repeated.push(`"${n}" x${rows.size}`);
  for (const g of document.querySelectorAll(".fmtgrid")) if ([...g.children].filter((c) => c.type !== "hidden").length % 3) out.grid.push(`${g.children.length} cells: ${g.textContent.trim().slice(0, 30)}`);
  return out;
}"""
# Identical names that are fine, with the reason (matched on the lower-case name)
SAME_NAME_OK = {}
LONG_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def pages(app) -> list[str]:
    return [r.path for r in app.routes if "GET" in getattr(r, "methods", ()) and "{" not in r.path
            and not r.path.startswith(("/static", "/api", "/docs", "/redoc", "/openapi")) and r.path not in SKIP] + EXTRA


@pytest.mark.parametrize("engine", ENGINES)
def test_every_page_fits_a_phone(tmp_path, monkeypatch, engine):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient

    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed", "email": "amy@example.org", "role": "President", "officer": True},
                                               {"id": "n0nomn", "first": "Jane", "last": "Doe", "email": "jane@example.org"}])
    club.admins = {"n0admn"}
    from club_minutes import branding, theme, treasurer
    monkeypatch.setattr(branding, "DATA", tmp_path)  # the colours saved below stay in this test
    monkeypatch.setattr(treasurer, "DATA", tmp_path)  # each engine starts with the same books
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
    # pages that need something behind their address: minutes to correct, an emailed confirmation link, a recovery under way
    import datetime as _d

    from club_minutes import recovery
    from club_minutes.meeting import Item, Meeting
    monkeypatch.setattr(recovery, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(authmod, "send_email", lambda to, subject, body, **kw: sent.append(body))
    m = Meeting(date=_d.date(2026, 10, 15), status="published", leader="n0admn", in_person=["n0admn"],
                sections={"Old Business": [Item("Ordered patches")]})
    open_ = Meeting(date=_d.date(2026, 10, 1), status="draft", leader="n0admn")  # its "Continue minutes" button in every top bar
    monkeypatch.setattr(web.archive, "meetings", [m, open_])
    monkeypatch.setattr(web.archive, "by_slug", {m.slug: m, open_.slug: open_})
    assert a.start_email_change("n0admn", "new@example.org", base_url="http://testserver") is None
    token = sent[-1].split("/email/confirm?t=")[1].split()[0]
    extra_pages = [f"/meetings/{m.slug}/correct", f"/email/confirm?t={token}"]
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
        too_wide, blocked, path, seen, audit = {}, [], "", [], {}
        # the security policy runs no inline handlers or unmarked scripts: anything it blocks, or any script error, fails
        page.on("console", lambda m: m.type == "error" and ("Content Security Policy" in m.text or "Pattern attribute" in m.text) and blocked.append((path, m.text[:200])))
        page.on("pageerror", lambda e: blocked.append((path, str(e)[:200])))

        def inspect(label):  # the checks every page gets, once it is showing
            if (w := page.evaluate("document.documentElement.scrollWidth")) > 375:
                too_wide[label] = w
            page.evaluate("document.querySelectorAll('details').forEach((d) => d.open = true)")  # closed parts count too
            found = page.evaluate(AUDIT)
            found["repeated"] = [n for n in found["repeated"] if n.rsplit(" x", 1)[0].strip('"') not in SAME_NAME_OK]
            if bad := {k: v[:6] for k, v in found.items() if v}:
                audit[label] = bad

        for path in pages(web.app) + extra_pages:
            r = c.get(path, follow_redirects=False)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            page.goto("http://testserver" + path)
            seen.append(path)
            inspect(path)
        # a recovery under way (the page above showed no recovery), and the correction's preview, which only a POST shows
        recovery.start(club, "n0admn", "n0nomn", [], "Motion to restore admin access", "http://testserver")
        page.goto("http://testserver/recover")
        seen.append("/recover")
        inspect("/recover (under way)")
        page.goto(f"http://testserver/meetings/{m.slug}/correct")
        # a guest row just added: its Remove takes it away at once, focus back on + Add; a saved item's is still Keep it
        page.locator("summary:has-text('Guests')").click()
        page.get_by_role("button", name="+ Add a guest").click()
        added = page.locator("fieldset.guest").count()
        page.get_by_role("button", name="Remove A guest left out").click()
        dropped = (added, page.locator("fieldset.guest").count(), page.evaluate("document.activeElement.textContent"))
        page.locator("summary:has-text('Old Business')").click()
        dropped += (page.locator(".item label.rm input[type=checkbox]").count(),)
        page.locator("textarea").first.fill("Ordered the patches")
        page.locator("input[name=reason]").fill("A typing mistake")
        path = "the correction preview"
        page.get_by_role("button", name="Preview the changes").click()
        page.wait_for_selector("table.diff")
        inspect(path)
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
        # the hex box: a typed code (short, no #) sets the picker and the preview; a wrong one is marked, the picker kept
        form.locator("[data-hex=main]").fill("4F6")
        form.locator(".unsaved").wait_for(state="visible")
        hexed = [form.locator("input[name=main]").input_value()]
        form.locator("[data-hex=main]").fill("#zz")
        hexed += [form.locator("[data-hex=main]").get_attribute("aria-invalid"), form.locator("input[name=main]").input_value()]
        form.locator("[data-cancel]").click()
        hexed += [form.locator("[data-hex=main]").input_value() == form.locator("input[name=main]").input_value()]
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
    assert hexed == ["#44ff66", "true", "#44ff66", True], hexed  # the box follows Cancel too
    assert dropped == (1, 0, "+ Add a guest", 1), dropped  # added, removed at once, focus on + Add; the saved item keeps Keep it
    assert lists == [["(nobody)", "Jane Doe"]] * 2, lists  # Amy is left out of the other box, whichever she is in
    assert not audit, f"labels, touch targets, repeated button names, form grids: {audit}"
    assert not too_wide, f"pages wider than a 375px phone: {too_wide}"
    assert not blocked, f"blocked by the security policy, or a script error: {blocked}"


@pytest.mark.parametrize("engine", ENGINES)
def test_view_as_shows_a_dialog_instead_of_sending_changes(tmp_path, monkeypatch, engine):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient

    from club_minutes import web
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed", "email": "amy@example.org", "role": "President", "officer": True},
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
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed", "email": "amy@example.org", "role": "President", "officer": True}])
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
    html = f'<style>{css}</style><article class="minutes"><ul class="items lined"><li>{line}</li><li>See <a href="{url}">{url}</a></li></ul></article>'
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
def test_a_console_comment_shows_two_whole_lines(engine):
    """Jim, 2026-11-22: a max-height clamp cut a formatted comment mid-line on desktop; now two lines and an ellipsis."""
    sync_api = pytest.importorskip("playwright.sync_api")
    from club_minutes.richtext import render
    static = Path(__file__).parents[1] / "src/club_minutes/static"
    css = (static / "style.css").read_text() + (static / "console.css").read_text()
    texts = ["Para one that runs on.\n\nPara two.\n\nPara three.", "- one\n- two\n- three", "word " * 200]
    html = f"<style>{css}</style>" + "".join(f'<div class="bizcomment"><div class="ctext clamp">{render(t)}</div></div>' for t in texts)
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page()
        for w in (390, 1280):
            page.set_viewport_size({"width": w, "height": 800})
            page.set_content(html)
            # the text starts at the box's top (a paragraph's margin pushed it down) and the box holds two lines
            got = page.eval_on_selector_all(".ctext", """els => els.map(e => { const r = document.createRange(); r.selectNodeContents(e);
                const top = r.getClientRects()[0].top - e.getBoundingClientRect().top;
                return [Math.round(top), e.clientHeight / parseFloat(getComputedStyle(e).lineHeight)]; })""")
            assert all(top <= 2 and 1.9 < n < 2.2 for top, n in got), (w, got)
        browser.close()


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
    css = (Path(__file__).parents[1] / "src/club_minutes/static/style.css").read_text()
    html = (f'<style>{css}</style><form><label>Starts <input type="date" name="start" required value="2026-11-13"></label>'
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
        size = page.get_by_role("button", name="Clear Due").bounding_box()
        assert size["width"] >= 36 and size["height"] >= 30, size  # a thumb can hit it (the circle drawn inside is small)
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


@pytest.mark.parametrize("engine", ENGINES)
def test_the_formatting_toolbar_on_a_phone(engine):
    """format.js: a web address typed without https:// works (it failed silently); a list starts on its own line with
    no placeholder word; Enter continues a list and Enter on an empty item ends it; the buttons are finger-sized."""
    sync_api = pytest.importorskip("playwright.sync_api")
    js = (Path(__file__).parents[1] / "src/club_minutes/static/format.js").read_text()
    css = (Path(__file__).parents[1] / "src/club_minutes/static/style.css").read_text()
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_context(viewport={"width": 390, "height": 600}, has_touch=True, is_mobile=engine != "firefox").new_page()
        page.set_content(f'<style>{css}</style><textarea data-format aria-label="Report"></textarea>')
        page.add_script_tag(content=js)
        box = page.get_by_label("Report")
        box.click()
        btn = page.get_by_role("button", name="Bulleted list").bounding_box()
        assert btn["width"] >= 44 and btn["height"] >= 44, btn
        dlg, link = page.locator("dialog.fmtlink"), page.get_by_role("button", name="Link", exact=False)
        box.type("See")
        link.click()  # one form: the address, its words and a QR code
        dlg.get_by_label("Web address").fill("not an address")
        dlg.get_by_role("button", name="Add").click()  # refused, with a message: the form stays, nothing changes
        assert dlg.get_by_role("alert").inner_text().startswith('"not an address" doesn') and box.input_value() == "See"
        dlg.get_by_label("Web address").fill("example.org")
        dlg.get_by_role("button", name="Add").click()
        box.type(" ok")  # straight away: the box has its caret back
        assert not dlg.is_visible() and box.input_value() == "See https://example.org ok"
        box.press("Backspace"); box.press("Backspace"); box.press("Backspace")
        page.get_by_role("button", name="Numbered list").click()
        box.type("mast")
        box.press("Enter")
        box.type("coax")
        box.press("Enter")
        box.press("Enter")  # an empty item: the list ends
        box.type("Done")
        assert box.input_value() == "See https://example.org\n1. mast\n2. coax\nDone"
        box.fill("")
        link.click()
        dlg.get_by_label("Web address").fill("example.org/fd")
        dlg.get_by_label("Words to show").fill(" Field [Day] ")
        dlg.get_by_label("Add a QR code").check()
        dlg.get_by_role("button", name="Add").click()  # its words without brackets; the QR mark after it
        assert box.input_value() == "[Field Day](https://example.org/fd) [QR]"
        box.press("End"); box.type(" ok")
        box.evaluate("t => t.setSelectionRange(3, 3)")  # the caret on the link: the form opens with it, to change
        link.click()
        assert dlg.get_by_label("Web address").input_value() == "https://example.org/fd" and dlg.get_by_label("Add a QR code").is_checked()
        assert dlg.get_by_role("button", name="Save").is_visible()
        dlg.get_by_label("Add a QR code").uncheck()
        dlg.get_by_role("button", name="Save").click()
        assert box.input_value() == "[Field Day](https://example.org/fd) ok"
        link.click(); dlg.get_by_role("button", name="Cancel").click()  # Cancel changes nothing
        assert box.input_value() == "[Field Day](https://example.org/fd) ok"
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_quote_and_code_buttons(engine):
    """format.js: Quote marks the caret's lines (and takes it off again); Enter continues a quote, Enter on an empty quote
    line ends it; Code wraps a word in backticks, and on an empty line starts a block with the caret inside it; a box with
    data-count shows its length."""
    sync_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).parents[1] / "src/club_minutes/static"
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_context(viewport={"width": 390, "height": 600}).new_page()
        page.set_content(f'<style>{(root / "style.css").read_text()}</style><textarea data-format aria-label="Report" maxlength="5000" data-count></textarea>')
        page.add_script_tag(content=(root / "format.js").read_text())
        assert page.locator(".fmtcount").inner_text() == "0 / 5,000 characters"
        box, quote = page.get_by_label("Report"), page.get_by_role("button", name="Quote")
        box.click()
        page.get_by_role("button", name="Bold").click()  # nothing selected: the caret between the marks, no placeholder word
        box.type("2 m")
        assert box.input_value() == "**2 m**"
        box.fill("")
        box.click()
        quote.click()  # an empty box: "> ", ready to type
        box.type("Dear park,")
        box.press("Enter")
        box.type("Thanks")
        box.press("Enter")
        box.press("Enter")  # an empty quote line: the quote ends
        box.type("Run ls")
        assert box.input_value() == "> Dear park,\n> Thanks\nRun ls"
        box.press("Shift+ArrowLeft")
        box.press("Shift+ArrowLeft")
        page.get_by_role("button", name="Code (on lines of its own: a code block)").click()
        box.press("End")
        box.press("Enter")
        page.get_by_role("button", name="Code (on lines of its own: a code block)").click()
        box.type("uv sync")
        assert box.input_value() == "> Dear park,\n> Thanks\nRun `ls`\n```\nuv sync\n```"
        box.press("Control+Home")
        quote.click()  # all its lines quoted: Quote takes it off
        assert box.input_value().startswith("Dear park,\n> Thanks")
        assert page.locator(".fmtcount").inner_text() == f"{len(box.input_value())} / 5,000 characters"  # it follows the toolbar too
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_toolbar_button_works_once_in_a_labelled_box(engine):
    """format.js: the Business page's boxes sit in a <label>; with the toolbar inside it too, one tap on B ran twice
    ("****tower****", found on stage 2026-11-20). The toolbar goes before the label, and before a console row (not beside its box)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).parents[1] / "src/club_minutes/static"
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_context(viewport={"width": 390, "height": 600}).new_page()
        page.set_content(f'<style>{(root / "style.css").read_text()}</style><form><label>Item<textarea data-format name="text"></textarea></label></form>'
                         '<div class="row"><textarea data-format aria-label="Bills"></textarea><button>Remove</button></div>')
        page.add_script_tag(content=(root / "format.js").read_text())
        box = page.get_by_label("Item")
        box.click()
        page.get_by_role("button", name="Bold").click()
        box.type("tower")
        assert box.input_value() == "**tower**"
        page.get_by_label("Bills").click()  # in a row with its buttons (the console): the toolbar goes above the row, full width
        assert page.evaluate("document.querySelector('.fmtbar').nextElementSibling.className") == "row"
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_quote_goes_on_over_several_lines(engine):
    """format.js: Quote, then Enter or Shift+Enter, carries the quote to the next line; Enter on an empty quote line ends it.
    A phone keyboard's Enter (no named key, so no usable keydown) is a bare beforeinput, which must do the same."""
    sync_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).parents[1] / "src/club_minutes/static"
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page()
        page.set_content('<textarea data-format aria-label="Box" rows="1"></textarea>')
        page.add_script_tag(content=(root / "format.js").read_text())
        box = page.get_by_label("Box")
        box.click()
        page.get_by_role("button", name="Quote").click()
        box.type("one")
        page.keyboard.press("Enter")
        box.type("two")
        page.keyboard.press("Shift+Enter")
        box.type("three")
        box.evaluate("t => t.dispatchEvent(new InputEvent('beforeinput', { inputType: 'insertLineBreak', cancelable: true, bubbles: true }))")
        assert box.input_value() == "> one\n> two\n> three\n> "
        page.keyboard.press("Enter")
        assert box.input_value() == "> one\n> two\n> three\n"
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_preview_shows_the_text_as_the_server_renders_it_and_goes_back(engine):
    """format.js: Preview swaps the box for the server's rendering (stubbed here with the same renderer), focus on it;
    Back to editing brings the box back with its text, focus in it."""
    sync_api = pytest.importorskip("playwright.sync_api")
    from club_minutes import richtext
    root = Path(__file__).parents[1] / "src/club_minutes/static"
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page()
        from urllib.parse import parse_qs
        page.route("http://testserver/**", lambda route: route.fulfill(
            body=str(richtext.render(parse_qs(route.request.post_data)["text"][0])) if route.request.method == "POST"
            else '<form><label>Item<textarea data-format></textarea></label></form>', content_type="text/html"))
        page.goto("http://testserver/")
        page.add_script_tag(content=(root / "format.js").read_text())
        box = page.get_by_label("Item")
        box.fill("> quoted")
        page.get_by_role("button", name="Preview").click()
        region = page.get_by_role("region", name="Preview")
        region.locator("blockquote.rquote").wait_for()
        assert not box.is_visible() and page.get_by_role("button", name="Bold").is_disabled()
        assert page.evaluate("document.activeElement.className") == "fmtpreview"
        page.get_by_role("button", name="Back to editing").click()
        assert box.is_visible() and box.input_value() == "> quoted" and region.count() == 0
        assert page.evaluate("document.activeElement.tagName") == "TEXTAREA"
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_the_toolbar_scrolls_instead_of_widening_the_console(engine):
    """The console's columns (.cols2) grew to the one-row toolbar's width on a phone, so the whole page scrolled
    sideways (found on stage 2026-11-21): the toolbar scrolls on its own row instead."""
    sync_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).parents[1] / "src/club_minutes/static"
    css = (root / "style.css").read_text() + (root / "console.css").read_text()
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_context(viewport={"width": 360, "height": 700}, has_touch=True, is_mobile=engine == "chromium").new_page()
        page.set_content(f'<meta name="viewport" content="width=device-width"><style>{css}</style><div class="cols2"><div class="queue">People</div>'
                         '<div class="card"><textarea data-format aria-label="Report"></textarea></div></div>')
        page.add_script_tag(content=(root / "format.js").read_text())
        page.get_by_label("Report").click()
        assert page.locator(".fmtbar").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 360
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_typing_an_at_sign_picks_a_member(engine):
    """format.js: "@ja" lists matching Active members; Enter writes the mention and a space; Escape closes the list."""
    sync_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).parents[1] / "src/club_minutes/static"
    js, css = (root / "format.js").read_text(), (root / "style.css").read_text()
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_context(viewport={"width": 390, "height": 600}).new_page()
        page.set_content(f'<style>{css}</style><textarea data-format aria-label="Report"></textarea><script type="application/json" id="mentions">'
                         '[{"id":"n0call","name":"Jane Doe","short":"Jane D","callsign":"N0CALL"},{"id":"w0demo","name":"John Roe","short":"John R","callsign":"W0DEMO"}]</script>')
        page.add_script_tag(content=js)
        box = page.get_by_label("Report")
        box.click()
        box.type("Thanks @ja")
        assert page.get_by_role("option").count() == 1
        box.press("Enter")
        assert box.input_value() == "Thanks @[Jane D](n0call) "
        assert page.get_by_role("listbox").is_hidden()
        box.type("@")
        assert page.get_by_role("option").count() == 2
        page.get_by_role("option", name="John R").click()  # a tap
        assert box.input_value() == "Thanks @[Jane D](n0call) @[John R](w0demo) "
        box.type("@zz")
        assert page.get_by_role("listbox").is_hidden()  # no match: closed
        # in a modal dialog (a business item's comments): the list shows inside it and a tap reaches it (it opened
        # behind the dialog, under its backdrop, where nothing can be tapped: found on stage 2026-11-20)
        page.evaluate("""document.body.insertAdjacentHTML("beforeend", '<dialog class="modal"><textarea data-format aria-label="Comment"></textarea></dialog>');
                         document.querySelector("dialog").showModal()""")
        comment = page.get_by_label("Comment")
        comment.click()
        comment.type("Ask @jo")
        page.get_by_role("option", name="John R").click(timeout=3000)  # fails if covered or inert
        assert comment.input_value() == "Ask @[John R](w0demo) "
        browser.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_tap_on_a_home_items_box_opens_the_item(engine):
    """Home's business items: the box drawn beside each is part of its link (a tap on the box on an iPhone did
    nothing, and the stuck "hover" left the faint tick showing as if it were done)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    css = (Path(__file__).parents[1] / "src/club_minutes/static/style.css").read_text()
    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page(viewport={"width": 390, "height": 400})
        page.set_content(f'<style>{css}</style><ul class="checklist open"><li><a href="#x" id="it">Order QSL cards</a></li></ul>')
        box = page.evaluate("(() => { const r = document.querySelector('li').getBoundingClientRect(); return [r.left + 8, r.top + 14]; })()")
        hit = page.evaluate(f"document.elementFromPoint({box[0]}, {box[1]}).closest('a')?.id")
        browser.close()
    assert hit == "it"


@pytest.mark.parametrize("engine", ENGINES)
def test_site_video_enter_saves_and_leaving_unsaved_asks(tmp_path, monkeypatch, engine):
    """Site → Video: Enter in a room's name saves (never presses a room's Delete); a change is marked and brings up
    Discard; a room's Delete deletes it alone; leaving a Site tab with unsaved changes asks first."""
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient

    from club_minutes import club as clubmod
    from club_minutes import meet, web
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed", "email": "amy@example.org", "role": "President", "officer": True}])
    club.modules["jitsi"] = True
    club.yaml = {**club.yaml, "modules": {**(club.yaml.get("modules") or {}), "jitsi": True}, "jitsi": {"domain": "meet.example.org", "app_id": "club-minutes"}}
    club.save_app_admins({"n0admn"})
    monkeypatch.setenv("CLUB_MINUTES_JITSI_SECRET", "x" * 40)
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("n0admn")
    a.set_admin(tok, 60)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, tok)
    c.post("/admin/site/video", data={"rows": "2", "name0": "Club meeting", "slug0": "club", "name1": "Board", "slug1": "board"})
    assert [r["slug"] for r in meet.rooms(club)] == ["club", "board"]

    def serve(route):
        q = route.request
        r = c.request(q.method, q.url.replace("http://testserver", ""), content=q.post_data_buffer,
                      headers={"content-type": q.headers.get("content-type", "")}, follow_redirects=q.method == "POST")
        route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)

    with sync_api.sync_playwright() as p:
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {engine} for Playwright: {e}")
        page = browser.new_page()
        page.route("http://testserver/**", serve)
        asked = []
        page.on("dialog", lambda d: (asked.append(d.type), d.accept() if d.type == "confirm" else d.dismiss()))
        page.goto("http://testserver/admin/site/video")
        page.locator("details.role").first.evaluate("e => e.open = true")
        page.fill("input[name=name0]", "Club night")
        with page.expect_navigation():
            page.press("input[name=name0]", "Enter")
        assert [r["name"] for r in meet.rooms(club)] == ["Club night", "Board"] and not asked
        page.locator("details.role").first.evaluate("e => e.open = true")
        assert page.is_hidden("[data-discard]")
        page.fill("input[name=name0]", "Not saved")
        assert "changed" in page.get_attribute("input[name=name0]", "class") and page.is_visible("[data-discard]")
        page.fill("input[name=name0]", "Club night")  # back as it was: nothing to save
        assert "changed" not in page.get_attribute("input[name=name0]", "class") and page.is_hidden("[data-discard]")
        page.fill("input[name=name0]", "Not saved")
        page.locator("details.role").nth(1).evaluate("e => e.open = true")
        with page.expect_navigation():
            page.click("text=Delete Board")
        assert [r["name"] for r in meet.rooms(club)] == ["Club night"] and asked == ["confirm"]  # Board alone; the typing discarded
        asked.clear()
        page.locator("details.role").first.evaluate("e => e.open = true")
        page.fill("input[name=name0]", "Thrown away")
        with page.expect_navigation():
            page.click("text=Discard changes")
        assert page.input_value("input[name=name0]") == "Club night" and not asked
        page.locator("details.role").first.evaluate("e => e.open = true")
        page.fill("input[name=name0]", "Unsaved")
        try:
            page.goto("http://testserver/", timeout=3000)
        except Exception:  # noqa: BLE001, S110  # the dismissed "Leave site?" stops the navigation
            pass
        assert asked == ["beforeunload"]
        browser.close()
