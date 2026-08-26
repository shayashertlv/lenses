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

import {
  parseFaceObj, silhouetteStrips, standardRegions, trackingRigidity, type FaceMesh,
} from '../core/mesh.js';
import { buildAnthropometricBasis } from '../core/shape/anthropometric.js';
import type { ShapeBasis } from '../core/shape/basis.js';
import {
  intrinsicsFromFov, MEDIAPIPE_ASSUMED_VERTICAL_FOV, type Intrinsics,
} from '../core/camera.js';
import { poseIdentity, type Pose } from '../core/linalg.js';
import {
  createFaceModel, deserializeFaceModel, serializeFaceModel, withScanRecord,
  type FaceModel,
} from '../core/facemodel.js';
import {
  advanceProtocol, createProtocol, sampleFromPose, scanRecord, summarise,
  type ProtocolState, type PoseSample,
} from '../enroll/protocol.js';
import type { BundleFrame } from '../enroll/bundle.js';
import { createTracker, track, type TrackerState, type TrackResult } from '../track/tracker.js';
import {
  armWearer, createIdentityWatch, forgetWearer, observeIdentity, qualifies,
  type IdentityWatch,
} from '../track/identity.js';
import { CalibrationField, occludingContour, snapOffsets, contourPushes } from '../track/snap.js';
import { createDepthBuffer, rasterize, type DepthBuffer } from '../core/raster.js';
import { solvePnP, buildCorrespondences } from '../track/pnp.js';
import {
  createUncertainty, estimateSigma, acquisitionSigma, UNCERTAINTY_DEFAULTS,
} from '../detect/uncertainty.js';
import { createMediaPipeDetector, DETECT_LONG_SIDE, type Detector } from '../detect/mediapipe.js';
import { earRestPoints, solveSeat, type SeatResult } from '../fit/contact.js';
import { serializeCapture, type Capture } from '../enroll/telemetry.js';
import { assessFit, rankCatalogue, type FitAssessment } from '../fit/score.js';
import { TEST_FRAMES, type FrameAsset } from '../fit/frame-asset.js';
import { CATALOGUE } from '../fit/catalogue.js';
import { frameFromMesh } from '../fit/frame-from-mesh.js';
import { readGlb } from '../fit/mesh-io.js';
import { loadFrameMesh } from '../render/frame-mesh.js';
import {
  CACHED_BY_CALLER, applySeat, attachFrame, createScene, detachFrame, type SceneHandle,
} from '../render/scene.js';
import { createFrameLock, detectToSourceScale, scaleLandmarksToSource, type FrameLock } from './framelock.js';
import { createCameraSource, createStillSource, type Source } from './sources.js';
import { createUI, type UI } from './ui.js';
import { createEnrollClient, type EnrollClient } from './enroll-client.js';
import { collectDiagnostics } from './diagnostics.js';

type Phase = 'boot' | 'acquire' | 'scan' | 'solving' | 'wear' | 'error';

interface App {
  phase: Phase;
  mesh: FaceMesh;
  basis: ShapeBasis;
  regions: Record<string, ReturnType<typeof standardRegions>[string]>;
  /** The wearer's own PD if they typed it in — a ruler measured on them rather
   *  than assumed from a population. Null until they do. */
  knownPdMm: number | null;
  scene: SceneHandle;
  lock: FrameLock;
  ui: UI;
  detector: Detector;
  enroller: EnrollClient;
  source: Source | null;
  intrinsics: Intrinsics;
  uncertainty: ReturnType<typeof createUncertainty>;
  /** Watches whether the face in front of the camera is still the one that was
   *  scanned. See `track/identity.ts` — it abstains until it has learned this
   *  wearer's own reading, and never convicts on a turned or occluded frame. */
  identity: IdentityWatch;
  tracker: TrackerState | null;
  /**
   * Which pose smoothing the tracker runs. The default has a history worth
   * keeping honest: OFF was the synthetic verdict ("every tuning worse than
   * none"); the first real wearer reported jiggle that grows with yaw, so
   * 'locked' (the stillness latch) shipped as default — and the same wearer
   * then reported it "stuck and choppy": the displacement-gated latch v1
   * engaged during slow real motion and cycled freeze/release. Default is now
   * `true` — the fixed One Euro, which that wearer judged acceptable — while
   * 'locked' carries latch v2 (velocity-gated, crossfaded) through the Steady
   * button's A/B. If v2 wins on their face, the default moves again and this
   * comment records why.
   */
  smooth: boolean | 'adaptive' | 'locked';
  /**
   * The edge snap — the invention the occlusion mandate demanded. When on,
   * every tracked frame searches the video for the REAL occluding edge along
   * the occluder's predicted contour and nudges the occluder's silhouette
   * vertices onto it (track/snap.ts). Confidence-gated: flat light degrades to
   * exactly the geometric occluder. The EMA lives here because the contour
   * resamples every frame but vertices persist.
   */
  edgeSnap: boolean;
  /** The convergent per-vertex correction — a property of THIS face, learned
   *  over the first second or two of wearing and then effectively frozen.
   *  See CalibrationField for why the per-frame EMA it replaced was wrong. */
  snapField: CalibrationField | null;
  snapFrame: number;
  snapBuffer: DepthBuffer | null;
  /** Last frame's snap health, for the readout: [samples, confident, medianAbsPx]. */
  snapStats: [number, number, number];
  /**
   * Rolling ring of per-frame tracking readouts (last ~10 s of real solves),
   * for the diagnostics panel. This is the instrument the latch thresholds
   * answer to: the wearer's paste carries the windowed-velocity distribution
   * their actual face and camera produce, which is the number the synthetic
   * sweep could only bracket.
   */
  trackStats: {
    velMmS: number; velDegS: number; sigmaMm: number; sigmaDeg: number;
    noiseVelMmS: number; noiseVelDegS: number;
    priorShareRot: number; priorShareMm: number; varianceFactor: number;
    latched: boolean; fading: boolean; lagMm: number;
  }[];
  /**
   * The rank-4 motion prior, and the A/B lever for it: `?prior=off` in the
   * URL turns it off for a reload, so the wearer can compare the same face,
   * the same light and the same scan with and without. Reported in the
   * diagnostics so a paste can never be ambiguous about which arm it is.
   */
  motionPrior: boolean;
  /**
   * Rank 6's landmark marching, OFF by default behind `?march=on`.
   *
   * The synthetic verdict is genuinely split — a large win where the oval
   * landmarks honestly mark the contour, a real loss where the detector
   * invents them — and no fixture here can say which describes a given
   * wearer's face. So it ships as an experiment the wearer can settle in a
   * minute rather than a default that bets on the friendly reading. Kept
   * separate from `?prior=off` so one rank is judged at a time, which is
   * the protocol every verdict in this tree has been taken under.
   */
  marchOval: boolean;
  /**
   * Q15/Q16's experiment knob, live: false = the shipped wall hook, true = the
   * physically derived 0.11 N/mm cantilever. The synthetic verdict kept the
   * wall; the first real wearer's seat over-closed exactly the way a wall
   * over-closes (vertex 5 mm, pads buried 1.9 mm), so their face runs the A/B
   * the harness could not. If soft wins on real faces, the default flips and
   * the ledger records that the synthetic population missed it.
   */
  softHook: boolean;
  model: FaceModel | null;
  seat: SeatResult | null;
  assessment: FitAssessment | null;
  frame: FrameAsset;
  /**
   * Mesh-backed frames that have finished loading, by id.
   *
   * Both halves are cached together because they must never be used apart: the
   * `asset` is what `solveSeat` fits, the `object` is what gets drawn, and the
   * object was placed by the asset's own `source.meshToFrame`. Loading is off
   * the boot path entirely — see `preloadCatalogue`.
   */
  meshFrames: Map<string, { asset: FrameAsset; object: any }>;
  /**
   * The frame id the wearer last asked for, or null if they have not asked.
   *
   * Last-click-wins. A GLB is a multi-megabyte download and `handleAction` is
   * synchronous and re-entrant, so two quick clicks start two loads and the
   * SLOWER one would otherwise attach last — the wearer ends up wearing the
   * frame they clicked first. It also decides whether the background preload
   * may install its result: if the wearer has already chosen, it may not.
   */
  wantedFrameId: string | null;
  protocol: ProtocolState;
  /**
   * Which scan the app is currently on. Bumped by anything that abandons the
   * scan in progress — today that is the rescan button.
   *
   * `runEnrollment` suspends for a second or more inside `enroller.run`, and
   * `handleAction` is free to run during that await: `case 'rescan'` REPLACES
   * `app.protocol`, empties `app.collected` and sets `phase = 'acquire'`. The
   * continuation then resumed against the new objects and undid all three —
   * it adopted the OLD model over the fresh `acquire` phase, stamped it with
   * the RESTARTED protocol's scan record ("in progress, 1 of 7 done"), stored
   * that to localStorage, and its tail `app.collected = []` threw away the
   * frames the restarted scan had already gathered. Comparing this counter
   * across the await is what makes a resumed continuation able to tell that
   * the scan it was solving is no longer the scan the app is on.
   */
  scanGen: number;
  /** Frames collected during the scan, awaiting the bundle. */
  collected: Omit<BundleFrame, 'pose'>[];
  /**
   * The frames the LAST completed scan solved from, kept so they can be saved.
   *
   * `runEnrollment` empties `app.collected` the moment it hands the frames to
   * the worker, so by the time a wearer could ask to keep the capture it is
   * already gone. This is the same array, held one scan longer — and it is what
   * makes a real capture available to `enroll/telemetry.ts` without a second
   * recording protocol that could drift from the shipping one.
   */
  lastCapture: Omit<BundleFrame, 'pose'>[] | null;
  lastPose: Pose | null;
  busy: boolean;
  fps: number;
  lastRenderMs: number;
  /** Which clock is driving the loop. Reported, because a timer is a worse
   *  clock than the display's and a reviewer should be able to see which one
   *  produced a measurement. */
  loopDriver: 'raf' | 'timer';
  /** Detector inference time over the last ~10 s, ms. Reported so a wearer's
   *  delay report can be attributed between the detector and the tracker. */
  detectMs: number[];
  /** Set once, so the runaway tripwire does not itself flood the console. */
  warnedRunaway: boolean;
}

const STORAGE_KEY = 'ar-v2.facemodel';

/**
 * Past scans of this face, kept so they can be compared with each other.
 *
 * Every accuracy figure in this repository is synthetic, and the synthetic
 * harness cannot measure repeatability at all — each capture is generated from
 * one fixed truth, so two "scans" of a subject are two draws from the same
 * answer. Scanning a real face twice and diffing the results is the only test
 * here that needs no ground truth and no instrument, and it is the only one that
 * can catch a pipeline claiming more precision than it has.
 *
 * Five, because the useful statistic is pairwise and five scans give ten pairs
 * for about four minutes of a wearer's time. Oldest is dropped first.
 */
const PD_KEY = 'ar-v2.knownpd';

/** The wearer's own PD, remembered between sessions. It does not change. */
function readStoredPd(): number | null {
  const raw = Number(localStorage.getItem(PD_KEY));
  return Number.isFinite(raw) && raw >= 45 && raw <= 85 ? raw : null;
}

const HISTORY_KEY = 'ar-v2.scanhistory';
const HISTORY_MAX = 5;

function loadHistory(): FaceModel[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    return (JSON.parse(raw) as string[])
      .map((text) => { try { return deserializeFaceModel(text); } catch { return null; } })
      .filter((m): m is FaceModel => m !== null);
  } catch {
    return [];
  }
}

function pushHistory(model: FaceModel): number {
  let texts: string[] = [];
  try { texts = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]') as string[]; } catch { /* start over */ }
  texts.push(serializeFaceModel(model));
  while (texts.length > HISTORY_MAX) texts.shift();
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(texts));
  } catch {
    // A full quota is not worth losing the scan over.
    return texts.length;
  }
  return texts.length;
}

/**
 * Reprojection above which a scan-phase pose is refused, px.
 *
 * Looser than the tracker's bar, because during the scan the pose is fitted to
 * the TEMPLATE rather than to the wearer, so a perfectly good frame still
 * carries the whole of that person's shape as residual. The job here is only to
 * reject a pose that is not describing this face at all — a hand across it, a
 * second person, a warm start that went stale during a fast turn.
 */
const SCAN_MAX_RMS_PX = 22;

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

/**
 * Fails a promise that takes too long, rather than letting it hang.
 *
 * Boot awaits four things that can each stall without erroring — a fetch, a
 * renderer init, a WASM module, an image decode — and a stalled await produces
 * the worst failure shape available: no error, no log, a status line saying
 * everything is fine, and a frozen page. This tree has produced that shape three
 * times now (an rAF that never fires, a worker that never answers, an image that
 * never decodes), so boot no longer waits on anything indefinitely.
 */
function withTimeout<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => setTimeout(
      () => reject(new Error(`${what} did not finish within ${(ms / 1000).toFixed(0)}s`)), ms,
    )),
  ]);
}

async function boot(): Promise<void> {
  const canvas = document.getElementById('stage') as HTMLCanvasElement;
  const ui = createUI(document.body);
  ui.status('loading the face template');

  const meshText = await withTimeout(
    fetch(asset('assets/face/canonical_face_model.obj')).then((r) => {
      if (!r.ok) throw new Error(`face template: HTTP ${r.status}`);
      return r.text();
    }),
    15000, 'loading the face template',
  );
  const mesh = parseFaceObj(meshText);
  const basis = buildAnthropometricBasis(mesh);
  const regions = standardRegions(mesh);

  ui.status('starting the renderer');
  const scene = await withTimeout(createScene(canvas), 20000, 'starting the renderer');
  ui.status(`renderer: ${scene.backendName}`);

  ui.status('loading the landmark detector');
  const vision = await withTimeout(
    import(/* @vite-ignore */ asset('vendor/mediapipe/vision_bundle.mjs')),
    30000, 'loading the vision runtime',
  ) as any;
  // Rank 7, dark by default: `?confidence=0.3` loosens MediaPipe's three
  // gates so the landmarker holds its track through a deep tilt instead of
  // re-running the face detector, whose fresh landmarks arrive in a slightly
  // different place and read as a pop. Nothing in this tree can measure that
  // — see DetectorConfidence — so it ships off and waits for a wearer who
  // sees pops to try it.
  const confMatch = /[?&]confidence=([0-9]*\.?[0-9]+)/.exec(location.search);
  const conf = confMatch ? Number(confMatch[1]) : NaN;
  const confidence = Number.isFinite(conf) && conf > 0 && conf < 1
    ? { detection: conf, presence: conf, tracking: conf }
    : null;
  const detector = await withTimeout(createMediaPipeDetector(
    vision,
    asset('vendor/mediapipe/wasm'),
    asset('assets/models/face_landmarker.task'),
    { onStatus: (t) => ui.status(t), confidence },
  ), 60000, 'loading the landmark detector');

  const lock = createFrameLock({ detectLongSide: DETECT_LONG_SIDE });

  // Started now rather than when the scan finishes: the worker spends ~40 ms
  // rebuilding the template and the basis, and it can do that while the wearer
  // is still being asked to look at the camera.
  const enroller = await createEnrollClient(
    asset('dist/src/app/enroll.worker.js'),
    asset('assets/face/canonical_face_model.obj'),
    { mesh, basis, regions },
    (m) => console.info('[enroll]', m),
  );
  ui.status(enroller.available ? 'ready' : 'ready (solving on the main thread)');

  const app: App = {
    phase: 'boot',
    mesh, basis, regions, scene, lock, ui, detector, enroller,
    knownPdMm: readStoredPd(),
    source: null,
    intrinsics: intrinsicsFromFov(1280, 720, MEDIAPIPE_ASSUMED_VERTICAL_FOV),
    uncertainty: createUncertainty(mesh.vertexCount),
    identity: createIdentityWatch(),
    tracker: null,
    smooth: true,
    edgeSnap: true,
    snapField: null,
    snapFrame: 0,
    snapBuffer: null,
    snapStats: [0, 0, 0],
    trackStats: [],
    // On by default; `?prior=off` is the wearer's A/B lever. Read once at
    // boot rather than per frame, so a paste describes one arm throughout.
    motionPrior: !/[?&]prior=off\b/.test(location.search),
    marchOval: /[?&]march=on\b/.test(location.search),
    softHook: false,
    model: null,
    seat: null,
    assessment: null,
    frame: TEST_FRAMES[1],
    meshFrames: new Map(),
    wantedFrameId: null,
    protocol: createProtocol(),
    scanGen: 0,
    collected: [],
    lastCapture: null,
    lastPose: null,
    busy: false,
    fps: 0,
    lastRenderMs: 0,
    loopDriver: 'raf',
    detectMs: [],
    warnedRunaway: false,
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
  // `startSource` signals "no camera and no sample image either" by setting the
  // error phase and leaving `app.source` null — its own tail already depends on
  // that convention, since it returns before touching the scene. Boot has to
  // honour it too: it used to overwrite the phase and then call `adoptModel`,
  // whose `app.source!` threw a raw TypeError into the same `#status` element
  // that had just been given the actionable message. The TypeError needed a
  // stored model with `intrinsicsSolved === false`, which is the ORDINARY case —
  // both of the intrinsics beats are optional.
  if (app.phase === 'error' || !app.source) return;
  app.phase = app.model ? 'wear' : 'acquire';
  if (app.model) adoptModel(app, app.model);

  refreshFaceControls(app);
  startLoop(app);

  // Real assets, after the loop is running and never before it. Deliberately
  // not awaited: the catalogue is 62.7 MB and a boot that waited for it would
  // show a frozen page for a minute on a slow connection, for geometry the
  // wearer cannot see until they have been scanned anyway.
  void preloadCatalogue(app).catch((error) => {
    console.warn('the frame catalogue could not be loaded:', error);
  });
}

/**
 * The animation loop, with a watchdog.
 *
 * `requestAnimationFrame` is the right driver — it is the only one synchronised
 * to the display — but there are real environments where it never fires at all:
 * an embedded webview, and (found the hard way) the automation browser this was
 * verified in, where rAF simply does not run and the app sat in `acquire`
 * forever with an fps of zero while every component underneath it worked
 * perfectly.
 *
 * A silent stall is the worst failure shape available: nothing errors, nothing
 * logs, and the picture is just frozen. So the loop watches itself, and falls
 * back to a timer if rAF has not fired within a second. The fallback is a worse
 * clock and says so in the readouts; it is not a worse app.
 *
 * ## The hidden tab, which is not a stall
 *
 * The watchdog measures time since the last rAF callback, and a backgrounded tab
 * satisfies that test **by design**: rAF suspends while the tab is hidden, while
 * `setInterval` keeps firing at the throttled ~1 Hz. Measured in real Chrome,
 * with a working rAF: switch away for a moment and the watchdog latches at
 * 1.66 s having received zero rAF callbacks. Nothing then reset `usingTimer`,
 * and the one queued rAF callback that did arrive on return was discarded by
 * `if (!usingTimer)` — so any tab switch longer than about 1.2 s dropped the
 * mirror onto an unsynchronised 60 Hz timer for the rest of the session, with
 * the console insisting rAF "is not firing in this environment" and the
 * diagnostics reporting `loop: 'timer'`, on a machine where rAF is perfect.
 *
 * Two halves, and only together:
 *
 *  - **Do not latch while hidden.** Counting callbacks instead of timing them
 *    does not help — a hidden tab genuinely delivers zero, which is the same
 *    observation a dead rAF makes. `document.hidden` is the only thing that
 *    distinguishes them. `lastTick` is refreshed on every `visibilitychange` as
 *    well, so the first half-second back does not latch on the gap.
 *  - **Take rAF back if it returns.** A callback arriving while the timer is
 *    driving is proof the environment has one, so it clears the fallback
 *    interval (whose handle now has to be kept, and was not) and re-arms the rAF
 *    chain. Recovery matters because the false latch above is only *probably*
 *    gone: any environment that suspends rAF for a second without setting
 *    `document.hidden` would still trip it, and now it un-trips.
 *
 * Everything here still obeys the rule in `tick`'s comment: exactly one thing
 * schedules the next frame at any moment. Once `usingTimer` goes true the rAF
 * chain stops re-arming, so at most ONE rAF callback is ever outstanding — the
 * one queued before the flip — and recovery cancels the timer before that
 * callback's `tick`, then arms exactly one more. There is no window where both
 * clocks drive.
 */
function startLoop(app: App): void {
  let usingTimer = false;
  let lastTick = performance.now();
  let watchdog: ReturnType<typeof setInterval> | null = null;
  let fallback: ReturnType<typeof setInterval> | null = null;

  const startWatchdog = () => {
    if (watchdog !== null) clearInterval(watchdog);
    watchdog = setInterval(() => {
      if (usingTimer) return;
      // A hidden tab is the one environment where "rAF has not fired in a
      // second" is correct behaviour rather than a fault. Keep the clock warm
      // so the moment of return is not read as the end of a long stall.
      if (document.hidden) { lastTick = performance.now(); return; }
      if (performance.now() - lastTick < 1000) return;
      usingTimer = true;
      clearInterval(watchdog!);
      watchdog = null;
      console.warn(
        'requestAnimationFrame has not fired for a second while this tab is ' +
        'visible — falling back to a timer. Pacing will be worse than the ' +
        'display can offer, and the loop will switch back if rAF returns.',
      );
      app.loopDriver = 'timer';
      fallback = setInterval(() => tick(app, performance.now()), 1000 / 60);
    }, 500);
  };

  const resumeRaf = () => {
    usingTimer = false;
    if (fallback !== null) { clearInterval(fallback); fallback = null; }
    app.loopDriver = 'raf';
    console.info('requestAnimationFrame is firing again — back on the display clock.');
    startWatchdog();
  };

  const step = (t: number) => {
    lastTick = performance.now();
    // rAF answered. If we had given up on it, take it back BEFORE ticking: the
    // timer is a worse clock, the only reason to be on it is this callback not
    // arriving, and cancelling the interval here is what keeps the two clocks
    // from ever driving the same frame.
    if (usingTimer) resumeRaf();
    tick(app, t);
    if (!usingTimer) requestAnimationFrame(step);
  };

  // Both directions. Going hidden stops the drift accumulating; coming back
  // gives rAF a clean second to deliver its first callback before the watchdog
  // is entitled to an opinion.
  document.addEventListener('visibilitychange', () => { lastTick = performance.now(); });

  requestAnimationFrame(step);
  startWatchdog();
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
      app.ui.status('no camera — running on a sample image');
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

/**
 * One frame. **Schedules nothing** — `startLoop` owns the clock.
 *
 * This function used to re-arm `requestAnimationFrame` itself, and when the
 * watchdog in `startLoop` was added it began arming it too. Both ran, so every
 * frame doubled the number of pending callbacks: 1, 2, 4, 8, 16. Within seconds
 * the main thread was doing nothing else.
 *
 * The symptoms did not look like a runaway loop, which is why it survived a
 * review. A wearer's readout showed **10,000 fps**, 402 dropped camera frames
 * and 172 ms of mirror delay, and the face detector could barely hold a lock —
 * all of which read as "this machine is too slow" rather than "the app is
 * calling itself exponentially". The fps figure was the tell and I had already
 * decided it was a clamp artefact.
 *
 * A tick that schedules is a tick that can be scheduled twice. This one cannot.
 */
function tick(app: App, nowMs: number): void {
  if (app.phase === 'error' || !app.source) return;

  const dt = app.lastRenderMs ? (nowMs - app.lastRenderMs) / 1000 : 1 / 60;
  app.lastRenderMs = nowMs;
  // Clamped at 1 ms, not 0.1 ms. Two ticks landing in the same millisecond used
  // to read as 10,000 fps, which is what the readout showed a wearer — a number
  // so obviously wrong that it undermines every honest number beside it.
  app.fps += (1 / Math.max(dt, 1e-3) - app.fps) * 0.1;

  // A tripwire for exactly the failure above. No display runs at 240 Hz here,
  // so a sustained reading past it means something is driving the loop more
  // than once — and the failure is otherwise invisible, presenting as a slow
  // machine rather than as a bug.
  if (app.fps > 240 && !app.warnedRunaway) {
    app.warnedRunaway = true;
    console.error(
      `the render loop is running at ${app.fps.toFixed(0)} fps — it is being driven `
      + 'more than once per frame. Only startLoop() may schedule tick().',
    );
  }

  const offer = app.source.nextFrame(nowMs);
  if (offer && !app.busy) {
    app.busy = true;
    const frame = app.lock.submit(
      offer.source, offer.capturedAtMs, offer.timestampMs, offer.measuredCapture,
    );

    // **The detector is synchronous here, and that is the whole picture's
    // latency, not just the pose's.**
    //
    // `lock.present` below runs AFTER this returns, so every displayed video
    // frame waits on face detection: the wearer sees the room late, which is
    // a different complaint from "the glasses lag my head" and has been
    // conflated with it. Presenting before detecting is NOT the fix — the
    // frame lock exists so a frame is composited with ITS OWN pose, and
    // showing the new pixels under the previous frame's pose is exactly the
    // defect it was built to prevent. A worker is the right home, and the
    // lock is already shaped for one since it drops rather than queues.
    //
    // Until then, measure it: `inferenceMs` has always been computed and
    // thrown away, so no paste could say how much of a reported delay was
    // this line. `app.busy` is a no-op while this stays synchronous; it is
    // kept because it is the guard the worker version needs.
    //
    // `finally`, not a bare assignment. `app.busy` gates every submission, and
    // it is written in exactly two places — here and its initialiser. A throw
    // out of `detectForVideo` (a lost GPU context is the realistic one) left it
    // latched true forever: no frame is ever submitted again, the picture
    // freezes on the last composited frame, and nothing logs. The loop keeps
    // running at full rate, so even the fps readout looks healthy.
    let result: ReturnType<Detector['detect']> = null;
    try {
      result = app.detector.detect(
        app.lock.detect, frame.timestampMs, app.lock.detect.width, app.lock.detect.height,
      );
    } finally {
      app.busy = false;
    }
    if (result && Number.isFinite(result.inferenceMs)) {
      app.detectMs.push(result.inferenceMs);
      if (app.detectMs.length > 300) app.detectMs.shift();
    }

    if (app.lock.present(frame)) app.scene.markBackgroundDirty();
    app.lock.measureBrightness();
    onDetection(app, result, frame.captureDt);
  }

  app.scene.render();
  renderReadouts(app);
}

/**
 * Advances the scan by one frame and shows it, wherever that frame came from.
 *
 * **`finished` has to be tested on every path that can set it, and it was not.**
 * `advanceProtocol` is reached from four places: the good-pose branch, and three
 * early returns — no detection, too few correspondences, and an unstable fit.
 * All four can retire the last beat, because the give-up counter deliberately
 * ticks on faceless frames too (that is what stops a profile hold freezing the
 * scan). Only the good-pose branch went on to fire enrollment.
 *
 * So a scan whose final beat timed out while the face was NOT being tracked sat
 * in `scan` with a finished protocol, prompt cleared and guide dot hidden,
 * until the next well-tracked frame arrived. If the wearer had already given up
 * and walked away, it sat there forever. Measured on the protocol directly, the
 * all-beats-skipped case takes 1,407 frames — 47 seconds — and ends on exactly
 * such a frame.
 */
function stepScan(app: App, sample: PoseSample | null): void {
  if (!app.protocol) return;
  const step = advanceProtocol(app.protocol, sample);
  app.ui.guide(step);
  if (step.finished) void runEnrollment(app);
}


function onDetection(
  app: App, result: ReturnType<Detector['detect']>, captureDt: number,
): void {
  if (!result) {
    // **The tracker has to hear about a faceless frame too**, and until it did,
    // the ride-out it documents did not exist in this app.
    //
    // `TRACKER_DEFAULTS.holdFrames` is 4, and `track()`'s `miss()` path keeps the
    // last pose for that many consecutive failures — the whole point being that
    // one dropped detection is not a lost face. But `track()` was only ever
    // reached from the `wear` branch below, which by construction needs a
    // detection to get to. This branch hid the head node and returned, and
    // `frameNode` is a child of `headNode`, so the glasses came off the face on
    // the FIRST dropped frame: an occlusion, an extreme yaw or roll, motion
    // blur, a dim room. The unit tests assert the hold; they were exercising a
    // path production never took.
    if (app.phase === 'wear' && app.tracker) {
      applyTracked(app, track(app.tracker, {
        landmarks: null, sigmaPx: null, intrinsics: app.intrinsics, dt: captureDt,
      }), captureDt, 'looking for your face');
      return;
    }
    app.scene.setHeadPose(null);
    app.ui.tracked(false, 'looking for your face');
    // The protocol still has to hear about it. A beat whose give-up timer only
    // ticks on frames WITH a face freezes forever when the detector loses the
    // wearer — which is exactly what a profile hold provokes.
    if (app.phase === 'scan') stepScan(app, null);
    return;
  }

  const scale = detectToSourceScale(app.lock);
  const landmarks = scaleLandmarksToSource(result.landmarks, scale);

  const geometry = app.model?.positions ?? app.mesh.positions;
  // Both halves of the return, and the second half is the one that used to be
  // dropped on the floor. `estimateSigma` rasterises the mesh to work out which
  // vertices this pose can actually see; taking only `sigmaPx` and handing the
  // bundle `fill(1)` told it every landmark was fully visible on every frame —
  // including the far-side nose at 35 degrees of yaw, which is behind the nose.
  //
  // The fingerprint was in a real wearer's dump: `noseObservations` equal to
  // `framesUsed`, exactly, because `perVertexUncertainty` weights each
  // observation by visibility and every weight was 1. That also pinned
  // `noseConfidence`'s `observed = min(observations / 25, 1)` term at 1.0 for
  // every real wearer forever, so the branch that was supposed to catch a nose
  // seen in too few frames could never fire.
  //
  // Every test passed the synthesizer's true visibility, so nothing caught it.
  const { sigmaPx, visibility } = app.lastPose
    ? estimateSigma(app.uncertainty, {
      landmarks, mesh: app.mesh, positions: geometry,
      intrinsics: app.intrinsics, pose: app.lastPose,
      // `landmarks` above were just scaled up to source pixels; `floorPx` is
      // calibrated at the detect resolution. Without this the sigma is half
      // what it should be and the bundle trusts it four times too much.
      pixelScale: scale,
    })
    // Before the first pose there is nothing to rasterise against, so nothing is
    // known to be hidden. `null` rather than a confident `fill(1)`.
    : { sigmaPx: acquisitionSigma(app.mesh.vertexCount, { floorPx: UNCERTAINTY_DEFAULTS.floorPx * scale }), visibility: null };

  switch (app.phase) {
    case 'acquire':
    case 'scan': {
      // During acquisition and the scan, pose is solved against the TEMPLATE.
      // That is fine and it is the point: the scan does not need an accurate
      // pose, it needs an initialisation the bundle can improve on.
      const correspondences = buildCorrespondences(
        landmarks, sigmaPx, app.mesh.vertexCount, undefined, 12,
      );
      if (correspondences.length < 40) {
        app.ui.tracked(false, 'not enough of your face is visible');
        if (app.phase === 'scan') stepScan(app, null);
        return;
      }

      let solved = solvePnP(
        app.mesh.positions, correspondences, app.intrinsics, app.lastPose ?? undefined,
      );
      // A warm start that lands badly is usually a warm start that went stale —
      // the head moved a long way while the detector was between frames. Retry
      // cold before accepting it, because an unchecked bad pose here does not
      // just look wrong: it feeds the protocol, so the wearer gets told they are
      // not turning when they are.
      if (!(solved.rmsPx <= SCAN_MAX_RMS_PX) && app.lastPose) {
        const cold = solvePnP(app.mesh.positions, correspondences, app.intrinsics);
        if (cold.rmsPx < solved.rmsPx) solved = cold;
      }
      if (!(solved.rmsPx <= SCAN_MAX_RMS_PX) || !(solved.pose.t[2] > 50)) {
        app.ui.tracked(false, 'hold steady — the fit is unstable');
        app.lastPose = null;
        if (app.phase === 'scan') stepScan(app, null);
        return;
      }

      app.lastPose = solved.pose;
      app.scene.setHeadPose(solved.pose);
      app.ui.tracked(true);

      if (app.phase === 'acquire') {
        app.phase = 'scan';
        app.ui.status('hold still for a moment');
      }

      // The reference is the protocol's own neutral, learned during the opening
      // beat, not the first frame the tracker happened to land on — which is
      // whatever pose the wearer was in while the page was still loading.
      const sample = sampleFromPose(solved.pose, app.protocol.neutral);
      const step = advanceProtocol(app.protocol, sample);
      app.ui.guide(step);

      collectFrame(app, landmarks, sigmaPx, visibility, step.beat?.id ?? 'done');

      if (step.finished) void runEnrollment(app);
      return;
    }

    case 'wear': {
      if (!app.tracker) return;
      applyTracked(app, track(app.tracker, {
        landmarks, sigmaPx, visibility, intrinsics: app.intrinsics, dt: captureDt,
      }), captureDt, undefined, meanFinite(sigmaPx));
      return;
    }

    default:
      return;
  }
}

/**
 * Pushes one tracker result onto the scene and the readouts.
 *
 * Shared by the faceless branch and the tracked branch, because the invariant
 * they have to agree on is `app.lastPose` and it is easy to get right in one of
 * them only. That pose is what `estimateSigma` rasterises visibility against and
 * what `refinePnP` warm-starts from, so it has to die at exactly the moment the
 * tracker drops its own `lastRaw` — which `miss()` does once the gap passes
 * `lostSecondsBeforeReset` (0.5 s), on the reasoning that a velocity carried
 * across half a second describes a movement that is over. Keeping ours alive
 * past that point rasterises the first frame back against a pose the head left
 * long ago, and hands `refinePnP` the same stale start the tracker just refused.
 *
 * `lostReason` overrides the tracker's own wording where the wearer-facing copy
 * is better: "no face detected" is what the tracker means, "looking for your
 * face" is what the person in front of the camera needs to read.
 */
function applyTracked(
  app: App, tracked: TrackResult, dt: number,
  lostReason?: string, meanSigmaPx = NaN,
): void {
  app.lastPose = tracked.rawPose ?? (app.tracker?.lastRaw ? app.lastPose : null);
  app.scene.setHeadPose(tracked.pose);
  if (tracked.tracked && tracked.pose) runEdgeSnap(app, tracked.pose, dt);
  if (tracked.tracked && !tracked.held) {
    app.trackStats.push({
      velMmS: tracked.velMmS,
      velDegS: tracked.velDegS,
      sigmaMm: tracked.sigmaMm,
      sigmaDeg: tracked.sigmaDeg,
      noiseVelMmS: tracked.noiseVelMmS,
      noiseVelDegS: tracked.noiseVelDegS,
      priorShareRot: tracked.priorShareRot,
      priorShareMm: tracked.priorShareMm,
      varianceFactor: tracked.varianceFactor,
      latched: tracked.latched,
      fading: tracked.fading,
      lagMm: tracked.smoothingLagMm,
    });
    if (app.trackStats.length > 300) app.trackStats.shift();
  }
  app.ui.tracked(tracked.tracked, lostReason ?? tracked.reason ?? undefined);

  // **Is this still the person we scanned?**
  //
  // Asked only while wearing, and only of a model that came from a scan taken
  // in THIS session: `identity.ts` learns the wearer's own reading before it
  // will judge anybody, and a model restored from storage was measured on a
  // previous session and possibly another device. The watch abstains there
  // rather than referencing whoever happens to be sitting down — see its
  // header, "What it refuses to answer".
  //
  // Degraded models are excluded for the plainer reason: the average face is
  // nobody, so every wearer disagrees with it and a predicate pointed at it
  // would convict on the first frontal frame. v1 hit exactly this and its
  // harness pins the rule — "a face with no iris reading is never called a
  // stranger... comparing a real face against an average would call every
  // wearer a stranger on their first frame."
  if (app.phase === 'wear' && app.model && !app.model.degraded) {
    const verdict = observeIdentity(app.identity, {
      solved: tracked.tracked && !tracked.held,
      varianceFactor: tracked.varianceFactor,
      yawRad: tracked.euler ? tracked.euler.yaw : NaN,
      pitchRad: tracked.euler ? tracked.euler.pitch : NaN,
      correspondences: tracked.correspondences,
      meanSigmaPx,
    });
    if (verdict === 'changed') resetPerson(app, 'identity');
  }
}

/**
 * The per-frame edge snap: predicted contour from the scan, real edge from the
 * video, silhouette vertices nudged onto it. See track/snap.ts for the design;
 * this function is only the plumbing between the tree's own pieces —
 * rasteriser, frame-locked pixels, tracker pose — and it must stay cheap: at a
 * 224-px raster with ~200 contour samples it measures ~2 ms.
 *
 * The EMA is the temporal half: offsets are per-frame opinions with noise, and
 * a boundary that flickers is worse than one that is steadily a pixel off. New
 * opinions blend in at SNAP_BLEND; a vertex nobody voted for decays back to
 * the pure scan, so a look-away heals to geometry in a few frames.
 */
/**
 * The per-frame half of the edge calibration: rasterise the scan at the pose,
 * find the predicted occluding contour, read the real edge off the locked
 * frame, and hand the pushes to the CalibrationField — which converges over
 * ~a second and then freezes, making the boundary a constant of the session.
 * Once converged, this runs one frame in eight as a drift monitor; the other
 * seven frames pay nothing. See track/snap.ts for the design and for why the
 * per-frame EMA this replaced was measurably wrong ("if the user doesn't
 * move, the edge of the glasses will").
 */
const SNAP_MONITOR_STRIDE = 8;
function runEdgeSnap(app: App, pose: Pose, dt: number): void {
  if (!app.edgeSnap || !app.model || app.phase !== 'wear') return;
  const V = app.mesh.vertexCount;
  if (!app.snapField) app.snapField = new CalibrationField(V);
  const field = app.snapField;

  app.snapFrame++;
  const converged = field.convergence() > 0.9;
  if (converged && app.snapFrame % SNAP_MONITOR_STRIDE !== 0) {
    // The glide must keep moving on the frames the snap itself skips, or a
    // correction still in flight would stall for seven frames out of eight.
    app.scene.nudgeOccluder(field.advance(dt));
    return;
  }

  const k = app.intrinsics;
  if (!app.snapBuffer || app.snapBuffer.width !== 224) {
    app.snapBuffer = createDepthBuffer(224, Math.round((224 * k.height) / k.width), k);
  }
  rasterize(app.snapBuffer, app.model.positions, app.mesh.indices, V, pose, k);
  const contour = occludingContour(app.snapBuffer, { jumpMm: 6, stride: 2 });

  if (contour.length >= 8) {
    // Pixels of the exact frame this pose was solved on — the frame lock's
    // whole guarantee, and the reason the snap can trust what it reads.
    const display = app.lock.display;
    const ctx = display.getContext('2d', { willReadFrequently: true });
    if (ctx) {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const c of contour) {
        if (c.x < minX) minX = c.x; if (c.x > maxX) maxX = c.x;
        if (c.y < minY) minY = c.y; if (c.y > maxY) maxY = c.y;
      }
      // Contour coordinates are INTRINSICS pixels; the display canvas may be a
      // different resolution. One scale factor bridges them at the sampler —
      // the classic px-of-which-image bug this tree has already paid for once.
      const toDisp = display.width / k.width;
      const pad = 12;
      const sx = Math.max(0, Math.floor(minX * toDisp - pad));
      const sy = Math.max(0, Math.floor(minY * toDisp - pad));
      const sw = Math.min(display.width - sx, Math.ceil((maxX - minX) * toDisp + 2 * pad));
      const sh = Math.min(display.height - sy, Math.ceil((maxY - minY) * toDisp + 2 * pad));
      if (sw > 4 && sh > 4) {
        const img = ctx.getImageData(sx, sy, sw, sh);
        const d = img.data;
        const lum = (x: number, y: number): number => {
          const ix = Math.max(0, Math.min(sw - 1.001, x * toDisp - sx));
          const iy = Math.max(0, Math.min(sh - 1.001, y * toDisp - sy));
          const x0 = ix | 0, y0 = iy | 0, fx = ix - x0, fy = iy - y0;
          const at = (xx: number, yy: number) => {
            const o = (yy * sw + xx) * 4;
            return 0.299 * d[o] + 0.587 * d[o + 1] + 0.114 * d[o + 2];
          };
          const top = at(x0, y0) * (1 - fx) + at(Math.min(x0 + 1, sw - 1), y0) * fx;
          const bot = at(x0, Math.min(y0 + 1, sh - 1)) * (1 - fx)
            + at(Math.min(x0 + 1, sw - 1), Math.min(y0 + 1, sh - 1)) * fx;
          return top * (1 - fy) + bot * fy;
        };
        const snap = snapOffsets(contour, lum);
        const pushes = contourPushes(contour, snap, app.model.positions, V, pose, k);
        field.update(pushes);
        let confident = 0; const abs: number[] = [];
        for (let i = 0; i < contour.length; i++) {
          if (snap.confidence[i] > 0) { confident++; abs.push(Math.abs(snap.offsetPx[i])); }
        }
        abs.sort((a, b) => a - b);
        app.snapStats = [contour.length, confident, abs.length ? abs[abs.length >> 1] : 0];
      }
    }
  }
  app.scene.nudgeOccluder(field.advance(dt));
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
  app: App, landmarks: Float64Array, sigmaPx: Float64Array,
  visibility: Float64Array | null, beat: string,
): void {
  app.collected.push({
    landmarks: new Float64Array(landmarks),
    sigmaPx: new Float64Array(sigmaPx),
    visibility: visibility
      ? new Float64Array(visibility)
      : new Float64Array(app.mesh.vertexCount).fill(1),
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

  // The scan this call is solving. Every resumption point below re-reads
  // `app.scanGen` and gives up if the wearer has started again in the
  // meantime — see `App.scanGen` for what the unguarded version did.
  const gen = app.scanGen;
  const superseded = (): boolean => {
    if (gen === app.scanGen) return false;
    console.info('[enroll] discarding a solve for a scan the wearer restarted');
    return true;
  };

  try {
    // Off the main thread when a worker is available, which is the normal case.
    // The frames are CLONED across, not transferred, so this side still holds
    // them — dropped explicitly here rather than by the handoff, both to release
    // the memory and because the enrollment client's fallback needs them intact
    // if the worker dies.
    const frames = app.collected;
    app.collected = [];
    // Held for `save-capture`. The bundle is about to consume these and nothing
    // else keeps them; a wearer who has just been scanned is exactly the person
    // who can be asked whether to keep the capture.
    app.lastCapture = frames;
    const result = await app.enroller.run({
      frames,
      imageWidth: app.source!.width,
      imageHeight: app.source!.height,
      knownPdMm: app.knownPdMm,
    });
    console.info('[enroll] coverage', result.coverage, summarise(app.protocol), `on the ${result.ranOn} thread`);
    if (superseded()) return;
    // Attach the scan BEFORE adopting or storing, so the record travels with the
    // model into localStorage and comes back on the next page load. Reading the
    // live protocol at diagnostics time instead is what produced a real dump
    // saying "0 of 7 done" beside a model built from 48 frames.
    const model = withScanRecord(result.model, scanRecord(app.protocol));
    adoptModel(app, model);
    // **Only a camera's face is allowed to become "your saved measurements".**
    //
    // `startSource` falls back to a bundled sample photograph when
    // `getUserMedia` throws, and a DENIED PERMISSION throws exactly like an
    // absent camera. The scan then ran to completion against a stranger's
    // face and stored it, so the next page load greeted the wearer with
    // "using your saved measurements" over somebody else's nose. `Source`
    // has carried `kind` since it was written and nothing had ever read it.
    //
    // The solve still runs and still adopts, because a still image is how
    // this app is developed on a machine with no camera — what it may not do
    // is persist. See `docs/PRIVACY.md`.
    const live = app.source?.kind === 'camera';
    // **Arm the identity watch here and nowhere else.** This is the one moment
    // the app knows who is in front of the camera: they have just sat through a
    // scan of their own face, on this device, in this session. Every other route
    // to a model — a restore from storage at boot, the average face — leaves the
    // watch disarmed, which is a permanent abstention rather than a soft start.
    if (live) armWearer(app.identity);
    const kept = live ? pushHistory(model) : 0;
    if (live) localStorage.setItem(STORAGE_KEY, serializeFaceModel(model));
    app.ui.status(
      (model.degraded
        ? `measured, with caveats: ${model.notes.join('; ')}`
        : `measured in ${model.solveMs.toFixed(0)} ms`)
      + (live
        ? (kept > 1 ? ` — ${kept} scans saved, "Compare scans" will diff them` : '')
        : ' — sample image, not saved'),
    );
  } catch (error) {
    console.error('enrollment failed', error);
    if (superseded()) return;
    app.ui.status('the scan did not work — using average measurements');
    adoptModel(app, templateModel(app));
  }
  if (gen === app.scanGen) app.collected = [];
}

/**
 * Which class of state each field of `App` belongs to.
 *
 * **This exists because `rescan` was already wrong, and nothing could tell.**
 * The reset was written by hand, field by field, and it cleared eleven of the
 * roughly eighteen person-derived fields. The seven it missed were not obscure:
 *
 *   - `lastCapture` — the PREVIOUS wearer's raw landmark frames, 1.8-3.6 MB of
 *     them, which **Save this scan** then writes to disk under the NEW wearer's
 *     PD. One person's biometrics in a file labelled with another's.
 *   - `knownPdMm` — person A's typed PD becomes the absolute ruler the next
 *     bundle scales person B's whole face by.
 *   - `intrinsics` — if A completed the lean beat, B's entire scan runs on A's
 *     solved focal length.
 *   - `lastPose` — warm-starts B's first PnP from A's pose, and gates whether
 *     `estimateSigma` runs at all.
 *   - `uncertainty` — its per-landmark disagreement EMA holds A's landmarks, so
 *     B's first frames are scored against a shape difference. Measured on two
 *     synthetic subjects: median disagreement 0.33 px -> 1.49 px on the swap
 *     frame, max 0.35 -> 4.41, decaying over about 15 frames. Those numbers are
 *     the sigmas the bundle weights by.
 *   - `trackStats`, `snapStats` — the previous face's instrument readings.
 *
 * A hand-written reset cannot be reviewed against a growing interface, so the
 * classification is data and `tests/app.test.ts` asserts that **every key of
 * `App` appears here exactly once**. Add a field and forget it, and the test
 * goes red naming the field. That is the check; the reset below is just a loop.
 *
 * v1 had the same idea and called it `PER_SESSION_STATE`. Its shape is worth
 * keeping: the classes are about WHO a field belongs to, not about when it
 * happens to be convenient to clear it.
 *
 *   'person'  belongs to the wearer. Cleared by a rescan AND by an identity
 *             change. If in doubt, a field goes here — clearing something
 *             person-independent costs a recompute; keeping something
 *             person-derived is the bug this manifest exists to stop.
 *   'app'     belongs to the session, the device or the wearer's CHOICES.
 *             Survives both. A change of wearer is not a change of taste, and
 *             the loop's own clocks are statements about frames, not people.
 *   'never'   immutable for the life of the page — the template, the renderer,
 *             the detector, the DOM.
 */
type StateClass = 'person' | 'app' | 'never';

const PERSON_STATE: Readonly<Record<keyof App, StateClass>> = {
  // ---- the wearer -------------------------------------------------------
  model: 'person',
  seat: 'person',
  assessment: 'person',
  tracker: 'person',
  protocol: 'person',
  collected: 'person',
  snapField: 'person',
  snapFrame: 'person',
  snapBuffer: 'person',
  snapStats: 'person',
  trackStats: 'person',
  lastCapture: 'person',
  lastPose: 'person',
  uncertainty: 'person',
  knownPdMm: 'person',
  // Person-owned, but reset through `forgetWearer` rather than by replacement:
  // the reference and the streak go, the LIFETIME COUNTERS stay. A counter that
  // resets with the thing it counts cannot report the reset, and `convictions`
  // is the only way a paste from a live session says whether this mechanism has
  // ever fired.
  identity: 'person',
  // Camera geometry is a property of the DEVICE, but `adoptModel` overwrites it
  // with the scan's SOLVED value, at which point the number in this field is a
  // thing measured about one wearer's session. It reverts to the device default
  // rather than to nothing.
  intrinsics: 'person',
  // The phase machine is where the reset lands, so it is person-owned by
  // definition: no wearer, no `wear`.
  phase: 'person',
  // The anti-resurrection counter. Person-owned because it must ADVANCE on
  // every reset — a solve suspended inside `enroller.run` compares it across
  // the await to find out that the scan it is solving is no longer the scan the
  // app is on. It increments rather than zeroing; see `App.scanGen`.
  scanGen: 'person',

  // ---- the session, the device, and the wearer's choices ----------------
  frame: 'app',            // which glasses. A new wearer keeps the same pair on screen.
  wantedFrameId: 'app',
  meshFrames: 'app',       // loaded assets; person-independent, and expensive
  softHook: 'app',         // an A/B toggle
  smooth: 'app',
  edgeSnap: 'app',
  motionPrior: 'app',
  marchOval: 'app',
  source: 'app',           // the camera; a source switch has its own path
  busy: 'app',
  fps: 'app',
  lastRenderMs: 'app',
  loopDriver: 'app',
  detectMs: 'app',
  warnedRunaway: 'app',

  // ---- fixed for the life of the page -----------------------------------
  mesh: 'never',
  basis: 'never',
  regions: 'never',
  scene: 'never',
  lock: 'never',
  ui: 'never',
  detector: 'never',
  enroller: 'never',
};


/**
 * Forgets the wearer. The single reset, used by every path that has one.
 *
 * Two callers, and they must not drift: the **Scan again** button, and an
 * identity change convicted by `track/identity.ts`. They differ in exactly one
 * thing — whether the wearer asked — and that difference belongs in the message,
 * not in two hand-written lists of assignments.
 *
 * Everything `PERSON_STATE` calls the wearer's is cleared here, and the manifest
 * is what makes that reviewable. `tests/app.test.ts` asserts the two agree.
 *
 * **What is deliberately NOT touched, and why each:**
 *
 *  - `localStorage`. A different person walking into the room is not a reason to
 *    destroy the first person's saved scan. `rescan` removes the stored model
 *    itself, before calling this, because a wearer who asked to start again has
 *    said what they want; an identity change has not. "Delete my measurements"
 *    is the control for that, and it is the only one.
 *  - `app.frame` and `wantedFrameId`. A change of wearer is not a change of
 *    taste — v1's phrase, and it is right. The glasses stay chosen.
 *  - The pose filter's level, the loop's clocks, the frame lock. Those are
 *    statements about FRAMES, not about people. Nobody moved when the app
 *    changed its mind about whose face it is.
 *  - The identity watch's lifetime counters. `forgetWearer` clears the
 *    reference and the streak and keeps `convictions`, because a counter that
 *    resets with the thing it counts cannot report the reset.
 */
function resetPerson(app: App, reason: 'rescan' | 'identity'): void {
  // FIRST, before anything below is overwritten. A solve may be suspended
  // inside `enroller.run` right now and its continuation is about to resume
  // against exactly this state; comparing the counter across the await is what
  // lets it discover that the scan it is solving is no longer the scan the app
  // is on. See `App.scanGen`.
  app.scanGen++;

  app.model = null;
  app.seat = null;
  app.assessment = null;
  app.tracker = null;
  app.protocol = createProtocol();
  app.collected = [];
  app.snapField = null;
  app.snapFrame = 0;
  app.snapBuffer = null;
  app.snapStats = [0, 0, NaN];
  app.trackStats = [];
  // The previous wearer's raw landmark frames. Held live at 1.8-3.6 MB, and
  // `save-capture` writes them out under whatever PD is current — which after a
  // swap is somebody else's.
  app.lastCapture = null;
  // Warm-starts the next PnP and gates `estimateSigma`. A new face solved from
  // the old face's pose is a solve starting in the wrong basin.
  app.lastPose = null;
  // Its per-landmark disagreement EMA is a memory of the previous face's
  // landmarks; on the first frames after a swap every residual reads as noise.
  app.uncertainty = createUncertainty(app.mesh.vertexCount);
  // Person A's typed PD is not person B's ruler. The stored value is left alone
  // — this clears the one in play, and the wearer is told.
  app.knownPdMm = null;
  // Back to the device default rather than to nothing: `adoptModel` replaced
  // this with the scan's SOLVED focal length, which belongs to that session.
  // The device's own default, from the live source's dimensions — not the
  // boot-time 1280x720 guess and not the previous wearer's solve.
  if (app.source) {
    app.intrinsics = intrinsicsFromFov(
      app.source.width, app.source.height, MEDIAPIPE_ASSUMED_VERTICAL_FOV,
    );
  }

  detachFrame(app.scene);
  app.scene.setHeadPose(null);
  forgetWearer(app.identity);

  app.phase = 'acquire';
  app.ui.frameNote('');
  app.ui.status(reason === 'rescan'
    ? 'starting again'
    : 'this looks like a different face — measuring again');
  refreshFaceControls(app);
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
  // The edge calibration is a property of ONE face's geometry, and this line
  // is the whole reason `CalibrationField.reset()` exists — it had no caller
  // at all until the landed-code review found that out. Without it a rescan
  // keeps the previous face's boundary, and keeps it HARD: after ~15 frames
  // every touched vertex sits at `weightCap`, so new evidence moves the
  // estimate by at most a sixteenth, and the agreement gate rejects outright
  // any push more than `agreementMm` from the old value. The second face
  // would be structurally unable to correct the first one's occluder for the
  // rest of the session. The buffer goes too: it caches the intrinsics, and
  // adopting a model can change them.
  app.snapField = null;
  app.snapFrame = 0;
  app.snapBuffer = null;
  // The same floor rule as the Steady handler, and it must stay the same:
  // the sigma stream this app feeds track() is in SOURCE pixels, so the
  // adaptive floor scales by the same factor. Dormant while 'adaptive' sits
  // outside the Steady cycle, but a tracker built here with the detect-
  // resolution floor would read every clean frame as ~2x noise the day that
  // mode returns — the two creation sites disagreeing is the actual bug.
  app.tracker = createTracker(model, {
    smooth: app.smooth,
    adaptiveFloorPx: UNCERTAINTY_DEFAULTS.floorPx * detectToSourceScale(app.lock),
    // The eye region may not vote on the pose: MediaPipe deforms it with
    // gaze, and the wearer's eyes must not steer their glasses.
    rigidity: trackingRigidity(app.mesh, app.regions),
    motionPrior: app.motionPrior,
    // The oval landmarks track a sliding contour, not a fixed point - built
    // from THIS wearer's solved geometry, so the strips describe their face.
    ovalStrips: app.marchOval ? silhouetteStrips(app.mesh, model.positions) : null,
  });
  // A new tracker starts its counters at zero; the readout ring must start
  // with it, or the diagnostics 'recent' block describes frames a different
  // tracker produced.
  app.trackStats = [];
  app.uncertainty = createUncertainty(app.mesh.vertexCount);
  // The source dimensions, or the same 1280x720 the app was constructed with.
  // Not `app.source!`: this is reached from the error path's neighbourhood, and
  // a raw TypeError landing in `#status` on top of an actionable message is
  // strictly worse than an assumed field of view that is already marked as
  // assumed in the readouts.
  app.intrinsics = model.intrinsicsSolved
    ? model.intrinsics
    : intrinsicsFromFov(
      app.source?.width ?? 1280, app.source?.height ?? 720, MEDIAPIPE_ASSUMED_VERTICAL_FOV,
    );
  app.scene.applyIntrinsics(app.intrinsics);
  // The seat and the occluder are one surface: the same `model.positions` the
  // contact solve seats the frame against goes to the GPU as the depth-only
  // occluder, unchanged. Passing anything else here is v1's occlusion bug.
  // The ear rests come from the model too, because the occluder is a HEAD now:
  // the skull is lofted from the face mesh's own boundary and a dish is hung at
  // each ear. Without them a temple at yaw has nothing to hide behind — measured
  // at 8.9% of temple samples X-raying through the head at 45 degrees and 12.5%
  // at 60.
  app.scene.setOccluder(model.positions, app.mesh.indices, earRestPoints(model));
  // A real asset may have finished downloading while the wearer was being
  // scanned, in which case `preloadCatalogue` could not install it — there was
  // no model to seat it against. Pick it up here rather than leaving the wearer
  // on the parametric stand-in until they click something.
  const ready = app.wantedFrameId === null
    ? app.meshFrames.values().next().value
    : app.meshFrames.get(app.wantedFrameId);
  if (ready) {
    app.wantedFrameId ??= ready.asset.id;
    fitFrame(app, ready.asset, ready.object);
  } else {
    fitFrame(app, app.frame);
  }
  app.phase = 'wear';
  refreshFaceControls(app);
}

/** Solves the seat ONCE and caches it on the scene graph. */
/**
 * Loads the mesh-backed catalogue in the background, after boot.
 *
 * **Nothing on the boot path awaits this.** The assets total 62.7 MB and
 * `meshy-glasses.glb` alone is 23.3 MB, so a boot that waited for them is a boot
 * that shows nothing for a minute on a slow connection. The app comes up wearing
 * the parametric default and real frames appear in the picker as they land.
 *
 * **Every row is fetched, and that is a reversal.** This loop used to skip any
 * row whose declared part lists were empty, on the sound reasoning that
 * `frameFromMesh` refused it on the names alone and the bytes could tell us
 * nothing — 47.7 of the 62.7 MB downloaded to reach a decidable refusal.
 *
 * The premise is gone. The derivation no longer needs a part called `temple`:
 * it finds each arm by splitting the mesh and fitting its knee, so whether an
 * asset derives is now a question about geometry for EVERY row, and geometry
 * needs the file. Seven of the ten reached the picker only after this filter
 * came out; while it was here they were unreachable no matter what the
 * derivation did, and the symptom was a frame list with three entries in it.
 *
 * Sequential rather than parallel: it is bandwidth that is scarce here, not
 * latency, and the wearer is looking at a working page throughout. The order is
 * the catalogue's, which puts the two authored assets first, so the frames most
 * worth looking at arrive while the 23 MB scan is still coming down.
 */
async function preloadCatalogue(app: App): Promise<void> {
  for (const entry of CATALOGUE) {
    try {
      const response = await fetch(asset(entry.file));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      const built = frameFromMesh(readGlb(bytes), entry);
      if (!built.ok) {
        // Not a warning. Nine of ten refuse today and every refusal is the
        // correct answer for an asset nothing has measured — see
        // `fit/catalogue.ts` for the per-asset reasons.
        console.info(`[catalogue] ${entry.id}: ${built.reason}`);
        continue;
      }
      const loaded = await loadFrameMesh(built.asset, document.baseURI);
      // The cache owns this object for the life of the page; the scene must not
      // dispose it on the next frame swap. See `attachFrameObject`.
      loaded.object.userData[CACHED_BY_CALLER] = true;
      if (loaded.lensPartCount === 0) {
        // A matcher that returns nothing looks exactly like a frame with no
        // lenses, and v1 shipped precisely that: "a frame with modelled lenses
        // rendered as a frame with empty rims". Say so rather than draw it.
        console.warn(`[catalogue] ${entry.id}: no lens parts matched — rims will render empty`);
      }
      app.meshFrames.set(entry.id, { asset: built.asset, object: loaded.object });
      app.ui.addFrame(entry.id, built.asset.name);
      for (const note of built.notes) console.info(`[catalogue] ${entry.id}: ${note}`);

      // The first real frame becomes the one on screen, but only if the wearer
      // has not already chosen for themselves. Swapping a frame out from under
      // somebody who just picked it is worse than showing them the default.
      if (app.wantedFrameId === null && app.model) {
        app.wantedFrameId = entry.id;
        fitFrame(app, built.asset, loaded.object);
        app.ui.status(`wearing ${built.asset.name}`);
      }
    } catch (error) {
      console.warn(`[catalogue] ${entry.id} failed to load:`, error);
    }
  }
}

/**
 * Puts the face controls in the state the app is actually in.
 *
 * Called wherever the answer to "does this app have a model, and is it the
 * wearer's?" changes — boot, adopt, rescan — rather than every frame, because
 * the panel is not a readout and rewriting it at 30 Hz is how a button stops
 * being clickable on a slow machine.
 */
function refreshFaceControls(app: App): void {
  const hasModel = app.model !== null;
  const scanning = app.phase === 'acquire' || app.phase === 'scan' || app.phase === 'solving';
  // The hint is the only place the difference between the two routes is stated
  // in words. A degraded model is the average face; anything else came from a
  // scan of this wearer.
  const hint = !hasModel
    ? 'Nothing is drawn until there is a face to draw it on.'
    : app.model!.degraded
      ? 'These are placed on an AVERAGE face. The picture is real; the millimetres are not yours.'
      : 'Placed on your own scan.';
  app.ui.face({ hasModel, scanning, hint });
}

/**
 * The mean of the finite entries, or NaN if there are none.
 *
 * A hidden landmark arrives with `Infinity` (see `detect/uncertainty.ts`), so a
 * plain mean of this array is `Infinity` on any turned frame — which would make
 * the identity watch's drift guard fire on every turn instead of on a drifting
 * detector.
 */
function meanFinite(values: ArrayLike<number>): number {
  let sum = 0, n = 0;
  for (let i = 0; i < values.length; i++) {
    if (Number.isFinite(values[i])) { sum += values[i]; n++; }
  }
  return n ? sum / n : NaN;
}

/** What this frame's fit numbers are worth, in one line for the wearer. */
function frameNoteFor(frame: FrameAsset): string {
  if (frame.source === null) return 'A parametric test shape, not a real pair of glasses.';
  switch (frame.earRestSource) {
    case 'measured':
      return 'Arms measured from the asset’s own temple. This is the reference frame.';
    case 'derived':
      return 'Arms found from the model’s geometry — the file names no temple part.';
    case 'assumed':
      return 'A wrap: its arms have no rest point, so your ear supplied one. '
        + 'The picture is right; treat the millimetres as an estimate.';
    default:
      return '';
  }
}


/**
 * Puts a frame on the face: solve the seat, draw it, grade the fit.
 *
 * Stays SYNCHRONOUS, deliberately. `adoptModel` calls it and then sets
 * `phase = 'wear'` on the very next line; an `await` between those two lets the
 * render loop run with a model adopted, an occluder set and no frame attached.
 * Loading is done ahead of time by `preloadCatalogue`, and `object` is that
 * already-loaded geometry — placed by the asset's own `source.meshToFrame`.
 */
function fitFrame(app: App, frame: FrameAsset, object?: any): void {
  if (!app.model) return;
  app.frame = frame;
  const seat = solveSeat(app.model, app.mesh, app.regions, frame,
    app.softHook ? { hookStiffnessNPerMm: 0.11 } : {});
  app.seat = seat;
  // Attach BEFORE applySeat: the swap disposes the old geometry and adds the
  // new, and the one call that writes the seat matrix then refreshes the world
  // matrices over the fresh child. `applySeat` itself stays "called once per
  // fit" and unchanged.
  attachFrame(app.scene, frame, object);
  applySeat(app.scene, seat.pose);
  frameSanityTripwire(app, frame, seat);
  app.assessment = assessFit(app.model, app.mesh, app.regions, frame, seat);
  app.ui.fit(app.assessment);
  app.ui.frameNote(frameNoteFor(frame));
  app.ui.selectFrame(frame.id);
}

/**
 * A console tripwire for the double-flip class of defect (`render/convert.ts`:
 * a seat passed through the CV->GL flip lands mirrored in Y and Z, 127 mm
 * below and behind the head). Checked in FACE space, so it is independent of
 * the live head pose: the seated frame's lateral centre must sit inside the
 * head's bounding box, and the lens centres must sit at the FRONT of the face
 * (a double flip throws them ~150 mm behind it). Warns, never throws — a
 * wrongly-placed frame is a visible defect, not a reason to take the app down.
 */
function frameSanityTripwire(app: App, frame: FrameAsset, seat: SeatResult): void {
  if (!app.model) return;
  const p = app.model.positions;
  let hx0 = Infinity, hx1 = -Infinity, hy0 = Infinity, hy1 = -Infinity, hz1 = -Infinity;
  for (let i = 0; i < p.length; i += 3) {
    if (p[i] < hx0) hx0 = p[i];
    if (p[i] > hx1) hx1 = p[i];
    if (p[i + 1] < hy0) hy0 = p[i + 1];
    if (p[i + 1] > hy1) hy1 = p[i + 1];
    if (p[i + 2] > hz1) hz1 = p[i + 2];
  }
  const { R, t } = seat.pose;
  const toFace = (v: ArrayLike<number>) => [
    R[0] * v[0] + R[1] * v[1] + R[2] * v[2] + t[0],
    R[3] * v[0] + R[4] * v[1] + R[5] * v[2] + t[1],
    R[6] * v[0] + R[7] * v[1] + R[8] * v[2] + t[2],
  ];
  // The frame's extremal points, per the asset's own fields.
  const marks = [
    ...frame.lensCentres.map(toFace), ...frame.hinges.map(toFace), ...frame.earRests.map(toFace),
  ];
  const cx = marks.reduce((s, m) => s + m[0], 0) / marks.length;
  const lensZ = (toFace(frame.lensCentres[0])[2] + toFace(frame.lensCentres[1])[2]) / 2;
  const lensY = (toFace(frame.lensCentres[0])[1] + toFace(frame.lensCentres[1])[1]) / 2;
  const laterallyCentred = cx >= hx0 && cx <= hx1;
  const lensesAtTheFront = lensZ > hz1 - 45; // within 45 mm of the frontmost skin
  const lensesAtEyeHeight = lensY >= hy0 && lensY <= hy1;
  if (laterallyCentred && lensesAtTheFront && lensesAtEyeHeight) {
    console.debug(
      `[frame] sanity ok: centre x ${cx.toFixed(1)} in [${hx0.toFixed(0)}, ${hx1.toFixed(0)}], `
      + `lens z ${lensZ.toFixed(1)} vs face front ${hz1.toFixed(1)}, lens y ${lensY.toFixed(1)}`,
    );
  } else {
    console.warn(
      `[frame] SANITY TRIPWIRE: frame "${frame.id}" seated implausibly — `
      + `centre x ${cx.toFixed(1)} (head x [${hx0.toFixed(0)}, ${hx1.toFixed(0)}]), `
      + `lens z ${lensZ.toFixed(1)} (face front ${hz1.toFixed(1)}), `
      + `lens y ${lensY.toFixed(1)} (head y [${hy0.toFixed(0)}, ${hy1.toFixed(0)}]). `
      + 'A mirrored Y/Z pair is the double-flip defect in render/convert.ts.',
    );
  }
}

function handleAction(app: App, action: string): void {
  switch (action) {
    case 'set-pd': {
      const raw = app.ui.askPd();
      if (raw === null) break;
      if (raw === 0) {
        localStorage.removeItem(PD_KEY);
        app.knownPdMm = null;
        app.ui.status('PD cleared — the scan will use the pooled iris assumption');
        break;
      }
      if (!(raw >= 45 && raw <= 85)) {
        app.ui.status(`${raw} mm is outside the human range (45 to 85) — not used`);
        break;
      }
      localStorage.setItem(PD_KEY, String(raw));
      app.knownPdMm = raw;
      app.ui.status(`PD set to ${raw} mm — rescan to use it as the ruler`);
      break;
    }
    case 'forget-scans':
      localStorage.removeItem(HISTORY_KEY);
      app.ui.status('saved scans deleted from this device');
      break;
    case 'rescan':
      // The stored MODEL goes, and only on this path: starting again is exactly
      // what a repeatability run consists of, and the wearer asked. An identity
      // change takes the same reset WITHOUT this line.
      localStorage.removeItem(STORAGE_KEY);
      resetPerson(app, 'rescan');
      break;

    // **The shortcut past the scan, and the reason it exists.**
    //
    // Nothing draws a frame until a `FaceModel` exists — `fitFrame` returns
    // early without one — and the only route to one was a seven-beat guided
    // scan. Measured on the protocol itself, that is 34 detection frames for a
    // co-operative wearer but 1,407 (about 47 seconds) when no face is found at
    // all, which is exactly the state anyone trying the glasses on a laptop
    // with the camera denied sits in. So the glasses could not be looked at.
    //
    // `templateModel` is the average face the tree already carries and already
    // labels: `degraded: true`, scale `assumed`, and a note saying every number
    // is the average face. Adopting it deliberately is no less honest than
    // adopting it on a failed solve, which is what already happened.
    case 'average':
      if (app.model) { app.ui.status('you already have a scan — use “Scan again” to replace it'); break; }
      adoptModel(app, templateModel(app));
      app.ui.status('average face — the glasses are placed, the measurements are not yours');
      break;
    case 'hook': {
      if (!app.model) { app.ui.status('scan first — there is no seat to adjust yet'); break; }
      app.softHook = !app.softHook;
      // Pass the loaded object back in: without it `attachFrame` would fall
      // back to `createFrameObject` and quietly replace a real mesh with tubes.
      fitFrame(app, app.frame, app.meshFrames.get(app.frame.id)?.object);
      const hookBtn = document.querySelector('[data-action="hook"]');
      if (hookBtn) hookBtn.textContent = `Hook: ${app.softHook ? 'soft' : 'wall'}`;
      const v = app.seat;
      app.ui.status(app.softHook
        ? `soft hook (0.11 N/mm cantilever) — descent ${v ? v.descentMm.toFixed(1) : '?'} mm. Check the Lens distance verdict.`
        : 'wall hook (shipped default) — the seat is back to the stiff configuration.');
      break;
    }
    case 'steady': {
      if (!app.model) { app.ui.status('scan first — there is no pose to steady yet'); break; }
      // off -> fixed -> locked -> off. Adaptive retired from the cycle after
      // the first real wearer: its noise proxy runs permanently high on real
      // frames (the mesh rim is always oblique somewhere), so it bought delay,
      // not steadiness. 'locked' is the stillness latch v2 — exact stillness
      // at rest, velocity-gated release through a short crossfade — layered
      // on the fixed filter the same wearer judged acceptable while moving.
      app.smooth = app.smooth === false ? true : app.smooth === true ? 'locked' : false;
      const scale = detectToSourceScale(app.lock);
      app.tracker = createTracker(app.model, {
        smooth: app.smooth,
        // The sigma stream main feeds track() is in SOURCE pixels; the floor
        // must be too, or every clean frame reads as 2x noise.
        adaptiveFloorPx: UNCERTAINTY_DEFAULTS.floorPx * scale,
        rigidity: trackingRigidity(app.mesh, app.regions),
        motionPrior: app.motionPrior,
        ovalStrips: app.marchOval && app.model
          ? silhouetteStrips(app.mesh, app.model.positions) : null,
      });
      // The A/B instrument must not mix modes: a paste taken minutes into
      // 'locked' with 'on' frames still in the ring would judge one mode by
      // the other's numbers.
      app.trackStats = [];
      const btn = document.querySelector('[data-action="steady"]');
      const label = app.smooth === 'locked' ? 'locked' : app.smooth ? 'on' : 'off';
      if (btn) btn.textContent = `Steady: ${label}`;
      app.ui.status(app.smooth === 'locked'
        ? 'stillness latch v2 — dead still at rest; real motion releases through a short crossfade.'
        : app.smooth
          ? 'fixed smoothing — One Euro at the tuned constants.'
          : 'pose smoothing off — raw PnP per frame.');
      break;
    }
    case 'edge': {
      app.edgeSnap = !app.edgeSnap;
      if (!app.edgeSnap && app.model) {
        app.scene.nudgeOccluder(new Float64Array(app.mesh.vertexCount * 3));
      } else if (app.edgeSnap && app.snapField) {
        app.scene.nudgeOccluder(app.snapField.applied);
      }
      const edgeBtn = document.querySelector('[data-action="edge"]');
      if (edgeBtn) edgeBtn.textContent = `Edge: ${app.edgeSnap ? 'on' : 'off'}`;
      app.ui.status(app.edgeSnap
        ? 'edge snap on — the occlusion boundary follows the real nose edge in the video.'
        : 'edge snap off — pure geometric occluder, for comparison.');
      break;
    }
    case 'rank': {
      if (!app.model) return;
      // Everything the picker offers, not just the parametric five — a ranked
      // row is a button, and ranking a frame the click handler cannot resolve
      // produces a dead button.
      // Ranked NEXT TO the frame on the face, not against an absolute target.
      // The width verdict then compares two frames whose widths are both known
      // to the millimetre, and the scan's scale — the thing no prop-free ruler
      // delivers better than about 4.7% — cancels out of the difference
      // exactly. It is not an endorsement of what they are wearing: a
      // similarity ordering does not require the reference to fit, which is
      // just as well, because they have not said that it does. See
      // `rankCatalogue` for what this fixes and, more to the point, what it
      // does not.
      const ranked = rankCatalogue(app.model, app.mesh, app.regions, [
        ...TEST_FRAMES, ...[...app.meshFrames.values()].map((m) => m.asset),
      ], app.frame);
      app.ui.catalogue(ranked, app.frame.name);
      break;
    }
    case 'diagnostics': {
      const report = JSON.stringify(collectDiagnostics({
        phase: app.phase,
        fps: app.fps,
        loopDriver: app.loopDriver,
        backend: app.scene.backendName,
        workerAvailable: app.enroller.available,
        // Where the last solve ACTUALLY ran, which is the only one of the three
        // that is a measurement rather than a prediction. A worker that died
        // mid-solve, or one that took longer than the solve timeout, produces
        // `workerAvailable: false` beside `solvedOn: 'main'` — and a solve that
        // fell back while the worker stayed healthy produces `true` beside
        // 'main', which is a different fault and used to be invisible.
        solvedOn: app.enroller.lastRanOn,
        lock: app.lock,
        source: app.source,
        protocol: app.protocol,
        model: app.model,
        seat: app.seat,
        assessment: app.assessment,
        steady: app.smooth === 'locked' ? 'locked'
          : app.smooth === 'adaptive' ? 'adaptive'
            : app.smooth ? 'on' : 'off',
        tracker: app.tracker,
        motionPrior: app.motionPrior,
        marchOval: app.marchOval,
        recentTrack: app.trackStats,
        recentDetectMs: app.detectMs,
      }), null, 2);
      console.log(report);
      app.ui.showDiagnostics(report);
      navigator.clipboard?.writeText(report).then(
        () => app.ui.status('diagnostics copied to the clipboard'),
        () => app.ui.status('diagnostics shown below — already selected, press ctrl+C'),
      );
      break;
    }
    case 'save-capture': {
      // **The only way a real face reaches the harness.** Everything the
      // estimator is measured against today is synthetic, and
      // `docs/HANDOFF.md` records a 6.7 mm PD disagreement across three
      // captures of one person that no synthetic population can settle.
      if (!app.lastCapture || app.lastCapture.length === 0) {
        app.ui.status('nothing to save yet — finish a scan first');
        break;
      }
      const pd = app.knownPdMm;
      const capture: Capture = {
        header: {
          v: 1,
          subject: 'wearer',
          date: new Date().toISOString().slice(0, 10),
          width: app.source?.width ?? 0,
          height: app.source?.height ?? 0,
          intrinsics: app.intrinsics,
          intrinsicsSolved: app.model?.intrinsicsSolved ?? false,
          knownPdMm: pd,
          // The wearer says whether a card was in frame; nothing here can tell.
          card: /[?&]card=1/.test(location.search),
          note: `${app.source?.label ?? 'unknown source'}; ${summarise(app.protocol)}`,
          frames: app.lastCapture.length,
        },
        frames: app.lastCapture,
      };
      const text = serializeCapture(capture);
      const blob = new Blob([text], { type: 'application/x-ndjson' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `capture-${capture.header.date}.ndjson`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Revoked on the next turn of the loop, not immediately: revoking before
      // the download has started cancels it in some browsers.
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      app.ui.status(
        `saved ${capture.frames.length} frames`
        + (pd === null ? ' — no PD set, so this capture cannot settle scale' : ` with PD ${pd} mm`),
      );
      break;
    }
    case 'forget':
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem(HISTORY_KEY);
      localStorage.removeItem(PD_KEY);
      app.knownPdMm = null;
      app.ui.status('your measurements have been deleted from this device');
      break;
    default:
      if (action.startsWith('frame:')) {
        const id = action.slice('frame:'.length);
        // Last click wins, and it also stops the background preload installing
        // its own choice over the wearer's.
        app.wantedFrameId = id;
        const mesh = app.meshFrames.get(id);
        if (mesh) { fitFrame(app, mesh.asset, mesh.object); break; }
        const frame = TEST_FRAMES.find((f) => f.id === id);
        if (frame) fitFrame(app, frame);
      }
  }
}

function renderReadouts(app: App): void {
  app.ui.readouts({
    fps: app.fps,
    brightness: app.lock.brightness,
    mirrorDelayMs: app.lock.mirrorDelayMs,
    droppedFrames: app.source?.droppedFrames ?? 0,
    backend: app.scene.backendName
      + (app.loopDriver === 'timer' ? ' · timer loop' : '')
      // Either "there is no worker" or "the last solve did not use it". The
      // second used to be unsayable, so a scan that fell back inline from a
      // still-live worker looked exactly like one that had not.
      + (app.enroller.available && app.enroller.lastRanOn !== 'main' ? '' : ' · inline solve'),
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
