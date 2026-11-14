"""The README's and the guides' screenshots, from the demo club: `uv run python demo/screenshots.py` (writes
docs/screenshots/*.png).

Builds the demo club in a temporary folder (demo/build.py), then serves the app to a real browser straight from
the test client, as the browser tests do: no server, and never a club's data. Signed in as the demo President.
The demo runs in preview mode (its email goes nowhere); its PREVIEW banner is hidden from the pictures.
"""
import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "docs" / "screenshots"
tmp = Path(tempfile.mkdtemp(prefix="club-minutes-demo-"))
subprocess.run([sys.executable, str(HERE / "build.py"), str(tmp)], check=True)
os.environ.update(CLUB_MINUTES_DATA=str(tmp / "data"), CLUB_MINUTES_CONFIG=str(tmp / "club.yaml"), CLUB_MINUTES_PREVIEW="demo")

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from club_minutes import web

sys.path.insert(0, str(HERE))
import manifest

rendered: set[str] = set()  # the templates the current picture's page rendered
_respond = web.templates.TemplateResponse
def _record(request, name, *a, **k):
    rendered.add(name)
    return _respond(request, name, *a, **k)
web.templates.TemplateResponse = _record

club = web.archive.club
me = club.canonical("n0jane")
web.updates.available = lambda: None  # the Admin page's "new version" line: never GitHub, and the same each time
who = {}  # who's signed in for each picture: the President (also in admin mode), the Secretary (keeps the roster), the Treasurer, a member
for key, mid in (("president", me), ("admin", me), ("secretary", club.canonical("w0alex")),
                 ("treasurer", club.canonical("n0sams")), ("member", club.canonical("k0pats"))):  # Pat is behind on dues
    who[key] = TestClient(web.app)
    who[key].cookies.set(web.COOKIE, web.authn.new_session(mid, "Mozilla/5.0", "127.0.0.1", "passkey"))
web.authn.set_admin(who["admin"].cookies.get(web.COOKIE), 60)
CLOCK = dt.datetime.combine(dt.date.today(), dt.time(18, 18))  # the top bar's clock: 6:18 PM today
_until = web.authn.admin_until  # admin mode "until 6:48 PM", to match that clock (it's on whatever the real time)
web.authn.admin_until = lambda token: int((CLOCK + dt.timedelta(minutes=30)).timestamp()) if _until(token) else None
latest = max(m.date for m in web.archive.meetings)
SHOTS = [  # file name, page, phone?, dark?, signed in as, height (a phone's is 844)
    ("home", "/", False, False, "president", 860),
    ("minutes", f"/meetings/{latest}", False, False, "president", 860),
    ("calendar", "/calendar?view=month", False, True, "president", 860),
    ("business", "/business", False, True, "president", 860),
    ("home-phone", "/", True, False, "president", 844),
    ("minutes-phone", f"/meetings/{latest}", True, True, "president", 844),
    # the guides': shorter, just the part the guide talks about
    ("roster-edit", f"/members/{club.canonical('k0john')}/edit", False, False, "secretary", 640),
    ("offices", "/offices", False, True, "president", 560),
    ("chow", "/chow", False, False, "president", 600),
    ("admin", "/admin", False, False, "admin", 560),
    ("backups", "/admin/backups", False, True, "admin", 640),
    ("site-look", "/admin/site/look", False, False, "admin", 640),
    ("site-roles", "/admin/site/roles#roles", False, False, "admin", 760),
    ("home-dues", "/", False, False, "member", 520),
    ("spending", "/spending", False, False, "member", 720),
    ("treasurer", "/treasurer", False, False, "treasurer", 680),
    ("treasurer-bills", "/treasurer?tab=bills", False, True, "treasurer", 640),
    ("treasurer-settings", "/treasurer?tab=settings", False, False, "treasurer", 680),
]
client = who["president"]


def serve(route):
    r = client.request(route.request.method, route.request.url.replace("http://testserver", ""), follow_redirects=False)
    route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)


OUT.mkdir(parents=True, exist_ok=True)
used: dict[str, set[str]] = {}
with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, path, phone, dark, as_who, height in SHOTS:
        client = who[as_who]
        page = browser.new_page(viewport={"width": 390 if phone else 1280, "height": height}, device_scale_factor=2 if phone else 1,
                                color_scheme="dark" if dark else "light", timezone_id=club.timezone)
        page.clock.set_fixed_time(CLOCK.replace(tzinfo=ZoneInfo(club.timezone)))  # the top bar's clock, in club time
        page.route("http://testserver/**", serve)
        rendered.clear()
        page.goto("http://testserver" + path)
        page.add_style_tag(content=".preview { display: none !important; }")  # the demo's PREVIEW banner
        page.screenshot(path=str(OUT / f"{name}.png"))
        used[name] = set(rendered)
        page.close()
    browser.close()
manifest.write(used)  # for the test that says when a picture is out of date
print(f"Screenshots in {OUT}")
