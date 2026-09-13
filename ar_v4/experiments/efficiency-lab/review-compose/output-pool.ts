/** A lease is the only owner of its mutable output. Retained/held consumers must
 * keep the lease or call copyPixels before handing ownership back. Release is
 * idempotent; it never revokes another generation or another lease. */
export interface CompositionOutputLease {
  readonly pixels: Uint8ClampedArray;
  readonly released: boolean;
  readonly bytesAllocated: number;
  readonly reused: boolean;
  readonly fallbackReason: string | null;
  readonly pooledLeases: number;
  readonly copiedBytes: number;
  copyPixels(): Uint8ClampedArray;
  release(): void;
}

interface Entry {pixels: Uint8ClampedArray; busy: boolean;}

/** At most two pooled outputs exist. If both are retained, an independent owned
 * allocation is safer than overwriting either. Clearing/disposal detaches the
 * pool from active leases; their pixels remain intact until their owners finish. */
export class CompositionOutputPool {
  private entries: Entry[] = [];
  private disposed = false;

  get retainedLeaseCount(): number { return this.entries.filter(entry => entry.busy).length; }
  get pooledBufferCount(): number { return this.entries.length; }

  acquire(before: Uint8ClampedArray): CompositionOutputLease {
    if (this.disposed) throw new Error('The composition output owner is disposed.');
    let entry = this.entries.find(value => !value.busy && value.pixels.length === before.length)
      ?? this.entries.find(value => !value.busy);
    const reused = !!entry && entry.pixels.length === before.length;
    if (!entry && this.entries.length < 2) {
      entry = {pixels: new Uint8ClampedArray(before.length), busy: false}; this.entries.push(entry);
    } else if (entry && !reused) entry.pixels = new Uint8ClampedArray(before.length);
    const pixels = entry?.pixels ?? new Uint8ClampedArray(before.length);
    if (entry) entry.busy = true;
    pixels.set(before);
    const fallbackReason = entry ? null : 'Both pooled outputs are retained; using an independent owned output.';
    let released = false, copiedBytes = 0;
    const pooledLeases = this.retainedLeaseCount;
    return {
      pixels, bytesAllocated: reused ? 0 : pixels.byteLength, reused, fallbackReason, pooledLeases,
      get released() { return released; }, get copiedBytes() { return copiedBytes; },
      copyPixels() {
        if (released) throw new Error('The composition output lease was already released.');
        copiedBytes += pixels.byteLength; return pixels.slice();
      },
      release() { if (released) return; released = true; if (entry) entry.busy = false; },
    };
  }

  clear(): void { this.entries = []; }
  dispose(): void { this.disposed = true; this.clear(); }
}
