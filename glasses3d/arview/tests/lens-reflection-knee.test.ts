/** The canonical lens's reflection knee is absolute (eyewear-reflection.ts LENS_REFLECTION_KNEE; review AR-R2,
 *  2026-09-30). lensenv, the owner's try-on link setting (automation tryon.py; renderer.ts: envMapIntensity 0.8 x lensenv),
 *  multiplies the lens reflection BEFORE the reflection limit, and the pipeline's lensenv recommendation (lens_colour.py:
 *  env = LUMA(photo - T bg) / LUMA(A)) assumes the runtime draws T bg + lensenv A. The first limit started its shoulder at
 *  half the headroom, so it bit ordinary mirror reflections: invu-astra2, accepted "perfect" live at lensenv 1.49, drew
 *  34.9 % of its visible lens more than 2 sRGB levels darker than linear scaling (p90 17.8, max 56 levels), and the
 *  recommended 1.49 delivered mean luma 0.390 where it assumed 0.418. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {
  CRYSTAL_REFLECTION_KNEE, LENS_REFLECTION_KNEE, LENS_REFLECTION_KNEE_LEVEL, limitReflection, REFLECTION_KNEE, REFLECTION_WHITE,
  reflectionLimitCall,
} from '../src/render/eyewear-reflection.ts';

type Rgb = [number, number, number];
const linear = (value: number): number => {const v = value / 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;};
const srgb = (value: number): number => 255 * (value <= .0031308 ? 12.92 * value : 1.055 * value ** (1 / 2.4) - .055);
const scale = (rgb: readonly number[], k: number): Rgb => [rgb[0]! * k, rgb[1]! * k, rgb[2]! * k];

test('a mirror reflection of 0.6 linear over a dark lens look-through (0.05) is added unchanged', () => {
  assert.deepEqual(limitReflection([.6, .6, .6], [.05, .05, .05], LENS_REFLECTION_KNEE), [.6, .6, .6]);
  assert.deepEqual(limitReflection([.6, .45, .3], [.05, .03, .02], LENS_REFLECTION_KNEE), [.6, .45, .3], 'a tinted mirror too');
  // The first limit's knee (half the headroom: 0.425 here), which the crystal keeps, compresses it: the knee is per surface.
  const [crystal] = limitReflection([.6, .6, .6], [.05, .05, .05], CRYSTAL_REFLECTION_KNEE);
  assert.ok(crystal < .59, `${crystal}`);
});

test('lensenv scales invu-astra2\'s ordinary mirror reflection linearly at every recommended lensenv', () => {
  // invu-astra2 at lensenv 1 (the harness's blue #3a4f6e and dim-skin #96695a fixtures, front view): the reflection's
  // peak channel p50 / p75 0.292 / 0.424 linear, the look-through T p50 (0.033, 0.119, 0.070), here over dim skin.
  const through: Rgb = [.033 * linear(150), .119 * linear(105), .070 * linear(90)];
  for (const lensenv of [1, 1.23, 1.47, 1.49, 1.56]) {           // the recommendations recorded for invu / oakley-astra2
    for (const peak of [.292, .424]) {
      const reflected = scale([peak, peak * .8, peak * .6], lensenv);
      assert.deepEqual(limitReflection(reflected, through, LENS_REFLECTION_KNEE), reflected, `lensenv ${lensenv} reflection ${peak}`);
    }
  }
  // Its p90 (0.565) at 1.49 totals 0.86: it is compressed, by about 2 sRGB levels, where the first knee took about 17.
  const reflected = scale([.565, .565, .565], 1.49), top = Math.max(...through);
  const [lens] = limitReflection(reflected, through, LENS_REFLECTION_KNEE), [first] = limitReflection(reflected, through, CRYSTAL_REFLECTION_KNEE);
  const drop = (value: number) => srgb(top + reflected[0]) - srgb(top + value);
  assert.ok(drop(lens) > 0 && drop(lens) < 2.5, `lens knee ${drop(lens).toFixed(2)} levels`);
  assert.ok(drop(first) > 12, `first knee ${drop(first).toFixed(2)} levels`);
});

test('below a total of 0.8 linear the lens reflection is exact, so lensenv k gives k times the reflection', () => {
  let seed = 7;
  const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  let checked = 0;
  for (let sample = 0; sample < 20000; sample++) {
    const through: Rgb = [random() * .4, random() * .4, random() * .4], reflected: Rgb = [random() * .7, random() * .7, random() * .7];
    const total = Math.max(...through) + Math.max(...reflected);
    const out = limitReflection(reflected, through, LENS_REFLECTION_KNEE);
    if (total <= LENS_REFLECTION_KNEE_LEVEL) {
      assert.deepEqual(out, reflected, `through ${through} reflection ${reflected}`);
      const k = LENS_REFLECTION_KNEE_LEVEL / total * random();
      assert.deepEqual(limitReflection(scale(reflected, k), through, LENS_REFLECTION_KNEE), scale(out, k), 'linear in lensenv');
      checked++;
    } else {
      assert.ok(Math.max(...through) + Math.max(...out) < REFLECTION_WHITE, 'above it the limit still holds');
      assert.ok(Math.max(...out) >= Math.min(Math.max(...reflected), LENS_REFLECTION_KNEE_LEVEL - Math.max(...through)) - 1e-12,
        'never darker than the knee: the shoulder only compresses what would run past 0.8');
    }
  }
  assert.ok(checked > 2000 && checked < 18000, `${checked} of 20000 below the knee level`);
});

test('the crystal keeps the first limit exactly: its twin\'s pixels do not move with the lens knee', () => {
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

test('each surface calls the GLSL with its own knee, and a Python port can read the constants as literals', async () => {
  assert.equal(reflectionLimitCall('a', 'b', LENS_REFLECTION_KNEE), 'eyewearReflectionLimit( a, b, 0.0000, 0.8000 )');
  assert.equal(reflectionLimitCall('a', 'b', CRYSTAL_REFLECTION_KNEE), 'eyewearReflectionLimit( a, b, 0.5000, 0.0000 )');
  assert.ok(Object.isFrozen(LENS_REFLECTION_KNEE) && Object.isFrozen(CRYSTAL_REFLECTION_KNEE));
  // automation reads these as text: appearance.py's crystal port pins REFLECTION_WHITE and REFLECTION_KNEE (the crystal's,
  // tests/test_appearance_reflection_limit.py); a lens port (lens_colour's lensenv recommendation) needs LENS_REFLECTION_KNEE_LEVEL.
  const source = await readFile(new URL('../src/render/eyewear-reflection.ts', import.meta.url), 'utf8');
  for (const [name, value] of [['REFLECTION_WHITE', REFLECTION_WHITE], ['LENS_REFLECTION_KNEE_LEVEL', LENS_REFLECTION_KNEE_LEVEL],
    ['REFLECTION_KNEE', REFLECTION_KNEE]] as const) {
    const match = new RegExp(`^export const ${name} = ([0-9.]+);$`, 'm').exec(source);
    assert.ok(match, name); assert.equal(Number(match[1]), value, name);
  }
  assert.match(source, /The crystal's knee, a share of the headroom/, 'REFLECTION_KNEE is documented as the crystal\'s');
});
