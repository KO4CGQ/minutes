"""Give a preview's roster copy made-up contact details, so no real phone numbers, addresses, birthdays
or emails sit in a preview. Names and callsigns stay: the meetings, attendance and chow schedule use them.

    python3 deploy/preview-fake.py PREVIEW_DATA_DIR

The first three non-officers (by callsign) are the test records, so what you see depends on what they share:
everything, phone only, and email only. Everyone else shares nothing, as on the real roster until a member
opts in. Officers see everything regardless. The script prints who they are.
"""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1]) / "roster.json"
people = json.loads(path.read_text())
testers = sorted((p for p in people if not p.get("officer")), key=lambda p: p["id"])[:3]
SHARES = dict(zip((p["id"] for p in testers), (["email", "phone", "address", "birthday"], ["phone"], ["email"])))
for i, p in enumerate(people, 1):
    p["email"] = f"{p['id']}@example.org"
    p["phone"] = f"419-555-01{i:02d}"
    p["address"] = f"{100 + i} Test Street\nAnytown, OH 44000"
    p["birthday"] = f"{i % 12 + 1:02d}-{i % 28 + 1:02d}"
    p["share"] = SHARES.get(p["id"], [])
path.write_text(json.dumps(people, indent=1))
print(f"Preview roster: made-up contact details for {len(people)} members; test records: "
      + ", ".join(f"{p.get('callsign') or p['id']} shares {' '.join(SHARES[p['id']]) or 'nothing'}" for p in testers))
