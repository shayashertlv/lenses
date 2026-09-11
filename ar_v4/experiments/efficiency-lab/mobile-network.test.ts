import assert from 'node:assert/strict';
import test from 'node:test';
import {createSameOriginFetch} from './mobile-network.ts';

const locationHref = 'https://lenses.example/ar_testing/assets/worker.js';
function spy(): {native: typeof fetch; calls: [RequestInfo | URL, RequestInit | undefined][]; response: Response} {
  const calls: [RequestInfo | URL, RequestInit | undefined][] = [];
  const response = new Response('local asset', {status: 200});
  return {calls, response, native: async (input, init) => {calls.push([input, init]); return response;}};
}

test('same-origin strings and URL objects reach the bound fetch with the exact original arguments', async () => {
  const {native, calls, response} = spy(), guarded = createSameOriginFetch(native, locationHref);
  const inputs = ['/ar_testing/models/face.task', '../models/hair.tflite', new URL('https://lenses.example/ar_testing/data')];
  const controller = new AbortController(), init: RequestInit = {method: 'GET', signal: controller.signal, cache: 'no-cache'};
  for (const input of inputs) assert.equal(await guarded(input, init), response);
  assert.equal(calls.length, 3);
  for (const [index, input] of inputs.entries()) {assert.equal(calls[index]![0], input); assert.equal(calls[index]![1], init);}
});

test('same-origin Request bodies, headers and overrides are passed through without reading or cloning', async () => {
  const {native, calls, response} = spy(), guarded = createSameOriginFetch(native, locationHref);
  const request = new Request('https://lenses.example/ar_testing/local', {method: 'POST', body: 'local-data', headers: {'X-Test': '1'}});
  const init: RequestInit = {cache: 'no-store'};
  assert.equal(await guarded(request, init), response);
  assert.equal(calls[0]![0], request); assert.equal(calls[0]![1], init);
  assert.equal(request.bodyUsed, false); assert.equal(await request.text(), 'local-data');
});

test('external requests are rejected before native fetch and without disclosing their URL or payload', async () => {
  const {native, calls} = spy(), guarded = createSameOriginFetch(native, locationHref);
  const inputs = ['https://external.example/secret-path', '//external.example/private', 'http://lenses.example/downgrade',
    'https://lenses.example:8443/other-port', 'https://lenses.example.evil.test/prefix',
    new URL('https://external.example/another'), new Request('https://external.example/body', {method: 'POST', body: 'private-body'})];
  for (const input of inputs) await assert.rejects(guarded(input), error => {
    assert.ok(error instanceof TypeError); assert.equal(error.message, 'AR testing blocks off-origin network requests.');
    assert.doesNotMatch(error.message, /external|secret|private|https/); return true;
  });
  assert.equal(calls.length, 0);
});

test('only same-origin blob URLs are allowed; data, opaque and external blobs never reach fetch', async () => {
  const {native, calls} = spy(), guarded = createSameOriginFetch(native, locationHref);
  const blob = 'blob:https://lenses.example/afef2109-9482-4928-a4e5-6b2c0a3cbb83';
  await guarded(blob); assert.equal(calls[0]![0], blob);
  for (const denied of ['blob:https://external.example/id', 'blob:null/id', 'data:application/octet-stream;base64,AAAA', 'javascript:alert(1)'])
    await assert.rejects(guarded(denied), /off-origin/);
  assert.equal(calls.length, 1);
});

test('native failure and AbortSignal semantics propagate without substituting a successful response', async () => {
  const failure = new DOMException('Aborted locally.', 'AbortError');
  const native: typeof fetch = async () => {throw failure;};
  const guarded = createSameOriginFetch(native, locationHref);
  await assert.rejects(guarded('/ar_testing/model'), error => error === failure);
  assert.throws(() => createSameOriginFetch(native, 'data:text/plain,opaque'), /origin/);
});

test('importing the module in Node does not mutate its global fetch without a browser location', async () => {
  const before = globalThis.fetch;
  await import('./mobile-network.ts');
  assert.equal(globalThis.fetch, before);
});
