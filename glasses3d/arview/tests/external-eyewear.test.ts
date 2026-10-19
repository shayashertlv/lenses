import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULT_EXTERNAL_NAME, describeExternalModel, modelFromExternal, parseExternalModel} from '../src/eyewear/external.ts';
import {GLASSES_OFFSET_CM, resolveModelDefinition, TEMPLE_CLIP_FARTHEST_LOCAL_Z_M,
  TEMPLE_CLIP_NEAREST_LOCAL_Z_M} from '../src/eyewear/catalog.ts';
import {createTempleBlendConfiguration, TEMPLE_BLEND_LENGTH_LOCAL_M} from '../src/render/temple-clip.ts';

const PAGE = 'http://127.0.0.1:8250';
const ASSET = 'http://127.0.0.1:8250/api/jobs/6eda65cd-d4d2-4e65-9452-7ac734b1807a/files/9f0c2a1b7e8d4f5a';
const DIGEST = 'c'.repeat(64);

test('a page without a model parameter carries no handover', () => {
  assert.equal(parseExternalModel('', PAGE), null);
  assert.equal(parseExternalModel('?face=cpu', PAGE), null);
  assert.equal(parseExternalModel('?model=', PAGE), null);
});

test('loopback and same-origin model addresses are accepted; clip, width and digest are optional', () => {
  const model = parseExternalModel(`?model=${encodeURIComponent(ASSET)}`, PAGE)!;
  assert.deepEqual(model, {url: ASSET, name: DEFAULT_EXTERNAL_NAME, clipZM: null, widthMm: null, sha256: null, lensEnvIntensity: null, reportUrl: null, warnings: []});
  const relative = parseExternalModel(`?model=/models/tom-ford-clear.glb&name=%20Sample%20&clip=-0.11&width=138&sha256=${DIGEST.toUpperCase()}&lensenv=1.5`, PAGE)!;
  assert.deepEqual(relative, {url: `${PAGE}/models/tom-ford-clear.glb`, name: 'Sample', clipZM: -0.11, widthMm: 138, sha256: DIGEST,
    lensEnvIntensity: 1.5, reportUrl: null, warnings: []});
  assert.equal(parseExternalModel('?model=http://localhost:8060/api/jobs/a/files/b', PAGE)!.url, 'http://localhost:8060/api/jobs/a/files/b');
  assert.equal(parseExternalModel(`?model=${encodeURIComponent(ASSET)}&name=${'x'.repeat(200)}`, PAGE)!.name.length, 120);
});

test('an unusable handover value is never dropped silently: it is a warning, and range checks happen once, on resolve', () => {
  const unusable = parseExternalModel(`?model=${encodeURIComponent(ASSET)}&clip=abc&width=900&sha256=nothex&lensenv=bright`, PAGE)!;
  assert.deepEqual([unusable.clipZM, unusable.widthMm, unusable.sha256, unusable.lensEnvIntensity], [null, null, null, null]);
  assert.equal(unusable.warnings.length, 4);
  assert.match(unusable.warnings.join(' '), /temple clip depth "abc".*not a number/);
  assert.match(unusable.warnings.join(' '), /frame width 900 mm.*outside 60-250 mm/);
  assert.match(unusable.warnings.join(' '), /lens reflection multiplier "bright".*not a number/);
  assert.match(unusable.warnings.join(' '), /digest \(\?sha256=\) is not 64 hexadecimal characters and was ignored/);
  // A numeric but out-of-range clip or lensenv is kept raw here and refused (with a warning) by resolveModelDefinition.
  const outOfRange = parseExternalModel(`?model=${encodeURIComponent(ASSET)}&clip=0.5&lensenv=9`, PAGE)!;
  assert.deepEqual([outOfRange.clipZM, outOfRange.lensEnvIntensity, outOfRange.warnings], [0.5, 9, []]);
  const resolved = resolveModelDefinition(modelFromExternal(outOfRange));
  assert.equal(resolved.templeClipZM, null, 'ignored: the renderer derives the clip from the temples');
  assert.equal(resolved.lensEnvIntensity, undefined);
  assert.equal(resolved.warnings.length, 2);
  assert.match(resolved.warnings[0]!, /temple clip depth 0.5 is outside the drawable range.*ignored; the arms end at the clip derived from the model's own temples/);
  assert.match(resolved.warnings[1]!, /lens reflection multiplier 9 is outside 0.3-4 and was ignored/);
  // The parse warnings travel with the definition into the report.
  assert.deepEqual(resolveModelDefinition(modelFromExternal(unusable)).warnings.slice(0, 4), unusable.warnings);
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

test('resolving a definition keeps usable values, and every fallback is a warning', () => {
  const full = resolveModelDefinition({url: ASSET, displayName: ' Oakley r005 ', templeClipZM: -0.094, lensEnvIntensity: 1.23});
  assert.deepEqual(full, {url: ASSET, displayName: 'Oakley r005', templeClipZM: -0.094, lensEnvIntensity: 1.23,
    offsetCm: GLASSES_OFFSET_CM, warnings: []});
  assert.ok(Object.isFrozen(full) && Object.isFrozen(full.warnings));
  const bare = resolveModelDefinition({url: `${PAGE}/models/frame.glb?v=2`, displayName: ''});
  assert.equal(bare.displayName, 'frame.glb');
  // No clip is the normal case now (glasses3d/plan/archive/20261019/WP1_DESIGN.md §3): the renderer derives it from the model's temples, so it is no
  // fallback and no warning (the fixed -0.110 m default and its warning are gone).
  assert.equal(bare.templeClipZM, null);
  assert.equal(bare.lensEnvIntensity, undefined, 'absent lensenv is the renderer default, not a fallback');
  assert.deepEqual(bare.offsetCm, GLASSES_OFFSET_CM);
  assert.equal(bare.warnings.length, 1);
  assert.match(bare.warnings[0]!, /no display name/);
  // The drawable clip range is the temple blend's: nearer than -45 mm the 15 mm end blend cannot end behind -30 mm.
  assert.equal(TEMPLE_CLIP_NEAREST_LOCAL_Z_M, -0.03 - TEMPLE_BLEND_LENGTH_LOCAL_M);
  assert.throws(() => createTempleBlendConfiguration(TEMPLE_CLIP_NEAREST_LOCAL_Z_M + 0.001), /invalid/, 'the limit is the blend itself');
  for (const clip of [TEMPLE_CLIP_NEAREST_LOCAL_Z_M, -0.14, TEMPLE_CLIP_FARTHEST_LOCAL_Z_M]) {
    const resolved = resolveModelDefinition({url: ASSET, displayName: 'x', templeClipZM: clip});
    assert.equal(resolved.templeClipZM, clip); assert.deepEqual(resolved.warnings, []);
    assert.doesNotThrow(() => createTempleBlendConfiguration(resolved.templeClipZM!));
  }
  for (const clip of [-0.03, -0.04, 0, -0.5, Number.NaN, Infinity]) {
    const resolved = resolveModelDefinition({url: ASSET, displayName: 'x', templeClipZM: clip});
    assert.equal(resolved.templeClipZM, null, `clip ${clip} is ignored`);
    assert.equal(resolved.warnings.length, 1); assert.match(resolved.warnings[0]!, /outside the drawable range/);
  }
  const offset = resolveModelDefinition({url: ASSET, displayName: 'x', templeClipZM: -0.14, offsetCm: [0, 3, Number.NaN] as never});
  assert.deepEqual(offset.offsetCm, GLASSES_OFFSET_CM); assert.match(offset.warnings[0]!, /bridge offset.*ignored/);
  const moved = resolveModelDefinition({url: ASSET, displayName: 'x', templeClipZM: -0.14, offsetCm: [0, 3.5, 6.5]});
  assert.deepEqual(moved.offsetCm, [0, 3.5, 6.5]); assert.deepEqual(moved.warnings, []);
  for (const url of ['', '  ', undefined]) assert.throws(() => resolveModelDefinition({url, displayName: 'x'} as never), /no address/);
});

test('a handover becomes the page model with its own clip, name and lensenv; an absent clip is left to the model temples', () => {
  const model = parseExternalModel(`?model=${encodeURIComponent(ASSET)}&name=Oakley%20r005&width=138&clip=-0.094&lensenv=1.49&sha256=${DIGEST}`, PAGE)!;
  const definition = modelFromExternal(model);
  assert.deepEqual(definition, {url: ASSET, displayName: 'Oakley r005', templeClipZM: -0.094, lensEnvIntensity: 1.49, handoverSha256: DIGEST,
    warnings: []});
  assert.match(describeExternalModel(model), /Oakley r005.*138 mm across the front.*is loaded on this page/);
  assert.doesNotMatch(describeExternalModel(model), /unavailable/);
  const bare = resolveModelDefinition(modelFromExternal(parseExternalModel(`?model=${encodeURIComponent(ASSET)}`, PAGE)!));
  assert.deepEqual([bare.displayName, bare.templeClipZM], [DEFAULT_EXTERNAL_NAME, null]);
  assert.doesNotMatch(bare.warnings.join(' '), /temple clip/, 'an absent clip is derived from the model, not a fallback');
  // Continuity no longer depends on a digest (glasses3d/plan/archive/20261019/WP1_DESIGN.md §3): a model without one is described like any other.
  assert.doesNotMatch(describeExternalModel({...model, sha256: null}), /continuity|unavailable/);
});

// Replaces 'a handed-over digest pins the asset for continuity; a malformed pin registration is refused': nothing is
// pinned any more (glasses3d/plan/archive/20261019/WP1_DESIGN.md §3). The digest is carried to the renderer, which only compares it with the loaded
// bytes (tests/model-load.test.ts covers the mismatch warning).
test('a handed-over digest is carried along, normalised and never pinned; a malformed one is a warning', () => {
  const definition = modelFromExternal(parseExternalModel(`?model=${encodeURIComponent(ASSET)}&sha256=${DIGEST.toUpperCase()}`, PAGE)!);
  assert.equal(definition.handoverSha256, DIGEST);
  assert.equal(resolveModelDefinition(definition).handoverSha256, DIGEST);
  const malformed = resolveModelDefinition({url: ASSET, displayName: 'x', templeClipZM: -0.14, handoverSha256: 'nothex'});
  assert.equal(malformed.handoverSha256, undefined);
  assert.match(malformed.warnings.join(' '), /handed-over digest "nothex" is not 64 hexadecimal characters and was ignored/);
});

test('the server report address (?report=, arview serve) follows the model\'s rules; an unusable one is a warning, never silent', () => {
  const served = parseExternalModel(`?model=${encodeURIComponent(ASSET)}&report=${encodeURIComponent(`${PAGE}/__arview/report.json`)}`, PAGE)!;
  assert.equal(served.reportUrl, `${PAGE}/__arview/report.json`);
  assert.deepEqual(served.warnings, []);
  assert.equal(parseExternalModel(`?model=${encodeURIComponent(ASSET)}&report=/__arview/report.json`, PAGE)!.reportUrl, `${PAGE}/__arview/report.json`);
  assert.equal(parseExternalModel(`?model=${encodeURIComponent(ASSET)}&report=http://localhost:8251/__arview/report.json`, PAGE)!.reportUrl,
    'http://localhost:8251/__arview/report.json');
  for (const [value, reason] of [['https://evil.example/report.json', /must be served from this computer/], ['javascript:alert(1)', /plain http\(s\) URL/],
    ['http://user:pw@127.0.0.1:8251/r.json', /plain http\(s\) URL/], ['http://127.0.0.1:8251/r.json%23x', null]] as const) {
    const parsed = parseExternalModel(`?model=${encodeURIComponent(ASSET)}&report=${encodeURIComponent(value)}`, PAGE)!;
    if (reason === null) {assert.equal(parsed.reportUrl, 'http://127.0.0.1:8251/r.json%23x', 'an encoded # is part of the path'); continue;}
    assert.equal(parsed.reportUrl, null, value);
    assert.equal(parsed.warnings.length, 1, value);
    assert.match(parsed.warnings[0]!, /^The model report address \(\?report=\) /);
    assert.match(parsed.warnings[0]!, reason);
  }
  assert.equal(parseExternalModel(`?model=${encodeURIComponent(ASSET)}&report=`, PAGE)!.reportUrl, null);
});
