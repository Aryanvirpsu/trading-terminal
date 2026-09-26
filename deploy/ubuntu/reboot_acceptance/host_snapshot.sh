#!/bin/sh
# usage: host_snapshot.sh <label>   -> writes ~/avdi-runtime/reboot_test/<label>.json
L="$1"
D=/home/ubuntu/avdi-runtime/reboot_test
mkdir -p $D
docker exec -i avdi-runtime python - < /tmp/snapshot.py > /tmp/csnap.json 2> /tmp/csnap.err
python3 - "$L" "$D" <<'PY'
import json, os, subprocess, sys, time
label, d = sys.argv[1], sys.argv[2]
def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
snap = {"label": label, "captured_at_utc": sh("date -u +%FT%TZ"),
        "host": {"boot_id": open("/proc/sys/kernel/random/boot_id").read().strip(),
                 "uptime_s": float(open("/proc/uptime").read().split()[0]),
                 "docker_active": sh("systemctl is-active docker"), "docker_enabled": sh("systemctl is-enabled docker"),
                 "containers": sh("docker ps --format '{{.Names}}={{.Status}}'").split("\n")},
        "container": {"image": sh("docker inspect -f '{{.Config.Image}}' avdi-runtime"),
                      "started_at": sh("docker inspect -f '{{.State.StartedAt}}' avdi-runtime"),
                      "restart_count": sh("docker inspect -f '{{.RestartCount}}' avdi-runtime"),
                      "health": sh("docker inspect -f '{{.State.Health.Status}}' avdi-runtime"),
                      "restart_policy": sh("docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' avdi-runtime"),
                      "volume": sh("docker volume inspect avdi_runtime_ledger -f '{{.Mountpoint}}'"),
                      "mounts": sh("docker inspect -f '{{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}' avdi-runtime")},
        "timers": sh("systemctl list-timers 'avdi-*' --no-pager --no-legend | awk '{print $NF}'").split("\n"),
        "timers_enabled": {u: sh("systemctl is-enabled %s.timer" % u) for u in ("avdi-health", "avdi-close-backup", "avdi-verify")},
        "timers_active": {u: sh("systemctl is-active %s.timer" % u) for u in ("avdi-health", "avdi-close-backup", "avdi-verify")},
        "current_sha_file": open("/home/ubuntu/avdi-runtime/current_sha").read().strip()}
try:
    snap["state"] = json.loads(open("/tmp/csnap.json").read())
except Exception as e:
    snap["state_error"] = str(e)
json.dump(snap, open(os.path.join(d, label + ".json"), "w"), indent=1)
print(json.dumps({k: snap[k] for k in ("captured_at_utc", "host", "container", "timers_enabled", "timers_active")}, indent=1)[:1600])
PY
