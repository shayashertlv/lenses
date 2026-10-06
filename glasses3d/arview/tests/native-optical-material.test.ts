/** The native (Studio) path through lens identification (WP1_DESIGN.md §2): explicit roles decide, materials keep every
 *  PBR property, shared materials are split per class and per front/arm owner, and the class is decided once. These are
 *  hand-built scenes (roles from userData); tests/lens-identification.test.ts covers the glTF JSON path. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {BoxGeometry, BufferAttribute, BufferGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Texture} from 'three';
import type {Material} from 'three';
import {classifyAssetMaterials, isAuthoredFrontFrame, isFrameMaterial, isOpticalMaterial, isTranslucentFrameMaterial,
  RETIRED_LENS_APPEARANCE_EXTENSION, stripRetiredLensAppearance} from '../src/eyewear/optical-material.ts';
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

test('native closed curved lenses keep their geometry and complete PBR material through classification', t => {
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
  const classified = classifyAssetMaterials(root);
  assert.deepEqual(classified.translucentFrameMaterials, [crystal]);
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
  classifyAssetMaterials(root);
  assert.equal(lens.material, mirror);
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

// Rewritten for the decided class: the untagged mesh beside role-tagged parts is frame by default (no live transmission
// rule), and the lens keeps the original. The old assertion 'untagged owners retain the numeric transmission fallback' is
// removed: that fallback no longer exists. The untagged part is a compact box at the centre, so it is a positional front
// (isPositionalFrontFrame, WP1 review E/F) and shares the front instance; an untagged arm shares the arm instance.
test('shared native materials are isolated by explicit roles; an untagged part beside them is frame', t => {
  for (const transmission of [0, .85]) {
    const texture = new Texture(); t.after(() => texture.dispose());
    const source = new MeshPhysicalMaterial({color: 0x95622f, transmission, thickness: .004, ior: 1.49,
      roughness: .123, metalness: .2, clearcoat: .7, clearcoatRoughness: .09, attenuationColor: 0xf2d6bc,
      attenuationDistance: .01, map: texture});
    source.userData = {native: {untouched: true}};
    const frame = mesh('frame', source), temple = mesh('temple', source), lens = mesh('lens', source);
    const unknown = mesh(undefined, source), root = new Group().add(frame, temple, lens, unknown);
    const untaggedArm = mesh(undefined, source, new BoxGeometry(.004, .005, .15).translate(-.06, 0, -.075)); root.add(untaggedArm);
    t.after(() => dispose(root));
    // Also exercise shared Material[] storage: updating one mesh's slot must not relabel the untagged mesh.
    const slots = [source]; frame.material = slots; unknown.material = slots;
    const classification = classifyAssetMaterials(root);
    const frameMaterial = (frame.material as Material[])[0]!;
    assert.deepEqual(classification.sharedMaterials, [source]);
    assert.equal(lens.material, source, 'the lens keeps the original');
    assert.equal(slots[0], source, 'the shared Material[] storage itself is never edited');
    assert.notEqual(temple.material, frameMaterial, 'front and arm shaders require independent structural materials');
    assert.notEqual(temple.material, source); assert.notEqual(frameMaterial, source);
    assert.equal((unknown.material as Material[])[0], frameMaterial, 'the untagged compact central part is front: it shares the front instance');
    assert.equal(untaggedArm.material, temple.material, 'the untagged arm shares the arm instance');
    assert.equal(isFrameMaterial(frameMaterial), true); assert.equal(isOpticalMaterial(frameMaterial), false);
    assert.equal(isFrameMaterial(temple.material as Material), true); assert.equal(isOpticalMaterial(temple.material as Material), false);
    assert.equal(isOpticalMaterial(source), true);
    assert.equal(classification.lensIdentification.components.pass, false, 'one material on lens and frame fails §2 Components');
    for (const material of [frameMaterial, temple.material, lens.material] as MeshPhysicalMaterial[]) {
      for (const property of ['transmission', 'thickness', 'ior', 'roughness', 'metalness', 'clearcoat',
        'clearcoatRoughness', 'attenuationDistance'] as const) assert.equal(material[property], source[property]);
      assert.ok(material.color.equals(source.color)); assert.ok(material.attenuationColor.equals(source.attenuationColor));
      assert.equal(material.map, texture); assert.deepEqual(material.userData, source.userData);
    }
    const assigned = [frameMaterial, temple.material, lens.material, unknown.material, untaggedArm.material];
    classifyAssetMaterials(root);
    assert.deepEqual([(frame.material as Material[])[0], temple.material, lens.material, unknown.material, untaggedArm.material], assigned,
      'reclassification does not create new material instances');
  }
});

test('compact authored fronts are isolated from a shared frame-tagged long arm without changing their identity', t => {
  const source = new MeshPhysicalMaterial({transmission: .9, thickness: .004, roughness: .18});
  const front = mesh('frame', source), arm = mesh('frame', source, new BoxGeometry(.004, .005, .15).translate(.06, 0, -.075));
  const root = new Group().add(front, arm); t.after(() => dispose(root));
  assert.equal(isAuthoredFrontFrame(front, root), false, 'an unclassified mesh is never an authored front');
  assert.deepEqual(classifyAssetMaterials(root).sharedMaterials, [source]);
  assert.equal(isAuthoredFrontFrame(front, root), true);
  assert.equal(isAuthoredFrontFrame(arm, root), false);
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
  classifyAssetMaterials(root);
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
  // The role is the decided one: a changed role counts only after (re)classification.
  front.userData.partRole = 'temple'; assert.equal(isAuthoredFrontFrame(front, root), true);
  classifyAssetMaterials(root); assert.equal(isAuthoredFrontFrame(front, root), false);
  front.userData.part_role = 'frame'; delete front.userData.partRole; classifyAssetMaterials(root);
  assert.equal(isAuthoredFrontFrame(front, root), true, 'a legacy part_role frame qualifies too');
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
  // Any explicit role other than lens is an explicit non-lens (it stops the name and transmission rules).
  inherited.userData.partRole = 'unrecognized'; classifyAssetMaterials(root);
  assert.equal(isFrameMaterial(material), true); assert.equal(isOpticalMaterial(material), false);
  inherited.userData.partRole = 'lens'; classifyAssetMaterials(root);
  assert.equal(isOpticalMaterial(material), true, 'the class follows the roles at each classification, never transmission');
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

test('a retired canonical descriptor is stripped and reported once per material; the material then classifies by its role', t => {
  // The descriptor (only the retired bsa/reconstruction routes wrote it) has no runtime in arview. Even a malformed one
  // never throws: it is removed, the caller reports it, and the native glTF material is what draws.
  const lensMaterial = new MeshPhysicalMaterial({name: 'Retired canonical lens', transmission: 1});
  lensMaterial.userData.gltfExtensions = {[RETIRED_LENS_APPEARANCE_EXTENSION]: {schema_version: 99},
    KHR_example_unknown: {kept: true}};
  const frameMaterial = new MeshPhysicalMaterial({transmission: 0});
  frameMaterial.userData.gltfExtensions = {[RETIRED_LENS_APPEARANCE_EXTENSION]: {schema_version: 1}};
  const plain = new MeshStandardMaterial({name: 'Plain acetate'});
  const lens = mesh('lens', lensMaterial), second = mesh('lens', lensMaterial), frame = mesh('frame', frameMaterial), arm = mesh('temple', plain);
  const root = new Group().add(lens, second, frame, arm); t.after(() => dispose(root));
  assert.deepEqual(stripRetiredLensAppearance(root), ['Retired canonical lens', '(unnamed material)'], 'one report per material, not per mesh');
  assert.deepEqual(lensMaterial.userData.gltfExtensions, {KHR_example_unknown: {kept: true}}, 'other extensions are untouched');
  assert.equal(frameMaterial.userData.gltfExtensions, undefined, 'an emptied extension record is removed');
  assert.equal(lensMaterial.transmission, 1, 'the native material is not edited');
  assert.deepEqual(stripRetiredLensAppearance(root), [], 'idempotent');
  classifyAssetMaterials(root);
  assert.equal(isOpticalMaterial(lensMaterial), true);
  assert.equal(isFrameMaterial(frameMaterial), true); assert.equal(isOpticalMaterial(frameMaterial), false,
    'a frame part that carried the descriptor is frame by its role');
  assert.equal(isOpticalMaterial(plain), false);
});
