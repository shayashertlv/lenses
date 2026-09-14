import assert from 'node:assert/strict';
import test from 'node:test';
import {contentHash, heldContentPair, identityMetadata, isContentHash, isPairedIdentity, isSourceIdentity, liveFrameIdentity, parseLiveIdentity} from '../runtime/frame-identity.ts';
import {createOwnedSourceFrame, assertSourceFrameCurrent} from '../runtime/experiments/speed-lab/speed-options.ts';
import {HairClient} from '../runtime/experiments/hair-live-preview/hair-client.ts';
import type {HairWorkerPort} from '../runtime/experiments/hair-live-preview/hair-client.ts';
import type {HairWorkerRequest} from '../runtime/experiments/hair-live-preview/hair-protocol.ts';
import {HAIR_MODELS} from '../runtime/experiments/hair-live-preview/models.ts';
import {FrameProfiler} from '../runtime/experiments/speed-lab/frame-profiler.ts';
import type {FrameInput} from '../runtime/experiments/speed-lab/frame-profiler.ts';

const session='camera-session-1';
const pair=(sequence=4)=>({sourceIdentity:liveFrameIdentity('source',session,sequence),detectionIdentity:liveFrameIdentity('detection',session,sequence),eyewearModel:'amber-horizon'});
test('live identities are explicit, collision-free by field boundary, and kind/session/sequence paired',()=>{
  const p=pair();assert.equal(isContentHash(p.sourceIdentity),false);assert.equal(isPairedIdentity(p.sourceIdentity,p.detectionIdentity),true);
  assert.deepEqual(identityMetadata(p),{identityKind:'session-frame'});
  assert.equal(isPairedIdentity(p.sourceIdentity,pair(5).detectionIdentity),false);
  assert.equal(isPairedIdentity(p.sourceIdentity,liveFrameIdentity('detection','camera-session-2',4)),false);
  assert.equal(isPairedIdentity(p.detectionIdentity,p.sourceIdentity),false);
  assert.equal(isPairedIdentity(p.sourceIdentity,'a'.repeat(64)),false);
  assert.equal(isSourceIdentity(p.sourceIdentity,5),false);
  for(const value of ['live:source:camera-session-1:04','live:source:camera-session-1:-1','live:source:a:b:4','live:source:camera-session-1:9007199254740992'])assert.equal(parseLiveIdentity(value),null);
  assert.throws(()=>liveFrameIdentity('source','camera:session',4));
});
test('held upgrade returns genuine exact content hashes together without modifying live identity',async()=>{
  const p=pair(),pixels=new Uint8ClampedArray([10,20,30,255]),detection={matrix:null,landmarks:[]};
  const result=await heldContentPair(p,pixels,detection);
  assert.equal(p.sourceIdentity,pair().sourceIdentity);assert.equal(result.identityKind,'sha256');
  assert.equal(result.sourceIdentity,await contentHash(pixels));assert.equal(result.sourceSHA256,result.sourceIdentity);
  assert.equal(result.detectionIdentity,await contentHash(new TextEncoder().encode(JSON.stringify(detection))));
  assert.equal(result.detectionSHA256,result.detectionIdentity);assert.deepEqual(identityMetadata(result),{identityKind:'sha256',sourceSHA256:result.sourceSHA256,detectionSHA256:result.detectionSHA256});
  await assert.rejects(heldContentPair(result,new Uint8ClampedArray([0,20,30,255]),detection),/do not match/);
  await assert.rejects(heldContentPair(result,pixels,{...detection,changed:true}),/do not match/);
  await assert.rejects(heldContentPair({...p,detectionIdentity:pair(5).detectionIdentity},pixels,detection),/Invalid held/);
});
test('owned live source detaches caller storage and rejects wrong session/generation/cancelled borrows',t=>{
  class ImageDataFake {
    data:Uint8ClampedArray<ArrayBuffer>;width:number;height:number;colorSpace:PredefinedColorSpace;
    constructor(data:Uint8ClampedArray<ArrayBuffer>,width:number,height:number,options:ImageDataSettings={}) {
      this.data=data;this.width=width;this.height=height;this.colorSpace=options.colorSpace??'srgb';
    }
  }
  const prior=Object.getOwnPropertyDescriptor(globalThis,'ImageData');
  Object.defineProperty(globalThis,'ImageData',{value:ImageDataFake,configurable:true});
  t.after(()=>{if(prior)Object.defineProperty(globalThis,'ImageData',prior);else Reflect.deleteProperty(globalThis,'ImageData');});
  const canvas={width:1,height:1} as HTMLCanvasElement,bytes=new Uint8ClampedArray([1,2,3,255]);let alive=true;
  const rgba=new ImageData(bytes,1,1,{colorSpace:'srgb'}),id={sourceIdentity:pair().sourceIdentity,sessionId:session,generation:4,isCurrent:()=>alive};
  const source=createOwnedSourceFrame(canvas,rgba,id);assert.equal(bytes.byteLength,0);assert.deepEqual([...source.rgba.data],[1,2,3,255]);
  assertSourceFrameCurrent(source,canvas,pair().sourceIdentity);
  assert.throws(()=>assertSourceFrameCurrent(source,canvas,pair(5).sourceIdentity),/another paired image/);
  assert.throws(()=>createOwnedSourceFrame(canvas,new ImageData(new Uint8ClampedArray([1,2,3,255]),1,1),{...id,sessionId:'another-session'}),/identity/);
  assert.throws(()=>createOwnedSourceFrame(canvas,new ImageData(new Uint8ClampedArray([1,2,3,255]),1,1),{...id,generation:5}),/identity/);
  alive=false;assert.throws(()=>assertSourceFrameCurrent(source),{name:'AbortError'});
});
class WorkerFake implements HairWorkerPort {
  onmessage:HairWorkerPort['onmessage']=null;onerror:HairWorkerPort['onerror']=null;onmessageerror:HairWorkerPort['onmessageerror']=null;
  messages:HairWorkerRequest[]=[];terminated=0;
  postMessage(message:HairWorkerRequest){this.messages.push(message);}terminate(){this.terminated++;}
  emit(data:unknown){this.onmessage?.({data} as MessageEvent<unknown>);}
  ready(){const m=this.messages.at(-1)!;assert.equal(m.type,'initialize');const model=HAIR_MODELS[m.modelId];this.emit({...m,type:'ready',model:model.id,modelSHA256:model.sha256,labels:[...model.labels],hairIndex:model.hairIndex,runningMode:'IMAGE',delegate:'CPU',initializationMs:1});}
  result(patch:Record<string,unknown>={},envelope:Record<string,unknown>={}){const m=this.messages.at(-1)!;assert.equal(m.type,'segment');const init=this.messages.find(value=>value.type==='initialize')!;assert.equal(init.type,'initialize');const model=HAIR_MODELS['hair-only'];this.emit({type:'result',sessionNonce:m.sessionNonce,requestId:m.requestId,output:{sourceIdentity:m.sourceIdentity,sequence:m.sequence,model:model.id,modelSHA256:model.sha256,labels:[...model.labels],hairIndex:model.hairIndex,width:1,height:1,category:new Uint8Array([1]),...(init.outputMode==='full'?{outputMode:'full',confidence:new Float32Array([.5])}:{outputMode:'category-only'}),inferenceMs:1,extractionMs:1,...patch},...envelope});}
}
async function ready(outputMode:'category-only'|'full'='category-only'){const worker=new WorkerFake(),controller=new AbortController(),client=new HairClient('hair-only',{createWorker:()=>worker,outputMode});const start=client.initialize(controller.signal);worker.ready();await start;return{worker,client,controller};}
function bitmap(){let closed=0;return{image:{width:1,height:1,close(){closed++;}} as ImageBitmap,closed:()=>closed};}
test('token worker retains exact source, sequence, request/nonce envelope and genuine category hash',async()=>{
  const {worker,client}=await ready(),image=bitmap(),work=client.segment(image.image,pair().sourceIdentity,4);
  worker.result({sourceIdentity:pair(5).sourceIdentity},{sessionNonce:'other-session'});worker.result({sequence:9},{requestId:1});worker.result();
  const result=await work;assert.equal(result.sourceIdentity,pair().sourceIdentity);assert.equal(result.categorySHA256,await contentHash(new Uint8Array([1])));assert.equal(image.closed(),1);
  const other=bitmap();await assert.rejects(client.segment(other.image,liveFrameIdentity('source','another-session',5),5),/another camera session/);assert.equal(other.closed(),1);client.close();
});
for(const [name,patch,error] of [
  ['wrong same-envelope source',{sourceIdentity:pair(5).sourceIdentity},/another frame sequence|another image/],
  ['invalid category',{category:new Uint8Array([255])},/invalid category/],
  ['wrong model hash',{modelSHA256:'0'.repeat(64)},/another model/],
  ['category-only fabricated confidence',{confidenceSHA256:'a'.repeat(64)},/fabricated confidence/],
] as const)test(name+' remains rejected for token mode',async()=>{const {worker,client}=await ready(),image=bitmap();const work=client.segment(image.image,pair().sourceIdentity,4),rejected=assert.rejects(work,error);worker.result(patch);await rejected;assert.equal(image.closed(),1);assert.equal(worker.terminated,1);client.close();});
test('abort during mask hashing and a late result cannot publish into stopped/restarted session',async()=>{
  const {worker,client,controller}=await ready(),image=bitmap(),work=client.segment(image.image,pair().sourceIdentity,4);
  const rejected=assert.rejects(work,{name:'AbortError'});worker.result();controller.abort();await rejected;worker.result();assert.equal(image.closed(),1);assert.equal(worker.terminated,1);
  const fresh=await ready(),next=bitmap(),pending=fresh.client.segment(next.image,liveFrameIdentity('source','fresh-session',0),0);fresh.worker.result();assert.equal((await pending).sequence,0);fresh.client.close();client.close();
});
test('full held masks retain actual confidence hashes and finite-range validation',async()=>{
  const {worker,client}=await ready('full'),image=bitmap();const pending=client.segment(image.image,pair().sourceIdentity,4);worker.result();
  const result=await pending;assert.ok(result.outputMode!=='category-only');assert.deepEqual([...result.confidence],[.5]);
  assert.equal(result.confidenceSHA256,await contentHash(new Uint8Array([0,0,0,63])));assert.equal(result.categorySHA256,await contentHash(new Uint8Array([1])));
  const invalid=bitmap(),bad=client.segment(invalid.image,pair(5).sourceIdentity,5),rejected=assert.rejects(bad,/nonfinite or outside/);
  worker.result({confidence:new Float32Array([NaN])});await rejected;assert.equal(invalid.closed(),1);client.close();
});
test('timing samples and benchmark exports remove source/detection identities and token values under renamed keys',()=>{
  const profiler=new FrameProfiler();profiler.begin(session,'hair',0,['combined']);
  const source=pair().sourceIdentity,detection=pair().detectionIdentity;
  const input:FrameInput={sessionId:session,sequence:4,pipeline:'combined',variant:'hair',capturedAtMs:80,publishedAtMs:100,
    videoPresentedFrames:12,videoMediaTime:.1,videoPresentationTimeMs:null,cameraSettingFps:30,
    sourceWidth:640,sourceHeight:427,sourceDrawMs:1,detectorDrawMs:1,sourceReadbackMs:1,sourceHashMs:0,
    faceBitmapMs:1,faceRequestWallMs:8,faceInferenceMs:6,faceWorkerMs:7,faceExtractionMs:.5,
    faceWorkerValidationMs:.5,faceClientValidationMs:.1,faceTransportSchedulingMs:1,prerequisitesWaitMs:8,
    detectionHashMs:0,prepareMs:5,finishMs:3,renderMs:8,totalMs:20,schedulerWaitMs:10,hairWaitMs:0,
    hairInferenceMs:9,hairExtractionMs:1,hasFace:true,hasMask:true,maskMode:'category-only',fallback:null,changedPixels:3,
    faceDelegate:'GPU',hairDelegate:'GPU',gpuRenderer:'test',cleanCameraMs:1,composeMs:1,continuityMs:.5,
    finalChecksMs:.5,publishMs:1,native:{sourceIdentity:source,detectionIdentity:detection,
      'speedLab.sourceIdentity.sessionId':'private-owner','geometry.detectionIdentity.value':detection,
      sourceSHA256:'a'.repeat(64),detectionSHA256:'b'.repeat(64),unexpectedSource:source,
      renamedStatus:'request '+detection+' completed','native.pbo.retrievedCalls':2,'fps.identityMode':'session-frame'}};
  for(const at of [100,1000,5100,6000])profiler.add({...input,capturedAtMs:at-20,publishedAtMs:at});
  const safe={'native.pbo.retrievedCalls':2,'fps.identityMode':'session-frame'};
  assert.deepEqual(profiler.samplesAfter(0)[0]?.native,safe);
  const exported=profiler.exportJSON();
  assert.doesNotMatch(exported,/live:(source|detection):|private-owner|sourceIdentity|detectionIdentity|sourceSHA256|detectionSHA256/);
  const report=JSON.parse(exported) as {benchmark:{segments:{samples:{native:unknown}[]}[]}};
  assert.deepEqual(report.benchmark.segments[0]?.samples[0]?.native,safe);
});
