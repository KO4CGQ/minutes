"""Checks on the repo itself: the screenshots match the pages they show, the docs' links lead somewhere, and nothing
that looks like a real person's contact details is in a tracked file (the repo is public)."""
import re
import subprocess
import sys
from pathlib import Path

from club_minutes import web

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").glob("*.md"))]
SHOTS = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT / "demo"))
import manifest


def test_screenshots_are_up_to_date():
    """A template, CSS or script a screenshot's page uses has changed since the pictures were taken."""
    stale = manifest.stale()
    assert not stale, ("out of date, retake with `uv run python demo/screenshots.py`: "
                       + "; ".join(f"{n}.png ({', '.join(f)})" for n, f in stale.items()))
    import json
    listed = set(json.loads(manifest.MANIFEST.read_text())["shots"])
    assert listed == {p.stem for p in SHOTS.glob("*.png")}, "a picture isn't in demo/screenshots.py, or one is missing"


def test_every_screenshot_is_used():
    text = "\n".join(p.read_text() for p in DOCS)
    assert not [p.name for p in SHOTS.glob("*.png") if p.name not in text], "a screenshot no guide or the README shows"


def test_doc_links_lead_somewhere():
    """Links and pictures in the README and guides point to files that exist; app addresses they name are pages."""
    bad = []
    for doc in DOCS:
        text = doc.read_text()
        for target in re.findall(r"\]\(([^)\s]+)\)|src=\"([^\"]+)\"", text):
            t = (target[0] or target[1]).split("#")[0]
            if t and not re.match(r"[a-z]+:", t) and not t.startswith("/") and not (doc.parent / t).exists():
                bad.append(f"{doc.name}: {t}")
    routes = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", r.path) + "$") for r in web.app.routes if hasattr(r, "path")]
    for doc in DOCS:
        for addr in re.findall(r"`(/[a-z][\w/.-]*)`", doc.read_text()):
            if not addr.startswith(("/etc/", "/opt/", "/var/", "/root/", "/usr/", "/home/", "/tmp/", "/static/")) \
                    and addr not in ("/etc", "/opt", "/var") and not any(r.match(addr.rstrip("/") or "/") for r in routes):
                bad.append(f"{doc.name}: `{addr}` isn't a page")
    assert not bad, bad


# Public repo: examples are fictional (docs/developers.md). Allowed: the copyright holder's callsign, the app's earlier
# name, callsigns with a four-letter suffix (never issued, like N0CALL), reserved addresses (RFC 2606) and 555-01xx.
CALLSIGN = re.compile(r"\b[AKNW][A-Z]?[0-9][A-Z]{1,3}\b")
OK_CALLS = {"KO4CGQ", "W8LBZ", "W3PD"}  # W3PD: a group in an example backup passphrase
EMAIL = re.compile(r"[\w.%+-]+@([\w-]+(?:\.[\w-]+)+)")
OK_DOMAINS = re.compile(r"(?:.*\.)?(example(\.(org|com|net))?|test|invalid|localhost)$|google\.com$", re.IGNORECASE)  # google.com: calendar ids in .ics tests
PHONE = re.compile(r"(?:\(\d{3}\) ?|\b\d{3}[-.])(\d{3})[-.](\d{4})\b")
SKIP = re.compile(r"\.(png|ico|jpe?g|gif|webp|woff2?|pdf|xlsx)$|^uv\.lock$|simple_icons\.json$")


def test_no_real_looking_contact_details():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    bad = []
    for f in files:
        p = ROOT / f
        if SKIP.search(f) or not p.is_file():
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        bad += [f"{f}: callsign {c}" for c in set(CALLSIGN.findall(text)) - OK_CALLS]
        bad += [f"{f}: email domain {d}" for d in {d for d in EMAIL.findall(text) if not OK_DOMAINS.match(d)}]
        bad += [f"{f}: phone {a}-{b}" for a, b in set(PHONE.findall(text)) if not (a == "555" and b.startswith("01"))]
    assert not bad, "use fictional examples (Jane Doe, N0CALL, example.org, 555-0100 to 555-0199): " + "; ".join(sorted(bad))
