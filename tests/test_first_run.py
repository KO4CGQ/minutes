"""A new club's first run: an empty server, the first member (an admin) and their way in."""
import json
import os
import subprocess
import sys

CONFIG = """club: {name: Example Radio Club, callsign: W0XYZ, meeting_day: Tuesday, meeting_time: "19:00", timezone: America/Denver}
auth: {rp_id: minutes.example.org}
admins: [KD8XYZ]
"""


def run(tmp_path, *args):
    env = {**os.environ, "CLUB_MINUTES_DATA": str(tmp_path / "data"), "CLUB_MINUTES_CONFIG": str(tmp_path / "club.yaml"),
           "CLUB_MINUTES_ROOT": str(tmp_path)}
    return subprocess.run([sys.executable, "-c", "from club_minutes import main; main()", *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_first_member_starts_the_roster_once_and_prints_their_way_in(tmp_path):
    (tmp_path / "club.yaml").write_text(CONFIG)
    (tmp_path / "data").mkdir()
    r = run(tmp_path, "login-link", "KD8XYZ")
    assert "No active member" in r.stderr  # the gap this closes: an empty server had no way in
    r = run(tmp_path, "first-member", "kd8xyz", "--first", "Amy", "--last", "Able", "--email", "amy@example.org", "--class", "Extra")
    assert "Unknown license class: use one of Technician, General, Amateur Extra." in r.stderr
    assert not (tmp_path / "data" / "roster-sheet.json").exists()  # a mistake leaves nothing behind: fix it and run again
    r = run(tmp_path, "first-member", "kd8xyz", "--first", "Amy", "--last", "Able", "--email", "amy@example.org",
            "--class", "Amateur Extra", "--base-url", "https://minutes.example.org")
    assert r.returncode == 0 and "Started the roster with Amy Able (KD8XYZ)" in r.stdout and "isn't under admins" not in r.stdout
    assert "https://minutes.example.org/login/link?t=" in r.stdout
    sheet = json.loads((tmp_path / "data" / "roster-sheet.json").read_text())
    assert sheet["Member Roster"][1][:3] == ["KD8XYZ", "Amy Able", "amy@example.org"] and sheet["Membership Log"][1][3:] == ["Joined", "The roster's first member, added on the server"]
    assert run(tmp_path, "login-link", "KD8XYZ").returncode == 0
    r = run(tmp_path, "first-member", "KD8BBB", "--first", "Bob", "--last", "Baker", "--email", "bob@example.org", "--class", "General")
    assert "There's already a roster" in r.stderr and len(json.loads((tmp_path / "data" / "roster-sheet.json").read_text())["Member Roster"]) == 2


def test_first_member_not_in_admins_is_told_how_to_fix_it(tmp_path):
    (tmp_path / "club.yaml").write_text(CONFIG.replace("[KD8XYZ]", "[]"))
    r = run(tmp_path, "first-member", "KD8XYZ", "--first", "Amy", "--last", "Able", "--email", "amy@example.org", "--class", "General")
    assert r.returncode == 0 and "isn't under admins: in club.yaml" in r.stdout


def test_without_email_settings_a_page_never_claims_it_emailed(monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import auth, web
    monkeypatch.setattr(auth, "PREVIEW", None)
    monkeypatch.delenv("CLUB_MINUTES_SMTP_USER", raising=False)
    page = TestClient(web.app).get("/login?sent=1").text
    assert "can't send email yet" in page and "journalctl -u club-minutes" in page and "on its way" not in page
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "x")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "y")
    assert "on its way" in TestClient(web.app).get("/login?sent=1").text
