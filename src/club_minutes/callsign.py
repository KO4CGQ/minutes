"""A member's new callsign (a vanity call, an upgrade with a new call).

A member's id is stored in the roster's id column and never changes, so a new callsign touches nothing else: `change`
writes it to the roster sheet, adds the old one to aliases (so old links and club.yaml still find the member:
Club.canonical), and adds a "Callsign Change" row to the Membership Log.

`rename_id` is for the old way, when the id was the callsign: it finishes a rename that stopped partway (the
`rename-id` command) by renaming the id in the data folder's current state. Logs keep the old id as history.
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

from . import profile
from .club import CALLSIGN, DATA, ROSTER_CACHE, Club, inside, one_line, roster_locked
from .meeting import split_front
from .membership import LOG_TAB
from .roster import Sheet

log = logging.getLogger("club_minutes.callsign")
# Current-state files that hold member ids (as values, or as keys): renamed. *.jsonl logs are history: left alone.
STATE_FILES = ("chow.json", "business.json", "events.json", "calendar-feeds.json", "ask-about.json", "email-prefs.json",
               "settings.json", "treasurer.json", "admins.json", "roles.json", "admins-suspended.json", "recovery.json",
               "removed.json", "purge-hold.json", "applications.json")
AUTH_COLUMNS = {"sessions": ("member", "view_as"), "links": ("member",), "passkeys": ("member",), "challenges": ("member",),
                "email_changes": ("member", "by"), "signins": ("member",), "events": ("member",),
                "known_browsers": ("member",)}


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
    # Minutes: the front matter's attendance lists, leader, recorder, correction and review names, and the report tags. Body text is left alone.
    item = re.compile(rf"(?m)^(\s*-\s+|leader:\s+|recorder:\s+|\s*(?:-\s+)?by:\s+){re.escape(old)}\s*$")
    tag = re.compile(rf"<!--\s*member:\s*{re.escape(old)}\s*-->")
    for p in sorted((data / "meetings").glob("*/minutes*.md")):  # and the earlier versions, minutes.vN.md
        text = p.read_text()
        _, fm, body = split_front(text) if text.startswith("---") else ("", "", text)
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
def change(club: Club, mid: str, callsign: str, date: str, by: str, sheet: Sheet | None = None) -> None:
    """Give a member a new callsign. Their id stays as it is, so no record changes. Raises ValueError or roster.SheetError."""
    m = club.members.get(mid)
    if not m:
        raise ValueError("No such member.")
    new_call = callsign.strip().upper()
    if not CALLSIGN.match(new_call):
        raise ValueError(f"{new_call or 'That'} doesn't look like a callsign.")
    if new_call == (m.callsign or ""):
        raise ValueError(f"{m.first}'s callsign is already {new_call}.")
    if club.resolve(new_call) not in (None, mid):
        raise ValueError(f"{new_call} belongs to another member, now or before.")
    if club.admin_listed(new_call) and club.resolve(new_call) != mid:  # a former admin's call would make the holder an admin
        raise ValueError("That callsign is on the admins list: an admin must remove it there first.")
    try:
        date = dt.date.fromisoformat(date).isoformat()
    except ValueError:
        raise ValueError("Give the date of the change.")

    # The roster rows as stored: Club._load moves callsign aliases (earlier calls) out of Member.aliases.
    rows = json.loads(ROSTER_CACHE.read_text()) if ROSTER_CACHE.exists() else [asdict(x) for x in club.members.values()]
    row = next(r for r in rows if r["id"] == mid)
    old_call = m.callsign
    aliases = list(row.get("aliases") or []) + ([old_call] if old_call else [])
    sheet = sheet or Sheet()
    sheet.update_member(m.id, {"callsign": new_call, "aliases": ", ".join(aliases)})
    sheet.add_row({"callsign": new_call, "full_name": m.name, "date": date, "event": "Callsign Change",
                   "note": f"{old_call or '(none)'} to {new_call}"}, tab=LOG_TAB, optional=("note",))
    event = {"date": date, "event": "Callsign Change", "note": f"{old_call or '(none)'} to {new_call}"}
    row.update(callsign=new_call, aliases=aliases, events=list(row.get("events") or []) + [event])
    if ROSTER_CACHE.exists():
        _write(ROSTER_CACHE, json.dumps(rows, indent=1))
    club._load(rows)
    profile._append({"id": secrets.token_hex(6), "at": int(time.time()), "member": mid, "by": by,
                     "logged": True, "old": {}, "new": {"callsign": f"{old_call or '(none)'} to {new_call}"}})
    log.info("callsign change %s: %s -> %s by %s", one_line(mid), one_line(old_call or ""), one_line(new_call), one_line(by))
