"""AVDI Companion v0.1 -- prints the real decision-time values for one signal, ready to paste into
avdi_companion.pine's Inputs panel (same folder). Display-only: reads lab/paper/db.py's real `signals`
table and writes nothing, places no order, touches no account state.

Usage (run wherever the real ledger lives -- locally against PAPER_DATA_DIR, or on the Ubuntu host via
`docker exec avdi-runtime python tools/tradingview_companion/generate_overlay_values.py ...`):

    python generate_overlay_values.py --symbol NVDA                 # latest TRADEABLE signal for NVDA
    python generate_overlay_values.py --signal-id sig_1c0f3ed8f238ced1
    python generate_overlay_values.py --symbol NVDA --session-date 2026-10-01
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in ("lab", "dashboard", "src"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


def fetch_signal(symbol: Optional[str], signal_id: Optional[str],
                 session_date: Optional[str]) -> Optional[Dict[str, Any]]:
    """The real signal row, straight from `paper.db` -- no inference, no synthesis. Prefers the most
    recent TRADEABLE signal for `symbol` (optionally narrowed to `session_date`) when `signal_id` isn't
    given directly."""
    from paper import db

    if signal_id:
        return db.query_one("SELECT * FROM signals WHERE signal_id=?", (signal_id,))
    if not symbol:
        raise ValueError("must supply either --symbol or --signal-id")
    if session_date:
        return db.query_one(
            "SELECT * FROM signals WHERE symbol=? AND session_date=? AND action='TRADEABLE' "
            "ORDER BY created_at DESC LIMIT 1", (symbol.upper(), session_date))
    return db.query_one(
        "SELECT * FROM signals WHERE symbol=? AND action='TRADEABLE' ORDER BY created_at DESC LIMIT 1",
        (symbol.upper(),))


def overlay_values(sig: Dict[str, Any]) -> Dict[str, Any]:
    """The exact six fields avdi_companion.pine's Inputs panel expects, read directly from the signal row.
    A field AVDI itself never recorded for this signal (e.g. a REJECT with no entry/stop/target, or a
    missing scanner_rank) is reported as None here -- never silently defaulted to 0 or a placeholder string,
    so the caller can see and decide what an absent field means rather than having it invisibly become a
    plausible-looking zero on the chart."""
    return {
        "entry_price": sig.get("entry"), "stop_price": sig.get("stop"), "target_price": sig.get("target"),
        "scanner_rank": sig.get("scanner_rank"), "strategy_name": sig.get("strategy"),
        "risk_amount": sig.get("planned_risk"), "regime_label": sig.get("market_regime"),
    }


def format_for_pine_inputs(values: Dict[str, Any]) -> str:
    def fmt(v, kind):
        if v is None:
            return "0" if kind == "num" else "(not recorded by AVDI for this signal)"
        return str(v)

    return (
        f"Entry price:            {fmt(values['entry_price'], 'num')}\n"
        f"Stop price:              {fmt(values['stop_price'], 'num')}\n"
        f"Target price:            {fmt(values['target_price'], 'num')}\n"
        f"Scanner rank:            {fmt(values['scanner_rank'], 'num')}\n"
        f"Strategy:                {fmt(values['strategy_name'], 'str')}\n"
        f"Risk amount ($):         {fmt(values['risk_amount'], 'num')}\n"
        f"Regime / market state:   {fmt(values['regime_label'], 'str')}\n"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol")
    ap.add_argument("--signal-id")
    ap.add_argument("--session-date")
    args = ap.parse_args(argv)

    sig = fetch_signal(args.symbol, args.signal_id, args.session_date)
    if sig is None:
        print("No matching signal found -- nothing to display.", file=sys.stderr)
        return 1

    values = overlay_values(sig)
    print(f"signal_id={sig['signal_id']}  symbol={sig['symbol']}  session_date={sig['session_date']}  "
         f"action={sig['action']}\n")
    print("Paste into avdi_companion.pine's Inputs panel:\n")
    print(format_for_pine_inputs(values))
    missing = [k for k, v in values.items() if v is None]
    if missing:
        print(f"Not recorded by AVDI for this signal (left as 0/blank above, not inferred): {missing}",
             file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
