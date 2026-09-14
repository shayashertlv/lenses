import {fileURLToPath} from 'node:url';
import {defineConfig, mergeConfig} from 'vite';
import type {Plugin, PreviewServer, ViteDevServer} from 'vite';
import hair from './experiments/hair-live-preview/vite.config.ts';

export const HAIR_ENTRY = '/experiments/hair-live-preview/live.html';

// Keep the original index and reference routes intact. Only the accepted launch
// configuration makes the long-hair page the default landing route.
function defaultHairEntry(): Plugin {
  const install = (server: ViteDevServer | PreviewServer): void => {
    server.middlewares.use((request, response, next) => {
      const url = new URL(request.url ?? '/', 'http://localhost');
      if (url.pathname !== '/' || !['GET', 'HEAD'].includes(request.method ?? '')) { next(); return; }
      response.statusCode = 302;
      response.setHeader('Location', HAIR_ENTRY + url.search);
      response.setHeader('Cache-Control', 'no-store');
      response.end();
    });
  };
  return {name: 'accepted-long-hair-entry', configureServer: install, configurePreviewServer: install};
}

export default defineConfig(async environment => {
  const config = typeof hair === 'function' ? await hair(environment) : await hair;
  return mergeConfig(config, {
    server: {host: '127.0.0.1', port: 8040, strictPort: true},
    preview: {host: '127.0.0.1', port: 8040, strictPort: true},
    plugins: [defaultHairEntry()],
    build: {rollupOptions: {input: {
      reference: fileURLToPath(new URL('./index.html', import.meta.url)),
      longHair: fileURLToPath(new URL('.' + HAIR_ENTRY, import.meta.url)),
      perfectTemples: fileURLToPath(new URL('./experiments/temple-sagittal/live.html', import.meta.url)),
    }}},
  });
});
