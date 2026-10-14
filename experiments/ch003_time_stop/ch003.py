"""CH-003 runner. See PREREG.md (hash-locked before any output existed).

    python ch003.py --stage-a [--backup DIR]   Stage A: replay the CHAMPION only and check it reproduces the cloud's real entries
    python ch003.py --freeze                   after Stage A passes: freeze this code (sha256 -> CODE.lock.json)
    python ch003.py [--backup DIR]             Stage B: all arms x all cost cases, exactly as pre-registered; writes ch003_results.json

Reads the mirrored cloud backup read-only (SQLite immutable=1). Never writes anywhere but this folder. Never promotes anything:
`promotion_gate.auto_promote` is always false and a promotion needs a person to write PROMOTION_LOG.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine as E  # noqa: E402

MIRROR = Path(os.environ.get("AVDI_MIRROR_DIR", "~/.avdi_cloud_mirror")).expanduser()
OOS_FROM = "2026-10-12"                       # positions ENTERED after this session are out-of-sample
V11_FROM = "2026-09-25"
ARMS = {"CHAMPION": None, "TS3": 3, "TS5": 5, "TS8": 8, "TS10": 10}
COSTS = {5: 25, 15: 35, 30: 50}               # slippage bps -> gap bps (PREREG: gap = slippage + 20)
ET = ZoneInfo("America/New_York")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ro(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    return c


def die(msg: str, code: int = 2):
    print("REFUSED:", msg, file=sys.stderr)
    sys.exit(code)


def check_prereg_lock() -> dict:
    lock = json.loads((HERE / "PREREG.lock.json").read_text(encoding="utf-8"))
    if sha(HERE / "PREREG.md") != lock["sha256"]:
        die("PREREG.md differs from PREREG.lock.json — an edited pre-registration invalidates the experiment.")
    return lock


def code_hashes() -> dict:
    return {"engine.py": sha(HERE / "engine.py"), "ch003.py": sha(HERE / "ch003.py")}


def load(backup: Path) -> dict:
    manifest = json.loads((backup / "MANIFEST.json").read_text(encoding="utf-8"))
    shadow = next(n for n in manifest["files"] if n.startswith("shadow__"))
    ledger = next(n for n in manifest["files"] if n.endswith(".db") and not n.startswith("shadow__"))
    S, L = ro(backup / shadow), ro(backup / ledger)
    cyc_rows = [dict(r) for r in S.execute("select cycle_id, session_date, scan_ts from shadow_cycles where session_type='regular' order by scan_ts")]
    by_cycle: dict = {}
    syms = set()
    for r in S.execute("select * from shadow_candidates where decision='TRADEABLE' and session_type='regular' order by scan_ts"):
        r = dict(r)
        by_cycle.setdefault(r["cycle_id"], []).append(E.Cand(datetime.fromisoformat(r["scan_ts"]), r["session_date"], r["symbol"], r["sector"] or "?",
                                                              r["event_id"], r["scanner_rank"] or 99, r["stop"], r["target"], r["quote_ask"]))
        syms.add(r["symbol"])
    bars: dict = {}
    for s in syms:
        bars[s] = [E.Bar(datetime.fromisoformat(b["ts"]), b["o"], b["h"], b["l"], b["c"]) for b in
                   S.execute("select ts,o,h,l,c from shadow_bars_5m where symbol=? and o is not null and h is not null and l is not null and c is not null order by ts", (s,))]
    days = sorted({b.day for bl in bars.values() for b in bl})
    sess = {d: i for i, d in enumerate(days)}
    cycles = [(datetime.fromisoformat(c["scan_ts"]), c["session_date"], by_cycle.get(c["cycle_id"], [])) for c in cyc_rows]
    actual = []
    for r in L.execute("select symbol, opened_at, avg_entry from positions where opened_at >= ? order by opened_at", (V11_FROM,)):
        t = datetime.fromisoformat(r["opened_at"]).astimezone(ET)
        actual.append({"symbol": r["symbol"], "session": str(t.date()), "avg_entry": r["avg_entry"]})
    ev_t = {e[0]: e[1] for e in S.execute("select event_id, symbol from shadow_events")}
    return {"manifest": manifest, "cycles": cycles, "bars": bars, "sess": sess, "actual": actual, "n_cand": sum(len(v) for v in by_cycle.values()),
            "files": {"ledger": ledger, "shadow": shadow}, "events": ev_t}


def replay(data: dict, n, slip: float) -> dict:
    p = E.Params(slip_bps=slip, gap_bps=COSTS[int(slip)], time_stop_n=n)
    run = E.run_arm(data["cycles"], data["bars"], data["sess"], p)
    timeline = sorted({b.end for sym in {x.symbol for x in run["positions"]} for b in data["bars"][sym]})
    res = [E.position_result(x, data["bars"][x.symbol], data["sess"]) for x in run["positions"]]
    return {"params": p, "raw": run["positions"], "res": res, "skipped": run["skipped"], "eq": E.equity_stats(run["positions"], timeline, p)}


def metrics(res: list, eq: dict, subset=None) -> dict:
    r = [x for x in res if subset is None or subset(x)]
    closed = [x for x in r if x["state"] != "open"]
    openp = [x for x in r if x["state"] == "open"]
    return {"entries": len(r), "closed": len(closed), "open": len(openp), "time_stopped": sum(x["state"] == "time_stop" for x in r),
            "realized_R": round(sum(x["R"] for x in closed), 3), "open_mark_R": round(sum(x["R"] for x in openp), 3),
            "total_net_R": round(sum(x["R"] for x in r), 3), "net_pnl_usd": round(sum(x["pnl_usd"] for x in r), 2),
            "mean_sessions_held": round(sum(x["sessions_held"] for x in r) / len(r), 2) if r else 0, "max_sessions_held": max((x["sessions_held"] for x in r), default=0),
            "worst_trade_R": round(min((x["R"] for x in r), default=0), 3), "longest_losing_streak": E.longest_losing_streak(r),
            "max_drawdown_usd": eq["max_drawdown_usd"], "mean_utilisation": eq["mean_utilisation"]}


def validity(data: dict) -> dict:
    run = replay(data, None, 5)
    got = sorted((x["symbol"], x["entry_session"], x["fill"]) for x in run["res"])
    want = sorted((a["symbol"], a["session"], a["avg_entry"]) for a in data["actual"])
    checks = []
    same_set = [(g[0], g[1]) for g in got] == [(w[0], w[1]) for w in want]
    checks.append({"check": "Champion replay enters exactly the cloud's real v1.1 entries (symbol + session)", "passed": same_set, "replay": [g[:2] for g in got], "actual": [w[:2] for w in want]})
    fills_ok = same_set and all(abs(g[2] - w[2]) / w[2] <= 0.001 for g, w in zip(got, want))
    checks.append({"check": "each fill within 0.1% of the recorded avg_entry", "passed": bool(fills_ok),
                   "detail": [{"symbol": g[0], "replay_fill": g[2], "actual": w[2]} for g, w in zip(got, want)] if same_set else "set differs"})
    return {"passed": all(c["passed"] for c in checks), "checks": checks, "skipped_sample": run["skipped"][:12]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup")
    ap.add_argument("--stage-a", action="store_true")
    ap.add_argument("--freeze", action="store_true")
    a = ap.parse_args()
    prereg = check_prereg_lock()
    backup = Path(a.backup).expanduser() if a.backup else Path(json.loads((MIRROR / "current.json").read_text(encoding="utf-8"))["path"])
    data = load(backup)
    prov = {"backup": backup.name, "backup_created_at": data["manifest"].get("created_iso"), "engine": data["manifest"].get("engine_version"),
            "ledger_sha256": sha(backup / data["files"]["ledger"]), "shadow_sha256": sha(backup / data["files"]["shadow"]),
            "prereg_sha256": prereg["sha256"], "sessions": len(data["sess"]), "tradeable_observations": data["n_cand"], "oos_from_exclusive": OOS_FROM}

    if a.stage_a or a.freeze:
        v = validity(data)
        (HERE / "validity.json").write_text(json.dumps({"provenance": prov, **v}, indent=2), encoding="utf-8")
        print(json.dumps({"validity_passed": v["passed"], "checks": [{k: c[k] for k in ("check", "passed")} for c in v["checks"]]}, indent=2))
        for c in v["checks"]:
            if not c["passed"]:
                print("  replay:", c.get("replay")); print("  actual:", c.get("actual"))
        if not v["passed"]:
            sys.exit(3)
        if a.freeze:
            (HERE / "CODE.lock.json").write_text(json.dumps({"frozen_at": datetime.now().astimezone().isoformat(), "code_sha256": code_hashes(),
                                                              "validity_passed_on_backup": backup.name, "prereg_sha256": prereg["sha256"]}, indent=2), encoding="utf-8")
            print("code frozen -> CODE.lock.json")
        return

    # ── Stage B ───────────────────────────────────────────────────────────────────────────────────────────────
    cl = HERE / "CODE.lock.json"
    if not cl.exists():
        die("run --stage-a then --freeze first: the replay code must be frozen before any non-Champion arm is run.")
    if json.loads(cl.read_text(encoding="utf-8"))["code_sha256"] != code_hashes():
        die("engine.py / ch003.py changed after the freeze — results would not be comparable. Re-do Stage A and freeze (and log why).")
    v = validity(data)
    if not v["passed"]:
        die("Stage A validity gate fails on this backup — the replay does not reproduce the real Champion; no arm may be interpreted.", 3)

    out: dict = {"experiment": "CH-003", "kind": "arm_comparison", "provenance": prov, "validity_gate": v, "arms": {}, "positions": {}, "deltas": {},
                 "opportunity_cost": {}, "time_stop_regret": {}, "leave_one_event_out": {}, "out_of_sample": {}}
    runs = {(n, c): replay(data, n, c) for n in ARMS.values() for c in COSTS}
    sample = lambda x: x["entry_session"] <= OOS_FROM
    oos = lambda x: x["entry_session"] > OOS_FROM
    for arm, n in ARMS.items():
        out["arms"][arm] = {}
        for c in COSTS:
            r = runs[(n, c)]
            out["arms"][arm][str(c)] = {"in_sample": metrics(r["res"], r["eq"], sample), "all": metrics(r["res"], r["eq"])}
        out["positions"][arm] = runs[(n, 5)]["res"]
        out["out_of_sample"][arm] = {"positions": sum(oos(x) for x in runs[(n, 5)]["res"]),
                                     "eligible_for_time_stop": sum(oos(x) and not x["touched_1r"] and (n is not None) and x["sessions_held"] >= n for x in runs[(n, 5)]["res"])}
    champ = {c: runs[(None, c)] for c in COSTS}
    ev_of = lambda x: x["event_id"]
    for arm, n in ARMS.items():
        if n is None:
            continue
        out["deltas"][arm] = {}
        out["leave_one_event_out"][arm] = {}
        for c in COSTS:
            a_, b_ = out["arms"][arm][str(c)]["in_sample"], out["arms"]["CHAMPION"][str(c)]["in_sample"]
            out["deltas"][arm][str(c)] = {"total_net_R": round(a_["total_net_R"] - b_["total_net_R"], 3), "net_pnl_usd": round(a_["net_pnl_usd"] - b_["net_pnl_usd"], 2),
                                          "max_drawdown_usd": round(a_["max_drawdown_usd"] - b_["max_drawdown_usd"], 3),
                                          "entries": a_["entries"] - b_["entries"], "mean_utilisation": round(a_["mean_utilisation"] - b_["mean_utilisation"], 4)}
            ra = [x for x in runs[(n, c)]["res"] if sample(x)]
            rb = [x for x in champ[c]["res"] if sample(x)]
            deltas = []
            for e in sorted({ev_of(x) for x in ra + rb}):
                deltas.append((round(sum(x["R"] for x in ra if ev_of(x) != e) - sum(x["R"] for x in rb if ev_of(x) != e), 3), e))
            lo = min(deltas) if deltas else (0, None)
            hi = max(deltas) if deltas else (0, None)
            out["leave_one_event_out"][arm][str(c)] = {"min_delta_R": lo[0], "min_when_dropping_event": lo[1], "max_delta_R": hi[0], "events": len(deltas),
                                                       "note": "accounting robustness (positions of one event removed from both tallies), not a re-simulation"}
        # opportunity cost and regret at the base cost case
        cp = {(x["symbol"], x["entry_session"]): x for x in champ[5]["res"]}
        ap_ = {(x["symbol"], x["entry_session"]): x for x in runs[(n, 5)]["res"]}
        out["opportunity_cost"][arm] = {
            "entries_only_because_a_slot_was_released": [x for k, x in ap_.items() if k not in cp],
            "champion_entries_not_made_in_this_arm": [x for k, x in cp.items() if k not in ap_]}
        reg = []
        for ps, rs in zip(runs[(n, 5)]["raw"], runs[(n, 5)]["res"]):
            if rs["state"] == "time_stop":
                h = E.hold_outcome(data["bars"][ps.symbol], ps.entry_ts, ps.stop, ps.target, ps.fill, data["sess"], runs[(n, 5)]["params"])
                reg.append({"symbol": ps.symbol, "entry_session": rs["entry_session"], "sessions_held_at_exit": rs["sessions_held"], "realized_R": rs["R"],
                            "R_if_held_champion_exits": round(h["R"], 3), "held_outcome": h["state"], "regret_R": round(h["R"] - rs["R"], 3)})
        out["time_stop_regret"][arm] = reg

    # ── pre-registered in-sample reading ──────────────────────────────────────────────────────────────────────
    supported = []
    for arm in ("TS3", "TS5", "TS8", "TS10"):
        d5, d15 = out["deltas"][arm]["5"], out["deltas"][arm]["15"]
        ok = (d5["total_net_R"] > 0 and d15["total_net_R"] > 0 and out["leave_one_event_out"][arm]["5"]["min_delta_R"] > 0
              and out["leave_one_event_out"][arm]["15"]["min_delta_R"] > 0 and d5["max_drawdown_usd"] <= 2.5 and d15["max_drawdown_usd"] <= 2.5)
        supported.append({"arm": arm, "meets_all_criteria": ok, "delta_R_5bps": d5["total_net_R"], "delta_R_15bps": d15["total_net_R"],
                          "loeo_min_5bps": out["leave_one_event_out"][arm]["5"]["min_delta_R"], "loeo_min_15bps": out["leave_one_event_out"][arm]["15"]["min_delta_R"],
                          "drawdown_delta_5bps": d5["max_drawdown_usd"]})
    n_ok = sum(s["meets_all_criteria"] for s in supported)
    out["in_sample_reading"] = {"per_arm": supported, "arms_meeting_criteria": n_ok, "directionally_supported": n_ok >= 3,
                                "text": ("directionally supported (>=3 of 4 arms meet every pre-registered criterion)" if n_ok >= 3 else
                                         "NOT supported by the pre-registered criteria (fewer than 3 of 4 arms meet all of them)"),
                                "caveat": "in-sample and exploratory; the hypothesis came from this same sample; no threshold is chosen as best; cannot promote"}
    oos_n = {arm: out["out_of_sample"][arm]["eligible_for_time_stop"] for arm in ARMS if ARMS[arm]}
    out["promotion_gate"] = {"auto_promote": False, "eligible": False, "checks": [
        {"check": "Stage A validity gate passed", "passed": True},
        {"check": "evaluated on out-of-sample sessions only (entered after %s)" % OOS_FROM, "passed": any(out["out_of_sample"][a]["positions"] for a in out["out_of_sample"]),
         "detail": "no out-of-sample positions yet — sync a later backup and re-run"},
        {"check": ">= 15 OOS positions eligible for the time stop", "passed": max(oos_n.values()) >= 15, "detail": oos_n},
        {"check": "threshold N declared in writing before the OOS window was read (PROMOTION_LOG.md)", "passed": (HERE / "PROMOTION_LOG.md").exists()},
        {"check": "delta total net R > 0 at 5, 15 and 30 bps on OOS; leave-one-event-out min > 0; drawdown within $2.50", "passed": False, "detail": "not evaluable without OOS data"}],
        "reason": "In-sample results can never promote. A promotion is a human decision recorded in PROMOTION_LOG.md."}
    out["notes"] = ["Costs: slippage 5/15/30 bps (gap 25/35/50). Open positions are marked at the last bar, not realised.",
                    "total_net_R = realized + open marks, in R of price risk at fill; net_pnl_usd uses the post-risk-fix sizing.",
                    "Champion arm == the cloud's real behaviour only to the extent Stage A passes (it did, or this file would not exist)."]
    (HERE / "ch003_results.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"validity": v["passed"], "reading": out["in_sample_reading"]["text"]}, indent=2))
    for arm in ARMS:
        m = out["arms"][arm]["5"]["in_sample"]
        print(f"{arm:9s} entries={m['entries']:2d} time_stopped={m['time_stopped']} total_R={m['total_net_R']:+7.3f} (realized {m['realized_R']:+.3f}, open {m['open_mark_R']:+.3f}) "
              f"pnl=${m['net_pnl_usd']:+7.2f} maxDD=${m['max_drawdown_usd']:.2f} util={m['mean_utilisation']:.2f}")
    print("wrote ch003_results.json")


if __name__ == "__main__":
    main()
