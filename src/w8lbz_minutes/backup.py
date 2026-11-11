"""Backups the app makes itself: the whole data folder in one encrypted archive, kept on the server and sent off it.

- What: every file in the data folder (minutes, chow, business, calendars, settings, logs, the roster cache)
  plus a consistent copy of the sign-in database, as a .tar.gz. Lock and temp files and the backups themselves
  are left out.
- Encrypted with a passphrase only the admins hold (W8LBZ_BACKUP_PASSPHRASE in /etc/w8lbz-minutes/env):
  scrypt -> AES-256-GCM. File layout: MAGIC, 16-byte salt, 12-byte nonce, ciphertext (with its tag).
  Without the passphrase a backup can't be read, by the storage provider or anyone else; lose it and the
  backups are useless.
- Checked: each backup is decrypted and its file list read back before it's kept or sent.
- Where: the last KEEP_LOCAL in data/backups/ on the server, and either or both of S3-compatible storage
  (Backblaze B2; how long they're kept is the bucket's lifecycle rule) and a Google Drive folder in a Shared
  Drive (the app keeps the last `keep` there). Admins choose on the Backups page (backup-settings.json, which
  holds the S3 secret and is left out of the backups; W8LBZ_S3_KEY_ID / W8LBZ_S3_SECRET override it).
- Restore: `w8lbz-minutes restore FILE DIR` (docs/server-admin.md -> Backups).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
import json
import logging
import os
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from . import branding
from .club import DATA, ROOT, Club

KEY_FILE = Path(os.environ.get("W8LBZ_GOOGLE_KEY", ROOT / "private" / "google-service-account.json"))  # Drive backups only

log = logging.getLogger("w8lbz.backup")
MAGIC = b"W8LBZ-BACKUP-1\n"
KEEP_LOCAL = 7  # the default; the Backups page changes it
# What the Backups page recommends: the data folder is small (MBs), so daily backups cost next to nothing.
RECOMMENDED = {"every": "daily", "hour": 2, "keep_local": 7, "drive_keep": 30, "s3_days": 90}
SKIP_SUFFIXES = (".lock", ".tmp")
SKIP_DIRS = ("backups",)
SKIP_FILES = ("backup-settings.json",)  # holds the S3 secret: re-enter it after a restore


class BackupError(Exception):
    pass


def passphrase() -> str:
    p = os.environ.get("W8LBZ_BACKUP_PASSPHRASE", "")
    if len(p) < 12:
        raise BackupError("Set W8LBZ_BACKUP_PASSPHRASE (12 characters or more) in /etc/w8lbz-minutes/env.")
    return p


# ---------------- choosing and checking the passphrase (never stored or logged by these) ----------------
# Capitals and digits without look-alikes (0/O, 1/I/L), in groups of four: easy to read off a printout and type.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def suggest(groups: int = 6) -> str:
    """A random passphrase like 7KQ4-M2XH-R9TB-W3PD-6YNC-HE8V (24 characters, about 119 bits)."""
    import secrets
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(groups))


def strength(pw: str) -> tuple[str, int]:
    """(weak | good | strong, estimated bits), assuming it was chosen at random: a password manager's generator,
    dice, or suggest(). Words separated by spaces or dashes count as words (about 12.9 bits each, as dice words);
    otherwise characters count by the kinds used. Separators add nothing."""
    import math
    import re as _re
    chunks = [c for c in _re.split(r"[\s\-_.]+", pw) if c]
    if len(chunks) >= 3 and all(c.isalpha() for c in chunks) and sum(map(len, chunks)) / len(chunks) >= 3:
        bits = round(len(chunks) * 12.9)
    else:
        chars = "".join(chunks)
        pool = sum(n for test, n in ((str.islower, 26), (str.isupper, 26), (str.isdigit, 10)) if any(test(ch) for ch in chars))
        pool += 33 if any(not ch.isalnum() for ch in chars) else 0
        bits = round(len(chars) * math.log2(pool)) if pool else 0
    return ("strong" if bits >= 90 else "good" if bits >= 64 else "weak"), bits


def pwned(pw: str) -> int | None:
    """How often it appears in Have I Been Pwned's leaked passwords (0: not found), or None if HIBP can't be
    reached. Only the first 5 characters of its SHA-1 leave the server (k-anonymity), and the reply is padded."""
    import httpx
    h = hashlib.sha1(pw.encode(), usedforsecurity=False).hexdigest().upper()  # HIBP's API is keyed by SHA-1
    try:
        r = httpx.get(f"https://api.pwnedpasswords.com/range/{h[:5]}", timeout=10,
                      headers={"Add-Padding": "true", "User-Agent": "Minutes-backup-check"})
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    for line in r.text.splitlines():
        suffix, _, count = line.partition(":")
        if suffix.strip() == h[5:]:
            return int(count)  # padding lines have a count of 0
    return 0


def check(pw: str) -> tuple[bool, str]:
    """For the Backups page: (all good?, one line saying whether it's the server's passphrase, its strength,
    and whether it's leaked)."""
    server = os.environ.get("W8LBZ_BACKUP_PASSPHRASE", "")
    matches = bool(server) and hmac.compare_digest(pw.encode(), server.encode())
    level, bits = strength(pw)
    leaks = pwned(pw)
    parts = ["It matches the server's passphrase." if matches else
             "It does NOT match the server's passphrase." if server else "The server has no passphrase set yet.",
             f"Strength: {level} (about {bits} bits, if it was chosen at random)." + (" Use a longer one." if level == "weak" else ""),
             "Leaks: couldn't reach Have I Been Pwned to check." if leaks is None else
             "Leaks: not found in Have I Been Pwned's leaked passwords." if not leaks else
             f"Leaks: FOUND {leaks:,} times in leaked passwords. Don't use it."]
    return matches and level != "weak" and not leaks, " ".join(parts)


def _key(pw: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 2 ** 20, dklen=32)


def encrypt(data: bytes, pw: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(pw, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, pw: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(MAGIC):
        raise BackupError("That isn't a backup from this app.")
    b = blob[len(MAGIC):]
    try:
        return AESGCM(_key(pw, b[:16])).decrypt(b[16:28], b[28:], MAGIC)
    except InvalidTag:
        raise BackupError("Wrong passphrase, or the file is damaged.")


def archive(data: Path = None) -> bytes:
    """The data folder as .tar.gz bytes. The sign-in database is copied with SQLite's backup API (consistent
    even while the app writes to it)."""
    data = data or DATA
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar, tempfile.TemporaryDirectory() as tmp:
        for p in sorted(data.rglob("*")):
            rel = p.relative_to(data)
            if not p.is_file() or rel.parts[0] in SKIP_DIRS or p.name in SKIP_FILES or p.suffix in SKIP_SUFFIXES or p.name.endswith(("-wal", "-shm")):
                continue
            if p.suffix == ".sqlite":
                copy = Path(tmp) / p.name
                src, dst = sqlite3.connect(p), sqlite3.connect(copy)
                with dst:
                    src.backup(dst)
                src.close()
                dst.close()
                tar.add(copy, arcname=str(rel))
            else:
                tar.add(p, arcname=str(rel))
    return buf.getvalue()


def members(blob: bytes, pw: str) -> list[str]:
    """Decrypt and list the files: the check that a backup really opens."""
    with tarfile.open(fileobj=io.BytesIO(decrypt(blob, pw)), mode="r:gz") as tar:
        return tar.getnames()


def make(pw: str | None = None) -> tuple[str, bytes, int]:
    """(file name, encrypted bytes, number of files), checked."""
    pw = pw or passphrase()
    blob = encrypt(archive(), pw)
    n = len(members(blob, pw))
    if not n:
        raise BackupError("The backup came out empty.")
    return f"w8lbz-minutes-{dt.datetime.now():%Y-%m-%d-%H%M}.tar.gz.enc", blob, n


def restore(blob: bytes, pw: str, dest: Path) -> int:
    """Unpack a backup into an empty folder (never over live data). Returns how many files."""
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise BackupError(f"{dest} isn't empty; restore into an empty folder, then move it into place.")
    with tarfile.open(fileobj=io.BytesIO(decrypt(blob, pw)), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")  # no absolute paths, links out of dest, or device files
        return len(tar.getnames())


# ---------------- settings (the Backups page) ----------------
SETTINGS = "backup-settings.json"
DRIVE_KEEP = 30


def settings() -> dict:
    p = DATA / SETTINGS
    s = json.loads(p.read_text()) if p.exists() else {}
    s.setdefault("s3", {})
    s.setdefault("drive", {})
    s.setdefault("schedule", {"every": RECOMMENDED["every"], "hour": RECOMMENDED["hour"]})
    s.setdefault("keep_local", KEEP_LOCAL)
    return s


def _int(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(int(v), hi))
    except (TypeError, ValueError):
        return default


def due(now: dt.datetime | None = None) -> bool:
    """The hourly timer asks this: is a scheduled backup due? One try per day (or week) from the chosen hour on,
    counting a failed try too, so a broken destination means one email a day, not one an hour."""
    now = now or dt.datetime.now()
    if len(os.environ.get("W8LBZ_BACKUP_PASSPHRASE", "")) < 12:
        return False  # backups not set up yet: no nightly "backup failed" email (the Backups page says so)
    sch = settings()["schedule"]
    if sch.get("every") == "off" or now.hour < sch.get("hour", 2):
        return False
    last = status().get("at")
    if not last:
        return True
    last = dt.datetime.fromtimestamp(last)
    return now - last >= dt.timedelta(days=6, hours=12) if sch.get("every") == "weekly" else last.date() < now.date()


def data_size(data: Path = None) -> int:
    """Bytes in what a backup takes (before compression)."""
    data = data or DATA
    return sum(p.stat().st_size for p in data.rglob("*") if p.is_file() and p.relative_to(data).parts[0] not in SKIP_DIRS
               and p.name not in SKIP_FILES)


def save_settings(form: dict) -> dict:
    """From the Backups page (a trust boundary): which destinations are on, and their details. A blank secret
    keeps the saved one."""
    from urllib.parse import urlparse
    old = settings()
    s3 = {k: " ".join(str(form.get(f"s3_{k}", "")).split()) for k in ("endpoint", "region", "bucket", "prefix", "key_id")}
    s3["on"] = bool(form.get("s3_on"))
    # The secret is kept encrypted with the backup passphrase, never as plain text (re-enter it if that changes).
    typed = str(form.get("s3_secret") or "").strip()
    if typed:
        import base64
        s3["secret_enc"] = base64.b64encode(encrypt(typed.encode(), passphrase())).decode()
    elif old["s3"].get("secret_enc"):
        s3["secret_enc"] = old["s3"]["secret_enc"]
    drive = {"on": bool(form.get("drive_on")), "folder": str(form.get("drive_folder", "")).strip(),
             "keep": _int(form.get("drive_keep"), 1, 365, DRIVE_KEEP)}
    every = form.get("every") if form.get("every") in ("daily", "weekly", "off") else RECOMMENDED["every"]
    schedule = {"every": every, "hour": _int(form.get("hour"), 0, 23, RECOMMENDED["hour"])}
    if "/folders/" in drive["folder"]:  # a pasted folder link
        drive["folder"] = drive["folder"].split("/folders/")[1].split("?")[0].split("/")[0]
    if s3["on"]:
        if urlparse(s3["endpoint"]).scheme != "https" or not s3["bucket"]:
            raise BackupError("S3 needs an https endpoint and a bucket.")
        s3["region"] = s3["region"] or "us-east-1"
    if drive["on"] and not drive["folder"].replace("-", "").replace("_", "").isalnum():
        raise BackupError("Paste the Google Drive folder's link or ID.")
    s = {"s3": s3, "drive": drive, "schedule": schedule, "keep_local": _int(form.get("keep_local"), 1, 60, KEEP_LOCAL)}
    p = DATA / SETTINGS
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return s


# ---------------- S3-compatible storage (Backblaze B2, AWS, Wasabi): one signed PUT ----------------

def s3_config(s: dict | None = None) -> dict | None:
    s3 = (s or settings())["s3"]
    if not s3.get("on"):
        return None
    key_id = os.environ.get("W8LBZ_S3_KEY_ID") or s3.get("key_id")
    secret = os.environ.get("W8LBZ_S3_SECRET")
    if not secret and s3.get("secret_enc"):
        import base64
        try:
            secret = decrypt(base64.b64decode(s3["secret_enc"]), passphrase()).decode()
        except BackupError:
            raise BackupError("The saved S3 secret no longer opens (the backup passphrase changed?): enter it again.") from None
    if not (key_id and secret):
        raise BackupError("S3 is on, but its key ID or secret is missing.")
    return {"endpoint": s3["endpoint"].rstrip("/"), "bucket": s3["bucket"], "region": s3.get("region") or "us-east-1",
            "prefix": s3.get("prefix", ""), "key_id": key_id, "secret": secret}


def _sign(k: bytes, msg: str) -> bytes:
    return hmac.new(k, msg.encode(), hashlib.sha256).digest()


def s3_request(cfg: dict, method: str, key: str, body: bytes = b"", now: dt.datetime | None = None) -> tuple[str, dict]:
    """URL and headers for an AWS Signature Version 4 request (path-style), so no SDK is needed."""
    from urllib.parse import quote, urlparse
    now = now or dt.datetime.now(dt.timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    host = urlparse(cfg["endpoint"]).netloc
    path = "/" + quote(f"{cfg['bucket']}/{key}")
    digest = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": digest, "x-amz-date": amz}
    signed = ";".join(sorted(headers))
    canonical = "\n".join([method, path, "", *(f"{h}:{headers[h]}" for h in sorted(headers)), "", signed, digest])
    scope = f"{day}/{cfg['region']}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(("AWS4" + cfg["secret"]).encode(), day), cfg["region"]), "s3"), "aws4_request")
    headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={cfg['key_id']}/{scope}, SignedHeaders={signed}, "
                                f"Signature={hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()}")
    return cfg["endpoint"] + path, headers


def s3_upload(cfg: dict, name: str, blob: bytes) -> str:
    import httpx
    key = f"{cfg['prefix']}{name}"
    url, headers = s3_request(cfg, "PUT", key, blob)
    r = httpx.put(url, content=blob, headers=headers, timeout=300)
    if r.status_code >= 300:
        raise BackupError(f"Upload to {cfg['bucket']} failed: HTTP {r.status_code} {r.text[:200]}")
    return f"{cfg['bucket']}/{key}"


# ---------------- Google Drive: the service account, into a folder in a Shared Drive ----------------
DRIVE = "https://www.googleapis.com/drive/v3/files"


def drive_session():
    import google.auth.transport.requests as gr
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=["https://www.googleapis.com/auth/drive"])
    return gr.AuthorizedSession(creds)


def _drive_error(r) -> BackupError:
    if "storageQuotaExceeded" in r.text or "do not have storage quota" in r.text:
        return BackupError("Google Drive refused: the service account has no storage of its own. The folder must be in a "
                           "Shared Drive (Google Workspace), with the service account added as Content manager.")
    return BackupError(f"Google Drive: HTTP {r.status_code} {r.text[:200]}")


def drive_upload(folder: str, name: str, blob: bytes, keep: int = DRIVE_KEEP, session=None) -> str:
    """Upload into the folder, then delete all but the newest `keep` of the app's backups there."""
    s = session or drive_session()
    boundary = "w8lbz" + os.urandom(8).hex()
    meta = json.dumps({"name": name, "parents": [folder]}).encode()
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta +
            f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + blob + f"\r\n--{boundary}--".encode())
    r = s.post("https://www.googleapis.com/upload/drive/v3/files", params={"uploadType": "multipart", "supportsAllDrives": "true"},
               data=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"}, timeout=300)
    if r.status_code >= 300:
        raise _drive_error(r)
    r = s.get(DRIVE, params={"q": f"'{folder}' in parents and name contains 'w8lbz-minutes-' and trashed = false",
                             "orderBy": "name desc", "fields": "files(id,name)", "pageSize": 1000,
                             "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}, timeout=60)
    if r.status_code >= 300:
        raise _drive_error(r)
    for f in r.json().get("files", [])[keep:]:  # names sort by date
        d = s.delete(f"{DRIVE}/{f['id']}", params={"supportsAllDrives": "true"}, timeout=60)
        if d.status_code >= 300:
            raise _drive_error(d)
    return f"Google Drive folder {folder}"


# ---------------- the nightly run (`w8lbz-minutes backup`) and its status ----------------

def status() -> dict:
    p = DATA / "backup-status.json"
    return json.loads(p.read_text()) if p.exists() else {}


def local_copies() -> list[Path]:
    d = DATA / "backups"
    return sorted(d.glob("*.enc"), reverse=True) if d.exists() else []


def run(club: Club, url: str = "") -> dict:
    """Make, check, keep and send a backup. Records the outcome; emails the admins if it failed."""
    st = {"at": int(time.time())}
    try:
        name, blob, n = make()
        d = DATA / "backups"
        d.mkdir(mode=0o700, exist_ok=True)
        (d / name).write_bytes(blob)
        os.chmod(d / name, 0o600)
        for old in local_copies()[settings()["keep_local"]:]:
            old.unlink()
        st.update(ok=True, name=name, size=len(blob), files=n, sent_to=[])
        s, errors = settings(), []
        for dest in ("S3", "Google Drive"):
            try:  # one destination failing doesn't stop the other
                if dest == "S3" and (cfg := s3_config(s)):
                    st["sent_to"].append(s3_upload(cfg, name, blob))
                elif dest == "Google Drive" and s["drive"].get("on"):
                    st["sent_to"].append(drive_upload(s["drive"]["folder"], name, blob, s["drive"].get("keep", DRIVE_KEEP)))
            except Exception as e:
                log.exception("sending the backup to %s failed", dest)
                errors.append(f"{dest}: {e}")
        if errors:
            st.update(ok=False, error="Kept on the server, but " + "; ".join(errors))
    except Exception as e:  # anything at all: the admins must hear about it
        log.exception("backup failed")
        st.update(ok=False, error=str(e))
    if not st["ok"]:
        _tell_admins(club, st, url)
    p = DATA / "backup-status.json"
    p.write_text(json.dumps(st))
    return st


def _tell_admins(club: Club, st: dict, url: str) -> None:
    from . import auth
    to = [m.email for mid, m in club.members.items() if mid in club.admins and m.email]
    if not to:
        return
    try:
        auth.send_email(to, f"{branding.name(club)}: the backup failed",
                        f"Tonight's backup didn't finish:\n\n  {st['error']}\n\nThe last good one is listed on the "
                        f"Backups page: {url}/admin/backups\n\n73,\n{branding.name(club)}",
                        only_to=club.email_only_to())
    except Exception:
        log.exception("couldn't email the admins about the failed backup")
