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


def _v7(data: Path) -> None:
    """7 (1.3.0): a meeting's cancelled night is a record on its series (meetings.py): a date deleted from a meeting series
    (its `skip` list) and a single Club events entry titled "No meeting…" on a meeting date become `changes[date] =
    {"cancelled": True, "reason": …}` (the entry's title after "No meeting" is the reason, and the entry goes); the
    app reads nothing else as a cancelled night from now on. Anything else stays. Run again, it finds nothing to do.
    1.2 would show the cancelled nights again, so it must refuse this data."""
    import datetime as dt
    import json
    import re

    from . import events
    p = data / "events.json"
    if not p.exists():
        return
    evs = json.loads(p.read_text())["events"]
    series = [e for e in evs if e.get("meeting")]
    for e in series:
        e.setdefault("changes", {})
        for d in e.get("skip", []):
            e["changes"][d] = {"cancelled": True, "reason": ""}
        e["skip"] = []
    gone = []
    for x in evs:
        if x.get("meeting") or x.get("cal") != "events" or x.get("repeat") or not x["title"].strip().lower().startswith("no meeting"):
            continue
        day = dt.datetime.fromisoformat(x["start"]).date()
        hit = False
        for e in series:
            pad = events.reach(e)
            for d in events.dates(e, day - pad, day + pad):
                if dt.datetime.fromisoformat(events.occurrence(e, d)["start"]).date() == day and not e["changes"].get(d.isoformat(), {}).get("cancelled"):
                    e["changes"][d.isoformat()] = {"cancelled": True, "reason": re.sub(r"^no meeting\W*", "", x["title"].strip(), flags=re.IGNORECASE).strip(" ()")}
                    hit = True
        gone += [x] if hit else []
    evs = [x for x in evs if not any(x is g for g in gone)]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"events": evs}, indent=1))
    tmp.replace(p)


def _save(p: Path, obj, mode: int | None = None) -> None:
    import json
    import os
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    if mode:
        os.chmod(tmp, mode)
    tmp.replace(p)


def _v8(data: Path) -> None:
    """8 (1.3.0): video meetings (Jitsi) leave the app for the "meet" plugin. Its settings (club.yaml's and Site's
    jitsi: block: domain, app_id, room, rooms, adhoc), the rooms members opened (meet-rooms.json) and the secret the
    app made (jitsi-secret) go into the plugin's storage, plugins/meet.json; `modules: jitsi` becomes the plugin
    ticked on; the meet_host permission becomes meet.host (the built-in Officer and Meeting leader had it by default,
    so they are written out with it). club.yaml is root's and stays as it is: the app no longer reads its jitsi: block.
    Nothing is done for a club that never set Jitsi up. Safe to run again: it starts from what the plugin file holds
    and takes what's left of the old places. THIS STEP EXISTS IN 1.3 ONLY: in the release after, empty this function
    (keep its STEPS entry: CURRENT counts STEPS, so the data version stays)."""
    import json
    import os

    import yaml

    from .club import ROOT
    cfg = Path(os.environ.get("CLUB_MINUTES_CONFIG", ROOT / "club.yaml"))
    yml = (yaml.safe_load(cfg.read_text()) if cfg.exists() else None) or {}
    sp, rp, op = data / "site-settings.json", data / "meet-rooms.json", data / "jitsi-secret"
    site = json.loads(sp.read_text()) if sp.exists() else {}
    block = {**(yml.get("jitsi") or {}), **(site.get("jitsi") or {})}
    on = bool({**(yml.get("modules") or {}), **(site.get("modules") or {})}.get("jitsi"))
    if not (on or block or rp.exists() or op.exists()):
        return
    out = data / "plugins" / "meet.json"
    plugin = json.loads(out.read_text()) if out.exists() else {}
    plugin |= {k: block[k] for k in ("domain", "app_id", "room", "rooms", "adhoc") if block.get(k) is not None}
    if rp.exists():
        plugin["openers"] = json.loads(rp.read_text() or "{}")
    if op.exists() and op.read_text().strip():
        plugin |= {"secret": op.read_text().strip(), "secret_made": op.stat().st_mtime}
    out.parent.mkdir(exist_ok=True)
    _save(out, plugin, 0o600)  # the plugin's file first: what's left of the old places is only removed after it
    rj = json.loads((data / "roles.json").read_text()) if (data / "roles.json").exists() else {}
    table = rj.setdefault("table", {})
    for k, default in (("officer", ("lead", "calendar", "chow", "contacts", "oversee", "moderate", "treasurer")),
                       ("minutes_editor", ("lead", "calendar"))):
        if "perms" not in table.get(k, {}):
            table.setdefault(k, {})["perms"] = list(default) + ["meet_host"]  # as shipped, so with it
    for t in table.values():
        t["perms"] = ["meet.host" if x == "meet_host" else x for x in t.get("perms", [])]
    _save(data / "roles.json", rj)
    site.pop("jitsi", None)
    mods = {k: v for k, v in (site.get("modules") or {}).items() if k != "jitsi"}
    site = {k: v for k, v in site.items() if k != "modules"} | ({"modules": mods} if mods else {})
    if on:
        site.setdefault("plugins", {})["meet"] = True
    _save(sp, site)
    rp.unlink(missing_ok=True)
    op.unlink(missing_ok=True)


STEPS: list[tuple[int, str, Callable[[Path], None]]] = [
    (2, "the Minutes editor and Calendar editor ticks moved to Admin → Site → Roles (roles.json)", _roles_file),
    (3, "1.1.0's data (custom roles, corrected minutes' versions, suspended admins, new sign-in columns): 1.0 can't read it", _v3),
    (4, "published minutes keep their names, agenda order, heading and links from when they were made (snapshot.py): 1.1 can't tell", _v4),
    (5, "the Treasurer's dues can be a week, a month or a year at a time (rates.dues with a period, arrears_periods): 1.2.0 can't read it", _v5),
    (6, "each member's id is stored in the roster's id column, so a callsign change keeps it: 1.2 would misread a changed callsign", _v6),
    (7, "a cancelled or rescheduled meeting is a record on its calendar series, not a deleted date or a \"No meeting\" entry: 1.2 can't read it", _v7),
    (8, "video meetings moved to the meet plugin (plugins/meet.json, the plugin ticked on, meet.host): 1.2 can't read it", _v8),
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
        # Copy to a folder inside the data folder first (the data stays as it was if that fails), then swap by renames
        new, old = data / ".undo-new", data / ".undo-old"
        for d in (new, old):
            shutil.rmtree(d, ignore_errors=True)
        _copytree(copy, new, lambda d, _names: ["data-version"] if Path(d) == copy else [])
        (data / "data-version").unlink(missing_ok=True)  # gone until the end: a stopped undo runs again
        old.mkdir()
        for p in list(data.iterdir()):
            if p.name not in SKIP and p not in (new, old) and not p.name.endswith(".lock"):
                p.replace(old / p.name)
        for p in new.iterdir():
            p.replace(data / p.name)
        shutil.copy2(copy / "data-version", data / "data-version")
        shutil.rmtree(new)
        shutil.rmtree(old)
    return True
