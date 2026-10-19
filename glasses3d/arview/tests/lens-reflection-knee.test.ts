import {test} from 'node:test';
import assert from 'node:assert/strict';
import {
  CRYSTAL_REFLECTION_KNEE, limitReflection, REFLECTION_KNEE, REFLECTION_WHITE,
  reflectionLimitCall,
} from '../src/render/eyewear-reflection.ts';

type Rgb = [number, number, number];
const scale = (rgb: readonly number[], k: number): Rgb => [rgb[0]! * k, rgb[1]! * k, rgb[2]! * k];

test('the crystal keeps the first limit exactly', () => {
  // The first limit's reference (knee = 0.5 x headroom), as shipped before review AR-R2.
  const first = (reflected: Rgb, through: Rgb): Rgb => {
    const peak = Math.max(...reflected), headroom = Math.max(REFLECTION_WHITE - Math.max(...through), 0);
    const knee = .5 * headroom, span = headroom - knee;
    if (peak <= knee) return [...reflected];
    if (span <= 1e-6) return [0, 0, 0];
    return scale(reflected, (knee + span * (1 - Math.exp(-(peak - knee) / span))) / peak);
  };
  assert.equal(REFLECTION_KNEE, .5); assert.deepEqual(CRYSTAL_REFLECTION_KNEE, {share: REFLECTION_KNEE, level: 0});
  for (const t of [0, .02, .1, .3, .45, .7, .85, .9, .95]) for (let r = 0; r <= 4; r += .037) {
    const through: Rgb = [t, t * .9, t * .8], reflected: Rgb = [r * .7, r, r * .9];
    assert.deepEqual(limitReflection(reflected, through, CRYSTAL_REFLECTION_KNEE), first(reflected, through), `through ${t} reflection ${r}`);
    // Without a knee it is the crystal's: automation's node cross-check calls limitReflection(r, t) against appearance.py.
    assert.deepEqual(limitReflection(reflected, through), first(reflected, through));
  }
});

test('the crystal calls the GLSL with its frozen knee', () => {
  assert.equal(reflectionLimitCall('a', 'b', CRYSTAL_REFLECTION_KNEE), 'eyewearReflectionLimit( a, b, 0.5000, 0.0000 )');
  assert.ok(Object.isFrozen(CRYSTAL_REFLECTION_KNEE));
});
