"""The roster moved into the app: imported once from the Google Sheet, then read and written locally."""
import json
import stat

import pytest

from w8lbz_minutes import roster

ROSTER = [["callsign", "full_name", "email", "Title(s)", "is_officer", "Status"],
          ["KD8AAA", "Amy Able", "amy@example.org", "Secretary", True, "Active"],
          ["KD8BBB", "Bob Baker", "bob@example.org", "", False, "Active"]]
LOG = [["callsign", "full_name", "date", "event", "note"], ["KD8AAA", "Amy Able", 43895, "Joined", ""]]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA", tmp_path)
    monkeypatch.setattr(roster, "CACHE", tmp_path / "roster.json")
    monkeypatch.setattr(roster, "PREVIEW", None)
    cfg = {"sheet_id": "x"}
    monkeypatch.setattr(roster, "_cfg", lambda: cfg)
    monkeypatch.setattr(roster, "_google_values", lambda tab=None: {None: ROSTER, "Membership Log": LOG}[tab])
    return tmp_path, cfg


def test_import_then_local_reads_and_writes(env):
    tmp, cfg = env
    before = roster.sync()  # from the "Google Sheet"
    assert roster.import_sheet() == []  # same people, same details
    p = tmp / "roster-sheet.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    cfg["source"] = "app"
    roster._google_values = None  # nothing may reach Google now
    assert roster.sync() == before
    roster.Sheet().update_member("KD8BBB", "Bob Baker", {"email": "bobby@example.org"})
    assert next(m for m in roster.sync() if m["id"] == "kd8bbb")["email"] == "bobby@example.org"
    with pytest.raises(SystemExit):
        roster.import_sheet()  # never twice


@pytest.mark.usefixtures("env")
def test_import_reports_differences():
    roster.CACHE.write_text(json.dumps([{"id": "kd8aaa", "first": "Amy", "last": "Able", "email": "old@example.org"},
                                        {"id": "kd8ccc", "first": "Cal", "last": "Carter"}]))
    diff = roster.import_sheet()
    assert any(d.startswith("kd8aaa:") and "email" in d for d in diff)
    assert "kd8bbb: only in the sheet" in diff and "kd8ccc: only in roster.json" in diff


def test_missing_local_copy_is_an_error(env):
    _, cfg = env
    cfg["source"] = "app"
    with pytest.raises(roster.SheetError):
        roster.sync()
