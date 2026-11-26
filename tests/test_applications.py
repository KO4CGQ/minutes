"""Membership applications: the emailed link, the form, the sponsor, who may send, see and download, the purge, the settings."""
import io
import json
import re
import time
from pathlib import Path

import pypdf
import pytest
from fastapi.testclient import TestClient

from club_minutes import applications, web
from club_minutes import club as clubmod
from club_minutes.auth import Auth
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "n0aaaa", "first": "Jane", "last": "Doe", "role": "Secretary", "officer": True, "email": "jane@example.org", "status": "Active"},
          {"id": "n0bbbb", "first": "Tom", "last": "Tester", "role": "Treasurer", "officer": True, "email": "tom@example.org", "status": "Active"},
          {"id": "n0cccc", "first": "Bob", "last": "Member", "callsign": "N0CCCC", "email": "bob@example.org", "status": "Active"},
          {"id": "n0dddd", "first": "Sam", "last": "Gone", "status": "Inactive"}]
GUESTS = [{"name": "Pat Visitor", "callsign": None, "visits": [("2026-10-01", ""), ("2026-10-08", ""), ("2026-10-15", "")], "member": None},
          {"name": "New Face", "callsign": None, "visits": [("2026-10-15", "")], "member": None}]
QUESTIONS = [{"label": "Been to a meeting before?", "type": "yesno"},
             {"label": "Which modes?", "type": "several", "options": ["Voice", "Digital"]},
             {"label": "Why join?", "type": "short"},
             {"label": "Tell us more", "type": "long"},
             {"label": "Bands", "type": "grid", "rows": ["20 m", "2 m"], "cols": ["Fixed", "Portable"]},
             {"label": "Best time?", "type": "one", "options": ["Mornings", "Evenings"]}]


@pytest.fixture(autouse=True)
def _keep_pillow_strict(monkeypatch):
    """Making a PDF imports weasyprint, which turns on Pillow's reading of cut-off pictures for the rest of the run."""
    from PIL import ImageFile
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", ImageFile.LOAD_TRUNCATED_IMAGES)


@pytest.fixture
def env(tmp_path, monkeypatch):
    club = Club(FIX / "test-club.yaml", roster=PEOPLE)
    authn = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", authn)
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(web.archive, "guests", lambda: GUESTS)
    for mod in (applications, clubmod, web):
        monkeypatch.setattr(mod, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(applications.auth, "send_email", lambda to, subject, body, **kw: sent.append(
        {"to": to, "subject": subject, "body": body, "files": [a[0] for a in kw.get("attachments", ())]}))
    club.save_site_settings({("membership", "questions"): QUESTIONS})

    def as_(who):
        c = TestClient(web.app, client=("203.0.113.6", 5000))
        if who:
            c.cookies.set(web.COOKIE, authn.new_session(who))
        return c
    return club, as_, sent, tmp_path


def send_link(as_, sent, who="n0aaaa", email="pat@example.org"):
    r = as_(who).post("/guests/apply", data={"guest": "Pat Visitor", "email": email}, follow_redirects=False)
    found = re.search(r"/apply/([\w-]+)", sent[-1]["body"]) if sent else None
    return r, found and found.group(1)


def good(club, token, **over):
    data = {"name": "Pat Visitor", "callsign": "n0call", "first_licensed": "1998", "license_class": "General", "expires": "2030-01-31",
            "street": "123 Main St", "city": "Anytown", "state": "OH", "zip": "45000", "phone": "555-0100", "email": "pat@example.org",
            "occupation": "Nurse", "q0": "Yes", "q1": ["Digital"], "q2": "Friends", "q3": "Line one\nLine two", "q4": ["0.0", "1.1"], "q5": "Evenings",
            "accept": "1", "signature": "pat  VISITOR", "sponsor": " bob  MEMBER ", "qh": applications.qhash(club)}
    return data | over


def test_the_link_is_stored_hashed_expires_and_works_once(env):
    club, as_, sent, tmp = env
    r, token = send_link(as_, sent)
    assert r.status_code == 303 and "note=" in r.headers["location"] and sent[-1]["to"] == "pat@example.org"
    raw = (tmp / "applications.json").read_text()
    assert token not in raw and applications._hash(token) in raw and (tmp / "applications.json").stat().st_mode & 0o777 == 0o600
    anon = as_(None)
    assert 'name="signature"' in anon.get(f"/apply/{token}").text and anon.get("/apply/not-a-link").status_code == 404
    _, again = send_link(as_, sent)  # sending again replaces the old link
    assert again != token and anon.get(f"/apply/{token}").status_code == 404 and anon.get(f"/apply/{again}").status_code == 200
    assert len(json.loads(raw)["items"]) == 1
    late = applications.find(again)["expires"] + 1
    assert not applications.usable(applications.find(again), late) and applications.usable(applications.find(again))
    assert anon.post(f"/apply/{again}", data=good(club, again)).status_code == 200  # used...
    assert anon.post(f"/apply/{again}", data=good(club, again)).status_code == 404  # ...once
    done = anon.post(f"/apply/{token}/pdf")  # (GET only)
    mine = anon.get(f"/apply/{again}/pdf")  # the applicant's own copy, from the thank-you page
    assert mine.status_code == 200 and mine.content.startswith(b"%PDF") and mine.headers["cache-control"] == "no-store"
    assert anon.get(f"/apply/{token}/pdf").status_code == 404 and done.status_code == 405  # the replaced link has no form
    with applications.edit() as items:
        items[0]["signed"]["at"] -= applications.OWN_COPY_SECONDS + 1
    assert anon.get(f"/apply/{again}/pdf").status_code == 404  # an hour later: gone


def test_the_form_is_checked_and_each_kind_of_question_is_stored(env):
    club, as_, sent, _ = env
    _, token = send_link(as_, sent)
    anon = as_(None)
    for over, why in (({"signature": "Someone Else"}, "Type your full name"), ({"accept": ""}, "Tick that you accept"),
                      ({"email": "nope"}, "email address"), ({"zip": ""}, "ZIP code is needed"), ({"first_licensed": "1800"}, "a year"),
                      ({"expires": "soon"}, "isn&#39;t a date"), ({"license_class": "Wizard"}, "license class"), ({"q0": ""}, "Please answer"),
                      ({"q1": ["Smoke signals"]}, "isn&#39;t one of the choices"), ({"q4": ["9.9"]}, "isn&#39;t in the grid"),
                      ({"q5": ""}, "Please answer: Best time?"), ({"q5": "Noon"}, "Please answer: Best time?"),
                      ({"qh": "stale"}, "changed its questions"), ({"name": "x" * 101}, "too long"),
                      ({"name": "Jane https://evil.example", "signature": "Jane https://evil.example"}, "web address")):
        r = anon.post(f"/apply/{token}", data=good(club, token, **over))
        assert r.status_code == 400 and why in r.text, why
        assert 'value="123 Main St"' in r.text  # what was typed is kept
    assert applications.usable(applications.find(token))
    sent.clear()
    assert anon.post(f"/apply/{token}", data=good(club, token)).status_code == 200
    a = applications.find(token)
    assert a["state"] == "submitted" and a["form"]["callsign"] == "N0CALL" and a["signed"] == {"name": "pat VISITOR", "at": a["used"]}
    assert a["answers"] == [{"label": "Been to a meeting before?", "type": "yesno", "value": "Yes"},
                            {"label": "Which modes?", "type": "several", "value": ["Digital"]},
                            {"label": "Why join?", "type": "short", "value": "Friends"},
                            {"label": "Tell us more", "type": "long", "value": "Line one\nLine two"},
                            {"label": "Bands", "type": "grid", "value": {"cols": ["Fixed", "Portable"], "rows": {"20 m": ["Fixed"], "2 m": ["Portable"]}}},
                            {"label": "Best time?", "type": "one", "value": "Evenings"}]
    assert a["sponsor"] == {"typed": "bob MEMBER", "id": "n0cccc", "asked": a["used"], "answer": None, "at": None} and a["statement"] == club.membership["statement"]
    by = {m["to"] if isinstance(m["to"], str) else m["to"][0]: m for m in sent}  # the sponsor, the Secretary and the applicant
    assert set(by) == {"bob@example.org", "jane@example.org", "pat@example.org"}
    assert not any(m["files"] for m in sent) and "/guests" in by["jane@example.org"]["body"]  # no PDF by email: it stays in the app
    from club_minutes import pdf
    assert pdf.application(club, a).startswith(b"%PDF")


def test_fields_hidden_are_ignored_required_are_enforced_and_bad_settings_refused(env):
    club, _, _, _ = env
    club.membership["fields"] = {**clubmod.FIELD_DEFAULTS, "callsign": "required", "phone": "hidden", "street": "hidden"}
    from starlette.datastructures import FormData

    def form(**over):  # a list value is several fields of the same name, as a browser sends
        return FormData([(k, x) for k, v in good(club, "t", **over).items() for x in (v if isinstance(v, list) else [v])])
    with pytest.raises(ValueError, match="Callsign is needed"):
        applications.check(club, form(callsign=""))
    f, _, _ = applications.check(club, form())
    assert f["phone"] == "" and f["street"] == "" and f["callsign"] == "N0CALL" and f["city"] == "Anytown"
    for bad in ({"phone": "maybe"}, {"fax": "hidden"}):
        with pytest.raises(ValueError, match="fields maps"):
            clubmod._membership({"fields": bad})


def test_only_the_right_people_send_see_and_download(env):
    club, as_, sent, _ = env
    assert as_("n0cccc").post("/guests/apply", data={"guest": "Pat Visitor", "email": "p@example.org"}).status_code == 403
    assert "Send the application" not in as_("n0cccc").get("/guests").text
    assert "Send the application" in as_("n0bbbb").get("/guests").text  # the Treasurer keeps the roster
    r, token = send_link(as_, sent, "n0bbbb")
    assert r.status_code == 303 and "error" not in r.headers["location"]
    assert "Application sent" in as_("n0bbbb").get("/guests").text and "Application sent" not in as_("n0cccc").get("/guests").text
    assert "visited%20enough" in as_("n0aaaa").post("/guests/apply", data={"guest": "New Face", "email": "n@example.org"}, follow_redirects=False).headers["location"]
    as_(None).post(f"/apply/{token}", data=good(club, token))
    aid = applications.find(token)["id"]
    assert as_("n0aaaa").get(f"/applications/{aid}/pdf").content.startswith(b"%PDF")  # the Secretary receives them
    for who in ("n0bbbb", "n0cccc"):
        assert as_(who).get(f"/applications/{aid}/pdf").status_code == 404
    assert f"/applications/{aid}/pdf" in as_("n0aaaa").get("/guests").text and f"/applications/{aid}/pdf" not in as_("n0bbbb").get("/guests").text
    assert as_(None).get(f"/applications/{aid}/pdf", follow_redirects=False).status_code == 303


def test_the_sponsor_confirms_or_declines(env):
    club, as_, sent, _ = env
    _, token = send_link(as_, sent)
    as_(None).post(f"/apply/{token}", data=good(club, token))
    aid = applications.find(token)["id"]
    assert as_("n0aaaa").get(f"/applications/{aid}/sponsor").status_code == 404  # only the sponsor
    assert as_("n0aaaa").post(f"/applications/{aid}/sponsor", data={"answer": "yes"}).status_code == 404
    page = as_("n0cccc").get(f"/applications/{aid}/sponsor").text
    assert "I sponsor Pat Visitor" in page and "I can't sponsor them" in page
    sent.clear()
    as_("n0cccc").post(f"/applications/{aid}/sponsor", data={"answer": "no"})
    a = applications.find(token)
    assert a["state"] == "declined" and a["sponsor"]["answer"] == "no" and [m["to"] for m in sent] == [["jane@example.org"], "pat@example.org"] and not sent[0]["files"] and "couldn't confirm" in sent[1]["body"]
    _, new = send_link(as_, sent)  # a new link: pick another sponsor
    assert new != token and applications.find(new)["state"] == "sent" and applications.find(new)["id"] == aid
    as_(None).post(f"/apply/{new}", data=good(club, new))
    sent.clear()
    as_("n0cccc").post(f"/applications/{aid}/sponsor", data={"answer": "yes"})
    a = applications.find(new)
    assert a["state"] == "candidate" and a["sponsor"]["answer"] == "yes" and a["sponsor"]["at"] and not sent[0]["files"] and "/guests" in sent[0]["body"]  # a link, not the PDF again
    assert send_link(as_, sent)[0].headers["location"].startswith("/guests?error=")  # applied already


def test_turned_down_waits_and_the_purge_deletes_after_the_days(env):
    club, as_, sent, _ = env
    _, token = send_link(as_, sent)
    now = time.time()
    day = 86400
    assert applications.purge(club, now + 119 * day) == 0 and applications.purge(club, now + 125 * day) == 1  # 30 days of link + 90 kept
    assert applications.load()["items"] == []
    _, token = send_link(as_, sent)
    as_(None).post(f"/apply/{token}", data=good(club, token))
    aid = applications.find(token)["id"]
    applications.turn_down(aid, now)
    assert applications.turned_down_recently(club, "pat visitor", "other@example.org", now + day)  # by name
    assert applications.turned_down_recently(club, "Someone Else", "PAT@example.org", now + day)  # by email
    r, _ = send_link(as_, sent)
    assert "turned%20them%20down" in r.headers["location"]
    assert applications.purge(club, now + 89 * day) == 0
    assert applications.purge(club, now + 91 * day) == 1  # answers gone, name and email kept for the 24 months
    stub = applications.load()["items"][0]
    assert "form" not in stub and "answers" not in stub and stub["state"] == "turned_down" and stub["email"] == "pat@example.org"
    assert applications.turned_down_recently(club, "Pat Visitor", "x@example.org", now + 700 * day)
    assert not applications.turned_down_recently(club, "Pat Visitor", "x@example.org", now + 800 * day)
    assert applications.purge(club, now + 800 * day) == 1 and applications.load()["items"] == []
    send_link(as_, sent)  # allowed again; a withdrawn one is deleted whole
    applications.withdraw(applications.load()["items"][0]["id"], now + 800 * day)
    assert applications.purge(club, now + 891 * day) == 1 and applications.load()["items"] == []


def test_membership_settings_defaults_save_and_back_to_club_yaml(env, monkeypatch):
    club, as_, _, _ = env
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    club.save_app_admins({"n0bbbb"})
    admin = as_("n0bbbb")
    club.save_site_settings({("membership", "questions"): None})
    assert club.membership == clubmod.MEMBERSHIP_DEFAULTS and club.membership["link_days"] == 30 and club.info.get("invite_after_visits") is None
    page = admin.get("/admin/site/membership").text
    assert 'name="link_days" min="1" max="365" value="30"' in page and 'name="invite_after_visits" min="1" max="50" value="3"' in page
    assert "Changed here" not in page and "Back to club.yaml" not in page and "By signing, you confirm" in page
    assert as_("n0cccc").get("/admin/site/membership").status_code == 404
    form = {"rows": "2", "invite_after_visits": "3", "link_days": "30", "listed_days": "0", "vote": "1", "vote_passes": "unanimous",
            "reapply_months": "24", "keep_days": "90", "statement": club.membership["statement"], "invite": club.membership["invite"],
            "label0": "Bands", "type0": "grid", "qrows0": "20 m\n2 m", "qcols0": "Fixed\nMobile", "place0": "2",
            "label1": "Licensed?", "type1": "yesno", "place1": "1", "required1": "1"}  # yes or no starts ticked
    assert "Saved" in admin.post("/admin/site/membership", data=form).text and club.site_settings["membership"] == {
        "questions": [{"label": "Licensed?", "type": "yesno"}, {"label": "Bands", "type": "grid", "rows": ["20 m", "2 m"], "cols": ["Fixed", "Mobile"]}]}
    assert "invite_after_visits" not in club.site_settings.get("club", {})  # unchanged values aren't kept
    assert "Saved" in admin.post("/admin/site/membership", data=form | {"invite_after_visits": "5", "link_days": "7", "vote": "", "vote_passes": "two_thirds",
                                                                         "statement": "", "remove1": "1"}).text
    assert club.info["invite_after_visits"] == 5 and club.membership["link_days"] == 7 and not club.membership["vote"]
    assert club.membership["vote_passes"] == "two_thirds" and club.membership["statement"] == "" and [q["label"] for q in club.membership["questions"]] == ["Bands"]
    before = club.site_settings
    for bad, why in ((form | {"link_days": "0"}, "from 1 to 365"), (form | {"vote_passes": "coin"}, "pick one"),
                     (form | {"type0": "grid", "qcols0": ""}, "give cols"), (form | {"type1": "one"}, "give options"),
                     (form | {"invite": "Hello {guest}"}, "needs {link}"), (form | {"invite": "{link} {name}"}, "not {name}")):
        assert why in admin.post("/admin/site/membership", data=bad).text and club.site_settings == before, why
    preview = admin.post("/admin/site/membership/preview", data=form | {"label2": "Favorite band?", "type2": "one", "options2": "20 m\n40 m", "required2": "1", "rows": "3", "welcome": "**Hi** there"})
    assert 'type="radio" name="q2" value="20 m" required' in preview.text and 'type="radio" name="q2" value="40 m">' in preview.text  # Pick one: round buttons
    assert "Favorite band?" in preview.text and "<strong>Hi</strong>" in preview.text and "Preview:" in preview.text and " disabled>Send my application" in preview.text
    assert club.site_settings == before  # nothing saved
    assert "from 1 to 365" in admin.post("/admin/site/membership/preview", data=form | {"link_days": "0"}).text
    assert as_("n0cccc").post("/admin/site/membership/preview", data=form).status_code == 404
    pdf = admin.post("/admin/site/membership/pdf", data=form | {"label2": "Favorite band?", "type2": "short", "rows": "3", "field_phone": "hidden"})
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF") and club.site_settings == before
    text = "".join(pg.extract_text() for pg in pypdf.PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Jane Doe" in text and "Favorite band?" in text and "555-0100" not in text  # phone not asked
    assert as_("n0cccc").post("/admin/site/membership/pdf", data=form).status_code == 404
    saved = admin.get("/admin/site/membership/preview")  # a reload or bookmark: the saved settings, not an error
    assert saved.status_code == 200 and "Preview:" in saved.text and 'name="q0"' in saved.text
    got = admin.get("/admin/site/membership/pdf")
    assert got.headers["content-type"] == "application/pdf" and as_("n0cccc").get("/admin/site/membership/pdf").status_code == 404
    assert "Back to club.yaml" in admin.get("/admin/site/membership").text
    admin.post("/admin/site/membership", data={"back": "1"})
    assert club.site_settings == {} and club.membership == clubmod.MEMBERSHIP_DEFAULTS
    with pytest.raises(ValueError, match="link_days"):
        club._configure({"membership": {"link_days": 0}})  # club.yaml's too
    assert club.may("n0aaaa", "applications") and not club.may("n0bbbb", "applications")  # the Secretary's role has it


def test_a_club_with_its_own_secretary_role_still_gets_the_new_permission(tmp_path, monkeypatch):
    """roles.json saved before the permission existed: no role has it, so the built-in that ships with it gets it."""
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    (tmp_path / "roles.json").write_text(json.dumps({"table": {"secretary": {"name": "Secretary", "perms": ["roster_emails"]}}}))
    c = Club(FIX / "test-club.yaml", roster=PEOPLE)
    assert "applications" in c.role_table()["secretary"]["perms"]
    c.save_roles(c.role_table())  # and Roles saves again


def test_the_sponsor_is_typed_and_unmatched_or_empty_waits_for_someone_to_pick(env):
    club, as_, sent, _ = env
    assert applications.sponsor_match(club, "N0CCCC") == "n0cccc" and applications.sponsor_match(club, "sam gone") is None  # callsign; not Active
    for typed, why in (("Zed", "x"), ("", "empty")):
        _, token = send_link(as_, sent)
        sent.clear()
        assert as_(None).post(f"/apply/{token}", data=good(club, token, sponsor=typed)).status_code == 200
        a = applications.find(token)
        assert a["state"] == "submitted" and a["sponsor"] == {"typed": typed, "id": None, "asked": None, "answer": None, "at": None}
        assert "bob@example.org" not in [m["to"] for m in sent]  # no sponsor asked yet
        page = as_("n0aaaa").get("/guests").text
        assert ("Sponsor typed: Zed" if typed else "No sponsor named") in page and "Ask this sponsor" in page and "Ask this sponsor" not in as_("n0cccc").get("/guests").text
        assert as_("n0cccc").post(f"/applications/{a['id']}/ask", data={"member": "n0cccc"}).status_code == 403
        sent.clear()
        as_("n0aaaa").post(f"/applications/{a['id']}/ask", data={"member": "n0dddd"})  # not Active: refused
        assert applications.find(token)["sponsor"]["id"] is None
        as_("n0aaaa").post(f"/applications/{a['id']}/ask", data={"member": "n0cccc"})
        assert applications.find(token)["sponsor"]["id"] == "n0cccc" and [m["to"] for m in sent] == ["bob@example.org"]
        assert as_("n0bbbb").post(f"/applications/{a['id']}/withdraw").status_code == 403  # roster keepers can't
        as_("n0aaaa").post(f"/applications/{a['id']}/withdraw")
        assert applications.find(token)["state"] == "withdrawn"
        with applications.edit() as items:
            items.clear()  # (a new application in the same second would tie with the old one)


def test_text_is_cleaned_the_link_checked_first_and_big_bodies_refused(env):
    club, as_, sent, tmp = env
    _, token = send_link(as_, sent)
    anon = as_(None)
    assert anon.post("/apply/not-a-link", data={"x": "y" * 70000}).status_code == 404  # the token first
    assert anon.post(f"/apply/{token}", content=b"x" * 70000, headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 413
    r = anon.get(f"/apply/{token}")
    assert r.headers["cache-control"] == "no-store" and "Only what you're happy" in r.text
    assert anon.post(f"/apply/{token}", data=good(club, token, name="Jane\u202e Doe\x07", signature="Jane Doe", q2="a\u200bb")).headers["cache-control"] == "no-store"
    a = applications.find(token)
    assert a["form"]["name"] == "Jane Doe" and a["answers"][2]["value"] == "ab"
    assert (tmp / "applications.lock").stat().st_mode & 0o777 == 0o600


def test_a_name_never_becomes_markup_in_the_minutes():
    from club_minutes import richtext
    for name in ("Jane (JD) O'Neil_Smith", "[x](https://evil.example/)", "**bold**", "# Head", "@[Jane Doe](n0aaaa)", "- item", "1. item", "> quote", "`code`"):
        html = str(richtext.render(richtext.escape(name) + " has applied.")).replace("&#x27;", "'").replace("&#39;", "'")
        assert "<a " not in html and "<strong" not in html and "<h" not in html and "<li" not in html and "<blockquote" not in html and "<code" not in html, name
        assert name.lstrip(">").replace("`", "'").strip() in html.replace("&gt;", ">"), name


def test_a_cross_site_request_for_a_made_up_link_isnt_counted_toward_a_ban(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from club_minutes import web
    seen = []
    monkeypatch.setattr(web.authn, "event", lambda *a, **k: seen.append(a[0]))
    anon = TestClient(web.app)
    for path in ("/apply/not-a-link", "/login/link?t=x"):
        assert anon.get(path, headers={"Sec-Fetch-Site": "cross-site"}, follow_redirects=False).status_code in (303, 404)
    assert seen == []
    anon.get("/apply/not-a-link")
    assert seen == ["bad_link"]


def test_a_near_sponsor_is_suggested_and_the_one_likely_member_may_claim_it(env):
    club, as_, sent, _ = env
    assert applications.likely_sponsors(club, "B Member") == ["n0cccc"] and applications.likely_sponsors(club, "Robert M") == []  # no fit better than a letter
    assert applications.likely_sponsors(club, "Bob") == ["n0cccc"] and applications.likely_sponsors(club, "N0CCCD") == ["n0cccc"]  # a callsign one letter off
    assert applications.likely_sponsors(club, "T") == ["n0bbbb"] and applications.likely_sponsors(club, "Bob", skip="n0cccc") == []
    assert set(applications.likely_sponsors(club, "J")) == {"n0aaaa"}  # Sam Gone isn't Active
    _, token = send_link(as_, sent)
    sent.clear()
    as_(None).post(f"/apply/{token}", data=good(club, token, sponsor="Bobby M."))
    a = applications.find(token)
    assert a["sponsor"]["id"] is None and a["sponsor"]["likely"] == "n0cccc"
    ask = next(m for m in sent if m["to"] == "bob@example.org")
    assert "typed \"Bobby M.\"" in ask["body"] and f"/applications/{a['id']}/sponsor" in ask["body"]
    assert "may be Bob Member" in next(m for m in sent if m["to"] == ["jane@example.org"])["body"]
    assert "Likely" in as_("n0aaaa").get("/guests").text and "asked Bob Member" in as_("n0aaaa").get("/guests").text
    assert as_("n0bbbb").get(f"/applications/{a['id']}/sponsor").status_code == 404
    assert "which may be you" in as_("n0cccc").get(f"/applications/{a['id']}/sponsor").text
    sent.clear()
    as_("n0cccc").post(f"/applications/{a['id']}/sponsor", data={"answer": "no"})  # not them
    a = applications.find(token)
    assert a["state"] == "submitted" and a["sponsor"]["id"] is None and a["sponsor"]["not_me"] == "n0cccc" and "likely" not in a["sponsor"]
    assert [m["to"] for m in sent] == [["jane@example.org"]] and "Pick a member" in sent[0]["body"]  # the applicant isn't told
    assert "isn't you" in as_("n0cccc").get(f"/applications/{a['id']}/sponsor").text
    assert "Likely" not in as_("n0aaaa").get("/guests").text  # Bob said no: not offered first again
    with applications.edit() as items:  # the same, but this time it is them
        next(x for x in items if x["id"] == a["id"])["sponsor"] = {"typed": "Bobby M.", "id": None, "asked": None, "answer": None, "at": None, "likely": "n0cccc"}
    sent.clear()
    as_("n0cccc").post(f"/applications/{a['id']}/sponsor", data={"answer": "yes"})
    a = applications.find(token)
    assert a["state"] == "candidate" and a["sponsor"]["id"] == "n0cccc" and a["sponsor"]["answer"] == "yes" and "is sponsored" in sent[0]["subject"]


def test_two_likely_members_ask_no_one(env):
    club, as_, sent, _ = env
    _, token = send_link(as_, sent)
    sent.clear()
    import dataclasses
    club.members["n0eeee"] = dataclasses.replace(club.members["n0cccc"], id="n0eeee", first="Bill", callsign="N0EEEE", email="bill@example.org")
    assert applications.likely_sponsors(club, "B Member") == ["n0eeee", "n0cccc"]  # Bill and Bob Member: both fit
    as_(None).post(f"/apply/{token}", data=good(club, token, sponsor="B Member"))
    assert "likely" not in applications.find(token)["sponsor"] and not {"bob@example.org", "bill@example.org"} & {m["to"] for m in sent if isinstance(m["to"], str)}
    assert "Likely" in as_("n0aaaa").get("/guests").text  # both offered first


def test_the_invitation_email_uses_the_clubs_words(env):
    club, as_, sent, _ = env
    _, token = send_link(as_, sent)
    assert sent[-1]["body"].startswith("Hello Pat Visitor,") and "works for 30 days" in sent[-1]["body"]  # the default
    club.save_site_settings({("membership", "invite"): "Hi {guest}! Join {club} here: {link} ({days}). {link}"})
    _, token = send_link(as_, sent)
    body = sent[-1]["body"]
    assert body.startswith(f"Hi Pat Visitor! Join {applications.club_name(club)} here: ") and body.count(f"/apply/{token}") == 2
    assert "(30 days)" in body and club.sign_off in body


def test_more_words_in_the_email_and_the_bylaws_for_an_applicant(env):
    club, as_, sent, tmp = env
    club.save_site_settings({("membership", "invite"): "{first_name}|{short}|{visits}|{until}|{bylaws}|{next_meeting}|{link}"})
    _, token = send_link(as_, sent)
    first, short, visits, until, bylaws, nxt, link = sent[-1]["body"].split("\n")[0].split("|")
    assert (first, short, visits) == ("Pat", club.label, "3") and bylaws == link + "/bylaws" and until[-4:].isdigit()
    assert nxt == "" or " at " in nxt
    anon = as_(None)
    assert "hasn't put its bylaws here" in anon.get(f"/apply/{token}/bylaws").text
    assert "Read the club" not in anon.get(f"/apply/{token}").text
    (tmp / "bylaws.md").write_text("# Bylaws\n\nArticle one.")
    r = anon.get(f"/apply/{token}/bylaws")
    assert r.status_code == 200 and "Article one." in r.text and r.headers["cache-control"] == "no-store"
    assert f"/apply/{token}/bylaws" in anon.get(f"/apply/{token}").text
    assert anon.get("/apply/not-a-link/bylaws").status_code == 404
    club.save_site_settings({("membership", "welcome"): "<script>alert(1)</script> **hi**"})
    page = anon.get(f"/apply/{token}").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page and "<strong>hi</strong>" in page
    assert anon.post(f"/apply/{token}", data=good(club, token)).status_code == 200
    assert anon.get(f"/apply/{token}/bylaws").status_code == 404  # used


def test_must_be_answered_per_question(env):
    club, as_, sent, _ = env
    assert clubmod.clean_questions([{"label": "A", "type": "yesno", "required": True}, {"label": "B", "type": "short", "required": False},
                                    {"label": "C", "type": "yesno", "required": False}, {"label": "D", "type": "grid", "rows": ["r"], "cols": ["c"], "required": True}]) == [
        {"label": "A", "type": "yesno"}, {"label": "B", "type": "short"},  # the type's default isn't stored
        {"label": "C", "type": "yesno", "required": False}, {"label": "D", "type": "grid", "rows": ["r"], "cols": ["c"], "required": True}]
    qs = [dict(q) for q in QUESTIONS]
    qs[0]["required"] = False  # yes or no, optional
    for q in qs[1:5]:
        q["required"] = True  # several, short, long, grid
    club.save_site_settings({("membership", "questions"): qs})
    _, token = send_link(as_, sent)
    anon = as_(None)
    form = as_("n0aaaa").get(f"/apply/{token}").text
    assert 'value="Yes" required' not in form and "(optional)" in form and "(tick at least one)" in form
    for over, label in (({"q1": []}, "Which modes?"), ({"q2": ""}, "Why join?"), ({"q3": " "}, "Tell us more"), ({"q4": []}, "Bands")):
        r = anon.post(f"/apply/{token}", data=good(club, token, **over))
        assert r.status_code == 400 and f"Please answer: {label}" in r.text, label
    assert anon.post(f"/apply/{token}", data=good(club, token, q0="")).status_code == 200  # the optional yes or no left empty
    assert applications.find(token)["answers"][0]["value"] == ""


def test_a_paper_application_follows_the_online_path(env):
    club, as_, sent, _ = env
    page = as_("n0aaaa").get("/guests").text
    assert "Paper application received" in page and "Paper application received" not in as_("n0bbbb").get("/guests").text  # receives them; the Treasurer doesn't
    assert as_("n0bbbb").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0cccc"}).status_code == 403
    r = as_("n0aaaa").post("/applications/paper", data={"guest": "New Face", "sponsor": "n0cccc"}, follow_redirects=False)
    assert "visited%20enough" in r.headers["location"]
    assert "Active%20member" in as_("n0aaaa").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0dddd"}, follow_redirects=False).headers["location"]
    assert "signed%20as%20the%20sponsor" in as_("n0aaaa").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0aaaa", "signed": "1"}, follow_redirects=False).headers["location"]
    r = as_("n0aaaa").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0cccc"}, follow_redirects=False)
    assert "asked%20to%20confirm" in r.headers["location"] and sent[-1]["to"] == "bob@example.org" and "Will you sponsor Pat Visitor?" == sent[-1]["subject"]
    a = applications.for_guest(applications.load()["items"], "Pat Visitor")
    assert a["paper"] and a["paper_by"] == "n0aaaa" and a["state"] == "submitted" and a["form"]["name"] == "Pat Visitor" and a["form"]["email"] == "" and a["answers"] == []
    assert "already" in as_("n0aaaa").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0cccc"}, follow_redirects=False).headers["location"]
    n = len(sent)
    applications.notify_sponsor_answered(club, applications.sponsor_answer(club, a["id"], "n0cccc", False), "http://testserver")
    assert all(m["to"] != "" for m in sent[n:])  # no email to the applicant: there's none
    r = as_("n0aaaa").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0cccc", "signed": "1"}, follow_redirects=False)
    assert "a%20candidate" in r.headers["location"]
    a = applications.for_guest(applications.load()["items"], "Pat Visitor")
    assert a["state"] == "candidate" and a["sponsor"]["answer"] == "yes" and "On paper" in as_("n0aaaa").get("/guests").text
    from club_minutes import pdf
    text = "".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf.application(club, a))).pages)
    assert "Handed in on paper; recorded by Jane Doe" in text and "signed the paper form (as recorded by Jane Doe)" in text and "Signed electronically" not in text
    assert applications.prefill(a)["address"] == "" and applications.prefill(a)["first"] == "Pat"  # typed in from the paper at Add a member
    send_link(as_, sent)  # a candidate: no new link
    assert "applied%20already" in as_("n0aaaa").post("/guests/apply", data={"guest": "Pat Visitor", "email": "p@example.org"}, follow_redirects=False).headers["location"]


def test_paper_keeps_a_declined_online_application_and_needs_a_sponsor_to_ask(env, monkeypatch):
    club, as_, sent, _ = env
    _, token = send_link(as_, sent)
    as_(None).post(f"/apply/{token}", data=good(club, token))
    online = applications.find(token)
    applications.sponsor_answer(club, online["id"], "n0cccc", False)  # declined
    r = as_("n0aaaa").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0bbbb"}, follow_redirects=False)
    assert "asked%20to%20confirm" in r.headers["location"]
    items = applications.load()["items"]
    kept = next(x for x in items if x["id"] == online["id"])
    assert kept["form"]["street"] == "123 Main St" and kept["state"] == "declined" and len(items) == 2  # the online one untouched
    applications.withdraw(applications.for_guest(items, "Pat Visitor")["id"])
    monkeypatch.setattr(club.members["n0bbbb"], "email", "")
    assert "no%20email" in as_("n0aaaa").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0bbbb"}, follow_redirects=False).headers["location"]
    assert "a%20candidate" in as_("n0aaaa").post("/applications/paper", data={"guest": "Pat Visitor", "sponsor": "n0bbbb", "signed": "1"}, follow_redirects=False).headers["location"]
