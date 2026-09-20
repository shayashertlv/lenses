import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {TempleCheekContactEstimator, MAX_TEMPLE_CHEEK_VERTICES} from '../src/render/temple-cheek-contact.ts';
import type {TempleCheekPoint} from '../src/render/temple-cheek-contact.ts';
import type {Landmark} from '../src/face/protocol.ts';

function fixture(): {estimator: TempleCheekContactEstimator; landmarks: Landmark[]} {
  const positions = new Float64Array(468 * 3);
  for (const [i, p] of [[-5, 5, 0], [5, 5, 0], [5, -5, 0], [-5, -5, 0]].entries()) positions.set(p, i * 3);
  // Canonical height deliberately disagrees with observed eyes: it must not define the projected boundary.
  positions[33 * 3 + 1] = 4.5; positions[263 * 3 + 1] = 4.5;
  const estimator = new TempleCheekContactEstimator(positions, [0, 1, 2, 0, 2, 3]);
  const landmarks: Landmark[] = Array.from({length: 468}, () => ({x: .5, y: .5, z: 0}));
  for (const [i, p] of [[.25, .1], [.75, .1], [.75, .9], [.25, .9]].entries()) landmarks[i] = {x: p[0]!, y: p[1]!, z: 0};
  landmarks[33] = {x: .32, y: .4, z: 0}; landmarks[263] = {x: .68, y: .4, z: 0};
  landmarks[152] = {x: .5, y: .86, z: 0};
  return {estimator, landmarks};
}
function equalPoints(actual: readonly TempleCheekPoint[], expected: readonly TempleCheekPoint[]): void {
  assert.equal(actual.length, expected.length);
  for (const p of actual) assert.ok(expected.some(q => Math.hypot(p.x - q.x, p.y - q.y) < 1e-10),
    `Unexpected vertex ${p.x}, ${p.y}`);
}

test('observed eye line defines cheek eligibility without inventing any shaft cutoff', () => {
  const {estimator, landmarks} = fixture(), before = JSON.stringify(landmarks);
  const contact = estimator.evaluate(landmarks);
  assert.ok(contact);
  equalPoints(contact.polygon, [{x: .25, y: .4}, {x: .75, y: .4}, {x: .75, y: .9}, {x: .25, y: .9}]);
  assert.deepEqual(Object.keys(contact), ['polygon'], 'region coverage does not imply an occlusion depth or a rear cut');
  assert.equal(JSON.stringify(landmarks), before, 'observed geometry is never edited');
});

test('narrow and wide observed outlines map to different cheek regions at the same eye level', () => {
  const {estimator, landmarks} = fixture();
  const narrow = estimator.evaluate(landmarks)!;
  const widened = landmarks.map(p => ({...p, x: .5 + (p.x - .5) * 1.4}));
  const wide = estimator.evaluate(widened)!;
  assert.equal(Math.min(...narrow.polygon.map(p => p.x)), .25);
  assert.ok(Math.abs(Math.min(...wide.polygon.map(p => p.x)) - .15) < 1e-12);
  assert.ok(Math.abs(Math.max(...wide.polygon.map(p => p.x)) - .85) < 1e-12);
  assert.equal(Math.min(...wide.polygon.map(p => p.y)), .4);
  assert.equal(Math.max(...wide.polygon.map(p => p.y)), .9);
});

test('a sloped observed eye line clips the actual outline rather than an axis-aligned eye-height box', () => {
  const {estimator, landmarks} = fixture();
  landmarks[33] = {x: .25, y: .3, z: 0}; landmarks[263] = {x: .75, y: .5, z: 0};
  const contact = estimator.evaluate(landmarks)!;
  equalPoints(contact.polygon, [{x: .25, y: .3}, {x: .75, y: .5}, {x: .75, y: .9}, {x: .25, y: .9}]);
});

test('mirroring, roll and affine image changes preserve the same observed cheek region', () => {
  const {estimator, landmarks} = fixture(), base = estimator.evaluate(landmarks)!;
  const angle = .7;
  const transforms = [
    (p: TempleCheekPoint) => ({x: 1 - p.x, y: p.y}),
    (p: TempleCheekPoint) => ({x: .5 + Math.cos(angle) * (p.x - .5) - Math.sin(angle) * (p.y - .5),
      y: .5 + Math.sin(angle) * (p.x - .5) + Math.cos(angle) * (p.y - .5)}),
    (p: TempleCheekPoint) => ({x: 1 - p.x, y: 1 - p.y}),
    (p: TempleCheekPoint) => ({x: p.x * 1.3 - .2, y: p.y * .7 + .1}),
  ];
  for (const transform of transforms) {
    const result = estimator.evaluate(landmarks.map(p => ({...p, ...transform(p)})));
    assert.ok(result);
    equalPoints(result.polygon, base.polygon.map(transform));
  }
});

test('current-frame changes replace coverage immediately and invalid/no-region frames clear it', () => {
  const {estimator, landmarks} = fixture(), first = estimator.evaluate(landmarks)!;
  const shifted = landmarks.map(p => ({...p, x: p.x + .15, y: p.y - .1}));
  equalPoints(estimator.evaluate(shifted)!.polygon, first.polygon.map(p => ({x: p.x + .15, y: p.y - .1})));
  assert.equal(estimator.evaluate([]), null);
  const noRegion = landmarks.map(p => ({...p}));
  noRegion[33]!.y = .95; noRegion[263]!.y = .95; noRegion[152]!.y = 1.1;
  assert.equal(estimator.evaluate(noRegion), null, 'an eye line below the entire contour leaves no eligible region');
  assert.deepEqual(estimator.evaluate(landmarks), first, 'there is no retained coverage or fitted history');
});

test('cropped observed outlines retain normalized geometry beyond the image instead of clamping it', () => {
  const {estimator, landmarks} = fixture(), shifted = landmarks.map(p => ({...p, x: p.x - .5}));
  const result = estimator.evaluate(shifted);
  assert.ok(result);
  assert.equal(Math.min(...result.polygon.map(p => p.x)), -.25);
  assert.equal(Math.max(...result.polygon.map(p => p.x)), .25);
});

test('missing points, crossing contours, collapsed eye lines and unusable topology decline coverage', () => {
  const {estimator, landmarks} = fixture();
  assert.equal(estimator.evaluate([]), null);
  const corrupt = landmarks.map(p => ({...p})); corrupt[33]!.x = NaN;
  assert.equal(estimator.evaluate(corrupt), null);
  const corruptBoundary = landmarks.map(p => ({...p})); corruptBoundary[2]!.y = Infinity;
  assert.equal(estimator.evaluate(corruptBoundary), null);
  const crossing = landmarks.map(p => ({...p})); [crossing[1], crossing[2]] = [crossing[2]!, crossing[1]!];
  assert.equal(estimator.evaluate(crossing), null);
  const flatEye = landmarks.map(p => ({...p})); flatEye[263] = {...flatEye[33]!};
  assert.equal(estimator.evaluate(flatEye), null);
  const flatChin = landmarks.map(p => ({...p})); flatChin[152]!.y = .4;
  assert.equal(estimator.evaluate(flatChin), null);
  assert.equal(new TempleCheekContactEstimator([], []).evaluate(landmarks), null);
});

test('the shipped canonical exterior produces a bounded observed cheek polygon', async () => {
  const canonical = JSON.parse(await readFile(new URL('../public/models/canonical-face.json', import.meta.url), 'utf8')) as {positions: number[]; indices: number[]};
  const estimator = new TempleCheekContactEstimator(canonical.positions, canonical.indices);
  const landmarks: Landmark[] = Array.from({length: canonical.positions.length / 3}, (_, i) => ({
    x: .5 + canonical.positions[i * 3]! / 25, y: .5 - canonical.positions[i * 3 + 1]! / 25, z: 0,
  }));
  const contact = estimator.evaluate(landmarks);
  assert.ok(contact && contact.polygon.length >= 12 && contact.polygon.length <= MAX_TEMPLE_CHEEK_VERTICES);
  const eyeY = (landmarks[33]!.y + landmarks[263]!.y) / 2;
  assert.ok(contact.polygon.every(p => p.y >= eyeY - 1e-10));
});
