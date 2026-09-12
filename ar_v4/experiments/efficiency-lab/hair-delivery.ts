import {PIPELINES} from './profiles.ts';
import type {Pipeline} from './profiles.ts';
import type {HairRequestTiming} from './hair-cost/delivery.ts';

/** One full observation of an owned frame's hair request; no image identities or output bytes. */
export interface HairDeliveryTrace {
  sessionId: string; generation: number; sequence: number; pipeline: Pipeline;
  capturedAtMs: number; observedAtMs: number; publicationAtMs: number | null;
  usedAtPublication: boolean | null; disposedAtMs: number | null; timing: HairRequestTiming | null;
}
export interface HairDeliveryExport {
  schemaVersion: 1; sessionId: string; requests: HairDeliveryTrace[]; truncated: boolean; rejected: number;
}
const TIMESTAMPS = ['submittedAtMs', 'receivedAtMs', 'workerReleasedAtMs', 'validatedAtMs',
  'hashStartedAtMs', 'completedAtMs'] as const;
const DURATIONS = ['validationMs', 'hashMs', 'workerValidationMs', 'workerInferenceMs',
  'workerExtractionMs', 'workerElapsedMs'] as const;
const OUTCOMES = ['pending', 'completed', 'failed', 'cancelled', 'timed-out', 'rejected'] as const;
const REASONS = ['not-ready', 'admission-rejected', 'invalid-input', 'transfer-failed', 'invalid-response',
  'worker-error', 'unreadable-message', 'deadline', 'client-closed', 'session-aborted'] as const;
const requireValid = (valid: boolean): void => {if (!valid) throw new Error('Invalid scalar hair trace.');};
const object = (value: unknown): Record<string, unknown> => {
  requireValid(value !== null && typeof value === 'object' && !Array.isArray(value) && !ArrayBuffer.isView(value));
  return value as Record<string, unknown>;
};
const time = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const identity = (value: unknown): value is number => time(value) && Number.isSafeInteger(value);
const nullableNumber = (value: unknown): number | null => {requireValid(value === null || time(value)); return value as number | null;};

function copyTiming(value: unknown, trace: Omit<HairDeliveryTrace, 'timing'>): HairRequestTiming {
  const raw = object(value);
  requireValid(raw.requestId === null || identity(raw.requestId));
  requireValid(raw.sequence === null || (identity(raw.sequence) && raw.sequence === trace.sequence));
  requireValid(typeof raw.releaseWorkerEarly === 'boolean');
  requireValid(raw.categoryExtractionMode === null || raw.categoryExtractionMode === 'sdk' || raw.categoryExtractionMode === 'direct');
  requireValid(OUTCOMES.includes(raw.outcome as HairRequestTiming['outcome']));
  requireValid(raw.reason === null || REASONS.includes(raw.reason as NonNullable<HairRequestTiming['reason']>));
  requireValid(raw.pendingAtSubmission === null || identity(raw.pendingAtSubmission));
  const result: HairRequestTiming = {
    requestId: raw.requestId as number | null, sequence: raw.sequence as number | null,
    releaseWorkerEarly: raw.releaseWorkerEarly as boolean,
    categoryExtractionMode: raw.categoryExtractionMode as HairRequestTiming['categoryExtractionMode'],
    submittedAtMs: nullableNumber(raw.submittedAtMs), receivedAtMs: nullableNumber(raw.receivedAtMs),
    workerReleasedAtMs: nullableNumber(raw.workerReleasedAtMs), validatedAtMs: nullableNumber(raw.validatedAtMs),
    hashStartedAtMs: nullableNumber(raw.hashStartedAtMs), completedAtMs: nullableNumber(raw.completedAtMs),
    validationMs: nullableNumber(raw.validationMs), hashMs: nullableNumber(raw.hashMs),
    workerValidationMs: nullableNumber(raw.workerValidationMs), workerInferenceMs: nullableNumber(raw.workerInferenceMs),
    workerExtractionMs: nullableNumber(raw.workerExtractionMs), workerElapsedMs: nullableNumber(raw.workerElapsedMs),
    pendingAtSubmission: raw.pendingAtSubmission as number | null,
    outcome: raw.outcome as HairRequestTiming['outcome'], reason: raw.reason as HairRequestTiming['reason'],
  };
  for (const key of TIMESTAMPS) {
    const at = result[key];
    requireValid(at === null || (at >= trace.capturedAtMs && at <= trace.observedAtMs));
    if (at !== null && result.submittedAtMs !== null) requireValid(at >= result.submittedAtMs);
  }
  requireValid(result.sequence !== null || result.requestId === null);
  requireValid(result.requestId !== null || (result.submittedAtMs === null && result.outcome === 'rejected'));
  requireValid(result.outcome === 'pending' ? result.completedAtMs === null : result.completedAtMs !== null);
  requireValid(result.outcome !== 'completed' || result.reason === null);
  for (const [before, after] of [['submittedAtMs', 'receivedAtMs'], ['receivedAtMs', 'workerReleasedAtMs'],
    ['receivedAtMs', 'validatedAtMs'], ['validatedAtMs', 'hashStartedAtMs']] as const)
    requireValid(result[after] === null || (result[before] !== null && result[after] >= result[before]));
  if (result.completedAtMs !== null) for (const key of TIMESTAMPS)
    requireValid(result[key] === null || result[key] <= result.completedAtMs);
  return result;
}

function copyTrace(input: unknown, sessionId: string): HairDeliveryTrace {
  const raw = object(input);
  requireValid(raw.sessionId === sessionId && identity(raw.generation) && identity(raw.sequence));
  requireValid(PIPELINES.includes(raw.pipeline as Pipeline));
  requireValid(time(raw.capturedAtMs) && time(raw.observedAtMs) && raw.observedAtMs >= raw.capturedAtMs);
  const publicationAtMs = nullableNumber(raw.publicationAtMs), disposedAtMs = nullableNumber(raw.disposedAtMs);
  requireValid(raw.usedAtPublication === null || typeof raw.usedAtPublication === 'boolean');
  requireValid(publicationAtMs === null ? raw.usedAtPublication === null : typeof raw.usedAtPublication === 'boolean');
  const trace: Omit<HairDeliveryTrace, 'timing'> = {
    sessionId, generation: raw.generation as number, sequence: raw.sequence as number, pipeline: raw.pipeline as Pipeline,
    capturedAtMs: raw.capturedAtMs as number, observedAtMs: raw.observedAtMs as number,
    publicationAtMs, usedAtPublication: raw.usedAtPublication as boolean | null, disposedAtMs,
  };
  for (const at of [publicationAtMs, disposedAtMs])
    requireValid(at === null || (at >= trace.capturedAtMs && at <= trace.observedAtMs));
  requireValid(publicationAtMs === null || disposedAtMs === null || publicationAtMs <= disposedAtMs);
  return {...trace, timing: raw.timing === null ? null : copyTiming(raw.timing, trace)};
}

function frozenCopy(trace: HairDeliveryTrace): HairDeliveryTrace {
  const result = {...trace, timing: trace.timing ? Object.freeze({...trace.timing}) : null};
  return Object.freeze(result);
}

function merge(old: HairDeliveryTrace, next: HairDeliveryTrace): HairDeliveryTrace {
  requireValid(next.observedAtMs >= old.observedAtMs && next.pipeline === old.pipeline && next.capturedAtMs === old.capturedAtMs);
  for (const key of ['publicationAtMs', 'usedAtPublication', 'disposedAtMs'] as const)
    requireValid(old[key] === null || next[key] === null || old[key] === next[key]);
  const previous = old.timing!, incoming = next.timing!;
  requireValid(previous.releaseWorkerEarly === incoming.releaseWorkerEarly
    && previous.categoryExtractionMode === incoming.categoryExtractionMode && previous.sequence === incoming.sequence);
  requireValid(previous.submittedAtMs === null || incoming.submittedAtMs === null || previous.submittedAtMs === incoming.submittedAtMs);
  // A later publication snapshot may still carry pending timing. Preserve the
  // first terminal outcome while retaining the newly observed display/disposal.
  const timing = previous.outcome !== 'pending' ? previous : {...incoming};
  if (previous.outcome === 'pending') {
    for (const key of [...TIMESTAMPS, ...DURATIONS, 'pendingAtSubmission'] as const) {
      requireValid(previous[key] === null || incoming[key] === null || previous[key] === incoming[key]);
      if (previous[key] !== null) timing[key] = previous[key];
    }
  }
  const result = {...next, timing,
    publicationAtMs: old.publicationAtMs ?? next.publicationAtMs,
    usedAtPublication: old.usedAtPublication ?? next.usedAtPublication,
    disposedAtMs: old.disposedAtMs ?? next.disposedAtMs};
  requireValid(result.publicationAtMs === null || result.disposedAtMs === null || result.publicationAtMs <= result.disposedAtMs);
  return result;
}

/** Per-run bounded request ledger. Diagnostics never own, delay or schedule image work. */
export class HairDeliveryLog {
  private readonly sessionId: string;
  private readonly limit: number;
  private readonly requests = new Map<string, HairDeliveryTrace>();
  private truncated = false;
  private rejected = 0;

  constructor(sessionId: string, limit = 10000) {
    if (typeof sessionId !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$/.test(sessionId)
      || !Number.isInteger(limit) || limit < 1 || limit > 100000) throw new Error('Invalid hair delivery log configuration.');
    this.sessionId = sessionId; this.limit = limit;
  }

  record(trace: HairDeliveryTrace): void {
    try {
      const next = copyTrace(trace, this.sessionId);
      if (!next.timing) return;
      const key = `${next.generation}:${next.sequence}:${next.timing.requestId ?? 'unassigned'}`;
      const old = this.requests.get(key);
      if (!old && this.requests.size >= this.limit) {this.truncated = true; this.rejected++; return;}
      this.requests.set(key, frozenCopy(old ? merge(old, next) : next));
    } catch {this.rejected++;}
  }

  export(): HairDeliveryExport {
    const requests = [...this.requests.values()].map(frozenCopy);
    Object.freeze(requests);
    return Object.freeze({schemaVersion: 1, sessionId: this.sessionId, requests,
      truncated: this.truncated, rejected: this.rejected});
  }
}
