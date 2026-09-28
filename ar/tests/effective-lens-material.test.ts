import assert from 'node:assert/strict';
import {test} from 'node:test';
import {BoxGeometry, DoubleSide, Group, Mesh, MeshPhysicalMaterial, ShaderLib} from 'three';
import type {WebGLRenderer} from 'three';
import {LENS_APPEARANCE_EXTENSION, readMaterialLensAppearance} from '../src/eyewear/lens-appearance.ts';
import type {LensAppearanceDescriptor} from '../src/eyewear/lens-appearance.ts';
import {EFFECTIVE_OPTICAL_GROUP_PROFILE} from '../src/eyewear/optical-material.ts';
import {canonicalLensRenderState, createCanonicalLensMaterial, installCanonicalLensMaterials} from '../src/render/lens-material.ts';

const appearance: LensAppearanceDescriptor = {
  schema_version: 1, color_space: 'scene_linear_srgb_D65', density_interpolation: 'piecewise_smoothstep_optical_density',
  vertical_coordinate: 'lens_local_bottom_0_top_1', normal_reflectance_rgb: [.7, .5, .2],
  refractive_index: 1.5, roughness: .05,
  optical_density_keyframes: [{v: 0, optical_density_rgb: [.1, .2, .3]}, {v: 1, optical_density_rgb: [.6, .9, 1.2]}],
  angular_reflectance_keyframes: null,
};

function sourceMaterial(): MeshPhysicalMaterial {
  const material = new MeshPhysicalMaterial({transmission: 0, transparent: true, opacity: .6});
  material.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {
    schema_version: 1, texcoord: 0, appearance: JSON.parse(JSON.stringify(appearance)),
  }};
  return material;
}

function member(group: string, index: number, source: MeshPhysicalMaterial, x = 0): Mesh {
  const geometry = new BoxGeometry(.04, .04, .003).translate(x, 0, 0);
  geometry.clearGroups();
  const position = geometry.getAttribute('position'), uv = geometry.getAttribute('uv');
  geometry.computeBoundingBox();
  const bottom = geometry.boundingBox!.min.y, span = geometry.boundingBox!.max.y - bottom;
  for (let i = 0; i < position.count; i++) uv.setXY(i, .5, (position.getY(i)-bottom)/span);
  const mesh = new Mesh(geometry, source);
  mesh.userData = {
    partRole: 'lens', lensSurfaceProfile: EFFECTIVE_OPTICAL_GROUP_PROFILE,
    lensUVConvention: 'lens_local_bottom_0_top_1', opticalGroupId: group,
    opticalGroupMemberId: `member-${index}`, opticalSourcePartIndex: index,
    opticalSourceSha256: 'a'.repeat(64), lensAppearanceSha256: 'b'.repeat(64),
    semanticIdentity: 'unverified', materialIdentification: 'unmeasured',
  };
  return mesh;
}

test('separate physical groups sharing source material get separate mutable transport state', t => {
  const source = sourceMaterial(), left = member('left', 0, source, -.04), right = member('right', 1, source, .04);
  const root = new Group().add(left, right);
  root.position.set(1, 2, -3); root.rotation.set(.1, .2, .3);
  const installed = installCanonicalLensMaterials(root);
  t.after(() => {left.geometry.dispose(); right.geometry.dispose(); source.dispose(); installed.materials.forEach(m => m.dispose());});
  assert.equal(installed.profile, EFFECTIVE_OPTICAL_GROUP_PROFILE);
  assert.equal(installed.effectiveGroups.length, 2);
  assert.equal(installed.materials.length, 2);
  assert.notEqual(left.material, right.material);
  assert.deepEqual(installed.replacedMaterials, [source]);
  const a = left.material as MeshPhysicalMaterial, b = right.material as MeshPhysicalMaterial;
  assert.equal(a.side, DoubleSide); assert.equal(a.userData.canonicalOpticalGroupId, 'left');
  assert.equal(b.userData.canonicalOpticalGroupId, 'right');
  assert.notEqual(canonicalLensRenderState(a), canonicalLensRenderState(b));
  canonicalLensRenderState(a).uCanonicalLayerMode.value = 3;
  assert.equal(canonicalLensRenderState(b).uCanonicalLayerMode.value, 0);
  assert.equal(source.opacity, .6);
});

test('multipart one-group geometry shares one group state across different source materials', t => {
  const firstSource = sourceMaterial(), secondSource = sourceMaterial();
  const first = member('one-lens', 0, firstSource, -.025), second = member('one-lens', 1, secondSource, .025);
  const installed = installCanonicalLensMaterials(new Group().add(first, second));
  t.after(() => {first.geometry.dispose(); second.geometry.dispose(); firstSource.dispose(); secondSource.dispose();
    installed.materials.forEach(material => material.dispose());});
  assert.equal(installed.effectiveGroups.length, 1);
  assert.equal(installed.effectiveGroups[0]!.meshes.length, 2);
  assert.equal(installed.materials.length, 1);
  assert.equal(first.material, second.material);
  assert.deepEqual(new Set(installed.replacedMaterials), new Set([firstSource, secondSource]));
});

test('invalid later group prevents all material mutation', t => {
  const firstSource = sourceMaterial(), secondSource = sourceMaterial();
  const first = member('left', 0, firstSource, -.04), second = member('right', 1, secondSource, .04);
  t.after(() => {first.geometry.dispose(); second.geometry.dispose(); firstSource.dispose(); secondSource.dispose();});
  second.geometry.getAttribute('normal').setXYZ(0, 0, 0, 0);
  assert.throws(() => installCanonicalLensMaterials(new Group().add(first, second)), /normal/i);
  assert.equal(first.material, firstSource); assert.equal(second.material, secondSource);
  second.geometry.getAttribute('normal').setXYZ(0, 1, 0, 0);
  delete second.userData.opticalGroupId;
  assert.throws(() => installCanonicalLensMaterials(new Group().add(first, second)), /group/i);
  assert.equal(first.material, firstSource);
});

test('effective material compiles capture, nearest-event and symmetric response paths through actual Three markers', t => {
  const source = sourceMaterial(), material = createCanonicalLensMaterial(source, appearance, 'test-group');
  t.after(() => {source.dispose(); material.dispose();});
  const shader = {vertexShader: ShaderLib.physical.vertexShader, fragmentShader: ShaderLib.physical.fragmentShader, uniforms: {}};
  material.onBeforeCompile(shader as Parameters<MeshPhysicalMaterial['onBeforeCompile']>[0], {} as WebGLRenderer);
  assert.ok(shader.fragmentShader.includes('lensIncidenceAngleDegrees(dot(canonicalNormal, canonicalView))'));
  assert.ok(shader.vertexShader.includes('modelViewMatrix * vec4(0.0, 0.0, 1.0, 0.0)'));
  assert.ok(shader.fragmentShader.includes('dot(normalize(vCanonicalFrontAxis), canonicalView) < 0.0'));
  assert.ok(shader.fragmentShader.includes('canonicalAngle, canonicalRearSide)'));
  assert.ok(shader.fragmentShader.includes('gl_FragCoord.z != nearestGroupDepth'));
  assert.ok(shader.fragmentShader.indexOf('uCanonicalLayerMode > 2.5') >= 0);
  assert.ok(shader.fragmentShader.indexOf('uCanonicalLayerMode > 2.5') < shader.fragmentShader.indexOf('vec2 canonicalPixel'));
  const state = canonicalLensRenderState(material);
  assert.equal(state.uCanonicalGroupEnabled.value, true);
  assert.equal(state.uCanonicalGroupNearest.value, null);
  assert.deepEqual(readMaterialLensAppearance(material), appearance);
});
