import { money, num } from '../../lib/format';
import { useWS } from '../../lib/store';
import { useSec } from '../../lib/snapshot';
import { Badge, Kv, Note, Sec, useBox, AnimatedNumber } from '../ui';

export default function Risk() {
  const { sec, box } = useBox('risk', 'risk');
  const pos = useSec('position'), decS = useSec('decision');
  const { account, symbol, set } = useWS();
  if (box) return <>{box}</>;
  const d = sec.data || {}, pa = d.paper || {}, br = d.broker || {};
  const dec = (decS || {}).data || {};
  const sz = dec.sizing || {};
  const opt = (sz.option || {}).risk || {}, sh = (sz.shares || {}).risk || {};
  const mine = ((pos || {}).data || {}).paper_positions || [];
  const util = pa.capital_utilization_pct;
  const stat = (l: string, v: any, s?: string) => (
    <div className="stat"><div className="l">{l}</div><div className="v"><AnimatedNumber value={v} format={money} /></div>{s && <div className="s">{s}</div>}</div>
  );
  return (
    <>
      <div className="flexrow mb8">
        <div className="seg" title="Which account this panel describes">
          {([['paper', 'Paper · simulated'], ['cash', 'Broker · cash'], ['robinhood', 'Broker · RH']] as const).map(([k, l]) =>
            <button key={k} className={`${account === k ? 'on' : ''}${k !== 'paper' ? ' cash' : ''}`} onClick={() => set({ account: k })}>{l}</button>)}
        </div>
        <span className="spacer" /><Badge cls="b-info">READ ONLY</Badge>
      </div>
      <div className="g4 mb6" style={{ gridTemplateColumns: 'repeat(2,1fr)' }}>
        {stat('Paper equity', pa.equity)}{stat('Buying power', d.buying_power, 'broker')}{stat('Exposure', d.exposure)}{stat('Capacity left', d.remaining_capacity)}
      </div>
      {util != null && (<>
        <Kv k="Capital utilisation">{num(util, 1)}%</Kv>
        <div className="hbar"><i style={{ width: Math.min(100, util) + '%', background: util > 80 ? 'var(--red)' : util > 50 ? 'var(--amber)' : 'var(--green)', color: util > 80 ? 'var(--red)' : util > 50 ? 'var(--amber)' : 'var(--green)' }} /></div>
      </>)}
      <Sec>Planned trade risk · {symbol}</Sec>
      <table><thead><tr><th className="nosort">Leg</th><th className="nosort">Capital</th><th className="nosort">Planned</th><th className="nosort">Stress</th><th className="nosort">Max</th></tr></thead><tbody>
        <tr><td>Shares</td><td>{money(sh.capital_committed)}</td><td className="amber">{money(sh.planned_risk)}</td><td className="amber">{money(sh.stress_risk)}</td><td className="red">{money(sh.absolute_max_loss)}</td></tr>
        <tr><td>Option</td><td>{money(opt.capital_committed)}</td><td className="amber">{money(opt.planned_risk)}</td><td className="amber">{money(opt.stress_risk)}</td><td className="red">{money(opt.absolute_max_loss)}</td></tr>
      </tbody></table>
      {(sz.option || {}).status && <Note style={{ marginTop: 5 }}>Option: {sz.option.status}{(sz.option.eligibility || {}).reason ? ' — ' + sz.option.eligibility.reason : ''}</Note>}
      <Sec>Open position · {symbol}</Sec>
      {mine.length ? mine.map((p: any, i: number) => (
        <div key={i} className="g2">
          <Kv k="Qty">{num(p.quantity, 4)}</Kv><Kv k="Entry">{num(p.avg_entry, 2)}</Kv>
          <Kv k="Stop"><span className="red">{num(p.stop, 2)}</span></Kv><Kv k="Target"><span className="green">{num(p.target, 2)}</span></Kv>
          <Kv k="Planned risk">{money(p.planned_risk)}</Kv><Kv k="Strategy"><span style={{ fontSize: 10 }}>{p.strategy || ''}</span></Kv>
        </div>
      )) : <Note>No open position in {symbol}.</Note>}
      {(pa.open_positions || 0) > 0 && <Note style={{ marginTop: 5 }}>Portfolio: {pa.open_positions} open · realised {money(pa.realized_pnl)} · drawdown {num(pa.drawdown_pct, 2)}%</Note>}
      {(d.open_option_positions || []).length > 0 && (<><Sec>Broker option exposure</Sec><Note>{d.open_option_positions.length} open option position(s)</Note></>)}
      {br.account_masked && <Note style={{ marginTop: 6 }}>Broker {br.account_masked} · {br.source || ''}</Note>}
    </>
  );
}
