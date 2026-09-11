import type {FrameInput} from './frame-profiler.ts';
import {distribution} from './frame-profiler.ts';
import {G_COMMIT} from './profiles.ts';

export const REVIEW_PIPELINES = ['g', 'publish', 'region', 'lens', 'ui'] as const;
export type ReviewPipeline = typeof REVIEW_PIPELINES[number];
type Scalar = number | boolean | string | null;
type Phase = 'switching' | 'warmup' | 'measured' | 'excluded';
export interface ContinuousWorkload {
  eyewearId: string; hairModelId: string; variant: 'hair' | 'accepted'; sourceWidth: number; sourceHeight: number;
}
export interface ContinuousRunOptions {
  sessionId: string; workload: ContinuousWorkload; metadata?: Record<string, unknown>;
  direction?: 'forward' | 'reverse'; warmupMs?: number; measureMs?: number; maxWarmupMs?: number;
  rowLimit?: number; id?: string;
}
export interface ContinuousRunStatus {
  state: 'idle' | 'switching' | 'warmup' | 'measuring' | 'complete' | 'partial'; running: boolean;
  windowIndex: number; token: number; pipeline: ReviewPipeline; windowCount: number;
  phaseElapsedMs: number; remainingMs: number; reason: string | null; rows: number;
}
export interface VideoObservation {atMs: number; presentedFrames: number; mediaTime: number | null;}
interface RunWindow {
  index: number; token: number; round: number; pipeline: ReviewPipeline;
  requestedAtMs: number | null; switchedAtMs: number | null; firstFrameAtMs: number | null;
  measureStartedAtMs: number | null; plannedEndAtMs: number | null; endedAtMs: number | null;
  transitionObservedAtMs: number | null; validWarmupFrames: number; thirdWarmupAtMs: number | null;
  completed: boolean;
}
interface ScalarFrame {fields: Record<string, Scalar>; native: Record<string, Scalar> | null; invalidFields: string[];}
export interface RecordedRunFrame extends ScalarFrame {
  serial: number; observedAtMs: number; windowIndex: number | null; phase: Phase; exclusion: string | null;
}
interface RunEvent {name: string; atMs: number; windowIndex: number | null; detail: string | null; durationMs: number | null;}
const FIELD_NAMES = [
  'sessionId', 'sequence', 'pipeline', 'variant', 'eyewearId', 'hairModelId', 'capturedAtMs', 'publishedAtMs',
  'videoPresentedFrames', 'videoMediaTime', 'videoPresentationTimeMs', 'cameraSettingFps', 'sourceWidth', 'sourceHeight',
  'sourceDrawMs', 'detectorDrawMs', 'sourceReadbackMs', 'sourceHashMs', 'faceBitmapMs', 'faceRequestWallMs',
  'faceInferenceMs', 'faceWorkerMs', 'faceExtractionMs', 'faceWorkerValidationMs', 'faceClientValidationMs',
  'faceTransportSchedulingMs', 'prerequisitesWaitMs', 'detectionHashMs', 'prepareMs', 'finishMs', 'renderMs',
  'totalMs', 'schedulerWaitMs', 'hairWaitMs', 'hairInferenceMs', 'hairExtractionMs', 'hasFace', 'hasMask',
  'maskMode', 'fallback', 'changedPixels', 'faceDelegate', 'hairDelegate', 'gpuRenderer', 'cleanCameraMs',
  'composeMs', 'continuityMs', 'finalChecksMs', 'publishMs',
] as const satisfies readonly (keyof FrameInput)[];
const forbiddenKey = /(^|\.)(sourceIdentity|\w*SHA256|\w*PngDataUrl|landmarks|categoryBase64|imageData|pixels|maskBytes|sourceImage|detection)(\.|$)/i;
const scalar = (value: unknown): value is Scalar => value === null || typeof value === 'boolean'
  || (typeof value === 'number' && Number.isFinite(value))
  || (typeof value === 'string' && value.length <= 2048 && !/^data:|;base64,/i.test(value));

/** Only bounded scalar telemetry is exported; arbitrary frame properties and binary arrays are never copied. */
export function sanitizeRunMetadata(input: unknown, depth = 0): Record<string, unknown> {
  if (!input || typeof input !== 'object' || Array.isArray(input) || ArrayBuffer.isView(input) || depth > 6) return {};
  const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const [key, value] of Object.entries(input)) {
    if (forbiddenKey.test(key) || key === '__proto__' || key === 'constructor' || key === 'prototype') continue;
    if (scalar(value)) output[key] = value;
    else if (value && typeof value === 'object' && !Array.isArray(value) && !ArrayBuffer.isView(value)) {
      const nested = sanitizeRunMetadata(value, depth + 1);
      if (Object.keys(nested).length) output[key] = nested;
    }
  }
  return output;
}
function normalizeFrame(input: FrameInput): ScalarFrame {
  const fields: Record<string, Scalar> = {}, invalidFields: string[] = [];
  for (const key of FIELD_NAMES) {
    const value = input[key];
    if (value === undefined) continue;
    if (scalar(value)) fields[key] = value;
    else {fields[key] = null; invalidFields.push(key);}
  }
  const native: Record<string, Scalar> | null = input.native ? {} : null;
  if (native && input.native) for (const [key, value] of Object.entries(input.native)) {
    if (forbiddenKey.test(key) || !scalar(value)) continue;
    native[key] = value;
  }
  return {fields, native, invalidFields};
}
const number = (row: RecordedRunFrame, key: string): number | null => typeof row.fields[key] === 'number' ? row.fields[key] as number : null;
const validTime = (value: number): boolean => Number.isFinite(value) && value >= 0;

/** A wall-clock experiment, independent of publication cadence. No renderer or scheduler changes occur here. */
export class ContinuousComparisonRun {
  private readonly options: Required<Pick<ContinuousRunOptions, 'warmupMs' | 'measureMs' | 'maxWarmupMs' | 'rowLimit' | 'direction'>>;
  private readonly sessionId: string;
  private readonly workload: ContinuousWorkload;
  private readonly metadata: Record<string, unknown>;
  private readonly id: string;
  private readonly windows: RunWindow[];
  private readonly rows: RecordedRunFrame[] = [];
  private readonly videos: VideoObservation[] = [];
  private readonly events: RunEvent[] = [];
  private readonly seenSequences = new Set<number>();
  private currentIndex = 0;
  private nowMs = 0;
  private startedAtMs: number | null = null;
  private endedAtMs: number | null = null;
  private reason: string | null = null;
  private complete = false;
  private rejectedRows = 0;
  private rejectedVideoObservations = 0;

  constructor(options: ContinuousRunOptions) {
    this.options = {warmupMs: options.warmupMs ?? 5000, measureMs: options.measureMs ?? 30000,
      maxWarmupMs: options.maxWarmupMs ?? 15000, rowLimit: options.rowLimit ?? 30000,
      direction: options.direction ?? 'forward'};
    const {warmupMs, measureMs, maxWarmupMs, rowLimit, direction} = this.options;
    if (!options.sessionId || !validTime(warmupMs) || !validTime(measureMs) || !validTime(maxWarmupMs)
      || measureMs === 0 || maxWarmupMs < warmupMs || !Number.isInteger(rowLimit) || rowLimit < 1
      || rowLimit > 100000 || !['forward', 'reverse'].includes(direction)) throw new Error('Invalid continuous comparison configuration.');
    if (!options.workload.eyewearId || !options.workload.hairModelId
      || !['hair', 'accepted'].includes(options.workload.variant)
      || !Number.isInteger(options.workload.sourceWidth) || options.workload.sourceWidth < 1
      || !Number.isInteger(options.workload.sourceHeight) || options.workload.sourceHeight < 1) throw new Error('Invalid fixed comparison workload.');
    this.id = options.id ?? crypto.randomUUID(); this.sessionId = options.sessionId;
    this.workload = {...options.workload}; this.metadata = sanitizeRunMetadata(options.metadata);
    const first = direction === 'forward' ? [...REVIEW_PIPELINES] : [...REVIEW_PIPELINES].reverse();
    this.windows = [...first, ...[...first].reverse()].map((pipeline, index) => ({index, token: index + 1,
      round: index < first.length ? 1 : 2, pipeline, requestedAtMs: null, switchedAtMs: null,
      firstFrameAtMs: null, measureStartedAtMs: null, plannedEndAtMs: null, endedAtMs: null,
      transitionObservedAtMs: null, validWarmupFrames: 0, thirdWarmupAtMs: null, completed: false}));
  }

  get status(): ContinuousRunStatus {
    const window = this.windows[Math.min(this.currentIndex, this.windows.length - 1)]!;
    const state = this.startedAtMs === null ? 'idle' : this.complete ? 'complete' : this.reason ? 'partial'
      : window.switchedAtMs === null ? 'switching' : window.measureStartedAtMs === null ? 'warmup' : 'measuring';
    const start = state === 'measuring' ? window.measureStartedAtMs : state === 'warmup' ? window.switchedAtMs : window.requestedAtMs;
    const limit = state === 'measuring' ? window.plannedEndAtMs : state === 'warmup'
      ? (window.switchedAtMs ?? this.nowMs) + this.options.warmupMs
      : (window.requestedAtMs ?? this.nowMs) + this.options.maxWarmupMs;
    return {state, running: state === 'switching' || state === 'warmup' || state === 'measuring',
      windowIndex: window.index, token: window.token, pipeline: window.pipeline, windowCount: this.windows.length,
      phaseElapsedMs: start === null ? 0 : Math.max(0, this.nowMs - start),
      remainingMs: state === 'complete' || state === 'partial' || state === 'idle' ? 0 : Math.max(0, (limit ?? this.nowMs) - this.nowMs),
      reason: this.reason, rows: this.rows.length};
  }

  begin(nowMs: number): ContinuousRunStatus {
    if (this.startedAtMs !== null) throw new Error('A continuous comparison cannot be restarted. Create a new run.');
    if (!validTime(nowMs)) throw new Error('Invalid comparison clock.');
    this.startedAtMs = this.nowMs = nowMs; this.windows[0]!.requestedAtMs = nowMs;
    this.event('run-started', nowMs); this.event('switch-requested', nowMs); return this.status;
  }

  /** The UI drains/stops the old pump, selects the requested pipeline, then acknowledges before starting a fresh pump. */
  switched(token: number, nowMs: number): ContinuousRunStatus {
    if (!this.advance(nowMs)) return this.status;
    const window = this.windows[this.currentIndex]!;
    if (token !== window.token || window.switchedAtMs !== null) return this.status;
    window.switchedAtMs = nowMs; this.event('switch-ready', nowMs); this.advance(nowMs); return this.status;
  }

  tick(nowMs: number): ContinuousRunStatus {this.advance(nowMs); return this.status;}

  observe(input: FrameInput, nowMs = input.publishedAtMs): ContinuousRunStatus {
    if (!this.status.running) return this.status;
    this.advance(nowMs);
    if (this.rows.length >= this.options.rowLimit) {
      this.rejectedRows++; return this.cancel('frame-row-limit', nowMs);
    }
    const raw = normalizeFrame(input);
    const valid = validTime(input.capturedAtMs) && validTime(input.publishedAtMs)
      && input.publishedAtMs >= input.capturedAtMs && input.publishedAtMs <= nowMs
      && Number.isSafeInteger(input.sequence) && input.sequence >= 0;
    const sameSession = input.sessionId === this.sessionId;
    const changedWorkload = input.variant !== this.workload.variant
      || input.eyewearId !== this.workload.eyewearId || input.hairModelId !== this.workload.hairModelId
      || input.sourceWidth !== this.workload.sourceWidth || input.sourceHeight !== this.workload.sourceHeight;
    let exclusion: string | null = !valid ? 'invalid-frame-clock-or-sequence' : !sameSession ? 'session-mismatch'
      : changedWorkload ? 'workload-changed' : this.seenSequences.has(input.sequence) ? 'duplicate-sequence' : null;
    if (valid && sameSession && !exclusion) this.seenSequences.add(input.sequence);
    const window = [...this.windows].reverse().find(candidate => candidate.requestedAtMs !== null && input.publishedAtMs >= candidate.requestedAtMs);
    let phase: Phase = 'excluded';
    if (!exclusion && window) {
      if (input.pipeline !== window.pipeline) exclusion = 'previous-or-unrequested-pipeline';
      else if (window.switchedAtMs === null) {phase = 'switching'; exclusion = 'switch-not-ready';}
      else if (input.capturedAtMs < window.switchedAtMs) exclusion = 'captured-before-switch';
      else {
        if (window.firstFrameAtMs === null) {
          window.firstFrameAtMs = input.publishedAtMs;
          this.event('first-frame', input.publishedAtMs, null, window.index);
        }
        if (window.measureStartedAtMs === null || input.publishedAtMs < window.measureStartedAtMs) {
          phase = 'warmup';
          if (input.hasFace && (this.workload.variant === 'accepted' || input.hasMask)) {
            window.validWarmupFrames++;
            if (window.validWarmupFrames === 3) window.thirdWarmupAtMs = input.publishedAtMs;
          }
        } else if (input.capturedAtMs < window.measureStartedAtMs) exclusion = 'captured-before-measurement';
        else if (input.publishedAtMs >= (window.endedAtMs ?? window.plannedEndAtMs ?? Infinity)) exclusion = 'published-after-window';
        else phase = 'measured';
      }
    } else if (!exclusion) exclusion = 'published-before-run';
    this.rows.push({...raw, serial: this.rows.length + 1, observedAtMs: nowMs,
      windowIndex: window?.index ?? null, phase, exclusion});
    if (!sameSession || changedWorkload) return this.cancel(exclusion ?? 'workload-changed', nowMs);
    this.advance(nowMs); return this.status;
  }

  /** Independent video callbacks measure delivered camera images even when AR produces no completions. */
  observeVideo(observation: VideoObservation): ContinuousRunStatus {
    if (!this.status.running) return this.status;
    if (!validTime(observation.atMs) || !Number.isSafeInteger(observation.presentedFrames) || observation.presentedFrames < 0
      || (observation.mediaTime !== null && !validTime(observation.mediaTime))) {
      this.rejectedVideoObservations++; this.event('invalid-video-observation', this.nowMs); return this.status;
    }
    const last = this.videos.at(-1);
    if (last && (observation.atMs <= last.atMs || observation.presentedFrames < last.presentedFrames)) {
      this.rejectedVideoObservations++; this.event('nonmonotonic-video-observation', this.nowMs); return this.status;
    }
    if (this.videos.length >= this.options.rowLimit) {
      this.rejectedVideoObservations++; return this.cancel('video-row-limit', Math.max(this.nowMs, observation.atMs));
    }
    this.videos.push({...observation}); return this.status;
  }

  recordEvent(name: string, atMs: number, durationMs: number | null = null): void {
    if (!this.status.running) return;
    if (!/^[a-z][a-z0-9-]{0,79}$/.test(name) || !validTime(atMs)
      || (durationMs !== null && !validTime(durationMs))) throw new Error('Invalid continuous run event.');
    if (this.events.length >= this.options.rowLimit) {this.cancel('event-row-limit', Math.max(this.nowMs, atMs)); return;}
    this.events.push({name, atMs, durationMs, detail: null, windowIndex: this.currentIndex});
  }

  cancel(reason: string, nowMs: number): ContinuousRunStatus {
    if (!this.status.running) return this.status;
    const at = validTime(nowMs) ? Math.max(nowMs, this.nowMs) : this.nowMs;
    this.nowMs = at; this.reason = String(reason).slice(0, 512) || 'cancelled'; this.endedAtMs = at;
    const window = this.windows[this.currentIndex]!;
    if (window.measureStartedAtMs !== null) window.endedAtMs = Math.min(at, window.plannedEndAtMs!);
    this.event('run-partial', at, this.reason); return this.status;
  }

  /** Export scans retained rows only on explicit save, never in the hot frame path. */
  export(): Record<string, unknown> {
    const windows = this.windows.map(window => ({...window,
      switchWaitMs: window.switchedAtMs === null || window.requestedAtMs === null ? null : window.switchedAtMs - window.requestedAtMs,
      switchToFirstFrameMs: window.firstFrameAtMs === null || window.requestedAtMs === null ? null : window.firstFrameAtMs - window.requestedAtMs,
      timerOvershootMs: window.transitionObservedAtMs === null || window.plannedEndAtMs === null ? null : Math.max(0, window.transitionObservedAtMs - window.plannedEndAtMs),
      summary: this.summarize(window),
    }));
    return {schema: 'ar-continuous-comparison-v1', id: this.id, baseCommit: G_COMMIT,
      candidateAccepted: false, sessionId: this.sessionId, startedAtMs: this.startedAtMs, endedAtMs: this.endedAtMs,
      observedAtMs: this.nowMs, completed: this.complete, partial: this.reason !== null, cancelledReason: this.reason,
      status: this.status, workload: {...this.workload}, metadata: structuredClone(this.metadata),
      protocol: {...this.options, minimumTrackedMaskedWarmupFrames: 3, order: this.windows.map(window => window.pipeline),
        measurementBoundary: 'Half-open [start,end): capture and publication must both belong to this measurement window; old pump, session and pre-boundary captures are excluded and retained.',
        clock: 'All atMs fields use performance.now on this page. Publication is completed canvas submission, not display scanout.',
        warmup: 'Warmup needs the minimum wall duration after switch-ready and three tracked frames with a same-image mask when hair is enabled. The maximum includes switch wait and stops on wall time even without frames.',
        fps: 'completedArFps uses the full actual measurement window, including zero-output time. Camera delivery uses independent video observations and reports its shorter observed span explicitly. Camera settings are a requested configuration, not a measurement.',
        gaps: 'Endpoint gaps include measurement start to first completion and last completion to window end. With no output the one gap is the entire window.',
        age: 'Frame age is source capture to completed canvas submission. It excludes sensor buffering, display scanout and unobserved motion-to-photon latency.',
        stages: 'Worker wall durations and GPU waits may overlap; adding them does not infer critical-path work or actual GPU parallelism.',
        ranking: 'No automatic winner. Compare both rounds, full-window throughput, frame-age tail, endpoint stalls, actual path counters and tracking/mask coverage. Thermal drift and user motion can differ by window; video recording adds overhead.'},
      retention: {frameRows: this.rows.length, videoRows: this.videos.length, limitPerKind: this.options.rowLimit,
        rejectedRows: this.rejectedRows, rejectedVideoObservations: this.rejectedVideoObservations,
        truncated: false, policy: 'No ring buffer: reaching either limit stops the run as partial and reports the rejected observation.'},
      privacy: 'Scalar timing and workload metadata only. No camera pixels, image hashes, detections, landmarks or masks. An explicitly requested separate video entry may contain the visible camera image.',
      windows, events: this.events.map(event => ({...event})),
      rows: this.rows.map(row => ({...row, fields: {...row.fields}, native: row.native ? {...row.native} : null, invalidFields: [...row.invalidFields]})),
      videoObservations: this.videos.map(observation => ({...observation})),
    };
  }

  private advance(nowMs: number): boolean {
    if (!this.status.running) return false;
    if (!validTime(nowMs) || nowMs < this.nowMs) {this.cancel('nonmonotonic-clock', this.nowMs); return false;}
    this.nowMs = nowMs;
    const window = this.windows[this.currentIndex]!;
    if (window.measureStartedAtMs === null) {
      const canStart = window.switchedAtMs !== null && window.thirdWarmupAtMs !== null
        && nowMs >= window.switchedAtMs + this.options.warmupMs;
      if (canStart) {
        const at = Math.max(window.switchedAtMs! + this.options.warmupMs, window.thirdWarmupAtMs!);
        if (at > window.requestedAtMs! + this.options.maxWarmupMs) {this.cancel('warmup-timeout', nowMs); return false;}
        window.measureStartedAtMs = at; window.plannedEndAtMs = at + this.options.measureMs;
        this.event('measurement-started', at);
      } else if (nowMs >= window.requestedAtMs! + this.options.maxWarmupMs) {
        this.cancel('warmup-timeout', nowMs); return false;
      }
    }
    if (window.plannedEndAtMs !== null && nowMs >= window.plannedEndAtMs) {
      window.endedAtMs = window.plannedEndAtMs; window.completed = true; window.transitionObservedAtMs = nowMs;
      this.event('measurement-ended', window.endedAtMs);
      if (this.currentIndex === this.windows.length - 1) {
        this.complete = true; this.endedAtMs = window.endedAtMs; this.event('run-complete', nowMs); return false;
      }
      this.currentIndex++; this.windows[this.currentIndex]!.requestedAtMs = nowMs; this.event('switch-requested', nowMs);
    }
    return this.status.running;
  }

  private event(name: string, atMs: number, detail: string | null = null, windowIndex = this.currentIndex): void {
    this.events.push({name, atMs, windowIndex, detail, durationMs: null});
  }

  private summarize(window: RunWindow): Record<string, unknown> {
    const rows = this.rows.filter(row => row.windowIndex === window.index && row.phase === 'measured');
    const start = window.measureStartedAtMs;
    const end = window.endedAtMs ?? (start === null ? null : Math.min(this.nowMs, window.plannedEndAtMs!));
    const durationMs = start === null || end === null ? 0 : Math.max(0, end - start);
    const times = rows.map(row => number(row, 'publishedAtMs')!).sort((a, b) => a - b);
    const points = start === null || end === null ? [] : [start, ...times, end];
    const gaps = points.slice(1).map((point, index) => point - points[index]!);
    const tracked = rows.filter(row => row.fields.hasFace === true);
    const masked = tracked.filter(row => row.fields.hasMask === true);
    const video = start === null || end === null ? [] : this.videos.filter(row => row.atMs >= start && row.atMs < end);
    const videoFirst = video[0], videoLast = video.at(-1);
    const videoSpanMs = videoFirst && videoLast ? videoLast.atMs - videoFirst.atMs : 0;
    const allCovered = rows.length > 0 && tracked.length === rows.length
      && (this.workload.variant === 'accepted' || masked.length === tracked.length);
    const stageKeys = FIELD_NAMES.filter(key => key.endsWith('Ms') && !['capturedAtMs', 'publishedAtMs', 'videoPresentationTimeMs'].includes(key));
    const nativeKeys = [...new Set(rows.flatMap(row => Object.keys(row.native ?? {})))];
    return {frames: rows.length, startAtMs: start, endAtMs: end, durationMs,
      completedArFps: durationMs > 0 ? rows.length * 1000 / durationMs : null,
      firstCompletionAtMs: times[0] ?? null, lastCompletionAtMs: times.at(-1) ?? null,
      frameAgeMs: distribution(rows.flatMap(row => {
        const captured = number(row, 'capturedAtMs'), published = number(row, 'publishedAtMs');
        return captured === null || published === null ? [] : [published - captured];
      })),
      completionGapMsIncludingEndpoints: distribution(gaps),
      initialNoCompletionMs: gaps[0] ?? null, trailingNoCompletionMs: gaps.at(-1) ?? null,
      gapCountsIncludingEndpoints: {over100Ms: gaps.filter(value => value > 100).length,
        over200Ms: gaps.filter(value => value > 200).length, over500Ms: gaps.filter(value => value > 500).length},
      coverage: {status: !rows.length ? 'no-frames' : !tracked.length ? 'no-tracking' : allCovered ? 'full' : 'partial',
        trackedFrames: tracked.length, maskedTrackedFrames: masked.length,
        trackedFraction: rows.length ? tracked.length / rows.length : null,
        maskedTrackedFraction: this.workload.variant === 'hair' && tracked.length ? masked.length / tracked.length : null},
      camera: {deliveryFps: videoSpanMs > 0 ? (videoLast!.presentedFrames - videoFirst!.presentedFrames) * 1000 / videoSpanMs : null,
        observationCount: video.length, observedSpanMs: videoSpanMs, spanFractionOfWindow: durationMs > 0 ? videoSpanMs / durationMs : null,
        source: video.length ? 'independent-video-frame-callback' : 'unavailable',
        settingFps: distribution(rows.flatMap(row => number(row, 'cameraSettingFps') === null ? [] : [number(row, 'cameraSettingFps')!]))},
      stages: Object.fromEntries(stageKeys.map(key => [key, distribution(rows.flatMap(row => number(row, key) === null ? [] : [number(row, key)!]))])),
      nativeNumericDistributions: Object.fromEntries(nativeKeys.map(key => [key, distribution(rows.flatMap(row => typeof row.native?.[key] === 'number' ? [row.native[key] as number] : []))]).filter(([, value]) => value !== null)),
      excludedFrames: this.rows.filter(row => row.windowIndex === window.index && row.exclusion !== null).length};
  }
}
