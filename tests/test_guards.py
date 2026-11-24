"""Checks on the repo itself: the screenshots match the pages they show, the docs' links lead somewhere, and nothing
that looks like a real person's contact details is in a tracked file (the repo is public)."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from club_minutes import web

pytestmark = pytest.mark.docs  # dev/changes.sh: a docs-only change runs just these

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").glob("*.md"))]
SHOTS = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT / "demo"))
import manifest


@pytest.mark.skipif(not os.environ.get("CHECK_SCREENSHOTS"), reason="only for a release: at the freeze and on pull requests to main")
def test_screenshots_are_up_to_date():
    """A template, CSS or script a screenshot's page uses has changed since the pictures were taken. Items don't retake
    them as they go (each one would, then the next item would again): the release does, once."""
    stale = manifest.stale()
    assert not stale, ("out of date, retake with `uv run python demo/screenshots.py`: "
                       + "; ".join(f"{n}.png ({', '.join(f)})" for n, f in stale.items()))


def test_every_screenshot_is_listed():
    import json
    listed = set(json.loads(manifest.MANIFEST.read_text())["shots"])
    assert listed == {p.stem for p in SHOTS.glob("*.png")}, "a picture isn't in demo/screenshots.py, or one is missing"


def test_manifest_keeps_pictures_not_retaken(tmp_path, monkeypatch):
    """screenshots.py retakes only what's out of date: the pictures it skips stay in the manifest, still up to date."""
    import json
    monkeypatch.setattr(manifest, "MANIFEST", tmp_path / "manifest.json")
    manifest.write({"home": {"home.html"}}, {"chow": ["templates/chow.html"]})
    m = json.loads(manifest.MANIFEST.read_text())
    assert set(m["shots"]) == {"home", "chow"} and "templates/chow.html" in m["files"] and "templates/base.html" in m["files"]
    assert manifest.stale() == {}


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
CALLSIGN = re.compile(r"(?<!-)\b[AKNW][A-Z]?[0-9][A-Z]{1,3}\b(?!-)", re.IGNORECASE)  # either case (ids are lowercase); not a piece of a uuid
OK_CALLS = {"KO4CGQ", "W8LBZ", "W3PD", "NL2BR"}  # W3PD: a group in an example backup passphrase; NL2BR: Markdown's nl2br extension
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
        bad += [f"{f}: callsign {c}" for c in {c.upper() for c in CALLSIGN.findall(text)} - OK_CALLS]
        bad += [f"{f}: email domain {d}" for d in {d for d in EMAIL.findall(text) if not OK_DOMAINS.match(d)}]
        bad += [f"{f}: phone {a}-{b}" for a, b in set(PHONE.findall(text)) if not (a == "555" and b.startswith("01"))]
    assert not bad, "use fictional examples (Jane Doe, N0CALL, example.org, 555-0100 to 555-0199): " + "; ".join(sorted(bad))


LISTS = ["roster.html", "index.html", "guests.html", "search.html", "treasurer.html", "business.html", "_business_item.html", "stats.html"]


def test_list_links_say_where_they_came_from():
    """A member or a meeting opened from a list leads back to that list (?from=..., base.html's back())."""
    bad = []
    for t in LISTS:
        for href in re.findall(r'href="(/(?:members|meetings)/\{\{[^"]*)"', (ROOT / "src/club_minutes/templates" / t).read_text()):
            if "from=" not in href and "from_item" not in href and not href.endswith((".pdf", "/edit")):
                bad.append(f"{t}: {href}")
    assert not bad, bad


def test_every_from_leads_back(monkeypatch):
    from fastapi.testclient import TestClient
    mid = next(iter(web.archive.club.members))
    t = TestClient(web.app)
    t.cookies.set(web.COOKIE, web.authn.new_session(mid))
    for came, href, label in (("roster", "/roster", "Roster"), ("guests", "/guests", "Guests"), ("minutes", "/minutes", "Minutes"),
                              ("stats", "/stats", "Attendance"), ("search&q=bob%20a", "/search?q=bob%20a", "Search"), ("home", "/", "Home")):
        html = t.get(f"/members/{mid}?from={came}").text
        assert f'<nav class="crumb" aria-label="Back"><a href="{href}">‹ {label}</a>' in html, came


TEMPLATES = ROOT / "src/club_minutes/templates"
CSS = (ROOT / "src/club_minutes/static/style.css").read_text()


def test_destructive_forms_ask_first():
    """A form whose button or address says remove, delete, ban... carries data-confirm (on the form or a button)."""
    risky = re.compile(r"\b(remov\w*|delet\w*|lift|suspend\w*|accept|object|call off|ban|unban|discard|revoke|end-all|sign ?out|withdraw|forget)\b", re.IGNORECASE)
    allowed = {  # part of the form tag -> why it needs no question
        'action="/logout"': "signing out loses nothing",
        'action="/admin/view-as"': "Stop only ends a look at the app as someone else",
        'action="/admin/mode"': "turning admin mode off loses nothing",
        'action="/admin/updates/cancel"': "withdrawing the request can simply be made again",
        'action="/admin/security/unban"': "Unban gives an address its access back",
        'action="/profile/photo/remove"': "a member's own picture: uploading again undoes it",
        'action="/chow/requests/{{ r.id }}/answer"': "cancelling a chow request can be asked again",
    }
    bad = []
    for t in sorted(TEMPLATES.glob("*.html")):
        for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", t.read_text(), re.DOTALL):
            head, inner = m.groups()
            texts = re.findall(r"<button\b(?![^>]*type=\"button\")[^>]*>(.*?)</button>", inner, re.DOTALL)  # type="button" sends nothing
            probe = " ".join([re.sub(r"<[^>]+>|\{[{%].*?[}%]\}", " ", " ".join(texts)),
                              (re.search(r'action="([^"]*)"', head) or [0, ""])[1].replace("/", " "),
                              " ".join(re.findall(r'name="op" value="(\w+)"', inner))])
            if risky.search(probe) and "data-confirm" not in head + inner and not any(a in head for a in allowed):
                bad.append(f"{t.name}: {' '.join(head.split())[:80]}")
    assert not bad, "destructive forms without data-confirm (or an allowlist reason): " + "; ".join(bad)


def test_changes_are_not_shown_by_colour_alone():
    assert re.search(r"table\.diff ins\b[^{}]*\{[^}]*text-decoration:\s*underline", CSS), "ins needs an underline"
    assert re.search(r"table\.diff del\b[^{}]*\{[^}]*text-decoration:\s*line-through", CSS), "del needs a strike-through"


def test_new_tab_links_are_safe_and_say_so():
    """rel=noopener, and the name says it opens in a new tab: ↗ or "new tab", or no class (style.css adds both to those)."""
    bad = []
    for t in sorted(TEMPLATES.glob("*.html")):
        for m in re.finditer(r"<a\b[^>]*target=\"_blank\"[^>]*>(.*?)</a>", t.read_text(), re.DOTALL):
            tag = m.group(0)
            if 'rel="noopener' not in tag or (" class=" in tag and not re.search(r"↗|new tab", tag)):
                bad.append(f"{t.name}: {tag[:90]}")
    for line in (ROOT / "src/club_minutes/static/console.js").read_text().splitlines():
        link = line[line.rindex('h("a"', 0, line.index('target: "_blank"')):] if 'target: "_blank"' in line else ""
        if link and ('rel: "noopener"' not in link or ("class:" in link and not re.search(r"↗|new tab", link))):
            bad.append(f"console.js: {link[:90]}")
    assert not bad, "target=_blank needs rel=noopener and ↗ or 'new tab' in its name: " + "; ".join(bad)


INTRO = re.compile(r"(?:</h1>|\{% (?:el)?if tab == [^%]*%\})(?:\s|\{[%#][^}]*[%#]\}|<[^p][^>]*>)*?<p class=\"muted\"[^>]*>(.*?)</p>", re.DOTALL)


def intros():
    for t in sorted(TEMPLATES.glob("*.html")):
        for m in INTRO.finditer(t.read_text()):
            text = re.sub(r"\{%.*?%\}|\{#.*?#\}|<[^>]+>", " ", m.group(1), flags=re.DOTALL)
            yield t.name, len(re.sub(r"\{\{.*?\}\}", "x", text).split()), text.split()[:6]


def test_page_intros_are_short():
    """The grey paragraph under a page's title: 60 words at most. Longer goes in a hint beside what it explains."""
    seen = list(intros())
    assert len(seen) > 10, "the intro pattern matches nothing"
    long = [f"{n} ({w} words)" for n, w, _ in seen if w > 60]
    assert not long, "page intros over 60 words: split them or move the detail into hints: " + "; ".join(long)


def test_new_tab_links_have_one_arrow():
    """style.css adds "↗" (and "opens in a new tab" for screen readers) to a plain link with target="_blank"; one typed
    into such a link as well shows twice. Only links with a class (buttons) carry their own."""
    twice = [f"{t.name}: {m.group(0)[:80]}" for t in sorted(TEMPLATES.glob("*.html"))
             for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", t.read_text(), re.DOTALL)
             if 'target="_blank"' in m.group(1) and "class=" not in m.group(1) and "↗" in m.group(2)]
    assert not twice, twice


def test_changelog_sub_bullets_indent_by_four():
    """The About page's Markdown (Python-Markdown) shows a 2-space sub-bullet as a flat line; GitHub nests either."""
    bad = [line for line in (ROOT / "CHANGELOG.md").read_text().splitlines()
           if re.match(r" +[-*] ", line) and (len(line) - len(line.lstrip())) % 4]
    assert not bad, f"indent sub-bullets by 4 spaces: {bad[:3]}"
