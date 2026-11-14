#!/usr/bin/env bash
# What this branch changes since BASE (default origin/main), the working tree included: prints "docs" if it's only
# Markdown and docs/ (the guides and their screenshots), otherwise "code". For "docs", dev/checks.sh and the workflows
# run just the quick checks (pip-audit, gitleaks, the changelog, `pytest -m docs`); for "code", everything.
# Nothing changed counts as "code", so an unclear case always gets every check.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
changed=$( { git diff --name-only "$(git merge-base "${1:-origin/main}" HEAD)"; git ls-files -o --exclude-standard; } )
if [ -n "$changed" ] && ! grep -qvE '^docs/|\.md$' <<<"$changed"; then echo docs; else echo code; fi
