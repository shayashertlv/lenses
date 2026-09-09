import "../../src/style.css";
import { openCamera } from "../../src/runtime/camera.ts";
import type { CameraSession } from "../../src/runtime/camera.ts";
import { DetectorClient } from "../../src/runtime/detector.ts";
import type { ComparisonVariant, TryOnRenderer } from "./renderer.ts";
import { CaptureController } from "./capture-controller.ts";
import { DEFAULT_EYEWEAR_ID, EYEWEAR, eyewearById } from "../../src/render/eyewear.ts";
import type { EyewearId } from "../../src/render/eyewear.ts";

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing interface element: ${id}`);
  return found as T;
}
const start = element<HTMLButtonElement>("start");
const stop = element<HTMLButtonElement>("stop");
const welcome = element("welcome");
const guidance = element("guidance");
const stageStatus = element("stage-status");
const stage = document.querySelector<HTMLElement>(".stage")!;
const tracking = element("tracking");
const fps = element("fps");
const latency = element("latency");
const resolution = element("resolution");
const eyewearSelect = element<HTMLSelectElement>("eyewear-select");
const eyewearHint = element("eyewear-hint");
let selectedEyewear: EyewearId = DEFAULT_EYEWEAR_ID;
const variantSelect = element<HTMLSelectElement>('variant-select');
const variantHint = element('variant-hint');
const holdFrame = element<HTMLButtonElement>('hold-frame');
const downloadDiagnostic = element<HTMLButtonElement>('download-diagnostic');
const diagnosticStatus = element('diagnostic-status');
let selectedVariant: ComparisonVariant = new URLSearchParams(location.search).get('variant') === 'perfecto'
  ? 'perfecto' : 'candidate';
variantSelect.value = selectedVariant;

function updateComparisonControl(): void {
  const replaying = current?.phase === 'replay';
  const held = current?.phase === 'held';
  variantSelect.disabled = replaying;
  variantHint.textContent = replaying
    ? 'Replay shows the version stored with each captured frame. Discard the capture to compare live again.'
    : held
      ? 'Camera and tracker stopped. Switching versions redraws this same held image and detection. Close the held frame to start a new camera session.'
    : current
      ? 'Switch versions while live, or hold a problem frame to compare one exact image and detection.'
      : 'Perfect temples is the accepted version. Choose a frame, then open the camera.';
  if (!replaying && selectedVariant === 'candidate' && (held || stage.dataset.state === 'tracking')
    && current?.renderer?.diagnostics?.fallback) {
    variantHint.textContent = `Perfect temples unavailable for this frame; showing perfecto. ${variantHint.textContent}`;
  }
  const captureBusy = !['idle', 'ready'].includes(element('capture').dataset.state ?? 'idle');
  holdFrame.disabled = current?.phase !== 'live' || stage.dataset.state !== 'tracking' || captureBusy;
  holdFrame.hidden = held || replaying;
  downloadDiagnostic.hidden = !held;
  if (!held) downloadDiagnostic.disabled = true;
  stage.dataset.comparisonVariant = selectedVariant;
}
for (const model of Object.values(EYEWEAR)) {
  eyewearSelect.add(new Option(model.optionLabel, model.id));
}
eyewearSelect.value = selectedEyewear;

function showSelectedEyewear(): void {
  const model = eyewearById(selectedEyewear);
  element("mirror-title").textContent = `See yourself in ${model.name}.`;
  element("frame-name").textContent = model.name;
  element("frame-description").textContent = model.description;
  element("frame-finish").textContent = model.finish;
}

function updateEyewearControl(): void {
  updateComparisonControl();
  eyewearSelect.disabled = current !== null;
  eyewearHint.textContent = current?.phase === 'replay'
    ? 'Discard the capture to change frames.'
    : current ? 'Close the camera to change frames.' : 'Choose a frame before opening the camera.';
}

interface Session {
  eyewearId: EyewearId;
  phase: 'live' | 'replay' | 'held';
  liveGeneration: number;
  abort: AbortController;
  camera?: CameraSession;
  detector: DetectorClient;
  renderer?: TryOnRenderer;
  canvas: HTMLCanvasElement;
  cancelFrame?: () => void;
  liveCleanups: (() => void)[];
  cleanups: (() => void)[];
  heldFrame?: { capturedAtMs: number | null; heldAt: string; performanceTimeOriginMs: number };
}
let current: Session | null = null;

function setState(state: string, label: string, message: string): void {
  stage.dataset.state = state;
  stageStatus.textContent = label;
  // Avoid repeating live-region announcements on every frame.
  if (guidance.textContent !== message) guidance.textContent = message;
}

function closeSession(
  message = "Camera closed. Ready whenever you are.",
  failed = false,
): void {
  const session = current;
  current = null; // Invalidate first: none of the awaited work may publish after this point.
  updateEyewearControl();
  captureController.reset();
  diagnosticStatus.textContent = 'Hold a visible problem frame to save its exact source and diagnostic views.';
  downloadDiagnostic.textContent = 'Download diagnostic';
  if (session) {
    session.liveGeneration++;
    session.cancelFrame?.();
    for (const cleanup of session.liveCleanups) cleanup();
    for (const cleanup of session.cleanups) cleanup();
    session.abort.abort();
    session.camera?.stop();
    session.detector.close();
    session.renderer?.dispose();
    session.canvas.hidden = true;
  }
  start.disabled = false;
  start.innerHTML = `${failed ? "Try again" : "Open camera"} <span aria-hidden="true">↗</span>`;
  stop.hidden = true;
  stop.textContent = 'Close camera';
  welcome.hidden = false;
  tracking.textContent = "Off";
  fps.textContent = latency.textContent = resolution.textContent = "—";
  setState(
    failed ? "error" : "idle",
    failed ? "CAMERA UNAVAILABLE" : "CAMERA OFF",
    message,
  );
}

function friendlyError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError")
      return "Camera access was denied. Allow it in your browser, then try again.";
    if (error.name === "NotFoundError")
      return "No camera was found. Connect a camera and try again.";
    if (error.name === "NotReadableError")
      return "The camera could not be opened. Close other apps using it, then try again.";
  }
  return error instanceof Error
    ? error.message
    : "The mirror could not start. Please try again.";
}

function context2D(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const context = canvas.getContext("2d", { alpha: false });
  if (!context)
    throw new Error("Your browser could not create the camera image.");
  return context;
}

function runFrames(session: Session): void {
  const video = session.camera!.video;
  const detector = session.detector;
  const liveGeneration = session.liveGeneration;
  const ownsLiveRun = () => current === session && session.phase === 'live' && session.liveGeneration === liveGeneration;
  const capture = document.createElement("canvas");
  const input = document.createElement("canvas");
  const captureContext = context2D(capture);
  const inputContext = context2D(input);
  let lastFrameIdentity = -1;
  let firstPresentedAt = 0;
  let framesPresented = 0;
  let lastStatsAt = 0;
  let lastFrameAt = performance.now();
  let processing = false;

  const watchdog = window.setInterval(() => {
    if (
      ownsLiveRun() &&
      !processing &&
      performance.now() - lastFrameAt > 6000
    ) {
      closeSession(
        "The camera stopped sending frames. Please open it again.",
        true,
      );
    }
  }, 1000);
  session.liveCleanups.push(() => clearInterval(watchdog));

  function schedule(): void {
    if (!ownsLiveRun()) return;
    if (typeof video.requestVideoFrameCallback === "function") {
      const id = video.requestVideoFrameCallback((_now, metadata) => {
        void process(metadata.presentedFrames);
      });
      session.cancelFrame = () => video.cancelVideoFrameCallback(id);
    } else {
      const id = requestAnimationFrame(() => {
        void process(video.currentTime);
      });
      session.cancelFrame = () => cancelAnimationFrame(id);
    }
  }

  async function process(frameIdentity: number): Promise<void> {
    if (!ownsLiveRun()) return;
    if (video.readyState < 2 || frameIdentity === lastFrameIdentity) {
      schedule();
      return;
    }
    lastFrameIdentity = frameIdentity;
    processing = true;
    const capturedAt = performance.now();
    lastFrameAt = capturedAt;
    try {
      // Both canvases are immutable until this inference is completed. No queue.
      const scale = Math.min(
        1,
        1280 / Math.max(video.videoWidth, video.videoHeight),
      );
      const width = Math.max(1, Math.round(video.videoWidth * scale));
      const height = Math.max(1, Math.round(video.videoHeight * scale));
      if (capture.width !== width || capture.height !== height) {
        capture.width = width;
        capture.height = height;
        const detectorScale = Math.min(1, 640 / Math.max(width, height));
        input.width = Math.round(width * detectorScale);
        input.height = Math.round(height * detectorScale);
      }
      captureContext.drawImage(video, 0, 0, width, height);
      inputContext.drawImage(capture, 0, 0, input.width, input.height);
      const bitmap = await createImageBitmap(input);
      if (!ownsLiveRun()) {
        bitmap.close();
        return;
      }
      const result = await detector.detect(bitmap, capturedAt);
      if (!ownsLiveRun()) return;
      const hasFace = session.renderer!.present(capture, result);
      stage.dataset.presentedAt = String(capturedAt);
      captureController.setLiveFace(hasFace);
      session.canvas.hidden = false;
      welcome.hidden = true;
      tracking.textContent = hasFace ? "Face visible" : "Looking for face";
      setState(
        hasFace ? "tracking" : "searching",
        hasFace ? "MIRROR LIVE" : "LOOKING FOR YOU",
        captureController.guidance ?? (hasFace
          ? "Turn slowly to check the bridge and sides."
          : "Bring your face into view and look towards the camera."),
      );
      updateComparisonControl();
      await captureController.observe(capture, result, capturedAt);
      if (!ownsLiveRun()) return;
      const now = performance.now();
      if (!firstPresentedAt) firstPresentedAt = now;
      framesPresented++;
      if (now - lastStatsAt > 500) {
        const elapsed = (now - firstPresentedAt) / 1000;
        fps.textContent =
          elapsed > 0
            ? `${Math.round((framesPresented - 1) / elapsed)} fps`
            : "Starting";
        latency.textContent = `${Math.round(now - capturedAt)} ms`;
        resolution.textContent = `${video.videoWidth} × ${video.videoHeight}`;
        stage.dataset.frames = String(framesPresented);
        stage.dataset.inferenceMs = String(Math.round(result.inferenceMs));
        stage.dataset.projectionResidualPx = String(
          session.renderer!.projectionResidualPx ?? "",
        );
        stage.dataset.bridgeCorrectionPx = String(session.renderer!.bridgeCorrectionPx ?? "");
        stage.dataset.yawDegrees = String(session.renderer!.yawDegrees ?? "");
        stage.dataset.nativeSamples = String(session.renderer!.nativeSamples);
        lastStatsAt = now;
      }
      processing = false;
      lastFrameAt = performance.now();
      schedule();
    } catch (error) {
      if (ownsLiveRun()) closeSession(friendlyError(error), true);
    }
  }
  schedule();
}

async function openSession(): Promise<void> {
  if (current) return;
  const oldCanvas = element<HTMLCanvasElement>("mirror");
  const canvas = oldCanvas.cloneNode(false) as HTMLCanvasElement;
  oldCanvas.replaceWith(canvas);
  const session: Session = {
    eyewearId: selectedEyewear,
    phase: 'live',
    liveGeneration: 1,
    abort: new AbortController(),
    detector: new DetectorClient(),
    canvas,
    liveCleanups: [],
    cleanups: [],
  };
  current = session;
  updateEyewearControl();
  delete stage.dataset.frames;
  delete stage.dataset.inferenceMs;
  delete stage.dataset.presentedAt;
  start.disabled = true;
  start.textContent = "Opening…";
  stop.hidden = false;
  setState(
    "starting",
    "STARTING CAMERA",
    "Allow camera access when your browser asks.",
  );
  try {
    session.camera = await openCamera(session.abort.signal);
    if (current !== session) {
      session.camera.stop();
      return;
    }
    const stream = session.camera.video.srcObject as MediaStream;
    const liveGeneration = session.liveGeneration;
    const ended = () => {
      if (current === session && session.liveGeneration === liveGeneration)
        closeSession(
          "Your camera was disconnected. Reconnect it and try again.",
          true,
        );
    };
    for (const track of stream.getTracks()) {
      track.addEventListener("ended", ended);
      session.liveCleanups.push(() => track.removeEventListener("ended", ended));
    }
    const contextLost = (event: Event) => {
      event.preventDefault();
      if (current === session)
        closeSession(
          "The graphics connection was interrupted. Please open the camera again.",
          true,
        );
    };
    canvas.addEventListener("webglcontextlost", contextLost);
    session.cleanups.push(() =>
      canvas.removeEventListener("webglcontextlost", contextLost),
    );
    setState(
      "starting",
      "PREPARING MIRROR",
      "Loading the frame and local face tracker…",
    );
    const { TryOnRenderer } = await import("./renderer.ts");
    if (current !== session) return;
    session.renderer = await TryOnRenderer.create(canvas, session.abort.signal, session.eyewearId);
    if (current !== session) {
      session.renderer.dispose();
      return;
    }
    session.renderer.selectVariant(selectedVariant);
    stage.dataset.nativeSamples = String(session.renderer.nativeSamples);
    await session.detector.initialize(session.abort.signal);
    if (current !== session) return;
    runFrames(session);
  } catch (error) {
    if (current === session) closeSession(friendlyError(error), true);
  }
}

const captureController = new CaptureController({
  renderer: () => current?.renderer ?? null,
  captureInfo: () => {
    const video = current?.camera?.video;
    const stream = video?.srcObject;
    const settings = stream instanceof MediaStream ? stream.getVideoTracks()[0]?.getSettings() : undefined;
    return {
      sourceWidth: video?.videoWidth ?? null,
      sourceHeight: video?.videoHeight ?? null,
      frameRate: settings?.frameRate ?? null,
      facingMode: settings?.facingMode ?? null,
      aspectRatio: settings?.aspectRatio ?? null,
    };
  },
  pauseForReplay: () => {
    const session = current;
    if (!session || session.phase !== 'live' || !session.renderer) return false;
    // Invalidate live callbacks before releasing the stream and in-flight worker.
    // Keep this session's renderer and abort owner for memory-only replay.
    session.phase = 'replay';
    updateEyewearControl();
    session.liveGeneration++;
    session.cancelFrame?.();
    session.cancelFrame = undefined;
    for (const cleanup of session.liveCleanups.splice(0)) cleanup();
    session.camera?.stop();
    session.detector.close();
    stop.hidden = true;
    tracking.textContent = 'Recorded frame';
    fps.textContent = latency.textContent = '—';
    setState('replay', 'RECORDED TURN', 'Camera closed. Move through the recorded turn.');
    return true;
  },
  replayPresented: (index, count) => {
    if (current?.phase !== 'replay') return;
    stage.dataset.replayFrame = String(index);
    setState('replay', 'RECORDED TURN', `Recorded frame ${index + 1} of ${count}. Camera closed.`);
  },
  close: () => closeSession('Capture discarded. Open the camera to record another turn.'),
});

holdFrame.addEventListener('click', () => {
  const session = current;
  if (!session || session.phase !== 'live' || !session.renderer || holdFrame.disabled
      || !session.renderer.captureSnapshot) return;
  // Freeze the already-presented pair. Invalidate in-flight inference before
  // releasing the worker/camera; its later completion must never replace it.
  session.phase = 'held';
  const capturedAtMs = Number(stage.dataset.presentedAt);
  session.heldFrame = { capturedAtMs: Number.isFinite(capturedAtMs) ? capturedAtMs : null,
    heldAt: new Date().toISOString(), performanceTimeOriginMs: performance.timeOrigin };
  session.liveGeneration++;
  session.cancelFrame?.();
  session.cancelFrame = undefined;
  for (const cleanup of session.liveCleanups.splice(0)) cleanup();
  session.camera?.stop();
  session.detector.close();
  // Holding is unavailable during a recording. No recorded frames are dropped.
  captureController.setLiveFace(false);
  element('capture').dataset.state = 'held';
  element('capture-status').textContent = 'Problem frame held. Close it before recording another turn.';
  element<HTMLButtonElement>('record-turn').disabled = true;
  stop.hidden = false;
  stop.textContent = 'Close held frame';
  tracking.textContent = 'Exact pair held';
  fps.textContent = latency.textContent = '—';
  setState('held', 'PROBLEM FRAME HELD', 'Camera stopped. Compare the same image, then download its diagnostics if useful.');
  updateEyewearControl();
  downloadDiagnostic.disabled = false;
  diagnosticStatus.textContent = 'This held source, detection and pose stay together. Download includes lossless source and matched diagnostic views.';
});

downloadDiagnostic.addEventListener('click', () => {
  void saveDiagnostic();
});

async function saveDiagnostic(): Promise<void> {
  const session = current;
  if (!session || session.phase !== 'held' || !session.renderer || downloadDiagnostic.disabled) return;
  const generation = session.liveGeneration;
  const ownsHeldFrame = () => current === session && session.phase === 'held'
    && session.liveGeneration === generation && !session.abort.signal.aborted;
  downloadDiagnostic.disabled = true;
  downloadDiagnostic.textContent = 'Preparing diagnostic…';
  try {
    const diagnostic = await session.renderer.exportDiagnostic();
    if (!ownsHeldFrame()) return;
    if (!diagnostic) throw new Error('The held frame no longer has valid diagnostic data.');
    const file = new Blob([JSON.stringify({ ...diagnostic,
      heldFrame: { ...session.heldFrame, selectedVariantAtDownload: session.renderer.variant } })],
    { type: 'application/json' });
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = `ar-v4-held-diagnostic-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    const timer = window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    session.cleanups.push(() => { clearTimeout(timer); URL.revokeObjectURL(url); });
    diagnosticStatus.textContent = 'Diagnostic download requested. It contains the exact held source, tracking data and matched before/after views.';
  } catch (error) {
    if (ownsHeldFrame()) diagnosticStatus.textContent = `Diagnostic download failed: ${friendlyError(error)}`;
  } finally {
    if (ownsHeldFrame()) {
      downloadDiagnostic.disabled = false;
      downloadDiagnostic.textContent = 'Download diagnostic';
    }
  }
}

// CaptureController changes its state synchronously when recording is started.
// Keep holding unavailable until the existing recording/replay is discarded.
element('record-turn').addEventListener('click', updateComparisonControl);
element('finish-recording').addEventListener('click', updateComparisonControl);

eyewearSelect.addEventListener("change", () => {
  // The session owns its chosen model, including asynchronous startup and replay.
  if (current) { eyewearSelect.value = current.eyewearId; return; }
  if (!Object.hasOwn(EYEWEAR, eyewearSelect.value)) {
    eyewearSelect.value = selectedEyewear;
    return;
  }
  selectedEyewear = eyewearSelect.value as EyewearId;
  showSelectedEyewear();
});
variantSelect.addEventListener('change', () => {
  if (current?.phase === 'replay') { variantSelect.value = selectedVariant; return; }
  if (variantSelect.value !== 'perfecto' && variantSelect.value !== 'candidate') {
    variantSelect.value = selectedVariant;
    return;
  }
  selectedVariant = variantSelect.value;
  try {
    current?.renderer?.selectVariant(selectedVariant);
    updateComparisonControl();
  } catch (error) {
    closeSession(friendlyError(error), true);
  }
});
showSelectedEyewear();
updateEyewearControl();

start.addEventListener("click", () => {
  void openSession();
});
stop.addEventListener("click", () => closeSession());
document.addEventListener("visibilitychange", () => {
  if (document.hidden && current?.phase === 'live')
    closeSession("Camera closed while this tab was away. Open it to continue.");
});
window.addEventListener("pagehide", () => closeSession());
