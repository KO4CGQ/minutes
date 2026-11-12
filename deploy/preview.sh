#!/usr/bin/env bash
# Run a branch as a preview on this machine, reachable from the LAN, without touching the live server.
#
#   deploy/preview.sh BRANCH [NAME]   start or update the preview of BRANCH, the local one if it exists, else origin's
#                                     (prints its address and a sign-in link)
#                                     NAME (default: the branch) keeps one address while it follows different branches,
#                                     e.g. `deploy/preview.sh my-branch dev` for a single "dev" preview of the latest work
#   DEMO=1 deploy/preview.sh BRANCH demo   the same with the demo club (demo/build.py) instead of the club's data
#                                     (signs in as its admin, Jane Doe N0JANE)
#   deploy/preview.sh --stop NAME     stop it and delete its copy
#   deploy/preview.sh --list          show running previews
#
# Each preview gets its own checkout and its own copy of data/ (meetings, roster cache, chow schedule;
# never the login database), with made-up contact details in place of the real ones. It runs in preview mode: email goes to an outbox page instead of being sent,
# and roster edits go to a local copy of the sheet. Ports start at 8100.
set -euo pipefail
REPO=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)
BASE=${CLUB_MINUTES_PREVIEWS:-$(dirname "$REPO")/previews}
HOST_IP=$(hostname -I | awk '{print $1}')
unit() { echo "club-minutes-preview-$(echo "$1" | tr -c 'a-zA-Z0-9-\n' '-')"; }

case "${1:-}" in
  --list)
    systemctl --user list-units --no-legend 'club-minutes-preview-*' | awk '{print $1, $3, $4}'
    for d in "$BASE"/*/; do [ -f "$d/port" ] && echo "$(basename "$d"): http://$HOST_IP:$(cat "$d/port")"; done
    exit 0 ;;
  --stop)
    B=${2:?branch}; systemctl --user stop "$(unit "$B")" 2>/dev/null || true
    git -C "$REPO" worktree remove --force "$BASE/$B/app" 2>/dev/null || true
    rm -rf "${BASE:?}/$B"; echo "Stopped and removed the preview of $B"; exit 0 ;;
  ""|-*) sed -n '2,15p' "$0"; exit 1 ;;
esac

B=$1; NAME=${2:-$1}; DIR=$BASE/$NAME; U=$(unit "$NAME")
mkdir -p "$DIR"
git -C "$REPO" fetch -q origin
# The local branch if there is one (work that hasn't been pushed yet), else the one on GitHub.
REF="origin/$B"; git -C "$REPO" rev-parse -q --verify "refs/heads/$B" >/dev/null && REF=$B
echo "Previewing $REF ($(git -C "$REPO" rev-parse --short "$REF"))"
# the preview's checkout is a throwaway copy: any edits made in it are discarded (and a failure stops here)
if [ -d "$DIR/app" ]; then git -C "$DIR/app" checkout -q --force --detach "$REF"; git -C "$DIR/app" reset -q --hard "$REF"
else git -C "$REPO" worktree add -q --detach "$DIR/app" "$REF"; fi

# Older branches have no preview mode: they would send real email and write to the real roster sheet.
if ! grep -q CLUB_MINUTES_PREVIEW "$DIR/app/src/club_minutes/club.py"; then
  echo "Refusing: $B has no preview mode (it would send real email). Rebase it on main first." >&2
  git -C "$REPO" worktree remove --force "$DIR/app"; rm -rf "${DIR:?}"; exit 1
fi

# A fresh copy of the data each time; the preview keeps its own sign-ins (auth.sqlite) between updates.
mkdir -p "$DIR/data"
find "$DIR/data" -mindepth 1 -maxdepth 1 ! -name auth.sqlite -exec rm -rf {} +
(cd "$DIR/app" && uv sync -q --frozen)
if [ -n "${DEMO:-}" ]; then  # the demo club, built fresh by the branch's own demo/build.py: none of the club's data
  T=$(mktemp -d); (cd "$DIR/app" && .venv/bin/python demo/build.py "$T/out" >/dev/null)
  cp -r "$T/out/data/." "$DIR/data/"; cp "$T/out/club.yaml" "$DIR/club.yaml"; rm -rf "$T"
  CLUB_MINUTES_PREVIEW_USER=N0JANE
else
# Everything in data/ (meetings, roster cache, chow schedule, ...) except the live login database.
find "$REPO/data" -mindepth 1 -maxdepth 1 ! -name 'auth.sqlite*' ! -name 'preview-*' -exec cp -r {} "$DIR/data/" \;
[ -f "$REPO/private/bylaws.md" ] && cp "$REPO/private/bylaws.md" "$DIR/data/"
[ -d "$REPO/private/branding" ] && cp -r "$REPO/private/branding" "$DIR/data/"  # the club's logo (admin → Site), kept out of the repo
cp "$REPO/club.yaml" "$DIR/club.yaml"
# Made-up contact details for everyone (see the script for which members to test sharing with).
[ -f "$DIR/data/roster.json" ] && python3 "$REPO/deploy/preview-fake.py" "$DIR/data"
fi
chmod -R go-rwx "$DIR/data" "$DIR/club.yaml"

if [ -f "$DIR/port" ]; then PORT=$(cat "$DIR/port")
else PORT=8100; while ss -ltn | grep -q ":$PORT " || grep -qx "$PORT" "$BASE"/*/port 2>/dev/null; do PORT=$((PORT+1)); done
  echo "$PORT" > "$DIR/port"; fi

systemctl --user stop "$U" 2>/dev/null || true
systemctl --user reset-failed "$U" 2>/dev/null || true
ENV=(CLUB_MINUTES_PREVIEW="$B" CLUB_MINUTES_DATA="$DIR/data" CLUB_MINUTES_CONFIG="$DIR/club.yaml" CLUB_MINUTES_ROOT="$DIR/app")
# To try backups in a preview, run this script with CLUB_MINUTES_BACKUP_PASSPHRASE set (never a real one: previews hold copies of the minutes).
[ -n "${CLUB_MINUTES_BACKUP_PASSPHRASE:-}" ] && ENV+=(CLUB_MINUTES_BACKUP_PASSPHRASE="$CLUB_MINUTES_BACKUP_PASSPHRASE")
systemd-run --user -q --unit "$U" --collect -p WorkingDirectory="$DIR/app" \
  $(printf -- '--setenv=%s ' "${ENV[@]}") "$DIR/app/.venv/bin/club-minutes" serve --host 0.0.0.0 --port "$PORT"
for _ in $(seq 20); do curl -s -o /dev/null "http://127.0.0.1:$PORT/login" && break; sleep 0.5; done

echo "Preview of $B ($(git -C "$DIR/app" log --oneline -1)): http://$HOST_IP:$PORT"
echo "Sign in (one use, 15 minutes):"
(cd "$DIR/app" && env "${ENV[@]}" .venv/bin/club-minutes login-link "${CLUB_MINUTES_PREVIEW_USER:?set CLUB_MINUTES_PREVIEW_USER to your callsign}" --base-url "http://$HOST_IP:$PORT" | tail -1)
