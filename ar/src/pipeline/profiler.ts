/** Per-frame timing telemetry and its summaries. Numeric only: no camera images, detections, masks or identity hashes
 *  ever enter the profiler, so a timing export can leave the page. */
export interface FrameSample {
  serial: number; sessionId: string; sequence: number; hair: boolean;
  capturedAtMs: number; publishedAtMs: number; videoPresentedFrames: number | null; videoMediaTime: number | null;
  videoPresentationTimeMs: number | null; cameraSettingFps: number | null; sourceWidth: number; sourceHeight: number;
  sourceDrawMs: number; sourceReadbackMs: number; sourceHashMs: number; detectorDrawMs: number; faceBitmapMs: number;
  faceRequestWallMs: number; faceInferenceMs: number | null; faceWorkerMs: number | null; faceExtractionMs: number | null;
  faceWorkerValidationMs: number | null; faceClientValidationMs: number | null; faceTransportSchedulingMs: number | null;
  detectionHashMs: number; schedulerWaitMs: number; prerequisitesWaitMs: number; hairAdmissionWaitMs: number;
  hairWaitMs: number; hairInferenceMs: number | null; hairExtractionMs: number | null;
  prepareMs: number; gpuWaitMs: number; poseMs: number; finishMs: number; maskUploadMs: number; continuityMs: number;
  submitMs: number; renderMs: number; totalMs: number;
  hasFace: boolean; hasMask: boolean; fallback: string | null;
  faceDelegate: string | null; hairDelegate: string | null; gpuRenderer: string | null;
  native: Record<string, number | boolean | string | null> | null;
}
export type FrameInput = Omit<FrameSample, 'serial'>;
export interface Distribution {median: number; p95: number; max: number;}
export interface ProfileSummary {
  frames: number; durationMs: number; processedFps: number | null; videoDeliveryFps: number | null;
  trackedFrames: number; maskedFrames: number; trackedHairFrames: number; hairCoverage: number | null;
  processing: Distribution | null; frameInterval: Distribution | null;
  stages: Record<string, Distribution | null>; source: {width: number; height: number} | null;
}
export const COVERAGE_POLICY = 'Full coverage requires tracking on 100% of measured frames and, with hair enabled, a mask on 100% of tracked frames. This describes workload coverage, not visual acceptance. All measured frames remain in timing summaries.';
export interface WorkCoverage {
  status: 'full' | 'partial' | 'no-tracking' | 'no-frames';
  trackedFraction: number | null;
  maskedTrackedFraction: number | null;
  untrackedFrames: number;
  trackedFramesWithoutMask: number | null;
}
export function workCoverage(summary: ProfileSummary, hair: boolean): WorkCoverage {
  const trackedFraction = summary.frames ? summary.trackedFrames / summary.frames : null;
  const maskedTrackedFraction = hair ? summary.hairCoverage : null;
  const full = trackedFraction === 1 && (!hair || maskedTrackedFraction === 1);
  return {status: !summary.frames ? 'no-frames' : !summary.trackedFrames ? 'no-tracking' : full ? 'full' : 'partial',
    trackedFraction, maskedTrackedFraction, untrackedFrames: summary.frames - summary.trackedFrames,
    trackedFramesWithoutMask: hair
      ? summary.trackedHairFrames - Math.round((summary.hairCoverage ?? 0) * summary.trackedHairFrames) : null};
}
export const distribution = (values: readonly number[]): Distribution | null => {
  const sorted = values.filter(Number.isFinite).slice().sort((a, b) => a - b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return {median: sorted.length % 2 ? sorted[middle]! : (sorted[middle - 1]! + sorted[middle]!) / 2,
    p95: sorted[Math.ceil(sorted.length * .95) - 1]!, max: sorted[sorted.length - 1]!};
};
export const STAGES = ['sourceDrawMs', 'detectorDrawMs', 'sourceReadbackMs', 'sourceHashMs', 'faceBitmapMs', 'faceRequestWallMs',
  'faceInferenceMs', 'faceWorkerMs', 'faceExtractionMs', 'faceWorkerValidationMs', 'faceClientValidationMs',
  'faceTransportSchedulingMs', 'prerequisitesWaitMs', 'detectionHashMs', 'schedulerWaitMs', 'hairAdmissionWaitMs',
  'hairWaitMs', 'hairInferenceMs', 'hairExtractionMs', 'prepareMs', 'gpuWaitMs', 'poseMs', 'finishMs', 'maskUploadMs',
  'continuityMs', 'submitMs', 'renderMs'] as const;

/** Parallel durations overlap; do not add the source hash, the face request and the hair inference to derive total time. */
export function summarize(samples: readonly FrameSample[]): ProfileSummary {
  const first = samples[0], last = samples.at(-1);
  const durationMs = first && last ? last.publishedAtMs - first.publishedAtMs : 0;
  const intervals = samples.slice(1).map((sample, index) => sample.publishedAtMs - samples[index]!.publishedAtMs);
  const cameraElapsed = first && last ? last.capturedAtMs - first.capturedAtMs : 0;
  const trackedHair = samples.filter(row => row.hasFace && row.hair);
  const videoFrames = first?.videoPresentedFrames !== null && first?.videoPresentedFrames !== undefined
    && last?.videoPresentedFrames !== null && last?.videoPresentedFrames !== undefined
    ? last.videoPresentedFrames - first.videoPresentedFrames : null;
  return {frames: samples.length, durationMs, processedFps: samples.length > 1 && durationMs > 0 ? (samples.length - 1) * 1000 / durationMs : null,
    videoDeliveryFps: videoFrames !== null && videoFrames >= 0 && cameraElapsed > 0 ? videoFrames * 1000 / cameraElapsed : null,
    trackedFrames: samples.filter(row => row.hasFace).length, maskedFrames: samples.filter(row => row.hasMask).length,
    trackedHairFrames: trackedHair.length, hairCoverage: trackedHair.length ? trackedHair.filter(row => row.hasMask).length / trackedHair.length : null,
    processing: distribution(samples.map(row => row.totalMs)),
    frameInterval: distribution(intervals), stages: Object.fromEntries(STAGES.map(key => [key, distribution(samples.flatMap(row => typeof row[key] === 'number' ? [row[key]] : []))])),
    source: first ? {width: first.sourceWidth, height: first.sourceHeight} : null};
}

/** Camera delivery rate over a window of rows, from the camera's own presented-frame counter: the ceiling no pipeline can exceed. */
export function cameraDeliveryFps(rows: readonly FrameSample[]): number | null {
  const counted = rows.filter(row => Number.isFinite(row.videoPresentedFrames));
  const first = counted[0], last = counted.at(-1);
  if (!first || !last || last === first || last.capturedAtMs <= first.capturedAtMs) return null;
  return ((last.videoPresentedFrames ?? 0) - (first.videoPresentedFrames ?? 0)) * 1000 / (last.capturedAtMs - first.capturedAtMs);
}

export const TIMINGS_SCHEMA = 'ar-timings-v1';
export class FrameProfiler {
  private serial = 0;
  private readonly rows: FrameSample[] = [];
  private readonly capacity: number;
  constructor(capacity = 4096) {
    if (!Number.isInteger(capacity) || capacity < 2) throw new Error('The profiling sample capacity must be at least two.');
    this.capacity = capacity;
  }
  add(input: FrameInput): FrameSample {
    if (!Number.isFinite(input.capturedAtMs) || !Number.isFinite(input.publishedAtMs) || input.publishedAtMs < input.capturedAtMs) throw new Error('Invalid completed profiling frame.');
    // Renderer diagnostics may contain exact-image identities. Those never enter timing telemetry.
    const native = input.native ? Object.fromEntries(Object.entries(input.native).filter(([key]) =>
      !/(^|\.)(sourceIdentity|\w*SHA256|\w*PngDataUrl|landmarks|categoryBase64)(\.|$)/i.test(key))) : null;
    const row = {...input, native, serial: ++this.serial};
    this.rows.push(row); if (this.rows.length > this.capacity) this.rows.shift();
    return {...row, native: row.native ? {...row.native} : null};
  }
  get hasSamples(): boolean {return this.rows.length > 0;}
  get lastSerial(): number {return this.serial;}
  samplesAfter(serial: number): FrameSample[] {
    return this.rows.filter(row => row.serial > serial).map(row => ({...row, native: row.native ? {...row.native} : null}));
  }
  /** The session's contiguous trailing rows within the window; never mixes rows of another session. */
  recent(sessionId: string, windowMs = 10_000): FrameSample[] {
    const end = this.rows.at(-1)?.publishedAtMs ?? 0;
    const result: FrameSample[] = [];
    for (let index = this.rows.length - 1; index >= 0; index--) {
      const row = this.rows[index]!;
      if (row.sessionId !== sessionId || end - row.publishedAtMs > windowMs) break;
      result.push(row);
    }
    return result.reverse();
  }
  snapshot(): Record<string, unknown> {
    return {schema: TIMINGS_SCHEMA, timeOriginMs: performance.timeOrigin,
      clock: 'performance.now in this page; publication means completed canvas submission, not physical display scanout',
      stagePolicy: 'Parallel durations overlap; do not add source hash, face request and hair inference to derive total time.',
      agePolicy: 'totalMs is capture-to-canvas-submission age and includes any pending-image wait. It excludes camera buffering and display scanout.',
      schedulerPolicy: 'schedulerWaitMs is the capture-to-inference wait under the two-image overlap.',
      coveragePolicy: COVERAGE_POLICY,
      privacy: 'Numeric timings and counters only; no camera images, detections, masks or identity hashes.',
      firstAvailableSerial: this.rows[0]?.serial ?? null, lastSerial: this.serial, capacity: this.capacity};
  }
  /** Explicit export only. */
  exportJSON(): string {return JSON.stringify({...this.snapshot(), samples: this.samplesAfter(0)}, null, 2);}
}
