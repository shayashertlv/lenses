import {getHairModel} from '../../hair-live-preview/models.ts';
import type {HairModel, HairModelId} from '../../hair-live-preview/models.ts';
import {assertDimensions, assertSequence, assertSourceHash, hashHairMasks, isRecord, validateHairOutput, validateHairReady, validNonce} from '../../hair-live-preview/hair-protocol.ts';
import type {HairDelegate, HairOutputMode} from '../../hair-live-preview/hair-protocol.ts';
import {CATEGORY_EXTRACTION_PROTOCOL, assertCategoryExtractionMode, validateCategoryExtractionMetrics, validateHairWorkerTiming} from './protocol.ts';
import type {HairExpectedPair, HairSegmentationResult, HairWorkerRequest, CategoryExtractionMode} from './protocol.ts';
import type {HairRequestTiming, HairTimingObserver} from './delivery.ts';

export type {HairSegmentationResult, CategoryExtractionMode, CategoryExtractionMetrics} from './protocol.ts';
export type {HairRequestTiming, HairTimingObserver} from './delivery.ts';
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
  outputMode?: HairOutputMode;
  /** Dependency seam for deterministic hash-lifetime tests. Production uses hashHairMasks. */
  hashMasks?: typeof hashHairMasks;
}
export interface HairSegmentDelivery {
  /** Resolves only after original pair/model/shape/content validation and hashes complete. */
  result: Promise<HairSegmentationResult>;
  /** Admission gate, not permission to publish. Default waits for the validated result. */
  workerReleased: Promise<void>;
  timing(): Readonly<HairRequestTiming>;
}
interface DeliveryState {
  trace: HairRequestTiming;
  observer?: HairTimingObserver;
  terminalNotified: boolean;
  released: boolean;
  release(): void;
  rejectRelease(error: Error): void;
}
interface Pending {
  requestId: number;
  kind: 'initialize' | 'segment';
  expected?: HairExpectedPair;
  delivery?: DeliveryState;
  validating: boolean;
  timer: ReturnType<typeof setTimeout>;
  resolve(result: HairSegmentationResult | undefined): void;
  reject(error: Error): void;
}
type State = 'new' | 'initializing' | 'ready' | 'closed';
const errorFrom = (error: unknown): Error => error instanceof Error ? error : new Error(String(error));
const aborted = (): DOMException => new DOMException('The hair preview session was cancelled.', 'AbortError');
const snapshot = (delivery: DeliveryState): Readonly<HairRequestTiming> => Object.freeze({...delivery.trace});
function notify(delivery: DeliveryState): void {
  if (delivery.terminalNotified) return;
  if (delivery.trace.outcome !== 'pending') delivery.terminalNotified = true;
  // Diagnostics cannot fail a valid image or bypass ownership checks.
  try {delivery.observer?.(snapshot(delivery));} catch { /* Diagnostic consumers do not own inference failure. */ }
}

/** IMAGE semantics with one worker computation. G owns one request through hashing;
 * explicit early release may own two requests, with the prior result validating.
 * No output, source, hash, sequence or session validation is skipped. */
export class HairClient {
  readonly model: HairModel;
  readonly delegate: HairDelegate;
  readonly outputMode: HairOutputMode;
  private readonly createWorker: () => HairWorkerPort;
  private readonly initializeTimeoutMs: number;
  private readonly segmentTimeoutMs: number;
  private readonly sessionNonce: string;
  private readonly hashMasks: typeof hashHairMasks;
  private worker: HairWorkerPort | null = null;
  private state: State = 'new';
  private readonly pending = new Map<number, Pending>();
  private workerRequestId: number | null = null;
  private nextId = 1;
  private lastSequence = -1;
  private removeAbortListener: (() => void) | null = null;

  constructor(modelId: HairModelId, options: HairClientOptions = {}) {
    this.model = getHairModel(modelId);
    this.delegate = options.delegate ?? 'CPU';
    if (this.delegate !== 'CPU' && this.delegate !== 'GPU') throw new Error('Unknown hair inference delegate.');
    this.outputMode = options.outputMode ?? 'full';
    if (this.outputMode !== 'full' && this.outputMode !== 'category-only') throw new Error('Unknown hair output mode.');
    this.createWorker = options.createWorker ?? (() => new Worker(new URL('./worker.ts', import.meta.url), {type: 'module'}));
    this.initializeTimeoutMs = options.initializeTimeoutMs ?? 45_000;
    this.segmentTimeoutMs = options.segmentTimeoutMs ?? 15_000;
    this.sessionNonce = options.sessionNonce ?? crypto.randomUUID();
    this.hashMasks = options.hashMasks ?? hashHairMasks;
    if (!validNonce(this.sessionNonce)) throw new Error('Invalid hair session nonce.');
    if (![this.initializeTimeoutMs, this.segmentTimeoutMs].every(value => Number.isFinite(value) && value > 0))
      throw new Error('Hair worker deadlines must be positive and finite.');
  }

  /** The signal owns this client's lifetime, including validation and hashing. */
  async initialize(signal: AbortSignal): Promise<void> {
    if (signal.aborted) {this.fail(aborted(), 'cancelled', 'session-aborted'); throw aborted();}
    if (this.state === 'ready') return;
    if (this.state !== 'new') throw new Error('The hair client is already starting or closed.');
    this.state = 'initializing';
    const onAbort = (): void => this.fail(aborted(), 'cancelled', 'session-aborted');
    signal.addEventListener('abort', onAbort, {once: true});
    this.removeAbortListener = () => signal.removeEventListener('abort', onAbort);
    try {
      const worker = this.createWorker(); this.worker = worker;
      worker.onmessage = event => {if (this.worker === worker) void this.handleMessage(event.data);};
      worker.onerror = event => {event.preventDefault(); if (this.worker === worker) this.fail(new Error(event.message || 'The hair worker failed.'), 'failed', 'worker-error');};
      worker.onmessageerror = () => {if (this.worker === worker) this.fail(new Error('The hair worker sent an unreadable response.'), 'failed', 'unreadable-message');};
      if (signal.aborted) throw aborted();
      await this.request({type: 'initialize', sessionNonce: this.sessionNonce, requestId: this.nextId++, modelId: this.model.id,
        delegate: this.delegate, outputMode: this.outputMode}, this.initializeTimeoutMs);
      if (signal.aborted || !this.worker) throw aborted();
      this.state = 'ready';
    } catch (error) {this.fail(errorFrom(error), 'failed', 'invalid-response'); throw error;}
  }

  /** Legacy admission and result behavior remains fully serialized. */
  segment(image: ImageBitmap, sourceSHA256: string, sequence: number, categoryExtractionMode: CategoryExtractionMode = 'sdk'): Promise<HairSegmentationResult> {
    return this.beginSegment(image, sourceSHA256, sequence, categoryExtractionMode).result;
  }

  /** Takes bitmap ownership immediately, including rejected admission/transfer.
   * Observers see scalar milestones and one terminal snapshot for every attempt. */
  beginSegment(image: ImageBitmap, sourceSHA256: string, sequence: number, categoryExtractionMode: CategoryExtractionMode = 'sdk',
    releaseWorkerEarly = false, onTiming?: HairTimingObserver): HairSegmentDelivery {
    let release!: () => void, rejectRelease!: (error: Error) => void;
    const workerReleased = new Promise<void>((resolve, reject) => {release = resolve; rejectRelease = reject;});
    // A consumer can await either branch first without unhandled-rejection noise.
    void workerReleased.catch(() => {});
    const delivery: DeliveryState = {released: false, terminalNotified: false, release, rejectRelease, observer: onTiming,
      trace: {requestId: null, sequence: Number.isSafeInteger(sequence) && sequence >= 0 ? sequence : null,
        releaseWorkerEarly: releaseWorkerEarly === true,
        categoryExtractionMode: categoryExtractionMode === 'sdk' || categoryExtractionMode === 'direct' || categoryExtractionMode === 'rgba8' ? categoryExtractionMode : null,
        submittedAtMs: null, receivedAtMs: null, workerReleasedAtMs: null, validatedAtMs: null, hashStartedAtMs: null,
        completedAtMs: null, validationMs: null, hashMs: null, workerValidationMs: null, workerInferenceMs: null,
        workerExtractionMs: null, workerElapsedMs: null, pendingAtSubmission: null, outcome: 'pending', reason: null}};
    let reason: HairRequestTiming['reason'] = 'invalid-input';
    let pendingResult: Promise<HairSegmentationResult | undefined>;
    try {
      if (this.state !== 'ready') {reason = 'not-ready'; throw new Error('The hair worker is not ready.');}
      if (this.workerRequestId !== null || this.pending.size >= (releaseWorkerEarly ? 2 : 1)
        || [...this.pending.values()].some(value => !value.delivery?.trace.releaseWorkerEarly)) {
        reason = 'admission-rejected'; throw new Error('A hair segmentation already owns an image.');
      }
      assertSourceHash(sourceSHA256); assertSequence(sequence); assertDimensions(image.width, image.height);
      assertCategoryExtractionMode(categoryExtractionMode);
      if (typeof releaseWorkerEarly !== 'boolean') throw new Error('Unknown hair worker release mode.');
      if (sequence <= this.lastSequence) throw new Error('Hair frame sequences must increase within a session.');
      this.lastSequence = sequence;
      const expected = {sourceSHA256, sequence, width: image.width, height: image.height, delegate: this.delegate,
        outputMode: this.outputMode, categoryExtractionMode};
      pendingResult = this.request({type: 'segment', sessionNonce: this.sessionNonce, requestId: this.nextId++, image,
        sourceSHA256, sequence, categoryExtractionMode}, this.segmentTimeoutMs, expected, delivery);
    } catch (error) {
      const failure = errorFrom(error);
      delivery.trace.outcome = 'rejected'; delivery.trace.reason = reason; delivery.trace.completedAtMs = performance.now();
      delivery.rejectRelease(failure); notify(delivery); pendingResult = Promise.reject(failure);
    }
    const result = pendingResult.then(value => {
      if (!value) throw new Error('The hair worker returned no segmentation.');
      return value;
    }).finally(() => {image.close();});
    void result.catch(() => {});
    return {result, workerReleased, timing: () => snapshot(delivery)};
  }

  close(): void {this.fail(new Error('The hair preview client was closed.'), 'cancelled', 'client-closed');}

  private request(message: HairWorkerRequest, timeoutMs: number, expected?: HairExpectedPair,
    delivery?: DeliveryState): Promise<HairSegmentationResult | undefined> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new Error(message.type === 'initialize' ? 'Hair model startup timed out.' : 'Hair segmentation timed out.'),
        'timed-out', 'deadline'), timeoutMs);
      const pending: Pending = {requestId: message.requestId, kind: message.type, expected, delivery, validating: false, timer, resolve, reject};
      this.pending.set(message.requestId, pending); this.workerRequestId = message.requestId;
      if (delivery) Object.assign(delivery.trace, {requestId: message.requestId, submittedAtMs: performance.now(), pendingAtSubmission: this.pending.size});
      try {this.worker!.postMessage(message, message.type === 'segment' ? [message.image] : []); if (delivery) notify(delivery);}
      catch (error) {this.fail(errorFrom(error), 'failed', 'transfer-failed');}
    });
  }

  private async handleMessage(message: unknown): Promise<void> {
    if (this.state === 'closed' || this.pending.size === 0) return;
    if (!isRecord(message)) {this.fail(new Error('The hair worker sent an unreadable response.'), 'failed', 'unreadable-message'); return;}
    if (message.sessionNonce !== this.sessionNonce) return;
    if (!Number.isSafeInteger(message.requestId)) {this.fail(new Error('The hair worker returned an invalid response envelope.'), 'failed', 'invalid-response'); return;}
    const pending = this.pending.get(message.requestId as number);
    // A duplicate validating reply and a stale completed request cannot release
    // the computation slot now owned by a different image.
    if (!pending || pending.validating) return;
    const delivery = pending.delivery;
    if (delivery) {delivery.trace.receivedAtMs = performance.now(); notify(delivery);}
    if (this.pending.get(pending.requestId) !== pending) return;
    let validationStarted: number | null = null;
    try {
      if (message.type === 'error') {
        this.fail(new Error(typeof message.message === 'string' ? message.message : 'Hair inference failed.'), 'failed', 'worker-error'); return;
      }
      if (pending.kind === 'initialize') {
        validateHairReady(message, this.model, this.delegate, this.outputMode);
        if (message.categoryExtractionProtocol !== CATEGORY_EXTRACTION_PROTOCOL) throw new Error('The hair worker did not acknowledge the category extraction protocol.');
        this.settle(pending); return;
      }
      if (message.type !== 'result' || !pending.expected || !delivery || !isRecord(message.output)) throw new Error('The hair worker returned an unexpected response.');
      if (this.workerRequestId !== pending.requestId) throw new Error('The hair response does not own the computation slot.');
      pending.validating = true; this.workerRequestId = null;
      if (delivery.trace.releaseWorkerEarly) this.releaseWorker(delivery);
      if (this.pending.get(pending.requestId) !== pending) return;
      validationStarted = performance.now();
      const output = validateHairOutput(message.output, this.model, pending.expected);
      const categoryExtraction = validateCategoryExtractionMetrics(message.output.categoryExtraction, pending.expected, output.extractionMs);
      Object.assign(delivery.trace, {categoryPath: categoryExtraction.path, categoryRetrievalMs: categoryExtraction.retrievalMs,
        categoryConversionMs: categoryExtraction.conversionMs, categoryCopyMs: categoryExtraction.copyMs,
        categoryTotalMs: categoryExtraction.totalMs, categoryAttemptMs: categoryExtraction.rgba8WorkMs ?? null,
        categoryReadbackBytes: categoryExtraction.rgba8ReadbackBytes ?? null,
        categoryFallbackReason: categoryExtraction.rgba8FallbackReason ?? null});
      const workerTiming = validateHairWorkerTiming(message.output.workerTiming, output.inferenceMs, output.extractionMs);
      delivery.trace.validatedAtMs = performance.now(); delivery.trace.validationMs = delivery.trace.validatedAtMs - validationStarted;
      delivery.trace.workerInferenceMs = output.inferenceMs; delivery.trace.workerExtractionMs = output.extractionMs;
      delivery.trace.workerValidationMs = workerTiming?.inputValidationMs ?? null; delivery.trace.workerElapsedMs = workerTiming?.totalMs ?? null;
      delivery.trace.hashStartedAtMs = performance.now(); notify(delivery);
      if (this.pending.get(pending.requestId) !== pending) return;
      let result: HairSegmentationResult;
      try {
        result = output.outputMode === 'category-only'
          ? {...output, ...await this.hashMasks(output), categoryExtraction, ...(workerTiming ? {workerTiming} : {})}
          : {...output, ...await this.hashMasks(output), categoryExtraction, ...(workerTiming ? {workerTiming} : {})};
      } finally {
        if (this.pending.get(pending.requestId) === pending) delivery.trace.hashMs = performance.now() - delivery.trace.hashStartedAtMs;
      }
      if (this.pending.get(pending.requestId) !== pending || this.isClosed()) return;
      this.settle(pending, result);
    } catch (error) {
      if (this.pending.get(pending.requestId) === pending) {
        if (delivery && validationStarted !== null && delivery.trace.validationMs === null)
          delivery.trace.validationMs = performance.now() - validationStarted;
        this.fail(errorFrom(error), 'failed', 'invalid-response');
      }
    }
  }

  private isClosed(): boolean {return this.state === 'closed';}
  private releaseWorker(delivery: DeliveryState, report = true): void {
    if (delivery.released) return;
    delivery.released = true; delivery.trace.workerReleasedAtMs = performance.now(); delivery.release();
    if (report) notify(delivery);
  }
  private settle(pending: Pending, output?: HairSegmentationResult): void {
    if (this.pending.get(pending.requestId) !== pending) return;
    this.pending.delete(pending.requestId); if (this.workerRequestId === pending.requestId) this.workerRequestId = null;
    clearTimeout(pending.timer);
    if (pending.delivery) {
      this.releaseWorker(pending.delivery, false);
      pending.delivery.trace.completedAtMs = performance.now(); pending.delivery.trace.outcome = 'completed'; notify(pending.delivery);
    }
    pending.resolve(output);
  }
  private fail(error: Error, outcome: HairRequestTiming['outcome'], reason: HairRequestTiming['reason']): void {
    this.state = 'closed'; this.workerRequestId = null;
    const requests = [...this.pending.values()]; this.pending.clear();
    this.removeAbortListener?.(); this.removeAbortListener = null;
    const worker = this.worker; this.worker = null;
    if (worker) {worker.onmessage = null; worker.onerror = null; worker.onmessageerror = null; worker.terminate();}
    for (const pending of requests) {
      clearTimeout(pending.timer);
      if (pending.delivery) {
        Object.assign(pending.delivery.trace, {outcome, reason, completedAtMs: performance.now()});
        if (!pending.delivery.released) pending.delivery.rejectRelease(error);
        notify(pending.delivery);
      }
      pending.reject(error);
    }
  }
}
