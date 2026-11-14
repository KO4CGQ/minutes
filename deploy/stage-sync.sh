#!/usr/bin/env bash
# Staging server only (docs/server-admin.md → Staging server): pull the live app's data, one way, then restart.
# Run as root by club-stage-sync.service, started by its nightly timer (skipped while paused on the Stage page)
# or at once by the Stage page's Sync now (it writes stage-sync-request in the data folder). The key in
# /etc/club-minutes/stage-sync-key is read-only on prod (rrsync -ro), so nothing here can change prod.
# Stage keeps its own: sign-ins and passkeys, email log, backups, update settings and these sync files.
# This runs as root in a folder the app's user can write: it never follows a link there (a planted one could
# otherwise make root read, overwrite or hand over a system file). It reads the request with O_NOFOLLOW and writes
# the status as a new file that's moved into place (a move replaces a link rather than writing through it).
main() {
  set -uo pipefail
  local DATA=${CLUB_MINUTES_DATA:-/var/lib/club-minutes} FROM=${STAGE_SYNC_FROM:?set STAGE_SYNC_FROM (root@prod-host:./) in /etc/club-minutes/env}
  local REQ=$DATA/stage-sync-request OUT=$DATA/stage-sync-status.json by=""
  if [ -e "$REQ" ] || [ -L "$REQ" ]; then
    by=$(python3 -c 'import os, sys; print(os.read(os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 40).decode(errors="replace").strip())' "$REQ" 2>/dev/null)
    rm -f "$REQ"
  elif [ -f "$DATA/stage-sync-paused" ]; then echo "Nightly sync paused on the Stage page."; return 0; fi
  local LOG; LOG=$(mktemp); local start; start=$(date +%s); local ok=false
  local owner=${STAGE_OWNER-minutes:minutes}  # empty in tests (chown needs root)
  if rsync -a --delete ${owner:+--chown=$owner} \
       -e "ssh -i ${STAGE_SYNC_KEY:-/etc/club-minutes/stage-sync-key} -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
       --exclude='auth.sqlite*' --exclude=email-log.jsonl --exclude=preview-outbox.jsonl --exclude=backups/ --exclude=before-upgrade/ \
       --exclude='backup-*.json' --exclude='update-*.json' --exclude='stage-sync-*' --exclude='*.lock' --exclude='*.tmp' \
       "$FROM" "$DATA/" >"$LOG" 2>&1 && ${STAGE_RESTART:-systemctl restart club-minutes} >>"$LOG" 2>&1; then
    ok=true; echo "Synced from the live app." >>"$LOG"
  fi
  local TMP; TMP=$(mktemp "$DATA/.stage-sync-status.XXXXXX")  # a new file, made by root
  python3 - "$TMP" "$LOG" "$ok" "$start" "$by" <<'PY'
import json, sys, time
out, logf, ok, start, by = sys.argv[1:]
json.dump({"ok": ok == "true", "started": int(start), "finished": int(time.time()), "by": by,
           "output": open(logf, errors="replace").read()[-4000:]}, open(out, "w"))
PY
  chmod 644 "$TMP"; [ -n "$owner" ] && chown "$owner" "$TMP"
  mv -f "$TMP" "$OUT"
  rm -f "$LOG"
  $ok
}
main "$@"; exit
