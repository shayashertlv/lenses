/** Camera-space eyewear shadows. Only the observed face receives them; the camera image stays on the GPU. */
import {
  Box3, Color, DepthTexture, DoubleSide, LinearFilter, Matrix3, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial,
  NearestFilter, NoBlending, OrthographicCamera, PlaneGeometry, Scene, ShaderMaterial, SRGBColorSpace,
  UnsignedIntType, Vector2, Vector3, Vector4, WebGLRenderTarget,
} from 'three';
import type {BufferGeometry, CanvasTexture, DataTexture, Material, Object3D, PerspectiveCamera, Texture, WebGLRenderer} from 'three';
import {validateTempleClip} from './temple-clip.ts';
import type {TempleClipConfiguration} from './temple-clip.ts';

export interface ShadowSettings {
  enabled: boolean;
  frameStrength: number;
  lensStrength: number;
  softness: number;
}

export const DEFAULT_SHADOW_SETTINGS: Readonly<ShadowSettings> = Object.freeze({
  enabled: true, frameStrength: .16, lensStrength: .18, softness: 1.9,
});

const bounded = (value: number | undefined, fallback: number, maximum: number): number =>
  typeof value === 'number' && Number.isFinite(value) ? Math.min(maximum, Math.max(0, value)) : fallback;

export function normalizeShadowSettings(value: Partial<ShadowSettings> = {}): ShadowSettings {
  return {
    enabled: typeof value.enabled === 'boolean' ? value.enabled : DEFAULT_SHADOW_SETTINGS.enabled,
    frameStrength: bounded(value.frameStrength, DEFAULT_SHADOW_SETTINGS.frameStrength, 1),
    lensStrength: bounded(value.lensStrength, DEFAULT_SHADOW_SETTINGS.lensStrength, 1),
    softness: bounded(value.softness, DEFAULT_SHADOW_SETTINGS.softness, 3),
  };
}

/** Linear RGB transmitted by an untextured lens at normal incidence. Texture colors multiply this in the caster
 * shader, preserving the asset's tint gradient. This is a material-based approximation, not measured optics. */
export function lensShadowTransmission(material: MeshPhysicalMaterial): readonly [number, number, number] {
  const ior = Number.isFinite(material.ior) ? Math.max(1, material.ior) : 1.5;
  const reflection = ((ior - 1) / (ior + 1)) ** 2;
  const transmitted = bounded(material.transmission, 1, 1) * (1 - reflection) ** 2;
  const thickness = Math.max(0, Number.isFinite(material.thickness) ? material.thickness : 0);
  const distance = material.attenuationDistance;
  const exponent = Number.isFinite(distance) && distance > 0 ? thickness / distance : 0;
  return [0, 1, 2].map(index => {
    const channel = (['r', 'g', 'b'] as const)[index]!;
    const tint = bounded(material.color[channel], 1, 1);
    const attenuation = bounded(material.attenuationColor[channel], 1, 1);
    return tint * transmitted * (exponent > 0 ? attenuation ** exponent : 1);
  }) as [number, number, number];
}

const MAP_SIZE = 512;
const LIGHT_DIRECTION = new Vector3(-10, 15, 20).normalize();
const LIGHT_RIGHT = new Vector3().crossVectors(new Vector3(0, 1, 0), LIGHT_DIRECTION).normalize();
const LIGHT_UP = new Vector3().crossVectors(LIGHT_DIRECTION, LIGHT_RIGHT).normalize();

interface CasterRecord {source: Mesh; mesh: Mesh}
interface ClipUniforms {
  clipEnabled: {value: number}; clipCutoffs: {value: Vector2}; clipFades: {value: Vector2};
}

/** Builds an independent material, retaining the source geometry and its material groups. No original render
 * hooks run in this pass: visibility overlays and the camera-replacement shaders are not shadow casters. */
function casterMaterial(source: Material, geometry: BufferGeometry, clip: ClipUniforms): ShaderMaterial {
  const lens = source instanceof MeshPhysicalMaterial && source.transmission > 0;
  const physical = source instanceof MeshPhysicalMaterial ? source : null;
  const standard = source instanceof MeshStandardMaterial ? source : null;
  const map = standard?.map ?? null;
  const transmissionMap = lens ? physical!.transmissionMap : null;
  const alphaMap = standard?.alphaMap ?? null;
  const maps = [map, transmissionMap, alphaMap];
  const uvNames = maps.map(texture => {
    const channel = texture?.channel ?? 0, name = channel > 0 ? `uv${channel}` : 'uv';
    return geometry.hasAttribute(name) ? name : 'uv';
  });
  const attributes = [...new Set(uvNames.filter(name => name !== 'uv'))].map(name => `attribute vec2 ${name};`).join('\n');
  for (const texture of maps) if (texture?.matrixAutoUpdate) texture.updateMatrix();
  const transmission = lens ? lensShadowTransmission(physical!) : [0, 0, 0];
  const material = new ShaderMaterial({
    name: lens ? 'Lens RGB transmission caster' : 'Opaque eyewear caster',
    side: DoubleSide, transparent: false, blending: NoBlending, depthTest: true, depthWrite: true, toneMapped: false,
    uniforms: {
      ...clip,
      baseTransmission: {value: new Vector3(...transmission as [number, number, number])},
      lens: {value: lens ? 1 : 0}, colorMap: {value: map}, useColorMap: {value: map ? 1 : 0},
      colorUv: {value: map?.matrix.clone() ?? new Matrix3()},
      transmissionMap: {value: transmissionMap}, useTransmissionMap: {value: transmissionMap ? 1 : 0},
      transmissionUv: {value: transmissionMap?.matrix.clone() ?? new Matrix3()},
      alphaMap: {value: alphaMap}, useAlphaMap: {value: alphaMap ? 1 : 0},
      alphaUv: {value: alphaMap?.matrix.clone() ?? new Matrix3()},
      opacity: {value: bounded(source.opacity, 1, 1)}, alphaTest: {value: source.alphaTest},
    },
    vertexShader: `${attributes}
      uniform mat3 colorUv, transmissionUv, alphaUv;
      varying vec2 vColorUv, vTransmissionUv, vAlphaUv, vLocalXZ;
      void main() {
        vColorUv = (colorUv * vec3(${uvNames[0]}, 1.0)).xy;
        vTransmissionUv = (transmissionUv * vec3(${uvNames[1]}, 1.0)).xy;
        vAlphaUv = (alphaUv * vec3(${uvNames[2]}, 1.0)).xy;
        vLocalXZ = position.xz;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `
      uniform vec3 baseTransmission;
      uniform float lens, useColorMap, useTransmissionMap, useAlphaMap, opacity, alphaTest;
      uniform float clipEnabled;
      uniform vec2 clipCutoffs, clipFades;
      uniform sampler2D colorMap, transmissionMap, alphaMap;
      varying vec2 vColorUv, vTransmissionUv, vAlphaUv, vLocalXZ;
      void main() {
        float coverage = 1.0;
        if (lens < 0.5 && clipEnabled > 0.5) {
          float cutoff = vLocalXZ.x < 0.0 ? clipCutoffs.x : clipCutoffs.y;
          float fade = vLocalXZ.x < 0.0 ? clipFades.x : clipFades.y;
          if (vLocalXZ.y < cutoff) discard;
          coverage = smoothstep(cutoff, cutoff + fade, vLocalXZ.y);
        }
        vec4 tint = vec4(1.0);
        if (useColorMap > 0.5) tint = texture2D(colorMap, vColorUv);
        float alpha = opacity * tint.a;
        if (useAlphaMap > 0.5) alpha *= texture2D(alphaMap, vAlphaUv).g;
        if (alpha < max(alphaTest, 0.001)) discard;
        vec3 transmitted = baseTransmission * tint.rgb;
        if (useTransmissionMap > 0.5) transmitted *= texture2D(transmissionMap, vTransmissionUv).r;
        if (lens < 0.5) transmitted = vec3(1.0 - coverage);
        // Alpha labels lenses; nearest depth chooses one physical surface, avoiding double tint from the
        // front/back of a closed lens. Empty texels are distinguished by their cleared depth.
        gl_FragColor = vec4(clamp(transmitted, 0.0, 1.0), lens);
      }`,
  });
  material.userData.kind = lens ? 'lens' : 'frame';
  return material;
}

const receiverVertex = `
  uniform mat4 lightMatrix;
  #ifdef SHADOW_STABLE_RECEIVER
    attribute vec3 shadowPosition;
  #endif
  varying vec4 vLightPosition;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    #ifdef SHADOW_STABLE_RECEIVER
      vLightPosition = lightMatrix * modelMatrix * vec4(shadowPosition, 1.0);
    #else
      vLightPosition = lightMatrix * world;
    #endif
    gl_Position = projectionMatrix * viewMatrix * world;
  }`;

const receiverFragment = `
  uniform sampler2D sourceImage, shadowColor, shadowDepth, hairMask;
  uniform mat3 sourceUv, maskUv;
  uniform vec2 viewport, strengths;
  uniform float mapSpanCm, depthSpanCm, softness, hasHairMask;
  const float shadowMapSize = ${MAP_SIZE}.0;
  varying vec4 vLightPosition;

  vec3 blockedLight(vec2 uv, float receiverDepth) {
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) return vec3(0.0);
    float depth = texture2D(shadowDepth, uv).r;
    float separationCm = (receiverDepth - depth) * depthSpanCm;
    if (depth >= 0.99999) return vec3(0.0);
    // A hard depth comparison flashes when face reconstruction moves through the contact plane. Keep
    // a small forward bias, then introduce the shadow continuously over a submillimetre-to-1.4mm band.
    float contact = smoothstep(0.025, max(0.14, 2.0 * mapSpanCm / shadowMapSize), separationCm);
    float falloff = 1.0 - smoothstep(4.0, 10.0, separationCm);
    vec4 transmission = texture2D(shadowColor, uv);
    vec3 reduction = transmission.a > 0.5
      ? strengths.y * (vec3(1.0) - transmission.rgb) : strengths.x * (vec3(1.0) - transmission.rgb);
    return reduction * contact * falloff;
  }

  vec3 filteredBlockedLight(vec2 uv, float receiverDepth) {
    // Filter four completed comparisons, never the depth or lens/frame label. Filtering those inputs
    // creates phantom blockers and blends a tinted lens into an opaque rim before its depth is tested.
    vec2 grid = uv * shadowMapSize - 0.5;
    vec2 fraction = fract(grid);
    vec2 base = (floor(grid) + 0.5) / shadowMapSize;
    vec2 stepUV = vec2(1.0 / shadowMapSize, 0.0);
    vec3 low = mix(blockedLight(base, receiverDepth), blockedLight(base + stepUV.xy, receiverDepth), fraction.x);
    vec3 high = mix(blockedLight(base + stepUV.yx, receiverDepth), blockedLight(base + stepUV.xx, receiverDepth), fraction.x);
    return mix(low, high, fraction.y);
  }

  void main() {
    vec2 screen = gl_FragCoord.xy / viewport;
    vec3 cameraColor = texture2D(sourceImage, (sourceUv * vec3(screen, 1.0)).xy).rgb;
    vec3 light = vLightPosition.xyz / vLightPosition.w * 0.5 + 0.5;
    vec3 reduction = vec3(0.0);
    if (light.z > 0.0 && light.z < 1.0) {
      // A fixed world-space light footprint cannot jump when the central texel changes from a blocker
      // to empty space. Even at zero user softness, one texel of comparison filtering prevents shimmer.
      // Five bilinear comparisons keep this bounded at twenty depth tests, without a dominant hard tap.
      float radius = max(mapSpanCm / shadowMapSize, 0.20 * softness) / mapSpanCm;
      reduction += filteredBlockedLight(light.xy, light.z);
      reduction += filteredBlockedLight(light.xy + vec2(radius, 0.0), light.z);
      reduction += filteredBlockedLight(light.xy - vec2(radius, 0.0), light.z);
      reduction += filteredBlockedLight(light.xy + vec2(0.0, radius), light.z);
      reduction += filteredBlockedLight(light.xy - vec2(0.0, radius), light.z);
      reduction /= 5.0;
    }
    if (hasHairMask > 0.5) {
      vec2 uv = (maskUv * vec3(screen.x, 1.0 - screen.y, 1.0)).xy;
      float inside = step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0);
      // Hair is in front of the receiver. Keep its captured color even when the projected face extends
      // beneath it. Mask reuse follows the same frame-to-mask transform as temple occlusion.
      reduction *= 1.0 - texture2D(hairMask, uv).r * inside;
    }
    gl_FragColor = vec4(cameraColor * (vec3(1.0) - clamp(reduction, 0.0, 1.0)), 1.0);
    #include <colorspace_fragment>
  }`;

/** Owns only the additional targets, shaders and fullscreen quad. Borrowed model/face geometry and textures are
 * never disposed or reparented. The returned sRGB texture is suitable for scene.background and camera blending. */
export class EyewearShadow {
  private readonly renderer: WebGLRenderer;
  private readonly eyewearRoot: Object3D;
  private readonly maxWorldScale: number | null;
  private readonly lightScene = new Scene();
  private readonly receiverScene = new Scene();
  private readonly copyScene = new Scene();
  private readonly lightCamera = new OrthographicCamera(-12, 12, 12, -12, .1, 100);
  private readonly copyCamera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly shadowTarget = new WebGLRenderTarget(MAP_SIZE, MAP_SIZE, {
    minFilter: NearestFilter, magFilter: NearestFilter, depthBuffer: true, stencilBuffer: false,
    depthTexture: new DepthTexture(MAP_SIZE, MAP_SIZE, UnsignedIntType),
  });
  private readonly compositeTarget = new WebGLRenderTarget(1, 1, {
    minFilter: LinearFilter, magFilter: LinearFilter, depthBuffer: true, stencilBuffer: false,
  });
  private readonly quadGeometry = new PlaneGeometry(2, 2);
  private readonly sourceUniform = {value: null as Texture | null};
  private readonly sourceUvUniform = {value: new Matrix3()};
  private readonly copyMaterial = new ShaderMaterial({
    name: 'Shadow camera copy', depthTest: false, depthWrite: false, toneMapped: false, blending: NoBlending,
    uniforms: {sourceImage: this.sourceUniform, sourceUv: this.sourceUvUniform},
    vertexShader: `varying vec2 vScreen; void main() {vScreen = uv; gl_Position = vec4(position.xy, 0.0, 1.0);}`,
    fragmentShader: `uniform sampler2D sourceImage; uniform mat3 sourceUv; varying vec2 vScreen;
      void main() {gl_FragColor = vec4(texture2D(sourceImage, (sourceUv * vec3(vScreen, 1.0)).xy).rgb, 1.0);
        #include <colorspace_fragment>
      }`,
  });
  private readonly receiverMaterial = new ShaderMaterial({
    name: 'Observed face eyewear shadow receiver', side: DoubleSide, depthTest: true, depthWrite: true,
    blending: NoBlending, toneMapped: false,
    uniforms: {
      sourceImage: this.sourceUniform, sourceUv: this.sourceUvUniform,
      shadowColor: {value: this.shadowTarget.texture}, shadowDepth: {value: this.shadowTarget.depthTexture},
      lightMatrix: {value: new Matrix4()}, viewport: {value: new Vector2(1, 1)}, strengths: {value: new Vector2()},
      mapSpanCm: {value: 24}, depthSpanCm: {value: 99.9}, softness: {value: 1},
      hairMask: {value: null as DataTexture | null}, maskUv: {value: new Matrix3()}, hasHairMask: {value: 0},
    },
    vertexShader: receiverVertex, fragmentShader: receiverFragment,
  });
  private readonly casters: CasterRecord[] = [];
  private readonly casterMaterials = new Set<ShaderMaterial>();
  private readonly clipUniforms: ClipUniforms = {
    clipEnabled: {value: 0}, clipCutoffs: {value: new Vector2(-.09, -.09)}, clipFades: {value: new Vector2(.015, .015)},
  };
  private readonly bounds = new Box3();
  private readonly meshBounds = new Box3();
  private readonly rootInverse = new Matrix4();
  private readonly localTransform = new Matrix4();
  private readonly localCenter = new Vector3();
  private readonly center = new Vector3();
  private readonly size = new Vector3();
  private frustumRadius = 0;
  private disposed = false;

  /** `maxWorldScale` reserves the greatest model-to-camera scale needed during startup fitting, without changing
   * shadow texel density as the fit eases. Omit it for an asset whose scale is already fixed at its first draw. */
  constructor(renderer: WebGLRenderer, eyewearRoot: Object3D, observedFaceGeometry: BufferGeometry, maxWorldScale?: number) {
    if (maxWorldScale !== undefined && (!Number.isFinite(maxWorldScale) || maxWorldScale <= 0)) {
      throw new Error('The maximum shadow asset scale is invalid.');
    }
    this.renderer = renderer; this.eyewearRoot = eyewearRoot;
    this.maxWorldScale = maxWorldScale ?? null;
    this.lightCamera.quaternion.setFromRotationMatrix(new Matrix4().makeBasis(LIGHT_RIGHT, LIGHT_UP, LIGHT_DIRECTION));
    if (observedFaceGeometry.hasAttribute('shadowPosition')) this.receiverMaterial.defines.SHADOW_STABLE_RECEIVER = 1;
    this.shadowTarget.texture.name = 'Eyewear linear RGB transmission';
    this.shadowTarget.texture.generateMipmaps = false;
    this.compositeTarget.texture.name = 'Camera with material-colored eyewear shadows';
    this.compositeTarget.texture.colorSpace = SRGBColorSpace;
    this.compositeTarget.texture.generateMipmaps = false;
    const receiver = new Mesh(observedFaceGeometry, this.receiverMaterial);
    receiver.name = 'Observed face shadow receiver'; receiver.frustumCulled = false;
    this.receiverScene.add(receiver);
    const quad = new Mesh(this.quadGeometry, this.copyMaterial); quad.frustumCulled = false; this.copyScene.add(quad);
    eyewearRoot.traverse(object => {
      if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true || !object.geometry.hasAttribute('position')) return;
      const materials = (Array.isArray(object.material) ? object.material : [object.material]).map(material => {
        const caster = casterMaterial(material, object.geometry, this.clipUniforms); this.casterMaterials.add(caster); return caster;
      });
      const mesh = new Mesh(object.geometry, Array.isArray(object.material) ? materials : materials[0]!);
      mesh.name = `Shadow caster: ${object.name}`; mesh.matrixAutoUpdate = false; mesh.frustumCulled = false;
      this.casters.push({source: object, mesh}); this.lightScene.add(mesh);
    });
  }

  /** Apply the same local endpoint and terminal fade as the visible shafts. A shaft already hidden beyond a hair
   * crossing must not cast a detached shadow farther along the cheek. Lens transmission is unaffected. */
  setTempleClip(configuration: TempleClipConfiguration | null): void {
    if (configuration) validateTempleClip(configuration);
    this.clipUniforms.clipEnabled.value = configuration ? 1 : 0;
    if (configuration) {
      this.clipUniforms.clipCutoffs.value.set(configuration.negativeXCutoffLocalZM, configuration.positiveXCutoffLocalZM);
      this.clipUniforms.clipFades.value.set(configuration.negativeXFadeLengthLocalM ?? configuration.fadeLengthLocalM,
        configuration.positiveXFadeLengthLocalM ?? configuration.fadeLengthLocalM);
    }
  }

  render(camera: PerspectiveCamera, source: CanvasTexture, width: number, height: number, settings: ShadowSettings,
    mask: DataTexture | null = null, maskWarp: readonly number[] | null = null): Texture {
    const normalized = normalizeShadowSettings(settings);
    if (this.disposed || !normalized.enabled || normalized.frameStrength + normalized.lensStrength === 0
      || !Number.isInteger(width) || width <= 0 || !Number.isInteger(height) || height <= 0) return source;
    this.eyewearRoot.updateWorldMatrix(true, true);
    let hasVisibleCaster = false;
    for (const {source: original, mesh} of this.casters) {
      mesh.matrix.copy(original.matrixWorld); mesh.matrixWorldNeedsUpdate = true;
      // Parent visibility is checked as well, but the caller's pose root may be temporarily hidden by an
      // audit variant. Root visibility is deliberately the caller's responsibility.
      let visible = original.visible, parent = original.parent;
      while (parent && parent !== this.eyewearRoot) {visible &&= parent.visible; parent = parent.parent;}
      mesh.visible = visible;
      hasVisibleCaster ||= visible;
    }
    if (!hasVisibleCaster) return source;
    if (this.frustumRadius === 0) {
      // Fit once after the caller first applies its fixed temple shape. A world-axis box grows and
      // shrinks as the head rotates, changing the shadow texel size every frame. This local sphere has
      // one world-space radius for the lifetime of the model, including temporarily hidden submeshes.
      const determinant = this.eyewearRoot.matrixWorld.determinant();
      if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-12) return source;
      this.rootInverse.copy(this.eyewearRoot.matrixWorld).invert(); this.bounds.makeEmpty();
      for (const {source: original} of this.casters) {
        if (!original.geometry.boundingBox) original.geometry.computeBoundingBox();
        this.localTransform.multiplyMatrices(this.rootInverse, original.matrixWorld);
        if (original.geometry.boundingBox) this.bounds.union(this.meshBounds.copy(original.geometry.boundingBox).applyMatrix4(this.localTransform));
      }
      if (this.bounds.isEmpty()) return source;
      this.bounds.getCenter(this.localCenter); this.bounds.getSize(this.size);
      const worldScale = Math.max(this.eyewearRoot.matrixWorld.getMaxScaleOnAxis(), this.maxWorldScale ?? 0);
      const radius = Math.max(8, this.size.length() * .5 * worldScale + 1.5);
      if (!Number.isFinite(radius) || radius > 100) return source;
      this.frustumRadius = radius;
      this.lightCamera.left = -radius; this.lightCamera.right = radius;
      this.lightCamera.top = radius; this.lightCamera.bottom = -radius;
      this.lightCamera.far = radius * 4 + 30;
      this.lightCamera.updateProjectionMatrix();
    }
    const radius = this.frustumRadius, texelCm = 2 * radius / MAP_SIZE;
    this.center.copy(this.localCenter).applyMatrix4(this.eyewearRoot.matrixWorld);
    if (![this.center.x, this.center.y, this.center.z].every(Number.isFinite)) return source;
    // Anchor the light-plane lattice to camera space. Subtexel head translations cannot slide the
    // entire sampling grid across the model; larger moves shift it by an exact whole texel.
    const x = this.center.dot(LIGHT_RIGHT), y = this.center.dot(LIGHT_UP);
    this.center.addScaledVector(LIGHT_RIGHT, Math.round(x / texelCm) * texelCm - x);
    this.center.addScaledVector(LIGHT_UP, Math.round(y / texelCm) * texelCm - y);
    this.lightCamera.position.copy(this.center).addScaledVector(LIGHT_DIRECTION, radius * 2 + 10);
    this.lightCamera.updateMatrixWorld();
    const uniforms = this.receiverMaterial.uniforms;
    (uniforms.lightMatrix!.value as Matrix4).multiplyMatrices(this.lightCamera.projectionMatrix, this.lightCamera.matrixWorldInverse);
    uniforms.mapSpanCm!.value = radius * 2; uniforms.depthSpanCm!.value = this.lightCamera.far - this.lightCamera.near;
    (uniforms.viewport!.value as Vector2).set(width, height);
    (uniforms.strengths!.value as Vector2).set(normalized.frameStrength, normalized.lensStrength);
    uniforms.softness!.value = normalized.softness; uniforms.hairMask!.value = mask; uniforms.hasHairMask!.value = mask ? 1 : 0;
    const warp = uniforms.maskUv!.value as Matrix3;
    if (maskWarp?.length === 9 && maskWarp.every(Number.isFinite)) warp.set(...maskWarp as [number, number, number, number, number, number, number, number, number]);
    else warp.identity();
    this.sourceUniform.value = source;
    if (source.matrixAutoUpdate) source.updateMatrix();
    this.sourceUvUniform.value.copy(source.matrix);
    if (this.compositeTarget.width !== width || this.compositeTarget.height !== height) this.compositeTarget.setSize(width, height);

    const renderer = this.renderer;
    const saved = {
      target: renderer.getRenderTarget(), face: renderer.getActiveCubeFace(), level: renderer.getActiveMipmapLevel(),
      viewport: renderer.getViewport(new Vector4()), scissor: renderer.getScissor(new Vector4()), scissorTest: renderer.getScissorTest(),
      clearColor: renderer.getClearColor(new Color()), clearAlpha: renderer.getClearAlpha(), autoClear: renderer.autoClear,
      xr: renderer.xr.enabled,
    };
    try {
      renderer.xr.enabled = false; renderer.autoClear = false;
      renderer.setRenderTarget(this.shadowTarget); renderer.setScissorTest(false); renderer.setClearColor(0xffffff, 0);
      renderer.clear(true, true, false); renderer.render(this.lightScene, this.lightCamera);
      renderer.setRenderTarget(this.compositeTarget); renderer.setScissorTest(false); renderer.setClearColor(0x000000, 1);
      renderer.clear(true, true, false); renderer.render(this.copyScene, this.copyCamera);
      renderer.render(this.receiverScene, camera);
      return this.compositeTarget.texture;
    } finally {
      renderer.setRenderTarget(saved.target, saved.face, saved.level);
      renderer.setViewport(saved.viewport); renderer.setScissor(saved.scissor); renderer.setScissorTest(saved.scissorTest);
      renderer.setClearColor(saved.clearColor, saved.clearAlpha); renderer.autoClear = saved.autoClear; renderer.xr.enabled = saved.xr;
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.shadowTarget.dispose(); this.compositeTarget.dispose();
    this.copyMaterial.dispose(); this.receiverMaterial.dispose(); this.quadGeometry.dispose();
    for (const material of this.casterMaterials) material.dispose();
    this.casterMaterials.clear(); this.casters.length = 0;
    this.lightScene.clear(); this.receiverScene.clear(); this.copyScene.clear(); this.sourceUniform.value = null;
  }
}
