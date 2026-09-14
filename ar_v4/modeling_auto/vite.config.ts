import { defineConfig } from 'vite';

export default defineConfig({
  server: { host: '127.0.0.1', port: 8061, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8060' } },
  build: { outDir: 'dist', emptyOutDir: true },
  cacheDir: 'data/cache/vite',
});
