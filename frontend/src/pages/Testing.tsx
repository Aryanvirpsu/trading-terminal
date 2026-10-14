import { Link, Navigate, useParams } from 'react-router-dom';
import { useGet } from '../lib/nav';
import { Badge, EmptyBox, Note, Skel } from '../components/ui';
import { Card, JsonView, PageHead, TabLinks, rowNav } from '../components/blocks';
import Markdown from '../components/Markdown';
import ArmComparison from './ArmComparison';

const KIND_LABEL: Record<string, string> = { challenger: 'Challenger', baseline: 'Baseline', review: 'Review', diagnosis: 'Diagnosis', watchlist: 'Watchlist', other: 'Notes' };
const verdictCls = (v?: string | null) => (!v ? 'b-neut' : /NOT SUPPORTED|UNINFORM/i.test(v) ? 'b-neut' : 'b-info');
const ORDER = ['DIAGNOSIS.md', 'PREREG.md', 'RESULTS.md', 'README.md', 'PROVENANCE.md', 'MONITORING.md'];
const slug = (n: string) => n.replace(/\.[^.]+$/, '').toLowerCase();

export function TestingIndex() {
  const d = useGet<any>('/api/research/index').data;
  const exps: any[] = (d && d.experiments) || [];
  const docs: any[] = (d && d.docs) || [];
  return (
    <>
      <PageHead title="Testing" sub="Pre-registered experiments, their verdicts and the raw results behind them — plus the project's reports. Everything here is read-only and reproducible from the repo." />
      <div className="stack">
        <Card title="Experiments" sub="experiments/" flush>
          {!d ? <div style={{ padding: 16 }}><Skel n={5} /></div> : d.__err ? <EmptyBox ico="⚠" msg="Research files unavailable" sub={d.__err} /> : exps.length ? (
            <table><thead><tr><th className="l">Experiment</th><th className="l">Type</th><th className="l">State</th><th className="l">Verdict</th><th>Updated</th></tr></thead><tbody>
              {exps.map((e) => (
                <tr key={e.folder} className="click" {...rowNav(() => { location.hash = `#/testing/${e.folder}`; })}>
                  <td className="l wrap" style={{ maxWidth: 420 }}><div style={{ fontWeight: 600 }}>{e.id !== e.folder ? <span className="mono">{e.id} </span> : null}<span style={{ fontWeight: 500 }}>{e.title.replace(/^CH-\d+\s*/, '').replace(/^[—–-]\s*/, '')}</span></div><div className="dim2 mono" style={{ fontSize: 11 }}>{e.folder}</div></td>
                  <td className="l"><Badge>{KIND_LABEL[e.kind] || e.kind}</Badge></td>
                  <td className="l"><Badge cls={e.has_results ? 'b-fresh' : 'b-stale'}>{e.status}</Badge>{e.prereg_status && <> <Badge cls="b-neut">{e.prereg_status}</Badge></>}</td>
                  <td className="l wrap setup" style={{ maxWidth: 380 }}>{e.verdict ? <span style={{ color: 'var(--text)' }}>{e.verdict}</span> : <span className="dim2">—</span>}</td>
                  <td className="dim2">{(e.updated || '').slice(0, 10)}</td>
                </tr>
              ))}
            </tbody></table>
          ) : <EmptyBox ico="○" msg="No experiments found" />}
        </Card>
        <Card title="Project reports" sub="docs/" flush>
          {docs.map((x) => (
            <Link key={x.name} to={`/testing/doc/${x.name}`} className="list-row"><div><div className="t">{x.title}</div><div className="d mono">{x.name}.md</div></div></Link>
          ))}
          {!docs.length && d && <EmptyBox ico="○" msg="No reports" />}
        </Card>
      </div>
    </>
  );
}

export function TestingDoc() {
  const { name } = useParams();
  const d = useGet<any>(name ? `/api/research/doc/${name}` : null).data;
  return (
    <>
      <PageHead crumbs={<><Link to="/testing">Testing</Link> / Report</>} title={d?.title || name} />
      <Card>{!d ? <Skel n={10} /> : d.__err ? <EmptyBox ico="⚠" msg="Report not found" /> : d.markdown ? <Markdown text={d.markdown} /> : <Note>This report is too large to show here.</Note>}</Card>
    </>
  );
}

export function TestingDetail() {
  const { folder, tab } = useParams();
  const d = useGet<any>(folder ? `/api/research/experiment/${folder}` : null).data;
  if (!d) return <><PageHead crumbs={<Link to="/testing">Testing</Link>} title={folder} /><Skel n={10} /></>;
  if (d.__err) return <><PageHead crumbs={<Link to="/testing">Testing</Link>} title={folder} /><EmptyBox ico="⚠" msg="Experiment not found" /></>;

  const docNames = Object.keys(d.documents).sort((a, b) => (ORDER.indexOf(a) < 0 ? 99 : ORDER.indexOf(a)) - (ORDER.indexOf(b) < 0 ? 99 : ORDER.indexOf(b)) || a.localeCompare(b));
  const jsonNames = Object.keys(d.json);
  const cmpName = jsonNames.find((n) => d.json[n] && d.json[n].kind === 'arm_comparison');
  const tabs = [...(cmpName ? [{ key: 'comparison', label: 'Comparison', kind: 'comparison', file: cmpName }] : []), ...docNames.map((n) => ({ key: slug(n), label: n === 'PREREG.md' ? 'Pre-registration' : n === 'RESULTS.md' ? 'Results' : n.replace(/\.[^.]+$/, '').replace(/_/g, ' '), kind: 'doc', file: n })),
    ...(jsonNames.length ? [{ key: 'data', label: 'Raw data', kind: 'data', file: '' }] : []), { key: 'files', label: 'Files', kind: 'files', file: '' }];
  const def = (cmpName ? 'comparison' : d.has_results ? 'results' : tabs[0]?.key) || 'files';
  if (!tab) return <Navigate to={`/testing/${folder}/${def}`} replace />;
  const cur = tabs.find((t) => t.key === tab) || tabs[0];

  return (
    <>
      <PageHead crumbs={<><Link to="/testing">Testing</Link> / {KIND_LABEL[d.kind] || 'Notes'}</>} title={<>{d.id !== d.folder && <span className="mono accent">{d.id} </span>}{d.title.replace(/^CH-\d+\s*/, '').replace(/^[—–-]\s*/, '')}</>}
        sub={d.verdict ? <><b style={{ color: 'var(--text)' }}>Verdict:</b> {d.verdict}</> : undefined}
        right={<><Badge cls={d.has_results ? 'b-fresh' : 'b-stale'}>{d.status}</Badge>{d.prereg_status && <Badge cls={verdictCls(d.verdict)}>{d.prereg_status}</Badge>}</>} />
      <TabLinks items={tabs.map((t) => ({ to: `/testing/${folder}/${t.key}`, label: t.label }))} />
      {cur.kind === 'comparison' && <ArmComparison data={d.json[cur.file]} />}
      {cur.kind === 'doc' && (
        <Card>{/\.(patch|txt)$/.test(cur.file) ? <pre className="mono" style={{ margin: 0, overflow: 'auto', fontSize: 12 }}>{d.documents[cur.file]}</pre> : <Markdown text={d.documents[cur.file]} />}</Card>
      )}
      {cur.kind === 'data' && (
        <div className="stack">
          <Note>Machine-readable outputs, exactly as written by the experiment scripts. Reproduce with the commands in the Results / Provenance notes.</Note>
          {jsonNames.map((n) => <Card key={n} title={<span className="mono">{n}</span>}><JsonView data={d.json[n]} /></Card>)}
        </div>
      )}
      {cur.kind === 'files' && (
        <Card flush>
          <table><thead><tr><th className="l">File</th><th className="l">In the UI</th></tr></thead><tbody>
            {d.files.map((f: string) => {
              const sk = (d.skipped || []).find((s: any) => s.name === f);
              return <tr key={f}><td className="l">{f}</td><td className="l dim2" style={{ fontFamily: 'var(--sans)' }}>{sk ? sk.reason : 'shown'}</td></tr>;
            })}
          </tbody></table>
        </Card>
      )}
    </>
  );
}
