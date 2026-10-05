/** arview capture: the portrait contract (qa/capture/portrait-contract.ts; ported from
 *  qa/portrait-preview-contract.test.mjs, its manifest test dropped with the manifest). */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {consoleLevel, eyeCrop, FACE_A_RELATIVE_PATH, FACE_A_SHA256} from './capture/portrait-contract.ts';

test('eye crop follows fixed source landmarks, retaining native pixels and staying in bounds', () => {
  const landmarks = []; landmarks[33] = {x: .3, y: .42}; landmarks[263] = {x: .69, y: .44};
  const a = eyeCrop(landmarks, 1024, 1024), b = eyeCrop(structuredClone(landmarks), 1024, 1024);
  assert.deepEqual(a, b); assert(a.width > 800 && a.height > 400);
  assert(a.x >= 0 && a.y >= 0 && a.x + a.width <= 1024 && a.y + a.height <= 1024);
  assert.throws(() => eyeCrop([], 1024, 1024), /Invalid/);
  landmarks[263].x = .301; landmarks[263].y = .42;
  assert.throws(() => eyeCrop(landmarks, 1024, 1024), /unsuitable/);
});

test('only the known successful MediaPipe CPU notice is informational', () => {
  assert.equal(consoleLevel('error', 'INFO: Created TensorFlow Lite XNNPACK delegate for CPU.'), 'info');
  assert.equal(consoleLevel('error', 'INFO: Could not initialize CPU.'), 'error');
  assert.equal(consoleLevel('error', 'THREE.WebGLProgram shader failed'), 'error');
  assert.equal(consoleLevel('warning', 'x'), 'warning');
});

test('face-a is read from ar/qa/fixtures and matches its pin (the only face image arview uses)', () => {
  const file = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', FACE_A_RELATIVE_PATH);
  const bytes = fs.readFileSync(file);
  assert.equal(bytes.length, 552034);
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), FACE_A_SHA256);
});
