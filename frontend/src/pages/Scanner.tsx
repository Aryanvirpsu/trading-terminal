import ScannerPanel from '../components/widgets/Watchlist';
import { PageHead } from '../components/blocks';

export default function Scanner() {
  return (
    <>
      <PageHead title="Scanner" sub="The funnel, today's top-5 cohort, alerts and the rejected population. Scanning the ~800-name universe is always an explicit action." />
      <ScannerPanel />
    </>
  );
}
