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
import {depthRelief, validateReliefBand} from '../src/render/temple-visibility.ts';
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

test('the sweep is a usable experiment, and asks round four\'s question rather than the settled ones', () => {
  assert.ok(TEMPLE_SWEEP.length >= 12, `a sweep worth stepping through (${TEMPLE_SWEEP.length})`);
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
    assert.ok(Number.isInteger(entry.runPx) && entry.runPx >= 1 && entry.runPx <= 200, entry.id);
    assert.equal(typeof entry.cut, 'boolean', entry.id); assert.equal(typeof entry.hair, 'boolean', entry.id);
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

  // Round four asks what makes the END of the arm pop. The bend is settled, so nothing judged moves it, and the
  // controls are the only entries that do.
  const controls = TEMPLE_SWEEP.filter(entry => entry.group.startsWith('Controls'));
  assert.ok(controls.length >= 2, 'at least two controls');
  const judged = TEMPLE_SWEEP.filter(entry => !controls.includes(entry));
  for (const entry of judged) {
    assert.equal(entry.bendMm, SHIPPED_TEMPLE_TEST.bendMm, `${entry.id}: the bend is settled; round four does not move it`);
    assert.equal(entry.pivotMm, 0, `${entry.id}: moving the pivot back was worse in four separate groups`);
    assert.ok(entry.bendMm > 0, `${entry.id}: arms pulled inward were worse`);
  }
  assert.ok(controls.some(entry => entry.bendMm === 0), 'one control has no bend at all');
  assert.ok(controls.some(entry => entry.dropCm > 2.6), 'one control has a band an earlier round called not good');

  // The two band groups are the experiment: one pins the ONSET and walks the GRADIENT, the other pins the gradient
  // and walks the onset. Confounding them is exactly what every earlier round did, and what round four is undoing.
  const gradient = groups.find(group => group.group.startsWith('How steeply'))!.tests;
  assert.ok(gradient.length >= 4, 'the gradient ladder has rungs');
  assert.equal(new Set(gradient.map(entry => entry.keepCm)).size, 1, 'the gradient ladder pins the onset');
  for (const [index, entry] of gradient.entries()) {
    if (index > 0) assert.ok(entry.dropCm > gradient[index - 1]!.dropCm, `${entry.id} is gentler than ${gradient[index - 1]!.id}`);
  }
  assert.ok(gradient.at(-1)!.dropCm - gradient.at(-1)!.keepCm >= 4 * (gradient[0]!.dropCm - gradient[0]!.keepCm),
    'and the gentlest rung is several times gentler than the shipped one, or the ladder cannot separate the two');
  const onset = groups.find(group => group.group.startsWith('How soon'))!.tests;
  assert.ok(onset.length >= 3, 'the onset ladder has rungs');
  assert.equal(new Set(onset.map(entry => Number((entry.dropCm - entry.keepCm).toFixed(6)))).size, 1,
    'the onset ladder pins the gradient');
  for (const [index, entry] of onset.entries()) {
    if (index > 0) assert.ok(entry.keepCm > onset[index - 1]!.keepCm, `${entry.id} starts later than ${onset[index - 1]!.id}`);
  }
  // The two ladders cross at one configuration, so each can be read against the other.
  assert.ok(gradient.some(a => onset.some(b => a.keepCm === b.keepCm && a.dropCm === b.dropCm)), 'the ladders share a rung');

  // The strip ladder removes one decision at a time, and ends with nothing removing any part of the arm at all.
  const strip = groups.find(group => group.group.startsWith('What is left'))!.tests;
  assert.ok(strip.some(entry => signature(entry) === signature(SHIPPED_TEMPLE_TEST)), 'it starts from the shipped baseline');
  assert.ok(strip.some(entry => !entry.hair), 'it takes hair off the arms');
  assert.ok(strip.some(entry => entry.mode === 'angles'), 'it tries the other rule');
  const openest = strip.find(entry => !entry.hair && entry.dropCm >= 6)!;
  assert.ok(openest, 'and it has a rung where the band is wide open');
  // "Nothing removes the arm" has to be true, not just labelled: an arm well behind the head is still drawn there.
  assert.ok(depthRelief(2, openest.keepCm, openest.dropCm) > 0.95,
    'an arm 2 cm behind the head keeps its coverage at the open rung');
  assert.ok(depthRelief(2, SHIPPED_TEMPLE_TEST.keepCm, SHIPPED_TEMPLE_TEST.dropCm) < 0.05,
    'while the shipped band has given it up entirely — so the two rungs really do differ');

  // What ships is in the sweep, and "shipped" is what a page with no address options actually runs.
  function signature(entry: {bendMm: number; pivotMm: number; keepCm: number; dropCm: number; mode: string; cut: boolean; runPx: number; hair: boolean}): string {
    return [entry.bendMm, entry.pivotMm, entry.keepCm, entry.dropCm, entry.mode, entry.cut, entry.runPx, entry.hair].join('|');
  }
  const shipped = TEMPLE_SWEEP.filter(entry => signature(entry) === signature(SHIPPED_TEMPLE_TEST));
  assert.ok(shipped.length >= 1 && shipped[0]!.sameAs === null, 'the shipped configuration is in the sweep');
  const running = parseConfig('');
  assert.equal(signature({bendMm: running.templeBendMm, pivotMm: running.templePivotMm, keepCm: running.templeKeepCm,
    dropCm: running.templeDropCm, mode: running.temples, cut: running.continuity, runPx: running.continuityRunPx,
    hair: running.hair ?? true}), signature(SHIPPED_TEMPLE_TEST));
  // Every repeat names the entry it repeats, and really is identical to it.
  const first = new Map<string, string>();
  for (const entry of TEMPLE_SWEEP) {
    const key = signature(entry), earlier = first.get(key) ?? null;
    assert.equal(entry.sameAs, earlier, `${entry.id} should point at ${earlier ?? 'nothing'}`);
    if (earlier === null) first.set(key, entry.id);
    else assert.equal(signature(templeTestById(earlier)!), key, entry.id);
  }
  // The line a screenshot is read by names the id, the bend, the band and what the hair cut is doing.
  assert.match(describeTempleTest(TEMPLE_SWEEP[0]!), /^\[U1\] bend 18 mm · band [\d.]+–[\d.]+ cm · hair cut at \d+ px$/);
  assert.match(describeTempleTest(TEMPLE_SWEEP.find(entry => !entry.hair)!), /· no hair on the arms$/);
  assert.match(describeTempleTest(TEMPLE_SWEEP.find(entry => entry.mode === 'angles')!), /· angles rule$/);
});

test('?templetest= starts on one entry and sets every temple lever, overriding the individual options', () => {
  assert.equal(parseConfig('').templeTest, null);
  for (const entry of TEMPLE_SWEEP) {
    const config = parseConfig(`?templetest=${entry.id}`);
    assert.equal(config.templeTest, entry.id, entry.id);
    assert.deepEqual([config.templeBendMm, config.templePivotMm, config.templeKeepCm, config.templeDropCm, config.temples,
      config.continuity, config.continuityRunPx, config.hair],
      [entry.bendMm, entry.pivotMm, entry.keepCm, entry.dropCm, entry.mode, entry.cut, entry.runPx, entry.hair], entry.id);
    // The address that runs the same entry from a cold page reaches the same levers.
    const plain = parseConfig(templeTestSearch(entry));
    assert.deepEqual([plain.templeBendMm, plain.templePivotMm, plain.templeKeepCm, plain.templeDropCm, plain.temples,
      plain.continuity, plain.continuityRunPx, plain.hair ?? true],
      [entry.bendMm, entry.pivotMm, entry.keepCm, entry.dropCm, entry.mode, entry.cut, entry.runPx, entry.hair], entry.id);
    assert.equal(plain.templeTest, null, 'but it is not reported as a sweep entry, because the id was not given');
    assert.deepEqual(unrecognizedOptions(templeTestSearch(entry)), [], entry.id);
  }
  // An entry wins over the individual levers, which is what "the sweep sets every one" means.
  const first = TEMPLE_SWEEP[0]!;
  const mixed = parseConfig(`?templebend=2&templepivot=1&templekeep=5&templedrop=6&temples=angles&hairrun=77&continuity=0&hair=0&templetest=${first.id}`);
  assert.deepEqual([mixed.templeBendMm, mixed.templePivotMm, mixed.templeKeepCm, mixed.templeDropCm, mixed.temples,
    mixed.continuity, mixed.continuityRunPx, mixed.hair],
    [first.bendMm, first.pivotMm, first.keepCm, first.dropCm, first.mode, first.cut, first.runPx, first.hair]);
  // Ids are matched without case or surrounding space; anything that is not an id leaves the other options alone.
  assert.equal(parseConfig(`?templetest=${first.id.toLowerCase()}`).templeTest, first.id);
  assert.equal(templeTestById(` ${first.id.toLowerCase()} `)?.id, first.id);
  for (const bad of ['?templetest=', '?templetest=ZZ9', '?templetest=1', '?templetest=A1', '?templetest=J4', '?templetest=Q4', '?templetest=U99']) {
    const config = parseConfig(`${bad}&templebend=13&hairrun=7`);
    assert.equal(config.templeTest, null, bad);
    assert.equal(config.templeBendMm, 13, bad); assert.equal(config.continuityRunPx, 7, bad);
  }
  assert.equal(templeTestById(null), null); assert.equal(templeTestById(undefined), null);
  assert.deepEqual(unrecognizedOptions(`?templetest=${first.id}`), []);
  assert.deepEqual(unrecognizedOptions('?templetests=U1'), ['templetests']);
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
