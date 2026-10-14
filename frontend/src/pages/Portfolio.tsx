import { useState } from 'react';
import { cls, money, num, pct, us } from '../lib/format';
import { usePaperGet } from '../lib/nav';
import { Badge, EmptyBox, Kv, Note, Skel } from '../components/ui';
import { Card, KeyTable, Kpi, LineChart, PageHead, SymLink, TabBtns, fmtCell } from '../components/blocks';

type Tab = 'positions' | 'performance' | 'signals' | 'options';

export default function Portfolio() {
  const [tab, setTab] = useState<Tab>('positions');
  const a = usePaperGet<any>('/paper/account', { poll: 15000 }).data;
  const perf = usePaperGet<any>('/paper/performance').data;
  if (!a) return <><PageHead title="Portfolio" /><Skel n={8} /></>;
  if (a.__err) return <><PageHead title="Portfolio" /><EmptyBox ico="⚠" msg="Paper account unavailable" sub={a.__err} /></>;
  const ov = (perf && perf.overall) || {};
  return (
    <>
      <PageHead title="Portfolio" sub="The simulated $500 cash account: no margin, no shorting, no naked options. Nothing here is real money." right={<Badge cls="b-info">PAPER · READ-ONLY</Badge>} />
      <div className="stack">
        <div className="kpis">
          <Kpi l="Equity" value={a.equity} format={money} sub={`Started at ${money(a.starting_equity)}`} />
          <Kpi l="Cash" value={a.cash} format={money} sub={`Buying power ${money(a.buying_power)}`} />
          <Kpi l="Positions value" value={a.positions_value} format={money} sub={`${num(a.capital_utilization_pct, 0)}% deployed`} />
          <Kpi l="Realised P&L" value={a.realized_pnl} format={money} tone={cls(a.realized_pnl)} />
          <Kpi l="Unrealised P&L" value={a.unrealized_pnl} format={money} tone={cls(a.unrealized_pnl)} />
          <Kpi l="Drawdown" value={a.drawdown_pct} format={(n) => num(n, 2) + '%'} tone={a.drawdown_pct > 0 ? 'amber' : ''} sub={`Win rate ${ov.win_rate != null ? Math.round(ov.win_rate) + '%' : '–'} · ${ov.n ?? 0} trades`} />
        </div>
        <TabBtns<Tab> items={[['positions', 'Positions'], ['performance', 'Performance'], ['signals', 'Signals & gates'], ['options', 'Options (shadow)']]} value={tab} onChange={setTab} />
        {tab === 'positions' && <Positions a={a} />}
        {tab === 'performance' && <Performance perf={perf} />}
        {tab === 'signals' && <Signals />}
        {tab === 'options' && <OptionsShadow />}
        <Note>Ledger <b>{a.ledger || ''}</b> · simulated, not real money. {(a.config && a.config.note) || ''}</Note>
      </div>
    </>
  );
}

function Positions({ a }: { a: any }) {
  const open = a.open || [], closed = a.closed || [];
  return (
    <>
      <Card title="Open positions" flush>
        {open.length ? (
          <table><thead><tr>{['Symbol', 'Qty', 'Entry', 'Current', 'P&L $', 'P&L %', 'Stop', 'Target', 'Planned risk'].map((h, i) => <th key={h} className="nosort">{h}</th>)}<th className="nosort l">Strategy</th></tr></thead><tbody>
            {open.map((p: any, i: number) => (
              <tr key={i}><td><SymLink s={p.symbol} /></td><td>{num(p.quantity, 4)}</td><td>{num(p.avg_entry, 2)}</td><td title={p.mark_source || ''}>{p.current_price != null ? num(p.current_price, 2) : '–'}</td>
                <td className={cls(p.unrealized_pnl)}>{p.unrealized_pnl != null ? money(p.unrealized_pnl) : '–'}</td><td className={cls(p.unrealized_pnl_pct)}>{p.unrealized_pnl_pct != null ? num(p.unrealized_pnl_pct, 2) + '%' : '–'}</td>
                <td className="red">{num(p.stop, 2)}</td><td className="green">{num(p.target, 2)}</td><td>{money(p.planned_risk)}</td><td className="l" style={{ fontSize: 11 }}>{p.strategy || ''}</td></tr>
            ))}
          </tbody></table>
        ) : <EmptyBox ico="○" msg="No open positions" sub="The paper account is flat." />}
      </Card>
      <Card title="Closed trades" flush>
        {closed.length ? (
          <table><thead><tr>{['Symbol', 'Qty', 'Entry', 'Exit', 'P&L'].map((h) => <th key={h} className="nosort">{h}</th>)}<th className="nosort l">Exit reason</th></tr></thead><tbody>
            {closed.slice(-40).reverse().map((p: any, i: number) => (
              <tr key={i}><td><SymLink s={p.symbol} /></td><td>{num(p.quantity, 4)}</td><td>{num(p.avg_entry, 2)}</td><td>{num(p.avg_exit, 2)}</td><td className={cls(p.realized_pnl)}>{money(p.realized_pnl)}</td><td className="l" style={{ fontSize: 11 }}>{p.exit_reason || ''}</td></tr>
            ))}
          </tbody></table>
        ) : <EmptyBox ico="○" msg="No closed trades yet" />}
      </Card>
    </>
  );
}

function Performance({ perf }: { perf: any }) {
  if (!perf) return <Skel n={8} />;
  if (perf.__err) return <EmptyBox ico="⚠" msg="Performance unavailable" sub={perf.__err} />;
  const curve = (perf.equity_curve || []).map((p: any) => ({ x: p.session_date, y: p.equity }));
  const ov = perf.overall || {}; const bm = perf.benchmarks || {}; const ms = perf.milestone || {}; const ex = perf.excursions || {};
  return (
    <div className="stack">
      <div className="row2">
        <Card title="Equity curve"><LineChart points={curve} height={200} fmt={money} /></Card>
        <div className="stack">
          <Card title="Summary">
            <Kv k="Closed trades">{ov.n ?? 0}</Kv><Kv k="Win rate">{ov.win_rate != null ? num(ov.win_rate, 0) + '%' : '–'}</Kv>
            <Kv k="Average win"><span className="green">{ov.avg_win != null ? money(ov.avg_win) : '–'}</span></Kv><Kv k="Average loss"><span className="red">{ov.avg_loss != null ? money(ov.avg_loss) : '–'}</span></Kv>
            <Kv k="Profit factor">{ov.profit_factor != null ? num(ov.profit_factor, 2) : '–'}</Kv><Kv k="Expectancy">{ov.expectancy != null ? num(ov.expectancy, 2) : '–'}</Kv>
            <Kv k="Max drawdown">{num(perf.max_drawdown_pct, 2)}%</Kv><Kv k="Avg MFE / MAE">{ex.avg_mfe != null ? `${num(ex.avg_mfe, 2)} / ${num(ex.avg_mae, 2)}` : '–'}</Kv>
          </Card>
          <Card title="Versus SPY" sub={bm.from ? `${bm.from} → ${bm.to}` : undefined}>
            <Kv k="Strategy return">{bm.strategy_return_pct != null ? pct(bm.strategy_return_pct) : '–'}</Kv>
            <Kv k="SPY return">{bm.spy_return_pct != null ? pct(bm.spy_return_pct) : '–'}</Kv>
            <Kv k="Excess"><span className={cls(bm.excess_vs_spy_pct)}>{bm.excess_vs_spy_pct != null ? pct(bm.excess_vs_spy_pct) : '–'}</span></Kv>
            <div className="mt8"><div className="flexrow" style={{ justifyContent: 'space-between' }}><span className="dim" style={{ fontSize: 12 }}>Evidence milestone</span><span className="mono" style={{ fontSize: 12 }}>{ms.progress ?? 0} / {ms.target ?? 50}</span></div>
              <div className="hbar"><i style={{ width: Math.min(100, ((ms.progress || 0) / (ms.target || 50)) * 100) + '%', background: 'var(--accent)' }} /></div></div>
          </Card>
        </div>
      </div>
      <div className="row2e">
        <Card title="By strategy" flush><div style={{ padding: 12 }}><KeyTable obj={perf.by_strategy} first="Strategy" /></div></Card>
        <Card title="By sector" flush><div style={{ padding: 12 }}><KeyTable obj={perf.by_sector} first="Sector" /></div></Card>
      </div>
      <Card title="Gate outcomes" sub="What happened to signals the gates allowed versus blocked" flush>
        {(perf.gate_outcomes || []).length ? (
          <table><thead><tr><th>Action</th><th>Signals</th><th>Resolved</th><th>Still open</th><th>Target hit</th><th>Stop hit</th><th>Win rate</th><th>Avg MFE</th><th>Avg MAE</th></tr></thead><tbody>
            {perf.gate_outcomes.map((g: any, i: number) => <tr key={i}><td>{g.action}</td><td>{g.n}</td><td>{g.resolved}</td><td>{g.still_open}</td><td>{g.target_hit}</td><td>{g.stop_hit}</td><td>{g.win_rate != null ? num(g.win_rate, 0) + '%' : '–'}</td><td>{fmtCell(g.avg_mfe)}</td><td>{fmtCell(g.avg_mae)}</td></tr>)}
          </tbody></table>
        ) : <EmptyBox ico="○" msg="No gate outcomes yet" />}
      </Card>
      <Card title="Blocked winners" sub="Signals the gates rejected that would have worked — the counterfactual check">
        {perf.blocked_winners && perf.blocked_winners.count ? <Note>{perf.blocked_winners.count} blocked winners · blocked win rate {perf.blocked_winners.blocked_win_rate ?? '–'} · {perf.blocked_winners.resolved_blocked} resolved</Note> : <Note>None recorded yet.</Note>}
      </Card>
    </div>
  );
}

function Signals() {
  const d = usePaperGet<any>('/paper/signals').data;
  if (!d) return <Skel n={8} />;
  if (d.__err) return <EmptyBox ico="⚠" msg="Signals unavailable" sub={d.__err} />;
  const rows = d.signals || [];
  return (
    <Card title="Decision log" sub={`${rows.length} most recent signals — every candidate the engine scored, executed or not`} flush>
      {rows.length ? (
        <table><thead><tr><th className="nosort l">When</th><th className="nosort l">Symbol</th><th className="nosort l">Action</th><th className="nosort">Entry</th><th className="nosort">Expected R</th><th className="nosort">Confidence</th><th className="nosort">Executed</th><th className="nosort l">Failed gates</th></tr></thead><tbody>
          {rows.map((s: any, i: number) => {
            const fg = Array.isArray(s.failed_gates) ? s.failed_gates : (() => { try { return JSON.parse(s.failed_gates || '[]'); } catch { return []; } })();
            return (
              <tr key={i}><td className="l dim">{(s.created_at || '').slice(0, 16).replace('T', ' ')}</td><td className="l">{s.symbol ? <SymLink s={s.symbol} /> : '–'}</td>
                <td className="l"><Badge cls={/TRADEABLE|BUY|ENTER/i.test(s.action || '') ? 'b-bull' : /MONITOR/i.test(s.action || '') ? 'b-stale' : 'b-neut'}>{s.action || '–'}</Badge></td>
                <td>{s.entry != null ? num(s.entry, 2) : '–'}</td><td>{s.expected_r != null ? num(s.expected_r, 2) : '–'}</td><td>{s.confidence != null ? num(s.confidence, 2) : '–'}</td>
                <td>{s.executed ? <span className="green">yes</span> : <span className="dim2">no</span>}</td><td className="l wrap" style={{ fontSize: 11, color: 'var(--dim)' }}>{fg.length ? fg.map((g: any) => us(typeof g === 'string' ? g : g.gate || g.name || JSON.stringify(g))).join(', ') : '–'}</td></tr>
            );
          })}
        </tbody></table>
      ) : <EmptyBox ico="○" msg="No signals recorded" />}
    </Card>
  );
}

function OptionsShadow() {
  const d = usePaperGet<any>('/paper/options_shadow').data;
  if (!d) return <Skel n={8} />;
  if (d.__err) return <EmptyBox ico="⚠" msg="Options shadow unavailable" sub={d.__err} />;
  const sm = d.summary || {}; const gr = d.graduation || {}; const rows = d.rows || [];
  const cols = rows.length ? Object.keys(rows[0]).filter((k) => typeof rows[0][k] !== 'object' && !/json$/.test(k)).slice(0, 9) : [];
  return (
    <div className="stack">
      <div className="row2e">
        <Card title="Shadow mode" right={<Badge cls={gr.ready ? 'b-bull' : 'b-stale'}>{gr.ready ? 'READY' : 'NOT READY'}</Badge>}>
          <Kv k="Recorded">{sm.count ?? 0}</Kv><Kv k="Gradeable">{sm.gradeable ?? 0}</Kv><Kv k="Ungradeable">{sm.ungradeable ?? 0}</Kv>
          <Note style={{ marginTop: 8 }}>{gr.note || sm.note || 'Options stay in shadow mode until the graduation checks pass.'}</Note>
        </Card>
        <Card title="Graduation checks">
          {(gr.checks || []).length ? gr.checks.map((c: any, i: number) => <Kv key={i} k={us(c.name)}>{c.passed ? <Badge cls="b-bull">PASS</Badge> : <Badge cls="b-bear">FAIL</Badge>}</Kv>) : <Note>No checks reported.</Note>}
        </Card>
      </div>
      <Card title="Shadow contracts" flush>
        {rows.length ? (
          <div style={{ overflowX: 'auto' }}><table><thead><tr>{cols.map((c, i) => <th key={c} className={`nosort${i ? '' : ' l'}`}>{us(c)}</th>)}</tr></thead><tbody>
            {rows.map((r: any, i: number) => <tr key={i}>{cols.map((c, j) => <td key={c} className={j ? '' : 'l'}>{fmtCell(r[c])}</td>)}</tr>)}
          </tbody></table></div>
        ) : <EmptyBox ico="○" msg="No shadow contracts yet" />}
      </Card>
    </div>
  );
}
