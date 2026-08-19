"""Trade journal — the evidence record.

Every decision the engine makes is journalled, **including the ones we refuse to
trade**. That is the whole point: if we only record what we executed, we can never
learn whether the gates are protecting us or filtering out the winners. REJECT and
MONITOR signals are tracked forward exactly like live positions (MFE / MAE / eventual
outcome against their hypothetical entry, stop and target).
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Optional

from . import db
from . import fills


def _j(v: Any) -> Optional[str]:
    return json.dumps(v, default=str) if v is not None else None


def record_signal(result: Dict[str, Any], *, strategy: str, sector: Optional[str] = None,
                  industry: Optional[str] = None, market_regime: Optional[str] = None,
                  scanner_rank: Optional[int] = None, quantity: Optional[float] = None,
                  planned_risk: Optional[float] = None,
                  session_date: Optional[str] = None) -> str:
    """Persist ONE decision-engine result with its full evidence. Returns signal_id."""
    now = db.utcnow()
    session_date = session_date or dt.date.today().isoformat()
    symbol = result.get("symbol", "?")
    sid = fills.signal_id(symbol, strategy, now)

    dq = result.get("data_quality") or {}
    fresh = result.get("freshness") or {}
    ev = result.get("ev_breakdown") or {}
    entry_range = result.get("entry_range") or [None, None]
    entry = entry_range[0] if isinstance(entry_range, (list, tuple)) else None

    db.execute("""INSERT OR REPLACE INTO signals(
        signal_id, created_at, session_date, symbol, strategy, action, sector, industry,
        market_regime, quality, quality_threshold, confidence, data_quality_json,
        freshness_state, freshness_json, provenance_json, gates_json, failed_gates,
        entry, stop, target, quantity, planned_risk, expected_value, expected_r,
        supporting_json, conflicting_json, scenarios_json, scanner_rank,
        config_version, engine_version, executed, order_id, outcome)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, now, session_date, symbol, strategy, result.get("decision", "?"),
         sector, industry, market_regime,
         result.get("confidence_quality"), result.get("quality_threshold"),
         result.get("overall_confidence"), _j(dq),
         fresh.get("state"), _j(fresh),
         _j({"data_source": result.get("data_source"), "data_state": result.get("data_state")}),
         _j(result.get("decision_gates")), _j(result.get("failed_gates")),
         entry, result.get("stop"), result.get("target"),
         quantity if quantity is not None else result.get("suggested_shares"),
         planned_risk, ev.get("ev_per_share"), ev.get("expected_r"),
         _j(result.get("supporting_signals")), _j(result.get("conflicting_signals")),
         _j(result.get("scenario_probabilities")), scanner_rank,
         db.config_version(), result.get("engine_version", "decision_engine/gates-v1"),
         0, None, "open"))
    db.audit("signal", sid, "recorded",
             {"symbol": symbol, "action": result.get("decision"), "strategy": strategy,
              "failed_gates": result.get("failed_gates")})
    return sid


def mark_executed(signal_id: str, order_id: str) -> None:
    db.execute("UPDATE signals SET executed=1, order_id=? WHERE signal_id=?",
               (order_id, signal_id))
    db.audit("signal", signal_id, "executed", {"order_id": order_id})


def update_excursions(signal_id: str, high: float, low: float) -> Dict[str, Any]:
    """Update MFE/MAE for a tracked signal from a new bar. Direction-aware: for a long,
    favourable is up. Applies to REJECT and MONITOR signals too — that is how we learn
    whether a blocked setup would have worked."""
    s = db.query_one("SELECT * FROM signals WHERE signal_id=?", (signal_id,))
    if not s or not s.get("entry"):
        return {"updated": False, "reason": "unknown signal or no entry reference"}
    entry = s["entry"]
    stop, target = s.get("stop"), s.get("target")
    is_long = (target is not None and stop is not None and target > stop) or \
              (target is not None and target > entry)
    fav = (high - entry) if is_long else (entry - low)
    adv = (entry - low) if is_long else (high - entry)
    mfe = max(s.get("mfe") or 0.0, round(fav, 4))
    mae = max(s.get("mae") or 0.0, round(adv, 4))

    outcome, pnl_ps, outcome_at = s.get("outcome") or "open", s.get("outcome_pnl_ps"), s.get("outcome_at")
    if outcome == "open" and stop is not None and target is not None:
        # Conservative: if a bar touches BOTH the stop and the target, assume the
        # STOP was hit first. Never flatter the record.
        hit_stop = (low <= stop) if is_long else (high >= stop)
        hit_target = (high >= target) if is_long else (low <= target)
        if hit_stop:
            outcome, pnl_ps, outcome_at = "stop_hit", round(
                (stop - entry) if is_long else (entry - stop), 4), db.utcnow()
        elif hit_target:
            outcome, pnl_ps, outcome_at = "target_hit", round(
                (target - entry) if is_long else (entry - target), 4), db.utcnow()

    db.execute("""UPDATE signals SET mfe=?, mae=?, outcome=?, outcome_pnl_ps=?,
                  outcome_at=?, tracked_until=? WHERE signal_id=?""",
               (mfe, mae, outcome, pnl_ps, outcome_at, db.utcnow(), signal_id))
    return {"updated": True, "mfe": mfe, "mae": mae, "outcome": outcome,
            "outcome_pnl_ps": pnl_ps}


def expire_stale_tracking(max_age_days: int = 20) -> int:
    """Stop tracking signals that never resolved, so 'open' doesn't grow forever."""
    cutoff = (dt.date.today() - dt.timedelta(days=max_age_days)).isoformat()
    rows = db.query("SELECT signal_id FROM signals WHERE outcome='open' AND session_date < ?",
                    (cutoff,))
    for r in rows:
        db.execute("UPDATE signals SET outcome='expired', outcome_at=? WHERE signal_id=?",
                   (db.utcnow(), r["signal_id"]))
    return len(rows)


# ── Queries ───────────────────────────────────────────────────────────────────

def signals(session_date: Optional[str] = None, action: Optional[str] = None,
            limit: int = 200) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM signals WHERE 1=1"
    params: list = []
    if session_date:
        sql += " AND session_date=?"; params.append(session_date)
    if action:
        sql += " AND action=?"; params.append(action)
    sql += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
    return db.query(sql, tuple(params))


def get(signal_id: str) -> Optional[Dict[str, Any]]:
    s = db.query_one("SELECT * FROM signals WHERE signal_id=?", (signal_id,))
    if s:
        for k in ("data_quality_json", "freshness_json", "provenance_json", "gates_json",
                  "failed_gates", "supporting_json", "conflicting_json", "scenarios_json"):
            if s.get(k):
                try:
                    s[k.replace("_json", "")] = json.loads(s[k])
                except Exception:
                    pass
    return s


def gate_outcomes() -> List[Dict[str, Any]]:
    """The question this whole journal exists to answer: for each action bucket, how
    did the setups actually perform? If REJECT setups win as often as TRADEABLE ones,
    the gates are filtering noise, not edge."""
    rows = db.query("""SELECT action,
                         COUNT(*) n,
                         SUM(CASE WHEN outcome='target_hit' THEN 1 ELSE 0 END) target_hit,
                         SUM(CASE WHEN outcome='stop_hit'   THEN 1 ELSE 0 END) stop_hit,
                         SUM(CASE WHEN outcome='open'       THEN 1 ELSE 0 END) still_open,
                         AVG(mfe) avg_mfe, AVG(mae) avg_mae,
                         AVG(outcome_pnl_ps) avg_pnl_ps
                       FROM signals GROUP BY action""")
    for r in rows:
        resolved = (r["target_hit"] or 0) + (r["stop_hit"] or 0)
        r["resolved"] = resolved
        r["win_rate"] = round((r["target_hit"] or 0) / resolved * 100, 1) if resolved else None
        for k in ("avg_mfe", "avg_mae", "avg_pnl_ps"):
            r[k] = round(r[k], 4) if r[k] is not None else None
    return rows


def blocked_winners() -> Dict[str, Any]:
    """Explicitly measure the cost of the gates: REJECT/MONITOR signals whose
    hypothetical target was reached. A high number here is evidence the gates are too
    tight and is the ONLY justification for loosening them."""
    rows = db.query("""SELECT signal_id, symbol, strategy, action, failed_gates,
                              quality, quality_threshold, outcome, mfe, mae, outcome_pnl_ps
                       FROM signals WHERE action IN ('REJECT','MONITOR')
                         AND outcome='target_hit' ORDER BY outcome_pnl_ps DESC""")
    tot = db.query_one("SELECT COUNT(*) n FROM signals WHERE action IN ('REJECT','MONITOR') "
                       "AND outcome IN ('target_hit','stop_hit')")["n"]
    return {"blocked_winners": rows, "count": len(rows),
            "resolved_blocked": tot,
            "blocked_win_rate": round(len(rows) / tot * 100, 1) if tot else None}
