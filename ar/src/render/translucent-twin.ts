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
 *  With either canonical or native transmissive lenses the twin is the crystal in every pass. On the canvas its source is the renderer's
 *  owned look-through image (translucent-look-through.ts: the camera behind the opaque eyewear, identity UV) instead of
 *  raw camera, so hardware inside the crystal is seen as a sharp line. Inside explicit canonical or native lens input,
 *  which excludes the arms, a shared per-draw flag selects the paired background instead: the owned image has arms in it.
 *
 *  The twin runs the original's compile hooks on the original's live uniforms (arm clip, cheek/lens-input, hair
 *  occlusion and, for a visibility overlay clone, the depth relief), read at compile time so a wrapper installed after
 *  the twin was made is honoured; `refreshCameraTransmissionTwin` bumps the program when that happened.
 *
 *  Energy and the reflection (v2, 2026-09-30). The look-through is kept apart from the surface's own light until the end
 *  of the shader, for two reasons measured on the Tom Ford FT1123-D crystal (r0009, the continued job's r0003):
 *  - It loses what the surface reflects, (1 - F) with F = EnvironmentBRDF, as Three's own refraction path does
 *    (transmission_pars_fragment getIBLVolumeRefraction returns (1 - F) * attenuatedColor). v1 mixed the full camera in and
 *    Three added the reflection on top, so a grazing rim wall showed the camera plus a near-total reflection and read
 *    milky. F rises toward the silhouette, so there the look-through fades and the room's reflection takes over: the rims
 *    read as reflective crystal edges (lighter lines over dark skin, darker over a white backdrop).
 *  - The reflection (specular, clearcoat, the body's own lit diffuse) goes through the reflection limit
 *    (eyewear-reflection.ts) against the look-through: the twin is not tone-mapped (renderer.ts, it carries the camera
 *    image), and the room panel's reflection clipped 12 % of r0009's crystal to flat white.
 *  A clearcoat (KHR_materials_clearcoat, loaded by GLTFLoader and kept by the clone) is Three's coat layer: its Fresnel
 *  takes its share from the look-through as it does from the base layer, and its sharp reflection of the see-through room
 *  (renderer.ts: PMREM blur 0, not the scene room's 0.04 floor) is what shows as crisp bright rim lines.
 *  Measured with the see-through room (the pipeline's harness, 2026-09-30): r0009's crystal clipped share 0.116 -> 0 over
 *  the observation's 19 views, frame_see_through 0.553 -> 0.701 (the pipeline's crystal line is 0.6), temple_see_through
 *  0.811 -> 0.762 (the temples' oblique faces give their Fresnel share to the reflection); a clearcoat 1 copy of the
 *  continued job's r0003 moves 10-33 % of the crystal front's pixels by more than 8 levels (checker / dim skin), and
 *  roughness 0 -> 0.2 -> 0.5 moves 7 / 19 % (checker): the finish is a visible knob again. */
import {Material, Matrix3, MeshPhysicalMaterial, Vector2, Vector3} from 'three';
import type {Texture} from 'three';
import {isTranslucentFrameMaterial, markFrameMaterial} from '../eyewear/optical-material.ts';
import {volumeAttenuationRgb} from './eyewear-volume.ts';
import {CRYSTAL_REFLECTION_KNEE, REFLECTION_LIMIT_GLSL, reflectionLimitCall} from './eyewear-reflection.ts';

/** Program cache key suffix. v3: separate canvas and lens-input sources selected by the shared draw-state flag. */
export const CAMERA_TRANSMISSION_TWIN_KEY = 'camera-transmission-twin-v3';

/** three 0.185.1 meshphysical.glsl.js: the clearcoat layer's composition, where the look-through is attenuated too. */
export const TWIN_CLEARCOAT_LAYER = 'outgoingLight = outgoingLight * ( 1.0 - material.clearcoat * Fcc ) + ( clearcoatSpecularDirect + clearcoatSpecularIndirect ) * material.clearcoat;';

function replaceOnce(source: string, marker: string, value: string): string {
  if (source.split(marker).length !== 2) throw new Error(`Camera transmission twin: pinned Three shader marker changed: ${marker}`);
  return source.replace(marker, () => value);
}

/** Shared by every twin of one renderer: the owned crystal image, paired lens-safe background, their UV transforms,
 *  render viewport and the temple-visibility draw-state flag. */
export interface CameraTransmissionUniforms {
  readonly twinCameraSource: {value: Texture | null};
  readonly twinCameraViewport: {value: Vector2};
  readonly twinCameraUvTransform: {value: Matrix3};
  /** The paired background stays separate from the core-inclusive crystal image. */
  readonly twinLensSource: {value: Texture | null};
  readonly twinLensUvTransform: {value: Matrix3};
  /** Shared with temple visibility's per-draw explicit/native lens-input flag. */
  readonly twinLensInput: {value: number};
}

export function createCameraTransmissionUniforms(): CameraTransmissionUniforms {
  return {twinCameraSource: {value: null}, twinCameraViewport: {value: new Vector2(1, 1)}, twinCameraUvTransform: {value: new Matrix3()},
    twinLensSource: {value: null}, twinLensUvTransform: {value: new Matrix3()}, twinLensInput: {value: 0}};
}

/** Bind the paired background for this frame. Both paths start here; the canvas path can later use the owned image. */
export function setCameraTransmissionSource(uniforms: CameraTransmissionUniforms, texture: Texture | null, width: number, height: number): void {
  setCrystalLookThroughSource(uniforms, texture, width, height);
  uniforms.twinLensSource.value = texture;
  uniforms.twinLensUvTransform.value.copy(uniforms.twinCameraUvTransform.value);
}

/** Rebind only the crystal's canvas input. Native/canonical lens input must never sample its posterior hardware. */
export function setCrystalLookThroughSource(uniforms: CameraTransmissionUniforms, texture: Texture | null, width: number, height: number): void {
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
    let fragment = `uniform sampler2D twinCameraSource;
      uniform sampler2D twinLensSource;
      uniform vec2 twinCameraViewport;
      uniform mat3 twinCameraUvTransform;
      uniform mat3 twinLensUvTransform;
      uniform float twinLensInput;
      uniform vec3 twinAttenuation;
      uniform float twinTransmission;
      ` + REFLECTION_LIMIT_GLSL + shader.fragmentShader;
    fragment = replaceOnce(fragment, '#include <transmission_fragment>', `vec3 twinThrough = vec3( 0.0 );
      {
        // The paired camera at this pixel (linear: the sRGB texture is decoded on sampling), absorbed by the
        // material's volume and tinted by its diffuse colour as Three's transmission is, weighted by the transmission,
        // less what the surface reflects. The body's own lit diffuse keeps the remaining (1 - transmission) share.
        vec2 twinCameraUV = ( twinCameraUvTransform * vec3( gl_FragCoord.xy / twinCameraViewport, 1.0 ) ).xy;
        vec3 twinCameraRGB;
        if ( twinLensInput > 0.5 ) {
          // Three draws these opaque twins again inside its native lens transmission target.
          // The owned crystal image contains posterior metal, which must not cross the wearer's eyes.
          vec2 twinLensUV = ( twinLensUvTransform * vec3( gl_FragCoord.xy / twinCameraViewport, 1.0 ) ).xy;
          twinCameraRGB = texture2D( twinLensSource, twinLensUV ).rgb;
        } else {
          twinCameraRGB = texture2D( twinCameraSource, twinCameraUV ).rgb;
        }
        vec3 twinReflectance = EnvironmentBRDF( geometryNormal, geometryViewDir, material.specularColorBlended, material.specularF90, material.roughness );
        twinThrough = twinTransmission * ( 1.0 - twinReflectance ) * twinCameraRGB * material.diffuseContribution * twinAttenuation;
        totalDiffuse *= 1.0 - twinTransmission;
      }`);
    // outgoingLight is now the surface's own light only; the coat's Fresnel takes its share of the look-through too.
    fragment = replaceOnce(fragment, TWIN_CLEARCOAT_LAYER, `twinThrough *= 1.0 - material.clearcoat * Fcc;
      ${TWIN_CLEARCOAT_LAYER}`);
    // The crystal's own knee, half the headroom (eyewear-reflection.ts): the lens knee is absolute for lensenv, which the
    // crystal has not, and the crystal's see-through measurements were taken with this one.
    shader.fragmentShader = replaceOnce(fragment, '#include <opaque_fragment>', `outgoingLight = twinThrough + ${reflectionLimitCall('outgoingLight', 'twinThrough', CRYSTAL_REFLECTION_KNEE)};
      #include <opaque_fragment>`);
  };
  twin.customProgramCacheKey = () => `${sourceKey(material)}|${CAMERA_TRANSMISSION_TWIN_KEY}`;
  twin.needsUpdate = true;
  twins.set(material, twin);
  records.set(twin, {source: material, hook: material.onBeforeCompile, key: material.customProgramCacheKey});
  twin.addEventListener('dispose', () => {
    if (twins.get(material) === twin) twins.delete(material);
    records.delete(twin);
  });
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
