import {CanvasTexture, Material, Matrix3, Mesh, MeshPhysicalMaterial, SRGBColorSpace, Vector2} from 'three';
import type {Object3D} from 'three';

export const TEMPLE_BLEND_METHOD = 'temple-end-blend-v3';
export const TEMPLE_BLEND_LENGTH_LOCAL_M = 0.015;

/** Original GLB mesh-local meters; +Z is forward on the registered assets. */
interface TempleEndpoints {
  readonly negativeXCutoffLocalZM: number;
  readonly positiveXCutoffLocalZM: number;
}

/** Dissolves terminal frame RGB into the paired camera; it is not scene transparency. */
export interface TempleBlendConfiguration extends TempleEndpoints {
  readonly method: typeof TEMPLE_BLEND_METHOD;
  readonly fadeLengthLocalM: number;
}

export type TempleClipConfiguration = TempleBlendConfiguration;

export function validateTempleClip(value: TempleClipConfiguration): void {
  if (!value || typeof value !== 'object' || value.method !== TEMPLE_BLEND_METHOD
      || [value.negativeXCutoffLocalZM, value.positiveXCutoffLocalZM].some(cutoff =>
        !Number.isFinite(cutoff) || cutoff < -0.2 || cutoff > -0.03)
      || !Number.isFinite(value.fadeLengthLocalM) || value.fadeLengthLocalM <= 0 || value.fadeLengthLocalM > 0.02
      || [value.negativeXCutoffLocalZM, value.positiveXCutoffLocalZM].some(cutoff =>
        cutoff + value.fadeLengthLocalM > -0.03)) {
    throw new Error('The recorded temple clipping configuration is invalid.');
  }
}

export function createTempleBlendConfiguration(cutoffLocalZM: number): TempleBlendConfiguration {
  const configuration: TempleBlendConfiguration = {
    method: TEMPLE_BLEND_METHOD, negativeXCutoffLocalZM: cutoffLocalZM,
    positiveXCutoffLocalZM: cutoffLocalZM, fadeLengthLocalM: TEMPLE_BLEND_LENGTH_LOCAL_M,
  };
  validateTempleClip(configuration);
  return configuration;
}

/** Truncate rear stems and blend their terminal band into the camera without changing geometry, pose, or depth test. */
export function createTempleClip(root: Object3D) {
  const enabled = {value: 0};
  const negativeCutoff = {value: -0.09};
  const positiveCutoff = {value: -0.09};
  const fadeLength = {value: 0};
  // 0 off, 3 the v3 blend. Mode 3 changes terminal RGB only; overlay eligibility keeps its own alpha.
  const fadeMode = {value: 0};
  const cameraSource = {value: null as CanvasTexture | null};
  const cameraViewport = {value: new Vector2(1, 1)};
  const cameraUvTransform = {value: new Matrix3()};
  let configuration: TempleClipConfiguration | null = null;
  let disposed = false;
  const owned = new Map<Material, {hook: Material['onBeforeCompile']; key: Material['customProgramCacheKey']}>();
  root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) {
      if (owned.has(material) || material instanceof MeshPhysicalMaterial && material.transmission > 0) continue;
      const hook = material.onBeforeCompile;
      const key = material.customProgramCacheKey;
      owned.set(material, {hook, key});
      material.onBeforeCompile = function(shader, renderer) {
        hook.call(this, shader, renderer);
        shader.uniforms.templeClipEnabled = enabled;
        shader.uniforms.templeClipNegativeXCutoffZ = negativeCutoff;
        shader.uniforms.templeClipPositiveXCutoffZ = positiveCutoff;
        shader.uniforms.templeFadeLength = fadeLength;
        shader.uniforms.templeFadeMode = fadeMode;
        shader.uniforms.templeCameraSource = cameraSource;
        shader.uniforms.templeCameraViewport = cameraViewport;
        shader.uniforms.templeCameraUvTransform = cameraUvTransform;
        shader.vertexShader = 'varying vec2 templeOriginalXZ;\n' + shader.vertexShader.replace(
          '#include <begin_vertex>', '#include <begin_vertex>\ntempleOriginalXZ = position.xz;');
        shader.fragmentShader = 'varying vec2 templeOriginalXZ;\nuniform float templeClipEnabled;\n'
          + 'uniform float templeClipNegativeXCutoffZ;\nuniform float templeClipPositiveXCutoffZ;\n'
          + 'uniform float templeFadeLength;\nuniform float templeFadeMode;\n'
          + 'uniform sampler2D templeCameraSource;\nuniform vec2 templeCameraViewport;\n'
          + 'uniform mat3 templeCameraUvTransform;\n' + shader.fragmentShader.replace(
            '#include <clipping_planes_fragment>', '#include <clipping_planes_fragment>\n'
              + 'if (templeClipEnabled > 0.5 && templeOriginalXZ.y < '
              + '(templeOriginalXZ.x < 0.0 ? templeClipNegativeXCutoffZ : templeClipPositiveXCutoffZ)) discard;\n')
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
        ? hook.toString() : key.call(material)}|${TEMPLE_BLEND_METHOD}`;
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
      fadeLength.value = value === null ? 0 : value.fadeLengthLocalM;
      fadeMode.value = value === null ? 0 : 3;
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
        material.needsUpdate = true;
      }
      owned.clear();
    },
  };
}
