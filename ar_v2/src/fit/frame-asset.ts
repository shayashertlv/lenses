/**
 * What the system needs to know about a pair of glasses.
 *
 * ## The thing v1 got wrong here was not code
 *
 * v1's audit records that nine of its eleven catalogue frames declare
 * `widthSource: 'assumed'`, because their geometry had been normalised to a
 * 140 mm placeholder. So its width verdict compared one estimate against
 * another, and its seat solved against pad positions inferred from a mesh whose
 * absolute size was invented. No amount of solver quality survives that: it is
 * an **asset pipeline** problem, and it is why this file leads with provenance
 * fields rather than geometry.
 *
 * `dimensionSource` is required, not optional. A frame whose front width was
 * measured with calipers and a frame whose front width was guessed from a
 * normalised scan are not interchangeable inputs, and the difference has to
 * survive all the way to the readout that tells a wearer whether the frame fits.
 *
 * ## Coordinates
 *
 * Frame-local, millimetres, and the same handedness as face space: **+X to the
 * wearer's left, +Y up, +Z out of the wearer's face** (so the lenses are at
 * positive Z relative to the pads, and the pad contact normals point in -Z-ish,
 * back toward the face). The origin is the midpoint between the two pad contact
 * centroids — not the mesh centroid, not the bridge vertex — because that is the
 * point the contact solve is really about, and putting the origin anywhere else
 * makes every rotation in the seat a rotation about the wrong pivot.
 */

import { type Vec3, v3, vnormalize } from '../core/linalg.js';

export type DimensionSource = 'measured' | 'cad' | 'scan-normalised' | 'assumed';

export interface FrameAsset {
  readonly id: string;
  readonly name: string;

  /**
   * Pad contact samples: the points on the pad surfaces that can touch skin.
   *
   * Samples rather than a single point per pad, and this is the difference
   * between v1's seat and a contact solve. A nose pad is a *surface* about
   * 12 x 8 mm; whether it beds down flat or digs in at one corner is decided by
   * the angle between it and the sidewall, which a single point cannot express.
   * v1's whole seat rested on one contact point per side and could not tell a
   * flush pad from a pad touching at its top edge.
   */
  readonly padSamples: Float64Array;
  /** Inward normal per sample (pointing toward the face). 3 per sample. */
  readonly padNormals: Float64Array;
  /** Which side each sample belongs to: -1 wearer's right, +1 left. */
  readonly padSide: Int8Array;

  /** Distance between the two pad centroids, mm. The number that decides how far
   *  down the wedge the frame settles. */
  readonly padSeparationMm: number;
  /** Angle of each pad's plane from the sagittal plane, radians. A pad angled to
   *  match the wearer's sidewall bears flush; one that does not digs an edge in. */
  readonly padAngleRad: number;
  /** Pad face size, mm. Null when the frame came from a mesh: real geometry has
   *  a contact patch, not a rectangle, and inventing dimensions for it is how
   *  assets end up declaring numbers they do not have. */
  readonly padHeightMm: number | null;
  readonly padWidthMm: number | null;

  /** Overall front width, temple to temple, mm. */
  readonly frontWidthMm: number;
  /** Lens optical centres in frame space, for the pupil-height verdict. */
  readonly lensCentres: readonly [Float64Array, Float64Array];
  /** Where the arms leave the front, per side. */
  readonly hinges: readonly [Float64Array, Float64Array];
  /** Where the arms come to rest on the ears, per side, when unsplayed. */
  readonly earRests: readonly [Float64Array, Float64Array];

  /** Mass, grams. Heavier frames sit lower — the wedge slide is proportional. */
  readonly massG: number;
  /**
   * Temple splay stiffness, newtons per millimetre of spread per side.
   *
   * A wide head pushes the arms outward; the arms push back, and that inward
   * force has a vertical component at the ear that carries part of the frame's
   * weight. Ignoring it is why a stock frame sits differently on a wide face and
   * a narrow one even when the nose is identical.
   */
  readonly splayStiffnessNPerMm: number;
  readonly bridgeType: 'pads' | 'saddle' | 'keyhole';
  readonly dimensionSource: DimensionSource;
  /** Free text: "Zeiss caliper, 2026-08-20" or "normalised to 140 mm placeholder". */
  readonly provenance: string;
}

export const GRAVITY_N_PER_G = 9.80665e-3; // newtons per gram

// ------------------------------------------------------------- parametric

export interface FrameSpec {
  id: string;
  name?: string;
  /** Distance between pad centroids, mm. Typical 14-22. */
  padSeparationMm: number;
  /**
   * Angle of each pad's plane, radians, measured from the sagittal plane.
   *
   * A pad is flush when this matches the wearer's nasal sidewall. On the
   * template that angle is `atan(0.60 / 0.76) = 0.67 rad` (38 degrees), from the
   * measured surface normal at pad height — which is the number a real pad arm
   * is bent to.
   */
  padAngleRad: number;
  /** Pad face height and width, mm. */
  padHeightMm?: number;
  padWidthMm?: number;
  /**
   * How far the lens plane sits AHEAD of the pad contact, mm.
   *
   * **The only fore-aft knob a parametric frame has**, now that `padSetbackMm`
   * is gone. That field named the distance from the pads to the lens plane from
   * the other end, and it did nothing: its only effect was a constant `-setback`
   * on every pad sample's Z, which the re-centring below subtracts back out
   * exactly — the origin is *defined* as the pad centroid, so a rigid shift of
   * every pad cannot survive it. Verified across 0, 3, 10, 18, 40, -25 and 1e6:
   * the same pad cloud every time, to 2e-15 mm (2e-11 at 1e6, which is what
   * cancelling a million-millimetre offset in doubles costs).
   *
   * **Measured by sweeping it against the population, not derived** — and the
   * failed derivation is the point of this comment.
   *
   * A real wearer reported *"Lens distance: about 5 mm from your eyes — close
   * enough that your lashes may touch"*, graded poor. The verdict was right and
   * the asset was wrong: the value had been six-tenths of that dead setback
   * field = 6 mm, a number with no anatomy behind it. Removing the field is what
   * left this one holding the axis alone.
   *
   * The obvious fix was to derive it. A prescription assumes the back lens
   * surface sits 12 to 16 mm from the cornea; the corneal apex is about 12 mm
   * ahead of the canthal plane; the nasal sidewall is 11.5 mm ahead of the same
   * plane on this template. Therefore 12.5 to 16.5 mm ahead of the pads.
   *
   * That derivation gives **20.7 mm** of vertex distance, which is worse than
   * what it replaced. It is wrong because it computes where the pads *should*
   * sit from a landmark, while the contact solve decides where they *actually*
   * land — a frame settles on the surface with the pad's own stand-off, several
   * millimetres forward of the landmark, and at a height that depends on the
   * wearer's nose.
   *
   * So it is swept instead. Across six synthetic faces and three pad widths:
   *
   *     ahead   vertex median   in the 10-18 mm band
   *       4          11.1              16/24
   *       6          13.1              21/24
   *       7          14.1              22/24     <- centres the 12-16 target
   *       8          15.1              22/24
   *      10          17.0              17/24
   *      14          20.3               5/24
   *
   * The wearer's 5 mm sits below this whole synthetic range, which is its own
   * finding: their frame settles further forward than any generated face. It is
   * one more reason the real assets need measuring (Q10).
   */
  lensAheadOfPadsMm?: number;
  frontWidthMm?: number;
  /**
   * How far behind the pad-centroid origin the temple's ear rest sits, mm:
   * `earRests` gets `z = -templeReachMm`. Default 95, the inline literal this
   * field replaces (Q16 — "the highest-leverage number in the tree, and it has
   * no spec field"; now it has one). The hinge sits at z = -2, so the arm's
   * cantilever span is reach - 2 = 93 mm at the default — the L in
   * `SKIN.hookCantileverNPerMm`'s derivation.
   *
   * The leverage, re-measured 2026-08-22 on the fixed-RNG population (5 seeds
   * x 8 subjects x 5 catalogue frames, shipped wall hook, seated against
   * ground truth; cross-seed medians of per-seed pooled medians):
   *
   *     reach mm   corneal vertex mm   descent mm   hook force /weight
   *        90             8.7             -0.05            1.79
   *        95            13.0              3.84            1.01
   *       100            16.7              9.33            0.72
   *
   * ±5 mm of reach carries the vertex across the entire 12-16 mm band, which
   * no other single number in the tree can do. The compliant hook does not
   * deflate that positional leverage — it trims the force swing (1.07 -> 0.68
   * weight-units over the same sweep) but the vertex swing stays 7.26 mm
   * against the wall's 8.00.
   *
   * The stocked-length reality: real temples come in 5 mm steps — 135, 140,
   * 145, 150 mm overall arm length, hinge to tip — a 15 mm spread, three times
   * the ±5 mm swept above. How much of a stock-length step reaches the BEND
   * (which is what this field measures) is itself unmeasured, which is why
   * this defaults rather than derives. On a parametric frame the value is
   * ASSUMED; measuring it off real assets is the most valuable measurement
   * Q16 leaves open.
   */
  templeReachMm?: number;
  massG?: number;
  splayStiffnessNPerMm?: number;
  bridgeType?: FrameAsset['bridgeType'];
  samplesPerPad?: number;
}

/**
 * A frame built from numbers rather than a mesh.
 *
 * This is what makes the contact solver testable without a single asset file,
 * and it is how the wedge relationship gets measured rather than asserted: sweep
 * `padSeparationMm` and watch the seat height move. v1 derived that slope
 * (0.54 mm of half-width per mm of descent) analytically and could not test it,
 * because it had no way to vary a frame's pad separation independently of
 * everything else about it.
 */
export function parametricFrame(spec: FrameSpec): FrameAsset {
  const padH = spec.padHeightMm ?? 12;
  const padW = spec.padWidthMm ?? 8;
  const n = spec.samplesPerPad ?? 9;
  const rows = Math.max(2, Math.round(Math.sqrt(n)));
  const cols = Math.max(2, Math.round(n / rows));

  const samples: number[] = [];
  const normals: number[] = [];
  const sides: number[] = [];

  for (const side of [-1, 1] as const) {
    // The pad plane: tilted by padAngle about the vertical axis so it faces
    // inward and slightly forward, matching a nasal sidewall.
    const ca = Math.cos(spec.padAngleRad), sa = Math.sin(spec.padAngleRad);
    // Inward normal: toward the midline and toward the face.
    const nrm = v3(-side * ca, 0, -sa);
    vnormalize(nrm, nrm);
    // In-plane axes: vertical, and the one perpendicular to both.
    const up = v3(0, 1, 0);
    const across = v3(side * sa, 0, -ca);

    const cx = (side * spec.padSeparationMm) / 2;
    // Row-major, across-pad innermost — and nothing downstream may lean on that.
    // `derivePads` emits samples in whatever order a GLB stores its vertices, so
    // a consumer that treats the sample index as a coordinate works on one of
    // the two ways an asset gets here. `padArticulation` used to, and it cost a
    // wearer-facing verdict.
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const fy = rows === 1 ? 0 : (r / (rows - 1) - 0.5) * padH;
        const fa = cols === 1 ? 0 : (c / (cols - 1) - 0.5) * padW;
        samples.push(
          cx + up[0] * fy + across[0] * fa,
          0 + up[1] * fy + across[1] * fa,
          0 + up[2] * fy + across[2] * fa,
        );
        normals.push(nrm[0], nrm[1], nrm[2]);
        sides.push(side);
      }
    }
  }

  // Re-centre so the origin is the midpoint of the two pad centroids, which is
  // the origin convention the whole contact solve is built on.
  //
  // It also means a spec field that offsets EVERY pad sample rigidly has no
  // effect whatsoever — the shift lands in the centroid and comes straight back
  // out. `padSetbackMm` was such a field and is gone; the fore-aft axis belongs
  // to `lensAheadOfPadsMm`, which moves the lenses relative to the pads rather
  // than moving both.
  let cxSum = 0, cySum = 0, czSum = 0;
  for (let i = 0; i < samples.length; i += 3) {
    cxSum += samples[i]; cySum += samples[i + 1]; czSum += samples[i + 2];
  }
  const count = samples.length / 3;
  cxSum /= count; cySum /= count; czSum /= count;
  for (let i = 0; i < samples.length; i += 3) {
    samples[i] -= cxSum; samples[i + 1] -= cySum; samples[i + 2] -= czSum;
  }

  const frontWidth = spec.frontWidthMm ?? 138;
  const half = frontWidth / 2;
  const lensAhead = spec.lensAheadOfPadsMm ?? 7;
  const templeReach = spec.templeReachMm ?? 95;
  // Fail where the mistake is: a non-positive reach puts the ear rests level
  // with or in front of the pads, and the one-sided ear and hook terms then
  // silently never engage — the frame has nothing holding it on and the seat
  // reports a plausible-looking answer for a frame that cannot exist.
  if (!Number.isFinite(templeReach) || templeReach <= 0) {
    throw new Error(
      `frame "${spec.id}": templeReachMm must be a positive finite number, ` +
      `got ${spec.templeReachMm}`,
    );
  }

  // A frame with one NaN in it does not fail — it seats, it scores, and it
  // reports a verdict that looks like a number. `padAngleRad` is required and
  // has no default, so a spec that omits it (which TypeScript catches, but a
  // plain-JS caller does not) produced a pad cloud of NaN, a solve that quietly
  // fell back to its initial pose, and a lens-distance verdict of "about 2 mm"
  // that cost an hour of chasing the wrong constant. Fail where the mistake is.
  for (let i = 0; i < samples.length; i++) {
    if (!Number.isFinite(samples[i]) || !Number.isFinite(normals[i])) {
      throw new Error(
        `frame "${spec.id}": pad geometry is not finite — check padAngleRad ` +
        `(${spec.padAngleRad}) and padSeparationMm (${spec.padSeparationMm})`,
      );
    }
  }

  return {
    id: spec.id,
    name: spec.name ?? spec.id,
    padSamples: Float64Array.from(samples),
    padNormals: Float64Array.from(normals),
    padSide: Int8Array.from(sides),
    padSeparationMm: spec.padSeparationMm,
    padAngleRad: spec.padAngleRad,
    padHeightMm: padH,
    padWidthMm: padW,
    frontWidthMm: frontWidth,
    lensCentres: [
      Float64Array.of(-frontWidth * 0.23, 0, lensAhead),
      Float64Array.of(frontWidth * 0.23, 0, lensAhead),
    ],
    hinges: [Float64Array.of(-half, 2, -2), Float64Array.of(half, 2, -2)],
    // Temple reach and drop. The reach comes from the spec now (`templeReachMm`,
    // default 95 — see that field for what ±5 mm of it does to the seat); the
    // drop is the trade's convention that the bend sits a few millimetres above
    // the hinge line rather than level with it, because the ear is higher than
    // the eye. Getting the height wrong here is not a cosmetic error — the ear
    // term is one-sided, so a rest point placed too low simply never engages
    // and the frame has nothing holding it on.
    earRests: [
      Float64Array.of(-half + 4, 8, -templeReach),
      Float64Array.of(half - 4, 8, -templeReach),
    ],
    massG: spec.massG ?? 24,
    splayStiffnessNPerMm: spec.splayStiffnessNPerMm ?? 0.05,
    bridgeType: spec.bridgeType ?? 'pads',
    dimensionSource: 'assumed',
    provenance: 'parametric — generated from a FrameSpec, not measured',
  };
}

/**
 * A small catalogue of parametric frames spanning the space that matters.
 *
 * Not a product catalogue: a *test* catalogue, chosen so that the pad separation
 * brackets the population's nose widths from clearly-too-narrow to
 * clearly-too-wide. A solver that gets the middle right and the ends wrong is a
 * solver that works on the frames somebody happened to try.
 *
 * All five share the default 95 mm temple reach, deliberately. Nothing measured
 * distinguishes their temples, and `templeReachMm`'s own table is the argument
 * against inventing per-frame values: ±5 mm of reach moves the corneal vertex
 * across the entire 12-16 mm band, so an unmeasured per-frame spread here would
 * put an unmeasured band-width of vertex into every comparison the catalogue
 * exists to make. Per-frame values belong here the day they are measured off
 * real assets, not before.
 */
export const TEST_FRAMES: FrameAsset[] = [
  parametricFrame({ id: 'narrow-pads', padSeparationMm: 13, padAngleRad: 0.67, massG: 20 }),
  parametricFrame({ id: 'standard', padSeparationMm: 17, padAngleRad: 0.67, massG: 24 }),
  parametricFrame({ id: 'wide-pads', padSeparationMm: 22, padAngleRad: 0.67, massG: 28 }),
  parametricFrame({ id: 'heavy-acetate', padSeparationMm: 19, padAngleRad: 0.67, massG: 42, bridgeType: 'saddle' }),
  // Deliberately mismatched: pads far steeper than any nasal sidewall. This one
  // exists so the tilt-advice metric has something to find — a catalogue where
  // every frame fits is a catalogue that cannot show a misfit.
  parametricFrame({ id: 'steep-pads', padSeparationMm: 17, padAngleRad: 0.20, massG: 24 }),
];

// -------------------------------------------------------- derivation from mesh

/**
 * Finds the pad contact surfaces in a raw eyewear mesh.
 *
 * The idea is v1's and it was a good one: real glasses are carried by the nose,
 * so look for the rearmost geometry in a narrow vertical column through the
 * centre of the frame. What is different here:
 *
 *  - **Two clusters, not one point.** v1 averaged the rearmost geometry in the
 *    column into a single contact point, which conflates a saddle bridge with a
 *    pair of pads and loses the separation entirely — and pad separation is the
 *    single most important number about how a frame sits.
 *  - **Normals, not just positions.** A pad's *angle* decides whether it beds
 *    flush against the sidewall or digs an edge in. It is available directly
 *    from the mesh and v1 discarded it.
 *  - **It reports when it failed.** Returning a confident answer for a mesh with
 *    no pads at all is how nine assets ended up declaring dimensions they did
 *    not have.
 */
export interface PadDerivation {
  ok: boolean;
  reason: string;
  padSamples: Float64Array;
  padNormals: Float64Array;
  padSide: Int8Array;
  padSeparationMm: number;
  padAngleRad: number;
}

export function derivePads(
  positions: Float64Array, indices: Uint32Array, options: {
    /** Half-width of the central column, mm. */
    columnHalfWidthMm?: number;
    /** Keep geometry within this much of the rearmost point in the column. */
    depthBandMm?: number;
  } = {},
): PadDerivation {
  const halfWidth = options.columnHalfWidthMm ?? 18;
  const band = options.depthBandMm ?? 6;

  // Face-ward is -Z in frame space, so the pads are at minimum Z in the column.
  let minZ = Infinity;
  const inColumn: number[] = [];
  for (let i = 0; i < positions.length / 3; i++) {
    if (Math.abs(positions[i * 3]) > halfWidth) continue;
    inColumn.push(i);
    minZ = Math.min(minZ, positions[i * 3 + 2]);
  }
  if (inColumn.length < 12) {
    return fail('no geometry in the central column — is this a frame mesh?');
  }

  const rear = inColumn.filter((i) => positions[i * 3 + 2] <= minZ + band);
  if (rear.length < 6) return fail('too few rearward vertices to form a pad surface');

  // Split by side. A saddle bridge has no gap and both clusters merge, which is
  // reported rather than forced.
  const right = rear.filter((i) => positions[i * 3] < -1.5);
  const left = rear.filter((i) => positions[i * 3] > 1.5);
  if (right.length < 3 || left.length < 3) {
    return fail('no distinct left and right contact clusters — saddle bridge?');
  }

  const normals = vertexNormals(positions, indices);
  const samples: number[] = [];
  const outNormals: number[] = [];
  const sides: number[] = [];
  const centroids: Vec3[] = [];

  for (const [cluster, side] of [[right, -1], [left, 1]] as const) {
    const c = v3();
    for (const i of cluster) {
      samples.push(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
      outNormals.push(normals[i * 3], normals[i * 3 + 1], normals[i * 3 + 2]);
      sides.push(side);
      c[0] += positions[i * 3]; c[1] += positions[i * 3 + 1]; c[2] += positions[i * 3 + 2];
    }
    c[0] /= cluster.length; c[1] /= cluster.length; c[2] /= cluster.length;
    centroids.push(c);
  }

  const separation = Math.abs(centroids[1][0] - centroids[0][0]);

  // Pad angle from the mean normal of the right cluster: the angle between its
  // inward direction and the -Z axis.
  let mnx = 0, mnz = 0;
  for (let k = 0; k < sides.length; k++) {
    if (sides[k] !== -1) continue;
    mnx += outNormals[k * 3];
    mnz += outNormals[k * 3 + 2];
  }
  const angle = Math.atan2(Math.abs(mnz), Math.abs(mnx));

  return {
    ok: true,
    reason: `${right.length} + ${left.length} contact samples`,
    padSamples: Float64Array.from(samples),
    padNormals: Float64Array.from(outNormals),
    padSide: Int8Array.from(sides),
    padSeparationMm: separation,
    padAngleRad: angle,
  };
}

const fail = (reason: string): PadDerivation => ({
  ok: false,
  reason,
  padSamples: new Float64Array(0),
  padNormals: new Float64Array(0),
  padSide: new Int8Array(0),
  padSeparationMm: NaN,
  padAngleRad: NaN,
});

function vertexNormals(positions: Float64Array, indices: Uint32Array): Float64Array {
  const out = new Float64Array(positions.length);
  for (let t = 0; t < indices.length; t += 3) {
    const a = indices[t] * 3, b = indices[t + 1] * 3, c = indices[t + 2] * 3;
    const e1x = positions[b] - positions[a];
    const e1y = positions[b + 1] - positions[a + 1];
    const e1z = positions[b + 2] - positions[a + 2];
    const e2x = positions[c] - positions[a];
    const e2y = positions[c + 1] - positions[a + 1];
    const e2z = positions[c + 2] - positions[a + 2];
    const nx = e1y * e2z - e1z * e2y;
    const ny = e1z * e2x - e1x * e2z;
    const nz = e1x * e2y - e1y * e2x;
    for (const v of [a, b, c]) { out[v] += nx; out[v + 1] += ny; out[v + 2] += nz; }
  }
  for (let i = 0; i < out.length; i += 3) {
    const l = Math.hypot(out[i], out[i + 1], out[i + 2]) || 1;
    out[i] /= l; out[i + 1] /= l; out[i + 2] /= l;
  }
  return out;
}
