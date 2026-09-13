import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {test} from 'node:test';
import {runExperimentPump} from '../live-pump.ts';
import type {PumpContext} from '../live-pump.ts';
import type {FrameInput} from '../frame-profiler.ts';
import type {OwnedSourceFrame} from '../speed-options.ts';
import type {HairMask} from '../comparison-renderer.ts';

function gate<T>() {let resolve!:(value:T)=>void;const promise=new Promise<T>(yes=>{resolve=yes;});return{promise,resolve};}
const flush=()=>new Promise<void>(resolve=>setImmediate(resolve));
const sha=(bytes:Uint8ClampedArray<ArrayBuffer>)=>createHash('sha256').update(bytes).digest('hex');
function fixture() {
  const previous=new Map<string,PropertyDescriptor|undefined>();
  const replace=(name:string,value:unknown)=>{previous.set(name,Object.getOwnPropertyDescriptor(globalThis,name));
    Object.defineProperty(globalThis,name,{value,configurable:true,writable:true});};
  const canvases:HTMLCanvasElement[]=[],captureCanvases:HTMLCanvasElement[]=[],stored=new WeakMap<HTMLCanvasElement,number>();
  let color=0,callback:VideoFrameRequestCallback|undefined,nextCallback=0,callbackId=0,clock=1000,maxCaptureCanvases=0;
  class Pixels {
    width:number;height:number;data:Uint8ClampedArray<ArrayBuffer>;colorSpace='srgb';
    constructor(dataOrWidth:Uint8ClampedArray<ArrayBuffer>|number,widthOrHeight:number,height?:number) {
      this.width=typeof dataOrWidth==='number'?dataOrWidth:widthOrHeight;
      this.height=typeof dataOrWidth==='number'?widthOrHeight:height!;
      this.data=typeof dataOrWidth==='number'?new Uint8ClampedArray(this.width*this.height*4):dataOrWidth;
    }
  }
  const bytes=(width:number,height:number,pixel:number)=>{
    const data=new Uint8ClampedArray(width*height*4);for(let offset=0;offset<data.length;offset+=4){data[offset]=pixel;data[offset+3]=255;}return data;};
  const pixels=(canvas:HTMLCanvasElement)=>bytes(canvas.width,canvas.height,stored.get(canvas)??0);
  replace('ImageData',Pixels);replace('MediaStream',class{});replace('performance',{now:()=>++clock});
  replace('document',{createElement:()=>{
    const canvas={width:300,height:150,getContext:()=>({clearRect(){},
      drawImage(source:HTMLCanvasElement){stored.set(canvas,stored.get(source)??0);},
      putImageData(image:ImageData){stored.set(canvas,image.data[0]!);},
      getImageData(){return new Pixels(pixels(canvas),canvas.width,canvas.height);}})} as unknown as HTMLCanvasElement;
    canvases.push(canvas);return canvas;
  }});
  type Copy={pixel:number;canvas:HTMLCanvasElement;closes:number;complete():void};
  const copies:Copy[]=[];
  class FrozenFrame {
    displayWidth=80;displayHeight=40;visibleRect={x:0,y:0,width:80,height:40};rotation=0;flip=false;
    private readonly completion=gate<PlaneLayout[]>();private readonly request:Copy;
    constructor() {
      const pixel=color,canvas=canvases.at(-1)!;
      captureCanvases.push(canvas);maxCaptureCanvases=Math.max(maxCaptureCanvases,captureCanvases.filter(item=>item.width>0).length);
      this.request={pixel,canvas,closes:0,complete:()=>this.completion.resolve([{offset:0,stride:320}])};copies.push(this.request);
    }
    async copyTo(destination:Uint8ClampedArray<ArrayBuffer>):Promise<PlaneLayout[]> {
      const layout=await this.completion.promise;destination.set(bytes(80,40,this.request.pixel));return layout;
    }
    close(){this.request.closes++;}
  }
  replace('VideoFrame',FrozenFrame);
  type Snapshot=ImageBitmap&{pixels:Uint8ClampedArray<ArrayBuffer>};
  replace('createImageBitmap',async(canvas:HTMLCanvasElement)=>({width:canvas.width,height:canvas.height,pixels:pixels(canvas),close(){}}));
  const video={srcObject:null,readyState:2,currentTime:0,videoWidth:80,videoHeight:40,
    requestVideoFrameCallback:(next:VideoFrameRequestCallback)=>{callback=next;callbackId=++nextCallback;return callbackId;},
    cancelVideoFrameCallback:(id:number)=>{if(id===callbackId)callback=undefined;}} as unknown as HTMLVideoElement;
  let presentedFrames=0;
  const offer=(pixel:number)=>{color=pixel;const next=callback;callback=undefined;assert.ok(next,'An active session schedules its next observation.');
    next(++clock,{mediaTime:0,presentedFrames:++presentedFrames,presentationTime:clock} as VideoFrameCallbackMetadata);};
  const restore=()=>{for(const[name,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,name,descriptor);else Reflect.deleteProperty(globalThis,name);}};
  const sessions:{errors:unknown[]}[]=[];
  const blockedFirstDetection=gate<void>();
  function session(id:string) {
    const rows:FrameInput[]=[],errors:unknown[]=[],detections:number[]=[],sources:OwnedSourceFrame[]=[];
    const hair:{pixel:number;sequence:number;complete():void}[]=[];let sequence=0;
    const renderer={pipeline:'frame-copy',variant:'hair',stats:{hasMask:false,fallbackReason:null,changedPixels:0,
      timings:{cleanCameraMs:0,composeMs:0,continuityMs:0,finalChecksMs:0,publishMs:0}},selectPipeline(){},selectVariant(){},
      async prepare(canvas:HTMLCanvasElement,detection:{landmarks:number[]},pair:{sourceSHA256:string;detectionSHA256:string},
        _model:unknown,_hair:boolean,source:OwnedSourceFrame) {
        assert.equal(source.sessionId,id);assert.equal(source.canvas,canvas);assert.equal(source.isCurrent(),true);
        assert.equal(source.rgba.data[0],detection.landmarks[0]);assert.deepEqual(source.rgba.data,pixels(canvas));
        assert.equal(source.sourceSHA256,sha(source.rgba.data));assert.equal(pair.sourceSHA256,source.sourceSHA256);
        assert.equal(pair.detectionSHA256,createHash('sha256').update(JSON.stringify(detection)).digest('hex'));
        sources.push(source);return true;
      },finish(mask:HairMask|null) {
        const source=sources.at(-1)!;
        if(mask){assert.equal(mask.sourceSHA256,source.sourceSHA256);assert.ok('sequence' in mask);assert.equal(mask.sequence,source.generation);assert.equal(mask.category[0],source.rgba.data[0]);}
        renderer.stats.hasMask=!!mask;return true;
      }};
    const pump=runExperimentPump({id,generation:1,video,pipeline:'frame-copy',mode:'overlap',owns:()=>true,
      nextSequence:()=>++sequence,hairReady:()=>true,variant:()=> 'hair',onBusy(){},renderer,
      detector:{async detect(bitmap:Snapshot){const pixel=bitmap.pixels[0]!;detections.push(pixel);if(pixel===11)await blockedFirstDetection.promise;
        bitmap.close();return{landmarks:[pixel]};},lastTiming:null,delegate:'CPU'},
      hair:()=>({segment(bitmap:Snapshot,sourceSHA256:string,seq:number){
        assert.equal(sourceSHA256,sha(bitmap.pixels));const pixel=bitmap.pixels[0]!,done=gate<void>();
        hair.push({pixel,sequence:seq,complete:()=>done.resolve()});
        return done.promise.then(()=>{bitmap.close();return{sequence:seq,sourceSHA256,category:new Uint8Array([pixel]),inferenceMs:1,extractionMs:1};});
      }}),hairId:'hair-only',eyewearId:'amber-horizon',backend:()=>({active:'CPU',renderer:null}),
      onError:(error:unknown)=>errors.push(error),onHairError:(error:unknown)=>errors.push(error),
      onPublished:(row:FrameInput)=>{rows.push(row);assert.equal(row.native!['capture.actualPath'],'video-frame-copy');
        assert.ok(Number(row.native!['input.capturePool.maxLeased'])<=2);},
    } as unknown as PumpContext);
    const stats=()=>pump.stats() as {capture:{ownedImages:number;maxOwnedImages:number;busy:boolean};maxOwnedFrames:number;replaced:number};
    const result={pump,rows,errors,detections,sources,hair,stats};sessions.push(result);return result;
  }
  const wait=async(predicate:()=>boolean)=>{
    for(let count=0;count<1000;count++){
      for(const session of sessions)assert.deepEqual(session.errors,[]);
      if(predicate())return;await new Promise<void>(resolve=>setTimeout(resolve,1));
    }throw new Error('Controlled pump condition did not settle.');
  };
  return{offer,restore,canvases,captureCanvases,copies,session,wait,blockedFirstDetection,
    pendingCallback:()=>callback,maxCaptureCanvases:()=>maxCaptureCanvases};
}

test('frame-copy pump replaces settled pending images, waits for late hair leases, and restarts without a third canvas',{timeout:15_000},async()=>{
  const f=fixture(),first=f.session('first');let second:ReturnType<typeof f.session>|undefined;
  try {
    f.offer(11);f.offer(12);assert.equal(f.copies.length,1,'Slow native copy rejects a second capture before allocation.');
    f.copies[0]!.complete();await f.wait(()=>first.detections.length===1&&first.hair.length===1);
    f.offer(22);f.copies[1]!.complete();await flush();await flush();
    const replaced=f.copies[1]!.canvas;
    f.offer(33);assert.equal(replaced.width,0,'A settled unprocessed replacement must release synchronously before allocation.');
    assert.equal(f.copies.length,3);assert.equal(first.stats().replaced,1);assert.equal(f.maxCaptureCanvases(),2);
    f.copies[2]!.complete();f.blockedFirstDetection.resolve();
    await f.wait(()=>first.rows.length===1&&first.detections.length===2);
    assert.deepEqual(first.detections,[11,33]);assert.equal(first.rows[0]!.hasMask,false);
    assert.equal(first.stats().capture.ownedImages,2,'Published image retains its late hair lease alongside the active image.');
    f.offer(44);assert.equal(f.copies.length,3,'Late hair backpressure suppresses a third live canvas.');
    first.hair[0]!.complete();await f.wait(()=>first.hair.length===2);first.hair[1]!.complete();
    await f.wait(()=>first.rows.length===2&&first.stats().capture.ownedImages===0);
    assert.equal(first.rows[1]!.hasMask,true);assert.deepEqual(first.sources.map(source=>source.rgba.data[0]),[11,33]);

    f.offer(55);const oldCallback=f.pendingCallback()!;first.pump.stop();
    second=f.session('second');f.offer(66);assert.equal(f.copies.length,5);
    oldCallback(9999,{mediaTime:0,presentedFrames:999,presentationTime:9999} as VideoFrameCallbackMetadata);
    assert.equal(f.copies.length,5,'A stopped callback cannot admit an image into the restarted session.');
    f.copies[3]!.complete();await first.pump.finishCurrent();
    assert.equal(first.rows.length,2);assert.equal(first.detections.length,2,'Revoked copy never reaches inference.');
    f.copies[4]!.complete();await f.wait(()=>second!.hair.length===1);second.hair[0]!.complete();
    await f.wait(()=>second!.rows.length===1&&second!.stats().capture.ownedImages===0);
    f.offer(77);f.copies[5]!.complete();await f.wait(()=>second!.hair.length===2);second.hair[1]!.complete();
    await f.wait(()=>second!.rows.length===2);await second.pump.finishCurrent();
    assert.deepEqual(second.detections,[66,77]);assert.ok(second.rows.every(row=>row.hasMask));
    assert.equal(first.stats().capture.ownedImages,0);assert.equal(second.stats().capture.ownedImages,0);
    assert.ok(first.stats().capture.maxOwnedImages<=2);assert.ok(second.stats().capture.maxOwnedImages<=2);
    assert.ok(first.stats().maxOwnedFrames<=2);assert.ok(second.stats().maxOwnedFrames<=2);
    assert.equal(f.maxCaptureCanvases(),2);assert.ok(f.canvases.every(canvas=>canvas.width===0));
    assert.ok(f.copies.every(copy=>copy.closes===1));
  }finally{
    f.blockedFirstDetection.resolve();for(const copy of f.copies)copy.complete();
    for(const session of [first,second])if(session){for(const request of session.hair)request.complete();session.pump.stop();await session.pump.finishCurrent();}
    f.restore();
  }
});
