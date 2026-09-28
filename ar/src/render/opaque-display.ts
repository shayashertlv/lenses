/** Preserve opaque display RGB while capturing the input behind optical sheets.
 * Three omits native tone mapping for ordinary offscreen targets. Canonical lens
 * display is already composed and toneMapped=false, so its opaque input needs
 * the same ACES response the frame would receive in the ordinary screen draw.
 * This helper does not encode sRGB, touch scene.background, or alter optics.
 */
import {ACESFilmicToneMapping, Material, NoToneMapping, RawShaderMaterial, ShaderChunk} from 'three';
import type {Object3D, WebGLRenderer} from 'three';

const MARKER = '#include <tonemapping_fragment>';
const CACHE_TAG = 'opaque-display-capture-v1';
const owners = new WeakMap<Material, OpaqueDisplayCapture>();

interface CaptureRecord {
  hook: Material['onBeforeCompile'];
  key: Material['customProgramCacheKey'];
  wrapper: Material['onBeforeCompile'];
  wrapperKey: Material['customProgramCacheKey'];
  enabled: {value: number};
}

export class OpaqueDisplayCapture {
  private readonly records = new Map<Material, CaptureRecord>();
  private readonly exposure = {value: 1};
  private active = false;
  private disposed = false;

  /** Call after existing material adapters. Repeated calls discover newly added
   * scene materials without recompiling already prepared materials. Hidden
   * objects are included for later visibility changes; colorWrite=false and
   * toneMapped=false materials, including canonical optics, are excluded.
   */
  prepare(scene: Object3D): void {
    if (this.disposed) throw new Error('Opaque display capture is disposed.');
    const pending = new Set<Material>();
    scene.traverse(object => {
      if (!('material' in object)) return;
      const values = Array.isArray(object.material) ? object.material : [object.material];
      for (const value of values) if (value instanceof Material && value.colorWrite && value.toneMapped && !this.records.has(value)) {
        if (value instanceof RawShaderMaterial) throw new Error('Opaque display capture does not support raw shader materials.');
        if (owners.has(value)) throw new Error('Opaque display material already belongs to another capture helper.');
        pending.add(value);
      }
    });
    for (const material of pending) {
      const hook = material.onBeforeCompile, key = material.customProgramCacheKey;
      const enabled = {value: this.active ? 1 : 0};
      const capture = this;
      const wrapper: Material['onBeforeCompile'] = function(this: Material, shader, renderer) {
        hook.call(this, shader, renderer);
        // A later owner's wrapper may retain this hook after disposal. In that
        // case delegate only; do not reinstall a disposed capture transform.
        if (capture.disposed) return;
        if (shader.fragmentShader.split(MARKER).length !== 2) {
          throw new Error('Opaque display capture requires exactly one tonemapping_fragment marker after existing hooks.');
        }
        if (shader.fragmentShader.includes('#include <tonemapping_pars_fragment>')) {
          throw new Error('Opaque display capture cannot combine custom tone-mapping declarations.');
        }
        Object.assign(shader.uniforms, {uOpaqueDisplayCapture: enabled, uOpaqueDisplayExposure: capture.exposure});
        // Native screen programs already receive the pinned Three declarations.
        // Alias only the fallback chunk's exposure, so renderer-owned native
        // toneMappingExposure and helper-owned capture exposure never conflict.
        shader.fragmentShader = `
          #ifndef TONE_MAPPING
            #define toneMappingExposure uOpaqueDisplayExposure
            ${ShaderChunk.tonemapping_pars_fragment}
            #undef toneMappingExposure
          #endif
          uniform float uOpaqueDisplayCapture;
        ` + shader.fragmentShader.replace(MARKER, `
          #ifdef TONE_MAPPING
            ${MARKER}
          #else
            if (uOpaqueDisplayCapture > 0.5) gl_FragColor.rgb = ACESFilmicToneMapping(gl_FragColor.rgb);
          #endif
        `);
      };
      const wrapperKey = () => `${key === Material.prototype.customProgramCacheKey ? hook.toString() : key.call(material)}|${CACHE_TAG}`;
      this.records.set(material, {hook, key, wrapper, wrapperKey, enabled});
      owners.set(material, this);
      material.onBeforeCompile = wrapper; material.customProgramCacheKey = wrapperKey; material.needsUpdate = true;
    }
  }

  /** Synchronous capture scope only. The normal screen branch still uses native
   * tone mapping exactly once. Other offscreen draws keep this transform off.
   * ACES and NoToneMapping are explicit supported policies; any other mapping
   * needs its own tested display contract. Uniform changes do not recompile.
   */
  withCapture<T>(renderer: WebGLRenderer, draw: () => T): T {
    if (this.disposed) throw new Error('Opaque display capture is disposed.');
    if (renderer.toneMapping !== ACESFilmicToneMapping && renderer.toneMapping !== NoToneMapping) {
      throw new Error('Opaque display capture supports only ACESFilmicToneMapping or NoToneMapping.');
    }
    if (!Number.isFinite(renderer.toneMappingExposure) || renderer.toneMappingExposure < 0) {
      throw new Error('Opaque display capture requires finite nonnegative exposure.');
    }
    const previousActive = this.active, previousExposure = this.exposure.value;
    const previous = new Map([...this.records].map(([material, record]) => [material, record.enabled.value]));
    this.active = renderer.toneMapping === ACESFilmicToneMapping;
    this.exposure.value = renderer.toneMappingExposure;
    for (const [material, record] of this.records) record.enabled.value = this.active && material.colorWrite && material.toneMapped ? 1 : 0;
    try {
      return draw();
    } finally {
      this.active = previousActive; this.exposure.value = previousExposure;
      for (const [material, record] of this.records) record.enabled.value = previous.get(material)
        ?? (previousActive && material.colorWrite && material.toneMapped ? 1 : 0);
    }
  }

  /** Restore only hooks still owned by this helper; later material adapters must
   * not be overwritten. Borrowed materials and textures are never disposed.
   */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.active = false;
    for (const [material, record] of this.records) {
      record.enabled.value = 0;
      let restored = false;
      if (material.onBeforeCompile === record.wrapper) {material.onBeforeCompile = record.hook; restored = true;}
      if (material.customProgramCacheKey === record.wrapperKey) {material.customProgramCacheKey = record.key; restored = true;}
      if (owners.get(material) === this) owners.delete(material);
      if (restored) material.needsUpdate = true;
    }
    this.records.clear();
  }
}
