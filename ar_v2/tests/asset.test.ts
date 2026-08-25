/**
 * The asset pipeline: a real `.glb` becoming something the seat solve can hold.
 *
 * Every test here exists because the failure it guards produced NUMBERS rather
 * than an error. That is the whole character of this file. A frame whose ear
 * rest is 30 mm too far back still seats, still reports a descent and a
 * pantoscopic tilt, and still renders — it just renders a pair of glasses that
 * has fallen down the wearer's face, and nothing in the readout says so. So the
 * assertions are mostly about REFUSING, and each refusal is demonstrated
 * reachable on a real catalogue asset rather than on a three-vertex toy.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { CATALOGUE, catalogueEntry } from '../src/fit/catalogue.js';
import { derivePads, TEST_FRAMES } from '../src/fit/frame-asset.js';
import {
  PAD_SAMPLE_BUDGET, findBend, frameFromMesh, type CatalogueEntry,
} from '../src/fit/frame-from-mesh.js';
import { readGlb, type MeshAsset } from '../src/fit/mesh-io.js';
import { solveSeat } from '../src/fit/contact.js';
import { createFaceModel, type FaceModel } from '../src/core/facemodel.js';
import { loadBasis, loadRegions, loadTemplateMesh } from '../src/testkit/fixtures.js';
import { generatePopulation } from '../src/testkit/synthetic.js';

const here = dirname(fileURLToPath(import.meta.url));
// `tests/` and `dist/tests/` are different depths, exactly as
// `testkit/fixtures.ts` documents for the face template. Both entries are
// load-bearing; neither is a fallback.
const ROOTS = [resolve(here, '..'), resolve(here, '../..')];

function assetBytes(file: string): Uint8Array {
  for (const root of ROOTS) {
    try { return new Uint8Array(readFileSync(resolve(root, file))); } catch { /* next */ }
  }
  throw new Error(`asset not found: ${file}. Looked under:\n  ${ROOTS.join('\n  ')}`);
}

const load = (file: string): MeshAsset => readGlb(assetBytes(file));

const mesh = loadTemplateMesh();
const regions = loadRegions();
const basis = loadBasis();

const truthModel = (positions: Float64Array): FaceModel => createFaceModel({
  positions: new Float64Array(positions),
  vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
  shapeCoeffs: new Float64Array(0),
  basisName: 'ground-truth',
  displacementRmsMm: 0, displacementMaxMm: 0,
  intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
  intrinsicsSolved: true,
  scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
  landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
  quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
  pdMm: null, pdSigmaMm: null,
  reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
});

/** A rigid Y flip. Mirrors, so the winding has to be reversed with it. */
function flipY(positions: Float64Array): Float64Array {
  const out = new Float64Array(positions.length);
  for (let i = 0; i < positions.length; i += 3) {
    out[i] = positions[i]; out[i + 1] = -positions[i + 1]; out[i + 2] = positions[i + 2];
  }
  return out;
}

/** Reverses triangle winding, which is what makes a mirror a rotation again. */
function rewind(indices: Uint32Array): Uint32Array {
  const out = new Uint32Array(indices.length);
  for (let t = 0; t + 2 < indices.length; t += 3) {
    out[t] = indices[t]; out[t + 1] = indices[t + 2]; out[t + 2] = indices[t + 1];
  }
  return out;
}

function scaled(positions: Float64Array, s: number): Float64Array {
  const out = new Float64Array(positions.length);
  for (let i = 0; i < positions.length; i++) out[i] = positions[i] * s;
  return out;
}

function xSpan(positions: Float64Array): number {
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < positions.length; i += 3) {
    if (positions[i] < lo) lo = positions[i];
    if (positions[i] > hi) hi = positions[i];
  }
  return hi - lo;
}

// ------------------------------------------------------------------ derivation

describe('a real asset becomes a frame the solve can hold', () => {
  const entry = catalogueEntry('navigator')!;

  it('navigator derives a complete, measured layout', () => {
    const built = frameFromMesh(load(entry.file), entry);
    assert.ok(built.ok, `navigator must derive; it refused with: ${(built as any).reason}`);
    const a = built.asset;

    // Pads, against the committed ground truth (13.843 mm). The tolerance is
    // 1 mm because `assets/glasses/ground-truth.json` records the methods
    // disagreeing with each other by about that much.
    assert.ok(Math.abs(a.padSeparationMm - 13.843) < 1.0,
      `pad separation ${a.padSeparationMm.toFixed(2)} mm is more than 1 mm from the declared 13.843`);

    // The ear rest is the temple's BEND, not its tip. The tip is at z -132.9,
    // y -11.7 and produced a frame that fell down the face — see the header of
    // fit/frame-from-mesh.ts for the measured cost.
    for (const e of a.earRests) {
      assert.ok(e[2] < -80 && e[2] > -115,
        `ear rest reach ${(-e[2]).toFixed(1)} mm is outside 80-115; the temple TIP is at 133`);
      assert.ok(e[1] > 0,
        `ear rest height ${e[1].toFixed(1)} mm is at or below the pads — the ear term is `
        + 'one-sided, so it would never engage and nothing would hold the frame on');
    }

    // Lenses ahead of the pads, and above them.
    for (const c of a.lensCentres) {
      assert.ok(c[2] > 0, `lens centre sits at z ${c[2].toFixed(1)}, behind the pad origin`);
      assert.ok(c[1] > 0, `lens centre sits at y ${c[1].toFixed(1)}, below the pad origin`);
    }
    assert.ok(a.frontWidthMm > 130 && a.frontWidthMm < 155,
      `front width ${a.frontWidthMm.toFixed(1)} mm is not an adult frame`);

    // Provenance survives to the asset, which is what lets a wearer-facing
    // readout distinguish a measured number from a placeholder.
    assert.equal(a.dimensionSource, 'cad');
    assert.equal(a.padHeightMm, null, 'a mesh pad is a contact patch, not a rectangle');
    assert.ok(a.source, 'a mesh-backed frame must carry the file it came from');
  });

  it('the pad samples are thinned to the budget, and both sides keep their share', () => {
    const built = frameFromMesh(load(entry.file), entry);
    assert.ok(built.ok);
    const n = built.asset.padSide.length;
    assert.ok(n <= PAD_SAMPLE_BUDGET + 2, `${n} samples exceeds the ${PAD_SAMPLE_BUDGET} budget`);

    let right = 0, left = 0;
    for (const s of built.asset.padSide) (s < 0 ? right++ : left++);
    assert.ok(right > 0 && left > 0, 'thinning emptied a side');
    // `contact.ts` normalises stiffness by TOTAL count, so an uneven thinning
    // moves load across the nose without changing anything a reader would see.
    assert.ok(Math.abs(right - left) / n < 0.15,
      `thinning left ${right} right against ${left} left — that shifts pad load across the nose`);

    // And the un-thinned answer is still available, so the budget's cost stays
    // measurable rather than baked in.
    const full = frameFromMesh(load(entry.file), entry, { padSampleBudget: 0 });
    assert.ok(full.ok);
    assert.ok(full.asset.padSide.length > 400, 'padSampleBudget: 0 must keep every sample');
  });

  it('every catalogue entry either derives or refuses with a reason — none throws', () => {
    const derived: string[] = [];
    const refused: string[] = [];
    for (const e of CATALOGUE) {
      const built = frameFromMesh(load(e.file), e);
      if (built.ok) derived.push(e.id);
      else {
        refused.push(e.id);
        assert.ok(built.reason.length > 20,
          `${e.id} refused with "${built.reason}" — a refusal has to say what it saw`);
        assert.ok(built.reason.includes(e.id),
          `${e.id}'s refusal does not name the asset`);
      }
    }
    // **One of ten, and that is the honest state of this catalogue.** A test
    // that only asserted "navigator works" would stay green on the day
    // somebody made the derivation credulous enough to accept the other nine.
    assert.deepEqual(derived, ['navigator'],
      `expected only navigator to derive; got [${derived.join(', ')}]. `
      + 'If a new asset genuinely derives, add it here WITH the measurement that says so.');
    assert.equal(refused.length, CATALOGUE.length - 1);
  });
});

// ------------------------------------------------------------------- refusals

describe('the derivation refuses rather than inventing a layout', () => {
  const entry = catalogueEntry('navigator')!;

  it('refuses a mirrored asset whose winding was not reversed', () => {
    const asset = load(entry.file);
    // A mirror without a rewind inverts every face normal, after which the
    // inward-facing test finds the BACK of each pad and returns a plausible
    // separation. Nothing downstream could tell.
    const mirrored: MeshAsset = { ...asset, positions: flipY(asset.positions) };
    const built = frameFromMesh(mirrored, entry);
    assert.ok(!built.ok, 'a mesh with inverted winding must be refused');
    assert.match(built.reason, /inside out|winding/i);
  });

  it('refuses an upside-down asset — and this is the guard that was a knife edge', () => {
    const asset = load(entry.file);
    // Flipped AND re-wound: a genuine rotation, so the signed-volume guard is
    // satisfied and the UP-CHECK is what has to catch it. A test that flipped
    // without re-winding would be testing the volume guard instead.
    const upsideDown: MeshAsset = {
      ...asset, positions: flipY(asset.positions), indices: rewind(asset.indices),
    };
    const built = frameFromMesh(upsideDown, entry);
    assert.ok(!built.ok, 'an upside-down asset must be refused');
    assert.match(built.reason, /upside down/i);
  });

  it('refuses an asset whose parts it cannot name', () => {
    const blind: CatalogueEntry = { ...entry, parts: { temple: [], lens: entry.parts.lens } };
    const built = frameFromMesh(load(entry.file), blind);
    assert.ok(!built.ok);
    assert.match(built.reason, /temple/i);
    // The refusal has to list what the file DOES name, or the reader has no
    // way to fix the catalogue row without opening the asset in Blender.
    assert.match(built.reason, /Temple_L|Temple_R/,
      'the refusal must name the parts the file actually has');
  });

  it('refuses an earhook: khronos and shield-golden have no bend to rest on', () => {
    for (const id of ['khronos', 'shield-golden']) {
      const e = catalogueEntry(id)!;
      const built = frameFromMesh(load(e.file), e);
      assert.ok(!built.ok, `${id} must refuse: its temple never stops descending`);
      assert.match(built.reason, /bend|earhook/i,
        `${id} refused for the wrong reason: ${built.reason}`);
    }
  });

  it('findBend says no rather than picking a bin, when there is no level run', () => {
    // A straight rod: level from end to end.
    const rod = new Float64Array(300);
    for (let i = 0; i < 100; i++) { rod[i * 3] = 70; rod[i * 3 + 1] = 10; rod[i * 3 + 2] = -i; }
    assert.equal(findBend(rod), null, 'a rod that never turns down has no bend');

    // A hook: descends from the hinge, which is khronos's shape.
    const hook = new Float64Array(300);
    for (let i = 0; i < 100; i++) {
      hook[i * 3] = 70; hook[i * 3 + 1] = 30 - i * 0.4; hook[i * 3 + 2] = -i;
    }
    assert.equal(findBend(hook), null, 'an arm that only ever descends has no bend');

    // A real temple: level, then down. This is what must still be FOUND, or
    // the two refusals above are just a broken detector.
    const temple = new Float64Array(300);
    for (let i = 0; i < 100; i++) {
      temple[i * 3] = 70;
      temple[i * 3 + 1] = i < 70 ? 10 : 10 - (i - 70) * 0.7;
      temple[i * 3 + 2] = -i;
    }
    const bend = findBend(temple);
    assert.ok(bend, 'a temple that runs level and then turns down must have a bend');
    assert.ok(bend.z < -55 && bend.z > -85, `bend at z ${bend.z.toFixed(1)}, expected near -70`);
  });
});

// -------------------------------------------------------------- the up-check

describe('the upside-down guard is not a knife edge', () => {
  /**
   * `docs/HANDOFF.md` carried this as an open question: "aviator-amber refuses
   * while aviator-tortoiseshell derives, and those two assets differ essentially
   * only in texture."
   *
   * It was never a property of the asset. The guard compared the pad centroid
   * against `(minY + maxY) / 2` of the WHOLE mesh, which a drooping temple drags
   * downward, and amber's sign change landed 0.0026 mm from the 140 mm
   * placeholder width somebody happened to declare. The reference is now the
   * mean height of the frontmost slab.
   */
  const at140 = (file: string) => {
    const a = load(file);
    const s = 140 / xSpan(a.positions);
    return derivePads(scaled(a.positions, s), a.indices);
  };

  it('both aviators derive, and they used to differ by three microns', () => {
    for (const id of ['aviator-amber.glb', 'aviator-tortoiseshell.glb']) {
      const d = at140(`assets/glasses/${id}`);
      assert.ok(d.ok, `${id} refused: ${d.reason}`);
    }
  });

  it('and it still fires on a genuinely inverted asset, with room to spare', () => {
    // Every catalogue asset, flipped and re-wound, must be refused. If the
    // reference ever drifts back toward a bbox statistic this is what goes red.
    for (const e of CATALOGUE) {
      const a = load(e.file);
      const s = e.realWidthMm === null ? 1 : e.realWidthMm / xSpan(a.positions);
      const d = derivePads(flipY(scaled(a.positions, s)), rewind(a.indices));
      assert.ok(!d.ok, `${e.id} flipped upside down was accepted: ${d.reason}`);
    }
  });

  it('refuses a pad pair that is not a mirrored pair', () => {
    // `glasses01-with-lenses` arrives ~40 degrees off axis, so the central
    // column cuts it diagonally and takes 2948 faces from one side against 7130
    // from the other. A real red case, in the tree, for the side-balance guard.
    const a = load('assets/glasses/glasses01-with-lenses.glb');
    const d = derivePads(scaled(a.positions, 140 / xSpan(a.positions)), a.indices);
    assert.ok(!d.ok, 'a 2.4x lopsided pair must be refused');
    assert.match(d.reason, /mirrored pair|imbalance/i);
  });
});

// --------------------------------------------------------------- the gate

describe('the derived pads land on the pads their author drew', () => {
  /**
   * Stage 5's gate. `derivePads` emits FACE CENTROIDS computed as (a+b+c)/3
   * over the concatenated buffer, so a returned sample is bit-identical to the
   * centroid of its source triangle — the labelling below needs no distance
   * tolerance at all, and a mismatch would show up as an unmatched sample
   * rather than as a quietly wrong score.
   */
  function precisionOn(file: string, padParts: RegExp, options = {}): number {
    const asset = load(file);
    const onPad = new Set<string>();
    let offset = 0;
    for (const part of asset.parts) {
      const isPad = padParts.test(part.name) || padParts.test(part.materialName);
      if (isPad) {
        for (let t = 0; t + 2 < part.indices.length; t += 3) {
          const a = part.indices[t] * 3, b = part.indices[t + 1] * 3, c = part.indices[t + 2] * 3;
          const p = part.positions;
          onPad.add([
            (p[a] + p[b] + p[c]) / 3,
            (p[a + 1] + p[b + 1] + p[c + 1]) / 3,
            (p[a + 2] + p[b + 2] + p[c + 2]) / 3,
          ].join(','));
        }
      }
      offset += part.positions.length / 3;
    }
    assert.ok(onPad.size > 0, `${file} names no pad part matching ${padParts}`);

    const d = derivePads(asset.positions, asset.indices, options);
    assert.ok(d.ok, `derivePads refused ${file}: ${d.reason}`);
    let hit = 0;
    const n = d.padSide.length;
    for (let i = 0; i < n; i++) {
      const key = [d.padSamples[i * 3], d.padSamples[i * 3 + 1], d.padSamples[i * 3 + 2]].join(',');
      if (onPad.has(key)) hit++;
    }
    return hit / n;
  }

  it('navigator: every returned sample is on an author-named nose pad', () => {
    const p = precisionOn('assets/glasses/navigator.glb', /nosepad/i);
    assert.ok(p >= 0.95, `precision ${(p * 100).toFixed(1)}% is below the 95% bar`);
  });

  it('and the bar is reachable: dropping the rearward test halves it', () => {
    // The red recipe, run rather than asserted. `PAD_REAR_COS` is the test that
    // separates a nose pad from the inner wall of the lens aperture — which
    // faces the midline just as squarely, because it is the inside of a hole.
    // Without it the aperture floods the sample set.
    const p = precisionOn('assets/glasses/navigator.glb', /nosepad/i, { rearCos: -1 });
    assert.ok(p < 0.90,
      `sabotaging the rearward test left precision at ${(p * 100).toFixed(1)}% — `
      + 'the gate above cannot fail, which makes it a bug');
  });

  it('khronos is recorded as a measured ceiling, not gated at 90%', () => {
    // **This is the number that argues for demoting the derivation from
    // PRODUCER to CHECKER.** khronos's frame front is sculpted rather than
    // flat and carries genuinely rearward-leaning faces of its own, so no
    // setting of the thresholds reaches 90% on it without breaking navigator.
    // A ratchet at 0.45 goes red on a regression and can never go green by
    // accident.
    const p = precisionOn('assets/glasses/sunglasses-khronos.glb', /nosepad|nose_pads/i);
    assert.ok(p >= 0.45,
      `khronos precision fell to ${(p * 100).toFixed(1)}%, below the recorded 45% floor`);
    assert.ok(p < 0.90,
      `khronos precision reached ${(p * 100).toFixed(1)}%. If that is real it is very good `
      + 'news and this assertion should be replaced by the measurement that explains it — '
      + 'but a silent pass here would hide the ceiling the CHECKER decision rests on.');
  });
});

// ------------------------------------------------------ fit and render agree

describe('the renderer is given the same placement the solve used', () => {
  it('meshToFrame maps the file\'s own coordinates onto the pad-centroid origin', () => {
    // **The invariant the whole two-reader design rests on.** `fit/` reads the
    // GLB headlessly for triangles; `render/` reads it again through three.js
    // for materials. They agree only because the renderer applies THIS matrix
    // and computes nothing. If it drifts, the frame is drawn a few millimetres
    // from where it was fitted, which looks exactly like a tracking bug.
    const entry = catalogueEntry('navigator')!;
    const built = frameFromMesh(load(entry.file), entry);
    assert.ok(built.ok);
    const m = built.asset.source!.meshToFrame;

    // Apply it to the RAW file coordinates — metres, as glTF declares them, and
    // as GLTFLoader will hand them to the renderer.
    const raw = readGlb(assetBytes(entry.file), 1).positions;
    const moved = new Float64Array(raw.length);
    for (let i = 0; i < raw.length; i += 3) {
      const x = raw[i], y = raw[i + 1], z = raw[i + 2];
      moved[i] = m[0] * x + m[1] * y + m[2] * z + m[3];
      moved[i + 1] = m[4] * x + m[5] * y + m[6] * z + m[7];
      moved[i + 2] = m[8] * x + m[9] * y + m[10] * z + m[11];
    }

    // Re-deriving the pads on the transformed geometry must put their midpoint
    // at the origin, because that is what the transform was built to do.
    const d = derivePads(moved, readGlb(assetBytes(entry.file), 1).indices);
    assert.ok(d.ok, `pads did not derive after meshToFrame: ${d.reason}`);
    let sx = 0, sy = 0, sz = 0;
    const sides = [[0, 0, 0, 0], [0, 0, 0, 0]];
    for (let i = 0; i < d.padSide.length; i++) {
      const s = d.padSide[i] < 0 ? 0 : 1;
      sides[s][0] += d.padSamples[i * 3];
      sides[s][1] += d.padSamples[i * 3 + 1];
      sides[s][2] += d.padSamples[i * 3 + 2];
      sides[s][3]++;
    }
    sx = (sides[0][0] / sides[0][3] + sides[1][0] / sides[1][3]) / 2;
    sy = (sides[0][1] / sides[0][3] + sides[1][1] / sides[1][3]) / 2;
    sz = (sides[0][2] / sides[0][3] + sides[1][2] / sides[1][3]) / 2;
    for (const [axis, v] of [['x', sx], ['y', sy], ['z', sz]] as const) {
      assert.ok(Math.abs(v) < 1e-6,
        `after meshToFrame the pad centroid sits ${v.toFixed(6)} mm off the origin in ${axis}`);
    }

    // And the scale really is the metres-to-millimetres conversion, or the
    // renderer draws a frame a thousand times too big.
    assert.ok(Math.abs(m[0] - 1000) < 1e-9, `meshToFrame scale is ${m[0]}, expected 1000`);
  });
});

// ------------------------------------------------------------------ the seat

describe('a measured frame seats like the parametric one it replaces', () => {
  it('navigator sits on the nose across the population, and converges every time', () => {
    const entry = catalogueEntry('navigator')!;
    const built = frameFromMesh(load(entry.file), entry);
    assert.ok(built.ok);

    // Five subjects rather than the usual population: this frame carries real
    // geometry and the solve is ~0.3 s each.
    const population = generatePopulation(mesh, basis, { count: 5, seed: 7 });
    const descents: number[] = [];
    const pantos: number[] = [];
    let converged = 0;
    let onPads = 0;
    for (const subject of population) {
      const seat = solveSeat(truthModel(subject.positions), mesh, regions, built.asset);
      descents.push(seat.descentMm);
      pantos.push(seat.pantoscopicDeg);
      if (seat.converged) converged++;
      if (seat.padLoadFraction > 0.5) onPads++;
    }

    assert.equal(converged, population.length,
      'a measured frame that fails to converge is the ear-rest-at-the-tip failure returning');
    // Pad load is the one that caught it. With the ear rest at the temple tip
    // the MEDIAN pad load read 99% while the worst case was 0% — the frame
    // either hung on the nose alone or slid off entirely, and the median hid it.
    assert.equal(onPads, population.length,
      'a subject is carrying no load on the pads — the frame has slid off their face');
    for (const p of pantos) {
      assert.ok(Math.abs(p) < 30,
        `pantoscopic ${p.toFixed(1)} deg. A fabricated ear rest produced -73 on khronos; `
        + 'a real frame sits within a few degrees of level.');
    }
    for (const d of descents) {
      assert.ok(d < 25, `descent ${d.toFixed(1)} mm — the frame is down the wearer's face`);
    }

    // And it is not merely "not broken": it lands where the parametric frame
    // the whole tree was tuned against lands.
    const reference = population.map(
      (s) => solveSeat(truthModel(s.positions), mesh, regions, TEST_FRAMES[1]).descentMm,
    );
    const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[xs.length >> 1];
    assert.ok(Math.abs(median(descents) - median(reference)) < 4,
      `navigator's median descent ${median(descents).toFixed(2)} mm against the parametric `
      + `standard's ${median(reference).toFixed(2)} mm — more than 4 mm apart`);
  });
});
