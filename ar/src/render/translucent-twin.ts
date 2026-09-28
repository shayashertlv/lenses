/** The camera-transmission twin of a translucent frame material (a crystal arm or front, authored roles).
 *
 *  The guarded render draws the frame in two stencil-limited passes (renderer.ts). Pass A draws the whole scene with
 *  the camera image as `scene.background`, so Three's physical transmission shows the camera through the crystal.
 *  Pass B redraws the editable arm corridors with the hair blend and no background: Three's transmission pre-pass
 *  would capture the clear colour there and a crystal arm would go dark, and an opaque twin (transmission 0) would
 *  draw the arm opaque in exactly the corridors where an arm lives. The twin here is that opaque clone with the one
 *  chunk that brings the transmitted light replaced: instead of Three's refraction sample it mixes the diffuse term
 *  toward the paired camera texture at this fragment's own pixel, attenuated by the material's volume (the same
 *  Beer-Lambert term the face shadow uses) and weighted by the material's transmission, so the arm shows the camera
 *  through its tint like it does in pass A. Refraction offsets and roughness blur are not reproduced: the corridor
 *  is narrow and the arm thin, so a straight look-through is what pass A shows there too.
 *
 *  With canonical lenses the twin is the crystal in every pass, and in the canvas passes its source is the renderer's
 *  owned look-through image (translucent-look-through.ts: the camera behind the opaque eyewear, identity UV) instead of
 *  the raw camera, so hardware inside the crystal is seen as a sharp line. Inside the canonical lens input, which
 *  excludes the arms, it keeps the background: the image has the arms in it.
 *
 *  The twin runs the original's compile hooks on the original's live uniforms (arm clip, cheek/lens-input, hair
 *  occlusion and, for a visibility overlay clone, the depth relief), read at compile time so a wrapper installed after
 *  the twin was made is honoured; `refreshCameraTransmissionTwin` bumps the program when that happened. */
import {Material, Matrix3, MeshPhysicalMaterial, Vector2, Vector3} from 'three';
import type {Texture} from 'three';
import {isTranslucentFrameMaterial, markFrameMaterial} from '../eyewear/optical-material.ts';
import {volumeAttenuationRgb} from './eyewear-volume.ts';

/** Program cache key suffix of every twin. */
export const CAMERA_TRANSMISSION_TWIN_KEY = 'camera-transmission-twin-v1';

/** The per-frame camera inputs, shared by every twin of one renderer: the paired background texture (camera or its
 *  shadowed composite), the render viewport and the texture's UV transform. */
export interface CameraTransmissionUniforms {
  readonly twinCameraSource: {value: Texture | null};
  readonly twinCameraViewport: {value: Vector2};
  readonly twinCameraUvTransform: {value: Matrix3};
}

export function createCameraTransmissionUniforms(): CameraTransmissionUniforms {
  return {twinCameraSource: {value: null}, twinCameraViewport: {value: new Vector2(1, 1)}, twinCameraUvTransform: {value: new Matrix3()}};
}

/** Bind this frame's background texture and render size (the same texture the arm clip and hair occlusion sample). */
export function setCameraTransmissionSource(uniforms: CameraTransmissionUniforms, texture: Texture | null, width: number, height: number): void {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
    throw new Error('The camera transmission viewport must be a positive integer size.');
  }
  uniforms.twinCameraSource.value = texture;
  uniforms.twinCameraViewport.value.set(width, height);
  if (texture) {
    if (texture.matrixAutoUpdate) texture.updateMatrix();
    uniforms.twinCameraUvTransform.value.copy(texture.matrix);
  } else uniforms.twinCameraUvTransform.value.identity();
}

interface TwinRecord {
  source: MeshPhysicalMaterial;
  /** The source hooks the twin's current program was built against. */
  hook: Material['onBeforeCompile']; key: Material['customProgramCacheKey'];
}
const twins = new WeakMap<MeshPhysicalMaterial, MeshPhysicalMaterial>();
const records = new WeakMap<MeshPhysicalMaterial, TwinRecord>();

const sourceKey = (source: Material): string => source.customProgramCacheKey === Material.prototype.customProgramCacheKey
  ? source.onBeforeCompile.toString() : source.customProgramCacheKey.call(source);

/** The twin of a translucent frame material (one per material, created on first use). The material must be classified
 *  as translucent frame (isTranslucentFrameMaterial); a visibility overlay clone registered as frame qualifies. */
export function createCameraTransmissionTwin(material: MeshPhysicalMaterial, uniforms: CameraTransmissionUniforms): MeshPhysicalMaterial {
  if (!(material instanceof MeshPhysicalMaterial) || !isTranslucentFrameMaterial(material)) {
    throw new Error('A camera transmission twin requires a translucent frame material.');
  }
  const existing = twins.get(material);
  if (existing) return existing;
  const twin = material.clone();
  twin.transmission = 0; twin.transmissionMap = null; twin.transparent = false;
  twin.name = `${material.name} (camera transmission twin)`;
  markFrameMaterial(twin);
  const owned = {
    twinTransmission: {value: Math.min(1, Math.max(0, material.transmission))},
    twinAttenuation: {value: new Vector3(...volumeAttenuationRgb(material))},
  };
  twin.onBeforeCompile = function(this: Material, shader, backend) {
    // The source's current hook: the arm clip, cheek/lens-input, hair occlusion and (overlay clone) relief wrappers.
    material.onBeforeCompile.call(this, shader, backend);
    Object.assign(shader.uniforms, uniforms, owned);
    shader.fragmentShader = `uniform sampler2D twinCameraSource;
      uniform vec2 twinCameraViewport;
      uniform mat3 twinCameraUvTransform;
      uniform vec3 twinAttenuation;
      uniform float twinTransmission;
      ` + shader.fragmentShader.replace('#include <transmission_fragment>', `{
        // The paired camera at this pixel (linear: the sRGB texture is decoded on sampling), absorbed by the
        // material's volume and tinted by its diffuse colour as Three's transmission is, mixed by the transmission.
        vec2 twinCameraUV = ( twinCameraUvTransform * vec3( gl_FragCoord.xy / twinCameraViewport, 1.0 ) ).xy;
        vec3 twinCameraRGB = texture2D( twinCameraSource, twinCameraUV ).rgb;
        totalDiffuse = mix( totalDiffuse, twinCameraRGB * material.diffuseContribution * twinAttenuation, twinTransmission );
      }`);
  };
  twin.customProgramCacheKey = () => `${sourceKey(material)}|${CAMERA_TRANSMISSION_TWIN_KEY}`;
  twin.needsUpdate = true;
  twins.set(material, twin);
  records.set(twin, {source: material, hook: material.onBeforeCompile, key: material.customProgramCacheKey});
  return twin;
}

/** Rebuild the twin's program when the source's compile hooks changed since the twin was last built (a wrapper
 *  installed or removed). The hooks themselves are always read live; this only bumps the material version. Returns
 *  whether a rebuild was requested. */
export function refreshCameraTransmissionTwin(twin: MeshPhysicalMaterial): boolean {
  const record = records.get(twin);
  if (!record) throw new Error('The material is not a camera transmission twin.');
  const {source} = record;
  if (record.hook === source.onBeforeCompile && record.key === source.customProgramCacheKey) return false;
  record.hook = source.onBeforeCompile; record.key = source.customProgramCacheKey;
  twin.needsUpdate = true;
  return true;
}
