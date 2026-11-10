#!/usr/bin/env bash
# Update and restart. Run as root. TAG=vX.Y.Z installs that release (what the app's Updates page and the nightly
# auto-update ask for); without it, the latest main (or BRANCH=...), as by hand. If the app doesn't answer
# afterwards, it goes back to the version it had and exits non-zero.
# git reset replaces this very file while it runs, and bash may have read only part of it; so everything
# is inside main(), which bash reads in full before running any of it.
main() {
  set -euo pipefail
  local APP=/opt/w8lbz-minutes BRANCH=${BRANCH:-main} TAG=${TAG:-}
  cd "$APP"
  install_and_restart() {
    sudo -u minutes env UV_CACHE_DIR=/var/lib/w8lbz-minutes-home/.cache/uv uv sync -q --frozen --no-dev
    # Install the app's systemd units (new ones arrive with updates) and turn on its timers and watchers.
    install -m 644 "$APP"/deploy/w8lbz-minutes*.service "$APP"/deploy/w8lbz-minutes*.timer "$APP"/deploy/w8lbz-minutes*.path \
      /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable -q w8lbz-minutes
    local t
    for t in "$APP"/deploy/w8lbz-minutes*.timer "$APP"/deploy/w8lbz-minutes*.path; do systemctl enable -q --now "$(basename "$t")"; done
    systemctl restart w8lbz-minutes
  }
  healthy() {  # the sign-in page answers within a minute
    local i
    for i in $(seq 30); do
      "$APP/.venv/bin/python" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=3)" 2>/dev/null && return 0
      sleep 2
    done
    return 1
  }
  local before; before=$(sudo -u minutes git rev-parse HEAD)
  sudo -u minutes git fetch -q --tags origin
  if [ -n "$TAG" ]; then
    [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Not a release tag: $TAG" >&2; exit 2; }
    sudo -u minutes git reset -q --hard "refs/tags/$TAG"
  else
    sudo -u minutes git reset -q --hard "origin/$BRANCH"
  fi
  install_and_restart
  if ! healthy; then
    echo "The app didn't come back up after the update: going back to $(sudo -u minutes git log --oneline -1 "$before")" >&2
    sudo -u minutes git reset -q --hard "$before"
    install_and_restart
    if healthy; then echo "Rolled back: the app is running the version it had before." >&2
    else echo "Still not answering after rolling back: see journalctl -u w8lbz-minutes" >&2; fi
    exit 1
  fi
  echo "Now at $(sudo -u minutes git log --oneline -1)"
}
main "$@"; exit
