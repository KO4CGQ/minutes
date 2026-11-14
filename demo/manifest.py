"""Which files each screenshot shows, so the tests can say when one is out of date (tests/test_guards.py).

demo/screenshots.py records the templates each picture's page rendered; this adds what those templates pull in
(extends, include, import, and the /static/ CSS and scripts they link) and writes docs/screenshots/manifest.json:
each picture's files, and a fingerprint (SHA-256) of every file as it was when the pictures were taken. Python
changes aren't tracked: a page that shows something new from the code needs a retake by eye."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "club_minutes"
MANIFEST = ROOT / "docs" / "screenshots" / "manifest.json"
REFS = re.compile(r"""\{%-?\s*(?:extends|include|import|from)\s+["']([^"']+)["']""")
STATIC = re.compile(r"/static/([\w.-]+)")


def files_for(templates: set[str]) -> list[str]:
    """The templates and everything they pull in, as paths under src/club_minutes/."""
    seen, todo = set(), list(templates)
    while todo:
        t = todo.pop()
        if t in seen or not (APP / "templates" / t).exists():
            continue
        seen.add(t)
        todo += REFS.findall((APP / "templates" / t).read_text())
    out = {f"templates/{t}" for t in seen}
    for t in seen:
        out |= {f"static/{s}" for s in STATIC.findall((APP / "templates" / t).read_text()) if (APP / "static" / s).exists()}
    return sorted(out)


def sha(path: str) -> str:
    return hashlib.sha256((APP / path).read_bytes()).hexdigest()


def write(shots: dict[str, set[str]]) -> None:
    """shots: picture name -> the templates its page rendered."""
    each = {name: files_for(t) for name, t in sorted(shots.items())}
    files = sorted({f for fs in each.values() for f in fs})
    MANIFEST.write_text(json.dumps({"shots": each, "files": {f: sha(f) for f in files}}, indent=1) + "\n")


def stale() -> dict[str, list[str]]:
    """Pictures whose files have changed since they were taken: name -> the changed files."""
    m = json.loads(MANIFEST.read_text())
    changed = {f for f, h in m["files"].items() if not (APP / f).exists() or sha(f) != h}
    return {name: sorted(changed & set(fs)) for name, fs in m["shots"].items() if changed & set(fs)}
