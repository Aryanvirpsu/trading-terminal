import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Built bundle lands in dashboard/ui_dist, which Flask serves at /ui/ (see dashboard/app.py).
// In dev, Vite proxies /api to the running Flask server.
export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  build: { outDir: '../dashboard/ui_dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  // Dev: the API target follows scripts/run_terminal.py (DASHBOARD_PORT) so ports never need editing here.
  server: { port: 5173, proxy: { '/api': { target: process.env.VITE_API_TARGET || `http://127.0.0.1:${process.env.DASHBOARD_PORT || 5057}`, changeOrigin: true } } },
});
