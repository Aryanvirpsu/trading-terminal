"""Why do so few setups become entries?  Reproducible diagnosis of the cloud (Case 1) evidence.

    python diagnose.py                       # reads the newest verified mirror (~/.avdi_cloud_mirror/current.json)
    python diagnose.py --backup <folder>     # or a specific mirrored backup folder

DESCRIPTIVE ONLY. Nothing here changes, tests or promotes any strategy. It reads the pristine backup read-only
(SQLite immutable=1), prints a summary and writes diagnosis_results.json next to this file. Every figure quoted in
DIAGNOSIS.md comes from this script. Sample = engine `decision_engine/gates-v1.1` only (never pooled with v1.0).
"""
from __future__ import annotations

import argparse
import collections as C
import hashlib
import json
import os
import sqlite3
import statistics as st
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIRROR = Path(os.environ.get("AVDI_MIRROR_DIR", "~/.avdi_cloud_mirror")).expanduser()


def open_ro(p: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    return con


def locate(backup: str | None) -> Path:
    if backup:
        return Path(backup).expanduser()
    ptr = json.loads((MIRROR / "current.json").read_text(encoding="utf-8"))
    return Path(ptr["path"])


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def gate(row: dict, name: str) -> dict:
    for g in json.loads(row["gates_json"] or "[]"):
        if g["name"] == name:
            return g
    return {}


def failed(row: dict) -> list[str]:
    out = []
    for g in json.loads(row["failed_gates"] or "[]"):
        out.append(g if isinstance(g, str) else (g.get("gate") or g.get("name") or json.dumps(g)[:40]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup")
    a = ap.parse_args()
    d = locate(a.backup)
    manifest = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
    ledger = next(n for n in manifest["files"] if n.endswith(".db") and not n.startswith("shadow__"))
    shadow = next(n for n in manifest["files"] if n.startswith("shadow__"))
    L, S = open_ro(d / ledger), open_ro(d / shadow)
    out: dict = {"provenance": {"backup": d.name, "backup_created_at": manifest.get("created_iso"), "engine": manifest.get("engine_version"),
                                "ledger_sha256": sha(d / ledger), "shadow_sha256": sha(d / shadow)}}

    # 1 ── the label is a function of one number ───────────────────────────────────────────────────────────────
    rows = [dict(r) for r in L.execute("select * from signals where engine_version like '%v1.1'")]
    out["v11_signals"] = len(rows)
    out["label_counts"] = dict(C.Counter(r["action"] for r in rows))
    out["executed_by_label"] = {k: sum(r["executed"] or 0 for r in rows if r["action"] == k) for k in ("TRADEABLE", "MONITOR", "REJECT")}
    out["failed_gate_frequency"] = {act: dict(C.Counter(g for r in rows if r["action"] == act for g in failed(r)).most_common())
                                    for act in ("REJECT", "MONITOR", "TRADEABLE")}
    out["quality_range_by_label"] = {act: [min(r["quality"] for r in rows if r["action"] == act), max(r["quality"] for r in rows if r["action"] == act)]
                                     for act in ("REJECT", "MONITOR", "TRADEABLE")}
    for r in rows:
        r["aligned"] = gate(r, "signal_alignment").get("value")
        dq = gate(r, "data_quality").get("value") or {}
        r["famconf"], r["cov"], r["liq"] = dq.get("mean_family_conf"), dq.get("coverage_pct"), gate(r, "liquidity").get("value")
    out["quality_inputs_median_by_label"] = {
        act: {k: round(st.median(x[k] for x in rows if x["action"] == act and isinstance(x[k], (int, float))), 3) for k in ("aligned", "famconf", "cov", "liq")}
        for act in ("REJECT", "MONITOR", "TRADEABLE")}
    out["max_alignment_observed"] = max(r["aligned"] for r in rows if isinstance(r["aligned"], (int, float)))
    q = sorted(r["quality"] for r in rows)
    out["quality_quantiles"] = {"p25": q[len(q) // 4], "p50": st.median(q), "p75": q[3 * len(q) // 4], "p90": q[int(.9 * len(q))], "max": q[-1],
                                "share_ge_60": round(sum(x >= 60 for x in q) / len(q), 3)}
    buckets = {}
    for lo, hi in ((0, .1), (.1, .2), (.2, .3), (.3, .4)):
        qs = [r["quality"] for r in rows if isinstance(r["aligned"], (int, float)) and lo <= r["aligned"] < hi]
        if qs:
            buckets[f"{lo:.1f}-{hi:.1f}"] = {"n": len(qs), "quality_median": round(st.median(qs), 1), "quality_max": max(qs)}
    out["quality_by_alignment"] = buckets

    # 2 ── does the 60 bar pick better setups?  event-level, equal terms, from the cloud's own 5-minute bars ───────
    ev = [dict(r) for r in S.execute("select * from shadow_events")]
    first: dict = {}
    for r in S.execute("select event_id, decision, quality from shadow_candidates where event_id is not null order by scan_ts"):
        first.setdefault(r["event_id"], dict(r))
    res = []
    for e in ev:
        f = first.get(e["event_id"])
        if not f or not (e["entry"] and e["stop"] and e["target"]):
            continue
        fw = [(datetime.fromisoformat(b["ts"]), b["h"], b["l"], b["c"]) for b in
              S.execute("select ts,h,l,c from shadow_bars_5m where symbol=? order by ts", (e["symbol"],))]
        t0 = datetime.fromisoformat(e["first_ts"])
        fw = [b for b in fw if b[0] > t0]
        if len(fw) < 12:
            continue
        long_ = (e["direction"] or "LONG").upper() == "LONG"
        risk = abs(e["entry"] - e["stop"])
        outcome, r_end = "open", None
        for _, h, l, _c in fw:
            if (l <= e["stop"]) if long_ else (h >= e["stop"]):
                outcome, r_end = "stop", -1.0
                break
            if (h >= e["target"]) if long_ else (l <= e["target"]):
                outcome, r_end = "target", abs(e["target"] - e["entry"]) / risk
                break
        if r_end is None:
            r_end = ((fw[-1][3] - e["entry"]) if long_ else (e["entry"] - fw[-1][3])) / risk
        res.append({"label": f["decision"], "quality": f["quality"], "outcome": outcome, "R": r_end})
    by = {}
    for lab in ("TRADEABLE", "MONITOR", "REJECT"):
        s = [r for r in res if r["label"] == lab]
        if s:
            c = C.Counter(r["outcome"] for r in s)
            by[lab] = {"events": len(s), "target": c["target"], "stop": c["stop"], "open": c["open"],
                       "mean_R_open_marked": round(st.mean(r["R"] for r in s), 2), "median_R": round(st.median(r["R"] for r in s), 2)}
    out["event_outcomes_by_first_label"] = {"events_measured": len(res), "by_label": by,
                                            "note": "no costs, stop-first on ambiguity, open positions marked at the last bar; small n, in-sample, descriptive"}

    # 3 ── TRADEABLE -> entry: the runtime's own funnel, and why setups are blocked ────────────────────────────────
    tot = C.Counter(); reasons = C.Counter(); ncyc = 0
    for c in S.execute("select funnel_json from shadow_cycles where session_type='regular'"):
        try:
            f = json.loads(c["funnel_json"] or "{}")
        except ValueError:
            continue
        ncyc += 1
        for k, v in f.items():
            if isinstance(v, (int, float)):
                tot[k] += v
            if k == "top_reasons" and isinstance(v, dict):
                for rk, rv in v.items():
                    reasons[rk] += rv
    out["funnel_regular_cycles"] = {"cycles": ncyc, "summed": {k: tot[k] for k in ("scanner_detections", "finalists", "TRADEABLE", "MONITOR", "REJECT", "capacity_eligible",
                                    "instrument_no_trade", "stock_executable_at_decision", "entry_blocked_pre_broker", "orders_attempted", "entries")},
                                    "top_reasons": dict(reasons.most_common(10)),
                                    "note": "an observation repeats every 15 minutes, so these are observations, not independent setups"}
    trad = [dict(r) for r in S.execute("select * from shadow_candidates where decision='TRADEABLE' and session_type='regular'")]
    viol = C.Counter()
    for r in trad:
        for x in json.loads(r["features_json"] or "{}").get("stock_fit_violations") or []:
            viol[x] += 1
    out["tradeable_observations"] = {"n": len(trad), "distinct_events": len({r["event_id"] for r in trad}), "executed": sum(r["champion_executed"] or 0 for r in trad),
                                     "without_any_fit_violation": sum(1 for r in trad if not (json.loads(r["features_json"] or "{}").get("stock_fit_violations") or [])),
                                     "fit_violations": dict(viol.most_common())}
    entered = {r[0] for r in S.execute("select distinct event_id from shadow_candidates where champion_executed=1")}
    firsts: dict = {}
    for r in sorted(trad, key=lambda r: r["scan_ts"]):
        firsts.setdefault(r["event_id"], r)
    attrib = C.Counter(); per_event = []
    for eid, r in firsts.items():
        v = sorted(json.loads(r["features_json"] or "{}").get("stock_fit_violations") or [])
        key = "entered" if eid in entered else ("blocked: " + ", ".join(v) if v else "blocked: " + str(r["champion_reason"] or r["capacity_reason"]))
        attrib[key] += 1
        per_event.append({"date": r["session_date"], "symbol": r["symbol"], "sector": r["sector"], "outcome": key})
    out["tradeable_events"] = {"n": len(firsts), "attribution_at_first_look": dict(attrib.most_common()), "events": sorted(per_event, key=lambda x: (x["date"], x["symbol"]))}

    # 4 ── what is holding the slots ──────────────────────────────────────────────────────────────────────────────
    pos = [dict(r) for r in L.execute("select symbol, sector, opened_at, closed_at, status, exit_reason from positions")]
    days = sorted({r[0] for r in S.execute("select distinct session_date from shadow_cycles where session_type='regular'")})
    occ = []
    for dday in days:
        t = dday + "T14:00:00"
        o = [p["symbol"] for p in pos if p["opened_at"][:19] <= t and (p["closed_at"] is None or p["closed_at"][:19] > t)]
        occ.append({"date": dday, "open": len(o), "symbols": o})
    out["slot_occupancy_1000_et"] = occ
    out["positions"] = pos
    (HERE / "diagnosis_results.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("provenance", "label_counts", "executed_by_label", "max_alignment_observed", "quality_quantiles")}, indent=2))
    print("tradeable events:", out["tradeable_events"]["n"], out["tradeable_events"]["attribution_at_first_look"])
    print("wrote", HERE / "diagnosis_results.json")


if __name__ == "__main__":
    main()
