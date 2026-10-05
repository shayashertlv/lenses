import assert from 'node:assert/strict';
import {test} from 'node:test';
import {BoxGeometry, Float32BufferAttribute, Group, Mesh, MeshPhysicalMaterial} from 'three';
import {loadQATempleCutoff, selectQATempleCutoff, DEFAULT_TEMPLE_CUTOFF_M, TIP_INTERIOR_GUARD_M} from './temple-cutoff.mjs';
import {buildGlb, trans} from '../tests/glb-fab.ts';
import {PART_VISIBILITY_KEY} from '../src/eyewear/part-visibility.ts';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';
import {buildTempleContinuityModel} from '../src/render/continuity.ts';
import {resolveModelDefinition} from '../src/eyewear/catalog.ts';
import {normalizeModel} from '../src/model/normalize.ts';
import {ModelRefusal, ModelReportBuilder} from '../src/model/report.ts';

// WP1 stage D2: the cutoff rule now lives in src (src/render/temple-geometry.ts) and the QA receipt is the renderer's own
// derivation. Temples are found by position (or valid authored regions) per side; a missing or short side is reported in
// the receipt instead of thrown, and continuity leaves only that side without a path instead of throwing.

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

test('short original arms: the derived clip lies just inside the shorter tip and continuity passes there, geometry unchanged', t => {
  const {root, left, right} = fixture(t);
  const before = [left, right].map(mesh => mesh.geometry.attributes.position.array.slice());
  // At the old fixed cutoff both arms end before it: each side is left without a path (no throw any more).
  const deep = check(root, DEFAULT_TEMPLE_CUTOFF_M);
  assert.deepEqual(deep.sides.map(side => side.length), [0, 0]);
  assert.deepEqual(deep.missing.map(gap => gap.side), [0, 1]);
  assert.match(deep.missing[0].reason, /the left arm has no cross-section at z -140\.0 mm .*ends before the clip or has a gap there/);
  const receipt = selectQATempleCutoff(root), model = check(root, receipt.cutoff_m);
  assert.equal(receipt.policy, 'authored_temple_regions'); assert.equal(receipt.source, 'derived');
  assert.equal(receipt.cutoff_m, receipt.side_bounds[1].min_z_m + TIP_INTERIOR_GUARD_M); assert.equal(receipt.shorter_side, 'right');
  assert.equal(receipt.geometry_modified, false); assert.equal(model.sides[0].length, 33); assert.equal(model.sides[1].length, 33);
  assert.deepEqual(model.missing, []);
  for (const [index, mesh] of [left, right].entries()) assert.deepEqual(mesh.geometry.attributes.position.array, before[index]);
  const resolved = resolveModelDefinition({url: '/test.glb', displayName: 'short fixture', templeClipZM: receipt.cutoff_m});
  assert.equal(resolved.templeClipZM, receipt.cutoff_m, 'the renderer uses the exact selected cutoff');
  assert.deepEqual(resolved.warnings, []);
});

test('asymmetric arm tips use the shorter physical side; long arms retain the -0.14 m floor', t => {
  const short = fixture(t, -.16, -.12), long = fixture(t, -.16, -.15);
  const result = selectQATempleCutoff(short.root);
  assert.equal(result.cutoff_m, result.side_bounds[1].min_z_m + TIP_INTERIOR_GUARD_M); assert.equal(result.shorter_side, 'right');
  assert.deepEqual(check(short.root, result.cutoff_m).sides.map(side => side.length), [33, 33]);
  const longer = selectQATempleCutoff(long.root);
  assert.equal(longer.cutoff_m, DEFAULT_TEMPLE_CUTOFF_M); assert.equal(longer.shorter_side, null, 'the floor, not an arm, sets it');
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
  assert.ok(Math.abs(result.side_bounds[0].min_z_m + .17) < 1e-7, 'the unreferenced vertex at z -0.4 does not count');
  assert.ok(result.cutoff_m > -.14); assert.deepEqual(check(root, result.cutoff_m).sides.map(side => side.length), [33, 33]);
});

test('adjusting tip metadata never bypasses strict missing interior sections: only the gapped side loses its path', t => {
  const {root, left} = fixture(t); root.remove(left); left.geometry.dispose(); left.material.dispose();
  root.add(part('left rear', 'temple_left', -.06, -.1387, -.10), part('left proximal', 'temple_left', -.06, -.07, -.005));
  const receipt = selectQATempleCutoff(root), model = check(root, receipt.cutoff_m);
  assert.deepEqual(model.sides.map(side => side.length), [0, 33], 'the right arm keeps its path');
  assert.deepEqual(model.missing.map(gap => gap.side), [0]);
  assert.match(model.missing[0].reason, /the left arm has no cross-section at z -(7|8|9)\d\.\d mm/);
});

// Missing or malformed profiles are optional metadata (WP1_DESIGN.md §1): the asset's profiles are ignored and position
// decides, with the warning in the receipt. A missing arm or arms too short to be temples are reported per side.
test('partial, missing, malformed, transformed, one-sided and too-short arms are reported, never thrown', t => {
  const positional = (root, pattern) => {
    const receipt = selectQATempleCutoff(root);
    assert.equal(receipt.policy, 'positional_temples'); assert.equal(receipt.source, 'derived');
    assert.match(receipt.profile_warnings.join(), pattern);
    return receipt;
  };
  const missing = fixture(t); delete missing.left.userData[PART_VISIBILITY_KEY];
  positional(missing.root, /ignored for the whole asset: mixed profiled and unprofiled parts; missing "left"/);
  const malformed = fixture(t); malformed.left.userData[PART_VISIBILITY_KEY] = null;
  positional(malformed.root, /expected \{version: 1, region: .*\} and nothing else on "left"/);
  // A profile on the wrong half of X (temple_left at +x) is ignored too: sides are judged by geometry.
  const swapped = fixture(t);
  swapped.left.userData[PART_VISIBILITY_KEY] = {version: 1, region: 'temple_right'}; swapped.right.userData[PART_VISIBILITY_KEY] = {version: 1, region: 'temple_left'};
  const bySide = positional(swapped.root, /"left" region 'temple_right' lies at x -60\.0 mm, but temple_left must lie at x < 0/);
  assert.deepEqual(bySide.side_bounds.map(side => side.present), [true, true]);
  // A transformed arm is refused once, by the load-time §2 Transforms check (src/model/normalize.ts), before any QA or
  // runtime consumer reads the scene; part-visibility no longer duplicates it (WP1_DESIGN.md §1).
  const moved = fixture(t); moved.left.position.z = .001;
  assert.throws(() => normalizeModel(moved.root, new ModelReportBuilder('moved', 'moved')),
    error => error instanceof ModelRefusal && /§2 Transforms: 1 mesh node\(s\) with a non-identity world transform/.test(error.message));
  const oneArm = fixture(t); oneArm.root.remove(oneArm.right);
  const one = selectQATempleCutoff(oneArm.root);
  assert.deepEqual(one.side_bounds.map(side => side.present), [true, false]);
  assert.match(one.side_bounds[1].missing, /no part carries the LENSES_part_visibility region temple_right/);
  assert.equal(one.cutoff_m, one.side_bounds[0].min_z_m + TIP_INTERIOR_GUARD_M, 'derived from the one arm there is');
  const shallow = selectQATempleCutoff(fixture(t, -.04, -.04).root);
  assert.deepEqual(shallow.side_bounds.map(side => side.present), [false, false]);
  assert.match(shallow.side_bounds[0].missing, /the temple_left region reaches only z -40\.0 mm .*hinge hardware alone is not a temple/);
  assert.equal(shallow.source, 'default'); assert.equal(shallow.cutoff_m, DEFAULT_TEMPLE_CUTOFF_M);
  assert.match(shallow.warnings.join(), /no temple on either side, so no clip can be derived/);
});

// loadQATempleCutoff used to require a pin and parse on its own (no normalisation): a transformed arm was measured from
// untransformed positions and a millimetre file threw "outside the supported range". It now reads the model through the
// renderer's own front-end and normalisation pass (model/check.ts).
const PROFILE_REGIONS = {Bridge: 'front', Brow_L: 'front', Brow_R: 'front', Lens_L: 'lens', Lens_R: 'lens', Temple_L: 'temple_left', Temple_R: 'temple_right'};
const profiled = doc => {for (const node of doc.nodes) if (PROFILE_REGIONS[node.name]) node.extras = {LENSES_part_visibility: {version: 1, region: PROFILE_REGIONS[node.name]}};};
function serve(t, built) {
  t.mock.method(globalThis, 'fetch', async () => new Response(built.glb.slice(0)));
  return 'http://127.0.0.1:8250/models/unpinned-candidate.glb';
}

test('the QA loader normalises like the renderer: a millimetre file is measured in metres, with no pin', async t => {
  const url = serve(t, buildGlb({scale: 1, patch: profiled}));
  const receipt = await loadQATempleCutoff(url);
  assert.equal(receipt.policy, 'authored_temple_regions');
  assert.ok(Math.abs(receipt.cutoff_m - (-.14 + TIP_INTERIOR_GUARD_M)) < 1e-6, `cutoff ${receipt.cutoff_m} m (temples end at -140 mm)`);
  assert.deepEqual(receipt.side_bounds.map(side => side.parts), [['Temple_L'], ['Temple_R']]);
  assert.match(receipt.model_sha256, /^[0-9a-f]{64}$/);
  assert.equal(receipt.model_status, 'rendered');
  assert.deepEqual(receipt.model_fallbacks.map(entry => entry.subsystem), ['units']);
  assert.ok(receipt.model_warnings.some(warning => warning.startsWith('UNITS: the model is in millimetres')));
});

test('the QA loader refuses what the renderer refuses (a transformed arm), instead of measuring raw positions', async t => {
  const url = serve(t, buildGlb({patch: profiled, nodeMatrix: trans(0, 0, .001), target: 'Temple_L'}));
  await assert.rejects(loadQATempleCutoff(url), /QA temple cutoff: the model was refused \(§2 Transforms: 1 mesh node\(s\) with a non-identity world transform/);
});

test('the QA loader reports an ignored part-visibility profile as a fallback and derives the clip by position', async t => {
  const url = serve(t, buildGlb({patch: doc => {profiled(doc); doc.nodes.find(node => node.name === 'Temple_L').extras = {LENSES_part_visibility: {version: 2}};}}));
  const receipt = await loadQATempleCutoff(url);
  assert.equal(receipt.policy, 'positional_temples');
  assert.ok(Math.abs(receipt.cutoff_m - (-.14 + TIP_INTERIOR_GUARD_M)) < 1e-6, `cutoff ${receipt.cutoff_m}`);
  assert.deepEqual(receipt.model_fallbacks.map(entry => entry.subsystem), ['part visibility']);
  assert.match(receipt.model_fallbacks[0].detail, /LENSES_part_visibility ignored for the whole asset: expected \{version: 1, .*"Temple_L"/);
});

// Replaces 'unprofiled legacy assets retain their exact existing registration' (a fixed -0.14 receipt for any
// unprofiled asset): an unprofiled asset now gets the same positional derivation the renderer uses.
test('unprofiled assets get the positional derivation: the shorter arm, just inside its tip', t => {
  const {root} = fixture(t); root.traverse(object => {delete object.userData[PART_VISIBILITY_KEY];});
  classifyAssetMaterials(root);
  const receipt = selectQATempleCutoff(root);
  assert.equal(receipt.policy, 'positional_temples'); assert.equal(receipt.source, 'derived'); assert.equal(receipt.shorter_side, 'right');
  assert.ok(Math.abs(receipt.cutoff_m - (-.138689 + TIP_INTERIOR_GUARD_M)) < 1e-7, `cutoff ${receipt.cutoff_m}`);
  assert.deepEqual(receipt.side_bounds.map(side => [side.side, side.present, side.source]), [['left', true, 'position'], ['right', true, 'position']]);
  assert.equal(receipt.profile_warnings, undefined);
});
