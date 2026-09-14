/// <reference lib="webworker" />

import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';
import { errorMessage, validateDetection } from './protocol.ts';
import type { DetectorRequest, DetectorResponse } from './protocol.ts';

const scope = self as unknown as DedicatedWorkerGlobalScope;
let detector: FaceLandmarker | null = null;
let busy = false;
let lastTimestampMs = -Infinity;

function reply(message: DetectorResponse): void {
  scope.postMessage(message);
}

scope.onmessage = async (event: MessageEvent<DetectorRequest>) => {
  const message = event.data;
  if (busy) {
    if (message.type === 'detect') message.image.close();
    reply({ type: 'error', id: message.id, message: 'The face tracking worker is busy.' });
    return;
  }
  busy = true;
  try {
    if (message.type === 'initialize') {
      if (detector) throw new Error('The face tracking worker is already initialized.');
      // The second parameter selects the ESM loader required by module workers.
      const fileset = await FilesetResolver.forVisionTasks(message.wasmRoot, true);
      detector = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: message.modelUrl, delegate: message.delegate },
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFacialTransformationMatrixes: true,
        outputFaceBlendshapes: false,
      });
      reply({ type: 'ready', id: message.id });
    } else {
      if (!detector) throw new Error('The face detector is not initialized.');
      if (!Number.isFinite(message.timestampMs) || message.timestampMs <= lastTimestampMs) {
        throw new Error('The frame timestamp did not increase.');
      }
      lastTimestampMs = message.timestampMs;
      const start = performance.now();
      const result = detector.detectForVideo(message.image, message.timestampMs);
      const inferenceMs = performance.now() - start;
      const landmarks = result.faceLandmarks[0] ?? [];
      const transformation = landmarks.length > 0 ? result.facialTransformationMatrixes[0] : undefined;
      const detection = validateDetection({
        landmarks,
        matrix: transformation ? Array.from(transformation.data) : null,
        inferenceMs,
      });
      reply({ type: 'result', id: message.id, detection });
    }
  } catch (error) {
    reply({ type: 'error', id: message.id, message: errorMessage(error) });
  } finally {
    if (message.type === 'detect') message.image.close();
    busy = false;
  }
};
