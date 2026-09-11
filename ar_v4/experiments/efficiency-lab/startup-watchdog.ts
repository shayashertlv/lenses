export const STARTUP_STAGES = ['module', 'g-renderer', 'candidate-renderer', 'face', 'first-ar'] as const;
export type StartupStage = typeof STARTUP_STAGES[number];
export type StartupState = 'idle' | 'running' | 'complete' | 'cancelled' | 'failed' | 'timed-out';
export interface StartupStageReceipt {
  stage: StartupStage; startedAtMs: number; endedAtMs: number | null; deadlineAtMs: number; timeoutMs: number;
}
export interface StartupReceipt {
  schema: 'ar-startup-v1'; sessionId: string; state: StartupState; stage: StartupStage | null; reason: string | null;
  startedAtMs: number | null; finishedAtMs: number | null; observedAtMs: number; elapsedMs: number;
  stageElapsedMs: number; remainingMs: number; overallDeadlineAtMs: number | null; overallTimeoutMs: number;
  timeoutScope: 'stage' | 'overall' | null; stages: StartupStageReceipt[];
}
export interface StartupWatchdogOptions {
  sessionId: string; onTimeout: (receipt: StartupReceipt) => void; now?: () => number;
  timeouts?: Partial<Record<StartupStage, number>>; overallTimeoutMs?: number; signal?: AbortSignal;
  /** Deterministic lifecycle tests; returns a cancellation function for this one timer. */
  schedule?: (callback: () => void, delayMs: number) => () => void;
}
const TIMEOUTS: Record<StartupStage, number> = {
  module: 60000, 'g-renderer': 60000, 'candidate-renderer': 60000, face: 100000, 'first-ar': 30000,
};
const scheduleTimer = (callback: () => void, delayMs: number): (() => void) => {
  const timer = setTimeout(callback, delayMs); return () => clearTimeout(timer);
};

/** Reports startup progress and bounds pending work; the owner handles aborting
 * its session on timeout. Existing renderer/worker deadlines remain unchanged.
 */
export class StartupWatchdog {
  private readonly options: StartupWatchdogOptions;
  private readonly timeouts: Record<StartupStage, number>;
  private readonly overallTimeoutMs: number;
  private readonly now: () => number;
  private readonly schedule: NonNullable<StartupWatchdogOptions['schedule']>;
  private state: StartupState = 'idle';
  private reason: string | null = null;
  private timeoutScope: StartupReceipt['timeoutScope'] = null;
  private startedAtMs: number | null = null;
  private finishedAtMs: number | null = null;
  private lastAtMs = 0;
  private stages: StartupStageReceipt[] = [];
  private timerCancel: (() => void) | null = null;
  private timerGeneration = 0;
  private readonly onAbort = (): void => {this.cancel('aborted');};

  constructor(options: StartupWatchdogOptions) {
    if (!options.sessionId || options.sessionId.length > 128) throw new Error('Invalid startup session identity.');
    this.options = options; this.timeouts = {...TIMEOUTS, ...options.timeouts};
    this.overallTimeoutMs = options.overallTimeoutMs ?? 240000;
    if (![...Object.values(this.timeouts), this.overallTimeoutMs].every(value => Number.isFinite(value) && value > 0))
      throw new Error('Startup deadlines must be positive and finite.');
    this.now = options.now ?? (() => performance.now()); this.schedule = options.schedule ?? scheduleTimer;
  }

  start(): StartupReceipt {
    if (this.state !== 'idle') throw new Error('A startup watchdog belongs to one attempt; create a new one to retry.');
    const at = this.readTime(); this.startedAtMs = at; this.state = 'running';
    if (this.options.signal?.aborted) return this.cancel('aborted');
    this.options.signal?.addEventListener('abort', this.onAbort, {once: true});
    return this.enter('module');
  }

  /** Duplicate milestones do not extend their deadline; terminal attempts ignore late replies. */
  enter(stage: StartupStage): StartupReceipt {
    if (this.state !== 'running') return this.snapshot();
    const at = this.readTime();
    if (this.expireIfDue(at)) return this.snapshot();
    const previous = this.stages.at(-1);
    if (previous?.stage === stage) return this.snapshot();
    if (stage !== STARTUP_STAGES[this.stages.length]) throw new Error('Startup milestones must follow their declared order.');
    if (previous) previous.endedAtMs = at;
    this.stages.push({stage, startedAtMs: at, endedAtMs: null, timeoutMs: this.timeouts[stage], deadlineAtMs: at + this.timeouts[stage]});
    this.arm(); return this.snapshot();
  }

  complete(): StartupReceipt {
    if (this.state !== 'running') return this.snapshot();
    const at = this.readTime();
    if (this.expireIfDue(at)) return this.snapshot();
    if (this.stages.at(-1)?.stage !== 'first-ar') throw new Error('Only the first completed AR publication finishes startup.');
    this.finish('complete', null, at); return this.snapshot();
  }

  cancel(reason = 'cancelled'): StartupReceipt {return this.stop('cancelled', reason);}
  fail(reason: string): StartupReceipt {return this.stop('failed', reason);}

  /** Scalar receipt only; callers cannot mutate the retained stage history. */
  snapshot(): StartupReceipt {
    const observedAtMs = this.readTime(), stage = this.stages.at(-1), end = this.finishedAtMs ?? observedAtMs;
    const overallDeadlineAtMs = this.startedAtMs === null ? null : this.startedAtMs + this.overallTimeoutMs;
    return {schema: 'ar-startup-v1', sessionId: this.options.sessionId, state: this.state,
      stage: stage?.stage ?? null, reason: this.reason, startedAtMs: this.startedAtMs, finishedAtMs: this.finishedAtMs,
      observedAtMs, elapsedMs: this.startedAtMs === null ? 0 : Math.max(0, end - this.startedAtMs),
      stageElapsedMs: stage ? Math.max(0, (stage.endedAtMs ?? end) - stage.startedAtMs) : 0,
      remainingMs: this.state === 'running' && stage && overallDeadlineAtMs !== null
        ? Math.max(0, Math.min(stage.deadlineAtMs, overallDeadlineAtMs) - observedAtMs) : 0,
      overallDeadlineAtMs, overallTimeoutMs: this.overallTimeoutMs, timeoutScope: this.timeoutScope,
      stages: this.stages.map(value => ({...value}))};
  }

  private stop(state: 'cancelled' | 'failed', reason: string): StartupReceipt {
    if (this.state === 'idle' || this.state === 'running') this.finish(state, String(reason).slice(0, 512), this.readTime());
    return this.snapshot();
  }

  private readTime(): number {
    const at = this.now();
    if (!Number.isFinite(at) || at < 0) throw new Error('Invalid startup clock.');
    // A precision change must never produce negative durations or extend a deadline.
    this.lastAtMs = Math.max(this.lastAtMs, at); return this.lastAtMs;
  }

  private arm(): void {
    this.clearTimer();
    if (this.state !== 'running') return;
    const generation = this.timerGeneration, stage = this.stages.at(-1)!;
    const at = this.readTime(), deadline = Math.min(stage.deadlineAtMs, this.startedAtMs! + this.overallTimeoutMs);
    this.timerCancel = this.schedule(() => {
      if (this.state !== 'running' || generation !== this.timerGeneration) return;
      const now = this.readTime();
      // Defensive against an early scheduler callback; remaining time is not reset.
      if (!this.expireIfDue(now)) this.arm();
    }, Math.max(0, deadline - at));
  }

  private expireIfDue(at: number): boolean {
    if (this.state !== 'running' || this.startedAtMs === null) return false;
    const stage = this.stages.at(-1), overall = this.startedAtMs + this.overallTimeoutMs;
    if (at < Math.min(stage?.deadlineAtMs ?? Infinity, overall)) return false;
    this.timeoutScope = overall <= (stage?.deadlineAtMs ?? Infinity) ? 'overall' : 'stage';
    this.finish('timed-out', this.timeoutScope === 'overall' ? 'overall-timeout' : `${stage!.stage}-timeout`, at);
    this.options.onTimeout(this.snapshot()); return true;
  }

  private clearTimer(): void {
    this.timerGeneration++; this.timerCancel?.(); this.timerCancel = null;
  }

  private finish(state: StartupState, reason: string | null, at: number): void {
    this.state = state; this.reason = reason; this.finishedAtMs = at;
    const stage = this.stages.at(-1); if (stage && stage.endedAtMs === null) stage.endedAtMs = at;
    this.clearTimer(); this.options.signal?.removeEventListener('abort', this.onAbort);
  }
}
