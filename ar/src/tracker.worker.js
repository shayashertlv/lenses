/**
 * The landmarker, running where it cannot stall a render: in a worker.
 *
 * The main thread hands over an `ImageBitmap` and gets back landmarks and the
 * facial transformation matrix. `detectForVideo` is synchronous and costs ~20 ms
 * of inference — run on the main thread that is a stall inside the render loop on
 * every camera frame, which reads as stutter no matter how good the pose is. Here
 * it blocks nobody; the pose estimator carries the head between results, and the
 * latency compensation already reasons from *capture* time, so the async hop adds
 * no visible lag of its own.
 *
 * Uses the `vision_wasm_module_internal` build of the runtime, which is the one
 * made for this: it registers `ModuleFactory` on the global itself and carries an
 * ES export, so a module worker can load it with a plain dynamic import. The
 * classic build leans on `importScripts`, which module workers do not have.
 */

import { FaceLandmarker } from '../vendor/mediapipe/vision_bundle.mjs';
import { pickFace } from './pick-face.js';

const FILESET = {
  wasmLoaderPath: new URL('../vendor/mediapipe/wasm/vision_wasm_module_internal.js', import.meta.url).href,
  wasmBinaryPath: new URL('../vendor/mediapipe/wasm/vision_wasm_module_internal.wasm', import.meta.url).href,
};
const MODEL_URL = new URL('../assets/models/face_landmarker.task', import.meta.url).href;

/**
 * The canvas the GPU delegate runs on, with its context created *here* so the power
 * preference is ours to state — left alone, MediaPipe asks for plain 'webgl2', which
 * on a dual-GPU machine is allowed to land on the power-saving GPU. Creating the
 * context first makes MediaPipe's own getContext call return this one.
 *
 * Measured honestly: on the machine this shipped on it bought 38 → 34 ms, so that
 * machine's worker-vs-main gap (34 against 14) is not GPU selection — it is what a
 * worker's GL context costs there, and the fallback policy owns that trade. Kept
 * because it is free, correct on the machines it does help, and stating the
 * preference is right regardless.
 */
const gpuCanvas = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(64, 64) : undefined;
gpuCanvas?.getContext('webgl2', { powerPreference: 'high-performance' });

/** Set by the `init` message. See `DEFAULT_NUM_FACES` in `tracker.js`. */
let numFaces = 1;

const optionsFor = (delegate) => ({
  baseOptions: { modelAssetPath: MODEL_URL, delegate },
  canvas: delegate === 'GPU' ? gpuCanvas : undefined,
  runningMode: 'VIDEO',
  numFaces,
  outputFaceBlendshapes: false,
  outputFacialTransformationMatrixes: true,
});

let landmarker = null;
let lastTimestamp = -1;

self.onmessage = async (event) => {
  const message = event.data;

  if (message.type === 'init') {
    try {
      if (message.numFaces > 0) numFaces = message.numFaces;
      self.postMessage({ type: 'status', text: 'loading the landmarker in a worker' });
      let delegate = 'GPU';
      try {
        landmarker = await FaceLandmarker.createFromOptions(FILESET, optionsFor('GPU'));
      } catch (gpuError) {
        // Same degradation the main-thread tracker makes, for the same reason.
        console.warn('worker GPU delegate unavailable, falling back to CPU', gpuError);
        delegate = 'CPU';
        landmarker = await FaceLandmarker.createFromOptions(FILESET, optionsFor('CPU'));
      }
      self.postMessage({ type: 'ready', delegate });
    } catch (error) {
      self.postMessage({ type: 'init-error', message: String(error?.message ?? error) });
    }
    return;
  }

  if (message.type === 'detect') {
    const { bitmap, frame } = message;

    // VIDEO mode demands strictly increasing timestamps; nudge rather than drop,
    // exactly as the main-thread tracker does.
    let timestampMs = frame.timestampMs;
    if (timestampMs <= lastTimestamp) timestampMs = lastTimestamp + 1;
    lastTimestamp = timestampMs;

    const started = performance.now();
    let result = null;
    try {
      result = landmarker.detectForVideo(bitmap, timestampMs);
    } catch (error) {
      bitmap.close();
      self.postMessage({ type: 'result', ok: false, frame, inferMs: performance.now() - started });
      return;
    }
    bitmap.close();
    const inferMs = performance.now() - started;

    // Chosen here rather than on the main thread, so only the wearer's landmarks
    // cross the hop. See `pickFace` — with MediaPipe's smoothing off there can be two
    // faces in the result and their order is not promised.
    const index = pickFace(result.faceLandmarks);
    const landmarks = index < 0 ? null : result.faceLandmarks[index];
    const matrix = index < 0 ? null : result.facialTransformationMatrixes?.[index];
    if (!landmarks || !matrix) {
      self.postMessage({ type: 'result', ok: false, frame, inferMs });
      return;
    }

    // Flat arrays, transferred rather than copied — 478 landmark objects would be
    // structured-cloned per frame; two ArrayBuffers move for free.
    const points = new Float32Array(landmarks.length * 3);
    for (let i = 0; i < landmarks.length; i++) {
      points[i * 3] = landmarks[i].x;
      points[i * 3 + 1] = landmarks[i].y;
      points[i * 3 + 2] = landmarks[i].z;
    }
    const pose = new Float32Array(matrix.data);

    self.postMessage(
      { type: 'result', ok: true, frame, inferMs, points: points.buffer, pose: pose.buffer },
      [points.buffer, pose.buffer],
    );
  }
};
