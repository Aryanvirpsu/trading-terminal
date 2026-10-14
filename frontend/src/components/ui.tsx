import { useEffect, useMemo, useRef, type ReactNode, type CSSProperties } from 'react';
import gsap from 'gsap';
import { ago } from '../lib/format';
import { useSnap } from '../lib/snapshot';

export const reduceMotion = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ── freshness: the shared classifier's vocabulary, rendered ONE way ──
   LIVE / DELAYED / MARKET CLOSED · LAST CLOSE / STALE / REFRESH FAILED / UNKNOWN.
   A widget that is still loading says LOADING — never a freshness state it hasn't earned. */
export const PRES_CLS: Record<string, string> = {
  'LIVE': 'b-fresh', 'DELAYED': 'b-stale', 'MARKET CLOSED · LAST CLOSE': 'b-info', 'STALE · REFRESH FAILED': 'b-critical', 'UNKNOWN': 'b-neut',
};
export const PRES_SHORT: Record<string, string> = { 'MARKET CLOSED · LAST CLOSE': 'CLOSED · LAST', 'STALE · REFRESH FAILED': 'STALE' };

export function Badge({ cls = 'b-neut', title, children, tight, style }: { cls?: string; title?: string; children?: ReactNode; tight?: boolean; style?: CSSProperties }) {
  return <span className={`badge ${cls}${tight ? ' tight' : ''}`} title={title} style={style}>{children}</span>;
}

export function FreshChip({ sec }: { sec: any }) {
  if (!sec) return null;
  if (sec.state === 'loading') return <Badge cls="b-load" title="fetching in the background">LOADING</Badge>;
  if (sec.state === 'error') return <Badge cls="b-critical" title={sec.reason || ''}>FAILED</Badge>;
  if (sec.state === 'unavailable' || sec.state === 'insufficient_history') return <Badge cls="b-neut" title={sec.reason || ''}>N/A</Badge>;
  const f = sec.freshness || {};
  const p = f.presentation || 'UNKNOWN';
  const tip = `${p}${f.age_seconds != null ? ' · ' + ago(f.age_seconds) + ' old' : ''}${f.kind ? '\nwindow: ' + f.kind : ''}${f.label ? '\n' + f.label : ''}${f.critical ? '\nCRITICAL input for a live decision' : ''}`;
  return <Badge cls={PRES_CLS[p] || 'b-neut'} title={tip}>{PRES_SHORT[p] || p}</Badge>;
}

export function MktBadge({ m }: { m: any }) {
  if (!m || !m.state) return <span className="dim">○ —</span>;
  const d = (s: number) => `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
  const t = m.seconds_until_close, o = m.seconds_to_open;
  switch (m.state) {
    case 'open': return <><span className="green">● OPEN</span>{t != null && <span className="dim2"> closes {d(t)}</span>}</>;
    case 'premarket': return <><span className="green">◐ PRE</span>{o != null && <span className="dim2"> opens {d(o)}</span>}</>;
    case 'afterhours': return <><span className="green">◑ AFTER</span>{o != null && <span className="dim2"> opens {d(o)}</span>}</>;
    case 'holiday': return <><span className="dim">○ HOLIDAY</span> <span className="dim2">{m.holiday || ''}</span></>;
    default: return <><span className="dim">○ CLOSED</span>{o != null && <span className="dim2"> opens {d(o)}</span>}</>;
  }
}

export function Skel({ n = 4 }: { n?: number }) {
  const widths = useMemo(() => Array.from({ length: n }, () => 55 + Math.random() * 40), [n]);
  return <>{widths.map((w, i) => <div key={i} className="sk" style={{ width: w + '%' }} />)}</>;
}
export function EmptyBox({ ico, msg, sub, children }: { ico: string; msg: string; sub?: string; children?: ReactNode }) {
  return <div className="state"><div className="ico">{ico}</div><div className="msg">{msg}</div><div className="sub">{sub || ''}</div>{children}</div>;
}
/** The loading / error / unavailable states every section shares; null when the section is ready. */
export function sectionBox(sec: any, label: string): ReactNode | null {
  if (!sec) return <EmptyBox ico="○" msg="No data" />;
  if (sec.state === 'loading') return <EmptyBox ico="◌" msg={`Loading ${label}…`} sub="Computing in the background — the rest of the terminal is unaffected." />;
  if (sec.state === 'error') return <EmptyBox ico="⚠" msg={`${label} unavailable`} sub={sec.reason || 'The provider returned an error. Other widgets are unaffected.'} />;
  if (sec.state === 'unavailable' || sec.state === 'insufficient_history') return <EmptyBox ico="○" msg={`${label} not available`} sub={sec.reason || ''} />;
  return null;
}

export const Kv = ({ k, children, bare }: { k: ReactNode; children?: ReactNode; bare?: boolean }) =>
  <div className={`kv${bare ? ' bare' : ''}`}><span className="k">{k}</span><span className="v">{children}</span></div>;
export const Sec = ({ children, onClick }: { children: ReactNode; onClick?: () => void }) =>
  <div className={`sec${onClick ? ' click' : ''}`} onClick={onClick}>{children}</div>;
export const Note = ({ children, warn, style }: { children?: ReactNode; warn?: boolean; style?: CSSProperties }) =>
  <div className={`note${warn ? ' warn' : ''}`} style={style}>{children}</div>;
export const yn = (b: any) => (b == null ? <span className="dim2">?</span> : b ? <span className="green">✓</span> : <span className="red">✕</span>);

/* ── motion helpers ── */
/** A figure shown EXACTLY as formatted — never tweened through intermediate values (a finance UI must not display
 *  numbers that were never true). When the value genuinely changes, it gets a brief green/red highlight. */
export function AnimatedNumber({ value, format, className, style }: { value: number | null | undefined; format: (n: any) => string; duration?: number; className?: string; style?: CSSProperties }) {
  const el = useRef<HTMLSpanElement>(null);
  const prev = useRef<number | null>(null);
  useEffect(() => {
    const node = el.current;
    if (node && value != null && prev.current != null && Math.abs(value - prev.current) >= 0.005) flashText(node, value > prev.current ? 1 : -1);
    prev.current = value == null || value !== value ? null : value;
  }, [value]);
  return <span ref={el} className={className} style={{ borderRadius: 3, ...style }}>{format(value)}</span>;
}

/** Brief green/red glow when a value ticks. Never animates the digits themselves. */
export function flash(el: HTMLElement | null, dir: 1 | -1) {
  if (!el || reduceMotion()) return;
  const c = dir > 0 ? '52,245,161' : '255,93,122';
  gsap.fromTo(el, { boxShadow: `0 0 0 0 rgba(${c},.7), inset 0 0 22px rgba(${c},.3)` }, { boxShadow: `0 0 0 14px rgba(${c},0), inset 0 0 0 rgba(${c},0)`, duration: 0.9, ease: 'power2.out', clearProps: 'boxShadow' });
}
export function flashCell(el: HTMLElement | null, dir: 1 | -1) {
  if (!el || reduceMotion()) return;
  const c = dir > 0 ? '52,245,161' : '255,93,122';
  gsap.fromTo(el, { backgroundColor: `rgba(${c},.28)` }, { backgroundColor: `rgba(${c},0)`, duration: 1.1, ease: 'power2.out', clearProps: 'backgroundColor' });
}

/** Section state box for a widget: skeleton while there is no snapshot at all, else the shared loading/error states. */
export function useBox(name: string, label: string): { sec: any; box: ReactNode | null } {
  const snap = useSnap((s) => s.data);
  const sec = snap && snap.sections ? snap.sections[name] : undefined;
  if (!snap) return { sec, box: <Skel n={6} /> };
  return { sec, box: sectionBox(sec, label) };
}

export function flashText(el: HTMLElement | null, dir: 1 | -1) {
  if (!el || reduceMotion()) return;
  const c = dir > 0 ? '47,211,127' : '240,86,106';
  gsap.fromTo(el, { backgroundColor: `rgba(${c},.24)` }, { backgroundColor: `rgba(${c},0)`, duration: 1.1, ease: 'power2.out', clearProps: 'backgroundColor' });
}
