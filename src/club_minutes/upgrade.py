"""Automatic data upgrades: the data folder says which shape its files are in (data-version, a number), and the
app brings it up to date by itself at start, so reshaping stored data in a 1.x needs nothing from the club.

Before the first step it copies the data folder to before-upgrade/ (replacing an earlier copy). If the new version
then doesn't start, update.sh puts that copy back (undo) before going back to the old version. An older version
finding data newer than it knows refuses to start, rather than misread it.

Adding a step: append (version, what it does, function(data folder)) to STEPS, with a test that runs it on a data
folder of the old shape. Never change or remove a released step; a step must be safe to run again if it was
stopped part way (the version is written after each one)."""
from __future__ import annotations

import fcntl
import shutil
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


STEPS: list[tuple[int, str, Callable[[Path], None]]] = [
    (2, "the Minutes editor and Calendar editor ticks moved to Admin → Site → Roles (roles.json)", _roles_file),
]
CURRENT = 1 + len(STEPS)  # 1: the shape of 1.0.0's data (and 0.x's, which is the same)
COPY = "before-upgrade"
SKIP = ("backups", COPY)  # never copied or put back: the backups, and the copy itself


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
            shutil.rmtree(data / COPY, ignore_errors=True)
            shutil.copytree(data, data / COPY, ignore=lambda d, names: SKIP if Path(d) == data else ())
            _set(data / COPY, have)
        for v, _what, step in todo:
            step(data)
            _set(data, v)
        if not (data / "data-version").exists():
            _set(data, CURRENT)
        return [what for _v, what, _s in todo]


def undo(was: int, data: Path = DATA) -> bool:
    """Put back the copy taken before an upgrade from data version `was` (update.sh, when the new version didn't
    start). Only if the data has moved on from `was` since, so an old copy never replaces newer data."""
    copy = data / COPY
    if not (copy / "data-version").exists() or version(copy) != was or version(data) <= was:
        return False
    with _locked(data):
        for p in data.iterdir():
            if p.name not in SKIP and not p.name.endswith(".lock"):
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        shutil.copytree(copy, data, dirs_exist_ok=True)
    return True
