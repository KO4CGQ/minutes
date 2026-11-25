"""Event webhooks (webhooks.py): signing, the retry queue, only ticked kinds, the https-only address, the admin routes."""
import datetime as dt
import hashlib
import hmac
import json
import stat
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from club_minutes import club as clubmod
from club_minutes import web, webhooks
from club_minutes.auth import Auth
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
ROSTER = [{"id": "pres", "first": "Jane", "last": "Doe", "role": "President"}, {"id": "n0call", "first": "John", "last": "Doe"}]
URL = "https://hooks.example.org/club"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    c = Club(FIX / "test-club.yaml", roster=ROSTER)
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)
    monkeypatch.setattr(webhooks.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())  # no background sends
    sent = []

    def post(url, content, headers, **kw):
        sent.append((url, content, headers, kw))
        return httpx.Response(status.pop(0) if status else 204)
    status = []
    monkeypatch.setattr(webhooks.httpx, "post", post)

    def as_(who):
        t = TestClient(web.app, client=("203.0.113.6", 5000))
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    return c, as_, sent, status


def set_up(c, kinds=("meeting_cancelled",)):
    c.save_site_settings({("webhooks", "url"): URL, ("webhooks", "kinds"): list(kinds)})
    webhooks.new_secret()


def test_signature_can_be_checked_by_a_receiver(env):
    c, _, sent, _ = env
    set_up(c)
    webhooks.fire("meeting_cancelled", {"date": "2026-12-01"})
    webhooks.deliver()
    url, body, h, kw = sent[0]
    t, v1 = (p.split("=", 1)[1] for p in h["X-Club-Minutes-Signature"].split(","))
    assert hmac.compare_digest(v1, hmac.new(webhooks.secret().encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest())
    assert abs(int(t) - dt.datetime.now().timestamp()) < 5 and h["X-Club-Minutes-Event"] == "meeting_cancelled"
    e = json.loads(body)
    assert len(e["id"]) == 32 and e["kind"] == "meeting_cancelled" and e["data"] == {"date": "2026-12-01"} and e["sent"][-6] in "+-"
    assert url == URL and kw["follow_redirects"] is False and kw["timeout"] == 10
    assert webhooks._load() == []  # a 2xx: done


def test_retries_then_drops(env, monkeypatch):
    c, _, sent, status = env
    set_up(c)
    webhooks.fire("meeting_cancelled", {})
    now = [1000.0]
    monkeypatch.setattr(webhooks.time, "time", lambda: now[0])
    ids = set()
    for wait in (*webhooks.RETRY, None):
        status[:] = [500]
        webhooks.deliver()
        if wait is None:
            break
        (e,) = webhooks._load()
        ids.add(e["id"])
        assert e["next"] == now[0] + wait
        webhooks.deliver()  # not due yet: nothing sent
        now[0] += wait
    assert len(sent) == 6 and webhooks._load() == [] and webhooks.failure()["error"] == "HTTP 500"
    assert len(ids) == 1  # the same event every time


def test_a_success_clears_the_last_failure(env):
    c, _, _, status = env
    set_up(c)
    status[:] = [503]
    assert webhooks.send_test() == "HTTP 503" and webhooks.failure()["kind"] == "test"
    assert webhooks.send_test() is None and webhooks.failure() is None


def test_only_ticked_kinds_and_only_when_set_up(env):
    c, _, _, _ = env
    webhooks.fire("meeting_cancelled", {})
    assert webhooks._load() == []  # no address, no secret
    set_up(c, ("meeting_moved",))
    webhooks.fire("meeting_cancelled", {})
    assert webhooks._load() == []  # not ticked
    webhooks.fire("meeting_moved", {})
    assert [e["kind"] for e in webhooks._load()] == ["meeting_moved"]


def test_address_must_be_https_or_local():
    ok = ["https://hooks.example.org/x", "http://localhost:8000/x", "http://127.0.0.1/x", "http://[::1]:9/x"]
    bad = ["http://hooks.example.org/x", "ftp://hooks.example.org", "https://", "https://user:pw@hooks.example.org/", "hooks.example.org", "", "https://hooks.example.org/\x00", "https://hooks.example.org/a b"]
    assert all(webhooks.valid_url(u) for u in ok) and not any(webhooks.valid_url(u) for u in bad)


def test_admin_routes(env, monkeypatch):
    c, as_, sent, _ = env
    monkeypatch.setattr(web, "DATA", clubmod.DATA)
    c.save_app_admins({"pres"})
    assert as_("pres").post("/admin/site/webhooks", follow_redirects=False).status_code == 403  # not in admin mode
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    for path in ("webhooks", "webhook-secret", "webhook-test"):
        assert as_("n0call").post(f"/admin/site/{path}").status_code == 404  # not an admin
    r = as_("pres").post("/admin/site/webhooks", data={"webhook_url": "http://hooks.example.org/x"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and not webhooks._settings()
    as_("pres").post("/admin/site/webhooks", data={"webhook_url": URL, "kind_meeting_moved": "1", "kind_bogus": "1"})
    assert webhooks._settings() == {"url": URL, "kinds": ["meeting_moved"]}
    # the secret needs a confirmation made in the last few minutes
    r = as_("pres").post("/admin/site/webhook-secret", follow_redirects=False)
    assert r.headers["location"].startswith("/admin/confirm") and not webhooks.secret()
    monkeypatch.setattr(web, "fresh_confirm", lambda request, next: None)
    r = as_("pres").post("/admin/site/webhook-secret")
    value = webhooks.secret()
    assert len(value) == 64 and value in r.text and r.headers["cache-control"] == "no-store"
    assert stat.S_IMODE((clubmod.DATA / "webhook-secret").stat().st_mode) == 0o600
    page = as_("pres").get("/admin/site/connections").text
    assert value not in page and "Make a new secret" in page and "Send a test event" in page
    log = (clubmod.DATA / "admin-log.jsonl").read_text()
    assert value not in log and "webhook_secret" in log and "site_webhooks" in log
    r = as_("pres").post("/admin/site/webhook-test", follow_redirects=False)
    assert "note=" in r.headers["location"] and json.loads(sent[-1][1])["kind"] == "test"


def test_calendar_changes_queue_events_with_the_contract(env):
    from club_minutes import events, meetings
    c, _, _, _ = env
    (events.DATA / "events-meeting-added").unlink()
    events.ensure_meeting(c)
    set_up(c, webhooks.KINDS)
    e = events.load()[0]
    day = events.dates(e, dt.date.today() + dt.timedelta(days=1), dt.date.today() + dt.timedelta(days=8))[0]
    meetings.cancel(c, e["id"], day.isoformat(), "Holiday", "pres", "https://minutes.example.org")
    meetings.restore(c, e["id"], day.isoformat(), "pres", "https://minutes.example.org")
    new = day + dt.timedelta(days=1)
    meetings.move(c, e["id"], day.isoformat(), new.isoformat(), "20:00", "Hall busy", "pres", "https://minutes.example.org")
    q = webhooks._load()
    assert [x["kind"] for x in q] == ["meeting_cancelled", "meeting_restored", "meeting_moved"]
    link = f"https://minutes.example.org/calendar?start={day}"
    assert q[0]["data"] == {"date": str(day), "title": e["title"], "reason": "Holiday", "url": link}
    assert q[1]["data"] == {"date": str(day), "title": e["title"], "url": link}
    assert q[2]["data"] == {"date": str(day), "title": e["title"], "reason": "Hall busy", "to": f"{new}T20:00", "url": link}
    webhooks.meeting_started(c, new, "https://minutes.example.org")  # the moved night still knows its series
    assert webhooks._load()[3]["data"]["title"] == e["title"]
