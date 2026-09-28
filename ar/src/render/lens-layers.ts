/** Ordered effective optical interactions over the nearest opaque scene surface.
 * Far-to-near depth peeling composes R*environment + T*behind per pixel, so
 * intersecting sheets need no object sorting. No camera image pixels are read.
 */
import {Color, DepthTexture, FloatType, GreaterDepth, LessEqualDepth, LinearSRGBColorSpace,
  Mesh, MeshPhysicalMaterial, NearestFilter, NoBlending, OrthographicCamera, PlaneGeometry,
  Scene, ShaderMaterial, Vector4, WebGLRenderTarget} from 'three';
import type {Camera, Texture, WebGLRenderer} from 'three';
import {canonicalLensRenderState} from './lens-material.ts';
import {OpticalOverflowChecker} from './layer-overflow.ts';
import {OpaqueDisplayCapture} from './opaque-display.ts';
import {EFFECTIVE_OPTICAL_GROUP_PROFILE} from '../eyewear/optical-material.ts';
import {NearestOpticalGroups} from './nearest-optical-groups.ts';

export const MAX_CANONICAL_OPTICAL_LAYERS = 4;

function target(): WebGLRenderTarget {
  const result = new WebGLRenderTarget(1, 1, {type: FloatType, minFilter: NearestFilter, magFilter: NearestFilter,
    depthBuffer: true, stencilBuffer: false});
  result.texture.colorSpace = LinearSRGBColorSpace; result.texture.generateMipmaps = false;
  result.depthTexture = new DepthTexture(1, 1, FloatType);
  return result;
}

export class CanonicalLensLayers {
  private readonly opaque = target();
  private readonly layers = [target(), target()];
  private readonly overflow = target();
  private readonly checker = new OpticalOverflowChecker();
  /** Owned unless shared: an asset with translucent frame materials shares its look-through image's capture, since a
   *  material has one capture owner (translucent-look-through.ts). A shared capture is never disposed here. */
  private readonly opaqueDisplay: OpaqueDisplayCapture;
  private readonly ownsOpaqueDisplay: boolean;
  private readonly scene = new Scene();
  private readonly copyScene = new Scene();
  private readonly copyCamera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly copyGeometry = new PlaneGeometry(2, 2);
  private readonly copyMaterial = new ShaderMaterial({
    name: 'Copy linear optical input and clear peel occupancy', depthTest: false, depthWrite: false,
    blending: NoBlending, toneMapped: false,
    uniforms: {image: {value: null as Texture | null}},
    vertexShader: 'varying vec2 vUv; void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}',
    fragmentShader: 'uniform sampler2D image; varying vec2 vUv; void main(){gl_FragColor=vec4(texture2D(image,vUv).rgb,0.0);}',
  });
  private readonly records: {source: Mesh; proxy: Mesh}[];
  private readonly materials: MeshPhysicalMaterial[];
  private readonly nearest: NearestOpticalGroups | null;
  private readonly groupIds = new Map<MeshPhysicalMaterial, string>();
  private disposed = false;

  constructor(meshes: readonly Mesh[], opaqueDisplay?: OpaqueDisplayCapture) {
    this.opaqueDisplay = opaqueDisplay ?? new OpaqueDisplayCapture(); this.ownsOpaqueDisplay = !opaqueDisplay;
    this.materials = [...new Set(meshes.map(mesh => {
      if (!(mesh.material instanceof MeshPhysicalMaterial)) throw new Error('Canonical optical layers require installed physical materials.');
      canonicalLensRenderState(mesh.material);
      return mesh.material;
    }))];
    const groups = new Map<string, Mesh[]>();
    for (const mesh of meshes) {
      const material = mesh.material as MeshPhysicalMaterial;
      if (material.userData.canonicalLensProfile !== EFFECTIVE_OPTICAL_GROUP_PROFILE) continue;
      const id: unknown = material.userData.canonicalOpticalGroupId;
      if (typeof id !== 'string' || !id.trim()) throw new Error('Effective optical material requires an explicit group ID.');
      if (mesh.userData.opticalGroupId !== id) throw new Error('Effective optical mesh and material group bindings differ.');
      this.groupIds.set(material, id);
      const members = groups.get(id) ?? []; members.push(mesh); groups.set(id, members);
    }
    this.nearest = groups.size ? new NearestOpticalGroups([...groups].map(([id, members]) => ({id, meshes: members}))) : null;
    // Own the opaque transport explicitly; Three must not start another hidden
    // transmission prepass while computing or displaying an optical peel.
    for (const material of this.materials) {material.transmission = 0; material.needsUpdate = true;}
    this.records = meshes.map(source => {
      const proxy = new Mesh(source.geometry, source.material);
      proxy.matrixAutoUpdate = false; proxy.frustumCulled = false;
      this.scene.add(proxy);
      return {source, proxy};
    });
    const quad = new Mesh(this.copyGeometry, this.copyMaterial); quad.frustumCulled = false; this.copyScene.add(quad);
  }

  /** Build the linear composed input once, before the ordinary guarded draw.
   * withOpaqueInput applies the same arm exclusions as legacy transmission.
   */
  render(renderer: WebGLRenderer, sourceScene: Scene, camera: Camera, width: number, height: number,
    withOpaqueInput: (draw: () => void) => void): void {
    if (this.disposed) throw new Error('Canonical optical layers are disposed.');
    if (!renderer.extensions.has('EXT_color_buffer_float')) throw new Error('Canonical optical layers require float color targets.');
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) throw new Error('Invalid optical viewport.');
    this.opaqueDisplay.prepare(sourceScene);
    for (const buffer of [this.opaque, ...this.layers, this.overflow]) if (buffer.width !== width || buffer.height !== height) buffer.setSize(width, height);
    const gl = renderer.getContext(), previous = {
      target: renderer.getRenderTarget(), cubeFace: renderer.getActiveCubeFace(), mip: renderer.getActiveMipmapLevel(),
      viewport: renderer.getViewport(new Vector4()), scissor: renderer.getScissor(new Vector4()), scissorTest: renderer.getScissorTest(),
      clear: renderer.getClearColor(new Color()), alpha: renderer.getClearAlpha(), autoClear: renderer.autoClear,
      xr: renderer.xr.enabled, depthClear: gl.getParameter(gl.DEPTH_CLEAR_VALUE) as number,
    };
    const materialVisibility = this.materials.map(material => material.visible);
    const states = this.materials.map(canonicalLensRenderState);
    let accumulated = this.opaque.texture;
    try {
      sourceScene.updateMatrixWorld(true);
      for (const {source, proxy} of this.records) {
        let visible = true;
        for (let object: typeof source.parent = source; object; object = object.parent) visible &&= object.visible;
        proxy.visible = visible; proxy.matrix.copy(source.matrixWorld);
        proxy.layers.mask = source.layers.mask; proxy.renderOrder = source.renderOrder;
      }
      // A lens mesh can parent another lens or an opaque component. Hiding
      // objects would also remove those descendants from the opaque input.
      for (const material of this.materials) material.visible = false;
      renderer.xr.enabled = false; renderer.autoClear = false; renderer.setScissorTest(false);
      renderer.setRenderTarget(this.opaque); renderer.setViewport(0, 0, width, height);
      renderer.state.buffers.depth.setClear(1); renderer.setClearColor(0, 0); renderer.clear(true, true, false);
      try {this.opaqueDisplay.withCapture(renderer, () => withOpaqueInput(() => renderer.render(sourceScene, camera)));}
      finally {this.materials.forEach((material, i) => {material.visible = materialVisibility[i]!;});}
      if (this.nearest) {
        this.nearest.capture(renderer, camera, width, height, material => {
          const state = canonicalLensRenderState(material), mode = state.uCanonicalLayerMode.value;
          const enabled = state.uCanonicalGroupEnabled.value, texture = state.uCanonicalGroupNearest.value;
          // Never bind the capture attachment as a sampler, even behind a
          // uniform branch that returns before sampling on the current draw.
          state.uCanonicalLayerMode.value = 3; state.uCanonicalGroupEnabled.value = false;
          state.uCanonicalGroupNearest.value = null;
          return () => {state.uCanonicalLayerMode.value = mode; state.uCanonicalGroupEnabled.value = enabled;
            state.uCanonicalGroupNearest.value = texture;};
        });
        for (const [material, id] of this.groupIds) {
          const state = canonicalLensRenderState(material);
          state.uCanonicalGroupNearest.value = this.nearest.texture(id); state.uCanonicalGroupEnabled.value = true;
        }
      }
      for (let layer = 0; layer <= MAX_CANONICAL_OPTICAL_LAYERS; layer++) {
        const output = layer === MAX_CANONICAL_OPTICAL_LAYERS ? this.overflow : this.layers[layer % 2]!;
        renderer.setRenderTarget(output); renderer.setViewport(0, 0, width, height);
        renderer.state.buffers.depth.setClear(0); renderer.clear(true, true, false);
        this.copyMaterial.uniforms.image!.value = accumulated;
        renderer.render(this.copyScene, this.copyCamera);
        for (let i = 0; i < this.materials.length; i++) {
          this.materials[i]!.depthFunc = GreaterDepth;
          const state = states[i]!;
          state.uCanonicalLayerMode.value = 1; state.uCanonicalLayerColor.value = accumulated;
          state.uCanonicalOpaqueDepth.value = this.opaque.depthTexture;
          state.uCanonicalLayerSize.value.set(width, height); state.uCanonicalFirstLayer.value = layer === 0;
        }
        renderer.render(this.scene, camera);
        if (layer === MAX_CANONICAL_OPTICAL_LAYERS) {
          this.checker.assertNoOverflow(renderer, output.texture, width, height, 'Visible lenses');
        } else accumulated = output.texture;
      }
    } finally {
      this.materials.forEach((material, i) => {material.visible = materialVisibility[i]!;});
      for (let i = 0; i < this.materials.length; i++) {
        this.materials[i]!.depthFunc = LessEqualDepth;
        states[i]!.uCanonicalLayerMode.value = 2; states[i]!.uCanonicalLayerColor.value = accumulated;
      }
      renderer.state.buffers.depth.setClear(previous.depthClear);
      renderer.setRenderTarget(previous.target, previous.cubeFace, previous.mip);
      renderer.setViewport(previous.viewport); renderer.setScissor(previous.scissor); renderer.setScissorTest(previous.scissorTest);
      renderer.setClearColor(previous.clear, previous.alpha); renderer.autoClear = previous.autoClear; renderer.xr.enabled = previous.xr;
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const buffer of [this.opaque, ...this.layers, this.overflow]) buffer.dispose();
    this.nearest?.dispose();
    if (this.ownsOpaqueDisplay) this.opaqueDisplay.dispose();
    this.checker.dispose(); this.copyMaterial.dispose(); this.copyGeometry.dispose(); this.scene.clear(); this.copyScene.clear();
    // Geometry and optical materials belong to the parent renderer.
  }
}
