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
import time
from importlib.metadata import entry_points
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader
from starlette.routing import compile_path

from . import club as clubmod
from .meeting import one_line

PLUGIN_API = 1
GROUP = "club_minutes.plugins"
log = logging.getLogger("club_minutes")
LOADED: dict[str, Plugin] = {}
PROBLEMS: dict[str, str] = {}  # entry point name -> why it isn't running
APP_PREFIXES: set[str] = set()  # the app's own first address parts, never blocked for a failed plugin of that name
_app_loader = None
# What a plugin may declare in DATA (module level), and who it's about. Each kind gates the api calls named; "visitors"
# gates none: it is for what a plugin collects itself from signed-out people (a typed name, say).
KINDS = {"names": "Members' names", "email": "Members' email addresses", "roles": "Members' roles",
         "attendance": "Who is at the meeting (and marking them present)", "ip": "The network address a request comes from",
         "visitors": "What visitors type or give it"}
ABOUT = {"visitors": "visitors (people not signed in)", "members": "members", "admins": "admins"}
CALLS_LOG = "plugin-calls.jsonl"  # one line per api.send; trimmed with the other logs (auth.prune_old_lines)


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
    """All a plugin gets: member data it declared in DATA, the permission check, outgoing requests to the hosts it
    declared in SHARES, its own storage, and who is asking. No Club, no sign-in internals, no other plugin's storage."""
    def __init__(self, plugin: Plugin, club, who, admin_mode, page):
        self._p, self._club, self.who, self.admin_mode, self._page = plugin, club, who, admin_mode, page
        self.storage = Storage(plugin.name)

    def _uses(self, kind: str) -> None:
        if kind not in self._p.data:
            raise PermissionError(f"{self._p.name} didn't declare {kind!r}: add it to DATA, "
                                  f"e.g. DATA = {{{kind!r}: {{'why': '...', 'about': 'members'}}}}")

    def member(self, id: str) -> dict | None:
        self._uses("names")
        m = self._club().members.get(id)
        return {"id": m.id, "name": m.name, "active": m.status.lower() not in ("inactive", "silent key")} if m else None

    def may(self, id: str | None, perm: str) -> bool:
        """Whether the member's roles allow this plugin's permission `perm` (its PERMISSIONS key). Admin mode is
        separate: `admin_mode(request)`."""
        return self._club().may(id, f"{self._p.name}.{perm}")

    def allowed(self, request, perm: str) -> bool:
        """The signed-in member has this plugin's permission `perm`, or is an admin in admin mode."""
        return self.may(self.who(request), perm) or bool(self.admin_mode(request))

    def members(self) -> dict[str, str]:
        """Every member on the roster, former ones too: {id: name}."""
        self._uses("names")
        return {i: m.name for i, m in self._club().members.items()}

    def today(self):
        """Today's date where the club is (its time zone), not the server's."""
        import datetime as dt
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(self._club().timezone)).date()

    def roles(self, id: str) -> list[str]:
        """The role keys the member holds (Site → Roles; admin isn't one)."""
        self._uses("roles")
        return self._club().roles(id)

    def role_labels(self) -> dict[str, str]:
        """Every role's key and name, for a settings page that picks roles."""
        self._uses("roles")
        return {k: r["name"] for k, r in self._club().role_table().items()}

    def base_url(self, request) -> str:
        """The address members reach the app at (https://host), for links a page shows."""
        from .web import base_url
        return base_url(request)

    def meeting(self) -> dict | None:
        """The meeting open in the leader console now: {"date": "YYYY-MM-DD", "opened": unix seconds the console opened
        it, "online": that night's Online link on the calendar ('' if none), "present": ids in its attendance}; else None."""
        self._uses("attendance")
        from . import console
        from .web import archive
        return console.open_meeting(archive)

    def mark_present(self, id: str, in_person: bool = False) -> bool:
        """Mark that member present (online, or in person) in the open console meeting unless they're in its
        attendance already. True if it did. Everyone's view of the meeting picks it up like any attendance change."""
        self._uses("attendance")
        from . import console
        from .web import archive
        return console.mark_present(archive, id, f"plugin:{self._p.name}", in_person)

    def client_ip(self, request) -> str | None:
        """The visitor's real address (through the app's proxy, as sign-in bans see it)."""
        self._uses("ip")
        from .web import client_ip
        return client_ip(request)

    def email(self, id: str) -> str | None:
        self._uses("email")
        m = self._club().members.get(id)
        return m.email if m else None

    def send(self, method: str, url: str, **kwargs) -> httpx.Response:
        """An outgoing request (httpx.request), only to https and a host named in SHARES; 10 seconds at most, redirects
        not followed. Each call is logged in plugin-calls.jsonl (time, plugin, method, host, path, status; never the
        query or a body). Raises PermissionError for any other host or a plain http address."""
        u = urlsplit(url)
        if u.scheme != "https" or (u.hostname or "").lower() not in {s["host"].lower() for s in self._p.shares if s.get("host")}:
            raise PermissionError(f"{self._p.name} may only send to https addresses of a host named in SHARES (not {u.hostname or url!r})")
        status = "error"
        try:
            r = httpx.request(method, url, timeout=10, follow_redirects=False, **kwargs)
            status = r.status_code
            return r
        finally:
            with (clubmod.DATA / CALLS_LOG).open("a") as f:
                f.write(json.dumps({"at": int(time.time()), "plugin": self._p.name, "method": method.upper(),
                                    "host": u.hostname.lower(), "path": u.path, "status": status}) + "\n")
            (clubmod.DATA / CALLS_LOG).chmod(0o600)

    def fresh(self, request, next: str):
        """Before a sensitive change: None if the admin confirmed it's them (passkey or emailed link) in the last few
        minutes, otherwise a redirect to confirm that brings them back to `next` (return it)."""
        from .web import fresh_confirm
        return fresh_confirm(request, next)

    def page(self, request, template: str, **ctx):
        """The app's page (its menu, styles, sign-in state) around one of the plugin's templates/. On the plugin's
        settings pages (/<NAME>/admin…) it also puts Admin → Site's heading and tabs above the template."""
        return self._page(request, f"{self._p.name}/{template}", site_head=is_admin_path(self._p, request.url.path), **ctx)


def _declared(d, where: str, keys: tuple) -> dict:
    """One DATA or SHARES entry, checked: each of `keys` a non-empty string, `about` one of ABOUT."""
    if not isinstance(d, dict) or not all(isinstance(d.get(k), str) and d[k].strip() for k in keys):
        raise ValueError(f"{where} needs {', '.join(keys)}, each a non-empty string")
    if d["about"] not in ABOUT:
        raise ValueError(f"{where}: about is one of {', '.join(ABOUT)}, not {d['about']!r}")
    return {k: d[k].strip() for k in keys}


def _host(h) -> str:
    if not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", h):
        raise ValueError(f"SHARES: host is a plain host name like meet.example.org, not {h!r}")
    return h


class Plugin:
    def __init__(self, mod, entry_name: str):
        if getattr(mod, "API", None) != PLUGIN_API:
            raise ValueError(f"it declares plugin API {getattr(mod, 'API', None)!r}; this app speaks API {PLUGIN_API}")
        self.mod, self.name = mod, mod.NAME
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z0-9]+", self.name) or self.name != entry_name:
            raise ValueError("NAME must be lowercase letters and digits, the same as its entry point's name")
        self.title, self.description = str(mod.TITLE), str(mod.DESCRIPTION)
        if unknown := set(getattr(mod, "DATA", None) or {}) - KINDS.keys():
            raise ValueError(f"DATA names an unknown kind {min(unknown)!r} (one of {', '.join(KINDS)})")
        self.data = {k: _declared(d, f"DATA {k!r}", ("why", "about")) for k, d in (getattr(mod, "DATA", None) or {}).items()}
        self.shares = [_declared(d, "SHARES", ("with", "what", "why", "about")) | ({"host": _host(d.get("host"))} if d.get("host") else {})
                       for d in getattr(mod, "SHARES", None) or []]
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


def check() -> int:
    """update.sh, after installing a new version and before starting it: each plugin that's on but won't load in it
    is turned off, or makes the update wait (1), as its admin chose on Site → Features. Reads only club.yaml and the
    Site settings, never the rest of the data: the old version is still running on it. 2: the check itself failed."""
    try:
        return _check()
    except Exception:
        log.exception("plugin check failed")
        return 2  # never read as "hold": a damaged settings file mustn't stop every update


def _check() -> int:
    import os

    import yaml
    cfg = yaml.safe_load(Path(os.environ.get("CLUB_MINUTES_CONFIG", clubmod.ROOT / "club.yaml")).read_text()) or {}
    p = clubmod.DATA / "site-settings.json"
    site = json.loads(p.read_text()) if p.exists() else {}
    cfg = clubmod._overlay(cfg, site)
    on = {k for k, v in (cfg.get("plugins") or {}).items() if v}
    hold = {k for k, v in (cfg.get("plugin_hold") or {}).items() if v}
    bad = {}
    for ep in _entry_points():
        if ep.name in on:
            try:
                Plugin(ep.load(), ep.name)
            except Exception as e:  # noqa: BLE001 - anything a plugin raises: it won't load
                bad[ep.name] = f"{type(e).__name__}: {e}"
    if held := [n for n in bad if n in hold]:
        for n in held:
            print(f"Plugin {n} has no release for this version yet ({bad[n]}), and Site → Features says to wait for it: the app stays on the version it had.")
        return 1
    for n, why in bad.items():
        print(f"Plugin {n} has no release for this version yet ({why}), so it was turned off. Tick it on again on Site → Features once it has one.")
        site.setdefault("plugins", {})[n] = False
    if bad:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(site, indent=1))
        tmp.replace(p)
    return 0


def owner(path: str) -> Plugin | None:
    return LOADED.get(path.split("/")[1]) if path.startswith("/") else None


def failed(path: str) -> bool:
    """Under the address of a plugin that failed to load: any routes it got before failing stay unreachable."""
    return path.startswith("/") and path.split("/")[1] in PROBLEMS.keys() - APP_PREFIXES


ORIGIN = re.compile(r"https://[A-Za-z0-9.-]{1,253}(:\d{1,5})?")


def form_action(path: str, club) -> list[str]:
    """Origins for the CSP form-action on `path`: what a plugin's form_action(api) names, only on that plugin's own
    addresses (/<NAME> and below) while it's on, and only if each is https://host[:port]."""
    p = owner(path)
    if not p or not club.plugin_on(p.name) or not hasattr(p.mod, "form_action"):
        return []
    try:
        return [o for o in p.mod.form_action(p.api) if isinstance(o, str) and ORIGIN.fullmatch(o)]
    except Exception:
        log.exception("plugin %s: form_action failed", p.name)
        return []


def is_admin_path(p: Plugin, path: str) -> bool:
    return path == f"/{p.name}/admin" or path.startswith(f"/{p.name}/admin/")


def is_public(path: str) -> bool:
    p = owner(path)
    # PUBLIC entries are route paths, so "/{room}" opens /<name>/<any one room> signed out (Jitsi's guest pages)
    return bool(p) and not is_admin_path(p, path) and any(compile_path(f"/{p.name}{x}")[0].match(path) for x in p.public)


def nav(club, mid: str, admin: bool) -> list[tuple[str, str]]:  # (address, label), as the menu takes them
    """Menu links of the plugins that are on and ready (nav_ready; admin mode: always, to finish setting one up), for
    the members whose permission each needs (admin mode: all)."""
    return [(f"/{p.name}{path}", label) for p in LOADED.values() if club.plugin_on(p.name) and (admin or _ready(p))
            for label, path, perm in p.nav if perm is None or admin or club.may(mid, f"{p.name}.{perm}")]


def _ready(p: Plugin) -> bool:
    if not hasattr(p.mod, "nav_ready"):
        return True
    try:
        return bool(p.mod.nav_ready(p.api))
    except Exception:
        log.exception("plugin %s: nav_ready failed", p.name)
        return False


def site_tabs(club) -> list[tuple[str, str]]:
    """Admin → Site tabs for the plugins that are on and have a settings page: (address, title)."""
    return [(f"/{p.name}/admin", p.title) for p in LOADED.values() if p.has_admin and club.plugin_on(p.name)]


def calendar_links(club) -> list[tuple[str, str]]:
    """Buttons for the calendar event form's online link, from the plugins that are on."""
    out = []
    for p in LOADED.values():
        if club.plugin_on(p.name) and hasattr(p.mod, "calendar_links"):
            try:
                out += [(str(label), f"/{p.name}{url}" if str(url).startswith("/") else str(url)) for label, url in p.mod.calendar_links(p.api)]  # "/x": the plugin's page
            except Exception:
                log.exception("plugin %s: calendar_links failed", p.name)
    return out


def console_attendance(club) -> list[str]:
    """The names plugins see in a video meeting right now who aren't members, for the console's Attendance. Each
    plugin's `console_attendance(api)` also runs whenever the console renders or refreshes attendance and just before
    the minutes are submitted, so it can mark members from what it knows (api.mark_present)."""
    out = []
    for p in LOADED.values():
        if club.plugin_on(p.name) and hasattr(p.mod, "console_attendance"):
            try:
                out += [one_line(str(n)) for n in p.mod.console_attendance(p.api)]
            except Exception:
                log.exception("plugin %s: console_attendance failed", p.name)
    return out


def guides(club) -> dict[str, str]:
    """Help guides of the plugins that are on: {"plugin-<name>": title}."""
    return {f"plugin-{p.name}": p.title for p in LOADED.values() if p.guide and club.plugin_on(p.name)}


def rows(club) -> list[dict]:
    """Site → Features: every installed plugin, running or not, and why not."""
    out = [{"name": p.name, "title": p.title, "description": p.description, "uses": uses(p), "shares": shares(p),
            "on": club.plugin_on(p.name), "hold": p.name in club.plugins_hold, "problem": "", "admin": f"/{p.name}/admin" if p.has_admin else ""} for p in LOADED.values()]
    return out + [{"name": n, "title": n, "description": "", "uses": [], "shares": [], "on": club.plugin_on(n),
                   "hold": n in club.plugins_hold, "problem": why, "admin": ""} for n, why in PROBLEMS.items()]


def uses(p: Plugin) -> list[dict]:
    """What a plugin says it uses, ready to show: {kind, about, why}, `about` in words."""
    return [{"kind": KINDS[k], "about": ABOUT[d["about"]], "why": d["why"], "audience": d["about"]} for k, d in p.data.items()]


def shares(p: Plugin) -> list[dict]:
    return [d | {"about_text": ABOUT[d["about"]]} for d in p.shares]


def disclosure(club, signed_in: bool) -> dict[str, list[dict]]:
    """The privacy page's Plugins part: {audience: [{plugin, text, why}]} for the plugins that are on; signed out, only what's
    about visitors."""
    out: dict[str, list[dict]] = {}
    for p in LOADED.values():
        if not club.plugin_on(p.name):
            continue
        for u in uses(p):
            out.setdefault(u["audience"], []).append({"plugin": p.title, "text": u["kind"], "why": u["why"]})
        for s in shares(p):
            out.setdefault(s["about"], []).append({"plugin": p.title, "text": f"Shares {s['what']} with {s['with']}", "why": s["why"]})
    return {a: out[a] for a in ABOUT if a in out and (signed_in or a == "visitors")}
