"""Recover admin access by the officers (FIXES → Problem 5): a majority (at least 2) and the nominee's acceptance,
then 7 days; an admin's objection makes it two-thirds and 14 days; every member told; suspended admins don't count."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import auth as authmod
from club_minutes import club as clubmod
from club_minutes import recovery, web
from club_minutes.auth import Auth
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
ROSTER = [{"id": i, "first": f, "last": "Doe", "email": f"{i}@example.org", "role": r} for i, f, r in (
    ("pres", "Jane", "President"), ("vp", "John", "Vice President"), ("sec", "Sam", "Secretary"), ("tre", "Tess", "Treasurer"),
    ("tru", "Terry", "Trustee"), ("n0call", "Pat", None), ("adm", "Ann", None))]
DAY = 86400


@pytest.fixture
def club(tmp_path, monkeypatch):
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(recovery, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(authmod, "send_email", lambda to, subject, body, **kw: sent.append((to, subject, body)))
    c = Club(FIX / "test-club.yaml", roster=ROSTER)
    c.offices = ["President", "Vice President", "Secretary", "Treasurer", {"title": "Trustee", "officer": False, "seats": 3}]
    c.admins = {"adm"}
    c.sent = sent
    return c


def test_majority_acceptance_and_the_wait(club):
    assert recovery.officers(club) == ["pres", "sec", "tre", "vp"]  # the trustee isn't an officer
    with pytest.raises(ValueError, match="Only an officer"):
        recovery.start(club, "n0call", "n0call", [], "", "https://x")
    r = recovery.start(club, "pres", "n0call", ["adm"], "Approved at the board meeting of Nov 1", "https://x")
    assert len(club.sent[-1][0]) == 7 and "Pat Doe to become an admin, and Ann Doe suspended" in club.sent[-1][2]  # everyone
    with pytest.raises(ValueError, match="already under way"):
        recovery.start(club, "vp", "tru", [], "", "https://x")
    with pytest.raises(ValueError, match="Only an officer"):
        recovery.approve(club, "tru", "https://x")
    recovery.approve(club, "vp", "https://x")
    st = recovery.state(club, recovery.load())
    assert (st["need"], st["have"], st["ready_at"]) == (3, 2, None)  # a majority of 4 is 3
    recovery.approve(club, "sec", "https://x")
    assert recovery.state(club, recovery.load())["ready_at"] is None  # not accepted yet
    with pytest.raises(ValueError, match="Only the member named"):
        recovery.accept(club, "vp", "https://x")
    recovery.accept(club, "n0call", "https://x")
    ready = recovery.state(club, recovery.load())["ready_at"]
    assert ready == recovery.load()["accepted"] + 7 * DAY and r["by"] == "pres"  # the acceptance came last
    assert not recovery.tick(club, "https://x", now=ready - 60) and recovery.load()  # not before the wait
    assert recovery.tick(club, "https://x", now=ready + 1)
    assert club.is_admin("n0call") and not club.is_admin("adm") and club.suspended == {"adm"} and recovery.load() is None
    assert "adm@example.org" in club.sent[-1][0] and "only another admin can lift that" in club.sent[-1][2]


def test_an_objection_raises_it_to_two_thirds_and_14_days(club):
    recovery.start(club, "pres", "n0call", [], "", "https://x")
    recovery.approve(club, "vp", "https://x")
    recovery.approve(club, "sec", "https://x")
    recovery.accept(club, "n0call", "https://x")
    with pytest.raises(ValueError, match="Only an admin"):
        recovery.object_(club, "tre", "https://x")
    recovery.object_(club, "adm", "https://x")
    st = recovery.state(club, recovery.load())
    assert st["need"] == 3 and st["objected"]  # two-thirds of 4, rounded up, is 3: still met
    assert st["ready_at"] == recovery.load()["accepted"] + 14 * DAY
    assert "two-thirds" in club.sent[-1][2]


def test_two_officers_at_least_and_calling_it_off(club):
    club.offices = ["President", {"title": "Trustee", "officer": False, "seats": 3}]  # one officer only
    recovery.start(club, "pres", "n0call", [], "", "https://x")
    recovery.accept(club, "n0call", "https://x")
    r = recovery.load()
    assert recovery.state(club, r)["need"] == 2 and not recovery.tick(club, "https://x", now=r["at"] + 20 * DAY)  # one officer can't
    with pytest.raises(ValueError, match="Only an officer"):
        recovery.cancel(club, "n0call", "https://x")
    recovery.cancel(club, "pres", "https://x")
    assert recovery.load() is None and "called off" in club.sent[-1][2]


def test_the_pages_confirm_first_and_a_suspended_admin_cant_lift_it(club, tmp_path, monkeypatch):
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)

    def as_(who):
        t = TestClient(web.app, client=("203.0.113.6", 5000))
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    pres = as_("pres")
    assert "Make this member an admin" in pres.get("/recover").text
    assert "Make this member an admin" not in as_("n0call").get("/recover").text  # members see where it stands, no form
    r = pres.post("/recover/act", data={"do": "start", "nominee": "n0call"}, follow_redirects=False)
    assert r.headers["location"] == "/recover/confirm" and recovery.load() is None  # confirm it's you first
    monkeypatch.setattr(web, "_recover_confirmed", lambda request: True)
    r = pres.post("/recover/act", data={"do": "start", "nominee": "nobody"}, follow_redirects=False)  # refused
    assert r.headers["location"] == "/recover" and recovery.load() is None                      # not in the link
    assert 'class="msg err"' in pres.get("/recover").text and 'class="msg err"' not in pres.get("/recover").text  # once
    p = pres.get("/recover?error=Your+account+is+locked.+Call+555-0100&note=Send+your+passkey").text
    assert "555-0100" not in p and "Send your passkey" not in p  # words from a link are never shown
    pres.post("/recover/act", data={"do": "start", "nominee": "n0call", "suspend": ["adm"]})
    assert recovery.load()["nominee"] == "n0call"
    assert "Done. Every member has been told." in pres.get("/recover?note=done").text
    assert "Approve" not in pres.get("/recover").text.split("<form")[1].split("</form>")[0]  # the starter already counts
    club.save_suspended({"adm"})
    r = as_("adm").post("/admin/security/admins/lift", data={"member": "adm"})
    assert r.status_code == 404 and club.suspended == {"adm"}  # a suspended admin isn't one


def test_no_suspension_without_the_new_admin(club):
    """Security sweep: if the nominee is no longer active when the wait ends, it ends without touching any admin."""
    recovery.start(club, "pres", "n0call", ["adm"], "", "https://x")
    recovery.approve(club, "vp", "https://x")
    recovery.approve(club, "sec", "https://x")
    recovery.accept(club, "n0call", "https://x")
    club.members["n0call"].status = "Inactive"
    assert not recovery.tick(club, "https://x", now=9e12)
    assert club.is_admin("adm") and not club.suspended and recovery.load() is None and "ended without changing" in club.sent[-1][2]


def test_a_new_callsign_keeps_a_suspension_and_an_open_recovery(club, tmp_path):
    """Security sweep: renaming a suspended admin (or the nominee) kept the old id in admins-suspended.json and
    recovery.json, which lifted the suspension."""
    from club_minutes import callsign
    club.save_suspended({"adm"})
    recovery.start(club, "pres", "n0call", [], "", "https://x")
    callsign.rename_id("adm", "n0newa", tmp_path)
    callsign.rename_id("n0call", "n0newc", tmp_path)
    assert club.suspended == {"n0newa"} and recovery.load()["nominee"] == "n0newc"


def test_it_lapses_after_30_days_and_a_majority_can_call_it_off(club):
    """Jim, 2026-11-15: one officer, or a nominee who never accepts, can't hold it forever."""
    r = recovery.start(club, "pres", "n0call", [], "", "https://x")
    assert not recovery.tick(club, "https://x", now=r["at"] + 29 * DAY) and recovery.load()
    assert not recovery.tick(club, "https://x", now=r["at"] + 30 * DAY) and recovery.load() is None
    assert "ended without changing any admin" in club.sent[-1][2]
    recovery.start(club, "pres", "n0call", [], "", "https://x")
    with pytest.raises(ValueError, match="Only an officer"):
        recovery.cancel(club, "tru", "https://x")
    assert not recovery.cancel(club, "vp", "https://x") and recovery.load()  # 1 of the 3 needed (a majority of 4)
    assert not recovery.cancel(club, "sec", "https://x")
    assert recovery.cancel(club, "tre", "https://x") and recovery.load() is None
    assert "a majority of the officers" in club.sent[-1][2]


def test_each_emailed_link_works_only_for_what_it_was_sent_for(club, tmp_path):
    a = Auth(club, tmp_path / "auth.sqlite")
    admin_link, recover_link = a.step_up_link("pres"), a.step_up_link("pres", "recover-email")
    assert not a.use_step_up_link(admin_link, "pres", "recover-email") and not a.use_step_up_link(recover_link, "pres")
    assert a.use_step_up_link(a.step_up_link("pres", "recover-email"), "pres", "recover-email")


def test_old_sign_in_records_are_pruned(club, tmp_path):
    import time
    a = Auth(club, tmp_path / "auth.sqlite")
    a.db.execute("insert into events (at, ip, kind) values (?, ?, ?)", (int(time.time()) - 91 * DAY, "203.0.113.9", "bad_link"))
    a.db.execute("insert into events (at, ip, kind) values (?, ?, ?)", (int(time.time()), "203.0.113.9", "bad_link"))
    assert a.prune() == 1 and len(a.events()) == 1
    now = int(time.time())
    for at, who in ((now - 200 * DAY, "pres"), (now - 100 * DAY, "pres"), (now - 100 * DAY, "n0call"), (now, "n0call")):
        a.db.execute("insert into signins (at, member, method, ip) values (?,?,?,?)", (at, who, "passkey", "203.0.113.9"))
    assert a.prune() == 2  # pres keeps the latest, if old ("last signed in"); n0call's old one goes
    assert sorted((r["member"], r["at"]) for r in a.signins()) == [("n0call", now), ("pres", now - 100 * DAY)]


def test_old_links_challenges_and_events_go_after_90_days_but_89_stay(club, tmp_path):
    import time
    a = Auth(club, tmp_path / "auth.sqlite")
    now = int(time.time())
    for days, tag in ((91, "old"), (89, "new")):
        at = now - days * DAY
        a.db.execute("insert into links (token_hash, member, expires, next, used, created, ip) values (?,?,?,?,?,?,?)", (tag, "pres", at, "/", 1, at, "203.0.113.9"))
        a.db.execute("insert into challenges (id, challenge, member, kind, expires) values (?,?,?,?,?)", (tag, b"x", "pres", "login", at))
        a.db.execute("insert into events (at, ip, kind) values (?,?,?)", (at, "203.0.113.9", "bad_link"))
        a.db.execute("insert into signins (at, member, method, ip) values (?,?,?,?)", (at, "pres", "email link", "203.0.113.9"))
    a.db.execute("insert into signins (at, member, method, ip) values (?,?,?,?)", (now - 300 * DAY, "adm", "email link", "203.0.113.9"))
    assert a.prune() == 4  # one old link, challenge, event and sign-in
    assert [r[0] for r in a.db.execute("select token_hash from links")] == ["new"]
    assert [r[0] for r in a.db.execute("select id from challenges")] == ["new"]
    assert len(a.events()) == 1 and sorted(r["member"] for r in a.signins()) == ["adm", "pres"]  # adm's only one is its latest


def test_inactive_and_silent_key_members_get_no_recovery_email_and_no_sign_in(club, tmp_path):
    club.members["n0call"].status = "Inactive"
    club.members["adm"].status = "Silent Key"  # an admin, suspended below: still no email
    club.members["tru"].status = "Inactive"
    recovery.start(club, "pres", "tre", ["adm"], "", "https://x")
    (to, *_), = club.sent
    assert not {"n0call@example.org", "adm@example.org", "tru@example.org"} & set(to) and "pres@example.org" in to
    a = Auth(club, tmp_path / "auth.sqlite")
    tokens = [a.new_session(m) for m in ("n0call", "adm", "pres")]
    assert [a.member_for(t) for t in tokens] == [None, None, "pres"]  # a live session of theirs gets nothing
    assert a.member_by_email("n0call@example.org") is None  # and no sign-in link is made for them
    a.send_link("n0call@example.org", ip="203.0.113.9")
    assert not a.db.execute("select 1 from links").fetchone()


def test_damaged_state_files_fall_back_instead_of_breaking_every_page(club, tmp_path):
    for name in ("roles.json", "admins.json", "admins-suspended.json", "recovery.json"):
        (tmp_path / name).write_text('["torn')
    assert club._picked() is None and club.app_admins == set() and club.suspended == set() and recovery.load() is None
    (tmp_path / "email-log.jsonl").write_text('{"at": 1}\n{"at": 2, "to\n{"at": 3}\n')
    assert [e["at"] for e in authmod.read_log(tmp_path / "email-log.jsonl", 500)] == [1, 3]
