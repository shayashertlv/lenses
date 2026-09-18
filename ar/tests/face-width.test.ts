/** The experimental face-width fit (`?fit=width`): the estimate itself (stability across distance and rotation, what it
 *  refuses, what a session reset does) and the geometry it drives (exact restoration of the original arms, and the same
 *  deformation in the drawn arms, the continuity centrelines and the protection corridor). */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {readFile} from 'node:fs/promises';
import {BoxGeometry, BufferGeometry, Euler, Float32BufferAttribute, Group, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Quaternion, Texture, Vector3, Vector4} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {
  ARM_LATERAL_MIN_M, armSpreadCurve, armSpreadM, armSpreadSlope, canonicalRegionSpans,
  FaceWidthEstimator, MAX_ARM_SPREAD_M, observeFaceWidth, spreadArmX, SPREAD_HINGE_ROUND_M, totalArmSpreadM,
  WIDTH_FIT, WIDTH_REGIONS,
} from '../src/render/face-width.ts';
import {armShaftStartZM, createRearDrop, rearDropCurve, REAR_DROP_PARAMETERS} from '../src/render/rear-drop.ts';
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
    assert.doesNotThrow(() => armSpreadCurve(-0.05, -0.026, -0.14, total));
  }
  // The cap is the arms' own inward curl, so a full bend straightens an arm and never turns it outward past its hinge.
  // Amber Horizon runs from |x| 7.24 cm at the hinge shaft to 5.08 cm at the hook, Tom Ford from 7.25 to 5.31 cm.
  for (const taper of [0.0724 - 0.0508, 0.0725 - 0.0531]) assert.ok(Math.abs(MAX_ARM_SPREAD_M - taper) <= 0.005, `${taper}`);
});

test('the bend is a hinge: rounded over the pivot, then a straight shaft at a constant angle', () => {
  // Amber Horizon's own numbers: the hinge the detector finds, its clip cap and the shipped default bend.
  const startZM = -0.015, cutoffZM = -0.140, bend = 0.014;
  const span = startZM - cutoffZM, slope = bend / (span - SPREAD_HINGE_ROUND_M / 2);
  // Nothing in front of the pivot moves, and the ramp leaves it with a zero slope: a hinge radius, not a corner.
  for (const z of [0, -0.005, -0.010, startZM]) {
    assert.equal(spreadArmX(0.065, z, startZM, cutoffZM, bend), 0.065, `nothing moves at z ${z}`);
    assert.equal(armSpreadCurve(z, startZM, cutoffZM, bend), 0, `no offset at z ${z}`);
  }
  assert.equal(Math.abs(armSpreadSlope(startZM, startZM, cutoffZM, bend)), 0);
  // Behind the hinge radius the shaft is STRAIGHT: equal increments per millimetre, all the way to the cap.
  const step = (z: number): number => armSpreadCurve(z - 0.001, startZM, cutoffZM, bend) - armSpreadCurve(z, startZM, cutoffZM, bend);
  for (let z = startZM - SPREAD_HINGE_ROUND_M; z > cutoffZM + 0.001; z -= 0.001) {
    assert.ok(Math.abs(step(z) - slope * 0.001) < 1e-12, `constant angle at z ${z}: ${step(z)}`);
    assert.ok(Math.abs(armSpreadSlope(z, startZM, cutoffZM, bend) + slope) < 1e-12, `and the slope says so at z ${z}`);
  }
  // Inside the hinge radius the angle opens up linearly from nothing to that same constant.
  for (let run = 0; run <= SPREAD_HINGE_ROUND_M; run += SPREAD_HINGE_ROUND_M / 6) {
    assert.ok(Math.abs(armSpreadSlope(startZM - run, startZM, cutoffZM, bend) + slope * run / SPREAD_HINGE_ROUND_M) < 1e-12, `run ${run}`);
  }
  // The full bend lands at the cap and stays there: the clipped stub behind it is not splayed further.
  assert.ok(Math.abs(armSpreadCurve(cutoffZM, startZM, cutoffZM, bend) - bend) < 1e-12, 'the full bend at the cap');
  for (const z of [cutoffZM - 0.001, cutoffZM - 0.01]) assert.ok(Math.abs(armSpreadCurve(z, startZM, cutoffZM, bend) - bend) < 1e-12);
  // Monotone, symmetric, mirrored under a negative bend, and 0 is exactly the authored geometry.
  let previous = 0;
  for (let z = startZM; z >= cutoffZM; z -= 0.002) {
    const offset = armSpreadCurve(z, startZM, cutoffZM, bend);
    assert.ok(offset >= previous - 1e-12 && offset <= bend + 1e-12, `bounded and monotone at z ${z}`);
    assert.ok(Math.abs(spreadArmX(0.065, z, startZM, cutoffZM, bend) + spreadArmX(-0.065, z, startZM, cutoffZM, bend)) < 1e-12, `symmetric at z ${z}`);
    assert.ok(Math.abs(offset + armSpreadCurve(z, startZM, cutoffZM, -bend)) < 1e-12, `mirrored at z ${z}`);
    assert.equal(spreadArmX(0.065, z, startZM, cutoffZM, 0), 0.065);
    previous = offset;
  }
  // The slope is the curve's own derivative everywhere, so normals and tangents follow the shape the arm is drawn with.
  for (let z = startZM - 0.0005; z > cutoffZM; z -= 0.0017) {
    const h = 1e-7;
    const numeric = (armSpreadCurve(z + h, startZM, cutoffZM, bend) - armSpreadCurve(z - h, startZM, cutoffZM, bend)) / (2 * h);
    assert.ok(Math.abs(numeric - armSpreadSlope(z, startZM, cutoffZM, bend)) < 1e-5, `slope at z ${z}`);
  }
  // A span too short to hold the hinge radius is refused rather than creased.
  assert.throws(() => armSpreadCurve(-0.02, startZM, startZM - SPREAD_HINGE_ROUND_M / 2, bend), /span is invalid/);
  assert.throws(() => armSpreadSlope(-0.02, startZM, startZM - SPREAD_HINGE_ROUND_M / 2, bend), /span is invalid/);
  // Where the bend is spent, measured: the station the arm is most deeply buried at is local z -0.086, about 7 cm
  // behind the hinge, where it is 8.6 mm inside the head. A straight shaft has delivered 56% of the tip's offset by
  // then, so the shipped 14 mm leaves 0.8 mm of it; the smoothstep ramp it replaced delivered 46% from 1 cm further
  // back, and needed the tip to stand proud of the head to make that up.
  const atBuriedStation = armSpreadCurve(-0.086, startZM, cutoffZM, bend) / bend;
  assert.ok(Math.abs(atBuriedStation - 0.557) < 0.01, `${atBuriedStation}`);
  assert.ok(bend * atBuriedStation > 0.0077, 'the default bend gets within a millimetre of the head there');
});

test('the pivot is the asset\'s own hinge: where the frame front ends and the shaft begins', () => {
  const slices = (entries: readonly (readonly [number, number])[]): Map<number, {low: number; high: number}> =>
    new Map(entries.map(([slice, height]) => [slice, {low: -height / 2, high: height / 2}]));
  // A frame: a sliver of rim grazes the lateral band at the very front, the rim and endpiece are tall behind it, and
  // the shaft runs thin from there back. The sliver must not be mistaken for the shaft.
  const frame: [number, number][] = [];
  for (let slice = 0; slice >= -4; slice--) frame.push([slice, 0.001 + 0.002 * -slice]);
  for (let slice = -5; slice >= -13; slice--) frame.push([slice, 0.044]);
  for (let slice = -14; slice >= -130; slice--) frame.push([slice, 0.007]);
  assert.equal(armShaftStartZM(slices(frame)), -0.014);
  // A coarsely tessellated shaft has gaps; an empty slice is no evidence, a tall one ends the run.
  assert.equal(armShaftStartZM(slices(frame.filter(([slice]) => slice > -14 || slice % 2 === 0))), -0.014);
  // Nothing thin enough for long enough: the caller keeps the rear drop's own start plane.
  assert.equal(armShaftStartZM(slices(frame.filter(([slice]) => slice > -14))), null);
  // A thin patch a few slices long is not a shaft either: the run has to hold for 20 mm.
  assert.equal(armShaftStartZM(slices([...frame.filter(([slice]) => slice > -14), [-14, 0.007], [-15, 0.007], [-16, 0.007], [-17, 0.044], [-18, 0.044]])), null);
  assert.equal(armShaftStartZM(new Map()), null);
});

for (const [id, cap, hinge, lensRear] of [['amber-horizon', -.140, -0.015, -0.0107], ['tom-ford-clear', -.148, -0.014, -0.0144]] as const) {
  test(`${id}: the hinge is found behind the endpiece, and the bend pivots there instead of 1 cm further back`, async () => {
    const bytes = await readFile(new URL(`../public/models/${id}.glb`, import.meta.url));
    const gltf = await new GLTFLoader().register(() => ({name: 'GeometryOnlyTestTextures', loadTexture: async () => new Texture()}))
      .parseAsync(new Uint8Array(bytes).buffer, '/models/');
    const drop = createRearDrop(gltf.scene, cap);
    try {
      assert.equal(drop.hingeZM, hinge, 'the measured hinge plane of this asset');
      assert.equal(drop.spreadStartZM, hinge, 'and the bend pivots there by default');
      // 4.3 mm behind the lens rear on one frame, 0.4 mm in front of it on the other: the hinge is a property of the
      // frame front, not of the lens, which is why it is measured rather than offset from the lens.
      assert.ok(Math.abs(hinge - lensRear) < 0.005);
      // Well forward of the rear drop's own start, which keeps its guard because it shears the whole arm.
      assert.ok(drop.spreadStartZM - (lensRear - REAR_DROP_PARAMETERS.proximalGuardM) > 0.01);
      // And `?templepivot=` moves it back along the shaft, never forward into the rim.
      for (const pivot of [0.004, 0.012, 0.030]) {
        const moved = createRearDrop(gltf.scene, cap, pivot);
        try {assert.ok(Math.abs(moved.spreadStartZM - (hinge - pivot)) < 1e-12, `${pivot}`);} finally {moved.dispose();}
      }
      assert.throws(() => createRearDrop(gltf.scene, cap, -0.001), /pivot is out of range/);
      assert.throws(() => createRearDrop(gltf.scene, cap, 0.031), /pivot is out of range/);
    } finally {drop.dispose();}
  });
}

test('a bent arm is bent everywhere the pipeline reads it: geometry, centrelines and the protection corridor', () => {
  // The synthetic arms end at z -0.12, so the cap sits inside them as the shipped ones do inside theirs.
  const bend = 0.008, cap = -.11;
  const {root, dispose} = asset(64);
  const model = buildTempleContinuityModel(root, cap);
  const drop = createRearDrop(root, cap);
  const originals = armPositions(root).map(array => array.slice());
  const pivotZM = drop.spreadStartZM;
  try {
    drop.setSpread(bend);
    // The drawn arms: everything at or in front of the pivot is untouched, the tips have opened out by the full bend.
    for (const [mesh, positions] of armPositions(root).entries()) for (let i = 0; i < positions.length; i += 3) {
      const [x0, z0] = [originals[mesh]![i]!, originals[mesh]![i + 2]!];
      if (Math.abs(x0) <= ARM_LATERAL_MIN_M || z0 >= pivotZM) assert.equal(positions[i], x0);
      else assert.ok(Math.abs(positions[i]!) > Math.abs(x0));
    }
    const pose = new Matrix4().makeRotationY(18 * Math.PI / 180).setPosition(0, 0, -40).toArray();
    const input = {eyewearMatrix: pose, offsetCm: GLASSES_OFFSET_CM, sourceAspect: 1.5, width: 1200, height: 800, dropM: .012};
    const bent = projectTempleContinuity(model, {...input, spreadM: bend, spreadStartZM: pivotZM})!;
    const plain = projectTempleContinuity(model, input)!;
    for (const side of [0, 1]) {
      // The cut walks the bent arm, not where the arm used to be. The first station sits just behind the pivot, inside
      // the hinge radius, so it carries a fraction of what the tip carries.
      const front = Math.abs(bent[side]!.points[0]!.x - plain[side]!.points[0]!.x);
      const tip = Math.abs(bent[side]!.points.at(-1)!.x - plain[side]!.points.at(-1)!.x);
      assert.ok(tip > 1 && front < tip / 20, `${side}: front ${front}, tip ${tip}`);
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
    assert.deepEqual(projectTempleContinuity(model, {...input, spreadM: 0, spreadStartZM: pivotZM}), plain);
  } finally {drop.dispose(); dispose();}
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
  // The pivot the drawn arms were actually bent about — not the model's own start plane, which is further back.
  const pivotZM = drop.spreadStartZM;
  assert.ok(pivotZM > model.startZM);
  // The same asset with the fit already baked into its vertices: its own centrelines are the reference.
  const baked = asset(64);
  for (const mesh of baked.root.children) {
    const positions = (mesh as Mesh).geometry.getAttribute('position');
    for (let i = 0; i < positions.count; i++) positions.setX(i, spreadArmX(positions.getX(i), positions.getZ(i), pivotZM, cap, spread));
  }
  const bakedModel = buildTempleContinuityModel(baked.root, cap);
  try {
    const pose = new Matrix4().makeRotationY(18 * Math.PI / 180).setPosition(0, 0, -40).toArray();
    const input = {eyewearMatrix: pose, offsetCm: GLASSES_OFFSET_CM, sourceAspect: 1.5, width: 1200, height: 800, dropM: .015};
    const fitted = projectTempleContinuity(model, {...input, spreadM: spread, spreadStartZM: pivotZM})!;
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
        const moved = spreadArmX(station.centerXM, station.zM, pivotZM, cap, spread);
        const clip = new Vector4(moved, station.centerYM - rearDropCurve(station.zM, model.startZM, cap, input.dropM).loweringM, station.zM, 1).applyMatrix4(projection);
        assert.ok(Math.abs((clip.x / clip.w + 1) * input.width / 2 - fitted[side]!.points[index]!.x) < 1e-9);
      }
      // The first station sits inside the hinge radius behind the pivot, so it barely moves; the tip carries it all.
      const front = Math.abs(fitted[side]!.points[0]!.x - plain[side]!.points[0]!.x);
      assert.ok(front > 0 && front < Math.abs(fitted[side]!.points.at(-1)!.x - plain[side]!.points.at(-1)!.x) / 20);
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
