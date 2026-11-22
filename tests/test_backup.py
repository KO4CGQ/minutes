"""Backups: the data folder encrypted, checked, kept, sent to S3-compatible storage; restore into an empty folder."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from club_minutes import backup
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
PW = "correct horse battery"


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    (d / "meetings" / "2026-11-05").mkdir(parents=True)
    (d / "meetings" / "2026-11-05" / "minutes.md").write_text("# minutes")
    (d / "chow.json").write_text("{}")
    (d / "business.lock").write_text("")
    db = sqlite3.connect(d / "auth.sqlite")
    db.execute("create table t (x)")
    db.execute("insert into t values (42)")
    db.commit()
    monkeypatch.setattr(backup, "DATA", d)
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", PW)
    return d


def test_backup_round_trip(data, tmp_path):
    name, path, n = backup.make()
    blob = path.read_bytes()
    path.unlink()
    assert name.endswith(".tar.gz.enc") and b"minutes" not in blob  # encrypted
    assert sorted(backup.members(blob, PW)) == ["auth.sqlite", "chow.json", "meetings/2026-11-05/minutes.md"]  # no lock file
    with pytest.raises(backup.BackupError, match="Wrong passphrase"):
        backup.members(blob, "not the passphrase")
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.members(blob[:-1] + bytes([blob[-1] ^ 1]), PW)
    out = tmp_path / "restored"
    assert backup.restore(blob, PW, out) == n
    assert sqlite3.connect(out / "auth.sqlite").execute("select x from t").fetchone() == (42,)
    with pytest.raises(backup.BackupError, match="isn't empty"):
        backup.restore(blob, PW, out)


def test_a_backup_from_before_1_0_says_so(monkeypatch):
    """0.x backups (the app's earlier name) aren't read since 1.1.0: a 0.x install restores them."""
    monkeypatch.setattr(backup, "MAGIC", b"W8LBZ-BACKUP-1\n")
    old = backup.encrypt(b"data", PW)
    monkeypatch.undo()
    with pytest.raises(backup.BackupError, match="made before 1.0.0"):
        backup.decrypt(old, PW)


class FakeDrive:
    """Drive's upload, list and delete, in memory."""
    def __init__(self, quota=False):
        self.files, self.quota, self.n = [], quota, 0

    def _r(self, code=200, data=None, text=""):
        return type("R", (), {"status_code": code, "text": text, "json": lambda self: data or {}})()

    def post(self, url, params, data, headers, timeout):
        if self.quota:
            return self._r(403, text='{"reason": "storageQuotaExceeded"}')
        data = b"".join(data)  # streamed from the file
        name = json.loads(data.split(b"\r\n\r\n", 1)[1].split(b"\r\n", 1)[0])["name"]
        self.n += 1
        self.files.append({"id": str(self.n), "name": name})
        return self._r()

    def get(self, url, params, timeout):
        return self._r(data={"files": sorted(self.files, key=lambda f: int(f["id"]), reverse=True)})  # createdTime desc

    def delete(self, url, params, timeout):
        self.files = [f for f in self.files if f["id"] != url.rsplit("/", 1)[1]]
        return self._r(204)


def test_nightly_run_keeps_sends_and_reports(data, monkeypatch):
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed", "email": "amy@example.org"}])
    club.admins = {"n0admn"}
    backup.save_settings({"s3_on": "1", "s3_endpoint": "https://s3.us-west-004.backblazeb2.com", "s3_bucket": "club-backups",
                          "s3_region": "us-west-004", "s3_prefix": "minutes/", "s3_key_id": "keyid", "s3_secret": "secret",
                          "drive_on": "1", "drive_folder": "https://drive.google.com/drive/folders/0ABcd-123?usp=sharing", "drive_keep": "3"})
    s = backup.settings()
    assert s["drive"]["folder"] == "0ABcd-123" and backup.s3_config()["secret"] == "secret"
    assert "secret" not in (data / "backup-settings.json").read_text().replace("secret_enc", "")  # never stored as text
    backup.save_settings({**{f"s3_{k}": v for k, v in s["s3"].items()}, "s3_on": "1", "s3_secret": "", "drive_on": "1",
                          "drive_folder": "0ABcd-123", "drive_keep": "3"})
    assert backup.s3_config()["secret"] == "secret"  # a blank secret keeps the saved one
    with pytest.raises(backup.BackupError):
        backup.save_settings({"s3_on": "1", "s3_endpoint": "http://insecure.example", "s3_bucket": "b"})
    put, drive = [], FakeDrive()
    monkeypatch.setattr("httpx.put", lambda url, content, headers, timeout: put.append((url, headers))
                        or type("R", (), {"status_code": 200, "text": ""})())
    monkeypatch.setattr(backup, "drive_session", lambda: drive)
    # the first two named as before 1.0.0: still pruned oldest-first among the new names
    names = iter(f"{'w8lbz' if d < 3 else 'club'}-minutes-2026-11-{d:02d}-0230.tar.gz.enc" for d in range(1, 30))
    real_make = backup.make
    monkeypatch.setattr(backup, "make", lambda: (next(names), *real_make()[1:]))
    for _ in range(backup.KEEP_LOCAL + 2):
        st = backup.run(club)
    assert st["ok"] and st["sent_to"][0].startswith("club-backups/minutes/club-minutes-") and "Google Drive" in st["sent_to"][1]
    assert [p.name[:5] for p in backup.local_copies()] == ["club-"] * backup.KEEP_LOCAL and [f["name"].rsplit("minutes-", 1)[1][:10] for f in drive.files] == [
        "2026-11-07", "2026-11-08", "2026-11-09"]  # the newest 3 on Drive
    url, h = put[-1]
    assert url.startswith("https://s3.us-west-004.backblazeb2.com/club-backups/minutes/")
    assert h["authorization"].startswith("AWS4-HMAC-SHA256 Credential=keyid/") and "/us-west-004/s3/aws4_request" in h["authorization"]
    assert "backup-settings.json" not in backup.members(backup.local_copies()[0].read_bytes(), PW)  # the secret stays out
    sent = []
    monkeypatch.setattr("club_minutes.auth.send_email", lambda to, subject, body, **kw: sent.append((to, subject)))
    drive.quota = True  # a My Drive folder: kept on the server and sent to S3, but reported
    st = backup.run(club)
    assert not st["ok"] and "Shared Drive" in st["error"] and st["sent_to"] and backup.local_copies()
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", "short")
    st = backup.run(club)
    assert not st["ok"] and "CLUB_MINUTES_BACKUP_PASSPHRASE" in st["error"] and backup.status()["ok"] is False
    assert len(sent) == 2 and sent[0][0] == ["amy@example.org"] and "backup failed" in sent[0][1]


def test_sigv4_matches_botocore():
    """The same signature botocore's S3SigV4Auth gives for this request (computed once with boto3, 2026-11-08)."""
    import datetime as dt
    cfg = {"endpoint": "https://s3.us-west-004.backblazeb2.com", "bucket": "club-backups", "region": "us-west-004",
           "key_id": "keyid", "secret": "secret"}
    url, h = backup.s3_request(cfg, "PUT", "minutes/w8lbz a.enc", b"x", now=dt.datetime(2026, 11, 9, 3, 6, 57, tzinfo=dt.UTC))
    assert url == "https://s3.us-west-004.backblazeb2.com/club-backups/minutes/w8lbz%20a.enc"
    assert h["authorization"].endswith("Signature=723393ff098d298c48a161b085e26c52035aabb0b57248f1fb2d6d1bc31051fb")


def test_schedule_and_page(data, tmp_path, monkeypatch):
    import datetime as dt

    from fastapi.testclient import TestClient

    from club_minutes import web
    assert backup.due(dt.datetime(2026, 11, 9, 3)) and not backup.due(dt.datetime(2026, 11, 9, 1))  # daily at 2 by default
    (data / "backup-status.json").write_text(f'{{"at": {int(dt.datetime(2026, 11, 9, 2, 5).timestamp())}, "ok": false}}')
    assert not backup.due(dt.datetime(2026, 11, 9, 23)) and backup.due(dt.datetime(2026, 11, 10, 2))  # one try a day
    backup.save_settings({"every": "weekly", "hour": "4", "keep_local": "3"})
    assert not backup.due(dt.datetime(2026, 11, 14, 5)) and backup.due(dt.datetime(2026, 11, 16, 4))
    backup.save_settings({"every": "off"})
    assert not backup.due(dt.datetime(2026, 12, 1, 12))
    backup.save_settings({"every": "daily", "hour": "2"})
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", "")
    assert not backup.due(dt.datetime(2026, 12, 1, 12))  # no passphrase yet: the schedule waits, quietly
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", "correct horse battery")
    backup.save_settings({"every": "off"})
    assert backup.data_size() > 0
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed"}])
    club.admins = {"n0admn"}
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web.authn, "member_for", lambda token: "n0admn")
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = TestClient(web.app)
    page = c.get("/admin/backups").text
    assert "Recommended:" in page and "The data now:" in page and "Only when I click" in page
    r = c.post("/admin/backups/download")
    assert r.status_code == 200 and r.content.startswith(backup.MAGIC) and "attachment" in r.headers["content-disposition"]
    c.post("/admin/backups/settings", data={"every": "daily", "hour": "2", "keep_local": "7", "drive_on": "1", "drive_folder": "bad folder!"})
    assert backup.settings()["schedule"]["every"] == "off"  # refused: nothing saved


def test_passphrase_suggest_strength_and_leaks(monkeypatch):
    import hashlib
    s = backup.suggest()
    assert len(s) == 29 and s.count("-") == 5 and not set(s) & set("01OIL") and backup.strength(s)[0] == "strong"
    assert backup.strength("correct horse battery staple")[0] == "weak"         # 4 words
    assert backup.strength("Gl8-zN2-pQ7-xR4-Mv9-kT3-Bw6")[0] == "strong"
    assert backup.strength("password1234")[0] == "weak"
    h = hashlib.sha1(b"password1234").hexdigest().upper()
    asked = []

    def fake_get(url, timeout, headers):
        asked.append((url, headers))
        return type("R", (), {"text": f"{h[5:]}:4321\r\n0000000000000000000000000000000000A:0", "raise_for_status": lambda self: None})()
    monkeypatch.setattr("httpx.get", fake_get)
    assert backup.pwned("password1234") == 4321 and asked[0][0].endswith("/range/" + h[:5]) and asked[0][1]["Add-Padding"] == "true"
    assert backup.pwned(s) == 0
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", s)
    ok, msg = backup.check(s)
    assert ok and "matches" in msg and "not found" in msg
    ok, msg = backup.check("password1234")
    assert not ok and "does NOT match" in msg and "FOUND 4,321" in msg and "weak" in msg
    import httpx
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    assert "couldn't reach" in backup.check(s)[1]


def test_update_files_stay_out(tmp_path):
    """A restore must not start an update or leave one "running"."""
    (tmp_path / "members.json").write_text("{}")
    for n in ("update-request.json", "update-running.json", "update-check.json", "update-check-next.json"):
        (tmp_path / n).write_text("{}")
    assert backup.data_size(tmp_path) == 2


def test_backup_streams_pictures_in_chunks(data, tmp_path):
    """A few MB of pictures: peak memory stays far under the data's size, the temp files are gone, and it restores."""
    import os
    import tracemalloc
    for i in range(6):
        (data / "pictures").mkdir(exist_ok=True)
        (data / "pictures" / f"p{i}.jpg").write_bytes(os.urandom(2 * 2 ** 20))  # incompressible, like photos
    size = backup.data_size()
    tracemalloc.start()
    _, path, n = backup.make()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert size > 12 * 2 ** 20 and peak < size / 4, (size, peak)
    assert [p.name for p in (data / "backups").iterdir()] == [path.name]  # only the result: no leftover temp files
    out = tmp_path / "restored"
    assert backup.restore(path, PW, out) == n and (out / "pictures" / "p5.jpg").stat().st_size == 2 * 2 ** 20
    path.unlink()


def test_make_cleans_up_when_it_fails(data, monkeypatch):
    monkeypatch.setattr(backup, "members", lambda *a, **k: [])
    with pytest.raises(backup.BackupError, match="empty"):
        backup.make()
    assert list((data / "backups").iterdir()) == []


def test_a_backup_made_by_the_old_in_memory_code_still_restores_and_new_ones_match_its_format(data, tmp_path):
    """The previous release's make(): AESGCM one-shot over the whole tar.gz, MAGIC + salt + nonce + ciphertext + tag."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    old = backup.MAGIC + salt + nonce + AESGCM(backup._key(PW, salt)).encrypt(nonce, backup.archive(), backup.MAGIC)
    old_file = tmp_path / "old.enc"
    old_file.write_bytes(old)
    out = tmp_path / "from-old"
    assert backup.restore(old_file, PW, out) == 3 and (out / "chow.json").read_text() == "{}"
    assert sorted(backup.members(old, PW)) == sorted(backup.members(old_file, PW))  # bytes and file both read
    # and the other way: the new streamed file opens with the old one-shot decrypt
    _, path, n = backup.make()
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(backup.decrypt(path.read_bytes(), PW))) as t:
        assert len(t.getnames()) == n
    path.unlink()


def test_a_crashed_backups_leftover_is_swept(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(backup, "DATA", tmp_path)
    (tmp_path / "roster.json").write_text("{}")
    d = tmp_path / "backups"
    d.mkdir()
    old, new = d / ".making-old.part", d / ".making-new.part"
    old.write_bytes(b"x"), new.write_bytes(b"x")
    os.utime(old, (0, 0))
    _name, path, _n = backup.make("correct horse battery staple")
    path.unlink()
    assert not old.exists() and new.exists()
