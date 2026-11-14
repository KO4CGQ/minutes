import datetime as dt
import re
from pathlib import Path

import pytest

from club_minutes.club import Club
from club_minutes.importer import file_date, parse
from club_minutes.meeting import Meeting
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
club = Club(FIX / "test-club.yaml")


@pytest.fixture(scope="module")
def m():
    return parse((FIX / "old-format.txt").read_text(), club)


def test_parse(m):
    assert str(m.date) == "2026-11-05"
    assert sorted(m.in_person) == ["doe", "kim"]
    assert m.remote == ["roe"]  # "Rowe" alias resolved
    assert m.guests == [{"name": "Pat Guest", "callsign": "N0YABC", "notes": "2nd meeting"}]
    assert m.items("Financial Report") == []  # empty template dropped
    assert m.items("Upcoming Chow") == []  # "See attached list" dropped
    reports = m.items("Community Reports")
    assert [r.member for r in reports] == ["roe", "doe", "kim"]  # "Jane": only Doe was present
    assert reports[1].text == "Jane worked on a long report that wraps onto a second line."


def test_never_record_names_are_dropped(m):
    assert "Tiny" not in m.to_markdown()


def test_markdown_round_trip(m):
    assert Meeting.from_markdown(m.to_markdown()) == m


def test_ambiguous_first_name():
    assert club.speaker_of("Jane nothing.", ["doe", "dummy"]) is None
    assert club.speaker_of("Jane D was busy") == "doe"


def test_file_date():
    assert str(file_date("29-jan-26-minutes-R1JCmreIiha6nmwZ.pdf")) == "2026-01-29"
    assert str(file_date("10-sept-26-minutes.pdf")) == "2026-09-10"


def test_permissions_and_possessives():
    assert club.can_lead("doe") and not club.can_lead("kim")
    assert club.speaker_of("Sams antenna fell down") == "kim"


def test_former_callsign_maps_to_member(tmp_path, m):
    from club_minutes.store import Archive
    m.in_person = ["doe", "n0oldc"]  # stored before Sam's callsign change
    m.save(tmp_path)
    a = Archive(tmp_path, club)
    assert a.meetings[0].in_person == ["doe", "kim"]
    assert club.members["kim"].aliases == []  # not treated as a name spelling


def test_membership_log():
    from club_minutes.roster import attach_log, to_members
    ms = to_members([{"full_name": "Sam Kim", "callsign": "N0NEWC", "is_officer": "True"},
                     {"full_name": "Old Timer", "status": "Silent Key"}])
    attach_log(ms, [{"callsign": "N0NEWC", "date": "46043", "event": "Joined"},
                    {"callsign": "", "full_name": "Old Timer", "date": "", "event": "Silent Key"}])
    assert ms[0]["officer"] and ms[0]["member_since"] == "2026-01-21"
    assert ms[1]["id"] == "old-timer" and ms[1]["events"][0]["event"] == "Silent Key"


def test_roster_contact_details_follow_sharing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    c = Club(FIX / "test-club.yaml", roster=[
        {"id": "n0aaaa", "first": "Jane", "last": "Doe", "callsign": "N0AAAA", "email": "jane@example.org",
         "phone": "419-555-0101", "address": "1 Elm St", "birthday": "07-04", "share": ["phone", "birthday"],
         "license_class": "Extra", "role": "Secretary", "officer": True, "member_since": "2024-03-07"},
        {"id": "n0cccc", "first": "Bob", "last": "Member", "callsign": "N0CCCC", "email": "bob@example.org",
         "phone": "419-555-0199", "address": "9 Oak Ave"},
        {"id": "n0bbbb", "first": "Old", "last": "Timer", "status": "Silent Key", "member_since": "not a date"}])
    monkeypatch.setattr(web, "archive", Archive(tmp_path, c))
    monkeypatch.setattr(web.templates.env, "globals", {**web.templates.env.globals, "club": c})
    viewer = {"id": "n0cccc"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: viewer["id"])
    client = TestClient(web.app)

    html = client.get("/roster?status=all").text  # Bob, a regular member
    assert "N0AAAA" in html and "Secretary" in html and "Mar 7, 2024" in html and "badge\">Silent Key" in html
    assert "419-555" not in html and "@example.org" not in html  # contact details live on member pages
    assert html.index("Doe") < html.index("Old Timer")
    assert "badge\">Silent Key" not in client.get("/roster").text  # Active only by default
    assert client.get("/roster/changes").status_code == 403     # officers only

    jane = client.get("/members/n0aaaa").text
    assert "419-555-0101" in jane and "July 4" in jane              # Jane shared these
    assert "jane@example.org" not in jane and "1 Elm St" not in jane  # but not these
    own = client.get("/members/n0cccc").text
    assert "bob@example.org" in own and "9 Oak Ave" in own and 'href="/profile"' in own  # Bob sees his own
    assert "Contact" not in client.get("/members/n0bbbb").text      # nothing to show: no section

    viewer["id"] = "n0aaaa"  # Jane, an officer, sees everything, with unshared details marked
    bob = client.get("/members/n0cccc").text
    assert "bob@example.org" in bob and "9 Oak Ave" in bob and "Only officers can see this" in bob
    assert client.get("/roster/changes").status_code == 200


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A signed-in test client over a throwaway archive of weekly meetings, 30 in 2024 and 3 in 2025."""
    import datetime as dt

    from fastapi.testclient import TestClient

    from club_minutes import web
    for i in range(30):
        Meeting(date=dt.date(2024, 1, 4) + dt.timedelta(weeks=i), status="published", in_person=["doe"]).save(tmp_path)
    for d in (dt.date(2025, 1, 2), dt.date(2025, 1, 9), dt.date.today()):
        Meeting(date=d, status="published", in_person=["doe", "kim"]).save(tmp_path)
    monkeypatch.setattr(web, "archive", Archive(tmp_path, club))
    monkeypatch.setattr(web.authn, "member_for", lambda token: "doe")
    return TestClient(web.app)


def rows(html):
    return html.count('<tr><td data-sort=')


def test_home_page_and_old_archive_links(client):
    html = client.get("/").text
    assert "Next meeting" in html and "Your chow night" in html and "Latest minutes" in html
    r = client.get("/?period=all", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/minutes?period=all"


def test_archive_periods_pages_and_page_size(client):
    html = client.get("/minutes").text  # this year by default
    assert rows(html) == 1 and f'<option selected>{dt.date.today().year}</option>' in html
    html = client.get("/minutes?period=all").text
    assert rows(html) == 25 and "Page 1 of 2" in html and html.count('class="yearrow"') == 3  # this year, 2025, 2024
    assert rows(client.get("/minutes?period=all&page=2").text) == 8
    assert rows(client.get("/minutes?period=all&per=0").text) == 33 and rows(client.get("/minutes?period=all&per=10&page=4").text) == 3
    p1, p2 = client.get("/minutes?period=2024").text, client.get("/minutes?period=2024&page=2").text
    assert rows(p1) == 25 and rows(p2) == 5 and "Page 2 of 2" in p2
    assert rows(client.get("/minutes?period=2024&page=99").text) == 5  # clamped to the last page
    assert rows(client.get("/minutes?period=6m").text) == 1 and rows(client.get("/minutes?period=12m").text) == 1
    for junk in ("/minutes?period=", "/minutes?period=abc", "/minutes?period=2024&page=x", "/minutes?per=7", "/minutes?per=x"):
        r = client.get(junk)
        assert r.status_code == 200 and "Period" in r.text


def test_roster_shows_active_by_default(client):
    html = client.get("/roster").text
    assert "Status" in html and client.get("/roster?status=all").status_code == 200


def test_stats_periods(client):
    assert "33 meetings on record" in client.get("/stats?period=all").text
    html = client.get("/stats?period=2024").text
    assert "30 meetings in 2024" in html and "Page 1 of 3" in html
    assert html.count('class="bar"') == 30  # the chart has every meeting in the period, not just the table's page
    assert 'class="chart"' not in client.get("/stats?period=12m").text  # one meeting: no chart
    assert "1 meeting in the last 12 months" in client.get("/stats?period=12m").text
    assert f"1 meeting in {dt.date.today().year}" in client.get("/stats").text  # this year by default

def test_guests_page_moves_new_members_to_their_own_section(tmp_path, monkeypatch):
    import datetime as dt

    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.store import Archive
    for d in (dt.date(2026, 10, 1), dt.date(2026, 10, 8)):
        Meeting(date=d, status="published", guests=[{"name": "Rick Roe"}, {"name": "Pat Guest"}]).save(tmp_path)
    Meeting(date=dt.date(2026, 10, 15), status="published", in_person=["roe"]).save(tmp_path)
    monkeypatch.setattr(web, "archive", Archive(tmp_path, club))
    monkeypatch.setattr(web.authn, "member_for", lambda token: "doe")
    html = TestClient(web.app).get("/guests").text
    current, joined = html.split("<h2>Became members")
    assert "Pat Guest" in current and "Rick Roe" not in current
    assert "Rick Roe" in joined and "Oct 15, 2026" in joined and "after 2.0 visits" in joined


def test_bylaws_page(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    monkeypatch.setattr(web, "DATA", tmp_path)
    monkeypatch.setattr(web.authn, "member_for", lambda token: "doe")
    c = TestClient(web.app)
    assert "haven't been added yet" in c.get("/bylaws").text
    (tmp_path / "bylaws.md").write_text("# Constitution\n\n### Article I\n\n**A.** Meetings <b>weekly</b>.")
    html = c.get("/bylaws").text
    assert "<h1>Constitution</h1>" in html and "<strong>A.</strong>" in html


def test_links_page_lists_club_links(monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    monkeypatch.setattr(web.archive.club, "_yaml_links", [{"label": "Club Discord", "url": "https://discord.example/x"}])
    monkeypatch.setattr(web.authn, "member_for", lambda token: "doe")
    html = TestClient(web.app).get("/links").text
    assert 'href="https://discord.example/x"' in html and "Club Discord" in html and 'href="/links"' in html


def test_links_to_pages_of_the_app_stay_relative(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.club import Club
    monkeypatch.setattr(web.authn, "member_for", lambda token: "doe")
    monkeypatch.setattr(web.authn, "rp_id", "minutes.example")
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    saved = []
    monkeypatch.setattr(Club, "save_links", lambda self, links: saved.extend(links))
    form = {"label": ["Bylaws", "Calendar", "Discord"], "url": ["https://Minutes.example/bylaws", "/calendar", "https://discord.example/x"],
            "icon": ["", "", ""], "show": ["both", "both", "both"]}
    assert TestClient(web.app).post("/links", data=form, follow_redirects=False).headers["location"] == "/links"
    assert [x["url"] for x in saved] == ["/bylaws", "/calendar", "https://discord.example/x"]  # the app's own address: made relative
    assert web.abs_url("/bylaws") == "https://minutes.example/bylaws" and web.abs_url("https://x.example") == "https://x.example"
    assert web.link_host("/bylaws") == "on this site"
    bad = {**form, "url": ["//evil.example/x", "/calendar", "https://discord.example/x"]}
    assert "error=" in TestClient(web.app).post("/links", data=bad, follow_redirects=False).headers["location"]


def test_link_icons():
    from club_minutes.web import link_icon
    assert link_icon("https://teams.live.com/meet/1?p=x") == "video"
    assert link_icon("https://discord.com/invite/abc") == "chat"
    assert link_icon("https://minutes.example.ts.net/bylaws") == "document"
    assert link_icon("https://example.org") == "globe"
    assert link_icon("https://example.org/some/page") == "link"


def test_about_page(monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    monkeypatch.setattr(web.authn, "member_for", lambda token: "doe")
    c = TestClient(web.app)
    html = c.get("/about").text
    assert web.version("club-minutes") in html and "James Flattery, KO4CGQ" in html and "AGPL-3.0-or-later" in html
    assert "About Club Minutes by KO4CGQ" in html and "for amateur radio clubs." in html  # the product, not this club
    assert ">fastapi<" in html.lower() and "What's new" in html
    folds = html.count("<details")  # one per release, only the newest open
    assert folds >= 1 and html.count("<details open>") == 1 and html.split('class="changelog"')[1].split("<details")[1].startswith(" open>")
    assert 'href="/about"' in c.get("/links").text  # in the name menu on every page


def test_breadcrumbs_back(client):
    from club_minutes import web
    c, slug = client, web.archive.meetings[0].slug
    back = lambda url: re.search(r'class="crumb"[^>]*><a href="([^"]+)">‹ ([^<]+)</a>', c.get(url).text).groups()
    assert back(f"/meetings/{slug}") == ("/minutes", "Minutes")
    assert back(f"/meetings/{slug}?from=stats") == ("/stats", "Attendance") and f"/meetings/{slug}?from=stats" in c.get("/stats").text
    assert back("/calendar/subscribe") == ("/calendar", "Calendar")
    mid = next(iter(web.archive.club.members))  # a member: back to the Roster, or to the list they were opened from
    assert back(f"/members/{mid}") == ("/roster", "Roster") and back(f"/members/{mid}?from=treasurer") == ("/treasurer", "Treasurer")
    assert back(f"/members/{mid}?from=junk") == ("/roster", "Roster") and f"/members/{mid}?from=stats" in c.get("/stats").text
    assert back(f"/members/{mid}?from=business&item=ab12") == ("/business#iab12", "Business")  # back to the item, open
    assert back(f"/members/{mid}?from=business&item=ab12&tab=done") == ("/business?tab=done#iab12", "Business")
    assert back(f"/members/{mid}?from=home&item=ab12") == ("/#iab12", "Home")
    assert back(f"/members/{mid}?from=business&item=x%22%3E") == ("/business", "Business")  # only a plain id goes in the link
    assert back("/business?from=home") == ("/", "Home") and back(f"/meetings/{slug}?from=home") == ("/", "Home")  # from Home's cards
    assert back(f"/members/{mid}?from=guests") == ("/guests", "Guests") and back(f"/members/{mid}?from=changes") == ("/roster/changes", "Roster changes")
    assert back("/offices") == ("/roster", "Roster")
    assert 'href="/business?from=home"' in c.get("/").text and 'class="crumb"' not in c.get("/business").text  # from the menu: none


def test_other_sites_open_in_a_new_tab():
    from club_minutes.web import new_tabs
    html = ('<a href="https://www.eff.org/x">a</a><a class="btn" href="https://meet.example.org/club">b</a><a href="/calendar">c</a>'
            '<a href="https://minutes.example.net/x">d</a><a href="mailto:jane@example.org">e</a><a target="_self" href="https://example.org">f</a>')
    out = new_tabs(html, "minutes.example.net")
    assert out.count('target="_blank" rel="noopener"') == 2 and '<a href="/calendar">' in out and '<a href="https://minutes.example.net/x">' in out


def test_help_pages_show_the_guides(monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    monkeypatch.setattr(web.authn, "member_for", lambda token: "doe")
    monkeypatch.setattr(web.archive.club, "can_lead", lambda mid: False)
    monkeypatch.setattr(web.archive.club, "is_admin", lambda mid: False)
    c = TestClient(web.app)
    html = c.get("/help").text
    assert ">Members guide</h1>" in html and 'aria-label="Guide"' not in html  # a member: just their guide
    assert 'href="/help"' in c.get("/links").text
    assert '<a target="_blank" rel="noopener" href="https://www.eff.org/' in html  # other sites open in a new tab
    for g in web.GUIDES:  # every link in a guide works in the app: another guide, or a full address
        body = c.get(f"/help/{g}").text.split('class="minutes guide"')[1]
        assert all(h.startswith(("/help", "https://", "#")) for h in re.findall(r'href="([^"]*)"', body)), g
        assert not [x for x in re.findall(r"<p>(.*?)</p>", body, re.DOTALL) if re.search(r"\n(- |\d+\. )", x)], g  # no list run into a paragraph
        assert "[!" not in body and all(s.startswith("/help/screenshots/") for s in re.findall(r'<img[^>]* src="([^"]*)"', body)), g
        assert all(c.get(s).status_code == 200 for s in re.findall(r'<img[^>]* src="([^"]*)"', body)), g  # every picture is there
    off = c.get("/help/officers").text  # every guide is public anyway; the link to the next stays in the app
    assert 'href="/help/members"' in off and "Server admin</a>" not in off
    admin = c.get("/help/server-admin").text
    assert "<pre><code" in admin and "Server admin</a>" in admin
    assert 'popovertarget="shot1"' in admin and '<div popover id="shot1"' in admin and "<p><button" not in admin  # pictures open full size
    assert c.get("/help/developers").status_code == 404 and c.get("/help/..%2Fsecrets").status_code == 404
    monkeypatch.setattr(web.archive.club, "is_admin", lambda mid: True)
    assert all(f'href="/help/{g}"' in c.get("/help").text for g in ("members", "officers", "server-admin"))
    found = c.get("/search", params={"q": "passphrase", "kind": "help"}).text  # the search finds guide sections
    assert 'href="/help/server-admin#backups"' in found and 'id="backups"' in c.get("/help/server-admin").text
    monkeypatch.setattr(web.archive.club, "is_admin", lambda mid: False)
    assert "/help/server-admin#" not in c.get("/search", params={"q": "passphrase"}).text  # only the guides offered to you
    assert c.get("/help/screenshots/..%2Fofficers.md").status_code == 404


def test_top_bar_links_everyone_to_tonights_live_meeting(client, monkeypatch):
    from club_minutes import web
    assert "Live meeting" not in client.get("/").text
    tonight = next(m for m in web.archive.meetings if m.date == dt.date.today())
    monkeypatch.setattr(tonight, "status", "draft")
    assert f'href="/meetings/{tonight.slug}"' in client.get("/").text.split("</nav>")[0]


def test_console_link_in_the_live_badge_for_leaders_only(client, monkeypatch):
    from club_minutes import web
    tonight = next(m for m in web.archive.meetings if m.date == dt.date.today())
    monkeypatch.setattr(tonight, "status", "draft")
    link = f'href="/console/{tonight.slug}"'
    page = client.get(f"/meetings/{tonight.slug}").text
    assert link in page.split("Meeting in progress")[1].split("</p>")[0]
    monkeypatch.setattr(web.authn, "member_for", lambda token: "kim")  # a member who can't lead
    assert link not in client.get(f"/meetings/{tonight.slug}").text
    monkeypatch.setattr(tonight, "status", "published")
    monkeypatch.setattr(web.authn, "member_for", lambda token: "doe")
    assert link not in client.get(f"/meetings/{tonight.slug}").text  # only while the meeting is live

def test_roster_opens_as_cards_on_phones_until_a_view_is_picked(client):
    phone = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148"}
    assert 'class="membergrid"' in client.get("/roster", headers=phone).text
    client.get("/roster?view=list")  # remembered in a cookie
    assert 'class="membergrid"' not in client.get("/roster", headers=phone).text


def test_every_icon_is_drawn():
    """ICONS (the Links editor's picker) and _icons.html stay in step: each name has its own drawing."""
    from club_minutes import web
    m = web.templates.env.get_template("_icons.html").module
    drawn = {name: str(m.icon(name)) for name in web.ICONS}
    assert len(set(drawn.values())) == len(web.ICONS)
    assert web.link_icon("mailto:club@example.org") == "mail" and web.link_icon("https://youtu.be/x") == "play"


def test_inline_scripts_parse(client, tmp_path):
    """A syntax error in one inline script silently kills the whole page's menus; check them all with node."""
    import re
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node isn't installed (GitHub Actions has it)")
    for path in ("/", "/minutes", "/links", "/chow", "/business", "/profile"):
        for i, js in enumerate(re.findall(r"<script>(.*?)</script>", client.get(path).text, re.DOTALL)):
            f = tmp_path / f"s{i}.js"
            f.write_text(js)
            r = subprocess.run(["node", "--check", str(f)], check=False, capture_output=True, text=True)
            assert r.returncode == 0, f"{path} script {i}: {r.stderr}"


def test_join_online_is_the_first_video_link(client, monkeypatch):
    from club_minutes import web
    c = web.archive.club
    monkeypatch.setattr(c, "_yaml_links", [{"label": "Club Discord", "url": "https://discord.com/invite/x"},
                                           {"label": "Weekly meeting", "url": "https://us02web.zoom.us/j/123"}])
    assert 'href="https://us02web.zoom.us/j/123">Join online (Weekly meeting)' in client.get("/").text
    monkeypatch.setattr(c, "_yaml_links", [{"label": "Club Discord", "url": "https://discord.com/invite/x"}])
    assert "Join online" not in client.get("/").text


def test_the_meeting_day_time_and_zone_are_the_clubs_own(tmp_path, monkeypatch):
    from club_minutes import club as club_mod
    c = Club(FIX / "test-club.yaml")
    assert (c.meeting_weekday, c.meeting_time) == (3, "19:30")
    c.info.update(meeting_day=" tuesday ", meeting_time=1170)  # unquoted in YAML, 19:30 arrives as 1170 minutes
    assert (c.meeting_weekday, c.meeting_time) == (1, "19:30")
    for k, bad in (("meeting_day", None), ("meeting_day", "Thurs"), ("meeting_time", None), ("meeting_time", "7:30 PM"),
                   ("meeting_time", "24:00"), ("meeting_time", True), ("timezone", "Mars/Olympus")):
        d = Club(FIX / "test-club.yaml")
        d.info[k] = bad
        with pytest.raises(ValueError, match=f"club.yaml club: {k}"):
            _ = d.meeting_weekday, d.meeting_time, d.timezone
    monkeypatch.setattr(club_mod.os.path, "realpath", lambda p: "/usr/share/zoneinfo/Europe/Berlin")
    bare = Club(FIX / "test-club.yaml")
    bare.info.pop("timezone")
    assert bare.timezone == "Europe/Berlin"  # none in club.yaml: the server's own
    monkeypatch.setattr(club_mod.os.path, "realpath", lambda p: "/etc/localtime")
    assert bare.timezone == "UTC"


def test_dates_in_the_clubs_format():
    from club_minutes.club import set_dates, show_date
    from club_minutes.meeting import Item
    d = dt.date(2026, 11, 5)
    try:
        set_dates({"list": "%-d %b %Y", "document": "%-d %B %Y", "short": "%-d %b", "long": "%-d %B"})  # day first
        assert [show_date(d, k) for k in ("list", "short", "day", "weekday", "day_year")] == \
            ["5 Nov 2026", "5 Nov", "Thu 5 Nov", "Thursday, 5 November", "Thu 5 Nov 2026"]
        assert show_date("2026-11-05", "document") == "5 November 2026"
        assert Item("VE testing", when="2026-11-05").shown == "Thu 5 Nov: VE testing"
    finally:
        set_dates(None)
    assert show_date(d, "weekday") == "Thursday, November 5"
    for bad in ({"list": 5}, {"lsit": "%b %-d"}, {"short": ""}, ["%b"]):  # stops the app at start, not every page later
        with pytest.raises(ValueError, match="date_formats"):
            set_dates(bad)
    set_dates({"list": "%-d %b %Y"})  # one alone is fine; the rest keep their defaults
    assert (show_date(d), show_date(d, "short")) == ("5 Nov 2026", "Nov 5")
    set_dates(None)


def test_anydate_formats_dates_and_leaves_typed_text():
    """Dates in roster values, logs and calendar changes show in the club's format; anything else as typed."""
    import datetime as dt

    from club_minutes.web import anydate
    assert anydate("2025-07-25") == anydate(dt.date(2025, 7, 25)) and "2025" in anydate("2025-07-25")
    assert anydate("2025-07-25") != "2025-07-25"
    assert anydate("2026-11-05T19:30") == anydate("2026-11-05")
    assert anydate("spring 2019") == "spring 2019" and anydate("2025-13-45") == "2025-13-45" and anydate(None) == ""
