# Reboot acceptance (outside market hours)

1. copy `snapshot.py` + `host_snapshot.sh` to the host `/tmp`, run `sh /tmp/host_snapshot.sh pre`
2. `sudo systemctl reboot`; wait for SSH and for `avdi-runtime` to be healthy (record the durations)
3. wait for the 2-minute boot health timer, copy the helpers to `/tmp` again (`/tmp` is cleared on boot) and run `sh /tmp/host_snapshot.sh post`
4. copy `pre.json`/`post.json` next to `compare.py` and run it: every check must PASS (new boot id, correct release, volume, ledger reconcile + identical row hashes, positions, evidence boundary, shadow DB, no back-filled slots, no new orders, timers active, live execution disabled).
Result of the 2026-09-25 acceptance: 29/29 checks passed; SSH back in 45 s, runtime healthy in 73 s.
