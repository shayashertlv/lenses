import {fileURLToPath} from 'node:url';
import {defineConfig, mergeConfig} from 'vite';
import type {Plugin, PreviewServer, ViteDevServer} from 'vite';
import comparisons from './experiments/performance-stage3/vite.config.ts';

export const SPEED_ENTRY = '/experiments/performance-stage2/live.html';

function defaultSpeedEntry(): Plugin {
  const install = (server: ViteDevServer | PreviewServer): void => {
    server.middlewares.use((request, response, next) => {
      const url = new URL(request.url ?? '/', 'http://localhost');
      if (url.pathname !== '/' || !['GET', 'HEAD'].includes(request.method ?? '')) { next(); return; }
      response.statusCode = 302;
      response.setHeader('Location', SPEED_ENTRY + url.search);
      response.setHeader('Cache-Control', 'no-store');
      response.end();
    });
  };
  return {name: 'speed-testing-default', configureServer: install, configurePreviewServer: install};
}

function preservedTempleEntry(): Plugin {
  return {
    name: 'speed-preserved-temple-entry', enforce: 'post',
    generateBundle: {order: 'post', handler(_options, bundle) {
      // The immutable resolver places this HTML under its pinned source path.
      // Keep the public checkpoint URL real, including in a static build.
      const pinned = bundle['references/perfect-temples/experiments/temple-sagittal/live.html'];
      if (!pinned || pinned.type !== 'asset') throw new Error('The preserved perfect-temples page was not built.');
      this.emitFile({type: 'asset', fileName: 'experiments/temple-sagittal/live.html', source: pinned.source});
    }},
  };
}

// Promote the launch choice, retaining the exact rendering implementations and
// the separately launchable long-hair and perfect-temples checkpoints.
export default defineConfig(async environment => {
  const config = typeof comparisons === 'function' ? await comparisons(environment) : await comparisons;
  return mergeConfig(config, {
    cacheDir: 'node_modules/.vite-speed',
    server: {host: '127.0.0.1', port: 8040, strictPort: true},
    preview: {host: '127.0.0.1', port: 8040, strictPort: true},
    plugins: [defaultSpeedEntry(), preservedTempleEntry()],
    build: {outDir: 'dist', rollupOptions: {input: {
      reference: fileURLToPath(new URL('./index.html', import.meta.url)),
      perfectTemples: fileURLToPath(new URL('./experiments/temple-sagittal/live.html', import.meta.url)),
    }}},
  });
});
