/** The experimental face-width fit (`?fit=width`): the estimate itself (stability across distance and rotation, what it
 *  refuses, what a session reset does) and the geometry it drives (exact restoration of the original arms, and the same
 *  deformation in the drawn arms, the continuity centrelines and the protection corridor). */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {BoxGeometry, BufferGeometry, Euler, Float32BufferAttribute, Group, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Quaternion, Vector3, Vector4} from 'three';
import {
  ARM_LATERAL_MIN_M, armSpreadCurve, armSpreadM, armSpreadSlope, canonicalRegionSpans, DEFAULT_SPREAD_REACH,
  FaceWidthEstimator, MAX_ARM_SPREAD_M, observeFaceWidth, spreadArmX, SPREAD_REACH_RANGE, totalArmSpreadM,
  WIDTH_FIT, WIDTH_REGIONS,
} from '../src/render/face-width.ts';
import {createRearDrop, rearDropCurve, REAR_DROP_PARAMETERS} from '../src/render/rear-drop.ts';
import {buildTempleContinuityModel, CONTINUITY_GEOMETRY, projectTempleContinuity} from '../src/render/continuity.ts';
import {createProtection, protectionProjection, projectBounds} from '../src/render/protection.ts';
import {TEMPLE_VISIBILITY_PARAMETERS} from '../src/render/temple-visibility.ts';
import {GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';
import {LiveRenderer} from '../src/render/live-renderer.ts';

const canonical: number[] = (JSON.parse(readFileSync(new URL('../public/models/canonical-face.json', import.meta.url), 'utf8')) as {positions: number[]}).positions;
const SPANS = canonicalRegionSpans(canonical);

interface Pose {yawDeg?: number; pitchDeg?: number; rollDeg?: number; depthCm?: number; scale?: number;}
const poseMatrix = ({yawDeg = 0, pitchDeg = 0, rollDeg = 0, depthCm = 45, scale = 1}: Pose): Matrix4 => {
  const radians = Math.PI / 180;
  const quaternion = new Quaternion().setFromEuler(new Euler(pitchDeg * radians, yawDeg * radians, rollDeg * radians, 'YXZ'));
  return new Matrix4().compose(new Vector3(0, 0, -depthCm), quaternion, new Vector3(scale, scale, scale));
};
/** A face whose lateral proportions are `widthRatio` times the canonical face's, seen under `pose`. The pose is the
 *  detector's: rotation, distance and the fitted overall scale. `jitterCm` is per-landmark noise in the head frame. */
function observedSurface(widthRatio: number, pose: Pose, jitterCm = 0, seed = 1): Float32Array {
  let state = seed >>> 0;
  const noise = (): number => {state = (state * 1664525 + 1013904223) >>> 0; return (state / 2 ** 32 - 0.5) * 2 * jitterCm;};
  const matrix = poseMatrix(pose), point = new Vector3(), out = new Float32Array(468 * 3);
  for (let index = 0; index < 468; index++) {
    point.set(canonical[index * 3]! * widthRatio + noise(), canonical[index * 3 + 1]! + noise(), canonical[index * 3 + 2]! + noise());
    point.applyMatrix4(matrix);
    out[index * 3] = point.x; out[index * 3 + 1] = point.y; out[index * 3 + 2] = point.z;
  }
  return out;
}

test('the width ratio is a proportion: the same face reads the same at every distance, scale and near-frontal rotation', () => {
  const poses: Pose[] = [{}, {depthCm: 30}, {depthCm: 90}, {scale: 0.6}, {scale: 1.9},
    {yawDeg: 11.5}, {yawDeg: -11.5}, {pitchDeg: 14}, {pitchDeg: -14}, {rollDeg: 11},
    {yawDeg: 9, pitchDeg: -9, rollDeg: 6, depthCm: 70, scale: 1.4}];
  for (const truth of [0.93, 1, 1.07]) {
    const read = poses.map(pose => observeFaceWidth(observedSurface(truth, pose), SPANS, poseMatrix(pose).toArray()));
    for (const [index, result] of read.entries()) {
      assert.equal(result.rejected, null, `${truth} at pose ${index}`);
      assert.ok(Math.abs(result.observation!.ratio - truth) < 1e-5, `${truth} at pose ${index}: ${result.observation!.ratio}`);
      // Every region sees the same proportion: the ratio is not carried by one landmark pair.
      assert.equal(result.observation!.regionRatios.length, WIDTH_REGIONS.length);
      assert.ok(result.observation!.regionRatios.every(value => Math.abs(value - truth) < 1e-5));
    }
  }
  // The raw spans this is taken from do move with distance and rotation: it is the head-relative step that removes
  // them, not a weak test. Camera-space width at 30 cm against 90 cm, and frontal against 11.5 degrees of yaw:
  const span = (surface: Float32Array): number => Math.abs(surface[127 * 3]! - surface[356 * 3]!);
  assert.ok(span(observedSurface(1, {depthCm: 30})) === span(observedSurface(1, {depthCm: 90})), 'camera-space x does not carry distance');
  assert.ok(span(observedSurface(1, {scale: 0.6})) < span(observedSurface(1, {scale: 1.9})) * 0.4, 'the fitted scale does');
  assert.ok(span(observedSurface(1, {yawDeg: 11.5})) < span(observedSurface(1, {})) * 0.99, 'and so does yaw');
});

test('landmark noise averages out: the estimate settles near the truth and stays there through a turn', () => {
  const estimator = new FaceWidthEstimator(canonical);
  assert.equal(estimator.state, 'collecting'); assert.equal(estimator.ratio, 1);
  // Collecting: the original geometry, whatever the observations say.
  for (let frame = 0; frame < WIDTH_FIT.minSamples - 1; frame++) {
    estimator.observe(observedSurface(1.06, {yawDeg: (frame % 7) - 3}, 0.06, frame + 1), poseMatrix({yawDeg: (frame % 7) - 3}).toArray(), frame * 33);
    assert.equal(estimator.ratio, 1, 'nothing is applied before there is enough evidence');
  }
  for (let frame = WIDTH_FIT.minSamples - 1; frame < 240; frame++) {
    estimator.observe(observedSurface(1.06, {yawDeg: (frame % 7) - 3, depthCm: 35 + (frame % 5) * 12}, 0.06, frame + 1),
      poseMatrix({yawDeg: (frame % 7) - 3, depthCm: 35 + (frame % 5) * 12}).toArray(), frame * 33);
  }
  assert.equal(estimator.state, 'stable');
  assert.ok(Math.abs(estimator.ratio - 1.06) < 0.01, `settled at ${estimator.ratio}`);
  assert.ok(estimator.sampleCount <= WIDTH_FIT.bufferSamples);
  // A turn is refused, not averaged in: the estimate is held exactly.
  const held = estimator.ratio;
  for (let frame = 240; frame < 300; frame++) {
    assert.equal(estimator.observe(observedSurface(1.06, {yawDeg: 35}, 0.06, frame), poseMatrix({yawDeg: 35}).toArray(), frame * 33), null);
    assert.equal(estimator.ratio, held);
  }
  assert.equal(estimator.lastRejection, 'pose');
  assert.equal(estimator.state, 'stable');
});

test('the applied ratio leaves the original geometry slowly and never exceeds its own bounds', () => {
  const estimator = new FaceWidthEstimator(canonical);
  const step = (frame: number, truth: number): void => {
    estimator.observe(observedSurface(truth, {}), poseMatrix({}).toArray(), frame * 33);
  };
  for (let frame = 0; frame < WIDTH_FIT.minSamples; frame++) step(frame, 1.18);
  // The first applied frame starts at the original geometry and eases from there.
  assert.equal(estimator.state, 'stable');
  assert.ok(estimator.ratio > 1 && estimator.ratio - 1 < 0.02, `first step ${estimator.ratio}`);
  for (let frame = WIDTH_FIT.minSamples; frame < 400; frame++) step(frame, 1.18);
  // An 18 % observation is believed but only 8 % of it may be applied.
  assert.equal(estimator.observedRatio! > 1.17, true);
  assert.ok(Math.abs(estimator.ratio - WIDTH_FIT.appliedMaxRatio) < 1e-6, `clamped at ${estimator.ratio}`);
  // The ratio bound is what binds: 8 % of the 0.07 m reference is 5.6 mm per arm, inside the hard 6 mm cap.
  const widest = (WIDTH_FIT.appliedMaxRatio - 1) * WIDTH_FIT.armSpreadPerRatioM;
  assert.ok(widest <= WIDTH_FIT.maxArmSpreadM, 'the hard cap can never be exceeded');
  assert.ok(Math.abs(armSpreadM(estimator.ratio) - widest) <= WIDTH_FIT.spreadQuantumM);
  assert.equal(armSpreadM(1), 0); assert.equal(armSpreadM(Number.NaN), 0);
  assert.ok(Math.abs(armSpreadM(3) - widest) <= WIDTH_FIT.spreadQuantumM);
  assert.ok(Math.abs(armSpreadM(0.5) + (1 - WIDTH_FIT.appliedMinRatio) * WIDTH_FIT.armSpreadPerRatioM) <= WIDTH_FIT.spreadQuantumM);
  assert.ok(Math.abs(armSpreadM(3)) <= WIDTH_FIT.maxArmSpreadM && Math.abs(armSpreadM(0.5)) <= WIDTH_FIT.maxArmSpreadM);
  // 0.1 mm steps, so a still head does not rebuild the arm buffers on every frame.
  for (const ratio of [1.001, 1.021, 0.977]) assert.equal(Math.abs(Math.round(armSpreadM(ratio) * 1e7) % 1000), 0);
});

test('an unusable observation is refused rather than guessed, and each refusal says why', () => {
  const frontal = poseMatrix({}).toArray();
  const turned = {yawDeg: 25};
  assert.equal(observeFaceWidth(observedSurface(1, turned), SPANS, poseMatrix(turned).toArray()).rejected, 'pose');
  assert.equal(observeFaceWidth(observedSurface(1, {pitchDeg: 22}), SPANS, poseMatrix({pitchDeg: 22}).toArray()).rejected, 'pose');
  assert.equal(observeFaceWidth(observedSurface(1, {rollDeg: 20}), SPANS, poseMatrix({rollDeg: 20}).toArray()).rejected, 'pose');
  assert.equal(observeFaceWidth(observedSurface(1, {}), SPANS, Array(16).fill(0)).rejected, 'pose');
  assert.equal(observeFaceWidth(observedSurface(1, {}), SPANS, [...frontal.slice(0, 15), Number.NaN]).rejected, 'pose');
  assert.equal(observeFaceWidth(observedSurface(1, {}), SPANS, frontal.slice(0, 12)).rejected, 'pose');
  // Incomplete input: too few points, or a non-finite one inside a region.
  assert.equal(observeFaceWidth(new Float32Array(300), SPANS, frontal).rejected, 'landmarks');
  const broken = observedSurface(1, {}); broken[WIDTH_REGIONS[0]!.left[1]! * 3 + 1] = Number.NaN;
  assert.equal(observeFaceWidth(broken, SPANS, frontal).rejected, 'landmarks');
  // One side of the head displaced: a turned or partly occluded observation, not a wider face.
  const lopsided = observedSurface(1, {});
  for (const region of WIDTH_REGIONS) for (const index of region.left) lopsided[index * 3] = lopsided[index * 3]! - 1.6;
  assert.equal(observeFaceWidth(lopsided, SPANS, frontal).rejected, 'asymmetry');
  // Regions that disagree beyond what a face's own proportions explain (see WIDTH_FIT.maxRegionSpread).
  const disagreeing = observedSurface(1, {});
  for (const index of [...WIDTH_REGIONS[3]!.left, ...WIDTH_REGIONS[3]!.right]) disagreeing[index * 3] = disagreeing[index * 3]! * 1.4;
  assert.equal(observeFaceWidth(disagreeing, SPANS, frontal).rejected, 'inconsistent');
  // A face whose bands differ by as much as the measured fixture face still reads, and reads its median.
  const profiled = observedSurface(1, {});
  const scaleRegion = (region: number, factor: number): void => {
    for (const index of [...WIDTH_REGIONS[region]!.left, ...WIDTH_REGIONS[region]!.right]) profiled[index * 3] = profiled[index * 3]! * factor;
  };
  scaleRegion(1, 1.06); scaleRegion(2, 1.047); scaleRegion(3, 0.945); scaleRegion(4, 0.965);
  const measured = observeFaceWidth(profiled, SPANS, frontal);
  assert.equal(measured.rejected, null, 'the proportions of a real face are not a bad observation');
  assert.ok(Math.abs(measured.observation!.ratio - 1) < 1e-6, 'and the summary is their median');
  // Beyond what any face plausibly is.
  assert.equal(observeFaceWidth(observedSurface(1.5, {}), SPANS, poseMatrix({}).toArray()).rejected, 'range');
  assert.equal(observeFaceWidth(observedSurface(0.6, {}), SPANS, poseMatrix({}).toArray()).rejected, 'range');
  // A refused observation never reaches the applied ratio.
  const estimator = new FaceWidthEstimator(canonical);
  for (let frame = 0; frame < 200; frame++) assert.equal(estimator.observe(observedSurface(1.5, {}), poseMatrix({}).toArray(), frame * 33), null);
  assert.equal(estimator.ratio, 1); assert.equal(estimator.state, 'collecting'); assert.equal(estimator.accepted, 0);
});

test('a brief tracking failure holds the estimate; a sustained one drops it back to the original geometry', () => {
  const estimator = new FaceWidthEstimator(canonical);
  for (let frame = 0; frame < 300; frame++) estimator.observe(observedSurface(1.05, {}), poseMatrix({}).toArray(), frame * 33);
  const held = estimator.ratio;
  assert.equal(estimator.state, 'stable'); assert.ok(held > 1.04);
  let now = 300 * 33;
  for (let frame = 0; frame < 80; frame++) {estimator.miss(now += 33); assert.equal(estimator.ratio, held);}
  assert.ok(now - 300 * 33 < WIDTH_FIT.lostResetMs, 'still inside the brief-loss window');
  estimator.miss(now + WIDTH_FIT.lostResetMs + 1);
  assert.equal(estimator.ratio, 1); assert.equal(estimator.state, 'fallback'); assert.equal(estimator.sampleCount, 0);
  // It collects again from nothing, and says it is on the fallback until it is stable.
  for (let frame = 0; frame < WIDTH_FIT.minSamples - 1; frame++) {
    estimator.observe(observedSurface(1.05, {}), poseMatrix({}).toArray(), 100_000 + frame * 33);
    assert.equal(estimator.state, 'fallback'); assert.equal(estimator.ratio, 1);
  }
  estimator.observe(observedSurface(1.05, {}), poseMatrix({}).toArray(), 200_000);
  assert.equal(estimator.state, 'stable');
  // A new session (a new estimator) starts from nothing; an explicit reset is the same.
  estimator.reset();
  assert.equal(estimator.ratio, 1); assert.equal(estimator.state, 'collecting'); assert.equal(estimator.accepted, 0);
  assert.equal(estimator.observedRatio, null);
  // miss() before any tracked frame cannot reset anything.
  const fresh = new FaceWidthEstimator(canonical); fresh.miss(1e9); assert.equal(fresh.state, 'collecting');
});

test('the lateral rule the fixed temple code tests is one number, and a fitted point never crosses it', () => {
  assert.equal(ARM_LATERAL_MIN_M, REAR_DROP_PARAMETERS.lateralMinM);
  assert.equal(ARM_LATERAL_MIN_M, CONTINUITY_GEOMETRY.lateralMinM);
  assert.equal(ARM_LATERAL_MIN_M, TEMPLE_VISIBILITY_PARAMETERS.lateralArmMinM);
  const startZM = -0.025, cutoffZM = -0.1;
  for (const spread of [-WIDTH_FIT.maxArmSpreadM, -0.002, 0.002, WIDTH_FIT.maxArmSpreadM]) {
    for (const magnitude of [0.0451, 0.046, 0.05, 0.065, 0.09]) for (const side of [-1, 1]) {
      for (let z = startZM; z >= cutoffZM; z -= 0.005) {
        const moved = spreadArmX(side * magnitude, z, startZM, cutoffZM, spread);
        assert.ok(Math.abs(moved) > ARM_LATERAL_MIN_M, `${magnitude} at ${z} with ${spread} -> ${moved}`);
        assert.equal(Math.sign(moved), side);
      }
    }
    // Inside the lateral minimum nothing moves at all: the bridge, rims, pads and lenses are never touched.
    for (const x of [0, 0.01, -0.04, ARM_LATERAL_MIN_M, -ARM_LATERAL_MIN_M]) {
      assert.equal(spreadArmX(x, cutoffZM, startZM, cutoffZM, spread), x);
    }
    // The hinge end of the arm does not move either: the ramp starts at zero there.
    assert.ok(armSpreadCurve(startZM, startZM, cutoffZM, spread) === 0);
    assert.equal(spreadArmX(0.065, startZM, startZM, cutoffZM, spread), 0.065);
    assert.ok(armSpreadCurve(startZM + 0.01, startZM, cutoffZM, spread) === 0, 'in front of the arm, nothing moves');
    assert.ok(Math.abs(armSpreadCurve(cutoffZM, startZM, cutoffZM, spread) - spread) < 1e-12, 'the full spread at the cap');
    assert.ok(Math.abs(armSpreadCurve(cutoffZM - 0.05, startZM, cutoffZM, spread) - spread) < 1e-12, 'and no further');
  }
  // Spread 0 is the identity everywhere, exactly.
  for (const x of [-0.09, -0.045, 0, 0.0451, 0.07]) for (let z = 0; z > -0.15; z -= 0.01) {
    assert.equal(spreadArmX(x, z, startZM, cutoffZM, 0), x);
    assert.ok(armSpreadCurve(z, startZM, cutoffZM, 0) === 0);
    assert.ok(armSpreadSlope(z, startZM, cutoffZM, 0) === 0);
  }
  // The slope is the derivative of the curve, so normals and tangents follow the same shear the positions do.
  for (const z of [-0.03, -0.05, -0.07, -0.09]) {
    const h = 1e-6, spread = 0.004;
    const numeric = (armSpreadCurve(z + h, startZM, cutoffZM, spread) - armSpreadCurve(z - h, startZM, cutoffZM, spread)) / (2 * h);
    assert.ok(Math.abs(numeric - armSpreadSlope(z, startZM, cutoffZM, spread)) < 1e-6, `${z}: ${numeric}`);
  }
  assert.throws(() => armSpreadCurve(-0.05, -0.1, -0.025, 0.004), /span is invalid/);
  assert.throws(() => armSpreadCurve(-0.05, startZM, cutoffZM, 0.05), /out of range/);
  assert.throws(() => spreadArmX(Number.NaN, -0.05, startZM, cutoffZM, 0.004), /position is invalid/);
});

test('the page switches the live renderer between the two pipelines and never runs both', () => {
  // The selector reaches the renderer through the live wrapper, which forwards nothing once the session is closed.
  const calls: boolean[] = [];
  const inner = {setWidthFit: (value: boolean) => {calls.push(value);}, widthFit: {mode: 'original', state: 'off', ratio: 1}, dispose() {}};
  const live = new (LiveRenderer as unknown as new (renderer: unknown) => LiveRenderer)(inner);
  assert.equal(live.widthFit.mode, 'original');
  live.setWidthFit(true); live.setWidthFit(false);
  assert.deepEqual(calls, [true, false]);
  live.dispose(); live.setWidthFit(true);
  assert.deepEqual(calls, [true, false], 'a closed session does not reach the renderer');
});

test('the manual bend splays each arm outward from its hinge, and the front of the frame never moves', () => {
  const startZM = -0.026, cutoffZM = -0.140, bend = 0.008;
  // The hinge end and everything in front of it are untouched: this is a bend, not a wider frame.
  for (const z of [0, -0.005, -0.02, startZM]) {
    assert.equal(spreadArmX(0.065, z, startZM, cutoffZM, bend), 0.065, `nothing moves at z ${z}`);
    assert.equal(spreadArmX(0.02, z, startZM, cutoffZM, bend), 0.02, 'and the bridge and rims are never lateral enough to move');
  }
  // Behind the hinge it opens out, monotonically, reaching the full bend at the arm's end.
  let previous = 0.065;
  for (let z = startZM; z >= cutoffZM; z -= 0.005) {
    const moved = spreadArmX(0.065, z, startZM, cutoffZM, bend);
    assert.ok(moved >= previous - 1e-12, `the arm only opens outward going back (z ${z})`);
    previous = moved;
  }
  assert.ok(Math.abs(spreadArmX(0.065, cutoffZM, startZM, cutoffZM, bend) - (0.065 + bend)) < 1e-12,
    'the full bend lands at the tip');
  // Both arms move by the same amount, in opposite directions: the frame stays symmetric.
  for (const z of [-0.05, -0.09, cutoffZM]) {
    assert.ok(Math.abs(spreadArmX(0.065, z, startZM, cutoffZM, bend) + spreadArmX(-0.065, z, startZM, cutoffZM, bend)) < 1e-12, `symmetric at z ${z}`);
  }
  // A negative bend pulls the arms in by the same curve, and 0 is exactly the authored geometry.
  for (let z = startZM; z >= cutoffZM; z -= 0.01) {
    const out = spreadArmX(0.065, z, startZM, cutoffZM, bend) - 0.065;
    const inward = spreadArmX(0.065, z, startZM, cutoffZM, -bend) - 0.065;
    assert.ok(Math.abs(out + inward) < 1e-12, `mirrored at z ${z}`);
    assert.equal(spreadArmX(0.065, z, startZM, cutoffZM, 0), 0.065);
  }
  // A bend at the cap is still bounded and still cannot cross the lateral plane the fixed temple rules test.
  for (const magnitude of [0.0451, 0.05, 0.065]) for (const side of [-1, 1]) for (let z = startZM; z >= cutoffZM; z -= 0.01) {
    const moved = spreadArmX(side * magnitude, z, startZM, cutoffZM, -MAX_ARM_SPREAD_M);
    assert.ok(Math.abs(moved) > ARM_LATERAL_MIN_M && Math.sign(moved) === side);
  }
  assert.throws(() => armSpreadCurve(-0.05, startZM, cutoffZM, MAX_ARM_SPREAD_M * 2), /out of range/);
});

test('the bend and the automatic width fit add up, bounded, and either one alone still works', () => {
  // The fit's own cap is far below the shared one, so a bend can always be added on top of a settled fit.
  assert.ok(WIDTH_FIT.maxArmSpreadM < MAX_ARM_SPREAD_M);
  assert.equal(totalArmSpreadM(0, 0), 0);
  assert.ok(Math.abs(totalArmSpreadM(0.003, 0.005) - 0.008) < 1e-9, 'they add');
  assert.ok(Math.abs(totalArmSpreadM(-0.003, 0.005) - 0.002) < 1e-9, 'including against each other');
  assert.equal(totalArmSpreadM(0.006, MAX_ARM_SPREAD_M), MAX_ARM_SPREAD_M, 'and the pair is capped');
  assert.equal(totalArmSpreadM(-0.006, -MAX_ARM_SPREAD_M), -MAX_ARM_SPREAD_M);
  assert.equal(totalArmSpreadM(Number.NaN, 0.004), 0.004, 'a missing contribution is not a missing bend');
  assert.equal(totalArmSpreadM(0.004, Number.NaN), 0.004);
  // Rounded to the same 0.1 mm step as the fit, so a still head does not rebuild the arm buffers every frame.
  for (const pair of [[0.00123, 0.00047], [-0.0009, 0.0034]] as const) {
    assert.equal(Math.abs(Math.round(totalArmSpreadM(pair[0], pair[1]) * 1e7) % 1000), 0);
  }
  // The total is always a spread the geometry will accept.
  for (const fit of [-0.006, 0, 0.006]) for (const bend of [-MAX_ARM_SPREAD_M, -0.004, 0, 0.004, MAX_ARM_SPREAD_M]) {
    const total = totalArmSpreadM(fit, bend);
    assert.ok(Math.abs(total) <= MAX_ARM_SPREAD_M + 1e-12);
    for (const reach of [SPREAD_REACH_RANGE.min, 0.7, SPREAD_REACH_RANGE.max]) {
      assert.doesNotThrow(() => armSpreadCurve(-0.05, -0.026, -0.14, total, reach));
    }
  }
  // The cap is the arms' own inward curl, so a full bend straightens an arm and never turns it outward past its hinge.
  // Amber Horizon runs from |x| 7.24 cm at the hinge shaft to 5.08 cm at the hook, Tom Ford from 7.25 to 5.31 cm.
  for (const taper of [0.0724 - 0.0508, 0.0725 - 0.0531]) assert.ok(Math.abs(MAX_ARM_SPREAD_M - taper) <= 0.005, `${taper}`);
});

test('the bend reach lands the full bend earlier along the arm, and the arm runs back parallel from there', () => {
  const startZM = -0.026, cutoffZM = -0.140, bend = 0.010;
  const fullZ = (reach: number): number => startZM + (cutoffZM - startZM) * reach;
  assert.equal(DEFAULT_SPREAD_REACH, SPREAD_REACH_RANGE.max, 'the shipped ramp spans the whole arm');
  for (const reach of [SPREAD_REACH_RANGE.min, 0.5, 0.65, 0.8, 1]) {
    // The front of the frame still does not move, whatever the reach: this stays a bend at the hinge.
    for (const z of [0, -0.01, startZM]) assert.equal(spreadArmX(0.065, z, startZM, cutoffZM, bend, reach), 0.065, `reach ${reach}, z ${z}`);
    assert.ok(Math.abs(armSpreadCurve(fullZ(reach), startZM, cutoffZM, bend, reach) - bend) < 1e-12, `the full bend by the reach plane (${reach})`);
    // Behind the reach plane the offset is constant and flat, so the arm runs parallel and the ramp leaves no corner.
    for (let z = fullZ(reach); z >= cutoffZM; z -= 0.002) {
      assert.ok(Math.abs(armSpreadCurve(z, startZM, cutoffZM, bend, reach) - bend) < 1e-12, `parallel (reach ${reach}, z ${z})`);
      assert.equal(Math.abs(armSpreadSlope(z, startZM, cutoffZM, bend, reach)), 0, `flat (reach ${reach}, z ${z})`);
    }
    // Monotone, bounded by the bend, symmetric between the two arms, and 0 is still the identity.
    let previous = 0;
    for (let z = startZM; z >= cutoffZM; z -= 0.002) {
      const offset = armSpreadCurve(z, startZM, cutoffZM, bend, reach);
      assert.ok(offset >= previous - 1e-12 && offset <= bend + 1e-12, `bounded and monotone (reach ${reach}, z ${z})`);
      assert.ok(Math.abs(spreadArmX(0.065, z, startZM, cutoffZM, bend, reach) + spreadArmX(-0.065, z, startZM, cutoffZM, bend, reach)) < 1e-12);
      assert.equal(spreadArmX(0.065, z, startZM, cutoffZM, 0, reach), 0.065);
      previous = offset;
    }
  }
  // What the lever is for: the same bend, more of it at every station in between, and exactly the same at both ends.
  for (let z = startZM; z >= cutoffZM; z -= 0.002) {
    assert.ok(armSpreadCurve(z, startZM, cutoffZM, bend, 0.65) >= armSpreadCurve(z, startZM, cutoffZM, bend, 1) - 1e-12, `z ${z}`);
  }
  assert.equal(armSpreadCurve(startZM, startZM, cutoffZM, bend, 0.65), armSpreadCurve(startZM, startZM, cutoffZM, bend, 1));
  assert.equal(armSpreadCurve(cutoffZM, startZM, cutoffZM, bend, 0.65), armSpreadCurve(cutoffZM, startZM, cutoffZM, bend, 1));
  // At the ear — about 7.4 cm behind the hinge on the shipped frames — a 65 % reach is worth more than 2 mm of bend.
  assert.ok(armSpreadCurve(-0.100, startZM, cutoffZM, bend, 0.65) - armSpreadCurve(-0.100, startZM, cutoffZM, bend, 1) > 0.002);
  // The slope stays the curve's own derivative, so normals and tangents follow the shape the arm is drawn with.
  for (const reach of [0.4, 0.7, 1]) for (let z = startZM - 0.002; z > cutoffZM; z -= 0.003) {
    const h = 1e-6;
    const numeric = (armSpreadCurve(z + h, startZM, cutoffZM, bend, reach) - armSpreadCurve(z - h, startZM, cutoffZM, bend, reach)) / (2 * h);
    assert.ok(Math.abs(numeric - armSpreadSlope(z, startZM, cutoffZM, bend, reach)) < 1e-5, `slope at reach ${reach}, z ${z}`);
  }
  // A reach outside the range is refused rather than clamped: too short a ramp is a kink, not a bend.
  for (const bad of [0, 0.2, SPREAD_REACH_RANGE.min - 0.01, 1.002, Number.NaN, Infinity]) {
    assert.throws(() => armSpreadCurve(-0.05, startZM, cutoffZM, bend, bad), /reach is out of range|span is invalid/, `curve ${bad}`);
    assert.throws(() => armSpreadSlope(-0.05, startZM, cutoffZM, bend, bad), /reach is out of range|span is invalid/, `slope ${bad}`);
    assert.throws(() => spreadArmX(0.065, -0.05, startZM, cutoffZM, bend, bad), /reach is out of range|span is invalid/, `x ${bad}`);
  }
});

test('a bent arm is bent everywhere the pipeline reads it: geometry, centrelines and the protection corridor', () => {
  // The synthetic arms end at z -0.12, so the cap sits inside them as the shipped ones do inside theirs.
  const bend = 0.008, cap = -.11;
  const {root, dispose} = asset(64);
  const model = buildTempleContinuityModel(root, cap);
  const drop = createRearDrop(root, cap);
  const originals = armPositions(root).map(array => array.slice());
  try {
    drop.setSpread(bend);
    // The drawn arms: the hinge end is untouched and the tips have opened out by the full bend.
    for (const [mesh, positions] of armPositions(root).entries()) for (let i = 0; i < positions.length; i += 3) {
      const [x0, z0] = [originals[mesh]![i]!, originals[mesh]![i + 2]!];
      if (Math.abs(x0) <= ARM_LATERAL_MIN_M || z0 >= model.startZM) assert.equal(positions[i], x0);
      else assert.ok(Math.abs(positions[i]!) > Math.abs(x0));
    }
    const pose = new Matrix4().makeRotationY(18 * Math.PI / 180).setPosition(0, 0, -40).toArray();
    const input = {eyewearMatrix: pose, offsetCm: GLASSES_OFFSET_CM, sourceAspect: 1.5, width: 1200, height: 800, dropM: .012};
    const bent = projectTempleContinuity(model, {...input, spreadM: bend})!;
    const plain = projectTempleContinuity(model, input)!;
    for (const side of [0, 1]) {
      // The cut walks the bent arm, not where the arm used to be: the hinge station is unmoved, the tip is not.
      assert.ok(Math.abs(bent[side]!.points[0]!.x - plain[side]!.points[0]!.x) < 1e-9);
      assert.ok(Math.abs(bent[side]!.points.at(-1)!.x - plain[side]!.points.at(-1)!.x) > 1);
    }
    // The stencil's editable corridor is built from the bent arm bounds, so every bent centreline point is inside it.
    const landmarks = Array.from({length: 478}, () => ({x: .5, y: .45, z: 0}));
    landmarks[33] = {x: .42, y: .43, z: 0}; landmarks[263] = {x: .58, y: .43, z: 0}; landmarks[2] = {x: .5, y: .52, z: 0};
    drop.setShape(input.dropM, bend);
    const protection = createProtection({optical: drop.opticalBounds, originalArms: drop.originalArmBounds, candidateArms: drop.candidateArmBounds},
      pose, GLASSES_OFFSET_CM, landmarks, input.width, input.height, input.sourceAspect)!;
    assert.ok(protection);
    for (const path of bent) for (const point of path.points) {
      assert.ok(protection.editableRects.some(rect => point.x >= rect.x0 - 1 && point.x <= rect.x1 + 1 && point.y >= rect.y0 - 1 && point.y <= rect.y1 + 1),
        `a bent centreline point (${point.x.toFixed(1)}, ${point.y.toFixed(1)}) fell outside the editable corridor`);
    }
    // And bend 0 is the authored frame again, exactly.
    drop.setShape(0, 0);
    assert.deepEqual(armPositions(root), originals);
    assert.deepEqual(projectTempleContinuity(model, {...input, spreadM: 0}), plain);
  } finally {drop.dispose(); dispose();}
});

test('the reach moves the bend along the arm in the geometry, the centrelines and the corridor together', () => {
  const bend = 0.008, cap = -.11, reach = 0.5;
  const {root, dispose} = asset(64);
  const model = buildTempleContinuityModel(root, cap);
  const fullZ = model.startZM + (cap - model.startZM) * reach;
  const drop = createRearDrop(root, cap, reach);
  const originals = armPositions(root).map(array => array.slice());
  try {
    assert.equal(drop.spreadReach, reach);
    drop.setSpread(bend);
    // The drawn arms: untouched in front of the hinge plane, the full bend from the reach plane back, rising between.
    let parallel = 0, rising = 0;
    for (const [mesh, positions] of armPositions(root).entries()) for (let i = 0; i < positions.length; i += 3) {
      const [x0, z0] = [originals[mesh]![i]!, originals[mesh]![i + 2]!];
      if (Math.abs(x0) <= ARM_LATERAL_MIN_M || z0 >= model.startZM) {assert.equal(positions[i], x0); continue;}
      // Positions are float32, so the tolerances are a few ulps at 7 cm, and the band around the reach plane itself is
      // left to the two bounds above rather than classified.
      const offset = Math.abs(positions[i]!) - Math.abs(x0);
      assert.ok(offset > -1e-7 && offset < bend + 1e-7, `bounded by the bend (z ${z0})`);
      if (z0 <= fullZ) {assert.ok(Math.abs(offset - bend) < 1e-7, `the full bend by the reach plane (z ${z0})`); parallel++;}
      else if (z0 > fullZ + 0.005) {assert.ok(offset > 0 && offset < bend - 1e-4, `still rising ahead of it (z ${z0})`); rising++;}
    }
    assert.ok(parallel > 0 && rising > 0, 'the fixture has arm vertices on both sides of the reach plane');
    const pose = new Matrix4().makeRotationY(18 * Math.PI / 180).setPosition(0, 0, -40).toArray();
    const input = {eyewearMatrix: pose, offsetCm: GLASSES_OFFSET_CM, sourceAspect: 1.5, width: 1200, height: 800, dropM: .012, spreadM: bend};
    const early = projectTempleContinuity(model, {...input, spreadReach: reach})!;
    const full = projectTempleContinuity(model, input)!;
    for (const side of [0, 1]) {
      // Same bend, same two ends, a different arm in between: this is the whole point of the lever.
      assert.ok(Math.abs(early[side]!.points[0]!.x - full[side]!.points[0]!.x) < 1e-9, 'the hinge station is where it was');
      assert.ok(Math.abs(early[side]!.points.at(-1)!.x - full[side]!.points.at(-1)!.x) < 1e-9, 'and so is the tip');
      const middle = Math.round((early[side]!.points.length - 1) / 2);
      assert.ok(Math.abs(early[side]!.points[middle]!.x - full[side]!.points[middle]!.x) > 1,
        'but the cut walks a differently bent arm in between, so a projection that ignored the reach would be caught here');
    }
    // The corridor is built from the same geometry, so every centreline point of the bent arm is inside it.
    const landmarks = Array.from({length: 478}, () => ({x: .5, y: .45, z: 0}));
    landmarks[33] = {x: .42, y: .43, z: 0}; landmarks[263] = {x: .58, y: .43, z: 0}; landmarks[2] = {x: .5, y: .52, z: 0};
    drop.setShape(input.dropM, bend);
    const protection = createProtection({optical: drop.opticalBounds, originalArms: drop.originalArmBounds, candidateArms: drop.candidateArmBounds},
      pose, GLASSES_OFFSET_CM, landmarks, input.width, input.height, input.sourceAspect)!;
    for (const path of early) for (const point of path.points) {
      assert.ok(protection.editableRects.some(rect => point.x >= rect.x0 - 1 && point.x <= rect.x1 + 1 && point.y >= rect.y0 - 1 && point.y <= rect.y1 + 1),
        `an early-bend centreline point (${point.x.toFixed(1)}, ${point.y.toFixed(1)}) fell outside the editable corridor`);
    }
    // And bend 0 is the authored frame again, whatever the reach was.
    drop.setShape(0, 0);
    assert.deepEqual(armPositions(root), originals);
  } finally {drop.dispose(); dispose();}
  // A reach the ramp cannot draw is refused where the geometry is built, not silently clamped at the first frame.
  const second = asset(4);
  try {
    for (const bad of [0, 0.2, 1.5, Number.NaN]) assert.throws(() => createRearDrop(second.root, cap, bad), /reach is out of range/, `${bad}`);
  } finally {second.dispose();}
});

/** Two box arms and a lens triangle: the same shape the continuity tests use, so the rear drop and the continuity
 *  model can be built from one asset without a GPU. */
function asset(depthSegments = 1) {
  const root = new Group(), left = new BoxGeometry(.004, .004, .1, 1, 1, depthSegments), right = left.clone();
  left.translate(-.065, 0, -.07); right.translate(.065, 0, -.07);
  const frame = new MeshStandardMaterial(), lensMaterial = new MeshPhysicalMaterial({transmission: 1});
  const lens = new BufferGeometry().setAttribute('position', new Float32BufferAttribute([-.02, 0, -.01, .02, 0, -.01, 0, .01, -.005], 3));
  root.add(new Mesh(left, frame), new Mesh(right, frame), new Mesh(lens, lensMaterial));
  return {root, left, right, lens, dispose: () => {for (const resource of [left, right, lens, frame, lensMaterial]) resource.dispose();}};
}
const armPositions = (root: Group): Float32Array[] =>
  root.children.map(child => Float32Array.from(((child as Mesh).geometry.getAttribute('position').array as Float32Array)));

test('the width fit and the rear drop compose in one pass, and spread 0 restores the original arms exactly', () => {
  const {root, dispose} = asset();
  const originals = armPositions(root).map(array => array.slice());
  const drop = createRearDrop(root, -.1);
  try {
    assert.equal(drop.spreadM, 0); assert.equal(drop.dropM, 0);
    // The clone starts identical to the original.
    assert.deepEqual(armPositions(root), originals);
    drop.setSpread(.004);
    assert.equal(drop.spreadM, .004);
    const spreadOnly = armPositions(root);
    assert.notDeepEqual(spreadOnly, originals);
    // Only x moved, and only outward, and only behind the arm's start plane.
    for (const [mesh, positions] of spreadOnly.entries()) for (let i = 0; i < positions.length; i += 3) {
      const [x0, y0, z0] = [originals[mesh]![i]!, originals[mesh]![i + 1]!, originals[mesh]![i + 2]!];
      assert.equal(positions[i + 1], y0); assert.equal(positions[i + 2], z0);
      if (Math.abs(x0) <= ARM_LATERAL_MIN_M || z0 >= -.025) assert.equal(positions[i], x0);
      else assert.ok(Math.abs(positions[i]!) > Math.abs(x0));
    }
    // Composition: the drop on top of the spread keeps both, in either order, and never loses one to the other.
    drop.setDrop(.02);
    const both = armPositions(root);
    assert.equal(drop.spreadM, .004); assert.equal(drop.dropM, .02);
    for (const [mesh, positions] of both.entries()) for (let i = 0; i < positions.length; i += 3) {
      assert.equal(positions[i], spreadOnly[mesh]![i], 'the drop did not overwrite the fitted width');
      assert.ok(positions[i + 1]! <= originals[mesh]![i + 1]! + 1e-12, 'and the drop is still applied');
    }
    drop.setShape(0, 0); assert.deepEqual(armPositions(root), originals);
    drop.setShape(.02, .004); assert.deepEqual(armPositions(root), both);
    drop.setSpread(0);
    const dropOnly = armPositions(root);
    drop.setShape(0, 0); drop.setDrop(.02);
    assert.deepEqual(armPositions(root), dropOnly, 'the drop alone is byte-identical with or without a fit before it');
    // Back to Original: the shipped geometry, exactly.
    drop.setShape(0, 0); assert.deepEqual(armPositions(root), originals);
    assert.equal(drop.spreadM, 0);
    assert.throws(() => drop.setSpread(.03), /arm spread must be finite/i);
    assert.throws(() => drop.setSpread(Number.NaN), /arm spread must be finite/i);
    assert.deepEqual(armPositions(root), originals, 'a refused spread changes nothing');
  } finally {drop.dispose(); dispose();}
});

test('the fitted arms, the continuity centrelines and the protection corridor all move together', () => {
  const spread = .005, cap = -.1;
  // Tessellated along z: the mesh interpolates the ramp between its vertices, as the shipped arms do, so the baked
  // geometry and the analytic centreline agree to well under a pixel rather than to the chord of one long edge.
  const {root, dispose} = asset(64);
  const model = buildTempleContinuityModel(root, cap);
  const drop = createRearDrop(root, cap);
  // The same asset with the fit already baked into its vertices: its own centrelines are the reference.
  const baked = asset(64);
  for (const mesh of baked.root.children) {
    const positions = (mesh as Mesh).geometry.getAttribute('position');
    for (let i = 0; i < positions.count; i++) positions.setX(i, spreadArmX(positions.getX(i), positions.getZ(i), model.startZM, cap, spread));
  }
  const bakedModel = buildTempleContinuityModel(baked.root, cap);
  try {
    const pose = new Matrix4().makeRotationY(18 * Math.PI / 180).setPosition(0, 0, -40).toArray();
    const input = {eyewearMatrix: pose, offsetCm: GLASSES_OFFSET_CM, sourceAspect: 1.5, width: 1200, height: 800, dropM: .015};
    const fitted = projectTempleContinuity(model, {...input, spreadM: spread})!;
    const reference = projectTempleContinuity(bakedModel, {...input, spreadM: 0})!;
    const plain = projectTempleContinuity(model, input)!;
    assert.equal(fitted.length, 2);
    for (const side of [0, 1]) {
      // The fitted centreline is the centreline of the fitted geometry, to floating point.
      for (const [index, point] of fitted[side]!.points.entries()) {
        assert.ok(Math.abs(point.x - reference[side]!.points[index]!.x) < 0.05, `${side}/${index}: ${point.x} vs ${reference[side]!.points[index]!.x}`);
        assert.ok(Math.abs(point.y - reference[side]!.points[index]!.y) < 0.05);
      }
      // And exactly: a station is projected from the lateral position spreadArmX gives the arm vertices there.
      const projection = protectionProjection(pose, GLASSES_OFFSET_CM, input.sourceAspect);
      for (const [index, station] of model.sides[side]!.entries()) {
        const moved = spreadArmX(station.centerXM, station.zM, model.startZM, cap, spread);
        const clip = new Vector4(moved, station.centerYM - rearDropCurve(station.zM, model.startZM, cap, input.dropM).loweringM, station.zM, 1).applyMatrix4(projection);
        assert.ok(Math.abs((clip.x / clip.w + 1) * input.width / 2 - fitted[side]!.points[index]!.x) < 1e-9);
      }
      // The hinge end is where it always was; the tip has moved.
      assert.ok(Math.abs(fitted[side]!.points[0]!.x - plain[side]!.points[0]!.x) < 1e-9);
      assert.ok(Math.abs(fitted[side]!.points.at(-1)!.x - plain[side]!.points.at(-1)!.x) > 1);
    }
    // Without the fit, nothing changed at all: the default pipeline is byte-identical.
    assert.deepEqual(projectTempleContinuity(model, {...input, spreadM: 0}), plain);
    // The protection corridor is built from the fitted arm bounds, so every fitted centreline point stays inside the
    // editable region the stencil marks, and the optical rectangle is unmoved.
    drop.setShape(input.dropM, spread);
    const landmarks = Array.from({length: 478}, () => ({x: .5, y: .45, z: 0}));
    landmarks[33] = {x: .42, y: .43, z: 0}; landmarks[263] = {x: .58, y: .43, z: 0}; landmarks[2] = {x: .5, y: .52, z: 0};
    const bounds = {optical: drop.opticalBounds, originalArms: drop.originalArmBounds, candidateArms: drop.candidateArmBounds};
    const protection = createProtection(bounds, pose, GLASSES_OFFSET_CM, landmarks, input.width, input.height, input.sourceAspect)!;
    assert.ok(protection); assert.equal(protection.editableRects.length, 2);
    for (const path of fitted) {
      for (const point of path.points) {
        assert.ok(protection.editableRects.some(rect => point.x >= rect.x0 - 1 && point.x <= rect.x1 + 1 && point.y >= rect.y0 - 1 && point.y <= rect.y1 + 1),
          `a fitted centreline point (${point.x.toFixed(1)}, ${point.y.toFixed(1)}) fell outside the editable corridor`);
      }
    }
    // The corridor grew laterally with the arms rather than leaving them outside it.
    drop.setShape(input.dropM, 0);
    const unfitted = createProtection({optical: drop.opticalBounds, originalArms: drop.originalArmBounds, candidateArms: drop.candidateArmBounds},
      pose, GLASSES_OFFSET_CM, landmarks, input.width, input.height, input.sourceAspect)!;
    const width = (rects: readonly {x0: number; x1: number}[]): number => rects.reduce((sum, rect) => sum + rect.x1 - rect.x0, 0);
    assert.ok(width(protection.editableRects) > width(unfitted.editableRects));
    assert.deepEqual(protection.protectedRects[0], unfitted.protectedRects[0], 'the optical rectangle is untouched by the fit');
    assert.deepEqual(protection.protectedRects[0], projectBounds(drop.opticalBounds, protectionProjection(pose, GLASSES_OFFSET_CM, input.sourceAspect), input.width, input.height, 4));
  } finally {drop.dispose(); dispose(); baked.dispose();}
});
