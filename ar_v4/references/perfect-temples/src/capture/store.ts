import { validateDetection } from '../runtime/protocol.ts';
import type { Detection } from '../runtime/protocol.ts';

export const CAPTURE_LIMITS = Object.freeze({
  sampleIntervalMs: 300,
  maxFrames: 96,
  maxCompressedBytes: 24 * 1024 * 1024,
  maxDurationMs: 30_000,
  jpegQuality: 0.94,
});

export type DeepReadonly<T> = T extends object
  ? { readonly [Key in keyof T]: DeepReadonly<T[Key]> }
  : T;

export type CaptureState = 'idle' | 'recording' | 'finishing' | 'finished';
export type CaptureStopReason = 'manual' | 'duration' | 'frame-limit' | 'byte-limit';
export type CaptureCanvas = Pick<HTMLCanvasElement, 'width' | 'height' | 'toBlob'>;
export type CaptureEncoder = (canvas: CaptureCanvas, quality: number) => Promise<Blob>;

export interface CaptureFrame<Metadata> {
  readonly index: number;
  /** performance.now() when these exact, unmirrored pixels were frozen. */
  readonly capturedAt: number;
  readonly relativeMs: number;
  readonly width: number;
  readonly height: number;
  readonly jpeg: Blob;
  readonly detection: DeepReadonly<Detection>;
  readonly metadata: DeepReadonly<Metadata>;
  readonly yawDegrees: number | null;
}

export interface CaptureSnapshot<Metadata> {
  readonly state: CaptureState;
  readonly frames: readonly CaptureFrame<Metadata>[];
  readonly compressedBytes: number;
  readonly startedAt: number | null;
  readonly stopReason: CaptureStopReason | null;
}

interface PendingEncoding {
  generation: number;
  done: Promise<void>;
  resolveDone(): void;
}

/** Copy finite JSON data without silently discarding unsupported values. */
function immutableJson<T>(value: T): DeepReadonly<T> {
  const ancestors = new Set<object>();
  const visit = (input: unknown): unknown => {
    if (input === null || typeof input === 'string' || typeof input === 'boolean') return input;
    if (typeof input === 'number' && Number.isFinite(input)) return input;
    if (typeof input !== 'object' || input === null) throw new Error('Capture metadata must contain finite JSON values.');
    if (ancestors.has(input)) throw new Error('Capture metadata must not contain cycles.');
    const prototype = Object.getPrototypeOf(input);
    if (!Array.isArray(input) && prototype !== Object.prototype && prototype !== null) {
      throw new Error('Capture metadata must use plain JSON objects and arrays.');
    }
    if (Object.getOwnPropertySymbols(input).length) throw new Error('Capture metadata must not contain symbol properties.');
    ancestors.add(input);
    const result = Array.isArray(input)
      ? Array.from(input, visit)
      : Object.fromEntries(Object.entries(input).map(([key, item]) => [key, visit(item)]));
    ancestors.delete(input);
    return Object.freeze(result);
  };
  return visit(value) as DeepReadonly<T>;
}

function encodeJpeg(canvas: CaptureCanvas, quality: number): Promise<Blob> {
  // toBlob snapshots the supplied canvas at invocation; never reread a video.
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (blob) resolve(blob);
      else reject(new Error('The captured frame could not be encoded.'));
    }, 'image/jpeg', quality);
  });
}

async function jpegDataUrl(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 32_768) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32_768)));
  }
  return `data:image/jpeg;base64,${btoa(chunks.join(''))}`;
}

/** Memory only. A caller must explicitly invoke exportJson and choose how to save it. */
export class CaptureStore<Metadata> {
  private readonly encode: CaptureEncoder;
  private readonly now: () => number;
  private generation = 0;
  private state: CaptureState = 'idle';
  private frames: CaptureFrame<Metadata>[] = [];
  private compressedBytes = 0;
  private startedAt: number | null = null;
  private stopReason: CaptureStopReason | null = null;
  private lastSampleAt = -Infinity;
  private pending: PendingEncoding | null = null;
  private deadline: ReturnType<typeof setTimeout> | null = null;

  constructor(options: { encode?: CaptureEncoder; now?: () => number } = {}) {
    this.encode = options.encode ?? encodeJpeg;
    this.now = options.now ?? (() => performance.now());
  }

  get snapshot(): CaptureSnapshot<Metadata> {
    return Object.freeze({
      state: this.state,
      frames: Object.freeze(this.frames.slice()),
      compressedBytes: this.compressedBytes,
      startedAt: this.startedAt,
      stopReason: this.stopReason,
    });
  }

  /** startedAt uses the same monotonic clock as capturedAt, not Date.now(). */
  start(startedAt = this.now()): void {
    if (!Number.isFinite(startedAt) || startedAt < 0) throw new Error('Capture start time must be finite and nonnegative.');
    const now = this.now();
    if (!Number.isFinite(now) || startedAt > now) throw new Error('Capture start time must not be in the future.');
    this.reset();
    this.startedAt = startedAt;
    this.state = 'recording';
    const remaining = CAPTURE_LIMITS.maxDurationMs - (now - startedAt);
    if (remaining <= 0) this.stopAdmission('duration');
    else {
      const generation = this.generation;
      this.deadline = setTimeout(() => {
        if (generation === this.generation) this.stopAdmission('duration');
      }, remaining);
    }
  }

  /** Discard immediately. A non-cancellable old JPEG encode retains its single-job lock. */
  reset(): void {
    this.generation++;
    this.clearDeadline();
    this.state = 'idle';
    this.frames = [];
    this.compressedBytes = 0;
    this.startedAt = null;
    this.stopReason = null;
    this.lastSampleAt = -Infinity;
  }

  /** Stop admission and retain the frame already being encoded, if any. */
  async finish(): Promise<void> {
    this.stopAdmission('manual');
    const pending = this.pending;
    if (pending?.generation === this.generation) await pending.done;
  }

  /**
   * Call while the supplied canvas still contains the exact detection input.
   * Encoder invocation and immutable metadata copies happen before the first await.
   * An injected encoder must also snapshot pixels synchronously at invocation.
   */
  async capture(
    canvas: CaptureCanvas,
    detection: Detection,
    details: { capturedAt: number; metadata: Metadata; yawDegrees?: number | null },
  ): Promise<boolean> {
    if (this.state !== 'recording') return false;
    const { width, height } = canvas;
    if (!Number.isSafeInteger(width) || width <= 0 || !Number.isSafeInteger(height) || height <= 0) {
      throw new Error('Capture dimensions must be positive integers.');
    }
    const { capturedAt } = details;
    if (!Number.isFinite(capturedAt) || capturedAt < 0) throw new Error('Capture timestamp must be finite and nonnegative.');
    if (capturedAt < this.startedAt!) return false; // Inference begun before the user started recording.
    if (capturedAt - this.startedAt! >= CAPTURE_LIMITS.maxDurationMs
      || this.now() - this.startedAt! >= CAPTURE_LIMITS.maxDurationMs) {
      this.stopAdmission('duration');
      return false;
    }
    if (this.pending || capturedAt - this.lastSampleAt < CAPTURE_LIMITS.sampleIntervalMs) return false;
    const copiedDetection = immutableJson(validateDetection(detection));
    const copiedMetadata = immutableJson(details.metadata);
    const yawDegrees = details.yawDegrees ?? null;
    if (yawDegrees !== null && !Number.isFinite(yawDegrees)) throw new Error('Capture yaw must be finite or null.');
    const generation = this.generation;
    const relativeMs = capturedAt - this.startedAt!;
    let resolveDone!: () => void;
    const pending: PendingEncoding = {
      generation,
      done: new Promise(resolve => { resolveDone = resolve; }),
      resolveDone: () => resolveDone(),
    };
    this.pending = pending;
    this.lastSampleAt = capturedAt;
    try {
      const jpeg = await this.encode(canvas, CAPTURE_LIMITS.jpegQuality);
      if (generation !== this.generation) return false;
      if (!(jpeg instanceof Blob) || jpeg.type !== 'image/jpeg' || jpeg.size <= 0) {
        throw new Error('The capture encoder must return a nonempty JPEG Blob.');
      }
      if (this.compressedBytes + jpeg.size > CAPTURE_LIMITS.maxCompressedBytes) {
        this.stopAdmission('byte-limit');
        return false;
      }
      this.frames.push(Object.freeze({
        index: this.frames.length, capturedAt, relativeMs, width, height,
        jpeg, detection: copiedDetection, metadata: copiedMetadata, yawDegrees,
      }));
      this.compressedBytes += jpeg.size;
      if (this.frames.length >= CAPTURE_LIMITS.maxFrames) this.stopAdmission('frame-limit');
      else if (this.compressedBytes >= CAPTURE_LIMITS.maxCompressedBytes) this.stopAdmission('byte-limit');
      return true;
    } catch (error) {
      if (generation !== this.generation) return false;
      throw error;
    } finally {
      this.settleEncoding(pending);
    }
  }

  /** Explicit export only; this method never downloads, uploads, or persists data. */
  async exportJson<Header>(header: Header): Promise<string> {
    if (this.state !== 'finished') throw new Error('Finish the capture before exporting it.');
    const copiedHeader = immutableJson(header);
    const generation = this.generation;
    const snapshot = this.snapshot;
    const frames = [];
    for (const frame of snapshot.frames) {
      const { jpeg, ...data } = frame;
      const encoded = await jpegDataUrl(jpeg);
      if (generation !== this.generation) throw new DOMException('Capture export was discarded.', 'AbortError');
      frames.push({ ...data, jpegDataUrl: encoded });
    }
    return JSON.stringify({
      schemaVersion: 1,
      projectId: 'ar_v4',
      limits: CAPTURE_LIMITS,
      header: copiedHeader,
      startedAt: snapshot.startedAt,
      stopReason: snapshot.stopReason,
      compressedBytes: snapshot.compressedBytes,
      frames,
    });
  }

  private stopAdmission(reason: CaptureStopReason): void {
    if (this.state !== 'recording') return;
    this.clearDeadline();
    this.stopReason = reason;
    this.state = this.pending?.generation === this.generation ? 'finishing' : 'finished';
  }

  private settleEncoding(pending: PendingEncoding): void {
    if (this.pending === pending) this.pending = null;
    if (pending.generation === this.generation && this.state === 'finishing') this.state = 'finished';
    pending.resolveDone();
  }

  private clearDeadline(): void {
    if (this.deadline !== null) clearTimeout(this.deadline);
    this.deadline = null;
  }
}
