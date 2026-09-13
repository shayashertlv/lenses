import {LiveHairRenderer} from './adapted/experiments/speed-lab/renderer.ts';
import {createOwnedSourceFrame} from './adapted/experiments/speed-lab/speed-options.ts';
import {PROFILES} from '../../speed-lab/profiles.ts';
import {assertOwner,assertSameOwner,assertPixels} from './protocol.ts';
import type {WorkerRequest,WorkerResponse,ResponsePayload,FrameOwner} from './protocol.ts';
interface Scope {onmessage:((event:MessageEvent<WorkerRequest>)=>void)|null;postMessage(value:WorkerResponse,transfer?:Transferable[]):void;}
const scope=globalThis as unknown as Scope;
let renderer:LiveHairRenderer|null=null,session:string|null=null,owner:FrameOwner|null=null;
let busy=false,prepared=false,closed=false,lastRequest=0,source:OffscreenCanvas|null=null;
const controller=new AbortController(),generations=new Map<string|number,number>();
const fail=(request:WorkerRequest,error:unknown):void=>{
  closed=true;controller.abort();renderer?.dispose();renderer=null;
  if(source)source.width=source.height=0;source=null;
  scope.postMessage({session:request.session,requestId:request.requestId,type:'error',message:error instanceof Error?error.message:String(error)});
};
scope.onmessage=event=>{
  const request=event.data;
  if(closed)return;
  if(busy || !Number.isSafeInteger(request.requestId) || request.requestId<=lastRequest || !request.session || session!==null&&request.session!==session) {
    fail(request,new Error('Render-worker ownership or single-request bound violated.'));return;
  }
  busy=true;lastRequest=request.requestId;
  const respond=(payload:ResponsePayload,transfer:Transferable[]=[]):void=>scope.postMessage({...payload,requestId:request.requestId,session:request.session},transfer);
  void (async()=>{
    if(request.type==='init') {
      if(renderer||session!==null)throw new Error('Render worker already initialized.');
      session=request.session;
      if(typeof OffscreenCanvas==='undefined')throw new Error('OffscreenCanvas is unavailable in the render worker.');
      renderer=await LiveHairRenderer.create(new OffscreenCanvas(300,150),controller.signal,request.eyewearId);
      respond({type:'ready',eyewear:renderer.eyewear,nativeSamples:renderer.nativeSamples});return;
    }
    if(!renderer)throw new Error('Render worker is not ready.');
    if(request.type==='prepare') {
      if(prepared)throw new Error('Render worker already owns a prepared frame.');
      assertOwner(request.owner);assertPixels(request.rgba);
      if(request.owner.sourceSHA256!==request.pair.sourceSHA256 || request.owner.detectionSHA256!==request.pair.detectionSHA256
        || request.pair.eyewearModel!==renderer.eyewear.id)throw new Error('Render-worker pair identity mismatch.');
      const previous=generations.get(request.owner.sessionId);
      if(previous!==undefined && request.owner.generation<=previous)throw new Error('Render-worker source generation was already consumed.');
      const started=performance.now();owner=Object.freeze({...request.owner});generations.set(owner.sessionId,owner.generation);
      if(source)source.width=source.height=0;
      source=new OffscreenCanvas(request.rgba.width,request.rgba.height);
      // The owned capture may contain alpha. Preserve it here; G applies its
      // existing opaque eligibility and source-over history in its own canvas.
      const context=source.getContext('2d',{alpha:true,willReadFrequently:true,colorSpace:'srgb'});
      if(!context)throw new Error('Render-worker source surface unavailable.');
      context.putImageData(request.rgba,0,0);
      const frameOwner=owner;
      const owned=createOwnedSourceFrame(source,request.rgba,{...frameOwner,isCurrent:()=>!closed&&owner===frameOwner});
      renderer.selectVariant(request.variant);renderer.setPreparationHairEnabled(request.needsHair);
      const visible=await renderer.prepare(source,request.detection,request.pair,request.model,{source:owned,options:PROFILES.combined.options});
      prepared=true;respond({type:'prepared',owner,visible,workerPrepareMs:performance.now()-started});return;
    }
    if(!owner)throw new Error('Render worker has no owned frame.');
    assertSameOwner(request.owner,owner);
    if(request.type==='complete') {
      if(!prepared)throw new Error('Render worker has no pending preparation.');
      if(request.mask&&(request.mask.sourceSHA256!==owner.sourceSHA256 || request.mask.detectionSHA256!==owner.detectionSHA256))
        throw new Error('Render-worker mask belongs to another image/detection.');
      const started=performance.now(),visible=renderer.finish(request.mask),workerCompleteMs=performance.now()-started;
      prepared=false;
      const copyStarted=performance.now(),pixels=renderer.copyCompletedPixels();
      if(!pixels)throw new Error('Render worker produced no complete image.');
      respond({type:'completed',result:{owner,visible,...pixels,stats:renderer.stats,snapshot:renderer.captureSnapshot,
        workerCompleteMs,outputCopyMs:performance.now()-copyStarted}},[pixels.accepted.data.buffer,pixels.hair.data.buffer]);return;
    }
    if(request.type==='diagnostic') {
      if(prepared)throw new Error('Complete the frame before exporting.');
      renderer.selectVariant(request.variant);
      const diagnostic=await renderer.exportDiagnostic();
      respond({type:'diagnostic',owner,diagnostic});return;
    }
    throw new Error('Unknown render-worker request.');
  })().catch(error=>fail(request,error)).finally(()=>{busy=false;});
};
