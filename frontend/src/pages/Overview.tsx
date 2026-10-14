import { Link } from 'react-router-dom';
import { cls, money, num, pct } from '../lib/format';
import { useGet, usePaperGet } from '../lib/nav';
import { useWS } from '../lib/store';
import { Badge, EmptyBox, FreshChip, Kv, Note, PRES_CLS, PRES_SHORT, Skel } from '../components/ui';
import { Card, Kpi, LineChart, PageHead, SymLink } from '../components/blocks';
import { useStatus } from '../lib/snapshot';
import { ago, us } from '../lib/format';

export default function Overview() {
  const acct = usePaperGet<any>('/paper/account', { poll: 15000 }).data;
  const perf = usePaperGet<any>('/paper/performance').data;
  const market = useGet<any>('/api/market').data;
  const regime = useGet<any>('/api/regime').data;
  const source = useWS((s) => s.source);
  const cohort = useGet<any>(source === 'local' ? '/api/cohort/today' : null).data;
  const cloudRt = useGet<any>(source === 'cloud' ? '/api/cloud/runtime' : null).data;
  const research = useGet<any>('/api/research/index').data;
  const status = useStatus((s) => s.data) || {};

  const a = acct && !acct.__err ? acct : null;
  const risk = (a?.config || {}).risk || {};
  const ov = (perf && perf.overall) || {};
  const ms = (perf && perf.milestone) || {};
  const curve = ((perf && perf.equity_curve) || []).map((p: any) => ({ x: p.session_date, y: p.equity }));
  const open = a?.open || [];
  const obs = (cohort && cohort.observations) || [];
  const challengers = ((research && research.experiments) || []).filter((e: any) => e.kind === 'challenger');
  const rg = (regime && regime.regime) || {};

  return (
    <>
      <PageHead title="Overview" sub="Account, market backdrop, today's candidates and the state of the strategy research — in one place." />
      <div className="stack">
        <div className="kpis">
          <Kpi l="Equity" value={a?.equity} format={money} sub={a ? `Started at ${money(a.starting_equity)}` : ''} />
          <Kpi l="Realised P&L" value={a?.realized_pnl} format={money} tone={cls(a?.realized_pnl)} sub={`${ov.n ?? 0} closed trades`} />
          <Kpi l="Unrealised P&L" value={a?.unrealized_pnl} format={money} tone={cls(a?.unrealized_pnl)} sub={`${a?.open_positions ?? 0} open · ${num(a?.capital_utilization_pct, 0)}% deployed`} />
          <Kpi l="Drawdown" value={a?.drawdown_pct} format={(n) => num(n, 2) + '%'} tone={a?.drawdown_pct > 0 ? 'amber' : ''} sub={risk.max_drawdown != null ? `Limit ${money(risk.max_drawdown)}` : ''} />
          <Kpi l="Win rate" value={ov.win_rate} format={(n) => (n == null ? '–' : Math.round(n) + '%')} sub={ov.expectancy != null ? `Expectancy ${num(ov.expectancy, 2)}` : 'No resolved trades yet'} />
          <Kpi l="Evidence milestone" value={ms.progress} format={(n) => `${Math.round(n ?? 0)} / ${ms.target ?? 50}`} sub="Resolved trades toward the first review" />
        </div>

        <div className="row2">
          <div className="stack">
            <Card title="Equity curve" sub="Paper account, by session">
              {perf ? <LineChart points={curve} fmt={money} /> : <Skel n={5} />}
            </Card>
            <Card title="Open positions" right={<Link className="btn sm" to="/portfolio">Portfolio</Link>} flush>
              {!acct ? <div style={{ padding: 16 }}><Skel n={3} /></div> : open.length ? (
                <table><thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Now</th><th>P&L</th><th>Stop</th><th>Target</th></tr></thead><tbody>
                  {open.map((p: any, i: number) => (
                    <tr key={i}><td><SymLink s={p.symbol} /></td><td>{num(p.quantity, 4)}</td><td>{num(p.avg_entry, 2)}</td><td>{p.current_price != null ? num(p.current_price, 2) : '–'}</td>
                      <td className={cls(p.unrealized_pnl)}>{p.unrealized_pnl != null ? money(p.unrealized_pnl) : '–'}</td><td className="red">{num(p.stop, 2)}</td><td className="green">{num(p.target, 2)}</td></tr>
                  ))}
                </tbody></table>
              ) : <EmptyBox ico="○" msg="No open positions" sub="The paper account is flat." />}
            </Card>
            {source === 'cloud' ? (
              <Card title="Cloud's latest scan" sub={cloudRt?.state === 'ok' ? `${cloudRt.latest_cycle_id || ''} · every finalist and what the Champion did` : undefined} right={<Link className="btn sm" to="/cloud">Cloud runtime</Link>} flush>
                {!cloudRt ? <div style={{ padding: 16 }}><Skel n={4} /></div> : cloudRt.state !== 'ok' ? <EmptyBox ico="○" msg="No runtime evidence" sub={cloudRt.reason || cloudRt.__err || ''} /> : (cloudRt.latest_candidates || []).length ? (
                  <table><thead><tr><th>#</th><th className="l">Symbol</th><th className="l">Strategy</th><th className="l">Decision</th><th>Exp. R</th><th className="l">Entered</th></tr></thead><tbody>
                    {cloudRt.latest_candidates.slice(0, 8).map((c: any, i: number) => (
                      <tr key={i}><td>{c.scanner_rank ?? '–'}</td><td className="l"><SymLink s={c.symbol} /></td><td className="setup">{us(c.strategy)}</td>
                        <td className="l"><Badge cls={c.decision === 'TRADEABLE' ? 'b-bull' : c.decision === 'MONITOR' ? 'b-stale' : 'b-neut'}>{c.decision || '–'}</Badge></td>
                        <td>{c.expected_r != null ? num(c.expected_r, 2) : '–'}</td><td className="l">{c.champion_executed ? <span className="green">yes</span> : <span className="dim2">no</span>}</td></tr>
                    ))}
                  </tbody></table>
                ) : <EmptyBox ico="○" msg="No candidates in the latest cycle" />}
              </Card>
            ) : (
                <Card title="Today's top candidates" sub={cohort?.scan ? `Scan ${(cohort.scan.scan_id || '').slice(0, 8)} · ${cohort.scan.preset || ''}` : undefined} right={<Link className="btn sm" to="/scanner">Scanner</Link>} flush>
                  {!cohort ? <div style={{ padding: 16 }}><Skel n={4} /></div> : obs.length ? (
                    <table><thead><tr><th>#</th><th className="l">Symbol</th><th>Score</th><th className="l">Setup</th><th>Return since scan</th><th className="l">Status</th></tr></thead><tbody>
                      {obs.map((o: any) => (
                        <tr key={o.symbol}><td>{o.rank}</td><td className="l"><SymLink s={o.symbol} /></td><td>{num(o.score, 1)}</td><td className="setup">{us(o.setup_type)}</td>
                          <td className={cls(o.return_since_scan_pct)}>{o.return_since_scan_pct != null ? pct(o.return_since_scan_pct) : '–'}</td><td className="l"><Badge>{us(o.status)}</Badge></td></tr>
                      ))}
                    </tbody></table>
                  ) : <EmptyBox ico="⊞" msg="No scan captured yet" sub="Run a scan from the Scanner page; its top 5 are tracked here." />}
                </Card>
            )}
          </div>

          <div className="stack">
            <Card title="Market" right={<Link className="btn sm" to="/markets">Details</Link>}>
              {!market ? <Skel n={5} /> : (
                <>
                  <div className="flexrow mb8"><Badge cls={/BULL|RISK-ON/i.test(market.regime || '') ? 'b-bull' : /BEAR|RISK-OFF/i.test(market.regime || '') ? 'b-bear' : 'b-neut'}>{market.regime || '—'}</Badge>
                    {rg.headline && <span className="dim" style={{ fontSize: 12 }}>{rg.headline}</span>}</div>
                  <Kv k="Risk appetite">{market.risk_appetite_score ?? '–'}<span className="dim2"> / 100</span></Kv>
                  <Kv k="VIX">{market.vix ? `${num(market.vix.level, 2)} (${pct(market.vix.change_pct)})` : '–'}</Kv>
                  <Kv k="Sector breadth">{market.sectors_green ?? '–'} green · {market.sectors_red ?? '–'} red</Kv>
                  <Kv k="New longs">{market.new_longs_ok ? <span className="green">OK</span> : <span className="amber">Be selective</span>}</Kv>
                  {market.trade_posture && <Note style={{ marginTop: 8 }}>{market.trade_posture}</Note>}
                </>
              )}
            </Card>
            <Card title="Strategy research" right={<Link className="btn sm" to="/strategies">Strategies</Link>} flush>
              {!research ? <div style={{ padding: 16 }}><Skel n={4} /></div> : (
                <>
                  <div className="list-row" style={{ cursor: 'default' }}><div><div className="t">Champion</div><div className="d">Frozen — the evidence baseline. Never changed silently.</div></div><span className="spacer" /><Badge cls="b-info">IMMUTABLE</Badge></div>
                  {challengers.map((c: any) => (
                    <Link key={c.folder} to={`/testing/${c.folder}`} className="list-row">
                      <div style={{ minWidth: 0 }}><div className="t">{c.id}</div><div className="d">{(c.title || '').replace(/^CH-\d+\s*/, '').slice(0, 70)}</div></div>
                      <span className="spacer" /><Badge cls={/NOT SUPPORTED|UNINFORM/i.test(c.verdict || '') ? 'b-neut' : c.verdict ? 'b-info' : 'b-stale'}>{c.prereg_status || c.status}</Badge>
                    </Link>
                  ))}
                  {!challengers.length && <EmptyBox ico="○" msg="No challengers registered" />}
                </>
              )}
            </Card>
            <Card title="Data & jobs" sub="Freshness, per dataset">
              {(status.datasets || []).map((ds: any, i: number) => {
                const p = ds.presentation || 'UNKNOWN';
                return <Kv key={i} k={ds.label || ds.id}><Badge cls={PRES_CLS[p] || 'b-neut'}>{PRES_SHORT[p] || p}</Badge></Kv>;
              })}
              {(status.jobs || []).map((j: any, i: number) => (
                <Kv key={'j' + i} k={j.label}>{j.running ? <Badge cls="b-load">running</Badge> : j.stalled ? <Badge cls="b-stale">stalled {ago(j.age_seconds)}</Badge> : j.idle ? <Badge cls="b-neut">idle {ago(j.age_seconds)}</Badge> : <Badge cls="b-fresh">ok</Badge>}</Kv>
              ))}
              {!(status.datasets || []).length && !(status.jobs || []).length && <Skel n={3} />}
              <div className="flexrow mt8"><FreshChip sec={null} /><Link className="btn sm" to="/system">System health</Link></div>
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
