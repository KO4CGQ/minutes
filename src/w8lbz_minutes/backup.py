"""Backups the app makes itself: the whole data folder in one encrypted archive, kept on the server and sent off it.

- What: every file in the data folder (minutes, chow, business, calendars, settings, logs, the roster cache)
  plus a consistent copy of the sign-in database, as a .tar.gz. Lock and temp files and the backups themselves
  are left out.
- Encrypted with a passphrase only the admins hold (W8LBZ_BACKUP_PASSPHRASE in /etc/w8lbz-minutes/env):
  scrypt -> AES-256-GCM. File layout: MAGIC, 16-byte salt, 12-byte nonce, ciphertext (with its tag).
  Without the passphrase a backup can't be read, by the storage provider or anyone else; lose it and the
  backups are useless.
- Checked: each backup is decrypted and its file list read back before it's kept or sent.
- Where: the last KEEP_LOCAL in data/backups/ on the server, and S3-compatible storage (Backblaze B2) when
  club.yaml has backup.s3 and the environment has W8LBZ_S3_KEY_ID / W8LBZ_S3_SECRET. How many to keep there is
  the bucket's lifecycle setting.
- Restore: `w8lbz-minutes restore FILE DIR` (README -> Backups).
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

from .club import DATA, Club

log = logging.getLogger("w8lbz.backup")
MAGIC = b"W8LBZ-BACKUP-1\n"
KEEP_LOCAL = 7
SKIP_SUFFIXES = (".lock", ".tmp")
SKIP_DIRS = ("backups",)


class BackupError(Exception):
    pass


def passphrase() -> str:
    p = os.environ.get("W8LBZ_BACKUP_PASSPHRASE", "")
    if len(p) < 12:
        raise BackupError("Set W8LBZ_BACKUP_PASSPHRASE (12 characters or more) in /etc/w8lbz-minutes/env.")
    return p


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
        raise BackupError("That isn't a W8LBZ Minutes backup.")
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
            if not p.is_file() or rel.parts[0] in SKIP_DIRS or p.suffix in SKIP_SUFFIXES or p.name.endswith(("-wal", "-shm")):
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


# ---------------- S3-compatible storage (Backblaze B2, AWS, Wasabi): one signed PUT ----------------

def s3_config(club: Club) -> dict | None:
    s3 = ((club.backup or {}).get("s3") or {})
    if not s3.get("bucket"):
        return None
    key_id, secret = os.environ.get("W8LBZ_S3_KEY_ID"), os.environ.get("W8LBZ_S3_SECRET")
    if not (key_id and secret):
        raise BackupError("backup.s3 is set in club.yaml, but W8LBZ_S3_KEY_ID / W8LBZ_S3_SECRET aren't in the environment.")
    return {"endpoint": s3["endpoint"].rstrip("/"), "bucket": s3["bucket"], "region": s3.get("region", "us-east-1"),
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


def upload(cfg: dict, name: str, blob: bytes) -> str:
    import httpx
    key = f"{cfg['prefix']}{name}"
    url, headers = s3_request(cfg, "PUT", key, blob)
    r = httpx.put(url, content=blob, headers=headers, timeout=300)
    if r.status_code >= 300:
        raise BackupError(f"Upload to {cfg['bucket']} failed: HTTP {r.status_code} {r.text[:200]}")
    return f"{cfg['bucket']}/{key}"


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
        for old in local_copies()[KEEP_LOCAL:]:
            old.unlink()
        st.update(ok=True, name=name, size=len(blob), files=n)
        if cfg := s3_config(club):
            st["sent_to"] = upload(cfg, name, blob)
    except Exception as e:  # anything at all: the admins must hear about it
        log.exception("backup failed")
        st.update(ok=False, error=str(e))
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
        auth.send_email(to, f"{club.info.get('callsign', 'W8LBZ')} Minutes: the backup failed",
                        f"Tonight's backup didn't finish:\n\n  {st['error']}\n\nThe last good one is listed on the "
                        f"Backups page: {url}/admin/security/backups\n\n73,\n{club.info.get('callsign', 'W8LBZ')} Minutes",
                        only_to=club.email_only_to())
    except Exception:
        log.exception("couldn't email the admins about the failed backup")
