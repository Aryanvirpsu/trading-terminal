import { useEffect, useRef, useState } from 'react';
import { ChartEngine } from '../../lib/chartEngine';
import { useWS } from '../../lib/store';
import { Snap, chartLiveState, useSec, useSnap, useStatus } from '../../lib/snapshot';
import { DL } from '../../lib/api';
import { Badge, useBox } from '../ui';

const TFS = ['1D', '5D', '1M', '3M', '6M', '1Y'];

export default function ChartWidget() {
  const { sec, box } = useBox('chart', 'chart');
  const quote = useSec('quote'), levels = useSec('levels');
  const { tf, chartMA, ovLevels, symbol, set } = useWS();
  const snap = useSnap((s) => s.data), status = useStatus((s) => s.data);
  const ls = chartLiveState(snap, status);
  const host = useRef<HTMLDivElement>(null);
  const [eng, setEng] = useState<ChartEngine | null>(null);
  const ready = !box;

  // The engine owns the canvas; it exists only while the section is ready.
  useEffect(() => {
    if (!ready || !host.current) return;
    const e = new ChartEngine(host.current); setEng(e);
    return () => { e.destroy(); setEng(null); };
  }, [ready]);
  useEffect(() => { eng?.configure({ ma: chartMA, showLevels: ovLevels !== false, levels: levels?.data || null, isLive: ls.live }); }, [eng, chartMA, ovLevels, levels, ls.live]);
  useEffect(() => { eng?.resetLive(); }, [eng, symbol]);
  useEffect(() => { if (eng && sec?.state === 'ok') eng.setData(sec.data.points || [], sec.data, `${symbol}|${tf}`); }, [eng, sec, symbol, tf]);
  useEffect(() => { if (eng && quote?.data) eng.setLive(quote.data); }, [eng, quote, sec]);

  const pickTf = (t: string) => {
    // Changing timeframe fetches the appropriate history — that IS a new dataset.
    set({ tf: t }); DL.drop('/api/terminal/symbol'); Snap.load(symbol);
  };
  const toggleMA = (p: number) => set({ chartMA: { ...chartMA, [p]: !chartMA[p] } });

  return (
    <div className="chart-wrap">
      <div className="flexrow mb4">
        {TFS.map((t) => <button key={t} className={`pill-btn${tf === t ? ' on' : ''}`} onClick={() => pickTf(t)}>{t}</button>)}
        <Badge cls={ls.cls} title="Derived from the market calendar and the quote's own freshness, never from a bare price">{ls.label}</Badge>
        <span className="spacer" />
        {[20, 50, 200].map((p) => <button key={p} className={`pill-btn sm${chartMA[p] ? ' on' : ''}`} onClick={() => toggleMA(p)}>{p}</button>)}
        <button className={`pill-btn sm${ovLevels !== false ? ' on' : ''}`} onClick={() => set({ ovLevels: ovLevels === false })}>levels</button>
        <button className="pill-btn sm" title="Auto scale (or double-click the chart)" onClick={() => eng?.autoScale()}>auto</button>
      </div>
      {box ? box : <div className="chost" ref={host} />}
    </div>
  );
}
