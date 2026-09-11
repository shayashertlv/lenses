import {ACESFilmicToneMapping, CanvasTexture, LinearFilter, PerspectiveCamera, Scene, SRGBColorSpace, WebGLRenderer} from 'three';
import {VIRTUAL_CAMERA} from '../../references/perfect-temples/src/render/projection.ts';

/** Same native camera-only draw, retaining its texture and allocation between live frames. */
export class CleanCameraPass {
  private readonly canvas = document.createElement('canvas');
  private readonly gl: WebGL2RenderingContext;
  private readonly renderer: WebGLRenderer;
  private readonly scene = new Scene();
  private readonly camera = new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees, 1, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
  private texture: CanvasTexture | null = null;
  private sourceWidth = 0;
  private sourceHeight = 0;
  private readback = new Uint8Array(0);
  private disposed = false;
  constructor() {
    const gl = this.canvas.getContext('webgl2', {alpha: false, antialias: true, powerPreference: 'high-performance'});
    if (!gl) throw new Error('The independent clean camera WebGL2 context is unavailable.');
    this.gl = gl;
    try { this.renderer = new WebGLRenderer({canvas: this.canvas, context: gl, alpha: false, antialias: true}); }
    catch (error) { gl.getExtension('WEBGL_lose_context')?.loseContext(); throw error; }
    this.renderer.setPixelRatio(1); this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1;
  }
  present(source: HTMLCanvasElement, width: number, height: number): {pixels: Uint8ClampedArray; samples: number} {
    if (this.disposed || this.gl.isContextLost()) throw new Error('The clean camera context is closed or lost.');
    if (width !== Math.min(source.width, 1280) || height !== Math.max(1, Math.round(width * source.height / source.width))) {
      throw new Error('The saved viewport differs from the accepted camera sizing rule.');
    }
    if (this.canvas.width !== width || this.canvas.height !== height) this.renderer.setSize(width, height, false);
    if (this.camera.aspect !== source.width / source.height) {
      this.camera.aspect = source.width / source.height; this.camera.updateProjectionMatrix();
    }
    if (!this.texture || this.texture.image !== source || this.sourceWidth !== source.width || this.sourceHeight !== source.height) {
      this.texture?.dispose(); this.texture = new CanvasTexture(source); this.texture.colorSpace = SRGBColorSpace;
      this.texture.generateMipmaps = false; this.texture.minFilter = LinearFilter; this.texture.magFilter = LinearFilter;
      this.sourceWidth = source.width; this.sourceHeight = source.height;
    } else this.texture.needsUpdate = true;
    this.scene.background = this.texture; this.renderer.render(this.scene, this.camera);
    if (this.readback.length !== width * height * 4) this.readback = new Uint8Array(width * height * 4);
    const bottomUp = this.readback, pixels = new Uint8ClampedArray(bottomUp.length);
    this.gl.readPixels(0, 0, width, height, this.gl.RGBA, this.gl.UNSIGNED_BYTE, bottomUp);
    if (this.gl.getError() !== this.gl.NO_ERROR) throw new Error('The clean camera readback failed.');
    for (let y = 0; y < height; y++) pixels.set(bottomUp.subarray((height - y - 1) * width * 4, (height - y) * width * 4), y * width * 4);
    return {pixels, samples: this.gl.getParameter(this.gl.SAMPLES) as number};
  }
  dispose(): void { if (this.disposed) return; this.disposed = true; this.scene.background = null; this.texture?.dispose(); this.texture = null;
    this.renderer.dispose(); this.renderer.forceContextLoss(); this.canvas.width = this.canvas.height = 0;
    this.readback = new Uint8Array(0); }
}
