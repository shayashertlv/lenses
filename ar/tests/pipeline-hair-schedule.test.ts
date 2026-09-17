/** The real runPipeline under a fake camera, fake workers and a fake renderer: what the hair schedule does to the frames.
 *  Real timers: thresholds are relative to the frames actually published wherever possible. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runPipeline} from '../src/pipeline/pipeline.ts';
import {DEFAULT_HAIR_SCHEDULE} from '../src/hair/mask-reuse.ts';
import type {HairSchedule, MaskWarp} from '../src/hair/mask-reuse.ts';
import type {FrameInput} from '../src/pipeline/profiler.ts';
import type {PipelineContext} from '../src/pipeline/pipeline.ts';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
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

interface Finish {mask: boolean; warp: MaskWarp | null; carried: boolean; waitMs: number;}
interface Run {rows: FrameInput[]; finishes: Finish[]; hairCalls: number; errors: string[]; hairErrors: string[]; facelessAt: Set<number>;}
interface Options {schedule?: HairSchedule; hairMs: number; faceMs?: number; hangFromCall?: number; rejectCall?: number; durationMs: number; pxPerMs?: number; faceless?: (timeMs: number) => boolean;}

async function run(options: Options): Promise<Run> {
  let callback: ((now: number, metadata: {presentedFrames: number; mediaTime: number}) => void) | null = null, presented = 0;
  const video = {srcObject: null, readyState: 4, videoWidth: 320, videoHeight: 180, currentTime: 0,
    requestVideoFrameCallback(cb: typeof callback) {callback = cb; return 1;}, cancelVideoFrameCallback() {callback = null;}};
  const camera = setInterval(() => {presented++; fill = presented & 255; const cb = callback; callback = null; cb?.(performance.now(), {presentedFrames: presented, mediaTime: presented / 30});}, 33);
  const result: Run = {rows: [], finishes: [], hairCalls: 0, errors: [], hairErrors: [], facelessAt: new Set()};
  let lastMask = false, preparedAt = 0;
  const renderer = {setHairEnabled() {}, async prepare() {await sleep(2); preparedAt = performance.now(); return true;},
    // The time from the renderer's pose to the draw: the pipeline's own wait for a mask, if any, sits in between.
    finish(mask: unknown, warp: MaskWarp | null = null, carried = false) {lastMask = mask !== null; result.finishes.push({mask: lastMask, warp, carried, waitMs: performance.now() - preparedAt});},
    get stats() {return {render: {maskUploadMs: 0, continuityMs: 0, submitMs: 0}, audit: {ran: false, ms: 0}, gpuWaitPolls: 0, gpuWaitTimedOut: false, gpuWaitMs: 0, poseMs: 0, hasMask: lastMask, fallbackReason: null};},
    get poseSample() {return null;}};
  const detector = {delegate: 'CPU', lastTiming: null, async detect(bitmap: {close(): void}, timestampMs: number) {
    await sleep(options.faceMs ?? 8); bitmap.close();
    if (options.faceless?.(timestampMs)) {result.facelessAt.add(timestampMs); return {landmarks: [], matrix: null, inferenceMs: 1};}
    return {landmarks: landmarksAt(timestampMs, options.pxPerMs ?? 0), matrix: null, inferenceMs: 1};
  }};
  const hair = {async segment(bitmap: {close(): void}, sourceSHA256: string, sequence: number) {
    const call = ++result.hairCalls; bitmap.close();
    if (options.hangFromCall !== undefined && call >= options.hangFromCall) await new Promise(() => {});
    await sleep(options.hairMs);
    if (call === options.rejectCall) throw new Error('The hair worker failed.');
    return {sequence, sourceSHA256, inferenceMs: options.hairMs, extractionMs: 1, width: 32, height: 18, hairIndex: 1, category: new Uint8Array(32 * 18),
      model: 'hair-only', modelSHA256: '0'.repeat(64), labels: ['background', 'hair'], delegate: 'CPU', categorySHA256: '1'.repeat(64)};
  }};
  let sequence = 0;
  const pipeline = runPipeline({id: 's', video, renderer, detector, hair: () => hair, hairModel: {id: 'hair-only'}, hairReady: () => true, hairEnabled: () => true,
    eyewearId: 'amber-horizon', owns: () => true, nextSequence: () => ++sequence, hairSchedule: options.schedule,
    onHairError: (error: unknown) => result.hairErrors.push(String(error)), onError: (error: unknown) => result.errors.push(String(error)),
    onPublished: (row: FrameInput) => result.rows.push(row), backend: () => ({active: 'CPU', renderer: null})} as unknown as PipelineContext);
  await sleep(options.durationMs); pipeline.stop(); clearInterval(camera); await sleep(40);
  return result;
}
const interval = (frames: number, extra: Partial<HairSchedule> = {}): HairSchedule => ({...DEFAULT_HAIR_SCHEDULE, mode: 'interval', frames, ...extra});
const gaps = (rows: FrameInput[]) => rows.slice(1).map((row, i) => row.publishedAtMs - rows[i]!.publishedAtMs);
const percentile = (values: number[], p: number) => {const sorted = [...values].sort((a, b) => a - b); return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]!;};
const jobsOf = (r: Run) => r.rows.filter(row => row.native?.['hair.requested'] === true).length;

test('default: every frame waits for and draws its own mask', async () => {
  // Hair (40 ms) is slower than face + pose, so each frame must wait for its mask.
  const r = await run({hairMs: 40, durationMs: 1200});
  assert.deepEqual(r.errors, []); assert.ok(r.rows.length > 5, `${r.rows.length} frames`);
  assert.ok(r.rows.every(row => row.native?.['hair.schedule'] === 'every' && row.native?.['hair.requested'] === true));
  assert.ok(r.rows.slice(1).every(row => row.hasMask && row.hairInferenceMs !== null && row.native?.['hair.carried'] === false), 'every frame draws its own mask');
  assert.ok(r.finishes.slice(1).every(finish => finish.mask && finish.warp === null && !finish.carried));
  assert.ok(percentile(r.finishes.slice(1).map(f => f.waitMs), 0.5) > 10, `median wait ${percentile(r.finishes.map(f => f.waitMs), 0.5)} ms: frames wait for their masks`);
});

test('every 2nd frame: frames never wait, half the jobs, the rest draw a reused mask', async () => {
  const r = await run({schedule: interval(2), hairMs: 45, durationMs: 1500});
  assert.deepEqual(r.errors, []); assert.ok(r.rows.length > 10, `${r.rows.length} frames`);
  assert.ok(percentile(r.finishes.map(f => f.waitMs), 0.95) < 10, `p95 wait ${percentile(r.finishes.map(f => f.waitMs), 0.95)} ms`);
  assert.ok(r.rows.every(row => row.hairAdmissionWaitMs < 5), 'no frame waits for the hair worker to take a job');
  const jobs = jobsOf(r), reused = r.rows.filter(row => row.native?.['hair.carried'] === true).length;
  assert.ok(jobs > 0 && jobs <= Math.ceil(r.rows.length / 2) + 1, `${jobs} jobs for ${r.rows.length} frames`);
  assert.ok(reused >= r.rows.length / 3, `${reused} of ${r.rows.length} frames reused a mask`);
  assert.ok(r.rows.slice(3).every(row => row.hasMask), 'after the first mask every frame has one');
  assert.ok(r.rows.every(row => row.native?.['hair.carried'] !== true || (row.native?.['hair.ageMs'] as number) <= DEFAULT_HAIR_SCHEDULE.maxAgeMs));
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
