#!/usr/bin/env bash
# Run as root by update.sh (and so by setup.sh) after the code changes, from the version being installed: brings the
# server up to what that version needs. Today: the Debian packages listed in deploy/system-packages, installed if
# missing. Exits non-zero if one can't be installed, and the update then goes back to the version it had.
set -euo pipefail
APP=${APP:-/opt/club-minutes}
mapfile -t want < <(sed 's/#.*//' "$APP/deploy/system-packages" | tr -s ' \t' '\n' | grep .)
missing=()
for p in "${want[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
[ ${#missing[@]} -eq 0 ] && exit 0
echo "Installing what this version needs from Debian: ${missing[*]}"
apt=(apt-get -o DPkg::Lock::Timeout=120 -y -qq)
for n in 1 2 3; do  # the network: three tries
  if "${apt[@]}" update >/dev/null && DEBIAN_FRONTEND=noninteractive "${apt[@]}" install "${missing[@]}" >/dev/null; then exit 0; fi
  echo "Try $n of 3 failed." >&2; [ "$n" = 3 ] || sleep 5
done
echo "Couldn't install ${missing[*]} (sudo apt-get install ${missing[*]} shows why)." >&2
exit 1
