import test from 'node:test';
import assert from 'node:assert/strict';
import {consoleLevel, eyeCrop, validatePortraitManifest} from './portrait-preview-contract.mjs';

test('eye crop follows fixed source landmarks, retaining native pixels and staying in bounds', () => {
  const landmarks = []; landmarks[33] = {x: .3, y: .42}; landmarks[263] = {x: .69, y: .44};
  const a = eyeCrop(landmarks, 1024, 1024), b = eyeCrop(structuredClone(landmarks), 1024, 1024);
  assert.deepEqual(a, b); assert(a.width > 800 && a.height > 400);
  assert(a.x >= 0 && a.y >= 0 && a.x + a.width <= 1024 && a.y + a.height <= 1024);
  assert.throws(() => eyeCrop([], 1024, 1024), /Invalid/);
  landmarks[263].x = .301; landmarks[263].y = .42;
  assert.throws(() => eyeCrop(landmarks, 1024, 1024), /unsuitable/);
});

test('portrait manifest requires exact model and approved synthetic fixture identities', () => {
  const valid = {schema_version: 1, model_sha256: 'a'.repeat(64), width_mm: 145,
    fixture: {id: 'face-a', synthetic: true, sha256: 'b'.repeat(64)}};
  assert.equal(validatePortraitManifest(valid), valid);
  for (const invalid of [{...valid, model_sha256: ''}, {...valid, width_mm: NaN},
    {...valid, fixture: {...valid.fixture, id: '../private'}}, {...valid, fixture: {...valid.fixture, synthetic: false}}]) {
    assert.throws(() => validatePortraitManifest(invalid), /Invalid/);
  }
});

test('only the known successful MediaPipe CPU notice is informational', () => {
  assert.equal(consoleLevel('error', 'INFO: Created TensorFlow Lite XNNPACK delegate for CPU.'), 'info');
  assert.equal(consoleLevel('error', 'INFO: Could not initialize CPU.'), 'error');
  assert.equal(consoleLevel('error', 'THREE.WebGLProgram shader failed'), 'error');
});
