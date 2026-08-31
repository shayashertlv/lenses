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
import { PAD_MIN_FACES, derivePads, TEST_FRAMES } from '../src/fit/frame-asset.js';
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
    //
    // Asserted RELATIVE to the budget, not as an absolute count. This read
    // `> 400` until `PAD_CONTACT_CONE_COS` landed, which was a proxy for "the
    // budget did not run" that quietly also encoded how many faces the SELECTOR
    // admits — and narrowing the selection from the pad's whole inward
    // hemisphere to its contact face took navigator from 812 samples to 302.
    // Both numbers satisfy the thing the assertion is about.
    const full = frameFromMesh(load(entry.file), entry, { padSampleBudget: 0 });
    assert.ok(full.ok);
    assert.ok(full.asset.padSide.length > PAD_SAMPLE_BUDGET * 2,
      `padSampleBudget: 0 kept ${full.asset.padSide.length} samples against a budget of `
      + `${PAD_SAMPLE_BUDGET} — the un-thinned answer is no longer distinguishable from `
      + 'the thinned one, so the budget cost cannot be measured');
  });

  it('estimates a lens centre within a millimetre of the named part it stands in for', () => {
    // Finding 6. The derived fallback took the bounding-box MIDPOINT in z of the
    // frontmost quarter of the whole asset — a ~35 mm slab holding the rim and
    // the first 30 mm of each temple — so the temple tail dragged the "lens
    // centre" about 10 mm behind the lens. The slab was never the problem; the
    // statistic was. Almost every vertex in it belongs to the rim, so the MEDIAN
    // sits on the rim.
    //
    // Seven assets name their lens parts, which makes them ground truth for the
    // estimator that stands in when an asset does not: build each one twice,
    // once as itself and once with its lens parts hidden, and compare.
    const zOf = (c: readonly [Float64Array, Float64Array]) => (c[0][2] + c[1][2]) / 2;
    const rows: { id: string; measured: number; derived: number }[] = [];
    for (const e of CATALOGUE) {
      if (e.parts.lens.length === 0) continue;
      const bytes = assetBytes(e.file);
      const named = frameFromMesh(readGlb(bytes), e);
      const blind = frameFromMesh(readGlb(bytes), { ...e, parts: { ...e.parts, lens: [] } });
      assert.ok(named.ok && blind.ok, `${e.id} refused`);
      assert.equal(named.asset.lensSource, 'measured');
      assert.equal(blind.asset.lensSource, 'derived');
      rows.push({ id: e.id, measured: zOf(named.asset.lensCentres), derived: zOf(blind.asset.lensCentres) });
    }
    assert.ok(rows.length >= 7, `only ${rows.length} assets name a lens to check against`);

    // A wrap recesses its lens behind the frame's frontmost point, and no
    // statistic taken off the front of the slab finds that. Pinned, not
    // excused: this is the one asset shape the estimator cannot do, and the
    // reason `score.ts` still withholds the verdict on a derived centre.
    const wrap = rows.find((r) => r.id === 'shield-golden')!;
    assert.ok(wrap.measured < -20,
      `shield-golden's lens no longer sits deeply recessed (${wrap.measured.toFixed(1)} mm) — `
      + 'the estimator limitation this pins may have changed shape');
    assert.ok(wrap.derived - wrap.measured > 10,
      'the front-of-slab estimator suddenly handles a wrap — check why before trusting it');

    for (const r of rows) {
      if (r.id === 'shield-golden') continue;
      const err = Math.abs(r.derived - r.measured);
      assert.ok(err < 1.5,
        `${r.id}: the derived lens centre is ${err.toFixed(2)} mm from the named part's `
        + `(${r.derived.toFixed(2)} against ${r.measured.toFixed(2)}) — the bounding-box `
        + 'midpoint this replaced was out by up to 13.4 mm');
    }
  });

  it('every catalogue entry either derives or refuses with a reason — none throws', () => {
    const derived: string[] = [];
    const refused: string[] = [];
    const tiers: Record<string, string> = {};
    for (const e of CATALOGUE) {
      const built = frameFromMesh(load(e.file), e);
      if (built.ok) { derived.push(e.id); tiers[e.id] = built.asset.earRestSource; }
      else {
        refused.push(e.id);
        assert.ok(built.reason.length > 20,
          `${e.id} refused with "${built.reason}" — a refusal has to say what it saw`);
        assert.ok(built.reason.includes(e.id),
          `${e.id}'s refusal does not name the asset`);
      }
    }
    // **Which assets reach the vertex-verdict gate.** `score.ts` withholds the
    // vertex distance when `lensSource` is 'derived', because those centres are
    // the extent centre of the frontmost quarter-slab of the whole mesh rather
    // than a lens. Pinned by name: before the gate existed these two showed the
    // wearer -1.42 mm and -1.33 mm and graded them 'poor', and the count is also
    // the ground `PAD_UP_REFERENCE_FRACTION`'s ledger row argues from.
    const derivedLens = CATALOGUE
      .map((e) => ({ id: e.id, built: frameFromMesh(load(e.file), e) }))
      .filter((r) => r.built.ok && r.built.asset.lensSource === 'derived')
      .map((r) => r.id)
      .sort();
    assert.deepEqual(derivedLens, ['crystal-parts', 'meshy'],
      `these assets name no lens part and have their vertex verdict withheld: ${derivedLens.join(', ')}`);

    // **Ten of ten derive, and the tier is the thing under test.**
    //
    // This assertion used to read `deepEqual(derived, ['navigator'])`, and it
    // was right when the only route to an ear rest was a named temple part with
    // a bend. It would now stay green on a derivation that had gone credulous
    // in the other direction, so what it pins is no longer WHETHER an asset
    // derives but WHAT IT ADMITS TO. The three tiers are not interchangeable:
    // 'measured' is a bend walked on a named part, 'derived' is the arm's knee
    // fitted from geometry, and 'assumed' means the asset has no rest point at
    // all and the wearer's ear supplied one.
    //
    // Every id below is placed by a measurement, not by preference — see
    // `deriveArmRest` for the slope-ratio table that separates the last two.
    assert.equal(refused.length, 0,
      `every catalogue asset should now derive; [${refused.join(', ')}] refused`);
    assert.deepEqual(tiers, {
      navigator: 'measured',
      khronos: 'assumed',
      'aviator-tortoiseshell': 'derived',
      'aviator-amber': 'derived',
      'horizon-amber': 'derived',
      'horizon-sage': 'derived',
      'shield-golden': 'assumed',
      'crystal-parts': 'derived',
      'crystal-lenses': 'derived',
      meshy: 'derived',
    }, 'an asset changed tier. That is a change in what the catalogue CLAIMS, '
      + 'not a cosmetic one — re-measure before moving a row.');

    // The two that cannot measure their own rest are the two the geometry says
    // are wraps. If this count ever reaches zero the discriminator has stopped
    // discriminating, and every frame in the catalogue would then be reporting
    // a rest point it does not have.
    const assumed = Object.values(tiers).filter((t) => t === 'assumed');
    assert.equal(assumed.length, 2,
      'exactly two catalogue assets are wraps with no rest point of their own');
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

  it('finds navigator\'s arm without its name, and lands where the name did', () => {
    // **This is the calibration point for the whole geometric-arm tier.**
    //
    // navigator is the only asset with a known-good ear rest, because it is the
    // only one that names a temple part `findBend` can walk. Hide that name and
    // the derivation has to find the arm the way it finds it on the other nine —
    // by splitting the whole mesh and fitting the knee. If the two answers
    // disagree, every DERIVED rest point in the catalogue is suspect and there
    // is no way to know it from the assets themselves.
    const named = frameFromMesh(load(entry.file), entry);
    assert.ok(named.ok);
    assert.equal(named.asset.earRestSource, 'measured');

    const blind: CatalogueEntry = { ...entry, parts: { temple: [], lens: entry.parts.lens } };
    const built = frameFromMesh(load(entry.file), blind);
    assert.ok(built.ok, `the geometric arm must find navigator's rest: ${(built as any).reason}`);
    assert.equal(built.asset.earRestSource, 'derived',
      'with no temple named, the rest must come from the arm and SAY that it did');

    for (const s of [0, 1]) {
      const dz = built.asset.earRests[s][2] - named.asset.earRests[s][2];
      const dy = built.asset.earRests[s][1] - named.asset.earRests[s][1];
      // The two bounds are sized differently, and on purpose.
      //
      // REACH, 12 mm: the two methods answer slightly different questions —
      // `findBend` walks back to the last level bin, the knee fit intersects
      // two lines — so they disagree by 8.4 mm here and always will. The bound
      // is there to catch a method that has stopped finding the arm at all,
      // which is what a retuned tolerance does.
      //
      // HEIGHT, 1.5 mm: this one is tight, because it is the only check in the
      // suite that can tell the level-run line from the knee bin's mean. Those
      // two readings are 0.2 mm and 2.0 mm from the measured answer, and NOTHING
      // ELSE distinguishes them — the seat moves by 0.01 mm of descent, so every
      // other bar in the tree stays green under either. Loosen this and the
      // curl-contaminated reading comes back silently.
      assert.ok(Math.abs(dz) < 12,
        `derived reach ${(-built.asset.earRests[s][2]).toFixed(1)} mm disagrees with the `
        + `measured ${(-named.asset.earRests[s][2]).toFixed(1)} by ${Math.abs(dz).toFixed(1)} mm`);
      assert.ok(Math.abs(dy) < 1.5,
        `derived height ${built.asset.earRests[s][1].toFixed(1)} mm disagrees with the `
        + `measured ${named.asset.earRests[s][1].toFixed(1)} by ${Math.abs(dy).toFixed(1)} mm`);
    }
  });

  it('will not call a wrap or an earhook a rest: khronos and shield-golden are ASSUMED', () => {
    // These two used to refuse outright, and refusing was right — neither has a
    // rest point, because neither arm rests on anything. What changed is that a
    // frame with no rest point is now WEARABLE with the wearer's own ear
    // supplying the reach and height, rather than unavailable.
    //
    // The falsifiable part is unchanged and is the whole test: the
    // discriminator must still put them in the assumed tier. If either ever
    // reads 'derived', the knee fit has started finding rest points in curves
    // that do not have them, which is exactly the failure that produced
    // pantoscopic -73 degrees and 13% pad load before any of this existed.
    for (const id of ['khronos', 'shield-golden']) {
      const e = catalogueEntry(id)!;
      const built = frameFromMesh(load(e.file), e);
      assert.ok(built.ok, `${id} must now derive an assumed layout: ${(built as any).reason}`);
      assert.equal(built.asset.earRestSource, 'assumed',
        `${id} is a wrap or an earhook and must not claim a derived rest point`);
      assert.ok(
        built.notes.some((n) => /ASSUMED/.test(n) && /picture, not a measurement/.test(n)),
        `${id} must say in its notes that the fit is a picture, not a measurement`,
      );
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

  it('the contact cone degrades continuously, with no cliff inside its range', () => {
    // `PAD_CONTACT_CONE_COS` narrows the found faces to the pad's contact face.
    // When the cone is tighter than the pad's own curvature it can leave fewer
    // faces than `PAD_MIN_FACES`, and what happens THEN decides whether the
    // constant has a cliff in the middle of its range.
    //
    // The first draft handed the whole inward hemisphere back the moment the
    // cone left 19 faces. That made the sweep non-monotone — `aviator-amber`
    // read a 4.36 mm deep patch at 0.95, 9.28 at 0.90 and 7.85 at 0.85, because
    // the fallback fired at one value and not its neighbours. "A parameter whose
    // failures are interior to its range is one whose safe values are
    // coincidences" is `PAD_UP_REFERENCE_FRACTION`'s own argument about its own
    // sweep, and it applies here.
    //
    // The shipped 0.955 leaves every catalogue asset well clear of the floor, so
    // no sabotage of the shipped configuration can reach this. It is asserted
    // directly instead.
    const asset = load('assets/glasses/navigator.glb');
    const wide = derivePads(asset.positions, asset.indices, { contactConeCos: -1 });
    assert.ok(wide.ok);
    let last = wide.padSide.length;
    for (const cos of [0.9, 0.95, 0.99, 0.999, 0.99999]) {
      const d = derivePads(asset.positions, asset.indices, { contactConeCos: cos });
      assert.ok(d.ok, `derivePads refused at cone ${cos}: ${d.reason}`);
      assert.ok(d.padSide.length <= last,
        `tightening the cone from the previous value to ${cos} INCREASED the sample count `
        + `${last} -> ${d.padSide.length}. A tighter cone cannot admit more faces unless `
        + 'something is handing the whole selection back.');
      last = d.padSide.length;
    }
    // At a cone no real face can satisfy, both sides fall back to exactly the
    // floor — not to everything.
    assert.equal(last, PAD_MIN_FACES * 2,
      `an impossible cone left ${last} samples rather than the ${PAD_MIN_FACES * 2}-sample `
      + 'floor, so the fallback is returning the whole hemisphere again');
  });

  it('khronos is recorded as a measured ceiling, not gated at 90%', () => {
    // **This is the number that argues for demoting the derivation from
    // PRODUCER to CHECKER.** khronos's frame front is sculpted rather than
    // flat and carries genuinely rearward-leaning faces of its own.
    //
    // **The floor moved 45% -> 70% on 2026-08-27** and the reason is worth
    // keeping: this test's comment used to say "no setting of the thresholds
    // reaches 90% on it without breaking navigator", and that was true of the
    // thresholds that existed. `PAD_CONTACT_CONE_COS` is a new one — a cone
    // about the pad's OWN mean normal rather than the x axis — and it takes
    // khronos from 48.2% to 79.0% with navigator unmoved at 100%. So the
    // ceiling stands and the reasoning behind it does not: it was never that
    // 90% is unreachable in principle, only that no threshold then in the file
    // could reach it.
    //
    // A ratchet at 0.70 goes red on a regression and can never go green by
    // accident.
    const p = precisionOn('assets/glasses/sunglasses-khronos.glb', /nosepad|nose_pads/i);
    assert.ok(p >= 0.70,
      `khronos precision fell to ${(p * 100).toFixed(1)}%, below the recorded 70% floor`);
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
