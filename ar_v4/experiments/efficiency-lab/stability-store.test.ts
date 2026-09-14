import assert from 'node:assert/strict';
import {test} from 'node:test';
import {claimStabilityManifest, completeStabilityManifest, createStabilityManifest,
  createStabilityPlan, encodeStabilityReport, interruptStabilityManifest, sameStabilityIdentity,
  STABILITY_MAX_REPORT_BYTES, validateStabilityCompletion} from './stability-store.ts';
import type {StabilityIdentity, StabilityManifest, StabilitySelection} from './stability-store.ts';

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

const makeReadback = (selection: StabilitySelection = 'all', direction: 'forward' | 'reverse' = 'forward') =>
  createStabilityManifest({identity, selection, direction, mode: 'readback'}, {suiteId: 'suite', token: 'first-token', now: 1000});
function readbackReport(manifest: StabilityManifest) {
  const chunk = manifest.plan[manifest.nextChunkIndex]!, active = manifest.active!;
  const pipeline = chunk.condition === 'readback-diagnostic' ? 'g-readback' : 'g';
  const start = 5100.125, end = start + 180_000;
  return {schema: 'ar-continuous-comparison-v1', sessionId: 'session-' + active.documentId, workload: identity,
    completed: true, partial: false,
    protocol: {studyOptions: pipeline === 'g' ? 'g-continuous' : 'g-readback-continuous', measureMs: 180_000, order: [pipeline]},
    metadata: {build: {id: identity.buildId}, performanceTimeOriginMs: 100_000,
      stability: {suiteId: manifest.id, chunkId: chunk.id, documentId: active.documentId, buildId: identity.buildId,
        timeOrigin: 100_000, condition: chunk.condition, chunkIndex: chunk.index}},
    windows: [{index: 0, pipeline, completed: true, measureStartedAtMs: start, plannedEndAtMs: end, endedAtMs: end,
      summary: {startAtMs: start, endAtMs: end, durationMs: 180_000}}],
    analysisBins: Array.from({length: 6}, (_, index) => ({index, windowIndex: 0, completed: true,
      plannedStartAtMs: start + index * 30_000, plannedEndAtMs: start + (index + 1) * 30_000,
      summary: {startAtMs: start + index * 30_000, endAtMs: start + (index + 1) * 30_000, durationMs: 30_000}})),
    rows: [{phase: 'measured', fields: {sessionId: 'session-' + active.documentId, pipeline}}],
    hairDeliveryDrain: {state: 'drained'}};
}
test('readback plan has two independent uninterrupted documents, reversible order and isolated selections', () => {
  const forward = createStabilityPlan('all', 'forward', 'readback'), reverse = createStabilityPlan('all', 'reverse', 'readback');
  assert.deepEqual(forward.map(chunk => chunk.condition), ['readback-control', 'readback-diagnostic']);
  assert.deepEqual(reverse.map(chunk => chunk.condition), ['readback-diagnostic', 'readback-control']);
  assert.equal(forward.reduce((sum, chunk) => sum + chunk.totalMeasureMs, 0), 360_000);
  for (const chunk of forward) {assert.equal(chunk.windowCount, 1); assert.equal(chunk.measureMs, 180_000);}
  assert.equal(createStabilityPlan('readback-control', 'reverse', 'readback').length, 1);
  assert.equal(makeReadback().schema, 'ar-g-readback-suite-v1');
  assert.equal(make().schema, 'ar-g-stability-suite-v1');
  assert.throws(() => createStabilityPlan('continuous', 'forward', 'readback'), /Invalid/);
  assert.throws(() => createStabilityPlan('readback-diagnostic', 'forward'), /Invalid/);
});
test('readback receipts require the selected pipeline, condition, document, model and full timing', () => {
  for (const selection of ['readback-control', 'readback-diagnostic'] as const) {
    const running = claimStabilityManifest(makeReadback(selection), claim(), 1100), report = readbackReport(running);
    const options = {...finish(), report};
    assert.doesNotThrow(() => validateStabilityCompletion(running, options));
    for (const mismatch of [
      {...report, protocol: {...report.protocol, order: [selection === 'readback-control' ? 'g-readback' : 'g']}},
      {...report, protocol: {...report.protocol, studyOptions: 'g-restart'}},
      {...report, metadata: {...report.metadata, stability: {...report.metadata.stability, condition: 'continuous'}}},
      {...report, metadata: {...report.metadata, stability: {...report.metadata.stability, chunkIndex: 1}}},
      {...report, metadata: {...report.metadata, stability: {...report.metadata.stability, documentId: 'foreign'}}},
      {...report, workload: {...identity, hairModelId: 'selfie-multiclass'}},
      {...report, rows: [{phase: 'measured', fields: {sessionId: 'foreign', pipeline: report.protocol.order[0]}}]},
      {...report, windows: [{...report.windows[0]!, endedAtMs: report.windows[0]!.endedAtMs - 1}]},
      {...report, analysisBins: report.analysisBins.slice(0, 5)},
      {...report, analysisBins: report.analysisBins.map((bin, index) => index === 2 ? {...bin, summary: {...bin.summary, durationMs: 29_000}} : bin)},
    ]) assert.throws(() => validateStabilityCompletion(running, {...options, report: mismatch}), /does not match/);
  }
});
test('readback control saves once, stale handoffs fail and second document must claim diagnostic separately', () => {
  const control = claimStabilityManifest(makeReadback(), claim(), 1100), report = readbackReport(control);
  validateStabilityCompletion(control, {...finish(), report});
  const next = completeStabilityManifest(control, finish());
  assert.equal(next.plan[next.nextChunkIndex]!.condition, 'readback-diagnostic');
  for (const stale of [claim('page-two'), {...claim(), token: 'second-token'}])
    assert.throws(() => claimStabilityManifest(next, stale, 2100), /cannot claim/);
  const diagnostic = claimStabilityManifest(next, {...claim('page-two'), token: 'second-token'}, 2100);
  assert.throws(() => validateStabilityCompletion(diagnostic, {...finish('page-two'), token: 'second-token', report}), /does not match/);
  const correct = readbackReport(diagnostic);
  validateStabilityCompletion(diagnostic, {...finish('page-two'), token: 'second-token', report: correct});
  const done = completeStabilityManifest(diagnostic, {...finish('page-two'), token: 'second-token'});
  assert.equal(done.status, 'complete'); assert.equal(done.results.length, 2); assert.equal(done.handoffToken, null);
});
test('partial diagnostic preserves earlier G control and cannot be mistaken for a completed measurement', () => {
  const saved = completeStabilityManifest(claimStabilityManifest(makeReadback(), claim(), 1100), finish());
  const running = claimStabilityManifest(saved, {...claim('page-two'), token: 'second-token'}, 2100);
  const report = {...readbackReport(running), completed: false, partial: true, analysisBins: [],
    windows: [{pipeline: 'g-readback', completed: false, measureStartedAtMs: null, endedAtMs: null}]};
  assert.doesNotThrow(() => validateStabilityCompletion(running, {...finish('page-two'), token: 'second-token', report, complete: false}));
  assert.throws(() => validateStabilityCompletion(running, {...finish('page-two'), token: 'second-token', report}), /does not match/);
  const partial = completeStabilityManifest(running, {...finish('page-two'), token: 'second-token', complete: false, reason: 'hidden'});
  assert.equal(partial.status, 'partial'); assert.equal(partial.results.length, 2);
  assert.equal(partial.results[0]!.completed, true); assert.equal(partial.results[1]!.completed, false);
  assert.equal(partial.handoffToken, null);
});
