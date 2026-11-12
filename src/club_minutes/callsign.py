"""A member's new callsign (a vanity call, an upgrade with a new call): their id in every record follows.

A member's id is their callsign in lowercase, and it's stored all over the data folder: the chow schedule,
business items, calendars, email choices, the minutes (attendance, leader, report tags), sign-ins and passkeys,
their picture. A change:

1. takes a backup first (so it needs the backup passphrase), kept on the server;
2. writes the new callsign to the roster sheet, adds the old one to aliases (so old links and anything missed
   still find the member: Club.canonical), and adds a "Callsign Change" row to the Membership Log;
3. renames the id in the data folder: current state only. Logs (chow, calendar, profile changes, sign-in
   history apart from the tables below) keep the old id as history, and show the member's name through
   the alias.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import secrets
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from . import backup, profile
from .club import CALLSIGN, DATA, ROSTER_CACHE, Club, inside, one_line, roster_locked
from .membership import LOG_TAB
from .roster import Sheet

log = logging.getLogger("club_minutes.callsign")
# Current-state files that hold member ids (as values, or as keys): renamed. *.jsonl logs are history: left alone.
STATE_FILES = ("chow.json", "business.json", "events.json", "calendar-feeds.json", "ask-about.json", "email-prefs.json",
               "settings.json", "treasurer.json", "admins.json")
AUTH_COLUMNS = {"sessions": ("member", "view_as"), "links": ("member",), "passkeys": ("member",), "challenges": ("member",),
                "email_changes": ("member",), "signins": ("member",), "events": ("member",)}


def _swap(x, old: str, new: str):
    """Every value equal to `old`, and every key equal to it, anywhere in a JSON structure."""
    if isinstance(x, dict):
        return {(new if k == old else k): _swap(v, old, new) for k, v in x.items()}
    if isinstance(x, list):
        return [_swap(v, old, new) for v in x]
    return new if x == old else x


def _write(p: Path, text: str) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text)
    tmp.chmod(p.stat().st_mode & 0o777)
    tmp.replace(p)


def rename_id(old: str, new: str, data: Path | None = None) -> dict[str, int]:
    """Rename a member id throughout the data folder. Returns what changed, per kind."""
    data = data or DATA
    for x in (old, new):  # ids become file names and patterns below: letters, digits and dashes only
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", x):
            raise ValueError(f"{x!r} isn't a member id.")
    done = {"files": 0, "minutes": 0, "signin rows": 0, "pictures": 0}
    for p in [data / f for f in STATE_FILES] + sorted((data / "meetings").glob("*/console.json")):
        if p.exists():
            before = json.loads(p.read_text())
            after = _swap(before, old, new)
            if after != before:
                _write(p, json.dumps(after, indent=1))
                done["files"] += 1
    # Minutes: the front matter's attendance lists and leader, and the report tags. Body text is left alone.
    item = re.compile(rf"(?m)^(\s*-\s+|leader:\s+){re.escape(old)}\s*$")
    tag = re.compile(rf"<!--\s*member:\s*{re.escape(old)}\s*-->")
    for p in sorted((data / "meetings").glob("*/minutes.md")):
        text = p.read_text()
        _, fm, body = text.split("---", 2) if text.startswith("---") else ("", "", text)
        new_text = (f"---{item.sub(lambda m: m.group(1) + new, fm)}---" if fm else "") + tag.sub(f"<!-- member: {new} -->", body)
        if new_text != text:
            _write(p, new_text)
            done["minutes"] += 1
    db = data / "auth.sqlite"
    if db.exists():
        con = sqlite3.connect(db)
        with con:
            tables = {r[0] for r in con.execute("select name from sqlite_master where type = 'table'")}
            for t, cols in AUTH_COLUMNS.items():
                for c in cols if t in tables else ():
                    done["signin rows"] += con.execute(f"update {t} set {c} = ? where {c} = ?", (new, old)).rowcount
        con.close()
    for ext in (".jpg", ".png"):
        p = inside(data / "avatars", f"{old}{ext}")
        if p.exists():
            p.rename(inside(data / "avatars", f"{new}{ext}"))
            done["pictures"] += 1
    return done


@roster_locked
def change(club: Club, mid: str, callsign: str, date: str, by: str, sheet: Sheet | None = None) -> str:
    """Give a member a new callsign. Returns their new id. Raises ValueError, backup.BackupError or
    roster.SheetError before anything is renamed."""
    m = club.members.get(mid)
    if not m:
        raise ValueError("No such member.")
    new_call = callsign.strip().upper()
    if not CALLSIGN.match(new_call):
        raise ValueError(f"{new_call or 'That'} doesn't look like a callsign.")
    new = new_call.lower()
    if new_call == (m.callsign or ""):
        raise ValueError(f"{m.first}'s callsign is already {new_call}.")
    if new in club.members or club.former_ids.get(new, mid) != mid:
        raise ValueError(f"{new_call} belongs to another member, now or before.")
    try:
        date = dt.date.fromisoformat(date).isoformat()
    except ValueError:
        raise ValueError("Give the date of the change.")

    name, blob, _ = backup.make()  # needs the passphrase: no renaming without a way back
    d = DATA / "backups"
    d.mkdir(mode=0o700, exist_ok=True)
    keep = inside(d, name.replace(".tar.gz.enc", f"-before-{new}.tar.gz.enc"))
    keep.write_bytes(blob)
    keep.chmod(0o600)

    # The roster rows as stored: Club._load moves callsign aliases (earlier calls) out of Member.aliases.
    rows = json.loads(ROSTER_CACHE.read_text()) if ROSTER_CACHE.exists() else [asdict(x) for x in club.members.values()]
    row = next(r for r in rows if r["id"] == mid)
    old_call = m.callsign
    aliases = list(row.get("aliases") or []) + ([old_call] if old_call else [])
    sheet = sheet or Sheet()
    sheet.update_member(old_call, m.name, {"callsign": new_call, "aliases": ", ".join(aliases)})
    sheet.add_row({"callsign": new_call, "full_name": m.name, "date": date, "event": "Callsign Change",
                   "note": f"{old_call or '(none)'} to {new_call}"}, tab=LOG_TAB, optional=("note",))

    done = rename_id(mid, new)
    event = {"date": date, "event": "Callsign Change", "note": f"{old_call or '(none)'} to {new_call}"}
    row.update(id=new, callsign=new_call, aliases=aliases, events=list(row.get("events") or []) + [event])
    if ROSTER_CACHE.exists():
        _write(ROSTER_CACHE, json.dumps(rows, indent=1))
    club._load(rows)
    profile._append({"id": secrets.token_hex(6), "at": int(time.time()), "member": new, "by": by,
                     "logged": True, "old": {}, "new": {"callsign": f"{old_call or '(none)'} to {new_call}"}})
    log.info("callsign change %s -> %s by %s (backup %s): %s", one_line(mid), one_line(new), one_line(by), one_line(keep.name), done)
    return new
