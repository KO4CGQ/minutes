#!/usr/bin/env bash
# Update and restart. Run as root. TAG=vX.Y.Z installs that release (what the app's Updates page and the nightly
# auto-update ask for); without it, the latest main (or BRANCH=...), as by hand. If the app doesn't answer
# afterwards, it goes back to the version it had and exits non-zero.
# git reset replaces this very file while it runs, and bash may have read only part of it; so everything
# is inside main(), which bash reads in full before running any of it.
main() {
  set -Eeuo pipefail
  # set -e stops silently, and the git commands are quiet: say what failed, for the Updates page and the email
  trap 'echo "Stopped: \"$BASH_COMMAND\" failed (exit $?)." >&2' ERR
  local APP=/opt/club-minutes BRANCH=${BRANCH:-main} TAG=${TAG:-}
  cd "$APP"
  install_and_restart() {
    sudo -u minutes env UV_CACHE_DIR=/var/lib/club-minutes-home/.cache/uv uv sync -q --frozen --no-dev
    # Install the app's systemd units (new ones arrive with updates) and turn on its timers and watchers.
    install -m 644 "$APP"/deploy/club-minutes*.service "$APP"/deploy/club-minutes*.timer "$APP"/deploy/club-minutes*.path \
      /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable -q club-minutes
    local t
    for t in "$APP"/deploy/club-minutes*.timer "$APP"/deploy/club-minutes*.path; do systemctl enable -q --now "$(basename "$t")"; done
    systemctl restart club-minutes
  }
  healthy() {  # the sign-in page answers within 5 minutes (startup takes seconds; the margin is for a slow, busy server)
    for _ in $(seq 100); do
      "$APP/.venv/bin/python" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=3)" 2>/dev/null && return 0
      sleep 2
    done
    return 1
  }
  local before; before=$(sudo -u minutes git rev-parse HEAD)
  # The data's shape before (the new version may upgrade it at start: see src/club_minutes/upgrade.py)
  local DATA=${CLUB_MINUTES_DATA:-/var/lib/club-minutes} was
  was=$(cat "$DATA/data-version" 2>/dev/null || echo 1)
  # --force: GitHub's tags are the truth (the server resets to them anyway); without it, a tag moved on GitHub
  # makes every fetch fail ("would clobber existing tag"), as it did for 0.17.0
  sudo -u minutes git fetch -q --tags --force origin
  if [ -n "$TAG" ]; then
    [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Not a release tag: $TAG" >&2; exit 2; }
    sudo -u minutes git reset -q --hard "refs/tags/$TAG"
  else
    sudo -u minutes git reset -q --hard "origin/$BRANCH"
  fi
  install_and_restart
  if ! healthy; then
    echo "The app didn't come back up after the update: going back to $(sudo -u minutes git log --oneline -1 "$before")" >&2
    # why, for the Updates page and its email (often a club.yaml setting the new version needs)
    journalctl -u club-minutes --since "-10min" --no-pager -o cat 2>/dev/null | grep -E "Error|error:" | tail -1 | sed 's/^/Why: /' >&2 || true
    systemctl stop club-minutes
    # if it upgraded the data, put back the copy from before (the old version can't read the new shape)
    sudo -u minutes env CLUB_MINUTES_DATA="$DATA" "$APP/.venv/bin/club-minutes" data-undo "$was" >&2 || true
    sudo -u minutes git reset -q --hard "$before"
    install_and_restart
    if healthy; then echo "Rolled back: the app is running the version it had before." >&2
    else echo "Still not answering after rolling back: see journalctl -u club-minutes" >&2; fi
    exit 1
  fi
  echo "Now at $(sudo -u minutes git log --oneline -1)"
}
main "$@"; exit
