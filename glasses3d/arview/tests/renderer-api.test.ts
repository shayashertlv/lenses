/** The renderer on a loaded GLB (WP1_DESIGN.md §1, §5): the normalisation pass runs inside configure before any
 *  subsystem, every subsystem that cannot be built falls back on its own (reported), and the public capture accessors
 *  (report, cameraState, assetMatrixWorld, parts, projectAssetPoints, setLighting, render overrides) replace private
 *  field access. The renderer runs on a recording backend that draws nothing. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Box3, Color, Euler, Group, Matrix4, Mesh, MeshBasicMaterial, MeshNormalMaterial, MeshPhysicalMaterial, MeshStandardMaterial, PerspectiveCamera,
  Quaternion, Texture, Vector2, Vector3, Vector4} from 'three';
import type {Material, Object3D, Scene, WebGLRenderTarget} from 'three';
import type {GLTFLoaderPlugin} from 'three/addons/loaders/GLTFLoader.js';
import {TryOnRenderer} from '../src/render/renderer.ts';
import type {RendererOptions} from '../src/render/renderer.ts';
import {resolveModelDefinition} from '../src/eyewear/catalog.ts';
import type {ModelDefinition} from '../src/eyewear/catalog.ts';
import type {GltfSource} from '../src/eyewear/optical-material.ts';
import {isOpticalMaterial} from '../src/eyewear/optical-material.ts';
import {setMaterialClass} from '../src/eyewear/part-class.ts';
import {loadModel} from '../src/model/load.ts';
import {ModelRefusal, ModelReportBuilder, reportLines, SPEC_ROWS} from '../src/model/report.ts';
import {LENS_ID_RGB} from '../src/render/override.ts';
import {checkModel} from '../src/model/check.ts';
import {deriveTempleClip, templeInventory, templeLoadFallbacks} from '../src/render/temple-geometry.ts';
import type {Detection} from '../src/face/protocol.ts';
import {buildGlb, defaultParts, r9Like, without} from './glb-fab.ts';
import type {BuildOptions, Built} from './glb-fab.ts';
import {publicFile} from './shipped-models.ts';

const canonical = JSON.parse(await readFile(publicFile('models/canonical-face.json'), 'utf8')) as {positions: number[]; indices: number[]};
const stubTextures = (): GLTFLoaderPlugin => ({name: 'ArviewTestTextures', loadTexture: async () => new Texture()});
const frame = {width: 640, height: 480} as HTMLCanvasElement;

function detection(pitchDegrees = 0, distanceCm = 45): Detection {
  const matrix = new Matrix4().compose(new Vector3(0, 0, -distanceCm),
    new Quaternion().setFromEuler(new Euler(pitchDegrees * Math.PI / 180, 0, 0)), new Vector3(1, 1, 1));
  const camera = new PerspectiveCamera(63, 4 / 3, 1, 10_000); camera.updateMatrixWorld();
  return {matrix: matrix.toArray(), inferenceMs: 0, landmarks: Array.from({length: 478}, (_, i) => {
    if (i >= 468) return {x: .5, y: .5, z: 0};
    const projected = new Vector3().fromArray(canonical.positions, i * 3).applyMatrix4(matrix).project(camera);
    return {x: (projected.x + 1) / 2, y: (1 - projected.y) / 2, z: 0};
  })};
}

interface Draw {background: unknown; meshes: {name: string; visible: boolean; material: Material | Material[]}[]}
interface Internal {
  scene: Scene; eyewearPose: Group;
  configure(face: typeof canonical, root: Group, source?: GltfSource | null): void;
  twinOf(material: Material): Material;
}

/** A renderer on a backend that records every canvas draw of the main scene. */
function renderer(model: ModelDefinition, report?: ModelReportBuilder) {
  const draws: Draw[] = [];
  const state = {target: null as WebGLRenderTarget | null, color: new Color(), alpha: 1};
  const noop = () => {};
  let instance: TryOnRenderer;
  const backend = {
    capabilities: {samples: 0, isWebGL2: true}, extensions: {has: () => true}, autoClear: true, toneMapping: 0, toneMappingExposure: 1, outputColorSpace: '',
    xr: {enabled: false}, state: {buffers: {stencil: {setMask: noop, setClear: noop}, depth: {setMask: noop, getReversed: () => false}}},
    setPixelRatio: noop, setSize: noop, setScissorTest: noop, clear: noop, clearDepth: noop, compile: noop, dispose: noop,
    render(scene: Scene) {
      const internal = instance as unknown as Internal;
      if (scene !== internal.scene || state.target !== null) return;
      const meshes: Draw['meshes'] = [];
      internal.eyewearPose.traverse(object => {
        if (!(object instanceof Mesh)) return;
        let visible = object.visible;
        for (let parent: Object3D | null = object.parent; parent; parent = parent.parent) visible &&= parent.visible;
        meshes.push({name: object.name, visible, material: object.material});
      });
      draws.push({background: scene.background, meshes});
    },
    forceContextLoss: noop, getDrawingBufferSize: (out: Vector2) => out.set(640, 480),
    getRenderTarget: () => state.target, setRenderTarget: (value: WebGLRenderTarget | null) => {state.target = value;},
    getActiveCubeFace: () => 0, getActiveMipmapLevel: () => 0,
    getViewport: (out: Vector4) => out, setViewport: noop, getScissor: (out: Vector4) => out, setScissor: noop, getScissorTest: () => false,
    getClearColor: (out: Color) => out.copy(state.color), getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha = 1) => {state.color.set(value); state.alpha = alpha;},
  };
  const gl = {SCISSOR_TEST: 1, COLOR_BUFFER_BIT: 2, DEPTH_BUFFER_BIT: 4, STENCIL_BUFFER_BIT: 8, disable: noop, enable: noop, clear: noop, scissor: noop};
  const resolved = resolveModelDefinition(model);
  instance = new (TryOnRenderer as unknown as new (backend: unknown, gl: unknown, model: unknown, options: RendererOptions, report?: ModelReportBuilder) => TryOnRenderer)(
    backend, gl, resolved, {sync: false, guard: true, steady: null}, report);
  return {instance, internal: instance as unknown as Internal, draws};
}

/** Load `built` through the front-end and configure a renderer with it, as TryOnRenderer.create does. No clip is handed
 *  over by default: the renderer derives it from the model's temples (WP1_DESIGN.md §3). */
async function configured(built: Built, model: Partial<ModelDefinition> = {}) {
  const definition: ModelDefinition = {url: 'http://127.0.0.1:8250/models/model.glb', displayName: 'Test', ...model};
  const resolved = resolveModelDefinition(definition);
  const report = new ModelReportBuilder(resolved.url, resolved.displayName, resolved.warnings);
  const loaded = await loadModel(built.glb, report, {url: resolved.url, renderer: null, plugins: [stubTextures]});
  const harness = renderer(definition, report);
  harness.internal.configure(canonical, loaded.gltf.scene, loaded.source);
  harness.instance.setShadows({enabled: false});
  return {...harness, loaded};
}
const build = (options: BuildOptions = {}) => buildGlb(options);
const reveal = (instance: TryOnRenderer) => Object.assign(instance, {fitRevealed: true});

test('a plain GLB: the report carries all seven §2 rows, the rendered SHA-256, the lens identification and no fallback', async t => {
  const built = build();
  const {instance} = await configured(built);
  t.after(() => instance.dispose());
  const report = instance.report();
  assert.equal(report.status, 'rendered'); assert.deepEqual(report.refusal, []);
  assert.deepEqual(SPEC_ROWS.filter(row => !report.spec[row].pass), []);
  assert.deepEqual(report.warnings, []); assert.deepEqual(report.fallbacks, []);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', built.glb)), v => v.toString(16).padStart(2, '0')).join('');
  assert.equal(report.rendered_sha256, digest);
  assert.deepEqual(instance.renderedAssetIntegrity, {sha256: digest});
  assert.equal(report.lens_identification?.lens_material_count, 1);
  assert.equal(instance.endpointUnavailable, null, 'continuity is built from the normalised scene: no pin, no second parse');
  assert.deepEqual(report.measurements.fit_scale, {mode: 'auto', applied: 1, state: 'collecting'}, 'the fit scale is reported before the fit settles too');
  assert.deepEqual((report.measurements.temple_clip as {source: string}).source, 'derived');
  assert.deepEqual(instance.report(), report, 'a copy each time');
});

test('a millimetre GLB is rescaled before the rear drop and continuity read it: both build', async t => {
  const {instance} = await configured(build({scale: 1.0}));
  t.after(() => instance.dispose());
  const report = instance.report();
  assert.equal(report.status, 'rendered');
  assert.deepEqual(report.fallbacks.map(f => f.subsystem), ['units']);
  assert.equal(instance.endpointUnavailable, null);
});

test('a §2 refusal inside configure throws ModelRefusal with the whole report', async () => {
  const definition = {url: 'http://127.0.0.1:8250/models/nan.glb', displayName: 'NaN', templeClipZM: -0.12};
  const report = new ModelReportBuilder(definition.url, definition.displayName);
  const loaded = await loadModel(build({nan: true, target: 'Brow_L'}).glb, report, {url: definition.url, renderer: null, plugins: [stubTextures]});
  const {instance, internal} = renderer(definition, report);
  try {
    assert.throws(() => internal.configure(canonical, loaded.gltf.scene, loaded.source), (error: unknown) => error instanceof ModelRefusal
      && error.report.status === 'refused' && error.report.refusal[0] === '§2 Geometry: Brow_L: non-finite vertex positions'
      && error.report.spec.units.pass && error.report.rendered_sha256 !== null);
  } finally {instance.dispose();}
});

// Rewritten for WP1 stage D2 (WP1_DESIGN.md §3): a model without temples used to make the rear drop refuse ('no eligible
// posterior opaque shaft', a 'rear drop' fallback that also turned the protection and hair off). Temples are now found by
// position per side: each missing side is its own 'temple <side>' fallback (fixed cap), the rear drop builds with no arm
// to bend (everything protected), the continuity has no arm, and the §2 Orientation row still fails on its own.
test('a model with no temples renders: each missing side is reported, the §2 row fails, and the rear drop builds with no arm', async t => {
  const {instance} = await configured(build({parts: without(['Temple_L', 'Temple_R'])}));
  t.after(() => instance.dispose());
  const report = instance.report();
  assert.equal(report.status, 'rendered');
  assert.ok(!report.spec.orientation.pass && report.spec.orientation.detail.some(d => d.includes('temple left missing')), 'the §2 failure is never hidden');
  const subsystems = report.fallbacks.map(f => f.subsystem);
  for (const expected of ['temple left', 'temple right', 'continuity', 'temple clip depth']) assert.ok(subsystems.includes(expected), `${expected} in ${subsystems.join(', ')}`);
  assert.ok(!subsystems.includes('rear drop'), 'no temple is no longer a rear-drop refusal');
  assert.match(report.fallbacks.find(f => f.subsystem === 'temple left')!.detail, /^TEMPLE LEFT MISSING: the non-lens geometry at x < -45 mm reaches only z -2\.0 mm/);
  assert.deepEqual((report.measurements.temple_clip as {source: string}).source, 'default');
  for (const fallback of report.fallbacks) assert.ok(report.warnings.includes(fallback.detail), 'every fallback is a warning');
  // What the webcam page lists (main.ts shows reportLines): the geometry defect itself, ahead of the fallbacks it caused.
  const lines = reportLines(report), texts = lines.map(line => line.text);
  const defect = texts.findIndex(text => text.startsWith('Orientation: temple left missing'));
  assert.ok(defect >= 0 && lines[defect]!.kind === 'error', `the orientation failure is on the page: ${texts.join(' | ')}`);
  assert.ok(texts.some(text => text.startsWith('Orientation: temple right missing')), texts.join(' | '));
  for (const fallback of report.fallbacks) assert.ok(texts.indexOf(fallback.detail) > defect, `${fallback.subsystem} is listed after the defect`);
  assert.ok(report.errors.some(error => error.startsWith('orientation: temple left missing')), 'and in the report errors, worded as spec.py');
  reveal(instance);
  assert.equal(instance.pose(frame, detection(), 100), true);
  const timing = instance.render(null);
  assert.deepEqual([timing.guarded, timing.safeFallback, timing.editableRects, timing.hairApplied], [true, false, 0, false],
    'the whole model is protected and no arm corridor is editable, so hair never touches it');
});

test('the renderer\'s temple load fallbacks are exactly templeLoadFallbacks on the checked scene (what arview serve reports before drawing)', async t => {
  const hinges = {...without(['Temple_L', 'Temple_R']),
    Hinge_L: {box: [-70, -60, 2, 8, -40, -2] as const, material: 'frame', role: 'hardware'},
    Hinge_R: {box: [60, 70, 2, 8, -40, -2] as const, material: 'frame', role: 'hardware'}};
  const cases: [string, Built, number][] = [['no temples', build({parts: without(['Temple_L', 'Temple_R'])}), 4], ['one-sided', build({parts: without(['Temple_R'])}), 1],
    ['hinge hardware only', build({parts: hinges}), 4], ['full', build(), 0]];
  for (const [name, built, count] of cases) {
    const {instance} = await configured(built);
    t.after(() => instance.dispose());
    const checked = await checkModel(built.glb.slice(0), {url: 'http://127.0.0.1:8250/models/model.glb', renderer: null, plugins: [stubTextures]});
    const inventory = templeInventory(checked.scene!), predicted = templeLoadFallbacks(inventory, deriveTempleClip(inventory));
    assert.equal(predicted.length, count, `${name}: ${JSON.stringify(predicted)}`);
    assert.deepEqual(instance.report().fallbacks.filter(entry => entry.stage === 'load'), predicted, name);
  }
});

test('a one-sided model: the missing side gets the fixed cap and its own report lines, the present arm keeps everything', async t => {
  const {instance} = await configured(build({parts: without(['Temple_R'])}));
  t.after(() => instance.dispose());
  const report = instance.report();
  assert.equal(report.status, 'rendered');
  assert.deepEqual(report.spec.orientation.detail.filter(d => d.startsWith('temple ')).map(d => d.slice(0, 20)), ['temple right missing']);
  assert.deepEqual(report.fallbacks.map(f => f.subsystem), ['temple right'], 'only the missing side falls back');
  assert.match(report.fallbacks[0]!.detail, /^TEMPLE RIGHT MISSING: the non-lens geometry at x > \+45 mm reaches only z -2\.0 mm .*That side keeps a fixed cap/);
  const temples = report.measurements.temples as {left: {present: boolean; tip_z_mm: number}; right: {present: boolean}; decided_by: string};
  assert.deepEqual([temples.decided_by, temples.left.present, temples.left.tip_z_mm, temples.right.present], ['position', true, -140, false]);
  // The clip comes from the one arm there is (just inside its tip, at the -0.14 m floor here).
  assert.ok(Math.abs(instance.templeClipZM - (-.14 + 1e-6)) < 1e-9, `clip ${instance.templeClipZM}`);
  assert.deepEqual(report.measurements.temple_clip, {clip_m: instance.templeClipZM, source: 'derived', derived_m: instance.templeClipZM, handover_m: null,
    shorter_side: 'left', floor_m: -.14, range_m: [-.2, -.045]});
  const internal = instance as unknown as {rearDrop: {diagnostics: {armSides: boolean[]; affectedVertexCountBySide: number[]}};
    continuityModel: {sides: unknown[][]; missing: {side: number}[]}};
  assert.deepEqual(internal.rearDrop.diagnostics.armSides, [true, false]);
  assert.ok(internal.rearDrop.diagnostics.affectedVertexCountBySide[0]! > 0 && internal.rearDrop.diagnostics.affectedVertexCountBySide[1] === 0);
  assert.deepEqual(internal.continuityModel.sides.map(side => side.length), [33, 0]);
  assert.deepEqual(internal.continuityModel.missing.map(gap => gap.side), [1]);
  assert.equal(instance.endpointUnavailable, null, 'the left arm still has its hair endpoint');
  assert.deepEqual(instance.temples()!.present, [true, false]);
  reveal(instance); instance.pose(frame, detection(), 100);
  const timing = instance.render(null);
  assert.equal(timing.editableRects, 1, 'one arm corridor');
  assert.equal(instance.paths?.length, 1, 'one projected arm path');
});

test('hinge hardware alone is not a temple: both sides missing, reported, and the model still renders', async t => {
  const parts = {...without(['Temple_L', 'Temple_R']),
    Hinge_L: {box: [-70, -60, 2, 8, -40, -2] as const, material: 'frame', role: 'hardware'},
    Hinge_R: {box: [60, 70, 2, 8, -40, -2] as const, material: 'frame', role: 'hardware'}};
  const {instance} = await configured(build({parts}));
  t.after(() => instance.dispose());
  const report = instance.report();
  assert.equal(report.status, 'rendered');
  for (const side of ['left', 'right']) {
    assert.ok(report.spec.orientation.detail.some(d => d.startsWith(`temple ${side} missing`) && d.includes('reaches only z -40.0 mm')), side);
    const fallback = report.fallbacks.find(f => f.subsystem === `temple ${side}`);
    assert.match(fallback?.detail ?? '', /reaches only z -40\.0 mm \(a temple reaches z <= -60\.0 mm; hinge hardware alone is not a temple\)/);
  }
  assert.deepEqual(instance.temples()!.present, [false, false]);
  assert.match(instance.endpointUnavailable ?? '', /No temple on either side/);
  const internal = instance as unknown as {rearDrop: {diagnostics: {affectedVertexCount: number}}};
  assert.equal(internal.rearDrop.diagnostics.affectedVertexCount, 0, 'the hinges are not bent like arms');
  reveal(instance); assert.equal(instance.pose(frame, detection(), 100), true); instance.render(null);
});

test('broken material hooks: the temple clip, temple visibility, hair occlusion and shadows each fall back, reported', async t => {
  const built = build();
  const definition: ModelDefinition = {url: 'http://127.0.0.1:8250/models/model.glb', displayName: 'Test', templeClipZM: -0.12};
  const report = new ModelReportBuilder(definition.url, definition.displayName);
  const loaded = await loadModel(built.glb, report, {url: definition.url, renderer: null, plugins: [stubTextures]});
  loaded.gltf.scene.traverse(object => {
    if (!(object instanceof Mesh) || !object.name.startsWith('Temple')) return;
    const material = (object.material as MeshStandardMaterial).clone();
    Object.defineProperty(material, 'onBeforeCompile', {get() {throw new Error('hook unavailable');}, set() {throw new Error('hook unavailable');}});
    const map = new Texture(); map.updateMatrix = () => {throw new Error('texture matrix unavailable');};
    material.map = map; object.material = material;
  });
  const {instance, internal} = renderer(definition, report);
  t.after(() => instance.dispose());
  internal.configure(canonical, loaded.gltf.scene, loaded.source);
  const subsystems = instance.report().fallbacks.map(f => f.subsystem);
  for (const expected of ['temple clip', 'temple visibility', 'hair occlusion', 'shadows']) assert.ok(subsystems.includes(expected), `${expected} in ${subsystems.join(', ')}`);
  assert.match(instance.report().warnings.join(' | '), /The eyewear shadows could not be built \(texture matrix unavailable\); the frame draws without shadows\./);
});

test('a twin that cannot be built keeps the material on three\'s own transmission, reported once', async t => {
  const {instance, internal} = await configured(r9Like());
  t.after(() => instance.dispose());
  class Unclonable extends MeshPhysicalMaterial {override clone(): this {throw new Error('cannot clone');}}
  const material = new Unclonable({name: 'Unclonable crystal', transmission: 1}); setMaterialClass(material, 'frame');
  assert.equal(internal.twinOf(material), material); assert.equal(internal.twinOf(material), material);
  const twinFallbacks = instance.report().fallbacks.filter(f => f.subsystem === 'translucent twin');
  assert.equal(twinFallbacks.length, 1); assert.equal(twinFallbacks[0]!.stage, 'render');
  assert.match(twinFallbacks[0]!.detail, /"Unclonable crystal" could not be built \(cannot clone\); it is drawn with Three's own transmission/);
});

test('the r9-style crystal renders: origin row failed, crystal drawn through its twins, lenses by legacy part_role', async t => {
  const {instance} = await configured(r9Like());
  t.after(() => instance.dispose());
  const report = instance.report();
  assert.equal(report.status, 'rendered');
  assert.deepEqual(SPEC_ROWS.filter(row => !report.spec[row].pass), ['origin']);
  const parts = instance.parts().filter(part => !part.overlay);
  assert.deepEqual(parts.filter(part => part.class === 'lens').map(part => [part.name, part.side]), [['Lens left', 'left'], ['Lens right', 'right']]);
  assert.deepEqual(parts.find(part => part.name === 'Temple left')?.side, 'right', 'sides come from geometry, never from names (r9 "Temple left" is at +X)');
  reveal(instance); instance.pose(frame, detection(), 100); instance.render(null);
  assert.ok((instance as unknown as {translucentTwins: Map<unknown, unknown>}).translucentTwins.size > 0, 'the crystal draws through its camera twins');
});

test('capture accessors: camera state, the asset matrix as drawn, projection of asset points, parts', async t => {
  const {instance} = await configured(build());
  t.after(() => instance.dispose());
  assert.equal(instance.cameraState(), null); assert.equal(instance.assetMatrixWorld(), null);
  assert.equal(instance.projectAssetPoints(new Float32Array(3)), null, 'nothing before the first pose');
  reveal(instance);
  assert.equal(instance.pose(frame, detection(), 100), true);
  const camera = instance.cameraState()!;
  assert.deepEqual([camera.width, camera.height, camera.verticalFovDegrees], [640, 480, 63]);
  const asset = instance.assetMatrixWorld()!;
  assert.equal(asset.length, 16);
  // The bridge origin, projected by hand with the reported matrices, lands where projectAssetPoints puts it.
  const clip = new Vector4(0, 0, 0, 1).applyMatrix4(new Matrix4().fromArray(asset)).applyMatrix4(new Matrix4().fromArray(camera.view))
    .applyMatrix4(new Matrix4().fromArray(camera.projection));
  const projected = instance.projectAssetPoints(new Float32Array([0, 0, 0, 0, 0, 10]))!;
  assert.ok(Math.abs(projected[0]! - (clip.x / clip.w + 1) * 320) < 1e-3 && Math.abs(projected[1]! - (1 - clip.y / clip.w) * 240) < 1e-3);
  assert.ok(projected[0]! > 200 && projected[0]! < 440 && projected[1]! > 100 && projected[1]! < 380, 'the bridge is near the frame centre');
  assert.ok(Number.isNaN(projected[3]!), 'a point behind the camera is NaN');
  const parts = instance.parts();
  assert.deepEqual(parts.filter(part => !part.overlay).map(part => [part.name, part.class, part.side]), [
    ['Bridge', 'frame', 'centre'], ['Brow_L', 'frame', 'left'], ['Brow_R', 'frame', 'right'], ['Lens_L', 'lens', 'left'], ['Lens_R', 'lens', 'right'],
    ['Temple_L', 'frame', 'left'], ['Temple_R', 'frame', 'right']]);
  assert.ok(parts.some(part => part.overlay), 'the near-arm overlays are listed and flagged');
});

test('render overrides apply to every eyewear mesh, overlays included, and restore everything afterwards', async t => {
  const {instance, draws} = await configured(build());
  t.after(() => instance.dispose());
  reveal(instance); instance.pose(frame, detection(), 100);
  instance.render(null);
  const beauty = draws.at(-1)!;
  const before = new Map(beauty.meshes.map(mesh => [mesh.name, mesh.material]));
  const restored = () => {
    const after = draws.length; instance.render(null);
    for (const mesh of draws[after]!.meshes) assert.equal(mesh.material, before.get(mesh.name), `${mesh.name} material restored`);
  };
  // part-id: lenses magenta, every other part its own colour, overlays hidden, black background, a palette.
  const timing = instance.render(null, {override: 'part-id'});
  const partId = draws.at(-1)!;
  assert.ok(partId.background instanceof Color && (partId.background as Color).getHex() === 0);
  const colours = new Map<string, number>();
  for (const mesh of partId.meshes) {
    if (mesh.name.endsWith('near-temple overlay')) {assert.equal(mesh.visible, false, `${mesh.name} hidden`); continue;}
    assert.ok(mesh.material instanceof MeshBasicMaterial, mesh.name);
    colours.set(mesh.name, (mesh.material as MeshBasicMaterial).color.getHex());
  }
  assert.equal(colours.get('Lens_L'), 0xff00ff); assert.equal(colours.get('Lens_R'), 0xff00ff);
  const frameColours = [...colours].filter(([name]) => !name.startsWith('Lens')).map(([, hex]) => hex);
  assert.equal(new Set(frameColours).size, frameColours.length, 'one colour per non-lens part');
  assert.ok(!frameColours.includes(0xff00ff) && !frameColours.includes(0));
  assert.equal(timing.override, 'part-id');
  assert.deepEqual(timing.palette!.filter(entry => entry.class === 'lens').map(entry => entry.rgb), [[...LENS_ID_RGB], [...LENS_ID_RGB]]);
  assert.equal(timing.palette!.length, 7); assert.equal(timing.guarded, false); assert.equal(timing.hairApplied, false);
  restored();
  // lens-mask: lenses white, the rest black.
  instance.render(null, {override: 'lens-mask'});
  for (const mesh of draws.at(-1)!.meshes) {
    if (!mesh.visible) continue;
    assert.equal((mesh.material as MeshBasicMaterial).color.getHex(), mesh.name.startsWith('Lens') ? 0xffffff : 0, mesh.name);
  }
  restored();
  instance.render(null, {override: 'normals'});
  assert.ok(draws.at(-1)!.meshes.filter(mesh => mesh.visible).every(mesh => mesh.material instanceof MeshNormalMaterial));
  restored();
  // lens-hidden / lens-only: the beauty materials, with a visibility filter.
  instance.render(null, {override: 'lens-hidden'});
  for (const mesh of draws.at(-1)!.meshes) if (mesh.name.startsWith('Lens')) assert.equal(mesh.visible, false, `${mesh.name} hidden`);
  assert.ok(draws.at(-1)!.meshes.some(mesh => mesh.name === 'Bridge' && mesh.visible && mesh.material === before.get('Bridge')));
  instance.render(null, {override: 'lens-only'});
  for (const mesh of draws.at(-1)!.meshes) assert.equal(mesh.visible, mesh.name.startsWith('Lens'), mesh.name);
  restored();
  assert.throws(() => instance.render(null, {override: 'x-ray' as never}), /Unknown render override "x-ray"/);
});

test('setLighting swaps the environment maps (scene, lenses with lensenv, crystal and its twins) and null restores them', async t => {
  const {instance, internal} = await configured(r9Like(), {lensEnvIntensity: 1.5});
  t.after(() => instance.dispose());
  reveal(instance); instance.pose(frame, detection(), 100); instance.render(null);   // the twins exist after the first draw
  const scene = internal.scene, lens = (instance as unknown as {lensMeshes: Mesh[]}).lensMeshes[0]!.material as MeshPhysicalMaterial;
  const twins = [...(instance as unknown as {translucentTwins: Map<MeshPhysicalMaterial, MeshPhysicalMaterial>}).translucentTwins];
  const before = {environment: scene.environment, intensity: scene.environmentIntensity, lens: lens.envMap, lensIntensity: lens.envMapIntensity,
    crystal: twins.map(([original, twin]) => [original.envMap, twin.envMap])};
  assert.ok(before.lens && isOpticalMaterial(lens));
  instance.setLighting({preset: 'side_studio', intensity: 0.8});
  assert.notEqual(scene.environment, before.environment); assert.equal(scene.environmentIntensity, 0.8);
  assert.notEqual(lens.envMap, before.lens); assert.ok(Math.abs(lens.envMapIntensity - 0.8 * 1.5) < 1e-9, 'lensenv scales the new map too');
  for (const [original, twin] of twins) {assert.equal(original.envMap, twin.envMap); assert.notEqual(original.envMap, scene.environment);}
  instance.setLighting({preset: 'room', intensity: 1.2});
  assert.equal(scene.environmentIntensity, 1.2);
  instance.setLighting(null);
  assert.equal(scene.environment, before.environment); assert.equal(scene.environmentIntensity, before.intensity);
  assert.equal(lens.envMap, before.lens); assert.equal(lens.envMapIntensity, before.lensIntensity);
  assert.deepEqual(twins.map(([original, twin]) => [original.envMap, twin.envMap]), before.crystal);
  assert.throws(() => instance.setLighting({preset: 'disco' as never, intensity: 1}), /Unknown lighting preset/);
  assert.throws(() => instance.setLighting({preset: 'room', intensity: 9}), /intensity/);
});

test('a protection that cannot be established is reported once per reason, as a render-time fallback', async t => {
  const {instance} = await configured(build());
  t.after(() => instance.dispose());
  reveal(instance);
  // An empty optical box cannot be projected: the protection is withheld for these frames (forced here; a real cause is a
  // pose whose bounds cross the near plane).
  const internals = instance as unknown as {rearDrop: object};
  internals.rearDrop = new Proxy(internals.rearDrop, {get: (target, key) => key === 'opticalBounds' ? new Box3() : Reflect.get(target, key)});
  instance.pose(frame, detection(), 100); assert.equal(instance.render(null).safeFallback, true);
  instance.pose(frame, detection(), 133); instance.render(null);
  const protection = instance.report().fallbacks.filter(f => f.subsystem === 'protection');
  assert.equal(protection.length, 1, 'once per reason'); assert.equal(protection[0]!.stage, 'render');
  assert.match(protection[0]!.detail, /could not be established for a frame \(The projected temple bounds are invalid\.\); such frames draw without hair/);
});

test('every default part keeps its own decided class through the renderer (no re-derivation from transmission)', async t => {
  const parts = defaultParts();
  const {instance} = await configured(build({parts}));
  t.after(() => instance.dispose());
  const lens = instance.parts().filter(part => part.class === 'lens').map(part => part.name);
  assert.deepEqual(lens, ['Lens_L', 'Lens_R']);
});
