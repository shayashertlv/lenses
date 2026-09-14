import type {FrameSample, ProfileSummary} from './experiments/speed-lab/frame-profiler.ts';

type Headline = Pick<ProfileSummary, 'frames'|'durationMs'|'processedFps'|'videoDeliveryFps'|'trackedFrames'|'maskedFrames'|'trackedHairFrames'|'hairCoverage'|'changedHairFrames'|'source'>;
const sameOwner=(a:FrameSample,b:FrameSample):boolean=>a.sessionId===b.sessionId&&a.pipeline===b.pipeline&&a.variant===b.variant;
function freezeSummary(summary:ProfileSummary):ProfileSummary {
  for(const value of Object.values(summary.stages))if(value)Object.freeze(value);
  Object.freeze(summary.stages);
  if(summary.source)Object.freeze(summary.source);
  if(summary.processing)Object.freeze(summary.processing);
  if(summary.frameInterval)Object.freeze(summary.frameInterval);
  return Object.freeze(summary);
}

/** Only the currently contiguous publication window. Raw telemetry is owned by
 * FrameProfiler; this private index never changes or removes its samples. */
export class PublicationWindow {
  private rows:FrameSample[]=[];
  private start=0;
  private tracked=0;
  private masked=0;
  private trackedHair=0;
  private maskedHair=0;
  private changed=0;
  private cached:ProfileSummary|null=null;
  private summarizedAt=-Infinity;
  private builds=0;
  private reuses=0;
  private added=0;
  private summaryMs=0;
  private readonly capacity:number;
  constructor(capacity:number) {this.capacity=capacity;}
  invalidate():void {
    this.rows=[];this.start=0;this.tracked=this.masked=this.trackedHair=this.maskedHair=this.changed=0;
    this.cached=null;this.summarizedAt=-Infinity;
  }
  private count(row:FrameSample,direction:1|-1):void {
    this.tracked+=Number(row.hasFace)*direction;this.masked+=Number(row.hasMask)*direction;
    const hair=row.hasFace&&row.variant==='hair';this.trackedHair+=Number(hair)*direction;
    this.maskedHair+=Number(hair&&row.hasMask)*direction;this.changed+=Number(row.changedPixels>0)*direction;
  }
  add(row:FrameSample):void {
    const previous=this.rows.at(-1);
    if(previous&&(!sameOwner(previous,row)||row.publishedAtMs<previous.publishedAtMs))this.invalidate();
    this.rows.push(row);this.count(row,1);this.added++;
    while(this.rows.length-this.start>this.capacity||row.publishedAtMs-this.rows[this.start]!.publishedAtMs>10000) {
      this.count(this.rows[this.start]!, -1);this.start++;
    }
    // Amortized compaction; no shift/copy of the complete window per frame.
    if(this.start>256&&this.start*2>this.rows.length){this.rows=this.rows.slice(this.start);this.start=0;}
  }
  matches(sessionId:string,pipeline:string,variant:string):boolean {
    const row=this.rows.at(-1);return !!row&&row.sessionId===sessionId&&row.pipeline===pipeline&&row.variant===variant;
  }
  private headline():Headline {
    const first=this.rows[this.start],last=this.rows.at(-1),frames=this.rows.length-this.start;
    const durationMs=first&&last?last.publishedAtMs-first.publishedAtMs:0;
    const elapsed=first&&last?last.capturedAtMs-first.capturedAtMs:0;
    const videoFrames=first?.videoPresentedFrames!==null&&first?.videoPresentedFrames!==undefined&&last?.videoPresentedFrames!==null&&last?.videoPresentedFrames!==undefined
      ?last.videoPresentedFrames-first.videoPresentedFrames:null;
    return {frames,durationMs,processedFps:frames>1&&durationMs>0?(frames-1)*1000/durationMs:null,
      videoDeliveryFps:videoFrames!==null&&videoFrames>=0&&elapsed>0?videoFrames*1000/elapsed:null,
      trackedFrames:this.tracked,maskedFrames:this.masked,trackedHairFrames:this.trackedHair,hairCoverage:this.trackedHair?this.maskedHair/this.trackedHair:null,
      changedHairFrames:this.changed,source:first?{width:first.sourceWidth,height:first.sourceHeight}:null};
  }
  summary(now:number,summarize:(rows:readonly FrameSample[])=>ProfileSummary):ProfileSummary {
    if(!Number.isFinite(now))throw new Error('Invalid publication summary clock.');
    if(!this.cached||now<this.summarizedAt||now-this.summarizedAt>=500) {
      const started=performance.now();this.cached=freezeSummary(summarize(this.rows.slice(this.start)));
      this.summaryMs+=performance.now()-started;this.summarizedAt=now;this.builds++;
    } else this.reuses++;
    // FPS and workload coverage are current on every publication. Only human
    // percentile/stage readouts use the explicitly bounded 500ms cache.
    const headline=this.headline();if(headline.source)Object.freeze(headline.source);
    return Object.freeze({...this.cached,...headline});
  }
  get counters():Record<string,number> {
    return {rawRowsIndexed:this.added,summaryBuilds:this.builds,summaryReuses:this.reuses,summaryWorkMs:this.summaryMs};
  }
}
