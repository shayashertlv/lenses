/**
 * The scan, solved off the main thread.
 *
 * The bundle takes one to three seconds. On the main thread that is a hard
 * freeze: no paint, no scroll, no cancel button, and — measured on the app
 * itself — no way to even show the "working out your measurements" message,
 * because the browser never gets a chance to paint it before the solve starts.
 *
 * ## Why this file sits in `app/` rather than in `enroll/`
 *
 * It used to sit in `enroll/`, next to the solver it calls, which reads well and
 * is wrong: `enroll/` is one of the six directories `scripts/check-isolation.mjs`
 * holds to running in Node with no browser at all, and this file assigns
 * `self.onmessage` at module scope. Importing it under Node throws
 * `ReferenceError: self is not defined` before a single line of it runs — while
 * the isolation check passed, because it looks for `document.`, `window.`,
 * `navigator.` and `new Worker`, none of which appear here.
 *
 * The file holds no arithmetic. It fetches a template, rebuilds geometry, calls
 * `enroll()` and serialises the answer; every number in it comes from `core/` or
 * `enroll/`. Its only consumers — `enroll-client.ts` and `main.ts` — are already
 * here. So the boundary was drawn in the wrong place rather than violated, and
 * moving the file is the fix rather than exempting it.
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
 * So the only thing that crosses is the frames, and they cross by **structured
 * clone**. They used to cross by transfer, on the argument that a ~4 MB copy is
 * one the main thread should not pay for while trying to stay responsive — but
 * that measures the wrong moment. This runs after the scan has ended, with the
 * wearer looking at a "working out your measurements" message the app has just
 * yielded to paint, and nothing else asking for the main thread. The copy is a
 * few milliseconds against a solve of one to three seconds.
 *
 * Transfer also detached the buffers on the caller's side, which quietly broke
 * both of `enroll-client`'s failure paths: they re-solved inline from arrays of
 * length 0 and produced the average face. See the note there.
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
import { enroll } from '../enroll/enroll.js';
import type { BundleFrame } from '../enroll/bundle.js';

export interface EnrollWorkerInit {
  type: 'init';
  templateUrl: string;
}

export interface EnrollWorkerRun {
  type: 'enroll';
  id: number;
  frames: {
    landmarks: Float64Array;
    sigmaPx: Float64Array;
    visibility: Float64Array;
    beat: string;
  }[];
  imageWidth: number;
  imageHeight: number;
  irisMm?: number;
  knownPdMm?: number | null;
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
        knownPdMm: message.knownPdMm,
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
