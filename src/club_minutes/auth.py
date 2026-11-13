"""Member login: passkeys (WebAuthn), with an emailed one-time link as the fallback.

Everything lives in a small SQLite file next to the archive data. Only hashes of
session and link tokens are stored, so a copy of the database can't be used to log in.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import secrets
import smtplib
import sqlite3
import time
from email.message import EmailMessage
from pathlib import Path

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import branding
from .club import DATA, PREVIEW, STAGE, Club, one_line, valid_email

ACTOR = contextvars.ContextVar("actor", default=None)  # the signed-in person's email, for stage mode (set per request)

log = logging.getLogger("club_minutes.auth")

# Passkey providers, by the AAGUID their authenticator reports (from the community-maintained
# passkey-authenticator-aaguids list). Used to name a new passkey; members can rename it.
PROVIDERS = {
    "d548826e-79b4-db40-a3d8-11116f7e8349": "Bitwarden",
    "bada5566-a7aa-401f-bd96-45619a55120d": "1Password",
    "fbfc3007-154e-4ecc-8c0b-6e020557d7bd": "iCloud Keychain",
    "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4": "Google Password Manager",
    "adce0002-35bc-c60a-648b-0b25f1f05503": "Chrome on Mac",
    "08987058-cadc-4b81-b6e1-30de50dcbe96": "Windows Hello",
    "9ddd1817-af5a-4672-a2b9-3e3dd95000a9": "Windows Hello",
    "6028b017-b1d4-4c02-b4b3-afcdafc96bb2": "Windows Hello",
    "531126d6-e717-415c-9320-3d9aa6981239": "Dashlane",
    "50726f74-6f6e-5061-7373-50726f746f6e": "Proton Pass",
    "fdb141b2-5d84-443e-8a35-4698c205a502": "KeePassXC",
    "53414d53-554e-4700-0000-000000000000": "Samsung Pass",
}

LINK_MINUTES = 15
CHALLENGE_SECONDS = 300
EMAILS_PER_HOUR = 5

SCHEMA = """
create table if not exists sessions (token_hash text primary key, member text not null,
  created integer not null, expires integer not null, agent text);
create table if not exists links (token_hash text primary key, member text not null,
  expires integer not null, next text, used integer default 0, created integer not null);
create table if not exists passkeys (id text primary key, member text not null, public_key blob not null,
  sign_count integer not null, transports text, label text, created integer not null, last_used integer,
  aaguid text);
create table if not exists email_changes (token_hash text primary key, member text not null,
  email text not null, expires integer not null, created integer not null);
create table if not exists challenges (id text primary key, challenge blob not null, member text,
  kind text not null, expires integer not null);
create table if not exists signins (at integer not null, member text not null, method text, ip text, agent text);
create table if not exists events (at integer not null, ip text, kind text not null, member text, detail text, agent text);
create table if not exists bans (ip text primary key, at integer not null, until integer, reason text, by text);
"""
BAN_AFTER = 10          # failed sign-in attempts from one internet address in an hour...
BAN_HOURS = 24          # ...ban it for this long (admins can lift it on the Security page)
EVENT_NAMES = {"unknown_email": "Sign-in link asked for an address not on the roster",
               "link_limit": "Sign-in link limit reached", "bad_link": "Used, expired or made-up sign-in link",
               "passkey_failed": "Passkey sign-in failed"}


def _public(ip: str) -> bool:
    """An internet address (not the LAN, the tailnet or this machine): only these are banned automatically."""
    import ipaddress
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, club: Club, path: Path | None = None):
        self.club = club
        cfg = club.auth
        self.rp_id: str | None = cfg.get("rp_id")
        self._rp_name: str | None = cfg.get("rp_name")  # club.yaml's, else the app's name (Site)
        self.origins: list[str] = cfg.get("origins") or ([f"https://{self.rp_id}"] if self.rp_id else [])
        self.session_days: int = int(cfg.get("session_days", 180))
        self.base_url: str | None = cfg.get("base_url") or (self.origins[0] if self.origins else None)
        path = path or DATA / "auth.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("pragma table_info(passkeys)")}
        if "aaguid" not in cols:  # added after the first release
            self.db.execute("alter table passkeys add column aaguid text")
        session_cols = {r["name"] for r in self.db.execute("pragma table_info(sessions)")}
        if "admin_until" not in session_cols:
            self.db.execute("alter table sessions add column admin_until integer")
        if "view_as" not in session_cols:
            self.db.execute("alter table sessions add column view_as text")
        for col in ("ip", "method"):
            if col not in session_cols:
                self.db.execute(f"alter table sessions add column {col} text")
        if "agent" not in {r["name"] for r in self.db.execute("pragma table_info(events)")}:  # added in 1.0.0
            self.db.execute("alter table events add column agent text")
        os.chmod(path, 0o600)

    # ---------------- sessions ----------------
    def new_session(self, member: str, agent: str = "", ip: str | None = None, method: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        self.db.execute("insert into sessions (token_hash, member, created, expires, agent, ip, method) values (?,?,?,?,?,?,?)",
                        (_hash(token), member, now, now + self.session_days * 86400, agent[:200], ip, method))
        if method:  # a real sign-in (tests and tools make sessions without one)
            self.db.execute("insert into signins values (?,?,?,?,?)", (now, member, method, ip, agent[:200]))
        return token

    # ---------------- security log and bans (the admin Security page) ----------------
    def event(self, kind: str, ip: str | None, member: str | None = None, detail: str = "", agent: str = "") -> None:
        """Record a failed or suspicious sign-in attempt (and the browser's user agent); ban an internet address
        after BAN_AFTER in an hour."""
        now = int(time.time())
        self.db.execute("insert into events (at, ip, kind, member, detail, agent) values (?,?,?,?,?,?)",
                        (now, ip, kind, member, detail[:200], (agent or "")[:300]))
        if ip and _public(ip) and not self.banned(ip):
            n = self.db.execute("select count(*) from events where ip=? and at>?", (ip, now - 3600)).fetchone()[0]
            if n >= BAN_AFTER:
                self.ban(ip, BAN_HOURS, f"{n} failed sign-in attempts in an hour", "automatic")
                log.warning("banned %s after %d failed sign-in attempts", ip, n)

    def banned(self, ip: str | None) -> bool:
        if not ip:
            return False
        row = self.db.execute("select until from bans where ip=?", (ip,)).fetchone()
        return bool(row and (row["until"] is None or row["until"] > time.time()))

    def ban(self, ip: str, hours: int | None, reason: str, by: str) -> None:
        now = int(time.time())
        self.db.execute("insert or replace into bans values (?,?,?,?,?)",
                        (ip, now, now + hours * 3600 if hours else None, reason[:200], by))

    def unban(self, ip: str) -> None:
        self.db.execute("delete from bans where ip=?", (ip,))

    def bans(self) -> list[sqlite3.Row]:
        return self.db.execute("select * from bans where until is null or until>? order by at desc", (time.time(),)).fetchall()

    def signins(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.db.execute("select * from signins order by at desc limit ?", (limit,)).fetchall()

    def events(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.db.execute("select * from events order by at desc limit ?", (limit,)).fetchall()

    def by_member(self) -> dict[str, dict]:
        """Per member: last sign-in, passkeys (how many, last used) and devices signed in now, for the Security page."""
        out: dict[str, dict] = {}
        for sql, cols in (("select member, max(at) from signins group by member", ("last",)),
                          ("select member, count(*), max(last_used) from passkeys group by member", ("passkeys", "pk_used")),
                          ("select member, count(*) from sessions where expires>? group by member", ("devices",))):
            for r in self.db.execute(sql, (time.time(),) if "?" in sql else ()):
                out.setdefault(r[0], {}).update(zip(cols, tuple(r)[1:]))
        return out

    def all_sessions(self) -> list[sqlite3.Row]:
        return self.db.execute("select rowid, member, created, expires, agent, ip, method from sessions where expires>? "
                               "order by member, created desc", (time.time(),)).fetchall()

    def end_session_id(self, rowid: int) -> str | None:
        row = self.db.execute("select member from sessions where rowid=?", (rowid,)).fetchone()
        self.db.execute("delete from sessions where rowid=?", (rowid,))
        return row["member"] if row else None

    def member_for(self, token: str | None) -> str | None:
        if not token:
            return None
        row = self.db.execute("select member, expires from sessions where token_hash=?", (_hash(token),)).fetchone()
        if not row or row["expires"] < time.time():
            return None
        member = self.club.canonical(row["member"])
        return member if self.is_active(member) else None  # off the roster: access ends at the next sync

    def is_active(self, member: str) -> bool:
        m = self.club.members.get(self.club.canonical(member))
        return bool(m and m.status.lower() not in ("inactive", "silent key"))

    # ---------------- admin mode (per sign-in, time-limited) ----------------
    def admin_until(self, token: str | None) -> int | None:
        """When this sign-in's admin mode ends, or None if it's off."""
        if not token:
            return None
        row = self.db.execute("select member, admin_until from sessions where token_hash=?", (_hash(token),)).fetchone()
        if not row or not row["admin_until"] or row["admin_until"] < time.time() or not self.club.is_admin(row["member"]):
            return None
        return row["admin_until"]

    def set_admin(self, token: str, minutes: int) -> int | None:
        """Turn admin mode on for `minutes` (0 turns it off). Only for members listed as admins."""
        row = self.db.execute("select member from sessions where token_hash=?", (_hash(token),)).fetchone()
        if not row or not self.club.is_admin(row["member"]):
            return None
        until = int(time.time()) + minutes * 60 if minutes else None
        self.db.execute("update sessions set admin_until=?, view_as=null where token_hash=?", (until, _hash(token)))
        return until

    def view_as(self, token: str | None) -> str | None:
        """The member an admin is viewing the app as (read-only), only while admin mode is on."""
        if not self.admin_until(token):
            return None
        row = self.db.execute("select view_as from sessions where token_hash=?", (_hash(token),)).fetchone()
        return row["view_as"] if row else None

    def set_view_as(self, token: str, member: str | None) -> None:
        self.db.execute("update sessions set view_as=? where token_hash=?", (member, _hash(token)))

    def end_session(self, token: str | None) -> None:
        if token:
            self.db.execute("delete from sessions where token_hash=?", (_hash(token),))

    def sessions(self, member: str) -> list[sqlite3.Row]:
        return self.db.execute("select created, expires, agent from sessions where member=? and expires>? "
                               "order by created desc", (member, time.time())).fetchall()

    def end_all_sessions(self, member: str) -> None:
        self.db.execute("delete from sessions where member=?", (member,))

    # ---------------- email links ----------------
    def member_by_email(self, email: str) -> str | None:
        e = email.strip().lower()
        for m in self.club.members.values():
            if m.email and m.email.strip().lower() == e and self.is_active(m.id):
                return m.id
        return None

    def send_link(self, email: str, next_url: str = "/", base_url: str | None = None, ip: str | None = None, agent: str = "") -> None:
        """Email a one-time login link. Silent if the address isn't on the roster (no account probing)."""
        member = self.member_by_email(email)
        if not member:
            log.info("login link requested for an address not on the roster")
            self.event("unknown_email", ip, agent=agent)  # the address itself isn't kept
            return
        now = int(time.time())
        recent = self.db.execute("select count(*) from links where member=? and created>?",
                                 (member, now - 3600)).fetchone()[0]
        if recent >= EMAILS_PER_HOUR:
            log.warning("login link rate limit hit for %s", member)
            self.event("link_limit", ip, member, agent=agent)
            return
        url = self.create_link(member, next_url, base_url)
        send_email(self.club.members[member].email, f"Your {branding.name(self.club)} sign-in link", only_to=self.club.email_only_to(), to_self=True,
                   body=f"Hi {self.club.members[member].first},\n\n"
                   f"Use this link to sign in to {branding.name(self.club)}. It works once, for {LINK_MINUTES} minutes:\n\n"
                   f"{url}\n\nIf you didn't ask for this, you can ignore this email.\n\n73,\n{branding.name(self.club)}")

    def create_link(self, member: str, next_url: str = "/", base_url: str | None = None) -> str:
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        self.db.execute("insert into links values (?,?,?,?,0,?)",
                        (_hash(token), member, now + LINK_MINUTES * 60, _safe_next(next_url), now))
        return f"{(base_url or self.base_url or '').rstrip('/')}/login/link?t={token}"

    def use_link(self, token: str) -> tuple[str, str] | None:
        """Returns (member, next_url) and burns the link, or None if invalid/expired/used."""
        row = self.db.execute("select member, expires, next, used from links where token_hash=?",
                              (_hash(token),)).fetchone()
        if not row or row["used"] or row["expires"] < time.time():
            return None
        self.db.execute("update links set used=1 where token_hash=?", (_hash(token),))
        return row["member"], row["next"] or "/"

    # ---------------- email changes ----------------
    def start_email_change(self, member: str, email: str, base_url: str | None = None) -> str | None:
        """Email a confirmation link to the new address. Returns an error message, or None if sent."""
        email = email.strip()
        if not valid_email(email):
            return "That doesn't look like an email address."
        other = self.member_by_email(email)
        if other and other != member:
            return "Another member already uses that address."
        now = int(time.time())
        if self.db.execute("select count(*) from email_changes where member=? and created>?",
                           (member, now - 3600)).fetchone()[0] >= EMAILS_PER_HOUR:
            return "Too many tries; please wait an hour."
        token = secrets.token_urlsafe(32)
        self.db.execute("insert into email_changes values (?,?,?,?,?)",
                        (_hash(token), member, email, now + LINK_MINUTES * 60, now))
        url = f"{(base_url or self.base_url or '').rstrip('/')}/profile/email?t={token}"
        send_email(email, f"Confirm your new {branding.name(self.club)} email", only_to=self.club.email_only_to(), to_self=True,
                   body=f"Hi {self.club.members[member].first},\n\nTo use this address for {branding.name(self.club)} and the club "
                   f"roster, open this link within {LINK_MINUTES} minutes:\n\n{url}\n\n"
                   f"If you didn't ask for this, ignore this email and nothing changes.\n\n73,\n{branding.name(self.club)}")
        return None

    def confirm_email_change(self, token: str) -> tuple[str, str] | None:
        """(member, new email) for a valid link, which is then used up."""
        row = self.db.execute("select member, email, expires from email_changes where token_hash=?",
                              (_hash(token),)).fetchone()
        self.db.execute("delete from email_changes where token_hash=?", (_hash(token),))
        if not row or row["expires"] < time.time():
            return None
        return row["member"], row["email"]

    # ---------------- passkeys ----------------
    def _challenge(self, kind: str, challenge: bytes, member: str | None = None) -> str:
        cid = secrets.token_urlsafe(16)
        self.db.execute("delete from challenges where expires<?", (time.time(),))
        self.db.execute("insert into challenges values (?,?,?,?,?)",
                        (cid, challenge, member, kind, int(time.time()) + CHALLENGE_SECONDS))
        return cid

    def _take_challenge(self, cid: str | None, kind: str) -> sqlite3.Row | None:
        if not cid:
            return None
        row = self.db.execute("select * from challenges where id=? and kind=? and expires>?",
                              (cid, kind, time.time())).fetchone()
        self.db.execute("delete from challenges where id=?", (cid,))
        return row

    def registration_options(self, member: str) -> tuple[str, str]:
        m = self.club.members[member]
        existing = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["id"]))
                    for r in self.db.execute("select id from passkeys where member=?", (member,))]
        opts = generate_registration_options(
            rp_id=self.rp_id, rp_name=self._rp_name or branding.name(self.club),
            user_id=member.encode(), user_name=m.callsign or m.email or m.name, user_display_name=m.name,
            exclude_credentials=existing,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,  # so sign-in needs no username
                user_verification=UserVerificationRequirement.PREFERRED))
        return self._challenge("register", opts.challenge, member), options_to_json(opts)

    def register(self, member: str, cid: str | None, credential: dict, label: str = "") -> None:
        ch = self._take_challenge(cid, "register")
        if not ch or ch["member"] != member:
            raise ValueError("Passkey setup expired; please try again.")
        v = verify_registration_response(credential=credential, expected_challenge=ch["challenge"],
                                         expected_rp_id=self.rp_id, expected_origin=self.origins)
        transports = json.dumps((credential.get("response") or {}).get("transports") or [])
        name = (label.strip() or PROVIDERS.get(v.aaguid) or "Passkey")[:60]
        self.db.execute("insert into passkeys (id, member, public_key, sign_count, transports, label, created, aaguid) "
                        "values (?,?,?,?,?,?,?,?)",
                        (bytes_to_base64url(v.credential_id), member, v.credential_public_key, v.sign_count,
                         transports, name, int(time.time()), v.aaguid))

    def authentication_options(self) -> tuple[str, str]:
        opts = generate_authentication_options(rp_id=self.rp_id,
                                               user_verification=UserVerificationRequirement.PREFERRED)
        return self._challenge("login", opts.challenge), options_to_json(opts)

    def step_up_options(self, member: str) -> tuple[str, str]:
        """Ask the signed-in member to confirm with one of their own passkeys (before admin mode)."""
        allow = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["id"]))
                 for r in self.db.execute("select id from passkeys where member=?", (member,))]
        opts = generate_authentication_options(rp_id=self.rp_id, allow_credentials=allow,
                                               user_verification=UserVerificationRequirement.REQUIRED)
        return self._challenge("step-up", opts.challenge, member), options_to_json(opts)

    def step_up_link(self, member: str) -> str:
        """For an admin without a passkey here: a one-time code, emailed, that only works for them."""
        return self._challenge("step-up-email", b"", member)

    def use_step_up_link(self, cid: str, member: str) -> bool:
        ch = self._take_challenge(cid, "step-up-email")
        return bool(ch and ch["member"] == member)

    def authenticate(self, cid: str | None, credential: dict, kind: str = "login", member: str | None = None) -> str:
        ch = self._take_challenge(cid, kind)
        if not ch or (member and ch["member"] != member):
            raise ValueError("Sign-in expired; please try again.")
        row = self.db.execute("select * from passkeys where id=?", (credential.get("id"),)).fetchone()
        if member and row and row["member"] != member:
            raise ValueError("That passkey belongs to someone else.")
        if not row:
            raise ValueError("That passkey isn't registered here. Sign in by email, then add it on your account page.")
        v = verify_authentication_response(credential=credential, expected_challenge=ch["challenge"],
                                           expected_rp_id=self.rp_id, expected_origin=self.origins,
                                           credential_public_key=row["public_key"],
                                           credential_current_sign_count=row["sign_count"])
        self.db.execute("update passkeys set sign_count=?, last_used=? where id=?",
                        (v.new_sign_count, int(time.time()), row["id"]))
        return row["member"]

    def passkeys(self, member: str) -> list[sqlite3.Row]:
        return self.db.execute("select id, label, created, last_used from passkeys where member=? "
                               "order by created", (member,)).fetchall()

    def rename_passkey(self, member: str, pid: str, label: str) -> None:
        self.db.execute("update passkeys set label=? where id=? and member=?", (label.strip()[:60] or "Passkey", pid, member))

    def remove_passkey(self, member: str, pid: str) -> None:
        self.db.execute("delete from passkeys where id=? and member=?", (pid, member))


def _safe_next(url: str) -> str:
    """Only allow local paths as a post-login redirect. Browsers read "/\\x" (and "/<tab>/x": they drop tabs and newlines)
    as "//x", another site, so a backslash or any control character sends you home instead."""
    ok = url.startswith("/") and not url.startswith("//") and "\\" not in url and not any(ord(c) < 32 or ord(c) == 127 for c in url)
    return url if ok else "/"


def _preview_outbox(recipients, subject, body, attachments, only_to, reply_to) -> int:
    """Preview mode: record the email (and who test mode would withhold it from) instead of sending."""
    allowed = [r for r in recipients if only_to is None or r.strip().lower() in only_to]
    entry = {"at": int(time.time()), "to": allowed, "withheld": [r for r in recipients if r not in allowed],
             "subject": subject, "body": body, "reply_to": reply_to, "attachments": [a[0] for a in attachments]}
    p = DATA / "preview-outbox.jsonl"
    with p.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("preview: email %r recorded in the outbox, not sent", one_line(subject))
    return len(allowed)


EMAIL_LOG_KEEP = 1000  # entries kept in email-log.jsonl


def _email_log(entry: dict) -> None:
    """Live site: every email the app sends or holds back (test mode), for admins' Email log page."""
    p = DATA / "email-log.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps({"at": int(time.time()), **entry}) + "\n")
        p.chmod(0o600)
        lines = p.read_text().splitlines()
        if len(lines) > EMAIL_LOG_KEEP * 1.5:
            p.write_text("\n".join(lines[-EMAIL_LOG_KEEP:]) + "\n")
    except OSError:
        log.exception("couldn't write the email log")


def email_ready() -> bool:
    """Whether this server can send email (SMTP settings in the env file), or records it instead (a preview)."""
    return bool(PREVIEW or (os.environ.get("CLUB_MINUTES_SMTP_USER") and os.environ.get("CLUB_MINUTES_SMTP_PASSWORD")))


def send_email(to: str | list[str], subject: str, body: str, attachments: list[tuple[str, bytes, str]] = (),
               only_to: set[str] | None = None, reply_to: str | None = None, to_self: bool = False) -> int:
    """Send through SMTP (Gmail by default) using CLUB_MINUTES_SMTP_* environment variables.

    `to` is one address or a list; each recipient gets their own copy. `only_to` is the
    test-mode allowlist (club.yaml email.only_to): anyone else is withheld. Without SMTP
    credentials, messages are logged instead. Returns how many messages were sent.
    `to_self`: the email goes to the person asking for it (a sign-in link, confirming a new address).
    On stage, an email reaches only the person clicking: one copy to them, or to `to` if `to_self`;
    with nobody signed in (reminders, timers), it's held, and either way listed in the Email log.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    if PREVIEW:
        return _preview_outbox(recipients, subject, body, attachments, only_to, reply_to)
    withheld = []
    if STAGE:
        allowed = recipients if to_self else [ACTOR.get()] if ACTOR.get() else []
        withheld = [r for r in recipients if r not in allowed]
        recipients, subject = allowed, "[STAGE] " + subject
        body = "(From the STAGING server: a test copy of the app, not the live one.)\n\n" + body
    if only_to is not None:
        allowed = [r for r in recipients if r.strip().lower() in only_to]
        withheld += [r for r in recipients if r not in allowed]
        if withheld:
            log.warning("email test mode: withheld %r from %d recipient(s) not in email.only_to",
                        one_line(subject), len(withheld))
        recipients = allowed
    entry = {"to": recipients, "withheld": withheld, "subject": subject, "body": body, "reply_to": reply_to,
             "attachments": [a[0] for a in attachments]}
    if not recipients:
        _email_log({**entry, "status": "held back (stage: only the person clicking gets email)" if STAGE else "held back (test mode)"})
        return 0
    user = os.environ.get("CLUB_MINUTES_SMTP_USER")
    password = (os.environ.get("CLUB_MINUTES_SMTP_PASSWORD") or "").replace(" ", "")  # Google shows app passwords in groups
    if not (user and password):
        log.warning("SMTP not configured; email to %s not sent: %s | %s", one_line(", ".join(recipients)), one_line(subject),
                    one_line(body)[:2000])
        _email_log({**entry, "status": "not sent (no SMTP settings)"})
        return 0
    try:
        sent = _smtp_send(recipients, subject, body, attachments, reply_to, user, password)
    except Exception as e:
        _email_log({**entry, "status": f"failed: {e}"[:200]})
        raise
    _email_log({**entry, "status": "sent"})
    return sent


def _smtp_send(recipients, subject, body, attachments, reply_to, user, password) -> int:
    reply_to = reply_to or os.environ.get("CLUB_MINUTES_SMTP_REPLY_TO")
    host, port = os.environ.get("CLUB_MINUTES_SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("CLUB_MINUTES_SMTP_PORT", "587"))
    sender = Club().info.get("callsign") or "Club Minutes"  # the From name, unless CLUB_MINUTES_SMTP_FROM sets it all
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        for r in recipients:
            msg = EmailMessage()
            msg["From"] = os.environ.get("CLUB_MINUTES_SMTP_FROM", f"{sender} <{user}>")
            msg["To"] = r
            msg["Subject"] = subject
            if reply_to:
                msg["Reply-To"] = reply_to
            msg.set_content(body)
            for name, data, mime in attachments:
                maintype, subtype = mime.split("/", 1)
                msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
            s.send_message(msg)
    log.info("email %r sent to %d recipient(s)", one_line(subject), len(recipients))
    return len(recipients)
