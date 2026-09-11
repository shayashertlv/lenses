import {fileURLToPath} from 'node:url';
import {defineConfig, mergeConfig} from 'vite';
import type {Plugin, ViteDevServer, PreviewServer} from 'vite';
import original from '../speed-lab/vite.config.ts';

const entry = '/experiments/efficiency-lab/live.html';
function experimentEntry(): Plugin {
  const install = (server: ViteDevServer | PreviewServer): void => {
    server.middlewares.use((request, response, next) => {
      const url = new URL(request.url ?? '/', 'http://localhost');
      if (url.pathname !== '/' || !['GET', 'HEAD'].includes(request.method ?? '')) {next(); return;}
      response.statusCode = 302; response.setHeader('Location', entry + url.search);
      response.setHeader('Cache-Control', 'no-store'); response.end();
    });
  };
  return {name: 'isolated-efficiency-entry', enforce: 'pre', configureServer: install, configurePreviewServer: install};
}
export default defineConfig(async environment => {
  const config = typeof original === 'function' ? await original(environment) : await original;
  return mergeConfig(config, {
    cacheDir: 'node_modules/.vite-efficiency-lab',
    server: {host: '127.0.0.1', port: 8094, strictPort: true},
    preview: {host: '127.0.0.1', port: 8096, strictPort: true},
    plugins: [experimentEntry()],
    build: {outDir: 'experiments/efficiency-lab/dist', emptyOutDir: true,
      rollupOptions: {input: {efficiencyLab: fileURLToPath(new URL('./live.html', import.meta.url))}}},
  });
});
