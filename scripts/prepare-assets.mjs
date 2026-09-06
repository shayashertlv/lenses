import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
// Prepared assets belong to this project. Never borrow missing files from v2/v3.
const assets = {
  'public/models/amber-horizon.glb': '78e0b472cd3e289ea7b784a86534fdeb0c90d27675e6f7ed55cc16ea3f7cc004',
  'public/models/face_landmarker.task': '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff',
  'public/models/canonical-face.json': '566302c1734dc3b096f7651f46a3a568de4d7a47ba662425b963880f7640cf14',
  'tests/fixtures/face-a.jpg': 'b6491464eb87c023a8e07e2004cb4d30e2a57025857a0de43810d0191dd7208e',
};
const manifest = {};
for (const [name, expectedHash] of Object.entries(assets)) {
  const bytes = await readFile(path.join(root, name));
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  if (sha256 !== expectedHash) throw new Error(`Asset integrity check failed: ${name}`);
  manifest[name] = { bytes: bytes.length, sha256 };
}
await writeFile(path.join(root, 'public/models/manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
const wasmTarget = path.join(root, 'public/mediapipe');
const wasmSource = path.join(root, 'node_modules/@mediapipe/tasks-vision/wasm');
await mkdir(wasmTarget, { recursive: true });
// The pinned FilesetResolver.forVisionTasks(root, true) selects this ESM pair.
for (const name of ['vision_wasm_module_internal.js', 'vision_wasm_module_internal.wasm'])
  await copyFile(path.join(wasmSource, name), path.join(wasmTarget, name));
console.log('Verified local assets and prepared the pinned MediaPipe runtime.');
