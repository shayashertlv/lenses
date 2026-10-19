/** The positional path draws a front as front (WP1 review E/F, major finding on temple-geometry.ts).
 *
 *  With no LENSES_part_visibility, the temples are the non-lens geometry at |x| > 45 mm behind the lens rear
 *  (glasses3d/plan/archive/20261019/WP1_DESIGN.md §3). The Prada PR17WS stripped of its metadata has ONE front mesh (z -9.9..3.9 mm) whose outer back lies
 *  behind the -4.6 mm lens rear at both endpieces, and its material is shared with both temples. The front got the arm
 *  treatment through that shared material: the lens-input exclusion removed its cream inner bevel from the image seen
 *  through the lenses (4,562 px, up to 146 levels in the portrait), while the part-id images stayed byte-identical.
 *  Now a compact frame mesh reaching the central band |x| <= 45 mm is front (isPositionalFrontFrame): it gets its own
 *  material instance and no near-temple overlay, the temple inventory leaves it out of the arm parts, and a mesh that
 *  holds front AND temple keeps the positional rule with a warning. qa/capture-positional-parity.test.mjs renders the
 *  real Prada pair on the GPU. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, PerspectiveCamera, Scene, ShaderLib, UniformsUtils} from 'three';
import type {BufferGeometry, Material, WebGLRenderer} from 'three';
import {mergeGeometries} from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import {classifyAssetMaterials, isFrontFramePart, isPositionalFrontFrame} from '../src/eyewear/optical-material.ts';
import {PART_VISIBILITY_KEY} from '../src/eyewear/part-visibility.ts';
import {createTempleVisibility} from '../src/render/temple-visibility.ts';
import {templeInventory} from '../src/render/temple-geometry.ts';

type CompileInput = Parameters<Material['onBeforeCompile']>[0];
function compile(material: Material) {
  const physical = ShaderLib.physical!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader'> = {
    uniforms: UniformsUtils.clone(physical.uniforms), vertexShader: physical.vertexShader, fragmentShader: physical.fragmentShader,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, {}]);
  return shader;
}
const boxMm = (x0: number, x1: number, y0: number, y1: number, z0: number, z1: number): BufferGeometry =>
  new BoxGeometry((x1 - x0) / 1000, (y1 - y0) / 1000, (z1 - z0) / 1000).translate((x0 + x1) / 2000, (y0 + y1) / 2000, (z0 + z1) / 2000);

/** A Prada-like asset with no metadata: a front box crossing x = 0 whose outer back lies behind the lens rear, two
 *  temples sharing the front's material, a one-sided hinge leaf, and two transmissive lenses (rule 3, warned). */
function pradaLike({profiles = false, fused = false} = {}) {
  const acetate = new MeshStandardMaterial({name: 'Material_0', color: 0xf2ece0});
  const gold = new MeshStandardMaterial({name: 'Material_1', color: 0xd8c28a});
  const glass = new MeshPhysicalMaterial({name: 'Material_2', transmission: 1});
  const lensL = new Mesh(boxMm(-59, -10, -22, 13, -4.6, 1.9), glass), lensR = new Mesh(boxMm(10, 59, -22, 13, -4.6, 1.9), glass);
  const frontGeometry = boxMm(-71, 71, -24, 18, -9.9, 3.9);
  const templeL = boxMm(-71.4, -47, -10, 10, -147, -10.6), templeR = boxMm(47, 71.4, -10, 10, -147, -10.6);
  const parts: Mesh[] = [];
  if (fused) parts.push(new Mesh(mergeGeometries([frontGeometry, templeL, templeR])!, acetate));
  else parts.push(new Mesh(frontGeometry, acetate), new Mesh(templeL, acetate), new Mesh(templeR, acetate));
  const hinge = new Mesh(boxMm(-65.7, -63.5, 0, 4, -11.2, -9), gold);
  parts.push(hinge);
  parts.forEach((mesh, index) => {mesh.name = `Object_${index}`;});
  lensL.name = 'Object_L'; lensR.name = 'Object_R';
  if (profiles) {
    const region = (mesh: Mesh, value: string) => {mesh.userData[PART_VISIBILITY_KEY] = {version: 1, region: value};};
    region(lensL, 'lens'); region(lensR, 'lens'); region(parts[0]!, 'front'); region(parts[1]!, 'temple_left');
    region(parts[2]!, 'temple_right'); region(hinge, 'temple_left');
  }
  const root = new Group().add(lensL, lensR, ...parts);
  const dispose = () => {
    root.traverse(object => {if (object instanceof Mesh) object.geometry.dispose();});
    for (const material of new Set<Material>([acetate, gold, glass])) material.dispose();
  };
  return {root, acetate, glass, lensL, lensR, parts, hinge, dispose};
}
function visibilityOf(root: Group) {
  const renderer = {capabilities: {samples: 4}} as unknown as WebGLRenderer;
  const scene = new Scene(), eyewearPose = new Group().add(root), camera = new PerspectiveCamera(60, 16 / 9, 1, 1000);
  scene.add(eyewearPose);
  return createTempleVisibility(root, {renderer, scene, camera, eyewearPose});
}
const overlaySources = (root: Group): BufferGeometry[] => root.children
  .filter((child): child is Mesh => child instanceof Mesh && child.userData.templeVisibilityOverlay === true).map(overlay => overlay.geometry);

test('without metadata a compact front crossing x = 0 is front: its own material, no arm shader, no overlay, not a temple part', t => {
  const f = pradaLike(); t.after(f.dispose);
  const [front, left, right] = f.parts as [Mesh, Mesh, Mesh];
  assert.equal(isPositionalFrontFrame(front), false, 'an unclassified mesh is never a positional front');
  const classification = classifyAssetMaterials(f.root);
  assert.ok(classification.lensIdentification.last_resort_used, 'the lenses are found by transmission alone (rule 3)');
  assert.equal(isPositionalFrontFrame(front), true);
  assert.equal(isFrontFramePart(front, f.root), true);
  for (const mesh of [left, right, f.hinge]) assert.equal(isPositionalFrontFrame(mesh), false, `${mesh.name} lies at |x| > 45 mm: arm geometry`);
  assert.equal(front.material, f.acetate, 'the front keeps the original (front comes before arm)');
  assert.notEqual(left.material, f.acetate, 'the temples get their own instance of the shared acetate');
  assert.equal(left.material, right.material);
  assert.deepEqual(classification.sharedMaterials, [f.acetate]);
  const visibility = visibilityOf(f.root); t.after(() => visibility.dispose());
  const sources = overlaySources(f.root);
  assert.ok(!sources.includes(front.geometry), 'the front gets no near-temple overlay');
  for (const mesh of [left, right, f.hinge]) assert.ok(sources.includes(mesh.geometry), `${mesh.name} keeps its near-temple overlay`);
  assert.equal(compile(front.material as Material).uniforms.templeCheekCount, undefined,
    'the front keeps its inner rim in the lens input (no lens-input exclusion, no observed-cheek blend)');
  assert.ok(compile(left.material as Material).uniforms.templeCheekCount, 'the temple material carries the arm shader');
  const inventory = templeInventory(f.root);
  assert.equal(inventory.profiled, false);
  assert.deepEqual(inventory.sides.map(side => side.parts), [['Object_1', 'Object_3'], ['Object_2']], 'the front is not a temple part');
  assert.deepEqual(inventory.present, [true, true]);
  assert.ok(Math.abs(inventory.sides[0].rootZM! + .009) < 1e-6, 'the arm root is the hinge leaf, not the front behind the lens rear');
  assert.deepEqual(inventory.warnings, []);
});

test('the same frame with authored regions draws the same parts as arms (the stripped copy matches its source)', t => {
  const authored = pradaLike({profiles: true}), stripped = pradaLike(); t.after(authored.dispose); t.after(stripped.dispose);
  classifyAssetMaterials(authored.root); classifyAssetMaterials(stripped.root);
  for (let i = 0; i < authored.parts.length; i++) {
    assert.equal(isFrontFramePart(stripped.parts[i]!, stripped.root), isFrontFramePart(authored.parts[i]!, authored.root),
      `${stripped.parts[i]!.name}: front by position exactly when front by its region`);
  }
  const authoredVisibility = visibilityOf(authored.root), strippedVisibility = visibilityOf(stripped.root);
  t.after(() => {authoredVisibility.dispose(); strippedVisibility.dispose();});
  const index = (f: ReturnType<typeof pradaLike>) => overlaySources(f.root).map(geometry => f.parts.findIndex(mesh => mesh.geometry === geometry)).sort();
  assert.deepEqual(index(stripped), index(authored), 'the same parts get near-temple overlays');
  assert.equal(isPositionalFrontFrame(authored.parts[0]!), false, 'a profiled mesh is decided by its region, never by position');
});

test('a mesh holding front AND temples keeps the positional arm rule and is a warning', t => {
  const f = pradaLike({fused: true}); t.after(f.dispose);
  classifyAssetMaterials(f.root);
  const fused = f.parts[0]!;
  assert.equal(isPositionalFrontFrame(fused), false, 'it reaches z -147 mm: not a compact front');
  const visibility = visibilityOf(f.root); t.after(() => visibility.dispose());
  assert.ok(overlaySources(f.root).includes(fused.geometry), 'its arms keep the near-temple overlay');
  const inventory = templeInventory(f.root);
  assert.equal(inventory.warnings.length, 1, inventory.warnings.join(' | '));
  assert.match(inventory.warnings[0]!, /^temple part "Object_0" crosses x = 0 and reaches back to z -147\.0 mm: front and temple are one mesh/);
  assert.match(inventory.warnings[0]!, /split the front from the temples or author LENSES_part_visibility$/);
});

test('an explicit non-frame role bars the positional front; a frame role on a compact front is the authored front', t => {
  const f = pradaLike(); t.after(f.dispose);
  const front = f.parts[0]!;
  front.userData.partRole = 'temple';
  classifyAssetMaterials(f.root);
  assert.equal(isPositionalFrontFrame(front), false, 'the author said temple');
  const g = pradaLike(); t.after(g.dispose);
  g.parts[0]!.userData.partRole = 'frame';
  classifyAssetMaterials(g.root);
  assert.equal(isPositionalFrontFrame(g.parts[0]!), true);
  assert.equal(isFrontFramePart(g.parts[0]!, g.root), true);
});
