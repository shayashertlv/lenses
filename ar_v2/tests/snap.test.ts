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
  CalibrationField, occludingContour, snapOffsets, contourPushes, SNAP_DEFAULTS,
  SNAP_SLEW_MM_PER_S,
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
    assert.ok(claimed <= samples.length / 4,
      `${claimed}/${samples.length} samples claimed edges in structureless noise`);
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

  it('refuses an outlier that disagrees with a converged estimate', () => {
    // A hand crossing the face is a confident, wildly-wrong edge. Once the
    // field knows this vertex, a 3 mm-off observation must bounce.
    const field = new CalibrationField(2);
    for (let i = 0; i < 20; i++) field.update([push(0, 1)]);
    const before = field.correction[0];
    const absorbed = field.update([push(0, 1 + 3)]);
    assert.equal(absorbed, 0, 'the outlier was absorbed');
    assert.equal(field.correction[0], before);
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
