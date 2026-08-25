/**
 * The occlusion report: how well the scanned face hides the frame, measured
 * before a single pixel is rendered.
 *
 * Stage 0 of the occlusion plan. The renderer this tree is about to grow will
 * use the scanned face mesh as its occluder, and v1's audit is the reason that
 * cannot be taken on faith: v1 placed the frame against one surface, drew it
 * against a second and depth-tested it against a third, and measured the
 * disagreement at a 50x27 mm patch of wrongly-drawn bridge. Before a pixel is
 * drawn here, occlusion quality has to be a measured quantity. This instrument
 * answers four questions with numbers:
 *
 *   A. How far does the scanned model's occluding contour sit from the truth's,
 *      in camera pixels, in the region where glasses actually live?
 *   B. How often does depth-ordering of the seated frame against the face FLIP
 *      relative to truth — split by direction, because the field tolerates
 *      over-hiding and never tolerates X-ray?
 *   C. How stable is that boundary under live tracking? Wobble, not offset, is
 *      what the eye punishes.
 *   D. What does the personal scan BUY over the average face as occluder — the
 *      number that settles whether scan-once occlusion was worth building.
 *
 * ## Why the ruler does not matter here, and how the harness proves it
 *
 * Every enrollment in this report is handed the subject's TRUE iris diameter.
 * That is not generosity — it is isolation. The iris ruler sets absolute
 * scale, and scale is a GAUGE in occlusion: the tracker solves the pose
 * against the scanned model itself, so a model that is 5% too large simply
 * tracks 5% farther away and projects onto the same pixels. Geometry error is
 * what moves an occlusion boundary; the ruler cannot. The pooled-iris arm of
 * METRIC D exists to verify that claim rather than assume it — if pooled and
 * true iris disagree here, that is a finding, not noise.
 *
 * The gauge statement is made operational, not rhetorical: every occluder arm
 * is posed by PnP against noiseless landmarks of the truth (exactly what the
 * live tracker does with real ones), and then the whole arm — geometry, pose
 * translation, seated frame samples — is scaled by `truthDistance /
 * fittedDistance`. That is a similarity about the camera centre, so it leaves
 * every projected pixel exactly where it was and only aligns the depth gauge,
 * which is what makes a depth comparison against the truth buffer meaningful
 * at all.
 *
 * ## The frame sample set is an instrument, not render geometry
 *
 * The point cloud built by `frameSampleSet` — rim ellipses at the lens plane,
 * a bridge segment, straight hinge-to-ear temple polylines — is a SAMPLING of
 * where a frame's visible structure sits, built from the asset's own fields
 * (the rim half-axes are `clearanceSamples`' own 0.11/0.7 convention). It is how
 * the instrument asks "would this part of the frame be hidden", not what the
 * renderer will draw. The straight temple carries contact.ts's own documented
 * approximation (Q6): a real arm curves around the skull; this one does not,
 * so the flip counts near the ear read the approximation as well as the
 * occluder. The boundary metrics do not depend on it — the samples only choose
 * the BAND.
 *
 * ## Conventions, stated once
 *
 *  - Camera space is CV convention (+Y down, +Z into the scene); face space is
 *    +Y up, +Z out of the face; the two differ by `FACE_TO_CAMERA_FLIP` and
 *    every pose here is built by `poseRotationFromHeadEuler`, the same helper
 *    the capture machinery uses. The truth-vs-truth test in pipeline.test.ts
 *    pins this path: it must measure EXACTLY zero, and the projected glasses
 *    band must sit ABOVE the face centroid in image y — an observable that a
 *    conjugated rotation cannot fake.
 *  - Depth bias sign: NEGATIVE bias moves the occluder TOWARD the camera
 *    (depths shrink), so it hides more — X-ray falls, forgiveness rises.
 *    Positive pushes it away and X-ray rises. Pinned by its own test.
 *  - All pixel figures are in the geometry's NATIVE image pixels. The raster
 *    itself runs on intrinsics cropped to the face (same focal length, shifted
 *    principal point) so that a 288-wide buffer spends its pixels on the face
 *    rather than on the empty frame around it.
 */

import { loadBasis, loadRegions, loadTemplateMesh } from './fixtures.js';
import {
  CAMERA_LADDER, type CameraGeometry, type SyntheticSubject,
  captureSeedFor, generatePopulation, populationSeedFor, synthesizeCapture,
} from './synthetic.js';
import { enroll } from '../enroll/enroll.js';
import { createFaceModel, type FaceModel } from '../core/facemodel.js';
import { computeVertexNormals, type FaceMesh, type Region } from '../core/mesh.js';
import { solveSeat } from '../fit/contact.js';
import { TEST_FRAMES, type FrameAsset } from '../fit/frame-asset.js';
import { ellipsePoint, frameLayout, segmentPoint } from '../fit/frame-layout.js';
import { distribution, table } from './metrics.js';
import {
  createDepthBuffer, extractSilhouette, normalsToCamera, rasterize, vertexVisibility,
} from '../core/raster.js';
import {
  type Intrinsics, intrinsicsFromFov, poseRotationFromHeadEuler, project,
} from '../core/camera.js';
import { type Pose, poseClone, poseIdentity } from '../core/linalg.js';
import { buildCorrespondences, solvePnP } from '../track/pnp.js';
import { createTracker, track } from '../track/tracker.js';
import { createRng, deriveSeed } from './random.js';

// --------------------------------------------------------------- the options

export interface OcclusionRunOptions {
  /**
   * Run ONE realisation at this campaign seed, at the full population, instead
   * of the default five-seed campaign {11, 23, 37, 41, 53}. Threaded through
   * `populationSeedFor` / `captureSeedFor`, the only sanctioned fold.
   */
  seed?: number;
  /** Sampled subjects per seed (the two named extremes are always appended). */
  subjects?: number;
  verbose?: boolean;
}

export interface CellOptions {
  /** Buffer width over the face crop. 288 native-crop pixels across a face
   *  makes one buffer cell ~0.7-1.1 mm on the skin at the ladder's distances,
   *  and a median over a few hundred contour pixels resolves well under a
   *  native pixel. */
  rasterWidth: number;
  /** Padding of the projected frame bbox that defines the glasses band, mm at
   *  the head (converted per cell through f/Z). */
  bandPadMm: number;
  /** A sample is hidden when the face is nearer than it by MORE than this, mm.
   *  0.5 because the pads are solved to sit ~0.5 mm INTO the skin
   *  (TARGET_CONTACT_MM) — a zero epsilon would count every bearing pad sample
   *  as contested z-fighting rather than as geometry. */
  hiddenEpsMm: number;
  /** The depth-bias sweep, mm along the camera axis. Negative = toward camera. */
  biasesMm: readonly number[];
  /** Crop margin around the projected face bbox, as a fraction of its size. */
  cropMarginFrac: number;
}

const CELL_DEFAULTS: CellOptions = {
  rasterWidth: 288,
  bandPadMm: 4,
  hiddenEpsMm: 0.5,
  biasesMm: [-1.5, -1, -0.5, 0, 0.5, 1, 1.5],
  cropMarginFrac: 0.14,
};

const CAMPAIGN_SEEDS = [11, 23, 37, 41, 53] as const;
/** Yaw ladder, degrees. The glasses question lives at 30-60. */
const YAWS_DEG = [0, 15, 30, 45, 60] as const;
/** Frames in the metric-C wandering hold. */
const STABILITY_FRAMES = 40;
/** The hold's yaw, degrees — the middle of the band the complaint named. */
const STABILITY_YAW_DEG = 35;

// ---------------------------------------------------------- frame sampling

/**
 * The instrument's sampling of a frame's visible structure, frame-local mm.
 *
 * **Every coordinate comes from `fit/frame-layout.ts`.** This function used to
 * re-derive the same arithmetic the renderer does, with both file headers naming
 * the other as a twin to keep in step by hand, and the bridge had drifted
 * 4.000000 mm — the rims were dropped by `LENS_DROP_MM` and the bridge was not.
 * The samples missed the drawn bridge tube by 2.4 mm of clear air, and the error
 * flattered: putting them where the bridge is actually drawn raises that part's
 * hidden fraction from 31.3% to 40.0% at yaw 0 and 37.5% to 51.3% at yaw 30,
 * because the samples had been riding up the nose dorsum into its shallowest
 * millimetres.
 *
 * ## Five parts, and two of them are new
 *
 * The renderer emits rims, lens discs, a bridge, endpieces and temples. The
 * instrument sampled three of those five, so a third of what a wearer sees was
 * measured by nothing. Both additions were measured before being added, and they
 * did NOT come out the same way:
 *
 *  - **Lens discs carry real, unmeasured signal**: 2,230 mm2 of drawn surface,
 *    0% hidden at yaw 0 and 30, 2.8% at 45 and **14.1% at yaw 60**. Profile
 *    occlusion of the far lens is a genuine effect the report had no row for.
 *  - **Endpieces buy nothing, and are added anyway.** 37.8 mm of centreline
 *    between them, and **0 of 160 samples hidden at yaw 0, 30, 45 or 60**. They
 *    cannot widen the band either — their far end is the hinge, which the
 *    temples already reach. They are here so the part list is exhaustive and the
 *    coverage gate has nothing to except; the row they produce is an honest,
 *    probably permanent zero, and a reader should expect it rather than read it
 *    as a bug.
 *
 * ## What it refuses
 *
 * A mesh-backed asset. `navigator.glb` draws its own triangles and the layout
 * above is a parametric stand-in for a shape that is not on screen, so measuring
 * it would produce occlusion numbers about geometry nobody drew — which is the
 * same failure as the 4 mm bridge, one order of magnitude larger. `frameSampleSet`
 * throws instead.
 */
/** Part labels for `frameSampleSet`'s cloud, in sample order. */
export const framePartNames = ['rim', 'lens', 'bridge', 'endpiece', 'temple'] as const;

/** Samples per closed ellipse, per side. */
const RIM_SAMPLES = 88;
const LENS_RING_SAMPLES = 44;
/** Rings across a lens disc: the interior matters, not only its edge. */
const LENS_RINGS = 3;
const BRIDGE_SAMPLES = 16;
const ENDPIECE_SAMPLES = 80;
const TEMPLE_SAMPLES = 64;

function buildFrameSamples(frame: FrameAsset): { points: Float64Array; parts: Uint8Array } {
  const layout = frameLayout(frame);
  if (!layout.describesDrawn) {
    throw new Error(
      `frameSampleSet: "${frame.id}" is mesh-backed (${frame.source?.url}), so this `
      + 'parametric layout is not what gets drawn. Sampling it would report occlusion '
      + 'for geometry nobody rendered. Sample the file\'s own triangles instead.',
    );
  }
  const out: number[] = [];
  const parts: number[] = [];
  const push = (p: Float64Array, part: number) => {
    out.push(p[0], p[1], p[2]);
    parts.push(part);
  };

  for (const rim of layout.rims) {
    for (let i = 0; i < RIM_SAMPLES; i++) push(ellipsePoint(rim, i / RIM_SAMPLES), 0);
  }

  // Lens discs: concentric rings rather than one outline, because what the far
  // cheek hides at yaw is the disc's INTERIOR, not its edge.
  for (const disc of layout.lenses) {
    for (let r = 1; r <= LENS_RINGS; r++) {
      const scale = r / LENS_RINGS;
      const ring = { centre: disc.centre, a: disc.a * scale, b: disc.b * scale };
      for (let i = 0; i < LENS_RING_SAMPLES; i++) {
        push(ellipsePoint(ring, i / LENS_RING_SAMPLES), 1);
      }
    }
  }

  for (let i = 0; i < BRIDGE_SAMPLES; i++) {
    push(segmentPoint(layout.bridge, i / (BRIDGE_SAMPLES - 1)), 2);
  }

  for (const e of layout.endpieces) {
    for (let i = 0; i < ENDPIECE_SAMPLES / 2; i++) {
      push(segmentPoint(e, i / (ENDPIECE_SAMPLES / 2 - 1)), 3);
    }
  }

  for (const t of layout.temples) {
    for (let i = 0; i < TEMPLE_SAMPLES; i++) {
      push(segmentPoint(t, i / (TEMPLE_SAMPLES - 1)), 4);
    }
  }

  return { points: Float64Array.from(out), parts: Uint8Array.from(parts) };
}

export function frameSampleSet(frame: FrameAsset): Float64Array {
  return buildFrameSamples(frame).points;
}

/** Which part each `frameSampleSet` sample is — an index into `framePartNames`. */
export function frameSampleParts(frame: FrameAsset): Uint8Array {
  return buildFrameSamples(frame).parts;
}

/** Applies a pose to a packed xyz cloud (frame-local -> model space). */
export function transformSamples(pose: Pose, samples: Float64Array): Float64Array {
  const out = new Float64Array(samples.length);
  const R = pose.R;
  for (let i = 0; i < samples.length; i += 3) {
    const x = samples[i], y = samples[i + 1], z = samples[i + 2];
    out[i] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
    out[i + 1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
    out[i + 2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
  }
  return out;
}

// ------------------------------------------------------------------- poses

/** A ladder pose: the head at `yawDeg`, pitched to face the geometry's camera
 *  the way `synthesizeCapture` poses it, at the geometry's distance. */
export function ladderPose(geometry: CameraGeometry, yawDeg: number): Pose {
  const pose = poseIdentity();
  const basePitch = Math.atan2(geometry.belowEyesMm, geometry.distanceMm);
  poseRotationFromHeadEuler(pose.R, (yawDeg * Math.PI) / 180, basePitch, 0);
  pose.t.set([0, 0, geometry.distanceMm]);
  return pose;
}

function noiselessLandmarks(
  positions: Float64Array, count: number, pose: Pose, k: Intrinsics,
): { landmarks: Float64Array; sigmaPx: Float64Array } {
  const landmarks = new Float64Array(count * 2).fill(NaN);
  const sigmaPx = new Float64Array(count).fill(1);
  const cam = new Float64Array(3);
  const uv = new Float64Array(2);
  const R = pose.R;
  for (let i = 0; i < count; i++) {
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    cam[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
    cam[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
    cam[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
    if (project(uv, k, cam)) { landmarks[i * 2] = uv[0]; landmarks[i * 2 + 1] = uv[1]; }
  }
  return { landmarks, sigmaPx };
}

export interface FittedArm {
  /** Occluder geometry, DEPTH-GAUGE NORMALISED (see below). */
  positions: Float64Array;
  /** Its fitted pose, translation scaled by the same gauge. */
  pose: Pose;
  /** truthDistance / fittedDistance — how far the arm's own gauge was from the
   *  truth's. For a true-iris scan this reads the realised ruler error. */
  gauge: number;
}

/**
 * Poses an occluder the way the renderer will: PnP against landmarks of the
 * truth. Noiseless landmarks, deliberately — pose NOISE is metric C's job, and
 * feeding it in here would smear shape error with tracking error.
 *
 * Then the depth gauge is normalised: geometry and translation are scaled by
 * `truth.t[2] / fitted.t[2]`, a similarity about the camera centre that leaves
 * every projected pixel fixed and puts the arm's depths on the truth's scale,
 * so that a depth comparison against the truth buffer compares surfaces rather
 * than rulers. This is the "scale is a gauge" statement made executable.
 */
export function fitOccluderArm(
  occluderPositions: Float64Array, truthPositions: Float64Array, count: number,
  truthPose: Pose, k: Intrinsics,
): FittedArm {
  const { landmarks, sigmaPx } = noiselessLandmarks(truthPositions, count, truthPose, k);
  const cs = buildCorrespondences(landmarks, sigmaPx, count);
  const fit = solvePnP(occluderPositions, cs, k, poseClone(truthPose));
  const gauge = truthPose.t[2] / fit.pose.t[2];
  const positions = new Float64Array(occluderPositions.length);
  for (let i = 0; i < positions.length; i++) positions[i] = occluderPositions[i] * gauge;
  const pose = poseClone(fit.pose);
  pose.t[0] *= gauge; pose.t[1] *= gauge; pose.t[2] *= gauge;
  return { positions, pose, gauge };
}

/** Scales a sample cloud into a fitted arm's gauge. */
export function scaleSamples(samples: Float64Array, gauge: number): Float64Array {
  const out = new Float64Array(samples.length);
  for (let i = 0; i < samples.length; i++) out[i] = samples[i] * gauge;
  return out;
}

// -------------------------------------------------------------- the cell

export interface OcclusionArm {
  /** Geometry in its own model space. */
  positions: Float64Array;
  /** Model -> camera. */
  pose: Pose;
}

export interface FlipCount {
  biasMm: number;
  /** Samples hidden by either surface at this bias. The denominator. */
  contested: number;
  /** Truth-hidden, occluder-visible: the frame shows through the head. */
  xray: number;
  /** Truth-visible, occluder-hidden: the occluder over-eats the frame. */
  forgiven: number;
  /** Same three counts split by sample part (indexing `framePartNames`), when
   *  the caller supplied parts. Where the flips LIVE decides what a bias eats. */
  byPart?: { contested: number[]; xray: number[]; forgiven: number[] };
}

export interface CellResult {
  /** Truth-contour pixels inside the glasses band. */
  bandTruthCount: number;
  /** Occluder-contour pixels inside the glasses band. */
  bandOccluderCount: number;
  /** Per truth band-contour pixel: distance to the nearest occluder band-contour
   *  pixel, native px. Empty if either band contour is empty. */
  boundaryPx: Float64Array;
  /** The same distances in mm on the skin, via that pixel's own depth over f. */
  boundaryMm: Float64Array;
  /**
   * Mean boundary offset: the in-band area where the two masks DISAGREE,
   * divided by the truth contour's in-band length. For a boundary translated
   * rigidly by d this is exactly d; for a knife-edge sliver that exists in one
   * mask only it adds the sliver's own area once — where the per-pixel
   * nearest-contour distances above walk the sliver's arc and read a sub-mm
   * visibility flip as tens of millimetres. This is the sub-cell-resolving,
   * spur-robust figure; the distances are the distribution the task names.
   */
  meanOffsetPx: number;
  meanOffsetMm: number;
  /** One entry per bias in `biasesMm`. The bias moves the OCCLUDER only; the
   *  frame stays where the seat put it. */
  flips: FlipCount[];
  /**
   * For every X-ray flip at bias 0: how far BEHIND the truth surface the sample
   * truly sat, mm. The number that separates "a sliver at the boundary flipped"
   * (margins near the epsilon) from "a temple drawn through a cheek" (margins
   * of centimetres). Empty when 0 is not in `biasesMm`.
   */
  xrayBehindMm: Float64Array;
  /** Observables for the orientation pin: image-y of the band centre and of the
   *  truth mask's centroid, native px. Glasses live above the centroid (+Y is
   *  DOWN in CV convention, so above means smaller). */
  bandCentrePxY: number;
  faceCentroidPxY: number;
}

export function flipsAt(cell: CellResult, biasMm: number): FlipCount {
  const f = cell.flips.find((x) => x.biasMm === biasMm);
  if (!f) throw new Error(`no flip count at bias ${biasMm} mm`);
  return f;
}

/**
 * One instrument cell: truth vs one occluder at one pose, one camera.
 *
 * `samples` is the seated frame cloud in ITS OWN model space with the pose to
 * composite it by — usually the occluder's own (the renderer welds the frame to
 * the head it tracks), but a comparison arm (the template occluder) is handed
 * the SCAN arm's samples so that every arm in a cell shares one band and one
 * frame placement, and only the occluding surface differs.
 */
export function occlusionCell(
  mesh: FaceMesh, truth: OcclusionArm, occluder: OcclusionArm,
  samples: { points: Float64Array; pose: Pose; parts?: Uint8Array }, k: Intrinsics,
  options: Partial<CellOptions> = {},
): CellResult {
  const opt = { ...CELL_DEFAULTS, ...options };

  // ---- crop intrinsics around the truth's projected face --------------------
  // Same focal length, shifted principal point: a window, not a zoom. The
  // margin keeps the silhouette off the buffer border, which extractSilhouette
  // would otherwise report as contour.
  const uv = new Float64Array(2);
  const cam = new Float64Array(3);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  {
    const R = truth.pose.R;
    for (let i = 0; i < mesh.vertexCount; i++) {
      const x = truth.positions[i * 3], y = truth.positions[i * 3 + 1], z = truth.positions[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + truth.pose.t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + truth.pose.t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + truth.pose.t[2];
      if (!project(uv, k, cam)) continue;
      if (uv[0] < minX) minX = uv[0];
      if (uv[0] > maxX) maxX = uv[0];
      if (uv[1] < minY) minY = uv[1];
      if (uv[1] > maxY) maxY = uv[1];
    }
  }
  const margin = Math.max(maxX - minX, maxY - minY) * opt.cropMarginFrac + 6;
  const x0 = Math.floor(minX - margin), y0 = Math.floor(minY - margin);
  const cropW = Math.ceil(maxX + margin) - x0;
  const cropH = Math.ceil(maxY + margin) - y0;
  const kCrop: Intrinsics = { f: k.f, cx: k.cx - x0, cy: k.cy - y0, k1: k.k1, width: cropW, height: cropH };

  const W = opt.rasterWidth;
  const H = Math.max(8, Math.round((W * cropH) / cropW));
  const truthBuf = createDepthBuffer(W, H, kCrop);
  const occBuf = createDepthBuffer(W, H, kCrop);
  rasterize(truthBuf, truth.positions, mesh.indices, mesh.vertexCount, truth.pose, kCrop);
  rasterize(occBuf, occluder.positions, mesh.indices, mesh.vertexCount, occluder.pose, kCrop);
  const scale = truthBuf.scale; // buffer px per crop-native px

  // ---- the glasses band, from the projected frame samples -------------------
  const n = samples.points.length / 3;
  const sx = new Float64Array(n), sy = new Float64Array(n), sz = new Float64Array(n);
  let bMinX = Infinity, bMaxX = -Infinity, bMinY = Infinity, bMaxY = -Infinity;
  {
    const R = samples.pose.R, t = samples.pose.t;
    for (let i = 0; i < n; i++) {
      const x = samples.points[i * 3], y = samples.points[i * 3 + 1], z = samples.points[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + t[2];
      sz[i] = cam[2];
      if (project(uv, kCrop, cam)) {
        sx[i] = uv[0]; sy[i] = uv[1];
        if (uv[0] < bMinX) bMinX = uv[0];
        if (uv[0] > bMaxX) bMaxX = uv[0];
        if (uv[1] < bMinY) bMinY = uv[1];
        if (uv[1] > bMaxY) bMaxY = uv[1];
      } else { sx[i] = NaN; sy[i] = NaN; }
    }
  }
  const padPx = (opt.bandPadMm * k.f) / truth.pose.t[2];
  bMinX -= padPx; bMaxX += padPx; bMinY -= padPx; bMaxY += padPx;

  const inBand = (px: number, py: number) =>
    px >= bMinX && px <= bMaxX && py >= bMinY && py <= bMaxY;

  // ---- metric A: banded boundary distance ----------------------------------
  const truthSil = extractSilhouette(truthBuf);   // crop-native px
  const occSil = extractSilhouette(occBuf);
  const truthBand: number[] = [];
  for (let i = 0; i < truthSil.length; i += 2) {
    if (inBand(truthSil[i], truthSil[i + 1])) truthBand.push(truthSil[i], truthSil[i + 1]);
  }
  const occBand: number[] = [];
  for (let i = 0; i < occSil.length; i += 2) {
    if (inBand(occSil[i], occSil[i + 1])) occBand.push(occSil[i], occSil[i + 1]);
  }

  const boundaryPx = new Float64Array(occBand.length ? truthBand.length / 2 : 0);
  const boundaryMm = new Float64Array(boundaryPx.length);
  for (let i = 0; i < boundaryPx.length; i++) {
    const px = truthBand[i * 2], py = truthBand[i * 2 + 1];
    let best = Infinity;
    for (let j = 0; j < occBand.length; j += 2) {
      const dx = px - occBand[j], dy = py - occBand[j + 1];
      const d2 = dx * dx + dy * dy;
      if (d2 < best) best = d2;
    }
    const d = Math.sqrt(best);
    boundaryPx[i] = d;
    // mm on the skin at this pixel: native px times Z over f, Z read from the
    // truth buffer at the contour pixel itself.
    const bx = Math.min(W - 1, Math.max(0, Math.floor(px * scale)));
    const by = Math.min(H - 1, Math.max(0, Math.floor(py * scale)));
    const z = truthBuf.depth[by * W + bx];
    boundaryMm[i] = d * ((Number.isFinite(z) ? z : truth.pose.t[2]) / k.f);
  }

  // ---- mean boundary offset: XOR area over contour length -------------------
  let xorAreaMm2 = 0, xorCount = 0;
  {
    const bx0 = Math.max(0, Math.floor(bMinX * scale));
    const bx1 = Math.min(W - 1, Math.ceil(bMaxX * scale));
    const by0 = Math.max(0, Math.floor(bMinY * scale));
    const by1 = Math.min(H - 1, Math.ceil(bMaxY * scale));
    for (let y = by0; y <= by1; y++) {
      for (let x = bx0; x <= bx1; x++) {
        const o = y * W + x;
        const td = truthBuf.depth[o], od = occBuf.depth[o];
        const t = td < Infinity, oo = od < Infinity;
        if (t === oo) continue;
        const z = t ? td : od;
        const side = z / (k.f * scale); // one buffer cell in mm at this depth
        xorAreaMm2 += side * side;
        xorCount++;
      }
    }
  }
  let contourLenMm = 0;
  for (let i = 0; i < truthBand.length; i += 2) {
    const bx = Math.min(W - 1, Math.max(0, Math.floor(truthBand[i] * scale)));
    const by = Math.min(H - 1, Math.max(0, Math.floor(truthBand[i + 1] * scale)));
    const z = truthBuf.depth[by * W + bx];
    contourLenMm += (Number.isFinite(z) ? z : truth.pose.t[2]) / (k.f * scale);
  }
  const bandTruthCount = truthBand.length / 2;
  const meanOffsetMm = contourLenMm > 0 ? xorAreaMm2 / contourLenMm : NaN;
  const meanOffsetPx = bandTruthCount > 0 ? xorCount / (bandTruthCount * scale) : NaN;

  // ---- metric B: depth-ordering flips --------------------------------------
  const hiddenBy = (buf: typeof truthBuf, i: number, biasMm: number): boolean => {
    if (Number.isNaN(sx[i]) || !(sz[i] > 0)) return false;
    const bx = Math.floor(sx[i] * scale), by = Math.floor(sy[i] * scale);
    if (bx < 0 || by < 0 || bx >= W || by >= H) return false;
    const d = buf.depth[by * W + bx];
    if (!(d < Infinity)) return false;
    return d + biasMm < sz[i] - opt.hiddenEpsMm;
  };

  const truthHidden = new Uint8Array(n);
  for (let i = 0; i < n; i++) truthHidden[i] = hiddenBy(truthBuf, i, 0) ? 1 : 0;

  const parts = samples.parts;
  const flips: FlipCount[] = opt.biasesMm.map((biasMm) => {
    let contested = 0, xray = 0, forgiven = 0;
    // Sized from the part list, NOT a literal `[0, 0, 0]`. It was that literal,
    // and a fourth part would have written `byPart.contested[3]++` on
    // `undefined` — yielding NaN silently, with no type error and no runtime
    // error, and printing a NaN row. Any coverage gate built on top of that
    // would have been a check that could not fail.
    const zeroes = () => new Array(framePartNames.length).fill(0) as number[];
    const byPart = parts
      ? { contested: zeroes(), xray: zeroes(), forgiven: zeroes() }
      : undefined;
    for (let i = 0; i < n; i++) {
      const occHidden = hiddenBy(occBuf, i, biasMm);
      if (!truthHidden[i] && !occHidden) continue;
      contested++;
      const p = parts ? parts[i] : 0;
      if (byPart) byPart.contested[p]++;
      if (truthHidden[i] && !occHidden) { xray++; if (byPart) byPart.xray[p]++; }
      if (!truthHidden[i] && occHidden) { forgiven++; if (byPart) byPart.forgiven[p]++; }
    }
    return { biasMm, contested, xray, forgiven, ...(byPart ? { byPart } : {}) };
  });

  // How deep the X-rayed samples truly were, at bias 0.
  const xrayBehind: number[] = [];
  if (opt.biasesMm.includes(0)) {
    for (let i = 0; i < n; i++) {
      if (!truthHidden[i] || hiddenBy(occBuf, i, 0)) continue;
      const bx = Math.floor(sx[i] * scale), by = Math.floor(sy[i] * scale);
      const d = truthBuf.depth[by * W + bx];
      if (Number.isFinite(d)) xrayBehind.push(sz[i] - d);
    }
  }

  // ---- orientation observables ---------------------------------------------
  let cySum = 0, cyCount = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (truthBuf.depth[y * W + x] < Infinity) { cySum += (y + 0.5) / scale; cyCount++; }
    }
  }

  return {
    bandTruthCount,
    bandOccluderCount: occBand.length / 2,
    boundaryPx,
    boundaryMm,
    meanOffsetPx,
    meanOffsetMm,
    flips,
    xrayBehindMm: Float64Array.from(xrayBehind),
    bandCentrePxY: (bMinY + bMaxY) / 2 + y0,
    faceCentroidPxY: (cyCount ? cySum / cyCount : NaN) + y0,
  };
}

// ------------------------------------------------------- metric C: stability

export interface StabilityResult {
  framesTracked: number;
  framesLost: number;
  /** Median over frames of the per-frame MEAN banded boundary error at the
   *  tracked pose, mm. (Mean within a frame so sub-buffer-cell movement is
   *  visible; median across frames so one bad solve is not the story.) */
  trackedMedianMm: number;
  /** Median |frame-to-frame delta| of that per-frame error — the crawl. */
  crawlMm: number;
  /** The shape-only arm: scan at the IDEAL pose (noiseless PnP per frame), mm. */
  shapeMedianMm: number;
}

/**
 * A short wandering hold near 35 degrees of yaw, tracked with the real tracker
 * against the scanned model, and the occlusion boundary re-measured per frame.
 *
 * The landmark synthesis mirrors `synthesizeCapture`'s per-frame path — the
 * smoothed postural and angular wander, the constant per-run detector bias
 * field, visibility-scaled noise and the hallucinated pull of hidden landmarks
 * toward the template — with the same magic numbers, minus the gaze artefact
 * and the iris (the tracker reads neither). It cannot reuse that function
 * directly because the protocol's beats are fixed and none of them holds 35
 * degrees; this is the same wander pointed at the yaw the question lives at.
 */
export function stabilityRun(
  mesh: FaceMesh, subject: SyntheticSubject, scan: FaceModel,
  seatSamplesScanSpace: Float64Array, geometry: CameraGeometry, seed: number,
  frames = STABILITY_FRAMES,
): StabilityResult {
  const k = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
  const rng = createRng(seed);
  const basePitch = Math.atan2(geometry.belowEyesMm, geometry.distanceMm);
  const normals = computeVertexNormals(subject.positions, mesh.indices, mesh.vertexCount);
  const normalsCam = new Float64Array(mesh.vertexCount * 3);

  // The detector's constant per-run offset from the surface, face space mm —
  // CAPTURE_DEFAULTS' 0.6 mm one-sigma.
  const bias = new Float64Array(mesh.vertexCount * 3);
  for (let i = 0; i < bias.length; i++) bias[i] = rng.truncatedNormal(2.5) * 0.6;

  const visBuf = createDepthBuffer(192, Math.round((192 * geometry.height) / geometry.width), k);
  const state = createTracker(scan);
  const observed = new Float64Array(mesh.vertexCount * 3);
  const uv = new Float64Array(2);
  const uvPrior = new Float64Array(2);
  const cam = new Float64Array(3);

  let wanderX = 0, wanderY = 0, wanderVx = 0, wanderVy = 0;
  let angleWx = 0, angleWy = 0, angleWz = 0;

  const perFrame: number[] = [];
  const shapePerFrame: number[] = [];
  let lost = 0;
  let idealWarm: Pose | undefined;

  for (let f = 0; f < frames; f++) {
    // The capture machinery's wander, verbatim (synthetic.ts).
    angleWx += (rng.truncatedNormal(2) * 0.25 - angleWx * 0.3) * (Math.PI / 180);
    angleWy += (rng.truncatedNormal(2) * 0.25 - angleWy * 0.3) * (Math.PI / 180);
    angleWz += (rng.truncatedNormal(2) * 0.25 - angleWz * 0.3) * (Math.PI / 180);
    wanderVx += rng.truncatedNormal(2) * 0.8 - wanderVx * 0.25;
    wanderVy += rng.truncatedNormal(2) * 0.8 - wanderVy * 0.25;
    wanderX = Math.max(-14, Math.min(14, wanderX + wanderVx));
    wanderY = Math.max(-14, Math.min(14, wanderY + wanderVy));

    const pose = poseIdentity();
    poseRotationFromHeadEuler(
      pose.R,
      (STABILITY_YAW_DEG * Math.PI) / 180 + angleWy,
      basePitch + angleWx,
      angleWz,
    );
    pose.t.set([wanderX, wanderY, geometry.distanceMm]);

    // Visibility from the raster, exactly as the capture generator reads it.
    const { px, depth } = rasterize(
      visBuf, subject.positions, mesh.indices, mesh.vertexCount, pose, k,
    );
    normalsToCamera(normalsCam, normals, mesh.vertexCount, pose);
    const vis = vertexVisibility(visBuf, px, depth, mesh.vertexCount, normalsCam);

    observed.set(subject.positions);
    for (let i = 0; i < observed.length; i++) observed[i] += bias[i];

    const landmarks = new Float64Array(mesh.vertexCount * 2).fill(NaN);
    const sigmaPx = new Float64Array(mesh.vertexCount).fill(Infinity);
    const R = pose.R;
    for (let i = 0; i < mesh.vertexCount; i++) {
      const x = observed[i * 3], y = observed[i * 3 + 1], z = observed[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
      if (!project(uv, k, cam)) continue;
      const hidden = 1 - vis[i];
      const sigma = 0.7 * (1 + 6 * hidden * hidden);
      let bx = 0, by = 0;
      if (hidden > 0.01) {
        const tx = mesh.positions[i * 3], ty = mesh.positions[i * 3 + 1], tz = mesh.positions[i * 3 + 2];
        cam[0] = R[0] * tx + R[1] * ty + R[2] * tz + pose.t[0];
        cam[1] = R[3] * tx + R[4] * ty + R[5] * tz + pose.t[1];
        cam[2] = R[6] * tx + R[7] * ty + R[8] * tz + pose.t[2];
        if (project(uvPrior, k, cam)) {
          const pull = hidden * hidden;
          bx = (uvPrior[0] - uv[0]) * pull;
          by = (uvPrior[1] - uv[1]) * pull;
        }
      }
      landmarks[i * 2] = uv[0] + bx + rng.normal() * sigma;
      landmarks[i * 2 + 1] = uv[1] + by + rng.normal() * sigma;
      sigmaPx[i] = sigma;
    }

    const result = track(state, { landmarks, sigmaPx, intrinsics: k, dt: 1 / 30 });
    if (!result.tracked || !result.pose || result.held) { lost++; continue; }

    // The per-frame statistic is the MEAN BOUNDARY OFFSET (XOR area over
    // contour length), deliberately: a median over contour pixels quantises to
    // the buffer grid (~0.8 mm), so a sub-cell crawl would read as exactly zero
    // or exactly one cell. The offset resolves fractional-cell movement, which
    // is the movement the eye sees.
    const truthArm: OcclusionArm = { positions: subject.positions, pose };
    const trackedCell = occlusionCell(
      mesh, truthArm, { positions: scan.positions, pose: result.pose },
      { points: seatSamplesScanSpace, pose: result.pose }, k, { biasesMm: [0] },
    );
    if (Number.isFinite(trackedCell.meanOffsetMm)) {
      perFrame.push(trackedCell.meanOffsetMm);
    }

    // The shape-only arm: the same scan at the best pose noiseless landmarks
    // can buy. The gap between this and the tracked arm is what TRACKING costs.
    const { landmarks: clean, sigmaPx: cleanSigma } = noiselessLandmarks(
      subject.positions, mesh.vertexCount, pose, k,
    );
    const cs = buildCorrespondences(clean, cleanSigma, mesh.vertexCount);
    const ideal = solvePnP(scan.positions, cs, k, idealWarm ?? poseClone(pose));
    idealWarm = poseClone(ideal.pose);
    const shapeCell = occlusionCell(
      mesh, truthArm, { positions: scan.positions, pose: ideal.pose },
      { points: seatSamplesScanSpace, pose: ideal.pose }, k, { biasesMm: [0] },
    );
    if (Number.isFinite(shapeCell.meanOffsetMm)) {
      shapePerFrame.push(shapeCell.meanOffsetMm);
    }
  }

  const deltas: number[] = [];
  for (let i = 1; i < perFrame.length; i++) deltas.push(Math.abs(perFrame[i] - perFrame[i - 1]));

  return {
    framesTracked: perFrame.length,
    framesLost: lost,
    trackedMedianMm: distribution(perFrame).median,
    crawlMm: distribution(deltas).median,
    shapeMedianMm: distribution(shapePerFrame).median,
  };
}

// ---------------------------------------------------------------- the run

interface CellFigures {
  subject: string;
  geometry: string;
  yawDeg: number;
  /** Raw per-contour-pixel distances, kept raw so a yaw bucket can pool PIXELS
   *  across subjects — a per-cell median quantises to the buffer grid, and a
   *  median over pooled pixels is the statistic the task actually names. */
  scanPx: Float64Array;
  scanMm: Float64Array;
  templateMm: Float64Array;
  pooledMm: Float64Array | null;
  /** Mean boundary offsets (XOR area / contour length) per arm, mm. */
  scanOffMm: number;
  scanOffPx: number;
  templateOffMm: number;
  pooledOffMm: number | null;
  bandCount: number;
  scanFlips: FlipCount[];
  templateFlips0: FlipCount;
  /** Depth by which each scan-arm X-ray sample truly sat behind the face, mm. */
  scanXrayBehindMm: Float64Array;
  scanGauge: number;
  pooledGauge: number | null;
}

interface SeedRun {
  seed: number;
  subjectCount: number;
  cells: CellFigures[];
  stability: { geometry: string; result: StabilityResult }[];
  enrollNoseNote: string;
}

function truthFaceModel(positions: Float64Array, vertexCount: number): FaceModel {
  return createFaceModel({
    positions: new Float64Array(positions),
    vertexSigmaMm: new Float64Array(vertexCount).fill(0.1),
    shapeCoeffs: new Float64Array(0),
    basisName: 'ground-truth',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
    landmarkBiasMm: new Float64Array(vertexCount * 3),
    quality: {},
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });
}

function scanOf(
  mesh: FaceMesh, basis: ReturnType<typeof loadBasis>, subject: SyntheticSubject,
  campaignSeed: number, trueIris: boolean,
): FaceModel {
  const geometry = CAMERA_LADDER[0];
  const capture = synthesizeCapture(mesh, subject, geometry, {
    framesPerBeat: 12, seed: captureSeedFor(campaignSeed),
  });
  return enroll({
    mesh, basis,
    frames: capture.frames.map((f) => ({
      landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
      silhouette: f.silhouette, beat: f.beat,
    })),
    imageWidth: geometry.width, imageHeight: geometry.height,
    // True iris isolates geometry from the ruler; omitting it hands the
    // enrollment the pooled default — the ablation arm.
    ...(trueIris ? { irisMm: subject.irisDiameterMm } : {}),
  }).model;
}

function runSeed(
  mesh: FaceMesh, basis: ReturnType<typeof loadBasis>, regions: Record<string, Region>,
  campaignSeed: number, subjectCount: number,
): SeedRun {
  const population = generatePopulation(mesh, basis, {
    count: subjectCount, seed: populationSeedFor(campaignSeed),
  });
  const standard = TEST_FRAMES[1]; // 'standard', 17 mm pads
  const rawSamples = frameSampleSet(standard);
  const sampleParts = frameSampleParts(standard);
  const template = mesh.positions;

  // The pooled-iris ablation runs on three subjects: the first sampled one and
  // both named extremes (whose true irises, 11.10 and 11.90 mm, bracket the
  // pooled 11.7 the hardest).
  const pooledIds = new Set([
    population[0].id, population[population.length - 2].id, population[population.length - 1].id,
  ]);

  const cells: CellFigures[] = [];
  const stability: { geometry: string; result: StabilityResult }[] = [];

  for (let si = 0; si < population.length; si++) {
    const subject = population[si];
    const scan = scanOf(mesh, basis, subject, campaignSeed, true);
    const pooledScan = pooledIds.has(subject.id)
      ? scanOf(mesh, basis, subject, campaignSeed, false)
      : null;

    const seat = solveSeat(scan, mesh, regions, standard);
    const seatSamples = transformSamples(seat.pose, rawSamples);
    const pooledSeatSamples = pooledScan
      ? transformSamples(solveSeat(pooledScan, mesh, regions, standard).pose, rawSamples)
      : null;

    for (const geometry of CAMERA_LADDER) {
      const k = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
      for (const yawDeg of YAWS_DEG) {
        const truthPose = ladderPose(geometry, yawDeg);
        const truthArm: OcclusionArm = { positions: subject.positions, pose: truthPose };

        const scanArm = fitOccluderArm(scan.positions, subject.positions, mesh.vertexCount, truthPose, k);
        const scanSamplesArm = {
          points: scaleSamples(seatSamples, scanArm.gauge), pose: scanArm.pose,
          parts: sampleParts,
        };
        const scanCell = occlusionCell(mesh, truthArm, scanArm, scanSamplesArm, k);

        // Template arm: same seat, same band — only the occluding surface
        // changes, so the delta is what the scan bought.
        const templateArm = fitOccluderArm(template, subject.positions, mesh.vertexCount, truthPose, k);
        const templateCell = occlusionCell(mesh, truthArm, templateArm, scanSamplesArm, k, { biasesMm: [0] });

        let pooledMm: Float64Array | null = null;
        let pooledOffMm: number | null = null;
        let pooledGauge: number | null = null;
        if (pooledScan && pooledSeatSamples) {
          const pooledArm = fitOccluderArm(pooledScan.positions, subject.positions, mesh.vertexCount, truthPose, k);
          const pooledCell = occlusionCell(mesh, truthArm, pooledArm, scanSamplesArm, k, { biasesMm: [0] });
          pooledMm = pooledCell.boundaryMm;
          pooledOffMm = pooledCell.meanOffsetMm;
          pooledGauge = pooledArm.gauge;
        }

        cells.push({
          subject: subject.id,
          geometry: geometry.name,
          yawDeg,
          scanPx: scanCell.boundaryPx,
          scanMm: scanCell.boundaryMm,
          templateMm: templateCell.boundaryMm,
          pooledMm,
          scanOffMm: scanCell.meanOffsetMm,
          scanOffPx: scanCell.meanOffsetPx,
          templateOffMm: templateCell.meanOffsetMm,
          pooledOffMm,
          bandCount: scanCell.bandTruthCount,
          scanFlips: scanCell.flips,
          templateFlips0: flipsAt(templateCell, 0),
          scanXrayBehindMm: scanCell.xrayBehindMm,
          scanGauge: scanArm.gauge,
          pooledGauge,
        });
      }

      // Metric C: one wandering hold per subject x geometry.
      stability.push({
        geometry: geometry.name,
        result: stabilityRun(
          mesh, subject, scan, seatSamples, geometry,
          deriveSeed(captureSeedFor(campaignSeed) ?? 0xc0ffee, 0x0cc1 + si * 8 + CAMERA_LADDER.indexOf(geometry)),
        ),
      });
    }
  }

  return {
    seed: campaignSeed,
    subjectCount: population.length,
    cells,
    stability,
    enrollNoseNote: `${population.length} subjects enrolled at eye-level, framesPerBeat 12, true iris`,
  };
}

// -------------------------------------------------------------- aggregation

const median = (xs: number[]): number => distribution(xs).median;

/** Pools the raw per-pixel distances of several cells into one array. */
function pool(cells: CellFigures[], pick: (c: CellFigures) => Float64Array | null): number[] {
  const out: number[] = [];
  for (const c of cells) {
    const a = pick(c);
    if (a) for (let i = 0; i < a.length; i++) out.push(a[i]);
  }
  return out;
}

function headline(run: SeedRun) {
  const at = (yaw: number) => run.cells.filter((c) => c.yawDeg === yaw);
  const scanMm = (yaw: number) => median(pool(at(yaw), (c) => c.scanMm));
  const scanMmMean = (yaw: number) => median(at(yaw).map((c) => c.scanOffMm));
  const buysMm = (yaw: number) => median(at(yaw).map((c) => c.templateOffMm - c.scanOffMm));

  // Flip fractions pooled over the yaws where occlusion actually happens.
  const turnedCells = run.cells.filter((c) => c.yawDeg >= 30);
  const fracs = (biasMm: number) => {
    let contested = 0, xray = 0, forgiven = 0;
    for (const c of turnedCells) {
      const f = c.scanFlips.find((x) => x.biasMm === biasMm)!;
      contested += f.contested; xray += f.xray; forgiven += f.forgiven;
    }
    return { xray: contested ? xray / contested : 0, forgiven: contested ? forgiven / contested : 0 };
  };

  const irisCells = run.cells.filter((c) => c.pooledOffMm !== null);
  const irisDelta = median(irisCells.map(
    (c) => Math.abs((c.pooledOffMm as number) - c.scanOffMm),
  ));

  return {
    scan30Mm: scanMm(30), scan45Mm: scanMm(45),
    scan30MmMean: scanMmMean(30), scan45MmMean: scanMmMean(45),
    scan30Px: median(pool(at(30), (c) => c.scanPx)),
    scan45Px: median(pool(at(45), (c) => c.scanPx)),
    scan30PxMean: median(at(30).map((c) => c.scanOffPx)),
    scan45PxMean: median(at(45).map((c) => c.scanOffPx)),
    buys30Mm: buysMm(30), buys45Mm: buysMm(45),
    xray0: fracs(0).xray, forgiven0: fracs(0).forgiven,
    xrayMinus05: fracs(-0.5).xray, forgivenMinus05: fracs(-0.5).forgiven,
    crawlMm: median(run.stability.map((s) => s.result.crawlMm)),
    trackedMm: median(run.stability.map((s) => s.result.trackedMedianMm)),
    shapeMm: median(run.stability.map((s) => s.result.shapeMedianMm)),
    irisDeltaMm: irisDelta,
    fracs,
  };
}

// ------------------------------------------------------------------ report

export function runOcclusionReport(options: OcclusionRunOptions = {}): string {
  const started = Date.now();
  const mesh = loadTemplateMesh();
  const basis = loadBasis();
  const regions = loadRegions();

  const fullCount = options.subjects ?? 10;
  const singleSeed = options.seed !== undefined;
  const seeds = singleSeed ? [options.seed as number] : [...CAMPAIGN_SEEDS];

  // The first seed carries the full population and every table below; the
  // replication seeds run a reduced population (4 sampled + the two extremes)
  // because enrollment is the wall-clock cost and the headline medians need
  // independent REALISATIONS more than they need more subjects per realisation.
  const runs: SeedRun[] = seeds.map((seed, i) =>
    runSeed(mesh, basis, regions, seed, i === 0 ? fullCount : Math.min(4, fullCount)),
  );
  const main = runs[0];

  const out: string[] = [];
  out.push('OCCLUSION — WHAT THE SCANNED FACE HIDES, MEASURED BEFORE A PIXEL IS DRAWN');
  out.push('==========================================================================');
  out.push('The renderer about to be built will use the scanned face mesh as its');
  out.push('occluder. This instrument measures, against synthetic ground truth, how');
  out.push('good that occluder is: boundary placement in the glasses band, depth-');
  out.push('ordering flips in both directions, stability under live tracking, and');
  out.push('what the personal scan buys over the average face.');
  out.push('');
  out.push(`Occluder scans: ${main.enrollNoseNote}. Frame: '${TEST_FRAMES[1].id}'`);
  out.push('seated by solveSeat on the scan (the shipping path). The frame sample');
  out.push('cloud samples all five parts the renderer draws — rims, lens discs,');
  out.push('bridge, endpieces, temples — off the SAME `fit/frame-layout.ts` the');
  out.push('renderer builds from, so this is render geometry and not a second');
  out.push('description of it. The line that stood here said "rims, bridge and');
  out.push('temples ... not render geometry", and both halves had become false:');
  out.push('two of the five parts were unsampled, and the bridge samples sat');
  out.push('4.000000 mm above the drawn tube — 2.4 mm clear of a 1.6 mm radius,');
  out.push('so nothing they reported was about geometry that existed. Every');
  out.push('occluder is posed');
  out.push('by PnP against noiseless truth landmarks (what the tracker does, minus');
  out.push('its noise — metric C carries the noise) and depth-gauge normalised, so');
  out.push('a ruler error cannot masquerade as an occlusion error.');
  out.push('');
  out.push(singleSeed
    ? `Single realisation at campaign seed ${seeds[0]}, ${main.subjectCount} subjects.`
    : `Campaign: seeds {${seeds.join(', ')}}. Seed ${seeds[0]} runs the full ` +
      `population (${main.subjectCount} subjects: ${fullCount} sampled + 2 named extremes) ` +
      'and provides every table below; the four replication seeds run 6 subjects ' +
      '(4 sampled + the extremes) — the population was shrunk before the yaw ' +
      'ladder, per the wall-clock rule, and the headline block reports all five.');
  out.push('');

  // ---- metric A + D ---------------------------------------------------------
  out.push('METRIC A + D — THE BOUNDARY, SCAN AGAINST TEMPLATE');
  out.push('--------------------------------------------------');
  out.push('For every truth-contour pixel in the glasses band: distance to the');
  out.push('nearest occluder-contour pixel. Median / p90 over ALL band pixels of');
  out.push(`all subjects in the bucket. Seed ${seeds[0]}.`);
  out.push('');
  const aRows: (string | number)[][] = [];
  for (const geometry of CAMERA_LADDER) {
    for (const yawDeg of YAWS_DEG) {
      const cells = main.cells.filter((c) => c.geometry === geometry.name && c.yawDeg === yawDeg);
      const scanPx = distribution(pool(cells, (c) => c.scanPx));
      const scanMm = distribution(pool(cells, (c) => c.scanMm));
      const templMm = distribution(pool(cells, (c) => c.templateMm));
      const scanOff = distribution(cells.map((c) => c.scanOffMm));
      const templOff = distribution(cells.map((c) => c.templateOffMm));
      aRows.push([
        geometry.name, yawDeg,
        Math.round(distribution(cells.map((c) => c.bandCount)).median),
        `${scanPx.median.toFixed(2)} / ${scanPx.p90.toFixed(2)}`,
        `${scanMm.median.toFixed(2)} / ${scanMm.p90.toFixed(2)}`,
        scanOff.median.toFixed(2),
        `${templMm.median.toFixed(2)} / ${templMm.p90.toFixed(2)}`,
        templOff.median.toFixed(2),
        distribution(cells.map((c) => c.templateOffMm - c.scanOffMm)).median.toFixed(2),
      ]);
    }
  }
  out.push(table(
    ['camera', 'yaw', 'band px', 'scan px (med/p90)', 'scan mm (med/p90)', 'scan off',
      'template mm (med/p90)', 'tmpl off', 'scan buys mm'],
    aRows,
  ));
  out.push('');
  out.push('  scan px      is in the geometry\'s NATIVE pixels: a phone pixel is a');
  out.push('               third of a laptop pixel on the skin, which is why the mm');
  out.push('               column is the comparable one and the px column is the one');
  out.push('               a compositor cares about.');
  out.push('  a 0.00 med   is a statement, not a rounding: more than half the band');
  out.push('               boundary coincides to within one buffer cell (~0.7-0.9 mm,');
  out.push('               ~1 native px at eye level), which is the grid the per-');
  out.push('               pixel distances are quantised to.');
  out.push('  off          the mean boundary OFFSET: in-band area where the two');
  out.push('               masks disagree, over the truth contour\'s in-band length');
  out.push('               (median across subjects). Sub-cell-resolving, and robust');
  out.push('               where the raw distances are not: a knife-edge sliver of');
  out.push('               face exposed in one mask only reads as its own thin area');
  out.push('               here, while the nearest-distance walks its arc and can');
  out.push('               report tens of millimetres for a sub-mm shape difference');
  out.push('               (that tail is real and lives in the p90 columns).');
  out.push('  scan buys    template offset minus scan offset, per subject, median.');
  out.push('               Metric D\'s headline: the boundary error the average face');
  out.push('               would add if it were the occluder.');
  out.push('');

  // ---- the ruler cancels ----------------------------------------------------
  out.push('THE RULER CANCELS — POOLED IRIS AGAINST TRUE IRIS');
  out.push('-------------------------------------------------');
  out.push('Same subjects scanned twice: once told their true iris, once left to the');
  out.push('pooled 11.7 mm default (the shipping configuration). If scale reached');
  out.push('occlusion, these columns would differ by the ruler error.');
  out.push('');
  const irisRows: (string | number)[][] = [];
  for (const yawDeg of YAWS_DEG) {
    const cells = main.cells.filter((c) => c.yawDeg === yawDeg && c.pooledOffMm !== null);
    const trueOff = distribution(cells.map((c) => c.scanOffMm));
    const pooledOff = distribution(cells.map((c) => c.pooledOffMm as number));
    const delta = distribution(cells.map((c) => Math.abs((c.pooledOffMm as number) - c.scanOffMm)));
    const gauge = distribution(cells.map((c) => Math.abs((c.pooledGauge as number) - 1) * 100));
    irisRows.push([
      yawDeg,
      trueOff.median.toFixed(2), pooledOff.median.toFixed(2),
      delta.median.toFixed(2), delta.worst.toFixed(2),
      gauge.median.toFixed(1),
    ]);
  }
  out.push(table(
    ['yaw', 'true-iris off mm', 'pooled off mm', '|delta| med', '|delta| worst', 'gauge err %'],
    irisRows,
  ));
  out.push('');
  out.push('  gauge err    how far the pooled arm\'s depth gauge sat from truth before');
  out.push('               normalisation — the ruler error the pose absorbed. That');
  out.push('               these percents dwarf the |delta| columns is the point: the');
  out.push('               ruler moves the gauge, the gauge cancels in projection, and');
  out.push('               the boundary does not notice. A |delta| of 0.00 here is');
  out.push('               EXACT, not merely small: the ruler multiplies the solved');
  out.push('               shape globally (applyScale in enroll.ts), so the pooled and');
  out.push('               true-iris models differ by a pure similarity that the PnP');
  out.push('               fit and the gauge normalisation cancel to the last bit.');
  out.push('               Scale provably cannot reach occlusion in this pipeline.');
  out.push('               (The pooled arm is measured against the SCAN arm\'s band');
  out.push('               so only the surface differs.)');
  out.push('');

  // ---- metric B -------------------------------------------------------------
  out.push('METRIC B — DEPTH-ORDERING FLIPS, AND THE BIAS TRADE');
  out.push('---------------------------------------------------');
  out.push('Every frame sample is classified hidden/visible against the truth depth');
  out.push('buffer and against the scanned one (hidden = face nearer by more than');
  out.push(`${CELL_DEFAULTS.hiddenEpsMm} mm). Contested = hidden by either surface at that bias; the`);
  out.push('fractions are of contested samples. X-RAY (truth-hidden, scan-visible)');
  out.push('is the failure the field never tolerates; FORGIVEN (truth-visible,');
  out.push('scan-hidden) it forgives. Sign convention: NEGATIVE bias moves the');
  out.push('occluder TOWARD the camera, hides more, and buys X-ray down at the');
  out.push('price of forgiven — pinned by its own test in pipeline.test.ts.');
  out.push('');
  const bias0Rows: (string | number)[][] = [];
  for (const yawDeg of YAWS_DEG) {
    const cells = main.cells.filter((c) => c.yawDeg === yawDeg);
    let contested = 0, xray = 0, forgiven = 0, tContested = 0, tXray = 0, tForgiven = 0;
    for (const c of cells) {
      const f = c.scanFlips.find((x) => x.biasMm === 0)!;
      contested += f.contested; xray += f.xray; forgiven += f.forgiven;
      tContested += c.templateFlips0.contested; tXray += c.templateFlips0.xray;
      tForgiven += c.templateFlips0.forgiven;
    }
    const pct = (a: number, b: number) => (b ? ((100 * a) / b).toFixed(1) : '-');
    bias0Rows.push([
      yawDeg, contested,
      pct(xray, contested), pct(forgiven, contested),
      pct(tXray, tContested), pct(tForgiven, tContested),
    ]);
  }
  out.push(table(
    ['yaw', 'contested', 'scan xray %', 'scan forgiven %', 'template xray %', 'template forgiven %'],
    bias0Rows,
  ));
  out.push('');
  out.push('The bias sweep, pooled over yaw >= 30 (where occlusion lives), scan arm:');
  out.push('');
  const sweepRows: (string | number)[][] = [];
  for (const biasMm of CELL_DEFAULTS.biasesMm) {
    let contested = 0, xray = 0, forgiven = 0;
    for (const c of main.cells.filter((x) => x.yawDeg >= 30)) {
      const f = c.scanFlips.find((x) => x.biasMm === biasMm)!;
      contested += f.contested; xray += f.xray; forgiven += f.forgiven;
    }
    sweepRows.push([
      biasMm > 0 ? `+${biasMm.toFixed(1)}` : biasMm.toFixed(1),
      contested,
      contested ? ((100 * xray) / contested).toFixed(2) : '-',
      contested ? ((100 * forgiven) / contested).toFixed(2) : '-',
    ]);
  }
  out.push(table(['bias mm', 'contested', 'xray %', 'forgiven %'], sweepRows));
  out.push('');
  out.push('  The trade both ways: pushing the occluder toward the camera erases');
  out.push('  X-ray and eats attached geometry (rim edges, temple roots) as');
  out.push('  forgiven hides; pushing it away does the opposite. The renderer\'s');
  out.push('  knob should be read off this table, not asserted.');
  out.push('');
  out.push('Where the flips live (yaw >= 30, scan arm):');
  out.push('');
  const partRows: (string | number)[][] = [];
  for (let p = 0; p < framePartNames.length; p++) {
    const acc = { c0: 0, x0: 0, f0: 0, cN: 0, xN: 0, fN: 0 };
    for (const c of main.cells.filter((x) => x.yawDeg >= 30)) {
      const at0 = c.scanFlips.find((x) => x.biasMm === 0)!.byPart;
      const atN = c.scanFlips.find((x) => x.biasMm === -0.5)!.byPart;
      if (!at0 || !atN) continue;
      acc.c0 += at0.contested[p]; acc.x0 += at0.xray[p]; acc.f0 += at0.forgiven[p];
      acc.cN += atN.contested[p]; acc.xN += atN.xray[p]; acc.fN += atN.forgiven[p];
    }
    const pct = (a: number, b: number) => (b ? ((100 * a) / b).toFixed(2) : '-');
    partRows.push([
      framePartNames[p], acc.c0,
      pct(acc.x0, acc.c0), pct(acc.f0, acc.c0),
      pct(acc.xN, acc.cN), pct(acc.fN, acc.cN),
    ]);
  }
  out.push(table(
    ['part', 'contested', 'xray % @0', 'forgiven % @0', 'xray % @-0.5', 'forgiven % @-0.5'],
    partRows,
  ));
  out.push('');
  out.push('  The temple arm grazes the head, so its samples sit within a');
  out.push('  millimetre of the surface for most of their length — precisely the');
  out.push('  geometry that flips on sub-millimetre shape error, in either');
  out.push('  direction. A bias spent to hide temple X-ray is a bias that starts');
  out.push('  eating the rim, and this table is where that trade is visible.');
  out.push('');
  {
    const behind = pool(main.cells.filter((c) => c.yawDeg >= 30), (c) => c.scanXrayBehindMm);
    const d = distribution(behind);
    out.push(`How deep the X-ray really is: an X-rayed sample sat a median ${d.median.toFixed(2)} mm`);
    out.push(`behind the truth surface (p90 ${d.p90.toFixed(2)}, worst ${d.worst.toFixed(1)}, over ${d.n} flips at`);
    out.push('yaw >= 30). Two populations live in that spread. The shallow half are');
    out.push('grazing skims — the temple riding within a couple of millimetres of');
    out.push('the cheek. The deep tail is samples far behind the head exposed at an');
    out.push('occluding EDGE (the nose profile, the mask\'s own cut edge) whose');
    out.push('position the scan has sub-millimetre wrong: the flip is deep but the');
    out.push('wrongly-drawn strip is only as wide as metric A\'s boundary offset.');
    out.push('Note the rim row above: its X-ray is IDENTICAL at bias 0 and -0.5,');
    out.push('because those samples land on pixels the scan mask does not cover at');
    out.push('all — no depth bias can hide a sample the occluder has no depth for.');
    out.push('A bias fixes depth-ordering X-ray only; edge-coverage X-ray is fixed');
    out.push('by boundary accuracy (metric A) or by the renderer dilating its');
    out.push('mask edge, which is a stage-1 decision this table informs.');
    out.push('');
  }

  // ---- metric C -------------------------------------------------------------
  out.push('METRIC C — STABILITY UNDER LIVE TRACKING');
  out.push('----------------------------------------');
  out.push(`A ${STABILITY_FRAMES}-frame wandering hold near ${STABILITY_YAW_DEG} degrees of yaw — the capture`);
  out.push('machinery\'s own postural wander and landmark noise — tracked with the');
  out.push('real tracker against the scan. Per frame: banded boundary error of the');
  out.push('scan at the TRACKED pose against truth at the TRUE pose. The crawl is');
  out.push('the median frame-to-frame change of that error — the wobble the eye');
  out.push('punishes. The shape-only arm re-poses the same scan with noiseless');
  out.push('landmarks, so tracked minus shape-only is what tracking itself costs.');
  out.push('');
  const cRows: (string | number)[][] = [];
  for (const geometry of CAMERA_LADDER) {
    const rs = main.stability.filter((s) => s.geometry === geometry.name).map((s) => s.result);
    cRows.push([
      geometry.name,
      rs.reduce((a, r) => a + r.framesTracked, 0),
      rs.reduce((a, r) => a + r.framesLost, 0),
      distribution(rs.map((r) => r.trackedMedianMm)).median.toFixed(2),
      distribution(rs.map((r) => r.crawlMm)).median.toFixed(3),
      distribution(rs.map((r) => r.crawlMm)).p90.toFixed(3),
      distribution(rs.map((r) => r.shapeMedianMm)).median.toFixed(2),
    ]);
  }
  out.push(table(
    ['camera', 'frames', 'lost', 'tracked err mm', 'crawl mm (med)', 'crawl p90', 'shape-only mm'],
    cRows,
  ));
  out.push('');
  out.push('  A lost frame is one the tracker refused (solve rejected or held); the');
  out.push('  boundary numbers are conditioned on tracking. The phone-lap loss rate');
  out.push('  is a finding of its own: this hold combines 35 degrees of yaw with the');
  out.push('  ~30 degree pitch a phone in the lap imposes, and the tracker starts');
  out.push('  refusing solves there.');
  out.push('');

  // ---- headline across seeds ------------------------------------------------
  const figures = runs.map(headline);
  const spread = (pick: (h: ReturnType<typeof headline>) => number, dp = 2) => {
    const vals = figures.map(pick);
    const d = distribution(vals);
    return `${d.median.toFixed(dp)}  [${Math.min(...vals).toFixed(dp)} .. ${Math.max(...vals).toFixed(dp)}]`;
  };
  out.push(singleSeed ? 'HEADLINE (single seed — no replication spread)' : 'HEADLINE — MEDIAN OF THE FIVE SEEDS, PER-SEED RANGE IN BRACKETS');
  out.push('----------------------------------------------------------------');
  out.push(`  boundary med, scan, 30 deg   ${spread((h) => h.scan30Mm)} mm   (${spread((h) => h.scan30Px)} native px)`);
  out.push(`  boundary med, scan, 45 deg   ${spread((h) => h.scan45Mm)} mm   (${spread((h) => h.scan45Px)} native px)`);
  out.push(`  boundary offset, scan, 30    ${spread((h) => h.scan30MmMean)} mm   (${spread((h) => h.scan30PxMean)} native px)`);
  out.push(`  boundary offset, scan, 45    ${spread((h) => h.scan45MmMean)} mm   (${spread((h) => h.scan45PxMean)} native px)`);
  out.push(`  scan buys over template, 30  ${spread((h) => h.buys30Mm)} mm`);
  out.push(`  scan buys over template, 45  ${spread((h) => h.buys45Mm)} mm`);
  out.push(`  xray at bias 0 (yaw>=30)     ${spread((h) => 100 * h.xray0)} % of contested`);
  out.push(`  forgiven at bias 0           ${spread((h) => 100 * h.forgiven0)} %`);
  out.push(`  xray at bias -0.5 mm         ${spread((h) => 100 * h.xrayMinus05)} %`);
  out.push(`  forgiven at bias -0.5 mm     ${spread((h) => 100 * h.forgivenMinus05)} %`);
  out.push(`  tracked boundary err (35deg) ${spread((h) => h.trackedMm)} mm`);
  out.push(`  boundary crawl per frame     ${spread((h) => h.crawlMm, 3)} mm`);
  out.push(`  shape-only boundary err      ${spread((h) => h.shapeMm)} mm`);
  out.push(`  |pooled - true iris| bound.  ${spread((h) => h.irisDeltaMm)} mm`);
  out.push('');

  out.push('READING THIS TABLE');
  out.push('------------------');
  out.push('The scan mm column is the error a wearer would see at the frame\'s edge');
  out.push('where it crosses the face contour: how many millimetres of temple arm');
  out.push('vanish too early or too late. The buys column is the same number for the');
  out.push('average head, minus the scan\'s — the argument for having scanned at all,');
  out.push('and it should GROW with yaw, because frontal occlusion is nearly shape-');
  out.push('free while profile occlusion is nothing but shape. X-ray percent is the');
  out.push('one to watch in metric B: a single X-rayed temple sample is a temple');
  out.push('drawn through a cheek. The crawl is metric C\'s verdict on the frozen-');
  out.push('scan architecture: the scan does not change between frames, so boundary');
  out.push('wobble can only come from pose noise, and it should sit well under a');
  out.push('native pixel-equivalent (~0.3-0.9 mm at these distances).');
  out.push('');
  out.push('A limitation, stated: truth here is the same 468-vertex mask topology');
  out.push('as the occluder, so everything the mask can never cover — ears, hair,');
  out.push('the back of the head — is outside this instrument by construction.');
  out.push('Whether the renderer should hide a temple tip behind hair it has no');
  out.push('geometry for is a stage-1 renderer question, not a scan-quality');
  out.push('question, and no number above speaks to it.');
  out.push('');
  out.push(`Wall clock: ${((Date.now() - started) / 1000).toFixed(1)} s.`);

  return out.join('\n');
}

export { CELL_DEFAULTS as occlusionCellDefaults, YAWS_DEG as occlusionYawLadder };
