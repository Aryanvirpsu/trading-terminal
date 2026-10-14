import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import { money, us } from '../lib/format';
import { useGet, usePaperGet } from '../lib/nav';
import { Badge, EmptyBox, Kv, Note, Sec, Skel } from '../components/ui';
import { Card, PageHead } from '../components/blocks';

const verdictCls = (v?: string | null) => (!v ? 'b-stale' : /NOT SUPPORTED|UNINFORM|REJECT/i.test(v) ? 'b-neut' : /SUPPORT|CONSISTENT/i.test(v) ? 'b-info' : 'b-stale');
const MONEY_KEYS = /(loss|drawdown|notional|premium|cash|equity)/i;
const fmtVal = (k: string, v: any) => (typeof v === 'number' && MONEY_KEYS.test(k) && !/cooldown|pct|days|count|positions|entries|ratio|threshold|bps|per_day|per_sector|fee_/i.test(k) ? money(v) : typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v));

export default function Strategies() {
  const research = useGet<any>('/api/research/index').data;
  const acct = usePaperGet<any>('/paper/account').data;
  const sig = usePaperGet<any>('/paper/signals').data;
  const wl = useGet<any>('/api/research/watchlist').data;
  const cfg = (acct && acct.config) || {};
  const latest = ((sig && sig.signals) || [])[0] || {};
  const exps: any[] = (research && research.experiments) || [];
  const challengers = exps.filter((e) => e.kind === 'challenger');
  const evidence = exps.filter((e) => e.kind === 'baseline' || e.kind === 'review');

  return (
    <>
      <PageHead title="Strategies" sub="The Champion is frozen. Every idea runs as an isolated Challenger and is promoted, killed or archived on pre-registered evidence — never pooled with another version." />
      <div className="stack">
        <Card title="Champion" right={<Badge cls="b-info">IMMUTABLE</Badge>} sub="The current strategy, as it trades the paper account">
          {!acct ? <Skel n={5} /> : (
            <div className="row3">
              <div>
                <Sec>Version</Sec>
                <Kv k="Decision engine">{latest.engine_version || '–'}</Kv>
                <Kv k="Config version">{latest.config_version || '–'}</Kv>
                <Kv k="Account">{money(acct.starting_equity)} cash · no margin</Kv>
                <Note style={{ marginTop: 8 }}>Logic, thresholds, weights, gates, exits, sizing and the execution model are not changed in place. Experiments go through a Challenger.</Note>
              </div>
              <div>
                <Sec>Risk envelope</Sec>
                {Object.entries(cfg.risk || {}).map(([k, v]) => <Kv key={k} k={us(k)}>{fmtVal(k, v)}</Kv>)}
              </div>
              <div>
                <Sec>Execution assumptions</Sec>
                {Object.entries(cfg.execution || {}).map(([k, v]) => <Kv key={k} k={us(k)}>{fmtVal(k, v)}</Kv>)}
              </div>
            </div>
          )}
        </Card>

        <Card title="Challengers" sub="Each changes one variable and is pre-registered before its result is computed" flush>
          {!research ? <div style={{ padding: 16 }}><Skel n={4} /></div> : challengers.length ? (
            challengers.map((c) => (
              <Link key={c.folder} to={`/testing/${c.folder}`} className="list-row">
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="t"><span className="mono">{c.id}</span> <span className="dim" style={{ fontWeight: 400 }}>{(c.title || '').replace(/^CH-\d+\s*/, '').replace(/^[—–-]\s*/, '')}</span></div>
                  <div className="d">{c.verdict || 'No verdict recorded'}</div>
                </div>
                <Badge cls="b-neut">{c.status}</Badge>
                <Badge cls={verdictCls(c.verdict)}>{c.prereg_status || '—'}</Badge>
              </Link>
            ))
          ) : <EmptyBox ico="○" msg="No challengers registered" />}
        </Card>

        <div className="row2e">
          <Card title="Baseline and reviews" sub="The evidence the Champion is judged on" flush>
            {evidence.length ? evidence.map((e) => (
              <Link key={e.folder} to={`/testing/${e.folder}`} className="list-row"><div style={{ minWidth: 0 }}><div className="t">{e.title}</div><div className="d">{e.folder} · {e.files.length} files</div></div></Link>
            )) : <EmptyBox ico="○" msg="None yet" />}
          </Card>
          <Card title="How a Challenger is judged">
            <ul className="rl" style={{ marginTop: 0 }}>
              <li>Replay and walk-forward, labelled in-sample or out-of-sample.</li>
              <li>Metrics net of spread, slippage and fees, in R terms.</li>
              <li>Counterfactuals use the whole candidate population, accepted and rejected.</li>
              <li>Drawdown, worst trade, streaks and tail losses are always reported.</li>
              <li>No fill, price, cost or timestamp is ever made more favourable to improve a result.</li>
            </ul>
          </Card>
        </div>

        <Watchlist wl={wl} />
      </div>
    </>
  );
}

function Watchlist({ wl }: { wl: any }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!wl) return <Card title="Strategy watchlist"><Skel n={5} /></Card>;
  const reg = wl.registry; const sources: any[] = (reg && reg.sources) || [];
  const mon = (reg && reg.monitor) || {}; const acc = (reg && reg.account_constraints) || {};
  return (
    <Card title="Strategy watchlist" sub="External free strategies under observation — research only, not deployed" flush
      right={mon.cadence ? <span className="dim2" style={{ fontSize: 11.5 }}>Reviewed {mon.cadence} · {mon.preferred_schedule}</span> : null}>
      {!sources.length ? <EmptyBox ico="○" msg="No watchlist entries" /> : (
        <table><thead><tr><th className="l">Strategy</th><th className="l">Source</th><th className="l">Evidence</th><th className="l">Status</th><th className="nosort" /></tr></thead><tbody>
          {sources.map((s) => (
            <Fragment key={s.id}>
              <tr className="click" onClick={() => setOpen(open === s.id ? null : s.id)}>
                <td className="l" style={{ fontFamily: 'var(--sans)', fontWeight: 600 }}>{s.name}</td><td className="l setup">{s.provider}</td>
                <td className="l wrap setup" style={{ maxWidth: 280 }}>{s.evidence_type}</td><td className="l"><Badge cls={/EXCLUDED/.test(s.status) ? 'b-neut' : /PENDING/.test(s.status) ? 'b-stale' : 'b-info'}>{us(s.status)}</Badge></td>
                <td className="dim2">{open === s.id ? '▾' : '▸'}</td>
              </tr>
              {open === s.id && (
                <tr><td colSpan={5} className="wrap" style={{ background: 'var(--bg2)', textAlign: 'left', padding: '14px 18px' }}>
                  <div className="row3">
                    <div><Sec>Worth borrowing</Sec><ul className="rl">{(s.borrow || []).map((b: string, i: number) => <li key={i} className="pos">{b}</li>)}</ul></div>
                    <div><Sec>Issues for a $500 account</Sec><ul className="rl">{(s.issues || []).map((b: string, i: number) => <li key={i} className="neg">{b}</li>)}</ul></div>
                    <div><Sec>Fit</Sec><Note>{s.fit}</Note>{s.url && <p style={{ marginTop: 8 }}><a className="accent" href={s.url} target="_blank" rel="noopener">Open source ↗</a></p>}
                      <Note style={{ marginTop: 6 }}>v{s.version} · published {s.published_date}</Note></div>
                  </div>
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody></table>
      )}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--line)' }}>
        <Note>Account limits every candidate must fit: ${acc.starting_cash_usd} cash · ${acc.reserve_usd} reserve · ${acc.max_position_usd} max position · ${acc.max_planned_loss_usd} max planned loss · {acc.max_positions} positions · no margin, shorting or options execution.
          {' '}<Link to="/testing/strategy_watchlist" style={{ color: 'var(--accent)' }}>Read the source review →</Link></Note>
      </div>
    </Card>
  );
}
