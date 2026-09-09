import {fileURLToPath} from 'node:url';
import {defineConfig, mergeConfig} from 'vite';
import baseline from '../../vite.config.ts';
import {acceptedReferencePlugin} from '../hair-live-preview/accepted-reference.ts';
import {hairModelAssetsPlugin} from '../hair-live-preview/vite.config.ts';

// A separate server/build. Original runtime imports still resolve to the verified
// checkpoint; candidate modules explicitly import their own changed implementations.
export default defineConfig(async () => mergeConfig(baseline, {
  cacheDir: 'node_modules/.vite-performance-candidate',
  plugins: [await acceptedReferencePlugin(), hairModelAssetsPlugin()],
  server: {host: '127.0.0.1', port: 8066, strictPort: true,
    watch: {ignored: ['**/.recovery/**', '**/model_studio/**', '**/qa/output/**']}},
  preview: {host: '127.0.0.1', port: 8066, strictPort: true},
  build: {outDir: 'experiments/performance-candidate/dist', emptyOutDir: true,
    rollupOptions: {input: {
      comparison: fileURLToPath(new URL('./live.html', import.meta.url)),
      current: fileURLToPath(new URL('../hair-live-preview/live.html', import.meta.url)),
    }}},
}));
