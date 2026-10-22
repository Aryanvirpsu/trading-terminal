"""CH-005 runner.  Enforces the protocol in code (see PREREG.md):

    python experiments/ch005_pullback_uptrend/ch005.py --freeze     # write CODE.lock.json (after PREREG.lock.json exists); no outcomes computed
    python experiments/ch005_pullback_uptrend/ch005.py              # run ONCE: train -> validation -> test; refuses if any lock is broken
    python experiments/ch005_pullback_uptrend/ch005.py --reproduce  # re-run into a temp file and verify it equals ch005_results.json

The run refuses when PREREG.md, the data files, engine.py/ch005.py or the test file differ from their recorded sha256, and refuses to overwrite an
existing ch005_results.json (single shot).  `auto_promote` is always false: nothing here changes any production setting.
"""
import argparse, datetime as dt, hashlib, importlib.util, json, sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA = HERE / "data"
PREREG, PLOCK, CLOCK, RESULTS = HERE / "PREREG.md", HERE / "PREREG.lock.json", HERE / "CODE.lock.json", HERE / "ch005_results.json"
TESTFILE = ROOT / "tests" / "unit" / "test_ch005_engine.py"

spec = importlib.util.spec_from_file_location("ch005_engine", HERE / "engine.py")
E = importlib.util.module_from_spec(spec)
sys.modules["ch005_engine"] = E
spec.loader.exec_module(E)

PERIODS = {"train": ("2015-01-02", "2019-12-31"), "validation": ("2020-01-02", "2022-12-30"), "test": ("2023-01-03", "2026-10-21")}
COSTS = (5.0, 15.0, 30.0)
PRIMARY_COST = 15.0
CONTROL_SEEDS = {"train": 100, "validation": 100, "test": 200}
BOOT_SEED = 20261022


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def code_hashes():
    return {"engine.py": sha(HERE / "engine.py"), "ch005.py": sha(__file__), "tests/unit/test_ch005_engine.py": sha(TESTFILE),
            "data/prices_daily.json.gz": sha(DATA / "prices_daily.json.gz"), "data/universe.json": sha(DATA / "universe.json")}


def check_locks():
    pl = json.loads(PLOCK.read_text())
    if sha(PREREG) != pl["prereg_sha256"]:
        raise SystemExit("REFUSING: PREREG.md differs from its lock")
    cl = json.loads(CLOCK.read_text())
    cur = code_hashes()
    bad = {k: (cl["sha256"].get(k), v) for k, v in cur.items() if cl["sha256"].get(k) != v}
    if bad:
        raise SystemExit(f"REFUSING: code/data differ from CODE.lock.json: {sorted(bad)}")
    return pl, cl


def setup():
    uni = json.loads((DATA / "universe.json").read_text())["symbols"]
    bars = E.load_prices(DATA / "prices_daily.json.gz")
    cal = bars["SPY"]["d"]
    frame = E.build_frame(bars, cal)
    return uni, cal, frame


def trade_stats(trades):
    R = np.array([t["R"] for t in trades]) if trades else np.array([])
    return R


def run_all():
    uni, cal, frame = setup()
    base_p = E.Params()
    out = {"protocol": "PREREG.md", "engine_sha256": sha(HERE / "engine.py"), "periods": {k: list(v) for k, v in PERIODS.items()}, "runs": {}, "control": {}, "notes": []}
    pre = {}
    for c in COSTS:
        p = E.Params(cost_bps=c)
        pre[c] = (p,) + E.prepare(frame, p)            # indicators do not depend on cost, but the Params object does
    # SPY volatility terciles from TRAIN sessions only (frozen cut points)
    ind0 = pre[PRIMARY_COST]
    spy_i = ind0[4]
    i_tr0 = next(i for i, d in enumerate(cal) if d >= PERIODS["train"][0]); i_tr1 = max(i for i, d in enumerate(cal) if d <= PERIODS["train"][1])
    v = spy_i["vol_bucket_src"][i_tr0:i_tr1 + 1]
    cuts = [float(np.nanpercentile(v, 33.333)), float(np.nanpercentile(v, 66.667))]
    out["spy_vol20_terciles_from_train"] = cuts
    for per, (s, e) in PERIODS.items():
        out["runs"][per] = {}
        for c in COSTS:
            p, ind, pull, base, spy_c, mk = pre[c]
            res = E.simulate(frame, ind, pull, base, spy_c, uni, cal, p, s, e, "rank", 0)
            m = E.metrics(res, p, s, cal)
            R = trade_stats(res["trades"])
            if len(R):
                m["expectancy_R_bootstrap_p10"] = E.bootstrap_lower(R, 10.0, 10_000, BOOT_SEED)
            out["runs"][per][str(int(c))] = {"metrics": m, "trades": res["trades"] if c == PRIMARY_COST else None}
            if c == PRIMARY_COST:
                out["runs"][per][str(int(c))]["equity"] = res["equity"][::5]                 # every 5th session keeps the file small
        # matched control at the primary cost: trend-only random entries, identical exits/sizing/limits/costs
        p, ind, pull, base, spy_c, mk = pre[PRIMARY_COST]
        cm = []
        for seed in range(1, CONTROL_SEEDS[per] + 1):
            res = E.simulate(frame, ind, pull, base, spy_c, uni, cal, p, s, e, "random", seed)
            mm = E.metrics(res, p, s, cal)
            cm.append({"seed": seed, "trades": mm.get("trades", 0), "net_pnl": mm.get("net_pnl", 0.0), "expectancy_R": mm.get("expectancy_R"), "pnl_per_slot_day": mm.get("pnl_per_slot_day")})
        eR = np.array([x["expectancy_R"] for x in cm if x["expectancy_R"] is not None])
        strat = out["runs"][per][str(int(PRIMARY_COST))]["metrics"]
        out["control"][per] = {"seeds": len(cm), "expectancy_R_mean": float(eR.mean()), "expectancy_R_p50": float(np.percentile(eR, 50)), "expectancy_R_p90": float(np.percentile(eR, 90)),
                               "trades_mean": float(np.mean([x["trades"] for x in cm])), "net_pnl_mean": float(np.mean([x["net_pnl"] for x in cm])),
                               "strategy_expectancy_R": strat.get("expectancy_R"),
                               "strategy_percentile_in_control": float((eR < strat["expectancy_R"]).mean() * 100) if strat.get("trades") else None, "runs": cm}
    out["tests_opened"] = True
    out["analysis"] = analyse(out, cal, frame, cuts)
    out["verdict"] = verdict(out)
    return out


def analyse(out, cal, frame, cuts):
    a = {}
    tr = out["runs"]["test"][str(int(PRIMARY_COST))]["trades"] or []
    pnl = np.array([t["pnl"] for t in tr]) if tr else np.array([])
    a["test_trades_by_year"] = {}
    for t in tr:
        y = t["entry"][:4]
        d = a["test_trades_by_year"].setdefault(y, {"n": 0, "pnl": 0.0, "R": []})
        d["n"] += 1; d["pnl"] += t["pnl"]; d["R"].append(t["R"])
    for y, d in a["test_trades_by_year"].items():
        d["expectancy_R"] = float(np.mean(d.pop("R"))); d["pnl"] = round(d["pnl"], 2)
    def bucket(v):
        return None if v is None else ("low" if v <= cuts[0] else "mid" if v <= cuts[1] else "high")
    vb = {}
    for t in tr:
        d = vb.setdefault(bucket(t.get("spy_vol20")), {"n": 0, "pnl": 0.0, "R": []}); d["n"] += 1; d["pnl"] += t["pnl"]; d["R"].append(t["R"])
    a["test_by_spy_vol20_tercile"] = {k: {"n": d["n"], "pnl": round(d["pnl"], 2), "expectancy_R": float(np.mean(d["R"]))} for k, d in vb.items()}
    ab = {}
    for t in tr:
        d = ab.setdefault("spy_above_50d" if t.get("spy_above_sma50") else "spy_below_50d", {"n": 0, "pnl": 0.0, "R": []}); d["n"] += 1; d["pnl"] += t["pnl"]; d["R"].append(t["R"])
    a["test_by_spy_trend"] = {k: {"n": d["n"], "pnl": round(d["pnl"], 2), "expectancy_R": float(np.mean(d["R"]))} for k, d in ab.items()}
    if len(pnl):
        a["test_net_without_best_trade"] = float(pnl.sum() - pnl.max())
        bysym = {}
        for t in tr:
            bysym[t["sym"]] = bysym.get(t["sym"], 0.0) + t["pnl"]
        top = max(bysym.items(), key=lambda kv: kv[1])
        a["test_top_symbol"] = {"sym": top[0], "pnl": round(top[1], 2), "share_of_net": float(top[1] / pnl.sum()) if pnl.sum() > 0 else None}
        a["test_by_sector"] = {}
        for t in tr:
            a["test_by_sector"][t["sector"]] = round(a["test_by_sector"].get(t["sector"], 0.0) + t["pnl"], 2)
    # buy-and-hold SPY and exposure context
    for per, (s, e) in PERIODS.items():
        i0 = next(i for i, d in enumerate(cal) if d >= s); i1 = max(i for i, d in enumerate(cal) if d <= e)
        a.setdefault("spy_buy_hold_pct", {})[per] = round((frame["SPY"]["c"][i1] / frame["SPY"]["c"][i0] - 1) * 100, 2)
    return a


def verdict(out):
    t = out["runs"]["test"]; v = out["runs"]["validation"]
    m15, m30, v15 = t["15"]["metrics"], t["30"]["metrics"], v["15"]["metrics"]
    an = out["analysis"]; ctl = out["control"]["test"]
    crit = {}
    crit["C1_trades>=60"] = m15.get("trades", 0) >= 60
    crit["C2_net_pnl>0_at_15bps_and_30bps"] = m15.get("net_pnl", -1) > 0 and m30.get("net_pnl", -1) > 0
    crit["C3_profit_factor>=1.25_at_15bps"] = (m15.get("profit_factor") or 0) >= 1.25
    crit["C4_expectancy>=0.10R_and_bootstrap_p10>0"] = m15.get("expectancy_R", -1) >= 0.10 and m15.get("expectancy_R_bootstrap_p10", -1) > 0
    crit["C5_max_drawdown<=50usd"] = m15.get("max_drawdown_usd", 1e9) <= 50.0
    crit["C6_beats_matched_control_p90"] = ctl["strategy_expectancy_R"] is not None and ctl["strategy_expectancy_R"] >= ctl["expectancy_R_p90"]
    crit["C7_validation_net_pnl>0_at_15bps"] = v15.get("net_pnl", -1) > 0
    top = an.get("test_top_symbol", {})
    crit["C8_robust_to_best_trade_and_top_symbol<=40%"] = an.get("test_net_without_best_trade", -1) > 0 and (top.get("share_of_net") is not None and top["share_of_net"] <= 0.40)
    ok = all(crit.values())
    return {"criteria": crit, "all_passed": ok, "label": "SUPPORTS FURTHER PAPER TESTING (never promotion)" if ok else "NOT SUPPORTED", "auto_promote": False}


def freeze():
    if not PLOCK.exists():
        raise SystemExit("PREREG.lock.json must exist first")
    if CLOCK.exists():
        raise SystemExit("CODE.lock.json already exists")
    CLOCK.write_text(json.dumps({"frozen_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "sha256": code_hashes(), "note": "frozen before any outcome was computed on real data"}, indent=1, sort_keys=True) + "\n", newline="\n")
    print("frozen", CLOCK)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--freeze", action="store_true"); ap.add_argument("--reproduce", action="store_true")
    a = ap.parse_args()
    if a.freeze:
        return freeze()
    check_locks()
    if a.reproduce:
        out = run_all()
        ref = json.loads(RESULTS.read_text())
        ref.pop("run_at_utc", None)
        same = json.dumps(out, sort_keys=True, default=float) == json.dumps(ref, sort_keys=True, default=float)
        print("REPRODUCED byte-for-byte" if same else "DIFFERENT"); return 0 if same else 1
    if RESULTS.exists():
        raise SystemExit("REFUSING: ch005_results.json exists (single-shot protocol). Use --reproduce to verify.")
    out = run_all()
    out["run_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    RESULTS.write_text(json.dumps(out, indent=1, sort_keys=True, default=float) + "\n", newline="\n")
    print(json.dumps({"verdict": out["verdict"], "test_15bps": out["runs"]["test"]["15"]["metrics"]}, indent=1, default=float))


if __name__ == "__main__":
    sys.exit(main() or 0)
