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
import type { EnrollWorkerReply } from './enroll.worker.js';
import type { FaceMesh, Region } from '../core/mesh.js';
import type { ShapeBasis } from '../core/shape/basis.js';

export interface EnrollRequest {
  frames: Omit<BundleFrame, 'pose'>[];
  imageWidth: number;
  imageHeight: number;
  irisMm?: number;
  knownPdMm?: number | null;
}

export interface EnrollOutcome {
  model: FaceModel;
  coverage: unknown;
  bundle: unknown;
  /** Which path produced it, for the readouts. */
  ranOn: 'worker' | 'main';
}

export interface EnrollClient {
  /**
   * Whether there is a live worker RIGHT NOW.
   *
   * A getter over the current worker rather than a snapshot taken at
   * construction, and the difference was worth a bug: once anything terminated
   * the worker, a snapshot kept answering `true`, the diagnostics kept reporting
   * `enrollmentWorker: true`, and every later scan posted into a dead worker and
   * sat there for the full solve timeout before falling back.
   */
  readonly available: boolean;
  /** Where the NEXT solve would run, if one started now. */
  readonly ranOn: 'worker' | 'main';
  /**
   * Where the most recent solve ACTUALLY ran. Null before the first one.
   *
   * The honest one for a diagnostics dump. `available` and `ranOn` are both
   * predictions, and an inline fallback — a worker that died mid-solve, a solve
   * that timed out — is exactly the event a prediction misses.
   */
  readonly lastRanOn: 'worker' | 'main' | null;
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
      /**
       * Every exit from the handshake, and **every exit detaches both
       * handlers.**
       *
       * They used to survive it. `onmessage` stayed bound for the life of the
       * worker, so a solve-time `{type:'error', id}` reply — the ordinary shape
       * of a solve that threw, minutes or hours later — was delivered to this
       * handler as well as to `run`'s listener. It logged "enrollment worker
       * failed to initialise" about a worker that had initialised perfectly, and
       * then called `terminate()` on the live one.
       *
       * That solve still recovered inline, so nothing looked broken. Every
       * subsequent scan posted into a terminated worker and waited out the whole
       * 60 s solve timeout first. Reachable from the "Start again" button, since
       * the client is built once at boot and outlives any number of scans.
       */
      const settle = (result: Worker | null) => {
        clearTimeout(timer);
        candidate.onmessage = null;
        candidate.onerror = null;
        resolve(result);
      };

      const timer = setTimeout(() => {
        console.warn('enrollment worker did not become ready; solving inline');
        candidate.terminate();
        settle(null);
      }, READY_TIMEOUT_MS);

      candidate.onmessage = (event: MessageEvent<EnrollWorkerReply>) => {
        if (event.data.type === 'ready') {
          onTrace?.(
            `enrollment worker ready (${event.data.vertexCount} vertices, ` +
            `${event.data.basisDim} shape modes)`,
          );
          settle(candidate);
        } else if (event.data.type === 'error') {
          console.warn('enrollment worker failed to initialise:', event.data.message);
          candidate.terminate();
          settle(null);
        }
      };
      candidate.onerror = (event) => {
        console.warn('enrollment worker error; solving inline', event.message);
        candidate.terminate();
        settle(null);
      };
      candidate.postMessage({ type: 'init', templateUrl });
    });
  }

  const runInline = (request: EnrollRequest): EnrollOutcome => {
    // Refuse to solve nothing.
    //
    // This function is the fallback for a worker that timed out or died, and it
    // used to be handed frames whose ArrayBuffers had already been TRANSFERRED
    // to that worker — every Float64Array detached to length 0. It did not
    // throw. `enroll` dutifully returned a degraded model built from no
    // observations, the app attached a scan record to it and wrote it to
    // localStorage, and the wearer's saved measurements were the average face.
    //
    // The transfer is gone (see `run`), so this should never fire. It stays
    // because the failure it guards is silent, and `main.ts` already has an
    // error path that says "the scan did not work" honestly.
    const empty = request.frames.filter((f) => f.landmarks.length === 0).length;
    if (empty > 0) {
      throw new Error(
        `enrollment fallback was handed ${empty} of ${request.frames.length} empty frames — ` +
        'their buffers were detached before it ran',
      );
    }
    const result = enroll({
      mesh: fallback.mesh,
      basis: fallback.basis,
      frames: request.frames,
      imageWidth: request.imageWidth,
      imageHeight: request.imageHeight,
      irisMm: request.irisMm,
      knownPdMm: request.knownPdMm,
      trace: onTrace,
    });
    return {
      model: result.model, coverage: result.coverage, bundle: result.bundle, ranOn: 'main',
    };
  };

  let nextId = 1;
  let lastRanOn: 'worker' | 'main' | null = null;

  /**
   * Terminates the worker and — the half that was missing — forgets it.
   *
   * `available` is a getter over this variable, so a worker that has been killed
   * stops being advertised at the moment it is killed rather than never.
   */
  const dropWorker = (): void => {
    worker?.terminate();
    worker = null;
  };

  return {
    get available() { return worker !== null; },
    get ranOn(): 'worker' | 'main' { return worker ? 'worker' : 'main'; },
    get lastRanOn() { return lastRanOn; },

    run(request) {
      if (!worker) {
        lastRanOn = 'main';
        return Promise.resolve(runInline(request));
      }
      const id = nextId++;
      const active = worker;

      return new Promise<EnrollOutcome>((resolve, reject) => {
        const settle = (outcome: EnrollOutcome) => {
          clearTimeout(timer);
          active.removeEventListener('message', listener as EventListener);
          lastRanOn = outcome.ranOn;
          resolve(outcome);
        };

        /**
         * Give up on the worker for this solve and do it here instead.
         *
         * **The `try` is the load-bearing part, not tidiness.** Both fallback
         * sites used to be a bare `resolve(runInline(request))` — after the
         * timer had been cleared and the listener removed, in an executor that
         * captured only `resolve`. So `runInline` throwing settled *nothing*:
         * the promise stayed pending, `app.phase` stayed on 'solving', the
         * status stayed on "working out your measurements…" and `main.ts`'s
         * honest catch never ran. Verified: still pending 500 ms later.
         *
         * And it is not a remote possibility. `enroll` is deterministic, and the
         * frames the worker was handed are a content-identical *clone* of these
         * — so a worker that threw is a worker whose throw the inline fallback
         * is about to reproduce, with near-certainty. The fallback was
         * structurally most likely to be needed exactly when it could not work.
         */
        const fallBack = () => {
          active.removeEventListener('message', listener as EventListener);
          lastRanOn = 'main';
          try {
            resolve(runInline(request));
          } catch (error) {
            reject(error);
          } finally {
            // Cleared AFTER `runInline` returns, not before. The `catch` above
            // is what settles a throw now, but the ordering keeps the timeout
            // armed as a second net for the whole of the inline solve rather
            // than disarming it on the way in.
            clearTimeout(timer);
          }
        };

        const timer = setTimeout(() => {
          console.warn('enrollment worker timed out; solving inline');
          // A worker that has not answered in a minute is not going to. Kill it,
          // so `available` goes false and the NEXT scan goes straight inline
          // instead of paying the same minute again before finding out.
          dropWorker();
          fallBack();
        }, SOLVE_TIMEOUT_MS);

        const listener = (event: MessageEvent<EnrollWorkerReply>) => {
          const reply = event.data;
          if (!('id' in reply) || reply.id !== id) return;
          if (reply.type === 'trace') { onTrace?.(reply.message); return; }
          if (reply.type === 'done') {
            let model: FaceModel;
            try {
              model = deserializeFaceModel(reply.model);
            } catch (error) {
              // The third way this promise could never settle, and the same
              // shape as the two above. `deserializeFaceModel` used to be called
              // inline in `settle`'s argument, so a payload this build cannot
              // read threw out of an event listener: uncaught, nothing resolved,
              // nothing rejected, and the app parked on "working out your
              // measurements…" permanently. This side still holds the frames —
              // they were cloned, not transferred — so the honest recovery is
              // the same one the other two use.
              console.warn(
                'enrollment worker returned a model this build cannot read; solving inline:',
                error,
              );
              fallBack();
              return;
            }
            settle({
              model, coverage: reply.coverage, bundle: reply.bundle, ranOn: 'worker',
            });
            return;
          }
          if (reply.type === 'error') {
            // The worker is healthy — it answered. It is this *solve* that
            // failed, so the worker is kept for the next scan.
            console.warn('enrollment worker failed; solving inline:', reply.message);
            fallBack();
          }
        };

        active.addEventListener('message', listener as EventListener);

        // **Clone, not transfer**, and the reasoning that argued for transfer
        // was measuring the wrong moment.
        //
        // Transferring detaches the buffers on this side. Both of this file's
        // failure paths — the solve timeout above and a worker-side `error`
        // below — then called `runInline(request)` on arrays that were already
        // length 0, and got back a model of the average face which the app
        // stored as the wearer's own measurements. A fallback that cannot run is
        // worse than no fallback, because it looks like it ran.
        //
        // The saved copy was justified as avoiding "a copy the main thread pays
        // for at exactly the moment it is trying to stay responsive". But this
        // runs *after* the scan ends, with the wearer looking at "working out
        // your measurements" and nothing to be responsive for — `runEnrollment`
        // deliberately yields to paint that message first. A ~3.6 MB structured
        // clone there is single-digit milliseconds against a solve that takes
        // one to three seconds.
        active.postMessage({
          type: 'enroll', id,
          frames: request.frames.map((f) => ({
            landmarks: f.landmarks,
            sigmaPx: f.sigmaPx,
            visibility: f.visibility,
            beat: f.beat,
          })),
          imageWidth: request.imageWidth,
          imageHeight: request.imageHeight,
          irisMm: request.irisMm,
          knownPdMm: request.knownPdMm,
        });
      });
    },

    close() { dropWorker(); },
  };
}
