/** Main-page performance.now timestamps; worker fields are durations only.
 * No image, mask bytes, source hashes, session nonces or error text enter this trace. */
export interface HairRequestTiming {
  requestId: number | null;
  sequence: number | null;
  releaseWorkerEarly: boolean;
  categoryExtractionMode: 'sdk' | 'direct' | 'rgba8' | null;
  /** Additive all-request extraction fields, including results missed by publication. */
  categoryPath?: string;
  categoryRetrievalMs?: number;
  categoryConversionMs?: number;
  categoryCopyMs?: number;
  categoryTotalMs?: number;
  /** V attempt including a later SDK fallback; null when V was not requested. */
  categoryAttemptMs?: number | null;
  categoryReadbackBytes?: number | null;
  categoryFallbackReason?: string | null;
  submittedAtMs: number | null;
  receivedAtMs: number | null;
  workerReleasedAtMs: number | null;
  validatedAtMs: number | null;
  hashStartedAtMs: number | null;
  completedAtMs: number | null;
  validationMs: number | null;
  hashMs: number | null;
  workerValidationMs: number | null;
  workerInferenceMs: number | null;
  workerExtractionMs: number | null;
  workerElapsedMs: number | null;
  pendingAtSubmission: number | null;
  outcome: 'pending' | 'completed' | 'failed' | 'cancelled' | 'timed-out' | 'rejected';
  reason: 'not-ready' | 'admission-rejected' | 'invalid-input' | 'transfer-failed' | 'invalid-response'
    | 'worker-error' | 'unreadable-message' | 'deadline' | 'client-closed' | 'session-aborted' | null;
}
export type HairTimingObserver = (timing: Readonly<HairRequestTiming>) => void;
/** Worker-local durations, never absolute timestamps compared with the page clock. */
export interface HairWorkerTiming {inputValidationMs: number; totalMs: number;}
