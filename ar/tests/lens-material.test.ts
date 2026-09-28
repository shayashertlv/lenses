import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, DoubleSide, FrontSide, Group, Mesh, MeshPhysicalMaterial, PlaneGeometry, ShaderLib} from 'three';
import type {WebGLRenderer} from 'three';
import {LENS_APPEARANCE_EXTENSION, readMaterialLensAppearance} from '../src/eyewear/lens-appearance.ts';
import type {LensAppearanceDescriptor} from '../src/eyewear/lens-appearance.ts';
import {createCanonicalLensMaterial, installCanonicalLensMaterials, validateCanonicalLensSurface} from '../src/render/lens-material.ts';

function descriptor(): LensAppearanceDescriptor {
  return {schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: .05,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}, {v: 1, optical_density_rgb: [2, 1, .5]}],
    angular_reflectance_keyframes: null};
}

function sheet(x = 0, material = new MeshPhysicalMaterial({transmission: 0, metalness: 1, side: DoubleSide})) {
  material.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: descriptor()}};
  const mesh = new Mesh(new PlaneGeometry(.05, .04).translate(x, 0, 0), material);
  mesh.userData = {partRole: 'lens', lensSurfaceProfile: 'front_sheet_v1'};
  return mesh;
}

test('canonical total mirrors install without mutating source optics or legacy frame materials', () => {
  const legacy = new MeshPhysicalMaterial({transmission: 0}), source = new MeshPhysicalMaterial({transmission: 0, color: 0x000000,
    metalness: .9, opacity: .12, transparent: true, side: DoubleSide, thickness: .02, ior: 2.2});
  const left = sheet(-.03, source), right = sheet(.03, source), frame = new Mesh(new PlaneGeometry(.02, .004), legacy);
  const root = new Group().add(left, right, frame);
  const installation = installCanonicalLensMaterials(root);
  assert.equal(installation.meshCount, 2); assert.equal(installation.materials.length, 1);
  assert.deepEqual(installation.replacedMaterials, [source]);
  assert.equal(left.material, right.material); assert.notEqual(left.material, source); assert.equal(frame.material, legacy);
  assert.equal(source.transmission, 0); assert.equal(source.opacity, .12); assert.equal(source.side, DoubleSide);
  assert.equal(left.material.side, FrontSide); assert.equal(left.material.forceSinglePass, true);
  assert.equal(left.material.transmission, 1); assert.equal(left.material.opacity, 1); assert.equal(left.material.thickness, 0);
  assert.deepEqual(readMaterialLensAppearance(left.material), descriptor());
  source.userData.gltfExtensions[LENS_APPEARANCE_EXTENSION].appearance.roughness = .7;
  assert.equal(readMaterialLensAppearance(left.material)!.roughness, .05);
});

test('a malformed later lens leaves the entire asset unchanged', () => {
  const good = sheet(-.03), bad = sheet(.03), goodSource = good.material, badSource = bad.material;
  bad.geometry.deleteAttribute('uv');
  assert.throws(() => installCanonicalLensMaterials(new Group().add(good, bad)), /TEXCOORD_0/);
  assert.equal(good.material, goodSource); assert.equal(bad.material, badSource);
});

test('runtime rejects volume/backward sheets, zero normals and reversed intrinsic gradients', () => {
  for (const mutate of [
    (mesh: Mesh) => {mesh.geometry = new BoxGeometry(.05, .04, .002);},
    (mesh: Mesh) => {mesh.geometry.getAttribute('normal').setXYZ(0, 0, 0, 0);},
    (mesh: Mesh) => {const uv = mesh.geometry.getAttribute('uv'); for (let i = 0; i < uv.count; i++) uv.setY(i, 1 - uv.getY(i));},
    (mesh: Mesh) => {mesh.geometry.getAttribute('uv').setY(0, 1.01);},
    (mesh: Mesh) => {delete mesh.userData.lensSurfaceProfile;},
    (mesh: Mesh) => {mesh.userData.partRole = 'frame';},
  ]) {
    const mesh = sheet(); mutate(mesh); assert.throws(() => validateCanonicalLensSurface(mesh), /Canonical lens/);
  }
  const backwards = sheet(), index = backwards.geometry.getIndex()!;
  const first = index.getX(0); index.setX(0, index.getX(1)); index.setX(1, first);
  assert.throws(() => validateCanonicalLensSurface(backwards), /winding/);
});

test('coincident interfaces, mixed material groups and unbaked coordinates cannot enter production silently', () => {
  const layered = new Group().add(sheet(), sheet());
  assert.throws(() => installCanonicalLensMaterials(layered), /coincident/);
  const mixed = sheet(); mixed.material = [mixed.material, new MeshPhysicalMaterial()] as unknown as MeshPhysicalMaterial;
  assert.throws(() => installCanonicalLensMaterials(new Group().add(mixed)), /single-material/);
  const transformed = sheet(); transformed.position.x = .01;
  assert.throws(() => installCanonicalLensMaterials(new Group().add(transformed)), /bake mesh transforms/);
});

test('distinct overlapping sheets retain independent response while mixed legacy interfaces are rejected', () => {
  const front = sheet(), rear = sheet(); rear.geometry.translate(0, 0, -.002);
  assert.equal(installCanonicalLensMaterials(new Group().add(front, rear)).meshCount, 2);
  const legacy = new Mesh(new PlaneGeometry(.05, .04), new MeshPhysicalMaterial({transmission: .8}));
  assert.throws(() => installCanonicalLensMaterials(new Group().add(sheet(), legacy)), /mixed canonical and legacy/);
  // The same transmissive mesh with the exporter's frame role is a translucent front, not legacy optics.
  const crystal = new Mesh(new PlaneGeometry(.05, .04), new MeshPhysicalMaterial({transmission: .8}));
  crystal.userData.partRole = 'frame';
  const withCrystal = installCanonicalLensMaterials(new Group().add(sheet(), crystal));
  assert.equal(withCrystal.meshCount, 1); assert.deepEqual(withCrystal.translucentFrameMaterials, [crystal.material]);
  const translucent = new Mesh(new PlaneGeometry(.05, .04), new MeshPhysicalMaterial({transparent: true, opacity: .5}));
  const canonical = sheet(), source = canonical.material;
  assert.throws(() => installCanonicalLensMaterials(new Group().add(canonical, translucent)), /alpha-blended frame materials/);
  assert.equal(canonical.material, source, 'unsupported frame transport fails before replacing optics');
  const duplicated = sheet(), indices = duplicated.geometry.getIndex()!;
  duplicated.geometry.setIndex([...indices.array, indices.getX(0), indices.getX(1), indices.getX(2)]);
  assert.throws(() => installCanonicalLensMaterials(new Group().add(duplicated)), /duplicate coincident/);
});

test('canonical material binds a detached descriptor to both shader and shadow consumers', () => {
  const mutable = JSON.parse(JSON.stringify(descriptor())), source = new MeshPhysicalMaterial();
  const material = createCanonicalLensMaterial(source, mutable);
  mutable.roughness = .9; mutable.normal_reflectance_rgb[0] = 0;
  const shader = {vertexShader: ShaderLib.physical.vertexShader, fragmentShader: ShaderLib.physical.fragmentShader, uniforms: {}};
  material.onBeforeCompile(shader as Parameters<MeshPhysicalMaterial['onBeforeCompile']>[0], {} as WebGLRenderer);
  const uniforms = shader.uniforms as Record<string, {value: unknown}>;
  assert.equal(uniforms.uCanonicalReflectionRoughness!.value, .05);
  assert.deepEqual(Array.from(uniforms.uLensNormalReflectance!.value as Float32Array), [1, 1, 1]);
  assert.equal(readMaterialLensAppearance(material)!.roughness, .05);
  // The actual driver compilation and numeric output are tested by the runtime
  // harness. This check ensures a Three update cannot silently bypass the hook.
  assert.throws(() => material.onBeforeCompile({vertexShader: '', fragmentShader: '', uniforms: {}} as
    Parameters<MeshPhysicalMaterial['onBeforeCompile']>[0], {} as WebGLRenderer), /shader marker changed/);
});
