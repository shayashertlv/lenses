/** Hair segmenter client: one module worker, one owned image at a time, IMAGE semantics (no latest-mask cache or
 *  replay of stale output). The delegate is chosen by the page from the graphics backend probe; a GPU failure at
 *  startup is retried on the CPU by the page. */
import {getHairModel} from './models.ts';
import type {HairModel, HairModelId} from './models.ts';
import {assertDimensions, assertSequence, assertSourceHash, hashHairMask, isRecord, validateHairOutput, validateHairReady, validNonce} from './protocol.ts';
import type {HairDelegate, HairExpectedPair, HairSegmentationResult, HairWorkerRequest} from './protocol.ts';

export type {HairSegmentationResult} from './protocol.ts';
export interface HairWorkerPort {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  onmessageerror: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: HairWorkerRequest, transfer?: Transferable[]): void;
  terminate(): void;
}
export interface HairClientOptions {
  /** Worker seam for lifecycle tests; the live default is a separate module worker. */
  createWorker?: () => HairWorkerPort;
  initializeTimeoutMs?: number;
  segmentTimeoutMs?: number;
  sessionNonce?: string;
  delegate?: HairDelegate;
}
interface Pending {
  requestId: number;
  kind: 'initialize' | 'segment';
  expected?: HairExpectedPair;
  validating: boolean;
  timer: ReturnType<typeof setTimeout>;
  resolve(result: HairSegmentationResult | undefined): void;
  reject(error: Error): void;
}
type State = 'new' | 'initializing' | 'ready' | 'closed';
const errorFrom = (error: unknown): Error => error instanceof Error ? error : new Error(String(error));
const aborted = (): DOMException => new DOMException('The hair session was cancelled.', 'AbortError');

export class HairClient {
  readonly model: HairModel;
  readonly delegate: HairDelegate;
  private readonly createWorker: () => HairWorkerPort;
  private readonly initializeTimeoutMs: number;
  private readonly segmentTimeoutMs: number;
  private readonly sessionNonce: string;
  private worker: HairWorkerPort | null = null;
  private state: State = 'new';
  private pending: Pending | null = null;
  private nextId = 1;
  private lastSequence = -1;
  private removeAbortListener: (() => void) | null = null;

  constructor(modelId: HairModelId, options: HairClientOptions = {}) {
    this.model = getHairModel(modelId);
    this.delegate = options.delegate ?? 'CPU';
    if (this.delegate !== 'CPU' && this.delegate !== 'GPU') throw new Error('Unknown hair inference delegate.');
    this.createWorker = options.createWorker ?? (() => new Worker(new URL('./worker.ts', import.meta.url), {type: 'module'}));
    this.initializeTimeoutMs = options.initializeTimeoutMs ?? 45_000;
    this.segmentTimeoutMs = options.segmentTimeoutMs ?? 15_000;
    this.sessionNonce = options.sessionNonce ?? crypto.randomUUID();
    if (!validNonce(this.sessionNonce)) throw new Error('Invalid hair session nonce.');
    if (![this.initializeTimeoutMs, this.segmentTimeoutMs].every(value => Number.isFinite(value) && value > 0)) {
      throw new Error('Hair worker deadlines must be positive and finite.');
    }
  }

  /** The signal owns this client's entire lifetime, including in-flight segmentation. */
  async initialize(signal: AbortSignal): Promise<void> {
    if (signal.aborted) { this.fail(aborted()); throw aborted(); }
    if (this.state === 'ready') return;
    if (this.state !== 'new') throw new Error('The hair client is already starting or closed.');
    this.state = 'initializing';
    const onAbort = (): void => this.fail(aborted());
    signal.addEventListener('abort', onAbort, {once: true});
    this.removeAbortListener = () => signal.removeEventListener('abort', onAbort);
    try {
      const worker = this.createWorker(); this.worker = worker;
      worker.onmessage = event => { if (this.worker === worker) void this.handleMessage(event.data); };
      worker.onerror = event => { event.preventDefault(); if (this.worker === worker) this.fail(new Error(event.message || 'The hair worker failed.')); };
      worker.onmessageerror = () => { if (this.worker === worker) this.fail(new Error('The hair worker sent an unreadable response.')); };
      if (signal.aborted) throw aborted();
      await this.request({type: 'initialize', sessionNonce: this.sessionNonce, requestId: this.nextId++, modelId: this.model.id,
        delegate: this.delegate}, this.initializeTimeoutMs);
      if (signal.aborted || !this.worker) throw aborted();
      this.state = 'ready';
    } catch (error) { this.fail(errorFrom(error)); throw error; }
  }

  /** Takes ownership immediately, including rejected calls and failed transfers. */
  async segment(image: ImageBitmap, sourceSHA256: string, sequence: number): Promise<HairSegmentationResult> {
    try {
      if (this.state !== 'ready') throw new Error('The hair worker is not ready.');
      if (this.pending) throw new Error('A hair segmentation already owns an image.');
      assertSourceHash(sourceSHA256); assertSequence(sequence); assertDimensions(image.width, image.height);
      if (sequence <= this.lastSequence) throw new Error('Hair frame sequences must increase within a session.');
      this.lastSequence = sequence;
      const expected: HairExpectedPair = {sourceSHA256, sequence, width: image.width, height: image.height, delegate: this.delegate};
      const result = await this.request({type: 'segment', sessionNonce: this.sessionNonce, requestId: this.nextId++, image, sourceSHA256, sequence},
        this.segmentTimeoutMs, expected);
      if (!result) throw new Error('The hair worker returned no segmentation.');
      return result;
    } finally { image.close(); }
  }

  close(): void { this.fail(new Error('The hair client was closed.')); }

  private request(message: HairWorkerRequest, timeoutMs: number, expected?: HairExpectedPair): Promise<HairSegmentationResult | undefined> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new Error(message.type === 'initialize' ? 'Hair model startup timed out.' : 'Hair segmentation timed out.')), timeoutMs);
      this.pending = {requestId: message.requestId, kind: message.type, expected, validating: false, timer, resolve, reject};
      try { this.worker!.postMessage(message, message.type === 'segment' ? [message.image] : []); }
      catch (error) { this.fail(errorFrom(error)); }
    });
  }

  private async handleMessage(message: unknown): Promise<void> {
    const pending = this.pending;
    if (!pending || !isRecord(message) || message.sessionNonce !== this.sessionNonce || message.requestId !== pending.requestId || pending.validating) return;
    try {
      if (message.type === 'error') throw new Error(typeof message.message === 'string' ? message.message : 'Hair inference failed.');
      if (pending.kind === 'initialize') {
        validateHairReady(message, this.model, this.delegate); this.settle(); return;
      }
      if (message.type !== 'result' || !pending.expected) throw new Error('The hair worker returned an unexpected response.');
      pending.validating = true;
      const output = validateHairOutput(message.output, this.model, pending.expected);
      const result: HairSegmentationResult = {...output, ...await hashHairMask(output)};
      if (this.pending !== pending || this.state === 'closed') return;
      this.settle(undefined, result);
    } catch (error) { if (this.pending === pending) this.fail(errorFrom(error)); }
  }

  private settle(error?: Error, output?: HairSegmentationResult): void {
    const pending = this.pending; if (!pending) return;
    this.pending = null; clearTimeout(pending.timer);
    if (error) pending.reject(error); else pending.resolve(output);
  }

  private fail(error: Error): void {
    this.state = 'closed'; this.settle(error); this.removeAbortListener?.(); this.removeAbortListener = null;
    const worker = this.worker; this.worker = null;
    if (worker) { worker.onmessage = null; worker.onerror = null; worker.onmessageerror = null; worker.terminate(); }
  }
}
