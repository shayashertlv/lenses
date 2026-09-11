import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {chromium} from '@playwright/test';
import {createServer} from 'vite';
const root='.recovery/temple-rethink-2026-09-08',read=async p=>JSON.parse(await fs.readFile(p)),sha=b=>createHash('sha256').update(b).digest('hex');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const selection=await read(root+'/selection.json');
const metas=process.argv.includes('--smoke')?['dropout-454','flicker_recording-70','fresh-72','original-71'].map(k=>selection.frames.find(f=>f.key===k)):selection.frames;
const out=root+'/runs/'+new Date().toISOString().replaceAll(':','-');await fs.mkdir(out,{recursive:true});
const report={createdAt:new Date().toISOString(),out,evidence:'Original recorded image/detection/pose pairs; current perfecto Option17 reconstructed once and frozen; model substitution explicit.',sourceHashes:{},cases:[],errors:[],complete:false};
for(const p of ['src/render/renderer.ts','src/render/eyewear.ts','src/render/temple-clip.ts','src/render/temple-visibility.ts',...(await fs.readdir('experiments/temple-sagittal')).filter(p=>/\.(ts|mjs)$/.test(p)).map(p=>'experiments/temple-sagittal/'+p)])report.sourceHashes[p]=sha(await fs.readFile(p));
const captures=new Map();for(const m of metas)if(!captures.has(m.sourcePath)){const b=await fs.readFile(m.sourcePath);assert.equal(sha(b),m.sourceSHA256);captures.set(m.sourcePath,JSON.parse(b));report.sourceHashes[m.sourcePath]=sha(b);}
const save=()=>fs.writeFile(out+'/report.json',JSON.stringify(report,null,2));
let server,browser;
try{
 server=await createServer({configFile:'experiments/temple-sagittal/vite.config.ts',server:{host:'127.0.0.1',port:8058,strictPort:true}});await server.listen();
 browser=await chromium.launch({headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});const page=await browser.newPage();page.on('pageerror',e=>report.errors.push(e.message));
 await page.route('**/*',r=>{const u=new URL(r.request().url());if(u.hostname!=='127.0.0.1')return r.abort();if(u.pathname==='/private-sagittal-replay')return r.fulfill({contentType:'text/html',body:'<!doctype html><title>Exact paired temple replay</title>'});return r.continue();});
 if(process.argv.includes('--no-msaa'))await page.addInitScript(()=>{const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,options){return original.call(this,type,type==='webgl2'?{...options,antialias:false}:options);};});
 await page.goto('http://127.0.0.1:8058/private-sagittal-replay');
 for(const model of ['tom-ford-clear','amber-horizon']){
  await page.evaluate(async model=>{const{createReplay}=await import('/experiments/temple-sagittal/replay-browser.mjs');window.replay=await createReplay(model);},model);
  for(const meta of metas){
   const original=captures.get(meta.sourcePath).frames[meta.sourceFrameArrayIndex];assert.equal(sha(Buffer.from(original[meta.imageField].split(',')[1],'base64')),meta.sourceImageSHA256);assert.equal(sha(JSON.stringify(canonical(original.detection))),meta.detectionCanonicalJsonSHA256);
   const result=await page.evaluate(async({original,meta})=>window.replay.frame(original,meta),{original:{width:original.width,height:original.height,detection:original.detection,sourceUrl:original[meta.imageField]},meta});
   const row={id:meta.key,model,pitch:meta.pitchDegrees,yaw:meta.yawDegrees,sourcePath:meta.sourcePath,sourceSHA256:meta.sourceSHA256,sourceImageSHA256:meta.sourceImageSHA256,detectionCanonicalJsonSHA256:meta.detectionCanonicalJsonSHA256,frameIndex:meta.sourceFrameArrayIndex,regions:meta.regions};
   const dir=out+'/'+model+'/'+meta.key;await fs.mkdir(dir,{recursive:true});
   for(const k of ['before','after','source','mask']){const data=Buffer.from(result[k].split(',')[1],'base64'),path=dir+'/'+k+'.png';await fs.writeFile(path,data,{flag:'wx'});row[k]={path,sha256:sha(data)};delete result[k];}
   Object.assign(row,result);
   if(['dropout-454','flicker_recording-70','fresh-72','original-71'].includes(meta.key))row.replay=await page.evaluate(()=>window.replay.verifyReplay());
   const diagnostic=await page.evaluate(()=>window.replay.diagnostic());
   if(meta.key==='dropout-454'){const diagnosticPath=dir+'/diagnostic.json';await fs.writeFile(diagnosticPath,JSON.stringify(diagnostic),{flag:'wx'});row.diagnosticPath=diagnosticPath;}
   row.diagnostics=diagnostic?.diagnostics;
   report.cases.push(row);await save();console.log(JSON.stringify({model,id:meta.key,dropMm:row.drop.dropM*1000,changed:row.whole.changedPixels,nose:row.nose,protected:row.protectedChanged}));
  }
  await page.evaluate(()=>window.replay.dispose());
 }
 assert.deepEqual(report.errors,[]);report.complete=true;await save();console.log('REPORT '+out+'/report.json');
}catch(e){report.errors.push(e.stack??String(e));await save();throw e;}finally{await browser?.close();await server?.close();}
