#!/usr/bin/env bash
# The checks GitHub runs on a pull request, here, before a push (docs/developers.md → Checks before a push):
#   1. the tests (with ruff, shellcheck, zizmor and the browser tests), as tests.yml;
#   2. pip-audit on the locked packages, as security.yml;
#   3. gitleaks at the version security.yml pins, checked against its checksum, on every branch and tag (as GitHub);
#   4. CodeQL with the security-extended queries, Python and JavaScript, as codeql.yml, on the committed files.
# CodeQL findings already reviewed as false positives are in dev/codeql-accepted.txt; anything else fails.
# Tools are kept in ~/.cache/club-minutes-checks (CodeQL: unpack GitHub's codeql-bundle-linux64 there, or set CODEQL).
# Usage: dev/checks.sh [--quick]   (--quick: skip the tests, for when they've just passed)
set -euo pipefail
cd "$(dirname "$0")/.."
CACHE=${CLUB_MINUTES_CHECKS:-$HOME/.cache/club-minutes-checks}
mkdir -p "$CACHE"
fail=0
step() { printf '\n== %s\n' "$1"; }

if [ "${1:-}" != "--quick" ]; then
  step "tests"
  uv run pytest -q || fail=1
fi

step "pip-audit (the locked packages)"
req=$(mktemp)
uv export --frozen --no-emit-project -o "$req" >/dev/null
uvx pip-audit -r "$req" --disable-pip --strict --progress-spinner off || fail=1
rm -f "$req"

step "gitleaks (every branch and tag, as GitHub's scan reads them all)"
v=$(sed -n 's/^ *GITLEAKS_VERSION: *//p' .github/workflows/security.yml | head -1)
sum=$(sed -n 's/^ *GITLEAKS_SHA256: *//p' .github/workflows/security.yml | head -1)
gl="$CACHE/gitleaks-$v"
if [ ! -x "$gl" ]; then
  curl -sSfL -o "$CACHE/gitleaks.tgz" "https://github.com/gitleaks/gitleaks/releases/download/v$v/gitleaks_${v}_linux_x64.tar.gz"
  echo "$sum  $CACHE/gitleaks.tgz" | sha256sum -c - >/dev/null
  tar xzf "$CACHE/gitleaks.tgz" -C "$CACHE" gitleaks && mv "$CACHE/gitleaks" "$gl" && rm "$CACHE/gitleaks.tgz"
fi
"$gl" git --redact --no-banner --log-opts="--branches --tags" . || fail=1  # not --all: that adds other worktrees' HEADs (the previews), which GitHub never sees

step "CodeQL (security-extended, the committed files)"
codeql=${CODEQL:-$CACHE/codeql/codeql}
if [ ! -x "$codeql" ]; then
  echo "CodeQL isn't installed: unpack codeql-bundle-linux64 from github/codeql-action's releases into $CACHE (or set CODEQL)."
  fail=1
else
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  git archive HEAD | tar -x -C "$work" --one-top-level=src  # what a push sends: no .venv, data or private files
  for lang in python javascript; do
    "$codeql" database create "$work/db-$lang" --language="$lang" --source-root="$work/src" --quiet --overwrite
    "$codeql" database analyze "$work/db-$lang" "codeql/$lang-queries:codeql-suites/$lang-security-extended.qls" \
      --format=sarif-latest --output="$work/$lang.sarif" --quiet
  done
  python3 - "$work" <<'EOF' || fail=1
import json, pathlib, sys
work = pathlib.Path(sys.argv[1])
accepted = set()
for line in pathlib.Path("dev/codeql-accepted.txt").read_text().splitlines():
    if line.strip() and not line.startswith("#"):
        rule, path, text = line.split("\t")[:3]
        accepted.add((rule, path, text.strip()))
new = []
for sarif in work.glob("*.sarif"):
    for run in json.loads(sarif.read_text())["runs"]:
        for r in run["results"]:
            loc = r["locations"][0]["physicalLocation"]
            path, n = loc["artifactLocation"]["uri"], loc["region"]["startLine"]
            text = (work / "src" / path).read_text().splitlines()[n - 1].strip()
            if (r["ruleId"], path, text) not in accepted:
                new.append(f"{r['ruleId']}\t{path}\t{text}\n    line {n}: {r['message']['text']}")
print("\n".join(new) if new else "No new findings.")
if new:
    print("\nFix them; or, if one is a false positive after a careful look, add its first line (tab-separated) to"
          " dev/codeql-accepted.txt with the reason, and dismiss it on GitHub the same way.")
sys.exit(1 if new else 0)
EOF
fi

printf '\n'
[ "$fail" = 0 ] && echo "All checks passed." || { echo "Some checks failed (above)."; exit 1; }
