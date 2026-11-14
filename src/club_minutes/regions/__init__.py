"""Holidays and election days by region, for the calendars. club.yaml (or Admin → Site → Features) picks them, none by default:

    calendar:
      holidays: us        # a country, or a state or region in it: us-oh, fr, de-by, gb-sct
      extra_holidays:     # the club's own (with or without a country's): a date, or a weekday rule
        - {name: Christmas Eve, month: 12, day: 24}
        - {name: Black Friday, month: 11, weekday: thursday, nth: 4, after: 1}   # the day after Thanksgiving
      elections: us-oh    # us (the general election) or us-oh (Ohio: primaries too)

Holidays come from the `holidays` package (python-holidays: every country it knows, and their states and regions, with
Easter-based days and weekend substitutes), in English where it has English names. Election days: "us" is the US general
election (even years, the same date everywhere); a state with its own rules here has a file (us_oh.py: Ohio's primaries too).
"""
from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache

import holidays as _lib


def _split(code: str) -> tuple[str, str | None]:
    country, _, sub = code.partition("-")
    return country.upper(), (sub.upper() or None)


def check(kind: str, code: str | None) -> None:
    """A region code club.yaml (or the Site page) may use for "holidays" or "elections"; a wrong one stops the app at
    start, saying which."""
    if not code:
        return
    ok = re.fullmatch(r"[a-z]{2}(-[a-z0-9]{1,5})?", str(code)) and code in choices()[kind]
    if not ok:
        raise ValueError(f"club.yaml calendar: {kind}: {code!r} isn't a region code there is (like "
                         + ("us, us-oh, fr, de-by or gb-sct)" if kind == "holidays" else "us or us-oh)"))


WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_extra(items) -> list[dict]:
    """The club's own holidays, each {name, month, and day (a date) or weekday + nth (1-4, or -1 for the last), and
    after: days later (0-30)}, e.g. Black Friday = {month: 11, weekday: thursday, nth: 4, after: 1}. Returned tidied
    (weekday as Monday 0); a wrong one stops the app at start, saying which."""
    out = []
    for x in items or []:
        try:
            name = " ".join(str(x.get("name") or "").split())
            month, after = int(x["month"]), int(x.get("after") or 0)
            wd = x.get("weekday")
            if not 1 <= len(name) <= 60 or not 1 <= month <= 12 or not 0 <= after <= 30:
                raise ValueError
            if wd is None:
                day = int(x["day"])
                dt.date(2000, month, day)  # 2000: Feb 29 is a date
                out.append({"name": name, "month": month, "day": day, "after": after})
            else:
                wd = WEEKDAY_NAMES.index(str(wd).lower()) if str(wd).lower() in WEEKDAY_NAMES else int(wd)
                nth = int(x["nth"])
                if not 0 <= wd <= 6 or nth not in (1, 2, 3, 4, -1):
                    raise ValueError
                out.append({"name": name, "month": month, "weekday": wd, "nth": nth, "after": after})
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError(f"club.yaml calendar: extra_holidays: {x!r} isn't a holiday: a name, a month, and a day "
                             "(a date, or a weekday and which one: 1-4 or -1 for the last), like {name: Christmas Eve, month: 12, day: 24}") from None
    if len(out) > 50:
        raise ValueError("club.yaml calendar: extra_holidays: 50 at most")
    return out


def extra_date(h: dict, year: int) -> dt.date | None:
    """When one of the club's own holidays falls in a year (None: Feb 29 in a year without one)."""
    try:
        d = dt.date(year, h["month"], h["day"]) if "day" in h else nth_weekday(year, h["month"], h["weekday"], h["nth"])
    except ValueError:
        return None
    return d + dt.timedelta(days=h["after"])


def holidays(code: str | None, lo: dt.date, hi: dt.date, extra=()) -> list[tuple[dt.date, str]]:
    """[(date, name)] from lo to hi: the region's official holidays (none without one) and the club's own (extra)."""
    out = []
    if code:
        check("holidays", code)
        country, sub = _split(code)
        days = _lib.country_holidays(country, subdiv=sub, years=range(lo.year, hi.year + 1), language="en_US")
        out = [(d, name) for d, name in days.items() if lo <= d <= hi]
    own = parse_extra(extra)
    for year in range(lo.year - 1, hi.year + 1):  # a year early too: "days after" can carry one into the next year
        for h in own:
            d = extra_date(h, year)
            if d and lo <= d <= hi:
                out.append((d, h["name"]))
    return sorted(out)


def elections(code: str | None, lo: dt.date, hi: dt.date) -> list[tuple[dt.date, str, str]]:
    """[(date, name, notes)] from lo to hi: a state's own rules (its file here), or the US general election ("us")."""
    if not code:
        return []
    check("elections", code)
    if code != "us":  # a state with its own file here
        import importlib
        return importlib.import_module(f"{__name__}.{code.replace('-', '_')}").elections(lo, hi)
    out = []
    for year in range(lo.year, hi.year + 1):
        d = nth_weekday(year, 11, 0, 1) + dt.timedelta(days=1)  # the Tuesday after the first Monday in November
        if year % 2 == 0 and lo <= d <= hi:
            out.append((d, "Election Day (general election)", "Federal and state offices. Your ballot and polling place: vote.gov"))
    return out


@lru_cache(maxsize=1)
def choices() -> dict[str, dict[str, str]]:
    """Every code there is, with what it is: {"holidays": {"us": "United States", "us-oh": "United States: Ohio", ...},
    "elections": {"us": "United States: general election", "us-oh": "Ohio: primaries and general election"}}."""
    from holidays.registry import COUNTRIES
    hol = {}
    for cls, code, *_ in COUNTRIES.values():
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cls)
        try:
            c = _lib.country_holidays(code)
        except NotImplementedError:
            continue
        hol[code.lower()] = name
        names = {v: k for k, v in reversed(list((getattr(c, "subdivisions_aliases", None) or {}).items()))}
        for sub in c.subdivisions:
            if not re.fullmatch(r"[A-Za-z0-9]{1,5}", sub):  # a few are names, not codes ("Flemish"): left out
                continue
            hol[f"{code.lower()}-{sub.lower()}"] = f"{name}: {names.get(sub, sub)}"
    # Elections: the US general election, and each state with a file here (like us_oh.py), named by its NAME
    import importlib
    from pathlib import Path
    ele = {"us": "United States: general election"}
    for f in sorted(Path(__file__).parent.glob("[a-z][a-z]_*.py")):
        ele[f.stem.replace("_", "-")] = importlib.import_module(f"{__name__}.{f.stem}").NAME
    return {"holidays": dict(sorted(hol.items(), key=lambda kv: kv[1])), "elections": ele}


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The nth weekday (Monday 0) of a month; n = -1 for the last."""
    from ..events import _nth_weekday
    return _nth_weekday(year, month, weekday, n, n == -1)
