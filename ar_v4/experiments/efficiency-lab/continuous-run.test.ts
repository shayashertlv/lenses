import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ContinuousComparisonRun, CONTINUOUS_STUDIES, REVIEW_PIPELINES, sanitizeRunMetadata} from './continuous-run.ts';
import type {ContinuousRunOptions, ContinuousPipeline, RecordedRunFrame} from './continuous-run.ts';
import type {FrameInput} from './frame-profiler.ts';

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
