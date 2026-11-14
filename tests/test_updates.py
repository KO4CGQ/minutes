"""Updating from the admin page: the version check, deploy notes, and the request the root updater picks up."""
import json

import pytest

from club_minutes import updates

NOTES = """- **Business items are tracked** ...
- **Deploy notes:** before updating:
  1. Copy the business list into the data folder:

          cp business.json /var/lib/club-minutes/

  2. Add `admins:` to club.yaml.
- **Reminders** ..."""


REAL_LATEST = updates.latest  # conftest stubs it out for every other test


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "latest", REAL_LATEST)
    monkeypatch.setattr(updates, "DATA", tmp_path)
    monkeypatch.setattr(updates, "PREVIEW", None)
    monkeypatch.setattr(updates, "running", lambda: "0.6.0")
    return tmp_path


def test_check_and_deploy_notes(env):
    assert updates.newer("0.10.0", "0.9.1") and not updates.newer("0.6.0", "0.6.0")
    steps = updates.deploy_notes(NOTES)
    assert steps.startswith("before updating:\n\n1. Copy") and "        cp business.json" in steps and "Reminders" not in steps
    assert updates.deploy_notes("- **Something** else") == ""
    calls = []
    def fetch():
        calls.append(1)
        return {"tag_name": "v0.7.0", "body": NOTES, "html_url": "https://github.com/x"}
    rel = updates.available(fetch)
    assert rel["version"] == "0.7.0" and "admins:" in rel["deploy_notes"]
    updates.available(fetch)
    assert len(calls) == 1                              # cached for a few hours
    assert updates.latest(lambda: (_ for _ in ()).throw(OSError("offline")), force=True)["version"] == "0.7.0"  # keeps the last


def test_request(env, monkeypatch):
    monkeypatch.setattr(updates, "latest", lambda *a, **k: {"version": "0.7.0", "notes": NOTES, "deploy_notes": ["x"]})
    with pytest.raises(ValueError):
        updates.request("n0admn", "0.7.0", notes_confirmed=False)       # steps by hand come first
    updates.request("n0admn", "0.7.0", notes_confirmed=True)
    req = json.loads((env / "update-request.json").read_text())
    assert req["by"] == "n0admn" and req["from"] == "0.6.0" and req["to"] == "0.7.0"
    assert "requested" in updates.status()
    with pytest.raises(ValueError):
        updates.request("n0admn", "0.7.0", True)                          # one at a time
    req["at"] -= 3600                                                      # an hour later, nothing picked it up
    (env / "update-request.json").write_text(json.dumps(req))
    assert updates.status()["stuck"]
    assert updates.cancel() and "requested" not in updates.status()
    updates.request("n0admn", "0.7.0", True)
    monkeypatch.setattr(updates, "PREVIEW", "branch")
    (env / "update-request.json").unlink()
    with pytest.raises(ValueError):
        updates.request("n0admn", "0.7.0", True)                          # previews never update themselves


def test_admin_page(env, tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    from club_minutes.club import Club
    from club_minutes.store import Archive
    club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml",
                roster=[{"id": "n0admn", "first": "Ann", "last": "Admin", "callsign": "N0ADMN"}, {"id": "bob", "first": "Bob", "last": "B"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(updates, "latest", lambda *a, **k: {"version": "0.7.0", "notes": NOTES, "url": "u",
                                                              "deploy_notes": updates.deploy_notes(NOTES)})
    c = TestClient(web.app)
    token = a.new_session("n0admn")
    c.cookies.set(web.COOKIE, token)
    assert c.get("/admin/updates", follow_redirects=False).headers["location"].startswith("/admin/confirm")
    page = c.get("/links").text                                                  # admins hear about it outside admin mode too
    assert "Version 0.7.0 of the app is available" in page and "Update to 0.7.0" in page and 'class="dot"' in page
    a.set_admin(token, 60)
    page = c.get("/admin/updates").text
    assert "Version 0.7.0" in page and "<pre><code>cp business.json" in page and "I've done these steps" in page  # formatted
    assert "<strong>Business items are tracked</strong>" in page and "<li>" in page            # the notes are rendered
    assert "Version 0.7.0 is available" in c.get("/links").text                  # the admin banner says so
    assert "needs the steps" in c.post("/admin/updates/run", data={"target": "0.7.0"}).text
    c.post("/admin/updates/run", data={"target": "0.7.0", "notes_done": 1})
    page = c.get("/admin/updates").text
    assert (tmp_path / "update-request.json").exists() and "Waiting to update to 0.7.0" in page
    assert 'id="updating"' in page and "setInterval" in page                    # the page watches for the result itself
    updates.cancel()
    assert 'id="updating"' not in c.get("/admin/updates").text                  # gone once nothing is pending: the poller reloads
    c.post("/admin/updates/run", data={"target": "0.7.0", "notes_done": 1})
    c.cookies.set(web.COOKIE, a.new_session("bob"))
    assert "of the app is available" not in c.get("/links").text                 # not for members
    assert c.post("/admin/updates/run", data={"target": "0.7.0", "notes_done": 1}).status_code == 403


def test_backup_before_update(env, tmp_path, monkeypatch):
    """Back up first (ticked by default when backups are set up): a failed backup means no update."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from club_minutes import backup, web
    from club_minutes.auth import Auth
    from club_minutes.club import Club
    from club_minutes.store import Archive
    club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[{"id": "n0admn", "first": "Ann", "last": "Admin"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(backup, "DATA", tmp_path)
    monkeypatch.setattr(updates, "latest", lambda *a, **k: {"version": "0.7.0", "notes": "", "url": "u", "deploy_notes": ""})
    c = TestClient(web.app)
    token = a.new_session("n0admn")
    a.set_admin(token, 60)
    c.cookies.set(web.COOKIE, token)
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", "a long test passphrase")
    assert 'name="backup_first" value="1" checked' in c.get("/admin/updates").text
    monkeypatch.setattr(backup, "run", lambda club, url="": {"ok": False, "error": "S3: no network"})
    assert "failed, so the update" in c.post("/admin/updates/run", data={"target": "0.7.0", "backup_first": 1}).text
    assert not (tmp_path / "update-request.json").exists()
    ran = []
    monkeypatch.setattr(backup, "run", lambda club, url="": ran.append(1) or {"ok": True})
    c.post("/admin/updates/run", data={"target": "0.7.0", "backup_first": 1})
    assert ran and (tmp_path / "update-request.json").exists()
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", "")
    updates.cancel()
    assert "no backup before the update" in c.get("/admin/updates").text


TWO = __import__("datetime").datetime(2026, 11, 10, 2, 30)


def test_nightly_auto_update(env, tmp_path, monkeypatch):
    """Off by default; on, a newer release without deploy notes is installed after a backup, admins are told the
    outcome; deploy notes or a failed backup stop it (told once), and a release that failed isn't retried."""
    from pathlib import Path

    from club_minutes import backup
    from club_minutes.club import Club
    club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[{"id": "n0admn", "first": "Ann", "last": "Admin", "email": "ann@example.org"}])
    club.admins = {"n0admn"}
    sent, ran = [], []
    monkeypatch.setattr(updates, "_tell_admins", lambda c, subject, body: sent.append(subject))
    monkeypatch.setattr(backup, "run", lambda c, url="": ran.append(1) or {"ok": True, "name": "b.tar.gz.enc"})
    rel = {"version": "0.7.0", "notes": "", "url": "u", "deploy_notes": ""}
    monkeypatch.setattr(updates, "latest", lambda *a, **k: rel)
    assert updates.auto(club, "https://x", now=TWO) == "automatic updates are off" and not ran
    updates.set_auto(True)

    def updater(*a):  # what the root updater does with the request: run it, record the outcome
        req = tmp_path / "update-request.json"
        if req.exists():
            r = json.loads(req.read_text())
            req.unlink()
            (tmp_path / "update-status.json").write_text(json.dumps({**r, "ok": outcome["ok"], "output": "log"}))
    outcome = {"ok": True}
    monkeypatch.setattr(updates.time, "sleep", updater)
    assert updates.auto(club, "https://x", now=TWO, wait=5, poll=0) == "updated to 0.7.0"
    assert ran == [1] and json.loads((tmp_path / "update-status.json").read_text())["by"] == "automatic"
    assert sent == ["updated to 0.7.0"]

    rel = {**rel, "version": "0.8.0"}
    monkeypatch.setattr(updates, "latest", lambda *a, **k: rel)
    outcome["ok"] = False
    assert updates.auto(club, "https://x", now=TWO, wait=5, poll=0) == "failed: 0.8.0" and sent[-1] == "the update to 0.8.0 failed"
    assert updates.auto(club, "https://x", now=TWO, wait=5, poll=0).startswith("0.8.0 failed before") and len(ran) == 2  # not again
    assert updates.auto(club, "https://x", now=TWO.replace(hour=14)).startswith("not now")  # only at the hour set
    rel = {**rel, "version": "0.8.1"}  # a fix for what broke 0.8.0: tried
    monkeypatch.setattr(updates, "latest", lambda *a, **k: rel)
    outcome["ok"] = True
    assert updates.auto(club, "https://x", now=TWO, wait=5, poll=0) == "updated to 0.8.1" and len(ran) == 3
    updates.set_auto(True, 4)
    assert updates.auto(club, "https://x", now=TWO).startswith("not now") and updates.auto_settings()["hour"] == 4
    updates.set_auto(True, 2)

    rel = {**rel, "version": "0.9.0", "deploy_notes": "Add x to club.yaml"}
    monkeypatch.setattr(updates, "latest", lambda *a, **k: rel)
    assert updates.auto(club, "https://x", now=TWO) == "0.9.0 needs steps by hand" and sent[-1] == "version 0.9.0 needs you"
    updates.auto(club, "https://x", now=TWO)
    assert sent.count("version 0.9.0 needs you") == 1 and len(ran) == 3  # told once, nothing installed

    rel = {**rel, "version": "1.0.0", "deploy_notes": ""}
    monkeypatch.setattr(updates, "latest", lambda *a, **k: rel)
    monkeypatch.setattr(backup, "run", lambda c, url="": {"ok": False, "error": "disk full"})
    assert updates.auto(club, "https://x", now=TWO) == "backup failed" and not (tmp_path / "update-request.json").exists()
    monkeypatch.setattr(updates, "PREVIEW", "branch")
    assert updates.auto(club, "https://x", now=TWO) == "automatic updates are off"  # previews never update themselves


def test_auto_update_switch(env, tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    from club_minutes.club import Club
    from club_minutes.store import Archive
    club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[{"id": "n0admn", "first": "Ann", "last": "Admin"}, {"id": "bob", "first": "Bob", "last": "B"}])
    club.admins = {"n0admn"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(updates, "latest", lambda *a, **k: None)
    c = TestClient(web.app)
    token = a.new_session("n0admn")
    c.cookies.set(web.COOKIE, token)
    assert c.post("/admin/updates/auto", data={"on": 1}).status_code == 403  # admin mode first
    a.set_admin(token, 60)
    monkeypatch.delenv("CLUB_MINUTES_BACKUP_PASSPHRASE", raising=False)
    assert "Set up backups first" in c.post("/admin/updates/auto", data={"on": 1}).text and not updates.auto_settings()["on"]
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", "a long test passphrase")
    assert "on, nightly at 3:30" in c.post("/admin/updates/auto", data={"on": 1, "hour": "3"}).text and updates.auto_settings()["on"]
    c.post("/admin/updates/auto", data={"on": 1, "hour": "²"})  # junk keeps the hour
    assert updates.auto_settings()["hour"] == 3
    assert "Turn off" in c.get("/admin/updates").text
    c.post("/admin/updates/auto", data={"on": 0})
    assert not updates.auto_settings()["on"]
    c.cookies.set(web.COOKIE, a.new_session("bob"))
    assert c.post("/admin/updates/auto", data={"on": 1}).status_code == 403  # not for members


def test_several_releases_waiting(env):
    """Going straight to the newest must not skip steps by hand in a release in between."""
    def fetch():
        return [{"tag_name": "v0.8.1", "body": "- **Fix:** the start-up crash"},
                {"tag_name": "v0.8.0", "body": NOTES},
                {"tag_name": "v0.7.0", "body": "- **Something**"},
                {"tag_name": "v0.6.0", "body": "- **Old** (running)"},
                {"tag_name": "v0.9.0-rc1", "body": "", "prerelease": True}]
    rel = updates.latest(fetch, force=True)
    assert rel["version"] == "0.8.1"                                        # the newest, not the pre-release
    assert "0.8.0:** before updating" in rel["deploy_notes"] and "cp business.json" in rel["deploy_notes"]
    assert "### 0.8.1" in rel["notes"] and "### 0.7.0" in rel["notes"] and "Old" not in rel["notes"]
    assert updates.latest()["deploy_notes"] == rel["deploy_notes"]          # the same from the cache
    (env / "update-check.json").write_text(json.dumps({"at": 9e12, "release": {"version": "0.7.0", "notes": "", "url": "u", "deploy_notes": ""}}))
    assert updates.latest()["version"] == "0.7.0"                           # a cache from before this change still reads
