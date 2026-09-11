import assert from 'node:assert/strict';
import {test} from 'node:test';
import {PROFILES} from '../profiles.ts';
import {BOUNDED_FENCE_REASON, checkProtocol, mechanismUse, noAuxiliaryCamera, summarizeMechanisms} from './protocol.mjs';

function example(options, drop = 0, first = false) {
  const bytes = 16 * 8 * 4, calls = options.reuseSourcePixels ? 1 : 2;
  const warm = Number(options.prewarmTemples && first);
  const geometry = {rearDrop: {dropM: drop}, hairPreview: {sourceSHA256: 'a'.repeat(64)}};
  const pipeline = {baselineReadbackCalls: 0, baselineReadbackBytes: 0,
    branchReadbackCalls: Number(drop !== 0), branchReadbackBytes: Number(drop !== 0) * bytes,
    zeroDropBranchSkipped: drop === 0,
    speedLab: {sourceCopiesAvoided: Number(options.fewerCopies), prewarmRequested: options.prewarmTemples,
      prewarmAttempted: Boolean(warm), prewarmCompleted: options.prewarmTemples, prewarmFailure: null,
      prewarmReadbackCalls: warm, prewarmReadbackBytes: warm * bytes},
    native: {sharedCameraReady: true, sharedCameraFailure: null, cleanSubmitMs: options.reuseSourcePixels ? 0 : 1,
      sharedReadback: options.asyncReadback ? null : {readbackCalls: calls, readbackBytes: calls * bytes},
      speedLab: {reuseSourcePixelsRequested: options.reuseSourcePixels, reuseSourcePixelsUsed: options.reuseSourcePixels,
        sourceReuseFallback: null, asyncReadbackRequested: options.asyncReadback, asyncReadbackUsed: options.asyncReadback,
        asyncFallback: null, pbo: options.asyncReadback ? {completed: true, fallbackReason: null,
          queuedCalls: calls, queuedBytes: calls * bytes, retrievedCalls: calls, retrievedBytes: calls * bytes} : null}}};
  const stats = {candidatePerformance: {nativePipeline: pipeline,
    cpuReadbackCalls: calls + warm + Number(drop !== 0), cpuReadbackBytes: (calls + warm + Number(drop !== 0)) * bytes,
    speedLab: {options, sourceCanvasBorrowed: options.fewerCopies, sourceCopyBytesAvoided: options.fewerCopies ? bytes : 0,
      sourceIdentity: {sourceSHA256: geometry.hairPreview.sourceSHA256}, sourceFallbackReason: null}}};
  return {stats, geometry, pipeline};
}
const passed = checks => Object.values(checks).every(Boolean);
test('all five profile contracts count source reuse, async retrieval, branch and first-use warm costs', () => {
  for (const name of ['source', 'warm', 'copies', 'async', 'combined']) for (const drop of [0, .02]) for (const first of [true, false]) {
    const options = PROFILES[name].options, value = example(options, drop, first);
    assert.equal(passed(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8)), true, `${name}/${drop}/${first}`);
  }
});
test('completed async work cannot masquerade as zero CPU download bytes', () => {
  const options = PROFILES.async.options, value = example(options);
  value.stats.candidatePerformance.cpuReadbackBytes = 0;
  assert.equal(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8).totalCpuReads, false);
  value.pipeline.native.speedLab.pbo.retrievedCalls = 0;
  assert.equal(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8).readbacks, false);
});
test('a source or async fallback never passes the eligible fast-path assertion', () => {
  for (const profile of ['source', 'async']) {
    const options = PROFILES[profile].options, value = example(options);
    if (profile === 'source') {value.pipeline.native.speedLab.reuseSourcePixelsUsed = false; value.pipeline.native.speedLab.sourceReuseFallback = 'ineligible';}
    else {value.pipeline.native.speedLab.asyncReadbackUsed = false; value.pipeline.native.speedLab.asyncFallback = 'fence failed';}
    assert.equal(passed(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8)), false);
  }
});
test('hair-off/no-face may read one async beauty image but never claim an auxiliary camera pair', () => {
  const native = {sharedCameraReady: false, cleanSubmitMs: 0, sharedReadback: null,
    speedLab: {reuseSourcePixelsUsed: false, pbo: {queuedCalls: 1, retrievedCalls: 1}}};
  assert.equal(noAuxiliaryCamera(native), true);
  native.speedLab.pbo.queuedCalls = 2; assert.equal(noAuxiliaryCamera(native), false);
});

const softwarePolicy = {allowAsyncFallback: true, generated: false, renderer: 'ANGLE (Google, SwiftShader Device (Subzero))'};
function timedOut(options = PROFILES.combined.options) {
  const value = example(options, .02, true), native = value.pipeline.native, calls = options.reuseSourcePixels ? 1 : 2;
  native.sharedReadback = {readbackCalls: calls, readbackBytes: calls * 512};
  native.speedLab.asyncReadbackUsed = false; native.speedLab.asyncFallback = BOUNDED_FENCE_REASON;
  Object.assign(native.speedLab.pbo, {completed: false, fallbackReason: BOUNDED_FENCE_REASON,
    retrievedCalls: 0, retrievedBytes: 0, waitMs: 505.4, polls: 107});
  return value;
}
test('only explicitly permitted recorded SwiftShader bounded timeouts can pass mechanism fallback', () => {
  for (const options of [PROFILES.async.options, PROFILES.combined.options]) {
    const {stats, geometry} = timedOut(options);
    assert.equal(passed(checkProtocol('candidate', options, stats, geometry, 16, 8)), false);
    assert.equal(passed(checkProtocol('candidate', options, stats, geometry, 16, 8, softwarePolicy)), true);
    for (const policy of [{...softwarePolicy, allowAsyncFallback: false}, {...softwarePolicy, generated: true},
      {...softwarePolicy, renderer: 'Intel Arc D3D11'}, {...softwarePolicy, renderer: null}])
      assert.equal(passed(checkProtocol('candidate', options, stats, geometry, 16, 8, policy)), false);
  }
});
test('declared fallback preserves precise fence, retrieval and synchronous accounting requirements', () => {
  const corruptions = [
    value => {value.pipeline.native.speedLab.asyncFallback = 'fence failed';},
    value => {value.pipeline.native.speedLab.pbo.fallbackReason = 'context lost';},
    value => {value.pipeline.native.speedLab.pbo.waitMs = 499.9;},
    value => {value.pipeline.native.speedLab.pbo.waitMs = Infinity;},
    value => {value.pipeline.native.speedLab.pbo.polls = 0;},
    value => {value.pipeline.native.speedLab.pbo.queuedCalls = 0;},
    value => {value.pipeline.native.speedLab.pbo.queuedBytes = 0;},
    value => {value.pipeline.native.speedLab.pbo.retrievedCalls = 1;},
    value => {value.pipeline.native.speedLab.pbo.retrievedBytes = 512;},
    value => {value.pipeline.native.sharedReadback.readbackCalls = 0;},
    value => {value.pipeline.native.sharedReadback.readbackBytes = 0;},
    value => {value.stats.candidatePerformance.cpuReadbackBytes = 0;},
    value => {value.pipeline.native.speedLab.reuseSourcePixelsUsed = false;},
  ];
  for (const corrupt of corruptions) {
    const value = timedOut(); corrupt(value);
    assert.equal(passed(checkProtocol('candidate', PROFILES.combined.options, value.stats, value.geometry, 16, 8, softwarePolicy)), false);
  }
});
test('fallback receipts and aggregate counts never claim an async fast path', () => {
  const options = PROFILES.combined.options, fallback = timedOut(), success = example(options);
  const uses = [fallback, success].map(value => mechanismUse(options, value.stats, value.geometry, 16, 8, softwarePolicy));
  assert.deepEqual(uses[0], {actualFastPathUsed: false, actualAsyncReadbackUsed: false, asyncFallbackAllowed: true,
    acceptedAsyncFallback: true, asyncFallbackReason: BOUNDED_FENCE_REASON});
  assert.equal(uses[1].actualFastPathUsed, true); assert.equal(uses[1].actualAsyncReadbackUsed, true);
  const summary = summarizeMechanisms(uses.map(mechanism => ({mechanism})));
  assert.equal(summary.cases, 2); assert.equal(summary.actualFastPathCases, 1);
  assert.equal(summary.actualAsyncReadbackCases, 1); assert.equal(summary.acceptedAsyncFallbackCases, 1);
  assert.equal(summary.asyncFallbackCases, 1); assert.deepEqual(summary.asyncFallbackReasons, {[BOUNDED_FENCE_REASON]: 1});
});
test('source mechanism checks cannot overwrite source PNG equality', () => {
  const options = PROFILES.source.options, value = example(options);
  const checks = {source: false, ...checkProtocol('candidate', options, value.stats, value.geometry, 16, 8)};
  assert.equal(checks.source, false); assert.equal(checks.sourceMechanism, true); assert.equal(passed(checks), false);
});
