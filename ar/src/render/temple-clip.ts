import {CanvasTexture, Material, Matrix3, Mesh, MeshPhysicalMaterial, SRGBColorSpace, Vector2} from 'three';
import type {Object3D} from 'three';

export const TEMPLE_CLIP_METHOD = 'temple-end-clip-v1';
export const TEMPLE_FADE_METHOD = 'temple-end-fade-v2';
export const TEMPLE_FADE_LENGTH_LOCAL_M = 0.004;
export const TEMPLE_BLEND_METHOD = 'temple-end-blend-v3';
export const TEMPLE_BLEND_LENGTH_LOCAL_M = 0.015;

/** Original GLB mesh-local meters; +Z is forward on the registered assets. */
interface TempleEndpoints {
  readonly negativeXCutoffLocalZM: number;
  readonly positiveXCutoffLocalZM: number;
}

export interface TempleHardClipConfiguration extends TempleEndpoints {
  readonly method: typeof TEMPLE_CLIP_METHOD;
}

export interface TempleFadeConfiguration extends TempleEndpoints {
  readonly method: typeof TEMPLE_FADE_METHOD;
  readonly fadeLengthLocalM: number;
  readonly coverage: 'alpha-to-coverage' | 'ordered-dither';
}

/** Dissolves terminal frame RGB into the paired camera; it is not scene transparency. */
export interface TempleBlendConfiguration extends TempleEndpoints {
  readonly method: typeof TEMPLE_BLEND_METHOD;
  readonly fadeLengthLocalM: number;
}

export type TempleClipConfiguration = TempleHardClipConfiguration | TempleFadeConfiguration | TempleBlendConfiguration;

export function validateTempleClip(value: TempleClipConfiguration): void {
  if (!value || typeof value !== 'object'
      || value.method !== TEMPLE_CLIP_METHOD && value.method !== TEMPLE_FADE_METHOD && value.method !== TEMPLE_BLEND_METHOD
      || [value.negativeXCutoffLocalZM, value.positiveXCutoffLocalZM].some(cutoff =>
        !Number.isFinite(cutoff) || cutoff < -0.2 || cutoff > -0.03)
      || value.method === TEMPLE_FADE_METHOD && (
        !Number.isFinite(value.fadeLengthLocalM) || value.fadeLengthLocalM < 0 || value.fadeLengthLocalM > 0.02
        || value.coverage !== 'alpha-to-coverage' && value.coverage !== 'ordered-dither'
        || [value.negativeXCutoffLocalZM, value.positiveXCutoffLocalZM].some(cutoff =>
          cutoff + value.fadeLengthLocalM > -0.03))
      || value.method === TEMPLE_BLEND_METHOD && (
        !Number.isFinite(value.fadeLengthLocalM) || value.fadeLengthLocalM <= 0 || value.fadeLengthLocalM > 0.02
        || [value.negativeXCutoffLocalZM, value.positiveXCutoffLocalZM].some(cutoff =>
          cutoff + value.fadeLengthLocalM > -0.03))) {
    throw new Error('The recorded temple clipping configuration is invalid.');
  }
}

/** The chosen coverage method becomes part of the recorded rendering policy. */
export function createTempleFadeConfiguration(cutoffLocalZM: number, nativeSamples: number): TempleFadeConfiguration {
  if (!Number.isInteger(nativeSamples) || nativeSamples < 0) throw new Error('The native sample count is invalid.');
  const configuration: TempleFadeConfiguration = {
    method: TEMPLE_FADE_METHOD,
    negativeXCutoffLocalZM: cutoffLocalZM, positiveXCutoffLocalZM: cutoffLocalZM,
    fadeLengthLocalM: TEMPLE_FADE_LENGTH_LOCAL_M,
    coverage: nativeSamples > 0 ? 'alpha-to-coverage' : 'ordered-dither',
  };
  validateTempleClip(configuration);
  return configuration;
}

export function createTempleBlendConfiguration(cutoffLocalZM: number): TempleBlendConfiguration {
  const configuration: TempleBlendConfiguration = {
    method: TEMPLE_BLEND_METHOD, negativeXCutoffLocalZM: cutoffLocalZM,
    positiveXCutoffLocalZM: cutoffLocalZM, fadeLengthLocalM: TEMPLE_BLEND_LENGTH_LOCAL_M,
  };
  validateTempleClip(configuration);
  return configuration;
}

/** Truncate/fade rear stems without changing their geometry, pose, or depth test. */
export function createTempleClip(root: Object3D) {
  const enabled = {value: 0};
  const negativeCutoff = {value: -0.09};
  const positiveCutoff = {value: -0.09};
  const fadeLength = {value: 0};
  // Historical modes: 0 hard clip, 1 A2C, 2 deterministic 4x4 coverage.
  // Mode 3 changes terminal RGB only; overlay eligibility keeps its own alpha.
  const fadeMode = {value: 0};
  const cameraSource = {value: null as CanvasTexture | null};
  const cameraViewport = {value: new Vector2(1, 1)};
  const cameraUvTransform = {value: new Matrix3()};
  const ditherThresholds = {value: new Float32Array([
    0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5,
  ].map(rank => (rank + 0.5) / 16))};
  let configuration: TempleClipConfiguration | null = null;
  let disposed = false;
  const owned = new Map<Material, {
    hook: Material['onBeforeCompile']; key: Material['customProgramCacheKey'];
    alphaToCoverage: boolean;
  }>();
  root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) {
      if (owned.has(material) || material instanceof MeshPhysicalMaterial && material.transmission > 0) continue;
      const hook = material.onBeforeCompile;
      const key = material.customProgramCacheKey;
      owned.set(material, {hook, key, alphaToCoverage: material.alphaToCoverage});
      material.onBeforeCompile = function(shader, renderer) {
        hook.call(this, shader, renderer);
        shader.uniforms.templeClipEnabled = enabled;
        shader.uniforms.templeClipNegativeXCutoffZ = negativeCutoff;
        shader.uniforms.templeClipPositiveXCutoffZ = positiveCutoff;
        shader.uniforms.templeFadeLength = fadeLength;
        shader.uniforms.templeFadeMode = fadeMode;
        shader.uniforms.templeFadeDitherThresholds = ditherThresholds;
        shader.uniforms.templeCameraSource = cameraSource;
        shader.uniforms.templeCameraViewport = cameraViewport;
        shader.uniforms.templeCameraUvTransform = cameraUvTransform;
        shader.vertexShader = 'varying vec2 templeOriginalXZ;\n' + shader.vertexShader.replace(
          '#include <begin_vertex>', '#include <begin_vertex>\ntempleOriginalXZ = position.xz;');
        shader.fragmentShader = 'varying vec2 templeOriginalXZ;\nuniform float templeClipEnabled;\n'
          + 'uniform float templeClipNegativeXCutoffZ;\nuniform float templeClipPositiveXCutoffZ;\n'
          + 'uniform float templeFadeLength;\nuniform float templeFadeMode;\n'
          + 'uniform float templeFadeDitherThresholds[16];\n'
          + 'uniform sampler2D templeCameraSource;\nuniform vec2 templeCameraViewport;\n'
          + 'uniform mat3 templeCameraUvTransform;\n' + shader.fragmentShader.replace(
            '#include <clipping_planes_fragment>', '#include <clipping_planes_fragment>\n'
              + 'if (templeClipEnabled > 0.5 && templeOriginalXZ.y < '
              + '(templeOriginalXZ.x < 0.0 ? templeClipNegativeXCutoffZ : templeClipPositiveXCutoffZ)) discard;\n'
              + 'float templeEndpointCoverage = 1.0;\n'
              + 'if (templeClipEnabled > 0.5 && templeFadeMode > 0.5 && templeFadeMode < 2.5 && templeFadeLength > 0.0) {\n'
              + '  float templeEndpointZ = templeOriginalXZ.x < 0.0 ? templeClipNegativeXCutoffZ : templeClipPositiveXCutoffZ;\n'
              + '  templeEndpointCoverage = smoothstep(templeEndpointZ, templeEndpointZ + templeFadeLength, templeOriginalXZ.y);\n'
              + '  if (templeFadeMode > 1.5) {\n'
              + '    int templeDitherIndex = int(mod(floor(gl_FragCoord.x), 4.0) + 4.0 * mod(floor(gl_FragCoord.y), 4.0));\n'
              + '    if (templeEndpointCoverage < templeFadeDitherThresholds[templeDitherIndex]) discard;\n'
              + '  }\n'
              + '}\n').replace('#include <opaque_fragment>', '#include <opaque_fragment>\n'
                // A2C removes Three's OPAQUE define. Restore full frame opacity
                // outside the tail instead of exposing previously ignored map alpha.
                + 'if (templeFadeMode > 0.5 && templeFadeMode < 1.5) gl_FragColor.a = templeEndpointCoverage;')
          .replace('#include <dithering_fragment>', '#include <dithering_fragment>\n'
            // Visibility wrappers insert their own code after this same include,
            // before this footer. Preserve their coverage alpha and tested depth.
            + 'if (templeClipEnabled > 0.5 && templeFadeMode > 2.5) {\n'
            + '  float templeBlendEndpointZ = templeOriginalXZ.x < 0.0 ? templeClipNegativeXCutoffZ : templeClipPositiveXCutoffZ;\n'
            + '  if (templeOriginalXZ.y < templeBlendEndpointZ + templeFadeLength) {\n'
            + '    float templeBlendWeight = smoothstep(templeBlendEndpointZ, templeBlendEndpointZ + templeFadeLength, templeOriginalXZ.y);\n'
            + '    vec2 templeCameraUV = (templeCameraUvTransform * vec3(gl_FragCoord.xy / templeCameraViewport, 1.0)).xy;\n'
            // CanvasTexture sRGB samples are decoded by the GPU. Match the
            // current output encoding without tone-mapping camera pixels.
            + '    vec3 templeCameraRGB = linearToOutputTexel(texture2D(templeCameraSource, templeCameraUV)).rgb;\n'
            + '    gl_FragColor.rgb = mix(templeCameraRGB, gl_FragColor.rgb, templeBlendWeight);\n'
            + '  }\n'
            + '}');
      };
      // Three's default key reads the hook itself; a caller's custom key may
      // depend on changing material state and must continue to be evaluated.
      material.customProgramCacheKey = () => `${key === Material.prototype.customProgramCacheKey
        ? hook.toString() : key.call(material)}|${TEMPLE_CLIP_METHOD}|${TEMPLE_FADE_METHOD}|${TEMPLE_BLEND_METHOD}`;
      material.needsUpdate = true;
    }
  });
  return {
    get configuration(): TempleClipConfiguration | null { return configuration ? {...configuration} : null; },
    set(value: TempleClipConfiguration | null): void {
      if (disposed) throw new Error('Temple clipping is disposed.');
      if (value !== null) validateTempleClip(value);
      configuration = value === null ? null : {...value};
      enabled.value = value === null ? 0 : 1;
      fadeLength.value = value?.method === TEMPLE_FADE_METHOD || value?.method === TEMPLE_BLEND_METHOD ? value.fadeLengthLocalM : 0;
      fadeMode.value = value?.method === TEMPLE_BLEND_METHOD ? 3
        : value?.method === TEMPLE_FADE_METHOD && value.fadeLengthLocalM > 0
          ? value.coverage === 'alpha-to-coverage' ? 1 : 2 : 0;
      cameraSource.value = null;
      if (value !== null) {
        negativeCutoff.value = value.negativeXCutoffLocalZM;
        positiveCutoff.value = value.positiveXCutoffLocalZM;
      }
    },
    /** V3 borrows the exact background texture and full render viewport for this frame. */
    prepareRender(cameraTexture?: CanvasTexture, width?: number, height?: number): void {
      if (disposed) throw new Error('Temple clipping is disposed.');
      if (fadeMode.value === 3) {
        const image = cameraTexture?.image as {width?: number; height?: number} | undefined;
        if (!(cameraTexture instanceof CanvasTexture) || cameraTexture.colorSpace !== SRGBColorSpace
            || !image || !Number.isFinite(image.width) || !Number.isFinite(image.height)
            || !(image.width! > 0) || !(image.height! > 0)
            || !Number.isInteger(width) || !Number.isInteger(height) || !(width! > 0) || !(height! > 0)) {
          throw new Error('Temple blending requires the paired sRGB camera texture and valid render dimensions.');
        }
        if (cameraTexture.matrixAutoUpdate) cameraTexture.updateMatrix();
        if (!cameraTexture.matrix.elements.every(Number.isFinite)) throw new Error('The paired camera UV transform is invalid.');
        cameraSource.value = cameraTexture;
        cameraViewport.value.set(width!, height!);
        cameraUvTransform.value.copy(cameraTexture.matrix);
      }
      for (const [material, previous] of owned) {
        const alphaToCoverage = fadeMode.value === 1 ? true : previous.alphaToCoverage;
        if (material.alphaToCoverage !== alphaToCoverage) {
          material.alphaToCoverage = alphaToCoverage;
          material.needsUpdate = true;
        }
      }
    },
    dispose(): void {
      if (disposed) return;
      disposed = true;
      configuration = null;
      enabled.value = 0;
      fadeLength.value = 0;
      fadeMode.value = 0;
      cameraSource.value = null;
      for (const [material, previous] of owned) {
        material.onBeforeCompile = previous.hook;
        material.customProgramCacheKey = previous.key;
        material.alphaToCoverage = previous.alphaToCoverage;
        material.needsUpdate = true;
      }
      owned.clear();
    },
  };
}
