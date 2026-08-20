/**
 * Synthetic subjects and synthetic captures — the ground truth this tree is
 * measured against until real scans exist.
 *
 * ## Why this file is the most important one in `testkit/`
 *
 * The v1 audit's finding was not that its constants were wrong. It was that six
 * in seven of them were **one person's number**, so no measurement in the tree
 * could distinguish "this works" from "this works on Shay". Every accuracy claim
 * in v2 is a distribution over the population generated here, per region, in
 * millimetres, and the harness reports the tail rather than the mean — because a
 * try-on that is excellent on average and unusable for one wearer in twenty is a
 * try-on that is unusable.
 *
 * ## The rule that makes these tests able to fail
 *
 * v1 shipped a check that "the pads stay on the nose" which measured a residual
 * that was identically zero by construction: the harness was re-deriving the
 * solver's own algebra and comparing it with itself. The lesson — *a number that
 * can only be zero is not a measurement until you have shown it can be something
 * else* — is enforced here structurally:
 *
 *   **A synthetic subject's nose carries detail the shape basis provably cannot
 *   represent.**
 *
 * `noseDetail` is a smooth random field drawn independently of the basis, and
 * `subjectResidualAgainstBasis()` reports how much of it the basis can explain.
 * If a future basis change made that residual zero, the enrollment tests would
 * be measuring nothing, and the harness asserts it is not.
 *
 * ## The camera ladder
 *
 * Three geometries, and the second one is not decoration. v1 shipped a seat that
 * scored **0 of 600 admitted frames** for a laptop-lid camera 13.5 degrees below
 * the eyes — a whole class of hardware for which the feature silently did not
 * exist — because a "square on" test was a cone about the camera axis and a
 * camera is not anybody's eye level. Every accuracy number in this tree is
 * reported across all three geometries or it is not reported.
 */

import { evaluateBasis, type ShapeBasis } from '../core/shape/basis.js';
import { basisExplains } from '../core/shape/anthropometric.js';
import {
  computeVertexNormals, LM, measure, standardRegions,
  type FaceMeasurements, type FaceMesh,
} from '../core/mesh.js';
import {
  type Intrinsics, intrinsicsFromFov, project,
} from '../core/camera.js';
import {
  type Pose, m3, mat3FromEulerYXZ, poseIdentity, v3,
} from '../core/linalg.js';
import {
  createDepthBuffer, normalsToCamera, rasterize, vertexVisibility,
} from '../core/raster.js';
import { createRng, type Rng } from './random.js';
import { DETECTOR_LANDMARK_COUNT, FIRST_IRIS_INDEX } from '../core/mesh.js';

// ------------------------------------------------------------------ subjects

export interface SyntheticSubject {
  id: string;
  /** Ground-truth geometry, mm. */
  positions: Float64Array;
  /** The part of the truth that lives in the basis. */
  shapeCoeffs: Float64Array;
  /** The part that does not: per-vertex displacement along the normal, mm. */
  noseDetail: Float64Array;
  /**
   * This subject's true horizontal visible iris diameter, mm.
   *
   * Varied on purpose, and this is the single most consequential line in the
   * file. Published HVID means differ by population — roughly 11.10 mm
   * (Japanese), 11.26 mm (Chinese), 11.75 mm (white adults) — with a
   * within-group SD near 0.45 mm. v1 hardcodes 11.7 mm, which is a white-adult
   * mean, so on an East Asian wearer its ruler is about 5% long and every
   * millimetre it reports inherits that bias. Generating subjects with a real
   * spread is what turns that from an argument into a measurable error bar.
   */
  irisDiameterMm: number;
  measurements: FaceMeasurements;
  /** Free text for report tables: "broad nose, low bridge". */
  description: string;
}

export interface PopulationOptions {
  count: number;
  seed: number;
  /** Peak amplitude of the non-basis nose detail, mm. */
  noseDetailMm: number;
  /** Shape coefficients are drawn from N(0,1) truncated here. 2.5 covers the
   *  published human range without generating faces nobody has. */
  shapeLimit: number;
}

export const POPULATION_DEFAULTS: PopulationOptions = {
  count: 15,
  seed: 0x5eed,
  noseDetailMm: 2.2,
  shapeLimit: 2.5,
};

/**
 * Bounds a generated face must fall inside to be kept, mm.
 *
 * Rejection sampling, and it is not a nicety. Several basis modes move the same
 * measured span — `faceWidth`, `templeWidth`, `faceTaper` and `jawWidth` all
 * change the temple-landmark distance — so independent draws at plus or minus
 * two and a half sigma each can compound into a face 26% wider than the
 * template. The first version of this file did exactly that and generated a
 * subject with a 196 mm temple span, which is outside the adult human range in
 * any population, and every fit verdict computed against it was meaningless.
 *
 * An orthogonalised basis makes the *coefficients* independent; it does not make
 * the *measurements* independent, and the measurements are what a human range is
 * defined on. So the population is rejected into the range rather than assumed
 * into it.
 *
 * Ranges are the published adult extremes plus a margin, transferred to this
 * mesh's own landmark conventions by ratio (the same rule v1 used for its
 * refusal bounds).
 */
export const PLAUSIBLE = {
  templeWidthMm: [128, 182] as const,
  noseWidthMm: [16, 32] as const,
  nasalRootDepthMm: [8, 22] as const,
  noseProtrusionMm: [11, 25] as const,
};

function isPlausible(m: FaceMeasurements): boolean {
  const within = (v: number, r: readonly [number, number]) => v >= r[0] && v <= r[1];
  return within(m.templeWidth, PLAUSIBLE.templeWidthMm)
    && within(m.noseWidth, PLAUSIBLE.noseWidthMm)
    && within(m.nasalRootDepth, PLAUSIBLE.nasalRootDepthMm)
    && within(Math.abs(m.noseProtrusion), PLAUSIBLE.noseProtrusionMm);
}

/** How many draws the last `generatePopulation` rejected. Reported, not hidden:
 *  a rejection rate near 1 means the basis is generating impossible faces and
 *  the population is no longer a fair sample of anything. */
export let lastRejectionCount = 0;

export function generatePopulation(
  mesh: FaceMesh, basis: ShapeBasis, options: Partial<PopulationOptions> = {},
): SyntheticSubject[] {
  const opt = { ...POPULATION_DEFAULTS, ...options };
  const regions = standardRegions(mesh);
  const subjects: SyntheticSubject[] = [];

  lastRejectionCount = 0;
  for (let s = 0; s < opt.count; s++) {
    let subject: SyntheticSubject | null = null;
    for (let attempt = 0; attempt < 200 && !subject; attempt++) {
      const rng = createRng(opt.seed).fork(s * 1000 + attempt);
      const candidate = generateSubject(mesh, basis, regions.nose.weight, rng, s, opt);
      if (isPlausible(candidate.measurements)) subject = candidate;
      else lastRejectionCount++;
    }
    if (!subject) {
      throw new Error(
        `could not draw a plausible face for slot ${s} in 200 attempts — ` +
        'the basis is generating geometry outside the human range',
      );
    }
    subjects.push(subject);
  }

  // Two named extremes, appended rather than sampled, because a random draw of
  // fifteen will not reliably contain them and they are the two the fit has to
  // survive. Their indices are stable so a report can name them.
  subjects.push(namedExtreme(mesh, basis, regions.nose.weight, opt, 'broad-low', {
    noseWidth: 2.2, noseSidewallFlare: 2.0, noseBridgeDepth: -2.0, noseBridgeHeight: -1.8,
  }, 11.10, 'broad nose, low flat bridge — the frame will slide far down the wedge'));

  subjects.push(namedExtreme(mesh, basis, regions.nose.weight, opt, 'narrow-high', {
    noseWidth: -2.0, noseSidewallFlare: -1.8, noseBridgeDepth: 2.2, noseBridgeHeight: 1.9,
  }, 11.90, 'narrow nose, high prominent bridge — the frame will perch high and stand off'));

  return subjects;
}

function generateSubject(
  mesh: FaceMesh, basis: ShapeBasis, noseWeight: Float64Array, rng: Rng,
  index: number, opt: PopulationOptions,
): SyntheticSubject {
  const coeffs = new Float64Array(basis.dim);
  for (let k = 0; k < basis.dim; k++) coeffs[k] = rng.truncatedNormal(opt.shapeLimit);

  const positions = new Float64Array(mesh.vertexCount * 3);
  evaluateBasis(basis, coeffs, positions);

  const noseDetail = randomNoseField(mesh, noseWeight, rng, opt.noseDetailMm);
  applyNormalField(mesh, positions, noseDetail);

  // Published HVID: group means 11.10 to 11.75, within-group SD ~0.45.
  const groupMean = [11.10, 11.26, 11.75, 11.60][index % 4];
  const irisDiameterMm = groupMean + rng.truncatedNormal(2) * 0.45;

  return {
    id: `S${String(index).padStart(2, '0')}`,
    positions,
    shapeCoeffs: coeffs,
    noseDetail,
    irisDiameterMm,
    measurements: measure(positions),
    description: describe(measure(positions)),
  };
}

function namedExtreme(
  mesh: FaceMesh, basis: ShapeBasis, noseWeight: Float64Array, opt: PopulationOptions,
  id: string, traits: Record<string, number>, irisDiameterMm: number, description: string,
): SyntheticSubject {
  const coeffs = new Float64Array(basis.dim);
  for (const [label, value] of Object.entries(traits)) {
    const k = basis.labels.indexOf(label);
    if (k >= 0) coeffs[k] = value;
  }
  const positions = new Float64Array(mesh.vertexCount * 3);
  evaluateBasis(basis, coeffs, positions);
  const rng = createRng(opt.seed ^ 0xbeef).fork(id.length);
  const noseDetail = randomNoseField(mesh, noseWeight, rng, opt.noseDetailMm);
  applyNormalField(mesh, positions, noseDetail);
  return {
    id, positions, shapeCoeffs: coeffs, noseDetail, irisDiameterMm,
    measurements: measure(positions), description,
  };
}

const describe = (m: FaceMeasurements): string => {
  const nose = m.noseWidth > 26 ? 'broad' : m.noseWidth < 21 ? 'narrow' : 'average';
  const bridge = m.nasalRootDepth > 17 ? 'high' : m.nasalRootDepth < 12 ? 'low' : 'average';
  return `${nose} nose, ${bridge} bridge`;
};

/**
 * A smooth random displacement field over the nose, built from a handful of
 * Gaussian bumps at random seed vertices.
 *
 * Bumps rather than per-vertex noise: per-vertex noise is not a nose, it is
 * sandpaper, and a solver with any smoothness prior at all would flatten it and
 * score well for the wrong reason. Bumps at a few millimetres of radius are the
 * scale real nasal variation actually has — a dorsal hump, a depressed radix, an
 * asymmetric sidewall — and they are exactly what a low-rank basis cannot
 * reproduce.
 */
function randomNoseField(
  mesh: FaceMesh, noseWeight: Float64Array, rng: Rng, amplitudeMm: number,
): Float64Array {
  const out = new Float64Array(mesh.vertexCount);
  const members: number[] = [];
  for (let i = 0; i < mesh.vertexCount; i++) if (noseWeight[i] > 0.2) members.push(i);
  if (members.length === 0) return out;

  const bumps = 5 + Math.floor(rng.next() * 3);
  for (let b = 0; b < bumps; b++) {
    const seed = members[Math.floor(rng.next() * members.length)];
    const radius = rng.range(4, 11);
    const amp = rng.truncatedNormal(2) * amplitudeMm;
    const sx = mesh.positions[seed * 3];
    const sy = mesh.positions[seed * 3 + 1];
    const sz = mesh.positions[seed * 3 + 2];
    for (const i of members) {
      const d = Math.hypot(
        mesh.positions[i * 3] - sx,
        mesh.positions[i * 3 + 1] - sy,
        mesh.positions[i * 3 + 2] - sz,
      );
      out[i] += amp * Math.exp(-(d * d) / (2 * radius * radius));
    }
  }
  for (let i = 0; i < out.length; i++) out[i] *= noseWeight[i];
  return out;
}

function applyNormalField(mesh: FaceMesh, positions: Float64Array, field: Float64Array): void {
  const normals = computeVertexNormals(positions, mesh.indices, mesh.vertexCount);
  for (let i = 0; i < mesh.vertexCount; i++) {
    const d = field[i];
    if (d === 0) continue;
    positions[i * 3] += normals[i * 3] * d;
    positions[i * 3 + 1] += normals[i * 3 + 1] * d;
    positions[i * 3 + 2] += normals[i * 3 + 2] * d;
  }
}

/**
 * How much of this subject the basis can explain — the "this test can fail"
 * instrument.
 *
 * If `residualRmsMm` on the nose is near zero, the basis has absorbed the
 * synthetic detail and any enrollment test built on this subject is measuring
 * the basis rather than the reconstruction.
 */
export function subjectResidualAgainstBasis(
  basis: ShapeBasis, subject: SyntheticSubject,
): { residualRmsMm: number; residualMaxMm: number } {
  const { residualRmsMm, residualMaxMm } = basisExplains(basis, subject.positions);
  return { residualRmsMm, residualMaxMm };
}

// ------------------------------------------------------------------ cameras

export interface CameraGeometry {
  name: string;
  /** Vertical field of view, degrees. */
  fovDeg: number;
  width: number;
  height: number;
  /** How far the camera sits below the wearer's eye line, mm. Negative is above. */
  belowEyesMm: number;
  /** Nominal viewing distance, mm. */
  distanceMm: number;
}

/**
 * The ladder. Every reported number is reported across all of it.
 *
 * `laptop-lid` is the geometry v1's seat could not serve at all, and it is the
 * most common way people use a webcam. `phone-lap` is the harder version of the
 * same thing.
 */
export const CAMERA_LADDER: CameraGeometry[] = [
  { name: 'eye-level', fovDeg: 63, width: 1280, height: 720, belowEyesMm: 0, distanceMm: 500 },
  { name: 'laptop-lid', fovDeg: 55, width: 1280, height: 720, belowEyesMm: 120, distanceMm: 500 },
  { name: 'phone-lap', fovDeg: 68, width: 1080, height: 1920, belowEyesMm: 260, distanceMm: 450 },
];

// ---------------------------------------------------------------- captures

export interface SyntheticFrame {
  /** Ground-truth pose: model -> camera. */
  pose: Pose;
  /** Detector landmarks in pixels, 2 * DETECTOR_LANDMARK_COUNT. NaN where the
   *  landmark was not produced at all. */
  landmarks: Float64Array;
  /** Per-landmark visibility, 0..1, as the detector would report confidence. */
  visibility: Float64Array;
  /** Per-landmark one-sigma the detector claims, px. */
  sigmaPx: Float64Array;
  /** Silhouette samples in pixels, from the true geometry. */
  silhouette: Float64Array;
  /** Which beat of the protocol this frame belongs to. */
  beat: string;
  trueYaw: number;
  truePitch: number;
  trueRoll: number;
}

export interface SyntheticCapture {
  subject: SyntheticSubject;
  geometry: CameraGeometry;
  /** The camera that actually generated the frames. Solvers must not see this. */
  trueIntrinsics: Intrinsics;
  frames: SyntheticFrame[];
}

export interface CaptureOptions {
  seed: number;
  /** Base landmark noise, px at the capture's own resolution. */
  noisePx: number;
  /** Per-landmark systematic bias in face space, mm, one-sigma. Models the
   *  detector's own offset from the mesh — the thing the bundle solves for. */
  biasMm: number;
  /** Include the profile beat. Turning this off is how the harness measures
   *  what the profile view is worth, rather than assuming. */
  includeProfile: boolean;
  /** Include the lean-in/out beat (the focal-length observability term). */
  includeLean: boolean;
  /** Simulate the gaze artefact: MediaPipe's landmarks follow the eyes. */
  gazeAmplitudeMm: number;
  framesPerBeat: number;
  /** Buffer resolution for the visibility rasteriser. */
  rasterWidth: number;
}

export const CAPTURE_DEFAULTS: CaptureOptions = {
  seed: 0xc0ffee,
  noisePx: 0.7,
  biasMm: 0.6,
  includeProfile: true,
  includeLean: true,
  gazeAmplitudeMm: 1.5,
  framesPerBeat: 22,
  rasterWidth: 192,
};

/**
 * The guided protocol, as pose trajectories.
 *
 * Each beat is a named segment with a start and end euler triple; frames are
 * interpolated with a small amount of postural wander added, because a wearer
 * following a dot does not travel a clean line and a solver tuned on clean lines
 * is a solver tuned on nothing.
 */
export interface Beat {
  name: string;
  from: [number, number, number]; // yaw, pitch, roll, degrees
  to: [number, number, number];
  /** Distance multiplier at the start and end of the beat. */
  distanceFrom?: number;
  distanceTo?: number;
}

export function protocolBeats(options: CaptureOptions): Beat[] {
  const beats: Beat[] = [
    { name: 'centre', from: [0, 0, 0], to: [0, 0, 0] },
    { name: 'yaw-right', from: [0, 0, 0], to: [35, 0, 0] },
    { name: 'yaw-left', from: [35, 0, 0], to: [-35, 0, 0] },
    { name: 'return', from: [-35, 0, 0], to: [0, 0, 0] },
    { name: 'pitch-down', from: [0, 0, 0], to: [0, 15, 0] },
    { name: 'pitch-up', from: [0, 15, 0], to: [0, -15, 0] },
  ];
  if (options.includeProfile) {
    beats.push(
      { name: 'profile-right', from: [0, 0, 0], to: [80, 0, 0] },
      { name: 'profile-hold-r', from: [80, 2, 3], to: [82, -2, -3] },
      { name: 'profile-left', from: [80, 0, 0], to: [-80, 0, 0] },
      { name: 'profile-hold-l', from: [-80, 2, -3], to: [-82, -2, 3] },
      { name: 'recentre', from: [-80, 0, 0], to: [0, 0, 0] },
    );
  }
  if (options.includeLean) {
    beats.push(
      { name: 'lean-in', from: [0, 0, 0], to: [0, 0, 0], distanceFrom: 1, distanceTo: 0.62 },
      { name: 'lean-out', from: [0, 0, 0], to: [0, 0, 0], distanceFrom: 0.62, distanceTo: 1.45 },
      { name: 'lean-back', from: [0, 0, 0], to: [0, 0, 0], distanceFrom: 1.45, distanceTo: 1 },
    );
  }
  return beats;
}

export function synthesizeCapture(
  mesh: FaceMesh, subject: SyntheticSubject, geometry: CameraGeometry,
  options: Partial<CaptureOptions> = {},
): SyntheticCapture {
  const opt = { ...CAPTURE_DEFAULTS, ...options };
  const rng = createRng(opt.seed).fork(subject.id.length * 31 + geometry.name.length);

  const trueIntrinsics = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
  const normals = computeVertexNormals(subject.positions, mesh.indices, mesh.vertexCount);

  // The detector's systematic per-landmark offset from the mesh surface, in
  // face space. Constant for a subject+session, which is exactly what makes it
  // separable from pose in a bundle over many views.
  const bias = new Float64Array(mesh.vertexCount * 3);
  for (let i = 0; i < bias.length; i++) bias[i] = rng.truncatedNormal(2.5) * opt.biasMm;

  const rasterHeight = Math.round(opt.rasterWidth * geometry.height / geometry.width);
  const buffer = createDepthBuffer(opt.rasterWidth, rasterHeight, trueIntrinsics);
  const normalsCam = new Float64Array(mesh.vertexCount * 3);

  // Where the head sits relative to the camera, from the geometry. The camera
  // being below the eyes reads to a tracker as head PITCH, which is exactly why
  // a trust law written about the camera axis fails for laptops.
  const basePitch = Math.atan2(geometry.belowEyesMm, geometry.distanceMm);

  const observed = new Float64Array(mesh.vertexCount * 3);
  const frames: SyntheticFrame[] = [];
  const beats = protocolBeats(opt);

  // Postural wander state, carried across the whole capture.
  let wanderX = 0, wanderY = 0, wanderVx = 0, wanderVy = 0;
  let angleWx = 0, angleWy = 0, angleWz = 0;

  for (const beat of beats) {
    for (let f = 0; f < opt.framesPerBeat; f++) {
      const t = opt.framesPerBeat === 1 ? 0 : f / (opt.framesPerBeat - 1);

      // Angular wander is smoothed the same way: a head does not jitter about
      // its own axis by a degree and back between two frames.
      angleWx += (rng.truncatedNormal(2) * 0.25 - angleWx * 0.3) * (Math.PI / 180);
      angleWy += (rng.truncatedNormal(2) * 0.25 - angleWy * 0.3) * (Math.PI / 180);
      angleWz += (rng.truncatedNormal(2) * 0.25 - angleWz * 0.3) * (Math.PI / 180);

      const yaw = lerp(beat.from[0], beat.to[0], t) * (Math.PI / 180) + angleWy;
      const pitch = lerp(beat.from[1], beat.to[1], t) * (Math.PI / 180) + angleWx + basePitch;
      const roll = lerp(beat.from[2], beat.to[2], t) * (Math.PI / 180) + angleWz;
      const distance = geometry.distanceMm
        * lerp(beat.distanceFrom ?? 1, beat.distanceTo ?? 1, t);

      const pose = poseIdentity();
      // Model +Y is up and +Z out of the face; the camera looks down +Z with +Y
      // down. The flip is a rotation of pi about X, folded into the euler build
      // by negating pitch and roll and mirroring y and z.
      const R = m3();
      mat3FromEulerYXZ(R, yaw, pitch, roll);
      // Convert model->camera: first flip the model's axes into CV convention.
      const flip = Float64Array.of(1, 0, 0, 0, -1, 0, 0, 0, -1);
      mul3(pose.R, R, flip);
      // Postural wander as a SMOOTH random walk, not per-frame white noise.
      //
      // An earlier version drew the lateral offset independently each frame,
      // which teleported the head six millimetres between consecutive frames.
      // That is not a wearer, it is a strobe — and it made the One Euro filter
      // look catastrophic (6.5 mm of apparent lag against 1.0 mm unsmoothed)
      // because it was correctly smoothing noise that the ground truth also
      // contained. A filter can only be judged against a trajectory a head could
      // actually follow.
      wanderVx += (rng.truncatedNormal(2) * 0.8 - wanderVx * 0.25);
      wanderVy += (rng.truncatedNormal(2) * 0.8 - wanderVy * 0.25);
      wanderX = Math.max(-14, Math.min(14, wanderX + wanderVx));
      wanderY = Math.max(-14, Math.min(14, wanderY + wanderVy));
      pose.t.set([wanderX, wanderY, distance]);

      // Ground-truth observed points: truth + detector bias, plus the gaze
      // artefact around the eyes.
      observed.set(subject.positions);
      for (let i = 0; i < observed.length; i++) observed[i] += bias[i];
      if (opt.gazeAmplitudeMm > 0) {
        applyGaze(mesh, observed, rng, opt.gazeAmplitudeMm, t);
      }

      const { px, depth } = rasterize(
        buffer, subject.positions, mesh.indices, mesh.vertexCount, pose, trueIntrinsics,
      );
      normalsToCamera(normalsCam, normals, mesh.vertexCount, pose);
      const vis = vertexVisibility(buffer, px, depth, mesh.vertexCount, normalsCam);

      const landmarks = new Float64Array(DETECTOR_LANDMARK_COUNT * 2).fill(NaN);
      const visibility = new Float64Array(DETECTOR_LANDMARK_COUNT);
      const sigmaPx = new Float64Array(DETECTOR_LANDMARK_COUNT).fill(Infinity);

      const uv = new Float64Array(2);
      const uvPrior = new Float64Array(2);
      const cam = v3();
      for (let i = 0; i < mesh.vertexCount; i++) {
        const v = vis[i];
        // The detector emits a landmark even where it cannot see it — it
        // hallucinates the occluded half — so a low-visibility landmark gets a
        // large sigma, not an absence. Modelling it as absent would make the
        // solver's job strictly easier than reality.
        applyPose(cam, pose, observed, i * 3);
        if (!project(uv, trueIntrinsics, cam)) continue;

        // ---- what a hidden landmark actually is -------------------------
        //
        // Not "the true position plus more noise". A landmark network that
        // cannot see a point falls back on its own prior, which is the average
        // face — so the reported position drifts toward where the TEMPLATE
        // would put that feature, and the error is **biased**, not merely
        // noisy. On a wearer whose nose differs from the mean, every occluded
        // frame quietly argues that their nose is average.
        //
        // This matters more than it sounds. An earlier version of this file
        // modelled occlusion as inflated Gaussian noise only, and under that
        // model the silhouette residual measured as worthless — because
        // unbiased noise at profile still carries the true nose shape, so the
        // landmarks alone were enough. Once the bias is here, the profile view
        // stops being redundant. A harness that is kinder than reality does not
        // merely flatter the system; it changes which parts of it look
        // necessary.
        const hidden = 1 - v;
        const sigma = opt.noisePx * (1 + 6 * hidden * hidden);
        let bx = 0, by = 0;
        if (hidden > 0.01) {
          applyPose(cam, pose, mesh.positions, i * 3);
          if (project(uvPrior, trueIntrinsics, cam)) {
            // Pull toward the template's projection, quadratically in how
            // hidden the point is. Fully occluded means fully hallucinated.
            const pull = hidden * hidden;
            bx = (uvPrior[0] - uv[0]) * pull;
            by = (uvPrior[1] - uv[1]) * pull;
          }
        }

        landmarks[i * 2] = uv[0] + bx + rng.normal() * sigma;
        landmarks[i * 2 + 1] = uv[1] + by + rng.normal() * sigma;
        visibility[i] = v;
        sigmaPx[i] = sigma;
      }

      synthesizeIris(
        subject, mesh, pose, trueIntrinsics, landmarks, visibility, sigmaPx, rng, opt.noisePx,
      );

      frames.push({
        pose,
        landmarks,
        visibility,
        sigmaPx,
        silhouette: extractSil(buffer),
        beat: beat.name,
        trueYaw: yaw,
        truePitch: pitch,
        trueRoll: roll,
      });
    }
  }

  return { subject, geometry, trueIntrinsics, frames };
}

// ---------------------------------------------------------------- internals

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

function mul3(out: Float64Array, A: Float64Array, B: Float64Array): void {
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      out[r * 3 + c] = A[r * 3] * B[c] + A[r * 3 + 1] * B[3 + c] + A[r * 3 + 2] * B[6 + c];
    }
  }
}

function applyPose(out: Float64Array, pose: Pose, p: ArrayLike<number>, o: number): void {
  const x = p[o], y = p[o + 1], z = p[o + 2];
  const R = pose.R;
  out[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
  out[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
  out[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
}

/**
 * The gaze artefact, reproduced deliberately.
 *
 * MediaPipe's landmarks follow the eyes: on a held-still head, pure gaze swung
 * v1's bridge landmark by 2.3 mm mean and 4 mm peak, and bent its iris-derived
 * scale by 12-17.8%. This is a documented defect of the detector
 * (google-ai-edge/mediapipe#1786), and any pipeline that reads landmarks near
 * the eyes inherits it.
 *
 * v1's answer was a door: refuse the whole sample while gaze is off-neutral.
 * That works and it throws away frames. v2's answer is to let the bundle see it
 * — it is a *per-frame* perturbation of a *constant* face, so across 150 views it
 * averages out of the identity while the per-frame poses absorb what is left.
 * Generating it here is how that claim gets tested rather than asserted.
 */
function applyGaze(
  mesh: FaceMesh, observed: Float64Array, rng: Rng, amplitudeMm: number, phase: number,
): void {
  // A slow sweep plus jitter — gaze is not white noise, it dwells.
  const gx = Math.sin(phase * Math.PI * 2.5) * amplitudeMm + rng.truncatedNormal(2) * 0.3;
  const gy = Math.cos(phase * Math.PI * 1.7) * amplitudeMm * 0.5;
  const affected = [
    LM.NOSE_BRIDGE, LM.NASION, LM.EYE_INNER_R, LM.EYE_INNER_L,
    LM.EYE_OUTER_R, LM.EYE_OUTER_L, LM.GLABELLA,
  ];
  for (const i of affected) {
    observed[i * 3] += gx * 0.6;
    observed[i * 3 + 1] += gy * 0.6;
  }
}

/**
 * The iris landmarks, which have no mesh vertex.
 *
 * Placed on a circle of this subject's own true iris diameter, in the eye plane,
 * facing the camera. That last part matters: a real iris is a disc on a sphere,
 * so under yaw its projection is an ellipse whose *mean* radius barely changes —
 * which is exactly why the iris is a usable ruler at angle and the pupil
 * separation is not.
 */
function synthesizeIris(
  subject: SyntheticSubject, mesh: FaceMesh, pose: Pose, k: Intrinsics,
  landmarks: Float64Array, visibility: Float64Array, sigmaPx: Float64Array,
  rng: Rng, noisePx: number,
): void {
  const radius = subject.irisDiameterMm / 2;
  const eyes: [number, number, readonly number[]][] = [
    [LM.EYE_INNER_R, LM.IRIS_R_CENTRE, LM.IRIS_R_CONTOUR],
    [LM.EYE_INNER_L, LM.IRIS_L_CENTRE, LM.IRIS_L_CONTOUR],
  ];

  const cam = v3();
  const uv = new Float64Array(2);

  for (const [anchor, centreIdx, contour] of eyes) {
    // Centre the iris on the midpoint of the inner and outer corner, pushed
    // slightly forward — the eyeball surface, not the lid plane.
    const outer = anchor === LM.EYE_INNER_R ? LM.EYE_OUTER_R : LM.EYE_OUTER_L;
    const cxm = (subject.positions[anchor * 3] + subject.positions[outer * 3]) / 2;
    const cym = (subject.positions[anchor * 3 + 1] + subject.positions[outer * 3 + 1]) / 2;
    const czm = (subject.positions[anchor * 3 + 2] + subject.positions[outer * 3 + 2]) / 2 + 2;

    applyPose(cam, pose, Float64Array.of(cxm, cym, czm), 0);
    if (!project(uv, k, cam)) continue;
    landmarks[centreIdx * 2] = uv[0] + rng.normal() * noisePx * 0.5;
    landmarks[centreIdx * 2 + 1] = uv[1] + rng.normal() * noisePx * 0.5;
    visibility[centreIdx] = 1;
    sigmaPx[centreIdx] = noisePx * 0.5;

    // Contour points on a camera-facing circle at the iris depth.
    const scale = k.f / cam[2];
    for (let c = 0; c < contour.length; c++) {
      const angle = (c / contour.length) * Math.PI * 2;
      const px = uv[0] + Math.cos(angle) * radius * scale;
      const py = uv[1] + Math.sin(angle) * radius * scale;
      const idx = contour[c];
      landmarks[idx * 2] = px + rng.normal() * noisePx * 0.6;
      landmarks[idx * 2 + 1] = py + rng.normal() * noisePx * 0.6;
      visibility[idx] = 1;
      sigmaPx[idx] = noisePx * 0.6;
    }
  }
}

function extractSil(buffer: { width: number; height: number; depth: Float32Array; scale: number }): Float64Array {
  const W = buffer.width, H = buffer.height;
  const out: number[] = [];
  const inv = 1 / buffer.scale;
  for (let y = 1; y < H - 1; y++) {
    for (let x = 1; x < W - 1; x++) {
      const o = y * W + x;
      if (!(buffer.depth[o] < Infinity)) continue;
      if (
        !(buffer.depth[o - 1] < Infinity) || !(buffer.depth[o + 1] < Infinity) ||
        !(buffer.depth[o - W] < Infinity) || !(buffer.depth[o + W] < Infinity)
      ) out.push((x + 0.5) * inv, (y + 0.5) * inv);
    }
  }
  return Float64Array.from(out);
}

export { FIRST_IRIS_INDEX, DETECTOR_LANDMARK_COUNT };
