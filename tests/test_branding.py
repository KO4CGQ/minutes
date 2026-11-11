"""The app's name and logo, set by an admin on Site (branding.py)."""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from w8lbz_minutes import branding, limits, web
from w8lbz_minutes.auth import Auth
from w8lbz_minutes.club import Club

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
    branding.set_name("  Our   Club  Minutes ", "kd8aaa")
    assert branding.name(c) == "Our Club Minutes"  # Site wins; spaces tidied
    branding.set_name("", "kd8aaa")
    assert branding.name(c) == "Club Records"     # empty: back to the next one
    c.info.pop("app_name"), c.info.pop("callsign", None)
    assert branding.name(c) == "Club Minutes"


def test_an_uploaded_logo_becomes_three_fresh_pngs_and_odd_files_are_refused(data):
    assert not branding.custom() and branding.path("logo").parent.name == "static"  # the plain one
    branding.save_logo(png())
    sizes = {k: Image.open(branding.path(k)).size for k in branding.FILES}
    assert sizes == {"logo": (300, 200), "favicon": (64, 64), "icon": (180, 180)} and branding.custom()
    assert Image.open(branding.path("icon")).getpixel((0, 0))[3] == 255  # a phone icon has no see-through corners
    with pytest.raises(ValueError, match="isn't a picture"):
        branding.save_logo(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    with pytest.raises(ValueError, match="Use a PNG"):
        b = io.BytesIO(); Image.new("RGB", (10, 10)).save(b, "GIF"); branding.save_logo(b.getvalue())
    branding.save_logo(png((2000, 2000)))
    assert max(Image.open(branding.path("logo")).size) == branding.LOGO_MAX  # only ever smaller
    branding.remove_logo()
    assert not branding.custom()


def test_site_page_is_for_admins_in_admin_mode_and_the_pictures_are_public(data, tmp_path, monkeypatch):
    club = Club(FIX / "test-club.yaml", roster=[{"id": "kd8adm", "first": "Amy", "last": "Able"},
                                                 {"id": "kd8bbb", "first": "Bob", "last": "Baker"}])
    club.admins = {"kd8adm"}
    monkeypatch.setattr(web.archive, "club", club)
    monkeypatch.setattr(web, "DATA", tmp_path)
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)

    def as_(who):
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    bob, amy = as_("kd8bbb"), as_("kd8adm")
    assert bob.get("/admin/site").status_code == 404 and bob.post("/admin/site/name", data={"name": "X"}).status_code == 404
    assert amy.get("/admin/site", follow_redirects=False).headers["location"].startswith("/admin/confirm")  # admin mode first
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    amy.post("/admin/site/name", data={"name": "Test Club Minutes"})
    assert "Test Club Minutes" in bob.get("/links").text  # every page's top bar
    r = amy.post("/admin/site/logo", files={"logo": ("logo.png", png(), "image/png")})
    assert "New logo saved" in r.text and branding.custom()
    anon = TestClient(web.app)
    pic = anon.get("/brand/logo.png")
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/png" and Image.open(io.BytesIO(pic.content)).size == (300, 200)
    assert anon.get("/brand/..%2Fclub.yaml").status_code == 404 and anon.get("/brand/secret.png").status_code == 404
    assert "a picture we can read" in amy.post("/admin/site/logo", files={"logo": ("x.png", b"not an image", "image/png")}).text
