/// <reference lib="webworker" />
import {FilesetResolver, ImageSegmenter} from '@mediapipe/tasks-vision';
import {getHairModel} from '../../hair-live-preview/models.ts';
import type {HairModel} from '../../hair-live-preview/models.ts';
import {isRecord, sha256, validNonce} from '../../hair-live-preview/hair-protocol.ts';
import type {HairOutputMode} from '../../hair-live-preview/hair-protocol.ts';
import {CATEGORY_EXTRACTION_PROTOCOL, validateHairCostRequest} from './protocol.ts';
import type {HairRawOutput, HairWorkerRequest, HairWorkerResponse} from './protocol.ts';
import {extractCategoryMask} from './extraction.ts';

const scope = self as unknown as DedicatedWorkerGlobalScope;
let segmenter: ImageSegmenter | null = null, model: HairModel | null = null;
let busy = false, sessionNonce: string | null = null, lastRequestId = 0, lastSequence = -1;
let delegate: 'CPU' | 'GPU' = 'CPU';
let outputMode: HairOutputMode = 'full';
const post = (response: HairWorkerResponse, transfer: Transferable[] = []): void => scope.postMessage(response, transfer);
const closeBitmap = (value: unknown): void => {
  if (isRecord(value) && typeof ImageBitmap !== 'undefined' && value.image instanceof ImageBitmap) value.image.close();
};

scope.onmessage = async (event: MessageEvent<unknown>) => {
  const workerStarted = performance.now();
  const raw = event.data;
  let message: HairWorkerRequest;
  try { message = validateHairCostRequest(raw); }
  catch (error) {
    closeBitmap(raw);
    if (isRecord(raw) && validNonce(raw.sessionNonce) && typeof raw.requestId === 'number') {
      post({type: 'error', sessionNonce: raw.sessionNonce, requestId: raw.requestId, message: error instanceof Error ? error.message : String(error)});
    }
    return;
  }
  const inputValidationMs = performance.now() - workerStarted;
  if (busy) { closeBitmap(message); post({type: 'error', sessionNonce: message.sessionNonce, requestId: message.requestId,
    message: 'The hair worker already owns a request.'}); return; }
  busy = true;
  try {
    if (message.type === 'initialize') {
      if (segmenter || sessionNonce) throw new Error('A hair worker cannot replace an initialized session.');
      sessionNonce = message.sessionNonce; lastRequestId = message.requestId;
      delegate = message.delegate ?? 'CPU';
      outputMode = message.outputMode ?? 'full';
      const selected = getHairModel(message.modelId), url = new URL(selected.urlPath, scope.location.href);
      if (url.origin !== scope.location.origin) throw new Error('Hair weights must come from the local preview origin.');
      const start = performance.now(), response = await fetch(url, {cache: 'no-store', credentials: 'same-origin', redirect: 'error'});
      if (!response.ok) throw new Error(`The local hair model could not be loaded (${response.status}).`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength !== selected.bytes || await sha256(bytes) !== selected.sha256) throw new Error('Hair model weight hash or size differs from the reviewed model.');
      const files = await FilesetResolver.forVisionTasks(new URL('/mediapipe/', scope.location.href).href, true);
      segmenter = await ImageSegmenter.createFromOptions(files, {
        baseOptions: {modelAssetBuffer: bytes, delegate}, runningMode: 'IMAGE',
        outputCategoryMask: true, outputConfidenceMasks: outputMode === 'full', displayNamesLocale: 'en',
      });
      const labels = segmenter.getLabels();
      if (JSON.stringify(labels) !== JSON.stringify(selected.labels)) throw new Error('The loaded hair model has unexpected class labels.');
      model = selected;
      post({type: 'ready', requestId: message.requestId, sessionNonce: message.sessionNonce, model: selected.id,
        modelSHA256: selected.sha256, labels, hairIndex: selected.hairIndex, runningMode: 'IMAGE',
        initializationMs: performance.now() - start, delegate, outputMode, categoryExtractionProtocol: CATEGORY_EXTRACTION_PROTOCOL});
    } else {
      if (!segmenter || !model || message.sessionNonce !== sessionNonce || message.requestId <= lastRequestId || message.sequence <= lastSequence) {
        throw new Error('The hair image belongs to an unready or stale worker session.');
      }
      lastRequestId = message.requestId; lastSequence = message.sequence;
      const start = performance.now(), selected = model;
      let copied: HairRawOutput | null = null;
      // The callback avoids cloning every class mask. Only requested CPU arrays
      // are copied before callback return, when MediaPipe ends their lifetime.
      segmenter.segment(message.image, result => {
        try {
          const extractionStart = performance.now(), categoryMask = result.categoryMask;
          if (!categoryMask) throw new Error('The hair model returned incomplete masks.');
          if (categoryMask.width !== message.image.width || categoryMask.height !== message.image.height) {
            throw new Error('Hair output dimensions differ from the exact source image.');
          }
          const extracted = extractCategoryMask(categoryMask, message.categoryExtractionMode);
          const common = {sourceSHA256: message.sourceSHA256, sequence: message.sequence, model: selected.id, modelSHA256: selected.sha256,
            labels: [...selected.labels], hairIndex: selected.hairIndex, width: categoryMask.width, height: categoryMask.height,
            category: extracted.category, categoryExtraction: extracted.metrics, inferenceMs: extractionStart - start, delegate};
          if (outputMode === 'category-only') {
            if (result.confidenceMasks !== undefined) throw new Error('The category-only graph unexpectedly returned confidence masks.');
            copied = {...common, outputMode: 'category-only', extractionMs: performance.now() - extractionStart};
          } else {
            const confidenceMask = result.confidenceMasks?.[selected.hairIndex];
            if (!confidenceMask || result.confidenceMasks?.length !== selected.labels.length) throw new Error('The hair model returned incomplete masks.');
            if (confidenceMask.width !== categoryMask.width || confidenceMask.height !== categoryMask.height)
              throw new Error('Hair output dimensions differ from the exact source image.');
            copied = {...common, outputMode: 'full', confidence: confidenceMask.getAsFloat32Array().slice(),
              extractionMs: performance.now() - extractionStart};
          }
        } finally { result.close(); }
      });
      // Callback execution is synchronous; this is an assertion of its completed contract.
      const output = copied as HairRawOutput | null;
      if (!output) throw new Error('The hair segmenter did not complete its synchronous result callback.');
      const transfer = output.outputMode === 'category-only' ? [output.category.buffer] : [output.category.buffer, output.confidence.buffer];
      post({type: 'result', requestId: message.requestId, sessionNonce: message.sessionNonce,
        output: {...output, workerTiming: {inputValidationMs, totalMs: performance.now() - workerStarted}}}, transfer);
    }
  } catch (error) {
    if (message.type === 'initialize') { segmenter?.close(); segmenter = null; model = null; }
    post({type: 'error', requestId: message.requestId, sessionNonce: message.sessionNonce, message: error instanceof Error ? error.message : String(error)});
  } finally { closeBitmap(message); busy = false; }
};
