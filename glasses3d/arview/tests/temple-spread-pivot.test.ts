/** The arm-spread pivot of a TALL temple (WP1 proof, prada-default): the thin-shaft boundary is the hinge only inside
 *  the front (in front of the proximal guard, 15 mm behind the lens rear). A shield or wide acetate arm collapses to a
 *  bar only far down the arm (the Prada PR17WS at z -73 mm); pivoting there left the front half of the arm unspread,
 *  inside the head proxy, so every turned capture drew the arm only to the shield. The arm now pivots at the guard plane
 *  and lies outside the head proxy (the canonical face rim joined to the posterior volume, temple-head-shell.ts) from
 *  the hinge to where its fixed return behind the ear begins. Thin-shaft frames keep their measured hinge. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {readFile} from 'node:fs/promises';
import {BoxGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, PlaneGeometry, Texture, Vector3} from 'three';
import type {BufferGeometry, Object3D} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';
import {GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';
import {createRearDrop, REAR_DROP_PARAMETERS} from '../src/render/rear-drop.ts';
import {deriveTempleClip, templeInventory} from '../src/render/temple-geometry.ts';
import {createTempleTerminalFitEvaluator, terminalFitX} from '../src/render/temple-terminal-fit.ts';
import {createTempleHeadShell} from '../src/render/temple-head-shell.ts';
import {spreadArmX} from '../src/render/face-width.ts';
import {publicFile} from './shipped-models.ts';

const SPREAD_M = .018;   // renderer.ts ARM_SPREAD_M
const HIDDEN_TAIL_M = .025;   // renderer.ts: the buried cap sits 25 mm in front of the clip

/** A lens sheet, a compact front and two arms at |x| `inner`..`inner + 5 mm`: `tall` metres high from the hinge back to
 *  `collapseZM`, then a 6 mm shaft to z -155 mm. */
function asset(tall: number, collapseZM: number, inner = .061) {
  const lensMaterial = new MeshPhysicalMaterial({transmission: 1}), frame = new MeshStandardMaterial();
  const lens = new Mesh(new PlaneGeometry(.05, .04).translate(0, 0, -.004), lensMaterial); lens.userData.partRole = 'lens';
  const front = new Mesh(new BoxGeometry(.13, .04, .006).translate(0, 0, -.003), frame); front.userData.partRole = 'frame';
  const root = new Group().add(lens, front);
  for (const sign of [-1, 1]) {
    const x = sign * (inner + .0025);
    const shield = new Mesh(new BoxGeometry(.005, tall, -.005 - collapseZM, 1, 1, 24).translate(x, .002, (collapseZM - .005) / 2), frame);
    const shaft = new Mesh(new BoxGeometry(.005, .006, collapseZM + .155, 1, 1, 24).translate(x, .005, (collapseZM - .155) / 2), frame);
    for (const arm of [shield, shaft]) {arm.userData.partRole = 'temple'; root.add(arm);}
  }
  const dispose = () => {root.traverse(object => {if (object instanceof Mesh) object.geometry.dispose();}); lensMaterial.dispose(); frame.dispose();};
  return {root, dispose};
}

test('a tall temple pivots at the proximal guard, not where its height finally collapses; a thin shaft keeps its hinge', t => {
  const tall = asset(.033, -.073); t.after(tall.dispose);
  classifyAssetMaterials(tall.root);
  const rear = createRearDrop(tall.root, -.14); t.after(() => rear.dispose());
  const guard = -.004 - REAR_DROP_PARAMETERS.proximalGuardM;
  // The first populated thin slice behind the shield (the shaft's first vertex ring lies 3.4 mm behind -73 mm).
  assert.ok(rear.shaftBoundaryZM !== null && rear.shaftBoundaryZM <= -.073 && rear.shaftBoundaryZM >= -.078, `the arm collapses at ${rear.shaftBoundaryZM}`);
  assert.equal(rear.hingeZM, null, 'a boundary behind the guard is not a hinge');
  assert.ok(Math.abs(rear.spreadStartZM - guard) < 1e-9, `the spread pivots at the guard plane (${rear.spreadStartZM})`);
  assert.equal(rear.diagnostics.shaftBoundaryZM, rear.shaftBoundaryZM);
  // The arm opens from the guard plane: every arm vertex between the guard and the old pivot moves outward now (with the
  // old pivot at -73 mm none of them moved).
  const before = new Map<BufferGeometry, Float32Array>();
  tall.root.traverse(object => {if (object instanceof Mesh && object.userData.partRole === 'temple') before.set(object.geometry, object.geometry.getAttribute('position').array.slice() as Float32Array);});
  rear.setShape(0, SPREAD_M);
  let moved = 0, between = 0;
  tall.root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.partRole !== 'temple') return;
    // createRearDrop already put the arm's clone on the mesh; setShape rewrites that clone in place.
    const original = before.get(object.geometry)!;
    const position = object.geometry.getAttribute('position');
    for (let i = 0; i < position.count; i++) {
      const z = original[i * 3 + 2]!;
      if (!(z < guard - .006 && z > -.072)) continue;
      between++;
      if (Math.abs(position.getX(i)) > Math.abs(original[i * 3]!) + 1e-6) moved++;
    }
  });
  assert.ok(between > 0 && moved === between, `${moved} of ${between} arm vertices between the guard and the old pivot are spread`);

  // A thin shaft (6 mm) from the hinge back: the measured boundary is in the front and stays the hinge.
  const thin = asset(.006, -.020); t.after(thin.dispose);
  classifyAssetMaterials(thin.root);
  const control = createRearDrop(thin.root, -.14); t.after(() => control.dispose());
  assert.ok(control.hingeZM !== null && control.hingeZM > guard, `the thin arm's hinge ${control.hingeZM} is in front of the guard`);
  assert.equal(control.hingeZM, control.shaftBoundaryZM);
  assert.equal(control.spreadStartZM, control.hingeZM);
});

/* The owner's proof files (PLAN.md WP1.6), read-only from outside arview; skipped where this machine lacks them. */
const LENSES = new URL('../../../', import.meta.url);
const PROOF = {
  prada: new URL('automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb', LENSES),
  ft1119: new URL('automation/data/blender_agent/studio/jobs/3b8a1c7ae8114868b49b298b681f1b29/original/model.glb', LENSES),
  r9: new URL('ar_v4/model_studio/data/jobs/d92b404c-f2c7-483e-ae9c-aa8bcf1b870c/agent/operations/8c315e48f6b84cbc897cf397d772d6f0/candidate.glb', LENSES),
};
async function load(url: URL) {
  const gltf = await new GLTFLoader().register(() => ({name: 'PivotGeometryOnly', loadTexture: async () => new Texture()}))
    .parseAsync(new Uint8Array(await readFile(url)).buffer, '/models/');
  classifyAssetMaterials(gltf.scene, gltf);
  const dispose = () => gltf.scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    object.geometry.dispose();
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) material.dispose();
  });
  return {scene: gltf.scene as Object3D, dispose};
}

/** The head proxy's planes (the convex shell in canonical head centimetres): a point is outside when any plane has it on
 *  its outer side; the value is how far outside (cm), negative inside. */
async function headProxy(): Promise<(point: Vector3) => number> {
  const face = JSON.parse(await readFile(publicFile('models/canonical-face.json'), 'utf8')) as {positions: number[]; indices: number[]};
  const geometry = createTempleHeadShell(face.positions, face.indices), position = geometry.getAttribute('position'), index = geometry.getIndex();
  const centre = new Vector3(), a = new Vector3(), b = new Vector3(), c = new Vector3(), planes: {normal: Vector3; offset: number}[] = [];
  for (let i = 0; i < position.count; i++) centre.add(a.fromBufferAttribute(position, i));
  centre.divideScalar(position.count);
  for (let i = 0; i < (index?.count ?? position.count); i += 3) {
    const at = (k: number) => index ? index.getX(i + k) : i + k;
    a.fromBufferAttribute(position, at(0)); b.fromBufferAttribute(position, at(1)); c.fromBufferAttribute(position, at(2));
    const normal = new Vector3().subVectors(b, a).cross(new Vector3().subVectors(c, a));
    if (normal.lengthSq() < 1e-16) continue;
    normal.normalize();
    if (normal.dot(new Vector3().subVectors(a, centre)) < 0) normal.negate();
    planes.push({normal, offset: normal.dot(a)});
  }
  geometry.dispose();
  return point => Math.max(...planes.map(plane => plane.normal.dot(point) - plane.offset));
}

test('proof: the Prada delivery is spread from its guard plane and lies outside the head proxy up to its fixed return', {skip: !existsSync(PROOF.prada)}, async t => {
  const {scene, dispose} = await load(PROOF.prada); t.after(dispose);
  const inventory = templeInventory(scene), clip = deriveTempleClip(inventory);
  assert.equal(clip.clipZM, -.14);
  const rear = createRearDrop(scene, clip.clipZM, 0, {sides: inventory.present}); t.after(() => rear.dispose());
  assert.ok(rear.shaftBoundaryZM !== null && Math.abs(rear.shaftBoundaryZM + .073) <= .0015, `the shield collapses at ${rear.shaftBoundaryZM}`);
  assert.equal(rear.hingeZM, null);
  const lensRear = inventory.lensRearZM!, guard = lensRear - REAR_DROP_PARAMETERS.proximalGuardM;
  assert.ok(Math.abs(rear.spreadStartZM - guard) < 1e-6, `pivot ${rear.spreadStartZM}, guard ${guard}`);
  // The capture's fitted scale and the renderer's buried cap / terminal return (renderer.ts configure + pose).
  const fitScale = .97517, maximumZM = Math.max(clip.clipZM, Math.min(-.055, clip.clipZM + HIDDEN_TAIL_M));
  const evaluator = createTempleTerminalFitEvaluator(scene, {offsetCm: GLASSES_OFFSET_CM, spreadM: SPREAD_M, spreadStartZM: rear.spreadStartZM,
    modelCutoffZM: clip.clipZM, maximumZM, sides: inventory.present});
  const fit = evaluator(fitScale);
  assert.ok(fit, 'the return is contained at the fitted scale');
  rear.setTerminalFit(fit); rear.setShape(0, SPREAD_M);
  const outside = await headProxy();
  // The outermost drawn point of the left arm per 2.5 mm station, from 10 mm behind the guard to the return start, in
  // head centimetres (the asset hangs at GLASSES_OFFSET_CM under the face pose, scaled by the fit).
  const head = (x: number, y: number, z: number) => new Vector3(x * 100 * fitScale + GLASSES_OFFSET_CM[0],
    y * 100 * fitScale + GLASSES_OFFSET_CM[1], z * 100 * fitScale + GLASSES_OFFSET_CM[2]);
  const stations: {z: number; drawn: number; oldPivot: number}[] = [];
  const oldPivotZM = rear.shaftBoundaryZM!;
  // Up to 2.5 mm in front of the return start: at the start itself the drawn arm touches the proxy (0.1 mm inside).
  for (let z = guard - .010; z >= fit!.startZM + .0025; z -= .0025) {
    let outer: {x: number; y: number; z: number} | null = null;
    scene.traverse(object => {
      if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
      const drawn = object.geometry.getAttribute('position');
      for (let i = 0; i < drawn.count; i++) {
        const x = drawn.getX(i), vz = drawn.getZ(i);
        if (x >= -.045 || Math.abs(vz - z) > .00125) continue;
        if (!outer || x < outer.x) outer = {x, y: drawn.getY(i), z: vz};
      }
    });
    assert.ok(outer, `the left arm has geometry at z ${z}`);
    const o = outer as unknown as {x: number; y: number; z: number};
    // The same point under the old pivot (-73 mm): undo the new spread and apply the old one (the return is unchanged).
    const authoredX = (() => {
      // spreadArmX is monotone in |x|: invert it by bisection on the authored side of the arm.
      let lo = -.2, hi = -.045;
      for (let k = 0; k < 60; k++) {
        const mid = (lo + hi) / 2, value = terminalFitX(spreadArmX(mid, o.z, rear.spreadStartZM, clip.clipZM, SPREAD_M), o.z, fit);
        if (value < o.x) lo = mid; else hi = mid;
      }
      return (lo + hi) / 2;
    })();
    const oldX = terminalFitX(spreadArmX(authoredX, o.z, oldPivotZM, clip.clipZM, SPREAD_M), o.z, fit);
    stations.push({z: o.z, drawn: outside(head(o.x, o.y, o.z)), oldPivot: outside(head(oldX, o.y, o.z))});
  }
  const fmt = (list: typeof stations) => list.map(s => `${(s.z * 1000).toFixed(1)}:${(s.drawn * 10).toFixed(1)}/${(s.oldPivot * 10).toFixed(1)}mm`).join(' ');
  // Drawn now: outside the head proxy at every station from the hinge region to where the return begins.
  assert.ok(stations.length >= 15 && stations.every(s => s.drawn > 0), `drawn arm vs head proxy (mm outside, new/old pivot): ${fmt(stations)}`);
  // Under the old pivot the same arm went inside the proxy ahead of its return (what the captures cut).
  assert.ok(stations.some(s => s.oldPivot < 0), `old pivot never inside: ${fmt(stations)}`);
});

for (const [name, url, hinge] of [['FT1119 q001', PROOF.ft1119, -.010], ['r9', PROOF.r9, -.011]] as const) {
  test(`proof: ${name} (a thin shaft) keeps its measured hinge`, {skip: !existsSync(url)}, async t => {
    const {scene, dispose} = await load(url); t.after(dispose);
    const inventory = templeInventory(scene), clip = deriveTempleClip(inventory);
    const rear = createRearDrop(scene, clip.clipZM, 0, {sides: inventory.present}); t.after(() => rear.dispose());
    assert.equal(rear.hingeZM, hinge); assert.equal(rear.shaftBoundaryZM, hinge); assert.equal(rear.spreadStartZM, hinge);
  });
}
