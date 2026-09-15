import {test} from 'node:test';
import assert from 'node:assert/strict';
import {describeExternalModel, installExternalModel, parseExternalModel} from '../src/eyewear/external.ts';
import {DEFAULT_EYEWEAR_ID, EYEWEAR, eyewearById, GLASSES_OFFSET_CM, isEyewearId, MODELING_AUTO_EYEWEAR_ID, modelingAutoEyewear,
  registerModelingAutoEyewear, SHIPPED_EYEWEAR, unregisterModelingAutoEyewear} from '../src/eyewear/catalog.ts';
import {registerPinnedGeometry} from '../src/render/continuity.ts';

const PAGE = 'http://127.0.0.1:8064';
const ASSET = 'http://127.0.0.1:8064/api/jobs/6eda65cd-d4d2-4e65-9452-7ac734b1807a/files/9f0c2a1b7e8d4f5a';
const DIGEST = 'c'.repeat(64);

test('a page without a model parameter carries no handover', () => {
  assert.equal(parseExternalModel('', PAGE), null);
  assert.equal(parseExternalModel('?face=cpu', PAGE), null);
  assert.equal(parseExternalModel('?model=', PAGE), null);
});

test('loopback and same-origin model addresses are accepted; clip, width and digest are optional', () => {
  const model = parseExternalModel(`?model=${encodeURIComponent(ASSET)}`, PAGE)!;
  assert.deepEqual(model, {url: ASSET, name: 'Modeling Auto model', clipZM: null, widthMm: null, sha256: null});
  const relative = parseExternalModel(`?model=/models/tom-ford-clear.glb&name=%20Sample%20&clip=-0.11&width=138&sha256=${DIGEST.toUpperCase()}`, PAGE)!;
  assert.deepEqual(relative, {url: `${PAGE}/models/tom-ford-clear.glb`, name: 'Sample', clipZM: -0.11, widthMm: 138, sha256: DIGEST});
  assert.equal(parseExternalModel('?model=http://localhost:8060/api/jobs/a/files/b', PAGE)!.url, 'http://localhost:8060/api/jobs/a/files/b');
  assert.equal(parseExternalModel(`?model=${encodeURIComponent(ASSET)}&name=${'x'.repeat(200)}`, PAGE)!.name.length, 120);
  const unusable = parseExternalModel(`?model=${encodeURIComponent(ASSET)}&clip=0.5&width=900&sha256=nothex`, PAGE)!;
  assert.deepEqual([unusable.clipZM, unusable.widthMm, unusable.sha256], [null, null, null]);
});

test('other hosts, credentials, fragments and non-http schemes are refused', () => {
  for (const search of [
    '?model=https://evil.example/frame.glb',
    '?model=http://user:secret@127.0.0.1:8060/a',
    '?model=http://127.0.0.1:8060/a%23frag',
    '?model=ftp://127.0.0.1/a.glb',
    '?model=javascript:alert(1)',
  ]) assert.throws(() => parseExternalModel(search, PAGE), search);
});

test('the catalog lists the shipped frames only until a Modeling Auto model is registered', () => {
  unregisterModelingAutoEyewear();
  assert.deepEqual(Object.keys(EYEWEAR), Object.keys(SHIPPED_EYEWEAR));
  assert.equal(EYEWEAR['amber-horizon'], SHIPPED_EYEWEAR['amber-horizon']);
  assert.equal(DEFAULT_EYEWEAR_ID, 'amber-horizon');
  assert.equal(modelingAutoEyewear(), null);
  assert.throws(() => eyewearById(MODELING_AUTO_EYEWEAR_ID), /unavailable/);
  assert.ok(isEyewearId('amber-horizon') && isEyewearId('tom-ford-clear') && isEyewearId('modeling-auto') && !isEyewearId('other'));
  const registered = registerModelingAutoEyewear({name: ' Oakley r005 ', assetUrl: ASSET, widthMm: 138, templeClipLocalZM: -0.11});
  assert.deepEqual(Object.keys(EYEWEAR), [...Object.keys(SHIPPED_EYEWEAR), MODELING_AUTO_EYEWEAR_ID]);
  assert.equal(eyewearById(MODELING_AUTO_EYEWEAR_ID), registered);
  assert.equal(modelingAutoEyewear(), registered);
  assert.deepEqual(registered, {id: 'modeling-auto', name: 'Oakley r005', optionLabel: 'Oakley r005 · Modeling Auto',
    description: 'Prepared by Modeling Auto at 138 mm across the front. Preview placement is not measured wearer fit.',
    finish: 'Modeling Auto · Prepared model', assetUrl: ASSET, offsetCm: GLASSES_OFFSET_CM, assumedWidthMm: 138, templeClipLocalZM: -0.11});
  assert.ok(Object.isFrozen(registered));
  for (const bad of [{assetUrl: ''}, {widthMm: 20}, {templeClipLocalZM: 0}, {templeClipLocalZM: -0.5}])
    assert.throws(() => registerModelingAutoEyewear({name: 'x', assetUrl: ASSET, widthMm: 145, templeClipLocalZM: -0.11, ...bad}));
  unregisterModelingAutoEyewear();
  assert.equal(modelingAutoEyewear(), null);
  assert.deepEqual(Object.keys(EYEWEAR), Object.keys(SHIPPED_EYEWEAR));
});

test('installing a handover registers the frame with its own clip and width, and the defaults when absent', () => {
  const model = parseExternalModel(`?model=${encodeURIComponent(ASSET)}&name=Oakley%20r005&width=138&clip=-0.094&sha256=${DIGEST}`, PAGE)!;
  const definition = installExternalModel(model);
  assert.equal(definition, eyewearById(MODELING_AUTO_EYEWEAR_ID));
  assert.deepEqual([definition.name, definition.assumedWidthMm, definition.templeClipLocalZM, definition.assetUrl], ['Oakley r005', 138, -0.094, ASSET]);
  assert.match(describeExternalModel(model), /Oakley r005.*138 mm across the front.*is selected on this page/);
  assert.doesNotMatch(describeExternalModel(model), /unavailable/);
  const bare = installExternalModel(parseExternalModel(`?model=${encodeURIComponent(ASSET)}`, PAGE)!);
  assert.deepEqual([bare.name, bare.assumedWidthMm, bare.templeClipLocalZM], ['Modeling Auto model', 145, -0.11]);
  assert.match(describeExternalModel({...model, sha256: null}), /continuity cut is unavailable/);
  unregisterModelingAutoEyewear();
});

test('continuity pins a handed-over asset by its address only with a well-formed digest', () => {
  registerPinnedGeometry(ASSET, DIGEST);
  assert.throws(() => registerPinnedGeometry(ASSET, 'nothex'), /invalid/);
  assert.throws(() => registerPinnedGeometry('', DIGEST), /invalid/);
});
