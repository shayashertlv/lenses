/**
 * Face tracking, wrapping MediaPipe's FaceLandmarker.
 *
 * Two outputs matter downstream:
 *
 *  - `matrix`: a 4x4 mapping the canonical face model into metric camera space.
 *    This is the whole ballgame. It is a similarity transform — rotation,
 *    translation and one uniform scale — solved so the canonical head lands on
 *    the head in frame. Parent anything to it and it rides the real face.
 *
 *  - `landmarks`: 478 points in normalised image space. We only use these for the
 *    debug overlay and for the quality signals below; the 3D placement never
 *    touches them, because image-space landmarks carry no reliable depth.
 *
 * Everything runs local — model and wasm are vendored, nothing is uploaded.
 */

import { FilesetResolver, FaceLandmarker } from '../vendor/mediapipe/vision_bundle.mjs';
import { pickFace } from './pick-face.js';

const WASM_DIR = new URL('../vendor/mediapipe/wasm', import.meta.url).href;
const MODEL_URL = new URL('../assets/models/face_landmarker.task', import.meta.url).href;

/**
 * How many faces the landmarker is asked for — which is really a smoothing switch.
 *
 * **Two, and the second one is never used.** MediaPipe filters its landmarks
 * internally when, and only when, this is 1: *"Smoothing is only applied when
 * num_faces is set to 1"*, from their own web guide. That put our pose filter second
 * in a chain of two, reading a signal whose velocity had already been damped by a
 * filter with no knobs, no readout, and no way to measure it from inside. Asking for
 * two faces is the documented way to turn it off, and doing so was worth an
 * immediately noticeable reduction in lag on a real head — larger than anything the
 * tuning below achieved.
 *
 * The extra face costs little: the detector stage runs once regardless, and the mesh
 * only runs per face actually found, so a session with one person in frame pays
 * essentially the same as before. What it does cost is the right to index `[0]` —
 * see `pickFace`.
 *
 * `?faces=1` restores MediaPipe's smoothing, which is the A/B this was decided on.
 */
export const DEFAULT_NUM_FACES = 2;

/**
 * The longest side, in pixels, of the image actually handed to the landmarker.
 *
 * A 720p camera frame costs three full-resolution copies on the way to a pose: the
 * snapshot the composite displays, the `ImageBitmap` handed to the worker, and the
 * texture upload inside it. Two of those three buy nothing, because **the landmarker
 * does not look at a 720p image**: MediaPipe's face detector runs at 192x192 and the
 * mesh on a 256x256 crop, whatever they are given. Everything above that is
 * resampled away inside the model.
 *
 * 640 keeps a comfortable margin over both — a face filling a third of the frame is
 * still ~210 px across before the mesh's own crop — while cutting the pixels that
 * have to be copied, transferred and uploaded by nearly 80%. The harness measures
 * what it costs in accuracy rather than assuming, on the same face at a spread of
 * scales; see `detection loses nothing worth having at reduced input resolution`.
 *
 * The *display* is unaffected. The snapshot canvas stays at the camera's own
 * resolution — this is the detection path only.
 */
export const DETECT_LONG_SIDE = 640;

export async function createTracker({ onStatus = () => {}, numFaces = DEFAULT_NUM_FACES } = {}) {
  onStatus('loading wasm runtime');
  const fileset = await FilesetResolver.forVisionTasks(WASM_DIR);

  onStatus('loading face landmarker');
  const options = (delegate) => ({
    baseOptions: { modelAssetPath: MODEL_URL, delegate },
    runningMode: 'VIDEO',
    numFaces,
    outputFaceBlendshapes: false,
    outputFacialTransformationMatrixes: true,
  });

  let landmarker;
  let delegate = 'GPU';
  try {
    landmarker = await FaceLandmarker.createFromOptions(fileset, options('GPU'));
  } catch (gpuError) {
    // Some drivers and remote desktop sessions have no usable WebGL2. CPU is
    // slower but the pipeline is identical, so degrade rather than fail.
    console.warn('GPU delegate unavailable, falling back to CPU', gpuError);
    onStatus('GPU unavailable — falling back to CPU');
    delegate = 'CPU';
    landmarker = await FaceLandmarker.createFromOptions(fileset, options('CPU'));
  }

  let lastTimestamp = -1;

  return {
    delegate,

    /**
     * Runs detection on one frame.
     *
     * `timestampMs` must strictly increase — the VIDEO running mode uses it to
     * decide whether a frame is new, and silently returns the previous result if
     * it is not. A paused or re-seeked source can hand us a repeated timestamp,
     * so nudge it forward rather than dropping the frame.
     *
     * Returns `null` when no face is in view.
     */
    detect(source, timestampMs) {
      if (timestampMs <= lastTimestamp) timestampMs = lastTimestamp + 1;
      lastTimestamp = timestampMs;

      const result = landmarker.detectForVideo(source, timestampMs);

      // Never `[0]`: with smoothing disabled the detector returns up to two faces and
      // does not promise an order. See `pickFace`.
      const index = pickFace(result.faceLandmarks);
      if (index < 0) return null;
      const landmarks = result.faceLandmarks[index];
      const matrix = result.facialTransformationMatrixes?.[index];
      if (!landmarks || !matrix) return null;

      return { landmarks, matrix: matrix.data };
    },

    close() {
      landmarker.close();
    },
  };
}

const WORKER_URL = new URL('./tracker.worker.js', import.meta.url);

/**
 * The tracker the app actually uses: inference in a worker when it earns its keep,
 * on the main thread when it does not — decided by measurement, not assumption.
 *
 * Why a worker at all: `detectForVideo` is synchronous and costs ~20 ms. Called
 * inside the render loop, that is a 20 ms stall on every camera frame — the frame
 * that was about to present is late, and the whole composite hiccups at the camera's
 * rate however well the pose itself is solved. In a worker the same call blocks
 * nobody: handing a frame over costs the main thread ~0.1 ms, and the pose estimator
 * already reasons from *capture* time, so the asynchronous hop adds no visible lag —
 * it only removes the stall. This is also the shape Google's own face-landmarker
 * samples ship now.
 *
 * Why not a worker unconditionally: a worker gets its own GL context, and on some
 * machines inference there genuinely costs more — 34 ms against 14 ms on the machine
 * this shipped on, GPU delegate both sides. That is a *rate* cost, not a smoothness
 * cost, and the fallback policy weighs it as one: the worker is abandoned only when
 * it cannot keep near the camera's rate at all, because a slow worker still renders
 * every frame on time while a fast main-thread tracker stalls the render loop on
 * every single detection.
 *
 * One result is in flight at a time; frames that arrive while busy are dropped and
 * the estimator covers the gap, which is the same policy a slow detector already
 * forced and the prediction was built for.
 */
export async function createTrackerClient({
  preferWorker = true,
  /** 'worker' | 'main' | null — pins the mode and disables the fallback. */
  lockMode = null,
  /** See `DEFAULT_NUM_FACES` — 2 disables MediaPipe's own landmark smoothing. */
  numFaces = DEFAULT_NUM_FACES,
  onStatus = () => {},
  onResult = () => {},
  onModeChange = () => {},
} = {}) {
  let mode = null;
  let worker = null;
  let mainTracker = null;
  let busy = false;
  let closed = false;

  /** EMA of the worker's own inference time, ms — for the readout. */
  let inferMs = null;
  /** EMA of the camera's frame interval, ms — reported in by the loop. */
  let cameraMs = null;
  let results = 0;
  let fellBack = false;
  /**
   * Recent worker inference times, for the fallback decision — and only the recent
   * ones, which is the point. The first inferences through a fresh GPU context
   * include shader compilation and are hundreds of milliseconds; folded into a
   * long-lived average they read as "the worker is slow" for far longer than the
   * warm-up lasts, and a one-way fallback then latches the wrong answer at every
   * startup. Measured on the machine this shipped on: the worker settled at ~20 ms
   * but the warm-up-poisoned average still read 80 ms at decision time, so every
   * session silently fell back to the main thread — and wore the 30 ms render
   * stall the worker exists to remove.
   */
  const WARMUP_RESULTS = 5;
  const DECIDE_AFTER = 25;
  /** And no decision inside the first seconds of wall clock, whatever the result
   * count says — a 60 fps source can deliver 25 results while the GPU is still
   * compiling shaders, and a decision that early measures the warm-up, not the
   * worker. */
  const DECIDE_AFTER_MS = 4000;
  const recentMs = [];
  let workerReadyAt = 0;

  const unpack = (message) => {
    if (!message.ok) return null;
    const flat = new Float32Array(message.points);
    const landmarks = new Array(flat.length / 3);
    for (let i = 0; i < landmarks.length; i++) {
      landmarks[i] = { x: flat[i * 3], y: flat[i * 3 + 1], z: flat[i * 3 + 2] };
    }
    return { landmarks, matrix: new Float32Array(message.pose) };
  };

  const startMain = async (reason) => {
    if (mainTracker || closed) return;
    const started = await createTracker({ onStatus, numFaces });
    if (closed) { started.close(); return; }
    mainTracker = started;
    mode = 'main';
    if (worker) { worker.terminate(); worker = null; }
    busy = false;
    onModeChange(mode, reason);
  };

  /**
   * The one-way fallback. Waits out the GPU warm-up, then compares what the worker
   * *currently* costs — the median of its recent inferences — against what the
   * camera delivers: a tracker slower than the camera halves the detection rate,
   * and nothing downstream can buy that back.
   *
   * Never decided while the tab is hidden. A background tab throttles the worker
   * and the camera both, in different ways, and a measurement taken there says
   * nothing about the machine — only about the throttling. The decision waits for
   * a visible tab or does not happen.
   */
  const maybeFallBack = () => {
    if (mode !== 'worker' || lockMode === 'worker' || fellBack) return;
    if (results < DECIDE_AFTER || recentMs.length < 10 || cameraMs === null) return;
    if (performance.now() - workerReadyAt < DECIDE_AFTER_MS) return;
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;

    const sorted = [...recentMs].sort((a, b) => a - b);
    const median = sorted[sorted.length >> 1];

    // Under the frame lock the display advances once per detection, so the mirror's
    // frame rate IS the tracker's rate — and the bar becomes simple: a worker that
    // cannot process every camera frame is showing the user a slower mirror than
    // the main thread would, because the main thread at any speed processes frames
    // back to back at whatever rate it manages, and when it is faster than the
    // camera (14 ms against a 33 ms interval, on the machine this shipped on) it
    // holds the full 30 fps that a 34 ms worker quantises down to 15. The stall a
    // main-thread inference causes lands between composites now, not inside them,
    // so it costs UI responsiveness only — a far cheaper coin than mirror rate.
    if (median <= cameraMs * 0.95) return;

    fellBack = true;
    const reason = `worker inference ${median.toFixed(0)} ms (median of the last `
      + `${recentMs.length}) against a ${cameraMs.toFixed(0)} ms camera interval`;
    console.warn(`tracker falling back to the main thread: ${reason}`);
    startMain(reason);
  };

  if (preferWorker && lockMode !== 'main' && typeof Worker !== 'undefined') {
    const started = await new Promise((resolve) => {
      let candidate;
      const fail = (why) => { candidate?.terminate(); resolve({ ok: false, why }); };
      try {
        candidate = new Worker(WORKER_URL, { type: 'module' });
      } catch (error) {
        resolve({ ok: false, why: String(error?.message ?? error) });
        return;
      }
      const timeout = setTimeout(() => fail('worker init timed out'), 30000);
      candidate.onerror = (event) => { clearTimeout(timeout); fail(event.message || 'worker error'); };
      candidate.onmessage = (event) => {
        const message = event.data;
        if (message.type === 'status') {
          onStatus(message.text);
        } else if (message.type === 'ready') {
          clearTimeout(timeout);
          resolve({ ok: true, candidate, delegate: message.delegate });
        } else if (message.type === 'init-error') {
          clearTimeout(timeout);
          fail(message.message);
        }
      };
      candidate.postMessage({ type: 'init', numFaces });
    });

    if (started.ok) {
      worker = started.candidate;
      mode = 'worker';
      workerReadyAt = performance.now();
      worker.onmessage = (event) => {
        const message = event.data;
        if (message.type === 'status') { onStatus(message.text); return; }
        if (message.type !== 'result' || closed) return;
        busy = false;
        results += 1;
        inferMs = inferMs === null
          ? message.inferMs
          : inferMs + (message.inferMs - inferMs) * 0.1;
        // The decision window opens only after the warm-up inferences have passed
        // through — they measure shader compilation, not the worker.
        if (results > WARMUP_RESULTS) {
          recentMs.push(message.inferMs);
          if (recentMs.length > 15) recentMs.shift();
        }
        maybeFallBack();
        onResult({
          detection: unpack(message), frame: message.frame, inferMs: message.inferMs,
          mode: 'worker',
        });
      };
      onModeChange(mode, started.delegate);
    } else {
      console.warn('worker tracker unavailable, using the main thread:', started.why);
    }
  }

  if (!mode) await startMain('worker unavailable');

  return {
    get mode() { return mode; },
    get busy() { return busy; },
    get inferMs() { return inferMs; },

    /** The camera's measured frame interval, for the fallback decision. The loop
     * hands in its own smoothed figure, so it is stored rather than re-filtered. */
    noteCameraInterval(ms) {
      cameraMs = ms;
    },

    /**
     * Offers one frame. Returns false when a previous frame is still in flight —
     * the caller simply tries again with the next camera frame.
     */
    submit(element, frame) {
      if (closed || busy) return false;

      if (mode === 'main') {
        const started = performance.now();
        let detection = null;
        try {
          detection = mainTracker.detect(element, frame.timestampMs);
        } catch (error) {
          console.error('detection failed', error);
        }
        const cost = performance.now() - started;
        inferMs = inferMs === null ? cost : inferMs + (cost - inferMs) * 0.1;
        onResult({ detection, frame, inferMs: cost, mode: 'main' });
        return true;
      }

      busy = true;
      // The copy out of the element happens here, on the capture side — an
      // ImageBitmap moves to the worker by transfer, so the pixels are not cloned.
      createImageBitmap(element).then((bitmap) => {
        if (closed || mode !== 'worker') { bitmap.close(); busy = false; return; }
        worker.postMessage({ type: 'detect', bitmap, frame }, [bitmap]);
      }).catch((error) => {
        busy = false;
        console.error('frame handoff failed', error);
      });
      return true;
    },

    close() {
      closed = true;
      worker?.terminate();
      worker = null;
      mainTracker?.close();
      mainTracker = null;
    },
  };
}

/**
 * How far the head is turned away from the camera, 0 (facing us) to 1 (profile).
 *
 * Measured in image space as the asymmetry between the two halves of the face:
 * the distance from each eye's outer corner to the centre of the nose bridge.
 * Turn away and the near half stretches while the far half foreshortens.
 *
 * Used to gate the auto-calibration, which is only trustworthy head-on.
 */
export function estimateYaw(landmarks) {
  const bridge = landmarks[6];
  const right = landmarks[33];
  const left = landmarks[263];

  const dR = Math.hypot(bridge.x - right.x, bridge.y - right.y);
  const dL = Math.hypot(bridge.x - left.x, bridge.y - left.y);
  const total = dR + dL;
  if (total < 1e-6) return 0;

  return Math.min(1, Math.abs(dR - dL) / total * 2);
}
