export interface FrameIdentity {
  readonly sequence: number;
  readonly capturedAtMs: number;
}

export interface FramePumpOptions<Frame, Inference, Prepared> {
  readonly mode: 'fresh' | 'overlap';
  identity(frame: Frame): FrameIdentity;
  /** Settle only after this callback's worker ownership ends. Detached work needs its own lock. */
  infer(frame: Frame, signal: AbortSignal): Promise<Inference>;
  /** Private preparation only: publication must happen in the synchronous publish callback. */
  prepare(frame: Frame, inference: Inference, signal: AbortSignal): Prepared | Promise<Prepared>;
  publish(frame: Frame, inference: Inference, prepared: Prepared): void;
  disposeFrame(frame: Frame): void;
  disposeInference?(inference: Inference): void;
  disposePrepared?(prepared: Prepared): void;
  /** Runs after publication and before disposal; copy or explicitly retain anything needed by Hold. */
  onPublished?(frame: Frame, inference: Inference, prepared: Prepared): void;
  onError?(error: unknown): void;
  now?(): number;
}

export interface FramePumpStats {
  readonly offered: number;
  readonly captured: number;
  readonly captureMisses: number;
  readonly published: number;
  readonly replaced: number;
  readonly dropped: number;
  readonly lockedDrops: number;
  readonly closedDrops: number;
  readonly invalidDrops: number;
  readonly ownedFrames: number;
  readonly processingFrames: number;
  readonly pendingFrames: number;
  readonly inFlightInference: number;
  readonly maxOwnedFrames: number;
  readonly maxInFlightInference: number;
  readonly inferenceCalls: number;
  readonly inferenceTotalMs: number;
  readonly lastInferenceMs: number | null;
  readonly accepting: boolean;
  readonly failed: boolean;
}

type Phase = 'captured' | 'inferring' | 'inferred' | 'preparing';
interface Slot<Frame, Inference, Prepared> {
  readonly frame: Frame;
  readonly identity: FrameIdentity;
  readonly abort: AbortController;
  phase: Phase;
  busy: number;
  release: boolean;
  disposed: boolean;
  inference?: Inference;
  hasInference: boolean;
  prepared?: Prepared;
  hasPrepared: boolean;
  inferenceTask?: Promise<void>;
}

/** A session-scoped, bounded handoff. The caller owns rVFC registration.
 * offer's capture callback must snapshot pixels and metadata synchronously in the
 * same rVFC task, into storage never reused while owned here. It must not read an
 * old callback identity and later capture a newer video image after an await.
 * Fresh mode has one processing pair plus one replaceable captured pair. Overlap
 * locks that second slot as soon as inference starts; newer offers then skip
 * capture, rather than close a worker-owned frame or allocate a third one.
 * New sessions/configuration epochs require new pumps. No baseline loop is changed.
 */
export class FramePump<Frame, Inference, Prepared> {
  private readonly options: FramePumpOptions<Frame, Inference, Prepared>;
  private readonly now: () => number;
  private readonly owned = new Set<Slot<Frame, Inference, Prepared>>();
  private readonly idleWaiters: (() => void)[] = [];
  private active: Slot<Frame, Inference, Prepared> | null = null;
  private pending: Slot<Frame, Inference, Prepared> | null = null;
  private inferring: Slot<Frame, Inference, Prepared> | null = null;
  private lastIdentity: FrameIdentity | null = null;
  private accepting = true;
  private stopped = false;
  private capturing = false;
  private hasFailed = false;
  private failure: unknown = null;
  private counts = {offered: 0, captured: 0, captureMisses: 0, published: 0, replaced: 0,
    dropped: 0, lockedDrops: 0, closedDrops: 0, invalidDrops: 0, maxOwnedFrames: 0,
    maxInFlightInference: 0, inferenceCalls: 0, inferenceTotalMs: 0, lastInferenceMs: null as number | null};

  constructor(options: FramePumpOptions<Frame, Inference, Prepared>) {
    this.options = options;
    this.now = options.now ?? (() => performance.now());
  }

  get stats(): FramePumpStats {
    return {...this.counts, ownedFrames: this.owned.size, processingFrames: Number(this.active !== null),
      pendingFrames: Number(this.pending !== null), inFlightInference: Number(this.inferring !== null),
      accepting: this.accepting, failed: this.hasFailed};
  }
  get error(): unknown { return this.failure; }

  offer(captureNow: () => Frame | null): boolean {
    this.counts.offered++;
    if (!this.accepting) {this.counts.dropped++; this.counts.closedDrops++; return false;}
    if (this.capturing || (this.pending && this.pending.phase !== 'captured')) {
      this.counts.dropped++; this.counts.lockedDrops++; return false;
    }
    // Dispose a superseded snapshot BEFORE allocating another: even transient
    // capture ownership stays within two frames, not just the end-of-task state.
    if (this.pending) {
      const previous = this.pending; this.pending = null;
      this.counts.replaced++; this.counts.dropped++;
      this.release(previous);
      if (!this.accepting) return false;
    }
    this.capturing = true;
    let frame: Frame | null = null;
    try {
      frame = captureNow();
      if (frame === null) {this.counts.captureMisses++; return false;}
      this.counts.captured++;
      const identity = {...this.options.identity(frame)};
      const valid = Number.isSafeInteger(identity.sequence) && identity.sequence >= 0
        && Number.isFinite(identity.capturedAtMs) && identity.capturedAtMs >= 0
        && (!this.lastIdentity || (identity.sequence > this.lastIdentity.sequence
          && identity.capturedAtMs > this.lastIdentity.capturedAtMs));
      if (!valid || !this.accepting) {
        this.counts.dropped++;
        if (!valid) this.counts.invalidDrops++; else this.counts.closedDrops++;
        const discarded = frame; frame = null; this.options.disposeFrame(discarded);
        return false;
      }
      const slot: Slot<Frame, Inference, Prepared> = {frame, identity, abort: new AbortController(),
        phase: 'captured', busy: 0, release: false, disposed: false, hasInference: false, hasPrepared: false};
      frame = null;
      this.owned.add(slot);
      this.counts.maxOwnedFrames = Math.max(this.counts.maxOwnedFrames, this.owned.size);
      this.lastIdentity = identity;
      if (!this.active) {this.active = slot; void this.process(slot);}
      else {this.pending = slot; this.prefetch();}
      return true;
    } catch (error) {
      if (frame !== null) {
        try {this.options.disposeFrame(frame);} catch { /* The original failure remains the cause. */ }
      }
      this.fail(error);
      return false;
    } finally {this.capturing = false; this.notifyIdle();}
  }

  /** Immediate revocation. In-flight callbacks must settle before their resources are disposed. */
  stop(): void {
    this.accepting = false; this.stopped = true;
    this.pending = null;
    for (const slot of this.owned) {slot.abort.abort(); this.release(slot);}
    this.notifyIdle();
  }

  /** Graceful Hold: finish only the active pair; discard the next pair and reject new offers. */
  finishCurrent(): Promise<void> {
    this.accepting = false;
    const next = this.pending; this.pending = null;
    if (next) {next.abort.abort(); this.counts.dropped++; this.release(next);}
    this.notifyIdle();
    return this.whenIdle();
  }

  /** Resolves after callbacks settle and every owned resource is released, not merely after abort. */
  whenIdle(): Promise<void> {
    if (this.isIdle()) return Promise.resolve();
    return new Promise(resolve => {this.idleWaiters.push(resolve);});
  }

  private live(slot: Slot<Frame, Inference, Prepared>): boolean {
    return !this.stopped && !slot.abort.signal.aborted && !slot.disposed && !slot.release;
  }

  private infer(slot: Slot<Frame, Inference, Prepared>): Promise<void> {
    if (slot.inferenceTask) return slot.inferenceTask;
    if (!this.live(slot)) return Promise.resolve();
    if (this.inferring) throw new Error('The frame pump already owns an inference request.');
    slot.phase = 'inferring'; slot.busy++; this.inferring = slot;
    this.counts.inferenceCalls++; this.counts.maxInFlightInference = 1;
    const started = this.now();
    // Install ownership before invoking user code, but submit the worker request
    // now so prefetch can overlap even the synchronous part of preparation.
    let settled!: () => void;
    slot.inferenceTask = new Promise(resolve => {settled = resolve;});
    void (async () => {
      try {
        if (!this.live(slot)) return;
        const result = await this.options.infer(slot.frame, slot.abort.signal);
        slot.inference = result; slot.hasInference = true; slot.phase = 'inferred';
      } catch (error) {
        if (this.live(slot)) this.fail(error);
      } finally {
        const elapsed = Math.max(0, this.now() - started);
        this.counts.lastInferenceMs = elapsed; this.counts.inferenceTotalMs += elapsed;
        slot.busy--; this.inferring = null;
        if (slot.release) this.release(slot);
        settled(); this.notifyIdle();
      }
    })();
    return slot.inferenceTask;
  }

  private prefetch(): void {
    if (this.options.mode === 'overlap' && this.accepting && this.active?.phase === 'preparing'
      && this.pending?.phase === 'captured' && !this.inferring) void this.infer(this.pending);
  }

  private async process(slot: Slot<Frame, Inference, Prepared>): Promise<void> {
    try {
      await this.infer(slot);
      if (!this.live(slot) || !slot.hasInference) return;
      slot.phase = 'preparing'; this.prefetch();
      // A prefetched callback can fail or stop synchronously before returning
      // its promise. That revocation must also suppress this pair's preparation.
      if (!this.live(slot)) return;
      slot.busy++;
      try {
        slot.prepared = await this.options.prepare(slot.frame, slot.inference as Inference, slot.abort.signal);
        slot.hasPrepared = true;
      } finally {slot.busy--;}
      if (!this.live(slot)) return;
      slot.busy++;
      try {
        this.options.publish(slot.frame, slot.inference as Inference, slot.prepared as Prepared);
        this.counts.published++;
        if (this.live(slot)) this.options.onPublished?.(slot.frame, slot.inference as Inference, slot.prepared as Prepared);
      } finally {slot.busy--;}
    } catch (error) {
      if (this.live(slot)) this.fail(error);
    } finally {
      this.release(slot);
      if (this.active === slot) this.active = null;
      const next = this.pending;
      if (next && this.accepting && !this.stopped) {
        this.pending = null; this.active = next; void this.process(next);
      }
      this.notifyIdle();
    }
  }

  private release(slot: Slot<Frame, Inference, Prepared>): void {
    slot.release = true;
    if (slot.disposed || slot.busy) return;
    slot.disposed = true; this.owned.delete(slot);
    const cleanups: (() => void)[] = [];
    if (slot.hasPrepared) cleanups.push(() => this.options.disposePrepared?.(slot.prepared as Prepared));
    if (slot.hasInference) cleanups.push(() => this.options.disposeInference?.(slot.inference as Inference));
    cleanups.push(() => this.options.disposeFrame(slot.frame));
    for (const cleanup of cleanups) {try {cleanup();} catch (error) {this.fail(error);}}
    this.notifyIdle();
  }

  private fail(error: unknown): void {
    if (this.hasFailed) return;
    this.hasFailed = true; this.failure = error; this.stop();
    try {this.options.onError?.(error);} catch { /* Cleanup and revocation must still complete. */ }
  }

  private isIdle(): boolean {
    return !this.active && !this.pending && !this.inferring && !this.capturing && this.owned.size === 0;
  }
  private notifyIdle(): void {
    if (this.isIdle()) for (const resolve of this.idleWaiters.splice(0)) resolve();
  }
}
