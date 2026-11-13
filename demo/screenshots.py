"""The README's screenshots, from the demo club: `uv run python demo/screenshots.py` (writes docs/screenshots/*.png).

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

from fastapi.testclient import TestClient  # noqa: E402  (after the folders are set)
from playwright.sync_api import sync_playwright  # noqa: E402

from club_minutes import web  # noqa: E402

club = web.archive.club
me = club.canonical("n0jane")
c = TestClient(web.app)
c.cookies.set(web.COOKIE, web.authn.new_session(me, "Mozilla/5.0", "127.0.0.1", "passkey"))
CLOCK = dt.datetime.combine(dt.date.today(), dt.time(18, 18))  # the top bar's clock: 6:18 pm today
latest = max(m.date for m in web.archive.meetings)
SHOTS = [  # file name, page, phone?, dark?
    ("home", "/", False, False),
    ("minutes", f"/meetings/{latest}", False, False),
    ("calendar", "/calendar?view=month", False, True),
    ("business", "/business", False, True),
    ("home-phone", "/", True, False),
    ("minutes-phone", f"/meetings/{latest}", True, True),
]


def serve(route):
    r = c.request(route.request.method, route.request.url.replace("http://testserver", ""), follow_redirects=False)
    route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)


OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, path, phone, dark in SHOTS:
        page = browser.new_page(viewport={"width": 390, "height": 844} if phone else {"width": 1280, "height": 860},
                                device_scale_factor=2 if phone else 1, color_scheme="dark" if dark else "light", timezone_id=club.timezone)
        page.clock.set_fixed_time(CLOCK.replace(tzinfo=ZoneInfo(club.timezone)))  # the top bar's clock, in club time
        page.route("http://testserver/**", serve)
        page.goto("http://testserver" + path)
        page.add_style_tag(content=".preview { display: none !important; }")  # the demo's PREVIEW banner
        page.screenshot(path=str(OUT / f"{name}.png"))
        page.close()
    browser.close()
print(f"Screenshots in {OUT}")
