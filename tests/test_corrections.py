"""Minutes in the archive: the console leaves them alone, and corrections keep every version (FR-28, FR-33)."""
import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from club_minutes import console, corrections
from club_minutes.auth import Auth
from club_minutes.club import Club
from club_minutes.meeting import Item, Meeting
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
ROSTER = [
    {"id": "doe", "first": "Jane", "last": "Doe", "email": "jane@example.org", "minutes_editor": True},
    {"id": "kim", "first": "Sam", "last": "Kim", "email": "sam@example.org", "minutes_editor": True},
    {"id": "roe", "first": "Rick", "last": "Roe", "email": "rick@example.org", "role": "Secretary", "officer": True, "board": True},
    {"id": "dummy", "first": "John", "last": "Dummy", "email": "john@example.org", "role": "Trustee", "board": True},
    {"id": "n0call", "first": "Pat", "last": "Plain", "email": "pat@example.org"},
    {"id": "n0admn", "first": "Ann", "last": "Admin", "email": "ann@example.org"},
]


def archive(tmp_path) -> Archive:
    club = Club(FIX / "test-club.yaml", roster=ROSTER)
    club.offices = ["President", "Vice President", "Secretary", "Treasurer", {"title": "Trustee", "officer": False, "seats": 3}]
    club.admins = {"n0admn"}
    Meeting(date=dt.date(2026, 10, 15), status="published", leader="doe", recorder="kim", in_person=["kim", "doe"],
            guests=[{"name": "Jane Guest", "notes": "(GMRS)  4th visit"}],
            sections={"Old Business": [Item("Jane motioned, John seconded, to buy patches.  Passed")],
                      "Community Reports": [Item("Sam built a dipole", "kim")],
                      "Upcoming Events": [Item("VE testing", when="2026-11-21T10:00", cal="community")]}).save(tmp_path)
    Meeting(date=dt.date(2025, 10, 23), status="imported", source="23-oct-25-minutes.pdf", in_person=["doe"],
            sections={"New Business": [Item("Field day plans")]}).save(tmp_path)
    return Archive(tmp_path, club)


# ---------------- step 1: the console only works on drafts ----------------

def test_the_console_refuses_minutes_already_in_the_archive(tmp_path):
    a = archive(tmp_path)
    for day in (dt.date(2026, 10, 15), dt.date(2025, 10, 23)):  # submitted, and imported
        with pytest.raises(ValueError, match="already in the archive. To fix something, use Correct these minutes"):
            console.start(a, day, "doe")
        for f in (lambda s: console.submit(a, s), lambda s: console.discard(a, s),
                  lambda s: console.apply(a, s, {"meeting": {"sections": {}}})):
            with pytest.raises(ValueError, match="already in the archive"):
                f(day.isoformat())
    assert a.by_slug["2025-10-23"].status == "imported" and a.by_slug["2025-10-23"].items("New Business")  # untouched


def test_the_web_console_refuses_them_too(tmp_path, monkeypatch):
    _a, c = web_as(tmp_path, monkeypatch, "doe")
    for slug in ("2026-10-15", "2025-10-23"):
        assert c.get(f"/console/{slug}", follow_redirects=False).headers["location"] == f"/meetings/{slug}"
        assert c.post(f"/console/{slug}/submit").status_code == 409            # the stage test's case: no re-email
        assert c.put(f"/api/meetings/{slug}", json={"meeting": {"sections": {}}, "state": {}}).status_code == 409  # nor a save
        assert c.post(f"/api/meetings/{slug}/hold", json={"tab": "t"}).status_code == 409
    assert "already in the archive" in c.post("/console/start", data={"date": "2026-10-15"}).text
    assert not (tmp_path / "2026-10-15" / "emailed.json").exists()


# ---------------- step 2: corrections ----------------

def test_who_may_correct(tmp_path):
    a = archive(tmp_path)
    m, imp = a.by_slug["2026-10-15"], a.by_slug["2025-10-23"]
    may = lambda mt, who, admin=False: corrections.may_correct(a.club, mt, who, admin)
    assert may(m, "doe") and may(m, "kim")                       # its leader and its recorder
    assert may(m, "roe") and may(m, "dummy")                     # the board: the Secretary, a trustee (there or not)
    assert not may(m, "n0call") and not may(m, "n0admn")         # a member; an admin without admin mode
    assert may(m, "n0admn", admin=True)
    assert not may(imp, "doe") and may(imp, "roe")               # imported: no leader on record, so the board
    a.by_slug["2026-11-12"] = draft = Meeting(date=dt.date(2026, 11, 12), leader="doe")
    assert not may(draft, "roe", admin=True)                     # a draft is the console's


def edit(m: Meeting, **change) -> Meeting:
    new = Meeting(date=m.date, status=m.status, in_person=list(m.in_person), remote=list(m.remote),
                  guests=list(m.guests), visitors=m.visitors, sections={k: list(v) for k, v in m.sections.items()})
    for k, v in change.items():
        setattr(new, k, v)
    return new


def test_a_correction_keeps_the_old_version_and_says_who_and_why(tmp_path):
    a = archive(tmp_path)
    m = a.by_slug["2026-10-15"]
    old_text = (tmp_path / "2026-10-15" / "minutes.md").read_text()
    new = edit(m, in_person=["doe", "kim", "roe"], sections={**m.sections, "Old Business": [Item("John motioned, Jane seconded, to buy patches")]})
    assert corrections.changes(m, new, a.club) == [
        "In person: added Rick Roe",
        'Old Business: "John motioned, Jane seconded, to buy patches" (reworded)']  # several stretches changed
    m2 = corrections.correct(a, m.slug, "roe", "  John seconded the patches motion,\n not Jane ", 1, new, notice=True)
    assert m2.version == 2 and m2.status == "published" and m2.in_person == ["doe", "kim", "roe"]
    assert m2.corrected == {"version": 2, "at": m2.corrected["at"], "by": "roe", "reason": "John seconded the patches motion, not Jane", "notice": True}
    assert (tmp_path / "2026-10-15" / "minutes.v1.md").read_text() == old_text            # version 1, word for word
    assert corrections.load_version(a, m.slug, 1).items("Old Business")[0].text.startswith("Jane")
    assert corrections.load_version(a, m.slug, 2) is None                                  # the current one isn't a "version"
    assert a.by_slug[m.slug].items("Upcoming Events")[0].when == "2026-11-21T10:00"        # tags kept
    assert [r["slug"] for r in a.search("John seconded")] == ["2026-10-15"]               # search: the current text only
    with pytest.raises(ValueError, match="while you were editing"):                       # opened on version 1
        corrections.correct(a, m.slug, "doe", "again", 1, edit(m2, visitors=2), notice=False)
    with pytest.raises(ValueError, match="Say why"):
        corrections.correct(a, m.slug, "doe", " ", 2, edit(m2, visitors=2), notice=False)
    with pytest.raises(ValueError, match="Nothing was changed"):
        corrections.correct(a, m.slug, "doe", "no change", 2, edit(m2), notice=False)
    m3 = corrections.correct(a, m.slug, "doe", "two visitors", 2, edit(m2, visitors=2), notice=False)
    assert m3.version == 3 and (tmp_path / "2026-10-15" / "minutes.v2.md").exists() and len(m3.corrections) == 2
    assert corrections.pdf_name(m3) == "15-oct-26-minutes-v3.pdf"
    assert corrections.pdf_name(corrections.load_version(a, m.slug, 1)) == "15-oct-26-minutes.pdf"


def test_imported_minutes_are_reviewed_or_corrected_but_stay_imported(tmp_path):
    a = archive(tmp_path)
    imp = a.by_slug["2025-10-23"]
    m = corrections.mark_reviewed(a, imp.slug, "roe")
    assert m.status == "imported" and m.version == 1 and m.reviewed["by"] == "roe"
    with pytest.raises(ValueError):
        corrections.mark_reviewed(a, "2026-10-15", "roe")              # only imported minutes are reviewed
    b = archive(tmp_path / "b")
    m = corrections.correct(b, "2025-10-23", "dummy", "Jane was online", 1, edit(b.by_slug["2025-10-23"], in_person=[], remote=["doe"]), False)
    assert m.status == "imported" and m.version == 2 and m.reviewed["by"] == "dummy"   # a correction is a review too


# ---------------- the web pages ----------------

def web_as(tmp_path, monkeypatch, who, admin=False):
    from club_minutes import web
    a = archive(tmp_path)
    monkeypatch.setattr(web, "archive", a)
    monkeypatch.setattr(web, "DATA", tmp_path)
    auth = Auth(a.club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", auth)
    tok = auth.new_session(who)
    if admin:
        auth.set_admin(tok, 60)
    c = TestClient(web.app)
    c.cookies.set(web.COOKIE, tok)
    return a, c


def form_for(c, slug: str) -> dict:
    """What the browser would send back for the editor unchanged: every field's current value."""
    import re
    html = c.get(f"/meetings/{slug}/correct").text
    out = {}
    for tag in re.findall(r"<(?:input|textarea)[^>]*>(?:[^<]*</textarea>)?", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name or 'type="checkbox"' in tag and "checked" not in tag:
            continue
        if 'type="radio"' in tag:
            if "checked" in tag:
                out[name.group(1)] = re.search(r'value="([^"]*)"', tag).group(1)
            continue
        if tag.startswith("<textarea"):
            out[name.group(1)] = __import__("html").unescape(re.search(r">([^<]*)</textarea>", tag).group(1))
        else:
            v = re.search(r'value="([^"]*)"', tag)
            out[name.group(1)] = __import__("html").unescape(v.group(1)) if v else ""
    return out


def test_correcting_on_the_web_and_reading_both_versions(tmp_path, monkeypatch):
    from club_minutes import web
    sent = []
    monkeypatch.setattr(corrections, "email_notice", lambda *a: sent.append(a[2]))
    a, c = web_as(tmp_path, monkeypatch, "roe")
    page = c.get("/meetings/2026-10-15").text
    assert "Correct these minutes" in page and "Mark as reviewed" not in page
    f = form_for(c, "2026-10-15")
    assert "Nothing was changed" in c.post("/meetings/2026-10-15/correct", data=f | {"reason": "x"}).text  # two spaces, the order: kept
    assert f["version"] == "1" and f["att-doe"] == "in_person" and f["att-roe"] == "" and f["notice"] == "1"
    old_business = next(k for k, v in f.items() if v.startswith("Jane motioned"))
    f.update({old_business: "John motioned, Jane seconded, to buy patches", "att-roe": "remote",
              "reason": "John seconded the patches motion, not Jane"})
    r = c.post("/meetings/2026-10-15/correct", data=f, follow_redirects=False)
    assert r.headers["location"] == "/meetings/2026-10-15"
    m = a.by_slug["2026-10-15"]
    assert m.version == 2 and m.remote == ["roe"] and m.items("Community Reports")[0].member == "kim"
    assert sent == [["Online: added Rick Roe", 'Old Business: "John motioned, Jane seconded, to buy patches" (reworded)']]
    page = c.get("/meetings/2026-10-15").text
    assert "Version 2<br>Corrected" in page and "John seconded the patches motion, not Jane" in page and "See version 1" in page
    assert "no notice sent" not in page
    v1 = c.get("/meetings/2026-10-15?v=1").text
    assert "This is version 1" in v1 and "See the current version" in v1 and "Jane motioned" in v1 and "Correct these minutes" not in v1
    assert c.get("/meetings/2026-10-15?v=2").status_code == 404 and c.get("/meetings/2026-10-15?v=x").status_code == 404
    log = (tmp_path / "admin-log.jsonl").read_text()
    assert '"corrected": "2026-10-15 v2"' in log and web.admin_action(__import__("json").loads(log.splitlines()[-1])).startswith(
        "Corrected the minutes of 2026-10-15 (version 2)")

    f = form_for(c, "2026-10-15") | {"reason": "Sam's report reworded", "notice": ""}
    f[next(k for k, v in f.items() if v == "Sam built a dipole")] = "Sam built a 40 m dipole"
    c.post("/meetings/2026-10-15/correct", data=f)
    assert "no notice sent" in c.get("/meetings/2026-10-15").text and len(sent) == 1   # unticked: nothing emailed


def test_only_those_who_may_correct_see_the_editor(tmp_path, monkeypatch):
    for who, admin, code in (("n0call", False, 403), ("n0admn", False, 403), ("n0admn", True, 200), ("doe", False, 200)):
        _, c = web_as(tmp_path / f"{who}{admin}", monkeypatch, who, admin)
        assert c.get("/meetings/2026-10-15/correct").status_code == code
        assert c.post("/meetings/2026-10-15/correct", data={"reason": "x", "version": "1"}, follow_redirects=False).status_code in (code, 303)
        assert ("Correct these minutes" in c.get("/meetings/2026-10-15").text) == (code == 200)
    _, c = web_as(tmp_path / "plain", monkeypatch, "n0call")
    assert c.post("/meetings/2025-10-23/reviewed").status_code == 403


def test_an_imported_meeting_on_the_web(tmp_path, monkeypatch):
    a, c = web_as(tmp_path, monkeypatch, "roe")
    page = c.get("/meetings/2025-10-23").text
    assert "not yet reviewed" in page and "Mark as reviewed" in page
    c.post("/meetings/2025-10-23/reviewed")
    page = c.get("/meetings/2025-10-23").text
    assert "reviewed by Rick Roe" in page and "Mark as reviewed" not in page and a.by_slug["2025-10-23"].version == 1
    assert c.post("/meetings/2026-10-15/reviewed").status_code == 409


def test_the_notice_email(tmp_path, monkeypatch):
    from test_console import FakeSMTP

    from club_minutes import auth
    monkeypatch.setattr(auth.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "club@example.org")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "abcd efgh")
    FakeSMTP.outbox = []
    a = archive(tmp_path)
    a.club.email = {}
    m = a.by_slug["2026-10-15"]
    new = edit(m, in_person=["doe", "kim", "dummy"])
    what = corrections.changes(m, new, a.club)
    corrections.correct(a, m.slug, "roe", "John was there", 1, new, notice=True)
    st = corrections.email_notice(a, m.slug, what, lambda: ("15-oct-26-minutes-v2.pdf", b"%PDF"), "https://x.example.org/meetings/2026-10-15")
    assert st["sent"] == 6 and corrections.email_notice(a, m.slug, what, None, "") == st   # once per version
    msg = FakeSMTP.outbox[0]
    body = msg.get_body(("plain",)).get_content()
    assert msg["Subject"] == "W0TEST meeting minutes, October 15, 2026 (corrected)" and msg["Reply-To"] is None  # Rick doesn't share his email
    assert "corrected (version 2) by Rick Roe:\nJohn was there" in body and "- In person: added John Dummy" in body
    assert [p.get_filename() for p in msg.iter_attachments()] == ["15-oct-26-minutes-v2.pdf"]


def test_rows_added_and_removed_in_the_browser_and_the_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(corrections, "email_notice", lambda *a: None)
    a, c = web_as(tmp_path, monkeypatch, "roe")
    m = a.by_slug["2026-10-15"]
    m.sections["Community Reports"] = [Item("Sam built a dipole", "kim"), Item("Jane worked a new country", "doe")]
    m.save(tmp_path)
    a.reload()
    f = form_for(c, "2026-10-15")
    n = next(k for k, v in f.items() if v == "Sam built a dipole").split("-")[0]
    f[f"{n}-0-rm"] = "1"                                                 # Sam's report: Remove ticked
    f |= {"g0-rm": "1", "g7-name": "John Doe", "g7-callsign": "n0call", f"{n}-5": "Rick Roe tuned up the repeater",  # added: any numbers
          "reason": "Fixed the reports"}
    p = c.post("/meetings/2026-10-15/correct/preview", data=f).text
    assert "<del>" not in p and 'class="removed"' in p and "Sam built a dipole" in p and 'class="added"' in p and "John Doe N0CALL" in p
    assert 'value="Fixed the reports"' in p and "Save version 2" in p                         # carried on to Save
    assert a.by_slug["2026-10-15"].version == 1                                                # nothing saved yet
    assert 'formaction="/meetings/2026-10-15/correct/edit"' in p and "history.back" not in p
    e = c.post("/meetings/2026-10-15/correct/edit", data=f).text                              # Back to editing: as typed
    assert 'value="John Doe"' in e and 'value="n0call"' in e and '>Rick Roe tuned up the repeater</textarea>' in e
    assert 'name="g0-rm" value="1" checked' in e and f'name="{n}-0-rm" value="1" checked' in e and 'value="Fixed the reports"' in e
    assert 'data-add="t-guest" data-next="8"' in e and 'name="notice" value="1" checked' in e and 'name="private" value="1" >' in e
    c.post("/meetings/2026-10-15/correct", data=f)
    m = a.by_slug["2026-10-15"]
    assert [(i.text, i.member) for i in m.items("Community Reports")] == [
        ("Jane worked a new country", "doe"), ("Rick Roe tuned up the repeater", "roe")]      # Jane's tag kept by its number
    assert m.guests == [{"name": "John Doe", "callsign": "N0CALL"}]                           # Jane Guest removed
    assert "Nothing was changed" in c.post("/meetings/2026-10-15/correct/preview", data=form_for(c, "2026-10-15") | {"reason": "x"}).text


def test_a_changed_line_marks_the_words_that_changed():
    old, new = corrections.words("Jane seconded <it>, not John", "John seconded <it>, not Jane")
    assert old == "<del>Jane</del> seconded &lt;it&gt;, not <del>John</del>" and new == "<ins>John</ins> seconded &lt;it&gt;, not <ins>Jane</ins>"


def test_import_correction_keeps_the_original_as_version_1(tmp_path, monkeypatch):
    from club_minutes import importer
    a = archive(tmp_path)
    orig = (FIX / "old-format.txt").read_text().replace("Date:5 Nov 2026", "Date:23 Oct 2025")
    (tmp_path / "2025-10-23" / "original.pdf").write_text(orig)
    fixed = tmp_path / "fixed.pdf"
    fixed.write_text(orig.replace("Fox hunt moved to the 20th", "Fox hunt moved to the 21st"))
    monkeypatch.setattr(importer, "pdf_text", lambda p: Path(p).read_text())  # the text files stand in for PDFs
    with pytest.raises(ValueError, match="roster"):
        importer.import_correction(a, dt.date(2025, 10, 23), fixed, "nobody", "x")
    other = tmp_path / "other.pdf"
    other.write_text((FIX / "old-format.txt").read_text())
    with pytest.raises(ValueError, match="Date line says 2026-11-05, not 2025-10-23"):  # another meeting's PDF
        importer.import_correction(a, dt.date(2025, 10, 23), other, "roe", "x")
    m = importer.import_correction(a, dt.date(2025, 10, 23), fixed, "ROE", "The Secretary's corrected minutes",
                                   on=dt.date(2025, 10, 30))
    assert dt.date.fromtimestamp(m.corrections[0]["at"]) == dt.date(2025, 10, 30)
    assert m.source == "23-oct-25-minutes.pdf" and m.note is None
    assert (m.version, m.status, m.corrections[0]["by"], m.corrections[0]["notice"]) == (2, "imported", "roe", False)
    assert m.items("Old Business")[0].text == "Fox hunt moved to the 21st"
    assert corrections.load_version(a, m.slug, 1).items("Old Business")[0].text == "Fox hunt moved to the 20th"
    with pytest.raises(ValueError, match="version 1"):  # only once
        importer.import_correction(a, dt.date(2025, 10, 23), fixed, "roe", "again")


def test_a_correction_that_fails_to_save_changes_nothing(tmp_path, monkeypatch):
    """Data & reliability sweep: the archive's own copy was changed before saving; a failed save left it saying
    version 2 while the disk said 1. And a renamed member keeps the right to correct (recorder via canonical)."""
    a = archive(tmp_path)
    new = a.by_slug["2026-10-15"]
    fixed = Meeting(date=new.date, status=new.status, in_person=new.in_person, guests=new.guests,
                    sections={**new.sections, "Old Business": [Item("Patches: passed")]})
    monkeypatch.setattr(Meeting, "save", lambda self, root: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        corrections.correct(a, "2026-10-15", "kim", "fix", 1, fixed, False)
    assert a.by_slug["2026-10-15"].version == 1 and not a.by_slug["2026-10-15"].corrections
    a.club.former_ids["n0kimo"] = "kim"
    m = a.by_slug["2026-10-15"]
    m.recorder = "n0kimo"
    assert a._canonicalize(m).recorder == "kim"


def test_removed_lines_are_never_quoted_and_private_hides_the_version_before(tmp_path, monkeypatch):
    from club_minutes import web
    """Security and privacy sweeps, Jim's middle way (2026-11-15): the notice never quotes a removed line, shows the few
    words a changed line replaced, and with "This removes private details" no old words at all; the version before a
    private correction (and its PDF) is only for those who may correct."""
    a = archive(tmp_path)
    m = a.by_slug["2026-10-15"]
    new = edit(m, sections={**m.sections, "Community Reports": [],
                            "Upcoming Events": [Item("VE testing on Nov 22", when="2026-11-21T10:00", cal="community")]})
    m.sections["Upcoming Events"] = [Item("VE testing on Nov 21", when="2026-11-21T10:00", cal="community")]
    assert corrections.changes(m, new, a.club) == ['Community Reports: 1 line removed', 'Upcoming Events: "Sat Nov 21, 10:00 AM: VE testing on Nov 22" (was "21")']
    assert corrections.changes(m, new, a.club, private=True)[1] == 'Upcoming Events: "Sat Nov 21, 10:00 AM: VE testing on Nov 22" (reworded)'
    assert not any("dipole" in x for x in corrections.changes(m, new, a.club))  # the removed report's words
    a, member = web_as(tmp_path, monkeypatch, "n0call")
    m = a.by_slug["2026-10-15"]
    new = edit(m, sections={**m.sections, "Community Reports": []})
    corrections.correct(a, "2026-10-15", "roe", "Took out a private detail", 1, new, notice=False, private=True)
    page = member.get("/meetings/2026-10-15").text
    assert "Version 2" in page and "See version 1" not in page
    assert member.get("/meetings/2026-10-15?v=1").status_code == 404 and member.get("/meetings/2026-10-15/minutes.pdf?v=1").status_code == 404
    editor = TestClient(web.app)
    editor.cookies.set(web.COOKIE, web.authn.new_session("kim"))  # the recorder
    assert "See version 1" in editor.get("/meetings/2026-10-15").text and editor.get("/meetings/2026-10-15?v=1").status_code == 200



# ---------------- what goes out by email ----------------

def _mail(monkeypatch, tmp_path):
    """The real send_email, up to the SMTP step: what would be sent is recorded, and the Email log is written."""
    from club_minutes import auth
    sent = []
    monkeypatch.setattr(auth, "DATA", tmp_path)
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "club@example.org")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "abcd efgh")
    monkeypatch.setattr(auth, "_smtp_send", lambda to, subject, body, att, reply_to, *_: sent.append((list(to), subject, body, reply_to)) or len(to))
    return sent


def test_a_removed_line_never_goes_out_in_the_notice_or_the_email_log(tmp_path, monkeypatch):
    sent = _mail(monkeypatch, tmp_path)
    a = archive(tmp_path)
    a.club.email = {}
    m = a.by_slug["2026-10-15"]
    secret = "ZZMARKER"
    m = corrections.correct(a, m.slug, "roe", "add", 1, edit(m, sections={**m.sections, "New Business": [Item(f"Call {secret} about the key")]}), notice=False)
    what = corrections.changes(m, edit(m, sections={**m.sections, "New Business": []}), a.club)  # the line removed...
    what += corrections.changes(m, edit(m, sections={**m.sections, "New Business": [Item("Call someone about the key")]}), a.club, private=True)  # ...and reworded, private
    m = corrections.correct(a, m.slug, "roe", "private details", 2, edit(m, sections={**m.sections, "New Business": []}), notice=True, private=True)
    corrections.email_notice(a, m.slug, what, lambda: ("m.pdf", b"%PDF"), "https://x.example.org/meetings/2026-10-15")
    assert sent and secret not in str(sent) and secret not in (tmp_path / "email-log.jsonl").read_text()


def test_inactive_and_silent_key_members_get_no_minutes_or_corrections(tmp_path, monkeypatch):
    sent = _mail(monkeypatch, tmp_path)
    a = archive(tmp_path)
    a.club.email = {}
    a.club.members["kim"].status, a.club.members["roe"].status = "Inactive", "Silent Key"
    m = a.by_slug["2026-10-15"]
    console.email_minutes(a, m.slug, lambda: ("m.pdf", b"%PDF"), "https://x.example.org/meetings/2026-10-15")
    new = edit(m, visitors=3)
    corrections.correct(a, m.slug, "doe", "visitors", 1, new, notice=True)
    corrections.email_notice(a, m.slug, corrections.changes(m, new, a.club), lambda: ("m.pdf", b"%PDF"), "https://x.example.org/meetings/2026-10-15")
    got = {x for to, *_ in sent for x in to}
    assert got and not got & {"sam@example.org", "rick@example.org"}


def test_unshared_details_never_appear_in_an_email_to_others(tmp_path, monkeypatch):
    """Minutes, corrections, recovery, an email change, reminders: a member who shares nothing has no phone, address or
    email in any body, and no Reply-To of theirs."""
    import datetime as dt

    from club_minutes import auth, business, prefs, recovery
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    monkeypatch.setattr(recovery, "DATA", tmp_path)
    sent = _mail(monkeypatch, tmp_path)
    a = archive(tmp_path)
    club = a.club
    club.email = {}
    jane = club.members["doe"]
    jane.phone, jane.address, jane.share = "555-0123", "123 Main St, Anytown", []
    m = a.by_slug["2026-10-15"]
    console.email_minutes(a, m.slug, lambda: ("m.pdf", b"%PDF"), "https://x.example.org/meetings/2026-10-15")
    new = edit(m, in_person=["kim", "roe"])  # Jane's attendance removed
    corrections.correct(a, m.slug, "roe", "Jane wasn't there", 1, new, notice=True)
    corrections.email_notice(a, m.slug, corrections.changes(m, new, club), lambda: ("m.pdf", b"%PDF"), "https://x.example.org/meetings/2026-10-15")
    recovery.start(club, "roe", "doe", ["n0admn"], "", "https://x.example.org")
    auth.Auth(club, tmp_path / "auth.sqlite").start_email_change("doe", "jane.new@example.org", "https://x.example.org", by="roe")
    due = [({"text": "Order patches", "due": "2026-10-20"}, "soon")]
    subject, body = business.reminder_email(club, "doe", due, dt.date(2026, 10, 19), "https://x.example.org")
    auth.send_email("anyone@example.org", subject, body)
    monday = dt.date(2026, 10, 12)
    day = monday + dt.timedelta(days=(club.meeting_weekday - monday.weekday()) % 7)
    for _mid, _key, subject, body in prefs.digest(club, a, day, "https://x.example.org") + prefs.chow_reminders(club, day, "https://x.example.org"):
        auth.send_email("anyone@example.org", subject, body)
    assert len(sent) >= 6
    for to, subject, body, reply_to in sent:
        assert reply_to != jane.email
        if to != [jane.email]:  # her own copies may say her own address
            assert not [w for w in ("555-0123", "123 Main St", jane.email) if w in body + subject], subject


def test_an_email_that_failed_or_never_went_is_sent_again_once(tmp_path, monkeypatch):
    """A pending note per email (console.mark_pending): a send that fails, or one a restart lost, shows "Send it now"
    to whoever may correct; the button and the daily job send it, still once; a notice for a replaced version is dropped."""
    import os
    import time

    from test_console import FakeSMTP

    from club_minutes import auth, console, web
    monkeypatch.setattr(auth.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("CLUB_MINUTES_SMTP_USER", "club@example.org")
    monkeypatch.setenv("CLUB_MINUTES_SMTP_PASSWORD", "abcd efgh")
    FakeSMTP.outbox = []
    a, c = web_as(tmp_path, monkeypatch, "roe")
    a.club.email = {}
    monkeypatch.setattr(web, "render_pdf", lambda m: ("m.pdf", b"%PDF"))
    slug, url = "2026-10-15", "https://x.example.org/meetings/2026-10-15"
    console.mark_pending(a, slug, "emailed.json", url)
    def broken(): raise OSError("mail server down")
    console.email_minutes(a, slug, broken, url)
    assert console.unsent(a, slug) == ["emailed.json"] and not FakeSMTP.outbox             # failed: listed at once
    assert "Send it now" in c.get(f"/meetings/{slug}").text
    c.post(f"/meetings/{slug}/send-now")
    n = len(FakeSMTP.outbox)
    assert n and console.unsent(a, slug) == [] and not (tmp_path / slug / "emailed.json.pending").exists()
    assert "Send it now" not in c.get(f"/meetings/{slug}").text
    c.post(f"/meetings/{slug}/send-now")
    assert len(FakeSMTP.outbox) == n                                                         # once
    # a correction's notice lost to a restart (pending, no status), then another correction replaces it
    console.mark_pending(a, slug, "emailed.v2.json", url, ["- In person: added John Doe"])
    p = tmp_path / slug / "emailed.v2.json.pending"
    assert console.unsent(a, slug) == []                                                     # not yet: it may still be sending
    os.utime(p, (time.time() - 700, time.time() - 700))
    assert console.unsent(a, slug) == ["emailed.v2.json"]
    assert corrections.retry_pending(a, web.render_pdf) == 0 and not p.exists()              # v1 is current: v2's dropped
    m = a.by_slug[slug]                                                                      # now a real v2 whose notice was lost
    new = edit(m, in_person=["doe", "kim", "dummy"])
    what = corrections.changes(m, new, a.club)
    corrections.correct(a, slug, "roe", "John was there", 1, new, notice=True)
    console.mark_pending(a, slug, "emailed.v2.json", url, what)
    os.utime(p, (time.time() - 700, time.time() - 700))
    before = len(FakeSMTP.outbox)
    assert corrections.retry_pending(a, web.render_pdf) == 1 and len(FakeSMTP.outbox) > before and not p.exists()
    assert "(corrected)" in FakeSMTP.outbox[-1]["Subject"] and corrections.retry_pending(a, web.render_pdf) == 0


def test_send_it_now_never_sends_twice_at_once(tmp_path, monkeypatch):
    """A click while the first send is still going (pending, no status, under 10 minutes) sends nothing, and while
    another retry of the meeting holds its lock, a second one skips it; a torn status counts as failed, not a 500."""
    import fcntl

    from club_minutes import console, web
    a, c = web_as(tmp_path, monkeypatch, "roe")
    sent = []
    slug, url = "2026-10-15", "https://x.example.org/meetings/2026-10-15"
    def fake_send(*x):  # a good send: its status, and the note gone
        sent.append(x)
        (tmp_path / slug / "emailed.json").write_text('{"sent": 1}')
        (tmp_path / slug / "emailed.json.pending").unlink(missing_ok=True)
    monkeypatch.setattr(console, "email_minutes", fake_send)
    console.mark_pending(a, slug, "emailed.json", url)
    assert "Nothing was sent" in c.post(f"/meetings/{slug}/send-now").text and sent == []  # the first send may still be going
    (tmp_path / slug / "emailed.json").write_text('{"sent": 3, "err')                         # torn by a crash
    assert console.unsent(a, slug) == ["emailed.json"] and c.get(f"/meetings/{slug}").status_code == 200
    with open(tmp_path / slug / ".send.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert corrections.retry_pending(a, web.render_pdf, slug) == 0 and sent == []        # another retry has it
    assert "Sent to members" in c.post(f"/meetings/{slug}/send-now").text and len(sent) == 1
    (tmp_path / slug / "emailed.json").write_text('{"error": "mail server down"}')       # failed before 1.1: no note to retry
    assert "Emailing these minutes failed" in c.get(f"/meetings/{slug}").text
