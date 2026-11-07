"""Give a preview's roster copy made-up contact details, so no real phone numbers, addresses, birthdays
or emails sit in a preview. Names and callsigns stay: the meetings, attendance and chow schedule use them.

    python3 deploy/preview-fake.py PREVIEW_DATA_DIR

Known test records (non-officers, so what you see depends on what they share):
    kd8aaa  Amy Able   shares everything
    kd8bbb Bob Baker    shares nothing
    kd8ccc  Cal Cole  shares phone only
Everyone else shares email and phone. Officers see everything regardless.
"""
import json
import sys
from pathlib import Path

SHARES = {"kd8aaa": ["email", "phone", "address", "birthday"], "kd8bbb": [], "kd8ccc": ["phone"]}

path = Path(sys.argv[1]) / "roster.json"
people = json.loads(path.read_text())
for i, p in enumerate(people, 1):
    p["email"] = f"{p['id']}@example.org"
    p["phone"] = f"419-555-01{i:02d}"
    p["address"] = f"{100 + i} Test Street\nAnytown, OH 44000"
    p["birthday"] = f"{i % 12 + 1:02d}-{i % 28 + 1:02d}"
    p["share"] = SHARES.get(p["id"], ["email", "phone"])
path.write_text(json.dumps(people, indent=1))
print(f"Preview roster: made-up contact details for {len(people)} members")
