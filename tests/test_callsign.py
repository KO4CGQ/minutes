"""A new callsign: the sheet, a backup first, and the member's id renamed in every current record."""
import json
import sqlite3
from pathlib import Path

import pytest

from club_minutes import backup, callsign, membership, profile, roster
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "n0aaaa", "first": "Amy", "last": "Appleseed", "callsign": "N0AAAA", "email": "amy@example.org",
           "aliases": ["Amelia", "N0OLDC"]},  # N0OLDC: a call she held before
          {"id": "n0bbbb", "first": "Bob", "last": "Bloggs", "callsign": "N0BBBB"}]
MINUTES = """---
date: 2026-11-05
status: published
leader: n0aaaa
attendance:
  in_person:
  - n0aaaa
  - n0bbbb
---

## Community Reports
- Amy worked N0AAAA's friend on 2m <!-- member: n0aaaa -->
- Bob is fine <!-- member: n0bbbb -->
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = tmp_path
    (d / "meetings" / "2026-11-05").mkdir(parents=True)
    (d / "meetings" / "2026-11-05" / "minutes.md").write_text(MINUTES)
    (d / "chow.json").write_text(json.dumps({"nights": {"2026-11-12": {"member": "n0aaaa"}}, "order": ["n0bbbb", "n0aaaa"],
                                             "constraints": {"n0aaaa": {"not_weeks": [2]}}}))
    (d / "business.json").write_text(json.dumps({"items": [{"owner": "n0aaaa", "text": "n0aaaa stays as text? no: exact only",
                                                            "history": [{"by": "n0aaaa"}]}]}))
    (d / "email-prefs.json").write_text(json.dumps({"n0aaaa": {"digest": "off"}}))
    (d / "treasurer.json").write_text(json.dumps({"entries": [{"member": "n0aaaa", "kind": "dues", "cents": 350}], "bills": {}}))
    (d / "avatars").mkdir()
    (d / "avatars" / "n0aaaa.jpg").write_bytes(b"jpg")
    db = sqlite3.connect(d / "auth.sqlite")
    db.execute("create table passkeys (id text, member text)")
    db.execute("create table sessions (token_hash text, member text, view_as text)")
    db.execute("insert into passkeys values ('pk', 'n0aaaa')")
    db.execute("insert into sessions values ('t', 'n0aaaa', null)")
    db.commit()
    cache = d / "roster.json"
    cache.write_text(json.dumps(PEOPLE))
    for mod, attr in ((roster, "CACHE"), (membership, "ROSTER_CACHE"), (callsign, "ROSTER_CACHE")):
        monkeypatch.setattr(mod, attr, cache)
    for mod in (roster, profile, callsign, backup):
        monkeypatch.setattr(mod, "DATA", d)
    monkeypatch.setattr(roster, "PREVIEW", "test")
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", "a long test passphrase")
    return Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text())), d


def test_callsign_change_renames_everything(env):
    club, d = env
    club.admins = {"n0aaaa"}
    assert callsign.change(club, "n0aaaa", "w0amya", "2026-11-08", "n0bbbb") == "w0amya"
    assert "n0aaaa" not in club.members and club.members["w0amya"].callsign == "W0AMYA"
    assert club.canonical("n0aaaa") == "w0amya" and club.canonical("n0oldc") == "w0amya"  # both old calls still find her
    assert club.is_admin("w0amya") and club.name("n0aaaa") == "Amy Appleseed"  # club.yaml and old log entries still work
    row = json.loads((d / "roster.json").read_text())[0]
    assert row["aliases"] == ["Amelia", "N0OLDC", "N0AAAA"] and row["events"][-1]["event"] == "Callsign Change"
    sheet = json.loads((d / "preview-sheet.json").read_text())
    assert sheet["Member Roster"][1][0] == "W0AMYA" and sheet["Membership Log"][-1][3:] == ["Callsign Change", "N0AAAA to W0AMYA"]
    chow = json.loads((d / "chow.json").read_text())
    assert chow["nights"]["2026-11-12"]["member"] == "w0amya" and chow["order"] == ["n0bbbb", "w0amya"] and "w0amya" in chow["constraints"]
    biz = json.loads((d / "business.json").read_text())["items"][0]
    assert biz["owner"] == "w0amya" and biz["history"][0]["by"] == "w0amya" and biz["text"].startswith("n0aaaa")  # text untouched
    assert "w0amya" in json.loads((d / "email-prefs.json").read_text())
    assert json.loads((d / "treasurer.json").read_text())["entries"][0]["member"] == "w0amya"  # payments follow the member
    md = (d / "meetings" / "2026-11-05" / "minutes.md").read_text()
    assert "leader: w0amya" in md and "  - w0amya\n  - n0bbbb" in md and "<!-- member: w0amya -->" in md
    assert "N0AAAA's friend" in md  # text in the minutes is left as written
    con = sqlite3.connect(d / "auth.sqlite")
    assert con.execute("select member from passkeys").fetchone() == ("w0amya",)  # passkeys keep working
    assert (d / "avatars" / "w0amya.jpg").exists()
    assert any("before-w0amya" in p.name for p in (d / "backups").iterdir())  # the backup taken first


def test_callsign_change_refusals(env, monkeypatch):
    club, d = env
    for call, msg in (("N0BBBB", "another member"), ("N0AAAA", "already"), ("not a call", "doesn't look like")):
        with pytest.raises(ValueError, match=msg):
            callsign.change(club, "n0aaaa", call, "2026-11-08", "n0bbbb")
    club.members["n0bbbb"].aliases = []
    club.former_ids["w0xyzk"] = "n0bbbb"  # Bob's old call
    with pytest.raises(ValueError, match="another member"):
        callsign.change(club, "n0aaaa", "W0XYZK", "2026-11-08", "n0bbbb")
    monkeypatch.setenv("CLUB_MINUTES_BACKUP_PASSPHRASE", "")
    with pytest.raises(backup.BackupError):  # no backup, no rename
        callsign.change(club, "n0aaaa", "W0AMYA", "2026-11-08", "n0bbbb")
    assert json.loads((d / "chow.json").read_text())["order"] == ["n0bbbb", "n0aaaa"]


def test_callsign_change_from_the_page(env, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.store import Archive
    club, d = env
    club.members["n0bbbb"].role = "Secretary"  # a roster keeper
    a = Archive(d / "meetings", club)
    monkeypatch.setattr(web, "archive", a)
    for mod in (web.business, web.events):
        monkeypatch.setattr(mod, "DATA", d)
    who = {"id": "n0aaaa"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    c = TestClient(web.app)
    assert c.post("/members/n0aaaa/callsign", data={"callsign": "W0AMYA", "date": "2026-11-08"}).status_code == 403
    who["id"] = "n0bbbb"
    assert "New callsign" in c.get("/members/n0aaaa/edit").text
    r = c.post("/members/n0aaaa/callsign", data={"callsign": "W0AMYA", "date": "2026-11-08"}, follow_redirects=False)
    assert r.headers["location"].startswith("/members/w0amya/edit?tab=callsign&saved=1")
    assert a.by_slug["2026-11-05"].leader == "w0amya" and c.get("/members/w0amya").status_code == 200
    club.members["n0bbbb"].role = "Secretary"  # the roster reload dropped the test's in-memory title
    assert "error=" in c.post("/members/w0amya/callsign", data={"callsign": "N0BBBB", "date": "2026-11-08"},
                              follow_redirects=False).headers["location"]
