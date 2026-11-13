"""The app's name and logo, set by an admin on Site (branding.py)."""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from club_minutes import branding, limits, web
from club_minutes.auth import Auth
from club_minutes.club import Club

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "DATA", tmp_path)
    monkeypatch.setattr(limits, "DATA", tmp_path, raising=False)
    return tmp_path


def png(size=(300, 200), color=(200, 30, 30, 255)) -> bytes:
    b = io.BytesIO()
    Image.new("RGBA", size, color).save(b, "PNG")
    return b.getvalue()


def test_the_name_comes_from_site_then_club_yaml_then_the_callsign(data):
    c = Club(FIX / "test-club.yaml", roster=[])
    assert branding.name(c) == f"{c.info['callsign']} Minutes"
    c.info["app_name"] = "Club Records"
    assert branding.name(c) == "Club Records"
    branding.set_name("  Our   Club  Minutes ", "n0aaaa")
    assert branding.name(c) == "Our Club Minutes"  # Site wins; spaces tidied
    branding.set_name("", "n0aaaa")
    assert branding.name(c) == "Club Records"     # empty: back to the next one
    c.info.pop("app_name"), c.info.pop("callsign", None)
    assert branding.name(c) == "Club Minutes"


def test_an_uploaded_logo_becomes_three_fresh_pngs_and_odd_files_are_refused(data):
    assert not branding.custom() and branding.path("logo").parent.name == "static"  # the plain one
    branding.save("logo", png())
    sizes = {k: Image.open(branding.path(k)).size for k in branding.FILES}
    assert sizes == {"logo": (300, 200), "favicon": (64, 64), "icon": (180, 180)} and branding.custom()
    assert Image.open(branding.path("icon")).getpixel((0, 0))[3] == 255  # a phone icon has no see-through corners
    with pytest.raises(ValueError, match="isn't a picture"):
        branding.save("logo", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    with pytest.raises(ValueError, match="Use a PNG"):
        b = io.BytesIO(); Image.new("RGB", (10, 10)).save(b, "GIF"); branding.save("logo", b.getvalue())
    branding.save("logo", png((2000, 2000)))
    assert max(Image.open(branding.path("logo")).size) == branding.LOGO_MAX  # only ever smaller
    branding.reset("logo")
    assert not branding.custom() and branding.source("favicon") == "plain"  # icons made from it go with it


def test_each_picture_is_uploaded_and_reset_on_its_own(data):
    blue, red = png(color=(0, 0, 255, 255)), png(color=(255, 0, 0, 255))
    # the colour in the middle, rounded (resizing blends a shade or two)
    pixel = lambda kind: tuple(round(c / 255) * 255 for c in Image.open(branding.path(kind)).convert("RGB").getpixel((32, 32)))
    branding.save("logo", red)
    assert [branding.source(k) for k in branding.FILES] == ["own", "logo", "logo"] and pixel("favicon") == (255, 0, 0)
    branding.save("favicon", blue)                                     # the tab icon on its own
    assert branding.source("favicon") == "own" and pixel("favicon") == (0, 0, 255)
    branding.save("logo", png(color=(0, 255, 0, 255)))                 # a new logo leaves it alone, redoes the phone icon
    assert pixel("favicon") == (0, 0, 255) and pixel("icon") == (0, 255, 0)
    branding.reset("favicon")                                          # back to made from the logo
    assert branding.source("favicon") == "logo" and pixel("favicon") == (0, 255, 0)
    branding.save("icon", blue); branding.reset("logo")                # an own icon stays when the logo goes
    assert [branding.source(k) for k in branding.FILES] == ["plain", "plain", "own"]
    with pytest.raises(ValueError):
        branding.save("../../etc/passwd", blue)


def test_site_page_is_for_admins_in_admin_mode_and_the_pictures_are_public(data, tmp_path, monkeypatch):
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed"},
                                                 {"id": "n0bbbb", "first": "Bob", "last": "Bloggs"}])
    from club_minutes import club as clubmod
    monkeypatch.setattr(clubmod, "DATA", tmp_path)
    club.save_app_admins({"n0admn"})  # (a Club tab save reloads the settings: an admin set by hand wouldn't last)
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(web, "DATA", tmp_path)
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)

    def as_(who):
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    bob, amy = as_("n0bbbb"), as_("n0admn")
    assert bob.get("/admin/site").status_code == 404 and bob.post("/admin/site/club", data={"app_name": "X"}).status_code == 404
    assert amy.get("/admin/site", follow_redirects=False).headers["location"].startswith("/admin/confirm")  # admin mode first
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    same = {"name": "Test Club", "callsign": "W0TEST", "meeting_day": "Thursday", "meeting_time": "19:30", "timezone": "America/New_York"}
    amy.post("/admin/site/club", data={**same, "app_name": "Test Club Minutes"})
    assert "Test Club Minutes" in bob.get("/links").text  # every page's top bar
    amy.post("/admin/site/club", data={**same, "callsign": "<b>", "app_name": "Other Minutes"})  # a refused save changes nothing
    amy.post("/admin/site/club", data={"back": "1", "app_name": "Other Minutes"})                # nor does Back to club.yaml
    assert branding.name(club) == "Test Club Minutes"
    r = amy.post("/admin/site/picture/logo", files={"file": ("logo.png", png(), "image/png")})
    assert "Logo saved" in r.text and branding.custom() and "Made from the logo" in amy.get("/admin/site").text
    assert amy.post("/admin/site/picture/other", files={"file": ("x.png", png(), "image/png")}).status_code == 404
    anon = TestClient(web.app)
    pic = anon.get("/brand/logo.png")
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/png" and Image.open(io.BytesIO(pic.content)).size == (300, 200)
    assert anon.get("/brand/..%2Fclub.yaml").status_code == 404 and anon.get("/brand/secret.png").status_code == 404
    assert "a picture we can read" in amy.post("/admin/site/picture/icon", files={"file": ("x.png", b"not an image", "image/png")}).text
    assert bob.post("/admin/site/picture/logo/reset").status_code == 404 and branding.custom()   # members can't


def test_behind_the_logo_is_picked_from_its_shape_unless_chosen(data):
    assert branding.backing() == "circle"                                # the plain logo: round, see-through corners
    branding.save("logo", png((400, 100), (0, 0, 0, 0)))                 # wide and see-through
    assert branding.backing() == "square"
    branding.save("logo", png((300, 300), (20, 60, 20, 255)))            # its own background: nothing behind it
    assert branding.backing() == "none"
    branding.set_backing("circle")
    assert branding.backing() == "circle" and branding.auto_backing() == "none"
    branding.set_backing("<script>")
    assert branding.backing() == "none"                                  # anything else is Automatic


def test_a_tab_icon_made_from_the_logo_gets_the_logos_white_behind_it(data):
    branding.save("logo", png((300, 300), (0, 0, 0, 0)))                # line art: see-through, round-ish
    fav = lambda: Image.open(branding.path("favicon")).convert("RGBA")
    assert branding.backing() == "circle" and fav().getpixel((32, 32)) == (255, 255, 255, 255) and fav().getpixel((0, 0))[3] == 0
    branding.set_backing("none")                                         # changing "behind it" redraws it
    assert fav().getpixel((32, 32))[3] == 0
    branding.set_backing("square")
    assert fav().getpixel((2, 32))[3] == 255
    branding.save("favicon", png((64, 64), (0, 0, 255, 255)))            # one of its own is left alone
    branding.set_backing("circle")
    assert fav().getpixel((0, 0)) == (0, 0, 255, 255)


def test_damaged_pictures_are_refused_and_a_damaged_logo_on_disk_never_stops_a_page(data, monkeypatch):
    with pytest.raises(ValueError, match="a picture we can read"):
        branding.save("logo", png()[:200])                              # cut off part way
    from club_minutes import avatars
    monkeypatch.setattr(avatars, "DATA", data)
    with pytest.raises(ValueError):
        avatars.save("n0aaaa", png()[:200])                             # profile pictures too
    branding.save("logo", png())
    (data / "branding" / "logo.png").write_bytes(b"damaged")            # disk trouble, a bad restore
    assert branding.backing() == "none"                                 # every page asks this
    branding.set_backing("circle")                                      # and changing it still works
    c = Club(FIX / "test-club.yaml", roster=[])
    c.info["app_name"] = "Two\r\nLines"
    assert branding.name(c) == "Two Lines"                              # never a line break in a subject
