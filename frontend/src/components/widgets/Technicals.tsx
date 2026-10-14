import { cls, dollarsC, kmb, num, us } from '../../lib/format';
import { useSec } from '../../lib/snapshot';
import { Badge, Kv, Note, Sec, useBox, yn } from '../ui';

export default function Technicals() {
  const { sec, box } = useBox('technicals', 'technicals');
  const lvS = useSec('levels');
  if (box) return <>{box}</>;
  const t = sec.data || {}, tr = t.trend || {}, lv = (lvS || {}).data || {};
  const rs = t.relative_strength || {};
  return (
    <>
      <div className="g3">
        <div>
          <Sec>Trend</Sec>
          <Kv k="20-DMA">{yn(tr.above_sma20)} {num(tr.sma20, 2)}</Kv>
          <Kv k="50-DMA">{yn(tr.above_sma50)} {num(tr.sma50, 2)}</Kv>
          <Kv k="200-DMA">{yn(tr.above_sma200)} {num(tr.sma200, 2)}</Kv>
          <Kv k="RSI(14)">{num(t.rsi14, 1)}</Kv>
          <Kv k="ATR(14)">{num(t.atr14, 2)} <span className="dim2">{num(t.atr_pct, 1)}%</span></Kv>
          <Kv k="Realized vol 20d">{num(t.realized_vol_20d, 0)}%</Kv>
        </div>
        <div>
          <Sec>Levels</Sec>
          <Kv k="Support (20d low)">{num(t.support, 2)}</Kv>
          <Kv k="Resistance (20d high)">{num(t.resistance, 2)}</Kv>
          <Kv k="Entry"><span className="accent">{lv.entry_zone ? num(lv.entry_zone[0], 2) + '–' + num(lv.entry_zone[1], 2) : '–'}</span></Kv>
          <Kv k="Invalidation"><span className="red">{num(lv.invalidation, 2)}</span></Kv>
          <Kv k="Target 1 / 2"><span className="green">{num(lv.target_1, 2)} / {num(lv.target_2, 2)}</span></Kv>
          <Kv k="Do not chase above">{num(lv.do_not_chase_above, 2)}</Kv>
        </div>
        <div>
          <Sec>Participation</Sec>
          <Kv k="Rel volume">{num(t.rel_volume, 2)}x</Kv>
          <Kv k="Avg vol 20d">{kmb(t.avg_volume_20d)}</Kv>
          <Kv k="Turnover 20d">{dollarsC(t.dollar_volume_20d)}</Kv>
          <Kv k="Range position">{num(t.pos_in_20d_range, 0)}%</Kv>
          <Kv k="5d / 20d / 60d"><span className={cls(t.chg_5d)}>{num(t.chg_5d, 1)}</span> / <span className={cls(t.chg_20d)}>{num(t.chg_20d, 1)}</span> / <span className={cls(t.chg_60d)}>{num(t.chg_60d, 1)}</span></Kv>
        </div>
      </div>
      <div className="flexrow mt6">
        {(t.setups || []).length
          ? t.setups.map((s: any, i: number) => <Badge key={i} cls="b-info" title={s.evidence || ''}>{us(s.type)} {num(s.strength, 0)}</Badge>)
          : <Note>No setup category matched — the evidence for every pattern was absent.</Note>}
      </div>
      {rs.note && <Note style={{ marginTop: 5 }}>Relative strength: {rs.note}</Note>}
      {lv.invalidation_basis && <Note>Stop basis: {lv.invalidation_basis} · target 1 basis: {lv.target_1_basis || ''}</Note>}
      {(t.missing || []).length > 0 && <Note>Missing inputs: {t.missing.join(', ')} — scored as absent, never substituted.</Note>}
    </>
  );
}
