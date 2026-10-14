import { Link } from 'react-router-dom';
import { ago, cls, money, num, us } from '../lib/format';
import { useCloudMeta, useGet } from '../lib/nav';
import { Badge, EmptyBox, Kv, Note, Skel } from '../components/ui';
import { Card, Kpi, PageHead, SymLink } from '../components/blocks';

/** flatten a small JSON object into [path, scalar] rows (funnel / capacity blobs have no fixed shape) */
function flatten(o: any, prefix = '', out: [string, any][] = []): [string, any][] {
  if (o == null) return out;
  if (Array.isArray(o)) { out.push([prefix || 'items', `${o.length} items`]); return out; }
  if (typeof o !== 'object') { out.push([prefix, o]); return out; }
  for (const [k, v] of Object.entries(o)) {
    if (out.length > 40) break;
    flatten(v, prefix ? `${prefix} · ${k}` : k, out);
  }
  return out;
}
const fmtV = (v: any) => (typeof v === 'number' ? (Number.isInteger(v) ? String(v) : num(v, 2)) : String(v));
const STALE_S = 36 * 3600;

export default function Cloud() {
  const meta = useCloudMeta().data;
  const rt = useGet<any>(meta && meta.state === 'ok' ? '/api/cloud/runtime' : null, { poll: 120000 }).data;
  const acct = useGet<any>(meta && meta.state === 'ok' ? '/api/cloud/paper/account' : null).data;

  const head = <PageHead title="Cloud runtime" sub="A read-only mirror of what the cloud paper runtime has been doing — its ledger, evidence boundary, scan funnel and shadow experiments. Separate from your local ledger; never merged with it." />;
  if (!meta) return <>{head}<Skel n={8} /></>;
  if (meta.__err || meta.state !== 'ok') {
    return (
      <>{head}
        <Card title="No cloud mirror yet">
          <Note>Nothing has been pulled from the cloud on this machine. The cloud runtime takes a verified backup at every session close; this copies the newest one here, checks its hashes and database integrity, and installs it read-only.</Note>
          <code className="cmd">python scripts/sync_cloud_data.py --dry-run</code><Note>shows what would be downloaded (name, size, time) and downloads nothing.</Note>
          <code className="cmd">python scripts/sync_cloud_data.py</code><Note>downloads, verifies and installs it. The running terminal switches to it automatically.</Note>
        </Card>
      </>
    );
  }

  const stale = (meta.backup_age_seconds ?? 0) > STALE_S;
  const v11 = rt?.v11;
  const cand = rt?.latest_candidates || [];
  const latest = (rt?.recent_cycles || []).find((c: any) => c.cycle_id === rt?.latest_cycle_id);
  const funnel = flatten(latest?.funnel_json);

  return (
    <>
      {head}
      <div className="stack">
        <Card title="Mirror" right={<Badge cls={stale ? 'b-stale' : 'b-fresh'}>{stale ? 'BACKUP IS OLD' : 'CURRENT'}</Badge>}
          sub="Verified copy of the cloud's last end-of-session backup">
          <div className="row2e">
            <div>
              <Kv k="Backup">{meta.name}</Kv>
              <Kv k="Taken">{meta.backup_created_at ? `${meta.backup_created_at.slice(0, 16).replace('T', ' ')} UTC · ${ago(meta.backup_age_seconds)} ago` : '–'}</Kv>
              <Kv k="Downloaded">{meta.synced_at ? `${ago(meta.synced_age_seconds)} ago` : '–'}</Kv>
              <Kv k="Verified">{meta.verified ? <span className="green">sha256 + integrity ✓</span> : <span className="red">no</span>}</Kv>
              <Kv k="Ledger">{meta.ledger}</Kv>
            </div>
            <div>
              <Note>Refresh to the newest backup:</Note>
              <code className="cmd">python scripts/sync_cloud_data.py</code>
              <Note>Ask the cloud for a brand-new backup first (runs the read-only ops workflow):</Note>
              <code className="cmd">python scripts/sync_cloud_data.py --refresh</code>
              <Note>Use the switch in the top bar (Ledger: Local / Cloud) to see Overview, Portfolio and Strategies for the cloud ledger.</Note>
            </div>
          </div>
        </Card>

        <div className="kpis">
          <Kpi l="Cloud equity" value={acct?.equity} format={money} sub={acct ? `${acct.open_positions ?? 0} open · cash ${money(acct.cash)}` : ''} />
          <Kpi l="P&L since v1.1 boundary" value={v11?.v11_pnl} format={money} tone={cls(v11?.v11_pnl)} sub={v11 ? `Boundary equity ${money(v11.boundary_equity)}` : 'No boundary recorded'} />
          <Kpi l="Scan cycles recorded" value={rt?.counts?.shadow_cycles} format={(n) => (n == null ? '–' : String(n))} sub={`${rt?.counts?.shadow_candidates ?? '–'} candidate rows`} />
          <Kpi l="Independent events" value={rt?.counts?.shadow_events} format={(n) => (n == null ? '–' : String(n))} sub="Count evidence by event, not observation" />
          <Kpi l="CH-001 forward results" value={rt?.counts?.shadow_ch001_results} format={(n) => (n == null ? '–' : String(n))} sub="Breakeven-after-+1R, clean sample" />
        </div>

        {!rt ? <Skel n={6} /> : rt.state !== 'ok' ? <EmptyBox ico="○" msg="No runtime evidence in this backup" sub={rt.reason || ''} /> : (
          <>
            <div className="row2e">
              <Card title="Latest scan" sub={latest ? `${latest.session_date} · ${latest.session_type} · ${(latest.scan_ts || '').slice(11, 16)} UTC` : undefined}>
                {funnel.length ? funnel.map(([k, v]) => <Kv key={k} k={us(k)}>{fmtV(v)}</Kv>) : <Note>No funnel recorded for the latest cycle.</Note>}
                {latest && <Note style={{ marginTop: 8 }}>{latest.finalists ?? '–'} finalists · {latest.eligible ?? '–'} capacity-eligible{latest.is_choice_event ? ' · a choice day (more eligible than slots)' : ''}</Note>}
              </Card>
              <Card title="Evidence boundary" sub="Where v1.1 evidence starts; v1.0 gains and losses are never used">
                {(rt.evidence_boundary || []).length ? (rt.evidence_boundary as any[]).map((b) => (
                  <div key={b.version}><Kv k={<b>{b.version}</b>}>{(b.at || '').slice(0, 16).replace('T', ' ')} UTC</Kv>
                    <Kv k="Equity / cash">{money(b.equity)} / {money(b.cash)}</Kv><Kv k="Open positions">{b.open_positions ?? 0}</Kv></div>
                )) : <Note>No boundary recorded yet.</Note>}
              </Card>
            </div>

            <Card title="Latest candidates" sub="Every finalist of the latest scan, with what the Champion did and why" flush>
              {cand.length ? (
                <div style={{ overflowX: 'auto' }}><table><thead><tr><th>Rank</th><th className="l">Symbol</th><th className="l">Strategy</th><th className="l">Decision</th><th>Quality</th><th>Exp. R</th><th>Entry</th><th>Stop</th><th>Target</th><th className="l">Entered</th><th className="l">Reason / capacity</th></tr></thead><tbody>
                  {cand.map((c: any, i: number) => (
                    <tr key={i}><td>{c.scanner_rank ?? '–'}</td><td className="l"><SymLink s={c.symbol} /></td><td className="setup">{us(c.strategy)}</td>
                      <td className="l"><Badge cls={c.decision === 'TRADEABLE' ? 'b-bull' : c.decision === 'MONITOR' ? 'b-stale' : 'b-neut'}>{c.decision || '–'}</Badge></td>
                      <td>{c.quality != null ? num(c.quality, 0) : '–'}</td><td>{c.expected_r != null ? num(c.expected_r, 2) : '–'}</td>
                      <td>{c.entry != null ? num(c.entry, 2) : '–'}</td><td>{c.stop != null ? num(c.stop, 2) : '–'}</td><td>{c.target != null ? num(c.target, 2) : '–'}</td>
                      <td className="l">{c.champion_executed ? <span className="green">yes</span> : <span className="dim2">no</span>}</td>
                      <td className="l wrap setup" style={{ minWidth: 220 }}>{c.champion_reason || c.capacity_reason || us(c.failed_gates) || '–'}</td></tr>
                  ))}
                </tbody></table></div>
              ) : <EmptyBox ico="○" msg="No candidate rows" />}
            </Card>

            <div className="row2e">
              <Card title="Sessions" sub="Scan cycles per day" flush>
                {(rt.sessions || []).length ? (
                  <table><thead><tr><th>Date</th><th className="l">Type</th><th>Cycles</th><th>Max finalists</th><th>Max eligible</th></tr></thead><tbody>
                    {rt.sessions.slice(0, 16).map((s: any, i: number) => <tr key={i}><td>{s.session_date}</td><td className="l">{us(s.session_type)}</td><td>{s.cycles}</td><td>{s.max_finalists ?? '–'}</td><td>{s.max_eligible ?? '–'}</td></tr>)}
                  </tbody></table>
                ) : <EmptyBox ico="○" msg="No cycles yet" />}
              </Card>
              <Card title="CH-001 forward evaluation" sub="Breakeven stop after +1R, scored on real cloud positions" flush>
                {(rt.ch001_results || []).length ? (
                  <table><thead><tr><th className="l">Symbol</th><th>Original R</th><th>Breakeven R</th><th>Δ R</th><th className="l">+1R?</th><th className="l">Ambiguous</th></tr></thead><tbody>
                    {rt.ch001_results.map((r: any, i: number) => <tr key={i}><td className="l"><SymLink s={r.symbol} /></td><td className={cls(r.original_R)}>{num(r.original_R, 2)}</td><td className={cls(r.be_R)}>{num(r.be_R, 2)}</td>
                      <td className={cls(r.delta_R)}>{num(r.delta_R, 2)}</td><td className="l">{r.plus1r_occurred ? 'yes' : 'no'}</td><td className="l">{r.ambiguous ? <Badge cls="b-stale">AMBIGUOUS</Badge> : 'no'}</td></tr>)}
                  </tbody></table>
                ) : <EmptyBox ico="○" msg="No clean sample yet" sub="A position must reach +1R before the rule can be evaluated." />}
              </Card>
            </div>

            {(rt.maintenance || []).length > 0 && (
              <Card title="Runtime maintenance log">{rt.maintenance.map((m: any, i: number) => <Kv key={i} k={<span className="mono dim">{(m.at || '').slice(0, 16).replace('T', ' ')}</span>}>{m.action}</Kv>)}</Card>
            )}
          </>
        )}
        <Note>Where this fits: <Link to="/strategies" style={{ color: 'var(--accent)' }}>Strategies</Link> for the Champion and Challengers, <Link to="/testing" style={{ color: 'var(--accent)' }}>Testing</Link> for pre-registered experiments.</Note>
      </div>
    </>
  );
}
