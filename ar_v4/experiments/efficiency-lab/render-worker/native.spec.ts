import {test,expect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';
import type {EyewearId} from '../../../references/perfect-temples/src/render/eyewear.ts';
import type {HairModelId} from '../../hair-live-preview/models.ts';
for(const eyewear of ['amber-horizon','tom-ford-clear'] as const)for(const hair of ['hair-only','selfie-multiclass'] as const) {
  test(`${eyewear}/${hair}: real worker preserves five matched pose controls and held export`,async({page},testInfo)=>{
    await page.goto('/experiments/efficiency-lab/render-worker/native.html');
    const result=await page.evaluate(async({eyewear,hair}:{eyewear:EyewearId;hair:HairModelId})=>{
      const root='/experiments/';
      const {RenderWorkerRenderer}=await import(/* @vite-ignore */ root+'efficiency-lab/render-worker/client.ts') as typeof import('./client.ts');
      const {LiveHairRenderer}=await import(/* @vite-ignore */ root+'speed-lab/renderer.ts') as typeof import('../../speed-lab/renderer.ts');
      const {PROFILES}=await import(/* @vite-ignore */ root+'speed-lab/profiles.ts') as typeof import('../../speed-lab/profiles.ts');
      const {createOwnedSourceFrame}=await import(/* @vite-ignore */ root+'speed-lab/speed-options.ts') as typeof import('../../speed-lab/speed-options.ts');
      const {DetectorClient}=await import(/* @vite-ignore */ root+'performance-stage2/face-detector.ts') as typeof import('../../performance-stage2/face-detector.ts');
      const {HairClient}=await import(/* @vite-ignore */ root+'hair-live-preview/hair-client.ts') as typeof import('../../hair-live-preview/hair-client.ts');
      const {hairModelById}=await import(/* @vite-ignore */ root+'hair-live-preview/models.ts') as typeof import('../../hair-live-preview/models.ts');
      const threeModule='/node_modules/three/build/three.module.js';
      const {Matrix4,Euler,Vector3,Quaternion}=await import(/* @vite-ignore */ threeModule) as typeof import('three');
      const hash=async(bytes:Uint8Array<ArrayBuffer>|Uint8ClampedArray<ArrayBuffer>)=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
      const canvas=()=>document.createElement('canvas'),source=canvas(),gCanvas=canvas(),wCanvas=canvas(),abort=new AbortController();
      const image=new Image();image.src='/tests/fixtures/face-a.jpg';await image.decode();source.width=640;source.height=427;
      source.getContext('2d',{alpha:false,willReadFrequently:true,colorSpace:'srgb'})!.drawImage(image,0,0,640,427);
      const pixels=(c:HTMLCanvasElement)=>c.getContext('2d',{willReadFrequently:true,colorSpace:'srgb'})!.getImageData(0,0,c.width,c.height);
      const rgba=pixels(source),sourceSHA256=await hash(rgba.data),face=new DetectorClient(),segmenter=new HairClient(hair,{delegate:'CPU'});
      await face.initialize(abort.signal);const detection=await face.detect(await createImageBitmap(source),1);face.close();
      if(!detection.matrix)throw new Error('The synthetic fixture has no detected face.');
      await segmenter.initialize(abort.signal);const segmentation=await segmenter.segment(await createImageBitmap(source),sourceSHA256,1);segmenter.close();
      const base=await LiveHairRenderer.create(gCanvas,abort.signal,eyewear),worker=await RenderWorkerRenderer.create(wCanvas,abort.signal,eyewear);
      const rows=[];let generation=0;const inputByteCount=rgba.data.byteLength,maskBytes=segmentation.category.byteLength;
      try {
        for(const [name,pitch,yaw] of [['front',0,0],['down',25,0],['up',-25,0],['yaw-left',0,-45],['yaw-right',0,45]] as const){
          const pose=structuredClone(detection),position=new Vector3(),rotation=new Quaternion(),scale=new Vector3();
          new Matrix4().fromArray(detection.matrix!).decompose(position,rotation,scale);
          pose.matrix=new Matrix4().compose(position,new Quaternion().setFromEuler(new Euler(pitch*Math.PI/180,yaw*Math.PI/180,0)),scale).toArray();
          const detectionSHA256=await hash(new TextEncoder().encode(JSON.stringify(pose))),pair={sourceSHA256,detectionSHA256,eyewearModel:eyewear};
          const mask={...segmentation,detectionSHA256},model=hairModelById(hair),owned=createOwnedSourceFrame(source,pixels(source),{
            sourceSHA256,generation:++generation,sessionId:'native-matched',isCurrent:()=>!abort.signal.aborted});
          await base.present(source,pose,mask,pair,model,{source:owned,options:PROFILES.combined.options});
          // Model switching back from G: the shared visible surface contains G's
          // newer output while the worker privately retains its preceding pair.
          wCanvas.width=gCanvas.width;wCanvas.height=gCanvas.height;
          wCanvas.getContext('2d')!.putImageData(pixels(gCanvas),0,0);
          const sentinel=await hash(pixels(wCanvas).data);
          await worker.prepare(source,pose,pair,model,true,owned,'accepted');
          const privatePrepare=await hash(pixels(wCanvas).data)===sentinel;
          await worker.complete(mask);const privateComplete=await hash(pixels(wCanvas).data)===sentinel;worker.finish(mask);
          const variants=[];
          for(const variant of ['accepted','hair'] as const){base.selectVariant(variant);worker.selectVariant(variant);
            variants.push({variant,g:await hash(pixels(gCanvas).data),worker:await hash(pixels(wCanvas).data)});}
          const stats=worker.stats!,gStats=base.stats!;
          rows.push({name,variants,privatePrepare,privateComplete,sameGeometry:JSON.stringify(worker.captureSnapshot)===JSON.stringify(base.captureSnapshot),
            sameSafeguards:JSON.stringify([stats.protectedCheck,stats.noseCheck,stats.outsideEditableCheck,stats.backgroundPreservationCheck])===JSON.stringify([gStats.protectedCheck,gStats.noseCheck,gStats.outsideEditableCheck,gStats.backgroundPreservationCheck]),
            hasMask:stats.hasMask,stats});
        }
        const diagnostic=await worker.exportDiagnostic(),held=worker.copyHeldInput();
        if(!diagnostic||!held)throw new Error('Worker held export unavailable.');
        const sourceRetained=await hash(pixels(held.source).data)===sourceSHA256;
        const exportedImage=new Image();exportedImage.src=diagnostic.hairPngDataUrl as string;await exportedImage.decode();
        const decoded=canvas();decoded.width=exportedImage.naturalWidth;decoded.height=exportedImage.naturalHeight;decoded.getContext('2d')!.drawImage(exportedImage,0,0);
        const exportMatches=await hash(pixels(decoded).data)===await hash(pixels(wCanvas).data);
        return {rows,sourceRetained,exportMatches,inputRetained:rgba.data.byteLength===inputByteCount,maskRetained:segmentation.category.byteLength===maskBytes,
          nativeSamples:{g:base.nativeSamples,worker:worker.nativeSamples}};
      } finally {worker.dispose();base.dispose();abort.abort();face.close();segmenter.close();}
    },{eyewear,hair});
    const receipt=testInfo.outputPath('native-matched.json');await mkdir(dirname(receipt),{recursive:true});
    await writeFile(receipt,JSON.stringify(result,null,2));
    await testInfo.attach('native-matched.json',{path:receipt,contentType:'application/json'});
    expect(result.sourceRetained).toBe(true);expect(result.exportMatches).toBe(true);expect(result.inputRetained).toBe(true);expect(result.maskRetained).toBe(true);
    expect(result.nativeSamples.worker).toBe(result.nativeSamples.g);
    for(const row of result.rows){expect(row.privatePrepare,row.name).toBe(true);expect(row.privateComplete,row.name).toBe(true);
      expect(row.sameGeometry,row.name).toBe(true);expect(row.sameSafeguards,row.name).toBe(true);expect(row.hasMask,row.name).toBe(true);
      for(const variant of row.variants)expect(variant.worker,row.name+'/'+variant.variant).toBe(variant.g);}
  });
}
test('native worker cancellation, protocol failure, stop and fresh restart keep publication private',async({page})=>{
  await page.goto('/experiments/efficiency-lab/render-worker/native.html');
  const result=await page.evaluate(async()=>{
    const root='/experiments/',module=root+'efficiency-lab/render-worker/client.ts';
    const {RenderWorkerRenderer}=await import(/* @vite-ignore */ module) as typeof import('./client.ts');
    const modelsModule=root+'hair-live-preview/models.ts';
    const {hairModelById}=await import(/* @vite-ignore */ modelsModule) as typeof import('../../hair-live-preview/models.ts');
    const source=document.createElement('canvas'),display=document.createElement('canvas');source.width=display.width=32;source.height=display.height=24;
    source.getContext('2d')!.fillRect(0,0,32,24);display.getContext('2d')!.fillStyle='red';display.getContext('2d')!.fillRect(0,0,32,24);
    const before=display.toDataURL(),sourceSHA256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',source.getContext('2d')!.getImageData(0,0,32,24).data)),b=>b.toString(16).padStart(2,'0')).join('');
    const detection={landmarks:[],matrix:null,inferenceMs:0},pair={sourceSHA256,detectionSHA256:'b'.repeat(64),eyewearModel:'amber-horizon'},model=hairModelById('hair-only');
    const workers:{terminated:boolean;prepared:number}[]=[];let mode:'cancel'|'corrupt'|'normal'='cancel';let abort=new AbortController();
    class ObservedWorker extends Worker {
      private record={terminated:false,prepared:0};
      constructor(){super(root+'efficiency-lab/render-worker/worker.ts',{type:'module'});workers.push(this.record);}
      override postMessage(message:unknown,transfer:Transferable[]|StructuredSerializeOptions=[]):void {
        const request=message as {type?:string;owner?:{generation:number}};
        if(request.type==='prepare') {
          this.record.prepared++;
          if(mode==='corrupt')message={...request,owner:{...request.owner,generation:-1}};
        }
        if(Array.isArray(transfer))super.postMessage(message,transfer);else super.postMessage(message,transfer);
        if(request.type==='prepare'&&mode==='cancel')queueMicrotask(()=>abort.abort());
      }
      override terminate():void{this.record.terminated=true;super.terminate();}
    }
    const failures:string[]=[];
    for(const next of ['cancel','corrupt'] as const){
      mode=next;abort=new AbortController();const renderer=await RenderWorkerRenderer.create(display,abort.signal,'amber-horizon',()=>new ObservedWorker());
      try {await renderer.prepare(source,detection,pair,model,false);throw new Error('Expected failure was missing.');}
      catch(error){failures.push(error instanceof Error?error.message:String(error));}
      finally{renderer.dispose();abort.abort();}
      if(display.toDataURL()!==before)throw new Error('An incomplete worker frame became visible.');
    }
    mode='normal';abort=new AbortController();const renderer=await RenderWorkerRenderer.create(display,abort.signal,'amber-horizon',()=>new ObservedWorker());
    await renderer.prepare(source,detection,pair,model,false);
    const afterPrepare=display.toDataURL();await renderer.complete(null);const afterComplete=display.toDataURL();renderer.finish(null);
    const afterFinish=display.toDataURL();renderer.dispose();abort.abort();
    const startupAbort=new AbortController(),startup=RenderWorkerRenderer.create(display,startupAbort.signal,'amber-horizon',()=>new ObservedWorker());
    startupAbort.abort();let startupCancelled=false;try{await startup;}catch{startupCancelled=true;}
    return {failures,privateUntilFinish:afterPrepare===before&&afterComplete===before,publishedAfterFinish:afterFinish!==before,
      stopped:workers.every(worker=>worker.terminated),requests:workers.map(worker=>worker.prepared),startupCancelled};
  });
  expect(result.failures[0]).toContain('stopped');expect(result.failures[1]).toContain('ownership');
  expect(result.privateUntilFinish).toBe(true);expect(result.publishedAfterFinish).toBe(true);expect(result.stopped).toBe(true);
  expect(result.requests).toEqual([1,1,1,0]);expect(result.startupCancelled).toBe(true);
});
test('native worker preserves alpha source history, size changes and G native width cap',async({page})=>{
  await page.goto('/experiments/efficiency-lab/render-worker/native.html');
  const rows=await page.evaluate(async()=>{
    const root='/experiments/';
    const {RenderWorkerRenderer}=await import(/* @vite-ignore */ root+'efficiency-lab/render-worker/client.ts') as typeof import('./client.ts');
    const {LiveHairRenderer}=await import(/* @vite-ignore */ root+'speed-lab/renderer.ts') as typeof import('../../speed-lab/renderer.ts');
    const {PROFILES}=await import(/* @vite-ignore */ root+'speed-lab/profiles.ts') as typeof import('../../speed-lab/profiles.ts');
    const {createOwnedSourceFrame}=await import(/* @vite-ignore */ root+'speed-lab/speed-options.ts') as typeof import('../../speed-lab/speed-options.ts');
    const {hairModelById}=await import(/* @vite-ignore */ root+'hair-live-preview/models.ts') as typeof import('../../hair-live-preview/models.ts');
    const abort=new AbortController(),source=document.createElement('canvas'),gCanvas=document.createElement('canvas'),wCanvas=document.createElement('canvas');
    const base=await LiveHairRenderer.create(gCanvas,abort.signal),worker=await RenderWorkerRenderer.create(wCanvas,abort.signal);
    const detection={landmarks:[],matrix:null,inferenceMs:0},model=hairModelById('hair-only');let generation=0;
    const hash=async(bytes:Uint8ClampedArray<ArrayBuffer>)=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
    const pixels=(canvas:HTMLCanvasElement)=>canvas.getContext('2d',{willReadFrequently:true,colorSpace:'srgb'})!.getImageData(0,0,canvas.width,canvas.height);
    const rows=[];
    try {
      for(const [width,height,alpha] of [[32,24,255],[32,24,127],[40,30,0],[1600,900,255]] as const){
        source.width=width;source.height=height;
        const image=new ImageData(width,height);for(let index=0;index<image.data.length;index+=4){image.data.set([index%255,83,141,alpha],index);}
        source.getContext('2d',{willReadFrequently:true,colorSpace:'srgb'})!.putImageData(image,0,0);
        const rgba=pixels(source),sourceSHA256=await hash(rgba.data),pair={sourceSHA256,detectionSHA256:'b'.repeat(64),eyewearModel:'amber-horizon'};
        const owned=createOwnedSourceFrame(source,rgba,{sourceSHA256,generation:++generation,sessionId:'alpha-controls',isCurrent:()=>!abort.signal.aborted});
        await base.present(source,detection,null,pair,model,{source:owned,options:PROFILES.combined.options});
        await worker.prepare(source,detection,pair,model,false,owned);await worker.complete(null);worker.finish(null);
        rows.push({width,height,alpha,g:await hash(pixels(gCanvas).data),worker:await hash(pixels(wCanvas).data),outputWidth:wCanvas.width,outputHeight:wCanvas.height});
      }return rows;
    } finally {worker.dispose();base.dispose();abort.abort();}
  });
  for(const row of rows){expect(row.worker,JSON.stringify(row)).toBe(row.g);expect(row.outputWidth).toBe(Math.min(row.width,1280));expect(row.outputHeight).toBe(Math.round(row.outputWidth*row.height/row.width));}
});
