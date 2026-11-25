"""Whole-app security checks: every route for a signed-out visitor, officer pages for a member, members' own-only actions,
post-sign-in redirects, and bad input that used to be a server error."""
import re
import shutil
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import business, chow, web
from club_minutes.auth import BAN_AFTER, Auth, _safe_next
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "n0aaaa", "first": "Amy", "last": "Appleseed", "email": "amy@example.org"},
          {"id": "n0bbbb", "first": "Bob", "last": "Bloggs", "email": "bob@example.org"},
          {"id": "n0offc", "first": "Ola", "last": "Officer", "role": "President", "officer": True, "email": "o@example.org"}]
PUBLIC_GET = {"/login", "/email/confirm", "/favicon.ico", "/brand/theme.css", "/calendar/public", "/calendar/public.ics", "/calendar/feed/x.ics", "/api/roster", "/login/link",
              "/healthz", "/privacy"}  # /privacy: the privacy page (signed out: only what is about visitors)


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
        for p in ("slug", "iid", "eid", "rid", "cid", "sid", "nid", "mid", "tab", "tid", "id", "key", "kind", "what"):
            path = path.replace(f"{{{p}}}", "x")
        path = path.replace("{token}.ics", "x.ics")
        if "{" not in path and not path.startswith(("/static", "/docs", "/openapi")):
            for m in getattr(r, "methods", None) or ():
                if m in ("GET", "POST"):
                    yield m, path


def test_a_signed_out_visitor_reaches_only_the_public_pages(app):
    anon = TestClient(web.app, client=("203.0.113.5", 5000))
    seen = 0
    for m, path in routes():
        if path.startswith(("/login/email", "/auth/passkey/login/", "/email/confirm")):  # public by design, behind a one-time token (tested on their own)
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
    assert app("n0aaaa").get(path, follow_redirects=False).status_code in (403, 404)


def test_members_change_only_their_own_things(app):
    s = business.load()
    x = business.add(s, "Bob's item", "n0offc", owner="n0bbbb")
    c = business.comment(s, x["id"], "n0bbbb", "Bob's comment")
    business.save(s)
    cs = chow.load()
    cs["swaps"].append({"id": "sw1", "requester": "n0offc", "with": "n0bbbb", "mine": "2030-01-03", "theirs": "2030-01-10", "status": "pending"})
    chow.save(cs)
    amy = app("n0aaaa")
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
    amy = app("n0aaaa")
    for slug in ("..", ".", "%2e%2e"):
        assert amy.get(f"/meetings/{slug}/original.pdf", follow_redirects=False).status_code == 404


def test_a_slow_profile_save_doesnt_hold_up_everyone_else(app, monkeypatch):
    """Saving a profile writes the Google roster sheet, which can take seconds; other requests are answered meanwhile."""
    import asyncio
    import time

    import httpx

    from club_minutes import profile
    monkeypatch.setattr(profile, "change", lambda *a, **k: time.sleep(2))  # the sheet, slow today
    cookie = {web.COOKIE: web.authn.new_session("n0aaaa")}

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
    amy = app("n0aaaa")
    big = b"x" * (limits.max_upload() + limits.FORM_ROOM + 1)
    r = amy.post("/profile/photo", files={"photo": ("p.jpg", big, "image/jpeg")})
    assert r.status_code == 413 and "1 MB at most" in r.text  # turned away before it's read
    def chunks():  # no size given: counted as it arrives
        for _ in range(3):
            yield b"x" * (limits.max_upload() + limits.FORM_ROOM)
    r = amy.post("/profile/photo", content=chunks(), headers={"content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413 and "1 MB at most" in r.text
    with pytest.raises(ValueError, match="1 MB at most"):
        avatars.save("n0aaaa", b"x" * (limits.max_upload() + 1))
    assert amy.post("/admin/security/limits", data={"upload_mb": "50"}).status_code == 404  # admins only
    assert limits.upload_mb() == 1
    (tmp_path / "limits.json").write_text("not json")  # a damaged file: the default, not a crash
    assert limits.upload_mb() == limits.DEFAULT_MB


def test_an_admin_in_admin_mode_sets_the_upload_limit(tmp_path, monkeypatch):
    from club_minutes import limits
    monkeypatch.setattr(limits, "DATA", tmp_path)
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    club.admins = {"n0offc"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tok = a.new_session("n0offc")
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, tok)
    assert t.post("/admin/security/limits", data={"upload_mb": "25"}).status_code == 403  # admin mode first
    a.set_admin(tok, 60)
    r = t.post("/admin/security/limits", data={"upload_mb": "25"})
    assert r.status_code == 200 and "Uploads up to 25 MB." in r.text and limits.upload_mb() == 25
    assert 'value="25"' in t.get("/admin/security/uploads").text
    from club_minutes import pictures
    monkeypatch.setattr(pictures, "DATA", tmp_path)
    (tmp_path / "pictures").mkdir(exist_ok=True)
    (tmp_path / "pictures" / "a.jpg").write_bytes(b"x" * 3_000_000)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**11, 0, 41 * 10**9))
    page = t.get("/admin/security/uploads").text
    assert "Pictures take 3 MB. The disk has 41.0 GB free." in page and "nearly full" not in page
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**10, 9 * 10**9, 5 * 10**8))
    page = t.get("/admin/security/uploads").text
    assert "The disk has 500 MB free." in page and "nearly full" in page


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
        [sys.executable, "-c", ("import time; from club_minutes.club import roster_locked\n"
                                "roster_locked(lambda: (print('held', flush=True), time.sleep(1.5)))()")],
        env={**os.environ, "CLUB_MINUTES_DATA": str(tmp_path)}, stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"  # the "sync" has the lock
    t0 = time.monotonic()
    assert clubmod.roster_locked(lambda: clubmod.roster_locked(lambda: "nested ok")())() == "nested ok"  # waits; nesting is fine
    assert time.monotonic() - t0 > 1.0
    holder.wait(5)



def test_admin_address_members_tab_and_a_page_for_not_found(app, monkeypatch):
    """/admin goes to Security for an admin and is not found for a member; a browser gets a page, scripts get JSON."""
    import time as _t
    web.archive.club.admins = {"n0offc"}
    admin, member = app("n0offc"), app("n0bbbb")
    assert admin.get("/admin/", follow_redirects=False).headers["location"].startswith("/admin/confirm")  # admin mode first
    old = admin.get("/admin/security/backups?note=hi", follow_redirects=False)  # Backups' old address, in older emails
    assert old.status_code == 301 and old.headers["location"] == "/admin/backups?note=hi"
    assert member.get("/admin/security/backups").status_code == 404
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    hub = admin.get("/admin").text
    assert all(f'href="/admin/{t}"' in hub for t in ("security", "updates", "site", "email")) and "failed sign-in" in hub
    assert "purge-hold" not in hub
    monkeypatch.setattr(web.prefs, "purge_hold", lambda: ["n0bbbb"])
    assert 'action="/admin/purge-hold/confirm"' in admin.get("/admin").text  # the notice, with its Confirm button
    assert member.get("/admin").status_code == 404
    page_ = member.get("/no-such-page", headers={"Accept": "text/html"})
    assert page_.status_code == 404 and "Page not found" in page_.text
    assert member.get("/no-such-page", headers={"Accept": "application/json"}).json() == {"detail": "Not Found"}
    web.authn.db.execute("insert into passkeys (id, member, public_key, sign_count, label, created) values ('pk1', 'n0bbbb', x'00', 0, 'phone', ?)",
                         (int(_t.time()),))
    got = web.authn.by_member()
    assert got["n0bbbb"]["passkeys"] == 1 and got["n0bbbb"]["devices"] == 1 and "last" not in got["n0bbbb"]


def test_admins_add_and_remove_admins_in_the_app(app, tmp_path, monkeypatch):
    """Admin → Security → Admins: adding needs a fresh confirmation; club.yaml's stay; never the last admin; others are told."""
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(web.authmod, "send_email", lambda to, subject, body, **k: sent.append((to, subject)) or 1)
    c = web.archive.club
    c.admins = {"n0offc"}  # club.yaml's
    admin, member = app("n0offc"), app("n0bbbb")
    tok = admin.cookies.get(web.COOKIE)
    assert member.post("/admin/security/admins/add", data={"member": "n0bbbb"}).status_code == 404  # not an admin
    assert admin.post("/admin/security/admins/add", data={"member": "n0bbbb"}).status_code == 403  # admin mode first
    web.authn.set_admin(tok, 50)  # an hour from confirming, so confirmed 10 minutes ago
    r = admin.post("/admin/security/admins/add", data={"member": "n0bbbb"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/confirm?next=" + web.quote("/admin/security/admins?add=n0bbbb") and not c.app_admins
    web.authn.set_admin(tok, 60)  # just confirmed
    assert admin.post("/admin/security/admins/add", data={"member": "nobody"}).status_code == 400
    page_ = admin.post("/admin/security/admins/add", data={"member": "n0bbbb"}).text
    assert "Bob Bloggs is now an admin." in page_ and c.is_admin("n0bbbb") and c.app_admins == {"n0bbbb"}
    assert sent == [(["bob@example.org"], "Bob Bloggs is now an admin")]  # not to whoever made the change
    assert "Made Bob Bloggs an admin" in admin.get("/admin/security/admin").text
    assert admin.post("/admin/security/admins/remove", data={"member": "n0offc"}).status_code == 400  # club.yaml's: on the server
    assert "Bob Bloggs is no longer an admin." in admin.post("/admin/security/admins/remove", data={"member": "n0bbbb"}).text
    assert not c.is_admin("n0bbbb") and sent[-1] == (["bob@example.org"], "Bob Bloggs is no longer an admin")
    c.admins = set()  # only app admins now: the last one can't go
    c.save_app_admins({"n0offc"})
    assert admin.post("/admin/security/admins/remove", data={"member": "n0offc"}).status_code == 400 and c.is_admin("n0offc")


@pytest.mark.parametrize("headers, ok", [
    ({}, True),  # scripts and older browsers: the SameSite cookie still covers them
    ({"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}, True),
    ({"Origin": "https://meet.example-tailnet.ts.net", "Sec-Fetch-Site": "same-site"}, False),  # a sibling host
    ({"Origin": "https://evil.example"}, False),
    ({"Origin": "null"}, False),
    ({"Sec-Fetch-Site": "cross-site"}, False),
])
def test_changes_come_only_from_the_apps_own_pages(app, headers, ok):
    r = app("n0bbbb").post("/profile/settings", data={}, headers=headers)
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
    c.save_app_admins({"n0offc"})  # (a save reloads the settings: an admin set by hand here wouldn't last)
    admin, member = app("n0offc"), app("n0bbbb")
    try:
        assert member.get("/admin/site/dates").status_code == 404 and member.post("/admin/site/dates", data={"preset": "dayfirst"}).status_code == 404
        with monkeypatch.context() as m:  # an admin with admin mode off is told to turn it on, as on the other admin pages
            m.setattr(web, "admin_mode", lambda request: False)
            r = admin.post("/admin/site/dates", data={"preset": "dayfirst"})
            assert r.status_code == 403 and "Turn on admin mode first" in r.text and c.site_settings == {}
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


def test_site_club_tab_keeps_only_changes_checks_them_and_moves_the_meeting(app, tmp_path, monkeypatch):
    """Admin → Site → Club: only what differs from club.yaml is kept; a wrong value changes nothing; a new meeting day
    moves the calendar's weekly meeting from the Starting date on (earlier dates stay) and says which chow nights aren't moved."""

    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = web.archive.club
    c.save_app_admins({"n0offc"})
    admin, member = app("n0offc"), app("n0bbbb")
    same = {"name": "Test Club", "callsign": "w0test", "ham": "1", "meeting_day": "thursday", "meeting_time": "19:30", "timezone": "America/New_York"}
    try:
        assert member.get("/admin/site/club").status_code == 404 and member.post("/admin/site/club", data=same).status_code == 404
        assert "Changed here" not in admin.get("/admin/site/club").text  # tagged only once changed here
        admin.post("/admin/site/club", data=same)
        assert c.site_settings == {}  # nothing differs: nothing kept
        for bad, why in (({"callsign": "W0 <b>"}, "callsign"), ({"name": "  "}, "name"), ({"timezone": "Mars/Base"}, "time zone: &#39;mars/base&#39; isn")):
            r = admin.post("/admin/site/club", data={**same, **bad})
            assert "msg err" in r.text and why in r.text.lower() and "club.yaml club" not in r.text and c.site_settings == {}, bad
        admin.post("/admin/site/club", data={k: v for k, v in same.items() if k != "ham"} | {"callsign": ""})  # unticked, no club call
        assert "ham" not in c.site_settings["club"] and not c.ham  # the default without a callsign
        admin.post("/admin/site/club", data=same | {"callsign": ""})  # ticked, no club call: stays on
        assert c.site_settings["club"]["ham"] is True and c.ham
        admin.post("/admin/site/club", data={k: v for k, v in same.items() if k != "ham"})  # unticked, with a club call: stays off
        assert c.site_settings["club"]["ham"] is False and not c.ham
        assert 'id="c-call"' not in admin.get("/admin/site/club").text  # off: no callsign field...
        admin.post("/admin/site/club", data={k: v for k, v in same.items() if k not in ("ham", "callsign")})
        assert c.info["callsign"] == "W0TEST" and not c.ham  # ...and saving without it keeps the club's callsign
        admin.post("/admin/site/club", data={k: v for k, v in same.items() if k != "callsign"})  # ticked again, still no field
        assert c.info["callsign"] == "W0TEST" and c.ham and 'id="c-call"' in admin.get("/admin/site/club").text
        admin.post("/admin/site/club", data=same)
        assert c.site_settings == {} and c.ham
        c.save_site_settings({("club", "date_formats"): {"list": "%-d %b %Y"}})
        admin.post("/admin/site/club", data={**same, "name": "Test Radio Club"})
        assert c.site_settings["club"] == {"date_formats": {"list": "%-d %b %Y"}, "name": "Test Radio Club"} and c.info["name"] == "Test Radio Club"
        tab = admin.get("/admin/site/club").text
        assert "Meeting nights are set on the" in tab and 'name="meeting_day"' not in tab and 'id="c-tz"' in tab
        assert "Club settings: name" in admin.get("/admin/security/admin").text
        admin.post("/admin/site/club", data={"back": "1"})
        assert c.site_settings == {"club": {"date_formats": {"list": "%-d %b %Y"}}}  # dates stay
        # short or full names, site-wide: full saved and used; a bad value refused; short again (club.yaml's) keeps nothing
        short = c.shown_name("n0bbbb")
        admin.post("/admin/site/club", data={**same, "names": "full"})
        assert c.site_settings["club"]["names"] == "full" and c.shown_name("n0bbbb") == c.name("n0bbbb") != short
        assert "msg err" in admin.post("/admin/site/club", data={**same, "names": "<b>"}).text and c.info["names"] == "full"
        admin.post("/admin/site/club", data={**same, "names": "short"})
        assert "names" not in c.site_settings["club"] and c.shown_name("n0bbbb") == short
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
    c.save_app_admins({"n0offc"})
    admin, member = app("n0offc"), app("n0bbbb")

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
    assert "Old Business" in page_ and "Winners" in page_ and "Changed here" not in page_
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
    # a reminder changed here alone shows "Changed here" and Back to club.yaml (they looked for the wrong key)
    admin.post("/admin/site/agenda", data=form(remind_days_before="5"))
    page_ = admin.get("/admin/site/agenda").text
    assert page_.count("Changed here") == 1 and "Back to club.yaml" in page_
    admin.post("/admin/site/agenda", data={"back": "1"})
    # quorum: a percent and/or officers (empty: off); bad values refused; only a change from club.yaml is kept
    assert c.quorum is None
    admin.post("/admin/site/agenda", data=form(quorum_percent="30", quorum_officers="2"))
    assert c.site_settings == {"meeting": {"quorum": {"percent": 30, "officers": 2}}} and c.quorum == {"percent": 30, "officers": 2}
    assert 'name="quorum_percent" min="1" max="100" value="30"' in admin.get("/admin/site/agenda").text
    for bad in ("0", "101", "x", "-1", "3.5"):
        assert "msg err" in admin.post("/admin/site/agenda", data=form(quorum_percent=bad)).text
    assert c.quorum == {"percent": 30, "officers": 2}
    admin.post("/admin/site/agenda", data=form(quorum_officers="2"))
    assert c.quorum == {"percent": None, "officers": 2}
    admin.post("/admin/site/agenda", data=form())
    assert c.site_settings == {} and c.quorum is None
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
    c.save_app_admins({"n0offc"})
    admin, member = app("n0offc"), app("n0bbbb")
    feats = {"chow": "1", "holidays": "us", "elections": "us-oh"}  # as the test club.yaml has them
    cert = {"cert_on": "1", "cert_name": "Bond (Certificate of Indebtedness)", "cert_short": "bond", "cert_note": "Each member buys one."}
    for tab, data in (("features", feats), ("bylaws", cert)):
        assert member.get(f"/admin/site/{tab}").status_code == 404 and member.post(f"/admin/site/{tab}", data=data).status_code == 404
        assert "Changed here" not in admin.get(f"/admin/site/{tab}").text
        admin.post(f"/admin/site/{tab}", data=data)
        assert c.site_settings == {}, tab  # unchanged: nothing kept
    admin.post("/admin/site/features", data={"holidays": "", "elections": "us-oh", "public_events": "1", "public_chow": "1"})
    assert not c.has("chow") and not c.calendar.get("holidays") and c.calendar["elections"] == "us-oh"
    assert c.info["public_calendars"] == ["events"]  # never chow, even if sent
    assert c.site_settings == {"modules": {"chow": False}, "calendar": {"holidays": ""}, "club": {"public_calendars": ["events"]}}
    before = c.site_settings
    assert "Pick a region" in admin.post("/admin/site/features", data={"holidays": "zz"}).text and c.site_settings == before
    import datetime as dt

    from club_minutes import chow
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


def test_a_renamed_setting_keeps_working_and_admins_are_told(app, monkeypatch, tmp_path):
    """docs/developers.md → Compatibility: an old club.yaml name is read as the new one until 2.0, with a line on Admin."""
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "RENAMED", {("club", "old_invite"): ("club", "invite_after_visits")})
    y = tmp_path / "club.yaml"
    y.write_text((Path(__file__).parent / "fixtures" / "test-club.yaml").read_text().replace("club: {", "club: {old_invite: 7, ", 1))
    c = clubmod.Club(y)
    assert c.info["invite_after_visits"] == 7 and "old_invite" not in c.info
    assert len(c.renamed) == 1 and "is now called club: invite_after_visits:" in c.renamed[0]
    monkeypatch.setattr(web.archive.club, "renamed", c.renamed)
    web.archive.club.admins = {"n0offc"}
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    assert "works until version 2.0" in app("n0offc").get("/admin").text


def test_site_offices_and_privacy_tabs(app, tmp_path, monkeypatch):
    """Admin → Site → Roles, its offices table: the club's offices (officer or board seat, seats), over
    club.yaml's; unchanged keeps nothing; an office someone holds can't be removed or renamed. Privacy: names never
    recorded. Members get nothing."""
    from club_minutes import club as clubmod
    from club_minutes import offices
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = web.archive.club
    c.save_app_admins({"n0offc"})
    c.members["n0offc"].role = "Secretary"
    admin, member = app("n0offc"), app("n0bbbb")
    for tab in ("roles", "privacy"):
        assert member.get(f"/admin/site/{tab}").status_code == 404 and member.post(f"/admin/site/{tab}", data={"back": "1"}).status_code == 404
    page_ = admin.get("/admin/site/roles").text
    assert 'value="President"' in page_ and 'value="Secretary" maxlength="60" placeholder="" aria-label="Office" readonly' in page_
    rows = lambda *rs: {"rows": str(len(rs))} | {f"{k}{i}": v for i, r in enumerate(rs) for k, v in r.items()}
    four = [{"title": t, "officer": "1", "seats": "1"} for t in ("President", "Vice President", "Secretary", "Treasurer")]
    admin.post("/admin/site/offices", data=rows(*four))
    assert c.site_settings == {}  # as club.yaml (the defaults): nothing kept
    trustee = {"title": "  Trustee ", "seats": "3"}  # no officer tick: a board seat
    admin.post("/admin/site/offices", data=rows(*four, trustee))
    assert offices.offices(c)[-1] == {"title": "Trustee", "officer": False, "seats": 3} and "roster_keepers" not in c.site_settings[""]
    for bad, msg in (([*four, {"title": "president", "officer": "1", "seats": "1"}], "listed twice"),
                     ([*four, {"title": "Webmaster", "seats": "0"}], "1 to 20"),
                     ([o for o in four if o["title"] != "Secretary"], "Secretary is held"),
                     ([{**o, "title": "Clerk"} if o["title"] == "Secretary" else o for o in four], "Secretary is held"),
                     ([], "at least one office")):
        before = c.site_settings
        r = admin.post("/admin/site/offices", data=rows(*bad))
        assert msg in r.text and c.site_settings == before, msg
    assert "Offices: offices" in admin.get("/admin/security/admin").text
    admin.post("/admin/site/offices", data={"back": "1"})
    assert c.site_settings == {} and len(offices.offices(c)) == 4
    admin.post("/admin/site/privacy", data={"names": "Jane  Roe\n\nJohn Q Public\nJane Roe"})
    assert c.never_record == ["Jane Roe", "John Q Public"] and c.is_redacted("Thanks to Jane Roe for the coffee")
    assert "Jane Roe\nJohn Q Public</textarea>" in admin.get("/admin/site/privacy").text
    admin.post("/admin/site/privacy", data={"back": "1"})
    assert c.never_record == ["Tiny Visitor"] and not c.is_redacted("Thanks to Jane Roe")  # club.yaml's own list again


def _roles_form(c, **change) -> dict:
    """The Roles form as the page sends it, with `change` ({key: {field: value}}) on top."""
    out = {"key": []}
    for k, r in c.role_table().items():
        r = {**r, **change.get(k, {})}
        out["key"].append(k)
        out |= {f"name.{k}": r["name"], f"perms.{k}": r["perms"], f"offices.{k}": r["offices"], f"members.{k}": r["members"]}
    return out


def test_site_roles(app, tmp_path, monkeypatch):
    """Admin → Site → Roles: what each role allows, the offices that give it and the members picked, all editable by an
    admin; the club's own roles added, renamed, removed; a built-in put back. Saved in roles.json and used at once,
    logged. Members get 404. Every check goes through Club.may, so a role's permissions are the only way in."""
    import json
    import os
    from pathlib import Path

    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = web.archive.club
    c.save_app_admins({"n0offc"})
    admin, member = app("n0offc"), app("n0bbbb")
    assert member.get("/admin/site/roles").status_code == 404
    assert member.post("/admin/site/roles", data=_roles_form(c, minutes_editor={"members": ["n0bbbb"]})).status_code == 404
    assert not c.members["n0bbbb"].minutes_editor
    page_ = admin.get("/admin/site/roles").text
    assert 'name="name.minutes_editor" value="Meeting leader"' in page_ and 'name="members.calendar_editor" value="n0bbbb"' in page_
    # picking a member, as 1.0 did (roles.json keeps 1.0's keys, so a rollback still reads it)
    admin.post("/admin/site/roles", data=_roles_form(c, minutes_editor={"members": ["n0bbbb", "nobody"]}))
    rj = json.loads((tmp_path / "roles.json").read_text())
    assert rj["minutes_editor"] == ["n0bbbb"] and rj["table"] == {}  # every built-in as it ships
    assert c.members["n0bbbb"].minutes_editor and c.can_lead("n0bbbb") and c.can_edit_calendar("n0bbbb")
    assert not c.may("n0bbbb", "contacts") and not c.may("n0bbbb", "money") and c.roles("n0bbbb") == ["minutes_editor"]
    assert "Meeting leader members: +" in admin.get("/admin/security/admin").text
    fresh = clubmod.Club(Path(os.environ["CLUB_MINUTES_CONFIG"]))  # a restart: roles.json wins over the roster's ticks
    assert not fresh.members["doe"].minutes_editor  # ticked in club.yaml's members, not in roles.json
    # take a permission away from an office's role; a role of the club's own, given by hand
    admin.post("/admin/site/roles", data=_roles_form(c, officer={"perms": ["lead", "chow", "contacts", "oversee", "moderate", "treasurer"]})
               | {"new": "Net manager"})
    net = next(k for k, r in c.role_table().items() if r["name"] == "Net manager")
    assert c.role_table()["officer"]["edited"] and "calendar" not in c.role_table()["officer"]["perms"]
    admin.post("/admin/site/roles", data=_roles_form(c, **{net: {"perms": ["calendar"], "members": ["n0bbbb"]}}))
    assert c.roles("n0bbbb") == ["minutes_editor", net]
    for bad, msg in (({"officer": {"name": "Meeting leader"}}, "Two roles can&#39;t both be called Meeting leader"),
                     ({"officer": {"name": "admin"}}, "Two roles can&#39;t both be called admin"),
                     ({"treasurer": {"perms": ["treasurer"]}}, "No role would be allowed to record money")):
        before = (tmp_path / "roles.json").read_text()
        assert msg in admin.post("/admin/site/roles", data=_roles_form(c, **bad)).text, msg
        assert (tmp_path / "roles.json").read_text() == before
    form = _roles_form(c)
    form["key"].remove("treasurer")  # a hand-made request leaving a built-in out keeps it as it was
    admin.post("/admin/site/roles", data=form | {"remove.officer": "1"})
    assert {"treasurer", "officer"} <= set(c.role_table())
    # money brings the Treasurer pages; remove the club's own role; put a built-in back
    admin.post("/admin/site/roles", data=_roles_form(c, **{net: {"perms": ["money"]}}))
    assert c.may("n0bbbb", "treasurer") and c.may("n0bbbb", "money")
    admin.post("/admin/site/roles", data=_roles_form(c) | {f"remove.{net}": "1"})
    assert net not in c.role_table() and not c.may("n0bbbb", "money")
    admin.post("/admin/site/roles", data={"back": "officer"})
    assert not c.role_table()["officer"]["edited"] and "calendar" in c.role_table()["officer"]["perms"]
    assert c.members["n0bbbb"].minutes_editor  # picks stay
    with pytest.raises(KeyError):
        c.may("n0bbbb", "everything")  # a misspelled permission fails loudly, never quietly "no"


def test_roles_come_from_whole_office_titles(app, tmp_path, monkeypatch):
    """Stage test of 1.0.0, problem 2: a title that only contains an office's name gives nothing; the office itself
    gives its roles, any capitals; a combined office gives both roles only when it's set up that way. The roster
    emails and the arrears email follow the roles, not the words."""
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    c = web.archive.club
    m = c.members["n0bbbb"]
    for title in ("Assistant Treasurer", "Secretary emeritus", "Treasurer's helper"):
        m.role = title
        assert c.roles("n0bbbb") == [] and not c.may("n0bbbb", "money") and not c.may("n0bbbb", "members"), title
        assert "n0bbbb" not in c.secretaries and not c.holds_office("n0bbbb")
    m.officer = m.board = True  # the roster's old ticks give nothing either: only offices and picks
    assert c.roles("n0bbbb") == [] and all("n0bbbb" not in r["members"] for r in c.role_table().values())
    m.role = "Net Manager, treasurer"
    assert c.roles("n0bbbb") == ["officer", "board", "treasurer", "roster_keeper"] and c.may("n0bbbb", "money")
    m.role = "secretary"
    assert "n0bbbb" in c.secretaries and c.may("n0bbbb", "members") and not c.may("n0bbbb", "money")
    c.offices = ["President", "Secretary-Treasurer"]
    assert c.roles("n0bbbb") == []  # Secretary is no longer an office here
    m.role = "Secretary-Treasurer"
    assert not c.may("n0bbbb", "money")  # its name alone gives nothing...
    t = c.role_table()
    c.save_roles({k: {**r, "offices": r["offices"] + ["Secretary-Treasurer"] * (k in ("treasurer", "secretary", "roster_keeper"))}
                  for k, r in t.items()})
    assert c.may("n0bbbb", "money") and "n0bbbb" in c.secretaries  # ...set up with both, it gives both


def test_every_admin_address_is_not_found_for_a_non_admin(tmp_path, monkeypatch):
    """FIXES → i: a signed-in member who isn't an admin gets the same 404 on every /admin address as on one that doesn't
    exist, so the admin pages' addresses tell them nothing (admins with admin mode off still get the confirm page)."""
    import re

    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    from club_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=[
        {"id": "n0admn", "first": "Ann", "last": "Admin", "callsign": "N0ADMN", "email": "ann@example.org"},
        {"id": "n0cccc", "first": "Bob", "last": "Member", "callsign": "N0CCCC", "email": "bob@example.org"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("n0cccc"))
    tried, wrong = 0, []
    for r in web.app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/admin"):
            path = re.sub(r"\{[^}]+\}", "x", r.path)
            for method in sorted(r.methods - {"HEAD"}):
                tried += 1
                got = c.request(method, path, follow_redirects=False).status_code
                if got != 404:
                    wrong.append(f"{method} {path}: {got}")
    assert tried > 30 and not wrong, wrong


def test_moderating_is_its_own_permission(app, tmp_path, monkeypatch):
    """Oversee (Roster changes) and moderate (pictures, the calendar log, any business item) are apart: a role with
    only moderate takes down a picture and sees the calendar log, but not Roster changes."""
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    c = web.archive.club
    t = c.role_table()
    c.save_roles({**t, "mods": {"name": "Moderator", "perms": ["moderate"], "offices": [], "members": ["n0bbbb"]}})
    m = app("n0bbbb")
    assert m.get("/calendar/changes").status_code == 200 and m.get("/roster/changes").status_code == 403
    assert c.may("n0bbbb", "moderate") and not c.may("n0bbbb", "oversee")


def test_a_roles_save_from_an_out_of_date_page_is_refused(app, tmp_path, monkeypatch):
    """Data sweep: admin A's open page would have dropped a role admin B added meanwhile."""
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = web.archive.club
    c.save_app_admins({"n0offc"})
    admin = app("n0offc")
    stamp = re.search(r'name="stamp" value="(\w+)"', admin.get("/admin/site/roles").text).group(1)
    admin.post("/admin/site/roles", data=_roles_form(c) | {"new": "Net manager", "stamp": stamp})  # B adds a role
    r = admin.post("/admin/site/roles", data=_roles_form(c, ) | {"stamp": stamp}, follow_redirects=False)  # A, from the old page
    assert "changed%20the%20roles" in r.headers["location"] and any(x["name"] == "Net manager" for x in c.role_table().values())


def test_a_new_office_says_who_already_had_its_title(app, tmp_path, monkeypatch):
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = web.archive.club
    c.save_app_admins({"n0offc"})
    c.members["n0bbbb"].role = "Webmaster"  # a free title a roster keeper typed
    rows = {"rows": "5"} | {f"title{i}": t for i, t in enumerate(("President", "Vice President", "Secretary", "Treasurer", "Webmaster"))} \
        | {f"seats{i}": "1" for i in range(5)} | {f"officer{i}": "1" for i in range(4)}
    r = app("n0offc").post("/admin/site/offices", data=rows, follow_redirects=False)
    assert "already%20had" in r.headers["location"] and "Webmaster" in urllib.parse.unquote(r.headers["location"])


def test_the_sign_in_pages_note_is_a_code(app):
    """Privacy sweep: ?note= showed any text from the link on the sign-in page (escaped, but phishing text)."""
    anon = TestClient(web.app, client=("203.0.113.5", 5000))
    assert "Your email has changed" in anon.get("/login?note=email").text
    assert "Call+555-0100" not in anon.get("/login?note=Call+555-0100+now").text and "555-0100" not in anon.get("/login?note=Call%20555-0100").text
PUBLIC_POST = {"/login/email", "/auth/passkey/login/begin", "/auth/passkey/login/finish"}  # sign-in: a one-time token or a passkey


def test_every_post_refuses_a_request_from_another_site(app):
    """A page on another site (or a sibling host) can't make a signed-in member change anything: no POST route is left out."""
    t = app("n0bbbb")
    seen = 0
    for m, path in routes():
        if m != "POST" or path in PUBLIC_POST or path.startswith(("/login/email", "/auth/passkey/login/", "/email/confirm")):
            continue
        for h in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            r = t.post(path, data={"x": "1"}, headers=h, follow_redirects=False)
            assert r.status_code == 403 and "another site" in r.text, (path, h, r.status_code)
        seen += 1
    assert seen > 50


def test_the_public_routes_are_a_fixed_list(app):
    """A new page or action a signed-out visitor can reach fails here until it's added to the lists on purpose."""
    anon = TestClient(web.app, client=("203.0.113.7", 5000))
    reached = set()
    for m, path in routes():
        if m == "POST" and path.startswith(("/login/email", "/auth/passkey/login/", "/email/confirm")):
            continue  # the sign-in ones, listed in PUBLIC_POST
        r = anon.request(m, path, data={"x": "1"} if m == "POST" else None, follow_redirects=False)
        if not (r.status_code in (303, 401, 404) and r.headers.get("location", "/login").startswith("/login")):
            reached.add((m, path))
    assert {p for m, p in reached if m == "GET"} <= PUBLIC_GET, sorted(reached)
    assert {p for m, p in reached if m == "POST"} <= PUBLIC_POST, sorted(reached)


def test_a_sign_in_link_request_answers_and_counts_the_same_for_a_stranger_and_a_member(app, tmp_path):
    """Same redirect, and the same effect on bans, so neither the reply nor getting banned tells whether an address is on the roster."""
    seen = set()
    for address, ip in (("amy@example.org", "8.8.8.8"), ("stranger@example.org", "1.1.1.1")):
        c = TestClient(web.app, client=(ip, 5000))
        replies = {(r.status_code, r.headers["location"], r.text) for r in
                   (c.post("/login/email", data={"email": address}, follow_redirects=False) for _ in range(BAN_AFTER + 3))}
        assert not web.authn.banned(ip), address
        seen |= replies
    assert len(seen) == 1


def _log_calls():
    import ast
    for p in Path(web.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id in ("log", "_log", "logging", "logger") and node.func.attr in ("debug", "info", "warning", "error", "exception", "critical"):
                yield p, node


def test_the_log_never_gets_an_email_body():
    import ast
    calls = list(_log_calls())
    assert len(calls) > 20  # the scan finds the app's log calls
    for p, node in calls:
        names = {n.id for a in (*node.args, *(k.value for k in node.keywords)) for n in ast.walk(a) if isinstance(n, ast.Name)}
        assert not any("body" in n.lower() for n in names), f"{p.name}:{node.lineno} logs an email body"


def test_reply_to_only_comes_from_reply_address():
    """Outside auth.py, an email's Reply-To is console.reply_address(...) (the author's own email only if they share it)."""
    import ast
    src = Path(web.__file__).parent
    asked = 0
    for p in src.glob("*.py"):
        if p.name == "auth.py":
            continue
        tree = ast.parse(p.read_text())
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                name = getattr(call.func, "id", getattr(call.func, "attr", ""))
                if name == "send_to_members":  # its reply_to argument comes from reply_address in the same function
                    asked += 1
                    assert "reply_address(" in ast.get_source_segment(p.read_text(), fn), f"{p.name}:{call.lineno}"
                if name != "send_to_members" and any(k.arg == "reply_to" for k in call.keywords):
                    assert fn.name == "send_to_members" and all(isinstance(k.value, ast.Name) for k in call.keywords if k.arg == "reply_to"), f"{p.name}:{call.lineno}"
    assert asked >= 2  # minutes and corrected minutes


URL_SECRET = re.compile(r"[?&;](jwt|t|token|key|secret|email\w*|addr\w*)=")
# where the app builds a URL with a token or an address in it, and why that's safe
URL_SECRETS_OK = {
    ("auth.py", "?t="): "one-time sign-in and email-confirm links, emailed (and hidden in logs by hide_tokens)",
    ("web.py", "?t="): "one-time admin-mode and recovery links, emailed",
    ("web.py", "&email_sent="): "the profile page's note: an address in our own page's URL; the default Referrer-Policy is same-origin",
}


def test_no_token_or_address_in_a_url_without_a_reason():
    found = {}
    for p in Path(web.__file__).parent.rglob("*"):
        if p.suffix in (".py", ".html", ".js") and "static" not in p.parts:
            for i, line in enumerate(p.read_text().splitlines(), 1):
                for m in URL_SECRET.finditer(line):
                    found.setdefault((p.name, m.group(0)), []).append(i)
    unknown = {k: v for k, v in found.items() if k not in URL_SECRETS_OK}
    assert not unknown, f"a token or address in a URL: add a reason to URL_SECRETS_OK, or hide it: {unknown}"


def test_access_log_hides_tokens():
    import logging
    rec = lambda path: logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s HTTP/%s" %d', ("10.0.0.1:5", "GET", path, "1.1", 200), None)
    for path, shown in (("/calendar/feed/AbC_d-9xYz.ics?show=chow", "/calendar/feed/….ics?show=chow"),
                        ("/login/link?t=s3cr3t-tok_en", "/login/link?t=…"), ("/admin/confirm/link?next=/x&t=abc", "/admin/confirm/link?next=/x&t=…"),
                        ("/login?next=%2Fadmin%2Fconfirm%2Flink%3Ft%3DAb-9_x", "/login?next=%2Fadmin%2Fconfirm%2Flink%3Ft%3D…"),
                        ("/minutes?tab=open", "/minutes?tab=open")):
        r = rec(path)
        assert web._hide_tokens(r) and r.getMessage() == f'10.0.0.1:5 - "GET {shown} HTTP/1.1" 200', path
