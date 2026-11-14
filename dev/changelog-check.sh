#!/usr/bin/env bash
# A changelog line per change: fails if this branch changes the app (src/) or its install (deploy/) since BASE
# without touching CHANGELOG.md (a line under Unreleased). On GitHub (changelog.yml) BASE is the pull request's base
# branch and a `no-changelog` label skips it (a typo fix, a test); here (dev/checks.sh) it's origin/main.
# Usage: dev/changelog-check.sh [BASE]
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
base=${1:-origin/main}
changed=$(git diff --name-only "$base"...HEAD)
app=$(grep -E '^(src|deploy)/' <<<"$changed" || true)
if [ -n "$app" ] && ! grep -qx 'CHANGELOG.md' <<<"$changed"; then
  echo "These change the app but CHANGELOG.md has no new line (add one under Unreleased, or label the PR no-changelog):"
  while read -r f; do echo "  $f"; done <<<"$app"
  exit 1
fi
echo "ok: changelog"
