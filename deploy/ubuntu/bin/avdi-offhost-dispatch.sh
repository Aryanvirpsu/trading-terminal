#!/bin/sh
# Best-effort off-host trigger, called after a VERIFIED local backup. GitHub-scheduled workflows proved unreliable
# (2 of ~16 probes fired; the nightly backup did not), so the HOST decides when: it asks GitHub to run the ops
# workflow (which pulls the prepared backup through the restricted `ops` SSH key and verifies it off-host).
# Credential: ~/avdi-runtime/secrets/gh_dispatch_token - a fine-grained token limited to THIS repository with the
# single permission "Actions: read and write" (it can only start workflows; no code, secrets or admin access).
# Absent token => the trigger is reported as NOT CONFIGURED (visible in health/backup logs) and nothing else happens.
BASE="$HOME/avdi-runtime"
H="$BASE/health"
mkdir -p "$H"
TOKEN_FILE="$BASE/secrets/gh_dispatch_token"
log() { echo "$(date -u +%FT%TZ) offhost $*" >> "$H/backup.log"; }
if [ ! -r "$TOKEN_FILE" ]; then
  log "trigger NOT CONFIGURED (no $TOKEN_FILE) - relying on the GitHub nightly schedule / manual dispatch"
  echo '{"configured": false}' > "$H/offhost_trigger.json"
  exit 0
fi
CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 -X POST \
  -H "Authorization: Bearer $(cat "$TOKEN_FILE")" -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/Aryanvirpsu/trading-terminal/actions/workflows/ubuntu-ops.yml/dispatches \
  -d '{"ref":"main"}')"
log "dispatch of ubuntu-ops.yml returned HTTP $CODE (204 = accepted) for backup ${1:-latest}"
printf '{"configured": true, "http": "%s", "at": "%s"}\n' "$CODE" "$(date -u +%FT%TZ)" > "$H/offhost_trigger.json"
[ "$CODE" = "204" ] || logger -t avdi-backup -p daemon.warning "off-host dispatch failed with HTTP $CODE"
exit 0
