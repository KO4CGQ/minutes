"""GET /api/roster: active members for the club's other tools, only from the server itself with the token."""
from pathlib import Path

from fastapi.testclient import TestClient

from club_minutes import web
from club_minutes.club import Club
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "n0aaaa", "first": "Amy", "last": "Appleseed", "callsign": "N0AAAA", "email": "amy@example.org", "officer": True,
           "phone": "419-555-0101", "aliases": ["Amelia", "N0OLDC"]},
          {"id": "n0bbbb", "first": "Bob", "last": "Bloggs", "callsign": "N0BBBB", "status": "Inactive"}]


def test_roster_api(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "archive", Archive(tmp_path, Club(FIX / "test-club.yaml", roster=PEOPLE)))
    monkeypatch.setattr(web, "DATA", tmp_path)
    local = TestClient(web.app, client=("127.0.0.1", 50000))
    auth = {"Authorization": "Bearer s3cret"}
    monkeypatch.delenv("CLUB_MINUTES_ROSTER_API_TOKEN", raising=False)
    assert local.get("/api/roster", headers=auth).status_code == 404  # off without a token on the server
    monkeypatch.setenv("CLUB_MINUTES_ROSTER_API_TOKEN", "s3cret")
    r = local.get("/api/roster", headers=auth)
    assert r.json() == [{"callsign": "N0AAAA", "name": "Amy Appleseed", "email": "amy@example.org", "officer": True, "former": ["N0OLDC"]}]
    assert local.get("/api/roster", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert local.get("/api/roster", headers={**auth, "X-Forwarded-For": "203.0.113.9"}).status_code == 403  # the proxy
    assert TestClient(web.app, client=("192.168.1.20", 50000)).get("/api/roster", headers=auth).status_code == 403
    log = (tmp_path / "api-log.jsonl").read_text().splitlines()
    assert len(log) == 5 and '"ok": true' in log[1] and '"ok": false' in log[2]


def test_roster_api_allowlist(tmp_path, monkeypatch):
    """The Discord bot's own container, listed in CLUB_MINUTES_ROSTER_API_ALLOW, may call it; other LAN addresses may not."""
    monkeypatch.setattr(web, "archive", Archive(tmp_path, Club(FIX / "test-club.yaml", roster=PEOPLE)))
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setenv("CLUB_MINUTES_ROSTER_API_TOKEN", "s3cret")
    monkeypatch.setenv("CLUB_MINUTES_ROSTER_API_ALLOW", "192.168.1.50, 192.168.1.51")
    auth = {"Authorization": "Bearer s3cret"}
    bot = TestClient(web.app, client=("192.168.1.51", 50000))
    assert bot.get("/api/roster", headers=auth).status_code == 200
    assert bot.get("/api/roster", headers={**auth, "X-Forwarded-For": "203.0.113.9"}).status_code == 403
    assert TestClient(web.app, client=("192.168.1.52", 50000)).get("/api/roster", headers=auth).status_code == 403
    assert web.app.openapi_url is None and TestClient(web.app).get("/docs", follow_redirects=False).status_code != 200
