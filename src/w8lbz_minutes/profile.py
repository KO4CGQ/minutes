"""Members' own profile edits: written to the roster sheet, logged, and the Secretary told.

Every change is appended to profile-changes.jsonl (readable only by the app) with the
old and new values, so an officer can undo it. The roster cache and the running app
are updated at once; the daily sync then reads the same values back from the sheet.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time

from . import auth, prefs
from .club import DATA, ROSTER_CACHE, Club, one_line, roster_locked
from .roster import COLUMNS, EDITABLE, FLAGS, Sheet, _truthy

log = logging.getLogger("w8lbz.profile")
LABELS = {"callsign": "Callsign", "license_class": "License class", "joined": "Joined", "email": "Email", "phone": "Phone", "address": "Address", "birthday": "Birthday", "share": "Shared with members", "role": "Title(s)",
          "name": "Name", "status": "Status", "officer": "Officer", "board": "Board", "minutes_editor": "May lead meetings",
          "calendar_editor": "May change the calendars", "aliases": "Other spellings", "membership_log": "Membership Log"}


def log_path():
    return DATA / "profile-changes.jsonl"


def as_cell(fld: str, value) -> str:
    """A member field as the text kept in the change log (ticks as TRUE / FALSE)."""
    if fld in FLAGS:
        return "TRUE" if value is True or _truthy(str(value or "")) else "FALSE"
    return ", ".join(value) if isinstance(value, list) else (value or "")


def history(limit: int = 200) -> list[dict]:
    p = log_path()
    if not p.exists():
        return []
    entries = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    undone = {e["undoes"] for e in entries if e.get("undoes")}
    for e in entries:
        e["undone"] = e["id"] in undone
    return entries[::-1][:limit]


def _append(entry: dict) -> None:
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    os.chmod(p, 0o600)


def log_added(mid: str, by: str, fields: dict) -> None:
    """A new member added from the Roster page; shown in Recent changes (it can't be undone from there)."""
    _append({"id": secrets.token_hex(6), "at": int(time.time()), "member": mid, "by": by,
             "added": True, "old": {}, "new": fields})


def _apply_locally(club: Club, mid: str, new: dict[str, str]) -> None:
    """Update the running app and the roster cache so the change shows before the next sync."""
    m = club.members[mid]
    for fld, v in new.items():
        if fld == "name":
            m.first, _, m.last = v.partition(" ")  # as the sync splits full_name
        elif fld in FLAGS:
            setattr(m, fld, v == "TRUE")
        elif fld == "status":
            m.status = v or "Active"
        else:
            setattr(m, fld, [x.strip() for x in v.split(",") if x.strip()] if fld in ("share", "aliases") else (v or None))
    fields = [f for f in new if f != "name"] + (["first", "last"] if "name" in new else [])
    if ROSTER_CACHE.exists():
        rows = json.loads(ROSTER_CACHE.read_text())
        for r in rows:
            if r["id"] == mid:
                r.update({f: getattr(m, f) for f in fields})
        tmp = ROSTER_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=1))
        tmp.chmod(0o600)
        tmp.replace(ROSTER_CACHE)


@roster_locked
def change(club: Club, mid: str, new: dict[str, str], by: str, sheet: Sheet | None = None,
           url: str = "", undoes: str | None = None, allowed=EDITABLE) -> dict | None:
    """Write a member's changed fields (field -> text, as as_cell gives it) to the sheet, log them, tell the
    Secretary. `allowed`: EDITABLE for a member's own profile, COLUMNS for roster keepers.

    Returns the log entry, or None if nothing changed. Raises roster.SheetError if the
    sheet can't be written; then nothing is changed anywhere.
    """
    m = club.members[mid]
    new = {f: v for f, v in new.items() if f in allowed and v != as_cell(f, getattr(m, f))}
    if not new:
        return None
    cells = {COLUMNS[f]: (v == "TRUE") if f in FLAGS else v for f, v in new.items()}  # ticks stay checkboxes
    old = (sheet or Sheet()).update_member(m.callsign, m.name, cells)
    old = {f: as_cell(f, old[COLUMNS[f]]) if f in FLAGS else old[COLUMNS[f]] for f in new}
    _apply_locally(club, mid, new)
    entry = {"id": secrets.token_hex(6), "at": int(time.time()), "member": mid, "by": by,
             "old": old, "new": new, "undoes": undoes}
    _append(entry)
    log.info("profile change %s for %s by %s: %s", entry["id"], one_line(mid), one_line(by), one_line(", ".join(new)))
    _notify(club, entry, url)
    return entry


@roster_locked
def undo(club: Club, change_id: str, by: str, sheet: Sheet | None = None, url: str = "") -> dict:
    """Put back the old values of a change, unless the member has changed those fields again since."""
    e = next((x for x in history(10_000) if x["id"] == change_id), None)
    if not e or e["undone"] or e.get("undoes") or e.get("added") or e.get("office") or e.get("logged"):
        raise ValueError("That change can't be undone.")
    m = club.members[e["member"]]
    if any(as_cell(f, getattr(m, f)) != v for f, v in e["new"].items()):
        raise ValueError("Those details have been changed again since; edit them on the roster sheet instead.")
    return change(club, e["member"], e["old"], by, sheet, url, undoes=change_id, allowed=COLUMNS)


def _notify(club: Club, e: dict, url: str) -> None:
    """Tell the Secretary, and the member whose details changed (old and new addresses, if the email changed)."""
    who, by = club.name(e["member"]), club.name(e["by"])

    def show(v):
        return (v or "(blank)").replace("\n", " / ")
    what = "\n".join(f"  {LABELS[f]}: {show(e['old'].get(f))}  ->  {show(v)}" for f, v in e["new"].items())
    action = f"{by} undid an earlier change to {who}'s roster details" if e.get("undoes") else (
        f"{who} updated their roster details" if e["by"] == e["member"] else f"{by} updated {who}'s roster details")
    secretaries = [club.members[s].email for s in club.secretaries
                   if s != e["by"] and club.members[s].email and prefs.wants(s, "roster") == "now"]
    _send(club, secretaries, f"Roster update: {who}",
          f"{action}. The roster sheet has been updated.\n\n{what}\n\n"
          f"If this looks wrong, any officer can undo it here: {url}/roster/changes\n\n"
          f"You get these because you keep the roster. To stop them: {url}/profile/email\n\n73,\nW8LBZ Minutes", e)

    m = club.members[e["member"]]
    member_to = [a for a in dict.fromkeys([m.email, e["old"].get("email")]) if a]  # both, after an email change
    you = "You" if e["by"] == e["member"] else by
    _send(club, member_to, "Your W8LBZ roster details were updated",
          f"Hi {m.first},\n\n{you} updated your details on the club roster:\n\n{what}\n\n"
          f"You can see and change them on your profile: {url}/profile\n"
          f"If you didn't expect this, reply to this email or tell an officer.\n\n73,\nW8LBZ Minutes", e)


def _send(club: Club, to: list[str], subject: str, body: str, e: dict) -> None:
    if not to:
        return
    try:
        auth.send_email(to, subject, body, only_to=club.email_only_to())
    except Exception:
        log.exception("couldn't send %r about change %s", one_line(subject), e["id"])
