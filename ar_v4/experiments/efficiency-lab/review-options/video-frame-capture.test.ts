import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ExactVideoFrameCapture} from './video-frame-capture.ts';
import type {OwnedVideoFrame,CaptureDependencies} from './video-frame-capture.ts';

const opaque = (width:number,height:number,color:number):Uint8ClampedArray<ArrayBuffer>=>{
  const bytes=new Uint8ClampedArray(width*height*4);
  for(let i=0;i<bytes.length;i+=4){bytes[i]=color;bytes[i+1]=17;bytes[i+2]=241;bytes[i+3]=255;}return bytes;
};
function deferred<T>() {let resolve!:(value:T)=>void;let reject!:(error:Error)=>void;
  const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};}
function fixture(width=720,height=1280) {
  const video={color:23} as unknown as HTMLVideoElement;
  const canvas={width,height} as HTMLCanvasElement;
  let pixels=opaque(width,height,0), reads=0,writes=0;const draws:unknown[]=[];
  const context={drawImage(source:unknown){draws.push(source);pixels=opaque(width,height,(source as {color:number}).color);},
    getImageData(){reads++;return{data:new Uint8ClampedArray(pixels),width,height} as ImageData;},
    putImageData(image:ImageData){writes++;pixels=new Uint8ClampedArray(image.data);}} as unknown as CanvasRenderingContext2D;
  const deps:CaptureDependencies={createImageData:(w,h)=>({width:w,height:h,data:new Uint8ClampedArray(w*h*4),colorSpace:'srgb'} as ImageData)};
  const frames:(OwnedVideoFrame&{color:number;closes:number})[]=[];
  const build=(copy:(dest:Uint8ClampedArray<ArrayBuffer>,options:VideoFrameCopyToOptions)=>Promise<PlaneLayout[]>)=>{
    const frame={displayWidth:width,displayHeight:height,visibleRect:{x:0,y:0,width,height},rotation:0,flip:false,
      color:(video as unknown as {color:number}).color,closes:0,copyTo:copy,close(){this.closes++;}};
    frames.push(frame);return frame;
  };
  return{video,canvas,context,deps,frames,build,get pixels(){return pixels;},get reads(){return reads;},get writes(){return writes;},draws};
}
test('720x1280 RGBA comes from one synchronous frozen image; copy and canvas bytes agree without readback', async()=>{
  const f=fixture(),gate=deferred<PlaneLayout[]>();let copied!:Uint8ClampedArray<ArrayBuffer>;
  const capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>f.build((data,options)=>{
    assert.deepEqual(options,{format:'RGBA',colorSpace:'srgb',rect:{x:0,y:0,width:720,height:1280},layout:[{offset:0,stride:2880}]});
    copied=data;data.set(opaque(720,1280,23));return gate.promise;})});
  const result=capture.capture(f.video,f.canvas,f.context,123,()=>true)!;
  assert.equal(f.frames.length,1);assert.equal(capture.busy,true);assert.equal(f.writes,0);
  (f.video as unknown as {color:number}).color=99;
  assert.equal(capture.capture(f.video,f.canvas,f.context,124,()=>true),null);assert.equal(f.frames.length,1);
  gate.resolve([{offset:0,stride:2880}]);const timing=await result.ready;
  assert.equal(timing.actualPath,'video-frame-copy');assert.equal(timing.fallbackReason,null);
  assert.equal(timing.copiedBytes,720*1280*4);assert.equal(result.rgba.data,copied);
  assert.deepEqual(f.pixels,opaque(720,1280,23));assert.equal(f.reads,0);assert.equal(f.writes,1);
  assert.equal(f.frames[0]!.closes,1);assert.equal(capture.busy,false);
});
test('unsupported API fallback snapshots current video synchronously and reports the actual path',async()=>{
  const f=fixture(3,2),capture=new ExactVideoFrameCapture({...f.deps,createFrame:null});
  const result=capture.capture(f.video,f.canvas,f.context,1,()=>true)!;
  (f.video as unknown as {color:number}).color=99;
  const telemetry=await result.ready;
  assert.equal(telemetry.actualPath,'canvas-video-fallback');assert.equal(telemetry.fallbackReason,'video-frame-unavailable');
  assert.equal(result.rgba.data[0],23);assert.equal(f.draws[0],f.video);assert.equal(f.reads,1);assert.equal(f.frames.length,0);
});
test('async copy failure falls back to its frozen frame even after live video advances',async()=>{
  const f=fixture(3,2),gate=deferred<PlaneLayout[]>(),capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>f.build(()=>gate.promise)});
  const result=capture.capture(f.video,f.canvas,f.context,1,()=>true)!;
  (f.video as unknown as {color:number}).color=99;gate.reject(new Error('RGBA unsupported'));
  const telemetry=await result.ready;
  assert.equal(telemetry.actualPath,'canvas-video-frame-fallback');assert.equal(telemetry.fallbackReason,'rgba-copy-failed');
  assert.equal(f.draws[0],f.frames[0]);assert.equal(result.rgba.data[0],23);assert.deepEqual(result.rgba.data,f.pixels);
  assert.equal(f.frames[0]!.closes,1);
});
test('stop/switch discards late copies, closes VideoFrames, and never writes to a revoked canvas',async()=>{
  for(const stop of [true,false]){
    const f=fixture(3,2),gate=deferred<PlaneLayout[]>();let current=true;
    const capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>f.build(data=>{data.set(opaque(3,2,23));return gate.promise;})});
    const result=capture.capture(f.video,f.canvas,f.context,1,()=>current)!;
    const rejected=assert.rejects(result.ready,{name:'AbortError'});
    if(stop)capture.stop();else current=false;
    gate.resolve([{offset:0,stride:12}]);await rejected;await capture.whenIdle();
    assert.equal(f.writes,0);assert.equal(f.reads,0);assert.equal(f.draws.length,0);assert.equal(f.frames[0]!.closes,1);
    assert.equal(capture.stats.revoked,1);assert.equal(capture.busy,false);
  }
});
test('wrong layout, alpha, dimensions, orientation and missing conversion report explicit frozen fallbacks',async()=>{
  for(const kind of ['layout','alpha','dimensions','rotation','flip','missing'] as const){
    const f=fixture(3,2),capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>{
      const frame=f.build(async data=>{data.set(opaque(3,2,23));if(kind==='alpha')data[3]=0;
        return kind==='layout'?[{offset:0,stride:3},{offset:6,stride:3}]:[{offset:0,stride:12}];});
      if(kind==='dimensions')frame.displayWidth=2;if(kind==='rotation')frame.rotation=90;if(kind==='flip')frame.flip=true;
      if(kind==='missing')Reflect.deleteProperty(frame,'copyTo');return frame;
    }});
    const result=capture.capture(f.video,f.canvas,f.context,1,()=>true)!,telemetry=await result.ready;
    assert.equal(telemetry.actualPath,'canvas-video-frame-fallback');assert.equal(f.frames[0]!.closes,1);
    assert.equal(f.reads,1);assert.equal(f.writes,0);assert.deepEqual(result.rgba.data,f.pixels);
    const reason={layout:'rgba-layout-unavailable',alpha:'rgba-alpha-unavailable',dimensions:'dimensions-or-transform',
      rotation:'dimensions-or-transform',flip:'dimensions-or-transform',missing:'rgba-copy-unavailable'}[kind];
    assert.equal(telemetry.fallbackReason,reason);
  }
});
test('fallback render failure releases its frame and lock; new capture can recover',async()=>{
  const f=fixture(3,2),capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>f.build(async()=>{throw new Error('no copy');})});
  const broken={...f.context,drawImage(){throw new Error('canvas failed');}} as CanvasRenderingContext2D;
  const result=capture.capture(f.video,f.canvas,broken,1,()=>true)!;
  await assert.rejects(result.ready,/canvas failed/);await capture.whenIdle();assert.equal(f.frames[0]!.closes,1);
  const retry=capture.capture(f.video,f.canvas,f.context,2,()=>true)!;await retry.ready;
  assert.equal(f.frames[1]!.closes,1);assert.equal(capture.stats.failed,1);
});
