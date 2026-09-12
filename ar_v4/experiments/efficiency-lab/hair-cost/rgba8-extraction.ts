import type {MPMask} from '@mediapipe/tasks-vision';

export const RGBA8_FALLBACK_REASONS = ['full-output', 'not-gpu-only', 'canvas-unavailable',
  'webgl2-unavailable', 'context-lost', 'unsupported-state', 'resource-failure',
  'shader-failure', 'framebuffer-incomplete', 'readback-failure'] as const;
export type Rgba8FallbackReason = typeof RGBA8_FALLBACK_REASONS[number];
export interface Rgba8AttemptMetrics {
  rgba8WorkMs: number;
  rgba8ReadbackBytes: number;
  rgba8ResourcesReused: boolean;
  rgba8FallbackReason: Rgba8FallbackReason | null;
}
type TextureMask = Pick<MPMask, 'width' | 'height'> & Partial<Pick<MPMask, 'canvas' | 'getAsWebGLTexture'>>;
export type Rgba8Attempt = {ok: true; category: Uint8Array<ArrayBuffer>; retrievalMs: number;
  conversionMs: number; metrics: Rgba8AttemptMetrics} | {ok: false; metrics: Rgba8AttemptMetrics};

export const CATEGORY_VERTEX_SHADER = `#version 300 es
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

/** Exact installed SDK conversion: Uint8Array(Float32Array.map(round(255*x))).
 * Float multiply on a GPU would round before Math.round and can change half
 * boundaries. Decode the float instead: a 24-bit significand times 255 fits in
 * uint32 exactly. Round the exact product, then emulate the float32 integer
 * store with ties to even, and finally Uint8's modulo. NaN/infinity and values
 * whose float32 integer spacing is >= 256 all map to zero. This also preserves
 * invalid/out-of-range byte values for the existing category validator. */
export const CATEGORY_FRAGMENT_SHADER = `#version 300 es
precision highp float;
precision highp int;
uniform highp sampler2D categoryTexture;
out vec4 outputColor;
uint sdkCategoryByte(float value) {
  uint bits = floatBitsToUint(value);
  uint exponent = (bits >> 23u) & 255u;
  if (exponent < 118u || exponent > 150u) return 0u;
  bool negative = (bits & 0x80000000u) != 0u;
  uint product = ((bits & 0x7fffffu) | 0x800000u) * 255u;
  uint shift = 150u - exponent;
  uint integerValue;
  if (shift == 0u) integerValue = product;
  else if (shift == 32u) {
    integerValue = (product > 0x80000000u || (!negative && product == 0x80000000u)) ? 1u : 0u;
  } else {
    uint remainder = product & ((1u << shift) - 1u);
    uint halfway = 1u << (shift - 1u);
    integerValue = (product >> shift) +
      ((remainder > halfway || (!negative && remainder == halfway)) ? 1u : 0u);
  }
  if (integerValue > 0x1000000u) {
    // In this bounded exponent range at most eight low bits are rounded away.
    // Explicit bounds keep this GLSL ES 3.00: findMSB needs ES 3.10.
    uint lowBits = 1u;
    if (integerValue >= 0x02000000u) lowBits = 2u;
    if (integerValue >= 0x04000000u) lowBits = 3u;
    if (integerValue >= 0x08000000u) lowBits = 4u;
    if (integerValue >= 0x10000000u) lowBits = 5u;
    if (integerValue >= 0x20000000u) lowBits = 6u;
    if (integerValue >= 0x40000000u) lowBits = 7u;
    if (integerValue >= 0x80000000u) lowBits = 8u;
    uint remainder = integerValue & ((1u << lowBits) - 1u);
    uint halfway = 1u << (lowBits - 1u);
    uint highBits = integerValue >> lowBits;
    if (remainder > halfway || (remainder == halfway && (highBits & 1u) != 0u)) highBits++;
    integerValue = highBits << lowBits;
  }
  return (negative ? 0u - integerValue : integerValue) & 255u;
}
void main() {
  // readPixels and the SDK getter both use this raw bottom-left row order.
  uint category = sdkCategoryByte(texelFetch(categoryTexture, ivec2(gl_FragCoord.xy), 0).r);
  // Nonzero markers make a silent failed draw/read fail closed without draining
  // the caller's WebGL error state via getError(). No blending or dithering.
  outputColor = vec4(float(category) / 255.0, 85.0 / 255.0, 170.0 / 255.0, 1.0);
}`;

interface Resources {
  gl: WebGL2RenderingContext;
  program: WebGLProgram;
  sampler: WebGLUniformLocation;
  vao: WebGLVertexArrayObject;
  framebuffer: WebGLFramebuffer;
  texture: WebGLTexture | null;
  width: number;
  height: number;
  scratch: Uint8Array<ArrayBuffer>;
}
interface GlState {
  activeTexture: number;
  texture0: WebGLTexture | null;
  sampler0: WebGLSampler | null;
  program: WebGLProgram | null;
  vao: WebGLVertexArrayObject | null;
  drawFramebuffer: WebGLFramebuffer | null;
  readFramebuffer: WebGLFramebuffer | null;
  viewport: Int32Array;
  colorMask: boolean[];
  packBuffer: WebGLBuffer | null;
  pack: number[];
  enabled: boolean[];
}
const cache = new WeakMap<WebGL2RenderingContext, Resources>();
const retained = new Set<Resources>();
const clearColor = new Float32Array(4);
const capabilities = (gl: WebGL2RenderingContext): number[] => [gl.BLEND, gl.CULL_FACE, gl.DEPTH_TEST,
  gl.DITHER, gl.SCISSOR_TEST, gl.STENCIL_TEST, gl.RASTERIZER_DISCARD, gl.SAMPLE_ALPHA_TO_COVERAGE, gl.SAMPLE_COVERAGE];
const packNames = (gl: WebGL2RenderingContext): number[] => [gl.PACK_ALIGNMENT, gl.PACK_ROW_LENGTH, gl.PACK_SKIP_PIXELS, gl.PACK_SKIP_ROWS];
class ExtractionFailure extends Error {
  readonly reason: Rgba8FallbackReason;
  constructor(reason: Rgba8FallbackReason) {super(reason); this.reason = reason;}
}
function saveState(gl: WebGL2RenderingContext): GlState {
  const activeTexture = gl.getParameter(gl.ACTIVE_TEXTURE) as number;
  // Only texture unit zero is changed. Read its binding, then restore the active
  // unit immediately so a later state-query exception cannot leave it changed.
  let texture0: WebGLTexture | null, sampler0: WebGLSampler | null;
  gl.activeTexture(gl.TEXTURE0);
  try {texture0 = gl.getParameter(gl.TEXTURE_BINDING_2D) as WebGLTexture | null;
    sampler0 = gl.getParameter(gl.SAMPLER_BINDING) as WebGLSampler | null;}
  finally {gl.activeTexture(activeTexture);}
  return {activeTexture, texture0, sampler0, program: gl.getParameter(gl.CURRENT_PROGRAM) as WebGLProgram | null,
    vao: gl.getParameter(gl.VERTEX_ARRAY_BINDING) as WebGLVertexArrayObject | null,
    drawFramebuffer: gl.getParameter(gl.DRAW_FRAMEBUFFER_BINDING) as WebGLFramebuffer | null,
    readFramebuffer: gl.getParameter(gl.READ_FRAMEBUFFER_BINDING) as WebGLFramebuffer | null,
    viewport: gl.getParameter(gl.VIEWPORT) as Int32Array, colorMask: gl.getParameter(gl.COLOR_WRITEMASK) as boolean[],
    packBuffer: gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING) as WebGLBuffer | null,
    pack: packNames(gl).map(name => gl.getParameter(name) as number), enabled: capabilities(gl).map(cap => gl.isEnabled(cap))};
}
function restoreState(gl: WebGL2RenderingContext, state: GlState): void {
  gl.useProgram(state.program); gl.bindVertexArray(state.vao);
  gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, state.drawFramebuffer);
  gl.bindFramebuffer(gl.READ_FRAMEBUFFER, state.readFramebuffer);
  gl.viewport(state.viewport[0]!, state.viewport[1]!, state.viewport[2]!, state.viewport[3]!);
  gl.colorMask(state.colorMask[0]!, state.colorMask[1]!, state.colorMask[2]!, state.colorMask[3]!);
  capabilities(gl).forEach((cap, index) => state.enabled[index] ? gl.enable(cap) : gl.disable(cap));
  gl.bindBuffer(gl.PIXEL_PACK_BUFFER, state.packBuffer);
  packNames(gl).forEach((name, index) => gl.pixelStorei(name, state.pack[index]!));
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, state.texture0); gl.bindSampler(0, state.sampler0);
  gl.activeTexture(state.activeTexture);
}
function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new ExtractionFailure('resource-failure');
  gl.shaderSource(shader, source); gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {gl.deleteShader(shader); throw new ExtractionFailure('shader-failure');}
  return shader;
}
function createResources(gl: WebGL2RenderingContext): Resources {
  let vertex: WebGLShader | null = null, fragment: WebGLShader | null = null;
  let program: WebGLProgram | null = null, vao: WebGLVertexArrayObject | null = null, framebuffer: WebGLFramebuffer | null = null;
  try {
    vertex = compile(gl, gl.VERTEX_SHADER, CATEGORY_VERTEX_SHADER); fragment = compile(gl, gl.FRAGMENT_SHADER, CATEGORY_FRAGMENT_SHADER);
    program = gl.createProgram(); vao = gl.createVertexArray(); framebuffer = gl.createFramebuffer();
    if (!program || !vao || !framebuffer) throw new ExtractionFailure('resource-failure');
    gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new ExtractionFailure('shader-failure');
    const sampler = gl.getUniformLocation(program, 'categoryTexture');
    if (sampler === null) throw new ExtractionFailure('shader-failure');
    const result: Resources = {gl, program, sampler, vao, framebuffer, texture: null, width: 0, height: 0, scratch: new Uint8Array(0)};
    cache.set(gl, result); retained.add(result); return result;
  } catch (error) {if (program) gl.deleteProgram(program); if (vao) gl.deleteVertexArray(vao); if (framebuffer) gl.deleteFramebuffer(framebuffer); throw error;}
  finally {if (vertex) gl.deleteShader(vertex); if (fragment) gl.deleteShader(fragment);}
}
function target(resources: Resources, width: number, height: number): void {
  if (resources.texture && resources.width === width && resources.height === height) return;
  const gl = resources.gl, texture = gl.createTexture();
  if (!texture) throw new ExtractionFailure('resource-failure');
  try {
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, width, height);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.bindFramebuffer(gl.FRAMEBUFFER, resources.framebuffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new ExtractionFailure('framebuffer-incomplete');
    const scratch = new Uint8Array(width * height * 4);
    if (resources.texture) gl.deleteTexture(resources.texture);
    resources.texture = texture; resources.width = width; resources.height = height; resources.scratch = scratch;
  } catch (error) {
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, resources.texture, 0);
    gl.deleteTexture(texture); throw error;
  }
}

/** Synchronous and callback-local. The SDK texture is never retained. Only the
 * independent category array escapes; the RGBA target/scratch belong to this
 * worker and are reused after the prior synchronous read has completed. */
export function readRgba8Mask(mask: TextureMask): Rgba8Attempt {
  const started = performance.now();
  const metrics: Rgba8AttemptMetrics = {rgba8WorkMs: 0, rgba8ReadbackBytes: 0,
    rgba8ResourcesReused: false, rgba8FallbackReason: null};
  let state: GlState | null = null, gl: WebGL2RenderingContext | null = null;
  let result: Rgba8Attempt;
  try {
    if (!mask.canvas || !mask.getAsWebGLTexture) throw new ExtractionFailure('canvas-unavailable');
    gl = mask.canvas.getContext('webgl2') as WebGL2RenderingContext | null;
    if (!gl || typeof gl.createVertexArray !== 'function') throw new ExtractionFailure('webgl2-unavailable');
    if (gl.isContextLost()) throw new ExtractionFailure('context-lost');
    if (gl.getParameter(gl.TRANSFORM_FEEDBACK_ACTIVE) && !gl.getParameter(gl.TRANSFORM_FEEDBACK_PAUSED))
      throw new ExtractionFailure('unsupported-state');
    state = saveState(gl);
    const source = mask.getAsWebGLTexture();
    if (!gl.isTexture(source)) throw new ExtractionFailure('resource-failure');
    const existing = cache.get(gl);
    metrics.rgba8ResourcesReused = Boolean(existing && existing.width === mask.width && existing.height === mask.height);
    const resources = existing ?? createResources(gl);
    target(resources, mask.width, mask.height);
    gl.bindFramebuffer(gl.FRAMEBUFFER, resources.framebuffer);
    gl.drawBuffers([gl.COLOR_ATTACHMENT0]); gl.readBuffer(gl.COLOR_ATTACHMENT0);
    gl.viewport(0, 0, mask.width, mask.height); gl.colorMask(true, true, true, true);
    capabilities(gl).forEach(cap => gl!.disable(cap));
    gl.bindVertexArray(resources.vao); gl.useProgram(resources.program);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, source); gl.bindSampler(0, null); gl.uniform1i(resources.sampler, 0);
    gl.clearBufferfv(gl.COLOR, 0, clearColor); gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    packNames(gl).forEach((name, index) => gl!.pixelStorei(name, index === 0 ? 1 : 0));
    resources.scratch.fill(0);
    gl.readPixels(0, 0, mask.width, mask.height, gl.RGBA, gl.UNSIGNED_BYTE, resources.scratch);
    if (gl.isContextLost()) throw new ExtractionFailure('context-lost');
    const conversionStarted = performance.now(), category = new Uint8Array(mask.width * mask.height);
    for (let i = 0, j = 0; i < category.length; i++, j += 4) {
      if (resources.scratch[j + 1] !== 85 || resources.scratch[j + 2] !== 170 || resources.scratch[j + 3] !== 255)
        throw new ExtractionFailure('readback-failure');
      category[i] = resources.scratch[j]!;
    }
    metrics.rgba8ReadbackBytes = resources.scratch.byteLength;
    result = {ok: true, category, conversionMs: performance.now() - conversionStarted,
      retrievalMs: conversionStarted - started, metrics};
  } catch (error) {
    metrics.rgba8FallbackReason = error instanceof ExtractionFailure ? error.reason : 'resource-failure';
    result = {ok: false, metrics};
  } finally {
    if (state && gl) restoreState(gl, state);
    metrics.rgba8WorkMs = performance.now() - started;
  }
  return result;
}

/** The live worker's termination releases its GL context. Explicit diagnostic
 * shutdown also disposes these caches before closing its owning MediaPipe task. */
export function disposeRgba8Extraction(): void {
  for (const resources of retained) {
    const gl = resources.gl;
    if (resources.texture) gl.deleteTexture(resources.texture);
    gl.deleteFramebuffer(resources.framebuffer); gl.deleteVertexArray(resources.vao); gl.deleteProgram(resources.program);
    cache.delete(gl); resources.scratch = new Uint8Array(0);
  }
  retained.clear();
}
