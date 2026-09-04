/**
 * The tracker configuration the app actually ships — in one place, so that the
 * instruments and the product cannot describe two different systems.
 *
 * ## Why this file exists
 *
 * `TRACKER_DEFAULTS` is the LIBRARY default: everything off, so a test or a
 * golden gets the bare solver and nothing surprises it. That is the right
 * default for a library and the wrong one for a measurement, and until
 * 2026-09-04 nothing said so out loud. The consequence, measured:
 *
 *   - `report-track.ts` built `createTracker(model, { smooth })` — the ONE
 *     option it thought to set — so every published tracking figure was
 *     produced with no motion prior, no rigidity map and no visibility, against
 *     an app that runs all three.
 *   - `report-occlusion.ts` built a bare `createTracker(scan)`, so its crawl
 *     figure did not even have the smoothing the app ships. It had computed the
 *     per-vertex visibility two lines earlier, used it to shape the sigma, and
 *     then dropped it on the floor rather than handing it to the solve.
 *
 * Neither was a typo. Both are what happens when "the shipped configuration"
 * exists only as a list of arguments at one call site in `app/main.ts` that a
 * headless module is forbidden to import. This file is that list, moved
 * somewhere both sides can reach, and `tests/app.test.ts` asserts the app uses
 * it rather than hand-rolling its own copy.
 *
 * ## What it does NOT decide
 *
 * The per-frame input. `track()` also takes `landmarks`, `sigmaPx`,
 * `visibility` and `dt`, and getting those wrong is just as capable of putting
 * a report on a system nobody ships — the missing `visibility` above was an
 * INPUT, not an option. `shippedSigma` covers the half of that a headless
 * caller can share; the landmarks themselves are necessarily the caller's.
 */

import { type FaceMesh, type Region, type SilhouetteStrip, trackingRigidity } from '../core/mesh.js';
import {
  UNCERTAINTY_DEFAULTS, acquisitionSigma, estimateSigma, type UncertaintyState,
} from '../detect/uncertainty.js';
import type { Intrinsics } from '../core/camera.js';
import type { Pose } from '../core/linalg.js';
import type { TrackerOptions } from './tracker.js';

export interface ShippedTrackerProfile {
  /** The template during acquisition, the wearer's solved model in wear. */
  mesh: FaceMesh;
  regions: Record<string, Region>;
  /**
   * `capture.width / detect.width`.
   *
   * The app runs the detector at a lower resolution than the source and scales
   * the landmarks up, so every pixel threshold has to be scaled with them. It
   * is **2 on a 1280-wide camera** and 1 for every headless caller, whose
   * landmarks are generated at the capture's own resolution. Defaulting it to 1
   * is therefore correct for the harness and wrong for the app, which is why
   * the app passes it explicitly.
   */
  pixelScale?: number;
  /** `app/main.ts` boots `true`. The wearer's Steady button moves it. */
  smooth?: TrackerOptions['smooth'];
  /** `app/main.ts` boots `true`; `?prior=off` is the wearer's A/B lever. */
  motionPrior?: boolean;
  /** `app/main.ts` boots this OFF (`?march=on` turns it on). */
  ovalStrips?: SilhouetteStrip[] | null;
}

/**
 * The options `createTracker` gets in the running app.
 *
 * Every field the app sets is set here, including the one that is currently
 * inert: `adaptiveFloorPx` is read only when `smooth === 'adaptive'`
 * (`tracker.ts`'s `noiseScale`), so on the shipped `smooth: true` it changes
 * nothing. It is in the profile anyway, because a profile that silently omits
 * the options that do not happen to matter today is a profile that stops
 * describing the app the moment one of them does.
 *
 * `UNCERTAINTY_DEFAULTS.floorPx` and `smoothing.ts`'s `ADAPTIVE_SIGMA_FLOOR_PX`
 * are both 0.7 and are not the same constant. The app scales the FORMER, so
 * this does too; if they ever diverge, this line follows the app.
 *
 * ## A live unit inconsistency, recorded rather than fixed
 *
 * `pixelScale` moves the sigma FLOOR (`uncertainty.ts` multiplies
 * `options.floorPx` by it) but nothing moves the sigma CUTOFF:
 * `TRACKER_DEFAULTS.maxSigmaPx` is a flat 12 and neither this profile nor the
 * app overrides it. So on a 1280-wide camera the app culls at 12 SOURCE px
 * against a 1.4 px floor — 8.6 floors — where the constant was chosen against a
 * 0.7 px detect-resolution floor, which is 17. The cull is roughly twice as
 * aggressive as the number was calibrated for, and it has been load-bearing
 * beyond the solve since 2026-09-01: `maxGrossFraction` folds its dropped
 * landmarks back in.
 *
 * `GATE_REFERENCE_F_PX`'s ledger row names the right unit — "it bounds the
 * SIGMA stream, whose unit travels through `pixelScale` (detect to source)
 * rather than through `f`" — while explaining why the FOCAL correction does not
 * apply to it. Nothing then applies the pixel one.
 *
 * It is not fixed here because **no instrument in this tree can grade the
 * change**: every headless caller runs at `pixelScale` 1, where scaling by it
 * is the identity, so the harness would report a bit-identical result for a
 * change that alters what the app culls on every frame. Correcting it on that
 * evidence would be the same mistake as the configuration drift this file
 * exists to close, in the opposite direction. It needs either a harness that
 * simulates the detect/source split or the real session that Q8 wants.
 */
export function shippedTrackerOptions(p: ShippedTrackerProfile): Partial<TrackerOptions> {
  return {
    smooth: p.smooth ?? true,
    motionPrior: p.motionPrior ?? true,
    rigidity: trackingRigidity(p.mesh, p.regions),
    adaptiveFloorPx: UNCERTAINTY_DEFAULTS.floorPx * (p.pixelScale ?? 1),
    ovalStrips: p.ovalStrips ?? null,
  };
}

// --------------------------------------------------------- the frame's input

export interface ShippedSigmaInput {
  /** The app's long-lived `UncertaintyState` — it caches a depth buffer. */
  state: UncertaintyState;
  /** Landmarks in SOURCE pixels, already scaled up from the detect resolution. */
  landmarks: Float64Array;
  mesh: FaceMesh;
  /**
   * The surface the detector's landmarks are taken to lie on:
   * `landmarkSurface(model)` once a model exists, the template mesh before.
   */
  positions: Float64Array;
  intrinsics: Intrinsics;
  /** The PREVIOUS frame's pose. Null before the first — never this frame's. */
  previousPose: Pose | null;
  /** See `ShippedTrackerProfile.pixelScale`. */
  pixelScale?: number;
}

/**
 * The `sigmaPx` and `visibility` the app hands `track()` every frame.
 *
 * **The app never sees a true sigma.** It rasterises the model at the PREVIOUS
 * pose to work out which vertices this frame can see, inflates the sigma of the
 * ones it cannot, and passes both halves down — so what ships is the tracker
 * *plus this estimator*, and a harness that feeds the synthesiser's true sigma
 * is measuring a system with an oracle in it. `report-track.ts` keeps a
 * `true-sigma` arm precisely so the two can be told apart.
 *
 * Two details that are easy to get wrong and were both paid for once already:
 *
 *  - `pixelScale`. The floor is calibrated at the DETECT resolution while the
 *    landmarks arrive in SOURCE pixels, so without it the sigma is half what it
 *    should be and everything downstream trusts it four times too much.
 *  - Before the first pose there is nothing to rasterise against, so visibility
 *    is `null` — "unknown" — and not a confident `fill(1)`. Handing the solve a
 *    confident 1 is the defect that made `noseObservations` equal `framesUsed`
 *    exactly on a real wearer's dump, which pinned `noseConfidence`'s
 *    observation term at 1.0 forever.
 */
export function shippedSigma(
  i: ShippedSigmaInput,
): { sigmaPx: Float64Array; visibility: Float64Array | null } {
  const pixelScale = i.pixelScale ?? 1;
  if (!i.previousPose) {
    return {
      sigmaPx: acquisitionSigma(i.mesh.vertexCount, {
        floorPx: UNCERTAINTY_DEFAULTS.floorPx * pixelScale,
      }),
      visibility: null,
    };
  }
  return estimateSigma(i.state, {
    landmarks: i.landmarks,
    mesh: i.mesh,
    positions: i.positions,
    intrinsics: i.intrinsics,
    pose: i.previousPose,
    pixelScale,
  });
}
