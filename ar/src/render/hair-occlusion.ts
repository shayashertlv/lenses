/** Hair occlusion inside the eyewear fragment shaders: temple fragments behind `startZ` (mesh-local metres, +Z
 *  forward) blend toward the paired camera texture by the hair mask sampled at their screen position. Linear sampling
 *  of the 0/255 mask gives the edge feather. Behind an arm's continuity cut everything blends fully to the camera. */
import {Material, Matrix3, Mesh, MeshPhysicalMaterial, Vector2} from 'three';
import type {CanvasTexture, DataTexture, Object3D} from 'three';

export const HAIR_OCCLUSION_METHOD = 'hair-gpu-occlusion-v1';
/** Mesh-local metres (+Z forward): fragments behind this plane count as temple arm and may blend toward the camera. */
export const DEFAULT_HAIR_START_Z_M = -0.02;
/** Sentinel cut depth below any mesh-local z: no continuity cut on that arm. */
const NO_CUT = -1000;

/** Wraps the non-lens eyewear materials; install after the temple clip and visibility hooks so it wraps them. */
export function createHairOcclusion(root: Object3D) {
  const enabled = {value: 0}, startZ = {value: DEFAULT_HAIR_START_Z_M}, cutZ = {value: new Vector2(NO_CUT, NO_CUT)};
  const mask = {value: null as DataTexture | null}, viewport = {value: new Vector2(1, 1)};
  const cameraSource = {value: null as CanvasTexture | null}, cameraUv = {value: new Matrix3()};
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
        shader.uniforms.hairOcclusionCamera = cameraSource; shader.uniforms.hairOcclusionCameraUv = cameraUv; shader.uniforms.hairOcclusionCutZ = cutZ;
        shader.vertexShader = 'varying vec2 hairLocalXZ;\n' + shader.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\nhairLocalXZ = position.xz;');
        shader.fragmentShader = 'varying vec2 hairLocalXZ;\nuniform float hairOcclusionEnabled;\nuniform float hairOcclusionStartZ;\n'
          + 'uniform sampler2D hairOcclusionMask;\nuniform vec2 hairOcclusionViewport;\nuniform sampler2D hairOcclusionCamera;\nuniform mat3 hairOcclusionCameraUv;\nuniform vec2 hairOcclusionCutZ;\n'
          + shader.fragmentShader.replace('#include <dithering_fragment>', '#include <dithering_fragment>\n'
            + 'if (hairOcclusionEnabled > 0.5 && hairLocalXZ.y < hairOcclusionStartZ) {\n'
            + '  vec2 hairScreen = gl_FragCoord.xy / hairOcclusionViewport;\n'
            // The mask is row-major from the top-left; the framebuffer origin is bottom-left.
            + '  float hairWeight = texture2D(hairOcclusionMask, vec2(hairScreen.x, 1.0 - hairScreen.y)).r;\n'
            // Continuity cut: behind the arm's first consistent hair run everything is removed to the tip.
            + '  if (hairLocalXZ.y < (hairLocalXZ.x < 0.0 ? hairOcclusionCutZ.x : hairOcclusionCutZ.y)) hairWeight = 1.0;\n'
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
    setCut(negative: number | null, positive: number | null): void {cutZ.value.set(negative ?? NO_CUT, positive ?? NO_CUT);},
    prepareRender(texture: DataTexture | null, width: number, height: number, camera: CanvasTexture | null): void {
      mask.value = texture; viewport.value.set(width, height); cameraSource.value = camera;
      if (camera) {if (camera.matrixAutoUpdate) camera.updateMatrix(); cameraUv.value.copy(camera.matrix);}
    },
    dispose(): void {
      enabled.value = 0; mask.value = null; cameraSource.value = null;
      for (const [material, previous] of owned) {material.onBeforeCompile = previous.hook; material.customProgramCacheKey = previous.key; material.needsUpdate = true;}
      owned.clear();
    },
  };
}
