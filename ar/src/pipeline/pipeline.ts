/** The frame pipeline. On each camera frame (requestVideoFrameCallback) the frame is drawn into a canvas of at most
 *  `captureMaxEdge` px and its pixels are read once, for the SHA-256 that ties the hair mask to its own image. The
 *  frame pump then runs inference (face landmarker on a 640 px copy; hair segmenter on the full frame) and lets the
 *  next frame's inference overlap the current frame's preparation, with at most two owned frames. Preparation waits
 *  for the previous frame's GPU completion and poses the frame; publication draws it. One timing row per published
 *  frame. */
import {FramePump} from './frame-pump.ts';
import {markFrame} from './frame-identity.ts';
import {bytesOfImageData, bytesOfVideoFrame, chooseCaptureSource, OwnedVideoFrame, sha256Hex} from './capture.ts';
import type {CaptureSource, FrameBytes} from './capture.ts';
import type {FrameMark} from './frame-identity.ts';
import type {FramePumpStats} from './frame-pump.ts';
import type {FrameInput} from './profiler.ts';
import type {LiveRenderer} from '../render/live-renderer.ts';
import type {DetectorClient, FaceStageTiming} from '../face/detector.ts';
import type {Detection} from '../face/protocol.ts';
import type {HairClient} from '../hair/client.ts';
import type {HairMask, HairSegmentationResult} from '../hair/protocol.ts';
import type {HairModel} from '../hair/models.ts';

/** The camera frame is captured at no more than this edge; the render keeps the capture size (up to 1280 wide). */
export const DEFAULT_CAPTURE_MAX_EDGE = 1280;
/** The face landmarker sees a copy of at most this edge. */
export const FACE_INPUT_MAX_EDGE = 640;
/** The hair segmenter sees a copy of at most this edge; the mask is that size and the render, the continuity cut and the
 *  CPU reference map frame pixels to mask pixels by nearest lookup (GPU output equal to the reference at both sizes on
 *  the laptop harness). At 640 the mask readback, the hair worker's largest cost after inference, is a quarter of the
 *  frame-size one (phone 20-25 → 8-10 ms); the owner accepted the 2:1 mask on the phone. `?hairinput=1280` restores the
 *  frame-size mask. */
export const DEFAULT_HAIR_INPUT_MAX_EDGE = 640;
/** Every frame carries its own hair mask: preparation waits for it. This deadline is only a guard against a stalled
 *  worker, after which the frame is drawn without hair rather than never. It is not a tuning: at 8 ms the laptop's fast
 *  capture drew one frame in four without its mask (arms blinking over hair) and the phone drew none with one
 *  (2026-09-15). The added age is the hair worker's own latency, which the hair input size below keeps small. */
export const HAIR_WAIT_MS = 120;
/** The hair worker as seen by the pipeline: results that arrived, frames drawn without their mask because it was late, and
 *  the last result's own timings. Rows record hair timings only for masks that were used, so a worker too slow for the frame
 *  is invisible there. */
export interface HairWorkerStats {results: number; missed: number; lastInferenceMs: number | null; lastExtractionMs: number | null; lastRoundTripMs: number | null;}

interface Packet {
  sequence: number; capturedAtMs: number; canvas: HTMLCanvasElement; bytes: FrameBytes; frame: OwnedVideoFrame | null; disposed: boolean;
  drawMs: number; videoFrames: number | null; mediaTime: number; presentation: number | null; hair: boolean;
}
interface Inferred {
  detection: Detection; sourceSHA256: string; detectionSHA256: string; face: FaceStageTiming | null;
  hashMs: number; detectorDrawMs: number; faceBitmapMs: number; faceWallMs: number; detectionHashMs: number;
  hair: Promise<HairSegmentationResult | null>; hairResult: HairSegmentationResult | null;
  inferenceStartedAt: number; hairAdmissionWaitMs: number;
}
interface Prepared {visible: boolean; mask: HairMask | null; hair: HairSegmentationResult | null; prepareMs: number; hairWaitMs: number;}
export interface PipelineContext {
  id: string; video: HTMLVideoElement; renderer: LiveRenderer; detector: DetectorClient;
  hair: () => HairClient; hairModel: HairModel; hairReady: () => boolean; hairEnabled: () => boolean;
  eyewearId: string;
  /** Capture max edge in px; default 1280. */
  captureMaxEdge?: number;
  /** How long a frame waits for its own hair mask before it is drawn without it; default HAIR_WAIT_MS. */
  hairWaitMs?: number;
  /** Hair input max edge in px; default the frame itself (DEFAULT_HAIR_INPUT_MAX_EDGE). */
  hairInputMaxEdge?: number;
  /** How the frame's pixels are taken (see capture.ts); default 'canvas'. */
  captureSource?: CaptureSource;
  /** Told once which capture source runs, with the fallback reason if any. */
  onCaptureSource?: (source: CaptureSource, reason: string | null) => void;
  owns: () => boolean; nextSequence: () => number;
  onHairError: (error: unknown) => void; onError: (error: unknown) => void;
  onPublished: (row: FrameInput, identity: {sourceSHA256: string; detectionSHA256: string}) => void;
  backend: () => {active: string | null; renderer: string | null};
}
export interface Pipeline {stop(): void; finishCurrent(): Promise<void>; stats: () => FramePumpStats; hair: () => HairWorkerStats; /** Stage of the active frame and the pump counts, for diagnostics. */ describe(): string;}

const hash = sha256Hex;
/** A scaled copy of the captured frame for a worker; the copy is a pure function of the frame, whose hash stays the identity.
 *  CPU-backed on the canvas path (as accepted); GPU-backed on the VideoFrame path, where nothing is ever read back. */
const scaledCopy = (source: HTMLCanvasElement, maxEdge: number, cpu: boolean): {canvas: HTMLCanvasElement; drawMs: number} => {
  const canvas = document.createElement('canvas'), scale = Math.min(1, maxEdge / Math.max(source.width, source.height));
  canvas.width = Math.max(1, Math.round(source.width * scale)); canvas.height = Math.max(1, Math.round(source.height * scale));
  const started = performance.now(); context(canvas, cpu).drawImage(source, 0, 0, canvas.width, canvas.height);
  return {canvas, drawMs: performance.now() - started};
};
const context = (canvas: HTMLCanvasElement, cpu = true): CanvasRenderingContext2D => {
  const c = canvas.getContext('2d', cpu ? {alpha: false, willReadFrequently: true, colorSpace: 'srgb'} : {alpha: false, colorSpace: 'srgb'}); if (!c) throw new Error('Camera canvas unavailable.'); return c;
};
function flatten(value: unknown, prefix: string, out: NonNullable<FrameInput['native']>): void {
  const visit = (item: unknown, name: string, depth: number): void => {
    if (!item || typeof item !== 'object' || depth > 6) return;
    for (const [key, value] of Object.entries(item)) {
      const path = name ? name + '.' + key : key;
      if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'string' || value === null) out[path] = value;
      else if (!Array.isArray(value) && !ArrayBuffer.isView(value)) visit(value, path, depth + 1);
    }
  };
  visit(value, prefix, 0);
}

export function runPipeline(c: PipelineContext): Pipeline {
  let stopped = false, draining = false, cancel: () => void = () => {}, lastMark: FrameMark | null = null, lastVideoAt = performance.now(), hairTail: Promise<unknown> = Promise.resolve();
  // Where the active frame is, for the startup diagnostics: a stall names its stage instead of waiting silently.
  const trace = {stage: 'waiting for the first camera frame', since: performance.now()};
  const at = (stage: string): void => {trace.stage = stage; trace.since = performance.now();};
  const owns = () => !stopped && c.owns();
  const stream = c.video.srcObject;
  const cameraFps = stream instanceof MediaStream ? stream.getVideoTracks()[0]?.getSettings().frameRate ?? null : null;
  const captureMaxEdge = c.captureMaxEdge ?? DEFAULT_CAPTURE_MAX_EDGE, hairWaitMs = c.hairWaitMs ?? HAIR_WAIT_MS, hairInputMaxEdge = c.hairInputMaxEdge ?? DEFAULT_HAIR_INPUT_MAX_EDGE;
  const capture = chooseCaptureSource(c.captureSource ?? 'canvas', typeof VideoFrame === 'function');
  c.onCaptureSource?.(capture.source, capture.reason);
  let cpuCanvases = capture.source === 'canvas';
  // A browser that refuses to construct a VideoFrame from the camera video falls back to the canvas for the session.
  const fallBackToCanvas = (error: unknown): void => {
    capture.source = 'canvas'; cpuCanvases = true;
    c.onCaptureSource?.('canvas', `VideoFrame capture failed: ${error instanceof Error ? error.message : String(error)}; the canvas capture is used.`);
  };
  const hairStats: HairWorkerStats = {results: 0, missed: 0, lastInferenceMs: null, lastExtractionMs: null, lastRoundTripMs: null};
  const pump = new FramePump<Packet, Inferred, Prepared>({mode: 'overlap', identity: p => p,
    infer: async (p, signal) => {
      const alive = () => owns() && !p.disposed && !signal.aborted;
      at('hashing the frame and preparing the face input');
      const inferenceStartedAt = performance.now(), hashStart = performance.now(); let hashMs = 0;
      const sha = p.bytes.bytes.then(hash).then(value => {hashMs = performance.now() - hashStart; return value;});
      // The face landmarker's copy is drawn first; when the hair edge agrees with it, the same copy serves both workers.
      // On the VideoFrame path each worker's copy matches its delegate: a CPU worker reading a GPU-backed bitmap would
      // pay the readback itself, inside its inference (synthetic run 2026-09-15: face 17 → 31 ms).
      const faceCpu = cpuCanvases || c.detector.delegate === 'CPU', hairCpu = cpuCanvases || c.backend().active === 'CPU';
      const input = scaledCopy(p.canvas, FACE_INPUT_MAX_EDGE, faceCpu), detectorDrawMs = input.drawMs;
      const hairSource = hairInputMaxEdge >= Math.max(p.canvas.width, p.canvas.height) ? null : hairInputMaxEdge === FACE_INPUT_MAX_EDGE && faceCpu === hairCpu ? input : scaledCopy(p.canvas, hairInputMaxEdge, hairCpu);
      const hairBitmap = c.hairReady() && p.hair ? createImageBitmap(hairSource ? hairSource.canvas : p.canvas) : Promise.resolve(null);
      let markHairStarted: () => void = () => {};
      const hairStarted = new Promise<void>(resolve => {markHairStarted = resolve;});
      // One serial hair worker; at most the pump's second owned image can wait.
      const previousHair = hairTail;
      const hair = Promise.all([sha, hairBitmap, previousHair]).then(async ([sourceSHA256, bitmap]) => {
        if (!bitmap) {markHairStarted(); return null;} if (!alive()) {bitmap.close(); markHairStarted(); return null;}
        try {
          const started = performance.now(), result = c.hair().segment(bitmap, sourceSHA256, p.sequence); markHairStarted(); const value = await result;
          hairStats.results++; hairStats.lastInferenceMs = value.inferenceMs; hairStats.lastExtractionMs = value.extractionMs; hairStats.lastRoundTripMs = performance.now() - started;
          return value;
        }
        catch (error) {if (alive()) c.onHairError(error); return null;}
        finally {markHairStarted();}
      }).catch(async (error) => {const bitmap = await hairBitmap.catch(() => null); bitmap?.close(); markHairStarted(); if (alive()) c.onHairError(error); return null;});
      hairTail = hair.then(() => undefined);
      const bitmapStart = performance.now(), faceBitmap = createImageBitmap(input.canvas); const bitmap = await faceBitmap; const faceBitmapMs = performance.now() - bitmapStart;
      // The copies are released once both bitmaps own their pixels.
      void Promise.allSettled([hairBitmap, faceBitmap]).then(() => {input.canvas.width = input.canvas.height = 0; if (hairSource && hairSource !== input) hairSource.canvas.width = hairSource.canvas.height = 0;});
      if (!alive()) {bitmap.close(); throw new DOMException('Frame revoked.', 'AbortError');}
      at('face landmarker'); const faceStart = performance.now(); const detection = await c.detector.detect(bitmap, p.capturedAtMs);
      const faceWallMs = performance.now() - faceStart, face = c.detector.lastTiming;
      const sourceSHA256 = await sha; const detectionHashStart = performance.now();
      const detectionSHA256 = await hash(new TextEncoder().encode(JSON.stringify(detection)));
      const detectionHashMs = performance.now() - detectionHashStart, hairAdmissionStart = performance.now();
      // Detached hair promises must not become a hidden queue: inference releases its slot only once this image owns
      // the hair worker (or is skipped).
      at('waiting for the hair worker to take the frame'); await hairStarted;
      const hairAdmissionWaitMs = performance.now() - hairAdmissionStart;
      if (!alive()) throw new DOMException('Frame revoked.', 'AbortError');
      const result: Inferred = {detection, sourceSHA256, detectionSHA256, face, hashMs, detectorDrawMs, faceBitmapMs, faceWallMs,
        detectionHashMs, hair, hairResult: null, inferenceStartedAt, hairAdmissionWaitMs};
      void hair.then(value => {if (alive()) result.hairResult = value;}); return result;
    },
    prepare: async (p, i, signal) => {
      if (!owns() || signal.aborted) throw new DOMException('Frame revoked.', 'AbortError');
      const hairEnabled = c.hairEnabled();
      c.renderer.setHairEnabled(hairEnabled);
      at("waiting for the previous frame's GPU work, then posing"); const started = performance.now();
      const visible = await c.renderer.prepare(p.canvas, i.detection, {sourceSHA256: i.sourceSHA256, detectionSHA256: i.detectionSHA256, eyewearModel: c.eyewearId},
        c.hairModel, hairEnabled);
      const prepareMs = performance.now() - started, waitStart = performance.now();
      let hair = i.hairResult;
      if (!hair && hairEnabled && visible) {
        at('waiting for the hair mask'); let timer: ReturnType<typeof setTimeout> | undefined;
        try {hair = await Promise.race([i.hair, new Promise<null>(resolve => {timer = setTimeout(() => resolve(null), hairWaitMs);})]);}
        finally {if (timer !== undefined) clearTimeout(timer);}
        if (!hair) hairStats.missed++;
      }
      if (!owns() || signal.aborted) throw new DOMException('Frame revoked.', 'AbortError');
      if (hair && (hair.sequence !== p.sequence || hair.sourceSHA256 !== i.sourceSHA256)) throw new Error('Hair result belongs to another image.');
      const mask = hair && hairEnabled ? {...hair, detectionSHA256: i.detectionSHA256} : null;
      return {visible, mask, hair, prepareMs, hairWaitMs: performance.now() - waitStart};
    },
    publish: (p, i, r) => {
      if (!owns()) throw new DOMException('Frame revoked.', 'AbortError');
      at('drawing'); const finishStart = performance.now(); c.renderer.finish(r.mask); const publishedAtMs = performance.now(), finishMs = publishedAtMs - finishStart;
      const stats = c.renderer.stats; if (!stats) throw new Error('The renderer published no statistics.');
      const backend = c.backend();
      const native: NonNullable<FrameInput['native']> = {};
      flatten(stats.render, 'render', native);
      native['render.audit'] = stats.audit.ran; native['render.auditMs'] = stats.audit.ms; native['render.gpuWaitPolls'] = stats.gpuWaitPolls; native['render.gpuWaitTimedOut'] = stats.gpuWaitTimedOut;
      for (const [key, value] of Object.entries(pump.stats)) if (typeof value === 'number' || typeof value === 'boolean') native['pump.' + key] = value;
      native['pump.inputWaitMs'] = i.inferenceStartedAt - p.capturedAtMs;
      native['capture.source'] = capture.source; native['capture.format'] = p.bytes.format;
      const row: FrameInput = {sessionId: c.id, sequence: p.sequence, hair: p.hair, capturedAtMs: p.capturedAtMs, publishedAtMs,
        videoPresentedFrames: p.videoFrames, videoMediaTime: p.mediaTime, videoPresentationTimeMs: p.presentation, cameraSettingFps: cameraFps,
        sourceWidth: p.canvas.width, sourceHeight: p.canvas.height, sourceDrawMs: p.drawMs, detectorDrawMs: i.detectorDrawMs, sourceReadbackMs: p.bytes.readMs(),
        sourceHashMs: i.hashMs, faceBitmapMs: i.faceBitmapMs, faceRequestWallMs: i.faceWallMs, faceInferenceMs: i.face?.inferenceMs ?? null,
        faceWorkerMs: i.face?.workerElapsedMs ?? null, faceExtractionMs: i.face?.workerExtractionMs ?? null, faceWorkerValidationMs: i.face?.workerValidationMs ?? null,
        faceClientValidationMs: i.face?.clientValidationMs ?? null, faceTransportSchedulingMs: i.face?.transportAndSchedulingMs ?? null,
        detectionHashMs: i.detectionHashMs, schedulerWaitMs: i.inferenceStartedAt - p.capturedAtMs,
        prerequisitesWaitMs: finishStart - r.prepareMs - r.hairWaitMs - i.inferenceStartedAt, hairAdmissionWaitMs: i.hairAdmissionWaitMs,
        hairWaitMs: r.hairWaitMs, hairInferenceMs: r.hair?.inferenceMs ?? null, hairExtractionMs: r.hair?.extractionMs ?? null,
        prepareMs: r.prepareMs, gpuWaitMs: stats.gpuWaitMs, poseMs: stats.poseMs, finishMs, maskUploadMs: stats.render.maskUploadMs,
        continuityMs: stats.render.continuityMs, submitMs: stats.render.submitMs, renderMs: r.prepareMs + finishMs, totalMs: publishedAtMs - p.capturedAtMs,
        hasFace: r.visible, hasMask: stats.hasMask, fallback: stats.fallbackReason,
        faceDelegate: c.detector.delegate, hairDelegate: backend.active, gpuRenderer: backend.renderer, native};
      c.onPublished(row, {sourceSHA256: i.sourceSHA256, detectionSHA256: i.detectionSHA256}); at('published; waiting for the next camera frame');
    },
    disposeFrame: p => {p.disposed = true; p.canvas.width = p.canvas.height = 0; p.frame?.close();},
    onError: error => {if (owns()) c.onError(error);},
  });
  function schedule(): void {
    if (!owns() || draining) return;
    const callback = (_now: number, metadata?: VideoFrameCallbackMetadata): void => {
      if (!owns() || draining) return;
      // A presented frame is identified by the callback's counter (see frame-identity.ts), never by currentTime.
      const ready = c.video.readyState >= 2, {fresh, mark} = markFrame(metadata, c.video.currentTime, lastMark);
      if (ready && fresh) {lastMark = mark; lastVideoAt = performance.now();}
      pump.offer(() => {
        if (!ready || !fresh) return null;
        at('capturing');
        const capturedAtMs = performance.now(), canvas = document.createElement('canvas');
        const scale = Math.min(1, captureMaxEdge / Math.max(c.video.videoWidth, c.video.videoHeight));
        canvas.width = Math.max(1, Math.round(c.video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(c.video.videoHeight * scale));
        let bytes: FrameBytes | null = null, frame: OwnedVideoFrame | null = null, drawMs = 0;
        if (capture.source === 'videoframe') {
          // The frame's own bytes are its identity; the canvas is GPU-backed and is never read back.
          try {
            const videoFrame = new VideoFrame(c.video); frame = new OwnedVideoFrame(videoFrame);
            try {
              bytes = bytesOfVideoFrame(videoFrame);
              const drawStart = performance.now(); context(canvas, false).drawImage(videoFrame, 0, 0, canvas.width, canvas.height); drawMs = performance.now() - drawStart;
            } catch (error) {frame.close(); frame = null; throw error;}
          } catch (error) {fallBackToCanvas(error); canvas.width = canvas.height = 0; return null;}
        }
        if (!bytes) {
          const ctx = context(canvas), drawStart = performance.now(); ctx.drawImage(c.video, 0, 0, canvas.width, canvas.height); drawMs = performance.now() - drawStart;
          const readStart = performance.now(), rgba = ctx.getImageData(0, 0, canvas.width, canvas.height);
          bytes = bytesOfImageData(rgba, performance.now() - readStart);
        }
        // The captured pixels are the frame (their SHA-256 pairs the hair mask with them); the counter and media time are
        // the callback's labels, at most one frame behind an image that arrived during the draw.
        return {sequence: c.nextSequence(), capturedAtMs, canvas, bytes, frame, disposed: false, drawMs,
          videoFrames: mark.presented, mediaTime: mark.mediaTime, presentation: metadata?.presentationTime ?? null, hair: c.hairEnabled()};
      }); schedule();
    };
    if (typeof c.video.requestVideoFrameCallback === 'function') {const id = c.video.requestVideoFrameCallback(callback); cancel = () => c.video.cancelVideoFrameCallback(id);}
    else {const id = requestAnimationFrame(callback); cancel = () => cancelAnimationFrame(id);}
  }
  const watchdog = setInterval(() => {
    if (owns() && !draining && performance.now() - lastVideoAt > 6000)
      c.onError(new Error('The camera stopped sending images. Open it again to restart.'));
  }, 1000);
  schedule();
  return {stop() {stopped = true; cancel(); clearInterval(watchdog); pump.stop();},
    async finishCurrent() {draining = true; cancel(); clearInterval(watchdog); await pump.finishCurrent(); await hairTail;}, stats: () => pump.stats, hair: () => ({...hairStats}),
    describe() {
      const s = pump.stats;
      return `${trace.stage} for ${Math.round(performance.now() - trace.since)} ms · offered ${s.offered}, captured ${s.captured}, published ${s.published}, dropped ${s.dropped} (locked ${s.lockedDrops}, misses ${s.captureMisses})`
        + ` · inference ${s.inferenceCalls} calls${s.lastInferenceMs === null ? '' : `, last ${Math.round(s.lastInferenceMs)} ms`}${s.failed ? ' · pump failed' : ''}`;
    }};
}
