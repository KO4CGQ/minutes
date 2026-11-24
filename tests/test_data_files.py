"""Checks over the source itself, for kinds of bug that reviews found: data files that miss a rename-id, writes
that can leave half a file, and SQL that breaks when a table gains a column."""
import re
from pathlib import Path

from club_minutes import callsign

SRC = sorted((Path(__file__).parents[1] / "src" / "club_minutes").glob("*.py"))

# JSON files in the data folder that `club-minutes rename-id` (callsign.STATE_FILES; a callsign change no longer renames) doesn't rewrite, and why.
NO_MEMBER_IDS = {
    "backup-status.json": "when the last backup ran",
    "branding.json": "the club's look",
    "limits.json": "the upload limit",
    "links.json": "the club's links",
    "reminders-sent.json": "keys of reminders already sent (an id inside only stops a repeat; dropped after 60 days)",
    "roster.json": "the roster cache: rebuilt from the sheet (it holds the ids, which a callsign change keeps)",
    "roster-sheet.json": "the sheet itself: its id column keeps each id when a callsign changes",
    "preview-sheet.json": "the preview's sheet, as roster-sheet.json",
    "site-settings.json": "club-wide settings",
    "stage-sync-status.json": "when stage last synced",
    "update-check.json": "the latest release on GitHub",
    "update-check-next.json": "the next branch's latest commit on GitHub",
    "update-request.json": "an update asked for (a member id is the asker, gone once it runs)",
    "update-running.json": "that request while the updater runs it",
    "update-settings.json": "update times",
    "meet-rooms.json": "1.3's upgrade only: moved into the meet plugin's file, then deleted",
}


def test_every_data_file_with_member_ids_follows_a_callsign_change():
    """A file of member ids left out of callsign.STATE_FILES keeps the old callsign (a suspension lifted by a rename)."""
    pattern = r'\b[Dd][Aa][Tt][Aa] / "([\w.-]+\.json)"|\b_(?:read|write|path)\("([\w.-]+\.json)"'  # prefs.py: _path("...")
    found = {m for p in SRC for g in re.findall(pattern, p.read_text()) for m in g if m}
    assert found - set(callsign.STATE_FILES) - set(NO_MEMBER_IDS) == set(), "add to callsign.STATE_FILES, or to NO_MEMBER_IDS with a reason"
    assert not set(callsign.STATE_FILES) & set(NO_MEMBER_IDS)


# Writes that aren't a temporary file then .replace(): "not atomic yet", except the locks and markers (nothing to tear).
NOT_ATOMIC = {
    ("auth.py", "p"): "the email log trimmed to its last lines",
    ("console.py", "state_path(archive, slug)"): "a console's saved state",
    ("console.py", "(archive.root / slug / name)"): "a console status file",
    ("backup.py", "(d / name)"): "a backup file being fetched",
    ("backup.py", "p"): "backup-status.json",
    ("events.py", "marker"): "a marker file",
    ("events.py", '(DATA / "events-meeting-added")'): "a marker file",
    ("updates.py", "cache"): "the release check's cache",
    ("calview.py", "p"): "calendar-feeds.json",
    ("web.py", '(DATA / "stage-sync-request")'): "a request file the sync timer reads",
    ("web.py", "f"): "the stage sync's pause marker",
    ("club.py", 'open(DATA / "roster.lock", "w")'): "a lock",
    ("events.py", 'open(DATA / "events.lock", "w")'): "a lock",
    ("business.py", 'open(DATA / "business.lock", "w")'): "a lock",
    ("recovery.py", 'open(DATA / "recovery.lock", "w")'): "a lock",
    ("prefs.py", 'open(DATA / "prefs.lock", "w")'): "a lock",
    ("auth.py", 'open(DATA / "email-log.lock", "w")'): "a lock",
    ("corrections.py", 'open(archive.root / s / ".send.lock", "w")'): "a lock",
    ("treasurer.py", 'open(DATA / "treasurer.lock", "w")'): "a lock",
    ("upgrade.py", 'open(data / "upgrade.lock", "w")'): "a lock",
    ("upgrade.py", "marker"): "a marker file (only that it exists counts; written after the copy is whole)",
}


def test_every_data_file_write_is_atomic_or_listed():
    """A crash mid-write leaves half a file: write to a .tmp path and .replace() it, or be on NOT_ATOMIC with a reason."""
    bad = []
    for p in SRC:
        lines = p.read_text().splitlines()
        for i, line in enumerate(lines):
            m = re.search(r"(\S.*?)\.write_(?:text|bytes)\(", line) or re.search(r'(?:with )?(open\([^)]*, "w"\))', line)
            if not m:
                continue
            what = m.group(1).removeprefix("with ").strip()
            if "tmp" in what and ".replace(" in "\n".join(lines[i + 1:i + 5]):
                continue
            if (p.name, what) not in NOT_ATOMIC:
                bad.append(f"{p.name}:{i + 1} {what}")
    assert not bad, bad


def test_every_sql_insert_names_its_columns():
    """`insert into t values (...)` breaks when a new release adds a column to t: name the columns."""
    bad = [p.name for p in SRC if re.search(r"insert\s+(?:or\s+\w+\s+)?into\s+\w+\s+values", p.read_text(), re.IGNORECASE)]
    assert not bad, bad
