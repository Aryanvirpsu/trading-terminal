"""Calibration loop (#9/#19) — make the confidence score HONEST over time.

Every engine evaluation is logged. When a journal trade later closes, we match it
back to the prediction and record predicted-confidence vs actual win/loss. Bucketed
by confidence band, we measure whether an "80" actually wins ~80%. If a band is
mis-calibrated, `adjustment()` returns a factor the engine applies to shrink future
scores toward reality — so confidence can't drift into fantasy.

Honest v1 caveat: calibration needs SAMPLES. Until >= MIN_SAMPLES matched trades
exist, adjustment is neutral (1.0) and the report says "insufficient data". It
gets trustworthy only after a few dozen logged, resolved trades — no shortcut.
"""
from __future__ import annotations
import json, os, sys, datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tradingview_mcp.core import portfolio

DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
PRED_FILE = os.path.join(DATA_DIR, "predictions.jsonl")
CALIB_FILE = os.path.join(DATA_DIR, "calibration.json")
MIN_SAMPLES = 20
BANDS = [("<40", 0, 40), ("40-55", 40, 55), ("55-70", 55, 70), ("70+", 70, 101)]


def log_prediction(r: dict) -> None:
    """Append one engine output as a prediction record."""
    if not r or "confidence_quality" not in r:
        return
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "symbol": r.get("symbol"),
           "direction": r.get("direction"), "decision": r.get("decision"),
           "quality": r.get("confidence_quality"), "p_direction": r.get("p_direction"),
           "p_trade": r.get("p_trade_profitable")}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PRED_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _load_predictions() -> list:
    if not os.path.exists(PRED_FILE):
        return []
    out = []
    for line in open(PRED_FILE, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _band(q):
    for name, lo, hi in BANDS:
        if lo <= q < hi:
            return name
    return "?"


def calibrate(account: str = "strategy-500") -> dict:
    preds = _load_predictions()
    closed = [e for e in portfolio.get_journal(account).get("entries", [])
              if e.get("status") == "closed" and e.get("created_at")]

    pairs = []   # (quality, p_direction, win)
    for tr in closed:
        sym, made = tr["symbol"], str(tr["created_at"])
        # most recent prediction for this symbol at/before the trade was opened
        cands = [p for p in preds if p.get("symbol") == sym and str(p.get("ts", "")) <= made + "Z"]
        if not cands:
            continue
        p = max(cands, key=lambda x: x.get("ts", ""))
        pairs.append((p.get("quality") or 0, p.get("p_direction") or 0.5,
                      1 if (tr.get("outcome_pnl") or 0) > 0 else 0))

    band_stats = {}
    for name, lo, hi in BANDS:
        wins = [w for q, _, w in pairs if lo <= q < hi]
        if wins:
            band_stats[name] = {"n": len(wins), "realized_winrate": round(sum(wins) / len(wins) * 100, 1),
                                "predicted_mid": (lo + hi) / 2}
    brier = round(sum((pd - w) ** 2 for _, pd, w in pairs) / len(pairs), 4) if pairs else None

    n = len(pairs)
    adjustments = {}
    for name, lo, hi in BANDS:
        bs = band_stats.get(name)
        if n >= MIN_SAMPLES and bs and bs["predicted_mid"]:
            # shrink predicted toward realized (50% weight) -> a factor on future quality
            adjustments[name] = round((bs["realized_winrate"] / bs["predicted_mid"]) * 0.5 + 0.5, 3)
        else:
            adjustments[name] = 1.0

    payload = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "matched_samples": n, "min_samples_for_trust": MIN_SAMPLES,
               "sufficient": n >= MIN_SAMPLES, "brier_score": brier,
               "band_stats": band_stats, "adjustments": adjustments,
               "note": ("Calibrated." if n >= MIN_SAMPLES else
                        f"INSUFFICIENT DATA ({n}/{MIN_SAMPLES}) — adjustments neutral until "
                        "more predictions resolve. This is honest, not a bug.")}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump(payload, open(CALIB_FILE, "w", encoding="utf-8"), indent=2, default=str)
    except Exception:
        pass
    return payload


def adjustment(quality: float) -> float:
    """Factor to scale a fresh quality score by, from the latest calibration.
    1.0 (neutral) until there's enough resolved data to trust."""
    try:
        c = json.load(open(CALIB_FILE, encoding="utf-8"))
        if not c.get("sufficient"):
            return 1.0
        return c.get("adjustments", {}).get(_band(quality), 1.0)
    except Exception:
        return 1.0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    r = calibrate()
    print(f"=== CALIBRATION · {r['matched_samples']} matched samples · "
          f"Brier {r['brier_score']} ===")
    print(f"  {r['note']}")
    for b, s in r["band_stats"].items():
        print(f"  band {b}: n={s['n']} realized {s['realized_winrate']}% (predicted ~{s['predicted_mid']}%)")
