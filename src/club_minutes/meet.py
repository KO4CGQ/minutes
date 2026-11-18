"""Jitsi Meet sign-in (an optional module: `modules: {jitsi: true}` and a `jitsi:` block in club.yaml).

Members sign in here (passkey or emailed link) and /meet/<room> hands the club's own Jitsi server a short-lived token
(a JWT, HS256, signed with the secret it shares: CLUB_MINUTES_JITSI_SECRET) saying who they are and whether they host
(the "meet_host" permission). The Jitsi server checks it with Prosody's token authentication (jitsi-meet-tokens);
token_affiliation reads the host from `context.user.affiliation`; `lobby_bypass` lets a signed-in member past the
lobby (token_lobby_bypass), hosts included, so only guests knock, and members a room doesn't name the roles of. The token only opens the room: it expires in minutes.

Rooms: the named rooms (Site → Video, or club.yaml's jitsi: rooms) and, if allowed (jitsi: adhoc), rooms members open
themselves, whose opener hosts too. Each room's rules for guests ride in every member's token as context.room.club, for
the Jitsi server's mod_club_rules: may guests knock, unmute, start their camera, share their screen, chat, and what
happens when the last host leaves.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time

from . import club as clubmod
from .club import Club

MINUTES = 5
ROOM = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
GUEST = ("mic", "camera", "screen", "chat")  # what a guest may do, each True (freely) or False (a host must allow it)
OTHERS = {"knock": "Knock in the lobby", "no": "Can't join"}  # members a room doesn't let straight in
LEAVES = {"stay": "Nothing: the meeting goes on", "guests": "The guests are dropped", "end": "The meeting ends for everyone"}
OPEN_HOURS = 24  # an ad-hoc room's opener stays its host until this long passes with no one joining it here
OPEN_MAX = (5, 200)  # rooms of their own a day: per member, and in all
_opening = threading.Lock()


def settings(club: Club) -> dict | None:
    """{domain, app_id, room} when Jitsi is on and set up (Site → Features or club.yaml's jitsi: block, and the
    secret in the server's env); else None."""
    domain, app_id = (str(_setting(club, k) or "").strip().strip("/") for k in ("domain", "app_id"))
    if not club.has("jitsi") or not domain or not app_id or len(secret()) < 32:  # a short secret can be guessed from one token
        return None
    return {"domain": domain, "app_id": app_id, "room": str(_setting(club, "room") or "club")}


def secret() -> str:
    """The secret shared with the Jitsi server: the server's env (CLUB_MINUTES_JITSI_SECRET) if set, else the one made
    on Admin → Site → Features (the data folder, readable by the app only)."""
    return os.environ.get("CLUB_MINUTES_JITSI_SECRET", "").strip() or _secret_file()


def _secret_file() -> str:
    try:
        return (clubmod.DATA / "jitsi-secret").read_text().strip()
    except OSError:
        return ""


def secret_source() -> tuple[str, float] | None:
    """Where the secret comes from: ("env", 0), ("app", when it was made), or None (not set)."""
    if os.environ.get("CLUB_MINUTES_JITSI_SECRET", "").strip():
        return "env", 0
    try:
        return ("app", (clubmod.DATA / "jitsi-secret").stat().st_mtime) if _secret_file() else None
    except OSError:
        return None


def new_secret() -> str:
    """Make a new secret (64 hex characters), keep it for the app only, and return it to show once."""
    import secrets
    value, p = secrets.token_hex(32), clubmod.DATA / "jitsi-secret"
    with _opening:  # one write at a time (they share the .tmp); two at once: the later one is the one kept
        tmp = p.with_suffix(".tmp")
        tmp.touch(mode=0o600)
        tmp.chmod(0o600)
        tmp.write_text(value + "\n")
        tmp.replace(p)
    return value


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(signing_input: bytes, key: bytes) -> str:
    return _b64(hmac.new(key, signing_input, hashlib.sha256).digest())


def lets_in(club: Club, mid: str, room: dict) -> bool:
    """Whether a member comes straight into this room: every member, unless it names roles."""
    return not room.get("roles") or bool(set(room["roles"]) & set(club.roles(mid)))


def _token_rules(r: dict) -> dict:
    """Members only: no guest limits, or the server would hold back members' mics and cameras for no one (the ticks
    stay stored, for when guests are let in again)."""
    return r if r["guests"] else r | {f"guest_{k}": True for k in GUEST}


def token(club: Club, mid: str, room: str, host: bool, now: float | None = None, room_rules: dict | None = None,
          straight_in: bool = True) -> str:
    s, now = settings(club), int(now or time.time())
    payload = {"aud": "jitsi", "iss": s["app_id"], "sub": s["domain"], "room": room, "iat": now, "nbf": now - 10,
               "exp": now + MINUTES * 60, "moderator": host,
               "context": {"user": {"id": mid, "name": club.name(mid), "affiliation": "owner" if host else "member",
                                    "lobby_bypass": straight_in},
                           "room": {"club": _token_rules(rules(room_rules))}}}
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{head}.{body}.{sign(f'{head}.{body}'.encode(), secret().encode())}"


def rules(d: dict | None) -> dict:
    """A room's rules for guests, from a stored room (or the ad-hoc settings): anything missing or odd is the default
    (guests welcome, free to do everything, the meeting goes on). Exactly what the token carries."""
    d = d or {}
    out = {"guests": d.get("guests", True) is not False}
    out |= {f"guest_{k}": d.get(f"guest_{k}", True) is not False for k in GUEST}
    out["last_host_leaves"] = d.get("last_host_leaves") if d.get("last_host_leaves") in LEAVES else "stay"
    return out


DOMAIN = re.compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")  # the Jitsi server: a host name, perhaps with a port
APP_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _setting(club: Club, key: str):
    """Site → Video's (or → Features') setting, else club.yaml's jitsi: one."""
    site = club.site_settings.get("jitsi") or {}
    return site[key] if key in site else (club.yaml.get("jitsi") or {}).get(key)


def rooms(club: Club) -> list[dict]:
    """The named rooms: [{slug, name, rules...}]. None set: club.yaml's one room (jitsi: room), with the defaults."""
    out, seen = [], set()
    for r in _setting(club, "rooms") or []:
        slug = str(r.get("slug") or "").lower()  # Jitsi's room names are lower case: NET is net
        if ROOM.match(slug) and slug not in seen:
            seen.add(slug)
            out.append({"slug": slug, "name": str(r.get("name") or slug)[:60], "about": " ".join(str(r.get("about") or "").split())[:300], "p2p": r.get("p2p", True) is not False,
                        # roles that come straight in (none: every member); a role removed later matches nobody
                        "roles": [str(x) for x in ([r["roles"]] if isinstance(r.get("roles"), str) else r.get("roles") or [])], "others": r.get("others") if r.get("others") in OTHERS else "knock"}
                       | rules(r))
    if not out and (s := settings(club)):
        out = [{"slug": s["room"], "name": "Club meeting", "about": "", "p2p": True, "roles": [], "others": "knock"} | rules(None)]
    return out


def adhoc(club: Club) -> dict | None:
    """The rules for rooms members open themselves, or None while they may not."""
    a = _setting(club, "adhoc") or {}
    return rules(a) | {"p2p": a.get("p2p", True) is not False} if a.get("on") else None


def members_only(club: Club, room: str) -> bool:
    """Whether this room lets no guests in (a guest reaching /meet/<room> is told so instead of sent on to Jitsi)."""
    if not settings(club) or not ROOM.match(room):
        return False
    r = next((r for r in rooms(club) if r["slug"] == room), None) or adhoc(club)
    return bool(r) and not r["guests"]


def guest_room(club: Club, room: str) -> dict | None:
    """The room a signed-out visitor may join as a guest, for /meet/<room>'s "Sign in or join as a guest" page: one of
    the club's rooms that lets guests in, or a member's own room while it's current (and own rooms let guests in)."""
    if not settings(club) or not ROOM.match(room):
        return None
    named = next((r for r in rooms(club) if r["slug"] == room), None)
    if named:
        return named if named["guests"] else None
    own, a = _openers().get(f"room:{room}"), adhoc(club)
    return {"slug": room, "name": own.get("name") or room, "about": ""} if own and a and a["guests"] else None


def p2p_off(room: dict) -> str:
    """What the address to Jitsi ends with when a room keeps two-person calls on the server: members' browsers then
    refuse peer-to-peer, so a call with a guest goes through the server, where the rules for guests hold."""
    return "" if room.get("p2p", True) else "#config.p2p.enabled=false"


def slug(name: str) -> str:
    """A room's address from its name: "Net night" → "net-night"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]


def _openers() -> dict:
    """{"room:<slug>": {by, at}}: at is the last join through the app; older than a day is gone. Keys never equal a member id, so a callsign change (callsign.py)
    renames only the openers. A damaged file reads as empty: it only says who hosts for a day."""
    try:
        d = json.loads((clubmod.DATA / "meet-rooms.json").read_text())
    except (OSError, ValueError):
        d = {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("at", 0) > time.time() - OPEN_HOURS * 3600}


def opener(room: str) -> str | None:
    """Who opened this ad-hoc room, while it's current."""
    return (_openers().get(f"room:{room}") or {}).get("by")


def mine(mid: str) -> list[dict]:
    """The ad-hoc rooms this member opened that are still current: [{slug, name}], for their cards on /meet."""
    return [{"slug": k[5:], "name": v.get("name") or k[5:]} for k, v in _openers().items() if v.get("by") == mid]


def open_room(room: str, mid: str, name: str = "") -> None:
    """Note a member opening an ad-hoc room (and the name they typed, for its card), unless someone else did in the
    last day (then they just join it). ValueError past OPEN_MAX."""
    with _opening:
        d = _openers()
        if f"room:{room}" not in d and (sum(v.get("by") == mid for v in d.values()) >= OPEN_MAX[0] or len(d) >= OPEN_MAX[1]):
            raise ValueError("That's as many rooms of your own as one day allows: use one you opened.")
        if d.get(f"room:{room}", {}).get("by") in (None, mid):
            d[f"room:{room}"] = {"by": mid, "at": time.time(), "name": name[:60] or d.get(f"room:{room}", {}).get("name", "")}
            _save(d)


def touch(room: str) -> None:
    """A join through the app keeps an ad-hoc room's name and its opener current for another day."""
    with _opening:
        d = _openers()
        if f"room:{room}" in d:
            d[f"room:{room}"]["at"] = time.time()
            _save(d)


def _save(d: dict) -> None:
    p = clubmod.DATA / "meet-rooms.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(p)
