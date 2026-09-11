import test from 'node:test';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {mobileAddressPlugin, namespaceAssetReferences} from './mobile-build.ts';

function transform(code: string, id: string): unknown {
  const hook = mobileAddressPlugin().transform;
  assert.equal(typeof hook, 'function');
  return (hook as (code: string, id: string) => unknown)(code, id);
}

test('deployment prefixes both main and worker runtime paths without changing asset identity or external URLs', () => {
  const source = `fetch('/models/canonical-face.json'); new URL("/mediapipe/", self.location.href); const model = '/hair-preview-models/hair-only.tflite'; const external = 'https://example.test/models/x';`;
  const output = namespaceAssetReferences(source);
  assert.equal(output, `fetch('/ar_testing/models/canonical-face.json'); new URL("/ar_testing/mediapipe", self.location.href); const model = '/ar_testing/hair-preview-models/hair-only.tflite'; const external = 'https://example.test/models/x';`);
  assert.equal(namespaceAssetReferences(output), output);
});

test('MediaPipe runtime root produces canonical single-separator SDK module addresses', () => {
  const literal = namespaceAssetReferences("'/mediapipe/'").slice(1, -1);
  const wasmRoot = new URL(literal, 'https://example.test/ar_testing/assets/worker.js').href;
  assert.equal(`${wasmRoot}/vision_wasm_module_internal.js`,
    'https://example.test/ar_testing/mediapipe/vision_wasm_module_internal.js');
  assert.equal(namespaceAssetReferences("'/models/'"), "'/ar_testing/models/'");
});

test('mobile main and worker hooks prepend the guard only to the exact pinned SDK module', () => {
  const sdk = fileURLToPath(new URL('../../node_modules/@mediapipe/tasks-vision/vision_bundle.mjs', import.meta.url)).replaceAll('\\', '/');
  const guard = fileURLToPath(new URL('./mobile-network.ts', import.meta.url)).replaceAll('\\', '/');
  const original = 'export const SDK = "original SDK bytes";';
  const expected = {code: `import ${JSON.stringify(guard)};\n${original}`, map: null};
  for (const id of [sdk, sdk.replaceAll('/', '\\'), sdk + '?worker_file&type=module']) assert.deepEqual(transform(original, id), expected);
  for (const id of [sdk.replace('vision_bundle.mjs', 'vision_bundle.js'), sdk.replace('@mediapipe/tasks-vision', 'another-package'),
    sdk.replace('/node_modules/', '/other/node_modules/')]) assert.equal(transform(original, id), null);
});

test('G renderer source and other dependencies receive no network guard or source mutation', () => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const original = 'export const source = "preserved geometry and pipeline";';
  assert.equal(transform(original, `${root}/experiments/speed-lab/renderer.ts`), null);
  assert.equal(transform(original, `${root}/node_modules/three/build/three.module.js`), null);
});
