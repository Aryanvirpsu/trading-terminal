import { useState } from 'react';
import { kmb, money, num } from '../../lib/format';
import { useUI, useWS } from '../../lib/store';
import { useSec } from '../../lib/snapshot';
import { Badge, EmptyBox, Note, useBox } from '../ui';
import { rowNav } from '../blocks';

export function contractDetail(c: any) {
  const skip = new Set(['symbol', 'side']);
  return Object.entries(c).filter(([k, v]) => !skip.has(k) && v != null && typeof v !== 'object');
}

export default function Options() {
  const { sec, box } = useBox('options', 'options');
  const decS = useSec('decision');
  const { optExp, symbol, set } = useWS();
  const openContract = useUI((s) => s.openContract);
  const [sort, setSort] = useState<{ k: string; dir: number }>({ k: 'strike', dir: 1 });
  if (box) return <>{box}</>;
  const d = sec.data || {};
  const chain: any[] = d.chain || [];
  const exps = [...new Set(chain.map((c) => c.expiry).filter(Boolean))] as string[];
  const exp = optExp && exps.includes(optExp) ? optExp : exps[0] || null;
  const best = d.best_contract;
  const dec = (decS || {}).data || {};
  const rows = chain.filter((c) => !exp || c.expiry === exp).sort((a, c) => {
    const x = a[sort.k], y = c[sort.k];
    return ((x == null ? -1e9 : x) - (y == null ? -1e9 : y)) * sort.dir;
  });
  const provNote = (d.provider || {}).contracts;
  const th = (k: string, label: string) => <th onClick={() => setSort((s) => (s.k === k ? { k, dir: -s.dir } : { k, dir: 1 }))}>{label}</th>;
  const cell = (l: string, v: any) => <div><div className="l">{l}</div><div className="mono">{v}</div></div>;
  return (
    <>
      {best ? (
        <div className="bestbox">
          <div className="flexrow">
            <b className="mono">{best.expiry || ''} ${num(best.strike, 2)} {(best.side || 'call').toUpperCase()}</b>
            <Badge cls={best.tradeable ? 'b-bull' : 'b-bear'}>{best.tradeable ? 'PASSES GATES' : 'REJECTED'}</Badge>
            <span className="spacer" />
            <Badge cls={dec.instrument === 'OPTION PREFERRED' ? 'b-bull' : 'b-neut'} title="The final instrument verdict, which is a different question from contract quality or suitability">{dec.instrument || '—'}</Badge>
          </div>
          <div className="g4" style={{ marginTop: 8, rowGap: 8 }}>
            {cell('BID / ASK', `${num(best.bid, 2)} / ${num(best.ask, 2)}`)}{cell('SPREAD', num(best.spread_pct, 1) + '%')}
            {cell('DELTA', num(best.delta, 3))}{cell('THETA', num(best.theta, 3))}
            {cell('DTE', best.dte ?? '–')}{cell('OI', kmb(best.open_interest))}
            {cell('VOLUME', kmb(best.volume))}{cell('IV', best.implied_volatility != null ? (best.implied_volatility * 100).toFixed(0) + '%' : '–')}
            {cell('BREAK-EVEN', num(best.break_even, 2))}{cell('MOVE NEEDED', num(best.pct_move_to_break_even, 1) + '%')}
            {cell('P(PROFIT)', best.chance_of_profit_long != null ? (best.chance_of_profit_long * 100).toFixed(0) + '%' : '–')}{cell('PREMIUM', money((best.limit_price || 0) * 100))}
          </div>
          {best.greeks_provenance === 'model' && <Note style={{ marginTop: 5 }}>Greeks are modelled (Black-Scholes), not provider-supplied.</Note>}
        </div>
      ) : <Note style={{ marginBottom: 6 }}>No contract passed the liquidity gates{d.contracts_analysed ? ` (${d.contracts_analysed} analysed)` : ''}.</Note>}
      <div className="flexrow mb4">
        <span className="dim2" style={{ fontSize: 9 }}>EXPIRY</span>
        <select className="mini" value={exp || ''} onChange={(e) => set({ optExp: e.target.value })}>{exps.length ? exps.map((e) => <option key={e}>{e}</option>) : <option>—</option>}</select>
        <span className="dim2" style={{ fontSize: 9 }} title="counts for the expiry shown, then across every expiry analysed">
          {rows.length} here · {rows.filter((c) => c.tradeable).length} pass <span style={{ opacity: 0.6 }}>({d.contracts_analysed || chain.length} / {d.contracts_passing || 0} all exp)</span>
        </span>
        <span className="spacer" />
        {provNote && <Badge cls="b-neut" title="chain provider">{provNote}</Badge>}
        {d.session_open === false && <Badge cls="b-info" title="Option volume is 0 chain-wide outside the session, so open interest carries the liquidity check">closed session</Badge>}
      </div>
      {rows.length ? (
        <table><thead><tr>
          {th('strike', 'Strike')}{th('bid', 'Bid')}{th('ask', 'Ask')}{th('spread_pct', 'Spr%')}{th('delta', 'Δ')}{th('theta', 'Θ')}
          {th('implied_volatility', 'IV')}{th('open_interest', 'OI')}{th('volume', 'Vol')}<th className="nosort">OK</th>
        </tr></thead><tbody>
          {rows.map((c, i) => (
            <tr key={i} className={`click${best && c.strike === best.strike && c.expiry === best.expiry ? ' sel' : ''}`}
              {...rowNav(() => openContract(`${symbol} ${c.expiry} $${c.strike} ${(c.side || 'call').toUpperCase()}`, c))}>
              <td>{num(c.strike, 2)}</td><td>{num(c.bid, 2)}</td><td>{num(c.ask, 2)}</td>
              <td className={(c.spread_pct || 0) <= 5 ? 'green' : 'amber'}>{num(c.spread_pct, 1)}</td>
              <td>{num(c.delta, 2)}</td><td>{num(c.theta, 3)}</td>
              <td>{c.implied_volatility != null ? (c.implied_volatility * 100).toFixed(0) : '–'}</td>
              <td>{kmb(c.open_interest)}</td><td>{kmb(c.volume)}</td>
              <td>{c.tradeable ? <span className="green">✓</span> : <span className="dim2" title={c.rejection || ''}>✕</span>}</td>
            </tr>
          ))}
        </tbody></table>
      ) : <EmptyBox ico="○" msg="No chain for this expiry" />}
      {d.earnings && d.earnings.date && <Note style={{ marginTop: 6 }}>Earnings {d.earnings.date}{d.earnings.days_away != null ? ` · ${d.earnings.days_away}d away` : ''}</Note>}
    </>
  );
}
