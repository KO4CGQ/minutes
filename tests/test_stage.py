"""Staging server: email reaches only the person clicking; the one-way sync keeps stage's own files."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_console import FakeSMTP


def stage_mail(monkeypatch, tmp_path):
    from club_minutes import auth
    monkeypatch.setattr(auth, "STAGE", True)
    monkeypatch.setattr(auth, "DATA", tmp_path)
    monkeypatch.setattr(auth.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "club@example.org")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "x")
    FakeSMTP.outbox = []
    return auth


def test_stage_email_goes_only_to_whoever_clicked(monkeypatch, tmp_path):
    auth = stage_mail(monkeypatch, tmp_path)
    everyone = ["amy@example.org", "bob@example.org", "cal@example.org"]
    tok = auth.ACTOR.set("bob@example.org")
    assert auth.send_email(everyone, "Minutes", "Hi") == 1  # the leader submits: only they get a copy
    msg = FakeSMTP.outbox[-1]
    assert msg["To"] == "bob@example.org" and msg["Subject"] == "[STAGE] Minutes" and "STAGING" in msg.get_content()
    assert auth.send_email("dan@example.org", "Your sign-in link", "x", to_self=True) == 1  # goes to whoever asked
    assert FakeSMTP.outbox[-1]["To"] == "dan@example.org"
    auth.ACTOR.reset(tok)
    assert auth.send_email(everyone, "Reminder", "x") == 0 and len(FakeSMTP.outbox) == 2  # nobody clicking: held
    log = [json.loads(line) for line in (tmp_path / "email-log.jsonl").read_text().splitlines()]
    assert log[0]["withheld"] == ["amy@example.org", "cal@example.org"] and log[-1]["to"] == [] and "stage" in log[-1]["status"]


@pytest.mark.skipif(not shutil.which("rsync"), reason="rsync isn't installed (GitHub Actions has it)")
def test_stage_sync_mirrors_prod_but_keeps_stages_own(tmp_path):
    prod, stage = tmp_path / "prod", tmp_path / "stage"
    (prod / "meetings").mkdir(parents=True), stage.mkdir()
    for f, text in {"roster.json": "prod roster", "meetings/m.md": "prod", "auth.sqlite": "PROD SIGN-INS",
                    "email-log.jsonl": "prod mail", "update-settings.json": '{"on": true}'}.items():
        (prod / f).write_text(text)
    for f, text in {"roster.json": "edited on stage", "auth.sqlite": "stage passkeys", "email-log.jsonl": "stage mail",
                    "stage-sync-paused": "x", "leftover.json": "made on stage"}.items():
        (stage / f).write_text(text)
    env = {**os.environ, "CLUB_MINUTES_DATA": str(stage), "STAGE_SYNC_FROM": f"{prod}/", "STAGE_OWNER": "", "STAGE_RESTART": "true"}
    script = str(Path(__file__).parents[1] / "deploy" / "stage-sync.sh")
    assert subprocess.run([script], env=env, check=False).returncode == 0
    assert (stage / "roster.json").read_text() == "edited on stage"  # paused: the nightly run did nothing
    (stage / "stage-sync-request").write_text("n0zaaa")  # Sync now still works while paused
    assert subprocess.run([script], env=env, check=False).returncode == 0
    assert (stage / "roster.json").read_text() == "prod roster" and (stage / "meetings/m.md").read_text() == "prod"
    assert (stage / "auth.sqlite").read_text() == "stage passkeys" and (stage / "email-log.jsonl").read_text() == "stage mail"
    assert not (stage / "update-settings.json").exists() and not (stage / "leftover.json").exists()
    st = json.loads((stage / "stage-sync-status.json").read_text())
    assert st["ok"] and st["by"] == "n0zaaa" and not (stage / "stage-sync-request").exists()
    assert (prod / "auth.sqlite").read_text() == "PROD SIGN-INS" and sorted(p.name for p in prod.iterdir()) == [
        "auth.sqlite", "email-log.jsonl", "meetings", "roster.json", "update-settings.json"]  # prod untouched


def test_stage_page_sync_now_and_pause(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.club import Club
    club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed"}])
    club.admins = {"n0admn"}
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web.authn, "member_for", lambda token: "n0admn")
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    c = TestClient(web.app)
    assert c.get("/admin/stage").status_code == 404  # only on a staging server
    monkeypatch.setattr(web, "STAGE", True)
    monkeypatch.setitem(web.templates.env.globals, "STAGE", True)
    page = c.get("/admin/stage").text
    assert "STAGING" in page and "prod's data" in page and "No sync yet" in page and "Sync now" in page and "Pause nightly sync" in page
    c.post("/admin/stage", data={"action": "sync"})
    assert (tmp_path / "stage-sync-request").read_text() == "n0admn" and "starts within seconds" in c.get("/admin/stage").text
    c.post("/admin/stage", data={"action": "pause"})
    assert (tmp_path / "stage-sync-paused").exists() and "Resume nightly sync" in c.get("/admin/stage").text
    c.post("/admin/stage", data={"action": "resume"})
    assert not (tmp_path / "stage-sync-paused").exists()
    (tmp_path / "stage-sync-status.json").write_text(json.dumps({"ok": False, "finished": 1, "by": "n0admn", "output": "rsync: connection refused"}))
    assert "failed" in (p := c.get("/admin/stage").text) and "connection refused" in p and "Amy Appleseed" in p


def test_stage_sync_never_follows_a_link_planted_in_the_data_folder(tmp_path):
    """The sync runs as root in a folder the app's user can write: links planted there for the status or the request
    must not make it read, overwrite (or, as root, hand over) the file they point at."""
    stage, secret, target = tmp_path / "stage", tmp_path / "secret", tmp_path / "system-file"
    stage.mkdir()
    secret.write_text("TOP SECRET")
    target.write_text("system contents")
    (stage / "stage-sync-status.json").symlink_to(target)
    (stage / "stage-sync-request").symlink_to(secret)
    env = {**os.environ, "CLUB_MINUTES_DATA": str(stage), "STAGE_SYNC_FROM": f"{tmp_path}/nowhere/", "STAGE_OWNER": "", "STAGE_RESTART": "true"}
    subprocess.run([str(Path(__file__).parents[1] / "deploy" / "stage-sync.sh")], env=env, check=False, capture_output=True)
    assert target.read_text() == "system contents" and secret.read_text() == "TOP SECRET"   # neither touched
    st = stage / "stage-sync-status.json"
    assert not st.is_symlink() and "TOP SECRET" not in st.read_text() and json.loads(st.read_text())["ok"] is False
    assert not (stage / "stage-sync-request").exists() and not list(stage.glob(".stage-sync-status.*"))


def test_stage_banner_says_little_to_the_public(monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    monkeypatch.setitem(web.templates.env.globals, "STAGE", True)
    page = TestClient(web.app).get("/login").text
    assert "STAGING" in page and "prod's data" not in page and "email goes only" not in page  # signed out: just "a test copy"
