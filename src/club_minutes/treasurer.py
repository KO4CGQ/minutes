"""Treasurer: what each member owes and has paid, and the club's bills. Not a bookkeeping system.

State lives in treasurer.json in the data directory; every change is appended to treasurer-log.jsonl.
- settings: set by the Treasurer on the page, never in club.yaml: mode (off; preview: officers see it all, members
  see nothing and no emails go out; on), start (the date tracking starts), dated amounts, accounts, funds, alerts.
- entries: one money movement each, in whole cents, always with an account, so a real ledger (or an export
  for QuickBooks) can be built from them later without re-keying anything:
  {id, date, kind, member, cents, account, note, bill, regular, by, at}
  kind: dues, initiation, chow (payments from a member), certificate (the bond, a Certificate of Indebtedness),
  adjustment (a charge to a member, or a credit when negative), income and expense (no member).
- bills: amounts read under "Bills and Communications", keyed by meeting, text and position: {key: {status, entry}}.
- regular: bills the club pays every month or year. alerted: {member: date} for the arrears emails.

What a member owes is worked out, never stored: the weekly dues for each meeting night since tracking started (or
since they joined), the initiation fee if they joined since then, a charge for each chow night marked missed, plus
adjustments, minus payments. Each amount has dates it takes effect, so a bylaws change doesn't rewrite the past.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from . import chow
from .club import DATA, Club, show_date

PAYMENTS = ("dues", "initiation", "chow", "certificate")
KINDS = PAYMENTS + ("adjustment", "income", "expense")
NO_MEMBER = ("income", "expense")  # money in or out of the club as a whole
def labels(club: Club) -> dict[str, str]:
    """LABELS with the club's name for its certificate."""
    return {**LABELS, **({"certificate": club.certificate["name"]} if club.certificate else {})}


def certificate_note(club: Club) -> str:
    """The note club.yaml gives on what the certificate is, with its bylaws section if given."""
    c = club.certificate
    return (c["note"] + (f"{club.cite('certificate')}." if club.cite("certificate") else "")).strip() if c and c["note"] else ""


LABELS = {"dues": "Dues", "initiation": "Initiation fee", "chow": "Missed-chow charge", "certificate": "Certificate",
          "adjustment": "Adjustment", "income": "Other income", "expense": "Expense"}
RATES = {"dues_weekly": "Dues, each meeting night", "initiation": "Initiation fee", "certificate": "Certificate", "missed_chow": "Missed chow night"}
# Where the club's money goes, for the Spending page; each expense has one (guessed from its words, the Treasurer can change it).
MAX_CATEGORIES = 8  # the chart's tested colours; a ninth would need a colour nobody checked
CATEGORIES = {"utilities": "Utilities", "insurance": "Insurance", "internet": "Internet and phone", "food": "Food",
              "equipment": "Equipment and supplies", "other": "Other"}
GUESSES = (("utilities", r"\bgas\b|\belec|edison|water|sewer"), ("insurance", r"insur"),
           ("internet", r"internet|phone|credit card|blink"), ("food", r"pizza|food|chow|drink|soda|\bpop\b"),
           ("equipment", r"radio|antenna|rope|pull[ei]|\bups\b|bulb|\bkeys?\b|tool|part|cable|coax|print|shirt"))
MODES = {"off": "Off", "preview": "Preview: officers only", "on": "On: members see their own figures"}
# the club's amounts; in cents, each a list of {from, cents} so a change takes effect on a date
DEFAULTS = {"mode": "off", "start": None, "certificate_years": 2, "arrears_weeks": 4, "alerts": False,
            "rates": {"dues_weekly": 350, "initiation": 2500, "certificate": 10000, "missed_chow": 3500},
            "accounts": ["Checking", "Cash"], "funds": [], "categories": [{"key": k, "label": v, "slot": n} for n, (k, v) in enumerate(CATEGORIES.items(), 1)]}
AMOUNTS = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)|(?<![\d$.,])(\d{1,5}\.\d{2})\b")  # "$81", "$1,200.50", "Electric 50.64"


def config(s: dict) -> dict:
    c = {**DEFAULTS, **s.get("settings", {})}
    c["rates"] = {k: (c["rates"].get(k) if isinstance(c["rates"].get(k), list) else [{"from": "2000-01-01", "cents": v}])
                  for k, v in DEFAULTS["rates"].items()}
    return c


def on(s: dict) -> bool:
    """Tracking is running (preview or on) and has a start date."""
    c = config(s)
    return c["mode"] in ("preview", "on") and bool(c["start"])


def members_see(s: dict) -> bool:
    return on(s) and config(s)["mode"] == "on"


def rate(c: dict, kind: str, day: str) -> int:
    """The amount in effect on a day, in cents (before the first change, the first amount)."""
    steps = sorted(c["rates"][kind], key=lambda x: x["from"])
    return ([x for x in steps if x["from"] <= day] or steps[:1])[-1]["cents"]


def categories(s: dict) -> dict[str, str]:
    """The Treasurer's categories, key -> name, in order. Other is always there (it catches the rest)."""
    return {c["key"]: c["label"] for c in config(s)["categories"]}


def colours(s: dict) -> dict[str, int]:
    """Each category's chart colour (1-8), kept for good: removing one never repaints the others."""
    return {c["key"]: c.get("slot", n) for n, c in enumerate(config(s)["categories"], 1)}


def guess(text: str, s: dict | None = None) -> str:
    """The likely category from a bill's words: "Gas Bill $81" -> utilities. Other when nothing matches, or when the
    Treasurer removed the one it would be."""
    c = next((c for c, pat in GUESSES if re.search(pat, text, re.I)), "other")
    return c if s is None or c in categories(s) else "other"


def kept_accounts(club: Club, s: dict, mid: str | None) -> list[str]:
    """The fund accounts a member looks after (a drinks committee, say): they record its money."""
    return [f["account"] for f in config(s)["funds"] if mid and club.canonical(mid) in {club.canonical(k) for k in f["keepers"]}]


@contextmanager
def locked():
    """Held around load-change-save, by the web app and the daily job alike (they're separate processes)."""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "treasurer.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


MAX_CENTS = 100_000_000  # $1,000,000: anything bigger is a typo
BILL_KEY = re.compile(r"\d{4}-\d{2}-\d{2}:[0-9a-f]{8}:\d{1,3}")


def cents(v) -> int:
    """'12.50', '$1,200', 12.5 -> cents. Plain digits only, commas only between thousands. ValueError otherwise."""
    s = str(v).replace("$", "").strip()
    if not re.fullmatch(r"-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?", s):
        raise ValueError("That isn't an amount.")
    whole, _, frac = s.lstrip("-").replace(",", "").partition(".")
    n = int(whole) * 100 + int((frac + "00")[:2])
    if n > MAX_CENTS:
        raise ValueError("That amount is too big: check it.")
    return -n if s.startswith("-") else n


def today() -> dt.date:  # one place for "now", so tests can set it
    return dt.date.today()


def day(v: str, latest: dt.date | None = None) -> str:
    """A date as YYYY-MM-DD, whatever ISO form came in ("20261109" too); not after `latest` when given."""
    try:
        d = dt.date.fromisoformat(str(v).strip())
    except ValueError:
        raise ValueError("That isn't a date.") from None
    if latest and d > latest:
        raise ValueError(f"That date is after {show_date(latest)}.")
    return d.isoformat()


def money(c: int) -> str:
    return f"{'−' if c < 0 else ''}${abs(c) // 100:,}.{abs(c) % 100:02d}"


def _path() -> Path:
    return DATA / "treasurer.json"


def load() -> dict:
    p = _path()
    s = json.loads(p.read_text()) if p.exists() else {}
    s.setdefault("entries", [])
    s.setdefault("bills", {})
    s.setdefault("regular", [])  # bills the club pays every month or year: {id, payee, cents, every, month}
    return s


def save(s: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def _log(action: str, by: str, **details) -> None:
    p = DATA / "treasurer-log.jsonl"
    with p.open("a") as f:
        f.write(json.dumps({"at": int(time.time()), "action": action, "by": by, **details}) + "\n")
    os.chmod(p, 0o600)


def record(club: Club, s: dict, by: str, kind: str, amount: str, date: str, account: str, member: str | None = None,
           note: str = "", bill: str | None = None, regular: str | None = None, category: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError("Pick what the money is for.")
    c = cents(amount)
    if kind != "adjustment" and c <= 0:
        raise ValueError("The amount must be more than zero (use an adjustment for a credit).")
    date = day(date, today() + dt.timedelta(days=1))  # money can't move in the future (a day's slack for time zones)
    if bill and not BILL_KEY.fullmatch(bill):
        raise ValueError("That bill is gone.")
    if kind in NO_MEMBER:
        member = None
    elif member not in club.members:
        raise ValueError("Pick a member.")
    start = config(s)["start"]
    before = bool(start) and date < start
    if before and kind in ("dues", "initiation", "chow"):
        raise ValueError("Nothing was owed before tracking started. For an older balance, use an adjustment dated on the start date.")
    on_file = before and kind == "certificate" and not account  # a bond bought years ago: on record, no money moves now
    if not on_file and account not in config(s)["accounts"]:
        raise ValueError("Pick an account.")
    e = {"id": secrets.token_hex(5), "date": date, "kind": kind, "member": member, "cents": c, "account": None if on_file else account,
         "note": note.strip()[:300], "bill": bill, "by": by, "at": int(time.time())}
    if on_file:
        e["on_file"] = True
    if kind == "expense":
        e["category"] = category if category in categories(s) else guess(note, s)
    if regular:
        if kind != "expense" or not any(r["id"] == regular for r in s["regular"]):
            raise ValueError("That regular bill is gone.")
        e["regular"] = regular
    s["entries"].append(e)
    if bill:
        s["bills"][bill] = {"status": "paid", "entry": e["id"]}
    _log("record", by, entry=e)
    return e


def delete(s: dict, eid: str, by: str) -> None:
    e = next((x for x in s["entries"] if x["id"] == eid), None)
    if not e:
        raise ValueError("That entry is already gone.")
    s["entries"].remove(e)
    if e.get("bill"):
        s["bills"].pop(e["bill"], None)  # the bill is waiting again
    _log("delete", by, entry=e)


def dismiss_bill(s: dict, key: str, by: str, undo: bool = False) -> None:
    if not BILL_KEY.fullmatch(key):
        raise ValueError("That bill is gone.")
    if undo:
        s["bills"].pop(key, None)
    else:
        s["bills"][key] = {"status": "dismissed"}
    _log("undismiss" if undo else "dismiss", by, bill=key)


EVERY = {"month": "Every month", "year": "Every year"}


def add_regular(s: dict, by: str, payee: str, amount: str, every: str, month: int | None = None) -> dict:
    payee = " ".join(payee.split())[:80]
    if not payee or every not in EVERY or (every == "year" and month not in range(1, 13)):
        raise ValueError("Give the payee, and how often (and for a yearly bill, its month).")
    r = {"id": secrets.token_hex(4), "payee": payee, "cents": cents(amount), "every": every, "month": month if every == "year" else None}
    s["regular"].append(r)
    _log("add regular", by, regular=r)
    return r


def remove_regular(s: dict, rid: str, by: str) -> None:
    """Payments already made keep their record; the bill just stops being expected."""
    r = next((x for x in s["regular"] if x["id"] == rid), None)
    if r:
        s["regular"].remove(r)
        _log("remove regular", by, regular=r)


def regular_due(s: dict, today: dt.date) -> list[dict]:
    """Each regular bill for this period (this month; a yearly one from its month to the year's end): paid or not."""
    out = []
    for r in s["regular"]:
        period = today.strftime("%Y-%m") if r["every"] == "month" else str(today.year)
        if r["every"] == "year" and today.month < r["month"]:
            continue
        paid = [e for e in s["entries"] if e.get("regular") == r["id"] and e["date"].startswith(period)]
        out.append({**r, "period": period, "paid": paid})
    return sorted(out, key=lambda r: (bool(r["paid"]), r["payee"].lower()))


def bills_from(s: dict) -> str | None:
    """How far back the Bills tab reads the minutes: the start date, unless the Treasurer chose to look further back."""
    c = config(s)
    return c.get("bills_from") or c["start"]


def set_bills_from(s: dict, by: str, date: str | None) -> None:
    """Read bills from older minutes (a date before the start), or None to go back to the start date. Bills already
    marked paid or not a bill keep that either way."""
    if date:
        date = day(date)
        if config(s)["start"] and date > config(s)["start"]:
            raise ValueError("Pick a date before tracking started; later minutes are read already.")
    _settings(s)["bills_from"] = date or None
    _log("bills from", by, date=date)


def bills(archive, s: dict, start: str | None) -> list[dict]:
    """Each amount read under "Bills and Communications" since `start` (see bills_from), newest first. One bullet often
    holds several ("$99 gas bill $35 Raffle tickets"), each its own bill; a bullet word for word the same as the
    week before is marked `repeated` (copied forward, most likely)."""
    out, ms = [], [m for m in archive.meetings if m.status != "draft"]  # newest first
    for i, m in enumerate(ms):
        if not start or m.slug < start:
            continue
        last_week = {it.text for it in ms[i + 1].items("Bills and Communications")} if i + 1 < len(ms) else set()
        for it in m.items("Bills and Communications"):
            h = hashlib.sha1(it.text.encode()).hexdigest()[:8]
            found_all = list(AMOUNTS.finditer(it.text))
            for n, found in enumerate(found_all):
                key = f"{m.slug}:{h}:{n}"
                # a bullet that starts with an amount names each after it ("$99 gas bill $35 raffle tickets"),
                # otherwise before it ("Gas Bill $81"); the other side only when that one has no words
                after = it.text[found.end():found_all[n + 1].start() if n + 1 < len(found_all) else None]
                before = it.text[found_all[n - 1].end() if n else 0:found.start()]
                label, other = (after, before) if found_all[0].start() == 0 else (before, after)
                label = label if re.search(r"[a-z]", label, re.I) else other
                cat = guess(label, s)
                out.append({"key": key, "meeting": m.slug, "text": it.text, "amount": found.group(0), "category": cat,
                            "label": label.strip(" ,.;:-–/") or it.text, "split": len(found_all) > 1,
                            "cents": cents(found.group(1) or found.group(2)), "repeated": it.text in last_week,
                            **s["bills"].get(key, {"status": "waiting"})})
    return out


def set_note(s: dict, eid: str, note: str, by: str) -> None:
    e = next((x for x in s["entries"] if x["id"] == eid), None)
    if not e:
        raise ValueError("That entry is gone.")
    _log("note", by, entry=eid, old=e["note"], note=note.strip()[:300])
    e["note"] = note.strip()[:300]


def set_category(s: dict, eid: str, category: str, by: str) -> None:
    e = next((x for x in s["entries"] if x["id"] == eid and x["kind"] == "expense"), None)
    if not e or category not in categories(s):
        raise ValueError("Pick a category for an expense.")
    e["category"] = category
    _log("category", by, entry=eid, category=category)


def _spans(m) -> list[tuple[str, str | None]]:
    """When someone was a member: from Joined/Rejoined to Left/Silent Key in the Membership Log, still open if they're
    a member now. Without log rows, from "member since" (or always) while they're a member now."""
    spans, start = [], None
    for e in sorted((e for e in m.events if e.get("date")), key=lambda e: e["date"]):
        if e["event"] in ("Joined", "Rejoined"):
            start = start or e["date"]
        elif e["event"] in ("Left", "Silent Key") and start:
            spans.append((start, e["date"]))
            start = None
    current = m.status.lower() in ("active", "suspended")
    if current and not start and not spans:
        start = m.member_since or "0000"
    if current and start:
        spans.append((start, None))
    # shortcut: someone marked Inactive with no Left row has no known end, so isn't counted; add the row to count them
    return spans


def members_in(club: Club, first: str, last: str) -> int:
    """How many were members at some point between two dates (ISO)."""
    return sum(any(a <= last and (b is None or b >= first) for a, b in _spans(m)) for m in club.members.values())


def spending(s: dict, today: dt.date, months: int = 12, club: Club | None = None) -> dict:
    """Expenses by month and category over the last `months` months (this one included), and the totals. With the
    club, also what that cost per member: each month's spending over the members that month."""
    keys, d = [], today.replace(day=1)
    for _ in range(months):
        keys.insert(0, d.strftime("%Y-%m"))
        d = (d - dt.timedelta(days=1)).replace(day=1)
    cats = categories(s)
    rows = {k: {c: 0 for c in cats} for k in keys}
    for e in s["entries"]:
        if e["kind"] == "expense" and e["date"][:7] in rows:
            c = e.get("category") if e.get("category") in cats else guess(e.get("note") or "", s)
            rows[e["date"][:7]][c] += e["cents"]
    totals = {c: sum(r[c] for r in rows.values()) for c in cats}
    out = {"months": [{"month": k, "by": r, "total": sum(r.values())} for k, r in rows.items()], "totals": totals,
           "total": sum(totals.values()), "top": max([sum(r.values()) for r in rows.values()] + [0])}
    if club:
        for r in out["months"]:
            y, mo = map(int, r["month"].split("-"))
            end = (dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
            r["members"] = members_in(club, f"{r['month']}-01", end)
            r["per_member"] = r["total"] // r["members"] if r["members"] else None
        avg = sum(r["members"] for r in out["months"]) / months
        out["members"] = round(avg)
        out["per_member_year"] = round(out["total"] / avg) if avg else None  # one member's share of the 12 months
        out["per_member_month"] = round(out["total"] / avg / months) if avg else None  # a month's cost per member, on average
        out["dues_week"] = rate(config(s), "dues_weekly", today.isoformat())
        out["dues_month"] = out["dues_week"] * 52 // 12  # what dues bring in a month, per member
    return out


def standing(club: Club, s: dict, mid: str, today: dt.date | None = None, nights: dict | None = None) -> dict | None:
    """What one member owes. None when tracking is off or they aren't a current member (a suspended member still
    owes: dues keep counting while the Board decides)."""
    c, today = config(s), today or dt.date.today()
    m = club.members.get(mid)
    if not on(s) or not m or m.status.lower() in ("inactive", "silent key", "prospect"):
        return None
    start, joined = max(c["start"], m.member_since or ""), m.member_since or c["start"]
    weekly = rate(c, "dues_weekly", today.isoformat())
    # Every meeting night accrues dues, holidays included (a weekly club's year is 52 nights).
    nights_owed = chow.meeting_nights(club, dt.date.fromisoformat(start), today)
    missed = sorted(d for d, x in (nights if nights is not None else chow.load()["nights"]).items()
                    if x.get("member") == mid and x.get("status") == "missed" and start <= d <= today.isoformat()
                    ) if club.has("chow") else []  # the missed-chow fee is part of the chow module
    mine = [e for e in s["entries"] if e["member"] == mid]
    charged = (sum(rate(c, "dues_weekly", d.isoformat()) for d in nights_owed) + sum(rate(c, "missed_chow", d) for d in missed)
               + (rate(c, "initiation", joined) if m.member_since and m.member_since >= c["start"] else 0)
               + sum(e["cents"] for e in mine if e["kind"] == "adjustment"))
    paid = sum(e["cents"] for e in mine if e["kind"] in PAYMENTS and e["kind"] != "certificate")
    owed = charged - paid
    # the bond's price when they joined: a later rise doesn't ask existing holders for more
    bonds = [e for e in mine if e["kind"] == "certificate"]
    # a bond on file from before tracking counts whatever it cost then; otherwise paid in full at the price when they joined
    # no certificate in club.yaml: nothing to buy, so never owed
    cert = not club.certificate or any(e.get("on_file") for e in bonds) or sum(e["cents"] for e in bonds) >= rate(c, "certificate", joined)
    cert_due = None
    if not cert and m.member_since:  # (only with a certificate: cert is True without one)
        j = dt.date.fromisoformat(m.member_since)
        cert_due = j.replace(year=j.year + int(c["certificate_years"]), day=28 if (j.month, j.day) == (2, 29) else j.day)
    ahead = -owed // weekly if owed < 0 else 0  # whole meeting nights already paid for (monthly or yearly payers)
    later = chow.meeting_nights(club, today + dt.timedelta(days=1), today + dt.timedelta(weeks=ahead + 1))
    return {"member": mid, "owed": owed, "weeks_behind": -(-owed // weekly) if owed > 0 else 0,
            "behind": owed >= int(c["arrears_weeks"]) * weekly, "missed_chow": missed, "certificate": cert,
            "certificate_bought": max((e["date"] for e in bonds), default=None) if cert else None, "suspended": m.status.lower() == "suspended", "paid_through": later[ahead - 1].isoformat() if ahead else None,
            "certificate_due": cert_due.isoformat() if cert_due else None,
            "certificate_late": bool(cert_due and cert_due < today)}


def standings(club: Club, s: dict, today: dt.date | None = None) -> list[dict]:
    nights = chow.load()["nights"]
    out = [standing(club, s, mid, today, nights) for mid in club.members]
    return sorted((x for x in out if x), key=lambda x: (-x["owed"], club.members[x["member"]].last))


# ---- settings (the Treasurer, and admins in admin mode) ----

def _settings(s: dict) -> dict:
    s.setdefault("settings", {})
    for k in ("rates", "accounts", "funds", "categories"):  # copy the defaults in before changing them
        s["settings"].setdefault(k, config(s)[k])
    return s["settings"]


def set_general(s: dict, by: str, mode: str, start: str, arrears_weeks: int, certificate_years: int, alerts: bool) -> None:
    if mode not in MODES:
        raise ValueError("Pick off, preview or on.")
    if start:
        start = day(start)
    elif mode != "off":
        raise ValueError("Give the date tracking starts before turning it on.")
    if not (1 <= arrears_weeks <= 52 and 0 <= certificate_years <= 10):
        raise ValueError("Weeks behind: 1 to 52; years to buy the certificate: 0 to 10.")
    new = {"mode": mode, "start": start or None, "arrears_weeks": arrears_weeks, "certificate_years": certificate_years, "alerts": alerts}
    _settings(s).update(new)
    _log("settings", by, **new)


def add_rate(s: dict, by: str, kind: str, start: str, amount: str) -> None:
    """A new amount from a date on (or a correction of the one starting that day)."""
    if kind not in RATES:
        raise ValueError("Pick which amount.")
    start = day(start)
    c = cents(amount)
    if c < 0:
        raise ValueError("The amount can't be negative.")
    steps = [x for x in _settings(s)["rates"][kind] if x["from"] != start] + [{"from": start, "cents": c}]
    s["settings"]["rates"][kind] = sorted(steps, key=lambda x: x["from"])
    _log("rate", by, kind=kind, start=start, cents=c)


def remove_rate(s: dict, by: str, kind: str, start: str) -> None:
    steps = _settings(s)["rates"].get(kind) or []
    if len(steps) < 2:
        raise ValueError("Keep at least one amount.")
    s["settings"]["rates"][kind] = [x for x in steps if x["from"] != start]
    _log("remove rate", by, kind=kind, start=start)


def add_account(s: dict, by: str, name: str) -> None:
    name = " ".join(name.split())[:60]
    if not name or name in _settings(s)["accounts"]:
        raise ValueError("Give the account a new name.")
    s["settings"]["accounts"].append(name)
    _log("add account", by, account=name)


def remove_account(s: dict, by: str, name: str) -> None:
    """Only one nothing uses: entries and funds keep their account."""
    if any(e["account"] == name for e in s["entries"]) or any(f["account"] == name for f in _settings(s)["funds"]):
        raise ValueError(f"{name} has money recorded or a fund: it stays.")
    if len(s["settings"]["accounts"]) < 2:
        raise ValueError("Keep at least one account.")
    s["settings"]["accounts"] = [a for a in s["settings"]["accounts"] if a != name]
    _log("remove account", by, account=name)


def set_fund(s: dict, by: str, name: str, account: str, keepers: list[str]) -> None:
    """A fund a committee runs (the drinks fund): its account, and the members who record its money."""
    name = " ".join(name.split())[:60]
    if not name or account not in _settings(s)["accounts"]:
        raise ValueError("Give the fund a name and pick its account (add the account first).")
    s["settings"]["funds"] = [f for f in s["settings"]["funds"] if f["name"] != name] + [{"name": name, "account": account, "keepers": keepers}]
    _log("fund", by, name=name, account=account, keepers=keepers)


def add_category(s: dict, by: str, label: str) -> None:
    label = " ".join(label.split())[:40]
    cats = _settings(s)["categories"]
    if not label or label.lower() in {c["label"].lower() for c in cats}:
        raise ValueError("Give the category a new name.")
    if len(cats) >= MAX_CATEGORIES:
        raise ValueError(f"{MAX_CATEGORIES} categories at most (the chart has a colour for each): merge two first.")
    key = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "c"
    while key in {c["key"] for c in cats}:
        key += "-2"
    free = min(set(range(1, MAX_CATEGORIES + 1)) - set(colours(s).values()))
    cats.insert(len(cats) - 1, {"key": key, "label": label, "slot": free})  # Other stays last
    _log("add category", by, key=key, label=label)


def rename_category(s: dict, by: str, key: str, label: str) -> None:
    label = " ".join(label.split())[:40]
    c = next((c for c in _settings(s)["categories"] if c["key"] == key), None)
    if not c or not label or label.lower() in {x["label"].lower() for x in s["settings"]["categories"] if x is not c}:
        raise ValueError("Give the category a new name.")
    _log("rename category", by, key=key, old=c["label"], label=label)
    c["label"] = label


def remove_category(s: dict, by: str, key: str) -> int:
    """Its expenses move to Other (nothing is lost); Other itself stays. Returns how many moved."""
    if key == "other":
        raise ValueError("Other stays: it catches whatever fits nowhere else.")
    s["settings"]["categories"] = [c for c in _settings(s)["categories"] if c["key"] != key]
    moved = [e for e in s["entries"] if e.get("category") == key]
    for e in moved:
        e["category"] = "other"
    _log("remove category", by, key=key, moved=len(moved))
    return len(moved)


def remove_fund(s: dict, by: str, name: str) -> None:
    s["settings"]["funds"] = [f for f in _settings(s)["funds"] if f["name"] != name]
    _log("remove fund", by, name=name)


# ---- arrears alerts (the daily job) ----

def arrears_alert(club: Club, s: dict, today: dt.date) -> tuple[list[str], str, str] | None:
    """Members who have just reached the arrears line, for the Treasurer and the Board, once each time
    they cross it. Only with alerts on and tracking on (not in preview). The Board decides; the app never suspends."""
    c = config(s)
    if not (members_see(s) and c["alerts"]):
        return None
    behind = {x["member"]: x for x in standings(club, s, today) if x["behind"] and not x["suspended"]}
    alerted = s.setdefault("alerted", {})
    for mid in list(alerted):
        if mid not in behind:
            del alerted[mid]  # caught up: a later slip alerts again
    new = [x for mid, x in behind.items() if mid not in alerted]
    if not new:
        return None
    for x in new:
        alerted[x["member"]] = today.isoformat()
    to = sorted({m.email for m in club.members.values() if m.email and m.status.lower() == "active"
                 and (m.board or "treasurer" in (m.role or "").lower())})
    lines = "\n".join(f"- {club.name(x['member'])}: {money(x['owed'])}, about {x['weeks_behind']} weeks" for x in new)
    note = " ".join(str(club.bylaws.get("arrears_note") or "").split())  # the club's own rules on what happens next
    body = (f"These members are now {c['arrears_weeks']} or more weeks behind on dues{club.cite('arrears')}:\n\n{lines}\n\n"
            + (f"{note}\n\n" if note else "") + "The app never suspends anyone. If the club suspends a member, record it "
            "on their member page (status Suspended, and a Membership Log row).\n")
    return to, f"Dues: {len(new)} member{'s' if len(new) > 1 else ''} {c['arrears_weeks']}+ weeks behind", body
