import fs from 'node:fs/promises';
import {chromium} from '@playwright/test';
import {createServer} from 'vite';
const out='.recovery/temple-rethink-2026-09-08/synthetic-runs/'+new Date().toISOString().replaceAll(':','-');await fs.mkdir(out,{recursive:true});
const report={out,evidence:'SYNTHETIC generated canonical geometry, source drawing, projected landmarks and pose. No wearer image or real detection; no anatomical validation.',cases:[],errors:[],complete:false};
let browser,server;
try{
 server=await createServer({configFile:'experiments/temple-sagittal/vite.config.ts',server:{port:8058,host:'127.0.0.1',strictPort:true}});await server.listen();
 browser=await chromium.launch({headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});const page=await browser.newPage();page.on('pageerror',e=>report.errors.push(e.message));
 await page.route('**/private-synthetic',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><title>Synthetic canonical experiment</title>'}));await page.goto('http://127.0.0.1:8058/private-synthetic');
 const canonical=JSON.parse(await fs.readFile('public/models/canonical-face.json'));
 for(const model of ['tom-ford-clear','amber-horizon']){
  await page.evaluate(async model=>{const{createReplay}=await import('/experiments/temple-sagittal/replay-browser.mjs');window.replay=await createReplay(model);},model);
  for(const [down,yaw]of [[20,0],[40,0],[50,0],[-20,0],[40,-30],[40,30]]){
   const result=await page.evaluate(async({canonical,down,yaw})=>{const{syntheticPair}=await import('/experiments/temple-sagittal/synthetic-pair.mjs');const pair=syntheticPair(canonical,down,yaw);return{meta:pair.meta,...await window.replay.frame(pair.original,pair.meta),diagnostic:window.replay.diagnostic()};},{canonical,down,yaw});
   const dir=out+'/'+model+'/'+result.meta.key;await fs.mkdir(dir,{recursive:true});
   for(const k of ['before','after','source','mask']){const p=dir+'/'+k+'.png';await fs.writeFile(p,Buffer.from(result[k].split(',')[1],'base64'));result[k]=p;}
   for(const k of ['originalPerfectoPngDataUrl','candidatePngDataUrl','branchPngDataUrl','sourcePngDataUrl']){if(k==='branchPngDataUrl'){const p=dir+'/unprotected-branch.png';await fs.writeFile(p,Buffer.from(result.diagnostic[k].split(',')[1],'base64'));result.branch=p;}delete result.diagnostic[k];}
   report.cases.push({model,...result});console.log(JSON.stringify({model,down,yaw,changed:result.whole.changedPixels,nose:result.nose,dropMm:result.drop.dropM*1000}));await fs.writeFile(out+'/report.json',JSON.stringify(report,null,2));
  }
  await page.evaluate(()=>window.replay.dispose());
 }
 report.complete=report.errors.length===0;
}catch(e){report.errors.push(e.stack??String(e));throw e;}finally{await fs.writeFile(out+'/report.json',JSON.stringify(report,null,2));await browser?.close();await server?.close();console.log('REPORT '+out+'/report.json');}
