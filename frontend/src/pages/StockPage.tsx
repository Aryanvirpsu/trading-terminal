import { useEffect } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import ChartWidget from '../components/widgets/ChartWidget';
import Technicals from '../components/widgets/Technicals';
import Options from '../components/widgets/Options';
import News from '../components/widgets/News';
import Risk from '../components/widgets/Risk';
import { DecisionHeader, DecisionPane } from '../components/widgets/Decision';
import { Badge, FreshChip, Kv, Note, Sec } from '../components/ui';
import { Card, PageHead, TabLinks } from '../components/blocks';
import { cls, num, pct, us as us0 } from '../lib/format';
import { Snap, setSymbol, useSec, useSnap } from '../lib/snapshot';
import { useWS } from '../lib/store';

const TABS: [string, string, string?][] = [
  ['', 'Overview'], ['chart', 'Chart'], ['technicals', 'Technicals', 'technicals'], ['options', 'Options', 'options'],
  ['news', 'News & catalysts', 'news'], ['decision', 'Decision', 'decision'], ['risk', 'Risk & position', 'risk'], ['strategy', 'Strategy & history', 'history'],
];

export default function StockPage() {
  const { symbol: param, tab = '' } = useParams();
  const current = useWS((s) => s.symbol);
  useEffect(() => { if (param) setSymbol(param); }, [param]);
  if (!param) return <Navigate to={`/stock/${current}`} replace />;
  const sym = param.toUpperCase();
  return (
    <>
      <PageHead crumbs={<><Link to="/scanner">Scanner</Link> / Stock</>} title={sym} right={<RefreshBtn />} />
      <Header />
      <TabLinks sticky items={TABS.map(([k, l, sec]) => ({ to: `/stock/${sym}${k ? '/' + k : ''}`, label: l, mark: sec ? <TabMark sec={sec} /> : undefined }))} />
      <div key={tab || 'overview'} className="tab-enter"><TabBody tab={tab} /></div>
    </>
  );
}

function RefreshBtn() {
  const refreshing = useSnap((s) => s.refreshing);
  return <button className="btn" disabled={refreshing} title="Refresh this symbol only — does NOT rescan the universe (r)" onClick={() => Snap.refresh()}>{refreshing ? 'Refreshing…' : '⟳ Refresh symbol'}</button>;
}
function TabMark({ sec }: { sec: string }) {
  const s = useSec(sec); const st = s ? s.state || 'loading' : 'loading';
  return st === 'loading' ? <span className="st amber" title="still computing">●</span> : st === 'ok' ? null : <span className="st dim2" title={st}>○</span>;
}

function Header() {
  const q = useSec('quote') || {}; const d = q.data || {};
  const dec = useSec('decision'); const dd = dec && dec.state === 'ok' ? dec.data || {} : null;
  const label = dd ? (dd.gated ? 'STALE' : String(dd.decision || '').toUpperCase()) : null;
  const vcls = label === 'TRADEABLE' ? 'b-bull' : label === 'MONITOR' ? 'b-stale' : label === 'STALE' ? 'b-critical' : 'b-neut';
  const mkt = useSnap((s) => (s.data || {}).market) || {};
  return (
    <div className="card stock-head">
      <div style={{ display: 'contents' }}>
        <div className="meta"><span className="l">Last</span><span className="px">{d.price != null ? '$' + num(d.price, 2) : '—'}</span></div>
        <div className="meta"><span className="l">Change</span><span className={`chg ${cls(d.change_pct)}`}>{d.change_pct != null ? pct(d.change_pct) : '—'}</span></div>
        <div className="meta"><span className="l">Data</span><span className="v"><FreshChip sec={q.state ? q : null} /></span></div>
        <div className="meta"><span className="l">Market</span><span className="v">{mkt.state ? us0(mkt.state) : '—'}</span></div>
        <span className="spacer" />
        <div className="meta" style={{ alignItems: 'flex-end' }}><span className="l">Verdict</span>
          <span className="flexrow" style={{ gap: 8 }}>{label ? <Badge cls={vcls}>{label || '—'}</Badge> : <Badge cls="b-load">LOADING</Badge>}{dd && !dd.gated && <span className="mono dim" style={{ fontSize: 12 }}>{dd.instrument || ''}</span>}</span></div>
        {dd && !dd.gated && <div className="meta" style={{ alignItems: 'flex-end' }}><span className="l">Decision score</span><span className="v">{num(dd.decision_score, 1)} / {num((dd.score_scale || {}).max, 0)}</span></div>}
      </div>
    </div>
  );
}

function TabBody({ tab }: { tab: string }) {
  switch (tab) {
    case 'chart':
      return <Card flush><div className="chart-tall"><ChartWidget /></div></Card>;
    case 'technicals':
      return <Card title="Technicals & levels"><Technicals /></Card>;
    case 'options':
      return <Card title="Options"><Options /></Card>;
    case 'news':
      return <Card title="News & catalysts"><News /></Card>;
    case 'decision':
      return (
        <div className="row2">
          <Card title="Thesis"><DecisionPane tab="thesis" /></Card>
          <div className="stack"><Card title="Verdict"><DecisionHeader /></Card><Card title="Why and what to watch"><DecisionPane tab="overview" /></Card></div>
        </div>
      );
    case 'risk':
      return <div className="row2e"><Card title="Risk & position"><Risk /></Card><Card title="Instrument-aware risk model"><DecisionPane tab="risk" /></Card></div>;
    case 'strategy':
      return (
        <div className="stack">
          <StrategyFit />
          <div className="row2e">
            <Card title="Historical validation" sub="Comparable past setups"><DecisionPane tab="history" /></Card>
            <Card title="Option contract quality"><DecisionPane tab="option" /></Card>
          </div>
        </div>
      );
    default:
      return (
        <div className="stack">
          <div className="row2">
            <Card flush title="Price" right={<Link className="btn sm" to={`/stock/${useWS.getState().symbol}/chart`}>Full chart</Link>}><div className="chart-box" style={{ padding: '10px 14px' }}><ChartWidget /></div></Card>
            <Card title="Verdict"><DecisionHeader /></Card>
          </div>
          <div className="row2">
            <Card title="Technicals & levels"><Technicals /></Card>
            <Card title="Why and what to watch"><DecisionPane tab="overview" /></Card>
          </div>
          <div className="row2e">
            <Card title="News & catalysts"><News /></Card>
            <Card title="Risk & position"><Risk /></Card>
          </div>
        </div>
      );
  }
}

/** Where this stock sits against the strategies: scanner setups, failed Champion gates, and links to the evidence. */
function StrategyFit() {
  const sec = useSec('decision');
  const d = sec && sec.state === 'ok' ? sec.data || {} : null;
  const setups = (d && d.setups) || [];
  const failed = (d && d.funnel && d.funnel.failed_gates) || [];
  const hard = (d && d.hard_failures) || [];
  return (
    <Card title="Strategy fit" sub="How this stock relates to the Champion and the research">
      {!d ? <Note>Decision is still computing.</Note> : (
        <div className="row3">
          <div>
            <Sec>Scanner setups matched</Sec>
            {setups.length ? setups.map((s: any, i: number) => <div key={i}><Kv k={us0(s.type)}>{num(s.strength, 0)}<span className="dim2"> · {s.horizon || ''}</span></Kv><div className="note" style={{ margin: '0 0 6px' }}>{s.evidence || ''}</div></div>) : <Note>No setup category matched.</Note>}
          </div>
          <div>
            <Sec>Champion gates</Sec>
            {hard.length || failed.length ? (
              <ul className="rl">{hard.map((h: any, i: number) => <li key={'h' + i} className="blk"><b>{us0(h.kind)}</b></li>)}{failed.map((g: string, i: number) => <li key={i} className="neg">{us0(g)}</li>)}</ul>
            ) : <Note>No failed gates recorded for this name.</Note>}
            <Note style={{ marginTop: 8 }}>{d.context_cannot_override || ''}</Note>
          </div>
          <div>
            <Sec>Evidence & research</Sec>
            <div className="stack" style={{ gap: 8 }}>
              <Link className="btn" to="/strategies">Strategies — Champion and challengers</Link>
              <Link className="btn" to="/testing">Testing — pre-registered experiments</Link>
              <Link className="btn" to="/scanner">Scanner — today's cohort and rejected names</Link>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
