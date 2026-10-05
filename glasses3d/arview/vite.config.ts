import {defineConfig} from 'vite';

/** arview dev server on 8250, frozen preview (after `npm run build`) on 8251 (ar/ keeps 8240/8241).
 *  Public binaries (MediaPipe wasm, face/hair models, canonical face) are read read-only from ar/public, so the
 *  38 MB are not duplicated into Git. Use the frozen build for any timed session: a dev-server hot reload in the
 *  middle of a measurement kills it. */
export default defineConfig({
  define: {__BUILD_TIME__: JSON.stringify(new Date().toISOString())},
  base: '/',
  publicDir: '../../ar/public',
  cacheDir: 'node_modules/.vite',
  worker: {format: 'es'},
  server: {host: '127.0.0.1', port: 8250, strictPort: true, fs: {allow: ['.', '../../ar/public', '../../ar/qa/fixtures']}},
  preview: {host: '127.0.0.1', port: 8251, strictPort: true},
  build: {outDir: 'dist', emptyOutDir: true, copyPublicDir: true, sourcemap: false, target: 'es2022',
    rollupOptions: {input: {main: 'index.html'}}},
});
