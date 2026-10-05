import assert from 'node:assert/strict';
import {test} from 'node:test';
import {BoxGeometry, Float32BufferAttribute, Group, Mesh, MeshPhysicalMaterial} from 'three';
import {selectQATempleCutoff, DEFAULT_TEMPLE_CUTOFF_M, TIP_INTERIOR_GUARD_M} from './temple-cutoff.mjs';
import {PART_VISIBILITY_KEY} from '../src/eyewear/part-visibility.ts';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';
import {buildTempleContinuityModel} from '../src/render/continuity.ts';
import {registerModelingAutoEyewear, eyewearById} from '../src/eyewear/catalog.ts';

function part(name, region, x, back, front) {
  const mesh = new Mesh(new BoxGeometry(.006, .008, front - back).translate(x, 0, (back + front) / 2),
    new MeshPhysicalMaterial({transmission: region === 'lens' ? .5 : 0}));
  mesh.name = name; mesh.userData = {partRole: region === 'lens' ? 'lens' : 'frame', [PART_VISIBILITY_KEY]: {version: 1, region}};
  return mesh;
}
function fixture(t, leftTip = -.138705, rightTip = -.138689) {
  const left = part('left', 'temple_left', -.06, leftTip, -.005), right = part('right', 'temple_right', .06, rightTip, -.005);
  const root = new Group().add(left, right, part('lens', 'lens', 0, -.001, .001));
  t.after(() => root.traverse(object => {if (object instanceof Mesh) {object.geometry.dispose(); object.material.dispose();}}));
  return {root, left, right};
}
function check(root, cutoff) {classifyAssetMaterials(root); return buildTempleContinuityModel(root, cutoff);}

test('short original arms pass strict continuity at their actual shared tip range without changing geometry', t => {
  const {root, left, right} = fixture(t);
  const before = [left, right].map(mesh => mesh.geometry.attributes.position.array.slice());
  assert.throws(() => check(root, DEFAULT_TEMPLE_CUTOFF_M), /cross-section is missing/);
  const receipt = selectQATempleCutoff(root), model = check(root, receipt.cutoff_m);
  assert.equal(receipt.cutoff_m, receipt.side_bounds[1].min_z_m + TIP_INTERIOR_GUARD_M);
  assert.equal(receipt.geometry_modified, false); assert.equal(model.sides[0].length, 33); assert.equal(model.sides[1].length, 33);
  for (const [index, mesh] of [left, right].entries()) assert.deepEqual(mesh.geometry.attributes.position.array, before[index]);
  registerModelingAutoEyewear({name: 'short fixture', assetUrl: '/test.glb', widthMm: 130, templeClipLocalZM: receipt.cutoff_m});
  assert.equal(eyewearById('modeling-auto').templeClipLocalZM, receipt.cutoff_m, 'catalog uses the exact selected cutoff');
});

test('asymmetric arm tips use the shorter physical side; long arms retain the existing cutoff', t => {
  const short = fixture(t, -.16, -.12), long = fixture(t, -.16, -.15);
  const result = selectQATempleCutoff(short.root);
  assert.equal(result.cutoff_m, result.side_bounds[1].min_z_m + TIP_INTERIOR_GUARD_M); check(short.root, result.cutoff_m);
  assert.equal(selectQATempleCutoff(long.root).cutoff_m, DEFAULT_TEMPLE_CUTOFF_M);
});

test('multipart arm bounds aggregate physical ownership and exclude deep front and unused positions', t => {
  const {root, left} = fixture(t);
  const rear = part('left tip section', 'temple_left', -.06, -.17, -.13); root.add(rear);
  root.add(part('front hardware', 'front', -.06, -.19, -.1));
  const positions = left.geometry.attributes.position, values = Array.from(positions.array); values.push(-.06, 0, -.4);
  left.geometry.setAttribute('position', new Float32BufferAttribute(values, 3));
  // A primitive Group carries one physical part's profile as GLTFLoader does for mixed materials.
  const group = new Group(); group.userData = {...rear.userData}; delete rear.userData[PART_VISIBILITY_KEY]; root.add(group.add(rear));
  const result = selectQATempleCutoff(root);
  assert.deepEqual(result.side_bounds[0].parts, ['left', 'left tip section']);
  assert.ok(Math.abs(result.side_bounds[0].min_z_m + .17) < 1e-7);
  assert.ok(result.cutoff_m > -.14); check(root, result.cutoff_m);
});

test('adjusting tip metadata never bypasses strict missing interior sections', t => {
  const {root, left} = fixture(t); root.remove(left); left.geometry.dispose(); left.material.dispose();
  root.add(part('left rear', 'temple_left', -.06, -.1387, -.10), part('left proximal', 'temple_left', -.06, -.07, -.005));
  const receipt = selectQATempleCutoff(root);
  assert.throws(() => check(root, receipt.cutoff_m), /cross-section is missing/);
});

test('partial, missing, malformed, transformed and unsupported short arms remain failures', t => {
  const {root, left, right} = fixture(t);
  const profile = left.userData[PART_VISIBILITY_KEY]; delete left.userData[PART_VISIBILITY_KEY];
  assert.throws(() => selectQATempleCutoff(root), /missing left/);
  left.userData[PART_VISIBILITY_KEY] = null; assert.throws(() => selectQATempleCutoff(root), /expected a versioned object/);
  left.userData[PART_VISIBILITY_KEY] = profile; left.position.z = .001;
  assert.throws(() => selectQATempleCutoff(root), /bake authored transforms/); left.position.z = 0;
  root.remove(right); assert.throws(() => selectQATempleCutoff(root), /both explicitly authored/); root.add(right);
  const shallow = fixture(t, -.04, -.04); assert.throws(() => selectQATempleCutoff(shallow.root), /outside the supported range/);
});

test('unprofiled legacy assets retain their exact existing registration', t => {
  const {root} = fixture(t); root.traverse(object => {delete object.userData[PART_VISIBILITY_KEY];});
  assert.deepEqual(selectQATempleCutoff(root), {policy: 'legacy_fixed_cutoff', cutoff_m: -.14, default_cutoff_m: -.14,
    side_bounds: null, geometry_modified: false});
});
