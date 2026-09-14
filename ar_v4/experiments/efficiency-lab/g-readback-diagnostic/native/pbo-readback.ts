import type {WebGLRenderer} from 'three';

/** G's PBO implementation with elapsed-time observation only. Read TIMINGS.md
 * before interpreting these fields: they are CPU wall-clock intervals, and
 * the observer itself adds work. The accepted G module is never modified. */
export interface PboReadbackMetrics {
  queuedCalls: number; queuedBytes: number; retrievedCalls: number; retrievedBytes: number; polls: number; waitMs: number;
  submitMs: number; extractMs: number; bufferBytesAllocated: number;
  completed: boolean; fallbackReason: string | null;
  diagnosticVersion: number; diagnosticClockReads: number;
  submitStateQueryMs: number; submitStateSetupMs: number; submitStateRestoreMs: number;
  extractStateQueryMs: number; extractStateSetupMs: number; extractStateRestoreMs: number;
  submitBufferCreateMs: number; submitBufferAllocateMs: number; submitReadPixelsMs: number;
  submitCheckMs: number; waitCheckMs: number; extractCheckMs: number; currentCheckMs: number;
  fenceSyncMs: number; fenceFlushMs: number; clientWaitMs: number;
  pollYieldMs: number; pollYields: number; waitObservationMs: number; extractObservationMs: number;
  extractBufferBindMs: number; extractGetBufferSubDataMs: number;
  extractAllocationMs: number; extractRowFlipMs: number; extractImageDataMs: number;
}
const empty = (): PboReadbackMetrics => ({queuedCalls: 0, queuedBytes: 0, retrievedCalls: 0, retrievedBytes: 0, polls: 0, waitMs: 0,
  submitMs: 0, extractMs: 0, bufferBytesAllocated: 0, completed: false, fallbackReason: null,
  diagnosticVersion: 1, diagnosticClockReads: 0,
  submitStateQueryMs: 0, submitStateSetupMs: 0, submitStateRestoreMs: 0,
  extractStateQueryMs: 0, extractStateSetupMs: 0, extractStateRestoreMs: 0,
  submitBufferCreateMs: 0, submitBufferAllocateMs: 0, submitReadPixelsMs: 0,
  submitCheckMs: 0, waitCheckMs: 0, extractCheckMs: 0, currentCheckMs: 0,
  fenceSyncMs: 0, fenceFlushMs: 0, clientWaitMs: 0, pollYieldMs: 0, pollYields: 0,
  waitObservationMs: 0, extractObservationMs: 0, extractBufferBindMs: 0,
  extractGetBufferSubDataMs: 0, extractAllocationMs: 0, extractRowFlipMs: 0, extractImageDataMs: 0});
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
  private stamp(measured: PboReadbackMetrics): number {
    measured.diagnosticClockReads++; return performance.now();
  }
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
    const measured = this.measured;
    try {this.withPackState(() => {
      let target = this.slots[slot];
      if (!target) {
        const createStarted = this.stamp(measured);
        let buffer: WebGLBuffer | null;
        try {buffer = gl.createBuffer();}
        finally {measured.submitBufferCreateMs += this.stamp(measured) - createStarted;}
        if (!buffer) throw new Error('Pixel-pack buffer allocation failed.');
        target = {buffer, bytes: 0}; this.slots[slot] = target;
      }
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, target.buffer);
      if (target.bytes !== bytes) {
        const allocateStarted = this.stamp(measured);
        try {gl.bufferData(gl.PIXEL_PACK_BUFFER, bytes, gl.STREAM_READ);}
        finally {measured.submitBufferAllocateMs += this.stamp(measured) - allocateStarted;}
        this.check('buffer allocation', 'submit', measured);
        target.bytes = bytes; measured.bufferBytesAllocated += bytes;
      }
      gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
      this.renderer.state.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
      const readStarted = this.stamp(measured);
      try {gl.readPixels(0, 0, this.width, this.height, gl.RGBA, gl.UNSIGNED_BYTE, 0);}
      finally {measured.submitReadPixelsMs += this.stamp(measured) - readStarted;}
      measured.queuedCalls++; measured.queuedBytes += bytes; this.check('read submission', 'submit', measured);
      this.count++;
    }, 'submit', measured);} catch (error) {measured.fallbackReason = String(error); this.cancel(); throw error;}
    finally {measured.submitMs += performance.now() - started;}
  }
  async finish(isCurrent: () => boolean, timeoutMs = 500): Promise<{beauty: ImageData; camera: ImageData | null}> {
    if (!this.pending || this.count < 1 || this.sync) throw new Error('No submitted native pair is ready to await.');
    const gl = this.gl, generation = this.generation, started = performance.now();
    // Keep observations attached to this pair if cancel/begin occurs while the
    // unchanged zero-timeout poll is suspended. Rendering ownership is unchanged.
    const measured = this.measured;
    let waitFinished = false;
    const current = (): void => {
      const checkStarted = this.stamp(measured);
      try {
        if (this.disposed || generation !== this.generation || !isCurrent()) throw abort();
        if (gl.isContextLost()) throw new Error('The native readback context was lost.');
      } finally {measured.currentCheckMs += this.stamp(measured) - checkStarted;}
    };
    try {
      current();
      const fenceStarted = this.stamp(measured);
      try {this.sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);}
      finally {measured.fenceSyncMs += this.stamp(measured) - fenceStarted;}
      if (!this.sync) throw new Error('The native readback fence could not be created.');
      const flushStarted = this.stamp(measured);
      try {gl.flush();}
      finally {measured.fenceFlushMs += this.stamp(measured) - flushStarted;}
      this.check('fence submission', 'wait', measured);
      for (;;) {
        current(); measured.polls++;
        const pollStarted = this.stamp(measured);
        let status: number;
        try {status = gl.clientWaitSync(this.sync, 0, 0);}
        finally {measured.clientWaitMs += this.stamp(measured) - pollStarted;}
        this.check('fence poll', 'wait', measured);
        if (status === gl.ALREADY_SIGNALED || status === gl.CONDITION_SATISFIED) break;
        if (status === gl.WAIT_FAILED) throw new Error('The native readback fence failed.');
        if (performance.now() - started >= timeoutMs) throw new Error('The native readback fence exceeded its bounded wait.');
        const yieldStarted = this.stamp(measured); measured.pollYields++;
        await new Promise<void>(resolve => setTimeout(resolve, 0));
        measured.pollYieldMs += this.stamp(measured) - yieldStarted;
      }
      measured.waitMs = performance.now() - started;
      measured.waitObservationMs = measured.waitMs; waitFinished = true;
      const extractStarted = performance.now();
      try {
        const images = this.withPackState(() => Array.from({length: this.count}, (_, index) => {
          current(); const slot = this.slots[index]!;
          const bottomStarted = this.stamp(measured);
          let bottom: Uint8Array<ArrayBuffer>;
          try {bottom = new Uint8Array(slot.bytes);}
          finally {measured.extractAllocationMs += this.stamp(measured) - bottomStarted;}
          const bindStarted = this.stamp(measured);
          try {gl.bindBuffer(gl.PIXEL_PACK_BUFFER, slot.buffer);}
          finally {measured.extractBufferBindMs += this.stamp(measured) - bindStarted;}
          const retrievalStarted = this.stamp(measured);
          try {gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, bottom);}
          finally {measured.extractGetBufferSubDataMs += this.stamp(measured) - retrievalStarted;}
          measured.retrievedCalls++; measured.retrievedBytes += bottom.byteLength;
          this.check('CPU retrieval', 'extract', measured);
          const topStarted = this.stamp(measured);
          let top: Uint8ClampedArray<ArrayBuffer>;
          try {top = new Uint8ClampedArray(bottom.length);}
          finally {measured.extractAllocationMs += this.stamp(measured) - topStarted;}
          const row = this.width * 4, flipStarted = this.stamp(measured);
          try {
            for (let y = 0; y < this.height; y++) top.set(bottom.subarray((this.height - y - 1) * row, (this.height - y) * row), y * row);
          } finally {measured.extractRowFlipMs += this.stamp(measured) - flipStarted;}
          const imageStarted = this.stamp(measured);
          try {return new ImageData(top, this.width, this.height);}
          finally {measured.extractImageDataMs += this.stamp(measured) - imageStarted;}
        }), 'extract', measured);
        current(); measured.extractMs = performance.now() - extractStarted; measured.completed = true;
        return {beauty: images[0]!, camera: images[1] ?? null};
      } finally {measured.extractObservationMs = this.stamp(measured) - extractStarted;}
    } catch (error) {measured.fallbackReason = error instanceof Error ? error.message : String(error); throw error;}
    finally {
      measured.waitMs ||= performance.now() - started;
      if (!waitFinished) measured.waitObservationMs = measured.waitMs;
      if (generation === this.generation) {this.pending = false; if (this.sync) gl.deleteSync(this.sync); this.sync = null;}
    }
  }
  private check(label: string, phase: 'submit' | 'wait' | 'extract', measured: PboReadbackMetrics): void {
    const started = this.stamp(measured);
    try {
      const error = this.gl.getError();
      if (error !== this.gl.NO_ERROR || this.gl.isContextLost()) throw new Error(`Native PBO ${label} failed (GL 0x${error.toString(16)}).`);
    } finally {measured[`${phase}CheckMs`] += this.stamp(measured) - started;}
  }
  private withPackState<T>(operation: () => T, phase: 'submit' | 'extract', measured: PboReadbackMetrics): T {
    const queryStarted = this.stamp(measured);
    const gl = this.gl, read = gl.getParameter(gl.READ_FRAMEBUFFER_BINDING) as WebGLFramebuffer | null;
    const pack = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING) as WebGLBuffer | null;
    const keys = [gl.PACK_ALIGNMENT, gl.PACK_ROW_LENGTH, gl.PACK_SKIP_PIXELS, gl.PACK_SKIP_ROWS];
    const values = keys.map(key => Number(gl.getParameter(key)));
    measured[`${phase}StateQueryMs`] += this.stamp(measured) - queryStarted;
    try {
      const setupStarted = this.stamp(measured);
      try {keys.forEach(key => gl.pixelStorei(key, key === gl.PACK_ALIGNMENT ? 1 : 0));}
      finally {measured[`${phase}StateSetupMs`] += this.stamp(measured) - setupStarted;}
      return operation();
    } finally {
      const restoreStarted = this.stamp(measured);
      try {
        gl.bindFramebuffer(gl.READ_FRAMEBUFFER, read); this.renderer.state.bindFramebuffer(gl.READ_FRAMEBUFFER, read);
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pack);
        keys.forEach((key, index) => gl.pixelStorei(key, values[index]!));
      } finally {measured[`${phase}StateRestoreMs`] += this.stamp(measured) - restoreStarted;}
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
