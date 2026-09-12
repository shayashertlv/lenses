/// <reference lib="webworker" />
// QA only: Playwright serves this module and its exact installed dependencies
// through private test routes. The production entry never imports this worker.
import {FilesetResolver, ImageSegmenter} from '@mediapipe/tasks-vision';
import {getHairModel} from '../../hair-live-preview/models.ts';
import type {HairModelId} from '../../hair-live-preview/models.ts';
import {extractCategoryMask} from './extraction.ts';
import {disposeRgba8Extraction, readRgba8Mask} from './rgba8-extraction.ts';

type Request = {id: number; type: 'initialize'; model: HairModelId; delegate: 'CPU' | 'GPU'}
  | {id: number; type: 'compare'; image: ImageBitmap; sourceId: string; mode?: 'direct' | 'rgba8'}
  | {id: number; type: 'codec'}
  | {id: number; type: 'close'};
const scope = self as unknown as DedicatedWorkerGlobalScope;
let segmenter: ImageSegmenter | null = null;
let previous: {direct: Uint8Array<ArrayBuffer>; sdk: Uint8Array<ArrayBuffer>; hash: string} | null = null;
const hash = async (bytes: Uint8Array<ArrayBuffer>): Promise<string> =>
  [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(n => n.toString(16).padStart(2, '0')).join('');
async function priorStillOwned(): Promise<boolean> {
  return !previous || await hash(previous.direct) === previous.hash && await hash(previous.sdk) === previous.hash;
}

scope.onmessage = async ({data}: MessageEvent<Request>) => {
  try {
    if (data.type === 'codec') {
      const words: number[] = [];
      for (let high = 0; high <= 0xffff; high++) for (const low of [0, 1, 0x7fff, 0x8000, 0xffff]) words.push(((high << 16) | low) >>> 0);
      const single = new Float32Array(1), word = new Uint32Array(single.buffer);
      for (let category = -1024; category <= 1024; category++) {
        single[0] = (category + .5) / 255; const center = word[0]!;
        for (const delta of [-2, -1, 0, 1, 2]) words.push((center + delta) >>> 0);
      }
      for (let i = 0; i <= 0xffff; i++) words.push(Math.imul(i, 2654435761) >>> 0);
      const width = 256, height = Math.ceil(words.length / width), bits = new Uint32Array(width * height); bits.set(words);
      const floats = new Float32Array(bits.buffer), expected = new Uint8Array(floats.map(value => Math.round(255 * value)));
      const canvas = new OffscreenCanvas(width, height), gl = canvas.getContext('webgl2');
      if (!gl) throw new Error('The RGBA8 shader audit requires real worker WebGL2.');
      const texture = gl.createTexture(); if (!texture) throw new Error('No QA source texture.');
      gl.bindTexture(gl.TEXTURE_2D, texture); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, width, height, 0, gl.RED, gl.FLOAT, floats);
      const callerTexture = gl.createTexture(), callerSampler = gl.createSampler(), callerVao = gl.createVertexArray();
      const callerPack = gl.createBuffer(), callerFramebuffer = gl.createFramebuffer();
      gl.bindVertexArray(callerVao); gl.bindFramebuffer(gl.FRAMEBUFFER, callerFramebuffer);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, callerTexture); gl.bindSampler(0, callerSampler);
      gl.activeTexture(gl.TEXTURE3); gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.viewport(7, 9, 17, 19); gl.colorMask(false, true, false, true);
      for (const cap of [gl.BLEND, gl.CULL_FACE, gl.DEPTH_TEST, gl.DITHER, gl.SCISSOR_TEST, gl.STENCIL_TEST,
        gl.RASTERIZER_DISCARD, gl.SAMPLE_ALPHA_TO_COVERAGE, gl.SAMPLE_COVERAGE]) gl.enable(cap);
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, callerPack); gl.bufferData(gl.PIXEL_PACK_BUFFER, 64, gl.STREAM_READ);
      gl.pixelStorei(gl.PACK_ALIGNMENT, 8); gl.pixelStorei(gl.PACK_ROW_LENGTH, 33);
      gl.pixelStorei(gl.PACK_SKIP_PIXELS, 2); gl.pixelStorei(gl.PACK_SKIP_ROWS, 3);
      const names = [gl.ACTIVE_TEXTURE, gl.VERTEX_ARRAY_BINDING, gl.CURRENT_PROGRAM, gl.DRAW_FRAMEBUFFER_BINDING,
        gl.READ_FRAMEBUFFER_BINDING, gl.PIXEL_PACK_BUFFER_BINDING, gl.PACK_ALIGNMENT, gl.PACK_ROW_LENGTH,
        gl.PACK_SKIP_PIXELS, gl.PACK_SKIP_ROWS, gl.VIEWPORT, gl.COLOR_WRITEMASK];
      const before = names.map(name => gl.getParameter(name) as unknown);
      if (gl.getError() !== gl.NO_ERROR) throw new Error('QA caller GL setup failed.');
      gl.enable(0); // A preexisting error must survive extraction, not be drained.
      const mask = {width, height, canvas, getAsWebGLTexture: () => texture};
      const first = readRgba8Mask(mask), second = readRgba8Mask(mask);
      if (!first.ok || !second.ok) throw new Error(`RGBA8 actual shader was not used: ${JSON.stringify([first.metrics, second.metrics])}`);
      let differences = 0; const firstDifferences: {index: number; bits: number; actual: number; expected: number}[] = [];
      for (let i = 0; i < expected.length; i++) if (first.category[i] !== expected[i] || second.category[i] !== expected[i]) {
        differences++; if (firstDifferences.length < 16) firstDifferences.push({index: i, bits: bits[i]!, actual: first.category[i]!, expected: expected[i]!});
      }
      const stateRestored = names.every((name, index) => {
        const actual: unknown = gl.getParameter(name), original = before[index];
        return ArrayBuffer.isView(original) || Array.isArray(original) ? JSON.stringify(actual) === JSON.stringify(original) : actual === original;
      }) && [gl.BLEND, gl.CULL_FACE, gl.DEPTH_TEST, gl.DITHER, gl.SCISSOR_TEST, gl.STENCIL_TEST,
        gl.RASTERIZER_DISCARD, gl.SAMPLE_ALPHA_TO_COVERAGE, gl.SAMPLE_COVERAGE].every(cap => gl.isEnabled(cap));
      gl.activeTexture(gl.TEXTURE0);
      const textureStateRestored = gl.getParameter(gl.TEXTURE_BINDING_2D) === callerTexture && gl.getParameter(gl.SAMPLER_BINDING) === callerSampler;
      const errorStatePreserved = gl.getError() === gl.INVALID_ENUM && gl.getError() === gl.NO_ERROR;
      const firstHash = await hash(first.category), secondHash = await hash(second.category), expectedHash = await hash(expected);
      disposeRgba8Extraction(); gl.deleteTexture(texture); gl.deleteTexture(callerTexture); gl.deleteSampler(callerSampler);
      gl.deleteVertexArray(callerVao); gl.deleteBuffer(callerPack); gl.deleteFramebuffer(callerFramebuffer);
      scope.postMessage({id: data.id, ok: true, width, height, comparedValues: words.length, differences, firstDifferences,
        firstHash, secondHash, expectedHash, stateRestored, textureStateRestored, errorStatePreserved,
        independentBuffers: first.category.buffer !== second.category.buffer,
        survivesResourceDisposal: await hash(first.category) === firstHash, first: first.metrics, second: second.metrics});
    } else if (data.type === 'initialize') {
      if (segmenter) throw new Error('QA worker cannot replace an initialized model.');
      const model = getHairModel(data.model);
      const response = await fetch(model.urlPath, {cache: 'no-store'});
      if (!response.ok) throw new Error(`Model response ${response.status}`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength !== model.bytes || await hash(bytes) !== model.sha256) throw new Error('Pinned model bytes differ.');
      const files = await FilesetResolver.forVisionTasks(new URL('/mediapipe/', scope.location.href).href, true);
      segmenter = await ImageSegmenter.createFromOptions(files, {baseOptions: {modelAssetBuffer: bytes, delegate: data.delegate},
        runningMode: 'IMAGE', outputCategoryMask: true, outputConfidenceMasks: false, displayNamesLocale: 'en'});
      const labels = segmenter.getLabels();
      if (JSON.stringify(labels) !== JSON.stringify(model.labels)) throw new Error('Pinned model labels differ.');
      scope.postMessage({id: data.id, ok: true, model: model.id, modelSHA256: model.sha256, labels,
        delegate: data.delegate, outputMode: 'category-only'});
    } else if (data.type === 'compare') {
      if (!segmenter) throw new Error('QA model not initialized.');
      const previousBefore = await priorStillOwned();
      let direct: ReturnType<typeof extractCategoryMask> | null = null;
      let sdk: ReturnType<typeof extractCategoryMask> | null = null;
      let width = 0, height = 0, confidenceAbsent = false;
      segmenter.segment(data.image, result => {
        try {
          const mask = result.categoryMask;
          if (!mask) throw new Error('Real model returned no category mask.');
          width = mask.width; height = mask.height;
          confidenceAbsent = result.confidenceMasks === undefined;
          // Ordering is essential: calling the SDK first could cache Uint8 and
          // make the direct path bypass the real GPU/float conversion entirely.
          direct = extractCategoryMask(mask, data.mode ?? 'direct');
          sdk = extractCategoryMask(mask, 'sdk');
        } finally { result.close(); }
      });
      const actual = direct as ReturnType<typeof extractCategoryMask> | null;
      const expected = sdk as ReturnType<typeof extractCategoryMask> | null;
      if (!actual || !expected) throw new Error('Real segmenter callback did not complete.');
      let differences = 0; const firstDifferences: number[] = [];
      for (let i = 0; i < actual.category.length; i++) if (actual.category[i] !== expected.category[i]) {
        differences++; if (firstDifferences.length < 12) firstDifferences.push(i);
      }
      const directSHA256 = await hash(actual.category), sdkSHA256 = await hash(expected.category);
      const previousAfter = await priorStillOwned();
      previous = {direct: actual.category, sdk: expected.category, hash: sdkSHA256};
      scope.postMessage({id: data.id, ok: true, sourceId: data.sourceId, width, height,
        imageWidth: data.image.width, imageHeight: data.image.height, confidenceAbsent,
        direct: actual.metrics, sdk: expected.metrics, categoryBytes: actual.category.byteLength,
        sdkCategoryBytes: expected.category.byteLength, independentBuffers: actual.category.buffer !== expected.category.buffer,
        differences, firstDifferences, directSHA256, sdkSHA256, previousBefore, previousAfter,
        categoryValues: [...new Set(actual.category)].sort((a, b) => a - b), comparedAfterResultClose: true});
    } else {
      disposeRgba8Extraction(); segmenter?.close(); segmenter = null;
      scope.postMessage({id: data.id, ok: true, lastOutputSurvivesSegmenterClose: await priorStillOwned()});
      previous = null;
    }
  } catch (error) {
    scope.postMessage({id: data.id, ok: false, message: error instanceof Error ? error.message : String(error)});
  } finally { if (data.type === 'compare') data.image.close(); }
};
