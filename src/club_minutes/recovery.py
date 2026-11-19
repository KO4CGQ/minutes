"""Recover admin access by the officers (FIXES → Problem 5), for when the club's admins have died, left or turned hostile.

An officer (an active holder of an officer's office) names an active member to become admin, and optionally admins to
suspend. The nominee accepts; a majority of the officers (at least 2) approve, each confirming it's them as for admin
mode; then a 7-day wait. If any admin objects, it needs two-thirds and 14 days: an objection, never a veto. Every
member is told at every step. When it completes, the nominee is an admin and the named admins are suspended (their
admin rights stop counting, club.yaml's too); only another admin lifts a suspension. The daily job (club-minutes
reminders) completes it once the wait is over.

The limit: this guards against an admin with only app access. Anyone with the server can change the files.
"""
from __future__ import annotations

import contextlib
import fcntl
import functools
import json
import secrets
import time

from . import auth, branding
from .club import DATA, Club, show_date

WAIT_DAYS, WAIT_DAYS_OBJECTED = 7, 14
LAPSE_DAYS = 30  # not approved and accepted by then: it ends by itself, so one officer or nominee can't hold it forever
MOTION_MAX = 200


SAFETY_NET = ("This is the club's safety net for when its website admins can't be reached. "
              "You don't need to do anything.\n\n")


@contextlib.contextmanager
def locked():
    """One lock for who's an admin: recovery steps, the daily job completing one, Admins add/remove/lift and a new
    callsign (separate processes, and two officers at once), so none of them undoes another's change."""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "recovery.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _locked(fn):
    @functools.wraps(fn)
    def run(*a, **kw):
        with locked():
            return fn(*a, **kw)
    return run


def _path():
    return DATA / "recovery.json"


def load() -> dict | None:
    """The open recovery, or None."""
    p = _path()
    return json.loads(p.read_text()) if p.exists() else None


def _save(r: dict | None) -> None:
    p = _path()
    if r is None:
        p.unlink(missing_ok=True)
        return
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(r, indent=1))
    tmp.chmod(0o600)
    tmp.replace(p)


def officers(club: Club) -> list[str]:
    """Active members holding an officer's office (whole title). Only admins change who holds an office."""
    offs = {o["title"].lower() for o in club.office_list() if o["officer"]}
    return sorted(m.id for m in club.members.values() if m.status.lower() == "active"
                  and any(t.strip().lower() in offs for t in (m.role or "").split(",")))


def needed(n: int, objected: bool) -> int:
    """A majority of the officers, or two-thirds once an admin objects; never fewer than 2."""
    return max(2, -(-2 * n // 3) if objected else n // 2 + 1)


def state(club: Club, r: dict) -> dict:
    """How it stands: the officers, how many approvals it needs and has, and when it completes (None: not yet set)."""
    offs = officers(club)
    approvals = sorted(t for m, t in r["approvals"].items() if m in offs)  # someone no longer an officer doesn't count
    objected = bool(r["objections"])
    need = needed(len(offs), objected)
    ready = None
    if r.get("accepted") and len(approvals) >= need:
        ready = max(r["accepted"], approvals[need - 1]) + (WAIT_DAYS_OBJECTED if objected else WAIT_DAYS) * 86400
    calloffs = [m for m in r.get("calloffs", {}) if m in offs]
    return {"officers": offs, "need": need, "have": len(approvals), "objected": objected, "ready_at": ready,
            "lapses_at": None if ready else r["at"] + LAPSE_DAYS * 86400, "calloffs": calloffs, "calloff_need": needed(len(offs), False)}


@_locked
def start(club: Club, by: str, nominee: str, suspend: list[str], motion: str, url: str) -> dict:
    if load():
        raise ValueError("A recovery is already under way: see it on this page.")
    if by not in officers(club):
        raise ValueError("Only an officer can start a recovery.")
    m = club.members.get(nominee)
    if not m or m.status.lower() != "active":
        raise ValueError("Pick an active member to become admin.")
    if nominee in club.all_admins:
        raise ValueError(f"{m.name} is already an admin.")
    suspend = sorted({a for a in suspend if a in club.all_admins} - {nominee})
    r = {"id": secrets.token_hex(4), "at": int(time.time()), "by": by, "nominee": nominee, "suspend": suspend,
         "motion": " ".join(motion.split())[:MOTION_MAX], "approvals": {by: int(time.time())}, "objections": {}, "accepted": None}
    _save(r)
    what = f"{club.name(by)} started a recovery of admin access: {m.name} to become an admin" + (
        f", and {', '.join(club.name(a) for a in suspend)} suspended as admin" if suspend else "") + "."
    _tell(club, "Recovering admin access", what, r, url, first=True)
    return r


def _open(club: Club) -> dict:
    r = load()
    if not r:
        raise ValueError("There's no recovery under way.")
    return r


@_locked
def approve(club: Club, by: str, url: str) -> dict:
    r = _open(club)
    if by not in officers(club):
        raise ValueError("Only an officer can approve.")
    if by not in r["approvals"]:
        r["approvals"][by] = int(time.time())
        _save(r)
        _tell(club, "Recovering admin access: approved", f"{club.name(by)} approved.", r, url)
    return r


@_locked
def accept(club: Club, by: str, url: str) -> dict:
    r = _open(club)
    if by != r["nominee"]:
        raise ValueError("Only the member named can accept.")
    if not r["accepted"]:
        r["accepted"] = int(time.time())
        _save(r)
        _tell(club, "Recovering admin access: accepted", f"{club.name(by)} accepted becoming an admin.", r, url)
    return r


@_locked
def object_(club: Club, by: str, url: str) -> dict:
    """An admin (in admin mode) objects: two-thirds and 14 days from then on. It can't stop it."""
    r = _open(club)
    if by not in club.all_admins:
        raise ValueError("Only an admin can object.")
    if by not in r["objections"]:
        r["objections"][by] = int(time.time())
        _save(r)
        _tell(club, "Recovering admin access: an admin objected",
              f"{club.name(by)}, an admin, objected. It can still go ahead, but now needs two-thirds of the officers "
              f"and a {WAIT_DAYS_OBJECTED}-day wait.", r, url)
    return r


@_locked
def cancel(club: Club, by: str, url: str) -> bool:
    """The officer who started it calls it off at once; other officers each ask, and a majority of the officers (at
    least 2) calls it off. True if it ended."""
    r = _open(club)
    offs = officers(club)
    if by != r["by"] and by not in offs:
        raise ValueError("Only an officer can call it off.")
    r.setdefault("calloffs", {})[by] = int(time.time())
    st = state(club, r)
    if by == r["by"] or len(st["calloffs"]) >= st["calloff_need"]:
        _save(None)
        who = club.name(by) if by == r["by"] else ", ".join(club.name(m) for m in st["calloffs"]) + " (a majority of the officers)"
        _tell(club, "Recovering admin access: called off", f"{who} called off the recovery.", r, url)
        return True
    _save(r)
    _tell(club, "Recovering admin access: asked to call it off", f"{club.name(by)} asked to call off the recovery "
          f"({len(st['calloffs'])} of the {st['calloff_need']} officers needed).", r, url)
    return False


@_locked
def tick(club: Club, url: str, now: float | None = None) -> bool:
    """Complete it once the wait is over (the daily job, and each view of the page). True if it completed."""
    r = load()
    if not r:
        return False
    st, now = state(club, r), now or time.time()
    if st["lapses_at"] and now >= st["lapses_at"]:
        _save(None)
        _tell(club, "Recovering admin access: lapsed", f"It wasn't approved and accepted within {LAPSE_DAYS} days, so it "
              "ended without changing any admin. An officer can start a new one.", r, url)
        return False
    if not st["ready_at"] or now < st["ready_at"]:
        return False
    n = club.members.get(r["nominee"])
    if not n or n.status.lower() != "active":  # never suspend admins without the new one: the club would be locked out
        _save(None)
        _tell(club, "Recovering admin access: ended", f"{club.name(r['nominee'])} is no longer an active member, so the "
              "recovery ended without changing any admin. An officer can start a new one.", r, url)
        return False
    club.save_app_admins(club.app_admins | {r["nominee"]})
    club.save_suspended(club.suspended | set(r["suspend"]))
    _save(None)
    with (DATA / "admin-log.jsonl").open("a") as f:  # logged first: a mail error mustn't lose it
        f.write(json.dumps({"at": int(time.time()), "member": r["by"], "recovery_done": r["nominee"], "suspended": r["suspend"]}) + "\n")
    _tell(club, "Recovering admin access: done", f"{club.name(r['nominee'])} is now an admin." + (
        f" {', '.join(club.name(a) for a in r['suspend'])} {'is' if len(r['suspend']) == 1 else 'are'} suspended as admin; "
        "only another admin can lift that." if r["suspend"] else ""), r, url)
    return True


def _tell(club: Club, subject: str, what: str, r: dict, url: str, first: bool = False) -> None:
    """Every active member (Jim, 2026-11-14: all of them, at every step), and the suspended admins."""
    st = state(club, r) if load() else None
    how = (f"\n\nApprovals: {st['have']} of the {st['need']} needed ({len(st['officers'])} officers)"
           + (f"; it completes on {show_date(time.strftime('%Y-%m-%d', time.localtime(st['ready_at'])), 'document')} unless an admin objects"
              if st["ready_at"] else "") + ("" if r.get("accepted") else f"; waiting for {club.name(r['nominee'])} to accept") + "."
           if st else "")
    motion = f"\n\nThe board's motion: {r['motion']}" if r.get("motion") else ""
    to = sorted({m.email for m in club.members.values() if m.email and m.status.lower() not in ("inactive", "silent key")
                 and (m.status.lower() == "active" or m.id in r["suspend"])})
    auth.send_email(to, f"{club.label}: {subject}", f"{what}{how}{motion}\n\nWhere it stands, and who has approved: "
                    f"{url}/recover\n\n{SAFETY_NET if first else ''}73,\n{branding.name(club)}", only_to=club.email_only_to())
