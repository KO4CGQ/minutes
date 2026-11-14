"""The largest upload (a profile picture, a calendar's .ics file), set by an admin on Security -> Server.

Kept in limits.json in the data directory. Bigger requests are turned away before they're read (web.py's
upload_limit), so a huge upload can't fill the disk.
"""
from __future__ import annotations

import json
import os

from .club import DATA

DEFAULT_MB, MIN_MB, MAX_MB = 10, 1, 100
FORM_ROOM = 1024 * 1024  # what a form adds around the file (field names, boundaries)


def upload_mb() -> int:
    p = DATA / "limits.json"
    try:
        n = int(json.loads(p.read_text()).get("upload_mb", DEFAULT_MB)) if p.exists() else DEFAULT_MB
    except (ValueError, OSError, AttributeError):
        return DEFAULT_MB
    return min(max(n, MIN_MB), MAX_MB)


def max_upload() -> int:
    """In bytes."""
    return upload_mb() * 1024 * 1024


def set_upload_mb(n: int) -> int:
    if not MIN_MB <= n <= MAX_MB:
        raise ValueError(f"Pick {MIN_MB} to {MAX_MB} MB.")
    p = DATA / "limits.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"upload_mb": n}))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return n
