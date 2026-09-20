import {
  CanvasTexture, Color, DepthTexture, DoubleSide, Material, MathUtils, Matrix3, Matrix4, Mesh, MeshBasicMaterial,
  MeshPhysicalMaterial, NearestFilter, Scene, ShaderMaterial, SRGBColorSpace, UnsignedIntType, Vector2,
  Vector4, WebGLRenderTarget,
} from 'three';
import type {BufferGeometry, Object3D, PerspectiveCamera, WebGLRenderer} from 'three';
import type {TempleCheekContact} from './temple-cheek-contact.ts';

/** Per-pixel relief from the arm fragment's distance behind the head surface. */
export const TEMPLE_VISIBILITY_METHOD = 'temple-behind-head-v4';
export const TEMPLE_VISIBILITY_PARAMETERS = Object.freeze({
  lateralArmMinM: 0.045,
  rootBlendM: 0.003, placementCm: 0.05,
  // The opaque rim can extend behind the physical lens. Reserve this extra depth
  // for the front/endpiece before observed-cheek contact can hide the shaft.
  cheekFrontGuardM: 0.005,
  cheekBehindStartCm: 0.1, cheekBehindFullCm: 0.3,
  /** Preserve shallow intersections with the fitted proxy; return deeper fragments to ordinary head depth. */
  reliefBehindStartCm: 0.3, reliefBehindFullCm: 1.2,
});

/** The share of an arm fragment that survives, given how far behind the head surface it is (centimetres, positive
 *  behind). The shader computes exactly this; it is exported so a test can pin the two against each other. */
export function depthRelief(behindCm: number, keepCm: number = TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm,
  dropCm: number = TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm): number {
  if (!Number.isFinite(behindCm)) return 0;
  return 1 - MathUtils.smoothstep(behindCm, keepCm, dropCm);
}

/** Validate the recorded relief band in centimetres. */
export function validateReliefBand(keepCm: number, dropCm: number): void {
  if (!Number.isFinite(keepCm) || !Number.isFinite(dropCm) || keepCm < 0 || keepCm > 6 || dropCm <= keepCm || dropCm > 12) {
    throw new Error('The temple relief band is invalid.');
  }
}

export interface TempleVisibilityConfiguration {
  readonly method: typeof TEMPLE_VISIBILITY_METHOD;
  readonly coverage: 'alpha-to-coverage' | 'ordered-dither';
  /** In centimetres behind the head surface: kept in full up to `reliefKeepCm`, gone beyond `reliefDropCm`. */
  readonly reliefKeepCm: number;
  readonly reliefDropCm: number;
  /** Relinquish the fixed returned rear shaft to ordinary head depth. */
  readonly terminalReturn?: {readonly startZM: number; readonly endZM: number} | null;
  /** Keep posterior opaque arms out of the camera image sampled by physical lenses. Ordinary shaft drawing is
   * unaffected; this applies only while Three renders its internal transmission input. */
  readonly excludeArmsFromLensInput?: boolean;
  /** Current observed cheek eligibility. A separate observed depth pass decides actual occlusion. */
  readonly cheekContact?: TempleCheekContact | null;
  /** Minimum projected contact transition, in render pixels; zero preserves the original 1–3mm ramp. */
  readonly cheekTransitionPx?: number;
}

export function validateTempleVisibility(value: TempleVisibilityConfiguration): void {
  if (!value || value.method !== TEMPLE_VISIBILITY_METHOD
      || value.coverage !== 'alpha-to-coverage' && value.coverage !== 'ordered-dither') {
    throw new Error('The recorded temple visibility configuration is invalid.');
  }
  try {validateReliefBand(value.reliefKeepCm, value.reliefDropCm);}
  catch {throw new Error('The recorded temple visibility configuration is invalid.');
  }
  if (value.terminalReturn && (!Number.isFinite(value.terminalReturn.startZM) || !Number.isFinite(value.terminalReturn.endZM)
    || value.terminalReturn.startZM <= value.terminalReturn.endZM || value.terminalReturn.startZM > -.03
    || value.terminalReturn.endZM < -.2)) throw new Error('The terminal relief exclusion is invalid.');
  if (value.excludeArmsFromLensInput !== undefined && typeof value.excludeArmsFromLensInput !== 'boolean') {
    throw new Error('The lens input arm exclusion is invalid.');
  }
  if (value.cheekContact && (value.cheekContact.polygon.length < 3 || value.cheekContact.polygon.length > 64
    || value.cheekContact.polygon.some(p => !Number.isFinite(p.x) || !Number.isFinite(p.y)))) {
    throw new Error('The observed cheek contact is invalid.');
  }
  if (value.cheekTransitionPx !== undefined && (!Number.isFinite(value.cheekTransitionPx)
    || value.cheekTransitionPx < 0 || value.cheekTransitionPx > 3)) throw new Error('The cheek transition is invalid.');
}

function validatePose(rawMatrix: readonly number[]): void {
  if (rawMatrix.length !== 16 || !rawMatrix.every(Number.isFinite)) throw new Error('The temple visibility pose is invalid.');
  const matrix = new Matrix4().fromArray(rawMatrix);
  if (Math.abs(matrix.determinant()) < 1e-12) throw new Error('The temple visibility pose is singular.');
}
/** Capture the backend's coverage rule and the depth relief band. */
export function createTempleVisibilityConfiguration(nativeSamples: number,
  reliefKeepCm: number = TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm,
  reliefDropCm: number = TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm): TempleVisibilityConfiguration {
  if (!Number.isInteger(nativeSamples) || nativeSamples < 0) throw new Error('The native sample count is invalid.');
  validateReliefBand(reliefKeepCm, reliefDropCm);
  return {method: TEMPLE_VISIBILITY_METHOD, coverage: nativeSamples > 0 ? 'alpha-to-coverage' : 'ordered-dither',
    reliefKeepCm, reliefDropCm};
}

/** Matches Three's ordinary perspective depth convention; used in diagnostics. */
export function templeLiftedDepth(surfaceDepth: number, near: number, far: number): number {
  if (!Number.isFinite(surfaceDepth) || surfaceDepth < 0 || surfaceDepth > 1
      || !Number.isFinite(near) || !Number.isFinite(far) || near <= 0 || far <= near) {
    throw new Error('The temple visibility perspective depth is invalid.');
  }
  const viewZ = near * far / ((far - near) * surfaceDepth - far);
  const aheadZ = Math.min(-near, viewZ + TEMPLE_VISIBILITY_PARAMETERS.placementCm);
  return ((near + aheadZ) * far) / ((far - near) * aheadZ);
}

interface VisibilityContext {
  renderer: WebGLRenderer;
  scene: Scene;
  camera: PerspectiveCamera;
  eyewearPose: Object3D;
  /** Camera-space centimetres, kept current before fitting the main face proxy. Caller owns geometry. */
  observedFaceSurface?: BufferGeometry;
}

const isLens = (material: Material): boolean => material instanceof MeshPhysicalMaterial && material.transmission > 0;
const copyConfiguration = (value: TempleVisibilityConfiguration): TempleVisibilityConfiguration => ({...value,
  ...(value.cheekContact ? {cheekContact: {...value.cheekContact, polygon: value.cheekContact.polygon.map(p => ({...p}))}} : {})});

/**
 * Adds a color-only near-arm overlay. The scene outside eyewearPose supplies
 * the current face/head occluders; their original main-pass depth is untouched.
 * Install after the temple clip wrapper so overlays inherit its live uniforms.
 */
export function createTempleVisibility(root: Object3D, context: VisibilityContext) {
  const {renderer, scene, camera, eyewearPose} = context;
  const originals: Mesh[] = [];
  let frontZM = Infinity;
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
    const geometry: BufferGeometry = object.geometry;
    const position = geometry.getAttribute('position'), index = geometry.getIndex();
    if (!position) return;
    if (materials.some(material => !isLens(material) && !material.transparent)) originals.push(object);
    for (const [materialIndex, material] of materials.entries()) {
      if (!isLens(material)) continue;
      const groups = Array.isArray(object.material)
        ? geometry.groups.filter(group => group.materialIndex === materialIndex)
        : [{start: 0, count: index?.count ?? position.count}];
      for (const group of groups) for (let i = group.start; i < group.start + group.count; i++) {
        frontZM = Math.min(frontZM, position.getZ(index ? index.getX(i) : i));
      }
    }
  });
  if (!Number.isFinite(frontZM) || originals.length === 0) throw new Error('Temple visibility requires an opaque frame and physical lenses.');
  const target = new WebGLRenderTarget(1, 1, {
    minFilter: NearestFilter, magFilter: NearestFilter, depthBuffer: true,
    depthTexture: new DepthTexture(1, 1, UnsignedIntType),
  });
  const cheekTarget = new WebGLRenderTarget(1, 1, {
    minFilter: NearestFilter, magFilter: NearestFilter, depthBuffer: true,
    depthTexture: new DepthTexture(1, 1, UnsignedIntType),
  });
  const cheekMaterial = new MeshBasicMaterial({color: 0xffffff, toneMapped: false, side: DoubleSide, depthTest: true, depthWrite: true});
  const cheekScene = new Scene();
  if (context.observedFaceSurface) {
    const mesh = new Mesh(context.observedFaceSurface, cheekMaterial);
    mesh.frustumCulled = false; cheekScene.add(mesh);
  }
  const depthMaterial = new ShaderMaterial({
    side: DoubleSide, depthWrite: true, depthTest: true,
    vertexShader: `void main() { vec4 world = modelMatrix * vec4(position, 1.0);
      gl_Position = projectionMatrix * viewMatrix * world; }`,
    fragmentShader: 'void main() { gl_FragColor = vec4(1.0); }',
  });
  const uniforms = {
    templeVisibilityHeadDepth: {value: target.depthTexture},
    templeVisibilitySize: {value: new Vector2(1, 1)}, templeVisibilityNearFar: {value: new Vector2(camera.near, camera.far)},
    templeVisibilityFront: {value: frontZM},
    templeVisibilityRelief: {value: new Vector2(TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm, TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm)},
    templeVisibilityTerminal: {value: new Vector2()},
    templeVisibilityTerminalEnabled: {value: 0},
    templeVisibilityDitherThresholds: {value: new Float32Array([
      0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5,
    ].map(rank => (rank + 0.5) / 16))},
  };
  const frontalUniforms = {
    templeFrontalFront: {value: frontZM}, templeFrontalCameraSource: {value: null as CanvasTexture | null},
    templeCheekFront: {value: frontZM - TEMPLE_VISIBILITY_PARAMETERS.cheekFrontGuardM},
    templeCheekCount: {value: 0}, templeCheekPolygon: {value: Array.from({length: 64}, () => new Vector2())},
    templeCheekDepth: {value: cheekTarget.depthTexture}, templeCheekMask: {value: cheekTarget.texture},
    templeCheekNearFar: uniforms.templeVisibilityNearFar,
    templeCheekTransitionPx: {value: 0},
    templeFrontalViewport: {value: new Vector2(1, 1)}, templeFrontalUvTransform: {value: new Matrix3()},
    templeExcludeArmsFromLensInput: {value: 0}, templeInternalLensInput: {value: 0},
  };
  const originalHooks = new Map<Material, {
    hook: Material['onBeforeCompile']; key: Material['customProgramCacheKey'];
    wrapper: Material['onBeforeCompile']; wrapperKey: Material['customProgramCacheKey'];
  }>();
  const restoreOriginalHooks = () => {
    for (const [material, saved] of originalHooks) {
      if (material.onBeforeCompile === saved.wrapper) material.onBeforeCompile = saved.hook;
      if (material.customProgramCacheKey === saved.wrapperKey) material.customProgramCacheKey = saved.key;
      material.needsUpdate = true;
    }
    originalHooks.clear();
  };
  const wrapFrontal = (material: Material) => {
    if (isLens(material) || material.transparent || originalHooks.has(material)) return;
    const hook = material.onBeforeCompile, key = material.customProgramCacheKey;
    const wrapper: Material['onBeforeCompile'] = function(this: Material, shader, backend) {
      hook.call(this, shader, backend);
      Object.assign(shader.uniforms, frontalUniforms);
      shader.vertexShader = 'varying vec3 templeFrontalOriginalPosition;\n' + shader.vertexShader.replace(
        '#include <begin_vertex>', `#include <begin_vertex>
          templeFrontalOriginalPosition = position;`);
      shader.fragmentShader = `varying vec3 templeFrontalOriginalPosition;
        uniform float templeFrontalFront, templeCheekFront, templeCheekTransitionPx;
        uniform int templeCheekCount;
        uniform vec2 templeCheekPolygon[64], templeCheekNearFar;
        uniform sampler2D templeCheekDepth, templeCheekMask;
        uniform float templeExcludeArmsFromLensInput, templeInternalLensInput;
        uniform sampler2D templeFrontalCameraSource;
        uniform vec2 templeFrontalViewport;
        uniform mat3 templeFrontalUvTransform;
        float templeCheekCoverage(vec2 pixel) {
          // Current observed lower-face contour, in source-aligned top-left pixels.
          // Only blend INWARD from its edge: an exposed shaft pixel stays exact.
          bool inside = false;
          float distanceSquared = 1.0e12;
          vec2 a = templeCheekPolygon[templeCheekCount - 1] * templeFrontalViewport;
          for (int i = 0; i < 64; i++) {
            if (i >= templeCheekCount) break;
            vec2 b = templeCheekPolygon[i] * templeFrontalViewport;
            vec2 edge = b - a;
            float t = clamp(dot(pixel - a, edge) / max(dot(edge, edge), 1.0e-8), 0.0, 1.0);
            vec2 delta = pixel - (a + t * edge);
            distanceSquared = min(distanceSquared, dot(delta, delta));
            if ((a.y > pixel.y) != (b.y > pixel.y)) {
              if (pixel.x < a.x + (pixel.y - a.y) * (b.x - a.x) / (b.y - a.y)) inside = !inside;
            }
            a = b;
          }
          return inside ? smoothstep(0.0, max(1.25, templeCheekTransitionPx), sqrt(distanceSquared)) : 0.0;
        }\n` + shader.fragmentShader;
      // Derivatives require intact fragment quads. Evaluate before every optical/clip/overlay
      // discard and before the varying local-X/Z eligibility branch below.
      shader.fragmentShader = shader.fragmentShader.replace('void main() {', `void main() {
        float cheekBehindCm = 0.0;
        float cheekBandCm = ${(TEMPLE_VISIBILITY_PARAMETERS.cheekBehindFullCm - TEMPLE_VISIBILITY_PARAMETERS.cheekBehindStartCm).toFixed(3)};
        #ifdef TONE_MAPPING
        if (templeCheekCount >= 3) {
          vec2 cheekDepthUV = gl_FragCoord.xy / templeFrontalViewport;
          float cheekObservedDepth = texture2D(templeCheekDepth, cheekDepthUV).x;
          float cheekNear = templeCheekNearFar.x, cheekFar = templeCheekNearFar.y;
          float cheekFaceZ = cheekNear * cheekFar / ((cheekFar - cheekNear) * cheekObservedDepth - cheekFar);
          float cheekArmZ = cheekNear * cheekFar / ((cheekFar - cheekNear) * gl_FragCoord.z - cheekFar);
          cheekBehindCm = cheekFaceZ - cheekArmZ;
          if (templeCheekTransitionPx > 0.0) cheekBandCm = clamp(
            fwidth(cheekBehindCm) * templeCheekTransitionPx, cheekBandCm, 0.5);
        }
        #endif`);
      // The real camera image is already the surface behind each lens. A proximal far arm may sit in front of
      // the approximate face depth and otherwise contaminate that image with a dark bar across an eye. Exclude
      // only shaft fragments in the internal lens input; retain front/rims and their normal scene rendering.
      shader.fragmentShader = shader.fragmentShader.replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
        if (templeExcludeArmsFromLensInput > 0.5 && templeInternalLensInput > 0.5
            && abs(templeFrontalOriginalPosition.x) > ${TEMPLE_VISIBILITY_PARAMETERS.lateralArmMinM}
            && templeFrontalOriginalPosition.z < templeFrontalFront) discard;`);
      // Color only: preserve opaque depth/coverage and exclude the optical front.
      // Later overlay wrapping runs its coverage/depth block before this footer;
      // the earlier clip wrapper's endpoint RGB footer runs afterwards.
      // This baseline renders beauty to the native ACES framebuffer. Three's
      // internal transmission pass has no TONE_MAPPING; keep its lens input exact.
      shader.fragmentShader = shader.fragmentShader.replace('#include <dithering_fragment>', `#include <dithering_fragment>
        #ifdef TONE_MAPPING
        if (templeCheekCount >= 3 && abs(templeFrontalOriginalPosition.x) > ${TEMPLE_VISIBILITY_PARAMETERS.lateralArmMinM}
            && templeFrontalOriginalPosition.z < templeCheekFront) {
          float cheekCoverage = templeCheekCoverage(vec2(gl_FragCoord.x, templeFrontalViewport.y - gl_FragCoord.y));
          vec2 cheekDepthUV = gl_FragCoord.xy / templeFrontalViewport;
          // Retain a shaft IN FRONT of observed skin, even when its image lies inside the face.
          // Keep the 1mm onset. Broaden compressed transitions to about two render pixels,
          // bounded to 6mm behind skin so a steep angle cannot dissolve a whole shaft.
          cheekCoverage *= texture2D(templeCheekMask, cheekDepthUV).a * smoothstep(
            ${TEMPLE_VISIBILITY_PARAMETERS.cheekBehindStartCm}, ${TEMPLE_VISIBILITY_PARAMETERS.cheekBehindStartCm} + cheekBandCm, cheekBehindCm);
          float cheekRoot = smoothstep(0.0, ${TEMPLE_VISIBILITY_PARAMETERS.rootBlendM}, templeCheekFront - templeFrontalOriginalPosition.z);
          vec2 cheekUV = (templeFrontalUvTransform * vec3(gl_FragCoord.xy / templeFrontalViewport, 1.0)).xy;
          vec3 cheekCameraRGB = linearToOutputTexel(texture2D(templeFrontalCameraSource, cheekUV)).rgb;
          gl_FragColor.rgb = mix(gl_FragColor.rgb, cheekCameraRGB, cheekCoverage * cheekRoot);
        }
        #endif`);
    };
    const wrapperKey = () => `${key === Material.prototype.customProgramCacheKey ? hook.toString() : key.call(material)}|temple-lens-input-v1|observed-cheek-v2`;
    originalHooks.set(material, {hook, key, wrapper, wrapperKey});
    material.onBeforeCompile = wrapper; material.customProgramCacheKey = wrapperKey; material.needsUpdate = true;
  };
  const overlays: Mesh[] = [], materials = new Map<Material, Material>();
  const entryTargets = new WeakMap<Object3D, WebGLRenderTarget | null>();
  const originalRenderHooks = new Map<Mesh, {before: Mesh['onBeforeRender']; wrapper: Mesh['onBeforeRender']}>();
  const markLensInput = (backend: WebGLRenderer, renderCamera: Object3D): void => {
    frontalUniforms.templeInternalLensInput.value = Number(backend === renderer && entryTargets.has(renderCamera)
      && backend.getRenderTarget() !== entryTargets.get(renderCamera));
  };
  const restoreRenderHooks = (): void => {
    for (const [mesh, saved] of originalRenderHooks) if (mesh.onBeforeRender === saved.wrapper) mesh.onBeforeRender = saved.before;
    originalRenderHooks.clear();
  };
  const pendingColorWrites = new Map<Material, boolean>();
  const restoreColorWrites = () => {
    for (const [material, previous] of pendingColorWrites) material.colorWrite = previous;
    pendingColorWrites.clear();
  };
  const hiddenMaterial = new MeshBasicMaterial({visible: false});
  let configuration: TempleVisibilityConfiguration | null = null, disposed = false;
  let alphaToCoverage = false;
  const wrap = (original: Material): Material => {
    if (isLens(original) || original.transparent) return hiddenMaterial;
    const existing = materials.get(original);
    if (existing) return existing;
    const material = original.clone(), hook = original.onBeforeCompile, key = original.customProgramCacheKey;
    material.depthWrite = false; material.depthTest = true; material.transparent = false; material.alphaToCoverage = false;
    material.onBeforeCompile = function(shader, backend) {
      hook.call(this, shader, backend);
      Object.assign(shader.uniforms, uniforms);
      shader.vertexShader = 'varying vec3 templeVisibilityPosition;\n' + shader.vertexShader.replace(
        '#include <begin_vertex>', '#include <begin_vertex>\ntempleVisibilityPosition = position;');
      shader.fragmentShader = `varying vec3 templeVisibilityPosition;
        uniform sampler2D templeVisibilityHeadDepth;
        uniform vec2 templeVisibilitySize, templeVisibilityNearFar, templeVisibilityRelief;
        uniform float templeVisibilityTerminalEnabled;
        uniform vec2 templeVisibilityTerminal;
        uniform float templeVisibilityFront, templeVisibilityDitherThresholds[16];
        ` + shader.fragmentShader;
      // It changes only an overlay fragment's tested depth, never depth-buffer contents.
      shader.fragmentShader = shader.fragmentShader.replace('#include <dithering_fragment>', `#include <dithering_fragment>
        if (abs(templeVisibilityPosition.x) <= ${TEMPLE_VISIBILITY_PARAMETERS.lateralArmMinM}
            || templeVisibilityPosition.z >= templeVisibilityFront) discard;
        vec2 templeVisibilityUV = gl_FragCoord.xy / templeVisibilitySize;
        float templeRootWeight = smoothstep(0.0, ${TEMPLE_VISIBILITY_PARAMETERS.rootBlendM}, templeVisibilityFront - templeVisibilityPosition.z);
        // The head's own depth under this pixel, and this arm fragment's. Both in view centimetres, negative away from
        // the camera, so their difference is how far behind the head surface this fragment sits.
        float templeSurfaceDepth = texture2D(templeVisibilityHeadDepth, templeVisibilityUV).r;
        float templeNear = templeVisibilityNearFar.x, templeFar = templeVisibilityNearFar.y;
        float templeViewZ = templeNear * templeFar / ((templeFar - templeNear) * templeSurfaceDepth - templeFar);
        float templeArmViewZ = templeNear * templeFar / ((templeFar - templeNear) * gl_FragCoord.z - templeFar);
        float templeBehindCm = templeViewZ - templeArmViewZ;
        float templeRelief = 1.0 - smoothstep(templeVisibilityRelief.x, templeVisibilityRelief.y, templeBehindCm);
        // Over the background the ordinary draw already shows the arm.
        float templeHeadPresent = step(templeSurfaceDepth, 0.9999);
        float templeOverlayCoverage = templeHeadPresent * templeRelief * templeRootWeight;
        if (templeVisibilityTerminalEnabled > 0.5) {
          // A deliberately buried fixed ending must not be lifted back in front of the skin.
          templeOverlayCoverage *= smoothstep(templeVisibilityTerminal.y, templeVisibilityTerminal.x, templeVisibilityPosition.z);
        }
        if (templeOverlayCoverage <= 0.0) discard;
        ${material.alphaToCoverage ? 'gl_FragColor.a = templeOverlayCoverage;' : `
          int templeVisibilityDitherIndex = int(mod(floor(gl_FragCoord.x), 4.0) + 4.0 * mod(floor(gl_FragCoord.y), 4.0));
          if (templeOverlayCoverage < templeVisibilityDitherThresholds[templeVisibilityDitherIndex]) discard;
          gl_FragColor.a = 1.0;`}
        float templeAheadZ = min(-templeNear, templeViewZ + ${TEMPLE_VISIBILITY_PARAMETERS.placementCm});
        float templeLifted = ((templeNear + templeAheadZ) * templeFar) / ((templeFar - templeNear) * templeAheadZ);
        gl_FragDepth = min(gl_FragCoord.z, templeLifted);`);
    };
    material.customProgramCacheKey = () => `${key === Material.prototype.customProgramCacheKey
      ? hook.toString() : key.call(original)}|${TEMPLE_VISIBILITY_METHOD}|terminal-return-v1|${material.alphaToCoverage ? 'a2c' : 'dither'}`;
    materials.set(original, material);
    return material;
  };
  try {
    // Wrap each original once, before clones inherit its live uniform owner.
    for (const original of originals) {
      for (const material of Array.isArray(original.material) ? original.material : [original.material]) wrapFrontal(material);
      const before = original.onBeforeRender;
      const wrapper: Mesh['onBeforeRender'] = function(this: Mesh, ...args) {
        before.apply(this, args); markLensInput(args[0], args[2]);
      };
      originalRenderHooks.set(original, {before, wrapper}); original.onBeforeRender = wrapper;
    }
    for (const original of originals) {
      const overlay = original.clone(false);
      overlay.material = Array.isArray(original.material) ? original.material.map(wrap) : wrap(original.material);
      overlay.name = original.name + ' near-temple overlay';
      overlay.renderOrder = 1; overlay.visible = false;
      overlay.userData = {...original.userData, templeVisibilityOverlay: true};
      overlay.onBeforeRender = (backend, _scene, renderCamera, _geometry, material) => {
        restoreColorWrites();
        markLensInput(backend, renderCamera);
        pendingColorWrites.set(material, material.colorWrite);
        // Scene.onBeforeRender identifies the caller's actual output target.
        // Three's internal transmission target is entered without that callback.
        material.colorWrite = material.colorWrite && entryTargets.has(renderCamera)
          && backend.getRenderTarget() === entryTargets.get(renderCamera);
      };
      overlay.onAfterRender = () => restoreColorWrites();
      original.parent?.add(overlay);
      overlays.push(overlay);
    }
  } catch (error) {
    for (const overlay of overlays) overlay.removeFromParent();
    for (const material of materials.values()) material.dispose();
    restoreRenderHooks();
    restoreOriginalHooks();
    hiddenMaterial.dispose(); target.dispose(); depthMaterial.dispose(); cheekTarget.dispose(); cheekMaterial.dispose();
    throw error;
  }
  const previousSceneBeforeRender = scene.onBeforeRender;
  const sceneBeforeRender: Scene['onBeforeRender'] = function(this: Scene, ...args) {
    previousSceneBeforeRender.apply(this, args);
    if (!disposed && args[0] === renderer) entryTargets.set(args[2], renderer.getRenderTarget());
  };
  scene.onBeforeRender = sceneBeforeRender;
  return {
    get configuration(): TempleVisibilityConfiguration | null { return configuration ? copyConfiguration(configuration) : null; },
    set(value: TempleVisibilityConfiguration | null): void {
      if (disposed) throw new Error('Temple visibility is disposed.');
      if (value !== null) {
        validateTempleVisibility(value);
        if (value.cheekContact && !context.observedFaceSurface) throw new Error('Observed cheek contact requires a current face surface.');
        if (value.coverage === 'alpha-to-coverage' && renderer.capabilities.samples === 0) {
          throw new Error('The recorded temple visibility requires multisampling.');
        }
      }
      restoreColorWrites();
      configuration = value ? copyConfiguration(value) : null;
      frontalUniforms.templeFrontalCameraSource.value = null;
      frontalUniforms.templeExcludeArmsFromLensInput.value = value?.excludeArmsFromLensInput ? 1 : 0;
      frontalUniforms.templeInternalLensInput.value = 0;
      const contact = value?.cheekContact;
      frontalUniforms.templeCheekCount.value = contact?.polygon.length ?? 0;
      frontalUniforms.templeCheekTransitionPx.value = value?.cheekTransitionPx ?? 0;
      contact?.polygon.forEach((p, i) => frontalUniforms.templeCheekPolygon.value[i]!.set(p.x, p.y));
      if (value) uniforms.templeVisibilityRelief.value.set(value.reliefKeepCm, value.reliefDropCm);
      uniforms.templeVisibilityTerminalEnabled.value = value?.terminalReturn ? 1 : 0;
      uniforms.templeVisibilityTerminal.value.set(value?.terminalReturn?.startZM ?? 0, value?.terminalReturn?.endZM ?? 0);
      // The per-pixel rule applies at every pose.
      for (const overlay of overlays) overlay.visible = value !== null;
    },
    prepare(rawMatrix: readonly number[], cameraTexture?: CanvasTexture): void {
      if (disposed) throw new Error('Temple visibility is disposed.');
      restoreColorWrites();
      if (configuration === null) return;
      validatePose(rawMatrix);
      const size = renderer.getDrawingBufferSize(new Vector2());
      frontalUniforms.templeFrontalCameraSource.value = null;
      if (frontalUniforms.templeCheekCount.value >= 3) {
        const image = cameraTexture?.image as {width?: number; height?: number} | undefined;
        if (!(cameraTexture instanceof CanvasTexture) || cameraTexture.colorSpace !== SRGBColorSpace
            || !Number.isFinite(image?.width) || !Number.isFinite(image?.height)
            || !(image!.width! > 0 && image!.height! > 0)
            || !Number.isInteger(size.x) || !Number.isInteger(size.y) || size.x <= 0 || size.y <= 0) {
          throw new Error('Observed cheek occlusion requires the current paired sRGB camera texture and viewport.');
        }
        if (cameraTexture.matrixAutoUpdate) cameraTexture.updateMatrix();
        if (!cameraTexture.matrix.elements.every(Number.isFinite)) throw new Error('The observed cheek camera UV transform is invalid.');
        frontalUniforms.templeFrontalCameraSource.value = cameraTexture;
        frontalUniforms.templeFrontalViewport.value.copy(size);
        frontalUniforms.templeFrontalUvTransform.value.copy(cameraTexture.matrix);
      }
      // Validate the camera convention before mutating render state.
      templeLiftedDepth(0.5, camera.near, camera.far);
      const nextA2C = configuration.coverage === 'alpha-to-coverage';
      if (nextA2C !== alphaToCoverage) {
        alphaToCoverage = nextA2C;
        for (const material of materials.values()) { material.alphaToCoverage = nextA2C; material.needsUpdate = true; }
      }
      target.setSize(size.x, size.y); uniforms.templeVisibilitySize.value.copy(size);
      if (frontalUniforms.templeCheekCount.value >= 3) cheekTarget.setSize(size.x, size.y);
      uniforms.templeVisibilityNearFar.value.set(camera.near, camera.far);
      const saved = {
        visible: eyewearPose.visible, background: scene.background, override: scene.overrideMaterial,
        target: renderer.getRenderTarget(), face: renderer.getActiveCubeFace(), mip: renderer.getActiveMipmapLevel(),
        viewport: renderer.getViewport(new Vector4()), scissor: renderer.getScissor(new Vector4()),
        scissorTest: renderer.getScissorTest(), clearColor: renderer.getClearColor(new Color()),
        clearAlpha: renderer.getClearAlpha(), autoClear: renderer.autoClear,
      };
      try {
        eyewearPose.visible = false; scene.background = null; scene.overrideMaterial = depthMaterial;
        renderer.autoClear = false;
        renderer.setRenderTarget(target); renderer.setViewport(0, 0, size.x, size.y); renderer.setScissorTest(false);
        renderer.setClearColor(0, 0); renderer.clear(true, true, true);
        renderer.render(scene, camera);
        if (frontalUniforms.templeCheekCount.value >= 3) {
          renderer.setRenderTarget(cheekTarget); renderer.setViewport(0, 0, size.x, size.y);
          renderer.clear(true, true, true); renderer.render(cheekScene, camera);
        }
      } finally {
        eyewearPose.visible = saved.visible; scene.background = saved.background; scene.overrideMaterial = saved.override;
        renderer.autoClear = saved.autoClear;
        renderer.setClearColor(saved.clearColor, saved.clearAlpha);
        renderer.setRenderTarget(saved.target, saved.face, saved.mip);
        renderer.setViewport(saved.viewport); renderer.setScissor(saved.scissor); renderer.setScissorTest(saved.scissorTest);
      }
    },
    dispose(): void {
      if (disposed) return;
      disposed = true; configuration = null;
      frontalUniforms.templeFrontalCameraSource.value = null;
      frontalUniforms.templeCheekCount.value = 0;
      frontalUniforms.templeCheekTransitionPx.value = 0;
      frontalUniforms.templeExcludeArmsFromLensInput.value = frontalUniforms.templeInternalLensInput.value = 0;
      restoreColorWrites();
      if (scene.onBeforeRender === sceneBeforeRender) scene.onBeforeRender = previousSceneBeforeRender;
      for (const overlay of overlays) overlay.removeFromParent();
      for (const material of materials.values()) material.dispose();
      restoreRenderHooks();
      restoreOriginalHooks();
      hiddenMaterial.dispose(); target.dispose(); depthMaterial.dispose(); cheekTarget.dispose(); cheekMaterial.dispose(); cheekScene.clear();
    },
  };
}
