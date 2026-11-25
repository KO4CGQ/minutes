"""Plugins (experimental, API 1): extra parts of the app installed as Python packages, each switched on by an admin
on Site → Features. Writing one: docs/developers.md → Writing a plugin. The app enforces sign-in, admin mode and
"off means unreachable" (web.py's require_login asks owner(), is_public() and is_admin_path()); a plugin only
declares what it has."""
from __future__ import annotations

import json
import logging
import re
import tempfile
import threading
from importlib.metadata import entry_points
from pathlib import Path

from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader
from starlette.routing import compile_path

from . import club as clubmod

PLUGIN_API = 1
GROUP = "club_minutes.plugins"
log = logging.getLogger("club_minutes")
LOADED: dict[str, Plugin] = {}
PROBLEMS: dict[str, str] = {}  # entry point name -> why it isn't running
APP_PREFIXES: set[str] = set()  # the app's own first address parts, never blocked for a failed plugin of that name
_app_loader = None


def _entry_points():
    return entry_points(group=GROUP)


class Storage:
    """One JSON file per plugin in the data folder (so in the backups), each call locked, saved by tmp file + replace."""
    def __init__(self, name: str):
        self.name, self._lock = name, threading.Lock()

    @property
    def _path(self) -> Path:
        return clubmod.DATA / "plugins" / f"{self.name}.json"

    def load(self) -> dict:
        with self._lock:
            return self._read()

    def save(self, data: dict) -> None:
        with self._lock:
            self._write(data)

    def update(self, change) -> dict:
        """Load, `change(data)` in place, save: one locked step, so two requests never lose each other's change."""
        with self._lock:
            data = self._read()
            change(data)
            self._write(data)
            return data

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text()) if self._path.exists() else {}
        except ValueError:
            log.exception("%s is damaged; the plugin starts empty", self._path)
            return {}

    def _write(self, data: dict) -> None:
        text = json.dumps(data, indent=1)  # before anything is written: a value that isn't JSON changes nothing
        self._path.parent.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self._path.parent, suffix=".tmp", delete=False) as f:
            f.write(text)
        Path(f.name).replace(self._path)


class Api:
    """All a plugin gets: members' names, the permission check, email addresses if it declared NEEDS, its own
    storage, and who is asking. No Club, no sign-in internals, no other plugin's storage."""
    def __init__(self, plugin: Plugin, club, who, admin_mode, page):
        self._p, self._club, self.who, self.admin_mode, self._page = plugin, club, who, admin_mode, page
        self.storage = Storage(plugin.name)

    def member(self, id: str) -> dict | None:
        m = self._club().members.get(id)
        return {"id": m.id, "name": m.name, "active": m.status.lower() not in ("inactive", "silent key")} if m else None

    def may(self, id: str | None, perm: str) -> bool:
        """Whether the member's roles allow this plugin's permission `perm` (its PERMISSIONS key). Admin mode is
        separate: `admin_mode(request)`."""
        return self._club().may(id, f"{self._p.name}.{perm}")

    def email(self, id: str) -> str | None:
        if not self._p.needs_email:
            raise PermissionError(f"{self._p.name} didn't declare NEEDS = {{'email'}}")
        m = self._club().members.get(id)
        return m.email if m else None

    def fresh(self, request, next: str):
        """Before a sensitive change: None if the admin confirmed it's them (passkey or emailed link) in the last few
        minutes, otherwise a redirect to confirm that brings them back to `next` (return it)."""
        from .web import fresh_confirm
        return fresh_confirm(request, next)

    def page(self, request, template: str, **ctx):
        """The app's page (its menu, styles, sign-in state) around one of the plugin's templates/."""
        return self._page(request, f"{self._p.name}/{template}", **ctx)


class Plugin:
    def __init__(self, mod, entry_name: str):
        if getattr(mod, "API", None) != PLUGIN_API:
            raise ValueError(f"it declares plugin API {getattr(mod, 'API', None)!r}; this app speaks API {PLUGIN_API}")
        self.mod, self.name = mod, mod.NAME
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z0-9]+", self.name) or self.name != entry_name:
            raise ValueError("NAME must be lowercase letters and digits, the same as its entry point's name")
        self.title, self.description = str(mod.TITLE), str(mod.DESCRIPTION)
        self.needs_email = "email" in getattr(mod, "NEEDS", ())
        self.public = set(getattr(mod, "PUBLIC", ()))
        self.permissions = {f"{self.name}.{k}": f"{v} ({self.title})" for k, v in getattr(mod, "PERMISSIONS", {}).items()}
        self.nav = list(getattr(mod, "NAV", ()))
        self.templates = Path(mod.__file__).parent / "templates"
        guide = getattr(mod, "GUIDE", None)
        self.guide = (Path(mod.__file__).parent / guide) if guide else None
        self.has_admin = any(getattr(r, "path", "") == "/admin" for r in getattr(getattr(mod, "router", None), "routes", []))
        self.api: Api


def load(app, env, club, who, admin_mode, page) -> None:
    """Find the installed plugins and register each good one (a bad one is skipped and logged, the app runs on);
    `club` is a function giving the current Club. Called once, after every route of the app exists."""
    global _app_loader
    LOADED.clear()
    PROBLEMS.clear()
    for k in [k for k in clubmod.PERMISSIONS if "." in k]:
        del clubmod.PERMISSIONS[k]
    taken = {getattr(r, "path", "").split("/")[1] for r in app.routes if getattr(r, "path", "").startswith("/")}
    APP_PREFIXES.clear()
    APP_PREFIXES.update(taken)
    prefixes = {}
    for ep in _entry_points():
        try:
            p = Plugin(ep.load(), ep.name)
            if p.name in taken:
                raise ValueError(f"/{p.name} is already an address of the app")
            if router := getattr(p.mod, "router", None):
                app.include_router(router, prefix=f"/{p.name}")
            taken.add(p.name)
            p.api = p.mod.api = Api(p, club, who, admin_mode, page)  # handlers read the module's `api` when a request comes
            LOADED[p.name] = p
            clubmod.PERMISSIONS.update(p.permissions)
            if p.templates.is_dir():
                prefixes[p.name] = FileSystemLoader(p.templates)
        except Exception as e:
            log.exception("plugin %s skipped", ep.name)
            PROBLEMS[ep.name] = f"{type(e).__name__}: {e}"
    _app_loader = _app_loader or env.loader
    env.loader = ChoiceLoader([_app_loader, PrefixLoader(prefixes)])  # a plugin's pages are "<name>/page.html"
    if failed := [n for n in PROBLEMS if club().plugin_on(n)]:
        _tell_admins(club(), failed)


def _tell_admins(club, failed: list[str]) -> None:
    """Once per start, as backup.py does for a failed backup: a plugin the club turned on isn't running."""
    from . import auth, branding
    to = [m.email for mid, m in club.members.items() if club.is_admin(mid) and m.email]
    if not to:
        return
    lines = "\n".join(f"  {n}: {PROBLEMS[n]}" for n in failed)
    try:
        auth.send_email(to, f"{branding.name(club)}: a plugin didn't start",
                        f"These plugins are turned on but aren't running (the rest of the app is):\n\n{lines}\n\n"
                        f"Admin → Site → Features shows the same.\n\n{club.sign_off}\n{branding.name(club)}",
                        only_to=club.email_only_to())
    except Exception:
        log.exception("couldn't email the admins about the plugins that didn't start")


def owner(path: str) -> Plugin | None:
    return LOADED.get(path.split("/")[1]) if path.startswith("/") else None


def failed(path: str) -> bool:
    """Under the address of a plugin that failed to load: any routes it got before failing stay unreachable."""
    return path.startswith("/") and path.split("/")[1] in PROBLEMS.keys() - APP_PREFIXES


def is_admin_path(p: Plugin, path: str) -> bool:
    return path == f"/{p.name}/admin" or path.startswith(f"/{p.name}/admin/")


def is_public(path: str) -> bool:
    p = owner(path)
    # PUBLIC entries are route paths, so "/{room}" opens /<name>/<any one room> signed out (Jitsi's guest pages)
    return bool(p) and not is_admin_path(p, path) and any(compile_path(f"/{p.name}{x}")[0].match(path) for x in p.public)


def nav(club, mid: str, admin: bool) -> list[tuple[str, str]]:  # (address, label), as the menu takes them
    """Menu links of the plugins that are on, for the members whose permission each needs (admin mode: all)."""
    return [(f"/{p.name}{path}", label) for p in LOADED.values() if club.plugin_on(p.name)
            for label, path, perm in p.nav if perm is None or admin or club.may(mid, f"{p.name}.{perm}")]


def calendar_links(club) -> list[tuple[str, str]]:
    """Buttons for the calendar event form's online link, from the plugins that are on."""
    out = []
    for p in LOADED.values():
        if club.plugin_on(p.name) and hasattr(p.mod, "calendar_links"):
            try:
                out += [(str(label), str(url)) for label, url in p.mod.calendar_links(p.api)]
            except Exception:
                log.exception("plugin %s: calendar_links failed", p.name)
    return out


def guides(club) -> dict[str, str]:
    """Help guides of the plugins that are on: {"plugin-<name>": title}."""
    return {f"plugin-{p.name}": p.title for p in LOADED.values() if p.guide and club.plugin_on(p.name)}


def rows(club) -> list[dict]:
    """Site → Features: every installed plugin, running or not, and why not."""
    out = [{"name": p.name, "title": p.title, "description": p.description, "needs_email": p.needs_email,
            "on": club.plugin_on(p.name), "problem": "", "admin": f"/{p.name}/admin" if p.has_admin else ""} for p in LOADED.values()]
    return out + [{"name": n, "title": n, "description": "", "needs_email": False, "on": club.plugin_on(n),
                   "problem": why, "admin": ""} for n, why in PROBLEMS.items()]
