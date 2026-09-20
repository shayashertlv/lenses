import test from 'node:test';
import assert from 'node:assert/strict';
import {TempleEndpointTracker, TEMPLE_ENDPOINT} from '../src/render/temple-endpoint.ts';
import type {TempleEndpointInput} from '../src/render/temple-endpoint.ts';
import type {ProjectedTemplePath, TempleContinuityModel} from '../src/render/continuity.ts';
import type {CategoryMask} from '../src/hair/protocol.ts';

const maximumZM = -.124;
const model: TempleContinuityModel = {startZM: -.02, cutoffZM: -.148,
  sides: [-1, 1].map(sign => Array.from({length: 33}, (_, index) => ({zM: -.02 - index * .004,
    centerXM: sign * .065, centerYM: 0, minXM: sign * .065 - .001, maxXM: sign * .065 + .001,
    minYM: -.001, maxYM: .001})))};
function fixture(scale = 1, maskScale = 1) {
  const render = {width: 160 * scale, height: 80 * scale};
  const mask: CategoryMask = {width: render.width * maskScale, height: render.height * maskScale,
    hairIndex: 1, category: new Uint8Array(render.width * render.height * maskScale * maskScale)};
  const paths: ProjectedTemplePath[] = [0, 1].map(side => ({side: side as 0 | 1, lengthPx: 128 * scale,
    points: model.sides[side]!.map((_, index) => ({x: (12 + index * 4) * scale, y: (20 + side * 30) * scale,
      radiusPx: scale, progressPx: index * 4 * scale}))}));
  // Paint a physical interval, independent of the model's coarse station indices.
  const paint = (side: number, frontZM: number, rearZM: number, edgeSlope = 0): void => {
    const x0 = (12 + (-.02 - frontZM) * 1000) * scale * maskScale,
      x1 = (12 + (-.02 - rearZM) * 1000) * scale * maskScale,
      y0 = (18 + side * 30) * scale * maskScale, y1 = (22 + side * 30) * scale * maskScale;
    for (let y = 0; y < mask.height; y++) for (let x = 0; x < mask.width; x++)
      if (x + .5 >= x0 + edgeSlope * (y + .5 - (20 + side * 30) * scale * maskScale)
        && x + .5 < x1 && y + .5 >= y0 && y + .5 < y1) mask.category[y * mask.width + x] = 1;
  };
  return {input: {paths, mask, render, timestampMs: 0, yawDegrees: 0, pitchDegrees: 0} as TempleEndpointInput, paint};
}
const near = (actual: number, expected: number, tolerance = 1e-9): void => {
  assert.ok(Math.abs(actual - expected) < tolerance, `${actual} should be near ${expected}`);
};

test('a substantial earlier hair gap hides the downstream tail on its FIRST fresh frame', () => {
  const f = fixture(); f.paint(0, -.055, -.075);
  const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
  assert.equal(report.negativeState, 'tracking');
  assert.ok(report.negativeZM > -.075 && report.negativeZM + TEMPLE_ENDPOINT.fadeM < -.055);
  assert.equal(report.positiveZM, maximumZM);
  assert.equal(report.zoneStartZM, -.035);
});

test('the front frame guard cannot erase the shaft, and the permanent maximum cannot grow', () => {
  const f = fixture(); f.paint(0, -.02, -.034);
  const tracker = new TempleEndpointTracker(model, maximumZM), report = tracker.update(f.input);
  assert.equal(report.negativeZM, maximumZM); assert.equal(report.negativeCandidateZM, null);
  assert.equal(tracker.reset().positiveZM, maximumZM);
  assert.throws(() => new TempleEndpointTracker(model, -.16));
});

test('the FIRST resolved hair crossing conceals the entire tail even when another patch follows it', () => {
  const f = fixture(); f.paint(0, -.055, -.072); f.paint(0, -.09, -.113);
  const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
  const firstOnly = fixture(); firstOnly.paint(0, -.055, -.072);
  const expected = new TempleEndpointTracker(model, maximumZM).update(firstOnly.input);
  assert.deepEqual(report, expected, 'later hair cannot replace the first crossing or expose the bare gap after it');
  assert.ok(report.negativeZM >= -.072);
  assert.ok(report.negativeZM + report.negativeFadeM <= -.055);
  assert.equal(report.negativeState, 'tracking');
});

test('a narrow resolved first sideburn wins over a wider rear patch at different distances and mask resolutions', () => {
  for (const [scale, maskScale, lengthM] of [[1, 1, .003], [2, .5, .003], [.5, 1, .006], [1, .5, .006]] as const) {
    const f = fixture(scale, maskScale); f.paint(0, -.065, -.065 - lengthM);
    const first = new TempleEndpointTracker(model, maximumZM).update(f.input);
    assert.equal(first.negativeState, 'tracking');
    f.paint(0, -.09, -.115); f.paint(1, -.095, -.115);
    const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
    assert.equal(report.negativeZM, first.negativeZM); assert.equal(report.negativeFadeM, first.negativeFadeM);
    assert.equal(report.negativeCandidateZM, first.negativeCandidateZM);
    assert.ok(report.positiveZM < -.095, 'the opposite arm chooses its own first crossing');
  }
});

test('a new first crossing overrides an existing rear endpoint immediately and holds across later-patch flicker', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), rear = fixture(); rear.paint(0, -.09, -.115);
  const initial = tracker.update({...rear.input, evidenceId: 'rear'});
  const both = fixture(); both.paint(0, -.055, -.072); both.paint(0, -.09, -.115);
  const shortened = tracker.update({...both.input, timestampMs: 33, evidenceId: 'both'});
  assert.ok(shortened.negativeZM > initial.negativeZM + .02, 'the rear remnant is removed in this draw');
  const firstOnly = fixture(); firstOnly.paint(0, -.055, -.072);
  for (let i = 1; i <= 30; i++) {
    const input = i % 2 ? firstOnly.input : both.input;
    const report = tracker.update({...input, timestampMs: 33 + i * 33, evidenceId: i});
    assert.equal(report.negativeZM, shortened.negativeZM);
    assert.equal(report.negativeFadeM, shortened.negativeFadeM);
  }
  const held = tracker.update({...both.input, mask: null, timestampMs: 2000});
  assert.equal(held.negativeZM, shortened.negativeZM);
});

test('a resolved 9 mm patch between coarse stations qualifies without needing an 11 mm station run', () => {
  const f = fixture(2); f.paint(0, -.0565, -.0655);
  const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
  assert.equal(report.negativeState, 'tracking');
  assert.ok(report.negativeZM >= -.0655);
  assert.ok(report.negativeZM + TEMPLE_ENDPOINT.fadeM <= -.0565);
});

test('moving a hair boundary by one original station preserves an endpoint inside both valid intervals', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), first = fixture(); first.paint(0, -.088, -.116);
  const initial = tracker.update(first.input);
  for (let timestampMs = 50; timestampMs <= 1500; timestampMs += 50) {
    const f = fixture(); f.paint(0, timestampMs % 100 === 0 ? -.088 : -.092, -.116);
    const report = tracker.update({...f.input, timestampMs});
    assert.equal(report.negativeZM, initial.negativeZM);
    assert.equal(report.negativeState, 'tracking');
  }
});

test('the same physical coverage stays concealed across render distance and mask resolution', () => {
  const endpoints: number[] = [];
  for (const [scale, maskScale] of [[1, 1], [.5, 1], [2, .5], [1, .5], [2, 1]]) {
    const f = fixture(scale, maskScale); f.paint(0, -.06, -.084);
    const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
    assert.equal(report.negativeState, 'tracking'); endpoints.push(report.negativeZM);
    assert.ok(report.negativeZM > -.084 && report.negativeZM + TEMPLE_ENDPOINT.fadeM < -.06);
  }
  // Raster boundary uncertainty remains; it must not become a switch between a cut and no cut.
  assert.ok(Math.max(...endpoints) - Math.min(...endpoints) < .0015);
});

test('missing and stale masks hold the hidden tail indefinitely instead of authorizing regrowth', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), f = fixture(); f.paint(0, -.06, -.082);
  const initial = tracker.update({...f.input, evidenceId: 'hair'});
  for (const timestampMs of [100, 350, 700, 2000, 30000]) {
    const report = tracker.update({...f.input, timestampMs, mask: null});
    assert.equal(report.negativeZM, initial.negativeZM); assert.equal(report.negativeState, 'held');
    assert.equal(tracker.update({...f.input, timestampMs: timestampMs + 1, evidenceId: 'hair'}).negativeZM, initial.negativeZM);
  }
});

test('only sustained distinct fresh clear observations release the endpoint, at a bounded speed', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), hair = fixture(); hair.paint(0, -.06, -.082);
  const initial = tracker.update(hair.input);
  const clear = fixture();
  for (const timestampMs of [50, 100])
    assert.equal(tracker.update({...clear.input, timestampMs, evidenceId: timestampMs}).negativeZM, initial.negativeZM);
  const release = tracker.update({...clear.input, timestampMs: 150, evidenceId: 150});
  assert.ok(release.negativeZM < initial.negativeZM);
  near(initial.negativeZM - release.negativeZM, TEMPLE_ENDPOINT.lengthenMPerSecond * .05);
  let report = release;
  for (let timestampMs = 200; timestampMs <= 5000; timestampMs += 50)
    report = tracker.update({...clear.input, timestampMs, evidenceId: timestampMs});
  assert.equal(report.negativeZM, maximumZM); assert.equal(report.negativeState, 'fallback');
});

test('a diagonal destination hair edge releases an earlier endpoint and reaches the strongly covered fade interval', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
  const initial = tracker.update(early.input), rear = fixture(); rear.paint(0, -.085, -.115, 1);
  let report = initial, previousZM = initial.negativeZM;
  for (let timestampMs = 50; timestampMs <= 5000; timestampMs += 50) {
    report = tracker.update({...rear.input, timestampMs, evidenceId: timestampMs});
    if (timestampMs <= 100) assert.equal(report.negativeZM, initial.negativeZM, 'fresh-observation dwell remains required');
    assert.ok(previousZM - report.negativeZM <= TEMPLE_ENDPOINT.lengthenMPerSecond * .05 + 1e-10);
    previousZM = report.negativeZM;
  }
  assert.equal(report.negativeState, 'tracking');
  near(report.negativeZM, -.091, 1e-9); // first full cross-section is at -86 mm; the complete fade fits behind it
  assert.ok(report.negativeZM < initial.negativeZM - .02);
});

test('a separate centre or thick partial obstruction still blocks release toward a later hair interval', () => {
  for (const rows of [[20], [19, 21]]) {
    const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
    const initial = tracker.update(early.input), rear = fixture(); rear.paint(0, -.085, -.115, 1);
    // A centre-only strand and majority-width edge contact remain unresolved obstructions.
    for (const y of rows) for (let x = 64; x < 69; x++) rear.input.mask!.category[y * rear.input.mask!.width + x] = 1;
    for (let timestampMs = 50; timestampMs <= 5000; timestampMs += 50) {
      const report = tracker.update({...rear.input, timestampMs, evidenceId: timestampMs});
      assert.equal(report.negativeZM, initial.negativeZM);
      assert.ok(report.negativeCandidateZM !== null, 'the later strong interval is still detected');
    }
  }
});

test('fresh release stops inside the first resolved full-width barrier instead of the later destination', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
  const initial = tracker.update(early.input), rear = fixture(); rear.paint(0, -.085, -.115, 1);
  for (let y = 19; y <= 21; y++) for (let x = 64; x < 69; x++) rear.input.mask!.category[y * rear.input.mask!.width + x] = 1;
  let report = initial;
  for (let timestampMs = 50; timestampMs <= 5000; timestampMs += 50) {
    const previous = report.negativeZM;
    report = tracker.update({...rear.input, timestampMs, evidenceId: timestampMs});
    if (timestampMs <= 100) assert.equal(report.negativeZM, initial.negativeZM, 'fresh dwell still protects release');
    assert.ok(previous - report.negativeZM <= TEMPLE_ENDPOINT.lengthenMPerSecond * .05 + 1e-10);
    assert.ok(report.negativeZM > -.077, 'no shaft beyond the first barrier is ever revealed');
    assert.ok(report.negativeCandidateZM! > -.077);
  }
  assert.equal(report.negativeState, 'tracking');
  assert.ok(report.negativeZM + report.negativeFadeM <= -.072 + 1e-10, 'the fade fits inside the first barrier');
});

test('a light non-centre fringe cannot permanently pin only one side before a supported rear hair interval', () => {
  for (const kind of ['fixed', 'alternating', 'wandering'] as const) {
    const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture();
    early.paint(0, -.055, -.075); early.paint(1, -.085, -.115);
    const initial = tracker.update(early.input);
    let report = initial;
    for (let timestampMs = 50; timestampMs <= 5000; timestampMs += 50) {
      const rear = fixture(); rear.paint(0, -.085, -.115, 1); rear.paint(1, -.085, -.115);
      if (kind !== 'alternating' || timestampMs % 100 === 0) {
        const x = kind === 'wandering' ? 62 + timestampMs / 50 % 8 : 64;
        rear.input.mask!.category[19 * rear.input.mask!.width + x] = 1;
      }
      report = tracker.update({...rear.input, timestampMs});
      if (timestampMs <= 100) assert.equal(report.negativeZM, initial.negativeZM, 'fresh dwell remains required');
      assert.equal(report.positiveZM, initial.positiveZM, 'the already covered opposite side stays fixed');
    }
    assert.equal(report.negativeState, 'tracking');
    near(report.negativeZM, -.091);
  }
});

test('an edge contact cannot authorize release without a supported rear destination', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
  const initial = tracker.update(early.input), clear = fixture();
  clear.input.mask!.category[19 * clear.input.mask!.width + 64] = 1;
  for (let timestampMs = 50; timestampMs <= 5000; timestampMs += 50) {
    const report = tracker.update({...clear.input, timestampMs, evidenceId: timestampMs});
    assert.equal(report.negativeCandidateZM, null); assert.equal(report.negativeZM, initial.negativeZM);
  }
});

test('release speed integrates fresh supported time independently of drawn-frame mask reuse', () => {
  const released: number[] = [];
  for (const maskEvery of [1, 2, 3]) {
    const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
    tracker.update(early.input);
    const clear = fixture(); let at1200 = 0, at2400 = 0;
    for (let frame = 1; frame <= 120; frame++) {
      const timestampMs = frame * 20;
      const report = tracker.update({...clear.input, timestampMs, evidenceId: Math.floor((frame - 1) / maskEvery)});
      if (frame === 60) at1200 = report.negativeZM;
      if (frame === 120) at2400 = report.negativeZM;
    }
    released.push(at1200 - at2400);
  }
  for (const distance of released) near(distance, TEMPLE_ENDPOINT.lengthenMPerSecond * 1.2);
});

test('dropout or uncertain reused observations reset the fresh-time clock without a catch-up jump', () => {
  for (const kind of ['missing', 'invalid-reuse', 'stale'] as const) {
    const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
    tracker.update(early.input);
    const rear = fixture(); rear.paint(0, -.085, -.115);
    let before = tracker.update({...rear.input, timestampMs: 50, evidenceId: 1});
    for (const timestampMs of [100, 150, 200]) before = tracker.update({...rear.input, timestampMs, evidenceId: timestampMs});
    const uncertain = fixture(); uncertain.paint(0, -.085, -.115);
    if (kind === 'invalid-reuse') uncertain.input.paths = uncertain.input.paths!.map(path => path.side !== 0 ? path : {...path,
      points: path.points.map((point, index) => index === 14 ? {...point, x: NaN} : point)});
    for (const timestampMs of [250, 400, 600, 800]) {
      const held = tracker.update({...uncertain.input, timestampMs, evidenceId: 200, mask: kind === 'missing' ? null : uncertain.input.mask});
      assert.equal(held.negativeZM, before.negativeZM);
    }
    for (const timestampMs of [850, 900])
      assert.equal(tracker.update({...rear.input, timestampMs, evidenceId: timestampMs}).negativeZM, before.negativeZM);
    const resumed = tracker.update({...rear.input, timestampMs: 950, evidenceId: 950});
    near(before.negativeZM - resumed.negativeZM, TEMPLE_ENDPOINT.lengthenMPerSecond * .05);
  }
});

test('an unobserved gap before the destination interval cannot be treated as its partial edge', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
  const initial = tracker.update(early.input), rear = fixture(); rear.paint(0, -.085, -.115, 1);
  rear.input.paths = rear.input.paths!.map(path => path.side !== 0 ? path : {...path,
    points: path.points.map((point, index) => index === 15 ? {...point, x: NaN} : point)});
  for (let timestampMs = 50; timestampMs <= 2000; timestampMs += 50) {
    const report = tracker.update({...rear.input, timestampMs, evidenceId: timestampMs});
    assert.equal(report.negativeZM, initial.negativeZM);
    assert.ok(report.negativeCandidateZM !== null);
  }
});

test('release into a straight destination interval does not stall before its final sub-sample step', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
  tracker.update(early.input);
  const rear = fixture(); rear.paint(0, -.085, -.115);
  let report = tracker.update({...rear.input, timestampMs: 50, evidenceId: 50});
  for (let timestampMs = 100; timestampMs <= 5000; timestampMs += 50)
    report = tracker.update({...rear.input, timestampMs, evidenceId: timestampMs});
  near(report.negativeZM, -.09, 1e-9); assert.equal(report.negativeState, 'tracking');
});

test('copies or warped reuses of one clear observation cannot build reveal confidence', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), hair = fixture(); hair.paint(0, -.06, -.082);
  const initial = tracker.update(hair.input), clear = fixture();
  for (let timestampMs = 50; timestampMs <= 1000; timestampMs += 50) {
    const copy = {...clear.input.mask!, category: clear.input.mask!.category.slice()};
    assert.equal(tracker.update({...clear.input, mask: copy, timestampMs, evidenceId: 'same-clear-frame'}).negativeZM, initial.negativeZM);
  }
});

test('a reused warped hair mask hides a newly earlier downstream tail on the same draw', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), hair = fixture(); hair.paint(0, -.09, -.114);
  const initial = tracker.update({...hair.input, evidenceId: 'same-hair-frame'});
  // The carried mask now covers an earlier part of the projected arm. The ordinary hair shader
  // uses this warp immediately, so endpoint hiding must follow it without waiting for new inference.
  const report = tracker.update({...hair.input, timestampMs: 33, evidenceId: 'same-hair-frame',
    warp: {width: 160, height: 80, toMask: {a: 1, b: 0, c: 0, d: 1, tx: 20, ty: 0}}});
  assert.ok(report.negativeZM > initial.negativeZM + .015);
  assert.ok(report.negativeZM > -.094 && report.negativeZM + TEMPLE_ENDPOINT.fadeM < -.07);
  const clear = fixture();
  for (let timestampMs = 66; timestampMs <= 1000; timestampMs += 33)
    assert.equal(tracker.update({...clear.input, timestampMs, evidenceId: 'one-clear-frame'}).negativeZM, report.negativeZM,
      'reusing clear evidence still cannot reveal the hidden tail');
});

test('invalid, missing, out-of-image and unresolved projections are never evidence for a clear shaft', () => {
  for (const kind of ['missing', 'outside', 'unresolved', 'invalid'] as const) {
    const tracker = new TempleEndpointTracker(model, maximumZM), hair = fixture(); hair.paint(0, -.06, -.082);
    const initial = tracker.update(hair.input), clear = fixture();
    clear.input.paths = kind === 'missing' ? null : clear.input.paths!.map(path => ({...path,
      points: path.points.map(point => ({...point, x: kind === 'outside' ? -2 : kind === 'unresolved' ? 40 : NaN}))}));
    for (let timestampMs = 50; timestampMs <= 1000; timestampMs += 50)
      assert.equal(tracker.update({...clear.input, timestampMs, evidenceId: timestampMs}).negativeZM, initial.negativeZM, kind);
    clear.input.mask!.category.fill(1);
    assert.equal(new TempleEndpointTracker(model, maximumZM).update(clear.input).negativeCandidateZM, null, kind);
  }
});

test('subpixel foreshortening cannot turn one hair texel into a confident 3D endpoint', () => {
  const f = fixture(); f.input.mask!.category.fill(1);
  f.input.paths = f.input.paths!.map(path => ({...path, points: path.points.map((point, index) =>
    ({...point, x: 40 + index * .01, y: 20.5, radiusPx: .1}))}));
  assert.equal(new TempleEndpointTracker(model, maximumZM).update(f.input).negativeCandidateZM, null);
});

test('opposite arms shorten independently, and a strong new forward gap removes the entire tail immediately', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), f = fixture();
  f.paint(0, -.09, -.114); f.paint(1, -.065, -.086);
  const first = tracker.update(f.input); assert.ok(first.positiveZM > first.negativeZM);
  const next = fixture(); next.paint(0, -.05, -.072); next.paint(1, -.065, -.086);
  const report = tracker.update({...next.input, timestampMs: 33});
  assert.ok(report.negativeZM + TEMPLE_ENDPOINT.fadeM < -.05 && report.negativeZM > -.072);
  assert.equal(report.positiveZM, first.positiveZM);
});

test('a frame gap or abrupt view change invalidates reveal confidence without revealing the tail', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), hair = fixture(); hair.paint(0, -.06, -.082);
  const initial = tracker.update(hair.input), clear = fixture();
  tracker.update({...clear.input, timestampMs: 50, evidenceId: 1});
  tracker.update({...clear.input, timestampMs: 100, evidenceId: 2});
  for (const [timestampMs, yawDegrees] of [[150, 25], [800, 25], [850, 50]] as const) {
    assert.equal(tracker.update({...clear.input, timestampMs, yawDegrees, evidenceId: timestampMs}).negativeZM, initial.negativeZM);
  }
});

test('a warped mask uses matching sample positions and transverse shaft widths', () => {
  const normal = fixture(2, .5), warped = fixture(2, .5);
  normal.paint(0, -.06, -.084); warped.paint(0, -.06, -.084);
  const shifted = new Uint8Array(warped.input.mask!.category.length), width = warped.input.mask!.width;
  for (let y = 0; y < warped.input.mask!.height; y++) for (let x = 0; x < width - 10; x++)
    shifted[y * width + x + 10] = warped.input.mask!.category[y * width + x]!;
  warped.input.mask!.category = shifted;
  warped.input.warp = {width: 320, height: 160, toMask: {a: 1, b: 0, c: 0, d: 1, tx: 20, ty: 0}};
  const a = new TempleEndpointTracker(model, maximumZM).update(normal.input), b = new TempleEndpointTracker(model, maximumZM).update(warped.input);
  near(a.negativeZM, b.negativeZM); assert.equal(b.negativeState, 'tracking');
});

test('a resolved narrow cross-shaft gap terminates the tail with a fade contained in that gap', () => {
  for (const [scale, maskScale, lengthM] of [[1, 1, .003], [2, .5, .003], [.5, 1, .006], [1, .5, .006]] as const) {
    const f = fixture(scale, maskScale); f.paint(0, -.065, -.065 - lengthM);
    const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
    assert.equal(report.negativeState, 'tracking', `${scale}/${maskScale}/${lengthM}`);
    assert.ok(report.negativeZM > -.065 - lengthM && report.negativeZM < -.065);
    assert.ok(report.negativeFadeM > 0 && report.negativeFadeM < .005);
    assert.ok(report.negativeZM + report.negativeFadeM <= -.065 + 1e-10, 'the entire fade is covered');
    assert.equal(report.positiveZM, maximumZM); assert.equal(report.positiveFadeM, .005);
  }
});

test('a single mask texel and disconnected speckles cannot masquerade as a resolved crossing', () => {
  for (const columns of [[60], [60, 62, 64]]) {
    const f = fixture();
    for (const x of columns) f.input.mask!.category[20 * f.input.mask!.width + x] = 1;
    const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
    assert.equal(report.negativeCandidateZM, null); assert.equal(report.negativeZM, maximumZM);
  }
});

test('one longitudinal column is a real barrier only when its transverse footprint is fully resolved', () => {
  for (const kind of ['full', 'partial', 'collapsed'] as const) {
    const f = fixture();
    if (kind === 'collapsed') f.input.paths = f.input.paths!.map(path => ({...path,
      points: path.points.map(point => ({...point, y: point.y + .5, radiusPx: .1}))}));
    for (let y = 19; y <= (kind === 'partial' ? 20 : 21); y++) f.input.mask!.category[y * f.input.mask!.width + 60] = 1;
    const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
    if (kind === 'full') {
      assert.equal(report.negativeState, 'tracking');
      assert.ok(report.negativeZM > -.069 && report.negativeZM + report.negativeFadeM <= -.068 + 1e-10);
      assert.ok(report.negativeFadeM > 0 && report.negativeFadeM < .001);
    } else {
      assert.equal(report.negativeCandidateZM, null); assert.equal(report.negativeZM, maximumZM);
    }
  }
});

test('two fully covered longitudinal texels terminate a genuine narrow barrier, while partial-width speckles do not', () => {
  for (const fullWidth of [false, true]) {
    const f = fixture();
    f.input.paths = f.input.paths!.map(path => ({...path, points: path.points.map(point => ({...point, radiusPx: 2}))}));
    for (const x of [60, 61]) for (let y = 18; y <= (fullWidth ? 22 : 21); y++)
      f.input.mask!.category[y * f.input.mask!.width + x] = 1;
    const report = new TempleEndpointTracker(model, maximumZM).update(f.input);
    if (fullWidth) {
      assert.equal(report.negativeState, 'tracking');
      assert.ok(report.negativeZM > -.07 && report.negativeZM + report.negativeFadeM <= -.068 + 1e-10);
      assert.ok(report.negativeFadeM > 0 && report.negativeFadeM < .001);
    } else {
      assert.equal(report.negativeCandidateZM, null); assert.equal(report.negativeZM, maximumZM);
    }
  }
});

test('a narrow carried-mask crossing applies immediately and preserves its fade through dropout or reused clear evidence', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), hair = fixture(); hair.paint(0, -.095, -.101);
  tracker.update({...hair.input, evidenceId: 'narrow-hair'});
  const cut = tracker.update({...hair.input, timestampMs: 33, evidenceId: 'narrow-hair',
    warp: {width: 160, height: 80, toMask: {a: 1, b: 0, c: 0, d: 1, tx: 20, ty: 0}}});
  assert.ok(cut.negativeZM > -.081 && cut.negativeZM + cut.negativeFadeM <= -.075 + 1e-10);
  const clear = fixture();
  for (let timestampMs = 66; timestampMs <= 2000; timestampMs += 33) {
    const held = tracker.update({...clear.input, timestampMs, evidenceId: 'one-clear'});
    assert.equal(held.negativeZM, cut.negativeZM); assert.equal(held.negativeFadeM, cut.negativeFadeM);
  }
  for (const timestampMs of [2500, 5000, 30000]) {
    const held = tracker.update({...clear.input, mask: null, timestampMs});
    assert.equal(held.negativeZM, cut.negativeZM); assert.equal(held.negativeFadeM, cut.negativeFadeM);
  }
});

test('fresh clear release can reach a later narrow band without extending its terminal fade outside coverage', () => {
  const tracker = new TempleEndpointTracker(model, maximumZM), early = fixture(); early.paint(0, -.055, -.075);
  tracker.update(early.input);
  const rear = fixture(); rear.paint(0, -.095, -.101);
  let report = tracker.update({...rear.input, timestampMs: 50, evidenceId: 50});
  for (let timestampMs = 100; timestampMs <= 5000; timestampMs += 50)
    report = tracker.update({...rear.input, timestampMs, evidenceId: timestampMs});
  assert.equal(report.negativeState, 'tracking');
  assert.ok(report.negativeFadeM < .003 && report.negativeZM > -.101);
  assert.ok(report.negativeZM + report.negativeFadeM <= -.095 + 1e-10);
  const clear = fixture();
  for (let timestampMs = 5050; timestampMs <= 10000; timestampMs += 50)
    report = tracker.update({...clear.input, timestampMs, evidenceId: timestampMs});
  assert.equal(report.negativeZM, maximumZM); assert.equal(report.negativeFadeM, .005);
});
