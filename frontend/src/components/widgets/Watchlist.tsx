import { useCallback, useEffect, useRef, useState } from 'react';
import { DL } from '../../lib/api';
import { ago, cls, num, pct, us } from '../../lib/format';
import { openStock } from '../../lib/nav';
import { useWS, type WS } from '../../lib/store';
import { Badge, EmptyBox, Note, Sec, Skel } from '../ui';
import { TabBtns, fmtCell, rowNav } from '../blocks';

const COHORT_STATUS_CLS: any = { selected: 'b-neut', watching: 'b-neut', approaching_entry: 'b-info', entry_triggered: 'b-info', target_1_hit: 'b-bull', target_2_hit: 'b-bull', invalidated: 'b-critical', thesis_broken: 'b-critical', expired: 'b-stale', horizon_complete: 'b-stale', manually_closed: 'b-neut' };
const TRACK_STATUS_CLS: any = { watch: 'b-neut', approaching_entry: 'b-info', entry_triggered: 'b-info', target_1_reached: 'b-bull', target_2_reached: 'b-bull', invalidated: 'b-critical', expired: 'b-neut', data_unavailable: 'b-stale', manually_closed: 'b-neut' };

type Src = WS['watchSrc'];
const TABS: [Src, string][] = [['scan', 'Scan'], ['cohort', "Today's top 5"], ['alerts', 'Alerts'], ['history', 'Scan history'], ['intel', 'Cohort intel'], ['rejected', 'Rejected'], ['watch', 'Robinhood lists'], ['recent', 'Recent']];

/* The scanner workspace: the universe funnel, the per-scan top-5 cohort (tracked forward regardless
   of whether entry ever triggers), alerts, history, intelligence, and the rejected population.
   The scan itself is a universe operation (~800 names, 45-76s) and is NEVER started automatically. */
export default function Scanner() {
  const { watchSrc, scanPreset, minScore, symbol, recent, set } = useWS();
  const src = (TABS.some(([k]) => k === watchSrc) ? watchSrc : 'scan') as Src;
  const [scanData, setScanData] = useState<any>(null);
  const [wl, setWl] = useState<any>(null);
  const [alerts, setAlerts] = useState<any>(null);
  const [today, setToday] = useState<any>(null);
  const [hist, setHist] = useState<any>(null);
  const [intel, setIntel] = useState<any>(null);
  const [rej, setRej] = useState<any>(null);
  const [scanId, setScanId] = useState('');
  const [busy, setBusy] = useState(false);
  const [sort, setSort] = useState<{ k: string; dir: number }>({ k: 'score', dir: -1 });
  const poll = useRef<any>(null);

  const running = !!(scanData && (scanData.state === 'loading' || scanData.cache_state === 'refreshing'));
  const loadScan = useCallback(async (force: boolean) => {
    clearTimeout(poll.current);
    const url = force ? `/api/scan?preset=${scanPreset}` : `/api/scan?preset=${scanPreset}&peek=1`;
    const d = await DL.get(url, { force });
    setScanData(d);
    if (d && (d.state === 'loading' || d.cache_state === 'refreshing')) poll.current = setTimeout(() => loadScan(false), 4000);
  }, [scanPreset]);
  useEffect(() => { loadScan(false); return () => clearTimeout(poll.current); }, [loadScan]);

  const loadToday = async (force?: boolean, id = scanId) => setToday(await DL.get(`/api/cohort/today${id ? '?scan_id=' + id : ''}`, { force: !!force }));
  useEffect(() => {
    if (src === 'watch' && !wl) DL.get('/api/options/watchlists').then(setWl);
    if (src === 'alerts' && !alerts) DL.get('/api/tracker?include_closed=1').then(setAlerts);
    if (src === 'cohort' && !today) loadToday();
    if (src === 'history' && !hist) DL.get('/api/cohort/history?limit=30').then(setHist);
    if (src === 'intel' && !intel) DL.get('/api/cohort/intelligence?horizon=d5').then(setIntel);
    if (src === 'rejected' && !rej) DL.get('/api/scan/rejected').then(setRej);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  const updateTracked = async () => {
    setBusy(true);
    if (src === 'cohort') { await DL.post('/api/cohort/update', {}); DL.drop('/api/cohort'); await loadToday(true); }
    else { await DL.post('/api/tracker/update', {}); DL.drop('/api/tracker'); setAlerts(await DL.get('/api/tracker?include_closed=1', { force: true })); }
    setBusy(false);
  };
  const pickScan = (id: string) => { setScanId(id); DL.drop('/api/cohort/today'); setToday(null); set({ watchSrc: 'cohort' }); loadToday(true, id); };

  const row = (s: string) => `click${s === symbol ? ' sel' : ''}`;
  let body: any = null;
  if (src === 'scan') {
    if (!scanData) body = <Skel n={8} />;
    else if (scanData.state === 'never_run') body = <EmptyBox ico="⊞" msg="No scan in cache" sub="The scan is a universe operation (~800 names, 45-76s). It is never started automatically — press Run scan." />;
    else if (scanData.state === 'loading') body = <><Note>Scanning the universe…</Note><Skel n={8} /></>;
    else {
      const cands = (scanData.candidates || []).filter((c: any) => ((c.score || {}).total || 0) >= (minScore || 0));
      const fin = new Set((scanData.finalists || []).map((c: any) => c.symbol));
      const g = (o: any) => sort.k === 'score' ? (o.score || {}).total || 0 : sort.k === 'price' ? (o.indicators || {}).price || 0 : sort.k === 'chg' ? (o.indicators || {}).change_pct || 0 : o.symbol || '';
      cands.sort((a: any, b: any) => { const x = g(a), y = g(b); return (typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number)) * sort.dir; });
      const th = (k: string, l: string) => <th onClick={() => setSort((s) => (s.k === k ? { k, dir: -s.dir } : { k, dir: -1 }))}>{l}{sort.k === k ? (sort.dir < 0 ? ' ↓' : ' ↑') : ''}</th>;
      body = cands.length ? (
        <>
          <table><thead><tr>{th('symbol', 'Symbol')}{th('price', 'Price')}{th('chg', 'Change')}{th('score', 'Score')}<th className="nosort l">Setup</th></tr></thead><tbody>
            {cands.slice(0, 150).map((c: any) => { const i = c.indicators || {}, s = c.score || {}; return (
              <tr key={c.symbol} className={row(c.symbol)} {...rowNav(() => openStock(c.symbol))}>
                <td><span className="sym-link">{c.symbol}</span> {fin.has(c.symbol) && <Badge cls="b-bull" tight>FINALIST</Badge>}</td>
                <td>{num(i.price, 2)}</td><td className={cls(i.change_pct)}>{pct(i.change_pct)}</td><td>{num(s.total, 1)}</td>
                <td className="setup">{us(c.primary_setup)}</td>
              </tr>); })}
          </tbody></table>
          <Note style={{ padding: '10px 12px' }}>{cands.length} candidates · {(scanData.finalists || []).length} finalists · scan {ago(scanData.cache_age_s)} old</Note>
        </>
      ) : <EmptyBox ico="○" msg="No candidates above the score filter" sub="Lower the minimum or run a fresh scan." />;
    }
  } else if (src === 'cohort') body = <CohortToday d={today} scanId={scanId} symbol={symbol} pickScan={pickScan} />;
  else if (src === 'alerts') body = <Alerts d={alerts} symbol={symbol} />;
  else if (src === 'history') body = <CohortHistory d={hist} pickScan={pickScan} />;
  else if (src === 'intel') body = <CohortIntel d={intel} />;
  else if (src === 'rejected') body = <Rejected d={rej} />;
  else if (src === 'watch') {
    if (!wl) body = <Skel n={5} />;
    else if (wl.__err || wl.state === 'unavailable') body = <EmptyBox ico="⚠" msg="Watchlists unavailable" sub={wl.reason || wl.__err || ''} />;
    else {
      const lists = wl.watchlists || [];
      body = lists.length ? <div style={{ padding: 16 }}>{lists.map((l: any, k: number) => {
        const items = (l.items || []).filter((i: any) => i.object_type !== 'currency_pair');
        const crypto = (l.items || []).length - items.length;
        return (
          <div key={k}>
            <Sec>{l.name || 'list'} <span className="dim2">{items.length}</span></Sec>
            {items.length ? <div className="flexrow">{items.map((i: any) => <button key={i.symbol} className={`pill-btn${i.symbol === symbol ? ' on' : ''}`} onClick={() => openStock(i.symbol)}>{i.symbol}</button>)}</div> : <Note>empty</Note>}
            {crypto > 0 && <Note>{crypto} crypto pair(s) excluded — not equities</Note>}
          </div>);
      })}</div> : <EmptyBox ico="○" msg="No watchlists" />;
    }
  } else {
    body = (recent || []).length
      ? <div className="flexrow" style={{ padding: 16 }}>{recent.map((s) => <button key={s} className={`pill-btn${s === symbol ? ' on' : ''}`} onClick={() => openStock(s)}>{s}</button>)}</div>
      : <EmptyBox ico="○" msg="Nothing yet" sub="Stocks you open appear here." />;
  }

  const showUpdate = src === 'cohort' || src === 'alerts';
  return (
    <div className="card">
      <div className="card-h" style={{ gap: 12 }}>
        <TabBtns items={TABS as any} value={src as any} onChange={(v: any) => set({ watchSrc: v })} />
        <span className="spacer" />
        {src === 'scan' && (<>
          <span className="dim2">Min score</span>
          <input className="mini" type="number" min={0} max={100} step={5} style={{ width: 64 }} value={minScore} onChange={(e) => set({ minScore: +e.target.value || 0 })} />
          {running && <Badge cls="b-load">scanning</Badge>}
          <button className="btn primary sm" disabled={running} title="Universe operation: ~800 names, 45-76s. Not triggered by opening a stock." onClick={() => loadScan(true)}>{running ? 'Scanning…' : 'Run scan'}</button>
        </>)}
        {showUpdate && <button className="btn sm" disabled={busy} title="Re-check tracked candidates against a fresh quote." onClick={updateTracked}>{busy ? 'Checking…' : 'Update prices'}</button>}
      </div>
      <div className="card-b flush scroll" style={{ maxHeight: 'calc(100vh - 230px)', minHeight: 240 }}>{body}</div>
    </div>
  );
}

function CohortToday({ d, scanId, symbol, pickScan }: any) {
  if (!d) return <div style={{ padding: 16 }}><Skel n={5} /></div>;
  if (d.__err) return <EmptyBox ico="⚠" msg="Cohort unavailable" sub={d.__err} />;
  if (d.state === 'empty') return <EmptyBox ico="⊞" msg="No scan captured yet" sub="Run a scan — its top 5 appear here automatically and are tracked forward." />;
  const run = d.scan || {}; const obs = d.observations || [];
  return (
    <>
      <Note style={{ padding: '10px 14px' }}>Scan {(run.scan_id || '').slice(0, 8)} · {run.created_at ? ago((Date.now() - Date.parse(run.created_at)) / 1000) : '–'} old · {run.preset || ''}
        {scanId && <> · <a href="#/scanner" style={{ color: 'var(--accent)' }} onClick={(e) => { e.preventDefault(); pickScan(''); }}>back to latest</a></>}</Note>
      {!obs.length ? <EmptyBox ico="○" msg="Empty cohort" sub="This scan produced no top-5 candidates." /> : (
        <table><thead><tr><th>#</th><th className="l">Symbol</th><th>Score</th><th className="nosort l">Setup</th><th>Scan price</th><th>Now</th><th>Return</th><th className="nosort">Status</th></tr></thead><tbody>
          {obs.map((o: any) => { const ret = o.return_since_scan_pct; return (
            <tr key={o.symbol} className={`click${o.symbol === symbol ? ' sel' : ''}`} {...rowNav(() => openStock(o.symbol))}>
              <td>{o.rank}</td><td className="l"><span className="sym-link">{o.symbol}</span></td><td>{num(o.score, 1)}</td><td className="setup">{us(o.setup_type)}</td>
              <td>{num(o.scan_price, 2)}</td><td>{o.current_price != null ? num(o.current_price, 2) : '–'}</td>
              <td className={cls(ret)}>{ret != null ? pct(ret) : '–'}</td>
              <td><Badge cls={COHORT_STATUS_CLS[o.status] || 'b-neut'}>{us(o.status)}</Badge></td>
            </tr>); })}
        </tbody></table>
      )}
      <Note style={{ padding: '10px 14px' }}>Tracked from selection regardless of entry — entry, target and invalidation are events, not filters.</Note>
    </>
  );
}

function Alerts({ d, symbol }: any) {
  if (!d) return <div style={{ padding: 16 }}><Skel n={5} /></div>;
  if (d.__err) return <EmptyBox ico="⚠" msg="Tracker unavailable" sub={d.__err} />;
  const setups = (d.setups || []).slice().sort((a: any, c: any) => (c.created_at || '').localeCompare(a.created_at || ''));
  if (!setups.length) return <EmptyBox ico="○" msg="No alerts yet" sub="Entry / target / invalidation alerts on manually tracked setups appear here." />;
  return (
    <>
      <table><thead><tr><th>Symbol</th><th className="l">Setup</th><th>Added</th><th>Status</th><th>Price then → now</th><th>Change</th></tr></thead><tbody>
        {setups.map((s: any, i: number) => {
          const chg = s.last_price != null && s.original_price ? (100 * (s.last_price - s.original_price)) / s.original_price : null;
          return (
            <tr key={i} className={`click${s.symbol === symbol ? ' sel' : ''}`} {...rowNav(() => openStock(s.symbol))}>
              <td><span className="sym-link">{s.symbol}</span></td><td className="setup">{us(s.setup_type)}</td><td className="dim2">{s.created_at ? s.created_at.slice(0, 10) : '–'}</td>
              <td><Badge cls={TRACK_STATUS_CLS[s.status] || 'b-neut'}>{us(s.status)}</Badge></td>
              <td>{num(s.original_price, 2)} → {num(s.last_price, 2)}</td><td className={cls(chg)}>{pct(chg)}</td>
            </tr>);
        })}
      </tbody></table>
      <Note style={{ padding: '10px 14px' }}>{setups.length} alert-tracked · added manually via the options desk, not auto-fed by every scan.</Note>
    </>
  );
}

function CohortHistory({ d, pickScan }: any) {
  if (!d) return <div style={{ padding: 16 }}><Skel n={5} /></div>;
  if (d.__err) return <EmptyBox ico="⚠" msg="History unavailable" sub={d.__err} />;
  const runs = d.runs || [];
  if (!runs.length) return <EmptyBox ico="○" msg="No scan history yet" sub="Every completed scan appears here; older scans are never overwritten." />;
  return (
    <>
      {runs.map((r: any) => (
        <div key={r.scan_id} className="list-row" onClick={() => pickScan(r.scan_id)}>
          <div style={{ minWidth: 0 }}>
            <div className="t mono" style={{ fontSize: 12.5 }}>{(r.created_at || '').slice(0, 16).replace('T', ' ')} <span className="dim2" style={{ fontWeight: 400 }}>{r.preset || ''} · {(r.observations || []).length} names</span></div>
            <div className="d">{(r.observations || []).length ? (r.observations || []).map((o: any, i: number) => <span key={i}>{i > 0 && ' · '}{o.symbol} <span className="dim2">{num(o.score, 0)}</span></span>) : 'empty cohort'}</div>
          </div>
        </div>
      ))}
      <Note style={{ padding: '10px 14px' }}>Click a scan to open it under "Today's top 5".</Note>
    </>
  );
}

function CohortIntel({ d }: any) {
  if (!d) return <div style={{ padding: 16 }}><Skel n={5} /></div>;
  if (d.__err) return <EmptyBox ico="⚠" msg="Intelligence unavailable" sub={d.__err} />;
  const rp = (d.rank || {}).groups || {}, sb = (d.score_bucket || {}).groups || {}, st = (d.setup_type || {}).groups || {}, rep = (d.repetition || {}).symbols || [];
  const grp = (title: string, groups: any, order?: string[]) => {
    const keys = (order || Object.keys(groups).sort()).filter((k) => groups[k]);
    if (!keys.length) return null;
    return (
      <div style={{ padding: '14px 16px 0' }}>
        <Sec>{title} <span className="dim2">({d.horizon})</span></Sec>
        <table><thead><tr><th>Group</th><th>N</th><th>Avg return</th><th>Hit rate</th><th>Alpha</th></tr></thead><tbody>
          {keys.map((k) => { const g = groups[k]; return (
            <tr key={k}><td>{k}</td><td>{g.n}</td><td className={cls(g.avg_return_pct)}>{pct(g.avg_return_pct)}</td><td>{g.hit_rate_pct}%</td><td className={cls(g.avg_alpha_pct)}>{g.avg_alpha_pct != null ? pct(g.avg_alpha_pct) : '–'}</td></tr>); })}
        </tbody></table>
      </div>
    );
  };
  const warn = rep.filter((r: any) => r.repeated_candidate_warning);
  return (
    <>
      {grp('Return by rank', rp) || <EmptyBox ico="○" msg="No performance data yet" sub="Once observations have 5+ trading days of history, rank / score / setup performance appears here." />}
      {grp('Return by score bucket', sb, ['0-59', '60-69', '70-79', '80-100'])}
      {grp('Return by setup type', st)}
      <div style={{ padding: '14px 16px' }}>
        <Sec>Repeated-candidate diagnostic {warn.length > 0 && <Badge cls="b-stale">{warn.length} flagged</Badge>}</Sec>
        {rep.length ? (
          <table><thead><tr><th>Symbol</th><th>Seen</th><th>Rate</th><th>Streak</th><th>Avg rank</th><th>Avg 5d</th></tr></thead><tbody>
            {rep.slice(0, 15).map((r: any, i: number) => (
              <tr key={i} style={r.repeated_candidate_warning ? { background: 'rgba(231,179,65,.08)' } : undefined}>
                <td>{r.symbol}</td><td>{r.appearances}</td><td>{Math.round(r.appearance_rate * 100)}%</td><td>{r.consecutive_streak}</td><td>{r.avg_rank}</td>
                <td className={cls(r.avg_5d_return_pct)}>{r.avg_5d_return_pct != null ? pct(r.avg_5d_return_pct) : '–'}</td>
              </tr>))}
          </tbody></table>
        ) : <Note>No repetition data yet.</Note>}
        <Note style={{ marginTop: 8 }}>{d.repetition ? d.repetition.scans_considered : 0} scans considered · sample sizes below ~12 are indicative only.</Note>
      </div>
    </>
  );
}

/** Candidates the funnel rejected — the other half of the population, for counterfactual review. */
function Rejected({ d }: any) {
  if (!d) return <div style={{ padding: 16 }}><Skel n={5} /></div>;
  if (d.__err) return <EmptyBox ico="⚠" msg="Rejected list unavailable" sub={d.__err} />;
  if (d.state === 'loading' || d.state === 'never_run') return <EmptyBox ico="○" msg="No rejected list yet" sub="It is produced by a scan. Run a scan, then reopen this tab." />;
  const rows = d.rejected || [];
  if (!rows.length) return <EmptyBox ico="○" msg="Nothing rejected" sub={`Preset ${d.preset || ''} · ${d.count ?? 0} rejected in the last scan.`} />;
  const cols = Object.keys(rows[0]).filter((k) => typeof rows[0][k] !== 'object').slice(0, 7);
  return (
    <>
      {d.by_stage && Object.keys(d.by_stage).length > 0 && (
        <div className="flexrow" style={{ padding: '10px 14px' }}>{Object.entries(d.by_stage).map(([k, v]: any) => <Badge key={k} cls="b-neut">{us(k)} · {v}</Badge>)}</div>
      )}
      <table><thead><tr>{cols.map((c, i) => <th key={c} className={`nosort${i === 0 ? '' : ' l'}`}>{us(c)}</th>)}</tr></thead><tbody>
        {rows.slice(0, 200).map((r: any, i: number) => (
          <tr key={i} className="click" {...rowNav(() => r.symbol && openStock(r.symbol))}>{cols.map((c, j) => <td key={c} className={j === 0 ? '' : 'l'}>{fmtCell(r[c])}</td>)}</tr>
        ))}
      </tbody></table>
    </>
  );
}
