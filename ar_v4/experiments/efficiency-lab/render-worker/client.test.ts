import test from 'node:test';
import assert from 'node:assert/strict';
import {RenderWorkerRenderer} from './client.ts';
import type {WorkerRequest,WorkerResponse} from './protocol.ts';
import type {LiveHairStats} from '../../speed-lab/renderer.ts';
import {eyewearById} from '../../../references/perfect-temples/src/render/eyewear.ts';
import {hairModelById} from '../../hair-live-preview/models.ts';
const sourceHash='a'.repeat(64),detectionHash='b'.repeat(64);
const pair={sourceSHA256:sourceHash,detectionSHA256:detectionHash,eyewearModel:'amber-horizon'};
const detection={landmarks:[],matrix:null,inferenceMs:0};
const pixels=(red=3)=>({width:2,height:1,colorSpace:'srgb',data:new Uint8ClampedArray([red,4,5,255,8,9,10,255])}) as ImageData;
class FakeWorker {
  onmessage:((event:MessageEvent<WorkerResponse>)=>void)|null=null;
  onerror:((event:ErrorEvent)=>void)|null=null;onmessageerror:((event:MessageEvent)=>void)|null=null;
  terminated=false;requests:WorkerRequest[]=[];pause=false;alter:((response:WorkerResponse)=>WorkerResponse)|null=null;
  terminate():void{this.terminated=true;}
  postMessage(value:WorkerRequest,transfer:Transferable[]):void {
    const request=structuredClone(value,{transfer});this.requests.push(request);if(this.pause)return;
    let response:WorkerResponse;
    if(request.type==='init')response={session:request.session,requestId:request.requestId,type:'ready',eyewear:eyewearById('amber-horizon'),nativeSamples:4};
    else if(request.type==='prepare')response={session:request.session,requestId:request.requestId,type:'prepared',owner:request.owner,visible:false,workerPrepareMs:1};
    else if(request.type==='complete')response={session:request.session,requestId:request.requestId,type:'completed',result:{owner:request.owner,visible:false,
      accepted:pixels(),hair:pixels(17),stats:null,snapshot:null,workerCompleteMs:1,outputCopyMs:1}};
    else response={session:request.session,requestId:request.requestId,type:'diagnostic',owner:request.owner,diagnostic:{}};
    queueMicrotask(()=>this.onmessage?.({data:this.alter?this.alter(response):response} as MessageEvent<WorkerResponse>));
  }
}
function install(t:test.TestContext){
  for(const name of ['Worker','OffscreenCanvas'] as const){const descriptor=Object.getOwnPropertyDescriptor(globalThis,name);
    Object.defineProperty(globalThis,name,{configurable:true,value:FakeWorker});
    t.after(()=>{if(descriptor)Object.defineProperty(globalThis,name,descriptor);else Reflect.deleteProperty(globalThis,name);});}
}
async function fixture(t:test.TestContext){
  install(t);const worker=new FakeWorker(),abort=new AbortController(),rgba=pixels();let published=0,visible=pixels();
  const canvas={width:2,height:1,getContext:()=>({getImageData:()=>rgba,putImageData:(value:ImageData)=>{visible=structuredClone(value);published++;}})} as unknown as HTMLCanvasElement;
  const renderer=await RenderWorkerRenderer.create(canvas,abort.signal,'amber-horizon',()=>worker as unknown as Worker);
  t.after(()=>renderer.dispose());
  return {worker,abort,rgba,canvas,renderer,published:()=>published,visible:()=>visible,showG:()=>{visible=pixels(241);},
    prepare:()=>renderer.prepare(canvas,detection,pair,hairModelById('hair-only'))};
}
test('worker transfer uses independent source bytes and publishes only after completion',async t=>{
  const f=await fixture(t);await f.prepare();assert.equal(f.rgba.data.byteLength,8);assert.equal(f.published(),0);
  assert.throws(()=>f.renderer.finish(null),/No complete/);
  await f.renderer.complete(null);assert.equal(f.published(),0);f.renderer.finish(null);assert.equal(f.published(),1);
  f.renderer.selectVariant('accepted');assert.equal(f.published(),2);
  const request=f.worker.requests.find(r=>r.type==='prepare');assert.ok(request?.type==='prepare');
  assert.deepEqual(request.rgba.data,f.rgba.data);assert.notEqual(request.rgba.data.buffer,f.rgba.data.buffer);
});
test('one pending frame and one RPC reject a second preparation without queuing',async t=>{
  const f=await fixture(t);f.worker.pause=true;const pending=f.prepare();
  await assert.rejects(f.prepare(),/Finish the owned/);assert.equal(f.worker.requests.length,2);
  f.abort.abort();await assert.rejects(pending,/stopped/);assert.equal(f.published(),0);assert.equal(f.worker.terminated,true);
});
test('a response from another source generation terminates without publishing',async t=>{
  const f=await fixture(t);f.worker.alter=response=>response.type==='prepared'?{...response,owner:{...response.owner,generation:999}}:response;
  await assert.rejects(f.prepare(),/another owned frame/);assert.equal(f.worker.terminated,true);assert.equal(f.published(),0);
});
test('a stale RPC/session reply terminates without publishing',async t=>{
  const f=await fixture(t);f.worker.alter=response=>({...response,session:'old-session'});
  await assert.rejects(f.prepare(),/stale or mismatched/);assert.equal(f.worker.terminated,true);assert.equal(f.published(),0);
});
test('worker failure rejects its one pending operation and closes resources',async t=>{
  const f=await fixture(t);f.worker.alter=response=>({session:response.session,requestId:response.requestId,type:'error',message:'WebGL unavailable'});
  await assert.rejects(f.prepare(),/WebGL unavailable/);assert.equal(f.worker.terminated,true);assert.equal(f.published(),0);
});
test('completed dimensions must retain the native resolution rule',async t=>{
  const f=await fixture(t);await f.prepare();f.worker.alter=response=>{
    if(response.type!=='completed')return response;
    const image={width:1,height:1,colorSpace:'srgb',data:new Uint8ClampedArray(4)} as ImageData;
    return {...response,result:{...response.result,accepted:image,hair:image}};
  };
  await assert.rejects(f.renderer.complete(null),/dimensions differ/);assert.equal(f.worker.terminated,true);assert.equal(f.published(),0);
});
test('abort during completion cannot display its late response',async t=>{
  const f=await fixture(t);await f.prepare();f.worker.pause=true;const pending=f.renderer.complete(null);
  f.abort.abort();await assert.rejects(pending,/stopped/);assert.equal(f.published(),0);
});
test('restart uses a new worker session and stopped clients reject work',async t=>{
  const first=await fixture(t);first.renderer.dispose();await assert.rejects(first.prepare(),/stopped/);
  const second=await fixture(t);await second.prepare();await second.renderer.complete(null);second.renderer.finish(null);
  assert.notEqual(first.worker.requests[0]!.session,second.worker.requests[0]!.session);assert.equal(second.published(),1);
});
test('worker re-entry leaves newer G pixels visible through preparation and completion',async t=>{
  const f=await fixture(t);await f.prepare();await f.renderer.complete(null);f.renderer.finish(null);
  assert.equal(f.visible().data[0],17);f.showG();const before=structuredClone(f.visible());
  const preparing=f.renderer.prepare(f.canvas,detection,pair,hairModelById('hair-only'),true,undefined,'accepted');
  assert.deepEqual(f.visible(),before);await preparing;assert.deepEqual(f.visible(),before);
  const request=f.worker.requests.filter(r=>r.type==='prepare').at(-1)!;assert.equal(request.type,'prepare');assert.equal(request.variant,'accepted');
  await f.renderer.complete(null);assert.deepEqual(f.visible(),before);assert.equal(f.published(),1);
  f.renderer.finish(null);assert.equal(f.visible().data[0],3);assert.equal(f.published(),2);
  f.renderer.selectVariant('hair');assert.equal(f.visible().data[0],17);assert.equal(f.published(),3);
});
test('standard publication time measures the visible upload and worker upload remains separate',async t=>{
  const f=await fixture(t);f.worker.alter=response=>response.type==='completed'?{...response,result:{...response.result,
    stats:{timings:{publishMs:917},candidatePerformance:{}} as unknown as LiveHairStats}}:response;
  await f.prepare();await f.renderer.complete(null);f.renderer.finish(null);
  const stats=f.renderer.stats!;
  const native=(stats.candidatePerformance as unknown as {renderWorker:{publishMs:number;workerPublishMs:number}}).renderWorker;
  assert.equal(stats.timings.publishMs,native.publishMs);assert.notEqual(stats.timings.publishMs,917);assert.equal(native.workerPublishMs,917);
});
