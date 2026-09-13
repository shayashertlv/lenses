import {assertSourceFrameCurrent} from '../speed-options.ts';
import type {OwnedSourceFrame} from '../speed-options.ts';
import {DEFAULT_EYEWEAR_ID} from '../../../references/perfect-temples/src/render/eyewear.ts';
import type {EyewearId, EyewearDefinition} from '../../../references/perfect-temples/src/render/eyewear.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';
import type {HairMask,PairIdentity,HairModelContract,LiveHairStats,HeldHairInput,CaptureGeometry} from '../../speed-lab/renderer.ts';
import type {LiveVariant} from '../../hair-live-preview/ownership.ts';
import {assertSameOwner,assertPixels} from './protocol.ts';
import type {FrameOwner,RequestPayload,WorkerRequest,WorkerResponse,CompletedFrame} from './protocol.ts';

type WorkerFactory=()=>Worker;
const abortError=()=>new DOMException('Render worker was stopped.','AbortError');
interface PendingRpc {id:number;timer:ReturnType<typeof setTimeout>;resolve:(value:WorkerResponse)=>void;reject:(error:Error)=>void;}
interface RetainedInput {rgba:ImageData;detection:Detection;pair:PairIdentity;model:HairModelContract;}
/** One worker, one RPC, one prepared frame; all publication remains synchronous. */
export class RenderWorkerRenderer {
  private readonly session=crypto.randomUUID();
  private serial=0;private localGeneration=0;private rpc:PendingRpc|null=null;private closed=false;
  private selected:LiveVariant='hair';private owner:FrameOwner|null=null;private pendingFrame=false;
  private result:CompletedFrame|null=null;private retained:RetainedInput|null=null;
  private lease:(()=>boolean)|null=null;private completionMaskKey:string|null=null;
  private ready: {eyewear:EyewearDefinition;nativeSamples:number}|null=null;
  private readonly removeAbort:()=>void;
  private readonly display:HTMLCanvasElement;
  private readonly worker:Worker;
  private metrics={sourceCopyMs:0,sourceCopyBytes:0,prepareRpcMs:0,workerPrepareMs:0,
    maskCopyMs:0,maskCopyBytes:0,completeRpcMs:0,workerCompleteMs:0,outputCopyMs:0,outputBytes:0,workerPublishMs:0,publishMs:0};
  private constructor(display:HTMLCanvasElement,worker:Worker,signal:AbortSignal) {
    this.display=display;this.worker=worker;
    const abort=()=>this.dispose();signal.addEventListener('abort',abort,{once:true});
    this.removeAbort=()=>signal.removeEventListener('abort',abort);
    worker.onmessage=(event:MessageEvent<WorkerResponse>)=>{
      if(this.closed)return;
      const response=event.data,pending=this.rpc;
      if(!pending||response.session!==this.session||response.requestId!==pending.id) {this.fail(new Error('Render worker returned a stale or mismatched request.'));return;}
      if(response.type==='error'){this.fail(new Error('Render worker failed: '+response.message));return;}
      this.rpc=null;clearTimeout(pending.timer);pending.resolve(response);
    };
    worker.onerror=event=>{event.preventDefault();this.fail(new Error('Render worker failed: '+(event.message||'worker execution error')));};
    worker.onmessageerror=()=>this.fail(new Error('Render worker returned unreadable data.'));
  }
  static async create(display:HTMLCanvasElement,signal:AbortSignal,id:EyewearId=DEFAULT_EYEWEAR_ID,
    factory:WorkerFactory=()=>new Worker(new URL('./worker.ts',import.meta.url),{type:'module',name:'AR render worker'})):Promise<RenderWorkerRenderer> {
    if(signal.aborted)throw abortError();
    if(typeof Worker==='undefined'||typeof OffscreenCanvas==='undefined')throw new Error('This browser does not support the OffscreenCanvas render-worker experiment. Select G to continue.');
    const instance=new RenderWorkerRenderer(display,factory(),signal);
    try {
      const response=await instance.request({type:'init',eyewearId:id});
      if(response.type!=='ready')throw new Error('Render worker did not initialize.');
      instance.ready={eyewear:response.eyewear,nativeSamples:response.nativeSamples};return instance;
    } catch(error) {instance.dispose();throw error;}
  }
  private request(payload:RequestPayload,transfer:Transferable[]=[]):Promise<WorkerResponse> {
    if(this.closed)return Promise.reject(abortError());
    if(this.rpc)return Promise.reject(new Error('Only one render-worker request may be pending.'));
    const id=++this.serial;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>this.fail(new Error('Render worker did not complete within 30 seconds.')),30_000);
      this.rpc={id,timer,resolve,reject};
      try {const request:WorkerRequest={...payload,session:this.session,requestId:id};this.worker.postMessage(request,transfer);}
      catch(error){this.fail(error instanceof Error?error:new Error(String(error)));}
    });
  }
  get eyewear():EyewearDefinition {if(!this.ready)throw new Error('Render worker is not ready.');return this.ready.eyewear;}
  get nativeSamples():number {return this.ready?.nativeSamples??0;}
  get variant():LiveVariant {return this.selected;}
  get stats():LiveHairStats|null {
    const stats=this.result?.stats;if(this.closed||this.pendingFrame||!stats)return null;
    return {...structuredClone(stats),selectedVariant:this.selected,
      timings:{...structuredClone(stats.timings),publishMs:this.metrics.publishMs},
      candidatePerformance:{...structuredClone(stats.candidatePerformance),
      ...{renderWorker:{backend:'offscreen-worker',singleInflight:true,...this.metrics}}}};
  }
  get captureSnapshot():CaptureGeometry|null {
    const snapshot=this.result?.snapshot;if(this.closed||this.pendingFrame||!snapshot)return null;
    return {...structuredClone(snapshot),hairPreview:{...structuredClone(snapshot.hairPreview),variant:this.selected,
      applied:this.selected==='hair'&&(this.result?.stats?.changedPixels??0)>0}};
  }
  selectVariant(variant:LiveVariant):void {
    if(variant!=='hair'&&variant!=='accepted')throw new Error('Unknown render-worker variant.');
    this.selected=variant;if(!this.pendingFrame)this.publish();
  }
  async prepare(frame:HTMLCanvasElement,detection:Detection,pair:PairIdentity,model:HairModelContract,
    needsHair=this.selected==='hair',source?:OwnedSourceFrame,variant:LiveVariant=this.selected):Promise<boolean> {
    if(this.closed)throw abortError();if(this.pendingFrame||this.rpc)throw new Error('Finish the owned render-worker frame first.');
    if(variant!=='hair'&&variant!=='accepted')throw new Error('Unknown render-worker variant.');
    if(source)assertSourceFrameCurrent(source,frame,pair.sourceSHA256);
    const rgba=source?.rgba??frame.getContext('2d',{willReadFrequently:true,colorSpace:'srgb'})?.getImageData(0,0,frame.width,frame.height);
    if(!rgba)throw new Error('Render-worker source pixels unavailable.');assertPixels(rgba);
    const owner:FrameOwner={sessionId:source?.sessionId??this.session,generation:source?.generation??++this.localGeneration,
      sourceSHA256:pair.sourceSHA256,detectionSHA256:pair.detectionSHA256};
    // Re-entering from another pipeline must keep its current display intact.
    // Select this frame's variant only after private preparation owns publication.
    this.pendingFrame=true;this.selected=variant;this.owner=owner;this.result=null;this.completionMaskKey=null;
    this.retained={rgba,detection:structuredClone(detection),pair:{...pair},model:structuredClone(model)};
    this.lease=source?()=>source.isCurrent():()=>!this.closed;
    const copyStarted=performance.now(),copy=structuredClone(rgba);
    this.metrics.sourceCopyMs=performance.now()-copyStarted;this.metrics.sourceCopyBytes=copy.data.byteLength;
    const started=performance.now();
    try {
      const response=await this.request({type:'prepare',owner,rgba:copy,detection:this.retained.detection,pair:this.retained.pair,
        model:this.retained.model,needsHair,variant:this.selected},[copy.data.buffer]);
      if(!this.lease?.())throw abortError();
      if(response.type!=='prepared')throw new Error('Render worker did not prepare this frame.');
      assertSameOwner(response.owner,owner);
      this.metrics.prepareRpcMs=performance.now()-started;this.metrics.workerPrepareMs=response.workerPrepareMs;return response.visible;
    } catch(error){this.fail(error instanceof Error?error:new Error(String(error)));throw error;}
  }
  /** Called while FramePump still owns preparation, after the exact-mask decision. */
  async complete(mask:HairMask|null):Promise<void> {
    if(this.closed)throw abortError();if(!this.pendingFrame||!this.owner||this.result)throw new Error('No pending render-worker frame.');
    if(!this.lease?.())throw abortError();
    if(mask&&(mask.sourceSHA256!==this.owner.sourceSHA256||mask.detectionSHA256!==this.owner.detectionSHA256))throw new Error('Render-worker mask belongs to another image/detection.');
    const copyStarted=performance.now(),copy=mask?structuredClone(mask):null;
    const transfer:Transferable[]=copy?[copy.category.buffer,...(copy.confidence?[copy.confidence.buffer]:[])]:[];
    this.metrics.maskCopyMs=performance.now()-copyStarted;
    this.metrics.maskCopyBytes=copy?copy.category.byteLength+(copy.confidence?.byteLength??0):0;
    const started=performance.now();
    try {
      const response=await this.request({type:'complete',owner:this.owner,mask:copy},transfer);
      if(!this.lease?.())throw abortError();
      if(response.type!=='completed')throw new Error('Render worker did not complete this frame.');
      assertSameOwner(response.result.owner,this.owner);assertPixels(response.result.accepted);assertPixels(response.result.hair);
      const expectedWidth=Math.min(this.retained!.rgba.width,1280),expectedHeight=Math.max(1,Math.round(expectedWidth*this.retained!.rgba.height/this.retained!.rgba.width));
      if(response.result.accepted.width!==response.result.hair.width||response.result.accepted.height!==response.result.hair.height
        || response.result.accepted.width!==expectedWidth || response.result.accepted.height!==expectedHeight)
        throw new Error('Render-worker output dimensions differ.');
      const snapshot=response.result.snapshot;
      if(snapshot&&(snapshot.hairPreview.sourceSHA256!==this.owner.sourceSHA256||snapshot.hairPreview.detectionSHA256!==this.owner.detectionSHA256))
        throw new Error('Render-worker geometry belongs to another image/detection.');
      this.metrics.completeRpcMs=performance.now()-started;this.metrics.workerCompleteMs=response.result.workerCompleteMs;
      this.metrics.workerPublishMs=response.result.stats?.timings.publishMs??0;
      this.metrics.outputCopyMs=response.result.outputCopyMs;this.metrics.outputBytes=response.result.accepted.data.byteLength+response.result.hair.data.byteLength;
      this.result=response.result;this.completionMaskKey=this.maskKey(mask);
    } catch(error){this.fail(error instanceof Error?error:new Error(String(error)));throw error;}
  }
  private maskKey(mask:HairMask|null):string {return mask?JSON.stringify([mask.sourceSHA256,mask.detectionSHA256,mask.categorySHA256,mask.confidenceSHA256??null,mask.modelSHA256]):'none';}
  finish(mask:HairMask|null):boolean {
    if(this.closed||!this.pendingFrame||!this.result||!this.lease?.())throw new Error('No complete owned render-worker frame to publish.');
    if(this.maskKey(mask)!==this.completionMaskKey)throw new Error('Render-worker publication mask differs from its completed frame.');
    this.pendingFrame=false;this.lease=null;this.publish();return this.result.visible;
  }
  private publish():void {
    if(this.closed||this.pendingFrame||!this.result)return;
    const pixels=this.selected==='hair'?this.result.hair:this.result.accepted,started=performance.now();
    if(this.display.width!==pixels.width||this.display.height!==pixels.height){this.display.width=pixels.width;this.display.height=pixels.height;}
    const context=this.display.getContext('2d',{willReadFrequently:true,colorSpace:'srgb'});
    if(!context)throw new Error('Render-worker display unavailable.');context.putImageData(pixels,0,0);this.metrics.publishMs=performance.now()-started;
  }
  copyHeldInput():HeldHairInput|null {
    if(this.closed||this.pendingFrame||!this.retained||!this.result)return null;
    const input=this.retained,source=document.createElement('canvas');source.width=input.rgba.width;source.height=input.rgba.height;
    const context=source.getContext('2d',{willReadFrequently:true,colorSpace:'srgb'});if(!context)throw new Error('Held render-worker source unavailable.');
    context.putImageData(input.rgba,0,0);
    return {source,detection:structuredClone(input.detection),pair:{...input.pair},expectedModel:structuredClone(input.model)};
  }
  async exportDiagnostic():Promise<Record<string,unknown>|null> {
    if(this.closed||this.pendingFrame||!this.owner||!this.result)return null;
    const response=await this.request({type:'diagnostic',owner:this.owner,variant:this.selected});
    if(response.type!=='diagnostic')throw new Error('Render-worker diagnostic unavailable.');assertSameOwner(response.owner,this.owner);
    return response.diagnostic?{...response.diagnostic,stats:this.stats,renderWorker:{backend:'offscreen-worker',...this.metrics}}:null;
  }
  private fail(error:Error):void {const pending=this.rpc;this.rpc=null;if(pending){clearTimeout(pending.timer);pending.reject(error);}this.dispose();}
  dispose():void {
    if(this.closed)return;this.closed=true;this.removeAbort();
    const pending=this.rpc;this.rpc=null;if(pending){clearTimeout(pending.timer);pending.reject(abortError());}
    this.worker.onmessage=null;this.worker.onerror=null;this.worker.onmessageerror=null;this.worker.terminate();
    this.pendingFrame=false;this.result=null;this.retained=null;this.owner=null;this.lease=null;
  }
}
