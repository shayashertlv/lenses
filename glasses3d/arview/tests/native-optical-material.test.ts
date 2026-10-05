import assert from 'node:assert/strict';
import {test} from 'node:test';
import {BoxGeometry, BufferAttribute, BufferGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Texture} from 'three';
import type {Material} from 'three';
import {classifyAssetMaterials, isAuthoredFrontFrame, isFrameMaterial, isOpticalMaterial, isTranslucentFrameMaterial} from '../src/eyewear/optical-material.ts';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {installCanonicalLensMaterials} from '../src/render/lens-material.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {createTempleClip} from '../src/render/temple-clip.ts';

function mesh(role: string | undefined, material: Material, geometry: BufferGeometry = new BoxGeometry(.04, .03, .002)): Mesh {
  const result = new Mesh(geometry, material);
  if (role !== undefined) result.userData.partRole = role;
  return result;
}

function dispose(root: Group, originals: Material[] = []): void {
  const materials = new Set(originals);
  root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    object.geometry.dispose();
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) materials.add(material);
  });
  for (const material of materials) material.dispose();
}

test('native closed curved lenses keep their geometry and complete PBR material without canonical descriptors', t => {
  const lensMaterial = new MeshPhysicalMaterial({color: 0x856039, transmission: .92, thickness: .0018,
    ior: 1.51, roughness: .07, clearcoat: .2, attenuationColor: 0xa57443, attenuationDistance: .004});
  const lens = mesh('lens', lensMaterial, new BoxGeometry(.05, .04, .0018, 8, 6, 1));
  const positions = lens.geometry.getAttribute('position');
  for (let i = 0; i < positions.count; i++) positions.setZ(i, positions.getZ(i)
    + .003 * (1 - (positions.getX(i) / .025) ** 2 - (positions.getY(i) / .02) ** 2));
  lens.geometry.computeVertexNormals();
  const before = {geometry: lens.geometry, position: positions.array.slice(), index: lens.geometry.index!.array.slice(),
    normals: lens.geometry.getAttribute('normal').array.slice(), material: lensMaterial.toJSON()};
  const crystal = new MeshPhysicalMaterial({transmission: 1, ior: 1.49, thickness: .004});
  const front = mesh('frame', crystal), root = new Group().add(lens, front); t.after(() => dispose(root));
  const installed = installCanonicalLensMaterials(root);
  assert.equal(installed.profile, 'none'); assert.equal(installed.meshCount, 0);
  assert.deepEqual(installed.translucentFrameMaterials, [crystal]);
  assert.equal(lens.material, lensMaterial); assert.deepEqual(lensMaterial.toJSON(), before.material);
  assert.equal(lens.geometry, before.geometry);
  assert.deepEqual(positions.array, before.position);
  assert.deepEqual(lens.geometry.index!.array, before.index);
  assert.deepEqual(lens.geometry.getAttribute('normal').array, before.normals);
  assert.ok(Math.max(...positions.array.filter((_, i) => i % 3 === 2))
    - Math.min(...positions.array.filter((_, i) => i % 3 === 2)) > .006);
  assert.equal(isOpticalMaterial(lensMaterial), true); assert.equal(isOpticalMaterial(crystal), false);
});

test('explicit native opaque mirror lenses are protected from arm deformation and clip shader edits', t => {
  const mirror = new MeshPhysicalMaterial({metalness: 1, transmission: 0, roughness: .02});
  const lens = mesh('lens', mirror, new BoxGeometry(.05, .04, .002).translate(0, 0, -.005));
  const crystal = new MeshPhysicalMaterial({transmission: 1, thickness: .004});
  const left = mesh('temple', crystal, new BoxGeometry(.004, .004, .15).translate(-.065, 0, -.09));
  const right = mesh('temple', crystal, new BoxGeometry(.004, .004, .15).translate(.065, 0, -.09));
  const root = new Group().add(lens, left, right); t.after(() => dispose(root));
  const installed = installCanonicalLensMaterials(root);
  assert.equal(installed.profile, 'none'); assert.equal(lens.material, mirror);
  assert.equal(isOpticalMaterial(mirror), true); assert.equal(isTranslucentFrameMaterial(crystal), true);
  const before = lens.geometry.getAttribute('position').array.slice(), hook = mirror.onBeforeCompile;
  const rear = createRearDrop(root, -.14), clip = createTempleClip(root);
  t.after(() => {clip.dispose(); rear.dispose();});
  rear.setShape(.01, .015);
  assert.deepEqual(lens.geometry.getAttribute('position').array, before);
  assert.equal(mirror.onBeforeCompile, hook);
  assert.ok(Math.abs(rear.diagnostics.lensRearZM + .006) < 1e-8);
  assert.ok(rear.diagnostics.affectedVertexCount > 0);
});

test('shared native materials are isolated by explicit roles and keep untagged legacy behavior', t => {
  for (const transmission of [0, .85]) {
    const texture = new Texture(); t.after(() => texture.dispose());
    const source = new MeshPhysicalMaterial({color: 0x95622f, transmission, thickness: .004, ior: 1.49,
      roughness: .123, metalness: .2, clearcoat: .7, clearcoatRoughness: .09, attenuationColor: 0xf2d6bc,
      attenuationDistance: .01, map: texture});
    source.userData = {native: {untouched: true}};
    const frame = mesh('frame', source), temple = mesh('temple', source), lens = mesh('lens', source);
    const unknown = mesh(undefined, source), root = new Group().add(frame, temple, lens, unknown);
    t.after(() => dispose(root));
    // Also exercise shared Material[] storage: updating one mesh's slot must not relabel the untagged mesh.
    const slots = [source]; frame.material = slots; unknown.material = slots;
    const classification = classifyAssetMaterials(root);
    const frameMaterial = (frame.material as Material[])[0]!;
    assert.deepEqual(classification.sharedMaterials, [source]);
    assert.equal((unknown.material as Material[])[0], source);
    assert.notEqual(temple.material, frameMaterial, 'front and arm shaders require independent structural materials');
    assert.notEqual(temple.material, source);
    assert.notEqual(frameMaterial, source); assert.notEqual(lens.material, source);
    assert.notEqual(lens.material, frameMaterial);
    assert.equal(isFrameMaterial(frameMaterial), true); assert.equal(isOpticalMaterial(frameMaterial), false);
    assert.equal(isFrameMaterial(temple.material as Material), true); assert.equal(isOpticalMaterial(temple.material as Material), false);
    assert.equal(isOpticalMaterial(lens.material as Material), true);
    assert.equal(isOpticalMaterial(source), transmission > 0, 'untagged owners retain the numeric transmission fallback');
    for (const material of [frameMaterial, temple.material, lens.material] as MeshPhysicalMaterial[]) {
      for (const property of ['transmission', 'thickness', 'ior', 'roughness', 'metalness', 'clearcoat',
        'clearcoatRoughness', 'attenuationDistance'] as const) assert.equal(material[property], source[property]);
      assert.ok(material.color.equals(source.color)); assert.ok(material.attenuationColor.equals(source.attenuationColor));
      assert.equal(material.map, texture); assert.deepEqual(material.userData, source.userData);
    }
    const assigned = [frameMaterial, temple.material, lens.material, unknown.material];
    classifyAssetMaterials(root);
    assert.deepEqual([(frame.material as Material[])[0], temple.material, lens.material, unknown.material], assigned,
      'reclassification does not create new material instances');
  }
});

test('compact authored fronts are isolated from a shared frame-tagged long arm without changing legacy identity', t => {
  const source = new MeshPhysicalMaterial({transmission: .9, thickness: .004, roughness: .18});
  const front = mesh('frame', source), arm = mesh('frame', source, new BoxGeometry(.004, .005, .15).translate(.06, 0, -.075));
  const root = new Group().add(front, arm); t.after(() => dispose(root));
  assert.equal(isAuthoredFrontFrame(front, root), true);
  assert.equal(isAuthoredFrontFrame(arm, root), false);
  assert.deepEqual(classifyAssetMaterials(root).sharedMaterials, [source]);
  assert.equal(front.material, source); assert.notEqual(arm.material, source);
  for (const material of [front.material, arm.material] as MeshPhysicalMaterial[]) {
    assert.equal(isTranslucentFrameMaterial(material), true); assert.equal(isOpticalMaterial(material), false);
    assert.equal(material.transmission, .9); assert.equal(material.thickness, .004);
  }
  const assigned = arm.material;
  classifyAssetMaterials(root); assert.equal(arm.material, assigned, 'classification remains idempotent');
});

test('front exemption requires finite baked geometry inside the asset and ignores only external pose transforms', t => {
  const material = new MeshStandardMaterial(), geometry = new BufferGeometry();
  const position = new BufferAttribute(new Float32Array([.05, -.02, -.006, .06, -.02, -.006, .06, -.015, -.005]), 3);
  geometry.setAttribute('position', position);
  const front = mesh(undefined, material, geometry), parent = new Group().add(front), root = new Group().add(parent);
  parent.userData.partRole = 'frame'; t.after(() => dispose(root));
  const pose = new Group().add(root); pose.scale.setScalar(100); pose.position.z = -50;
  assert.equal(isAuthoredFrontFrame(front, root), true, 'a primitive inherits its authored node role');
  for (const axis of ['x', 'y', 'z'] as const) {
    const previous = position.getComponent(0, {x: 0, y: 1, z: 2}[axis]);
    position.setComponent(0, {x: 0, y: 1, z: 2}[axis], NaN);
    assert.equal(isAuthoredFrontFrame(front, root), false, `invalid ${axis} cannot qualify`);
    position.setComponent(0, {x: 0, y: 1, z: 2}[axis], previous);
  }
  position.setZ(0, -.031); assert.equal(isAuthoredFrontFrame(front, root), false); position.setZ(0, -.006);
  parent.position.z = -.08; assert.equal(isAuthoredFrontFrame(front, root), false); parent.position.z = 0;
  front.scale.z = 2; assert.equal(isAuthoredFrontFrame(front, root), false); front.scale.z = 1;
  front.matrixAutoUpdate = false; front.matrix.makeTranslation(0, 0, -.08);
  assert.equal(isAuthoredFrontFrame(front, root), false); front.matrix.identity();
  assert.equal(isAuthoredFrontFrame(front, root), true);
  front.userData.partRole = 'temple'; assert.equal(isAuthoredFrontFrame(front, root), false);
  front.userData.partRole = 'frame'; geometry.deleteAttribute('position');
  assert.equal(isAuthoredFrontFrame(front, root), false);
  geometry.setAttribute('position', new BufferAttribute(new Float32Array(), 3));
  assert.equal(isAuthoredFrontFrame(front, root), false);
});

test('roles inherit from a glTF primitive parent, explicit child overrides win, and role changes clear old identity', t => {
  const material = new MeshPhysicalMaterial({transmission: 0}), inherited = mesh(undefined, material);
  const parent = new Group().add(inherited); parent.userData.partRole = 'lens';
  const root = new Group().add(parent); t.after(() => dispose(root));
  classifyAssetMaterials(root); assert.equal(isOpticalMaterial(material), true);
  inherited.userData.partRole = 'frame'; classifyAssetMaterials(root);
  assert.equal(isFrameMaterial(material), true); assert.equal(isOpticalMaterial(material), false);
  inherited.userData.partRole = 'unrecognized'; classifyAssetMaterials(root);
  assert.equal(isFrameMaterial(material), false); assert.equal(isOpticalMaterial(material), false);
});

test('a lens mesh does not relabel an opaque child mesh as optical', t => {
  const lensMaterial = new MeshPhysicalMaterial({transmission: 1}), childMaterial = new MeshStandardMaterial();
  const lens = mesh('lens', lensMaterial), child = mesh(undefined, childMaterial);
  lens.add(child);
  const root = new Group().add(lens); t.after(() => dispose(root));
  classifyAssetMaterials(root);
  assert.equal(isOpticalMaterial(lensMaterial), true);
  assert.equal(isOpticalMaterial(childMaterial), false);
  child.userData.partRole = 'lens'; classifyAssetMaterials(root);
  assert.equal(isOpticalMaterial(childMaterial), true, 'an explicit child role still applies');
});

test('canonical descriptors remain authoritative and malformed descriptors fail before shared native assignments change', t => {
  const shared = new MeshStandardMaterial(), frame = mesh('frame', shared), lens = mesh('lens', shared);
  const described = new MeshPhysicalMaterial({transmission: 0});
  described.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: {
    schema_version: 1, color_space: 'scene_linear_srgb_D65', density_interpolation: 'piecewise_smoothstep_optical_density',
    vertical_coordinate: 'lens_local_bottom_0_top_1', normal_reflectance_rgb: [.04, .04, .04], refractive_index: 1.5,
    roughness: .05, optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null,
  }}};
  const conflict = mesh('frame', described), root = new Group().add(frame, lens, conflict); t.after(() => dispose(root));
  const extension = described.userData.gltfExtensions[LENS_APPEARANCE_EXTENSION];
  extension.schema_version = 99;
  assert.throws(() => classifyAssetMaterials(root), /schema_version/);
  assert.equal(frame.material, shared); assert.equal(lens.material, shared);
  extension.schema_version = 1;
  const classification = classifyAssetMaterials(root);
  assert.equal(classification.canonical, true); assert.equal(conflict.material, described);
  assert.equal(isFrameMaterial(described), false); assert.equal(isOpticalMaterial(described), true);
  assert.ok(classification.sharedMaterials.includes(described));
  assert.throws(() => installCanonicalLensMaterials(root), /partRole must explicitly be lens/);
  // Existing shadow assets can store canonical lenses and opaque frame material groups in one mesh. Their lens
  // descriptor identifies the optical group; a whole-mesh lens tag must not convert its frame material too.
  const ordinary = new MeshStandardMaterial(); t.after(() => ordinary.dispose());
  const mixed = mesh('lens', described); mixed.material = [ordinary, described];
  const mixedRoot = new Group().add(mixed); t.after(() => mixed.geometry.dispose());
  classifyAssetMaterials(mixedRoot);
  assert.equal(isOpticalMaterial(ordinary), false); assert.equal(isOpticalMaterial(described), true);
});
