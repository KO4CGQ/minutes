import datetime as dt

from test_calview import isolated, signed_in  # noqa: F401  (isolated: the autouse fixture)

from w8lbz_minutes import business, chow, web
from w8lbz_minutes.meeting import Meeting
from w8lbz_minutes.store import Archive


def test_one_search_box_finds_members_guests_business_chow(tmp_path, monkeypatch):
    monkeypatch.setattr(business, "DATA", tmp_path)
    s = business.load()
    s["seeded"] = True
    business.add(s, "Buy a new antenna for the repeater", "kd8aaa", owner="kd8bbb")
    business.add(s, "Plan the picnic", "kd8aaa")
    business.save(s)
    night = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    c = chow.load()
    c["nights"] = {night: {"member": "kd8bbb"}}
    chow.save(c)
    root = tmp_path / "meetings"
    Meeting(date=dt.date(2026, 10, 1), status="published", guests=[{"name": "Gus Green", "callsign": "KD8GGG"}]).save(root)
    m, club, _ = signed_in(tmp_path, monkeypatch, "kd8aaa")
    club.members["kd8aaa"].callsign = "KD8AAA"
    monkeypatch.setattr(web, "archive", Archive(root, club))
    bob = m.get("/search?q=bob").text
    assert "/members/kd8bbb" in bob and "Buy a new antenna" in bob and "Plan the picnic" not in bob  # Bob owns it
    assert ">Chow</h2>" in bob and dt.date.fromisoformat(night).strftime("%a %b %-d, %Y") in bob
    assert "/members/kd8aaa" in m.get("/search?q=KD8AAA").text  # callsign, any case
    gus = m.get("/search?q=kd8ggg").text
    assert ">Guests</h2>" in gus and "Gus Green" in gus
    assert "Nothing found." in m.get("/search?q=zebra").text


def test_results_by_kind_a_page_at_a_time(tmp_path, monkeypatch):
    from w8lbz_minutes import events
    day = dt.date.today() + dt.timedelta(days=3)
    with events.locked():
        evs = []
        for i in range(12):
            events.add(evs, "events", events.from_form({"title": f"Fox hunt {i}", "date": (day + dt.timedelta(days=i)).isoformat()}), "x")
        events.save(evs)
    root = tmp_path / "meetings"
    root.mkdir()
    m, club, _ = signed_in(tmp_path, monkeypatch, "kd8aaa")
    monkeypatch.setattr(web, "archive", Archive(root, club))
    every = m.get("/search?q=fox").text
    assert "All (12)" in every and "Calendar (12)" in every and "Members (" not in every  # tabs only for kinds found
    assert every.count("Fox hunt") == 5 and "See all 12" in every and "kind=calendar" in every
    first = m.get("/search?q=fox&kind=calendar&per=10").text
    assert first.count("Fox hunt") == 10 and "Page 1 of 2" in first
    assert m.get("/search?q=fox&kind=calendar&per=10&page=2").text.count("Fox hunt") == 2
    assert "No members found." in m.get("/search?q=fox&kind=members").text
    assert "All (12)" in m.get("/search?q=fox&kind=junk").text  # an unknown kind: All


def test_text_typed_into_the_minutes_or_console_cannot_run_as_script(tmp_path, monkeypatch):
    from w8lbz_minutes.meeting import Item
    root = tmp_path / "meetings"
    Meeting(date=dt.date(2026, 10, 1), status="published", sections={"New Business": [Item("Antenna <img src=x onerror=alert(1)>")]}).save(root)
    m, club, _ = signed_in(tmp_path, monkeypatch, "kd8aaa")
    monkeypatch.setattr(web, "archive", Archive(root, club))
    page = m.get("/search?q=antenna").text
    assert "<img src=x" not in page and "<mark>Antenna</mark> &lt;img" in page
    s = business.load()
    s["seeded"] = True
    business.add(s, "</script><script>alert(1)</script>", "kd8aaa")
    business.save(s)
    Meeting(date=dt.date.today(), status="draft").save(root)
    monkeypatch.setattr(web, "archive", Archive(root, club))
    console = m.get(f"/console/{dt.date.today()}").text
    assert "\\u003c/script>\\u003cscript>alert(1)" in console and "</script><script>alert(1)" not in console


def test_other_spellings_of_a_name_and_close_spellings(tmp_path, monkeypatch):
    from w8lbz_minutes.meeting import Item
    from w8lbz_minutes.store import spellings
    root = tmp_path / "meetings"
    Meeting(date=dt.date(2026, 10, 1), status="published",
            sections={"Community Reports": [Item("Bobby Bakker passed his Extra exam")]}).save(root)
    m, club, _ = signed_in(tmp_path, monkeypatch, "kd8aaa")
    club.members["kd8bbb"].aliases = ["Bobby", "Bakker"]  # as the minutes spelled him
    names = spellings(club)
    assert {"bob", "bobby"} <= names["bob"] and "bakker" in names["baker"] and "bakker" not in names["bob"]
    monkeypatch.setattr(web, "archive", Archive(root, club))
    assert "passed his" in m.get("/search?q=Bob+Baker+Extra&kind=minutes").text  # the roster's other spellings
    assert "/members/kd8bbb" in m.get("/search?q=bakker").text  # and the other way round
    assert "passed his" in m.get("/search?q=Extre+exam&kind=minutes").text  # a typo: close spellings, when nothing else is found
    assert "No minutes found." in m.get("/search?q=Zebra+exam&kind=minutes").text


def _nick_env(tmp_path, monkeypatch, firsts: dict, reports: list[str]):
    from w8lbz_minutes.meeting import Item
    root = tmp_path / "meetings"
    Meeting(date=dt.date(2026, 10, 1), status="published",
            sections={"Community Reports": [Item(t) for t in reports]}).save(root)
    m, club, _ = signed_in(tmp_path, monkeypatch, "kd8aaa")
    for mid, first in firsts.items():
        club.members[mid].first = first
    monkeypatch.setattr(web, "archive", Archive(root, club))
    return m, club


def _minutes(m, q):
    return m.get("/search", params={"q": q, "kind": "minutes"}).text


def test_nicknames_find_each_other_for_names_on_the_roster(tmp_path, monkeypatch):
    m, club = _nick_env(tmp_path, monkeypatch, {"kd8bbb": "Richard"},
                        ["Rich passed his Extra exam", "Dick fixed the repeater", "Rick brought the coffee"])
    assert "passed his" in _minutes(m, "Richard Extra")          # Richard finds Rich
    assert "fixed the" in _minutes(m, "richard repeater")        # any case
    assert "brought the" in _minutes(m, "Dick coffee")          # nickname to nickname, through the group
    assert "/members/kd8bbb" in m.get("/search", params={"q": "Rick"}).text  # the member, by a nickname
    assert "passed his" in _minutes(m, "Rich Extra")             # what's typed still matches as a prefix too


def test_no_nicknames_without_someone_of_that_name(tmp_path, monkeypatch):
    m, _ = _nick_env(tmp_path, monkeypatch, {}, ["Rich passed his Extra exam"])
    assert "No minutes found." in _minutes(m, "Richard Extra")   # no Richard on the roster: no guessing


def test_nicknames_that_are_everyday_words_are_left_out(tmp_path, monkeypatch):
    from w8lbz_minutes.store import spellings
    m, club = _nick_env(tmp_path, monkeypatch, {"kd8bbb": "William"},
                        ["We will vote on the tower", "Paid the electric bill", "Billy ran the net"])
    assert "No minutes found." in _minutes(m, "William tower") and "No minutes found." in _minutes(m, "William electric")
    assert "ran the" in _minutes(m, "William net")                # Billy is a name, not a word
    names = spellings(club)
    assert "will" not in names["william"] and "bill" not in names["william"]
    club.members["kd8bbb"].first = "Bill"                         # someone actually called Bill: his own name counts
    assert "bill" in spellings(club)["william"] and "will" not in spellings(club)["bill"]
    club.members["kd8bbb"].aliases = ["Will"]                     # and Other spellings can add any of them
    assert "will" in spellings(club)["bill"]


def test_short_nicknames_match_whole_words_only(tmp_path, monkeypatch):
    m, club = _nick_env(tmp_path, monkeypatch, {"kd8bbb": "Alan"},
                        ["All members also voted", "Al brought donuts", "Almost done"])
    page = _minutes(m, "Alan")
    assert "brought donuts" in page and "members also" not in page and "Almost" not in page
    club.members["kd8bbb"].last = "Kalamazoo"                     # the lists: "al" inside a word isn't a match
    from w8lbz_minutes.web import _has
    assert _has("Alan", "Al Smith") and not _has("Alan", "Kalamazoo museum") and _has("Alan", "Alan Kalamazoo")


def test_a_nickname_in_several_groups(tmp_path, monkeypatch):
    m, _ = _nick_env(tmp_path, monkeypatch, {"kd8bbb": "Al"}, ["Albert set up the antenna", "Alfred ran the net"])
    assert "set up" in _minutes(m, "Al antenna") and "ran the" in _minutes(m, "Al net")  # Al could be either


def test_the_nickname_table():
    from w8lbz_minutes.store import COMMON_WORDS, NICK_GROUPS
    words = {w for g in NICK_GROUPS for w in g}
    assert all(len(g) >= 2 for g in NICK_GROUPS) and all(w.isalpha() and w.islower() for w in words)
    assert COMMON_WORDS <= words                                  # each one guards a real nickname
    assert any({"richard", "rich", "rick", "dick"} <= g for g in NICK_GROUPS) and {"james", "jim", "jimmy", "jamie"} in NICK_GROUPS
