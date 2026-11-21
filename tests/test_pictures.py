"""Pictures on reports, business items and comments (pictures.py): checked like profile pictures,
shrunk, no EXIF (no phone location), members only, at most 4 each; carried through the console and corrections."""
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_avatars import photo

from club_minutes import business, pictures, web
from club_minutes import club as clubmod
from club_minutes.auth import Auth
from club_minutes.club import Club
from club_minutes.store import Archive

FIX = Path(__file__).parent / "fixtures"
PEOPLE = [{"id": "pres", "first": "Jane", "last": "Doe", "role": "President", "email": "jane@example.org"},
          {"id": "n0call", "first": "John", "last": "Doe", "email": "john@example.org"},
          {"id": "w0demo", "first": "Sam", "last": "Roe", "email": "sam@example.org"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    for mod in (clubmod, pictures, business):
        monkeypatch.setattr(mod, "DATA", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path)
    c = Club(FIX / "test-club.yaml", roster=PEOPLE)
    a = Auth(c, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)
    monkeypatch.setattr(web, "archive", Archive(tmp_path / "meetings", c))

    def as_(who):
        t = TestClient(web.app, client=("203.0.113.6", 5000))
        if who:
            t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    return c, as_


def test_saved_shrunk_without_location(env):
    pid = pictures.save(photo(4000, 3000))
    big, small = Image.open(pictures.path(pid)), Image.open(pictures.path(pid, True))
    assert max(big.size) == 1600 and max(small.size) == 800 and not big.getexif() and not small.getexif()
    for bad, msg in ((b"", "Pick a picture"), (b"not a picture", "isn't a picture"), (photo(fmt="GIF", exif=False), "JPEG, PNG or WebP")):
        with pytest.raises(ValueError, match=msg):
            pictures.save(bad)
    assert pictures.ids([pid, pid, "nope", "../../etc/passwd", 7]) == [pid]


def test_members_only_and_on_items_and_comments(env):
    _c, as_ = env
    s = business.load()
    x = business.add(s, "Fix the tower light", "pres", owner="n0call")
    business.save(s)
    member, owner = as_("w0demo"), as_("n0call")
    f = [("files", ("a.jpg", photo(), "image/jpeg"))]
    owner.post(f"/business/{x['id']}/pictures", files=f)
    pid = business.load()["items"][-1]["pictures"][0]
    assert as_(None).get(f"/pictures/{pid}.jpg", follow_redirects=False).status_code in (303, 401)  # never public
    assert member.get(f"/pictures/{pid}-s.jpg").headers["content-type"] == "image/jpeg"
    assert member.get("/pictures/..%2Fauth.sqlite").status_code == 404
    owner.post(f"/business/{x['id']}/pictures", files=f * 4)  # 1 + 4 > 4: refused, nothing added
    assert len(business.load()["items"][-1]["pictures"]) == 1
    member.post(f"/business/{x['id']}/comment", data={"text": "Here's the bracket"}, files=f)
    cm = business.load()["items"][-1]["comments"][-1]
    assert len(cm["pictures"]) == 1 and f'/pictures/{cm["pictures"][0]}-s.jpg' in member.get("/business").text
    owner.post(f"/business/{x['id']}/pictures/{pid}/remove")
    assert business.load()["items"][-1]["pictures"] == []


def test_who_may_add_and_remove(env):
    _c, as_ = env
    s = business.load()
    x = business.add(s, "Fix the tower light", "pres", owner="n0call")
    business.save(s)
    pics = lambda: business.load()["items"][-1].get("pictures", [])
    f = [("files", ("a.jpg", photo(), "image/jpeg"))]
    as_("w0demo").post(f"/business/{x['id']}/pictures", files=f)  # another member's item: refused
    as_(None).post(f"/business/{x['id']}/pictures", files=f, follow_redirects=False)  # signed out: refused
    as_("n0call").post(f"/business/{x['id']}/pictures", files=[("files", ("a.txt", b"not a picture", "text/plain"))])  # not an image
    assert pics() == [] and not list((pictures.DATA / "pictures").glob("*.jpg"))  # refused before anything was stored
    as_("w0demo").post("/business/nope/comment", data={"text": "Hi"}, files=f)  # no such item: nothing stored either
    assert not list((pictures.DATA / "pictures").glob("*.jpg"))
    as_("pres").post(f"/business/{x['id']}/pictures", files=f)  # an officer may
    pid = pics()[0]
    as_("w0demo").post(f"/business/{x['id']}/pictures/{pid}/remove")
    assert pics() == [pid]
    as_("w0demo").post(f"/business/{x['id']}/comment", data={"text": "Look"}, files=f)
    c = business.load()["items"][-1]["comments"][-1]
    url = f"/business/{x['id']}/comments/{c['id']}/pictures/{c['pictures'][0]}/remove"
    assert as_("w0demo").post(url).status_code == 403 and business.load()["items"][-1]["comments"][-1]["pictures"]  # not even its author
    as_("pres").post(url)  # a moderator may
    assert business.load()["items"][-1]["comments"][-1]["pictures"] == []
    assert as_("w0demo").post("/api/pictures", files={"file": ("a.jpg", photo(), "image/jpeg")}).status_code == 403  # the console: leaders only
    assert as_("pres").post("/api/pictures", files={"file": ("a.txt", b"nope", "text/plain")}).status_code == 400


def test_a_correction_can_remove_a_picture(env):
    import datetime as dt

    from club_minutes import corrections
    from club_minutes.meeting import Item, Meeting
    a, b = pictures.save(photo()), pictures.save(photo())
    m = Meeting(date=dt.date(2026, 10, 15), status="published", sections={"Community Reports": [Item("New antenna", "n0call", pictures=[a, b])]})
    n = list(m.section_order(web.archive.club.sections())).index("Community Reports")
    new = corrections.from_form(web.archive, m, {f"s{n}-0": "New antenna", f"s{n}-0-pic-{a}-rm": "1"})
    assert new.items("Community Reports")[0].pictures == [b]


def test_a_report_keeps_its_pictures_through_the_minutes(env):
    import datetime as dt

    from club_minutes.meeting import Item, Meeting
    pid = pictures.save(photo())
    m = Meeting(date=dt.date(2026, 10, 15), status="published", sections={"Community Reports": [Item("New antenna", "n0call", pictures=[pid])]})
    back = Meeting.from_markdown(m.to_markdown())
    assert back.items("Community Reports")[0].pictures == [pid] and back.items("Community Reports")[0].text == "New antenna"
    _c, as_ = env
    m.save(web.archive.root)
    web.archive.reload()
    assert f'src="/pictures/{pid}-s.jpg"' in as_("n0call").get("/meetings/2026-10-15").text
    _name, pdf = web.render_pdf(web.archive.by_slug["2026-10-15"])
    assert pdf.startswith(b"%PDF") and b"/Subtype /Image" in pdf


def test_a_typed_pictures_tag_is_only_text():
    import datetime as dt

    from club_minutes.meeting import Item, Meeting
    md = Meeting(date=dt.date(2026, 10, 15), sections={"Community Reports": [Item("Hello")]}).to_markdown()
    it = Meeting.from_markdown(md.replace("- Hello", "- Hello <!-- pictures: ../../x zz -->")).items("Community Reports")[0]
    assert it.pictures == [] and "pictures:" in it.text


def test_a_removed_picture_is_deleted_unless_still_used(env):
    import datetime as dt

    from club_minutes.meeting import Item, Meeting
    _c, as_ = env
    s = business.load()
    x = business.add(s, "Fix the tower light", "pres", owner="n0call")
    business.save(s)
    f = [("files", ("a.jpg", photo(), "image/jpeg"))]
    owner = as_("n0call")
    owner.post(f"/business/{x['id']}/pictures", files=f * 2)
    a, b = business.load()["items"][-1]["pictures"]
    Meeting(date=dt.date(2026, 10, 15), status="published",  # b is also in published minutes
            sections={"Community Reports": [Item("New antenna", "n0call", pictures=[b])]}).save(web.archive.root)
    for p in (a, b):
        owner.post(f"/business/{x['id']}/pictures/{p}/remove")
    assert not pictures.exists(a) and owner.get(f"/pictures/{a}.jpg").status_code == 404 and pictures.exists(b)
    owner.post(f"/business/{x['id']}/comment", data={"text": "Look"}, files=f)
    c = business.load()["items"][-1]["comments"][-1]
    as_("w0demo").post(f"/business/{x['id']}/comments/{c['id']}/delete")  # not theirs: kept, picture too
    assert pictures.exists(c["pictures"][0])
    owner.post(f"/business/{x['id']}/comments/{c['id']}/delete")
    assert not pictures.exists(c["pictures"][0])


def test_a_daily_cap_per_member(env, monkeypatch):
    monkeypatch.setattr(pictures, "_added", {})
    c, as_ = env
    assert pictures.per_day(c) == pictures.PER_DAY
    c.info["pictures_per_day"] = 2
    s = business.load()
    x = business.add(s, "Fix the tower light", "pres", owner="n0call")
    business.save(s)
    f = [("files", ("a.jpg", photo(), "image/jpeg"))]
    owner = as_("n0call")
    owner.post(f"/business/{x['id']}/pictures", files=f * 2)
    r = owner.post(f"/business/{x['id']}/comment", data={"text": "One more"}, files=f, follow_redirects=False)
    assert "pictures+today" in r.headers["location"] or "pictures%20today" in r.headers["location"]
    assert len(business.load()["items"][-1]["pictures"]) == 2 and not business.load()["items"][-1].get("comments")
    as_("w0demo").post(f"/business/{x['id']}/comment", data={"text": "Mine"}, files=f)  # another member: their own count
    assert business.load()["items"][-1]["comments"][-1]["pictures"]


def test_the_cap_is_set_on_site_features(env, monkeypatch):
    c, as_ = env
    c.admins = {"pres"}
    monkeypatch.setattr(web, "_site_admin", lambda request: "pres")  # admin mode and step-up: tested in test_security
    monkeypatch.setattr(c, "save_site_settings", lambda values: c.info.update({k: v for (sec, k), v in values.items() if sec == "club" and k == "pictures_per_day"}))
    r = as_("pres").post("/admin/site/features", data={"pictures_per_day": "0"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and pictures.per_day(c) == pictures.PER_DAY
    as_("pres").post("/admin/site/features", data={"pictures_per_day": "7"}, follow_redirects=False)
    assert pictures.per_day(c) == 7


def test_pictures_on_a_new_item_a_note_and_a_motion_reach_the_minutes(env):
    """Jim, 2026-11-20: Add an item, a note for the next meeting and a motion take pictures; a business item's pictures
    and formatting go in the minutes (the owner and due date end its first line, not a list it holds)."""
    import datetime as dt

    from club_minutes import console
    _c, as_ = env
    f = [("files", ("a.jpg", photo(), "image/jpeg"))]
    pres = as_("pres")
    pres.post("/business/add", data={"text": "Fix the **tower** light\n- bulb\n- ladder", "owner": "John Doe"}, files=f)
    x = business.load()["items"][-1]
    assert len(x["pictures"]) == 1 and x["text"].startswith("Fix the **tower**")
    assert pres.post("/business/add", data={"text": "x"}, files=f * 5, follow_redirects=False).headers["location"].startswith("/business?tab=add&error=")
    meeting = web.upcoming_meetings()[0].isoformat()
    as_("w0demo").post("/business/notes", data={"text": "Away; *new* mast up", "meeting": meeting}, files=f)
    assert len(business.load()["notes"][-1]["pictures"]) == 1
    assert "<em>new</em>" in as_("pres").get("/business?tab=notes").text
    # a motion at a live meeting, with a picture uploaded in the console first
    day = dt.date.fromisoformat(meeting)
    s = business.load()
    business.get(s, x["id"])["opened"] = "2026-01-01"  # raised before that meeting: its Old Business
    business.save(s)
    console.start(web.archive, day, "pres")
    pid = pres.post("/api/pictures", files={"file": ("a.jpg", photo(), "image/jpeg")}).json()["id"]
    pres.post("/business/motion", data={"mover": "John Doe", "seconder": "Sam Roe", "what": "buy a *ladder*", "result": "Passed",
                                         "meeting": meeting, "pics": [pid, "nope"]}, headers={"Accept": "application/json"})
    assert business.motions(business.load())[-1]["pictures"] == [pid]
    m = web.archive.by_slug[meeting]
    old = next(i for i in m.items("Old Business") if i.text.startswith("Fix the"))
    assert old.text == "Fix the **tower** light (John)\n- bulb\n- ladder" and old.pictures == x["pictures"]
    new = next(i for i in m.items("New Business") if "ladder" in i.text)
    assert new.pictures == [pid] and "*ladder*" in new.text


def test_new_pictures_are_tidied_up(env):
    """The security review of 2026-11-20: a refused item or note stores no picture; a withdrawn or skipped note's
    pictures are deleted (a note read out keeps them); a picture taken off an item leaves minutes being written too."""
    import datetime as dt

    from club_minutes import console
    _c, as_ = env
    f = [("files", ("a.jpg", photo(), "image/jpeg"))]
    pres, member = as_("pres"), as_("w0demo")
    files = lambda: {p.name for p in pictures._dir().iterdir()} if pictures._dir().exists() else set()
    before = files()
    pres.post("/business/add", data={"text": "x", "owner": "Nobody Here"}, files=f)
    pres.post("/business/add", data={"text": "  "}, files=f)
    meeting = web.upcoming_meetings()[0].isoformat()
    member.post("/business/notes", data={"text": " ", "meeting": meeting}, files=f)
    assert files() == before
    for status in ("withdrawn", "dismissed"):
        member.post("/business/notes", data={"text": "Away", "meeting": meeting}, files=f)
        n = business.load()["notes"][-1]
        (member if status == "withdrawn" else pres).post(f"/business/notes/{n['id']}/{status}")
        assert not pictures.exists(n["pictures"][0])
    pres.post("/business/add", data={"text": "Tower light"}, files=f)
    s = business.load()
    x = s["items"][-1]
    x["opened"] = "2026-01-01"
    business.save(s)
    console.start(web.archive, dt.date.fromisoformat(meeting), "pres")
    m = lambda: next(i for i in web.archive.by_slug[meeting].items("Old Business") if i.text.startswith("Tower light"))
    assert m().pictures == x["pictures"]
    pres.post(f"/business/{x['id']}/pictures/{x['pictures'][0]}/remove")
    assert m().pictures == [] and not pictures.exists(x["pictures"][0])

def test_nightly_prune_deletes_only_old_unused_pictures(env):
    import os
    import time
    old_t = time.time() - 2 * 86400

    def make(age=0):
        pid = pictures.save(photo())
        for small in (False, True):
            os.utime(pictures.path(pid, small), (old_t, old_t) if age else None)
        return pid
    unused, fresh, on_item, on_comment, in_minutes, in_version, in_draft = (
        make(1), make(), make(1), make(1), make(1), make(1), make(1))
    s = business.load()
    x = business.add(s, "Fix the tower light", "pres", owner="n0call")
    x["pictures"] = [on_item]
    x["comments"] = [{"id": "c1", "by": "pres", "text": "Done", "pictures": [on_comment]}]
    business.save(s)
    root = web.archive.root
    root.mkdir(parents=True, exist_ok=True)
    (root / "minutes.md").write_text(f"pictures: {in_minutes}")
    (root / "minutes.v1.md").write_text(f"pictures: {in_version}")
    (root / "console-draft.json").write_text(json.dumps({"pictures": [in_draft]}))
    stray = pictures.path(unused).with_suffix(".tmp")
    stray.write_bytes(b"x")
    os.utime(stray, (old_t, old_t))
    assert pictures.prune_unused(web.archive) == 1
    assert not pictures.exists(unused) and not pictures.path(unused, True).exists() and not stray.exists()
    assert all(pictures.exists(p) for p in (fresh, on_item, on_comment, in_minutes, in_version, in_draft))


def test_prune_deletes_nothing_without_a_minutes_folder(env, tmp_path):
    import os
    import time
    pid = pictures.save(photo())
    old_t = time.time() - 2 * 86400
    for small in (False, True):
        os.utime(pictures.path(pid, small), (old_t, old_t))
    lost = type("A", (), {"root": tmp_path / "no-such-folder"})()
    assert pictures.prune_unused(lost) == 0 and pictures.exists(pid)


def test_a_refused_remove_deletes_nothing_and_checks_nothing(env, monkeypatch):
    _c, as_ = env
    s = business.load()
    x = business.add(s, "Fix the tower light", "pres", owner="n0call")
    pid = pictures.save(photo())
    x["pictures"] = [pid]
    business.save(s)
    calls = []
    monkeypatch.setattr(pictures, "unused", lambda *a: calls.append(a) or [])
    as_("w0demo").post(f"/business/{x['id']}/pictures/{pid}/remove")  # not theirs: refused
    as_("pres").post(f"/business/{x['id']}/pictures/..%2Fx/remove")  # not a picture id
    assert not calls and pictures.exists(pid) and business.load()["items"][-1]["pictures"] == [pid]


def test_a_huge_transparent_picture_is_refused(env):
    buf = io.BytesIO()
    Image.new("RGBA", (6000, 5000)).save(buf, "PNG")  # 30 MP, a few KB
    with pytest.raises(ValueError, match="too big"):
        pictures.save(buf.getvalue())


def test_prune_deletes_nothing_without_business_json(env):
    import os
    import time
    pid = pictures.save(photo())
    old_t = time.time() - 2 * 86400
    for small in (False, True):
        os.utime(pictures.path(pid, small), (old_t, old_t))
    web.archive.root.mkdir(parents=True, exist_ok=True)
    assert not (web.DATA / "business.json").exists()
    assert pictures.prune_unused(web.archive) == 0 and pictures.exists(pid)


def test_only_a_private_correction_deletes_the_pictures_it_removed(env):
    import datetime as dt

    from club_minutes.meeting import Item, Meeting
    _c, as_ = env
    business.save(business.load())
    pres = as_("pres")
    for private in (False, True):
        a, b = pictures.save(photo()), pictures.save(photo())
        date = dt.date(2026, 10, 15 + private)
        Meeting(date=date, status="published", sections={"Community Reports": [Item("New antenna", "n0call", pictures=[a, b])]}).save(web.archive.root)
        web.archive.reload()
        m = web.archive.by_slug[date.isoformat()]
        n = list(m.section_order(web.archive.club.sections())).index("Community Reports")
        r = pres.post(f"/meetings/{m.slug}/correct", follow_redirects=False,
                      data={f"s{n}-0": "New antenna", f"s{n}-0-pic-{a}-rm": "1", "reason": "Oops", "version": "1", **({"private": "1"} if private else {})})
        assert r.status_code == 303 and "error" not in r.headers["location"], r.headers["location"]
        assert pictures.exists(b) and pictures.exists(a) is (not private)
