"""The club's colours (theme.py): a palette from one colour, always readable, set on Site."""
import colorsys
import datetime as dt
import re
from pathlib import Path

import pytest
from PIL import Image

from club_minutes import branding, limits, theme
from club_minutes.club import Club
from club_minutes.meeting import Meeting

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "DATA", tmp_path)
    monkeypatch.setattr(limits, "DATA", tmp_path, raising=False)
    return tmp_path


def failing(light, dark):
    return [(t, b) for p, pairs, texts in ((light, theme.LIGHT_PAIRS, theme.LIGHT_TEXT), (dark, theme.DARK_PAIRS, theme.DARK_TEXT))
            for t, b, least in pairs + texts if theme.contrast(p.get(t, t)[:7], p.get(b, b)[:7]) < least]


def test_the_colour_the_palette_was_tuned_from_gives_it_back_exactly():
    light, dark, notes = theme.palette(theme.SHAPE_FROM)
    assert light == {**theme.LIGHT, "--btn-text": "#ffffff"} and dark == theme.DARK and not notes  # a club keeping that green loses nothing


def test_every_colour_and_top_bar_stays_readable():
    for h in range(0, 360, 30):
        for s in (0, 0.5, 1):
            for lightness in (0, 0.2, 0.5, 0.8, 1):
                c = theme._hex(*colorsys.hls_to_rgb(h / 360, lightness, s))
                for bar in (None, c, "#ffffff", "#000000", "#ffff00"):
                    light, dark, notes = theme.palette(c, bar)
                    assert not failing(light, dark), (c, bar)
    assert theme.palette("#ffff00", "#ffff00")[2]  # a pale bar behind white text was darkened, and the admin is told


def test_style_css_has_the_default_blue_built_in():
    css = (Path(theme.__file__).parent / "static" / "style.css").read_text()
    light, dark, _ = theme.palette(theme.DEFAULT)
    root, dark_block, media = (css[css.index(":root {"):css.index(':root[data-theme="dark"]')],
                               css[css.index(':root[data-theme="dark"]'):css.index("@media (prefers-color-scheme: dark)")],
                               css[css.index("@media (prefers-color-scheme: dark)"):])
    for block, pal in ((root, light), (dark_block, dark), (media, dark)):
        for k, v in pal.items():
            assert re.search(rf"{re.escape(k)}: {re.escape(v)};", block), (k, v)  # run the generator again if this fails


def test_bad_colours_are_refused_or_ignored(data):
    assert theme.valid(" #2D5F9A ") == "#2d5f9a" and not any(map(theme.valid, ("red", "#fff", "#12345g", "url(x)", None)))
    assert theme.palette("not a colour") == theme.palette(theme.DEFAULT)
    with pytest.raises(ValueError):
        branding.set_colours("red", None, "n0admn")
    with pytest.raises(ValueError):
        branding.set_colours("#123456", "};body{display:none", "n0admn")
    assert branding.colours() == {"main": theme.DEFAULT, "bar": None}


def test_suggestions_from_the_logo_are_its_colours_not_white_black_or_see_through(tmp_path):
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for (x, y), colour in (((0, 0), (200, 30, 30, 255)), ((50, 0), (20, 60, 160, 255)), ((0, 50), (255, 255, 255, 255)), ((50, 50), (0, 0, 0, 255))):
        img.paste(Image.new("RGBA", (50, 50), colour), (x, y))
    img.save(tmp_path / "logo.png")
    got = theme.from_logo(tmp_path / "logo.png")
    assert len(got) == 2 and {g[1] > g[5] for g in got} == {True, False}  # the red and the blue; no white, black or clear
    (tmp_path / "bad.png").write_bytes(b"not a picture")
    assert theme.from_logo(tmp_path / "bad.png") == []


def test_site_sets_the_colours_every_page_and_pdf_follows(data, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from club_minutes import web
    from club_minutes.auth import Auth
    club = Club(FIX / "test-club.yaml", roster=[{"id": "n0admn", "first": "Amy", "last": "Appleseed"}, {"id": "n0bbbb", "first": "Bob", "last": "Bloggs"}])
    club.admins = {"n0admn"}
    monkeypatch.setattr(web.archive, "club", club)
    a = Auth(club, tmp_path / "auth.sqlite")
    monkeypatch.setattr(web, "authn", a)

    def as_(who):
        t = TestClient(web.app)
        t.cookies.set(web.COOKIE, a.new_session(who))
        return t
    bob, amy, anon = as_("n0bbbb"), as_("n0admn"), TestClient(web.app)
    assert anon.get("/brand/theme.css").text == theme.css(theme.DEFAULT)  # public: the sign-in page uses it
    assert bob.get("/admin/site/colours/preview?main=%23ff0000").status_code == 404
    assert bob.post("/admin/site/colours", data={"main": "#ff0000"}).status_code == 404
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    link = lambda: re.search(r'href="/brand/theme\.css\?v=(\w+)"', bob.get("/links").text).group(1)
    before = link()
    prev = amy.get("/admin/site/colours/preview?main=%23aa2222&bar=%23ffff00").json()
    assert prev["light"]["--nav-bg"] != "#ffff00" and prev["notes"] and branding.colours()["main"] == theme.DEFAULT  # only a preview
    r = amy.post("/admin/site/colours", data={"main": "#aa2222", "own_bar": "1", "bar": "#103060"})
    assert "Colours saved" in r.text and branding.colours() == {"main": "#aa2222", "bar": "#103060"}
    assert link() != before and "--nav-bg: #103060" in anon.get("/brand/theme.css").text  # browsers fetch the new colours
    assert "Bad" not in amy.post("/admin/site/colours", data={"main": "#zzzzzz"}).text and branding.colours()["main"] == "#aa2222"
    html = web.templates.get_template("minutes_doc.html").render(club=club, pdf=True, m=Meeting(date=dt.date(2026, 11, 5)))
    assert f"--sheet-h: {theme.palette('#aa2222', '#103060')[0]['--sheet-h']}" in html  # the PDF's section headings too
    amy.post("/admin/site/colours/reset")
    assert branding.colours() == {"main": theme.DEFAULT, "bar": None} and link() == before


def test_a_picked_top_bar_shows_in_dark_mode_too_dimmed():
    hue = lambda h: round(colorsys.rgb_to_hls(*theme._rgb(h))[0], 2)
    light, dark, _ = theme.palette(theme.DEFAULT, "#7a3fb0")
    assert light["--nav-bg"] == "#7a3fb0" and hue(dark["--nav-bg"]) == hue("#7a3fb0")  # a purple bar is a dark purple bar
    assert colorsys.rgb_to_hls(*theme._rgb(dark["--nav-bg"]))[1] <= 0.225  # dimmed (to 0.22, give or take the hex rounding): a dark page keeps a dark bar
    assert theme.palette(theme.SHAPE_FROM)[1]["--nav-bg"] == theme.DARK["--nav-bg"]  # no bar picked: unchanged
