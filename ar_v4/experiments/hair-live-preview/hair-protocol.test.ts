import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {HAIR_MODELS, hairModelById} from './models.ts';
import {assertDimensions, hashHairMasks, validateHairOutput, validateHairReady, validateHairRequest} from './hair-protocol.ts';
import type {HairCategoryOutput, HairFullOutput} from './hair-protocol.ts';

const model = HAIR_MODELS['hair-only'];
const expected = {sourceSHA256: 'a'.repeat(64), sequence: 4, width: 2, height: 2};
const output = (): HairFullOutput => ({...expected, model: model.id, modelSHA256: model.sha256, labels: [...model.labels],
  hairIndex: 1, category: new Uint8Array([0, 1, 1, 0]), confidence: new Float32Array([0, .5, 1, .25]), inferenceMs: 4, extractionMs: .2});

test('hair output is owned and retains exact source, labels and raw model values', () => {
  const raw = output(), validated = validateHairOutput(raw, model, expected);
  raw.category[0] = 1; raw.confidence[0] = 1;
  assert.equal(validated.category[0], 0); assert.equal(validated.confidence[0], 0);
  assert.equal(validated.sourceSHA256, expected.sourceSHA256); assert.equal(validated.sequence, expected.sequence);
  assert.deepEqual([...validated.confidence], [0, .5, 1, .25]);
});

test('another source, frame sequence, model or label mapping cannot pass validation', () => {
  for (const patch of [{sourceSHA256: 'b'.repeat(64)}, {sequence: 5}, {model: 'selfie-multiclass'},
    {modelSHA256: 'f'.repeat(64)}, {labels: ['hair', 'background']}, {hairIndex: 0}]) {
    assert.throws(() => validateHairOutput({...output(), ...patch}, model, expected));
  }
});

test('mask dimensions, storage, class values and confidence are checked without clamping', () => {
  const invalid = [
    {width: 4, height: 1}, {category: new Uint8Array([0, 1])}, {confidence: new Float32Array([.5])},
    {category: new Uint8Array([0, 2, 1, 0])}, {category: [0, 1, 1, 0]},
    ...[NaN, Infinity, -.001, 1.000001].map(value => ({confidence: new Float32Array([0, value, 1, .25])})),
    {inferenceMs: -1}, {extractionMs: Infinity},
  ];
  for (const patch of invalid) assert.throws(() => validateHairOutput({...output(), ...patch}, model, expected));
  assert.throws(() => assertDimensions(8192, 8192)); assert.throws(() => assertDimensions(0, 2));
});

test('the readiness contract enforces reviewed model, IMAGE mode and CPU semantics', () => {
  const ready = {type: 'ready', model: model.id, modelSHA256: model.sha256, labels: [...model.labels], hairIndex: 1,
    runningMode: 'IMAGE', delegate: 'CPU', initializationMs: 20};
  validateHairReady(ready, model);
  for (const patch of [{runningMode: 'VIDEO'}, {delegate: 'GPU'}, {modelSHA256: '0'.repeat(64)}, {labels: ['background']}]) {
    assert.throws(() => validateHairReady({...ready, ...patch}, model));
  }
});

test('raw hashes use actual class bytes and canonical little-endian confidence floats', async () => {
  const raw = output(), result = await hashHairMasks(raw), confidence = Buffer.alloc(16);
  [0, .5, 1, .25].forEach((value, index) => confidence.writeFloatLE(value, index * 4));
  assert.equal(result.categorySHA256, createHash('sha256').update(raw.category).digest('hex'));
  assert.equal(result.confidenceSHA256, createHash('sha256').update(confidence).digest('hex'));
  raw.confidence[0] = .25; assert.notEqual((await hashHairMasks(raw)).confidenceSHA256, result.confidenceSHA256);
});

test('request and model identities are restricted to pinned local models', () => {
  assert.equal(hairModelById('selfie-multiclass').sha256, 'c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0');
  assert.throws(() => hairModelById('arbitrary-model'));
  const valid = {type: 'initialize', requestId: 1, sessionNonce: 'unit-session-a', modelId: 'hair-only'};
  assert.equal(validateHairRequest(valid).type, 'initialize');
  for (const patch of [{requestId: 0}, {requestId: 1.5}, {sessionNonce: ''}, {modelId: 'arbitrary-model'}, {outputMode: 'confidence-only'}]) {
    assert.throws(() => validateHairRequest({...valid, ...patch}));
  }
});

test('category-only output keeps the exact category contract while omitting every confidence field and operation', async () => {
  const {confidence: _confidence, ...base} = output();
  const raw: HairCategoryOutput = {...base, outputMode: 'category-only'};
  const validated = validateHairOutput(raw, model, {...expected, outputMode: 'category-only'});
  const hashes = await hashHairMasks(validated);
  assert.deepEqual(hashes, {categorySHA256: createHash('sha256').update(raw.category).digest('hex')});
  assert.equal('confidence' in validated, false); assert.equal('confidenceSHA256' in hashes, false);
  raw.category.fill(1); assert.deepEqual([...validated.category], [0, 1, 1, 0]);
  for (const patch of [{sourceSHA256: 'b'.repeat(64)}, {sequence: 5}, {width: 4, height: 1},
    {category: new Uint8Array([0, 2, 1, 0])}, {category: new Uint8Array(2)}, {modelSHA256: 'b'.repeat(64)},
    {labels: ['hair', 'background']}, {confidence: undefined}, {confidenceSHA256: 'b'.repeat(64)}, {outputMode: undefined}])
    assert.throws(() => validateHairOutput({...raw, ...patch}, model, {...expected, outputMode: 'category-only'}));
});

test('mode negotiation defaults only legacy full requests and is explicit for category-only ready/output', () => {
  const request = {type: 'initialize', requestId: 1, sessionNonce: 'unit-session-a', modelId: model.id};
  const full = validateHairRequest(request), category = validateHairRequest({...request, outputMode: 'category-only'});
  assert.equal(full.type, 'initialize'); assert.equal(category.type, 'initialize');
  assert.equal(full.outputMode, 'full'); assert.equal(category.outputMode, 'category-only');
  const ready = {type: 'ready', model: model.id, modelSHA256: model.sha256, labels: [...model.labels], hairIndex: 1,
    runningMode: 'IMAGE', delegate: 'CPU', initializationMs: 20};
  validateHairReady({...ready, outputMode: 'category-only'}, model, 'CPU', 'category-only');
  assert.throws(() => validateHairReady(ready, model, 'CPU', 'category-only'), /output mode/);
  assert.throws(() => validateHairReady({...ready, outputMode: 'category-only'}, model), /output mode/);
  assert.throws(() => validateHairOutput({...output(), outputMode: 'category-only'}, model, expected), /output mode/);
});
