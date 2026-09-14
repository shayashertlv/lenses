import {fileURLToPath} from 'node:url';
import {defineConfig, mergeConfig} from 'vite';
import baseline from '../../vite.config.ts';
import {acceptedReferencePlugin} from '../hair-live-preview/accepted-reference.ts';
import {hairModelAssetsPlugin} from '../hair-live-preview/vite.config.ts';

export default defineConfig(async () => mergeConfig(baseline, {
  cacheDir: 'node_modules/.vite-performance-stage3',
  plugins: [await acceptedReferencePlugin(), hairModelAssetsPlugin()],
  server: {host: '127.0.0.1', port: 8076, strictPort: true,
    watch: {ignored: ['**/.recovery/**', '**/model_studio/**', '**/qa/output/**']}},
  preview: {host: '127.0.0.1', port: 8076, strictPort: true},
  build: {outDir: 'experiments/performance-stage3/dist', emptyOutDir: true,
    rollupOptions: {input: {
      stage3: fileURLToPath(new URL('./live.html', import.meta.url)),
      stage2: fileURLToPath(new URL('../performance-stage2/live.html', import.meta.url)),
      test1: fileURLToPath(new URL('../performance-candidate/live.html', import.meta.url)),
      current: fileURLToPath(new URL('../hair-live-preview/live.html', import.meta.url)),
    }}},
}));
