"""Treasurer: what members owe (dues by the week, fees, missed chow), payments, the bond, bills from the minutes,
settings the Treasurer changes on the page, preview, funds and the arrears alert."""
import datetime as dt
from pathlib import Path

import pytest

from club_minutes import treasurer
from club_minutes.club import Club
from club_minutes.meeting import Item, Meeting
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [
    {"id": "amy", "first": "Amy", "last": "Appleseed", "member_since": "2020-03-05", "email": "amy@example.org"},
    {"id": "bob", "first": "Bob", "last": "Bloggs", "member_since": "2026-11-12"},  # joined after tracking began
    {"id": "old", "first": "Old", "last": "Timer", "status": "Silent Key"},
    {"id": "tre", "first": "Tess", "last": "Taxpayer", "role": "Treasurer", "officer": True, "board": True,
     "member_since": "2019-01-03", "email": "tre@example.org"},
    {"id": "pres", "first": "Pat", "last": "Prez", "role": "President", "officer": True, "board": True, "email": "pres@example.org"},
]


@pytest.fixture
def club(tmp_path, monkeypatch):
    monkeypatch.setattr(treasurer, "DATA", tmp_path)
    monkeypatch.setattr(treasurer, "today", lambda: dt.date(2027, 6, 1))  # the tests record money up to then
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
    assert amy["behind"] and amy["periods_behind"] == 15 and amy["period"] == "week"
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
    treasurer.add_rate(s, "tre", "dues", "2026-11-19", "4")
    treasurer.add_rate(s, "tre", "certificate", "2026-11-10", "150")
    amy = treasurer.standing(club, s, "amy", today, {})
    assert amy["owed"] == 2 * 350 + 3 * 400  # Nov 5 and 12 at the old rate, Nov 19 on at the new
    treasurer.record(club, s, "tre", "certificate", "100", "2021-01-07", "Checking", "amy")  # bought before the rise: still good
    treasurer.record(club, s, "tre", "dues", "19", "2026-12-03", "Cash", "amy")
    treasurer.record(club, s, "tre", "dues", "50", "2026-12-03", "Cash", "amy")  # a few months ahead
    amy = treasurer.standing(club, s, "amy", today, {})
    assert amy["certificate"] and amy["owed"] == -5000 and amy["paid_through"] == "2027-03-03"  # 12 nights of $4 (Dec 10 … Feb 25): the day before Mar 4
    assert treasurer.standing(club, s, "bob", today, {})["certificate_due"] == "2028-11-12"  # joined after the rise: owes $150
    c = treasurer.config(s)
    assert treasurer.rate(c, "dues", "1999-01-01") == 350  # before any change: the first amount
    treasurer.remove_rate(s, "tre", "dues", "2026-11-19")
    with pytest.raises(ValueError):
        treasurer.remove_rate(s, "tre", "dues", "2000-01-01")  # the last one stays


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
    to, _subject, body = treasurer.arrears_alert(club, s, today)
    assert to == ["pres@example.org", "tre@example.org"] and "Amy Appleseed" in body and "behind on dues:" in body
    assert "bylaws" not in body and "The app never suspends" in body  # no club rules unless club.yaml gives them
    assert treasurer.arrears_alert(club, s, today) is None  # once
    s["alerted"] = {}
    club.bylaws = {"refs": {"arrears": "IX.Z"}, "arrears_note": "Only the Board suspends,\n after a hearing."}
    body = treasurer.arrears_alert(club, s, today)[2]
    assert "behind on dues (bylaws IX.Z):" in body and "Only the Board suspends, after a hearing.\n" in body
    treasurer.record(club, s, "tre", "dues", "100", "2026-12-03", "Cash", "amy")
    treasurer.arrears_alert(club, s, today)
    assert "amy" not in s["alerted"]  # caught up: a later slip alerts again


def test_pages_officers_look_the_treasurer_records_members_see_their_own(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
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
    assert "Tracking (dues, bills and spending) is off" in pres.get("/treasurer").text and "Your dues" not in amy.get("/").text
    on = {"mode": "preview", "start": "2026-11-05", "arrears_periods": "4", "certificate_years": "2"}
    assert pres.post("/treasurer/settings", data=on).status_code == 403
    assert tre.post("/treasurer/settings", data=on, follow_redirects=False).status_code == 303
    assert "Preview:" in pres.get("/treasurer").text and "Your dues" not in amy.get("/").text  # members don't see a preview
    form = {"kind": "dues", "amount": "7", "date": "2026-11-12", "account": "Cash", "member": "Amy Appleseed"}
    assert pres.post("/treasurer/record", data=form).status_code == 403
    assert tre.post("/treasurer/record", data=form, follow_redirects=False).status_code == 303
    assert treasurer.load()["entries"][0]["member"] == "amy"
    assert "Record</button>" not in pres.get("/treasurer?tab=money").text and "Amy Appleseed" in pres.get("/treasurer?tab=money").text
    assert tre.post("/treasurer/settings/rate", data={"kind": "dues", "start": "2027-01-07", "amount": "4", "period": "week"}).status_code == 200
    assert tre.post("/treasurer/settings/account", data={"name": "Emergency Fund"}).status_code == 200
    assert "Emergency Fund" in tre.get("/treasurer?tab=settings").text and "$4.00" in pres.get("/treasurer?tab=settings").text
    assert "bylaws (" not in (pg := tre.get("/treasurer?tab=settings").text) and "(bylaws" not in pg  # no sections unless given
    club.bylaws = {"refs": {"dues": "VII", "bills": "<b>IV.C</b>"}}
    pg = tre.get("/treasurer?tab=settings").text + tre.get("/treasurer?tab=bills").text
    assert "The club's amounts (bylaws VII)." in pg and "every month or year (bylaws &lt;b&gt;IV.C&lt;/b&gt;)" in pg  # escaped
    tre.post("/treasurer/settings", data={**on, "mode": "on"})
    assert "Your dues" in amy.get("/").text
    yearly = {"kind": "dues", "start": "2030-01-01", "amount": "150", "period": "year", "due_month": "7", "due_day": "1"}
    assert tre.post("/treasurer/settings/rate", data=yearly).status_code == 200
    assert "$150.00 a year, due July 1" in tre.get("/treasurer?tab=settings").text
    assert "Behind after (weeks)" in tre.get("/treasurer?tab=settings").text  # the period in effect today
    assert "Pick the month and day" in tre.post("/treasurer/settings/rate", data={**yearly, "due_day": "0"}).text


def test_a_funds_keepers_record_only_its_account(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tre = TestClient(web.app)
    tre.cookies.set(web.COOKIE, a.new_session("tre"))
    tre.post("/treasurer/settings/account", data={"name": "Drinks Checking"})
    tre.post("/treasurer/settings/fund", data={"name": "Drinks fund", "account": "Drinks Checking", "keepers": "Bob Bloggs"})
    s = treasurer.load()
    assert treasurer.kept_accounts(club, s, "bob") == ["Drinks Checking"]
    bob = TestClient(web.app)
    bob.cookies.set(web.COOKIE, a.new_session("bob"))
    sale = {"kind": "income", "amount": "42", "date": "2026-11-12", "account": "Drinks Checking", "note": "can sales"}
    assert bob.post("/treasurer/record", data=sale, follow_redirects=False).status_code == 303
    assert bob.post("/treasurer/record", data={**sale, "account": "Checking"}).status_code == 403
    assert bob.post("/treasurer/record", data={**sale, "kind": "dues", "member": "Amy Appleseed"}).status_code == 403
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
    from club_minutes import console
    treasurer.save(s)
    Meeting(date=dt.date(2026, 11, 12), status="draft").save(tmp_path / "m")
    assert console.paid_since(Archive(tmp_path / "m", club), dt.date(2026, 11, 12)) == [
        "Paid $99.00: Gas bill", "Paid $10.00: for the fox hunt"]  # since the Nov 5 meeting


def test_spending_page_members_once_on_officers_in_preview(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
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
    assert "$81.00" in amy.get("/spending").text and "Amy Appleseed" not in amy.get("/spending").text


def test_cost_per_member_follows_people_joining_and_leaving(club):
    club.members["amy"].events = [{"date": "2020-03-05", "event": "Joined"}]
    club.members["bob"].events = [{"date": "2026-11-12", "event": "Joined"}]  # joins in November
    club.members["old"].events = [{"date": "2010-01-07", "event": "Joined"}, {"date": "2026-10-15", "event": "Silent Key"}]
    # tre and pres: no log rows, active now, so members all along
    assert treasurer.members_in(club, "2026-10-01", "2026-10-31") == 4  # amy, old (until Oct 15), tre, pres
    assert treasurer.members_in(club, "2026-11-01", "2026-11-30") == 4  # amy, bob, tre, pres
    assert treasurer.members_in(club, "2026-09-01", "2026-09-30") == 4  # amy, old, tre, pres
    s = started()
    treasurer.record(club, s, "tre", "expense", "100", "2026-11-06", "Checking", note="Gas bill")
    sp = treasurer.spending(s, dt.date(2026, 11, 20), club=club)
    nov = sp["months"][-1]
    assert nov["members"] == 4 and nov["per_member"] == 2500
    assert sp["per_member_month"] == round(10000 / 4 / 12) and sp["dues_month"] == 350 * 52 // 12  # $15.16 a month


def test_a_bond_bought_years_ago_is_on_file_and_moves_no_money(club):
    s, today = started(), dt.date(2026, 12, 3)
    e = treasurer.record(club, s, "tre", "certificate", "50", "1998-06-04", "", "amy", note="from the old ledger")
    assert e["on_file"] and e["account"] is None
    amy = treasurer.standing(club, s, "amy", today, {})
    assert amy["certificate"] and amy["certificate_bought"] == "1998-06-04" and amy["owed"] == 5 * 350  # dues untouched
    with pytest.raises(ValueError):
        treasurer.record(club, s, "tre", "dues", "100", "2019-01-03", "Cash", "amy")  # nothing was owed back then
    with pytest.raises(ValueError):
        treasurer.record(club, s, "tre", "certificate", "100", "2026-11-12", "", "bob")  # since tracking: an account, please
    treasurer.record(club, s, "tre", "expense", "81", "2026-11-12", "Checking", note="Gas bill")
    assert treasurer.spending(s, today)["total"] == 8100  # the old bond isn't money in or out


def test_who_owes_what_lists_who_owes_unless_asked_for_everyone(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(treasurer.chow, "load", lambda: {"nights": {}})
    tre = TestClient(web.app)
    tre.cookies.set(web.COOKIE, a.new_session("tre"))
    with treasurer.locked():
        s = started("preview", dt.date.today().isoformat())
        treasurer.record(club, s, "tre", "dues", "100", dt.date.today().isoformat(), "Cash", "pres")  # paid ahead
        treasurer.save(s)
    owe, everyone = tre.get("/treasurer").text, tre.get("/treasurer?everyone=1").text
    table = lambda html: html.split("<tbody>")[1].split("</tbody>")[0]
    assert "Pat Prez" not in table(owe) and "Pat Prez" in table(everyone)
    assert "Each member buys one." in owe and "Thu" not in owe.split("Record a bond bought before")[1][:20]


def test_without_a_certificate_in_club_yaml_nobody_owes_one(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(treasurer.chow, "DATA", tmp_path)  # an empty chow schedule (Home reads it too)
    with treasurer.locked():
        s = started("on", "2026-01-01")
        treasurer.save(s)
    tre, amy = TestClient(web.app), TestClient(web.app)
    tre.cookies.set(web.COOKIE, a.new_session("tre"))
    amy.cookies.set(web.COOKIE, a.new_session("amy"))
    pages = lambda: "".join(tre.get(f"/treasurer?tab={t}&everyone=1").text for t in ("standing", "money", "settings")) + amy.get("/").text
    looks = lambda html: {"name": "Certificate of Indebtedness" in html, "record": "Record a bond" in html,
                          "column": "<th>Bond</th>" in html, "years": "Years to buy the bond" in html, "note": "Each member buys one." in html,
                          "kind": 'value="certificate"' in html}
    assert all((on := looks(pages())).values()), on  # each check sees it while the club has one
    club.certificate = None
    assert not any((off := looks(pages())).values()), off
    assert treasurer.standing(club, s, "amy", dt.date(2026, 3, 1))["certificate"]  # nothing to buy: never late


def test_the_treasurer_manages_categories_and_colours_stay_put(club):
    s = started()
    treasurer.add_category(s, "tre", "Repeater")
    e = treasurer.record(club, s, "tre", "expense", "40", "2026-11-12", "Checking", note="duplexer", category="repeater")
    treasurer.rename_category(s, "tre", "repeater", "Repeater site")
    assert list(treasurer.categories(s))[-2:] == ["repeater", "other"] and treasurer.categories(s)["repeater"] == "Repeater site"
    before = treasurer.colours(s)
    assert treasurer.remove_category(s, "tre", "insurance") == 0
    after = treasurer.colours(s)
    assert after["repeater"] == before["repeater"] == 7 and after["food"] == before["food"]  # no repainting
    assert treasurer.guess("Paid insurance", s) == "other"  # its guess falls back once it's gone
    assert treasurer.remove_category(s, "tre", "repeater") == 1 and e["category"] == "other"
    with pytest.raises(ValueError):
        treasurer.remove_category(s, "tre", "other")
    treasurer.add_category(s, "tre", "Antennas")
    assert treasurer.colours(s)["antennas"] == 2  # the lowest colour free
    treasurer.add_category(s, "tre", "Field Day")
    treasurer.add_category(s, "tre", "Awards")
    with pytest.raises(ValueError):
        treasurer.add_category(s, "tre", "Ninth")


def test_bills_from_older_minutes_only_when_asked(club, tmp_path):
    for d, items in ((dt.date(2026, 10, 29), [Item("Water bill $40")]), (dt.date(2026, 11, 5), [Item("Pizza bill for fox hunt $10, Gas Bill $81")])):
        Meeting(date=d, status="published", sections={"Bills and Communications": items}).save(tmp_path / "m")
    archive, s = Archive(tmp_path / "m", club), started()
    bills = treasurer.bills(archive, s, treasurer.bills_from(s))
    assert [(b["label"], b["cents"], b["split"]) for b in bills] == [("Pizza bill for fox hunt", 1000, True), ("Gas Bill", 8100, True)]
    treasurer.set_bills_from(s, "tre", "2026-10-01")
    assert len(treasurer.bills(archive, s, treasurer.bills_from(s))) == 3
    treasurer.set_bills_from(s, "tre", None)  # undo
    assert len(treasurer.bills(archive, s, treasurer.bills_from(s))) == 2


def test_bill_tabs_names_and_the_read_only_dialog(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    tre = TestClient(web.app)
    tre.cookies.set(web.COOKIE, a.new_session("tre"))
    with treasurer.locked():
        s = started("preview")
        e = treasurer.record(club, s, "tre", "expense", "81", "2026-11-12", "Checking", note="Gas Bill $81 and more")
        treasurer.save(s)
    tre.post(f"/treasurer/entries/{e['id']}/note", data={"note": "Gas, November"})
    assert treasurer.load()["entries"][0]["note"] == "Gas, November"
    page = tre.get("/treasurer?tab=bills&show=dismissed").text
    assert 'class="on">Not a bill (0)' in page and "None marked not a bill." in page
    assert 'id="readonly"' not in page  # the dialog only comes with View as


def test_amounts_and_dates_that_are_typos_or_tricks(club):
    for bad in ("1e5", "0x10", "١٢", "12.", ".5", "+5", "1,2,3", "12.345", "$", "", "10000000.01"):
        with pytest.raises(ValueError):
            treasurer.cents(bad)
    assert treasurer.cents(" $1,000,000 ") == 100_000_000 and treasurer.cents("-0") == 0
    assert treasurer.day("20261109") == "2026-11-09"  # stored the one way, whatever ISO form came in
    s = started()
    e = treasurer.record(club, s, "tre", "dues", "7", "2026-W46-4", "Cash", "amy")
    assert e["date"] == "2026-11-12"
    with pytest.raises(ValueError):
        treasurer.record(club, s, "tre", "dues", "7", "2027-06-03", "Cash", "amy")  # after "today" (+1 day)
    for key in ("../../etc", "2026-11-05:zzzz:0", "x" * 500):
        with pytest.raises(ValueError):
            treasurer.dismiss_bill(s, key, "tre")
        with pytest.raises(ValueError):
            treasurer.record(club, s, "tre", "expense", "5", "2026-11-12", "Cash", bill=key)
    with pytest.raises(ValueError):
        treasurer.set_bills_from(s, "tre", "2026-12-01")  # after the start: nothing to look back for


def test_nobody_but_the_treasurer_reaches_the_treasurer_actions(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    from club_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    with treasurer.locked():
        s = started("on")
        treasurer.add_account(s, "tre", "Drinks")
        treasurer.set_fund(s, "tre", "Drinks fund", "Drinks", ["bob"])
        e = treasurer.record(club, s, "tre", "dues", "7", "2026-11-12", "Cash", "amy")
        treasurer.save(s)
    before = treasurer.load()
    every = {"kind": "dues", "amount": "5", "date": "2026-11-12", "account": "Cash", "member": "Amy Appleseed", "key": "2026-11-05:abcdef12:0",
             "payee": "Gas", "every": "month", "mode": "on", "start": "2026-11-05", "name": "X", "label": "X", "note": "x",
             "category": "other", "keepers": "Bob Bloggs"}
    posts = [r.path.replace("{eid}", e["id"]).replace("{rid}", "x") for r in web.app.routes
             if r.path.startswith("/treasurer") and "POST" in getattr(r, "methods", ())]
    assert len(posts) >= 11
    for who in ("amy", "bob"):  # an ordinary member; the drinks fund's keeper
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, a.new_session(who))
        for path in posts:
            assert t.post(path, data=every, follow_redirects=False).status_code == 403, (who, path)
    assert treasurer.load() == before  # nothing changed
    signed_out = TestClient(web.app).post("/treasurer/record", data=every, follow_redirects=False)
    assert signed_out.status_code in (303, 401, 403) and treasurer.load() == before


def test_text_from_the_minutes_and_members_is_never_run_as_html(club, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import business, web
    from club_minutes.auth import Auth
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web.archive, "club", club)
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)> $45'
    Meeting(date=dt.date(2026, 11, 12), status="published", sections={"Bills and Communications": [Item(evil)]}).save(tmp_path / "m")
    monkeypatch.setattr(web, "archive", Archive(tmp_path / "m", club))
    with treasurer.locked():
        s = started("on")
        treasurer.add_category(s, "tre", '"><script>alert(3)</script>')
        treasurer.record(club, s, "tre", "expense", "5", "2026-11-12", "Cash", note=evil)
        treasurer.save(s)
    bs = business.load()
    business.add(bs, evil, "tre")
    business.save(bs)
    tre = TestClient(web.app)
    tre.cookies.set(web.COOKIE, a.new_session("tre"))
    for path in ("/treasurer?tab=bills&show=all", "/treasurer?tab=money", "/treasurer?tab=settings", "/spending", "/business"):
        html = tre.get(path).text
        assert "<script>alert" not in html and "<img src=x" not in html, path
        if path != "/spending":  # it's on the page, as text
            assert "&lt;script&gt;alert" in html or "&lt;img src=x" in html, path


def dues(s, *steps):
    """Set the dues steps: (from, dollars, period, due)."""
    for start, amt, period, due in steps:
        treasurer.add_rate(s, "tre", "dues", start, amt, period, due)
    return s


def owed(club, s, who, day):
    return treasurer.standing(club, s, who, dt.date.fromisoformat(day), {})["owed"]


def test_monthly_dues_fall_on_the_1st_from_the_first_full_month_after_joining(club):
    s = dues(started(), ("2000-01-01", "30", "month", ""))  # tracking starts Nov 5
    assert owed(club, s, "amy", "2026-11-30") == 0 and owed(club, s, "amy", "2027-01-20") == 2 * 3000  # Dec 1 and Jan 1
    assert owed(club, s, "bob", "2027-01-20") == 2 * 3000 + 2500  # joined Nov 12: starts with Dec 1
    club.members["bob"].member_since = "2026-12-01"  # joining on a due date counts that month
    assert owed(club, s, "bob", "2026-12-01") == 3000 + 2500


def test_yearly_dues_fall_on_the_chosen_day_and_refuse_impossible_ones(club):
    s = dues(started(), ("2000-01-01", "150", "year", "7-1"))
    assert owed(club, s, "amy", "2027-06-30") == 0 and owed(club, s, "amy", "2027-07-01") == 15000
    assert treasurer.dues_text(treasurer.step(treasurer.config(s), "dues", "2027-01-01")) == "$150.00 a year, due July 1"
    for bad in ("02-29", "13-01", "4-31", "", "x"):
        with pytest.raises(ValueError):
            treasurer.add_rate(s, "tre", "dues", "2028-01-01", "150", "year", bad)
    with pytest.raises(ValueError):
        treasurer.add_rate(s, "tre", "dues", "2028-01-01", "150", "decade")


def test_a_change_from_weekly_to_yearly_charges_weekly_before_and_yearly_after(club):
    s = dues(started(), ("2000-01-01", "3.50", "week", ""), ("2027-01-01", "150", "year", "03-01"))
    assert owed(club, s, "amy", "2026-12-31") == 9 * 350  # Thursdays Nov 5 … Dec 31
    assert owed(club, s, "amy", "2027-02-28") == 9 * 350  # no weekly charges once the yearly step is in
    assert owed(club, s, "amy", "2027-03-01") == 9 * 350 + 15000


def test_behind_counts_the_clubs_current_period_and_home_rounds_to_the_nearest(club):
    s = dues(started(), ("2000-01-01", "30", "month", ""))
    s["settings"]["arrears_periods"] = 2
    st = lambda day: treasurer.standing(club, s, "amy", dt.date.fromisoformat(day), {})
    assert not st("2026-12-15")["behind"] and st("2026-12-15")["periods_behind"] == 1
    x = st("2027-01-02")
    assert x["behind"] and x["periods_behind"] == 2 and x["period"] == "month"
    s = dues(started(), ("2000-01-01", "10", "month", ""))
    treasurer.record(club, s, "tre", "adjustment", "21", "2026-12-02", "Cash", "amy")  # 10 + 21 = 3.1 months
    assert st("2026-12-15")["periods_behind"] == 3
    treasurer.record(club, s, "tre", "dues", "7", "2026-12-02", "Cash", "amy")  # 24 owed
    treasurer.record(club, s, "tre", "dues", "17", "2026-12-02", "Cash", "amy")  # 7 left: 0.7 of a month
    assert st("2026-12-15")["periods_behind"] == 1
    treasurer.record(club, s, "tre", "dues", "3", "2026-12-02", "Cash", "amy")  # 4 left: under half
    assert st("2026-12-15")["owed"] == 400 and st("2026-12-15")["periods_behind"] == 0


def test_paid_through_for_a_monthly_payer_ahead_is_the_day_before_the_first_unpaid_1st(club):
    s = dues(started(), ("2000-01-01", "30", "month", ""))
    treasurer.record(club, s, "tre", "dues", "100", "2026-12-15", "Cash", "amy")
    amy = treasurer.standing(club, s, "amy", dt.date(2026, 12, 15), {})
    assert amy["owed"] == -7000 and amy["paid_through"] == "2027-02-28"  # Jan 1 and Feb 1 covered, Mar 1 not
    treasurer.record(club, s, "tre", "dues", "1", "2026-12-15", "Cash", "bob")
    assert treasurer.standing(club, s, "bob", dt.date(2026, 12, 15), {})["paid_through"] is None  # owes the fee: nothing ahead


def test_spending_converts_dues_to_a_month_by_period(club):
    for step, month in ((("2000-01-01", "3.50", "week", ""), 350 * 52 // 12), (("2000-01-01", "30", "month", ""), 3000),
                        (("2000-01-01", "150", "year", "07-01"), 1250)):
        s = dues(started(), step)
        assert treasurer.spending(s, dt.date(2026, 11, 20), club=club)["dues_month"] == month


def test_the_upgrade_renames_weekly_dues_and_arrears_and_a_weekly_clubs_figures_stay(club, tmp_path):
    import json

    from club_minutes import upgrade
    old = {"settings": {"mode": "on", "start": "2026-11-05", "arrears_weeks": 3, "certificate_years": 2, "alerts": False,
                        "rates": {"dues_weekly": [{"from": "2000-01-01", "cents": 350}, {"from": "2026-11-19", "cents": 400}],
                                  "initiation": [{"from": "2000-01-01", "cents": 2500}]}},
           "entries": [{"id": "a1", "date": "2026-12-01", "kind": "dues", "member": "amy", "cents": 20000, "account": "Cash", "note": ""}]}
    p = tmp_path / "treasurer.json"
    p.write_text(json.dumps(old))
    figures = lambda: [treasurer.standing(club, treasurer.load(), m, dt.date(2026, 12, 20), {}) for m in ("amy", "bob", "tre")]
    before = figures()
    upgrade._v5(tmp_path)
    st = json.loads(p.read_text())["settings"]
    assert st["rates"]["dues"] == [{"from": "2000-01-01", "cents": 350, "period": "week"}, {"from": "2026-11-19", "cents": 400, "period": "week"}]
    assert "dues_weekly" not in st["rates"] and st["arrears_periods"] == 3 and "arrears_weeks" not in st
    assert st["rates"]["initiation"] == old["settings"]["rates"]["initiation"] and st["mode"] == "on"
    assert figures() == before and before[0]["paid_through"] and before[0]["owed"] < 0
    upgrade._v5(tmp_path)  # again: nothing changes
    assert figures() == before
    p.write_text(json.dumps({"entries": []}))  # no settings: left alone; no file: nothing made
    upgrade._v5(tmp_path)
    assert json.loads(p.read_text()) == {"entries": []}
    p.unlink()
    upgrade._v5(tmp_path)
    assert not p.exists()
