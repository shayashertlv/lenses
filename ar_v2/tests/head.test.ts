/**
 * The head proxy, and the instrument that could not see it was missing.
 *
 * MediaPipe gives a face that stops at the silhouette — rearmost vertex 24.4 mm
 * back on this template — while a temple runs to an ear rest 96 mm back. **72 mm
 * of a 96 mm arm was drawn against nothing.**
 *
 * The reason that went unmeasured for so long is the interesting part, and it is
 * what the last test here exists to prevent recurring: `occlusionCell` rasterised
 * BOTH arms with `mesh.indices`, so truth and occluder were the same 468-vertex
 * face. An arm drawn against nothing in both cancelled exactly, and the temple
 * row read 4.57% X-ray while the entire back of the head was absent from the
 * model. The instrument had no way to express a truth that included one.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import {
  PINNA, SKULL_DEPTH_MM, boundaryLoop, buildHeadShell, buildHeadWithEars, buildPinna,
  reloftSkull, skullHalfWidthAt,
} from '../src/core/head.js';
import { createFaceModel, type FaceModel } from '../src/core/facemodel.js';
import { intrinsicsFromFov } from '../src/core/camera.js';
import { earRestPoints, solveSeat } from '../src/fit/contact.js';
import { TEST_FRAMES } from '../src/fit/frame-asset.js';
import { loadBasis, loadRegions, loadTemplateMesh } from '../src/testkit/fixtures.js';
import { CAMERA_LADDER, generatePopulation } from '../src/testkit/synthetic.js';
import {
  framePartNames, frameSampleParts, frameSampleSet, ladderPose, occlusionCell,
  transformSamples,
} from '../src/testkit/report-occlusion.js';

const mesh = loadTemplateMesh();
const regions = loadRegions();
const basis = loadBasis();

const truthModel = (positions: Float64Array): FaceModel => createFaceModel({
  positions: new Float64Array(positions),
  vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
  shapeCoeffs: new Float64Array(0), basisName: 'ground-truth',
  displacementRmsMm: 0, displacementMaxMm: 0,
  intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
  intrinsicsSolved: true,
  scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
  landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
  quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
  pdMm: null, pdSigmaMm: null,
  reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
});

/** Edges used by exactly one triangle. Zero means closed. */
function boundaryEdgeCount(indices: ArrayLike<number>): number {
  const counts = new Map<string, number>();
  const key = (a: number, b: number) => (a < b ? `${a},${b}` : `${b},${a}`);
  for (let t = 0; t + 2 < indices.length; t += 3) {
    const tri = [indices[t], indices[t + 1], indices[t + 2]];
    for (let e = 0; e < 3; e++) {
      const k = key(tri[e], tri[(e + 1) % 3]);
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
  }
  let n = 0;
  for (const v of counts.values()) if (v === 1) n++;
  return n;
}

describe('the face mesh closes into a head', () => {
  it('its boundary is one closed ring, found rather than tabulated', () => {
    const ring = boundaryLoop(mesh.indices);
    assert.equal(ring.length, 36,
      `the boundary is ${ring.length} vertices, not the 36 the loft is built on`);
    assert.equal(new Set(ring).size, ring.length, 'the walk revisited a vertex');
  });

  it('the loft is watertight, and shares the face\'s own vertices', () => {
    const shell = buildHeadShell(mesh.positions, mesh.indices);

    // The whole point: no seam for an arm to appear through.
    assert.equal(boundaryEdgeCount(mesh.indices), 36, 'the face mesh should be open');
    assert.equal(boundaryEdgeCount(shell.indices), 0,
      'the head shell has a hole — the loft is not closing on the rim or the pole');

    // SHARED, not copied. If the loft took a copy of the rim it would stay
    // identical until the first time the head took a measured shape, which is
    // exactly how v1's shadow catcher lost its shadow.
    for (let i = 0; i < mesh.positions.length; i++) {
      assert.equal(shell.positions[i], mesh.positions[i],
        `the shell changed face vertex component ${i} — the seat solved against the original`);
    }

    // And it reaches past the temple.
    let rearmost = Infinity;
    for (let i = 2; i < shell.positions.length; i += 3) {
      if (shell.positions[i] < rearmost) rearmost = shell.positions[i];
    }
    assert.ok(rearmost < -100,
      `the shell reaches only z ${rearmost.toFixed(1)}; the ear rest is at -96 and the `
      + 'temple tip beyond it');
  });

  it('re-lofting restores the skull after the rim moves — the seam guard', () => {
    // The edge snap moves the 36 rim vertices the skull was lofted from, and the
    // skull SHARES them. Leaving it where it was tears a gap behind the ear.
    const shell = buildHeadShell(mesh.positions, mesh.indices);
    const moved = new Float64Array(shell.positions);
    // Push the whole rim outward by 3 mm, as a coarse stand-in for a snap.
    for (const i of shell.ring) moved[i * 3] *= 1.03;

    const before = new Float64Array(moved);
    reloftSkull(moved, shell.ring, shell.faceVertexCount);

    let maxShift = 0;
    for (let i = shell.faceVertexCount * 3; i < moved.length; i++) {
      maxShift = Math.max(maxShift, Math.abs(moved[i] - before[i]));
    }
    assert.ok(maxShift > 0.5,
      `re-lofting moved the skull by at most ${maxShift.toFixed(3)} mm after a 3% rim `
      + 'change — it is not following the rim, so the seam would open');

    // The face itself must be untouched by the re-loft.
    for (let i = 0; i < shell.faceVertexCount * 3; i++) {
      assert.equal(moved[i], before[i], `reloftSkull wrote face vertex component ${i}`);
    }
  });

  it('an ear is an open dish, never a ball', () => {
    // The crevice behind the pinna is where the temple runs. A closed ear fills
    // it in, so the arm ends up INSIDE the head and vanishes — which is exactly
    // what v1's 40 mm ear balls did. An open surface has a boundary; a ball does
    // not, and that is mechanically checkable.
    const pinna = buildPinna();
    assert.ok(boundaryEdgeCount(pinna.indices) >= PINNA.segments,
      'the pinna is closed — it is a ball, and it will fill the crevice the temple '
      + 'runs through');

    // ...and it stands OFF the head rather than lying on it.
    let maxX = -Infinity;
    for (let i = 0; i < pinna.positions.length; i += 3) maxX = Math.max(maxX, pinna.positions[i]);
    assert.ok(Math.abs(maxX - PINNA.standoffMm) < 1e-9,
      `the dish's apex stands ${maxX.toFixed(1)} mm off, not ${PINNA.standoffMm}`);
  });

  it('the skull keeps its width to the ear, which is the arm\'s whole clearance', () => {
    const shell = buildHeadShell(mesh.positions, mesh.indices);
    let ringHalf = 0;
    for (const i of shell.ring) ringHalf = Math.max(ringHalf, Math.abs(shell.positions[i * 3]));
    const atEar = skullHalfWidthAt(shell, -41.7);
    assert.ok(atEar / ringHalf > 0.97,
      `the skull is ${(100 * atEar / ringHalf).toFixed(1)}% of the ring's width at the ear. `
      + 'A circular sweep gives 93.1% and the missing 5 mm is the arm\'s whole clearance.');
    // ...and it does close.
    assert.ok(skullHalfWidthAt(shell, shell.ringCentre[2] - SKULL_DEPTH_MM) < 1,
      'the skull never closes at the occiput');
  });
});

describe('the head proxy earns its place, and the instrument can see it', () => {
  it('a temple X-rays through a headless occluder at yaw, and not through a head', () => {
    // **Non-vacuity as a rule.** The same face, the same frame, the same pose:
    // only the occluding surface changes. Before this, truth and occluder were
    // both the plain face mesh, so the missing head cancelled exactly and the
    // instrument reported 4.57% temple X-ray while the whole back of the head
    // was absent from the model.
    //
    // RED: drop `indices` from `OcclusionArm` and rasterise both arms with
    // `mesh.indices` again — the "without" column collapses to the "with"
    // column and this assertion fails, because that is precisely the blindness
    // it exists to prevent.
    const geometry = CAMERA_LADDER[0];
    const k = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
    const frame = TEST_FRAMES[1];
    const raw = frameSampleSet(frame);
    const parts = frameSampleParts(frame);
    const templePart = (framePartNames as readonly string[]).indexOf('temple');
    const population = generatePopulation(mesh, basis, { count: 3, seed: 11 });

    const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[xs.length >> 1];

    for (const yawDeg of [45, 60]) {
      const without: number[] = [];
      const withHead: number[] = [];
      for (const subject of population) {
        const model = truthModel(subject.positions);
        const seat = solveSeat(model, mesh, regions, frame);
        const head = buildHeadWithEars(subject.positions, mesh.indices, earRestPoints(model));
        const pose = ladderPose(geometry, yawDeg);
        const samples = { points: transformSamples(seat.pose, raw), pose, parts };

        // Truth carries a head. Both occluders are the EXACT truth surface with
        // and without the skull, so there is no scan error anywhere in this
        // comparison — the only difference is the geometry under test.
        const truth = { positions: head.positions, indices: head.indices, pose };
        const read = (occluder: typeof truth | { positions: Float64Array; pose: typeof pose }) => {
          const cell = occlusionCell(mesh, truth, occluder as never, samples, k, { biasesMm: [0] });
          const flip = cell.flips[0];
          const contested = flip.byPart!.contested[templePart];
          return contested > 0 ? (100 * flip.byPart!.xray[templePart]) / contested : null;
        };
        const a = read({ positions: subject.positions, pose });
        const b = read(truth);
        if (a !== null) without.push(a);
        if (b !== null) withHead.push(b);
      }

      assert.ok(without.length > 0 && withHead.length > 0,
        `no contested temple samples at yaw ${yawDeg} — the band is empty and this test `
        + 'is measuring nothing');
      const bad = median(without);
      const good = median(withHead);
      assert.ok(bad > 4,
        `at yaw ${yawDeg} a headless occluder X-rays only ${bad.toFixed(1)}% of temple `
        + 'samples. Measured at 8.9% (yaw 45) and 12.5% (yaw 60) when this was written; '
        + 'if it is now small the instrument has gone blind to the missing head again.');
      assert.ok(good < 1,
        `at yaw ${yawDeg} the head proxy still leaves ${good.toFixed(1)}% X-ray`);
      assert.ok(bad - good > 4,
        `the head proxy buys ${(bad - good).toFixed(1)} pp at yaw ${yawDeg}`);
    }
  });
});
