"""Auto-execute the decision engine's TRADEABLE picks on the PAPER account, then
feed everything to calibration. Runs at market open (wired into daily_runner).

Discipline: PAPER only, stock only, portfolio-risk-gated, skips names already
open, capped per run. Every pick's prediction is already logged by the engine;
journaling the trade lets calibration match prediction -> outcome when it closes.
"""
from __future__ import annotations
import os, sys, datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from tradingview_mcp.core.services import strategy_service as ss
from tradingview_mcp.core.services.paper_trading_service import paper_trade
from tradingview_mcp.core import portfolio
import decision_engine as de
import calibration
import risk_engine

ACCOUNT = "strategy-500"
UNIVERSE = ["AAPL", "BAC", "NVDA", "AMD", "SOFI", "F", "PLTR", "ABNB", "AXP", "CRWD"]


def run(max_new: int = 3) -> dict:
    ss.setup_account(reset=False)
    bal = ss._live_balance()
    res = de.sweep(UNIVERSE, balance=bal)          # auto-logs predictions

    open_syms = {p["symbol"] for p in portfolio.get_portfolio(ACCOUNT).get("positions", [])}
    open_syms |= {e["symbol"] for e in portfolio.get_journal(ACCOUNT).get("entries", [])
                  if e.get("status") == "open"}

    tradeable = sorted([r for r in res.values() if r.get("decision") == "TRADEABLE"],
                       key=lambda r: -(r.get("confidence_quality") or 0))
    taken, skipped = [], []
    for r in tradeable:
        if len(taken) >= max_new:
            break
        sym = r["symbol"]
        shares = round(r.get("suggested_shares") or 0, 4)
        if sym in open_syms or shares <= 0:
            skipped.append((sym, "already-open/zero-size")); continue
        rg = risk_engine.check_new_trade(sym, r.get("max_loss_usd") or 0)
        if not rg.get("allow"):
            skipped.append((sym, "; ".join(rg.get("reasons", [])))); continue
        try:
            paper_trade(sym, shares, "BUY", exchange="NASDAQ", user_id=ACCOUNT)
            ss.log_trade(symbol=sym, instrument_type="STOCK", setup_type="engine",
                         thesis=(f"Auto @open: engine TRADEABLE q{r['confidence_quality']} "
                                 f"P(dir){r['p_direction']} EV/sh ${r['expected_value_per_share']}"),
                         entry=r["price"], stop=r["stop"], targets=[r["target"]], quantity=shares,
                         notes="Auto-executed from decision_engine; feeds calibration on close.")
            taken.append({"symbol": sym, "shares": shares, "quality": r["confidence_quality"]})
        except Exception as e:
            skipped.append((sym, str(e)[:60]))

    cal = calibration.calibrate()
    return {"ran_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "tradeable_found": len(tradeable), "taken": taken, "skipped": skipped,
            "calibration_samples": cal.get("matched_samples")}


if __name__ == "__main__":
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    r = run()
    print(f"AUTO-PAPER: took {len(r['taken'])} of {r['tradeable_found']} tradeable | "
          f"calibration {r['calibration_samples']} samples")
    for t in r["taken"]:
        print(f"  BUY {t['symbol']} {t['shares']}sh (q{t['quality']})")
    for s in r["skipped"][:6]:
        print(f"  skip {s[0]}: {s[1]}")
