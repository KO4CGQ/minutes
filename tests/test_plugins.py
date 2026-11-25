"""Plugins (plugins.py, experimental API 1): off until an admin ticks one, sign-in and admin mode enforced by the
app, one broken or wrong-API plugin never stops the rest."""
import time
import types
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from club_minutes import club as clubmod
from club_minutes import plugins, web
from club_minutes.auth import Auth
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "m1", "first": "Jane", "last": "Doe", "email": "jane@example.org"},
          {"id": "m2", "first": "John", "last": "Doe"}, {"id": "n0admn", "first": "Ann", "last": "Admin", "email": "ann@example.org"}]


class EP:
    def __init__(self, name, mod=None, error=None):
        self.name, self.mod, self.error = name, mod, error

    def load(self):
        if self.error:
            raise self.error
        return self.mod


def module(name, **kw):
    return types.SimpleNamespace(API=1, NAME=name, TITLE=name.title(), DESCRIPTION="x", __file__="/nowhere/x.py", **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.syspath_prepend(str(FIX))
    import helloplugin
    clash = module("chow", router=APIRouter())
    eps = [EP("hello", helloplugin), EP("broken", error=RuntimeError("boom")), EP("wrong", types.SimpleNamespace(API=2, NAME="wrong")),
           EP("chow", clash)]
    monkeypatch.setattr(plugins, "_entry_points", lambda: eps)
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    club.yaml = {**club.yaml, "admins": ["n0admn"]}  # kept when a save re-reads the settings
    club._configure(club.yaml)
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    told = []
    monkeypatch.setattr(plugins, "_tell_admins", lambda c, failed: told.append(failed))
    n = len(web.app.router.routes)
    plugins.load(web.app, web.templates.env, lambda: web.archive.club, lambda r: getattr(r.state, "member", None), web.admin_mode, web.page)
    yield club, a, told
    del web.app.router.routes[n:]
    monkeypatch.setattr(plugins, "_entry_points", list)  # none installed again
    plugins.load(web.app, web.templates.env, lambda: web.archive.club, lambda r: None, web.admin_mode, web.page)


def client(a, who=None, admin=False):
    t = TestClient(web.app, client=("203.0.113.6", 5000))
    if who:
        tok = a.new_session(who)
        t.cookies.set(web.COOKIE, tok)
        if admin:
            a.set_admin(tok, 60)
    return t


def test_off_by_default_then_sign_in_public_admin_and_permission_rules(env):
    club, a, _ = env
    assert client(a, "m1").get("/hello/").status_code == 404 and client(a).get("/hello/open").status_code == 404
    assert "/hello/" not in client(a, "m1").get("/").text
    club.save_site_settings({("plugins", "hello"): True})
    assert 'href="/hello/"' in client(a, "m1").get("/").text and "Hello editor" not in client(a, "m1").get("/").text
    anon = client(a)
    assert anon.get("/hello/", follow_redirects=False).status_code == 303  # sign in first
    assert anon.get("/hello/open").json() == {"open": True}  # PUBLIC
    assert anon.get("/hello/door/net").json() == {"door": "net"}  # a PUBLIC pattern: any one room
    assert anon.get("/hello/door/net/x", follow_redirects=False).status_code == 303  # but nothing deeper
    assert anon.get("/hello/admin", follow_redirects=False).status_code == 303
    assert "Hi m1" in client(a, "m1").get("/hello/").text
    assert client(a, "m1").get("/hello/admin").status_code == 404  # not an admin
    assert client(a, "n0admn").get("/hello/admin", follow_redirects=False).headers["location"].startswith("/admin/confirm")
    assert client(a, "n0admn", admin=True).get("/hello/admin").json() == {"settings": True}
    # its permission: shown on Site → Roles, gates a page, and a role can be given it
    assert client(a, "m1").get("/hello/gated").status_code == 403
    assert "Edit the hello board (Hello board)" in client(a, "n0admn", admin=True).get("/admin/site/roles").text
    t = club.role_table()
    t["officer"]["perms"] = [*t["officer"]["perms"], "hello.edit"]
    t["officer"]["members"] = ["m1"]
    club.save_roles(t)
    assert client(a, "m1").get("/hello/gated").json() == {"edit": True}
    assert 'href="/hello/gated"' in client(a, "m1").get("/").text
    assert "/help/plugin-hello" in client(a, "m1").get("/help").text and "Hello guide" in client(a, "m1").get("/help/plugin-hello").text


def test_storage_round_trips_and_email_needs_its_declaration(env):
    club, a, _ = env
    club.save_site_settings({("plugins", "hello"): True})
    assert client(a, "m1").get("/hello/note/hi").json() == {"note": "hi"}
    assert (clubmod.DATA / "plugins" / "hello.json").exists() and not list((clubmod.DATA / "plugins").glob("*.tmp"))
    api = plugins.LOADED["hello"].api
    assert api.storage.update(lambda d: d.update(n=d.get("n", 0) + 1))["n"] == 1 and api.storage.load()["n"] == 1
    assert api.member("m1") == {"id": "m1", "name": "Jane Doe", "active": True}
    with pytest.raises(PermissionError):
        api.email("m1")


def test_a_broken_wrong_api_or_clashing_plugin_is_skipped_and_shown_with_the_reason(env):
    club, a, told = env
    assert set(plugins.PROBLEMS) == {"broken", "wrong", "chow"}
    assert "boom" in plugins.PROBLEMS["broken"] and "API 2" in plugins.PROBLEMS["wrong"] and "already an address" in plugins.PROBLEMS["chow"]
    assert client(a, "m1").get("/").status_code == 200  # the app runs on
    assert client(a, "m1").get("/broken/admin").status_code == 404  # a failed plugin's address stays shut
    assert not plugins.failed("/chow") and plugins.failed("/broken/x")  # never the app's own address of that name
    body = client(a, "n0admn", admin=True).get("/admin/site/features").text
    assert "Hello board" in body and "boom" in body and "this app speaks API 1" in body and "already an address" in body
    assert not told  # none of them was ticked on
    club.save_site_settings({("plugins", "broken"): True})
    plugins.load(web.app, web.templates.env, lambda: web.archive.club, lambda r: None, web.admin_mode, web.page)
    assert told == [["broken"]] or told[-1] == ["broken"]  # the admins are told, once per start


def test_calendar_buttons_show_only_while_the_plugin_is_on(env):
    club, a, _ = env
    admin = client(a, "n0admn", admin=True)
    assert "Hello room" not in admin.get("/calendar/edit").text
    club.save_site_settings({("plugins", "hello"): True})
    page = admin.get("/calendar/edit").text
    assert 'data-url="https://example.org/hello"' in page and 'data-url="http://testserver/hello/"' in page  # a path gets the app's address


def test_plugin_admin_changes_are_logged_and_a_stale_confirmation_is_asked_again(env, monkeypatch):
    club, a, _ = env
    club.save_site_settings({("plugins", "hello"): True})
    admin = client(a, "n0admn", admin=True)
    assert admin.post("/hello/admin/secret").json() == {"secret": "new"}  # admin mode just confirmed: fresh
    assert '"plugin_admin": "POST /hello/admin/secret"' in (web.DATA / "admin-log.jsonl").read_text()
    monkeypatch.setattr(web, "FRESH_SECONDS", -1)
    r = admin.post("/hello/admin/secret", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/confirm?next=/hello/admin"


def test_update_check_turns_off_a_plugin_that_wont_load_or_holds_the_update_if_chosen(env, monkeypatch, capsys):
    club, a, _ = env
    monkeypatch.setenv("CLUB_MINUTES_CONFIG", str(FIX / "test-club.yaml"))
    club.save_site_settings({("plugins", "hello"): True, ("plugins", "wrong"): True})
    assert plugins.check() == 0 and "Plugin wrong has no release for this version yet" in capsys.readouterr().out
    assert club.site_settings["plugins"] == {"hello": True, "wrong": False}  # turned off; hello untouched
    club.save_site_settings({("plugins", "wrong"): True, ("plugin_hold", "wrong"): True})
    assert plugins.check() == 1 and "wait for it" in capsys.readouterr().out
    assert club.site_settings["plugins"]["wrong"] is True  # held: nothing changed
    assert 'name="plugin_hold_wrong" value="1" checked' in client(a, "n0admn", admin=True).get("/admin/site/features").text


def test_update_check_warns_of_a_plugin_that_is_on_but_not_installed_and_holds_if_chosen(env, monkeypatch, capsys):
    club, _a, _ = env
    monkeypatch.setenv("CLUB_MINUTES_CONFIG", str(FIX / "test-club.yaml"))
    club.save_site_settings({("plugins", "ghost"): True})
    assert plugins.check() == 0 and "Plugin ghost is turned on but isn't installed" in capsys.readouterr().out
    assert club.site_settings["plugins"]["ghost"] is True  # warned, not turned off
    club.save_site_settings({("plugin_hold", "ghost"): True})
    assert plugins.check() == 1 and "Plugin ghost is not installed, and Site" in capsys.readouterr().out


def test_a_damaged_settings_file_never_reads_as_hold(env, monkeypatch):
    monkeypatch.setenv("CLUB_MINUTES_CONFIG", str(FIX / "test-club.yaml"))
    (clubmod.DATA / "site-settings.json").write_text("[1]")
    assert plugins.check() == 2


def test_roles_labels_and_form_action_only_on_the_plugins_own_addresses(env):
    club, a, _ = env
    assert "form-action 'self';" in client(a, "m1").get("/hello/roles").headers["content-security-policy"] + ";"  # off: nothing added
    club.save_site_settings({("plugins", "hello"): True})
    t = club.role_table()
    t["officer"]["members"] = ["m1"]
    club.save_roles(t)
    d = client(a, "m1").get("/hello/roles").json()
    assert d["roles"] == ["officer"] and d["labels"]["officer"] == "Officer" and d["base"].startswith("http")
    assert "form-action 'self' https://forms.example.org:8443;" in client(a, "m1").get("/hello/").headers["content-security-policy"] + ";"  # only the valid origin
    assert "form-action 'self';" in client(a, "m1").get("/").headers["content-security-policy"] + ";"
    assert "form-action 'self';" in client(a, "m1").get("/helloworld").headers["content-security-policy"] + ";"  # another address that starts the same


def test_nav_ready_false_hides_the_menu_links_except_in_admin_mode(env):
    import helloplugin
    club, a, _ = env
    club.save_site_settings({("plugins", "hello"): True})
    helloplugin.READY[0] = False
    try:
        assert 'href="/hello/"' not in client(a, "m1").get("/").text
        assert 'href="/hello/"' in client(a, "n0admn", admin=True).get("/").text  # to finish setting it up
        assert client(a, "m1").get("/hello/").status_code == 200  # the page itself is still there
    finally:
        helloplugin.READY[0] = True


def test_a_plugin_with_a_settings_page_gets_a_site_tab_while_on(env):
    club, a, _ = env
    admin = client(a, "n0admn", admin=True)
    assert 'href="/hello/admin"' not in admin.get("/admin/site/club").text
    club.save_site_settings({("plugins", "hello"): True})
    page = admin.get("/admin/site/club").text
    assert '<a href="/hello/admin" class="">Hello board</a>' in page and 'href="/admin/site/club" class="on" aria-current="page"' in page
    assert 'href="/admin/site/look" class="on"' in admin.get("/admin/site").text  # the bare address is Look


def test_a_plugin_sees_the_open_meeting_marks_members_online_and_lists_guests(env, tmp_path, monkeypatch):
    import datetime as dt

    from club_minutes import console, events
    club, *_ = env
    club.save_site_settings({("plugins", "hello"): True})
    from club_minutes.store import Archive
    (tmp_path / "minutes").mkdir()
    arch = Archive(tmp_path / "minutes", club)  # its own: no draft left open by another test
    monkeypatch.setattr(web, "archive", arch)
    api = plugins.LOADED["hello"].api
    assert api.meeting() is None and api.mark_present("m1") is False  # no console open
    events.save([{"id": "w", "cal": "events", "title": "Meeting", "online": "https://example.org/meet/club", "meeting": True,
                  "start": "2026-11-10T19:00", "repeat": {"freq": "weekly"}}])
    console.start(arch, dt.date(2026, 11, 10), "m2")
    m = api.meeting()
    assert m["date"] == "2026-11-10" and m["online"] == "https://example.org/meet/club" and abs(m["opened"] - time.time()) < 5
    assert api.mark_present("m1") is True and api.mark_present("m1") is False and api.mark_present("nobody") is False
    assert api.mark_present("n0admn", in_person=True) is True and arch.by_slug["2026-11-10"].in_person == ["n0admn"]
    assert arch.by_slug["2026-11-10"].remote == ["m1"] and api.meeting()["present"] == ["n0admn", "m1"]
    api.storage.save({"mark": "m2"})  # the hook marks while the console renders, refreshes and submits
    assert console.attendance(arch, "2026-11-10") == {"in_person": ["n0admn"], "remote": ["m1", "m2"], "video_guests": ["Pat Guest"]}
    assert console.context(arch, arch.by_slug["2026-11-10"])["video_guests"] == ["Pat Guest"]
    club.save_site_settings({("plugins", "hello"): False})
    assert console.attendance(arch, "2026-11-10")["video_guests"] == []  # off: not asked


def test_the_public_test_helper_runs_a_plugin_and_allowed_members_today_and_the_site_head(tmp_path, monkeypatch):
    import datetime as dt
    from zoneinfo import ZoneInfo

    import helloplugin

    from club_minutes.testing import plugin_app
    monkeypatch.syspath_prepend(str(FIX))
    app = plugin_app(helloplugin, PEOPLE, tmp_path, monkeypatch, admins=["n0admn"])
    assert "Hi m1" in app.client("m1").get("/hello/").text and app.client().get("/hello/", follow_redirects=False).status_code == 303
    me = app.client("m1").get("/hello/whoami").json()
    assert me["librarian"] is False and me["members"]["m2"] == "John Doe"
    assert me["today"] == str(dt.datetime.now(ZoneInfo("America/New_York")).date())  # the club's day, not the server's
    assert app.client("n0admn", admin=True).get("/hello/whoami").json()["librarian"] is True  # admin mode counts
    app.give("officer", "edit")
    page = app.client("n0admn", admin=True).get("/hello/admin/page").text  # a settings page: Site's heading and tabs by themselves
    assert "<h1>Site</h1>" in page and 'href="/hello/admin"' in page and "Hi the admin" in page
    assert app.club.role_table()["officer"]["perms"][-1] == "hello.edit"


def test_a_roles_save_keeps_a_plugins_permissions_while_it_is_off_or_not_loaded(env, monkeypatch):
    club, *_ = env
    club.save_site_settings({("plugins", "hello"): True})
    t = club.role_table()
    t["officer"]["perms"] = [*t["officer"]["perms"], "hello.edit"]
    club.save_roles(t)
    club.save_site_settings({("plugins", "hello"): False})
    club.save_roles(club.role_table())  # Site → Roles saved while it's off: the form doesn't show hello.edit
    monkeypatch.delitem(clubmod.PERMISSIONS, "hello.edit")  # and saved again by something with no plugins loaded
    club.save_roles(club.role_table())
    monkeypatch.setitem(clubmod.PERMISSIONS, "hello.edit", "edit the hello board (Hello board)")
    club.save_site_settings({("plugins", "hello"): True})
    assert "hello.edit" in club.role_table()["officer"]["perms"]  # still there once it's back
    t = club.role_table()
    t["officer"]["perms"].remove("hello.edit")
    club.save_roles(t)  # while it's on, taking it off a role works as before
    assert "hello.edit" not in club.role_table()["officer"]["perms"]


def test_undeclared_data_is_refused_and_bad_declarations_fail_to_load(env):
    api = plugins.LOADED["hello"].api
    assert api.member("m1")["name"] == "Jane Doe"  # declared
    with pytest.raises(PermissionError, match="'email'"):
        api.email("m1")
    ok = {"why": "x", "about": "members"}
    for bad in ({"nope": ok}, {"names": {"why": "", "about": "members"}}, {"names": {"why": "x", "about": "everyone"}}, {"names": "x"}):
        with pytest.raises(ValueError):
            plugins.Plugin(module("bad", DATA=bad), "bad")
    with pytest.raises(ValueError):
        plugins.Plugin(module("bad", SHARES=[{"with": "a", "what": "b", "why": "c", "about": "members", "host": "https://x.org/y"}]), "bad")
    assert "privacy" in plugins.APP_PREFIXES  # /privacy is the app's: load() refuses a plugin named that


def test_features_and_the_data_page_show_declarations_escaped_and_visitors_see_only_theirs(env):
    club, a, _ = env
    feat = client(a, "n0admn", admin=True).get("/admin/site/features").text
    assert "to run the hello board" in feat and feat.count("api.example.org") >= 1 and "&lt;b&gt;Pat&lt;/b&gt;" in feat and "<b>Pat</b>" not in feat
    assert "No plugin" in client(a).get("/privacy").text  # signed out, and nothing is on
    club.save_site_settings({("plugins", "hello"): True})
    mine = client(a, "m1").get("/privacy").text
    assert "About members" in mine and "About visitors" in mine and "a member&#39;s first name" in mine
    anon = client(a).get("/privacy")
    assert anon.status_code == 200 and "&lt;b&gt;Pat&lt;/b&gt;" in anon.text and "to run the hello board" not in anon.text
    assert "/privacy" not in client(a, "m1").get("/help").text  # not linked until the app's own sections are written (#68)


def test_send_only_to_a_declared_https_host_and_logs_no_query_or_body(env, monkeypatch):
    seen = []
    monkeypatch.setattr(plugins.httpx, "request", lambda m, u, **kw: seen.append((m, u, kw)) or types.SimpleNamespace(status_code=204))
    api = plugins.LOADED["hello"].api
    for url in ("https://other.example.org/x", "http://api.example.org/x", "ftp://api.example.org/x"):
        with pytest.raises(PermissionError):
            api.send("POST", url)
    assert not seen and not (clubmod.DATA / plugins.CALLS_LOG).exists()
    assert api.send("post", "https://API.example.org/v1/hi?key=secret", json={"name": "Jane"}).status_code == 204
    assert seen[0][2]["timeout"] == 10 and seen[0][2]["follow_redirects"] is False
    line = (clubmod.DATA / plugins.CALLS_LOG).read_text()
    assert '"host": "api.example.org"' in line and '"path": "/v1"' in line and '"status": 204' in line
    assert "secret" not in line and "Jane" not in line


def test_a_plugin_cant_act_while_an_admin_views_as_a_member(env, monkeypatch):
    api = plugins.LOADED["hello"].api
    assert not api.viewing_as(types.SimpleNamespace(state=types.SimpleNamespace(viewing_as=None)))
    assert api.viewing_as(types.SimpleNamespace(state=types.SimpleNamespace(viewing_as="m1")))
    token = plugins.VIEWING_AS.set(True)
    try:
        for call in (lambda: api.mark_present("m1"), lambda: api.send("POST", "https://api.example.org/x")):
            with pytest.raises(PermissionError, match="viewing the app as another member"):
                call()
    finally:
        plugins.VIEWING_AS.reset(token)


def test_video_guest_names_are_capped_and_stripped_of_control_and_bidi_characters():
    assert plugins._guest_name("Pat\u202e Guest\x07\u200b") == "Pat Guest"
    assert len(plugins._guest_name("x" * 500)) == 100


def test_the_calls_log_keeps_only_the_first_part_of_the_path(env, monkeypatch):
    monkeypatch.setattr(plugins.httpx, "request", lambda m, u, **kw: types.SimpleNamespace(status_code=204))
    plugins.LOADED["hello"].api.send("POST", "https://api.example.org/hooks/SECRET-TOKEN/x?k=v")
    line = (clubmod.DATA / plugins.CALLS_LOG).read_text()
    assert '"path": "/hooks"' in line and "SECRET" not in line


def test_a_torn_opened_file_counts_as_now(tmp_path):
    from club_minutes import console
    from club_minutes.store import Archive
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    arch = Archive(tmp_path, club)
    import datetime as dt
    console.start(arch, dt.date(2026, 11, 10), "m2")
    f = tmp_path / "2026-11-10" / "opened"
    f.write_text("")
    assert abs(console.open_meeting(arch)["opened"] - time.time()) < 5 and f.read_text().isdigit()
