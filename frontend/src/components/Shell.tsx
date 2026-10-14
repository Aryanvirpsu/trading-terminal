import { useEffect, useRef, useState, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import ErrorBoundary from './ErrorBoundary';
import { DL, useNet } from '../lib/api';
import { useCloudMeta } from '../lib/nav';
import { num } from '../lib/format';
import { openStock } from '../lib/nav';
import { Snap, useStatus } from '../lib/snapshot';
import { useUI, useWS } from '../lib/store';
import { Icon } from './blocks';
import { Kv, MktBadge } from './ui';
import { contractDetail } from './widgets/Options';

function Search() {
  const recent = useWS((s) => s.recent);
  const [q, setQ] = useState('');
  const [items, setItems] = useState<any[]>([]);
  const [heading, setHeading] = useState('');
  const [sel, setSel] = useState(-1);
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const timer = useRef<any>(null);

  const showRecent = async () => {
    const r = (recent || []).map((s) => ({ symbol: s, name: '' }));
    if (r.length) { setItems(r); setSel(0); setHeading('Recent'); setOpen(true); return; }
    const d = await DL.get('/api/search/popular');
    setItems((d && d.results) || []); setSel(0); setHeading('Popular'); setOpen(true);
  };
  const onInput = (v: string) => {
    setQ(v); clearTimeout(timer.current);
    if (!v.trim()) { showRecent(); return; }
    timer.current = setTimeout(async () => {
      const d = await DL.get('/api/search?q=' + encodeURIComponent(v.trim()) + '&limit=10');
      const r = (d && d.results) || []; setItems(r); setSel(r.length ? 0 : -1); setHeading('Results'); setOpen(true);
    }, 110);
  };
  const pick = (s: string) => { setQ(''); setOpen(false); input.current?.blur(); openStock(s); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setOpen(false); input.current?.blur(); return; }
    if (e.key === 'Enter' && q.trim() && !items.length) { pick(q.trim()); e.preventDefault(); return; }
    if (!items.length) return;
    if (e.key === 'ArrowDown') { setSel((sel + 1) % items.length); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { setSel((sel - 1 + items.length) % items.length); e.preventDefault(); }
    else if (e.key === 'Enter') { const it = items[sel] || items[0]; if (it) pick(it.symbol); e.preventDefault(); }
  };
  useEffect(() => {
    const h = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('click', h); return () => document.removeEventListener('click', h);
  }, []);
  useEffect(() => { (window as any).__focusSearch = () => { input.current?.focus(); input.current?.select(); }; }, []);

  return (
    <div id="searchWrap" ref={wrap}>
      <span className="mag">⌕</span>
      <input id="search" ref={input} value={q} placeholder="Open a stock…  PLTR, AAPL, BRK.B" autoComplete="off" spellCheck={false}
        onChange={(e) => onInput(e.target.value)} onFocus={() => { if (!q.trim()) showRecent(); }} onKeyDown={onKey} />
      <span className="kbd">/</span>
      {open && items.length > 0 && (
        <div id="searchResults">
          {heading && <div className="rhead">{heading}</div>}
          {items.map((it, i) => (
            <div key={it.symbol + i} className={`r${i === sel ? ' sel' : ''}`} onClick={() => pick(it.symbol)} onMouseEnter={() => setSel(i)}>
              <span className="rl"><b className="mono">{it.symbol}</b> <span className="dim">{it.name || ''}</span></span>
              <span className="dim2" style={{ fontSize: 10 }}>{it.exchange || ''} {it.type ? <span className="tt">{String(it.type).slice(0, 4).toUpperCase()}</span> : null}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const NAV: { group: string; items: { to: string; label: string; icon: any; end?: boolean }[] }[] = [
  { group: 'Trade', items: [
    { to: '/', label: 'Overview', icon: 'overview', end: true },
    { to: '__stock', label: 'Stock', icon: 'stock' },
    { to: '/scanner', label: 'Scanner', icon: 'scanner' },
    { to: '/markets', label: 'Markets', icon: 'markets' },
    { to: '/portfolio', label: 'Portfolio', icon: 'portfolio' },
    { to: '/cloud', label: 'Cloud runtime', icon: 'cloud' },
  ] },
  { group: 'Research', items: [
    { to: '/strategies', label: 'Strategies', icon: 'strategies' },
    { to: '/testing', label: 'Testing', icon: 'testing' },
  ] },
  { group: 'Admin', items: [{ to: '/system', label: 'System', icon: 'system' }] },
];

function Sidebar() {
  const symbol = useWS((s) => s.symbol);
  return (
    <aside className="sidebar">
      <div className="brand"><div className="mark">T</div><div><span className="name">Terminal</span><small>$500 paper account</small></div></div>
      <nav className="nav">
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="nav-label">{g.group}</div>
            {g.items.map((i) => i.to === '__stock' ? (
              <NavLink key={i.to} to={`/stock/${symbol}`} title="Stock" className={({ isActive }) => (isActive ? 'active' : '')}>
                <Icon n="stock" /><span>Stock</span><span className="tag">{symbol}</span>
              </NavLink>
            ) : (
              <NavLink key={i.to} to={i.to} end={i.end} title={i.label} className={({ isActive }) => (isActive ? 'active' : '')}><Icon n={i.icon} /><span>{i.label}</span></NavLink>
            ))}
          </div>
        ))}
      </nav>
      <div className="side-foot"><b>Paper only · read-only</b><br />Live execution is off. Nothing here can place an order.</div>
    </aside>
  );
}

function LoadBar() {
  const busy = useNet((s) => s.busy);
  return <div className={`loadbar${busy ? ' on' : ''}`} role="progressbar" aria-hidden={!busy} />;
}

function Backend() {
  const down = useNet((s) => s.down);
  return down ? <span className="badge b-critical" title="The API is not responding. Start it with: python scripts/run_terminal.py">BACKEND UNREACHABLE</span> : null;
}

function Topbar() {
  const status = useStatus((s) => s.data);
  const { source, set } = useWS();
  return (
    <header className="topbar">
      <Search />
      <Backend />
      <span className="spacer" />
      <div className="tb-item"><span className="lab">Market</span><span className="val"><MktBadge m={(status || {}).market} /></span></div>
      <span className="seg-label">Ledger</span>
      <div className="seg" title="Which paper ledger Overview, Portfolio and Strategies describe. Local = this machine (Case 2). Cloud = read-only mirror of the cloud runtime (Case 1). They are separate and never merged.">
        {([['local', 'Local'], ['cloud', 'Cloud']] as const).map(([k, l]) =>
          <button key={k} className={`${source === k ? 'on' : ''}${k === 'cloud' ? ' cloud' : ''}`} onClick={() => set({ source: k })}>{l}</button>)}
      </div>
    </header>
  );
}

// Only the pages that actually describe a paper ledger show which one: Overview, Portfolio, Strategies and Cloud runtime.
const LEDGER_PAGES = ['', 'portfolio', 'strategies', 'cloud'];

function SourceBanner() {
  const source = useWS((s) => s.source);
  const section = useLocation().pathname.split('/')[1] || '';
  const meta = useCloudMeta().data;
  if (source !== 'cloud' || !LEDGER_PAGES.includes(section)) return null;
  const ok = meta && meta.state === 'ok';
  const h = ok && meta.backup_age_seconds != null ? Math.round(meta.backup_age_seconds / 3600) : null;
  return (
    <div className="banner">
      <b>CLOUD MIRROR · CASE 1</b>
      {ok ? <span>Account, positions, performance and signals are a <b style={{ color: 'var(--text)' }}>read-only copy</b> of the cloud ledger as of its last backup ({meta.backup_created_at ? meta.backup_created_at.slice(0, 16).replace('T', ' ') + ' UTC' : '?'}{h != null ? `, ${h}h ago` : ''}). Not live.</span>
        : <span>No mirror installed yet — run <code>python scripts/sync_cloud_data.py</code>.</span>}
      <span className="spacer" />
      <a href="#/cloud" style={{ color: 'var(--accent)' }}>Cloud runtime →</a>
      <button className="pill-btn sm" onClick={() => useWS.getState().set({ source: 'local' })}>Back to local ledger</button>
    </div>
  );
}

function ContractDrawer() {
  const { contract, closeContract } = useUI();
  return (
    <>
      <div className={`scrim${contract ? ' on' : ''}`} onClick={closeContract} />
      <div className={`drawer${contract ? ' open' : ''}`} aria-hidden={!contract}>
        <div className="dh"><span>{contract?.title}</span><button className="x" onClick={closeContract}>✕</button></div>
        <div className="db">
          {contract && <div>{contractDetail(contract.data).map(([k, v]: any) => <Kv key={k} k={String(k).replace(/_/g, ' ')}>{typeof v === 'number' ? num(v, 4).replace(/\.?0+$/, '') : String(v)}</Kv>)}</div>}
        </div>
      </div>
    </>
  );
}

export default function Shell(): ReactNode {
  const loc = useLocation();
  const content = useRef<HTMLDivElement>(null);
  const page = loc.pathname.split('/').slice(1, 3).join('/');   // stock/PLTR, testing/ch001…, scanner — tabs share a page
  const section = loc.pathname.split('/')[1] || 'overview';
  useEffect(() => { content.current?.scrollTo({ top: 0 }); }, [page]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = (e.target as HTMLElement).tagName;
      if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA' || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '/') { e.preventDefault(); (window as any).__focusSearch?.(); }
      else if (e.key === 'r' && loc.pathname.startsWith('/stock')) Snap.refresh();
      else if (e.key === 'Escape') useUI.getState().closeContract();
    };
    addEventListener('keydown', h); return () => removeEventListener('keydown', h);
  }, [loc.pathname]);
  return (
    <div className="app">
      <Sidebar />
      <div className="main">
        <LoadBar />
        <Topbar />
        <div className="content" ref={content}><div className="content-inner page-enter" key={section}><SourceBanner /><ErrorBoundary key={loc.pathname}><Outlet /></ErrorBoundary></div></div>
      </div>
      <ContractDrawer />
    </div>
  );
}
