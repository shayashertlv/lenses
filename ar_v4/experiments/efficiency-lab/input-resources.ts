/** Session-private scratch canvases. A lease must outlive every asynchronous
 * createImageBitmap read of its canvas; the resulting bitmap owns its pixels. */
export class InputCanvasPool {
  private readonly all = new Set<HTMLCanvasElement>();
  private readonly available: HTMLCanvasElement[] = [];
  private readonly leased = new Set<HTMLCanvasElement>();
  private disposed = false;
  private counts = {created: 0, reused: 0, resized: 0, acquired: 0, released: 0, maxLeased: 0};
  private readonly enabled: boolean;
  private readonly create: () => HTMLCanvasElement;
  private readonly limit: number;
  constructor(enabled: boolean, create = (): HTMLCanvasElement => document.createElement('canvas'), limit = 2) {
    this.enabled = enabled; this.create = create; this.limit = limit;
  }
  get stats() {return {...this.counts, liveCanvases: this.all.size, leasedCanvases: this.leased.size, freeCanvases: this.available.length};}
  acquire(width: number, height: number): HTMLCanvasElement {
    if (this.disposed) throw new DOMException('Input pool closed.', 'AbortError');
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0)
      throw new Error('Input dimensions must be positive integers.');
    if (this.leased.size >= this.limit) throw new Error('Input canvas ownership exceeds the two-image bound.');
    const reused = this.enabled ? this.available.pop() : undefined;
    const canvas = reused ?? this.create();
    if (reused) this.counts.reused++;
    else {this.all.add(canvas); this.counts.created++;}
    this.leased.add(canvas); this.counts.acquired++;
    this.counts.maxLeased = Math.max(this.counts.maxLeased, this.leased.size);
    try {
      if (!reused || canvas.width !== width || canvas.height !== height) {
        if (reused) this.counts.resized++;
        canvas.width = width; canvas.height = height;
      } else {
        // Fresh alpha:false canvases start opaque black. Preserve that source-
        // over behavior even for unusual transparent inputs on a reused lease.
        inputContext(canvas).clearRect(0, 0, width, height);
      }
      return canvas;
    } catch (error) {this.leased.delete(canvas); this.all.delete(canvas); canvas.width = canvas.height = 0; throw error;}
  }
  release(canvas: HTMLCanvasElement): void {
    if (!this.leased.delete(canvas)) throw new Error('Input canvas is not owned by this lease.');
    this.counts.released++;
    if (this.enabled && !this.disposed) this.available.push(canvas);
    else {this.all.delete(canvas); canvas.width = canvas.height = 0;}
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const canvas of this.available.splice(0)) {this.all.delete(canvas); canvas.width = canvas.height = 0;}
    // Leased canvases remain valid until their outstanding reads settle. Their
    // later release destroys them instead of returning them to the pool.
  }
}

export function inputContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const context = canvas.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'});
  if (!context) throw new Error('Camera canvas unavailable.');
  return context;
}

export interface InputHashResult {value: string; copyMs: number; submitMs: number; digestWallMs: number; explicitCopyBytes: number;}
/** getImageData storage remains immutable and attached until this promise
 * settles. WebCrypto snapshots the exact view; lean removes only the explicit
 * JavaScript copy, never hashing, bytes, or WebCrypto's own required snapshot. */
export async function hashInput(bytes: Uint8Array | Uint8ClampedArray, lean: boolean): Promise<InputHashResult> {
  const start = performance.now();
  if (!(bytes.buffer instanceof ArrayBuffer)) throw new Error('Input hashing requires independently owned storage.');
  const input = lean ? new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength) : new Uint8Array(bytes);
  const copyMs = performance.now() - start, submitAt = performance.now();
  const digest = crypto.subtle.digest('SHA-256', input);
  const submitMs = performance.now() - submitAt;
  const result = await digest;
  return {value: Array.from(new Uint8Array(result), v => v.toString(16).padStart(2, '0')).join(''),
    copyMs, submitMs, digestWallMs: performance.now() - submitAt, explicitCopyBytes: lean ? 0 : bytes.byteLength};
}
