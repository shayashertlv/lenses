import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {defineConfig} from 'vite';
import type {Plugin} from 'vite';
import {DECODER_FILES} from './src/model/decoders.ts';

const require = createRequire(import.meta.url);
const CONTENT_TYPES: Readonly<Record<string, string>> = {'.js': 'text/javascript', '.wasm': 'application/wasm'};

/** The Draco decoder and the Basis (KTX2) transcoder (src/model/decoders.ts): served from three's own
 *  examples/jsm/libs in node_modules during development and preview, and copied into the build at the same paths, so
 *  the page never fetches a decoder from outside. */
function decoderFiles(): Plugin {
  const read = (published: string): Buffer => readFileSync(require.resolve(DECODER_FILES[published]!));
  const serve = (base: string) => (request: {url?: string}, response: {setHeader(name: string, value: string): void; end(body: Buffer): void},
    next: () => void): void => {
    const path = (request.url ?? '').split('?')[0]!;
    const published = path.startsWith(base) ? path.slice(base.length) : null;
    if (!published || !(published in DECODER_FILES)) {next(); return;}
    response.setHeader('Content-Type', CONTENT_TYPES[published.slice(published.lastIndexOf('.'))] ?? 'application/octet-stream');
    response.setHeader('Cache-Control', 'no-cache');
    response.end(read(published));
  };
  // three r185's DRACOLoader and KTX2Loader also name their decoders by `new URL('../libs/...', import.meta.url)` defaults,
  // which the build would copy a second time under hashed names. arview always sets the published paths above
  // (src/model/load.ts createGltfLoader), so those defaults are emptied: a loader built without a path fails loudly.
  const defaults = /new URL\(\s*'\.\.\/libs\/(?:draco|basis)\/[\w./]+'\s*,\s*import\.meta\.url\s*\)\.toString\(\)/g;
  return {
    name: 'arview-decoders',
    configureServer(server) {server.middlewares.use(serve(server.config.base));},
    transform(code, id) {
      if (!/three[\\/]examples[\\/]jsm[\\/]loaders[\\/](DRACOLoader|KTX2Loader)\.js$/.test(id.split('?')[0]!)) return null;
      return {code: code.replace(defaults, "''"), map: null};
    },
    generateBundle() {
      for (const published of Object.keys(DECODER_FILES)) this.emitFile({type: 'asset', fileName: published, source: read(published)});
    },
  };
}

/** arview dev server on 8250, frozen preview (after `npm run build`) on 8251 (ar/ keeps 8240/8241).
 *  Public binaries (MediaPipe wasm, face/hair models, canonical face) are read read-only from ar/public, so the
 *  38 MB are not duplicated into Git. Use the frozen build for any timed session: a dev-server hot reload in the
 *  middle of a measurement kills it. */
export default defineConfig({
  define: {__BUILD_TIME__: JSON.stringify(new Date().toISOString())},
  base: '/',
  publicDir: '../../ar/public',
  cacheDir: 'node_modules/.vite',
  plugins: [decoderFiles()],
  worker: {format: 'es'},
  server: {host: '127.0.0.1', port: 8250, strictPort: true, fs: {allow: ['.', '../../ar/public', '../../ar/qa/fixtures']}},
  preview: {host: '127.0.0.1', port: 8251, strictPort: true},
  build: {outDir: 'dist', emptyOutDir: true, copyPublicDir: true, sourcemap: false, target: 'es2022',
    rollupOptions: {input: {main: 'index.html'}}},
});
