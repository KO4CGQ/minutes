"""Refresh the brand logos for the Links page from Simple Icons (https://simpleicons.org, CC0), stored in the repo
so the app never loads icons from a CDN.

    python3 deploy/update-simple-icons.py [VERSION]     # default: the version below

Reads the npm package's icons/*.svg straight from the downloaded archive (nothing is unpacked) and writes
src/w8lbz_minutes/simple_icons.json: {slug: [title, svg path]}. Each path is checked to be only path data.
"""
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = sys.argv[1] if len(sys.argv) > 1 else "16.37.0"
OUT = Path(__file__).resolve().parent.parent / "src" / "w8lbz_minutes" / "simple_icons.json"
SVG = re.compile(r'^<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>(.+?)</title><path d="([^"]+)"/></svg>\s*$')
PATH_DATA = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9.,\s-]+$")

url = f"https://registry.npmjs.org/simple-icons/-/simple-icons-{VERSION}.tgz"
data = urllib.request.urlopen(url, timeout=60).read()
icons, skipped = {}, []
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
    for m in tar.getmembers():
        name = m.name.rsplit("/", 1)[-1]
        if not (m.isfile() and m.name.startswith("package/icons/") and name.endswith(".svg")):
            continue
        slug = name[:-4]
        match = SVG.match(tar.extractfile(m).read().decode())
        if not (re.fullmatch(r"[a-z0-9_]+", slug) and match and PATH_DATA.match(match.group(2))):
            skipped.append(slug)
            continue
        title = match.group(1).replace("&amp;", "&").replace("&apos;", "'").replace("&quot;", '"')
        icons[slug] = [title, match.group(2)]
OUT.write_text(json.dumps(dict(sorted(icons.items())), separators=(",", ":")))
print(f"simple-icons {VERSION}: {len(icons)} logos -> {OUT} ({OUT.stat().st_size // 1024} KB)"
      + (f"; skipped {len(skipped)}: {', '.join(skipped[:10])}" if skipped else ""))
