"""Whole-app security checks: every route for a signed-out visitor, officer pages for a member, members' own-only actions,
post-sign-in redirects, and bad input that used to be a server error."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import business, chow, web
from club_minutes.auth import Auth, _safe_next
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "kd8aaa", "first": "Amy", "last": "Able", "email": "amy@example.org"},
          {"id": "kd8bbb", "first": "Bob", "last": "Baker", "email": "bob@example.org"},
          {"id": "kd8off", "first": "Ola", "last": "Officer", "officer": True, "email": "o@example.org"}]
PUBLIC_GET = {"/login", "/favicon.ico", "/brand/theme.css", "/calendar/public", "/calendar/public.ics", "/calendar/feed/x.ics", "/api/roster", "/login/link",
              "/healthz"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(chow, "DATA", tmp_path)

    def as_(who):
        t = TestClient(web.app, client=("203.0.113.6", 5000))
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    return as_


def routes():
    for r in web.app.routes:
        path = r.path
        for p in ("slug", "iid", "eid", "rid", "cid", "sid", "nid", "mid", "tab", "tid", "id", "key", "kind"):
            path = path.replace("{%s}" % p, "x")
        path = path.replace("{token}.ics", "x.ics")
        if "{" not in path and not path.startswith(("/static", "/docs", "/openapi")):
            for m in getattr(r, "methods", None) or ():
                if m in ("GET", "POST"):
                    yield m, path


def test_a_signed_out_visitor_reaches_only_the_public_pages(app):
    anon = TestClient(web.app, client=("203.0.113.5", 5000))
    seen = 0
    for m, path in routes():
        if path.startswith(("/login/email", "/auth/passkey/login/")):  # how you sign in: public by design (tested on their own)
            continue
        r = anon.request(m, path, data={"x": "1"} if m == "POST" else None, follow_redirects=False)
        seen += 1
        if m == "GET" and path not in PUBLIC_GET:
            assert r.status_code in (303, 401) and r.headers.get("location", "/login").startswith("/login"), (path, r.status_code)
        if m == "POST":
            assert r.status_code == 401, (path, r.status_code)
    assert seen > 100


@pytest.mark.parametrize("path", ["/console", "/roster/add", "/roster/changes", "/calendar/edit", "/calendar/import", "/calendar/changes",
                                  "/links/icons", "/treasurer", "/admin/updates", "/admin/security", "/admin/email", "/spending"])
def test_officer_and_admin_pages_turn_members_away(app, path):
    assert app("kd8aaa").get(path, follow_redirects=False).status_code in (403, 404)


def test_members_change_only_their_own_things(app):
    s = business.load()
    x = business.add(s, "Bob's item", "kd8off", owner="kd8bbb")
    c = business.comment(s, x["id"], "kd8bbb", "Bob's comment")
    business.save(s)
    cs = chow.load()
    cs["swaps"].append({"id": "sw1", "requester": "kd8off", "with": "kd8bbb", "mine": "2030-01-03", "theirs": "2030-01-10", "status": "pending"})
    chow.save(cs)
    amy = app("kd8aaa")
    amy.post(f"/business/{x['id']}/edit", data={"text": "hijacked", "due": ""})
    amy.post(f"/business/{x['id']}/done", data={"done": "1"})
    amy.post(f"/business/{x['id']}/comments/{c['id']}/delete")
    amy.post("/chow/requests/sw1/answer", data={"approve": "1"})
    it = business.load()["items"][0]
    assert (it["text"], it["status"], len(it["comments"])) == ("Bob's item", "open", 1)
    assert chow.load()["swaps"][0]["status"] == "pending"


@pytest.mark.parametrize("bad", ["//evil.example", "/\\evil.example", "/\t/evil.example", "/\n/evil.example", "https://evil.example",
                                 "javascript:alert(1)", "evil.example", "/\x7f/x"])
def test_after_sign_in_you_never_leave_the_site(bad):
    assert _safe_next(bad) == "/"


def test_after_sign_in_local_paths_still_work():
    assert _safe_next("/business?tab=done#i1") == "/business?tab=done#i1" and _safe_next("/%5Cx") == "/%5Cx"


def test_unreadable_requests_are_a_400_not_a_crash(app):
    anon = TestClient(web.app, raise_server_exceptions=False)
    for body in (b"", b"not json"):
        assert anon.post("/auth/passkey/login/verify", content=body).status_code == 400


def test_the_original_pdf_is_only_served_for_a_real_meeting(app):
    amy = app("kd8aaa")
    for slug in ("..", ".", "%2e%2e"):
        assert amy.get(f"/meetings/{slug}/original.pdf", follow_redirects=False).status_code == 404


def test_a_slow_profile_save_doesnt_hold_up_everyone_else(app, monkeypatch):
    """Saving a profile writes the Google roster sheet, which can take seconds; other requests are answered meanwhile."""
    import asyncio
    import time
    import httpx
    from club_minutes import profile
    monkeypatch.setattr(profile, "change", lambda *a, **k: time.sleep(2))  # the sheet, slow today
    cookie = {web.COOKIE: web.authn.new_session("kd8aaa")}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://testserver", cookies=cookie) as c:
            t0 = time.monotonic()
            save = asyncio.create_task(c.post("/profile", data={"phone": "419-555-0101"}))
            await asyncio.sleep(0.2)  # the save is under way
            other = await c.get("/about")
            waited = time.monotonic() - t0
            assert other.status_code == 200 and waited < 1.5, waited  # not stuck behind the 2-second save
            assert (await save).status_code == 303
    asyncio.run(run())


def test_the_upload_limit_is_the_admins_and_turns_big_requests_away(app, tmp_path, monkeypatch):
    from club_minutes import avatars, limits
    monkeypatch.setattr(limits, "DATA", tmp_path)
    assert limits.upload_mb() == limits.DEFAULT_MB
    with pytest.raises(ValueError):
        limits.set_upload_mb(0)
    with pytest.raises(ValueError):
        limits.set_upload_mb(101)
    limits.set_upload_mb(1)
    amy = app("kd8aaa")
    big = b"x" * (limits.max_upload() + limits.FORM_ROOM + 1)
    r = amy.post("/profile/photo", files={"photo": ("p.jpg", big, "image/jpeg")})
    assert r.status_code == 413 and "1 MB at most" in r.text  # turned away before it's read
    with pytest.raises(ValueError, match="1 MB at most"):
        avatars.save("kd8aaa", b"x" * (limits.max_upload() + 1))
    assert amy.post("/admin/security/limits", data={"upload_mb": "50"}).status_code in (403, 404)  # admins only
    assert limits.upload_mb() == 1
    (tmp_path / "limits.json").write_text("not json")  # a damaged file: the default, not a crash
    assert limits.upload_mb() == limits.DEFAULT_MB


def test_an_admin_in_admin_mode_sets_the_upload_limit(tmp_path, monkeypatch):
    from club_minutes import limits
    monkeypatch.setattr(limits, "DATA", tmp_path)
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    club.admins = {"kd8off"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("kd8off")
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, tok)
    assert t.post("/admin/security/limits", data={"upload_mb": "25"}).status_code == 403  # admin mode first
    a.set_admin(tok, 60)
    r = t.post("/admin/security/limits", data={"upload_mb": "25"})
    assert r.status_code == 200 and "Uploads up to 25 MB." in r.text and limits.upload_mb() == 25
    assert 'value="25"' in t.get("/admin/security/server").text


def test_roster_changes_wait_for_the_nightly_sync_in_another_process(tmp_path, monkeypatch):
    """The sync (`club-minutes roster`, its own process) and the web app take the same roster lock, so neither writes over
    the other's roster.json."""
    import os
    import subprocess
    import sys
    import time
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import time; from club_minutes.club import roster_locked\n"
                               "roster_locked(lambda: (print('held', flush=True), time.sleep(1.5)))()"],
        env={**os.environ, "CLUB_MINUTES_DATA": str(tmp_path)}, stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"  # the "sync" has the lock
    t0 = time.monotonic()
    assert clubmod.roster_locked(lambda: clubmod.roster_locked(lambda: "nested ok")())() == "nested ok"  # waits; nesting is fine
    assert time.monotonic() - t0 > 1.0
    holder.wait(5)



def test_admin_address_members_tab_and_a_page_for_not_found(app, monkeypatch):
    """/admin goes to Security for an admin and is not found for a member; a browser gets a page, scripts get JSON."""
    import time as _t
    web.archive.club.admins = {"kd8off"}
    admin, member = app("kd8off"), app("kd8bbb")
    assert admin.get("/admin/", follow_redirects=False).headers["location"].startswith("/admin/confirm")  # admin mode first
    old = admin.get("/admin/security/backups?note=hi", follow_redirects=False)  # Backups' old address, in older emails
    assert old.status_code == 301 and old.headers["location"] == "/admin/backups?note=hi"
    assert member.get("/admin/security/backups").status_code == 404
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    hub = admin.get("/admin").text
    assert all(f'href="/admin/{t}"' in hub for t in ("security", "updates", "site", "email")) and "failed sign-in" in hub
    assert member.get("/admin").status_code == 404
    page_ = member.get("/no-such-page", headers={"Accept": "text/html"})
    assert page_.status_code == 404 and "Page not found" in page_.text
    assert member.get("/no-such-page", headers={"Accept": "application/json"}).json() == {"detail": "Not Found"}
    web.authn.db.execute("insert into passkeys (id, member, public_key, sign_count, label, created) values ('pk1', 'kd8bbb', x'00', 0, 'phone', ?)",
                         (int(_t.time()),))
    got = web.authn.by_member()
    assert got["kd8bbb"]["passkeys"] == 1 and got["kd8bbb"]["devices"] == 1 and "last" not in got["kd8bbb"]


def test_admins_add_and_remove_admins_in_the_app(app, tmp_path, monkeypatch):
    """Admin → Security → Admins: adding needs a fresh confirmation; club.yaml's stay; never the last admin; others are told."""
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(web.authmod, "send_email", lambda to, subject, body, **k: sent.append((to, subject)) or 1)
    c = web.archive.club
    c.admins = {"kd8off"}  # club.yaml's
    admin, member = app("kd8off"), app("kd8bbb")
    tok = admin.cookies.get(web.COOKIE)
    assert member.post("/admin/security/admins/add", data={"member": "kd8bbb"}).status_code == 404  # not an admin
    assert admin.post("/admin/security/admins/add", data={"member": "kd8bbb"}).status_code == 403  # admin mode first
    web.authn.set_admin(tok, 50)  # an hour from confirming, so confirmed 10 minutes ago
    r = admin.post("/admin/security/admins/add", data={"member": "kd8bbb"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/confirm?next=" + web.quote("/admin/security/admins?add=kd8bbb") and not c.app_admins
    web.authn.set_admin(tok, 60)  # just confirmed
    assert admin.post("/admin/security/admins/add", data={"member": "nobody"}).status_code == 400
    page_ = admin.post("/admin/security/admins/add", data={"member": "kd8bbb"}).text
    assert "Bob Baker is now an admin." in page_ and c.is_admin("kd8bbb") and c.app_admins == {"kd8bbb"}
    assert sent == [(["bob@example.org"], "Bob Baker is now an admin")]  # not to whoever made the change
    assert "Made Bob Baker an admin" in admin.get("/admin/security/admin").text
    assert admin.post("/admin/security/admins/remove", data={"member": "kd8off"}).status_code == 400  # club.yaml's: on the server
    assert "Bob Baker is no longer an admin." in admin.post("/admin/security/admins/remove", data={"member": "kd8bbb"}).text
    assert not c.is_admin("kd8bbb") and sent[-1] == (["bob@example.org"], "Bob Baker is no longer an admin")
    c.admins = set()  # only app admins now: the last one can't go
    c.save_app_admins({"kd8off"})
    assert admin.post("/admin/security/admins/remove", data={"member": "kd8off"}).status_code == 400 and c.is_admin("kd8off")


@pytest.mark.parametrize("headers, ok", [
    ({}, True),  # scripts and older browsers: the SameSite cookie still covers them
    ({"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}, True),
    ({"Origin": "https://meet.example-tailnet.ts.net", "Sec-Fetch-Site": "same-site"}, False),  # a sibling host
    ({"Origin": "https://evil.example"}, False),
    ({"Origin": "null"}, False),
    ({"Sec-Fetch-Site": "cross-site"}, False),
])
def test_changes_come_only_from_the_apps_own_pages(app, headers, ok):
    r = app("kd8bbb").post("/profile/settings", data={}, headers=headers)
    assert (r.status_code != 403) == ok and (("another site" in r.text) != ok)


def test_site_dates_tab_saves_over_club_yaml_and_goes_back(app, tmp_path, monkeypatch):
    """Admin → Site → Dates: a preset or own formats, used at once; a wrong one changes nothing; never other settings."""
    import datetime as dt
    from club_minutes import club as clubmod
    from club_minutes.club import show_date
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c, d = web.archive.club, dt.date(2026, 11, 5)
    c.save_app_admins({"kd8off"})  # (a save reloads the settings: an admin set by hand here wouldn't last)
    admin, member = app("kd8off"), app("kd8bbb")
    try:
        assert member.get("/admin/site/dates").status_code == 404 and member.post("/admin/site/dates", data={"preset": "dayfirst"}).status_code == 404
        assert "US: month first" in admin.get("/admin/site/dates").text and admin.get("/admin/site/nope").status_code == 404
        assert "Saved" in admin.post("/admin/site/dates", data={"preset": "dayfirst"}).text and show_date(d) == "5 Nov 2026"
        assert c.site_settings == {"club": {"date_formats": web.DATE_PRESETS["dayfirst"][1]}}
        bad = admin.post("/admin/site/dates", data={"preset": "custom", "list": "", "document": "%B", "short": "%b", "long": "%B"})
        assert "date_formats" in bad.text and show_date(d) == "5 Nov 2026"  # unchanged
        assert "Dates: Day first" in admin.get("/admin/security/admin").text
        admin.post("/admin/site/dates", data={"preset": "yaml"})
        assert show_date(d) == "Nov 5, 2026" and c.site_settings == {}
        with pytest.raises(ValueError):
            c.save_site_settings({("auth", "rp_id"): "evil.example"})
    finally:
        clubmod.set_dates(None)


def test_site_club_tab_keeps_only_changes_checks_them_and_says_what_isnt_moved(app, tmp_path, monkeypatch):
    """Admin → Site → Club: only what differs from club.yaml is kept; a wrong value changes nothing; a new meeting day
    says what's already planned (the calendar's weekly meeting, chow nights) and links to the meeting."""
    import datetime as dt
    from urllib.parse import unquote
    from club_minutes import club as clubmod, events
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = web.archive.club
    c.save_app_admins({"kd8off"})
    admin, member = app("kd8off"), app("kd8bbb")
    same = {"name": "Test Club", "callsign": "w0tst", "meeting_day": "thursday", "meeting_time": "19:30", "timezone": "America/New_York"}
    try:
        assert member.get("/admin/site/club").status_code == 404 and member.post("/admin/site/club", data=same).status_code == 404
        assert admin.get("/admin/site/club").text.count("From club.yaml") == 5
        admin.post("/admin/site/club", data=same)
        assert c.site_settings == {}  # nothing differs: nothing kept
        for bad, why in (({"callsign": "W0 <b>"}, "callsign"), ({"name": "  "}, "name"), ({"timezone": "Mars/Base"}, "time zone: &#39;mars/base&#39; isn"),
                         ({"meeting_time": "25:99"}, "start time: &#39;25:99&#39;"), ({"meeting_day": "Caturday"}, "meeting day: &#39;caturday&#39;")):
            r = admin.post("/admin/site/club", data={**same, **bad})
            assert "msg err" in r.text and why in r.text.lower() and "club.yaml club" not in r.text and c.site_settings == {}, bad
        c.save_site_settings({("club", "date_formats"): {"list": "%-d %b %Y"}})
        (tmp_path / "x").mkdir(exist_ok=True)
        (events.DATA / "events-meeting-added").unlink()
        events.ensure_meeting(c)
        from club_minutes import chow
        monkeypatch.setattr(chow, "DATA", tmp_path)
        nxt_thu = dt.date.today() + dt.timedelta(days=(3 - dt.date.today().weekday()) % 7 or 7)
        chow.save({"nights": {nxt_thu.isoformat(): {"member": "kd8aaa", "status": "scheduled"}}})
        r = admin.post("/admin/site/club", data={**same, "name": "Test Radio Club", "meeting_day": "Tuesday"}, follow_redirects=False)
        loc = unquote(r.headers["location"])
        assert "weekly meeting on the calendar" in loc and "1 chow night on the old day" in loc and "&meeting=" in loc
        assert c.site_settings["club"] == {"date_formats": {"list": "%-d %b %Y"}, "name": "Test Radio Club", "meeting_day": "Tuesday"}
        assert c.meeting_weekday == 1 and c.info["name"] == "Test Radio Club"
        assert 'href="/calendar/edit?id=' in admin.get(r.headers["location"]).text
        tab = admin.get("/admin/site/club").text
        assert tab.count("Set here") == 2 and tab.count("From club.yaml") == 3  # name and day were changed
        assert admin.get("/admin/site/club?meeting=../x&d=2026-01-01").text.count("/calendar/edit") == 0  # only a real id
        assert "Club settings: name, meeting day" in admin.get("/admin/security/admin").text
        admin.post("/admin/site/club", data={"back": "1"})
        assert c.site_settings == {"club": {"date_formats": {"list": "%-d %b %Y"}}} and c.meeting_weekday == 3  # dates stay
    finally:
        clubmod.set_dates(None)


def test_site_agenda_tab_orders_adds_removes_and_refuses_bad_ones(app, tmp_path, monkeypatch):
    """Admin → Site → Agenda: tick, place and hint each section, add one, and the two reminders; only changes from
    club.yaml are kept; a wrong one changes nothing."""
    import pytest as _pytest
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = web.archive.club
    c.save_app_admins({"kd8off"})
    admin, member = app("kd8off"), app("kd8bbb")

    def form(change=lambda rows: rows, **extra):
        rows = change([dict(r) for r in web._agenda_rows(c)])
        f = {"rows": str(len(rows)), "invite_after_visits": "3", "remind_days_before": "3"}
        for i, r in enumerate(rows):
            f |= {f"name{i}": r["name"], f"hint{i}": r["hint"], f"pos{i}": str(r.get("pos", i + 1))}
            f |= {f"was{i}": "1"} if r.get("was", bool(r["name"])) else {}
            f |= {f"use{i}": "1"} if r["use"] else {}
        return f | extra

    assert member.get("/admin/site/agenda").status_code == 404 and member.post("/admin/site/agenda", data=form()).status_code == 404
    page_ = admin.get("/admin/site/agenda").text
    assert "Old Business" in page_ and "Winners" in page_ and page_.count("From club.yaml") == 3
    assert 'name="remind_days_before" min="0" max="60" value="3"' in page_ and 'name="invite_after_visits" min="1" max="50" value="3"' in page_
    admin.post("/admin/site/agenda", data=form())
    assert c.site_settings == {}  # nothing changed: nothing kept

    def moved(rows):  # New Business before Old Business, a new section with a hint, Financial Report dropped
        for r in rows:
            r["pos"] = {"New Business": 3.5, "Financial Report": 1}.get(r["name"], rows.index(r) + 1)
            r["use"] = r["use"] and r["name"] != "Financial Report"
        rows[-1] |= {"name": "Net Report", "hint": "Who checked in", "was": False, "pos": 2}
        return rows
    assert "Saved" in admin.post("/admin/site/agenda", data=form(moved, invite_after_visits="5")).text
    names = c.sections()
    assert names.index("Net Report") < names.index("New Business") < names.index("Old Business") and "Financial Report" not in names
    assert c.section_hints() == {"Net Report": "Who checked in"} and c.info["invite_after_visits"] == 5
    assert set(c.site_settings["club"]) == {"invite_after_visits"} and "sections" in c.site_settings["meeting"]
    before = c.site_settings
    for bad, why in ((form(lambda r: r[:-1] + [{**r[-1], "name": "Old Business", "was": False}]), "isn&#39;t a usable section name"),
                     (form(lambda r: r[:-1] + [{**r[-1], "name": "Attendance", "was": False}]), "isn&#39;t a usable section name"),
                     (form(lambda r: [{**r[0], "pos": "first"}] + r[1:]), "isn&#39;t a number"),
                     (form(lambda r: [{**x, "use": False} for x in r]), "at least one section"),
                     (form(invite_after_visits="0"), "from 1 to 50"), (form(remind_days_before="soon"), "from 0 to 60")):
        r = admin.post("/admin/site/agenda", data=bad)
        assert "msg err" in r.text and why in r.text and c.site_settings == before, why
    assert "Agenda: sections, invite after visits" in admin.get("/admin/security/admin").text
    admin.post("/admin/site/agenda", data={"back": "1"})
    assert c.site_settings == {} and "Financial Report" in c.sections() and c.info.get("invite_after_visits") is None
    with _pytest.raises(ValueError, match="invite_after_visits"):
        c._configure({"club": {"invite_after_visits": "three"}})  # club.yaml's too: stops the app at start
    c._configure(c.yaml)


def test_site_features_and_bylaws_tabs(app, tmp_path, monkeypatch):
    """Admin → Site → Features and Bylaws: unchanged keeps nothing; turning off what club.yaml turns on wins over it;
    a wrong one changes nothing; Back to club.yaml undoes them."""
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = web.archive.club
    c.save_app_admins({"kd8off"})
    admin, member = app("kd8off"), app("kd8bbb")
    feats = {"chow": "1", "holidays": "us", "elections": "us-oh"}  # as the test club.yaml has them
    cert = {"cert_on": "1", "cert_name": "Bond (Certificate of Indebtedness)", "cert_short": "bond", "cert_note": "Each member buys one."}
    for tab, data in (("features", feats), ("bylaws", cert)):
        assert member.get(f"/admin/site/{tab}").status_code == 404 and member.post(f"/admin/site/{tab}", data=data).status_code == 404
        assert "From club.yaml" in admin.get(f"/admin/site/{tab}").text
        admin.post(f"/admin/site/{tab}", data=data)
        assert c.site_settings == {}, tab  # unchanged: nothing kept
    admin.post("/admin/site/features", data={"holidays": "", "elections": "us-oh", "public_events": "1", "public_chow": "1"})
    assert not c.has("chow") and not c.calendar.get("holidays") and c.calendar["elections"] == "us-oh"
    assert c.info["public_calendars"] == ["events"]  # never chow, even if sent
    assert c.site_settings == {"modules": {"chow": False}, "calendar": {"holidays": ""}, "club": {"public_calendars": ["events"]}}
    before = c.site_settings
    assert "Pick a region" in admin.post("/admin/site/features", data={"holidays": "zz"}).text and c.site_settings == before
    import datetime as dt
    from club_minutes import chow, regions
    rows = lambda *hs: {"hol_rows": str(len(hs) + 1)} | {f"hol_{k}{i}": v for i, h in enumerate(hs) for k, v in h.items()}
    eve = {"name": "Christmas  Eve", "month": "12", "day": "d24", "after": "0"}
    black = {"name": "Black Friday", "month": "11", "day": "w3:4", "after": "1"}  # the day after the 4th Thursday
    admin.post("/admin/site/features", data={"holidays": "Germany: Bayern", "elections": "us-oh", "public_events": "1"} | rows(eve, black))
    assert c.calendar["holidays"] == "de-by" and "Germany: Bayern" in admin.get("/admin/site/features").text  # a name, kept as its code
    assert c.calendar["extra_holidays"] == [{"name": "Christmas Eve", "month": 12, "day": 24, "after": 0},
                                            {"name": "Black Friday", "month": 11, "weekday": 3, "nth": 4, "after": 1}]
    assert chow.holiday(c, dt.date(2026, 12, 24)) == "Christmas Eve" and chow.holiday(c, dt.date(2026, 12, 26)) == "Second Day of Christmas"
    assert chow.holiday(c, dt.date(2024, 11, 29)) == "Black Friday" and chow.holiday(c, dt.date(2024, 11, 22)) is None  # not the 4th Friday
    tab = admin.get("/admin/site/features").text
    assert 'value="w3:4" selected' in tab and 'value="d24" selected' in tab
    c.save_site_settings({k: None for k in web.FEATURE_KEYS if k != ("calendar", "extra_holidays")})
    assert "Back to club.yaml" in admin.get("/admin/site/features").text  # its own holidays alone are something to go back from
    for bad in ({**eve, "day": "x"}, {**eve, "month": "13"}, {**eve, "day": "d30", "month": "2"}, {**black, "day": "w3:5"}):
        r = admin.post("/admin/site/features", data={"holidays": "", "elections": "us-oh"} | rows(bad))
        assert "The club&#39;s own holidays" in r.text and len(c.calendar["extra_holidays"]) == 2, bad
    admin.post("/admin/site/features", data={"holidays": "", "elections": "us-oh", "public_events": "1"} | rows({**eve, "name": ""}, black))
    assert [h["name"] for h in c.calendar["extra_holidays"]] == ["Black Friday"]  # an emptied name removes it
    admin.post("/admin/site/features", data={"holidays": "", "elections": "us-oh", "public_events": "1"} | rows(black, eve) | {"hol_rm0": "1"})
    assert [h["name"] for h in c.calendar["extra_holidays"]] == ["Christmas Eve"]  # Remove ticked: gone on save
    assert "holiday" in __import__("club_minutes.calview", fromlist=["x"]).kinds(c)  # its own holidays alone are enough for the calendar
    admin.post("/admin/site/features", data={"holidays": "", "elections": "us-oh", "public_events": "1"})
    before = c.site_settings
    admin.post("/admin/site/bylaws", data={"ref_dues": "VII", "ref_arrears": "<b>VII.B</b>", "arrears_note": "Only the  Board\nmay suspend."})
    assert c.certificate is None and c.cite("dues") == " (bylaws VII)" and c.bylaws["arrears_note"] == "Only the Board may suspend."
    assert "&lt;b&gt;VII.B&lt;/b&gt;" in admin.get("/admin/site/bylaws").text
    assert "Give the certificate" in admin.post("/admin/site/bylaws", data={"cert_on": "1", "cert_name": " "}).text and c.certificate is None
    assert "Features: chow, public calendars, holidays" in admin.get("/admin/security/admin").text
    for tab in ("features", "bylaws"):
        admin.post(f"/admin/site/{tab}", data={"back": "1"})
    assert c.site_settings == {} and c.has("chow") and c.certificate["short"] == "bond" and c.cite("dues") == ""
