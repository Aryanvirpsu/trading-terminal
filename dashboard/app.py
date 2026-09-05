"""Local web dashboard for the $500 strategy account.

A small Flask app that renders the same data as the `strategy_status` MCP tool,
so you can SEE the account in a browser instead of asking in chat: live equity,
cash, open stock + option positions with mark-to-market P&L, the trade journal,
and (on demand) fresh screener candidates.

Reads the shared SQLite paper ledger via the installed package — no separate
data store. Run it with the Claude preview tooling (see .claude/launch.json) or
directly:  python dashboard/app.py   then open http://127.0.0.1:5057
"""
from __future__ import annotations

import os
import sys

# Make the package importable when run directly from the repo, even if it isn't
# pip-installed in this interpreter (falls back to the src/ layout).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flask import Flask, jsonify, render_template_string

from tradingview_mcp.core.services import strategy_service as strategy

app = Flask(__name__)

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>$500 Strategy Account</title>
<style>
  :root {
    --bg:#0b0e14; --panel:#141a24; --panel2:#1b2330; --line:#26303f;
    --txt:#e6edf3; --dim:#8b98a9; --green:#3fb950; --red:#f85149;
    --accent:#58a6ff; --amber:#d29922;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  header { padding:20px 28px; border-bottom:1px solid var(--line);
    display:flex; align-items:baseline; gap:16px; flex-wrap:wrap; }
  header h1 { font-size:18px; margin:0; font-weight:600; }
  header .tag { color:var(--dim); font-size:12px; }
  header .updated { margin-left:auto; color:var(--dim); font-size:12px; }
  .wrap { padding:24px 28px; max-width:1200px; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:14px; margin-bottom:26px; }
  .kpi { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:16px 18px; }
  .kpi .label { color:var(--dim); font-size:11px; text-transform:uppercase;
    letter-spacing:.05em; }
  .kpi .val { font-size:24px; font-weight:650; margin-top:6px; }
  .section { margin-bottom:30px; }
  .section h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
    color:var(--dim); margin:0 0 12px; }
  table { width:100%; border-collapse:collapse; background:var(--panel);
    border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:13px; }
  th,td { text-align:right; padding:10px 14px; border-bottom:1px solid var(--line); }
  th:first-child,td:first-child { text-align:left; }
  th { color:var(--dim); font-weight:600; font-size:11px; text-transform:uppercase;
    background:var(--panel2); }
  tr:last-child td { border-bottom:none; }
  .green { color:var(--green); } .red { color:var(--red); } .dim { color:var(--dim); }
  .pill { display:inline-block; padding:2px 8px; border-radius:20px; font-size:11px;
    background:var(--panel2); border:1px solid var(--line); }
  .empty { color:var(--dim); padding:18px; background:var(--panel);
    border:1px solid var(--line); border-radius:10px; font-size:13px; }
  button { background:var(--accent); color:#04101f; border:none; padding:9px 16px;
    border-radius:8px; font-weight:600; cursor:pointer; font-size:13px; }
  button:disabled { opacity:.5; cursor:default; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:16px; margin-bottom:12px; }
  .card .top { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
  .card .sym { font-size:16px; font-weight:650; }
  .card .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
    gap:10px 20px; font-size:13px; }
  .card .g .k { color:var(--dim); font-size:11px; }
  .opt { margin-top:10px; padding-top:10px; border-top:1px dashed var(--line);
    font-size:12px; color:var(--dim); }
  .thesis { color:var(--dim); font-size:12px; margin-top:6px; }
  .foot { color:var(--dim); font-size:11px; margin-top:30px; line-height:1.6; }
  .market { border:1px solid var(--line); border-radius:10px; padding:14px 18px; margin-bottom:22px;
    display:flex; gap:20px; align-items:center; flex-wrap:wrap; }
  .market .badge { font-size:15px; font-weight:700; padding:4px 12px; border-radius:8px; }
  .market .m-on { background:rgba(63,185,80,.15); color:var(--green); border:1px solid var(--green); }
  .market .m-off { background:rgba(248,81,73,.15); color:var(--red); border:1px solid var(--red); }
  .market .m-mid { background:rgba(210,153,34,.15); color:var(--amber); border:1px solid var(--amber); }
  .market .m-item { font-size:12px; color:var(--dim); }
  .market .m-item b { color:var(--txt); font-weight:600; }
  .market .m-warn { color:var(--amber); font-size:12px; }
  .tag-stock { background:rgba(88,166,255,.15); color:var(--accent); border:1px solid var(--accent); }
  .tag-option { background:rgba(210,153,34,.18); color:var(--amber); border:1px solid var(--amber); }
  .exec { font-family:ui-monospace,Menlo,monospace; font-size:11px; color:var(--dim);
    background:var(--panel2); padding:6px 8px; border-radius:6px; margin-top:8px; overflow-x:auto; white-space:nowrap; }
  #acctbar { display:flex; gap:8px; align-items:center; margin-bottom:18px; flex-wrap:wrap; }
  .acct-btn { background:var(--panel2); color:var(--dim); border:1px solid var(--line);
    padding:7px 16px; border-radius:8px; font-weight:600; font-size:13px; cursor:pointer; }
  .acct-btn.active { background:var(--accent); color:#04101f; border-color:var(--accent); }
  .badge-paper { background:rgba(88,166,255,.15); color:var(--accent); border:1px solid var(--accent);
    padding:2px 9px; border-radius:6px; font-size:11px; font-weight:700; }
  .badge-cash { background:rgba(210,153,34,.18); color:var(--amber); border:1px solid var(--amber);
    padding:2px 9px; border-radius:6px; font-size:11px; font-weight:700; }
  .acct-label { margin:0 0 10px; font-size:12px; color:var(--dim); }
  .sched-item { font-size:12px; color:var(--dim); } .sched-item b { color:var(--txt); font-weight:600; }
  .ready-yes { background:rgba(63,185,80,.15); color:var(--green); border:1px solid var(--green); }
  .ready-no { background:rgba(210,153,34,.15); color:var(--amber); border:1px solid var(--amber); }
  .countdown { font-family:ui-monospace,Menlo,monospace; font-weight:700; color:var(--txt); }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:22px; }
  @media (max-width:820px){ .grid2 { grid-template-columns:1fr; } }
  .meter { height:6px; background:var(--panel2); border-radius:4px; overflow:hidden; margin-top:4px; min-width:80px; }
  .meter > i { display:block; height:100%; border-radius:4px; }
  .dec { padding:2px 9px; border-radius:6px; font-size:11px; font-weight:700; }
  .dec-TRADEABLE { background:rgba(63,185,80,.15); color:var(--green); border:1px solid var(--green); }
  .dec-PAPER\\/MONITOR, .dec-MONITOR { background:rgba(210,153,34,.15); color:var(--amber); border:1px solid var(--amber); }
  .dec-REJECT { background:rgba(139,152,169,.12); color:var(--dim); border:1px solid var(--line); }
  .lean-bull { color:var(--green); } .lean-bear { color:var(--red); } .lean-mixed { color:var(--amber); }
  .hl { font-size:12px; color:var(--dim); margin-top:4px; line-height:1.5; }
  .stat-row { display:flex; gap:18px; flex-wrap:wrap; margin-bottom:10px; }
  .stat-row .s .n { font-size:20px; font-weight:650; } .stat-row .s .l { font-size:10px; color:var(--dim); text-transform:uppercase; letter-spacing:.05em; }
</style>
</head>
<body>
<header>
  <h1>$500 Strategy Account</h1>
  <span class="tag">Balanced risk · momentum / breakout · paper</span>
  <span class="updated" id="updated">loading…</span>
</header>
<div class="wrap">
  <div id="premarket" class="market"><span class="dim">Loading scheduler…</span></div>

  <div id="acctbar">
    <span class="tag">Account</span>
    <button class="acct-btn active" data-acct="paper" onclick="switchAccount('paper')">Paper</button>
    <button class="acct-btn" data-acct="cash" onclick="switchAccount('cash')">Cash</button>
    <button class="acct-btn" data-acct="combined" onclick="switchAccount('combined')">Combined</button>
    <span id="acctBadges"></span>
  </div>

  <div id="cashState"></div>
  <div id="market" class="market"><span class="dim">Reading the market…</span></div>

  <div class="section">
    <h2>AI Opportunity Rankings <span class="dim" style="font-weight:400;font-size:11px;">— decision engine · 9 evidence families · execution-aware</span></h2>
    <div id="engine"><div class="empty">Loading engine…</div></div>
  </div>

  <div class="grid2">
    <div class="section"><h2>Catalysts &amp; News Sentiment</h2><div id="catalysts"><div class="empty">Loading…</div></div></div>
    <div class="section"><h2>What's Working <span class="dim" style="font-weight:400;font-size:11px;">— learned from your trades</span></h2><div id="whatsworking"><div class="empty">Loading…</div></div></div>
  </div>

  <div class="grid2">
    <div class="section"><h2>Model Calibration</h2><div id="calib"><div class="empty">Loading…</div></div></div>
    <div class="section"><h2>Strategy Champions <span class="dim" style="font-weight:400;font-size:11px;">— walk-forward survivors</span></h2><div id="champs"><div class="empty">Loading…</div></div></div>
  </div>

  <div id="paperPanels">
  <div class="acct-label" id="paperLabel" style="display:none;"><span class="badge-paper">PAPER</span> simulated account</div>
  <div class="kpis" id="kpis"></div>

  <div class="section">
    <h2>Equity Curve</h2>
    <div id="equity" class="card"><span class="dim">No snapshots yet — run strategy_daily_run to start recording.</span></div>
  </div>

  <div class="section">
    <h2>Position Review — live vs stops &amp; targets
      <button id="mgBtn" onclick="loadManage()" style="margin-left:12px;">Refresh review</button>
    </h2>
    <div id="manage"><div class="empty">Loading position review…</div></div>
  </div>

  <div class="section">
    <h2>Stock Positions</h2>
    <div id="stocks"></div>
  </div>

  <div class="section">
    <h2>Option Positions</h2>
    <div id="options"></div>
  </div>

  <div class="section">
    <h2>Trade Journal</h2>
    <div id="journal"></div>
  </div>

  <div class="section">
    <h2>Options Setups <span class="dim" style="font-weight:400;font-size:12px;">— 🎯 options first, 📈 stock only as an alternative</span>
      <button id="findBtn" onclick="findTrades()" style="margin-left:12px;">Find options now</button>
    </h2>
    <div id="candidates"><div class="empty">Click “Find options now” to scan for option setups that pass the quality gate (~30-60s).</div></div>
  </div>

  <div class="section">
    <h2>Post-Trade Lessons</h2>
    <div id="lessons"><div class="empty">Loading lessons...</div></div>
  </div>
  
  <div class="section">
    <h2>System AI Logs (Failover & Tasks)</h2>
    <div id="ai_logs"><div class="empty">Loading logs...</div></div>
  </div>
  </div><!-- /paperPanels -->

  <div class="foot">
    Educational paper-trading simulation. Not financial advice. Prices via Yahoo Finance /
    TradingView; may be delayed. This dashboard reads the local ledger at
    ~/.tradingview_mcp_data/portfolio.db.
  </div>
</div>

<script>
const money = n => (n==null?'–':'$'+Number(n).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}));
const pct = n => (n==null?'–':(n>=0?'+':'')+Number(n).toFixed(2)+'%');
const cls = n => (n==null?'dim':(n>0?'green':(n<0?'red':'dim')));

async function loadState() {
  try {
    const r = await fetch('/api/state'); const s = await r.json();
    document.getElementById('updated').textContent = 'updated ' + new Date().toLocaleTimeString();

    const eqCls = cls(s.total_return_pct);
    document.getElementById('kpis').innerHTML = `
      <div class="kpi"><div class="label">Total Equity</div><div class="val">${money(s.total_equity)}</div></div>
      <div class="kpi"><div class="label">Total Return</div><div class="val ${eqCls}">${pct(s.total_return_pct)}</div></div>
      <div class="kpi"><div class="label">Cash</div><div class="val">${money(s.cash)}</div></div>
      <div class="kpi"><div class="label">Unrealized P&L</div><div class="val ${cls(s.total_unrealized_pnl)}">${money(s.total_unrealized_pnl)}</div></div>
      <div class="kpi"><div class="label">Realized P&L</div><div class="val ${cls(s.journal.total_realized_pnl)}">${money(s.journal.total_realized_pnl)}</div></div>
      <div class="kpi"><div class="label">Win Rate</div><div class="val">${s.journal.win_rate_pct==null?'–':s.journal.win_rate_pct+'%'}</div></div>`;

    renderEquity(s.equity_curve || []);

    // Stocks
    const sp = s.stock_positions || [];
    document.getElementById('stocks').innerHTML = sp.length ? `<table>
      <tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>Last</th><th>Mkt Value</th><th>Unreal P&L</th><th>%</th></tr>
      ${sp.map(p=>`<tr><td>${p.symbol}</td><td>${p.quantity}</td><td>${money(p.average_price)}</td>
        <td>${money(p.current_price)}</td><td>${money(p.market_value)}</td>
        <td class="${cls(p.unrealized_pnl)}">${money(p.unrealized_pnl)}</td>
        <td class="${cls(p.unrealized_pnl_pct)}">${pct(p.unrealized_pnl_pct)}</td></tr>`).join('')}
      </table>` : '<div class="empty">No open stock positions.</div>';

    // Options
    const op = s.option_positions || [];
    document.getElementById('options').innerHTML = op.length ? `<table>
      <tr><th>Contract</th><th>Qty</th><th>Avg Prem</th><th>Last</th><th>Mkt Value</th><th>Unreal P&L</th><th>%</th></tr>
      ${op.map(p=>`<tr><td>${p.underlying_symbol} ${p.expiry} ${p.strike} ${p.option_type}</td>
        <td>${p.quantity}</td><td>${money(p.average_premium)}</td><td>${money(p.current_premium)}</td>
        <td>${money(p.market_value)}</td><td class="${cls(p.unrealized_pnl)}">${money(p.unrealized_pnl)}</td>
        <td class="${cls(p.unrealized_pnl_pct)}">${pct(p.unrealized_pnl_pct)}</td></tr>`).join('')}
      </table>` : '<div class="empty">No open option positions.</div>';

    // Journal
    const je = s.journal.entries || [];
    document.getElementById('journal').innerHTML = je.length ? `<table>
      <tr><th>#</th><th>Symbol</th><th>Type</th><th>Setup</th><th>Entry</th><th>Stop</th><th>Targets</th><th>R:R</th><th>Status</th><th>P&L</th></tr>
      ${je.map(e=>`<tr><td>${e.id}</td><td>${e.symbol}</td><td>${e.instrument_type}</td>
        <td>${e.setup_type||'–'}</td><td>${money(e.entry)}</td><td>${money(e.stop)}</td>
        <td>${(e.targets||[]).map(money).join(' / ')}</td><td>${e.risk_reward||'–'}</td>
        <td><span class="pill">${e.status}</span></td>
        <td class="${cls(e.outcome_pnl)}">${e.status==='closed'?money(e.outcome_pnl):'–'}</td></tr>`).join('')}
      </table>` : '<div class="empty">No journaled trades yet. Log one with strategy_log_trade after you take a candidate.</div>';
  } catch(e) {
    document.getElementById('updated').textContent = 'error loading state';
  }
}

async function loadMarket() {
  try {
    const r = await fetch('/api/market'); const m = await r.json();
    const cls = m.regime && m.regime.startsWith('RISK-ON') ? 'm-on'
              : (m.regime && m.regime.startsWith('RISK-OFF') ? 'm-off' : 'm-mid');
    const vix = m.vix||{};
    document.getElementById('market').innerHTML = `
      <span class="badge ${cls}">${m.regime} · ${m.risk_appetite_score}/100</span>
      <span class="m-item">${m.trade_posture}</span>
      <span class="m-item">New longs: <b>${m.new_longs_ok?'OK':'STAND DOWN'}</b></span>
      <span class="m-item">Indices <b>${(m.avg_index_change_pct>=0?'+':'')+m.avg_index_change_pct}%</b></span>
      <span class="m-item">VIX <b>${vix.level}</b> (${(vix.change_pct>=0?'+':'')+vix.change_pct}%)</span>
      <span class="m-item">Breadth <b>${m.sector_breadth_pct}%</b> (${m.sectors_green}🟢/${m.sectors_red}🔴)</span>
      ${(m.warnings||[]).map(w=>`<span class="m-warn">⚠ ${w}</span>`).join('')}`;
  } catch(e) { document.getElementById('market').innerHTML='<span class="dim">Market read unavailable.</span>'; }
}

function renderEquity(curve) {
  const el = document.getElementById('equity');
  if (!curve || curve.length < 2) {
    el.innerHTML = '<span class="dim">Need at least 2 snapshots to draw a curve — run strategy_daily_run over a few sessions.</span>';
    return;
  }
  const vals = curve.map(p=>p.equity);
  const min = Math.min(...vals), max = Math.max(...vals);
  const W=760, H=90, pad=6, span=(max-min)||1;
  const pts = curve.map((p,i)=>{
    const x = pad + i*(W-2*pad)/(curve.length-1);
    const y = H-pad - (p.equity-min)/span*(H-2*pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const last = vals[vals.length-1], first = vals[0];
  const stroke = last>=first ? 'var(--green)' : 'var(--red)';
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none">
      <polyline points="${pts}" fill="none" stroke="${stroke}" stroke-width="2"/>
    </svg>
    <div class="dim" style="font-size:11px;margin-top:6px;">
      ${curve.length} snapshots · low ${money(min)} · high ${money(max)} · latest ${money(last)}</div>`;
}

const ACT_COLOR = {HOLD:'var(--dim)', SCALE_OUT_T1:'var(--amber)', TAKE_PROFIT_FINAL:'var(--green)', EXIT_STOP:'var(--red)', UNKNOWN:'var(--dim)',
  EXPIRED_ITM:'var(--green)', EXPIRED_WORTHLESS:'var(--red)', EXPIRED_UNPRICED:'var(--amber)'};
async function loadManage() {
  const btn = document.getElementById('mgBtn');
  btn.disabled = true; btn.textContent = 'Reviewing…';
  try {
    const r = await fetch('/api/manage'); const m = await r.json();
    const rv = m.reviews || [];
    document.getElementById('manage').innerHTML = rv.length ? `<table>
      <tr><th>#</th><th>Symbol</th><th>Type</th><th>Entry</th><th>Stop</th><th>Now</th><th>Unreal R</th><th>Action</th><th>Note</th></tr>
      ${rv.map(x=>`<tr><td>${x.journal_id}</td><td>${x.symbol}</td><td>${x.instrument}</td>
        <td>${money(x.entry)}</td><td>${money(x.stop)}</td><td>${money(x.current)}</td>
        <td class="${cls(x.unrealized_R)}">${x.unrealized_R==null?'–':x.unrealized_R+'R'}</td>
        <td><span class="pill" style="border-color:${ACT_COLOR[x.action]||'var(--line)'};color:${ACT_COLOR[x.action]||'var(--txt)'}">${x.action}</span></td>
        <td class="dim" style="text-align:left;">${x.note||''}</td></tr>`).join('')}
      </table>` : '<div class="empty">No open positions to manage.</div>';
  } catch(e) {
    document.getElementById('manage').innerHTML = '<div class="empty">Review failed — data source may be rate-limited. Try again shortly.</div>';
  }
  btn.disabled = false; btn.textContent = 'Refresh review';
}

async function findTrades() {
  const btn = document.getElementById('findBtn');
  btn.disabled = true; btn.textContent = 'Scanning…';
  document.getElementById('candidates').innerHTML = '<div class="empty">Scanning for OPTIONS setups… (~30-60s)</div>';
  try {
    // Options-first: minimize stocks — only names with an affordable option.
    const r = await fetch('/api/candidates?options_only=1'); const d = await r.json();
    const cs = d.candidates || [];
    let mc = '';
    if (d.market_context) {
      const k = d.market_context;
      mc = `<div class="empty" style="margin-bottom:12px;border-left:3px solid ${k.new_longs_ok?'var(--green)':'var(--amber)'}">
        Market: <b>${k.regime}</b> (${k.score}/100) — ${k.posture} ${k.new_longs_ok?'':'<b>New longs: stand down.</b>'}</div>`;
    }
    if (!cs.length) {
      document.getElementById('candidates').innerHTML = mc +
        `<div class="empty">No option setups that pass the quality gate right now — cash is a valid position.${d.stale?' (showing last cached set — scanner was rate-limited)':''}</div>`;
    } else {
      document.getElementById('candidates').innerHTML = mc + cs.map(c=>{
        const o = c.option_idea; const sz = c.sizing||{};
        // OPTION is the headline (stocks minimized); stock shown only as a secondary line.
        const optBlock = o ? `
          <div class="top"><span class="pill tag-option">🎯 OPTION</span>
            <span class="sym">${c.symbol} $${o.strike} ${o.option_type}</span>
            <span class="dim">${o.expiry} · ${o.moneyness||''} ${o.days_to_expiry?('· '+o.days_to_expiry+'DTE'):''}</span></div>
          <div class="grid">
            <div class="g"><div class="k">Premium</div>${money(o.premium)}</div>
            <div class="g"><div class="k">Contracts</div>${o.sizing.contracts}</div>
            <div class="g"><div class="k">Cost</div>${money(o.sizing.capital_committed)}</div>
            <div class="g"><div class="k">Max loss</div>${money(o.sizing.max_loss)}</div>
            <div class="g"><div class="k">Breakeven</div>${money(o.breakeven)}</div>
            <div class="g"><div class="k">Underlying</div>score ${c.context.stock_score} · ${c.context.trend_state}</div>
          </div>
          <div class="exec">${o.how_to_trade}</div>` : '';
        return `<div class="card">${optBlock}
          <div class="thesis"><span class="pill tag-stock">📈 STOCK alt</span> ${sz.shares} sh @ ${money(c.entry)} · stop ${money(c.stop)} · targets ${(c.targets||[]).map(money).join(' / ')} · ${c.risk_reward}</div>
        </div>`;
      }).join('');
    }
  } catch(e) {
    document.getElementById('candidates').innerHTML = '<div class="empty">Scan failed — the market data source may be rate-limited. Try again in a minute.</div>';
  }
  btn.disabled = false; btn.textContent = 'Find trades now';
}

async function loadLessons() {
  try {
    const r = await fetch('/api/lessons'); const d = await r.json();
    document.getElementById('lessons').innerHTML = d.length ? `<table>
      <tr><th>Symbol</th><th>Type</th><th>Lesson</th><th>Date</th></tr>
      ${d.map(x=>`<tr><td>${x.symbol}</td><td>${x.lesson_type}</td>
        <td style="text-align:left;white-space:pre-wrap;">${x.lesson_text}</td><td>${x.created_at}</td></tr>`).join('')}
      </table>` : '<div class="empty">No lessons learned yet.</div>';
  } catch(e) { document.getElementById('lessons').innerHTML='<span class="dim">Failed to load lessons.</span>'; }
}

async function loadAILogs() {
  try {
    const r = await fetch('/api/ai_logs'); const d = await r.json();
    document.getElementById('ai_logs').innerHTML = d.length ? `<table>
      <tr><th>Task ID</th><th>Status</th><th>Step</th><th>Model</th><th>Updated</th></tr>
      ${d.map(x=>`<tr><td>${x.task_id.substring(0,8)}...</td><td>${x.status}</td>
        <td>${x.current_step}</td><td>${x.model_used||'pending'}</td><td>${x.updated_at}</td></tr>`).join('')}
      </table>` : '<div class="empty">No AI tasks in queue.</div>';
  } catch(e) { document.getElementById('ai_logs').innerHTML='<span class="dim">Failed to load AI logs.</span>'; }
}

let ACCT = 'paper';
const fmtTime = iso => { if(!iso) return '—'; try { return new Date(iso).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); } catch(e){ return iso; } };
const fmtET = iso => { if(!iso) return '—'; try { return new Date(iso).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); } catch(e){ return iso; } };

function switchAccount(a){
  ACCT = a;
  document.querySelectorAll('.acct-btn').forEach(b=>b.classList.toggle('active', b.dataset.acct===a));
  loadAccounts();
}

async function loadAccounts(){
  try {
    const r = await fetch('/api/accounts?account='+ACCT); const d = await r.json();
    const paper = d.paper, cash = d.cash;
    let badges = '';
    if (paper) badges += `<span class="badge-paper">PAPER ${money(paper.portfolio_value)}</span> `;
    if (cash) badges += cash.connected ? `<span class="badge-cash">CASH ${money(cash.portfolio_value)}</span>`
                                        : `<span class="badge-cash">CASH · not connected</span>`;
    document.getElementById('acctBadges').innerHTML = badges;

    const showPaper = (ACCT==='paper' || ACCT==='combined');
    document.getElementById('paperPanels').style.display = showPaper ? '' : 'none';
    document.getElementById('paperLabel').style.display = (ACCT==='combined') ? '' : 'none';

    const cs = document.getElementById('cashState');
    if (cash) {
      if (!cash.connected) {
        cs.innerHTML = `<div class="section"><h2><span class="badge-cash">CASH</span> ${cash.label}</h2>
          <div class="empty" style="border-left:3px solid var(--amber)">
            <b>Configuration required — real-money account not connected.</b><br>${cash.reason||''}<br>
            <span class="dim">No real-money data is shown (never invented). Live trading is disabled.</span></div></div>`;
      } else {
        const bd = cash.account_breakdown || [];
        const sp = cash.stock_positions || [];
        const bdTable = bd.length ? `<table><tr><th>Account</th><th>Type</th><th>Cash</th><th>Value</th></tr>
          ${bd.map(a=>`<tr><td>${a.name}</td><td class="dim">${a.type||'–'}</td><td>${money(a.cash)}</td><td>${money(a.value)}</td></tr>`).join('')}</table>` : '';
        const posTable = sp.length ? `<h2 style="margin-top:18px;">Positions</h2><table>
          <tr><th>Symbol</th><th>Account</th><th>Qty</th><th>Avg</th><th>Last</th><th>Mkt Value</th><th>Unreal P&L</th></tr>
          ${sp.map(p=>`<tr><td>${p.symbol}</td><td class="dim">${p.account||''}</td><td>${p.quantity}</td>
            <td>${money(p.average_price)}</td><td>${money(p.current_price)}</td><td>${money(p.market_value)}</td>
            <td class="${cls(p.unrealized_pnl)}">${money(p.unrealized_pnl)}</td></tr>`).join('')}</table>`
          : '';
        const opos = cash.option_positions || [];
        const optTable = opos.length ? `<h2 style="margin-top:18px;">Option Positions</h2><table>
          <tr><th>Contract</th><th>Account</th><th>Qty</th><th>Avg Prem</th><th>Last</th><th>Mkt Value</th><th>Unreal P&L</th></tr>
          ${opos.map(o=>`<tr><td>${o.underlying} ${o.expiry} ${o.strike} ${o.option_type}</td><td class="dim">${o.account||''}</td>
            <td>${o.quantity}</td><td>${money(o.average_premium)}</td><td>${money(o.current_premium)}</td>
            <td>${money(o.market_value)}</td><td class="${cls(o.unrealized_pnl)}">${money(o.unrealized_pnl)}</td></tr>`).join('')}</table>` : '';
        const noneMsg = (!sp.length && !opos.length) ? '<div class="empty" style="margin-top:12px;">No positions synced — account holds only cash.</div>' : '';
        cs.innerHTML = `<div class="section"><h2><span class="badge-cash">CASH</span> ${cash.label}
            <span class="dim" style="font-weight:400;font-size:11px;">· ${cash.broker} · last sync ${fmtTime(cash.last_sync)}</span></h2>
          <div class="kpis">
            <div class="kpi"><div class="label">Portfolio</div><div class="val">${money(cash.portfolio_value)}</div></div>
            <div class="kpi"><div class="label">Cash</div><div class="val">${money(cash.cash)}</div></div>
            <div class="kpi"><div class="label">Buying Power</div><div class="val">${money(cash.buying_power)}</div></div>
            <div class="kpi"><div class="label">Accounts</div><div class="val">${cash.accounts}</div></div>
          </div>
          ${bdTable}${posTable}${optTable}${noneMsg}
          <div class="dim" style="font-size:11px;margin-top:10px;">🔒 ${cash.note||''}</div></div>`;
      }
    } else { cs.innerHTML=''; }
  } catch(e){ document.getElementById('acctBadges').innerHTML='<span class="dim">account load error</span>'; }
}

let _cdSecs = null, _cdBase = 0;
async function loadScheduler(){
  try {
    const r = await fetch('/api/scheduler'); const s = await r.json();
    _cdSecs = s.seconds_to_open; _cdBase = Date.now();
    const fr = s.data_freshness || {};
    const freshTxt = (fr.last_scan_both && fr.last_scan_both.age_seconds!=null)
      ? Math.round(fr.last_scan_both.age_seconds/60)+'m ago' : '—';
    const errs = (s.errors||[]).length ? `<span class="m-warn">⚠ ${s.errors.length} stage error(s): ${s.errors.join('; ').slice(0,120)}</span>` : '';
    const staleTxt = s.status_stale
      ? `<span class="m-warn">⚠ scheduler idle ${s.status_age_seconds!=null?Math.round(s.status_age_seconds/3600)+'h':''} — task not firing</span>` : '';
    document.getElementById('premarket').innerHTML = `
      <span class="badge ${s.premarket_ready?'ready-yes':'ready-no'}">Pre-market ${s.premarket_ready?'READY':'not ready'}</span>
      <span class="sched-item">US open in <b class="countdown" id="cd">—</b></span>
      <span class="sched-item">Run status <b>${s.current_status||'idle'}</b></span>
      <span class="sched-item">Last success <b>${fmtET(s.last_success_at)}</b> ET${s.last_run_ok===false?' <span class="red">(errors)</span>':''}</span>
      <span class="sched-item">Next run <b>${fmtET(s.next_run)}</b> ET</span>
      <span class="sched-item">Data <b>${freshTxt}</b></span>
      ${staleTxt}${errs}`;
  } catch(e){ document.getElementById('premarket').innerHTML='<span class="dim">Scheduler status unavailable.</span>'; }
}

function tickCountdown(){
  const el = document.getElementById('cd'); if(!el) return;
  if (_cdSecs==null){ el.textContent='closed/na'; return; }
  let rem = Math.max(0, _cdSecs - Math.floor((Date.now()-_cdBase)/1000));
  const h=Math.floor(rem/3600), m=Math.floor(rem%3600/60), sec=rem%60;
  el.textContent = `${h}h ${String(m).padStart(2,'0')}m ${String(sec).padStart(2,'0')}s`;
}

const qColor = q => q>=60?'var(--green)':(q>=45?'var(--amber)':'var(--dim)');
async function loadInsights(){
  try {
    const r = await fetch('/api/insights'); const d = await r.json();
    // Engine rankings
    const all = ((d.engine||{}).all)||[];
    all.sort((a,b)=>(b.quality||0)-(a.quality||0));
    document.getElementById('engine').innerHTML = all.length ? `<table>
      <tr><th>Symbol</th><th>Decision</th><th>Confidence</th><th>P(dir)</th><th>EV/share</th></tr>
      ${all.map(e=>`<tr><td><b>${e.symbol}</b></td>
        <td><span class="dec dec-${(e.decision||'').replace('/','\\\\/')}">${e.decision||'—'}</span></td>
        <td><div style="display:flex;align-items:center;gap:8px;justify-content:flex-end;"><span>${e.quality==null?'–':e.quality}</span>
          <div class="meter" style="width:90px;"><i style="width:${Math.min(100,e.quality||0)}%;background:${qColor(e.quality||0)}"></i></div></div></td>
        <td>${e.p_direction==null?'–':(e.p_direction*100).toFixed(0)+'%'}</td>
        <td class="${cls(e.ev_per_share)}">${e.ev_per_share==null?'–':money(e.ev_per_share)}</td></tr>`).join('')}
      </table><div class="dim" style="font-size:11px;margin-top:8px;">Rejects included — a "no-trade" is a valid, risk-controlled outcome.</div>` : '<div class="empty">No engine sweep yet — runs pre-market.</div>';

    // Catalysts & sentiment
    const cats = d.catalysts||[];
    document.getElementById('catalysts').innerHTML = cats.length ? cats.map(c=>`
      <div class="card" style="padding:12px 14px;margin-bottom:8px;">
        <div class="top" style="margin-bottom:4px;"><span class="sym" style="font-size:14px;">${c.symbol}</span>
          <span class="lean-${c.lean}">${c.lean}</span><span class="dim" style="margin-left:auto;">score ${c.catalyst_score} · ${c.headline_count} hl</span></div>
        ${(c.headlines||[]).slice(0,1).map(h=>`<div class="hl">"${(h.title||'').slice(0,90)}" <span class="dim">— ${h.source||''}</span></div>`).join('')}
      </div>`).join('') : '<div class="empty">No watchlist catalysts in the current feed.</div>';

    // What's working
    const j = d.journal||{}; const o = j.overall||{};
    document.getElementById('whatsworking').innerHTML = o.n ? `
      <div class="stat-row">
        <div class="s"><div class="n ${cls(o.expectancy_usd)}">${money(o.expectancy_usd)}</div><div class="l">Expectancy/trade</div></div>
        <div class="s"><div class="n">${o.win_rate_pct}%</div><div class="l">Win rate</div></div>
        <div class="s"><div class="n">${o.profit_factor}</div><div class="l">Profit factor</div></div>
        <div class="s"><div class="n ${cls(o.total_pnl)}">${money(o.total_pnl)}</div><div class="l">Total P&L</div></div>
      </div>
      ${(j.lessons_ranked||[]).slice(0,5).map(l=>`<div class="hl">• ${l}</div>`).join('')}` : '<div class="empty">Not enough closed trades yet.</div>';

    // Calibration
    const cal = d.calibration||{};
    document.getElementById('calib').innerHTML = `
      <div class="stat-row">
        <div class="s"><div class="n">${cal.matched_samples||0}/${cal.min_samples_for_trust||20}</div><div class="l">Samples</div></div>
        <div class="s"><div class="n">${cal.brier_score==null?'–':cal.brier_score}</div><div class="l">Brier score</div></div>
        <div class="s"><div class="n" style="color:${cal.sufficient?'var(--green)':'var(--amber)'}">${cal.sufficient?'Calibrated':'Learning'}</div><div class="l">Status</div></div>
      </div>
      <div class="hl">${cal.note||''}</div>`;

    // Champions
    const ch = d.champions||{}; const cs = ch.champions||[];
    document.getElementById('champs').innerHTML = `<div class="dim" style="font-size:11px;margin-bottom:8px;">Generation ${ch.generation||1} · ${ch.champions_count||0} survived · tested ${ch.tested||0}</div>` +
      (cs.length ? `<table><tr><th>Symbol</th><th>Strategy</th><th>Robustness</th><th>OOS %</th></tr>
      ${cs.map(c=>`<tr><td>${c.symbol}</td><td>${c.strategy}</td><td>${c.robustness}</td><td class="${cls(c.oos_return)}">${c.oos_return}%</td></tr>`).join('')}</table>`
      : '<div class="empty">None survived out-of-sample this generation — the guardrail refusing overfit noise (working as designed).</div>');
  } catch(e){ document.getElementById('engine').innerHTML='<div class="empty">Intelligence temporarily unavailable — retrying…</div>'; setTimeout(loadInsights, 3000); }
}

loadInsights();
loadState();
loadManage();
loadMarket();
loadLessons();
loadAILogs();
loadAccounts();
loadScheduler();
setInterval(loadInsights, 60000);
setInterval(loadState, 30000);
setInterval(loadMarket, 60000);
setInterval(loadLessons, 60000);
setInterval(loadAILogs, 15000);
setInterval(loadAccounts, 30000);
setInterval(loadScheduler, 30000);
setInterval(tickCountdown, 1000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    # New institutional terminal UI lives in terminal.html; fall back to the
    # legacy PAGE only if the file is missing.
    tpl = os.path.join(os.path.dirname(__file__), "terminal.html")
    if os.path.exists(tpl):
        with open(tpl, encoding="utf-8") as f:
            return f.read()
    return render_template_string(PAGE)


_ACCT_READY = {"done": False}


def _account_status():
    if not _ACCT_READY["done"]:
        strategy.setup_account(reset=False)
        _ACCT_READY["done"] = True
    return strategy.account_status()


@app.route("/api/state")
def api_state():
    # SWR-cached: serves the last portfolio snapshot instantly and refreshes in the
    # background, so polling never blocks on the per-position quote fetch.
    with _R.timed("api.state"):
        val, cs = _R.swr("acct:state", _R.TTL["portfolio"], _account_status)
    resp = jsonify(val)
    resp.headers["X-Cache"] = cs
    return resp


@app.route("/api/candidates")
def api_candidates():
    from flask import request
    options_only = request.args.get("options_only") in ("1", "true", "yes")
    preset = request.args.get("preset", "liquid")
    # Build the preset scan universe from the security master (local filter);
    # cached separately so it isn't rebuilt each poll.
    uni_info, _uc = _R.swr(f"scanuni:{preset}", 3600, lambda: _R.build_scan_universe(preset))
    tickers = uni_info.get("tickers") if isinstance(uni_info, dict) else None
    key = f"cand:{'opt' if options_only else 'all'}:{preset}"
    with _R.timed("api.candidates"):
        val, cs = _R.swr(key, 120, lambda: strategy.find_trades(
            max_candidates=6, include_options=True, options_only=options_only,
            universe=tickers, preset=preset))
    resp = jsonify(val)
    resp.headers["X-Cache"] = cs
    return resp


@app.route("/api/scan/presets")
def api_scan_presets():
    """List the available scanner presets + the size of each master-derived pool."""
    names = ["liquid", "momentum", "mean_reversion", "options_eligible", "etf",
             "small_cap_speculative"]
    # Report the size at the SCANNER'S configured limit. Reporting build_scan_universe's
    # 120 default made the picker say "liquid (120)" for a scan that actually walks 250.
    try:
        import scanner as _SC
        limit = _SC.config()["universe_limit"]
    except Exception:  # noqa: BLE001
        limit = 120
    out = []
    for p in names:
        try:
            u = _R.build_scan_universe(p, limit=limit)
            out.append({"preset": p, "size": u["size"], "speculative": u["speculative"],
                        "limit": limit, "note": u["note"]})
        except Exception as e:  # noqa: BLE001
            out.append({"preset": p, "error": str(e)[:60]})
    return jsonify({"presets": out})


@app.route("/api/market")
def api_market():
    # Shares the "regime" cache key with the research sentiment endpoint.
    with _R.timed("api.market"):
        val, cs = _R.swr("regime", _R.TTL["regime"], strategy.market_regime)
    resp = jsonify(val)
    resp.headers["X-Cache"] = cs
    return resp


@app.route("/api/manage")
def api_manage():
    # Recommendations only from the dashboard — never auto-executes trades.
    with _R.timed("api.manage"):
        val, cs = _R.swr("acct:manage", _R.TTL["manage"],
                         lambda: strategy.manage_positions(execute_stops=False))
    resp = jsonify(val)
    resp.headers["X-Cache"] = cs
    return resp

@app.route("/api/lessons")
def api_lessons():
    import sqlite3
    from tradingview_mcp.core.portfolio import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM post_trade_lessons ORDER BY created_at DESC LIMIT 50")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(rows)

@app.route("/api/ai_logs")
def api_ai_logs():
    import sqlite3
    from tradingview_mcp.core.portfolio import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT task_id, status, current_step, model_used, updated_at FROM ai_tasks_queue ORDER BY updated_at DESC LIMIT 20")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(rows)


import json as _json
import time as _time
from datetime import datetime as _dt, timedelta as _td
from zoneinfo import ZoneInfo as _ZI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "automation"))

# The ONE market calendar, imported once at module level. It was previously imported
# locally inside two route handlers, which made it easy to add a second, private clock
# elsewhere in this file without noticing the first one existed.
import market_regime as _MR      # noqa: E402

_DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
_ET = _ZI("America/New_York")


def _read_json(name):
    try:
        return _json.load(open(os.path.join(_DATA_DIR, name), encoding="utf-8"))
    except Exception:
        return {}


def _clean(o):
    import dataclasses
    import math
    # Canonical Option Architecture dataclasses (ContractQualityResult,
    # AccountFitResult, etc.) can now appear anywhere in a response tree
    # (dashboard/options_desk.py:decide(), Step 8) — generic, forward-
    # compatible handling here rather than converting each one at its call
    # site. `not isinstance(o, type)` excludes the dataclass CLASS itself
    # (dataclasses.is_dataclass is also true for the class, not just
    # instances).
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return _clean(dataclasses.asdict(o))
    if isinstance(o, float):
        return None if (math.isinf(o) or math.isnan(o)) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def _fresh(name):
    p = os.path.join(_DATA_DIR, name)
    if not os.path.exists(p):
        return None
    return {"age_seconds": round(_time.time() - os.path.getmtime(p)),
            "at": _dt.fromtimestamp(os.path.getmtime(p), _ET).isoformat()}


# Scan artifacts refresh on the daily/premarket cadence, so their natural age is
# HOURS, not seconds — a live-quote threshold set would call a normal 4h-old scan
# "critically stale". These scan-appropriate windows are handed to the SAME shared
# classifier so the vocabulary (fresh/ageing/stale/…) stays identical everywhere.
_SCAN_THRESHOLDS = {"fresh": 8 * 3600, "ageing": 24 * 3600, "stale": 72 * 3600}


def _data_state(*names):
    """Authoritative batch-SCAN freshness (via the ONE shared classifier), based on
    the freshest actual data artifact's age — NOT the scheduler task's own idle age.
    This is what the header 'Data' badge must reflect, so it can never contradict the
    decision engine / panels (the 'header stale 18h while engine fresh' bug). The
    per-symbol engine/panel freshness is separate and LIVE (seconds old).

    Market-aware: overnight and weekend artifacts are EXPECTED to be hours old. Labelling
    Friday's scan "stale (28h)" all weekend describes a working system as a broken one
    and trains the eye to ignore the badge that matters intraday.
    """
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab"))
        import freshness as _fr
    except Exception:
        return None
    try:
        market_state = _MR.session_state()["state"]
    except Exception:
        market_state = None
    ages = [f["age_seconds"] for f in (_fresh(n) for n in names) if f]
    st = _fr.classify_typed("scanner", min(ages) if ages else None,
                            market_state=market_state)          # freshest wins
    st["scope"] = "batch-scan"                                   # NOT the live engine read
    return st


def _paper_account():
    strategy.setup_account(reset=False)
    s = strategy.account_status()
    ps = {}
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab"))
        import risk_engine
        ps = risk_engine.portfolio_state()
    except Exception:
        pass
    fills = [e for e in s["journal"]["entries"] if e.get("status") == "closed"][:8]
    return {
        "connected": True, "label": "Paper", "broker": "simulated ledger", "status": "active",
        "cash": s["cash"], "buying_power": s["cash"], "portfolio_value": s["total_equity"],
        "day_pnl": ps.get("day_pnl"),
        "total_pnl": round(s["total_equity"] - s.get("initial_balance", 500), 2),
        "total_return_pct": s["total_return_pct"], "unrealized_pnl": s["total_unrealized_pnl"],
        "realized_pnl": s["journal"]["total_realized_pnl"],
        "stock_positions": s["stock_positions"], "option_positions": s["option_positions"],
        "pending_orders": [], "recent_fills": fills,
        "drawdown_pct": ps.get("drawdown_pct"), "sector_exposure": ps.get("sector_exposure"),
        "cash_pct": ps.get("cash_pct"), "equity_curve": s["equity_curve"], "journal": s["journal"],
    }


def _cash_account():
    # Read-only Robinhood via SnapTrade (aggregator; never trades).
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab"))
        import snaptrade_data
        if snaptrade_data.configured():
            return snaptrade_data.cash_account()
    except Exception as e:
        return {"connected": False, "label": "Cash (Robinhood)", "status": "error", "reason": str(e)[:140]}
    return {"connected": False, "label": "Cash (Robinhood)", "status": "configuration_required",
            "reason": "SnapTrade not configured. Real-money account read-only until linked."}


def _robinhood_accounts():
    # Official Robinhood MCP (read-only) — cash + agentic kept SEPARATE. Never
    # blocks the page: any failure returns a clean structured state.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab"))
        import robinhood_view
        return robinhood_view.accounts()
    except Exception as e:  # noqa: BLE001
        return {"status": {"connected": False, "status": "error", "reason": str(e)[:140]},
                "cash": {"connected": False}, "agentic": {"connected": False}}


@app.route("/api/accounts")
def api_accounts():
    from flask import request
    which = request.args.get("account", "paper")
    out = {"account": which}
    # Each account source is SEPARATE — balances are never combined.
    if which in ("paper", "combined"):
        out["paper"] = _paper_account()
    if which in ("cash", "combined"):
        out["cash"] = _cash_account()
    if which in ("robinhood", "combined"):
        with _R.timed("api.robinhood"):
            out["robinhood"] = _robinhood_accounts()
    return jsonify(_clean(out))


@app.route("/api/symbol/rh_position")
def api_rh_position():
    """Robinhood holding of the selected ticker (read-only) for the stock page.
    Independent + non-blocking: a Robinhood outage returns a state, not an error."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab"))
        import robinhood_view
        return jsonify(_clean(robinhood_view.position_for(_sym_arg())))
    except Exception as e:  # noqa: BLE001
        return jsonify({"symbol": _sym_arg(), "connected": False, "status": "error",
                        "reason": str(e)[:120], "holdings": [], "held": False})


def _market_clock():
    """Market state from the ONE authoritative implementation.

    This used to be a second, private `_seconds_to_open()` that only ever answered
    "when is the next 9:30 in the future?". During a live session today's open is in
    the past, so it walked to the NEXT trading day and the header read
    "opens 67h 50m" at 14:07 ET on a Friday with the market open. It had no concept of
    an open session, and duplicated — badly — the calendar in market_regime, which
    already handles weekends, holidays, early closes and DST.
    """
    s = _MR.session_state()
    nxt = s.get("next_session") or {}
    return {
        "state": s["state"],                    # open|premarket|afterhours|closed|holiday
        "reason": s["reason"],
        "is_open": s["state"] == "open",
        "is_trading_day": s["is_trading_day"],
        "holiday": s.get("holiday"),
        "early_close": s.get("early_close"),
        "now_et": s["now_et"],
        "weekday_et": s["weekday_et"],
        "regular_open_et": s["regular_open_et"],
        "regular_close_et": s["regular_close_et"],
        "seconds_until_close": s.get("seconds_until_close"),
        "seconds_to_open": nxt.get("seconds_until_open"),
        "market_open_et": nxt.get("opens_et"),
    }


@app.route("/api/scheduler")
def api_scheduler():
    st = _read_json("scheduler_status.json")
    now_et = _dt.now(_ET)
    clock = _market_clock()
    secs, open_iso = clock["seconds_to_open"], clock["market_open_et"]

    # How old is the status file itself? A frozen file means the scheduler task
    # hasn't run — so its stored fields (skip_reason, errors, next_run,
    # premarket_ready) describe an OLD run and must not be shown as if current.
    status_path = os.path.join(_DATA_DIR, "scheduler_status.json")
    status_age = round(_time.time() - os.path.getmtime(status_path)) if os.path.exists(status_path) else None
    stale = status_age is None or status_age > 18 * 3600  # nothing written in ~18h

    # Recompute the next scheduled run fresh — the stored value goes stale the moment
    # a run is missed and can even point to a time already in the past.
    try:
        from scheduler_run import next_scheduled
        next_run = next_scheduled(now_et) or st.get("next_run")
    except Exception:
        next_run = st.get("next_run")

    # Pre-market readiness = TODAY's protective run actually produced a fresh
    # artifact, not a boolean frozen days ago.
    ldr = _fresh("last_daily_run.json")
    premarket_ready = bool(ldr and ldr["age_seconds"] < 18 * 3600)

    return jsonify({
        "now_et": now_et.isoformat(),
        "seconds_to_open": secs, "market_open_et": open_iso,
        # The authoritative session, so the header can say what the market is ACTUALLY
        # doing instead of inferring it from a countdown that only points forward.
        "market": clock,
        "last_success_at": st.get("last_success_at"),
        "last_run_at": st.get("last_run_at"), "last_run_ok": st.get("last_run_ok"),
        "current_status": ("stale" if stale else st.get("current_status", "idle")),
        "status_age_seconds": status_age, "status_stale": stale,
        "running_stage": st.get("running_stage"), "last_stage": st.get("last_stage"),
        "next_run": next_run, "timezone": st.get("timezone", "America/New_York"),
        "premarket_ready": premarket_ready,
        # Once the status file is stale its stored errors/skip_reason are an old run's
        # — suppress them so they don't read as a permanent false alarm.
        "errors": [] if stale else st.get("errors", []),
        "stage_results": st.get("stage_results", []),
        "skip_reason": None if stale else st.get("skip_reason"),
        "data_freshness": {"last_daily_run": _fresh("last_daily_run.json"),
                           "last_scan_both": _fresh("last_scan_both.json"),
                           "lab_report": _fresh("lab_report.json")},
        # Authoritative market-DATA freshness (shared classifier), distinct from the
        # scheduler-task idle state above. The header 'Data' badge uses THIS.
        "data_state": _data_state("last_scan_both.json", "last_daily_run.json"),
    })


@app.route("/api/insights")
def api_insights():
    r = _read_json("lab_report.json")
    return jsonify(_clean({
        "generated": r.get("ran_at"),
        "engine": r.get("engine") or {},
        "catalysts": (_read_json("catalysts.json").get("catalysts") or [])[:12],
        "journal": _read_json("journal_lab.json"),
        "champions": _read_json("champions.json"),
        "calibration": _read_json("calibration.json"),
        "scan": _read_json("last_scan_both.json"),
    }))


@app.route("/api/history")
def api_history():
    strategy.setup_account(reset=False)
    s = strategy.account_status()
    closed = [e for e in s["journal"]["entries"] if e.get("status") == "closed"]
    return jsonify({"closed": closed, "equity_curve": s.get("equity_curve", []),
                    "realized_pnl": s["journal"]["total_realized_pnl"],
                    "win_rate": s["journal"]["win_rate_pct"]})


# ── Universal research routes (any US symbol; reuse every provider) ───────────
import research as _R  # dashboard/ is on sys.path when app.py runs


def _sym_arg():
    from flask import request
    return request.args.get("symbol") or request.args.get("q") or ""


@app.route("/api/search")
def api_search():
    """Universal, instant ticker search over the LOCAL security master — symbol,
    company name, alias, foreign listing, typo-tolerant. No provider call, no
    per-keystroke latency. Target < 150 ms."""
    from flask import request
    q = request.args.get("q") or request.args.get("symbol") or ""
    try:
        limit = min(25, max(1, int(request.args.get("limit", "10"))))
    except Exception:
        limit = 10
    with _R.timed("api.search"):
        out = _R.search(q, limit)
    return jsonify(out)


@app.route("/api/search/popular")
def api_search_popular():
    return jsonify({"results": _R.popular_symbols(12)})


@app.route("/api/search/stats")
def api_search_stats():
    return jsonify(_R.master_stats())


@app.route("/api/symbol/resolve")
def api_sym_resolve():
    return jsonify(_R.resolve_symbol(_sym_arg()))


@app.route("/api/symbol/summary")
def api_sym_summary():
    """Fast partial decision (trend + regime + scenarios) for instant paint —
    returned with analysis_status='summary' and the families still computing."""
    with _R.timed("api.summary"):
        return jsonify(_clean(_R.summary(_sym_arg())))


@app.route("/api/symbol/overview")
def api_sym_overview():
    return jsonify(_clean(_R.overview(_sym_arg())))


@app.route("/api/symbol/technicals")
def api_sym_technicals():
    return jsonify(_clean(_R.technicals(_sym_arg())))


@app.route("/api/symbol/fundamentals")
def api_sym_fundamentals():
    return jsonify(_clean(_R.fundamentals(_sym_arg())))


@app.route("/api/symbol/options")
def api_sym_options():
    from flask import request
    return jsonify(_clean(_R.options(_sym_arg(), expiry=request.args.get("expiry"),
                                     side=request.args.get("side", "CALL"))))


@app.route("/api/symbol/catalysts")
def api_sym_catalysts():
    return jsonify(_clean(_R.catalysts(_sym_arg())))


@app.route("/api/symbol/sentiment")
def api_sym_sentiment():
    return jsonify(_clean(_R.news_sentiment(_sym_arg())))


@app.route("/api/symbol/chart")
def api_sym_chart():
    from flask import request
    return jsonify(_clean(_R.price_history(_sym_arg(), request.args.get("range", "3M"))))


@app.route("/api/models")
def api_models():
    """Model-registry status: which sentiment backend is active (HF FinBERT vs the
    lexical fallback), whether transformers is installed, and configured slots."""
    return jsonify(_clean(_R.registry_status()))


@app.route("/api/models/sentiment-health")
def api_models_sentiment_health():
    """FinBERT service health."""
    try:
        from tradingview_mcp.lab import finbert_service
        return jsonify(_clean(finbert_service.health()))
    except Exception as e:
        return jsonify({"error": str(e), "enabled": False, "available": False})


@app.route("/api/compare")
def api_compare():
    """Compare 2-8 securities on one normalized schema (aligned %-performance,
    volatility, beta, drawdown, correlation, fundamentals, technicals)."""
    from flask import request
    raw = request.args.get("symbols") or request.args.get("q") or ""
    syms = [s for s in raw.replace(" ", ",").split(",") if s.strip()]
    rng = request.args.get("range", "6M")
    with _R.timed("api.compare"):
        out = _R.compare(syms, rng)
    return jsonify(_clean(out))


@app.route("/api/sectors")
def api_sectors():
    """Sector Map — TradingView-free heatmap over the 11 SPDR sector ETFs."""
    from flask import request
    weighting = request.args.get("weighting", "cap")
    import sector_map as _SEC
    with _R.timed("api.sectors"):
        out = _SEC.sector_map(weighting if weighting in ("cap", "equal") else "cap")
    return jsonify(_clean(out))


@app.route("/api/sector/<key>")
def api_sector_detail(key):
    """Drill-down: ETF proxy, industry groups, ranked stocks, trend, RS vs SPY,
    catalysts and options availability for one sector."""
    import sector_map as _SEC
    with _R.timed("api.sector_detail"):
        out = _SEC.sector_detail(key)
    return jsonify(_clean(out))


# ── Research terminal: regime, scan funnel, options desk, tracker ────────────
# Every one of these is READ-ONLY. None of them can place, preview or queue an
# order; the options desk reaches Robinhood exclusively through the read allowlist.

@app.route("/api/regime")
def api_regime():
    """Session state (ET/IST, pre/open/after/closed/holiday) + the tape + the regime
    classification with the evidence behind it."""
    from flask import request
    import market_regime as _MR
    blocking = request.args.get("blocking") in ("1", "true", "yes")
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    with _R.timed("api.regime"):
        out = _MR.overview(refresh=refresh, blocking=blocking)
    return jsonify(_clean(out))


@app.route("/api/session")
def api_session():
    """Pure-calendar session state. No provider, so it can never be stale or fail."""
    import market_regime as _MR
    return jsonify(_clean(_MR.session_state()))


@app.route("/api/scan")
def api_scan():
    """The staged scan funnel: universe → eligible → sector-aligned → deep → top25 →
    top10 → finalists, plus the rejection ledger. Non-blocking by default.

    `peek=1` reads the cache WITHOUT starting a run. This is a UNIVERSE operation
    (~800 names, 45-76s), so the terminal loads with a peek and only starts one when
    the user explicitly asks — a widget appearing on screen must not cost a scan.
    """
    from flask import request
    import scanner as _SC
    preset = request.args.get("preset", "liquid")
    blocking = request.args.get("blocking") in ("1", "true", "yes")
    if request.args.get("peek") in ("1", "true", "yes"):
        cached_scan = _R.peek(f"scan:{preset}")
        if not cached_scan:
            return jsonify({"state": "never_run", "preset": preset, "scope": "universe",
                            "reason": "no scan in cache — start one explicitly",
                            "candidates": [], "finalists": [], "stages": [],
                            "rejected": []})
        return jsonify(_clean({**cached_scan, "preset": preset, "scope": "universe",
                               "cache_state": _R.cache_state(f"scan:{preset}"),
                               "cache_age_s": _R.cache_age(f"scan:{preset}")}))
    with _R.timed("api.scan"):
        out = _SC.scan_cached(preset, blocking=blocking)
    return jsonify(_clean({**out, "scope": "universe"}))


@app.route("/api/scan/rejected")
def api_scan_rejected():
    """Rejected candidates with the stage and reason for each drop."""
    from flask import request
    import scanner as _SC
    preset = request.args.get("preset", "liquid")
    stage = request.args.get("stage")
    out = _SC.scan_cached(preset, blocking=False)
    rej = out.get("rejected") or []
    if stage:
        rej = [r for r in rej if r.get("stage") == stage]
    by_stage = {}
    for r in (out.get("rejected") or []):
        by_stage[r.get("stage", "?")] = by_stage.get(r.get("stage", "?"), 0) + 1
    return jsonify(_clean({"state": out.get("state"), "preset": preset,
                           "rejected": rej[:400], "count": len(rej),
                           "by_stage": by_stage, "generated_at": out.get("generated_at")}))


@app.route("/api/options/desk")
def api_options_desk():
    """Chain analysis + shares-vs-option verdict for ONE symbol. READ-ONLY.

    Any symbol works. If the name happens to be in the current scan its funnel-scored
    candidate is reused; otherwise the SAME per-name pipeline is run for that one
    symbol (`scanner.analyse_symbol`) — selecting a ticker must never cost a universe
    scan. A name analysed on demand carries `funnel.failed_gates`, so a stock the
    scanner would have rejected is visibly different from one it surfaced.
    """
    from flask import request
    import scanner as _SC
    import options_desk as _OD
    sym = (request.args.get("symbol") or "").upper().strip()
    if not sym:
        return jsonify({"state": "error", "reason": "symbol is required"}), 400
    force = request.args.get("refresh") in ("1", "true", "yes")
    scan = _SC.scan_cached(request.args.get("preset", "liquid"), blocking=False)
    cand = next((c for c in (scan.get("candidates") or []) if c["symbol"] == sym), None)
    origin = "scan"
    if cand is None:
        origin = "on_demand"
        cand = _SC.analyse_symbol_cached(sym, force=force)
        if cand.get("state") != "ok":
            return jsonify(_clean({**cand, "state": cand.get("state", "error"),
                                   "symbol": sym, "origin": origin})), 200
    with _R.timed("api.options_desk"):
        if force:
            _R.invalidate(f"desk:{sym}")
        out = _R.cached(f"desk:{sym}", 180, lambda: _OD.evaluate_candidate(cand))
    return jsonify(_clean({**out, "candidate_origin": origin,
                           "funnel": cand.get("funnel"), "setups": cand.get("setups"),
                           "levels": cand.get("levels"), "score": cand.get("score"),
                           "indicators": cand.get("indicators")}))


@app.route("/api/options/account")
def api_options_account():
    """Robinhood buying power / positions — READ ONLY, never an order path."""
    import options_desk as _OD
    with _R.timed("api.options_account"):
        out = _R.cached("desk:account", 60, _OD.account)
    return jsonify(_clean(out))


@app.route("/api/options/watchlists")
def api_options_watchlists():
    """Robinhood watchlists — READ ONLY. Every mutating watchlist tool is off the
    allowlist and refused before the network."""
    from flask import request
    import options_desk as _OD
    force = request.args.get("refresh") in ("1", "true", "yes")
    with _R.timed("api.watchlists"):
        out = _OD.watchlists(force=force)
    return jsonify(_clean(out))


@app.route("/api/tracker")
def api_tracker():
    from flask import request
    import tracker as _TR
    inc = request.args.get("include_closed") in ("1", "true", "yes")
    with _R.timed("api.tracker"):
        out = _TR.list_setups(include_closed=inc)
    return jsonify(_clean(out))


@app.route("/api/tracker/update", methods=["POST", "GET"])
def api_tracker_update():
    """Re-check tracked setups against fresh quotes. Cannot place an order."""
    import tracker as _TR
    with _R.timed("api.tracker_update"):
        out = _TR.update_all()
    return jsonify(_clean(out))


@app.route("/api/tracker/track", methods=["POST"])
def api_tracker_track():
    """Add the current scan's finalists to the tracker. Tracking only — no order."""
    from flask import request
    import scanner as _SC
    import options_desk as _OD
    import tracker as _TR
    body = request.get_json(silent=True) or {}
    preset = body.get("preset", "liquid")
    scan = _SC.scan_cached(preset, blocking=False)
    if scan.get("state") == "loading":
        return jsonify({"state": "loading", "reason": "scan still running — try again shortly"}), 202
    wanted = set(body.get("symbols") or [])
    pool = scan.get("finalists") or []
    if wanted:
        pool = [c for c in (scan.get("candidates") or []) if c["symbol"] in wanted]
    acct = _OD.account()
    added = []
    for cand in pool:
        try:
            ev = _OD.evaluate_candidate(cand, account_info=acct)
            added.append(_TR.add_setup(cand, verdict=ev.get("decision"),
                                       contract=ev.get("best_contract")))
        except Exception as e:  # noqa: BLE001
            added.append({"state": "error", "symbol": cand.get("symbol"), "reason": str(e)[:120]})
    return jsonify(_clean({"state": "ok", "results": added,
                           "order_placement": "disabled"}))


@app.route("/api/tracker/close", methods=["POST"])
def api_tracker_close():
    from flask import request
    import tracker as _TR
    body = request.get_json(silent=True) or {}
    sid = body.get("setup_id")
    if not sid:
        return jsonify({"state": "error", "reason": "setup_id required"}), 400
    return jsonify(_clean(_TR.close_setup(sid, body.get("reason") or "closed from the dashboard")))


@app.route("/api/tracker/alerts")
def api_tracker_alerts():
    from flask import request
    import tracker as _TR
    only = request.args.get("unacknowledged") in ("1", "true", "yes")
    return jsonify(_clean(_TR.recent_alerts(unacknowledged_only=only)))


@app.route("/api/tracker/schedule")
def api_tracker_schedule():
    """Cadence + the next scheduled runs, market-calendar aware."""
    import tracker as _TR
    out = _TR.next_runs()
    out["stats"] = _TR.stats()
    return jsonify(_clean(out))


# ── Scan cohort — the Top-5 MEMORY layer (separate from the entry-trigger alert
# tracker above). See dashboard/scan_cohort.py for the RAW → OUTCOME → AGGREGATE →
# DERIVED architecture. Read-only except /update, which only re-checks status. ──

@app.route("/api/cohort/today")
def api_cohort_today():
    """The most recent scan's Top-5, live-enriched with current price + return
    since scan. `scan_id` picks a specific historical cohort instead of latest."""
    from flask import request
    import scan_cohort as _SCO
    sid = request.args.get("scan_id")
    preset = request.args.get("preset")
    with _R.timed("api.cohort_today"):
        out = _SCO.cohort(sid, preset=preset, live=True)
    return jsonify(_clean(out))


@app.route("/api/cohort/history")
def api_cohort_history():
    import scan_cohort as _SCO
    from flask import request
    limit = int(request.args.get("limit", 30))
    with _R.timed("api.cohort_history"):
        out = _SCO.list_scan_runs(limit=limit)
    return jsonify(_clean(out))


@app.route("/api/cohort/update", methods=["POST", "GET"])
def api_cohort_update():
    """Re-check tracked observations against fresh quotes. Status changes only —
    never removes an observation, never places an order."""
    import scan_cohort as _SCO
    from flask import request
    sid = request.args.get("scan_id") or (request.get_json(silent=True) or {}).get("scan_id")
    with _R.timed("api.cohort_update"):
        out = _SCO.update_observations(sid)
    return jsonify(_clean(out))


@app.route("/api/cohort/intelligence")
def api_cohort_intelligence():
    """Accumulated scanner performance: rank/score/setup-type breakdowns and the
    repetition diagnostic, each computed fresh from persisted observations."""
    from flask import request
    import scan_cohort as _SCO
    horizon = request.args.get("horizon", "d5")
    with _R.timed("api.cohort_intelligence"):
        out = {
            "state": "ok", "horizon": horizon,
            "rank": _SCO.rank_performance(horizon),
            "score_bucket": _SCO.score_bucket_performance(horizon),
            "setup_type": _SCO.setup_type_performance(horizon),
            "repetition": _SCO.repetition_stats(),
        }
    return jsonify(_clean(out))


@app.route("/api/cohort/review")
def api_cohort_review():
    from flask import request
    import scan_cohort as _SCO
    date_ = request.args.get("date")
    with _R.timed("api.cohort_review"):
        out = _SCO.daily_review(date_)
    return jsonify(_clean(out))


@app.route("/api/paper/account")
def api_paper_account():
    """PAPER account only. Deliberately a separate endpoint from the Robinhood cash and
    Robinhood agentic views so simulated and real money can never be conflated."""
    from paper import broker as _pb, config as _pc, db as _pdb
    acct = _pb.account()
    return jsonify(_clean({
        "account_type": "paper", "is_simulated": True, "is_real_money": False,
        "label": "Paper (simulated ledger)",
        **acct,
        "config": _pc.snapshot(),
        "reconciliation": _pb.reconcile(),
        "schema_version": _pdb.schema_version(),
        "config_version": _pdb.config_version(),
    }))


@app.route("/api/paper/report")
def api_paper_report():
    from flask import request
    from paper import report as _pr
    d = request.args.get("date")
    return jsonify(_clean(_pr.daily(d) if d else _pr.daily()))


@app.route("/api/paper/performance")
def api_paper_performance():
    from paper import report as _pr
    return jsonify(_clean(_pr.performance()))


@app.route("/api/paper/signals")
def api_paper_signals():
    """Every signal — including the REJECT and MONITOR ones we did not trade."""
    from flask import request
    from paper import journal as _pj
    return jsonify(_clean({
        "signals": _pj.signals(session_date=request.args.get("date"),
                               action=request.args.get("action"),
                               limit=int(request.args.get("limit", 100))),
        "gate_outcomes": _pj.gate_outcomes(),
        "blocked_winners": _pj.blocked_winners(),
    }))


@app.route("/api/paper/options_shadow")
def api_paper_options_shadow():
    from paper import options_shadow as _os
    return jsonify(_clean({"summary": _os.summary(),
                           "graduation": _os.graduation_readiness()}))


# ── TERMINAL: the selected-symbol workspace ──────────────────────────────────
# One symbol, one snapshot, one coherent refresh model. These three routes are what
# the terminal actually runs on; the per-family /api/symbol/* routes remain for
# targeted use but the workspace no longer stitches its own view out of them.

@app.route("/api/terminal/symbol")
def api_terminal_symbol():
    """Canonical snapshot of the SELECTED symbol.

    Every section carries its own data, its own typed freshness and its own cache
    state. Sections still computing return state='loading' (the shell paints first);
    a section whose provider failed returns state='error' without taking its siblings
    down. Cheap and expensive work are separated: this NEVER runs a universe scan.
    """
    from flask import request
    import symbol_snapshot as _SS
    sym = (request.args.get("symbol") or "").upper().strip()
    if not sym:
        return jsonify({"state": "error", "reason": "symbol is required"}), 400
    secs = request.args.get("sections")
    sections = [s.strip() for s in secs.split(",") if s.strip()] if secs else None
    blocking = request.args.get("blocking") in ("1", "true", "yes")
    with _R.timed("api.terminal_symbol", sym):
        out = _SS.snapshot(sym, sections=sections,
                           rng=request.args.get("range", "3M"),
                           blocking=blocking)
    return jsonify(_clean(out))


@app.route("/api/terminal/refresh", methods=["POST", "GET"])
def api_terminal_refresh():
    """Explicit refresh of ONE symbol. Drops only that symbol's caches.

    This is the cheap operation. The universe scan is the expensive one and lives
    behind /api/scan — changing the selected ticker must never trigger it, which is
    why the two have separate routes and separate cache namespaces.
    """
    from flask import request
    import symbol_snapshot as _SS
    body = request.get_json(silent=True) or {}
    sym = ((request.args.get("symbol") or body.get("symbol") or "")).upper().strip()
    if not sym:
        return jsonify({"state": "error", "reason": "symbol is required"}), 400
    secs = request.args.get("sections") or body.get("sections")
    if isinstance(secs, str):
        secs = [s.strip() for s in secs.split(",") if s.strip()]
    with _R.timed("api.terminal_refresh", sym):
        out = _SS.refresh(sym, sections=secs or None,
                          rng=request.args.get("range", "3M"))
    return jsonify(_clean(out))


@app.route("/api/terminal/history")
def api_terminal_history():
    """Historical validation for one symbol's live strategy, or an explicit one."""
    from flask import request
    import history as _H
    import scanner as _SC
    sym = (request.args.get("symbol") or "").upper().strip()
    if not sym:
        return jsonify({"state": "error", "reason": "symbol is required"}), 400
    strategy = request.args.get("strategy")
    cand = _SC.analyse_symbol_cached(sym)
    if strategy is None and isinstance(cand, dict):
        strategy = cand.get("primary_setup")
    import symbol_snapshot as _SS
    regime = _SS._market_regime_now()
    feats = _H.current_features(cand if isinstance(cand, dict) else {}, regime)
    with _R.timed("api.terminal_history", sym):
        out = _H.evaluate(sym, strategy=strategy, current_features=feats,
                          sector=(cand or {}).get("sector"))
    return jsonify(_clean(out))


@app.route("/api/terminal/snapshots")
def api_terminal_snapshots():
    """The terminal's own recorded strategy dataset — and how much of it has resolved."""
    from flask import request
    import strategy_store as _ST
    if request.args.get("resolve") in ("1", "true", "yes"):
        _ST.attach_outcomes()
    return jsonify(_clean(_ST.stats()))


@app.route("/api/terminal/status")
def api_terminal_status():
    """ONE coherent answer to 'what is the state of this terminal'.

    The header used to render three independently-derived facts side by side —
    `DATA stale`, `scheduler idle`, `scanner refreshing` — which read as a
    contradiction because they answered three different questions with one vocabulary.
    They are separated here by NAME and by SCOPE:

        market    the authoritative exchange session (calendar, never inferred)
        datasets  per-dataset freshness, each against its own window
        jobs      background work: is it running, when did it last succeed

    A job being idle is not a data fault; a shut exchange is not a stale feed.
    """
    st = _read_json("scheduler_status.json")
    status_path = os.path.join(_DATA_DIR, "scheduler_status.json")
    status_age = round(_time.time() - os.path.getmtime(status_path)) if os.path.exists(status_path) else None
    scheduler_idle = status_age is None or status_age > 18 * 3600
    clock = _market_clock()
    mstate = clock.get("state")

    # `label` on a freshness record is the AGE phrase ("market closed — last close
    # (1h ago)"), not a name for the dataset. Spreading the record last overwrote the
    # human label and the strip rendered the age where the dataset name belongs — so
    # the record is nested under `freshness` and the display name is set explicitly.
    def _dataset(did, label, scope, rec, **extra):
        rec = dict(rec or {})
        return {"id": did, "label": label, "scope": scope,
                "freshness_label": rec.pop("label", None), **rec, **extra}

    datasets = [_dataset("batch_scan", "Batch scan artifacts", "batch-scan",
                         _data_state("last_scan_both.json", "last_daily_run.json"))]
    try:
        # PEEK, never scan_cached: a status readout that starts an 800-name scan is
        # how "scanner refreshing" ended up permanently on screen next to stale data.
        sc = _R.peek("scan:liquid") or {}
        age = _R.cache_age("scan:liquid")
        import freshness as _fr
        datasets.append(_dataset(
            "scanner", "Live scanner", "universe",
            _fr.classify_typed("scanner", age, market_state=mstate),
            has_run=bool(sc),
            candidates=len(sc.get("candidates") or []),
            finalists=len(sc.get("finalists") or []),
            running=_R.cache_state("scan:liquid") == "refreshing"))
    except Exception as e:  # noqa: BLE001
        datasets.append({"id": "scanner", "label": "Live scanner", "scope": "universe",
                         "state": "unknown", "error": str(e)[:100]})

    # `running_stage` is a field the scheduler WRITES when it starts and does not
    # always clear — so on its own it reports a run that died days ago as "running".
    # A run is only live if it also wrote its status within the last few minutes;
    # a stale claim is a STALLED run, which is a different thing to say than "running"
    # and a different thing again from "idle".
    stage = st.get("running_stage")
    claims_running = bool(stage)
    recently_written = status_age is not None and status_age < 300
    running = claims_running and recently_written
    stalled = claims_running and not recently_written
    jobs = [{"id": "scheduler", "label": "Background scheduler",
             "running": running, "stalled": stalled,
             "idle": scheduler_idle or (not claims_running),
             "running_stage": stage,
             "last_run_at": st.get("last_run_at"),
             "last_success_at": st.get("last_success_at"),
             "last_run_ok": st.get("last_run_ok"),
             "age_seconds": status_age,
             "note": ("hasn't written a status in over 18h — this is the JOB, not the "
                      "market data" if scheduler_idle else
                      f"claims stage '{stage}' but hasn't written a status in "
                      f"{status_age}s — the run is stalled, not live" if stalled else None)}]
    try:
        jobs.append({"id": "scan", "label": "Universe scan",
                     "running": _R.cache_state("scan:liquid") == "refreshing",
                     "age_seconds": _R.cache_age("scan:liquid"),
                     "note": "expensive — 800 names; never triggered by selecting a symbol"})
    except Exception:
        pass

    return jsonify(_clean({
        "market": clock,
        "datasets": datasets,
        "jobs": jobs,
        "any_job_running": any(j.get("running") for j in jobs),
        "worst_dataset_state": _fresh_worst(datasets),
        "generated_at": _dt.now(_ET).isoformat(),
    }))


def _fresh_worst(datasets):
    import freshness as _fr
    return _fr.worst(*[d.get("state") for d in datasets if d.get("state")])


@app.route("/api/providers")
def api_providers():
    """The normalized provider matrix + inventory + cache-provenance summary."""
    import providers as _P
    import cache_policy as _CP
    return jsonify(_clean({
        "matrix": _P.matrix_rows(),
        "inventory": _P.provider_inventory(),
        "provenance": _CP.summary(),
        "recent_provenance": _CP.recent(30),
    }))


@app.route("/api/health")
def api_health():
    st = _read_json("scheduler_status.json")
    status_path = os.path.join(_DATA_DIR, "scheduler_status.json")
    status_age = round(_time.time() - os.path.getmtime(status_path)) if os.path.exists(status_path) else None
    _prov = {}
    try:
        import providers as _P
        import cache_policy as _CP
        _prov = {"tradingview_enabled": _P.tv_enabled(),
                 "tradingview_role": "optional technical-signal confirmation only",
                 "cache_provenance": _CP.summary()}
    except Exception:
        pass
    return jsonify(_clean({
        "providers": _R.provider_health(),
        "models": _R.registry_status(),
        "search": _R.master_stats(),
        "provider_layer": _prov,
        "scheduler": {"last_success_at": st.get("last_success_at"),
                      "last_run_ok": st.get("last_run_ok"),
                      "status_age_seconds": status_age,
                      "stale": status_age is None or status_age > 18 * 3600,
                      "stage_results": st.get("stage_results", [])},
        "data_freshness": {"last_daily_run": _fresh("last_daily_run.json"),
                           "last_scan_both": _fresh("last_scan_both.json"),
                           "catalysts": _fresh("catalysts.json")},
        "data_state": _data_state("last_scan_both.json", "last_daily_run.json"),
    }))


# ── Per-request timing (every endpoint, automatically) ───────────────────────
from flask import g as _g, request as _req


@app.before_request
def _perf_start():
    _g._t0 = _time.perf_counter()


@app.after_request
def _perf_end(resp):
    try:
        ms = round((_time.perf_counter() - _g._t0) * 1000, 1)
        resp.headers["X-Duration-ms"] = str(ms)
        if _req.path.startswith("/api/"):
            _R._DIAG.append({"stage": "HTTP " + _req.path, "ms": ms,
                             "extra": resp.headers.get("X-Cache"),
                             "at": _dt.now(_ET).isoformat()})
    except Exception:
        pass
    return resp


# ── Fast trading bundle: portfolio + manage + regime, CONCURRENT + cached ─────
@app.route("/api/trading")
def api_trading():
    """One round-trip for Trading Mode's critical data. Everything is SWR-cached
    and fetched concurrently, so it stays fast (and usable) even if the market
    regime provider is slow."""
    with _R.timed("api.trading"):
        res = _R.gather({
            "state": lambda: _R.swr("acct:state", _R.TTL["portfolio"], _account_status),
            "manage": lambda: _R.swr("acct:manage", _R.TTL["manage"],
                                     lambda: strategy.manage_positions(execute_stops=False)),
            "market": lambda: _R.swr("regime", _R.TTL["regime"], strategy.market_regime),
        }, timeout=8.0)

    def _unwrap(name):
        v = res.get(name)
        return v[0] if isinstance(v, tuple) else (None if isinstance(v, dict) and "__err" in v else v)

    # Trim the heavy journal + equity_curve out of state — Trading Mode only needs the
    # live risk fields, so we ship a small payload (faster serialization + transfer).
    s = _unwrap("state") or {}
    keep = ("total_equity", "total_return_pct", "total_unrealized_pnl", "cash",
            "stock_market_value", "option_market_value", "stock_positions", "option_positions")
    state = {k: s[k] for k in keep if k in s} if isinstance(s, dict) else s
    return jsonify(_clean({"state": state, "manage": _unwrap("manage"), "market": _unwrap("market"),
                           "cache": {k: (res[k][1] if isinstance(res.get(k), tuple) else "err") for k in ("state", "manage", "market")}}))


# ── Bulk quotes (concurrent, keep-alive) ─────────────────────────────────────
@app.route("/api/quotes")
def api_quotes():
    syms = [s for s in (_req.args.get("symbols", "").split(",")) if s.strip()][:25]

    def _q(sym):
        s = _R.canonical(sym)

        def _fetch():
            import finnhub_data
            q = finnhub_data.quote(_R.to_finnhub(s))
            if isinstance(q, dict) and q.get("c"):
                return {"symbol": s, "price": q.get("c"), "change_pct": q.get("dp"), "state": "ok"}
            return {"symbol": s, "state": "empty"}
        return _R.swr(f"quote:{s}", _R.TTL["quote"], _fetch)[0]

    with _R.timed("api.quotes"):
        out = _R.gather({s: (lambda s=s: _q(s)) for s in syms}, timeout=6.0)
    return jsonify(_clean({"quotes": [out[s] for s in syms if not (isinstance(out.get(s), dict) and "__err" in out[s])]}))


# ── Diagnostics (dev perf panel) ─────────────────────────────────────────────
@app.route("/api/diag")
def api_diag():
    return jsonify(_clean(_R.diag_snapshot()))


# ── Boot pre-warmer: fill the hot caches in the background so the first real
#    user interaction is already warm (portfolio, regime, candidates). ─────────
def _prewarm():
    import threading

    def _warm_candidates():
        # Warm the SAME key the endpoint uses (cand:opt:liquid) with the preset
        # universe, so the first scan is already warm instead of a cold miss.
        uni = (_R.build_scan_universe("liquid") or {}).get("tickers")
        return _R.swr("cand:opt:liquid", 120, lambda: strategy.find_trades(
            max_candidates=6, include_options=True, options_only=True,
            universe=uni, preset="liquid"))

    def _warm():
        for fn in (lambda: _R._SM.ensure_loaded(),  # build/load the ticker universe up front
                   lambda: _R.swr("acct:state", _R.TTL["portfolio"], _account_status),
                   lambda: _R.swr("regime", _R.TTL["regime"], strategy.market_regime),
                   lambda: _R.swr("acct:manage", _R.TTL["manage"],
                                  lambda: strategy.manage_positions(execute_stops=False)),
                   _warm_candidates):
            try:
                fn()
            except Exception:
                pass
        # FinBERT loads LAST, in its own background thread, and ONLY if enabled —
        # so the model never delays the page and startup stays fast when it's off.
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab"))
            import finbert_service
            finbert_service.warm_async()
        except Exception:
            pass
    threading.Thread(target=_warm, daemon=True).start()


_prewarm()


if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", "5057"))
    app.run(host="127.0.0.1", port=port, debug=False)
