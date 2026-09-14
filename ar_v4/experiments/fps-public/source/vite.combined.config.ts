import {defineConfig, mergeConfig} from 'vite';
import type {Plugin, PreviewServer, ViteDevServer} from 'vite';
import comparison from './experiments/speed-lab/vite.config.ts';

export const COMBINED_ENTRY = '/experiments/speed-lab/live.html';

function currentCombinedEntry(): Plugin {
  const install = (server: ViteDevServer | PreviewServer): void => {
    server.middlewares.use((request, response, next) => {
      const url = new URL(request.url ?? '/', 'http://localhost');
      if (url.pathname !== '/' || !['GET', 'HEAD'].includes(request.method ?? '')) {next(); return;}
      response.statusCode = 302;
      response.setHeader('Location', COMBINED_ENTRY + url.search);
      response.setHeader('Cache-Control', 'no-store');
      response.end();
    });
  };
  // This runs before the retained Test 2 entry middleware. The original launch
  // configuration remains independently usable through dev/build/preview:test2.
  return {name: 'current-g-combined-entry', enforce: 'pre', configureServer: install, configurePreviewServer: install};
}

export default defineConfig(async environment => {
  const config = typeof comparison === 'function' ? await comparison(environment) : await comparison;
  return mergeConfig(config, {
    cacheDir: 'node_modules/.vite-combined',
    server: {host: '127.0.0.1', port: 8040, strictPort: true},
    preview: {host: '127.0.0.1', port: 8040, strictPort: true},
    plugins: [currentCombinedEntry()],
    build: {outDir: 'dist', emptyOutDir: true},
  });
});
