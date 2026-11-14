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
