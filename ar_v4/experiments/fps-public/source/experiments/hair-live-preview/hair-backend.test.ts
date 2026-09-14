import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseHairDelegate} from './hair-backend.ts';

test('hardware acceleration is used while identified software renderers stay on CPU', () => {
  assert.equal(chooseHairDelegate('ANGLE (Intel Arc 140T, Direct3D11)'), 'GPU');
  assert.equal(chooseHairDelegate('ANGLE (NVIDIA GeForce, Direct3D11)'), 'GPU');
  for (const name of ['ANGLE (Google, SwiftShader Device)', 'llvmpipe (LLVM)', 'lavapipe', 'Microsoft Basic Render Driver']) {
    assert.equal(chooseHairDelegate(name), 'CPU');
  }
  assert.equal(chooseHairDelegate(null, false), 'CPU');
  // Restricted renderer identity still permits a GPU attempt; startup owns a fresh CPU retry.
  assert.equal(chooseHairDelegate(null), 'GPU');
});
