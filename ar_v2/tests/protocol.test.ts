/**
 * The guided scan protocol.
 *
 * This file exists because of a bug a wearer reported by name — *"the scan gets
 * stuck when asking to look downward"* — and because that bug had **two
 * independent causes**, either of which alone would have made the scan
 * impossible to finish. Both are pinned here.
 *
 *  1. **The euler convention.** `eulerYXZ(pose.R)` extracts the euler of the
 *     model-to-camera rotation, and face space differs from CV camera space by a
 *     rotation of pi about X. So a frontal face reported yaw = -180 and an
 *     inverted pitch, and every beat with a `|yaw| < k` clause was
 *     unsatisfiable. Meanwhile `turn-right` (`yaw <= -30`) was satisfied
 *     instantly, without the wearer turning at all.
 *
 *  2. **Absolute pitch thresholds.** Even with the angles correct, asking for
 *     `pitch >= 12` to mean "look down" only works if the camera is at eye
 *     level. This is v1's laptop-lid failure exactly — it scored *0 of 600
 *     admitted frames* for a camera 13.5 degrees below the eyes, because its
 *     square-on test was an absolute cone about the camera axis. Its own note is
 *     the lesson: **a camera is not anybody's eye level.**
 *
 * The second is the more interesting failure, because the first would have been
 * caught by any real session and the second only shows up on some hardware.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import {
  eulerYXZ, m3, mat3FromEulerYXZ, poseIdentity, type Pose,
} from '../src/core/linalg.js';
import { headEuler } from '../src/core/camera.js';
import {
  BEATS, advanceProtocol, createProtocol, sampleFromPose,
} from '../src/enroll/protocol.js';
import { CAMERA_LADDER } from '../src/testkit/synthetic.js';

const DEG = 180 / Math.PI;

/**
 * A pose for a wearer doing what they were asked, at a given camera geometry.
 *
 * `basePitchDeg` is the tilt a camera below the eyes induces. To the tracker it
 * is head pitch, indistinguishable from the wearer looking down — which is the
 * whole difficulty, and why every beat after the first is measured relative to
 * the wearer's own neutral.
 */
function poseFor(
  yawDeg: number, pitchDeg: number, basePitchDeg: number, distanceMm: number,
): Pose {
  const pose = poseIdentity();
  const R = m3();
  mat3FromEulerYXZ(R, yawDeg / DEG, (pitchDeg + basePitchDeg) / DEG, 0);
  // Face space (+Y up, +Z out of the face) to camera space (+Y down, +Z
  // forward) is a rotation of pi about X.
  const flip = Float64Array.of(1, 0, 0, 0, -1, 0, 0, 0, -1);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      pose.R[r * 3 + c] =
        R[r * 3] * flip[c] + R[r * 3 + 1] * flip[3 + c] + R[r * 3 + 2] * flip[6 + c];
    }
  }
  pose.t.set([0, 0, distanceMm]);
  return pose;
}

const basePitchOf = (geometry: typeof CAMERA_LADDER[number]): number =>
  Math.atan2(geometry.belowEyesMm, geometry.distanceMm) * DEG;

describe('head angles', () => {
  it('headEuler reports the wearer, not the camera frame', () => {
    const frontal = headEuler(poseFor(0, 0, 0, 500));
    assert.ok(Math.abs(frontal.yaw * DEG) < 0.01, `frontal yaw ${frontal.yaw * DEG}`);
    assert.ok(Math.abs(frontal.pitch * DEG) < 0.01, `frontal pitch ${frontal.pitch * DEG}`);
    assert.ok(Math.abs(frontal.roll * DEG) < 0.01, `frontal roll ${frontal.roll * DEG}`);
  });

  it('pins the sign of every axis', () => {
    // Named directions, because "pitch" alone has burned this project once.
    assert.ok(headEuler(poseFor(0, 20, 0, 500)).pitch * DEG > 19, 'looking DOWN is positive pitch');
    assert.ok(headEuler(poseFor(0, -20, 0, 500)).pitch * DEG < -19, 'looking UP is negative pitch');
    assert.ok(headEuler(poseFor(35, 0, 0, 500)).yaw * DEG > 34, 'turning to your LEFT is positive yaw');
    assert.ok(headEuler(poseFor(-35, 0, 0, 500)).yaw * DEG < -34, 'turning to your RIGHT is negative yaw');
  });

  it('the raw camera-frame euler must disagree, or the flip has gone missing', () => {
    // If this ever fails, the model-to-camera flip has been removed somewhere
    // upstream and `headEuler` has quietly become a no-op.
    const raw = eulerYXZ(poseFor(0, 0, 0, 500).R);
    assert.ok(
      Math.abs(Math.abs(raw.yaw * DEG) - 180) < 0.01,
      `the raw euler should report a frontal face at +/-180 yaw, got ${raw.yaw * DEG}`,
    );
    assert.ok(
      eulerYXZ(poseFor(0, 20, 0, 500).R).pitch * DEG < 0,
      'the raw euler should invert the sign of pitch',
    );
  });
});

describe('the guided scan', () => {
  it('a cooperative wearer completes it at every camera geometry', () => {
    // The reported bug, as a test. A wearer who follows every prompt must reach
    // the end — on a laptop lid and a phone in the lap as well as at eye level.
    for (const geometry of CAMERA_LADDER) {
      const basePitchDeg = basePitchOf(geometry);
      const state = createProtocol();

      // What a cooperative wearer does, RELATIVE to their own neutral — which is
      // the only thing a person actually controls.
      const script: Record<string, { yaw: number; pitch: number; scale: number }> = {
        centre: { yaw: 0, pitch: 0, scale: 1 },
        'turn-right': { yaw: -38, pitch: 0, scale: 1 },
        'profile-right': { yaw: -72, pitch: 0, scale: 1 },
        'turn-left': { yaw: 38, pitch: 0, scale: 1 },
        'profile-left': { yaw: 72, pitch: 0, scale: 1 },
        'nod-down': { yaw: 0, pitch: 16, scale: 1 },
        'nod-up': { yaw: 0, pitch: -16, scale: 1 },
        'lean-in': { yaw: 0, pitch: 0, scale: 0.7 },
        'lean-back': { yaw: 0, pitch: 0, scale: 1.35 },
      };

      for (let guard = 0; guard < 4000 && !state.finished; guard++) {
        const current = BEATS[state.index];
        if (!current) break;
        const move = script[current.id] ?? { yaw: 0, pitch: 0, scale: 1 };
        const pose = poseFor(move.yaw, move.pitch, basePitchDeg, geometry.distanceMm * move.scale);
        advanceProtocol(state, sampleFromPose(pose, state.neutral));
      }

      assert.ok(
        state.finished,
        `${geometry.name}: never finished (stuck on ${BEATS[state.index]?.id ?? '?'}, ` +
        `done: ${state.done.join(',')})`,
      );
      assert.equal(
        state.skipped.length, 0,
        `${geometry.name}: skipped ${state.skipped.join(', ')} for a wearer who did everything asked`,
      );
      assert.ok(state.neutral, `${geometry.name}: no neutral was established`);
      // The neutral must absorb the camera's own tilt rather than fight it.
      assert.ok(
        Math.abs(state.neutral!.pitchDeg - basePitchDeg) < 2,
        `${geometry.name}: neutral pitch ${state.neutral!.pitchDeg.toFixed(1)} should be ` +
        `the camera's own ${basePitchDeg.toFixed(1)}`,
      );
    }
  });

  it('measures the nod relative to neutral, not to the camera axis', () => {
    // A phone in the lap sits ~30 degrees below the eyes, so an absolute
    // `pitch >= 12` for "look down" is satisfied before the wearer moves, and
    // `pitch <= -12` for "look up" would need them to crane 42 degrees.
    const basePitchDeg = 30;
    const state = createProtocol();
    const neutralPose = poseFor(0, 0, basePitchDeg, 500);
    for (let i = 0; i < 60 && state.index === 0; i++) {
      advanceProtocol(state, sampleFromPose(neutralPose, state.neutral));
    }
    assert.ok(state.neutral, 'the opening beat never completed at 30 degrees of camera tilt');
    assert.ok(Math.abs(state.neutral!.pitchDeg - basePitchDeg) < 2);

    const nodDown = BEATS.find((b) => b.id === 'nod-down')!;
    const nodUp = BEATS.find((b) => b.id === 'nod-up')!;
    const at = (pitch: number) => sampleFromPose(poseFor(0, pitch, basePitchDeg, 500), state.neutral);

    assert.ok(!nodDown.satisfied(at(0), state.neutral), 'sitting still must not count as looking down');
    assert.ok(nodDown.satisfied(at(14), state.neutral), 'looking down 14 degrees must count');
    assert.ok(!nodUp.satisfied(at(0), state.neutral), 'sitting still must not count as looking up');
    assert.ok(nodUp.satisfied(at(-14), state.neutral), 'looking up 14 degrees must count');
  });

  it('gives up rather than stalling when the wearer will not move', () => {
    // A scan must never hang. A wearer who cannot or will not turn gets a
    // shorter scan and an honestly-degraded model, not a spinner.
    const state = createProtocol();
    const stubborn = poseFor(0, 0, 0, 500);
    for (let i = 0; i < 8000 && !state.finished; i++) {
      advanceProtocol(state, sampleFromPose(stubborn, state.neutral));
    }
    assert.ok(state.finished, 'the protocol stalled on a wearer who never moved');
    assert.ok(state.done.includes('centre'), 'the opening beat should still have completed');
    assert.ok(state.skipped.length >= 6, `only skipped ${state.skipped.length} beats`);
  });

  it('still establishes a neutral even if the opening beat is skipped', () => {
    // Otherwise every later beat is measured against zero — i.e. against the
    // camera axis, which is the bug this whole mechanism exists to avoid.
    const state = createProtocol();
    // A wearer who is turned well away from the camera the whole time: the
    // opening beat can never be satisfied.
    const turned = poseFor(45, 0, 0, 500);
    for (let i = 0; i < 8000 && !state.finished; i++) {
      advanceProtocol(state, sampleFromPose(turned, state.neutral));
    }
    assert.ok(state.skipped.includes('centre'), 'the opening beat should have been skipped');
    assert.ok(state.neutral, 'no neutral was adopted after skipping the opening beat');
    assert.ok(
      Math.abs(state.neutral!.yawDeg - 45) < 2,
      `neutral yaw ${state.neutral!.yawDeg.toFixed(1)} should be the pose actually seen`,
    );
  });
});
