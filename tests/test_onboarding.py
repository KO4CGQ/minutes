"""Guest to member: the candidate in the minutes, the vote, Add as a member, the fees on the PDF, the purge."""
import datetime as dt
import time

import pytest
import test_applications as ta

from club_minutes import applications, console, treasurer, web
from club_minutes.store import Archive

with_env = ta.env  # the applications tests' club, sign-ins and data folder
keep_pillow = ta._keep_pillow_strict
good, send_link = ta.good, ta.send_link


def test_the_three_rules_and_their_edges():
    p = applications.passed
    assert [p("unanimous", *v) for v in ((12, 0), (12, 1), (0, 0), (1, 0))] == [True, False, False, True]
    assert [p("two_thirds", *v) for v in ((8, 4), (7, 4), (0, 0), (0, 3), (1, 0))] == [True, False, False, False, True]
    assert [p("majority", *v) for v in ((3, 2), (2, 2), (0, 0), (1, 0))] == [True, False, False, True]


def candidate(with_env):
    club, as_, sent, tmp = with_env
    _, token = send_link(as_, sent)
    as_(None).post(f"/apply/{token}", data=good(club, token))
    aid = applications.find(token)["id"]
    as_("n0cccc").post(f"/applications/{aid}/sponsor", data={"answer": "yes"})
    with applications.edit() as items:
        items[0]["sponsor"]["at"] = int(dt.datetime(2026, 11, 10).timestamp())  # a candidate since the 10th
    return club, as_, aid, Archive(tmp / "minutes", club)


def meeting(a, day):
    console.start(a, dt.date.fromisoformat(day), "n0aaaa")
    return a.by_slug[day]


def test_listed_until_the_vote_which_waits_for_listed_days_and_a_failed_vote_turns_them_down(with_env):
    club, _, aid, a = candidate(with_env)
    club.save_site_settings({("membership", "listed_days"): 7})
    m = meeting(a, "2026-11-12")
    line = [i.text for i in m.items(applications.SECTION)]
    assert line == ["Pat Visitor (N0CALL) has applied for membership, sponsored by Bob Member."]  # names only
    with pytest.raises(ValueError, match="can't be recorded until November 19"):
        console.vote(a, "2026-11-12", aid, 3, 0)
    console.submit(a, "2026-11-12")
    assert applications.load()["items"][0]["state"] == "candidate"  # still listed
    m = meeting(a, "2026-11-19")
    assert m.items(applications.SECTION)
    with pytest.raises(ValueError, match="whole numbers"):
        console.vote(a, "2026-11-19", aid, None, 0)
    console.vote(a, "2026-11-19", aid, 3, 1)
    console.vote(a, "2026-11-19", aid, 5, 2)  # the second count replaces the first
    text = [i.text for i in a.by_slug["2026-11-19"].items(applications.SECTION)]
    assert text == ["Pat Visitor's application was voted on: not passed (5–2)."]
    assert console.context(a, a.by_slug["2026-11-19"])["applicants"]["people"][0]["ballot"]["yes"] == 5
    console.submit(a, "2026-11-19")
    item = applications.load()["items"][0]
    assert item["state"] == "turned_down" and item["ended"]


def test_a_passed_vote_then_add_as_a_member_marks_it_and_the_fees_come_from_the_treasurer(with_env, monkeypatch):
    club, as_, aid, a = candidate(with_env)
    meeting(a, "2026-11-12")
    console.vote(a, "2026-11-12", aid, 9, 0)
    console.submit(a, "2026-11-12")
    item = applications.load()["items"][0]
    assert item["state"] == "candidate" and item["club"]["voted"] == "2026-11-12"
    assert not [i for i in meeting(a, "2026-11-19").items(applications.SECTION)]  # voted on: no longer listed
    assert "Add as a member" in as_("n0bbbb").get("/guests").text and "Add as a member" not in as_("n0cccc").get("/guests").text
    page = as_("n0bbbb").get(f"/roster/add?application={aid}").text
    assert 'value="Pat"' in page and 'value="Visitor"' in page and "123 Main St" in page and "pat@example.org" in page and f'value="{aid}"' in page
    got = []
    monkeypatch.setattr(web.membership, "add_member", lambda club, form, by, **kw: got.append(form) or "mnew001")
    assert as_("n0bbbb").post("/roster/add", data={"first": "Pat", "last": "Visitor", "application": aid}, follow_redirects=False).status_code == 303
    item = applications.load()["items"][0]
    assert item["state"] == "member" and item["member"] == "mnew001" and item["club"]["initiated"] == dt.date.today().isoformat()
    assert "Add as a member" not in as_("n0bbbb").get("/guests").text  # once added, no longer offered
    as_("n0bbbb").post("/roster/add", data={"first": "Pat", "last": "Visitor", "application": aid})  # a second time: nothing to the application
    assert applications.load()["items"][0]["member"] == "mnew001" and "application" not in got[-1]
    monkeypatch.setattr(treasurer, "load", lambda: {"entries": [{"member": "mnew001", "kind": "initiation", "date": "2026-11-20"},
                                                                 {"member": "n0cccc", "kind": "certificate", "date": "2026-01-01"}]})
    assert applications.fees(item) == ("2026-11-20", "")
    now = time.time()
    assert applications.purge(club, now + 89 * 86400) == 0 and applications.purge(club, now + 91 * 86400) == 1
    kept = applications.load()["items"][0]
    assert set(kept) >= {"id", "guest", "state", "member", "club"} and not {"form", "answers", "signed", "sponsor", "email"} & set(kept)


def test_a_candidate_never_added_is_deleted_after_keep_days(with_env):
    club, _, _aid, _a = candidate(with_env)
    since = applications.load()["items"][0]["sponsor"]["at"]
    assert applications.purge(club, since + 89 * 86400) == 0 and applications.purge(club, since + 91 * 86400) == 1  # no vote passed
    club, _, _aid, _a = candidate(with_env)
    with applications.edit() as items:
        items[0]["club"]["voted"] = "2026-12-01"
    voted = dt.datetime(2026, 12, 1).timestamp()
    assert applications.purge(club, voted + 89 * 86400) == 0 and applications.purge(club, voted + 91 * 86400) == 1


def test_minutes_started_before_the_sponsor_confirmed_list_the_candidate_when_reopened(with_env):
    club, _, _, tmp = with_env
    a = Archive(tmp / "minutes", club)
    meeting(a, "2026-11-12")  # started first: nobody to list yet
    assert not a.by_slug["2026-11-12"].items(applications.SECTION)
    candidate(with_env)
    assert meeting(a, "2026-11-12").items(applications.SECTION)  # Start meeting again (Continue) picks them up
