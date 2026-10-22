"""CH-005 post-hoc DIAGNOSTIC D1 (exploratory; NOT part of the pre-registered verdict, which is already fixed by ch005_results.json).

Question: is the result caused by AVDI's $50 drawdown halt ending the account early, or by the trades themselves?
Method: re-run the frozen engine with ONLY the drawdown halt switched off, on the TRAIN and VALIDATION periods (both already examined).
The TEST period is deliberately NOT run in this variant: it stays unspent for any future, separately registered challenger.
Nothing here changes any rule, parameter or production limit; a halt-free account is not deployable under AVDI's limits.

    python experiments/ch005_pullback_uptrend/diagnostic_d1.py
"""
import importlib.util, json, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ch005", HERE / "ch005.py")
C = importlib.util.module_from_spec(spec); sys.modules["ch005"] = C; spec.loader.exec_module(C)
E = C.E

uni, cal, frame = C.setup()
out = {"note": "post-hoc diagnostic D1: drawdown halt OFF, train+validation only; does not alter the verdict", "runs": {}}
for cost in (5.0, 15.0, 30.0):
    p = E.Params(cost_bps=cost, drawdown_halt=1e12)
    ind, pull, base, spy_c, mk = E.prepare(frame, p)
    for per in ("train", "validation"):
        s, e = C.PERIODS[per]
        res = E.simulate(frame, ind, pull, base, spy_c, uni, cal, p, s, e, "rank", 0)
        m = E.metrics(res, p, s, cal)
        R = np.array([t["R"] for t in res["trades"]])
        if len(R):
            m["expectancy_R_bootstrap_p10"] = E.bootstrap_lower(R, 10.0, 10_000, C.BOOT_SEED)
        yrs = {}
        for t in res["trades"]:
            d = yrs.setdefault(t["entry"][:4], {"n": 0, "pnl": 0.0}); d["n"] += 1; d["pnl"] = round(d["pnl"] + t["pnl"], 2)
        m["by_year"] = yrs
        out["runs"][f"{per}_{int(cost)}bps"] = m
        if cost == 15.0:
            cm = []
            for seed in range(1, 101):
                r2 = E.simulate(frame, ind, pull, base, spy_c, uni, cal, p, s, e, "random", seed)
                mm = E.metrics(r2, p, s, cal)
                cm.append(mm.get("expectancy_R"))
            cm = np.array([x for x in cm if x is not None])
            out.setdefault("control_15bps_no_halt", {})[per] = {"expectancy_R_mean": float(cm.mean()), "p90": float(np.percentile(cm, 90)),
                                                                   "strategy_percentile": float((cm < m["expectancy_R"]).mean() * 100)}
Path(HERE / "diagnostic_d1_results.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=float) + "\n", newline="\n")
for k, m in out["runs"].items():
    print(f"{k:20s} trades {m['trades']:4d} net {m['net_pnl']:8.2f} PF {m.get('profit_factor') and round(m['profit_factor'],2)} E[R] {m['expectancy_R']:.3f} (p10 {m.get('expectancy_R_bootstrap_p10', float('nan')):.3f}) win {m['win_rate']:.0%} maxDD ${m['max_drawdown_usd']:.0f}")
print(json.dumps(out["control_15bps_no_halt"], indent=1, default=float))
