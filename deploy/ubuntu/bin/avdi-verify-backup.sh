#!/bin/sh
# Nightly verification (systemd timer, 22:15 America/New_York): the latest local backup must exist, be recent, pass
# sha256 + integrity_check, and contain both the ledger and the shadow DB. Read-only.
BASE="$HOME/avdi-runtime"
H="$BASE/health"
mkdir -p "$H"
RT="docker exec avdi-runtime python automation/avdi_runtime.py"
TODAY="$(TZ=America/New_York date +%F)"
TRADING="$($RT schedule "$TODAY" 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["trading_day"])' 2>/dev/null)"
# after a trading-day close the latest backup is a few hours old; over weekends/holidays allow up to 80 h
if [ "$TRADING" = "True" ]; then MAXH=30; else MAXH=80; fi
OUT="$($RT verify-backup latest --max-age-hours $MAXH 2>/dev/null)"
OK="$(printf '%s' "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["ok"])' 2>/dev/null)"
echo "$(date -u +%FT%TZ) verify latest max_age=${MAXH}h ok=$OK $(printf '%s' "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("backup=%s age_h=%s problems=%s" % (d.get("backup"), d.get("age_hours"), d.get("problems")))' 2>/dev/null)" >> "$H/backup.log"
if [ "$OK" != "True" ]; then
  echo "$(date -u +%FT%TZ) nightly backup verification failed" > "$H/ALERT_BACKUP"
  logger -t avdi-backup -p daemon.err "nightly backup verification FAILED"
  exit 1
fi
rm -f "$H/ALERT_BACKUP"
exit 0
