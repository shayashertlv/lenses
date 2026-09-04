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
  type Intrinsics, intrinsicsFromFov, poseRotationFromHeadEuler, project,
} from '../core/camera.js';
import { type Pose, poseIdentity, v3 } from '../core/linalg.js';
import {
  createDepthBuffer, normalsToCamera, rasterize, vertexVisibility,
} from '../core/raster.js';
import { createRng, deriveSeed, type Rng } from './random.js';
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
  // An explicitly-undefined seed means "the default", not "seed 0". Without
  // this, `{ seed: maybeSeed }` with `maybeSeed === undefined` would clobber
  // the default in the spread and run every caller on `createRng(0)` —
  // silently different from the historical run AND identical across callers.
  if (options.seed === undefined) opt.seed = POPULATION_DEFAULTS.seed;
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

  const groupMean = HVID_GROUP_MEANS[index % HVID_GROUP_MEANS.length];
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

/**
 * A 32-bit hash of a label, for salting an RNG stream. FNV-1a.
 *
 * This exists because of a defect that sat under every published sweep in the
 * tree. The salts here used to be **string lengths** — `subject.id.length` for a
 * capture, `id.length` for a named extreme — and a sampled id is `S00`, `S01`,
 * `S02` ..., always exactly three characters. Every sampled subject therefore
 * drew the *same* detector bias field, the *same* postural wander and the *same*
 * landmark noise, byte for byte, through the final frame; the streams did not
 * even desynchronise. `eye-level` and `phone-lap` are both nine characters, so
 * the salt `id.length * 31 + name.length` took exactly **six** values over the
 * default 17 x 3 population-by-camera grid: six noise streams where there should
 * have been fifty-one. Measured before the fix on the eight-subject population
 * (six drawn plus the two named extremes), 15 of the 28 subject pairs shared a
 * byte-identical wander trace, and 2 of the 3 camera geometries were
 * indistinguishable.
 *
 * That is nearly invisible in a region-RMS column and fatal in a scalar one. One
 * fixed subject over ten independent capture draws spans 0.71 to 3.12 mm of
 * protrusion error and 0.49 to 2.60 mm of standoff — as wide as the entire
 * across-subject spread the reports print. A noise-driven tail failure shared by
 * every subject is **common-mode**: it can never surface as an outlier, and any
 * constant bracketed by such a sweep is calibrated on one frozen draw.
 * `VERTEX_SEAT_SIGMA_MM` was: its 8-sample sweep read 1.58 / 2.81 mm
 * (median/p90) on the frozen draw and 1.95 / 6.94 on an honest one.
 *
 * `generatePopulation` never had the bug — it folds the subject index and the
 * attempt number into the fork salt as integers. This is the same idea for
 * things whose identity is a name rather than an index.
 */
function hashLabel(label: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < label.length; i++) {
    h = Math.imul(h ^ label.charCodeAt(i), 0x01000193);
  }
  return h >>> 0;
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
  // Hashed, not `id.length`: the two extremes shipped happened to have names of
  // different lengths, so this one was latent rather than live — but a third
  // extreme named to match either would have silently inherited its nose.
  const rng = createRng(opt.seed ^ 0xbeef).fork(hashLabel(id));
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
  /**
   * How much the detector's landmarks UNDER-ROTATE at large yaw, 0..1.
   *
   * The landmarks are generated by rotating the true geometry by
   * `yaw * (1 - k * sin^2(yaw))` instead of `yaw`. Zero reproduces the old
   * behaviour exactly.
   *
   * **This models a real effect the harness could not previously represent, and
   * it is calibrated from a single report.** A wearer found that the scan's
   * "30 degrees" arrived only after roughly 70 degrees of physical turn. The
   * synthetic pipeline, projecting true geometry, reads yaw to within ten
   * percent and even slightly over-reads — so it was structurally incapable of
   * catching the problem that made the guided scan unusable.
   *
   * `k = 0.75` reproduces roughly 30 measured at 70 physical. That is one
   * anecdote, not a measurement, which is why the default is **0**: turning it
   * on would silently move every accuracy number in this repository onto an
   * uncalibrated basis. Turn it on deliberately to see what it costs, and see
   * `docs/OPEN-QUESTIONS.md` Q13 for what it would take to calibrate properly.
   */
  yawUnderRotation: number;
  /**
   * Multiplier on the postural and angular wander a resting head performs.
   *
   * **This exists because the wander speed is what decides a filter's verdict,
   * and nothing was ever measured to set it.** The lateral wander is an AR(1)
   * velocity driven at 0.8 mm/frame, which lands at a steady-state spread near
   * 1.06 mm/frame per axis — around 45 mm/s of combined lateral drift at 30 fps,
   * with the *angle* frozen, during the beats that ask the wearer to hold still.
   * A seated person watching a screen sways an order slower than that.
   *
   * A lagging estimator's error moves with the true trajectory's ACCELERATION,
   * so this multiplier scales the penalty the harness charges for smoothing
   * almost directly. `report:track`'s crawl and shimmer lines cannot be read as
   * a verdict on the One Euro without sweeping it — see `TrackerOptions.smooth`.
   *
   * `1` reproduces the previous behaviour bit for bit: it multiplies the drawn
   * value rather than the number of draws, so the random stream is untouched.
   */
  wanderScale: number;
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
  yawUnderRotation: 0,
  wanderScale: 1,
  framesPerBeat: 22,
  rasterWidth: 192,
};

// -------------------------------------------------------------- campaign seeds

/**
 * How one campaign seed becomes the two sub-seeds a report actually runs on.
 *
 * A replication campaign wants ONE number per run — "seed 3" — but the
 * synthetic pipeline has two independent noise domains with two different base
 * seeds: the population (0x5eed: who the subjects are) and the captures
 * (0xc0ffee: what the detector saw of them). Feeding the same raw campaign
 * seed to both would put both domains on the same `createRng` base, and their
 * fork salts are different in *kind* (small integers for subjects, 32-bit
 * label hashes for captures), not guaranteed disjoint — a label hash landing in
 * the population's salt range would recreate exactly the shared-stream defect
 * `hashLabel` documents. So the campaign seed is folded into each domain's own
 * base with `deriveSeed`, which preserves the 0x5eed / 0xc0ffee separation
 * structurally rather than probabilistically.
 *
 * `undefined` passes through as `undefined` — i.e. "use the historical
 * default" — so a report that threads `seed: populationSeedFor(opt.seed)` with
 * no campaign seed set reproduces the pre-campaign run byte for byte.
 *
 * These two functions are the ONLY sanctioned spelling of the fold. A
 * measurement agent that wants the same subjects a report drew at campaign
 * seed k must call `generatePopulation(mesh, basis, { count, seed:
 * populationSeedFor(k) })`, not invent its own mix.
 */
export function populationSeedFor(campaignSeed: number | undefined): number | undefined {
  if (campaignSeed === undefined) return undefined;
  assertCampaignSeed(campaignSeed);
  return deriveSeed(POPULATION_DEFAULTS.seed, campaignSeed);
}

/** See `populationSeedFor`. The capture-domain half of the same fold. */
export function captureSeedFor(campaignSeed: number | undefined): number | undefined {
  if (campaignSeed === undefined) return undefined;
  assertCampaignSeed(campaignSeed);
  return deriveSeed(CAPTURE_DEFAULTS.seed, campaignSeed);
}

/** 0xffffffff would alias the unseeded historical run (`salt + 1` wraps to 0
 *  in the fork mix), so a "replicate" at that seed would replicate nothing.
 *  Rejected here so every threading path — report entry point or direct call —
 *  refuses it, not only `acrossSeeds`. */
function assertCampaignSeed(seed: number): void {
  if (!Number.isInteger(seed) || seed < 0 || seed > 0xfffffffe) {
    throw new Error(
      `campaign seed ${seed} is not an integer in [0, 0xfffffffe] — ` +
      '0xffffffff aliases the unseeded run through the fork mix',
    );
  }
}

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
      // `profile-dwell-*` LINGER near profile; they do not hold. Each ramps two
      // degrees of yaw, four of pitch and six of roll across the beat, on
      // purpose — a wearer asked to look sideways does not freeze, and the
      // bundle wants the small angular spread. They were called `profile-hold-*`
      // until 2026-09-03, and `report-track.ts` believed the name: it pooled
      // them into a window meant to contain a MOTIONLESS head, where six degrees
      // of roll at 80 degrees of yaw is 0.704 mm/frame of true bridge
      // motion, measured with the wander switched off entirely. Nothing in this tree branches on a beat's name; only humans and
      // their metrics do, which is exactly why it has to be true.
      { name: 'profile-dwell-r', from: [80, 2, 3], to: [82, -2, -3] },
      { name: 'profile-left', from: [80, 0, 0], to: [-80, 0, 0] },
      { name: 'profile-dwell-l', from: [-80, 2, -3], to: [-82, -2, 3] },
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
  // Same guard as `generatePopulation`: an explicitly-undefined seed means the
  // default, never `createRng(0)`.
  if (options.seed === undefined) opt.seed = CAPTURE_DEFAULTS.seed;
  // One stream per (subject, geometry) pair, keyed by the pair's *names* rather
  // than their lengths. See `hashLabel` for what the length version cost: six
  // distinct noise streams across a grid of fifty-one, and every scalar accuracy
  // number in this tree calibrated on one frozen draw.
  const rng = createRng(opt.seed).fork(hashLabel(`${subject.id}/${geometry.name}`));

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
      angleWx += (rng.truncatedNormal(2) * 0.25 * opt.wanderScale - angleWx * 0.3) * (Math.PI / 180);
      angleWy += (rng.truncatedNormal(2) * 0.25 * opt.wanderScale - angleWy * 0.3) * (Math.PI / 180);
      angleWz += (rng.truncatedNormal(2) * 0.25 * opt.wanderScale - angleWz * 0.3) * (Math.PI / 180);

      const yaw = lerp(beat.from[0], beat.to[0], t) * (Math.PI / 180) + angleWy;
      const pitch = lerp(beat.from[1], beat.to[1], t) * (Math.PI / 180) + angleWx + basePitch;
      const roll = lerp(beat.from[2], beat.to[2], t) * (Math.PI / 180) + angleWz;
      const distance = geometry.distanceMm
        * lerp(beat.distanceFrom ?? 1, beat.distanceTo ?? 1, t);

      // What the DETECTOR would report, if it under-rotates at yaw. The true
      // pose (used for ground truth) is unaffected; only the landmarks move.
      const reportedYaw = opt.yawUnderRotation > 0
        ? yaw * (1 - opt.yawUnderRotation * Math.sin(yaw) * Math.sin(yaw))
        : yaw;

      const pose = poseIdentity();
      // Model->camera, composed the way a real pose factorises: FLIP * R_head.
      //
      // This used to be `R_head * FLIP`, which is the conjugate — the same
      // rotation with yaw and roll negated. It cancelled exactly against the
      // matching error in `headEuler`, so the synthetic pipeline agreed with
      // itself while inverting head yaw on every real face. One shared helper
      // now, so the two cannot drift apart again.
      poseRotationFromHeadEuler(pose.R, yaw, pitch, roll);
      // Postural wander as a SMOOTH random walk, not per-frame white noise.
      //
      // An earlier version drew the lateral offset independently each frame,
      // which teleported the head six millimetres between consecutive frames.
      // That is not a wearer, it is a strobe — and it made the One Euro filter
      // look catastrophic (6.5 mm of apparent lag against 1.0 mm unsmoothed)
      // because it was correctly smoothing noise that the ground truth also
      // contained. A filter can only be judged against a trajectory a head could
      // actually follow.
      wanderVx += (rng.truncatedNormal(2) * 0.8 * opt.wanderScale - wanderVx * 0.25);
      wanderVy += (rng.truncatedNormal(2) * 0.8 * opt.wanderScale - wanderVy * 0.25);
      wanderX = Math.max(-14, Math.min(14, wanderX + wanderVx));
      wanderY = Math.max(-14, Math.min(14, wanderY + wanderVy));
      pose.t.set([wanderX, wanderY, distance]);

      // Ground-truth observed points: truth + detector bias, plus the gaze
      // artefact around the eyes.
      observed.set(subject.positions);
      for (let i = 0; i < observed.length; i++) observed[i] += bias[i];
      if (opt.gazeAmplitudeMm > 0) {
        applyGaze(observed, rng, opt.gazeAmplitudeMm, t);
      }

      // The pose the landmarks are actually drawn from. Identical to `pose`
      // unless under-rotation is switched on.
      const reportedPose = poseIdentity();
      poseRotationFromHeadEuler(reportedPose.R, reportedYaw, pitch, roll);
      reportedPose.t.set(pose.t);

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
        applyPose(cam, reportedPose, observed, i * 3);
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
          applyPose(cam, reportedPose, mesh.positions, i * 3);
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
        subject, mesh, observed, reportedPose, trueIntrinsics, landmarks, visibility,
        sigmaPx, rng, opt.noisePx,
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

/**
 * The postural wander trace of a capture: `pose.t[0]` and `pose.t[1]` per frame,
 * mm, interleaved.
 *
 * Exported because it is the cheapest instrument that can catch a collapsed RNG
 * salt, and because it catches it *exactly*. Wander is a smoothed random walk
 * carried across the whole capture, so two captures drawn from the same stream
 * agree on it to the last bit through the final frame — there is no drift to
 * hide behind and no tolerance to argue about. It is also all but insensitive to
 * the subject's own geometry, because every frame consumes the same number of
 * draws for any face whose vertices all project: two different faces filmed by
 * the same camera share this trace **only** if they share a noise stream.
 */
export function wanderTrace(capture: SyntheticCapture): Float64Array {
  const out = new Float64Array(capture.frames.length * 2);
  for (let f = 0; f < capture.frames.length; f++) {
    out[f * 2] = capture.frames[f].pose.t[0];
    out[f * 2 + 1] = capture.frames[f].pose.t[1];
  }
  return out;
}

/** True if two captures were drawn from the same noise stream. */
export function sharesNoiseStream(a: SyntheticCapture, b: SyntheticCapture): boolean {
  const ta = wanderTrace(a), tb = wanderTrace(b);
  if (ta.length !== tb.length || ta.length === 0) return false;
  for (let i = 0; i < ta.length; i++) if (ta[i] !== tb[i]) return false;
  return true;
}

/**
 * Throws if any two of these captures share a noise stream.
 *
 * The standing guard against the defect described in `hashLabel`. Pass one
 * capture per distinct (subject, geometry) pair — passing the same pair twice is
 * a caller error and this will correctly report it as a collision.
 *
 * Worth running over the *whole* grid a report sweeps rather than one sampled
 * pair. The length collision was not uniform: it hit every pair of *sampled*
 * subjects, but only two of the three geometries (`eye-level` and `phone-lap`
 * are both nine characters; `laptop-lid` is ten), and the two named extremes
 * escaped it entirely because their names happen to differ in length. A spot
 * check on the wrong pair would have passed.
 */
export function assertDistinctNoiseStreams(captures: SyntheticCapture[]): void {
  for (let i = 0; i < captures.length; i++) {
    for (let j = i + 1; j < captures.length; j++) {
      if (!sharesNoiseStream(captures[i], captures[j])) continue;
      const name = (c: SyntheticCapture) => `${c.subject.id}/${c.geometry.name}`;
      throw new Error(
        `${name(captures[i])} and ${name(captures[j])} were drawn from the same ` +
        'noise stream — their postural wander is byte-identical over all ' +
        `${captures[i].frames.length} frames. The capture RNG salt has collapsed; ` +
        'every scalar measurement over this grid is common-mode.',
      );
    }
  }
}

// ---------------------------------------------------------------- internals

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

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
  observed: Float64Array, rng: Rng, amplitudeMm: number, phase: number,
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
 * **A disc in the head, not a circle in the image**, and the difference is the
 * whole reason this function was rewritten.
 *
 * It used to place the four contour points on a camera-facing circle straight in
 * pixel space — `px = uv[0] + cos(angle) * radius * scale` — so the projected
 * iris was a perfect circle at every yaw, at every distance, with `visibility`
 * pinned to 1 even at 86 degrees where the far iris is behind the nose. Its own
 * docstring asserted the physics it declined to implement: *"a real iris is a
 * disc on a sphere, so under yaw its projection is an ellipse whose mean radius
 * barely changes"*. That is the claim the iris ruler rests on, and the harness
 * was not testing it — it was assuming it, and then reporting the assumption
 * back as a passing test.
 *
 * What it does now:
 *
 * - The iris is a disc of the subject's own diameter lying in the eye, with a
 *   normal that **rotates with the head**.
 * - Except that the eye counter-rotates in the orbit to keep the camera
 *   fixated, which is what actually keeps the iris readable at angle — the mean
 *   radius argument is second-order beside it. That counter-rotation runs out:
 *   see `EYE_ROTATION_LIMIT_DEG`.
 * - Beyond that the disc turns with the head and its projection foreshortens
 *   into a genuine ellipse.
 * - Visibility is inherited from the eye corner the iris sits between, and the
 *   reading is degraded to match: pulled toward the detector's prior — a
 *   population-mean circle on the average face, held face-on — quadratically in
 *   how hidden it is, with sigma inflated the same way the mesh loop inflates
 *   it. An iris behind the nose is a hallucination, not a clean reading.
 *
 *   This used to be a flag and nothing else. The contour points were written as
 *   exact projections of the subject's own disc whatever the visibility said,
 *   and `readIris` — the only consumer — reads no visibility, so nothing
 *   downstream ever saw the flag drop. The ruler this tree takes its absolute
 *   millimetres from was the one landmark exempt from the file's own occlusion
 *   model, and the 55-degree yaw gate was removed on a measurement taken under
 *   that exemption (`enroll/scale.ts`).
 */

/**
 * How far the eye can rotate in its orbit, degrees.
 *
 * `published`. The oculomotor range is about 50 degrees from primary position in
 * each direction; comfortable sustained gaze is nearer 30. 50 is used because
 * the scan actively asks the wearer to keep looking at the guide dot, which is
 * exactly the condition that recruits the full range.
 *
 * This constant is what decides whether the iris ruler works at angle. Below it
 * the wearer holds fixation and the disc stays fronto-parallel; above it the
 * disc turns with the head and the ruler starts to foreshorten.
 */
const EYE_ROTATION_LIMIT_DEG = 50;

/** Published HVID group means, 11.10 to 11.75, within-group SD ~0.45. */
const HVID_GROUP_MEANS = [11.10, 11.26, 11.75, 11.60];

/**
 * What a detector that cannot see the iris falls back on, mm.
 *
 * The mesh loop's prior is the template — the average face. The iris has no
 * template vertex, so its prior is the same thing said about a diameter: the
 * mean of the population this harness generates. A network that cannot see the
 * iris reports a circle of typical size, and on a wearer whose iris is not
 * typical that is a BIAS in the one quantity this tree reads absolute
 * millimetres from.
 */
const IRIS_PRIOR_MM = HVID_GROUP_MEANS.reduce((a, b) => a + b, 0) / HVID_GROUP_MEANS.length;
function synthesizeIris(
  subject: SyntheticSubject, mesh: FaceMesh, observed: Float64Array, pose: Pose,
  k: Intrinsics, landmarks: Float64Array, visibility: Float64Array, sigmaPx: Float64Array,
  rng: Rng, noisePx: number, eyeRotationLimitDeg = EYE_ROTATION_LIMIT_DEG,
): void {
  const radius = subject.irisDiameterMm / 2;

  // Where the camera is, in FACE space: the pose maps face to camera, so the
  // camera's own origin comes back through the inverse.
  const R = pose.R;
  const camPos = Float64Array.of(
    -(R[0] * pose.t[0] + R[3] * pose.t[1] + R[6] * pose.t[2]),
    -(R[1] * pose.t[0] + R[4] * pose.t[1] + R[7] * pose.t[2]),
    -(R[2] * pose.t[0] + R[5] * pose.t[1] + R[8] * pose.t[2]),
  );
  const limit = (eyeRotationLimitDeg * Math.PI) / 180;
  const eyes: [number, number, readonly number[]][] = [
    [LM.EYE_INNER_R, LM.IRIS_R_CENTRE, LM.IRIS_R_CONTOUR],
    [LM.EYE_INNER_L, LM.IRIS_L_CENTRE, LM.IRIS_L_CONTOUR],
  ];

  const cam = v3();
  const uv = new Float64Array(2);

  for (const [anchor, centreIdx, contour] of eyes) {
    // Centre the iris on the midpoint of the inner and outer corner, pushed
    // slightly forward — the eyeball surface, not the lid plane.
    // `observed`, not `subject.positions`: the iris sits on the geometry the
    // detector reports, so it inherits the detector-bias field like every other
    // landmark. Reading the true positions here exempted the ruler from a bias
    // the rest of the frame carries.
    const outer = anchor === LM.EYE_INNER_R ? LM.EYE_OUTER_R : LM.EYE_OUTER_L;
    const cxm = (observed[anchor * 3] + observed[outer * 3]) / 2;
    const cym = (observed[anchor * 3 + 1] + observed[outer * 3 + 1]) / 2;
    const czm = (observed[anchor * 3 + 2] + observed[outer * 3 + 2]) / 2 + 2;

    applyPose(cam, pose, Float64Array.of(cxm, cym, czm), 0);
    if (!project(uv, k, cam)) continue;
    landmarks[centreIdx * 2] = uv[0] + rng.normal() * noisePx * 0.5;
    landmarks[centreIdx * 2 + 1] = uv[1] + rng.normal() * noisePx * 0.5;
    visibility[centreIdx] = 1;
    sigmaPx[centreIdx] = noisePx * 0.5;

    // Which way the iris disc faces, in face space.
    //
    // Straight ahead is +Z. The eye rotates toward the camera to hold fixation,
    // as far as the orbit allows; past that the disc is dragged round by the
    // head and starts to foreshorten.
    let tx = camPos[0] - cxm, ty = camPos[1] - cym, tz = camPos[2] - czm;
    const tl = Math.hypot(tx, ty, tz) || 1;
    tx /= tl; ty /= tl; tz /= tl;
    const need = Math.acos(Math.max(-1, Math.min(1, tz)));   // angle from +Z

    let nx: number, ny: number, nz: number;
    if (need <= limit || need < 1e-6) {
      nx = tx; ny = ty; nz = tz;                              // fixation holds
    } else {
      // Rotate +Z toward the camera by exactly `limit` — a slerp at t = limit/need.
      const sn = Math.sin(need);
      const a = Math.sin(need - limit) / sn;
      const b = Math.sin(limit) / sn;
      nx = b * tx; ny = b * ty; nz = a + b * tz;
      const nl = Math.hypot(nx, ny, nz) || 1;
      nx /= nl; ny /= nl; nz /= nl;
    }

    // Any two in-plane axes will do; the contour is a circle.
    const basisFor = (bnx: number, bny: number, bnz: number) => {
      const helper = Math.abs(bnz) < 0.9 ? [0, 0, 1] : [1, 0, 0];
      let hx = bny * helper[2] - bnz * helper[1];
      let hy = bnz * helper[0] - bnx * helper[2];
      let hz = bnx * helper[1] - bny * helper[0];
      const hl = Math.hypot(hx, hy, hz) || 1;
      hx /= hl; hy /= hl; hz /= hl;
      return {
        ux: hx, uy: hy, uz: hz,
        vx: bny * hz - bnz * hy, vy: bnz * hx - bnx * hz, vz: bnx * hy - bny * hx,
      };
    };
    const disc = basisFor(nx, ny, nz);

    // ---- what a hidden iris actually is ------------------------------
    //
    // The mesh loop above makes this argument once already: a landmark network
    // that cannot see a point falls back on its own prior, so the error is
    // BIASED and not merely noisy. The iris was exempt from it — occluded
    // contour points were written as exact projections of the subject's own
    // disc, with the tightest sigma in the frame, and only `visibility`
    // dropped. `readIris` is the sole consumer and reads no visibility, so the
    // flag protected nothing and the docstring's promise that a hidden iris is
    // "reported as hidden rather than as a clean reading" described a flag
    // nothing reads.
    //
    // It matters most here. The iris is the only landmark this tree reads
    // absolute millimetres from, `readIris` AVERAGES the two eyes, and the far
    // eye's corner is invisible from about 45 degrees of yaw — so a harness
    // that hands back a bit-true foreshortened far iris is not modelling the
    // ruler's dominant failure mode at all. The 55-degree yaw gate was removed
    // on a measurement taken under exactly that exemption.
    //
    // The prior: the average face's eye, an iris of the population's mean
    // diameter, held face-on. A prior does not know the head has turned, so it
    // does not foreshorten.
    // How hidden the iris is, which is NOT the eye corner's visibility.
    //
    // `vertexVisibility` returns the surface's facing cosine where it is
    // visible and 0 where it is not, so the inner canthus reads about 0.7 even
    // head-on — it sits in a crease. Driving the hallucination off that would
    // bias the ruler on a frontal frame, where a real detector sees the iris
    // perfectly, and would make this harness harsher than reality instead of
    // kinder. The two things that actually stop a detector reading an iris are
    // the nose getting in front of it, and the disc turning away once fixation
    // runs out.
    //
    // The occlusion half asks the eye REGION, not one vertex.
    // `vertexVisibility`'s value is the vertex's own normal-facing cosine where
    // the depth test passes, and the INNER canthus reads exactly 0 on some faces
    // at zero yaw — it sits in a crease. Keying off it alone marked 100% of
    // near-frontal frames fully hallucinated for 2 of 8 subjects.
    //
    // **A residue survives and a plain depth test is not the cure.** Taking
    // `max(inner, outer)` leaves 0.76% of frontal eye-observations reading
    // occluded when nothing occludes them, which is depth-buffer quantisation at
    // the 192 px visibility raster. The obvious repair — ask `vertexVisibility`
    // without normals, so it answers occlusion alone — is worse, and measurably:
    // 3.74% on the same frames. Dropping the normals also drops the SLOPE
    // TOLERANCE that `raster.ts` widens on grazing surfaces for exactly this
    // reason, so the depth test gets stricter, not cleaner. A finer raster fixes
    // it (0.00% at 384 px) at a cost every silhouette number in the tree would
    // pay, which is not worth 0.76%.
    const regionSeen = Math.max(visibility[anchor], visibility[outer]);
    const discFacing = regionSeen > 0 ? Math.max(0, nx * tx + ny * ty + nz * tz) : 0;
    const hidden = 1 - discFacing;
    const pull = hidden * hidden;
    const inflate = 1 + 6 * hidden * hidden;

    // **The inflation is one displacement of the whole disc, not four
    // independent draws on its rim.**
    //
    // The mesh loop can scatter each landmark on its own because nothing
    // downstream combines them non-linearly. `readIris` does: it estimates the
    // radius as mean|contour - centre|, a NORM, and isotropic scatter on the rim
    // biases a norm upward — +18% at full inflation on this geometry, against a
    // prior effect worth 2.7-3.6% and signed per wearer. Injecting it per point
    // would manufacture a one-directional scale bias five to seven times the
    // size of the thing being modelled, in the one quantity this tree reads
    // absolute millimetres from.
    //
    // A regressor that cannot see the iris does not jitter its rim; it puts a
    // plausible disc in the wrong place. So the extra width is drawn once per
    // eye and added to the centre and every contour point alike, where it
    // cancels in the radius exactly as a placement error should. `sigmaPx` still
    // reports the inflated figure: the claimed uncertainty is honest, and
    // `readIris` reading none of it is a separate matter.
    const displaceX = rng.normal() * noisePx * (inflate - 1) * 0.6;
    const displaceY = rng.normal() * noisePx * (inflate - 1) * 0.6;
    const noise = (scale: number) => rng.normal() * noisePx * scale;
    const sigmaOf = (scale: number) => noisePx * scale * inflate;

    let prior: {
      cx: number; cy: number; cz: number; r: number;
      ux: number; uy: number; uz: number; vx: number; vy: number; vz: number;
    } | null = null;
    if (hidden > 0.01) {
      const px = (mesh.positions[anchor * 3] + mesh.positions[outer * 3]) / 2;
      const py = (mesh.positions[anchor * 3 + 1] + mesh.positions[outer * 3 + 1]) / 2;
      const pz = (mesh.positions[anchor * 3 + 2] + mesh.positions[outer * 3 + 2]) / 2 + 2;
      // The same orientation as the subject's own disc. The mesh loop's prior
      // is the TEMPLATE vertex projected through the same pose, so this is the
      // template's eye through the same pose and the same fixation — differing
      // from the truth in exactly the two things a detector's prior differs in,
      // where the average face keeps that eye and how big the average iris is.
      // Holding it face-on instead would be inventing a story about
      // foreshortening that nothing here measured.
      prior = { cx: px, cy: py, cz: pz, r: IRIS_PRIOR_MM / 2, ...disc };
    }

    // The centre: pulled toward the prior and displaced with the disc.
    const pcv = new Float64Array(2);
    let cbx = 0, cby = 0;
    if (prior) {
      applyPose(cam, pose, Float64Array.of(prior.cx, prior.cy, prior.cz), 0);
      if (project(pcv, k, cam)) {
        cbx = (pcv[0] - uv[0]) * pull;
        cby = (pcv[1] - uv[1]) * pull;
      }
    }
    landmarks[centreIdx * 2] = uv[0] + cbx + displaceX + noise(0.5);
    landmarks[centreIdx * 2 + 1] = uv[1] + cby + displaceY + noise(0.5);
    sigmaPx[centreIdx] = sigmaOf(0.5);

    const pt = v3();
    const puv = new Float64Array(2);
    const prv = new Float64Array(2);
    for (let c = 0; c < contour.length; c++) {
      const angle = (c / contour.length) * Math.PI * 2;
      const cosA = Math.cos(angle), sinA = Math.sin(angle);
      const ca = cosA * radius, sa = sinA * radius;
      const idx = contour[c];
      applyPose(pt, pose, Float64Array.of(
        cxm + ca * disc.ux + sa * disc.vx,
        cym + ca * disc.uy + sa * disc.vy,
        czm + ca * disc.uz + sa * disc.vz,
      ), 0);
      if (!project(puv, k, pt)) { visibility[idx] = 0; continue; }
      let bx = 0, by = 0;
      if (prior) {
        const pa = cosA * prior.r, pb = sinA * prior.r;
        applyPose(pt, pose, Float64Array.of(
          prior.cx + pa * prior.ux + pb * prior.vx,
          prior.cy + pa * prior.uy + pb * prior.vy,
          prior.cz + pa * prior.uz + pb * prior.vz,
        ), 0);
        if (project(prv, k, pt)) {
          bx = (prv[0] - puv[0]) * pull;
          by = (prv[1] - puv[1]) * pull;
        }
      }
      landmarks[idx * 2] = puv[0] + bx + displaceX + noise(0.6);
      landmarks[idx * 2 + 1] = puv[1] + by + displaceY + noise(0.6);
      visibility[idx] = regionSeen;
      sigmaPx[idx] = sigmaOf(0.6);
    }
    visibility[centreIdx] = regionSeen;
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
