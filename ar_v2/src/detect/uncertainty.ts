/**
 * Per-landmark uncertainty — the signal that replaces v1's trust ramps.
 *
 * ## Why this file is the hinge of the whole redesign
 *
 * v1 decided how much to believe a measurement from the *pose*: three smooth
 * ramps on yaw, pitch and roll, multiplied together. That is an indirect
 * estimate of an indirect thing. What actually degrades at a turned pose is not
 * "the frame", it is **specific landmarks** — the ones on the far side of the
 * face, which the detector cannot see and is therefore inventing. A pose-level
 * weight cannot express that: it discounts the visible near-side landmarks
 * exactly as hard as the invented far-side ones, and it does so on a schedule
 * somebody had to tune, per axis, against one person's captures.
 *
 * Per-landmark sigma expresses it directly. A hidden landmark arrives with a
 * large sigma and contributes almost nothing to the solve; the visible ones keep
 * their full weight. Nothing has to know what yaw is.
 *
 * ## The ideal case, and the case we are in
 *
 * A detector of the 703-dense-landmark class predicts each landmark as a 2D
 * Gaussian — position *and* uncertainty — because it was trained to. Where such
 * a model is available, this file is three lines: read the sigma it reports.
 *
 * MediaPipe does not report uncertainty. So this file estimates it from what
 * MediaPipe does give, and the estimate is deliberately conservative: it is
 * better to under-trust a good landmark than to over-trust an invented one,
 * because the first costs precision and the second costs correctness.
 *
 * Three signals, combined in quadrature:
 *
 *  1. **Self-occlusion**, from the model's own geometry at the previous pose.
 *     This is the big one and it is the only one that knows the far cheek is
 *     hidden. It requires a pose, so on the very first frame it is unavailable
 *     and everything falls back to (2) and (3).
 *  2. **Temporal disagreement**: how much this landmark has moved relative to
 *     the rigid motion of the whole set. A landmark that jumps while its
 *     neighbours do not is a landmark the detector is unsure about, whatever it
 *     claims. This is the signal that catches blinks, hair and hands.
 *  3. **A floor**, the detector's measured noise on a still, frontal face.
 */

import { type Intrinsics } from '../core/camera.js';
import { type Pose, clamp } from '../core/linalg.js';
import { computeVertexNormals, type FaceMesh } from '../core/mesh.js';
import {
  createDepthBuffer, normalsToCamera, rasterize, vertexVisibility, type DepthBuffer,
} from '../core/raster.js';

export interface UncertaintyOptions {
  /**
   * The detector's own noise on a well-lit, frontal, still face, px at the
   * detection resolution.
   *
   * Measured, not assumed: `npm run report:detector` drives a still image
   * through the detector with seeded sub-pixel jitter and reports the spread.
   * 0.7 px is the figure at 640 px long side that this default carries until
   * that report runs against a real camera. See `docs/OPEN-QUESTIONS.md` Q1.
   */
  floorPx: number;
  /**
   * How much a fully-hidden landmark's sigma is inflated.
   *
   * Seven, because a hallucinated landmark is not merely noisier — it is biased
   * toward the detector's own prior, which is the average face. Under that bias
   * the error does not average out over frames, so the inflation has to be large
   * enough that a hidden landmark cannot outvote a visible one however many
   * frames it appears in.
   */
  occludedInflation: number;
  /** Weight on the temporal-disagreement term, px per px of residual. */
  disagreementGain: number;
  /** Resolution of the visibility buffer's long side. */
  rasterWidth: number;
}

export const UNCERTAINTY_DEFAULTS: UncertaintyOptions = {
  floorPx: 0.7,
  occludedInflation: 7,
  disagreementGain: 0.6,
  rasterWidth: 160,
};

export interface UncertaintyState {
  options: UncertaintyOptions;
  buffer: DepthBuffer | null;
  normalsCam: Float64Array;
  /** Per-landmark EMA of the unexplained motion, px. */
  disagreement: Float64Array;
  previous: Float64Array | null;
  vertexCount: number;
}

export function createUncertainty(
  vertexCount: number, options: Partial<UncertaintyOptions> = {},
): UncertaintyState {
  return {
    options: { ...UNCERTAINTY_DEFAULTS, ...options },
    buffer: null,
    normalsCam: new Float64Array(vertexCount * 3),
    disagreement: new Float64Array(vertexCount).fill(0),
    previous: null,
    vertexCount,
  };
}

export interface UncertaintyInput {
  landmarks: Float64Array;
  mesh: FaceMesh;
  positions: Float64Array;
  intrinsics: Intrinsics;
  /** The previous frame's pose, or null on acquisition. */
  pose: Pose | null;
  /**
   * Pixels of `landmarks` per pixel of the detect canvas. **This is not
   * optional bookkeeping — it was a silent factor of four on every real scan.**
   *
   * `floorPx` is calibrated at the DETECTION resolution, 640 px on the long
   * side. But the app scales the detector's output up to source pixels before
   * calling here (`scaleLandmarksToSource`), so at the default 1280-wide capture
   * the landmarks arrive twice as large as the floor that describes them. Every
   * sigma was therefore half what it should have been, and since a covariance
   * goes as sigma squared, the bundle believed its landmarks **four times** more
   * than it should.
   *
   * The fingerprint is the a-posteriori variance factor: a real wearer's scans
   * measured about 3.5 where the synthetic harness gives 1.6, and the harness
   * never touches this code path — it feeds `sigmaPx` straight from its own
   * noise model, so no test could see it.
   *
   * 1 when the landmarks are already in detect pixels.
   */
  pixelScale?: number;
}

/**
 * Estimates per-landmark sigma in pixels.
 *
 * Returns the array it owns — callers must not retain it across frames.
 */
export function estimateSigma(
  state: UncertaintyState, input: UncertaintyInput,
): { sigmaPx: Float64Array; visibility: Float64Array } {
  const { options } = state;
  const n = state.vertexCount;
  // The floor has to be in the same pixels as the landmarks it bounds.
  const floorPx = options.floorPx * (input.pixelScale ?? 1);
  const sigma = new Float64Array(n).fill(floorPx);
  const visibility = new Float64Array(n).fill(1);

  // ---- 1. self-occlusion --------------------------------------------------
  if (input.pose) {
    const height = Math.round(
      (options.rasterWidth * input.intrinsics.height) / input.intrinsics.width,
    );
    if (!state.buffer || state.buffer.width !== options.rasterWidth) {
      state.buffer = createDepthBuffer(options.rasterWidth, height, input.intrinsics);
    }
    const { px, depth } = rasterize(
      state.buffer, input.positions, input.mesh.indices, n, input.pose, input.intrinsics,
    );
    const normals = computeVertexNormals(input.positions, input.mesh.indices, n);
    normalsToCamera(state.normalsCam, normals, n, input.pose);
    const vis = vertexVisibility(state.buffer, px, depth, n, state.normalsCam);
    for (let i = 0; i < n; i++) {
      visibility[i] = vis[i];
      const hidden = 1 - vis[i];
      // Quadratic in hiddenness, so a landmark that is merely oblique keeps most
      // of its weight and one that is genuinely behind the head loses almost all
      // of it. A linear ramp here is v1's mistake in a new place: it treats
      // "slightly turned" and "invisible" as points on one line.
      sigma[i] *= 1 + (options.occludedInflation - 1) * hidden * hidden;
    }
  }

  // ---- 2. temporal disagreement ------------------------------------------
  if (state.previous) {
    // The rigid part of the frame-to-frame motion, as a median translation.
    // A median rather than a mean, because the whole point is to be unmoved by
    // the landmarks that are about to be flagged.
    const dx: number[] = [];
    const dy: number[] = [];
    for (let i = 0; i < n; i++) {
      const a = input.landmarks[i * 2], b = state.previous[i * 2];
      if (Number.isNaN(a) || Number.isNaN(b)) continue;
      dx.push(a - b);
      dy.push(input.landmarks[i * 2 + 1] - state.previous[i * 2 + 1]);
    }
    if (dx.length > 20) {
      const mx = median(dx);
      const my = median(dy);
      for (let i = 0; i < n; i++) {
        const a = input.landmarks[i * 2], b = state.previous[i * 2];
        if (Number.isNaN(a) || Number.isNaN(b)) continue;
        const rx = (a - b) - mx;
        const ry = (input.landmarks[i * 2 + 1] - state.previous[i * 2 + 1]) - my;
        const r = Math.hypot(rx, ry);
        // EMA so one noisy frame does not condemn a landmark for the session,
        // and so a landmark that becomes reliable again recovers.
        state.disagreement[i] += 0.25 * (r - state.disagreement[i]);
        sigma[i] = Math.hypot(sigma[i], options.disagreementGain * state.disagreement[i]);
      }
    }
  }

  state.previous = new Float64Array(input.landmarks);

  for (let i = 0; i < n; i++) {
    if (Number.isNaN(input.landmarks[i * 2])) sigma[i] = Infinity;
    else sigma[i] = clamp(sigma[i], floorPx, 1e5);
  }

  return { sigmaPx: sigma, visibility };
}

function median(values: number[]): number {
  const s = [...values].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/**
 * The sigma to use before any pose exists — acquisition, frame one.
 *
 * Uniform, because there is genuinely nothing to distinguish the landmarks by
 * yet. Slightly inflated over the floor so that a first solve is appropriately
 * humble; the second frame has geometry and does better.
 */
export function acquisitionSigma(
  vertexCount: number, options: Partial<UncertaintyOptions> = {},
): Float64Array {
  const opt = { ...UNCERTAINTY_DEFAULTS, ...options };
  return new Float64Array(vertexCount).fill(opt.floorPx * 2);
}
