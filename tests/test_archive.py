from pathlib import Path

import datetime as dt
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
    assert m.guests == [{"name": "Pat Guest", "callsign": "KE8ABC", "notes": "2nd meeting"}]
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
    assert club.speaker_of("Jane nothing.", ["doe", "dunn"]) is None
    assert club.speaker_of("Jane D was busy") == "doe"


def test_file_date():
    assert str(file_date("29-jan-26-minutes-R1JCmreIiha6nmwZ.pdf")) == "2026-01-29"
    assert str(file_date("10-sept-26-minutes.pdf")) == "2026-09-10"


def test_permissions_and_possessives():
    assert club.can_lead("doe") and not club.can_lead("kim")
    assert club.speaker_of("Sams antenna fell down") == "kim"


def test_former_callsign_maps_to_member(tmp_path, m):
    from club_minutes.store import Archive
    m.in_person = ["doe", "kd8old"]  # stored before Sam's callsign change
    m.save(tmp_path)
    a = Archive(tmp_path, club)
    assert a.meetings[0].in_person == ["doe", "kim"]
    assert club.members["kim"].aliases == []  # not treated as a name spelling


def test_membership_log():
    from club_minutes.roster import attach_log, to_members
    ms = to_members([{"full_name": "Sam Kim", "callsign": "KD8NEW", "is_officer": "True"},
                     {"full_name": "Old Timer", "status": "Silent Key"}])
    attach_log(ms, [{"callsign": "KD8NEW", "date": "46043", "event": "Joined"},
                    {"callsign": "", "full_name": "Old Timer", "date": "", "event": "Silent Key"}])
    assert ms[0]["officer"] and ms[0]["member_since"] == "2026-01-21"
    assert ms[1]["id"] == "old-timer" and ms[1]["events"][0]["event"] == "Silent Key"


def test_roster_contact_details_follow_sharing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import web
    c = Club(FIX / "test-club.yaml", roster=[
        {"id": "kd8aaa", "first": "Jane", "last": "Doe", "callsign": "KD8AAA", "email": "jane@example.org",
         "phone": "419-555-0101", "address": "1 Elm St", "birthday": "07-04", "share": ["phone", "birthday"],
         "license_class": "Extra", "role": "Secretary", "officer": True, "member_since": "2024-03-07"},
        {"id": "kd8ccc", "first": "Bob", "last": "Member", "callsign": "KD8CCC", "email": "bob@example.org",
         "phone": "419-555-0199", "address": "9 Oak Ave"},
        {"id": "kd8bbb", "first": "Old", "last": "Timer", "status": "Silent Key", "member_since": "not a date"}])
    monkeypatch.setattr(web, "archive", Archive(tmp_path, c))
    monkeypatch.setattr(web.templates.env, "globals", {**web.templates.env.globals, "club": c})
    viewer = {"id": "kd8ccc"}
    monkeypatch.setattr(web.authn, "member_for", lambda token: viewer["id"])
    client = TestClient(web.app)

    html = client.get("/roster?status=all").text  # Bob, a regular member
    assert "KD8AAA" in html and "Secretary" in html and "Mar 7, 2024" in html and "badge\">Silent Key" in html
    assert "419-555" not in html and "@example.org" not in html  # contact details live on member pages
    assert html.index("Doe") < html.index("Old Timer")
    assert "badge\">Silent Key" not in client.get("/roster").text  # Active only by default
    assert client.get("/roster/changes").status_code == 403     # officers only

    jane = client.get("/members/kd8aaa").text
    assert "419-555-0101" in jane and "July 4" in jane              # Jane shared these
    assert "jane@example.org" not in jane and "1 Elm St" not in jane  # but not these
    own = client.get("/members/kd8ccc").text
    assert "bob@example.org" in own and "9 Oak Ave" in own and 'href="/profile"' in own  # Bob sees his own
    assert "Contact" not in client.get("/members/kd8bbb").text      # nothing to show: no section

    viewer["id"] = "kd8aaa"  # Jane, an officer, sees everything, with unshared details marked
    bob = client.get("/members/kd8ccc").text
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
    assert rows(html) == 1 and '<option selected>%d</option>' % dt.date.today().year in html
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
    assert "1 meeting in %d" % dt.date.today().year in client.get("/stats").text  # this year by default

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
    assert ">fastapi<" in html.lower() and "What's new" in html
    folds = html.count("<details")  # one per release, only the newest open
    assert folds > 3 and html.count("<details open>") == 1 and html.split('class="changelog"')[1].split("<details")[1].startswith(" open>")
    assert 'href="/about"' in c.get("/links").text  # in the name menu on every page


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
        for i, js in enumerate(re.findall(r"<script>(.*?)</script>", client.get(path).text, re.S)):
            f = tmp_path / f"s{i}.js"
            f.write_text(js)
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
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
            d.meeting_weekday, d.meeting_time, d.timezone
    monkeypatch.setattr(club_mod.os.path, "realpath", lambda p: "/usr/share/zoneinfo/Europe/Berlin")
    bare = Club(FIX / "test-club.yaml")
    bare.info.pop("timezone")
    assert bare.timezone == "Europe/Berlin"  # none in club.yaml: the server's own
    monkeypatch.setattr(club_mod.os.path, "realpath", lambda p: "/etc/localtime")
    assert bare.timezone == "UTC"
