/** Reduce only a generated optical-capacity flag; never read camera image pixels.
 * Immediate detection costs a bounded reduction pyramid and a 1-pixel GPU read.
 * Performance across mobile devices remains a separate validation requirement.
 */
import {Color, NearestFilter, NoBlending, OrthographicCamera, PlaneGeometry, Scene, ShaderMaterial,
  Mesh, Vector2, Vector4, WebGLRenderTarget} from 'three';
import type {Texture, WebGLRenderer} from 'three';

export class OpticalLayerOverflowError extends Error {
  constructor(label: string) {super(`${label}: optical layer capacity exceeded`); this.name = 'OpticalLayerOverflowError';}
}

/** Reusable checker. Input alpha is either positive occupancy, or exact depth
 * cleared to 1 (one_minus_alpha). Every nonzero input becomes a binary flag
 * before conversion to RGBA8; small positive float depths cannot round to zero.
 */
export class OpticalOverflowChecker {
  private readonly scene = new Scene();
  private readonly camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly geometry = new PlaneGeometry(2, 2);
  private readonly material = new ShaderMaterial({
    name: 'Optical layer overflow flag reduction', blending: NoBlending, depthTest: false, depthWrite: false, toneMapped: false,
    uniforms: {inputImage: {value: null as Texture | null}, inputSize: {value: new Vector2()}, invertAlpha: {value: 0}},
    vertexShader: 'void main() { gl_Position = vec4(position.xy, 0.0, 1.0); }',
    fragmentShader: `
      uniform sampler2D inputImage;
      uniform vec2 inputSize;
      uniform float invertAlpha;
      float occupied(vec2 pixel) {
        if (pixel.x >= inputSize.x || pixel.y >= inputSize.y) return 0.0;
        float alpha = texture2D(inputImage, pixel / inputSize).a;
        float value = invertAlpha > 0.5 ? 1.0 - alpha : alpha;
        return value > 0.0 ? 1.0 : 0.0;
      }
      void main() {
        vec2 first = floor(gl_FragCoord.xy) * 2.0 + vec2(0.5);
        float flag = max(max(occupied(first), occupied(first + vec2(1.0, 0.0))),
          max(occupied(first + vec2(0.0, 1.0)), occupied(first + vec2(1.0, 1.0))));
        gl_FragColor = vec4(0.0, 0.0, 0.0, flag);
      }`,
  });
  private readonly result = new Uint8Array(4);
  private targets: WebGLRenderTarget[] = [];
  private width = 0;
  private height = 0;
  private disposed = false;

  constructor() {
    const quad = new Mesh(this.geometry, this.material); quad.frustumCulled = false; this.scene.add(quad);
  }

  assertNoOverflow(renderer: WebGLRenderer, texture: Texture, width: number, height: number, label: string,
    channel: 'alpha' | 'one_minus_alpha' = 'alpha'): void {
    if (this.disposed) throw new Error('Optical overflow checker is disposed.');
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
      throw new Error('Optical overflow input dimensions must be positive integers.');
    }
    if (width !== this.width || height !== this.height) {
      for (const target of this.targets) target.dispose();
      this.targets = []; this.width = width; this.height = height;
      let w = width, h = height;
      do {
        w = Math.max(1, Math.ceil(w / 2)); h = Math.max(1, Math.ceil(h / 2));
        const target = new WebGLRenderTarget(w, h, {minFilter: NearestFilter, magFilter: NearestFilter,
          depthBuffer: false, stencilBuffer: false});
        target.texture.name = 'Generated optical overflow flag'; target.texture.generateMipmaps = false;
        this.targets.push(target);
      } while (w > 1 || h > 1);
    }
    const saved = {target: renderer.getRenderTarget(), face: renderer.getActiveCubeFace(), level: renderer.getActiveMipmapLevel(),
      viewport: renderer.getViewport(new Vector4()), scissor: renderer.getScissor(new Vector4()), scissorTest: renderer.getScissorTest(),
      clearColor: renderer.getClearColor(new Color()), clearAlpha: renderer.getClearAlpha(), autoClear: renderer.autoClear,
      xr: renderer.xr.enabled};
    try {
      renderer.xr.enabled = false; renderer.autoClear = false; renderer.setScissorTest(false);
      let input = texture, w = width, h = height;
      for (let index = 0; index < this.targets.length; index++) {
        const target = this.targets[index]!;
        this.material.uniforms.inputImage!.value = input;
        (this.material.uniforms.inputSize!.value as Vector2).set(w, h);
        this.material.uniforms.invertAlpha!.value = index === 0 && channel === 'one_minus_alpha' ? 1 : 0;
        renderer.setRenderTarget(target); renderer.render(this.scene, this.camera);
        input = target.texture; w = target.width; h = target.height;
      }
      // WebGLRenderer reports unsupported readbacks through its error path;
      // an untouched buffer must not masquerade as an empty overflow target.
      this.result.fill(255);
      renderer.readRenderTargetPixels(this.targets[this.targets.length - 1]!, 0, 0, 1, 1, this.result);
      if (this.result[3]! > 0) throw new OpticalLayerOverflowError(label);
    } finally {
      this.material.uniforms.inputImage!.value = null;
      renderer.setRenderTarget(saved.target, saved.face, saved.level);
      renderer.setViewport(saved.viewport); renderer.setScissor(saved.scissor); renderer.setScissorTest(saved.scissorTest);
      renderer.setClearColor(saved.clearColor, saved.clearAlpha); renderer.autoClear = saved.autoClear; renderer.xr.enabled = saved.xr;
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const target of this.targets) target.dispose();
    this.targets = []; this.material.dispose(); this.geometry.dispose(); this.scene.clear();
  }
}
