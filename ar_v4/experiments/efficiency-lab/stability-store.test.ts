import assert from 'node:assert/strict';
import {test} from 'node:test';
import {claimStabilityManifest, completeStabilityManifest, createStabilityManifest,
  createStabilityPlan, encodeStabilityReport, interruptStabilityManifest, sameStabilityIdentity,
  STABILITY_MAX_REPORT_BYTES, validateStabilityCompletion} from './stability-store.ts';
import type {StabilityIdentity} from './stability-store.ts';

const identity: StabilityIdentity = {buildId: 'build-one', eyewearId: 'amber-horizon', hairModelId: 'hair-only',
  variant: 'hair', sourceWidth: 720, sourceHeight: 1280, power: 'unknown'};
const make = () => createStabilityManifest({identity, selection: 'all', direction: 'forward'},
  {suiteId: 'suite', token: 'first-token', now: 1000});
const claim = (documentId = 'page-one') => ({suiteId: 'suite', token: 'first-token', identity, documentId});
const finish = (documentId = 'page-one') => ({suiteId: 'suite', token: 'first-token', documentId,
  summary: {completedArFps: 18, frameAgeMs: {p95: 155}}, complete: true, bytes: 100,
  nextToken: 'second-token', now: 2000});

test('stability plans hold identical 180 seconds per condition and eight independent documents', () => {
  const forward = createStabilityPlan('all', 'forward'), reverse = createStabilityPlan('all', 'reverse');
  assert.equal(forward.length, 8);
  assert.equal(forward.reduce((sum, item) => sum + item.totalMeasureMs, 0), 540_000);
  assert.deepEqual(forward.map(item => item.condition), ['continuous', 'restarted', ...Array(6).fill('fresh-page')]);
  assert.deepEqual(reverse.map(item => item.condition), [...Array(6).fill('fresh-page'), 'restarted', 'continuous']);
  assert.equal(forward[0]!.windowCount, 1); assert.equal(forward[0]!.measureMs, 180_000);
  assert.equal(forward[1]!.windowCount, 6); assert.equal(forward[1]!.measureMs, 30_000);
  for (const item of forward.slice(2)) {assert.equal(item.windowCount, 1); assert.equal(item.measureMs, 30_000);}
  assert.deepEqual(createStabilityPlan('fresh-page', 'forward'), createStabilityPlan('fresh-page', 'reverse'));
});
test('create copies fixed identity and claim leaves its input untouched', () => {
  const original = make(), captured = JSON.stringify(original), running = claimStabilityManifest(original, claim(), 1100);
  assert.equal(JSON.stringify(original), captured); assert.equal(running.status, 'running');
  assert.equal(running.handoffToken, null); assert.equal(running.active!.documentId, 'page-one');
  assert.notEqual(original.identity, identity); assert.equal(sameStabilityIdentity(original.identity, identity), true);
  assert.throws(() => claimStabilityManifest(running, claim('duplicate-page'), 1200), /cannot claim/);
});
test('build, geometry, model and power mismatches cannot claim an existing suite', () => {
  for (const changed of [{buildId: 'other'}, {sourceWidth: 1280}, {sourceHeight: 720}, {eyewearId: 'other'},
    {hairModelId: 'other'}, {variant: 'accepted' as const}, {power: 'charging'}]) {
    assert.throws(() => claimStabilityManifest(make(), {...claim(), identity: {...identity, ...changed}}, 1100), /different build or workload/);
  }
  assert.throws(() => claimStabilityManifest(make(), {...claim(), token: 'stale'}, 1100), /cannot claim/);
});
test('a completed chunk advances once and requires a new document for the next chunk', () => {
  const running = claimStabilityManifest(make(), claim(), 1100), saved = completeStabilityManifest(running, finish());
  assert.equal(saved.status, 'ready'); assert.equal(saved.nextChunkIndex, 1); assert.equal(saved.handoffToken, 'second-token');
  assert.equal(saved.results[0]!.chunkId, 'continuous-1'); assert.equal(saved.results[0]!.documentId, 'page-one');
  assert.equal(running.results.length, 0);
  assert.throws(() => completeStabilityManifest(saved, finish()), /no longer owns/);
  assert.throws(() => completeStabilityManifest(running, finish('duplicate-page')), /no longer owns/);
  assert.throws(() => claimStabilityManifest(saved, {...claim(), token: 'second-token'}, 2100), /cannot claim/);
  const next = claimStabilityManifest(saved, {...claim('page-two'), token: 'second-token'}, 2100);
  assert.equal(next.active!.chunkId, 'restarted-1');
});
test('partial report and abrupt interruption are terminal, preserve earlier receipts and cannot auto-claim', () => {
  const first = completeStabilityManifest(claimStabilityManifest(make(), claim(), 1100), finish());
  const running = claimStabilityManifest(first, {...claim('page-two'), token: 'second-token'}, 2100);
  const partial = completeStabilityManifest(running, {...finish('page-two'), token: 'second-token', complete: false, reason: 'backgrounded'});
  assert.equal(partial.status, 'partial'); assert.equal(partial.handoffToken, null);
  assert.equal(partial.results.length, 2); assert.equal(partial.results[1]!.completed, false);
  assert.equal(partial.interrupted!.reason, 'backgrounded');
  assert.throws(() => claimStabilityManifest(partial, {...claim('page-three'), token: 'second-token'}, 3100), /cannot claim/);
  const interrupted = interruptStabilityManifest(running, {...claim('page-two'), token: 'second-token', reason: 'unexpected-reload'}, 2300);
  assert.equal(interrupted.results.length, 1); assert.equal(interrupted.interrupted!.chunkId, 'restarted-1');
  assert.equal(interrupted.active, null); assert.equal(interrupted.status, 'partial');
});
test('all eight documents complete once, with no clock concatenation or reusable final token', () => {
  let manifest = make();
  for (let index = 0; index < 8; index++) {
    const token = manifest.handoffToken!, documentId = `document-${index}`;
    manifest = claimStabilityManifest(manifest, {suiteId: 'suite', token, documentId, identity}, 1000 + index);
    manifest = completeStabilityManifest(manifest, {...finish(documentId), token, nextToken: `token-${index}`, now: 2000 + index});
  }
  assert.equal(manifest.status, 'complete'); assert.equal(manifest.results.length, 8);
  assert.equal(new Set(manifest.results.map(result => result.documentId)).size, 8);
  assert.equal(manifest.handoffToken, null); assert.equal(manifest.nextChunkIndex, 8);
});
test('storage limit and invalid scalar payloads fail without mutating a claimed chunk', async () => {
  const running = claimStabilityManifest(make(), claim(), 1100), snapshot = JSON.stringify(running);
  assert.throws(() => completeStabilityManifest(running, {...finish(), bytes: STABILITY_MAX_REPORT_BYTES + 1}), /storage limit/);
  assert.equal(JSON.stringify(running), snapshot);
  for (const bad of [{pixels: [1, 2]}, {value: new Uint8Array([1, 2])}, {value: 'data:image/png;base64,aaaa'},
    {value: NaN}, {value: new Blob(['image'])}, {metadata: {sourceIdentity: 'private'}}]) {
    assert.throws(() => encodeStabilityReport(bad));
  }
  const good = {schema: 'scalar', rows: [{fields: {capturedAtMs: 4, publishedAtMs: 15}, native: {workerMs: 8}}], metadata: {video: false}};
  assert.deepEqual(JSON.parse(await encodeStabilityReport(good).text()), good);
});
test('report receipt is bound to the actual claimed G page, build, workload and completed hair drain', () => {
  const running = claimStabilityManifest(make(), claim(), 1100);
  const report = {schema: 'ar-continuous-comparison-v1', sessionId: 'session-one', workload: identity,
    completed: true, partial: false, protocol: {studyOptions: 'g-continuous', measureMs: 180_000, order: ['g']},
    metadata: {build: {id: identity.buildId}, performanceTimeOriginMs: 100_000,
      stability: {suiteId: 'suite', chunkId: 'continuous-1', documentId: 'page-one', buildId: identity.buildId, timeOrigin: 100_000}},
    windows: [{pipeline: 'g', completed: true}], rows: [{fields: {sessionId: 'session-one', pipeline: 'g'}}], hairDeliveryDrain: {state: 'drained'}};
  const options = {...finish(), report};
  assert.doesNotThrow(() => validateStabilityCompletion(running, options));
  for (const mismatch of [
    {...report, protocol: {...report.protocol, measureMs: 30_000}},
    {...report, protocol: {...report.protocol, order: ['face-cpu']}},
    {...report, windows: [{pipeline: 'face-cpu', completed: true}]},
    {...report, metadata: {...report.metadata, stability: {...report.metadata.stability, timeOrigin: 200_000}}},
    {...report, workload: {...identity, sourceWidth: 1280}},
    {...report, rows: [{fields: {sessionId: 'old-session'}}]},
  ]) assert.throws(() => validateStabilityCompletion(running, {...options, report: mismatch}), /does not match/);
  assert.throws(() => validateStabilityCompletion(running, {...options, report: {...report, hairDeliveryDrain: {state: 'incomplete'}}}), /incomplete/);
  assert.doesNotThrow(() => validateStabilityCompletion(running, {...options, complete: false,
    report: {...report, hairDeliveryDrain: {state: 'incomplete'}}}));
  const diagnostic = {...report, rows: [{fields: {sessionId: 'old-session', pipeline: 'g'}, phase: 'excluded', exclusion: 'session-mismatch'}]};
  assert.doesNotThrow(() => validateStabilityCompletion(running, {...options, complete: false, report: diagnostic}));
  assert.throws(() => validateStabilityCompletion(running, {...options, report: diagnostic}), /does not match/);
});
