"""The roster lives in the app (roster-sheet.json): read and written locally, never through Google."""
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
    return tmp_path


def test_reads_and_writes_the_local_roster(env):
    roster.LocalSheet(env / "roster-sheet.json").save({"Member Roster": ROSTER, "Membership Log": LOG})
    ms = roster.sync()
    assert [m["id"] for m in ms] == ["kd8aaa", "kd8bbb"] and ms[0]["officer"] and ms[0]["member_since"] == "2020-03-05"
    roster.Sheet().update_member("KD8BBB", "Bob Baker", {"email": "bobby@example.org"})
    assert next(m for m in roster.sync() if m["id"] == "kd8bbb")["email"] == "bobby@example.org"
    assert stat.S_IMODE((env / "roster-sheet.json").stat().st_mode) == 0o600  # the only copy: the app's alone


def test_missing_roster_is_an_error(env):
    with pytest.raises(roster.SheetError, match="restore it from a backup"):
        roster.sync()
