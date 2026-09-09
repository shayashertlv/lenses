import '../../src/style.css';
import './live.css';
import {openCamera} from '../../src/runtime/camera.ts';
import type {CameraSession} from '../../src/runtime/camera.ts';
import {DetectorClient} from '../../src/runtime/detector.ts';
import {DEFAULT_EYEWEAR_ID, EYEWEAR, eyewearById} from '../../src/render/eyewear.ts';
import type {EyewearId} from '../../src/render/eyewear.ts';
import {HairClient} from './hair-client.ts';
import type {HairSegmentationResult} from './hair-client.ts';
import {FrameBudget} from './frame-budget.ts';
import {detectHairBackend} from './hair-backend.ts';
import type {HairBackend} from './hair-backend.ts';
import {hairModelById} from './models.ts';
import type {HairModelId} from './models.ts';
import type {HairMask, LiveHairRenderer} from './renderer.ts';

function element<T extends HTMLElement>(id: string): T {
  const value = document.getElementById(id);
  if (!value) throw new Error(`Missing preview control: ${id}`);
  return value as T;
}
const start = element<HTMLButtonElement>('start'), stop = element<HTMLButtonElement>('stop');
const welcome = element('welcome'), guidance = element('guidance'), stageStatus = element('stage-status');
const stage = document.querySelector<HTMLElement>('.stage')!;
const eyewearSelect = element<HTMLSelectElement>('eyewear-select');
const hairSelect = element<HTMLSelectElement>('hair-model-select');
const variantSelect = element<HTMLSelectElement>('variant-select');
const hold = element<HTMLButtonElement>('hold-frame'), resume = element<HTMLButtonElement>('resume-live');
const download = element<HTMLButtonElement>('download-diagnostic');
let selectedEyewear: EyewearId = DEFAULT_EYEWEAR_ID;
let selectedHair: HairModelId = 'hair-only';
let selectedVariant: 'accepted' | 'hair' = 'hair';

interface Session {
  id: string;
  phase: 'live' | 'held';
  generation: number;
  abort: AbortController;
  eyewearId: EyewearId;
  hairId: HairModelId;
  camera?: CameraSession;
  detector: DetectorClient;
  hair: HairClient;
  backend: HairBackend;
  hairReady: boolean;
  hairError: string | null;
  renderer?: LiveHairRenderer;
  canvas: HTMLCanvasElement;
  cancelFrame?: () => void;
  liveCleanups: (() => void)[];
  cleanups: (() => void)[];
  nextSequence: number;
  presented: {sequence: number; sourceSHA256: string; detectionSHA256: string; capturedAtMs: number} | null;
  heldAt: string | null;
  heldBusy: boolean;
  processing: boolean;
  holdRequested: boolean;
  budgetMisses: number;
  timing: {faceMs: number; hairWaitMs: number; renderMs: number; totalMs: number} | null;
}
let current: Session | null = null;

const messageFor = (error: unknown): string => error instanceof Error ? error.message : String(error);
async function sha256(bytes: Uint8Array | Uint8ClampedArray): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new Uint8Array(bytes).buffer);
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
}
function context2D(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const context = canvas.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'});
  if (!context) throw new Error('The preview could not create the camera image.');
  return context;
}
function setState(state: string, label: string, message: string): void {
  stage.dataset.state = state;
  stageStatus.textContent = label;
  if (guidance.textContent !== message) guidance.textContent = message;
}
function updateControls(): void {
  const held = current?.phase === 'held';
  eyewearSelect.disabled = hairSelect.disabled = current !== null;
  element('selection-hint').textContent = current
    ? 'Close this session to change glasses or hair model.'
    : 'Choose glasses and a hair model before opening the camera.';
  hold.hidden = held;
  hold.disabled = current?.phase !== 'live' || !current.presented || stage.dataset.state !== 'tracking' || current.holdRequested;
  resume.hidden = !held;
  download.hidden = !held;
  if (!held) download.disabled = true;
  stop.textContent = held ? 'Close held frame' : 'Close camera';
  element('hold-hint').textContent = held
    ? 'Camera stopped. Both versions show this exact held image. Resume starts a fresh live session.'
    : 'Hold a frame to compare both versions on exactly the same image.';
  stage.dataset.variant = selectedVariant;
}
function showHairStatus(): void {
  const session = current;
  element('hair-engine').textContent = session?.backend.active ?? (session ? 'Preparing' : '—');
  let text = 'Hair can cover eligible parts of the side arms. Nose and front protection stays fixed.';
  if (session) {
    const fallback = session.renderer?.stats?.fallbackReason;
    if (selectedVariant === 'accepted') text = 'Showing the accepted rendering. Toggle to compare the hair preview.';
    else if (session.hairError) text = session.phase === 'held' && session.renderer?.stats?.hasMask
      ? 'Showing the held hair comparison. Extra diagnostic data is unavailable.'
      : 'Hair processing is unavailable; showing the accepted rendering. Close and reopen to retry.';
    else if (!session.hairReady && session.phase === 'live') text = 'Preparing hair detection. The accepted mirror can continue while it loads.';
    else if (session.heldBusy) text = 'Camera stopped. Preparing hair on this exact held image…';
    else if (fallback && stage.dataset.state !== 'searching') text = session.phase === 'live'
      ? 'Hair was skipped on this frame to keep the mirror moving. Hold a frame for a complete hair comparison.'
      : 'Showing the accepted rendering for this frame because the hair preview could not be applied safely.';
    else text = 'Hair preview on. Nose and front protection stays fixed; compare the side arms as you turn.';
    element('hair-detail').textContent = session.hairError ?? fallback ?? '';
  } else element('hair-detail').textContent = '';
  if (element('hair-status').textContent !== text) element('hair-status').textContent = text;
}

async function initializeHair(session: Session, owns: () => boolean): Promise<HairClient> {
  let client = session.hair;
  try { await client.initialize(session.abort.signal); }
  catch (error) {
    if (!owns() || session.abort.signal.aborted || client.delegate !== 'GPU') throw error;
    client.close();
    session.backend.fallbackReason = messageFor(error);
    client = new HairClient(session.hairId, {delegate: 'CPU', outputMode: client.outputMode}); session.hair = client;
    await client.initialize(session.abort.signal);
  }
  if (!owns() || session.abort.signal.aborted) {
    client.close(); throw new DOMException('Hair startup was cancelled.', 'AbortError');
  }
  session.backend.active = client.delegate;
  return client;
}
function releaseLive(session: Session): void {
  session.generation++;
  session.cancelFrame?.(); session.cancelFrame = undefined;
  for (const cleanup of session.liveCleanups.splice(0)) cleanup();
  session.camera?.stop();
  session.detector.close();
  session.hair.close();
}
function closeSession(message = 'Camera closed. Ready whenever you are.', failed = false): void {
  const session = current;
  current = null;
  if (session) {
    releaseLive(session);
    for (const cleanup of session.cleanups.splice(0)) cleanup();
    session.abort.abort();
    session.renderer?.dispose();
    session.canvas.hidden = true;
    session.canvas.width = session.canvas.height = 0;
  }
  start.disabled = false; start.textContent = failed ? 'Try again' : 'Open camera';
  stop.hidden = true; welcome.hidden = false;
  for (const id of ['fps', 'latency', 'resolution', 'hair-latency', 'hair-wait', 'render-latency', 'changed-pixels']) element(id).textContent = '—';
  element('tracking').textContent = 'Off';
  element('download-status').textContent = '';
  download.textContent = 'Download held comparison';
  for (const key of ['frames', 'sessionId', 'frameSequence', 'sourceSha256', 'detectionSha256', 'hairStatus', 'changedPixels']) delete stage.dataset[key];
  setState(failed ? 'error' : 'idle', failed ? 'PREVIEW UNAVAILABLE' : 'CAMERA OFF', message);
  updateControls(); showHairStatus();
}
function friendlyError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError') return 'Allow camera access in your browser, then try again.';
    if (error.name === 'NotFoundError') return 'Connect a camera, then try again.';
    if (error.name === 'NotReadableError') return 'The camera could not open. Close other apps using it, then try again.';
  }
  return messageFor(error);
}

function runFrames(session: Session): void {
  const video = session.camera!.video, generation = session.generation;
  const owns = () => current === session && session.phase === 'live' && session.generation === generation;
  const capture = document.createElement('canvas'), input = document.createElement('canvas');
  const captureContext = context2D(capture), inputContext = context2D(input);
  const hairBudget = new FrameBudget<HairSegmentationResult>();
  let lastIdentity = -1, frames = 0, firstPresentedAt = 0, lastFrameAt = performance.now(), processing = false;
  const watchdog = window.setInterval(() => {
    if (owns() && !processing && performance.now() - lastFrameAt > 6000)
      closeSession('The camera stopped sending images. Open it again to restart.', true);
  }, 1000);
  session.liveCleanups.push(() => { hairBudget.close(); clearInterval(watchdog); capture.width = capture.height = input.width = input.height = 0; });
  function schedule(): void {
    if (!owns()) return;
    if (typeof video.requestVideoFrameCallback === 'function') {
      const id = video.requestVideoFrameCallback((_time, metadata) => { void process(metadata.presentedFrames); });
      session.cancelFrame = () => video.cancelVideoFrameCallback(id);
    } else {
      const id = requestAnimationFrame(() => { void process(video.currentTime); });
      session.cancelFrame = () => cancelAnimationFrame(id);
    }
  }
  async function process(identity: number): Promise<void> {
    if (!owns()) return;
    if (video.readyState < 2 || identity === lastIdentity) { schedule(); return; }
    lastIdentity = identity; processing = true; session.processing = true;
    const capturedAtMs = performance.now(), sequence = ++session.nextSequence;
    lastFrameAt = capturedAtMs;
    try {
      // Both bitmaps snapshot this immutable source before another frame is captured.
      // Optional hair inference may finish later, but its result is never used on a newer image.
      const scale = Math.min(1, 1280 / Math.max(video.videoWidth, video.videoHeight));
      const width = Math.max(1, Math.round(video.videoWidth * scale)), height = Math.max(1, Math.round(video.videoHeight * scale));
      if (capture.width !== width || capture.height !== height) {
        capture.width = width; capture.height = height;
        const detectorScale = Math.min(1, 640 / Math.max(width, height));
        input.width = Math.max(1, Math.round(width * detectorScale)); input.height = Math.max(1, Math.round(height * detectorScale));
      }
      captureContext.drawImage(video, 0, 0, width, height);
      inputContext.drawImage(capture, 0, 0, input.width, input.height);
      const sourceHashPromise = sha256(captureContext.getImageData(0, 0, width, height).data);
      const wantsHair = selectedVariant === 'hair' && session.hairReady && !session.hairError && !hairBudget.busy;
      const hairBitmapPromise = wantsHair ? createImageBitmap(capture) : Promise.resolve(null);
      // Own every bitmap even if capture, hashing, detection, or session ownership fails.
      const hairStartPromise = Promise.all([sourceHashPromise, hairBitmapPromise]).then(([sourceSHA256, bitmap]) => {
        if (!bitmap) return;
        if (!owns() || selectedVariant !== 'hair') { bitmap.close(); return; }
        const started = hairBudget.start(sequence, async () => {
          if (!owns()) { bitmap.close(); return null; }
          try { return await session.hair.segment(bitmap, sourceSHA256, sequence); }
          catch (error) {
            if (owns()) { session.hairError = messageFor(error); session.hairReady = false; session.hair.close(); }
            return null;
          }
        });
        if (!started) bitmap.close();
      }).catch(async error => {
        const bitmap = await hairBitmapPromise.catch(() => null); bitmap?.close();
        if (owns()) { session.hairError = messageFor(error); session.hairReady = false; session.hair.close(); }
      });
      const faceBitmap = await createImageBitmap(input);
      if (!owns()) { faceBitmap.close(); return; }
      const faceStarted = performance.now();
      const facePromise = session.detector.detect(faceBitmap, capturedAtMs);
      const [detection, sourceSHA256] = await Promise.all([facePromise, sourceHashPromise, hairStartPromise]);
      const faceMs = performance.now() - faceStarted;
      if (!owns()) return;
      const detectionSHA256 = await sha256(new TextEncoder().encode(JSON.stringify(detection)));
      if (!owns()) return;
      const pair = {sourceSHA256, detectionSHA256, eyewearModel: session.eyewearId};
      // Build the accepted frame privately while the independent hair worker runs.
      // Publishing happens once, after the current-pair optional result is selected.
      const renderStarted = performance.now();
      const hasFace = session.renderer!.prepare(capture, detection, pair, hairModelById(session.hairId));
      const acceptedRenderMs = performance.now() - renderStarted;
      const hairWaitStarted = performance.now();
      const segmented = await hairBudget.take(sequence, selectedVariant === 'hair' && detection.matrix ? 8 : 0);
      const hairWaitMs = performance.now() - hairWaitStarted;
      if (!owns()) return;
      let mask: HairMask | null = null;
      if (segmented && selectedVariant === 'hair') {
        if (segmented.sequence !== sequence || segmented.sourceSHA256 !== sourceSHA256) {
          session.hairError = 'The hair mask did not belong to the current camera image.';
          session.hairReady = false; session.hair.close();
        } else mask = {...segmented, detectionSHA256};
      }
      if (selectedVariant === 'hair' && session.hairReady && detection.matrix && !mask) session.budgetMisses++;
      const finishingStarted = performance.now();
      session.renderer!.finish(mask);
      const renderMs = acceptedRenderMs + performance.now() - finishingStarted;
      if (!owns()) return;
      const stats = session.renderer!.stats;
      if (!stats) throw new Error('The current rendered pair has no status.');
      session.presented = {sequence, sourceSHA256, detectionSHA256, capturedAtMs};
      stage.dataset.frameSequence = String(sequence);
      stage.dataset.sourceSha256 = sourceSHA256; stage.dataset.detectionSha256 = detectionSHA256;
      stage.dataset.hairStatus = selectedVariant === 'accepted' ? 'off' : session.hairError ? 'unavailable' : !session.hairReady ? 'loading'
        : !hasFace ? 'no-face' : stats.fallbackReason ? 'fallback' : 'ready';
      stage.dataset.changedPixels = String(stats.changedPixels);
      session.canvas.hidden = false; welcome.hidden = true;
      element('tracking').textContent = hasFace ? 'Face visible' : 'Looking for face';
      setState(hasFace ? 'tracking' : 'searching', hasFace ? 'HAIR PREVIEW LIVE' : 'LOOKING FOR YOU',
        hasFace ? 'Turn slowly and toggle the hair preview to compare.' : 'Bring your face into view and look towards the camera.');
      frames++; const now = performance.now();
      session.timing = {faceMs, hairWaitMs, renderMs, totalMs: now - capturedAtMs};
      if (!firstPresentedAt) firstPresentedAt = now;
      const seconds = (now - firstPresentedAt) / 1000;
      element('fps').textContent = frames > 1 && seconds > 0 ? `${((frames - 1) / seconds).toFixed(1)} fps` : 'Starting';
      element('latency').textContent = `${Math.round(now - capturedAtMs)} ms`;
      element('resolution').textContent = `${video.videoWidth} × ${video.videoHeight}`;
      element('hair-latency').textContent = segmented ? `${Math.round(segmented.inferenceMs + segmented.extractionMs)} ms` : '—';
      element('hair-wait').textContent = `${Math.round(hairWaitMs)} ms`;
      element('render-latency').textContent = `${Math.round(renderMs)} ms`;
      element('changed-pixels').textContent = String(stats.changedPixels);
      stage.dataset.frames = String(frames);
      processing = false; session.processing = false;
      updateControls(); showHairStatus();
      lastFrameAt = performance.now();
      if (session.holdRequested) {
        session.holdRequested = false;
        if (hasFace) { holdSession(session); return; }
      }
      schedule();
    } catch (error) { if (owns()) closeSession(friendlyError(error), true); }
  }
  schedule();
}

async function openSession(): Promise<void> {
  if (current) return;
  const previous = element<HTMLCanvasElement>('mirror'), canvas = previous.cloneNode(false) as HTMLCanvasElement;
  previous.replaceWith(canvas);
  const backend = detectHairBackend();
  const session: Session = {id: crypto.randomUUID(), phase: 'live', generation: 1, abort: new AbortController(),
    eyewearId: selectedEyewear, hairId: selectedHair, detector: new DetectorClient(),
    hair: new HairClient(selectedHair, {delegate: backend.requested, outputMode: 'category-only'}), backend,
    hairReady: false, hairError: null, canvas, liveCleanups: [], cleanups: [], nextSequence: 0, presented: null, heldAt: null,
    heldBusy: false, processing: false, holdRequested: false, budgetMisses: 0, timing: null};
  current = session; stage.dataset.sessionId = session.id;
  start.disabled = true; start.textContent = 'Opening…'; stop.hidden = false;
  setState('starting', 'STARTING CAMERA', 'Allow camera access when your browser asks.');
  updateControls(); showHairStatus();
  try {
    session.camera = await openCamera(session.abort.signal);
    if (current !== session) { session.camera.stop(); return; }
    const ended = () => { if (current === session && session.phase === 'live') closeSession('Your camera disconnected. Reconnect it and try again.', true); };
    const stream = session.camera.video.srcObject as MediaStream;
    for (const track of stream.getTracks()) {
      track.addEventListener('ended', ended);
      session.liveCleanups.push(() => track.removeEventListener('ended', ended));
    }
    const lost = (event: Event) => {
      event.preventDefault();
      if (current === session) closeSession('The graphics connection was interrupted. Open the camera again to restart.', true);
    };
    canvas.addEventListener('webglcontextlost', lost);
    session.cleanups.push(() => canvas.removeEventListener('webglcontextlost', lost));
    setState('starting', 'PREPARING MIRROR', 'Loading the glasses and local face tracker…');
    const {LiveHairRenderer} = await import('./renderer.ts');
    if (current !== session) return;
    session.renderer = await LiveHairRenderer.create(canvas, session.abort.signal, session.eyewearId);
    if (current !== session) { session.renderer.dispose(); return; }
    session.renderer.selectVariant(selectedVariant);
    // Optional hair startup never blocks a usable accepted mirror.
    void initializeHair(session, () => current === session && session.phase === 'live').then(() => {
      if (current === session && session.phase === 'live') { session.hairReady = true; showHairStatus(); }
    }).catch(error => {
      if (current === session && session.phase === 'live') {
        session.hairError = messageFor(error); session.hairReady = false; session.hair.close(); showHairStatus();
      }
    });
    await session.detector.initialize(session.abort.signal);
    if (current !== session) return;
    runFrames(session);
  } catch (error) { if (current === session) closeSession(friendlyError(error), true); }
}

for (const eyewear of Object.values(EYEWEAR)) eyewearSelect.add(new Option(eyewear.optionLabel, eyewear.id));
eyewearSelect.value = selectedEyewear;
function showEyewear(): void {
  const model = eyewearById(selectedEyewear);
  element('frame-name').textContent = model.name;
  element('frame-description').textContent = model.description;
}
eyewearSelect.addEventListener('change', () => {
  if (current) return;
  const value = eyewearSelect.value;
  if (value === 'amber-horizon' || value === 'tom-ford-clear') { selectedEyewear = value; showEyewear(); }
});
hairSelect.addEventListener('change', () => { if (!current) selectedHair = hairModelById(hairSelect.value).id; });
variantSelect.addEventListener('change', () => {
  selectedVariant = variantSelect.value === 'accepted' ? 'accepted' : 'hair';
  current?.renderer?.selectVariant(selectedVariant); updateControls(); showHairStatus();
});
element('toggle-version').addEventListener('click', () => {
  variantSelect.value = selectedVariant === 'hair' ? 'accepted' : 'hair';
  variantSelect.dispatchEvent(new Event('change'));
});
start.addEventListener('click', () => { void openSession(); });
stop.addEventListener('click', () => closeSession());
async function completeHeldHair(session: Session): Promise<void> {
  if (current !== session || session.phase !== 'held' || session.renderer?.stats?.maskOutputMode === 'full' || session.heldBusy) return;
  const input = session.renderer?.copyHeldInput();
  if (!input) return;
  const generation = session.generation;
  const owns = () => current === session && session.phase === 'held' && session.generation === generation;
  let client = new HairClient(session.hairId, {delegate: session.backend.active ?? session.backend.requested});
  session.hair = client; session.heldBusy = true; session.hairError = null; download.disabled = true; showHairStatus();
  try {
    client = await initializeHair(session, owns);
    if (!owns()) return;
    const bitmap = await createImageBitmap(input.source);
    if (!owns()) { bitmap.close(); return; }
    const result = await client.segment(bitmap, input.pair.sourceSHA256, session.presented!.sequence);
    if (!owns()) return;
    try {
      session.renderer!.present(input.source, input.detection, {...result, detectionSHA256: input.pair.detectionSHA256}, input.pair, input.expectedModel);
    } catch (error) {
      // An accepted-render failure can clear its output; do not label that blank surface a held comparison.
      if (owns()) closeSession(friendlyError(error), true);
      return;
    }
    session.hairReady = true; session.hairError = null;
    const stats = session.renderer!.stats!;
    stage.dataset.hairStatus = stats.hasMask ? 'ready' : 'fallback';
    stage.dataset.changedPixels = String(stats.changedPixels);
    element('changed-pixels').textContent = String(stats.changedPixels);
    element('hair-latency').textContent = `${Math.round(result.inferenceMs + result.extractionMs)} ms (held)`;
  } catch (error) {
    if (owns()) { session.hairError = messageFor(error); session.hairReady = false; }
  } finally {
    client.close(); input.source.width = input.source.height = 0;
    if (owns()) { session.heldBusy = false; download.disabled = false; updateControls(); showHairStatus(); }
  }
}
function holdSession(session: Session): void {
  if (current !== session || session.phase !== 'live' || !session.presented || !session.renderer) return;
  if (session.processing) {
    session.holdRequested = true; hold.disabled = true;
    element('hold-hint').textContent = 'Finishing the current paired frame, then stopping the camera…';
    return;
  }
  if (!session.renderer.stats?.hasFace) return;
  session.phase = 'held'; session.heldAt = new Date().toISOString();
  releaseLive(session);
  setState('held', 'EXACT FRAME HELD', 'Camera stopped. Toggle before and after on the same image.');
  element('tracking').textContent = 'Held image'; element('fps').textContent = 'Paused';
  updateControls(); showHairStatus(); download.disabled = false;
  void completeHeldHair(session);
}
hold.addEventListener('click', () => {
  if (current && !hold.disabled) holdSession(current);
});
resume.addEventListener('click', () => {
  if (current?.phase !== 'held') return;
  closeSession(); void openSession();
});
function diagnostic(): Record<string, unknown> | null {
  const session = current, output = session?.renderer?.exportDiagnostic();
  if (!session || !output || !session.presented) return null;
  return {...output, liveSession: {id: session.id, phase: session.phase, heldAt: session.heldAt,
    presented: {...session.presented}, sourceHashKind: 'SHA-256 of full captured RGBA bytes, unmirrored',
    detectionHashKind: 'SHA-256 of JSON.stringify of the exact validated Detection',
    performanceTimeOriginMs: performance.timeOrigin, hairError: session.hairError,
    timing: session.timing, budgetMisses: session.budgetMisses, hairWaitTimerMs: 8, backend: {...session.backend}}};
}
download.addEventListener('click', () => {
  const session = current;
  if (!session || session.phase !== 'held' || download.disabled) return;
  download.disabled = true;
  try {
    const output = diagnostic();
    if (!output) throw new Error('The held comparison is no longer available.');
    const url = URL.createObjectURL(new Blob([JSON.stringify(output)], {type: 'application/json'}));
    const link = document.createElement('a'); link.href = url;
    link.download = `hair-live-comparison-${new Date().toISOString().replaceAll(':', '-')}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 10_000);
    element('download-status').textContent = 'Comparison downloaded locally. It includes the original image and both outputs.';
  } catch (error) { element('download-status').textContent = messageFor(error); }
  finally { if (current === session) download.disabled = false; }
});
window.addEventListener('pagehide', () => closeSession());
document.addEventListener('visibilitychange', () => {
  if (document.hidden && current?.phase === 'live') closeSession('Camera closed while the page was hidden. Open it again when ready.');
});
declare global {
  interface Window { hairLivePreview: {diagnostics(): Record<string, unknown>; exportDiagnostic(): Record<string, unknown> | null}; }
}
// Read-only local inspection for exact-pair QA. It cannot start a camera or modify session state.
window.hairLivePreview = Object.freeze({
  diagnostics: () => ({state: stage.dataset.state, sessionId: current?.id ?? null, phase: current?.phase ?? null,
    presented: current?.presented ? {...current.presented} : null, hairReady: current?.hairReady ?? false,
    hairError: current?.hairError ?? null, stats: current?.renderer?.stats ?? null, variant: selectedVariant,
    timing: current?.timing ?? null, budgetMisses: current?.budgetMisses ?? 0, heldBusy: current?.heldBusy ?? false,
    processing: current?.processing ?? false, holdRequested: current?.holdRequested ?? false, backend: current ? {...current.backend} : null}),
  exportDiagnostic: diagnostic,
});
showEyewear(); updateControls(); showHairStatus();
