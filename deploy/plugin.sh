#!/usr/bin/env bash
# Plugins from outside the app (server admin guide → Plugins). Run as root.
#   plugin.sh add NAME URL [KEY]   install NAME's newest release (a vX.Y.Z tag) from a Git repo, e.g.
#                                  git+ssh://git@git.example.org/OWNER/REPO.git#subdirectory=plugin; KEY: a read-only
#                                  deploy key for a private repo, copied to /etc/club-minutes/plugin-keys/NAME
#   plugin.sh remove NAME          uninstall it and forget it
#   plugin.sh list                 what's installed, at which release
#   plugin.sh install [newest]     (update.sh) put every plugin back after the app's install: at its release, or newest
# The list is root's (/etc/club-minutes/plugins: NAME DIST URL TAG), outside the app's files, so the app's user can't
# change what gets installed. A plugin does nothing until an admin ticks it on Site → Features.
main() {
  set -Eeuo pipefail
  trap 'echo "Stopped: \"$BASH_COMMAND\" failed (exit $?)." >&2' ERR
  local APP=/opt/club-minutes ETC=/etc/club-minutes
  local LIST=$ETC/plugins KEYS=$ETC/plugin-keys
  export UV_CACHE_DIR=/var/cache/club-minutes-uv UV_PYTHON_INSTALL_DIR="$APP/.uv-python"  # root's, as update.sh's
  lock() {  # one updater at a time, with update.sh (it holds the lock itself while it runs plugin.sh install)
    [ -z "${CLUB_MINUTES_UPDATE_LOCKED:-}" ] || return 0
    exec 9>/run/club-minutes-update.lock
    flock -n 9 || { echo "Another update or plugin change is running; try again when it has finished." >&2; exit 1; }
  }
  retry() {  # the network steps: three tries, a short pause between
    local n; for n in 1 2 3; do "$@" && return 0; echo "Try $n of 3 failed." >&2; [ "$n" = 3 ] || sleep 5; done; return 1
  }
  git_env() {  # $1 NAME: its key, if it has one; host keys kept beside the keys (the first connection, at add, sets
    # them; a changed one is refused). Never a prompt: the nightly update runs with no one there.
    local key=""; [ -f "$KEYS/$1" ] && key="-i $KEYS/$1 -o IdentitiesOnly=yes"
    export GIT_SSH_COMMAND="ssh $key -o BatchMode=yes -o ConnectTimeout=20 -o UserKnownHostsFile=$KEYS/known_hosts -o StrictHostKeyChecking=accept-new"
    install -d -m 700 "$KEYS"
  }
  newest() {  # $1 NAME, $2 URL: the highest vX.Y.Z tag in its repo (release candidates aren't releases)
    git_env "$1"
    local repo=${2#git+}; repo=${repo%%#*}
    timeout 120 git ls-remote --tags --refs "$repo" | sed -n 's|.*refs/tags/\(v[0-9]*\.[0-9]*\.[0-9]*\)$|\1|p' | sort -V | tail -1
  }
  put() {  # $1 NAME, $2 URL, $3 TAG: install that release into the app's environment
    git_env "$1"
    local at=${2%%#*}@$3
    [[ "$2" == *#* ]] && at+="#${2#*#}"
    # never moving the app's own packages from the versions it was tested with (its uv.lock)
    # and the app itself pinned too, so a plugin that depends on club-minutes can't pull another copy from PyPI
    local pins; pins=$(mktemp)
    { (cd "$APP" && uv export -q --frozen --no-dev --no-hashes --no-emit-project -o "$pins") &&
      "$APP/.venv/bin/python" -c 'from importlib.metadata import version; print("club-minutes==" + version("club-minutes"))' >>"$pins"; } || { rm -f "$pins"; return 1; }
    retry timeout 600 uv pip install -q --python "$APP/.venv/bin/python" -c "$pins" "$at" || { rm -f "$pins"; return 1; }
    rm -f "$pins"
  }
  dist_of() {  # $1 NAME: the installed package whose entry point is NAME
    "$APP/.venv/bin/python" -c 'import sys
from importlib.metadata import entry_points
print(next((e.dist.name for e in entry_points(group="club_minutes.plugins") if e.name == sys.argv[1]), ""))' "$1"
  }
  local cmd=${1:-}
  case "$cmd" in
    add)
      local name=${2:-} url=${3:-} key=${4:-}
      lock
      [[ "$name" =~ ^[a-z0-9]+$ ]] || { echo "NAME: lowercase letters and digits, the plugin's own name" >&2; exit 2; }
      [[ "$url" =~ ^git\+(ssh|https|file):// ]] || { echo "URL: git+ssh://…, git+https://… (or git+file://… for a test)" >&2; exit 2; }
      if [ -f "$LIST" ] && grep -q "^$name " "$LIST"; then echo "$name is installed already: remove it first" >&2; exit 1; fi
      [[ "$url" =~ ^git\+https://[^/]*@ ]] && { echo "No token in the URL (other programs would see it): use git+ssh:// and a deploy key" >&2; exit 2; }
      if [ -n "$key" ]; then install -d -m 700 "$KEYS"; install -m 600 "$key" "$KEYS/$name"; fi
      # shellcheck disable=SC2064  # expanded now on purpose: KEYS is local to main, gone when EXIT runs; NAME is checked above
      trap "rm -f $KEYS/$name" EXIT  # a failed add leaves nothing behind
      local tag; tag=$(newest "$name" "$url")
      [ -n "$tag" ] || { echo "No release (vX.Y.Z tag) found in $url" >&2; exit 1; }
      put "$name" "$url" "$tag"
      local dist; dist=$(dist_of "$name")
      [ -n "$dist" ] || { echo "Installed, but no plugin named $name is in it: check NAME (uv pip uninstall what it installed)" >&2; exit 1; }
      trap - EXIT
      echo "$name $dist $url $tag" >>"$LIST"
      chmod 600 "$LIST"
      systemctl restart club-minutes
      echo "Installed $name $tag. Turn it on at Admin → Site → Features." ;;
    remove)
      local name=${2:-} line
      lock
      line=$(grep "^$name " "$LIST" 2>/dev/null) || { echo "$name isn't installed by this command" >&2; exit 1; }
      uv pip uninstall -q --python "$APP/.venv/bin/python" "$(cut -d' ' -f2 <<<"$line")"
      grep -v "^$name " "$LIST" >"$LIST.new" || true
      mv "$LIST.new" "$LIST"; chmod 600 "$LIST"
      rm -f "$KEYS/$name"
      systemctl restart club-minutes
      echo "Removed $name. Its data stays in the data folder (plugins/$name.json)." ;;
    list) [ -s "$LIST" ] && awk '{print $1, $4, $3}' "$LIST" || echo "No plugins installed by this command." ;;
    install)
      lock
      [ -s "$LIST" ] || return 0
      local name dist url tag new out=""
      while read -r name dist url tag <&3; do  # fd 3: ssh and uv may read stdin
        new=$tag
        if [ "${2:-}" = newest ]; then
          new=$(newest "$name" "$url" || true)
          if [ -z "$new" ]; then echo "Plugin $name: couldn't reach its releases; keeping $tag" >&2; new=$tag; fi
        fi
        # one plugin that won't install never stops the app's update, or a rollback: it stays out, and says so
        if put "$name" "$url" "$new"; then [ "$new" = "$tag" ] || echo "Plugin $name: $tag → $new"
        elif [ "$new" != "$tag" ] && put "$name" "$url" "$tag"; then echo "Plugin $name: $new didn't install; kept $tag" >&2; new=$tag
        else echo "Plugin $name: couldn't be installed, so it isn't running (plugin.sh install puts it back)" >&2; new=$tag; fi
        out+="$name $dist $url $new"$'\n'
      done 3<"$LIST"
      printf %s "$out" >"$LIST.new"; chmod 600 "$LIST.new"; mv "$LIST.new" "$LIST" ;;
    *) sed -n '2,10p' "$0" >&2; exit 2 ;;
  esac
}
main "$@"; exit
