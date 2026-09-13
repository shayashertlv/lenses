export type CapturePath = 'video-frame-copy' | 'canvas-video-frame-fallback' | 'canvas-video-fallback';
export type CaptureFallback = 'video-frame-unavailable' | 'video-frame-construction-failed'
  | 'rgba-copy-unavailable' | 'dimensions-or-transform' | 'rgba-copy-failed'
  | 'rgba-layout-unavailable' | 'rgba-alpha-unavailable' | 'orientation-metadata-unavailable';
export interface CaptureTelemetry {
  requestedPath: 'video-frame-copy'; actualPath: CapturePath; fallbackReason: CaptureFallback | null;
  snapshotMs: number; copyToMs: number; canvasWriteMs: number; readbackMs: number; totalMs: number;
  width: number; height: number; copiedBytes: number;
  videoWidth: number | null; videoHeight: number | null;
  frameCodedWidth: number | null; frameCodedHeight: number | null;
  frameVisibleWidth: number | null; frameVisibleHeight: number | null;
  frameDisplayWidth: number | null; frameDisplayHeight: number | null;
  frameRotation: number | null; frameFlip: boolean | null; orientationMetadataAvailable: boolean;
}
export interface CapturedPixels {
  /** Storage is ready only after ready resolves; every consumer must await it. */
  readonly rgba: ImageData;
  readonly ready: Promise<CaptureTelemetry>;
}
/** Minimal, typed native boundary also used by lifecycle tests. */
export interface OwnedVideoFrame {
  readonly codedWidth?: number; readonly codedHeight?: number;
  readonly displayWidth: number; readonly displayHeight: number;
  readonly visibleRect: {readonly x: number; readonly y: number; readonly width: number; readonly height: number} | null;
  readonly rotation?: number; readonly flip?: boolean;
  copyTo?(destination: Uint8ClampedArray<ArrayBuffer>, options: VideoFrameCopyToOptions): Promise<PlaneLayout[]>;
  close(): void;
}
export interface CaptureDependencies {
  createFrame?: ((video: HTMLVideoElement, timestampUs: number) => OwnedVideoFrame) | null;
  createImageData?: (width: number, height: number) => ImageData;
  now?: () => number;
}
const revoked = (): DOMException => new DOMException('The captured image was revoked.', 'AbortError');
const dimension = (value: unknown): number | null => typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : null;

/** One session, one asynchronous copy. Call capture synchronously in the same
 * camera callback / FramePump lazy factory as metadata admission. Keep canvas
 * leased until ready AND all later readers settle. Check busy before offering
 * another frame, so a pending copy cannot be replaced into a third image lease.
 * stop revokes publication immediately; whenIdle waits for the native copy to
 * settle and its VideoFrame to close. There is no queue and no later video read.
 */
export class ExactVideoFrameCapture {
  private readonly createFrame: CaptureDependencies['createFrame'];
  private readonly makeImageData: (width: number, height: number) => ImageData;
  private readonly now: () => number;
  private pending: Promise<CaptureTelemetry> | null = null;
  private stopped = false;
  private counts = {requests: 0, copied: 0, fallbacks: 0, rejectedBusy: 0, revoked: 0, failed: 0, framesClosed: 0};
  constructor(dependencies: CaptureDependencies = {}) {
    this.createFrame = dependencies.createFrame === undefined
      ? (typeof VideoFrame === 'function'
        ? (video, timestampUs) => new VideoFrame(video, {timestamp: timestampUs, alpha: 'discard'}) : null)
      : dependencies.createFrame;
    this.makeImageData = dependencies.createImageData ?? ((width, height) => new ImageData(width, height, {colorSpace: 'srgb'}));
    this.now = dependencies.now ?? (() => performance.now());
  }
  get busy(): boolean { return this.pending !== null; }
  get stats() { return {...this.counts, busy: this.busy, stopped: this.stopped}; }
  capture(video: HTMLVideoElement, canvas: HTMLCanvasElement, context: CanvasRenderingContext2D,
    capturedAtMs: number, isCurrent: () => boolean): CapturedPixels | null {
    if (this.stopped || !isCurrent()) throw revoked();
    if (this.pending) { this.counts.rejectedBusy++; return null; }
    if (!Number.isFinite(capturedAtMs) || capturedAtMs < 0 || canvas.width <= 0 || canvas.height <= 0)
      throw new Error('Invalid captured image dimensions or timestamp.');
    this.counts.requests++;
    const start = this.now(), rgba = this.makeImageData(canvas.width, canvas.height);
    const telemetry: CaptureTelemetry = {requestedPath: 'video-frame-copy', actualPath: 'video-frame-copy', fallbackReason: null,
      snapshotMs: 0, copyToMs: 0, canvasWriteMs: 0, readbackMs: 0, totalMs: 0,
      width: canvas.width, height: canvas.height, copiedBytes: 0,
      videoWidth: dimension(video.videoWidth), videoHeight: dimension(video.videoHeight),
      frameCodedWidth: null, frameCodedHeight: null, frameVisibleWidth: null, frameVisibleHeight: null,
      frameDisplayWidth: null, frameDisplayHeight: null, frameRotation: null, frameFlip: null, orientationMetadataAvailable: false};
    let frame: OwnedVideoFrame | null = null;
    let fallback: CaptureFallback | null = null;
    const alive = (): boolean => !this.stopped && isCurrent();
    const canvasFallback = (source: CanvasImageSource, reason: CaptureFallback, frozen: boolean): void => {
      if (!alive()) throw revoked();
      telemetry.actualPath = frozen ? 'canvas-video-frame-fallback' : 'canvas-video-fallback';
      telemetry.fallbackReason = reason;
      const drawStart = this.now();
      context.drawImage(source, 0, 0, canvas.width, canvas.height);
      telemetry.canvasWriteMs += this.now() - drawStart;
      const readStart = this.now();
      // The readback is authoritative for BOTH canvas and inference/hash bytes.
      rgba.data.set(context.getImageData(0, 0, canvas.width, canvas.height).data);
      telemetry.readbackMs += this.now() - readStart;
      this.counts.fallbacks++;
    };
    try {
      if (!this.createFrame) fallback = 'video-frame-unavailable';
      else {
        try { frame = this.createFrame(video, Math.round(capturedAtMs * 1000)); }
        catch { fallback = 'video-frame-construction-failed'; }
      }
      telemetry.snapshotMs = this.now() - start;
      if (!frame) {
        // API absence/construction failure is still inside the original task.
        // Reading live video after a rejected asynchronous copy is forbidden.
        canvasFallback(video, fallback!, false);
        telemetry.totalMs = this.now() - start;
        return {rgba, ready: Promise.resolve(telemetry)};
      }
      const rect = frame.visibleRect;
      const rotation = frame.rotation, flip = frame.flip;
      telemetry.frameCodedWidth = dimension(frame.codedWidth); telemetry.frameCodedHeight = dimension(frame.codedHeight);
      telemetry.frameVisibleWidth = dimension(rect?.width); telemetry.frameVisibleHeight = dimension(rect?.height);
      telemetry.frameDisplayWidth = dimension(frame.displayWidth); telemetry.frameDisplayHeight = dimension(frame.displayHeight);
      telemetry.frameRotation = typeof rotation === 'number' && [0, 90, 180, 270].includes(rotation) ? rotation : null;
      telemetry.frameFlip = typeof flip === 'boolean' ? flip : null;
      telemetry.orientationMetadataAvailable = telemetry.frameRotation !== null && telemetry.frameFlip !== null;
      // Some browsers retain camera orientation internally without exposing it
      // and draw VideoFrames without applying that orientation. Missing metadata
      // cannot mean zero rotation. Use G's video snapshot in THIS camera task,
      // before any asynchronous copy can let the live image advance.
      if (!telemetry.orientationMetadataAvailable) fallback = 'orientation-metadata-unavailable';
      else if (!rect || rect.width !== canvas.width || rect.height !== canvas.height
        || frame.displayWidth !== canvas.width || frame.displayHeight !== canvas.height
        || rotation !== 0 || flip !== false) fallback = 'dimensions-or-transform';
      if (fallback) {
        const unusedFrame = frame; frame = null;
        unusedFrame.close(); this.counts.framesClosed++;
        canvasFallback(video, fallback, false);
        telemetry.totalMs = this.now() - start;
        return {rgba, ready: Promise.resolve(telemetry)};
      }
      const ownedFrame = frame;
      const task = (async (): Promise<CaptureTelemetry> => {
        try {
          if (typeof ownedFrame.copyTo !== 'function') fallback = 'rgba-copy-unavailable';
          else {
            const copyStart = this.now();
            try {
              const layout = await ownedFrame.copyTo(rgba.data, {format: 'RGBA', colorSpace: 'srgb',
                rect: {x: rect!.x, y: rect!.y, width: rect!.width, height: rect!.height},
                layout: [{offset: 0, stride: canvas.width * 4}]});
              telemetry.copyToMs = this.now() - copyStart;
              if (!alive()) throw revoked();
              if (layout.length !== 1 || layout[0]!.offset !== 0 || layout[0]!.stride !== canvas.width * 4)
                fallback = 'rgba-layout-unavailable';
              else {
                // Opaque camera bytes survive putImageData exactly; transparent
                // data would introduce a premultiplication roundtrip on canvas.
                for (let offset = 3; offset < rgba.data.length; offset += 4)
                  if (rgba.data[offset] !== 255) { fallback = 'rgba-alpha-unavailable'; break; }
              }
            } catch (error) {
              telemetry.copyToMs = this.now() - copyStart;
              if (!alive()) throw revoked();
              if (error instanceof DOMException && error.name === 'AbortError') throw error;
              fallback = 'rgba-copy-failed';
            }
          }
          if (!alive()) throw revoked();
          if (fallback) canvasFallback(ownedFrame as VideoFrame, fallback, true);
          else {
            const writeStart = this.now();
            context.putImageData(rgba, 0, 0);
            telemetry.canvasWriteMs = this.now() - writeStart;
            telemetry.copiedBytes = rgba.data.byteLength;
            this.counts.copied++;
          }
          telemetry.totalMs = this.now() - start;
          return telemetry;
        } catch (error) {
          if (!alive()) this.counts.revoked++; else this.counts.failed++;
          throw error;
        } finally {
          ownedFrame.close(); this.counts.framesClosed++;
        }
      })();
      // Install the lock before exposing the result. Even synchronous fallback
      // holds it until its promise settles, preventing nested admissions.
      this.pending = task;
      const clear = (): void => { if (this.pending === task) this.pending = null; };
      void task.then(clear, clear);
      return {rgba, ready: task};
    } catch (error) {
      if (frame) { frame.close(); this.counts.framesClosed++; }
      this.counts.failed++;
      throw error;
    }
  }
  stop(): void { this.stopped = true; }
  async whenIdle(): Promise<void> { await this.pending?.catch(() => undefined); }
}
