"""Holidays and election days by region, for the calendars. club.yaml picks them (none by default):

    calendar:
      holidays: us        # a file here: us.py
      elections: us-oh    # us_oh.py

A region is one small file: `HOLIDAYS` (a list of (name, month, day), where day is a number or (weekday, nth):
Monday 0, nth -1 for the last) and/or `elections(lo, hi)` returning [(date, name, notes)]. To add a state or a
country, copy one and change its rules.
"""
from __future__ import annotations

import datetime as dt
import importlib
import re


def _module(code: str | None):
    """The region file for a code like "us" or "us-oh"; None for none. A code with no file is a config mistake."""
    if not code:
        return None
    if not re.fullmatch(r"[a-z]{2}(-[a-z0-9]{1,3})?", str(code)):
        raise ValueError(f"club.yaml calendar: {code!r} isn't a region code (like us or us-oh)")
    try:
        return importlib.import_module(f"{__name__}.{code.replace('-', '_')}")
    except ModuleNotFoundError:
        raise ValueError(f"club.yaml calendar: no region {code!r} (the regions are the files in club_minutes/regions/)")


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The nth weekday (Monday 0) of a month; n = -1 for the last."""
    from ..events import _nth_weekday
    return _nth_weekday(year, month, weekday, n, n == -1)


def holidays(code: str | None, lo: dt.date, hi: dt.date) -> list[tuple[dt.date, str]]:
    m = _module(code)
    out = []
    for year in range(lo.year, hi.year + 1):
        for name, month, day in getattr(m, "HOLIDAYS", []):
            d = dt.date(year, month, day) if isinstance(day, int) else nth_weekday(year, month, *day)
            if lo <= d <= hi:
                out.append((d, name))
    return sorted(out)


def elections(code: str | None, lo: dt.date, hi: dt.date) -> list[tuple[dt.date, str, str]]:
    m = _module(code)
    return m.elections(lo, hi) if m and hasattr(m, "elections") else []
