"""The code passes ruff, the version pinned in uv.lock, with its default rules: here and on GitHub, as part of the tests.

A newer ruff is tried on GitHub too (Tests → ruff-latest, warn only), and Dependabot's weekly "bump ruff" pull request
moves the pin; locally, `uvx ruff@latest check .` shows what the next version would flag.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_ruff_passes():
    ruff = Path(sys.executable).with_name("ruff")
    if not ruff.exists():
        ruff = shutil.which("ruff")
        if not ruff:
            pytest.skip("ruff isn't installed (uv sync installs it)")
    r = subprocess.run([str(ruff), "check", "--no-cache", "."], cwd=Path(__file__).parents[1],
                       check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-4000:]
