"""A new callsign: the sheet, a backup first, and the member's id renamed in every current record."""
import json
import sqlite3
from pathlib import Path

import pytest

from w8lbz_minutes import backup, callsign, membership, profile, roster
from w8lbz_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "kd8aaa", "first": "Amy", "last": "Able", "callsign": "KD8AAA", "email": "amy@example.org",
           "aliases": ["Amelia", "KD8OLD"]},  # KD8OLD: a call she held before
          {"id": "kd8bbb", "first": "Bob", "last": "Baker", "callsign": "KD8BBB"}]
MINUTES = """---
date: 2026-11-05
status: published
leader: kd8aaa
attendance:
  in_person:
  - kd8aaa
  - kd8bbb
---

## Community Reports
- Amy worked KD8AAA's friend on 2m <!-- member: kd8aaa -->
- Bob is fine <!-- member: kd8bbb -->
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = tmp_path
    (d / "meetings" / "2026-11-05").mkdir(parents=True)
    (d / "meetings" / "2026-11-05" / "minutes.md").write_text(MINUTES)
    (d / "chow.json").write_text(json.dumps({"nights": {"2026-11-12": {"member": "kd8aaa"}}, "order": ["kd8bbb", "kd8aaa"],
                                             "constraints": {"kd8aaa": {"not_weeks": [2]}}}))
    (d / "business.json").write_text(json.dumps({"items": [{"owner": "kd8aaa", "text": "kd8aaa stays as text? no: exact only",
                                                            "history": [{"by": "kd8aaa"}]}]}))
    (d / "email-prefs.json").write_text(json.dumps({"kd8aaa": {"digest": "off"}}))
    (d / "treasurer.json").write_text(json.dumps({"entries": [{"member": "kd8aaa", "kind": "dues", "cents": 350}], "bills": {}}))
    (d / "avatars").mkdir()
    (d / "avatars" / "kd8aaa.jpg").write_bytes(b"jpg")
    db = sqlite3.connect(d / "auth.sqlite")
    db.execute("create table passkeys (id text, member text)")
    db.execute("create table sessions (token_hash text, member text, view_as text)")
    db.execute("insert into passkeys values ('pk', 'kd8aaa')")
    db.execute("insert into sessions values ('t', 'kd8aaa', null)")
    db.commit()
    cache = d / "roster.json"
    cache.write_text(json.dumps(PEOPLE))
    for mod, attr in ((roster, "CACHE"), (membership, "ROSTER_CACHE"), (callsign, "ROSTER_CACHE")):
        monkeypatch.setattr(mod, attr, cache)
    for mod in (roster, profile, callsign, backup):
        monkeypatch.setattr(mod, "DATA", d)
    monkeypatch.setattr(roster, "PREVIEW", "test")
    monkeypatch.setenv("W8LBZ_BACKUP_PASSPHRASE", "a long test passphrase")
    return Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text())), d


def test_callsign_change_renames_everything(env):
    club, d = env
    club.admins = {"kd8aaa"}
    assert callsign.change(club, "kd8aaa", "w8amy", "2026-11-08", "kd8bbb") == "w8amy"
    assert "kd8aaa" not in club.members and club.members["w8amy"].callsign == "W8AMY"
    assert club.canonical("kd8aaa") == "w8amy" and club.canonical("kd8old") == "w8amy"  # both old calls still find her
    assert club.is_admin("w8amy") and club.name("kd8aaa") == "Amy Able"  # club.yaml and old log entries still work
    row = json.loads((d / "roster.json").read_text())[0]
    assert row["aliases"] == ["Amelia", "KD8OLD", "KD8AAA"] and row["events"][-1]["event"] == "Callsign Change"
    sheet = json.loads((d / "preview-sheet.json").read_text())
    assert sheet["Member Roster"][1][0] == "W8AMY" and sheet["Membership Log"][-1][3:] == ["Callsign Change", "KD8AAA to W8AMY"]
    chow = json.loads((d / "chow.json").read_text())
    assert chow["nights"]["2026-11-12"]["member"] == "w8amy" and chow["order"] == ["kd8bbb", "w8amy"] and "w8amy" in chow["constraints"]
    biz = json.loads((d / "business.json").read_text())["items"][0]
    assert biz["owner"] == "w8amy" and biz["history"][0]["by"] == "w8amy" and biz["text"].startswith("kd8aaa")  # text untouched
    assert "w8amy" in json.loads((d / "email-prefs.json").read_text())
    assert json.loads((d / "treasurer.json").read_text())["entries"][0]["member"] == "w8amy"  # payments follow the member
    md = (d / "meetings" / "2026-11-05" / "minutes.md").read_text()
    assert "leader: w8amy" in md and "  - w8amy\n  - kd8bbb" in md and "<!-- member: w8amy -->" in md
    assert "KD8AAA's friend" in md  # text in the minutes is left as written
    con = sqlite3.connect(d / "auth.sqlite")
    assert con.execute("select member from passkeys").fetchone() == ("w8amy",)  # passkeys keep working
    assert (d / "avatars" / "w8amy.jpg").exists()
    assert any("before-w8amy" in p.name for p in (d / "backups").iterdir())  # the backup taken first


def test_callsign_change_refusals(env, monkeypatch):
    club, d = env
    for call, msg in (("KD8BBB", "another member"), ("KD8AAA", "already"), ("not a call", "doesn't look like")):
        with pytest.raises(ValueError, match=msg):
            callsign.change(club, "kd8aaa", call, "2026-11-08", "kd8bbb")
    club.members["kd8bbb"].aliases = []
    club.former_ids["k8xyz"] = "kd8bbb"  # Bob's old call
    with pytest.raises(ValueError, match="another member"):
        callsign.change(club, "kd8aaa", "K8XYZ", "2026-11-08", "kd8bbb")
    monkeypatch.setenv("W8LBZ_BACKUP_PASSPHRASE", "")
    with pytest.raises(backup.BackupError):  # no backup, no rename
        callsign.change(club, "kd8aaa", "W8AMY", "2026-11-08", "kd8bbb")
    assert json.loads((d / "chow.json").read_text())["order"] == ["kd8bbb", "kd8aaa"]


def test_callsign_change_from_the_page(env, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.store import Archive
    club, d = env
    club.members["kd8bbb"].role = "Secretary"  # a roster keeper
    a = Archive(d / "meetings", club)
    monkeypatch.setattr(web, "archive", a)
    for mod in (web.business, web.events):
        monkeypatch.setattr(mod, "DATA", d)
    who = {"id": "kd8aaa"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    c = TestClient(web.app)
    assert c.post("/members/kd8aaa/callsign", data={"callsign": "W8AMY", "date": "2026-11-08"}).status_code == 403
    who["id"] = "kd8bbb"
    assert "New callsign" in c.get("/members/kd8aaa/edit").text
    r = c.post("/members/kd8aaa/callsign", data={"callsign": "W8AMY", "date": "2026-11-08"}, follow_redirects=False)
    assert r.headers["location"].startswith("/members/w8amy/edit?saved=1")
    assert a.by_slug["2026-11-05"].leader == "w8amy" and c.get("/members/w8amy").status_code == 200
    club.members["kd8bbb"].role = "Secretary"  # the roster reload dropped the test's in-memory title
    assert "error=" in c.post("/members/w8amy/callsign", data={"callsign": "KD8BBB", "date": "2026-11-08"},
                              follow_redirects=False).headers["location"]
