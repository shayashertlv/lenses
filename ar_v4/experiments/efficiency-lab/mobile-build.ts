import {createHash} from 'node:crypto';
import {readFile, readdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import type {Plugin} from 'vite';

export const MOBILE_BASE = '/ar_testing/';
export const MOBILE_ENTRY = 'experiments/efficiency-lab/live.html';
const mobileNetworkModule = fileURLToPath(new URL('./mobile-network.ts', import.meta.url)).replaceAll('\\', '/');
const mobileSdkModule = fileURLToPath(new URL('../../node_modules/@mediapipe/tasks-vision/vision_bundle.mjs', import.meta.url)).replaceAll('\\', '/');
const hash = (bytes: Uint8Array | string): string => createHash('sha256').update(bytes).digest('hex');
const omitted = new Set(['node_modules', '.git', '.recovery', 'dist', 'test-results', 'logs', 'output', '__pycache__']);
async function files(directory: string, prefix = ''): Promise<string[]> {
  const result: string[] = [];
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    if (omitted.has(entry.name)) continue;
    const relative = prefix + entry.name;
    if (entry.isDirectory()) result.push(...await files(path.join(directory, entry.name), relative + '/'));
    else if (entry.isFile()) result.push(relative);
    else throw new Error(`Build inputs may not contain symlinks: ${relative}`);
  }
  return result.sort();
}

/** Only deployment addresses change. Reviewed runtime files remain untouched. */
export function namespaceAssetReferences(code: string): string {
  // FilesetResolver adds its own separator. Vite tolerates the historical
  // double slash, but the public server requires canonical allowlisted paths.
  return code.replace(/(['"`])\/mediapipe\/\1/g, '$1/ar_testing/mediapipe$1')
    .replace(/(['"`])\/(models|mediapipe|hair-preview-models)\//g, '$1/ar_testing/$2/');
}
export function mobileAddressPlugin(): Plugin {
  return {name: 'mobile-asset-addresses', enforce: 'pre',
    transform(code, id) {
      const moduleId = id.replaceAll('\\', '/').split('?', 1)[0]!;
      if (moduleId === mobileSdkModule) return {code: `import ${JSON.stringify(mobileNetworkModule)};\n${code}`, map: null};
      if (moduleId.includes('/node_modules/') || !/\.[cm]?[jt]s$/.test(moduleId)) return null;
      const changed = namespaceAssetReferences(code);
      return changed === code ? null : {code: changed, map: null};
    },
    transformIndexHtml(html) {
      return html.replace('href="/experiments/speed-lab/live.html"', 'href="/"')
        .replace('Original G / A–F comparison ↗', 'Back to Lenses ↗');
    },
  };
}

export interface MobileRelease {schema: 'ar-mobile-release-v1'; baseCommit: string;
  sourceFingerprint: string; createdAt: string; sourceFiles: number;}
export async function mobileRelease(root: string): Promise<MobileRelease> {
  const inputs: {path: string; sha256: string}[] = [];
  for (const directory of ['src', 'references', 'experiments', 'public/models']) {
    for (const filename of await files(path.join(root, directory))) {
      if (!/\.(ts|mjs|html|css|json|glb|task|tflite)$/.test(filename)
        || /(?:\.spec|\.test)\.ts$/.test(filename) || filename.includes('/qa/')) continue;
      const relative = `${directory}/${filename}`;
      inputs.push({path: relative, sha256: hash(await readFile(path.join(root, relative)))});
    }
  }
  for (const filename of ['package.json', 'package-lock.json'])
    inputs.push({path: filename, sha256: hash(await readFile(path.join(root, filename)))});
  return {schema: 'ar-mobile-release-v1', baseCommit: 'b9142b2a3b957445f378d8012eea7e27ca68fd0b',
    sourceFingerprint: hash(JSON.stringify(inputs)), createdAt: new Date().toISOString(), sourceFiles: inputs.length};
}

const publicAssets = [
  'models/amber-horizon.glb', 'models/tom-ford-clear.glb', 'models/face_landmarker.task',
  'models/canonical-face.json', 'models/manifest.json',
  'models/hair/manifest.json', 'models/hair/LICENSE-2.0.txt', 'models/hair/ATTRIBUTION.md',
  'licenses/Three-MIT.txt', 'licenses/Apache-2.0.txt',
  'mediapipe/vision_wasm_module_internal.js', 'mediapipe/vision_wasm_module_internal.wasm',
] as const;

/** Explicit public inputs only; private recordings and source directories are
 * never copied into the Python server's static document root. */
export function mobilePackagePlugin(root: string, release: MobileRelease): Plugin {
  let output = '', generated = false;
  return {name: 'mobile-public-package', enforce: 'post',
    configResolved(config) {
      output = path.resolve(config.root, config.build.outDir);
      if (output !== path.resolve(root, 'mobile-site')) throw new Error('Unexpected mobile build output directory.');
    },
    async buildStart() {
      for (const filename of publicAssets)
        this.emitFile({type: 'asset', fileName: filename, source: await readFile(path.join(root, 'public', filename))});
      this.emitFile({type: 'asset', fileName: 'release.json', source: JSON.stringify(release, null, 2) + '\n'});
    },
    generateBundle() {generated = true;},
    async closeBundle() {
      if (!generated) return;
      const manifest: {schemaVersion: 1; files: {path: string; size: number; sha256: string}[]} = {schemaVersion: 1, files: []};
      for (const filename of await files(output)) {
        const allowed = filename === MOBILE_ENTRY || filename === 'release.json'
          || (publicAssets as readonly string[]).includes(filename)
          || /^hair-preview-models\/(hair-only|selfie-multiclass)\.tflite$/.test(filename)
          || /^assets\/[\w.-]+\.(js|css)$/.test(filename);
        if (!allowed) throw new Error(`Unexpected file in the public mobile package: ${filename}`);
        const bytes = await readFile(path.join(output, filename));
        if (filename.endsWith('.js') && /(['"`])\/(models|mediapipe|hair-preview-models)\//.test(bytes.toString('utf8')))
          throw new Error(`Unscoped runtime asset address: ${filename}`);
        manifest.files.push({path: filename, size: bytes.length, sha256: hash(bytes)});
      }
      if (!manifest.files.some(file => file.path === MOBILE_ENTRY)) throw new Error('Mobile entry is missing.');
      await writeFile(path.join(output, 'public-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
      console.log(`Mobile public package: ${manifest.files.length} files; release ${release.sourceFingerprint.slice(0, 12)}.`);
    },
  };
}
