"""Club config and people.

People come only from the roster (the Google Sheet, or the app's own copy once moved; synced to
data/roster.json by `club-minutes roster`). club.yaml (local only, never in git) holds app settings.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field, fields
from functools import cached_property, wraps
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from . import regions
from .meeting import SECTIONS

log = logging.getLogger(__name__)
MEMBER_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")  # same as callsign.py / roster.rename_id

ROOT = Path(os.environ.get("CLUB_MINUTES_ROOT", Path(__file__).resolve().parents[2]))
_site_lock = threading.Lock()
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


def unname(text: str, names: set[str]) -> str:
    """`text` without one of `names` (the longest that fits) at its start, and the dash or comma after it."""
    for n in sorted(names - {""}, key=len, reverse=True):
        if re.match(rf"{re.escape(n)}\b", text, re.IGNORECASE):
            return re.sub(r"^[ \t]*[-–—:,]?[ \t]*", "", text[len(n):]).lstrip("\n")  # not a list's "- " below
    return text


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
                f = open(DATA / "roster.lock", "w")  # noqa: SIM115 (held until the lock is released, then closed)
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
# goes only to the person clicking (auth.send_email); it updates from what Admin → Updates → Update from says (updates.source()).
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
CALLSIGN = re.compile(r"^[A-Z]{1,2}\d[A-Z]{1,4}$", re.IGNORECASE)  # 4 letters: some countries issue them, and examples like N0CALL can't be anyone's


def num(v) -> int | None:
    """A whole number typed or sent as text, else None. Not str.isdigit(): it accepts "²" and "⁰", which int() can't read."""
    v = str(v if v is not None else "").strip()
    return int(v) if v.isascii() and v.isdecimal() and len(v) <= 9 else None


def valid_email(s: str) -> bool:
    """name@example.org, no spaces, at most 254 characters. No regex: a crafted address can't make it slow."""
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
    minutes_editor: bool = False  # given the Minutes editor role (roles.json; before it, the roster's tick)
    calendar_editor: bool = False  # given the Calendar editor role (the same)
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


# What a member may do, and the roles that allow it (Admin → Site → Roles). Each check in the app asks Club.may(member,
# permission); admins, in admin mode, may do everything (admin isn't a role, and no role edit can give it). A role is
# given by whole office titles (exact, any capitals; titles that aren't offices give nothing) and by members picked by
# hand; admins change both, and what each role allows, on Site → Roles (roles.json).
PERMISSIONS = {
    "lead": "lead meetings and edit the minutes",
    "correct": "correct minutes in the archive",
    "calendar": "change the club calendars",
    "chow": "change the chow schedule",
    "contacts": "see every member's contact details",
    "oversee": "see Roster changes and undo them",
    "moderate": "moderate: any business item, motions, comments, pictures, the calendar log",
    "treasurer": "look at the Treasurer pages",
    "money": "record money and change the Treasurer settings",
    "members": "add members and edit their details",
    "emails": "change another member's email",
    "status": "change a member's status",
    "roster_emails": "get the roster emails",
    "applications": "receive membership applications",
}
# The built-in roles: key: (name, what it allows until an admin changes it). The keys are older than the names:
# "board" is Minutes editor and "minutes_editor" is Meeting leader (roles.json and the roster's columns keep the keys).
ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "officer": ("Officer", ("lead", "calendar", "chow", "contacts", "oversee", "moderate", "treasurer")),
    "board": ("Minutes editor", ("correct",)),
    "treasurer": ("Treasurer", ("treasurer", "money")),
    "roster_keeper": ("Roster keeper", ("members", "emails", "status")),
    "secretary": ("Secretary", ("roster_emails", "applications")),
    "minutes_editor": ("Meeting leader", ("lead", "calendar")),
    "calendar_editor": ("Calendar editor", ("calendar",)),
}
PICKED = ("minutes_editor", "calendar_editor")  # built-in roles given only by hand (before roles.json: the roster's ticks)
DEFAULT_OFFICES = ["President", "Vice President", "Secretary", "Treasurer"]



def office_list(raw: list) -> list[dict]:
    """[{title, officer, seats}]: club.yaml entries are a title (an officer's office, one seat) or
    {title, officer: false, seats: 3} (e.g. the elected trustees). Empty: DEFAULT_OFFICES."""
    out = []
    for o in raw or DEFAULT_OFFICES:
        o = {"title": o} if isinstance(o, str) else dict(o)
        out.append({"title": str(o["title"]), "officer": bool(o.get("officer", True)), "seats": int(o.get("seats", 1))})
    return out

def _quorum(raw) -> dict | None:
    """club.yaml `meeting: quorum: {percent: 30, officers: 2}` as {percent, officers} (each a whole number or None), or None
    when neither is set. Raises ValueError for a wrong one."""
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict) or set(raw) - {"percent", "officers"}:
        raise ValueError("club.yaml meeting: quorum: needs percent and/or officers")
    out = {}
    for k, hi in (("percent", 100), ("officers", 50)):
        v = raw.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= hi):
            raise ValueError(f"club.yaml meeting: quorum: {k}: {v!r} isn't a whole number from 1 to {hi}")
        out[k] = v
    return out if any(v is not None for v in out.values()) else None


# Membership applications (Admin → Site → Membership; club.yaml `membership:`): how long the link works, the vote, how
# long a refusal lasts, what's kept. `club: invite_after_visits` is the tab's other setting. Questions: [{label, type: yesno|several|short|long|grid, options | rows + cols}].
DEFAULT_STATEMENT = ("By signing, you confirm that what you've written here is true, that you've read the club's bylaws "
                     "and agree to follow them, and that you'll pay the club's dues and fees if you become a member.")
FIELD_DEFAULTS = {"callsign": "optional", "first_licensed": "optional", "license_class": "optional", "expires": "optional",
                  "street": "required", "city": "required", "state": "required", "zip": "required", "phone": "optional", "occupation": "optional"}
DEFAULT_INVITE = ("Hello {guest},\n\nThank you for visiting {club}. Here is your membership application:\n\n{link}\n\n"
                  "It's yours alone, so please don't share it. It works for {days} and until you send it in.")
INVITE_WORDS = {"guest": "the guest's name", "club": "the club's name", "link": "their application link (required)",
                "days": "how long the link works, e.g. 30 days"}
MEMBERSHIP_DEFAULTS = {"link_days": 30, "listed_days": 0, "vote": True, "vote_passes": "unanimous", "reapply_months": 24,
                       "keep_days": 90, "statement": DEFAULT_STATEMENT, "questions": [], "welcome": "", "fields": FIELD_DEFAULTS,
                       "invite": DEFAULT_INVITE}
FIELD_MODES = {"required": "Required", "optional": "Optional", "hidden": "Not asked"}  # name, email and the signature are always required
VOTE_PASSES = {"unanimous": "Unanimous", "two_thirds": "Two thirds", "majority": "A majority"}
QUESTION_TYPES = {"yesno": "Yes or no", "several": "Pick several", "short": "Short text", "long": "Long text", "grid": "Grid of ticks"}
_MEMBERSHIP_RANGES = {"link_days": (1, 365), "listed_days": (0, 365), "reapply_months": (0, 120), "keep_days": (1, 3650)}


def clean_questions(raw) -> list[dict]:
    """The club's own application questions, checked: each {label, type} plus options (several) or rows and cols (grid),
    lists of distinct non-empty lines. Raises ValueError saying which is wrong."""
    if not isinstance(raw, list) or len(raw) > 40:
        raise ValueError("The questions are a list of at most 40.")
    out = []
    for q in raw:
        if not isinstance(q, dict) or q.get("type") not in QUESTION_TYPES:
            raise ValueError("Every question needs a type: " + ", ".join(QUESTION_TYPES) + ".")
        label = " ".join(str(q.get("label") or "").split())[:200]
        if not label:
            raise ValueError("Every question needs its words.")
        item = {"label": label, "type": q["type"]}
        for key, need in (("options", "several"), ("rows", "grid"), ("cols", "grid")):
            if q["type"] == need:
                lines = list(dict.fromkeys(" ".join(str(x).split())[:100] for x in q.get(key) or [] if str(x).strip()))
                if not lines or len(lines) > 40:
                    raise ValueError(f"{label}: give {key} (one to 40).")
                item[key] = lines
        out.append(item)
    return out


def _membership(raw) -> dict:
    """The membership: settings with defaults filled in. Raises ValueError for a wrong one (as for the other settings)."""
    raw = raw or {}
    if not isinstance(raw, dict) or set(raw) - set(MEMBERSHIP_DEFAULTS):
        raise ValueError("club.yaml membership: has a setting the app doesn't know")
    out = {**MEMBERSHIP_DEFAULTS, **raw}
    for k, (lo, hi) in _MEMBERSHIP_RANGES.items():
        if isinstance(out[k], bool) or not isinstance(out[k], int) or not lo <= out[k] <= hi:
            raise ValueError(f"club.yaml membership: {k}: {out[k]!r} isn't a whole number from {lo} to {hi}")
    if not isinstance(out["vote"], bool) or out["vote_passes"] not in VOTE_PASSES:
        raise ValueError("club.yaml membership: vote is true or false, vote_passes one of " + ", ".join(VOTE_PASSES))
    if not isinstance(out["statement"], str) or len(out["statement"]) > 5000:
        raise ValueError("club.yaml membership: statement is text of at most 5000 characters")
    if not isinstance(out["welcome"], str) or len(out["welcome"]) > 5000:
        raise ValueError("club.yaml membership: welcome is text of at most 5000 characters")
    f = out["fields"]
    if not isinstance(f, dict) or set(f) - set(FIELD_DEFAULTS) or set(f.values()) - set(FIELD_MODES):
        raise ValueError("club.yaml membership: fields maps " + ", ".join(FIELD_DEFAULTS) + " to " + ", ".join(FIELD_MODES))
    out["fields"] = {**FIELD_DEFAULTS, **f}
    check_invite(out["invite"])
    out["questions"] = clean_questions(out["questions"])
    return out


def check_invite(text) -> None:
    """The invitation email's words: plain text with {link} in it, and no {word} the app doesn't fill in."""
    if not isinstance(text, str) or len(text) > 5000:
        raise ValueError("club.yaml membership: invite is text of at most 5000 characters")
    if "{link}" not in text:
        raise ValueError("The invitation email needs {link} in it, or the guest can't apply.")
    if bad := [w for w in re.findall(r"\{([^{}]*)\}", text) if w not in INVITE_WORDS]:
        raise ValueError("The invitation email can use only " + ", ".join("{" + w + "}" for w in INVITE_WORDS) + f"; not {{{bad[0]}}}.")


# Settings an admin may change on Admin → Site (saved in site-settings.json, over club.yaml's): (section, key).
# Never sign-in, admins or email test mode: a mistake there locks people out or needs the server anyway.
SITE_KEYS = {("club", k) for k in ("date_formats", "name", "callsign", "meeting_day", "meeting_time", "timezone",
                                    "invite_after_visits", "remind_days_before", "public_calendars", "supports_app", "pictures_per_day", "names", "ham")} | {
    ("meeting", "sections"), ("meeting", "quorum"), ("modules", "chow"), ("calendar", "holidays"), ("calendar", "extra_holidays"), ("calendar", "elections"),
    ("bylaws", "refs"), ("bylaws", "arrears_note"), ("treasurer", "certificate"), ("privacy", "never_record"),
    ("", "offices"), ("", "roster_keepers"), ("webhooks", "url"), ("webhooks", "kinds")} | {("membership", k) for k in MEMBERSHIP_DEFAULTS}  # "": a top-level club.yaml setting


# club.yaml settings renamed since 1.0.0: (section, old name) -> (section, new name). The old name keeps working
# until 2.0, with a line on the Admin page (docs/developers.md → Compatibility). Never remove an entry before 2.0.
RENAMED: dict[tuple[str, str], tuple[str, str]] = {}


def _renamed(cfg: dict) -> tuple[dict, list[str]]:
    """club.yaml with renamed settings under their new names (a new name given too wins), and a line for each."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in cfg.items()}
    notes = []
    for (sec, old), (nsec, new) in RENAMED.items():
        if isinstance(out.get(sec), dict) and old in out[sec]:
            v = out[sec].pop(old)
            out.setdefault(nsec, {}).setdefault(new, v)
            notes.append(f"club.yaml: {sec}: {old}: is now called {nsec}: {new}:. The old name works until version 2.0; rename it when you can.")
    return out, notes


def _overlay(cfg: dict, site: dict) -> dict:
    """club.yaml with the Site page's settings on top: a key there replaces club.yaml's, section by section."""
    out = dict(cfg)
    for sec, keys in site.items():
        if sec == "":  # top-level settings (offices:, roster_keepers:) replace club.yaml's as a whole
            out.update(keys)
        else:
            out[sec] = {**(cfg.get(sec) or {}), **keys}
    return out


class Club:
    def __init__(self, path: Path | None = None, roster: list[dict] | None = None):
        path = path or Path(os.environ.get("CLUB_MINUTES_CONFIG", ROOT / "club.yaml"))
        self.yaml, self.renamed = _renamed(yaml.safe_load(path.read_text()) or {})
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
        try:
            return json.loads(p.read_text()) if p.exists() else {}
        except ValueError:
            log.exception("%s is damaged; using club.yaml's settings", p)  # every page reads it: a torn file mustn't stop them all
            return {}

    def save_site_settings(self, values: dict) -> None:
        """Set Site page settings together ({(section, key): value}; None: back to club.yaml's). Checked as the app
        checks club.yaml at start, then used at once; a ValueError says what's wrong and nothing changes."""
        with _site_lock:  # two admins saving at once: one after the other, neither lost
            if bad := [f"{s}: {k}" for s, k in values if (s, k) not in SITE_KEYS and not (s in ("plugins", "plugin_hold") and re.fullmatch(r"[a-z0-9]+", k))]:
                raise ValueError(f"{', '.join(bad)} can't be changed here.")
            site = self.site_settings
            for (section, key), value in values.items():
                if value is None:
                    site.get(section, {}).pop(key, None)
                else:
                    site.setdefault(section, {})[key] = value
            site = {k: v for k, v in site.items() if v}
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
        self.membership = _membership(cfg.get("membership"))
        for k, lo, hi in (("invite_after_visits", 1, 50), ("remind_days_before", 0, 60)):  # read later, so checked now
            v = self.info.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi):
                raise ValueError(f"club.yaml club: {k}: {v!r} isn't a whole number from {lo} to {hi}")
        set_dates(self.info.get("date_formats"))  # one club per process: the last Club loaded sets them
        self._yaml_links = cfg.get("links", [])  # until an admin edits them on the Links page (links.json)
        self.auth = cfg.get("auth") or {}
        self.email = cfg.get("email") or {}
        self.never_record = [" ".join(str(x).split()) for x in (cfg.get("privacy") or {}).get("never_record") or [] if str(x).strip()]
        self.redact = [_n(x) for x in self.never_record]
        self.admins = {str(x).lower() for x in cfg.get("admins") or []}  # may turn on admin mode (member ids or current callsigns)
        # Offices whose holders add new members to the roster (the Secretary keeps it, usually).
        self.offices = cfg.get("offices") or []  # the bylaws' offices (offices.py); a default list if empty
        self.roster_keepers = [str(x).lower() for x in cfg.get("roster_keepers") or ["Secretary", "Treasurer"]]
        cert = (cfg.get("treasurer") or {}).get("certificate") or {}
        # A certificate each member buys once (a "bond" at some clubs): only if club.yaml names it; off otherwise.
        self.certificate = {"name": " ".join(str(cert["name"]).split())[:80],
                            "short": " ".join(str(cert.get("short") or "certificate").split())[:30],
                            "note": " ".join(str(cert.get("note") or "").split())} if cert.get("name") else None
        self.bylaws = cfg.get("bylaws") or {}
        self.quorum = _quorum((cfg.get("meeting") or {}).get("quorum"))  # None: no quorum rule, the feature is off
        self._sections = _agenda((cfg.get("meeting") or {}).get("sections"))  # refs: the sections the app cites; arrears_note: added to the arrears email
        self.calendar = cfg.get("calendar") or {}  # holidays / elections: region codes (regions/), none by default
        for k in ("holidays", "elections"):
            regions.check(k, self.calendar.get(k))  # a misspelled region stops the app at start, with what's wrong
        regions.parse_extra(self.calendar.get("extra_holidays"))
        self.plugins_on = {k for k, v in (cfg.get("plugins") or {}).items() if v}  # installed plugins an admin ticked (plugins.py)
        self.plugins_hold = {k for k, v in (cfg.get("plugin_hold") or {}).items() if v}  # an update that doesn't fit them waits
        self.modules = {**MODULES, **{k: bool(v) for k, v in (cfg.get("modules") or {}).items() if k in MODULES}}

    @property
    def meeting_weekday(self) -> int | None:
        """The seed's day (Monday 0), from club.yaml `club: meeting_day:`: optional, used once to make the first meeting
        series (events.ensure_meeting). None if missing; a wrong one stops the start."""
        day = str(self.info.get("meeting_day") or "").strip().lower()
        if not day:
            return None
        if day not in WEEKDAYS:
            raise ValueError(f"club.yaml club: meeting_day: {self.info.get('meeting_day')!r} isn't a day of the week (e.g. Thursday)")
        return WEEKDAYS.index(day)

    @property
    def meeting_time(self) -> str | None:
        """The seed's start, "HH:MM" (24-hour), from club.yaml `club: meeting_time:`: optional, like meeting_day. Unquoted,
        YAML reads 19:30 as 1170 (minutes): taken as that time, not an error."""
        raw = self.info.get("meeting_time")
        if raw is None or raw == "":
            return None
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

    @property
    def ham(self) -> bool:
        """An amateur radio club: callsigns and license classes are shown and asked for. Unset: on when the club has a callsign."""
        return bool(self.info["ham"]) if self.info.get("ham") is not None else bool(self.info.get("callsign"))

    @property
    def label(self) -> str:
        """What the club is called in subjects, titles and sign-offs: its callsign (an amateur radio club), else its name,
        else "Club" (a new server before Site is filled in)."""
        return " ".join(str((self.ham and self.info.get("callsign")) or self.info.get("name") or "Club").split())

    @property
    def sign_off(self) -> str:
        """How emails end, before the app's name."""
        return "73," if self.ham else "Thanks,"

    def has(self, module: str) -> bool:
        """Whether an optional part of the app is turned on in club.yaml (`modules:`)."""
        return self.modules.get(module, False)

    def plugin_on(self, name: str) -> bool:
        return name in self.plugins_on

    @property
    def links(self) -> list[dict]:
        """Club links: label, url, optional icon, and where they show (page / minutes, both by default)."""
        p = DATA / "links.json"
        try:
            return json.loads(p.read_text()) if p.exists() else self._yaml_links
        except ValueError:
            log.exception("%s is damaged; using club.yaml's links", p)
            return self._yaml_links

    def save_links(self, links: list[dict]) -> None:
        p = DATA / "links.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(links, indent=1))
        tmp.replace(p)

    def _load(self, roster: list[dict]) -> None:
        known = {f.name for f in fields(Member)}
        self.members: dict[str, Member] = {}
        calls: set[str] = set()  # callsigns of rows kept so far: a later row can't take one as its id (the reverse is a legit reissued call)
        for n, d in enumerate(roster, 1):
            mid = d.get("id")
            # A hand-edited roster must not let one row replace another member (their email decides who signs in as them).
            if not (isinstance(mid, str) and MEMBER_ID.fullmatch(mid)) or mid in self.members or mid in calls:
                log.warning("Roster row %d skipped: its id is malformed, repeated or another member's callsign.", n)
                continue
            self.members[mid] = Member(**{k: v for k, v in d.items() if k in known})
            if d.get("callsign") and d["callsign"].lower() != mid:
                calls.add(d["callsign"].lower())
        self._auto_short_names()
        # A former callsign (e.g. before a vanity change) listed in aliases maps to the member. Never another member's
        # current callsign, and never one two members both list: else typing an admin's callsign into your own
        # "Other spellings" made you that admin (and their sessions yours).
        claims: dict[str, set[str]] = {}
        for m in self.members.values():
            for a in m.aliases:
                if CALLSIGN.match(a):
                    claims.setdefault(a.lower(), set()).add(m.id)
        self.former_ids = {a: next(iter(ids)) for a, ids in claims.items() if len(ids) == 1 and a not in self.members}
        # A current callsign that isn't the member's id (their id stayed when the callsign changed): club.yaml may name it.
        self._by_call = {m.callsign.lower(): m.id for m in self.members.values()
                         if m.callsign and m.callsign.lower() != m.id and m.callsign.lower() not in self.members}
        for m in self.members.values():  # callsign aliases aren't name spellings
            m.aliases = [a for a in m.aliases if not CALLSIGN.match(a)]
        if (picked := self._picked()) is not None:  # roles.json, once there is one, over the roster's old ticks
            for m in self.members.values():
                for k in PICKED:
                    setattr(m, k, m.id in picked.get(k, []))
        self.__dict__.pop("_speaker_patterns", None)  # cached; rebuilt from the new names
        if self.members and (stale := sorted(a for a in self.admins if self._admin_id(a) not in self.members)):
            log.warning("admins: in club.yaml names no current member: %s (a member id or a current callsign counts; a former one doesn't)", ", ".join(stale))

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
        if mid in self.members:  # an id is always its own member, even if someone now holds that callsign
            return mid
        return self.former_ids.get(mid) or self._by_call.get(mid, mid)

    def resolve(self, who: str) -> str | None:
        """The member id for a callsign (current or former) or an id, in any capitals; a callsign wins. None if no member."""
        w = who.strip().lower()
        for m in self.members.values():
            if (m.callsign or "").lower() == w:
                return m.id
        w = self.former_ids.get(w, w)
        return w if w in self.members else None

    def member_url(self, mid: str) -> str:
        """The address of a member's page: their lowercase callsign (an amateur radio club), else their id."""
        m = self.members.get(self.canonical(mid))
        return "/members/" + quote(m.callsign.lower() if m and m.callsign and self.ham else m.id if m else mid, safe="")

    def _auto_short_names(self) -> None:
        """A member's short name is their first name, plus as much of their last name as tells them apart from others
        with that first name: "Jane D" and "Jane S"; "Jane Do" and "Jane Du" when both start with D. Active members are
        told apart only from Active members (one who left doesn't make today's Jane "Jane D"); a member who's no longer
        Active, from everyone, so their name in old minutes still points to them. A short name set in the roster wins."""
        by_first: dict[str, list[Member]] = {}
        for m in self.members.values():
            by_first.setdefault(m.first.lower(), []).append(m)
        for group in by_first.values():
            for m in group:
                active = m.status.lower() == "active"
                others = [o.last.lower() for o in group if o is not m and (o.status.lower() == "active" or not active)]
                if m.short or not m.last or not others:
                    continue
                n = 1
                while n < len(m.last) and any(o.startswith(m.last[:n].lower()) for o in others):
                    n += 1
                m.short = f"{m.first} {m.last[:n]}"

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
    def _picked(self) -> dict | None:
        """roles.json, read again only when it changes (every permission check asks)."""
        p = DATA / "roles.json"
        try:
            st = p.stat()
        except FileNotFoundError:
            return None
        key = (p, st.st_mtime_ns, st.st_ino, st.st_size)  # each save replaces the file (a new inode): two saves in one clock tick differ
        if getattr(self, "_rj", (None, None))[0] != key:
            try:
                data = json.loads(p.read_text())
                if not isinstance(data, dict):
                    raise TypeError("not an object")
            except (OSError, ValueError, TypeError):
                log.exception("%s is damaged; using the roster's ticks", p)  # every permission check reads it
                data = None
            self._rj = (key, data)
        return self._rj[1]

    def office_list(self) -> list[dict]:
        return office_list(self.offices)

    def _default_offices(self, key: str) -> list[str]:
        offs = self.office_list()
        pick = {"officer": lambda o: o["officer"], "board": lambda o: True,
                "treasurer": lambda o: o["title"].lower() == "treasurer", "secretary": lambda o: o["title"].lower() == "secretary",
                "roster_keeper": lambda o: o["title"].lower() in self.roster_keepers}.get(key, lambda o: False)
        return [o["title"] for o in offs if pick(o)]

    def role_table(self) -> dict[str, dict]:
        """Every role, built-in first: {key: {name, perms, offices, members, builtin, edited}}. Offices are only the
        club's current ones; a built-in role an admin hasn't changed follows today's offices."""
        rj, offs = self._picked() or {}, {o["title"].lower(): o["title"] for o in self.office_list()}
        table = rj.get("table") or {}
        out = {}
        for k in [*ROLES, *(k for k in table if k not in ROLES)]:
            name, perms = ROLES.get(k, ("", ()))
            t = table.get(k) or {}
            # before roles.json, only the hand-given built-ins have picks: the roster's old ticks (never is_officer/is_board)
            picked = rj.get(k) if k in rj else [m.id for m in self.members.values() if k in PICKED and getattr(m, k)]
            out[k] = {"name": t.get("name") or name, "perms": [p for p in PERMISSIONS if p in t.get("perms", perms)],
                      "offices": [offs[o.lower()] for o in t.get("offices", self._default_offices(k)) if o.lower() in offs],
                      "members": [self.canonical(i) for i in picked or [] if self.canonical(i) in self.members],
                      "builtin": k in ROLES, "edited": bool(t)}
        for p in PERMISSIONS:  # a permission newer than the saved table (no role has it): the built-ins that ship with it
            if not any(p in r["perms"] for r in out.values()):
                for k, (_n, perms) in ROLES.items():
                    if p in perms:
                        out[k]["perms"] = [q for q in PERMISSIONS if q in out[k]["perms"] or q == p]
        return out

    def save_roles(self, table: dict[str, dict]) -> None:
        """Site → Roles: {key: {name, perms, offices, members}} for every role (a built-in left out is refused; a role
        of the club's own left out is removed). Each built-in kept as it ships unless it differs. Used at once."""
        if missing := [ROLES[k][0] for k in ROLES if k not in table]:
            raise ValueError(f"The built-in roles can't be removed: {', '.join(missing)}.")
        offs = {o["title"].lower(): o["title"] for o in self.office_list()}
        names, out = set(), {}
        # a plugin's permissions stay on a role while the plugin is off or not loaded: no form shows them then
        stored = (self._picked() or {}).get("table") or {}
        hidden = {k: [x for x in (stored.get(k) or {}).get("perms", []) if "." in x
                      and (x not in PERMISSIONS or not self.plugin_on(x.split(".")[0]))] for k in table}
        for k, r in table.items():
            name = " ".join(str(r.get("name") or "").split())[:40]
            if not name:
                raise ValueError("Every role needs a name.")
            if name.lower() in names or name.lower() == "admin":
                raise ValueError(f"Two roles can't both be called {name}.")
            names.add(name.lower())
            perms = {p for p in r.get("perms", []) if p in PERMISSIONS} | ({"treasurer"} if "money" in r.get("perms", []) else set())
            perms |= {x for x in hidden[k] if x in PERMISSIONS}
            out[k] = {"name": name, "perms": [p for p in PERMISSIONS if p in perms] + [x for x in hidden[k] if x not in PERMISSIONS],
                      "offices": [offs[o.lower()] for o in r.get("offices", []) if o.lower() in offs],
                      "members": sorted({self.canonical(i) for i in r.get("members", []) if self.canonical(i) in self.members})}
        if lost := [PERMISSIONS[p] for p in PERMISSIONS if "." not in p and not any(p in r["perms"] for r in out.values())]:
            raise ValueError(f"No role would be allowed to {lost[0]}: give it to at least one role.")
        rj = {k: r["members"] for k, r in out.items()}
        rj["table"] = {k: {"name": r["name"], "perms": r["perms"], "offices": r["offices"]} for k, r in out.items()
                       if k not in ROLES or (r["name"], tuple(r["perms"]), r["offices"])
                       != (ROLES[k][0], tuple(p for p in PERMISSIONS if p in ROLES[k][1]), self._default_offices(k))}
        p = DATA / "roles.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rj, indent=1))
        tmp.replace(p)
        for m in self.members.values():
            for k in PICKED:
                setattr(m, k, m.id in rj[k])

    def holds_office(self, mid: str | None) -> bool:
        """Holds one of the club's offices (a whole title, any capitals): the board."""
        m, offs = self.members.get(mid or ""), {o["title"].lower() for o in self.office_list()}
        return bool(m) and any(t.strip().lower() in offs for t in (m.role or "").split(","))

    def roles(self, mid: str | None, table: dict | None = None) -> list[str]:
        """The member's roles (role_table keys), built-in first. Admin isn't one: it's admin mode."""
        m = self.members.get(mid or "")
        if not m:
            return []
        held = {t.strip().lower() for t in (m.role or "").split(",")}
        return [k for k, r in (table or self.role_table()).items() if m.id in r["members"] or held & {o.lower() for o in r["offices"]}]

    def role_names(self, mid: str | None) -> list[str]:
        t = self.role_table()
        return [t[k]["name"] for k in self.roles(mid)]

    def perms(self, mid: str | None) -> set[str]:
        """Everything the member's roles allow."""
        t = self.role_table()
        return {p for k in self.roles(mid, t) for p in t[k]["perms"]}

    def may(self, mid: str | None, perm: str) -> bool:
        """Whether a member's roles allow `perm` (PERMISSIONS). The web app adds admins in admin mode."""
        if perm not in PERMISSIONS:
            raise KeyError(perm)
        t = self.role_table()
        return any(perm in t[k]["perms"] for k in self.roles(mid, t))

    def can_lead(self, mid: str) -> bool:
        """Officers and minutes editors may lead a meeting and edit minutes."""
        return self.may(mid, "lead")

    can_edit = can_lead

    def can_edit_calendar(self, mid: str) -> bool:
        """Officers, minutes editors and calendar editors (say, the net manager) may change the club calendars."""
        return self.may(mid, "calendar")

    @property
    def app_admins(self) -> set[str]:
        """Admins added in the app (Admin → Security → Admins), besides club.yaml's `admins:` (self.admins)."""
        p = DATA / "admins.json"
        try:
            return set(json.loads(p.read_text())) if p.exists() else set()
        except (OSError, ValueError, TypeError):
            log.exception("%s is damaged; only club.yaml's admins count", p)
            return set()

    @property
    def all_admins(self) -> set[str]:
        """club.yaml's and the app's admins, less any suspended by the officers (recovery.py)."""
        return {self._admin_id(a) for a in self.admins | self.app_admins} - self.suspended

    def _admin_id(self, a: str) -> str:
        """A member id, or the member who now holds that callsign; never through a former callsign (an alias), which
        anyone could list for a stale entry and so become that admin."""
        return a if a in self.members else self._by_call.get(a, a)

    @property
    def suspended(self) -> set[str]:
        """Admins suspended by a recovery: their admin rights don't count, club.yaml's included; another admin lifts it."""
        p = DATA / "admins-suspended.json"
        try:
            return {self._admin_id(a) for a in json.loads(p.read_text())} if p.exists() else set()
        except (OSError, ValueError, TypeError):
            log.exception("%s is damaged; nobody is suspended", p)
            return set()

    def save_suspended(self, ids: set[str]) -> None:
        p = DATA / "admins-suspended.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(ids)))
        tmp.replace(p)

    @property
    def active_admins(self) -> set[str]:
        """Admins who can still sign in (an Inactive or Silent Key admin can't, so they don't count)."""
        return {a for a in self.all_admins if a in self.members and self.members[a].status.lower() == "active"}

    def save_app_admins(self, ids: set[str]) -> None:
        p = DATA / "admins.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(ids)))
        tmp.replace(p)

    def admin_listed(self, who: str) -> bool:
        """Whether an id or callsign is on an admin list (club.yaml, the app's, suspended), whoever holds it now: handing
        it to a new member would make them an admin."""
        return who.strip().lower() in {str(a).lower() for a in self.admins | self.app_admins | self.suspended}

    def is_admin(self, mid: str | None) -> bool:
        """Listed under admins: in club.yaml or added in the app. Admin rights apply only while admin mode is on."""
        return bool(mid) and mid in self.all_admins

    def can_see(self, viewer: str | None, mid: str, fld: str, elevated: bool = False) -> bool:
        """Contact details: officers, admins in admin mode, and the member always; others only what was shared."""
        v, m = self.members.get(viewer or ""), self.members.get(mid)
        return bool(m and v and (self.may(v.id, "contacts") or elevated or v.id == m.id or fld in m.share))

    def keeps_roster(self, mid: str | None) -> bool:
        return self.may(mid, "members")

    @property
    def secretaries(self) -> list[str]:
        """Who gets the roster emails (the "roster_emails" permission; the Secretary's office by default)."""
        return [mid for mid in self.members if self.may(mid, "roster_emails")]

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

    def quorum_ids(self) -> tuple[list[str], list[str]]:
        """Who counts toward a quorum: the Active members, and those of them who are officers (guests never count)."""
        active = [m for m in self.members.values() if m.status.lower() == "active"]
        return [m.id for m in active], [m.id for m in active if m.officer]

    def quorum_needed(self) -> int | None:
        """Members needed: the percent of the Active members, rounded up (30% of 41 is 13); None if no percent is set."""
        pct = (self.quorum or {}).get("percent")
        return None if pct is None else -(-pct * len(self.quorum_ids()[0]) // 100)

    def quorum_record(self, present: list[str]) -> dict | None:
        """What the minutes keep about a quorum for these present members: {members, present, needed, officers,
        officers_needed, met} (a number only for a part of the rule that's set), or None when the club has no rule."""
        if not self.quorum:
            return None
        active, officers = self.quorum_ids()
        here = [x for x in dict.fromkeys(present) if x in active]
        need, off = self.quorum_needed(), self.quorum["officers"]
        rec = {"members": len(active), "present": len(here), "needed": need,
               "officers": sum(x in officers for x in here), "officers_needed": off}
        rec["met"] = (need is None or rec["present"] >= need) and (off is None or rec["officers"] >= off)
        return {k: v for k, v in rec.items() if v is not None}

    def shown_name(self, mid: str) -> str:
        """A member's name wherever the app shows one in brief (report tags, comments, chow, search, Old Business):
        short ("Jane D", the default) or full (`club: names: full`; Site → Club). `short` stays the short form, for
        matching names typed in text."""
        return self.name(mid) if self.info.get("names") == "full" else self.short(mid)

    def known_name(self, mid: str) -> str | None:
        """The shown name of a member, or None when the id is no member's (a mention in text). "" for one the club never
        records (never_record): a mention shows neutral text, never the name."""
        if self.canonical(mid) not in self.members:
            return None
        return "" if self.is_redacted(self.shown_name(mid)) or self.is_redacted(self.name(mid)) else self.shown_name(mid)

    def mentionable(self) -> list[dict]:
        """Who the @ picker offers: the Active members, as the page's JSON (static/format.js)."""
        return [{"id": m.id, "name": m.name, "short": self.shown_name(m.id), "callsign": m.callsign if self.ham else None}
                for m in self.members.values() if m.status.lower() == "active" and self.known_name(m.id)]

    def unnamed(self, text: str, mid: str | None) -> str:
        """A report without the member's name at its start ("Jim F - Built…", "Jim built…"): the minutes show the name
        once, as a pill before the report (Jim, 2026-11-20). The saved text keeps it, so the file and emails still say who."""
        m = self.members.get(self.canonical(mid)) if mid else None
        return unname(text, {m.name, self.short(mid), m.first}) if m else text

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
            pats.append((rf"{m.first}(?:'s)?\s+{m.last[0]}\b\.?", m.id))  # "Jane D", "Jane's D"
            for a in m.aliases:
                pats.append((rf"{a}\b", m.id))
        for first, ids in firsts.items():
            if len(ids) == 1:
                pats.append((rf"{first}(?:'?s)?\b", ids[0]))  # "Amy", "Amys", "Amy's"
        return [(re.compile(rf"^\s*{p}", re.IGNORECASE), mid) for p, mid in pats]

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
