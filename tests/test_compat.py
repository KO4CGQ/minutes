"""Guards for the compatibility rule (docs/developers.md → Compatibility): what a club relies on is still there in
every 1.x. A test here failing means a change would need a 2.0: keep the old name working instead (RENAMED, an alias,
a redirect), don't edit the lists below to make it pass. Adding things is always fine."""
import re
import subprocess
import sys
from pathlib import Path

from club_minutes import upgrade, web
from club_minutes.auth import Auth
from club_minutes.club import RENAMED, SITE_KEYS, Club

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src" / "club_minutes"
FIX = Path(__file__).parent / "fixtures"


def test_a_1_0_0_club_yaml_with_every_setting_still_starts(tmp_path):
    c = Club(FIX / "club-1.0.0.yaml", roster=[])
    _ = (c.meeting_weekday, c.meeting_time, c.timezone, c.sections())
    a = Auth(c, tmp_path / "auth.sqlite")
    assert a.rp_id and a.base_url and c.has("chow") and c.certificate and c.cite("dues")


def _settings_read() -> set[str]:
    """Every club.yaml setting the code reads, as "section: key" (or "key" at the top)."""
    code = "\n".join(p.read_text() for p in SRC.glob("*.py"))
    found = {f"club: {k}" for k in re.findall(r'\binfo\.get\("(\w+)"', code)}
    for sec in ("auth", "email", "ai", "calendar", "bylaws"):
        found |= {f"{sec}: {k}" for k in re.findall(rf'\b{sec}\.get\("(\w+)"', code)}
    configure = (SRC / "club.py").read_text().split("def _configure", 1)[1].split("\n    @", 1)[0]
    found |= set(re.findall(r'cfg\.get\("(\w+)"', configure))
    auth_init = (SRC / "auth.py").read_text().split("cfg = club.auth", 1)[1].split("path =", 1)[0]
    found |= {f"auth: {k}" for k in re.findall(r'cfg\.get\("(\w+)"', auth_init)}
    found |= {f"{s}: {k}" for s, k in SITE_KEYS}
    return found - {"members"}  # inline members: tests and the demo only


def test_club_example_yaml_names_every_setting():
    """A new setting goes in club.example.yaml (commented out if optional), with its default."""
    example = (ROOT / "club.example.yaml").read_text()
    missing = [s for s in sorted(_settings_read()) if not re.search(rf"^[# ]*{s.split(': ')[-1]}:", example, re.MULTILINE)]
    assert not missing, f"not in club.example.yaml: {missing}"


def test_settings_from_1_0_0_are_still_read_or_renamed():
    """Every setting 1.0.0 had is still read, or listed in RENAMED (read under its new name until 2.0)."""
    import yaml
    old = yaml.safe_load((FIX / "club-1.0.0.yaml").read_text())
    had = {f"{s}: {k}" for s, v in old.items() if isinstance(v, dict) for k in v} | {s for s, v in old.items() if not isinstance(v, dict)}
    read = _settings_read() | {s for s, v in old.items() if isinstance(v, dict)} | {"links", "offices", "admins", "roster_keepers"}
    renamed = {f"{s}: {k}" for s, k in RENAMED}
    assert not sorted(had - read - renamed - {"privacy: never_record"}), "a 1.0.0 setting is no longer read"


# What clubs, their members' calendar apps and the Discord bot use, as of 1.0.0.
COMMANDS_1_0 = {"import", "roster", "login-link", "first-member", "delete-meeting", "chow-import", "reminders",
                "auto-update", "ai-ask-about", "backup", "restore", "data-undo", "serve"}
ROUTES_1_0 = {"/login", "/login/link", "/calendar/public", "/calendar/public.ics", "/calendar/feed/{token}.ics",
              "/api/roster", "/meetings/{slug}", "/profile/email", "/admin/backups", "/admin/updates"}
ROSTER_API_FIELDS_1_0 = {"callsign", "name", "email", "officer", "former"}
UNITS_1_0 = {"club-minutes.service", "club-minutes-update.path", "club-minutes-update.service", "club-minutes-backup.timer",
             "club-minutes-backup.service", "club-minutes-reminders.timer", "club-minutes-reminders.service",
             "club-minutes-roster.timer", "club-minutes-roster.service", "club-minutes-autoupdate.timer",
             "club-minutes-autoupdate.service"}
ENV_1_0 = {"CLUB_MINUTES_CONFIG", "CLUB_MINUTES_DATA", "CLUB_MINUTES_BACKUP_PASSPHRASE", "CLUB_MINUTES_GOOGLE_KEY",
           "CLUB_MINUTES_OPENROUTER_KEY", "CLUB_MINUTES_ROSTER_API_TOKEN", "CLUB_MINUTES_ROSTER_API_ALLOW",
           "CLUB_MINUTES_SMTP_HOST", "CLUB_MINUTES_SMTP_PORT", "CLUB_MINUTES_SMTP_USER", "CLUB_MINUTES_SMTP_PASSWORD",
           "CLUB_MINUTES_SMTP_FROM", "CLUB_MINUTES_SMTP_REPLY_TO"}


def test_commands_are_kept():
    out = subprocess.run([sys.executable, "-c", "from club_minutes import main; main()", "--help"],
                         check=True, capture_output=True, text=True).stdout
    have = set(re.search(r"\{([\w,-]+)\}", out)[1].split(","))
    assert not COMMANDS_1_0 - have, f"commands gone: {COMMANDS_1_0 - have}"


def test_addresses_are_kept():
    have = {r.path for r in web.app.routes}
    assert not ROUTES_1_0 - have, f"addresses gone (keep a redirect): {ROUTES_1_0 - have}"


def test_roster_api_fields_are_kept(monkeypatch):
    monkeypatch.setenv("CLUB_MINUTES_ROSTER_API_TOKEN", "t" * 32)
    from fastapi.testclient import TestClient
    rows = TestClient(web.app, client=("127.0.0.1", 5000)).get("/api/roster", headers={"Authorization": "Bearer " + "t" * 32}).json()
    rows = rows.get("members", rows) if isinstance(rows, dict) else rows
    assert rows and not ROSTER_API_FIELDS_1_0 - set(rows[0]), "roster API fields gone"


def test_services_and_environment_are_kept():
    units = {p.name for p in (ROOT / "deploy").iterdir()}
    assert not UNITS_1_0 - units, f"units gone: {UNITS_1_0 - units}"
    code = "\n".join(p.read_text() for p in SRC.glob("*.py"))
    assert not {e for e in ENV_1_0 if e not in code}, "an environment setting is no longer read"


def test_upgrade_steps_are_numbered_in_order():
    """Steps only ever get added at the end: 2, 3, 4…; the data version follows."""
    assert [v for v, _w, _s in upgrade.STEPS] == list(range(2, 2 + len(upgrade.STEPS)))
    assert upgrade.CURRENT == 1 + len(upgrade.STEPS)
