import '../../src/style.css';
import './live.css';
import {PROFILES, DEFAULT_PIPELINE, CURRENT_BASE_METADATA} from './profiles.ts';
import {createOwnedSourceFrame} from './speed-options.ts';
import {runExperimentPump} from './live-pump.ts';
import {openCamera} from '../../references/perfect-temples/src/runtime/camera.ts';
import type {CameraSession} from '../../references/perfect-temples/src/runtime/camera.ts';
import {DetectorClient} from '../performance-stage2/face-detector.ts';
import {FrameProfiler, PIPELINES, PIPELINE_LABELS, summarize} from './frame-profiler.ts';
import type {FrameInput, FrameSample} from './frame-profiler.ts';
import {DEFAULT_EYEWEAR_ID, EYEWEAR, eyewearById} from '../../references/perfect-temples/src/render/eyewear.ts';
import type {EyewearId} from '../../references/perfect-temples/src/render/eyewear.ts';
import {HairClient} from '../hair-live-preview/hair-client.ts';
import type {HairSegmentationResult} from '../hair-live-preview/hair-client.ts';
import {FrameBudget} from '../hair-live-preview/frame-budget.ts';
import {detectHairBackend} from '../hair-live-preview/hair-backend.ts';
import type {HairBackend} from '../hair-live-preview/hair-backend.ts';
import {hairModelById} from '../hair-live-preview/models.ts';
import type {HairModelId} from '../hair-live-preview/models.ts';
import type {HairMask, ComparisonRenderer as LiveHairRenderer, Pipeline} from './comparison-renderer.ts';

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
let selectedPipeline: Pipeline = DEFAULT_PIPELINE;
const pipelineSelect = element<HTMLSelectElement>('pipeline-select');
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
function nativeMetrics(value: Record<string, unknown> | undefined): FrameInput['native'] {
  if (!value) return null;
  const result: NonNullable<FrameInput['native']>={};
  const walk=(object:Record<string,unknown>,prefix:string,depth:number):void=>{
    for(const [key,item] of Object.entries(object)) {
      const name=prefix ? `${prefix}.${key}`:key;
      if(typeof item==='number' || typeof item==='boolean' || typeof item==='string' || item===null) result[name]=item;
      else if(item && typeof item==='object' && !Array.isArray(item) && !ArrayBuffer.isView(item) && depth<5)
        walk(item as Record<string,unknown>,name,depth+1);
    }
  };
  walk(value,'',0);return result;
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
    ? 'Camera stopped. All eight versions show this exact held image. Resume starts a fresh live session.'
    : 'Switch algorithms while moving. Hold a frame to compare their appearance on exactly the same image.';
  stage.dataset.variant = selectedVariant;
  pipelineSelect.disabled = !!current?.heldBusy;
  element<HTMLButtonElement>('toggle-pipeline').disabled = pipelineSelect.disabled;
  element<HTMLButtonElement>('stage-toggle-pipeline').disabled = pipelineSelect.disabled;
  const displayedPipeline = current?.phase === 'held' ? current.renderer!.pipeline : current?.presented?.pipeline ?? selectedPipeline;
  stage.dataset.pipeline = displayedPipeline;
  stage.dataset.requestedPipeline = selectedPipeline;
  element('active-pipeline').textContent = PIPELINE_LABELS[displayedPipeline];
  element('stage-toggle-pipeline').textContent = `${PIPELINE_LABELS[displayedPipeline]} · switch`;
  benchmark.disabled = !current?.presented || held || current.heldBusy || profiler.running;
  downloadMetrics.disabled = !profiler.hasSamples;
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

function updateProfileUi(session?: Session): void {
  element('benchmark-status').textContent = profiler.progress(performance.now());
  updateCompletedComparison();
  if (!session?.performanceSample || performance.now()-lastProfileUiAt<500) return;
  lastProfileUiAt=performance.now();
  const summary=summarize(profiler.recent(session.id,session.performanceSample.pipeline,selectedVariant));
  const native=session.performanceSample.native;
  const status=(suffix:string):string=>{const value=Object.entries(native??{}).find(([key])=>key.endsWith(suffix))?.[1];return value===true?'active':value===false?'off / fallback':'—';};
  element('actual-path').textContent='Camera pixel reuse: '+status('reuseSourcePixelsUsed')+' · Async download: '+status('asyncReadbackUsed')+' · Source borrowing: '+status('sourceCanvasBorrowed');
  const ms=(value: number | undefined | null): string => value===null || value===undefined ? '—' : `${value.toFixed(1)} ms`;
  element('profile-fps').textContent=summary.processedFps===null ? 'Starting' : `${summary.processedFps.toFixed(1)} fps`;
  element('profile-video-fps').textContent=summary.videoDeliveryFps===null ? 'Unavailable' : `${summary.videoDeliveryFps.toFixed(1)} fps`;
  element('profile-p95').textContent=ms(summary.processing?.p95);
  element('profile-interval-p95').textContent=ms(summary.frameInterval?.p95);
  element('profile-face').textContent=ms(summary.stages.faceInferenceMs?.median);
  element('profile-face-roundtrip').textContent=ms(summary.stages.faceRequestWallMs?.median);
  element('profile-scheduler').textContent=ms(summary.stages.schedulerWaitMs?.median);
  element('profile-copy').textContent=ms(summary.stages.sourceReadbackMs?.median);
  element('profile-prepare').textContent=ms(summary.stages.prepareMs?.median);
  element('profile-finish').textContent=ms(summary.stages.finishMs?.median);
  element('profile-coverage').textContent=summary.hairCoverage===null ? '—' : `${Math.round(summary.hairCoverage*100)}%`;
  element('profile-count').textContent=`${summary.frames} completed frames · last ${Math.round(summary.durationMs/1000)} seconds`;
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
      intervalP95: segment.summary.frameInterval ? `${segment.summary.frameInterval.p95.toFixed(1)} ms` : '—',
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
  profiler.cancel(failed ? 'The camera session failed.' : 'The camera session closed.');
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
    if(current!==session||session.phase!=='live')return;
    session.pump=undefined;session.processing=false;session.switching=false;
    if(session.holdRequested){holdSession(session);return;}
    session.renderer!.selectPipeline(selectedPipeline);runFrames(session);
  } catch(error){if(current===session)closeSession(friendlyError(error),true);}
  finally{session.switching=false;}
}
function runPumpedFrames(session:Session):void {
  const mode=PROFILES[selectedPipeline].mode;if(mode==='baseline')return;
  const generation=session.generation,pipeline=selectedPipeline;
  const owns=()=>current===session&&session.phase==='live'&&session.generation===generation;
  session.pump=runExperimentPump({id:session.id,video:session.camera!.video,renderer:session.renderer!,detector:session.detector,
    hair:()=>session.hair,hairId:session.hairId,eyewearId:session.eyewearId,hairReady:()=>session.hairReady&&!session.hairError,
    mode,pipeline,generation,variant:()=>selectedVariant,owns,nextSequence:()=>++session.nextSequence,
    onBusy:()=>{if(owns())session.processing=true;},
    onHairError:error=>{if(owns()){session.hairError=messageFor(error);session.hairReady=false;session.hair.close();}},
    onError:error=>{if(owns())closeSession(friendlyError(error),true);},backend:()=>session.backend,
    onPublished:(input,identity)=>{
      if(!owns())return;session.performanceSample=profiler.add(input);
      session.presented={sequence:input.sequence,...identity,capturedAtMs:input.capturedAtMs,pipeline:input.pipeline};
      session.timing={faceMs:input.faceRequestWallMs,hairWaitMs:input.hairWaitMs,renderMs:input.renderMs,totalMs:input.totalMs};
      session.processing=false;session.canvas.hidden=false;welcome.hidden=true;
      stage.dataset.frameSequence=String(input.sequence);stage.dataset.sourceSha256=identity.sourceSHA256;stage.dataset.detectionSha256=identity.detectionSHA256;
      stage.dataset.hairStatus=input.variant==='accepted'?'off':!input.hasFace?'no-face':input.hasMask?'ready':'fallback';
      stage.dataset.changedPixels=String(input.changedPixels);stage.dataset.frames=String(input.sequence);
      const summary=summarize(profiler.recent(session.id,input.pipeline,input.variant));
      element('tracking').textContent=input.hasFace?'Face visible':'Looking for face';
      element('fps').textContent=summary.processedFps===null?'Starting':summary.processedFps.toFixed(1)+' fps';
      element('latency').textContent=Math.round(input.totalMs)+' ms';element('resolution').textContent=input.sourceWidth+' × '+input.sourceHeight;
      element('hair-latency').textContent=input.hairInferenceMs===null?'—':Math.round(input.hairInferenceMs+(input.hairExtractionMs??0))+' ms';
      element('hair-wait').textContent=Math.round(input.hairWaitMs)+' ms';element('render-latency').textContent=Math.round(input.renderMs)+' ms';
      element('changed-pixels').textContent=String(input.changedPixels);element('hair-coverage').textContent=summary.hairCoverage===null?'—':Math.round(summary.hairCoverage*100)+'%';
      element('stall-latency').textContent=summary.processing?Math.round(summary.processing.p95)+' ms':'—';
      setState(input.hasFace?'tracking':'searching',input.hasFace?'COMPARISON LIVE':'LOOKING FOR YOU',input.hasFace?'Switch experiments while moving. Hold to compare this exact image.':'Bring your face into view.');
      updateControls();showHairStatus();updateProfileUi(session);
      if(session.holdRequested){holdSession(session);return;}
      const next=profiler.takeNextPipeline();if(next)selectPipeline(next,false);
    },
  });
}

function runFrames(session: Session): void {
  if(PROFILES[selectedPipeline].mode==='baseline')runSerialFrames(session);else runPumpedFrames(session);
}
function runSerialFrames(session: Session): void {
  const video = session.camera!.video, generation = session.generation;
  let ended=false;
  const owns = () => current === session && session.phase === 'live' && session.generation === generation && !ended;
  const capture = document.createElement('canvas'), input = document.createElement('canvas');
  const captureContext = context2D(capture), inputContext = context2D(input);
  const hairBudget = new FrameBudget<HairSegmentationResult>();
  let lastHairWork:Promise<unknown>=Promise.resolve();
  let lastIdentity = -1, frames = 0, firstPresentedAt = 0, lastFrameAt = performance.now(), processing = false;
  let measuredPipeline: Pipeline | null = null, measuredVariant: string | null = null, measuredFrames = 0, maskedFrames = 0;
  const recent: {at: number; totalMs: number}[] = [];
  let scheduledAtMs = performance.now();
  const sourceStream = video.srcObject;
  const cameraSettingFps = sourceStream instanceof MediaStream ? sourceStream.getVideoTracks()[0]?.getSettings().frameRate ?? null : null;
  const watchdog = window.setInterval(() => {
    if (owns() && !processing && performance.now() - lastFrameAt > 6000)
      closeSession('The camera stopped sending images. Open it again to restart.', true);
  }, 1000);
  const endLoop=():void=>{ended=true;hairBudget.close();clearInterval(watchdog);capture.width=capture.height=input.width=input.height=0;};
  session.liveCleanups.push(endLoop);
  function schedule(): void {
    if (!owns()) return;
    if(PROFILES[selectedPipeline].mode!=='baseline'){endLoop();void lastHairWork.then(()=>{if(current===session&&session.phase==='live')runFrames(session);});return;}
    scheduledAtMs = performance.now();
    if (typeof video.requestVideoFrameCallback === 'function') {
      const id = video.requestVideoFrameCallback((_time, metadata) => { void process(metadata.presentedFrames, metadata); });
      session.cancelFrame = () => video.cancelVideoFrameCallback(id);
    } else {
      const id = requestAnimationFrame(() => { void process(video.currentTime); });
      session.cancelFrame = () => cancelAnimationFrame(id);
    }
  }
  async function process(identity: number, metadata?: VideoFrameCallbackMetadata): Promise<void> {
    if (!owns()) return;
    if (video.readyState < 2 || identity === lastIdentity) { schedule(); return; }
    lastIdentity = identity; processing = true; session.processing = true;
    const capturedAtMs = performance.now(), sequence = ++session.nextSequence, framePipeline=selectedPipeline;
    const schedulerWaitMs = capturedAtMs - scheduledAtMs;
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
      const sourceDrawStarted = performance.now();
      captureContext.drawImage(video, 0, 0, width, height);
      const sourceDrawMs = performance.now() - sourceDrawStarted;
      const detectorDrawStarted = performance.now();
      inputContext.drawImage(capture, 0, 0, input.width, input.height);
      const detectorDrawMs = performance.now() - detectorDrawStarted;
      const readbackStarted = performance.now();
      const sourceBytes = captureContext.getImageData(0, 0, width, height).data;
      const sourceReadbackMs = performance.now() - readbackStarted;
      const sourceHashStarted = performance.now(); let sourceHashMs = 0;
      const sourceHashPromise = sha256(sourceBytes).then(hash => {sourceHashMs=performance.now()-sourceHashStarted;return hash;});
      const wantsHair = selectedVariant === 'hair' && session.hairReady && !session.hairError && !hairBudget.busy;
      const hairBitmapPromise = wantsHair ? createImageBitmap(capture) : Promise.resolve(null);
      // Own every bitmap even if capture, hashing, detection, or session ownership fails.
      const hairStartPromise = Promise.all([sourceHashPromise, hairBitmapPromise]).then(([sourceSHA256, bitmap]) => {
        if (!bitmap) return;
        if (!owns() || selectedVariant !== 'hair') { bitmap.close(); return; }
        const started = hairBudget.start(sequence, async () => {
          if (!owns()) { bitmap.close(); return null; }
          try {const work=session.hair.segment(bitmap,sourceSHA256,sequence);lastHairWork=work.catch(()=>null);return await work;}
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
      const faceBitmapStarted = performance.now();
      const faceBitmap = await createImageBitmap(input);
      const faceBitmapMs = performance.now() - faceBitmapStarted;
      if (!owns()) { faceBitmap.close(); return; }
      const faceStarted = performance.now();
      let faceRequestWallMs = 0;
      const facePromise = session.detector.detect(faceBitmap, capturedAtMs).then(detection => {
        faceRequestWallMs=performance.now()-faceStarted;return detection;
      });
      const [detection, sourceSHA256] = await Promise.all([facePromise, sourceHashPromise, hairStartPromise]);
      const faceMs = performance.now() - faceStarted;
      const faceTiming = session.detector.lastTiming;
      if (!owns()) return;
      const detectionHashStarted = performance.now();
      const detectionSHA256 = await sha256(new TextEncoder().encode(JSON.stringify(detection)));
      const detectionHashMs = performance.now() - detectionHashStarted;
      if (!owns()) return;
      const pair = {sourceSHA256, detectionSHA256, eyewearModel: session.eyewearId};
      // Build the accepted frame privately while the independent hair worker runs.
      // Publishing happens once, after the current-pair optional result is selected.
      const renderStarted = performance.now();
      session.renderer!.selectPipeline(framePipeline);
      const ownedSource=createOwnedSourceFrame(capture,new ImageData(sourceBytes,width,height,{colorSpace:'srgb'}),
        {sourceSHA256,generation:sequence,sessionId:session.id,isCurrent:owns});
      const hasFace = await session.renderer!.prepare(capture, detection, pair, hairModelById(session.hairId),selectedVariant==='hair',ownedSource);
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
      const publishedAtMs = performance.now();
      const finishMs = publishedAtMs - finishingStarted;
      const renderMs = acceptedRenderMs + finishMs;
      if (!owns()) return;
      const stats = session.renderer!.stats;
      if (!stats) throw new Error('The current rendered pair has no status.');
      const candidate = (stats as unknown as {candidatePerformance?: Record<string, unknown>}).candidatePerformance;
      const native = nativeMetrics(candidate);
      session.performanceSample = profiler.add({sessionId:session.id,sequence,pipeline:session.renderer!.pipeline,variant:selectedVariant,
        capturedAtMs,publishedAtMs,videoPresentedFrames:metadata?.presentedFrames ?? null,videoMediaTime:metadata?.mediaTime ?? null,
        videoPresentationTimeMs:metadata?.presentationTime ?? null,cameraSettingFps,sourceWidth:width,sourceHeight:height,
        sourceDrawMs,detectorDrawMs,sourceReadbackMs,sourceHashMs,faceBitmapMs,faceRequestWallMs,
        faceInferenceMs:faceTiming?.inferenceMs ?? null,faceWorkerMs:faceTiming?.workerElapsedMs ?? null,
        faceExtractionMs:faceTiming?.workerExtractionMs ?? null,faceWorkerValidationMs:faceTiming?.workerValidationMs ?? null,
        faceClientValidationMs:faceTiming?.clientValidationMs ?? null,faceTransportSchedulingMs:faceTiming?.transportAndSchedulingMs ?? null,
        prerequisitesWaitMs:faceMs,detectionHashMs,prepareMs:acceptedRenderMs,finishMs,renderMs,totalMs:publishedAtMs-capturedAtMs,schedulerWaitMs,
        hairWaitMs,hairInferenceMs:segmented?.inferenceMs ?? null,hairExtractionMs:segmented?.extractionMs ?? null,
        hasFace,hasMask:stats.hasMask,maskMode:stats.maskOutputMode ?? null,fallback:stats.fallbackReason,changedPixels:stats.changedPixels,
        faceDelegate:session.detector.delegate,hairDelegate:session.backend.active,gpuRenderer:session.backend.renderer,
        cleanCameraMs:stats.timings.cleanCameraMs,composeMs:stats.timings.composeMs,continuityMs:stats.timings.continuityMs,
        finalChecksMs:stats.timings.finalChecksMs,publishMs:stats.timings.publishMs,native});
      session.presented = {sequence, sourceSHA256, detectionSHA256, capturedAtMs, pipeline: session.renderer!.pipeline};
      stage.dataset.frameSequence = String(sequence);
      stage.dataset.sourceSha256 = sourceSHA256; stage.dataset.detectionSha256 = detectionSHA256;
      stage.dataset.hairStatus = selectedVariant === 'accepted' ? 'off' : session.hairError ? 'unavailable' : !session.hairReady ? 'loading'
        : !hasFace ? 'no-face' : stats.fallbackReason ? 'fallback' : 'ready';
      stage.dataset.changedPixels = String(stats.changedPixels);
      session.canvas.hidden = false; welcome.hidden = true;
      element('tracking').textContent = hasFace ? 'Face visible' : 'Looking for face';
      setState(hasFace ? 'tracking' : 'searching', hasFace ? 'COMPARISON LIVE' : 'LOOKING FOR YOU',
        hasFace ? 'Switch algorithms to compare movement. Hold to compare the same image.' : 'Bring your face into view and look towards the camera.');
      frames++; const now = performance.now();
      const pipeline = session.renderer!.pipeline;
      if (pipeline !== measuredPipeline || selectedVariant !== measuredVariant) {
        measuredPipeline = pipeline; measuredVariant = selectedVariant;
        measuredFrames = maskedFrames = 0; firstPresentedAt = 0; recent.length = 0;
      }
      measuredFrames++; maskedFrames += Number(stats.hasMask);
      recent.push({at: now, totalMs: now - capturedAtMs});
      if (recent.length > 30) recent.shift();
      element('hair-coverage').textContent = selectedVariant === 'accepted' ? 'Off' : `${Math.round(100 * maskedFrames / measuredFrames)}% of ${measuredFrames} frames`;
      const ordered = recent.map(value => value.totalMs).sort((a,b) => a-b);
      element('stall-latency').textContent = `${Math.round(ordered[Math.min(ordered.length-1, Math.ceil(ordered.length*.95)-1)]!)} ms`;
      stage.dataset.pipeline = pipeline;
      session.timing = {faceMs, hairWaitMs, renderMs, totalMs: now - capturedAtMs};
      if (!firstPresentedAt) firstPresentedAt = now;
      const seconds = (now - firstPresentedAt) / 1000;
      element('fps').textContent = measuredFrames > 1 && seconds > 0 ? `${((measuredFrames - 1) / seconds).toFixed(1)} fps` : 'Starting';
      element('latency').textContent = `${Math.round(now - capturedAtMs)} ms`;
      element('resolution').textContent = `${video.videoWidth} × ${video.videoHeight}`;
      element('hair-latency').textContent = segmented ? `${Math.round(segmented.inferenceMs + segmented.extractionMs)} ms` : '—';
      element('hair-wait').textContent = `${Math.round(hairWaitMs)} ms`;
      element('render-latency').textContent = `${Math.round(renderMs)} ms`;
      element('changed-pixels').textContent = String(stats.changedPixels);
      stage.dataset.frames = String(frames);
      processing = false; session.processing = false;
      updateControls(); showHairStatus();
      updateProfileUi(session);
      lastFrameAt = performance.now();
      if (session.holdRequested) {
        session.holdRequested = false;
        if (hasFace) { holdSession(session); return; }
      }
      const nextPipeline = profiler.takeNextPipeline();
      if (nextPipeline) selectPipeline(nextPipeline, false);
      schedule();
    } catch (error) { if (owns()) closeSession(friendlyError(error), true); }
  }
  schedule();
}

async function openSession(): Promise<void> {
  if (current) return;
  for(const id of ['profile-fps','profile-video-fps','profile-p95','profile-interval-p95','profile-face','profile-face-roundtrip',
    'profile-scheduler','profile-copy','profile-prepare','profile-finish','profile-coverage']) element(id).textContent='—';
  element('profile-count').textContent='Waiting for completed frames from this session.';lastProfileUiAt=0;
  const previous = element<HTMLCanvasElement>('mirror'), canvas = previous.cloneNode(false) as HTMLCanvasElement;
  previous.replaceWith(canvas);
  const backend = detectHairBackend();
  const session: Session = {id: crypto.randomUUID(), phase: 'live', generation: 1, abort: new AbortController(),
    eyewearId: selectedEyewear, hairId: selectedHair, detector: new DetectorClient(),
    hair: new HairClient(selectedHair, {delegate: backend.requested, outputMode: 'category-only'}), backend,
    hairReady: false, hairError: null, canvas, liveCleanups: [], cleanups: [], nextSequence: 0, presented: null, heldAt: null,
    heldBusy: false, processing: false, holdRequested: false, budgetMisses: 0, timing: null, performanceSample:null};
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
    const {ComparisonRenderer: LiveHairRenderer} = await import('./comparison-renderer.ts');
    if (current !== session) return;
    session.renderer = await LiveHairRenderer.create(canvas, session.abort.signal, session.eyewearId);
    if (current !== session) { session.renderer.dispose(); return; }
    session.renderer.selectVariant(selectedVariant);
    session.renderer.selectPipeline(selectedPipeline);
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
  selectPipeline(PIPELINES.includes(value as Pipeline) ? value as Pipeline : 'base');
});
for (const id of ['toggle-pipeline', 'stage-toggle-pipeline']) element(id).addEventListener('click', () => {
  if (pipelineSelect.disabled) return;
  pipelineSelect.value = PIPELINES[(PIPELINES.indexOf(selectedPipeline)+1)%PIPELINES.length]!;
  pipelineSelect.dispatchEvent(new Event('change'));
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
        const base=result?.base as {acceptedPngDataUrl:string;hairPngDataUrl:string}|undefined;
        const different=base?PIPELINES.filter(id=>{const item=result![id] as typeof base;return item?.acceptedPngDataUrl!==base.acceptedPngDataUrl||item?.hairPngDataUrl!==base.hairPngDataUrl;}):[];
        element('held-result').textContent=base?(different.length?'Held differences found: '+different.map(id=>PIPELINE_LABELS[id]).join(', '):'All held experiment images match Test 2 pixel-for-pixel on this image.'):'Held comparison unavailable.';
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
    void pump.finishCurrent().then(()=>{if(current!==session||session.phase!=='live')return;pump.stop();session.pump=undefined;session.processing=false;holdSession(session);});return;
  }
  if (session.processing) {
    session.holdRequested = true; hold.disabled = true;
    element('hold-hint').textContent = 'Finishing the current paired frame, then stopping the camera…';
    return;
  }
  if (!session.renderer.stats?.hasFace) return;
  selectedPipeline = session.renderer.pipeline; pipelineSelect.value = selectedPipeline;
  // Hold owns the completed pair; discard any queued live-only switch before
  // preparing that same image for its full-mask comparison.
  session.renderer.selectPipeline(selectedPipeline);
  element('experiment-detail').textContent = PROFILES[selectedPipeline].detail;
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
    link.download = `performance-comparison-${new Date().toISOString().replaceAll(':', '-')}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 10_000);
    element('download-status').textContent = 'Comparison downloaded locally. It includes the original image and all eight outputs.';
  } catch (error) { element('download-status').textContent = messageFor(error); }
  finally { if (current === session) download.disabled = false; }
});
benchmark.addEventListener('click', () => {
  if (!current || current.phase!=='live' || !current.presented || benchmark.disabled) return;
  const choice=selectedPipeline==='base'?'source':selectedPipeline;
  const reverse=element<HTMLSelectElement>('benchmark-order').value==='experiment-first';
  selectPipeline(profiler.begin(current.id,selectedVariant,performance.now(),reverse?[choice,'base']:['base',choice]),false);
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
    link.download=`ar-speed-comparison-${new Date().toISOString().replaceAll(':','-')}.json`;
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
window.addEventListener('pagehide', () => closeSession());
document.addEventListener('visibilitychange', () => {
  if (document.hidden && current?.phase === 'live') closeSession('Camera closed while the page was hidden. Open it again when ready.');
});
declare global {
  interface Window { hairLivePreview: {diagnostics(): Record<string, unknown>; exportDiagnostic(): Record<string, unknown> | null}; }
  interface Window { arPerformanceProfiler: {snapshot(): Record<string, unknown>; samplesAfter(serial: number): FrameSample[]}; }
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
showEyewear(); updateControls(); showHairStatus(); updateProfileUi();
