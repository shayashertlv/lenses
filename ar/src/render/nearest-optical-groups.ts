/** One nearest effective interaction per explicit optical group.
 * Proxies borrow the actual geometry/material, so capture and peel use the same
 * vertex program. This helper neither infers identity nor owns source resources.
 */
import {Color, DepthTexture, FloatType, LessDepth, LinearSRGBColorSpace, Material, Mesh,
  NearestFilter, NoBlending, Scene, Vector4, WebGLRenderTarget} from 'three';
import type {Camera, Texture, WebGLRenderer} from 'three';

export const MAX_NEAREST_OPTICAL_GROUPS = 8;
export interface NearestOpticalGroup {readonly id: string; readonly meshes: readonly Mesh[];}

function target(id: string): WebGLRenderTarget {
  const result = new WebGLRenderTarget(1, 1, {type: FloatType, minFilter: NearestFilter,
    magFilter: NearestFilter, depthBuffer: true, stencilBuffer: false});
  result.texture.name = `Nearest optical group ${id}`;
  result.texture.colorSpace = LinearSRGBColorSpace; result.texture.generateMipmaps = false;
  result.depthTexture = new DepthTexture(1, 1, FloatType);
  return result;
}

function validateMesh(mesh: Mesh): asserts mesh is Mesh {
  if (!(mesh instanceof Mesh) || !(mesh.material instanceof Material) || 'isSkinnedMesh' in mesh
      || 'isInstancedMesh' in mesh || 'isBatchedMesh' in mesh || mesh.morphTargetInfluences?.length
      || Object.values(mesh.geometry.morphAttributes).some(attributes => attributes?.length)) {
    throw new Error('Nearest optical groups require ordinary static single-material meshes.');
  }
}

function rasterState(material: Material) {
  return {depthFunc: material.depthFunc, depthTest: material.depthTest, depthWrite: material.depthWrite,
    colorWrite: material.colorWrite, blending: material.blending, transparent: material.transparent,
    stencilWrite: material.stencilWrite, polygonOffset: material.polygonOffset,
    alphaToCoverage: material.alphaToCoverage, forceSinglePass: material.forceSinglePass};
}

export class NearestOpticalGroups {
  private readonly groups: {id: string; scene: Scene; target: WebGLRenderTarget;
    records: {source: Mesh; proxy: Mesh}[]}[];
  private disposed = false;
  private capturing = false;

  constructor(groups: readonly NearestOpticalGroup[]) {
    if (!Array.isArray(groups) || groups.length > MAX_NEAREST_OPTICAL_GROUPS) {
      throw new Error(`Nearest optical groups support at most ${MAX_NEAREST_OPTICAL_GROUPS} groups.`);
    }
    const ids = new Set<string>(), meshes = new Set<Mesh>();
    // Validate the complete inventory before allocating owned GPU resources.
    for (const group of groups) {
      if (!group || typeof group.id !== 'string' || !group.id.trim() || ids.has(group.id)
          || !Array.isArray(group.meshes) || !group.meshes.length) throw new Error('Invalid or duplicate optical group identity/membership.');
      ids.add(group.id);
      for (const mesh of group.meshes) {
        validateMesh(mesh);
        if (meshes.has(mesh)) throw new Error('Each optical mesh must belong to exactly one group.');
        meshes.add(mesh);
      }
    }
    this.groups = groups.map((group: NearestOpticalGroup) => {
      const scene = new Scene();
      const records = group.meshes.map(source => {
        const proxy = new Mesh(source.geometry, source.material);
        proxy.matrixAutoUpdate = false; proxy.frustumCulled = false; scene.add(proxy);
        return {source, proxy};
      });
      return {id: group.id, scene, records, target: target(group.id)};
    });
  }

  /** Empty pixels have alpha 1; LessDepth only captures depths in [0,1).
   * enterCapture must return its cleanup after successful entry. Every returned
   * cleanup runs even if another entry, draw or cleanup throws. A callback that
   * itself throws before returning remains responsible for its partial changes.
   */
  capture(renderer: WebGLRenderer, camera: Camera, width: number, height: number,
    enterCapture: (material: Material) => () => void): void {
    if (this.disposed) throw new Error('Nearest optical groups are disposed.');
    if (this.capturing) throw new Error('Nearest optical group capture is not reentrant.');
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) throw new Error('Invalid nearest optical viewport.');
    if (typeof enterCapture !== 'function') throw new Error('Nearest optical capture callback is required.');
    if (!renderer.extensions.has('EXT_color_buffer_float')) throw new Error('Nearest optical groups require float color targets.');
    for (const group of this.groups) for (const {source} of group.records) validateMesh(source);
    const gl = renderer.getContext(), previous = {
      target: renderer.getRenderTarget(), cubeFace: renderer.getActiveCubeFace(), mip: renderer.getActiveMipmapLevel(),
      viewport: renderer.getViewport(new Vector4()), scissor: renderer.getScissor(new Vector4()), scissorTest: renderer.getScissorTest(),
      color: renderer.getClearColor(new Color()), alpha: renderer.getClearAlpha(), autoClear: renderer.autoClear,
      xr: renderer.xr.enabled, depthClear: gl.getParameter(gl.DEPTH_CLEAR_VALUE) as number,
    };
    const errors: unknown[] = [];
    this.capturing = true;
    try {
      renderer.xr.enabled = false; renderer.autoClear = false; renderer.setScissorTest(false);
      for (const group of this.groups) {
        if (group.target.width !== width || group.target.height !== height) group.target.setSize(width, height);
        const visibleMaterials = new Set<Material>();
        for (const {source, proxy} of group.records) {
          source.updateWorldMatrix(true, false);
          let visible = true;
          for (let object: typeof source.parent = source; object; object = object.parent) visible &&= object.visible;
          const material = source.material as Material;
          proxy.geometry = source.geometry; proxy.material = material;
          proxy.matrix.copy(source.matrixWorld); proxy.layers.mask = source.layers.mask; proxy.renderOrder = source.renderOrder;
          proxy.visible = visible && material.visible && camera.layers.test(source.layers);
          if (proxy.visible) visibleMaterials.add(material);
        }
        const changes: {material: Material; state: ReturnType<typeof rasterState>; leave?: () => void}[] = [];
        try {
          for (const material of visibleMaterials) {
            const change = {material, state: rasterState(material), leave: undefined as (() => void) | undefined};
            changes.push(change);
            change.leave = enterCapture(material);
            if (typeof change.leave !== 'function') throw new Error('Nearest optical capture must return a restoration callback.');
            Object.assign(material, {depthFunc: LessDepth, depthTest: true, depthWrite: true, colorWrite: true,
              blending: NoBlending, transparent: false, stencilWrite: false, polygonOffset: false,
              alphaToCoverage: false, forceSinglePass: true});
          }
          renderer.setRenderTarget(group.target); renderer.setViewport(0, 0, width, height);
          renderer.state.buffers.depth.setClear(1); renderer.setClearColor(0, 1); renderer.clear(true, true, false);
          renderer.render(group.scene, camera);
        } catch (error) {errors.push(error);}
        finally {
          for (const change of changes.reverse()) {
            try {change.leave?.();} catch (error) {errors.push(error);}
            Object.assign(change.material, change.state);
          }
        }
        if (errors.length) break;
      }
    } catch (error) {errors.push(error);}
    finally {
      this.capturing = false;
      renderer.state.buffers.depth.setClear(previous.depthClear);
      renderer.setRenderTarget(previous.target, previous.cubeFace, previous.mip);
      renderer.setViewport(previous.viewport); renderer.setScissor(previous.scissor); renderer.setScissorTest(previous.scissorTest);
      renderer.setClearColor(previous.color, previous.alpha); renderer.autoClear = previous.autoClear; renderer.xr.enabled = previous.xr;
    }
    if (errors.length === 1) throw errors[0];
    if (errors.length) throw new AggregateError(errors, 'Nearest optical capture and restoration failed.');
  }

  texture(id: string): Texture {
    if (this.disposed) throw new Error('Nearest optical groups are disposed.');
    const group = this.groups.find(group => group.id === id);
    if (!group) throw new Error(`Unknown nearest optical group: ${id}`);
    return group.target.texture;
  }

  dispose(): void {
    if (this.disposed) return;
    if (this.capturing) throw new Error('Cannot dispose nearest optical groups during capture.');
    this.disposed = true;
    for (const group of this.groups) {group.target.dispose(); group.scene.clear();}
  }
}
