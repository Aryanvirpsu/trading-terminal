import { ago, num, us } from '../../lib/format';
import { useSec } from '../../lib/snapshot';
import { useWS } from '../../lib/store';
import { Badge, EmptyBox, Kv, Note, Sec, useBox } from '../ui';

export const SENT_CLS: any = { bullish: 'b-bull', bearish: 'b-bear', neutral: 'b-neut', no_data: 'b-neut' };
export const DIR_CLS: any = { bullish: 'green', bearish: 'red', neutral: 'dim', no_data: 'dim2' };
const pol = (p: number) => (p > 0.15 ? ['b-bull', 'BULL'] : p < -0.15 ? ['b-bear', 'BEAR'] : ['b-neut', 'NEUT']);

export function Headline({ i, extra }: { i: any; extra?: boolean }) {
  const [c, l] = pol(i.polarity);
  return (
    <div className="nrow">
      <div className="nt">{i.url ? <a href={i.url} target="_blank" rel="noopener">{i.title}</a> : i.title}</div>
      <div className="flexrow" style={{ marginTop: 4 }}>
        <Badge cls={c} tight>{l}</Badge>
        <span className={`tierchip t-${i.tier}`} title={i.tier_reason || ''}>{String(i.tier).replace('INDIRECT_', '')}</span>
        <span className="dim2" style={{ fontSize: 9 }}>{i.source || ''}</span>
        {i.age_hours != null && <span className="dim2" style={{ fontSize: 9 }}>· {ago(i.age_hours * 3600)}</span>}
        <span className="dim2" style={{ fontSize: 9 }} title="weight this headline carries in the aggregate">· rel {Math.round((i.relevance || 0) * 100)}%</span>
        {extra && i.catalyst && <Badge cls="b-info" tight title={`${i.catalyst.type} · ${i.catalyst.importance}`}>CATALYST</Badge>}
        {extra && i.duplicates ? <span className="dim2" style={{ fontSize: 9 }} title="syndicated copies collapsed into this one">· ×{i.duplicates + 1}</span> : null}
      </div>
    </div>
  );
}

export function Catalysts({ cd, earn, cats, trailing }: { cd: any; earn: any; cats: any[]; trailing: string }) {
  return (
    <>
      {earn && earn.date && <Kv k="Next earnings">{earn.date}{earn.days_away != null && <span className="dim2"> · {earn.days_away}d</span>}</Kv>}
      {cats.length ? cats.map((x, i) => (
        <div key={i}>
          <Kv k={<><Badge cls={x.direction === 'bullish' ? 'b-bull' : x.direction === 'bearish' ? 'b-bear' : 'b-neut'} tight>{String(x.direction).slice(0, 4).toUpperCase()}</Badge> {us(x.type)}</>}>
            <span className="dim2" style={{ fontSize: 9.5 }}>{x.importance}{x.age_hours != null ? ' · ' + ago(x.age_hours * 3600) : ''}</span>
          </Kv>
          <div className="note" style={{ margin: '-1px 0 5px' }} title={x.headline || ''}>{(x.headline || '').slice(0, 90)}</div>
        </div>
      )) : <Note>{trailing}</Note>}
    </>
  );
}

export default function News() {
  const { sec, box } = useBox('news', 'news');
  const cat = useSec('catalysts');
  const symbol = useWS((s) => s.symbol);
  if (box) return <>{box}</>;
  const d = sec.data || {}, items = d.items || [];
  const cd = (cat || {}).data || {};
  const earn = cd.earnings, cats = cd.catalysts || [];
  const c = d.counts || {}, div = d.source_diversity || {}, nf = d.news_freshness || {};
  return (
    <>
      <div className="flexrow mb4">
        <Badge cls={SENT_CLS[d.direction] || 'b-neut'}>{(d.direction || 'no data').toUpperCase()}</Badge>
        <b className={`mono ${DIR_CLS[d.direction] || 'dim'}`} style={{ fontSize: 14 }}>{d.sentiment_score != null ? (d.sentiment_score >= 0 ? '+' : '') + num(d.sentiment_score, 2) : '—'}</b>
        <span className="spacer" />
        <span className="dim2" style={{ fontSize: 9.5 }} title="earned from agreement, sample, independent sourcing and recency">conf {d.confidence != null ? Math.round(d.confidence * 100) + '%' : '—'}</span>
      </div>
      <Note style={{ marginBottom: 6 }}>
        {d.relevant_count || 0} relevant · {d.direct_count || 0} directly about the company
        {nf.newest_hours != null && ` · newest ${nf.newest_hours < 1 ? Math.round(nf.newest_hours * 60) + 'm' : num(nf.newest_hours, 1) + 'h'}`}
        {div.sources ? ` · ${div.sources} source${div.sources > 1 ? 's' : ''}` : ''}
        {d.article_count ? <span className="dim2"> ({d.article_count} fetched)</span> : null}
      </Note>
      <div className="flexrow" style={{ marginBottom: 6, fontSize: 10.5 }}>
        <span className="green">Positive {c.positive || 0}</span><span className="dim">Neutral {c.neutral || 0}</span><span className="red">Negative {c.negative || 0}</span>
        <span className="spacer" />
        <span className="dim2" style={{ fontSize: 9 }} title="how the fetched items split by relevance to this company">
          {Object.entries(d.tier_breakdown || {}).filter(([, v]) => v).map(([k, v]) => k.replace('INDIRECT_', '') + ' ' + v).join(' · ')}
        </span>
      </div>
      {d.reason && <Note warn style={{ marginBottom: 6 }}>{d.reason}</Note>}
      <Sec>Catalysts <span className="dim2" style={{ textTransform: 'none', letterSpacing: 0 }}>— events, not tone</span></Sec>
      <Catalysts cd={cd} earn={earn} cats={cats} trailing={earn && earn.date ? 'No event beyond the scheduled earnings date.' : 'No identifiable event — drift, not a catalyst.'} />
      <Sec>Headlines</Sec>
      {items.length ? items.map((i: any, k: number) => <Headline key={k} i={i} extra />) : <EmptyBox ico="○" msg="No headlines" sub={`Nothing recent for ${symbol}.`} />}
      <Note style={{ marginTop: 8 }}>Sentiment is the TONE of coverage; catalysts are EVENTS. They are scored separately and never averaged.{d.backend ? ' Model: ' + d.backend + '.' : ''}</Note>
    </>
  );
}
