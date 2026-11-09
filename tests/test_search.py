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
