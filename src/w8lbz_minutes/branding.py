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
    """The club's picture if uploaded, else the plain one that comes with the app."""
    own = _dir() / FILES[kind]
    return own if own.exists() else HERE / "static" / FILES[kind]


def custom() -> bool:
    return (_dir() / FILES["logo"]).exists()


def stamp() -> str:
    """Changes with each upload, for the pictures' addresses (?v=), so browsers fetch the new one."""
    p = _dir() / FILES["logo"]
    return str(int(p.stat().st_mtime)) if p.exists() else "0"


def save_logo(data: bytes) -> None:
    """Check an uploaded logo and make the three pictures from it. Raises ValueError with a message for the admin."""
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
        img = ImageOps.exif_transpose(img).convert("RGBA")
    except UnidentifiedImageError:
        raise ValueError("That file isn't a picture we can read. Use a PNG, JPEG or WebP.")
    logo = img.copy()
    logo.thumbnail((LOGO_MAX, LOGO_MAX), Image.LANCZOS)  # only ever smaller; keeps its shape

    def square(size: int, background=None) -> Image.Image:  # the whole logo, centred on a square
        side = max(img.size)
        sq = Image.new("RGBA", (side, side), background or (0, 0, 0, 0))
        sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
        return sq.resize((size, size), Image.LANCZOS)
    out = {"logo": logo, "favicon": square(64), "icon": square(180, (255, 255, 255, 255))}  # phones want no see-through
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    for kind, im in out.items():
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)  # a fresh file: nothing from the original but the pixels
        tmp = d / (FILES[kind] + ".tmp")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(d / FILES[kind])


def remove_logo() -> None:
    for f in FILES.values():
        (_dir() / f).unlink(missing_ok=True)
