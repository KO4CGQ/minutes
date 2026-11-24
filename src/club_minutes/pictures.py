"""Pictures on community reports, business items and comments. Each upload is checked like a profile
picture (size, real picture, not a decompression bomb), shrunk to at most 1600 px and saved anew (no EXIF: no phone
location), with an 800 px copy for thumbnails and the PDF. Stored once under a random id; a report, item or comment
lists the ids it shows (at most 4), so corrected minutes' old versions and business items carried week to week share
the files. Served to signed-in members only (/pictures/<id>.jpg), never public.

A picture removed from an item or comment is deleted at once (web.py), unless something else still names it; the
daily job deletes the ones nothing names (uploaded in the console, never saved) once they're a day old.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import secrets
import shutil
import threading
import time

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


def space() -> tuple[int, int]:
    """(bytes the pictures take, bytes free on the disk holding the data folder), for Admin -> Security -> Uploads."""
    with os.scandir(_dir()) as it:
        return sum(e.stat().st_size for e in it if e.is_file()), shutil.disk_usage(DATA).free


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


def unused(archive, pids) -> list[str]:
    """Those of these ids that nothing saved names: the business state, minutes, earlier versions, console drafts.
    A version hidden by a private correction doesn't count (its pictures may go). Call under business.locked().
    No business.json: nothing is unused (a partial restore must not delete the items' pictures)."""
    from . import business, corrections
    if not (DATA / "business.json").exists():
        return []

    def counts(f) -> bool:
        v = re.fullmatch(r"minutes\.v(\d+)\.md", f.name)
        m = archive.by_slug.get(f.parent.name) if v else None
        return not (m and corrections.hidden_version(m, int(v.group(1))))
    used = json.dumps(business.load()) + "".join(
        f.read_text(errors="replace") for f in archive.root.rglob("*") if f.suffix in (".md", ".json") and f.is_file() and counts(f))
    return [p for p in pids if p not in used]


def prune_unused(archive) -> int:
    """The daily job: delete pictures nothing names and stray or crashed-save files, all over a day old (a new upload
    may be a console draft not saved yet). Returns how many pictures went."""
    from . import business
    if not archive.root.is_dir():  # no minutes folder to check against: delete nothing rather than everything
        return 0
    with business.locked():
        return _prune(archive)


def _prune(archive) -> int:
    old, fresh = {}, set()
    for f in _dir().iterdir():
        if not f.is_file():
            continue
        m = re.fullmatch(r"([0-9a-f]{16})(-s)?\.jpg", f.name)
        young = time.time() - f.stat().st_mtime < 86400
        if not m:
            if not young:
                f.unlink(missing_ok=True)  # .tmp from a crash, or anything else that isn't a picture
        elif young:
            fresh.add(m.group(1))
        else:
            old[m.group(1)] = 1
    gone = unused(archive, [p for p in old if p not in fresh])
    for p in gone:
        delete(p)
    return len(gone)


_decoding = threading.Semaphore(2)  # a small file can decode to hundreds of MB: two at a time


def save(data: bytes) -> str:
    with _decoding:
        return _save(data)


def _save(data: bytes) -> str:
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
    if px > 25_000_000:
        raise ValueError("That picture is too big.")
    try:  # only our own messages reach the member: anything Pillow raises from here is "can't read"
        if fmt in ("JPEG", "MPO"):
            img.draft("RGB", (BIG, BIG))  # decode a big photo at a smaller scale: far less memory
        img.load()  # decode it all now: a damaged or cut-off file fails here
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:  # see-through parts (a screenshot): on white
            rgba = img.convert("RGBA")
            img = Image.new("RGB", img.size, "white")
            img.paste(rgba, mask=rgba.getchannel("A"))
        img = img.convert("RGB")  # a no-op copy for the RGBA path: small next to the decode
    except Image.DecompressionBombError:
        raise ValueError("That picture is too big.")
    except (OSError, ValueError, SyntaxError):
        raise unreadable
    pid = secrets.token_hex(8)
    for size, small in ((SMALL, True), (BIG, False)):  # the big one last: exists() says yes only for a whole picture
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
