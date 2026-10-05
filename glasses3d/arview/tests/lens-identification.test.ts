/** Lens identification (WP1_DESIGN.md §2): the shared vectors with the Python side (g3d_host/lens_id.py), the glTF JSON
 *  reader (never three's merged userData), the per-material report block, and the real files. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {readFile} from 'node:fs/promises';
import {Mesh, MeshPhysicalMaterial, Texture} from 'three';
import type {Material, Object3D} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import type {GLTF} from 'three/addons/loaders/GLTFLoader.js';
import {classifyUses, decideName, enumerateGltfUses, excludedName, identifyGltfJson, nameTokens} from '../src/eyewear/lens-identification.ts';
import type {UseSignals} from '../src/eyewear/lens-identification.ts';
import {classifyAssetMaterials, isFrameMaterial, isOpticalMaterial, isTranslucentFrameMaterial, materialClassOf} from '../src/eyewear/optical-material.ts';
import {publicFile} from './shipped-models.ts';

interface FixturePrim {
  key: string; node_name?: string; node_extras?: unknown; mesh_extras?: unknown; material_name?: string;
  material_extras?: unknown; material?: unknown;
}
const fixtures = JSON.parse(await readFile(new URL('../../tests/fixtures/lens_id_cases.json', import.meta.url), 'utf8')) as
  {cases: {name: string; prims: FixturePrim[]; expect: Record<string, [boolean, number]>}[]};
const signals = (prim: FixturePrim): UseSignals => ({key: prim.key, nodeName: prim.node_name ?? null, nodeExtras: prim.node_extras,
  meshExtras: prim.mesh_extras, materialName: prim.material_name ?? null, materialExtras: prim.material_extras, material: prim.material ?? null});

test('every shared vector gives the same [is_lens, rule] as the Python side', () => {
  assert.ok(fixtures.cases.length >= 10, 'the shared vectors are present');
  for (const testCase of fixtures.cases) {
    const {decisions} = classifyUses(testCase.prims.map(signals));
    const got = Object.fromEntries([...decisions].map(([key, decision]) => [key, [decision.isLens, decision.rule]]));
    assert.deepEqual(got, testCase.expect, testCase.name);
  }
});

test('tokens split camelCase, digits and separators; glasses is not glass; exclusions are token stems', () => {
  assert.deepEqual(nameTokens('LensRight.001'), ['lens', 'right']);
  assert.deepEqual(nameTokens('Lens | light brown'), ['lens', 'light', 'brown']);
  assert.deepEqual(nameTokens('AmberGradientLenses'), ['amber', 'gradient', 'lenses']);
  assert.deepEqual(nameTokens('TOMFord2Lens'), ['tom', 'ford', 'lens']);
  assert.deepEqual(nameTokens('LENSRight'), ['lens', 'right'], 'ACRONYMCase splits as on the Python side');
  assert.deepEqual(nameTokens(null), []); assert.deepEqual(nameTokens(''), []);
  assert.equal(decideName('Sunglasses body', 'Eyeglasses'), null);
  assert.equal(decideName('Tinted glass', null)?.value, 'glass');
  assert.equal(excludedName('Subtle warm lens inscription'), true);
  assert.equal(excludedName('Lens | subtle etched lettering.001'), true);
  assert.equal(excludedName('Printed'), true); assert.equal(excludedName('Sprint'), false, 'a stem, not a substring');
  assert.equal(decideName('Lens print', 'Lens'), null, 'either name excludes');
});

test('the last resort and conflicting roles are warned; the signal names the deciding source', () => {
  let result = classifyUses([{key: 't', nodeName: 'O', materialName: 'M',
    material: {extensions: {KHR_materials_transmission: {transmissionFactor: 1}}}}]);
  assert.ok(result.warnings.some(warning => warning.includes('last resort')));
  result = classifyUses([{key: 'm', nodeName: 'S', nodeExtras: {partRole: 'frame'}, materialName: 'S', materialExtras: {partRole: 'lens'}}]);
  assert.ok(result.warnings.some(warning => warning.includes('disagrees')));
  assert.equal(result.decisions.get('m')!.site, 'material');
  result = classifyUses([{key: 'a', nodeName: 'Lens_L', meshExtras: {partRole: 'lens'}, materialName: 'x'}]);
  assert.equal(result.decisions.get('a')!.signal, 'mesh.extras.partRole=lens');
  assert.equal(result.decisions.get('a')!.kind, 'partRole');
  result = classifyUses([{key: 'r', nodeExtras: {part_role: 'lens_left'}}, {key: 'f', nodeExtras: {part_role: 'hardware'}}]);
  assert.equal(result.decisions.get('r')!.kind, 'part_role_legacy'); assert.equal(result.decisions.get('r')!.value, 'lens_left');
  assert.equal(result.decisions.get('f')!.isLens, false); assert.equal(result.warnings.length, 0, 'agreeing roles are silent');
});

/* ------------------------------------------------------------------------------------------- glTF JSON and GLBs */

/** A minimal GLB: every mesh primitive is the same triangle (accessor 0), so the JSON alone shapes the test. */
function glb(json: Record<string, unknown>): ArrayBuffer {
  const positions = new Float32Array([0, 0, 0, .01, 0, 0, 0, .01, 0]);
  const doc = {asset: {version: '2.0'}, buffers: [{byteLength: positions.byteLength}],
    bufferViews: [{buffer: 0, byteOffset: 0, byteLength: positions.byteLength}],
    accessors: [{bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [.01, .01, 0]}], ...json};
  let text = JSON.stringify(doc); while (text.length % 4) text += ' ';
  const jsonBytes = new TextEncoder().encode(text), bin = new Uint8Array(positions.buffer);
  const out = new Uint8Array(12 + 8 + jsonBytes.length + 8 + bin.length), view = new DataView(out.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, out.length, true);
  view.setUint32(12, jsonBytes.length, true); view.setUint32(16, 0x4e4f534a, true); out.set(jsonBytes, 20);
  view.setUint32(20 + jsonBytes.length, bin.length, true); view.setUint32(24 + jsonBytes.length, 0x004e4942, true);
  out.set(bin, 28 + jsonBytes.length);
  return out.buffer;
}
const prim = (material?: number, mode?: number) => ({attributes: {POSITION: 0}, ...(material === undefined ? {} : {material}),
  ...(mode === undefined ? {} : {mode})});
async function parse(bytes: ArrayBuffer): Promise<GLTF> {
  return new GLTFLoader().register(() => ({name: 'LensIdTestTextures', loadTexture: async () => new Texture()})).parseAsync(bytes, '');
}
function dispose(roots: Object3D[]): void {
  const resources = new Set<{dispose(): void}>();
  for (const root of roots) root.traverse(object => {
    if (!(object instanceof Mesh)) return; resources.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) resources.add(material as Material);
  });
  for (const resource of resources) resource.dispose();
}
const meshNamed = (root: Object3D, name: string): Mesh => {
  let found: Mesh | null = null;
  root.traverse(object => {if (!found && object instanceof Mesh && object.name === name) found = object;});
  assert.ok(found, name); return found;
};
const crystalT = {extensions: {KHR_materials_transmission: {transmissionFactor: 1}}};

test('uses are every node x primitive of the default scene, all modes, depth first; no scenes means root nodes', () => {
  const json = {scene: 1, scenes: [{nodes: [2]}, {nodes: [0]}], nodes: [{name: 'Root', children: [1]}, {name: 'Child', mesh: 0}, {name: 'Other', mesh: 0}],
    meshes: [{primitives: [prim(0), prim(1, 1)]}], accessors: [{count: 3}], materials: [{name: 'a'}, {name: 'b'}]};
  const uses = enumerateGltfUses(json);
  assert.deepEqual(uses.map(use => [use.key, use.nodeName, use.material, use.mode, use.triangles]),
    [['1:0', 'Child', 0, 4, 1], ['1:1', 'Child', 1, 1, 0]], 'the default scene only; a LINES primitive is a use too');
  const sceneless = enumerateGltfUses({nodes: [{mesh: 0}, {mesh: 0, children: [0]}], meshes: [{primitives: [prim()]}]});
  assert.deepEqual(sceneless.map(use => use.key), ['1:0', '0:0']);
});

test('the r9 legacy roles: crystal front and temples stay frame, the part_role lens is lens, the inscription is frame', () => {
  const json = {scenes: [{nodes: [0, 1, 2, 3, 4]}],
    nodes: [{name: 'Crystal front', mesh: 0, extras: {part_role: 'frame'}}, {name: 'Temple left', mesh: 1, extras: {part_role: 'temple_left'}},
      {name: 'Lens left', mesh: 2, extras: {part_role: 'lens_left'}}, {name: 'Lens inscription', mesh: 3, extras: {part_role: 'hardware'}},
      {name: 'Embedded core left', mesh: 4, extras: {part_role: 'hardware'}}],
    meshes: [{primitives: [prim(0)]}, {primitives: [prim(0)]}, {primitives: [prim(3)]}, {primitives: [prim(2)]}, {primitives: [prim(1)]}],
    materials: [{name: 'Crystal acetate', ...crystalT}, {name: 'Champagne gold hardware'}, {name: 'Subtle warm lens inscription'},
      {name: 'Warm brown optical tint', ...crystalT}, {name: 'Unused spare'}]};
  const {report} = identifyGltfJson(json);
  const entry = (name: string) => report.materials.find(row => row.name === name)!;
  assert.equal(entry('Crystal acetate').class, 'frame'); assert.equal(entry('Crystal acetate').signal, 'part_role_legacy');
  assert.equal(entry('Crystal acetate').value, 'frame'); assert.equal(entry('Crystal acetate').uses.length, 2);
  assert.ok(entry('Crystal acetate').notes.some(note => note.includes('explicit role')), 'see-through frame by role is noted');
  assert.equal(entry('Warm brown optical tint').class, 'lens'); assert.equal(entry('Warm brown optical tint').value, 'lens_left');
  assert.equal(entry('Subtle warm lens inscription').class, 'frame'); assert.equal(entry('Subtle warm lens inscription').value, 'hardware');
  assert.equal(entry('Unused spare').signal, 'unused'); assert.deepEqual(entry('Unused spare').uses, []);
  assert.equal(report.materials.length, 5, 'one entry per glTF material');
  assert.equal(report.source, 'gltf-json'); assert.equal(report.last_resort_used, false);
  assert.deepEqual(report.components, {pass: true, reasons: []}); assert.deepEqual(report.warnings, []);
  assert.equal(report.lens_material_count, 1); assert.equal(report.frame_material_count, 3);
  assert.deepEqual(entry('Crystal acetate').uses.map(use => [use.node, use.node_name, use.mesh, use.primitive, use.class, use.site]),
    [[0, 'Crystal front', 0, 0, 'frame', 'node'], [1, 'Temple left', 1, 0, 'frame', 'node']]);
});

test('Components failures: a material shared by lens and frame, the default material on a lens, nothing but lens', () => {
  const shared = identifyGltfJson({scenes: [{nodes: [0, 1]}], nodes: [{name: 'Lens_L', mesh: 0}, {name: 'Rim', mesh: 1}],
    meshes: [{primitives: [prim(0)]}, {primitives: [prim(0)]}], materials: [{name: 'Clear'}]}).report;
  assert.equal(shared.materials[0]!.class, 'mixed'); assert.equal(shared.mixed_material_count, 1);
  assert.equal(shared.components.pass, false); assert.match(shared.components.reasons[0]!, /used by both lens and non-lens/);
  assert.ok(shared.warnings.some(warning => /cloned per class/.test(warning) && /"Lens_L"/.test(warning) && /"Rim"/.test(warning)));
  const defaulted = identifyGltfJson({scenes: [{nodes: [0, 1]}], nodes: [{name: 'Lens', mesh: 0}, {name: 'Rim', mesh: 1}],
    meshes: [{primitives: [prim()]}, {primitives: [prim(0)]}], materials: [{name: 'Rim'}]}).report;
  assert.equal(defaulted.materials.at(-1)!.index, null); assert.equal(defaulted.materials.at(-1)!.class, 'lens');
  assert.equal(defaulted.components.pass, false); assert.match(defaulted.components.reasons.join(), /has no material/);
  const allLens = identifyGltfJson({scenes: [{nodes: [0]}], nodes: [{name: 'Shield lens', mesh: 0}], meshes: [{primitives: [prim(0)]}],
    materials: [{name: 'm'}]}).report;
  assert.match(allLens.components.reasons.join(), /no frame\/temple primitives/);
  const none = identifyGltfJson({scenes: [{nodes: [0]}], nodes: [{name: 'Body', mesh: 0}], meshes: [{primitives: [prim(0)]}], materials: [{name: 'm'}]}).report;
  assert.match(none.components.reasons.join(), /no lens identified/); assert.equal(none.decided_by.none, 1);
});

test('the loader path reads the glTF JSON, not three\'s merged userData', async t => {
  // GLTFLoader merges node extras over mesh extras on a single-primitive mesh: userData.partRole would say frame.
  // The JSON order (material > mesh > node) says lens, with a warning for the disagreeing node role.
  const gltf = await parse(glb({scenes: [{nodes: [0, 1, 2, 3]}],
    nodes: [{name: 'Disputed', mesh: 0, extras: {partRole: 'frame'}}, {name: 'Pair', mesh: 1, extras: {partRole: 'lens'}},
      {name: 'Front', mesh: 2, extras: {partRole: 'frame'}}, {name: 'Arm', mesh: 2, extras: {partRole: 'frame'}}],
    meshes: [{name: 'disputed mesh', extras: {partRole: 'lens'}, primitives: [prim(0)]},
      {name: 'pair mesh', primitives: [prim(1), prim(1)]}, {name: 'reused mesh', primitives: [prim(2)]}],
    materials: [{name: 'Smoke'}, {name: 'Tint', ...crystalT}, {name: 'Crystal', ...crystalT}]}));
  t.after(() => dispose(gltf.scenes));
  const disputed = meshNamed(gltf.scene, 'Disputed');
  assert.equal(disputed.userData.partRole, 'frame', 'three\'s merged userData would have decided frame');
  const classification = classifyAssetMaterials(gltf.scene, gltf);
  assert.equal(isOpticalMaterial(disputed.material as Material), true, 'the mesh extras outrank the node extras');
  assert.ok(classification.warnings.some(warning => /node "Disputed" primitive 0: node.extras.partRole='frame' disagrees with mesh.extras.partRole='lens'/.test(warning)));
  // A multi-primitive node: the node extras reach both primitive meshes below its Group.
  const pair: Mesh[] = [];
  gltf.scene.traverse(object => {if (object instanceof Mesh && object.parent?.name === 'Pair') pair.push(object);});
  assert.equal(pair.length, 2); assert.ok(pair.every(mesh => isOpticalMaterial(mesh.material as Material)));
  // A mesh reused by two frame nodes stays one frame material (the transmissive crystal is frame by role).
  for (const name of ['Front', 'Arm']) assert.equal(isTranslucentFrameMaterial(meshNamed(gltf.scene, name).material as Material), true);
  assert.equal(classification.lensIdentification.source, 'gltf-json');
  assert.deepEqual(classification.lensIdentification.materials.map(entry => [entry.name, entry.class, entry.site]),
    [['Smoke', 'lens', 'mesh'], ['Tint', 'lens', 'node'], ['Crystal', 'frame', 'node']]);
});

test('a material used by lens and frame primitives is cloned per class at load and the Components row fails', async t => {
  const gltf = await parse(glb({scenes: [{nodes: [0, 1]}],
    nodes: [{name: 'Lens_L', mesh: 0, extras: {partRole: 'lens'}}, {name: 'Pad', mesh: 1, extras: {partRole: 'frame'}}],
    meshes: [{primitives: [prim(0)]}, {primitives: [prim(0)]}], materials: [{name: 'Clear shared', ...crystalT}]}));
  t.after(() => dispose(gltf.scenes));
  const lens = meshNamed(gltf.scene, 'Lens_L'), pad = meshNamed(gltf.scene, 'Pad'), original = lens.material as Material;
  assert.equal(pad.material, original, 'GLTFLoader shares one material object');
  const classification = classifyAssetMaterials(gltf.scene, gltf);
  t.after(() => (pad.material as Material).dispose());
  assert.equal(lens.material, original, 'the lens keeps the original');
  assert.notEqual(pad.material, original, 'the frame use draws its own clone');
  assert.equal(materialClassOf(original), 'lens'); assert.equal(materialClassOf(pad.material as Material), 'frame');
  assert.equal((pad.material as MeshPhysicalMaterial).transmission, 1, 'the clone keeps every PBR property');
  assert.deepEqual(classification.sharedMaterials, [original]);
  assert.equal(classification.lensIdentification.materials[0]!.class, 'mixed');
  assert.equal(classification.lensIdentification.components.pass, false);
  assert.ok(classification.warnings.some(warning => /Clear shared.*cloned per class.*Components row fails/.test(warning)));
  const padMaterial = pad.material;
  const again = classifyAssetMaterials(gltf.scene, gltf);
  assert.equal(pad.material, padMaterial, 'a second classification creates no new instance');
  assert.deepEqual(again.sharedMaterials, []); assert.equal(lens.material, original);
});

test('the class is decided once: editing transmission after load never makes a frame optical or a lens frame', async t => {
  const gltf = await parse(glb({scenes: [{nodes: [0, 1]}], nodes: [{name: 'Lens_R', mesh: 0}, {name: 'Body', mesh: 1}],
    meshes: [{primitives: [prim(0)]}, {primitives: [prim(1)]}], materials: [{name: 'Clear'}, {name: 'Body acetate'}]}));
  t.after(() => dispose(gltf.scenes));
  classifyAssetMaterials(gltf.scene, gltf);
  const lens = meshNamed(gltf.scene, 'Lens_R').material as MeshPhysicalMaterial | Material;
  const body = meshNamed(gltf.scene, 'Body').material as Material;
  assert.equal(isOpticalMaterial(lens), true, 'an opaque lens named Lens is a lens by name');
  assert.equal(isFrameMaterial(body), true);
  if (body instanceof MeshPhysicalMaterial) body.transmission = 1;
  else Object.assign(body, {transmission: 1});
  assert.equal(isOpticalMaterial(body), false, 'no live transmission fallback');
  const stray = new MeshPhysicalMaterial({transmission: 1}); t.after(() => stray.dispose());
  assert.equal(isOpticalMaterial(stray), false, 'an unclassified material is never optical');
});

test('the shipped models are identified by their material name token, with no last resort', async t => {
  for (const [file, lensName] of [['amber-horizon.glb', 'AmberGradientLenses'], ['tom-ford-clear.glb', 'TomFordClearTestLenses']] as const) {
    const gltf = await parse(new Uint8Array(await readFile(publicFile(`models/${file}`))).buffer);
    t.after(() => dispose(gltf.scenes));
    const {lensIdentification: report, warnings} = classifyAssetMaterials(gltf.scene, gltf);
    assert.deepEqual(warnings, [], file);
    assert.equal(report.last_resort_used, false);
    assert.deepEqual(report.materials.map(entry => [entry.name, entry.class, entry.signal, entry.site]),
      [[report.materials[0]!.name, 'frame', 'none', null], [lensName, 'lens', 'name_token', 'material']], file);
    assert.deepEqual(report.components, {pass: true, reasons: []});
    const lenses: Material[] = [];
    gltf.scene.traverse(object => {if (object instanceof Mesh && isOpticalMaterial(object.material as Material)) lenses.push(object.material as Material);});
    assert.deepEqual(lenses.map(material => material.name), [lensName]);
  }
});

/* The owner's proof files (PLAN.md WP1.6), read-only from outside arview; skipped where this machine lacks them. */
const LENSES = new URL('../../../', import.meta.url);
const PROOF = {
  r9: new URL('ar_v4/model_studio/data/jobs/d92b404c-f2c7-483e-ae9c-aa8bcf1b870c/agent/operations/8c315e48f6b84cbc897cf397d772d6f0/candidate.glb', LENSES),
  ft1119: new URL('automation/data/blender_agent/studio/jobs/3b8a1c7ae8114868b49b298b681f1b29/original/model.glb', LENSES),
  prada: new URL('automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb', LENSES),
};
async function proof(url: URL) {
  const gltf = await parse(new Uint8Array(await readFile(url)).buffer);
  return {gltf, ...classifyAssetMaterials(gltf.scene, gltf)};
}
const classes = (report: {materials: readonly {name: string; class: string}[]}) => Object.fromEntries(report.materials.map(entry => [entry.name, entry.class]));

test('proof: the crystal Tom Ford r9 (legacy part_role) keeps its crystal as frame', {skip: !existsSync(PROOF.r9)}, async t => {
  const {gltf, lensIdentification: report, warnings, translucentFrameMaterials} = await proof(PROOF.r9);
  t.after(() => dispose(gltf.scenes));
  assert.deepEqual(classes(report), {'Crystal acetate': 'frame', 'Champagne gold hardware': 'frame',
    'Subtle warm lens inscription': 'frame', 'Warm brown optical tint': 'lens'});
  assert.equal(report.materials.find(entry => entry.name === 'Crystal acetate')!.signal, 'part_role_legacy');
  assert.deepEqual(warnings, []); assert.equal(report.components.pass, true);
  assert.ok(translucentFrameMaterials.length >= 1 && translucentFrameMaterials.every(material => material.name === 'Crystal acetate'));
  // three sanitizes node names (spaces become underscores).
  assert.equal(isOpticalMaterial(meshNamed(gltf.scene, 'Crystal_front').material as Material), false, 'the crystal front is not optical');
  assert.equal(isOpticalMaterial(meshNamed(gltf.scene, 'Lens_left').material as Material), true);
});

test('proof: FT1119 quality-001 (Studio partRole) keeps its clear pads as frame', {skip: !existsSync(PROOF.ft1119)}, async t => {
  const {gltf, lensIdentification: report, warnings} = await proof(PROOF.ft1119);
  t.after(() => dispose(gltf.scenes));
  const lens = Object.entries(classes(report)).filter(([, value]) => value !== 'frame');
  assert.deepEqual(lens, [['Champagne lens.001', 'lens']]);
  assert.equal(report.materials.find(entry => entry.name === 'Clear pads.001')!.signal, 'partRole');
  assert.deepEqual(warnings, []); assert.equal(report.components.pass, true);
});

test('proof: the Prada delivery keeps its etched lens as lens by role', {skip: !existsSync(PROOF.prada)}, async t => {
  const {gltf, lensIdentification: report, warnings} = await proof(PROOF.prada);
  t.after(() => dispose(gltf.scenes));
  const lens = Object.entries(classes(report)).filter(([, value]) => value !== 'frame').map(([name]) => name);
  assert.deepEqual(lens, ['Lens | light brown neutral transmission.001', 'Lens | subtle etched lettering.001']);
  assert.deepEqual(warnings, []); assert.equal(report.components.pass, true);
});
