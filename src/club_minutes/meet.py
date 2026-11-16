"""Jitsi Meet sign-in (an optional module: `modules: {jitsi: true}` and a `jitsi:` block in club.yaml).

Members sign in here (passkey or emailed link) and /meet/<room> hands the club's own Jitsi server a short-lived token
(a JWT, HS256, signed with the secret it shares: CLUB_MINUTES_JITSI_SECRET) saying who they are and whether they host
(the "meet_host" permission). The Jitsi server checks it with Prosody's token authentication (jitsi-meet-tokens);
token_affiliation reads the host from `context.user.affiliation`; `lobby_bypass` lets every signed-in member past the
lobby (token_lobby_bypass), hosts included, so only guests knock. The token only opens the room: it expires in minutes.

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
LEAVES = {"stay": "Nothing: the meeting goes on", "guests": "The guests are dropped", "end": "The meeting ends for everyone"}
OPEN_HOURS = 24  # an ad-hoc room's opener stays its host this long
OPEN_MAX = (5, 200)  # rooms of their own a day: per member, and in all
_opening = threading.Lock()


def settings(club: Club) -> dict | None:
    """{domain, app_id, room} when Jitsi is on and set up (club.yaml's jitsi: block and the secret); else None."""
    j = club.yaml.get("jitsi") or {}
    if not club.has("jitsi") or not j.get("domain") or not j.get("app_id") or len(secret()) < 32:  # a short secret can be guessed from one token
        return None
    return {"domain": str(j["domain"]).strip().strip("/"), "app_id": str(j["app_id"]), "room": str(j.get("room") or "club")}


def secret() -> str:
    return os.environ.get("CLUB_MINUTES_JITSI_SECRET", "")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(signing_input: bytes, key: bytes) -> str:
    return _b64(hmac.new(key, signing_input, hashlib.sha256).digest())


def token(club: Club, mid: str, room: str, host: bool, now: float | None = None, room_rules: dict | None = None) -> str:
    s, now = settings(club), int(now or time.time())
    payload = {"aud": "jitsi", "iss": s["app_id"], "sub": s["domain"], "room": room, "iat": now, "nbf": now - 10,
               "exp": now + MINUTES * 60, "moderator": host,
               "context": {"user": {"id": mid, "name": club.name(mid), "affiliation": "owner" if host else "member",
                                    "lobby_bypass": True},
                           "room": {"club": rules(room_rules)}}}
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


def _setting(club: Club, key: str):
    """Site → Video's setting, else club.yaml's jitsi: one."""
    site = club.site_settings.get("jitsi") or {}
    return site[key] if key in site else (club.yaml.get("jitsi") or {}).get(key)


def rooms(club: Club) -> list[dict]:
    """The named rooms: [{slug, name, rules...}]. None set: club.yaml's one room (jitsi: room), with the defaults."""
    out, seen = [], set()
    for r in _setting(club, "rooms") or []:
        slug = str(r.get("slug") or "").lower()  # Jitsi's room names are lower case: NET is net
        if ROOM.match(slug) and slug not in seen:
            seen.add(slug)
            out.append({"slug": slug, "name": str(r.get("name") or slug)[:60], "p2p": r.get("p2p", True) is not False} | rules(r))
    if not out and (s := settings(club)):
        out = [{"slug": s["room"], "name": "Club meeting", "p2p": True} | rules(None)]
    return out


def adhoc(club: Club) -> dict | None:
    """The rules for rooms members open themselves, or None while they may not."""
    a = _setting(club, "adhoc") or {}
    return rules(a) | {"p2p": a.get("p2p", True) is not False} if a.get("on") else None


def p2p_off(room: dict) -> str:
    """What the address to Jitsi ends with when a room keeps two-person calls on the server: members' browsers then
    refuse peer-to-peer, so a call with a guest goes through the server, where the rules for guests hold."""
    return "" if room.get("p2p", True) else "#config.p2p.enabled=false"


def slug(name: str) -> str:
    """A room's address from its name: "Net night" → "net-night"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]


def _openers() -> dict:
    """{"room:<slug>": {by, at}} for the last day. Keys never equal a member id, so a callsign change (callsign.py)
    renames only the openers. A damaged file reads as empty: it only says who hosts for a day."""
    try:
        d = json.loads((clubmod.DATA / "meet-rooms.json").read_text())
    except (OSError, ValueError):
        d = {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("at", 0) > time.time() - OPEN_HOURS * 3600}


def opener(room: str) -> str | None:
    """Who opened this ad-hoc room, in the last day."""
    return (_openers().get(f"room:{room}") or {}).get("by")


def open_room(room: str, mid: str) -> None:
    """Note a member opening an ad-hoc room, unless someone else did in the last day (then they just join it).
    ValueError past OPEN_MAX."""
    with _opening:
        d = _openers()
        if f"room:{room}" not in d and (sum(v.get("by") == mid for v in d.values()) >= OPEN_MAX[0] or len(d) >= OPEN_MAX[1]):
            raise ValueError("That's as many rooms of your own as one day allows: use one you opened.")
        if d.get(f"room:{room}", {}).get("by") in (None, mid):
            d[f"room:{room}"] = {"by": mid, "at": time.time()}
            p = clubmod.DATA / "meet-rooms.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1))
            tmp.replace(p)
