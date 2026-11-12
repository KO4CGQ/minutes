"""Chow list: rotation with constraints, swap requests, leader changes, meeting-night records, import."""
import datetime as dt
from pathlib import Path

import pytest

from club_minutes import chow, console
from club_minutes.club import Club
from club_minutes.meeting import Item, Meeting
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [
    {"id": "sec", "first": "Sam", "last": "Scribe", "email": "sec@example.org", "role": "Secretary", "officer": True},
    {"id": "amy", "first": "Amy", "last": "Able", "email": "amy@example.org"},
    {"id": "bob", "first": "Bob", "last": "Baker", "email": "bob@example.org"},
    {"id": "cal", "first": "Cal", "last": "Carter", "email": "cal@example.org"},
    {"id": "dee", "first": "Dee", "last": "Dunn", "email": "dee@example.org"},
    {"id": "old", "first": "Old", "last": "Timer", "status": "Silent Key"},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(chow, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(chow.auth, "send_email", lambda to, subject, body, **kw: sent.append((sorted(to), subject, body)))
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    club.info["meeting_day"] = "Thursday"
    s = chow.load()
    s["order"] = ["amy", "bob", "cal", "dee", "old"]
    return club, s, sent


THU = dt.date(2027, 1, 7)  # a first Thursday


def test_generate_follows_the_rotation_and_constraints(env):
    club, s, _ = env
    s["constraints"]["bob"] = {"not_weeks": [2]}               # can't do 2nd Thursdays
    s["constraints"]["cal"] = {"away": [["2027-01-01", "2027-01-31"]]}
    s["nights"]["2027-01-28"] = {"member": None, "status": "none", "note": "Holiday"}
    filled = chow.generate(club, s, dt.date(2027, 2, 25), "sec", today=THU - dt.timedelta(days=3))
    who = {d: s["nights"][d]["member"] for d in filled}
    assert who == {"2027-01-07": "amy",   # Jan 14 is Bob's 2nd Thursday: Cal is next but away, so Dee
                   "2027-01-14": "dee", "2027-01-21": "bob",  # Bob keeps his turn and gets the next night he can do
                   "2027-02-04": "cal", "2027-02-11": "amy",  # Cal is back in February; Jan 28 stays "no chow"
                   "2027-02-18": "bob", "2027-02-25": "cal"}  # next round in order; the Silent Key is never scheduled
    assert s["nights"]["2027-01-28"]["status"] == "none"
    s["next"] = "cal"
    assert chow.next_up(club, s) == "cal"


def test_swap_request_approved_by_the_other_member(env, monkeypatch):
    club, s, sent = env
    monkeypatch.setattr(chow.dt, "date", type("D", (dt.date,), {"today": staticmethod(lambda: THU - dt.timedelta(days=1))}))
    s["nights"] = {"2027-01-07": {"member": "amy", "status": "scheduled"}, "2027-01-14": {"member": "bob", "status": "scheduled"}}
    r = chow.request_swap(club, s, "amy", "2027-01-07", "2027-01-14", "https://x")
    assert sent[-1][0] == ["bob@example.org"] and "swap" in sent[-1][1].lower()  # only Bob is asked
    with pytest.raises(ValueError):
        chow.decide_swap(club, s, r["id"], "amy", True)       # the requester can't approve their own request
    with pytest.raises(ValueError):
        chow.request_swap(club, s, "amy", "2027-01-07", "2027-01-14")  # already waiting
    chow.decide_swap(club, s, r["id"], "bob", True, "https://x")
    assert s["nights"]["2027-01-07"]["member"] == "bob" and s["nights"]["2027-01-14"]["member"] == "amy"
    assert sent[-1][0] == ["amy@example.org", "bob@example.org", "sec@example.org"]  # both, and the Secretary
    with pytest.raises(ValueError):
        chow.decide_swap(club, s, r["id"], "bob", True)       # answered already


def test_swap_request_declined_cancelled_or_stale(env, monkeypatch):
    club, s, sent = env
    monkeypatch.setattr(chow.dt, "date", type("D", (dt.date,), {"today": staticmethod(lambda: THU - dt.timedelta(days=1))}))
    s["nights"] = {d: {"member": m, "status": "scheduled"} for d, m in
                   (("2027-01-07", "amy"), ("2027-01-14", "bob"), ("2027-01-21", "cal"))}
    with pytest.raises(ValueError):
        chow.request_swap(club, s, "amy", "2027-01-14", "2027-01-21")  # not Amy's night to give
    r = chow.request_swap(club, s, "amy", "2027-01-07", "2027-01-14")
    chow.decide_swap(club, s, r["id"], "bob", False)
    assert r["status"] == "declined" and sent[-1][0] == ["amy@example.org"] and s["nights"]["2027-01-07"]["member"] == "amy"
    r = chow.request_swap(club, s, "amy", "2027-01-07", "2027-01-21")
    chow.decide_swap(club, s, r["id"], "amy", False)
    assert r["status"] == "cancelled"
    r = chow.request_swap(club, s, "amy", "2027-01-07", "2027-01-21")
    chow.assign(club, s, "2027-01-21", "dee", "sec")             # a leader changed it meanwhile
    with pytest.raises(ValueError):
        chow.decide_swap(club, s, r["id"], "cal", True)
    assert r["status"] == "stale"


def test_meeting_night_record_and_minutes(env, tmp_path, monkeypatch):
    club, s, _ = env
    today = dt.date.today()
    d = [(today + dt.timedelta(days=7 * i)).isoformat() for i in range(4)]
    s["nights"] = {d[0]: {"member": "amy", "status": "scheduled"}, d[1]: {"member": "bob", "status": "scheduled"},
                   d[2]: {"member": "cal", "status": "scheduled"}, d[3]: {"member": "dee", "status": "scheduled"}}
    chow.record(club, s, d[0], "provided", "sec", note="brownies")
    assert chow.tonight_line(club, d[0], s["nights"][d[0]]) == "Chow tonight provided by Amy Able (brownies)."
    chow.record(club, s, d[0], "missed", "sec", note="ignored")
    assert chow.tonight_line(club, d[0], s["nights"][d[0]]) == "Chow tonight: Amy Able did not provide chow."
    chow.record(club, s, d[0], "covered", "sec", covered_by="dee", note="pizza delivered")
    assert s["nights"][d[0]] == {"member": "amy", "status": "covered", "covered_by": "dee", "note": "pizza delivered"}
    chow.swap(club, s, d[1], d[2], "sec")
    chow.save(s)
    # Changes are logged with today's date, and the meeting is today: only the latest record counts.
    lines = chow.minutes_section(club, s, chow.history(), d[0])
    assert lines[0] == "Chow tonight provided by Dee Dunn for Amy Able (pizza delivered)."
    assert lines[1].startswith("Chow swap: Bob Baker now has")
    assert lines[2:] == [chow.describe(club, x, s["nights"][x]) for x in d[1:]]

    meetings = tmp_path / "meetings"
    meetings.mkdir()
    Meeting(date=today, status="draft", sections={"Upcoming Chow": [Item("stale line from the start")]}).save(meetings)
    a = Archive(meetings, club)
    console.submit(a, d[0])
    assert [i.text for i in a.by_slug[d[0]].items("Upcoming Chow")] == lines


def test_describe_and_missed(env):
    club, s, _ = env
    chow.set_none(s, "2026-11-26", "Thanksgiving", "sec")
    assert chow.describe(club, "2026-11-26", s["nights"]["2026-11-26"]) == "Nov 26: no chow (Thanksgiving)"
    s["nights"]["2026-12-03"] = {"member": "amy", "status": "scheduled"}
    chow.record(club, s, "2026-12-03", "missed", "sec")
    assert s["nights"]["2026-12-03"]["status"] == "missed"
    with pytest.raises(ValueError):
        chow.record(club, s, "2026-12-03", "bogus", "sec")


def test_import_matches_misspelled_names_and_fixes_dates(env, tmp_path):
    import openpyxl
    club, _, _ = env
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["", "26 Chow List"])
    ws.append(["Able, Amy", dt.datetime(2026, 11, 5)])
    ws.append(["Bob Bakker", dt.datetime(2026, 11, 12)])     # misspelled
    ws.append(["", dt.datetime(2026, 11, 25)])               # a Wednesday, and blank: no chow
    ws.append(["Carter", None, "3-Dec"])                     # date typed as text, in column C
    ws.append(["Nobody, Atall", dt.datetime(2026, 12, 10)])
    p = tmp_path / "chow.xlsx"
    wb.save(p)
    s, problems = chow.import_xlsx(club, p, ["Able", "Baker", "Carter", "Dunn"], "sec", 2026)
    assert {d: x["member"] for d, x in s["nights"].items()} == {
        "2026-11-05": "amy", "2026-11-12": "bob", "2026-11-26": None, "2026-12-03": "cal"}
    assert s["nights"]["2026-11-26"] == {"member": None, "status": "none", "note": "Thanksgiving"}
    # Sam wasn't listed: he joins at the end of the round, just before Dee (next after Cal's Dec 3)
    assert s["order"] == ["amy", "bob", "cal", "sec", "dee"] and s["pending"] == []
    assert any("Nov 25" in p for p in problems) and any("Nobody" in p for p in problems)


def test_chow_page_permissions(env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import web
    club, s, _ = env
    s["nights"] = {"2099-01-01": {"member": "amy", "status": "scheduled"}, "2099-01-08": {"member": "bob", "status": "scheduled"}}
    chow.save(s)
    monkeypatch.setattr(web, "archive", Archive(tmp_path, club))
    who = {"id": "amy"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    c = TestClient(web.app)
    page = c.get("/chow").text
    assert "Amy Able" in page and "Ask to swap" in page and "Fill the schedule" not in page  # a member
    assert c.post("/chow/assign", data={"date": "2099-01-01", "member": "bob"}).status_code == 403
    assert c.post("/chow/swap", data={"a": "2099-01-01", "b": "2099-01-08"}).status_code == 403
    c.post("/chow/request", data={"mine": "2099-01-01", "theirs": "2099-01-08"})
    who["id"] = "bob"
    assert "asks to swap" in c.get("/chow").text
    rid = chow.load()["swaps"][0]["id"]
    c.post(f"/chow/requests/{rid}/answer", data={"approve": 1})
    assert chow.load()["nights"]["2099-01-01"]["member"] == "bob"
    assert c.get("/chow/rotation").status_code == 404                    # a member has only the schedule
    club.members["cal"].minutes_editor = True  # can lead a meeting and record chow, but not change the schedule
    who["id"] = "cal"
    assert "Fill the schedule" not in c.get("/chow").text and c.get("/chow/rotation").status_code == 404
    assert c.post("/chow/assign", data={"date": "2099-01-08", "member": "cal"}).status_code == 403
    assert c.post("/chow/record", data={"date": "2099-01-01", "status": "provided"},
                  headers={"Accept": "application/json"}).status_code == 200
    who["id"] = "sec"  # an officer can change the schedule
    assert "Swap two nights" in c.get("/chow").text and "Fill the schedule" in c.get("/chow/rotation").text
    assert "Past nights" in c.get("/chow/history").text
    assert c.post("/chow/assign", data={"date": "2099-01-08", "member": "cal"}, follow_redirects=False).status_code == 303
    assert chow.load()["nights"]["2099-01-08"]["member"] == "cal"


def test_new_members_join_at_the_end_of_the_round(env):
    club, s, _ = env
    s["nights"] = {"2027-01-07": {"member": "amy", "status": "scheduled"}, "2027-01-14": {"member": "bob", "status": "scheduled"}}
    assert chow.next_up(club, s) == "cal"
    chow.add_to_rotation(club, s, "sec", "sec")  # everyone from Cal round to Bob goes first
    assert s["order"] == ["amy", "bob", "sec", "cal", "dee", "old"] and chow.next_up(club, s) == "cal"
    chow.generate(club, s, dt.date(2027, 2, 18), "sec", today=THU)
    assert [s["nights"][d]["member"] for d in sorted(s["nights"])] == ["amy", "bob", "cal", "dee", "amy", "bob", "sec"]


def test_skip_next_turn(env):
    club, s, _ = env
    chow.set_constraints(s, "bob", [], [], "covered his turn early", "sec", skip=1)
    filled = chow.generate(club, s, dt.date(2027, 2, 4), "sec", today=THU - dt.timedelta(days=3))
    assert [s["nights"][d]["member"] for d in filled] == ["amy", "cal", "dee", "amy", "bob"]  # Bob passed over once only
    assert s["constraints"]["bob"]["skip"] == 0 and chow.history()[0]["skipped"] == ["bob"]


def test_odd_chow_requests_are_refused_not_errors(tmp_path, monkeypatch):
    import datetime as dt
    from fastapi.testclient import TestClient
    from test_calview import signed_in
    from club_minutes import calview, web
    monkeypatch.setattr(chow, "DATA", tmp_path)
    monkeypatch.setattr(calview, "DATA", tmp_path)
    m, *_ = signed_in(tmp_path, monkeypatch, "kd8aaa")
    t = TestClient(web.app, raise_server_exceptions=False, cookies=m.cookies)
    assert t.post("/chow/assign", data={"date": "junk", "member": "kd8bbb"}).status_code == 400
    assert t.post("/chow/none", data={"date": "junk"}).status_code == 400
    d = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    s = chow.load()
    s["nights"], s["order"] = {d: {"member": "kd8bbb"}}, ["kd8aaa", "kd8bbb"]
    chow.save(s)
    r = t.post("/chow/swap", data={"a": d, "b": d}, follow_redirects=False)
    assert "different%20nights" in r.headers["location"] and not chow.history(5)  # no swap logged, nobody emailed
    r = t.post("/chow/generate", data={"until": "2400-01-01"}, follow_redirects=False)
    assert "two%20years" in r.headers["location"] and list(chow.load()["nights"]) == [d]


def test_chow_list_pdf_and_email(env, tmp_path, monkeypatch):
    """Anyone can print the list; officers email it, and the page tells them when it has changed since."""
    from fastapi.testclient import TestClient
    from club_minutes import prefs, web
    club, s, sent = env
    s["nights"] = {"2099-01-01": {"member": "amy", "status": "scheduled"},
                   "2099-01-08": {"member": None, "status": "none", "note": "Snowed in"}}
    chow.save(s)
    monkeypatch.setattr(web, "archive", Archive(tmp_path, club))
    prefs.save("bob", {"chowlist": "off"})
    html = []  # the template as rendered; the real PDF too where WeasyPrint's system libraries (pango) are installed
    real_pdf = web.html_to_pdf

    def to_pdf(tpl, **ctx):
        html.append(web.templates.get_template(tpl).render(club=club, pdf=True, **ctx))
        try:
            return real_pdf(tpl, **ctx)
        except web.HTTPException:  # no pango here
            return b"%PDF"
    monkeypatch.setattr(web, "html_to_pdf", to_pdf)
    who = {"id": "amy"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    c = TestClient(web.app)
    r = c.get("/chow/list.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert "Amy Able" in html[0] and "No chow (Snowed in)" in html[0]
    if len(r.content) > 4:  # a real PDF: its text says the same
        import io
        from pypdf import PdfReader
        text = PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
        assert "Amy Able" in text and "No chow (Snowed in)" in text
    assert c.post("/chow/email").status_code == 403 and "Email the list" not in c.get("/chow").text
    who["id"] = "sec"
    assert "Not emailed yet" in c.get("/chow").text
    c.post("/chow/email")
    to, subject, _ = sent[-1]
    assert "bob@example.org" not in to and "amy@example.org" in to and "chow list" in subject
    assert not chow.list_changed(chow.load()) and "no changes since" in c.get("/chow").text
    c.post("/chow/assign", data={"date": "2099-01-01", "member": "bob"})
    assert "has changed since" in c.get("/chow").text
    # a night passing isn't a change
    assert not chow.list_changed({**chow.load(), "nights": {}, "emailed": {"nights": {"2000-01-01": ["amy", "scheduled", ""]}}})


def test_the_chow_list_goes_as_a_pdf_or_a_link_as_each_chose(monkeypatch):
    from club_minutes import auth
    sent = []
    monkeypatch.setattr(auth, "send_email", lambda to, subject, body, attachments=(), **kw: sent.append((list(to), body, len(attachments))) or len(to))
    s = chow.load()
    n = chow.email_list(Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[]), s, ("list.pdf", b"%PDF", ), ["amy@example.org"],
                        "kd8aaa", "https://minutes.example.org", ["bob@example.org"])
    assert n == 2 and sent[0][0] == ["amy@example.org"] and sent[0][2] == 1
    assert sent[1][0] == ["bob@example.org"] and sent[1][2] == 0 and "https://minutes.example.org/chow" in sent[1][1]


def test_with_the_chow_module_off_it_leaves_no_trace(env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import calview, prefs, web
    club, s, _ = env
    s["nights"] = {"2099-01-01": {"member": "amy", "status": "scheduled"}, "2099-01-08": {"member": "bob", "status": "scheduled"}}
    chow.save(s)
    a = Archive(tmp_path, club)
    monkeypatch.setattr(web, "archive", a)
    who = {"id": "amy"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: who["id"])
    c = TestClient(web.app)
    c.post("/chow/request", data={"mine": "2099-01-01", "theirs": "2099-01-08"})
    lo, hi = dt.date(2098, 12, 25), dt.date(2099, 1, 15)

    def looks():
        who["id"] = "bob"  # has a swap request waiting
        return {"menu": 'href="/chow"' in c.get("/minutes").text, "home": "chow" in c.get("/").text.lower(),
                "prefs": "chow" in c.get("/profile/email").text.lower(),
                "calendar": any(e.kind == "chow" for e in calview.entries(club, a, "bob", lo, hi)[0]),
                "subscribe": "Chow" in c.get("/calendar/subscribe").text, "search": "Chow" in c.get("/search?q=amy").text,
                "page": c.get("/chow").status_code == 200, "api": c.post("/chow/request", data={}).status_code != 404,
                "reminders": bool(prefs.chow_reminders(club, dt.date(2098, 12, 29), "https://x")),
                "console": "Upcoming Chow" in console.start(a, dt.date(2098, 12, 31), "sec").sections
                           and bool(console.context(a, console.start(a, dt.date(2098, 12, 31), "sec"))["chow"])}

    on = looks()
    assert all(on.values()), on  # each check sees chow while the module is on, so none passes by accident
    console.discard(a, "2098-12-31")
    club.modules["chow"] = False
    off = looks()
    assert not any(off.values()), off


def test_not_provided_says_the_clubs_real_fee_and_section(env, monkeypatch):
    from club_minutes import treasurer
    club, *_ = env
    monkeypatch.setattr(treasurer, "load", lambda: {})
    monkeypatch.setattr(treasurer, "members_see", lambda s: False)
    assert console._missed_note(club, THU) == ""  # dues not tracked: no fee to mention
    monkeypatch.setattr(treasurer, "members_see", lambda s: True)
    monkeypatch.setattr(treasurer, "config", lambda s: {"rates": {"missed_chow": [{"from": "2000-01-01", "cents": 1250}]}})
    assert console._missed_note(club, THU) == "$12.50 is added to their dues."
    club.bylaws = {"refs": {"missed_chow": "  VII.F  "}}
    assert console._missed_note(club, THU) == "$12.50 is added to their dues (bylaws VII.F)."
    club.bylaws = {"refs": {"missed_chow": "x" * 99}}
    assert club.cite("missed_chow") == f" (bylaws {'x' * 20})" and club.cite("nothing") == ""  # kept short; unset: nothing
