/// <reference lib="webworker" />
/** Hair segmenter worker: loads the pinned weights from this origin, verifies size and SHA-256, and runs MediaPipe's
 *  ImageSegmenter in IMAGE mode with the category mask only. */
import {FilesetResolver, ImageSegmenter} from '@mediapipe/tasks-vision';
import {getHairModel} from './models.ts';
import type {HairModel} from './models.ts';
import {isRecord, sha256, validateHairRequest, validNonce} from './protocol.ts';
import type {HairDelegate, HairOutput, HairWorkerRequest, HairWorkerResponse} from './protocol.ts';
import {assetPath} from '../assets.ts';

const scope = self as unknown as DedicatedWorkerGlobalScope;

/** MediaPipe decides from the user-agent string whether a worker may use OffscreenCanvas (Safari needs a
 *  "Version/17+" token); WebKit in-app browsers omit that token, and MediaPipe then reaches for `document`, which
 *  a worker does not have ("Can't find variable: document", 2026-09-15). The canvas is handed over instead. */
const workerCanvas = (): OffscreenCanvas | undefined => typeof OffscreenCanvas === 'function' ? new OffscreenCanvas(1, 1) : undefined;
let segmenter: ImageSegmenter | null = null, model: HairModel | null = null;
let busy = false, sessionNonce: string | null = null, lastRequestId = 0, lastSequence = -1;
let delegate: HairDelegate = 'CPU';
const post = (response: HairWorkerResponse, transfer: Transferable[] = []): void => scope.postMessage(response, transfer);
const closeBitmap = (value: unknown): void => {
  if (isRecord(value) && typeof ImageBitmap !== 'undefined' && value.image instanceof ImageBitmap) value.image.close();
};

scope.onmessage = async (event: MessageEvent<unknown>) => {
  const raw = event.data;
  let message: HairWorkerRequest;
  try { message = validateHairRequest(raw); }
  catch (error) {
    closeBitmap(raw);
    if (isRecord(raw) && validNonce(raw.sessionNonce) && typeof raw.requestId === 'number') {
      post({type: 'error', sessionNonce: raw.sessionNonce, requestId: raw.requestId, message: error instanceof Error ? error.message : String(error)});
    }
    return;
  }
  if (busy) { closeBitmap(message); post({type: 'error', sessionNonce: message.sessionNonce, requestId: message.requestId,
    message: 'The hair worker already owns a request.'}); return; }
  busy = true;
  try {
    if (message.type === 'initialize') {
      if (segmenter || sessionNonce) throw new Error('A hair worker cannot replace an initialized session.');
      sessionNonce = message.sessionNonce; lastRequestId = message.requestId;
      delegate = message.delegate;
      const selected = getHairModel(message.modelId), url = new URL(selected.urlPath, scope.location.href);
      if (url.origin !== scope.location.origin) throw new Error('Hair weights must come from this origin.');
      const start = performance.now(), response = await fetch(url, {cache: 'no-store', credentials: 'same-origin', redirect: 'error'});
      if (!response.ok) throw new Error(`The hair model could not be loaded (${response.status}).`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength !== selected.bytes || await sha256(bytes) !== selected.sha256) throw new Error('Hair model weight hash or size differs from the pinned model.');
      // MediaPipe appends "/<module>.js" to the root itself; a trailing slash would double it.
      const files = await FilesetResolver.forVisionTasks(new URL(assetPath('mediapipe'), scope.location.href).href, true);
      segmenter = await ImageSegmenter.createFromOptions(files, {
        canvas: workerCanvas(),
        baseOptions: {modelAssetBuffer: bytes, delegate}, runningMode: 'IMAGE',
        outputCategoryMask: true, outputConfidenceMasks: false, displayNamesLocale: 'en',
      });
      const labels = segmenter.getLabels();
      if (JSON.stringify(labels) !== JSON.stringify(selected.labels)) throw new Error('The loaded hair model has unexpected class labels.');
      model = selected;
      post({type: 'ready', requestId: message.requestId, sessionNonce: message.sessionNonce, model: selected.id,
        modelSHA256: selected.sha256, labels, hairIndex: selected.hairIndex, runningMode: 'IMAGE',
        initializationMs: performance.now() - start, delegate});
    } else {
      if (!segmenter || !model || message.sessionNonce !== sessionNonce || message.requestId <= lastRequestId || message.sequence <= lastSequence) {
        throw new Error('The hair image belongs to an unready or stale worker session.');
      }
      lastRequestId = message.requestId; lastSequence = message.sequence;
      const start = performance.now(), selected = model;
      let copied: HairOutput | null = null;
      // The callback avoids cloning every class mask; the category array is copied before the callback returns, when
      // MediaPipe ends its lifetime.
      segmenter.segment(message.image, result => {
        try {
          const extractionStart = performance.now(), categoryMask = result.categoryMask;
          if (!categoryMask) throw new Error('The hair model returned no category mask.');
          if (result.confidenceMasks !== undefined) throw new Error('The category-only graph unexpectedly returned confidence masks.');
          if (categoryMask.width !== message.image.width || categoryMask.height !== message.image.height) {
            throw new Error('Hair output dimensions differ from the exact source image.');
          }
          copied = {sourceSHA256: message.sourceSHA256, sequence: message.sequence, model: selected.id, modelSHA256: selected.sha256,
            labels: [...selected.labels], hairIndex: selected.hairIndex, width: categoryMask.width, height: categoryMask.height,
            category: categoryMask.getAsUint8Array().slice(), inferenceMs: extractionStart - start, delegate,
            extractionMs: performance.now() - extractionStart};
        } finally { result.close(); }
      });
      // Callback execution is synchronous; this is an assertion of its completed contract.
      const output = copied as HairOutput | null;
      if (!output) throw new Error('The hair segmenter did not complete its synchronous result callback.');
      post({type: 'result', requestId: message.requestId, sessionNonce: message.sessionNonce, output}, [output.category.buffer]);
    }
  } catch (error) {
    if (message.type === 'initialize') { segmenter?.close(); segmenter = null; model = null; }
    post({type: 'error', requestId: message.requestId, sessionNonce: message.sessionNonce, message: error instanceof Error ? error.message : String(error)});
  } finally { closeBitmap(message); busy = false; }
};
