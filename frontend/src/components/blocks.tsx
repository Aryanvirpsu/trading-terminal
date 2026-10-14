import { type CSSProperties, type ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { setSymbol } from '../lib/snapshot';
import { AnimatedNumber } from './ui';

/* Layout + content blocks shared by every page. Flat, bordered, no effects. */

export function Card({ title, sub, right, flush, scroll, children, style, className = '' }: {
  title?: ReactNode; sub?: ReactNode; right?: ReactNode; flush?: boolean; scroll?: boolean; children?: ReactNode; style?: CSSProperties; className?: string;
}) {
  return (
    <section className={`card ${className}`} style={style}>
      {(title || right) && (
        <header className="card-h">
          {title && <span>{title}</span>}
          {sub && <span className="sub">{sub}</span>}
          <span className="spacer" />
          {right}
        </header>
      )}
      <div className={`card-b${flush ? ' flush' : ''}${scroll ? ' scroll' : ''}`} style={{ flex: 1 }}>{children}</div>
    </section>
  );
}

export function PageHead({ title, sub, crumbs, right }: { title: ReactNode; sub?: ReactNode; crumbs?: ReactNode; right?: ReactNode }) {
  return (
    <div className="page-head">
      <div>{crumbs && <div className="crumbs">{crumbs}</div>}<h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      {right && <div className="flexrow">{right}</div>}
    </div>
  );
}

/** URL-driven tabs (deep-linkable). */
export function TabLinks({ items, sticky }: { items: { to: string; label: ReactNode; mark?: ReactNode }[]; sticky?: boolean }) {
  return (
    <nav className={`tabs${sticky ? ' sticky' : ''}`}>
      {items.map((i) => <NavLink key={i.to} to={i.to} end className={({ isActive }) => (isActive ? 'active' : '')}>{i.label}{i.mark}</NavLink>)}
    </nav>
  );
}
/** State-driven tabs (inside a card or small panel). */
export function TabBtns<T extends string>({ items, value, onChange }: { items: [T, string][]; value: T; onChange: (v: T) => void }) {
  return <div className="tabs">{items.map(([k, l]) => <button key={k} className={k === value ? 'on' : ''} onClick={() => onChange(k)}>{l}</button>)}</div>;
}

export function SymLink({ s }: { s: string }) {
  return <a className="sym-link" href={`#/stock/${encodeURIComponent(s)}`} onClick={() => setSymbol(s)}>{s}</a>;
}

export function Kpi({ l, value, format, sub, tone }: { l: string; value: number | null | undefined; format: (n: any) => string; sub?: ReactNode; tone?: string }) {
  return (
    <div className="kpi">
      <div className="l">{l}</div>
      <div className={`v ${tone || ''}`}><AnimatedNumber value={value} format={format} duration={0.8} /></div>
      {sub != null && <div className="s">{sub}</div>}
    </div>
  );
}

/** Minimal line chart for an equity curve: [{x label, y}] */
export function LineChart({ points, height = 150, color = 'var(--accent)', fmt = (n: number) => n.toFixed(2) }: { points: { x: string; y: number }[]; height?: number; color?: string; fmt?: (n: number) => string }) {
  if (points.length < 2) return <div className="state"><div className="sub">Not enough history yet — a curve needs at least two sessions.</div></div>;
  const W = 600, H = height, P = 6;
  const ys = points.map((p) => p.y); let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi === lo) { hi += 1; lo -= 1; }
  const X = (i: number) => P + (i / (points.length - 1)) * (W - 2 * P);
  const Y = (v: number) => H - P - ((v - lo) / (hi - lo)) * (H - 2 * P);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join(' ');
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block' }}>
        <path d={`${d} L${X(points.length - 1)},${H - P} L${X(0)},${H - P} Z`} fill={color} opacity="0.08" />
        <path d={d} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flexrow mono dim2" style={{ justifyContent: 'space-between', fontSize: 11, marginTop: 4 }}>
        <span>{points[0].x}</span><span>low {fmt(lo)} · high {fmt(hi)}</span><span>{points[points.length - 1].x}</span>
      </div>
    </div>
  );
}

/** Collapsible JSON tree (read-only) for raw experiment results. */
export function JsonView({ data, open = 1 }: { data: any; open?: number }) {
  return <div className="jv">{node(data, 0, open)}</div>;
}
function node(v: any, depth: number, open: number): ReactNode {
  if (v === null) return <span className="z">null</span>;
  if (typeof v === 'string') return <span className="s">"{v.length > 220 ? v.slice(0, 220) + '…' : v}"</span>;
  if (typeof v === 'number') return <span className="n">{v}</span>;
  if (typeof v === 'boolean') return <span className="b">{String(v)}</span>;
  const isArr = Array.isArray(v); const entries = isArr ? v.map((x: any, i: number) => [i, x]) : Object.entries(v);
  if (!entries.length) return <span className="z">{isArr ? '[]' : '{}'}</span>;
  return (
    <details open={depth < open}>
      <summary>{isArr ? `Array(${entries.length})` : `{${entries.length}}`}</summary>
      {entries.slice(0, 200).map(([k, x]: any) => (
        <div key={k} style={{ marginLeft: 14 }}><span className="k">{k}</span>: {node(x, depth + 1, open)}</div>
      ))}
      {entries.length > 200 && <div className="z" style={{ marginLeft: 14 }}>… {entries.length - 200} more</div>}
    </details>
  );
}

/** Render a {name: {stat: value}} object as a table (used for by_strategy / by_sector style payloads). */
export function KeyTable({ obj, first = 'Name' }: { obj: Record<string, any> | null | undefined; first?: string }) {
  const keys = Object.keys(obj || {});
  if (!keys.length) return <div className="note">Nothing recorded yet.</div>;
  const cols = Object.keys(keys.map((k) => obj![k]).find((v) => v && typeof v === 'object') || {}).filter((c) => typeof obj![keys[0]][c] !== 'object');
  return (
    <table><thead><tr><th className="nosort">{first}</th>{cols.map((c) => <th key={c} className="nosort">{c.replace(/_/g, ' ')}</th>)}</tr></thead><tbody>
      {keys.map((k) => <tr key={k}><td>{k}</td>{cols.map((c) => <td key={c}>{fmtCell(obj![k]?.[c])}</td>)}</tr>)}
    </tbody></table>
  );
}
export const fmtCell = (v: any) => (v == null ? '–' : typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(2)) : typeof v === 'object' ? JSON.stringify(v) : String(v));

/* ── icons (stroke, 24px grid) ── */
const P: Record<string, string> = {
  overview: 'M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10',
  scanner: 'M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-4.3-4.3',
  stock: 'M3 17l5-6 4 3 5-8 4 5M3 21h18',
  markets: 'M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  portfolio: 'M3 7h18v13H3zM8 7V4h8v3M3 13h18',
  strategies: 'M4 5h16M4 12h10M4 19h16M17 9l3 3-3 3',
  testing: 'M9 3h6M10 3v6l-5 9a2 2 0 002 3h10a2 2 0 002-3l-5-9V3',
  cloud: 'M7 18a4 4 0 010-8 5.5 5.5 0 0110.6 1.5A3.3 3.3 0 0117 18H7z',
  system: 'M12 15a3 3 0 100-6 3 3 0 000 6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M17.7 6.3l-1.4 1.4M7.7 16.3l-1.4 1.4',
};
export const Icon = ({ n }: { n: keyof typeof P }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={P[n]} /></svg>
);

/** Props that make a clickable <tr> reachable and operable from the keyboard (Tab to focus, Enter / Space to open). */
export const rowNav = (go: () => void) => ({
  onClick: go, tabIndex: 0, role: 'link' as const,
  onKeyDown: (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } },
});
