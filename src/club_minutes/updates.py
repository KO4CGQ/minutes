"""Updating the app from its own admin page.

- "Update available": the latest GitHub release vs. the running version, checked at most every few hours.
- "Update now": the app runs as the unprivileged `minutes` user, so it only writes update-request.json in
  its data folder. A root-owned systemd path unit (deploy/club-minutes-update.path) notices the file and
  runs deploy/update-from-app.sh, which runs update.sh (the same as a manual update: latest main) and writes
  update-status.json for this page. The app can't make it do anything else.
- Deploy notes: a release whose CHANGELOG section has a "Deploy notes:" line needs extra steps by hand
  (a config change, a data file). The page shows them and an admin must confirm they're done first.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from importlib.metadata import version as installed_version
from pathlib import Path

from . import branding
from .club import DATA, PREVIEW, STAGE

log = logging.getLogger("club_minutes.updates")
REPO = "KO4CGQ/minutes"
STUCK_AFTER = 600  # seconds a request may wait before the page says the updater didn't pick it up
STALE_RUN = 1200  # seconds: past the unit's 15-minute timeout, a running file is left over from a kill or reboot
CHECK_EVERY = 3600  # GitHub allows 60 unauthenticated checks an hour; one is plenty


def _read(p: Path) -> dict:
    """A cache or setting file; an unreadable one counts as none (the web app and the hourly job both write them)."""
    try:
        d = json.loads(p.read_text()) if p.exists() else {}
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        log.error("%s is unreadable; ignoring it", p)
        return {}


def _write(p: Path, d: dict) -> None:
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp")  # the other process writes here too: never the same temp file
    tmp.write_text(json.dumps(d))
    tmp.replace(p)


def running() -> str:
    return installed_version("club-minutes")


def _v(s: str) -> tuple[int, ...]:
    """Sort key for X.Y.Z, X.Y.Z-rc.N (a tag) or X.Y.ZrcN (pyproject): a release candidate sorts before its release."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-?rc\.?(\d+))?", s) or re.match(r"v?(\d+)\.(\d+)\.(\d+)()", s)
    if not m:
        raise ValueError(f"Not a version: {s}")
    rc = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if rc else 1, int(rc or 0))


def newer(a: str, b: str) -> bool:
    """Is version a newer than version b?"""
    return _v(a) > _v(b)


SOURCES = ("releases", "rc", "next")


def source() -> str:
    """What this server updates from: "releases", "rc" (release candidates too) or "next" (the next branch).
    Only a staging server may choose; every other server always updates from releases."""
    s = auto_settings().get("from")
    return s if STAGE and s in SOURCES else "releases"


def set_source(value: str) -> None:
    if not STAGE or value not in SOURCES:
        raise ValueError("Only a staging server chooses what to update from.")
    _save_auto({**auto_settings(), "from": value})


def running_commit() -> str:
    """The commit this install is at ("" if it can't tell)."""
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _latest_next(fetch=None, force: bool = False) -> dict | None:
    """The head of the next branch, as a pseudo-release (cached in update-check-next.json)."""
    cache = DATA / "update-check-next.json"
    if not force:
        c = _read(cache)
        if time.time() - c.get("at", 0) < CHECK_EVERY and c.get("next"):
            return c["next"]
    try:
        if fetch is None:
            import requests
            r = requests.get(f"https://api.github.com/repos/{REPO}/branches/next", timeout=10,
                             headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            d = r.json()
        else:
            d = fetch()
        sha = d["commit"]["sha"]
        rel = {"version": "next", "commit": sha[:7], "notes": d["commit"]["commit"]["message"].splitlines()[0],
               "url": f"https://github.com/{REPO}/commit/{sha}", "deploy_notes": ""}
        _write(cache, {"at": int(time.time()), "next": rel})
        return rel
    except Exception as e:  # noqa: BLE001
        log.warning("update check failed: %s", e)
        return _read(cache).get("next")


def deploy_notes(notes: str) -> str:
    """The steps under a "Deploy notes:" marker in a release's notes (its CHANGELOG section), as Markdown:
    text on the marker's own line, then everything indented under it (numbered steps, code blocks), dedented."""
    import textwrap
    first, rest, on = "", [], False
    for line in notes.splitlines():
        m = re.match(r"^\s*[-*]?\s*\**Deploy notes:?\**:?\s*(.*)$", line, re.IGNORECASE)
        if m:
            on, first = True, m.group(1).strip()
            continue
        if on:
            if line.startswith("  ") or not line.strip():
                rest.append(line)
            else:
                break
    return (first + "\n\n" + textwrap.dedent("\n".join(rest))).strip()


def latest(fetch=None, force: bool = False) -> dict | None:
    """The latest GitHub release: {version, notes, url, deploy_notes}; the releases are cached in update-check.json.
    With several releases newer than the running one, notes and deploy_notes cover them all (newest first), so
    steps by hand in a release in between aren't skipped by going straight to the newest."""
    if source() == "next":
        return _latest_next(fetch, force)
    cache = DATA / "update-check.json"
    rels = None
    if not force:
        c = _read(cache)
        if time.time() - c.get("at", 0) < CHECK_EVERY:
            rels = c.get("releases") or ([c["release"]] if c.get("release") else [])
    if rels is None:
        try:
            if fetch is None:
                import requests
                r = requests.get(f"https://api.github.com/repos/{REPO}/releases", params={"per_page": 30}, timeout=10,
                                 headers={"Accept": "application/vnd.github+json"})
                r.raise_for_status()
                data = r.json()
            else:
                data = fetch()
            data = [data] if isinstance(data, dict) else data
            rels = [{"version": d["tag_name"].lstrip("v"), "notes": d.get("body") or "", "url": d.get("html_url"),
                     "deploy_notes": deploy_notes(d.get("body") or ""), "rc": "-rc." in d["tag_name"]}
                    for d in data if not d.get("draft") and (not d.get("prerelease") or "-rc." in d.get("tag_name", "")) and re.fullmatch(r"v?\d+\.\d+\.\d+(-rc\.\d+)?", d.get("tag_name", ""))]
            _write(cache, {"at": int(time.time()), "releases": rels})
        except Exception as e:  # noqa: BLE001
            log.warning("update check failed: %s", e)
            c = _read(cache)
            if not c:
                return None
            rels = c.get("releases") or ([c["release"]] if c.get("release") else [])
    rels = [r for r in rels if source() == "rc" or not r.get("rc")]
    if not rels:
        return None
    rels = sorted(rels, key=lambda r: _v(r["version"]), reverse=True)
    ahead = [r for r in rels if newer(r["version"], running())]
    if len(ahead) < 2:
        return dict(rels[0])
    both = lambda key: "\n\n".join(f"**{r['version']}:** {r[key]}" if key == "deploy_notes" else f"### {r['version']}\n\n{r[key]}"
                                    for r in ahead if r[key])
    return {**rels[0], "notes": both("notes"), "deploy_notes": both("deploy_notes")}


def available(fetch=None) -> dict | None:
    """The latest release (or the next branch's head) if it's newer than what's running."""
    rel = latest(fetch)
    if rel and rel["version"] == "next":
        return rel if rel["commit"] != running_commit()[:7] else None  # can't tell: offer it
    return rel if rel and newer(rel["version"], running()) else None


def status() -> dict:
    """{"requested": {...}} while an update is waiting or running, and the last result."""
    out = {}
    for key, name in (("requested", "update-request.json"), ("running", "update-running.json"),
                      ("last", "update-status.json")):
        p = DATA / name
        if p.exists():
            try:
                out[key] = json.loads(p.read_text())
            except ValueError:
                out[key] = {}
    req = out.get("requested")
    if req and "running" not in out and time.time() - req.get("at", 0) > STUCK_AFTER:
        out["stuck"] = True  # nothing on the server picked it up: the updater isn't installed or failed to start
    try:
        if "running" in out and time.time() - (DATA / "update-running.json").stat().st_mtime > STALE_RUN:
            out["stuck"] = True  # a reboot or kill left it behind: Cancel clears it
    except FileNotFoundError:
        pass  # the updater finished between the read and now
    return out


def cancel() -> bool:
    """Withdraw a request the updater never picked up, or clear a stale running file it left behind."""
    st = status()
    names = ["update-request.json"] + (["update-running.json"] if "running" in st and st.get("stuck") else [])
    gone = [DATA / n for n in names if (DATA / n).exists()]
    for p in gone:
        p.unlink(missing_ok=True)
    return bool(gone)


def request(by: str, target: str, notes_confirmed: bool) -> None:
    """Ask the root-owned updater to run. Raises ValueError if it can't be asked now."""
    if PREVIEW:
        raise ValueError("Previews don't update themselves (deploy/preview.sh).")
    rel = latest()
    if not rel or target != rel["version"]:  # exactly what this server would install now (its Update from)
        raise ValueError("That isn't something this server updates to.")
    st = status()
    if "requested" in st or "running" in st:
        raise ValueError("An update is already waiting or running.")
    if rel and rel.get("deploy_notes") and not notes_confirmed:
        raise ValueError("This release needs the steps under Deploy notes first. Tick the box once they're done.")
    p = DATA / "update-request.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": int(time.time()), "by": by, "from": running(), "to": target}))
    tmp.replace(p)  # the path unit fires on the file appearing


# ---------------- automatic updates (club-minutes-autoupdate.timer: hourly; runs at the hour set on the Updates page) ----------------

AUTO_HOUR = 2  # the default: the small hours, away from Thursday meetings

def auto_settings() -> dict:
    """{"on": bool, "hour": 0-23 (local), "told": [versions the admins were already emailed about], "from": see source()}
    (update-settings.json)."""
    p = DATA / "update-settings.json"
    return {"on": False, "hour": AUTO_HOUR, "told": [], "from": "releases", **_read(p)}


def _save_auto(s: dict) -> None:
    p = DATA / "update-settings.json"
    _write(p, s)


def set_auto(on: bool, hour: int | None = None) -> None:
    s = auto_settings()
    _save_auto({**s, "on": bool(on), "hour": hour if hour in range(24) else s["hour"]})


def _tell_admins(club, subject: str, body: str) -> None:
    from . import auth
    to = [m.email for mid, m in club.members.items() if club.is_admin(mid) and m.email]
    if to:
        try:
            auth.send_email(to, f"{branding.name(club)}: {subject}", body + f"\n\n73,\n{branding.name(club)}")
        except Exception:
            log.exception("couldn't email the admins about an automatic update")


def auto(club, url: str, wait: float = 900, poll: float = 10, now=None) -> str:
    """The nightly run: if automatic updates are on and a newer release needs no steps by hand, back up, ask the
    updater for exactly that release, wait for the outcome and email the admins. Returns what it did."""
    s = auto_settings()
    if PREVIEW or not s["on"]:
        return "automatic updates are off"
    import datetime as dt
    if (now or dt.datetime.now()).hour != s["hour"]:
        return f"not now: automatic updates run at {s['hour']}:30"
    rel = latest(force=True)
    nxt = source() == "next"
    if not rel or not ((running_commit() or rel["commit"])[:7] != rel["commit"] if nxt else newer(rel["version"], running())):
        return "up to date"
    v, page = rel["version"], f"{url}/admin/updates"
    last = status().get("last") or {}
    if last.get("to") == v and not last.get("ok"):
        return f"{v} failed before; waiting for an admin"  # never retried night after night
    if rel.get("deploy_notes"):
        if v not in s["told"]:
            _tell_admins(club, f"version {v} needs you", f"Version {v} is out, but it needs steps by hand on the server "
                         f"first, so it wasn't installed automatically:\n\n{rel['deploy_notes']}\n\nThen update from {page}")
            _save_auto({**s, "told": [*s["told"], v][-20:]})
        return f"{v} needs steps by hand"
    st = status()
    if "requested" in st or "running" in st:
        return "an update is already waiting or running"
    from . import backup
    b = backup.run(club, url)
    if not b.get("ok"):
        _tell_admins(club, f"version {v} not installed", f"Version {v} wasn't installed automatically because the "
                     f"backup before it failed:\n\n  {b.get('error')}\n\nSee {url}/admin/backups")
        return "backup failed"
    request("automatic", v, notes_confirmed=False)
    end = time.time() + wait
    while time.time() < end:
        done = status().get("last") or {}
        if done.get("to") == v and "requested" not in status() and "running" not in status():
            ok = done.get("ok")
            _tell_admins(club, f"updated to {v}" if ok else f"the update to {v} failed",
                         (f"The app updated itself to {v} overnight, after a backup ({b.get('name')})."
                          if ok else f"The automatic update to {v} failed. The updater's last words are below: if it had "
                                     f"installed anything, they say whether it went back to the version before. "
                                     f"It won't try {v} again by itself.\n\n"
                                     f"{done.get('output', '').strip()[-1500:] or '(It said nothing: see journalctl -u club-minutes-update on the server.)'}") + f"\n\n{page}")
            return f"{'updated to' if ok else 'failed:'} {v}"
        time.sleep(poll)
    _tell_admins(club, f"the update to {v} hasn't finished", f"The automatic update to {v} was asked for but hadn't "
                 f"finished after {int(wait // 60)} minutes. Have a look: {page}")
    return "still waiting"
