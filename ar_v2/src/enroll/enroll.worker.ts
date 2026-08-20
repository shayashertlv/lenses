/**
 * The scan, solved off the main thread.
 *
 * The bundle takes one to three seconds. On the main thread that is a hard
 * freeze: no paint, no scroll, no cancel button, and — measured on the app
 * itself — no way to even show the "working out your measurements" message,
 * because the browser never gets a chance to paint it before the solve starts.
 *
 * ## Why the worker rebuilds the model rather than receiving it
 *
 * The obvious design ships the mesh, the basis and the regions across. All
 * three are derived deterministically from one 46 KB `.obj`: the basis is
 * twenty constructed fields, the regions are Dijkstra over the same topology.
 * Rebuilding them here costs about 40 ms once, at worker start, in parallel with
 * the wearer still doing the scan — and it removes an entire class of bug, which
 * is the two sides disagreeing about geometry because one of them was built from
 * a stale copy.
 *
 * So the only thing that crosses is the frames, and they cross by **transfer**:
 * a 240-frame scan is ~4 MB of Float64, and structured-cloning that is a
 * copy the main thread pays for at exactly the moment it is trying to stay
 * responsive.
 *
 * ## What does not cross back
 *
 * A `FaceModel` goes back as its own JSON serialisation rather than as a
 * structured clone, because that serialisation is the format it is stored in
 * anyway — so the round trip through the worker exercises the same code path as
 * a returning wearer, every session. A format bug cannot hide until somebody
 * reloads the page.
 */

import { parseFaceObj, standardRegions, type FaceMesh, type Region } from '../core/mesh.js';
import { buildAnthropometricBasis } from '../core/shape/anthropometric.js';
import type { ShapeBasis } from '../core/shape/basis.js';
import { serializeFaceModel } from '../core/facemodel.js';
import { enroll } from './enroll.js';
import type { BundleFrame } from './bundle.js';

export interface EnrollWorkerInit {
  type: 'init';
  templateUrl: string;
}

export interface EnrollWorkerRun {
  type: 'enroll';
  id: number;
  frames: {
    landmarks: ArrayBuffer;
    sigmaPx: ArrayBuffer;
    visibility: ArrayBuffer;
    beat: string;
  }[];
  imageWidth: number;
  imageHeight: number;
  irisMm?: number;
}

export type EnrollWorkerMessage = EnrollWorkerInit | EnrollWorkerRun;

export interface EnrollWorkerReady { type: 'ready'; vertexCount: number; basisDim: number }
export interface EnrollWorkerTrace { type: 'trace'; id: number; message: string }
export interface EnrollWorkerDone {
  type: 'done';
  id: number;
  model: string;
  coverage: unknown;
  bundle: unknown;
}
export interface EnrollWorkerError { type: 'error'; id: number; message: string }

export type EnrollWorkerReply =
  EnrollWorkerReady | EnrollWorkerTrace | EnrollWorkerDone | EnrollWorkerError;

let mesh: FaceMesh | null = null;
let basis: ShapeBasis | null = null;
let regions: Record<string, Region> | null = null;

const post = (message: EnrollWorkerReply) => (self as unknown as Worker).postMessage(message);

self.onmessage = async (event: MessageEvent<EnrollWorkerMessage>) => {
  const message = event.data;

  if (message.type === 'init') {
    try {
      const text = await fetch(message.templateUrl).then((r) => {
        if (!r.ok) throw new Error(`face template: HTTP ${r.status}`);
        return r.text();
      });
      mesh = parseFaceObj(text);
      basis = buildAnthropometricBasis(mesh);
      regions = standardRegions(mesh);
      post({ type: 'ready', vertexCount: mesh.vertexCount, basisDim: basis.dim });
    } catch (error) {
      post({ type: 'error', id: -1, message: String((error as Error)?.message ?? error) });
    }
    return;
  }

  if (message.type === 'enroll') {
    if (!mesh || !basis) {
      post({ type: 'error', id: message.id, message: 'worker was not initialised' });
      return;
    }
    try {
      const frames: Omit<BundleFrame, 'pose'>[] = message.frames.map((f) => ({
        landmarks: new Float64Array(f.landmarks),
        sigmaPx: new Float64Array(f.sigmaPx),
        visibility: new Float64Array(f.visibility),
        silhouette: null,
        beat: f.beat,
      }));

      const result = enroll({
        mesh, basis, frames,
        imageWidth: message.imageWidth,
        imageHeight: message.imageHeight,
        irisMm: message.irisMm,
        trace: (m) => post({ type: 'trace', id: message.id, message: m }),
      });

      post({
        type: 'done',
        id: message.id,
        model: serializeFaceModel(result.model),
        coverage: result.coverage,
        bundle: result.bundle,
      });
    } catch (error) {
      post({
        type: 'error',
        id: message.id,
        message: String((error as Error)?.stack ?? (error as Error)?.message ?? error),
      });
    }
  }
};
