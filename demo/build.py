"""Build the demo club's data folder through the app's own code: `uv run python demo/build.py OUTDIR`.

OUTDIR gets club.yaml and data/ (roster, offices, minutes, business, chow, calendar), dated around today so the
pages always look current. Everyone is invented (see demo/club.yaml). Run it in a new or empty folder: it refuses any other.
"""
import datetime as dt
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-out").resolve()
if OUT.exists() and any(OUT.iterdir()):  # never next to (or over) a real club's data
    raise SystemExit(f"{OUT} isn't empty: give a new or empty folder.")
(OUT / "data").mkdir(parents=True)
shutil.copy(HERE / "club.yaml", OUT / "club.yaml")
os.environ.update(CLUB_MINUTES_DATA=str(OUT / "data"), CLUB_MINUTES_CONFIG=str(OUT / "club.yaml"),
                  CLUB_MINUTES_PREVIEW="demo")  # email goes to the outbox, never out

from club_minutes import (
    business,
    chow,
    events,
    membership,
    offices,
    profile,
    roster,
    treasurer,
)
from club_minutes.club import MEETINGS, Club
from club_minutes.meeting import Item, Meeting

PEOPLE = [  # callsign, first, last, license class; office (or None)
    ("N0JANE", "Jane", "Doe", "Amateur Extra", "President"), ("K0JOHN", "John", "Doe", "General", "Vice President"),
    ("W0ALEX", "Alex", "Rivera", "Amateur Extra", "Secretary"), ("N0SAMS", "Sam", "Taylor", "General", "Treasurer"),
    ("K0PATS", "Pat", "Morgan", "Technician", None), ("W0CHRS", "Chris", "Lee", "General", None),
    ("N0ROBN", "Robin", "Park", "Technician", None), ("K0DANA", "Dana", "Brooks", "Amateur Extra", None),
    ("W0JESS", "Jess", "Quinn", "Technician", None), ("N0TERY", "Terry", "Cole", "General", None),
    ("K0MAXX", "Max", "Ellis", "Technician", None), ("W0LEEE", "Lee", "Fox", "General", None),
]
today = dt.date.today()
club = Club()
sheet = roster.LocalSheet(roster._local())
sheet.save({"Member Roster": [roster.PreviewSheet.HEADER], "Membership Log": [roster.PreviewSheet.LOG_HEADER]})
for i, (call, first, last, cls, _) in enumerate(PEOPLE):
    joined = today - dt.timedelta(weeks=8 if call == "K0MAXX" else 400 // 7 + i)  # Max is new: he owes the initiation fee
    membership.add_member(club, {"callsign": call, "first": first, "last": last, "email": f"{first.lower()}.{last.lower()}@example.org",
                                 "phone": f"555-01{i:02d}", "license_class": cls, "joined": joined.isoformat()}, by="demo")
ids = {call: club.canonical(call.lower()) for call, *_ in PEOPLE}
start = (today - dt.timedelta(days=400)).isoformat()
for call, *_, office in PEOPLE:
    if office:
        offices.change(club, office, None, ids[call], start, by=ids["N0JANE"])
club.refresh()
J, JO, A, S = (ids[c] for c in ("N0JANE", "K0JOHN", "W0ALEX", "N0SAMS"))

# Minutes: the last three meeting nights, published.
wd = club.meeting_weekday
last = today - dt.timedelta(days=(today.weekday() - wd) % 7 or 7)
nights = [last - dt.timedelta(weeks=k) for k in (2, 1, 0)]
everyone = list(ids.values())
for n, d in enumerate(nights):
    m = Meeting(date=d, status="published", leader=J, recorder=A, in_person=everyone[: 9 - n], remote=everyone[9 - n: 11 - n],
                guests=[{"name": "Casey Guest", "callsign": "", "notes": "first visit"}] if n == 1 else [])
    m.sections = {
        "Bills and Communications": [Item("Repeater site rent, $50 (paid)."), Item("Thank-you card from the county fair for the radio demo.")],
        "Financial Report": [Item(f"Balance ${1250 + 35 * n:,}.00; dues coming in for next year.")],
        "Community Reports": [Item("Worked a new country on 20 m FT8.", member=ids["W0CHRS"]),
                              Item("Finished the antenna tuner kit.", member=ids["K0PATS"])],
        "Old Business": [Item("Field day site: the park is booked.")],
        "New Business": [Item("Start a Tuesday night beginners' net.")] if n == 2 else [],
        "Upcoming Events": [Item("Fox hunt, meet at the park", when=(d + dt.timedelta(days=9)).isoformat())] if n == 2 else [],
    }
    m.save(MEETINGS)

# Two members share their birthday with the club (on the Birthdays calendar and at that week's meeting).
for call, days in (("K0DANA", 6), ("W0CHRS", 20)):
    profile.change(club, ids[call], {"birthday": (today + dt.timedelta(days=days)).strftime("%m-%d"), "share": "birthday"}, by=ids[call])

# Business: open items with owners and dates, and one done.
s = business.load()
business.add(s, "Order new club patches", J, owner=S, due=(today + dt.timedelta(days=10)).isoformat(), opened=nights[0].isoformat())
business.add(s, "Plan the winter antenna workshop", J, owner=ids["K0DANA"], opened=nights[1].isoformat())
business.add(s, "Update the repeater's ID message", J, owner=JO, due=(today - dt.timedelta(days=3)).isoformat(), opened=nights[0].isoformat())
done = business.add(s, "Renew the club's insurance", J, owner=S, opened=nights[0].isoformat())
business.close(s, done["id"], S, nights[2].isoformat())
business.save(s)

# Chow: everyone in the rotation.
c = chow.load()
for mid in everyone:
    chow.add_to_rotation(club, c, mid, "demo")
chow.generate(club, c, today + dt.timedelta(weeks=12), ids["W0ALEX"], today=nights[0])  # the Secretary keeps it
chow.record(club, c, nights[0].isoformat(), "provided", ids["W0ALEX"], note="Chili and cornbread")
chow.record(club, c, nights[1].isoformat(), "missed", ids["W0ALEX"])  # the Treasurer's missed-chow charge
chow.record(club, c, nights[2].isoformat(), "covered", ids["W0ALEX"], covered_by=ids["W0CHRS"], note="Sandwiches and cookies")
chow.save(c)

# Treasurer: tracking On for half a year. Most members paid up, two behind, a new member; a year of the club's bills.
with treasurer.locked():
    t = treasurer.load()
    tstart = today - dt.timedelta(weeks=26)
    treasurer.set_general(t, S, "on", tstart.isoformat(), 4, 2, True)
    weeks_paid = {"N0JANE": 30, "K0PATS": 19, "N0TERY": 15, "K0MAXX": 4}  # the rest: up to date (26 weeks)
    for call, mid in ids.items():
        weeks = weeks_paid.get(call, 26)
        first = max(tstart, today - dt.timedelta(weeks=8)) if call == "K0MAXX" else tstart
        if call == "K0MAXX":
            treasurer.record(club, t, S, "initiation", "25", first.isoformat(), "Cash", member=mid)
        for half in (0, 1):  # paid in two lots
            n = weeks // 2 + (weeks % 2) * half
            treasurer.record(club, t, S, "dues", f"{3.5 * n:.2f}", min(first + dt.timedelta(weeks=13 * half), today).isoformat(), "Cash", member=mid)
    reg = {payee: treasurer.add_regular(t, S, payee, amount, every, month)["id"]  # what the club pays every month or year
           for payee, amount, every, month in (("Electric bill", "60", "month", None), ("Internet and phone", "64.99", "month", None),
                                               ("Repeater site rent", "50", "month", None),
                                               ("Club insurance, yearly", "420", "year", (today - dt.timedelta(days=40)).month))}
    for k in range(12):  # the monthly bills, a year back
        d = (today.replace(day=1) - dt.timedelta(days=30 * k)).replace(day=5)
        if d > today:
            continue
        for note, amount in (("Electric bill", f"{48 + (k * 7) % 30}.{k:02d}"), ("Internet and phone", "64.99"), ("Repeater site rent", "50")):
            treasurer.record(club, t, S, "expense", amount, d.isoformat(), "Checking", note=note, regular=reg[note])
    for days, note, amount in ((40, "Club insurance, yearly", "420"), (75, "Coax and connectors for the shack", "126.40"),
                               (150, "Pizza for Field Day", "86.50"), (200, "Antenna parts", "64.15")):
        treasurer.record(club, t, S, "expense", amount, (today - dt.timedelta(days=days)).isoformat(), "Checking", note=note, regular=reg.get(note))
    for call, mid in ids.items():  # the bond: long-time members bought it years ago (on file), Pat this year, Max not yet
        if call == "K0PATS":
            treasurer.record(club, t, S, "certificate", "100", (tstart + dt.timedelta(weeks=3)).isoformat(), "Checking", member=mid)
        elif call != "K0MAXX":
            treasurer.record(club, t, S, "certificate", "100", club.members[mid].member_since or tstart.isoformat(), "", member=mid)
    treasurer.record(club, t, S, "income", "38", (today - dt.timedelta(days=20)).isoformat(), "Cash", note="Drink sales")
    treasurer.save(t)

# Calendar: a few events around the weekly meeting (added by the app itself on first start).
evs = events.load()
for days, title, where, t, rep in ((5, "Beginners' net", "Club repeater 146.520", "20:00", "weekly"),
                                   (12, "VE testing", "Anytown Library, 123 Main St", "10:00", ""),
                                   (26, "Antenna workshop", "Club shack", "13:00", "")):
    d = today + dt.timedelta(days=days)
    events.add(evs, "events", events.from_form({"title": title, "where": where, "date": d.isoformat(), "time": t,
                                                "end_time": f"{int(t[:2]) + 1}:{t[3:]}", "repeat": rep}, club.timezone), "demo")
for cal, days, title, where in (("planning", 33, "Annual meeting and election of officers", "Club shack"),
                                ("community", 16, "Anytown Hamfest", "Anytown Fairgrounds")):
    events.add(evs, cal, events.from_form({"title": title, "where": where, "date": (today + dt.timedelta(days=days)).isoformat()}, club.timezone), "demo")
events.save(evs)
# Preview mode kept the roster in preview-sheet.json: make it the club's own, as in a real install.
(OUT / "data" / "preview-sheet.json").replace(roster._local())
(OUT / "data" / "preview-outbox.jsonl").unlink(missing_ok=True)
print(f"Demo club in {OUT}: CLUB_MINUTES_CONFIG={OUT}/club.yaml CLUB_MINUTES_DATA={OUT}/data club-minutes serve")
