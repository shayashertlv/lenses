import { validateDetection } from '../../references/perfect-temples/src/runtime/protocol.ts';
import type { Detection, DetectorRequest } from '../../references/perfect-temples/src/runtime/protocol.ts';
import {validatedWorkerTiming} from './face-timing.ts';
import type {FaceDelegate, FaceStageTiming, TimedDetectorRequest, TimedDetectorResponse} from './face-timing.ts';

export type { Detection, Landmark } from '../../references/perfect-temples/src/runtime/protocol.ts';
export type {FaceStageTiming} from './face-timing.ts';

export interface FaceWorkerPort {
  onmessage: ((event: MessageEvent<TimedDetectorResponse>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  onmessageerror: ((event: MessageEvent) => void) | null;
  postMessage(message: TimedDetectorRequest, transfer?: Transferable[]): void;
  terminate(): void;
}
export interface DetectorOptions {
  /** Lifecycle/timing tests only; production defaults remain unchanged. */
  createWorker?: () => FaceWorkerPort;
  now?: () => number;
  sessionNonce?: string;
  initializeTimeoutMs?: number;
  detectTimeoutMs?: number;
}

const INITIALIZE_TIMEOUT_MS = 45_000;
const DETECT_TIMEOUT_MS = 15_000;

type State = 'new' | 'initializing' | 'ready' | 'closed';
interface Pending {
  id: number;
  kind: 'initialize' | 'detect';
  resolve(value: Detection | undefined): void;
  reject(error: Error): void;
  timer: ReturnType<typeof setTimeout>;
  removeAbortListener(): void;
  sentAtMs: number;
  postMessageMs: number;
  image?: {width: number; height: number; timestampMs: number};
}

function cancelled(): DOMException {
  return new DOMException('Face detector startup was cancelled.', 'AbortError');
}

/** Only a task's explicit initialization reply permits the CPU retry. */
class InitializationReplyError extends Error {}

export class DetectorClient {
  private worker: FaceWorkerPort | null = null;
  private state: State = 'new';
  private pending: Pending | null = null;
  private nextId = 1;
  private lastTimestampMs = -Infinity;
  private readonly options: DetectorOptions;
  private readonly now: () => number;
  private readonly sessionNonce: string;
  private activeDelegate: FaceDelegate | null = null;
  private completedTiming: FaceStageTiming | null = null;

  constructor(options: DetectorOptions = {}) {
    this.options = options;
    this.now = options.now ?? (() => performance.now());
    this.sessionNonce = options.sessionNonce ?? crypto.randomUUID();
    if (!/^[A-Za-z0-9_-]{8,128}$/.test(this.sessionNonce)) throw new Error('Invalid face detector session nonce.');
    if (![options.initializeTimeoutMs ?? INITIALIZE_TIMEOUT_MS, options.detectTimeoutMs ?? DETECT_TIMEOUT_MS]
      .every(value => Number.isFinite(value) && value > 0)) throw new Error('Face detector deadlines must be positive and finite.');
  }

  get delegate(): FaceDelegate | null { return this.activeDelegate; }
  get lastTiming(): FaceStageTiming | null {
    return this.state === 'closed' || this.pending || !this.completedTiming ? null : {...this.completedTiming};
  }

  async initialize(signal: AbortSignal): Promise<void> {
    if (signal.aborted) throw cancelled();
    if (this.state === 'ready') return;
    if (this.state !== 'new') throw new Error('The face detector is already starting or closed.');
    this.state = 'initializing';
    try {
      for (const delegate of ['GPU', 'CPU'] as const) {
        if (signal.aborted) throw cancelled();
        this.createWorker();
        try {
          await this.request({
            type: 'initialize', id: this.nextId++, delegate,
            modelUrl: new URL('/models/face_landmarker.task', window.location.href).href,
            wasmRoot: new URL('/mediapipe/', window.location.href).href,
          }, this.options.initializeTimeoutMs ?? INITIALIZE_TIMEOUT_MS, signal);
        } catch (error) {
          if (signal.aborted) throw cancelled();
          if (delegate !== 'GPU' || !(error instanceof InitializationReplyError)
            || this.state !== 'initializing') throw error;
          // A fresh worker also gives the ESM WASM loader a fresh module cache.
          this.releaseWorker();
          continue;
        }
        if (signal.aborted) throw cancelled();
        if (!this.worker) throw new Error('The face detector was closed during startup.');
        this.activeDelegate = delegate;
        this.state = 'ready';
        return;
      }
    } catch (error) {
      this.fail(error instanceof Error ? error : new Error(String(error)));
      throw error;
    }
  }

  /** Takes ownership of image immediately, including rejected requests. */
  async detect(image: ImageBitmap, timestampMs: number): Promise<Detection> {
    const callStartedAtMs = this.now();
    let ownedRequestId: number | null = null;
    try {
      if (this.state !== 'ready') throw new Error('The face detector is not ready.');
      if (this.pending) throw new Error('A face detection is already in progress.');
      if (!Number.isFinite(timestampMs) || timestampMs < 0 || timestampMs <= this.lastTimestampMs) {
        throw new Error('Frame timestamps must be finite, nonnegative, and strictly increasing.');
      }
      if (image.width <= 0 || image.height <= 0) throw new Error('The camera frame is empty.');
      this.lastTimestampMs = timestampMs;
      this.completedTiming = null;
      ownedRequestId = this.nextId++;
      const result = await this.request({ type: 'detect', id: ownedRequestId, image, timestampMs }, this.options.detectTimeoutMs ?? DETECT_TIMEOUT_MS);
      return result!;
    } finally {
      image.close();
      if (ownedRequestId !== null && this.completedTiming?.requestId === ownedRequestId && this.state !== 'closed') {
        this.completedTiming.detectCallMs = this.now() - callStartedAtMs;
      }
    }
  }

  close(): void {
    this.fail(new Error('The face detector was closed.'));
  }

  private createWorker(): void {
    const worker = this.options.createWorker?.() ?? new Worker(new URL('./face-detector.worker.ts', import.meta.url), { type: 'module' });
    this.worker = worker;
    worker.onmessage = (event: MessageEvent<TimedDetectorResponse>) => {
      if (this.worker === worker) this.handleMessage(event.data);
    };
    worker.onerror = (event: ErrorEvent) => {
      event.preventDefault();
      if (this.worker === worker) this.fail(new Error(event.message || 'The face tracking worker failed.'));
    };
    worker.onmessageerror = () => {
      if (this.worker === worker) this.fail(new Error('The face tracking worker sent an unreadable message.'));
    };
  }

  private request(message: DetectorRequest, timeoutMs: number, signal?: AbortSignal): Promise<Detection | undefined> {
    return new Promise((resolve, reject) => {
      const onAbort = () => this.fail(cancelled());
      const timer = setTimeout(() => this.fail(new Error(
        message.type === 'initialize' ? 'Face detector startup timed out.' : 'Face detection timed out.',
      )), timeoutMs);
      this.pending = {
        id: message.id, kind: message.type, resolve, reject, timer,
        removeAbortListener: () => signal?.removeEventListener('abort', onAbort),
        sentAtMs: 0, postMessageMs: 0,
        ...(message.type === 'detect' ? {image: {width: message.image.width, height: message.image.height, timestampMs: message.timestampMs}} : {}),
      };
      signal?.addEventListener('abort', onAbort, { once: true });
      if (signal?.aborted) {
        onAbort();
        return;
      }
      try {
        const pending = this.pending;
        pending.sentAtMs = this.now();
        this.worker!.postMessage({...message, sessionNonce: this.sessionNonce}, message.type === 'detect' ? [message.image] : []);
        pending.postMessageMs = this.now() - pending.sentAtMs;
      } catch (error) {
        this.fail(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  private handleMessage(message: TimedDetectorResponse): void {
    const receivedAtMs = this.now();
    const pending = this.pending;
    if (!pending || !message || message.id !== pending.id || message.sessionNonce !== this.sessionNonce) return;
    if (message.type === 'error') {
      const ErrorType = pending.kind === 'initialize' ? InitializationReplyError : Error;
      this.settle(new ErrorType(message.message || 'Face detection failed.'));
      return;
    }
    if (pending.kind === 'initialize' && message.type === 'ready') {
      this.settle();
    } else if (pending.kind === 'detect' && message.type === 'result') {
      try {
        const validationStartedAtMs = this.now();
        const detection = validateDetection(message.detection);
        const validatedAtMs = this.now();
        if (pending.image && this.activeDelegate) {
          const result = validatedWorkerTiming(message.timing, {requestId: pending.id, ...pending.image,
            delegate: this.activeDelegate, detection});
          const worker = result.timing, roundTripMs = receivedAtMs - pending.sentAtMs;
          const residualMs = worker ? roundTripMs - worker.elapsedMs : null;
          this.completedTiming = {requestId: pending.id, ...pending.image, sessionNonce: this.sessionNonce,
            delegate: this.activeDelegate, inferenceMs: detection.inferenceMs, workerTimingStatus: result.status,
            workerRequestChecksMs: worker?.requestChecksMs ?? null, workerExtractionMs: worker?.extractionMs ?? null,
            workerValidationMs: worker?.validationMs ?? null, workerElapsedMs: worker?.elapsedMs ?? null,
            postMessageMs: pending.postMessageMs, roundTripMs, clientValidationMs: validatedAtMs - validationStartedAtMs,
            clientTotalMs: validatedAtMs - pending.sentAtMs, detectCallMs: validatedAtMs - pending.sentAtMs,
            // Precision/clock anomalies are unavailable rather than silently clamped to zero.
            transportAndSchedulingMs: residualMs !== null && residualMs >= 0 ? residualMs : null};
        }
        this.settle(undefined, detection);
      } catch (error) {
        this.settle(error instanceof Error ? error : new Error(String(error)));
      }
    } else {
      this.fail(new Error('The face tracking worker returned an unexpected response.'));
    }
  }

  private settle(error?: Error, result?: Detection): void {
    const pending = this.pending;
    if (!pending) return;
    this.pending = null;
    clearTimeout(pending.timer);
    pending.removeAbortListener();
    if (error) pending.reject(error);
    else pending.resolve(result);
  }

  private fail(error: Error): void {
    this.state = 'closed';
    this.completedTiming = null; this.activeDelegate = null;
    this.settle(error);
    this.releaseWorker();
  }

  private releaseWorker(): void {
    const worker = this.worker;
    this.worker = null;
    if (worker) {
      worker.onmessage = null;
      worker.onerror = null;
      worker.onmessageerror = null;
      worker.terminate();
    }
  }
}
