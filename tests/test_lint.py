"""Lint, here and on GitHub, as part of the tests, each tool at the version pinned in uv.lock (Dependabot bumps them):
ruff on the Python (its default rules), shellcheck on deploy/*.sh (setup, update and stage-sync run as root) and dev/*.sh, and
zizmor on the GitHub workflows (injection, tokens left on the runner, unpinned actions).

A newer ruff is tried on GitHub too (Tests → ruff-latest, warn only); locally, `uvx ruff@latest check .` shows what
the next version would flag.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def run(tool: str, *args: str) -> subprocess.CompletedProcess:
    exe = Path(sys.executable).with_name(tool)
    if not exe.exists():
        exe = shutil.which(tool)
        if not exe:
            pytest.skip(f"{tool} isn't installed (uv sync installs it)")
    return subprocess.run([str(exe), *args], cwd=ROOT, check=False, capture_output=True, text=True)


def test_ruff_passes():
    r = run("ruff", "check", "--no-cache", ".")
    assert r.returncode == 0, r.stdout[-4000:]


def test_shellcheck_passes():
    r = run("shellcheck", *sorted(str(p.relative_to(ROOT)) for d in ("deploy", "dev") for p in (ROOT / d).glob("*.sh")))
    assert r.returncode == 0, r.stdout[-4000:]


def test_zizmor_passes():
    r = run("zizmor", "--offline", "--no-progress", ".github/workflows")  # offline: no GitHub token needed
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]


def test_jobs_run_as_app_user_are_sandboxed():
    """Anything that runs as the app's user (not root) gets the web app's sandbox, or it could write more than its data."""
    for p in (ROOT / "deploy").glob("club-minutes*.service"):
        text = p.read_text()
        if "User=minutes" in text:
            assert "ProtectSystem=strict" in text and "ReadWritePaths=/var/lib/club-minutes" in text, p.name
