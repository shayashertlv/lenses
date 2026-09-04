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

import { v3, vnormalize } from '../core/linalg.js';

export type DimensionSource = 'measured' | 'cad' | 'scan-normalised' | 'assumed';

/** How a frame's ear rest was arrived at. See `FrameAsset.earRestSource`. */
export type EarRestSource = 'measured' | 'derived' | 'constructed' | 'assumed';

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
  /** Yaw of each pad's plane about the VERTICAL axis, radians — how far it turns
   *  toward the face, with no vertical component in it. A pad angled to match the
   *  wearer's sidewall bears flush; one that does not digs an edge in. Not a cone
   *  angle from the x axis: see `derivePads`, where those were one number until
   *  2026-08-26 and it was neither. */
  readonly padAngleRad: number;
  /** Pad face size, mm. Null when the frame came from a mesh: real geometry has
   *  a contact patch, not a rectangle, and inventing dimensions for it is how
   *  assets end up declaring numbers they do not have. */
  readonly padHeightMm: number | null;
  readonly padWidthMm: number | null;

  /** Overall front width, temple to temple, mm. */
  readonly frontWidthMm: number;
  /**
   * Lens optical centres in frame space.
   *
   * Documented for years as being "for the pupil-height verdict". There is no
   * pupil-height verdict in `score.ts` and there never has been. What actually
   * reads this is the vertex-distance verdict, the frame's centre of mass in
   * `contact.ts`'s `comOf`, the depth of the clearance ring, and the rim
   * geometry in `frame-layout.ts`.
   */
  readonly lensCentres: readonly [Float64Array, Float64Array];
  /** Where the arms leave the front, per side. */
  readonly hinges: readonly [Float64Array, Float64Array];
  /** Where the arms come to rest on the ears, per side, when unsplayed. */
  readonly earRests: readonly [Float64Array, Float64Array];
  /**
   * How `earRests` was arrived at, and therefore what the seat is worth.
   *
   * The ear rest is the single most sensitive input to the seat — measured over
   * 10 subjects x 5 specs, a reach of 60 mm buries the pads 12.2 mm and presses
   * the hook at 74x the frame's weight, where 90-100 mm is flat and quiet. So
   * where it came from has to travel with it rather than being lost at the
   * boundary, exactly as `dimensionSource` does for the width.
   *
   *   'measured'     a named temple part whose bend was walked directly
   *   'derived'       the arm found from the mesh's own geometry, its knee
   *                   fitted — a measurement of THIS asset either way
   *   'constructed'   placed by the spec, which for the rest means
   *                   `templeReachMm`: a swept constant, not a measurement of
   *                   any real frame. Parametric frames only, and the same word
   *                   `lensSource` uses for the same reason — though there it
   *                   means EXACT, because a spec really does place a lens
   *                   centre, and here it means the opposite
   *   'assumed'       a wrap or an earhook, which has no rest point at all: the
   *                   WEARER's ear supplies the reach and height, and only the
   *                   lateral position is the asset's own
   *
   * **The split between `derived` and `constructed` is the whole point of the
   * flag.** Parametric frames used to report `derived` alongside the mesh assets
   * whose arms were actually fitted, so the one question a consumer wants to ask
   * — was this reach measured off THIS frame? — could not be answered from here.
   * `score.ts`'s vertex caveat asked `dimensionSource` instead, and the width
   * and the reach travel together only by accident: measured over all fifteen
   * frames the tree can build, that key was wrong on seven of them.
   */
  readonly earRestSource: EarRestSource;
  /**
   * Whether `lensCentres` came from named lens parts or from the frame front.
   *
   * `'measured'` is the asset's own named lens parts. `'derived'` means it
   * names no lens anywhere and the centres are the extent centres of the
   * frontmost slice of the whole mesh — the rim opening, but with hinge and
   * forward-temple geometry in the slab. Good enough to place and draw a frame,
   * not good enough for the vertex-distance verdict, which `score.ts` withholds
   * on it: the measure is emitted as `'unknown'` with a null value, so the grade
   * scores neutral and no millimetre figure reaches the readout.
   *
   * `'constructed'` is a different thing and is NOT withheld. A parametric frame
   * places its centres at a fixed fraction of its own front width, so nothing
   * was measured off a real frame — but nothing was estimated either: the label
   * describes the plane the renderer actually draws, where a derived label
   * describes a quarter-slab of a scan the drawn lens is nowhere near.
   *
   * Read the verdict it allows with that in mind, though. The constant that
   * fixes the plane, `lensAheadOfPadsMm`, was chosen by sweeping it until the
   * vertex verdict landed in its own target band — its docstring prints the
   * sweep with 7 annotated as centring the 12-to-16 target — and measured over
   * 210 (face, frame) pairs a parametric frame grades 195 good, 15 fair and
   * never poor. That is a self-consistent number, not independent evidence, and
   * the one real wearer's frame in the record sat at 5 mm, below the whole
   * synthetic range.
   *
   * That withholding was documented here, and in `frame-from-mesh.ts`, from the
   * day the derived fallback was written, and for that whole time this field had
   * no reader — it was set, stored, and used only to build a note string. The
   * promise is now real. The other half of it never was: there is no
   * pupil-height verdict in `score.ts` to withhold, and `lensCentres`' own
   * comment above still describes one.
   */
  readonly lensSource: 'measured' | 'derived' | 'constructed';

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
  /**
   * Where the drawable geometry lives, when this asset came from a mesh.
   *
   * Null for a parametric frame, which the renderer builds out of the fields
   * above and nothing else.
   */
  readonly source: FrameSource | null;
}

/**
 * The file a mesh-backed frame was read from, and the one transform that puts
 * it where the seat solve thinks it is.
 *
 * **This exists so there is exactly one such transform.** `fit/frame-from-mesh.ts`
 * rotates the asset into frame space, scales it to its declared width and
 * re-centres it on the pad-contact origin, and every number it then measures —
 * pads, lens centres, hinges, ear rests — is expressed in that frame. The
 * renderer loads the same file again through three.js, for materials and
 * textures this tree's headless reader deliberately does not decode. If it
 * derived its own placement the two would agree until the day they did not,
 * and the failure would be a frame drawn a few millimetres from where it was
 * fitted — which looks like a tracking bug and is not one.
 *
 * So the renderer applies THIS matrix and computes nothing.
 */
export interface FrameSource {
  /** Path under the served root, as the app fetches it. */
  readonly url: string;
  /**
   * Row-major 4x4 taking the file's own coordinates into frame space.
   *
   * Row-major because that is what the rest of this tree writes; three.js wants
   * column-major, and `render/` is where that transpose belongs.
   */
  readonly meshToFrame: Float64Array;
}

export const GRAVITY_N_PER_G = 9.80665e-3; // newtons per gram

// ------------------------------------------------------------- parametric

export interface FrameSpec {
  id: string;
  name?: string;
  /** Distance between pad centroids, mm. Typical 14-22. */
  padSeparationMm: number;
  /**
   * Yaw of each pad's plane about the VERTICAL axis, radians.
   *
   * A pad is flush when this matches the wearer's nasal sidewall. On the
   * template that angle is `atan(0.60 / 0.76) = 0.67 rad` (38 degrees), from the
   * measured surface normal at pad height — which is the number a real pad arm
   * is bent to.
   *
   * **Note what that derivation drops.** `SKIN` records the template's sidewall
   * normal as `(-0.76, +0.24, +0.60)`, and `0.67` is `atan(|nz| / |nx|)` — the
   * `+0.24` vertical component is in the data and deliberately not in the
   * number. That is what settles the naming: this is a yaw, `parametricFrame`
   * inverts it as a yaw at line 293 (`n = (-side*cos a, 0, -sin a)`, `ny`
   * identically zero), and only the two measuring sites — both added later —
   * ever read it as a cone angle from the x axis.
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
    // Both follow from the spec's own fields rather than from any real frame:
    // the rest is `templeReachMm`, a swept constant, and the lens centres are
    // placed at a fixed fraction of the front width. Neither was measured off a
    // pair of glasses, and neither is a guess about the wearer.
    // `constructed`, not `derived`: nothing was fitted to this frame's arm
    // because there is no arm to fit — the rest is `templeReachMm`, the same
    // 95 mm every parametric shape carries. See `EarRestSource`.
    earRestSource: 'constructed',
    lensSource: 'constructed',
    provenance: 'parametric — generated from a FrameSpec, not measured',
    // No file behind it: the renderer builds this one from the fields above.
    source: null,
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
  /** Yaw of the contact normals about the VERTICAL axis, radians —
   *  `atan2(|nz|, |nx|)`. See the derivation for why it is not a cone angle. */
  padAngleRad: number;
  /** The downward lean the yaw drops, radians — `asin(-ny)`. An optician's
   *  frontal angle. Nothing reads it; it exists so that splitting one number
   *  into two did not silently discard the half this derivation recovers best. */
  padVerticalLeanRad: number;
}

/**
 * How far a face must lean toward the midline before it counts as pad contact
 * surface. Cosine of the angle between its normal and the inward x axis.
 *
 * `stated`, and the value matters less than the criterion. What makes it work
 * is that a nose pad is the only structure on a pair of glasses consisting of
 * TWO SURFACES THAT FACE EACH OTHER ACROSS THE MIDLINE. Everything else in the
 * central column — the bridge, the rim fronts, the lens edges — faces outward,
 * forward or back. So this one test both finds the pads and refuses objects
 * that have none, which is why the negative controls below need no special
 * handling: a sphere, a cylinder, a flat plate and the human face mesh all
 * have outward normals, and none of them has a surface looking at the midline.
 *
 * 0.35 is about 70 degrees off the x axis, which is generous — a real pad
 * leans 15 to 35 degrees so the band has ample room, and tightening it starts
 * discarding the outer edge of a curved pad before it discards anything else.
 *
 * **That 15–35 is a CONE angle and must stay one.** This gate tests `n . x`,
 * which is the cone angle from the x axis; `padAngleRad` in
 * `assets/glasses/ground-truth.json` is a YAW as of 2026-08-26 and is a smaller
 * number for the same pad (navigator 34.56 deg cone against 30.80 yaw, khronos
 * 16.77 against 7.95). Read `padConeAngleDeg` in that file, not `padAngleDeg` —
 * which is exactly why it carries all three angles rather than swapping one for
 * another.
 */
export const PAD_INWARD_COS = 0.35;

/**
 * How far a face's normal may sit from the PAD'S OWN mean normal and still
 * count as contact surface. Cosine.
 *
 * **`PAD_INWARD_COS` was doing two jobs and is only good at one of them.** It
 * has to be generous — 70 degrees off the x axis — because it is the FINDER:
 * the test that locates two surfaces facing each other across the midline and
 * refuses an object that has none, on assets whose pads sit at whatever angle
 * the author left them. Used as the SELECTOR it is far too loose. Measured over
 * the ten catalogue assets, the faces it admits span 19 to 29 degrees of normal
 * spread at the median, 38 to 55 at p90 and up to 80 at the worst, over 3.2 to
 * 13.5 mm of depth along the pad's own normal. A nose pad's contact face is a
 * patch a few millimetres deep; that is its whole inward-facing hemisphere,
 * sides and rolled-off edges included.
 *
 * The cost of sampling the edges is not cosmetic. `padSeatErrorArticulatedMm`
 * removes a rigid pivot from the gap field and reports what is left as
 * "unfixable curvature of THIS FACE", so an asset whose samples wrap around its
 * own pad reports that wrap against any nose. Before this gate existed the
 * wearer-facing pad verdict fired on 85 to 100% of faces for all ten derived
 * assets, against 2 to 10 of 29 for the parametric frames -- and it was
 * describing the asset.
 *
 * Measured against the pad's own mean normal rather than the x axis, because
 * that is the axis the question is about: a pad angled 40 degrees out still has
 * a flat contact face, and a cone about a fixed axis would keep the near half
 * of it and throw away the far half.
 *
 * **Derivation: graded against the pads two authors declared.** Nothing here
 * reads part names, so `sunglasses-khronos.glb`'s own `Nosepads` part is an
 * independent target. Precision is the fraction of derived samples that ARE an
 * authored face centroid — an exact match, not a tolerance, because the samples
 * are emitted verbatim:
 *
 *     cone      faces/side   on the authored pad   mean-normal error
 *     off           278            48.2%                20.0 deg
 *     0.85          196            60.2%                12.1
 *     0.90          167            69.5%                 8.2
 *     0.93          155            72.3%                 7.9
 *     0.95          145            77.2%                 7.6
 *     0.955         138            79.0%                 7.7
 *     0.96          120            80.8%                 8.2
 *     0.97          102            81.4%                 7.1
 *     0.972          49            55.1%                12.7   <- the cliff
 *     0.975          42            52.4%                12.5
 *     0.98           33            57.6%                15.0
 *
 * The 48.2% at the top is the number `PAD_REAR_COS`'s docstring records as
 * "~48%" and calls the case for treating this derivation as a CHECKER rather
 * than a producer. navigator, which is authored CAD, reads 100% at every value
 * and cannot choose between them.
 *
 * **Precision rises monotonically to 0.97 and then falls off a cliff**: past
 * about 0.972 the cone is tighter than the pad's own curvature, the face count
 * halves, and what survives is a flattest sliver no longer centred on the pad.
 * That edge sits at this pad's curvature scale — `acos(0.972)` is 13.6 degrees
 * — so a more curved pad would cliff at a WIDER cone, and the value has to
 * stand back from the worst one rather than sit at the best measured point.
 *
 * **0.955 gives up 2.4 points of precision for eight times the margin.** 0.97
 * scores 81.4% and sits 0.002 from the collapse; 0.955 scores 79.0% and sits
 * 0.017 from it. Failure is worse on the tight side: too loose contaminates the
 * patch, too tight replaces it with a sliver AND engages the `PAD_MIN_FACES`
 * floor, past which this constant stops mattering at all.
 *
 * **What it does to the population.** 15 frames x 29 faces, seed 11:
 *
 *                              pad depth mm    normal spread   fires >0.9
 *     the 5 parametric frames   0.00 (both)     0.0 (both)     unchanged
 *     navigator                 3.15 -> 1.26   22.6 -> 11.2    17 -> 1 /29
 *     khronos                  12.63 -> 4.16   17.4 ->  1.7    28 -> 5 /29
 *     horizon-amber             7.84 -> 2.15   25.5 ->  9.3    24 -> 1 /29
 *     horizon-sage             12.43 -> 3.31   26.4 -> 10.9    25 -> 1 /29
 *     crystal-parts            13.07 -> 2.77   27.5 ->  9.0    23 -> 4 /29
 *     meshy                     9.04 -> 2.34   26.7 ->  9.1    28 -> 2 /29
 *     TOTAL                                              65.1% -> 29.9%
 *
 * The parametric frames do not move at all, by construction: their pads are a
 * flat rectangle of identical normals, so every face is inside any cone and the
 * narrowing is the identity. `reports/seat.txt` is generated from those five and
 * is unaffected.
 *
 * **Three assets barely move**, and they are the honest residue:
 * `aviator-tortoiseshell` 29 -> 29, `shield-golden` 29 -> 29, `aviator-amber`
 * 28 -> 25. Their pads genuinely are the wrong shape for most of this
 * population, and better sampling does not change that.
 */
export const PAD_CONTACT_CONE_COS = 0.955;

/** Fewest inward-facing faces per side that can describe a pad surface. */
export const PAD_MIN_FACES = 20;

/**
 * How much of an asset's depth counts as 'the front', for deciding whether a
 * contact surface is where a nose pad would be.
 *
 * `stated`. Measured on the two assets that declare their pads, the contact
 * centroid sits 5% (navigator) and 3% (khronos) of the way back from the
 * frontmost point, because the depth range is set by the temple tips 140 mm
 * behind. A third is therefore enormous headroom in the direction that matters
 * and still refuses a back-to-front asset outright, whose pads land at 95%.
 */
export const PAD_FRONT_FRACTION = 1 / 3;

/**
 * How far a contact face must ALSO lean rearward, toward the wearer.
 *
 * `measured`. This is the test that separates a nose pad from the inner wall
 * of the lens aperture, which is the contaminant that made the inward test
 * alone insufficient: the aperture wall faces the midline just as squarely,
 * because it is the inside of a hole. What it does not do is lean back. On
 * navigator the aperture and the frame front read a mean z-normal of **exactly
 * 0.000** while the authored nose pads read **-0.106**, because a pad has to
 * face the nose and the nose sits behind the lens plane.
 *
 * Swept 0 to 0.12 against `assets/glasses/ground-truth.json`. On navigator the
 * answer is flat across the whole range — precision 100%, separation +0.42 mm
 * — because the contaminant sits at exactly zero and any positive requirement
 * removes all of it. 0.04 is taken from the middle of that plateau rather than
 * its edge, since a threshold of 0 is a knife edge against a surface that
 * measures 0.000.
 *
 * **It does not fix everything, and the number that says so is kept here.** On
 * sunglasses-khronos this threshold alone reaches ~48% precision and separation
 * lands +2.2 mm, because that asset's frame front is sculpted rather than flat
 * and carries genuinely rearward-leaning faces of its own. One of the two
 * gradeable assets passes a 90% precision bar and one does not; eight of ten
 * cannot be graded at all. That is the case for treating this derivation as a
 * CHECKER against declared geometry rather than as the producer of it.
 *
 * **`PAD_CONTACT_CONE_COS` took that 48% to 79% on 2026-08-27** by narrowing
 * the selection to the pad's contact face rather than its whole inward
 * hemisphere. It does not change the argument above — 79% is still not 90%, and
 * eight of ten assets still cannot be graded at all — but the figure quoted
 * here is this threshold's contribution, not the derivation's current score.
 */
export const PAD_REAR_COS = 0.04;

/**
 * How much of an asset's depth the "is it upside down?" guard measures against.
 *
 * `measured`, and it replaces a reference that was a knife edge.
 *
 * The guard asks whether the inward-facing surfaces sit below the frame. What
 * it used to compare them against was `(minY + maxY) / 2` of the WHOLE mesh —
 * and a temple that droops toward its tip drags `minY` down, so the reference
 * moves with a part of the asset that has nothing to do with which way up it
 * is. Scans droop further than authored frames, so the reference is worst
 * exactly where it is needed most.
 *
 * The margins it produced, at each asset's declared width (negative = passes,
 * and a correct guard is negative on every one of the ten):
 *
 *     asset                  whole-mesh midY   front slab 0.12
 *     aviator-amber                   +0.003            -2.101
 *     horizon-sage                    +2.011            -0.806
 *     meshy-glasses                   +1.397            -2.365
 *     sunglasses-khronos              -0.164            -4.250
 *     navigator                       -5.546            -7.011
 *     shield-golden                   -9.257           -12.782
 *
 * **`aviator-amber` refused by three microns.** `docs/HANDOFF.md` carried it as
 * an open question — "aviator-amber refuses while aviator-tortoiseshell derives,
 * and those two assets differ essentially only in texture" — and the answer is
 * that it was never a property of the asset. Amber's sign change lands 0.1 mm
 * from whatever front width somebody happened to declare; at 139.899 mm it
 * passes. The guard was reporting the catalogue's placeholder, not the geometry.
 *
 * Swept 0.05 to 0.50. **It is not monotone and the wide end is not safer**:
 *
 *     frac   worst margin over the ten    refusals
 *     0.05        +0.566 (khronos)           1
 *     0.08        -0.981                     0
 *     0.10        -0.587                     0
 *     0.12        -0.806                     0
 *     0.15        -0.875                     0
 *     0.20        -0.232                     0
 *     0.25        +0.362 (glasses01)         2
 *     0.33        -0.896                     0
 *     0.50        -1.658                     0
 *
 * The safe band is **0.08 to 0.15**, and 0.12 is its middle. 0.20 narrows to a
 * quarter of a millimetre and 0.25 fails outright, because a deeper slab starts
 * catching the tops of the temples on the assets whose temples rise. That 0.33
 * and 0.50 pass again is not a reason to prefer them: a parameter whose failures
 * are interior to its range is one whose safe values are coincidences, and the
 * plateau that is contiguous with the failure boundary is the only one worth
 * standing on.
 *
 * What would be better than any slab is the guard the pads' own anatomy
 * suggests — nose pads sit below the LENS CENTRES, always — but lens centres
 * need part names, and two of the ten assets name none (`crystal-parts` and
 * `meshy`). Corrected 2026-09-01 from "eight of the ten do not name one", which
 * inverted the count this argument rests on; `docs/CONSTANTS.md`'s row for this
 * constant was corrected on 2026-08-31 and this docstring, which it was copied
 * from, was not.
 */
export const PAD_UP_REFERENCE_FRACTION = 0.12;

/**
 * How lopsided the two sides' face counts may be before the pair is refused.
 *
 * `measured`. Two nose pads are a mirrored pair, so a derivation that found one
 * of them and half of something else is a derivation that found neither. The
 * face-count imbalance |L - R| / (L + R), over the ten assets as they arrive:
 *
 *     navigator            0.0000     (406 + 406)
 *     sunglasses-khronos   0.0000     (278 + 278)
 *     horizon-amber        0.0091     aviator-amber        0.0097
 *     meshy-glasses        0.0112     crystal-parts        0.0122
 *     aviator-tortoise     0.0172     horizon-sage         0.0231
 *     shield-golden        0.0257
 *     glasses01            0.4150     (2948 + 7130)  <- the one that is wrong
 *
 * The two assets with author-declared pads score EXACTLY zero, which is what a
 * mirrored pair should do and is the reason to believe the statistic. 0.15 is
 * six times the worst honest value and a third of the outlier.
 *
 * `glasses01-with-lenses` scores 0.415 because it arrives rotated ~40 degrees
 * off axis, so the central column cuts the frame diagonally and takes more of
 * one rim than the other. That makes it a real red case for this guard and a
 * standing reminder that the guard fires on a MIS-ORIENTED asset, not only on a
 * mis-shaped one — which is the more common fault and the harder one to see.
 */
export const PAD_SIDE_IMBALANCE_MAX = 0.15;

/**
 * Pad contact geometry, derived from a mesh.
 *
 * ## What this used to do, and why it was replaced
 *
 * The first version selected "the rearmost vertices inside a central column"
 * and reported their vertex normals. It could not refuse anything and it did
 * not measure a pad:
 *
 *  - It returned `ok: true` on all eleven assets of the catalogue as it then
 *    stood — v1's, since `fit/catalogue.ts` did not exist yet — on all eleven
 *    MIRRORED, on a Z-flipped frame, on a sphere, on a flat plate, on a
 *    cylinder and on the human face mesh. Zero refusals across 231 sane
 *    configurations. Its own docstring said "It reports when it failed."
 *  - `positions[i * 3 + 1]` — the Y coordinate — appeared nowhere in any test,
 *    only where samples were emitted, so an UPSIDE-DOWN frame derived
 *    byte-identical pads.
 *  - Against the two assets that ship author-named pads, 39% and 36% of what
 *    it returned lay on the actual pads; on `navigator`, 2,202 of 3,830
 *    samples came from `Frame_Front`. The rearmost sliver of a rounded pad
 *    points straight back by construction, so its normal is -Z whatever the
 *    pad's plane is doing, which drove `padAngleRad` toward 90 degrees on
 *    nine of those eleven independently of the asset.
 *
 * The selection now uses the criterion that defines the thing being looked
 * for. Samples are FACE centroids with FACE normals rather than vertices with
 * smoothed vertex normals, because a contact patch is a surface and its area
 * is what the seat solve is really integrating over.
 *
 * ## The refusals, and that they are reachable
 *
 * Every one of them is exercised by the must-fail battery in
 * `tests/pipeline.test.ts`, on real assets rather than on constructed toys,
 * because a guard whose only evidence is a three-vertex triangle is a guard
 * nobody has tested.
 */
export function derivePads(
  positions: Float64Array, indices: Uint32Array, options: {
    /** Half-width of the central column, mm. */
    columnHalfWidthMm?: number;
    /** How far a face must lean toward the midline. */
    inwardCos?: number;
    /** How far a face must also lean rearward, toward the wearer. */
    rearCos?: number;
    /** How far a face's normal may sit from the pad's own mean normal and
     *  still be contact surface rather than the pad's edge. */
    contactConeCos?: number;
  } = {},
): PadDerivation {
  const halfWidth = options.columnHalfWidthMm ?? 18;
  const inwardCos = options.inwardCos ?? PAD_INWARD_COS;
  const rearCos = options.rearCos ?? PAD_REAR_COS;
  const contactConeCos = options.contactConeCos ?? PAD_CONTACT_CONE_COS;

  if (indices.length < 3 || positions.length < 9) {
    return fail('not a mesh — no triangles to read a contact surface from');
  }

  // The asset's own centre, which is what "below" and "behind" are measured
  // against. Using the geometry's own extents rather than the origin means
  // this does not depend on where the author put 0,0,0.
  let minY = Infinity, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (let i = 0; i < positions.length / 3; i++) {
    const y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }
  // **Is it inside out?** Every face normal here comes from triangle winding,
  // so a mesh whose winding is reversed has every normal pointing into the
  // solid — and the inward-facing test then finds the BACK of each pad instead
  // of its contact face, happily, with a plausible-looking separation. The
  // signed volume of a closed mesh is positive when the winding is right.
  // Measured across all ten catalogue assets: every one is positive (5.2e3 to
  // 4.2e7), and every one mirrored without re-winding is negative.
  let signedVolume = 0;
  for (let t = 0; t + 2 < indices.length; t += 3) {
    const a = indices[t] * 3, b = indices[t + 1] * 3, c = indices[t + 2] * 3;
    signedVolume += (
      positions[a] * (positions[b + 1] * positions[c + 2] - positions[b + 2] * positions[c + 1])
      - positions[a + 1] * (positions[b] * positions[c + 2] - positions[b + 2] * positions[c])
      + positions[a + 2] * (positions[b] * positions[c + 1] - positions[b + 1] * positions[c])
    ) / 6;
  }
  if (signedVolume < 0) {
    return fail('the triangle winding is inverted — this mesh is inside out');
  }

  // The reference the up-check measures against: the mean height of the
  // frontmost slab of the asset. NOT `(minY + maxY) / 2` of the whole mesh,
  // which a drooping temple drags downward — see PAD_UP_REFERENCE_FRACTION for
  // the margins that reference produced, including the three microns by which
  // `aviator-amber` refused.
  const upReference = (() => {
    const cut = maxZ - (maxZ - minZ) * PAD_UP_REFERENCE_FRACTION;
    let sum = 0, n = 0;
    for (let i = 0; i < positions.length / 3; i++) {
      if (positions[i * 3 + 2] < cut) continue;
      sum += positions[i * 3 + 1];
      n++;
    }
    return n ? sum / n : (minY + maxY) / 2;
  })();
  // **Not the midpoint of Z.** The temples run 140 mm back from the front, so
  // the depth midpoint of a pair of glasses sits behind the wearer's ears and
  // every pad on earth is in front of it. What identifies a correctly-oriented
  // asset is that the pads are near the FRONT, just behind the lens plane.
  const frontZ = maxZ - (maxZ - minZ) * PAD_FRONT_FRACTION;

  interface Face { cx: number; cy: number; cz: number; nx: number; ny: number; nz: number; area: number }
  const right: Face[] = [];
  const left: Face[] = [];

  for (let t = 0; t + 2 < indices.length; t += 3) {
    const a = indices[t] * 3, b = indices[t + 1] * 3, c = indices[t + 2] * 3;
    const cx = (positions[a] + positions[b] + positions[c]) / 3;
    if (Math.abs(cx) > halfWidth) continue;
    const ux = positions[b] - positions[a];
    const uy = positions[b + 1] - positions[a + 1];
    const uz = positions[b + 2] - positions[a + 2];
    const vx = positions[c] - positions[a];
    const vy = positions[c + 1] - positions[a + 1];
    const vz = positions[c + 2] - positions[a + 2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz);
    if (!(len > 0)) continue; // a degenerate triangle describes no surface
    const area = len / 2;
    nx /= len; ny /= len; nz /= len;
    // A face left of the midline contacts toward +x, and vice versa.
    const inward = cx < 0 ? nx : -nx;
    if (inward <= inwardCos) continue;
    // ...and leaning back toward the wearer. The inner wall of the lens
    // aperture faces the midline exactly as squarely as a pad does; what it
    // does not do is lean rearward. See PAD_REAR_COS.
    if (nz >= -rearCos) continue;
    const face: Face = {
      cx,
      cy: (positions[a + 1] + positions[b + 1] + positions[c + 1]) / 3,
      cz: (positions[a + 2] + positions[b + 2] + positions[c + 2]) / 3,
      nx, ny, nz, area,
    };
    (cx < 0 ? right : left).push(face);
  }

  if (right.length < PAD_MIN_FACES || left.length < PAD_MIN_FACES) {
    return fail(
      `no pair of surfaces facing the midline (${right.length} + ${left.length} faces) — `
      + 'a saddle bridge, a wrap, or not a frame at all',
    );
  }

  // **Are they a PAIR?** Two nose pads are mirror images, so a wildly lopsided
  // count means the column caught one pad and part of something else. The two
  // assets with author-declared pads score exactly 0 here; the one asset that
  // arrives rotated off axis scores 0.415. See PAD_SIDE_IMBALANCE_MAX.
  const imbalance = Math.abs(left.length - right.length) / (left.length + right.length);
  if (imbalance > PAD_SIDE_IMBALANCE_MAX) {
    return fail(
      `the two inward surfaces are ${right.length} and ${left.length} faces `
      + `(imbalance ${imbalance.toFixed(3)}) — not a mirrored pair. A frame that arrives `
      + 'rotated off axis reads this way, because the central column then cuts it diagonally.',
    );
  }

  const summarise = (faces: Face[]) => {
    let sx = 0, sy = 0, sz = 0, nx = 0, ny = 0, nz = 0, area = 0;
    for (const f of faces) {
      sx += f.cx * f.area; sy += f.cy * f.area; sz += f.cz * f.area;
      nx += f.nx * f.area; ny += f.ny * f.area; nz += f.nz * f.area;
      area += f.area;
    }
    const nlen = Math.hypot(nx, ny, nz) || 1;
    return {
      cx: sx / area, cy: sy / area, cz: sz / area,
      nx: nx / nlen, ny: ny / nlen, nz: nz / nlen,
      area,
    };
  };

  const r = summarise(right);
  const l = summarise(left);

  // **Is it the right way up?** Nose pads sit below the lens centres, always.
  // The old version never read Y at all, so an upside-down asset derived
  // byte-identical pads and nothing could tell.
  if ((r.cy + l.cy) / 2 > upReference) {
    return fail(
      'the inward surfaces sit above the front of the frame — is this upside down? '
      + `(${((r.cy + l.cy) / 2 - upReference).toFixed(3)} mm above)`,
    );
  }

  // **Is it the right way round?** Pads are on the wearer's side, behind the
  // lens plane, and -Z is face-ward.
  if ((r.cz + l.cz) / 2 < frontZ) {
    return fail('the inward surfaces sit back among the temples — is this back to front?');
  }

  // The REFUSAL runs on the finder's faces, because it is asking whether there
  // are two pads here at all; the reported figure comes off the contact faces
  // below, because that is the separation the seat actually rests on.
  if (!(Math.abs(l.cx - r.cx) > 4)) {
    return fail(`contact surfaces ${Math.abs(l.cx - r.cx).toFixed(1)} mm apart `
      + '— one surface, not two pads');
  }

  // **Found, then SELECTED.** Everything above answers "is there a pad here";
  // this answers "which of these faces is the part that touches". They are
  // different questions and `PAD_INWARD_COS` was being asked both — see
  // `PAD_CONTACT_CONE_COS` for what the second answer costs when the first one
  // gives it.
  //
  // The cone is taken about each side's OWN mean normal, and the mean is then
  // recomputed from what survived and the cut applied once more. One
  // refinement, not a loop: the first mean is pulled off-axis by exactly the
  // edge faces being removed, so a single re-cut is worth having and a second
  // one moves nothing measurable.
  // **Keep the best `PAD_MIN_FACES` rather than falling back to everything.**
  // An all-or-nothing fallback puts a cliff in the middle of the parameter: the
  // first draft of this took the whole hemisphere back the moment the cone left
  // 19 faces, and the sweep went non-monotone — `aviator-amber` read a 4.36 mm
  // deep patch at 0.95, a 9.28 mm one at 0.90 and 7.85 at 0.85, because the
  // fallback fired at one value and not its neighbours. A constant whose
  // failures are interior to its range is one whose safe values are
  // coincidences — the same argument `PAD_UP_REFERENCE_FRACTION` makes about
  // its own sweep. Sorting and taking a floor is continuous everywhere.
  //
  // Refusing instead would be wrong: the asset HAS pads, the finder above just
  // proved it, and a derivation that refuses a frame it has already located is
  // worse than one that reports a coarser surface.
  const narrow = (faces: Face[], mean: { nx: number; ny: number; nz: number }): Face[] => {
    const scored = faces
      .map((f) => ({ f, a: f.nx * mean.nx + f.ny * mean.ny + f.nz * mean.nz }))
      .sort((p, q) => q.a - p.a);
    const inside = scored.filter((s) => s.a >= contactConeCos);
    return (inside.length >= PAD_MIN_FACES ? inside : scored.slice(0, PAD_MIN_FACES))
      .map((s) => s.f);
  };
  const refine = (faces: Face[], mean: { nx: number; ny: number; nz: number }): Face[] => {
    const once = narrow(faces, mean);
    return narrow(once, summarise(once));
  };
  const contactRight = refine(right, r);
  const contactLeft = refine(left, l);
  // **Everything reported comes off the CONTACT faces, not the finder's.**
  // Until 2026-08-27 the samples came from one set and the separation and
  // angles from the other, which is both inconsistent and measurably worse:
  // the finder's set is the pad's whole inward hemisphere, so its centroid sits
  // wherever the pad's sides pull it and its mean normal is the average of a
  // wrap rather than of a contact face. Against the two authored pads, moving
  // these three onto the contact faces:
  //
  //     asset      quantity      from the finder   from the contact faces
  //     khronos    separation      +2.24 mm             +0.30 mm
  //     khronos    yaw            +17.75 deg            +7.71 deg
  //     khronos    lean            -3.93                +2.21
  //     navigator  separation      +0.42 mm             -0.19 mm
  //     navigator  yaw            +10.42 deg           +10.53 deg
  //     navigator  lean            -2.08                -0.82
  //
  // Better or level on five of six, and the two that move most are khronos's,
  // which is the asset the finder is only 48% precise on.
  const cr = summarise(contactRight);
  const cl = summarise(contactLeft);
  const separation = Math.abs(cl.cx - cr.cx);

  const samples: number[] = [];
  const outNormals: number[] = [];
  const sides: number[] = [];
  // Face centroids VERBATIM, and that is worth stating because it was briefly
  // not. Projecting them onto a best-fit quadric to remove tessellation noise
  // was tried and measured: after the contact cone above it removes only
  // 0.02 mm on navigator and 0.39 on khronos, and it destroys the only
  // independent grading this derivation has — `tests/asset.test.ts` scores
  // precision by matching derived samples against the authored pad's own face
  // centroids EXACTLY, which is a crisp number precisely because the samples
  // are verbatim. Smoothed, that score becomes a function of the match
  // tolerance (18% at 0.05 mm, 67% at 0.25, 93% at 0.5) and stops measuring
  // anything. A second-order cleanup that costs a first-order instrument is
  // not worth having.
  for (const [faces, side] of [[contactRight, -1], [contactLeft, 1]] as const) {
    for (const f of faces) {
      samples.push(f.cx, f.cy, f.cz);
      outNormals.push(f.nx, f.ny, f.nz);
      sides.push(side);
    }
  }

  // **`padAngleRad` is a YAW about the vertical axis**, not a cone angle from
  // the x axis. It is the quantity `parametricFrame` inverts to build a pad
  // plane — `n = (-side*cos a, 0, -sin a)`, with `ny` identically zero — and the
  // one `SKIN`'s `atan(0.60 / 0.76) = 0.67 rad` was derived as, from a template
  // sidewall normal of `(-0.76, +0.24, +0.60)` whose vertical component is
  // present in the data and deliberately absent from the constant.
  //
  // This measured the CONE angle `atan2(hypot(ny, nz), |nx|)` until 2026-08-26,
  // and on a parametric frame the two are identical — `ny` is exactly 0, so
  // every round-trip test passed. On a real pad they are not: a pad's normal
  // leans DOWN as well as in (mean |ny| is 0.31 on navigator and 0.32 on
  // khronos), and the two definitions differ by 6.7 and 8.5 degrees.
  const yaw = (n: { nx: number; nz: number }) =>
    Math.atan2(Math.abs(n.nz), Math.abs(n.nx));
  // The vertical component the yaw drops, kept rather than discarded: it is a
  // real property of a pad — an optician's frontal angle — and this derivation
  // recovers it BETTER than it recovers the yaw, 2.1 degrees out on navigator
  // against 10.4. Nothing reads it yet; it is here so that splitting the two
  // angles does not throw one of them away.
  const drop = (n: { ny: number }) => Math.asin(Math.max(-1, Math.min(1, -n.ny)));
  const angle = (yaw(cr) + yaw(cl)) / 2;
  const vertical = (drop(cr) + drop(cl)) / 2;

  return {
    ok: true,
    reason: `${right.length} + ${left.length} inward faces, ${(r.area + l.area).toFixed(0)} mm2 of contact`,
    padSamples: Float64Array.from(samples),
    padNormals: Float64Array.from(outNormals),
    padSide: Int8Array.from(sides),
    padSeparationMm: separation,
    padAngleRad: angle,
    padVerticalLeanRad: vertical,
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
  padVerticalLeanRad: NaN,
});
