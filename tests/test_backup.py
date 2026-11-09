"""Backups: the data folder encrypted, checked, kept, sent to S3-compatible storage; restore into an empty folder."""
import sqlite3
from pathlib import Path

import pytest

from w8lbz_minutes import backup
from w8lbz_minutes.club import Club

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
    monkeypatch.setenv("W8LBZ_BACKUP_PASSPHRASE", PW)
    return d


def test_backup_round_trip(data, tmp_path):
    name, blob, n = backup.make()
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


def test_nightly_run_keeps_sends_and_reports(data, monkeypatch):
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Amy", "last": "Able", "email": "amy@example.org"}])
    club.admins = {"kd8adm"}
    club.backup = {"s3": {"endpoint": "https://s3.us-west-004.backblazeb2.com", "bucket": "club-backups",
                          "region": "us-west-004", "prefix": "minutes/"}}
    monkeypatch.setenv("W8LBZ_S3_KEY_ID", "keyid")
    monkeypatch.setenv("W8LBZ_S3_SECRET", "secret")
    put = []
    monkeypatch.setattr("httpx.put", lambda url, content, headers, timeout: put.append((url, headers))
                        or type("R", (), {"status_code": 200, "text": ""})())
    for _ in range(backup.KEEP_LOCAL + 2):
        st = backup.run(club)
    assert st["ok"] and st["sent_to"].startswith("club-backups/minutes/w8lbz-minutes-")
    assert len(backup.local_copies()) <= backup.KEEP_LOCAL and not list(data.glob("backups/backups"))
    url, h = put[-1]
    assert url.startswith("https://s3.us-west-004.backblazeb2.com/club-backups/minutes/")
    assert h["authorization"].startswith("AWS4-HMAC-SHA256 Credential=keyid/") and "/us-west-004/s3/aws4_request" in h["authorization"]
    assert "backups/" not in str(backup.members((data / "backups" / backup.local_copies()[0].name).read_bytes(), PW))
    sent = []
    monkeypatch.setattr("w8lbz_minutes.auth.send_email", lambda to, subject, body, **kw: sent.append((to, subject)))
    monkeypatch.setenv("W8LBZ_BACKUP_PASSPHRASE", "short")
    st = backup.run(club)
    assert not st["ok"] and "W8LBZ_BACKUP_PASSPHRASE" in st["error"] and backup.status()["ok"] is False
    assert sent[0][0] == ["amy@example.org"] and "backup failed" in sent[0][1]


def test_sigv4_matches_botocore():
    """The same signature botocore's S3SigV4Auth gives for this request (computed once with boto3, 2026-11-08)."""
    import datetime as dt
    cfg = {"endpoint": "https://s3.us-west-004.backblazeb2.com", "bucket": "club-backups", "region": "us-west-004",
           "key_id": "keyid", "secret": "secret"}
    url, h = backup.s3_request(cfg, "PUT", "minutes/w8lbz a.enc", b"x", now=dt.datetime(2026, 11, 9, 3, 6, 57, tzinfo=dt.timezone.utc))
    assert url == "https://s3.us-west-004.backblazeb2.com/club-backups/minutes/w8lbz%20a.enc"
    assert h["authorization"].endswith("Signature=723393ff098d298c48a161b085e26c52035aabb0b57248f1fb2d6d1bc31051fb")
