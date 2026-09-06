export interface Landmark {
  x: number;
  y: number;
  z: number;
}

export interface Detection {
  /** Original, unmirrored image coordinates. Empty means no visible face. */
  landmarks: Landmark[];
  /** MediaPipe's packed 4 x 4 canonical-face transformation, unchanged. */
  matrix: number[] | null;
  inferenceMs: number;
}

export type DetectorRequest =
  | { type: 'initialize'; id: number; modelUrl: string; wasmRoot: string; delegate: 'GPU' | 'CPU' }
  | { type: 'detect'; id: number; image: ImageBitmap; timestampMs: number };

export type DetectorResponse =
  | { type: 'ready'; id: number }
  | { type: 'result'; id: number; detection: Detection }
  | { type: 'error'; id: number; message: string };

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Validate the worker boundary without clamping legitimate off-image points. */
export function validateDetection(value: unknown): Detection {
  if (!record(value) || !Array.isArray(value.landmarks)
    || (value.landmarks.length !== 0 && value.landmarks.length !== 478)) {
    throw new Error('The face detector returned an incomplete landmark set.');
  }
  const landmarks = Array.from(value.landmarks, (point: unknown) => {
    if (!record(point) || !finite(point.x) || !finite(point.y) || !finite(point.z)) {
      throw new Error('The face detector returned invalid landmark coordinates.');
    }
    return { x: point.x, y: point.y, z: point.z };
  });
  let matrix: number[] | null = null;
  if (value.matrix !== null) {
    if (!Array.isArray(value.matrix) || value.matrix.length !== 16 || !Array.from(value.matrix).every(finite)) {
      throw new Error('The face detector returned an invalid face transformation.');
    }
    matrix = value.matrix.slice() as number[];
  }
  if (!finite(value.inferenceMs) || value.inferenceMs < 0) {
    throw new Error('The face detector returned an invalid inference duration.');
  }
  if (landmarks.length === 0 && matrix !== null) {
    throw new Error('The face detector returned a transformation without a face.');
  }
  return { landmarks, matrix, inferenceMs: value.inferenceMs };
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
