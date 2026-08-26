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
  IDENTITY_REFERENCE_FRAMES, IDENTITY_STRIKES, IDENTITY_VF_RATIO, IDENTITY_WINDOW,
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
import { templatePath } from '../src/testkit/fixtures.js';

/** A frame that qualifies: frontal, solved, plenty of correspondences. */
const frame = (varianceFactor: number, over: Partial<IdentityObservation> = {}): IdentityObservation => ({
  solved: true,
  varianceFactor,
  yawRad: 0,
  pitchRad: 0,
  correspondences: 468,
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

    forgetWearer(watch);
    assert.ok(!Number.isFinite(watch.reference), 'the reference survived a forget');
    assert.equal(watch.armed, false, 'a forgotten wearer left the watch armed');
    assert.equal(watch.strikes, 0);
    assert.equal(watch.convictions, 1, 'the conviction count was reset with the conviction');
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
        const v = observeIdentity(watch, {
          solved: r.tracked && !r.held,
          varianceFactor: r.varianceFactor,
          yawRad: r.euler ? r.euler.yaw : NaN,
          pitchRad: r.euler ? r.euler.pitch : NaN,
          correspondences: r.correspondences,
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
