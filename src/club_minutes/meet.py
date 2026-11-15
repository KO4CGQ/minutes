"""Jitsi Meet sign-in (an optional module: `modules: {jitsi: true}` and a `jitsi:` block in club.yaml).

Members sign in here (passkey or emailed link) and /meet/<room> hands the club's own Jitsi server a short-lived token
(a JWT, HS256, signed with the secret it shares: CLUB_MINUTES_JITSI_SECRET) saying who they are and whether they host
(the "meet_host" permission). The Jitsi server checks it with Prosody's token authentication (jitsi-meet-tokens);
token_affiliation reads the host from `context.user.affiliation`. The token only opens the room: it expires in minutes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time

from .club import Club

MINUTES = 5
ROOM = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def settings(club: Club) -> dict | None:
    """{domain, app_id, room} when Jitsi is on and set up (club.yaml's jitsi: block and the secret); else None."""
    j = club.yaml.get("jitsi") or {}
    if not club.has("jitsi") or not j.get("domain") or not j.get("app_id") or not secret():
        return None
    return {"domain": str(j["domain"]).strip().strip("/"), "app_id": str(j["app_id"]), "room": str(j.get("room") or "club")}


def secret() -> str:
    return os.environ.get("CLUB_MINUTES_JITSI_SECRET", "")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(signing_input: bytes, key: bytes) -> str:
    return _b64(hmac.new(key, signing_input, hashlib.sha256).digest())


def token(club: Club, mid: str, room: str, host: bool, now: float | None = None) -> str:
    s, now = settings(club), int(now or time.time())
    payload = {"aud": "jitsi", "iss": s["app_id"], "sub": s["domain"], "room": room, "iat": now, "nbf": now - 10,
               "exp": now + MINUTES * 60, "moderator": host,
               "context": {"user": {"id": mid, "name": club.name(mid), "affiliation": "owner" if host else "member"}}}
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{head}.{body}.{sign(f'{head}.{body}'.encode(), secret().encode())}"
