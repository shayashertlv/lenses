/** Authored part roles classify native assets independently of their shader (WP1_DESIGN.md §2, lens-identification.ts):
 * explicit roles first (`partRole`, legacy `part_role`), then lens names, and transmission only as a warned last resort
 * when nothing names a lens. These are hand-built scenes, so the roles come from userData (the renderer reads the glTF
 * JSON; tests/lens-identification.test.ts covers that path). */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, PerspectiveCamera, PlaneGeometry, Scene, ShaderLib, UniformsUtils} from 'three';
import type {Material, WebGLRenderer} from 'three';
import {classifyAssetMaterials, explicitRoleOf, isAuthoredFrontFrame, isFrameMaterial, isOpticalMaterial, isTranslucentFrameMaterial,
  materialClassOf} from '../src/eyewear/optical-material.ts';
import {buildTempleContinuityModel} from '../src/render/continuity.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {createTempleClip} from '../src/render/temple-clip.ts';
import {createHairOcclusion} from '../src/render/hair-occlusion.ts';
import {createTempleVisibility} from '../src/render/temple-visibility.ts';

type CompileInput = Parameters<Material['onBeforeCompile']>[0];
/** Run a material's compile hooks on the physical template, as the renderer would. */
function compile(material: Material) {
  const physical = ShaderLib.physical!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader'> = {
    uniforms: UniformsUtils.clone(physical.uniforms), vertexShader: physical.vertexShader, fragmentShader: physical.fragmentShader,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, {}]);
  return shader;
}

/** A native transmissive lens sheet, a crystal front behind it (z −16 mm … −4 mm) and two arms, with the exporter's roles. */
function crystalAsset({roles = true, crystalTemples = false, opaqueFront = false} = {}) {
  const lensMaterial = new MeshPhysicalMaterial({transmission: 1});
  const crystal = new MeshPhysicalMaterial({transmission: 1, ior: 1.49, thickness: .004, color: 0xf2ece0});
  const opaque = new MeshStandardMaterial();
  // A +Z front sheet at z = −10 mm.
  const lens = new Mesh(new PlaneGeometry(.05, .04).translate(0, 0, -.01), lensMaterial);
  const front = new Mesh(new BoxGeometry(.12, .04, .012).translate(0, 0, -.01), opaqueFront ? opaque : crystal);
  const left = new Mesh(new BoxGeometry(.004, .004, .15).translate(-.065, 0, -.09), crystalTemples ? crystal : opaque);
  const right = new Mesh(new BoxGeometry(.004, .004, .15).translate(.065, 0, -.09), crystalTemples ? crystal : opaque);
  if (roles) {
    lens.userData.partRole = 'lens';
    front.userData.partRole = 'frame'; left.userData.partRole = 'temple'; right.userData.partRole = 'temple';
  }
  const root = new Group().add(lens, front, left, right);
  const dispose = () => {
    for (const mesh of [lens, front, left, right]) mesh.geometry.dispose();
    for (const material of [lensMaterial, crystal, opaque]) material.dispose();
  };
  return {root, lensMaterial, crystal, opaque, lens, front, left, right, dispose};
}

test('roles identify crystal in native assets', t => {
  const f = crystalAsset(); t.after(f.dispose);
  const classification = classifyAssetMaterials(f.root);
  assert.deepEqual(classification.translucentFrameMaterials, [f.crystal]);
  assert.deepEqual(classification.sharedMaterials, []);
  assert.equal(isOpticalMaterial(f.crystal), false, 'transmission alone does not make an authored front optical');
  assert.equal(isFrameMaterial(f.crystal), true); assert.equal(isTranslucentFrameMaterial(f.crystal), true);
  assert.equal(isFrameMaterial(f.opaque), true); assert.equal(isTranslucentFrameMaterial(f.opaque), false);
  assert.equal(isOpticalMaterial(f.lensMaterial), true, 'the native lens stays optical');
  assert.equal(f.crystal.transmission, 1, 'classification never edits materials');
  assert.deepEqual(classification.warnings, [], 'agreeing roles need no warning');
  assert.equal(classification.lensIdentification.source, 'scene-userData');
  assert.deepEqual(classification.lensIdentification.components, {pass: true, reasons: []});
});

// Replaces 'untagged assets keep the transmission rule' and 'without roles a transmissive front keeps its conservative
// optical identity': without any role or lens name, transmission still makes both the lens and the crystal optical, but
// now only as the warned last resort (never silent), and a named lens bars it.
test('without roles or lens names transmission is the warned last resort; a named lens bars it', t => {
  const legacy = crystalAsset({roles: false}); t.after(legacy.dispose);
  const last = classifyAssetMaterials(legacy.root);
  assert.equal(isOpticalMaterial(legacy.crystal), true); assert.equal(isOpticalMaterial(legacy.lensMaterial), true);
  assert.deepEqual(last.translucentFrameMaterials, []);
  assert.equal(last.lensIdentification.last_resort_used, true);
  assert.equal(last.warnings.filter(warning => /identified as lens only by transmissionFactor=1 \(last resort/.test(warning)).length, 2,
    'each last-resort use is a warning');
  const named = crystalAsset({roles: false}); t.after(named.dispose);
  named.lens.name = 'Lens_L';
  const byName = classifyAssetMaterials(named.root);
  assert.equal(isOpticalMaterial(named.lensMaterial), true, 'the lens is a lens by its name token');
  assert.equal(isOpticalMaterial(named.crystal), false, 'a named lens bars the last resort: the crystal stays frame');
  assert.deepEqual(byName.translucentFrameMaterials, [named.crystal]);
  assert.deepEqual(byName.warnings, []); assert.equal(byName.lensIdentification.last_resort_used, false);
});

test('legacy part_role (ar_v4 r9): a crystal frame front and temples stay frame, part_role lens_* is lens', t => {
  const f = crystalAsset({roles: false, crystalTemples: true}); t.after(f.dispose);
  f.front.userData.part_role = 'frame'; f.left.userData.part_role = 'temple_left'; f.right.userData.part_role = 'temple_right';
  f.lens.userData.part_role = 'lens_left';
  const classification = classifyAssetMaterials(f.root);
  t.after(() => (f.left.material as Material).dispose());
  assert.equal(isOpticalMaterial(f.front.material as Material), false, 'the part_role frame crystal front is not optical');
  assert.equal(isTranslucentFrameMaterial(f.front.material as Material), true);
  assert.equal(isTranslucentFrameMaterial(f.left.material as Material), true);
  assert.equal(isOpticalMaterial(f.lensMaterial), true);
  assert.deepEqual(explicitRoleOf(f.front), {key: 'part_role', value: 'frame', site: 'mesh'});
  assert.equal(isAuthoredFrontFrame(f.front, f.root), true, 'a legacy frame role marks the compact front like partRole');
  assert.notEqual(f.front.material, f.left.material, 'the crystal shared by front and temples is split front/arm');
  assert.deepEqual(classification.warnings, []);
  assert.equal(classification.lensIdentification.materials.find(entry => entry.class === 'lens')!.signal, 'part_role_legacy');
});

test('a native material shared by lens and frame is separated without affecting the other lens', t => {
  const f = crystalAsset(); t.after(f.dispose);
  const shared = new MeshPhysicalMaterial({transmission: 1}); t.after(() => shared.dispose());
  const secondLens = new Mesh(new BoxGeometry(.01, .01, .001), shared); secondLens.userData.partRole = 'lens';
  const trim = new Mesh(new BoxGeometry(.01, .01, .001), shared); trim.userData.partRole = 'frame';
  f.root.add(secondLens, trim);
  t.after(() => {secondLens.geometry.dispose(); trim.geometry.dispose();});
  const classification = classifyAssetMaterials(f.root);
  assert.deepEqual(classification.sharedMaterials, [shared]);
  assert.equal(isOpticalMaterial(shared), true);
  assert.equal(secondLens.material, shared);
  assert.notEqual(trim.material, shared); t.after(() => (trim.material as Material).dispose());
  assert.equal(isOpticalMaterial(trim.material as Material), false);
  assert.equal(isTranslucentFrameMaterial(trim.material as Material), true);
  assert.equal(isOpticalMaterial(f.crystal), false, 'the properly owned front is still classified');
  // The §2 Components row: a material used by lens and frame primitives is not distinguished by its assignment.
  const report = classification.lensIdentification;
  assert.equal(report.components.pass, false); assert.equal(report.mixed_material_count, 1);
  assert.ok(classification.warnings.some(warning => /cloned per class so both render, and the §2 Components row fails/.test(warning)));
});

test('a role on the node group reaches every primitive mesh below it; a role on the model root does not', t => {
  const f = crystalAsset(); t.after(f.dispose);
  const node = new Group(); node.userData.partRole = 'frame';
  const bridge = new Mesh(new BoxGeometry(.02, .004, .004), new MeshPhysicalMaterial({transmission: 1}));
  node.add(bridge); f.root.add(node);
  t.after(() => {bridge.geometry.dispose(); (bridge.material as Material).dispose();});
  classifyAssetMaterials(f.root);
  assert.deepEqual(explicitRoleOf(bridge), {key: 'partRole', value: 'frame', site: 'node'});
  assert.equal(isOpticalMaterial(bridge.material as Material), false);
  // A model-wide role never supplies every part's identity (the old inherited() walked up to the root).
  const loose = crystalAsset({roles: false}); t.after(loose.dispose);
  loose.root.userData.partRole = 'lens'; loose.lens.name = 'Lens';
  classifyAssetMaterials(loose.root);
  assert.equal(explicitRoleOf(loose.front), null);
  assert.equal(materialClassOf(loose.crystal), 'frame', 'the root role does not make the crystal a lens');
});

test('a classified crystal front is front geometry for continuity, rear drop, clipping and hair occlusion', t => {
  const crystal = crystalAsset(), opaque = crystalAsset({opaqueFront: true});
  t.after(crystal.dispose); t.after(opaque.dispose);
  classifyAssetMaterials(crystal.root); classifyAssetMaterials(opaque.root);
  assert.deepEqual(buildTempleContinuityModel(crystal.root, -.14), buildTempleContinuityModel(opaque.root, -.14),
    'the crystal front counts exactly like an opaque front');
  const rear = createRearDrop(crystal.root, -.14); t.after(() => rear.dispose());
  assert.ok(Math.abs(rear.diagnostics.lensRearZM + .01) < 1e-8, 'the lens rear comes from the lens, not from the front box');
  const hook = crystal.crystal.onBeforeCompile;
  const clip = createTempleClip(crystal.root); t.after(() => clip.dispose());
  assert.notEqual(crystal.crystal.onBeforeCompile, hook, 'the front receives the arm clip wrapper like any frame material');
  const hairHook = crystal.crystal.onBeforeCompile;
  const hair = createHairOcclusion(crystal.root); t.after(() => hair.dispose());
  assert.notEqual(crystal.crystal.onBeforeCompile, hairHook, 'hair occludes the crystal front');
  const untagged = crystalAsset({roles: false}); t.after(untagged.dispose);
  classifyAssetMaterials(untagged.root);
  const untaggedRear = createRearDrop(untagged.root, -.14); t.after(() => untaggedRear.dispose());
  assert.ok(Math.abs(untaggedRear.diagnostics.lensRearZM + .016) < 1e-8,
    'without roles or names the transmissive front box is optical by the (warned) last resort');
});

test('crystal temples classify as frame too, so continuity and rear drop accept them (the producer contract still keeps temples opaque)', t => {
  const f = crystalAsset({crystalTemples: true}), reference = crystalAsset(); t.after(f.dispose); t.after(reference.dispose);
  classifyAssetMaterials(f.root); classifyAssetMaterials(reference.root);
  assert.deepEqual(buildTempleContinuityModel(f.root, -.14), buildTempleContinuityModel(reference.root, -.14));
  const rear = createRearDrop(f.root, -.14); t.after(() => rear.dispose());
  assert.ok(rear.diagnostics.affectedVertexCount > 0);
});

test('crystal temples are drawn like arms: clipped, hair-occluded and lifted by a transmissive near-arm overlay', t => {
  const f = crystalAsset({crystalTemples: true}); t.after(f.dispose);
  classifyAssetMaterials(f.root);
  const armMaterial = f.left.material as MeshPhysicalMaterial;
  t.after(() => armMaterial.dispose());
  assert.notEqual(armMaterial, f.front.material, 'front and arms have separate visibility behavior');
  assert.equal(armMaterial, f.right.material);
  assert.equal(isTranslucentFrameMaterial(armMaterial), true);
  const bare = armMaterial.onBeforeCompile;
  const clip = createTempleClip(f.root); t.after(() => clip.dispose());
  assert.notEqual(armMaterial.onBeforeCompile, bare, 'the arm clip wraps the crystal arm material');
  const clipped = armMaterial.onBeforeCompile;
  // A minimal backend: construction touches no GL, the fitted head depth is rendered only by prepare().
  const renderer = {capabilities: {samples: 4}} as unknown as WebGLRenderer;
  const scene = new Scene(), eyewearPose = new Group().add(f.root), camera = new PerspectiveCamera(60, 16 / 9, 1, 1000);
  scene.add(eyewearPose);
  const visibility = createTempleVisibility(f.root, {renderer, scene, camera, eyewearPose}); t.after(() => visibility.dispose());
  const overlays = f.root.children.filter((child): child is Mesh => child instanceof Mesh && child.userData.templeVisibilityOverlay === true);
  const armOverlays = overlays.filter(overlay => overlay.geometry === f.left.geometry || overlay.geometry === f.right.geometry);
  assert.equal(armOverlays.length, 2, 'each crystal arm gets its own near-arm overlay');
  for (const overlay of armOverlays) {
    const clone = overlay.material as Material;
    assert.ok(clone instanceof MeshPhysicalMaterial, 'the overlay draws a physical clone, not the hidden placeholder');
    assert.notEqual(clone, armMaterial); assert.equal(clone.visible, true);
    assert.equal(clone.transmission, armMaterial.transmission, 'the overlay keeps the crystal transmissive');
    assert.equal(clone.transparent, false); assert.equal(clone.depthWrite, false); assert.equal(clone.depthTest, true);
    assert.equal(isOpticalMaterial(clone), false, 'the clone is registered as frame, so it is never optical');
    assert.equal(isTranslucentFrameMaterial(clone), true);
    assert.equal(isFrameMaterial(clone), true);
    const shader = compile(clone);
    assert.ok(shader.uniforms.templeVisibilityHeadDepth, 'the clone carries the depth relief of an opaque arm overlay');
    assert.ok(shader.fragmentShader.includes('gl_FragDepth = min(gl_FragCoord.z, templeLifted);'));
    assert.ok(shader.uniforms.templeClipEnabled, 'the clone inherits the arm clip through the original hook');
  }
  assert.equal(armOverlays[0]!.material, armOverlays[1]!.material, 'one clone per crystal material');
  assert.equal(compile(armMaterial).uniforms.templeCheekCount !== undefined, true, 'the original arm material gets the cheek/lens-input wrapper');
  assert.equal(compile(f.front.material).uniforms.templeCheekCount, undefined, 'the crystal front keeps its inner rim');
  const hair = createHairOcclusion(f.root); t.after(() => hair.dispose());
  assert.notEqual(armMaterial.onBeforeCompile, clipped, 'hair occludes the crystal arm');
  assert.ok(compile(armOverlays[0]!.material as Material).uniforms.hairOcclusionEnabled, 'hair occludes the overlay clone too');
  assert.equal(isOpticalMaterial(f.lensMaterial), true, 'the lens stays optical');
  assert.equal(compile(f.lensMaterial).uniforms.templeClipEnabled, undefined, 'the lens is never wrapped');
  // Continuity and the rear drop still accept the asset with its overlays in place.
  const reference = crystalAsset(); t.after(reference.dispose); classifyAssetMaterials(reference.root);
  assert.deepEqual(buildTempleContinuityModel(f.root, -.14), buildTempleContinuityModel(reference.root, -.14));
  const rear = createRearDrop(f.root, -.14); t.after(() => rear.dispose());
  assert.ok(rear.diagnostics.affectedVertexCount > 0);
  assert.ok(Math.abs(rear.diagnostics.lensRearZM + .01) < 1e-8, 'the lens rear still comes from the lens');
  visibility.dispose();
  assert.equal(f.root.children.filter(child => child.userData.templeVisibilityOverlay === true).length, 0);
});
