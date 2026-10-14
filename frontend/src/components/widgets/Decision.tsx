import { type ReactNode } from 'react';
import { cls as colorCls, kmb, money, num, shortReason, us } from '../../lib/format';
import { useWS } from '../../lib/store';
import { Snap, useSec } from '../../lib/snapshot';
import { Badge, EmptyBox, Kv, Note, Sec, sectionBox } from '../ui';
import { Catalysts, Headline, SENT_CLS, DIR_CLS } from './News';

// Which snapshot section each tab reads, so a tab reports its OWN state instead of the widget
// blocking on its slowest dependency.
const DEC_TAB_SECTION: Record<string, string> = { overview: 'decision', thesis: 'decision', history: 'history', news: 'news', risk: 'risk', option: 'options' };
const TAB_LABEL: Record<string, string> = { overview: 'decision', thesis: 'decision', history: 'historical validation', news: 'news', risk: 'account', option: 'option chain' };
const TH_CLS: Record<string, string> = {
  STRONG: 'green', SUPPORTIVE: 'green', BULLISH: 'green', POSITIVE: 'green', GOOD: 'green', PASS: 'green',
  MODERATE: 'accent', MIXED: 'amber', NEUTRAL: 'dim', FAIR: 'amber',
  WEAK: 'red', HOSTILE: 'red', BEARISH: 'red', NEGATIVE: 'red', POOR: 'red', FAIL: 'red',
  INSUFFICIENT: 'dim2', 'NO DATA': 'dim2', NONE: 'dim2',
};
const sgn = (n: any, d = 2) => (n >= 0 ? '+' : '') + num(n, d);

/** The persistent verdict block: decision, scores, levels, sizing and the four risk numbers. */
export function DecisionHeader() {
  const sec = useSec('decision');
  const d = sec && sec.state === 'ok' ? sec.data || {} : null;
  return <Header d={d} sec={sec} />;
}

/** One evidence pane (overview | thesis | history | news | risk | option) with its own loading state. */
export function DecisionPane({ tab }: { tab: string }) {
  const sec = useSec('decision');
  const d = sec && sec.state === 'ok' ? sec.data || {} : null;
  return <Pane tab={tab} d={d} />;
}

function Pane({ tab, d }: { tab: string; d: any }) {
  const sec = useSec(DEC_TAB_SECTION[tab]);
  const box = sectionBox(sec, TAB_LABEL[tab]);
  if (box) return <>{box}</>;
  const P: any = { overview: Overview, thesis: Thesis, history: History, news: NewsTab, risk: RiskTab, option: OptionTab }[tab] || Overview;
  return <P d={d} />;
}

/* ── persistent header: may not invent a verdict it does not have ── */
function Header({ d, sec }: { d: any; sec: any }) {
  const symbol = useWS((s) => s.symbol);
  const levelsS = useSec('levels');
  if (!d) {
    const st = sec && sec.state;
    const msg = st === 'loading' || !sec ? 'computing decision…' : (sec && sec.reason) || 'decision unavailable';
    return <div className="verdict v-no"><div className="vl dim">{st === 'loading' || !sec ? '◌' : '—'}</div><div className="vi dim2">{msg}</div></div>;
  }
  if (d.gated) {
    return (
      <div className="verdict v-stale">
        <div className="vl">STALE</div><div className="vi red">REFRESH REQUIRED</div>
        <div className="vr">A verdict is only as current as the oldest input behind it: {(d.gate_reason || []).join('; ')}</div>
        <button className="pill-btn mt6" onClick={() => Snap.refresh()}>⟳ Refresh {symbol}</button>
        <Note style={{ marginTop: 5 }}>Withheld verdict was <b>{d.instrument_withheld || '—'}</b> — shown so you know what changed, not as guidance.</Note>
      </div>
    );
  }
  const label = String(d.decision || '').toUpperCase();
  const vcls = label === 'TRADEABLE' ? 'v-go' : label === 'MONITOR' ? 'v-watch' : 'v-no';
  const inst = d.instrument || '—';
  const icls = inst === 'OPTION PREFERRED' ? 'amber' : inst === 'STOCK PREFERRED' ? 'accent' : 'dim';
  const lv = d.levels && d.levels.state === 'ok' ? d.levels : (levelsS || {}).data || {};
  const sz = d.sizing || {};
  const chosen = inst === 'OPTION PREFERRED' ? sz.option || {} : sz.shares || {};
  const rk = chosen.risk || {};
  const scale = d.score_scale || {};
  return (
    <>
      <div className={`verdict ${vcls}`}>
        <div className="vl">{label || '—'}</div>
        <div className={`vi ${icls}`}>{inst}</div>
        <div className="flexrow" style={{ marginTop: 7 }}>
          <Badge cls="b-neut" title={(d.label_reason || '') + ' — structural + context, exactly.'}>Decision {num(d.decision_score, 1)}<span className="dim2">/{num(scale.max, 0)}</span></Badge>
          <Badge cls="b-neut" title="The setup itself. News and history cannot substitute for this.">Structure {num(d.structural_score, 1)}<span className="dim2">/{num(d.structural_max, 0)}</span></Badge>
          <Badge cls={(d.context_score || 0) >= 0 ? 'b-fresh' : 'b-stale'} title="News + catalysts + history. Bounded — it adjusts confidence, it cannot rescue a bad setup.">Context {d.context_score >= 0 ? '+' : ''}{num(d.context_score, 1)}</Badge>
          {d.setup_score != null && <Badge cls="b-neut" title="The SCANNER's own funnel ranking, 0-100 — a different quantity on a different scale.">Setup {num(d.setup_score, 1)}<span className="dim2">/100</span></Badge>}
        </div>
      </div>
      {lv.state === 'ok' ? (
        <div className="g2">
          <Kv k="Entry">{lv.entry_zone ? num(lv.entry_zone[0], 2) + '–' + num(lv.entry_zone[1], 2) : num(lv.reference_price, 2)}</Kv>
          <Kv k="Invalidation"><span className="red">{num(lv.invalidation, 2)}</span></Kv>
          <Kv k="Target 1"><span className="green">{num(lv.target_1, 2)}</span></Kv>
          <Kv k="Target 2"><span className="green">{num(lv.target_2, 2)}</span></Kv>
          <Kv k="R:R">{num(lv.rr_target_1, 2)}:1</Kv>
          <Kv k="Position size">{chosen.quantity != null ? chosen.quantity + (inst === 'OPTION PREFERRED' ? ' ctr' : ' sh') : '—'}</Kv>
        </div>
      ) : <Note>{lv.reason || 'No tradeable levels — a stop cannot be placed objectively.'}</Note>}
      <div className="riskgrid">
        <div className="rc"><div className="l">Capital</div><div className="v">{money(rk.capital_committed)}</div></div>
        <div className="rc"><div className="l">Planned</div><div className="v amber">{money(rk.planned_risk)}</div></div>
        <div className="rc"><div className="l">Stress</div><div className="v amber">{money(rk.stress_risk)}</div></div>
        <div className="rc"><div className="l">Max loss</div><div className="v red">{money(rk.absolute_max_loss)}</div></div>
      </div>
    </>
  );
}

/* ── OVERVIEW: the fastest read ── */
function Overview({ d }: { d: any }) {
  const th = (useSec('thesis') || {}).data || {};
  const rows = th.rows || [];
  if (!d) return <EmptyBox ico="◌" msg="Decision computing…" />;
  return (
    <>
      {rows.length ? <div className="thesis">{rows.map((r: any, i: number) => <div className="trow" key={i}><span className="k">{r.row}</span><span className={`v ${TH_CLS[r.value] || 'dim'}`}>{r.value}</span></div>)}</div> : <Note>thesis pending</Note>}
      {(d.supports || []).length > 0 && <><Sec>Why this setup</Sec><ul className="rl">{d.supports.slice(0, 3).map((x: string, i: number) => <li key={i} className="pos">{shortReason(x)}</li>)}</ul></>}
      {(d.contradicts || []).length > 0 && <><Sec>Watch</Sec><ul className="rl">{d.contradicts.slice(0, 3).map((x: string, i: number) => {
        const hard = /stale|invalid|liquidity|risk policy/i.test(x); const long = String(x).length > 110;
        return <li key={i} className={hard ? 'blk' : 'neg'}>{shortReason(x)}{long && <span className="dim2"> (Risk tab)</span>}</li>;
      })}</ul></>}
      {(d.hard_failures || []).length > 0 && <><Sec>Hard failures</Sec>
        <ul className="rl">{d.hard_failures.map((h: any, i: number) => <li key={i} className="blk"><b>{us(h.kind)}</b> — {shortReason(h.detail)}</li>)}</ul>
        <Note>A hard rule is not outvoted by news, catalysts or history.</Note></>}
    </>
  );
}

/* ── THESIS: why the setup exists ── */
function Thesis({ d }: { d: any }) {
  const lvS = (useSec('levels') || {}).data || {};
  if (!d) return <EmptyBox ico="◌" msg="Decision computing…" />;
  const lv = d.levels && d.levels.state === 'ok' ? d.levels : lvS;
  const setups = d.setups || [];
  const comp = (n: string) => (d.components || []).find((c: any) => c.component === n) || {};
  const line = (k: string, c: any): ReactNode => c.note ? (
    <>
      <Kv k={k} bare><span className={c.score > 0 ? 'green' : c.score < 0 ? 'red' : 'dim'}>{c.score > 0 ? '+' : ''}{num(c.score, 1)}</span></Kv>
      <div className="note" style={{ margin: '0 0 6px' }}>{c.note}</div>
    </>
  ) : null;
  return (
    <>
      <Sec>Structure</Sec>
      {line('Technical', comp('technical_quality'))}{line('Setup quality', comp('setup_quality'))}{line('Market regime', comp('market_regime'))}{line('Execution', comp('execution_quality'))}
      <Sec>Scanner strategy</Sec>
      {setups.length ? setups.map((s: any, i: number) => (
        <div key={i}><Kv k={us(s.type)} bare>{num(s.strength, 0)}<span className="dim2"> · {s.horizon || ''}</span></Kv><div className="note" style={{ margin: '0 0 6px' }}>{s.evidence || ''}</div></div>
      )) : <Note>No setup category matched — the evidence for every pattern was absent.</Note>}
      <Sec>Sector context</Sec>
      <Note>{lvS.sector || 'sector unknown'}{lvS.sector_rank ? ` — ranked #${lvS.sector_rank}` : ' — no rank available, so it scores no credit'}{lvS.context && lvS.context.note ? <><br />{lvS.context.note}</> : null}</Note>
      <Sec>Invalidation logic</Sec>
      <Note>{d.invalidation && d.invalidation.note ? d.invalidation.note : 'no objective invalidation level'}{lv.invalidation_basis ? <> — placed at the <b>{lv.invalidation_basis}</b>, so the stop is a level the chart defines rather than a round number.</> : null}</Note>
      <Sec>Target rationale</Sec>
      {lv.state === 'ok' ? (
        <Note>Target 1: {lv.target_1_basis || '—'}.<br />Target 2: {lv.target_2_basis || '—'}.<br />
          Targets are measured moves off the 20-day range, never multiples of the stop — so the {num(lv.rr_target_1, 2)}:1 varies with this chart instead of being a constant.
          {lv.note && <><br /><b>{lv.note}</b></>}{lv.do_not_chase_above != null && <><br />Do not chase above {num(lv.do_not_chase_above, 2)}.</>}</Note>
      ) : <Note>{lv.reason || 'no levels'}</Note>}
      {d.candidate_origin === 'on_demand' && d.funnel && (d.funnel.failed_gates || []).length > 0 && (
        <><Sec>Scan gates this name fails</Sec><ul className="rl">{d.funnel.failed_gates.map((g: string, i: number) => <li key={i} className="neg">{us(g)}</li>)}</ul><Note>{d.funnel.note || ''}</Note></>
      )}
      <Note style={{ marginTop: 8 }}>{d.context_cannot_override || ''}</Note>
    </>
  );
}

/* ── HISTORY: the full record, no dropdown ── */
function History() {
  const h = (useSec('history') || {}).data || {};
  if (!h.classification) return <EmptyBox ico="○" msg="No historical validation" />;
  const s = h.stats || {}, w = h.window || {}; const c0 = h.classification;
  const c = /STRONG/.test(c0) ? 'b-bull' : /WEAK/.test(c0) ? 'b-bear' : /INSUFFICIENT|NO HISTORY/.test(c0) ? 'b-neut' : 'b-stale';
  if (/INSUFFICIENT|NO HISTORY/.test(c0)) return (
    <>
      <div className="flexrow"><Badge cls={c}>{c0}</Badge><span className="dim2" style={{ fontSize: 10 }}>{h.sample_size || 0} comparable setup(s)</span></div>
      <Note style={{ marginTop: 5 }}>{h.classification_reason || ''}</Note>
      <Note warn style={{ marginTop: 8 }}>{h.options_note || ''}</Note><Note warn style={{ marginTop: 5 }}>{h.news_note || ''}</Note>
    </>
  );
  const r5 = s.ret_5d || {}, r10 = s.ret_10d || {}, r20 = s.ret_20d || {};
  return (
    <>
      <div className="flexrow mb6">
        <Badge cls={c}>{c0}</Badge>
        {h.fit_pct != null && <Badge cls="b-neut" title="average similarity of the matched setups to today's">{h.fit_pct}% similarity</Badge>}
        <span className="dim2" style={{ fontSize: 10 }}>{h.sample_size} of {h.total_instances} instances</span>
      </div>
      <Note style={{ marginBottom: 6 }}>Strategy <b>{us(h.strategy || '—')}</b> · {w.first || '?'} → {w.last || '?'} · {h.horizon_days}-day horizon · above the {Math.round((h.similarity_floor || 0) * 100)}% similarity floor</Note>
      <div className="g2">
        <Kv k="T1 hit rate"><span className="green">{num(s.t1_hit_rate, 0)}%</span></Kv><Kv k="T2 hit rate"><span className="green">{num(s.t2_hit_rate, 0)}%</span></Kv>
        <Kv k="Stop rate"><span className="red">{num(s.stop_hit_rate, 0)}%</span></Kv><Kv k="Win rate">{num(s.win_rate, 0)}%</Kv>
        <Kv k="Profit factor">{s.profit_factor == null ? '—' : num(s.profit_factor, 2)}</Kv><Kv k="Expectancy"><span className={colorCls(s.expectancy_pct)}>{num(s.expectancy_pct, 2)}%</span></Kv>
        <Kv k="Expectancy (R)">{num(s.expectancy_r, 2)}</Kv><Kv k="Avg holding time">{num(s.avg_hold_days, 1)}d</Kv>
        <Kv k="Avg MFE"><span className="green">+{num(s.avg_mfe_pct, 1)}%</span></Kv><Kv k="Avg MAE"><span className="red">{num(s.avg_mae_pct, 1)}%</span></Kv>
        <Kv k="Max loss"><span className="red">{num(s.max_loss_pct, 1)}%</span></Kv><Kv k="Max losing streak">{s.max_losing_streak ?? '—'}</Kv>
      </div>
      <Sec>Median forward return</Sec>
      <table><thead><tr><th className="nosort">Horizon</th><th className="nosort">Median</th><th className="nosort">Avg</th><th className="nosort">Positive</th></tr></thead><tbody>
        {([['5d', r5], ['10d', r10], ['20d', r20]] as [string, any][]).map(([k, r]) => (
          <tr key={k}><td>{k}</td><td className={colorCls(r.median)}>{num(r.median, 2)}%</td><td className={colorCls(r.avg)}>{num(r.avg, 2)}%</td><td>{num(r.positive_pct, 0)}%</td></tr>
        ))}
      </tbody></table>
      {s.exit_mix && <Note style={{ marginTop: 5 }}>Exits — target 1: {s.exit_mix.target_1 || 0} · target 2: {s.exit_mix.target_2 || 0} · stop: {s.exit_mix.stop || 0} · horizon: {s.exit_mix.horizon || 0}</Note>}
      <Sec>Closest matches</Sec>
      <table><thead><tr><th className="nosort">Date</th><th className="nosort">Sim</th><th className="nosort">Regime</th><th className="nosort">Ret</th><th className="nosort">Exit</th><th className="nosort">Held</th></tr></thead><tbody>
        {(h.examples || []).slice(0, 8).map((e: any, i: number) => (
          <tr key={i}><td>{e.date}</td><td>{e.similarity != null ? Math.round(e.similarity * 100) + '%' : '—'}</td><td className="l" style={{ fontSize: 10 }}>{(e.regime || {}).trend || '?'}</td>
            <td className={colorCls(e.return_pct)}>{num(e.return_pct, 1)}%</td><td className="l" style={{ fontSize: 10 }}>{e.exit}</td><td>{e.held_days}d</td></tr>
        ))}
        {!(h.examples || []).length && <tr><td colSpan={6} className="dim">none</td></tr>}
      </tbody></table>
      <Sec>Limits</Sec>
      <div className="flexrow"><Badge cls="b-stale">HISTORICAL OPTION EXECUTION · NOT VALIDATED</Badge></div>
      <Note style={{ marginTop: 5 }}>{h.options_note || ''}</Note><Note style={{ marginTop: 5 }}>{h.news_note || ''}</Note><Note style={{ marginTop: 5 }}>{h.methodology || ''}</Note>
    </>
  );
}

/* ── NEWS: only what feeds the decision ── */
function NewsTab() {
  const n = (useSec('news') || {}).data || {};
  const cd = (useSec('catalysts') || {}).data || {};
  const cats = cd.catalysts || [], earn = cd.earnings;
  const div = n.source_diversity || {}, nf = n.news_freshness || {}, c = n.counts || {};
  // DIRECT first — those are what actually moved the number.
  const items = (n.items || []).filter((i: any) => i.tier === 'DIRECT' || i.tier === 'INDIRECT_SECTOR').slice(0, 6);
  return (
    <>
      <div className="flexrow mb4">
        <Badge cls={SENT_CLS[n.direction] || 'b-neut'}>{(n.direction || 'no data').toUpperCase()}</Badge>
        <b className={`mono ${DIR_CLS[n.direction] || 'dim'}`} style={{ fontSize: 14 }}>{n.sentiment_score != null ? sgn(n.sentiment_score) : '—'}</b>
        <span className="spacer" /><span className="dim2" style={{ fontSize: 9.5 }}>confidence {n.confidence != null ? Math.round(n.confidence * 100) + '%' : '—'}</span>
      </div>
      <div className="g2">
        <Kv k="Relevant articles">{n.relevant_count ?? 0} <span className="dim2">of {n.article_count ?? 0}</span></Kv>
        <Kv k="Directly about company">{n.direct_count ?? 0}</Kv>
        <Kv k="Source diversity">{div.sources ?? '—'} <span className="dim2">outlets</span></Kv>
        <Kv k="Newest story">{nf.newest_hours != null ? (nf.newest_hours < 1 ? Math.round(nf.newest_hours * 60) + 'm' : num(nf.newest_hours, 1) + 'h') : '—'}</Kv>
        <Kv k="Catalyst score"><span className={colorCls(cd.catalyst_score)}>{cd.catalyst_score != null ? sgn(cd.catalyst_score) : '—'}</span></Kv>
        <Kv k="Split"><span className="green">{c.positive || 0}</span> / <span className="dim">{c.neutral || 0}</span> / <span className="red">{c.negative || 0}</span></Kv>
      </div>
      {n.reason && <Note warn style={{ marginTop: 5 }}>{n.reason}</Note>}
      <Sec>Catalysts <span className="dim2" style={{ textTransform: 'none', letterSpacing: 0 }}>— events, scored separately from tone</span></Sec>
      <Catalysts cd={cd} earn={earn} cats={cats} trailing="No identifiable event — drift, not a catalyst." />
      <Sec>Stories feeding the score</Sec>
      {items.length ? items.map((i: any, k: number) => <Headline key={k} i={i} />) : <Note>No directly relevant coverage — market-wide items are excluded from the score.</Note>}
      <Note style={{ marginTop: 8 }}>Only DIRECT and sector items move the number; market-wide roundups are context. The full feed is in the News widget.</Note>
    </>
  );
}

/* ── RISK: the instrument-aware model, and the long rejection reason ── */
function RiskTab({ d }: { d: any }) {
  const rsec = (useSec('risk') || {}).data || {};
  const decData = (useSec('decision') || {}).data;
  const dd = d || decData || {};
  const sz = dd.sizing || {};
  const sh = (sz.shares || {}).risk || {}, op = (sz.option || {}).risk || {};
  const ps = dd.portfolio_suitability || {}; const elig = ps.eligibility || {};
  const pa = rsec.paper || {};
  const profile = ps.policy_profile || (sz.option || {}).policy_profile;
  const failing = (elig.checks || []).find((x: any) => !x.pass);
  return (
    <>
      {profile && <div className="flexrow mb6"><Badge cls="b-info">RISK PROFILE · {String(profile).toUpperCase()}</Badge><Badge cls="b-info">READ ONLY</Badge></div>}
      <Sec>Shares</Sec>
      <div className="g2">
        <Kv k="Capital committed">{money(sh.capital_committed)}</Kv><Kv k="Planned risk"><span className="amber">{money(sh.planned_risk)}</span></Kv>
        <Kv k="Stress risk"><span className="amber">{money(sh.stress_risk)}</span></Kv><Kv k="Max loss"><span className="red">{money(sh.absolute_max_loss)}</span></Kv>
      </div>
      {sh.absolute_max_loss_basis && <Note>{sh.absolute_max_loss_basis}</Note>}
      <Sec>Option</Sec>
      <div className="g2">
        <Kv k="Premium">{money(op.capital_committed)}</Kv><Kv k="Planned risk"><span className="amber">{money(op.planned_risk)}</span></Kv>
        <Kv k="Stress risk"><span className="amber">{money(op.stress_risk)}</span></Kv><Kv k="Absolute max loss"><span className="red">{money(op.absolute_max_loss)}</span></Kv>
      </div>
      {op.planned_risk_basis && <Note>{op.planned_risk_basis}</Note>}
      {op.exit_note && <Note style={{ marginTop: 4 }}>{op.exit_note}</Note>}
      <Sec>Portfolio</Sec>
      <div className="g2">
        <Kv k="Buying power">{money(rsec.buying_power)}</Kv><Kv k="Remaining capacity">{money(rsec.remaining_capacity)}</Kv>
        <Kv k="Current exposure">{money(rsec.exposure)}</Kv><Kv k="Capital utilisation">{num(pa.capital_utilization_pct, 1)}%</Kv>
        <Kv k="Open option positions">{(rsec.open_option_positions || []).length}</Kv><Kv k="Paper equity">{money(pa.equity)}</Kv>
      </div>
      {(sz.blocked || []).length > 0 && <><Sec>Blocked</Sec><ul className="rl">{sz.blocked.map((x: string, i: number) => <li key={i} className="blk">{x}</li>)}</ul></>}
      <Sec>Option eligibility</Sec>
      <div className="flexrow mb4">
        <Badge cls={ps.pass ? 'b-bull' : 'b-bear'}>{ps.pass ? 'FITS POLICY' : String((sz.option || {}).status || 'DOES NOT FIT POLICY').toUpperCase()}</Badge>
        {elig.binding_constraint && <span className="dim2" style={{ fontSize: 10 }}>binding constraint: <b>{elig.binding_constraint}</b></span>}
      </div>
      {(elig.checks || []).length ? (
        <>
          <table><thead><tr><th className="nosort">Check</th><th className="nosort">Value</th><th className="nosort">Limit</th><th className="nosort">OK</th></tr></thead><tbody>
            {elig.checks.map((ck: any, i: number) => (
              <tr key={i}><td className="l">{ck.check}</td><td className={ck.pass ? '' : 'red'}>{money(ck.value)}</td><td>{money(ck.limit)}</td><td>{ck.pass ? <span className="green">✓</span> : <span className="red">✕</span>}</td></tr>
            ))}
          </tbody></table>
          <Note style={{ marginTop: 5 }}>{(failing || {}).detail || ''}</Note>
        </>
      ) : <Note>{elig.reason || dd.option_ineligible_reason || 'no eligibility detail'}</Note>}
      {dd.option_ineligible_reason && <Note style={{ marginTop: 6 }}>{dd.option_ineligible_reason}</Note>}
    </>
  );
}

/* ── OPTION: the contract, and the three distinct verdicts ── */
function OptionTab({ d }: { d: any }) {
  const o = (useSec('options') || {}).data || {};
  const decData = (useSec('decision') || {}).data;
  const dd = d || decData || {};
  const best = o.best_contract;
  const q = dd.contract_quality || {}, ps = dd.portfolio_suitability || {}; const elig = ps.eligibility || {};
  const failing = (elig.checks || []).filter((c: any) => !c.pass);
  return (
    <>
      {best ? (
        <>
          <div className="flexrow mb6"><b className="mono" style={{ fontSize: 13 }}>{best.expiry || ''} ${num(best.strike, 2)} {(best.side || 'call').toUpperCase()}</b><Badge cls={best.tradeable ? 'b-bull' : 'b-bear'}>{best.tradeable ? 'PASSES GATES' : 'REJECTED'}</Badge></div>
          <div className="g2">
            <Kv k="Bid / Ask">{num(best.bid, 2)} / {num(best.ask, 2)}</Kv><Kv k="Spread">{num(best.spread_pct, 1)}%</Kv>
            <Kv k="Delta">{num(best.delta, 3)}</Kv><Kv k="Theta">{num(best.theta, 3)}</Kv>
            <Kv k="IV">{best.implied_volatility != null ? (best.implied_volatility * 100).toFixed(0) + '%' : '—'}</Kv><Kv k="DTE">{best.dte ?? '—'}</Kv>
            <Kv k="Open interest">{kmb(best.open_interest)}</Kv><Kv k="Volume">{kmb(best.volume)}</Kv>
            <Kv k="Break-even">{num(best.break_even, 2)}</Kv><Kv k="Move to break-even">{num(best.pct_move_to_break_even, 1)}%</Kv>
            <Kv k="Probability">{best.chance_of_profit_long != null ? (best.chance_of_profit_long * 100).toFixed(0) + '%' : '—'}</Kv><Kv k="Premium">{money((best.limit_price || 0) * 100)}</Kv>
          </div>
          {best.greeks_provenance === 'model' && <Note style={{ marginTop: 4 }}>Greeks are modelled (Black-Scholes), not provider-supplied.</Note>}
        </>
      ) : <Note>No contract passed the liquidity gates{o.contracts_analysed ? ` (${o.contracts_analysed} analysed)` : ''}.</Note>}
      <Sec>The three questions, answered separately</Sec>
      <Kv k={<>Option quality <span className="dim2">— best in its chain?</span></>}><Badge cls={q.gates_passed ? 'b-bull' : 'b-bear'}>{q.gates_passed ? 'PASS' : 'FAIL'}</Badge></Kv>
      <Kv k={<>Portfolio suitability <span className="dim2">— should THIS account?</span></>}><Badge cls={ps.pass ? 'b-bull' : 'b-bear'}>{ps.pass ? 'PASS' : 'FAIL'}</Badge></Kv>
      <Kv k="Final instrument"><Badge cls="b-info">{dd.instrument || '—'}</Badge></Kv>
      {!ps.pass && (
        <>
          <Sec>Binding constraint</Sec>
          <Note>{elig.binding_constraint ? <>The option fails on <b>{elig.binding_constraint}</b></> : 'The option does not fit the active risk policy'}{failing.length ? ` — ${failing.length} of ${(elig.checks || []).length} policy checks fail. Full table in the Risk tab.` : '.'}</Note>
          {failing.slice(0, 3).map((c: any, i: number) => <Note key={i} style={{ marginTop: 3 }}>• {c.check}: {c.detail || ''}</Note>)}
        </>
      )}
      {(dd.reasons_for_option || []).length > 0 && <><Sec>For the option</Sec><ul className="rl">{dd.reasons_for_option.map((x: string, i: number) => <li key={i} className="pos">{x}</li>)}</ul></>}
      {(dd.reasons_for_stock || []).length > 0 && <><Sec>For shares instead</Sec><ul className="rl">{dd.reasons_for_stock.slice(0, 5).map((x: string, i: number) => <li key={i} className="neg">{shortReason(x)}</li>)}</ul></>}
      {dd.spread_note && <Note style={{ marginTop: 5 }}>{dd.spread_note}</Note>}
      <Note style={{ marginTop: 6 }}>The full chain lives in the Options widget. {dd.disclaimer || 'Research only.'}</Note>
    </>
  );
}
