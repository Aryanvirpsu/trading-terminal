"""AVDI daily profit-accountability report: answers the five questions every session, from the verified cloud mirror, read-only.

    python scripts/avdi_daily_report.py [--mirror <dir>] [--session YYYY-MM-DD] [--event-risk] [--out report.md]

1 How much money did AVDI make or lose?        (scripts/avdi_scorecard.py figures; realized and unrealized separate)
2 Which strategies and decisions caused it?    (closed trades by strategy / exit; that session's exits and entries)
3 Which identified opportunities were not executed, and why?   COUNTS of TRADEABLE events by the recorded refusal reason.  Hypothetical gains are
  NOT computed or implied: no outcome column is read, and a refused candidate's later price is never treated as realizable profit.
4 What is preventing AVDI from capturing validated opportunities?   (entries-disabled events, capacity, open-slot occupancy)
5 What is the highest-value change next?       (rule-based pointer from the measured blockers; a human decides)

--event-risk additionally runs the installed `trader-news` skill (advisory only, never a gate) for the open positions and flags earnings/deal/halt/
regulatory headlines and SEC 8-Ks.  Nothing here writes to a ledger, places an order or changes a setting.
"""
import argparse, datetime as dt, json, os, re, sqlite3, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def ro(p):
    c = sqlite3.connect(f"file:{Path(p).as_posix()}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    return c


def event_risk(symbols):
    news = Path.home() / ".claude" / "skills" / "trader-news" / "news.py"
    if not symbols or not news.exists():
        return None
    env = dict(os.environ, SEC_USER_AGENT=os.environ.get("SEC_USER_AGENT", "AVDI research dhasvanth2612@gmail.com"))
    with tempfile.TemporaryDirectory(prefix="avdi_ev_") as d:                       # the skill writes a trader/ folder: keep it out of the repo
        p = subprocess.run([sys.executable, "-B", str(news), "--tickers", ",".join(symbols), "--hours", "48"], cwd=d, env=env, capture_output=True, text=True, timeout=120)
    ana = Path.home() / ".claude" / "skills" / "trader-analyst" / "facts.py"
    earn = {}
    for sym in symbols:
        if ana.exists():
            with tempfile.TemporaryDirectory(prefix="avdi_an_") as d2:
                q = subprocess.run([sys.executable, "-B", str(ana), sym], cwd=d2, env=env, capture_output=True, text=True, timeout=90)
            m2 = re.search(r"Latest earnings 8-K \(item 2\.02\): filed (\d{4}-\d\d-\d\d)", q.stdout)
            if m2:
                earn[sym] = (m2.group(1), (dt.date.today() - dt.date.fromisoformat(m2.group(1))).days)
    flagged, last = [], None
    for line in p.stdout.splitlines():
        m = re.match(r"\s*(\d{4}-\d\d-\d\d \d\d:\d\d ET)\s+(\S+)\s+(.*)", line)
        if m:
            last = (m.group(1), m.group(2), m.group(3)[:110])
        elif line.strip().startswith("tags:") and last:
            tags = line.split("tags:")[1].split("http")[0].strip()
            if any(t in tags for t in ("earnings", "deal", "halt", "regulatory", "bankruptcy", "restatement", "people")):
                flagged.append((*last, tags))
    return {"ok": p.returncode == 0, "flagged": flagged[:8], "raw_lines": len(p.stdout.splitlines()), "earnings": earn}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mirror"); ap.add_argument("--session"); ap.add_argument("--event-risk", action="store_true"); ap.add_argument("--out")
    a = ap.parse_args()
    mir = Path(a.mirror).expanduser() if a.mirror else Path(json.loads((Path.home() / ".avdi_cloud_mirror" / "current.json").read_text())["path"])
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import avdi_scorecard as SC
    tmp = Path(tempfile.mkdtemp(prefix="avdi_sc_")) / "sc.json"
    sys.argv = ["x", "--ledger", str(mir), "--out", str(tmp), "--fetch-spy"]
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        SC.main()
    sc = json.loads(tmp.read_text())
    L, S = ro(mir / "robinhood_500_baseline.db"), ro(mir / "shadow__shadow_candidates.db")
    runtime = json.loads((mir / "MANIFEST.json").read_text()).get("runtime_version", "?")[:7]
    day = a.session or sc["evidence_through_session"]
    m, r = sc["money"], sc["risk_and_speed"]
    daily = next((d for d in sc["daily"] if d["session"] == day), None)
    out = [f"# AVDI session report — {day}  (evidence: backup {sc['backup']} taken {sc['backup_created_utc'][:16]}Z; generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%MZ')})", ""]

    out += ["## 1. Money",
            f"* Account: equity **${m['equity']:.2f}** on ${m['start_equity']:.0f} = **${m['net_total_pnl']:+.2f}** since inception ({m['return_pct']:+.2f}%). Realized **${m['realized_pnl']:+.2f}**, unrealized **${m['unrealized_pnl_snapshot']:+.2f}** (marked at the backup's last bar, not live).",
            f"* This session: " + (f"equity change **${daily['day_pnl']:+.2f}**; exits today: {daily['exits_today'] or 'none'}." if daily else "no equity snapshot for this date."),
            f"* Max drawdown ${r['max_drawdown_usd']:.2f} ({r['max_drawdown_pct']:.2f}%); capital deployed on average ${r['avg_capital_deployed_usd']} ({r['avg_capital_deployed_pct']}%); modeled costs: entries {sc['costs_modeled']['entries']['avg_slippage_bps']} bps, exits {sc['costs_modeled']['exits']['avg_slippage_bps']} bps, fees $0.",
            ("* SPY same window: %+.2f%% (strategy %+.2f%%; not exposure-matched)." % (sc["benchmark"]["spy_return_pct"], sc["benchmark"]["strategy_return_pct"])) if "spy_return_pct" in sc.get("benchmark", {}) else "* Benchmark unavailable.", ""]

    out += ["## 2. What caused it"]
    for ev, b in sc["by_engine_version"].items():
        out.append(f"* {ev}: {b['closed_trades']} closed, {b['wins']} wins, net **${b['net_realized_pnl']:+.2f}**, profit factor {b['profit_factor']}, expectancy ${b['expectancy_usd_per_trade']:+.2f}/trade ({b['expectancy_R_per_trade']} R)")
    out.append("* By strategy: " + "; ".join(f"{k} {v['trades']} trades ${v['net_pnl']:+.2f}" for k, v in sc["attribution_by_strategy"].items()))
    out.append("* By exit: " + "; ".join(f"{k} {v['trades']} trades ${v['net_pnl']:+.2f}" for k, v in sc["attribution_by_exit_reason"].items()) + f". Stops: {sum(1 for c in sc['closed_trades'] if c['exit_reason']=='exit_stop')} of {len(sc['closed_trades'])} closed trades.")
    ent = [dict(x) for x in L.execute("SELECT o.created_at, o.symbol, o.intent, f.price FROM orders o JOIN fills f ON f.order_id=o.order_id WHERE o.session_date=? ORDER BY o.created_at", (day,))]
    out.append(f"* Orders filled this session: {len(ent)}" + (" — " + ", ".join(f"{x['symbol']} {x['intent']}" for x in ent[:6]) if ent else "") + "")
    out.append("")

    out += ["## 3. Identified but not executed (counts only; no hypothetical gain is assumed)"]
    rows = [dict(x) for x in S.execute("""SELECT COALESCE(capacity_reason,'-') cap, COALESCE(substr(champion_reason,1,60),'-') why, COUNT(*) obs, COUNT(DISTINCT event_id) ev
        FROM shadow_candidates WHERE decision='TRADEABLE' AND session_type!='manual' AND session_date=? AND champion_executed=0 GROUP BY 1,2 ORDER BY ev DESC""", (day,))]
    tot_ev = S.execute("SELECT COUNT(DISTINCT event_id) FROM shadow_candidates WHERE decision='TRADEABLE' AND session_type!='manual' AND session_date=? AND champion_executed=0", (day,)).fetchone()[0]
    out.append(f"* TRADEABLE events not executed this session: **{tot_ev}**" + ("" if rows else " (none)"))
    for x in rows:
        out.append(f"  * {x['ev']} event(s), {x['obs']} observations: capacity `{x['cap']}`; {x['why']}")
    week = S.execute("SELECT COUNT(DISTINCT event_id) FROM shadow_candidates WHERE decision='TRADEABLE' AND session_type!='manual' AND champion_executed=0 AND session_date>=?", ((dt.date.fromisoformat(day) - dt.timedelta(days=7)).isoformat(),)).fetchone()[0]
    out.append(f"* Last 7 days: {week} TRADEABLE events not executed. Their later prices were not examined.")
    out.append("")

    out += ["## 4. What is blocking validated opportunities"]
    dis = r["entries_disabled_by_day"]
    out.append(f"* Entries disabled by the ledger check: {r['entries_disabled_events']} events ({dis or 'none'}). {'**Entries were disabled by the ledger check on this session.**' if dis.get(day) else 'Not triggered this session.'} (runtime that produced this backup: `{runtime}`)")
    opn = [dict(x) for x in L.execute("SELECT symbol, sector, opened_at FROM positions WHERE status='open'")]
    out.append(f"* Open slots in use: {len(opn)} of 3 ({', '.join(p['symbol'] + ' ' + p['sector'] for p in opn) or 'none'}); free cash after the $100 reserve is limited by the $125 position cap.")
    out.append(f"* Trade frequency {r['entries_per_session']} entries/session; at $5 planned risk a +2R winner earns about $10, so the account cannot reach large dollar targets by volume.")
    out.append("")

    out += ["## 5. Highest-value next change"]
    if dis.get(day) and runtime.startswith("b236d54"):
        out.append("* **Restore trading: deploy the reconcile fix (PR #11).** This backup was produced by the pre-fix runtime; every session without the fix is lost evidence.")
    elif dis.get(day) and runtime == "unknown":
        out.append("* Entries were disabled this session and the backup does not record its build (pre-deploy snapshots are taken by a fresh container). "
                   "If the reconcile fix was deployed after this session, **confirm the next session's report shows no `entries_disabled` events**; if it was not, deploy it.")
    elif dis.get(day):
        out.append(f"* Entries were disabled this session although runtime `{runtime}` is not the pre-fix build: investigate `reconcile` in the status output before anything else.")
    elif tot_ev and any(x["cap"] == "max_open" for x in rows):
        out.append("* Slots are the binding constraint (`max_open`): evaluate slot-efficiency only via a pre-registered challenger on the shadow log (HIST-005 style), never by loosening limits.")
    else:
        out.append("* Let entries accumulate under the unchanged limits; the scorecard needs 50 resolved trades before any edge claim. Candidate studies already show no edge to add (CH-003, CH-005 NOT SUPPORTED).")
    out.append("")

    if a.event_risk:
        er = event_risk([p["symbol"] for p in opn])
        out += ["## Advisory: event risk on open positions (trader-news; never a gate)"]
        if er is None:
            out.append("* trader-news unavailable.")
        else:
            for sym, (d0, ago) in er["earnings"].items():
                out.append(f"* {sym}: last earnings 8-K filed {d0} ({ago} days ago). Reports are roughly quarterly, so a new earnings gap is "
                           + ("**unlikely within the next ~6 weeks**." if ago < 45 else "**possibly due soon: check the date before holding through it**."))
            out.append(f"* Headline flags below are KEYWORD MATCHES on news titles, not event dates; treat them as prompts to read, not as signals: " + (f"{len(er['flagged'])} item(s)." if er["flagged"] else "none."))
            for ts, sym, title, tags in er["flagged"]:
                out.append(f"  * {ts} {sym} [{tags}] {title}")
        out.append("")
    text = "\n".join(out) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
