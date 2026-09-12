import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';

const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const commit = 'b9142b2a3b957445f378d8012eea7e27ca68fd0b';
export const manifestPath = path.resolve('experiments/efficiency-lab/qa/g-base-manifest.json');
const blob = filename => execFileSync('git', ['show', `${commit}:ar_v4/${filename}`], {maxBuffer: 64 * 1024 * 1024});
const normalized = bytes => bytes.toString('utf8').replaceAll('\r\n', '\n');

/** A new G boundary records Git content and this checkout's actual bytes independently.
 * Older CRLF manifests and every historical receipt remain untouched. */
export async function verifyBase({gitExact = false} = {}) {
  const bytes = await fs.readFile(manifestPath), manifest = JSON.parse(bytes);
  assert.equal(digest(bytes), '17e8e7543fd6ba3b4643232d53396ef9d6b574a32b29bca5681f3dc72c60364f');
  assert.equal(manifest.schema, 'efficiency-lab-g-base-v1');
  assert.equal(manifest.commit, commit);
  assert.ok(manifest.files.length > 200);
  const unique = new Set();
  for (const item of manifest.files) {
    const filename = path.resolve(item.path), relative = path.relative(process.cwd(), filename);
    assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative));
    assert.ok(!unique.has(filename)); unique.add(filename);
    const current = await fs.readFile(filename), committed = blob(item.path);
    assert.equal(committed.length, item.gitBytes, `G blob size differs: ${item.path}`);
    assert.equal(digest(committed), item.gitSHA256, `G blob differs: ${item.path}`);
    if (gitExact) {
      assert.ok(current.equals(committed), `This isolated checkout must retain exact G Git bytes: ${item.path}`);
    } else {
      assert.equal(current.length, item.bytes, `Local G byte length differs: ${item.path}`);
      assert.equal(digest(current), item.sha256, `Local G bytes differ: ${item.path}`);
    }
    if (!gitExact && !current.equals(committed)) {
      assert.equal(item.representation, 'CRLF checkout / LF Git blob');
      assert.equal(normalized(current), committed.toString('utf8'), `Beyond-line-ending G change: ${item.path}`);
    } else if (!gitExact) assert.equal(item.representation, 'exact Git blob');
  }
  return {manifest: {path: manifestPath, sha256: digest(bytes), bytes: bytes.length}, commit, filesVerified: unique.size,
    ...(gitExact ? {localRepresentation: 'exact Git blob'} : {})};
}

if (process.argv.includes('--create')) {
  const historical = JSON.parse(await fs.readFile('experiments/speed-lab/qa/base-manifest.json'));
  const tracked = execFileSync('git', ['ls-tree', '-r', '--name-only', commit, '--', 'ar_v4/experiments/speed-lab'],
    {cwd: path.resolve('..'), encoding: 'utf8'}).trim().split('\n').filter(Boolean).map(name => name.slice('ar_v4/'.length));
  const files = [];
  for (const filename of [...new Set([...historical.files.map(item => item.path), ...tracked,
    'vite.combined.config.ts', 'vite.speed.config.ts', 'vite.hair.config.ts'])].sort()) {
    const local = await fs.readFile(filename), committed = blob(filename), exact = local.equals(committed);
    assert.ok(exact || normalized(local) === committed.toString('utf8'), `Uncommitted G dependency change: ${filename}`);
    files.push({path: filename, sha256: digest(local), bytes: local.length, gitSHA256: digest(committed), gitBytes: committed.length,
      representation: exact ? 'exact Git blob' : 'CRLF checkout / LF Git blob'});
  }
  await fs.writeFile(manifestPath, JSON.stringify({schema: 'efficiency-lab-g-base-v1', commit,
    scope: 'Unchanged G and accepted renderer dependencies, with separate local-byte and Git-blob identities. Historical manifests remain unchanged.', files}, null, 2) + '\n', {flag: 'wx'});
}
if (process.argv.includes('--verify') || process.argv.includes('--create')) console.log(JSON.stringify(await verifyBase({gitExact: process.argv.includes('--git-exact')})));
