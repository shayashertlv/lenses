import {defineConfig} from 'vite';

/** Dev server on 8240, frozen preview (after `npm run build`) on 8241. Use the frozen build for any timed session: a
 *  dev-server hot reload in the middle of a measurement kills it. */
export default defineConfig({
  base: '/',
  publicDir: 'public',
  worker: {format: 'es'},
  server: {host: '127.0.0.1', port: 8240, strictPort: true},
  preview: {host: '127.0.0.1', port: 8241, strictPort: true},
  build: {outDir: 'dist', emptyOutDir: true, copyPublicDir: true, sourcemap: false, target: 'es2022'},
});
