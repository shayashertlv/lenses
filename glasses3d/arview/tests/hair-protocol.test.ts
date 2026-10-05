import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {HAIR_MODELS, getHairModel} from '../src/hair/models.ts';
import {assertDimensions, hashHairMask, validateHairOutput, validateHairReady, validateHairRequest} from '../src/hair/protocol.ts';
import type {HairOutput} from '../src/hair/protocol.ts';

const model = HAIR_MODELS['hair-only'];
const expected = {sourceSHA256: 'a'.repeat(64), sequence: 4, width: 2, height: 2, delegate: 'CPU' as const};
const output = (): HairOutput => ({...expected, model: model.id, modelSHA256: model.sha256, labels: [...model.labels],
  hairIndex: 1, category: new Uint8Array([0, 1, 1, 0]), inferenceMs: 4, extractionMs: .2});

test('hair output is owned and retains the exact source, labels and raw model values', () => {
  const raw = output(), validated = validateHairOutput(raw, model, expected);
  raw.category[0] = 1;
  assert.equal(validated.category[0], 0);
  assert.equal(validated.sourceSHA256, expected.sourceSHA256); assert.equal(validated.sequence, expected.sequence);
  assert.deepEqual([...validated.category], [0, 1, 1, 0]); assert.equal(validated.delegate, 'CPU');
  assert.equal('confidence' in validated, false);
});

test('another source, frame sequence, model, label mapping or delegate cannot pass validation', () => {
  for (const patch of [{sourceSHA256: 'b'.repeat(64)}, {sequence: 5}, {model: 'selfie-multiclass'},
    {modelSHA256: 'f'.repeat(64)}, {labels: ['hair', 'background']}, {hairIndex: 0}, {delegate: 'GPU'}]) {
    assert.throws(() => validateHairOutput({...output(), ...patch}, model, expected));
  }
});

test('mask dimensions, storage and class values are checked without clamping', () => {
  const invalid = [
    {width: 4, height: 1}, {category: new Uint8Array([0, 1])}, {category: new Uint8Array([0, 2, 1, 0])}, {category: [0, 1, 1, 0]},
    {inferenceMs: -1}, {extractionMs: Infinity},
  ];
  for (const patch of invalid) assert.throws(() => validateHairOutput({...output(), ...patch}, model, expected));
  assert.throws(() => assertDimensions(8192, 8192)); assert.throws(() => assertDimensions(0, 2));
});

test('the readiness contract enforces the pinned model, IMAGE mode and the requested delegate', () => {
  const ready = {type: 'ready', model: model.id, modelSHA256: model.sha256, labels: [...model.labels], hairIndex: 1,
    runningMode: 'IMAGE', delegate: 'CPU', initializationMs: 20};
  validateHairReady(ready, model, 'CPU');
  validateHairReady({...ready, delegate: 'GPU'}, model, 'GPU');
  for (const patch of [{runningMode: 'VIDEO'}, {delegate: 'GPU'}, {modelSHA256: '0'.repeat(64)}, {labels: ['background']}]) {
    assert.throws(() => validateHairReady({...ready, ...patch}, model, 'CPU'));
  }
});

test('the mask hash covers the actual category bytes', async () => {
  const raw = output(), result = await hashHairMask(raw);
  assert.deepEqual(result, {categorySHA256: createHash('sha256').update(raw.category).digest('hex')});
  raw.category[0] = 1; assert.notEqual((await hashHairMask(raw)).categorySHA256, result.categorySHA256);
});

test('request and model identities are restricted to the pinned models and an explicit delegate', () => {
  assert.equal(getHairModel('selfie-multiclass').sha256, 'c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0');
  assert.throws(() => getHairModel('arbitrary-model'));
  const valid = {type: 'initialize', requestId: 1, sessionNonce: 'unit-session-a', modelId: 'hair-only', delegate: 'CPU'};
  assert.equal(validateHairRequest(valid).type, 'initialize');
  for (const patch of [{requestId: 0}, {requestId: 1.5}, {sessionNonce: ''}, {modelId: 'arbitrary-model'}, {delegate: 'NPU'}, {delegate: undefined}]) {
    assert.throws(() => validateHairRequest({...valid, ...patch}));
  }
});
