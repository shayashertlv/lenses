/**
 * The guided scan protocol, and the head-angle convention it stands on.
 *
 * ## Why this file is written the way it is
 *
 * A wearer reported the scan getting stuck twice. The second time, the cause was
 * that `headEuler` returned the **conjugate** of the head's rotation — yaw and
 * roll negated — because it right-multiplied the face-to-camera flip where it
 * should have left-multiplied.
 *
 * Fifty-six tests passed over that bug. They passed because the synthetic
 * capture generator made the *same* mistake, composing `R_head * FLIP` instead
 * of `FLIP * R_head`. The two errors cancel exactly, so the entire synthetic
 * pipeline was self-consistent — and wrong together. Every test that checked
 * "the angle I put in comes back out" was checking a round trip through two
 * inverse bugs.
 *
 * That is v1's own lesson arriving in the worst possible form: *a check that
 * cannot fail is not a check.* The fix is not more tests of the same shape. It
 * is to ground the expected answer in something **observable** — where landmarks
 * actually land in the image — which no amount of self-consistent convention
 * error can satisfy.
 *
 * So the sign tests below never say "I built a 30-degree turn, so it should read
 * 30 degrees". They say:
 *
 *   1. On the template mesh, the wearer's right temple is at negative x. That is
 *      data in the file, not a convention.
 *   2. An unmirrored camera puts the wearer's right side on the image LEFT. That
 *      is a fact about cameras, and it pins what a frontal pose must be.
 *   3. Turning right means the nose swings toward the right temple's side. That
 *      is what the words mean.
 *   4. Therefore the right half of the face must foreshorten in the image, and
 *      `headEuler` must report a negative yaw.
 *
 * Steps 1 to 3 are checked directly. Step 4 is the consequence under test.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import {
  eulerYXZ, m3, poseIdentity, type Mat3, type Pose,
} from '../src/core/linalg.js';
import {
  FACE_TO_CAMERA_FLIP, headEuler, intrinsicsFromFov, poseRotationFromHeadEuler, project,
} from '../src/core/camera.js';
import { LM } from '../src/core/mesh.js';
import {
  BEATS, achievedTurnDeg, advanceProtocol, createProtocol, sampleFromPose, summarise,
} from '../src/enroll/protocol.js';
import { CAMERA_LADDER } from '../src/testkit/synthetic.js';
import { loadTemplateMesh } from '../src/testkit/fixtures.js';

const DEG = 180 / Math.PI;
const mesh = loadTemplateMesh();
const K = intrinsicsFromFov(1280, 720, 63);

/** Row-major 3x3 multiply. Spelled out so no helper's convention is assumed. */
function mul(out: Mat3, A: ArrayLike<number>, B: ArrayLike<number>): Mat3 {
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      out[r * 3 + c] = A[r * 3] * B[c] + A[r * 3 + 1] * B[3 + c] + A[r * 3 + 2] * B[6 + c];
    }
  }
  return out;
}

/** Projects a mesh landmark through a pose. Returns [x, y] in pixels. */
function projectLandmark(pose: Pose, index: number): [number, number] {
  const p = mesh.positions;
  const x = p[index * 3], y = p[index * 3 + 1], z = p[index * 3 + 2];
  const R = pose.R;
  const cam = Float64Array.of(
    R[0] * x + R[1] * y + R[2] * z + pose.t[0],
    R[3] * x + R[4] * y + R[5] * z + pose.t[1],
    R[6] * x + R[7] * y + R[8] * z + pose.t[2],
  );
  const px = new Float64Array(2);
  assert.ok(project(px, K, cam), `landmark ${index} projected behind the camera`);
  return [px[0], px[1]];
}

// -------------------------------------------------------- grounding the axes

describe('the head-angle convention, grounded in observables', () => {
  it('1. the template puts the wearer\'s right temple at negative x', () => {
    // Data in the file, not a convention anyone chose. Everything else hangs
    // off this.
    assert.ok(
      mesh.positions[LM.TEMPLE_R * 3] < 0,
      'TEMPLE_R should be at negative mesh x',
    );
    assert.ok(mesh.positions[LM.TEMPLE_L * 3] > 0, 'TEMPLE_L should be at positive mesh x');
  });

  it('2. a frontal pose puts the wearer\'s right side on the IMAGE LEFT', () => {
    // A fact about cameras: an unmirrored self-view looks wrong precisely
    // because your right hand appears on the left of the picture. This is what
    // pins the frontal pose, and therefore what pins FACE_TO_CAMERA_FLIP.
    const frontal = poseIdentity();
    frontal.R.set(FACE_TO_CAMERA_FLIP);
    frontal.t.set([0, 0, 500]);

    const right = projectLandmark(frontal, LM.TEMPLE_R);
    const left = projectLandmark(frontal, LM.TEMPLE_L);
    assert.ok(
      right[0] < left[0],
      `the wearer's right temple projected to x=${right[0].toFixed(0)}, their left to ` +
      `${left[0].toFixed(0)} — right must be further LEFT in an unmirrored image`,
    );

    // And the top of the head must be ABOVE the chin in image coordinates
    // (+Y is down in CV convention).
    const forehead = projectLandmark(frontal, LM.FOREHEAD);
    const chin = projectLandmark(frontal, LM.CHIN);
    assert.ok(forehead[1] < chin[1], 'the forehead must project above the chin');
  });

  it('3. turning right swings the nose toward the right temple\'s side', () => {
    // What the words mean, expressed against the mesh's own geometry.
    // A rotation about the model Y axis, built by hand.
    const theta = 30 / DEG;
    const turnRightInModel = Float64Array.of(
      Math.cos(theta), 0, -Math.sin(theta),
      0, 1, 0,
      Math.sin(theta), 0, Math.cos(theta),
    );
    // The nose direction is model +Z. After the rotation it must have moved
    // toward the side the right temple is on, i.e. negative x.
    const nose = [turnRightInModel[2], turnRightInModel[5], turnRightInModel[8]];
    assert.ok(
      nose[0] < -0.1,
      `the rotated nose direction has x=${nose[0].toFixed(3)}; a right turn must ` +
      'send it toward negative x, where TEMPLE_R is',
    );
  });

  it('4. therefore: a right turn foreshortens the right half and reads NEGATIVE yaw', () => {
    // The consequence under test. Nothing here uses the euler helpers to define
    // the rotation — only to read it back.
    const theta = 30 / DEG;
    const turnRightInModel = Float64Array.of(
      Math.cos(theta), 0, -Math.sin(theta),
      0, 1, 0,
      Math.sin(theta), 0, Math.cos(theta),
    );

    // model -> camera is FLIP * R_model. Left-multiply: the head rotates in its
    // OWN frame, then that frame maps to the camera's.
    const pose = poseIdentity();
    mul(pose.R, FACE_TO_CAMERA_FLIP, turnRightInModel);
    pose.t.set([0, 0, 500]);

    // Observable consequence: the wearer's right half of the face compresses.
    const bridge = projectLandmark(pose, LM.NOSE_BRIDGE);
    const rightEye = projectLandmark(pose, LM.EYE_OUTER_R);
    const leftEye = projectLandmark(pose, LM.EYE_OUTER_L);
    const spanRight = Math.hypot(bridge[0] - rightEye[0], bridge[1] - rightEye[1]);
    const spanLeft = Math.hypot(bridge[0] - leftEye[0], bridge[1] - leftEye[1]);
    assert.ok(
      spanRight < spanLeft * 0.8,
      `turning right should compress the right half: right span ${spanRight.toFixed(0)} px, ` +
      `left ${spanLeft.toFixed(0)} px`,
    );

    // And the reported angle must be negative, at the right magnitude.
    const e = headEuler(pose);
    assert.ok(
      e.yaw * DEG < -25 && e.yaw * DEG > -35,
      `headEuler reported yaw = ${(e.yaw * DEG).toFixed(1)} for a 30-degree RIGHT turn; ` +
      'it must be about -30. A positive value means the flip is being applied on ' +
      'the wrong side and yaw is coming back conjugated.',
    );
    assert.ok(Math.abs(e.pitch * DEG) < 1, `pitch leaked: ${(e.pitch * DEG).toFixed(2)}`);
    assert.ok(Math.abs(e.roll * DEG) < 1, `roll leaked: ${(e.roll * DEG).toFixed(2)}`);
  });

  it('5. looking down moves the chin up the image and reads POSITIVE pitch', () => {
    // Same grounding for the other axis. Looking down rotates the nose toward
    // the feet — model -Y — and the observable is that the chin rises toward the
    // face in the image while the forehead falls away.
    const theta = 20 / DEG;
    const lookDownInModel = Float64Array.of(
      1, 0, 0,
      0, Math.cos(theta), -Math.sin(theta),
      0, Math.sin(theta), Math.cos(theta),
    );
    // Ground the matrix before trusting it: the nose direction is model +Z,
    // which is the third COLUMN. Looking down must give it a negative y.
    const noseAfter = [lookDownInModel[2], lookDownInModel[5], lookDownInModel[8]];
    assert.ok(
      noseAfter[1] < -0.3,
      `the rotated nose direction has y=${noseAfter[1].toFixed(3)}; looking down must ` +
      'send it negative. (The first version of this test had the matrix upside ' +
      'down and its own sanity check written so loosely it passed anyway.)',
    );

    const pose = poseIdentity();
    mul(pose.R, FACE_TO_CAMERA_FLIP, lookDownInModel);
    pose.t.set([0, 0, 500]);

    const frontal = poseIdentity();
    frontal.R.set(FACE_TO_CAMERA_FLIP);
    frontal.t.set([0, 0, 500]);

    // Observable: the face shortens vertically and the chin comes up.
    const heightNow = projectLandmark(pose, LM.CHIN)[1] - projectLandmark(pose, LM.FOREHEAD)[1];
    const heightFlat =
      projectLandmark(frontal, LM.CHIN)[1] - projectLandmark(frontal, LM.FOREHEAD)[1];
    assert.ok(
      heightNow < heightFlat,
      `looking down should foreshorten the face: ${heightNow.toFixed(0)} px against ` +
      `${heightFlat.toFixed(0)} flat`,
    );

    const e = headEuler(pose);
    assert.ok(
      e.pitch * DEG > 15 && e.pitch * DEG < 25,
      `headEuler reported pitch = ${(e.pitch * DEG).toFixed(1)} for looking DOWN 20 degrees; ` +
      'it must be about +20',
    );
  });

  it('poseRotationFromHeadEuler is the exact inverse of headEuler', () => {
    // The shared constructor, so nothing has to build a pose by hand again.
    for (const [yaw, pitch, roll] of [
      [0, 0, 0], [30, 0, 0], [-30, 0, 0], [0, 20, 0], [0, -20, 0],
      [0, 0, 15], [-45, 12, -8], [70, -5, 3],
    ]) {
      const pose = poseIdentity();
      poseRotationFromHeadEuler(pose.R, yaw / DEG, pitch / DEG, roll / DEG);
      pose.t.set([0, 0, 500]);
      const e = headEuler(pose);
      assert.ok(Math.abs(e.yaw * DEG - yaw) < 1e-6, `yaw ${yaw} -> ${e.yaw * DEG}`);
      assert.ok(Math.abs(e.pitch * DEG - pitch) < 1e-6, `pitch ${pitch} -> ${e.pitch * DEG}`);
      assert.ok(Math.abs(e.roll * DEG - roll) < 1e-6, `roll ${roll} -> ${e.roll * DEG}`);
    }
  });

  it('the raw camera-frame euler must DISAGREE, or the flip has gone missing', () => {
    // If this ever fails, `headEuler` has quietly become a no-op and the whole
    // mechanism is dead code that happens to work.
    const pose = poseIdentity();
    poseRotationFromHeadEuler(pose.R, 0, 0, 0);
    pose.t.set([0, 0, 500]);
    const raw = eulerYXZ(pose.R);
    assert.ok(
      Math.abs(Math.abs(raw.yaw * DEG) - 180) < 0.01,
      `the raw euler should report a frontal face at +/-180 yaw, got ${raw.yaw * DEG}`,
    );
  });
});

// ------------------------------------------------------------- the protocol

/** A pose for a wearer at the given head angles and camera geometry. */
function poseFor(
  yawDeg: number, pitchDeg: number, basePitchDeg: number, distanceMm: number,
): Pose {
  const pose = poseIdentity();
  poseRotationFromHeadEuler(pose.R, yawDeg / DEG, (pitchDeg + basePitchDeg) / DEG, 0);
  pose.t.set([0, 0, distanceMm]);
  return pose;
}

const basePitchOf = (geometry: typeof CAMERA_LADDER[number]): number =>
  Math.atan2(geometry.belowEyesMm, geometry.distanceMm) * DEG;

describe('the guided scan moves, and the protocol has to cope with that', () => {
  /**
   * Turn smoothly to `maxYaw` at `degPerFrame`, then hold still.
   *
   * Every other test in this file TELEPORTS: it jumps straight to the target
   * angle and holds, so the measured value is constant from the first frame and
   * the plateau detector fires immediately. That is why a per-frame velocity
   * gate sat in `advanceProtocol` undetected — no test ever moved a head.
   */
  function turnSmoothly(degPerFrame: number, maxYaw: number, geometry = CAMERA_LADDER[0]) {
    const basePitchDeg = basePitchOf(geometry);
    const state = createProtocol();

    // Settle the centre beat first so `neutral` is learned.
    for (let i = 0; i < 200 && BEATS[state.index]?.id === 'centre'; i++) {
      advanceProtocol(state, sampleFromPose(
        poseFor(0, 0, basePitchDeg, geometry.distanceMm), state.neutral));
    }

    let yaw = 0;
    for (let f = 0; f < 3000 && BEATS[state.index]?.id === 'turn-right'; f++) {
      yaw = Math.max(-maxYaw, yaw - degPerFrame);
      advanceProtocol(state, sampleFromPose(
        poseFor(yaw, 0, basePitchDeg, geometry.distanceMm), state.neutral));
    }
    return { state, achieved: state.achieved['turn-right'] ?? 0 };
  }

  it('does not cut a smooth turn short — at any speed a person actually turns', () => {
    // 0.3 deg/frame is 9 deg/s, a slow deliberate turn; 2.0 is 60 deg/s, brisk.
    // The old code compared each frame against the LAST frame, so anything under
    // PLATEAU.epsilonDeg (1.2 deg/frame = 36 deg/s) counted as "stopped" from
    // frame one and the beat closed at roughly 18 degrees.
    for (const degPerFrame of [0.3, 0.6, 1.0, 2.0]) {
      const { achieved } = turnSmoothly(degPerFrame, 45);
      assert.ok(
        achieved > 40,
        `turning at ${degPerFrame} deg/frame the beat closed at ${achieved.toFixed(1)} ` +
        'degrees, but the wearer turned 45 — the plateau detector is reading ' +
        'speed rather than stillness',
      );
    }
  });

  it('still gives up when the wearer genuinely stops', () => {
    // The other half: a wearer whose neck stops at 22 degrees must not be asked
    // for 55 forever. Plateau has to fire, just not while they are still moving.
    const { state, achieved } = turnSmoothly(0.6, 22);
    assert.ok(
      state.done.includes('turn-right') || state.index > 1,
      `the wearer stopped at 22 degrees and the beat never closed (achieved ${achieved.toFixed(1)})`,
    );
    assert.ok(
      achieved > 19 && achieved < 25,
      `reported ${achieved.toFixed(1)} degrees for a wearer who turned 22`,
    );
  });

  it('reports what the wearer reached, not the plateau reference', () => {
    // `achieved` feeds the ScanRecord and is the only calibration data for the
    // yaw-compression question (Q13), so it has to be the furthest they got.
    const { achieved } = turnSmoothly(0.5, 33);
    assert.ok(
      Math.abs(achieved - 33) < 3,
      `wearer reached 33 degrees, record says ${achieved.toFixed(1)}`,
    );
  });
});

describe('the guided scan', () => {
  it('a cooperative wearer completes it at every camera geometry', () => {
    for (const geometry of CAMERA_LADDER) {
      const basePitchDeg = basePitchOf(geometry);
      const state = createProtocol();

      // What a wearer following the prompts does, RELATIVE to their own neutral.
      // A right turn is negative yaw — pinned by the observable tests above.
      const script: Record<string, { yaw: number; pitch: number; scale: number }> = {
        centre: { yaw: 0, pitch: 0, scale: 1 },
        'turn-right': { yaw: -60, pitch: 0, scale: 1 },
        'turn-left': { yaw: 60, pitch: 0, scale: 1 },
        'nod-down': { yaw: 0, pitch: 14, scale: 1 },
        'nod-up': { yaw: 0, pitch: -14, scale: 1 },
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
      assert.ok(
        Math.abs(state.neutral!.pitchDeg - basePitchDeg) < 2,
        `${geometry.name}: neutral pitch ${state.neutral!.pitchDeg.toFixed(1)} should be ` +
        `the camera's own ${basePitchDeg.toFixed(1)}`,
      );
    }
  });

  it('the guide dot is on the side the wearer will actually move toward', () => {
    // The preview is a mirror, so a wearer turning right sees themselves move
    // RIGHT on screen. The first version put `turn-right` on the left — where
    // the face goes in the RAW camera image — so it instructed the opposite of
    // what the prompt said.
    const dotOf = (id: string) => BEATS.find((b) => b.id === id)!.target.x;
    assert.ok(dotOf('turn-right') > 0.5, 'turn-right must put the dot on the display RIGHT');
    assert.ok(dotOf('turn-left') < 0.5, 'turn-left must put the dot on the display LEFT');
    assert.ok(BEATS.find((b) => b.id === 'nod-down')!.target.y > 0.5, 'nod-down goes down');
    assert.ok(BEATS.find((b) => b.id === 'nod-up')!.target.y < 0.5, 'nod-up goes up');
  });

  it('no beat asks for an angle a wearer might not have', () => {
    // The reported failure: "turn 30 degrees" completed only after roughly 70
    // degrees of real head turn, and the follow-up beat then demanded 60 more,
    // which is anatomically impossible. Any beat whose quantity is a head
    // ROTATION must be a `reach` — satisfiable by going as far as you can —
    // rather than a threshold whose calibration cannot be verified from here.
    for (const beat of BEATS) {
      if (beat.unit.includes('deg right') || beat.unit.includes('deg left')) {
        assert.equal(
          beat.kind, 'reach',
          beat.id + ' demands a specific turn angle; the measured angle is '
          + 'compressed against the physical one and cannot be calibrated here',
        );
      }
    }
  });

  it('reports what it is measuring, so a stall is diagnosable', () => {
    const state = createProtocol();
    for (let i = 0; i < 40 && state.index === 0; i++) {
      advanceProtocol(state, sampleFromPose(poseFor(0, 0, 0, 500), state.neutral));
    }
    const step = advanceProtocol(state, sampleFromPose(poseFor(-15, 0, 0, 500), state.neutral));
    assert.ok(step.reading, 'no reading was produced');
    assert.ok(
      step.reading!.value > 13 && step.reading!.value < 17,
      'reading ' + step.reading!.value.toFixed(1) + ' should be ~15 degrees of right turn',
    );
    assert.equal(step.reading!.kind, 'reach');
  });

  it('a wearer who can only turn a little still completes the turn', () => {
    // THE reported bug. Whatever the calibration between measured and physical
    // degrees, somebody who turns as far as they comfortably can must be able to
    // finish. The plateau detector — not a threshold — is what makes that true.
    for (const limitDeg of [16, 22, 30, 45]) {
      const state = createProtocol();
      let guard = 0;
      while (!state.finished && guard++ < 6000) {
        const beat = BEATS[state.index];
        let yaw = 0; let pitch = 0; let scale = 1;
        if (beat.id === 'turn-right') yaw = -limitDeg;
        else if (beat.id === 'turn-left') yaw = limitDeg;
        else if (beat.id === 'nod-down') pitch = 14;
        else if (beat.id === 'nod-up') pitch = -14;
        else if (beat.id === 'lean-in') scale = 0.7;
        else if (beat.id === 'lean-back') scale = 1.35;
        advanceProtocol(state, sampleFromPose(poseFor(yaw, pitch, 0, 500 * scale), state.neutral));
      }
      assert.ok(state.finished, 'a ' + limitDeg + '-degree turner never finished');
      assert.ok(
        !state.skipped.includes('turn-right') && !state.skipped.includes('turn-left'),
        'a ' + limitDeg + '-degree turner had a turn beat SKIPPED rather than completed: '
        + state.skipped.join(', '),
      );
      // And what they achieved is recorded rather than pretended.
      const got = achievedTurnDeg(state);
      assert.ok(
        Math.abs(got - limitDeg) < 3,
        'achieved ' + got.toFixed(1) + ' for a ' + limitDeg + '-degree turner',
      );
    }
  });

  it('a wearer who turns a long way finishes without waiting for a plateau', () => {
    const state = createProtocol();
    for (let i = 0; i < 40 && state.index === 0; i++) {
      advanceProtocol(state, sampleFromPose(poseFor(0, 0, 0, 500), state.neutral));
    }
    assert.equal(BEATS[state.index].id, 'turn-right');
    let frames = 0;
    while (BEATS[state.index]?.id === 'turn-right' && frames++ < 500) {
      advanceProtocol(state, sampleFromPose(poseFor(-60, 0, 0, 500), state.neutral));
    }
    assert.ok(state.done.includes('turn-right'), 'a 60-degree turn did not complete');
    assert.ok(frames < 20, 'took ' + frames + ' frames; should finish on the hold, not a plateau');
  });

  it('reports the turn it actually got rather than the one it asked for', () => {
    const state = createProtocol();
    let guard = 0;
    while (!state.finished && guard++ < 6000) {
      const beat = BEATS[state.index];
      let yaw = 0; let pitch = 0; let scale = 1;
      if (beat.id === 'turn-right') yaw = -24;
      else if (beat.id === 'turn-left') yaw = 24;
      else if (beat.id === 'nod-down') pitch = 14;
      else if (beat.id === 'nod-up') pitch = -14;
      else if (beat.id === 'lean-in') scale = 0.7;
      else if (beat.id === 'lean-back') scale = 1.35;
      advanceProtocol(state, sampleFromPose(poseFor(yaw, pitch, 0, 500 * scale), state.neutral));
    }
    const text = summarise(state);
    assert.ok(/24 deg/.test(text), 'summary should name the achieved turn: ' + text);
    assert.ok(
      /inferred rather than seen/.test(text),
      'a 24-degree turn is not enough for a nose silhouette and the summary should say so: ' + text,
    );
  });

  it('measures the nod relative to neutral, not to the camera axis', () => {
    const basePitchDeg = 30;
    const state = createProtocol();
    for (let i = 0; i < 60 && state.index === 0; i++) {
      advanceProtocol(state, sampleFromPose(poseFor(0, 0, basePitchDeg, 500), state.neutral));
    }
    assert.ok(state.neutral, 'the opening beat never completed at 30 degrees of camera tilt');
    assert.ok(Math.abs(state.neutral!.pitchDeg - basePitchDeg) < 2);

    const nodDown = BEATS.find((b) => b.id === 'nod-down')!;
    const nodUp = BEATS.find((b) => b.id === 'nod-up')!;
    const at = (pitch: number) =>
      sampleFromPose(poseFor(0, pitch, basePitchDeg, 500), state.neutral);
    const meets = (beat: typeof nodDown, pitch: number) => {
      const sample = at(pitch);
      return beat.measure(sample, state.neutral) >= beat.goal
        && (beat.also ? beat.also(sample, state.neutral) : true);
    };

    assert.ok(!meets(nodDown, 0), 'sitting still must not count as looking down');
    assert.ok(meets(nodDown, 14), 'looking down 14 degrees must count');
    assert.ok(!meets(nodUp, 0), 'sitting still must not count as looking up');
    assert.ok(meets(nodUp, -14), 'looking up 14 degrees must count');
  });

  it('never stalls: not on a wearer who will not move', () => {
    const state = createProtocol();
    const stubborn = poseFor(0, 0, 0, 500);
    for (let i = 0; i < 8000 && !state.finished; i++) {
      advanceProtocol(state, sampleFromPose(stubborn, state.neutral));
    }
    assert.ok(state.finished, 'the protocol stalled on a wearer who never moved');
    assert.ok(state.done.includes('centre'), 'the opening beat should still have completed');
    // A wearer who never turns at all cannot COMPLETE a turn beat: `minimum`
    // exists so that "stopped at zero" is not mistaken for "went as far as they
    // could". It is skipped, and the summary says so.
    assert.ok(
      state.skipped.includes('turn-right'),
      'a wearer who never turned should have the turn SKIPPED, not completed at 0',
    );
  });

  it('never stalls: not when the detector loses the face entirely', () => {
    // The failure a profile hold provokes, and the one that froze the scan with
    // no progress, no give-up and no message.
    const state = createProtocol();
    for (let i = 0; i < 40 && state.index === 0; i++) {
      advanceProtocol(state, sampleFromPose(poseFor(0, 0, 0, 500), state.neutral));
    }
    assert.ok(state.index > 0, 'the opening beat did not complete');

    for (let i = 0; i < 8000 && !state.finished; i++) advanceProtocol(state, null);
    assert.ok(state.finished, 'the protocol stalled when the face was never seen again');
    assert.ok(state.skipped.length >= 5, 'only skipped ' + state.skipped.length + ' beats');
  });

  it('never stalls: not when there is never a face at all', () => {
    const state = createProtocol();
    for (let i = 0; i < 8000 && !state.finished; i++) advanceProtocol(state, null);
    assert.ok(state.finished, 'the protocol stalled with no face from the very first frame');
    assert.equal(state.neutral, null, 'a neutral cannot be invented from nothing');
  });

  it('still establishes a neutral when the opening beat is skipped but a face was seen', () => {
    const state = createProtocol();
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

// ------------------------------------------------------------- the neutral

/**
 * A pose sample by its numbers rather than through a pose.
 *
 * These tests are about WHICH frames reach the neutral, not about the geometry
 * that produced them, so building the sample directly keeps the euler chain out
 * of the question entirely.
 */
const S = (yaw: number, pitch = 0) => ({
  yawDeg: yaw, pitchDeg: pitch, rollDeg: 0, distanceMm: 500, distanceRatio: 1,
});

describe('the wearer\'s neutral is a measurement, so it has to come from the right frames', () => {
  it('is learned from the centre beat and from nothing else', () => {
    // The failure this pins was silent and complete. A `centre` beat the
    // detector never satisfied left the accumulator open, so the guard was "the
    // neutral is still null" rather than "this is the centre beat" — and the
    // NEXT beat filled it. The wearer's hard right turn latched as straight
    // ahead. Everything after was measured against a lie: `turn-left` completed
    // with the wearer at TRUE centre while the record claimed 50 degrees of turn
    // that never happened, and both nod beats' `|dYaw| < 25` guard read 50 at
    // every centre pose and could only ever time out.
    const st = createProtocol();

    // 245 frames with no face at all — past GIVE_UP_FRAMES.required (240), so
    // `centre` is abandoned having never once seen the wearer.
    for (let i = 0; i < 245; i++) advanceProtocol(st, null);
    assert.ok(st.skipped.includes('centre'), 'the opening beat did not give up');
    assert.equal(
      st.neutral, null,
      'a neutral was invented from a beat that saw no face at all',
    );

    // Now the wearer is turned hard right for `turn-right`, which closes on the
    // plateau detector at 50 degrees.
    for (let i = 0; i < 40; i++) advanceProtocol(st, S(-50));
    assert.ok(st.done.includes('turn-right'), 'the turn beat never closed');
    assert.equal(
      st.neutral, null,
      `the neutral came back as ${JSON.stringify(st.neutral)} — a turned pose has ` +
      'latched as the wearer\'s straight-ahead, and every later beat is now ' +
      'measured against it',
    );

    // And the consequence, which is the part a wearer would have felt: sitting
    // at TRUE centre must not complete a beat that asks for a left turn.
    for (let i = 0; i < 60; i++) advanceProtocol(st, S(0));
    assert.ok(
      !st.done.includes('turn-left'),
      'turn-left completed with the wearer facing straight at the camera',
    );
    assert.equal(
      st.achieved['turn-left'], undefined,
      `turn-left recorded ${st.achieved['turn-left']} degrees of turn from a wearer ` +
      'who did not move',
    );
  });

  it('keeps the frames a timed-out centre beat did see', () => {
    // The other half. `abandon` used to throw away every good centre sample it
    // had collected and then adopt whatever single pose it happened to be
    // holding at the moment it gave up — or, with no face at that moment,
    // nothing at all. A wearer half out of frame, with the detector dropping in
    // and out, is the ordinary way to get here.
    const st = createProtocol();

    // Four frames that satisfy the beat (|yaw| 2 <= goal 14, |pitch| 3 < 38) but
    // fall short of its holdFrames of 8, so it never completes.
    for (let i = 0; i < 4; i++) advanceProtocol(st, S(2, 3));
    assert.equal(st.neutral, null, 'the beat completed early — the fixture is wrong');

    // Then the face is gone for long enough to run past the 240-frame give-up.
    for (let i = 0; i < 260; i++) advanceProtocol(st, null);

    assert.ok(st.skipped.includes('centre'), 'the opening beat did not give up');
    assert.deepEqual(
      st.neutral, { yawDeg: 2, pitchDeg: 3, distanceMm: 500 },
      'the four good centre frames were discarded rather than averaged',
    );
  });
});
