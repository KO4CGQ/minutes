"""Automatic data upgrades (upgrade.py): the data folder's version, steps run once at start, the copy taken before
them, and putting it back when the new version doesn't start."""
import json

import pytest

from club_minutes import backup, upgrade


def _rename_key(data):  # a made-up step: business.json's "items" become "entries"
    p = data / "business.json"
    d = json.loads(p.read_text())
    if "items" in d:
        d["entries"] = d.pop("items")
    p.write_text(json.dumps(d))


@pytest.fixture
def steps(monkeypatch):
    monkeypatch.setattr(upgrade, "STEPS", [(2, "business items renamed", _rename_key)])
    monkeypatch.setattr(upgrade, "CURRENT", 2)


def test_new_or_older_folder_is_marked_with_this_version(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade, "STEPS", [])
    monkeypatch.setattr(upgrade, "CURRENT", 1)
    assert upgrade.version(tmp_path) == 1  # no file: 1.0.0's shape (0.x data is the same)
    assert upgrade.run(tmp_path) == []
    assert (tmp_path / "data-version").read_text() == f"{upgrade.CURRENT}\n"
    assert not (tmp_path / upgrade.COPY).exists()  # nothing to do, nothing copied


def test_step_runs_once_after_a_copy(tmp_path, steps):
    (tmp_path / "business.json").write_text('{"items": [1]}')
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "big.enc").write_text("x")
    assert upgrade.run(tmp_path) == ["business items renamed"]
    assert json.loads((tmp_path / "business.json").read_text()) == {"entries": [1]}
    assert upgrade.version(tmp_path) == 2
    copy = tmp_path / upgrade.COPY
    assert json.loads((copy / "business.json").read_text()) == {"items": [1]} and upgrade.version(copy) == 1
    assert not (copy / "backups").exists()
    assert upgrade.run(tmp_path) == []  # already done


def test_newer_data_stops_the_start(tmp_path):
    (tmp_path / "data-version").write_text("99\n")
    with pytest.raises(RuntimeError, match="newer version"):
        upgrade.run(tmp_path)


def test_undo_puts_back_the_copy_from_this_update_only(tmp_path, steps):
    (tmp_path / "business.json").write_text('{"items": [1]}')
    upgrade.run(tmp_path)
    (tmp_path / "written-after.json").write_text("{}")
    assert not upgrade.undo(2, tmp_path)  # the update started at 2: the copy (from 1) isn't this update's
    assert upgrade.undo(1, tmp_path)
    assert json.loads((tmp_path / "business.json").read_text()) == {"items": [1]}
    assert upgrade.version(tmp_path) == 1 and not (tmp_path / "written-after.json").exists()
    assert not upgrade.undo(1, tmp_path)  # once: the data is at 1 again



def test_undo_stopped_halfway_runs_again(tmp_path, steps, monkeypatch):
    """Expert sweep (1.1 freeze): a crash (a reboot) in the middle of an undo must not leave the data folder half
    empty with the undo refusing to run again."""
    import shutil
    (tmp_path / "business.json").write_text('{"items": [1]}')
    (tmp_path / "links.json").write_text("[]")
    upgrade.run(tmp_path)
    real = shutil.copytree
    monkeypatch.setattr(shutil, "copytree", lambda *a, **k: (_ for _ in ()).throw(OSError("power cut")))
    with pytest.raises(OSError):
        upgrade.undo(1, tmp_path)  # everything deleted, nothing copied back yet
    monkeypatch.setattr(shutil, "copytree", real)
    assert upgrade.undo(1, tmp_path)
    assert json.loads((tmp_path / "business.json").read_text()) == {"items": [1]} and (tmp_path / "links.json").exists()
    assert upgrade.version(tmp_path) == 1

def test_backups_leave_out_the_copy(tmp_path, steps):
    (tmp_path / "business.json").write_text('{"items": []}')
    upgrade.run(tmp_path)
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(backup.archive(tmp_path))) as t:
        names = t.getnames()
    assert "business.json" in names and "data-version" in names
    assert not any(n.startswith(upgrade.COPY) for n in names)


def test_step_2_moves_the_editor_ticks_to_roles_json(tmp_path):
    """The roster's minutes_editor / calendar_editor ticks become roles.json; a roles.json already there is kept."""
    grid = [["callsign", "full_name", "minutes_editor", "calendar_editor"],
            ["N0AAAA", "Jane Doe", "TRUE", ""], ["N0BBBB", "John Doe", "FALSE", "TRUE"], ["", "Pat Doe", "", "yes"]]
    (tmp_path / "roster-sheet.json").write_text(json.dumps({"Member Roster": grid}))
    upgrade._roles_file(tmp_path)
    assert json.loads((tmp_path / "roles.json").read_text()) == {"minutes_editor": ["n0aaaa"], "calendar_editor": ["n0bbbb", "pat-doe"]}
    (tmp_path / "roles.json").write_text('{"minutes_editor": [], "calendar_editor": []}')
    upgrade._roles_file(tmp_path)  # run again (stopped part way): roles.json stays as it is
    assert json.loads((tmp_path / "roles.json").read_text()) == {"minutes_editor": [], "calendar_editor": []}
    empty = tmp_path / "new"
    empty.mkdir()
    upgrade._roles_file(empty)  # a new server, no roster yet: nothing written, the roster's ticks are read until Site saves
    assert not (empty / "roles.json").exists()


def test_step_3_marks_1_1_data_so_a_failed_update_puts_the_copy_back(tmp_path):
    """Data & reliability sweep: 1.1 writes what 1.0 misreads, so it's data version 3: the copy from before is kept
    (and put back by data-undo), and 1.0 (which knows up to 2) refuses it instead of misreading it."""
    (tmp_path / "data-version").write_text("2\n")
    (tmp_path / "admins.json").write_text('["n0call"]')
    assert upgrade.run(tmp_path)[0].startswith("1.1.0's data") and upgrade.version(tmp_path) == upgrade.CURRENT
    assert upgrade.version(tmp_path / upgrade.COPY) == 2 and (tmp_path / upgrade.COPY / "admins.json").exists()
    (tmp_path / "admins-suspended.json").write_text('["n0call"]')  # written by 1.1
    assert upgrade.undo(2, tmp_path) and upgrade.version(tmp_path) == 2 and not (tmp_path / "admins-suspended.json").exists()


def test_a_sqlite_file_is_copied_as_a_valid_database_and_put_back(tmp_path, steps):
    import sqlite3
    (tmp_path / "business.json").write_text("{}")
    db = sqlite3.connect(tmp_path / "auth.sqlite")
    db.execute("create table t (x)")
    db.execute("insert into t values (7)")
    db.commit()
    (tmp_path / "auth.sqlite-wal").write_text("junk")
    upgrade.run(tmp_path)
    db.close()
    copy = tmp_path / upgrade.COPY
    assert not (copy / "auth.sqlite-wal").exists()
    assert sqlite3.connect(copy / "auth.sqlite").execute("select x from t").fetchall() == [(7,)]
    (tmp_path / "auth.sqlite").write_text("ruined")
    assert upgrade.undo(1, tmp_path)
    assert sqlite3.connect(tmp_path / "auth.sqlite").execute("select x from t").fetchall() == [(7,)]


def test_step_5_stores_each_members_id_as_it_is_today_and_nothing_else(tmp_path):
    """The roster gets an id column holding exactly the id worked out until now (lowercase callsign, else a name slug);
    a row that has one keeps it; running again changes nothing."""
    grid = [["callsign", "full_name", "email"], ["N0AAAA", "Jane Doe", "jane@example.org"], ["", "John Q. Doe", ""], []]
    (tmp_path / "roster-sheet.json").write_text(json.dumps({"Member Roster": grid, "Membership Log": [["callsign"]]}))
    (tmp_path / "chow.json").write_text('{"order": ["n0aaaa"]}')
    upgrade._v6(tmp_path)
    tabs = json.loads((tmp_path / "roster-sheet.json").read_text())
    assert tabs["Member Roster"][0] == ["callsign", "full_name", "email", "id"] and tabs["Membership Log"] == [["callsign"]]
    assert [r[3] for r in tabs["Member Roster"][1:3]] == ["n0aaaa", "john-q-doe"]
    from club_minutes.roster import _dicts, to_members
    assert [m["id"] for m in to_members(_dicts(tabs["Member Roster"]))] == ["n0aaaa", "john-q-doe"]
    tabs["Member Roster"][1][3] = "m1x2y3"  # an id once stored is never worked out again
    (tmp_path / "roster-sheet.json").write_text(json.dumps(tabs))
    upgrade._v6(tmp_path)
    assert json.loads((tmp_path / "roster-sheet.json").read_text()) == tabs and (tmp_path / "chow.json").read_text() == '{"order": ["n0aaaa"]}'
    upgrade._v6(tmp_path / "empty")  # no roster yet: nothing to do


def test_step_7_turns_deleted_dates_and_no_meeting_entries_into_cancel_records(tmp_path):
    """A skipped date of a meeting series, and a single "No meeting" entry on a meeting date (its title the reason), become
    cancel records; anything else stays; running again changes nothing."""
    from club_minutes import events
    weekly = {"freq": "weekly", "interval": 1, "until": None}
    evs = []
    meet = events.add(evs, "events", {"title": "Club meeting", "start": "2026-11-05T19:30", "end": None, "repeat": weekly}, "x")
    meet["meeting"], meet["skip"] = True, ["2026-11-12"]
    events.add(evs, "events", {"title": "No meeting (Thanksgiving)", "start": "2026-11-26", "end": None}, "x")
    events.add(evs, "events", {"title": "No meeting for the net", "start": "2026-11-27", "end": None}, "x")  # not a meeting date
    other = events.add(evs, "events", {"title": "Fox hunt", "start": "2026-11-14", "end": None, "repeat": weekly}, "x")
    other["skip"] = ["2026-11-21"]  # not a meeting series: stays a skipped date
    (tmp_path / "events.json").write_text(json.dumps({"events": evs}))
    upgrade._v7(tmp_path)
    got = json.loads((tmp_path / "events.json").read_text())["events"]
    assert [e["title"] for e in got] == ["Club meeting", "No meeting for the net", "Fox hunt"]
    assert got[0]["skip"] == [] and got[0]["changes"] == {"2026-11-12": {"cancelled": True, "reason": ""},
                                                          "2026-11-26": {"cancelled": True, "reason": "Thanksgiving"}}
    assert got[2]["skip"] == ["2026-11-21"]
    upgrade._v7(tmp_path)
    assert json.loads((tmp_path / "events.json").read_text())["events"] == got
    upgrade._v7(tmp_path / "empty")  # no calendar yet: nothing to do
