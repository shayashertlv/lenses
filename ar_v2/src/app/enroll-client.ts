/**
 * Driving the enrollment worker, with a main-thread fallback that is a real
 * fallback rather than a stub.
 *
 * Two failure modes are handled, and the second is the one that bites:
 *
 *  - **No worker at all.** Older embedded webviews, or a `file://` page. Falls
 *    back to solving inline. The wearer gets a freeze instead of a spinner,
 *    which is worse but is not broken.
 *  - **A worker that starts and then never answers.** Far nastier, because
 *    nothing errors: the promise simply never settles and the app sits on
 *    "working out your measurements" forever. This tree has already produced
 *    exactly that shape of bug once, from awaiting a `requestAnimationFrame`
 *    that never fired, so every await here has a timeout and the timeout falls
 *    back rather than failing.
 */

import { deserializeFaceModel, type FaceModel } from '../core/facemodel.js';
import { enroll } from '../enroll/enroll.js';
import type { BundleFrame } from '../enroll/bundle.js';
import type { EnrollWorkerReply } from '../enroll/enroll.worker.js';
import type { FaceMesh, Region } from '../core/mesh.js';
import type { ShapeBasis } from '../core/shape/basis.js';

export interface EnrollRequest {
  frames: Omit<BundleFrame, 'pose'>[];
  imageWidth: number;
  imageHeight: number;
  irisMm?: number;
}

export interface EnrollOutcome {
  model: FaceModel;
  coverage: unknown;
  bundle: unknown;
  /** Which path produced it, for the readouts. */
  ranOn: 'worker' | 'main';
}

export interface EnrollClient {
  readonly available: boolean;
  readonly ranOn: 'worker' | 'main';
  run(request: EnrollRequest): Promise<EnrollOutcome>;
  close(): void;
}

/** How long to wait for the worker to load its template before giving up. */
const READY_TIMEOUT_MS = 20000;
/**
 * How long to wait for a solve.
 *
 * Generous — the measured solve is 1.5 to 3 seconds and a slow phone could be
 * several times that — but finite, because an infinite wait is indistinguishable
 * from a crash and produces a worse experience than either.
 */
const SOLVE_TIMEOUT_MS = 60000;

export async function createEnrollClient(
  workerUrl: string,
  templateUrl: string,
  fallback: { mesh: FaceMesh; basis: ShapeBasis; regions: Record<string, Region> },
  onTrace?: (message: string) => void,
): Promise<EnrollClient> {
  let worker: Worker | null = null;

  if (typeof Worker !== 'undefined') {
    worker = await new Promise<Worker | null>((resolve) => {
      let candidate: Worker;
      try {
        candidate = new Worker(workerUrl, { type: 'module' });
      } catch (error) {
        console.warn('enrollment worker could not start; solving inline', error);
        resolve(null);
        return;
      }
      const timer = setTimeout(() => {
        console.warn('enrollment worker did not become ready; solving inline');
        candidate.terminate();
        resolve(null);
      }, READY_TIMEOUT_MS);

      candidate.onmessage = (event: MessageEvent<EnrollWorkerReply>) => {
        if (event.data.type === 'ready') {
          clearTimeout(timer);
          onTrace?.(
            `enrollment worker ready (${event.data.vertexCount} vertices, ` +
            `${event.data.basisDim} shape modes)`,
          );
          resolve(candidate);
        } else if (event.data.type === 'error') {
          clearTimeout(timer);
          console.warn('enrollment worker failed to initialise:', event.data.message);
          candidate.terminate();
          resolve(null);
        }
      };
      candidate.onerror = (event) => {
        clearTimeout(timer);
        console.warn('enrollment worker error; solving inline', event.message);
        candidate.terminate();
        resolve(null);
      };
      candidate.postMessage({ type: 'init', templateUrl });
    });
  }

  const runInline = (request: EnrollRequest): EnrollOutcome => {
    const result = enroll({
      mesh: fallback.mesh,
      basis: fallback.basis,
      frames: request.frames,
      imageWidth: request.imageWidth,
      imageHeight: request.imageHeight,
      irisMm: request.irisMm,
      trace: onTrace,
    });
    return {
      model: result.model, coverage: result.coverage, bundle: result.bundle, ranOn: 'main',
    };
  };

  let nextId = 1;

  return {
    available: worker !== null,
    ranOn: worker ? 'worker' : 'main',

    run(request) {
      if (!worker) return Promise.resolve(runInline(request));
      const id = nextId++;
      const active = worker;

      return new Promise<EnrollOutcome>((resolve) => {
        const settle = (outcome: EnrollOutcome) => {
          clearTimeout(timer);
          active.removeEventListener('message', listener as EventListener);
          resolve(outcome);
        };

        const timer = setTimeout(() => {
          console.warn('enrollment worker timed out; solving inline');
          active.removeEventListener('message', listener as EventListener);
          resolve(runInline(request));
        }, SOLVE_TIMEOUT_MS);

        const listener = (event: MessageEvent<EnrollWorkerReply>) => {
          const reply = event.data;
          if (!('id' in reply) || reply.id !== id) return;
          if (reply.type === 'trace') { onTrace?.(reply.message); return; }
          if (reply.type === 'done') {
            settle({
              model: deserializeFaceModel(reply.model),
              coverage: reply.coverage,
              bundle: reply.bundle,
              ranOn: 'worker',
            });
            return;
          }
          if (reply.type === 'error') {
            console.warn('enrollment worker failed; solving inline:', reply.message);
            clearTimeout(timer);
            active.removeEventListener('message', listener as EventListener);
            resolve(runInline(request));
          }
        };

        active.addEventListener('message', listener as EventListener);

        // Transfer rather than clone. A 240-frame scan is about 4 MB of
        // Float64, and cloning it is a copy the main thread pays for at exactly
        // the moment it is trying to stay responsive.
        const payload = request.frames.map((f) => ({
          landmarks: f.landmarks.buffer,
          sigmaPx: f.sigmaPx.buffer,
          visibility: f.visibility.buffer,
          beat: f.beat,
        }));
        const transfer: ArrayBuffer[] = [];
        for (const f of payload) transfer.push(f.landmarks as ArrayBuffer, f.sigmaPx as ArrayBuffer, f.visibility as ArrayBuffer);

        active.postMessage({
          type: 'enroll', id, frames: payload,
          imageWidth: request.imageWidth,
          imageHeight: request.imageHeight,
          irisMm: request.irisMm,
        }, transfer);
      });
    },

    close() {
      worker?.terminate();
      worker = null;
    },
  };
}
