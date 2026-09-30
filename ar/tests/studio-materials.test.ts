import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, ShaderLib, Texture, UniformsUtils} from 'three';
import type {Material} from 'three';
import type {GLTF} from 'three/addons/loaders/GLTFLoader.js';
import {applyStudioMaterials, studioMaterials, tagStudioMaterials} from '../src/studio/materials.ts';
import {authoredAttenuationDistance, volumeAttenuationRgb} from '../src/render/eyewear-volume.ts';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';
import {createCameraTransmissionTwin, createCameraTransmissionUniforms} from '../src/render/translucent-twin.ts';

function fixture() {
  const root = new Group(), material = new MeshPhysicalMaterial({transmission: .8, thickness: .001}), opaque = new MeshStandardMaterial();
  const glass = new Mesh(new BoxGeometry(.1, .02, .005), material); glass.userData.partRole = 'frame';
  root.add(glass, new Mesh(glass.geometry, opaque));
  const gltf = {scene: root, parser: {json: {materials: [{extensions: {KHR_materials_transmission: {}, KHR_materials_volume: {},
    KHR_materials_ior: {}, KHR_materials_iridescence: {}, KHR_materials_clearcoat: {}}}, {}]},
    associations: new Map([[material, {materials: 0}], [opaque, {materials: 1}]])}} as unknown as GLTF;
  tagStudioMaterials(gltf);
  return {root, material, opaque, gltf, glass};
}
test('glTF material indices survive clones and only actual supported controls are advertised', () => {
  const f = fixture(), clone = f.material.clone(); f.root.add(new Mesh(f.glass.geometry, clone));
  const catalog = studioMaterials(f.root);
  assert.equal(catalog.length, 2); assert.equal(catalog[0]!.id, '0');
  assert.deepEqual(catalog[1]!.editable_keys, ['base_color', 'metallic', 'roughness']);
  applyStudioMaterials(f.root, {'0': {base_color: '#808080', roughness: .31, clearcoat: .7, iridescence_thickness: 50}});
  for (const material of [f.material, clone]) {
    assert.ok(Math.abs(material.color.r - .2158605) < 1e-6); assert.equal(material.roughness, .31);
    assert.equal(material.clearcoat, .7); assert.deepEqual(material.iridescenceThicknessRange, [50, 50]);
  }
  assert.notEqual(f.opaque.roughness, .31);
});
test('unsupported edit fails atomically; zero-transmission and thickness-map classes are not offered', () => {
  const f = fixture(), original = f.material.roughness;
  assert.throws(() => applyStudioMaterials(f.root, {'0': {roughness: .2}, '1': {transmission: .4}}), /does not support/);
  assert.equal(f.material.roughness, original);
  assert.throws(() => applyStudioMaterials(f.root, {'99': {roughness: .2}}), /absent/);
  f.material.transmission = 0; f.material.iridescenceThicknessMap = new Texture(); tagStudioMaterials(f.gltf);
  const keys = studioMaterials(f.root)[0]!.editable_keys;
  assert.ok(!keys.includes('transmission')); assert.ok(!keys.includes('iridescence_thickness'));
});
test('canonical assets refuse native edits for all materials', () => {
  const f = fixture(); f.gltf.parser.json.extensionsUsed = ['LENSES_lens_appearance']; tagStudioMaterials(f.gltf);
  assert.ok(studioMaterials(f.root).every(m => m.editable_keys.length === 0));
  assert.throws(() => applyStudioMaterials(f.root, {'1': {base_color: '#ffffff'}}));
});
test('AR absorption edits retain metres for shadows and twins, including originally infinite distance', () => {
  const f = fixture();
  applyStudioMaterials(f.root, {'0': {attenuation_distance: .002, attenuation_color: '#808080'}}, 100);
  assert.equal(f.material.attenuationDistance, .2); assert.equal(authoredAttenuationDistance(f.material), .002);
  assert.ok(Math.abs(volumeAttenuationRgb(f.material)[0] - Math.sqrt(.2158605)) < 1e-6);
  applyStudioMaterials(f.root, {'0': {attenuation_distance: 0}}, 100);
  assert.equal(f.material.attenuationDistance, Infinity); assert.deepEqual(volumeAttenuationRgb(f.material), [1, 1, 1]);
});
test('replaying unchanged edits does not recompile and resetting rounded color restores exact original linear color', () => {
  const f = fixture(); f.material.color.setRGB(.6, .455, .305); delete f.material.userData.lensesStudioBaseline; tagStudioMaterials(f.gltf);
  const original = f.material.color.toArray(), hex = '#' + f.material.color.getHexString();
  applyStudioMaterials(f.root, {'0': {base_color: '#123456', roughness: .17}});
  const version = f.material.version;
  assert.deepEqual(applyStudioMaterials(f.root, {'0': {base_color: '#123456', roughness: .17}}), []);
  assert.equal(f.material.version, version);
  applyStudioMaterials(f.root, {'0': {base_color: hex}});
  assert.deepEqual(f.material.color.toArray(), original);
});
test('disposed crystal twins are rebuilt with edited base color and optical uniforms', () => {
  const f = fixture(); classifyAssetMaterials(f.root);
  const uniforms = createCameraTransmissionUniforms(), before = createCameraTransmissionTwin(f.material, uniforms);
  before.dispose();
  applyStudioMaterials(f.root, {'0': {base_color: '#ff0000', transmission: .4, attenuation_distance: .002}}, 100);
  const after = createCameraTransmissionTwin(f.material, uniforms);
  assert.notEqual(after, before); assert.equal(after.color.getHexString(), 'ff0000'); assert.equal(after.transmission, 0);
  const physical = ShaderLib.physical!, shader = {uniforms: UniformsUtils.clone(physical.uniforms), vertexShader: physical.vertexShader, fragmentShader: physical.fragmentShader};
  Reflect.apply(after.onBeforeCompile as Material['onBeforeCompile'], after, [shader, {}]);
  assert.equal(shader.uniforms.twinTransmission!.value, .4);
  after.dispose();
});
