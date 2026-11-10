"""Fuzzing: thousands of hostile inputs, from a fixed seed so any failure reproduces. No library needed.

What must hold whatever is typed: search never errors and never echoes markup; a saved club link is only ever
https/mailto/tel or a page of this app (never another site through //host or /\\host); a sign-in redirect only ever
stays on the site; the roster file keeps every value exactly, as text; the roster API never answers without its
token; and no page answers a random query string with a server error."""
import json
import random
import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from test_calview import signed_in

from w8lbz_minutes import roster, web
from w8lbz_minutes.auth import _safe_next
from w8lbz_minutes.meeting import Item, Meeting
from w8lbz_minutes.store import Archive, spellings

R = random.Random(20261109)
NASTY = ['"', "'", "*", "^", ":", "(", ")", "-", "+", "\\", "/", "//", "/\\", "%", "%2F", "%00", "\x00", "\t", "\n", "\r",
         "\x7f", "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "javascript:alert(1)", "NEAR", "AND", "OR",
         "NOT", "NEAR(a b, 2)", "{col}", "docs:", "text:", "slug:", "é", "ß", "İ", "🙂", "\U0001f468\u200d\U0001f469\u200d\U0001f467", "\u202e", "\ufeff",
         "a" * 300, "x" * 3, " ", "=1+1", "@SUM(1)", "${7*7}", "{{7*7}}", "../../etc/passwd", "https://evil.example",
         "Bob", "bob", "Baker", "Extra", "net"]


def junk(n: int = 8) -> str:
    """A random mix of the nasty pieces and random printable or odd characters."""
    out = []
    for _ in range(R.randint(1, n)):
        r = R.random()
        out.append(R.choice(NASTY) if r < .5 else chr(R.randint(32, 126)) if r < .8 else chr(R.choice([R.randint(0, 31), R.randint(0x80, 0x2FFF)])))
    return "".join(out)


def ascii_junk() -> str:
    """What can travel in a header or cookie: printable ASCII only (browsers and HTTP send nothing else)."""
    return "".join(chr(R.randint(33, 126)) for _ in range(R.randint(0, 20))).replace(";", "").replace('"', "")


@pytest.fixture
def member(tmp_path, monkeypatch):
    root = tmp_path / "meetings"
    Meeting(date=__import__("datetime").date(2026, 10, 1), status="published",
            sections={"Community Reports": [Item("Bobby Bakker passed his Extra exam <b>tonight</b>")]}).save(root)
    m, club, a = signed_in(tmp_path, monkeypatch, "kd8aaa")
    club.members["kd8bbb"].aliases = ["Bobby", "Bakker", junk(), junk()]  # aliases are typed by roster keepers
    monkeypatch.setattr(web, "archive", Archive(root, club))
    return m, club, a


def test_search_never_errors_or_echoes_markup(member):
    m, club, _ = member
    spellings(club)
    for _ in range(400):
        q = junk()
        found = web.archive.search(q, limit=50)  # the full-text index: FTS syntax in q must never reach it unquoted
        assert all("<script" not in r["snippet"] and "onerror=" not in r["snippet"] for r in found)
        r = m.get("/search", params={"q": q, "kind": R.choice(["all", "minutes", "members", junk(3)]), "page": junk(2)})
        assert r.status_code == 200, q
        assert "<script>alert(1)" not in r.text and "<img src=x onerror" not in r.text


def test_saved_links_only_ever_point_to_allowed_places(member, monkeypatch):
    m, club, _ = member
    monkeypatch.setattr(web.authn, "rp_id", "minutes.example")
    monkeypatch.setattr(web, "admin_mode", lambda request: True)
    saved = []
    monkeypatch.setattr(type(club), "save_links", lambda self, links: saved.extend(links))
    stems = ["https://minutes.example/", "https://MINUTES.example/", "https://minutes.example.evil.example/",
             "https://minutes.example@evil.example/", "/", "//", "/\\", "\\\\", "javascript:", "data:text/html,",
             "mailto:", "tel:", "http://", " /", "/\t/", "/%0a/", "vbscript:"]
    for _ in range(600):
        url = R.choice(stems) + junk(4)
        r = m.post("/links", data={"label": "L", "url": url, "icon": "", "show": "both"}, follow_redirects=False)
        assert r.status_code == 303
    for x in saved:
        u = x["url"]
        if u.startswith("/"):  # a page of this app: must stay on the site, wherever it's opened
            assert _safe_next(u) == u and not re.search(r"\s", u), u
            assert web.abs_url(u).startswith("https://minutes.example/"), u
        else:
            assert re.match(r"^(https?://\S+|mailto:\S+@\S+|tel:[+\d][\d ()-]*)$", u), u
            assert not u.lower().startswith("https://minutes.example/"), u  # the app's own address is stored relative


def test_sign_in_redirects_stay_on_the_site():
    for _ in range(3000):
        u = R.choice(["/", "//", "/\\", "\\", "https:", "/%2F", ""]) + junk(5)
        out = _safe_next(u)
        assert out.startswith("/") and not out.startswith("//") and "\\" not in out
        assert not any(ord(c) < 32 or ord(c) == 127 for c in out), repr(out)


def test_roster_file_keeps_every_value_exactly(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA", tmp_path)
    monkeypatch.setattr(roster, "CACHE", tmp_path / "roster.json")
    monkeypatch.setattr(roster, "PREVIEW", None)
    header = ["callsign", "full_name", "email", "Phone", "Address", "aliases", "Title(s)"]
    roster.LocalSheet(tmp_path / "roster-sheet.json").save({"Member Roster": [header, ["KD8AAA", "Amy Able", "", "", "", "", ""]],
                                                             "Membership Log": [["callsign", "full_name", "date", "event", "note"]]})
    sheet = roster.Sheet()
    for _ in range(300):
        new = {f: junk(6) for f in R.sample(["email", "phone", "address", "aliases", "title(s)"], 2)}
        sheet.update_member("KD8AAA", "Amy Able", new)
        row = dict(zip([h.lower() for h in sheet.read()[0]], sheet.read()[1]))
        assert all(row[f] == v for f, v in new.items())  # as typed: "=1+1" stays text, nothing is parsed
    json.loads((tmp_path / "roster-sheet.json").read_text())  # still valid after every write
    roster.sync()


def test_roster_api_never_answers_without_its_token(monkeypatch):
    monkeypatch.setenv("W8LBZ_ROSTER_API_TOKEN", "s3cret-token-value")
    c = TestClient(web.app, client=("127.0.0.1", 5000))
    for _ in range(300):
        h = {"authorization": R.choice(["", "Bearer ", "bearer ", "Basic "]) + R.choice(["s3cret-token-valu", "s3cret-token-value ", ascii_junk(), ""])}
        if R.random() < .3:
            h["x-forwarded-for"] = "127.0.0.1"
        r = c.get("/api/roster", headers=h)
        token_ok = h["authorization"].removeprefix("Bearer ").strip() == "s3cret-token-value" and "x-forwarded-for" not in h
        assert (r.status_code == 200) == token_ok, h


def test_passkey_offer_cookie_is_never_echoed(member, monkeypatch):
    m, *_ = member
    monkeypatch.setattr(web.authn, "rp_id", "testserver")
    for _ in range(100):
        v = "zq" + ascii_junk() + "zq"  # headers are ASCII; the marks make an echo findable
        m.cookies.set(web.PK_OFFER, v)
        r = m.get("/")
        assert r.status_code == 200 and v not in r.text and r.text.count("/static/auth.js") <= 1


def test_no_page_answers_random_parameters_with_a_server_error(member):
    m, *_ = member
    paths = sorted({r.path for r in web.app.routes if "GET" in getattr(r, "methods", set())} - {"/logout"})
    for path in paths:
        for _ in range(4):
            url = re.sub(r"\{[^}]+\}", lambda _: quote(junk(3), safe="") or "x", path)  # as a browser would send it
            params = {k: junk(3) for k in R.sample(["q", "page", "per", "kind", "period", "view", "date", "next", "edit", "show",
                                                    "error", "who", "tab", "since", "year", "month", "start", "guest"], 4)}
            try:
                r = m.get(url, params=params, follow_redirects=False)
            except Exception as e:  # noqa: BLE001 (any unhandled exception inside the app is the finding)
                pytest.fail(f"{url} {params}: {e!r}")
            assert r.status_code < 500, f"{url} {params} -> {r.status_code}"


def test_numbers_from_the_query_string():
    """Found by the page sweep: str.isdigit() accepts "²" and "⁰", which int() can't read (a server error)."""
    from w8lbz_minutes.club import num
    assert [num(v) for v in ("12", " 7 ", "²", "⁰", "٣", "-1", "1e3", "", None, "9" * 5000)] == [12, 7, None, None, None, None, None, None, None, None]
    assert web.paginate(list(range(30)), "²", "⁰")["p"] == 1 and web.paginate(list(range(30)), "2", "10")["rows"] == list(range(10, 20))
