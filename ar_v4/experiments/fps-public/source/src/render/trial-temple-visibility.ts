/**
 * Trial-only physical frame visibility. The accepted two-model renderer keeps
 * temple-visibility.ts unchanged. Optical membership comes from the host's
 * validated semantic material policy, never from transmission being nonzero.
 */
import {
  CanvasTexture, Color, DepthTexture, DoubleSide, Material, Matrix3, Matrix4, Mesh, MeshBasicMaterial,
  NearestFilter, ShaderMaterial, SRGBColorSpace, UnsignedIntType, Vector2, Vector4, WebGLRenderTarget,
} from 'three';
import type {BufferGeometry, Object3D, PerspectiveCamera, Scene, WebGLRenderer} from 'three';
import {TEMPLE_VISIBILITY_METHOD, TEMPLE_VISIBILITY_PARAMETERS, templeLiftedDepth, validateTempleVisibility}
  from './temple-visibility.ts';
import type {TempleVisibilityConfiguration} from './temple-visibility.ts';

interface VisibilityContext {
  renderer: WebGLRenderer;
  scene: Scene;
  camera: PerspectiveCamera;
  eyewearPose: Object3D;
}

function inversePose(rawMatrix: readonly number[]): Matrix4 {
  if (rawMatrix.length !== 16 || !rawMatrix.every(Number.isFinite)) throw new Error('The trial temple pose is invalid.');
  const matrix = new Matrix4().fromArray(rawMatrix);
  if (Math.abs(matrix.determinant()) < 1e-12) throw new Error('The trial temple pose is singular.');
  return matrix.invert();
}

/** Same bounded pose policy; physical frame and overlay materials retain their shaders. */
export function createTrialTempleVisibility(root: Object3D, context: VisibilityContext, isLens: (material: Material) => boolean) {
  const {renderer, scene, camera, eyewearPose} = context;
  const originals: Mesh[] = [];
  let frontZM = Infinity, opticalFrontZM = Infinity;
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
    const geometry: BufferGeometry = object.geometry;
    const position = geometry.getAttribute('position'), index = geometry.getIndex();
    if (!position) return;
    // The crystal front and the arms share a material. Protect the complete
    // authored front by geometry role, not only the thinner optical surfaces.
    // Generic hardware can include full-length internal cores, so it cannot
    // establish this boundary. The explicit optical predicate is unchanged.
    const role: unknown = object.userData.part_role;
    const protectedFront = role === 'frame' || role === 'rim_left' || role === 'rim_right'
      || role === 'bridge' || role === 'pad_left' || role === 'pad_right';
    if (materials.some(material => !isLens(material) && !material.transparent)) originals.push(object);
    for (const [materialIndex, material] of materials.entries()) {
      const optical = isLens(material);
      if (!optical && !protectedFront) continue;
      const groups = Array.isArray(object.material)
        ? geometry.groups.filter(group => group.materialIndex === materialIndex)
        : [{start: 0, count: index?.count ?? position.count}];
      for (const group of groups) for (let i = group.start; i < group.start + group.count; i++) {
        const z = position.getZ(index ? index.getX(i) : i);
        frontZM = Math.min(frontZM, z);
        if (optical) opticalFrontZM = Math.min(opticalFrontZM, z);
      }
    }
  });
  if (!Number.isFinite(opticalFrontZM) || !Number.isFinite(frontZM) || originals.length === 0)
    throw new Error('Trial temple visibility requires classified frame parts and physical optical lenses.');
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
    templeFrontalBeautyPass: {value: 0}, templeFrontalWeight: {value: 0}, templeFrontalHeadMask: {value: target.texture},
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
        uniform float templeFrontalWeight, templeFrontalFront, templeFrontalBeautyPass;
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
      // Crystal parts intentionally have toneMapped=false, so TONE_MAPPING is
      // not a pass identifier. Object hooks select the actual caller's beauty
      // target; the internal transmission/backface targets must retain physical radiance.
      shader.fragmentShader = shader.fragmentShader.replace('#include <dithering_fragment>', `#include <dithering_fragment>
        if (templeFrontalBeautyPass > 0.5) {
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
        }`);
    };
    const wrapperKey = () => `${key === Material.prototype.customProgramCacheKey ? hook.toString() : key.call(material)}|trial-temple-frontal-physical-v1`;
    originalHooks.set(material, {hook, key, wrapper, wrapperKey});
    material.onBeforeCompile = wrapper; material.customProgramCacheKey = wrapperKey; material.needsUpdate = true;
  };
  const overlays: Mesh[] = [], materials = new Map<Material, Material>();
  const entryTargets = new WeakMap<Object3D, WebGLRenderTarget | null>();
  const objectHooks = new Map<Mesh, {before: Mesh['onBeforeRender']; wrapper: Mesh['onBeforeRender']}>();
  const selectBeautyPass = (backend: WebGLRenderer, renderCamera: Object3D): void => {
    frontalUniforms.templeFrontalBeautyPass.value = backend === renderer && entryTargets.has(renderCamera)
      && backend.getRenderTarget() === entryTargets.get(renderCamera) ? 1 : 0;
  };
  const restoreObjectHooks = (): void => {
    for (const [object, saved] of objectHooks) if (object.onBeforeRender === saved.wrapper) object.onBeforeRender = saved.before;
    objectHooks.clear();
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
      ? hook.toString() : key.call(original)}|trial-temple-side-physical-v1|${material.alphaToCoverage ? 'a2c' : 'dither'}`;
    materials.set(original, material);
    return material;
  };
  try {
    // Wrap each original once, before clones inherit its live uniform owner.
    for (const original of originals) {
      const before = original.onBeforeRender;
      const wrapper: Mesh['onBeforeRender'] = function(this: Mesh, ...args) {
        before.apply(this, args);
        selectBeautyPass(args[0], args[2]);
      };
      objectHooks.set(original, {before, wrapper});
      original.onBeforeRender = wrapper;
      for (const material of Array.isArray(original.material) ? original.material : [original.material]) wrapFrontal(material);
    }
    for (const original of originals) {
      const overlay = original.clone(false);
      overlay.material = Array.isArray(original.material) ? original.material.map(wrap) : wrap(original.material);
      overlay.name = original.name + ' near-temple overlay';
      overlay.renderOrder = 1; overlay.visible = false;
      overlay.userData = {...original.userData, templeVisibilityOverlay: true};
      overlay.onBeforeRender = (backend, _scene, renderCamera, _geometry, material) => {
        selectBeautyPass(backend, renderCamera);
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
    restoreOriginalHooks(); restoreObjectHooks();
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
      frontalUniforms.templeFrontalBeautyPass.value = 0; frontalUniforms.templeFrontalWeight.value = 0; frontalUniforms.templeFrontalCameraSource.value = null;
      restoreColorWrites();
      if (scene.onBeforeRender === sceneBeforeRender) scene.onBeforeRender = previousSceneBeforeRender;
      for (const overlay of overlays) overlay.removeFromParent();
      for (const material of materials.values()) material.dispose();
      restoreOriginalHooks(); restoreObjectHooks();
      hiddenMaterial.dispose(); target.dispose(); depthMaterial.dispose();
    },
  };
}
