import { useState } from 'react';
import { cls, money, num, us } from '../lib/format';
import { Badge, Kv, Note, Sec } from '../components/ui';
import { Card, TabBtns } from '../components/blocks';

/* Renders any experiment results file with `kind: "arm_comparison"` — a frozen Champion against Challenger arms, replayed on the same
   data with the same rules. The page only DISPLAYS what the experiment script computed; it never recomputes or recommends. */

const R = (n: any) => (n == null ? '–' : (n >= 0 ? '+' : '') + num(n, 2));
const D = (n: any, d = 2) => (n == null ? '–' : (n >= 0 ? '+' : '') + num(n, d));

const METRICS: { key: string; label: string; fmt: (v: any) => string; good?: 'high' | 'low'; delta?: boolean }[] = [
  { key: 'entries', label: 'Entries', fmt: (v) => String(v) },
  { key: 'time_stopped', label: 'Time-stopped', fmt: (v) => String(v) },
  { key: 'realized_R', label: 'Realized R', fmt: R, good: 'high' },
  { key: 'open_mark_R', label: 'Open-mark R', fmt: R },
  { key: 'total_net_R', label: 'Total net R', fmt: R, good: 'high', delta: true },
  { key: 'net_pnl_usd', label: 'Net P&L', fmt: money, good: 'high', delta: true },
  { key: 'max_drawdown_usd', label: 'Max drawdown', fmt: money, good: 'low', delta: true },
  { key: 'mean_utilisation', label: 'Utilisation', fmt: (v) => Math.round(v * 100) + '%' },
  { key: 'mean_sessions_held', label: 'Sessions held (avg)', fmt: (v) => num(v, 1) },
  { key: 'worst_trade_R', label: 'Worst trade', fmt: R },
];

// The API returns JSON with sorted keys, so put the arms back in their natural order: the frozen Champion, then by threshold N.
const armNum = (a: string) => (a === 'CHAMPION' ? -1 : parseInt(a.replace(/\D/g, ''), 10) || 0);
const byArm = (a: string, b: string) => armNum(a) - armNum(b);

export default function ArmComparison({ data }: { data: any }) {
  const [cost, setCost] = useState<'5' | '15' | '30'>('5');
  const [sample, setSample] = useState<'in_sample' | 'all'>('in_sample');
  const arms = Object.keys(data.arms).sort(byArm);
  const challengers = arms.filter((a) => a !== 'CHAMPION');
  const [pick, setPick] = useState<string>(challengers[0]);
  const base = data.arms.CHAMPION[cost][sample];
  const gate = data.promotion_gate || {};
  const read = data.in_sample_reading || {};
  const v = data.validity_gate || {};

  return (
    <div className="stack">
      <div className="row3">
        <Card title="Replay validity" right={<Badge cls={v.passed ? 'b-bull' : 'b-bear'}>{v.passed ? 'PASSED' : 'FAILED'}</Badge>}>
          <Note>The Champion arm must reproduce what the cloud actually did before any comparison means anything.</Note>
          {(v.checks || []).map((c: any, i: number) => <Kv key={i} k={<span style={{ fontSize: 11.5 }}>{c.check}</span>}>{c.passed ? <span className="green">✓</span> : <span className="red">✕</span>}</Kv>)}
        </Card>
        <Card title="Pre-registered reading" right={<Badge cls={read.directionally_supported ? 'b-bull' : 'b-neut'}>{read.directionally_supported ? 'SUPPORTED' : 'NOT SUPPORTED'}</Badge>}>
          <Kv k="Arms meeting every criterion">{read.arms_meeting_criteria ?? 0} of {(read.per_arm || []).length} <span className="dim2">(≥ 3 needed)</span></Kv>
          <Note style={{ marginTop: 6 }}>{read.caveat}</Note>
        </Card>
        <Card title="Promotion gate" right={<Badge cls="b-stale">NOT ELIGIBLE</Badge>}>
          <Kv k="Automatic promotion"><b className="red">OFF</b></Kv>
          {(gate.checks || []).map((c: any, i: number) => <Kv key={i} k={<span style={{ fontSize: 11.5 }}>{c.check}</span>}>{c.passed ? <span className="green">✓</span> : <span className="dim2">✕</span>}</Kv>)}
          <Note style={{ marginTop: 6 }}>{gate.reason}</Note>
        </Card>
      </div>

      <Card title="Champion vs Challengers" sub="same candidates, rules, sizing and costs for every arm — only the exit rule differs"
        right={<div className="flexrow">
          <TabBtns<'5' | '15' | '30'> items={[['5', '5 bps'], ['15', '15 bps'], ['30', '30 bps']]} value={cost} onChange={setCost} />
          <TabBtns<'in_sample' | 'all'> items={[['in_sample', 'In-sample'], ['all', 'All sessions']]} value={sample} onChange={setSample} />
        </div>} flush>
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead><tr><th className="l">Arm</th>{METRICS.map((m) => <th key={m.key} className="nosort">{m.label}</th>)}<th className="nosort">Δ total R</th><th className="nosort">Δ drawdown</th></tr></thead>
            <tbody>
              {arms.map((a) => {
                const m = data.arms[a][cost][sample];
                const d = data.deltas?.[a]?.[cost];
                return (
                  <tr key={a} className="click" style={a === 'CHAMPION' ? { background: 'var(--accent-dim)' } : undefined} onClick={() => a !== 'CHAMPION' && setPick(a)}>
                    <td className="l"><b>{a}</b> {a === 'CHAMPION' && <Badge cls="b-info">FROZEN</Badge>}</td>
                    {METRICS.map((mt) => <td key={mt.key} className={mt.key === 'total_net_R' || mt.key === 'net_pnl_usd' ? cls(m[mt.key]) : ''}>{mt.fmt(m[mt.key])}</td>)}
                    <td className={d ? cls(d.total_net_R) : 'dim2'}>{d ? D(d.total_net_R) : '—'}</td>
                    <td className={d ? cls(-d.max_drawdown_usd) : 'dim2'}>{d ? D(d.max_drawdown_usd) : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <Note style={{ padding: '10px 14px' }}>
          Open positions are <b>marks</b> at the last bar, not outcomes. Baseline ({sample === 'in_sample' ? 'in-sample' : 'all sessions'}): {base.entries} entries, total net R {R(base.total_net_R)}.
          Out-of-sample positions so far: {Object.values<any>(data.out_of_sample || {}).reduce((s, x) => s + (x.positions || 0), 0)}.
        </Note>
      </Card>

      <div className="row2e">
        <Card title="Direction over the threshold N" sub="Δ total net R vs Champion — read as a curve, not a search for the best N">
          <table><thead><tr><th className="l">Arm</th><th className="nosort">5 bps</th><th className="nosort">15 bps</th><th className="nosort">30 bps</th><th className="nosort">LOEO min Δ (5 bps)</th><th className="l nosort">All criteria</th></tr></thead><tbody>
            {challengers.map((a) => {
              const per = (read.per_arm || []).find((x: any) => x.arm === a) || {};
              return (
                <tr key={a}><td className="l"><b>{a}</b></td>
                  {(['5', '15', '30'] as const).map((c) => <td key={c} className={cls(data.deltas[a][c].total_net_R)}>{D(data.deltas[a][c].total_net_R)}</td>)}
                  <td className={cls(data.leave_one_event_out[a]['5'].min_delta_R)} title={`dropping ${data.leave_one_event_out[a]['5'].min_when_dropping_event}`}>{D(data.leave_one_event_out[a]['5'].min_delta_R)}</td>
                  <td className="l">{per.meets_all_criteria ? <Badge cls="b-bull">MET</Badge> : <Badge cls="b-neut">NOT MET</Badge>}</td></tr>
              );
            })}
          </tbody></table>
          <Note style={{ marginTop: 8 }}>LOEO = leave-one-event-out: the smallest Δ when any single setup is removed from both sides. A positive average that turns negative here rests on one trade.</Note>
        </Card>
        <Card title="Out-of-sample" sub={`positions entered after ${data.provenance?.oos_from_exclusive}`}>
          {Object.entries<any>(data.out_of_sample || {}).sort(([a], [b]) => byArm(a, b)).map(([a, o]) => <Kv key={a} k={a}>{o.positions} positions{a !== 'CHAMPION' ? <span className="dim2"> · {o.eligible_for_time_stop} time-stop eligible</span> : null}</Kv>)}
          <Note style={{ marginTop: 8 }}>In-sample results can never promote. Sync a later backup (<code className="cmd" style={{ display: 'inline', padding: '0 5px' }}>python scripts/sync_cloud_data.py</code>) and re-run the experiment to fill this in.</Note>
        </Card>
      </div>

      <Card title="Arm detail" right={<TabBtns<string> items={challengers.map((a) => [a, a] as [string, string])} value={pick} onChange={setPick} />}>
        <div className="row2e">
          <div>
            <Sec>Positions in {pick} ({cost} bps)</Sec>
            <Positions list={data.positions[pick]} />
          </div>
          <div>
            <Sec>Entries made possible by the released slot</Sec>
            <Positions list={data.opportunity_cost[pick]?.entries_only_because_a_slot_was_released} empty="None — no slot was released." />
            {(data.opportunity_cost[pick]?.champion_entries_not_made_in_this_arm || []).length > 0 && (<><Sec>Champion entries this arm did not make</Sec><Positions list={data.opportunity_cost[pick].champion_entries_not_made_in_this_arm} /></>)}
            <Sec>Time-stop regret</Sec>
            {(data.time_stop_regret[pick] || []).length ? (
              <table><thead><tr><th className="l">Symbol</th><th className="nosort">Held at exit</th><th className="nosort">Realized R</th><th className="nosort">If held</th><th className="nosort">Regret</th></tr></thead><tbody>
                {data.time_stop_regret[pick].map((r: any, i: number) => (
                  <tr key={i}><td className="l">{r.symbol}</td><td>{r.sessions_held_at_exit} sessions</td><td className={cls(r.realized_R)}>{R(r.realized_R)}</td>
                    <td className={cls(r.R_if_held_champion_exits)} title={us(r.held_outcome)}>{R(r.R_if_held_champion_exits)} <span className="dim2">{r.held_outcome === 'open' ? 'mark' : us(r.held_outcome)}</span></td>
                    <td className={cls(r.regret_R)}>{D(r.regret_R)}</td></tr>
                ))}
              </tbody></table>
            ) : <Note>No position was time-stopped.</Note>}
            <Note style={{ marginTop: 6 }}>Regret = R if the same entry had been held under Champion exits − R realized. "mark" = still open at the end of the data, so it is not a final outcome.</Note>
          </div>
        </div>
      </Card>
    </div>
  );
}

function Positions({ list, empty }: { list?: any[]; empty?: string }) {
  if (!list || !list.length) return <Note>{empty || 'None.'}</Note>;
  return (
    <table><thead><tr><th className="l">Symbol</th><th className="l">Entered</th><th className="l">State</th><th className="nosort">R</th><th className="nosort">Held</th></tr></thead><tbody>
      {list.map((p, i) => (
        <tr key={i}><td className="l"><b>{p.symbol}</b></td><td className="l dim">{p.entry_session}</td>
          <td className="l"><Badge cls={p.state === 'open' ? 'b-load' : p.state === 'target' ? 'b-bull' : p.state === 'time_stop' ? 'b-stale' : 'b-bear'}>{p.state === 'open' ? 'OPEN (mark)' : us(p.state).toUpperCase()}</Badge>{p.ambiguous ? <Badge cls="b-stale"> AMBIG.</Badge> : null}</td>
          <td className={cls(p.R)}>{R(p.R)}</td><td>{p.sessions_held}s</td></tr>
      ))}
    </tbody></table>
  );
}
