/** Records the displayed canvas without additional renderer draws or audio. */
export interface RecorderMetadata {
  requested: boolean;
  status: 'disabled' | 'unsupported' | 'recording' | 'stopped' | 'failed';
  mimeType: string | null;
  startedAtMs: number | null;
  stoppedAtMs: number | null;
  width: number;
  height: number;
  bytes: number;
  chunks: number;
  requestedCaptureFps: number;
  requestedVideoBitsPerSecond: number;
  maxBytes: number;
  reason: string | null;
  audio: false;
  source: 'displayed-ar-canvas';
  timeAnchor: string;
  overhead: string;
}

interface OutputStream {getTracks(): {stop(): void}[];}
interface RecorderHandle {
  state: string;
  mimeType: string;
  ondataavailable: ((event: {data: Blob}) => void) | null;
  onstop: (() => void) | null;
  onerror: ((event: {error?: {message?: string}}) => void) | null;
  start(timeslice: number): void;
  stop(): void;
}
export interface RecordingEnvironment {
  supported: boolean;
  isTypeSupported(type: string): boolean;
  capture(canvas: HTMLCanvasElement, fps: number): OutputStream;
  create(stream: OutputStream, options: {mimeType?: string; videoBitsPerSecond: number}): RecorderHandle;
  now(): number;
}
const defaultEnvironment = (): RecordingEnvironment => ({
  supported: typeof MediaRecorder !== 'undefined' && typeof HTMLCanvasElement !== 'undefined'
    && typeof HTMLCanvasElement.prototype.captureStream === 'function',
  isTypeSupported: type => MediaRecorder.isTypeSupported(type),
  capture: (canvas, fps) => canvas.captureStream(fps),
  create: (stream, options) => new MediaRecorder(stream as MediaStream, options) as unknown as RecorderHandle,
  now: () => performance.now(),
});
const TYPES = ['video/webm;codecs=vp8', 'video/mp4;codecs=avc1.42E01E', 'video/mp4', 'video/webm'];
const reasonFor = (error: unknown): string => error instanceof Error ? error.message : String(error);

export class ContinuousCanvasRecorder {
  private readonly environment: RecordingEnvironment;
  private stream: OutputStream | null = null;
  private recorder: RecorderHandle | null = null;
  private chunks: Blob[] = [];
  private result: Promise<Blob | null> | null = null;
  private resolveResult: ((blob: Blob | null) => void) | null = null;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private settled = false;
  private issueReported = false;
  private readonly metadata: RecorderMetadata;
  constructor(canvas: HTMLCanvasElement, requested: boolean, options: {
    environment?: RecordingEnvironment; maxBytes?: number; onIssue?: (reason: string) => void;
  } = {}) {
    this.environment = options.environment ?? defaultEnvironment();
    this.onIssue = options.onIssue;
    this.metadata = {requested, status: requested ? 'unsupported' : 'disabled', mimeType: null,
      startedAtMs: null, stoppedAtMs: null, width: canvas.width, height: canvas.height,
      bytes: 0, chunks: 0, requestedCaptureFps: 30, requestedVideoBitsPerSecond: 2_000_000,
      maxBytes: options.maxBytes ?? 128 * 1024 * 1024, reason: null, audio: false,
      source: 'displayed-ar-canvas',
      timeAnchor: 'startedAtMs marks MediaRecorder.start() returning on the page performance clock. Video encoder buffering can offset the first encoded frame; video-to-telemetry alignment is approximate.',
      overhead: 'Canvas capture, video encoding and in-memory chunks add load throughout this run. Recorded FPS is not completed AR update FPS. Repeat with video off to isolate recording overhead.'};
    if (!requested) return;
    if (!this.environment.supported) {
      this.metadata.reason = 'This browser cannot record the AR canvas. Timing measurements remain available.';
      return;
    }
    try {
      if (canvas.width <= 0 || canvas.height <= 0) throw new Error('The AR canvas has no completed image.');
      const mimeType = TYPES.find(type => this.environment.isTypeSupported(type));
      this.stream = this.environment.capture(canvas, this.metadata.requestedCaptureFps);
      this.recorder = this.environment.create(this.stream, {videoBitsPerSecond: this.metadata.requestedVideoBitsPerSecond,
        ...(mimeType ? {mimeType} : {})});
      this.recorder.ondataavailable = event => {
        if (this.settled || !event.data.size) return;
        if (this.metadata.bytes + event.data.size > this.metadata.maxBytes) {
          this.fail('The local video memory limit was reached. The recording is partial.');
          return;
        }
        this.chunks.push(event.data); this.metadata.bytes += event.data.size; this.metadata.chunks++;
      };
      this.recorder.onerror = event => this.fail(event.error?.message ?? 'The video encoder failed.');
      this.recorder.onstop = () => {
        if (this.metadata.status === 'recording' && !this.result) {
          this.fail('The video encoder stopped before the comparison finished.');
        }
        this.finish();
      };
      this.recorder.start(1000);
      this.metadata.status = 'recording';
      this.metadata.startedAtMs = this.environment.now();
      this.metadata.mimeType = this.recorder.mimeType || mimeType || 'video/webm';
    } catch (error) {
      this.metadata.status = 'unsupported'; this.metadata.reason = reasonFor(error);
      if (this.recorder) {
        this.recorder.ondataavailable = this.recorder.onstop = this.recorder.onerror = null;
        try {if (this.recorder.state !== 'inactive') this.recorder.stop();} catch { /* Release tracks below. */ }
      }
      this.releaseTracks(); this.recorder = null;
    }
  }
  private readonly onIssue: ((reason: string) => void) | undefined;
  snapshot(): RecorderMetadata { return {...this.metadata}; }
  stop(atMs = this.environment.now()): Promise<Blob | null> {
    if (this.result) return this.result;
    this.metadata.stoppedAtMs = atMs;
    this.result = new Promise(resolve => {this.resolveResult = resolve;});
    if (!this.recorder || this.recorder.state === 'inactive') {this.finish(); return this.result;}
    // Do not stop stream tracks until the encoder has delivered its final chunk.
    this.stopTimer = setTimeout(() => {
      this.metadata.status = 'failed'; this.metadata.reason = 'The video encoder did not finish within 8 seconds; retained video may be partial.';
      this.finish();
    }, 8000);
    try {this.recorder.stop();}
    catch (error) {this.metadata.status = 'failed'; this.metadata.reason = reasonFor(error); this.finish();}
    return this.result;
  }
  private fail(reason: string): void {
    if (this.settled) return;
    this.metadata.status = 'failed'; this.metadata.reason = reason;
    void this.stop();
    if (!this.issueReported) {this.issueReported = true; this.onIssue?.(reason);}
  }
  private releaseTracks(): void {
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
  }
  private finish(): void {
    if (this.settled) return;
    this.settled = true;
    if (this.stopTimer !== null) clearTimeout(this.stopTimer);
    if (this.metadata.status === 'recording') this.metadata.status = 'stopped';
    if (this.metadata.requested && this.metadata.startedAtMs !== null && this.chunks.length === 0) {
      this.metadata.status = 'failed'; this.metadata.reason ??= 'The video encoder produced no video chunks. Timings were retained.';
    }
    this.metadata.stoppedAtMs ??= this.environment.now();
    const blob = this.chunks.length ? new Blob(this.chunks, {type: this.metadata.mimeType ?? 'video/webm'}) : null;
    this.chunks = [];
    this.releaseTracks();
    if (this.recorder) this.recorder.ondataavailable = this.recorder.onstop = this.recorder.onerror = null;
    this.resolveResult?.(blob); this.resolveResult = null;
  }
}
