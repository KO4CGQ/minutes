"""GET /api/roster: active members for the club's other tools, only from the server itself with the token."""
from pathlib import Path

from fastapi.testclient import TestClient

from w8lbz_minutes import web
from w8lbz_minutes.club import Club
from w8lbz_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "kd8aaa", "first": "Amy", "last": "Able", "callsign": "KD8AAA", "email": "amy@example.org", "officer": True,
           "phone": "419-555-0101"},
          {"id": "kd8bbb", "first": "Bob", "last": "Baker", "callsign": "KD8BBB", "status": "Inactive"}]


def test_roster_api(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "archive", Archive(tmp_path, Club(FIX / "test-club.yaml", roster=PEOPLE)))
    monkeypatch.setattr(web, "DATA", tmp_path)
    local = TestClient(web.app, client=("127.0.0.1", 50000))
    auth = {"Authorization": "Bearer s3cret"}
    monkeypatch.delenv("W8LBZ_ROSTER_API_TOKEN", raising=False)
    assert local.get("/api/roster", headers=auth).status_code == 404  # off without a token on the server
    monkeypatch.setenv("W8LBZ_ROSTER_API_TOKEN", "s3cret")
    r = local.get("/api/roster", headers=auth)
    assert r.json() == [{"callsign": "KD8AAA", "name": "Amy Able", "email": "amy@example.org", "officer": True}]
    assert local.get("/api/roster", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert local.get("/api/roster", headers={**auth, "X-Forwarded-For": "203.0.113.9"}).status_code == 403  # the proxy
    assert TestClient(web.app, client=("192.168.1.20", 50000)).get("/api/roster", headers=auth).status_code == 403
    log = (tmp_path / "api-log.jsonl").read_text().splitlines()
    assert len(log) == 5 and '"ok": true' in log[1] and '"ok": false' in log[2]


def test_roster_api_allowlist(tmp_path, monkeypatch):
    """The Discord bot's own container, listed in W8LBZ_ROSTER_API_ALLOW, may call it; other LAN addresses may not."""
    monkeypatch.setattr(web, "archive", Archive(tmp_path, Club(FIX / "test-club.yaml", roster=PEOPLE)))
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setenv("W8LBZ_ROSTER_API_TOKEN", "s3cret")
    monkeypatch.setenv("W8LBZ_ROSTER_API_ALLOW", "192.168.1.50, 192.168.1.51")
    auth = {"Authorization": "Bearer s3cret"}
    bot = TestClient(web.app, client=("192.168.1.51", 50000))
    assert bot.get("/api/roster", headers=auth).status_code == 200
    assert bot.get("/api/roster", headers={**auth, "X-Forwarded-For": "203.0.113.9"}).status_code == 403
    assert TestClient(web.app, client=("192.168.1.52", 50000)).get("/api/roster", headers=auth).status_code == 403
    assert web.app.openapi_url is None and TestClient(web.app).get("/docs", follow_redirects=False).status_code != 200
