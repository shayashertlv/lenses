/// <reference lib="webworker" />
// QA only: Playwright serves this module and its exact installed dependencies
// through private test routes. The production entry never imports this worker.
import {FilesetResolver, ImageSegmenter} from '@mediapipe/tasks-vision';
import {getHairModel} from '../../hair-live-preview/models.ts';
import type {HairModelId} from '../../hair-live-preview/models.ts';
import {extractCategoryMask} from './extraction.ts';

type Request = {id: number; type: 'initialize'; model: HairModelId; delegate: 'CPU' | 'GPU'}
  | {id: number; type: 'compare'; image: ImageBitmap; sourceId: string}
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
    if (data.type === 'initialize') {
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
          direct = extractCategoryMask(mask, 'direct');
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
      segmenter?.close(); segmenter = null;
      scope.postMessage({id: data.id, ok: true, lastOutputSurvivesSegmenterClose: await priorStillOwned()});
      previous = null;
    }
  } catch (error) {
    scope.postMessage({id: data.id, ok: false, message: error instanceof Error ? error.message : String(error)});
  } finally { if (data.type === 'compare') data.image.close(); }
};
