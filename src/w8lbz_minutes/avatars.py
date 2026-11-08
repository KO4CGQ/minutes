"""Members' profile pictures.

A member uploads a photo on their profile; it's turned the right way up, cropped to a square, shrunk to
256 px and saved as a fresh JPEG (PNG if it has transparency, which is kept), which also drops anything hidden in the original (camera details, GPS
location). Pictures are only served to signed-in members. An officer, or an admin in admin mode, can
remove one. Without a picture, a coloured circle with the member's initials is shown.
"""
from __future__ import annotations

import hashlib
import io

from .club import DATA

SIZE = 256
MAX_UPLOAD = 8 * 1024 * 1024  # bytes
FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}  # MPO: some phone cameras


def _dir():
    d = DATA / "avatars"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path(mid: str):
    """The member's picture file: .png when the upload had transparency, else .jpg."""
    png = _dir() / f"{mid}.png"
    return png if png.exists() else _dir() / f"{mid}.jpg"


def save(mid: str, data: bytes) -> None:
    """Check, square up and store a picture. Raises ValueError with a message for the member."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data:
        raise ValueError("Pick a picture first.")
    if len(data) > MAX_UPLOAD:
        raise ValueError("That picture is too big (8 MB at most).")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in FORMATS:
            raise ValueError("Use a JPEG, PNG or WebP picture.")
        if img.width * img.height > 60_000_000:
            raise ValueError("That picture is too big.")
        img = ImageOps.exif_transpose(img)
    except UnidentifiedImageError:
        raise ValueError("That file isn't a picture we can read. Use a JPEG, PNG or WebP.")
    clear = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    img = ImageOps.fit(img.convert("RGBA" if clear else "RGB"), (SIZE, SIZE), Image.LANCZOS, centering=(0.5, 0.4))
    out = io.BytesIO()
    img.save(out, *(("PNG",) if clear else ("JPEG",)), optimize=True, **({} if clear else {"quality": 85}))  # a new file: no EXIF, no location
    remove(mid)  # the old picture may have the other extension
    p = _dir() / f"{mid}.{'png' if clear else 'jpg'}"
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(out.getvalue())
    tmp.chmod(0o600)
    tmp.replace(p)


def remove(mid: str) -> bool:
    found = False
    for p in (_dir() / f"{mid}.jpg", _dir() / f"{mid}.png"):
        if p.exists():
            p.unlink()
            found = True
    return found


def url(mid: str | None) -> str | None:
    """The picture's address, changing whenever the picture does (so browsers can cache it)."""
    if not mid:
        return None
    p = path(mid)
    if not p.exists():
        return None
    return f"/avatars/{p.name}?v={int(p.stat().st_mtime)}"


def hue(mid: str) -> int:
    """A steady colour per member for the initials circle."""
    return int(hashlib.sha1(mid.encode()).hexdigest()[:4], 16) % 360
