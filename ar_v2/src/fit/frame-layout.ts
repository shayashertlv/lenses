/**
 * Where a parametric frame's structure is, said once.
 *
 * `render/frame-geometry.ts` draws it. `testkit/report-occlusion.ts` samples it
 * to measure how much of it the face hides. Those two files used to describe the
 * same object independently, with each header naming the other as a twin that
 * had to be kept in step by hand — and the twin had already drifted.
 *
 * ## The drift, measured
 *
 * **The bridge was 4.000000 mm apart, pure +Y, at every one of its 16 samples.**
 * The renderer drops the whole lens box by `LENS_DROP_MM` and the instrument
 * dropped the rims but not the bridge: it wrote `r[1] + (l[1] - r[1]) * t` where
 * the renderer writes `r[1] - LENS_DROP_MM`.
 *
 * Four millimetres sounds cosmetic and was not. The drawn bridge is a cylinder
 * of radius `BRIDGE_TUBE_MM` = 1.6 mm, so the instrument's samples missed the
 * drawn solid by **2.4 mm of clear air** — nothing it reported about the bridge
 * was about geometry that existed. And it changed the answer in the direction
 * that flatters: moving the samples onto the drawn bridge flips 7 to 11 of 80
 * from visible to hidden, raising the bridge's hidden fraction from 31.3% to
 * 40.0% at yaw 0 and from 37.5% to 51.3% at yaw 30. The instrument was
 * under-reporting occlusion by 9 to 14 percentage points, because its samples
 * rode 4 mm up the nose dorsum into its shallowest millimetres — which is the
 * exact defect `LENS_DROP_MM` exists to avoid, described in that constant's own
 * docstring.
 *
 * Everything else agreed bit-for-bit: rim half-axes to 0.000e+0 on all five
 * catalogue frames, rim centres likewise, and both temple polylines land exactly
 * on `hinges[]` and `earRests[]`. One number, in one place, wrong for as long as
 * the twin existed.
 *
 * ## Why the collapse could not simply be "make them import each other"
 *
 * `render/` imports three.js; `testkit/` must load in Node with no browser, and
 * `scripts/check-isolation.mjs` enforces that by actually `import()`ing every
 * built module under `core/ enroll/ track/ fit/ detect/ testkit/`. A testkit
 * module importing `render/frame-geometry.js` would make Node resolve `three` —
 * which is a vendored browser file, not a dependency — and the gate would fail.
 *
 * `testkit/` is disqualified in the other direction: `testkit/fixtures.ts`
 * imports `node:fs`, and nothing stops `render/` importing testkit textually, so
 * a module there is one careless import away from pulling `node:fs` into the
 * page. A new top-level directory would be worse still — `check-isolation.mjs`
 * iterates a hardcoded list, so `src/frame/` would be scanned by nothing at all.
 *
 * `fit/` is gated today, for free, and `render/ -> fit/` is the legal direction
 * and already exercised (`scene.ts` imports `FrameAsset` from here).
 *
 * ## What this does NOT describe
 *
 * A mesh-backed asset. `navigator.glb` draws its own 68,638 triangles and none
 * of the arithmetic below has anything to do with them — `rimHalfAxes` is a
 * guess about where a rim would be if this frame were parametric, and for a real
 * asset it is simply a different shape in a different place. `describesDrawn`
 * says so, and the occlusion instrument refuses rather than reporting numbers
 * about geometry nobody drew.
 */

import type { FrameAsset } from './frame-asset.js';

/**
 * How far the rim and lens box sit BELOW the asset's lens-centre line, mm.
 *
 * A worn frame's lens centre is a few millimetres below the pupil — the trade
 * fits the pupil at the upper third of the box. `FrameAsset.lensCentres` carry
 * the FIT meaning (vertex distance, height verdicts) and are not touched; this
 * is a placement offset for the drawn box.
 *
 * It also matters for occlusion, and that is how it was found: the first real
 * wearer's far rim crossed the nose at the ROOT, its shallowest millimetres,
 * because round rims centred on the eye line never reached the dorsum where
 * occlusion is strong. Dropping the box moves the crossing down into it.
 */
export const LENS_DROP_MM = 4;

/** Rim tube radius, mm — a typical acetate wall. Cosmetic; nothing measures it. */
export const RIM_TUBE_MM = 1.7;
/** Bridge tube radius, mm. Cosmetic. */
export const BRIDGE_TUBE_MM = 1.6;
/** Temple cross-section across the head, mm. Cosmetic. */
export const TEMPLE_THICKNESS_MM = 2.6;
/** Temple cross-section vertically, mm. Cosmetic. */
export const TEMPLE_HEIGHT_MM = 4.4;
/** How far the lens disc is inset inside the rim ellipse, so the tube overlaps it. */
export const LENS_INSET_MM = 0.6;

/** An ellipse in a Z-normal plane. */
export interface RimEllipse {
  /** Frame-local millimetres, drop already applied. */
  readonly centre: Float64Array;
  /** Half-width across the face. */
  readonly a: number;
  /** Half-height. */
  readonly b: number;
}

export interface Segment {
  readonly from: Float64Array;
  readonly to: Float64Array;
}

export interface FrameLayout {
  /** The rim tube centrelines. */
  readonly rims: readonly [RimEllipse, RimEllipse];
  /** The lens discs, inset inside the rims by `LENS_INSET_MM`. */
  readonly lenses: readonly [RimEllipse, RimEllipse];
  /** Inner rim edge to inner rim edge. */
  readonly bridge: Segment;
  /** Rim outer edge to hinge, per side. */
  readonly endpieces: readonly [Segment, Segment];
  /** Hinge to ear rest, per side. Straight — the tree's documented Q6 approximation. */
  readonly temples: readonly [Segment, Segment];
  /**
   * Whether this describes the geometry that is actually DRAWN.
   *
   * False for a mesh-backed asset, where the renderer draws the file's own
   * triangles and everything here is a parametric stand-in for a shape that is
   * not on screen. A consumer that measures the picture must refuse; a consumer
   * that only wants a coarse extent may proceed knowing what it has.
   */
  readonly describesDrawn: boolean;
}

/**
 * The rim half-axes, derived from the asset's own layout.
 *
 * The first version used `0.11 * frontWidthMm` — `clearanceSamples`' convention
 * — which renders a 30 mm lens on a 138 mm front. A real lens box is ~50 mm and
 * the first real wearer saw the gap immediately: tiny rims floating mid-face
 * that the nose could never occlude, and a bare hinge gap between rim and temple.
 *
 * The honest derivation from what the spec actually measures: the rim's inner
 * edge belongs at the bridge, just clear of the pad gap, so the half-width is the
 * lens centre's offset minus half the pad separation minus a margin — capped so
 * the outer edge leaves room for the endpiece before the hinge, and floored at
 * the old value so it can never shrink below what shipped. Height is 0.8 of
 * width, the trade's usual box aspect. On the 'standard' asset (front 138,
 * centres +-31.7, pads 17) that gives a ~= 21.7 mm — a 43 mm lens whose inner
 * edge sits ~10 mm from the centreline, where a nose can finally shadow it.
 */
function rimHalfAxes(asset: FrameAsset): { a: number; b: number } {
  const cx = Math.abs(asset.lensCentres[0][0]);
  const inner = cx - asset.padSeparationMm / 2 - 1.5; // nose side sets the size
  const outerCap = asset.frontWidthMm / 2 - cx - 1;   // never past the hinge
  const floor = asset.frontWidthMm * 0.11;            // never below the old size
  const a = Math.max(floor, Math.min(inner, outerCap));
  return { a, b: a * 0.8 };
}

export function frameLayout(asset: FrameAsset): FrameLayout {
  const { a, b } = rimHalfAxes(asset);

  const centre = (s: 0 | 1): Float64Array => {
    const c = asset.lensCentres[s];
    return Float64Array.of(c[0], c[1] - LENS_DROP_MM, c[2]);
  };
  const rimR = { centre: centre(0), a, b };
  const rimL = { centre: centre(1), a, b };

  const bridge: Segment = {
    from: Float64Array.of(rimR.centre[0] + a, rimR.centre[1], rimR.centre[2]),
    to: Float64Array.of(rimL.centre[0] - a, rimL.centre[1], rimL.centre[2]),
  };

  // Endpieces: rim outer edge to hinge. Without these the first real wearer saw
  // a bare gap between rim and temple — the hinge sits at the front's half-width
  // while the rim ends at its own outer edge, and nothing spanned the difference.
  const endpiece = (s: 0 | 1): Segment => {
    const c = s === 0 ? rimR.centre : rimL.centre;
    const sign = s === 0 ? -1 : 1;
    return {
      from: Float64Array.of(c[0] + sign * a, c[1], c[2]),
      to: Float64Array.from(asset.hinges[s]),
    };
  };

  const temple = (s: 0 | 1): Segment => ({
    from: Float64Array.from(asset.hinges[s]),
    to: Float64Array.from(asset.earRests[s]),
  });

  return {
    rims: [rimR, rimL],
    lenses: [
      { centre: rimR.centre, a: a - LENS_INSET_MM, b: b - LENS_INSET_MM },
      { centre: rimL.centre, a: a - LENS_INSET_MM, b: b - LENS_INSET_MM },
    ],
    bridge,
    endpieces: [endpiece(0), endpiece(1)],
    temples: [temple(0), temple(1)],
    // A mesh-backed asset draws its file's own triangles; nothing above is on
    // screen for it.
    describesDrawn: asset.source === null,
  };
}

/** A point on an ellipse at parameter `t` in [0, 1). */
export function ellipsePoint(e: RimEllipse, t: number): Float64Array {
  const th = t * Math.PI * 2;
  return Float64Array.of(
    e.centre[0] + e.a * Math.cos(th),
    e.centre[1] + e.b * Math.sin(th),
    e.centre[2],
  );
}

/** A point along a segment at parameter `t` in [0, 1]. */
export function segmentPoint(s: Segment, t: number): Float64Array {
  return Float64Array.of(
    s.from[0] + (s.to[0] - s.from[0]) * t,
    s.from[1] + (s.to[1] - s.from[1]) * t,
    s.from[2] + (s.to[2] - s.from[2]) * t,
  );
}
