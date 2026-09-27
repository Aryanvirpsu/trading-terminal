import json
import collections
import pandas as pd

d = json.load(open("friday_full.json"))
BARS = pd.read_csv("friday_5m.csv", header=[0, 1], index_col=0)
BARS.index = pd.to_datetime(BARS.index, utc=True)
BPS = 5.0

cycles = d["cycles"]
obs = d["observations"]
signals = {s["signal_id"]: s for s in d["signals"]}
orders = d["orders"]
audit = d["audit"]

STRATEGY_STAGES = {"symbol_resolution", "valid_levels", "quality_threshold", "positive_ev", "liquidity",
                    "signal_alignment", "critical_family", "data_quality", "freshness", "signal_disagreement",
                    "conviction"}
PORTFOLIO_REASONS = {"sector": "sector_cap", "entries_per_day": "daily_entry_cap", "max_open": "open_position_cap",
                     "duplicate_symbol": "duplicate_symbol"}

# ---------------------------------------------------------------------------
# 1. FUNNEL ATTRIBUTION (per cycle, aggregated), raw obs + independent events
# ---------------------------------------------------------------------------
print("=" * 100)
print("FUNNEL ATTRIBUTION — 2026-09-25, all cycles")
tot = collections.Counter()
for c in cycles:
    f = c["funnel"]
    for k in ("universe_considered", "scanner_detections", "finalists", "TRADEABLE", "MONITOR", "REJECT",
              "capacity_eligible", "orders_attempted", "entries", "entry_blocked_pre_broker"):
        tot[k] += f.get(k) or 0
print(dict(tot))

by_event = {}
for o in obs:
    if o["session_type"] == "manual":
        continue
    by_event.setdefault(o["event_id"], []).append(o)
print(f"raw observations (non-manual) = {sum(len(v) for v in by_event.values())}, independent events = {len(by_event)}")

# ---------------------------------------------------------------------------
# 2. STRATEGY vs PORTFOLIO attribution — where do TRADEABLE candidates actually die?
# ---------------------------------------------------------------------------
print("\n" + "=" * 100)
print("STRATEGY GATE vs PORTFOLIO/EXECUTION CONSTRAINT — fate of every non-manual observation")
fate = collections.Counter()
strategy_reject = collections.Counter()
portfolio_block = collections.Counter()
for o in obs:
    if o["session_type"] == "manual":
        continue
    fg = json.loads(o["failed_gates"] or "[]")
    if o["decision"] == "REJECT":
        fate["strategy_reject (REJECT)"] += 1
        for g in fg:
            strategy_reject[g] += 1
    elif o["decision"] == "MONITOR":
        fate["strategy_soft_gate (MONITOR)"] += 1
        for g in fg:
            strategy_reject[g] += 1
    elif o["decision"] == "TRADEABLE":
        if o["champion_executed"]:
            fate["EXECUTED"] += 1
        else:
            cr = o["capacity_reason"]
            if cr in PORTFOLIO_REASONS:
                fate[f"portfolio_block:{PORTFOLIO_REASONS[cr]}"] += 1
                portfolio_block[PORTFOLIO_REASONS[cr]] += 1
            elif o["session_type"] == "post_cutoff":
                fate["portfolio_block:entry_cutoff"] += 1
                portfolio_block["entry_cutoff"] += 1
            else:
                reason = (o["champion_reason"] or "")[:60]
                fate[f"other_not_executed:{reason}"] += 1
print("observation fate:", dict(fate))
print("strategy soft/hard gates hit (MONITOR+REJECT observations):", dict(strategy_reject.most_common()))
print("portfolio/execution blocks (TRADEABLE, not executed):", dict(portfolio_block))

# same, by unique EVENT (each event counted once, by its most-favourable decision that day)
event_best = {}
for eid, rows in by_event.items():
    order = {"TRADEABLE": 2, "MONITOR": 1, "REJECT": 0}
    best = max(rows, key=lambda r: order[r["decision"]])
    event_best[eid] = best
print(f"\nBy EVENT ({len(event_best)} events), best decision reached that day:")
print(dict(collections.Counter(r["decision"] for r in event_best.values())))
executed_events = {eid for eid, r in event_best.items() if r["champion_executed"]}
blocked_tradeable_events = {eid: r for eid, r in event_best.items()
                           if r["decision"] == "TRADEABLE" and not r["champion_executed"]}
print("events that reached TRADEABLE but were never executed:",
      [(r["symbol"], r["capacity_reason"], r["session_type"]) for r in blocked_tradeable_events.values()])

# ---------------------------------------------------------------------------
# 3. CANDIDATE SUPPLY / CONVERSION per stage
# ---------------------------------------------------------------------------
print("\n" + "=" * 100)
print("CANDIDATE SUPPLY / CONVERSION (aggregate across the day's 27 cycles)")
scanner_hits = tot["scanner_detections"]
finalists = tot["finalists"]
tradeable = tot["TRADEABLE"]
entries = tot["entries"]
print(f"scanner_detections {scanner_hits} -> finalists {finalists}  (finalist rate {finalists/scanner_hits*100:.1f}%)")
print(f"finalists {finalists} -> TRADEABLE {tradeable}  (TRADEABLE rate {tradeable/finalists*100:.1f}%)")
print(f"TRADEABLE {tradeable} -> entries {entries}  (entry rate {entries/tradeable*100:.1f}%)  <-- LARGEST DROP")
print(f"per-event: {len(event_best)} events -> {sum(1 for r in event_best.values() if r['decision']=='TRADEABLE')} reached TRADEABLE -> {len(executed_events)} entered")

# ---------------------------------------------------------------------------
# 4. Counterfactual replay for blocked TRADEABLE events (executable-price assumptions)
# ---------------------------------------------------------------------------
def replay(entry, stop, target, created_at, symbol, bps=BPS):
    ef = entry * (1 + bps / 1e4)
    risk = entry - stop
    b = BARS[symbol].dropna()
    b = b[b.index >= pd.Timestamp(created_at)]
    trig = entry + risk
    plus1r_ts = None
    for ts, row in b.iterrows():
        o, h, l = row["Open"], row["High"], row["Low"]
        hit_stop = l <= stop
        hit_tgt = h >= target
        if plus1r_ts is None and h >= trig:
            plus1r_ts = ts
        if hit_stop and hit_tgt:
            return dict(outcome="AMBIGUOUS", R=None, exit_ts=str(ts), plus1r_ts=str(plus1r_ts) if plus1r_ts else None)
        if hit_stop:
            return dict(outcome="stop", R=(stop * (1 - bps / 1e4) - ef) / risk, exit_ts=str(ts),
                        plus1r_ts=str(plus1r_ts) if plus1r_ts else None)
        if hit_tgt:
            return dict(outcome="target", R=(target - ef) / risk, exit_ts=str(ts),
                        plus1r_ts=str(plus1r_ts) if plus1r_ts else None)
    if len(b) == 0:
        return dict(outcome="no_data", R=None, exit_ts=None, plus1r_ts=None)
    last = float(b["Close"].iloc[-1])
    mfe = (b["High"].max() - entry) / risk
    mae = (entry - b["Low"].min()) / risk
    return dict(outcome="open", R=(last - ef) / risk, exit_ts=None,
               plus1r_ts=str(plus1r_ts) if plus1r_ts else None)


print("\n" + "=" * 100)
print("BLOCKED-EVENT COUNTERFACTUALS (decision-time executable assumptions, 5-minute bars)")
results = []
for eid, r in blocked_tradeable_events.items():
    rep = replay(r["entry"], r["stop"], r["target"], r["scan_ts"], r["symbol"])
    b = BARS[r["symbol"]].dropna()
    b = b[b.index >= pd.Timestamp(r["scan_ts"])]
    mfe = round((b["High"].max() - r["entry"]) / (r["entry"] - r["stop"]), 2) if len(b) else None
    mae = round((r["entry"] - b["Low"].min()) / (r["entry"] - r["stop"]), 2) if len(b) else None
    results.append(dict(symbol=r["symbol"], event_id=eid, reason=r["capacity_reason"] or ("entry_cutoff" if r["session_type"] == "post_cutoff" else "?"),
                        entry=r["entry"], stop=r["stop"], target=r["target"], **rep, mfe_R=mfe, mae_R=mae))
    print(f"{r['symbol']:5s} reason={results[-1]['reason']:16s} entry={r['entry']:.2f} stop={r['stop']:.2f} target={r['target']:.2f}"
          f" -> outcome={rep['outcome']:8s} R={rep.get('R')} mfe={mfe}R mae={mae}R +1R_at={rep.get('plus1r_ts')}")
json.dump(results, open("blocked_counterfactuals.json", "w"), indent=1, default=str)

# actual chosen trades for comparison
print("\nCHOSEN (actual) trades for comparison:")
for sym, entry_field in (("DELL", None), ("META", None)):
    o = next(x for x in obs if x["symbol"] == sym and x["champion_executed"])
    rep = replay(o["entry"], o["stop"], o["target"], o["scan_ts"], sym)
    print(f"{sym}: entry={o['entry']} stop={o['stop']} target={o['target']} -> {rep}")

# ---------------------------------------------------------------------------
# 5. Capacity choice-events: chosen vs blocked, same cycle
# ---------------------------------------------------------------------------
print("\n" + "=" * 100)
print("CAPACITY CHOICE EVENTS (same cycle, one slot, competing eligible candidates)")
by_cycle = collections.defaultdict(list)
for o in obs:
    if o["session_type"] != "manual":
        by_cycle[o["cycle_id"]].append(o)
for cid, rows in sorted(by_cycle.items()):
    elig = [r for r in rows if r["eligible"]]
    if len(elig) > 1:
        picked = [r["symbol"] for r in elig if r["champion_executed"] or (r["capacity_reason"] is None and r["champion_reason"] == "executed")]
        blocked = [(r["symbol"], r["capacity_reason"]) for r in elig if r["symbol"] not in picked]
        print(cid, "eligible:", [r["symbol"] for r in elig], "| picked:", picked, "| blocked:", blocked)
