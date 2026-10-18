/** temple_reach (qa/capture/temple-reach.ts): how far back the near arm is drawn in a turned part-id render, and the
 *  TEMPLE CUT warning when the face/head occluders hide it ahead of where its fixed return behind the ear begins. The
 *  numbers mirror the WP1 proof's Prada delivery at yaw +45 (before: drawn to z -61 mm; after the spread-pivot fix:
 *  z -76 mm; the return begins at z -75 mm, the cap at z -115 mm). */
import assert from 'node:assert/strict';
import test from 'node:test';
import {measureTempleReach, nearSideOfYaw, TEMPLE_REACH, templeReachWarning} from './capture/temple-reach.ts';

const W = 200, H = 40, ARM = 3, OTHER = 5;
/** 33 stations from z -19.6 mm to the clip at -140 mm, laid out left to right along row 20 at 1.5 px per mm. */
function arm() {
  const n = 33, centreline = new Float32Array(n * 3), projected = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const z = -0.0196 + (-0.140 + 0.0196) * i / (n - 1);
    centreline.set([-0.065, 0.004, z], i * 3);
    projected.set([10 + (-z * 1000 - 19.6) * 1.5, 20, 0.5], i * 3);
  }
  return {centreline, projected};
}
/** A part-id label image with the arm painted from its hinge back to `toZmm` (row 20, 3 px tall). */
function labels(toZmm, paint = ARM) {
  const out = new Int16Array(W * H).fill(-1);
  const x1 = Math.round(10 + (-toZmm - 19.6) * 1.5);
  for (let y = 19; y <= 21; y++) for (let x = 0; x <= Math.min(W - 1, x1); x++) out[y * W + x] = paint;
  return out;
}
const base = {side: 0, yawDegrees: 45, width: W, height: H, armLabels: new Set([ARM]), returnStartZM: -0.075, capZM: -0.115};

test('an arm hidden in the MIDDLE and drawn again behind is a cut too (WP1 review E/F: the cheek can hide the middle)', () => {
  // The reviewer's repro (scratchpad review_e_f/reach_gap.ts): 20 stations from z -10 to -105 mm, hidden from -40 to -70,
  // drawn again from -75 to -105: the end shortfall is 0, but 35 mm ahead of the return start (-75 mm) are not drawn.
  const n = 20, width = 120, height = 3, centreline = [], projected = [], gap = new Int16Array(width * height).fill(-1);
  for (let i = 0; i < n; i++) {
    const z = -0.010 - i * 0.005, x = 10 + i * 5;
    centreline.push(-0.065, 0, z); projected.push(x, 1, 0.5);
    if (!(z <= -0.040 + 1e-9 && z >= -0.070 - 1e-9)) for (let y = 0; y < height; y++) gap[y * width + x] = 1;
  }
  const reach = measureTempleReach({side: 0, yawDegrees: 45, centreline, projected, labels: gap, width, height,
    armLabels: new Set([1]), returnStartZM: -0.075, capZM: -0.115});
  assert.equal(reach.hidden_before_return_mm, 0, 'the end is drawn');
  assert.equal(reach.longest_hidden_run_mm, 35); assert.equal(reach.longest_hidden_run_from_z_mm, -40);
  assert.equal(reach.cut, true);
  assert.match(templeReachWarning('yaw_p45__part-id.png', reach),
    /^yaw_p45__part-id\.png: TEMPLE CUT: the near \(wearer's right, -X\) temple is hidden for 35 mm from z -40 mm, ahead of where its fixed return behind the ear begins \(z -75 mm\), and drawn again behind/);
  // One hidden station (a 5 mm gap: within the tolerance) is not a cut.
  const one = gap.slice(); one.fill(-1);
  for (let i = 0; i < n; i++) if (i !== 8) for (let y = 0; y < height; y++) one[y * width + 10 + i * 5] = 1;
  const single = measureTempleReach({side: 0, yawDegrees: 45, centreline, projected, labels: one, width, height,
    armLabels: new Set([1]), returnStartZM: -0.075, capZM: -0.115});
  assert.equal(single.longest_hidden_run_mm, 5); assert.equal(single.cut, false);
  // A run that reaches past the return start is clamped there (the arm may vanish behind it by design).
  const late = gap.slice(); late.fill(-1);
  for (let i = 0; i < n; i++) {const z = -0.010 - i * 0.005; if (z > -0.0699 || z < -0.0851) for (let y = 0; y < height; y++) late[y * width + 10 + i * 5] = 1;}
  const clamped = measureTempleReach({side: 0, yawDegrees: 45, centreline, projected, labels: late, width, height,
    armLabels: new Set([1]), returnStartZM: -0.075, capZM: -0.115});
  assert.equal(clamped.longest_hidden_run_mm, 5, 'from -70 to the return start at -75'); assert.equal(clamped.cut, false);
});

test('only turned views are measured, and the near arm is the one facing the camera', () => {
  assert.equal(nearSideOfYaw(0), null); assert.equal(nearSideOfYaw(-19.9), null); assert.equal(nearSideOfYaw(Number.NaN), null);
  assert.equal(nearSideOfYaw(TEMPLE_REACH.minYawDegrees), 0, 'yaw > 0 shows the asset -X side (poses.ts)');
  assert.equal(nearSideOfYaw(-45), 1);
});

test('the Prada cut (drawn to z -61 mm, the return begins at -75 mm) is a TEMPLE CUT warning, never silent', () => {
  const reach = measureTempleReach({...base, ...arm(), labels: labels(-61)});
  assert.equal(reach.cut, true);
  assert.ok(Math.abs(reach.drawn_to_z_mm + 61) < 4, `drawn to ${reach.drawn_to_z_mm}`);
  assert.ok(reach.hidden_before_return_mm > 10 && reach.hidden_before_return_mm < 16, `${reach.hidden_before_return_mm}`);
  assert.equal(reach.return_start_z_mm, -75); assert.equal(reach.cap_z_mm, -115);
  assert.ok(reach.stations === 26 && reach.drawn_stations < reach.stations, 'stations behind the cap are not counted');
  const warning = templeReachWarning('yaw_p45__part-id.png', reach);
  assert.match(warning, /^yaw_p45__part-id\.png: TEMPLE CUT: the near \(wearer's right, -X\) temple is drawn only to z -6\d(\.\d)? mm, 1\d(\.\d)? mm short of where its fixed return behind the ear begins \(z -75 mm\)/);
});

test('an arm drawn to where its return begins (the fixed Prada, z -76 mm) or to its cap is not cut', () => {
  for (const to of [-76, -100, -115, -140]) {
    const reach = measureTempleReach({...base, ...arm(), labels: labels(to)});
    assert.equal(reach.cut, false, `${to}`); assert.equal(reach.hidden_before_return_mm, 0);
    assert.equal(templeReachWarning('x.png', reach), null);
  }
  // Within the tolerance (one station plus the clip fade) is not a cut either.
  assert.equal(measureTempleReach({...base, ...arm(), labels: labels(-72)}).cut, false);
  // The stations behind the return start that read as drawn are counted apart (never judged).
  const {centreline, projected} = arm();
  for (const to of [-72, -76, -100, -140]) {
    // A station reads as drawn when the paint (to column x1) is within 1 px of its rounded projected centre.
    const x1 = Math.round(10 + (-to - 19.6) * 1.5);
    let expected = 0;
    for (let i = 0; i < centreline.length / 3; i++) {
      const z = centreline[i * 3 + 2] * 1000;
      if (z < -75 && z >= -115 && Math.round(projected[i * 3]) - 1 <= x1) expected++;
    }
    assert.equal(measureTempleReach({...base, ...arm(), labels: labels(to)}).behind_return_drawn_stations, expected, `${to}`);
  }
});

test('a curled return projected back onto the arm\'s own nearer part reads as drawn: counted apart, never judged (WP1 closeout, Prada yaw 30)', () => {
  // The arm is painted from its hinge to the return start (-75 mm); behind it the return curls inward and its stations
  // project back onto pixels the nearer arm already covers, as the Prada PR17WS's do onto its shield at yaw ±30.
  const {centreline, projected} = arm();
  const xOf = zMm => 10 + (-zMm - 19.6) * 1.5;
  for (let i = 0; i < centreline.length / 3; i++) {
    const z = centreline[i * 3 + 2] * 1000;
    if (z < -75) projected[i * 3] = xOf(-75 + (z + 75) * -0.5);   // folds back toward the hinge
  }
  const reach = measureTempleReach({...base, centreline, projected, labels: labels(-75)});
  assert.ok(reach.drawn_to_z_mm <= -110, `the folded stations read as drawn: ${reach.drawn_to_z_mm}`);
  assert.equal(reach.behind_return_drawn_stations, 11, 'every station from -76 to -113.7 (the last ahead of the cap at -115) is behind the return start');
  assert.equal(reach.hidden_before_return_mm, 0); assert.equal(reach.cut, false);
});

test('another part painted where the arm should be does not count as the arm; nothing drawn is the whole arm hidden', () => {
  const reach = measureTempleReach({...base, ...arm(), labels: labels(-140, OTHER)});
  assert.equal(reach.drawn_to_z_mm, null); assert.equal(reach.drawn_stations, 0); assert.equal(reach.cut, true);
  assert.ok(Math.abs(reach.first_hidden_z_mm + 19.6) < 0.1);
  assert.ok(Math.abs(reach.hidden_before_return_mm - 55.4) < 0.1, 'from the first station to the return start');
  assert.match(templeReachWarning('y.png', reach), /drawn only to nowhere \(no station of it is drawn\)/);
});

test('without a terminal fit the arm should reach its cap; stations behind the camera are not drawn; bad input throws', () => {
  const reach = measureTempleReach({...base, ...arm(), labels: labels(-90), returnStartZM: null});
  assert.equal(reach.cut, true); assert.ok(reach.hidden_before_return_mm > 20);
  assert.match(templeReachWarning('z.png', reach), /short of its cap \(z -115 mm\)/);
  const {centreline, projected} = arm(); projected.fill(Number.NaN);
  assert.equal(measureTempleReach({...base, centreline, projected, labels: labels(-140)}).drawn_stations, 0);
  assert.throws(() => measureTempleReach({...base, centreline, projected: projected.subarray(3), labels: labels(-140)}), /differ in length/);
  assert.throws(() => measureTempleReach({...base, ...arm(), labels: new Int16Array(5)}), /width x height/);
});
