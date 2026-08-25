/**
 * Occlusion-boundary snapping: the scan proposes, the image disposes.
 *
 * ## Why this exists
 *
 * The occlusion the viewer judges is the edge of their REAL nose in the video.
 * The occluder is the SCANNED nose at the TRACKED pose, and every error between
 * those two worlds — scan surface error (~0.6 mm), the detector's bias against
 * real skin (Q2, unmeasurable synthetically), the eye-corner depth bias (Q24),
 * per-frame pose noise — lands, summed, on that one boundary. Measured on the
 * first real wearer it comes to a few millimetres: a far rim visibly drawn on
 * top of the photographed nose before the cut arrives. No amount of geometry
 * work closes the last pixels, because a geometric occluder is being marked
 * against an image it never looks at.
 *
 * So this module looks at the image. Along the occluder's own predicted
 * contour, in a thin band, it searches the video's luminance for the strongest
 * edge crossing and reports the offset from where the geometry thought the
 * boundary was to where the image says it is — with a confidence, so that a
 * low-contrast band (skin against skin in flat light) degrades to exactly the
 * geometric behaviour rather than to a guess. The renderer then nudges the
 * occluder's silhouette vertices by those offsets: millimetre prior, pixel
 * answer.
 *
 * The research survey for the occlusion plan found this in the literature
 * (depth edges snapped to image edges — Holynski & Kopf 2018; Niantic 2023's
 * "small depth errors ruin the occlusion mask") and in NO shipped eyewear
 * try-on. The nearest in-tree relative is the card detector's edge search,
 * which walks a perpendicular band for the strongest gradient with a
 * confidence gate; this is the same discipline bent around a contour.
 *
 * ## Everything here is headless
 *
 * The module takes a depth buffer (the tree's own rasteriser) and a luminance
 * sampler function, and returns numbers. The browser hands it real video
 * pixels; the tests hand it synthetic edges with known offsets and assert
 * recovery — including the abstention cases, because a snapper that cannot
 * refuse is a snapper that hallucinates boundaries in flat light.
 */

import type { Pose } from '../core/linalg.js';
import type { Intrinsics } from '../core/camera.js';
import type { DepthBuffer } from '../core/raster.js';

/** One point on the occluder's predicted occluding contour, in SOURCE pixels. */
export interface ContourSample {
  /** Image position of the near-side (occluding) edge pixel, source px. */
  x: number;
  y: number;
  /** Unit image-space normal, pointing from the NEAR surface toward the far
   *  side — the direction along which the true edge is searched. */
  nx: number;
  ny: number;
  /** Depth of the near surface at this sample, mm — converts px to mm. */
  depthMm: number;
}

/**
 * Extracts the occluding contour from a depth buffer: pixels where the depth
 * jumps by more than `jumpMm` toward the neighbour (or falls off the mask
 * entirely). This includes INTERIOR occluding edges — the nose in front of the
 * far cheek — which the mask-boundary silhouette cannot see, and which are
 * exactly where a spectacle rim gets occluded at yaw.
 *
 * Coordinates come back in the buffer's own scale-free source pixels (the
 * buffer records its scale), sampled every `stride` buffer pixels to keep the
 * count in the low hundreds.
 */
export function occludingContour(
  buffer: DepthBuffer, options: { jumpMm?: number; stride?: number } = {},
): ContourSample[] {
  const jump = options.jumpMm ?? 6;
  const stride = Math.max(1, options.stride ?? 2);
  const W = buffer.width, H = buffer.height;
  const inv = 1 / buffer.scale;
  const depth = buffer.depth;
  const out: ContourSample[] = [];

  for (let y = 1; y < H - 1; y += stride) {
    for (let x = 1; x < W - 1; x += stride) {
      const o = y * W + x;
      const d = depth[o];
      if (!(d < Infinity)) continue;
      // The far side is either much deeper or off the mask. Accumulate the
      // direction toward it over the 4-neighbourhood so diagonal edges get a
      // diagonal normal rather than an axis-locked one.
      let gx = 0, gy = 0, edge = false;
      const consider = (dn: number, ddx: number, ddy: number) => {
        const farBy = (dn < Infinity ? dn - d : Infinity);
        if (farBy > jump) { edge = true; gx += ddx; gy += ddy; }
      };
      consider(depth[o - 1], -1, 0);
      consider(depth[o + 1], 1, 0);
      consider(depth[o - W], 0, -1);
      consider(depth[o + W], 0, 1);
      if (!edge) continue;
      const len = Math.hypot(gx, gy);
      if (!(len > 0)) continue;
      out.push({
        x: (x + 0.5) * inv, y: (y + 0.5) * inv,
        nx: gx / len, ny: gy / len,
        depthMm: d,
      });
    }
  }
  return out;
}

/** Bilinear luminance at source-pixel coordinates, 0..255. */
export type LuminanceSampler = (x: number, y: number) => number;

export interface SnapOptions {
  /** Half-width of the search band along the normal, source px. */
  searchPx: number;
  /** Directional-derivative magnitude below which a sample abstains. On 0..255
   *  luminance, skin-on-skin nose shading measures ~8-25 per px in ordinary
   *  light; sensor noise is ~1-3. */
  minGradient: number;
  /** Search step, source px. */
  stepPx: number;
}

export const SNAP_DEFAULTS: SnapOptions = {
  searchPx: 8,
  minGradient: 6,
  stepPx: 1,
};

export interface SnapResult {
  /** Signed offset along each sample's normal to the image's edge, source px.
   *  Positive = the real edge lies FARTHER out than the geometry thought
   *  (the occluder should grow). Zero where confidence is zero. */
  offsetPx: Float64Array;
  /** 0..1 per sample. Zero = abstained; the caller must treat the offset as
   *  absent, not as "the edge is exactly where geometry put it". */
  confidence: Float64Array;
}

/**
 * For each contour sample, walk the band [-searchPx, +searchPx] along the
 * normal and find the strongest luminance edge — the extremum of the
 * directional derivative — with sub-pixel refinement by parabola. Polarity is
 * deliberately ignored: whether the nose is lighter or darker than what it
 * occludes depends on the light, not on the geometry.
 *
 * Confidence is contrast against the band's own median response, so a band of
 * uniform skin abstains no matter how the absolute numbers scale.
 */
export function snapOffsets(
  samples: ContourSample[], luminance: LuminanceSampler,
  options: Partial<SnapOptions> = {},
): SnapResult {
  const opt = { ...SNAP_DEFAULTS, ...options };
  const n = samples.length;
  const offsetPx = new Float64Array(n);
  const confidence = new Float64Array(n);
  const steps = Math.max(3, Math.round((2 * opt.searchPx) / opt.stepPx) + 1);
  const responses = new Float64Array(steps);

  for (let i = 0; i < n; i++) {
    const s = samples[i];
    let best = -1, bestT = 0, bestIdx = -1;
    for (let k = 0; k < steps; k++) {
      const t = -opt.searchPx + k * opt.stepPx;
      const cx = s.x + s.nx * t, cy = s.y + s.ny * t;
      // Central difference along the normal: the edge response.
      const g = Math.abs(
        luminance(cx + s.nx, cy + s.ny) - luminance(cx - s.nx, cy - s.ny),
      ) / 2;
      responses[k] = g;
      if (g > best) { best = g; bestT = t; bestIdx = k; }
    }
    if (best < opt.minGradient) continue; // flat band: abstain

    // Contrast against the band's own typical response — a plain threshold
    // would make the gate exposure-dependent.
    const sorted = Float64Array.from(responses).sort();
    const median = sorted[steps >> 1];
    const conf = Math.max(0, Math.min(1, (best - median) / (best + 1e-6)));
    if (!(conf > 0.2)) continue;

    // The flank check: a real edge is a RIDGE — a sensor renders it over a
    // couple of pixels, so the peak's neighbours carry most of its response.
    // Structureless noise makes one-sample spikes whose neighbours fall back
    // to the band median (~0.35 of the peak on uniform grain, measured in the
    // test that forced this gate to exist). Requiring coherent flanks is what
    // lets the module keep a permissive gradient threshold without
    // hallucinating boundaries at night.
    if (bestIdx > 0 && bestIdx < steps - 1) {
      const flank = (responses[bestIdx - 1] + responses[bestIdx + 1]) / 2;
      if (flank < 0.45 * best) continue;
    }

    // Sub-pixel: parabola through the peak and its neighbours.
    let t = bestT;
    if (bestIdx > 0 && bestIdx < steps - 1) {
      const a = responses[bestIdx - 1], b = responses[bestIdx], c = responses[bestIdx + 1];
      const denom = a - 2 * b + c;
      if (Math.abs(denom) > 1e-9) {
        const shift = 0.5 * (a - c) / denom;
        if (Math.abs(shift) <= 1) t += shift * opt.stepPx;
      }
    }
    offsetPx[i] = t;
    confidence[i] = conf;
  }
  return { offsetPx, confidence };
}

/** A vertex push produced from the snapped contour, in FACE-space mm. */
export interface VertexPush {
  vertex: number;
  dx: number;
  dy: number;
  dz: number;
}

/**
 * Converts snapped contour offsets into face-space pushes on the occluder's
 * silhouette vertices.
 *
 * Each confident contour sample votes for the mesh vertices that project near
 * it (within `gatherPx`); each gathered vertex is pushed along the sample's
 * image normal, lifted to camera space at the vertex's depth (one source px at
 * depth Z is Z/f mm), then rotated into face space by the pose's inverse. The
 * push is capped: the snap corrects the last millimetres, and a correction
 * that could exceed `capMm` is a tracking failure wearing a snap's clothes —
 * refusing it is what keeps a bad frame looking like the geometric baseline
 * instead of like a hallucination.
 */
export function contourPushes(
  samples: ContourSample[], snap: SnapResult,
  positions: Float64Array, vertexCount: number,
  pose: Pose, intrinsics: Intrinsics,
  options: { gatherPx?: number; capMm?: number } = {},
): VertexPush[] {
  const gather = options.gatherPx ?? 6;
  const cap = options.capMm ?? 3;
  const { R, t } = pose;
  const f = intrinsics.f;

  // Project every vertex once; gather per sample. Counts are small (hundreds
  // of samples, 468 vertices), so the quadratic pass is microseconds.
  const px = new Float64Array(vertexCount);
  const py = new Float64Array(vertexCount);
  const pz = new Float64Array(vertexCount);
  for (let v = 0; v < vertexCount; v++) {
    const X = positions[v * 3], Y = positions[v * 3 + 1], Z = positions[v * 3 + 2];
    const cx = R[0] * X + R[1] * Y + R[2] * Z + t[0];
    const cy = R[3] * X + R[4] * Y + R[5] * Z + t[1];
    const cz = R[6] * X + R[7] * Y + R[8] * Z + t[2];
    pz[v] = cz;
    px[v] = intrinsics.cx + (f * cx) / cz;
    py[v] = intrinsics.cy + (f * cy) / cz;
  }

  const sumX = new Float64Array(vertexCount);
  const sumY = new Float64Array(vertexCount);
  const sumW = new Float64Array(vertexCount);
  for (let i = 0; i < samples.length; i++) {
    const c = snap.confidence[i];
    if (!(c > 0)) continue;
    const s = samples[i];
    const off = snap.offsetPx[i];
    for (let v = 0; v < vertexCount; v++) {
      const d = Math.hypot(px[v] - s.x, py[v] - s.y);
      if (d > gather) continue;
      const w = c * (1 - d / gather);
      sumX[v] += w * s.nx * off;
      sumY[v] += w * s.ny * off;
      sumW[v] += w;
    }
  }

  const out: VertexPush[] = [];
  for (let v = 0; v < vertexCount; v++) {
    if (!(sumW[v] > 1e-6)) continue;
    const offX = sumX[v] / sumW[v];
    const offY = sumY[v] / sumW[v];
    // px -> camera mm at this vertex's depth. Image y grows downward in CV
    // camera space, which is also +y — no sign gymnastics needed here.
    const scale = pz[v] / f;
    let camX = offX * scale, camY = offY * scale;
    const mag = Math.hypot(camX, camY);
    if (mag > cap) { camX *= cap / mag; camY *= cap / mag; }
    // Rotate the camera-space push into face space: R^T (no translation for a
    // direction).
    out.push({
      vertex: v,
      dx: R[0] * camX + R[3] * camY,
      dy: R[1] * camX + R[4] * camY,
      dz: R[2] * camX + R[5] * camY,
    });
  }
  return out;
}

// ------------------------------------------------------------- calibration

/**
 * The convergent form of the snap: a per-vertex correction FIELD that treats
 * the boundary error as what it physically is — a property of the wearer's
 * face, constant in face coordinates — rather than of the frame.
 *
 * The first, per-frame version applied each frame's pushes through a
 * decay-and-blend EMA, and the first real wearer caught its flaw in one
 * sentence: "if the user doesn't move, the edge of the glasses will." Per-frame
 * re-estimation leaks per-frame pose noise into a boundary that is stiller
 * than any estimate of it. So this class integrates instead: every confident
 * observation updates a confidence-weighted running mean, an agreement gate
 * throws away observations that disagree wildly with a converged estimate
 * (a hand crossing the face must not be "learned"), and once a vertex has
 * absorbed `weightCap` worth of confidence its correction is effectively
 * FROZEN — the update magnitude falls below a tenth of a millimetre and the
 * boundary becomes a constant of the session. Convergence also means the
 * caller can stop paying for the snap: a converged field only needs an
 * occasional drift-monitor frame, not 45 measurements a second.
 */
export interface CalibrationOptions {
  /** Total confidence a vertex can absorb; reaching it freezes the vertex.
   *  At ~0.5 confidence per good frame, 15 is ~30 frames — about a second. */
  weightCap: number;
  /** Observations farther than this from a converged estimate are rejected,
   *  mm. Wide enough for honest drift, narrow enough to refuse a passing
   *  hand or a hair strand. */
  agreementMm: number;
  /** A vertex is considered converged past this fraction of the cap. */
  convergedFraction: number;
}

export const CALIBRATION_DEFAULTS: CalibrationOptions = {
  weightCap: 15,
  agreementMm: 1.5,
  convergedFraction: 0.6,
};

/**
 * How fast the APPLIED correction may move, mm per second.
 *
 * The estimate and what the wearer is shown are deliberately different
 * things. `correction` is a running average, so its gain is 1 on the first
 * evidence and 1/n after: it lands the whole correction in one frame and
 * then oscillates down as the average settles. Measured on steady input,
 * the worst vertex moves 5.6 mm on frame one and 0.65, 0.42, 0.32, 0.22 mm
 * over the next four, quiet by about frame twelve. That is a third of a
 * second of the occluder — the depth mask deciding where the face hides the
 * glasses — visibly reshaping them, and it is exactly what the first wearer
 * to see it called "a wobble until they get steady, like 0.4 seconds".
 *
 * The estimator is not wrong; showing every intermediate value of it is.
 * This tree has already learned the general form of that lesson once, on the
 * latch's "breathe": a correction that arrives as an event is worse than one
 * that arrives continuously, even when the event is smaller in total. So the
 * applied field slews toward the estimate at a bounded rate and the wearer
 * sees a glide instead of a settle.
 *
 * 8.5 mm/s is the latch's own translation enter gate — the speed that file
 * measured as indistinguishable from stillness, and rate-caps its anchor
 * pursuit at for the same reason. Borrowed rather than re-derived, because
 * it is the same question about the same eye: 0.28 mm in a 30 fps frame,
 * and about 0.7 s to walk out a 5.6 mm correction that nobody sees move.
 */
export const SNAP_SLEW_MM_PER_S = 8.5;

export class CalibrationField {
  readonly correction: Float64Array;
  /** What to actually show: `correction` approached at a bounded rate. Use
   *  this, not `correction`, for anything the wearer looks at. */
  readonly applied: Float64Array;
  private readonly weight: Float64Array;
  private readonly opt: CalibrationOptions;
  private readonly vertexCount: number;

  constructor(vertexCount: number, options: Partial<CalibrationOptions> = {}) {
    this.vertexCount = vertexCount;
    this.correction = new Float64Array(vertexCount * 3);
    this.applied = new Float64Array(vertexCount * 3);
    this.weight = new Float64Array(vertexCount);
    this.opt = { ...CALIBRATION_DEFAULTS, ...options };
  }

  /**
   * Walks `applied` toward `correction`, no vertex moving faster than
   * `SNAP_SLEW_MM_PER_S`. Call once per rendered frame — including the
   * frames the snap itself skips once converged, or the glide stops
   * mid-stride.
   */
  advance(dt: number, mmPerSecond = SNAP_SLEW_MM_PER_S): Float64Array {
    const step = mmPerSecond * (dt > 0 ? dt : 1 / 30);
    for (let v = 0; v < this.vertexCount; v++) {
      const o = v * 3;
      const dx = this.correction[o] - this.applied[o];
      const dy = this.correction[o + 1] - this.applied[o + 1];
      const dz = this.correction[o + 2] - this.applied[o + 2];
      const d = Math.hypot(dx, dy, dz);
      if (d <= step || d === 0) {
        this.applied[o] = this.correction[o];
        this.applied[o + 1] = this.correction[o + 1];
        this.applied[o + 2] = this.correction[o + 2];
      } else {
        const k = step / d;
        this.applied[o] += dx * k;
        this.applied[o + 1] += dy * k;
        this.applied[o + 2] += dz * k;
      }
    }
    return this.applied;
  }

  /** Integrates one frame's pushes. Returns how many were absorbed. */
  update(pushes: VertexPush[], confidence = 1): number {
    let absorbed = 0;
    for (const push of pushes) {
      const v = push.vertex;
      const w = this.weight[v];
      const o = v * 3;
      if (w > 3) {
        const dx = push.dx - this.correction[o];
        const dy = push.dy - this.correction[o + 1];
        const dz = push.dz - this.correction[o + 2];
        if (Math.hypot(dx, dy, dz) > this.opt.agreementMm) continue; // outlier
      }
      const c = Math.min(confidence, 1);
      const wNew = Math.min(this.opt.weightCap, w + c);
      const gain = c / (w + c);
      this.correction[o] += gain * (push.dx - this.correction[o]);
      this.correction[o + 1] += gain * (push.dy - this.correction[o + 1]);
      this.correction[o + 2] += gain * (push.dz - this.correction[o + 2]);
      this.weight[v] = wNew;
      absorbed++;
    }
    return absorbed;
  }

  /** Fraction of ever-touched vertices that have converged. 1 when every
   *  vertex the snap has opinions about is frozen; 0 before any evidence. */
  convergence(): number {
    let touched = 0, converged = 0;
    const bar = this.opt.weightCap * this.opt.convergedFraction;
    for (let v = 0; v < this.vertexCount; v++) {
      if (this.weight[v] > 0) {
        touched++;
        if (this.weight[v] >= bar) converged++;
      }
    }
    return touched === 0 ? 0 : converged / touched;
  }

  reset(): void {
    this.correction.fill(0);
    this.applied.fill(0);
    this.weight.fill(0);
  }
}
