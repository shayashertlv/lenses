import { validateDetection } from './protocol.ts';
import type { Detection, DetectorRequest, DetectorResponse } from './protocol.ts';

export type { Detection, Landmark } from './protocol.ts';

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
}

function cancelled(): DOMException {
  return new DOMException('Face detector startup was cancelled.', 'AbortError');
}

/** Only a task's explicit initialization reply permits the CPU retry. */
class InitializationReplyError extends Error {}

export class DetectorClient {
  private worker: Worker | null = null;
  private state: State = 'new';
  private pending: Pending | null = null;
  private nextId = 1;
  private lastTimestampMs = -Infinity;

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
          }, INITIALIZE_TIMEOUT_MS, signal);
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
    try {
      if (this.state !== 'ready') throw new Error('The face detector is not ready.');
      if (this.pending) throw new Error('A face detection is already in progress.');
      if (!Number.isFinite(timestampMs) || timestampMs < 0 || timestampMs <= this.lastTimestampMs) {
        throw new Error('Frame timestamps must be finite, nonnegative, and strictly increasing.');
      }
      if (image.width <= 0 || image.height <= 0) throw new Error('The camera frame is empty.');
      this.lastTimestampMs = timestampMs;
      const result = await this.request({ type: 'detect', id: this.nextId++, image, timestampMs }, DETECT_TIMEOUT_MS);
      return result!;
    } finally {
      image.close();
    }
  }

  close(): void {
    this.fail(new Error('The face detector was closed.'));
  }

  private createWorker(): void {
    const worker = new Worker(new URL('./detector.worker.ts', import.meta.url), { type: 'module' });
    this.worker = worker;
    worker.onmessage = (event: MessageEvent<DetectorResponse>) => {
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
      };
      signal?.addEventListener('abort', onAbort, { once: true });
      if (signal?.aborted) {
        onAbort();
        return;
      }
      try {
        this.worker!.postMessage(message, message.type === 'detect' ? [message.image] : []);
      } catch (error) {
        this.fail(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  private handleMessage(message: DetectorResponse): void {
    const pending = this.pending;
    if (!pending || !message || message.id !== pending.id) return;
    if (message.type === 'error') {
      const ErrorType = pending.kind === 'initialize' ? InitializationReplyError : Error;
      this.settle(new ErrorType(message.message || 'Face detection failed.'));
      return;
    }
    if (pending.kind === 'initialize' && message.type === 'ready') {
      this.settle();
    } else if (pending.kind === 'detect' && message.type === 'result') {
      try {
        this.settle(undefined, validateDetection(message.detection));
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
