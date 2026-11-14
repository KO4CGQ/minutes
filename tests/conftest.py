import os
import tempfile
from pathlib import Path

# Importing the web app opens an archive and a login database; keep both away from real data.
os.environ["CLUB_MINUTES_DATA"] = tempfile.mkdtemp(prefix="club-minutes-test-")
os.environ["CLUB_MINUTES_CONFIG"] = str(Path(__file__).parent / "fixtures" / "test-club.yaml")

import pytest


@pytest.fixture(autouse=True)
def fresh_business(tmp_path, monkeypatch):
    """Each test starts with no tracked business items (business.json would otherwise be shared)."""
    from club_minutes import business
    d = tmp_path / "business-data"
    d.mkdir()
    monkeypatch.setattr(business, "DATA", d)


@pytest.fixture(autouse=True)
def fresh_prefs(tmp_path, monkeypatch):
    """Email preferences and the reminders' sent list start empty in each test."""
    from club_minutes import prefs
    d = tmp_path / "prefs-data"
    d.mkdir()
    monkeypatch.setattr(prefs, "DATA", d)


@pytest.fixture(autouse=True)
def fresh_events(tmp_path, monkeypatch):
    """The club calendars (events.json) and the calendar log start empty in each test."""
    from club_minutes import events
    d = tmp_path / "events-data"
    d.mkdir()
    (d / "events-meeting-added").write_text("")  # tests add the weekly meeting themselves when they want it
    monkeypatch.setattr(events, "DATA", d)


@pytest.fixture(autouse=True)
def no_update_check(monkeypatch):
    """Pages for admins in admin mode check GitHub for a new release; never from the tests."""
    from club_minutes import updates
    monkeypatch.setattr(updates, "latest", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def no_console_holds(monkeypatch):
    """Who holds an open meeting's console is kept in memory: each test starts with nobody."""
    from club_minutes import console
    monkeypatch.setattr(console, "_holds", {})
    monkeypatch.setattr(console, "closed_by", {})
