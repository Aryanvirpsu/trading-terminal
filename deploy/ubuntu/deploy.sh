#!/bin/sh
# Deploy a specific commit of `main` to THIS host (run on the Ubuntu host).
#   deploy.sh <git-sha> <source-tarball> [--force]
# Safe cutover: build (no downtime) -> refuse during the market window (unless --force) -> graceful stop ->
# consistent backup -> start new -> wait healthy -> on failure ROLL BACK to the previous image.
# State lives in the named volume `avdi_runtime_ledger`, so the new runtime resumes the true account.
set -eu
SHA="${1:?usage: deploy.sh <git-sha> <source-tarball> [--force]}"
TARBALL="${2:?source tarball}"
FORCE="${3:-}"
BASE="$HOME/avdi-runtime"
REL="$BASE/releases/$SHA"
IMG="avdi-paper:$SHA"
mkdir -p "$BASE/releases"

echo "== extract $SHA"
rm -rf "$REL" && mkdir -p "$REL" && tar -xzf "$TARBALL" -C "$REL"

echo "== market-window guard"
if python3 - "$REL" <<'PY'
import datetime as dt, sys
sys.path.insert(0, sys.argv[1] + "/lab/paper")
import market_calendar as cal
now = dt.datetime.now(cal.ET)
s = cal.session(now.date())
busy = s is not None and (s[0] - dt.timedelta(minutes=10)) <= now <= (s[1] + dt.timedelta(minutes=20))
print("ET now:", now.strftime("%Y-%m-%d %H:%M"), "| market window active:", busy)
sys.exit(1 if busy else 0)
PY
then
  :
else
  if [ "$FORCE" != "--force" ]; then
    echo "REFUSING: market window is active. Re-run with --force to override."
    exit 9
  fi
  echo "WARNING: --force during the market window"
fi

echo "== build $IMG (no downtime)"
docker build -f "$REL/docker/Dockerfile.terminal" --target paper -t "$IMG" "$REL"

PREV_IMG=""
if docker inspect avdi-runtime >/dev/null 2>&1; then
  PREV_IMG="$(docker inspect -f '{{.Config.Image}}' avdi-runtime)"
fi
echo "previous image: ${PREV_IMG:-none}"

echo "== graceful stop (state persists in the volume)"
if docker inspect avdi-runtime >/dev/null 2>&1; then
  docker stop -t 120 avdi-runtime >/dev/null
  if [ "$(docker inspect -f '{{.State.Running}}' avdi-runtime)" != "false" ]; then
    echo "old runtime still running - abort"
    exit 1
  fi
fi

echo "== pre-deploy backup"
docker run --rm --user 10001:10001 \
  --env-file "$REL/docker/env/case1.env" --env-file "$REL/docker/env/ubuntu-runtime.env" \
  -v avdi_runtime_ledger:/data/case1/paper --tmpfs /home/tv/.tradingview_mcp_data:mode=1777 \
  "$IMG" python automation/avdi_runtime.py backup || echo "(backup skipped - first deploy or no ledger yet)"

echo "== start new runtime"
docker rm -f avdi-runtime >/dev/null 2>&1 || true
AVDI_IMAGE="$IMG" AVDI_CODE_VERSION="$SHA" \
  docker compose -f "$REL/docker/compose.ubuntu.yml" -p avdi-runtime up -d --no-build

echo "== wait healthy (max 240 s)"
ok=0
for i in $(seq 1 48); do
  st="$(docker inspect -f '{{.State.Health.Status}}' avdi-runtime 2>/dev/null || echo none)"
  running="$(docker inspect -f '{{.State.Running}}' avdi-runtime 2>/dev/null || echo false)"
  echo "  [$i] running=$running health=$st"
  if [ "$st" = "healthy" ]; then
    ok=1
    break
  fi
  sleep 5
done

if [ "$ok" != "1" ]; then
  echo "!! new runtime did not become healthy - ROLLING BACK"
  docker logs --tail 40 avdi-runtime 2>&1 | tail -40 || true
  docker rm -f avdi-runtime >/dev/null 2>&1 || true
  if [ -n "$PREV_IMG" ]; then
    AVDI_IMAGE="$PREV_IMG" docker compose -f "$REL/docker/compose.ubuntu.yml" -p avdi-runtime up -d --no-build
    echo "rolled back to $PREV_IMG"
  else
    echo "no previous image to roll back to"
  fi
  exit 1
fi

echo "$SHA" > "$BASE/current_sha"
docker tag "$IMG" avdi-paper:current
# keep the three newest releases/images
ls -1t "$BASE/releases" | tail -n +4 | while read -r old; do
  rm -rf "$BASE/releases/$old"
  docker rmi "avdi-paper:$old" >/dev/null 2>&1 || true
done
echo "== DEPLOYED $SHA"
docker logs --tail 8 avdi-runtime 2>&1 | tail -8
