import type {WebGLRenderer} from 'three';

export interface NativeImagePair {beauty: ImageData; camera: ImageData;}
export interface SharedReadbackMetrics {
  mode: 'shared-context-two-readbacks' | 'shared-context-one-beauty-readback';
  beautyReadbackMs: number; cameraReadbackMs: number; readbackMs: number; extractionMs: number;
  readbackCalls: number; readbackBytes: number; allocatedBytes: number;
}
const emptyMetrics = (): SharedReadbackMetrics => ({mode: 'shared-context-two-readbacks',
  beautyReadbackMs: 0, cameraReadbackMs: 0, readbackMs: 0, extractionMs: 0,
  readbackCalls: 0, readbackBytes: 0, allocatedBytes: 0});

/** Reads each original antialiased default framebuffer before the next pass touches it. */
export class SharedNativeReadback {
  private readonly renderer: WebGLRenderer;
  private readonly gl: WebGL2RenderingContext;
  private bottomUp = new Uint8Array(0);
  private width = 0;
  private height = 0;
  private beauty: ImageData | null = null;
  private camera: ImageData | null = null;
  private disposed = false;
  private measured = emptyMetrics();

  constructor(renderer: WebGLRenderer) {
    this.renderer = renderer;
    const context = renderer.getContext();
    if (!('texStorage2D' in context)) throw new Error('Shared native camera capture requires WebGL 2.');
    this.gl = context;
  }
  get metrics(): SharedReadbackMetrics {return {...this.measured};}

  begin(width: number, height: number): void {
    this.beauty = this.camera = null; this.measured = emptyMetrics();
    if (this.disposed || this.gl.isContextLost()) throw new Error('The shared native context is unavailable.');
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0)
      throw new Error('Invalid shared native viewport.');
    if (this.renderer.getRenderTarget() !== null) throw new Error('Shared native capture requires the default framebuffer.');
    this.width = width; this.height = height;
    if (this.bottomUp.length !== width * height * 4) {
      this.bottomUp = new Uint8Array(width * height * 4);
      this.measured.allocatedBytes += this.bottomUp.byteLength;
    }
  }

  capture(slot: 0 | 1): void {
    if (this.disposed || this.width <= 0 || this.height <= 0 || this.renderer.getRenderTarget() !== null)
      throw new Error('The shared native readback is not prepared.');
    if (slot === 0 ? this.beauty !== null || this.camera !== null : this.beauty === null || this.camera !== null)
      throw new Error('Native captures must follow beauty then camera.');
    const gl = this.gl, started = performance.now();
    try {
      // Three's FRAMEBUFFER cache aliases DRAW, not READ. Force the actual read
      // binding, then align its cache, before consuming authoritative native bytes.
      gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
      this.renderer.state.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
      gl.readPixels(0, 0, this.width, this.height, gl.RGBA, gl.UNSIGNED_BYTE, this.bottomUp);
      this.measured.readbackCalls++; this.measured.readbackBytes += this.bottomUp.byteLength;
      const error = gl.getError();
      if (error !== gl.NO_ERROR || gl.isContextLost())
        throw new Error(`The shared native ${slot === 0 ? 'beauty' : 'camera'} readback failed (GL 0x${error.toString(16)}).`);
    } catch (error) {this.beauty = this.camera = null; throw error;}
    finally {
      const elapsed = performance.now() - started;
      this.measured.readbackMs += elapsed;
      if (slot === 0) this.measured.beautyReadbackMs += elapsed; else this.measured.cameraReadbackMs += elapsed;
    }
    const extractStarted = performance.now();
    try {
      const topDown = new Uint8ClampedArray(this.bottomUp.length), rowBytes = this.width * 4;
      this.measured.allocatedBytes += topDown.byteLength;
      for (let y = 0; y < this.height; y++)
        topDown.set(this.bottomUp.subarray((this.height - y - 1) * rowBytes, (this.height - y) * rowBytes), y * rowBytes);
      const image = new ImageData(topDown, this.width, this.height);
      if (slot === 0) this.beauty = image; else this.camera = image;
    } catch (error) {this.beauty = this.camera = null; throw error;}
    finally {this.measured.extractionMs += performance.now() - extractStarted;}
  }

  read(): NativeImagePair {
    if (this.disposed || !this.beauty || !this.camera) throw new Error('Both current native images are required before publication.');
    const pair = {beauty: this.beauty, camera: this.camera};
    this.beauty = this.camera = null;
    return pair;
  }

  readBeauty(): ImageData {
    if (this.disposed || !this.beauty) throw new Error('The current native beauty is unavailable.');
    this.measured.mode = 'shared-context-one-beauty-readback';
    const beauty = this.beauty; this.beauty = this.camera = null; return beauty;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.beauty = this.camera = null;
    this.width = this.height = 0; this.bottomUp = new Uint8Array(0);
  }
}
