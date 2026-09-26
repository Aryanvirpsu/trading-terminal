#!/bin/sh
# SSH forced-command gate for the AVDI Ubuntu host. Installed at ~/avdi-runtime/ops-gate.sh and referenced by
# authorized_keys as:   restrict,command="/home/ubuntu/avdi-runtime/ops-gate.sh <role>" ssh-ed25519 AAAA...
# A key holder gets NO shell: only the whitelisted operations for its role below.
#   role ops     : status | health | evidence | backup-now | backup-list | backup-pull |
#                  verify-backup | health-log | offhost-ack <backup-name>
#   role deploy  : status | health | deploy <40-hex-sha> [--simulate-failure]   (tarball on stdin)
# Deployment is NEVER forced into the market window: deploy.sh refuses it and this gate never passes --force.
set -eu
ROLE="${1:-}"
CMD="${SSH_ORIGINAL_COMMAND:-}"
BASE="$HOME/avdi-runtime"
mkdir -p "$BASE"
echo "$(date -u +%FT%TZ) role=$ROLE cmd=$CMD" >> "$BASE/ops-gate.log"

set -- $CMD
OP="${1:-}"
if [ "$#" -gt 0 ]; then shift; fi      # dash exits on a failing `shift`, even with `|| true`
allowed() {
  case "$ROLE:$OP" in
    ops:status|ops:health|ops:evidence|ops:backup-now|ops:backup-list|ops:backup-pull) return 0 ;;
    ops:verify-backup|ops:health-log|ops:offhost-ack) return 0 ;;
    deploy:status|deploy:health|deploy:deploy) return 0 ;;
  esac
  return 1
}
if ! allowed; then
  echo "denied: '$OP' is not permitted for role '$ROLE'" >&2
  exit 126
fi

RT="docker exec avdi-runtime python automation/avdi_runtime.py"
case "$OP" in
  status)      exec $RT status ;;
  health)      exec $RT health ;;
  evidence)    exec $RT evidence ;;
  backup-now)  exec $RT backup-live ;;
  verify-backup) exec $RT verify-backup latest ;;
  health-log)  exec tail -n 60 "$BASE/health/health.log" ;;
  offhost-ack)
    NAME="${1:-}"
    case "$NAME" in
      [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
      *) echo "bad backup name" >&2; exit 2 ;;
    esac
    exec $RT offhost-ack "$NAME" ;;
  backup-list) exec docker run --rm -v avdi_runtime_ledger:/d:ro alpine:3 sh -c 'ls -1 /d/backups' ;;
  backup-pull)
    exec docker run --rm -v avdi_runtime_ledger:/d:ro alpine:3 sh -c \
      'cd /d/backups && d=$(ls -1 | tail -1) && tar -czf - "$d"' ;;
  deploy)
    SHA="${1:-}"
    case "$SHA" in
      [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
      *) echo "bad sha" >&2; exit 2 ;;
    esac
    [ "${#SHA}" -eq 40 ] || { echo "sha must be 40 hex chars" >&2; exit 2; }
    SIM=""
    [ "${2:-}" = "--simulate-failure" ] && SIM="--simulate-failure"
    TB="/tmp/avdi-src-$SHA.tgz"
    head -c 104857600 > "$TB"                 # max 100 MB from stdin
    [ -s "$TB" ] || { echo "empty tarball" >&2; exit 2; }
    exec sh "$BASE/deploy.sh" "$SHA" "$TB" $SIM
    ;;
esac
