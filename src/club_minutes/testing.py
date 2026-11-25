"""For a plugin's own tests (experimental, like the plugin API): Club Minutes' web app with one plugin loaded from its
module, without installing it. With pytest:

    from club_minutes.testing import plugin_app

    def test_books(tmp_path, monkeypatch):
        app = plugin_app(library_plugin, [{"id": "m1", "first": "Jane", "last": "Doe"}], tmp_path, monkeypatch)
        assert app.client("m1").get("/library").status_code == 200

Import this module before anything else from club_minutes: the app reads its data folder and club.yaml when it's
imported, and this points both at throwaway ones unless CLUB_MINUTES_DATA / CLUB_MINUTES_CONFIG are already set.
Everything it changes is put back when the test ends (monkeypatch)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="club-minutes-plugin-test-"))
if "CLUB_MINUTES_CONFIG" not in os.environ:
    (_tmp / "club.yaml").write_text('club: {name: Test Club, meeting_day: Thursday, meeting_time: "19:30", timezone: America/New_York}\n')
    os.environ["CLUB_MINUTES_CONFIG"] = str(_tmp / "club.yaml")
os.environ.setdefault("CLUB_MINUTES_DATA", str(_tmp / "data"))


class PluginApp:
    def __init__(self, club, auth, plugin):
        self.club, self._auth, self.plugin = club, auth, plugin
        self.api, self.storage = plugin.api, plugin.api.storage

    def client(self, member: str | None = None, admin: bool = False):
        """A TestClient signed in as `member` (None: signed out); `admin`: an admin with admin mode on (list them
        in plugin_app's `admins`)."""
        from fastapi.testclient import TestClient

        from . import web
        t = TestClient(web.app, client=("203.0.113.6", 5000))
        if member:
            token = self._auth.new_session(member)
            t.cookies.set(web.COOKIE, token)
            if admin:
                self._auth.set_admin(token, 60)
        return t

    def give(self, role: str, perm: str) -> None:
        """Give a role (e.g. "officer") the plugin's permission `perm`, as an admin does on Site → Roles."""
        t = self.club.role_table()
        t[role]["perms"] = [*t[role]["perms"], f"{self.plugin.name}.{perm}"]
        self.club.save_roles(t)


def plugin_app(module, roster: list[dict], tmp_path: Path, monkeypatch, admins=(), on: bool = True) -> PluginApp:
    """The app with `module` (a plugin's module) loaded under its NAME and, with `on`, ticked on; `roster`: members as
    {"id", "first", "last", …} (add "role": "President" for an officer); `admins`: ids that may use admin mode."""
    from types import SimpleNamespace

    from . import club as clubmod
    from . import plugins, web
    from .auth import Auth
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setattr(clubmod, "DATA", data)
    club = clubmod.Club(Path(os.environ["CLUB_MINUTES_CONFIG"]), roster=roster)
    club.yaml = {**club.yaml, "admins": list(admins)}  # kept when a save re-reads the settings
    club._configure(club.yaml)
    auth = Auth(club, data / "auth.sqlite")
    monkeypatch.setattr(web, "authn", auth)
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(plugins, "_entry_points", lambda: [SimpleNamespace(name=module.NAME, load=lambda: module)])
    # what loading changes, put back afterwards: the app's routes and template loader, the plugins found
    monkeypatch.setattr(web.app.router, "routes", list(web.app.router.routes))
    monkeypatch.setattr(web.templates.env, "loader", web.templates.env.loader)
    monkeypatch.setattr(plugins, "LOADED", {})
    monkeypatch.setattr(plugins, "PROBLEMS", {})
    before = set(clubmod.PERMISSIONS)
    plugins.load(web.app, web.templates.env, lambda: web.archive.club, lambda r: getattr(r.state, "member", None), web.admin_mode, web.page)
    for k in set(clubmod.PERMISSIONS) - before:  # shared with the rest of the app: removed again when the test ends
        monkeypatch.setitem(clubmod.PERMISSIONS, k, clubmod.PERMISSIONS.pop(k))
    if module.NAME not in plugins.LOADED:
        raise RuntimeError(f"{module.NAME} didn't load: {plugins.PROBLEMS.get(module.NAME)}")
    if on:
        club.save_site_settings({("plugins", module.NAME): True})
    return PluginApp(club, auth, plugins.LOADED[module.NAME])
