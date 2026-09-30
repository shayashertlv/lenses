/** How the crystal twin and the canonical lens compose the camera seen through them with their own reflection (v2 twin,
 *  v4 / v3 lens programs, 2026-09-30). Tom Ford FT1123-D review: v1 mixed the full camera into the crystal and Three
 *  added the room reflection on top, untone-mapped, so grazing rim walls read milky and the room's front panel clipped
 *  12 % of the crystal (and 10-13 % of the lens) to flat white. The actual driver compilation and pixels are the
 *  runtime harness's (ar/qa/provider-comparison.mjs --stage=ar); these checks pin the composition in the shader text. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, Group, Material, Mesh, MeshPhysicalMaterial, PlaneGeometry, ShaderChunk, ShaderLib, UniformsUtils} from 'three';
import type {WebGLRenderer} from 'three';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';
import {REFLECTION_LIMIT_GLSL} from '../src/render/eyewear-reflection.ts';
import {createCanonicalLensMaterial} from '../src/render/lens-material.ts';
import {
  CAMERA_TRANSMISSION_TWIN_KEY, createCameraTransmissionTwin, createCameraTransmissionUniforms, TWIN_CLEARCOAT_LAYER,
} from '../src/render/translucent-twin.ts';

type CompileInput = Parameters<Material['onBeforeCompile']>[0];
function compile(material: Material) {
  const physical = ShaderLib.physical!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader'> = {
    uniforms: UniformsUtils.clone(physical.uniforms), vertexShader: physical.vertexShader, fragmentShader: physical.fragmentShader,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, {} as WebGLRenderer]);
  return shader;
}

const appearance = {schema_version: 1, color_space: 'scene_linear_srgb_D65',
  density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
  normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: .065,
  optical_density_keyframes: [{v: 0, optical_density_rgb: [.3, .4, .5]}], angular_reflectance_keyframes: null};

/** A crystal front (frame role, the exporter's clearcoat) beside a canonical lens, classified as the renderer does. */
function crystal(t: {after(fn: () => void): void}, parameters: ConstructorParameters<typeof MeshPhysicalMaterial>[0] = {}) {
  const lens = new MeshPhysicalMaterial({transmission: 0});
  lens.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance}};
  const material = new MeshPhysicalMaterial({transmission: 1, ior: 1.49, roughness: .085, thickness: .0048, clearcoat: 1, clearcoatRoughness: .03,
    ...parameters});
  material.toneMapped = false;
  const lensMesh = new Mesh(new PlaneGeometry(.05, .04), lens), front = new Mesh(new BoxGeometry(.13, .04, .005), material);
  lensMesh.userData = {partRole: 'lens', lensSurfaceProfile: 'front_sheet_v1'}; front.userData.partRole = 'frame';
  classifyAssetMaterials(new Group().add(lensMesh, front));
  const twin = createCameraTransmissionTwin(material, createCameraTransmissionUniforms());
  t.after(() => {twin.dispose(); material.dispose(); lens.dispose(); lensMesh.geometry.dispose(); front.geometry.dispose();});
  return {material, twin};
}

test('the look-through loses what the surface reflects, the way Three\'s own refraction path does', t => {
  const {twin} = crystal(t);
  const fragment = compile(twin).fragmentShader;
  assert.ok(fragment.includes('vec3 twinReflectance = EnvironmentBRDF( geometryNormal, geometryViewDir, material.specularColorBlended, '
    + 'material.specularF90, material.roughness );'), 'the split-sum Fresnel of the reflection Three draws: it rises toward the silhouette');
  // Three's transmission path takes the same F from the same material terms and returns (1 - F) * attenuatedColor.
  assert.ok(ShaderChunk.transmission_fragment.includes('material.specularColorBlended, material.specularF90'));
  assert.ok(ShaderChunk.transmission_pars_fragment.includes('vec3 F = EnvironmentBRDF( n, v, specularColor, specularF90, roughness );'));
  assert.ok(ShaderChunk.transmission_pars_fragment.includes('return vec4( ( 1.0 - F ) * attenuatedColor'));
  assert.ok(fragment.includes('twinThrough = twinTransmission * ( 1.0 - twinReflectance ) * twinCameraRGB * material.diffuseContribution * twinAttenuation;'));
  assert.ok(fragment.includes('totalDiffuse *= 1.0 - twinTransmission;'), 'the body\'s own lit diffuse keeps the rest, as mix() gave it');
  assert.ok(!fragment.includes('totalDiffuse = mix( totalDiffuse, twinCameraRGB'), 'v1 added the full look-through under the reflection');
  const through = fragment.indexOf('twinThrough = twinTransmission'), outgoing = fragment.indexOf('vec3 outgoingLight = totalDiffuse + totalSpecular');
  assert.ok(through > 0 && outgoing > through, 'outgoingLight is the surface\'s own light only: the look-through is kept apart');
  assert.equal(CAMERA_TRANSMISSION_TWIN_KEY, 'camera-transmission-twin-v3');
  assert.ok(twin.customProgramCacheKey().endsWith(`|${CAMERA_TRANSMISSION_TWIN_KEY}`), 'an older twin program is never reused');
});

test('the coat takes its Fresnel share of the look-through too, and the reflection alone goes through the limit', t => {
  const {twin} = crystal(t);
  const fragment = compile(twin).fragmentShader;
  assert.ok(ShaderLib.physical!.fragmentShader.includes(TWIN_CLEARCOAT_LAYER), 'three 0.185.1\'s coat composition, pinned');
  const fcc = fragment.indexOf('vec3 Fcc = F_Schlick( material.clearcoatF0, material.clearcoatF90, dotNVcc );');
  const coatThrough = fragment.indexOf('twinThrough *= 1.0 - material.clearcoat * Fcc;'), layer = fragment.indexOf(TWIN_CLEARCOAT_LAYER);
  assert.ok(fcc > 0 && coatThrough > fcc && layer > coatThrough, 'the same (1 - clearcoat * Fcc) the coat applies to the base layer');
  // The crystal's own knee (half the headroom); the lens knee is absolute (eyewear-reflection.ts, review AR-R2).
  const limit = fragment.indexOf('outgoingLight = twinThrough + eyewearReflectionLimit( outgoingLight, twinThrough, 0.5000, 0.0000 );');
  const opaque = fragment.indexOf('#include <opaque_fragment>');
  assert.ok(limit > layer && opaque > limit, 'after the coat (its highlight is reflection too), before the output');
  assert.ok(fragment.indexOf(REFLECTION_LIMIT_GLSL) >= 0 && fragment.indexOf(REFLECTION_LIMIT_GLSL) < fragment.indexOf('void main()'),
    'the limit is declared before main');
  assert.equal(fragment.split('eyewearReflectionLimit(').length - 1, 2, 'declared once, called once');
});

test('the twin keeps the GLB\'s clearcoat and its roughness; no coat is invented', t => {
  const {material, twin} = crystal(t);
  assert.equal(twin.clearcoat, 1); assert.equal(twin.clearcoatRoughness, .03);
  assert.equal(material.clearcoat, 1, 'the source keeps its coat (Three\'s own path for a legacy lens)');
  const plain = crystal(t, {clearcoat: 0});
  assert.equal(plain.twin.clearcoat, 0, 'no coat is invented: the exporter decides');
});

test('a Three upgrade that moves a marker the twin needs fails at compile instead of drawing the v1 composition', t => {
  const {material, twin} = crystal(t);
  for (const marker of ['#include <transmission_fragment>', TWIN_CLEARCOAT_LAYER, '#include <opaque_fragment>']) {
    const hook = material.onBeforeCompile;
    material.onBeforeCompile = shader => {shader.fragmentShader = shader.fragmentShader.replace(marker, '');};
    try {assert.throws(() => compile(twin), /pinned Three shader marker changed/, marker);}
    finally {material.onBeforeCompile = hook;}
  }
});

test('the canonical lens adds its reflection through the limit against what it transmits', () => {
  for (const effective of [false, true]) {
    const material = createCanonicalLensMaterial(new MeshPhysicalMaterial(), appearance as never, effective ? 'group-a' : undefined);
    const shader = {vertexShader: ShaderLib.physical!.vertexShader, fragmentShader: ShaderLib.physical!.fragmentShader, uniforms: {}};
    material.onBeforeCompile(shader as CompileInput, {} as WebGLRenderer);
    const fragment = shader.fragmentShader;
    assert.ok(fragment.indexOf(REFLECTION_LIMIT_GLSL) >= 0 && fragment.indexOf(REFLECTION_LIMIT_GLSL) < fragment.indexOf('void main()'));
    assert.ok(fragment.includes('vec3 canonicalThrough = canonicalResponse.transmission * canonicalBackground;'));
    assert.ok(fragment.includes('gl_FragColor = vec4(canonicalThrough + eyewearReflectionLimit( canonicalResponse.reflectance * canonicalEnvironment, canonicalThrough, 0.0000, 0.8000 ),'),
      'the lens\'s absolute knee (exact up to 0.8 linear in total, review AR-R2)');
    assert.ok(!fragment.includes('+ canonicalResponse.reflectance * canonicalEnvironment, uCanonicalLayerMode'), 'the unlimited sum is gone');
    assert.equal(material.customProgramCacheKey(), effective ? 'canonical-effective-group-transport-v4' : 'canonical-lens-layer-transport-v5');
    material.dispose();
  }
});
