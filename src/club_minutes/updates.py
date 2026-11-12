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
import re
import time
from importlib.metadata import version as installed_version

from . import branding
from .club import DATA, PREVIEW, STAGE

log = logging.getLogger("club_minutes.updates")
REPO = "KO4CGQ/minutes"
STUCK_AFTER = 600  # seconds a request may wait before the page says the updater didn't pick it up
CHECK_EVERY = 3600  # GitHub allows 60 unauthenticated checks an hour; one is plenty


def running() -> str:
    return installed_version("club-minutes")


def _v(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])


def newer(a: str, b: str) -> bool:
    """Is version a newer than version b?"""
    return _v(a) > _v(b)


def deploy_notes(notes: str) -> str:
    """The steps under a "Deploy notes:" marker in a release's notes (its CHANGELOG section), as Markdown:
    text on the marker's own line, then everything indented under it (numbered steps, code blocks), dedented."""
    import textwrap
    first, rest, on = "", [], False
    for line in notes.splitlines():
        m = re.match(r"^\s*[-*]?\s*\**Deploy notes:?\**:?\s*(.*)$", line, re.I)
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
    cache = DATA / "update-check.json"
    rels = None
    if not force and cache.exists():
        c = json.loads(cache.read_text())
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
                     "deploy_notes": deploy_notes(d.get("body") or "")}
                    for d in data if not d.get("draft") and not d.get("prerelease") and re.fullmatch(r"v?\d+\.\d+\.\d+", d.get("tag_name", ""))]
            cache.write_text(json.dumps({"at": int(time.time()), "releases": rels}))
        except Exception as e:
            log.warning("update check failed: %s", e)
            if not cache.exists():
                return None
            c = json.loads(cache.read_text())
            rels = c.get("releases") or ([c["release"]] if c.get("release") else [])
    if not rels:
        return None
    rels = sorted(rels, key=lambda r: _v(r["version"]), reverse=True)
    ahead = [r for r in rels if newer(r["version"], running())]
    if len(ahead) < 2:
        return dict(rels[0])
    both = lambda key: "\n\n".join(f"**{r['version']}:** {r[key]}" if key == "deploy_notes" else f"### {r['version']}\n\n{r[key]}"
                                    for r in ahead if r[key])  # noqa: E731
    return {**rels[0], "notes": both("notes"), "deploy_notes": both("deploy_notes")}


def available(fetch=None) -> dict | None:
    """The latest release if it's newer than what's running."""
    rel = latest(fetch)
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
    return out


def cancel() -> bool:
    """Withdraw a request the updater never picked up."""
    p = DATA / "update-request.json"
    if p.exists():
        p.unlink()
        return True
    return False


def request(by: str, target: str, notes_confirmed: bool) -> None:
    """Ask the root-owned updater to run. Raises ValueError if it can't be asked now."""
    if PREVIEW or STAGE:
        raise ValueError("Previews and the staging server don't update themselves (deploy/preview.sh, deploy/stage-push.sh).")
    st = status()
    if "requested" in st or "running" in st:
        raise ValueError("An update is already waiting or running.")
    rel = latest()
    if rel and rel.get("deploy_notes") and not notes_confirmed:
        raise ValueError("This release needs the steps under Deploy notes first. Tick the box once they're done.")
    p = DATA / "update-request.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": int(time.time()), "by": by, "from": running(), "to": target}))
    tmp.replace(p)  # the path unit fires on the file appearing


# ---------------- automatic updates (club-minutes-autoupdate.timer: hourly; runs at the hour set on the Updates page) ----------------

AUTO_HOUR = 2  # the default: the small hours, away from Thursday meetings

def auto_settings() -> dict:
    """{"on": bool, "hour": 0-23 (local), "told": [versions the admins were already emailed about]} (update-settings.json)."""
    p = DATA / "update-settings.json"
    return {"on": False, "hour": AUTO_HOUR, "told": [], **(json.loads(p.read_text()) if p.exists() else {})}


def _save_auto(s: dict) -> None:
    p = DATA / "update-settings.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s))
    tmp.replace(p)


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
    if PREVIEW or STAGE or not s["on"]:
        return "automatic updates are off"
    import datetime as dt
    if (now or dt.datetime.now()).hour != s["hour"]:
        return f"not now: automatic updates run at {s['hour']}:30"
    rel = latest(force=True)
    if not rel or not newer(rel["version"], running()):
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
