"""What happens to a Dependabot pull request (.github/workflows/dependabot.yml runs this on GitHub).

  python dev/dependabot.py decide    prints merge, release or wait (and why), and writes verdict= to $GITHUB_OUTPUT
  python dev/dependabot.py release   the patch release in the same PR: version + 1, uv.lock, a CHANGELOG section

merge: it changes only uv.lock (Python) or pinned `uses:` lines (Actions), every update is a patch or minor one,
Actions come from a trusted publisher, and the app's own packages are unchanged (dev tools only).
release: the same, but an app package changed for a high or critical security advisory (CVSS 7.0 or more), the
fixed version has been on PyPI 48 hours (a poisoned release, a hijacked maintainer's "fix", is usually pulled by
then), and main holds nothing unreleased: this PR becomes the release x.y.z+1 (the Release workflow tags it once it
merges; prod's nightly update installs it).
hold: as release, but the fixed version is younger than 48 hours: the workflow labels it, tells the maintainer (a
comment, and a review request to SECURITY_CONTACT), and looks again later. A `release-now` label (the maintainer's,
for a confirmed serious issue) skips the hold and the severity rule: RELEASE_NOW=1.
wait: anything else; Jim gets GitHub's usual email. An ordinary app-package update waits for the next release.

Reads from the environment: BASE (the PR's base commit), ECOSYSTEM, UPDATE_TYPE, DEPS (comma-separated), GHSA
(the advisory ids, empty unless it's a security update), CVSS (its score), UPDATED (the updates as JSON, with each
newVersion): dependabot/fetch-metadata's outputs.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISHERS = ("actions/", "github/", "astral-sh/")  # Actions from anyone else wait for Jim
HOLD_HOURS, MIN_CVSS = 48, 7.0
RELEASE_CONTENT = ["src", "docs", "deploy", "pyproject.toml", "club.example.yaml", "club.minimal.yaml", "LICENSE"]


def decide(files: list[str], ecosystem: str, update_type: str, deps: list[str], ghsa: str,
           app_changed: bool, unreleased: bool, cvss: float = 0.0, age_hours: float = 0.0,
           release_now: bool = False) -> tuple[str, str]:
    """The verdict and why, from what the PR changes. Pure, so tests/test_dependabot.py can try every case."""
    if update_type not in ("version-update:semver-patch", "version-update:semver-minor"):
        return "wait", f"{update_type or 'unknown'} update: Jim decides"
    if ecosystem == "uv":
        if set(files) != {"uv.lock"}:
            return "wait", f"changes more than uv.lock: {', '.join(sorted(files))}"
    elif ecosystem == "github_actions":
        if not files or any(not f.startswith(".github/workflows/") for f in files):
            return "wait", f"changes more than the workflows: {', '.join(sorted(files))}"
        if any(not d.startswith(PUBLISHERS) for d in deps):
            return "wait", f"an Action from another publisher: {', '.join(deps)}"
        return "merge", "Actions only (CI, never a release)"
    else:
        return "wait", f"ecosystem {ecosystem!r}"
    if not app_changed:
        return "merge", "development tools only"
    if not ghsa:
        return "wait", "an app package, not a security fix: it goes with the next release"
    if unreleased:
        return "wait", "a security fix, but main holds unreleased changes to the app: Jim releases it"
    if release_now:
        return "release", f"a security fix released now by the maintainer's release-now label ({ghsa})"
    if cvss < MIN_CVSS:
        return "wait", f"a security fix below high (CVSS {cvss}): Jim releases it"
    if age_hours < HOLD_HOURS:
        return "hold", f"a security fix, but the fixed version is {age_hours:.0f} hours old: released at {HOLD_HOURS}"
    return "release", f"a security fix in an app package ({ghsa}, CVSS {cvss})"


def _age_hours(updated: str) -> float:
    """How long the youngest of the new versions has been on PyPI, in hours (0 if PyPI doesn't say)."""
    from urllib.parse import quote
    from urllib.request import urlopen
    ages = []
    for u in json.loads(updated or "[]"):
        try:
            with urlopen(f"https://pypi.org/pypi/{quote(u['dependencyName'])}/{quote(u['newVersion'])}/json", timeout=20) as r:
                times = [f["upload_time_iso_8601"] for f in json.load(r).get("urls", [])]
        except OSError:
            times = []
        if not times:
            return 0.0
        first = min(dt.datetime.fromisoformat(x) for x in times)
        ages.append((dt.datetime.now(dt.UTC) - first).total_seconds() / 3600)
    return min(ages, default=0.0)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def _app_packages(rev: str | None) -> str:
    """The app's own packages (no dev tools) as uv.lock has them at rev (None: the working tree)."""
    with tempfile.TemporaryDirectory() as d:
        for f in ("pyproject.toml", "uv.lock"):
            (Path(d) / f).write_text(_git("show", f"{rev}:{f}") if rev else (ROOT / f).read_text())
        return subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project", "--no-header", "--quiet",
                               "--directory", d], check=True, capture_output=True, text=True).stdout


def _last_release(rev: str) -> str:
    """The newest vX.Y.Z tag in rev's history (release candidates don't count)."""
    tags = [t for t in _git("tag", "--merged", rev, "--sort=-v:refname", "--list", "v*").split()
            if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    return tags[0]


def main_decide() -> None:
    base, env = os.environ["BASE"], os.environ
    files = [f for f in _git("diff", "--name-only", f"{base}...HEAD").splitlines() if f]
    app_changed = env.get("ECOSYSTEM") == "uv" and _app_packages(base) != _app_packages(None)
    tag = _last_release(base)
    unreleased = bool(_git("diff", "--name-only", tag, base, "--", *RELEASE_CONTENT).strip()) or (
        app_changed and _app_packages(tag) != _app_packages(base))
    ghsa = env.get("GHSA", "").strip()
    verdict, why = decide(files, env.get("ECOSYSTEM", ""), env.get("UPDATE_TYPE", ""),
                          [d.strip() for d in env.get("DEPS", "").split(",") if d.strip()], ghsa, app_changed, unreleased,
                          float(env.get("CVSS") or 0), _age_hours(env.get("UPDATED", "")) if ghsa and app_changed else 0.0,
                          env.get("RELEASE_NOW") == "1")
    print(f"{verdict}: {why}")
    if out := env.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"verdict={verdict}\n")


def release(changelog: str, version: str, deps: str, ghsa: str, today: dt.date) -> tuple[str, str]:
    """The next patch version and the CHANGELOG with its section: what waited under Unreleased (development lines,
    since main is always releasable) goes out with it, and Unreleased starts empty again."""
    major, minor, patch = (int(x) for x in version.split("."))
    new = f"{major}.{minor}.{patch + 1}"
    head, sep, rest = changelog.partition("## Unreleased\n")
    if not sep:
        raise SystemExit("CHANGELOG.md has no '## Unreleased' heading")
    waiting, _, older = rest.partition("\n## ")
    lines = [f"- **Security:** {deps} updated ({ghsa}). Update soon."] + [ln for ln in waiting.strip().splitlines() if ln.strip()]
    section = f"{head}## Unreleased\n\n## {new} ({today.isoformat()})\n" + "\n".join(lines) + "\n"
    return new, section + (f"\n## {older}" if older else "")


def main_release() -> None:
    py = ROOT / "pyproject.toml"
    version = tomllib.loads(py.read_text())["project"]["version"]
    cl = ROOT / "CHANGELOG.md"
    new, text = release(cl.read_text(), version, os.environ["DEPS"], os.environ["GHSA"], dt.date.today())
    cl.write_text(text)
    py.write_text(py.read_text().replace(f'version = "{version}"', f'version = "{new}"', 1))
    subprocess.run(["uv", "lock", "--quiet"], cwd=ROOT, check=True)
    print(new)


if __name__ == "__main__":
    {"decide": main_decide, "release": main_release}[sys.argv[1]]()
