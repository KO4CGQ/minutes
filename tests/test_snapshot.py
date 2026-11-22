"""Published minutes keep what they showed the night they were made (snapshot.py), and so does their PDF."""
import datetime as dt

import pytest
from starlette.testclient import TestClient
from test_corrections import archive, edit, web_as

from club_minutes import club as clubmod
from club_minutes import console, corrections, pdf, richtext, snapshot, upgrade
from club_minutes.meeting import Item, Meeting

SLUG = "2026-10-15"


def stamp(a):
    """The October meeting as 1.1.1 would have saved it: with a snapshot of the club at the time."""
    m = a.by_slug[SLUG]
    m.shown = snapshot.take(a.club, m)
    m.save(a.root)
    a.reload()


def change_the_club(a):
    """Everything the minutes show that an admin or the roster can change later."""
    c = a.club
    c.members["kim"].first = "Zed"
    c.info.update(names="full", name="Other Club", callsign="N0CALL")
    c._sections = [{"name": "New Business"}]
    c._yaml_links = [{"label": "New site", "url": "https://example.org/new"}]
    clubmod.set_dates({"document": "%d/%m/%Y", "list": "%d/%m/%y", "day": "%a %d/%m"})


def test_published_minutes_keep_their_names_order_heading_links_and_dates(tmp_path):
    a = archive(tmp_path)
    a.club._yaml_links = [{"label": "Old site", "url": "https://example.org/old"}]
    stamp(a)
    m = a.by_slug[SLUG]
    before = snapshot.View(m, a.club)
    then = (before.name("kim"), before.shown_name("kim"), before.sections, before.heading, before.links, before.longdate(m.date))
    try:
        change_the_club(a)
        v = snapshot.View(m, a.club)
        assert (v.name("kim"), v.shown_name("kim"), v.sections, v.heading, v.links, v.longdate(m.date)) == then
        assert v.shown(m.items("Upcoming Events")[0]).startswith("Sat Nov 21, 10:00")  # the day format of that night
        assert v.unnamed("Sam Kim built a dipole", "kim") == "built a dipole"  # the name as it was, not "Zed"
        assert (v.heading, v.longdate(m.date), v.links) == ("W0TEST · Test Club", "October 15, 2026",
                                                            [{"label": "Old site", "url": "https://example.org/old"}])
        assert v.sections != a.club.sections() and "Upcoming Chow" in v.sections
    finally:
        clubmod.set_dates(None)


def test_a_draft_follows_todays_state(tmp_path):
    a = archive(tmp_path)
    d = Meeting(date=dt.date(2026, 11, 5), status="draft", in_person=["kim"])
    assert d.shown == {}
    try:
        change_the_club(a)
        v = snapshot.View(d, a.club)
        assert (v.name("kim"), v.heading, v.longdate(d.date)) == ("Zed Kim", "N0CALL · Other Club", "05/11/2026")
        assert v.sections == d.section_order(a.club.sections())
    finally:
        clubmod.set_dates(None)


def test_the_page_shows_the_snapshot_and_submit_and_a_correction_take_their_own(tmp_path, monkeypatch):
    a, c = web_as(tmp_path, monkeypatch, "n0call")
    stamp(a)
    try:
        change_the_club(a)
        page = c.get(f"/meetings/{SLUG}").text
        assert "Sam Kim" in page and "Zed" not in page and "October 15, 2026" in page and "Other Club" not in page
        m = a.by_slug[SLUG]  # a correction takes the club as it is now; version 1 keeps its own
        corrections.correct(a, SLUG, "roe", "Typo", 1, edit(m, visitors=2), notice=False)
        assert a.by_slug[SLUG].shown["names"]["kim"]["name"] == "Zed Kim" and a.by_slug[SLUG].shown["heading"] == "N0CALL · Other Club"
        assert corrections.load_version(a, SLUG, 1).shown["names"]["kim"]["name"] == "Sam Kim"
        d = console.start(a, dt.date(2026, 11, 5), "doe")  # a draft has none; submitting takes the club of that moment
        assert d.shown == {} and console.submit(a, d.slug).shown["heading"] == "N0CALL · Other Club"
    finally:
        clubmod.set_dates(None)


def test_the_pdf_is_made_once_kept_and_never_served_to_who_may_not_see_it(tmp_path, monkeypatch):
    from club_minutes import web
    a, member = web_as(tmp_path, monkeypatch, "n0call")
    stamp(a)
    made = []
    real = pdf.render_pdf
    monkeypatch.setattr(pdf, "render_pdf", lambda *x: made.append(1) or real(*x))
    first = member.get(f"/meetings/{SLUG}/minutes.pdf")
    second = member.get(f"/meetings/{SLUG}/minutes.pdf")
    assert first.content == second.content and first.content.startswith(b"%PDF") and len(made) == 1
    assert first.headers["content-disposition"].startswith("attachment;")
    assert first.headers["content-type"] == "application/octet-stream"  # iPhone Safari shows any application/pdf in the tab  # Download PDF downloads (iOS opened inline ones in the tab)
    assert (a.root / SLUG / "minutes.v1.pdf").read_bytes() == first.content and not list((a.root / SLUG).glob("*.tmp"))
    m = a.by_slug[SLUG]
    corrections.correct(a, SLUG, "roe", "Took out a private detail", 1, edit(m, visitors=1), notice=False, private=True)
    assert member.get(f"/meetings/{SLUG}/minutes.pdf?v=1").status_code == 404  # the kept file is there, but not for them
    editor = TestClient(web.app)
    editor.cookies.set(web.COOKIE, web.authn.new_session("kim"))
    assert editor.get(f"/meetings/{SLUG}/minutes.pdf?v=1").content == first.content
    assert member.get(f"/meetings/{SLUG}/minutes.pdf").status_code == 200 and (a.root / SLUG / "minutes.v2.pdf").exists()  # its own


def test_the_upgrade_snapshots_old_minutes_and_their_versions_once_and_undoes(tmp_path):
    archive(tmp_path / "meetings")
    d = tmp_path / "meetings" / SLUG
    (d / "minutes.v1.md").write_text((d / "minutes.md").read_text())  # an earlier version, as corrections.py keeps it
    Meeting(date=dt.date(2026, 11, 5), status="draft", in_person=["kim"]).save(tmp_path / "meetings")
    (tmp_path / "data-version").write_text("3\n")
    assert "shown" not in (d / "minutes.md").read_text()
    assert upgrade.run(tmp_path) == [upgrade.STEPS[-1][1]]
    for p in (d / "minutes.md", d / "minutes.v1.md", tmp_path / "meetings" / "2025-10-23" / "minutes.md"):
        assert Meeting.load(p).shown["names"]
    assert Meeting.load(tmp_path / "meetings" / "2026-11-05" / "minutes.md").shown == {}  # drafts stay live
    done = (d / "minutes.md").read_text()
    upgrade._v4(tmp_path)  # again: nothing changes
    assert (d / "minutes.md").read_text() == done
    assert upgrade.undo(3, tmp_path) and "shown" not in (d / "minutes.md").read_text() and upgrade.version(tmp_path) == 3


def test_a_crash_mid_upgrade_keeps_the_clean_copy_and_a_rerun_finishes(tmp_path, monkeypatch):
    archive(tmp_path / "meetings")
    (tmp_path / "data-version").write_text("3\n")
    real, n = Meeting.write, []

    def flaky(self, p):
        if n:
            raise OSError("disk went away")
        n.append(1)
        real(self, p)
    monkeypatch.setattr(Meeting, "write", flaky)
    with pytest.raises(OSError):
        upgrade.run(tmp_path)
    assert upgrade.version(tmp_path) == 3 and (tmp_path / upgrade.STARTED).exists()
    monkeypatch.setattr(Meeting, "write", real)
    assert upgrade.run(tmp_path)
    assert upgrade.version(tmp_path) == 4 and not (tmp_path / upgrade.STARTED).exists()
    assert not any("shown:" in p.read_text() for p in (tmp_path / upgrade.COPY).rglob("minutes*.md"))


def test_a_mentioned_members_name_is_kept_with_the_minutes(tmp_path):
    a = archive(tmp_path)
    m = a.by_slug[SLUG]
    m.sections["Old Business"][0].text = "Thanks @[Jane D](doe) and @[Gone](nobody)"
    m.shown = snapshot.take(a.club, m)
    assert "doe" in m.shown["names"] and "nobody" not in m.shown["names"]
    a.club.members["doe"].first = "Zed"
    v = snapshot.View(m, a.club)
    assert v.known_name("doe") == m.shown["names"]["doe"]["shown"] != a.club.known_name("doe")
    assert 'class="who"' in str(richtext.render(m.sections["Old Business"][0].text, names=v)) and "Gone" in str(richtext.render("@[Gone](nobody)", names=v))


def test_a_never_record_mention_is_not_frozen_or_shown(tmp_path):
    a = archive(tmp_path)
    m = a.by_slug[SLUG]
    m.sections.setdefault("Old Business", []).append(__import__("club_minutes.meeting").meeting.Item("Thanks @[Kim](kim)"))
    m.shown = snapshot.take(a.club, m)  # frozen with Kim's name, before the club made it never-record
    a.club.redact = [clubmod._n(a.club.shown_name("kim"))]
    assert not any(x["id"] == "kim" for x in a.club.mentionable())
    assert str(richtext.render("@[Kim](kim)", names=snapshot.View(m, a.club))) == "a member"


def test_minutes_from_before_1_2_keep_1_1s_report_layout(tmp_path, monkeypatch):
    """Found on stage 2026-11-21: 1.2's name pill and formatting changed minutes published under 1.1. The upgrade marks
    them; they show each report as typed, with the short name after it (on screen only). A correction is made today: the new layout
    (Jim, 2026-11-21); version 1 keeps the old."""
    a, c = web_as(tmp_path, monkeypatch, "n0call")
    m = a.by_slug[SLUG]
    m.sections["Community Reports"] = [Item("Sam K - net at *8pm` on https://example.org", "kim")]
    m.save(a.root)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "meetings").symlink_to(a.root)  # the upgrade's view of this archive
    upgrade._v4(tmp_path / "data")
    a.reload()
    assert a.by_slug[SLUG].shown["layout"] == 1
    page = c.get(f"/meetings/{SLUG}").text
    assert "Sam K - net at *8pm` on " in page and 'href="https://example.org"' in page  # as typed: no pill first, no Markdown
    assert page.index("Sam K - net") < page.index('class="who"')  # the name after the report, as 1.1 had it
    assert "items reports" not in page and "items lined" not in page  # and no line between items: 1.2 brought it
    corrections.correct(a, SLUG, "roe", "Typo", 1, edit(a.by_slug[SLUG], visitors=2), notice=False)
    assert a.by_slug[SLUG].shown["layout"] == 2 and corrections.load_version(a, SLUG, 1).shown["layout"] == 1
    page = c.get(f"/meetings/{SLUG}").text  # version 2: the pill first, the typed name left off
    assert page.index('class="who"') < page.index("net at") and "Sam K - net" not in page


def test_only_minutes_made_now_get_a_line_between_every_sections_items(tmp_path, monkeypatch):
    """Jim, 2026-11-22: the line between items in every section leaves minutes made before it as they were."""
    a, c = web_as(tmp_path, monkeypatch, "n0call")
    m = a.by_slug[SLUG]
    m.shown = snapshot.take(a.club, m)
    del m.shown["layout"]  # as 1.2 made it: lines in Community Reports only
    m.save(a.root)
    a.reload()
    assert 'class="items lined"' not in c.get(f"/meetings/{SLUG}").text
    stamp(a)  # made today
    assert 'class="items lined"' in c.get(f"/meetings/{SLUG}").text


def test_published_minutes_keep_their_logo_and_colours_and_a_correction_takes_todays(tmp_path, monkeypatch):
    """Jim, 2026-11-21: old minutes keep the logo and colours they were published with; an edited version takes the new."""
    import io

    from PIL import Image

    from club_minutes import branding

    def png(rgb):
        b = io.BytesIO()
        Image.new("RGBA", (64, 64), rgb).save(b, "PNG")
        return b.getvalue()

    monkeypatch.setattr(branding, "DATA", tmp_path / "brand")
    (tmp_path / "brand").mkdir()
    a, c = web_as(tmp_path, monkeypatch, "n0call")
    branding.save("logo", png((200, 30, 30, 255)))
    branding.set_colours("#aa2200", None, "doe")
    stamp(a)
    kept = a.by_slug[SLUG].shown["logo"]
    branding.save("logo", png((30, 30, 200, 255)))  # a new logo and colours on Site
    branding.set_colours("#0044aa", None, "doe")
    page = c.get(f"/meetings/{SLUG}").text
    from club_minutes import theme
    assert f'src="/brand/kept/{kept}.png"' in page and theme.css("#aa2200", None, " .minutes") in page  # quotes unescaped: dark mode works
    assert c.get(f"/brand/kept/{kept}.png").content == branding.kept_logo(kept).read_bytes() != (branding._dir() / "logo.png").read_bytes()
    assert c.get("/brand/kept/..%2Fx.png").status_code == 404 and c.get("/brand/kept/0000000000000000.png").status_code == 404
    v = snapshot.View(a.by_slug[SLUG], a.club)
    assert v.logo(True) == branding.kept_logo(kept).as_uri() and v.colours_css.startswith(":root .minutes")
    corrections.correct(a, SLUG, "roe", "Typo", 1, edit(a.by_slug[SLUG], visitors=2), notice=False)
    assert a.by_slug[SLUG].shown["logo"] != kept and a.by_slug[SLUG].shown["colours"]["main"] == "#0044aa"
    assert corrections.load_version(a, SLUG, 1).shown["logo"] == kept  # version 1 keeps its own
