/** Canonical optical response in the production physical-material transport.
 * Supports authored +Z front sheets and explicitly grouped closed/multipart
 * geometry. Groups contribute their nearest interaction per camera/light ray.
 * Optional rear reflection preserves reciprocal transmission; neither profile
 * simulates refraction or internal reflections.
 */
import {Box3, DoubleSide, FrontSide, Material, Mesh, MeshPhysicalMaterial, Vector2, Vector3} from 'three';
import type {Object3D, Texture} from 'three';
import {createLensAppearanceUniforms, LENS_APPEARANCE_EXTENSION, LENS_INCIDENCE_GLSL, LENS_RESPONSE_GLSL, readMaterialLensAppearance,
  validateLensAppearance} from '../eyewear/lens-appearance.ts';
import type {LensAppearanceDescriptor} from '../eyewear/lens-appearance.ts';
import {classifyAssetMaterials, EFFECTIVE_OPTICAL_GROUP_PROFILE, isOpticalMaterial} from '../eyewear/optical-material.ts';
import {validateCanonicalOpticalTopology} from './optical-topology.ts';
import {validateEffectiveOpticalGroups} from './effective-optical-topology.ts';

export const CANONICAL_LENS_SURFACE_PROFILE = 'front_sheet_v1';

export interface CanonicalLensRenderState {
  /** 0: standalone; 1: far-to-near peel; 2: display; 3: effective-group nearest capture. */
  uCanonicalLayerMode: {value: number};
  uCanonicalLayerColor: {value: Texture | null};
  uCanonicalOpaqueDepth: {value: Texture | null};
  uCanonicalLayerSize: {value: Vector2};
  uCanonicalFirstLayer: {value: boolean};
  uCanonicalGroupEnabled: {value: boolean};
  uCanonicalGroupNearest: {value: Texture | null};
}
const renderStates = new WeakMap<Material, CanonicalLensRenderState>();
export function canonicalLensRenderState(material: Material): CanonicalLensRenderState {
  const state = renderStates.get(material);
  requireCondition(state, 'material was not installed by the canonical adapter.');
  return state;
}

function requireCondition(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Canonical lens: ${message}`);
}

/** Verify the runtime profile before mutating any material in the asset.
 * Metadata declares the intended interface; these checks reject common volume,
 * winding and coordinate mistakes but do not prove global surface topology.
 */
export function validateCanonicalLensSurface(mesh: Mesh): Box3 {
  requireCondition(mesh.userData.partRole === 'lens', 'partRole must explicitly be lens.');
  requireCondition(mesh.userData.lensSurfaceProfile === CANONICAL_LENS_SURFACE_PROFILE,
    `lensSurfaceProfile must explicitly be ${CANONICAL_LENS_SURFACE_PROFILE}.`);
  requireCondition(!('isSkinnedMesh' in mesh) && !('isInstancedMesh' in mesh) && !('isBatchedMesh' in mesh)
    && !mesh.morphTargetInfluences?.length,
  'bake skinning, instancing, batching and morph targets before authoring the optical sheet.');
  const geometry = mesh.geometry, position = geometry.getAttribute('position');
  const normal = geometry.getAttribute('normal'), uv = geometry.getAttribute('uv'), index = geometry.getIndex();
  requireCondition(position?.itemSize === 3 && position.count >= 3, 'finite XYZ positions are required.');
  requireCondition(normal?.itemSize === 3 && normal.count === position.count, 'one authored normal per vertex is required.');
  requireCondition(uv?.itemSize === 2 && uv.count === position.count, 'intrinsic TEXCOORD_0 is required.');
  const bounds = new Box3(), point = new Vector3();
  let minimumV = Infinity, maximumV = -Infinity, sumY = 0, sumV = 0, sumYV = 0;
  for (let i = 0; i < position.count; i++) {
    const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
    const nx = normal.getX(i), ny = normal.getY(i), nz = normal.getZ(i), u = uv.getX(i), v = uv.getY(i);
    requireCondition([x, y, z, nx, ny, nz, u, v].every(Number.isFinite), 'geometry/UV/normal contains nonfinite values.');
    requireCondition(Math.hypot(nx, ny, nz) > 1e-8 && nz > 0, 'front-sheet normals must point toward +Z.');
    requireCondition(v >= 0 && v <= 1, 'intrinsic vertical coordinates must lie in [0,1].');
    bounds.expandByPoint(point.set(x, y, z));
    minimumV = Math.min(minimumV, v); maximumV = Math.max(maximumV, v);
    sumY += y; sumV += v; sumYV += y * v;
  }
  requireCondition(minimumV <= 1e-6 && maximumV >= 1 - 1e-6, 'UV height must cover bottom 0 through top 1.');
  requireCondition(sumYV - sumY * sumV / position.count > 0, 'intrinsic UV height is reversed or unrelated to authored +Y height.');
  const count = index?.count ?? position.count;
  requireCondition(count > 0 && count % 3 === 0, 'complete optical triangles are required.');
  const a = new Vector3(), b = new Vector3(), c = new Vector3(), n = new Vector3();
  const areaTolerance = Math.max(bounds.getSize(new Vector3()).lengthSq() * 1e-14, Number.MIN_VALUE);
  for (let offset = 0; offset < count; offset += 3) {
    const indices = [0, 1, 2].map(corner => index ? index.getX(offset + corner) : offset + corner);
    requireCondition(indices.every(i => Number.isInteger(i) && i >= 0 && i < position.count), 'invalid triangle index.');
    a.fromBufferAttribute(position, indices[0]!); b.fromBufferAttribute(position, indices[1]!); c.fromBufferAttribute(position, indices[2]!);
    n.subVectors(b, a).cross(c.sub(a));
    requireCondition(n.z > areaTolerance, 'sheet winding must face +Z, without collapsed or backward triangles.');
  }
  return bounds;
}

function replaceOnce(source: string, marker: string, value: string): string {
  requireCondition(source.split(marker).length === 2, `pinned Three shader marker changed: ${marker}`);
  return source.replace(marker, value);
}

export function createCanonicalLensMaterial(source: Material, descriptor: LensAppearanceDescriptor,
  effectiveGroupId?: string): MeshPhysicalMaterial {
  if (effectiveGroupId !== undefined) requireCondition(/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(effectiveGroupId),
    'effective optical group identity is invalid.');
  const effective = effectiveGroupId !== undefined;
  descriptor = validateLensAppearance(descriptor);
  const uniforms = createLensAppearanceUniforms(descriptor);
  const state: CanonicalLensRenderState = {
    uCanonicalLayerMode: {value: 0}, uCanonicalLayerColor: {value: null},
    uCanonicalOpaqueDepth: {value: null}, uCanonicalLayerSize: {value: new Vector2(1, 1)},
    uCanonicalFirstLayer: {value: true},
    uCanonicalGroupEnabled: {value: effective}, uCanonicalGroupNearest: {value: null},
  };
  // Positive physical transmission selects Three's existing opaque/background
  // prepass, even for a canonical total mirror. It is NOT the lens coefficient.
  const material = new MeshPhysicalMaterial({name: source.name, color: 0xffffff, metalness: 0,
    roughness: descriptor.roughness, ior: descriptor.refractive_index, transmission: 1, thickness: 0,
    side: effective ? DoubleSide : FrontSide, transparent: false, opacity: 1, depthTest: true, depthWrite: true, toneMapped: false});
  material.forceSinglePass = true;
  material.userData = JSON.parse(JSON.stringify(source.userData));
  material.userData.gltfExtensions = {...material.userData.gltfExtensions,
    [LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: descriptor}};
  material.userData.canonicalLensProfile = effective ? EFFECTIVE_OPTICAL_GROUP_PROFILE : CANONICAL_LENS_SURFACE_PROFILE;
  if (effective) material.userData.canonicalOpticalGroupId = effectiveGroupId;
  renderStates.set(material, state);
  material.customProgramCacheKey = () => effective ? 'canonical-effective-group-transport-v2' : 'canonical-lens-layer-transport-v3';
  material.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms, state, {uCanonicalReflectionRoughness: {value: descriptor.roughness}});
    shader.vertexShader = 'varying float vCanonicalLensV;\nvarying vec3 vCanonicalWorldPosition, vCanonicalFrontAxis;\n' + replaceOnce(shader.vertexShader,
      '#include <uv_vertex>', '#include <uv_vertex>\nvCanonicalLensV = uv.y;');
    shader.vertexShader = replaceOnce(shader.vertexShader, '#include <worldpos_vertex>',
      '#include <worldpos_vertex>\nvCanonicalWorldPosition = (modelMatrix * vec4(transformed, 1.0)).xyz;\n'
        + 'vCanonicalFrontAxis = normalize((modelViewMatrix * vec4(0.0, 0.0, 1.0, 0.0)).xyz);');
    shader.fragmentShader = 'varying float vCanonicalLensV;\nuniform float uCanonicalReflectionRoughness;\n'
      + `varying vec3 vCanonicalWorldPosition, vCanonicalFrontAxis;
         uniform float uCanonicalLayerMode;
         uniform sampler2D uCanonicalLayerColor, uCanonicalOpaqueDepth;
         uniform vec2 uCanonicalLayerSize;
         uniform bool uCanonicalFirstLayer;\n`
      + (effective ? 'uniform bool uCanonicalGroupEnabled;\nuniform sampler2D uCanonicalGroupNearest;\n' + LENS_INCIDENCE_GLSL : '')
      + LENS_RESPONSE_GLSL + shader.fragmentShader;
    if (effective) shader.fragmentShader = replaceOnce(shader.fragmentShader, '#include <clipping_planes_fragment>', /* glsl */`
      #include <clipping_planes_fragment>
      if (uCanonicalLayerMode > 2.5) {
        // Same material and vertex shader as the optical peel: no separate
        // position arithmetic may move a nearest-map hit across a depth tie.
        gl_FragColor = vec4(0.0, 0.0, 0.0, gl_FragCoord.z);
        return;
      }
    `);
    shader.fragmentShader = replaceOnce(shader.fragmentShader, '#include <opaque_fragment>', /* glsl */`
      vec2 canonicalPixel = gl_FragCoord.xy / uCanonicalLayerSize;
      ${effective ? `
      // Composition selects one event per group. Display copies the same
      // already-composed RGB for every covered optical fragment, so repeated
      // surfaces are idempotent under the normal scene depth test. Comparing
      // the multisampled display depth with a single-sample capture here would
      // discard triangle-edge fragments and expose a mesh-shaped crack pattern.
      if (uCanonicalGroupEnabled && uCanonicalLayerMode < 1.5) {
        float nearestGroupDepth = texture2D(uCanonicalGroupNearest, canonicalPixel).a;
        if (nearestGroupDepth >= 1.0 || gl_FragCoord.z != nearestGroupDepth) discard;
      }` : ''}
      if (uCanonicalLayerMode > 1.5) {
        gl_FragColor = vec4(texture2D(uCanonicalLayerColor, canonicalPixel).rgb, 1.0);
      } else {
      if (uCanonicalLayerMode > 0.5) {
        if (gl_FragCoord.z >= texture2D(uCanonicalOpaqueDepth, canonicalPixel).r) discard;
        // Alpha stores exact shader float depth, avoiding a quantized depth
        // comparison that can select the same sheet on successive peels.
        if (!uCanonicalFirstLayer && gl_FragCoord.z >= texture2D(uCanonicalLayerColor, canonicalPixel).a) discard;
      }
      vec3 canonicalView = isOrthographic ? vec3(0.0, 0.0, 1.0) : normalize(vViewPosition);
      vec3 canonicalNormal = normalize(normal);
      ${effective ? `if (dot(canonicalNormal, canonicalView) < 0.0) canonicalNormal = -canonicalNormal;
      float canonicalAngle = lensIncidenceAngleDegrees(dot(canonicalNormal, canonicalView));`
        : 'float canonicalAngle = degrees(acos(clamp(dot(canonicalNormal, canonicalView), 0.0, 1.0)));'}
      // Vertex UVs are validated; clamp only interpolator roundoff at endpoints.
      bool canonicalRearSide = dot(normalize(vCanonicalFrontAxis), canonicalView) < 0.0;
      LensResponse canonicalResponse = evaluateLensResponse(clamp(vCanonicalLensV, 0.0, 1.0), canonicalAngle, canonicalRearSide);
      vec3 canonicalBackground = vec3(0.0);
      #ifdef USE_TRANSMISSION
        vec4 canonicalClip = projectionMatrix * viewMatrix * vec4(vCanonicalWorldPosition, 1.0);
        vec2 canonicalScreen = canonicalClip.xy / canonicalClip.w * 0.5 + 0.5;
        canonicalBackground = texture2D(transmissionSamplerMap, canonicalScreen).rgb;
      #endif
      if (uCanonicalLayerMode > 0.5) canonicalBackground = texture2D(uCanonicalLayerColor, canonicalPixel).rgb;
      vec3 canonicalEnvironment = vec3(0.0);
      #ifdef USE_ENVMAP
        canonicalEnvironment = getIBLRadiance(canonicalView, canonicalNormal, uCanonicalReflectionRoughness);
      #endif
      // The optical response is already energy-accounted. Do not reuse the
      // physical material's Fresnel, fallback color, opacity or absorption.
      gl_FragColor = vec4(canonicalResponse.transmission * canonicalBackground
        + canonicalResponse.reflectance * canonicalEnvironment, uCanonicalLayerMode > 0.5 ? gl_FragCoord.z : 1.0);
      }
    `);
  };
  return material;
}

export interface CanonicalLensInstallation {
  readonly meshCount: number;
  readonly materials: readonly MeshPhysicalMaterial[];
  readonly profile: typeof CANONICAL_LENS_SURFACE_PROFILE | typeof EFFECTIVE_OPTICAL_GROUP_PROFILE | 'none';
  readonly effectiveGroups: readonly {id: string; meshes: readonly Mesh[]}[];
  /** Retain these until scene disposal: fallback textures may be shared. */
  readonly replacedMaterials: readonly Material[];
  /** Transmissive materials that authored part roles classified as frame (crystal / translucent acetate). They are
   * rendered by Three's physical transmission and are not optics anywhere in the runtime. */
  readonly translucentFrameMaterials: readonly Material[];
}

export function installCanonicalLensMaterials(root: Object3D): CanonicalLensInstallation {
  const pending: {mesh: Mesh; source: Material; descriptor: LensAppearanceDescriptor; effective: boolean}[] = [];
  let legacyOptics = false, translucentFrame = false;
  root.updateWorldMatrix(true, true);
  // Roles first: a transmissive material owned only by frame/temple parts of a canonical asset is frame, so the
  // legacy-optics test below and every later consumer see it as such.
  const classification = classifyAssetMaterials(root);
  const inverseRoot = root.matrixWorld.clone().invert();
  root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
    const descriptors = materials.map(readMaterialLensAppearance);
    if (!descriptors.some(Boolean)) {
      requireCondition(object.userData.lensSurfaceProfile !== EFFECTIVE_OPTICAL_GROUP_PROFILE,
        'an effective optical group requires its canonical appearance descriptor.');
      legacyOptics ||= materials.some(isOpticalMaterial);
      translucentFrame ||= materials.some(material => material.transparent);
      return;
    }
    requireCondition(materials.length === 1, 'canonical optics must be a separate single-material mesh.');
    const transform = inverseRoot.clone().multiply(object.matrixWorld);
    requireCondition(transform.elements.every((value, index) => Math.abs(value - (index % 5 === 0 ? 1 : 0)) <= 1e-10),
      'bake mesh transforms into authored optical coordinates.');
    const effective = object.userData.lensSurfaceProfile === EFFECTIVE_OPTICAL_GROUP_PROFILE;
    if (!effective) validateCanonicalLensSurface(object);
    const descriptor = descriptors[0]!;
    createLensAppearanceUniforms(descriptor); // Capacity/precision validation before any mutation.
    pending.push({mesh: object, source: materials[0]!, descriptor, effective});
  });
  requireCondition(!pending.length || !legacyOptics, 'mixed canonical and legacy optical interfaces require explicit surface conversion '
    + '(a transmissive frame or temple part needs its partRole extra to be classified as frame).');
  requireCondition(!pending.length || !translucentFrame,
    'alpha-blended frame materials require ordered color and depth transport beyond the front-sheet profile (use physical transmission with a frame partRole).');
  const effective = pending.some(item => item.effective);
  requireCondition(!effective || pending.every(item => item.effective),
    'mixed front-sheet and effective-group profiles require an explicit common transport contract.');
  const effectiveGroups = effective ? validateEffectiveOpticalGroups(pending.map(item => item.mesh), root) : [];
  const opticalTriangles = new Set<string>();
  for (const {mesh} of effective ? [] : pending) {
    const positions = mesh.geometry.getAttribute('position'), indices = mesh.geometry.getIndex();
    const count = indices?.count ?? positions.count;
    for (let offset = 0; offset < count; offset += 3) {
      const vertices = [0, 1, 2].map(corner => {
        const index = indices?.getX(offset + corner) ?? offset + corner;
        return [positions.getX(index), positions.getY(index), positions.getZ(index)].join(',');
      }).sort().join('|');
      requireCondition(!opticalTriangles.has(vertices), 'duplicate coincident optical triangles have undefined interface multiplicity.');
      opticalTriangles.add(vertices);
    }
  }
  if (!effective) validateCanonicalOpticalTopology(pending.map(item => item.mesh));
  const groupByMesh = new Map(effectiveGroups.flatMap(group => group.meshes.map(mesh => [mesh, group.id] as const)));
  const replacements = new Map<Material | string, MeshPhysicalMaterial>();
  try {
    for (const item of pending) {
      const groupId = effective ? groupByMesh.get(item.mesh) : undefined;
      requireCondition(!effective || groupId !== undefined, 'validated group membership is incomplete.');
      const key = groupId ?? item.source;
      if (!replacements.has(key)) replacements.set(key, createCanonicalLensMaterial(item.source, item.descriptor, groupId));
    }
  } catch (error) {
    for (const material of replacements.values()) material.dispose();
    throw error;
  }
  for (const item of pending) item.mesh.material = replacements.get(effective ? groupByMesh.get(item.mesh)! : item.source)!;
  return {meshCount: pending.length, materials: [...replacements.values()],
    replacedMaterials: [...new Set(pending.map(item => item.source))], effectiveGroups,
    translucentFrameMaterials: classification.translucentFrameMaterials,
    profile: !pending.length ? 'none' : effective ? EFFECTIVE_OPTICAL_GROUP_PROFILE : CANONICAL_LENS_SURFACE_PROFILE};
}
