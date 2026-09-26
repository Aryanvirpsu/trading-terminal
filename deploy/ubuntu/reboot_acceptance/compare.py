import json
import sys

pre = json.load(open("pre.json"))
post = json.load(open("post.json"))
ps, qs = pre["state"], post["state"]
rows = []


def chk(name, ok, detail=""):
    rows.append((name, "PASS" if ok else "FAIL", detail))


chk("VM actually rebooted (new boot_id)", pre["host"]["boot_id"] != post["host"]["boot_id"],
    f"{pre['host']['boot_id'][:8]} -> {post['host']['boot_id'][:8]}; uptime at post-capture {int(post['host']['uptime_s'])} s")
chk("Docker active and enabled", post["host"]["docker_active"] == "active" and post["host"]["docker_enabled"] == "enabled")
names = {c.split("=")[0] for c in post["host"]["containers"]}
chk("AVDI + dashboard + proxy containers all running", {"avdi-runtime", "avdi-proxy", "avdi-dashboard-cloud"} <= names,
    "; ".join(post["host"]["containers"]))
chk("AVDI container healthy", post["container"]["health"] == "healthy")
chk("CORRECT release started", post["container"]["image"] == pre["container"]["image"] and
    post["state"]["env"]["AVDI_CODE_VERSION"] == pre["state"]["env"]["AVDI_CODE_VERSION"] == post["current_sha_file"],
    post["state"]["env"]["AVDI_CODE_VERSION"][:12])
chk("container was recreated by the restart policy (not manually)", post["container"]["started_at"] != pre["container"]["started_at"]
    and post["container"]["restart_policy"] == "unless-stopped", f"policy={post['container']['restart_policy']}")
chk("persistent volume mounted at /data/case1/paper", "avdi_runtime_ledger:/data/case1/paper" in post["container"]["mounts"],
    post["container"]["mounts"].strip())
chk("ledger reconciles (delta 0.0)", qs["reconciled"]["reconciled"] and qs["reconciled"]["delta"] == 0.0,
    f"cash_from_fills={qs['reconciled']['cash_from_fills']} equity={qs['reconciled']['equity']}")
for t in ("signals", "orders", "fills", "positions", "equity"):
    chk(f"ledger table '{t}' byte-identical (row hash)", ps["ledger"][t] == qs["ledger"][t], f"n={qs['ledger'][t]['n']}")
chk("open positions survive (DELL, META identical)", ps["open_positions"] == qs["open_positions"],
    str([(p['symbol'], p['quantity'], p['avg_entry']) for p in qs["open_positions"]]))
chk("cash unchanged", ps["ledger"]["cash_from_fills"] == qs["ledger"]["cash_from_fills"], str(qs["ledger"]["cash_from_fills"]))
chk("daily entries used (2026-09-25) persist", ps["entries_2026_09_25"] == qs["entries_2026_09_25"] == 2)
chk("NO duplicate/new orders", ps["orders_today"] == qs["orders_today"] and ps["ledger"]["orders"]["n"] == qs["ledger"]["orders"]["n"])
chk("evidence boundary survives", ps["boundary"] == qs["boundary"], f"equity {qs['boundary']['equity']} at {qs['boundary']['at']}")
for t in ("shadow_cycles", "shadow_candidates", "shadow_events", "shadow_evidence_boundary", "shadow_ch001_quarantine", "shadow_maintenance_log"):
    chk(f"shadow table '{t}' identical", ps["shadow"][t] == qs["shadow"][t], f"n={qs['shadow'][t]['n']}")
chk("shadow 5-minute bars unchanged", ps["shadow"]["bars_5m"] == qs["shadow"]["bars_5m"], str(qs["shadow"]["bars_5m"]))
chk("NO historical discovery slots back-filled", ps["runtime_state"]["cycle_log"] == qs["runtime_state"]["cycle_log"] and
    ps["runtime_state"]["discovery_runs"] == qs["runtime_state"]["discovery_runs"] and ps["runtime_state"]["done"] == qs["runtime_state"]["done"],
    f"cycle_log {qs['runtime_state']['cycle_log']}, discovery_runs {qs['runtime_state']['discovery_runs']}, done {qs['runtime_state']['done']}")
chk("timers enabled and active after reboot", all(v == "enabled" for v in post["timers_enabled"].values()) and
    all(v == "active" for v in post["timers_active"].values()), str(post["timers_active"]))
chk("live execution remains disabled", qs["env"]["ROBINHOOD_TRADING_ENABLED"] == "false" and qs["env"]["BROKER_PROVIDER"] == "none")
chk("config_version unchanged", ps["config_version"] == qs["config_version"], qs["config_version"])
w = max(len(r[0]) for r in rows)
for n, r, d in rows:
    print(f"{r}  {n.ljust(w)}  {d}")
fails = [r for r in rows if r[1] == "FAIL"]
print(f"\n{len(rows) - len(fails)}/{len(rows)} checks passed")
sys.exit(1 if fails else 0)
