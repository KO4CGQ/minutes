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

from club_minutes import business, chow, events, membership, offices, roster
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
    membership.add_member(club, {"callsign": call, "first": first, "last": last, "email": f"{first.lower()}.{last.lower()}@example.org",
                                 "phone": f"555-01{i:02d}", "license_class": cls}, by="demo")
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
for d, food in zip(nights, ("Chili and cornbread", "Pizza", "Sandwiches and cookies")):
    chow.record(club, c, d.isoformat(), "provided", ids["W0ALEX"], note=food)
chow.save(c)

# Calendar: a few events around the weekly meeting (added by the app itself on first start).
evs = events.load()
for days, title, where, t, rep in ((5, "Beginners' net", "Club repeater 146.520", "20:00", "weekly"),
                                   (12, "VE testing", "Anytown Library, 123 Main St", "10:00", ""),
                                   (26, "Antenna workshop", "Club shack", "13:00", "")):
    d = today + dt.timedelta(days=days)
    events.add(evs, "events", events.from_form({"title": title, "where": where, "date": d.isoformat(), "time": t,
                                                "end_time": f"{int(t[:2]) + 1}:{t[3:]}", "repeat": rep}, club.timezone), "demo")
events.save(evs)
# Preview mode kept the roster in preview-sheet.json: make it the club's own, as in a real install.
(OUT / "data" / "preview-sheet.json").replace(roster._local())
(OUT / "data" / "preview-outbox.jsonl").unlink(missing_ok=True)
print(f"Demo club in {OUT}: CLUB_MINUTES_CONFIG={OUT}/club.yaml CLUB_MINUTES_DATA={OUT}/data club-minutes serve")
