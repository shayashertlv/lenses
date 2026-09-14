/// <reference lib="webworker" />

import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';
import { errorMessage, validateDetection } from '../../references/perfect-temples/src/runtime/protocol.ts';
import type { DetectorResponse } from '../../references/perfect-temples/src/runtime/protocol.ts';
import type {TimedDetectorRequest, TimedDetectorResponse, WorkerFaceTiming, FaceDelegate} from './face-timing.ts';

const scope = self as unknown as DedicatedWorkerGlobalScope;
let detector: FaceLandmarker | null = null;
let busy = false;
let lastTimestampMs = -Infinity;
let sessionNonce: string | null = null;
let delegate: FaceDelegate = 'GPU';

function reply(message: DetectorResponse, nonce: string, timing?: WorkerFaceTiming): void {
  const response: TimedDetectorResponse = {...message, sessionNonce: nonce, ...(timing ? {timing} : {})};
  scope.postMessage(response);
}

scope.onmessage = async (event: MessageEvent<TimedDetectorRequest>) => {
  const receivedAtMs = performance.now();
  const message = event.data;
  if (busy) {
    if (message.type === 'detect') message.image.close();
    reply({ type: 'error', id: message.id, message: 'The face tracking worker is busy.' }, message.sessionNonce);
    return;
  }
  busy = true;
  try {
    if (typeof message.sessionNonce !== 'string' || !/^[A-Za-z0-9_-]{8,128}$/.test(message.sessionNonce)) {
      throw new Error('The face detector session identity is invalid.');
    }
    if (message.type === 'initialize') {
      if (detector) throw new Error('The face tracking worker is already initialized.');
      sessionNonce = message.sessionNonce; delegate = message.delegate;
      // The second parameter selects the ESM loader required by module workers.
      const fileset = await FilesetResolver.forVisionTasks(message.wasmRoot, true);
      detector = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: message.modelUrl, delegate: message.delegate },
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFacialTransformationMatrixes: true,
        outputFaceBlendshapes: false,
      });
      reply({ type: 'ready', id: message.id }, message.sessionNonce);
    } else {
      if (!detector) throw new Error('The face detector is not initialized.');
      if (message.sessionNonce !== sessionNonce) throw new Error('The face image belongs to another detector session.');
      if (!Number.isFinite(message.timestampMs) || message.timestampMs <= lastTimestampMs) {
        throw new Error('The frame timestamp did not increase.');
      }
      lastTimestampMs = message.timestampMs;
      const start = performance.now();
      const result = detector.detectForVideo(message.image, message.timestampMs);
      const inferenceEndedAtMs = performance.now();
      const inferenceMs = inferenceEndedAtMs - start;
      const landmarks = result.faceLandmarks[0] ?? [];
      const transformation = landmarks.length > 0 ? result.facialTransformationMatrixes[0] : undefined;
      const matrix = transformation ? Array.from(transformation.data) : null;
      const validationStartedAtMs = performance.now();
      const detection = validateDetection({
        landmarks,
        matrix,
        inferenceMs,
      });
      const validatedAtMs = performance.now();
      reply({ type: 'result', id: message.id, detection }, message.sessionNonce, {
        schema: 'face-worker-timing-v1', requestId: message.id, timestampMs: message.timestampMs,
        delegate, width: message.image.width, height: message.image.height,
        requestChecksMs: start - receivedAtMs, inferenceMs,
        extractionMs: validationStartedAtMs - inferenceEndedAtMs,
        validationMs: validatedAtMs - validationStartedAtMs, elapsedMs: validatedAtMs - receivedAtMs,
      });
    }
  } catch (error) {
    reply({ type: 'error', id: message.id, message: errorMessage(error) }, message.sessionNonce);
  } finally {
    if (message.type === 'detect') message.image.close();
    busy = false;
  }
};
