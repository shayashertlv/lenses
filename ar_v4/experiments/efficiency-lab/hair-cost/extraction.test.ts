import assert from 'node:assert/strict';
import {test} from 'node:test';
import {extractCategoryMask} from './extraction.ts';
import type {CategoryExtractionMode, CategoryMaskSource} from './extraction.ts';

function sdkFormula(values: Float32Array): Uint8Array<ArrayBuffer> {
  // Installed tasks-vision 1.0.1 getAsUint8Array conversion, including map's float32 store.
  return new Uint8Array(values.map(value => Math.round(255 * value)));
}

function makeMask(input: {floats?: Float32Array; bytes?: Uint8Array; texture?: boolean;
  floatCached?: boolean; width?: number; height?: number}) {
  let bytes = input.bytes, floatCached = input.floatCached ?? Boolean(input.floats);
  const floats = input.floats ?? new Float32Array(input.bytes?.length ?? 1);
  const calls = {byte: 0, float: 0};
  const mask: CategoryMaskSource = {
    width: input.width ?? bytes?.length ?? floats.length, height: input.height ?? 1,
    hasUint8Array: () => Boolean(bytes), hasFloat32Array: () => floatCached,
    hasWebGLTexture: () => Boolean(input.texture),
    getAsUint8Array: () => {calls.byte++; bytes ??= sdkFormula(floats); return bytes;},
    getAsFloat32Array: () => {calls.float++; floatCached = true; return floats;},
  };
  return {mask, calls, floats};
}

test('direct matches SDK map rounding across every float32 upper word and representative lower words', () => {
  const lowWords = [0, 1, 0x7fff, 0x8000, 0xffff];
  const bits = new Uint32Array(0x10000 * lowWords.length + 0x10000);
  let offset = 0;
  for (let high = 0; high <= 0xffff; high++) {
    for (const low of lowWords) bits[offset++] = ((high << 16) | low) >>> 0;
  }
  for (let i = 0; i <= 0xffff; i++) bits[offset++] = Math.imul(i, 2654435761) >>> 0;
  const values = new Float32Array(bits.buffer), fixture = makeMask({floats: values});
  const {category} = extractCategoryMask(fixture.mask, 'direct');
  assert.deepEqual(category, sdkFormula(values));
  assert.deepEqual(fixture.calls, {byte: 0, float: 1});
  assert.equal(values.length, 393216);
});

test('direct preserves all category values, half-step neighbours, nonfinite and float32 integer rounding', () => {
  const values = [NaN, Infinity, -Infinity, 0, -0, 100001, -100001,
    Number.MAX_VALUE, -Number.MAX_VALUE, Number.MIN_VALUE, -Number.MIN_VALUE];
  for (let category = 0; category <= 255; category++) values.push(category / 255);
  const single = new Float32Array(1), bits = new Uint32Array(single.buffer);
  for (let category = -512; category <= 512; category++) {
    single[0] = (category + 0.5) / 255;
    const center = bits[0]!;
    for (const delta of [-1, 0, 1]) {bits[0] = center + delta; values.push(single[0]!);}
  }
  const floats = new Float32Array(values), {mask} = makeMask({floats});
  const {category} = extractCategoryMask(mask, 'direct');
  assert.deepEqual(category, sdkFormula(floats));
  const withoutFloat32Store = new Uint8Array([Math.round(255 * 100001)]);
  assert.notEqual(category[5], withoutFloat32Store[0], 'fixture must detect removal of Math.fround');
});

test('SDK control uses the public byte getter then an independent slice', () => {
  for (const backedByBytes of [false, true]) {
    const bytes = new Uint8Array([0, 1, 2, 255]);
    const fixture = makeMask(backedByBytes ? {bytes} : {floats: new Float32Array([0, 1 / 255, 2 / 255, 1])});
    const result = extractCategoryMask(fixture.mask);
    assert.deepEqual(result.category, bytes);
    assert.deepEqual(fixture.calls, {byte: 1, float: 0});
    assert.equal(result.metrics.mode, 'sdk'); assert.equal(result.metrics.path, 'sdk-copy');
    assert.equal(result.metrics.conversionMs, 0);
    assert.equal(result.metrics.retrievedArrayBytes, 4);
    assert.equal(result.metrics.categoryBytesCopied, 4);
    assert.equal(result.metrics.categoryBytesConverted, 0);
    assert.equal(result.metrics.explicitFloatTemporaryBytesAvoided, 0);
    assert.equal(result.metrics.explicitCategoryCopyBytesAvoided, 0);
    assert.notEqual(result.category.buffer, fixture.mask.getAsUint8Array().buffer);
  }
});

test('direct retains byte priority when byte, float and texture representations coexist', () => {
  const fixture = makeMask({bytes: new Uint8Array([9, 8, 7]),
    floats: new Float32Array([0, 0, 0]), texture: true});
  const {category, metrics} = extractCategoryMask(fixture.mask, 'direct');
  assert.deepEqual(category, new Uint8Array([9, 8, 7]));
  assert.deepEqual(fixture.calls, {byte: 1, float: 0});
  assert.equal(metrics.path, 'direct-byte-copy');
  assert.deepEqual([metrics.hasUint8, metrics.hasFloat32, metrics.hasWebGLTexture], [true, true, true]);
  assert.equal(metrics.conversionMs, 0);
  assert.equal(metrics.retrievedArrayBytes, 3);
  assert.equal(metrics.categoryBytesCopied, 3);
  assert.equal(metrics.categoryBytesConverted, 0);
  assert.equal(metrics.explicitFloatTemporaryBytesAvoided, 0);
  assert.equal(metrics.explicitCategoryCopyBytesAvoided, 0);
});

test('representation metrics precede public retrieval and explicit savings exclude GPU transfer', () => {
  for (const gpuOnly of [false, true]) {
    const fixture = makeMask({floats: new Float32Array([0, 1 / 255, 2 / 255, 1]),
      floatCached: !gpuOnly, texture: gpuOnly, width: 2, height: 2});
    const {metrics} = extractCategoryMask(fixture.mask, 'direct');
    assert.equal(fixture.mask.hasFloat32Array(), true);
    assert.deepEqual([metrics.hasUint8, metrics.hasFloat32, metrics.hasWebGLTexture], [false, !gpuOnly, gpuOnly]);
    assert.equal(metrics.path, 'direct-float-conversion'); assert.equal(metrics.maskPixels, 4);
    assert.equal(metrics.retrievedArrayBytes, 16);
    assert.equal(metrics.categoryBytesCopied, 0);
    assert.equal(metrics.categoryBytesConverted, 4);
    assert.equal(metrics.ownedCategoryBytesAllocated, 4);
    assert.equal(metrics.explicitFloatTemporaryBytesAvoided, 16);
    assert.equal(metrics.explicitCategoryCopyBytesAvoided, 4); assert.equal(metrics.copyMs, 0);
    for (const elapsed of [metrics.retrievalMs, metrics.conversionMs, metrics.copyMs, metrics.totalMs]) {
      assert.ok(Number.isFinite(elapsed) && elapsed >= 0);
    }
    assert.ok(metrics.totalMs >= metrics.retrievalMs + metrics.conversionMs + metrics.copyMs);
  }
});

test('every result has independent owned storage that survives source mutation and transfer', () => {
  for (const mode of ['sdk', 'direct'] as const) {
    for (const byteBacked of [true, false]) {
      const source = new Uint8Array([0, 1, 2, 255]);
      const fixture = makeMask(byteBacked ? {bytes: source} : {floats: new Float32Array([0, 1 / 255, 2 / 255, 1])});
      const first = extractCategoryMask(fixture.mask, mode).category;
      const second = extractCategoryMask(fixture.mask, mode).category;
      assert.notEqual(first.buffer, second.buffer);
      source.fill(0); fixture.floats.fill(0); fixture.mask.getAsUint8Array().fill(0);
      assert.deepEqual(first, new Uint8Array([0, 1, 2, 255]));
      const moved = structuredClone(first, {transfer: [first.buffer]});
      assert.equal(first.byteLength, 0);
      assert.deepEqual(moved, new Uint8Array([0, 1, 2, 255]));
      assert.deepEqual(second, new Uint8Array([0, 1, 2, 255]));
    }
  }
});

test('invalid dimensions and storage lengths fail before returning any category', () => {
  for (const mode of ['sdk', 'direct'] as const) {
    for (const dimensions of [{width: 0}, {height: -1}, {width: 1.5}, {height: NaN},
      {width: Infinity}, {width: Number.MAX_SAFE_INTEGER, height: 2}]) {
      const fixture = makeMask({floats: new Float32Array([0]), ...dimensions});
      assert.throws(() => extractCategoryMask(fixture.mask, mode), /dimensions/);
      assert.deepEqual(fixture.calls, {byte: 0, float: 0});
    }
    for (const byteBacked of [false, true]) {
      const fixture = makeMask({floats: new Float32Array(3),
        ...(byteBacked ? {bytes: new Uint8Array(3)} : {}), width: 2, height: 2});
      assert.throws(() => extractCategoryMask(fixture.mask, mode), /storage differs/);
    }
  }
});

test('unknown mode, missing representation and public retrieval errors fail explicitly', () => {
  const fixture = makeMask({floats: new Float32Array([0])});
  assert.throws(() => extractCategoryMask(fixture.mask, 'other' as CategoryExtractionMode), /mode/);
  assert.throws(() => extractCategoryMask(makeMask({floatCached: false}).mask), /representation/);
  const failure = new Error('closed or unavailable mask');
  for (const mode of ['sdk', 'direct'] as const) {
    const failing: CategoryMaskSource = {...fixture.mask,
      getAsUint8Array: () => {throw failure;}, getAsFloat32Array: () => {throw failure;}};
    assert.throws(() => extractCategoryMask(failing, mode), error => error === failure);
  }
});
