"""End-to-end research run: regime → sectors → scan → finalists → chains → verdicts
→ tracker. READ-ONLY: this script has no order path and never calls a write tool.

    python automation/research_run.py                 # full run
    python automation/research_run.py --no-track      # analyse only, track nothing
    python automation/research_run.py --preset etf
    python automation/research_run.py update          # just re-check tracked setups
    python automation/research_run.py status          # tracker + schedule snapshot
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("", "dashboard", "lab", "src"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

try:
    import _config  # noqa: F401  — loads .env
except Exception:  # noqa: BLE001
    pass


def _hr(t=""):
    print("\n" + "═" * 88)
    if t:
        print(t)
        print("═" * 88)


def run(preset: str = "liquid", track: bool = True) -> dict:
    import market_regime as MR
    import scanner as SC
    import options_desk as OD
    import tracker as TR

    t0 = time.time()
    out = {"started_at": datetime.now(timezone.utc).isoformat(), "preset": preset}

    # 1 — session + regime -----------------------------------------------------
    _hr("1. SESSION + MARKET REGIME")
    reg = MR.overview(blocking=True)
    s, r = reg["session"], reg["regime"]
    print(f"  {s['state'].upper()} — {s['reason']}")
    print(f"  New York {s['now_et']}   India {s['now_ist']}")
    print(f"  Next regular session: {s['next_session']['date']} "
          f"({s['next_session']['hours_until_open']}h away)")
    print(f"  Regime: {r['headline']}  (risk score {r['risk_score']}, "
          f"coverage {r['data_coverage']}, confidence {r['confidence']})")
    for e in r["evidence"]:
        print(f"    · [{e['factor']}] {e['observation']}")
    out["session"], out["regime"] = s, {k: r[k] for k in
                                        ("headline", "risk_score", "labels", "confidence")}

    # 2 — sectors + 3 — scan ---------------------------------------------------
    _hr("2. SECTOR RANKING  +  3. UNIVERSE SCAN")
    scan = SC.scan(preset, regime_data=reg)
    for x in scan["sectors"]:
        b = x.get("breadth") or {}
        print(f"  #{str(x.get('rank') or '-'):<3} {x['name']:<24} score {str(x.get('rank_score')):>7}"
              f"   RS {str(x.get('rs_vs_spy_1m')):>6}   5D {str(x.get('perf_5d')):>6}"
              f"   breadth {b.get('advancers')}/{b.get('decliners')} of {b.get('requested')}"
              f"{'' if b.get('complete', True) else '  [PARTIAL]'}")
    print()
    for st in scan["stages"]:
        print(f"  {st['stage']:<16}{st['count']:>6}   {st['note'][:88]}")
    print(f"  {'rejected':<16}{scan['rejected_count']:>6}")
    out["stages"] = {st["stage"]: st["count"] for st in scan["stages"]}
    out["rejected_count"] = scan["rejected_count"]

    from collections import Counter
    by_stage = Counter(x["stage"] for x in scan["rejected"])
    print("\n  Rejections by stage: " + ", ".join(f"{k}={v}" for k, v in by_stage.most_common()))
    out["rejected_by_stage"] = dict(by_stage)

    # 4 — finalists ------------------------------------------------------------
    _hr("4. FINALISTS (deep analysis)")
    for c in scan["finalists"]:
        i, l = c["indicators"], c["levels"]
        print(f"  {c['symbol']:<6} score {c['score']['total']:<6} {c['primary_setup']}")
        print(f"         {c['sector_name']} (#{c['sector_rank']})  price {i['price']}  "
              f"ATR {i['atr_pct']}%  RVOL {i['rel_volume']}  RSI {i['rsi14']}")
        print(f"         entry {l['entry_zone']}  stop {l['invalidation']} ({l['invalidation_basis']})"
              f"  T1 {l['target_1']} T2 {l['target_2']}  R:R {l['rr_target_1']}/{l['rr_target_2']}")
        print(f"         evidence: {c['setups'][0]['evidence']}")

    # 5 — account + 6 — chains + 7 — verdicts ---------------------------------
    _hr("5. ROBINHOOD ACCOUNT (read-only)  +  6. OPTION CHAINS  +  7. VERDICTS")
    acct = OD.account()
    print(f"  Account {acct.get('account_masked')} ({acct.get('account_type')}) "
          f"buying power {acct.get('buying_power')} · positions {acct.get('position_count')} "
          f"· read_only={acct.get('read_only')}")
    out["account"] = {"buying_power": acct.get("buying_power"), "read_only": acct.get("read_only"),
                      "state": acct.get("state")}

    decisions = []
    for c in scan["finalists"]:
        ev = OD.evaluate_candidate(c, account_info=acct)
        b, d = ev.get("best_contract"), ev["decision"]
        print(f"\n  {ev['symbol']}: {d['verdict'].upper()}  "
              f"({ev['contracts_passing']}/{ev['contracts_analysed']} contracts passed, "
              f"provider {ev['provider']['contracts']})")
        if b:
            print(f"     contract {b['expiry']} ${b['strike']} {b['side']}  "
                  f"bid/ask {b['bid']}/{b['ask']}  spread {b['spread_pct']}%  "
                  f"OI {b['open_interest']}  IV {b['implied_volatility']}")
            print(f"     limit {b['limit_price']} ({b['limit_basis']})  max loss {b['max_loss']}  "
                  f"BE {b['break_even']} (needs {b['pct_move_to_break_even']}%)  "
                  f"theta {b['theta_pct_of_premium_per_day']}%/day  DTE {b['dte']}")
            print(f"     EV: {b.get('expected_value') if b.get('expected_value') is not None else 'not published — ' + str(b.get('expected_value_unavailable_reason'))[:90]}")
        else:
            print(f"     no contract passed the gates")
        for x in d["reasons_for_stock"]:
            print(f"       stock : {x}")
        for x in d["reasons_for_option"]:
            print(f"       option: {x}")
        sz = (d.get("sizing") or {}).get("shares") or {}
        print(f"     sizing: {sz.get('quantity')} shares, {sz.get('notional')} notional, "
              f"max loss {sz.get('max_loss')} ({sz.get('pct_of_account_at_risk')}% of account), "
              f"binding {sz.get('binding_constraint')}")
        decisions.append({"symbol": ev["symbol"], "verdict": d["verdict"],
                          "contract": (f"{b['expiry']} {b['strike']}{b['side'][0]}" if b else None),
                          "candidate": c, "evaluation": ev})
    out["decisions"] = [{k: d[k] for k in ("symbol", "verdict", "contract")} for d in decisions]

    # 8 — tracker --------------------------------------------------------------
    _hr("8. TRACKER")
    if track:
        for d in decisions:
            res = TR.add_setup(d["candidate"], verdict=d["evaluation"]["decision"],
                               contract=d["evaluation"].get("best_contract"))
            print(f"  {d['symbol']:<6} {res['state']}"
                  + (f"  id={res.get('setup_id')}" if res.get("setup_id") else
                     f"  ({res.get('reason','')})"))
        upd = TR.update_all()
        print(f"\n  re-check: {upd['checked']} checked, {upd['updated']} updated, "
              f"{upd['alerts']} alerts fired")
        for ch in upd.get("changes", []):
            print(f"    {ch['symbol']}: {ch['status']} — {ch.get('reason','')}")
    else:
        print("  --no-track: nothing added")
    st = TR.stats()
    print(f"\n  tracker status counts: {st['status_counts']}")
    print(f"  ORDER PLACEMENT: {st['order_placement']}")
    nxt = TR.next_runs()
    print("  next scheduled runs:")
    for u in nxt["upcoming"][:4]:
        if u.get("skipped"):
            print(f"    {u['date']} skipped — {u['reason']}")
        else:
            print(f"    {u['run']:<16} {u['at_et']} ET / {u['at_ist']} IST (in {u['in_minutes']}m)")
    out["tracker"] = st["status_counts"]
    out["order_placement"] = st["order_placement"]

    out["took_seconds"] = round(time.time() - t0, 1)
    _hr(f"DONE in {out['took_seconds']}s — no order was placed, previewed or queued.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="run", choices=["run", "update", "status"])
    ap.add_argument("--preset", default="liquid")
    ap.add_argument("--no-track", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    import tracker as TR
    if a.command == "update":
        print(json.dumps(TR.update_all(), indent=2, default=str))
        return 0
    if a.command == "status":
        print(json.dumps({"stats": TR.stats(), "setups": TR.list_setups(),
                          "schedule": TR.next_runs()}, indent=2, default=str))
        return 0
    res = run(a.preset, track=not a.no_track)
    if a.json:
        print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
