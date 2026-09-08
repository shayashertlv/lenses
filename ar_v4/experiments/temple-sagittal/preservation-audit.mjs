import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const study = path.join(app, '.recovery/temple-rethink-2026-09-08');
if (process.argv.includes('--help')) {
  console.log('Usage: node experiments/temple-sagittal/preservation-audit.mjs [new-output.json]'); process.exit(0);
}
const start = JSON.parse(fs.readFileSync(path.join(study, 'start.json'), 'utf8'));
assert.equal(Object.keys(start.protectedFiles).length, 29180);
assert.equal(Object.keys(start.tracked).length, 50);
const streamHash = file => new Promise((resolve, reject) => {
  const digest = createHash('sha256'), stream = fs.createReadStream(file);
  stream.on('data', chunk => digest.update(chunk)); stream.on('error', reject);
  stream.on('end', () => resolve(digest.digest('hex')));
});
let checked = 0;
const changed = [], missing = [];
for (const [relative, expected] of Object.entries(start.protectedFiles)) {
  const absolute = path.resolve(app, relative);
  assert(absolute.startsWith(app + path.sep), 'A protection receipt path escaped ar_v4.');
  try {
    const actual = await streamHash(absolute);
    if (actual !== expected) changed.push({path: relative, expected, actual});
  } catch (error) {missing.push({path: relative, error: error.message});}
  checked++;
  if (checked % 2000 === 0) console.log(`Rehashed ${checked} / 29180 protected files.`);
}
const tracked = [], docs = [];
for (const [relative, expected] of Object.entries(start.tracked)) {
  const actual = await streamHash(path.resolve(app, relative));
  const row = {path: relative, expected, actual, byteExact: actual === expected};
  (relative === 'docs/REVIEWS.md' ? docs : tracked).push(row);
}
assert.equal(tracked.length, 49);
const head = execFileSync('git', ['rev-parse', 'HEAD'], {cwd: app, encoding: 'utf8'}).trim();
const statusRaw = execFileSync('git', ['status', '--porcelain=v1', '-z', '--untracked-files=all'], {cwd: app, encoding: 'utf8'});
const records = statusRaw.split('\0').filter(Boolean), outside = [], arChanges = [], concurrentModelStudio = [];
for (let i = 0; i < records.length; i++) {
  const status = records[i].slice(0, 2), currentPath = records[i].slice(3);
  const paths = [currentPath];
  if (status.includes('R') || status.includes('C')) paths.push(records[++i]);
  const row = {status, paths};
  if (paths.some(file => !file.startsWith('ar_v4/'))) outside.push(row);
  else if (paths.every(file => file.startsWith('ar_v4/model_studio/'))) concurrentModelStudio.push(row);
  else arChanges.push(row);
}
const allStaged = execFileSync('git', ['diff', '--cached', '--name-only', '-z'], {cwd: app, encoding: 'utf8'}).split('\0').filter(Boolean);
const staged = allStaged.filter(relative => !relative.startsWith('ar_v4/model_studio/'));
const result = {createdAt: new Date().toISOString(), startReceipt: '.recovery/temple-rethink-2026-09-08/start.json',
  protectedFileCount: checked, protectedChanged: changed, protectedMissing: missing,
  trackedUnchangedExpectedCount: 49, tracked, allowedDocumentationChanges: docs,
  head, acceptedHead: start.head, headUnchanged: head === start.head,
  parentStatusOutsideArV4: outside, arChanges, stagedPaths: staged,
  concurrentModelStudioStatusCount: concurrentModelStudio.length,
  concurrentModelStudioStagedCount: allStaged.length - staged.length,
  modelStudioPolicy: 'Concurrent model_studio work excluded from modification attribution; no active model_studio file read, edited or hashed.',
  passed: !changed.length && !missing.length && tracked.every(row => row.byteExact) && head === start.head && !outside.length && !staged.length};
const output = path.resolve(app, process.argv[2] ?? path.join(study, `preservation-${new Date().toISOString().replaceAll(':', '-')}.json`));
assert(output.startsWith(study + path.sep));
fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n', {flag: 'wx'});
console.log(JSON.stringify({output, protectedFileCount: checked, protectedChanged: changed.length,
  protectedMissing: missing.length, trackedUnchanged: tracked.filter(row => row.byteExact).length,
  parentChanges: outside.length, stagedPaths: staged, passed: result.passed}, null, 2));
if (!result.passed) process.exitCode = 1;
