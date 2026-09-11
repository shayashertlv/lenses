import {
  CanvasTexture, Color, DepthTexture, DoubleSide, Material, MathUtils, Matrix3, Matrix4, Mesh, MeshBasicMaterial,
  MeshPhysicalMaterial, NearestFilter, ShaderMaterial, SRGBColorSpace, UnsignedIntType, Vector2, Vector3,
  Vector4, WebGLRenderTarget,
} from 'three';
import type {BufferGeometry, Object3D, PerspectiveCamera, Scene, WebGLRenderer} from 'three';

export const LEGACY_TEMPLE_VISIBILITY_METHOD = 'temple-side-depth-v1';
export const VIEW_TEMPLE_VISIBILITY_METHOD = 'temple-side-depth-v2';
export const TEMPLE_VISIBILITY_METHOD = 'temple-side-depth-v3';
export const TEMPLE_VISIBILITY_PARAMETERS = Object.freeze({
  lateralStartCm: 3.5, lateralEndCm: 4.5, lateralArmMinM: 0.045,
  rootBlendM: 0.003, placementCm: 0.05, farSideViewRamp: 0.35,
  viewConfidenceStart: 0.15, viewConfidenceFull: 0.35,
  frontalMaskRadiusPx: 1, frontalContinuationSteps: 4,
  frontalPitchStartDegrees: 8, frontalPitchFullDegrees: 18,
});

interface TempleVisibilityWeights {
  readonly negativeXWeight: number;
  readonly positiveXWeight: number;
  readonly coverage: 'alpha-to-coverage' | 'ordered-dither';
}

export interface LegacyTempleVisibilityConfiguration extends TempleVisibilityWeights {
  readonly method: typeof LEGACY_TEMPLE_VISIBILITY_METHOD;
}

export interface ViewTempleVisibilityConfiguration extends TempleVisibilityWeights {
  readonly method: typeof VIEW_TEMPLE_VISIBILITY_METHOD;
}

export interface CurrentTempleVisibilityConfiguration extends TempleVisibilityWeights {
  readonly method: typeof TEMPLE_VISIBILITY_METHOD;
  readonly frontalOcclusionWeight: number;
}

export type TempleVisibilityConfiguration = LegacyTempleVisibilityConfiguration | ViewTempleVisibilityConfiguration | CurrentTempleVisibilityConfiguration;

export function validateTempleVisibility(value: TempleVisibilityConfiguration): void {
  if (!value || value.method !== TEMPLE_VISIBILITY_METHOD && value.method !== VIEW_TEMPLE_VISIBILITY_METHOD && value.method !== LEGACY_TEMPLE_VISIBILITY_METHOD
      || [value.negativeXWeight, value.positiveXWeight].some(weight => !Number.isFinite(weight) || weight < 0 || weight > 1)
      || value.method === TEMPLE_VISIBILITY_METHOD && (!Number.isFinite(value.frontalOcclusionWeight)
        || value.frontalOcclusionWeight < 0 || value.frontalOcclusionWeight > 1)
      || value.coverage !== 'alpha-to-coverage' && value.coverage !== 'ordered-dither') {
    throw new Error('The recorded temple visibility configuration is invalid.');
  }
}

function inversePose(rawMatrix: readonly number[]): Matrix4 {
  if (rawMatrix.length !== 16 || !rawMatrix.every(Number.isFinite)) throw new Error('The temple visibility pose is invalid.');
  const matrix = new Matrix4().fromArray(rawMatrix);
  if (Math.abs(matrix.determinant()) < 1e-12) throw new Error('The temple visibility pose is singular.');
  return matrix.invert();
}

export function createLegacyTempleVisibilityConfiguration(rawMatrix: readonly number[], nativeSamples: number): LegacyTempleVisibilityConfiguration {
  if (!Number.isInteger(nativeSamples) || nativeSamples < 0) throw new Error('The native sample count is invalid.');
  const cameraLocal = new Vector3().applyMatrix4(inversePose(rawMatrix));
  const distance = Math.hypot(cameraLocal.x, cameraLocal.z);
  if (!(distance > 1e-12)) throw new Error('The temple visibility viewing direction is invalid.');
  const viewX = cameraLocal.x / distance;
  return {
    method: LEGACY_TEMPLE_VISIBILITY_METHOD,
    negativeXWeight: 1 - MathUtils.smoothstep(viewX, 0, TEMPLE_VISIBILITY_PARAMETERS.farSideViewRamp),
    positiveXWeight: 1 - MathUtils.smoothstep(-viewX, 0, TEMPLE_VISIBILITY_PARAMETERS.farSideViewRamp),
    coverage: nativeSamples > 0 ? 'alpha-to-coverage' : 'ordered-dither',
  };
}

function lateralConfidence(rawMatrix: readonly number[]): number {
  const cameraLocal = new Vector3().applyMatrix4(inversePose(rawMatrix));
  const cameraBearing = Math.abs(cameraLocal.x) / cameraLocal.length();
  const headOrientation = Math.abs(rawMatrix[8]!) / Math.hypot(rawMatrix[8]!, rawMatrix[9]!, rawMatrix[10]!);
  // Relief is conservative when translation and head heading disagree.
  // Ordinary head depth handles a frontal head seen away from image center.
  return MathUtils.smoothstep(Math.min(cameraBearing, headOrientation),
    TEMPLE_VISIBILITY_PARAMETERS.viewConfidenceStart, TEMPLE_VISIBILITY_PARAMETERS.viewConfidenceFull);
}

export function createViewTempleVisibilityConfiguration(rawMatrix: readonly number[], nativeSamples: number): ViewTempleVisibilityConfiguration {
  const legacy = createLegacyTempleVisibilityConfiguration(rawMatrix, nativeSamples);
  const confidence = lateralConfidence(rawMatrix);
  return {
    method: VIEW_TEMPLE_VISIBILITY_METHOD, coverage: legacy.coverage,
    negativeXWeight: legacy.negativeXWeight * confidence, positiveXWeight: legacy.positiveXWeight * confidence,
  };
}

/** Keep the actual stored permissions explicit; replay never re-estimates them. */
export function createTempleVisibilityConfiguration(rawMatrix: readonly number[], nativeSamples: number): CurrentTempleVisibilityConfiguration {
  const view = createViewTempleVisibilityConfiguration(rawMatrix, nativeSamples);
  const pitch = Math.abs(rawMatrix[9]!) / Math.hypot(rawMatrix[8]!, rawMatrix[9]!, rawMatrix[10]!);
  const frontalOcclusionWeight = (1 - lateralConfidence(rawMatrix)) * MathUtils.smoothstep(pitch,
    Math.sin(MathUtils.degToRad(TEMPLE_VISIBILITY_PARAMETERS.frontalPitchStartDegrees)),
    Math.sin(MathUtils.degToRad(TEMPLE_VISIBILITY_PARAMETERS.frontalPitchFullDegrees)));
  return {...view, method: TEMPLE_VISIBILITY_METHOD, frontalOcclusionWeight};
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

/** Diagnostic of the shader's bounded original-asset probe, in native UV space. */
export function templeFrontalProbeUV(position: readonly [number, number, number], modelView: Matrix4, projection: Matrix4, frontZM: number, fraction: number): Vector2 | null {
  if (![...position, ...modelView.elements, ...projection.elements, frontZM, fraction].every(Number.isFinite)
      || fraction < 0 || fraction > 1) throw new Error('The frontal temple probe is invalid.');
  const projected = new Vector4(...position, 1).applyMatrix4(modelView).applyMatrix4(projection);
  const front = new Vector4(position[0], position[1], frontZM, 1).applyMatrix4(modelView).applyMatrix4(projection);
  projected.lerp(front, fraction);
  if (projected.w <= 0) return null;
  const uv = new Vector2(projected.x / projected.w * .5 + .5, projected.y / projected.w * .5 + .5);
  return uv.x < 0 || uv.x > 1 || uv.y < 0 || uv.y > 1 ? null : uv;
}

interface VisibilityContext {
  renderer: WebGLRenderer;
  scene: Scene;
  camera: PerspectiveCamera;
  eyewearPose: Object3D;
}

const isLens = (material: Material): boolean => material instanceof MeshPhysicalMaterial && material.transmission > 0;

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
  const headInverse = {value: new Matrix4()};
  const depthMaterial = new ShaderMaterial({
    side: DoubleSide, depthWrite: true, depthTest: true,
    uniforms: {headInverse},
    vertexShader: `uniform mat4 headInverse; varying float lateralX;
      void main() { vec4 world = modelMatrix * vec4(position, 1.0);
        lateralX = (headInverse * world).x;
        gl_Position = projectionMatrix * viewMatrix * world; }`,
    fragmentShader: `varying float lateralX;
      void main() { gl_FragColor = vec4(smoothstep(${TEMPLE_VISIBILITY_PARAMETERS.lateralStartCm.toFixed(1)},
        ${TEMPLE_VISIBILITY_PARAMETERS.lateralEndCm.toFixed(1)}, abs(lateralX)), 0.0, 0.0, 1.0); }`,
  });
  const uniforms = {
    templeVisibilityHeadDepth: {value: target.depthTexture}, templeVisibilityHeadMask: {value: target.texture},
    templeVisibilitySize: {value: new Vector2(1, 1)}, templeVisibilityNearFar: {value: new Vector2(camera.near, camera.far)},
    templeVisibilityWeights: {value: new Vector2()}, templeVisibilityFront: {value: frontZM},
    templeVisibilityDitherThresholds: {value: new Float32Array([
      0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5,
    ].map(rank => (rank + 0.5) / 16))},
  };
  const frontalUniforms = {
    templeFrontalWeight: {value: 0}, templeFrontalHeadMask: {value: target.texture},
    templeFrontalFront: {value: frontZM}, templeFrontalCameraSource: {value: null as CanvasTexture | null},
    templeFrontalViewport: {value: new Vector2(1, 1)}, templeFrontalUvTransform: {value: new Matrix3()},
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
      shader.vertexShader = `varying vec3 templeFrontalOriginalPosition;
        uniform float templeFrontalFront;
        varying vec4 templeFrontalOriginalProjection, templeFrontalFrontProjection;\n` + shader.vertexShader.replace(
        '#include <begin_vertex>', `#include <begin_vertex>
          templeFrontalOriginalPosition = position;
          templeFrontalOriginalProjection = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          templeFrontalFrontProjection = projectionMatrix * modelViewMatrix * vec4(position.xy, templeFrontalFront, 1.0);`);
      shader.fragmentShader = `varying vec3 templeFrontalOriginalPosition;
        varying vec4 templeFrontalOriginalProjection, templeFrontalFrontProjection;
        uniform float templeFrontalWeight, templeFrontalFront;
        uniform sampler2D templeFrontalCameraSource, templeFrontalHeadMask;
        uniform vec2 templeFrontalViewport;
        uniform mat3 templeFrontalUvTransform;
        float templeFrontalMask(vec2 uv) {
          float coverage = 0.0;
          for (int y = -1; y <= 1; y++) {
            for (int x = -1; x <= 1; x++) {
              float weight = (x == 0 ? 2.0 : 1.0) * (y == 0 ? 2.0 : 1.0);
              vec2 offset = vec2(float(x), float(y)) * ${TEMPLE_VISIBILITY_PARAMETERS.frontalMaskRadiusPx.toFixed(1)} / templeFrontalViewport;
              coverage += weight * texture2D(templeFrontalHeadMask, uv + offset).a / 16.0;
            }
          }
          return coverage;
        }\n` + shader.fragmentShader;
      // Color only: preserve opaque depth/coverage and exclude the optical front.
      // Later overlay wrapping runs its coverage/depth block before this footer;
      // the earlier clip wrapper's endpoint RGB footer runs afterwards.
      // This baseline renders beauty to the native ACES framebuffer. Three's
      // internal transmission pass has no TONE_MAPPING; keep its lens input exact.
      shader.fragmentShader = shader.fragmentShader.replace('#include <dithering_fragment>', `#include <dithering_fragment>
        #ifdef TONE_MAPPING
        if (templeFrontalWeight > 0.0 && abs(templeFrontalOriginalPosition.x) > ${TEMPLE_VISIBILITY_PARAMETERS.lateralArmMinM}
            && templeFrontalOriginalPosition.z < templeFrontalFront) {
          float templeFrontalRoot = smoothstep(0.0, ${TEMPLE_VISIBILITY_PARAMETERS.rootBlendM}, templeFrontalFront - templeFrontalOriginalPosition.z);
          vec2 templeFrontalMaskUV = gl_FragCoord.xy / templeFrontalViewport;
          float templeFrontalInside = templeFrontalMask(templeFrontalMaskUV);
          for (int templeStep = 1; templeStep <= ${TEMPLE_VISIBILITY_PARAMETERS.frontalContinuationSteps}; templeStep++) {
            vec4 templeProbeProjection = mix(templeFrontalOriginalProjection, templeFrontalFrontProjection,
              float(templeStep) / ${TEMPLE_VISIBILITY_PARAMETERS.frontalContinuationSteps.toFixed(1)});
            if (templeProbeProjection.w > 0.0) {
              vec2 templeProbeUV = templeProbeProjection.xy / templeProbeProjection.w * 0.5 + 0.5;
              if (all(greaterThanEqual(templeProbeUV, vec2(0.0))) && all(lessThanEqual(templeProbeUV, vec2(1.0))))
                templeFrontalInside = max(templeFrontalInside, templeFrontalMask(templeProbeUV));
            }
          }
          vec2 templeFrontalUV = (templeFrontalUvTransform * vec3(gl_FragCoord.xy / templeFrontalViewport, 1.0)).xy;
          vec3 templeFrontalCameraRGB = linearToOutputTexel(texture2D(templeFrontalCameraSource, templeFrontalUV)).rgb;
          gl_FragColor.rgb = mix(gl_FragColor.rgb, templeFrontalCameraRGB, templeFrontalWeight * templeFrontalRoot * templeFrontalInside);
        }
        #endif`);
    };
    const wrapperKey = () => `${key === Material.prototype.customProgramCacheKey ? hook.toString() : key.call(material)}|temple-frontal-rgb-v3`;
    originalHooks.set(material, {hook, key, wrapper, wrapperKey});
    material.onBeforeCompile = wrapper; material.customProgramCacheKey = wrapperKey; material.needsUpdate = true;
  };
  const overlays: Mesh[] = [], materials = new Map<Material, Material>();
  const entryTargets = new WeakMap<Object3D, WebGLRenderTarget | null>();
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
      const endpointCoverage = shader.uniforms.templeClipEnabled ? 'templeEndpointCoverage' : '1.0';
      Object.assign(shader.uniforms, uniforms);
      shader.vertexShader = 'varying vec3 templeVisibilityPosition;\n' + shader.vertexShader.replace(
        '#include <begin_vertex>', '#include <begin_vertex>\ntempleVisibilityPosition = position;');
      shader.fragmentShader = `varying vec3 templeVisibilityPosition;
        uniform sampler2D templeVisibilityHeadDepth, templeVisibilityHeadMask;
        uniform vec2 templeVisibilitySize, templeVisibilityNearFar, templeVisibilityWeights;
        uniform float templeVisibilityFront, templeVisibilityDitherThresholds[16];
        ` + shader.fragmentShader;
      // This must run after the clip wrapper's opaque_fragment alpha override.
      // It changes only an overlay fragment's tested depth, never depth-buffer contents.
      shader.fragmentShader = shader.fragmentShader.replace('#include <dithering_fragment>', `#include <dithering_fragment>
        if (abs(templeVisibilityPosition.x) <= ${TEMPLE_VISIBILITY_PARAMETERS.lateralArmMinM}
            || templeVisibilityPosition.z >= templeVisibilityFront) discard;
        vec2 templeVisibilityUV = gl_FragCoord.xy / templeVisibilitySize;
        float templeSideMask = texture2D(templeVisibilityHeadMask, templeVisibilityUV).r;
        float templeSideWeight = templeVisibilityPosition.x < 0.0 ? templeVisibilityWeights.x : templeVisibilityWeights.y;
        float templeRootWeight = smoothstep(0.0, ${TEMPLE_VISIBILITY_PARAMETERS.rootBlendM}, templeVisibilityFront - templeVisibilityPosition.z);
        float templeOverlayCoverage = ${endpointCoverage} * templeSideMask * templeSideWeight * templeRootWeight;
        if (templeOverlayCoverage <= 0.0) discard;
        ${material.alphaToCoverage ? 'gl_FragColor.a = templeOverlayCoverage;' : `
          int templeVisibilityDitherIndex = int(mod(floor(gl_FragCoord.x), 4.0) + 4.0 * mod(floor(gl_FragCoord.y), 4.0));
          if (templeOverlayCoverage < templeVisibilityDitherThresholds[templeVisibilityDitherIndex]) discard;
          gl_FragColor.a = 1.0;`}
        float templeSurfaceDepth = texture2D(templeVisibilityHeadDepth, templeVisibilityUV).r;
        float templeNear = templeVisibilityNearFar.x, templeFar = templeVisibilityNearFar.y;
        float templeViewZ = templeNear * templeFar / ((templeFar - templeNear) * templeSurfaceDepth - templeFar);
        float templeAheadZ = min(-templeNear, templeViewZ + ${TEMPLE_VISIBILITY_PARAMETERS.placementCm});
        float templeLifted = ((templeNear + templeAheadZ) * templeFar) / ((templeFar - templeNear) * templeAheadZ);
        gl_FragDepth = min(gl_FragCoord.z, templeLifted);`);
    };
    material.customProgramCacheKey = () => `${key === Material.prototype.customProgramCacheKey
      ? hook.toString() : key.call(original)}|${TEMPLE_VISIBILITY_METHOD}|${material.alphaToCoverage ? 'a2c' : 'dither'}`;
    materials.set(original, material);
    return material;
  };
  try {
    // Wrap each original once, before clones inherit its live uniform owner.
    for (const original of originals) {
      for (const material of Array.isArray(original.material) ? original.material : [original.material]) wrapFrontal(material);
    }
    for (const original of originals) {
      const overlay = original.clone(false);
      overlay.material = Array.isArray(original.material) ? original.material.map(wrap) : wrap(original.material);
      overlay.name = original.name + ' near-temple overlay';
      overlay.renderOrder = 1; overlay.visible = false;
      overlay.userData = {...original.userData, templeVisibilityOverlay: true};
      overlay.onBeforeRender = (backend, _scene, renderCamera, _geometry, material) => {
        restoreColorWrites();
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
    restoreOriginalHooks();
    hiddenMaterial.dispose(); target.dispose(); depthMaterial.dispose();
    throw error;
  }
  const previousSceneBeforeRender = scene.onBeforeRender;
  const sceneBeforeRender: Scene['onBeforeRender'] = function(this: Scene, ...args) {
    previousSceneBeforeRender.apply(this, args);
    if (!disposed && args[0] === renderer) entryTargets.set(args[2], renderer.getRenderTarget());
  };
  scene.onBeforeRender = sceneBeforeRender;
  return {
    get configuration(): TempleVisibilityConfiguration | null { return configuration ? {...configuration} : null; },
    set(value: TempleVisibilityConfiguration | null): void {
      if (disposed) throw new Error('Temple visibility is disposed.');
      if (value !== null) {
        validateTempleVisibility(value);
        if (value.coverage === 'alpha-to-coverage' && renderer.capabilities.samples === 0) {
          throw new Error('The recorded temple visibility requires multisampling.');
        }
      }
      restoreColorWrites();
      configuration = value ? {...value} : null;
      frontalUniforms.templeFrontalCameraSource.value = null;
      frontalUniforms.templeFrontalWeight.value = value?.method === TEMPLE_VISIBILITY_METHOD ? value.frontalOcclusionWeight : 0;
      uniforms.templeVisibilityWeights.value.set(value?.negativeXWeight ?? 0, value?.positiveXWeight ?? 0);
      for (const overlay of overlays) overlay.visible = value !== null;
    },
    prepare(rawMatrix: readonly number[], cameraTexture?: CanvasTexture): void {
      if (disposed) throw new Error('Temple visibility is disposed.');
      restoreColorWrites();
      if (configuration === null) return;
      const inverse = inversePose(rawMatrix);
      const size = renderer.getDrawingBufferSize(new Vector2());
      frontalUniforms.templeFrontalCameraSource.value = null;
      if (frontalUniforms.templeFrontalWeight.value > 0) {
        const image = cameraTexture?.image as {width?: number; height?: number} | undefined;
        if (!(cameraTexture instanceof CanvasTexture) || cameraTexture.colorSpace !== SRGBColorSpace
            || !Number.isFinite(image?.width) || !Number.isFinite(image?.height)
            || !(image!.width! > 0 && image!.height! > 0)
            || !Number.isInteger(size.x) || !Number.isInteger(size.y) || size.x <= 0 || size.y <= 0) {
          throw new Error('Frontal temple occlusion requires the current paired sRGB camera texture and viewport.');
        }
        if (cameraTexture.matrixAutoUpdate) cameraTexture.updateMatrix();
        if (!cameraTexture.matrix.elements.every(Number.isFinite)) throw new Error('The frontal temple camera UV transform is invalid.');
        frontalUniforms.templeFrontalCameraSource.value = cameraTexture;
        frontalUniforms.templeFrontalViewport.value.copy(size);
        frontalUniforms.templeFrontalUvTransform.value.copy(cameraTexture.matrix);
      }
      // Validate the camera convention before mutating render state.
      templeLiftedDepth(0.5, camera.near, camera.far);
      headInverse.value.copy(inverse);
      const nextA2C = configuration.coverage === 'alpha-to-coverage';
      if (nextA2C !== alphaToCoverage) {
        alphaToCoverage = nextA2C;
        for (const material of materials.values()) { material.alphaToCoverage = nextA2C; material.needsUpdate = true; }
      }
      target.setSize(size.x, size.y); uniforms.templeVisibilitySize.value.copy(size);
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
      disposed = true; configuration = null; uniforms.templeVisibilityWeights.value.set(0, 0);
      frontalUniforms.templeFrontalWeight.value = 0; frontalUniforms.templeFrontalCameraSource.value = null;
      restoreColorWrites();
      if (scene.onBeforeRender === sceneBeforeRender) scene.onBeforeRender = previousSceneBeforeRender;
      for (const overlay of overlays) overlay.removeFromParent();
      for (const material of materials.values()) material.dispose();
      restoreOriginalHooks();
      hiddenMaterial.dispose(); target.dispose(); depthMaterial.dispose();
    },
  };
}
