import {fileURLToPath} from 'node:url';
import {defineConfig, mergeConfig} from 'vite';
import baseline from '../../vite.config.ts';
import {acceptedReferencePlugin} from '../hair-live-preview/accepted-reference.ts';
import {hairModelAssetsPlugin} from '../hair-live-preview/vite.config.ts';

export default defineConfig(async () => mergeConfig(baseline, {
  cacheDir: 'node_modules/.vite-performance-stage2',
  plugins: [await acceptedReferencePlugin(), hairModelAssetsPlugin()],
  server: {host: '127.0.0.1', port: 8072, strictPort: true,
    watch: {ignored: ['**/.recovery/**', '**/model_studio/**', '**/qa/output/**']}},
  preview: {host: '127.0.0.1', port: 8072, strictPort: true},
  build: {outDir: 'experiments/performance-stage2/dist', emptyOutDir: true,
    rollupOptions: {input: {
      stage2: fileURLToPath(new URL('./live.html', import.meta.url)),
      stage3: fileURLToPath(new URL('../performance-stage3/live.html', import.meta.url)),
      test1: fileURLToPath(new URL('../performance-candidate/live.html', import.meta.url)),
      current: fileURLToPath(new URL('../hair-live-preview/live.html', import.meta.url)),
    }}},
}));
