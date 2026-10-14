import { ago } from '../lib/format';
import { DL, stats } from '../lib/api';
import { useGet } from '../lib/nav';
import { clearAllSaved, useWS } from '../lib/store';
import { useSnap } from '../lib/snapshot';
import { Badge, FreshChip, Kv, MktBadge, Note, PRES_CLS, Skel } from '../components/ui';
import { Card, PageHead } from '../components/blocks';

export default function System() {
  const h = useGet<any>('/api/health', { poll: 30000 }).data;
  const st = useGet<any>('/api/terminal/status', { poll: 15000 }).data;
  const sched = useGet<any>('/api/scheduler').data;
  const snap = useSnap((s) => s.data);
  const ws = useWS();
  const prov = (h && ((h.providers && h.providers.providers) || h.providers)) || {};

  return (
    <>
      <PageHead title="System" sub="Data freshness, background jobs, providers and local settings. Broker access is read-only; this app cannot place, preview or queue an order." />
      <div className="stack">
        <div className="row2e">
          <Card title="Market session">{!st ? <Skel n={3} /> : <div className="flexrow" style={{ fontFamily: 'var(--mono)' }}><MktBadge m={st.market} /></div>}
            {sched && !sched.__err && <><Kv k="Scheduler">{sched.current_status || '–'}</Kv><Kv k="Last run">{sched.last_run_at || 'never'}</Kv><Kv k="Next run">{sched.next_run || '–'}</Kv></>}
          </Card>
          <Card title="Datasets">
            {!st ? <Skel n={3} /> : (st.datasets || []).map((x: any, i: number) => (
              <Kv key={i} k={<>{x.label || x.id} <span className="dim2">{x.scope || ''}</span></>}><Badge cls={PRES_CLS[x.presentation] || 'b-neut'}>{x.presentation || '?'}</Badge> {x.age_seconds != null ? ago(x.age_seconds) : ''}</Kv>
            ))}
          </Card>
        </div>
        <div className="row2e">
          <Card title="Background jobs">
            {!st ? <Skel n={3} /> : (st.jobs || []).map((j: any, i: number) => (
              <div key={i}><Kv k={j.label}>{j.running ? <Badge cls="b-load">running</Badge> : j.stalled ? <Badge cls="b-stale">stalled</Badge> : j.idle ? <Badge cls="b-neut">idle</Badge> : <Badge cls="b-fresh">ok</Badge>} {j.age_seconds != null ? ago(j.age_seconds) : ''}</Kv>
                {j.note && <Note style={{ margin: '-2px 0 8px' }}>{j.note}</Note>}</div>
            ))}
          </Card>
          <Card title="Providers">
            {!h ? <Skel n={5} /> : Object.keys(prov).length ? Object.entries(prov).map(([k, v]: any) => (
              <Kv key={k} k={k}><span style={{ fontSize: 11.5, color: 'var(--dim)' }}>{typeof v === 'object' ? (v.detail || v.state || JSON.stringify(v)).toString().slice(0, 48) : String(v)}</span></Kv>
            )) : <Note>No provider report.</Note>}
          </Card>
        </div>
        <Card title="Sections for the open stock" sub={snap ? undefined : 'Open a stock to populate'}>
          {Object.entries(((snap || {}).sections || {}) as Record<string, any>).map(([n, s]) => <Kv key={n} k={n}><FreshChip sec={s} /> <span className="dim2" style={{ fontSize: 11 }}>{s.cache_state || ''}</span></Kv>)}
          {!snap && <Note>No stock loaded yet.</Note>}
        </Card>
        <Card title="Workspace">
          <Kv k="Selected stock">{ws.symbol}</Kv><Kv k="Account view">{ws.account}</Kv><Kv k="Chart range">{ws.tf}</Kv><Kv k="Scan preset">{ws.scanPreset}</Kv>
          <Kv k="Client cache"><span className="mono" style={{ fontSize: 11.5 }}>{Object.entries(stats).map(([k, v]) => `${k}:${v}`).join(' ')} · urls:{DL.size()}</span></Kv>
          <div className="flexrow mt12"><button className="btn" onClick={() => { clearAllSaved(); location.reload(); }}>Clear saved workspace</button></div>
          <Note style={{ marginTop: 8 }}>Selected stock, scanner filters, chart settings and the account view persist in this browser only.</Note>
        </Card>
      </div>
    </>
  );
}
