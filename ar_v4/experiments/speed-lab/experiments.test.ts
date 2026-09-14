import assert from 'node:assert/strict';
import test from 'node:test';
import {DEFAULT_FACE_DELEGATE, describeSpeedExperiments, parseSpeedExperiments} from './experiments.ts';

test('the accepted default is the CPU face landmarker with no other experiment active', () => {
  assert.equal(DEFAULT_FACE_DELEGATE, 'CPU');
  assert.deepEqual(parseSpeedExperiments(''), {faceDelegate: 'CPU', hairMaxEdge: null, transmissionResolutionScale: 1});
  assert.deepEqual(parseSpeedExperiments('?face=cpu'), parseSpeedExperiments(''));
  assert.equal(describeSpeedExperiments(parseSpeedExperiments('')), '');
});
test('?face=gpu restores the previous GPU-then-CPU order and is described as a deviation', () => {
  const value = parseSpeedExperiments('?face=GPU');
  assert.equal(value.faceDelegate, null);
  assert.match(describeSpeedExperiments(value), /previous GPU-then-CPU order/);
});
test('unknown face values and out-of-range measurement options fall back to the accepted defaults', () => {
  assert.equal(parseSpeedExperiments('?face=npu').faceDelegate, 'CPU');
  assert.deepEqual(parseSpeedExperiments('?hair=64&tx=0'), {faceDelegate: 'CPU', hairMaxEdge: null, transmissionResolutionScale: 1});
  assert.deepEqual(parseSpeedExperiments('?hair=640&tx=0.5'), {faceDelegate: 'CPU', hairMaxEdge: 640, transmissionResolutionScale: 0.5});
  assert.match(describeSpeedExperiments(parseSpeedExperiments('?hair=640&tx=0.5')), /640 px max edge; lens transmission target at 0\.5x/);
});
