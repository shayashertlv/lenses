/** The renderer's model report (WP1_DESIGN §1, §5): every fallback the load takes is a warning, never silent. Covers the
 *  definition's own fallbacks, the retired canonical lens descriptor (stripped, then drawn as its native material) and the
 *  temple continuity fallback. The renderer runs on a backend that draws nothing. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {BoxGeometry, Color, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Texture, Vector2, Vector4} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import type {WebGLRenderTarget} from 'three';
import {TryOnRenderer} from '../src/render/renderer.ts';
import type {RendererOptions} from '../src/render/renderer.ts';
import {LiveRenderer} from '../src/render/live-renderer.ts';
import {resolveModelDefinition, TEMPLE_CLIP_FLOOR_LOCAL_Z_M} from '../src/eyewear/catalog.ts';
import type {ModelDefinition} from '../src/eyewear/catalog.ts';
import {gltfSourceOf, isOpticalMaterial, isTranslucentFrameMaterial, RETIRED_LENS_APPEARANCE_EXTENSION} from '../src/eyewear/optical-material.ts';
import type {GltfSource} from '../src/eyewear/optical-material.ts';
import {publicFile, shippedModel} from './shipped-models.ts';

const canonical = JSON.parse(await readFile(publicFile('models/canonical-face.json'), 'utf8')) as {positions: number[]; indices: number[]};

function renderer(model: ModelDefinition) {
  const state = {target: null as WebGLRenderTarget | null, color: new Color(), alpha: 1};
  const noop = () => {};
  const backend = {
    capabilities: {samples: 0, isWebGL2: true}, extensions: {has: () => true}, autoClear: true, toneMapping: 0, toneMappingExposure: 1, outputColorSpace: '',
    xr: {enabled: false}, state: {buffers: {stencil: {setMask: noop, setClear: noop}, depth: {setMask: noop, getReversed: () => false}}},
    setPixelRatio: noop, setSize: noop, setScissorTest: noop, clear: noop, clearDepth: noop, compile: noop, render: noop, dispose: noop,
    forceContextLoss: noop, getDrawingBufferSize: (out: Vector2) => out.set(640, 480),
    getRenderTarget: () => state.target, setRenderTarget: (value: WebGLRenderTarget | null) => {state.target = value;},
    getActiveCubeFace: () => 0, getActiveMipmapLevel: () => 0,
    getViewport: (out: Vector4) => out, setViewport: noop, getScissor: (out: Vector4) => out, setScissor: noop, getScissorTest: () => false,
    getClearColor: (out: Color) => out.copy(state.color), getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha = 1) => {state.color.set(value); state.alpha = alpha;},
  };
  const gl = {SCISSOR_TEST: 1, COLOR_BUFFER_BIT: 2, DEPTH_BUFFER_BIT: 4, STENCIL_BUFFER_BIT: 8, disable: noop, enable: noop, clear: noop, scissor: noop};
  const instance = new (TryOnRenderer as unknown as new (backend: unknown, gl: unknown, model: unknown, options: RendererOptions) => TryOnRenderer)(
    backend, gl, resolveModelDefinition(model), {sync: false, guard: true});
  const internal = instance as unknown as {configure(face: typeof canonical, root: Group, source?: GltfSource | null): void; noteContinuityFailure(error: unknown): void;
    continuityFailure: string | null; endpointUnavailable: string | null};
  return {instance, internal};
}

/** A crystal asset as the exporter writes it, whose lens still carries the retired descriptor (a retired-route GLB). */
function retiredRouteAsset() {
  const lens = new MeshPhysicalMaterial({name: 'Retired route lens', transmission: 1, roughness: .05});
  lens.userData.gltfExtensions = {[RETIRED_LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: {}}};
  const crystal = new MeshPhysicalMaterial({name: 'Crystal acetate', transmission: 1, thickness: .004});
  const metal = new MeshStandardMaterial({name: 'Hinge metal', metalness: 1});
  const lensMesh = new Mesh(new BoxGeometry(.05, .025, .002).translate(0, 0, -.01), lens);
  const front = new Mesh(new BoxGeometry(.13, .04, .005).translate(0, 0, -.005), crystal);
  const left = new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(-.065, 0, -.1), metal);
  const right = new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(.065, 0, -.1), metal);
  lensMesh.userData.partRole = 'lens'; front.userData.partRole = 'frame'; left.userData.partRole = right.userData.partRole = 'temple';
  return {root: new Group().add(lensMesh, front, left, right), lens, crystal, lensMesh};
}

test('the report starts with the definition\'s own fallbacks and is a copy', t => {
  const {instance} = renderer({url: 'http://127.0.0.1:8250/m/model.glb', displayName: '', templeClipZM: 0.4, warnings: ['handover note']});
  t.after(() => instance.dispose());
  const report = instance.report();
  assert.equal(report.url, 'http://127.0.0.1:8250/m/model.glb'); assert.equal(report.displayName, 'model.glb');
  assert.equal(report.warnings.length, 3);
  assert.equal(report.warnings[0], 'handover note', 'the handover\'s own warnings come first');
  assert.match(report.warnings[1]!, /no display name/); assert.match(report.warnings[2]!, /temple clip depth 0.4.*ignored/);
  assert.equal(instance.model.templeClipZM, null, 'the unusable clip is dropped: the renderer derives one from the temples');
  assert.equal(instance.templeClipZM, TEMPLE_CLIP_FLOOR_LOCAL_Z_M, 'until a model is configured the clip is the floor');
  (report.warnings as string[]).push('mutated');
  assert.equal(instance.report().warnings.length, 3, 'callers cannot edit the report');
  const clean = renderer({url: '/models/amber-horizon.glb', displayName: 'Amber Horizon', templeClipZM: -0.14}).instance;
  t.after(() => clean.dispose());
  const cleanReport = clean.report();
  assert.deepEqual([cleanReport.url, cleanReport.displayName, cleanReport.status, cleanReport.warnings, cleanReport.fallbacks],
    ['/models/amber-horizon.glb', 'Amber Horizon', 'rendered', [], []]);
  assert.deepEqual(report.fallbacks.map(entry => [entry.subsystem, entry.stage]), [['definition', 'load'], ['definition', 'load'], ['definition', 'load']],
    'every definition fallback is a fallbacks[] entry as well as a warning');
});

test('a retired canonical descriptor is stripped with a warning and the lens draws as its native material', t => {
  const {instance, internal} = renderer({url: '/m/retired.glb', displayName: 'Retired', templeClipZM: -0.14});
  t.after(() => instance.dispose());
  const asset = retiredRouteAsset();
  internal.configure(canonical, asset.root);
  // The hand-built asset sits off the origin (its front's underside is at y -20 mm): that is a loud ORIGIN warning of
  // its own (WP1_DESIGN.md §1), asserted apart from the fallback under test.
  const all = instance.report().warnings, warnings = all.filter(w => !w.startsWith('ORIGIN: '));
  assert.ok(all.includes('ORIGIN: bridge underside is at y -20.00 mm (must be 0 +/- 1.0 mm). arview renders the model where it was authored; '
    + 'the delivered file violates §2 Origin.'), all.join(' | '));
  assert.equal(warnings.length, 1);
  assert.match(warnings[0]!, new RegExp(`"Retired route lens" carried the retired ${RETIRED_LENS_APPEARANCE_EXTENSION} descriptor; it was stripped`));
  assert.equal(asset.lens.userData.gltfExtensions, undefined, 'nothing downstream can read the descriptor');
  assert.equal(asset.lensMesh.material, asset.lens, 'no replacement material: the authored lens draws');
  assert.equal(asset.lens.transmission, 1);
  assert.ok(isOpticalMaterial(asset.lens), 'the lens is a lens by its role');
  assert.ok(isTranslucentFrameMaterial(asset.crystal), 'the crystal beside it is frame');
});

test('the continuity fallback is reported, and the endpoint stays unavailable', t => {
  const {instance, internal} = renderer({url: '/m/unpinned.glb', displayName: 'Unpinned', templeClipZM: -0.14});
  t.after(() => instance.dispose());
  internal.noteContinuityFailure(new Error('No pinned original geometry is available for this frame model.'));
  assert.equal(instance.endpointUnavailable, 'No pinned original geometry is available for this frame model.');
  assert.deepEqual(instance.report().warnings, ['The temple continuity model is unavailable (No pinned original geometry is available '
    + 'for this frame model.); both arms keep the fixed buried cap and no hair endpoint.']);
});

test('the live renderer forwards the model and its report', t => {
  const {instance} = renderer({url: '/m/x.glb', displayName: ''});
  const live = new (LiveRenderer as unknown as new (inner: TryOnRenderer) => LiveRenderer)(instance);
  t.after(() => live.dispose());
  assert.equal(live.model, instance.model);
  assert.deepEqual(live.report(), instance.report());
  assert.match(live.report().warnings[0]!, /no display name/);
});

/** Two opaque arms and an optional lens sheet, with no roles and no names: nothing but rule 3 can find a lens. */
function untaggedAsset(lens: boolean) {
  const metal = new MeshStandardMaterial({name: 'Metal'}), clear = new MeshPhysicalMaterial({name: 'Clear', transmission: 1});
  const root = new Group();
  for (const sign of [-1, 1]) root.add(new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(sign * .065, 0, -.1), metal));
  if (lens) root.add(new Mesh(new BoxGeometry(.05, .025, .002).translate(0, 0, -.01), clear));
  return {root, clear};
}

test('the lens identification is exposed as a copy and every fallback it took is a model-report warning', t => {
  const {instance, internal} = renderer({url: '/m/untagged.glb', displayName: 'Untagged', templeClipZM: -0.14});
  t.after(() => instance.dispose());
  assert.equal(instance.lensIdentification(), null, 'nothing before a model is configured');
  const asset = untaggedAsset(true);
  internal.configure(canonical, asset.root);
  // The untagged asset is off the origin too: its ORIGIN warnings are the origin row's, not the identification's.
  const all = instance.report().warnings, warnings = all.filter(w => !w.startsWith('ORIGIN: '));
  assert.ok(all.some(w => w.startsWith('ORIGIN: bridge underside is at y -12.50 mm')), all.join(' | '));
  assert.equal(warnings.length, 1);
  assert.match(warnings[0]!, /^Lens identification: mesh "\(unnamed mesh\)": identified as lens only by transmissionFactor=1 \(last resort/);
  const report = instance.lensIdentification()!;
  assert.equal(report.source, 'scene-userData'); assert.equal(report.last_resort_used, true);
  assert.deepEqual(report.materials.map(entry => [entry.name, entry.class, entry.signal]), [['Metal', 'frame', 'none'], ['Clear', 'lens', 'transmission']]);
  assert.ok(isOpticalMaterial(asset.clear));
  (report.materials as unknown[]).length = 0;
  assert.equal(instance.lensIdentification()!.materials.length, 2, 'callers cannot edit the block');
});

test('a model with no identified lens is refused (§2 Components)', t => {
  const {instance, internal} = renderer({url: '/m/no-lens.glb', displayName: 'No lens', templeClipZM: -0.14});
  t.after(() => instance.dispose());
  assert.throws(() => internal.configure(canonical, untaggedAsset(false).root), /§2 Components: no lens identified/);
});

test('the renderer identifies a loaded GLB from its glTF JSON', async t => {
  const bytes = await readFile(publicFile('models/amber-horizon.glb'));
  const gltf = await new GLTFLoader().register(() => ({name: 'ReportTestTextures', loadTexture: async () => new Texture()}))
    .parseAsync(new Uint8Array(bytes).buffer, '/models/');
  const {instance, internal} = renderer(shippedModel('amber-horizon'));
  t.after(() => instance.dispose());
  internal.configure(canonical, gltf.scene, gltfSourceOf(gltf));
  assert.deepEqual(instance.report().warnings, [], 'the shipped model needs no fallback');
  const report = instance.lensIdentification()!;
  assert.equal(report.source, 'gltf-json');
  assert.deepEqual(report.materials.map(entry => [entry.index, entry.name, entry.class, entry.signal, entry.site, entry.value]),
    [[0, 'AmberFrame', 'frame', 'none', null, null], [1, 'AmberGradientLenses', 'lens', 'name_token', 'material', 'lenses']]);
  assert.deepEqual(report.materials[1]!.uses.map(use => [use.node, use.node_name, use.mesh, use.primitive, use.class]),
    [[0, 'AmberHorizon', 0, 1, 'lens']]);
  assert.deepEqual(report.components, {pass: true, reasons: []});
});
