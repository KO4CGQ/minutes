"""The app's name and pictures, set by an admin (admin mode → name menu → Site).

The name: Site's setting, else `app_name` in club.yaml, else "<callsign> Minutes". The pictures: the club's logo,
uploaded once on Site, becomes three files in the data folder (branding/): the logo shown on pages and in PDFs, the
browser tab's icon and a phone's home-screen icon. Each upload is opened, checked and drawn again as a fresh PNG,
so nothing hidden in the original (a script in an SVG, camera details) is kept. Without an upload, the plain
pictures in static/ are used.
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

from . import limits
from .club import DATA

HERE = Path(__file__).parent
FILES = {"logo": "logo.png", "favicon": "favicon.png", "icon": "apple-touch-icon.png"}
FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}
LOGO_MAX = 512  # the logo's longest side; it keeps its shape (round, wide or tall)


def _dir() -> Path:
    return DATA / "branding"


def _settings() -> dict:
    p = DATA / "branding.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def name(club) -> str:
    """What the app calls itself: page titles, the top bar, emails, the passkey's name on phones."""
    return (_settings().get("name") or str(club.info.get("app_name") or "").strip()
            or f"{club.info.get('callsign') or 'Club'} Minutes")


def set_name(text: str, by: str) -> None:
    text = " ".join((text or "").split())[:60]
    s = _settings() | {"name": text, "by": by, "at": int(time.time())}
    tmp = DATA / "branding.json.tmp"
    tmp.write_text(json.dumps(s))
    tmp.replace(DATA / "branding.json")


def path(kind: str) -> Path:
    """The club's picture if there is one (uploaded, or for an icon, made from the logo), else the plain one."""
    own = _dir() / FILES[kind]
    return own if own.exists() else HERE / "static" / FILES[kind]


def source(kind: str) -> str:
    """Where a picture comes from: "own" (uploaded for this use), "logo" (an icon made from the logo) or "plain"."""
    if not (_dir() / FILES[kind]).exists():
        return "plain"
    return "own" if kind == "logo" or _settings().get("own", {}).get(kind) else "logo"


BACKINGS = {"auto": "Automatic", "circle": "A white circle", "square": "A white rounded square", "none": "Nothing"}


def backing() -> str:
    """What goes behind the logo on pages, so dark ink on a see-through background still shows in dark mode:
    Site's choice, or by itself: a see-through logo about as wide as tall gets a white circle (a round emblem), another
    see-through one a white rounded square, and one with its own background nothing."""
    chosen = _settings().get("backing")
    return chosen if chosen in ("circle", "square", "none") else auto_backing()


def auto_backing() -> str:
    """Automatic's pick, from the logo's shape and whether its corners are see-through."""
    from PIL import Image
    img = Image.open(path("logo")).convert("RGBA")
    w, h = img.size
    corners = [img.getpixel(p)[3] for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    if min(corners) == 255:
        return "none"
    return "circle" if 0.85 <= w / h <= 1.18 else "square"


def set_backing(value: str) -> None:
    s = _settings()
    s["backing"] = value if value in BACKINGS else "auto"
    tmp = DATA / "branding.json.tmp"
    tmp.write_text(json.dumps(s))
    tmp.replace(DATA / "branding.json")


def custom() -> bool:
    return (_dir() / FILES["logo"]).exists()


def stamp() -> str:
    """Changes with each upload, for the pictures' addresses (?v=), so browsers fetch the new one."""
    files = [_dir() / f for f in FILES.values() if (_dir() / f).exists()]
    return str(max(int(f.stat().st_mtime_ns // 1_000_000) for f in files)) if files else "0"


def _open(data: bytes):
    """An uploaded picture, checked, the right way up, with see-through kept. Raises ValueError for the admin."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data:
        raise ValueError("Pick a picture first.")
    if len(data) > limits.max_upload():
        raise ValueError(f"That picture is too big ({limits.upload_mb()} MB at most).")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in FORMATS:
            raise ValueError("Use a PNG (best: it can have a see-through background), JPEG or WebP picture.")
        if img.width * img.height > 60_000_000:
            raise ValueError("That picture is too big.")
        return ImageOps.exif_transpose(img).convert("RGBA")
    except UnidentifiedImageError:
        raise ValueError("That file isn't a picture we can read. Use a PNG, JPEG or WebP.")


def _render(kind: str, img):
    """The picture for one use: the logo keeps its shape (at most LOGO_MAX); an icon is the whole picture centred on
    a square (a phone's on white: home screens show no see-through)."""
    from PIL import Image
    if kind == "logo":
        out = img.copy()
        out.thumbnail((LOGO_MAX, LOGO_MAX), Image.LANCZOS)  # only ever smaller
        return out
    size, background = (64, None) if kind == "favicon" else (180, (255, 255, 255, 255))
    side = max(img.size)
    sq = Image.new("RGBA", (side, side), background or (0, 0, 0, 0))
    sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return sq.resize((size, size), Image.LANCZOS)


def _write(kind: str, img) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)  # a fresh file: nothing from the original but the pixels
    tmp = d / (FILES[kind] + ".tmp")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(d / FILES[kind])


def _set_own(kind: str, own: bool) -> None:
    s = _settings()
    s["own"] = {**s.get("own", {}), kind: own}
    tmp = DATA / "branding.json.tmp"
    tmp.write_text(json.dumps(s))
    tmp.replace(DATA / "branding.json")


def save(kind: str, data: bytes) -> None:
    """Upload one picture. The logo also makes the icons that weren't uploaded on their own."""
    if kind not in FILES:
        raise ValueError("Pick which picture this is.")
    img = _open(data)
    _write(kind, _render(kind, img))
    if kind == "logo":
        for icon in ("favicon", "icon"):
            if source(icon) != "own":
                _write(icon, _render(icon, img))
    else:
        _set_own(kind, True)


def reset(kind: str) -> None:
    """Back to the default: for the logo, the plain one (and icons made from it go too); for an icon, made from the
    club's logo if there is one, else the plain one."""
    if kind not in FILES:
        return
    if kind == "logo":
        (_dir() / FILES["logo"]).unlink(missing_ok=True)
        for icon in ("favicon", "icon"):
            if source(icon) == "logo":
                (_dir() / FILES[icon]).unlink(missing_ok=True)
        return
    _set_own(kind, False)
    (_dir() / FILES[kind]).unlink(missing_ok=True)
    logo = _dir() / FILES["logo"]
    if logo.exists():
        from PIL import Image
        _write(kind, _render(kind, Image.open(logo).convert("RGBA")))
