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
  # Root owns the code, so what runs as 'minutes' can't edit what runs as root here. An install from before
  # (owned by minutes) is handed over once, before root runs git in it. The venv goes too: its Python may live in
  # the minutes user's home, which that user can edit.
  local handed=""
  if find "$APP" -user minutes -print -quit | grep -q .; then
    echo "Making root the owner of $APP (once; the app's user can no longer change its code)."
    chown -R root:root "$APP"
    handed=1
  fi
  cd "$APP"
  install_app() {  # $1 "newest": plugins move to their newest releases; otherwise they go back in at the ones they had
    if [ -n "$handed" ]; then rm -rf "$APP/.venv"; handed=""; fi  # rebuilt just below, with root's Python
    # root's own uv cache and Python (readable by 'minutes', writable only by root)
    env UV_CACHE_DIR=/var/cache/club-minutes-uv UV_PYTHON_INSTALL_DIR="$APP/.uv-python" uv sync -q --frozen --no-dev
    # uv sync leaves only the app's own packages: the plugins installed with deploy/plugin.sh go back in
    if [ -x "$APP/deploy/plugin.sh" ]; then "$APP/deploy/plugin.sh" install "${1:-}"; fi
    # Install the app's systemd units (new ones arrive with updates) and turn on its timers and watchers.
    install -m 644 "$APP"/deploy/club-minutes*.service "$APP"/deploy/club-minutes*.timer "$APP"/deploy/club-minutes*.path \
      /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable -q club-minutes
    local t
    for t in "$APP"/deploy/club-minutes*.timer "$APP"/deploy/club-minutes*.path; do systemctl enable -q --now "$(basename "$t")"; done
  }
  install_and_restart() { install_app "${1:-}"; systemctl restart club-minutes; }
  # Before starting: a plugin that's on but won't load in this version is turned off, or makes the update wait
  # (exit 1): each plugin's choice on Site → Features (it says which, for the Updates page)
  plugins_fit() {
    [ -x "$APP/deploy/plugin.sh" ] || return 0
    sudo -u minutes env CLUB_MINUTES_CONFIG=/etc/club-minutes/club.yaml CLUB_MINUTES_DATA="$DATA" \
      "$APP/.venv/bin/club-minutes" plugins-check
  }
  healthy() {  # the sign-in page answers within 5 minutes (startup takes seconds; the margin is for a slow, busy server)
    for _ in $(seq 100); do
      "$APP/.venv/bin/python" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=3)" 2>/dev/null && return 0
      sleep 2
    done
    return 1
  }
  # a version from before root owned the code (1.1 and older) runs git and uv as 'minutes': give it back its folder,
  # or its own updater can't run git there ("dubious ownership"): after a rollback to one, or going back to one on purpose
  give_back() { if grep -qE '^ *sudo -u minutes git fetch' deploy/update.sh; then chown -R minutes:minutes "$APP"; fi; }
  local before; before=$(git rev-parse HEAD)
  # The data's shape before (the new version may upgrade it at start: see src/club_minutes/upgrade.py)
  local DATA=${CLUB_MINUTES_DATA:-/var/lib/club-minutes} was
  was=$(cat "$DATA/data-version" 2>/dev/null || echo 1)
  # --force: GitHub's tags are the truth (the server resets to them anyway); without it, a tag moved on GitHub
  # makes every fetch fail ("would clobber existing tag"), as it did for 0.17.0
  git fetch -q --tags --force origin
  if [ -n "$TAG" ]; then
    [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ ]] || { echo "Not a release tag: $TAG" >&2; exit 2; }
    git reset -q --hard "refs/tags/$TAG"
  else
    git reset -q --hard "origin/$BRANCH"
  fi
  local LIST=/etc/club-minutes/plugins
  if [ -f "$LIST" ]; then cp -p "$LIST" "$LIST.before"; fi  # the plugins' releases, for going back
  install_app newest
  local SITE=$DATA/site-settings.json held="" rc=0
  # check may turn plugins off there: a rollback puts it back
  if [ -f "$SITE" ]; then sudo -u minutes cp -p "$SITE" "$SITE.before-update"; fi
  plugins_fit || rc=$?
  if [ "$rc" = 1 ]; then held=1
  elif [ "$rc" != 0 ]; then echo "The plugin check itself failed (exit $rc); updating anyway, plugins as they were." >&2; fi
  if [ -n "$held" ] || ! { systemctl restart club-minutes; healthy; }; then
    if [ -n "$held" ]; then echo "Waiting for a plugin (above): going back to $(git log --oneline -1 "$before")" >&2
    else
      echo "The app didn't come back up after the update: going back to $(git log --oneline -1 "$before")" >&2
      # why, for the Updates page and its email (often a club.yaml setting the new version needs)
      journalctl -u club-minutes --since "-10min" --no-pager -o cat 2>/dev/null | grep -E "Error|error:" | tail -1 | sed 's/^/Why: /' >&2 || true
    fi
    systemctl stop club-minutes
    # if it upgraded the data, put back the copy from before (the old version can't read the new shape)
    sudo -u minutes env CLUB_MINUTES_DATA="$DATA" "$APP/.venv/bin/club-minutes" data-undo "$was" >&2 || true
    git reset -q --hard "$before"
    if [ -f "$LIST.before" ]; then mv "$LIST.before" "$LIST"; fi
    if [ -f "$SITE.before-update" ]; then sudo -u minutes mv "$SITE.before-update" "$SITE"; fi
    install_and_restart
    give_back  # after the install: what root's uv sync wrote goes back too
    if healthy; then echo "Rolled back: the app is running the version it had before." >&2
    else echo "Still not answering after rolling back: see journalctl -u club-minutes" >&2; fi
    exit 1
  fi
  rm -f "$LIST.before" "$SITE.before-update"
  give_back
  echo "Now at $(git log --oneline -1)"
}
main "$@"; exit
