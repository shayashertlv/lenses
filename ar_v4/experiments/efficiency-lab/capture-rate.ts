export interface CaptureAdmissionStats {
  readonly rateHz: number | null;
  /** Distinct rVFC frames (or best-effort rAF playback positions), including busy/skipped callbacks. */
  readonly candidateCallbacks: number;
  readonly duplicateCallbacks: number;
  readonly rateSkipped: number;
  readonly backpressureSkipped: number;
  readonly captures: number;
}

/** Session-local admission only. Never stores an image, schedules a timer, or
 * accrues catch-up credit. Commit only after the synchronous snapshot succeeds. */
export class CaptureRateAdmission {
  readonly rateHz: number | null;
  private readonly intervalMs: number;
  private lastCaptureAtMs: number | null = null;
  private lastObservedIdentity: number | null = null;
  private counts = {candidateCallbacks: 0, duplicateCallbacks: 0, rateSkipped: 0,
    backpressureSkipped: 0, captures: 0};

  constructor(rateHz: number | null) {
    if (rateHz !== null && (!Number.isFinite(rateHz) || rateHz <= 0))
      throw new Error('Capture admission rate must be positive and finite, or null.');
    this.rateHz = rateHz; this.intervalMs = rateHz === null ? 0 : 1000 / rateHz;
  }

  get stats(): CaptureAdmissionStats {return {rateHz: this.rateHz, ...this.counts};}

  observeReady(frameIdentity: number): boolean {
    if (frameIdentity === this.lastObservedIdentity) {this.counts.duplicateCallbacks++; return false;}
    this.lastObservedIdentity = frameIdentity; this.counts.candidateCallbacks++; return true;
  }

  canCapture(startAtMs: number): boolean {
    if (!Number.isFinite(startAtMs) || startAtMs < 0) throw new Error('Capture time must be a monotonic timestamp.');
    return this.rateHz === null || this.lastCaptureAtMs === null || startAtMs - this.lastCaptureAtMs >= this.intervalMs;
  }

  skipRate(): void {this.counts.rateSkipped++;}
  skipBackpressure(): void {this.counts.backpressureSkipped++;}

  accepted(startAtMs: number): void {
    if (!this.canCapture(startAtMs)) throw new Error('Capture began before its admission interval elapsed.');
    this.lastCaptureAtMs = startAtMs; this.counts.captures++;
  }
}
