/** Bundle the actual AR renderer with its pinned Three dependency, never a second renderer. */
import {build} from '../../arview/node_modules/vite/dist/node/index.js';
import {DECODER_FILES} from '../../arview/src/model/decoders.ts';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const arview = path.resolve(here, '../../arview');
const outDir = path.resolve(here, '../.lens-renderer');
const three = path.join(arview, 'node_modules/three');
const decoderDefaults = /new URL\(\s*'\.\.\/libs\/(?:draco|basis)\/[\w./]+'\s*,\s*import\.meta\.url\s*\)\.toString\(\)/g;
await build({
  configFile: false,
  root: here,
  base: '/lens-renderer/',
  publicDir: false,
  logLevel: 'warn',
  resolve: {alias: [
    {find: /^three$/, replacement: path.join(three, 'build/three.module.js')},
    {find: 'three/addons', replacement: path.join(three, 'examples/jsm')},
  ]},
  plugins: [{name: 'local-ar-decoder-defaults', transform(code, id) {
    return /three[\\/]examples[\\/]jsm[\\/]loaders[\\/](DRACOLoader|KTX2Loader)\.js$/.test(id.split('?')[0])
      ? {code: code.replace(decoderDefaults, "''"), map: null} : null;
  }}],
  build: {outDir, emptyOutDir: true, copyPublicDir: false, target: 'es2022', minify: true,
    lib: {entry: path.join(here, 'viewer.js'), formats: ['es'], fileName: () => 'lens-renderer.js'},
  },
});
for (const [published, source] of Object.entries(DECODER_FILES)) {
  const destination = path.join(outDir, published);
  fs.mkdirSync(path.dirname(destination), {recursive: true});
  fs.copyFileSync(path.join(arview, 'node_modules', source), destination);
}
const canonical = path.join(arview, 'public/models/canonical-face.json');
if (!fs.existsSync(canonical)) throw new Error('AR assets are missing. Run npm run assets in glasses3d/arview first.');
fs.mkdirSync(path.join(outDir, 'models'), {recursive: true});
fs.copyFileSync(canonical, path.join(outDir, 'models/canonical-face.json'));
