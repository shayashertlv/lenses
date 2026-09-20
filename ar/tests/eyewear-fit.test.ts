import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {Euler, Matrix4, Vector3} from 'three';
import type {Landmark} from '../src/face/protocol.ts';
import {EYEWEAR_FIT, EyewearFitSession, observeFitWidth} from '../src/render/eyewear-fit.ts';
import {SHIPPED_EYEWEAR} from '../src/eyewear/catalog.ts';

const canonical: {positions: number[]} = JSON.parse(await readFile(new URL('../public/models/canonical-face.json', import.meta.url), 'utf8'));
const close = (actual: number, expected: number, tolerance = 1e-8): void => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} differs from ${expected}`);
const collect = (fit: EyewearFitSession, width = 14, start = 0): void => {
  for (let t = start; t <= start + 2000; t += 50) fit.observe({faceWidthCm: width}, t);
};

function projectedFixture({depth = 45, yaw = 0, pitch = 0, roll = 0, faceScale = 1, frontWidth = 14.5} = {}) {
  const aspect = 16 / 9, tangent = Math.tan(63 * Math.PI / 360);
  const matrix = new Matrix4().makeRotationFromEuler(new Euler(pitch * Math.PI / 180, yaw * Math.PI / 180, roll * Math.PI / 180, 'YXZ')).setPosition(0, 0, -depth);
  const project = (point: Vector3): Landmark => {
    point.applyMatrix4(matrix);
    return {x: .5 + point.x / (-point.z * tangent * aspect * 2), y: .5 - point.y / (-point.z * tangent * 2), z: point.z};
  };
  const landmarks: Landmark[] = [], observedPositions: number[] = [];
  for (let i = 0; i < canonical.positions.length; i += 3) {
    const point = new Vector3().fromArray(canonical.positions, i); point.x *= faceScale;
    observedPositions.push(...point.clone().applyMatrix4(matrix).toArray());
    landmarks.push(project(point));
  }
  const a = project(new Vector3(-frontWidth / 2, 3.27, 6.53)), b = project(new Vector3(frontWidth / 2, 3.27, 6.53));
  return {landmarks, observedPositions, rawMatrix: matrix.toArray(), baselineFrontWidth: Math.abs(b.x - a.x), frontWidthCm: frontWidth,
    canonicalPositions: canonical.positions, sourceAspect: aspect};
}

test('visual calibration uses elapsed valid time across frame rates, then scale settling completes', () => {
  for (const fps of [10, 20, 30, 60]) {
    const fit = new EyewearFitSession(); let lockedAt: number | null = null;
    for (let frame = 0; frame <= fps * 4; frame++) {
      const t = frame * 1000 / fps;
      fit.observe({faceWidthCm: 14 + .005 * Math.sin(t / 130)}, t);
      const scale = fit.scaleFor(14.5, .98, t);
      if (fit.report.faceWidthCm === null) assert.equal(scale, 1, 'collecting cannot resize the baseline frame');
      else if (lockedAt === null) lockedAt = t;
    }
    assert.ok(lockedAt !== null && lockedAt >= 1800 && lockedAt <= 2000, `${fps}fps locks in roughly two seconds: ${lockedAt}`);
    assert.equal(fit.report.state, 'fitted'); assert.equal(fit.report.progress, 1);
    close(fit.report.faceWidthCm!, 14, .003);
    close(fit.scaleFor(14.5, .98, 4100), fit.report.faceWidthCm! * .98 / 14.5 * 1.08);
  }
});

test('duplicate, backwards and invalid timestamps cannot accumulate calibration or advance an audit', () => {
  const fit = new EyewearFitSession();
  fit.observe({faceWidthCm: 14}, 100);
  for (let i = 0; i < 100; i++) fit.observe({faceWidthCm: 14}, [100, 50, NaN, Infinity, -10][i % 5]!);
  assert.equal(fit.report.faceWidthCm, null); assert.equal(fit.report.progress, 0);
  collect(fit, 13, 200); fit.scaleFor(14.5, .98, 2200);
  const eased = fit.scaleFor(14.5, .98, 2250);
  assert.ok(eased < 1 && eased > 13 * .98 / 14.5 * 1.08);
  for (let i = 0; i < 50; i++) assert.equal(fit.scaleFor(14.5, .98, 2250), eased);
  assert.equal(fit.scaleFor(14.5, .98, 2200), eased);
});

test('isolated width outliers and rejected observations cannot poison a stable median', () => {
  const fit = new EyewearFitSession();
  for (let i = 0; i < 100; i++) {
    const sample = i > 5 && i % 9 === 0 ? {faceWidthCm: 21} : {faceWidthCm: 14 + (i % 2 ? .02 : -.02)};
    fit.observe(sample, i * 50);
  }
  close(fit.report.faceWidthCm!, 14, .02); assert.equal(fit.report.progress, 1);
  const unstable = new EyewearFitSession();
  for (let i = 0; i < 80; i++) unstable.observe({faceWidthCm: i % 2 ? 14.25 : 13.75}, i * 50);
  assert.equal(unstable.report.faceWidthCm, null); assert.equal(unstable.report.rejection, 'Keep your head still.');
  for (let i = 80; i < 150; i++) unstable.observe({faceWidthCm: 14}, i * 50);
  assert.equal(unstable.report.faceWidthCm, 14, 'a stable recent window can replace noisy initial observations');
});

test('disjoint short glimpses cannot satisfy the valid-time requirement', () => {
  const fit = new EyewearFitSession();
  for (let block = 0; block < 4; block++) {
    for (let i = 0; i < 15; i++) fit.observe({faceWidthCm: 14}, block * 3000 + i * 50);
    fit.observe({faceWidthCm: NaN, rejection: 'Look straight ahead.'}, block * 3000 + 800);
    assert.equal(fit.report.faceWidthCm, null);
  }
  collect(fit, 14, 13000); assert.equal(fit.report.faceWidthCm, 14);
});

test('the locked wearer profile survives tracking loss and frame changes until an explicit smooth reset', () => {
  const fit = new EyewearFitSession(); collect(fit, 13);
  for (let t = 0; t <= 3000; t += 50) fit.scaleFor(14.5, .98, t);
  const baseline = fit.scaleFor(14.5, .98, 3050);
  fit.observe({faceWidthCm: NaN, rejection: 'Waiting for a clear face.'}, 9000);
  fit.observe({faceWidthCm: 18}, 9500);
  assert.equal(fit.report.faceWidthCm, 13); assert.equal(fit.report.state, 'fitted');
  assert.equal(fit.scaleFor(14.5, .98, 9500), baseline);
  fit.setAdjustment(.08);
  const first = fit.scaleFor(15, .96, 9550);
  assert.ok(first > baseline && first < 13 * .96 / 15 * 1.08 * 1.08, 'switching frame dimensions shares the profile and eases to its own target');
  for (let t = 9600; t <= 12000; t += 50) fit.scaleFor(15, .96, t);
  const beforeReset = fit.scaleFor(15, .96, 12050);
  fit.reset(); assert.deepEqual(fit.report, {state: 'collecting', progress: 0, faceWidthCm: null, adjustment: 0, rejection: null, limited: false});
  assert.equal(fit.scaleFor(15, .96, 12050), beforeReset, 'reset cannot advance a held frame');
  const next = fit.scaleFor(15, .96, 12100);
  assert.ok(next > beforeReset && next < 1, 'reset returns gently toward the baseline');
});

test('automatic resizing and manual adjustment remain independently bounded', () => {
  for (const [width, automatic, adjusted, limited] of [[10, .918, .99144, false], [22, 1.242, 1.242, true]] as const) {
    const fit = new EyewearFitSession(); collect(fit, width!);
    for (let t = 0; t <= 4000; t += 50) fit.scaleFor(14.5, 1, t);
    close(fit.scaleFor(14.5, 1, 4050), automatic);
    assert.equal(fit.report.limited, false, 'the preferred automatic size fits within the geometry range');
    fit.setAdjustment(5); assert.equal(fit.report.adjustment, .08);
    for (let t = 4100; t <= 7000; t += 50) fit.scaleFor(14.5, 1, t);
    close(fit.scaleFor(14.5, 1, 7050), adjusted);
    assert.equal(fit.report.limited, limited, 'a manual request beyond the geometry limit is reported');
    assert.throws(() => fit.setAdjustment(NaN)); assert.equal(fit.report.adjustment, .08);
    fit.setAdjustment(-5); assert.equal(fit.report.adjustment, -.08);
    for (let t = 7100; t <= 10000; t += 50) fit.scaleFor(14.5, 1, t);
    close(fit.scaleFor(14.5, 1, 10050), automatic * .92);
    assert.equal(fit.report.limited, false, 'reducing the requested size clears the limit');
  }
});

test('the preferred default exactly matches the former +8% fit below, within and above the automatic clamp', () => {
  assert.equal(EYEWEAR_FIT.defaultFitPreference, 1.08);
  for (const {width, oldAutomatic} of [
    {width: 10, oldAutomatic: .85},
    {width: 14.5, oldAutomatic: 1},
    {width: 22, oldAutomatic: 1.15},
  ]) {
    const fit = new EyewearFitSession();
    for (let t = 0; t <= 2000; t += 50) assert.equal(fit.scaleFor(14.5, 1, t), 1, 'preference cannot resize an uncalibrated frame');
    collect(fit, width, 2050);
    for (let t = 4050; t <= 7050; t += 50) fit.scaleFor(14.5, 1, t);
    close(fit.scaleFor(14.5, 1, 7100), oldAutomatic * 1.08);
    assert.equal(fit.report.adjustment, 0, 'the new default is centered on the slider');
    assert.equal(fit.report.limited, false);
  }
});

test('refit clears an explicitly limited manual request while preserving a smooth scale transition', () => {
  const fit = new EyewearFitSession(); collect(fit, 22); fit.setAdjustment(.08);
  for (let t = 0; t <= 3000; t += 50) fit.scaleFor(14.5, 1, t);
  const capped = fit.scaleFor(14.5, 1, 3050);
  close(capped, 1.242); assert.equal(fit.report.limited, true);
  fit.reset();
  assert.equal(fit.report.limited, false); assert.equal(fit.report.adjustment, 0);
  assert.equal(fit.scaleFor(14.5, 1, 3050), capped);
  const first = fit.scaleFor(14.5, 1, 3100);
  assert.ok(first < capped && first > 1, 'reset eases toward the uncalibrated baseline');
});

test('upper-face observations survive ordinary distance and admitted tilt, and remain independent of frame choice', () => {
  const near = observeFitWidth(projectedFixture({depth: 35})), far = observeFitWidth(projectedFixture({depth: 60}));
  assert.equal(near.rejection, null); assert.equal(far.rejection, null);
  close(near.faceWidthCm, far.faceWidthCm, 1e-8);
  const ordinary = observeFitWidth(projectedFixture({pitch: 13.8, yaw: 10}));
  assert.equal(ordinary.rejection, null, 'the real fixture pose is comfortably inside the quality gate');
  close(ordinary.faceWidthCm, near.faceWidthCm, 1e-8);
  const tiltedFar = observeFitWidth(projectedFixture({depth: 60, pitch: -12, yaw: -14, roll: 8}));
  assert.equal(tiltedFar.rejection, null); close(tiltedFar.faceWidthCm, near.faceWidthCm, 1e-8);
  const narrowFrame = observeFitWidth(projectedFixture({frontWidth: 12})), wideFrame = observeFitWidth(projectedFixture({frontWidth: 17}));
  close(narrowFrame.faceWidthCm, wideFrame.faceWidthCm, 1e-8);
  for (const eyewear of Object.values(SHIPPED_EYEWEAR)) assert.ok(eyewear.preferredFrontRatio > .9 && eyewear.preferredFrontRatio <= 1);
});

test('pose, clipped landmarks, collapsed eyes and inconsistent upper-side groups are rejected', () => {
  for (const options of [{yaw: 24}, {pitch: 28}, {roll: 20}]) assert.equal(observeFitWidth(projectedFixture(options)).rejection, 'Look straight ahead.');
  const clipped = projectedFixture(); clipped.landmarks[127]!.x = 0;
  assert.equal(observeFitWidth(clipped).rejection, 'Move fully into view.');
  const eye = projectedFixture(); eye.landmarks[133] = {...eye.landmarks[33]!};
  assert.ok(observeFitWidth(eye).rejection);
  const side = projectedFixture();
  for (const index of [21, 162]) side.landmarks[index]!.x -= .07;
  for (const index of [251, 389]) side.landmarks[index]!.x += .07;
  assert.ok(observeFitWidth(side).rejection);
  const baseline = projectedFixture(); baseline.baselineFrontWidth = 0;
  assert.ok(observeFitWidth(baseline).rejection);
});

test('session observations are returned by value and acceptance rejects impossible width values', () => {
  const fit = new EyewearFitSession();
  for (const [i, width] of [0, NaN, Infinity, EYEWEAR_FIT.maximumWidthCm + 1].entries()) fit.observe({faceWidthCm: width}, i * 100);
  assert.equal(fit.report.progress, 0);
  const report = fit.report; report.adjustment = 1; report.progress = 1;
  assert.equal(fit.report.adjustment, 0); assert.equal(fit.report.progress, 0);
});
