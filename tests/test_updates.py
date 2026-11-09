"""Updating from the admin page: the version check, deploy notes, and the request the root updater picks up."""
import json

import pytest

from w8lbz_minutes import updates

NOTES = """- **Business items are tracked** ...
- **Deploy notes:** before updating:
  1. Copy the business list into the data folder:

          cp business.json /var/lib/w8lbz-minutes/

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
        updates.request("kd8adm", "0.7.0", notes_confirmed=False)       # steps by hand come first
    updates.request("kd8adm", "0.7.0", notes_confirmed=True)
    req = json.loads((env / "update-request.json").read_text())
    assert req["by"] == "kd8adm" and req["from"] == "0.6.0" and req["to"] == "0.7.0"
    assert "requested" in updates.status()
    with pytest.raises(ValueError):
        updates.request("kd8adm", "0.7.0", True)                          # one at a time
    req["at"] -= 3600                                                      # an hour later, nothing picked it up
    (env / "update-request.json").write_text(json.dumps(req))
    assert updates.status()["stuck"]
    assert updates.cancel() and "requested" not in updates.status()
    updates.request("kd8adm", "0.7.0", True)
    monkeypatch.setattr(updates, "PREVIEW", "branch")
    (env / "update-request.json").unlink()
    with pytest.raises(ValueError):
        updates.request("kd8adm", "0.7.0", True)                          # previews never update themselves


def test_admin_page(env, tmp_path, monkeypatch):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    from w8lbz_minutes.club import Club
    from w8lbz_minutes.store import Archive
    club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml",
                roster=[{"id": "kd8adm", "first": "Ann", "last": "Admin", "callsign": "KD8ADM"}, {"id": "bob", "first": "Bob", "last": "B"}])
    club.admins = {"kd8adm"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(updates, "latest", lambda *a, **k: {"version": "0.7.0", "notes": NOTES, "url": "u",
                                                              "deploy_notes": updates.deploy_notes(NOTES)})
    c = TestClient(web.app)
    token = a.new_session("kd8adm")
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
    from w8lbz_minutes import backup, web
    from w8lbz_minutes.auth import Auth
    from w8lbz_minutes.club import Club
    from w8lbz_minutes.store import Archive
    club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Ann", "last": "Admin"}])
    club.admins = {"kd8adm"}
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "archive", Archive(root=tmp_path, club=club))
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(backup, "DATA", tmp_path)
    monkeypatch.setattr(updates, "latest", lambda *a, **k: {"version": "0.7.0", "notes": "", "url": "u", "deploy_notes": ""})
    c = TestClient(web.app)
    token = a.new_session("kd8adm")
    a.set_admin(token, 60)
    c.cookies.set(web.COOKIE, token)
    monkeypatch.setenv("W8LBZ_BACKUP_PASSPHRASE", "a long test passphrase")
    assert 'name="backup_first" value="1" checked' in c.get("/admin/updates").text
    monkeypatch.setattr(backup, "run", lambda club, url="": {"ok": False, "error": "S3: no network"})
    assert "failed, so the update" in c.post("/admin/updates/run", data={"target": "0.7.0", "backup_first": 1}).text
    assert not (tmp_path / "update-request.json").exists()
    ran = []
    monkeypatch.setattr(backup, "run", lambda club, url="": ran.append(1) or {"ok": True})
    c.post("/admin/updates/run", data={"target": "0.7.0", "backup_first": 1})
    assert ran and (tmp_path / "update-request.json").exists()
    monkeypatch.setenv("W8LBZ_BACKUP_PASSPHRASE", "")
    updates.cancel()
    assert "no backup before the update" in c.get("/admin/updates").text
