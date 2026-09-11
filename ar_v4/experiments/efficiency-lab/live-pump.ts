import {FramePump} from './frame-pump.ts';
import {createOwnedSourceFrame} from './speed-options.ts';
import type {OwnedSourceFrame} from './speed-options.ts';
import type {Pipeline} from './profiles.ts';
import {PROFILES} from './profiles.ts';
import {CaptureRateAdmission} from './capture-rate.ts';
import {InputCanvasPool,hashInput,inputContext} from './input-resources.ts';
import type {InputHashResult} from './input-resources.ts';
import type {FrameInput} from './frame-profiler.ts';
import type {ComparisonRenderer,HairMask} from './comparison-renderer.ts';
import type {DetectorClient} from '../performance-stage2/face-detector.ts';
import type {FaceStageTiming} from '../performance-stage2/face-timing.ts';
import type {HairClient,HairSegmentationResult} from './hair-cost/client.ts';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import {hairModelById} from '../hair-live-preview/models.ts';
import type {HairModelId} from '../hair-live-preview/models.ts';
import type {EyewearId} from '../../references/perfect-temples/src/render/eyewear.ts';

interface Packet {
  sequence:number;capturedAtMs:number;canvas:HTMLCanvasElement;rgba:ImageData;disposed:boolean;
  drawMs:number;readMs:number;videoFrames:number|null;mediaTime:number|null;presentation:number|null;
  pipeline:Pipeline;variant:'hair'|'accepted';source?:OwnedSourceFrame;
  reads:Promise<unknown>[];canvasReads:number;
}
interface Inferred {
  detection:Detection;sourceSHA256:string;detectionSHA256:string;face:FaceStageTiming|null;
  hashMs:number;detectorDrawMs:number;faceBitmapMs:number;faceWallMs:number;detectionHashMs:number;
  hair:Promise<HairSegmentationResult|null>;hairResult:HairSegmentationResult|null;
  inferenceStartedAt:number;hairAdmissionWaitMs:number;
  sourceHash:InputHashResult;hairBitmapMs:number;
}
interface Prepared {visible:boolean;mask:HairMask|null;hair:HairSegmentationResult|null;prepareMs:number;hairWaitMs:number;nativeSubmittedMs:number|null;
  prePrepareSkipped:boolean;prePrepareMs:number;}
export interface PumpContext {
  id:string;video:HTMLVideoElement;renderer:ComparisonRenderer;detector:DetectorClient;
  hair:()=>HairClient;hairId:HairModelId;eyewearId:EyewearId;hairReady:()=>boolean;
  mode:'fresh'|'overlap';pipeline:Pipeline;variant:()=> 'hair'|'accepted';generation:number;
  owns:()=>boolean;nextSequence:()=>number;onBusy:()=>void;onHairError:(error:unknown)=>void;
  onError:(error:unknown)=>void;
  onPublished:(row:FrameInput,identity:{sourceSHA256:string;detectionSHA256:string})=>void;
  backend:()=>{active:string|null;renderer:string|null};
}
const hash=async(bytes:Uint8Array|Uint8ClampedArray):Promise<string>=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new Uint8Array(bytes).buffer)),v=>v.toString(16).padStart(2,'0')).join('');
function flatten(value:unknown):NonNullable<FrameInput['native']> {
  const out:NonNullable<FrameInput['native']>={};
  const visit=(item:unknown,prefix:string,depth:number):void=>{
    if(!item||typeof item!=='object'||depth>6)return;
    for(const [key,value] of Object.entries(item)) {
      const name=prefix?prefix+'.'+key:key;
      if(typeof value==='number'||typeof value==='boolean'||typeof value==='string'||value===null)out[name]=value;
      else if(!Array.isArray(value)&&!ArrayBuffer.isView(value))visit(value,name,depth+1);
    }
  };visit(value,'',0);return out;
}

export function runExperimentPump(c:PumpContext):{stop():void;finishCurrent():Promise<void>;stats:()=>unknown} {
  let stopped=false,draining=false,cancel:()=>void=()=>{},lastFrame=-1,lastVideoFrame=-1,lastVideoAt=performance.now(),hairTail:Promise<unknown>=Promise.resolve();
  const owns=()=>!stopped&&c.owns();
  const profile=PROFILES[c.pipeline];
  let hasPublished=false,prePrepareRequests=0,prePrepareSkipped=0;
  const startup={faceBitmapRequests:0,faceBitmapReady:0,faceRequests:0,faceCompleted:0,
    sourceHashes:0,hairRequests:0,inferred:0,prepareCalls:0,prepared:0};
  const admission=new CaptureRateAdmission(profile.captureRateHz);
  const capturePool=new InputCanvasPool(profile.leanInputs),facePool=new InputCanvasPool(profile.leanInputs);
  const releaseTasks=new Set<Promise<void>>();
  const stream=c.video.srcObject;
  const cameraFps=stream instanceof MediaStream?stream.getVideoTracks()[0]?.getSettings().frameRate??null:null;
  const pump=new FramePump<Packet,Inferred,Prepared>({mode:c.mode,deferPrefetch:profile.deferPrefetch,identity:p=>p,
    infer:async(p,signal)=>{
      c.onBusy();const alive=()=>owns()&&!p.disposed&&!signal.aborted;
      const inferenceStartedAt=performance.now(),hashStart=performance.now();let hashMs=0;
      let sourceHash!:InputHashResult;
      const sha=hashInput(p.rgba.data,profile.leanInputs).then(result=>{startup.sourceHashes++;sourceHash=result;hashMs=performance.now()-hashStart;return result.value;});
      const hairBitmapStart=performance.now();let hairBitmapMs=0;
      const readHair=c.hairReady()&&p.variant==='hair';p.canvasReads=Number(readHair);
      const hairBitmap=readHair?createImageBitmap(p.canvas).then(bitmap=>{hairBitmapMs=performance.now()-hairBitmapStart;return bitmap;}).finally(()=>{p.canvasReads--;}):Promise.resolve(null);
      p.reads.push(hairBitmap);
      let markHairStarted:()=>void=()=>{};
      const hairStarted=new Promise<void>(resolve=>{markHairStarted=resolve;});
      // One serial hair worker; at most the pump's second owned image can wait.
      const previousHair=hairTail;
      const hair=Promise.all([sha,hairBitmap,previousHair]).then(async([sourceSHA256,bitmap])=>{
        if(!bitmap){markHairStarted();return null;}if(!alive()){bitmap.close();markHairStarted();return null;}
        try {startup.hairRequests++;const result=c.hair().segment(bitmap,sourceSHA256,p.sequence,profile.hairExtractionMode);markHairStarted();return await result;}
        catch(error){if(alive())c.onHairError(error);return null;}
        finally{markHairStarted();}
      }).catch(async(error)=>{const bitmap=await hairBitmap.catch(()=>null);bitmap?.close();markHairStarted();if(alive())c.onHairError(error);return null;});
      hairTail=hair.then(()=>undefined);
      const scale=Math.min(1,640/Math.max(p.canvas.width,p.canvas.height));
      const input=facePool.acquire(Math.max(1,Math.round(p.canvas.width*scale)),Math.max(1,Math.round(p.canvas.height*scale)));
      let bitmap:ImageBitmap,detectorDrawMs:number,faceBitmapMs:number;
      try {
        const drawStart=performance.now();inputContext(input).drawImage(p.canvas,0,0,input.width,input.height);detectorDrawMs=performance.now()-drawStart;
        const bitmapStart=performance.now();startup.faceBitmapRequests++;bitmap=await createImageBitmap(input);startup.faceBitmapReady++;faceBitmapMs=performance.now()-bitmapStart;
      } finally {facePool.release(input);}
      if(!alive()){bitmap.close();throw new DOMException('Frame revoked.','AbortError');}
      const faceStart=performance.now();startup.faceRequests++;const detection=await c.detector.detect(bitmap,p.capturedAtMs);startup.faceCompleted++;
      const faceWallMs=performance.now()-faceStart,face=c.detector.lastTiming;
      const sourceSHA256=await sha;const detectionHashStart=performance.now();
      const detectionSHA256=await hash(new TextEncoder().encode(JSON.stringify(detection)));
      const detectionHashMs=performance.now()-detectionHashStart,hairAdmissionStart=performance.now();
      // Do not let detached hair promises become a hidden queue. Inference may
      // release its slot only once this image owns the worker (or is skipped).
      await hairStarted;
      const hairAdmissionWaitMs=performance.now()-hairAdmissionStart;
      if(!alive())throw new DOMException('Frame revoked.','AbortError');
      p.source=createOwnedSourceFrame(p.canvas,p.rgba,{sourceSHA256,generation:p.sequence,sessionId:c.id,isCurrent:alive});
      const result:Inferred={detection,sourceSHA256,detectionSHA256,face,hashMs,detectorDrawMs,faceBitmapMs,faceWallMs,
        detectionHashMs,hair,hairResult:null,inferenceStartedAt,hairAdmissionWaitMs,sourceHash,hairBitmapMs};
      void hair.then(value=>{if(alive())result.hairResult=value;});startup.inferred++;return result;
    },
    prepare:async(p,i,signal,beginPrefetch)=>{
      if(!owns()||signal.aborted)throw new DOMException('Frame revoked.','AbortError');
      startup.prepareCalls++;
      const variant=c.variant();
      c.renderer.selectPipeline(p.pipeline);
      const prePrepareStarted=performance.now();prePrepareRequests++;
      // Suppress only the repeat presentation before preparation. A fresh pump,
      // renderer/pipeline change or unapplied variant still takes the original
      // call. User toggles continue through selectVariant immediately.
      const skip=profile.suppressUnchangedPublication&&hasPublished
        &&c.renderer.pipeline===p.pipeline&&c.renderer.variant===variant;
      if(skip)prePrepareSkipped++;else c.renderer.selectVariant(variant);
      const prePrepareMs=performance.now()-prePrepareStarted;
      const started=performance.now();let nativeSubmittedMs:number|null=null;
      const onNativeSubmitted=()=>{if(nativeSubmittedMs!==null)return;nativeSubmittedMs=performance.now()-started;beginPrefetch();};
      const visible=await c.renderer.prepare(p.canvas,i.detection,{sourceSHA256:i.sourceSHA256,detectionSHA256:i.detectionSHA256,eyewearModel:c.eyewearId},
        hairModelById(c.hairId),variant==='hair',p.source,profile.deferPrefetch?onNativeSubmitted:undefined);
      // No-face or native fallback preparation may have no submit milestone.
      // Release the gate after it completes; report null, not a fictitious GPU submission.
      if(profile.deferPrefetch&&nativeSubmittedMs===null)beginPrefetch();
      const prepareMs=performance.now()-started,waitStart=performance.now();
      let hair=i.hairResult;
      if(!hair&&variant==='hair'&&visible) {
        let timer:ReturnType<typeof setTimeout>|undefined;
        try{hair=await Promise.race([i.hair,new Promise<null>(resolve=>{timer=setTimeout(()=>resolve(null),8);})]);}
        finally{if(timer!==undefined)clearTimeout(timer);}
      }
      if(!owns()||signal.aborted)throw new DOMException('Frame revoked.','AbortError');
      if(hair&&(hair.sequence!==p.sequence||hair.sourceSHA256!==i.sourceSHA256))throw new Error('Hair result belongs to another image.');
      const mask=hair&&variant==='hair'?{...hair,detectionSHA256:i.detectionSHA256}:null;
      startup.prepared++;
      return {visible,mask,hair,prepareMs,hairWaitMs:performance.now()-waitStart,nativeSubmittedMs,prePrepareSkipped:skip,prePrepareMs};
    },
    publish:(p,i,r)=>{
      if(!owns())throw new DOMException('Frame revoked.','AbortError');
      const finishStart=performance.now();c.renderer.finish(r.mask);const publishedAtMs=performance.now(),finishMs=publishedAtMs-finishStart;
      hasPublished=true;
      const stats=c.renderer.stats!;const backend=c.backend();
      const native=flatten((stats as unknown as {candidatePerformance?:unknown}).candidatePerformance);
      for(const [key,value] of Object.entries(pump.stats))if(typeof value==='number'||typeof value==='boolean')native['pump.'+key]=value;
      for(const [key,value] of Object.entries(admission.stats))native['admission.'+key]=value;
      native['pump.inputWaitMs']=i.inferenceStartedAt-p.capturedAtMs;
      native['pump.hairAdmissionWaitMs']=i.hairAdmissionWaitMs;
      native['pump.mode']=c.mode;
      native['pump.deferPrefetch']=profile.deferPrefetch;
      native['pump.nativeSubmittedMs']=r.nativeSubmittedMs;
      native['publication.suppressUnchangedRequested']=profile.suppressUnchangedPublication;
      native['publication.prePrepareRequests']=prePrepareRequests;
      native['publication.prePrepareSkipped']=prePrepareSkipped;
      native['publication.prePrepareCalls']=prePrepareRequests-prePrepareSkipped;
      native['publication.prePrepareSkippedThisFrame']=r.prePrepareSkipped;
      native['publication.prePrepareMs']=r.prePrepareMs;
      native['input.leanInputs']=profile.leanInputs;
      native['input.hashExplicitCopyBytes']=i.sourceHash.explicitCopyBytes;
      native['input.hashCopyMs']=i.sourceHash.copyMs;
      native['input.hashSubmitMs']=i.sourceHash.submitMs;
      native['input.hashDigestWallMs']=i.sourceHash.digestWallMs;
      native['input.hairBitmapMs']=i.hairBitmapMs;
      native['hairCategory.requestedMode']=profile.hairExtractionMode;
      if(r.hair?.categoryExtraction)for(const [key,value] of Object.entries(r.hair.categoryExtraction))
        if(typeof value==='number'||typeof value==='boolean'||typeof value==='string'||value===null)native['hairCategory.'+key]=value;
      for(const [key,value] of Object.entries(capturePool.stats))native['input.capturePool.'+key]=value;
      for(const [key,value] of Object.entries(facePool.stats))native['input.facePool.'+key]=value;
      const row:FrameInput={sessionId:c.id,sequence:p.sequence,pipeline:p.pipeline,variant:c.renderer.variant,capturedAtMs:p.capturedAtMs,publishedAtMs,
        videoPresentedFrames:p.videoFrames,videoMediaTime:p.mediaTime,videoPresentationTimeMs:p.presentation,cameraSettingFps:cameraFps,
        sourceWidth:p.canvas.width,sourceHeight:p.canvas.height,sourceDrawMs:p.drawMs,detectorDrawMs:i.detectorDrawMs,sourceReadbackMs:p.readMs,
        sourceHashMs:i.hashMs,faceBitmapMs:i.faceBitmapMs,faceRequestWallMs:i.faceWallMs,faceInferenceMs:i.face?.inferenceMs??null,
        faceWorkerMs:i.face?.workerElapsedMs??null,faceExtractionMs:i.face?.workerExtractionMs??null,faceWorkerValidationMs:i.face?.workerValidationMs??null,
        faceClientValidationMs:i.face?.clientValidationMs??null,faceTransportSchedulingMs:i.face?.transportAndSchedulingMs??null,
        prerequisitesWaitMs:finishStart-r.prepareMs-r.hairWaitMs-i.inferenceStartedAt,detectionHashMs:i.detectionHashMs,
        prepareMs:r.prepareMs,finishMs,renderMs:r.prepareMs+finishMs,totalMs:publishedAtMs-p.capturedAtMs,schedulerWaitMs:i.inferenceStartedAt-p.capturedAtMs,
        hairWaitMs:r.hairWaitMs,hairInferenceMs:r.hair?.inferenceMs??null,hairExtractionMs:r.hair?.extractionMs??null,
        hasFace:r.visible,hasMask:stats.hasMask,maskMode:stats.maskOutputMode??null,fallback:stats.fallbackReason,changedPixels:stats.changedPixels,
        faceDelegate:c.detector.delegate,hairDelegate:backend.active,gpuRenderer:backend.renderer,
        cleanCameraMs:stats.timings.cleanCameraMs,composeMs:stats.timings.composeMs,continuityMs:stats.timings.continuityMs,
        finalChecksMs:stats.timings.finalChecksMs,publishMs:stats.timings.publishMs,native};
      c.onPublished(row,{sourceSHA256:i.sourceSHA256,detectionSHA256:i.detectionSHA256});
    },
    disposeFrame:p=>{
      p.disposed=true;
      if(!profile.leanInputs||p.canvasReads===0){capturePool.release(p.canvas);return;}
      // Failures can settle inference before a bitmap snapshot has finished.
      // Revocation is immediate, but reuse waits for every canvas reader.
      const task=Promise.allSettled(p.reads).then(()=>{capturePool.release(p.canvas);}).finally(()=>{releaseTasks.delete(task);});
      releaseTasks.add(task);
    },
    onError:error=>{if(owns())c.onError(error);},
  });
  function schedule():void {
    if(!owns()||draining)return;
    const callback=(_now:number,metadata?:VideoFrameCallbackMetadata):void=>{
      if(!owns()||draining)return;
      // WebKit live-stream currentTime advances on each getter. It is a playback
      // clock, not a stable image identity. Snapshot in this callback's task;
      // use its frame counter for admission, even when its PTS remains zero.
      const pairedMetadata=metadata&&Number.isSafeInteger(metadata.presentedFrames)&&metadata.presentedFrames>=0?metadata:null;
      const frameIdentity=pairedMetadata?.presentedFrames??c.video.currentTime;
      const mediaTime=pairedMetadata?(Number.isFinite(pairedMetadata.mediaTime)?pairedMetadata.mediaTime:null):frameIdentity;
      const presentation=pairedMetadata&&Number.isFinite(pairedMetadata.presentationTime)?pairedMetadata.presentationTime:null;
      if(c.video.readyState>=2&&frameIdentity!==lastVideoFrame){lastVideoFrame=frameIdentity;lastVideoAt=performance.now();}
      const distinctReady=c.video.readyState>=2&&admission.observeReady(frameIdentity);
      // An early rejection preserves a replaceable pending pair: FramePump
      // disposes that pair before invoking the lazy capture callback. Recheck
      // inside the factory at the actual snapshot start; never delay old pixels.
      if(profile.captureRateHz!==null&&(!distinctReady||!admission.canCapture(performance.now()))) {
        if(distinctReady)admission.skipRate();schedule();return;
      }
      let captureInvoked=false;
      pump.offer(()=>{
        captureInvoked=true;
        if(c.video.readyState<2||frameIdentity===lastFrame)return null;
        const capturedAtMs=performance.now();
        if(!admission.canCapture(capturedAtMs)){if(distinctReady)admission.skipRate();return null;}
        const scale=Math.min(1,1280/Math.max(c.video.videoWidth,c.video.videoHeight));
        const canvas=capturePool.acquire(Math.max(1,Math.round(c.video.videoWidth*scale)),Math.max(1,Math.round(c.video.videoHeight*scale)));
        try {
          const ctx=inputContext(canvas),drawStart=performance.now();ctx.drawImage(c.video,0,0,canvas.width,canvas.height);const drawMs=performance.now()-drawStart;
          // drawImage freezes the source in owned canvas storage. Every hash,
          // face/hair bitmap and render below uses these same pixels. A later
          // playback-clock read cannot validate that pairing and must not veto it.
          const readStart=performance.now(),rgba=ctx.getImageData(0,0,canvas.width,canvas.height);
          const packet:Packet={sequence:c.nextSequence(),capturedAtMs,canvas,rgba,disposed:false,drawMs,readMs:performance.now()-readStart,
            videoFrames:pairedMetadata?.presentedFrames??null,mediaTime,presentation,pipeline:c.pipeline,variant:c.variant(),reads:[],canvasReads:0};
          admission.accepted(capturedAtMs);lastFrame=frameIdentity;return packet;
        } catch(error) {capturePool.release(canvas);throw error;}
      });
      if(distinctReady&&!captureInvoked)admission.skipBackpressure();
      schedule();
    };
    if(typeof c.video.requestVideoFrameCallback==='function'){const id=c.video.requestVideoFrameCallback(callback);cancel=()=>c.video.cancelVideoFrameCallback(id);}
    else{const id=requestAnimationFrame(callback);cancel=()=>cancelAnimationFrame(id);}
  }
  const watchdog=setInterval(()=>{
    if(owns()&&!draining&&performance.now()-lastVideoAt>6000)
      c.onError(new Error('The camera stopped sending images. Open it again to restart.'));
  },1000);
  schedule();
  return {stop(){stopped=true;cancel();clearInterval(watchdog);pump.stop();capturePool.dispose();facePool.dispose();},
    async finishCurrent(){draining=true;cancel();clearInterval(watchdog);await pump.finishCurrent();await hairTail;
      await Promise.allSettled([...releaseTasks]);capturePool.dispose();facePool.dispose();},stats:()=>({...pump.stats,admission:admission.stats,startup:{...startup}})};
}
