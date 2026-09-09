import type {WebGLRenderer} from 'three';
import {GPU_HAIR_PACKED_FLAGS_FRAGMENT_GLSL, GPU_HAIR_COMPOSE_FRAGMENT_GLSL,
  GPU_HAIR_AUDIT_FRAGMENT_GLSL} from '../gpu-rules.ts';

export interface GpuFrameMetrics {
  captureCalls: number; beautyCaptureCalls: number; cameraCaptureCalls: number; gpuCaptureMs: number;
  uploadCalls: number; uploadBytes: number; flagsSubmitMs: number; flagsReadbackMs: number;
  referenceReduceMs: number; composeSubmitMs: number; auditSubmitMs: number; auditReduceMs: number;
  publishSubmitMs: number; diagnosticReadbackMs: number; shaderCompileMs: number; stateSaveRestoreMs: number;
  readbackCalls: number; readbackBytes: number; fullFrameReadbackCalls: number; fullFrameReadbackBytes: number;
  compactReadbackCalls: number; compactReadbackBytes: number; gpuStorageBytesRequested: number; cpuBytesAllocated: number;
}
const emptyMetrics = (): GpuFrameMetrics => ({captureCalls: 0, beautyCaptureCalls: 0, cameraCaptureCalls: 0, gpuCaptureMs: 0,
  uploadCalls: 0, uploadBytes: 0, flagsSubmitMs: 0, flagsReadbackMs: 0, referenceReduceMs: 0, composeSubmitMs: 0,
  auditSubmitMs: 0, auditReduceMs: 0, publishSubmitMs: 0, diagnosticReadbackMs: 0, shaderCompileMs: 0,
  stateSaveRestoreMs: 0, readbackCalls: 0, readbackBytes: 0, fullFrameReadbackCalls: 0, fullFrameReadbackBytes: 0,
  compactReadbackCalls: 0, compactReadbackBytes: 0, gpuStorageBytesRequested: 0, cpuBytesAllocated: 0});
export interface GpuHairUpload {
  category: Uint8Array; maskWidth: number; maskHeight: number; hairIndex: number; regions: Uint8Array;
}
export interface GpuPackedFlags {
  width: number; height: number; bytes: Uint8Array; rowOrder: 'bottom-up'; referenceMaxDelta: number;
}
export interface GpuAuditResult {
  protectedMaxDelta: number; noseMaxDelta: number; outsideEditableMaxDelta: number;
  backgroundPreservationMaxDelta: number; passed: boolean;
}
type PixelVariant = 'beauty' | 'camera' | 'output';
interface Target {texture: WebGLTexture; framebuffer: WebGLFramebuffer; width: number; height: number; integer: boolean;}
interface Program {program: WebGLProgram; uniforms: Map<string, WebGLUniformLocation | null>;}
const VERTEX = `#version 300 es
const vec2 points[3]=vec2[3](vec2(-1,-1),vec2(3,-1),vec2(-1,3));
void main(){gl_Position=vec4(points[gl_VertexID],0,1);}`;
const PRESENT = `#version 300 es
precision highp float; precision highp int; uniform sampler2D uImage; out vec4 color;
void main(){color=texelFetch(uImage,ivec2(gl_FragCoord.xy),0);}`;
const REDUCE = `#version 300 es
precision highp float; precision highp int; uniform highp usampler2D uImage; uniform ivec2 uSourceSize;
layout(location=0) out highp uvec4 maximum;
void main(){ivec2 base=ivec2(gl_FragCoord.xy)*8;uvec4 value=uvec4(0);
for(int y=0;y<8;y++)for(int x=0;x<8;x++){ivec2 p=base+ivec2(x,y);
if(all(lessThan(p,uSourceSize)))value=max(value,texelFetch(uImage,p,0));}maximum=value;}`;

/** A generation-checked lease: pixels remain private and immutable to callers.
 * Native capture textures cannot escape or survive a new source presentation. */
export class GpuNativeFrame {
  private readonly owner: NativeGpuFrames;
  readonly generation: number;
  readonly width: number;
  readonly height: number;
  readonly cameraReady: boolean;
  constructor(owner: NativeGpuFrames, generation: number, width: number, height: number, cameraReady: boolean) {
    this.owner = owner; this.generation = generation; this.width = width; this.height = height; this.cameraReady = cameraReady;
  }
  get metrics(): GpuFrameMetrics {this.owner.assertLease(this.generation); return this.owner.metrics;}
  runPackedFlags(input: GpuHairUpload): GpuPackedFlags {this.owner.assertLease(this.generation); return this.owner.runPackedFlags(input);}
  compose(detachedIndices: readonly number[] | Int32Array | Uint32Array): void {this.owner.assertLease(this.generation); this.owner.compose(detachedIndices);}
  audit(): GpuAuditResult {this.owner.assertLease(this.generation); return this.owner.audit();}
  presentToCanvas(variant: 'beauty' | 'output'): HTMLCanvasElement {this.owner.assertLease(this.generation); return this.owner.presentToCanvas(variant);}
  readDiagnostic(variant: PixelVariant): ImageData {this.owner.assertLease(this.generation); return this.owner.readDiagnostic(variant);}
}

/** Owns raw resources in the original Three context. Every raw operation restores
 * the touched GL state, leaving Three's caches consistent without resetting it. */
export class NativeGpuFrames {
  private readonly renderer: WebGLRenderer;
  private readonly gl: WebGL2RenderingContext;
  private readonly programs = new Map<string, Program>();
  private readonly textures = new Map<string, Target>();
  private readonly reductions: Target[] = [];
  private vao: WebGLVertexArrayObject | null = null;
  private flagsFramebuffer: WebGLFramebuffer | null = null;
  private generation = 0;
  private width = 0;
  private height = 0;
  private beautyReady = false;
  private cameraReady = false;
  private inputsReady = false;
  private outputReady = false;
  private outputAudited = false;
  private disposed = false;
  private maskWidth = 0;
  private maskHeight = 0;
  private hairIndex = 0;
  private detached = new Uint8Array(0);
  private measured = emptyMetrics();

  constructor(renderer: WebGLRenderer) {
    this.renderer = renderer;
    const context = renderer.getContext();
    if (!('texStorage2D' in context)) throw new Error('GPU frame ownership requires WebGL 2.');
    this.gl = context;
    if (context.getContextAttributes()?.alpha !== false) throw new Error('Exact native GPU capture requires the accepted opaque framebuffer.');
  }
  get metrics(): GpuFrameMetrics {return {...this.measured};}
  invalidate(): void {
    this.generation++; this.beautyReady = this.cameraReady = this.inputsReady = this.outputReady = this.outputAudited = false;
  }
  assertLease(generation: number): void {
    if (this.disposed || this.gl.isContextLost() || generation !== this.generation || !this.beautyReady)
      throw new Error('The native GPU frame lease is stale or unavailable.');
  }
  begin(width: number, height: number): void {
    this.invalidate(); this.measured = emptyMetrics();
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0)
      throw new Error('Invalid native GPU viewport.');
    this.width = width; this.height = height;
    this.withState(() => {this.target('beauty', width, height, this.gl.RGB8, false);});
  }
  capture(variant: 'beauty' | 'camera'): void {
    if (variant === 'beauty' ? this.beautyReady : !this.beautyReady || this.cameraReady)
      throw new Error('GPU captures must follow current beauty then optional camera.');
    const started = performance.now();
    try {this.withState(() => {
      const gl = this.gl, target = this.target(variant, this.width, this.height, gl.RGB8, false);
      gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, target.texture);
      // Copy the original opaque multisampled default buffer into matching RGB8
      // storage. A direct blit lost the second beauty on SwiftShader after canvas
      // publication unless a diagnostic readPixels first forced its resolve.
      gl.copyTexSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 0, 0, this.width, this.height);
      this.measured.captureCalls++; if (variant === 'beauty') this.measured.beautyCaptureCalls++; else this.measured.cameraCaptureCalls++;
      this.check('native RGB8 capture');
      if (variant === 'beauty') this.beautyReady = true; else this.cameraReady = true;
    });} finally {this.measured.gpuCaptureMs += performance.now() - started;}
  }
  lease(): GpuNativeFrame {
    this.assertLease(this.generation);
    return new GpuNativeFrame(this, this.generation, this.width, this.height, this.cameraReady);
  }

  runPackedFlags(input: GpuHairUpload): GpuPackedFlags {
    this.assertLease(this.generation);
    if (!this.cameraReady) throw new Error('The current GPU pair has no clean camera capture.');
    if (!Number.isSafeInteger(input.maskWidth) || !Number.isSafeInteger(input.maskHeight) || input.maskWidth <= 0 || input.maskHeight <= 0
      || input.category.length !== input.maskWidth * input.maskHeight || input.regions.length !== this.width * this.height
      || !Number.isInteger(input.hairIndex) || input.hairIndex < 0 || input.hairIndex > 255)
      throw new Error('GPU hair input dimensions differ from the owned pair.');
    this.inputsReady = this.outputReady = this.outputAudited = false;
    return this.withState(() => {
      const gl = this.gl;
      this.maskWidth = input.maskWidth; this.maskHeight = input.maskHeight; this.hairIndex = input.hairIndex;
      this.upload('category', input.maskWidth, input.maskHeight, input.category);
      this.upload('regions', this.width, this.height, input.regions);
      const packedWidth = Math.ceil(this.width / 4);
      const flags = this.target('flags', packedWidth, this.height, gl.RGBA8UI, true);
      const reference = this.target('reference', packedWidth, this.height, gl.RGBA8UI, true);
      this.flagsFramebuffer ??= gl.createFramebuffer();
      if (!this.flagsFramebuffer) throw new Error('GPU flag framebuffer allocation failed.');
      gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, this.flagsFramebuffer);
      gl.framebufferTexture2D(gl.DRAW_FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, flags.texture, 0);
      gl.framebufferTexture2D(gl.DRAW_FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, reference.texture, 0);
      gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
      if (gl.checkFramebufferStatus(gl.DRAW_FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error('GPU flag framebuffer is incomplete.');
      const started = performance.now(), program = this.program('flags', GPU_HAIR_PACKED_FLAGS_FRAGMENT_GLSL);
      this.bindHair(program); gl.viewport(0, 0, packedWidth, this.height); gl.drawArrays(gl.TRIANGLES, 0, 3);
      this.check('packed hair flags'); this.measured.flagsSubmitMs += performance.now() - started;
      const readStarted = performance.now(), bytes = this.readBytes(flags, true);
      this.measured.flagsReadbackMs += performance.now() - readStarted;
      const referenceStarted = performance.now(), maxima = this.reduce(reference);
      this.measured.referenceReduceMs += performance.now() - referenceStarted;
      this.inputsReady = true;
      return {width: this.width, height: this.height, bytes, rowOrder: 'bottom-up', referenceMaxDelta: Math.max(...maxima)};
    });
  }

  compose(detachedIndices: readonly number[] | Int32Array | Uint32Array): void {
    this.assertLease(this.generation);
    if (!this.inputsReady) throw new Error('GPU composition requires current validated flags.');
    this.outputReady = this.outputAudited = false;
    if (this.detached.length !== this.width * this.height) {
      this.detached = new Uint8Array(this.width * this.height); this.measured.cpuBytesAllocated += this.detached.byteLength;
    } else this.detached.fill(0);
    for (const index of detachedIndices) {
      if (!Number.isInteger(index) || index < 0 || index >= this.detached.length) throw new Error('A detached temple index is outside the current pair.');
      this.detached[index] = 1;
    }
    const started = performance.now();
    try {this.withState(() => {
      const gl = this.gl;
      this.upload('detached', this.width, this.height, this.detached);
      const output = this.target('output', this.width, this.height, gl.RGBA8, false);
      this.drawTarget(output); this.bindHair(this.program('compose', GPU_HAIR_COMPOSE_FRAGMENT_GLSL));
      gl.drawArrays(gl.TRIANGLES, 0, 3); this.check('hair composition'); this.outputReady = true;
    });} finally {this.measured.composeSubmitMs += performance.now() - started;}
  }

  audit(): GpuAuditResult {
    this.assertLease(this.generation);
    if (!this.outputReady) throw new Error('The current GPU pair has no completed output to audit.');
    return this.withState(() => {
      const gl = this.gl, audit = this.target('audit', this.width, this.height, gl.RGBA8UI, true);
      const started = performance.now();
      this.drawTarget(audit); this.bindHair(this.program('audit', GPU_HAIR_AUDIT_FRAGMENT_GLSL));
      gl.drawArrays(gl.TRIANGLES, 0, 3); this.check('final pixel guards');
      this.measured.auditSubmitMs += performance.now() - started;
      const reduceStarted = performance.now(), values = this.reduce(audit);
      this.measured.auditReduceMs += performance.now() - reduceStarted;
      const passed = values.every(value => value === 0); this.outputAudited = passed;
      return {protectedMaxDelta: values[0]!, noseMaxDelta: values[1]!, outsideEditableMaxDelta: values[2]!,
        backgroundPreservationMaxDelta: values[3]!, passed};
    });
  }

  presentToCanvas(variant: 'beauty' | 'output'): HTMLCanvasElement {
    this.assertLease(this.generation);
    if (variant === 'output' && !this.outputAudited) throw new Error('GPU output must pass final guards before publication.');
    const started = performance.now();
    try {this.withState(() => {
      const gl = this.gl, target = this.requireTarget(variant), program = this.program('present', PRESENT);
      gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, null); gl.drawBuffers([gl.BACK]); gl.viewport(0, 0, this.width, this.height);
      gl.useProgram(program.program); this.bindTexture(program, 'uImage', target, 0);
      gl.drawArrays(gl.TRIANGLES, 0, 3); this.check('native canvas publication');
    });} finally {this.measured.publishSubmitMs += performance.now() - started;}
    return this.renderer.domElement;
  }
  readDiagnostic(variant: PixelVariant): ImageData {
    this.assertLease(this.generation);
    if (variant === 'camera' && !this.cameraReady) throw new Error('No current clean camera image exists.');
    if (variant === 'output' && !this.outputReady) throw new Error('No current composed image exists.');
    const started = performance.now();
    try {return this.withState(() => {
      const bottom = this.readBytes(this.requireTarget(variant), false), top = new Uint8ClampedArray(bottom.length);
      this.measured.cpuBytesAllocated += top.byteLength;
      const row = this.width * 4;
      for (let y = 0; y < this.height; y++) top.set(bottom.subarray((this.height - y - 1) * row, (this.height - y) * row), y * row);
      return new ImageData(top, this.width, this.height);
    });} finally {this.measured.diagnosticReadbackMs += performance.now() - started;}
  }

  private check(operation: string): void {
    const error = this.gl.getError();
    if (error !== this.gl.NO_ERROR || this.gl.isContextLost()) throw new Error(`GPU ${operation} failed (GL 0x${error.toString(16)}).`);
  }
  private requireTarget(name: string): Target {
    const target = this.textures.get(name); if (!target) throw new Error(`The current GPU ${name} texture is unavailable.`); return target;
  }
  private target(name: string, width: number, height: number, format: number, integer: boolean): Target {
    const previous = this.textures.get(name);
    if (previous?.width === width && previous.height === height) return previous;
    if (previous) {this.gl.deleteTexture(previous.texture); this.gl.deleteFramebuffer(previous.framebuffer); this.textures.delete(name);}
    const target = this.createTarget(width, height, format, integer); this.textures.set(name, target); return target;
  }
  private createTarget(width: number, height: number, format: number, integer: boolean): Target {
    const gl = this.gl, texture = gl.createTexture(), framebuffer = gl.createFramebuffer();
    if (!texture || !framebuffer) {
      if (texture) gl.deleteTexture(texture); if (framebuffer) gl.deleteFramebuffer(framebuffer);
      throw new Error('Native GPU storage allocation failed.');
    }
    try {
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texStorage2D(gl.TEXTURE_2D, 1, format, width, height);
      gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, framebuffer);
      gl.framebufferTexture2D(gl.DRAW_FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
      gl.drawBuffers([gl.COLOR_ATTACHMENT0]);
      if (gl.checkFramebufferStatus(gl.DRAW_FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error('Native GPU storage is incomplete.');
      this.check('storage allocation');
      this.measured.gpuStorageBytesRequested += width * height * (format === gl.RGB8 ? 3 : format === gl.R8UI ? 1 : 4);
      return {texture, framebuffer, width, height, integer};
    } catch (error) {gl.deleteTexture(texture); gl.deleteFramebuffer(framebuffer); throw error;}
  }
  private upload(name: string, width: number, height: number, bytes: Uint8Array): void {
    const gl = this.gl, target = this.target(name, width, height, gl.R8UI, true);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, target.texture);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, width, height, gl.RED_INTEGER, gl.UNSIGNED_BYTE, bytes);
    this.measured.uploadCalls++; this.measured.uploadBytes += bytes.byteLength; this.check(`${name} upload`);
  }
  private program(name: string, source: string): Program {
    const prior = this.programs.get(name); if (prior) return prior;
    const gl = this.gl, started = performance.now(), program = gl.createProgram();
    if (!program) throw new Error('GPU shader program allocation failed.');
    try {
      for (const [type, text] of [[gl.VERTEX_SHADER, VERTEX], [gl.FRAGMENT_SHADER, source]] as const) {
        const shader = gl.createShader(type); if (!shader) throw new Error('GPU shader allocation failed.');
        gl.shaderSource(shader, text); gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
          const error = gl.getShaderInfoLog(shader); gl.deleteShader(shader); throw new Error(`GPU ${name} shader: ${error}`);
        }
        gl.attachShader(program, shader); gl.deleteShader(shader);
      }
      gl.linkProgram(program); if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(`GPU ${name} link: ${gl.getProgramInfoLog(program)}`);
      const result = {program, uniforms: new Map<string, WebGLUniformLocation | null>()}; this.programs.set(name, result); return result;
    } catch (error) {gl.deleteProgram(program); throw error;}
    finally {this.measured.shaderCompileMs += performance.now() - started;}
  }
  private uniform(program: Program, name: string): WebGLUniformLocation | null {
    if (!program.uniforms.has(name)) program.uniforms.set(name, this.gl.getUniformLocation(program.program, name));
    return program.uniforms.get(name)!;
  }
  private bindTexture(program: Program, name: string, target: Target, slot: number): void {
    this.gl.activeTexture(this.gl.TEXTURE0 + slot); this.gl.bindTexture(this.gl.TEXTURE_2D, target.texture);
    this.gl.bindSampler(slot, null); this.gl.uniform1i(this.uniform(program, name), slot);
  }
  private bindHair(program: Program): void {
    const gl = this.gl; gl.useProgram(program.program);
    for (const [slot, name, uniform] of [[0, 'beauty', 'uBeauty'], [1, 'camera', 'uCamera'], [2, 'category', 'uCategory'],
      [3, 'regions', 'uRegions'], [4, 'detached', 'uDetached'], [5, 'output', 'uOutput']] as const) {
      const target = this.textures.get(name); if (target) this.bindTexture(program, uniform, target, slot);
    }
    gl.uniform2i(this.uniform(program, 'uSize'), this.width, this.height);
    gl.uniform2i(this.uniform(program, 'uMaskSize'), this.maskWidth, this.maskHeight);
    gl.uniform1ui(this.uniform(program, 'uHairIndex'), this.hairIndex);
  }
  private drawTarget(target: Target): void {
    this.gl.bindFramebuffer(this.gl.DRAW_FRAMEBUFFER, target.framebuffer);
    this.gl.drawBuffers([this.gl.COLOR_ATTACHMENT0]); this.gl.viewport(0, 0, target.width, target.height);
  }
  private readBytes(target: Target, compact: boolean): Uint8Array {
    const gl = this.gl, bytes = new Uint8Array(target.width * target.height * 4);
    this.measured.cpuBytesAllocated += bytes.byteLength;
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, target.framebuffer);
    gl.readPixels(0, 0, target.width, target.height, target.integer ? gl.RGBA_INTEGER : gl.RGBA, gl.UNSIGNED_BYTE, bytes);
    this.measured.readbackCalls++; this.measured.readbackBytes += bytes.byteLength;
    if (compact) {this.measured.compactReadbackCalls++; this.measured.compactReadbackBytes += bytes.byteLength;}
    else {this.measured.fullFrameReadbackCalls++; this.measured.fullFrameReadbackBytes += bytes.byteLength;}
    this.check('pixel readback'); return bytes;
  }
  private reduce(source: Target): Uint8Array {
    const gl = this.gl, program = this.program('reduce', REDUCE);
    let current = source, level = 0;
    while (current.width > 1 || current.height > 1) {
      const width = Math.ceil(current.width / 8), height = Math.ceil(current.height / 8);
      let next = this.reductions[level];
      if (!next || next.width !== width || next.height !== height) {
        if (next) {gl.deleteTexture(next.texture); gl.deleteFramebuffer(next.framebuffer);}
        next = this.createTarget(width, height, gl.RGBA8UI, true); this.reductions[level] = next;
      }
      this.drawTarget(next); gl.useProgram(program.program); this.bindTexture(program, 'uImage', current, 0);
      gl.uniform2i(this.uniform(program, 'uSourceSize'), current.width, current.height);
      gl.drawArrays(gl.TRIANGLES, 0, 3); current = next; level++;
    }
    this.check('guard reduction'); return this.readBytes(current, true);
  }

  private withState<T>(operation: () => T): T {
    if (this.disposed || this.gl.isContextLost()) throw new Error('The native GPU context is unavailable.');
    if (this.renderer.getRenderTarget() !== null) throw new Error('Native GPU operations require the original default render target.');
    const gl = this.gl, savedAt = performance.now();
    const caps = [gl.BLEND, gl.CULL_FACE, gl.DEPTH_TEST, gl.SCISSOR_TEST, gl.STENCIL_TEST, gl.SAMPLE_ALPHA_TO_COVERAGE,
      gl.SAMPLE_COVERAGE, gl.DITHER, gl.RASTERIZER_DISCARD];
    const stores = [gl.PACK_ALIGNMENT, gl.PACK_ROW_LENGTH, gl.PACK_SKIP_PIXELS, gl.PACK_SKIP_ROWS,
      gl.UNPACK_ALIGNMENT, gl.UNPACK_ROW_LENGTH, gl.UNPACK_SKIP_PIXELS, gl.UNPACK_SKIP_ROWS,
      gl.UNPACK_FLIP_Y_WEBGL, gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, gl.UNPACK_COLORSPACE_CONVERSION_WEBGL];
    const saved = {read: gl.getParameter(gl.READ_FRAMEBUFFER_BINDING) as WebGLFramebuffer | null,
      draw: gl.getParameter(gl.DRAW_FRAMEBUFFER_BINDING) as WebGLFramebuffer | null,
      drawBuffer: gl.getParameter(gl.DRAW_BUFFER0) as number,
      program: gl.getParameter(gl.CURRENT_PROGRAM) as WebGLProgram | null,
      vao: gl.getParameter(gl.VERTEX_ARRAY_BINDING) as WebGLVertexArrayObject | null,
      active: gl.getParameter(gl.ACTIVE_TEXTURE) as number,
      viewport: Array.from(gl.getParameter(gl.VIEWPORT) as Int32Array), scissor: Array.from(gl.getParameter(gl.SCISSOR_BOX) as Int32Array),
      colors: gl.getParameter(gl.COLOR_WRITEMASK) as boolean[], depth: gl.getParameter(gl.DEPTH_WRITEMASK) as boolean,
      pack: gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING) as WebGLBuffer | null, unpack: gl.getParameter(gl.PIXEL_UNPACK_BUFFER_BINDING) as WebGLBuffer | null,
      caps: caps.map(cap => gl.isEnabled(cap)), stores: stores.map(store => Number(gl.getParameter(store))),
      units: [] as {texture: WebGLTexture | null; sampler: WebGLSampler | null}[]};
    if (saved.draw !== null) throw new Error('A raw GPU pass cannot interrupt an offscreen native draw.');
    for (let unit = 0; unit < 6; unit++) {
      gl.activeTexture(gl.TEXTURE0 + unit); saved.units.push({texture: gl.getParameter(gl.TEXTURE_BINDING_2D) as WebGLTexture | null,
        sampler: gl.getParameter(gl.SAMPLER_BINDING) as WebGLSampler | null});
    }
    gl.activeTexture(saved.active); this.measured.stateSaveRestoreMs += performance.now() - savedAt;
    try {
      this.vao ??= gl.createVertexArray(); if (!this.vao) throw new Error('GPU vertex array allocation failed.');
      gl.bindVertexArray(this.vao); for (const cap of caps) gl.disable(cap);
      gl.colorMask(true, true, true, true); gl.depthMask(false);
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null); gl.bindBuffer(gl.PIXEL_UNPACK_BUFFER, null);
      for (const store of stores) gl.pixelStorei(store, store === gl.PACK_ALIGNMENT || store === gl.UNPACK_ALIGNMENT ? 1 : 0);
      return operation();
    } finally {
      const restoredAt = performance.now();
      gl.bindFramebuffer(gl.READ_FRAMEBUFFER, saved.read); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, saved.draw); gl.drawBuffers([saved.drawBuffer]);
      gl.useProgram(saved.program); gl.bindVertexArray(saved.vao);
      gl.viewport(saved.viewport[0]!, saved.viewport[1]!, saved.viewport[2]!, saved.viewport[3]!);
      gl.scissor(saved.scissor[0]!, saved.scissor[1]!, saved.scissor[2]!, saved.scissor[3]!);
      gl.colorMask(saved.colors[0]!, saved.colors[1]!, saved.colors[2]!, saved.colors[3]!); gl.depthMask(saved.depth);
      for (let i = 0; i < caps.length; i++) if (saved.caps[i]) gl.enable(caps[i]!); else gl.disable(caps[i]!);
      for (let i = 0; i < stores.length; i++) gl.pixelStorei(stores[i]!, saved.stores[i]!);
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, saved.pack); gl.bindBuffer(gl.PIXEL_UNPACK_BUFFER, saved.unpack);
      saved.units.forEach((unit, index) => {gl.activeTexture(gl.TEXTURE0 + index); gl.bindTexture(gl.TEXTURE_2D, unit.texture); gl.bindSampler(index, unit.sampler);});
      gl.activeTexture(saved.active); this.measured.stateSaveRestoreMs += performance.now() - restoredAt;
    }
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.invalidate();
    for (const target of [...this.textures.values(), ...this.reductions]) {this.gl.deleteTexture(target.texture); this.gl.deleteFramebuffer(target.framebuffer);}
    this.textures.clear(); this.reductions.length = 0;
    for (const entry of this.programs.values()) this.gl.deleteProgram(entry.program); this.programs.clear();
    if (this.flagsFramebuffer) this.gl.deleteFramebuffer(this.flagsFramebuffer); this.flagsFramebuffer = null;
    if (this.vao) this.gl.deleteVertexArray(this.vao); this.vao = null; this.detached = new Uint8Array(0);
  }
}
