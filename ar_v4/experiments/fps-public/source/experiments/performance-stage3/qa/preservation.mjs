import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

export const preservationManifest = path.resolve('experiments/performance-stage3/qa/output/preservation-before-2026-09-09T13-03-49.571Z.json');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export async function verifyPreservation() {
  const bytes = await fs.readFile(preservationManifest), manifest = JSON.parse(bytes);
  assert.equal(manifest.schema, 'ar-stage3-independent-preservation-v1');
  assert.equal(manifest.previousManifest.entries, 137); assert.equal(manifest.files.length, 175);
  for (const file of manifest.files) {
    const current = await fs.readFile(file.path);
    assert.equal(current.length, file.bytes, `Preserved file size changed: ${file.path}`);
    assert.equal(sha(current), file.sha256, `Preserved file changed: ${file.path}`);
  }
  return {manifest: {path: preservationManifest, sha256: sha(bytes)}, previousFiles: 137, stage2Files: 38, uniqueFiles: manifest.files.length,
    verifiedAt: new Date().toISOString()};
}
if (process.argv.includes('--verify')) console.log(JSON.stringify(await verifyPreservation()));
