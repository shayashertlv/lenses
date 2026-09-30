/** Authored part roles classify native and canonical assets independently of their shader; untagged catalog
 * materials retain the legacy transmission rule. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, PerspectiveCamera, PlaneGeometry, Scene, ShaderLib, UniformsUtils} from 'three';
import type {Material, WebGLRenderer} from 'three';
import {classifyAssetMaterials, isFrameMaterial, isOpticalMaterial, isTranslucentFrameMaterial, partRoleOf} from '../src/eyewear/optical-material.ts';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {buildTempleContinuityModel} from '../src/render/continuity.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {createTempleClip} from '../src/render/temple-clip.ts';
import {createHairOcclusion} from '../src/render/hair-occlusion.ts';
import {createTempleVisibility} from '../src/render/temple-visibility.ts';
import {installCanonicalLensMaterials} from '../src/render/lens-material.ts';

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

function canonical<T extends Material>(material: T): T {
  material.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: {
    schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: 0,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null,
  }}};
  return material;
}

/** A canonical lens sheet, a crystal front behind it (z −16 mm … −4 mm) and two arms, with the exporter's roles. */
function crystalAsset({roles = true, descriptor = true, crystalTemples = false, opaqueFront = false} = {}) {
  const lensMaterial = descriptor ? canonical(new MeshPhysicalMaterial({transmission: 0})) : new MeshPhysicalMaterial({transmission: 1});
  const crystal = new MeshPhysicalMaterial({transmission: 1, ior: 1.49, thickness: .004, color: 0xf2ece0});
  const opaque = new MeshStandardMaterial();
  // A valid +Z front sheet (authored normals and UV height) at z = −10 mm, so the canonical adapter can install it.
  const lens = new Mesh(new PlaneGeometry(.05, .04).translate(0, 0, -.01), lensMaterial);
  const front = new Mesh(new BoxGeometry(.12, .04, .012).translate(0, 0, -.01), opaqueFront ? opaque : crystal);
  const left = new Mesh(new BoxGeometry(.004, .004, .15).translate(-.065, 0, -.09), crystalTemples ? crystal : opaque);
  const right = new Mesh(new BoxGeometry(.004, .004, .15).translate(.065, 0, -.09), crystalTemples ? crystal : opaque);
  if (roles) {
    lens.userData.partRole = 'lens'; lens.userData.lensSurfaceProfile = 'front_sheet_v1';
    front.userData.partRole = 'frame'; left.userData.partRole = 'temple'; right.userData.partRole = 'temple';
  }
  const root = new Group().add(lens, front, left, right);
  const dispose = () => {
    for (const mesh of [lens, front, left, right]) mesh.geometry.dispose();
    for (const material of [lensMaterial, crystal, opaque]) material.dispose();
  };
  return {root, lensMaterial, crystal, opaque, lens, front, left, right, dispose};
}

test('roles identify crystal in canonical and native assets; untagged assets keep the transmission rule', t => {
  const f = crystalAsset(); t.after(f.dispose);
  const classification = classifyAssetMaterials(f.root);
  assert.equal(classification.canonical, true);
  assert.deepEqual(classification.translucentFrameMaterials, [f.crystal]);
  assert.deepEqual(classification.sharedMaterials, []);
  assert.equal(isOpticalMaterial(f.crystal), false, 'transmission alone does not make an authored front optical');
  assert.equal(isFrameMaterial(f.crystal), true); assert.equal(isTranslucentFrameMaterial(f.crystal), true);
  assert.equal(isFrameMaterial(f.opaque), true); assert.equal(isTranslucentFrameMaterial(f.opaque), false);
  assert.equal(isOpticalMaterial(f.lensMaterial), true, 'the canonical lens stays optical');
  assert.equal(f.crystal.transmission, 1, 'classification never edits materials');
  const native = crystalAsset({descriptor: false}); t.after(native.dispose);
  const nativeClassification = classifyAssetMaterials(native.root);
  assert.equal(nativeClassification.canonical, false);
  assert.deepEqual(nativeClassification.translucentFrameMaterials, [native.crystal]);
  assert.equal(isOpticalMaterial(native.crystal), false);
  assert.equal(isFrameMaterial(native.crystal), true);
  const legacy = crystalAsset({descriptor: false, roles: false}); t.after(legacy.dispose);
  const legacyClassification = classifyAssetMaterials(legacy.root);
  assert.equal(legacyClassification.canonical, false);
  assert.deepEqual(legacyClassification.translucentFrameMaterials, []);
  assert.equal(isOpticalMaterial(legacy.crystal), true, 'without roles the shipped rule is untouched');
  assert.equal(isFrameMaterial(legacy.crystal), false);
});

test('without roles a transmissive front keeps its conservative optical identity', t => {
  const f = crystalAsset({roles: false}); t.after(f.dispose);
  const classification = classifyAssetMaterials(f.root);
  assert.equal(classification.canonical, true);
  assert.deepEqual(classification.translucentFrameMaterials, []);
  assert.equal(isOpticalMaterial(f.crystal), true);
});

test('a native material shared by lens and frame is separated without affecting canonical optics', t => {
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
});

test('a role on the node group reaches every primitive mesh below it', t => {
  const f = crystalAsset(); t.after(f.dispose);
  const node = new Group(); node.userData.partRole = 'frame';
  const bridge = new Mesh(new BoxGeometry(.02, .004, .004), new MeshPhysicalMaterial({transmission: 1}));
  node.add(bridge); f.root.add(node);
  t.after(() => {bridge.geometry.dispose(); (bridge.material as Material).dispose();});
  assert.equal(partRoleOf(bridge), 'frame');
  classifyAssetMaterials(f.root);
  assert.equal(isOpticalMaterial(bridge.material as Material), false);
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
  assert.ok(Math.abs(untaggedRear.diagnostics.lensRearZM + .016) < 1e-8, 'without roles the transmissive front box is optical (the old rule)');
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
  assert.equal(isTranslucentFrameMaterial(f.crystal), true);
  const bare = f.crystal.onBeforeCompile;
  const clip = createTempleClip(f.root); t.after(() => clip.dispose());
  assert.notEqual(f.crystal.onBeforeCompile, bare, 'the arm clip wraps the crystal arm material');
  const clipped = f.crystal.onBeforeCompile;
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
    assert.notEqual(clone, f.crystal); assert.equal(clone.visible, true);
    assert.equal(clone.transmission, f.crystal.transmission, 'the overlay keeps the crystal transmissive');
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
  assert.equal(compile(f.crystal).uniforms.templeCheekCount !== undefined, true, 'the original arm material gets the cheek/lens-input wrapper');
  const hair = createHairOcclusion(f.root); t.after(() => hair.dispose());
  assert.notEqual(f.crystal.onBeforeCompile, clipped, 'hair occludes the crystal arm');
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

test('the canonical adapter installs beside a role-tagged translucent front and refuses an untagged one', t => {
  const f = crystalAsset(); t.after(f.dispose);
  const installation = installCanonicalLensMaterials(f.root);
  assert.equal(installation.meshCount, 1);
  assert.deepEqual(installation.translucentFrameMaterials, [f.crystal]);
  assert.equal(isOpticalMaterial(f.crystal), false);
  const untagged = crystalAsset({roles: false}); t.after(untagged.dispose);
  untagged.lens.userData.partRole = 'lens'; untagged.lens.userData.lensSurfaceProfile = 'front_sheet_v1';
  assert.throws(() => installCanonicalLensMaterials(untagged.root), /mixed canonical and legacy/);
});
