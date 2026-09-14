import {FramePump} from './frame-pump.ts';
import {FPS_OPTIONS} from '../../options.ts';
import {liveFrameIdentity} from '../../frame-identity.ts';
import {createOwnedSourceFrame} from './speed-options.ts';
import type {OwnedSourceFrame} from './speed-options.ts';
import type {Pipeline} from './profiles.ts';
import type {FrameInput} from './frame-profiler.ts';
import type {ComparisonRenderer,HairMask} from './comparison-renderer.ts';
import type {LiveHairStats} from '../hair-live-preview/renderer.ts';
import type {DetectorClient} from '../performance-stage2/face-detector.ts';
import type {FaceStageTiming} from '../performance-stage2/face-timing.ts';
import type {HairClient,HairSegmentationResult} from '../hair-live-preview/hair-client.ts';
import type {Detection} from '../../../../../references/perfect-temples/src/runtime/detector.ts';
import {hairModelById} from '../hair-live-preview/models.ts';
import type {HairModelId} from '../hair-live-preview/models.ts';
import type {EyewearId} from '../../../../../references/perfect-temples/src/render/eyewear.ts';
import {hairInputBitmap, SPEED_EXPERIMENTS} from './experiments.ts';

interface Packet {
  sequence:number;capturedAtMs:number;canvas:HTMLCanvasElement;rgba:ImageData;disposed:boolean;
  drawMs:number;readMs:number;videoFrames:number|null;mediaTime:number|null;presentation:number|null;
  pipeline:Pipeline;variant:'hair'|'accepted';source?:OwnedSourceFrame;
}
interface Inferred {
  detection:Detection;sourceIdentity:string;detectionIdentity:string;face:FaceStageTiming|null;
  hashMs:number;detectorDrawMs:number;faceBitmapMs:number;faceWallMs:number;detectionHashMs:number;
  hair:Promise<HairSegmentationResult|null>;hairResult:HairSegmentationResult|null;
  inferenceStartedAt:number;hairAdmissionWaitMs:number;
}
interface Prepared {visible:boolean;mask:HairMask|null;hair:HairSegmentationResult|null;prepareMs:number;hairWaitMs:number;}
export interface PumpContext {
  id:string;video:HTMLVideoElement;renderer:ComparisonRenderer;detector:DetectorClient;
  hair:()=>HairClient;hairId:HairModelId;eyewearId:EyewearId;hairReady:()=>boolean;
  mode:'fresh'|'overlap';pipeline:Pipeline;variant:()=> 'hair'|'accepted';generation:number;
  owns:()=>boolean;nextSequence:()=>number;onBusy:()=>void;onHairError:(error:unknown)=>void;
  onError:(error:unknown)=>void;
  onPublished:(row:FrameInput,identity:{sourceIdentity:string;detectionIdentity:string},stats:LiveHairStats)=>void;
  backend:()=>{active:string|null;renderer:string|null};
  /** Opt-in experiment: cap the hair segmenter input edge; null/undefined keeps G's full-size bitmap. */
  hairMaxEdge?:number|null;
}
const hash=async(bytes:Uint8Array|Uint8ClampedArray):Promise<string>=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new Uint8Array(bytes).buffer)),v=>v.toString(16).padStart(2,'0')).join('');
const context=(canvas:HTMLCanvasElement):CanvasRenderingContext2D=>{
  const c=canvas.getContext('2d',{alpha:false,willReadFrequently:true,colorSpace:'srgb'});if(!c)throw new Error('Camera canvas unavailable.');return c;
};
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
  const stream=c.video.srcObject;
  const cameraFps=stream instanceof MediaStream?stream.getVideoTracks()[0]?.getSettings().frameRate??null:null;
  const pump=new FramePump<Packet,Inferred,Prepared>({mode:c.mode,identity:p=>p,
    infer:async(p,signal)=>{
      c.onBusy();const alive=()=>owns()&&!p.disposed&&!signal.aborted;
      const inferenceStartedAt=performance.now(),hashStart=performance.now();let hashMs=0;
      const sha=FPS_OPTIONS.identity ? Promise.resolve(liveFrameIdentity('source',c.id,p.sequence))
        : hash(p.rgba.data).then(value=>{hashMs=performance.now()-hashStart;return value;});
      const hairBitmap=c.hairReady()&&p.variant==='hair'?hairInputBitmap(p.canvas,c.hairMaxEdge):Promise.resolve(null);
      let markHairStarted:()=>void=()=>{};
      const hairStarted=new Promise<void>(resolve=>{markHairStarted=resolve;});
      // One serial hair worker; at most the pump's second owned image can wait.
      const previousHair=hairTail;
      const hair=Promise.all([sha,hairBitmap,previousHair]).then(async([sourceIdentity,bitmap])=>{
        if(!bitmap){markHairStarted();return null;}if(!alive()){bitmap.close();markHairStarted();return null;}
        try {const result=c.hair().segment(bitmap,sourceIdentity,p.sequence);markHairStarted();return await result;}
        catch(error){if(alive())c.onHairError(error);return null;}
        finally{markHairStarted();}
      }).catch(async(error)=>{const bitmap=await hairBitmap.catch(()=>null);bitmap?.close();markHairStarted();if(alive())c.onHairError(error);return null;});
      hairTail=hair.then(()=>undefined);
      const input=document.createElement('canvas'),scale=Math.min(1,640/Math.max(p.canvas.width,p.canvas.height));
      input.width=Math.max(1,Math.round(p.canvas.width*scale));input.height=Math.max(1,Math.round(p.canvas.height*scale));
      const drawStart=performance.now();context(input).drawImage(p.canvas,0,0,input.width,input.height);const detectorDrawMs=performance.now()-drawStart;
      const bitmapStart=performance.now();const bitmap=await createImageBitmap(input);const faceBitmapMs=performance.now()-bitmapStart;
      input.width=input.height=0;
      if(!alive()){bitmap.close();throw new DOMException('Frame revoked.','AbortError');}
      const faceStart=performance.now();const detection=await c.detector.detect(bitmap,p.capturedAtMs);
      const faceWallMs=performance.now()-faceStart,face=c.detector.lastTiming;
      const sourceIdentity=await sha;const detectionHashStart=performance.now();
      const detectionIdentity=FPS_OPTIONS.identity ? liveFrameIdentity('detection',c.id,p.sequence)
        : await hash(new TextEncoder().encode(JSON.stringify(detection)));
      const detectionHashMs=FPS_OPTIONS.identity ? 0 : performance.now()-detectionHashStart,hairAdmissionStart=performance.now();
      // Do not let detached hair promises become a hidden queue. Inference may
      // release its slot only once this image owns the worker (or is skipped).
      await hairStarted;
      const hairAdmissionWaitMs=performance.now()-hairAdmissionStart;
      if(!alive())throw new DOMException('Frame revoked.','AbortError');
      p.source=createOwnedSourceFrame(p.canvas,p.rgba,{sourceIdentity,generation:p.sequence,sessionId:c.id,isCurrent:alive});
      const result:Inferred={detection,sourceIdentity,detectionIdentity,face,hashMs,detectorDrawMs,faceBitmapMs,faceWallMs,
        detectionHashMs,hair,hairResult:null,inferenceStartedAt,hairAdmissionWaitMs};
      void hair.then(value=>{if(alive())result.hairResult=value;});return result;
    },
    prepare:async(p,i,signal)=>{
      if(!owns()||signal.aborted)throw new DOMException('Frame revoked.','AbortError');
      const variant=c.variant();
      c.renderer.selectPipeline(p.pipeline);c.renderer.selectVariant(variant);
      const started=performance.now();
      const visible=await c.renderer.prepare(p.canvas,i.detection,{sourceIdentity:i.sourceIdentity,detectionIdentity:i.detectionIdentity,eyewearModel:c.eyewearId},
        hairModelById(c.hairId),variant==='hair',p.source);
      const prepareMs=performance.now()-started,waitStart=performance.now();
      let hair=i.hairResult;
      if(!hair&&variant==='hair'&&visible) {
        let timer:ReturnType<typeof setTimeout>|undefined;
        try{hair=await Promise.race([i.hair,new Promise<null>(resolve=>{timer=setTimeout(()=>resolve(null),8);})]);}
        finally{if(timer!==undefined)clearTimeout(timer);}
      }
      if(!owns()||signal.aborted)throw new DOMException('Frame revoked.','AbortError');
      if(hair&&(hair.sequence!==p.sequence||hair.sourceIdentity!==i.sourceIdentity))throw new Error('Hair result belongs to another image.');
      const mask=hair&&variant==='hair'?{...hair,detectionIdentity:i.detectionIdentity}:null;
      return {visible,mask,hair,prepareMs,hairWaitMs:performance.now()-waitStart};
    },
    publish:(p,i,r)=>{
      if(!owns())throw new DOMException('Frame revoked.','AbortError');
      const finishStart=performance.now();c.renderer.finish(r.mask);const publishedAtMs=performance.now(),finishMs=publishedAtMs-finishStart;
      const stats=c.renderer.stats!;const backend=c.backend();
      const native=flatten((stats as unknown as {candidatePerformance?:unknown}).candidatePerformance);
      if(FPS_OPTIONS.cpuCompose)for(const [key,value] of Object.entries(native))if(key.startsWith('cpuComposition.'))native['fps.cpuCompose.'+key.slice('cpuComposition.'.length)]=value;
      for(const [key,value] of Object.entries(pump.stats))if(typeof value==='number'||typeof value==='boolean')native['pump.'+key]=value;
      native['pump.inputWaitMs']=i.inferenceStartedAt-p.capturedAtMs;
      native['pump.hairAdmissionWaitMs']=i.hairAdmissionWaitMs;
      native['pump.mode']=c.mode;
      native['fps.identityMode']=FPS_OPTIONS.identity?'session-frame':'sha256';
      native['fps.sourceHashBytesAvoided']=FPS_OPTIONS.identity?p.canvas.width*p.canvas.height*4:0;
      native['experiment.hairMaxEdge']=c.hairMaxEdge??0;
      native['experiment.hairMaskWidth']=r.hair?.width??0;native['experiment.hairMaskHeight']=r.hair?.height??0;
      native['experiment.transmissionResolutionScale']=SPEED_EXPERIMENTS.transmissionResolutionScale;
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
      if(FPS_OPTIONS.bookkeeping)native['fps.bookkeeping.statsCollectionWorkMs']=performance.now()-publishedAtMs;
      c.onPublished(row,{sourceIdentity:i.sourceIdentity,detectionIdentity:i.detectionIdentity},stats);
    },
    disposeFrame:p=>{p.disposed=true;p.canvas.width=p.canvas.height=0;},
    onError:error=>{if(owns())c.onError(error);},
  });
  function schedule():void {
    if(!owns()||draining)return;
    const callback=(_now:number,metadata?:VideoFrameCallbackMetadata):void=>{
      if(!owns()||draining)return;
      // WebKit live-stream currentTime is an advancing playback clock, not an
      // image identity. Admit the rVFC frame even when its PTS stays zero.
      const pairedMetadata=metadata&&Number.isSafeInteger(metadata.presentedFrames)&&metadata.presentedFrames>=0?metadata:null;
      const frameIdentity=pairedMetadata?.presentedFrames??c.video.currentTime;
      const mediaTime=pairedMetadata?(Number.isFinite(pairedMetadata.mediaTime)?pairedMetadata.mediaTime:null):frameIdentity;
      const presentation=pairedMetadata&&Number.isFinite(pairedMetadata.presentationTime)?pairedMetadata.presentationTime:null;
      if(c.video.readyState>=2&&frameIdentity!==lastVideoFrame){lastVideoFrame=frameIdentity;lastVideoAt=performance.now();}
      pump.offer(()=>{
        if(c.video.readyState<2||frameIdentity===lastFrame)return null;
        const capturedAtMs=performance.now(),canvas=document.createElement('canvas');
        const scale=Math.min(1,1280/Math.max(c.video.videoWidth,c.video.videoHeight));
        canvas.width=Math.max(1,Math.round(c.video.videoWidth*scale));canvas.height=Math.max(1,Math.round(c.video.videoHeight*scale));
        const ctx=context(canvas),drawStart=performance.now();ctx.drawImage(c.video,0,0,canvas.width,canvas.height);const drawMs=performance.now()-drawStart;
        // This synchronous copy freezes the pixels used by every hash, worker
        // and render. A later playback-clock read cannot validate that pairing.
        const readStart=performance.now(),rgba=ctx.getImageData(0,0,canvas.width,canvas.height);
        lastFrame=frameIdentity;
        return {sequence:c.nextSequence(),capturedAtMs,canvas,rgba,disposed:false,drawMs,readMs:performance.now()-readStart,
          videoFrames:pairedMetadata?.presentedFrames??null,mediaTime,presentation,pipeline:c.pipeline,variant:c.variant()};
      });schedule();
    };
    if(typeof c.video.requestVideoFrameCallback==='function'){const id=c.video.requestVideoFrameCallback(callback);cancel=()=>c.video.cancelVideoFrameCallback(id);}
    else{const id=requestAnimationFrame(callback);cancel=()=>cancelAnimationFrame(id);}
  }
  const watchdog=setInterval(()=>{
    if(owns()&&!draining&&performance.now()-lastVideoAt>6000)
      c.onError(new Error('The camera stopped sending images. Open it again to restart.'));
  },1000);
  schedule();
  return {stop(){stopped=true;cancel();clearInterval(watchdog);pump.stop();},
    async finishCurrent(){draining=true;cancel();clearInterval(watchdog);await pump.finishCurrent();await hairTail;},stats:()=>pump.stats};
}
