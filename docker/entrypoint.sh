#!/bin/sh
# C0 G4 entrypoint. Runs the preflight (docker/c0_preflight.py) and refuses to start
# the real command if it fails. See C0_CONTAINER_DESIGN.md §9.1 / §7.3.
set -eu

python /app/docker/c0_preflight.py

exec "$@"
