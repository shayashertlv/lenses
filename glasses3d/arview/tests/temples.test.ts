/** Temples by position per side, the clip derived from them, and per-side continuity (WP1 stage D2, glasses3d/plan/archive/20261019/WP1_DESIGN.md §3):
 *  crystal and alpha-blended temples are temples like opaque ones, a side without a temple keeps a fixed cap (reported),
 *  hinge hardware alone is never a temple, and the clip lies just inside the shorter arm. Hand-built scenes, classified
 *  from their userData roles; tests/renderer-api.test.ts covers the same rules through the renderer on GLB bytes. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, Group, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, PerspectiveCamera, PlaneGeometry, Scene, ShaderLib,
  UniformsUtils} from 'three';
import type {Material, WebGLRenderer} from 'three';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';
import {TEMPLE_CLIP_FLOOR_LOCAL_Z_M} from '../src/eyewear/catalog.ts';
import {deriveTempleClip, templeInventory, templeMeasurements, TEMPLE_TIP_GUARD_M} from '../src/render/temple-geometry.ts';
import type {TempleSide} from '../src/render/temple-geometry.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {buildTempleContinuityModel, projectTempleContinuity} from '../src/render/continuity.ts';
import {createTempleVisibility} from '../src/render/temple-visibility.ts';
import {createTempleTerminalFitEvaluator} from '../src/render/temple-terminal-fit.ts';
import {TempleEndpointTracker} from '../src/render/temple-endpoint.ts';
import {GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';

type CompileInput = Parameters<Material['onBeforeCompile']>[0];
function compile(material: Material) {
  const physical = ShaderLib.physical!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader'> = {
    uniforms: UniformsUtils.clone(physical.uniforms), vertexShader: physical.vertexShader, fragmentShader: physical.fragmentShader,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, {}]);
  return shader;
}

/** A lens sheet, a compact front and two arms (z -5 .. -155 mm at |x| 63-67 mm) of the given material, with roles. */
function asset(armMaterial: Material, {left = true, right = true, leftTip = -.155, rightTip = -.155} = {}) {
  const lensMaterial = new MeshPhysicalMaterial({transmission: 1}), frame = new MeshStandardMaterial();
  const lens = new Mesh(new PlaneGeometry(.05, .04).translate(0, 0, -.004), lensMaterial); lens.userData.partRole = 'lens';
  const front = new Mesh(new BoxGeometry(.13, .04, .006).translate(0, 0, -.003), frame); front.userData.partRole = 'frame';
  const root = new Group().add(lens, front);
  const arms: Mesh[] = [];
  for (const [sign, wanted, tip] of [[-1, left, leftTip], [1, right, rightTip]] as const) {
    if (!wanted) continue;
    const arm = new Mesh(new BoxGeometry(.004, .004, -.005 - tip, 1, 1, 16).translate(sign * .065, 0, (tip - .005) / 2), armMaterial);
    arm.userData.partRole = 'temple'; arm.name = sign < 0 ? 'arm left' : 'arm right';
    root.add(arm); arms.push(arm);
  }
  const dispose = () => {root.traverse(object => {if (object instanceof Mesh) object.geometry.dispose();}); lensMaterial.dispose(); frame.dispose(); armMaterial.dispose();};
  return {root, lens, front, arms, dispose};
}
const minimalRenderer = {capabilities: {samples: 4}} as unknown as WebGLRenderer;

for (const [kind, make] of [
  ['crystal (transmissive, non-lens)', () => new MeshPhysicalMaterial({transmission: 1, thickness: .004})],
  ['alpha-blended', () => new MeshStandardMaterial({transparent: true, opacity: .45, depthWrite: false})],
] as const) test(`${kind} temples are temples by position: rear drop, continuity, terminal return and visibility all accept them`, t => {
  const f = asset(make()); t.after(f.dispose);
  classifyAssetMaterials(f.root);
  const inventory = templeInventory(f.root);
  assert.deepEqual(inventory.present, [true, true]);
  assert.deepEqual(inventory.sides.map(side => [side.source, side.tipZM !== null && Math.abs(side.tipZM + .155) < 1e-6, side.missing]),
    [['position', true, null], ['position', true, null]]);
  assert.ok(Math.abs(inventory.lensRearZM! + .004) < 1e-9, 'the lens rear comes from lens-class geometry');
  assert.deepEqual(inventory.warnings, []);
  const clip = deriveTempleClip(inventory);
  assert.equal(clip.clipZM, TEMPLE_CLIP_FLOOR_LOCAL_Z_M, 'long arms end at the floor');
  // Rear drop: before stage D2 an alpha-blended arm was 'not opaque', so the asset refused ('no eligible posterior opaque shaft').
  const rear = createRearDrop(f.root, clip.clipZM); t.after(() => rear.dispose());
  const before = f.arms.map(arm => arm.geometry.getAttribute('position').array.slice());
  rear.setShape(0, .018);
  assert.deepEqual(rear.diagnostics.armSides, [true, true]);
  assert.ok(rear.diagnostics.affectedVertexCountBySide.every(count => count > 0));
  for (const [index, arm] of f.arms.entries()) assert.notDeepEqual(arm.geometry.getAttribute('position').array, before[index], `${arm.name} is spread`);
  rear.setShape(0, 0);
  const continuity = buildTempleContinuityModel(f.root, clip.clipZM);
  assert.deepEqual(continuity.sides.map(side => side.length), [33, 33]); assert.deepEqual(continuity.missing, []);
  const evaluator = createTempleTerminalFitEvaluator(f.root, {offsetCm: GLASSES_OFFSET_CM, spreadM: .018, spreadStartZM: rear.spreadStartZM,
    modelCutoffZM: clip.clipZM, maximumZM: clip.clipZM + .025});
  assert.equal(evaluator.unavailable, null, 'both arms are sampled in the terminal band');
  // Temple visibility treats every non-lens material as frame: the arm material gets the cheek / lens-input wrapper.
  const scene = new Scene(), eyewearPose = new Group().add(f.root); scene.add(eyewearPose);
  const visibility = createTempleVisibility(f.root, {renderer: minimalRenderer, scene, camera: new PerspectiveCamera(60, 1, 1, 1000), eyewearPose});
  t.after(() => visibility.dispose());
  const armMaterial = f.arms[0]!.material as Material;
  assert.ok(compile(armMaterial).uniforms.templeCheekCount, 'the arm material carries the observed-cheek and lens-input wrapper');
  const overlays = f.root.children.filter((child): child is Mesh => child instanceof Mesh && child.userData.templeVisibilityOverlay === true
    && f.arms.some(arm => arm.geometry === child.geometry));
  assert.equal(overlays.length, 2, 'each arm has a near-arm overlay');
  // A blended overlay is not built: an alpha-blended arm's overlay stays the hidden placeholder (it would draw opaque).
  for (const overlay of overlays) assert.equal((overlay.material as Material).visible, !armMaterial.transparent);
});

test('a one-sided model: the missing side is reported and fixed; the present arm keeps its shape, path and return', t => {
  const f = asset(new MeshStandardMaterial(), {right: false}); t.after(f.dispose);
  classifyAssetMaterials(f.root);
  const inventory = templeInventory(f.root);
  assert.deepEqual(inventory.present, [true, false]);
  assert.equal(inventory.sides[1].missing, 'the non-lens geometry at x > +45 mm reaches only z -6.0 mm (a temple reaches z <= -60.0 mm; '
    + 'hinge hardware alone is not a temple)');
  const clip = deriveTempleClip(inventory);
  assert.deepEqual([clip.source, clip.clipZM, clip.shorterSide], ['derived', TEMPLE_CLIP_FLOOR_LOCAL_Z_M, null]);
  const rear = createRearDrop(f.root, clip.clipZM); t.after(() => rear.dispose());
  const front = f.front.geometry.getAttribute('position').array.slice();
  rear.setShape(0, .018);
  assert.deepEqual(rear.diagnostics.affectedVertexCountBySide.map(count => count > 0), [true, false]);
  assert.deepEqual(f.front.geometry.getAttribute('position').array, front, 'the temple-less side of the front is never bent');
  assert.equal(rear.originalArmBounds.length, 1);
  const continuity = buildTempleContinuityModel(f.root, clip.clipZM);
  assert.deepEqual(continuity.sides.map(side => side.length), [33, 0]);
  assert.deepEqual(continuity.missing, [{side: 1, reason: 'no temple on the left side'}]);
  const pose = new Matrix4().makeTranslation(0, 0, -45).toArray();
  const paths = projectTempleContinuity(continuity, {eyewearMatrix: pose, offsetCm: GLASSES_OFFSET_CM, sourceAspect: 4 / 3, width: 640, height: 480, dropM: 0});
  assert.deepEqual(paths?.map(path => path.side), [0], 'one projected path, for the arm that exists');
  // The hair endpoint tracker runs with one empty side: that side stays at the fixed cap ('fallback').
  const maximum = Math.max(clip.clipZM, Math.min(-.055, clip.clipZM + .025, continuity.startZM - .008));
  const tracker = new TempleEndpointTracker(continuity, maximum);
  const mask = {width: 64, height: 48, category: new Uint8Array(64 * 48).fill(1), hairIndex: 1};
  const report = tracker.update({paths, mask, render: {width: 640, height: 480}, timestampMs: 100, yawDegrees: 0, pitchDegrees: 0});
  assert.deepEqual([report.positiveZM, report.positiveState], [maximum, 'fallback']);
  // The terminal return: only the present arm is sampled; the missing side never makes it unavailable.
  const input = {offsetCm: GLASSES_OFFSET_CM, spreadM: .018, spreadStartZM: rear.spreadStartZM, modelCutoffZM: clip.clipZM, maximumZM: maximum};
  assert.match(createTempleTerminalFitEvaluator(f.root, input).unavailable ?? '', /the wearer's left \(x > 0\) arm has no geometry in the terminal band/,
    'assuming both sides, the missing arm is named');
  const oneSided = createTempleTerminalFitEvaluator(f.root, {...input, sides: inventory.present});
  assert.equal(oneSided.unavailable, null);
  const fit = oneSided(1);
  assert.ok(fit && fit.negativeInsetM > 0 && fit.positiveInsetM < 1e-5, JSON.stringify(fit));
});

test('hinge hardware alone is not a temple: both sides missing, nothing bends, continuity has no arm, the clip is the default', t => {
  const hinge = new MeshStandardMaterial({metalness: 1});
  const f = asset(new MeshStandardMaterial(), {left: false, right: false}); t.after(f.dispose); t.after(() => hinge.dispose());
  for (const sign of [-1, 1]) {
    const knuckle = new Mesh(new BoxGeometry(.012, .006, .035).translate(sign * .064, 0, -.0225), hinge); knuckle.userData.partRole = 'hardware';
    f.root.add(knuckle);
  }
  classifyAssetMaterials(f.root);
  const inventory = templeInventory(f.root);
  assert.deepEqual(inventory.present, [false, false]);
  for (const side of inventory.sides) assert.match(side.missing!, /reaches only z -40\.0 mm .*hinge hardware alone is not a temple/);
  const clip = deriveTempleClip(inventory);
  assert.deepEqual([clip.source, clip.clipZM, clip.derivedZM], ['default', TEMPLE_CLIP_FLOOR_LOCAL_Z_M, null]);
  assert.match(clip.warnings.join(), /no temple on either side, so no clip can be derived/);
  // Before stage D2 this threw 'The asset has no eligible posterior opaque shaft.'
  const rear = createRearDrop(f.root, clip.clipZM); t.after(() => rear.dispose());
  assert.equal(rear.diagnostics.affectedVertexCount, 0); assert.deepEqual(rear.originalArmBounds, []);
  assert.ok(rear.opticalBounds.min.z < -.0399, 'everything, the hinges included, is protected');
  assert.throws(() => buildTempleContinuityModel(f.root, clip.clipZM), /No temple on either side: there is no arm to follow/);
  const measured = templeMeasurements(inventory, clip);
  assert.deepEqual([(measured.temples.left as {present: boolean}).present, measured.temple_clip.source], [false, 'default']);
});

test('the clip is derived per side from the shorter arm, floored at -0.14 m and clamped to the drawable range', () => {
  const side = (name: 'left' | 'right', tipZM: number | null, present = tipZM !== null && tipZM <= -.06): TempleSide =>
    ({side: name, present, source: 'position', tipZM, rootZM: null, vertexCount: 0, parts: [], missing: present ? null : 'x'});
  const short = deriveTempleClip({sides: [side('left', -.16), side('right', -.12)]});
  assert.deepEqual([short.source, short.shorterSide], ['derived', 'right']);
  assert.ok(Math.abs(short.clipZM - (-.12 + TEMPLE_TIP_GUARD_M)) < 1e-12, 'just inside the shorter arm, never a single global minimum');
  const long = deriveTempleClip({sides: [side('left', -.17), side('right', -.16)]});
  assert.deepEqual([long.clipZM, long.shorterSide], [TEMPLE_CLIP_FLOOR_LOCAL_Z_M, null]);
  assert.match(long.warnings.join(), /authored temple tips differ by 10.0 mm/);
  const stub = deriveTempleClip({sides: [side('left', -.16), side('right', -.07)]});
  assert.ok(Math.abs(stub.clipZM - (-.07 + TEMPLE_TIP_GUARD_M)) < 1e-12 && stub.clipZM <= -.045);
  // A tip nearer than the drawable range (only possible for a side forced present) is clamped, with a warning.
  const clamped = deriveTempleClip({sides: [side('left', -.03, true), side('right', null)]});
  assert.equal(clamped.clipZM, -.045); assert.match(clamped.warnings.join(), /outside the drawable range .* clamped to -0\.045 m/);
  // A usable handover is used as given, and cross-checked against the tips.
  const handed = deriveTempleClip({sides: [side('left', -.16), side('right', -.12)]}, -.13);
  assert.deepEqual([handed.source, handed.clipZM, handed.handoverZM], ['handover', -.13, -.13]);
  assert.equal(handed.warnings.length, 2);
  assert.match(handed.warnings.join(), /handed-over temple clip -0\.1300 m is at or behind the right temple's tip \(z -120\.0 mm\): that arm has no cross-section/);
  assert.match(deriveTempleClip({sides: [side('left', -.16), side('right', -.15)]}, -.14).warnings.join(), /authored temple tips differ/);
  // A handover outside [-0.2, -0.045] m is ignored with a warning; the derived clip is used.
  for (const bad of [-.03, -.25, Number.NaN]) {
    const ignored = deriveTempleClip({sides: [side('left', -.16), side('right', -.12)]}, bad);
    assert.equal(ignored.source, 'derived'); assert.equal(ignored.handoverZM, null);
    assert.match(ignored.warnings.join(), /handed-over temple clip .* is outside the drawable range \[-0\.200, -0\.045\] m and was ignored; the clip derived from the temples/);
  }
});

test('lens-class geometry behind -60 mm is a warning: lens material reaching the temple region', t => {
  const f = asset(new MeshStandardMaterial()); t.after(f.dispose);
  f.lens.geometry.translate(0, 0, -.07);
  classifyAssetMaterials(f.root);
  const inventory = templeInventory(f.root);
  assert.ok(Math.abs(inventory.lensRearZM! + .074) < 1e-9);
  assert.match(inventory.warnings.join(), /lens-class geometry reaches z -74\.0 mm, behind -60\.0 mm: lens material reaches the temple region/);
  assert.deepEqual(inventory.present, [true, true], 'presence is the §2 rule, independent of the lens rear');
});

test('authored temple regions decide the temples, cross-checked against position', t => {
  const f = asset(new MeshStandardMaterial()); t.after(f.dispose);
  const profile = (mesh: Mesh, region: string) => {mesh.userData.LENSES_part_visibility = {version: 1, region};};
  profile(f.lens, 'lens'); profile(f.front, 'front'); profile(f.arms[0]!, 'temple_left'); profile(f.arms[1]!, 'temple_right');
  classifyAssetMaterials(f.root);
  const inventory = templeInventory(f.root);
  assert.equal(inventory.profiled, true);
  assert.deepEqual(inventory.sides.map(side => [side.source, side.present, side.parts]), [['profile', true, ['arm left']], ['profile', true, ['arm right']]]);
  // A right arm authored as front: the region decides (no temple on the right), and the disagreement is a warning.
  const g = asset(new MeshStandardMaterial()); t.after(g.dispose);
  profile(g.lens, 'lens'); profile(g.front, 'front'); profile(g.arms[0]!, 'temple_left'); profile(g.arms[1]!, 'front');
  classifyAssetMaterials(g.root);
  const mixed = templeInventory(g.root);
  assert.deepEqual(mixed.present, [true, false]);
  assert.match(mixed.warnings.join(), /temple left: the authored temple_right region says missing but position says present; the authored region decides/);
});
