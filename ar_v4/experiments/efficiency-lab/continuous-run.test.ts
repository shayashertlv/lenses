import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ContinuousComparisonRun, CONTINUOUS_STUDIES, REVIEW_PIPELINES, sanitizeRunMetadata} from './continuous-run.ts';
import type {ContinuousRunOptions, ContinuousPipeline, RecordedRunFrame} from './continuous-run.ts';
import type {FrameInput} from './frame-profiler.ts';
import {FPS_REVIEW_CANDIDATES, FPS_REVIEW_PIPELINES} from './profiles.ts';

const options = (extra: Partial<ContinuousRunOptions> = {}): ContinuousRunOptions => ({sessionId: 'session',
  workload: {eyewearId: 'amber-horizon', hairModelId: 'hair-only', variant: 'hair', sourceWidth: 640, sourceHeight: 427}, ...extra});
function frame(at: number, sequence = at, pipeline: ContinuousPipeline = 'g', extra: Partial<FrameInput> = {}): FrameInput {
  return {sessionId: 'session', sequence, pipeline, variant: 'hair', eyewearId: 'amber-horizon', hairModelId: 'hair-only',
    capturedAtMs: at - 20, publishedAtMs: at, videoPresentedFrames: sequence * 3, videoMediaTime: at / 1000,
    videoPresentationTimeMs: null, cameraSettingFps: 30, sourceWidth: 640, sourceHeight: 427,
    sourceDrawMs: 1, detectorDrawMs: 1, sourceReadbackMs: 1, sourceHashMs: 2, faceBitmapMs: 1, faceRequestWallMs: 8,
    faceInferenceMs: 6, faceWorkerMs: 7, faceExtractionMs: .5, faceWorkerValidationMs: .5, faceClientValidationMs: .1,
    faceTransportSchedulingMs: 1, prerequisitesWaitMs: 8, detectionHashMs: 1, prepareMs: 5, finishMs: 3,
    renderMs: 8, totalMs: 20, schedulerWaitMs: 10, hairWaitMs: 0, hairInferenceMs: 9, hairExtractionMs: 1,
    hasFace: true, hasMask: true, maskMode: 'category-only', fallback: null, changedPixels: 3,
    faceDelegate: 'GPU', hairDelegate: 'GPU', gpuRenderer: 'test', cleanCameraMs: 1, composeMs: 1,
    continuityMs: .5, finalChecksMs: .5, publishMs: 1, native: {readbacks: 1}, ...extra};
}
function warmed(run = new ContinuousComparisonRun(options())): ContinuousComparisonRun {
  run.begin(0); run.switched(1, 0);
  for (const at of [100, 200, 300]) run.observe(frame(at));
  run.tick(5000); assert.equal(run.status.state, 'measuring'); return run;
}
interface WindowResult {
  completed: boolean; measureStartedAtMs: number | null; endedAtMs: number | null;
  switchedAtMs: number | null; switchWaitMs: number | null; switchToFirstFrameMs: number | null; timerOvershootMs: number | null;
  validWarmupFrames: number;
  summary: {frames: number; completedArFps: number | null; durationMs: number;
    initialNoCompletionMs: number | null; trailingNoCompletionMs: number | null;
    frameAgeMs: {median: number; p95: number; max: number} | null;
    completionGapMsIncludingEndpoints: {max: number} | null;
    gapCountsIncludingEndpoints: {over500Ms: number};
    coverage: {status: string; trackedFraction: number | null; maskedTrackedFraction: number | null};
    camera: {deliveryFps: number | null; observedSpanMs: number; source: string; spanFractionOfWindow: number | null};};
}
const windows = (run: ContinuousComparisonRun): WindowResult[] => run.export().windows as WindowResult[];
const rows = (run: ContinuousComparisonRun): RecordedRunFrame[] => run.export().rows as RecordedRunFrame[];

test('all FPS experiments run two balanced rounds with the same masked warmup and measurement defaults', () => {
  for (const direction of ['forward', 'reverse'] as const) {
    const run = new ContinuousComparisonRun(options({studyOptions: 'fps-all', direction}));
    const first = direction === 'forward' ? [...FPS_REVIEW_PIPELINES] : [...FPS_REVIEW_PIPELINES].reverse();
    const protocol = run.export().protocol as Record<string, unknown>;
    assert.deepEqual(protocol.order, [...first, ...[...first].reverse()]);
    assert.equal(protocol.studyOptions, 'fps-all');
    assert.equal(protocol.warmupMs, 5000); assert.equal(protocol.measureMs, 30000);
    assert.equal(protocol.maxWarmupMs, 15000); assert.equal(protocol.minimumTrackedMaskedWarmupFrames, 3);
    assert.equal(run.status.windowCount, 12);
  }
  assert.equal(CONTINUOUS_STUDIES['fps-all'].defaultVideo, false);
  assert.equal(CONTINUOUS_STUDIES['fps-all'].approximateMinutes, 7);
  assert.throws(() => new ContinuousComparisonRun(options({studyOptions: 'fps-all', candidate: 'face-cpu'})), /candidate/);
});

test('all twelve windows retain full denominators, zero-output time and distinct adjacent V ownership', () => {
  const run = new ContinuousComparisonRun(options({studyOptions: 'fps-all'}));
  let sequence = 0; run.begin(0);
  for (let index = 0; index < 12; index++) {
    const start = index * 35000, status = run.status;
    assert.equal(status.windowIndex, index);
    if (index > 0) {
      run.switched(status.token - 1, start);
      assert.equal(run.status.state, 'switching', 'each window requires its own acknowledgement even for adjacent V');
    }
    run.switched(status.token, start);
    run.observe(frame(start + 50, ++sequence, status.pipeline, {hasMask: false}));
    for (const offset of [100, 200, 300]) run.observe(frame(start + offset, ++sequence, status.pipeline));
    run.tick(start + 5000); assert.equal(run.status.state, 'measuring');
    run.observe(frame(start + 5010, ++sequence, status.pipeline, {capturedAtMs: start + 4990}));
    if (index !== 2) {
      run.observe(frame(start + 5100, ++sequence, status.pipeline));
      run.observe(frame(start + 34999, ++sequence, status.pipeline));
    }
    run.tick(start + 35000);
  }
  const report = run.export();
  const results = report.windows as (WindowResult & {token: number; pipeline: ContinuousPipeline; round: number})[];
  assert.equal(report.completed, true); assert.equal(report.endedAtMs, 420000);
  assert.ok(results.every(value => value.completed && value.validWarmupFrames === 3 && value.summary.durationMs === 30000));
  assert.deepEqual(results.map(value => value.token), Array.from({length: 12}, (_, index) => index + 1));
  assert.deepEqual(results.map(value => value.round), [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2]);
  assert.equal(results[5]!.pipeline, 'mask-bytes'); assert.equal(results[6]!.pipeline, 'mask-bytes');
  const stalled = results[2]!;
  assert.equal(stalled.pipeline, 'render-worker'); assert.equal(stalled.summary.frames, 0);
  assert.equal(stalled.summary.completedArFps, 0); assert.equal(stalled.summary.completionGapMsIncludingEndpoints!.max, 30000);
  assert.ok(results.filter((_, index) => index !== 2).every(value => value.summary.frames === 2 && value.summary.completedArFps === 2 / 30));
  assert.equal(rows(run).filter(value => value.phase === 'measured').length, 22);
  assert.equal(rows(run).filter(value => value.exclusion === 'captured-before-measurement').length, 12);
});

test('FPS review uses G/candidate/candidate/G and keeps strict masked warmup and full windows', () => {
  for(const candidate of FPS_REVIEW_CANDIDATES) {
    const run=new ContinuousComparisonRun(options({studyOptions:'fps-review',candidate,
      metadata:{device:{userReportedPower:'battery'}}}));
    const protocol=run.export().protocol as Record<string,unknown>;
    assert.deepEqual(protocol.order,['g',candidate,candidate,'g']);
    assert.equal(protocol.measureMs,30000);assert.equal(protocol.warmupMs,5000);
    assert.equal(protocol.minimumTrackedMaskedWarmupFrames,3);
    assert.equal(run.status.windowCount,4);
    const reverse=new ContinuousComparisonRun(options({studyOptions:'fps-review',candidate,direction:'reverse'}));
    assert.deepEqual((reverse.export().protocol as Record<string,unknown>).order,[candidate,'g','g',candidate]);
  }
  assert.throws(()=>new ContinuousComparisonRun(options({studyOptions:'fps-review',candidate:'hair-release' as never})),/candidate/);
  assert.throws(()=>new ContinuousComparisonRun(options({candidate:'face-cpu'})),/candidate/);
  const run=warmed(new ContinuousComparisonRun(options({studyOptions:'fps-review',candidate:'frame-copy'})));
  run.observe(frame(5100,5100,'g',{native:{'capture.actualPath':'canvas-video-frame-fallback','capture.fallbackReason':'dimensions-or-transform'}}));
  assert.equal(rows(run).at(-1)!.native!['capture.actualPath'],'canvas-video-frame-fallback');
});

test('ten windows reverse the same five choices, retaining separate adjacent middle windows', () => {
  const run = new ContinuousComparisonRun(options());
  const report = run.export();
  assert.deepEqual((report.protocol as {order: string[]}).order, [...REVIEW_PIPELINES, ...[...REVIEW_PIPELINES].reverse()]);
  const reverse = new ContinuousComparisonRun(options({direction: 'reverse'}));
  assert.deepEqual((reverse.export().protocol as {order: string[]}).order, ['ui', 'lens', 'region', 'publish', 'g', 'g', 'publish', 'region', 'lens', 'ui']);
  assert.equal(run.status.state, 'idle'); assert.throws(() => run.begin(NaN), /clock/);
  run.begin(0); assert.throws(() => run.begin(1), /restarted/);
});

test('focused G/U comparison uses four counterbalanced windows and measurements-only UI defaults', () => {
  const run = new ContinuousComparisonRun(options({studyOptions: 'hair-delivery'}));
  const protocol = run.export().protocol as Record<string, unknown>;
  assert.deepEqual(protocol.order, ['g', 'hair-release', 'hair-release', 'g']);
  assert.equal(protocol.studyOptions, 'hair-delivery'); assert.equal(run.status.windowCount, 4);
  assert.equal(protocol.warmupMs, 5000); assert.equal(protocol.measureMs, 30000);
  assert.equal(protocol.minimumTrackedMaskedWarmupFrames, 3);
  const reverse = new ContinuousComparisonRun(options({studyOptions: 'hair-delivery', direction: 'reverse'}));
  assert.deepEqual((reverse.export().protocol as Record<string, unknown>).order, ['hair-release', 'g', 'g', 'hair-release']);
  assert.equal(CONTINUOUS_STUDIES['hair-delivery'].defaultVideo, false);
  assert.equal(CONTINUOUS_STUDIES['hair-delivery'].approximateMinutes, 2.5);
  assert.equal(CONTINUOUS_STUDIES.review.defaultVideo, true);
  assert.equal(CONTINUOUS_STUDIES.review.approximateMinutes, 6);
  assert.throws(() => new ContinuousComparisonRun(options({studyOptions: 'other' as never})), /Unsupported.*study/);
  assert.throws(() => new ContinuousComparisonRun(options({studyOptions: '__proto__' as never})), /Unsupported.*study/);
});

test('G/U windows retain exact workload, mask warmup and adjacent U switch boundaries', () => {
  const run = new ContinuousComparisonRun(options({studyOptions: 'hair-delivery'})); let sequence = 0; run.begin(0);
  for (let index = 0; index < 4; index++) {
    const start = index * 35000, status = run.status;
    assert.equal(status.windowIndex, index); run.switched(status.token, start);
    run.observe(frame(start + 50, ++sequence, status.pipeline, {hasMask: false}));
    for (const offset of [100, 200, 300]) run.observe(frame(start + offset, ++sequence, status.pipeline));
    run.tick(start + 5000); assert.equal(run.status.state, 'measuring');
    run.observe(frame(start + 5100, ++sequence, status.pipeline));
    run.observe(frame(start + 34999, ++sequence, status.pipeline)); run.tick(start + 35000);
  }
  const report = run.export(), rounds = report.windows as (WindowResult & {token: number; pipeline: ContinuousPipeline})[];
  assert.equal(report.completed, true); assert.equal(rounds.length, 4);
  assert.ok(rounds.every(window => window.completed && window.validWarmupFrames === 3));
  assert.ok(windows(run).every(window => window.summary.durationMs === 30000 && window.summary.frames === 2));
  assert.deepEqual(rounds.map(window => window.token), [1, 2, 3, 4]);
  assert.equal(rounds[1]!.pipeline, 'hair-release'); assert.equal(rounds[2]!.pipeline, 'hair-release');
  assert.equal(report.endedAtMs, 140000);
});

test('G/V preview retains four complete windows and separate adjacent V ownership boundaries', () => {
  const run = new ContinuousComparisonRun(options({studyOptions: 'mask-preview'}));
  const protocol = run.export().protocol as Record<string, unknown>;
  assert.deepEqual(protocol.order, ['g', 'mask-bytes', 'mask-bytes', 'g']);
  assert.equal(protocol.studyOptions, 'mask-preview'); assert.equal(run.status.windowCount, 4);
  assert.equal(protocol.warmupMs, 5000); assert.equal(protocol.measureMs, 30000);
  assert.equal(protocol.minimumTrackedMaskedWarmupFrames, 3);
  assert.equal(CONTINUOUS_STUDIES['mask-preview'].defaultVideo, false);
  assert.equal(CONTINUOUS_STUDIES['mask-preview'].approximateMinutes, 2.5);
  const reverse = new ContinuousComparisonRun(options({studyOptions: 'mask-preview', direction: 'reverse'}));
  assert.deepEqual((reverse.export().protocol as Record<string, unknown>).order, ['mask-bytes', 'g', 'g', 'mask-bytes']);
  let sequence = 0; run.begin(0);
  for (let index = 0; index < 4; index++) {
    const start = index * 35000, status = run.status;
    assert.equal(status.windowIndex, index); run.switched(status.token, start);
    run.observe(frame(start + 50, ++sequence, status.pipeline, {hasMask: false}));
    for (const offset of [100, 200, 300]) run.observe(frame(start + offset, ++sequence, status.pipeline));
    run.tick(start + 5000); assert.equal(run.status.state, 'measuring');
    run.observe(frame(start + 5010, ++sequence, status.pipeline, {capturedAtMs: start + 4990}));
    run.observe(frame(start + 5100, ++sequence, status.pipeline));
    run.observe(frame(start + 34999, ++sequence, status.pipeline)); run.tick(start + 35000);
  }
  const report = run.export(), result = report.windows as (WindowResult & {token: number; pipeline: ContinuousPipeline})[];
  assert.equal(report.completed, true); assert.equal(result.length, 4); assert.equal(report.endedAtMs, 140000);
  assert.ok(result.every(window => window.completed && window.validWarmupFrames === 3));
  assert.ok(result.every(window => window.summary.durationMs === 30000 && window.summary.frames === 2));
  assert.deepEqual(result.map(window => window.token), [1, 2, 3, 4]);
  assert.deepEqual(result.map(window => window.pipeline), ['g', 'mask-bytes', 'mask-bytes', 'g']);
  assert.equal(rows(run).filter(row => row.phase === 'measured').length, 8);
});

test('wall timer times out startup with zero frames and preserves a partial downloadable report', () => {
  const run = new ContinuousComparisonRun(options()); run.begin(100);
  assert.equal(run.tick(15099).state, 'switching');
  assert.equal(run.tick(15100).state, 'partial');
  assert.equal(run.status.reason, 'warmup-timeout'); assert.equal(run.export().partial, true);
  assert.equal(run.export().completed, false); assert.equal(windows(run)[0]!.summary.frames, 0);
  assert.equal(windows(run)[0]!.summary.completedArFps, null);
});

test('separate runtime setup allows a twenty-second switch before the unchanged masked warmup', () => {
  const run = new ContinuousComparisonRun(options({maxSwitchMs: 90000})); run.begin(100);
  assert.equal(run.status.remainingMs, 90000);
  assert.equal(run.tick(20100).state, 'switching'); assert.equal(run.status.remainingMs, 70000);
  assert.equal(run.switched(1, 20100).state, 'warmup');
  assert.equal(run.status.phaseElapsedMs, 0); assert.equal(run.status.remainingMs, 5000);
  run.observe(frame(20200, 1)); run.observe(frame(20300, 2));
  run.observe(frame(20400, 3, 'g', {hasMask: false}));
  assert.equal(run.tick(25100).state, 'warmup', 'setup time and maskless output cannot satisfy warmup');
  assert.equal(run.observe(frame(25200, 4)).state, 'measuring');
  const result = windows(run)[0]!;
  assert.equal(result.switchWaitMs, 20000); assert.equal(result.validWarmupFrames, 3);
  assert.equal(result.measureStartedAtMs, 25200); assert.equal(result.summary.frames, 0);
  const exported = run.export();
  const protocol = exported.protocol as Record<string, unknown>;
  assert.equal(protocol.maxSwitchMs, 90000); assert.equal(protocol.maxWarmupMs, 15000);
  assert.equal(protocol.warmupTimeoutStartsAt, 'switch-ready');
  assert.match(String(protocol.warmup), /separate maxSwitchMs deadline/);
  assert.match(String(protocol.warmup), /setup time is excluded/);
  const timing = (exported.windows as {switchDeadlineAtMs: number | null; warmupDeadlineAtMs: number | null}[])[0]!;
  assert.equal(timing.switchDeadlineAtMs, 90100); assert.equal(timing.warmupDeadlineAtMs, 35100);
});

test('runtime setup times out without output at its own exact deadline and cannot be revived', () => {
  const run = new ContinuousComparisonRun(options({maxSwitchMs: 90000})); run.begin(100);
  assert.equal(run.tick(90099).state, 'switching'); assert.equal(run.status.remainingMs, 1);
  assert.equal(run.tick(90100).state, 'partial'); assert.equal(run.status.reason, 'switch-timeout');
  assert.equal(run.status.remainingMs, 0); assert.equal(run.status.rows, 0);
  assert.equal(run.switched(1, 90101).state, 'partial');
  const report = run.export(), result = windows(run)[0]!;
  assert.equal(report.endedAtMs, 90100); assert.equal(report.partial, true); assert.equal(report.completed, false);
  assert.equal(result.switchedAtMs, null); assert.equal(result.measureStartedAtMs, null);
  assert.equal(result.summary.completedArFps, null); assert.equal(result.summary.durationMs, 0);
  assert.equal((report.windows as {warmupDeadlineAtMs: number | null}[])[0]!.warmupDeadlineAtMs, null);
});

test('post-ready mask timeout is independent of setup duration and still applies without more frames', () => {
  const run = new ContinuousComparisonRun(options({maxSwitchMs: 90000})); run.begin(0); run.switched(1, 20000);
  run.observe(frame(20100, 1)); run.observe(frame(20200, 2));
  run.observe(frame(20300, 3, 'g', {hasMask: false})); run.observe(frame(20400, 4, 'g', {hasFace: false}));
  assert.equal(run.tick(34999).state, 'warmup');
  assert.equal(run.tick(35000).state, 'partial'); assert.equal(run.status.reason, 'warmup-timeout');
  assert.equal(windows(run)[0]!.validWarmupFrames, 2); assert.equal(windows(run)[0]!.measureStartedAtMs, null);
  assert.equal(windows(run)[0]!.summary.frames, 0); assert.equal(run.export().endedAtMs, 35000);
});

test('switch readiness must precede its deadline, while an already-satisfied warmup can start at its limit', () => {
  const before = new ContinuousComparisonRun(options({maxSwitchMs: 90000})); before.begin(0);
  assert.equal(before.switched(1, 89999.5).state, 'warmup');
  assert.equal(before.tick(90000).state, 'warmup', 'the old switch deadline no longer applies after readiness');
  const exact = new ContinuousComparisonRun(options({maxSwitchMs: 90000})); exact.begin(0);
  assert.equal(exact.switched(1, 90000).state, 'partial');
  assert.equal(exact.status.reason, 'switch-timeout'); assert.equal(windows(exact)[0]!.switchedAtMs, null);
  const warmup = new ContinuousComparisonRun(options({maxSwitchMs: 90000, maxWarmupMs: 5000}));
  warmup.begin(0); warmup.switched(1, 20000);
  for (const at of [20100, 20200, 20300]) warmup.observe(frame(at));
  assert.equal(warmup.tick(24999).state, 'warmup'); assert.equal(warmup.tick(25000).state, 'measuring');
  assert.equal(windows(warmup)[0]!.measureStartedAtMs, 25000);
  const missing = new ContinuousComparisonRun(options({maxSwitchMs: 90000, maxWarmupMs: 5000}));
  missing.begin(0); missing.switched(1, 20000);
  missing.observe(frame(20100, 1)); missing.observe(frame(20200, 2));
  assert.equal(missing.observe(frame(25000, 3)).state, 'partial', 'a third frame arriving at timeout cannot start measurement');
  assert.equal(missing.status.reason, 'warmup-timeout'); assert.equal(windows(missing)[0]!.measureStartedAtMs, null);
});

test('repeated slow setup keeps full measurement windows, endpoint stalls and acknowledgement ownership', () => {
  const run = new ContinuousComparisonRun(options({studyOptions: 'fps-review', candidate: 'frame-copy', maxSwitchMs: 90000}));
  let sequence = 0; run.begin(0);
  for (let index = 0; index < 4; index++) {
    const requestedAt = index * 55000, readyAt = requestedAt + 20000, measuredAt = readyAt + 5000;
    const status = run.status; assert.equal(status.windowIndex, index);
    run.tick(requestedAt + 16000); assert.equal(run.status.state, 'switching');
    if (index > 0) {
      run.switched(status.token - 1, readyAt);
      assert.equal(run.status.state, 'switching', 'the adjacent candidate still requires the current window token');
    }
    run.switched(status.token, readyAt);
    run.observe(frame(readyAt + 10, ++sequence, status.pipeline, {capturedAtMs: readyAt - 1}));
    for (const offset of [100, 200, 300]) run.observe(frame(readyAt + offset, ++sequence, status.pipeline));
    run.tick(measuredAt); assert.equal(run.status.state, 'measuring');
    run.observe(frame(measuredAt + 10, ++sequence, status.pipeline, {capturedAtMs: measuredAt - 1}));
    if (index !== 1) {
      run.observe(frame(measuredAt + 1000, ++sequence, status.pipeline));
      run.observe(frame(measuredAt + 2000, ++sequence, status.pipeline));
    }
    run.tick(measuredAt + 30000);
  }
  assert.equal(run.status.state, 'complete'); assert.equal(run.export().endedAtMs, 220000);
  const results = windows(run);
  assert.ok(results.every(result => result.completed && result.switchWaitMs === 20000 && result.validWarmupFrames === 3
    && result.summary.durationMs === 30000));
  const zero = results[1]!.summary;
  assert.equal(zero.frames, 0); assert.equal(zero.completedArFps, 0);
  assert.equal(zero.initialNoCompletionMs, 30000); assert.equal(zero.trailingNoCompletionMs, 30000);
  assert.equal(zero.completionGapMsIncludingEndpoints!.max, 30000);
  for (const result of results.filter((_, index) => index !== 1)) {
    assert.equal(result.summary.frames, 2); assert.equal(result.summary.completedArFps, 2 / 30);
    assert.equal(result.summary.initialNoCompletionMs, 1000); assert.equal(result.summary.trailingNoCompletionMs, 28000);
  }
  assert.equal(rows(run).filter(row => row.exclusion === 'captured-before-switch').length, 4);
  assert.equal(rows(run).filter(row => row.exclusion === 'captured-before-measurement').length, 4);
});

test('omitting maxSwitchMs retains switch-inclusive legacy timeouts and exports that policy', () => {
  const slow = new ContinuousComparisonRun(options()); slow.begin(100);
  assert.equal(slow.status.remainingMs, 15000); assert.equal(slow.switched(1, 20100).state, 'partial');
  assert.equal(slow.status.reason, 'warmup-timeout'); assert.equal(windows(slow)[0]!.switchedAtMs, null);
  const ready = new ContinuousComparisonRun(options()); ready.begin(100); ready.switched(1, 14100);
  assert.equal(ready.tick(15099).state, 'warmup'); assert.equal(ready.tick(15100).state, 'partial');
  assert.equal(ready.status.reason, 'warmup-timeout');
  const report = ready.export(), protocol = report.protocol as Record<string, unknown>;
  assert.equal(Object.hasOwn(protocol, 'maxSwitchMs'), false);
  assert.equal(protocol.warmupTimeoutStartsAt, 'switch-requested'); assert.match(String(protocol.warmup), /maximum includes switch wait/);
  const timing = (report.windows as {switchDeadlineAtMs: number | null; warmupDeadlineAtMs: number | null}[])[0]!;
  assert.equal(timing.switchDeadlineAtMs, 15100); assert.equal(timing.warmupDeadlineAtMs, 15100);
});

test('a separate switch deadline must be finite and positive', () => {
  for (const maxSwitchMs of [-1, 0, NaN, Infinity])
    assert.throws(() => new ContinuousComparisonRun(options({maxSwitchMs})), /switch deadline/);
});

test('five seconds alone cannot replace three tracked and masked warmup frames', () => {
  const run = new ContinuousComparisonRun(options()); run.begin(0); run.switched(1, 0);
  run.observe(frame(100, 1)); run.observe(frame(200, 2));
  run.observe(frame(300, 3, 'g', {hasMask: false})); run.observe(frame(400, 4, 'g', {hasFace: false}));
  assert.equal(run.tick(6000).state, 'warmup');
  assert.equal(run.observe(frame(6100, 5)).state, 'measuring');
  assert.equal(windows(run)[0]!.measureStartedAtMs, 6100);
  assert.equal(windows(run)[0]!.summary.frames, 0, 'third warmup image is excluded from measurement');
  run.observe(frame(6120, 6, 'g', {capturedAtMs: 6100}));
  assert.equal(windows(run)[0]!.summary.frames, 1);
});

test('zero-output measured window uses its entire duration and endpoint stall even without a final frame', () => {
  const run = warmed();
  assert.equal(run.tick(35000).state, 'switching');
  const result = windows(run)[0]!;
  assert.equal(result.completed, true); assert.equal(result.summary.durationMs, 30000);
  assert.equal(result.summary.completedArFps, 0); assert.equal(result.summary.coverage.status, 'no-frames');
  assert.equal(result.summary.completionGapMsIncludingEndpoints!.max, 30000);
  assert.equal(result.summary.gapCountsIncludingEndpoints.over500Ms, 1);
  assert.equal(run.status.pipeline, 'publish');
});

test('publication age, full-window throughput and leading/trailing stalls stay distinct', () => {
  const run = warmed();
  run.observe(frame(6000)); run.observe(frame(7000)); run.tick(35000);
  const result = windows(run)[0]!.summary;
  assert.equal(result.frames, 2); assert.equal(result.completedArFps, 2 / 30);
  assert.equal(result.frameAgeMs!.p95, 20);
  assert.equal(result.initialNoCompletionMs, 1000); assert.equal(result.trailingNoCompletionMs, 28000);
  assert.equal(result.completionGapMsIncludingEndpoints!.max, 28000);
});

test('capture and publication must both lie inside the half-open measurement window', () => {
  const run = warmed();
  run.observe(frame(5000, 5, 'g', {capturedAtMs: 4999}));
  run.observe(frame(5010, 6, 'g', {capturedAtMs: 5000}));
  run.observe(frame(35000, 7, 'g', {capturedAtMs: 34980}));
  assert.equal(windows(run)[0]!.summary.frames, 1);
  assert.equal(rows(run)[3]!.exclusion, 'captured-before-measurement');
  assert.equal(rows(run)[5]!.exclusion, 'previous-or-unrequested-pipeline');
  assert.equal(rows(run)[4]!.phase, 'measured');
});

test('switch waits, stale captures and stale acknowledgement tokens cannot enter the next window', () => {
  const run = warmed(); run.tick(35000);
  run.observe(frame(35100, 4, 'g')); run.switched(1, 35200);
  assert.equal(run.status.state, 'switching');
  run.switched(2, 35500);
  run.observe(frame(35520, 5, 'publish', {capturedAtMs: 35499}));
  run.observe(frame(35600, 6, 'publish')); run.observe(frame(35700, 7, 'publish')); run.observe(frame(35800, 8, 'publish'));
  run.tick(40500);
  const result = windows(run)[1]!;
  assert.equal(result.switchWaitMs, 500); assert.equal(result.switchToFirstFrameMs, 600);
  assert.equal(result.validWarmupFrames, 3); assert.equal(result.measureStartedAtMs, 40500);
  assert.equal(rows(run).find(row => row.fields.sequence === 5)!.exclusion, 'captured-before-switch');
});

test('late timer records overshoot separately and starts the next switch at actual observation time', () => {
  const run = warmed(); run.tick(40000);
  assert.equal(windows(run)[0]!.endedAtMs, 35000); assert.equal(windows(run)[0]!.timerOvershootMs, 5000);
  const exported = run.export().windows as {requestedAtMs: number | null}[];
  assert.equal(exported[1]!.requestedAtMs, 40000);
});

test('independent camera callback counts still measure delivery during complete AR stalls', () => {
  const run = warmed();
  run.observeVideo({atMs: 5100, presentedFrames: 10, mediaTime: 5.1});
  run.observeVideo({atMs: 6100, presentedFrames: 40, mediaTime: 6.1});
  run.observeVideo({atMs: 7100, presentedFrames: 70, mediaTime: 7.1});
  run.tick(35000);
  const summary = windows(run)[0]!.summary;
  assert.equal(summary.completedArFps, 0); assert.equal(summary.camera.deliveryFps, 30);
  assert.equal(summary.camera.observedSpanMs, 2000); assert.equal(summary.camera.spanFractionOfWindow, 2 / 30);
  assert.equal(summary.camera.source, 'independent-video-frame-callback');
});

test('missing camera callback evidence is unavailable rather than zero or camera settings', () => {
  const run = warmed(); run.observe(frame(6000)); run.tick(35000);
  assert.equal(windows(run)[0]!.summary.camera.deliveryFps, null);
  assert.equal(windows(run)[0]!.summary.camera.source, 'unavailable');
});

test('coverage failures stay in throughput statistics rather than making an easier workload look faster', () => {
  const run = warmed(); run.observe(frame(6000)); run.observe(frame(7000, 7000, 'g', {hasFace: false, hasMask: false}));
  run.observe(frame(8000, 8000, 'g', {hasMask: false})); run.tick(35000);
  const summary = windows(run)[0]!.summary;
  assert.equal(summary.frames, 3); assert.equal(summary.completedArFps, .1);
  assert.equal(summary.coverage.status, 'partial'); assert.equal(summary.coverage.trackedFraction, 2 / 3);
  assert.equal(summary.coverage.maskedTrackedFraction, .5);
});

test('session, models, variant and source dimensions are fixed and violations stop partial with retained evidence', () => {
  const changes: Partial<FrameInput>[] = [{sessionId: 'different'}, {eyewearId: 'tom-ford'}, {hairModelId: 'multiclass'},
    {variant: 'accepted'}, {sourceWidth: 480}, {sourceHeight: 640}];
  for (const change of changes) {
    const run = warmed(); run.observe(frame(6000, 10, 'g', change));
    assert.equal(run.status.state, 'partial'); assert.equal(windows(run)[0]!.summary.frames, 0);
    assert.equal(rows(run).at(-1)!.phase, 'excluded');
    assert.equal(run.export().partial, true);
  }
});

test('duplicate, invalid and out-of-order source timings never inflate completed throughput', () => {
  const run = warmed(); run.observe(frame(6000, 5)); run.observe(frame(6100, 5));
  run.observe(frame(6200, 6, 'g', {capturedAtMs: 6300})); run.observe(frame(6300, 7, 'g', {sourceDrawMs: NaN}));
  run.tick(35000);
  assert.equal(windows(run)[0]!.summary.frames, 2);
  assert.equal(rows(run)[4]!.exclusion, 'duplicate-sequence');
  assert.equal(rows(run)[5]!.exclusion, 'invalid-frame-clock-or-sequence');
  assert.deepEqual(rows(run)[6]!.invalidFields, ['sourceDrawMs']);
  assert.equal(rows(run)[6]!.fields.sourceDrawMs, null);
});

test('cancellation and clock rollback work without another completed frame', () => {
  const run = warmed(); run.observe(frame(6000));
  run.cancel('document-hidden', 20000);
  assert.equal(run.status.state, 'partial'); assert.equal(windows(run)[0]!.summary.durationMs, 15000);
  assert.equal(windows(run)[0]!.summary.trailingNoCompletionMs, 14000);
  run.cancel('second-reason', 22000); assert.equal(run.status.reason, 'document-hidden');
  const backward = warmed(); backward.tick(4999);
  assert.equal(backward.status.reason, 'nonmonotonic-clock');
});

test('retention stops explicitly at capacity rather than overwriting early raw rows', () => {
  const run = warmed(new ContinuousComparisonRun(options({rowLimit: 4})));
  run.observe(frame(6000)); run.observe(frame(7000));
  assert.equal(run.status.reason, 'frame-row-limit'); assert.equal(rows(run).length, 4);
  assert.equal(rows(run)[0]!.fields.publishedAtMs, 100);
  assert.deepEqual(run.export().retention, {frameRows: 4, videoRows: 0, limitPerKind: 4,
    rejectedRows: 1, rejectedVideoObservations: 0, truncated: false,
    policy: 'No ring buffer: reaching either limit stops the run as partial and reports the rejected observation.'});
});

test('video retention is separately bounded and backwards observations are explicitly counted', () => {
  const run = new ContinuousComparisonRun(options({rowLimit: 2})); run.begin(0);
  run.observeVideo({atMs: 1, presentedFrames: 1, mediaTime: null});
  run.observeVideo({atMs: 1, presentedFrames: 1, mediaTime: null});
  run.observeVideo({atMs: 2, presentedFrames: 2, mediaTime: null});
  run.observeVideo({atMs: 3, presentedFrames: 3, mediaTime: null});
  assert.equal(run.status.reason, 'video-row-limit');
  assert.equal((run.export().retention as {rejectedVideoObservations: number}).rejectedVideoObservations, 2);
});

test('all ten rounds complete, retain separate middle windows, and summaries use exact fixed durations', () => {
  const run = new ContinuousComparisonRun(options()); let sequence = 0; run.begin(0);
  for (let index = 0; index < 10; index++) {
    const start = index * 35000, status = run.status;
    assert.equal(status.windowIndex, index); run.switched(status.token, start);
    for (const offset of [100, 200, 300]) run.observe(frame(start + offset, ++sequence, status.pipeline));
    run.tick(start + 5000);
    run.observe(frame(start + 5100, ++sequence, status.pipeline));
    run.observe(frame(start + 34999, ++sequence, status.pipeline));
    run.tick(start + 35000);
  }
  assert.equal(run.status.state, 'complete'); assert.equal(run.export().completed, true);
  assert.equal(run.export().partial, false);
  assert.equal(windows(run).length, 10);
  assert.ok(windows(run).every(window => window.completed && window.summary.frames === 2 && window.summary.durationMs === 30000));
  assert.equal(rows(run).length, 50);
});

test('normalization protects raw ownership and removes private image identities and unexpected arrays', () => {
  const run = warmed(new ContinuousComparisonRun(options({metadata: {buildId: 'release-v1',
    nested: {sourceSHA256: 'private-id', userAgent: 'test', imageData: 'private-image', values: [1, 2]}}})));
  const input = frame(6000, 6, 'g', {native: {'frame.sourceSHA256': 'private-id', 'frame.sourceIdentity.sessionId': 'private-id',
    'frame.landmarks': 'private-coordinates', 'data.imageData': 'private-image', readbacks: 1}});
  Object.assign(input, {sourcePngDataUrl: 'data:image/png;base64,private', landmarks: [1, 2]});
  run.observe(input); input.native!.readbacks = 999;
  const snapshot = run.export(), serialized = JSON.stringify(snapshot);
  assert.equal(serialized.includes('private-id'), false); assert.equal(serialized.includes('private-image'), false);
  assert.equal(serialized.includes('private-coordinates'), false); assert.equal(serialized.includes('sourcePngDataUrl'), false);
  assert.equal(serialized.includes('release-v1'), true);
  const first = rows(run); first.at(-1)!.native!.readbacks = 77;
  assert.equal(rows(run).at(-1)!.native!.readbacks, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(sanitizeRunMetadata({buffer: new Uint8Array(4), data: 'data:image/png;base64,pixel'}))), {});
});

test('large continuous report retains rows beyond the ordinary profiler ring capacity', () => {
  const run = warmed(new ContinuousComparisonRun(options({measureMs: 50000})));
  for (let index = 0; index < 4200; index++) run.observe(frame(5030 + index * 10, 10000 + index));
  assert.equal(rows(run).length, 4203); assert.equal(rows(run)[0]!.fields.publishedAtMs, 100);
  assert.equal(windows(run)[0]!.summary.frames, 4200);
});

test('per-image run retains eight distinct full-duration windows in forward and reverse order', () => {
  const run = new ContinuousComparisonRun(options({studyOptions: 'per-image'}));
  const order = ['g', 'mask-bytes', 'gl-state', 'word-compose', 'word-compose', 'gl-state', 'mask-bytes', 'g'];
  let sequence = 0; run.begin(0);
  for (const [index, id] of order.entries()) {
    const start = index * 35000, status = run.status;
    assert.equal(status.pipeline, id); run.switched(status.token, start);
    for (const offset of [100, 200, 300]) run.observe(frame(start+offset, ++sequence, status.pipeline));
    run.tick(start+5000); run.observe(frame(start+6000, ++sequence, status.pipeline)); run.tick(start+35000);
  }
  assert.equal(run.status.state, 'complete');
  assert.equal(windows(run).length, 8);
  assert.ok(windows(run).every(value => value.summary.durationMs === 30000 && value.summary.frames === 1));
  const reverse = new ContinuousComparisonRun(options({studyOptions: 'per-image', direction: 'reverse'}));
  assert.deepEqual((reverse.export().protocol as {order: string[]}).order, ['word-compose', 'gl-state', 'mask-bytes', 'g', 'g', 'mask-bytes', 'gl-state', 'word-compose']);
});
