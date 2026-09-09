import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const digest = bytes => createHash('sha256').update(bytes).digest('hex');
export const manifestPath = path.resolve('experiments/speed-lab/qa/base-manifest.json');
export async function verifyBase() {
  const bytes = await fs.readFile(manifestPath), manifest = JSON.parse(bytes);
  assert.equal(digest(bytes), '527c94adedbd1fb11ae1159fc1414c90d9a875bd70d248661d428cf446ecd990');
  assert.equal(manifest.schema, 'speed-lab-base-v1');
  assert.equal(manifest.commit, '8baa16c9ffae945130b936821d5948c2f1cac720');
  assert.equal(manifest.files.length, 186);
  const unique = new Set();
  for (const item of manifest.files) {
    const filename = path.resolve(item.path), relative = path.relative(process.cwd(), filename);
    assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative), 'Base path leaves the AR workspace.');
    assert.ok(!unique.has(filename), 'Duplicate base file.'); unique.add(filename);
    const current = await fs.readFile(filename);
    assert.equal(current.length, item.bytes, `Base byte length differs: ${item.path}`);
    assert.equal(digest(current), item.sha256, `Committed base differs: ${item.path}`);
  }
  return {manifest: {path: manifestPath, sha256: digest(bytes), bytes: bytes.length},
    commit: manifest.commit, filesVerified: unique.size};
}
if (process.argv.includes('--verify')) console.log(JSON.stringify(await verifyBase()));
