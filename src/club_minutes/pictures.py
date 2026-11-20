"""Pictures on community reports, business items and comments. Each upload is checked like a profile
picture (size, real picture, not a decompression bomb), shrunk to at most 1600 px and saved anew (no EXIF: no phone
location), with an 800 px copy for thumbnails and the PDF. Stored once under a random id; a report, item or comment
lists the ids it shows (at most 4), so corrected minutes' old versions and business items carried week to week share
the files. Served to signed-in members only (/pictures/<id>.jpg), never public.

shortcut: a picture uploaded in the console but never saved with a report stays on disk; prune unreferenced files if
the folder ever grows (they're in the backups meanwhile). A picture removed from an item or comment is deleted at once
(web.py), unless something else still names it.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import secrets
import threading

from . import limits
from .club import DATA, inside

MAX_EACH = 4  # pictures on one report, item or comment
PER_DAY, MAX_PER_DAY = 20, 500  # pictures one member may add in a day (Site -> Features: club.pictures_per_day)
BIG, SMALL = 1600, 800
ID = re.compile(r"[0-9a-f]{16}")  # used with fullmatch
FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}  # MPO: some phones' JPEGs


def _dir():
    d = DATA / "pictures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path(pid: str, small: bool = False):
    if not ID.fullmatch(pid or ""):
        raise ValueError("No such picture.")
    return inside(_dir(), f"{pid}{'-s' if small else ''}.jpg")


def exists(pid: str) -> bool:
    return bool(ID.fullmatch(pid or "")) and path(pid).exists()


def ids(values) -> list[str]:
    """The picture ids a form or the console sent: real ones only, each once, at most MAX_EACH."""
    out = [p for p in dict.fromkeys(str(v) for v in (values or []) if isinstance(v, str)) if exists(p)]
    return out[:MAX_EACH]


_added: dict[tuple[str, str], int] = {}  # (member, day): pictures added
_lock = threading.Lock()


def per_day(club) -> int:
    n = club.info.get("pictures_per_day")
    return n if isinstance(n, int) and 1 <= n <= MAX_PER_DAY else PER_DAY


def take(mid: str, n: int, club) -> None:
    """Count n more pictures for a member today; ValueError past the club's daily cap (Site -> Features).
    shortcut: counted in memory, so a restart starts the day's count again; keep it in a file if that's ever abused."""
    if not n:
        return
    day, cap = dt.date.today().isoformat(), per_day(club)
    with _lock:
        for k in [k for k in _added if k[1] != day]:
            del _added[k]
        if _added.get((mid, day), 0) + n > cap:
            raise ValueError(f"That's more than {cap} pictures today, the most a member can add in a day. Try again tomorrow.")
        _added[(mid, day)] = _added.get((mid, day), 0) + n


def delete(pid: str) -> None:
    for small in (False, True):
        path(pid, small).unlink(missing_ok=True)


def save(data: bytes) -> str:
    """Check, shrink and store a picture; returns its id. Raises ValueError with a message for the member."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data:
        raise ValueError("Pick a picture first.")
    if len(data) > limits.max_upload():
        raise ValueError(f"That picture is too big ({limits.upload_mb()} MB at most).")
    unreadable = ValueError("That file isn't a picture we can read. Use a JPEG, PNG or WebP.")
    try:
        img = Image.open(io.BytesIO(data))
        fmt, px = img.format, img.width * img.height
    except Image.DecompressionBombError:
        raise ValueError("That picture is too big.")
    except (UnidentifiedImageError, OSError, ValueError):
        raise unreadable
    if fmt not in FORMATS:
        raise ValueError("Use a JPEG, PNG or WebP picture.")
    if px > 60_000_000:
        raise ValueError("That picture is too big.")
    try:  # only our own messages reach the member: anything Pillow raises from here is "can't read"
        if fmt in ("JPEG", "MPO"):
            img.draft("RGB", (BIG, BIG))  # decode a big photo at a smaller scale: far less memory
        img.load()  # decode it all now: a damaged or cut-off file fails here
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:  # see-through parts (a screenshot): on white
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").getchannel("A"))
            img = bg
        img = img.convert("RGB")
    except Image.DecompressionBombError:
        raise ValueError("That picture is too big.")
    except (OSError, ValueError, SyntaxError):
        raise unreadable
    pid = secrets.token_hex(8)
    for size, small in ((BIG, False), (SMALL, True)):
        copy = img.copy()
        copy.thumbnail((size, size), Image.LANCZOS)
        out = io.BytesIO()
        copy.save(out, "JPEG", quality=85, optimize=True)  # a new file: no EXIF, no location
        p = path(pid, small)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(out.getvalue())
        tmp.chmod(0o600)
        tmp.replace(p)
    return pid


def src(p: str, pdf: bool = False) -> str:
    """The 800 px copy for a template: on a page, its members-only address; in a PDF, the file."""
    return path(p, True).as_uri() if pdf else f"/pictures/{p}-s.jpg"
