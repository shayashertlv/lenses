import {test} from 'node:test';
import assert from 'node:assert/strict';
import {isStudioSender, mergeStudioPreview, parseStudioPreview, studioConnection} from '../src/studio/protocol.ts';

const sha = 'a'.repeat(64), channel = 'a-secure-random-channel', origin = 'http://127.0.0.1:8767';
const search = `?studioOrigin=${encodeURIComponent(origin)}&studioChannel=${channel}&sha256=${sha}`;
const message = (edits: unknown = {'0': {base_color: '#AaCcFF', roughness: .2}}, viewer: unknown = {lens_reflection: 2}) =>
  ({type: 'lenses-studio:preview', channel, model_sha256: sha, edits, viewer});

test('studio bridge requires explicit loopback origin, random-shaped channel and pinned model revision', () => {
  assert.deepEqual(studioConnection(search, 'http://localhost:8240'), {origin, channel, modelSha256: sha});
  for (const invalid of ['', search.replace(channel, 'short'), search.replace(sha, 'bad'), search.replace(encodeURIComponent(origin), encodeURIComponent('https://example.com')),
    search.replace(encodeURIComponent(origin), encodeURIComponent(origin + '/path')), search.replace(encodeURIComponent(origin), encodeURIComponent('http://user@127.0.0.1:8767'))]) {
    assert.equal(studioConnection(invalid, 'http://127.0.0.1:8240'), null);
  }
  assert.equal(studioConnection(search, 'https://example.com'), null);
});

test('origin, exact parent window, type and channel must all match', () => {
  const connection = studioConnection(search, 'http://localhost:8240')!, parent = {} as Window;
  const event = {origin, source: parent, data: message()};
  assert.equal(isStudioSender(event, parent, connection), true);
  for (const changed of [{...event, origin: 'http://localhost:8767'}, {...event, source: {} as Window},
    {...event, data: {...event.data, channel: 'different'}}, {...event, data: {...event.data, type: 'other'}}]) {
    assert.equal(isStudioSender(changed, parent, connection), false);
  }
});

test('rejects wrong revision, URLs, unknown fields, colors, nonfinite and out-of-bounds numeric controls', () => {
  assert.deepEqual(parseStudioPreview(message(), sha), {edits: {'0': {base_color: '#aaccff', roughness: .2}}, viewer: {lens_reflection: 2}});
  for (const invalid of [null, {...message(), model_sha256: 'b'.repeat(64)}, {...message(), url: 'http://localhost/other.glb'},
    message({'0': {opacity: .2}}), message({'0': {base_color: 'red'}}), message({'0': {roughness: NaN}}),
    message({'0': {metallic: true}}), message({'0': {transmission: 0}}), message({'0': {ior: 2.6}}),
    message({'0': {attenuation_distance: 11}}), message({'0': {iridescence_thickness: 1501}}),
    message({'-1': {roughness: .2}}), message({'00': {roughness: .2}}), message({}, {lens_reflection: 4.1}),
    message({}, {model: 'changed'}), message(JSON.parse('{"__proto__":{"roughness":0.1}}')),
    message(Object.fromEntries(Array.from({length: 129}, (_, i) => [i, {}])))]) assert.throws(() => parseStudioPreview(invalid, sha));
});

test('accepted edits accumulate by property while camera is closed and reset values can overwrite them', () => {
  const first = parseStudioPreview(message({'2': {base_color: '#112233'}}, {}), sha);
  const second = parseStudioPreview(message({'2': {roughness: .6}}, {lens_reflection: 1.3}), sha);
  const merged = mergeStudioPreview(first, second);
  assert.deepEqual(merged, {edits: {'2': {base_color: '#112233', roughness: .6}}, viewer: {lens_reflection: 1.3}});
  assert.equal(mergeStudioPreview(merged, {edits: {'2': {roughness: .1}}, viewer: {}}).edits['2']!.roughness, .1);
});
