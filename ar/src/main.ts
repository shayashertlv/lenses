/** The try-on page. Nothing starts until Open camera. One session = one camera stream, one renderer, one face
 *  landmarker worker and one hair segmenter worker, driven by the frame pipeline; closing the camera disposes all
 *  of them. The live panel shows the pipeline's own rate, the camera's delivered rate (the ceiling), and the stage
 *  medians; Hold & audit checks one frame against the CPU reference; Measure runs fresh sessions for the fps report. */
import './style.css';
import {describeConfig, parseConfig} from './config.ts';
import {openCamera} from './camera/camera.ts';
import type {CameraSession} from './camera/camera.ts';
import {lockCameraExposure} from './camera/exposure.ts';
import {DEFAULT_EYEWEAR_ID, EYEWEAR, eyewearById, MODELING_AUTO_EYEWEAR_ID} from './eyewear/catalog.ts';
import {describeExternalModel, installExternalModel, parseExternalModel} from './eyewear/external.ts';
import {DEFAULT_HAIR_MODEL_ID, getHairModel, HAIR_MODEL_LIST, isHairModelId} from './hair/models.ts';
import {HairClient} from './hair/client.ts';
import {detectHairBackend} from './hair/backend.ts';
import type {HairBackend} from './hair/backend.ts';
import {DetectorClient} from './face/detector.ts';
import {LiveRenderer} from './render/live-renderer.ts';
import {runPipeline} from './pipeline/pipeline.ts';
import type {Pipeline} from './pipeline/pipeline.ts';
import {cameraDeliveryFps, FrameProfiler, summarize} from './pipeline/profiler.ts';
import type {FrameSample, ProfileSummary} from './pipeline/profiler.ts';
import type {Audit} from './audit/audit.ts';

export const config = parseConfig(location.search);
const element = <T extends HTMLElement = HTMLElement>(id: string): T => {const value = document.getElementById(id); if (!value) throw new Error(`Missing control: ${id}`); return value as T;};
const stage = document.querySelector<HTMLElement>('.stage') as HTMLElement;
const eyewearSelect = element<HTMLSelectElement>('eyewear-select'), hairSelect = element<HTMLSelectElement>('hair-model-select');
const hairToggle = element<HTMLSelectElement>('hair-toggle');
const start = element<HTMLButtonElement>('start'), stop = element<HTMLButtonElement>('stop');
const profiler = new FrameProfiler(8192);
const messageFor = (error: unknown): string => error instanceof Error ? error.message : String(error);

interface Session {
  id: string; abort: AbortController; camera: CameraSession | null; canvas: HTMLCanvasElement;
  renderer: LiveRenderer | null; detector: DetectorClient | null; hair: HairClient | null; hairBackend: HairBackend;
  hairReady: boolean; hairError: string | null; pipeline: Pipeline | null; sequence: number;
  startedAtMs: number; firstAtMs: number | null; rows: number;
  exposure: {applied: number | null; mode: string | null; error: string | null} | null;
}
let current: Session | null = null;
let hairEnabled = config.hair ?? true;

// Opt-in handover from Modeling Auto (?model=&name=&clip=&width=&sha256=): the prepared asset becomes this page's
// Modeling Auto frame and is selected. Absent, nothing changes; an unusable address leaves the shipped frames in place.
let selectedEyewear: string = DEFAULT_EYEWEAR_ID;
{
  const note = document.createElement('p'); note.className = 'control-hint'; note.id = 'external-model';
  try {
    const externalModel = parseExternalModel(location.search, location.origin);
    if (externalModel) {installExternalModel(externalModel); selectedEyewear = MODELING_AUTO_EYEWEAR_ID; note.textContent = describeExternalModel(externalModel); stage.dataset.externalModel = 'ready';}
  } catch (error) {note.textContent = `The model in this address was not loaded: ${messageFor(error)} The shipped frames remain available.`; stage.dataset.externalModel = 'invalid';}
  if (note.textContent) element('frame-description').after(note);
}
for (const eyewear of Object.values(EYEWEAR)) eyewearSelect.add(new Option(eyewear.optionLabel, eyewear.id));
for (const model of HAIR_MODEL_LIST) hairSelect.add(new Option(model.title, model.id));
eyewearSelect.value = config.eyewear && Object.hasOwn(EYEWEAR, config.eyewear) && [...eyewearSelect.options].some(option => option.value === config.eyewear) ? config.eyewear : selectedEyewear;
hairSelect.value = isHairModelId(config.hairModel) ? config.hairModel : DEFAULT_HAIR_MODEL_ID;
hairToggle.value = hairEnabled ? 'on' : 'off';
element('config-note').textContent = describeConfig(config);

function setState(state: string, label: string, message: string): void {stage.dataset.state = state; element('stage-status').textContent = label; element('guidance').textContent = message;}
function showEyewear(): void {const model = eyewearById(eyewearSelect.value); element('frame-name').textContent = model.name; element('frame-description').textContent = model.description;}
eyewearSelect.addEventListener('change', showEyewear); showEyewear();
hairToggle.addEventListener('change', () => {hairEnabled = hairToggle.value !== 'off'; current?.renderer?.setHairEnabled(hairEnabled);});
function updateControls(): void {
  const live = !!current;
  eyewearSelect.disabled = hairSelect.disabled = live;
  start.hidden = live; stop.hidden = !live; element<HTMLButtonElement>('download-metrics').disabled = !profiler.hasSamples;
  element<HTMLButtonElement>('audit').disabled = !current?.renderer || auditPending;
}
let uiTimer: ReturnType<typeof setInterval> | null = null;
function updateUi(): void {
  const session = current; if (!session) return;
  const recent = profiler.recent(session.id).filter(row => performance.now() - row.publishedAtMs <= 10_000);
  const summary = summarize(recent);
  element('fps').textContent = summary.processedFps === null ? 'Starting' : `${summary.processedFps.toFixed(1)} fps`;
  element('age').textContent = summary.processing ? `${Math.round(summary.processing.median)} / ${Math.round(summary.processing.p95)} ms` : '—';
  const cameraFps = cameraDeliveryFps(recent);
  element('coverage').textContent = recent.length ? `${summary.trackedFrames}/${recent.length} tracked · ${summary.maskedFrames} masked · camera ${cameraFps === null ? '—' : cameraFps.toFixed(1) + ' fps'}` : '—';
  const ms = (value: number | undefined): string => value === undefined ? '—' : `${value.toFixed(1)} ms`;
  element('stages').textContent = `face ${ms(summary.stages.faceRequestWallMs?.median)} · prepare ${ms(summary.stages.prepareMs?.median)} (gpu wait ${ms(summary.stages.gpuWaitMs?.median)}, pose ${ms(summary.stages.poseMs?.median)}) · finish ${ms(summary.stages.finishMs?.median)} (submit ${ms(summary.stages.submitMs?.median)}) · hair wait ${ms(summary.stages.hairWaitMs?.median)}`;
  const exposure = session.exposure ? session.exposure.error ? ` · exposure lock failed: ${session.exposure.error}` : ` · exposure ${session.exposure.applied} × 100 µs (${session.exposure.mode})` : '';
  const continuity = session.renderer?.continuityUnavailable ? ` · continuity cut unavailable: ${session.renderer.continuityUnavailable}` : '';
  element('frames').textContent = `${session.rows} frames this session · ${session.canvas.width}×${session.canvas.height} · startup ${session.firstAtMs === null ? '…' : Math.round(session.firstAtMs - session.startedAtMs) + ' ms'}${exposure}${continuity}`;
}

async function openSession(): Promise<void> {
  if (current) return;
  const previous = element<HTMLCanvasElement>('mirror'), canvas = previous.cloneNode(false) as HTMLCanvasElement; previous.replaceWith(canvas);
  const session: Session = {id: crypto.randomUUID(), abort: new AbortController(), camera: null, canvas, renderer: null, detector: null, hair: null,
    hairBackend: detectHairBackend(), hairReady: false, hairError: null, pipeline: null, sequence: 0, startedAtMs: performance.now(), firstAtMs: null, rows: 0, exposure: null};
  current = session; stage.dataset.sessionId = session.id; updateControls();
  const signal = session.abort.signal, owns = (): boolean => current === session;
  setState('starting', 'STARTING CAMERA', 'Allow camera access when your browser asks.');
  try {
    session.camera = await openCamera(signal);
    if (!owns()) {session.camera.stop(); return;}
    if (config.exposure !== null) {
      const lock = await lockCameraExposure(session.camera.video, config.exposure);
      if (!owns()) {session.camera.stop(); return;}
      session.exposure = {applied: lock.applied, mode: lock.mode, error: lock.error};
    }
    const ended = () => {if (owns()) closeSession('Your camera disconnected. Reconnect it and try again.', true);};
    for (const track of (session.camera.video.srcObject as MediaStream).getTracks()) track.addEventListener('ended', ended);
    const lost = (event: Event) => {event.preventDefault(); if (owns()) closeSession('The graphics connection was interrupted. Open the camera again to restart.', true);};
    canvas.addEventListener('webglcontextlost', lost);
    setState('starting', 'PREPARING MIRROR', 'Loading the glasses and the local face tracker…');
    const eyewearId = eyewearSelect.value, hairModel = getHairModel(hairSelect.value);
    const renderer = await LiveRenderer.create(canvas, signal, eyewearId, {hairStartZ: config.hairStartZ, sync: config.sync, guard: config.guard, continuity: config.continuity, continuityRunPx: config.continuityRunPx});
    if (!owns()) {renderer.dispose(); return;}
    renderer.setHairEnabled(hairEnabled); session.renderer = renderer;
    const detector = new DetectorClient(config.faceDelegate ? {delegate: config.faceDelegate} : {}); session.detector = detector;
    session.hair = new HairClient(hairModel.id, {delegate: session.hairBackend.requested});
    // Hair startup never blocks a usable mirror; a GPU failure is retried once on the CPU.
    void (async () => {
      let hair = session.hair!;
      try {await hair.initialize(signal);}
      catch (error) {
        if (!owns() || hair.delegate !== 'GPU') {session.hairError = messageFor(error); return;}
        hair.close(); session.hairBackend.fallbackReason = messageFor(error);
        hair = new HairClient(hairModel.id, {delegate: 'CPU'}); session.hair = hair;
        try {await hair.initialize(signal);} catch (retry) {session.hairError = messageFor(retry); return;}
      }
      if (owns()) {session.hairReady = true; session.hairBackend.active = hair.delegate; element('hair-engine').textContent = `hair ${hair.delegate}`;}
    })();
    await detector.initialize(signal);
    if (!owns()) return;
    session.pipeline = runPipeline({
      id: session.id, video: session.camera.video, renderer, detector,
      hair: () => session.hair!, hairModel, eyewearId, hairReady: () => session.hairReady && !session.hairError,
      hairEnabled: () => hairEnabled, captureMaxEdge: config.captureMaxEdge, owns, nextSequence: () => ++session.sequence,
      onHairError: error => {if (owns()) {session.hairError = messageFor(error); session.hairReady = false; session.hair?.close(); element('hair-engine').textContent = `hair error: ${session.hairError}`;}},
      onError: error => {if (owns()) closeSession(messageFor(error), true);},
      backend: () => ({active: session.hairBackend.active, renderer: session.hairBackend.renderer}),
      onPublished: row => {
        if (!owns()) return;
        profiler.add(row); session.rows++; session.firstAtMs ??= row.publishedAtMs; canvas.hidden = false; element('welcome').hidden = true;
        stage.dataset.frames = String(row.sequence);
        setState(row.hasFace ? 'tracking' : 'searching', row.hasFace ? 'LIVE' : 'LOOKING FOR YOU', row.hasFace ? 'Turn slowly and compare the feel.' : 'Bring your face into view.');
      },
    });
    element('gpu').textContent = `${renderer.gpuRenderer ?? '—'} · face ${detector.delegate ?? '—'}`;
    updateControls(); if (!uiTimer) uiTimer = setInterval(updateUi, 500);
  } catch (error) {if (owns()) closeSession(messageFor(error), true);}
}
function closeSession(message = 'Camera closed.', failed = false): void {
  const session = current; current = null;
  if (session) {
    session.abort.abort();
    session.pipeline?.stop(); session.detector?.close(); session.hair?.close(); session.renderer?.dispose(); session.camera?.stop();
    session.canvas.hidden = true; session.canvas.width = session.canvas.height = 0;
  }
  if (uiTimer) {clearInterval(uiTimer); uiTimer = null;}
  element('welcome').hidden = false; setState(failed ? 'error' : 'idle', failed ? 'PREVIEW UNAVAILABLE' : 'CAMERA OFF', message); updateControls();
}
start.addEventListener('click', () => {void openSession();});
stop.addEventListener('click', () => closeSession());
function download(name: string, json: unknown): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(json, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = `${name}-${new Date().toISOString().replaceAll(':', '-')}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
element('download-metrics').addEventListener('click', () => download('ar-timings', {...JSON.parse(profiler.exportJSON()) as Record<string, unknown>, config, page: location.href}));

/** Hold & audit: the next finished frame is drawn with and without hair and without eyewear, read back once, and
 *  checked against the CPU reference compose and the four protection checks. Explicit action; the images stay in this
 *  page until downloaded. */
let auditPending = false;
let lastAudit: Audit | null = null;
async function runAudit(): Promise<Audit | null> {
  const session = current, renderer = session?.renderer;
  if (!session || !renderer || auditPending) return null;
  auditPending = true; updateControls(); element('audit-result').textContent = 'Auditing the next frame…';
  try {
    renderer.requestAudit();
    const started = performance.now();
    let audit: Audit | null = null;
    while (!audit && performance.now() - started < 15_000 && current === session) {await new Promise(resolve => setTimeout(resolve, 100)); audit = renderer.takeAudit();}
    if (!audit) {element('audit-result').textContent = 'No tracked frame was audited within 15 s.'; return null;}
    lastAudit = audit; element('audit-download').hidden = false;
    const c = audit.checks, r = audit.reference;
    const check = (name: string, value: {changedPixels: number} | undefined): string => `${name} ${value ? value.changedPixels === 0 ? 'pass' : `${value.changedPixels} px changed` : '—'}`;
    const mm = (value: number | null): string => value === null ? 'none' : `${(value * 1000).toFixed(0)} mm`;
    element('audit-result').textContent = `Frame ${audit.sequence} ${audit.width}×${audit.height} · guard ${audit.guard.guarded ? `on (${audit.guard.protectedRects.length} protected, ${audit.guard.editableRects.length} editable rects)` : audit.guard.safeFallback ? 'safe fallback' : 'off'} · hair ${audit.hairApplied ? 'applied' : 'not applied'} · `
      + (c ? `${check('protected', c.protectedCheck)} · ${check('nose', c.noseCheck)} · ${check('outside editable', c.outsideEditableCheck)} · ${check('background', c.backgroundPreservationCheck)}` : `checks unavailable: ${audit.checkError}`)
      + ` · GPU edit vs no-hair render ${audit.afterVsBefore.differentPixels} px (max Δ ${audit.afterVsBefore.maxDelta})`
      + ` · drop inside protected ${audit.dropInsideProtected ? `${audit.dropInsideProtected.differentPixels} px (${audit.dropInsideProtected.differentPixelsOver8} over 8)` : '—'}`
      + ` · continuity cut ${audit.cut.continuity ? `left arm ${mm(audit.cut.negative)}, right arm ${mm(audit.cut.positive)}` : 'off'}${r.continuityRemovedPixels !== null ? `; the reference continuity would remove ${r.continuityRemovedPixels} px` : ''}`
      + (audit.afterVsReference ? ` · vs the CPU reference of the same inputs ${audit.afterVsReference.differentPixels} px differ (max Δ ${audit.afterVsReference.maxDelta}); the reference changes ${r.changedPixels} px${r.fallbackReason ? `, reference fallback: ${r.fallbackReason}` : ''}` : r.error ? ` · reference error: ${r.error}` : '')
      + ` · ${Math.round(audit.timings.totalMs)} ms (renders ${Math.round(audit.timings.rendersMs)}, readback ${Math.round(audit.timings.readbackMs)}, compose ${Math.round(audit.timings.composeMs)}, checks ${Math.round(audit.timings.checksMs)}).`;
    return audit;
  } finally {auditPending = false; updateControls();}
}
element('audit').addEventListener('click', () => {void runAudit();});
element('audit-download').addEventListener('click', () => {if (lastAudit) download('ar-audit', lastAudit);});

/** Measure: fresh sessions in sequence, each warmed up then measured; sessions are the unit of comparison. */
export interface MeasuredSession {index: number; status: 'complete' | 'failed' | 'partial'; error: string | null; sessionId: string; startupMs: number | null; rows: FrameSample[]; summary: ProfileSummary | null; measuredFromMs: number | null; measuredToMs: number | null;}
let cancelMeasure: (() => void) | null = null;
let lastReport: Record<string, unknown> | null = null;
async function measure(sessions: number, warmupMs: number, measurementMs: number): Promise<{sessions: MeasuredSession[]; status: string}> {
  if (cancelMeasure) throw new Error('A measurement is already running.');
  let cancelled = false; cancelMeasure = () => {cancelled = true;};
  const measured: MeasuredSession[] = [];
  element('measure-cancel').hidden = false; element<HTMLButtonElement>('measure-start').disabled = true;
  try {
    for (let index = 0; index < sessions; index++) {
      if (cancelled) break;
      const entry: MeasuredSession = {index, status: 'failed', error: null, sessionId: '', startupMs: null, rows: [], summary: null, measuredFromMs: null, measuredToMs: null};
      const serialBefore = profiler.lastSerial, openedAt = performance.now();
      try {
        await openSession();
        if (!current) throw new Error(element('guidance').textContent ?? 'Session failed to open.');
        entry.sessionId = current.id;
        let serial = serialBefore, firstAt: number | null = null, measureAt: number | null = null, valid = 0;
        for (;;) {
          await new Promise(resolve => setTimeout(resolve, 100));
          if (cancelled) throw new Error('Cancelled.');
          if (!current || current.id !== entry.sessionId) throw new Error(element('guidance').textContent ?? 'Session closed.');
          for (const sample of profiler.samplesAfter(serial)) {
            serial = sample.serial; firstAt ??= sample.publishedAtMs;
            if (measureAt === null) {
              if (sample.hasFace && (!hairEnabled || sample.hasMask)) valid++;
              if (sample.publishedAtMs - firstAt >= warmupMs && valid >= 3) {measureAt = sample.publishedAtMs; entry.measuredFromMs = measureAt;}
              else if (sample.publishedAtMs - firstAt > warmupMs + 30_000) throw new Error('Tracking and hair coverage were unavailable during warmup.');
            }
            if (measureAt !== null) {entry.rows.push(sample); if (sample.publishedAtMs - measureAt >= measurementMs) {entry.measuredToMs = sample.publishedAtMs; entry.status = 'complete'; break;}}
          }
          if (entry.status === 'complete') break;
          if (firstAt === null && performance.now() - openedAt > 90_000) throw new Error('No first frame within 90 s.');
          element('measure-status').textContent = `${index + 1} / ${sessions} · ${firstAt === null ? 'starting' : measureAt === null ? 'warming up' : `${Math.min(measurementMs / 1000, Math.floor((performance.now() - measureAt) / 1000))} / ${measurementMs / 1000} s`}`;
        }
        entry.startupMs = firstAt === null ? null : firstAt - openedAt;
      } catch (error) {entry.status = entry.rows.length ? 'partial' : 'failed'; entry.error = messageFor(error);}
      finally {closeSession('Session finished.'); entry.summary = entry.rows.length ? summarize(entry.rows) : null;}
      measured.push(entry); renderMeasured(measured);
      if (index + 1 < sessions) await new Promise(resolve => setTimeout(resolve, 1500));
    }
  } finally {cancelMeasure = null; element('measure-cancel').hidden = true; element<HTMLButtonElement>('measure-start').disabled = false;}
  const status = cancelled ? 'stopped' : 'complete';
  element('measure-status').textContent = status === 'complete' ? 'Measurement complete. Download the report; sessions are the unit of comparison.' : 'Measurement stopped.';
  lastReport = {schema: 'ar-sessions-v1', createdAt: new Date().toISOString(), status, sessions: sessions, warmupMs, measurementMs, config,
    eyewear: eyewearSelect.value, hairModel: hairSelect.value, hair: hairEnabled,
    page: {userAgent: navigator.userAgent, url: location.href, hardwareConcurrency: navigator.hardwareConcurrency},
    measured};
  element('report-download').hidden = false;
  return {sessions: measured, status};
}
const medianOf = (values: number[]): number | null => {const sorted = [...values].sort((a, b) => a - b); if (!sorted.length) return null; return sorted.length % 2 ? sorted[(sorted.length - 1) / 2]! : ((sorted[sorted.length / 2 - 1] ?? 0) + (sorted[sorted.length / 2] ?? 0)) / 2;};
function renderMeasured(measured: MeasuredSession[]): void {
  const body = element<HTMLTableSectionElement>('measure-rows'); body.replaceChildren(); element('measure-results').hidden = false;
  for (const entry of measured) {
    const row = document.createElement('tr'); const s = entry.summary;
    for (const text of [String(entry.index + 1), entry.status + (entry.error ? ` · ${entry.error.slice(0, 50)}` : ''), entry.startupMs === null ? '—' : `${Math.round(entry.startupMs)} ms`,
      s?.processedFps === null || s?.processedFps === undefined ? '—' : s.processedFps.toFixed(2), s ? `${cameraDeliveryFps(entry.rows)?.toFixed(1) ?? '—'}` : '—',
      s?.processing ? `${Math.round(s.processing.median)} / ${Math.round(s.processing.p95)} ms` : '—',
      s ? `${s.trackedFrames}/${s.frames}` : '—', s ? String(s.maskedFrames) : '—', s?.frameInterval ? `${Math.round(s.frameInterval.p95)} ms` : '—']) {
      const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
    }
    body.append(row);
  }
  const done = measured.filter(entry => entry.summary?.processedFps);
  element('measure-pooled').textContent = done.length ? `${done.length} sessions · median fps ${medianOf(done.map(entry => entry.summary?.processedFps ?? 0))?.toFixed(2) ?? '—'} · median age p95 ${medianOf(done.map(entry => entry.summary?.processing?.p95 ?? 0))?.toFixed(0) ?? '—'} ms` : '';
}
element('measure-start').addEventListener('click', () => {
  void measure(Math.max(1, Math.min(20, Number(element<HTMLInputElement>('measure-sessions').value) || 1)),
    Math.max(1, Number(element<HTMLInputElement>('measure-warmup').value) || 15) * 1000, Math.max(5, Number(element<HTMLInputElement>('measure-seconds').value) || 60) * 1000);
});
element('measure-cancel').addEventListener('click', () => cancelMeasure?.());
element('report-download').addEventListener('click', () => {if (lastReport) download('ar-sessions', lastReport);});
window.addEventListener('pagehide', () => closeSession('Page closed.'));

declare global {interface Window {__ar: {
  open(): Promise<void>; close(): void; isOpen(): boolean;
  measure(sessions: number, warmupMs: number, measurementMs: number): Promise<{sessions: MeasuredSession[]; status: string}>;
  samplesAfter(serial: number): FrameSample[]; lastReport(): Record<string, unknown> | null; audit(): Promise<Audit | null>;
  config(): typeof config;
};}}
window.__ar = {open: openSession, close: () => closeSession(), isOpen: () => current !== null, measure, samplesAfter: serial => profiler.samplesAfter(serial),
  lastReport: () => lastReport, audit: runAudit, config: () => config};
updateControls();
