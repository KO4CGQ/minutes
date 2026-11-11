#!/usr/bin/env bash
# Staging server only (docs/server-admin.md → Staging server): pull the live app's data, one way, then restart.
# Run as root by w8lbz-stage-sync.service, started by its nightly timer (skipped while paused on the Stage page)
# or at once by the Stage page's Sync now (it writes stage-sync-request in the data folder). The key in
# /etc/w8lbz-minutes/stage-sync-key is read-only on prod (rrsync -ro), so nothing here can change prod.
# Stage keeps its own: sign-ins and passkeys, email log, backups, update settings and these sync files.
main() {
  set -uo pipefail
  local DATA=${W8LBZ_DATA:-/var/lib/w8lbz-minutes} FROM=${STAGE_SYNC_FROM:?set STAGE_SYNC_FROM (root@prod-host:./) in /etc/w8lbz-minutes/env}
  local REQ=$DATA/stage-sync-request OUT=$DATA/stage-sync-status.json by=""
  if [ -f "$REQ" ]; then by=$(head -c 40 "$REQ"); rm -f "$REQ"
  elif [ -f "$DATA/stage-sync-paused" ]; then echo "Nightly sync paused on the Stage page."; return 0; fi
  local LOG; LOG=$(mktemp); local start; start=$(date +%s); local ok=false
  local owner=${STAGE_OWNER-minutes:minutes}  # empty in tests (chown needs root)
  if rsync -a --delete ${owner:+--chown=$owner} \
       -e "ssh -i ${STAGE_SYNC_KEY:-/etc/w8lbz-minutes/stage-sync-key} -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
       --exclude='auth.sqlite*' --exclude=email-log.jsonl --exclude=preview-outbox.jsonl --exclude=backups/ \
       --exclude='backup-*.json' --exclude='update-*.json' --exclude='stage-sync-*' --exclude='*.lock' --exclude='*.tmp' \
       "$FROM" "$DATA/" >"$LOG" 2>&1 && ${STAGE_RESTART:-systemctl restart w8lbz-minutes} >>"$LOG" 2>&1; then
    ok=true; echo "Synced from the live app." >>"$LOG"
  fi
  python3 - "$OUT" "$LOG" "$ok" "$start" "$by" <<'PY'
import json, sys, time
out, logf, ok, start, by = sys.argv[1:]
json.dump({"ok": ok == "true", "started": int(start), "finished": int(time.time()), "by": by,
           "output": open(logf, errors="replace").read()[-4000:]}, open(out, "w"))
PY
  [ -n "$owner" ] && chown "$owner" "$OUT"
  rm -f "$LOG"
  $ok
}
main "$@"; exit
