"""Treasurer: what members owe (dues by the week, fees, missed chow), payments, the bond, bills from the minutes,
settings the Treasurer changes on the page, preview, funds and the arrears alert."""
import datetime as dt
from pathlib import Path

import pytest

from w8lbz_minutes import treasurer
from w8lbz_minutes.club import Club
from w8lbz_minutes.meeting import Item, Meeting
from w8lbz_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [
    {"id": "amy", "first": "Amy", "last": "Able", "member_since": "2020-03-05", "email": "amy@example.org"},
    {"id": "bob", "first": "Bob", "last": "Baker", "member_since": "2026-11-12"},  # joined after tracking began
    {"id": "old", "first": "Old", "last": "Timer", "status": "Silent Key"},
    {"id": "tre", "first": "Tess", "last": "Teller", "role": "Treasurer", "officer": True, "board": True,
     "member_since": "2019-01-03", "email": "tre@example.org"},
    {"id": "pres", "first": "Pat", "last": "Prez", "role": "President", "officer": True, "board": True, "email": "pres@example.org"},
]


@pytest.fixture
def club(tmp_path, monkeypatch):
    monkeypatch.setattr(treasurer, "DATA", tmp_path)
    c = Club(FIX / "test-club.yaml", roster=PEOPLE)
    c.info.update(meeting_day="Thursday")
    return c


def started(mode="on", start="2026-11-05"):
    s = treasurer.load()
    treasurer.set_general(s, "tre", mode, start, 4, 2, False)
    return s


def test_cents_and_money():
    assert [treasurer.cents(v) for v in ("3.50", "$1,200", "81", "-5.5", 12.5)] == [350, 120000, 8100, -550, 1250]
    assert treasurer.money(-1250) == "−$12.50" and treasurer.money(120000) == "$1,200.00"
    with pytest.raises(ValueError):
        treasurer.cents("12.345")


def test_standing_dues_by_the_week_chow_fees_and_payments(club):
    s, today = started(), dt.date(2026, 12, 3)  # 5 Thursdays from Nov 5
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
    club.members["amy"].status = "Suspended"  # dues keep counting while suspended
    assert treasurer.standing(club, s, "amy", today, nights)["suspended"]


def test_off_until_the_treasurer_turns_it_on(club):
    s = treasurer.load()
    assert treasurer.standing(club, s, "amy", dt.date(2026, 12, 3), {}) is None
    with pytest.raises(ValueError):
        treasurer.set_general(s, "tre", "preview", "", 4, 2, False)  # needs a start date
    treasurer.set_general(s, "tre", "preview", "2026-11-05", 4, 2, False)
    assert treasurer.on(s) and not treasurer.members_see(s) and treasurer.standing(club, s, "amy", dt.date(2026, 12, 3), {})


def test_amounts_that_change_on_a_date_and_paying_ahead(club):
    s, today = started(), dt.date(2026, 12, 3)
    treasurer.add_rate(s, "tre", "dues_weekly", "2026-11-19", "4")
    treasurer.add_rate(s, "tre", "certificate", "2026-11-10", "150")
    amy = treasurer.standing(club, s, "amy", today, {})
    assert amy["owed"] == 2 * 350 + 3 * 400  # Nov 5 and 12 at the old rate, Nov 19 on at the new
    treasurer.record(club, s, "tre", "certificate", "100", "2021-01-07", "Checking", "amy")  # bought before the rise: still good
    treasurer.record(club, s, "tre", "dues", "19", "2026-12-03", "Cash", "amy")
    treasurer.record(club, s, "tre", "dues", "50", "2026-12-03", "Cash", "amy")  # a few months ahead
    amy = treasurer.standing(club, s, "amy", today, {})
    assert amy["certificate"] and amy["owed"] == -5000 and amy["paid_through"] == "2027-02-25"  # 12 nights of $4: Dec 10 … Feb 25
    assert treasurer.standing(club, s, "bob", today, {})["certificate_due"] == "2028-11-12"  # joined after the rise: owes $150
    c = treasurer.config(s)
    assert treasurer.rate(c, "dues_weekly", "1999-01-01") == 350  # before any change: the first amount
    treasurer.remove_rate(s, "tre", "dues_weekly", "2026-11-19")
    with pytest.raises(ValueError):
        treasurer.remove_rate(s, "tre", "dues_weekly", "2000-01-01")  # the last one stays


def test_accounts_the_treasurer_adds_and_removes(club):
    s = started()
    treasurer.add_account(s, "tre", "Emergency Fund")
    treasurer.record(club, s, "tre", "income", "20", "2026-11-12", "Emergency Fund", note="donation")
    with pytest.raises(ValueError):
        treasurer.remove_account(s, "tre", "Emergency Fund")  # money recorded in it
    treasurer.remove_account(s, "tre", "Cash")
    assert treasurer.config(s)["accounts"] == ["Checking", "Emergency Fund"]


def test_record_checks_its_input(club):
    s = started()
    for kw in ({"kind": "bribe"}, {"amount": "0"}, {"member": "nobody"}, {"account": "Mattress"}, {"date": "soon"}):
        with pytest.raises(ValueError):
            treasurer.record(club, s, "tre", **{"kind": "dues", "amount": "5", "date": "2026-12-03", "account": "Cash",
                                               "member": "amy", **kw})
    assert treasurer.record(club, s, "tre", "adjustment", "-3.50", "2026-12-03", "Cash", "amy")["cents"] == -350
    assert treasurer.record(club, s, "tre", "expense", "81", "2026-12-03", "Checking", "amy")["member"] is None


def test_bills_from_the_minutes_every_amount(club, tmp_path):
    for d, items in ((dt.date(2026, 10, 29), [Item("Water bill $40")]),  # before tracking
                     (dt.date(2026, 11, 5), [Item("Gas Bill $81")]),
                     (dt.date(2026, 11, 12), [Item("Gas Bill $81"), Item("Letter from the city"),
                                              Item("Electric 50.64 $1,200.50 insurance, pizza $10, ok")])):
        Meeting(date=d, status="published", sections={"Bills and Communications": items}).save(tmp_path / "m")
    archive, s = Archive(tmp_path / "m", club), started()
    bills = treasurer.bills(archive, s, "2026-11-05")
    assert [(b["meeting"], b["amount"], b["cents"], b["repeated"]) for b in bills] == [
        ("2026-11-12", "$81", 8100, True), ("2026-11-12", "50.64", 5064, False), ("2026-11-12", "$1,200.50", 120050, False), ("2026-11-12", "$10", 1000, False),
        ("2026-11-05", "$81", 8100, False)]
    e = treasurer.record(club, s, "tre", "expense", "81", "2026-11-13", "Checking", bill=bills[4]["key"])
    treasurer.dismiss_bill(s, bills[0]["key"], "tre")
    assert [b["status"] for b in treasurer.bills(archive, s, "2026-11-05")] == ["dismissed", "waiting", "waiting", "waiting", "paid"]
    treasurer.delete(s, e["id"], "tre")  # a mistake: the bill waits again
    assert treasurer.bills(archive, s, "2026-11-05")[4]["status"] == "waiting"


def test_regular_bills_this_month_or_year(club):
    s = started()
    gas = treasurer.add_regular(s, "tre", "Gas company", "80", "month")
    ins = treasurer.add_regular(s, "tre", "Insurance", "900", "year", 3)
    with pytest.raises(ValueError):
        treasurer.add_regular(s, "tre", "Taxes", "500", "year")  # a yearly bill needs its month
    assert [r["payee"] for r in treasurer.regular_due(s, dt.date(2026, 2, 10))] == ["Gas company"]  # insurance from March
    treasurer.record(club, s, "tre", "expense", "81.20", "2026-03-04", "Checking", regular=gas["id"])
    due = {r["payee"]: r for r in treasurer.regular_due(s, dt.date(2026, 3, 20))}
    assert due["Gas company"]["paid"][0]["cents"] == 8120 and not due["Insurance"]["paid"]
    assert not {r["payee"]: r for r in treasurer.regular_due(s, dt.date(2026, 4, 1))}["Gas company"]["paid"]  # a new month
    treasurer.remove_regular(s, ins["id"], "tre")
    assert [r["payee"] for r in treasurer.regular_due(s, dt.date(2026, 3, 20))] == ["Gas company"]


def test_arrears_alert_once_each_time_and_never_in_preview(club, monkeypatch):
    monkeypatch.setattr(treasurer.chow, "load", lambda: {"nights": {}})
    s = started("preview")
    s["settings"]["alerts"] = True
    today = dt.date(2026, 12, 3)
    assert treasurer.arrears_alert(club, s, today) is None  # preview: no emails
    s["settings"]["mode"] = "on"
    to, subject, body = treasurer.arrears_alert(club, s, today)
    assert to == ["pres@example.org", "tre@example.org"] and "Amy Able" in body and "certified mail" in body
    assert treasurer.arrears_alert(club, s, today) is None  # once
    treasurer.record(club, s, "tre", "dues", "100", "2026-12-03", "Cash", "amy")
    treasurer.arrears_alert(club, s, today)
    assert "amy" not in s["alerted"]  # caught up: a later slip alerts again


def test_pages_officers_look_the_treasurer_records_members_see_their_own(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    club.admins = {"pres"}

    def as_(who):
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    tre, pres, amy = as_("tre"), as_("pres"), as_("amy")
    assert amy.get("/treasurer").status_code == 403
    assert "Dues tracking is off" in pres.get("/treasurer").text and "Your dues" not in amy.get("/").text
    on = {"mode": "preview", "start": "2026-11-05", "arrears_weeks": "4", "certificate_years": "2"}
    assert pres.post("/treasurer/settings", data=on).status_code == 403
    assert tre.post("/treasurer/settings", data=on, follow_redirects=False).status_code == 303
    assert "Preview:" in pres.get("/treasurer").text and "Your dues" not in amy.get("/").text  # members don't see a preview
    form = {"kind": "dues", "amount": "7", "date": "2026-11-12", "account": "Cash", "member": "Amy Able"}
    assert pres.post("/treasurer/record", data=form).status_code == 403
    assert tre.post("/treasurer/record", data=form, follow_redirects=False).status_code == 303
    assert treasurer.load()["entries"][0]["member"] == "amy"
    assert "Record</button>" not in pres.get("/treasurer?tab=money").text and "Amy Able" in pres.get("/treasurer?tab=money").text
    assert tre.post("/treasurer/settings/rate", data={"kind": "dues_weekly", "start": "2027-01-07", "amount": "4"}).status_code == 200
    assert tre.post("/treasurer/settings/account", data={"name": "Emergency Fund"}).status_code == 200
    assert "Emergency Fund" in tre.get("/treasurer?tab=settings").text and "$4.00" in pres.get("/treasurer?tab=settings").text
    tre.post("/treasurer/settings", data={**on, "mode": "on"})
    assert "Your dues" in amy.get("/").text


def test_a_funds_keepers_record_only_its_account(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tre = TestClient(web.app)
    tre.cookies.set(web.COOKIE, a.new_session("tre"))
    tre.post("/treasurer/settings/account", data={"name": "Drinks Checking"})
    tre.post("/treasurer/settings/fund", data={"name": "Drinks fund", "account": "Drinks Checking", "keepers": "Bob Baker"})
    s = treasurer.load()
    assert treasurer.kept_accounts(club, s, "bob") == ["Drinks Checking"]
    bob = TestClient(web.app)
    bob.cookies.set(web.COOKIE, a.new_session("bob"))
    sale = {"kind": "income", "amount": "42", "date": "2026-11-12", "account": "Drinks Checking", "note": "can sales"}
    assert bob.post("/treasurer/record", data=sale, follow_redirects=False).status_code == 303
    assert bob.post("/treasurer/record", data={**sale, "account": "Checking"}).status_code == 403
    assert bob.post("/treasurer/record", data={**sale, "kind": "dues", "member": "Amy Able"}).status_code == 403
    assert bob.post("/treasurer/settings/account", data={"name": "Mine"}).status_code == 403
    with treasurer.locked():
        s = treasurer.load()
        treasurer.record(club, s, "tre", "dues", "7", "2026-11-12", "Cash", "amy")
        treasurer.save(s)
    page = bob.get("/treasurer?tab=standing").text  # keepers only get their fund's money
    assert "can sales" in page and "Dues" not in page.split("<tbody>")[1] and "Who owes what" not in page
    assert bob.post(f"/treasurer/entries/{s['entries'][1]['id']}/delete").status_code == 403
    assert bob.post(f"/treasurer/entries/{s['entries'][0]['id']}/delete", follow_redirects=False).status_code == 303


def test_categories_spending_by_month_and_the_console(club, tmp_path, monkeypatch):
    for d, items in ((dt.date(2026, 11, 5), [Item("$99 gas bill $35 Raffle tickets $10 for pizza"), Item("Paid insurance $2884")]),):
        Meeting(date=d, status="published", sections={"Bills and Communications": items}).save(tmp_path / "m")
    archive, s = Archive(tmp_path / "m", club), started()
    assert [(b["amount"], b["category"]) for b in treasurer.bills(archive, s, "2026-11-05")] == [
        ("$99", "utilities"), ("$35", "other"), ("$10", "food"), ("$2884", "insurance")]
    treasurer.record(club, s, "tre", "expense", "99", "2026-11-06", "Checking", note="Gas bill")
    e = treasurer.record(club, s, "tre", "expense", "10", "2026-11-06", "Cash", note="for the fox hunt", category="food")
    treasurer.record(club, s, "tre", "expense", "2884", "2026-02-05", "Checking", note="Paid insurance")
    treasurer.record(club, s, "tre", "dues", "7", "2026-11-06", "Cash", "amy")  # money in: not spending
    treasurer.set_category(s, e["id"], "other", "tre")
    sp = treasurer.spending(s, dt.date(2026, 11, 20))
    assert [r["month"] for r in sp["months"]][::11] == ["2025-12", "2026-11"]
    nov = sp["months"][-1]
    assert nov["by"]["utilities"] == 9900 and nov["by"]["other"] == 1000 and nov["total"] == 10900
    assert sp["totals"]["insurance"] == 288400 and sp["total"] == 299300 and sp["top"] == 288400
    from w8lbz_minutes import console
    treasurer.save(s)
    Meeting(date=dt.date(2026, 11, 12), status="draft").save(tmp_path / "m")
    assert console.paid_since(Archive(tmp_path / "m", club), dt.date(2026, 11, 12)) == [
        "Paid $99.00: Gas bill", "Paid $10.00: for the fox hunt"]  # since the Nov 5 meeting


def test_spending_page_members_once_on_officers_in_preview(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from w8lbz_minutes import web
    from w8lbz_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)

    def as_(who):
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    amy, pres = as_("amy"), as_("pres")
    assert pres.get("/spending").status_code == 404  # tracking off
    with treasurer.locked():
        s = started("preview", "2025-01-02")
        treasurer.record(club, s, "tre", "expense", "81", dt.date.today().isoformat(), "Checking", note="Gas bill")
        treasurer.save(s)
    assert amy.get("/spending").status_code == 404 and "/spending" not in amy.get("/").text
    page = pres.get("/spending").text
    assert "Preview:" in page and "$81.00" in page and 'href="/spending"' in page
    with treasurer.locked():
        s = treasurer.load()
        s["settings"]["mode"] = "on"
        treasurer.save(s)
    assert "$81.00" in amy.get("/spending").text and "Amy Able" not in amy.get("/spending").text
