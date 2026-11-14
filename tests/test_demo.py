"""The demo club (demo/build.py) builds through the app's own code, and its main pages open for its admin."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = """
import json, os
from fastapi.testclient import TestClient
from club_minutes import web
assert len(json.load(open(os.environ["CLUB_MINUTES_DATA"] + "/roster-sheet.json"))["Member Roster"]) == 13  # header + 12
t = TestClient(web.app)
t.cookies.set(web.COOKIE, web.authn.new_session("n0jane"))
for path in ("/", "/calendar", "/business", "/chow", "/offices", "/roster", "/minutes", "/stats"):
    r = t.get(path)
    assert r.status_code == 200 and "W0DEMO" in r.text, (path, r.status_code)
slug = sorted(os.listdir(os.environ["CLUB_MINUTES_DATA"] + "/meetings"))[-1]
assert "Worked a new country" in t.get(f"/meetings/{slug}").text
print("ok")
"""


def test_the_demo_club_builds_and_opens(tmp_path):
    out = tmp_path / "demo"
    subprocess.run([sys.executable, str(ROOT / "demo" / "build.py"), str(out)], check=True, capture_output=True, text=True)
    env = {**os.environ, "CLUB_MINUTES_DATA": str(out / "data"), "CLUB_MINUTES_CONFIG": str(out / "club.yaml")}
    r = subprocess.run([sys.executable, "-c", CHECK], env=env, check=False, capture_output=True, text=True, cwd=tmp_path)
    assert r.stdout.strip().endswith("ok"), r.stderr[-2000:]


def test_the_demo_is_never_built_in_a_folder_with_anything_in_it(tmp_path):
    club = tmp_path / "club-data"  # a club's data folder, say
    club.mkdir()
    (club / "roster.json").write_text("[]")
    r = subprocess.run([sys.executable, str(ROOT / "demo" / "build.py"), str(club)], check=False, capture_output=True, text=True)
    assert r.returncode and "isn't empty" in r.stderr and [p.name for p in club.iterdir()] == ["roster.json"]
