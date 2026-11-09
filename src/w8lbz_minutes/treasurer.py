"""Treasurer: what each member owes and has paid, and the club's bills (bylaws VII). Not a bookkeeping system.

State lives in treasurer.json in the data directory; every change is appended to treasurer-log.jsonl.
- entries: one money movement each, in whole cents, always with an account, so a real ledger (or an export
  for QuickBooks) can be built from them later without re-keying anything:
  {id, date, kind, member, cents, account, note, bill, by, at}
  kind: dues, initiation, chow (payments from a member), certificate (a Certificate of Indebtedness bought),
  adjustment (a charge to a member, or a credit when negative), expense (money out; member is None).
- bills: "Bills and Communications" items with a $ amount, keyed by meeting and text: {key: {status, entry}}.
  status "paid" (an expense entry) or "dismissed" (not a bill).

What a member owes is worked out, never stored: $3.50 for each meeting night since tracking started (or since
they joined), the initiation fee if they joined since then, $35 for each chow night marked missed, plus
adjustments, minus payments. Paying ahead (monthly, yearly) shows as "paid through" a date. Amounts come from
`treasurer:` under `club:` in club.yaml, the bylaws' by default; each can be a dated list when the bylaws change it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import chow
from .club import DATA, Club

PAYMENTS = ("dues", "initiation", "chow", "certificate")
KINDS = PAYMENTS + ("adjustment", "expense")
LABELS = {"dues": "Dues", "initiation": "Initiation fee", "chow": "Missed-chow charge", "certificate": "Certificate of Indebtedness",
          "adjustment": "Adjustment", "expense": "Expense"}
BYLAWS = {"dues_weekly": 3.50, "initiation": 25, "certificate": 100, "certificate_years": 2, "missed_chow": 35, "arrears_weeks": 4,
          "accounts": ["Checking", "Cash"]}
AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{2})?)")


def config(club: Club) -> dict:
    """club.yaml `club: treasurer:`; `start` (when tracking began) must be set before anything is owed."""
    c = {**BYLAWS, **(club.info.get("treasurer") or {})}
    c["start"] = str(c["start"]) if c.get("start") else None
    return c


def cents(v) -> int:
    """'12.50', '$1,200', 12.5 -> cents. Raises ValueError for anything else."""
    s = str(v).replace("$", "").replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d{1,2})?", s):
        raise ValueError("That isn't an amount.")
    whole, _, frac = s.lstrip("-").partition(".")
    n = int(whole) * 100 + int((frac + "00")[:2])
    return -n if s.startswith("-") else n


def at(v, day: str) -> int:
    """An amount from the config, in cents: a number, or a list of `{from: YYYY-MM-DD, amount: N}` when the bylaws
    change it (the amount in effect on that day; before the first `from`, the first amount)."""
    if isinstance(v, list):
        steps = sorted(v, key=lambda x: str(x["from"]))
        v = ([x for x in steps if str(x["from"]) <= day] or steps[:1])[-1]["amount"]
    return cents(v)


def money(c: int) -> str:
    return f"{'−' if c < 0 else ''}${abs(c) // 100:,}.{abs(c) % 100:02d}"


def _path() -> Path:
    return DATA / "treasurer.json"


def load() -> dict:
    p = _path()
    s = json.loads(p.read_text()) if p.exists() else {}
    s.setdefault("entries", [])
    s.setdefault("bills", {})
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
           note: str = "", bill: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError("Pick what the money is for.")
    c = cents(amount)
    if kind != "adjustment" and c <= 0:
        raise ValueError("The amount must be more than zero (use an adjustment for a credit).")
    dt.date.fromisoformat(date)  # ValueError if it isn't a date
    if kind == "expense":
        member = None
    elif member not in club.members:
        raise ValueError("Pick a member.")
    if account not in config(club)["accounts"]:
        raise ValueError("Pick an account.")
    e = {"id": secrets.token_hex(5), "date": date, "kind": kind, "member": member, "cents": c, "account": account,
         "note": note.strip()[:300], "bill": bill, "by": by, "at": int(time.time())}
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
    if undo:
        s["bills"].pop(key, None)
    else:
        s["bills"][key] = {"status": "dismissed"}
    _log("undismiss" if undo else "dismiss", by, bill=key)


def bills(archive, s: dict, start: str | None) -> list[dict]:
    """Every "Bills and Communications" item with a $ amount in minutes since tracking started, newest first."""
    out = []
    for m in archive.meetings:
        if m.status == "draft" or not start or m.slug < start:
            continue
        for it in m.items("Bills and Communications"):
            found = AMOUNT.search(it.text)
            if found:
                key = f"{m.slug}:{hashlib.sha1(it.text.encode()).hexdigest()[:8]}"
                out.append({"key": key, "meeting": m.slug, "text": it.text, "cents": cents(found.group(1)),
                            **s["bills"].get(key, {"status": "waiting"})})
    return out


def standing(club: Club, s: dict, mid: str, today: dt.date | None = None, nights: dict | None = None) -> dict | None:
    """What one member owes. None when tracking hasn't started (no `start`) or they aren't an active member."""
    c, today = config(club), today or dt.date.today()
    m = club.members.get(mid)
    if not c["start"] or not m or m.status.lower() in ("inactive", "silent key"):
        return None
    start, joined = max(c["start"], m.member_since or ""), m.member_since or c["start"]
    weekly = at(c["dues_weekly"], today.isoformat())
    # Every meeting night accrues dues, holidays included: 52 x $3.50 is the bylaws' $182 a year.
    nights_owed = chow.meeting_nights(club, dt.date.fromisoformat(start), today)
    missed = sorted(d for d, x in (nights if nights is not None else chow.load()["nights"]).items()
                    if x.get("member") == mid and x.get("status") == "missed" and start <= d <= today.isoformat())
    mine = [e for e in s["entries"] if e["member"] == mid]
    charged = (sum(at(c["dues_weekly"], d.isoformat()) for d in nights_owed) + sum(at(c["missed_chow"], d) for d in missed)
               + (at(c["initiation"], joined) if m.member_since and m.member_since >= c["start"] else 0)
               + sum(e["cents"] for e in mine if e["kind"] == "adjustment"))
    paid = sum(e["cents"] for e in mine if e["kind"] in PAYMENTS and e["kind"] != "certificate")
    owed = charged - paid
    # the certificate's price when they joined: a later rise doesn't ask existing holders for more
    cert = sum(e["cents"] for e in mine if e["kind"] == "certificate") >= at(c["certificate"], joined)
    cert_due = None
    if not cert and m.member_since:
        j = dt.date.fromisoformat(m.member_since)
        cert_due = j.replace(year=j.year + int(c["certificate_years"]), day=28 if (j.month, j.day) == (2, 29) else j.day)
    ahead = -owed // weekly if owed < 0 else 0  # whole meeting nights already paid for (monthly or yearly payers)
    later = chow.meeting_nights(club, today + dt.timedelta(days=1), today + dt.timedelta(weeks=ahead + 1))
    return {"member": mid, "owed": owed, "weeks_behind": -(-owed // weekly) if owed > 0 else 0,
            "behind": owed >= int(c["arrears_weeks"]) * weekly, "missed_chow": missed, "certificate": cert,
            "paid_through": later[ahead - 1].isoformat() if ahead else None,
            "certificate_due": cert_due.isoformat() if cert_due else None,
            "certificate_late": bool(cert_due and cert_due < today)}


def standings(club: Club, s: dict, today: dt.date | None = None) -> list[dict]:
    nights = chow.load()["nights"]
    out = [standing(club, s, mid, today, nights) for mid in club.members]
    return sorted((x for x in out if x), key=lambda x: (-x["owed"], club.members[x["member"]].last))
