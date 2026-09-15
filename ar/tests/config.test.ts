import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULT_CONFIG, describeConfig, parseConfig} from '../src/config.ts';

test('an empty search yields the accepted defaults', () => {
  assert.deepEqual(parseConfig(''), DEFAULT_CONFIG);
  assert.deepEqual(parseConfig('?unknown=1'), DEFAULT_CONFIG);
  assert.match(describeConfig(DEFAULT_CONFIG), /CPU delegate.*capture 1280 px max edge · hair start z -0.02 m · GPU completion gate on · camera exposure auto.*guard on · continuity cut on \(hair run ≥ 10 px/);
});

test('every lever parses with its bounds and its off spelling', () => {
  const config = parseConfig('?face=gpu&capture=960&hairz=-0.03&sync=0&exposure=312&guard=0&continuity=off&hairrun=16&eyewear=tom-ford-clear&hairModel=selfie-multiclass&hair=0');
  assert.deepEqual(config, {faceDelegate: null, captureMaxEdge: 960, hairStartZ: -0.03, sync: false, exposure: 312, guard: false, continuity: false,
    continuityRunPx: 16, eyewear: 'tom-ford-clear', hairModel: 'selfie-multiclass', hair: false});
  assert.match(describeConfig(config), /GPU-then-CPU \(\?face=gpu\).*capture 960 px max edge \(\?capture=\).*OFF \(\?sync=0\).*locked at 312 × 100 µs.*guard OFF.*continuity cut OFF/);
  const out = parseConfig('?capture=100&hairz=5&exposure=auto&hairrun=0&face=cpu&hair=1');
  assert.deepEqual(out, {...DEFAULT_CONFIG, hair: true});
  assert.equal(parseConfig('?exposure=0').exposure, null);
  assert.equal(parseConfig('?capture=1000.4').captureMaxEdge, 1000);
});
