/** The real runPipeline under a fake camera, fake workers and a fake renderer: what the hair schedule does to the frames.
 *  Real timers: thresholds are relative to the frames actually published wherever possible. The pipeline and the fake
 *  renderer read one injected clock (`clock` below), so instruments that compare their timestamps compare equal numbers,
 *  not two wall-clock reads a loaded machine can pull apart. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runPipeline} from '../src/pipeline/pipeline.ts';
import {DEFAULT_HAIR_SCHEDULE} from '../src/hair/mask-reuse.ts';
import type {HairSchedule, MaskWarp} from '../src/hair/mask-reuse.ts';
import type {FrameInput} from '../src/pipeline/profiler.ts';
import type {HairJobsSummary, PipelineContext} from '../src/pipeline/pipeline.ts';

/** A harness timer: its callback starts a fresh reading of the shared clock (see `clock`). */
const sleep = (ms: number) => new Promise(resolve => setTimeout(() => {turnTime = null; resolve(undefined);}, ms));
/** Real time, read once per callback: every read until the current callback AND all the microtasks it queues have run
 *  returns the first read. It is reset by setImmediate (after them) and at the start of every harness timer callback
 *  (`sleep`, the camera), so two harness timers that fire in one timers phase still read two times. The pipeline's draw
 *  start and the fake renderer's finish() run in one turn, so they get the same number even when the OS deschedules the
 *  process or a GC pause lands between them (a 1.5 ms wall-clock tolerance failed once in two full suite runs: '16.70 +
 *  35.98 ms is no gap between consecutive draws'). A wait on an already-resolved promise is a microtask, so it reads 0 ms
 *  (the reset used to be a microtask itself, queued ahead of such a continuation: a GC pause between them read as a 5+ ms
 *  hair admission wait, WP1 review E/F). Awaits on timers and on WebCrypto still see time pass. */
let turnTime: number | null = null;
const clock = (): number => {
  if (turnTime === null) {turnTime = performance.now(); setImmediate(() => {turnTime = null;});}
  return turnTime;
};
/** Busy-wait: a draw the OS descheduled, or a GC pause, inside the synchronous finish(). */
const stall = (ms: number) => {const until = performance.now() + ms; while (performance.now() < until) { /* stalled */ }};
const g = globalThis as Record<string, unknown>;
g.MediaStream ??= class {};
let fill = 0;
g.document = {createElement: () => {
  const canvas: Record<string, unknown> = {width: 300, height: 150};
  canvas.getContext = () => ({drawImage() {}, save() {}, restore() {}, translate() {}, rotate() {},
    getImageData: (_x: number, _y: number, w: number, h: number) => ({data: new Uint8ClampedArray(w * h * 4).fill(fill), width: w, height: h})});
  return canvas;
}};
g.createImageBitmap = async (source: {width: number; height: number}) => ({width: source.width, height: source.height, close() {}});

/** Landmarks of the frame captured at `timeMs`: the head drifts `pxPerMs` right in a 320 px wide frame. */
const landmarksAt = (timeMs: number, pxPerMs: number) => Array.from({length: 478}, (_, i) => ({
  x: 0.3 + 0.4 * ((i * 37) % 100) / 100 + timeMs * pxPerMs / 320, y: 0.2 + 0.6 * ((i * 53) % 100) / 100, z: 0}));

interface Finish {mask: boolean; warp: MaskWarp | null; carried: boolean; waitMs: number; at: number; order: number;}
interface Run {rows: FrameInput[]; finishes: Finish[]; hairCalls: number; errors: string[]; hairErrors: string[]; facelessAt: Set<number>;
  jobs: HairJobsSummary | null; recentJobs: HairJobsSummary | null; describe: string; auditRequestedAt: number | null; auditedFinish: number | null;
  /** Frames (sequence) whose mask was delivered only after the renderer had posed that frame. */
  maskAfterPrepare: Set<number>;
  /** Every hair result, by its frame's sequence, with the clock when the fake worker handed it over. */
  delivered: {sequence: number; at: number; order: number}[];
  /** The clock when the detector was called for the frame captured at each time (the pipeline's face post). */
  facePostAt: Map<number, number>;
  /** The same posts in event order (one counter shared with Finish.order): two events in one turn share a clock reading. */
  facePostOrder: Map<number, number>;
  /** The clock when the job log was read (pipeline.hairJobs), in the same turn, and its place in the event order. */
  jobsQueriedAt: number | null; jobsQueriedOrder: number | null;}
interface Options {schedule?: HairSchedule; hairMs: number; faceMs?: number; auditAtMs?: number; hangFromCall?: number; rejectCall?: number; durationMs: number; pxPerMs?: number; faceless?: (timeMs: number) => boolean;
  /** Hold each hair job's result until its own frame has been posed (renderer.prepare), so the frame must wait for it
   *  whatever the machine load (a frame dropped before its pose releases its job after 400 ms). */
  hairAfterPrepare?: boolean;
  /** Stall every 3rd finish() this long before it records its time (see `stall`). */
  finishStallMs?: number;
  /** Until an audit is requested, hold each hair job's result until its own frame has been drawn (or 400 ms): a frame
   *  that does not wait then never draws its own mask, however late the face timers fire on a loaded machine. */
  holdUntilDrawn?: boolean;
  /** With hairAfterPrepare: release each job this long after its frame was posed, so a frame that waits for its own mask
   *  waits at least this long whatever the machine load. */
  hairAfterPoseMs?: number;
  /** Hold each face result until the camera has delivered a newer frame (or 400 ms): the next frame is then always
   *  captured before this one leaves inference, so its face request is posted while this frame is posed, whatever the
   *  machine load (a loaded machine can fire the 33 ms camera timer after this frame's draw). */
  faceAfterNextCapture?: boolean;}

async function run(options: Options): Promise<Run> {
  let callback: ((now: number, metadata: {presentedFrames: number; mediaTime: number}) => void) | null = null, presented = 0;
  const video = {srcObject: null, readyState: 4, videoWidth: 320, videoHeight: 180, currentTime: 0,
    requestVideoFrameCallback(cb: typeof callback) {callback = cb; return 1;}, cancelVideoFrameCallback() {callback = null;}};
  // Frames the pipeline actually captured (it numbers each one, nextSequence below); the face gate waits for the next.
  let captured = 0; const captureWaiters: (() => void)[] = [];
  const camera = setInterval(() => {
    turnTime = null;
    presented++; fill = presented & 255; const cb = callback; callback = null; cb?.(clock(), {presentedFrames: presented, mediaTime: presented / 30});
  }, 33);
  const result: Run = {rows: [], finishes: [], hairCalls: 0, errors: [], hairErrors: [], facelessAt: new Set(), jobs: null, recentJobs: null, describe: '', auditRequestedAt: null, auditedFinish: null,
    maskAfterPrepare: new Set(), delivered: [], facePostAt: new Map(), facePostOrder: new Map(), jobsQueriedAt: null, jobsQueriedOrder: null};
  let events = 0;
  let lastMask = false, preparedAt = 0, auditPending = false;
  // The frame each hair job belongs to (segment gets the sequence and the image digest; prepare gets the digest).
  const sequenceOf = new Map<string, number>(), posed = new Set<number>(), gates = new Map<number, () => void>();
  const drawn = new Set<number>(), drawGates = new Map<number, () => void>(), posedAt = new Map<number, number>();
  let lastPrepared: number | null = null;
  // Like LiveRenderer: a pending audit is taken by the first frame that draws its own mask.
  const auditTimer = options.auditAtMs === undefined ? null : setTimeout(() => {auditPending = true; result.auditRequestedAt = result.finishes.length;}, options.auditAtMs);
  const renderer = {setHairEnabled() {}, async prepare(_canvas: unknown, _detection: unknown, ids: {sourceSHA256: string}) {
    await sleep(2); preparedAt = clock();
    const sequence = sequenceOf.get(ids.sourceSHA256);
    lastPrepared = sequence ?? null;
    if (sequence !== undefined) {posed.add(sequence); posedAt.set(sequence, performance.now()); gates.get(sequence)?.();}
    return true;
  },
    get auditRequestPending() {return auditPending;},
    // The time from the renderer's pose to the draw: the pipeline's own wait for a mask, if any, sits in between.
    finish(mask: unknown, warp: MaskWarp | null = null, carried = false) {lastMask = mask !== null;
      if (options.finishStallMs && result.finishes.length % 3 === 2) stall(options.finishStallMs);
      result.finishes.push({mask: lastMask, warp, carried, waitMs: clock() - preparedAt, at: clock(), order: ++events});
      if (lastPrepared !== null) {drawn.add(lastPrepared); drawGates.get(lastPrepared)?.();}
      if (auditPending && lastMask && !carried) {auditPending = false; result.auditedFinish = result.finishes.length - 1;}},
    get stats() {return {render: {maskUploadMs: 0, endpointMs: 0, submitMs: 0}, audit: {ran: false, ms: 0}, gpuWaitPolls: 0, gpuWaitTimedOut: false, gpuWaitMs: 0, poseMs: 0, hasMask: lastMask, fallbackReason: null};},
    get poseSample() {return null;}};
  const detector = {delegate: 'CPU', lastTiming: null, async detect(bitmap: {close(): void}, timestampMs: number) {
    result.facePostAt.set(timestampMs, clock()); result.facePostOrder.set(timestampMs, ++events); const capturedAtPost = captured;
    await sleep(options.faceMs ?? 8); bitmap.close();
    if (options.faceAfterNextCapture && captured === capturedAtPost) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      await new Promise<void>(resolve => {captureWaiters.push(resolve); timer = setTimeout(resolve, 400);});
      clearTimeout(timer);
    }
    if (options.faceless?.(timestampMs)) {result.facelessAt.add(timestampMs); return {landmarks: [], matrix: null, inferenceMs: 1};}
    return {landmarks: landmarksAt(timestampMs, options.pxPerMs ?? 0), matrix: null, inferenceMs: 1};
  }};
  const hair = {async segment(bitmap: {close(): void}, sourceSHA256: string, sequence: number) {
    const call = ++result.hairCalls; bitmap.close(); sequenceOf.set(sourceSHA256, sequence);
    if (options.hangFromCall !== undefined && call >= options.hangFromCall) await new Promise(() => {});
    await sleep(options.hairMs);
    let gated = false;
    if (options.hairAfterPrepare && !posed.has(sequence)) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      await new Promise<void>(resolve => {gates.set(sequence, resolve); timer = setTimeout(resolve, 400);});
      clearTimeout(timer); gates.delete(sequence); gated = true;
    }
    // Never sooner than hairAfterPoseMs after the pose, also when the pose came first (a slow job on a loaded machine).
    // Released by the pose itself, always on a timer set now (before the pipeline's own wait deadline, which starts after
    // the pose): a GC pause between the pose and this line used to skip the sleep, so the job resumed in the pose's own
    // macrotask and the frame's wait read 0 ms on the shared clock. A job whose own timer fired after the pose resumes in
    // a later macrotask already and must not add a timer behind an already expired wait deadline.
    const posedMs = posedAt.get(sequence);
    if (options.hairAfterPoseMs && posedMs !== undefined && (gated || performance.now() - posedMs < options.hairAfterPoseMs)) {
      await sleep(Math.max(0, options.hairAfterPoseMs - (performance.now() - posedMs)));
    }
    if (options.holdUntilDrawn && result.auditRequestedAt === null && !drawn.has(sequence)) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      await new Promise<void>(resolve => {drawGates.set(sequence, resolve); timer = setTimeout(resolve, 400);});
      clearTimeout(timer); drawGates.delete(sequence);
    }
    if (posed.has(sequence)) result.maskAfterPrepare.add(sequence);
    if (call === options.rejectCall) throw new Error('The hair worker failed.');
    result.delivered.push({sequence, at: clock(), order: ++events});
    return {sequence, sourceSHA256, inferenceMs: options.hairMs, extractionMs: 1, width: 32, height: 18, hairIndex: 1, category: new Uint8Array(32 * 18),
      model: 'hair-only', modelSHA256: '0'.repeat(64), labels: ['background', 'hair'], delegate: 'CPU', categorySHA256: '1'.repeat(64)};
  }};
  let sequence = 0;
  const pipeline = runPipeline({id: 's', video, renderer, detector, hair: () => hair, hairModel: {id: 'hair-only'}, hairReady: () => true, hairEnabled: () => true,
    eyewearId: 'amber-horizon', owns: () => true, hairSchedule: options.schedule,
    nextSequence: () => {captured++; queueMicrotask(() => {for (const wake of captureWaiters.splice(0)) wake();}); return ++sequence;},
    onHairError: (error: unknown) => result.hairErrors.push(String(error)), onError: (error: unknown) => result.errors.push(String(error)),
    onPublished: (row: FrameInput) => result.rows.push(row), backend: () => ({active: 'CPU', renderer: null}), now: clock} as unknown as PipelineContext);
  await sleep(options.durationMs); result.jobsQueriedAt = clock(); result.jobsQueriedOrder = ++events; result.jobs = pipeline.hairJobs(60_000); result.recentJobs = pipeline.hairJobs(150); result.describe = pipeline.describe(); pipeline.stop(); clearInterval(camera); if (auditTimer) clearTimeout(auditTimer); await sleep(40);
  return result;
}
const interval = (frames: number, extra: Partial<HairSchedule> = {}): HairSchedule => ({...DEFAULT_HAIR_SCHEDULE, mode: 'interval', frames, ...extra});
const gaps = (rows: FrameInput[]) => rows.slice(1).map((row, i) => row.publishedAtMs - rows[i]!.publishedAtMs);
const percentile = (values: number[], p: number) => {const sorted = [...values].sort((a, b) => a - b); return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]!;};
const jobsOf = (r: Run) => r.rows.filter(row => row.native?.['hair.requested'] === true).length;

test('default: every frame waits for and draws its own mask', async () => {
  // Hair (40 ms) is slower than face + pose, and the harness holds each mask until 3 ms after its frame is posed, so each
  // frame must wait for its mask, on a timer (a wait the shared clock measures), however late the timers fire on a loaded
  // machine. (Released by the pose itself, the wait was microtasks only and read 0 ms on the per-macrotask clock.)
  const r = await run({hairMs: 40, durationMs: 1200, hairAfterPrepare: true, hairAfterPoseMs: 3});
  assert.deepEqual(r.errors, []); assert.ok(r.rows.length > 5, `${r.rows.length} frames`);
  assert.ok(r.rows.every(row => row.native?.['hair.schedule'] === 'every' && row.native?.['hair.requested'] === true));
  assert.ok(r.rows.slice(1).every(row => row.hasMask && row.hairInferenceMs !== null && row.native?.['hair.carried'] === false), 'every frame draws its own mask');
  assert.ok(r.jobs && r.jobs.jobs >= r.rows.length - 1 && r.jobs.inferenceMs?.median === 40, `${r.jobs?.jobs} logged jobs for ${r.rows.length} frames`);
  // Serial 40 ms jobs: at most 4 complete in the last 150 ms.
  // The 150 ms window holds exactly the jobs delivered in it (the harness's own record, on the shared clock): how many that
  // is depends on the machine's load (0 of 9 under two parallel suites), so only the bound and the windowing are asserted.
  // (Delivered before the query in event order: a job whose timer fires later in the same timers phase is not logged yet.)
  const inWindow = r.delivered.filter(entry => entry.order < r.jobsQueriedOrder! && entry.at >= r.jobsQueriedAt! - 150).length;
  assert.ok(r.recentJobs && r.recentJobs.jobs === inWindow && r.recentJobs.jobs <= r.jobs.jobs && r.recentJobs.jobs <= Math.ceil(150 / 40) + 1,
    `${r.recentJobs?.jobs} jobs in the last 150 ms of ${r.jobs.jobs}; the harness delivered ${inWindow} in that window`);
  assert.ok(r.rows.every(row => typeof row.native?.['overlap.hairAtSubmit'] === 'boolean'));
  assert.ok(r.finishes.slice(1).every(finish => finish.mask && finish.warp === null && !finish.carried));
  // Scheduling order, not wall-clock time (a 10 ms median-wait threshold failed once under a parallel Python child):
  // every drawn frame's own mask was delivered only after that frame was posed, so drawing it meant waiting for it.
  const early = r.rows.slice(1).filter(row => !r.maskAfterPrepare.has(row.sequence)).map(row => row.sequence);
  assert.deepEqual(early, [], `frames whose mask was ready before their pose (median wait ${percentile(r.finishes.map(f => f.waitMs), 0.5).toFixed(1)} ms)`);
  assert.ok(r.rows.slice(1).every(row => row.hairWaitMs > 0), 'the pipeline measured a wait on every frame');
});

/** Rows that drew NO mask although a fresh one was there: the newest mask (by frame, MaskStore) delivered in an earlier
 *  turn than the row's mask choice (the fake prepare's end, `at - waitMs` on the shared clock) came from a frame captured
 *  within maxAgeMs of the row's. Scheduling order and the injected clock only: under load masks may legitimately go
 *  stale (the camera timer fires late, captures drift apart), and then no mask is the right answer. A mask whose frame
 *  was never published has no known capture time, so it is not counted (that only makes the check more lenient). */
function missedFreshMasks(r: Run, maxAgeMs: number): string[] {
  const capturedAt = new Map(r.rows.map(row => [row.sequence, row.capturedAtMs]));
  const missed: string[] = [];
  r.rows.forEach((row, k) => {
    const finish = r.finishes[k];
    if (row.hasMask || !finish) return;
    const choseAt = finish.at - finish.waitMs;
    const newest = r.delivered.filter(entry => entry.at < choseAt && entry.sequence <= row.sequence)
      .reduce<{sequence: number; at: number} | null>((best, entry) => !best || entry.sequence > best.sequence ? entry : best, null);
    const maskCapturedAt = newest ? capturedAt.get(newest.sequence) : undefined;
    if (maskCapturedAt !== undefined && Math.abs(row.capturedAtMs - maskCapturedAt) <= maxAgeMs) {
      missed.push(`frame ${row.sequence} (captured ${row.capturedAtMs.toFixed(1)}) drew no mask; frame ${newest!.sequence}'s (captured ${maskCapturedAt.toFixed(1)}) was there`);
    }
  });
  return missed;
}

test('every 2nd frame: frames never wait, half the jobs, the rest draw a reused mask', async () => {
  const r = await run({schedule: interval(2), hairMs: 45, durationMs: 1500});
  assert.deepEqual(r.errors, []); assert.ok(r.rows.length > 10, `${r.rows.length} frames`);
  // The pipeline's own wait for hair (0 unless an audit holds the frame) and its admission wait: no frame waits for hair.
  assert.ok(r.rows.every(row => row.hairWaitMs === 0), `hair waits ${r.rows.map(row => row.hairWaitMs).join(',')}`);
  // With a schedule the admission is an already-resolved promise: a microtask, so exactly 0 on the shared clock.
  assert.ok(r.rows.every(row => row.hairAdmissionWaitMs === 0), `admission waits ${r.rows.map(row => row.hairAdmissionWaitMs).join(',')}`);
  const jobs = jobsOf(r), reused = r.rows.filter(row => row.native?.['hair.carried'] === true).length;
  assert.ok(jobs > 0 && jobs <= Math.ceil(r.rows.length / 2) + 1, `${jobs} jobs for ${r.rows.length} frames`);
  assert.ok(reused > 0, `${reused} of ${r.rows.length} frames reused a mask`);
  // A frame that did not start a job and draws a mask draws another frame's (reused) one.
  assert.ok(r.rows.every(row => row.native?.['hair.requested'] === true || !row.hasMask || row.native?.['hair.carried'] === true));
  // Once a mask exists, every frame draws one while it is fresh (by scheduling order, not by a frame count: under load the
  // first 45 ms job can outlast three frames, the check that failed in 2 of 3 parallel runs).
  assert.deepEqual(missedFreshMasks(r, DEFAULT_HAIR_SCHEDULE.maxAgeMs), [], 'after the first mask every frame has one while it is fresh');
  assert.ok(r.rows.some(row => row.hasMask), 'masks are drawn');
  assert.ok(r.rows.every(row => row.native?.['hair.carried'] !== true || (row.native?.['hair.ageMs'] as number) <= DEFAULT_HAIR_SCHEDULE.maxAgeMs));
  // Instruments: every row says what was in flight at its submit; the job log holds each completed job's own timing.
  assert.ok(r.rows.every(row => typeof row.native?.['overlap.faceAtSubmit'] === 'boolean' && typeof row.native?.['overlap.hairAtSubmit'] === 'boolean'));
  checkDrawGaps(r);
  // Frames never wait for hair, so a 45 ms job is in flight at some draw. (How often, and whether an 8 ms face request is
  // ever idle at a draw, depend on timer order on a loaded machine: not asserted.)
  const hairAtSubmit = r.rows.filter(row => row.native?.['overlap.hairAtSubmit'] === true).length;
  assert.ok(hairAtSubmit > 0, `a 45 ms hair job was in flight at ${hairAtSubmit} of ${r.rows.length} submits`);
  assert.ok(r.jobs && r.jobs.jobs >= jobs - 2 && r.jobs.jobs <= r.hairCalls, `${r.jobs?.jobs} logged jobs, ${jobs} requested, ${r.hairCalls} calls`);
  assert.equal(r.jobs.inferenceMs?.median, 45); assert.ok(r.jobs.roundTripMs!.median >= 45);
  assert.match(r.describe, /dropped \d+ \(replaced after capture \d+, locked \d+, misses \d+\)/);
});

/** Each row's face post sits between two draw starts: the previous draw → post and post → next draw add up to one gap
 *  between consecutive draws, which the fake renderer saw as consecutive finish() calls. Both read the injected clock,
 *  so the sum equals that gap up to float rounding: an instrument that took its draw start anywhere else is caught. */
function checkDrawGaps(r: Run): void {
  const draws = r.finishes.map(finish => finish.at), gaps = draws.slice(1).map((at, k) => at - draws[k]!);
  const rows = r.rows.slice(1).filter(row => row.native?.['overlap.faceAfterDrawMs'] !== null);
  assert.ok(rows.length >= r.rows.length / 2, `${rows.length} rows with both gaps of ${r.rows.length}`);
  for (const row of rows) {
    const before = row.native?.['overlap.faceBeforeDrawMs'] as number, after = row.native?.['overlap.faceAfterDrawMs'] as number;
    assert.ok(before >= 0 && after >= 0, `gaps ${after} / ${before}`);
    assert.ok(gaps.some(gap => Math.abs(gap - (before + after)) < 1e-9), `${after.toFixed(2)} + ${before.toFixed(2)} ms is no gap between consecutive draws`);
  }
  assert.equal(r.rows[0]!.native?.['overlap.faceAfterDrawMs'], null);
  assert.ok(r.rows.every(row => typeof row.native?.['overlap.faceBeforeDrawMs'] === 'number'));
}

test('a face request prefetched while the previous frame is posed is posted just before that frame\'s draw', async () => {
  // 40 ms face requests at a 33 ms camera: the next request starts when this frame enters prepare, before its draw. The
  // harness holds each face result until a newer frame is captured, so that frame is always waiting when this one leaves
  // inference, and the claim is checked by ORDER on the shared clock (a median of post->draw against draw->post
  // failed under two parallel suites: 49.6 vs 32.9 ms, the camera timer had fired after the draw).
  const r = await run({schedule: interval(2), hairMs: 20, faceMs: 40, durationMs: 1500, faceAfterNextCapture: true});
  // The claim is per frame, so a handful suffices: two parallel suites cut the throughput to 10 frames in 1.5 s.
  assert.deepEqual(r.errors, []); assert.ok(r.rows.length >= 5, `${r.rows.length} frames`);
  checkDrawGaps(r);
  const late: string[] = [];
  for (let k = 1; k < r.rows.length; k++) {
    const post = r.facePostAt.get(r.rows[k]!.capturedAtMs), postOrder = r.facePostOrder.get(r.rows[k]!.capturedAtMs);
    const previous = r.finishes[k - 1]!;
    assert.ok(post !== undefined && postOrder !== undefined, `frame ${r.rows[k]!.sequence} has a face post`);
    // Posted before the previous frame's draw (event order), and that draw is the first one after the post (the row's own
    // instruments, on the shared clock).
    if (!(postOrder < previous.order) || Math.abs(post + (r.rows[k]!.native?.['overlap.faceBeforeDrawMs'] as number) - previous.at) > 1e-9) {
      late.push(`frame ${r.rows[k]!.sequence}: posted ${post.toFixed(2)} (event ${postOrder}), previous frame drawn ${previous.at.toFixed(2)} (event ${previous.order})`);
    }
  }
  assert.deepEqual(late, [], 'every face request is posted while the previous frame is posed, before its draw');
  // So at every draw but the last, the next frame's face request is in flight.
  const faceAtSubmit = r.rows.filter(row => row.native?.['overlap.faceAtSubmit'] === true).length;
  assert.ok(faceAtSubmit >= r.rows.length - 1, `face request in flight at ${faceAtSubmit} of ${r.rows.length} draw starts`);
});

test('the default schedule: the draw gaps around each face post are consistent too, even across a stalled draw', async () => {
  // Every 3rd finish() busy-waits 3 ms before it records its time, as a descheduled process would: the instruments and the
  // renderer read one clock, so the check stays exact (with two wall-clock reads this fails every run).
  const r = await run({hairMs: 40, faceMs: 5, durationMs: 1500, finishStallMs: 3});
  assert.deepEqual(r.errors, []); assert.ok(r.rows.length > 10, `${r.rows.length} frames`);
  checkDrawGaps(r);
});

test('with a schedule an audit gets a frame that waited for its own mask, the first frame that finds the worker idle', async () => {
  // 45 ms hair against 8 ms face: without the audit path no frame would draw its own mask. A schedule that asks for
  // no mask after the first (a still head), so only the audit itself can start the job it needs.
  // Scheduling order, not timer races: the first frame's job is held until that frame is drawn (it does not wait for it;
  // under load its 8 ms face request outlasted the 45 ms hair job and it drew its own mask before any audit, 2 of 3
  // overloaded suite runs), and every job is released only 12 ms after its frame's pose, so the audited frame waits.
  const r = await run({schedule: interval(100_000), hairMs: 45, durationMs: 1800, auditAtMs: 700, holdUntilDrawn: true, hairAfterPrepare: true,
    hairAfterPoseMs: 12});
  assert.deepEqual(r.errors, []); assert.ok(r.auditRequestedAt !== null, 'the audit was requested');
  const before = r.finishes.slice(0, r.auditRequestedAt!);
  const own = (finish: Finish) => finish.mask && !finish.carried;
  assert.ok(before.length > 5, `${before.length} frames before the audit`);
  assert.ok(!before.some(own), 'before the audit no frame draws its own mask');
  assert.ok(r.auditedFinish !== null && r.auditedFinish - r.auditRequestedAt! <= 4, `audited at frame ${r.auditedFinish}, requested at ${r.auditRequestedAt}`);
  const audited = r.rows[r.auditedFinish!]!;
  assert.ok(audited.hasMask && audited.native?.['hair.carried'] === false && audited.native?.['hair.requested'] === true && audited.hairWaitMs > 5,
    `the audited frame waited ${audited.hairWaitMs} ms for its own mask`);
  const after = r.finishes.slice(r.auditedFinish! + 1);
  assert.ok(after.length > 5 && !after.some(own), 'after the audit no frame waits for or draws its own mask again');
  assert.ok(r.rows.filter((_row, k) => k !== r.auditedFinish).every(row => row.hairWaitMs === 0), 'no other frame waits');
});

test('a failed hair job leaves no hair job counted in flight', async () => {
  const r = await run({schedule: interval(2), hairMs: 5, rejectCall: 2, durationMs: 1200});
  assert.deepEqual(r.errors, []); assert.equal(r.hairErrors.length, 1); assert.ok(r.hairCalls > 4, `${r.hairCalls} hair calls`);
  const late = r.rows.slice(-10);
  assert.ok(late.length === 10 && late.some(row => row.native?.['overlap.hairAtSubmit'] === false),
    `hairAtSubmit after the failure: ${late.map(row => row.native?.['overlap.hairAtSubmit']).join(',')}`);
});

test('every 2nd frame with fast hair: a frame that started a job draws its own mask when it is ready in time', async () => {
  const r = await run({schedule: interval(2), hairMs: 1, faceMs: 20, durationMs: 1500});
  assert.deepEqual(r.errors, []);
  const requesting = r.rows.filter(row => row.native?.['hair.requested'] === true && row.hasMask);
  const own = requesting.filter(row => row.native?.['hair.carried'] === false && row.hairInferenceMs !== null);
  assert.ok(requesting.length > 3 && own.length >= requesting.length * 0.7, `${own.length} of ${requesting.length} requesting frames drew their own mask`);
  assert.ok(r.rows.filter(row => row.native?.['hair.requested'] === false && row.hasMask).every(row => row.native?.['hair.carried'] === true && row.hairInferenceMs === null));
});

test('a hair worker that stops answering does not freeze the frames; stale masks are dropped', async () => {
  const r = await run({schedule: interval(2, {maxAgeMs: 150}), hairMs: 20, hangFromCall: 3, durationMs: 1500});
  assert.deepEqual(r.errors, []);
  const late = r.rows.filter(row => row.capturedAtMs > r.rows[0]!.capturedAtMs + 500);
  assert.ok(late.length > 5, `${late.length} frames published after the hang`);
  assert.ok(Math.max(...gaps(r.rows)) < 400, `largest gap ${Math.max(...gaps(r.rows))} ms`);
  assert.equal(r.hairCalls, 3, 'a busy worker is never given another job');
  assert.ok(late.slice(-5).every(row => !row.hasMask), 'masks older than 150 ms are not drawn');
});

test('the default pipeline, for contrast, stalls behind the hung hair job', async () => {
  const r = await run({hairMs: 20, hangFromCall: 3, durationMs: 900});
  assert.ok(Math.max(...gaps(r.rows)) > 500 || r.rows.length < 8, 'every frame waits behind the serial hair job');
});

test('a reused mask carries the head motion since its frame', async () => {
  const pxPerMs = 0.06, r = await run({schedule: interval(3, {movePx: 0}), hairMs: 30, pxPerMs, durationMs: 1500});
  assert.deepEqual(r.errors, []);
  const reused = r.rows.map((row, i) => ({row, finish: r.finishes[i]!})).filter(({row}) => row.native?.['hair.carried'] === true);
  assert.ok(reused.length > 3, `${reused.length} reused frames`);
  for (const {row, finish} of reused) {
    const ageMs = row.native?.['hair.ageMs'] as number, frames = row.native?.['hair.ageFrames'] as number;
    // The mask's frame was captured ageMs earlier (later when frames < 0): moving back to it undoes that much drift.
    const drift = Math.sign(frames) * ageMs * pxPerMs;
    assert.ok(finish.warp !== null && Math.abs(finish.warp.toMask.tx + drift) < 1e-6 && Math.abs(finish.warp.toMask.a - 1) < 1e-9,
      `age ${ageMs} ms / ${frames} frames, warp tx ${finish.warp?.toMask.tx}, expected ${-drift}`);
    assert.ok(Math.abs((row.native?.['hair.motionPx'] as number) - ageMs * pxPerMs) < 1e-6);
  }
});

test('a fast head starts hair jobs early (?hairmove=); ?hairmove=0 keeps the interval', async () => {
  const fast = {hairMs: 5, pxPerMs: 0.5, durationMs: 1500};
  const early = await run({schedule: interval(4, {movePx: 2}), ...fast}), fixed = await run({schedule: interval(4, {movePx: 0}), ...fast});
  assert.deepEqual([...early.errors, ...fixed.errors], []);
  const share = (r: Run) => jobsOf(r) / r.rows.length;
  assert.ok(share(fixed) <= 0.3, `fixed interval: ${jobsOf(fixed)} jobs for ${fixed.rows.length} frames`);
  assert.ok(share(early) > share(fixed) * 1.5, `early starts: ${jobsOf(early)}/${early.rows.length} vs ${jobsOf(fixed)}/${fixed.rows.length}`);
});

test('a lost face and a failed hair job: no mask without a face, the error is reported after its frame is drawn, the session goes on', async () => {
  const r = await run({schedule: interval(2), hairMs: 60, rejectCall: 2, durationMs: 1800, faceless: t => t % 500 < 180});
  assert.deepEqual(r.errors, []);
  assert.ok(r.hairErrors.length === 1 && /failed/.test(r.hairErrors[0]!), `hair errors ${JSON.stringify(r.hairErrors)}`);
  const faceless = r.rows.filter(row => r.facelessAt.has(row.capturedAtMs)), withFace = r.rows.filter(row => !r.facelessAt.has(row.capturedAtMs));
  assert.ok(faceless.length > 2 && faceless.every(row => !row.hasMask), 'a frame without landmarks draws no mask');
  assert.ok(withFace.slice(-5).some(row => row.hasMask), 'masks come back with the face');
});
