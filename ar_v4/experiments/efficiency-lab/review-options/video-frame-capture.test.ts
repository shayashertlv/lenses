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
  const video={color:23,videoWidth:width,videoHeight:height} as unknown as HTMLVideoElement;
  const canvas={width,height} as HTMLCanvasElement;
  let pixels=opaque(width,height,0), reads=0,writes=0;const draws:unknown[]=[];
  const context={drawImage(source:unknown){draws.push(source);pixels=opaque(width,height,(source as {color:number}).color);},
    getImageData(){reads++;return{data:new Uint8ClampedArray(pixels),width,height} as ImageData;},
    putImageData(image:ImageData){writes++;pixels=new Uint8ClampedArray(image.data);}} as unknown as CanvasRenderingContext2D;
  const deps:CaptureDependencies={createImageData:(w,h)=>({width:w,height:h,data:new Uint8ClampedArray(w*h*4),colorSpace:'srgb'} as ImageData)};
  const frames:(OwnedVideoFrame&{color:number;closes:number})[]=[];
  const build=(copy:(dest:Uint8ClampedArray<ArrayBuffer>,options:VideoFrameCopyToOptions)=>Promise<PlaneLayout[]>)=>{
    const frame={codedWidth:width,codedHeight:height,displayWidth:width,displayHeight:height,visibleRect:{x:0,y:0,width,height},rotation:0,flip:false,
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
  assert.equal(timing.orientationMetadataAvailable,true);assert.equal(timing.frameRotation,0);assert.equal(timing.frameFlip,false);
  assert.equal(timing.videoWidth,720);assert.equal(timing.videoHeight,1280);
  assert.equal(timing.frameCodedWidth,720);assert.equal(timing.frameCodedHeight,1280);
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
test('wrong layout, alpha and missing conversion retain only a verified untransformed frozen frame',async()=>{
  for(const kind of ['layout','alpha','missing'] as const){
    const f=fixture(3,2),capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>{
      const frame=f.build(async data=>{data.set(opaque(3,2,23));if(kind==='alpha')data[3]=0;
        return kind==='layout'?[{offset:0,stride:3},{offset:6,stride:3}]:[{offset:0,stride:12}];});
      if(kind==='missing')Reflect.deleteProperty(frame,'copyTo');return frame;
    }});
    const result=capture.capture(f.video,f.canvas,f.context,1,()=>true)!,telemetry=await result.ready;
    assert.equal(telemetry.actualPath,'canvas-video-frame-fallback');assert.equal(f.frames[0]!.closes,1);
    assert.equal(f.reads,1);assert.equal(f.writes,0);assert.deepEqual(result.rgba.data,f.pixels);
    const reason={layout:'rgba-layout-unavailable',alpha:'rgba-alpha-unavailable',missing:'rgba-copy-unavailable'}[kind];
    assert.equal(telemetry.fallbackReason,reason);
  }
});
test('missing or invalid orientation snapshots upright video before returning, never the unsafe frozen frame',async()=>{
  const cases:Record<string,{rotation?:unknown;flip?:unknown}>={
    bothMissing:{},rotationMissing:{flip:false},flipMissing:{rotation:0},
    nonfiniteRotation:{rotation:NaN,flip:false},infiniteRotation:{rotation:Infinity,flip:false},
    fractionalRotation:{rotation:0.5,flip:false},unsupportedRotation:{rotation:360,flip:false},
    negativeRotation:{rotation:-90,flip:false},stringRotation:{rotation:'0',flip:false},
    stringFlip:{rotation:0,flip:'false'},numericFlip:{rotation:0,flip:0},nullFlip:{rotation:0,flip:null},
  };
  for(const [label,metadata] of Object.entries(cases)){
    const f=fixture(3,2);let copies=0;
    const capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>{
      const frame=f.build(async()=>{copies++;return [{offset:0,stride:12}];});
      frame.color=147; // Native orientation can disagree even when dimensions match.
      Reflect.deleteProperty(frame,'rotation');Reflect.deleteProperty(frame,'flip');Object.assign(frame,metadata);return frame;
    }});
    const result=capture.capture(f.video,f.canvas,f.context,1,()=>true)!;
    assert.deepEqual(result.rgba.data,opaque(3,2,23),label);assert.equal(f.reads,1,label);
    assert.equal(copies,0,label);assert.deepEqual(f.draws,[f.video],label);assert.equal(f.frames[0]!.closes,1,label);
    (f.video as unknown as {color:number}).color=99;
    const timing=await result.ready;
    assert.equal(timing.actualPath,'canvas-video-fallback',label);assert.equal(timing.fallbackReason,'orientation-metadata-unavailable',label);
    assert.equal(timing.orientationMetadataAvailable,false,label);assert.deepEqual(result.rgba.data,opaque(3,2,23),label);
    assert.equal(capture.busy,false,label);capture.stop();await capture.whenIdle();assert.equal(f.frames[0]!.closes,1,label);
  }
});
test('known rotation, flip and incompatible dimensions use the original synchronous video snapshot',async()=>{
  for(const kind of ['rotation90','rotation180','rotation270','flip','display','visible','swapped','noRect'] as const){
    const f=fixture(3,2);let copies=0;
    const capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>{
      const frame=f.build(async()=>{copies++;return [{offset:0,stride:12}];});frame.color=147;
      if(kind.startsWith('rotation'))frame.rotation=Number(kind.slice(8));
      if(kind==='flip')frame.flip=true;
      if(kind==='display')frame.displayWidth=2;
      if(kind==='visible')frame.visibleRect.width=2;
      if(kind==='swapped'){frame.visibleRect.width=2;frame.visibleRect.height=3;frame.codedWidth=2;frame.codedHeight=3;}
      if(kind==='noRect')Object.assign(frame,{visibleRect:null});return frame;
    }});
    const result=capture.capture(f.video,f.canvas,f.context,1,()=>true)!;
    assert.equal(copies,0,kind);assert.deepEqual(f.draws,[f.video],kind);assert.equal(f.frames[0]!.closes,1,kind);
    assert.deepEqual(result.rgba.data,opaque(3,2,23),kind);(f.video as unknown as {color:number}).color=99;
    const timing=await result.ready;
    assert.equal(timing.actualPath,'canvas-video-fallback',kind);assert.equal(timing.fallbackReason,'dimensions-or-transform',kind);
    assert.equal(timing.orientationMetadataAvailable,true,kind);assert.equal(timing.frameRotation,kind.startsWith('rotation')?Number(kind.slice(8)):0,kind);
    assert.deepEqual(result.rgba.data,opaque(3,2,23),kind);assert.equal(f.writes,0,kind);
  }
});
test('synchronous orientation fallback closes once on cancellation or draw failure and permits recovery',async()=>{
  for(const failure of ['revocation','draw'] as const){
    const f=fixture(3,2);let current=true,first=true;
    const capture=new ExactVideoFrameCapture({...f.deps,createFrame:()=>{
      const frame=f.build(async()=>{throw new Error('Unverified orientation must not copy.');});Reflect.deleteProperty(frame,'rotation');
      if(first&&failure==='revocation')current=false;return frame;
    }});
    const broken={...f.context,drawImage(){throw new Error('canvas failed');}} as CanvasRenderingContext2D;
    assert.throws(()=>capture.capture(f.video,f.canvas,failure==='draw'?broken:f.context,1,()=>current),
      failure==='draw'?/canvas failed/:{name:'AbortError'});
    assert.equal(f.frames[0]!.closes,1);assert.equal(capture.busy,false);await capture.whenIdle();
    first=false;current=true;const retry=capture.capture(f.video,f.canvas,f.context,2,()=>current)!;
    assert.equal((await retry.ready).actualPath,'canvas-video-fallback');assert.equal(f.frames[1]!.closes,1);
    assert.equal(capture.stats.framesClosed,2);assert.deepEqual(retry.rgba.data,opaque(3,2,23));
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
