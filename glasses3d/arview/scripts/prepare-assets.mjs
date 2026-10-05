/** Verifies, READ-ONLY, the public binaries arview reads from ../../ar/public (vite.config.ts publicDir) and the face-a
 *  fixture in ../../ar/qa/fixtures, each by its pinned SHA-256, and that the MediaPipe WASM runtime there is byte-identical
 *  to the one in arview's node_modules (the version the page's tasks-vision import expects). Writes nothing: arview never
 *  modifies ar/ (AGENTS.md; WP1_DESIGN.md). `npm run build` runs it first; `arview serve` runs verifyPublicAssets() and
 *  lists any mismatch as an error in its report.
 *
 *  (The ar/ original wrote public/models/manifest.json and copied the WASM into public/mediapipe. arview has no public/
 *  of its own, so the copy failed with ENOENT on public/models/amber-horizon.glb and `npm run build` could not run; the
 *  catalog GLBs it pinned are not used by arview, which renders only the handed-over model.) */
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const ARVIEW_ROOT = fileURLToPath(new URL('../', import.meta.url));
/** Paths relative to the lenses repository root. */
export const PINNED_ASSETS = Object.freeze({
  'ar/public/models/face_landmarker.task': '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff',
  'ar/public/models/canonical-face.json': '566302c1734dc3b096f7651f46a3a568de4d7a47ba662425b963880f7640cf14',
  'ar/public/models/hair/hair-only.tflite': '2628cf3ce5f695f604cbea2841e00befcaa3624bf80caf3664bef2656d59bf84',
  'ar/public/models/hair/selfie-multiclass.tflite': 'c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0',
  'ar/public/models/hair/LICENSE-2.0.txt': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
  'ar/qa/fixtures/face-a.jpg': 'b6491464eb87c023a8e07e2004cb4d30e2a57025857a0de43810d0191dd7208e',
});
/** The pinned FilesetResolver.forVisionTasks(root, true) selects this ESM pair. */
export const MEDIAPIPE_RUNTIME = Object.freeze(['vision_wasm_module_internal.js', 'vision_wasm_module_internal.wasm']);

const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

/** Resolves {ok, problems[], files: {path: sha256}}; never throws for a missing or changed file (it is a problem). */
export async function verifyPublicAssets(arviewRoot = ARVIEW_ROOT) {
  const lenses = path.resolve(arviewRoot, '..', '..');
  const problems = [], files = {};
  const read = async file => {
    try {return await readFile(file);}
    catch (error) {problems.push(`${path.relative(lenses, file).replaceAll('\\', '/')} cannot be read (${error.code ?? error.message})`); return null;}
  };
  for (const [name, expected] of Object.entries(PINNED_ASSETS)) {
    const bytes = await read(path.join(lenses, name));
    if (!bytes) continue;
    files[name] = sha256(bytes);
    if (files[name] !== expected) problems.push(`${name} has sha256 ${files[name]}, not the pinned ${expected}`);
  }
  for (const name of MEDIAPIPE_RUNTIME) {
    const published = await read(path.join(lenses, 'ar/public/mediapipe', name));
    const installed = await read(path.join(arviewRoot, 'node_modules/@mediapipe/tasks-vision/wasm', name));
    if (!published || !installed) continue;
    files[`ar/public/mediapipe/${name}`] = sha256(published);
    if (!published.equals(installed)) problems.push(`ar/public/mediapipe/${name} differs from arview's node_modules/@mediapipe/tasks-vision/wasm/${name}`);
  }
  return {ok: problems.length === 0, problems, files};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = await verifyPublicAssets();
  if (!result.ok) {
    console.error(`Public asset check FAILED (nothing was written):\n  ${result.problems.join('\n  ')}`);
    process.exit(1);
  }
  console.log(`Verified ${Object.keys(result.files).length} pinned public assets read-only from ../../ar (nothing written).`);
}
