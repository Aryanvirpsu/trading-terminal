import { Suspense, lazy, useEffect } from 'react';
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom';
import Shell from './components/Shell';
import Overview from './pages/Overview';
import Scanner from './pages/Scanner';
import StockPage from './pages/StockPage';
import Markets from './pages/Markets';
import Portfolio from './pages/Portfolio';
import Strategies from './pages/Strategies';
import { Skel } from './components/ui';

// The markdown renderer is only needed for the experiment write-ups, so keep it out of the first load.
const TestingIndex = lazy(() => import('./pages/Testing').then((m) => ({ default: m.TestingIndex })));
const TestingDoc = lazy(() => import('./pages/Testing').then((m) => ({ default: m.TestingDoc })));
const TestingDetail = lazy(() => import('./pages/Testing').then((m) => ({ default: m.TestingDetail })));
import System from './pages/System';
import Cloud from './pages/Cloud';
import { boot } from './lib/snapshot';

export default function App() {
  useEffect(() => { boot(); }, []);
  return (
    <HashRouter>
      <Suspense fallback={<div className="content-inner" style={{ padding: 24 }}><Skel n={6} /></div>}>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="scanner" element={<Scanner />} />
          <Route path="stock" element={<StockPage />} />
          <Route path="stock/:symbol/:tab?" element={<StockPage />} />
          <Route path="markets" element={<Markets />} />
          <Route path="portfolio" element={<Portfolio />} />
          <Route path="strategies" element={<Strategies />} />
          <Route path="testing" element={<TestingIndex />} />
          <Route path="testing/doc/:name" element={<TestingDoc />} />
          <Route path="testing/:folder/:tab?" element={<TestingDetail />} />
          <Route path="cloud" element={<Cloud />} />
          <Route path="system" element={<System />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      </Suspense>
    </HashRouter>
  );
}
