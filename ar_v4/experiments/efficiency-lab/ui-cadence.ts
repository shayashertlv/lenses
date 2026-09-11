/** Gates human-facing summaries only. Admission never drops a profiling sample,
 * changes processing cadence or owns a timer. State transitions bypass the gate. */
export class UiSummaryCadence {
  private lastKey: string | null = null;
  private lastRefreshAt = -Infinity;
  private requests = 0;
  private refreshes = 0;
  private skipped = 0;
  readonly intervalMs: number;
  constructor(intervalMs = 500) {
    if (!Number.isFinite(intervalMs) || intervalMs <= 0) throw new Error('Invalid UI summary interval.');
    this.intervalMs=intervalMs;
  }
  observe(atMs: number, stateKey: string, throttled: boolean) {
    if (!Number.isFinite(atMs) || !stateKey) throw new Error('Invalid UI summary observation.');
    const transition = stateKey !== this.lastKey;
    const refresh = !throttled || transition || atMs - this.lastRefreshAt >= this.intervalMs;
    this.requests++; this.lastKey = stateKey;
    if (refresh) {this.refreshes++; this.lastRefreshAt = atMs;} else this.skipped++;
    return {refresh, transition, requested: throttled, intervalMs: throttled ? this.intervalMs : 0,
      requests: this.requests, refreshes: this.refreshes, skipped: this.skipped};
  }
}
