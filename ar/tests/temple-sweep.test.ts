/** The temple test sweep: the catalogue itself (is it a usable experiment, and does every entry describe a
 *  configuration the pipeline will actually accept), the address option that starts on one, and the live pivot the
 *  page's sweep needs — switching a configuration inside a session must land on exactly the geometry a cold start
 *  with the same values would have produced. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, BufferGeometry, Float32BufferAttribute, Group, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial} from 'three';
import {
  describeTempleTest, SHIPPED_TEMPLE_TEST, TEMPLE_SWEEP, templeSweepGroups, templeTestById, templeTestSearch,
} from '../src/temple-sweep.ts';
import {parseConfig, unrecognizedOptions} from '../src/config.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {ARM_LATERAL_MIN_M, MAX_ARM_SPREAD_M, SPREAD_PIVOT_RANGE_M} from '../src/render/face-width.ts';
import {buildTempleContinuityModel, projectTempleContinuity} from '../src/render/continuity.ts';
import {createProtection} from '../src/render/protection.ts';
import {validateReliefBand} from '../src/render/temple-visibility.ts';
import {GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';

/** Two box arms and a lens triangle, tessellated along z so the hinge is found and the ramp is resolved. */
function asset(depthSegments = 16) {
  const root = new Group(), left = new BoxGeometry(.004, .004, .1, 1, 1, depthSegments), right = left.clone();
  left.translate(-.065, 0, -.07); right.translate(.065, 0, -.07);
  const frame = new MeshStandardMaterial(), lensMaterial = new MeshPhysicalMaterial({transmission: 1});
  const lens = new BufferGeometry().setAttribute('position', new Float32BufferAttribute([-.02, 0, -.01, .02, 0, -.01, 0, .01, -.005], 3));
  root.add(new Mesh(left, frame), new Mesh(right, frame), new Mesh(lens, lensMaterial));
  return {root, dispose: () => {for (const resource of [left, right, lens, frame, lensMaterial]) resource.dispose();}};
}
const armPositions = (root: Group): Float32Array[] =>
  root.children.map(child => Float32Array.from(((child as Mesh).geometry.getAttribute('position').array as Float32Array)));

test('the sweep is a usable experiment: distinct ids, contiguous groups, and every entry inside the levers\' own bounds', () => {
  assert.ok(TEMPLE_SWEEP.length >= 40, `a sweep worth stepping through (${TEMPLE_SWEEP.length})`);
  const ids = TEMPLE_SWEEP.map(entry => entry.id);
  assert.equal(new Set(ids).size, ids.length, 'ids are distinct, so a verdict names exactly one configuration');
  for (const entry of TEMPLE_SWEEP) {
    assert.match(entry.id, /^[A-Z][0-9]{1,2}$/, entry.id);
    assert.ok(entry.label.length > 0 && entry.group.length > 0, entry.id);
    // Every value is one the address parser and the renderer both accept, so no entry can be unreachable.
    assert.ok(Number.isFinite(entry.bendMm) && Math.abs(entry.bendMm) <= MAX_ARM_SPREAD_M * 1000, entry.id);
    assert.ok(Number.isFinite(entry.pivotMm) && entry.pivotMm >= SPREAD_PIVOT_RANGE_M.min * 1000
      && entry.pivotMm <= SPREAD_PIVOT_RANGE_M.max * 1000, entry.id);
    assert.doesNotThrow(() => validateReliefBand(entry.keepCm, entry.dropCm), entry.id);
    assert.ok(entry.mode === 'depth' || entry.mode === 'angles', entry.id);
  }
  // Groups are contiguous and each id's letter is its group's, so stepping walks one axis at a time.
  const groups = templeSweepGroups();
  assert.equal(groups.reduce((sum, group) => sum + group.tests.length, 0), TEMPLE_SWEEP.length);
  assert.equal(new Set(groups.map(group => group.group)).size, groups.length, 'no group is split in two');
  for (const group of groups) {
    const letters = new Set(group.tests.map(entry => entry.id[0]));
    assert.equal(letters.size, 1, group.group);
    assert.deepEqual(group.tests.map(entry => entry.id), group.tests.map((_, index) => `${[...letters][0]}${index + 1}`));
  }
  // Each axis is actually swept: the sweep contains more than one value of every lever, and covers both rules.
  assert.ok(new Set(TEMPLE_SWEEP.map(entry => entry.bendMm)).size >= 8, 'bends');
  assert.ok(new Set(TEMPLE_SWEEP.map(entry => entry.pivotMm)).size >= 4, 'pivots');
  assert.ok(new Set(TEMPLE_SWEEP.map(entry => `${entry.keepCm}/${entry.dropCm}`)).size >= 4, 'bands');
  assert.equal(new Set(TEMPLE_SWEEP.map(entry => entry.mode)).size, 2, 'both rules');
  assert.ok(TEMPLE_SWEEP.some(entry => entry.bendMm === 0), 'the arms as authored are in the sweep');
  assert.ok(TEMPLE_SWEEP.some(entry => entry.bendMm < 0), 'and so are arms pulled inward');
  // What ships is in the sweep, so "the same as now" is one of the things that can be judged.
  const signature = (entry: {bendMm: number; pivotMm: number; keepCm: number; dropCm: number; mode: string}): string =>
    `${entry.bendMm}|${entry.pivotMm}|${entry.keepCm}|${entry.dropCm}|${entry.mode}`;
  const shipped = TEMPLE_SWEEP.filter(entry => signature(entry) === signature(SHIPPED_TEMPLE_TEST));
  assert.ok(shipped.length >= 1 && shipped[0]!.sameAs === null, 'the shipped configuration is in the sweep');
  // Each ladder carries the reference rung, so a few configurations repeat. Every repeat names the entry it repeats,
  // and every entry that names one really is identical to it — so nothing is judged twice without being told.
  const first = new Map<string, string>();
  for (const entry of TEMPLE_SWEEP) {
    const key = signature(entry), earlier = first.get(key) ?? null;
    assert.equal(entry.sameAs, earlier, `${entry.id} should point at ${earlier ?? 'nothing'}`);
    if (earlier === null) first.set(key, entry.id);
    else assert.deepEqual([entry.bendMm, entry.pivotMm, entry.keepCm, entry.dropCm, entry.mode],
      (() => {const other = templeTestById(earlier)!; return [other.bendMm, other.pivotMm, other.keepCm, other.dropCm, other.mode];})(), entry.id);
  }
  assert.ok(first.size >= 40, `${first.size} distinct configurations, not counting the repeated reference rungs`);
  // The line a screenshot is read by names the id and all four levers.
  const line = describeTempleTest(TEMPLE_SWEEP[0]!);
  assert.match(line, /^\[A1\] bend -?\d+ mm · pivot \d+ mm · band [\d.]+–[\d.]+ cm/);
  assert.match(describeTempleTest(TEMPLE_SWEEP.find(entry => entry.mode === 'angles')!), /· angles rule$/);
});

test('?templetest= starts on one entry, sets all four levers, and overrides the individual options', () => {
  assert.equal(parseConfig('').templeTest, null);
  for (const entry of TEMPLE_SWEEP) {
    const config = parseConfig(`?templetest=${entry.id}`);
    assert.equal(config.templeTest, entry.id, entry.id);
    assert.equal(config.templeBendMm, entry.bendMm, entry.id);
    assert.equal(config.templePivotMm, entry.pivotMm, entry.id);
    assert.equal(config.templeKeepCm, entry.keepCm, entry.id);
    assert.equal(config.templeDropCm, entry.dropCm, entry.id);
    assert.equal(config.temples, entry.mode, entry.id);
    // The address that runs the same entry from a cold page reaches the same four levers.
    const plain = parseConfig(templeTestSearch(entry));
    assert.deepEqual([plain.templeBendMm, plain.templePivotMm, plain.templeKeepCm, plain.templeDropCm, plain.temples],
      [entry.bendMm, entry.pivotMm, entry.keepCm, entry.dropCm, entry.mode], entry.id);
    assert.equal(plain.templeTest, null, 'but it is not reported as a sweep entry, because the id was not given');
  }
  // An entry wins over the individual levers, which is what "the sweep sets all four" means.
  const mixed = parseConfig('?templebend=2&templepivot=1&templekeep=5&templedrop=6&temples=angles&templetest=A1');
  const a1 = templeTestById('A1')!;
  assert.deepEqual([mixed.templeBendMm, mixed.templePivotMm, mixed.templeKeepCm, mixed.templeDropCm, mixed.temples],
    [a1.bendMm, a1.pivotMm, a1.keepCm, a1.dropCm, a1.mode]);
  // Ids are matched without case or surrounding space; anything that is not an id leaves the other options alone.
  assert.equal(parseConfig('?templetest=a1').templeTest, 'A1');
  assert.equal(templeTestById(' c3 ')?.id, 'C3');
  for (const bad of ['?templetest=', '?templetest=ZZ9', '?templetest=1', '?templetest=A99']) {
    const config = parseConfig(`${bad}&templebend=2&templepivot=1`);
    assert.equal(config.templeTest, null, bad);
    assert.equal(config.templeBendMm, 2, bad); assert.equal(config.templePivotMm, 1, bad);
  }
  assert.equal(templeTestById(null), null); assert.equal(templeTestById(undefined), null);
  assert.deepEqual(unrecognizedOptions('?templetest=A1'), []);
  assert.deepEqual(unrecognizedOptions('?templetests=A1'), ['templetests']);
});

test('moving the pivot inside a session lands on exactly the geometry a cold start would have produced', () => {
  const cap = -.11, spread = .016;
  // 64 segments over the 10 cm shaft puts vertices every 1.6 mm, so there are several between the rear drop's own
  // start plane (-0.025) and the hinge (-0.020). That band is the whole point: a pivot that has moved back must not
  // narrow the set of vertices the bend may touch, and a coarser shaft has no vertex in it to prove it with.
  const segments = 64;
  for (const [from, to] of [[0, .012], [.012, 0], [.030, 0], [0, .030], [.006, .024]] as const) {
    const live = asset(segments), cold = asset(segments), original = asset(segments);
    const liveDrop = createRearDrop(live.root, cap, from), coldDrop = createRearDrop(cold.root, cap, to);
    const authored = armPositions(original.root);
    try {
      liveDrop.setSpread(spread); coldDrop.setSpread(spread);
      liveDrop.setSpreadPivot(to);
      assert.equal(liveDrop.spreadStartZM, coldDrop.spreadStartZM, `${from} -> ${to}`);
      assert.deepEqual(armPositions(live.root), armPositions(cold.root), `${from} -> ${to}: the same arms`);
      // The set of vertices the bend may touch is fixed at the hinge, so coming BACK to a forward pivot is not
      // permanently narrowed by having started further back.
      liveDrop.setSpreadPivot(from);
      const there = createRearDrop(original.root, cap, from);
      try {
        there.setSpread(spread);
        assert.deepEqual(armPositions(live.root), armPositions(original.root), `${from} -> ${to} -> ${from}`);
        // Specifically: the vertices between the drop's start plane and the hinge are bent by a pivot at the hinge,
        // whatever pivot the session has been through, because the deformable set is fixed at the hinge.
        const authoredFront = asset(segments);
        try {
          const front = armPositions(authoredFront.root).flatMap((positions, mesh) => [...positions.keys()]
            .filter(i => i % 3 === 0 && positions[i + 2]! < -.020 && positions[i + 2]! > -.025 && Math.abs(positions[i]!) > ARM_LATERAL_MIN_M)
            .map(i => [mesh, i] as const));
          assert.ok(front.length > 0, 'the fixture has arm vertices between the drop start and the hinge');
          if (from === 0 || to === 0) {
            const now = armPositions(from === 0 ? live.root : cold.root);
            const authored = armPositions(authoredFront.root);
            assert.ok(front.some(([mesh, i]) => now[mesh]![i] !== authored[mesh]![i]),
              'a pivot at the hinge bends the shaft in front of the rear drop\'s own start');
          }
        } finally {authoredFront.dispose();}
        // And spread 0 is the authored frame again, whatever the pivot has been.
        liveDrop.setSpread(0); there.setSpread(0);
        assert.deepEqual(armPositions(live.root), authored);
      } finally {there.dispose();}
    } finally {liveDrop.dispose(); coldDrop.dispose(); live.dispose(); cold.dispose(); original.dispose();}
  }
  // A pivot the ramp could not draw is refused, and refusing it changes nothing.
  const {root, dispose} = asset();
  const drop = createRearDrop(root, cap);
  try {
    drop.setSpread(.01);
    const before = armPositions(root), plane = drop.spreadStartZM;
    for (const bad of [-0.001, SPREAD_PIVOT_RANGE_M.max + 0.001, Number.NaN, Infinity]) {
      assert.throws(() => drop.setSpreadPivot(bad), /pivot is out of range/, `${bad}`);
    }
    assert.equal(drop.spreadStartZM, plane);
    assert.deepEqual(armPositions(root), before, 'a refused pivot leaves the arms alone');
  } finally {drop.dispose(); dispose();}
});

test('every entry in the sweep draws arms the rest of the pipeline agrees with', () => {
  const cap = -.11;
  const landmarks = Array.from({length: 478}, () => ({x: .5, y: .45, z: 0}));
  landmarks[33] = {x: .42, y: .43, z: 0}; landmarks[263] = {x: .58, y: .43, z: 0}; landmarks[2] = {x: .5, y: .52, z: 0};
  const pose = new Matrix4().makeRotationY(18 * Math.PI / 180).setPosition(0, 0, -40).toArray();
  const input = {eyewearMatrix: pose, offsetCm: GLASSES_OFFSET_CM, sourceAspect: 1.5, width: 1200, height: 800, dropM: .012};
  const {root, dispose} = asset(32);
  const model = buildTempleContinuityModel(root, cap);
  const drop = createRearDrop(root, cap);
  const authored = armPositions(root).map(array => array.slice());
  try {
    for (const entry of TEMPLE_SWEEP) {
      drop.setSpreadPivot(entry.pivotMm / 1000);
      drop.setShape(input.dropM, entry.bendMm / 1000);
      assert.equal(drop.spreadM, entry.bendMm / 1000, entry.id);
      // No entry may move an arm point across the lateral plane the fixed temple rules test, or flip a side.
      for (const [mesh, positions] of armPositions(root).entries()) for (let i = 0; i < positions.length; i += 3) {
        const x0 = authored[mesh]![i]!, x = positions[i]!;
        if (Math.abs(x0) <= ARM_LATERAL_MIN_M) {assert.equal(x, x0, `${entry.id}: the frame front never moves`); continue;}
        assert.ok(Math.sign(x) === Math.sign(x0) && Math.abs(x) > ARM_LATERAL_MIN_M, `${entry.id}: ${x0} -> ${x}`);
      }
      // The cut walks the arms as drawn, and every station of it is inside the corridor the stencil may edit.
      const paths = projectTempleContinuity(model, {...input, spreadM: entry.bendMm / 1000, spreadStartZM: drop.spreadStartZM})!;
      const protection = createProtection({optical: drop.opticalBounds, originalArms: drop.originalArmBounds, candidateArms: drop.candidateArmBounds},
        pose, GLASSES_OFFSET_CM, landmarks, input.width, input.height, input.sourceAspect)!;
      assert.ok(protection, entry.id);
      for (const path of paths) for (const point of path.points) {
        assert.ok(protection.editableRects.some(rect => point.x >= rect.x0 - 1 && point.x <= rect.x1 + 1 && point.y >= rect.y0 - 1 && point.y <= rect.y1 + 1),
          `${entry.id}: a centreline point (${point.x.toFixed(1)}, ${point.y.toFixed(1)}) fell outside the editable corridor`);
      }
      // An entry with no bend is the authored frame, whatever pivot it names.
      if (entry.bendMm === 0) {
        drop.setShape(0, 0);
        assert.deepEqual(armPositions(root), authored, `${entry.id}: no bend is the authored frame`);
      }
    }
    // And after the whole sweep, back to nothing is still byte-identical.
    drop.setSpreadPivot(0); drop.setShape(0, 0);
    assert.deepEqual(armPositions(root), authored);
  } finally {drop.dispose(); dispose();}
});
