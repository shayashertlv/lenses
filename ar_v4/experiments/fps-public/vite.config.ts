import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {readFile, readdir, realpath} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {defineConfig} from 'vite';
import type {Plugin, PluginOption, UserConfig} from 'vite';
import {acceptedReferencePlugin} from './source/experiments/hair-live-preview/accepted-reference.ts';
import {mobileAddressPlugin} from '../efficiency-lab/mobile-build.ts';

const directory = fileURLToPath(new URL('./', import.meta.url));
const application = path.resolve(directory, '../..');
const source = path.join(directory, 'source');
const normalize = (filename: string): string => filename.replaceAll('\\', '/');
const hash = (bytes: Uint8Array | string): string => createHash('sha256').update(bytes).digest('hex');
const networkModule = normalize(path.join(application, 'experiments/efficiency-lab/mobile-network.ts'));
const sharedAssets = [
  'models/amber-horizon.glb', 'models/tom-ford-clear.glb', 'models/face_landmarker.task',
  'models/canonical-face.json', 'models/manifest.json', 'models/hair/manifest.json',
  'models/hair/LICENSE-2.0.txt', 'models/hair/ATTRIBUTION.md',
  'licenses/Three-MIT.txt', 'licenses/Apache-2.0.txt',
  'mediapipe/vision_wasm_module_internal.js', 'mediapipe/vision_wasm_module_internal.wasm',
  'hair-preview-models/hair-only.tflite', 'hair-preview-models/selfie-multiclass.tflite',
];
const omitted = new Set(['node_modules', 'dist', '.git', '.recovery', 'output', 'test-results', 'qa']);

async function sourceFiles(root: string, prefix = ''): Promise<string[]> {
  const result: string[] = [];
  for (const item of await readdir(root, {withFileTypes: true})) {
    if (omitted.has(item.name)) continue;
    const relative = prefix + item.name;
    if (item.isDirectory()) result.push(...await sourceFiles(path.join(root, item.name), relative + '/'));
    else if (!item.isFile()) throw new Error(`FPS source may not contain links: ${relative}`);
    else if (/\.(?:ts|mjs|html|css|json)$/.test(item.name)
      && !/\.(?:spec|test)\.[cm]?[jt]s$/.test(item.name)) result.push(relative);
  }
  return result.sort();
}

async function release() {
  const files: {path: string; sha256: string}[] = [];
  for (const name of await sourceFiles(source))
    files.push({path: 'source/' + name, sha256: hash(await readFile(path.join(source, name)))});
  for (const name of ['vite.config.ts', 'tsconfig.json', 'package.mjs'])
    files.push({path: name, sha256: hash(await readFile(path.join(directory, name)))});
  for (const name of ['package.json', 'package-lock.json',
    'experiments/efficiency-lab/mobile-build.ts', 'experiments/efficiency-lab/mobile-network.ts'])
    files.push({path: 'application/' + name, sha256: hash(await readFile(path.join(application, name)))});
  for (const name of sharedAssets)
    files.push({path: 'shared-public/' + name, sha256: hash(await readFile(path.join(application, 'mobile-site', name)))});
  files.sort((a, b) => a.path.localeCompare(b.path));
  return {schema: 'fps-public-build-v1', fingerprint: hash(JSON.stringify(files)),
    createdAt: new Date().toISOString(), entry: '/ar_testing/fps/experiments/fps-candidate/live.html',
    comparisonBaseline: 'Isolated local G snapshot with the shared iPhone capture correction.',
    sharedAssets, files};
}

/** Vite resolves linked dependencies to their real path. Preserve the existing
 * mobile guard even when that differs from mobileAddressPlugin's lexical path. */
async function sdkRealPathGuard(): Promise<Plugin> {
  const require = createRequire(import.meta.url);
  const sdk = normalize(await realpath(require.resolve('@mediapipe/tasks-vision')));
  return {name: 'fps-mobile-sdk-real-path-guard', enforce: 'pre', transform(code, id) {
    if (normalize(id.split('?', 1)[0]!) !== sdk || code.includes(networkModule)) return null;
    return {code: `import ${JSON.stringify(networkModule)};\n${code}`, map: null};
  }};
}
function mobilePlugins(): PluginOption[] {
  return [acceptedReferencePlugin(), mobileAddressPlugin(), sdkRealPathGuard()];
}

export default defineConfig(async (): Promise<UserConfig> => {
  const build = await release();
  const metadata: Plugin = {name: 'fps-public-fingerprint', apply: 'build', buildStart() {
    this.emitFile({type: 'asset', fileName: 'fps-build.json', source: JSON.stringify(build, null, 2) + '\n'});
  }};
  return {root: source, base: '/ar_testing/fps/', cacheDir: path.join(application, 'node_modules/.vite-fps-public'),
    define: {'import.meta.env.VITE_FPS_BUILD_ID': JSON.stringify(build.fingerprint),
      'import.meta.env.VITE_FPS_BUILD_AT': JSON.stringify(build.createdAt)},
    plugins: [...mobilePlugins(), metadata],
    worker: {format: 'es', plugins: mobilePlugins},
    build: {outDir: path.join(directory, 'dist'), emptyOutDir: true, copyPublicDir: false, sourcemap: false,
      rolldownOptions: {input: {fps: path.join(source, 'experiments/fps-candidate/live.html')}}},
  };
});
