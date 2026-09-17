import {test} from 'node:test';
import assert from 'node:assert/strict';
import {applyAffine, chooseMask, DEFAULT_HAIR_SCHEDULE, estimateSimilarity, HairScheduler, headMotionPx, invertAffine, maskUvMatrix, MaskStore, STABLE_LANDMARKS} from '../src/hair/mask-reuse.ts';
import type {Affine, HairSchedule} from '../src/hair/mask-reuse.ts';
import {continuityCut} from '../src/render/continuity.ts';
import type {ProjectedTemplePath, TempleContinuityModel} from '../src/render/continuity.ts';
import {describeConfig, parseConfig} from '../src/config.ts';
import {describeHairReport, describeOverlapReport, hairReport, overlapReport} from '../src/pipeline/hair-report.ts';
import type {FrameSample} from '../src/pipeline/profiler.ts';
import type {Landmark} from '../src/face/protocol.ts';

const W = 1280, H = 720;
/** 478 landmarks spread over the face area, normalised. */
const base: Landmark[] = Array.from({length: 478}, (_, i) => ({x: 0.3 + 0.4 * ((i * 37) % 100) / 100, y: 0.2 + 0.6 * ((i * 53) % 100) / 100, z: 0}));
const moved = (m: Affine, landmarks = base): Landmark[] => landmarks.map(p => {const [x, y] = applyAffine(m, p.x * W, p.y * H); return {x: x / W, y: y / H, z: p.z};});
const similarity = (scale: number, degrees: number, tx: number, ty: number): Affine => {
  const c = scale * Math.cos(degrees * Math.PI / 180), s = scale * Math.sin(degrees * Math.PI / 180);
  return {a: c, b: -s, c: s, d: c, tx, ty};
};
const close = (a: number, b: number, tolerance = 1e-6) => Math.abs(a - b) <= tolerance;
const interval = (frames: number, extra: Partial<HairSchedule> = {}): HairSchedule => ({...DEFAULT_HAIR_SCHEDULE, mode: 'interval', frames, ...extra});

test('the head-motion fit recovers a shift, turn and scale exactly, and its inverse undoes it', () => {
  const truth = similarity(1.04, 6, 23.5, -11.25), fit = estimateSimilarity(base, moved(truth), W, H)!;
  for (const key of ['a', 'b', 'c', 'd', 'tx', 'ty'] as const) assert.ok(close(fit[key], truth[key], 1e-7), `${key}: ${fit[key]} vs ${truth[key]}`);
  const inverse = invertAffine(fit)!, [x, y] = applyAffine(inverse, ...applyAffine(fit, 400, 300));
  assert.ok(close(x, 400, 1e-7) && close(y, 300, 1e-7));
  // Points outside the stable set (eyes, mouth) do not steer the fit.
  const expression = moved(truth).map((p, i) => STABLE_LANDMARKS.includes(i) ? p : {x: p.x + 0.05, y: p.y - 0.03, z: 0});
  assert.ok(close(estimateSimilarity(base, expression, W, H)!.tx, truth.tx, 1e-7));
});

test('an incomplete, collapsed or implausible fit is refused', () => {
  assert.equal(estimateSimilarity(base.slice(0, 100), base, W, H), null, 'missing stable landmarks');
  const collapsed = base.map(() => ({x: 0.5, y: 0.5, z: 0}));
  assert.equal(estimateSimilarity(collapsed, base, W, H), null);
  assert.equal(estimateSimilarity(base, moved(similarity(3, 0, 0, 0)), W, H), null, 'a 3× scale between two frames is not a head motion');
  assert.equal(estimateSimilarity(base, base.map((p, i) => i === 10 ? {x: NaN, y: p.y, z: 0} : p), W, H), null);
  assert.equal(invertAffine({a: 1, b: 2, c: 2, d: 4, tx: 0, ty: 0}), null);
});

test('head motion is the RMS landmark displacement in pixels', () => {
  assert.ok(close(headMotionPx(base, moved(similarity(1, 0, 3, 4)), W, H)!, 5));
  assert.equal(headMotionPx(base, base, W, H), 0);
  assert.equal(headMotionPx(base.slice(0, 5), base, W, H), null);
});

test('the texture-space matrix is exactly the identity for no motion, and moves texture coordinates like the pixel warp', () => {
  assert.deepEqual(maskUvMatrix({toMask: {a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0}, width: W, height: H}), [1, 0, 0, 0, 1, 0, 0, 0, 1]);
  const toMask = similarity(0.97, -4, -30, 12), m = maskUvMatrix({toMask, width: W, height: H});
  const u = 0.61, v = 0.37, [px, py] = applyAffine(toMask, u * W, v * H);
  assert.ok(close(m[0]! * u + m[1]! * v + m[2]!, px / W, 1e-12) && close(m[3]! * u + m[4]! * v + m[5]!, py / H, 1e-12));
});

test('the scheduler never queues, keeps the interval, and starts early when the head moves', () => {
  const s = new HairScheduler(interval(2));
  assert.equal(s.shouldRequest(1, false, false, null), false, 'a busy worker is never given a second job');
  assert.equal(s.shouldRequest(1, true, false, null), true, 'no mask yet'); s.noteRequested(1);
  assert.equal(s.shouldRequest(2, true, true, 0), false, 'one frame after a job');
  assert.equal(s.shouldRequest(3, true, true, 0), true, 'the interval'); s.noteRequested(3);
  assert.equal(s.shouldRequest(4, true, true, 8.1), true, 'faster than 8 px starts early');
  assert.equal(s.shouldRequest(4, true, true, 8), false);
  const never = new HairScheduler(interval(3, {movePx: 0})); never.noteRequested(10);
  assert.equal(never.shouldRequest(11, true, true, 500), false, '?hairmove=0 never starts early');
});

test('edges: a scheduler without a mask always asks; refusals at 0.4× scale, by height, by future age; a refused fit draws no hair', () => {
  const s = new HairScheduler(interval(4)); s.noteRequested(1);
  assert.equal(s.shouldRequest(2, true, false, 0), true, 'no mask yet, even right after a job');
  assert.equal(estimateSimilarity(base, moved(similarity(0.4, 0, 0, 0)), W, H), null);
  const stored = {mask: 'old', landmarks: base, sequence: 10, capturedAtMs: 330, width: W, height: H};
  const frame = (extra: object) => ({sequence: 12, capturedAtMs: 396, width: W, height: H, landmarks: base, ...extra});
  assert.equal(chooseMask(null, frame({height: 1280}), stored, interval(2)), null, 'height alone differs');
  assert.equal(chooseMask(null, frame({sequence: 2, capturedAtMs: 0}), stored, interval(2)), null, 'a mask 330 ms in the future is too old too');
  const otherFace = moved(similarity(3, 0, 0, 0));
  assert.equal(chooseMask(null, frame({landmarks: otherFace}), stored, interval(2)), null, 'the fit is refused: no hair rather than a misplaced mask');
});

test('the hair shader reads the mask through the warp exactly as the pixel warp maps it, and exactly as before without one', async () => {
  const {Matrix3, Mesh, MeshStandardMaterial, BoxGeometry, Vector3} = await import('three');
  const {createHairOcclusion} = await import('../src/render/hair-occlusion.ts');
  const material = new MeshStandardMaterial(), mesh = new Mesh(new BoxGeometry(), material), hair = createHairOcclusion(mesh);
  const shader = {uniforms: {} as Record<string, {value: unknown}>, vertexShader: '#include <begin_vertex>', fragmentShader: '#include <dithering_fragment>'};
  material.onBeforeCompile(shader as never, {} as never);
  assert.match(shader.fragmentShader, /vec2 hairLookupUV = hairOcclusionMaskWarp > 0\.5 \? hairWarpedUV : hairMaskUV;/);
  assert.match(shader.fragmentShader, /texture2D\(hairOcclusionMask, hairLookupUV\)\.r \* hairInside/);
  const warpUniform = shader.uniforms.hairOcclusionMaskWarp!, uvUniform = shader.uniforms.hairOcclusionMaskUv! as {value: InstanceType<typeof Matrix3>};
  assert.equal(warpUniform.value, 0, 'off by default: the own-mask lookup');
  const toMask = similarity(0.98, 3, -25, 14);
  hair.setMaskUv(maskUvMatrix({toMask, width: W, height: H}));
  assert.equal(warpUniform.value, 1);
  // GLSL `mat3 * vec3` is three's Vector3.applyMatrix3 on the same column-major elements.
  const u = 0.42, v = 0.58, lookup = new Vector3(u, v, 1).applyMatrix3(uvUniform.value), [px, py] = applyAffine(toMask, u * W, v * H);
  assert.ok(close(lookup.x, px / W, 1e-12) && close(lookup.y, py / H, 1e-12), `${lookup.x},${lookup.y} vs ${px / W},${py / H}`);
  hair.setMaskUv(null); assert.equal(warpUniform.value, 0); assert.deepEqual(uvUniform.value.elements, new Matrix3().elements);
  hair.dispose();
});

test('the store keeps the newest frame\'s mask', () => {
  const store = new MaskStore<string>(), entry = (sequence: number) => ({mask: `m${sequence}`, landmarks: base, sequence, capturedAtMs: sequence * 33, width: W, height: H});
  store.offer(entry(4)); store.offer(entry(2)); assert.equal(store.newest?.mask, 'm4');
  store.offer(entry(6)); assert.equal(store.newest?.mask, 'm6'); store.clear(); assert.equal(store.newest, null);
});

test('a frame draws its own mask when ready, else the newest earlier mask moved back onto it, within age and size', () => {
  const frame = (sequence: number, landmarks = base, extra = {}) => ({sequence, capturedAtMs: sequence * 33, width: W, height: H, landmarks, ...extra});
  const stored = {mask: 'old', landmarks: base, sequence: 10, capturedAtMs: 330, width: W, height: H};
  assert.deepEqual(chooseMask('own', frame(12), stored, interval(2)), {mask: 'own', carried: false, warp: null, ageMs: 0, ageFrames: 0, motionPx: 0});
  const motion = similarity(1.01, 2, 9, -4), now = moved(motion);
  const reused = chooseMask(null, frame(12, now), stored, interval(2))!;
  assert.equal(reused.carried, true); assert.equal(reused.ageFrames, 2); assert.equal(reused.ageMs, 66);
  // The warp carries a pixel of the drawn frame to where the same skull point was in the mask's frame.
  const [x, y] = applyAffine(reused.warp!.toMask, now[234]!.x * W, now[234]!.y * H);
  assert.ok(close(x, base[234]!.x * W, 1e-6) && close(y, base[234]!.y * H, 1e-6));
  assert.equal(chooseMask(null, frame(20), stored, interval(2, {maxAgeMs: 200})), null, '330 ms is too old');
  assert.equal(chooseMask(null, frame(12, now, {width: 720}), stored, interval(2)), null, 'a mask of another frame size');
  assert.equal(chooseMask(null, frame(12, []), stored, interval(2)), null, 'no face in the drawn frame');
  assert.equal(chooseMask(null, frame(12), null, interval(2)), null);
});

test('the continuity cut reads a moved mask where the arm was in the mask\'s own frame', () => {
  const stations = Array.from({length: 33}, (_, i) => ({zM: -0.03 - i * 0.0025, centerXM: -0.07, centerYM: 0, minXM: -0.071, maxXM: -0.069, minYM: -0.001, maxYM: 0.001}));
  const model: TempleContinuityModel = {startZM: -0.03, cutoffZM: -0.11, sides: [stations, stations.map(s => ({...s, centerXM: 0.07}))]};
  const render = {width: 1280, height: 720};
  const points = stations.map((_, i) => ({x: 300 + i * 8, y: 400, radiusPx: 3, progressPx: i * 8}));
  const paths: ProjectedTemplePath[] = [{side: 0, points, lengthPx: 256}, {side: 1, points: points.map(p => ({...p, y: 100})), lengthPx: 256}];
  const paint = (dx: number, dy: number) => {
    const mask = {width: 640, height: 360, hairIndex: 1, category: new Uint8Array(640 * 360)};
    for (let y = 190 + dy; y <= 210 + dy; y++) for (let x = 250 + dx; x <= 300 + dx; x++) mask.category[y * 640 + x] = 1;
    return mask;
  };
  const still = continuityCut(model, paths, paint(0, 0), render, 10);
  assert.ok(still.negative !== null && still.positive === null, 'hair over the arm cuts it');
  assert.deepEqual(continuityCut(model, paths, paint(0, 0), render, 10, null), still, 'no warp is the frame\'s own mask');
  // The head (and its hair) moved 40 px right and 16 px down since the mask was made: in mask pixels 20 and 8.
  const shifted = paint(-20, -8), warp = {toMask: {a: 1, b: 0, c: 0, d: 1, tx: -40, ty: -16}, width: 1280, height: 720};
  assert.deepEqual(continuityCut(model, paths, shifted, render, 10, warp), still, 'moved back, the old mask cuts the same station');
  assert.notDeepEqual(continuityCut(model, paths, shifted, render, 10), still, 'unmoved, it does not');
});

test('?hairframes=2, ?hairmove= and ?hairmaxage= parse; the default is every frame; the rejected variants are gone', () => {
  assert.deepEqual(parseConfig('').hairSchedule, DEFAULT_HAIR_SCHEDULE); assert.equal(DEFAULT_HAIR_SCHEDULE.mode, 'every');
  assert.deepEqual(parseConfig('?hairframes=2').hairSchedule, {...DEFAULT_HAIR_SCHEDULE, mode: 'interval', frames: 2});
  assert.deepEqual(parseConfig('?hairframes=2&hairmove=0&hairmaxage=120').hairSchedule, {mode: 'interval', frames: 2, movePx: 0, maxAgeMs: 120});
  assert.deepEqual(parseConfig('?hairframes=2&hairwarp=0').hairSchedule, {...DEFAULT_HAIR_SCHEDULE, mode: 'interval', frames: 2}, 'no held-mask variant');
  assert.equal(parseConfig('?hairframes=2&hairmove=500').hairSchedule.movePx, 8, 'out of range keeps the default');
  for (const bad of ['1', '3', '4', 'auto', '5', 'x', '', '24']) assert.deepEqual(parseConfig(`?hairframes=${bad}`).hairSchedule, DEFAULT_HAIR_SCHEDULE, `?hairframes=${bad}`);
  assert.equal(parseConfig('?hairframes=2&hairmaxage=5').hairSchedule.maxAgeMs, 200, 'out of range keeps the default');
  assert.doesNotMatch(describeConfig(parseConfig('')), /frames never wait/);
  assert.match(describeConfig(parseConfig('?hairframes=2')), /hair every 2 frames \(\?hairframes=2\), sooner when the head moves > 8 px \(\?hairmove=\); frames never wait, others reuse the newest mask moved with the head up to 200 ms old/);
});

test('the live hair line counts own, reused and missing masks from the rows', () => {
  const row = (hasMask: boolean, carried: boolean | null, extra: Record<string, number | boolean | null> = {}): FrameSample => ({hasFace: true, hair: true, hasMask,
    native: {'hair.carried': carried, 'hair.requested': carried === false, 'hair.ageMs': carried ? 40 : 0, 'hair.motionPx': carried ? 2 : 0, ...extra}}) as unknown as FrameSample;
  const rows = [row(true, false), row(true, true), row(true, false), row(true, true), row(false, null)];
  const report = hairReport(rows);
  assert.deepEqual({frames: report.frames, own: report.own, reused: report.reused, none: report.none, jobs: report.jobs}, {frames: 5, own: 2, reused: 2, none: 1, jobs: 2});
  assert.equal(report.reuseAgeMs?.median, 40);
  assert.match(describeHairReport(report, '?hairframes=2'), /5 tracked frames · own 40% · reused 40% · none 20% · 2 hair jobs · reused masks moved with the head, age median 40 \/ p95 40 ms, head motion median 2.0/);
  assert.match(describeHairReport(hairReport([]), 'x'), /no tracked frames/);
});

test('the overlap line: in flight at draw start, the draw starts around each face post, and face time near a draw against apart', () => {
  const row = (face: boolean | undefined, hair: boolean, before: number | null, after: number | null, inference: number | null): FrameSample => ({hasFace: true, hair: true,
    hasMask: true, faceInferenceMs: inference,
    native: face === undefined ? {} : {'overlap.faceAtSubmit': face, 'overlap.hairAtSubmit': hair, 'overlap.faceBeforeDrawMs': before, 'overlap.faceAfterDrawMs': after}}) as unknown as FrameSample;
  const report = overlapReport([row(true, true, 2, null, 30), row(true, false, 10, 31, 34), row(false, false, 25, 8, 12), row(true, true, 10.5, 20, null),
    row(undefined, true, 1, 1, 99), row(false, false, null, 33, 11)]);
  assert.deepEqual({frames: report.frames, face: report.faceAtDrawStart, hair: report.hairAtDrawStart}, {frames: 5, face: 3, hair: 2});
  assert.deepEqual(report.beforeDrawMs, {median: 10.25, p95: 25, max: 25}); assert.deepEqual(report.afterDrawMs, {median: 25.5, p95: 33, max: 33});
  assert.deepEqual(report.nearDraw, {requests: 2, inferenceMs: {median: 32, p95: 34, max: 34}}); assert.deepEqual(report.apart, {requests: 2, inferenceMs: {median: 12, p95: 12, max: 12}});
  assert.equal(describeOverlapReport(report), 'draw start: face in flight 60%, hair 40% · face post→next draw 10.3 ms, previous draw→post 25.5 ms · face inference 32.0 ms (n 2) with a draw ≤10 ms after post, else 12.0 ms (n 2)');
  assert.equal(describeOverlapReport(overlapReport([])), 'overlap: no frames');
});

test('the diagnostics hair event stays under the 600-character event cap in its longest realistic form', () => {
  const d = {median: 1234.5, p95: 1234.5, max: 1234.5};
  const overlap = describeOverlapReport({frames: 300, faceAtDrawStart: 300, hairAtDrawStart: 300, beforeDrawMs: d, afterDrawMs: d,
    nearDraw: {requests: 300, inferenceMs: d}, apart: {requests: 300, inferenceMs: d}});
  const masks = describeHairReport({frames: 300, own: 300, reused: 300, none: 300, jobs: 300, reuseAgeMs: {median: 1000, p95: 1000, max: 1000}, reuseMotionPx: {median: 123.4, p95: 1234.5, max: 1234.5}},
    'every frame waits for its own mask');
  const worker = 'hair worker 123456 results, 123456 missed · 10 s: 1024 jobs, inference 1234.5 / p95 1234.5 ms, round trip 1234.5 / p95 1234.5 ms';
  const event = `${worker} · ${overlap} · ${masks}`;
  assert.ok(event.length < 600, `${event.length} characters: ${event}`);
});
