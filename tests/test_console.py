import datetime as dt

import pytest
from pathlib import Path

from club_minutes import console
from club_minutes.club import Club
from club_minutes.meeting import Item, Meeting
from club_minutes.store import Archive

club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml")


def archive_with_prev(tmp_path):
    prev = Meeting(date=dt.date(2026, 11, 5), status="published", in_person=["doe"],
                   sections={"Old Business": [Item("Fix the tower")], "New Business": [Item("Plan field day")],
                             "Upcoming Events": [Item("VE testing")],
                             "Community Reports": [Item("Sam built a dipole", "kim")]})
    prev.save(tmp_path)
    return Archive(tmp_path, club)


def test_console_knows_when_club_events_are_public(tmp_path, monkeypatch):
    a = archive_with_prev(tmp_path)
    m = console.start(a, dt.date(2026, 11, 12), "doe")
    assert console.context(a, m)["events_public"] is False
    monkeypatch.setitem(a.club.info, "public_calendars", ["events", "community"])
    assert console.context(a, m)["events_public"] is True  # the Upcoming Events hint warns it's public


def test_start_carries_business_forward(tmp_path):
    a = archive_with_prev(tmp_path)
    m = console.start(a, dt.date(2026, 11, 12), "doe")
    assert m.status == "draft" and m.leader == "doe"
    assert [i.text for i in m.items("Old Business")] == ["Fix the tower", "Plan field day"]
    assert [i.text for i in m.items("Upcoming Events")] == ["VE testing"]
    ctx = console.context(a, m)
    assert ctx["history"]["kim"] == [("2026-11-05", "Sam built a dipole")]


def test_past_guests_exclude_members_and_sort_recent_first(tmp_path):
    for d, guests in ((dt.date(2026, 10, 1), [{"name": "Pat Guest", "callsign": "N0YABC"}, {"name": "Rick Roe"}]),
                      (dt.date(2026, 10, 8), [{"name": "Lee Visitor"}])):
        Meeting(date=d, status="published", in_person=["roe"] if d.day == 8 else [], guests=guests).save(tmp_path)
    a = Archive(tmp_path, club)
    m = console.start(a, dt.date(2026, 11, 12), "doe")
    past = console.context(a, m)["past_guests"]
    assert [g["name"] for g in past] == ["Lee Visitor", "Pat Guest"]  # Rick Roe became a member
    assert past[1] == {"name": "Pat Guest", "callsign": "N0YABC", "visits": 1, "last": "2026-10-01"}


def test_apply_saves_and_drops_never_record_guests(tmp_path):
    a = archive_with_prev(tmp_path)
    console.start(a, dt.date(2026, 11, 12), "doe")
    data = {"meeting": {"in_person": ["doe", "kim"], "remote": ["kim", "roe"],
                        "guests": [{"name": "Pat Guest", "callsign": "N0YABC", "online": True}, {"name": "Tiny Visitor"}],
                        "visitors": 1,
                        "sections": {"Community Reports": [{"text": "Sam got on HF", "member": "kim"}, {"text": " "}]}},
            "state": {"done": ["kim"]}}
    m = console.apply(a, "2026-11-12", data)
    assert m.in_person == ["doe", "kim"] and m.remote == ["roe"]  # can't be both
    assert m.guests == [{"name": "Pat Guest", "callsign": "N0YABC", "online": True}]  # an online guest stays online
    assert m.visitors == 1
    assert [(i.text, i.member) for i in m.items("Community Reports")] == [("Sam got on HF", "kim")]
    console.submit(a, "2026-11-12")
    try:
        console.apply(a, "2026-11-12", data)
        raise AssertionError("published minutes must not be overwritten")
    except ValueError:
        pass


def test_discard_only_drafts(tmp_path):
    a = archive_with_prev(tmp_path)
    console.start(a, dt.date(2026, 11, 12), "doe")
    console.discard(a, "2026-11-12")
    assert "2026-11-12" not in a.by_slug
    try:
        console.discard(a, "2026-11-05")
        raise AssertionError("published minutes must not be discarded")
    except ValueError:
        pass


class FakeSMTP:
    """Stands in for smtplib.SMTP: records messages instead of sending them."""
    outbox: list = []

    def __init__(self, host, port, timeout=None): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def starttls(self): pass
    def login(self, user, password): pass
    def send_message(self, msg): FakeSMTP.outbox.append(msg)


def mail_archive(tmp_path, monkeypatch, only_to):
    from club_minutes import auth
    monkeypatch.setattr(auth.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "club@example.org")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "abcd efgh")
    FakeSMTP.outbox = []
    c = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[
        {"id": "doe", "first": "Jane", "last": "Doe", "email": "jane@example.org", "minutes_editor": True},
        {"id": "kim", "first": "Sam", "last": "Kim", "email": "sam@example.org"},
        {"id": "roe", "first": "Rick", "last": "Roe", "email": "rick@example.org", "status": "Silent Key"},
        {"id": "dummy", "first": "Jane", "last": "Dummy"}])
    c.email = {"only_to": only_to} if only_to is not None else {}
    Meeting(date=dt.date(2026, 11, 12), status="published", leader="doe").save(tmp_path)
    return Archive(tmp_path, c)


def test_email_minutes_test_mode_only_reaches_allowlist(tmp_path, monkeypatch):
    a = mail_archive(tmp_path, monkeypatch, only_to=["doe"])
    st = console.email_minutes(a, "2026-11-12", lambda: ("m.pdf", b"%PDF"), "http://x/meetings/2026-11-12")
    assert [m["To"] for m in FakeSMTP.outbox] == ["jane@example.org"]  # Sam is on the roster but withheld
    assert st["sent"] == 1 and st["roster"] == 2 and st["test_mode"]
    msg = FakeSMTP.outbox[0]
    assert msg["Reply-To"] == "jane@example.org" and "November 12, 2026" in msg["Subject"]
    assert [p.get_filename() for p in msg.iter_attachments()] == ["m.pdf"]
    console.email_minutes(a, "2026-11-12", lambda: ("m.pdf", b"%PDF"), "u")  # only once
    assert len(FakeSMTP.outbox) == 1


def test_email_minutes_empty_allowlist_sends_nothing(tmp_path, monkeypatch):
    a = mail_archive(tmp_path, monkeypatch, only_to=[])
    assert console.email_minutes(a, "2026-11-12", lambda: ("m.pdf", b"%PDF"), "u")["sent"] == 0
    assert FakeSMTP.outbox == []


def test_email_minutes_live_skips_inactive_and_addressless(tmp_path, monkeypatch):
    a = mail_archive(tmp_path, monkeypatch, only_to=None)
    console.email_minutes(a, "2026-11-12", lambda: ("m.pdf", b"%PDF"), "u")
    assert sorted(m["To"] for m in FakeSMTP.outbox) == ["jane@example.org", "sam@example.org"]


def test_email_minutes_records_failure_and_retries(tmp_path, monkeypatch):
    a = mail_archive(tmp_path, monkeypatch, only_to=["doe"])
    def broken(): raise OSError("no pango")
    assert "no pango" in console.email_minutes(a, "2026-11-12", broken, "u")["error"]
    assert console.email_status(a, "2026-11-12")["error"]
    assert console.email_minutes(a, "2026-11-12", lambda: ("m.pdf", b"%PDF"), "u")["sent"] == 1


def test_email_minutes_as_pdf_or_link_per_member(tmp_path, monkeypatch):
    from club_minutes import prefs
    a = mail_archive(tmp_path, monkeypatch, only_to=None)
    prefs.save("kim", {"minutes": "link"})
    st = console.email_minutes(a, "2026-11-12", lambda: ("m.pdf", b"%PDF"), "http://x/meetings/2026-11-12")
    got = {m["To"]: m for m in FakeSMTP.outbox}
    assert st["sent"] == 2 and sorted(got) == ["jane@example.org", "sam@example.org"]
    assert [p.get_filename() for p in got["jane@example.org"].iter_attachments()] == ["m.pdf"]
    link = got["sam@example.org"]
    assert not list(link.iter_attachments()) and "The PDF is attached" not in link.get_body().get_content()
    assert "To get the PDF attached instead" in link.get_body().get_content()
    assert "stop these emails: http://x/profile/email" in got["jane@example.org"].get_body().get_content()
    assert "http://x/meetings/2026-11-12" in link.get_body().get_content()
    FakeSMTP.outbox = []
    (tmp_path / "2026-11-12" / "emailed.json").unlink()
    prefs.save("kim", {"minutes": "off"})
    prefs.save("doe", {"minutes": "off"})
    assert console.email_minutes(a, "2026-11-12", lambda: 1 / 0, "u")["sent"] == 0  # nobody wants it: no PDF made


def test_celebrations_the_week_up_to_tonight_shared_birthdays_only():
    c = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[
        {"id": "amy", "first": "Amy", "last": "Appleseed", "birthday": "11-10", "share": ["birthday"], "member_since": "2020-11-06"},
        {"id": "bob", "first": "Bob", "last": "Bloggs", "birthday": "11-12", "share": []},  # not shared: never shown
        {"id": "cal", "first": "Cal", "last": "Customer", "birthday": "12", "share": ["birthday"], "member_since": "2026-11-01"},
        {"id": "dee", "first": "Dee", "last": "Dummy", "birthday": "11-13", "share": ["birthday"]}])  # tomorrow: next week
    assert console.celebrations(c, dt.date(2026, 11, 12)) == ["Amy Appleseed: 6 years as a member", "Happy birthday, Amy Appleseed!"]
    assert console.celebrations(c, dt.date(2026, 12, 3)) == ["Happy birthday, Cal Customer! (December)"]  # first meeting of the month
    assert console.celebrations(c, dt.date(2026, 12, 10)) == []
    assert console.celebrations(c, dt.date(2027, 1, 2)) == []  # the week crosses New Year


def test_recorded_by_shows_only_when_someone_else_led(tmp_path, monkeypatch):
    from club_minutes.web import templates
    a = archive_with_prev(tmp_path)
    m = console.start(a, dt.date(2026, 11, 12), "doe", recorder="kim")  # the president chairs, Sam takes the minutes
    m = Archive(tmp_path, club).by_slug[m.slug]  # survives saving
    assert (m.leader, m.recorder) == ("doe", "kim")
    html = lambda: templates.get_template("_minutes.html").render(m=m, pdf=False, club=club)
    assert "Led by Jane Doe · Recorded by Sam Kim" in html()
    m.recorder = "doe"
    assert "Led &amp; Recorded by Jane Doe" in html()  # one person did both
    m.recorder = None
    assert "Recorded by" not in html() and "Led by Jane Doe" in html()  # older minutes: no recorder saved

    a = mail_archive(tmp_path / "mail", monkeypatch, only_to=None)
    a.by_slug["2026-11-12"].recorder = "kim"
    console.email_minutes(a, "2026-11-12", lambda: ("m.pdf", b"%PDF"), "u")
    msg = FakeSMTP.outbox[0]
    assert msg["Reply-To"] == "sam@example.org" and "led by Jane Doe, recorded by Sam Kim" in msg.get_body(("plain",)).get_content()


def test_one_meeting_open_at_a_time(tmp_path):
    a = archive_with_prev(tmp_path)
    m = console.start(a, dt.date(2026, 11, 12), "doe")
    assert console.start(a, dt.date(2026, 11, 12), "kim").slug == m.slug  # the same date: opens it
    with pytest.raises(ValueError, match="November 12 are still open"):
        console.start(a, dt.date(2026, 11, 19), "doe")  # last week's never submitted
    console.discard(a, m.slug)
    m = console.start(a, dt.date(2026, 11, 19), "doe")  # after a discard
    console.submit(a, m.slug)
    assert console.start(a, dt.date(2026, 11, 26), "doe").status == "draft"  # and after a submit


def test_one_console_takes_the_minutes_and_another_can_take_over(tmp_path):
    a = archive_with_prev(tmp_path)
    m = console.start(a, dt.date(2026, 11, 12), "doe", recorder="kim")
    assert console.hold(a, m.slug, "kim", "tab1") is None                  # Sam's console holds them
    assert console.hold(a, m.slug, "kim", "tab1") is None                  # checking in keeps them
    assert console.hold(a, m.slug, "doe", "tab2")["member"] == "kim"       # Jane's is read-only
    assert console.holder(m.slug, "tab1") is None and console.holder(m.slug, "tab2")["member"] == "kim"
    assert console.hold(a, m.slug, "doe", "tab2", take=True) is None       # Jane takes over: she's recording now
    assert a.by_slug[m.slug].recorder == "doe" and console.hold(a, m.slug, "kim", "tab1")["member"] == "doe"
    console.release(m.slug, "tab1")                                        # Sam's (read-only) tab closing changes nothing
    assert console.holder(m.slug)["member"] == "doe"
    console.release(m.slug, "tab2")                                        # Jane closes hers: free at once
    assert console.holder(m.slug) is None and console.hold(a, m.slug, "kim", "tab1") is None
    console._holds[m.slug]["at"] -= console.HOLD_SECONDS                  # a console that lost its connection
    assert console.hold(a, m.slug, "doe", "tab3") is None and a.by_slug[m.slug].recorder == "doe"  # carries on; no take over


def test_the_web_console_refuses_saves_and_discards_while_someone_else_holds_it(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import web
    from club_minutes.auth import Auth
    a = archive_with_prev(tmp_path)
    a.club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[
        {"id": "doe", "first": "Jane", "last": "Doe", "minutes_editor": True}, {"id": "kim", "first": "Sam", "last": "Kim", "minutes_editor": True}])
    monkeypatch.setattr(web, "archive", a)
    auth = Auth(a.club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", auth)
    def as_(who):
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, auth.new_session(who))
        return t
    sam, jane = as_("kim"), as_("doe")
    m = console.start(a, dt.date(2026, 11, 12), "doe", recorder="kim")
    assert sam.post(f"/api/meetings/{m.slug}/hold", json={"tab": "s"}).json() == {"held": True}
    r = jane.post(f"/api/meetings/{m.slug}/hold", json={"tab": "j"})
    assert r.status_code == 409 and r.json()["holder"] == "Sam Kim"
    body = {"meeting": {"sections": {}}, "state": {}, "tab": "j"}
    assert jane.put(f"/api/meetings/{m.slug}", json=body).status_code == 409   # would overwrite Sam's work
    assert "is taking these minutes" in jane.post(f"/console/{m.slug}/discard").text  # back on the start page, with why
    assert m.slug in a.by_slug                                                 # still there
    assert jane.post(f"/api/meetings/{m.slug}/hold", json={"tab": "j", "take": True}).json() == {"held": True}
    assert sam.put(f"/api/meetings/{m.slug}", json={**body, "tab": "s"}).status_code == 409  # Sam's console is read-only now
    jane.post(f"/console/{m.slug}/discard")                                  # Jane discards them: Sam's console is told
    r = sam.post(f"/api/meetings/{m.slug}/hold", json={"tab": "s", "take": True})
    assert r.status_code == 409 and r.json() == {"closed": "discarded", "by": "Jane Doe"} and console.holder(m.slug) is None


def test_top_bar_says_continue_minutes_for_an_open_meeting_from_another_day(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import web
    from club_minutes.auth import Auth
    a = archive_with_prev(tmp_path)
    a.club = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[
        {"id": "doe", "first": "Jane", "last": "Doe", "minutes_editor": True}, {"id": "kim", "first": "Sam", "last": "Kim"}])
    monkeypatch.setattr(web, "archive", a)
    auth = Auth(a.club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", auth)
    def page_as(who):
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, auth.new_session(who))
        return t.get("/links").text  # not About: it shows the changelog, which names the button
    assert "Start meeting" in page_as("doe")
    day = next(d for d in (dt.date(2026, 11, 12), dt.date(2026, 11, 19)) if d != dt.date.today())  # never today
    console.start(a, day, "doe")  # not today: no red dot for anyone
    assert f"Continue minutes ({day:%b %-d})" in page_as("doe") and "start live" not in page_as("doe")
    assert "Continue minutes" not in page_as("kim") and "start live" not in page_as("kim")


def test_a_reloaded_console_keeps_the_minutes_and_a_duplicated_tab_never_shares_them(tmp_path):
    a = archive_with_prev(tmp_path)
    m = console.start(a, dt.date(2026, 11, 12), "doe")
    assert console.hold(a, m.slug, "doe", "load1") is None
    assert console.hold(a, m.slug, "doe", "load2", prev="load1") is None      # reloaded: carries on, no take over
    assert console.hold(a, m.slug, "doe", "load1")["tab"] == "load2"          # the old page's id no longer holds them
    assert console.hold(a, m.slug, "kim", "x", prev="load2")["member"] == "doe"  # someone else can't borrow the id


def test_the_meeting_follows_the_clubs_agenda(tmp_path):
    from club_minutes import club as club_mod
    from club_minutes.web import templates
    c = Club(Path(__file__).parent / "fixtures" / "test-club.yaml")
    assert c.sections() == ["Bills and Communications", "Financial Report", "Community Reports", "Old Business", "New Business",
                            "Upcoming Chow", "Upcoming Events"]  # the default: no Winners; chow only with its module
    c.modules["chow"] = False
    assert "Upcoming Chow" not in c.sections()
    c._sections = club_mod._agenda(["Old Business", {"name": "  Treasurer's\n corner ", "hint": "Balances only."},
                                    "New Business", {"name": "Upcoming Chow"}])  # listed, but the module is off
    assert c.sections() == ["Old Business", "Treasurer's corner", "New Business"]
    assert c.section_hints() == {"Treasurer's corner": "Balances only."}
    # an older meeting with a section the agenda has since dropped: it still shows and still saves
    Meeting(date=dt.date(2026, 11, 5), status="published", sections={"Winners": [Item("50/50: Amy A $7")]}).save(tmp_path)
    a = Archive(tmp_path, c)
    old = a.by_slug["2026-11-05"]
    assert old.section_order(c.sections())[-1] == "Winners" and "50/50: Amy A $7" in templates.get_template("_minutes.html").render(m=old, pdf=False, club=c)
    m = console.start(a, dt.date(2026, 11, 12), "doe")
    ctx = console.context(a, m)
    assert ctx["sections"] == c.sections() and ctx["hints"] == {"Treasurer's corner": "Balances only."}
    data = {"meeting": {"sections": {"Treasurer's corner": [{"text": "$412 in checking"}], "Winners": [{"text": "sneaked in"}],
                                     "Made up": [{"text": "nope"}]}}}
    m = console.apply(a, m.slug, data)
    assert [i.text for i in m.items("Treasurer's corner")] == ["$412 in checking"] and not m.items("Winners") and "Made up" not in m.sections
    text = (tmp_path / m.slug / "minutes.md").read_text()
    assert text.index("## Old Business") < text.index("## Treasurer's corner") < text.index("## New Business")  # the club's order
    assert Meeting.from_markdown(text).items("Treasurer's corner")[0].text == "$412 in checking"  # survives a save and reload
    for bad in (["Attendance"], ["Old Business", "Old Business"], ["x" * 61], [""], [{"hint": "no name"}]):
        with pytest.raises(ValueError, match="meeting: sections"):
            club_mod._agenda(bad)
