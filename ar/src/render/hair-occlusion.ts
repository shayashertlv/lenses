/** Hair occlusion inside the eyewear fragment shaders: temple fragments behind `startZ` (mesh-local metres, +Z
 *  forward) blend toward the paired camera texture by the hair mask sampled at their screen position. The 0/255 mask
 *  stays nearest (renderer.ts). An optional small tent filter softens only the rendered edge, leaving category
 *  evidence for the endpoint tracker unchanged. */
import {Material, Matrix3, Mesh, MeshPhysicalMaterial, Vector2} from 'three';
import type {Texture, DataTexture, Object3D} from 'three';

export const HAIR_OCCLUSION_METHOD = 'hair-gpu-occlusion-v1';
/** Mesh-local metres (+Z forward): fragments behind this plane count as temple arm and may blend toward the camera. */
export const DEFAULT_HAIR_START_Z_M = -0.02;

/** Wraps the non-lens eyewear materials; install after the temple clip and visibility hooks so it wraps them. */
export function createHairOcclusion(root: Object3D) {
  const enabled = {value: 0}, startZ = {value: DEFAULT_HAIR_START_Z_M};
  const mask = {value: null as DataTexture | null}, viewport = {value: new Vector2(1, 1)};
  const feather = {value: 0};
  // A mask reused from an earlier frame is looked up through this matrix (see hair/mask-reuse.ts); off, the lookup is
  // exactly the frame's own mask as before.
  const maskWarp = {value: 0}, maskUv = {value: new Matrix3()};
  const cameraSource = {value: null as Texture | null}, cameraUv = {value: new Matrix3()};
  const owned = new Map<Material, {hook: Material['onBeforeCompile']; key: Material['customProgramCacheKey']}>();
  root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) {
      if (owned.has(material) || material instanceof MeshPhysicalMaterial && material.transmission > 0) continue;
      const hook = material.onBeforeCompile, key = material.customProgramCacheKey;
      owned.set(material, {hook, key});
      material.onBeforeCompile = function(shader, renderer) {
        hook.call(this, shader, renderer);
        shader.uniforms.hairOcclusionEnabled = enabled; shader.uniforms.hairOcclusionStartZ = startZ;
        shader.uniforms.hairOcclusionMask = mask; shader.uniforms.hairOcclusionViewport = viewport;
        shader.uniforms.hairOcclusionFeatherPx = feather;
        shader.uniforms.hairOcclusionCamera = cameraSource; shader.uniforms.hairOcclusionCameraUv = cameraUv;
        shader.uniforms.hairOcclusionMaskWarp = maskWarp; shader.uniforms.hairOcclusionMaskUv = maskUv;
        shader.vertexShader = 'varying vec2 hairLocalXZ;\n' + shader.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\nhairLocalXZ = position.xz;');
        shader.fragmentShader = 'varying vec2 hairLocalXZ;\nuniform float hairOcclusionEnabled;\nuniform float hairOcclusionStartZ;\n'
          + 'uniform sampler2D hairOcclusionMask;\nuniform vec2 hairOcclusionViewport;\nuniform sampler2D hairOcclusionCamera;\nuniform mat3 hairOcclusionCameraUv;\n'
          + 'uniform float hairOcclusionMaskWarp;\nuniform mat3 hairOcclusionMaskUv;\n'
          + 'uniform float hairOcclusionFeatherPx;\n'
          // Offsets are measured in the drawn frame, then each tap follows that frame's mask warp.
          // Testing every tap prevents a clamp-to-edge texel from extending hair beyond a reused frame.
          + 'float sampleHairOcclusion(vec2 screenUV) {\n'
          + '  vec2 lookupUV = hairOcclusionMaskWarp > 0.5 ? (hairOcclusionMaskUv * vec3(screenUV, 1.0)).xy : screenUV;\n'
          + '  float inside = step(0.0, lookupUV.x) * step(lookupUV.x, 1.0) * step(0.0, lookupUV.y) * step(lookupUV.y, 1.0);\n'
          + '  return texture2D(hairOcclusionMask, lookupUV).r * inside;\n'
          + '}\n'
          + shader.fragmentShader.replace('#include <dithering_fragment>', '#include <dithering_fragment>\n'
            + 'if (hairOcclusionEnabled > 0.5 && hairLocalXZ.y < hairOcclusionStartZ) {\n'
            + '  vec2 hairScreen = gl_FragCoord.xy / hairOcclusionViewport;\n'
            // The mask is row-major from the top-left; the framebuffer origin is bottom-left.
            + '  vec2 hairMaskUV = vec2(hairScreen.x, 1.0 - hairScreen.y);\n'
            // A reused mask: the drawn pixel's place in the mask's own frame; outside that frame there is no hair.
            + '  vec2 hairWarpedUV = (hairOcclusionMaskUv * vec3(hairMaskUV, 1.0)).xy;\n'
            + '  vec2 hairLookupUV = hairOcclusionMaskWarp > 0.5 ? hairWarpedUV : hairMaskUV;\n'
            + '  float hairInside = hairOcclusionMaskWarp > 0.5 ? step(0.0, hairLookupUV.x) * step(hairLookupUV.x, 1.0) * step(0.0, hairLookupUV.y) * step(hairLookupUV.y, 1.0) : 1.0;\n'
            + '  float hairWeight = texture2D(hairOcclusionMask, hairLookupUV).r * hairInside;\n'
            + '  if (hairOcclusionFeatherPx > 0.0) {\n'
            + '    vec2 hairFeatherStep = vec2(hairOcclusionFeatherPx) / hairOcclusionViewport;\n'
            + '    hairWeight = 0.0;\n'
            + '    hairWeight += 1.0 * sampleHairOcclusion(hairMaskUV + vec2(-1.0, -1.0) * hairFeatherStep);\n'
            + '    hairWeight += 2.0 * sampleHairOcclusion(hairMaskUV + vec2(0.0, -1.0) * hairFeatherStep);\n'
            + '    hairWeight += 1.0 * sampleHairOcclusion(hairMaskUV + vec2(1.0, -1.0) * hairFeatherStep);\n'
            + '    hairWeight += 2.0 * sampleHairOcclusion(hairMaskUV + vec2(-1.0, 0.0) * hairFeatherStep);\n'
            + '    hairWeight += 4.0 * sampleHairOcclusion(hairMaskUV + vec2(0.0, 0.0) * hairFeatherStep);\n'
            + '    hairWeight += 2.0 * sampleHairOcclusion(hairMaskUV + vec2(1.0, 0.0) * hairFeatherStep);\n'
            + '    hairWeight += 1.0 * sampleHairOcclusion(hairMaskUV + vec2(-1.0, 1.0) * hairFeatherStep);\n'
            + '    hairWeight += 2.0 * sampleHairOcclusion(hairMaskUV + vec2(0.0, 1.0) * hairFeatherStep);\n'
            + '    hairWeight += 1.0 * sampleHairOcclusion(hairMaskUV + vec2(1.0, 1.0) * hairFeatherStep);\n'
            + '    hairWeight /= 16.0;\n'
            + '  }\n'
            + '  if (hairWeight > 0.0) {\n'
            + '    vec2 hairCameraUV = (hairOcclusionCameraUv * vec3(hairScreen, 1.0)).xy;\n'
            + '    vec3 hairCameraRGB = linearToOutputTexel(texture2D(hairOcclusionCamera, hairCameraUV)).rgb;\n'
            + '    gl_FragColor.rgb = mix(gl_FragColor.rgb, hairCameraRGB, hairWeight);\n'
            + '  }\n}');
      };
      material.customProgramCacheKey = () => `${key === Material.prototype.customProgramCacheKey ? hook.toString() : key.call(material)}|${HAIR_OCCLUSION_METHOD}`;
      material.needsUpdate = true;
    }
  });
  return {
    set(on: boolean, start: number): void {enabled.value = on ? 1 : 0; startZ.value = start;},
    /** Row-major 3×3 matrix from the drawn frame's texture coordinates to the reused mask's, or null for the frame's own mask. */
    setMaskUv(rowMajor: readonly number[] | null): void {
      if (rowMajor && rowMajor.length === 9 && rowMajor.every(Number.isFinite)) {
        maskUv.value.set(rowMajor[0]!, rowMajor[1]!, rowMajor[2]!, rowMajor[3]!, rowMajor[4]!, rowMajor[5]!, rowMajor[6]!, rowMajor[7]!, rowMajor[8]!); maskWarp.value = 1;
      } else {maskUv.value.identity(); maskWarp.value = 0;}
    },
    /** Optional presentation-only radius in render pixels; zero preserves the original category lookup. */
    prepareRender(texture: DataTexture | null, width: number, height: number, camera: Texture | null, featherPx = 0): void {
      if (!Number.isFinite(featherPx) || featherPx < 0 || featherPx > 3) throw new Error('The hair feather must be between 0 and 3 render pixels.');
      mask.value = texture; viewport.value.set(width, height); cameraSource.value = camera; feather.value = featherPx;
      if (camera) {if (camera.matrixAutoUpdate) camera.updateMatrix(); cameraUv.value.copy(camera.matrix);}
    },
    dispose(): void {
      enabled.value = 0; mask.value = null; cameraSource.value = null; maskWarp.value = 0; maskUv.value.identity(); feather.value = 0;
      for (const [material, previous] of owned) {material.onBeforeCompile = previous.hook; material.customProgramCacheKey = previous.key; material.needsUpdate = true;}
      owned.clear();
    },
  };
}
