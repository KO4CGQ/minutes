"""Checks over the data folder: what a damaged file does, what a failed save leaves, what grows, what a downgrade reads."""
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import auth, web
from club_minutes.auth import Auth
from club_minutes.club import Club
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "n0call", "first": "Jane", "last": "Doe", "email": "jane@example.org", "role": "President", "officer": True},
          {"id": "w0demo", "first": "John", "last": "Doe", "email": "john@example.org"}]


def use_data(tmp_path, monkeypatch):
    """Every module reads and writes its files in tmp_path."""
    for name, mod in list(sys.modules.items()):
        if name.startswith("club_minutes") and hasattr(mod, "DATA"):
            monkeypatch.setattr(mod, "DATA", tmp_path)


# ---------------- a damaged file breaks no page ----------------

# Files that fail loudly when damaged, and why: each is the club's own records (the next save from "empty" would overwrite
# the damaged file, which an admin can still repair or restore from a backup), or who may do what (never guessed).
SAVED = "the club's records: a save from empty would overwrite it; repair it or restore a backup"
FAIL_LOUDLY = {"chow.json": SAVED, "business.json": SAVED, "events.json": SAVED, "treasurer.json": SAVED,
               "admins.json": "who is an admin: never guessed", "admins-suspended.json": "who is suspended: never guessed",
               "roles.json": "who may do what: never guessed"}
DAMAGEABLE = ("chow.json", "business.json", "events.json", "calendar-feeds.json", "ask-about.json", "email-prefs.json",
              "settings.json", "treasurer.json", "admins.json", "roles.json", "admins-suspended.json", "recovery.json",
              "meet-rooms.json", "site-settings.json", "links.json", "branding.json", "limits.json", "update-check.json",
              "update-request.json", "update-settings.json", "backup-status.json", "stage-sync-status.json",
              "reminders-sent.json", "email-log.jsonl", "chow-log.jsonl", "calendar-log.jsonl", "treasurer-log.jsonl",
              "profile-changes.jsonl", "admin-log.jsonl", "api-log.jsonl", "ai-log.jsonl")
PAGES = ("/", "/calendar", "/chow", "/business", "/roster", "/profile/email", "/profile", "/members/w0demo")


@pytest.mark.parametrize("name", [n for n in DAMAGEABLE if n not in FAIL_LOUDLY])
def test_a_damaged_data_file_breaks_no_page_and_no_email(name, tmp_path, monkeypatch):
    use_data(tmp_path, monkeypatch)
    (tmp_path / name).write_bytes(b'\x00{"torn": [1, \xff')
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "archive", Archive(tmp_path, club))
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, a.new_session("w0demo"))
    for page in PAGES:
        assert c.get(page).status_code == 200, page
    auth.send_email("john@example.org", "Hello", "A body")


# ---------------- every permission is used ----------------

def test_every_permission_is_checked_somewhere():
    """A permission nothing asks about (a role ticks it, and it does nothing) is a dead switch on the Roles page."""
    from club_minutes.club import PERMISSIONS
    src = Path(__file__).parents[1] / "src" / "club_minutes"
    text = "\n".join(p.read_text() for p in [*src.glob("*.py"), *src.glob("templates/*.html")])
    unused = [k for k in PERMISSIONS if not re.search(rf"""\bmay(?:_change)?\(.*["']{k}["']""", text)]
    assert not unused, unused


# ---------------- a failed save leaves nothing half done ----------------

def fail_on(monkeypatch, name):
    """Replacing the file called `name` fails, as a full disk or a crash would, after the temporary file was written."""
    real = Path.replace

    def replace(self, target):
        if Path(target).name == name:
            raise OSError("disk full")
        return real(self, target)
    monkeypatch.setattr(Path, "replace", replace)


def on_disk(folder):
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file() and p.suffix != ".tmp"}


def test_a_failed_site_settings_save_changes_nothing(tmp_path, monkeypatch):
    use_data(tmp_path, monkeypatch)
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    club.save_site_settings({("club", "name"): "First Name"})
    disk, name = on_disk(tmp_path), club.info["name"]
    fail_on(monkeypatch, "site-settings.json")
    with pytest.raises(OSError):
        club.save_site_settings({("club", "name"): "Second Name"})
    assert on_disk(tmp_path) == disk and club.info["name"] == name == "First Name"


def test_a_failed_roles_save_changes_nothing(tmp_path, monkeypatch):
    use_data(tmp_path, monkeypatch)
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    t = club.role_table()
    club.save_roles({**t, "calendar_editor": {**t["calendar_editor"], "members": ["w0demo"]}})
    disk, table, flags = on_disk(tmp_path), club.role_table(), {m.id: m.calendar_editor for m in club.members.values()}
    fail_on(monkeypatch, "roles.json")
    with pytest.raises(OSError):
        club.save_roles({**table, "calendar_editor": {**table["calendar_editor"], "members": []}})
    assert on_disk(tmp_path) == disk and club.role_table() == table
    assert {m.id: m.calendar_editor for m in club.members.values()} == flags


def test_a_failed_settings_save_changes_nothing(tmp_path, monkeypatch):
    from club_minutes import prefs
    use_data(tmp_path, monkeypatch)
    prefs.save_settings("w0demo", {"week_start": "monday"})
    disk = on_disk(tmp_path)
    fail_on(monkeypatch, "settings.json")
    with pytest.raises(OSError):
        prefs.save_settings("w0demo", {"week_start": "sunday"})
    assert on_disk(tmp_path) == disk and prefs.settings("w0demo")["week_start"] == "monday"


def test_a_failed_roster_save_changes_nothing(tmp_path, monkeypatch):
    """The sheet is written first: when that fails, the cache, the running app and the change log stay as they were."""
    from test_roster_edit import PEOPLE as ROSTER

    from club_minutes import membership, profile, roster
    cache = tmp_path / "roster.json"
    cache.write_text(json.dumps(ROSTER))
    for mod, attr in ((roster, "CACHE"), (membership, "ROSTER_CACHE"), (profile, "ROSTER_CACHE")):
        monkeypatch.setattr(mod, attr, cache)
    monkeypatch.setattr(roster, "PREVIEW", "test")
    use_data(tmp_path, monkeypatch)
    club = Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text()))
    monkeypatch.setattr(profile.auth, "send_email", lambda *a, **k: 0)
    profile.change(club, "n0bbbb", {"phone": "419-555-0100"}, by="n0aaaa")  # the sheet file exists now
    disk, members = on_disk(tmp_path), {i: asdict(m) for i, m in club.members.items()}
    fail_on(monkeypatch, "preview-sheet.json")
    with pytest.raises(OSError):
        profile.change(club, "n0bbbb", {"phone": "419-555-0101"}, by="n0aaaa")
    assert on_disk(tmp_path) == disk and {i: asdict(m) for i, m in club.members.items()} == members


# ---------------- what grows is pruned ----------------

STALE = {  # one row each that is long gone
    "sessions": ["insert into sessions (token_hash, member, created, expires) values ('old', 'w0demo', 1, 2)"],
    "links": ["insert into links (token_hash, member, expires, created) values ('old', 'w0demo', 2, 1)"],
    "email_changes": ["insert into email_changes (token_hash, member, email, expires, created) values ('old', 'w0demo', 'a@example.org', 2, 1)"],
    "challenges": ["insert into challenges (id, challenge, member, kind, expires) values ('old', x'00', 'w0demo', 'login', 2)"],
    "signins": ["insert into signins (at, member) values (1, 'w0demo')"],
    "events": ["insert into events (at, kind) values (1, 'probe')"],
    "bans": ["insert into bans (ip, at, until) values ('203.0.113.9', 1, 2)"],
}
KEPT = {"passkeys": "a member's own devices: only they add and remove them"}
LIVE = ["insert into sessions (token_hash, member, created, expires) values ('live', 'w0demo', 1, 9999999999)",
        "insert into bans (ip, at, until) values ('203.0.113.10', 1, null)"]  # signed in now; banned with no end


def test_every_login_table_is_pruned_or_listed(tmp_path):
    a = Auth(Club(FIX / "test-club.yaml", roster=PEOPLE), tmp_path / "auth.sqlite")
    tables = {r[0] for r in a.db.execute("select name from sqlite_master where type = 'table'")}
    assert tables == set(STALE) | set(KEPT), "a new table: prune it in Auth.prune (and add a stale row here), or list it with a reason"
    for sql in [s for rows in STALE.values() for s in rows] + LIVE:
        a.db.execute(sql)
    a.prune()
    left = {t: a.db.execute(f"select count(*) from {t}").fetchone()[0] for t in STALE}
    assert left == {"sessions": 1, "links": 0, "email_changes": 0, "challenges": 0, "signins": 1, "events": 0, "bans": 1}  # signins: the latest stays


# Logs that only grow, and why that's fine; email-log.jsonl trims itself (tests/test_profile.py).
HISTORY = {"admin-log.jsonl": "a line per admin action", "ai-log.jsonl": "a line per AI request, off unless AI is set up",
           "api-log.jsonl": "a line per call of the off-by-default API", "calendar-log.jsonl": "a line per calendar change",
           "chow-log.jsonl": "a line per chow change", "profile-changes.jsonl": "a line per roster change, the undo history",
           "treasurer-log.jsonl": "the treasurer's audit trail", "preview-outbox.jsonl": "a preview's fake outbox, never a real club"}
TRIMS = {"email-log.jsonl"}


def test_every_growing_log_is_trimmed_or_listed():
    src = Path(__file__).parents[1] / "src" / "club_minutes"
    found = set(re.findall(r'"([\w-]+\.jsonl)"', "\n".join(p.read_text() for p in src.glob("*.py"))))
    assert found == set(HISTORY) | TRIMS, "a new log: trim it, or list it with why it stays small"


def test_the_reminders_list_forgets_old_keys(tmp_path, monkeypatch):
    import datetime as dt

    from club_minutes import prefs
    use_data(tmp_path, monkeypatch)
    today = dt.date(2026, 11, 16)
    prefs.mark_sent(["chow:2026-01-01:w0demo:3"], dt.date(2026, 1, 1))
    prefs.mark_sent(["chow:2026-11-16:w0demo:3"], today)
    assert prefs.unsent(["chow:2026-01-01:w0demo:3"]) and not prefs.unsent(["chow:2026-11-16:w0demo:3"])


# ---------------- a downgrade reads what this version wrote, or is refused ----------------

# What the last release (1.0.1, data version 2) wrote: its files, and each login table's columns. Anything not here
# is new since, and 1.0.1 can't read it: it needs a data-version step (upgrade.STEPS) so 1.0.1 refuses the data.
RELEASED_VERSION = 2
RELEASED_FILES = {"admin-log.jsonl", "admins.json", "ai-log.jsonl", "api-log.jsonl", "ask-about.json", "auth.sqlite",
                  "backup-settings.json", "backup-status.json", "branding.json", "business.json", "business.lock",
                  "calendar-feeds.json", "calendar-log.jsonl", "chow.json", "chow-log.jsonl", "console.json", "emailed.json",
                  "email-log.jsonl", "email-prefs.json", "events.json", "events.lock", "google-service-account.json",
                  "limits.json", "links.json", "preview-outbox.jsonl", "preview-sheet.json", "profile-changes.jsonl",
                  "reminders-sent.json", "roles.json", "roster.json", "roster.lock", "roster-sheet.json", "settings.json",
                  "site-settings.json", "stage-sync-status.json", "treasurer.json", "treasurer.lock", "treasurer-log.jsonl",
                  "update-check.json", "update-request.json", "update-running.json", "update-settings.json",
                  "update-status.json", "upgrade.lock"}
# 1.0.1's Admin → Site settings (club.SITE_KEYS), all in site-settings.json: a key added since (Site → Video's, say)
# is in a file 1.0.1 reads, so it too needs the data-version step that makes 1.0.1 refuse the data.
RELEASED_SITE_KEYS = {("club", k) for k in ("date_formats", "name", "callsign", "meeting_day", "meeting_time", "timezone",
                                             "invite_after_visits", "remind_days_before", "public_calendars")} | {
    ("meeting", "sections"), ("modules", "chow"), ("calendar", "holidays"), ("calendar", "extra_holidays"), ("calendar", "elections"),
    ("bylaws", "refs"), ("bylaws", "arrears_note"), ("treasurer", "certificate"), ("privacy", "never_record"),
    ("", "offices"), ("", "roster_keepers")}
RELEASED_COLUMNS = {
    "sessions": {"token_hash", "member", "created", "expires", "agent", "admin_until", "view_as", "ip", "method"},
    "links": {"token_hash", "member", "expires", "next", "used", "created"},
    "passkeys": {"id", "member", "public_key", "sign_count", "transports", "label", "created", "last_used", "aaguid"},
    "email_changes": {"token_hash", "member", "email", "expires", "created"},
    "challenges": {"id", "challenge", "member", "kind", "expires"},
    "signins": {"at", "member", "method", "ip", "agent"},
    "events": {"at", "ip", "kind", "member", "detail", "agent"},
    "bans": {"ip", "at", "until", "reason", "by"}}


def test_new_data_files_and_columns_come_with_a_data_version_step(tmp_path):
    from club_minutes import upgrade
    src = Path(__file__).parents[1] / "src" / "club_minutes"
    files = set(re.findall(r'"([a-z0-9-]+\.(?:json|jsonl|lock|sqlite))"', "\n".join(p.read_text() for p in src.glob("*.py"))))
    a = Auth(Club(FIX / "test-club.yaml", roster=PEOPLE), tmp_path / "auth.sqlite")
    columns = {t: {r["name"] for r in a.db.execute(f"pragma table_info({t})")} for t in RELEASED_COLUMNS}
    new = {t: sorted(c - RELEASED_COLUMNS[t]) for t, c in columns.items() if c - RELEASED_COLUMNS[t]}
    from club_minutes.club import SITE_KEYS
    if SITE_KEYS - RELEASED_SITE_KEYS:
        assert upgrade.CURRENT > RELEASED_VERSION, f"new Site settings {sorted(SITE_KEYS - RELEASED_SITE_KEYS)}: add a step to upgrade.STEPS"
    if files - RELEASED_FILES or new:
        assert upgrade.CURRENT > RELEASED_VERSION, (
            f"new since the last release: files {sorted(files - RELEASED_FILES)}, columns {new}: add a step to "
            "upgrade.STEPS (the last release then refuses this data instead of misreading it)")
