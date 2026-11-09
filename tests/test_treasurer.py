"""Treasurer: what members owe (dues by the week, fees, missed chow), payments, certificates, bills from the minutes."""
import datetime as dt
from pathlib import Path

import pytest

from w8lbz_minutes import treasurer
from w8lbz_minutes.club import Club
from w8lbz_minutes.meeting import Item, Meeting
from w8lbz_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [
    {"id": "amy", "first": "Amy", "last": "Able", "member_since": "2020-03-05"},
    {"id": "bob", "first": "Bob", "last": "Baker", "member_since": "2026-11-12"},  # joined after tracking began
    {"id": "old", "first": "Old", "last": "Timer", "status": "Silent Key"},
]


@pytest.fixture
def club(tmp_path, monkeypatch):
    monkeypatch.setattr(treasurer, "DATA", tmp_path)
    c = Club(FIX / "test-club.yaml", roster=PEOPLE)
    c.info.update(meeting_day="Thursday", treasurer={"start": "2026-11-05", "accounts": ["Checking", "Cash"]})
    return c


def test_cents_and_money():
    assert [treasurer.cents(v) for v in ("3.50", "$1,200", "81", "-5.5", 12.5)] == [350, 120000, 8100, -550, 1250]
    assert treasurer.money(-1250) == "−$12.50" and treasurer.money(120000) == "$1,200.00"
    with pytest.raises(ValueError):
        treasurer.cents("12.345")


def test_standing_dues_by_the_week_chow_fees_and_payments(club):
    s, today = treasurer.load(), dt.date(2026, 12, 3)  # 5 Thursdays from Nov 5
    nights = {"2026-11-19": {"member": "amy", "status": "missed"}, "2026-10-01": {"member": "amy", "status": "missed"}}
    amy = treasurer.standing(club, s, "amy", today, nights)
    assert amy["owed"] == 5 * 350 + 3500 and amy["missed_chow"] == ["2026-11-19"]  # October's was before tracking
    assert amy["behind"] and amy["weeks_behind"] == 15
    treasurer.record(club, s, "tre", "dues", "17.50", "2026-12-03", "Cash", "amy")
    treasurer.record(club, s, "tre", "chow", "35", "2026-12-03", "Cash", "amy")
    amy = treasurer.standing(club, s, "amy", today, nights)
    assert amy["owed"] == 0 and not amy["behind"]
    assert amy["certificate_late"] and amy["certificate_due"] == "2022-03-05"
    treasurer.record(club, s, "tre", "certificate", "100", "2026-12-03", "Checking", "amy")
    assert treasurer.standing(club, s, "amy", today, nights)["certificate"]
    bob = treasurer.standing(club, s, "bob", today, {})
    assert bob["owed"] == 4 * 350 + 2500 and not bob["certificate_late"]  # 4 nights since joining, plus initiation
    assert treasurer.standing(club, s, "old", today, {}) is None


def test_amounts_that_change_and_paying_ahead(club):
    club.info["treasurer"].update(dues_weekly=[{"from": "2026-01-01", "amount": 3.50}, {"from": "2026-11-19", "amount": 4}],
                                  certificate=[{"from": "2000-01-01", "amount": 100}, {"from": "2026-11-10", "amount": 150}])
    s, today = treasurer.load(), dt.date(2026, 12, 3)
    amy = treasurer.standing(club, s, "amy", today, {})
    assert amy["owed"] == 2 * 350 + 3 * 400  # Nov 5 and 12 at the old rate, Nov 19 on at the new
    treasurer.record(club, s, "tre", "certificate", "100", "2021-01-07", "Checking", "amy")  # bought before the rise: still good
    treasurer.record(club, s, "tre", "dues", "19", "2026-12-03", "Cash", "amy")
    treasurer.record(club, s, "tre", "dues", "50", "2026-12-03", "Cash", "amy")  # a few months ahead
    amy = treasurer.standing(club, s, "amy", today, {})
    assert amy["certificate"] and amy["owed"] == -5000 and amy["paid_through"] == "2027-02-25"  # 12 nights of $4: Dec 10 … Feb 25
    bob = treasurer.standing(club, s, "bob", today, {})
    assert bob["certificate_due"] == "2028-11-12"  # joined after the rise: owes $150, nothing bought
    assert treasurer.at(club.info["treasurer"]["dues_weekly"], "1999-01-01") == 350  # before the first step: the first


def test_nothing_is_owed_until_tracking_starts(club):
    club.info["treasurer"] = {}
    assert treasurer.standing(club, treasurer.load(), "amy", dt.date(2026, 12, 3), {}) is None


def test_record_checks_its_input(club):
    s = treasurer.load()
    for kw in ({"kind": "bribe"}, {"amount": "0"}, {"member": "nobody"}, {"account": "Mattress"}, {"date": "soon"}):
        with pytest.raises(ValueError):
            treasurer.record(club, s, "tre", **{"kind": "dues", "amount": "5", "date": "2026-12-03", "account": "Cash",
                                               "member": "amy", **kw})
    assert treasurer.record(club, s, "tre", "adjustment", "-3.50", "2026-12-03", "Cash", "amy")["cents"] == -350
    assert treasurer.record(club, s, "tre", "expense", "81", "2026-12-03", "Checking", "amy")["member"] is None


def test_bills_from_the_minutes(club, tmp_path):
    for d, items in ((dt.date(2026, 10, 29), [Item("Water bill $40")]),  # before tracking
                     (dt.date(2026, 11, 12), [Item("Gas Bill $81"), Item("Letter from the city"), Item("Insurance $1,200.50")])):
        Meeting(date=d, status="published", sections={"Bills and Communications": items}).save(tmp_path / "m")
    archive, s = Archive(tmp_path / "m", club), treasurer.load()
    bills = treasurer.bills(archive, s, "2026-11-05")
    assert [(b["text"], b["cents"], b["status"]) for b in bills] == [("Gas Bill $81", 8100, "waiting"), ("Insurance $1,200.50", 120050, "waiting")]
    e = treasurer.record(club, s, "tre", "expense", "81", "2026-11-13", "Checking", bill=bills[0]["key"])
    treasurer.dismiss_bill(s, bills[1]["key"], "tre")
    assert [b["status"] for b in treasurer.bills(archive, s, "2026-11-05")] == ["paid", "dismissed"]
    treasurer.delete(s, e["id"], "tre")  # a mistake: the bill waits again
    assert treasurer.bills(archive, s, "2026-11-05")[0]["status"] == "waiting"


def test_officers_look_the_treasurer_records_members_see_their_own(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    c = Club(FIX / "test-club.yaml", roster=PEOPLE + [
        {"id": "tre", "first": "Tess", "last": "Teller", "role": "Treasurer", "officer": True, "member_since": "2019-01-03"},
        {"id": "pres", "first": "Pat", "last": "Prez", "role": "President", "officer": True}])
    c.info.update(club.info)
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", c)

    def as_(who):
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    tre, pres, amy = as_("tre"), as_("pres"), as_("amy")
    assert amy.get("/treasurer").status_code == 403
    form = {"kind": "dues", "amount": "7", "date": "2026-11-12", "account": "Cash", "member": "Amy Able"}
    assert pres.post("/treasurer/record", data=form).status_code == 403
    assert tre.post("/treasurer/record", data=form, follow_redirects=False).status_code == 303
    assert treasurer.load()["entries"][0]["member"] == "amy"
    assert "Record</button>" not in pres.get("/treasurer?tab=money").text and "Amy Able" in pres.get("/treasurer?tab=money").text
    assert "Record</button>" in tre.get("/treasurer?tab=money").text
    assert "Your dues" in amy.get("/").text
    c.info["treasurer"] = {}
    c.admins = {"pres"}
    assert "ask Pat Prez" in pres.get("/treasurer").text and "Your dues" not in amy.get("/").text
