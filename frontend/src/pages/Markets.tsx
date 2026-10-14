import { cls, num, pct, us } from '../lib/format';
import { useGet } from '../lib/nav';
import { Badge, EmptyBox, FreshChip, Kv, Note, Skel } from '../components/ui';
import { Card, PageHead, SymLink } from '../components/blocks';

export default function Markets() {
  const market = useGet<any>('/api/market').data;
  const regime = useGet<any>('/api/regime').data;
  const sectors = useGet<any>('/api/sectors').data;
  const rg = (regime && regime.regime) || {};
  const rows = ((sectors && sectors.sectors) || []).slice().sort((a: any, b: any) => (b.momentum_pct ?? -1e9) - (a.momentum_pct ?? -1e9));
  const bench = (sectors && sectors.benchmark) || {};
  const maxAbs = Math.max(1, ...rows.map((r: any) => Math.abs(r.momentum_pct || 0)));

  return (
    <>
      <PageHead title="Markets" sub="The backdrop the Champion trades into: regime, volatility, and which sectors are leading." />
      <div className="stack">
        <div className="row2e">
          <Card title="Regime">
            {!regime ? <Skel n={6} /> : regime.__err ? <EmptyBox ico="⚠" msg="Regime unavailable" sub={regime.__err} /> : (
              <>
                <div className="flexrow mb8"><Badge cls="b-info">{(rg.risk_appetite || '—').toUpperCase()}</Badge><Badge>{rg.structure || '—'}</Badge><Badge>{rg.volatility_regime || '—'}</Badge>{rg.event_driven && <Badge cls="b-stale">EVENT-DRIVEN</Badge>}</div>
                <Kv k="Headline">{rg.headline || '—'}</Kv>
                <Kv k="Risk score">{num(rg.risk_score, 2)}</Kv>
                <Kv k="Data coverage">{rg.data_coverage != null ? Math.round(rg.data_coverage * 100) + '%' : '–'}</Kv>
                {rg.confidence_note && <Note style={{ marginTop: 8 }}>{rg.confidence_note}</Note>}
              </>
            )}
          </Card>
          <Card title="Whole-market read" sub="Yahoo" right={market ? <FreshChip sec={market.freshness ? { state: 'ok', freshness: market.freshness } : null} /> : null}>
            {!market ? <Skel n={6} /> : (
              <>
                <Kv k="Regime"><Badge>{market.regime || '—'}</Badge></Kv>
                <Kv k="Risk appetite">{market.risk_appetite_score ?? '–'} / 100</Kv>
                <Kv k="VIX">{market.vix ? `${num(market.vix.level, 2)} · ${pct(market.vix.change_pct)}` : '–'}</Kv>
                <Kv k="Avg index change"><span className={cls(market.avg_index_change_pct)}>{pct(market.avg_index_change_pct)}</span></Kv>
                <Kv k="Sector breadth">{market.sector_breadth_pct != null ? num(market.sector_breadth_pct, 1) + '% advancing' : '–'}</Kv>
                {market.trade_posture && <Note style={{ marginTop: 8 }}>{market.trade_posture}</Note>}
                {(market.warnings || []).map((w: string, i: number) => <Note key={i} warn>{w}</Note>)}
              </>
            )}
          </Card>
        </div>

        <Card title="Sectors" sub={bench.symbol ? `vs ${bench.symbol}: 1d ${pct(bench.perf_1d)} · 5d ${pct(bench.perf_5d)} · 1m ${pct(bench.perf_1m)}` : undefined} flush>
          {!sectors ? <div style={{ padding: 16 }}><Skel n={8} /></div> : sectors.__err || !rows.length ? <EmptyBox ico="○" msg="Sector data unavailable" sub={sectors.reason || sectors.__err || ''} /> : (
            <table><thead><tr><th>Sector</th><th className="l">ETF</th><th>Momentum</th><th className="l" style={{ width: '24%' }} />
              <th>Breadth (up / counted)</th><th>Candidates</th><th>Cap weight</th></tr></thead><tbody>
              {rows.map((r: any) => (
                <tr key={r.key}>
                  <td>{r.name}</td><td className="l"><SymLink s={r.etf} /></td>
                  <td className={cls(r.momentum_pct)}>{r.momentum_pct != null ? pct(r.momentum_pct) : '–'}</td>
                  <td className="l"><div className="hbar" style={{ marginTop: 0 }}><i style={{ width: Math.min(100, (Math.abs(r.momentum_pct || 0) / maxAbs) * 100) + '%', background: (r.momentum_pct || 0) >= 0 ? 'var(--green)' : 'var(--red)' }} /></div></td>
                  <td>{r.breadth && r.breadth.counted ? `${r.breadth.advancers} / ${r.breadth.counted} up` : '–'}</td>
                  <td>{r.opportunity_count ?? '–'}</td><td>{r.cap_weight != null ? num(r.cap_weight * (r.cap_weight <= 1 ? 100 : 1), 1) + '%' : '–'}</td>
                </tr>
              ))}
            </tbody></table>
          )}
        </Card>

        <div className="row2e">
          <Card title="Regime evidence" flush>
            {!(rg.evidence || []).length ? <EmptyBox ico="○" msg="No evidence" /> : (
              <table><thead><tr><th>Factor</th><th className="l">Symbol</th><th className="l">Observation</th><th>Value</th></tr></thead><tbody>
                {rg.evidence.map((e: any, i: number) => <tr key={i}><td>{us(e.factor)}</td><td className="l">{e.symbol ? <SymLink s={e.symbol} /> : '–'}</td><td className="setup">{us(e.observation)}</td><td>{typeof e.value === 'number' ? num(e.value, 2) : String(e.value ?? '–')}</td></tr>)}
              </tbody></table>
            )}
          </Card>
          <Card title="Leaders and laggards" flush>
            {!market ? <div style={{ padding: 16 }}><Skel n={4} /></div> : (
              <table><thead><tr><th>Sector</th><th className="l">ETF</th><th>Change</th></tr></thead><tbody>
                {(market.leaders || []).map((r: any, i: number) => <tr key={'l' + i}><td>{r.sector}</td><td className="l"><SymLink s={r.etf} /></td><td className="green">{pct(r.change_pct)}</td></tr>)}
                {(market.laggards || []).map((r: any, i: number) => <tr key={'g' + i}><td>{r.sector}</td><td className="l"><SymLink s={r.etf} /></td><td className="red">{pct(r.change_pct)}</td></tr>)}
              </tbody></table>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
