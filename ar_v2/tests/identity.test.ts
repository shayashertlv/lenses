/**
 * The identity watch, asked what it actually does.
 *
 * Two halves. The first is the state machine — cheap, exhaustive, and every
 * assertion names how to make it red. The second runs the REAL tracker over the
 * synthetic population and pins the measurement the constants were chosen from,
 * because a threshold with no regression bar under it is a threshold that drifts
 * the first time somebody touches the estimator that feeds it.
 *
 * The state-machine half deliberately does not go through `track()`. Feeding
 * hand-made observations is what makes "a turned frame neither strikes nor
 * acquits" testable at all — through the tracker you cannot hold every other
 * variable still while moving yaw alone.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { readFileSync } from 'node:fs';

import {
  IDENTITY_MAX_PITCH_DEG, IDENTITY_MAX_YAW_DEG, IDENTITY_MIN_CORRESPONDENCES,
  IDENTITY_REFERENCE_FRAMES, IDENTITY_SIGMA_DRIFT_MAX, IDENTITY_STRIKES,
  IDENTITY_VF_RATIO, IDENTITY_WINDOW,
  armWearer, createIdentityWatch, forgetWearer, observeIdentity, qualifies,
  type IdentityObservation, type IdentityWatch,
} from '../src/track/identity.js';
import { parseFaceObj } from '../src/core/mesh.js';
import { buildAnthropometricBasis } from '../src/core/shape/anthropometric.js';
import { createFaceModel } from '../src/core/facemodel.js';
import {
  CAMERA_LADDER, captureSeedFor, generatePopulation, populationSeedFor, synthesizeCapture,
} from '../src/testkit/synthetic.js';
import { createTracker, track } from '../src/track/tracker.js';
import {
  UNCERTAINTY_DEFAULTS, createUncertainty, estimateSigma,
} from '../src/detect/uncertainty.js';
import { intrinsicsFromFov } from '../src/core/camera.js';
import { templatePath } from '../src/testkit/fixtures.js';

/** A frame that qualifies: frontal, solved, plenty of correspondences. */
const frame = (varianceFactor: number, over: Partial<IdentityObservation> = {}): IdentityObservation => ({
  solved: true,
  varianceFactor,
  yawRad: 0,
  pitchRad: 0,
  correspondences: 468,
  meanSigmaPx: 1,
  ...over,
});

/** Arms a watch and walks it past the reference window at `vf`. */
function learned(vf = 2): IdentityWatch {
  const watch = createIdentityWatch();
  armWearer(watch);
  for (let i = 0; i < IDENTITY_REFERENCE_FRAMES; i++) observeIdentity(watch, frame(vf));
  assert.ok(Number.isFinite(watch.reference), 'the reference never settled');
  return watch;
}

describe('the identity watch refuses to judge before it can', () => {
  it('abstains for ever until it is armed, however wrong the readings are', () => {
    // RED: delete the `if (!watch.armed) return 'abstain'` line in
    // `observeIdentity`. This is the guard that keeps a model restored from
    // localStorage — scanned in another session, possibly on another device —
    // from referencing whoever happens to sit down in front of it.
    const watch = createIdentityWatch();
    for (let i = 0; i < 200; i++) {
      assert.equal(observeIdentity(watch, frame(1000)), 'abstain');
    }
    assert.ok(!Number.isFinite(watch.reference), 'a disarmed watch learned a reference');
    assert.equal(watch.convictions, 0);
  });

  it('cannot convict while it is still learning this wearer', () => {
    // RED: move the `watch.learning` block below the strike arithmetic, or seed
    // `reference` from the first frame. A predicate that judges before it has a
    // reference is judging against a constant, which is the failure the whole
    // module is shaped to avoid.
    const watch = createIdentityWatch();
    armWearer(watch);
    for (let i = 0; i < IDENTITY_REFERENCE_FRAMES - 1; i++) {
      assert.equal(observeIdentity(watch, frame(500)), 'learning');
    }
    assert.equal(watch.convictions, 0, 'convicted before it knew the wearer');
  });

  it('takes the MEDIAN of the reference window, so one wild frame cannot set it', () => {
    // RED: replace `median(watch.learning)` with `watch.learning[0]` or a mean.
    // With a mean, the 900 below drags the reference to ~77 and the wearer's own
    // subsequent readings all look tiny — the watch goes permanently blind.
    const watch = createIdentityWatch();
    armWearer(watch);
    observeIdentity(watch, frame(900));
    for (let i = 1; i < IDENTITY_REFERENCE_FRAMES; i++) observeIdentity(watch, frame(2));
    assert.equal(watch.reference, 2, `reference ${watch.reference} was dragged by one frame`);
  });
});

describe('the identity watch asks only frames that can answer', () => {
  it('refuses a turned head — and neither strikes NOR acquits on it', () => {
    // The measured reason is the opposite of the obvious one. Matched
    // varianceFactor is FLAT in yaw (1.74-2.02 from 0 to 90 degrees), so a
    // turning wearer is not a false-alarm risk. What decays is the impostor
    // signal: in-bucket AUC 0.975 frontal, 0.814 at 90 degrees, because the far
    // half-face is hallucinated and its inflated sigma mutes the very
    // correspondences that carry identity. This gate is a false-NEGATIVE guard.
    //
    // RED: delete the yaw test in `qualifies`.
    const beyond = ((IDENTITY_MAX_YAW_DEG + 5) * Math.PI) / 180;
    assert.ok(!qualifies(frame(2, { yawRad: beyond })), 'a turned frame qualified');
    assert.ok(!qualifies(frame(2, { yawRad: -beyond })), 'yaw is not symmetric');
    assert.ok(!qualifies(frame(2, {
      pitchRad: ((IDENTITY_MAX_PITCH_DEG + 5) * Math.PI) / 180,
    })), 'a nodded frame qualified');

    // And now the part that matters: a turned frame must be INVISIBLE to the
    // streak, not a vote either way. Build a streak, interrupt it with turned
    // frames that would otherwise acquit, and the streak must survive.
    const watch = learned(2);
    const high = frame(2 * IDENTITY_VF_RATIO * 4);
    for (let i = 0; i < IDENTITY_WINDOW; i++) observeIdentity(watch, high);
    const before = watch.strikes;
    assert.ok(before > 0, 'the setup never struck');
    for (let i = 0; i < 20; i++) {
      assert.equal(observeIdentity(watch, frame(2, { yawRad: beyond })), 'abstain');
    }
    assert.equal(watch.strikes, before,
      'a turned frame moved the streak — it must neither build one nor break one');
    assert.equal(watch.acquitted, 0, 'a turned frame acquitted');
  });

  it('refuses a frame that placed too little face, and a held frame', () => {
    // RED: delete either guard. A frame with a quarter of the landmarks has a
    // whitened residual dominated by whichever quarter survived; a held frame
    // has no solve of its own and carries NaN.
    assert.ok(!qualifies(frame(2, { correspondences: IDENTITY_MIN_CORRESPONDENCES - 1 })));
    assert.ok(!qualifies(frame(2, { solved: false })));
    assert.ok(!qualifies(frame(NaN)));
    assert.ok(!qualifies(frame(0)), 'a zero variance factor is not a reading');
    assert.ok(!qualifies(frame(2, { yawRad: NaN })), 'a missing euler is not a frontal frame');
  });
});

describe('the identity watch convicts on a streak and acquits whole', () => {
  it('says the same person while the reading sits at their own reference', () => {
    // RED: change the comparison to `>=` against the reference itself rather
    // than against reference * IDENTITY_VF_RATIO.
    const watch = learned(2);
    for (let i = 0; i < 100; i++) {
      assert.equal(observeIdentity(watch, frame(2)), 'same');
    }
    assert.equal(watch.convictions, 0);
    assert.equal(watch.struck, 0);
  });

  it('tolerates a reading just under the bar for ever', () => {
    // The bar is a plateau, not a knife edge: measured, anything from 1.75 to
    // 2.5 gives 0/80 false convictions and 93% detection. This pins the near
    // side of it.
    // RED: lower IDENTITY_VF_RATIO below 1.9.
    const watch = learned(2);
    const justUnder = 2 * IDENTITY_VF_RATIO * 0.95;
    for (let i = 0; i < 100; i++) observeIdentity(watch, frame(justUnder));
    assert.equal(watch.convictions, 0,
      `a reading at ${justUnder.toFixed(2)}x convicted; the bar is ${IDENTITY_VF_RATIO}x`);
  });

  it('needs the whole streak, and the window full before it counts at all', () => {
    // RED: set IDENTITY_STRIKES to 1, or return 'changed' before the streak
    // check. v1 shipped a single-frame conviction and a live session reset the
    // person model NINE times in four minutes.
    const watch = learned(2);
    const high = frame(2 * IDENTITY_VF_RATIO * 4);
    // The window has to fill before any median is taken, then the streak runs.
    const needed = (IDENTITY_WINDOW - 1) + IDENTITY_STRIKES;
    for (let i = 0; i < needed - 1; i++) {
      assert.equal(observeIdentity(watch, high), 'same', `convicted early at frame ${i}`);
    }
    assert.equal(observeIdentity(watch, high), 'changed');
    assert.equal(watch.convictions, 1);
  });

  it('acquits the WHOLE streak the moment the window turns over', () => {
    // **The window is the reading, and this test exists because I got that
    // wrong first.** The prose said "a single agreeing frame acquits", which is
    // v1's rule applied to v1's shape — it compared one raw sample against a
    // carried median. Here the comparison is a 5-frame median against a fixed
    // reference, so one agreeing frame does not move the verdict at all: the
    // median still holds four disagreeing frames. Three do.
    //
    // That is the better behaviour and not a compromise — a wearer is not
    // acquitted by one lucky frame any more than they are convicted by one bad
    // one — but it means the evidence a conviction needs is the window turning
    // over PLUS the streak, which is why the calibration was run end to end
    // rather than reasoned about.
    //
    // RED: change `watch.strikes = 0` to `watch.strikes--` in the acquit branch.
    // With a decrement a wearer producing four bad frames for every good one
    // still convicts eventually — a slow false positive, and one that a short
    // test would not show.
    const watch = learned(2);
    const high = frame(2 * IDENTITY_VF_RATIO * 4);
    // Two strikes: the window fills on the 5th frame and strikes from there.
    for (let i = 0; i < IDENTITY_WINDOW + 1; i++) observeIdentity(watch, high);
    assert.equal(watch.strikes, 2, 'the setup did not strike twice');

    // Agreeing frames now displace the window one at a time. The first two do
    // not move the median off 'high', so they STRIKE rather than acquit.
    observeIdentity(watch, frame(2));
    observeIdentity(watch, frame(2));
    assert.equal(watch.strikes, 4, 'an agreeing frame acquitted before the window turned over');
    assert.equal(watch.acquitted, 0);

    // The third takes the median under the bar, and the streak goes whole —
    // from 4 to 0, not to 3.
    assert.equal(observeIdentity(watch, frame(2)), 'same');
    assert.equal(watch.strikes, 0, 'the streak was decremented rather than cleared');
    assert.equal(watch.acquitted, 1);
  });

  it('forgets the wearer but keeps the counters — a reset must stay reportable', () => {
    // v1's rule and the sharpest small idea in that tree: a counter that resets
    // with the thing it counts cannot report the reset.
    // RED: zero `convictions` in `forgetWearer`, or clear it on conviction.
    const watch = learned(2);
    const high = frame(2 * IDENTITY_VF_RATIO * 4);
    let verdict = '';
    for (let i = 0; i < 40 && verdict !== 'changed'; i++) verdict = observeIdentity(watch, high);
    assert.equal(verdict, 'changed');
    assert.equal(watch.convictions, 1);

    // The retirement's own streak is STATE and goes; the count of excursions is
    // a lifetime counter and stays, for the same reason `convictions` does.
    // RED: drop `watch.sigmaStrikes = 0` from `forgetWearer`, and a part-built
    // run leaks into the next wearer; or zero `sigmaExcursions` there, and a
    // session that spent itself on transients cannot report that it did.
    for (let i = 0; i < IDENTITY_STRIKES - 1; i++) {
      observeIdentity(watch, frame(2, { meanSigmaPx: 1.8 }));
    }
    assert.ok(watch.sigmaStrikes > 0, 'the excursions never built a run to begin with');

    forgetWearer(watch);
    assert.ok(!Number.isFinite(watch.reference), 'the reference survived a forget');
    assert.equal(watch.armed, false, 'a forgotten wearer left the watch armed');
    assert.equal(watch.strikes, 0);
    assert.equal(watch.sigmaStrikes, 0,
      'a part-built retirement run survived into the next wearer');
    assert.equal(watch.convictions, 1, 'the conviction count was reset with the conviction');
    assert.equal(watch.sigmaExcursions, IDENTITY_STRIKES - 1,
      'the excursion count was reset with the thing it counts');
  });
});

describe('the identity watch, measured against the population it was tuned on', () => {
  // This is the regression bar under IDENTITY_VF_RATIO. It runs the REAL
  // tracker, so it fails if `pnp.ts` changes what `varianceFactor` means — which
  // is exactly the coupling a hand-picked constant hides.
  //
  // Deliberately smaller than the calibration sweep (2 seeds x 6 subjects x 1
  // geometry against 5 x 8 x 3) so the suite stays affordable. The calibration's
  // own numbers are in the constant's docstring.
  const mesh = parseFaceObj(readFileSync(templatePath(), 'utf8'));
  const basis = buildAnthropometricBasis(mesh);

  const truthModel = (positions: Float64Array) => createFaceModel({
    positions: new Float64Array(positions),
    vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.1),
    shapeCoeffs: new Float64Array(0), basisName: 'ground-truth',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
    landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
    quality: {}, pdMm: null, pdSigmaMm: null, reprojectionRmsPx: 0,
    framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });

  /** Learns on `first`, then continues on `second`. Returns whether it convicted. */
  function session(
    model: ReturnType<typeof truthModel>,
    intrinsics: any,
    first: readonly any[],
    second: readonly any[],
  ): boolean | null {
    const tracker = createTracker(model, { smooth: false });
    const watch = createIdentityWatch();
    armWearer(watch);
    let convicted = false;
    const feed = (frames: readonly any[], after: boolean) => {
      for (const f of frames) {
        const r = track(tracker, {
          landmarks: f.landmarks, sigmaPx: f.sigmaPx, intrinsics, dt: 1 / 30,
        });
        let sum = 0, count = 0;
        for (let i = 0; i < f.sigmaPx.length; i++) {
          if (Number.isFinite(f.sigmaPx[i])) { sum += f.sigmaPx[i]; count++; }
        }
        const v = observeIdentity(watch, {
          solved: r.tracked && !r.held,
          varianceFactor: r.varianceFactor,
          yawRad: r.euler ? r.euler.yaw : NaN,
          pitchRad: r.euler ? r.euler.pitch : NaN,
          correspondences: r.correspondences,
          meanSigmaPx: count ? sum / count : NaN,
        });
        if (after && v === 'changed') convicted = true;
      }
    };
    feed(first, false);
    if (!Number.isFinite(watch.reference)) return null;
    feed(second, true);
    return convicted;
  }

  it('never calls the wearer a stranger, and catches most strangers', () => {
    const geometry = CAMERA_LADDER[0];
    let sameFalse = 0, sameTotal = 0, caught = 0, swapTotal = 0;

    for (const seed of [11, 23]) {
      const subjects = generatePopulation(mesh, basis, {
        count: 6, seed: populationSeedFor(seed),
      }).slice(0, 6);
      const models = subjects.map((s) => truthModel(s.positions));
      const caps = subjects.map((s) => synthesizeCapture(mesh, s, geometry, {
        framesPerBeat: 10, seed: captureSeedFor(seed),
      }));

      for (let a = 0; a < subjects.length; a++) {
        const half = Math.floor(caps[a].frames.length / 2);
        const learn = caps[a].frames.slice(0, half);
        const stay = session(models[a], caps[a].trueIntrinsics, learn, caps[a].frames.slice(half));
        if (stay !== null) { sameTotal++; if (stay) sameFalse++; }
        for (let b = 0; b < subjects.length; b++) {
          if (b === a || (a + b) % 2 !== 0) continue;
          const swap = session(
            models[a], caps[a].trueIntrinsics, learn, caps[b].frames.slice(half),
          );
          if (swap !== null) { swapTotal++; if (swap) caught++; }
        }
      }
    }

    assert.ok(sameTotal >= 10 && swapTotal >= 10,
      `the harness produced too few sessions to mean anything (${sameTotal}/${swapTotal})`);

    // RED (false positives): raise IDENTITY_VF_RATIO's denominator, drop the
    // frontal gate, or set IDENTITY_STRIKES to 1. The calibration sweep put the
    // worst genuine session at 1.723x its own reference against a 2.0x bar, so
    // this has 16% of headroom and is not a knife edge.
    assert.equal(sameFalse, 0,
      `${sameFalse} of ${sameTotal} genuine sessions were convicted. A wearer being told `
      + 'they are a stranger throws away a scan they sat through.');

    // RED (the feature quietly dying): switch the signal back to `rmsPx`, which
    // measured EER 0.316 and is destroyed by 15% occlusion. The bar is 70%
    // rather than the measured 89% because this runs one camera geometry and
    // two seeds; it is a floor under the mechanism, not a restatement of the
    // calibration.
    const rate = caught / swapTotal;
    assert.ok(rate >= 0.7,
      `only ${caught} of ${swapTotal} swaps were caught (${(rate * 100).toFixed(0)}%)`);
  });
});

describe('the identity watch knows a moving ruler from a moving face', () => {
  // The module's own header used to argue that a ratio to the wearer's own
  // reference cancels a miscalibrated sigma estimator. It does — completely,
  // and ONLY for a constant miscalibration. Measured end to end:
  //
  //     arm                        same-person worst ratio   false convictions
  //     honest                             1.687                   0/36
  //     OFFSET, 4x overconfident           1.797                   0/36
  //     DRIFT to 2x mid-session            4.720                  36/36
  //     DRIFT to 4x mid-session           16.847                  36/36
  //
  // These three tests are that experiment, in the small.

  /**
   * Learns at (`learnVf`, `learnSigma`), then judges at (`judgeVf`,
   * `judgeSigma`).
   *
   * Both halves are stated explicitly, because the distinction the whole guard
   * turns on lives between them. An overconfident detector inflates the
   * REFERENCE as well as the reading — that is why the ratio cancels it — so an
   * arm that inflates only the reading is not modelling an overconfident
   * detector at all. It is modelling a different face. The first draft of this
   * helper made exactly that mistake and its "constant overconfidence" arm
   * convicted, correctly, for a reason that had nothing to do with sigma.
   */
  function run(
    learnVf: number, learnSigma: number, judgeVf: number, judgeSigma: number,
  ) {
    const watch = createIdentityWatch();
    armWearer(watch);
    for (let i = 0; i < IDENTITY_REFERENCE_FRAMES; i++) {
      observeIdentity(watch, frame(learnVf, { meanSigmaPx: learnSigma }));
    }
    const verdicts: string[] = [];
    for (let i = 0; i < 60; i++) {
      verdicts.push(observeIdentity(watch, frame(judgeVf, { meanSigmaPx: judgeSigma })));
    }
    return { watch, verdicts };
  }

  it('a CONSTANT overconfidence changes nothing, because the ratio cancels it', () => {
    // RED: compare `varianceFactor` against an absolute constant instead of
    // against the wearer's own reference. Every reading here is 4x what the
    // honest detector would report, and none of it should matter.
    const { watch, verdicts } = run(8, 0.25, 8, 0.25);
    assert.equal(watch.convictions, 0,
      'a detector that has ALWAYS been overconfident convicted the wearer');
    assert.equal(watch.recalibrations, 0,
      'a constant offset is not a drift and must not throw the reference away');
    assert.ok(verdicts.every((v) => v === 'same'), 'expected a steady verdict');
  });

  it('a MID-SESSION drift retires the reference instead of convicting', () => {
    // This is the fix. The sigma scale halves, so every whitened residual
    // quadruples — 4x the bar — and without the guard this is 36/36 false
    // convictions. The claimed sigma is the tell: an identity change moves it
    // by at most 1.35x, a drift like this by 2x.
    //
    // RED: delete the `sigmaScale` guard in `observeIdentity`. The verdict
    // becomes 'changed' and a wearer sitting perfectly still is told they are
    // somebody else.
    const { watch, verdicts } = run(2, 1, 8, 0.5);
    assert.equal(watch.convictions, 0,
      'the wearer was convicted for the detector changing its mind about its own noise');
    assert.equal(watch.recalibrations, 1, 'the stale reference was not retired');
    // On the FIRST frame, and that is not the same rule as the one above it.
    // This fixture is a FALL — sigma 1 to 0.5 — and a fall cannot be a
    // transient: `estimateSigma` fills at `floorPx` and only ever multiplies UP
    // (occlusion by a factor >= 1, disagreement through a `hypot`, then a clamp
    // whose lower bound is the floor again). A reading BELOW a calm reference
    // therefore has no per-frame mechanism behind it; it is the scale that
    // moved. Measured over the same-person sessions, the per-frame ratio bottoms
    // out at 0.72 against a low bar of 0.625 and never approaches it, while the
    // HIGH side reaches 1.97. See `observeIdentity`.
    assert.equal(verdicts[0], 'recalibrating',
      'a FALL in the claimed sigma was not retired at once — nothing in the '
      + 'estimator can produce one transiently, so waiting costs sensitivity for nothing');
    // ...and it comes back on its own, on the new scale, rather than abstaining
    // for ever. A drifted estimator does not drift back.
    assert.ok(Number.isFinite(watch.reference), 'the reference was never relearned');
    assert.ok(verdicts.slice(-5).every((v) => v === 'same'),
      'the watch never recovered after recalibrating');
  });

  it('still convicts a new face when the ruler has NOT moved', () => {
    // The guard must not be an amnesty. Same sigma throughout, vf up 4x — that
    // is a face, and it has to be caught.
    // RED: widen IDENTITY_SIGMA_DRIFT_MAX to swallow everything, or recalibrate
    // unconditionally.
    const { watch } = run(2, 1, 8, 1);
    assert.equal(watch.recalibrations, 0, 'a steady ruler was mistaken for a drifting one');
    // At least one. The reference survives a conviction — only the rolling
    // window is cleared — so a reading that stays high goes on convicting every
    // ninth frame. That is correct: the app resets on the first one.
    assert.ok(watch.convictions >= 1, 'a different face went uncaught');
  });

  it('a TRANSIENT excursion abstains: it does not retire, and the swap after it still convicts', () => {
    // **The retirement used to be a one-frame decision.** Everything else in
    // this module that destroys state demands persistence — the verdict needs a
    // full window median AND `IDENTITY_STRIKES` consecutive strikes, and its
    // docstrings say why: per-frame readings are wild. The retirement destroys
    // strictly more (the reference, the window, the strikes and the learning
    // arrays) and asked for one frame.
    //
    // And the bar it uses is an AGGREGATE. `IDENTITY_SIGMA_DRIFT_MAX`'s own
    // derivation is "second half of a session against the first"; nothing ever
    // measured a single frame against a twelve-frame median.
    //
    // Measured, through the real `estimateSigma` over synthetic captures of the
    // SAME person with `varianceFactor` pinned at 1.0 — nothing about the
    // wearer changing at all (scratchpad/f27-rate.mjs, f27-choose.mjs):
    //
    //     rule        false retirements   a real 2x drift   a real wearer SWAP
    //     shipped         8 of 8            caught, +30       caught 0 of 8
    //     5 consecutive   0 of 8            caught, +34       caught 8 of 8
    //
    // The middle column is the cost and it is four qualifying frames, with zero
    // false convictions in the gap at any rule tried. The right-hand column is
    // the point: the one-frame retirement does not degrade this watch, it
    // DISABLES it. The wearer turns their head, one qualifying frame on the way
    // back reads 1.8x, the reference and the strikes go, and the relearn adopts
    // whoever is in front of the camera.
    //
    // RED: retire on the first excursion. `convictions` reads 0.
    const watch = learned(2);
    const verdicts: string[] = [];

    // A transient one frame short of the streak: real, and over as quickly as a
    // head turn is. Nothing about the wearer has changed.
    for (let i = 0; i < IDENTITY_STRIKES - 1; i++) {
      verdicts.push(observeIdentity(watch, frame(2, { meanSigmaPx: 1.8 })));
    }
    assert.ok(verdicts.every((v) => v === 'abstain'),
      `a transient excursion produced ${verdicts.join(', ')} — it must change nothing, `
      + 'the way a turned frame does');
    assert.equal(watch.recalibrations, 0, 'a transient retired the reference');
    assert.ok(Number.isFinite(watch.reference), 'a transient threw the wearer away');

    // ...and now a real change of wearer, with the ruler steady again.
    for (let i = 0; i < IDENTITY_WINDOW * (IDENTITY_STRIKES + 2); i++) {
      observeIdentity(watch, frame(8, { meanSigmaPx: 1 }));
    }
    assert.equal(watch.recalibrations, 0, 'a steady ruler was mistaken for a drifting one');
    assert.ok(watch.convictions >= 1,
      'the swap went uncaught: the transient had already thrown away the reference it '
      + 'would have been judged against, and the relearn adopted the stranger');
  });

  it('and a SUSTAINED rise still retires, on the streak the rest of the module uses', () => {
    // The other end of the same bar. A drifted estimator does not drift back,
    // so the retirement must still happen — just not on one frame's word.
    // RED: never retire, or require more than IDENTITY_STRIKES.
    const watch = learned(2);
    const verdicts: string[] = [];
    for (let i = 0; i < IDENTITY_STRIKES; i++) {
      verdicts.push(observeIdentity(watch, frame(2, { meanSigmaPx: 1.8 })));
    }
    assert.deepEqual(
      verdicts,
      [...Array(IDENTITY_STRIKES - 1).fill('abstain'), 'recalibrating'],
      'the sustained rise was not retired on exactly the streak',
    );
    assert.equal(watch.recalibrations, 1);
    assert.equal(watch.convictions, 0,
      'the wearer was convicted for the detector changing its mind about its own noise');
  });

  it('a FALL is retired at once, because nothing in the estimator can fake one', () => {
    // The asymmetry is the estimator's, not a preference. `estimateSigma` fills
    // at `floorPx`, multiplies by an occlusion factor of at least 1, combines
    // the disagreement EMA through `Math.hypot`, and clamps with `floorPx` as
    // the LOWER bound. Every step is non-decreasing, so a per-frame excursion
    // can only be upward: a head turn inflates the claimed sigma and nothing
    // deflates it.
    //
    // Measured on same-person captures the per-frame ratio spans 0.72 to 1.97
    // against a band of [0.625, 1.6] — it leaves through the top in 8 of 8
    // sessions and never comes near the bottom. So the streak buys nothing on
    // this side and costs real drift sensitivity: measured across the eight
    // sessions with the variance factor moving as 1/scale^2 the way a genuine
    // drift moves it, a symmetric streak protects 4 of 8 sessions at a sigma
    // scale of 0.55 where the one-frame rule protects 7, and 44 false
    // convictions against 28.
    // RED: put the streak on both sides.
    const watch = learned(2);
    assert.equal(observeIdentity(watch, frame(2, { meanSigmaPx: 1 / (IDENTITY_SIGMA_DRIFT_MAX + 0.2) })),
      'recalibrating', 'a fall in the claimed sigma waited for a streak it cannot need');
    assert.equal(watch.recalibrations, 1);
    assert.equal(watch.sigmaStrikes, 0, 'a fall left a run behind it');
  });

  it('the streak is a RUN, not a tally: two separate transients do not add up', () => {
    // A session that turns its head twice must not retire on the sum of the two
    // turns. `sigmaStrikes` has to reset on the first frame that reads normally,
    // exactly as `strikes` does when the window turns over.
    // RED: drop `watch.sigmaStrikes = 0;` after the excursion branch.
    const watch = learned(2);
    for (const _ of Array(2)) {
      for (let i = 0; i < IDENTITY_STRIKES - 1; i++) {
        observeIdentity(watch, frame(2, { meanSigmaPx: 1.8 }));
      }
      for (let i = 0; i < IDENTITY_WINDOW * 2; i++) observeIdentity(watch, frame(2));
    }
    assert.equal(watch.recalibrations, 0,
      'two transients four frames long added up to a retirement');
    assert.equal(watch.sigmaExcursions, 2 * (IDENTITY_STRIKES - 1),
      'the excursions were not counted, so a session spending its time on transients '
      + 'cannot be told from a steady one');
  });

  it('an excursion is not judged, only skipped: the verdict never sees a suspect denominator', () => {
    // The frames below sit past the bar AND well over `IDENTITY_VF_RATIO`. If an
    // excursion fell through to the verdict path instead of abstaining, they
    // would strike — and a reading whose denominator this watch has just called
    // untrustworthy is exactly what `IDENTITY_SIGMA_DRIFT_MAX` exists to refuse.
    // RED: delete the early `return 'abstain'` and let the excursion fall through.
    const watch = learned(2);
    for (let i = 0; i < IDENTITY_STRIKES - 1; i++) {
      observeIdentity(watch, frame(20, { meanSigmaPx: 1.8 }));
    }
    assert.equal(watch.asked, IDENTITY_REFERENCE_FRAMES ? watch.asked : 0);
    assert.equal(watch.struck, 0, 'an excursion frame was allowed to strike');
    assert.equal(watch.strikes, 0, 'an excursion frame built a streak toward a conviction');
    assert.equal(watch.recent.length, 0, 'an excursion frame entered the verdict window');
  });

  it('the streak counts consecutive ASKED frames, so a turned head neither builds nor breaks it', () => {
    // The same rule the strike streak lives under, and for the same reason: the
    // window is consecutive frames the watch was ASKED about, not consecutive
    // frames. A blink or a hard turn is invisible to it.
    // RED: move the excursion test above the `qualifies` guard, or reset the
    // streak on a non-qualifying frame.
    const watch = learned(2);
    for (let i = 0; i < IDENTITY_STRIKES - 1; i++) {
      observeIdentity(watch, frame(2, { meanSigmaPx: 1.8 }));
    }
    // A turned frame, in the middle of the run.
    assert.equal(
      observeIdentity(watch, frame(2, { meanSigmaPx: 1.8, yawRad: (IDENTITY_MAX_YAW_DEG + 5) * Math.PI / 180 })),
      'abstain',
    );
    assert.equal(watch.recalibrations, 0, 'a turned frame completed the retirement streak');
    // ...and the next asked excursion completes it, because the turn did not
    // break the run either.
    assert.equal(observeIdentity(watch, frame(2, { meanSigmaPx: 1.8 })), 'recalibrating',
      'the turned frame broke a run of asked frames it should have been invisible to');
  });

  it('leaves room between the largest identity excursion and the smallest harmful drift', () => {
    // The bound is not arbitrary and this pins both sides of it. Measured, the
    // mean claimed sigma moves by at most 1.349x across an identity change and
    // by 2.0x on the smallest drift that produces false convictions.
    // RED: move IDENTITY_SIGMA_DRIFT_MAX outside [1.4, 1.9].
    assert.ok(IDENTITY_SIGMA_DRIFT_MAX > 1.35,
      `${IDENTITY_SIGMA_DRIFT_MAX} sits under the 1.349x that a change of WEARER produced — `
      + 'every real identity change would be dismissed as a drift');
    assert.ok(IDENTITY_SIGMA_DRIFT_MAX < 2.0,
      `${IDENTITY_SIGMA_DRIFT_MAX} reaches the 2.0x drift that convicted 36 of 36 genuine `
      + 'wearers — the guard would not fire on the thing it exists for');
  });
});

describe('the sigma the APP feeds this watch, which no other test here supplies', () => {
  /**
   * Every other measurement in this file hands the watch `f.sigmaPx` — the
   * synthetic harness's own noise model. The app does not: it calls
   * `estimateSigma`, which rasterises the mesh for self-occlusion and carries a
   * per-landmark EMA of unexplained motion. The two are different quantities,
   * and the difference is the whole of the retirement's behaviour.
   *
   * That gap is why a one-frame retirement survived to ship. It is also why the
   * population arm above cannot see this: its stream has no disagreement term.
   */
  const mesh = parseFaceObj(readFileSync(templatePath(), 'utf8'));
  const basis = buildAnthropometricBasis(mesh);
  const geometry = CAMERA_LADDER[0];
  const k = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);

  /** `meanSigmaPx` per frame, exactly as `main.ts` computes it in the wear phase. */
  function sigmaTrace(subject: any, capture: Partial<Record<string, unknown>> = {}) {
    const shot = synthesizeCapture(mesh, subject, geometry, { seed: 11, ...capture } as any);
    const state = createUncertainty(mesh.vertexCount);
    const out: { yaw: number; pitch: number; meanSigmaPx: number }[] = [];
    let previous = null as any;
    for (const f of shot.frames) {
      // Against the PREVIOUS pose, which is what `app.lastPose` holds.
      const sigmaPx = previous
        ? estimateSigma(state, {
          landmarks: f.landmarks, mesh, positions: subject.positions,
          intrinsics: k, pose: previous, pixelScale: 1,
        }).sigmaPx
        : new Float64Array(mesh.vertexCount).fill(UNCERTAINTY_DEFAULTS.floorPx);
      let sum = 0, n = 0;
      for (let i = 0; i < sigmaPx.length; i++) {
        if (Number.isFinite(sigmaPx[i])) { sum += sigmaPx[i]; n++; }
      }
      out.push({ yaw: f.trueYaw, pitch: f.truePitch, meanSigmaPx: n ? sum / n : NaN });
      previous = f.pose;
    }
    // The watch is armed after enrolment, so the acquisition frame — the one
    // with no previous pose and a flat floor — is never in its learning set.
    return out.slice(1);
  }

  /** Runs the watch over a trace with the wearer pinned, so only sigma can act. */
  function watchOver(rows: ReturnType<typeof sigmaTrace>) {
    const watch = createIdentityWatch();
    armWearer(watch);
    for (const r of rows) {
      observeIdentity(watch, {
        solved: true, varianceFactor: 1, correspondences: 468,
        meanSigmaPx: r.meanSigmaPx, yawRad: r.yaw, pitchRad: r.pitch,
      });
    }
    return watch;
  }

  const subject = generatePopulation(mesh, basis, { count: 3, seed: 11 })[0];

  it('a co-operative session raises real excursions, and none of them retires the wearer', () => {
    // The wearer does the whole protocol and nothing about them changes — the
    // variance factor is pinned at 1, so the only thing that can act is the
    // sigma guard. Before the streak this session retired the reference, and a
    // swap in those frames would have been adopted as the new one.
    // RED: retire on the first excursion.
    const watch = watchOver(sigmaTrace(subject));
    assert.ok(watch.sigmaExcursions > 0,
      'no excursion at all: this fixture no longer exercises the guard, so the '
      + 'assertion below passes for the wrong reason. Re-measure before deleting it.');
    assert.equal(watch.recalibrations, 0,
      `${watch.sigmaExcursions} excursion frames retired the wearer ${watch.recalibrations} `
      + 'times on a session where nothing about them changed');
    assert.ok(Number.isFinite(watch.reference), 'the wearer was thrown away');
  });

  it('and the excursion needs the wearer to LEAVE the asked band and come back', () => {
    // The mechanism, pinned. The disagreement EMA inflates on frames the watch
    // never sees — past IDENTITY_MAX_YAW_DEG — and the first frames back inside
    // the band carry that inflation against a reference learned within it.
    //
    // Measured over four subjects at eye-level: with the profile beats the
    // per-frame ratio reaches 1.969 and crosses the 1.6 bar; with them removed
    // it peaks at 1.471 and never does. A turn that stays inside the band
    // cannot trip this guard, which is why a fixture built only of small turns
    // reports the excursion as unreachable.
    // RED: none — this is a property of the fixture pair and it is what makes
    // the test above meaningful. If it stops holding, the mechanism has moved.
    const inBand = watchOver(sigmaTrace(subject, { includeProfile: false }));
    assert.equal(inBand.sigmaExcursions, 0,
      `a protocol that never leaves the asked band raised ${inBand.sigmaExcursions} `
      + 'excursions — the mechanism is no longer the one documented in observeIdentity');
    assert.equal(inBand.recalibrations, 0);
  });
});
