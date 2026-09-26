#!/bin/sh
# Install (or refresh) the AVDI host scripts and systemd timers. Run ON the host from a release checkout:
#   sh deploy/ubuntu/install-timers.sh
# Owner-managed like ops-gate.sh/deploy.sh: releases do not overwrite these automatically.
set -eu
SRC="$(cd "$(dirname "$0")" && pwd)"
BASE="$HOME/avdi-runtime"
mkdir -p "$BASE/bin" "$BASE/health" "$BASE/secrets"
chmod 700 "$BASE/secrets"
for f in "$SRC"/bin/*.sh; do
  install -m 755 "$f" "$BASE/bin/$(basename "$f")"
done
for u in avdi-health avdi-close-backup avdi-verify; do
  sudo install -m 644 "$SRC/systemd/$u.service" "/etc/systemd/system/$u.service"
  sudo install -m 644 "$SRC/systemd/$u.timer" "/etc/systemd/system/$u.timer"
done
sudo systemctl daemon-reload
for u in avdi-health avdi-close-backup avdi-verify; do
  systemd-analyze verify "/etc/systemd/system/$u.service" "/etc/systemd/system/$u.timer" 2>&1 | sed 's/^/  analyze: /' || true
  sudo systemctl enable --now "$u.timer"
done
echo "--- calendar checks"
systemd-analyze calendar "Mon..Fri 16:25 America/New_York" | head -4
echo "--- installed timers"
systemctl list-timers 'avdi-*' --no-pager
