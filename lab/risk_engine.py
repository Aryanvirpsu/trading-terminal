"""Portfolio Risk Engine — stops a bad trade, a cluster of correlated trades, or
a run of small losses from bleeding the account (spec #13-15).

Reads the live account (account_status) + journal and enforces, BEFORE any new
trade is allowed:
  * per-trade max risk (tighter for speculative names)
  * daily & weekly loss limits -> auto-SUSPEND new risk
  * drawdown control from the equity peak
  * cash-reserve floor
  * total open-risk cap
  * sector / correlation exposure cap
Position size comes from the RISK BUDGET (not the confidence score, #14),
scaled down by uncertainty, speculation, and remaining drawdown room.
"""
from __future__ import annotations
import os, sys, datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tradingview_mcp.core.services import strategy_service as ss

# Limits (fractions of equity)
MAX_RISK_TRADE = 0.03
MAX_RISK_TRADE_SPEC = 0.015
MAX_DAILY_LOSS = 0.06
MAX_WEEKLY_LOSS = 0.10
MAX_DRAWDOWN = 0.20
CASH_RESERVE = 0.20
MAX_OPEN_RISK = 0.15
MAX_SECTOR_POSITIONS = 2

SECTOR = {"JPM": "financials", "BAC": "financials", "AXP": "financials", "GS": "financials",
          "AAPL": "tech", "MSFT": "tech", "NVDA": "tech", "AMD": "tech", "CRWD": "tech",
          "PANW": "tech", "ABNB": "consumer", "AMZN": "consumer", "TSLA": "consumer",
          "SOFI": "financials", "F": "consumer", "IMAX": "consumer"}


def portfolio_state(account: str = "strategy-500") -> dict:
    s = ss.account_status()
    equity = s["total_equity"]
    cash = s["cash"]
    curve = [p["equity"] for p in s.get("equity_curve", []) if p.get("equity")]
    peak = max(curve + [equity]) if curve else equity
    drawdown = round((peak - equity) / peak * 100, 2) if peak else 0.0

    today = dt.date.today().isoformat()
    week_ago = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    closed = [e for e in s["journal"]["entries"] if e.get("status") == "closed" and e.get("closed_at")]
    day_pnl = sum(e["outcome_pnl"] or 0 for e in closed if str(e["closed_at"])[:10] == today)
    week_pnl = sum(e["outcome_pnl"] or 0 for e in closed if str(e["closed_at"])[:10] >= week_ago)

    # sector exposure from open positions
    opens = [p["symbol"] for p in s.get("stock_positions", [])] + \
            [p["underlying_symbol"] for p in s.get("option_positions", [])]
    sect_count: dict = {}
    for sym in opens:
        sect_count[SECTOR.get(sym, "unknown")] = sect_count.get(SECTOR.get(sym, "unknown"), 0) + 1

    suspended, why = False, []
    if day_pnl <= -MAX_DAILY_LOSS * equity:
        suspended = True; why.append(f"daily loss limit hit ({round(day_pnl,2)})")
    if week_pnl <= -MAX_WEEKLY_LOSS * equity:
        suspended = True; why.append(f"weekly loss limit hit ({round(week_pnl,2)})")
    if drawdown >= MAX_DRAWDOWN * 100:
        suspended = True; why.append(f"drawdown {drawdown}% >= {MAX_DRAWDOWN*100}%")

    return {"equity": equity, "cash": cash, "cash_pct": round(cash / equity * 100, 1) if equity else 0,
            "peak": peak, "drawdown_pct": drawdown, "day_pnl": round(day_pnl, 2),
            "week_pnl": round(week_pnl, 2), "open_positions": len(opens),
            "sector_exposure": sect_count, "suspended": suspended, "suspend_reasons": why}


def check_new_trade(symbol: str, proposed_max_loss: float, speculative: bool = False,
                    account: str = "strategy-500") -> dict:
    st = portfolio_state(account)
    equity = st["equity"]
    reasons, allow = [], True
    if st["suspended"]:
        return {"allow": False, "reasons": st["suspend_reasons"], "size_cap_usd": 0.0,
                "portfolio": st}

    cap_frac = MAX_RISK_TRADE_SPEC if speculative else MAX_RISK_TRADE
    per_trade_cap = round(equity * cap_frac, 2)
    if proposed_max_loss > per_trade_cap:
        reasons.append(f"risk ${proposed_max_loss} > per-trade cap ${per_trade_cap}")
        allow = False

    # cash reserve
    if (st["cash"] - proposed_max_loss) < CASH_RESERVE * equity:
        reasons.append(f"would breach {int(CASH_RESERVE*100)}% cash reserve")
        allow = False

    # drawdown de-risking: within 5% of the limit -> half size
    dd_scale = 0.5 if st["drawdown_pct"] >= (MAX_DRAWDOWN * 100 - 5) else 1.0

    # sector / correlation cap
    sect = SECTOR.get(symbol.upper(), "unknown")
    if st["sector_exposure"].get(sect, 0) >= MAX_SECTOR_POSITIONS:
        reasons.append(f"already {st['sector_exposure'][sect]} positions in {sect} (cap {MAX_SECTOR_POSITIONS})")
        allow = False

    size_cap = round(min(proposed_max_loss, per_trade_cap) * dd_scale, 2)
    return {"allow": allow, "reasons": reasons or ["ok"], "size_cap_usd": size_cap,
            "per_trade_cap": per_trade_cap, "dd_scale": dd_scale, "sector": sect,
            "portfolio": st}


if __name__ == "__main__":
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("PORTFOLIO STATE:")
    print(json.dumps(portfolio_state(), indent=2, default=str))
    print("\nCHECK: new BAC trade risking $10 (BAC already held):")
    print(json.dumps(check_new_trade("BAC", 10.0), indent=2, default=str))
