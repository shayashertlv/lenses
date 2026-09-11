export const PIPELINES = ['current', 'test', 'test2', 'test3'] as const;
export type Pipeline = typeof PIPELINES[number];
export const PIPELINE_LABELS: Record<Pipeline, string> = {current: 'Long-hair checkpoint', test: 'Test 1', test2: 'Current · Test 2', test3: 'Test 3'};

/** Numeric telemetry only: no camera images, detections, masks or identity hashes. */
export interface FrameSample {
  serial: number; sessionId: string; sequence: number; pipeline: Pipeline; variant: 'hair' | 'accepted';
  capturedAtMs: number; publishedAtMs: number; videoPresentedFrames: number | null; videoMediaTime: number | null;
  videoPresentationTimeMs: number | null; cameraSettingFps: number | null; sourceWidth: number; sourceHeight: number;
  sourceDrawMs: number; detectorDrawMs: number; sourceReadbackMs: number; sourceHashMs: number;
  faceBitmapMs: number; faceRequestWallMs: number; faceInferenceMs: number | null; faceWorkerMs: number | null;
  faceExtractionMs: number | null; faceWorkerValidationMs: number | null; faceClientValidationMs: number | null;
  faceTransportSchedulingMs: number | null; prerequisitesWaitMs: number; detectionHashMs: number;
  prepareMs: number; finishMs: number; renderMs: number; totalMs: number; schedulerWaitMs: number;
  hairWaitMs: number; hairInferenceMs: number | null; hairExtractionMs: number | null;
  hasFace: boolean; hasMask: boolean; maskMode: string | null; fallback: string | null; changedPixels: number;
  faceDelegate: string | null; hairDelegate: string | null; gpuRenderer: string | null;
  cleanCameraMs: number; composeMs: number; continuityMs: number; finalChecksMs: number; publishMs: number;
  native: Record<string, number | boolean | string | null> | null;
}
export type FrameInput = Omit<FrameSample, 'serial'>;
export interface Distribution {median: number; p95: number; max: number;}
export interface ProfileSummary {
  frames: number; durationMs: number; processedFps: number | null; videoDeliveryFps: number | null;
  trackedFrames: number; maskedFrames: number; trackedHairFrames: number; hairCoverage: number | null;
  changedHairFrames: number; processing: Distribution | null; frameInterval: Distribution | null;
  gpuHairFraction: number | null;
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
export interface CompletedSegment {
  pipeline: Pipeline; variant: 'hair' | 'accepted'; endedAtMs: number;
  summary: ProfileSummary; coverage: WorkCoverage;
}
export function workCoverage(summary: ProfileSummary, variant: 'hair' | 'accepted'): WorkCoverage {
  const trackedFraction = summary.frames ? summary.trackedFrames / summary.frames : null;
  const maskedTrackedFraction = variant === 'hair' ? summary.hairCoverage : null;
  const full = trackedFraction === 1 && (variant === 'accepted' || maskedTrackedFraction === 1);
  return {status: !summary.frames ? 'no-frames' : !summary.trackedFrames ? 'no-tracking' : full ? 'full' : 'partial',
    trackedFraction, maskedTrackedFraction, untrackedFrames: summary.frames - summary.trackedFrames,
    trackedFramesWithoutMask: variant === 'hair'
      ? summary.trackedHairFrames - Math.round((summary.hairCoverage ?? 0) * summary.trackedHairFrames) : null};
}
export const distribution = (values: readonly number[]): Distribution | null => {
  const sorted = values.filter(Number.isFinite).slice().sort((a,b) => a-b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length/2);
  return {median: sorted.length%2 ? sorted[middle]! : (sorted[middle-1]!+sorted[middle]!)/2,
    p95: sorted[Math.ceil(sorted.length*.95)-1]!, max: sorted[sorted.length-1]!};
};
const STAGES = ['sourceDrawMs','detectorDrawMs','sourceReadbackMs','sourceHashMs','faceBitmapMs','faceRequestWallMs',
  'faceInferenceMs','faceWorkerMs','faceExtractionMs','faceWorkerValidationMs','faceClientValidationMs',
  'faceTransportSchedulingMs','prerequisitesWaitMs','detectionHashMs','prepareMs','finishMs','renderMs',
  'schedulerWaitMs','hairWaitMs','hairInferenceMs','hairExtractionMs','cleanCameraMs','composeMs',
  'continuityMs','finalChecksMs','publishMs'] as const;

export function summarize(samples: readonly FrameSample[]): ProfileSummary {
  const first = samples[0], last = samples.at(-1);
  const durationMs = first && last ? last.publishedAtMs-first.publishedAtMs : 0;
  const intervals = samples.slice(1).map((sample,index) => sample.publishedAtMs-samples[index]!.publishedAtMs);
  const cameraElapsed = first && last ? last.capturedAtMs-first.capturedAtMs : 0;
  const trackedHair = samples.filter(row => row.hasFace && row.variant==='hair');
  const videoFrames = first?.videoPresentedFrames !== null && first?.videoPresentedFrames !== undefined
    && last?.videoPresentedFrames !== null && last?.videoPresentedFrames !== undefined
    ? last.videoPresentedFrames-first.videoPresentedFrames : null;
  return {frames:samples.length,durationMs,processedFps:samples.length>1 && durationMs>0 ? (samples.length-1)*1000/durationMs : null,
    videoDeliveryFps:videoFrames!==null && videoFrames>=0 && cameraElapsed>0 ? videoFrames*1000/cameraElapsed : null,
    trackedFrames:samples.filter(row=>row.hasFace).length,maskedFrames:samples.filter(row=>row.hasMask).length,
    trackedHairFrames:trackedHair.length,hairCoverage:trackedHair.length ? trackedHair.filter(row=>row.hasMask).length/trackedHair.length : null,
    changedHairFrames:samples.filter(row=>row.changedPixels>0).length,processing:distribution(samples.map(row=>row.totalMs)),
    gpuHairFraction:trackedHair.some(row=>row.pipeline==='test3')
      ? trackedHair.filter(row=>row.native?.gpuCompositorUsed===true).length/trackedHair.length : null,
    frameInterval:distribution(intervals),stages:Object.fromEntries(STAGES.map(key=>[key,distribution(samples.flatMap(row=>typeof row[key]==='number'?[row[key]]:[]))])),
    source:first ? {width:first.sourceWidth,height:first.sourceHeight}:null};
}

interface BenchmarkSegment {
  pipeline: Pipeline; warmupStartedAtMs: number | null; measureStartedAtMs: number | null; endedAtMs: number | null;
  validWarmupFrames: number;
  samples: FrameSample[];
  completed: CompletedSegment | null;
}
interface Benchmark {
  id: string; sessionId: string; variant: 'hair' | 'accepted'; startedAtMs: number; completed: boolean;
  cancelledReason: string | null; index: number; segments: BenchmarkSegment[];
}
export class FrameProfiler {
  private serial = 0;
  private readonly rows: FrameSample[] = [];
  private benchmark: Benchmark | null = null;
  private readonly warmupMs = 5000;
  private readonly measureMs = 30000;
  private nextPipeline: Pipeline | null = null;
  private comparisonVersion = 0;
  private readonly capacity: number;
  constructor(capacity = 4096) {
    if (!Number.isInteger(capacity) || capacity<2) throw new Error('The profiling sample capacity must be at least two.');
    this.capacity=capacity;
  }
  add(input: FrameInput): FrameSample {
    if (!Number.isFinite(input.capturedAtMs) || !Number.isFinite(input.publishedAtMs) || input.publishedAtMs<input.capturedAtMs
      || !PIPELINES.includes(input.pipeline)) throw new Error('Invalid completed profiling frame.');
    const row = {...input,native:input.native ? {...input.native}:null,serial:++this.serial};
    this.rows.push(row); if (this.rows.length>this.capacity) this.rows.shift();
    const run = this.benchmark;
    if (run && !run.completed && !run.cancelledReason) {
      if (input.sessionId!==run.sessionId || input.variant!==run.variant) this.cancel('Session or hair setting changed.');
      else {
        const segment = run.segments[run.index]!;
        if (input.pipeline===segment.pipeline) {
          // Warmup starts on the first completed frame in the requested pipeline.
          if (segment.warmupStartedAtMs===null) segment.warmupStartedAtMs=input.publishedAtMs;
          if (segment.measureStartedAtMs===null && input.hasFace && (input.variant==='accepted' || input.hasMask)) segment.validWarmupFrames++;
          if (segment.measureStartedAtMs===null && input.publishedAtMs-segment.warmupStartedAtMs>15000 && segment.validWarmupFrames<3)
            this.cancel('A tracked face with valid hair masks was unavailable during warmup.');
          if (segment.measureStartedAtMs===null && input.publishedAtMs-segment.warmupStartedAtMs>=this.warmupMs && segment.validWarmupFrames>=3)
            segment.measureStartedAtMs=input.publishedAtMs;
          if (segment.measureStartedAtMs!==null) {
            segment.samples.push(row);
            if (input.publishedAtMs-segment.measureStartedAtMs>=this.measureMs) {
              segment.endedAtMs=input.publishedAtMs;
              const summary=summarize(segment.samples);
              segment.completed={pipeline:segment.pipeline,variant:run.variant,endedAtMs:input.publishedAtMs,
                summary,coverage:workCoverage(summary,run.variant)};
              this.comparisonVersion++; run.index++;
              if (run.index===run.segments.length) run.completed=true;
              else this.nextPipeline=run.segments[run.index]!.pipeline;
            }
          }
        }
      }
    }
    return {...row,native:row.native ? {...row.native}:null};
  }
  begin(sessionId: string, variant: 'hair'|'accepted', at: number): Pipeline {
    this.benchmark={id:crypto.randomUUID(),sessionId,variant,startedAtMs:at,completed:false,cancelledReason:null,index:0,
      segments:PIPELINES.map(pipeline=>({pipeline,warmupStartedAtMs:null,measureStartedAtMs:null,endedAtMs:null,validWarmupFrames:0,samples:[],completed:null}))};
    this.comparisonVersion++; this.nextPipeline=null; return 'current';
  }
  cancel(reason: string): void {
    if (this.benchmark && !this.benchmark.completed && !this.benchmark.cancelledReason) this.benchmark.cancelledReason=reason;
    this.nextPipeline=null;
  }
  takeNextPipeline(): Pipeline | null {const value=this.nextPipeline;this.nextPipeline=null;return value;}
  get running(): boolean {return !!this.benchmark && !this.benchmark.completed && !this.benchmark.cancelledReason;}
  get hasSamples(): boolean {return this.rows.length>0;}
  get comparisonRevision(): number {return this.comparisonVersion;}
  /** Only four small cached summaries; never scans or copies per-frame samples. */
  get completedSegments(): CompletedSegment[] {
    return this.benchmark?.segments.flatMap(segment=>segment.completed ? [structuredClone(segment.completed)] : []) ?? [];
  }
  progress(at: number): string {
    const run=this.benchmark;
    if (!run) return 'Measure each algorithm for 30 seconds after warmup. The comparison takes about two and a half minutes.';
    if (run.cancelledReason) return `Measurement stopped: ${run.cancelledReason} Partial results can be downloaded.`;
    if (run.completed) return run.segments.some(segment=>segment.completed?.coverage.status!=='full')
      ? 'Measurement complete with missing tracking or hair masks. Compare coverage alongside speed; timings include those frames.'
      : 'Measurement complete with full tracking and requested mask coverage. Compare the results below or download timings.';
    const segment=run.segments[run.index]!;
    if (segment.warmupStartedAtMs===null) return `Starting ${PIPELINE_LABELS[segment.pipeline]}…`;
    if (segment.measureStartedAtMs===null) return `Warming up ${PIPELINE_LABELS[segment.pipeline]}…`;
    return `Measuring ${PIPELINE_LABELS[segment.pipeline]} · ${Math.min(30,Math.max(0,Math.floor((at-segment.measureStartedAtMs)/1000)))} / 30 seconds`;
  }
  samplesAfter(serial: number): FrameSample[] {
    return this.rows.filter(row=>row.serial>serial).map(row=>({...row,native:row.native?{...row.native}:null}));
  }
  recent(sessionId: string, pipeline: Pipeline, variant: string): FrameSample[] {
    const end=this.rows.at(-1)?.publishedAtMs ?? 0;
    // Never mix pre-switch samples into the current contiguous segment.
    const result: FrameSample[]=[];
    for(let index=this.rows.length-1;index>=0;index--) {
      const row=this.rows[index]!;
      if(row.sessionId!==sessionId || row.pipeline!==pipeline || row.variant!==variant || end-row.publishedAtMs>10000) break;
      result.push(row);
    }
    return result.reverse();
  }
  snapshot(): Record<string, unknown> {
    const run=this.benchmark;
    return {schema:'ar-stage3-performance-v1',timeOriginMs:performance.timeOrigin,
      clock:'performance.now in this page; publication means completed canvas submission, not physical display scanout',
      stagePolicy:'Parallel durations overlap; do not add source hash, face request and hair inference to derive total time.',
      coveragePolicy:COVERAGE_POLICY,
      privacy:'Numeric timings and counters only; no camera images, detections, masks or identity hashes.',
      firstAvailableSerial:this.rows[0]?.serial ?? null,lastSerial:this.serial,capacity:this.capacity,
      benchmark:run ? {id:run.id,sessionId:run.sessionId,variant:run.variant,startedAtMs:run.startedAtMs,
        completed:run.completed,cancelledReason:run.cancelledReason,index:run.index,warmupMs:this.warmupMs,measureMs:this.measureMs,
        order:[...PIPELINES],segments:run.segments.map(segment=>{
          const {completed,...details}=segment;
          const summary=completed ? structuredClone(completed.summary) : summarize(segment.samples);
          return {...details,samples:segment.samples.map(row=>({...row,native:row.native?{...row.native}:null})),
            summary,coverage:completed ? {...completed.coverage} : workCoverage(summary,run.variant)};
        })}:null};
  }
  /** Explicit export only: the visible fallback and downloaded file share one JSON string. */
  exportJSON(): string {return JSON.stringify({...this.snapshot(),samples:this.samplesAfter(0)},null,2);}
}
