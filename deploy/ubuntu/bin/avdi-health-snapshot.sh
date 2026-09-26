#!/bin/sh
# Host-side health snapshot (systemd timer, every 15 min, 24/7). Read-only: it never touches trading.
# Writes ~/avdi-runtime/health/latest.json + a one-line history in health.log; raises ALERT (file + journald
# priority err) when the runtime is unhealthy or unreachable. Reliable because it does not depend on GitHub.
BASE="$HOME/avdi-runtime"
H="$BASE/health"
mkdir -p "$H"
TS="$(date -u +%FT%TZ)"

OUT="$(timeout 60 docker exec avdi-runtime python automation/avdi_runtime.py status 2>/dev/null)"
RC=$?
if [ -z "$OUT" ]; then
  OUT='{"healthy": false, "unhealthy": ["runtime_unreachable"], "degraded": [], "in_session": null}'
  RC=2
fi
printf '%s\n' "$OUT" > "$H/latest.json.tmp" && mv "$H/latest.json.tmp" "$H/latest.json"

LINE="$(printf '%s' "$OUT" | python3 -c '
import json, sys
ts = sys.argv[1]
try:
    d = json.load(sys.stdin)
except Exception:
    print(ts, "healthy=False unhealthy=status_unparseable"); sys.exit(0)
ld = (d.get("last_discovery") or {}).get("at")
lt = (d.get("last_tracker") or {}).get("at")
print(ts, "healthy=%s" % d.get("healthy"), "unhealthy=%s" % ",".join(d.get("unhealthy") or []) or "-",
      "degraded=%s" % (",".join(d.get("degraded") or []) or "-"), "in_session=%s" % d.get("in_session"),
      "last_discovery=%s" % ld, "last_tracker=%s" % lt, "code=%s" % (d.get("code_version") or "?")[:12])
' "$TS")"
echo "$LINE" >> "$H/health.log"
tail -n 4000 "$H/health.log" > "$H/health.log.tmp" && mv "$H/health.log.tmp" "$H/health.log"

if printf '%s' "$LINE" | grep -q "healthy=True"; then
  rm -f "$H/ALERT"
else
  echo "$LINE" > "$H/ALERT"
  logger -t avdi-health -p daemon.err "UNHEALTHY: $LINE"
fi
exit 0
