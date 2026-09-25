#!/bin/sh
# One-time: seed the runtime volume with an existing paper ledger DB (e.g. the latest Case 1 artifact).
#   seed_ledger.sh <path-to-robinhood_500_baseline.db>
# Refuses to overwrite an existing ledger. Run BEFORE the first deploy (or with the runtime stopped).
set -eu
SRC="${1:?usage: seed_ledger.sh <ledger.db>}"
docker volume create avdi_runtime_ledger >/dev/null
if docker run --rm -v avdi_runtime_ledger:/d alpine:3 sh -c 'ls /d/*.db >/dev/null 2>&1'; then
  echo "REFUSING: the volume already holds a ledger"
  exit 1
fi
docker run --rm -v avdi_runtime_ledger:/d -v "$(dirname "$SRC")":/src:ro alpine:3 \
  sh -c "cp /src/$(basename "$SRC") /d/ && chmod 644 /d/*.db && chown -R 10001:10001 /d && ls -l /d"
echo "seeded"
