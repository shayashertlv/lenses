/**
 * Turning a real pair of glasses into something the seat solve can hold.
 *
 * `mesh-io.ts` reads a `.glb` into triangles. `frame-asset.ts` says what the
 * solve needs to know. This file is the bridge, and it is the file that decides
 * whether v2 fits a MEASURED frame or a parametric stand-in.
 *
 * ## The one number that decides everything, and it is not the pads
 *
 * The first end-to-end run of navigator through this path put the ear rest at
 * the temple's REARMOST vertex, which is the obvious reading of "where the arm
 * comes to rest" and is wrong by a wide margin. A real temple runs level to the
 * bend and then curls DOWN behind the ear, so its rearmost vertex is the tip:
 * on navigator that is z -132.9, y -11.7, where the bend is at z -103.8,
 * y +9.3 — **30 mm too far back and 21 mm too low**.
 *
 * The ear term in `contact.ts` is ONE-SIDED. A rest point below the ear never
 * engages, so the frame has nothing holding it on, and the solve reports a
 * plausible-looking number for a frame that has fallen down the face. Measured
 * across 17 synthetic subjects, tip against bend, everything else identical:
 *
 *     ear rest at      descent mm            pantoscopic deg     pad load      converged
 *     the TIP          8.57 [-1.2, 42.4]     21.5 [10, 53]       99% [0, 100]  13/17
 *     the BEND         4.07 [ 0.6, 12.1]      0.3 [-2, 11]       90% [69, 100] 17/17
 *     parametric ref   4.09 [ 0.4, 10.7]      3.9 [ 1, 15]       93% [56, 100] 17/17
 *
 * The tip's pad load reads 99% MEDIAN while its worst case is 0% — the frame
 * either hangs on the nose alone or slides off entirely, and the median hides
 * it. That is why the tables here report the spread and not the middle.
 *
 * With the bend, a real asset seats indistinguishably from the parametric frame
 * the whole tree was tuned on. That agreement is the result this file exists
 * for, and it was not a foregone conclusion.
 *
 * ## The bend also checks the tree's highest-leverage constant
 *
 * `FrameSpec.templeReachMm` defaults to 95 mm, swept against the synthetic
 * population, and its own docstring calls +-5 mm of it "the highest-leverage
 * number in the tree" — enough to carry the corneal vertex across the entire
 * 12-16 mm band. Nothing had ever measured it on a real pair of glasses.
 *
 * navigator's bend measures **96.2 mm** of reach at **+13.5 mm** of height
 * above the pad origin, against the swept 95 and the conventional +8. The reach
 * agrees to 1.2 mm. That is one asset, not a population, and it is recorded
 * here as corroboration rather than as a new value for the default.
 *
 * ## What it refuses
 *
 * Every step that cannot be measured refuses instead of guessing, because a
 * guessed layout is exactly the failure above: it produces numbers, and nothing
 * downstream can tell they are wrong. A frame is refused when its pads do not
 * derive, when its temples cannot be told apart, and — the one that matters —
 * when a temple has no bend at all. `sunglasses-khronos` has none: its earhook
 * descends monotonically from the hinge, because it is a sports wrap whose arm
 * hooks around the ear rather than resting on it. There is no bend to find, and
 * inventing one is how this file would go back to being wrong.
 *
 * ## Isolation
 *
 * `fit/` is headless: no three.js, no DOM, no file I/O. The caller reads the
 * bytes. The renderer loads the SAME file again through three.js and applies
 * `FrameSource.meshToFrame` — the transform this file computed — because the
 * drawn frame has to be where the solve put it, and one matrix in one place is
 * the only way that stays true.
 */

import type { DimensionSource, FrameAsset } from './frame-asset.js';
import { derivePads } from './frame-asset.js';
import type { MeshAsset, MeshPart } from './mesh-io.js';

/**
 * What the catalogue declares about an asset that its geometry cannot say.
 *
 * Absolute scale is the honest example: an arbitrary-unit mesh carries shape and
 * nothing else, so no measurement of the vertices can put its size back. v1's
 * catalogue learned this the expensive way — nine of its eleven entries declare
 * `widthSource: 'assumed'`, and its width verdict therefore compared one
 * estimate against another. The field is required here for the same reason
 * `FrameAsset.dimensionSource` is.
 */
export interface CatalogueEntry {
  readonly id: string;
  readonly name: string;
  /** Path under `assets/glasses/`, as the app fetches it. */
  readonly file: string;
  /**
   * The frame's real front width in millimetres, or null when the asset is
   * already life-sized and its author's number is the one to use.
   *
   * When set, the geometry is scaled so its front width matches. What "front
   * width" means is the frontmost quarter of the asset's depth, measured across
   * — v1's definition, and it measured that this slice spans the same x as the
   * whole model on ten of its eleven assets.
   */
  readonly realWidthMm: number | null;
  readonly widthSource: DimensionSource;
  /**
   * Quaternion [x, y, z, w] taking the file's axes into frame space, or null
   * when the asset already arrives +Y up with the lenses at +Z.
   *
   * A rotation only. A mirror would reverse triangle winding and invert every
   * normal, after which `derivePads` finds the BACK of each pad and returns a
   * plausible separation — so a negative-determinant transform is rejected
   * rather than corrected.
   */
  readonly orient: readonly [number, number, number, number] | null;
  readonly massG: number;
  readonly splayStiffnessNPerMm?: number;
  readonly bridgeType?: FrameAsset['bridgeType'];
  /** Free text. Where the numbers above came from, in a sentence. */
  readonly provenance: string;
  /**
   * Case-insensitive substrings identifying parts, matched against the node
   * name AND the material name.
   *
   * Declared rather than inferred because part naming is the one thing an asset
   * pipeline genuinely varies: navigator says `Temple_L`, khronos says
   * `EarhookLeft`, and Tripo says `tripo_part_3`. Where an asset names nothing,
   * these are empty and the derivation refuses — which is the true answer.
   */
  readonly parts: {
    readonly temple: readonly string[];
    readonly lens: readonly string[];
  };
}

export interface MeshFrame {
  readonly ok: true;
  readonly asset: FrameAsset;
  /** Every step that had to be measured, with what it read. For the report. */
  readonly notes: readonly string[];
}

export interface MeshFrameRefusal {
  readonly ok: false;
  readonly reason: string;
}

export type MeshFrameResult = MeshFrame | MeshFrameRefusal;

/**
 * How many pad samples reach the solve.
 *
 * `measured`. `derivePads` returns one sample per inward-facing triangle, which
 * on navigator is 812 and on a denser asset would be more. `contact.ts`
 * normalises by count (`kPerSample = SKIN.stiffnessNPerMm / samples`), so the
 * total pad stiffness does not change with sampling — only the cost and the
 * spatial resolution of the contact patch do.
 *
 * Swept on navigator over 17 synthetic subjects (median [min, max] descent, and
 * the per-solve cost):
 *
 *     samples   descent mm             ms/solve
 *       812     4.07 [0.6, 12.1]        2723
 *        64     4.20 [0.0, 12.6]         357
 *        18     4.61 [0.1, 11.6]         179
 *
 * 64 reproduces the full-resolution answer to 0.13 mm for an eighth of the
 * cost. 18 — the parametric frame's own sample count — costs 0.54 mm, and the
 * drift is MONOTONE with coarseness rather than noisy, so it is a real bias and
 * not sampling scatter. 64 is taken where the bias is still below the seat's
 * own reproducibility.
 *
 * The cost matters because it is paid on the wearer's clock: `fitFrame` solves
 * synchronously on every frame change, so 2.7 s is a frozen page.
 *
 * **The second-order effect was checked rather than assumed.** `padArticulation`
 * charges its residual to `n - 3` degrees of freedom, so changing `n` moves
 * `padSeatErrorArticulatedMm` against `PAD_CURVATURE_LIMIT_MM` (0.9) — a bar set
 * on 18-sample parametric frames. Measured on navigator over 7 subjects
 * (median / max articulated residual, and how many exceed the limit):
 *
 *     812 samples   0.720 / 1.601   3 of 7
 *     200           0.719 / 1.575   3 of 7
 *      64           0.699 / 1.562   2 of 7
 *      32           0.726 / 1.670   2 of 7
 *      18           0.884 / 1.855   3 of 7
 *
 * 64 tracks the full-resolution median to 0.021 mm. **18 does not** — it drifts
 * to 0.884, which is 18% of the limit the verdict is judged against, so the
 * parametric frame's own sample count would have moved a wearer-facing readout.
 * That is a second, independent argument for 64 over 18.
 *
 * It also shows something that is NOT an artefact of thinning: navigator exceeds
 * the curvature limit on 2 to 3 of 7 subjects at every sample count, where the
 * parametric standard exceeds on 1. Its pads are genuinely more curved than the
 * flat rectangle the limit was set against. Whether 0.9 is still the right bar
 * for a real pad is unresolved and belongs with the ledger row, not here.
 */
export const PAD_SAMPLE_BUDGET = 64;

/**
 * How far the temple's centreline may fall below its highest point and still
 * count as running level, mm.
 *
 * `stated`, and the shape of the answer matters more than the value. On
 * navigator the centreline runs flat at y ~ 10.2-10.3 from the hinge back to
 * z = -94, then falls away to -10.5 at the tip: the level run and the curl are
 * separated by 20 mm of height, so any tolerance between roughly 0.5 and 5 mm
 * finds the same bend to within one bin. 1.5 mm sits in the middle of that
 * plateau.
 *
 * It is NOT a knife edge on navigator and it would be on an asset whose temple
 * droops gently the whole way. That asset has no bend, and `findBend` says so
 * rather than reporting the bin the tolerance happened to land in.
 */
export const TEMPLE_BEND_TOLERANCE_MM = 1.5;

/**
 * How much of a temple's own depth must be level for its bend to be a bend.
 *
 * `measured`, and it is the guard that stopped two assets deriving nonsense.
 *
 * A temple that rests on an ear runs level from the hinge and then turns down.
 * That shape has two degenerate neighbours, and both are in the catalogue:
 *
 *     asset           depth mm   level run mm   fraction
 *     navigator          135.5           73.4      0.542   a real bend
 *     shield-golden      104.9           13.1      0.125   descends from the hinge
 *     sunglasses-khronos 161.4            0.0      0.000   an earhook, all curve
 *
 * Below the threshold the arm never runs level — it is a hook that wraps the ear
 * rather than resting on it, and there is no rest point for this method to find.
 * The other end is covered separately: an arm that is level all the way to its
 * rearmost bin is a straight rod with no bend either, and `findBend` returns
 * null for that without needing a number.
 *
 * 0.25 sits between a 2.2x margin on the one asset that works and a 2x margin
 * on the nearest that does not. With one positive example the threshold is
 * bounded rather than optimised, and it is deliberately placed where BOTH
 * neighbours fail rather than where the gap is widest — a wrap seating on a
 * fabricated rest point is a worse error than a wrap being refused.
 *
 * What made this necessary is worth recording, because the failure was silent
 * until it reached the seat: khronos with a fabricated rest point produced
 * pantoscopic **−73 degrees** and 13% pad load, and shield-golden −6.3 degrees
 * and 6%. Neither refused, and neither number is one a reader would question
 * without the frame beside it.
 */
export const TEMPLE_LEVEL_RUN_MIN_FRACTION = 0.25;

/**
 * How much of the asset's depth counts as 'the front', for the width.
 *
 * `measured` by v1 across the same eleven assets: the frontmost quarter spans
 * the same x as the whole model on ten of them, the eleventh being
 * `shield-golden`, whose wrap puts its arms 1.1 mm wider. So this measures the
 * optician's total front width — twice the lens plus the bridge plus the two
 * endpieces — rather than the widest point of a splayed arm.
 */
export const FRONT_SLICE_FRACTION = 0.25;

/** How much of a temple's depth its hinge end occupies, for the hinge point. */
const HINGE_SLICE_FRACTION = 0.06;

/** Bins along Z when profiling a temple's centreline. */
const TEMPLE_PROFILE_BINS = 24;

const refuse = (reason: string): MeshFrameRefusal => ({ ok: false, reason });

// -------------------------------------------------------------- geometry aids

interface Extent { readonly min: Float64Array; readonly max: Float64Array }

function extentOf(positions: Float64Array): Extent {
  const min = Float64Array.of(Infinity, Infinity, Infinity);
  const max = Float64Array.of(-Infinity, -Infinity, -Infinity);
  for (let i = 0; i < positions.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      if (positions[i + k] < min[k]) min[k] = positions[i + k];
      if (positions[i + k] > max[k]) max[k] = positions[i + k];
    }
  }
  return { min, max };
}

/** Row-major 4x4 identity. */
function identity4(): Float64Array {
  const m = new Float64Array(16);
  m[0] = m[5] = m[10] = m[15] = 1;
  return m;
}

function uniformScale(s: number): Float64Array {
  const m = identity4();
  m[0] = m[5] = m[10] = s;
  return m;
}

/** Row-major product a*b. */
function mul4(a: Float64Array, b: Float64Array): Float64Array {
  const o = new Float64Array(16);
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 4; c++) {
      let v = 0;
      for (let k = 0; k < 4; k++) v += a[r * 4 + k] * b[k * 4 + c];
      o[r * 4 + c] = v;
    }
  }
  return o;
}

/** Rotation from a unit quaternion [x, y, z, w], row-major 4x4. */
function fromQuaternion(q: readonly [number, number, number, number]): Float64Array {
  const len = Math.hypot(q[0], q[1], q[2], q[3]);
  if (!(len > 0)) throw new Error('orient is not a rotation: the quaternion has zero length');
  const x = q[0] / len, y = q[1] / len, z = q[2] / len, w = q[3] / len;
  const m = identity4();
  m[0] = 1 - 2 * (y * y + z * z); m[1] = 2 * (x * y - w * z); m[2] = 2 * (x * z + w * y);
  m[4] = 2 * (x * y + w * z); m[5] = 1 - 2 * (x * x + z * z); m[6] = 2 * (y * z - w * x);
  m[8] = 2 * (x * z - w * y); m[9] = 2 * (y * z + w * x); m[10] = 1 - 2 * (x * x + y * y);
  return m;
}

function applyTo(m: Float64Array, positions: Float64Array): Float64Array {
  const out = new Float64Array(positions.length);
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i], y = positions[i + 1], z = positions[i + 2];
    out[i] = m[0] * x + m[1] * y + m[2] * z + m[3];
    out[i + 1] = m[4] * x + m[5] * y + m[6] * z + m[7];
    out[i + 2] = m[8] * x + m[9] * y + m[10] * z + m[11];
  }
  return out;
}

/** The upper-left 3x3 determinant. Negative means the transform mirrors. */
function determinant3(m: Float64Array): number {
  return m[0] * (m[5] * m[10] - m[6] * m[9])
    - m[1] * (m[4] * m[10] - m[6] * m[8])
    + m[2] * (m[4] * m[9] - m[5] * m[8]);
}

const hits = (haystack: string, needles: readonly string[]): boolean => {
  const h = haystack.toLowerCase();
  return needles.some((n) => h.includes(n.toLowerCase()));
};

/**
 * Parts matching a declared name list — **node names first, materials only if
 * no node name matches at all**.
 *
 * The precedence is not cosmetic. `sunglasses-khronos` gives its frame front the
 * node name `Frames` and the MATERIAL name `temples`, because RapidCompact
 * assigned one material to the front and the arms together. Matching either
 * field equally swept the whole frame front into the temple set, and the bend
 * detector then profiled the entire asset: it reported a "bend" 5.8 mm behind
 * the lenses with a level run of ZERO, and the frame seated at −73 degrees of
 * pantoscopic tilt with 13% of its weight on the pads.
 *
 * Falling back to materials is still needed: `shield-golden` names its nodes
 * `Frame`, `Frame_x` and `Lenses`, and the only thing identifying the arms is
 * `Frame_x`'s material, `Temples_Matte`. So the rule is that the more specific
 * evidence wins where it exists, rather than that one field is trusted and the
 * other is not.
 */
function selectParts<T extends MeshPart>(parts: readonly T[], needles: readonly string[]): T[] {
  if (needles.length === 0) return [];
  const byNode = parts.filter((p) => hits(p.name, needles));
  if (byNode.length > 0) return byNode;
  return parts.filter((p) => hits(p.materialName, needles));
}

/**
 * Splits a vertex cloud into the wearer's right (x < 0) and left (x > 0).
 *
 * By VERTEX and not by part, because pipelines disagree about whether a pair of
 * things is a pair of nodes. navigator ships `Temple_L` and `Temple_R`;
 * sunglasses-khronos ships `LensesInterior` holding BOTH lenses, and
 * shield-golden ships one `Frame_x` holding both temples. Grouping whole parts
 * by their mean x refused those two for having "0 right, 2 left" — a refusal
 * that names the wrong problem, which is worse than no refusal because it sends
 * the reader to the wrong file.
 *
 * Connectivity is not preserved and does not need to be: everything downstream
 * of this reads vertex positions only. Vertices exactly on the midline go
 * nowhere, which is right — a bridge spanning x = 0 belongs to neither side.
 */
function splitBySide(positions: Float64Array): [Float64Array, Float64Array] {
  const right: number[] = [];
  const left: number[] = [];
  for (let i = 0; i < positions.length; i += 3) {
    const to = positions[i] < 0 ? right : positions[i] > 0 ? left : null;
    if (!to) continue;
    to.push(positions[i], positions[i + 1], positions[i + 2]);
  }
  return [Float64Array.from(right), Float64Array.from(left)];
}

/**
 * The temple's bend: the rearmost place its centreline is still running level.
 *
 * Binned along Z, centreline = the MEAN Y of the vertices in a bin. Not the
 * bounding-box mid and not the top surface: a temple is a box in cross-section,
 * so its top edge stays at the same height right through the curl for as long
 * as the box is deep, and a rule written on `max Y` picks the HINGE on
 * navigator — the opposite end of the arm.
 *
 * Returns null when the centreline's highest bin is the frontmost one and the
 * profile only ever descends from there. That is a temple with no bend: an
 * earhook, which wraps around the ear instead of resting on it, and whose rest
 * point this method cannot find.
 */
export function findBend(
  positions: Float64Array,
  toleranceMm = TEMPLE_BEND_TOLERANCE_MM,
  bins = TEMPLE_PROFILE_BINS,
): { x: number; y: number; z: number; levelRunMm: number } | null {
  if (positions.length < 9) return null;
  let zMin = Infinity, zMax = -Infinity;
  for (let i = 2; i < positions.length; i += 3) {
    if (positions[i] < zMin) zMin = positions[i];
    if (positions[i] > zMax) zMax = positions[i];
  }
  const span = zMax - zMin;
  if (!(span > 0)) return null;

  const sx = new Float64Array(bins), sy = new Float64Array(bins), n = new Float64Array(bins);
  for (let i = 0; i < positions.length; i += 3) {
    const b = Math.min(bins - 1, Math.floor((positions[i + 2] - zMin) / span * bins));
    sx[b] += positions[i]; sy[b] += positions[i + 1]; n[b]++;
  }

  // Bin 0 is the REARMOST slice (lowest z); bin `bins-1` is the hinge end.
  let top = -Infinity, topBin = -1;
  for (let b = 0; b < bins; b++) if (n[b] && sy[b] / n[b] > top) { top = sy[b] / n[b]; topBin = b; }
  if (topBin < 0) return null;

  // Walk REARWARD from the highest bin — toward lower index — while the
  // centreline is still level. The last such bin is the bend.
  let bend = topBin;
  for (let b = topBin - 1; b >= 0; b--) {
    if (!n[b]) continue;
    if (sy[b] / n[b] < top - toleranceMm) break;
    bend = b;
  }

  // ...and FORWARD too, which is not symmetry for its own sake. On a temple
  // whose level section is exactly flat, every bin in it ties for the maximum
  // and `topBin` lands on whichever the scan happened to reach first — the
  // REARMOST of the tie, since a later equal value does not exceed it. Measuring
  // the level run from there backwards then reports almost nothing: a synthetic
  // temple with 70% of its length dead level measured 12.4 mm of level run out
  // of 99, was judged an earhook, and was refused. navigator hid the defect
  // because a real temple rises gently to a peak near its hinge, so its `topBin`
  // was already near the front.
  let levelFront = topBin;
  for (let b = topBin + 1; b < bins; b++) {
    if (!n[b]) continue;
    if (sy[b] / n[b] < top - toleranceMm) break;
    levelFront = b;
  }

  // A temple whose level run reaches its own rearmost bin never turned down:
  // it is a straight rod, and there is no bend in it.
  if (bend === 0) return null;

  // ...and one with no level run at all never turned down either — it descends
  // from the hinge, which is an earhook wrapping the ear rather than an arm
  // resting on it. Both degenerate shapes are in the catalogue and both used to
  // return a confident, wrong rest point. See TEMPLE_LEVEL_RUN_MIN_FRACTION.
  const levelRunMm = (levelFront - bend) / bins * span;
  if (levelRunMm < span * TEMPLE_LEVEL_RUN_MIN_FRACTION) return null;

  const z = zMin + (bend + 0.5) / bins * span;
  return { x: sx[bend] / n[bend], y: sy[bend] / n[bend], z, levelRunMm };
}

/** Centroid of the vertices in the frontmost `frac` of a part's depth. */
function frontSliceCentroid(positions: Float64Array, frac: number): Float64Array | null {
  let zMin = Infinity, zMax = -Infinity;
  for (let i = 2; i < positions.length; i += 3) {
    if (positions[i] < zMin) zMin = positions[i];
    if (positions[i] > zMax) zMax = positions[i];
  }
  if (!(zMax >= zMin)) return null;
  const cut = zMax - (zMax - zMin) * frac;
  let x = 0, y = 0, z = 0, n = 0;
  for (let i = 0; i < positions.length; i += 3) {
    if (positions[i + 2] < cut) continue;
    x += positions[i]; y += positions[i + 1]; z += positions[i + 2]; n++;
  }
  return n ? Float64Array.of(x / n, y / n, z / n) : null;
}

/**
 * Thins a pad sample set to a budget, preserving each side's share of the area.
 *
 * A stride over the face list, taken PER SIDE. Per side because the two pads
 * must keep their relative weight: `contact.ts` normalises by the total count,
 * so dropping more samples from one pad than the other silently moves load
 * across the nose. A stride rather than a random draw because the result has to
 * be identical on every run — a seat that changes between two solves of the same
 * frame is a seat nobody can debug.
 *
 * The stride assumes the face list is spatially coherent, which is true of an
 * authored mesh and only approximately true of a scan. What guards against the
 * assumption failing is that `PAD_SAMPLE_BUDGET`'s adoption was measured on the
 * seated result, not on the sample cloud: if a thinning were unrepresentative,
 * the descent would move.
 */
function thinPads(
  samples: Float64Array, normals: Float64Array, side: Int8Array, budget: number,
): { samples: Float64Array; normals: Float64Array; side: Int8Array } {
  const total = side.length;
  if (total <= budget) return { samples, normals, side };

  const keep: number[] = [];
  for (const s of [-1, 1] as const) {
    const idx: number[] = [];
    for (let i = 0; i < total; i++) if (side[i] === s) idx.push(i);
    if (idx.length === 0) continue;
    const want = Math.max(1, Math.round(budget * (idx.length / total)));
    const step = idx.length / want;
    for (let k = 0; k < want; k++) keep.push(idx[Math.min(idx.length - 1, Math.floor(k * step))]);
  }

  const outS = new Float64Array(keep.length * 3);
  const outN = new Float64Array(keep.length * 3);
  const outD = new Int8Array(keep.length);
  for (let k = 0; k < keep.length; k++) {
    const i = keep[k];
    outS[k * 3] = samples[i * 3]; outS[k * 3 + 1] = samples[i * 3 + 1]; outS[k * 3 + 2] = samples[i * 3 + 2];
    outN[k * 3] = normals[i * 3]; outN[k * 3 + 1] = normals[i * 3 + 1]; outN[k * 3 + 2] = normals[i * 3 + 2];
    outD[k] = side[i];
  }
  return { samples: outS, normals: outN, side: outD };
}

// ------------------------------------------------------------------- the bridge

/**
 * Builds a `FrameAsset` from a mesh and its catalogue row, or refuses.
 *
 * `padSampleBudget` is exposed so the harness can measure what the thinning
 * costs; pass 0 to keep every sample.
 */
export function frameFromMesh(
  mesh: MeshAsset, entry: CatalogueEntry,
  options: { padSampleBudget?: number; meshScaleToMm?: number } = {},
): MeshFrameResult {
  const budget = options.padSampleBudget ?? PAD_SAMPLE_BUDGET;
  // What `readGlb` was given. Everything below works in millimetres, but the
  // renderer loads the file through three.js and gets the file's OWN units —
  // metres, per glTF — so `meshToFrame` has to carry the conversion or the
  // drawn frame is a thousand times too big. Folding it in here means the
  // renderer applies one matrix and computes nothing.
  const meshScaleToMm = options.meshScaleToMm ?? 1000;
  const notes: string[] = [];

  if (mesh.parts.length === 0 || mesh.indices.length < 3) {
    return refuse(`${entry.id}: the file holds no triangles`);
  }

  // ---- 1. into frame space: rotate, then scale to the declared width.
  let toFrame = entry.orient ? fromQuaternion(entry.orient) : identity4();
  if (determinant3(toFrame) <= 0) {
    return refuse(
      `${entry.id}: orient mirrors the asset (determinant ${determinant3(toFrame).toFixed(3)}). `
      + 'A mirror reverses triangle winding and inverts every normal, after which the pads '
      + 'derive off the BACK of each pad surface and look plausible.',
    );
  }

  let positions = applyTo(toFrame, mesh.positions);

  if (entry.realWidthMm !== null) {
    const raw = frontWidthOf(positions);
    if (!(raw > 0)) return refuse(`${entry.id}: the asset has no width to scale`);
    const s = entry.realWidthMm / raw;
    const scale = identity4();
    scale[0] = scale[5] = scale[10] = s;
    toFrame = mul4(scale, toFrame);
    positions = applyTo(scale, positions);
    notes.push(`scaled x${s.toFixed(5)} to the declared front width ${entry.realWidthMm} mm`);
  } else {
    notes.push('life-sized as authored; no scale applied');
  }

  // ---- 2. the pads, which also fix the origin.
  const pads = derivePads(positions, mesh.indices);
  if (!pads.ok) return refuse(`${entry.id}: ${pads.reason}`);

  const padMid = padCentroidMidpoint(pads.padSamples, pads.padSide);
  if (!padMid) return refuse(`${entry.id}: the derived pads are all on one side`);

  const recentre = identity4();
  recentre[3] = -padMid[0]; recentre[7] = -padMid[1]; recentre[11] = -padMid[2];
  toFrame = mul4(recentre, toFrame);
  positions = applyTo(recentre, positions);
  const padSamples = applyTo(recentre, pads.padSamples);
  notes.push(
    `pads: ${pads.reason}; separation ${pads.padSeparationMm.toFixed(2)} mm, `
    + `angle ${pads.padAngleRad.toFixed(4)} rad`,
  );

  // Parts, re-expressed about the pad origin — the frame of every number below.
  const parts = mesh.parts.map((p) => ({ ...p, positions: applyTo(toFrame, p.positions) }));

  // ---- 3. the temples, and the bend that is the whole point of this file.
  const temples = selectParts(parts, entry.parts.temple);
  if (temples.length === 0) {
    return refuse(
      `${entry.id}: no part matches the declared temple names [${entry.parts.temple.join(', ')}]. `
      + `The file names: ${mesh.parts.map((p) => p.name || '(unnamed)').join(', ')}`,
    );
  }
  const sides = splitBySide(mergeParts(temples));
  if (sides[0].length < 9 || sides[1].length < 9) {
    return refuse(
      `${entry.id}: the temple geometry does not span two sides `
      + `(${sides[0].length / 3} vertices right, ${sides[1].length / 3} left)`,
    );
  }
  const bends = sides.map((p) => findBend(p));
  if (!bends[0] || !bends[1]) {
    return refuse(
      `${entry.id}: a temple has no bend — its centreline never stops descending. `
      + 'That is an earhook, which wraps around the ear rather than resting on it, and '
      + 'this method cannot find its rest point. Declare it or leave the asset ungraded.',
    );
  }
  const earRests: [Float64Array, Float64Array] = [
    Float64Array.of(bends[0].x, bends[0].y, bends[0].z),
    Float64Array.of(bends[1].x, bends[1].y, bends[1].z),
  ];
  notes.push(
    `ear rests at the temple bend: reach ${(-earRests[0][2]).toFixed(1)} / `
    + `${(-earRests[1][2]).toFixed(1)} mm, height ${earRests[0][1].toFixed(1)} / `
    + `${earRests[1][1].toFixed(1)} mm (level run ${bends[0].levelRunMm.toFixed(0)} mm)`,
  );

  // `parametricFrame` refuses a non-positive reach for the same reason: the ear
  // term is one-sided, so a rest at or in front of the pads never engages.
  for (const e of earRests) {
    if (!(e[2] < 0)) {
      return refuse(
        `${entry.id}: an ear rest sits at z ${e[2].toFixed(1)}, level with or ahead of the `
        + 'pads. The ear term would never engage and the frame would have nothing holding it on.',
      );
    }
  }

  const hinges = sides.map((p) => frontSliceCentroid(p, HINGE_SLICE_FRACTION));
  if (!hinges[0] || !hinges[1]) return refuse(`${entry.id}: a temple has no hinge end to measure`);

  // ---- 4. the lenses.
  const lenses = selectParts(parts, entry.parts.lens);
  if (lenses.length === 0) {
    return refuse(
      `${entry.id}: no part matches the declared lens names [${entry.parts.lens.join(', ')}]. `
      + 'Lens centres carry the vertex-distance and pupil-height verdicts, so guessing them '
      + 'would put a number on a wearer readout that nothing measured.',
    );
  }
  const lensSides = splitBySide(mergeParts(lenses));
  if (lensSides[0].length < 9 || lensSides[1].length < 9) {
    return refuse(
      `${entry.id}: the lens geometry does not span two sides `
      + `(${lensSides[0].length / 3} vertices right, ${lensSides[1].length / 3} left)`,
    );
  }
  const lensCentres: [Float64Array, Float64Array] = [
    extentCentre(lensSides[0]), extentCentre(lensSides[1]),
  ];
  notes.push(
    `lens centres from ${lenses.length} named part(s): `
    + `${Array.from(lensCentres[0]).map((v) => v.toFixed(1)).join(', ')}`,
  );

  const frontWidthMm = frontWidthOf(positions);
  notes.push(`front width ${frontWidthMm.toFixed(2)} mm over the frontmost quarter of the depth`);

  // ---- 5. thin the pads to the budget.
  const thin = budget > 0
    ? thinPads(padSamples, pads.padNormals, pads.padSide, budget)
    : { samples: padSamples, normals: pads.padNormals, side: pads.padSide };
  if (thin.side.length !== pads.padSide.length) {
    notes.push(`pad samples thinned ${pads.padSide.length} -> ${thin.side.length}`);
  }

  const asset: FrameAsset = {
    id: entry.id,
    name: entry.name,
    padSamples: thin.samples,
    padNormals: thin.normals,
    padSide: thin.side,
    padSeparationMm: pads.padSeparationMm,
    padAngleRad: pads.padAngleRad,
    // Null, and deliberately: real geometry has a contact patch, not a
    // rectangle. Nothing in `src/` reads either field.
    padHeightMm: null,
    padWidthMm: null,
    frontWidthMm,
    lensCentres,
    hinges: [hinges[0], hinges[1]],
    earRests,
    massG: entry.massG,
    splayStiffnessNPerMm: entry.splayStiffnessNPerMm ?? 0.05,
    bridgeType: entry.bridgeType ?? 'pads',
    dimensionSource: entry.widthSource,
    provenance: entry.provenance,
    // The renderer's matrix maps the FILE's coordinates, not the millimetres
    // everything above works in, so the unit conversion goes in here.
    source: { url: entry.file, meshToFrame: mul4(toFrame, uniformScale(meshScaleToMm)) },
  };

  return { ok: true, asset, notes };
}

/** The midpoint of the two pad clouds' centroids — the frame-space origin. */
function padCentroidMidpoint(samples: Float64Array, side: Int8Array): Float64Array | null {
  const sum = [new Float64Array(3), new Float64Array(3)];
  const n = [0, 0];
  for (let i = 0; i < side.length; i++) {
    const s = side[i] < 0 ? 0 : 1;
    sum[s][0] += samples[i * 3]; sum[s][1] += samples[i * 3 + 1]; sum[s][2] += samples[i * 3 + 2];
    n[s]++;
  }
  if (!n[0] || !n[1]) return null;
  return Float64Array.of(
    (sum[0][0] / n[0] + sum[1][0] / n[1]) / 2,
    (sum[0][1] / n[0] + sum[1][1] / n[1]) / 2,
    (sum[0][2] / n[0] + sum[1][2] / n[1]) / 2,
  );
}

/**
 * The frame's front width: the x span of the frontmost quarter of its depth.
 *
 * Not the whole model's x span, which the temples set when they splay. On
 * navigator the two differ by 2.5 mm (147.5 whole against 145.0 for the front
 * part alone), and the width verdict is a comparison against the wearer's head
 * where a couple of millimetres is a real share of the band.
 */
export function frontWidthOf(positions: Float64Array): number {
  const { min, max } = extentOf(positions);
  const cut = max[2] - (max[2] - min[2]) * FRONT_SLICE_FRACTION;
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < positions.length; i += 3) {
    if (positions[i + 2] < cut) continue;
    if (positions[i] < lo) lo = positions[i];
    if (positions[i] > hi) hi = positions[i];
  }
  return hi > lo ? hi - lo : 0;
}

function extentCentre(positions: Float64Array): Float64Array {
  const { min, max } = extentOf(positions);
  return Float64Array.of((min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2);
}

function mergeParts(parts: readonly { positions: Float64Array }[]): Float64Array {
  let n = 0;
  for (const p of parts) n += p.positions.length;
  const out = new Float64Array(n);
  let at = 0;
  for (const p of parts) { out.set(p.positions, at); at += p.positions.length; }
  return out;
}
