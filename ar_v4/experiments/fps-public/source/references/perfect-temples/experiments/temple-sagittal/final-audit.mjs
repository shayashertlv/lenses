import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const study = path.join(app, '.recovery/temple-rethink-2026-09-08');
const sha = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const canonicalHash = value => sha(JSON.stringify(canonical(value)));
const read = relative => JSON.parse(fs.readFileSync(path.resolve(app, relative), 'utf8'));
const reportPath = process.argv[2];
if (!reportPath || reportPath === '--help') {
  console.log('Usage: node experiments/temple-sagittal/final-audit.mjs <complete-32-case-report.json> [new-output.json]');
  process.exit(reportPath ? 0 : 2);
}
const absoluteReport = path.resolve(app, reportPath), reportBytes = fs.readFileSync(absoluteReport), report = JSON.parse(reportBytes);
assert.equal(report.complete, true, 'Audit only a completed report.');
assert.deepEqual(report.errors, [], 'Rendering report contains errors.');
const selection = read(path.join(study, 'selection.json'));
const selected = new Map(selection.frames.map(frame => [frame.key, frame]));
const models = ['amber-horizon', 'tom-ford-clear'];
const requiredReplayIds = new Set(['dropout-454', 'flicker_recording-70', 'fresh-72', 'original-71']);
const requiredRuntime = new Set(['branch-renderer.ts', 'capture-controller.ts', 'contracts.ts', 'live-main.ts',
  'protection.ts', 'rear-drop.ts', 'renderer.ts'].map(name => `experiments/temple-sagittal/${name}`));
const selectedSources = new Set(selection.frames.map(frame => frame.sourcePath));
const expected = new Set(models.flatMap(model => selection.frames.map(frame => `${model}/${frame.key}`)));
assert.equal(expected.size, 32, 'The full frozen selection must contain 16 paired views for each model.');
assert.equal(report.cases.length, expected.size, 'A complete rendering sweep is required.');
const sourceChecks = [];
for (const [relative, expectedHash] of Object.entries(report.sourceHashes)) {
  const actual = sha(fs.readFileSync(path.resolve(app, relative)));
  const requiredRuntimeOrOriginalMatch = relative.startsWith('src/') || requiredRuntime.has(relative) || selectedSources.has(relative);
  if (requiredRuntimeOrOriginalMatch) assert.equal(actual, expectedHash, `Reported runtime/original source changed: ${relative}`);
  sourceChecks.push({path: relative, reportSha256: expectedHash, currentSha256: actual,
    matchesCurrent: actual === expectedHash, requiredRuntimeOrOriginalMatch,
    postRunQaEditsPermitted: !requiredRuntimeOrOriginalMatch});
}
for (const relative of requiredRuntime) assert(report.sourceHashes[relative], `Missing frozen runtime hash: ${relative}`);
const start = read(path.join(study, 'start.json'));
for (const [relative, expectedHash] of Object.entries(start.tracked)) if (relative.startsWith('src/')) {
  assert.equal(sha(fs.readFileSync(path.resolve(app, relative))), expectedHash, `Accepted baseline source changed: ${relative}`);
}
const recordings = new Map(), identityChecks = [];
for (const row of report.cases) {
  const key = `${row.model}/${row.id}`;
  assert(expected.delete(key), `Unexpected or repeated case: ${key}`);
  const meta = selected.get(row.id);
  assert(meta);
  assert.equal(row.sourcePath, meta.sourcePath); assert.equal(row.frameIndex, meta.sourceFrameArrayIndex);
  assert.equal(row.pitch, meta.pitchDegrees); assert.equal(row.yaw, meta.yawDegrees);
  assert.equal(row.sourceSHA256, meta.sourceSHA256); assert.equal(row.sourceImageSHA256, meta.sourceImageSHA256);
  assert.equal(row.detectionCanonicalJsonSHA256, meta.detectionCanonicalJsonSHA256);
  assert.deepEqual(row.regions, meta.regions, 'Nose regions must be the frozen selection, not resized after seeing changes.');
  if (!recordings.has(meta.sourcePath)) {
    const bytes = fs.readFileSync(path.resolve(app, meta.sourcePath));
    assert.equal(sha(bytes), meta.sourceSHA256);
    recordings.set(meta.sourcePath, JSON.parse(bytes));
  }
  const frame = recordings.get(meta.sourcePath).frames[meta.sourceFrameArrayIndex];
  assert(frame, `Missing original array index: ${key}`);
  const imageHash = sha(Buffer.from(frame[meta.imageField].split(',')[1], 'base64'));
  assert.equal(imageHash, meta.sourceImageSHA256);
  assert.equal(canonicalHash(frame.detection), meta.detectionCanonicalJsonSHA256);
  const metadata = frame[meta.metadataField];
  assert(metadata, `Missing original metadata: ${key}`);
  if (meta.metadataCanonicalJsonSHA256) assert.equal(canonicalHash(metadata), meta.metadataCanonicalJsonSHA256);
  if (meta.savedSurfaceCanonicalJsonSHA256) {
    const savedSurface = meta.savedSurfaceField.split('.').reduce((value, part) => value[part], frame);
    assert.equal(canonicalHash(savedSurface), meta.savedSurfaceCanonicalJsonSHA256);
  }
  assert.equal(frame.width, meta.width); assert.equal(frame.height, meta.height);
  assert.equal(row.width, frame.width); assert.equal(row.height, frame.height);
  for (const field of ['rawMatrix', 'correctedMatrix', 'eyewearMatrix']) {
    const expectedPose = meta[`recorded${field[0].toUpperCase()}${field.slice(1)}`];
    assert.deepEqual(metadata[field], expectedPose, `Frozen ${field} differs from original recorded metadata.`);
    assert.deepEqual(row.baselineSnapshot[field], expectedPose, `Rendered baseline ${field} differs from original.`);
    assert.deepEqual(row.candidateSnapshot[field], expectedPose, `Candidate ${field} differs from original.`);
  }
  assert.deepEqual(frame.detection.matrix, row.baselineSnapshot.rawMatrix);
  assert.deepEqual(row.candidateSnapshot.surfacePositions, row.baselineSnapshot.surfacePositions,
    'Candidate must use the fresh perfecto surface, without conflating it with an older saved nose method.');
  assert.deepEqual(row.candidateSnapshot.templeClip, row.baselineSnapshot.templeClip);
  assert.deepEqual(row.candidateSnapshot.templeVisibility, row.baselineSnapshot.templeVisibility);
  assert.equal(row.baselineSnapshot.eyewearModelId, row.model); assert.equal(row.candidateSnapshot.eyewearModelId, row.model);
  const replayVerified = Boolean(row.replay && Object.keys(row.replay).length >= 6
    && Object.values(row.replay).every(value => value === true));
  if (requiredReplayIds.has(row.id)) assert(replayVerified, `Required saved/held/legacy/live/no-face replay is missing: ${key}`);
  if (row.replay) assert(replayVerified, `Recorded replay assertion failed: ${key}`);
  assert.equal(row.diagnostics?.fallback, null, `Candidate silently fell back to perfecto: ${key}`);
  assert.equal(row.candidateSnapshot.rearDrop.dropM, row.drop.dropM, `Actual drop differs from requested paired policy: ${key}`);
  assert.equal(row.independentPerfectoByteExact, true);
  assert.equal(row.uncoveredOpticalVertices, 0);
  for (const field of ['before', 'after', 'source', 'mask']) {
    const image = fs.readFileSync(path.resolve(app, row[field].path));
    assert.equal(sha(image), row[field].sha256, `Image hash mismatch: ${row[field].path}`);
  }
  identityChecks.push({key, originalArrayIndex: meta.sourceFrameArrayIndex, originalImageSha256: imageHash,
    detectionCanonicalJsonSha256: canonicalHash(frame.detection), originalModelId: meta.originalModelId ?? null,
    renderedModelId: row.model, modelSubstitutionExplicit: (meta.originalModelId ?? row.model) !== row.model,
    originalRecordedPosesExact: true, currentPerfectoSurfacePreserved: true, replayAssertionsPresentAndTrue: replayVerified});
}
assert.equal(expected.size, 0);
const python = process.env.SAGITTAL_AUDIT_PYTHON ?? 'python';
const pixelBytes = execFileSync(python, [path.join(app, 'experiments/temple-sagittal/final-pixels.py'), absoluteReport],
  {cwd: app, maxBuffer: 12 * 1024 * 1024});
const pixels = JSON.parse(pixelBytes.toString('utf8'));
const output = path.resolve(app, process.argv[3] ?? path.join(path.dirname(absoluteReport), 'independent-final-audit.json'));
assert(output.startsWith(study + path.sep), 'Write audit output only inside this new study.');
const audit = {createdAt: new Date().toISOString(), reportPath: path.relative(app, absoluteReport).replaceAll('\\', '/'),
  reportSha256: sha(reportBytes), evidence: 'Independent original-source hash/pose checks and decoded PNG byte comparisons.',
  caseCount: identityChecks.length, requiredReplayCaseCount: requiredReplayIds.size * models.length,
  reportedReplayCaseCount: identityChecks.filter(row => row.replayAssertionsPresentAndTrue).length,
  sourceChecks, identityChecks, pixels,
  limits: 'Replay execution assertions are read from the completed browser report; this audit independently checks its persisted images and original inputs. Numeric preservation does not establish acceptable contact, real motion or anatomical accuracy.'};
fs.writeFileSync(output, JSON.stringify(audit, null, 2) + '\n', {flag: 'wx'});
console.log(JSON.stringify({output, cases: audit.caseCount, summary: pixels.summary}, null, 2));
