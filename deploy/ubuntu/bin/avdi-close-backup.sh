#!/bin/sh
# Weekday close backup (systemd timer, 16:25 America/New_York, after AVDI's 16:10 close processing).
#  1. skip non-trading days;  2. wait (max ~25 min) for today's close job;  3. take a consistent sqlite snapshot inside
#  the runtime container (online backup API: no lock, no interruption of paper trading);  4. verify it independently
#  (sha256 + integrity_check + ledger AND shadow DB present);  5. best-effort off-host trigger.
# A failure here NEVER affects the Champion: everything runs in a separate oneshot unit.
BASE="$HOME/avdi-runtime"
H="$BASE/health"
mkdir -p "$H"
RT="docker exec avdi-runtime python automation/avdi_runtime.py"
log() { echo "$(date -u +%FT%TZ) $*" >> "$H/backup.log"; }

TODAY="$(TZ=America/New_York date +%F)"
TRADING="$($RT schedule "$TODAY" 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["trading_day"])' 2>/dev/null)"
if [ "$TRADING" != "True" ]; then
  log "skip: $TODAY is not a trading day (or runtime unreachable: '$TRADING')"
  exit 0
fi

DONE=""
for i in $(seq 1 25); do
  DONE="$(docker exec avdi-runtime python -c "
import json, datetime as dt
from zoneinfo import ZoneInfo
s = json.load(open('/data/case1/paper/runtime/runtime_state.json'))
at = (s.get('last_close') or {}).get('at')
print(bool(at) and dt.datetime.fromisoformat(at).astimezone(ZoneInfo('America/New_York')).date().isoformat() == '$TODAY')
" 2>/dev/null)"
  [ "$DONE" = "True" ] && break
  sleep 60
done
[ "$DONE" = "True" ] && log "close job for $TODAY confirmed done" || log "WARN: close job for $TODAY not confirmed after ~25 min; backing up anyway"

RES="$($RT backup-live 2>/dev/null)"
NAME="$(printf '%s' "$RES" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["at"] if d.get("ok") else "")' 2>/dev/null)"
if [ -z "$NAME" ]; then
  log "FAIL: backup-live did not produce an ok backup: $RES"
  echo "$(date -u +%FT%TZ) backup failed" > "$H/ALERT_BACKUP"
  logger -t avdi-backup -p daemon.err "close backup FAILED"
  exit 1
fi

VER="$($RT verify-backup "$NAME" --max-age-hours 2 2>/dev/null)"
VOK="$(printf '%s' "$VER" | python3 -c 'import sys,json; print(json.load(sys.stdin)["ok"])' 2>/dev/null)"
if [ "$VOK" != "True" ]; then
  log "FAIL: verification of $NAME failed: $VER"
  echo "$(date -u +%FT%TZ) backup $NAME failed verification" > "$H/ALERT_BACKUP"
  logger -t avdi-backup -p daemon.err "close backup $NAME FAILED verification"
  exit 1
fi
rm -f "$H/ALERT_BACKUP"
log "OK backup $NAME verified (sha256 + integrity_check + ledger + shadow)"
printf '{"backup": "%s", "verified_at": "%s"}\n' "$NAME" "$(date -u +%FT%TZ)" > "$H/last_backup.json"

sh "$BASE/bin/avdi-offhost-dispatch.sh" "$NAME" || true
exit 0
