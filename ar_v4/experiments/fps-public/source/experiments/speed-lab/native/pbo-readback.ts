import type {WebGLRenderer} from 'three';

export interface PboReadbackMetrics {
  queuedCalls: number; queuedBytes: number; retrievedCalls: number; retrievedBytes: number; polls: number; waitMs: number;
  submitMs: number; extractMs: number; bufferBytesAllocated: number;
  completed: boolean; fallbackReason: string | null;
}
const empty = (): PboReadbackMetrics => ({queuedCalls: 0, queuedBytes: 0, retrievedCalls: 0, retrievedBytes: 0, polls: 0, waitMs: 0,
  submitMs: 0, extractMs: 0, bufferBytesAllocated: 0, completed: false, fallbackReason: null});
const abort = (): DOMException => new DOMException('The asynchronous native pair was cancelled.', 'AbortError');
interface Slot {buffer: WebGLBuffer; bytes: number;}

/** One pending pair only. Native read commands precede any later default-buffer
 * draw; CPU retrieval occurs only after their fence signals. No previous bytes
 * can become the result of a failed, expired or superseded pair. */
export class PboNativeReadback {
  private readonly renderer: WebGLRenderer;
  private readonly gl: WebGL2RenderingContext;
  private slots: Slot[] = [];
  private sync: WebGLSync | null = null;
  private width = 0;
  private height = 0;
  private generation = 0;
  private count = 0;
  private pending = false;
  private disposed = false;
  private measured = empty();
  constructor(renderer: WebGLRenderer) {
    this.renderer = renderer;
    const gl = renderer.getContext();
    if (!('fenceSync' in gl)) throw new Error('WebGL 2 pixel-pack buffers are unavailable.');
    this.gl = gl;
  }
  get metrics(): PboReadbackMetrics {return {...this.measured};}
  begin(width: number, height: number): void {
    if (this.pending) throw new Error('A native readback pair is already pending.');
    this.cancel(); this.measured = empty();
    if (this.disposed || this.gl.isContextLost()) throw new Error('The native readback context is unavailable.');
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0)
      throw new Error('The native readback dimensions are invalid.');
    this.width = width; this.height = height; this.count = 0; this.pending = true;
  }
  capture(slot: 0 | 1): void {
    if (!this.pending || slot !== this.count || this.sync) throw new Error('Native reads must follow beauty then optional camera.');
    if (this.renderer.getRenderTarget() !== null || this.gl.isContextLost()) throw new Error('The native default framebuffer is unavailable.');
    const gl = this.gl, started = performance.now(), bytes = this.width * this.height * 4;
    try {this.withPackState(() => {
      let target = this.slots[slot];
      if (!target) {
        const buffer = gl.createBuffer(); if (!buffer) throw new Error('Pixel-pack buffer allocation failed.');
        target = {buffer, bytes: 0}; this.slots[slot] = target;
      }
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, target.buffer);
      if (target.bytes !== bytes) {
        gl.bufferData(gl.PIXEL_PACK_BUFFER, bytes, gl.STREAM_READ); this.check('buffer allocation');
        target.bytes = bytes; this.measured.bufferBytesAllocated += bytes;
      }
      gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
      this.renderer.state.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
      gl.readPixels(0, 0, this.width, this.height, gl.RGBA, gl.UNSIGNED_BYTE, 0);
      this.measured.queuedCalls++; this.measured.queuedBytes += bytes; this.check('read submission');
      this.count++;
    });} catch (error) {this.measured.fallbackReason = String(error); this.cancel(); throw error;}
    finally {this.measured.submitMs += performance.now() - started;}
  }
  async finish(isCurrent: () => boolean, timeoutMs = 500): Promise<{beauty: ImageData; camera: ImageData | null}> {
    if (!this.pending || this.count < 1 || this.sync) throw new Error('No submitted native pair is ready to await.');
    const gl = this.gl, generation = this.generation, started = performance.now();
    const current = (): void => {
      if (this.disposed || generation !== this.generation || !isCurrent()) throw abort();
      if (gl.isContextLost()) throw new Error('The native readback context was lost.');
    };
    try {
      current();
      this.sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
      if (!this.sync) throw new Error('The native readback fence could not be created.');
      gl.flush(); this.check('fence submission');
      for (;;) {
        current(); this.measured.polls++;
        const status = gl.clientWaitSync(this.sync, 0, 0); this.check('fence poll');
        if (status === gl.ALREADY_SIGNALED || status === gl.CONDITION_SATISFIED) break;
        if (status === gl.WAIT_FAILED) throw new Error('The native readback fence failed.');
        if (performance.now() - started >= timeoutMs) throw new Error('The native readback fence exceeded its bounded wait.');
        await new Promise<void>(resolve => setTimeout(resolve, 0));
      }
      this.measured.waitMs = performance.now() - started;
      const extractStarted = performance.now();
      const images = this.withPackState(() => Array.from({length: this.count}, (_, index) => {
        current(); const slot = this.slots[index]!;
        const bottom = new Uint8Array(slot.bytes);
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER, slot.buffer);
        gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, bottom);
        this.measured.retrievedCalls++; this.measured.retrievedBytes += bottom.byteLength;
        this.check('CPU retrieval');
        const top = new Uint8ClampedArray(bottom.length), row = this.width * 4;
        for (let y = 0; y < this.height; y++) top.set(bottom.subarray((this.height - y - 1) * row, (this.height - y) * row), y * row);
        return new ImageData(top, this.width, this.height);
      }));
      current(); this.measured.extractMs = performance.now() - extractStarted; this.measured.completed = true;
      return {beauty: images[0]!, camera: images[1] ?? null};
    } catch (error) {this.measured.fallbackReason = error instanceof Error ? error.message : String(error); throw error;}
    finally {
      this.measured.waitMs ||= performance.now() - started;
      if (generation === this.generation) {this.pending = false; if (this.sync) gl.deleteSync(this.sync); this.sync = null;}
    }
  }
  private check(label: string): void {
    const error = this.gl.getError();
    if (error !== this.gl.NO_ERROR || this.gl.isContextLost()) throw new Error(`Native PBO ${label} failed (GL 0x${error.toString(16)}).`);
  }
  private withPackState<T>(operation: () => T): T {
    const gl = this.gl, read = gl.getParameter(gl.READ_FRAMEBUFFER_BINDING) as WebGLFramebuffer | null;
    const pack = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING) as WebGLBuffer | null;
    const keys = [gl.PACK_ALIGNMENT, gl.PACK_ROW_LENGTH, gl.PACK_SKIP_PIXELS, gl.PACK_SKIP_ROWS];
    const values = keys.map(key => Number(gl.getParameter(key)));
    try {
      keys.forEach(key => gl.pixelStorei(key, key === gl.PACK_ALIGNMENT ? 1 : 0));
      return operation();
    } finally {
      gl.bindFramebuffer(gl.READ_FRAMEBUFFER, read); this.renderer.state.bindFramebuffer(gl.READ_FRAMEBUFFER, read);
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pack);
      keys.forEach((key, index) => gl.pixelStorei(key, values[index]!));
    }
  }
  cancel(): void {
    this.generation++; this.pending = false; this.count = 0;
    if (this.sync) this.gl.deleteSync(this.sync); this.sync = null;
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.cancel();
    for (const slot of this.slots) this.gl.deleteBuffer(slot.buffer); this.slots = [];
  }
}
