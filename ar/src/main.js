/**
 * Wiring, around one rule: **the face and the glasses share a single clock.**
 *
 * Per camera frame:
 *   snapshot the frame -> detect on the snapshot -> when the result returns,
 *   show that same snapshot and the pose it produced, together
 *
 * This is video AR, not see-through AR, and the distinction decides the whole
 * architecture. In see-through AR the wearer sees the world at zero latency, so the
 * pose must be *predicted* to display time or the virtual object trails the real
 * one. Here the user sees a **video** of their face — already 30-80 ms old by the
 * time it is on screen — so the only alignment that matters is between the glasses
 * and *that video*, and the way to get it is not prediction but a frame lock:
 * detect on exactly the pixels that will be shown, and show them only with their
 * own pose. The composite is then exact at every velocity, by construction, and
 * the pipeline's entire latency collapses into the mirror running a few hundredths
 * of a second behind the room — which nobody can see. The previous architecture
 * showed the newest camera frame while posing the glasses from an older, detected
 * one extrapolated forward to "now": two clocks, and every head movement visibly
 * sheared the glasses across the face. Prediction was not the cure for that slip;
 * it was the cause.
 *
 * The display therefore steps at the detection rate — the camera's rate when the
 * tracker keeps up — like the slightly-delayed 30 fps mirror every commercial
 * try-on ships, because they all hit this same fork and chose the same tine.
 *
 * The placement of the frame on the head is not in the loop at all. It is solved in
 * the canonical face model's space (see `fit.js`), and the head node's matrix
 * carries it onto the real face. The only reason `solvePlacement` runs per
 * detection is that physical sizing needs the current head-scale estimate; it is a
 * handful of vector operations, not a search.
 */

import { loadCanonicalFace, LM } from './canonical-face.js';
import { MODELS, DEFAULT_MODEL, findModel, loadGlassesModel } from './models.js';
import { createTrackerClient, DETECT_LONG_SIDE } from './tracker.js';
import { createScene, createAnchorDebug } from './scene.js';
import {
  createOccluder, createShadowCatcher, createOccluderDebugMesh, OCCLUDER_FEATHER,
} from './occluder.js';
import { PAD_SINK } from './nose.js';
import { createOcclusionMask, installOcclusionMask } from './occlusion-mask.js';
import { createLightProbe, lightingFor, softenTint } from './lighting.js';
import { analyseModel, DEFAULT_FIT, FIT_MODES } from './fit.js';
import {
  updateFrame, remeasure, noteFacelessResult, HOLD_FACELESS_RESULTS,
  LOST_SECONDS_BEFORE_RESET,
} from './frame.js';
import { fitRect } from './layout.js';
import { prepareTemples } from './temples.js';
import { PoseSmoother, DEFAULT_SMOOTHING } from './smoothing.js';
import { createStabMeter, screenPointOf } from './stab.js';
import { createCameraSource, createSampleSource } from './sources.js';
import { createControls, createReadout } from './ui.js';

const SOURCES = [
  { value: 'camera', label: 'Camera (live)' },
  { value: '../assets/samples/face-a.jpg', label: 'Sample face A' },
  { value: '../assets/samples/face-b.jpg', label: 'Sample face B' },
];

const dom = {
  stageFit: document.getElementById('stageFit'),
  stage: document.getElementById('stage'),
  canvas: document.getElementById('gl'),
  overlay: document.getElementById('overlay'),
  status: document.getElementById('status'),
  controls: document.getElementById('controls'),
  stats: document.getElementById('stats'),
};

const state = {
  face: null,
  scene: null,
  tracker: null,
  source: null,
  model: null,
  modelRoot: null,
  /** The catalogue entry actually on screen, which a failed load must not change. */
  modelValue: null,
  background: null,
  occluder: null,
  /** The soft-occlusion pre-pass. See `occlusion-mask.js`. */
  occlusionMask: null,
  faceDebug: null,
  anchorDebug: null,
  smoother: new PoseSmoother(DEFAULT_SMOOTHING),
  /**
   * The live stillness meter (anchoring-v3 R0) — `__ar.stab` is its readout:
   * trailing-5 s screen RMS + worst step of the composed glasses origin,
   * gated on pose stillness (< 2 cm/s, < 5°/s). See `stab.js`. This is the
   * number read out loud during the live capture protocol.
   */
  stabMeter: createStabMeter(),
  get stab() { return this.stabMeter.readout; },
  anchors: null,
  /**
   * The bounded window of recent head-on samples the fit stands on. See `updateFrame`
   * and `medianAnchors`. There is no scan phase and no lock — the first sample is
   * already a fit, and the window keeps running for the whole session.
   */
  sampleSet: null,
  anchorSamples: 0,
  temples: null,
  templesAimed: false,
  fit: { ...DEFAULT_FIT },
  lastFrameMs: 0,
  fps: 0,
  /** Detections per second, which is the camera's rate and not the display's. */
  trackFps: 0,
  /**
   * When the last delivered frame's pixels were taken, on `performance.now()`'s
   * clock. The interval between two of these is the camera's own rate.
   */
  frameAtMs: 0,
  /** Smoothed camera frame interval, ms — what the tracker fallback compares against. */
  cameraIntervalMs: 0,
  /** When the last frame actually *submitted* for detection was captured. Frames
   * dropped while the tracker is busy do not advance this, so the interval between
   * two submissions is the true measurement interval the estimator must be told. */
  lastSubmitMs: 0,
  /** When the last detection result landed, for the tracking-rate readout. */
  lastResultMs: 0,
  /** Counts source switches, so a detection in flight across one is recognisably
   * stale — a result solved on the previous source must not pose the new one. */
  sourceEpoch: 0,
  /**
   * The frame-locked display surface: the pixels of the frame whose detection was
   * shown last. This canvas IS the scene's background — see the snapshot note in
   * `tick`.
   */
  frameCanvas: document.createElement('canvas'),
  frameCtx: null,
  /**
   * The frame currently *under* detection, copied at submission. Separate from the
   * display canvas, and the separation is the frame lock's missing half: with one
   * canvas, the next submission overwrote the pixels before the render displaying
   * the previous result had uploaded them — so in steady state every composite
   * showed pixels one camera frame newer than the pose driving the occluder, ~33 ms
   * of shear that only appeared while the head moved. The result's pixels now blit
   * from here into `frameCanvas` at the moment the result lands, so what is shown is
   * always the frame its own pose was solved on.
   */
  captureCanvas: document.createElement('canvas'),
  captureCtx: null,
  /**
   * The same snapshot, scaled down to what the landmarker actually looks at.
   *
   * Drawn *from* `frameCanvas` rather than from the source, so the frame lock is
   * untouched: these are the identical pixels the composite will show, just fewer of
   * them. The landmarker resamples to 256x256 internally whatever it is handed (see
   * `DETECT_LONG_SIDE`), so the full-resolution copy, transfer and texture upload it
   * used to receive were paid on every frame and thrown away inside the model —
   * measured at 0.42 mm of landmark disagreement for 61% fewer pixels.
   */
  detectCanvas: document.createElement('canvas'),
  detectCtx: null,
  /**
   * The rotation applied to the detection snapshot before MediaPipe sees it, in
   * radians of canvas rotation about its centre.
   *
   * This is what makes a lying-down wearer trackable at all. MediaPipe's landmarker
   * is trained on roughly upright faces and degrades badly past ~40° of roll — the
   * pose skews, the landmarks smear, and the glasses land wherever the wreckage
   * says, which is exactly the in-repo diag captures (a wearer on a pillow) and
   * exactly the complaint. So the detect copy — and ONLY the detect copy; the
   * frame-locked display is untouched — is rotated to stand the face upright, and
   * everything the detector returns is rotated back before a single consumer sees
   * it. The landmarker works in its comfort zone at any roll, and the rest of the
   * pipeline never learns the image it reasons about was ever rotated.
   *
   * Re-aimed from the measured eye line with a deadband (see `onDetection`), so it
   * changes rarely — the landmarker carries ROI state between calls, and feeding it
   * a rotation that churns every frame would measure state churn, not faces.
   */
  detectRotation: 0,
  /** Index into the acquisition search — see the faceless branch of `onDetection`. */
  rollSearch: 0,
  /** How far the mirror runs behind the room, in ms — capture to composite, measured. */
  mirrorDelayMs: 0,
  /** False when the source cannot say when a frame was captured. See `sources.js`. */
  latencyMeasured: true,
  // null rather than false, so the first frame always writes the readout.
  detected: null,
  switching: false,
  /** How long the face has been out of frame, in seconds. See `noteFaceLost`. */
  lostSeconds: 0,
  /**
   * Consecutive detections that came back with no face in them.
   *
   * Drives the dropout hold in `onDetection`: a face that vanishes for one or two
   * frames is almost always a motion-blurred frame or a blink, and popping the
   * glasses off for 60 ms costs more than showing the previous composite for 60 ms.
   */
  facelessResults: 0,
  /**
   * When the last detection the pose filter actually consumed was captured.
   *
   * Distinct from `lastSubmitMs`, and the distinction is a bug the dropout hold
   * used to cause: submissions keep flowing through a faceless burst, so on the
   * first face afterwards `captureDt` reports one frame's interval while the head
   * has been moving unobserved for four or five. See the recovery note in
   * `onDetection`.
   */
  lastAppliedDetectionMs: 0,
  /** Every dropout recovery and the dt it was fed — `__ar.recovery`. */
  recovery: { events: 0, lastDtUsedMs: null, lastGapMs: null },
  /** The pose filters' noise-floor state, live — `__ar.noise`. See `NoiseFloor`. */
  get noise() {
    return {
      positionFloor: this.smoother.position.noise.floor,
      rotationFloor: this.smoother.rotation.noise.floor,
      positionActivityEff: this.smoother.position.activityEff,
      rotationActivityEff: this.smoother.rotation.activityEff,
    };
  },
  /**
   * The rebuild cadence, live — `__ar.rebuilds`, extended at stage 4. The
   * counters live on the occluder (they are its cost); this view adds the
   * per-minute rate over the last 1800 processed frames (~a minute at 30 fps)
   * and the cause of the last surface rebuild, so a cadence regression is
   * visible without the harness. (`__ar.person` is `state.person` itself —
   * the model exposes meanW/noseMeanW/zConfBridge/residualRmsMm/commits/
   * resets/tripwireActive/crossfadeOn as plain fields.)
   */
  get rebuilds() {
    const data = this.occluder?.userData;
    if (!data) return null;
    const marks = data.rebuilds.marks ?? [];
    const horizon = data.frameCount - 1800;
    let recent = 0;
    for (let i = marks.length - 1; i >= 0 && marks[i] > horizon; i--) recent++;
    return {
      surface: data.rebuilds.surface,
      profile: data.rebuilds.profile,
      surfacePerMin: Math.min(data.frameCount, 1800) > 0
        ? Math.round((recent * 1800) / Math.min(data.frameCount, 1800)) : 0,
      lastCause: data.rebuilds.lastCause,
    };
  },
  /**
   * Camera frames the browser presented but never handed us, counted from rVFC's
   * `presentedFrames`. This is the number that distinguishes "the pipeline is slow"
   * from "the pipeline is dropping input" — the literature is clear that dropouts
   * hurt perceived quality much more than a uniformly lower rate does, and until
   * this existed the two were indistinguishable from inside the app.
   */
  droppedFrames: 0,
  /** What the camera actually gave us, as opposed to what was asked for. */
  cameraSettings: null,
  /**
   * Capture-to-callback, in ms: how much of the mirror delay happens before any of
   * our code runs. Everything downstream is ours to fix; this part is not.
   */
  upstreamMs: 0,
  /**
   * Whether `upstreamMs` is a reading at all.
   *
   * Separate from the value because zero is a legitimate answer — a sample source
   * has no sensor and no transport, so none of its delay is upstream — and a
   * browser that will not report `captureTime` gives no answer at all. Those two
   * must not print the same thing.
   */
  upstreamMeasured: false,
};

/**
 * Diagnostics, off the query string.
 *
 * `?tracker=main|worker` pins where inference runs. `?faces=1` turns MediaPipe's own
 * landmark smoothing back *on* (see `DEFAULT_NUM_FACES`) — the app ships with it off
 * because it was a large, unmeasurable source of lag, and this is the A/B that
 * decided it.
 */
const params = new URLSearchParams(location.search);

const setStatus = (text, tone = 'info') => {
  dom.status.textContent = text ?? '';
  dom.status.dataset.tone = tone;
  dom.status.hidden = !text;
};

/**
 * Clears the status unless it is currently carrying an error.
 *
 * Progress messages are noise that can be dropped at any time; an error is the only
 * account the user gets of something that failed, and it took a real failure to put
 * it there. Anything that finishes successfully afterwards clears it deliberately.
 */
const clearTransientStatus = () => {
  if (dom.status.dataset.tone !== 'error') setStatus('');
};

// ---------------------------------------------------------------- boot

async function boot() {
  setStatus('Loading face model…');
  state.face = await loadCanonicalFace();

  state.scene = createScene(dom.canvas);

  state.occluder = createOccluder(state.face);
  state.scene.head.add(state.occluder);

  // The face receives the frame's shadow — the contact cue that attaches the
  // glasses to the face instead of floating them in front of it. Built from the
  // occluder, whose surface it has to share exactly.
  state.scene.head.add(createShadowCatcher(state.occluder));

  // The soft boundary. Created before any model loads, because every material the
  // catalogue produces has to be patched with the same uniform objects and there is no
  // second chance once a program is compiled.
  state.occlusionMask = createOcclusionMask(state.scene.renderer);
  state.scene.setOcclusionMask(state.occlusionMask);

  // The head the occluder is actually using, not the average one it used to draw.
  state.faceDebug = createOccluderDebugMesh(state.occluder);
  state.scene.head.add(state.faceDebug);

  state.anchorDebug = createAnchorDebug(state.face, [
    LM.NOSE_BRIDGE, LM.NASION, LM.TEMPLE_R, LM.TEMPLE_L, LM.EAR_TOP_R, LM.EAR_TOP_L,
  ]);
  state.scene.head.add(state.anchorDebug);

  // `?tracker=main` or `?tracker=worker` pins the mode — the escape hatch, and how
  // the harness and a curious user compare the two on their own machine.
  const pinned = params.get('tracker');
  const faces = Number(params.get('faces'));
  state.tracker = await createTrackerClient({
    lockMode: pinned === 'main' || pinned === 'worker' ? pinned : null,
    numFaces: faces > 0 ? faces : undefined,
    onStatus: (t) => setStatus(`${t}…`),
    onResult: onDetection,
    onModeChange: (mode, why) => {
      console.info(`tracker: ${mode} (${why})`);
      readout?.set('tracker', mode);
    },
  });

  // The glasses load in parallel with everything else. The default model is a 24 MB
  // scan, and serialised before the camera it put a download between the user and
  // their own face — the scan phase needs a face, not a frame catalogue, so tracking
  // starts now and the glasses appear the moment they arrive.
  loadModel(DEFAULT_MODEL).catch((error) => {
    console.error(error);
    setStatus(`Could not load the glasses: ${error.message}`, 'error');
  });

  buildUI();
  readout.set('tracker', state.tracker.mode);

  // The sample face is the default so the pipeline proves itself before asking
  // anyone for camera permission.
  await useSource(SOURCES[1].value);

  // Not an unconditional clear: if the sample failed to load, that error is the only
  // thing telling anyone why the stage is empty.
  clearTransientStatus();
  requestAnimationFrame(tick);
}

// ---------------------------------------------------------------- model

/**
 * Counts model load requests, so a superseded one can recognise itself.
 *
 * Two rapid selections put two loads in flight, and they finish in whatever order
 * the files happen to parse in — not the order they were asked for. Without this the
 * last load to *resolve* installs itself, so a quick flick through the catalogue can
 * leave the wrong frame on the face while the select shows the right one, and every
 * fit readout then describes an entry the user is not looking at.
 */
let modelRequests = 0;

async function loadModel(value) {
  const request = ++modelRequests;
  const entry = findModel(value);
  const root = await loadGlassesModel(entry, import.meta.url);

  if (request !== modelRequests) {
    // A later selection overtook this one. It was never added to the scene, so
    // dropping it is just releasing what the loader allocated.
    disposeTree(root);
    return;
  }

  const analysis = analyseModel(root);
  if (!analysis.orientationLooksSane) {
    console.warn(
      'Model is taller than it is wide — it may not follow the glTF convention of ' +
      '+Y up and +Z forward. Placement assumes it does.',
      analysis,
    );
  }

  if (state.modelRoot) {
    state.scene.glasses.remove(state.modelRoot);
    disposeTree(state.modelRoot);
  }

  // Give the arms their own hinges before anything is rendered, so they can be
  // aimed at the ears independently of the front's tilt.
  state.temples = prepareTemples(root);
  if (!state.temples) {
    console.warn('No temple arms found in this model — the arms will not be fitted to the ears.');
  }

  // Every part of the frame casts the contact shadow onto the face except the ones
  // that are genuinely *clear glass*, because three's shadow pass has no notion of
  // transmission at all. `getDepthMaterial` branches on clipping planes, displacement
  // maps, alpha maps and alpha test, and on nothing else, so a 92%-transmissive rim
  // was laying down the same shadow a block of wood would. That put the darkest thing
  // about the clear frame underneath it: the grey slab left the bridge and came back
  // as a stripe across the nose.
  //
  // "Clear" has to be all three of these, and testing only the first was too blunt —
  // it silently un-shadowed a whole *sunglass*:
  //
  //  - **highly transmissive.** A translucent acetate at 0.5 stops half the light and
  //    should ground the frame on the face like any other solid.
  //  - **essentially colourless.** This is the one that matters most. A tinted lens
  //    passes light and still casts a strong shadow — that is what makes sunglasses
  //    sunglasses. Transmission alone called an 18%-luminance amber lens "glass".
  //  - **uniformly so.** A `transmissionMap` exists precisely because the part is not
  //    uniformly transparent; the sage frame declares a factor of 1 and then modulates
  //    it to about 0.5 through a texture, so the factor on its own is a lie about it.
  //
  // Get it wrong the other way and the whole frame stops casting: the sage acetate is
  // a single translucent material covering every part of the frame, so it went from a
  // 1.4% contact shadow to 0.09% and read as pasted onto the face.
  const luminance = (c) => (c ? 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b : 1);
  const clearGlass = (m) => m?.transmission > 0.8
    && !m.transmissionMap
    && luminance(m.color) > 0.8;
  root.traverse((node) => {
    if (!node.isMesh) return;
    node.castShadow = ![].concat(node.material).some(clearGlass);
  });

  state.scene.glasses.add(root);
  // Any glass the asset declared gets its own handle on the environment, without
  // which its reflection is pinned to whatever suits the diffuse lighting. See
  // `attachGlassEnvironment`.
  state.scene.attachGlassEnvironment(root);

  // Last, after every other pass over this model's materials.
  //
  // Order is load-bearing rather than tidy. `installOcclusionMask` composes itself onto
  // whatever `onBeforeCompile` each material already carries, so anything that
  // *replaces* a material — `prepareDeclaredGlass` rebuilding a physical chain,
  // `prepareTemples` installing its depth fade — has to have finished first, or the
  // replacement drops the mask and that part of the frame is never occluded at all.
  const masked = installOcclusionMask(root, state.occlusionMask.uniforms);
  console.info(`occlusion mask on ${masked} material${masked === 1 ? '' : 's'}`);
  state.modelRoot = root;
  state.model = analysis;
  state.modelValue = entry.value;
  state.templesAimed = false;
  // A different pair has a different resting configuration — its own pad
  // separation, its own equilibrium height; the seat state starts over rather
  // than dragging the old pair's solve onto the new geometry. (The scheduler
  // would also catch the swap as a model-change event, but a fresh state is
  // the honest semantics: nothing of the old pair's easing survives.)
  state.seatConfig = null;

  console.info('model measured', {
    widthMm: +(analysis.widthM * 1000).toFixed(1),
    noseContactM: analysis.noseContact.toArray().map((n) => +n.toFixed(4)),
    vertices: analysis.vertexCount,
  });
}

function disposeTree(root) {
  root.traverse((node) => {
    if (!node.isMesh) return;
    node.geometry?.dispose();
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    for (const material of materials) {
      if (!material) continue;
      // `Material.dispose()` releases the shader program but not the textures the
      // material points at — those are separate GPU allocations with their own
      // lifetimes. The sample .glb keeps its authored materials and their texture
      // set, so without this every swap back to it orphans another copy in GPU
      // memory. Walking the material's own properties rather than a list of known
      // map names keeps this correct as three adds more of them.
      for (const value of Object.values(material)) {
        if (value?.isTexture && value !== state.scene?.scene.environment) value.dispose();
      }
      material.dispose();
    }
  });
}

// ---------------------------------------------------------------- source

/**
 * The most recent source asked for while a switch was already running.
 *
 * Dropping it instead is not harmless. The guard's window spans `getUserMedia`,
 * which is a permission prompt the user takes as long as they like over, and the
 * select is live throughout — so a change made during the wait would vanish, leaving
 * the control describing a source that never started. Re-picking the option it
 * already shows fires no change event, so there is no way back short of flipping to
 * a third option and returning.
 */
let queuedSource = null;

async function useSource(value) {
  if (state.switching) {
    queuedSource = value;
    return;
  }

  state.switching = true;
  try {
    await startSource(value);
  } finally {
    state.switching = false;
  }

  if (queuedSource !== null) {
    const next = queuedSource;
    queuedSource = null;
    if (next !== value) await useSource(next);
  }
}

async function startSource(value) {
  try {
    state.source?.stop();
    state.source = null;

    if (value === 'camera') {
      setStatus('Requesting camera…');
      state.source = await createCameraSource({ onLost: reportCameraLost });
      controls?.set('mirror', true);

      // What the camera agreed to, not what was asked for. This is the ceiling on
      // everything downstream — a track that settled at 15 fps because the room is
      // dark makes every tracking optimisation below it pointless, and there is no
      // other way to find that out. Logged rather than shown: it is the first thing
      // to ask for when a session on someone else's machine feels slow.
      state.cameraSettings = state.source.settings;
      if (state.cameraSettings) {
        const { width, height, frameRate } = state.cameraSettings;
        console.info(`camera: ${width}x${height} @ ${frameRate ?? '?'} fps`, state.cameraSettings);
      }
    } else {
      setStatus('Loading sample…');
      state.source = await createSampleSource(new URL(value, import.meta.url).href, {
        drift: controls?.values.drift ?? false,
      });
      // A photograph is not a mirror; flipping it would just be wrong.
      controls?.set('mirror', false);
    }

    applyMirror();

    // The scene's background is the frame-locked snapshot canvas, not the source
    // element. A live element as the background is what desynchronised the app: the
    // texture pulled the newest camera frame at 60 Hz while the glasses were posed
    // from an older, detected one — two clocks, visibly shearing apart on every head
    // movement. The snapshot canvas holds exactly the frame the current pose was
    // solved on, so face and glasses advance together or not at all.
    state.frameCanvas.width = state.source.width;
    state.frameCanvas.height = state.source.height;
    state.frameCtx = state.frameCanvas.getContext('2d');
    state.captureCanvas.width = state.source.width;
    state.captureCanvas.height = state.source.height;
    state.captureCtx = state.captureCanvas.getContext('2d');

    // The detection copy, capped on its long side. Never enlarged — a source already
    // smaller than the cap is handed over as it is, rather than upscaled into extra
    // pixels that carry no extra face.
    const shrink = Math.min(1, DETECT_LONG_SIDE / Math.max(state.source.width, state.source.height));
    state.detectCanvas.width = Math.round(state.source.width * shrink);
    state.detectCanvas.height = Math.round(state.source.height * shrink);
    state.detectCtx = state.detectCanvas.getContext('2d');
    // Show something at once rather than a black stage until the first detection —
    // and for a sample, draw the source's own canvas first: it paints lazily.
    state.source.update(performance.now());
    state.frameCtx.drawImage(state.source.element, 0, 0);
    state.background = state.scene.setBackground(state.frameCanvas);
    // The boundary snap reads these very pixels. It is the frame-locked snapshot, not
    // the live element, which is the whole point: the edge it snaps to has to belong to
    // the same frame the pose was solved from, or it would chase a nose the glasses are
    // not on yet.
    state.occlusionMask?.setCameraTexture(state.background);
    state.smoother.reset();
    // A new source is a new geometry; a stillness window spanning the switch
    // would read the source change as one enormous step.
    state.stabMeter.reset();
    // The new source has its own frame clock, and so does the loop: an interval
    // measured across the switch would be however long the permission prompt was on
    // screen, on both of them. The epoch bump is what retires a detection still in
    // flight — its result describes pixels from the source that just went away.
    state.sourceEpoch += 1;
    state.frameAtMs = 0;
    state.lastFrameMs = 0;
    state.lastSubmitMs = 0;
    state.lastAppliedDetectionMs = 0;
    state.lastResultMs = 0;
    state.cameraIntervalMs = 0;
    // Counters describing the *input* belong to the input, so they start over with it
    // — a drop count carried across a switch is attributed to the wrong camera.
    state.droppedFrames = 0;
    state.upstreamMs = 0;
    state.upstreamMeasured = false;
    state.facelessResults = 0;
    // A new source is a new orientation problem; start looking upright.
    state.detectRotation = 0;
    state.rollSearch = 0;
    // A new source is a new face, and the old measurements are wrong rather than
    // merely stale. This is one of the two places `remeasure` is still called; the
    // other is the button. Losing the face is emphatically not one of them.
    remeasure(state);
    // And a new scene means the old light is wrong too — re-prime from the first
    // sample rather than easing out of the previous room over several seconds.
    resetLightAdaptation();
    setStatus('');
  } catch (error) {
    console.error(error);

    if (value === 'camera') {
      // Fall back first and report second. The other way round — which is the
      // obvious way to write it — loses the message entirely: the fallback's own
      // 'Loading sample…' overwrites it within the same task, and its success path
      // then clears the bar, so the user watches the source flip back with no
      // account of why the camera did not start.
      controls?.set('source', SOURCES[1].value);
      await startSource(SOURCES[1].value);
      setStatus(`Camera unavailable: ${error.message} — falling back to a sample face.`, 'error');
      return;
    }

    setStatus(`Could not load source: ${error.message}`, 'error');
  }
}

/**
 * The camera stopped delivering frames after it had started.
 *
 * Deliberately does not switch to a sample. The frozen frame is the last thing the
 * camera actually saw, and replacing it with a stock portrait would bury the one
 * fact worth reporting; the head is hidden so nothing keeps claiming to track a face
 * that is no longer being seen.
 */
function reportCameraLost() {
  state.scene.head.visible = false;
  state.scene.render();
  state.detected = null;
  readout?.set('face', 'camera lost');
  setStatus('The camera stopped delivering frames — it may have been unplugged, or its '
    + 'permission revoked. Pick a source to carry on.', 'error');
}

// ---------------------------------------------------------------- frame loop

function tick(nowMs) {
  requestAnimationFrame(tick);

  const source = state.source;
  // Every early return below has to forget the render clock, or the gap it is
  // returning across arrives as one enormous frame interval in the fps figure on
  // the far side.
  if (!source) {
    state.lastFrameMs = 0;
    return;
  }

  source.update(nowMs);
  if (!source.ready || source.width === 0) {
    state.lastFrameMs = 0;
    return;
  }

  // No layout means no camera. `syncSize` is what configures the projection to match
  // the source, so measuring anything through it before then would unproject rays
  // against a placeholder aspect ratio — and the fit locks after 45 frames, which is
  // well under a second, so it would lock onto whatever that produced and hold it
  // long after the layout recovered.
  if (!syncSize()) {
    state.lastFrameMs = 0;
    return;
  }

  const renderDt = state.lastFrameMs ? (nowMs - state.lastFrameMs) / 1000 : 1 / 60;
  state.lastFrameMs = nowMs;
  state.fps += ((1 / Math.max(renderDt, 1e-4)) - state.fps) * 0.1;

  // New pixels, or nothing? Only the camera knows, and asking it is what keeps the
  // landmarker off frames it has already seen.
  const frame = source.nextFrame(nowMs);

  if (frame) {
    // The camera's own rate, measured per *delivered* frame whether or not this one
    // is submitted — it is what the tracker's fallback compares its cost against.
    const arrivalDt = state.frameAtMs ? (frame.capturedAtMs - state.frameAtMs) / 1000 : 1 / 30;
    state.frameAtMs = frame.capturedAtMs;
    state.cameraIntervalMs = state.cameraIntervalMs
      ? state.cameraIntervalMs + (arrivalDt * 1000 - state.cameraIntervalMs) * 0.1
      : arrivalDt * 1000;
    state.tracker.noteCameraInterval(state.cameraIntervalMs);
    state.droppedFrames = source.droppedFrames ?? 0;

    // Offer the frame; the tracker declines while a detection is still in flight,
    // and a declined frame is dropped whole — never shown, never tracked.
    //
    // The snapshot is the heart of the architecture. The frame's pixels are copied
    // *now*, at submission, into the canvas the scene uses as its background — and
    // that background is only marked dirty when this frame's own detection returns.
    // So the face on screen and the glasses on it always come from the same pixels:
    // the composite is exact by construction, at every velocity, and the pipeline's
    // whole latency shows up only as the mirror running a few hundredths of a
    // second behind the room — which is invisible — instead of as glasses sliding
    // across a fresher face — which was the app.
    if (!state.tracker.busy) {
      const captureDt = state.lastSubmitMs
        ? (frame.capturedAtMs - state.lastSubmitMs) / 1000
        : 1 / 30;
      // Into the capture canvas, not the display one: the display keeps showing the
      // previous result's own pixels until this frame's result arrives to join them.
      state.captureCtx.drawImage(source.element, 0, 0);
      // Scaled from the snapshot, not from the source: same pixels, fewer of them.
      // Taking it from the live element instead would open a window in which the
      // video could present a new frame between the two draws — the pose would then
      // describe an image the composite never shows, which is the one thing the frame
      // lock exists to prevent.
      //
      // Rotated upright when the head is rolled — see `detectRotation`. About the
      // canvas centre, which is also the projection's principal point, so undoing it
      // downstream is a pure rotation with no perspective term.
      const rotation = state.detectRotation;
      const dw = state.detectCanvas.width;
      const dh = state.detectCanvas.height;
      if (rotation) {
        state.detectCtx.fillStyle = '#000';
        state.detectCtx.fillRect(0, 0, dw, dh);
        state.detectCtx.save();
        state.detectCtx.translate(dw / 2, dh / 2);
        state.detectCtx.rotate(rotation);
        state.detectCtx.drawImage(state.captureCanvas, -dw / 2, -dh / 2, dw, dh);
        state.detectCtx.restore();
      } else {
        state.detectCtx.drawImage(state.captureCanvas, 0, 0, dw, dh);
      }
      if (state.tracker.submit(state.detectCanvas, {
        timestampMs: frame.timestampMs,
        capturedAtMs: frame.capturedAtMs,
        measuredCapture: frame.measuredCapture,
        // Forwarded explicitly, like everything else here. This object is rebuilt
        // rather than passed through because it crosses to a worker and back, and a
        // field left out of it is not a compile error — it is a readout that reads
        // "—" forever while the measurement it describes is being taken correctly
        // one function away.
        upstreamMs: frame.upstreamMs ?? null,
        captureDt,
        epoch: state.sourceEpoch,
        // The rotation THIS frame was drawn with, so the result is undone with the
        // same angle even if the aim has moved by the time it returns.
        rotation,
      })) {
        state.lastSubmitMs = frame.capturedAtMs;
      }
    }
  }

  // Renders between results change nothing about the composite — same frame, same
  // pose — but they keep the sliders live: a placement control moves the glasses on
  // the frozen frame immediately rather than on the next detection.
  present();
}

/**
 * A detection result landing — this is the moment the display advances.
 *
 * The frame the result was solved on is already sitting in `frameCanvas` (copied at
 * submission), so showing the two together is one dirty flag and one placement. The
 * video therefore steps at the *detection* rate, in lockstep with the pose — which
 * is the point. The old loop showed the newest camera frame at 60 fps and posed the
 * glasses from an older, detected one extrapolated forward: two clocks, and every
 * head movement sheared them apart. One clock, no shear.
 */
// The dropout hold itself — how many faceless results are ridden out, and the
// lost-clock bookkeeping that runs through them — lives in `frame.js` now
// (`HOLD_FACELESS_RESULTS`, `noteFacelessResult`), where the harness can drive
// it. What stays here is what only a browser has: the roll-search re-aim and
// the composite of the held frame.

/**
 * How far the measured roll may drift from the applied detect rotation before the
 * rotation is re-aimed, in radians (~15°).
 *
 * A deadband rather than continuous tracking, for two reasons: the landmarker is
 * comfortably accurate within ±15° of upright, so residual roll inside the band
 * costs nothing; and the landmarker carries ROI state between calls, so a rotation
 * that changed every frame would keep resetting the very state that makes it fast.
 */
const ROLL_REAIM = 0.26;

/**
 * Rotations tried, in turn, while no face can be found.
 *
 * A face rolled past what the landmarker tolerates does not merely track badly — it
 * is often not detected at all, so the eye-line estimate below never gets a chance
 * to aim the correction. Cycling candidate rotations turns acquisition into a
 * search: a wearer already lying down when the session starts is found within a few
 * attempts, and the estimator takes over from there. Upright first, then the two
 * lying-down poses, then the halves, then upside down.
 */
const ROLL_CANDIDATES = [0, Math.PI / 2, -Math.PI / 2, Math.PI / 4, -Math.PI / 4, Math.PI];

/**
 * Undoes the detect rotation on everything a result carries, in place.
 *
 * The landmarks are mapped through the exact inverse of the canvas transform the
 * frame was drawn with, and the pose is premultiplied by the equivalent camera-space
 * rotation about the optical axis (the image was rotated about its centre, which is
 * the principal point, so the correction is a pure roll — image rotation by θ in
 * y-down pixels is −θ in y-up camera space, hence the same θ fed to both).
 * Downstream of this function the pipeline never learns the rotation happened.
 */
function unrotateDetection(detection, rotation) {
  const w = state.detectCanvas.width;
  const h = state.detectCanvas.height;
  const c = Math.cos(-rotation);
  const s = Math.sin(-rotation);

  for (const p of detection.landmarks) {
    const x = p.x * w - w / 2;
    const y = p.y * h - h / 2;
    p.x = (c * x - s * y + w / 2) / w;
    p.y = (s * x + c * y + h / 2) / h;
  }

  // Premultiply the column-major pose by Rz(rotation) in camera space.
  const m = detection.matrix;
  const cz = Math.cos(rotation);
  const sz = Math.sin(rotation);
  for (let col = 0; col < 4; col++) {
    const x = m[col * 4];
    const y = m[col * 4 + 1];
    m[col * 4] = cz * x - sz * y;
    m[col * 4 + 1] = sz * x + cz * y;
  }
}

/** Re-aims the detect rotation from the corrected (true-image) eye line. */
function aimDetectRotation(landmarks) {
  const right = landmarks[LM.EYE_OUTER_R];
  const left = landmarks[LM.EYE_OUTER_L];
  if (!right || !left) return;
  const vx = (left.x - right.x) * state.detectCanvas.width;
  const vy = (left.y - right.y) * state.detectCanvas.height;
  // A collapsed eye line (profile, blink artefacts) aims nothing.
  if (vx * vx + vy * vy < 16) return;

  // The angle that stands this eye line level in the rotated image.
  const wanted = -Math.atan2(vy, vx);
  const diff = Math.atan2(
    Math.sin(wanted - state.detectRotation), Math.cos(wanted - state.detectRotation),
  );
  if (Math.abs(diff) > ROLL_REAIM) {
    state.detectRotation = wanted;
    console.info(`detect rotation → ${(wanted * 180 / Math.PI).toFixed(0)}°`);
  }
}

function onDetection({ detection, frame, inferMs, mode }) {
  // Solved against a source that has since been switched away from. The pose would
  // be planted on whatever replaced it.
  if (frame.epoch !== state.sourceEpoch || !state.source) return;

  // Back into the true image's coordinates before ANY consumer sees the result —
  // from here down, the rotation never happened.
  if (detection && frame.rotation) unrotateDetection(detection, frame.rotation);

  // Whether this face ends a faceless run, read before the counter is cleared
  // below — it decides the recovery dt at the bottom of this function.
  const endsFacelessRun = !!detection && state.facelessResults > 0;

  if (!detection) {
    // The run counter and the lost clock, advanced TOGETHER — the clock counts
    // from the first faceless result, held frames included; see
    // `noteFacelessResult` for why the hold must never stop it. Only the pose
    // filter is discarded when the clock runs out, and only once: a velocity
    // carried across half a second describes a movement that is over, and on
    // re-acquisition it throws the frame off the face until it decays. The *fit*
    // is not touched and cannot be — see `IDENTITY_TOLERANCE` in `frame.js`.
    const { hold, resetFilter } = noteFacelessResult(state, frame.captureDt);
    if (resetFilter) state.smoother.reset();
    // No face found where we were looking — including at the current rotation. Try
    // the next candidate angle: a rolled face is often undetectable rather than
    // merely mis-tracked, so acquisition has to search. See `ROLL_CANDIDATES`.
    if (state.facelessResults > HOLD_FACELESS_RESULTS && state.facelessResults % 4 === 0) {
      state.rollSearch = (state.rollSearch + 1) % ROLL_CANDIDATES.length;
      state.detectRotation = ROLL_CANDIDATES[state.rollSearch];
    }
    // Ride out a brief dropout by simply not advancing anything. See above.
    if (hold) return;
  } else {
    state.facelessResults = 0;
    aimDetectRotation(detection.landmarks);
  }

  // Show the frame the result belongs to — with or without a face in it. A camera
  // pointed at an empty chair must still be a mirror. The result's pixels are still
  // sitting in the capture canvas (nothing can have overwritten them: submissions
  // only happen while the tracker is idle), so this blit is what pairs them with
  // their own pose — see `captureCanvas`.
  if (state.captureCtx) state.frameCtx.drawImage(state.captureCanvas, 0, 0);
  if (state.background) state.background.needsUpdate = true;
  state.mirrorDelayMs = performance.now() - frame.capturedAtMs;
  if (frame.upstreamMs != null) {
    state.upstreamMs = state.upstreamMeasured
      ? state.upstreamMs + (frame.upstreamMs - state.upstreamMs) * 0.1
      : frame.upstreamMs;
    state.upstreamMeasured = true;
  }

  if (detection) {
    state.latencyMeasured = frame.measuredCapture;
    // Kept on the debug handle for the same reason the rest of `window.__ar` is: the
    // readouts summarise, and a question like "how well do MediaPipe's landmark depths
    // actually describe this head" can only be answered against the raw result.
    state.lastDetection = detection;

    // The filter's dt is the interval since the previous measurement it *consumed*
    // — and on the first face after a faceless run, that is not `captureDt`.
    // Submissions keep flowing through the run (the tracker is not busy; its
    // results just carry no face), so `captureDt` reports one camera frame while
    // the head has actually been moving unobserved since the last applied
    // detection — 2–4x longer after a ridden-out blur burst. Fed the short dt, the
    // filter reads the accumulated displacement as one enormous velocity, the
    // adaptive cutoff blows open, and the catch-up lands as a snap with a shimmer
    // burst trailing it while the rate estimate decays. So that one frame is given
    // the true gap instead, clamped to `LOST_SECONDS_BEFORE_RESET` — past that the
    // filter WAS reset (`noteFacelessResult` runs the lost clock from the first
    // faceless result, held frames included, so the premise holds by
    // construction) and a first sample is adopted
    // whole, so the clamp only bounds the pathological clock. The frames after the
    // recovery return to `captureDt` by construction, because
    // `lastAppliedDetectionMs` advances again with every applied detection.
    let dt = frame.captureDt;
    if (endsFacelessRun && state.lastAppliedDetectionMs) {
      const gap = (frame.capturedAtMs - state.lastAppliedDetectionMs) / 1000;
      if (gap > dt) {
        dt = Math.min(gap, LOST_SECONDS_BEFORE_RESET);
        state.recovery.events += 1;
        state.recovery.lastDtUsedMs = dt * 1000;
        state.recovery.lastGapMs = gap * 1000;
      }
    }
    updatePose(detection, dt);
    state.lastAppliedDetectionMs = frame.capturedAtMs;
    drawLandmarks(detection.landmarks);

    const nowMs = performance.now();
    if (state.lastResultMs) {
      const dt = (nowMs - state.lastResultMs) / 1000;
      state.trackFps += ((1 / Math.max(dt, 1e-4)) - state.trackFps) * 0.1;
    }
    state.lastResultMs = nowMs;
  } else {
    state.scene.head.visible = false;
    clearOverlay();
    // The bookkeeping already ran at the top of this function — the clock and the
    // filter reset live with the counter in `noteFacelessResult`, so a faceless
    // frame is accounted for exactly once whether the hold rode it out or not.
  }

  if (state.detected !== !!detection) {
    state.detected = !!detection;
    readout.set('face', detection ? 'tracking' : 'not found');
  }

  readout.set('tracker', `${mode} · ${inferMs.toFixed(0)} ms`);
  matchLighting(detection ? { landmarks: detection.landmarks } : null);
}

/** Draws, and writes the readouts that describe the drawing rather than the fit. */
function present() {
  state.scene.render();

  readout.set('fps', `${state.fps.toFixed(0)} / ${state.trackFps.toFixed(0)}`);
  readout.set('delay', state.detected && state.mirrorDelayMs
    ? `${state.mirrorDelayMs.toFixed(0)} ms${state.latencyMeasured ? '' : '+'}`
    : '—');
  readout.set('input', `${state.droppedFrames} / ${state.upstreamMeasured
    ? `${state.upstreamMs.toFixed(0)} ms` : '—'}`);
  readout.set('bounded', `${(state.smoother.bounded * 100).toFixed(0)}%`);
}

function updatePose(result, dt) {
  const measured = updateFrame({
    scene: state.scene,
    face: state.face,
    model: state.model,
    fit: state.fit,
    smoother: state.smoother,
    state,
    source: state.source,
    detection: result,
    dt,
    smoothing: controls.values.smoothing,
    adaptToFace: controls.values.adaptToFace,
    temples: state.temples,
    deformOccluder: controls.values.occluderFit,
    landmarkDepth: controls.values.landmarkDepth,
  });

  // How far away the wearer is, which is what turns the boundary's millimetres into
  // pixels. Without it the fade and the edge search have no idea whether they are
  // costing two pixels or seven.
  state.occlusionMask?.setDistance(measured.distanceCm);

  // The stillness meter (`__ar.stab`), fed per applied detection. With
  // smoothing toggled off the smoother's measured speeds are stale, so the
  // gate is fed Infinity — "never still" is the honest reading of a state
  // nothing is measuring.
  if (measured.placement) {
    const pt = screenPointOf(measured.placement.position, state.scene.head.matrixWorld,
      state.scene.camera, state.source.width, state.source.height);
    const live = controls.values.smoothing;
    state.stabMeter.update(pt.x, pt.y,
      live ? state.smoother.position.measuredSpeed : Infinity,
      live ? state.smoother.rotation.measuredRate : Infinity,
      performance.now() / 1000);
  }

  const delta = (measured.faceWidthRatio - 1) * 100;
  readout.set('width', `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}%`);
  // No "scanning 34%" any more, because there is nothing to wait for: the fit is a
  // fit from the first detection. What is shown instead is what the fit currently
  // says, which is true on frame one and stays true.
  readout.set('fit', measured.width
    ? `${measured.width.verdict} · ${(measured.pupilHeight * 100).toFixed(0)}%`
    : '—');
  // Pupil distance, the one number here that is in real millimetres rather than in
  // ratios against the average head — see `measureMetricScale`. A dash means no iris
  // has been resolved yet, which is honest and not a failure.
  readout.set('pd', measured.pdCm ? `${(measured.pdCm * 10).toFixed(0)} mm` : '—');
  readout.set('yaw', `${(measured.yaw * 100).toFixed(0)}%`);
  readout.set('depth', `${measured.distanceCm.toFixed(0)} cm`);
  readout.set('nose', noseReadout(measured.placement));
}

/** The seating solve, as millimetres of standoff and what it came to rest on. */
function noseReadout(placement) {
  const seat = placement?.noseSeat;
  if (!seat || !seat.touched) return 'off';
  // The eased value is what is actually applied to the frame this frame.
  return `${((seat.easedPush ?? seat.push) * 10).toFixed(1)} mm${seat.clamped ? ' (clamped)' : ''}`;
}

// ---------------------------------------------------------------- lighting

const lightProbe = createLightProbe();
let lightFrames = 0;
// Exponential average over samples, not frames — the probe runs every 15th frame,
// so a 0.2 blend settles in about a second and a half. Light changes (a lamp, a
// cloud) should ease in, not flicker the frame.
const lightState = { brightness: null, tint: [1, 1, 1] };

/**
 * Forgets the adapted lighting so the next sample is adopted outright.
 *
 * Called on source switch: easing is for a lamp turning on in the *same* room,
 * not for changing rooms — eased across a switch, the new scene wears the old
 * scene's light for several seconds.
 */
function resetLightAdaptation() {
  lightState.brightness = null;
  lightFrames = 0;
}

/**
 * Every 15th frame, reads the scene's brightness and colour cast off the source
 * and eases the key and ambient lights toward them. Sampling the face's bounding
 * box rather than the whole frame keeps a bright window behind the wearer from
 * reading as a bright face.
 */
function matchLighting(result) {
  if (++lightFrames < 15) return;
  lightFrames = 0;

  let region = null;
  if (result) {
    let minX = 1; let minY = 1; let maxX = 0; let maxY = 0;
    for (const p of result.landmarks) {
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    region = { minX, minY, maxX, maxY };
  }

  let sampled;
  try {
    sampled = lightProbe.sample(state.source.element, region);
  } catch {
    // A source that cannot be read back (zero-sized, tainted) just keeps the
    // current lighting; this is a refinement, never a reason to break the frame.
    return;
  }

  if (lightState.brightness === null) {
    lightState.brightness = sampled.brightness;
    lightState.tint = [...sampled.tint];
  } else {
    const blend = 0.2;
    lightState.brightness += (sampled.brightness - lightState.brightness) * blend;
    for (let i = 0; i < 3; i++) {
      lightState.tint[i] += (sampled.tint[i] - lightState.tint[i]) * blend;
    }
  }

  state.scene.setLighting({
    ...lightingFor(lightState.brightness),
    tint: softenTint(lightState.tint),
  });
}

// ---------------------------------------------------------------- layout

let lastWidth = 0;
let lastHeight = 0;
let unsizedFrames = 0;
let reportedUnsized = false;

/** Returns whether the stage has a real size — i.e. whether the frame can proceed. */
function syncSize() {
  const { width: sourceWidth, height: sourceHeight } = state.source;
  const aspect = sourceWidth / sourceHeight;

  // The stage is sized to the source's aspect ratio so the canvas and the tracker's
  // input frame describe the same rectangle. Anything else and the pose would be
  // projected onto a different image than the one on screen.
  const { width, height } = fitRect(
    dom.stageFit.clientWidth, dom.stageFit.clientHeight, aspect,
  );

  // No layout yet — a hidden tab, or the first frame before the flex box resolves.
  // Wait for a real box rather than committing a zero size.
  //
  // But do not wait silently forever. A stage that never gets a size renders a
  // black rectangle while every readout keeps updating, which reads as "the camera
  // is broken" when the truth is a CSS problem. Say so instead.
  if (width === 0 || height === 0) {
    if (++unsizedFrames === 120) {
      reportedUnsized = true;
      setStatus('The viewport has no size, so the video cannot be laid out — and until '
        + 'it can, nothing is being tracked. This is a layout problem, not a camera '
        + 'one.', 'error');
    }
    return false;
  }
  // Only clear the message this function put up, rather than whatever is on the bar.
  if (reportedUnsized) {
    reportedUnsized = false;
    setStatus('');
  }
  unsizedFrames = 0;

  if (width === lastWidth && height === lastHeight) return true;
  lastWidth = width;
  lastHeight = height;

  dom.stage.style.width = `${width}px`;
  dom.stage.style.height = `${height}px`;

  state.scene.resize(width, height, aspect);
  dom.overlay.width = width;
  dom.overlay.height = height;
  return true;
}

function applyMirror() {
  dom.stage.classList.toggle('is-mirrored', !!controls?.values.mirror);
}

// ---------------------------------------------------------------- overlay

function clearOverlay() {
  const ctx = dom.overlay.getContext('2d');
  ctx.clearRect(0, 0, dom.overlay.width, dom.overlay.height);
}

function drawLandmarks(landmarks) {
  const ctx = dom.overlay.getContext('2d');
  const { width, height } = dom.overlay;
  ctx.clearRect(0, 0, width, height);
  if (!controls.values.showLandmarks) return;

  ctx.fillStyle = 'rgba(56, 189, 248, 0.85)';
  for (const point of landmarks) {
    ctx.fillRect(point.x * width - 1, point.y * height - 1, 2, 2);
  }
}

// ---------------------------------------------------------------- controls

let controls;
let readout;

function buildUI() {
  readout = createReadout(dom.stats, {
    /** Renders per second, then detections per second: the display's clock, then the camera's. */
    fps: 'fps draw/track',
    /**
     * How far the mirror runs behind the room: capture to composite, measured. A
     * trailing `+` means the browser would not say when the frame was captured, so
     * the true figure is larger by however long the camera took. This is the price
     * of the frame lock, and ~50-100 ms of it is invisible in a mirror — what WOULD
     * be visible is glasses sliding on the face, which is what it buys.
     */
    delay: 'mirror delay',
    /** Where inference runs and what it costs — `worker · 21 ms` or `main · 21 ms`. */
    tracker: 'tracker',
    face: 'face',
    // Relative, not millimetres — a single camera cannot recover absolute head
    // size. See `measureFaceWidthRatio`.
    width: 'width vs avg',
    /** Frame-vs-face width, and where the pupils fall in the lens. */
    fit: 'fit',
    /** Pupil distance in millimetres, measured against the iris. Absolute, unlike the rest. */
    pd: 'pupils',
    /** How far the seating solve pushed the frame off the nose. See `nose.js`. */
    nose: 'nose seat',
    depth: 'distance',
    /**
     * The image-asymmetry yaw estimate, labelled as such since it was demoted to
     * a readout (nose-v2 stage 2): it conflates pillow roll with turn, so nothing
     * gates on it any more — the true-euler trust weights live at
     * `__ar.poseTrust`. It stays because it is still an honest description of
     * what the *image* shows.
     */
    yaw: 'turn (img)',
    /**
     * Two numbers about the input the app never used to be able to see: how many
     * camera frames the browser presented but we never received, and how much of the
     * mirror delay happened before any of our code ran. Both are the difference
     * between "our pipeline is slow" and "our pipeline is being starved", which no
     * amount of staring at an fps figure can tell apart.
     */
    input: 'dropped / upstream',
    /**
     * How hard the pose filter's lag ceiling is having to pull, 0-100%. Anything
     * persistently above a few percent means the smoothing is mistuned rather than
     * merely bounded — see `PoseSmoother.update`.
     */
    bounded: 'lag bound',
  });

  controls = createControls(dom.controls, [
    {
      label: 'Input',
      controls: [
        {
          key: 'source', type: 'select', label: 'Source', value: SOURCES[1].value,
          options: SOURCES,
        },
        {
          key: 'drift', type: 'toggle', label: 'Animate sample', value: false,
          hint: 'Pans and rocks the still image so the tracking is exercised over time.',
        },
        { key: 'mirror', type: 'toggle', label: 'Mirror', value: false },
      ],
    },
    {
      label: 'Frame',
      controls: [
        { key: 'model', type: 'select', label: 'Model', value: DEFAULT_MODEL, options: MODELS },
        {
          key: 'mode', type: 'select', label: 'Sizing', value: DEFAULT_FIT.mode,
          options: FIT_MODES.map((value) => ({
            value,
            label: value === 'physical' ? 'True size' : 'Fit to face',
          })),
          hint: 'True size keeps the frame at its manufactured width, so it can look '
              + 'too big or too small — which is the point. Fit to face always spans the face.',
        },
        {
          key: 'sizeMultiplier', type: 'range', label: 'Size', value: DEFAULT_FIT.sizeMultiplier,
          min: 0.6, max: 1.6, step: 0.01, unit: '×',
        },
        {
          key: 'widthRatio', type: 'range', label: 'Face span', value: DEFAULT_FIT.widthRatio,
          min: 0.7, max: 1.2, step: 0.01, unit: '×',
        },
      ],
    },
    {
      label: 'Placement',
      controls: [
        {
          key: 'offsetY', type: 'range', label: 'Up / down', value: DEFAULT_FIT.offsetY,
          min: -2, max: 2, step: 0.05, unit: ' cm', decimals: 2,
        },
        {
          key: 'offsetZ', type: 'range', label: 'Standoff', value: DEFAULT_FIT.offsetZ,
          min: -1, max: 2, step: 0.05, unit: ' cm', decimals: 2,
          hint: 'Vertex distance — how far the frame stands off the nose it is resting '
              + 'on. Zero is in contact, which is where the seating solve puts it.',
        },
        {
          key: 'offsetX', type: 'range', label: 'Left / right', value: DEFAULT_FIT.offsetX,
          min: -1.5, max: 1.5, step: 0.05, unit: ' cm', decimals: 2,
        },
        {
          key: 'pantoscopicTilt', type: 'range', label: 'Tilt',
          value: DEFAULT_FIT.pantoscopicTilt, min: -10, max: 25, step: 0.5, unit: '°', decimals: 1,
        },
        {
          key: 'pupilTarget', type: 'range', label: 'Pupil height',
          value: DEFAULT_FIT.pupilTarget, min: 0.3, max: 0.62, step: 0.01, decimals: 2,
          hint: 'Where the pupils sit in the lens, 0 at the top edge. This is what '
              + 'sets the frame\'s height — the up/down offset only nudges it.',
        },
        { type: 'button', label: 'Reset placement', onClick: resetFit },
        {
          type: 'button',
          label: 'Re-measure face',
          onClick: () => remeasure(state),
        },
      ],
    },
    {
      label: 'Pipeline',
      controls: [
        {
          key: 'fitTemples', type: 'toggle', label: 'Arms to ears', value: true,
          hint: 'Swings each temple arm about its hinge so it runs to the ear, the '
              + 'way an optician adjusts them. Off leaves them where the modeller '
              + 'put them — which on most assets is inside the head.',
        },
        {
          key: 'adaptToFace', type: 'toggle', label: 'Adapt to face', value: true,
          hint: 'Places the frame on this face\'s measured nose bridge, eye line and '
              + 'width. Turn off to fall back to the average head — the difference is '
              + 'the whole point.',
        },
        {
          key: 'seatOnNose', type: 'toggle', label: 'Rest on the nose', value: true,
          hint: 'Solves how far the frame stands off the face by putting the back of '
              + 'its bridge on the nose\'s actual surface. Off hangs it from the '
              + 'bridge landmark instead, which buries the pads about 5 mm in.',
        },
        {
          key: 'smoothing', type: 'toggle', label: 'Smoothing', value: true,
          hint: 'Velocity-carrying filter on the head pose. Turn off to see the raw jitter.',
        },
        {
          key: 'occlusion', type: 'toggle', label: 'Occlusion', value: true,
          hint: 'Hides the frame where the head covers it — the temple arms behind '
              + 'the face, and the bridge and inner rims behind the nose.',
        },
        {
          key: 'occluderFit', type: 'toggle', label: 'Fit head to face', value: true,
          hint: 'Deforms the occluding head onto this face\'s own landmarks, and seats '
              + 'the frame against that same surface. Off draws the average head — '
              + 'which is what the nose used to be occluded by on everybody.',
        },
        {
          key: 'landmarkDepth', type: 'toggle', label: 'Nose depth from landmarks', value: true,
          hint: 'Recovers how far this nose actually projects, by fitting MediaPipe\'s '
              + 'relative landmark depths onto the average head. Off leaves the '
              + 'occluder with the average nose\'s protrusion at this face\'s width.',
        },
        {
          key: 'occlusionFeather', type: 'range', label: 'Edge softness',
          value: OCCLUDER_FEATHER, min: 0, max: 0.4, step: 0.01, unit: ' cm', decimals: 2,
          hint: 'How wide the fade at the occlusion boundary is. Zero is a hard depth '
              + 'test, which draws every millimetre of tracking error as an edge.',
        },
        {
          key: 'occlusionSnap', type: 'range', label: 'Snap to video edge',
          value: 0.7, min: 0, max: 1, step: 0.05, decimals: 2,
          hint: 'Moves the occlusion boundary onto the edge the camera can actually see, '
              + 'up to a few pixels. The depth test decides which side of the nose a '
              + 'pixel is on; this decides where the nose ends. Zero is depth only.',
        },
        {
          key: 'showOcclusionMask', type: 'toggle', label: 'Show occlusion mask', value: false,
          hint: 'Paints the boundary onto the frame: green is drawn, red is hidden by the '
              + 'head, and the gradient between them is the fade. Put your nose in that '
              + 'gradient to see whether the edge is where it belongs.',
        },
        {
          key: 'showFaceMesh', type: 'toggle', label: 'Show face mesh', value: false,
          hint: 'The head the occluder is actually using, deformed onto your face.',
        },
        { key: 'showAnchors', type: 'toggle', label: 'Show fit anchors', value: false },
        { key: 'showLandmarks', type: 'toggle', label: 'Show landmarks', value: false },
      ],
    },
  ], onControlChange);

  syncModeVisibility();
  applyToggles();
}

function onControlChange(key, value) {
  switch (key) {
    case 'source':
      useSource(value);
      break;
    case 'model':
      loadModel(value).catch((error) => {
        setStatus(`Could not load model: ${error.message}`, 'error');
        // A failed load leaves the previous frame rendering, so the select has to go
        // back to naming it rather than the entry that did not arrive.
        if (state.modelValue) controls.set('model', state.modelValue);
      });
      break;
    case 'drift':
      if (state.source?.kind === 'sample') state.source.drift = value;
      break;
    case 'mirror':
      applyMirror();
      break;
    case 'mode':
      state.fit.mode = value;
      syncModeVisibility();
      break;
    case 'smoothing':
      if (!value) state.smoother.reset();
      break;
    case 'fitTemples':
      // Only the fit flag changes. Clearing `templesAimed` here too looks harmless
      // but makes switching *off* impossible: the reset branch in `updateFrame` only
      // runs while `templesAimed` is still true, so clearing it first leaves the
      // arms aimed forever and `resetTemples` unreachable.
      state.fit.fitTemples = value;
      break;
    case 'adaptToFace':
      // Start the measurement cleanly rather than continuing a window collected
      // under the other setting.
      remeasure(state);
      break;
    case 'occlusion':
    case 'occlusionFeather':
    case 'occlusionSnap':
    case 'showOcclusionMask':
    case 'showFaceMesh':
    case 'showAnchors':
      applyToggles();
      break;
    case 'occluderFit':
      // The occluder rebuilds itself from the next detection either way; nothing to
      // reset here, and `updateOccluder` clears the carried shape when it is switched
      // off so the toggle is a real A/B rather than a frozen one.
      break;
    default:
      if (key in state.fit) state.fit[key] = value;
  }
}

function syncModeVisibility() {
  const physical = state.fit.mode === 'physical';
  controls.setVisible('widthRatio', !physical);
}

function applyToggles() {
  // Both halves, or the toggle only half works: the mesh stops writing depth while the
  // mask carries on discarding fragments against the last depth texture it was handed.
  state.occluder.visible = controls.values.occlusion;
  if (state.occlusionMask) {
    state.occlusionMask.enabled = controls.values.occlusion;
    state.occlusionMask.feather = controls.values.occlusionFeather;
    state.occlusionMask.snap = controls.values.occlusionSnap;
    state.occlusionMask.show = controls.values.showOcclusionMask;
  }
  // The relief is derived from the feather (`relief >= PAD_SINK + feather` is what
  // keeps a seated pad in front of the fade), so the Edge-softness slider has to
  // move both. Left apart, a widened fade starts in front of the relieved surface
  // and dithers away the very bridge contact the relief exists to protect.
  {
    const data = state.occluder.userData;
    const base = PAD_SINK + Math.max(controls.values.occlusionFeather, 0) + 0.01;
    if (Math.abs((data.reliefBase ?? 0) - base) > 1e-6) {
      data.reliefBase = base;
      data.relief = base;
      data.driftSurface = Infinity;
    }
    // The mask sizes its edge-search radius from the relief, because the relief is
    // how far the boundary was deliberately displaced. Left on the compile-time
    // value, widening Edge softness grows the displacement while the search stays
    // put — and the snap dies exactly when the slider is used.
    if (state.occlusionMask) state.occlusionMask.relief = base;
  }
  // Showing the mask means the hidden fragments have to survive to be tinted, so the
  // head stops culling them in the main pass. Its own depth pre-pass is unaffected —
  // `renderDepth` forces the write back on for the one render that needs it.
  const head = state.occluder.userData.head;
  head.material.depthWrite = !controls.values.showOcclusionMask;
  state.faceDebug.visible = controls.values.showFaceMesh;
  state.anchorDebug.visible = controls.values.showAnchors;
}

function resetFit() {
  for (const key of ['sizeMultiplier', 'widthRatio', 'offsetX', 'offsetY', 'offsetZ', 'pantoscopicTilt']) {
    state.fit[key] = DEFAULT_FIT[key];
    controls.set(key, DEFAULT_FIT[key]);
  }
}

// ----------------------------------------------------------------

window.addEventListener('resize', () => { lastWidth = 0; });

// The deliberate debug handle. Everything else in this module is closed over, which
// is right for the app and wrong for diagnosing it: when a session on some other
// machine behaves badly, the readouts summarise but cannot be interrogated. This is
// how a console (or a colleague's tooling) asks the running app what it actually
// measured — tracker mode, camera interval, latency figures, the locked anchors.
window.__ar = state;

boot().catch((error) => {
  console.error(error);
  setStatus(`${error.message}`, 'error');
});
