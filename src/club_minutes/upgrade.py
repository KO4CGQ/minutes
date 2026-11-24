"""Automatic data upgrades: the data folder says which shape its files are in (data-version, a number), and the
app brings it up to date by itself at start, so reshaping stored data in a 1.x needs nothing from the club.

Before the first step it copies the data folder to before-upgrade/ (replacing an earlier copy; SQLite files with
its backup API), then writes upgrade-started: a rerun after a crash keeps that clean copy. If the new version
then doesn't start, update.sh puts that copy back (undo) before going back to the old version. An older version
finding data newer than it knows refuses to start, rather than misread it.

Adding a step: append (version, what it does, function(data folder)) to STEPS, with a test that runs it on a data
folder of the old shape. Never change or remove a released step; a step must be safe to run again if it was
stopped part way (the version is written after each one)."""
from __future__ import annotations

import fcntl
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from .club import DATA


def _roles_file(data: Path) -> None:
    """2 (1.0.0): the roster's Minutes editor and Calendar editor ticks become roles.json (Admin → Site → Roles), which
    the app reads from then on. Only with a roster and no roles.json yet; the roster's columns stay, unread."""
    import json

    from .roster import _dicts, to_members
    sheet, out = data / "roster-sheet.json", data / "roles.json"
    if not sheet.exists() or out.exists():
        return
    people = to_members(_dicts(json.loads(sheet.read_text()).get("Member Roster", [])))
    roles = {k: sorted(p["id"] for p in people if p[k]) for k in ("minutes_editor", "calendar_editor")}
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(roles, indent=1))
    tmp.replace(out)


def _v3(data: Path) -> None:
    """3 (1.1.0): nothing to reshape, but 1.1 writes what 1.0 misreads (sign-in columns 1.0's inserts don't expect,
    suspended admins it would ignore, custom roles its Roles save would drop, minutes versions). Marking it makes the
    update keep the copy from before, which a failed update puts back, and 1.0 refuses this data instead."""


def _v4(data: Path) -> None:
    """4 (1.2.0): every published or imported meeting, and each earlier version kept beside it (minutes.vN.md), gets the
    snapshot of what it shows (snapshot.py), made from the club as it stands now: the best there is for minutes made
    before. A file that has one already is left alone. No PDFs here: each is kept the first time it's downloaded.
    1.1 ignores the new `shown:` key, but it can't be told to: the version number makes it refuse this data instead."""
    import yaml

    from . import snapshot
    from .club import Club
    from .meeting import Meeting
    files = sorted((data / "meetings").glob("*/minutes*.md"))
    club = Club() if files else None
    for p in files:
        try:
            m = Meeting.load(p)
        except (yaml.YAMLError, ValueError, KeyError, OSError):  # unreadable: the archive skips it too
            m = None
        if m and m.status != "draft" and not m.shown:
            m.shown = snapshot.take(club, m) | {"layout": 1}  # made before 1.2: drawn as 1.1 drew it (snapshot.View.old)
            m.write(p)


def _v5(data: Path) -> None:
    """5 (1.3.0): the Treasurer's dues are a week, a month or a year at a time: settings.rates.dues_weekly becomes rates.dues with
    period "week" on every step, and settings.arrears_weeks becomes arrears_periods. Same amounts; nothing else changes.
    No file, or no settings in it (the defaults apply): left alone."""
    import json
    import os
    p = data / "treasurer.json"
    if not p.exists():
        return
    s = json.loads(p.read_text())
    st = s.get("settings")
    if not st:
        return
    rates = st.get("rates") or {}
    if "dues_weekly" in rates:
        rates["dues"] = [{**x, "period": "week"} for x in rates.pop("dues_weekly")]
    if "arrears_weeks" in st:
        st["arrears_periods"] = st.pop("arrears_weeks")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def _v6(data: Path) -> None:
    """6 (1.3.0): the roster's new `id` column holds each member's id, which used to be worked out from the callsign on
    every rebuild. Each row gets exactly the id it has today, so no other file changes; from now on a callsign change
    keeps the id. 1.2 would read a changed callsign as a different member, so it must refuse this data. A row that
    has an id is left alone, so running it again is harmless."""
    import json

    from .roster import derived_id
    p = data / "roster-sheet.json"
    if not p.exists():
        return
    tabs = json.loads(p.read_text())
    grid = tabs.get("Member Roster") or []
    if not grid:
        return
    header = [str(h).strip().lower() for h in grid[0]]
    if not {"full_name", "callsign"} <= set(header):  # not a roster this app wrote: leave it, ids are derived as before
        return
    if "id" not in header:
        grid[0] = [*grid[0], "id"]
        header.append("id")
    i, name, call = header.index("id"), header.index("full_name"), header.index("callsign")
    for row in grid[1:]:
        row += [""] * (len(header) - len(row))
        if not str(row[i]).strip() and str(row[name]).strip():
            row[i] = derived_id(str(row[name]), str(row[call]))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(tabs))
    tmp.chmod(0o600)
    tmp.replace(p)


STEPS: list[tuple[int, str, Callable[[Path], None]]] = [
    (2, "the Minutes editor and Calendar editor ticks moved to Admin → Site → Roles (roles.json)", _roles_file),
    (3, "1.1.0's data (custom roles, corrected minutes' versions, suspended admins, new sign-in columns): 1.0 can't read it", _v3),
    (4, "published minutes keep their names, agenda order, heading and links from when they were made (snapshot.py): 1.1 can't tell", _v4),
    (5, "the Treasurer's dues can be a week, a month or a year at a time (rates.dues with a period, arrears_periods): 1.2.0 can't read it", _v5),
    (6, "each member's id is stored in the roster's id column, so a callsign change keeps it: 1.2 would misread a changed callsign", _v6),
]
CURRENT = 1 + len(STEPS)  # 1: the shape of 1.0.0's data (and 0.x's, which is the same)
COPY = "before-upgrade"
SKIP = ("backups", COPY)  # never copied or put back: the backups, and the copy itself
STARTED = "upgrade-started"  # written once the copy is complete, holding its data version; removed when the steps finish


def _copy_file(src: str, dst: str) -> None:
    """A SQLite database is copied with its backup API (a job may hold it open); anything else as is."""
    if not src.endswith(".sqlite"):
        shutil.copy2(src, dst)
        return
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    with b:
        a.backup(b)
    a.close()
    b.close()


def _copytree(src: Path, dst: Path, skip: Callable[[str, list[str]], list[str]]) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=_copy_file,
                    ignore=lambda d, names: [*skip(d, names), *(n for n in names if n.endswith(("-wal", "-shm")))])


def version(data: Path = DATA) -> int:
    p = data / "data-version"
    return int(p.read_text().strip()) if p.exists() else 1


def _set(data: Path, v: int) -> None:
    tmp = data / "data-version.tmp"
    tmp.write_text(f"{v}\n")
    tmp.replace(data / "data-version")


@contextmanager
def _locked(data: Path):
    with open(data / "upgrade.lock", "w") as f:  # the app and a timer starting at once: one upgrades, the other waits
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def run(data: Path = DATA) -> list[str]:
    """Bring the data folder up to CURRENT; returns what was done. Raises RuntimeError if it's newer than this app."""
    data.mkdir(parents=True, exist_ok=True)
    with _locked(data):
        have = version(data)
        if have > CURRENT:
            raise RuntimeError(f"The data folder is from a newer version of the app (data version {have}; this version "
                               f"knows up to {CURRENT}). Install that version again, or restore a backup made with this one.")
        todo = [s for s in STEPS if s[0] > have]
        if todo:
            marker = data / STARTED
            # A run that crashed part way (even after some steps) left the marker: its copy is still the clean one
            if not ((data / COPY).is_dir() and marker.exists()):
                marker.unlink(missing_ok=True)
                shutil.rmtree(data / COPY, ignore_errors=True)
                _copytree(data, data / COPY, lambda d, names: SKIP if Path(d) == data else ())
                _set(data / COPY, have)
                marker.write_text(f"{have}\n")
        for v, _what, step in todo:
            step(data)
            _set(data, v)
        (data / STARTED).unlink(missing_ok=True)
        if not (data / "data-version").exists():
            _set(data, CURRENT)
        return [what for _v, what, _s in todo]


def undo(was: int, data: Path = DATA) -> bool:
    """Put back the copy taken before an upgrade from data version `was` (update.sh, when the new version didn't
    start). Only if the data has moved on from `was` since, so an old copy never replaces newer data."""
    copy = data / COPY
    if not (copy / "data-version").exists() or version(copy) != was:
        return False
    # No data-version means an undo stopped halfway (it goes first, and comes back last): run it again
    if (data / "data-version").exists() and version(data) <= was:
        return False
    with _locked(data):
        (data / "data-version").unlink(missing_ok=True)
        for p in data.iterdir():
            if p.name not in SKIP and not p.name.endswith(".lock"):
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        _copytree(copy, data, lambda d, _names: ["data-version"] if Path(d) == copy else [])
        shutil.copy2(copy / "data-version", data / "data-version")
    return True
