"""A new callsign: the sheet and aliases change, the member's id and every record keep as they are; rename_id finishes old renames."""
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
    return Club(FIX / "test-club.yaml", roster=json.loads(cache.read_text())), d


def test_callsign_change_keeps_the_id_and_renames_nothing(env):
    club, d = env
    club.admins = {"n0aaaa"}
    before = {p: p.read_bytes() for p in d.rglob("*") if p.is_file() and p.name not in ("roster.json", "preview-sheet.json")}
    assert callsign.change(club, "n0aaaa", "w0amya", "2026-11-08", "n0bbbb") is None
    assert club.members["n0aaaa"].callsign == "W0AMYA" and "w0amya" not in club.members  # the id stays
    assert club.resolve("w0amya") == club.resolve("N0AAAA") == club.resolve("n0oldc") == "n0aaaa"  # new, old and earlier calls
    assert club.is_admin("n0aaaa") and club.canonical("w0amya") == "n0aaaa" and club.member_url("n0aaaa") == "/members/w0amya"
    row = json.loads((d / "roster.json").read_text())[0]
    assert row["id"] == "n0aaaa" and row["aliases"] == ["Amelia", "N0OLDC", "N0AAAA"] and row["events"][-1]["event"] == "Callsign Change"
    sheet = json.loads((d / "preview-sheet.json").read_text())
    assert sheet["Member Roster"][1][0] == "W0AMYA" and sheet["Membership Log"][-1][3:] == ["Callsign Change", "N0AAAA to W0AMYA"]
    assert before == {p: p.read_bytes() for p in before}  # no file renamed, rewritten or added (no backup either)
    assert profile.history()[0]["member"] == "n0aaaa"


def test_an_old_callsign_never_takes_over_its_first_holder(env):
    """Security review, 2026-11-22: after N0AAAA became W0AMYA, a new member given N0AAAA would have taken over the
    id n0aaaa (club.yaml admins:, sessions). The id always means its own member, and adding that call is refused."""
    club, d = env
    callsign.change(club, "n0aaaa", "w0amya", "2026-11-08", "n0bbbb")
    form = {"callsign": "N0AAAA", "first": "Jane", "last": "Roe", "email": "jane.roe@example.org", "license_class": "General",
            "joined": "2026-11-20"}
    with pytest.raises(ValueError, match="was .*'s callsign"):
        membership.add_member(club, form, by="n0bbbb")
    rows = json.loads((d / "roster.json").read_text())  # even if the roster gets that call some other way (edited by hand)
    rows.append({**rows[1], "id": "m1abcde", "callsign": "N0AAAA", "first": "Jane", "last": "Roe", "aliases": []})
    club._load(rows)
    assert club.canonical("n0aaaa") == "n0aaaa" and club.resolve("w0amya") == "n0aaaa"


def test_callsign_change_refusals(env):
    club, _d = env
    for call, msg in (("N0BBBB", "another member"), ("N0AAAA", "already"), ("not a call", "doesn't look like")):
        with pytest.raises(ValueError, match=msg):
            callsign.change(club, "n0aaaa", call, "2026-11-08", "n0bbbb")
    club.members["n0bbbb"].aliases = []
    club.former_ids["w0xyzk"] = "n0bbbb"  # Bob's old call
    with pytest.raises(ValueError, match="another member"):
        callsign.change(club, "n0aaaa", "W0XYZK", "2026-11-08", "n0bbbb")


def test_member_pages_answer_to_a_callsign_a_former_callsign_or_an_id(env, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.store import Archive
    club, d = env
    club.members["n0bbbb"].role = "Secretary"  # a roster keeper
    a = Archive(d / "meetings", club)
    monkeypatch.setattr(web, "archive", a)
    who = {"id": "n0aaaa"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    c = TestClient(web.app)
    assert c.post("/members/n0aaaa/callsign", data={"callsign": "W0AMYA", "date": "2026-11-08"}).status_code == 403
    who["id"] = "n0bbbb"
    r = c.post("/members/n0aaaa/callsign", data={"callsign": "W0AMYA", "date": "2026-11-08"}, follow_redirects=False)
    assert r.headers["location"].startswith("/members/w0amya/edit?tab=callsign&saved=1")
    club.members["n0bbbb"].role = "Secretary"  # the roster reload dropped the test's in-memory title
    for who_url in ("w0amya", "W0AMYA", "n0aaaa", "n0oldc"):  # the callsign, any capitals, the id, a former callsign
        assert "Amy Appleseed" in c.get(f"/members/{who_url}").text, who_url
    assert 'href="/members/w0amya' in c.get("/roster").text and 'href="/members/n0aaaa' not in c.get("/roster").text
    assert c.get("/members/nobody").status_code == 404
    assert "error=" in c.post("/members/w0amya/callsign", data={"callsign": "N0BBBB", "date": "2026-11-08"},
                              follow_redirects=False).headers["location"]


def test_rename_id_finishes_an_old_partial_rename(env):
    _club, d = env
    callsign.rename_id("n0aaaa", "w0amya", d)  # what the command runs; safe to run again
    callsign.rename_id("n0aaaa", "w0amya", d)
    assert not [f for f in ("chow.json", "email-prefs.json", "treasurer.json") if "n0aaaa" in (d / f).read_text()]
    assert (d / "avatars" / "w0amya.jpg").exists() and "w0amya" in (d / "meetings" / "2026-11-05" / "minutes.md").read_text()
    assert json.loads((d / "chow.json").read_text())["nights"]["2026-11-12"]["member"] == "w0amya"
    assert sqlite3.connect(d / "auth.sqlite").execute("select member from passkeys").fetchone() == ("w0amya",)


def test_rename_follows_recorder_corrections_review_and_earlier_versions(tmp_path):
    md = ("---\ndate: 2026-11-05\nrecorder: n0aaaa\ncorrections:\n- version: 2\n  at: 1\n  by: n0aaaa\n  reason: fix\n"
          "reviewed:\n  by: n0aaaa\n  at: 1\n---\n\n## Old Business\n- Keep n0aaaa as text\n")
    for name in ("minutes.md", "minutes.v1.md"):
        (tmp_path / "meetings" / "2026-11-05").mkdir(parents=True, exist_ok=True)
        (tmp_path / "meetings" / "2026-11-05" / name).write_text(md)
    assert callsign.rename_id("n0aaaa", "w0amya", tmp_path)["minutes"] == 2
    for name in ("minutes.md", "minutes.v1.md"):
        out = (tmp_path / "meetings" / "2026-11-05" / name).read_text()
        assert out.count("w0amya") == 3 and "Keep n0aaaa as text" in out and "recorder: w0amya" in out
