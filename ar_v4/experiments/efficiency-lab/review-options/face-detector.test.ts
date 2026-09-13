import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ForcedDelegateDetectorClient} from './face-detector.ts';
import type {FaceWorkerPort} from './face-detector.ts';
import type {FaceDelegate,TimedDetectorRequest,TimedDetectorResponse} from '../../performance-stage2/face-timing.ts';

class WorkerStub implements FaceWorkerPort {
  onmessage: FaceWorkerPort['onmessage'] = null;
  onerror: FaceWorkerPort['onerror'] = null;
  onmessageerror: FaceWorkerPort['onmessageerror'] = null;
  terminated = false;
  requests: TimedDetectorRequest[] = [];
  postMessage(message: TimedDetectorRequest): void { this.requests.push(message); }
  terminate(): void { this.terminated = true; }
  get latest(): TimedDetectorRequest { return this.requests.at(-1)!; }
  emit(message: TimedDetectorResponse): void { this.onmessage?.(new MessageEvent('message', {data: message})); }
  ready(): void { this.emit({type:'ready',id:this.latest.id,sessionNonce:this.latest.sessionNonce}); }
}
function fixture(t: {after(fn:()=>void):void}, delegate: FaceDelegate, timeout = 15_000) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window');
  Object.defineProperty(globalThis, 'window', {configurable:true,value:{location:{href:'http://127.0.0.1:8098/'}}});
  const workers: WorkerStub[] = [];
  const client = new ForcedDelegateDetectorClient(delegate, {sessionNonce:'review-face-test',detectTimeoutMs:timeout,
    createWorker:()=>{ const worker = new WorkerStub(); workers.push(worker); return worker; }});
  t.after(()=>{client.close(); if(previous)Object.defineProperty(globalThis,'window',previous);else Reflect.deleteProperty(globalThis,'window');});
  return {client,workers};
}
const bitmap = () => ({width:360,height:640,closes:0,close(){this.closes++;}});

for (const delegate of ['CPU','GPU'] as const) {
  test(`forced ${delegate} is the first and only delegate requested; failures never change it`, async t=>{
    const {client,workers} = fixture(t,delegate);
    const starting = client.initialize(new AbortController().signal), worker = workers[0]!;
    assert.equal(worker.latest.type,'initialize');
    if(worker.latest.type==='initialize') assert.equal(worker.latest.delegate,delegate);
    worker.emit({type:'error',id:worker.latest.id,sessionNonce:worker.latest.sessionNonce,message:'unavailable'});
    await assert.rejects(starting,/unavailable/);
    assert.equal(workers.length,1); assert.equal(worker.terminated,true); assert.equal(client.delegate,null);
  });
}
test('CPU timing and detection retain exact session/image ownership with no concurrent request', async t=>{
  const {client,workers}=fixture(t,'CPU'), starting=client.initialize(new AbortController().signal),worker=workers[0]!;
  worker.ready();await starting;assert.equal(client.delegate,'CPU');
  const image=bitmap(), extra=bitmap(), detecting=client.detect(image as unknown as ImageBitmap,42);
  await assert.rejects(client.detect(extra as unknown as ImageBitmap,43),/already in progress/);assert.equal(extra.closes,1);
  const request=worker.latest;assert.equal(request.type,'detect');
  const detection={landmarks:[],matrix:null,inferenceMs:5};
  const response:TimedDetectorResponse={type:'result',id:request.id,sessionNonce:request.sessionNonce,detection,
    timing:{schema:'face-worker-timing-v1',requestId:request.id,timestampMs:42,delegate:'CPU',width:360,height:640,
      requestChecksMs:1,inferenceMs:5,extractionMs:1,validationMs:1,elapsedMs:8}};
  worker.emit({...response,sessionNonce:'unrelated-session'});assert.equal(client.lastTiming,null);
  worker.emit(response);assert.deepEqual(await detecting,detection);assert.equal(image.closes,1);
  assert.equal(client.lastTiming!.delegate,'CPU');assert.equal(client.lastTiming!.workerTimingStatus,'valid');
  const late=worker.onmessage!;client.close();late(new MessageEvent('message',{data:response}));
  assert.equal(client.lastTiming,null);assert.equal(client.delegate,null);
});
test('CPU cancellation, timeout and stop release all owned images and workers', async t=>{
  const {client,workers}=fixture(t,'CPU',10),starting=client.initialize(new AbortController().signal),worker=workers[0]!;
  worker.ready();await starting;
  const image=bitmap();await assert.rejects(client.detect(image as unknown as ImageBitmap,1),/timed out/);
  assert.equal(image.closes,1);assert.equal(worker.terminated,true);
  const other=fixture(t,'CPU'),abort=new AbortController(),initializing=other.client.initialize(abort.signal);
  abort.abort();await assert.rejects(initializing,{name:'AbortError'});assert.equal(other.workers.length,1);
  assert.equal(other.workers[0]!.terminated,true);
});
