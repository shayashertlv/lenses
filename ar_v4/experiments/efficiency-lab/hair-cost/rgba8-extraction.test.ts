import assert from 'node:assert/strict';
import {test} from 'node:test';
import {extractCategoryMask} from './extraction.ts';
import type {CategoryMaskSource} from './extraction.ts';
import {disposeRgba8Extraction} from './rgba8-extraction.ts';
import {validateCategoryExtractionMetrics} from './protocol.ts';

/** Integer proof model for the GLSL conversion. Browser tests independently run
 * the real shader on these float32 boundaries and compare against the SDK. */
function integerByte(bits: number): number {
  const exponent = (bits >>> 23) & 255;
  if (exponent < 118 || exponent > 150) return 0;
  const negative = (bits >>> 31) !== 0, product = ((bits & 0x7fffff) | 0x800000) * 255;
  const shift = 150 - exponent;
  const integer = Math.floor(product / 2 ** shift);
  const remainder = product - integer * 2 ** shift;
  let rounded = integer + Number(shift > 0 && (remainder > 2 ** (shift - 1) || (!negative && remainder === 2 ** (shift - 1))));
  if (rounded > 2 ** 24) {
    let lowBits = 1;
    for (let bits = 2; bits <= 8; bits++) if (rounded >= 2 ** (23 + bits)) lowBits = bits;
    const high = Math.floor(rounded / 2 ** lowBits), low = rounded - high * 2 ** lowBits;
    rounded = (high + Number(low > 2 ** (lowBits - 1) || (low === 2 ** (lowBits - 1) && high % 2 === 1))) * 2 ** lowBits;
  }
  return (negative ? -rounded : rounded) & 255;
}
test('integer category conversion equals installed SDK semantics over exponent, tie, modulo and nonfinite boundaries', () => {
  const input: number[] = [];
  for (let high = 0; high <= 0xffff; high++) for (const low of [0, 1, 0x7fff, 0x8000, 0xffff]) input.push(((high << 16) | low) >>> 0);
  const value = new Float32Array(1), word = new Uint32Array(value.buffer);
  for (let category = -1024; category <= 1024; category++) {
    value[0] = (category + .5) / 255;
    const center = word[0]!;
    for (const delta of [-2, -1, 0, 1, 2]) input.push((center + delta) >>> 0);
  }
  for (let i = 0; i <= 0xffff; i++) input.push(Math.imul(i, 2654435761) >>> 0);
  const bits = new Uint32Array(input), floats = new Float32Array(bits.buffer);
  const sdk = new Uint8Array(floats.map(value => Math.round(255 * value)));
  assert.deepEqual(new Uint8Array(bits.map(integerByte)), sdk);
});

function fixture() {
  const names = ['ACTIVE_TEXTURE', 'TEXTURE0', 'TEXTURE_2D', 'TEXTURE_BINDING_2D', 'SAMPLER_BINDING', 'CURRENT_PROGRAM',
    'VERTEX_ARRAY_BINDING', 'DRAW_FRAMEBUFFER_BINDING', 'READ_FRAMEBUFFER_BINDING', 'VIEWPORT', 'COLOR_WRITEMASK',
    'PIXEL_PACK_BUFFER_BINDING', 'PACK_ALIGNMENT', 'PACK_ROW_LENGTH', 'PACK_SKIP_PIXELS', 'PACK_SKIP_ROWS',
    'BLEND', 'CULL_FACE', 'DEPTH_TEST', 'DITHER', 'SCISSOR_TEST', 'STENCIL_TEST', 'RASTERIZER_DISCARD',
    'SAMPLE_ALPHA_TO_COVERAGE', 'SAMPLE_COVERAGE', 'TRANSFORM_FEEDBACK_ACTIVE', 'TRANSFORM_FEEDBACK_PAUSED',
    'VERTEX_SHADER', 'FRAGMENT_SHADER', 'COMPILE_STATUS', 'LINK_STATUS', 'FRAMEBUFFER', 'DRAW_FRAMEBUFFER',
    'READ_FRAMEBUFFER', 'COLOR_ATTACHMENT0', 'FRAMEBUFFER_COMPLETE', 'RGBA8', 'TEXTURE_MIN_FILTER', 'TEXTURE_MAG_FILTER',
    'NEAREST', 'COLOR', 'TRIANGLES', 'PIXEL_PACK_BUFFER', 'RGBA', 'UNSIGNED_BYTE'];
  const constants = Object.fromEntries(names.map((name, i) => [name, i + 1])) as Record<string, number>;
  const c = (name: string): number => constants[name]!;
  const values = new Map<number, unknown>(), textures = new Map<number, unknown>(), samplers = new Map<number, unknown>();
  const enabled = new Set([c('DITHER'), c('BLEND'), c('SCISSOR_TEST'), c('RASTERIZER_DISCARD')]);
  const allocated = new Set<object>(), source = {}, callerProgram = {}, callerVao = {};
  const create = (): object => {const object = {}; allocated.add(object); return object;};
  const remove = (object: object): void => {allocated.delete(object);};
  values.set(c('ACTIVE_TEXTURE'), c('TEXTURE0') + 3); textures.set(c('TEXTURE0'), {}); samplers.set(c('TEXTURE0'), {});
  values.set(c('CURRENT_PROGRAM'), callerProgram); values.set(c('VERTEX_ARRAY_BINDING'), callerVao);
  values.set(c('DRAW_FRAMEBUFFER_BINDING'), {}); values.set(c('READ_FRAMEBUFFER_BINDING'), {});
  values.set(c('VIEWPORT'), new Int32Array([11, 12, 23, 24])); values.set(c('COLOR_WRITEMASK'), [false, true, false, true]);
  values.set(c('PIXEL_PACK_BUFFER_BINDING'), {});
  for (const name of ['PACK_ALIGNMENT', 'PACK_ROW_LENGTH', 'PACK_SKIP_PIXELS', 'PACK_SKIP_ROWS']) values.set(c(name), 4);
  values.set(c('TRANSFORM_FEEDBACK_ACTIVE'), false); values.set(c('TRANSFORM_FEEDBACK_PAUSED'), false);
  let failedRead = false, completeFramebuffer = true, reads = 0, errorQueries = 0;
  const gl = {...constants,
    getParameter: (name: number): unknown => name === c('TEXTURE_BINDING_2D') ? textures.get(values.get(c('ACTIVE_TEXTURE')) as number) ?? null
      : name === c('SAMPLER_BINDING') ? samplers.get(values.get(c('ACTIVE_TEXTURE')) as number) ?? null : values.get(name),
    activeTexture: (unit: number) => values.set(c('ACTIVE_TEXTURE'), unit),
    bindTexture: (_: number, texture: object | null) => textures.set(values.get(c('ACTIVE_TEXTURE')) as number, texture),
    bindSampler: (unit: number, sampler: object | null) => samplers.set(c('TEXTURE0') + unit, sampler),
    isContextLost: () => false, isTexture: (texture: object) => texture === source || allocated.has(texture),
    createShader: create, deleteShader: remove, shaderSource() {}, compileShader() {}, getShaderParameter: () => true,
    createProgram: create, deleteProgram: remove, attachShader() {}, linkProgram() {}, getProgramParameter: () => true,
    getUniformLocation: () => ({}), createVertexArray: create, deleteVertexArray: remove,
    createFramebuffer: create, deleteFramebuffer: remove, createTexture: create, deleteTexture: remove,
    useProgram: (program: object | null) => values.set(c('CURRENT_PROGRAM'), program),
    bindVertexArray: (vao: object | null) => values.set(c('VERTEX_ARRAY_BINDING'), vao),
    bindFramebuffer: (target: number, framebuffer: object | null) => {
      if (target !== c('READ_FRAMEBUFFER')) values.set(c('DRAW_FRAMEBUFFER_BINDING'), framebuffer);
      if (target !== c('DRAW_FRAMEBUFFER')) values.set(c('READ_FRAMEBUFFER_BINDING'), framebuffer);
    },
    viewport: (...v: number[]) => values.set(c('VIEWPORT'), new Int32Array(v)),
    colorMask: (...v: boolean[]) => values.set(c('COLOR_WRITEMASK'), v),
    isEnabled: (cap: number) => enabled.has(cap), enable: (cap: number) => enabled.add(cap), disable: (cap: number) => enabled.delete(cap),
    bindBuffer: (_: number, buffer: object | null) => values.set(c('PIXEL_PACK_BUFFER_BINDING'), buffer),
    pixelStorei: (name: number, value: number) => values.set(name, value),
    texStorage2D() {}, texParameteri() {}, framebufferTexture2D() {},
    checkFramebufferStatus: () => completeFramebuffer ? c('FRAMEBUFFER_COMPLETE') : 0,
    drawBuffers() {}, readBuffer() {}, uniform1i() {}, clearBufferfv() {}, drawArrays() {},
    getError: () => {errorQueries++; return 0;},
    readPixels: (_x: number, _y: number, w: number, h: number, _format: number, _type: number, output: Uint8Array) => {
      reads++; if (failedRead) return;
      for (let i = 0; i < w * h; i++) output.set([i % 6, 85, 170, 255], i * 4);
    },
  } as unknown as WebGL2RenderingContext;
  let byteReads = 0;
  const mask: CategoryMaskSource = {width: 3, height: 2, canvas: {getContext: () => gl} as unknown as OffscreenCanvas,
    hasUint8Array: () => false, hasFloat32Array: () => false, hasWebGLTexture: () => true,
    getAsWebGLTexture: () => source as WebGLTexture,
    getAsUint8Array: () => {byteReads++; return new Uint8Array([0, 1, 2, 3, 4, 5]);}, getAsFloat32Array: () => {throw new Error('Unexpected float read');}};
  const snapshot = () => ({values: structuredClone([...values]), enabled: [...enabled].sort(),
    texture0: textures.get(c('TEXTURE0')), sampler0: samplers.get(c('TEXTURE0'))});
  return {gl, mask, snapshot, allocated, stats: () => ({reads, byteReads, errorQueries}),
    failRead: () => {failedRead = true;}, incomplete: () => {completeFramebuffer = false;}};
}

test('RGBA8 keeps GL state, raw row order, independent output and bounded reusable scratch without draining SDK errors', () => {
  const f = fixture(), before = f.snapshot();
  try {
    const first = extractCategoryMask(f.mask, 'rgba8'), second = extractCategoryMask(f.mask, 'rgba8');
    assert.deepEqual(first.category, new Uint8Array([0, 1, 2, 3, 4, 5]));
    assert.equal(first.metrics.path, 'rgba8-readback'); assert.equal(first.metrics.rgba8ReadbackBytes, 24);
    assert.equal(first.metrics.rgba8ResourcesReused, false); assert.equal(second.metrics.rgba8ResourcesReused, true);
    assert.deepEqual(f.snapshot(), before); assert.deepEqual(f.stats(), {reads: 2, byteReads: 0, errorQueries: 0});
    assert.notEqual(first.category.buffer, second.category.buffer); first.category.fill(255);
    assert.deepEqual(second.category, new Uint8Array([0, 1, 2, 3, 4, 5]));
    const transferred = structuredClone(second.category, {transfer: [second.category.buffer]});
    assert.deepEqual(transferred, new Uint8Array([0, 1, 2, 3, 4, 5]));
    const validated = validateCategoryExtractionMetrics(first.metrics, {sourceSHA256: 'a'.repeat(64), sequence: 1,
      width: 3, height: 2, outputMode: 'category-only', categoryExtractionMode: 'rgba8'}, first.metrics.totalMs);
    assert.deepEqual(validated, first.metrics);
  } finally {disposeRgba8Extraction();}
  assert.equal(f.allocated.size, 0);
});

test('failed RGBA8 reads and framebuffer preparation restore state and use fresh SDK bytes with explicit fallback', () => {
  for (const fail of ['failRead', 'incomplete'] as const) {
    const f = fixture(), before = f.snapshot(); f[fail]();
    try {
      const result = extractCategoryMask(f.mask, 'rgba8');
      assert.equal(result.metrics.path, 'rgba8-sdk-fallback'); assert.equal(result.metrics.rgba8ReadbackBytes, 0);
      assert.equal(result.metrics.rgba8FallbackReason, fail === 'failRead' ? 'readback-failure' : 'framebuffer-incomplete');
      assert.deepEqual(result.category, new Uint8Array([0, 1, 2, 3, 4, 5]));
      assert.deepEqual(f.snapshot(), before); assert.equal(f.stats().byteReads, 1); assert.equal(f.stats().errorQueries, 0);
    } finally {disposeRgba8Extraction();}
    assert.equal(f.allocated.size, 0);
  }
});

test('RGBA8 never replaces a cached CPU representation or full diagnostics and a failed SDK fallback still fails', () => {
  for (const full of [false, true]) {
    const f = fixture();
    const mask = full ? f.mask : {...f.mask, hasFloat32Array: () => true};
    const result = extractCategoryMask(mask, 'rgba8', !full);
    assert.equal(result.metrics.path, 'rgba8-sdk-fallback');
    assert.equal(result.metrics.rgba8FallbackReason, full ? 'full-output' : 'not-gpu-only');
    assert.equal(f.stats().reads, 0); assert.equal(f.stats().byteReads, 1); assert.equal(f.allocated.size, 0);
  }
  const f = fixture(), failure = new Error('Original SDK failure');
  assert.throws(() => extractCategoryMask({...f.mask, canvas: undefined, getAsUint8Array: () => {throw failure;}}, 'rgba8'), error => error === failure);
});
