/**
 * The MediaPipe adapter.
 *
 * One job: turn whatever the detector emits into `{ landmarks, sigmaPx }` in
 * **pixels**, and know nothing else about the system. Everything downstream
 * consumes that pair and only that pair, which is what makes swapping the
 * detector a one-file change — and swapping it is a real plan, not a hypothetical
 * (see `docs/OPEN-QUESTIONS.md` Q1: a dense-landmark model that reports its own
 * uncertainty would let `detect/uncertainty.ts` shrink to three lines).
 *
 * ## Two things carried over from v1, unchanged, because they were right
 *
 * **`numFaces: 2`.** MediaPipe applies its own internal landmark smoothing when,
 * and only when, `numFaces` is 1 — an untunable filter with no readout, sitting
 * upstream of every number the pipeline sees. Asking for two switches it off.
 * v1 measured this as a large, immediately visible reduction in lag, and it
 * remains the single highest-value line in this file.
 *
 * **Picking the wearer by landmark-bounding-box area.** The cost of the above is
 * that `[0]` stops being a safe index: nothing documents the ordering, so a
 * second person or a portrait on the wall could take the glasses. Area rather
 * than width, because a face turned to profile loses width without getting
 * further away.
 *
 * ## What is deliberately NOT carried over
 *
 * v1's roll pre-rotation — rotating the detect image upright before inference
 * and un-rotating the result. It was a good fix for a real problem (the
 * landmarker degrades past ~40 degrees of roll) and it is not needed here in the
 * same way, because a rolled head no longer degrades the *fit*: shape is frozen,
 * so a noisier landmark set costs precision rather than correctness. It remains
 * worth doing for accuracy and is listed as future work rather than quietly
 * dropped — see `ROLL_PREROTATION` below.
 */

import { pickFace } from './pick-face.js';

export const DEFAULT_NUM_FACES = 2;

/**
 * MediaPipe's three confidence gates, or null to leave the library's own
 * defaults (0.5) alone — which is what ships.
 *
 * The research plan's rank 7: lowering presence and tracking confidence to
 * ~0.3 should make the landmarker hold its track through a deep tilt instead
 * of dropping below threshold and re-running the face DETECTOR, whose fresh
 * landmarks arrive in a slightly different place. That re-detection is
 * visible as a pop.
 *
 * **It ships dark, and the reason is that nothing here can measure it.** The
 * plan's own condition was "ship only if a replay measures a win", and the
 * instrument that would decide it does not exist in this tree: the detector
 * needs a browser and a face, the headless harness feeds `sigmaPx` straight
 * from its own noise model, and ar_v2 has no telemetry replay. Lowering the
 * gates is not free either — a looser presence threshold is more willing to
 * believe in a face that is not there, and `pickFace` can only choose among
 * what it is given. So the knob exists, costs nothing at its default, and
 * waits for the one instrument that could settle it: a wearer who reports
 * pops at tilt, sets `?confidence=0.3`, and says whether they stop.
 */
export interface DetectorConfidence {
  detection: number;
  presence: number;
  tracking: number;
}

/**
 * Long side of the image actually handed to the detector, px.
 *
 * MediaPipe's detector runs at 192x192 and its mesh on a 256x256 crop whatever
 * it is given; everything above that is resampled away inside the model. v1
 * measured 640 as costing 0.42 mm of landmark disagreement for 61% fewer pixels
 * to copy, transfer and upload.
 */
export const DETECT_LONG_SIDE = 640;

/**
 * Roll pre-rotation: implemented in v1, not ported here.
 *
 * Kept as a named constant so its absence is a decision rather than an
 * oversight. The reasoning for deferring it: in v1 a rolled head degraded the
 * landmarks, the landmarks degraded the shape estimate, and the shape estimate
 * degraded the fit — so roll was a correctness problem. Here shape is frozen
 * before tracking starts, so a rolled head costs pose precision only, and pose
 * precision degrades gracefully. It is still worth roughly a factor of two on
 * landmark noise past 40 degrees and should be added.
 */
export const ROLL_PREROTATION = false;

export interface DetectorResult {
  /** Landmarks in pixels of the SOURCE image, 2 per landmark. NaN if absent. */
  landmarks: Float64Array;
  /** Number of landmarks, 478 for MediaPipe with iris refinement. */
  count: number;
  /** Wall-clock cost of the inference, ms. */
  inferenceMs: number;
}

export interface Detector {
  readonly name: string;
  readonly landmarkCount: number;
  /** True when the detector reports its own per-landmark uncertainty. */
  readonly reportsUncertainty: boolean;
  detect(source: unknown, timestampMs: number, width: number, height: number): DetectorResult | null;
  close(): void;
}

/**
 * Creates the MediaPipe detector.
 *
 * `vision` is the vendored `vision_bundle.mjs` module object, passed in rather
 * than imported, so that this file has no import that only resolves in a
 * browser — which is what lets the whole `detect/` tree be type-checked and
 * unit-tested under Node.
 */
export async function createMediaPipeDetector(
  vision: {
    FilesetResolver: { forVisionTasks(dir: string): Promise<unknown> };
    FaceLandmarker: {
      createFromOptions(fileset: unknown, options: unknown): Promise<MediaPipeLandmarker>;
    };
  },
  wasmDir: string,
  modelUrl: string,
  options: {
    numFaces?: number;
    onStatus?: (text: string) => void;
    confidence?: DetectorConfidence | null;
  } = {},
): Promise<Detector> {
  const numFaces = options.numFaces ?? DEFAULT_NUM_FACES;
  const onStatus = options.onStatus ?? (() => {});
  const confidence = options.confidence ?? null;

  onStatus('loading the vision runtime');
  const fileset = await vision.FilesetResolver.forVisionTasks(wasmDir);

  const build = (delegate: 'GPU' | 'CPU') => ({
    baseOptions: { modelAssetPath: modelUrl, delegate },
    runningMode: 'VIDEO',
    numFaces,
    outputFaceBlendshapes: false,
    // Not read any more, and that is the headline difference from v1. The
    // facial transformation matrix is a rigid similarity fit of the AVERAGE head
    // to the landmarks; v2 solves pose against the wearer's own geometry
    // instead, which is why it does not lose accuracy at large yaw. Asking for
    // it would cost inference time for a number nothing consumes.
    outputFacialTransformationMatrixes: false,
    // Spread rather than always-present, so the default build is byte-for-byte
    // the options object this file has always sent and nothing about the
    // shipped detector moves. See DetectorConfidence.
    ...(confidence ? {
      minFaceDetectionConfidence: confidence.detection,
      minFacePresenceConfidence: confidence.presence,
      minTrackingConfidence: confidence.tracking,
    } : {}),
  });

  let landmarker: MediaPipeLandmarker;
  try {
    onStatus('loading the landmarker (GPU)');
    landmarker = await vision.FaceLandmarker.createFromOptions(fileset, build('GPU'));
  } catch (gpuError) {
    console.warn('GPU delegate unavailable, falling back to CPU', gpuError);
    onStatus('GPU unavailable — falling back to CPU');
    landmarker = await vision.FaceLandmarker.createFromOptions(fileset, build('CPU'));
  }

  let lastTimestamp = -1;

  return {
    name: 'mediapipe-face-landmarker',
    landmarkCount: 478,
    reportsUncertainty: false,

    detect(source, timestampMs, width, height) {
      // VIDEO mode uses the timestamp to decide whether a frame is new and
      // silently returns the previous result if it is not. A paused or
      // re-seeked source can repeat one, so nudge rather than drop.
      if (timestampMs <= lastTimestamp) timestampMs = lastTimestamp + 1;
      lastTimestamp = timestampMs;

      const started = now();
      const result = landmarker.detectForVideo(source as never, timestampMs);
      const inferenceMs = now() - started;

      const index = pickFace(result.faceLandmarks);
      if (index < 0) return null;
      const points = result.faceLandmarks[index];
      if (!points || points.length === 0) return null;

      // Normalised to pixels, once, here. Nothing downstream ever sees
      // normalised coordinates — they are the reason v1 had to explain, in two
      // separate files, that a distance taken in normalised units is stretched
      // along whichever axis it happens to lie.
      const landmarks = new Float64Array(points.length * 2);
      for (let i = 0; i < points.length; i++) {
        landmarks[i * 2] = points[i].x * width;
        landmarks[i * 2 + 1] = points[i].y * height;
      }
      return { landmarks, count: points.length, inferenceMs };
    },

    close() { landmarker.close(); },
  };
}

interface MediaPipeLandmarker {
  detectForVideo(source: unknown, timestampMs: number): {
    faceLandmarks: { x: number; y: number; z: number }[][];
  };
  close(): void;
}

const now = (): number =>
  (typeof performance !== 'undefined' ? performance.now() : Date.now());
