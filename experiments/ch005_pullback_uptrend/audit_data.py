"""CH-005 data audit (no strategy outcome is computed).  python -I experiments/ch005_pullback_uptrend/audit_data.py

Checks, per symbol, against the SPY session calendar:
  calendar     every bar date is a weekday and a SPY session; no duplicate dates; strictly increasing
  completeness missing sessions vs SPY (gaps) in the usable window; first/last date; history length
  integrity    OHLC ordering (l <= o,c <= h), positive prices, non-negative volume, zero-volume days
  corporate    day-over-day close ratio outside [0.6, 1.6] on a day with no Yahoo-reported split => suspected UNADJUSTED event or bad bar;
  actions      Yahoo-reported splits are listed (the series are already split-adjusted by Yahoo)
  freshness    last bar must be a completed session (not today's partial bar)
  survivorship the universe is the CURRENT AVDI sector map; delisted names are absent by construction (disclosed bias, not testable here)
Writes data/data_audit.json and prints a summary.
"""
import datetime as dt, gzip, hashlib, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
D = HERE / "data"
bars = json.loads(gzip.open(D / "prices_daily.json.gz").read())
man = json.loads((D / "data_manifest.json").read_text())
spy_days = bars["SPY"]["d"]
spy_set = set(spy_days)
out = {"spy_first": spy_days[0], "spy_last": spy_days[-1], "spy_sessions": len(spy_days), "symbols": {}, "flags": []}

for s, b in bars.items():
    d = b["d"]
    ent = {"n": len(d), "first": d[0], "last": d[-1]}
    ent["not_increasing"] = sum(1 for a, c in zip(d, d[1:]) if c <= a)
    ent["weekend_dates"] = sum(1 for x in d if dt.date.fromisoformat(x).weekday() >= 5)
    ent["dates_not_in_spy_calendar"] = [x for x in d if x not in spy_set][:5]
    window = [x for x in spy_days if x >= d[0]]
    have = set(d)
    miss = [x for x in window if x not in have]
    ent["missing_sessions"] = len(miss); ent["missing_examples"] = miss[:5]
    bad_ohlc = sum(1 for o, h, l, c in zip(b["o"], b["h"], b["l"], b["c"]) if not (l <= min(o, c) + 1e-9 and h + 1e-9 >= max(o, c)))
    ent["ohlc_violations"] = bad_ohlc
    ent["nonpositive_price"] = sum(1 for x in b["c"] + b["o"] + b["h"] + b["l"] if x <= 0)
    ent["negative_volume"] = sum(1 for x in b["v"] if x < 0)
    ent["zero_volume_days"] = sum(1 for x in b["v"] if x == 0)
    split_dates = {x[0] for x in man["per_symbol"][s]["splits"]}
    jumps = []
    for i in range(1, len(d)):
        r = b["c"][i] / b["c"][i - 1]
        if (r < 0.6 or r > 1.6):
            jumps.append((d[i], round(r, 3), d[i] in split_dates))
    ent["close_jumps_outside_0.6_1.6"] = jumps
    ent["reported_splits"] = man["per_symbol"][s]["splits"]
    out["symbols"][s] = ent
    if ent["not_increasing"] or ent["weekend_dates"] or ent["ohlc_violations"] or ent["nonpositive_price"] or ent["negative_volume"]:
        out["flags"].append((s, "integrity"))
    if ent["dates_not_in_spy_calendar"]:
        out["flags"].append((s, "date_not_in_spy_calendar"))
    if any(not j[2] for j in jumps):
        out["flags"].append((s, "unexplained_jump"))
today_et_guess = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date().isoformat()
out["last_bar_is_today_partial_risk"] = spy_days[-1] >= today_et_guess
out["tz_values"] = sorted({v["tz"] for v in man["per_symbol"].values()})
ns = [v["n"] for k, v in out["symbols"].items() if k != "SPY"]
out["summary"] = {"symbols": len(ns), "min_history": min(ns), "median_history": sorted(ns)[len(ns) // 2],
                  "symbols_with_missing_sessions": sum(1 for v in out["symbols"].values() if v["missing_sessions"]),
                  "total_missing_sessions": sum(v["missing_sessions"] for v in out["symbols"].values()),
                  "symbols_with_zero_volume_days": sum(1 for v in out["symbols"].values() if v["zero_volume_days"]),
                  "symbols_first_bar_after_2015-01-01": sorted(k for k, v in out["symbols"].items() if v["first"] > "2015-01-01" and k != "SPY"),
                  "unexplained_jumps": [(s, j) for s, v in out["symbols"].items() for j in v["close_jumps_outside_0.6_1.6"] if not j[2]],
                  "explained_split_jumps": sum(1 for v in out["symbols"].values() for j in v["close_jumps_outside_0.6_1.6"] if j[2])}
(D / "data_audit.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", newline="\n")
print(json.dumps({"flags": out["flags"], "last_bar_is_today_partial_risk": out["last_bar_is_today_partial_risk"], "tz": out["tz_values"], "spy": [out["spy_first"], out["spy_last"], out["spy_sessions"]], **out["summary"]}, indent=1))
