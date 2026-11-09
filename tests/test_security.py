"""Whole-app security checks: every route for a signed-out visitor, officer pages for a member, members' own-only actions,
post-sign-in redirects, and bad input that used to be a server error."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from w8lbz_minutes import business, chow, web
from w8lbz_minutes.auth import Auth, _safe_next
from w8lbz_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "kd8aaa", "first": "Amy", "last": "Able", "email": "amy@example.org"},
          {"id": "kd8bbb", "first": "Bob", "last": "Baker", "email": "bob@example.org"},
          {"id": "kd8off", "first": "Ola", "last": "Officer", "officer": True, "email": "o@example.org"}]
PUBLIC_GET = {"/login", "/favicon.ico", "/calendar/public", "/calendar/public.ics", "/calendar/feed/x.ics", "/api/roster", "/login/link",
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
    from w8lbz_minutes import profile
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
    from w8lbz_minutes import avatars, limits
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
    from w8lbz_minutes import limits
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
