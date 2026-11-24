"""Plugins (plugins.py, experimental API 1): off until an admin ticks one, sign-in and admin mode enforced by the
app, one broken or wrong-API plugin never stops the rest."""
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
