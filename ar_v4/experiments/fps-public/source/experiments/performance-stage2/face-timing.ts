import type {Detection, DetectorRequest, DetectorResponse} from '../../references/perfect-temples/src/runtime/protocol.ts';

export type FaceDelegate = 'GPU' | 'CPU';
export type TimedDetectorRequest = DetectorRequest & {sessionNonce: string};
export interface WorkerFaceTiming {
  schema: 'face-worker-timing-v1';
  requestId: number;
  timestampMs: number;
  delegate: FaceDelegate;
  width: number;
  height: number;
  requestChecksMs: number;
  inferenceMs: number;
  extractionMs: number;
  validationMs: number;
  /** Worker receipt through validated result, before response serialization/posting. */
  elapsedMs: number;
}
export type TimedDetectorResponse = DetectorResponse & {sessionNonce: string; timing?: WorkerFaceTiming};

export interface FaceStageTiming {
  requestId: number;
  timestampMs: number;
  sessionNonce: string;
  delegate: FaceDelegate;
  width: number;
  height: number;
  /** Same pure detectForVideo duration already present in the unchanged Detection. */
  inferenceMs: number;
  workerTimingStatus: 'valid' | 'missing' | 'invalid';
  workerRequestChecksMs: number | null;
  workerExtractionMs: number | null;
  workerValidationMs: number | null;
  workerElapsedMs: number | null;
  /** Synchronous main-thread postMessage call, including transfer submission. */
  postMessageMs: number;
  /** Main-thread send start to receipt; excludes client result validation. */
  roundTripMs: number;
  clientValidationMs: number;
  /** Main-thread send start through client validation, all on one clock. */
  clientTotalMs: number;
  /** Public detect() entry through owned bitmap cleanup/promise continuation. */
  detectCallMs: number;
  /** Residual includes both message directions, serialization and queue/scheduling.
   * It is not a measurement of physical transfer alone. No clocks are subtracted across realms. */
  transportAndSchedulingMs: number | null;
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null;
const duration = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;

/** Instrumentation is optional. Invalid/missing sidechannels never replace or reject a valid Detection. */
export function validatedWorkerTiming(value: unknown, expected: {
  requestId: number; timestampMs: number; delegate: FaceDelegate; width: number; height: number; detection: Detection;
}): {status: 'valid' | 'missing' | 'invalid'; timing: WorkerFaceTiming | null} {
  if (value === undefined) return {status: 'missing', timing: null};
  try {
    if (!record(value) || value.schema !== 'face-worker-timing-v1' || value.requestId !== expected.requestId
      || value.timestampMs !== expected.timestampMs || value.delegate !== expected.delegate
      || value.width !== expected.width || value.height !== expected.height
      || value.inferenceMs !== expected.detection.inferenceMs
      || ![value.requestChecksMs, value.inferenceMs, value.extractionMs, value.validationMs, value.elapsedMs].every(duration)) {
      return {status: 'invalid', timing: null};
    }
    const timing = value as unknown as WorkerFaceTiming;
    const sum = timing.requestChecksMs + timing.inferenceMs + timing.extractionMs + timing.validationMs;
    if (Math.abs(sum - timing.elapsedMs) > 1e-6) return {status: 'invalid', timing: null};
    return {status: 'valid', timing: {...timing}};
  } catch {
    // A malformed optional diagnostic object must never invalidate Detection.
    return {status: 'invalid', timing: null};
  }
}
