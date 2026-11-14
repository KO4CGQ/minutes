#!/usr/bin/env bash
# Upgrade from the last release (docs/developers.md → Compatibility): the newest 1.x release builds the demo club's data
# with its own code and club.yaml, then this code opens that data folder (its data upgrades run at start) and loads the
# main pages as an admin and as a member. Catches an upgrade step, or a change in how data is read, that breaks a real
# club's data of the old shape, which the unit tests (hand-made old shapes) can miss. Run by tests.yml and dev/checks.sh.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
tag=$(git ls-remote --tags --refs origin 'v1.*' | sed 's#.*refs/tags/##' | sort -V | tail -1)
[ -n "$tag" ] || { echo "No 1.x release found on origin." >&2; exit 1; }
work=$(mktemp -d)
trap 'git worktree remove --force "$work/old" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
git fetch -q --depth=1 origin "refs/tags/$tag:refs/tags/$tag" 2>/dev/null || true  # CI's checkout has no tags
git worktree add -q --detach "$work/old" "$tag"
echo "Building the demo club with $tag…"
(cd "$work/old" && uv run -q --frozen python demo/build.py "$work/club")

echo "Opening it with this code…"
CLUB_MINUTES_DATA="$work/club/data" CLUB_MINUTES_CONFIG="$work/club/club.yaml" CLUB_MINUTES_PREVIEW=demo \
  uv run -q --frozen python - <<'EOF'
import json
import os
from pathlib import Path

data = Path(os.environ["CLUB_MINUTES_DATA"])
# What the old release wrote, read before the upgrade: the pages must still show all of it afterwards.
rows = json.loads((data / "roster-sheet.json").read_text())["Member Roster"]
names = [r[rows[0].index("full_name")] for r in rows[1:]]
slugs = sorted(p.name for p in (data / "meetings").iterdir())
assert names and slugs, "the old release built no roster or minutes"

from fastapi.testclient import TestClient  # noqa: E402

from club_minutes import upgrade, web  # noqa: E402  (importing web runs the data upgrades)

have = int((data / "data-version").read_text())
assert have == upgrade.CURRENT, f"data-version {have}, expected {upgrade.CURRENT}"
pages = ["/", "/minutes", f"/meetings/{slugs[-1]}", "/calendar", "/business", "/chow", "/roster", "/offices", "/stats"]
for who, extra in (("n0jane", ["/treasurer"]), ("k0pats", [])):  # the demo's President (admin) and a member
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, web.authn.new_session(who))
    for path in pages + extra:
        r = t.get(path)
        assert r.status_code == 200 and "W0DEMO" in r.text, (who, path, r.status_code)
        if path == "/roster":
            assert not (gone := [n for n in names if n not in r.text]), f"members missing after the upgrade: {gone}"
        if path == "/minutes":
            assert not (gone := [s for s in slugs if f"/meetings/{s}" not in r.text]), f"minutes missing: {gone}"
print(f"ok: data-version {have}; {len(names)} members and {len(slugs)} minutes still there; pages open as an admin and a member")
EOF
