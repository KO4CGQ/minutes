"""Club config and people.

People come only from the roster (the Google Sheet, or the app's own copy once moved; synced to
data/roster.json by `club-minutes roster`). club.yaml (local only, never in git) holds app settings.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import threading
from dataclasses import dataclass, field, fields
from functools import cached_property, wraps
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from . import regions
from .meeting import SECTIONS

ROOT = Path(os.environ.get("CLUB_MINUTES_ROOT", Path(__file__).resolve().parents[2]))
DATA = Path(os.environ.get("CLUB_MINUTES_DATA", ROOT / "data"))
MEETINGS = DATA / "meetings"
ROSTER_CACHE = DATA / "roster.json"
ROSTER_LOCK = threading.RLock()  # one roster change at a time: the sheet, then roster.json (read, change, write)
DATES: dict[str, str] = {}  # strftime formats by name (show_date), from club.yaml club: date_formats: when a Club loads


def set_dates(custom: dict | None) -> None:
    """The four a club sets, then the weekday forms built from them (a club may set those too)."""
    d = {"list": "%b %-d, %Y", "document": "%B %-d, %Y", "short": "%b %-d", "long": "%B %-d"}
    known = {*d, "day", "weekday", "day_year"}
    if not isinstance(custom or {}, dict) or any(k not in known or not isinstance(v, str) or not v.strip() for k, v in (custom or {}).items()):
        raise ValueError(f"club.yaml club: date_formats: {custom!r} isn't right: each of {', '.join(sorted(known))} "
                         'is a format in quotes, e.g. list: "%b %-d, %Y"')
    d.update(custom or {})
    DATES.clear()
    DATES.update({"day": f"%a {d['short']}", "weekday": f"%A, {d['long']}", "day_year": f"%a {d['list']}", **d})


def show_date(d: dt.date | str, kind: str = "list") -> str:
    """A date (or ISO "2026-11-05") in the club's format. US defaults: list Nov 5, 2026; document November 5, 2026;
    short Nov 5; long November 5; day Thu Nov 5; weekday Thursday, November 5; day_year Thu Nov 5, 2026."""
    return (dt.date.fromisoformat(d) if isinstance(d, str) else d).strftime(DATES[kind])


set_dates(None)
_depth = 0  # nesting of roster_locked calls in this process (changed only while ROSTER_LOCK is held)


def roster_locked(fn):
    """For whatever changes the roster: the web app's threads (ROSTER_LOCK) and the nightly sync, a separate process
    (roster.lock in the data folder, taken by the outermost call only, so one locked function can call another)."""
    @wraps(fn)
    def run(*a, **k):
        global _depth
        with ROSTER_LOCK:
            f = None
            if _depth == 0:
                DATA.mkdir(parents=True, exist_ok=True)
                f = open(DATA / "roster.lock", "w")
                try:
                    fcntl.flock(f, fcntl.LOCK_EX)
                except BaseException:
                    f.close()
                    raise
            _depth += 1
            try:
                return fn(*a, **k)
            finally:
                _depth -= 1
                if f:
                    fcntl.flock(f, fcntl.LOCK_UN)
                    f.close()
    return run
# Preview mode (deploy/preview.sh): set to the branch name. Email goes to an outbox file instead of
# SMTP, and roster edits go to a local copy of the sheet, never the real one.
PREVIEW = os.environ.get("CLUB_MINUTES_PREVIEW") or None
# Stage mode (a staging server: docs/server-admin.md → Staging server): prod's data, pulled one way each night. Email
# goes only to the person clicking (auth.send_email); the app doesn't update itself.
STAGE = bool(os.environ.get("CLUB_MINUTES_STAGE"))


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _server_timezone() -> str:
    """The server's time zone name (/etc/localtime links into the zoneinfo folder), else UTC."""
    path = os.path.realpath("/etc/localtime")
    return path.split("zoneinfo/", 1)[1] if "zoneinfo/" in path else "UTC"


def _agenda(raw) -> list[dict]:
    """club.yaml meeting: sections: as [{name, hint}]: each a name or {name, hint}; one line each, no repeats.
    A mistake stops the app at start, saying what's wrong."""
    out = []
    for x in raw or []:
        x = x if isinstance(x, dict) else {"name": x}
        name = " ".join(str(x.get("name") or "").split())
        if not name or len(name) > 60 or name in ("Attendance", "Review") or name in [s["name"] for s in out]:
            raise ValueError(f"club.yaml meeting: sections: {x.get('name')!r} isn't a usable section name "
                             "(one line, at most 60 characters, not Attendance or Review, and listed once)")
        out.append({"name": name, "hint": " ".join(str(x.get("hint") or "").split())})
    return out


# Optional parts of the app, each off unless club.yaml turns it on (`modules: {chow: true}`):
# chow: a rotation of members bringing food to meetings (the Chow page, reminders, the console's step, a missed-night fee).
MODULES = {"chow": False}
CALLSIGN = re.compile(r"^[A-Z]{1,2}\d[A-Z]{1,3}$", re.I)


def num(v) -> int | None:
    """A whole number typed or sent as text, else None. Not str.isdigit(): it accepts "²" and "⁰", which int() can't read."""
    v = str(v if v is not None else "").strip()
    return int(v) if v.isascii() and v.isdecimal() and len(v) <= 9 else None


def valid_email(s: str) -> bool:
    """name@domain.tld, no spaces, at most 254 characters. No regex: a crafted address can't make it slow."""
    if not s or len(s) > 254 or any(ch.isspace() for ch in s) or s.count("@") != 1:
        return False
    local, domain = s.split("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def inside(folder: Path, name: str) -> Path:
    """folder/name, refused unless it really is a file directly in that folder (no "..", no slashes)."""
    base = os.path.realpath(folder)
    p = os.path.realpath(os.path.join(base, name))
    if not p.startswith(base + os.sep) or os.path.dirname(p) != base:
        raise ValueError(f"{name!r} isn't a plain file name.")
    return Path(p)


def one_line(v) -> str:
    """A value for a log line: line breaks shown as \\n, so typed text can't add a fake log entry."""
    return str(v).replace("\r", "\\r").replace("\n", "\\n")


def _n(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


@dataclass
class Member:
    id: str
    first: str
    last: str
    short: str | None = None
    callsign: str | None = None
    email: str | None = None
    role: str | None = None  # officer title(s)
    officer: bool = False
    board: bool = False
    license_class: str | None = None
    minutes_editor: bool = False  # may lead meetings and edit minutes (officers always may)
    calendar_editor: bool = False  # may change the club calendars (leaders always may)
    status: str = "Active"
    aliases: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)  # from the Membership Log tab
    member_since: str | None = None
    phone: str | None = None
    address: str | None = None
    birthday: str | None = None  # MM-DD
    share: list[str] = field(default_factory=list)  # fields shown to all members (officers see everything)

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}".strip()


# Settings an admin may change on Admin → Site (saved in site-settings.json, over club.yaml's): (section, key).
# Never sign-in, admins, email test mode or AI: a mistake there locks people out or needs the server anyway.
SITE_KEYS = {("club", "date_formats")}


def _overlay(cfg: dict, site: dict) -> dict:
    """club.yaml with the Site page's settings on top: a key there replaces club.yaml's, section by section."""
    out = dict(cfg)
    for sec, keys in site.items():
        out[sec] = {**(cfg.get(sec) or {}), **keys}
    return out


class Club:
    def __init__(self, path: Path | None = None, roster: list[dict] | None = None):
        path = path or Path(os.environ.get("CLUB_MINUTES_CONFIG", ROOT / "club.yaml"))
        self.yaml = yaml.safe_load(path.read_text()) or {}
        cfg = _overlay(self.yaml, self.site_settings)
        self._configure(cfg)

        self._mtime = None  # set when the people come from the roster cache, so refresh() can notice a sync
        if roster is None and "members" in cfg:  # inline members (tests, example config)
            roster = [{"id": k, **(v or {})} for k, v in cfg["members"].items()]
        if roster is None and ROSTER_CACHE.exists():
            self._mtime = ROSTER_CACHE.stat().st_mtime
            roster = json.loads(ROSTER_CACHE.read_text())
        self._load(roster or [])

    @property
    def site_settings(self) -> dict:
        """What admins saved on Admin → Site: {section: {key: value}}, over club.yaml's."""
        p = DATA / "site-settings.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_site_setting(self, section: str, key: str, value) -> None:
        """Set one Site page setting (None: back to club.yaml's). Checked as the app checks club.yaml at start, then
        used at once; a ValueError says what's wrong and nothing changes."""
        if (section, key) not in SITE_KEYS:
            raise ValueError(f"{section}: {key} can't be changed here.")
        site = self.site_settings
        if value is None:
            site.get(section, {}).pop(key, None)
            site = {k: v for k, v in site.items() if v}
        else:
            site.setdefault(section, {})[key] = value
        cfg = _overlay(self.yaml, site)
        probe = Club.__new__(Club)
        try:
            probe._configure(cfg)
            _ = (probe.meeting_weekday, probe.meeting_time, probe.timezone)
        except Exception:
            set_dates(self.info.get("date_formats"))  # the probe may have set the shared formats already
            raise
        p = DATA / "site-settings.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(site, indent=1))
        tmp.replace(p)
        self._configure(cfg)

    def _configure(self, cfg: dict) -> None:
        """The settings, from club.yaml with the Site page's on top. Raises ValueError for a wrong one."""
        self.info = cfg.get("club", {})
        set_dates(self.info.get("date_formats"))  # one club per process: the last Club loaded sets them
        self._yaml_links = cfg.get("links", [])  # until an admin edits them on the Links page (links.json)
        self.auth = cfg.get("auth") or {}
        self.email = cfg.get("email") or {}
        self.redact = [_n(x) for x in (cfg.get("privacy") or {}).get("never_record", [])]
        self.ai = cfg.get("ai") or {}  # AI assistance (ai.py): model, and whether it's on
        self.admins = {str(x).lower() for x in cfg.get("admins") or []}  # may turn on admin mode (callsign ids)
        # Offices whose holders add new members to the roster (the Secretary keeps it, usually).
        self.offices = cfg.get("offices") or []  # the bylaws' offices (offices.py); a default list if empty
        self.roster_keepers = [str(x).lower() for x in cfg.get("roster_keepers") or ["Secretary", "Treasurer"]]
        cert = (cfg.get("treasurer") or {}).get("certificate") or {}
        # A certificate each member buys once (a "bond" at some clubs): only if club.yaml names it; off otherwise.
        self.certificate = {"name": " ".join(str(cert["name"]).split())[:80],
                            "short": " ".join(str(cert.get("short") or "certificate").split())[:30],
                            "note": " ".join(str(cert.get("note") or "").split())} if cert.get("name") else None
        self.bylaws = cfg.get("bylaws") or {}
        self._sections = _agenda((cfg.get("meeting") or {}).get("sections"))  # refs: the sections the app cites; arrears_note: added to the arrears email
        self.calendar = cfg.get("calendar") or {}  # holidays / elections: region codes (regions/), none by default
        for k in ("holidays", "elections"):
            regions._module(self.calendar.get(k))  # a misspelled region stops the app at start, with what's wrong
        self.modules = {**MODULES, **{k: bool(v) for k, v in (cfg.get("modules") or {}).items() if k in MODULES}}

    @property
    def meeting_weekday(self) -> int:
        """The weekly meeting's day (Monday 0), from club.yaml `club: meeting_day:`. Required: no club's day is a default."""
        day = str(self.info.get("meeting_day") or "").strip().lower()
        if day not in WEEKDAYS:
            raise ValueError(f"club.yaml club: meeting_day: {self.info.get('meeting_day')!r} isn't a day of the week (e.g. Thursday)")
        return WEEKDAYS.index(day)

    @property
    def meeting_time(self) -> str:
        """The meeting's start, "HH:MM" (24-hour), from club.yaml `club: meeting_time:`. Required. Unquoted, YAML reads
        19:30 as 1170 (minutes): taken as that time, not an error."""
        raw = self.info.get("meeting_time")
        t = f"{raw // 60:02d}:{raw % 60:02d}" if isinstance(raw, int) and not isinstance(raw, bool) else str(raw or "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            raise ValueError(f"club.yaml club: meeting_time: {raw!r} isn't a time like \"19:30\" (24-hour, in quotes)")
        return t

    @property
    def timezone(self) -> str:
        """The club's time zone: club.yaml `club: timezone:`, else the server's own (setup.sh sets it)."""
        tz = str(self.info.get("timezone") or "").strip() or _server_timezone()
        try:
            ZoneInfo(tz)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError(f"club.yaml club: timezone: {tz!r} isn't a time zone (e.g. America/New_York)")
        return tz

    def sections(self) -> list[str]:
        """The meeting's sections, in order: club.yaml `meeting: sections:` (built-in ones and the club's own), else the
        built-ins with something behind them. Upcoming Chow is there only with the chow module (before Upcoming Events
        if the list doesn't place it)."""
        names = [s["name"] for s in self._sections] if self._sections else \
            [s for s in SECTIONS if s not in ("Winners", "Upcoming Chow")]
        names = [s for s in names if s != "Upcoming Chow" or self.has("chow")]
        if self.has("chow") and "Upcoming Chow" not in names:
            i = names.index("Upcoming Events") if "Upcoming Events" in names else len(names)
            names.insert(i, "Upcoming Chow")
        return names

    def section_hints(self) -> dict[str, str]:
        """The console's hint for each of the club's own sections that club.yaml gives one for."""
        return {s["name"]: s["hint"] for s in self._sections if s.get("hint")}

    def cite(self, rule: str) -> str:
        """" (bylaws VII.B)" for a rule club.yaml gives a section for (bylaws: refs:), else "": the sentence reads without it."""
        ref = " ".join(str((self.bylaws.get("refs") or {}).get(rule) or "").split())[:20]
        return f" (bylaws {ref})" if ref else ""

    def has(self, module: str) -> bool:
        """Whether an optional part of the app is turned on in club.yaml (`modules:`)."""
        return self.modules.get(module, False)

    @property
    def links(self) -> list[dict]:
        """Club links: label, url, optional icon, and where they show (page / minutes, both by default)."""
        p = DATA / "links.json"
        return json.loads(p.read_text()) if p.exists() else self._yaml_links

    def save_links(self, links: list[dict]) -> None:
        p = DATA / "links.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(links, indent=1))
        tmp.replace(p)

    def _load(self, roster: list[dict]) -> None:
        known = {f.name for f in fields(Member)}
        self.members: dict[str, Member] = {}
        for d in roster:
            self.members[d["id"]] = Member(**{k: v for k, v in d.items() if k in known})
        self._auto_short_names()
        # A former callsign (e.g. before a vanity change) listed in aliases maps to the member.
        self.former_ids = {a.lower(): m.id for m in self.members.values()
                           for a in m.aliases if CALLSIGN.match(a)}
        for m in self.members.values():  # callsign aliases aren't name spellings
            m.aliases = [a for a in m.aliases if not CALLSIGN.match(a)]
        self.__dict__.pop("_speaker_patterns", None)  # cached; rebuilt from the new names

    def refresh(self) -> bool:
        """Reload people if the roster cache changed on disk (e.g. the nightly sync). Cheap: one stat."""
        if self._mtime is None or not ROSTER_CACHE.exists():
            return False
        mtime = ROSTER_CACHE.stat().st_mtime
        if mtime == self._mtime:
            return False
        self._mtime = mtime
        self._load(json.loads(ROSTER_CACHE.read_text()))
        return True

    def canonical(self, mid: str) -> str:
        """Current id for a member id that may be an old callsign."""
        return self.former_ids.get(mid, mid)

    def _auto_short_names(self) -> None:
        """'Amy A' / 'Amy B' style names when a first name is shared."""
        by_first: dict[str, list[Member]] = {}
        for m in self.members.values():
            by_first.setdefault(m.first.lower(), []).append(m)
        for group in by_first.values():
            for m in group:
                if not m.short and len(group) > 1 and m.last:
                    m.short = f"{m.first} {m.last[0]}"

    def email_only_to(self) -> set[str] | None:
        """Test mode: if club.yaml has email.only_to (callsigns or addresses), mail goes only to them."""
        only = self.email.get("only_to")
        if only is None:
            return None
        out = set()
        for x in only:
            m = self.members.get(self.canonical(str(x).lower()))
            addr = m.email if m else str(x)
            if addr and "@" in addr:
                out.add(addr.strip().lower())
        return out

    # ---- permissions ----
    def can_lead(self, mid: str) -> bool:
        """Officers and members ticked minutes_editor may lead a meeting and edit minutes."""
        m = self.members.get(mid)
        return bool(m and (m.officer or m.minutes_editor))

    can_edit = can_lead

    def can_edit_calendar(self, mid: str) -> bool:
        """Leaders, and members ticked calendar_editor (say, the net manager), may change the club calendars."""
        m = self.members.get(mid)
        return bool(m and (self.can_lead(mid) or m.calendar_editor))

    @property
    def app_admins(self) -> set[str]:
        """Admins added in the app (Admin → Security → Admins), besides club.yaml's `admins:` (self.admins)."""
        p = DATA / "admins.json"
        return set(json.loads(p.read_text())) if p.exists() else set()

    @property
    def all_admins(self) -> set[str]:
        return {self.canonical(a) for a in self.admins | self.app_admins}  # club.yaml may still have an old call

    def save_app_admins(self, ids: set[str]) -> None:
        p = DATA / "admins.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(ids)))
        tmp.replace(p)

    def is_admin(self, mid: str | None) -> bool:
        """Listed under admins: in club.yaml or added in the app. Admin rights apply only while admin mode is on."""
        return bool(mid) and mid in self.all_admins

    def can_see(self, viewer: str | None, mid: str, fld: str, elevated: bool = False) -> bool:
        """Contact details: officers, admins in admin mode, and the member always; others only what was shared."""
        v, m = self.members.get(viewer or ""), self.members.get(mid)
        return bool(m and v and (v.officer or elevated or v.id == m.id or fld in m.share))

    def keeps_roster(self, mid: str | None) -> bool:
        m = self.members.get(mid or "")
        return bool(m and m.role and any(k in m.role.lower() for k in self.roster_keepers))

    @property
    def secretaries(self) -> list[str]:
        """Members whose title includes Secretary (they keep the roster)."""
        return [m.id for m in self.members.values() if m.role and "secretary" in m.role.lower()]

    @property
    def leaders(self) -> list[str]:
        return [mid for mid in self.members if self.can_lead(mid)]

    # ---- privacy ----
    def is_redacted(self, text: str) -> bool:
        t = _n(text)
        return any(r and r in t for r in self.redact)

    # ---- names ----
    def name(self, mid: str | None) -> str:
        m = self.members.get(self.canonical(mid or ""))
        return m.name if m else (mid or "").title()

    def short(self, mid: str) -> str:
        m = self.members.get(self.canonical(mid))
        return (m.short or m.first or m.last) if m else mid.title()

    def resolve_surname(self, word: str) -> str | None:
        """Map an attendance-list surname (with spelling drift) to a member id."""
        w = word.lower()
        for m in self.members.values():
            if w == m.last.lower() or w in (a.lower() for a in m.aliases):
                return m.id
        return None

    @cached_property
    def _speaker_patterns(self) -> list[tuple[re.Pattern, str]]:
        """Patterns for 'who is this report about', most specific first."""
        pats: list[tuple[str, str]] = []
        firsts: dict[str, list[str]] = {}
        for m in self.members.values():
            if not m.first:
                continue
            firsts.setdefault(m.first.lower(), []).append(m.id)
            pats.append((rf"{m.first}(?:'?s)?\s+{m.last}", m.id))
            pats.append((rf"{m.first}(?:'s)?\s+{m.last[0]}\b\.?", m.id))  # "Amy A", "Amy's A"
            for a in m.aliases:
                pats.append((rf"{a}\b", m.id))
        for first, ids in firsts.items():
            if len(ids) == 1:
                pats.append((rf"{first}(?:'?s)?\b", ids[0]))  # "Amy", "Amys", "Amy's"
        return [(re.compile(rf"^\s*{p}", re.I), mid) for p, mid in pats]

    def speaker_of(self, text: str, present: list[str] | None = None) -> str | None:
        """Guess which member a report is about from its first words.

        An ambiguous first name ("Amy") is settled by who was present, if only one was.
        """
        t = text.replace("’", "'")
        for pat, mid in self._speaker_patterns:
            if pat.match(t):
                return mid
        word = re.match(r"\s*([A-Za-z]+?)(?:'?s)?\b", t)
        if present and word:
            ids = [m.id for m in self.members.values()
                   if m.first.lower() == word.group(1).lower() and m.id in present]
            if len(ids) == 1:
                return ids[0]
        return None
