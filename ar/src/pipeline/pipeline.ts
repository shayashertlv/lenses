/** The frame pipeline. On each camera frame (requestVideoFrameCallback) the frame is drawn into a canvas of at most
 *  `captureMaxEdge` px and its pixels are read once, for the SHA-256 that ties the hair mask to its own image. The
 *  frame pump then runs inference (face landmarker on a 640 px copy; hair segmenter on the full frame) and lets the
 *  next frame's inference overlap the current frame's preparation, with at most two owned frames. Preparation waits
 *  for the previous frame's GPU completion and poses the frame; publication draws it. One timing row per published
 *  frame. */
import {FramePump} from './frame-pump.ts';
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
/** How long preparation waits for the frame's own hair mask before the frame is drawn without it. */
export const HAIR_WAIT_MS = 8;

interface Packet {
  sequence: number; capturedAtMs: number; canvas: HTMLCanvasElement; rgba: ImageData; disposed: boolean;
  drawMs: number; readMs: number; videoFrames: number | null; mediaTime: number; presentation: number | null; hair: boolean;
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
  owns: () => boolean; nextSequence: () => number;
  onHairError: (error: unknown) => void; onError: (error: unknown) => void;
  onPublished: (row: FrameInput, identity: {sourceSHA256: string; detectionSHA256: string}) => void;
  backend: () => {active: string | null; renderer: string | null};
}
export interface Pipeline {stop(): void; finishCurrent(): Promise<void>; stats: () => FramePumpStats;}

const hash = async (bytes: Uint8Array | Uint8ClampedArray): Promise<string> => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(bytes).buffer)), v => v.toString(16).padStart(2, '0')).join('');
const context = (canvas: HTMLCanvasElement): CanvasRenderingContext2D => {
  const c = canvas.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'}); if (!c) throw new Error('Camera canvas unavailable.'); return c;
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
  let stopped = false, draining = false, cancel: () => void = () => {}, lastMedia = -1, lastVideoTime = -1, lastVideoAt = performance.now(), hairTail: Promise<unknown> = Promise.resolve();
  const owns = () => !stopped && c.owns();
  const stream = c.video.srcObject;
  const cameraFps = stream instanceof MediaStream ? stream.getVideoTracks()[0]?.getSettings().frameRate ?? null : null;
  const captureMaxEdge = c.captureMaxEdge ?? DEFAULT_CAPTURE_MAX_EDGE;
  const pump = new FramePump<Packet, Inferred, Prepared>({mode: 'overlap', identity: p => p,
    infer: async (p, signal) => {
      const alive = () => owns() && !p.disposed && !signal.aborted;
      const inferenceStartedAt = performance.now(), hashStart = performance.now(); let hashMs = 0;
      const sha = hash(p.rgba.data).then(value => {hashMs = performance.now() - hashStart; return value;});
      const hairBitmap = c.hairReady() && p.hair ? createImageBitmap(p.canvas) : Promise.resolve(null);
      let markHairStarted: () => void = () => {};
      const hairStarted = new Promise<void>(resolve => {markHairStarted = resolve;});
      // One serial hair worker; at most the pump's second owned image can wait.
      const previousHair = hairTail;
      const hair = Promise.all([sha, hairBitmap, previousHair]).then(async ([sourceSHA256, bitmap]) => {
        if (!bitmap) {markHairStarted(); return null;} if (!alive()) {bitmap.close(); markHairStarted(); return null;}
        try {const result = c.hair().segment(bitmap, sourceSHA256, p.sequence); markHairStarted(); return await result;}
        catch (error) {if (alive()) c.onHairError(error); return null;}
        finally {markHairStarted();}
      }).catch(async (error) => {const bitmap = await hairBitmap.catch(() => null); bitmap?.close(); markHairStarted(); if (alive()) c.onHairError(error); return null;});
      hairTail = hair.then(() => undefined);
      const input = document.createElement('canvas'), scale = Math.min(1, FACE_INPUT_MAX_EDGE / Math.max(p.canvas.width, p.canvas.height));
      input.width = Math.max(1, Math.round(p.canvas.width * scale)); input.height = Math.max(1, Math.round(p.canvas.height * scale));
      const drawStart = performance.now(); context(input).drawImage(p.canvas, 0, 0, input.width, input.height); const detectorDrawMs = performance.now() - drawStart;
      const bitmapStart = performance.now(); const bitmap = await createImageBitmap(input); const faceBitmapMs = performance.now() - bitmapStart;
      input.width = input.height = 0;
      if (!alive()) {bitmap.close(); throw new DOMException('Frame revoked.', 'AbortError');}
      const faceStart = performance.now(); const detection = await c.detector.detect(bitmap, p.capturedAtMs);
      const faceWallMs = performance.now() - faceStart, face = c.detector.lastTiming;
      const sourceSHA256 = await sha; const detectionHashStart = performance.now();
      const detectionSHA256 = await hash(new TextEncoder().encode(JSON.stringify(detection)));
      const detectionHashMs = performance.now() - detectionHashStart, hairAdmissionStart = performance.now();
      // Detached hair promises must not become a hidden queue: inference releases its slot only once this image owns
      // the hair worker (or is skipped).
      await hairStarted;
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
      const started = performance.now();
      const visible = await c.renderer.prepare(p.canvas, i.detection, {sourceSHA256: i.sourceSHA256, detectionSHA256: i.detectionSHA256, eyewearModel: c.eyewearId},
        c.hairModel, hairEnabled);
      const prepareMs = performance.now() - started, waitStart = performance.now();
      let hair = i.hairResult;
      if (!hair && hairEnabled && visible) {
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {hair = await Promise.race([i.hair, new Promise<null>(resolve => {timer = setTimeout(() => resolve(null), HAIR_WAIT_MS);})]);}
        finally {if (timer !== undefined) clearTimeout(timer);}
      }
      if (!owns() || signal.aborted) throw new DOMException('Frame revoked.', 'AbortError');
      if (hair && (hair.sequence !== p.sequence || hair.sourceSHA256 !== i.sourceSHA256)) throw new Error('Hair result belongs to another image.');
      const mask = hair && hairEnabled ? {...hair, detectionSHA256: i.detectionSHA256} : null;
      return {visible, mask, hair, prepareMs, hairWaitMs: performance.now() - waitStart};
    },
    publish: (p, i, r) => {
      if (!owns()) throw new DOMException('Frame revoked.', 'AbortError');
      const finishStart = performance.now(); c.renderer.finish(r.mask); const publishedAtMs = performance.now(), finishMs = publishedAtMs - finishStart;
      const stats = c.renderer.stats; if (!stats) throw new Error('The renderer published no statistics.');
      const backend = c.backend();
      const native: NonNullable<FrameInput['native']> = {};
      flatten(stats.render, 'render', native);
      native['render.audit'] = stats.audit.ran; native['render.auditMs'] = stats.audit.ms; native['render.gpuWaitPolls'] = stats.gpuWaitPolls;
      for (const [key, value] of Object.entries(pump.stats)) if (typeof value === 'number' || typeof value === 'boolean') native['pump.' + key] = value;
      native['pump.inputWaitMs'] = i.inferenceStartedAt - p.capturedAtMs;
      const row: FrameInput = {sessionId: c.id, sequence: p.sequence, hair: p.hair, capturedAtMs: p.capturedAtMs, publishedAtMs,
        videoPresentedFrames: p.videoFrames, videoMediaTime: p.mediaTime, videoPresentationTimeMs: p.presentation, cameraSettingFps: cameraFps,
        sourceWidth: p.canvas.width, sourceHeight: p.canvas.height, sourceDrawMs: p.drawMs, detectorDrawMs: i.detectorDrawMs, sourceReadbackMs: p.readMs,
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
      c.onPublished(row, {sourceSHA256: i.sourceSHA256, detectionSHA256: i.detectionSHA256});
    },
    disposeFrame: p => {p.disposed = true; p.canvas.width = p.canvas.height = 0;},
    onError: error => {if (owns()) c.onError(error);},
  });
  function schedule(): void {
    if (!owns() || draining) return;
    const callback = (_now: number, metadata?: VideoFrameCallbackMetadata): void => {
      if (!owns() || draining) return;
      if (c.video.readyState >= 2 && c.video.currentTime !== lastVideoTime) {lastVideoTime = c.video.currentTime; lastVideoAt = performance.now();}
      pump.offer(() => {
        const mediaTime = c.video.currentTime; if (c.video.readyState < 2 || mediaTime === lastMedia) return null;
        const capturedAtMs = performance.now(), canvas = document.createElement('canvas');
        const scale = Math.min(1, captureMaxEdge / Math.max(c.video.videoWidth, c.video.videoHeight));
        canvas.width = Math.max(1, Math.round(c.video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(c.video.videoHeight * scale));
        const ctx = context(canvas), drawStart = performance.now(); ctx.drawImage(c.video, 0, 0, canvas.width, canvas.height); const drawMs = performance.now() - drawStart;
        if (c.video.currentTime !== mediaTime) {canvas.width = canvas.height = 0; return null;}
        lastMedia = mediaTime; const readStart = performance.now(), rgba = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const pairedMetadata = metadata && Math.abs(metadata.mediaTime - mediaTime) < .001 ? metadata : null;
        return {sequence: c.nextSequence(), capturedAtMs, canvas, rgba, disposed: false, drawMs, readMs: performance.now() - readStart,
          videoFrames: pairedMetadata?.presentedFrames ?? null, mediaTime, presentation: pairedMetadata?.presentationTime ?? null, hair: c.hairEnabled()};
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
    async finishCurrent() {draining = true; cancel(); clearInterval(watchdog); await pump.finishCurrent(); await hairTail;}, stats: () => pump.stats};
}
