/**
 * The occlusion-boundary snapper, proven on edges it cannot have memorised.
 *
 * The module's one job is to move a predicted boundary onto the image's real
 * edge and to KNOW WHEN IT CANNOT. So the tests here are recovery tests with
 * known offsets — inject a bias, demand it back — and abstention tests,
 * because a snapper that cannot refuse hallucinates boundaries in flat light,
 * which is strictly worse than the geometric baseline it corrects.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  CALIBRATION_DEFAULTS, CalibrationField, occludingContour, snapOffsets,
  snappedContourPoints, contourPushes, SNAP_DEFAULTS, SNAP_SLEW_MM_PER_S,
} from '../src/track/snap.js';
import { createDepthBuffer, rasterize, clearDepthBuffer } from '../src/core/raster.js';
import type { Intrinsics } from '../src/core/camera.js';

const K: Intrinsics = { f: 400, cx: 128, cy: 128, k1: 0, width: 256, height: 256 };

/** A synthetic image with a vertical luminance edge at x = edgeX: dark left,
 *  light right, softened over ~2 px the way a real sensor renders one. */
const verticalEdge = (edgeX: number) => (x: number, _y: number): number => {
  const t = (x - edgeX) / 2;
  return 128 + 100 * Math.tanh(t);
};

describe('the snapper recovers a known edge offset', () => {
  // Predicted contour: a vertical line of samples at x = 100, normals +x.
  const samples = Array.from({ length: 24 }, (_, i) => ({
    x: 100, y: 80 + i * 4, nx: 1, ny: 0, depthMm: 450,
  }));

  it('finds an edge exactly where the prediction is, and says so', () => {
    const snap = snapOffsets(samples, verticalEdge(100));
    for (let i = 0; i < samples.length; i++) {
      assert.ok(snap.confidence[i] > 0.3, `sample ${i} abstained on a clean edge`);
      assert.ok(Math.abs(snap.offsetPx[i]) < 0.35,
        `edge at the prediction, but offset ${snap.offsetPx[i].toFixed(2)} px`);
    }
  });

  it('recovers a 3 px bias to a third of a pixel — the whole point', () => {
    // The image's edge sits 3 px farther out than the geometry predicted:
    // exactly the real-wearer failure (rim drawn over the photographed nose).
    const snap = snapOffsets(samples, verticalEdge(103));
    for (let i = 0; i < samples.length; i++) {
      assert.ok(snap.confidence[i] > 0.3, `sample ${i} abstained`);
      assert.ok(Math.abs(snap.offsetPx[i] - 3) < 0.35,
        `injected +3 px, recovered ${snap.offsetPx[i].toFixed(2)} px`);
    }
  });

  it('recovers the offset in both directions and along a diagonal normal', () => {
    // Samples strung ALONG the anti-diagonal edge direction, so every one of
    // them sees the same edge at the same along-normal distance. (The first
    // version of this fixture marched the samples down the diagonal AWAY from
    // the edge — sample 15 sat 24 px out, past any search band — which is a
    // fixture bug, not a module behaviour worth asserting.)
    const diag = Array.from({ length: 16 }, (_, j) => ({
      x: 100 + j, y: 100 - j, nx: Math.SQRT1_2, ny: Math.SQRT1_2, depthMm: 450,
    }));
    // Edge where (x+y)/2 = 98. Every sample has (x+y)/2 = 100; the gradient of
    // (x+y)/2 along the normal is SQRT1_2, so the zero-cross sits at
    // t = (98 - 100)/SQRT1_2 = -2.83 normal-px for all of them.
    const edge = (x: number, y: number): number =>
      128 + 100 * Math.tanh(((x + y) / 2 - 98) / 2);
    const snap = snapOffsets(diag, edge);
    for (let i = 0; i < diag.length; i++) {
      assert.ok(snap.confidence[i] > 0.3, `diagonal sample ${i} abstained`);
      assert.ok(snap.offsetPx[i] < -2.2 && snap.offsetPx[i] > -3.5,
        `diagonal offset ${snap.offsetPx[i].toFixed(2)} px, expected ~-2.83`);
    }
  });

  it('abstains on flat skin instead of inventing a boundary', () => {
    const flat = () => 140;
    const snap = snapOffsets(samples, flat);
    for (let i = 0; i < samples.length; i++) {
      assert.equal(snap.confidence[i], 0, `sample ${i} claimed an edge in flat light`);
      assert.equal(snap.offsetPx[i], 0);
    }
  });

  it('abstains on noise without structure', () => {
    // Deterministic hash noise, +/-8 around a mean — real sensor grain is
    // smaller, so a snapper fooled by this would be fooled nightly.
    const noise = (x: number, y: number): number => {
      const h = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453;
      return 140 + 8 * ((h - Math.floor(h)) * 2 - 1);
    };
    const snap = snapOffsets(samples, noise, { minGradient: 6 });
    let claimed = 0;
    for (let i = 0; i < samples.length; i++) if (snap.confidence[i] > 0) claimed++;
    // Tightened from `samples.length / 4` = 6 on 2026-08-26. The shipped code
    // sat exactly ON that bar — 6 of 24 — and one of those six was a band-end
    // acceptance at -8.00 px with confidence 0.67, admitted because the ridge
    // gate did not run at the ends of the band. A bar a defect sits exactly on
    // is a bar that cannot see it.
    assert.ok(claimed <= 5,
      `${claimed}/${samples.length} samples claimed edges in structureless noise`);
  });

  it('a one-sample spike at a band end is refused like one anywhere else', () => {
    // `snapOffsets` guarded BOTH its ridge test and its sub-pixel parabola on
    // `bestIdx > 0 && bestIdx < steps - 1`, with no `else` on either. So a peak
    // at either end of the band — 2 of the 17 shipped positions — skipped the
    // gate entirely and was emitted at `offsetPx = +/-searchPx` exactly, the
    // largest offset the module can produce. At 450 mm with f = 587.5 that is
    // 6.13 mm against `contourPushes`' 3 mm cap: a full-cap push in a direction
    // noise chose.
    //
    // The interior form of this fixture was already refused; the end form was
    // not. That asymmetry is the whole finding, in one line.
    const s = [{ x: 100, y: 100, nx: 1, ny: 0, depthMm: 450 }];
    const spikeAt = (t0: number) => (x: number): number => {
      const t = x - 100;                       // along-normal, source px
      return 140 + (Math.abs(t - t0) < 0.5 ? 60 : 0);
    };
    for (const t0 of [-SNAP_DEFAULTS.searchPx, 0, SNAP_DEFAULTS.searchPx]) {
      const r = snapOffsets(s, spikeAt(t0));
      assert.equal(r.confidence[0], 0,
        `a lone spike at t=${t0} was accepted at ${r.offsetPx[0]} px`);
    }
  });

  it('a real edge AT the band edge still gets through — one-sided, not rejected', () => {
    // The reason the fix gates one-sided instead of rejecting band-end peaks
    // outright. A real edge arrives as a RAMP a couple of pixels wide, so its
    // inner neighbour carries most of the peak's response and passes the same
    // 0.45 test. Rejecting would throw away every genuine snap from about
    // 5.7 mm of geometric error outward — which is the LARGEST error the snap
    // exists to correct.
    const samples = Array.from({ length: 12 }, (_, i) => ({
      x: 100, y: 80 + i * 4, nx: 1, ny: 0, depthMm: 450,
    }));
    const atEdge = snapOffsets(samples, verticalEdge(100 + SNAP_DEFAULTS.searchPx));
    let kept = 0;
    for (let i = 0; i < samples.length; i++) if (atEdge.confidence[i] > 0) kept++;
    assert.equal(kept, samples.length,
      `only ${kept}/${samples.length} kept a real edge sitting exactly at the band edge`);
    for (let i = 0; i < samples.length; i++) {
      assert.ok(atEdge.offsetPx[i] > SNAP_DEFAULTS.searchPx - 0.5,
        `the band-edge offset came back at ${atEdge.offsetPx[i].toFixed(2)}, not the clamp`);
    }
  });
});

describe('the snapped contour is handed on as the edge the IMAGE put there', () => {
  // The enrolment's silhouette term wants image points, not offsets. Getting
  // this wrong in the obvious direction — emitting every sample, abstentions
  // included, at its geometric position — would hand the bundle its own
  // prediction back as evidence, which is the failure this whole module is
  // written against.
  const samples = Array.from({ length: 24 }, (_, i) => ({
    x: 100, y: 80 + i * 4, nx: 1, ny: 0, depthMm: 450,
  }));

  it('puts each point where the offset says, along that sample own normal', () => {
    const snap = snapOffsets(samples, verticalEdge(103));
    const pts = snappedContourPoints(samples, snap);
    assert.equal(pts.length, 2 * samples.length, 'a clean edge should leave nothing out');
    for (let i = 0; i < samples.length; i++) {
      assert.ok(Math.abs(pts[i * 2] - 103) < 0.4,
        `point ${i} landed at x=${pts[i * 2].toFixed(2)} for an edge at 103`);
      assert.equal(pts[i * 2 + 1], samples[i].y, 'the normal is +x, so y must not move');
    }
  });

  it('follows a diagonal normal rather than the axes', () => {
    // Same fixture as the diagonal recovery test above: the edge is at
    // (x+y)/2 = 98 and every sample sits at (x+y)/2 = 100, so each point must
    // move by ~-2.83 ALONG (SQRT1_2, SQRT1_2) — which lands it on the edge.
    const diag = Array.from({ length: 16 }, (_, j) => ({
      x: 100 + j, y: 100 - j, nx: Math.SQRT1_2, ny: Math.SQRT1_2, depthMm: 450,
    }));
    const edge = (x: number, y: number): number =>
      128 + 100 * Math.tanh(((x + y) / 2 - 98) / 2);
    const pts = snappedContourPoints(diag, snapOffsets(diag, edge));
    assert.equal(pts.length, 2 * diag.length);
    for (let i = 0; i < diag.length; i++) {
      const mid = (pts[i * 2] + pts[i * 2 + 1]) / 2;
      assert.ok(Math.abs(mid - 98) < 0.5,
        `point ${i} sits on (x+y)/2 = ${mid.toFixed(2)}, not on the edge at 98`);
      // An axis-aligned shortcut would leave y (or x) untouched. Both moved.
      assert.ok(Math.abs(pts[i * 2] - diag[i].x) > 1.5);
      assert.ok(Math.abs(pts[i * 2 + 1] - diag[i].y) > 1.5);
    }
  });

  it('emits nothing at all where the snapper abstained', () => {
    // Flat light: every sample refuses. An abstention is NOT "the edge is
    // exactly where the geometry put it", and a silhouette built from the
    // geometric positions would be the template's own contour dressed up as an
    // observation — the bundle would then fit the template it started from.
    const flat = snappedContourPoints(samples, snapOffsets(samples, () => 140));
    assert.equal(flat.length, 0,
      `${flat.length / 2} points were emitted for a band with no edge in it`);

    // **INTERLEAVED, and that is the whole fixture.** The first version of this
    // put the edge over the first twelve samples and the flat skin over the
    // last twelve, then asserted the output LENGTH. Both of those are wrong in
    // the same way: the length is decided by the counting pass, so dropping the
    // guard from the WRITING pass left it unchanged, and with the confident
    // samples contiguous at the front the emitted content was unchanged too —
    // the array simply filled up before it reached an abstention. Measured:
    // deleting the guard passed that test. Alternating rows, and an assertion
    // on where each point LANDED, is what makes the guard visible.
    const striped = (x: number, y: number): number =>
      (((y - 80) / 4) % 2 === 0 ? 128 + 100 * Math.tanh((x - 103) / 2) : 140);
    const snap = snapOffsets(samples, striped);
    let confident = 0;
    for (let i = 0; i < samples.length; i++) if (snap.confidence[i] > 0) confident++;
    assert.ok(confident > 0 && confident < samples.length,
      `${confident}/${samples.length} confident — this fixture cannot tell the stripes apart`);
    const pts = snappedContourPoints(samples, snap);
    assert.equal(pts.length, 2 * confident);
    for (let i = 0; i < pts.length; i += 2) {
      assert.ok(Math.abs(pts[i] - 103) < 0.5,
        `an emitted point sits at x=${pts[i].toFixed(2)} — that is the geometric `
        + 'prediction at 100, not an observed edge at 103, so an abstention was emitted');
    }
  });
});

describe('the occluding contour includes interior depth edges', () => {
  it('finds the near ridge in front of a far plane — the nose-at-yaw shape', () => {
    // Two quads: a far plane filling the view and a near strip down the
    // middle, 25 mm nearer — a cartoon nose in front of a cartoon cheek. The
    // mask boundary silhouette cannot see this edge; the occluding contour
    // must.
    const positions = new Float64Array([
      // far plane (z = 500)
      -80, -80, 500, 80, -80, 500, 80, 80, 500, -80, 80, 500,
      // near strip (z = 475), x in [-10, 10]
      -10, -80, 475, 10, -80, 475, 10, 80, 475, -10, 80, 475,
    ]);
    const indices = new Uint32Array([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]);
    const K2: Intrinsics = { f: 400, cx: 64, cy: 64, k1: 0, width: 128, height: 128 };
    const buffer = createDepthBuffer(128, 128, K2);
    clearDepthBuffer(buffer);
    rasterize(buffer, positions, indices, 8, {
      R: Float64Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1),
      t: Float64Array.of(0, 0, 0),
    }, K2);
    const contour = occludingContour(buffer, { jumpMm: 6, stride: 1 });
    assert.ok(contour.length > 10, `only ${contour.length} contour samples`);
    // The strip's edges project to x = cx +- f*10/475 ~ 64 +- 8.4. Every
    // interior-edge sample must hug one of them, with a normal pointing away
    // from the strip.
    for (const s of contour) {
      const nearLeft = Math.abs(s.x - (K2.cx - (K2.f * 10) / 475));
      const nearRight = Math.abs(s.x - (K2.cx + (K2.f * 10) / 475));
      assert.ok(Math.min(nearLeft, nearRight) < 3,
        `contour sample at x=${s.x.toFixed(1)} is not on either strip edge`);
      assert.ok(Math.abs(s.depthMm - 475) < 2, `near-side depth ${s.depthMm}`);
      assert.ok(Math.abs(s.nx) > 0.7, 'edge normal should be mostly horizontal');
    }
  });
});

describe('contour pushes reach the right vertices with the right units', () => {
  it('pushes a vertex by the snapped millimetres, capped, in face space', () => {
    // One vertex at the camera axis, 400 mm out; one contour sample right on
    // top of it, snapped +4 px along +x with full confidence. At f=400 and
    // z=400, one px is one mm — so the push must be +4 mm... except the cap
    // is 3, so it must be exactly the cap. The pose is identity, so face
    // space and camera space agree and the whole unit chain is legible.
    const positions = new Float64Array([0, 0, 400]);
    const pose = {
      R: Float64Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1),
      t: Float64Array.of(0, 0, 0),
    };
    const samples = [{ x: K.cx, y: K.cy, nx: 1, ny: 0, depthMm: 400 }];
    const snap = { offsetPx: Float64Array.of(4), confidence: Float64Array.of(1) };
    const pushes = contourPushes(samples, snap, positions, 1, pose, K, { capMm: 3 });
    assert.equal(pushes.length, 1);
    assert.equal(pushes[0].vertex, 0);
    assert.ok(Math.abs(pushes[0].dx - 3) < 1e-9,
      `push ${pushes[0].dx} mm — the 4 mm ask must hit the 3 mm cap`);
    assert.ok(Math.abs(pushes[0].dy) < 1e-9 && Math.abs(pushes[0].dz) < 1e-9);
  });

  it('a vertex outside the gather radius is untouched', () => {
    const positions = new Float64Array([30, 0, 400]); // ~30 px away in image
    const pose = {
      R: Float64Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1),
      t: Float64Array.of(0, 0, 0),
    };
    const samples = [{ x: K.cx, y: K.cy, nx: 1, ny: 0, depthMm: 400 }];
    const snap = { offsetPx: Float64Array.of(4), confidence: Float64Array.of(1) };
    const pushes = contourPushes(samples, snap, positions, 1, pose, K, { gatherPx: 6 });
    assert.equal(pushes.length, 0);
  });

  it('zero confidence produces zero pushes — abstention propagates', () => {
    const positions = new Float64Array([0, 0, 400]);
    const pose = {
      R: Float64Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1),
      t: Float64Array.of(0, 0, 0),
    };
    const samples = [{ x: K.cx, y: K.cy, nx: 1, ny: 0, depthMm: 400 }];
    const snap = { offsetPx: Float64Array.of(4), confidence: Float64Array.of(0) };
    assert.equal(
      contourPushes(samples, snap, positions, 1, pose, K).length, 0,
      'an abstained sample still pushed a vertex',
    );
  });
});

describe('the calibration field is a constant of the face, not of the frame', () => {
  const push = (v: number, dx: number) => ({ vertex: v, dx, dy: 0, dz: 0 });

  it('converges to the mean of noisy observations and then stops moving', () => {
    // The first real wearer's finding, as an assertion: "if the user doesn't
    // move, the edge of the glasses will" was the per-frame EMA leaking frame
    // noise into a static boundary. The field must do the opposite — absorb
    // sixty noisy observations of a 2 mm truth, land near 2, and be so heavy
    // by then that one more observation moves it by less than 0.05 mm.
    const field = new CalibrationField(4);
    let seed = 7;
    const noise = () => {
      seed = (seed * 16807) % 2147483647;
      return ((seed / 2147483647) - 0.5) * 1.2; // +/-0.6 mm of frame noise
    };
    for (let i = 0; i < 60; i++) field.update([push(1, 2 + noise())]);
    const settled = field.correction[3];
    assert.ok(Math.abs(settled - 2) < 0.35, `converged to ${settled.toFixed(2)}, truth 2`);
    field.update([push(1, 2 + 0.6)]);
    assert.ok(Math.abs(field.correction[3] - settled) < 0.05,
      `a converged vertex moved ${Math.abs(field.correction[3] - settled).toFixed(3)} mm ` +
      'on one observation — the boundary would still breathe at rest');
    assert.ok(field.convergence() > 0.9, `convergence ${field.convergence()}`);
  });

  it('an outlier barely moves a converged estimate, however far out it is', () => {
    // A hand crossing the face is a confident, wildly-wrong edge. Once the
    // field knows this vertex, such an observation must not be able to take it.
    //
    // This used to assert the outlier BOUNCED — `absorbed === 0`, correction
    // bit-identical — because the field refused anything past `agreementMm`
    // outright. That hard refusal is what `core/robust.ts`'s header argues
    // against in this tree's own words, and here it was worse than a boundary
    // effect: the estimate the gate tested against was one the gate had built,
    // so once the field latched onto anything, the observations that would pull
    // it back out were exactly the ones it refused. Measured, a hand arriving
    // across frames 2-9 — before the estimate settled — was LATCHED, and the
    // p90 error came out at 1.926 mm against 0.378 for the rule that ships now.
    //
    // What replaces it is Huber's own weight, and the property to hold it to is
    // BOUNDED INFLUENCE rather than refusal.
    const field = new CalibrationField(2);
    for (let i = 0; i < 20; i++) field.update([push(0, 1)]);
    const before = field.correction[0];

    // (a) A 3 mm-off observation moves a converged estimate by well under a
    //     tenth of a millimetre. It is absorbed, and that is the point: it is
    //     absorbed at 1.5/3 of a frame's weight, out of a cap of 15.
    const moved = new CalibrationField(2);
    for (let i = 0; i < 20; i++) moved.update([push(0, 1)]);
    moved.update([push(0, 1 + 3)]);
    const step3 = moved.correction[0] - before;
    assert.ok(step3 > 0 && step3 < 0.15,
      `one 3 mm outlier moved a converged estimate by ${step3.toFixed(4)} mm`);

    // (b) **Bounded influence, which is the property that makes this a robust
    //     loss rather than a soft gate.** The estimate moves by
    //     `c/(w+c) * d` with `c = agreementMm/d`, i.e. `agreementMm/(w + c)` —
    //     so as the observation goes to infinity the move rises to a CEILING of
    //     `agreementMm / weightCap` = 1.5/15 = 0.1 mm and stops. It saturates;
    //     it does not fall away. (A redescending loss would fall away, and
    //     `robust.ts`'s `cauchy` docstring says why that is not wanted where an
    //     estimate can lock onto a bad basin.)
    //
    //     Written as a bound and not as an ordering, because the first draft of
    //     this assertion had it backwards — it demanded the 30 mm outlier move
    //     the estimate LESS than the 3 mm one, and Huber does the opposite:
    //     0.0997 against 0.0968, both under the ceiling.
    const CEILING = CALIBRATION_DEFAULTS.agreementMm / CALIBRATION_DEFAULTS.weightCap;
    const wild = new CalibrationField(2);
    for (let i = 0; i < 20; i++) wild.update([push(0, 1)]);
    wild.update([push(0, 1 + 30)]);
    const step30 = wild.correction[0] - before;
    assert.ok(step30 < CEILING + 1e-9,
      `a 30 mm outlier moved a converged estimate ${step30.toFixed(4)} mm, past the `
      + `${CEILING.toFixed(4)} mm ceiling agreementMm/weightCap sets — influence is not `
      + 'bounded, so this is a plain down-weighting and not a robust loss');

    // And the ceiling has to be worth having: with no agreement term at all the
    // same observation moves it `30/(15+1)` = 1.875 mm, nineteen times further.
    assert.ok(30 / (CALIBRATION_DEFAULTS.weightCap + 1) > 10 * step30,
      'an ungated update would move the estimate by a similar amount, so this '
      + 'assertion is not measuring the agreement term at all');
  });

  it('does not latch an outlier that arrives before the estimate exists', () => {
    // **The defect the hard gate had, in one fixture.** The gate armed at
    // `weight > 3` — about six frames — and then refused anything more than
    // `agreementMm` from whatever it had. So a wrong observation arriving in
    // those first frames was not rejected: it became the estimate, and every
    // correct observation afterwards was refused for disagreeing with it.
    //
    // Four bad frames, then forty good ones. The truth is 1.0.
    const field = new CalibrationField(2);
    for (let i = 0; i < 4; i++) field.update([push(0, 6)]);
    for (let i = 0; i < 40; i++) field.update([push(0, 1)]);
    assert.ok(Math.abs(field.correction[0] - 1) < 0.5,
      `after four bad frames and forty good ones the field sits at `
      + `${field.correction[0].toFixed(3)} against a truth of 1.0 — it has latched the `
      + 'first thing it saw and is refusing the evidence that would correct it');
  });


  it('the wearer never sees the estimate settle — only a glide', () => {
    // The field's gain is 1 on first evidence and 1/n after, so the ESTIMATE
    // lands its whole correction in one frame and then oscillates down as the
    // average settles. Measured on steady input, the worst vertex moved
    // 5.6 mm on frame one, then 0.65, 0.42, 0.32, 0.22, quiet by frame twelve.
    // That is the occluder — the mask deciding where the face hides the
    // glasses — visibly reshaping them for a third of a second, and it is what
    // the first wearer to notice it called "a wobble until they get steady".
    //
    // The estimator is right; showing every intermediate value of it is not.
    // `applied` walks toward `correction` at a bounded rate, so what is drawn
    // glides instead of settling.
    const field = new CalibrationField(4);
    const target = 5;
    const step = SNAP_SLEW_MM_PER_S / 30;

    // Fixture sanity: the raw estimate must genuinely jump, or nothing here
    // convicts anything.
    field.update([push(0, target)]);
    assert.ok(Math.abs(field.correction[0]) > 1,
      `the estimate moved only ${field.correction[0].toFixed(3)} mm on first evidence — ` +
      'the fixture does not exhibit the jump this slew exists for');

    // The applied field may never move faster than the cap, on any frame.
    let previous = 0;
    let frames = 0;
    for (let i = 0; i < 60; i++) {
      field.update([push(0, target)]);
      const applied = field.advance(1 / 30)[0];
      const moved = Math.abs(applied - previous);
      assert.ok(moved <= step + 1e-9,
        `the applied correction moved ${moved.toFixed(4)} mm in one frame, past the ` +
        `${step.toFixed(4)} mm cap — the wearer can see that`);
      previous = applied;
      if (Math.abs(applied - field.correction[0]) < 1e-6) { frames = i + 1; break; }
    }
    // ...and it must actually arrive, or the glide is a leak.
    assert.ok(frames > 0,
      `the applied correction never reached the estimate (${previous.toFixed(3)} vs ` +
      `${field.correction[0].toFixed(3)} mm) — the slew is not converging`);
    assert.ok(Math.abs(field.applied[0] - field.correction[0]) < 1e-6,
      'applied and correction disagree after convergence');
    // The glide is longer than the settle it replaces, and that is the trade:
    // ~0.7 s of invisible movement against ~0.4 s of visible movement.
    assert.ok(frames >= target / step - 2,
      `it arrived in ${frames} frames, faster than the rate cap allows — the cap is not binding`);
  });

  it('reset forgets everything — a rescan starts a fresh face', () => {
    const field = new CalibrationField(2);
    field.update([push(0, 2)]);
    field.advance(1 / 30);
    field.reset();
    assert.equal(field.correction[0], 0);
    // The APPLIED field too, or a rescan would glide away from the previous
    // face's boundary instead of starting from the new one's geometry.
    assert.equal(field.applied[0], 0, 'reset left the applied correction behind');
    assert.equal(field.convergence(), 0);
  });
});

describe('the contour sample sits on the boundary, not on the last drawn pixel', () => {
  // Production geometry, because the defect is invisible at scale 1: a 224-px
  // buffer against 1280-px intrinsics makes one buffer pixel 5.714 source px,
  // which is 4.38 mm at 450 mm. `rasterize` draws a pixel only when its CENTRE
  // is covered, so the last drawn pixel's centre lies 0..1 buffer px INSIDE the
  // true silhouette; reporting it verbatim hands `snapOffsets` a reference that
  // is always inward and makes every measured offset read outward by half a
  // buffer pixel on average. `CalibrationField` then freezes that as wearer
  // geometry.
  //
  // The truth here is ANALYTIC — the projected edge of a flat quad is a line in
  // closed form — so the assertion is not graded against another rasterisation
  // carrying the same bias. Both cases sweep the edge across a whole buffer
  // pixel of grid phase, because at any single phase the gap is whatever that
  // phase happens to be; only the mean over the sweep is the bias.
  const KP: Intrinsics = { f: 587.5, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 };
  const IDENTITY = {
    R: Float64Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1),
    t: Float64Array.of(0, 0, 0),
  };
  const Z = 450;
  const bufferAt = () => createDepthBuffer(224, Math.round((224 * KP.height) / KP.width), KP);
  const srcPerBufferPx = 1280 / 224;
  const PHASES = 32;
  /** Camera-mm step that moves the projected edge exactly one buffer pixel. */
  const oneBufferPxMm = (srcPerBufferPx * Z) / KP.f;

  /**
   * 0.30 source px, and the looser 0.6 it replaced was not a guard.
   *
   * The shift is exactly linear in the correction, so the pass window for a
   * FLAT constant `h` buffer px is analytic: |−2.81 + 5.714h| < T on the axis
   * fixture and |−2.01 + 5.714h| < T on the 45-degree one. Those windows
   * overlap whenever T > 0.398 — so at 0.6 a flat `h = 0.45`, a constant with
   * no normal dependence at all, passes the test whose own title says the
   * half-pixel is along the NORMAL and not the axis. `0.5*max(...)**2` escaped
   * by 0.017 px, and the derived law's only non-trivial content IS that
   * exponent. At 0.30 the windows are disjoint — axis (0.439, 0.544) against
   * diagonal (0.302, 0.407) — so no flat constant passes, the squared law
   * fails at −0.583, and the shipped policy still clears with ~1.4x margin
   * across buffer widths 64–448, triangle sizes 20–100 and 16–64 phases.
   */
  const TOL = 0.30;

  it('an axis-aligned silhouette is reported on the edge, not half a pixel inside', () => {
    const buffer = bufferAt();
    const errs: number[] = [];
    // 32 phases across one buffer pixel, expressed as the quad's right edge in
    // camera mm. One buffer px is 5.714 source px = 4.377 mm at this depth.
    // Load-bearing, and asserted rather than assumed: the sweep must cover
    // exactly one buffer pixel of grid phase. At half the span the shipped
    // policy reads +1.08 source px and this test fails — the mean is the bias
    // only when every phase is equally represented.
    assert.ok(Math.abs((oneBufferPxMm * KP.f) / Z / srcPerBufferPx - 1) < 1e-9,
      'the phase sweep must span exactly one buffer pixel');
    for (let p = 0; p < PHASES; p++) {
      const edgeX = 20 + (p / PHASES) * oneBufferPxMm;
      const positions = new Float64Array([
        -60, -60, Z, edgeX, -60, Z, edgeX, 60, Z, -60, 60, Z,
      ]);
      const indices = new Uint32Array([0, 1, 2, 0, 2, 3]);
      rasterize(buffer, positions, indices, 4, IDENTITY, KP);
      const trueEdgePx = KP.cx + (KP.f * edgeX) / Z;
      // `stride: 1`, not the wear phase's 2. A vertical edge occupies ONE
      // buffer column at a time, so a stride that keeps only odd columns keeps
      // either every phase's samples or none of them — the subsample would be
      // locked to the phase this test sweeps. The convention under test does
      // not depend on stride; the sampling of it must not either.
      for (const s of occludingContour(buffer, { jumpMm: 6, stride: 1 })) {
        if (s.nx !== 1 || s.ny !== 0) continue;         // the right edge only
        if (Math.abs(s.x - trueEdgePx) > 2 * srcPerBufferPx) continue;
        errs.push(s.x - trueEdgePx);                    // negative = inside
      }
    }
    assert.ok(errs.length > 100, `only ${errs.length} right-edge samples`);
    const mean = errs.reduce((a, b) => a + b, 0) / errs.length;
    assert.ok(Math.abs(mean) < TOL,
      `mean reported position is ${mean.toFixed(2)} source px from the true silhouette `
      + `(${(mean / srcPerBufferPx).toFixed(3)} buffer px); a systematic inward reference `
      + 'is read back out as scan error');
    // Not `worst < srcPerBufferPx`: a flagged pixel's distance to the boundary
    // is uniform on (-1, 0) buffer px, so with any shift h in [0, 1] the worst
    // sample is max(h, 1-h) buffer px and that bar cannot fire — the defect
    // itself clears it by 2.4%. Scaled to something the defect fails (5.58)
    // and the shipped policy clears with room (2.82).
    const worst = Math.max(...errs.map(Math.abs));
    assert.ok(worst < 0.75 * srcPerBufferPx,
      `worst sample ${worst.toFixed(2)} source px out`);
  });

  it('a 45-degree silhouette too — the half-pixel is along the NORMAL, not the axis', () => {
    // The correction is `max(|nx|,|ny|) / 2` buffer px, not a flat half: a
    // flagged pixel's centre is uniform over that distance from the boundary,
    // and on a diagonal normal the neighbour that crosses first only advances
    // 0.707 px along it. A flat 0.5 overshoots here by 0.146 buffer px =
    // -0.83 source px, and the axis-aligned case above cannot see that.
    const buffer = bufferAt();
    const errs: number[] = [];
    for (let p = 0; p < PHASES; p++) {
      const c = 20 + (p / PHASES) * oneBufferPxMm;
      // Right triangle with a 45-degree hypotenuse from (c,-60) to (-60,c):
      // the boundary is X + Y = c - 60, outward normal (1,1)/sqrt(2).
      const positions = new Float64Array([-60, -60, Z, c, -60, Z, -60, c, Z]);
      const indices = new Uint32Array([0, 1, 2]);
      rasterize(buffer, positions, indices, 3, IDENTITY, KP);
      // (x - cx) + (y - cy) = (c - 60) * f / Z  is the boundary in source px.
      const C = KP.cx + KP.cy + ((c - 60) * KP.f) / Z;
      // Same reason as above, sharper here: on a 45-degree staircase the
      // boundary pixels satisfy y = x + k, so demanding both odd keeps ALL of
      // them for even k and NONE for odd.
      for (const s of occludingContour(buffer, { jumpMm: 6, stride: 1 })) {
        if (!(s.nx > 0.7 && s.ny > 0.7)) continue;      // the hypotenuse only
        const along = (s.x + s.y - C) / Math.SQRT2;     // negative = inside
        if (Math.abs(along) > 2 * srcPerBufferPx) continue;
        errs.push(along);
      }
    }
    assert.ok(errs.length > 100, `only ${errs.length} hypotenuse samples`);
    const mean = errs.reduce((a, b) => a + b, 0) / errs.length;
    assert.ok(Math.abs(mean) < TOL,
      `mean reported position is ${mean.toFixed(2)} source px from the 45-degree silhouette `
      + `(${(mean / srcPerBufferPx).toFixed(3)} buffer px)`);
  });

  it('an interior peak emits the same point either way — the only case the algebra covers', () => {
    // `snappedContourPoints` emits `s.x + nx*t`, so moving the band's centre
    // moves `t` by the same amount and cancels. That is why the enrolment
    // silhouette never exposed the bias — but the cancellation holds only for
    // a peak INTERIOR to the band that clears the confidence gate in BOTH
    // arms, which is exactly what this case constructs. It is not a general
    // no-op, and the next case is the part that does move.
    const samples = [{ x: 100, y: 120, nx: 1, ny: 0, depthMm: 450 }];
    const shifted = [{ x: 100 + 2.86, y: 120, nx: 1, ny: 0, depthMm: 450 }];
    const image = verticalEdge(103);
    const a = snappedContourPoints(samples, snapOffsets(samples, image));
    const b = snappedContourPoints(shifted, snapOffsets(shifted, image));
    assert.equal(a.length, 2);
    assert.equal(b.length, 2);
    assert.ok(Math.abs(a[0] - b[0]) < 0.25,
      `the emitted image point moved ${(b[0] - a[0]).toFixed(3)} px when only the `
      + 'band centre moved');
  });

  it('and the band it walks becomes symmetric about the prediction', () => {
    // The consequence the case above cannot see. `searchPx` is 8 source px
    // about the REPORTED position, so with the old reference sitting 2.86 px
    // inside the boundary the band reached 10.9 px to one side of the truth
    // and 5.1 px to the other: a scan error outward was refused three px
    // sooner than the same error inward, on every sample, for no reason but
    // the raster convention. Centring the reference on the boundary makes the
    // reach symmetric, which is what a two-sided error deserves.
    const truth = 200;            // where the geometry says the boundary is
    // `e` is the real scan-vs-image error: the reference stays put and the
    // IMAGE edge moves, which is the way round production sees it.
    const reach = (refAt: number): number[] => {
      const found: number[] = [];
      for (let e = -12; e <= 12; e++) {
        const s = [{ x: refAt, y: 120, nx: 1, ny: 0, depthMm: 450 }];
        const pts = snappedContourPoints(s, snapOffsets(s, verticalEdge(truth + e)));
        if (pts.length === 2 && Math.abs(pts[0] - (truth + e)) < 0.5) found.push(e);
      }
      return found;
    };
    // The old convention reported the boundary 0.5 buffer px inside it.
    const halfBufferPx = 0.5 * (1280 / 224);
    const old = reach(truth - halfBufferPx);
    const now = reach(truth);
    assert.ok(old.length > 0 && now.length > 0, 'neither arm recovered anything');
    const skew = (a: number[]) => Math.abs(Math.max(...a) + Math.min(...a));
    assert.ok(skew(now) < skew(old),
      `the reach is no less lopsided than before: ${skew(now)} against ${skew(old)}`);
    assert.ok(skew(now) <= 1,
      `the reach about the prediction is lopsided by ${skew(now)} source px`);
  });
});
