/**
 * The browser shell: the animation loop, the camera, the controls, the readouts.
 *
 * This file owns everything that needs a browser and nothing that does not. All
 * the arithmetic lives under `core/`, `enroll/`, `track/` and `fit/`, which run
 * headless in Node and are enforced to stay that way by
 * `scripts/check-isolation.mjs`. v1 maintained the same split by discipline;
 * here it is a build step that fails.
 *
 * The session has three phases and they are genuinely different programs:
 *
 *   1. **Acquire** — find a face at all, using the template. Milliseconds.
 *   2. **Scan** — run the guided protocol, collect frames, solve one bundle.
 *      Four seconds of the wearer's time, a second or two of solve.
 *   3. **Wear** — track pose against the scanned model and render. This phase
 *      re-solves nothing: the seat is a cached transform and the per-frame path
 *      is six numbers.
 *
 * A wearer who declines the scan gets phase 3 against the template, everything
 * marked as an estimate. That is v1's normal operating mode, kept as v2's
 * fallback.
 */

import { parseFaceObj, standardRegions, type FaceMesh } from '../core/mesh.js';
import { buildAnthropometricBasis } from '../core/shape/anthropometric.js';
import type { ShapeBasis } from '../core/shape/basis.js';
import {
  intrinsicsFromFov, MEDIAPIPE_ASSUMED_VERTICAL_FOV, type Intrinsics,
} from '../core/camera.js';
import { poseIdentity, type Pose } from '../core/linalg.js';
import {
  createFaceModel, deserializeFaceModel, serializeFaceModel, type FaceModel,
} from '../core/facemodel.js';
import {
  advanceProtocol, createProtocol, sampleFromPose, summarise, type ProtocolState,
} from '../enroll/protocol.js';
import type { BundleFrame } from '../enroll/bundle.js';
import { createTracker, track, type TrackerState } from '../track/tracker.js';
import { solvePnP, buildCorrespondences } from '../track/pnp.js';
import { createUncertainty, estimateSigma, acquisitionSigma } from '../detect/uncertainty.js';
import { createMediaPipeDetector, DETECT_LONG_SIDE, type Detector } from '../detect/mediapipe.js';
import { solveSeat, type SeatResult } from '../fit/contact.js';
import { assessFit, rankCatalogue, type FitAssessment } from '../fit/advice.js';
import { TEST_FRAMES, type FrameAsset } from '../fit/frame-asset.js';
import { applySeat, createScene, type SceneHandle } from '../render/scene.js';
import { createFrameLock, detectToSourceScale, scaleLandmarksToSource, type FrameLock } from './framelock.js';
import { createCameraSource, createStillSource, type Source } from './sources.js';
import { createUI, type UI } from './ui.js';
import { createEnrollClient, type EnrollClient } from './enroll-client.js';

type Phase = 'boot' | 'acquire' | 'scan' | 'solving' | 'wear' | 'error';

interface App {
  phase: Phase;
  mesh: FaceMesh;
  basis: ShapeBasis;
  regions: Record<string, ReturnType<typeof standardRegions>[string]>;
  scene: SceneHandle;
  lock: FrameLock;
  ui: UI;
  detector: Detector;
  enroller: EnrollClient;
  source: Source | null;
  intrinsics: Intrinsics;
  uncertainty: ReturnType<typeof createUncertainty>;
  tracker: TrackerState | null;
  model: FaceModel | null;
  seat: SeatResult | null;
  assessment: FitAssessment | null;
  frame: FrameAsset;
  protocol: ProtocolState;
  /** Frames collected during the scan, awaiting the bundle. */
  collected: Omit<BundleFrame, 'pose'>[];
  lastPose: Pose | null;
  referenceDistance: number | null;
  busy: boolean;
  fps: number;
  lastRenderMs: number;
  /** Which clock is driving the loop. Reported, because a timer is a worse
   *  clock than the display's and a reviewer should be able to see which one
   *  produced a measurement. */
  loopDriver: 'raf' | 'timer';
}

const STORAGE_KEY = 'ar-v2.facemodel';

/**
 * Where the served assets live, relative to the PAGE rather than to this module.
 *
 * `new URL('../../assets/x', import.meta.url)` is the idiom v1 used and it is
 * wrong here, because v1 served its source directly while v2 serves compiled
 * output: this module lives at `/dist/src/app/main.js`, so counting two
 * directories up lands on `/dist/assets/` and every asset 404s.
 *
 * Counting three instead would work and would break again the moment the output
 * layout changed. Assets are served from the site root, so they are resolved
 * from the document — which is a statement about deployment rather than about
 * where this file happens to sit.
 */
const asset = (path: string): string => new URL(path, document.baseURI).href;

async function boot(): Promise<void> {
  const canvas = document.getElementById('stage') as HTMLCanvasElement;
  const ui = createUI(document.body);
  ui.status('loading the face template');

  const meshText = await fetch(asset('assets/face/canonical_face_model.obj'))
    .then((r) => {
      if (!r.ok) throw new Error(`face template: HTTP ${r.status}`);
      return r.text();
    });
  const mesh = parseFaceObj(meshText);
  const basis = buildAnthropometricBasis(mesh);
  const regions = standardRegions(mesh);

  ui.status('starting the renderer');
  const scene = await createScene(canvas);
  ui.status(`renderer: ${scene.backendName}`);

  ui.status('loading the landmark detector');
  const vision = await import(/* @vite-ignore */ asset('vendor/mediapipe/vision_bundle.mjs')) as any;
  const detector = await createMediaPipeDetector(
    vision,
    asset('vendor/mediapipe/wasm'),
    asset('assets/models/face_landmarker.task'),
    { onStatus: (t) => ui.status(t) },
  );

  const lock = createFrameLock({ detectLongSide: DETECT_LONG_SIDE });

  // Started now rather than when the scan finishes: the worker spends ~40 ms
  // rebuilding the template and the basis, and it can do that while the wearer
  // is still being asked to look at the camera.
  const enroller = await createEnrollClient(
    asset('dist/src/enroll/enroll.worker.js'),
    asset('assets/face/canonical_face_model.obj'),
    { mesh, basis, regions },
    (m) => console.info('[enroll]', m),
  );
  ui.status(enroller.available ? 'ready' : 'ready (solving on the main thread)');

  const app: App = {
    phase: 'boot',
    mesh, basis, regions, scene, lock, ui, detector, enroller,
    source: null,
    intrinsics: intrinsicsFromFov(1280, 720, MEDIAPIPE_ASSUMED_VERTICAL_FOV),
    uncertainty: createUncertainty(mesh.vertexCount),
    tracker: null,
    model: null,
    seat: null,
    assessment: null,
    frame: TEST_FRAMES[1],
    protocol: createProtocol(),
    collected: [],
    lastPose: null,
    referenceDistance: null,
    busy: false,
    fps: 0,
    lastRenderMs: 0,
    loopDriver: 'raf',
  };

  (globalThis as any).__ar = app;

  ui.onAction((action) => handleAction(app, action));

  // A stored model means a returning wearer skips the scan entirely — which is
  // the whole point of the model being one immutable, serialisable object.
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored) {
    try {
      app.model = deserializeFaceModel(stored);
      ui.status('using your saved measurements');
    } catch (error) {
      console.warn('stored face model could not be read; re-scanning', error);
      localStorage.removeItem(STORAGE_KEY);
    }
  }

  await startSource(app);
  app.phase = app.model ? 'wear' : 'acquire';
  if (app.model) adoptModel(app, app.model);

  startLoop(app);
}

/**
 * The animation loop, with a watchdog.
 *
 * `requestAnimationFrame` is the right driver — it is the only one synchronised
 * to the display — but there are real environments where it never fires at all:
 * a backgrounded tab, an embedded webview, and (found the hard way) the
 * automation browser this was verified in, where rAF simply does not run and the
 * app sat in `acquire` forever with an fps of zero while every component
 * underneath it worked perfectly.
 *
 * A silent stall is the worst failure shape available: nothing errors, nothing
 * logs, and the picture is just frozen. So the loop watches itself, and falls
 * back to a timer if rAF has not fired within a second. The fallback is a worse
 * clock and says so in the readouts; it is not a worse app.
 */
function startLoop(app: App): void {
  let usingTimer = false;
  let lastTick = performance.now();

  const step = (t: number) => {
    lastTick = performance.now();
    tick(app, t);
    if (!usingTimer) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);

  const watchdog = setInterval(() => {
    if (usingTimer) return;
    if (performance.now() - lastTick < 1000) return;
    usingTimer = true;
    clearInterval(watchdog);
    console.warn(
      'requestAnimationFrame is not firing in this environment — ' +
      'falling back to a timer. Pacing will be worse than the display can offer.',
    );
    app.loopDriver = 'timer';
    setInterval(() => tick(app, performance.now()), 1000 / 60);
  }, 500);
}

async function startSource(app: App): Promise<void> {
  app.lock.nextEpoch();
  try {
    app.source = await createCameraSource({
      onLost: () => {
        app.ui.status('the camera went away');
        app.phase = 'error';
      },
    });
  } catch (error) {
    // No camera on this machine — which is the normal case on the build box.
    // Fall through to a still, and say so rather than showing a dead canvas.
    console.warn('camera unavailable, using a still image', error);
    app.ui.status('no camera — running on a sample image');
    try {
      app.source = await createStillSource(asset('assets/samples/face-a.jpg'));
    } catch {
      app.ui.status('no camera and no sample image available');
      app.phase = 'error';
      return;
    }
  }

  app.lock.resize(app.source.width, app.source.height);
  app.scene.setBackgroundSource(app.lock.display);
  app.scene.setSize(app.source.width, app.source.height);
  app.intrinsics = app.model?.intrinsics
    ?? intrinsicsFromFov(app.source.width, app.source.height, MEDIAPIPE_ASSUMED_VERTICAL_FOV);
  app.scene.applyIntrinsics(app.intrinsics);
}

function tick(app: App, nowMs: number): void {
  requestAnimationFrame((t) => tick(app, t));
  if (app.phase === 'error' || !app.source) return;

  const dt = app.lastRenderMs ? (nowMs - app.lastRenderMs) / 1000 : 1 / 60;
  app.lastRenderMs = nowMs;
  app.fps += (1 / Math.max(dt, 1e-4) - app.fps) * 0.1;

  const offer = app.source.nextFrame(nowMs);
  if (offer && !app.busy) {
    app.busy = true;
    const frame = app.lock.submit(
      offer.source, offer.capturedAtMs, offer.timestampMs, offer.measuredCapture,
    );

    // The detector is synchronous here. A worker is the right home for it and is
    // the obvious next step; the frame lock is already shaped for it, since it
    // drops rather than queues.
    const result = app.detector.detect(
      app.lock.detect, frame.timestampMs, app.lock.detect.width, app.lock.detect.height,
    );
    app.busy = false;

    if (app.lock.present(frame)) app.scene.markBackgroundDirty();
    onDetection(app, result, frame.captureDt);
  }

  app.scene.render();
  renderReadouts(app);
}

function onDetection(
  app: App, result: ReturnType<Detector['detect']>, captureDt: number,
): void {
  if (!result) {
    app.scene.setHeadPose(null);
    app.ui.tracked(false);
    return;
  }

  const scale = detectToSourceScale(app.lock);
  const landmarks = scaleLandmarksToSource(result.landmarks, scale);

  const geometry = app.model?.positions ?? app.mesh.positions;
  const { sigmaPx } = app.lastPose
    ? estimateSigma(app.uncertainty, {
      landmarks, mesh: app.mesh, positions: geometry,
      intrinsics: app.intrinsics, pose: app.lastPose,
    })
    : { sigmaPx: acquisitionSigma(app.mesh.vertexCount) };

  switch (app.phase) {
    case 'acquire':
    case 'scan': {
      // During acquisition and the scan, pose is solved against the TEMPLATE.
      // That is fine and it is the point: the scan does not need an accurate
      // pose, it needs an initialisation the bundle can improve on.
      const correspondences = buildCorrespondences(
        landmarks, sigmaPx, app.mesh.vertexCount, undefined, 12,
      );
      if (correspondences.length < 40) { app.ui.tracked(false); return; }
      const solved = solvePnP(app.mesh.positions, correspondences, app.intrinsics, app.lastPose ?? undefined);
      app.lastPose = solved.pose;
      app.scene.setHeadPose(solved.pose);
      app.ui.tracked(true);

      if (app.phase === 'acquire') {
        app.phase = 'scan';
        app.ui.status('hold still for a moment');
      }

      if (app.referenceDistance === null) app.referenceDistance = solved.pose.t[2];
      const step = advanceProtocol(
        app.protocol, sampleFromPose(solved.pose, app.referenceDistance),
      );
      app.ui.guide(step);

      collectFrame(app, landmarks, sigmaPx, step.beat?.id ?? 'done');

      if (step.finished) void runEnrollment(app);
      return;
    }

    case 'wear': {
      if (!app.tracker) return;
      const tracked = track(app.tracker, {
        landmarks, sigmaPx, intrinsics: app.intrinsics, dt: captureDt,
      });
      app.lastPose = tracked.rawPose ?? app.lastPose;
      app.scene.setHeadPose(tracked.pose);
      app.ui.tracked(tracked.tracked, tracked.reason ?? undefined);
      return;
    }

    default:
      return;
  }
}

/**
 * How many frames the scan hands to `enroll()`.
 *
 * The keyframe selector already picks 48 of whatever it is given, so collecting
 * more than this buys nothing — but it is not free, because `enroll()`
 * initialises a pose for *every* frame before selecting. A scan that ran long
 * (or a still-image session, which runs faster than real time) collected 1,121
 * frames and spent twenty seconds on PnP for 48 of them, with the wearer looking
 * at "working out your measurements".
 *
 * 240 is about eight seconds of real capture, which is twice the protocol's
 * length — enough slack for a wearer who takes their time, and a fifth of a
 * second of initialisation.
 */
const COLLECT_BUDGET = 240;

/**
 * Adds a frame, halving the set when it overflows.
 *
 * Halving rather than dropping the oldest: the value of this set is that it
 * SPANS the poses the wearer visited, and first-in-first-out eviction would
 * throw away the beginning of the scan — which is the frontal anchor the bundle's
 * gauge rests on. Halving keeps the temporal spread and therefore the pose
 * spread. Same instinct as the weight-aware eviction v1 eventually needed for
 * its anchor window, for the same reason.
 */
function collectFrame(
  app: App, landmarks: Float64Array, sigmaPx: Float64Array, beat: string,
): void {
  app.collected.push({
    landmarks: new Float64Array(landmarks),
    sigmaPx: new Float64Array(sigmaPx),
    visibility: new Float64Array(app.mesh.vertexCount).fill(1),
    silhouette: null,
    beat,
  });
  if (app.collected.length > COLLECT_BUDGET) {
    app.collected = app.collected.filter((_, i) => i % 2 === 0);
  }
}

async function runEnrollment(app: App): Promise<void> {
  app.phase = 'solving';
  app.ui.status('working out your measurements…');
  app.ui.guide(null);

  // A single yield, so the message paints before anything heavy starts.
  //
  // **Not via `requestAnimationFrame`.** The obvious way to wait for a paint is
  // to await one, and it deadlocks in exactly the environments `startLoop`'s
  // watchdog exists for: where rAF never fires, this promise never settles and
  // the app sits on "working out your measurements" forever while every
  // component underneath it is fine. Found by doing it, three lines after
  // writing the comment explaining that rAF can be dead.
  await new Promise((r) => setTimeout(r, 0));

  try {
    // Off the main thread when a worker is available, which is the normal case.
    // The frames are transferred, so `app.collected` is emptied by the handoff
    // and must not be read afterwards.
    const frames = app.collected;
    app.collected = [];
    const result = await app.enroller.run({
      frames,
      imageWidth: app.source!.width,
      imageHeight: app.source!.height,
    });
    console.info('[enroll] coverage', result.coverage, summarise(app.protocol), `on the ${result.ranOn} thread`);
    adoptModel(app, result.model);
    localStorage.setItem(STORAGE_KEY, serializeFaceModel(result.model));
    app.ui.status(
      result.model.degraded
        ? `measured, with caveats: ${result.model.notes.join('; ')}`
        : `measured in ${result.model.solveMs.toFixed(0)} ms`,
    );
  } catch (error) {
    console.error('enrollment failed', error);
    app.ui.status('the scan did not work — using average measurements');
    adoptModel(app, templateModel(app));
  }
  app.collected = [];
}

function templateModel(app: App): FaceModel {
  return createFaceModel({
    positions: new Float64Array(app.mesh.positions),
    vertexSigmaMm: new Float64Array(app.mesh.vertexCount).fill(8),
    shapeCoeffs: new Float64Array(app.basis.dim),
    basisName: app.basis.name,
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: app.intrinsics, intrinsicsSolved: false,
    scale: { source: 'assumed', factor: 1, sigma: 0.05, note: 'no scan — average face' },
    landmarkBiasMm: new Float64Array(app.mesh.vertexCount * 3),
    quality: {}, pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: NaN, framesUsed: 0, solveMs: 0, degraded: true,
    notes: ['no scan was taken — every number here is the average face'],
  });
}

function adoptModel(app: App, model: FaceModel): void {
  app.model = model;
  app.tracker = createTracker(model);
  app.uncertainty = createUncertainty(app.mesh.vertexCount);
  app.intrinsics = model.intrinsicsSolved
    ? model.intrinsics
    : intrinsicsFromFov(app.source!.width, app.source!.height, MEDIAPIPE_ASSUMED_VERTICAL_FOV);
  app.scene.applyIntrinsics(app.intrinsics);
  fitFrame(app, app.frame);
  app.phase = 'wear';
}

/** Solves the seat ONCE and caches it on the scene graph. */
function fitFrame(app: App, frame: FrameAsset): void {
  if (!app.model) return;
  app.frame = frame;
  const seat = solveSeat(app.model, app.mesh, app.regions, frame);
  app.seat = seat;
  applySeat(app.scene, seat.pose);
  app.assessment = assessFit(app.model, app.mesh, app.regions, frame, seat);
  app.ui.verdicts(app.assessment);
}

function handleAction(app: App, action: string): void {
  switch (action) {
    case 'rescan':
      localStorage.removeItem(STORAGE_KEY);
      app.protocol = createProtocol();
      app.collected = [];
      app.referenceDistance = null;
      app.model = null;
      app.tracker = null;
      app.phase = 'acquire';
      app.ui.status('starting again');
      break;
    case 'rank': {
      if (!app.model) return;
      const ranked = rankCatalogue(app.model, app.mesh, app.regions, TEST_FRAMES);
      app.ui.catalogue(ranked);
      break;
    }
    case 'forget':
      localStorage.removeItem(STORAGE_KEY);
      app.ui.status('your measurements have been deleted from this device');
      break;
    default:
      if (action.startsWith('frame:')) {
        const id = action.slice('frame:'.length);
        const frame = TEST_FRAMES.find((f) => f.id === id);
        if (frame) fitFrame(app, frame);
      }
  }
}

function renderReadouts(app: App): void {
  app.ui.readouts({
    fps: app.fps,
    mirrorDelayMs: app.lock.mirrorDelayMs,
    droppedFrames: app.source?.droppedFrames ?? 0,
    backend: app.scene.backendName
      + (app.loopDriver === 'timer' ? ' · timer loop' : '')
      + (app.enroller.available ? '' : ' · inline solve'),
    phase: app.phase,
    model: app.model,
    seat: app.seat,
  });
}

boot().catch((error) => {
  console.error(error);
  const el = document.getElementById('status');
  if (el) el.textContent = String(error?.message ?? error);
});

export { boot, poseIdentity };
