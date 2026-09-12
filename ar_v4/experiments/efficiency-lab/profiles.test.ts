import {test} from 'node:test';
import assert from 'node:assert/strict';
import {initialPipeline, PIPELINES, PROFILES, previewSearch, studyPipelines, usesBaseRenderer} from './profiles.ts';

test('rate experiments retain original G rendering and input options', () => {
  for (const [id, rate] of [['rate12', 12], ['rate10', 10], ['rate8', 8]] as const) {
    assert.equal(usesBaseRenderer(id), true);
    assert.equal(PROFILES[id].captureRateHz, rate);
    assert.deepEqual(PROFILES[id].options, PROFILES.g.options);
    assert.equal(PROFILES[id].mode, PROFILES.g.mode);
    assert.equal(PROFILES[id].leanInputs, false);
    assert.equal(PROFILES[id].deferPrefetch, false);
  }
  assert.equal(PROFILES.g.captureRateHz, null);
});

test('only an explicit valid link selects a candidate; ordinary entry stays G', () => {
  assert.equal(initialPipeline(''), 'g');
  assert.equal(initialPipeline('?pipeline=invalid'), 'g');
  assert.equal(initialPipeline('?pipeline=rate12'), 'rate12');
  assert.equal(initialPipeline('?pipeline=rate10'), 'rate10');
  assert.equal(initialPipeline('?pipeline=rate8'), 'rate8');
  assert.equal(initialPipeline('?pipeline=mask'), 'mask');
});

test('P changes only hair extraction and keeps G input, scheduling and renderer settings uncapped', () => {
  assert.equal(usesBaseRenderer('mask'), true);
  assert.deepEqual(PROFILES.mask.options, PROFILES.g.options);
  for (const key of ['mode', 'leanInputs', 'deferPrefetch', 'captureRateHz'] as const)
    assert.equal(PROFILES.mask[key], PROFILES.g[key]);
  assert.equal(PROFILES.mask.captureRateHz, null);
  assert.equal(PROFILES.mask.hairExtractionMode, 'direct');
  assert.equal(PROFILES.g.hairExtractionMode, 'sdk');
});

test('review study keeps G as default and accepts only its own explicit candidate choices', () => {
  assert.deepEqual(studyPipelines('?study=review'), ['g','publish','region','lens','ui']);
  assert.equal(initialPipeline('?study=review'), 'g');
  assert.equal(initialPipeline('?study=review&pipeline=mask'), 'g');
  for (const pipeline of ['publish','region','lens','ui']) assert.equal(initialPipeline('?study=review&pipeline='+pipeline), pipeline);
  assert.deepEqual(studyPipelines('?study=mask'), ['g','mask']);
  assert.equal(initialPipeline('?study=mask&pipeline=mask'), 'mask');
  assert.equal(initialPipeline('?study=mask&pipeline=lens'), 'g');
  assert.deepEqual(studyPipelines(''), PIPELINES);
  assert.equal(initialPipeline(''), 'g');
});

test('Q and T independently retain G rendering, input, extraction and scheduling', () => {
  for (const id of ['publish','ui'] as const) {
    assert.equal(usesBaseRenderer(id), true);
    assert.deepEqual(PROFILES[id].options, PROFILES.g.options);
    for (const key of ['mode','leanInputs','deferPrefetch','captureRateHz','hairExtractionMode'] as const)
      assert.equal(PROFILES[id][key], PROFILES.g[key]);
  }
  for (const id of PIPELINES) {
    assert.equal(PROFILES[id].suppressUnchangedPublication, id==='publish');
    assert.equal(PROFILES[id].throttleUi, id==='ui');
    assert.equal(PROFILES[id].options.cropBranchReadback, id==='region');
    assert.equal(PROFILES[id].options.omitBranchLenses, id==='lens');
  }
  assert.equal(usesBaseRenderer('region'), false); assert.equal(usesBaseRenderer('lens'), false);
});

test('U changes only early hair-worker release while preserving all G rendering and quality options', () => {
  const {detail: _gDetail, releaseHairWorkerEarly: _gRelease, ...baseline} = PROFILES.g;
  const {detail: _uDetail, releaseHairWorkerEarly: release, ...candidate} = PROFILES['hair-release'];
  assert.deepEqual(candidate, baseline); assert.equal(release, true);
  assert.equal(usesBaseRenderer('hair-release'), true);
  for (const id of PIPELINES) assert.equal(PROFILES[id].releaseHairWorkerEarly, id === 'hair-release');
});

test('hair delivery study exposes exactly G and U and never selects U implicitly', () => {
  assert.deepEqual(studyPipelines('?study=hair-delivery'), ['g', 'hair-release']);
  assert.equal(initialPipeline('?study=hair-delivery'), 'g');
  assert.equal(initialPipeline('?study=hair-delivery&pipeline=hair-release'), 'hair-release');
  assert.equal(initialPipeline('?study=hair-delivery&pipeline=publish'), 'g');
  assert.equal(initialPipeline('?study=review&pipeline=hair-release'), 'g');
});

test('per-image study isolates three uncapped changes and leaves G admission and protections intact', () => {
  assert.deepEqual(studyPipelines('?study=per-image'), ['g', 'mask-bytes', 'gl-state', 'word-compose']);
  assert.equal(initialPipeline('?study=per-image'), 'g');
  assert.equal(initialPipeline('?study=per-image&pipeline=hair-release'), 'g');
  for (const id of ['mask-bytes', 'gl-state', 'word-compose'] as const) {
    assert.equal(initialPipeline('?study=per-image&pipeline='+id), id);
    for (const key of ['mode', 'leanInputs', 'deferPrefetch', 'captureRateHz', 'releaseHairWorkerEarly', 'throttleUi', 'suppressUnchangedPublication'] as const)
      assert.equal(PROFILES[id][key], PROFILES.g[key], `${id}: ${key}`);
    const expected = {...PROFILES.g.options, ownedPackState: id === 'gl-state', wordCompose: id === 'word-compose'};
    assert.deepEqual(PROFILES[id].options, expected);
    assert.equal(PROFILES[id].hairExtractionMode, id === 'mask-bytes' ? 'rgba8' : 'sdk');
    assert.equal(usesBaseRenderer(id), id === 'mask-bytes');
  }
});

test('focused preview exposes only G and V while requiring explicit selection of V', () => {
  assert.deepEqual(studyPipelines('?study=mask-preview'), ['g', 'mask-bytes']);
  assert.equal(initialPipeline('?study=mask-preview'), 'g');
  assert.equal(initialPipeline('?study=mask-preview&pipeline=mask-bytes'), 'mask-bytes');
  for (const pipeline of PIPELINES.filter(id => id !== 'g' && id !== 'mask-bytes'))
    assert.equal(initialPipeline('?study=mask-preview&pipeline=' + pipeline), 'g');
});

test('mobile entry opens the focused preview and keeps explicit historical URLs available', () => {
  for (const search of ['', '?study=review']) {
    const normalized = previewSearch(search, true);
    assert.equal(new URLSearchParams(normalized).get('study'), 'mask-preview');
    assert.equal(initialPipeline(normalized), 'g');
    assert.equal(previewSearch(normalized, true), normalized);
  }
  const selected = new URLSearchParams(previewSearch('?study=review&pipeline=mask-bytes&capture=scalar', true));
  assert.equal(selected.get('study'), 'mask-preview');
  assert.equal(selected.get('pipeline'), 'mask-bytes'); assert.equal(selected.get('capture'), 'scalar');
  for (const search of ['?study=review&legacy=1', '?study=per-image', '?study=hair-delivery', '?study=mask-preview'])
    assert.equal(previewSearch(search, true), search);
  for (const search of ['', '?study=review', '?study=review&pipeline=region'])
    assert.equal(previewSearch(search, false), search);
});
