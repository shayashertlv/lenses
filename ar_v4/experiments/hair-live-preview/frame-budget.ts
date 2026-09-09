/** One optional job at a time. A missed frame can never borrow its result later. */
export class FrameBudget<T> {
  private pending: {sequence: number; result: T | null; done: Promise<void>} | null = null;
  private closed = false;

  get busy(): boolean { return this.pending !== null; }

  start(sequence: number, task: () => Promise<T | null>): boolean {
    if (this.closed || this.pending) return false;
    const job = {sequence, result: null as T | null, done: Promise.resolve()};
    this.pending = job;
    job.done = Promise.resolve().then(task).then(value => {
      if (!this.closed) job.result = value;
    }, () => { job.result = null; }).finally(() => {
      if (this.pending === job) this.pending = null;
    });
    this.latest = job;
    return true;
  }

  private latest: {sequence: number; result: T | null; done: Promise<void>} | null = null;

  async take(sequence: number, maximumWaitMs: number): Promise<T | null> {
    const job = this.latest;
    if (this.closed || !job || job.sequence !== sequence) return null;
    this.latest = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      if (maximumWaitMs > 0) await Promise.race([job.done, new Promise<void>(resolve => { timer = setTimeout(resolve, maximumWaitMs); })]);
      return this.closed ? null : job.result;
    } finally { if (timer !== undefined) clearTimeout(timer); }
  }

  close(): void { this.closed = true; this.latest = null; }
}
