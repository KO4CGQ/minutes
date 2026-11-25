"""Event webhooks: tell a service the club chooses (a chat bot, say) when a meeting starts, is cancelled, moved or put
back, and when minutes are published. Set up on Admin → Site → Connections (the address, the kinds sent, a secret made
by the app); the contract a receiver follows is in docs/server-admin.md.

Each event is appended to webhook-queue.json (data folder), then sent at once in a background thread; one that fails
is tried again by `club-minutes webhooks` (a timer, every minute) after 1 min, 5 min, 30 min, 2 h and 6 h, then
dropped. Only one sender runs at a time, so an event is never sent twice at once. Events carry no member's name,
email or other personal data."""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx

from . import club as clubmod
from . import events, meetings

log = logging.getLogger("club_minutes")
KINDS = {"meeting_started": "Meeting started", "meeting_cancelled": "Meeting cancelled", "meeting_moved": "Meeting moved",
         "meeting_restored": "Meeting back on", "minutes_published": "Minutes published"}
RETRY = (60, 300, 1800, 7200, 21600)  # seconds to wait after the 1st, 2nd... failed attempt; one more failure: dropped
LOCAL = ("localhost", "127.0.0.1", "::1")


def valid_url(url: str) -> bool:
    """https, or plain http to this machine only (a receiver beside the app); no sign-in in the address."""
    try:
        u = urlsplit(url)
        return len(url) <= 500 and url.isprintable() and " " not in url and bool(u.hostname) and not u.username and not u.password and u.port != 0 and (
            u.scheme == "https" or u.scheme == "http" and u.hostname in LOCAL)
    except ValueError:
        return False


def _settings() -> dict:
    try:
        return json.loads((clubmod.DATA / "site-settings.json").read_text()).get("webhooks") or {}
    except (OSError, ValueError):
        return {}


def secret() -> str:
    try:
        return (clubmod.DATA / "webhook-secret").read_text().strip()
    except OSError:
        return ""


def secret_made() -> float | None:
    try:
        return (clubmod.DATA / "webhook-secret").stat().st_mtime if secret() else None
    except OSError:
        return None


def new_secret() -> str:
    """Make a new secret (64 hex characters), keep it for the app only, and return it to show once."""
    value, p = secrets.token_hex(32), clubmod.DATA / "webhook-secret"
    tmp = p.with_suffix(f".{secrets.token_hex(4)}.tmp")
    tmp.touch(mode=0o600)
    tmp.write_text(value + "\n")
    tmp.replace(p)
    return value


def signature(key: str, t: int, body: bytes) -> str:
    return f"t={t},v1=" + hmac.new(key.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


@contextmanager
def _locked(name: str, wait: bool = True):
    with open(clubmod.DATA / name, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True


def _load() -> list[dict]:
    try:
        return json.loads((clubmod.DATA / "webhook-queue.json").read_text())
    except OSError:
        return []
    except ValueError:  # torn: start again rather than stop every event
        log.exception("webhook-queue.json is damaged; starting a new queue")
        return []


def _save(q: list[dict]) -> None:
    p = clubmod.DATA / "webhook-queue.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(q))
    tmp.chmod(0o600)
    tmp.replace(p)


def _failure(kind: str | None = None, error: str | None = None) -> None:
    """Remember the last failure (shown on the card), or clear it."""
    p = clubmod.DATA / "webhook-failure.json"
    if error:
        p.write_text(json.dumps({"at": int(time.time()), "kind": kind, "error": error}))
        p.chmod(0o600)
    else:
        p.unlink(missing_ok=True)


def failure() -> dict | None:
    try:
        f = json.loads((clubmod.DATA / "webhook-failure.json").read_text())
        return {**f, "when": dt.datetime.fromtimestamp(f["at"]).strftime("%Y-%m-%d %H:%M")}
    except (OSError, ValueError, KeyError):
        return None


def _event(kind: str, data: dict) -> dict:
    return {"id": secrets.token_hex(16), "kind": kind, "sent": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "data": data}


def _attempt(url: str, key: str, e: dict) -> str | None:
    """One POST of event e, signed now. None if the receiver answered 2xx, else what went wrong. Logged (never the body)."""
    body = json.dumps({k: e[k] for k in ("id", "kind", "sent", "data")}, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "X-Club-Minutes-Event": e["kind"],
               "X-Club-Minutes-Signature": signature(key, int(time.time()), body)}
    try:
        r = httpx.post(url, content=body, headers=headers, timeout=10, follow_redirects=False, trust_env=False)
        err = None if r.is_success else f"HTTP {r.status_code}"
    except (httpx.HTTPError, httpx.InvalidURL) as x:
        err = type(x).__name__  # never str(x): it can hold the address
    log.info("webhook %s to %s: %s", e["kind"], urlsplit(url).hostname, err or "sent")
    return err


def deliver() -> None:
    """Send what's due (the thread after each event, and the timer). Does nothing when there's no queue or no setup."""
    url, key = _settings().get("url") or "", secret()
    if not (clubmod.DATA / "webhook-queue.json").exists() or not valid_url(url) or not key:
        return
    with _locked("webhook-send.lock", wait=False) as mine:
        if not mine:
            return  # another sender is at it
        tried: set[str] = set()
        while True:  # again after each pass: an event queued while this one was sending goes now, not next minute
            with _locked("webhook-queue.lock"):
                due = [e for e in _load() if e["next"] <= time.time() and e["id"] not in tried]
            if not due:
                return
            tried |= {e["id"] for e in due}
            _send(url, key, due)


def _send(url: str, key: str, due: list[dict]) -> None:
    for e in due:
        err = _attempt(url, key, e)
        with _locked("webhook-queue.lock"):
            q = _load()
            if not any(x["id"] == e["id"] for x in q):
                continue
            if not err:
                q = [x for x in q if x["id"] != e["id"]]
            elif e["tries"] >= len(RETRY):
                q = [x for x in q if x["id"] != e["id"]]
                log.warning("webhook %s dropped after %d tries", e["kind"], e["tries"] + 1)
            else:
                next(x for x in q if x["id"] == e["id"]).update(tries=e["tries"] + 1, next=time.time() + RETRY[e["tries"]])
            _save(q)
        _failure(e["kind"], err)


def fire(kind: str, data: dict) -> None:
    """Queue an event if the admin set up webhooks and ticked this kind, and send it in the background. Never raises."""
    try:
        s = _settings()
        if kind not in (s.get("kinds") or []) or not valid_url(s.get("url") or "") or not secret():
            return
        with _locked("webhook-queue.lock"):
            _save(_load() + [{**_event(kind, data), "tries": 0, "next": 0}])
        threading.Thread(target=deliver, daemon=True).start()
    except Exception:  # an event that can't be queued must never undo the change that caused it
        log.exception("couldn't queue webhook %s", kind)


def send_test() -> str | None:
    """One test event now, not queued. None if the receiver took it, else what went wrong."""
    url, key = _settings().get("url") or "", secret()
    if not valid_url(url) or not key:
        return "Set the address and make a secret first."
    err = _attempt(url, key, _event("test", {}))
    _failure("test", err)
    return err


# ---------------- the events ----------------

def _held(day: dt.date) -> tuple[dict, dt.date] | tuple[None, None]:
    """The meeting series held on `day` and that night's original date: a date moved to `day` counts, a cancelled one doesn't."""
    for e in events.load():
        if not e.get("meeting"):
            continue
        for d, rec in e.get("changes", {}).items():
            if rec.get("moved") and rec.get("start", "")[:10] == day.isoformat():
                return e, dt.date.fromisoformat(d)
        if day in events.dates(e, day, day) and not e.get("changes", {}).get(day.isoformat()):
            return e, day
    return None, None


def _series(club, day: dt.date) -> dict | None:
    return next((e for e in events.load() if e.get("meeting") and day in events.dates(e, day, day)), None)


def _title(club, day: dt.date) -> str:
    e = _series(club, day)
    return e["title"] if e else f"{club.label} meeting"


def _calendar_event(kind: str):
    def listener(club, date, url, **kw):
        data = {"date": date.isoformat(), "title": _title(club, date)}
        if "reason" in kw:
            data["reason"] = kw["reason"]
        if kind == "meeting_moved":
            e = _series(club, date)
            data["to"] = kw["to"].strftime("%Y-%m-%d" if e and "T" not in e["start"] else "%Y-%m-%dT%H:%M")
        data["url"] = f"{url}/calendar?start={date}"
        fire(kind, data)
    return listener


for _k in ("meeting_cancelled", "meeting_moved", "meeting_restored"):
    meetings.on(_k, _calendar_event(_k))


def meeting_started(club, day: dt.date, url: str) -> None:
    e, d = _held(day)
    online = events.occurrence(e, d).get("online") if e else None
    fire("meeting_started", {"date": day.isoformat(), "title": e["title"] if e else f"{club.label} meeting", "url": f"{url}/meetings/{day}", "online_url": online or None})


def minutes_published(club, day: dt.date, url: str) -> None:
    fire("minutes_published", {"date": day.isoformat(), "title": _title(club, day), "url": f"{url}/meetings/{day}"})
