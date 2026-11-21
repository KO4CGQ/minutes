"""dev/dependabot.py: which Dependabot pull requests merge themselves, which release themselves, which wait for Jim."""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("dependabot", Path(__file__).parent.parent / "dev" / "dependabot.py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)

PATCH, MINOR, MAJOR = "version-update:semver-patch", "version-update:semver-minor", "version-update:semver-major"


@pytest.mark.parametrize("files, eco, kind, deps, ghsa, app, unreleased, verdict", [
    (["uv.lock"], "uv", PATCH, ["ruff"], "", False, False, "merge"),  # a dev tool
    (["uv.lock"], "uv", MINOR, ["pypdf"], "", True, False, "wait"),  # an app package: the next release
    (["uv.lock"], "uv", PATCH, ["pypdf"], "GHSA-xxxx-yyyy-zzzz", True, False, "release"),  # a high fix, 2 days old
    (["uv.lock"], "uv", PATCH, ["pypdf"], "GHSA-xxxx-yyyy-zzzz", True, True, "wait"),  # main has unreleased app work
    (["uv.lock"], "uv", MAJOR, ["ruff"], "", False, False, "wait"),  # a major version: Jim
    (["uv.lock", "pyproject.toml"], "uv", PATCH, ["ruff"], "", False, False, "wait"),  # more than the lock
    ([".github/workflows/tests.yml"], "github_actions", MINOR, ["actions/checkout"], "", False, False, "merge"),
    ([".github/workflows/tests.yml"], "github_actions", PATCH, ["someone/action"], "", False, False, "wait"),
    ([".github/workflows/tests.yml", "dev/checks.sh"], "github_actions", PATCH, ["actions/checkout"], "", False, False, "wait"),
    (["package.json"], "npm", PATCH, ["x"], "", False, False, "wait"),
])
def test_decide(files, eco, kind, deps, ghsa, app, unreleased, verdict):
    assert bot.decide(files, eco, kind, deps, ghsa, app, unreleased, cvss=7.5, age_hours=49)[0] == verdict


def test_a_security_fix_releases_itself_only_when_high_and_48_hours_old():
    """A poisoned "fix" (a hijacked maintainer's) is usually pulled within a day or two; a low one waits for Jim."""
    fix = (["uv.lock"], "uv", PATCH, ["pypdf"], "GHSA-xxxx-yyyy-zzzz", True, False)
    assert bot.decide(*fix, cvss=9.8, age_hours=2)[0] == "hold"
    assert bot.decide(*fix, cvss=9.8, age_hours=48)[0] == "release"
    assert bot.decide(*fix, cvss=5.3, age_hours=100)[0] == "wait"
    assert bot.decide(*fix, cvss=0.0, age_hours=100)[0] == "wait"  # no score: Jim
    assert bot.decide(*fix, cvss=9.8, age_hours=2, release_now=True)[0] == "release"  # the maintainer's label
    assert bot.decide(*fix[:-1], True, cvss=9.8, age_hours=2, release_now=True)[0] == "wait"  # never past unreleased work
    assert bot.decide(["uv.lock"], "uv", PATCH, ["pypdf"], "", True, False, release_now=True)[0] == "wait"  # not a fix


def test_release_section():
    """The next patch version; what waited under Unreleased goes with it; Unreleased starts empty again."""
    before = "# Changelog\n\nIntro.\n\n## Unreleased\n- Development only: a check.\n\n## 1.0.1 (2026-11-15)\n- **Security:** old.\n"
    new, text = bot.release(before, "1.0.1", "pypdf", "GHSA-xxxx-yyyy-zzzz", dt.date(2026, 11, 20))
    assert new == "1.0.2"
    assert text == ("# Changelog\n\nIntro.\n\n## Unreleased\n\n## 1.0.2 (2026-11-20)\n"
                    "- Development only: a check.\n### Security\n- pypdf updated (GHSA-xxxx-yyyy-zzzz). Update soon.\n"
                    "\n## 1.0.1 (2026-11-15)\n- **Security:** old.\n")
    assert bot.release("# C\n\n## Unreleased\n\n## 1.0.1 (x)\n- y\n", "1.0.1", "a", "G", dt.date(2026, 1, 2))[1] == \
        "# C\n\n## Unreleased\n\n## 1.0.2 (2026-01-02)\n### Security\n- a updated (G). Update soon.\n\n## 1.0.1 (x)\n- y\n"
