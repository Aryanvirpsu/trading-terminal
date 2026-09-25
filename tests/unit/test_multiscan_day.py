"""Compressed market-day simulation on the REAL workflow + ledger + shadow log (providers mocked).

Covers scenarios A-E, fresh quote on every cycle, stale-quote refusal, restart recovery, event clustering,
funnel counts, and CH-001 forward shadow. Nothing here can reach a broker."""
import datetime as dt
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

import decision_engine as de
from paper import db, report, risk, runtime as rt, shadow_log, workflow
from paper.fills import Quote

DAY = "2026-09-25"
SECTOR = {"NVDA": "technology", "AMD": "industrials", "XOM": "energy", "ZZZ": "financials", "META": "communication"}


class World:
    """Mutable market/scanner state the tests drive between cycles."""

    def __init__(self):
        self.finalists = []          # list of symbols the scanner returns this cycle
        self.decision = {}           # symbol -> "MONITOR" | "TRADEABLE" | "REJECT"
        self.quality = {}
        self.stale = set()
        self.last = {}               # symbol -> last price override
        self.quote_calls = []


@pytest.fixture()
def world(monkeypatch, tmp_path):
    w = World()
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DECISION_ALLOW_STALE", "false")
    db.reset_for_tests(str(tmp_path))
    monkeypatch.setattr(risk, "_live_mark", lambda s: 40.0)
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
    monkeypatch.setattr(workflow, "provider_health", lambda: {"healthy": True})
    monkeypatch.setattr(de, "_safe_regime", lambda: {})

    def scan():
        fins = [{"symbol": s, "direction": "LONG", "sector": SECTOR[s], "strategy": "liquid_momentum",
                 "scanner_rank": i + 1} for i, s in enumerate(w.finalists)]
        return {"state": "ok", "finalists": fins, "candidate_count": len(fins) + 3,
                "universe_considered": 60, "bars_available": 58}

    def evaluate(sym, *a, **k):
        d = w.decision[sym]
        ok = d == "TRADEABLE"
        px = w.last.get(sym, 40.0)
        return {"symbol": sym, "decision": d, "direction": "LONG", "price": px,
                "entry_range": [px, px + 0.1], "stop": 39.6, "target": 42.0, "suggested_shares": 100,
                "decision_gates": [{"name": "quality", "passed": ok},
                                   {"name": "liquidity", "passed": True, "value": 0.9}],
                "failed_gates": [] if ok else ["conviction"], "freshness": {"state": "fresh"},
                "data_source": "yahoo", "data_state": "fresh",
                "ev_breakdown": {"ev_per_share": 0.5, "expected_r": 1.1},
                "confidence_quality": w.quality.get(sym, 70 if ok else 55)}

    def quote_for(sym):
        w.quote_calls.append(sym)
        age = 3600 if sym in w.stale else 0
        return Quote(sym, last=w.last.get(sym, 40), bid=39.95, ask=40.05,
                     source_ts=time.time() - age, provider="yahoo")

    monkeypatch.setattr(workflow.strategies, "scan", scan)
    monkeypatch.setattr(de, "evaluate", evaluate)
    monkeypatch.setattr(workflow, "quote_for", quote_for)
    yield w
    db.close()


def cycle(hhmm, allow=True, session_type="regular"):
    ts = f"{DAY}T{hhmm[:2]}:{hhmm[2:]}:00+00:00"
    return workflow.premarket(DAY, cycle_id=f"{DAY}T{hhmm}", allow_entries=allow, session_type=session_type, scan_ts=ts)


def n(sql, *p):
    return db.query_one(sql, p)["n"]


def shadow():
    c = sqlite3.connect(shadow_log.path())
    c.row_factory = sqlite3.Row
    return c


def test_full_compressed_day(world):
    # A. same candidate, unchanged market: two observations, ONE journal row, ONE event
    world.finalists, world.decision = ["NVDA"], {"NVDA": "MONITOR"}
    cycle("1335")
    cycle("1350")
    assert n("SELECT COUNT(*) n FROM signals WHERE symbol='NVDA'") == 1
    assert n("SELECT COUNT(*) n FROM orders") == 0

    # C-prep: empty scanner cycle (no AMD yet) records nothing and breaks nothing
    world.finalists = []
    r = cycle("1405")
    assert r["state"] == "ok" and r["orders_placed"] == []

    # stale quote is refused every time: fresh price required at decision time
    world.finalists, world.decision = ["ZZZ"], {"ZZZ": "TRADEABLE"}
    world.stale = {"ZZZ"}
    cycle("1420")
    assert n("SELECT COUNT(*) n FROM orders") == 0
    world.stale = set()
    world.decision["ZZZ"] = "REJECT"

    # B. state transition MONITOR -> TRADEABLE enters
    world.finalists, world.decision = ["NVDA"], {"NVDA": "TRADEABLE"}
    r = cycle("1435")
    assert [o["symbol"] for o in r["orders_placed"]] == ["NVDA"]
    rows = db.query("SELECT action, executed FROM signals WHERE symbol='NVDA' ORDER BY created_at")
    assert [(x["action"], x["executed"]) for x in rows] == [("MONITOR", 0), ("TRADEABLE", 1)]
    assert n("SELECT COUNT(*) n FROM positions WHERE status='open' AND symbol='NVDA'") == 1

    # D. existing position: another NVDA signal must not enter again
    r = cycle("1450")
    assert r["orders_placed"] == [] and n("SELECT COUNT(*) n FROM orders WHERE symbol='NVDA'") == 1
    assert "existing position" in r["evaluated"][0]["note"]

    # C. a brand-new candidate appearing later in the day is discovered and entered
    world.finalists, world.decision = ["NVDA", "AMD"], {"NVDA": "TRADEABLE", "AMD": "TRADEABLE"}
    r = cycle("1605")
    assert [o["symbol"] for o in r["orders_placed"]] == ["AMD"]
    assert risk.account_state(DAY)["entries_today"] == 2

    # E. capacity: the third entry is refused for the correct PERSISTED reason
    world.finalists, world.decision = ["XOM"], {"XOM": "TRADEABLE"}
    r = cycle("1620")
    assert r["orders_placed"] == []
    xom = db.query_one("SELECT signal_id FROM signals WHERE symbol='XOM'")
    reasons = [json.loads(a["detail_json"])["reason"] for a in db.audit_trail(xom["signal_id"]) if a["event"] == "not_executed"]
    assert any("daily entry cap reached" in x for x in reasons), reasons
    assert n("SELECT COUNT(*) n FROM orders WHERE intent='entry'") == 2

    # fresh quote on EVERY cycle for EVERY finalist (never carried forward)
    assert world.quote_calls.count("NVDA") >= 5

    # restart: a brand-new runtime must reconstruct the true account before any entry
    db.close()
    r2 = rt.Runtime(actions=object(), clock=lambda: dt.datetime(2026, 9, 25, 16, 30, tzinfo=dt.timezone.utc),
                    state_path=str(Path(db._DATA_DIR) / "restart.json"))
    info = r2.startup()
    assert info["restore_ok"] and info["account"]["open_positions"] == 2 and info["account"]["entries_today"] == 2
    assert info["account"]["sector_positions"] == {"technology": 1, "industrials": 1}
    world.finalists, world.decision = ["META"], {"META": "TRADEABLE"}
    r = cycle("1635")                                    # after restart: still refused (cap is persisted)
    assert r["orders_placed"] == [] and n("SELECT COUNT(*) n FROM orders WHERE intent='entry'") == 2

    # ── shadow evidence ────────────────────────────────────────────────────────────────────────────
    c = shadow()
    assert c.execute("SELECT COUNT(*) n FROM shadow_cycles").fetchone()["n"] == 8   # every cycle that had finalists
    nvda = c.execute("SELECT cand_id, event_id, decision FROM shadow_candidates WHERE symbol='NVDA' ORDER BY scan_ts").fetchall()
    assert len(nvda) >= 5 and len({r["event_id"] for r in nvda}) == 1               # many observations, ONE event
    assert len({r["cand_id"] for r in nvda}) == len(nvda)                            # distinct observation ids
    f = json.loads(c.execute("SELECT funnel_json FROM shadow_cycles WHERE cycle_id=?", (f"{DAY}T1605",)).fetchone()[0])
    assert f["universe_considered"] == 60 and f["scanner_detections"] == 5 and f["finalists"] == 2
    assert f["TRADEABLE"] == 2 and f["entries"] == 1 and f["orders_attempted"] >= 1
    f2 = json.loads(c.execute("SELECT funnel_json FROM shadow_cycles WHERE cycle_id=?", (f"{DAY}T1620",)).fetchone()[0])
    assert f2["entry_blocked_pre_broker"] == 1 and f2["entries"] == 0 and f2["top_reasons"]
    # the daily report keeps the original refusal reason
    rep = report.daily(DAY)
    xoms = [o for o in rep["entry_outcomes"] if o["symbol"] == "XOM"]
    assert xoms and not xoms[0]["executed"] and any("daily entry cap" in x for x in xoms[0]["reasons"])


def test_observation_only_cycles_never_trade_or_journal(world):
    world.finalists, world.decision = ["NVDA"], {"NVDA": "TRADEABLE"}
    r = cycle("1315", allow=False, session_type="premarket_prep")
    assert r["orders_placed"] == [] and n("SELECT COUNT(*) n FROM orders") == 0
    assert n("SELECT COUNT(*) n FROM signals") == 0                                  # Champion did not act
    c = shadow()
    row = c.execute("SELECT session_type, decision, champion_executed FROM shadow_candidates").fetchone()
    assert (row["session_type"], row["decision"], row["champion_executed"]) == ("premarket_prep", "TRADEABLE", 0)


def test_rerunning_the_same_cycle_id_never_duplicates(world):
    world.finalists, world.decision = ["NVDA"], {"NVDA": "MONITOR"}
    cycle("1335")
    cycle("1335")
    c = shadow()
    assert c.execute("SELECT COUNT(*) n FROM shadow_cycles").fetchone()["n"] == 1
    assert c.execute("SELECT COUNT(*) n FROM shadow_candidates").fetchone()["n"] == 1
    assert n("SELECT COUNT(*) n FROM signals") == 1


def test_fail_closed_when_ledger_does_not_reconcile(world, monkeypatch):
    from paper import broker
    monkeypatch.setattr(broker, "reconcile", lambda: {"reconciled": False, "delta": 9.99})
    world.finalists, world.decision = ["NVDA"], {"NVDA": "TRADEABLE"}
    r = cycle("1335")
    assert r["orders_placed"] == [] and r["entries_allowed"] is False and n("SELECT COUNT(*) n FROM orders") == 0


# ── event identity rule ─────────────────────────────────────────────────────────────────────────────

def _row(sym="NVDA", last=40.0, stop=39.6, target=42.0):
    return {"symbol": sym, "sector": "technology", "strategy": "s", "direction": "LONG", "scanner_rank": 1,
            "decision": "MONITOR", "failed_gates": "[]", "quality": 55.0, "expected_r": 1.0, "ev_per_share": 0.5,
            "rr": 2.0, "exec_conf": 0.9, "composite": 1.0, "entry": last, "stop": stop, "target": target,
            "quote_bid": None, "quote_ask": None, "quote_last": last, "quote_source_ts": time.time(),
            "quote_provider": "yahoo", "spread_pct": None, "eligible": 0,
            "features_json": json.dumps({"instrument": "STOCK"})}


CAP = {"max_open": 3, "max_entries_per_day": 2, "max_per_sector": 1, "open_positions": 0, "entries_today": 0,
       "sector_positions": {}, "open_symbols": []}


def _rec(day, hhmm, **kw):
    shadow_log.record_cycle(day, [_row(**kw)], [], CAP, cycle_id=f"{day}T{hhmm}",
                            scan_ts=f"{day}T{hhmm[:2]}:{hhmm[2:]}:00+00:00")


def _events():
    c = shadow()
    return [r["event_id"] for r in c.execute("SELECT event_id FROM shadow_candidates ORDER BY scan_ts")]


def test_event_rule_new_event_on_stop_cross_gap_and_closed_trade(world):
    _rec("2026-09-21", "1335")
    _rec("2026-09-21", "1350")
    _rec("2026-09-23", "1335")                          # 2 days later, price still inside (stop, target): same event
    ev = _events()
    assert len(set(ev)) == 1
    _rec("2026-09-23", "1350", last=39.0)               # price below the event's stop: setup over -> NEW event
    ev = _events()
    assert len(set(ev)) == 2 and ev[-1] != ev[0]
    _rec("2026-09-30", "1335")                          # >5 calendar-day gap: NEW event
    assert len(set(_events())) == 3


# ── CH-001 forward shadow ───────────────────────────────────────────────────────────────────────────

def _pos(pid, sym, status, realized, exit_reason, opened, closed):
    db.execute("""INSERT INTO positions(position_id, signal_id, symbol, strategy, sector, opened_at, closed_at, quantity,
                  avg_entry, avg_exit, stop, target, status, realized_pnl, fees, exit_reason, planned_risk, mfe, mae)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (pid, None, sym, "s", "x", opened, closed, 10.0, 40.0, 39.6 if status == "closed" else None,
                39.6, 42.0, status, realized, 0.0, exit_reason, 4.0, 0.0, 0.0))


def _bars(sym, rows):
    c = shadow_log._connect()
    with c:
        for ts, o, h, l, cl in rows:
            c.execute("INSERT OR IGNORE INTO shadow_bars_5m VALUES(?,?,?,?,?,?,?,?)", (sym, ts, o, h, l, cl, 1, "t"))
    c.close()


def test_ch001_forward_reversal_saved_by_breakeven(world):
    _pos("p1", "AAA", "closed", -4.0, "exit_stop", "2026-09-25T14:00:00+00:00", "2026-09-25T18:00:00+00:00")
    _bars("AAA", [("2026-09-25T14:05:00+00:00", 40, 40.2, 39.9, 40.1),      # below +1R (40.4)
                  ("2026-09-25T14:10:00+00:00", 40.1, 40.5, 40.0, 40.4),    # +1R reached (high>=40.4), stop not touched
                  ("2026-09-25T14:15:00+00:00", 40.4, 40.5, 39.9, 40.0),    # next bar trades back through entry -> BE exit
                  ("2026-09-25T17:55:00+00:00", 40.0, 40.0, 39.5, 39.6)])   # original stop later
    out = shadow_log.evaluate_ch001(now=dt.datetime(2026, 9, 25, 19, tzinfo=dt.timezone.utc))
    assert out["results"] == 1 and out["obs"] == 1
    r = shadow()
    row = r.execute("SELECT * FROM shadow_ch001_results WHERE position_id='p1'").fetchone()
    assert row["plus1r_occurred"] == 1 and row["plus1r_ts"].startswith("2026-09-25T14:10")
    assert row["be_exit_ts"].startswith("2026-09-25T14:15") and row["ambiguous"] == 0
    assert row["original_R"] == pytest.approx(-1.0) and row["delta_R"] > 0.9       # ~ -0.03 vs -1.0
    assert shadow_log.evaluate_ch001()["results"] == 0                               # idempotent, append-only


def test_ch001_forward_ambiguous_bar_is_never_resolved_favourably(world):
    _pos("p2", "BBB", "closed", -4.0, "exit_stop", "2026-09-25T14:00:00+00:00", "2026-09-25T18:00:00+00:00")
    _bars("BBB", [("2026-09-25T14:05:00+00:00", 40, 40.5, 39.5, 40.0)])              # trigger AND stop in one bar
    shadow_log.evaluate_ch001(now=dt.datetime(2026, 9, 25, 19, tzinfo=dt.timezone.utc))
    row = shadow().execute("SELECT * FROM shadow_ch001_results WHERE position_id='p2'").fetchone()
    assert row["ambiguous"] == 1 and row["plus1r_occurred"] == 0 and row["delta_R"] == 0.0
    assert "one bar" in row["ambiguity_note"]


def test_ch001_open_position_records_plus1r_observation_only(world):
    _pos("p3", "CCC", "open", None, None, "2026-09-25T14:00:00+00:00", None)
    _bars("CCC", [("2026-09-25T14:05:00+00:00", 40, 40.6, 40.0, 40.5)])
    out = shadow_log.evaluate_ch001(now=dt.datetime(2026, 9, 25, 15, tzinfo=dt.timezone.utc))
    assert out["obs"] == 1 and out["results"] == 0
    assert shadow().execute("SELECT plus1r_ts FROM shadow_ch001_obs WHERE position_id='p3'").fetchone()


def test_shadow_failure_never_blocks_champion(world, monkeypatch):
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("shadow down"))
    monkeypatch.setattr(shadow_log, "record_cycle", boom)
    monkeypatch.setattr(shadow_log, "capacity_snapshot", lambda d: {"max_open": 3, "max_entries_per_day": 2,
                        "max_per_sector": 1, "open_positions": 0, "entries_today": 0, "sector_positions": {}})
    world.finalists, world.decision = ["NVDA"], {"NVDA": "TRADEABLE"}
    r = cycle("1335")
    assert [o["symbol"] for o in r["orders_placed"]] == ["NVDA"]


def test_runtime_end_to_end_with_real_actions(world):
    """Runtime -> PaperActions -> real workflow/ledger/shadow log through a fake clock."""
    from paper import market_calendar as cal
    world.finalists, world.decision = ["NVDA"], {"NVDA": "TRADEABLE"}
    et = lambda h, m, s=0: dt.datetime(2026, 9, 25, h, m, s, tzinfo=cal.ET).astimezone(dt.timezone.utc)
    now = [et(9, 35, 30)]
    r = rt.Runtime(clock=lambda: now[0], state_path=str(Path(db._DATA_DIR) / "rs.json"))
    r.startup()
    ev = r.tick()
    assert any(e.startswith("discovery:2026-09-25T0935") for e in ev) and "tracker:all" in ev
    assert n("SELECT COUNT(*) n FROM positions WHERE status='open'") == 1            # paper entry at 09:35 slot
    ld = r.state["last_discovery"]
    assert ld["finalists"] == 1 and ld["entries"] == 1 and ld["account"]["open_positions"] == 1
    now[0] = et(9, 36, 40)
    ev = r.tick()
    assert "tracker:positions" in ev and r.state["last_tracker"]["scope"] == "positions"
    assert r.state["last_tracker"]["quote_age_s_max"] is not None                     # quote freshness is tracked
    # 15:20 ET (after the entry cutoff): observation only, no new entry even for a fresh TRADEABLE
    world.finalists, world.decision = ["NVDA", "XOM"], {"NVDA": "TRADEABLE", "XOM": "TRADEABLE"}
    now[0] = et(15, 20, 10)
    r.tick()
    assert n("SELECT COUNT(*) n FROM orders WHERE intent='entry'") == 1
    assert r.state["last_discovery"]["entries_allowed"] is False


def test_manual_smoke_cycles_never_seed_or_join_real_events(world):
    world.finalists, world.decision = ["NVDA"], {"NVDA": "MONITOR"}
    workflow.premarket(DAY, cycle_id=f"{DAY}T2033manual", allow_entries=False, session_type="manual",
                       scan_ts=f"{DAY}T20:33:00+00:00")
    cycle("1335")                                        # first REAL observation of the same symbol
    c = shadow()
    ev = {r["cand_id"]: r["event_id"] for r in c.execute("SELECT cand_id, event_id FROM shadow_candidates")}
    manual = [v for k, v in ev.items() if "manual" in k][0]
    real = [v for k, v in ev.items() if "manual" not in k][0]
    assert manual != real and manual.startswith("evtm_") and real.startswith("evt_")


def test_acceptance_extraction_reports_a_session(world, capsys):
    import importlib.util
    world.finalists, world.decision = ["NVDA"], {"NVDA": "MONITOR"}
    cycle("1335")
    cycle("1350")
    world.decision["NVDA"] = "TRADEABLE"
    cycle("1405")
    spec = importlib.util.spec_from_file_location("avdi_acc", ROOT / "automation" / "avdi_acceptance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([DAY]) == 0
    out = json.loads(capsys.readouterr().out)
    ec = out["evidence_counts"]
    assert ec["raw_observations"] == 3 and ec["unique_events"] == 1 and ec["by_decision"]["TRADEABLE"] == 1
    assert out["state_transitions"]["NVDA"] == ["13:35:MONITOR", "13:50:MONITOR", "14:05:TRADEABLE"]
    assert len(out["shadow_cycles"]) == 3 and out["shadow_cycles"][0]["funnel"]["finalists"] == 1
    assert len(out["ledger"]["orders_today"]) == 1 and out["ledger"]["duplicate_entries"] == []
    assert out["schedule"]["expected_discovery_cycles"] == 26 and out["schedule"]["expected_entry_cycles"] == 22
    assert out["provider_freshness"]["max_quote_age_s_at_scan"] is not None


def test_runtime_records_cycle_timing(world):
    r = rt.Runtime(clock=lambda: dt.datetime(2026, 9, 25, 13, 35, 30, tzinfo=dt.timezone.utc),
                   state_path=str(Path(db._DATA_DIR) / "rs2.json"))
    world.finalists, world.decision = [], {}
    r.tick()
    cl = r.state["cycle_log"]
    assert cl and cl[0]["cycle_id"] == "2026-09-25T0935" and cl[0]["late_s"] == 30.0 and cl[0]["state"] == "ok"
