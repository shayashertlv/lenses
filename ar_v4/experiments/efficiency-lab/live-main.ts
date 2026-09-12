import '../../src/style.css';
import './live.css';
import {PROFILES, initialPipeline, studyPipelines, CURRENT_BASE_METADATA} from './profiles.ts';
import {UiSummaryCadence} from './ui-cadence.ts';
import {ContinuousComparisonRun, CONTINUOUS_STUDIES} from './continuous-run.ts';
import type {ContinuousRunStatus} from './continuous-run.ts';
import {ContinuousCanvasRecorder} from './continuous-recorder.ts';
import {createRunArchive} from './run-export.ts';
import {HairDeliveryLog} from './hair-delivery.ts';
import {StartupWatchdog} from './startup-watchdog.ts';
import type {StartupReceipt, StartupStage} from './startup-watchdog.ts';
import {startupPumpDiagnostic, startupPumpLabel} from './startup-pump-diagnostic.ts';
import type {StartupPumpDiagnostic} from './startup-pump-diagnostic.ts';
import {runExperimentPump} from './live-pump.ts';
import {openCamera} from '../../references/perfect-temples/src/runtime/camera.ts';
import type {CameraSession} from '../../references/perfect-temples/src/runtime/camera.ts';
import {DetectorClient} from '../performance-stage2/face-detector.ts';
import {FrameProfiler, PIPELINES, PIPELINE_LABELS, summarize} from './frame-profiler.ts';
import type {FrameSample, ProfileSummary} from './frame-profiler.ts';
import {DEFAULT_EYEWEAR_ID, EYEWEAR, eyewearById} from '../../references/perfect-temples/src/render/eyewear.ts';
import type {EyewearId} from '../../references/perfect-temples/src/render/eyewear.ts';
import {HairClient} from './hair-cost/client.ts';
import {detectHairBackend} from '../hair-live-preview/hair-backend.ts';
import type {HairBackend} from '../hair-live-preview/hair-backend.ts';
import {hairModelById} from '../hair-live-preview/models.ts';
import type {HairModelId} from '../hair-live-preview/models.ts';
import type {ComparisonRenderer as LiveHairRenderer, Pipeline} from './comparison-renderer.ts';

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
let selectedPipeline: Pipeline = initialPipeline(location.search);
const pipelineSelect = element<HTMLSelectElement>('pipeline-select');
pipelineSelect.add(new Option(PIPELINE_LABELS['hair-release'], 'hair-release'));
const focusedStudy = new URLSearchParams(location.search).get('study');
const continuousStudy = focusedStudy === 'hair-delivery' ? 'hair-delivery' : 'review';
const continuousStudyOptions = CONTINUOUS_STUDIES[continuousStudy];
const visiblePipelines = studyPipelines(location.search);
for (const option of [...pipelineSelect.options]) if (!visiblePipelines.includes(option.value as Pipeline)) option.remove();
if (focusedStudy === 'hair-delivery') {
  element('study-intro').textContent = 'Compare G with U: earlier hair processing for the next owned image. Measure completed updates, frame age and matching hair coverage.';
  element('study-notice').textContent = 'U is a separate scheduling experiment. G remains your reference; the same rendering, resolution and nose/front safeguards stay active.';
  element('continuous-title').textContent = 'Compare G and U.';
  element('continuous-protocol').textContent = 'Automatically test G, U, U, G. Each window warms for at least 5 seconds and three tracked images with matching hair masks, then measures for 30 seconds. Allow about 2.5 minutes and repeat the same movement cues.';
  element('continuous-video-hint').textContent = 'Start with measurements only to avoid video encoding load. Enable video to review tracking, hair and nose/front quality on a separate run. No audio or uploads.';
  element('baseline-detail').textContent = 'G remains your accepted baseline. U tests earlier availability of the hair worker while keeping rendering, models, resolution and exact image/pose/mask pairing. Review down, up, both yaw directions and nose/front protection with both glasses and hair models. Compare tracking and hair coverage as well as updates, image age and stalls; no candidate has been accepted.';
  element('hair-delivery-study').setAttribute('aria-current', 'page');
} else if (focusedStudy === 'review') {
  element('review-study').setAttribute('aria-current', 'page');
  element('study-intro').textContent = 'Compare G with four separate ideas: fewer repeat uploads, smaller temple downloads, leaner temple rendering and lighter statistics.';
  element('study-notice').textContent = 'Q–T are separate experiments. Switch while moving, then Hold for the same-image comparison. G remains your reference.';
} else if (focusedStudy === 'mask') {
  element('study-intro').textContent = 'Compare G with P: fewer temporary arrays while reading the hair mask, with no added processing-rate cap.';
  element('study-notice').textContent = 'P tests cheaper work per image. Earlier efficiency and reduced-rate experiments remain available in the full lab.';
}
pipelineSelect.value = selectedPipeline;
element('experiment-detail').textContent = PROFILES[selectedPipeline].detail;
const profiler = new FrameProfiler();
const benchmark = element<HTMLButtonElement>('benchmark');
const downloadMetrics = element<HTMLButtonElement>('download-metrics');
let lastProfileUiAt = 0;
let lastComparisonRevision = -1;

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
  presented: {sequence: number; sourceSHA256: string; detectionSHA256: string; capturedAtMs: number; pipeline: Pipeline} | null;
  heldAt: string | null;
  heldBusy: boolean;
  processing: boolean;
  holdRequested: boolean;
  budgetMisses: number;
  timing: {faceMs: number; hairWaitMs: number; renderMs: number; totalMs: number} | null;
  performanceSample: FrameSample | null;
  pump?: ReturnType<typeof runExperimentPump>;
  switching?: boolean;
  openedAtMs:number;
  firstPublishedAtMs:number|null;
  firstMaskedAtMs:number|null;
  uiCadence:UiSummaryCadence;
  startup: StartupInfo | null;
}
let current: Session | null = null;

interface StartupInfo {
  watchdog: StartupWatchdog;
  progressTimer: number | null;
  error: string | null;
  milestones: {name: string; atMs: number}[];
  frameProgress: StartupPumpDiagnostic | null;
  workload: {eyewearId: EyewearId; hairModelId: HairModelId; variant: 'accepted' | 'hair'; pipeline: Pipeline};
}
let lastStartupReport: Record<string, unknown> | null = null;
const STARTUP_LABELS: Record<StartupStage, string> = {
  module: 'Loading mirror code', 'g-renderer': 'Preparing G glasses',
  'candidate-renderer': 'Preparing comparison glasses', face: 'Starting face tracking',
  'first-ar': 'Waiting for the first AR image',
};
function startupErrorText(value: unknown): string {
  return (value instanceof Error ? value.message : String(value)).replace(/data:[^\s"']+/gi, '[data URL omitted]').slice(0, 1000);
}
function startupCapabilities(): Record<string, unknown> {
  return {offscreenCanvasMain: typeof OffscreenCanvas !== 'undefined', offscreenCanvasWorker: null,
    workerCapabilityStatus: 'Not reported by the worker; main-thread support does not establish worker support.',
    requestVideoFrameCallback: typeof HTMLVideoElement.prototype.requestVideoFrameCallback === 'function',
    canvasCaptureStream: typeof HTMLCanvasElement.prototype.captureStream === 'function',
    mediaRecorder: typeof MediaRecorder !== 'undefined', createImageBitmap: typeof createImageBitmap === 'function',
    workerConstructor: typeof Worker !== 'undefined', webAssembly: typeof WebAssembly !== 'undefined',
    crossOriginIsolated, secureContext: isSecureContext};
}
function startupResourceTimings(): Record<string, unknown>[] {
  return performance.getEntriesByType('resource').slice(-120).flatMap(entry => {
    try {
      const url = new URL(entry.name, location.href);
      if (url.origin !== location.origin || !/^\/(ar_testing\/|models\/|mediapipe\/|assets\/|experiments\/)/.test(url.pathname)) return [];
      const timing = entry as PerformanceResourceTiming & {responseStatus?: number};
      return [{path: url.pathname.slice(0, 400), initiatorType: timing.initiatorType, startTimeMs: timing.startTime,
        durationMs: timing.duration, transferBytes: timing.transferSize, encodedBytes: timing.encodedBodySize,
        decodedBytes: timing.decodedBodySize, responseStatus: typeof timing.responseStatus === 'number' ? timing.responseStatus : null}];
    } catch {return [];}
  });
}
function startupFrameProgress(session: Session, settled = false): StartupPumpDiagnostic {
  if (session.startup?.frameProgress) return structuredClone(session.startup.frameProgress);
  const video = session.camera?.video;
  return startupPumpDiagnostic(session.pump?.stats() ?? null, video ? {readyState: video.readyState,
    paused: video.paused, ended: video.ended, currentTime: video.currentTime,
    width: video.videoWidth, height: video.videoHeight} : null, performance.now(),
  session.firstPublishedAtMs !== null, settled);
}
function startupReport(session: Session, includeResources = false): Record<string, unknown> | null {
  const startup = session.startup;
  if (!startup) return null;
  return {schema: 'ar-startup-diagnostic-v1', ...CURRENT_BASE_METADATA,
    build: {id: import.meta.env.VITE_AR_BUILD_ID ?? null, createdAt: import.meta.env.VITE_AR_BUILD_AT ?? null},
    createdAt: new Date().toISOString(), performanceTimeOriginMs: performance.timeOrigin,
    cameraRequestedAtMs: session.openedAtMs, startup: startup.watchdog.snapshot(),
    frameProgress: startupFrameProgress(session),
    frameProgressPolicy: 'Only allowlisted scalar counters and video state. Preserved before startup cleanup or immediately at the first publication callback. The pump publication count increments after that callback returns; publicationObserved records the callback itself. Concurrent face/hair work is not a serial stage timeline.',
    milestones: startup.milestones.map(value => ({...value})),
    workload: {...startup.workload},
    workloadPolicy: 'Selections requested when this camera session opened. Later controls do not relabel the startup attempt.',
    device: deviceMetadata(session),
    capabilities: startupCapabilities(),
    acceleration: {hairRequested: session.backend.requested, hairActive: session.backend.active,
      faceActive: session.detector.delegate, renderer: session.backend.renderer,
      hairFallbackReason: session.backend.fallbackReason ? startupErrorText(session.backend.fallbackReason) : null},
    errors: {startup: startup.error, hair: session.hairError ? startupErrorText(session.hairError) : null},
    privacy: 'Startup status, selected workload and bounded scalar device details only. No camera images, image identities, detections, landmarks, hair masks, device IDs or camera labels.',
    clock: 'Milestones use performance.now on this page. Mirror setup deadlines begin after the camera is ready; camera permission and playback keep their existing separate deadlines.',
    ...(includeResources ? {resourceTimings: startupResourceTimings(),
      resourceTimingPolicy: 'At most the latest 120 page resource entries, restricted to same-origin AR asset paths with query strings omitted. No request bodies or response contents. Zero sizes/status may mean browser metadata is unavailable.'} : {})};
}
function showStartupProgress(session: Session): void {
  const info = session.startup;
  if (!info) return;
  const receipt = info.watchdog.snapshot();
  const label = receipt.stage === 'first-ar' && receipt.state === 'running'
    ? startupPumpLabel(startupFrameProgress(session)) : receipt.stage ? STARTUP_LABELS[receipt.stage] : 'Opening camera';
  let message: string;
  if (receipt.state === 'idle') message = `Opening camera · ${Math.floor((performance.now() - session.openedAtMs) / 1000)} s elapsed`;
  else if (receipt.state === 'running') message = `${label} · ${Math.floor(receipt.stageElapsedMs / 1000)} s elapsed`;
  else if (receipt.state === 'complete') message = `Mirror ready · ${(receipt.elapsedMs / 1000).toFixed(1)} s to prepare after the camera opened.`;
  else message = `Setup ${receipt.state === 'cancelled' ? 'cancelled' : 'stopped'} · ${label}. ${info.error ?? receipt.reason ?? 'Save the startup report for details.'}`;
  for (const id of ['startup-status', 'startup-stage-progress']) {
    const target = element(id); if (target.textContent !== message) target.textContent = message;
  }
  element('startup-stage-progress').hidden = receipt.state === 'complete';
  element('startup-panel').hidden = false;
  stage.dataset.startupStage = receipt.stage ?? 'camera';
  stage.dataset.startupState = receipt.state;
}
function enterStartupStage(session: Session, phase: StartupStage): void {
  if (current !== session || session.abort.signal.aborted || !session.startup) return;
  const previous = session.startup.watchdog.snapshot().stage;
  session.startup.watchdog.enter(phase);
  if (current !== session || session.abort.signal.aborted) return;
  if (previous !== phase) session.startup.milestones.push({name: `stage-${phase}`, atMs: performance.now()});
  setState('starting', STARTUP_LABELS[phase].toUpperCase(), `${STARTUP_LABELS[phase]}… You can close the camera to cancel.`);
  showStartupProgress(session);
}
function settleStartup(session: Session, failed: boolean, message: string): void {
  const info = session.startup;
  if (!info) return;
  // Snapshot once while this attempt still owns its camera/pump resources.
  // Later cancelled callbacks, pipeline switches and cleanup cannot rewrite it.
  info.frameProgress ??= startupFrameProgress(session, true);
  if (info.progressTimer !== null) window.clearInterval(info.progressTimer);
  info.progressTimer = null;
  const receipt = info.watchdog.snapshot();
  if (receipt.state !== 'complete') {
    if (failed) {info.error = startupErrorText(message); info.watchdog.fail(info.error);}
    else info.watchdog.cancel('Camera session closed.');
  }
  showStartupProgress(session); lastStartupReport = startupReport(session);
}
function latestStartupReport(includeResources = false): Record<string, unknown> | null {
  if (current?.startup) return startupReport(current, includeResources);
  if (!lastStartupReport) return null;
  return {...structuredClone(lastStartupReport), ...(includeResources ? {
    resourceTimings: startupResourceTimings(),
    resourceTimingPolicy: 'At most the latest 120 page resource entries, restricted to same-origin AR asset paths with query strings omitted. No request bodies or response contents. Zero sizes/status may mean browser metadata is unavailable.',
  } : {})};
}
function saveStartupReport(): void {
  if (continuousActive()) return;
  const report = latestStartupReport(true);
  if (!report) return;
  const json = JSON.stringify(report, null, 2);
  element<HTMLTextAreaElement>('startup-json').value = json;
  element<HTMLDetailsElement>('startup-report-copy').open = true;
  element<HTMLButtonElement>('select-startup-json').disabled = false;
  element('startup-export-status').textContent = 'Startup JSON is ready. If the file does not appear, select and copy it below. No images or face data are included.';
  let url: string | null = null;
  try {
    url = URL.createObjectURL(new Blob([json], {type: 'application/json'}));
    const link = document.createElement('a'); link.href = url;
    link.download = `ar-startup-${new Date().toISOString().replaceAll(':', '-')}.json`;
    document.body.append(link); link.click(); link.remove();
  } catch {
    element('startup-export-status').textContent = 'The file download could not start. Select and copy the startup JSON below.';
  } finally {
    if (url) {const ownedUrl = url; window.setTimeout(() => URL.revokeObjectURL(ownedUrl), 10000);}
  }
}

interface ContinuousContext {
  controller: ContinuousComparisonRun;
  session: Session;
  recorder: ContinuousCanvasRecorder;
  timer: number | null;
  videoCallback: number | null;
  switchToken: number | null;
  finalizing: boolean;
  startedAtMs: number;
  wakeLock: WakeLockSentinel | null;
  wakeLockAcquired: boolean;
  wakeLockReason: string | null;
  events: {name: string; atMs: number; durationMs: number | null}[];
  hairDelivery: HairDeliveryLog | null;
  hairDrain: {state: 'drained' | 'incomplete'; startedAtMs: number; endedAtMs: number; reason: string | null} | null;
}
let continuousRun: ContinuousContext | null = null;
let continuousFile: File | null = null;
let continuousFileUrl: string | null = null;
let lastContinuousReport: Record<string, unknown> | null = null;
const continuousStart = element<HTMLButtonElement>('continuous-start');
const continuousStop = element<HTMLButtonElement>('continuous-stop');
const continuousVideo = element<HTMLInputElement>('continuous-video');
continuousVideo.checked = continuousStudyOptions.defaultVideo;
function showContinuousRecordingChoice(): void {
  continuousStart.textContent = `${continuousVideo.checked ? 'Video + measurements' : 'Measure only'} · ${continuousStudy === 'hair-delivery' ? 'G vs U' : 'all five options'} · ~${continuousStudyOptions.approximateMinutes} min`;
}
continuousVideo.addEventListener('change', showContinuousRecordingChoice);
showContinuousRecordingChoice();

function setContinuousText(id: string, value: string): void {
  const node = element(id); if (node.textContent !== value) node.textContent = value;
}
function deviceMetadata(session: Session): Record<string, unknown> {
  const extendedNavigator = navigator as Navigator & {deviceMemory?: number};
  const stream = session.camera?.video.srcObject as MediaStream | null;
  const settings = stream?.getVideoTracks()[0]?.getSettings();
  return {userAgent: navigator.userAgent, platform: navigator.platform, hardwareConcurrency: navigator.hardwareConcurrency ?? null,
    deviceMemoryGiB: extendedNavigator.deviceMemory ?? null, viewport: {width: innerWidth, height: innerHeight, dpr: devicePixelRatio},
    camera: settings ? {width: settings.width ?? null, height: settings.height ?? null, frameRate: settings.frameRate ?? null,
      facingMode: settings.facingMode ?? null, aspectRatio: settings.aspectRatio ?? null} : null,
    crossOriginIsolated, secureContext: isSecureContext,
    cameraDeliveryObservation: typeof session.camera?.video.requestVideoFrameCallback === 'function' ? 'independent-video-callback' : 'unavailable',
    longTaskObservation: typeof PerformanceObserver !== 'undefined' && PerformanceObserver.supportedEntryTypes.includes('longtask')};
}
function continuousActive(): boolean {return continuousRun !== null;}
function showContinuousStatus(context: ContinuousContext, status: ContinuousRunStatus): void {
  if (continuousRun !== context || context.finalizing) return;
  const seconds = Math.max(0, Math.ceil(status.remainingMs / 1000));
  const phase = status.state === 'switching' ? 'Switching safely'
    : status.state === 'warmup' ? 'Warming up' : status.state === 'measuring' ? 'Measuring' : status.state;
  const label = `${status.windowIndex + 1}/${status.windowCount} · ${PIPELINE_LABELS[status.pipeline]} · ${phase}`;
  setContinuousText('continuous-status', `${label}${status.running && status.state !== 'switching' ? ` · ${seconds} s` : ''}. Keep this page visible.`);
  setContinuousText('run-stage-progress', `${label}${status.state === 'measuring' || status.state === 'warmup' ? ` · ${seconds} s` : ''}`);
  const cues = ['Face forward · check nose/front', 'Slowly look down', 'Slowly look up', 'Slowly turn left', 'Slowly turn right'];
  const cue = status.state === 'measuring' ? cues[Math.min(4, Math.floor(status.phaseElapsedMs / 6000))]! : 'Face forward · get ready';
  setContinuousText('run-movement', cue);
}
function recordVideoDelivery(context: ContinuousContext): void {
  const video = context.session.camera?.video;
  if (!video || typeof video.requestVideoFrameCallback !== 'function') return;
  const observe: VideoFrameRequestCallback = (atMs, info) => {
    context.videoCallback = null;
    if (continuousRun !== context || context.finalizing || current !== context.session) return;
    context.controller.observeVideo({atMs, presentedFrames: info.presentedFrames, mediaTime: info.mediaTime});
    if (!context.controller.status.running) driveContinuous(context);
    if (continuousRun === context && !context.finalizing) context.videoCallback = video.requestVideoFrameCallback(observe);
  };
  context.videoCallback = video.requestVideoFrameCallback(observe);
}
async function switchContinuous(context: ContinuousContext, status: ContinuousRunStatus): Promise<void> {
  if (context.switchToken === status.token || context.finalizing) return;
  context.switchToken = status.token;
  const session = context.session, pump = session.pump;
  try {
    if (session.switching) throw new Error('A previous manual algorithm switch was still finishing.');
    session.switching = true;
    if (pump) {await pump.finishCurrent(); pump.stop();}
    if (current !== session || session.phase !== 'live') return;
    session.pump = undefined; session.processing = false;
    if (continuousRun !== context || context.finalizing || !context.controller.status.running) {
      // Stopping a run while a switch drains must not strand the manual mirror
      // with a stopped pump. Resume its last selected algorithm in this session.
      session.switching = false;
      // The focused finalizer owns the restart after collecting late hair
      // outcomes. Starting here would create fresh requests during that drain.
      if (!context.hairDelivery) runFrames(session);
      return;
    }
    selectedPipeline = status.pipeline; pipelineSelect.value = status.pipeline;
    element('experiment-detail').textContent = PROFILES[status.pipeline].detail;
    session.renderer!.selectPipeline(status.pipeline);
    // The old pump is drained before this capture boundary. Even adjacent T
    // windows are distinct windows; no old-pipeline source can enter the next one.
    context.controller.switched(status.token, performance.now());
    session.switching = false; runFrames(session);
    updateControls(); showHairStatus();
  } catch (error) {if (current === session) closeSession(`Algorithm switch failed: ${messageFor(error)}`, true);}
  finally {session.switching = false;}
}
function driveContinuous(context = continuousRun): void {
  if (!context || continuousRun !== context || context.finalizing) return;
  const status = context.controller.tick(performance.now());
  showContinuousStatus(context, status);
  if (!status.running) {void finishContinuous(context); return;}
  if (status.state === 'switching') void switchContinuous(context, status);
}
async function keepScreenAwake(context: ContinuousContext): Promise<void> {
  if (!('wakeLock' in navigator)) {context.wakeLockReason = 'Screen wake lock is unavailable.'; return;}
  try {
    const lock = await navigator.wakeLock.request('screen');
    if (continuousRun !== context || context.finalizing) {await lock.release(); return;}
    context.wakeLock = lock; context.wakeLockAcquired = true;
    lock.addEventListener('release', () => {context.wakeLock = null; context.events.push({name: 'wake-lock-released', atMs: performance.now(), durationMs: null});});
  } catch (error) {context.wakeLockReason = messageFor(error);}
}
function saveContinuousFile(): void {
  if (!continuousFile || !continuousFileUrl) return;
  const link = document.createElement('a'); link.href = continuousFileUrl; link.download = continuousFile.name;
  document.body.append(link); link.click(); link.remove();
}
function canShareContinuousFile(): boolean {
  try {return !!continuousFile && typeof navigator.canShare === 'function' && navigator.canShare({files: [continuousFile]});}
  catch {return false;}
}
async function drainContinuousHair(context: ContinuousContext): Promise<void> {
  const session = context.session, pump = session.pump, startedAtMs = performance.now();
  session.switching = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    if (pump) await Promise.race([pump.finishCurrent(), new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error('Pending frame work did not finish within 20 seconds.')), 20000);
    })]);
    context.hairDrain = {state: 'drained', startedAtMs, endedAtMs: performance.now(), reason: null};
  } catch (error) {
    const reason = messageFor(error);
    context.hairDrain = {state: 'incomplete', startedAtMs, endedAtMs: performance.now(), reason};
    if (current === session) closeSession('The test stopped while finishing pending frame work. Save the partial diagnostic file, then reopen the camera.', true);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    pump?.stop();
    if (session.pump === pump) session.pump = undefined;
    session.processing = false;
  }
}
async function finishContinuous(context: ContinuousContext): Promise<void> {
  if (continuousRun !== context || context.finalizing) return;
  context.finalizing = true;
  if (context.timer !== null) window.clearInterval(context.timer);
  if (context.videoCallback !== null) context.session.camera?.video.cancelVideoFrameCallback(context.videoCallback);
  context.timer = context.videoCallback = null;
  const wakeLock = context.wakeLock; context.wakeLock = null;
  if (wakeLock) void wakeLock.release().catch(() => {});
  element('run-stage-cue').hidden = true;
  continuousStop.disabled = true;
  setContinuousText('continuous-status', 'Finishing the local recording and preparing one comparison file…');
  // End optional encoding at the measurement boundary. Late hair outcomes are
  // collected separately and never become measured frames or extra video.
  const stoppedVideo = context.recorder.stop(performance.now());
  if (context.hairDelivery) await drainContinuousHair(context);
  const video = await stoppedVideo;
  const recording = context.recorder.snapshot();
  const report = {...context.controller.export(), recording, deviceAtEnd: deviceMetadata(context.session),
    clientEvents: context.events, wakeLock: {requested: true, acquired: context.wakeLockAcquired, reason: context.wakeLockReason},
    ...(context.hairDelivery ? {hairDelivery: context.hairDelivery.export(), hairDeliveryDrain: context.hairDrain} : {})};
  lastContinuousReport = report;
  try {
    const extension = recording.mimeType?.includes('mp4') ? 'mp4' : 'webm';
    const archive = await createRunArchive(report, video ? {blob: video, filename: `ar-mirror.${extension}`} : undefined);
    if (continuousFileUrl) URL.revokeObjectURL(continuousFileUrl);
    continuousFile = new File([archive], `ar-mobile-comparison-${new Date().toISOString().replaceAll(':', '-')}.zip`, {type: 'application/zip'});
    continuousFileUrl = URL.createObjectURL(continuousFile);
    element('continuous-save-actions').hidden = false;
    element('continuous-share').hidden = !canShareContinuousFile();
    const status = context.controller.status;
    setContinuousText('continuous-status', context.hairDrain?.state === 'incomplete'
      ? 'Measurements were retained, but pending frame work could not finish. Save the diagnostic ZIP, then reopen the camera. Unresolved hair requests remain identified.'
      : status.state === 'complete'
      ? `All ${status.windowCount} measurement windows finished. Save the comparison ZIP and send it back for analysis.`
      : `Partial recording retained: ${status.reason ?? 'The test stopped.'} Save the ZIP; its incomplete windows stay identified.`);
    setContinuousText('continuous-recording-status', video
      ? `${(continuousFile.size / (1024 * 1024)).toFixed(1)} MB · timings and AR video${recording.status === 'failed' ? ' (video partial)' : ''}. ${recording.reason ?? ''}`
      : `Timings saved without video. ${recording.reason ?? 'Video was turned off.'}`);
    // One completion attempt only; iOS may require the explicit Save or Share
    // button because this callback no longer has a user activation.
    try {saveContinuousFile();} catch {setContinuousText('continuous-recording-status', 'Your comparison file is ready. Tap Save or Share to keep it.');}
  } catch (error) {
    continuousFile = new File([JSON.stringify(report)], `ar-mobile-comparison-${new Date().toISOString().replaceAll(':', '-')}.json`, {type: 'application/json'});
    if (continuousFileUrl) URL.revokeObjectURL(continuousFileUrl);
    continuousFileUrl = URL.createObjectURL(continuousFile); element('continuous-save-actions').hidden = false;
    element('continuous-share').hidden = true;
    setContinuousText('continuous-status', `Archive preparation failed: ${messageFor(error)}. Save the retained timing JSON.`);
  } finally {
    if (continuousRun === context) continuousRun = null;
    if (context.hairDelivery) {
      const session = context.session;
      session.switching = false;
      if (context.hairDrain?.state === 'drained' && current === session && session.phase === 'live'
        && !session.abort.signal.aborted && !session.pump && session.camera
        && (session.camera.video.srcObject as MediaStream | null)?.getVideoTracks().some(track => track.readyState === 'live')) {
        runFrames(session);
      }
    }
    continuousStop.disabled = false; updateControls();
    if (matchMedia('(max-width: 720px)').matches) {
      element('continuous-save-actions').scrollIntoView({block: 'center', behavior: 'auto'});
    }
  }
}
function cancelContinuous(reason: string): void {
  const context = continuousRun;
  if (!context || context.finalizing) return;
  context.controller.cancel(reason, performance.now());
  void finishContinuous(context);
}
function beginContinuous(): void {
  const session = current;
  if (!session || continuousActive() || continuousStart.disabled || !session.performanceSample) return;
  // The UI explains that starting another test replaces the result held on the
  // page. Release it before recording so two videos do not compete for memory.
  if (continuousFileUrl) URL.revokeObjectURL(continuousFileUrl);
  continuousFileUrl = null; continuousFile = null; lastContinuousReport = null;
  profiler.cancel('Continuous comparison started.');
  const sample = session.performanceSample;
  const recorder = new ContinuousCanvasRecorder(session.canvas, continuousVideo.checked,
    {onIssue: reason => cancelContinuous(`Video recording interrupted: ${reason}`)});
  const controller = new ContinuousComparisonRun({sessionId: session.id, studyOptions: continuousStudy,
    workload: {eyewearId: session.eyewearId, hairModelId: session.hairId, variant: selectedVariant,
      sourceWidth: sample.sourceWidth, sourceHeight: sample.sourceHeight},
    metadata: {...CURRENT_BASE_METADATA, build: {id: import.meta.env.VITE_AR_BUILD_ID ?? null, createdAt: import.meta.env.VITE_AR_BUILD_AT ?? null},
      device: deviceMetadata(session), recording: recorder.snapshot(), performanceTimeOriginMs: performance.timeOrigin,
      sessionStartup: {openedAtMs: session.openedAtMs, firstPublishedAtMs: session.firstPublishedAtMs, firstMaskedAtMs: session.firstMaskedAtMs},
      movementProtocol: 'Repeat five six-second cues: front/nose, down, up, left, right. Glasses and hair model stay fixed; repeat the test for the other model combinations.'}});
  const context: ContinuousContext = {controller, session, recorder, timer: null, videoCallback: null, switchToken: null,
    finalizing: false, startedAtMs: performance.now(), wakeLock: null, wakeLockAcquired: false, wakeLockReason: null, events: [],
    hairDelivery: continuousStudy === 'hair-delivery' ? new HairDeliveryLog(session.id) : null, hairDrain: null};
  continuousRun = context;
  element('continuous-save-actions').hidden = true; element('run-stage-cue').hidden = false;
  setContinuousText('continuous-recording-status', recorder.snapshot().status === 'recording'
    ? 'Recording the AR mirror continuously, with no audio. All files remain in this browser until you save or share them.'
    : recorder.snapshot().reason ?? 'Timings only; video is turned off.');
  controller.begin(context.startedAtMs);
  context.timer = window.setInterval(() => driveContinuous(context), 250);
  recordVideoDelivery(context); void keepScreenAwake(context); updateControls(); driveContinuous(context);
  if (matchMedia('(max-width: 720px)').matches) stage.scrollIntoView({block: 'start', behavior: 'auto'});
}

function lighterUi(): boolean {return PROFILES[current?.performanceSample?.pipeline ?? selectedPipeline].throttleUi;}
function writeText(id: string, text: string): void {
  const target=element(id);if(!lighterUi()||target.textContent!==text)target.textContent=text;
}
function writeValue<T extends object,K extends keyof T>(target:T,key:K,value:T[K]):void {
  if(!lighterUi()||target[key]!==value)target[key]=value;
}

function updatePublicationStatus(): void {
  const session=current, sample=session?.performanceSample;
  let text='Camera off · values describe the last completed window.';
  if(session?.phase==='held')text='Held · values describe the last completed live window.';
  else if(session){
    const age=sample?Math.max(0,performance.now()-sample.publishedAtMs):null;
    text=age===null?'Waiting for the first AR update.':age>=1000
      ?`Waiting for an AR update · ${(age/1000).toFixed(1)} s since last completion; values describe the last completed window.`
      :'Live · values describe the last completed window.';
  }
  const label=element('profile-freshness');if(label.textContent!==text)label.textContent=text;
}

const messageFor = (error: unknown): string => error instanceof Error ? error.message : String(error);
function setState(state: string, label: string, message: string): void {
  writeValue(stage.dataset,'state',state);
  writeValue(stageStatus,'textContent',label);
  if (guidance.textContent !== message) guidance.textContent = message;
}
function updateControls(): void {
  const held = current?.phase === 'held';
  const recording = continuousActive();
  writeValue(eyewearSelect,'disabled',current!==null);writeValue(hairSelect,'disabled',current!==null);
  writeText('selection-hint',current
    ? 'Close this session to change glasses or hair model.'
    : 'Choose glasses and a hair model before opening the camera.');
  writeValue(hold,'hidden',held);
  writeValue(hold,'disabled',recording || current?.phase !== 'live' || !current.presented || stage.dataset.state !== 'tracking' || current.holdRequested);
  writeValue(resume,'hidden',!held);
  writeValue(download,'hidden',!held);
  if (!held) writeValue(download,'disabled',true);
  writeValue(stop,'textContent',held ? 'Close held frame' : 'Close camera');
  writeText('hold-hint',held
    ? 'Camera stopped. All versions show this exact held image. Rate options share G’s held output; judge their smoothness live. Resume starts a fresh session.'
    : 'Switch algorithms while moving. Hold a frame to compare their appearance on exactly the same image.');
  writeValue(stage.dataset,'variant',selectedVariant);
  writeValue(pipelineSelect,'disabled',recording || !!current?.heldBusy);
  writeValue(variantSelect,'disabled',recording);
  writeValue(element<HTMLButtonElement>('toggle-version'),'disabled',recording);
  writeValue(element<HTMLButtonElement>('toggle-pipeline'),'disabled',pipelineSelect.disabled);
  writeValue(element<HTMLButtonElement>('stage-toggle-pipeline'),'disabled',pipelineSelect.disabled);
  const displayedPipeline = current?.phase === 'held' ? current.renderer!.pipeline : current?.presented?.pipeline ?? selectedPipeline;
  writeValue(stage.dataset,'pipeline',displayedPipeline);
  writeValue(stage.dataset,'requestedPipeline',selectedPipeline);
  writeText('active-pipeline',PIPELINE_LABELS[displayedPipeline]);
  writeText('stage-toggle-pipeline',`${PIPELINE_LABELS[displayedPipeline]} · switch`);
  if (!current?.performanceSample) showRateStatus(selectedPipeline);
  writeValue(benchmark,'disabled',recording || !current?.presented || held || current.heldBusy || profiler.running || selectedPipeline==='g');
  writeValue(element<HTMLSelectElement>('benchmark-order'),'disabled',recording);
  writeValue(downloadMetrics,'disabled',recording || !profiler.hasSamples);
  writeValue(element<HTMLButtonElement>('download-startup'),'disabled',recording || !current?.startup && !lastStartupReport);
  writeValue(continuousStart,'disabled',recording || current?.phase !== 'live' || !!current.switching || !!current.holdRequested
    || !current.performanceSample?.hasFace || selectedVariant === 'hair' && !current.performanceSample.hasMask);
  writeValue(continuousVideo,'disabled',recording);
  writeValue(continuousStop,'hidden',!recording);
  writeValue(continuousStart,'hidden',recording);
  updatePublicationStatus();
}
function showHairStatus(): void {
  const session = current;
  writeText('hair-engine',session?.backend.active ?? (session ? 'Preparing' : '—'));
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
    writeText('hair-detail',session.hairError ?? fallback ?? '');
  } else writeText('hair-detail','');
  if (element('hair-status').textContent !== text) element('hair-status').textContent = text;
}

function showRateStatus(pipeline: Pipeline, native?: FrameSample['native']): void {
  const rate = PROFILES[pipeline].captureRateHz;
  const skipped = native?.['admission.rateSkipped'];
  writeText('rate-status',rate === null
    ? 'No added processing-rate cap. The measured AR update rate depends on available processing time.'
    : `At most ${rate} new images/s; actual updates may be lower. `
      + (typeof skipped === 'number' ? `${skipped} camera images skipped by this cap since the last switch. ` : '')
      + 'The last complete AR image stays visible between updates.');
}

function updateProfileUi(session?: Session, sharedSummary?: ProfileSummary, force=false): void {
  writeText('benchmark-status',profiler.progress(performance.now()));
  updateCompletedComparison();
  if (!session?.performanceSample || !force&&performance.now()-lastProfileUiAt<500) return;
  lastProfileUiAt=performance.now();
  const summary=sharedSummary??summarize(profiler.recent(session.id,session.performanceSample.pipeline,selectedVariant));
  const native=session.performanceSample.native;
  showRateStatus(session.performanceSample.pipeline, native);
  const maskPath = native?.['hairCategory.path'], maskTotal = native?.['hairCategory.totalMs'];
  const maskDescription = maskPath === 'direct-float-conversion' ? 'Direct conversion exercised'
    : maskPath === 'direct-byte-copy' ? 'Already bytes; no conversion savings'
    : maskPath === 'sdk-copy' ? 'SDK extraction control' : 'No completed hair extraction on this frame';
  writeText('mask-cost-status','Last hair mask: ' + maskDescription
    + (typeof maskTotal === 'number' ? ` · ${maskTotal.toFixed(1)} ms extraction` : '') + '.');
  const metric=(suffix:string)=>Object.entries(native??{}).find(([key])=>key.endsWith(suffix))?.[1];
  const active=(value:unknown):string=>value===true?'active':value===false?'off / fallback':'—';
  const pooled=Object.entries(native??{}).some(([key,value])=>key.endsWith('scratchBytesReused')&&typeof value==='number'&&value>0);
  const deferred=metric('pump.deferPrefetch')===true
    ? typeof metric('pump.nativeSubmittedMs')==='number'?'submission observed':'not exercised':'off';
  const lean=metric('input.leanInputs')===true&&metric('input.hashExplicitCopyBytes')===0;
  const temple=metric('efficiencyLab.asyncTemplesRequested')!==true?'off'
    :metric('efficiencyLab.asyncTemplesUsed')===true?'active'
    :metric('zeroDropBranchSkipped')===true?'not needed'
    :metric('speedLab.prewarmAttempted')===true?'warming'
    :typeof metric('efficiencyLab.branchFallback')==='string'?'fallback':'not exercised';
  writeText('actual-path',`Last live frame (${PIPELINE_LABELS[session.performanceSample.pipeline]}) · Beauty async: `+active(metric('asyncReadbackUsed'))
    +' · Download memory reused: '+(pooled?'yes':'no')+' · Lean hash input: '+(lean?'used':'off')
    +' · Later-start milestone: '+deferred+' · Temple async: '+temple
    +' · Repeat upload skipped: '+(metric('publication.prePrepareSkippedThisFrame')===true?'yes':'no')
    +' · Temple region download: '+active(metric('branchRegion.used'))
    +' · Branch lenses omitted: '+active(metric('branchLenses.used'))
    +' · Statistics cadence: '+(metric('ui.throttleSummariesRequested')===true?'500 ms':'every image'));
  const ms=(value: number | undefined | null): string => value===null || value===undefined ? '—' : `${value.toFixed(1)} ms`;
  writeText('profile-fps',summary.processedFps===null ? 'Starting' : `${summary.processedFps.toFixed(1)} fps`);
  writeText('profile-video-fps',summary.videoDeliveryFps===null ? 'Unavailable' : `${summary.videoDeliveryFps.toFixed(1)} fps`);
  writeText('profile-p95',ms(summary.processing?.p95));
  writeText('profile-interval-p95',ms(summary.frameInterval?.p95));
  writeText('profile-face',ms(summary.stages.faceInferenceMs?.median));
  writeText('profile-face-roundtrip',ms(summary.stages.faceRequestWallMs?.median));
  writeText('profile-scheduler',ms(summary.stages.schedulerWaitMs?.median));
  writeText('profile-copy',ms(summary.stages.sourceReadbackMs?.median));
  writeText('profile-prepare',ms(summary.stages.prepareMs?.median));
  writeText('profile-finish',ms(summary.stages.finishMs?.median));
  writeText('profile-coverage',summary.hairCoverage===null ? '—' : `${Math.round(summary.hairCoverage*100)}%`);
  writeText('profile-count',`${summary.frames} completed frames · last ${Math.round(summary.durationMs/1000)} seconds`);
}

function updateCompletedComparison(): void {
  if (lastComparisonRevision === profiler.comparisonRevision) return;
  lastComparisonRevision = profiler.comparisonRevision;
  const completed = profiler.completedSegments;
  element('benchmark-comparison').hidden = completed.length === 0;
  const body = element<HTMLTableSectionElement>('benchmark-results');
  body.replaceChildren();
  const percent = (fraction: number | null): string => fraction === null ? '—' : `${(fraction * 100).toFixed(1)}%`;
  for (const segment of completed) {
    const row = document.createElement('tr'); row.dataset.pipeline = segment.pipeline; row.dataset.coverage = segment.coverage.status;
    const label = document.createElement('th'); label.scope = 'row'; label.textContent = PIPELINE_LABELS[segment.pipeline];
    const status = document.createElement('small');
    status.textContent = {full:'Full coverage',partial:'Partial coverage','no-tracking':'No tracking','no-frames':'No frames'}[segment.coverage.status];
    label.append(status); row.append(label);
    const fields = {
      fps: segment.summary.processedFps === null ? '—' : segment.summary.processedFps.toFixed(1),
      ageP95: segment.summary.processing ? `${segment.summary.processing.p95.toFixed(1)} ms` : '—',
      intervalP95: segment.summary.frameInterval ? `${segment.summary.frameInterval.p95.toFixed(1)} ms` : '—',
      maxGap: segment.summary.frameInterval ? `${segment.summary.frameInterval.max.toFixed(1)} ms` : '—',
      tracked: percent(segment.coverage.trackedFraction),
      maskedTracked: segment.variant === 'accepted' ? 'Hair off' : percent(segment.coverage.maskedTrackedFraction),
    };
    for (const [metric,value] of Object.entries(fields)) {
      const cell = document.createElement('td'); cell.dataset.metric = metric; cell.textContent = value; row.append(cell);
    }
    body.append(row);
  }
}

function selectPipeline(pipeline: Pipeline, manual = true): void {
  if (manual && continuousActive()) return;
  if (manual) profiler.cancel('Algorithm changed manually.');
  selectedPipeline=pipeline;pipelineSelect.value=pipeline;lastProfileUiAt=0;
  element('experiment-detail').textContent=PROFILES[pipeline].detail;
  if(current?.pump && current.phase==='live')void restartPump(current);
  try {current?.renderer?.selectPipeline(pipeline);updateControls();showHairStatus();updateProfileUi();}
  catch (error) {closeSession(friendlyError(error),true);}
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
  session.pump?.stop();session.pump=undefined;
  for (const cleanup of session.liveCleanups.splice(0)) cleanup();
  session.camera?.stop();
  session.detector.close();
  session.hair.close();
}
function closeSession(message = 'Camera closed. Ready whenever you are.', failed = false): void {
  cancelContinuous(failed ? `Camera session failed: ${message}` : 'Camera session closed before the comparison finished.');
  profiler.cancel(failed ? 'The camera session failed.' : 'The camera session closed.');
  const session = current;
  current = null;
  if (session) {
    settleStartup(session, failed, message);
    profiler.recordEvent(session.id,failed?'session-failed':'session-closed',performance.now());
    releaseLive(session);
    for (const cleanup of session.cleanups.splice(0)) cleanup();
    session.abort.abort();
    session.renderer?.dispose();
    session.canvas.hidden = true;
    session.canvas.width = session.canvas.height = 0;
  }
  start.disabled = false; start.textContent = failed ? 'Try again' : 'Open camera';
  stop.hidden = true; welcome.hidden = false;
  for (const id of ['fps', 'latency', 'resolution', 'hair-latency', 'hair-wait', 'render-latency', 'changed-pixels', 'hair-coverage', 'stall-latency']) element(id).textContent = '—';
  element('tracking').textContent = 'Off';
  element('download-status').textContent = '';
  download.textContent = 'Download held comparison';
  for (const key of ['frames', 'sessionId', 'frameSequence', 'sourceSha256', 'detectionSha256', 'hairStatus', 'changedPixels']) delete stage.dataset[key];
  setState(failed ? 'error' : 'idle', failed ? 'PREVIEW UNAVAILABLE' : 'CAMERA OFF', message);
  if(profiler.hasSamples) element('profile-count').textContent='Last completed session · camera closed';
  updateControls(); showHairStatus(); updateProfileUi();
}
function friendlyError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError') return 'Allow camera access in your browser, then try again.';
    if (error.name === 'NotFoundError') return 'Connect a camera, then try again.';
    if (error.name === 'NotReadableError') return 'The camera could not open. Close other apps using it, then try again.';
  }
  return messageFor(error);
}

async function restartPump(session: Session):Promise<void> {
  if(session.switching||!session.pump)return;session.switching=true;
  const pump=session.pump;
  try {await pump.finishCurrent();pump.stop();
    if(current!==session||session.phase!=='live'||session.pump!==pump)return;
    session.pump=undefined;session.processing=false;session.switching=false;
    if(session.holdRequested){holdSession(session);return;}
    session.renderer!.selectPipeline(selectedPipeline);runFrames(session);
  } catch(error){if(current===session)closeSession(friendlyError(error),true);}
  finally{session.switching=false;}
}
function runPumpedFrames(session:Session):void {
  const mode=PROFILES[selectedPipeline].mode;
  const generation=session.generation,pipeline=selectedPipeline;
  const hairTraceContext = continuousRun?.session === session && continuousRun.hairDelivery ? continuousRun : null;
  const owns=()=>current===session&&session.phase==='live'&&session.generation===generation;
  session.pump=runExperimentPump({id:session.id,video:session.camera!.video,renderer:session.renderer!,detector:session.detector,
    hair:()=>session.hair,hairId:session.hairId,eyewearId:session.eyewearId,hairReady:()=>session.hairReady&&!session.hairError,
    mode,pipeline,generation,variant:()=>selectedVariant,owns,nextSequence:()=>++session.nextSequence,
    onBusy:()=>{if(owns())session.processing=true;},
    onHairError:error=>{if(owns()){session.hairError=messageFor(error);session.hairReady=false;session.hair.close();}},
    onError:error=>{if(owns())closeSession(friendlyError(error),true);},backend:()=>session.backend,
    onHairTrace: hairTraceContext ? trace => {
      if (continuousRun === hairTraceContext && trace.capturedAtMs >= hairTraceContext.startedAtMs) hairTraceContext.hairDelivery!.record(trace);
    } : undefined,
    onPublished:(input,identity)=>{
      if(!owns())return;
      const throttleUi=PROFILES[input.pipeline].throttleUi;
      const ui=session.uiCadence.observe(performance.now(),JSON.stringify([
        session.id,input.pipeline,input.variant,input.hasFace,input.hasMask,input.fallback]),throttleUi);
      // Every completion reaches the profiler before any human-facing summary.
      // The counters describe summary admission, not dropped processing frames.
      session.performanceSample=profiler.add({...input,eyewearId:session.eyewearId,hairModelId:session.hairId,native:{...input.native,
        'ui.throttleSummariesRequested':ui.requested,'ui.summaryIntervalMs':ui.intervalMs,
        'ui.summaryRequests':ui.requests,'ui.summaryRefreshes':ui.refreshes,'ui.summarySkipped':ui.skipped,
        'ui.summaryRefreshThisFrame':ui.refresh}});
      if (continuousRun?.session === session && !continuousRun.finalizing) {
        continuousRun.controller.observe(session.performanceSample, input.publishedAtMs);
      }
      if(session.firstPublishedAtMs===null) {
        const startupReceipt = session.startup?.watchdog.complete();
        if (!owns()) return;
        if (startupReceipt?.state !== 'complete') {
          closeSession('The first AR image arrived after startup was cancelled. Open the camera again to retry.', true); return;
        }
        session.firstPublishedAtMs=input.publishedAtMs;
        profiler.recordEvent(session.id,'first-publication',input.publishedAtMs,input.publishedAtMs-session.openedAtMs);
        element('profile-startup').textContent=((input.publishedAtMs-session.openedAtMs)/1000).toFixed(2)+' s';
        session.startup?.milestones.push({name: 'first-publication', atMs: input.publishedAtMs});
        settleStartup(session, false, 'Mirror ready.');
      }
      if(input.hasMask&&session.firstMaskedAtMs===null) {
        session.firstMaskedAtMs=input.publishedAtMs;
        session.startup?.milestones.push({name: 'first-masked-publication', atMs: input.publishedAtMs});
        profiler.recordEvent(session.id,'first-masked-publication',input.publishedAtMs,input.publishedAtMs-session.openedAtMs);
        element('profile-masked-startup').textContent=((input.publishedAtMs-session.openedAtMs)/1000).toFixed(2)+' s';
      }
      session.presented={sequence:input.sequence,...identity,capturedAtMs:input.capturedAtMs,pipeline:input.pipeline};
      session.timing={faceMs:input.faceRequestWallMs,hairWaitMs:input.hairWaitMs,renderMs:input.renderMs,totalMs:input.totalMs};
      session.processing=false;writeValue(session.canvas,'hidden',false);writeValue(welcome,'hidden',true);
      stage.dataset.frameSequence=String(input.sequence);stage.dataset.sourceSha256=identity.sourceSHA256;stage.dataset.detectionSha256=identity.detectionSHA256;
      stage.dataset.hairStatus=input.variant==='accepted'?'off':!input.hasFace?'no-face':input.hasMask?'ready':'fallback';
      stage.dataset.changedPixels=String(input.changedPixels);stage.dataset.frames=String(input.sequence);
      writeText('tracking',input.hasFace?'Face visible':'Looking for face');
      let summary:ProfileSummary|undefined;
      if(ui.refresh) {
        summary=summarize(profiler.recent(session.id,input.pipeline,input.variant));
        writeText('fps',summary.processedFps===null?'Starting':summary.processedFps.toFixed(1)+' fps');
        writeText('latency',Math.round(input.totalMs)+' ms');writeText('resolution',input.sourceWidth+' × '+input.sourceHeight);
        writeText('hair-latency',input.hairInferenceMs===null?'—':Math.round(input.hairInferenceMs+(input.hairExtractionMs??0))+' ms');
        writeText('hair-wait',Math.round(input.hairWaitMs)+' ms');writeText('render-latency',Math.round(input.renderMs)+' ms');
        writeText('changed-pixels',String(input.changedPixels));writeText('hair-coverage',summary.hairCoverage===null?'—':Math.round(summary.hairCoverage*100)+'%');
        writeText('stall-latency',summary.processing?Math.round(summary.processing.p95)+' ms':'—');
      }
      setState(input.hasFace?'tracking':'searching',input.hasFace?'COMPARISON LIVE':'LOOKING FOR YOU',input.hasFace?'Switch experiments while moving. Hold to compare this exact image.':'Bring your face into view.');
      updateControls();showHairStatus();
      // Benchmark transitions still happen immediately even on a skipped UI
      // refresh. T shares its one summary between both human readout panels.
      if(!throttleUi||ui.refresh)updateProfileUi(session,throttleUi?summary:undefined,throttleUi);
      else {updateCompletedComparison();writeText('benchmark-status',profiler.progress(performance.now()));}
      if (continuousRun?.session === session) driveContinuous(continuousRun);
      if(session.holdRequested){holdSession(session);return;}
      const next=profiler.takeNextPipeline();if(next)selectPipeline(next,false);
    },
  });
}

function runFrames(session: Session): void {runPumpedFrames(session);}

async function openSession(): Promise<void> {
  if (current) return;
  const openedAtMs=performance.now();
  element('profile-startup').textContent=element('profile-masked-startup').textContent='—';
  for(const id of ['profile-fps','profile-video-fps','profile-p95','profile-interval-p95','profile-face','profile-face-roundtrip',
    'profile-scheduler','profile-copy','profile-prepare','profile-finish','profile-coverage']) element(id).textContent='—';
  element('profile-count').textContent='Waiting for completed frames from this session.';lastProfileUiAt=0;
  element('mask-cost-status').textContent='Waiting for hair-mask extraction from this session.';
  const previous = element<HTMLCanvasElement>('mirror'), canvas = previous.cloneNode(false) as HTMLCanvasElement;
  previous.replaceWith(canvas);
  const backend = detectHairBackend();
  const session: Session = {id: crypto.randomUUID(), phase: 'live', generation: 1, abort: new AbortController(),
    eyewearId: selectedEyewear, hairId: selectedHair, detector: new DetectorClient(),
    hair: new HairClient(selectedHair, {delegate: backend.requested, outputMode: 'category-only'}), backend,
    hairReady: false, hairError: null, canvas, liveCleanups: [], cleanups: [], nextSequence: 0, presented: null, heldAt: null,
    heldBusy: false, processing: false, holdRequested: false, budgetMisses: 0, timing: null, performanceSample:null,
    openedAtMs,firstPublishedAtMs:null,firstMaskedAtMs:null,uiCadence:new UiSummaryCadence(), startup: null};
  session.startup = {watchdog: new StartupWatchdog({sessionId: session.id, onTimeout: receipt => {
    if (current !== session) return;
    const label = receipt.stage ? STARTUP_LABELS[receipt.stage] : 'Mirror setup';
    closeSession(`${label} took too long. Save the startup report below, then try again.`, true);
  }}), progressTimer: null, error: null, milestones: [{name: 'camera-request', atMs: openedAtMs}], frameProgress: null,
    workload: {eyewearId: session.eyewearId, hairModelId: session.hairId, variant: selectedVariant, pipeline: selectedPipeline}};
  lastStartupReport = null;
  element<HTMLTextAreaElement>('startup-json').value = '';
  element<HTMLDetailsElement>('startup-report-copy').open = false;
  element<HTMLButtonElement>('select-startup-json').disabled = true;
  element('startup-export-status').textContent = 'Save this attempt’s startup report if setup stops. It contains device details and timings, with no images or face data.';
  profiler.recordEvent(session.id,'camera-request',openedAtMs);
  current = session; stage.dataset.sessionId = session.id;
  session.startup.progressTimer = window.setInterval(() => {if (current === session) showStartupProgress(session);}, 1000);
  showStartupProgress(session);
  start.disabled = true; start.textContent = 'Opening…'; stop.hidden = false;
  setState('starting', 'STARTING CAMERA', 'Allow camera access when your browser asks.');
  updateControls(); showHairStatus();
  try {
    session.camera = await openCamera(session.abort.signal);
    if (current !== session) { session.camera.stop(); return; }
    profiler.recordEvent(session.id,'camera-ready',performance.now());
    session.startup.milestones.push({name: 'camera-ready', atMs: performance.now()});
    session.startup.watchdog.start();
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
    enterStartupStage(session, 'module');
    const {ComparisonRenderer: LiveHairRenderer} = await import('./comparison-renderer.ts');
    if (current !== session) return;
    session.startup.milestones.push({name: 'module-ready', atMs: performance.now()});
    session.renderer = await LiveHairRenderer.create(canvas, session.abort.signal, session.eyewearId, phase => {
      if (current !== session) return;
      if (phase === 'candidate-renderer') session.startup!.milestones.push({name: 'g-renderer-ready', atMs: performance.now()});
      enterStartupStage(session, phase);
    });
    if (current !== session) { session.renderer.dispose(); return; }
    session.startup.milestones.push({name: 'candidate-renderer-ready', atMs: performance.now()});
    profiler.recordEvent(session.id,'renderers-ready',performance.now());
    session.renderer.selectVariant(selectedVariant);
    session.renderer.selectPipeline(selectedPipeline);
    // Optional hair startup never blocks a usable accepted mirror.
    void initializeHair(session, () => current === session && session.phase === 'live').then(() => {
      if (current === session && session.phase === 'live') { session.hairReady = true;
        session.startup?.milestones.push({name: 'hair-ready', atMs: performance.now()});
        profiler.recordEvent(session.id,'hair-ready',performance.now());showHairStatus(); }
    }).catch(error => {
      if (current === session && session.phase === 'live') {
        session.hairError = messageFor(error); session.hairReady = false; session.hair.close(); showHairStatus();
      }
    });
    enterStartupStage(session, 'face');
    if (current !== session) return;
    await session.detector.initialize(session.abort.signal);
    if (current !== session) return;
    profiler.recordEvent(session.id,'face-ready',performance.now());
    session.startup.milestones.push({name: 'face-ready', atMs: performance.now()});
    enterStartupStage(session, 'first-ar');
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
  if (continuousActive()) {variantSelect.value = selectedVariant; return;}
  profiler.cancel('Hair setting changed.');
  selectedVariant = variantSelect.value === 'accepted' ? 'accepted' : 'hair';
  current?.renderer?.selectVariant(selectedVariant); updateControls(); showHairStatus();
});
element('toggle-version').addEventListener('click', () => {
  variantSelect.value = selectedVariant === 'hair' ? 'accepted' : 'hair';
  variantSelect.dispatchEvent(new Event('change'));
});
pipelineSelect.addEventListener('change', () => {
  const value=pipelineSelect.value;
  selectPipeline(visiblePipelines.includes(value as Pipeline) ? value as Pipeline : 'g');
});
for (const id of ['toggle-pipeline', 'stage-toggle-pipeline']) element(id).addEventListener('click', () => {
  if (pipelineSelect.disabled) return;
  pipelineSelect.value = visiblePipelines[(visiblePipelines.indexOf(selectedPipeline)+1)%visiblePipelines.length]!;
  pipelineSelect.dispatchEvent(new Event('change'));
});
start.addEventListener('click', () => { void openSession(); });
stop.addEventListener('click', () => closeSession());
element('download-startup').addEventListener('click', saveStartupReport);
element('select-startup-json').addEventListener('click', () => {
  const field = element<HTMLTextAreaElement>('startup-json'); field.focus(); field.select();
});
continuousStart.addEventListener('click', beginContinuous);
continuousStop.addEventListener('click', () => cancelContinuous('Stopped by the user; partial measurements retained.'));
element('continuous-save').addEventListener('click', saveContinuousFile);
element('continuous-share').addEventListener('click', () => {
  if (!continuousFile || typeof navigator.share !== 'function') return;
  void navigator.share({files: [continuousFile], title: 'Lenses AR mobile comparison'}).catch(error => {
    if (!(error instanceof DOMException && error.name === 'AbortError')) {
      setContinuousText('continuous-recording-status', 'Sharing was unavailable. Tap Save comparison file to keep it locally.');
    }
  });
});
async function completeHeldHair(session: Session): Promise<void> {
  if (current !== session || session.phase !== 'held' || session.renderer?.stats?.maskOutputMode === 'full' || session.heldBusy) return;
  const input = session.renderer?.copyHeldInput();
  if (!input) return;
  const generation = session.generation;
  const owns = () => current === session && session.phase === 'held' && session.generation === generation;
  let client = new HairClient(session.hairId, {delegate: session.backend.active ?? session.backend.requested});
  session.hair = client; session.heldBusy = true; session.hairError = null; download.disabled = true; updateControls(); showHairStatus();
  try {
    client = await initializeHair(session, owns);
    if (!owns()) return;
    const bitmap = await createImageBitmap(input.source);
    if (!owns()) { bitmap.close(); return; }
    const result = await client.segment(bitmap, input.pair.sourceSHA256, session.presented!.sequence);
    if (!owns()) return;
    try {
      await session.renderer!.present(input.source, input.detection, {...result, detectionSHA256: input.pair.detectionSHA256}, input.pair, input.expectedModel);
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
    if (owns()) {
      try {await session.renderer!.setHeld();}catch(error){if(owns())closeSession(friendlyError(error),true);}
      if(owns()){
        const result=session.renderer!.exportDiagnostic();
        const base=result?.g as {acceptedPngDataUrl:string;hairPngDataUrl:string}|undefined;
        const different=base?PIPELINES.filter(id=>{const item=result![id] as typeof base;return item?.acceptedPngDataUrl!==base.acceptedPngDataUrl||item?.hairPngDataUrl!==base.hairPngDataUrl;}):[];
        element('held-result').textContent=base?(different.length?'Held differences found: '+different.map(id=>PIPELINE_LABELS[id]).join(', '):'All held experiment images match G pixel-for-pixel on this image.'):'Held comparison unavailable.';
        session.heldBusy = false; download.disabled = false; updateControls(); showHairStatus();
      }
    }
  }
}
function holdSession(session: Session): void {
  if (current !== session || session.phase !== 'live' || !session.presented || !session.renderer) return;
  profiler.cancel('Frame held.'); updateProfileUi();
  if(session.pump){
    session.holdRequested=true;hold.disabled=true;const pump=session.pump;
    void pump.finishCurrent().then(()=>{if(current!==session||session.phase!=='live'||session.pump!==pump)return;pump.stop();session.pump=undefined;session.processing=false;holdSession(session);});return;
  }
  if (session.processing) {
    session.holdRequested = true; hold.disabled = true;
    element('hold-hint').textContent = 'Finishing the current paired frame, then stopping the camera…';
    return;
  }
  if (!session.renderer.stats?.hasFace) {
    // Hold can be requested from a tracked image while the last owned image
    // loses the face. Draining must not strand a live session without a pump.
    session.holdRequested=false;
    updateControls();
    setState('searching','LOOKING FOR YOU','Your face moved out of view before Hold finished. Bring it back into view and try Hold again.');
    runFrames(session);return;
  }
  selectedPipeline = session.renderer.pipeline; pipelineSelect.value = selectedPipeline;
  // Hold owns the completed pair; discard any queued live-only switch before
  // preparing that same image for its full-mask comparison.
  session.renderer.selectPipeline(selectedPipeline);
  element('experiment-detail').textContent = PROFILES[selectedPipeline].detail;
  session.phase = 'held'; session.heldAt = new Date().toISOString();
  profiler.recordEvent(session.id,'frame-held',performance.now());
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
  return {...output, ...CURRENT_BASE_METADATA, liveSession: {id: session.id, phase: session.phase, heldAt: session.heldAt,
    presented: {...session.presented}, sourceHashKind: 'SHA-256 of full captured RGBA bytes, unmirrored',
    detectionHashKind: 'SHA-256 of JSON.stringify of the exact validated Detection',
    performanceTimeOriginMs: performance.timeOrigin, hairError: session.hairError, pipeline: session.renderer!.pipeline,
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
    link.download = `efficiency-comparison-${new Date().toISOString().replaceAll(':', '-')}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 10_000);
    element('download-status').textContent = 'Comparison downloaded locally. It includes the original image and every held experiment output.';
  } catch (error) { element('download-status').textContent = messageFor(error); }
  finally { if (current === session) download.disabled = false; }
});
benchmark.addEventListener('click', () => {
  if (!current || current.phase!=='live' || !current.presented || benchmark.disabled) return;
  if(selectedPipeline==='g')return;
  const choice=selectedPipeline;
  const reverse=element<HTMLSelectElement>('benchmark-order').value==='experiment-first';
  selectPipeline(profiler.begin(current.id,selectedVariant,performance.now(),reverse?[choice,'g']:['g',choice]),false);
  updateControls();updateProfileUi();
});
downloadMetrics.addEventListener('click', () => {
  if (!profiler.hasSamples) return;
  const json=profiler.exportJSON();
  // Populate the copyable fallback before attempting browser-dependent file download.
  element<HTMLTextAreaElement>('metrics-json').value=json;
  element<HTMLDetailsElement>('metrics-export').open=true;
  element<HTMLButtonElement>('select-metrics-json').disabled=false;
  const status=element('metrics-export-status');
  status.textContent='Timing JSON is ready below. If no file arrives, select and copy it. No images, detections, or masks are included.';
  let url: string | null=null;
  try {
    url=URL.createObjectURL(new Blob([json],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;
    link.download=`ar-efficiency-comparison-${new Date().toISOString().replaceAll(':','-')}.json`;
    link.click();
  } catch {
    status.textContent='The file download could not start. Select and copy the timing JSON below; it contains no images, detections, or masks.';
  } finally {
    if (url) {const ownedUrl=url;setTimeout(()=>URL.revokeObjectURL(ownedUrl),10000);}
  }
});
element('select-metrics-json').addEventListener('click', () => {
  const field=element<HTMLTextAreaElement>('metrics-json');field.focus();field.select();
});
let freshnessTimer:number|undefined;
window.addEventListener('pageshow',()=>{
  window.clearInterval(freshnessTimer);freshnessTimer=window.setInterval(updatePublicationStatus,1000);updatePublicationStatus();
});
window.addEventListener('pagehide', () => {window.clearInterval(freshnessTimer);closeSession();});
if(typeof PerformanceObserver!=='undefined' && PerformanceObserver.supportedEntryTypes.includes('longtask')) {
  const observer=new PerformanceObserver(list=>{
    if(!current || current.phase!=='live')return;
    for(const entry of list.getEntries()) if(entry.startTime>=current.openedAtMs) {
      profiler.recordEvent(current.id,'long-task',entry.startTime,entry.duration);
      if (continuousRun && entry.startTime >= continuousRun.startedAtMs) {
        continuousRun.controller.recordEvent('long-task',entry.startTime,entry.duration);
      }
    }
  });
  observer.observe({type:'longtask',buffered:false});
  window.addEventListener('pagehide',()=>observer.disconnect(),{once:true});
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) cancelContinuous('The page became hidden; background time is excluded and the partial recording was retained.');
  if (document.hidden && current?.phase === 'live') closeSession('Camera closed while the page was hidden. Open it again when ready.');
});
declare global {
  interface Window { hairLivePreview: {diagnostics(): Record<string, unknown>; exportDiagnostic(): Record<string, unknown> | null}; }
  interface Window { arPerformanceProfiler: {snapshot(): Record<string, unknown>; samplesAfter(serial: number): FrameSample[]}; }
  interface Window { arContinuousComparison: {status(): ContinuousRunStatus | null; report(): Record<string, unknown> | null}; }
  interface Window { arStartupDiagnostics: {status(): StartupReceipt | null; report(): Record<string, unknown> | null}; }
}
// Read-only local inspection for exact-pair QA. It cannot start a camera or modify session state.
window.hairLivePreview = Object.freeze({
  diagnostics: () => ({...CURRENT_BASE_METADATA, state: stage.dataset.state, sessionId: current?.id ?? null, phase: current?.phase ?? null,
    presented: current?.presented ? {...current.presented} : null, hairReady: current?.hairReady ?? false,
    hairError: current?.hairError ?? null, stats: current?.renderer?.stats ?? null, variant: selectedVariant,
    pipeline: current?.renderer?.pipeline ?? selectedPipeline, requestedPipeline: selectedPipeline,
    timing: current?.timing ?? null, performanceSample:current?.performanceSample ? {...current.performanceSample,native:current.performanceSample.native?{...current.performanceSample.native}:null}:null,
    faceTiming:current?.detector.lastTiming ?? null, budgetMisses: current?.budgetMisses ?? 0, heldBusy: current?.heldBusy ?? false,
    processing: current?.processing ?? false, holdRequested: current?.holdRequested ?? false, backend: current ? {...current.backend} : null}),
  exportDiagnostic: diagnostic,
});
window.arPerformanceProfiler=Object.freeze({snapshot:()=>profiler.snapshot(),samplesAfter:(serial:number)=>profiler.samplesAfter(serial)});
window.arContinuousComparison = Object.freeze({
  status: () => continuousRun?.controller.status ?? null,
  report: () => continuousRun ? {...continuousRun.controller.export(), recording: continuousRun.recorder.snapshot()} : lastContinuousReport,
});
window.arStartupDiagnostics = Object.freeze({
  status: () => current?.startup?.watchdog.snapshot()
    ?? (lastStartupReport?.startup ? structuredClone(lastStartupReport.startup) as StartupReceipt : null),
  report: () => latestStartupReport(),
});
showEyewear(); updateControls(); showHairStatus(); updateProfileUi();
