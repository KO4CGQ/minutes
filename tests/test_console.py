import datetime as dt
from pathlib import Path

from w8lbz_minutes import console
from w8lbz_minutes.club import Club
from w8lbz_minutes.meeting import Item, Meeting
from w8lbz_minutes.store import Archive

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
    for d, guests in ((dt.date(2026, 10, 1), [{"name": "Pat Guest", "callsign": "KE8ABC"}, {"name": "Rick Roe"}]),
                      (dt.date(2026, 10, 8), [{"name": "Lee Visitor"}])):
        Meeting(date=d, status="published", in_person=["roe"] if d.day == 8 else [], guests=guests).save(tmp_path)
    a = Archive(tmp_path, club)
    m = console.start(a, dt.date(2026, 11, 12), "doe")
    past = console.context(a, m)["past_guests"]
    assert [g["name"] for g in past] == ["Lee Visitor", "Pat Guest"]  # Rick Roe became a member
    assert past[1] == {"name": "Pat Guest", "callsign": "KE8ABC", "visits": 1, "last": "2026-10-01"}


def test_apply_saves_and_drops_never_record_guests(tmp_path):
    a = archive_with_prev(tmp_path)
    console.start(a, dt.date(2026, 11, 12), "doe")
    data = {"meeting": {"in_person": ["doe", "kim"], "remote": ["kim", "roe"],
                        "guests": [{"name": "Pat Guest", "callsign": "KE8ABC", "online": True}, {"name": "Tiny Visitor"}],
                        "visitors": 1,
                        "sections": {"Community Reports": [{"text": "Sam got on HF", "member": "kim"}, {"text": " "}]}},
            "state": {"done": ["kim"]}}
    m = console.apply(a, "2026-11-12", data)
    assert m.in_person == ["doe", "kim"] and m.remote == ["roe"]  # can't be both
    assert m.guests == [{"name": "Pat Guest", "callsign": "KE8ABC", "online": True}]  # an online guest stays online
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
    from w8lbz_minutes import auth
    monkeypatch.setattr(auth.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("W8LBZ_SMTP_USER", "club@example.org")
    monkeypatch.setenv("W8LBZ_SMTP_PASSWORD", "abcd efgh")
    FakeSMTP.outbox = []
    c = Club(Path(__file__).parent / "fixtures" / "test-club.yaml", roster=[
        {"id": "doe", "first": "Jane", "last": "Doe", "email": "jane@example.org", "minutes_editor": True},
        {"id": "kim", "first": "Sam", "last": "Kim", "email": "sam@example.org"},
        {"id": "roe", "first": "Rick", "last": "Roe", "email": "rick@example.org", "status": "Silent Key"},
        {"id": "dunn", "first": "Jane", "last": "Dunn"}])
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
    from w8lbz_minutes import prefs
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
        {"id": "amy", "first": "Amy", "last": "Able", "birthday": "11-10", "share": ["birthday"], "member_since": "2020-11-06"},
        {"id": "bob", "first": "Bob", "last": "Baker", "birthday": "11-12", "share": []},  # not shared: never shown
        {"id": "cal", "first": "Cal", "last": "Cole", "birthday": "12", "share": ["birthday"], "member_since": "2026-11-01"},
        {"id": "dee", "first": "Dee", "last": "Dunn", "birthday": "11-13", "share": ["birthday"]}])  # tomorrow: next week
    assert console.celebrations(c, dt.date(2026, 11, 12)) == ["Amy Able: 6 years as a member", "Happy birthday, Amy Able!"]
    assert console.celebrations(c, dt.date(2026, 12, 3)) == ["Happy birthday, Cal Cole! (December)"]  # first meeting of the month
    assert console.celebrations(c, dt.date(2026, 12, 10)) == []
    assert console.celebrations(c, dt.date(2027, 1, 2)) == []  # the week crosses New Year


def test_recorded_by_shows_only_when_someone_else_led(tmp_path, monkeypatch):
    from w8lbz_minutes.web import templates
    from w8lbz_minutes.console import SECTIONS
    a = archive_with_prev(tmp_path)
    m = console.start(a, dt.date(2026, 11, 12), "doe", recorder="kim")  # the president chairs, Sam takes the minutes
    m = Archive(tmp_path, club).by_slug[m.slug]  # survives saving
    assert (m.leader, m.recorder) == ("doe", "kim")
    html = lambda: templates.get_template("_minutes.html").render(m=m, pdf=False, club=club, SECTIONS=SECTIONS)
    assert "Led by Jane Doe · Recorded by Sam Kim" in html()
    m.recorder = "doe"
    assert "Recorded by" not in html() and "Led by Jane Doe" in html()

    a = mail_archive(tmp_path / "mail", monkeypatch, only_to=None)
    a.by_slug["2026-11-12"].recorder = "kim"
    console.email_minutes(a, "2026-11-12", lambda: ("m.pdf", b"%PDF"), "u")
    msg = FakeSMTP.outbox[0]
    assert msg["Reply-To"] == "sam@example.org" and "led by Jane Doe, recorded by Sam Kim" in msg.get_body(("plain",)).get_content()
