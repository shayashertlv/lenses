/** Isolated material contract check. No live camera, app restart or publication.
 * node qa/lens-conformance.mjs --fixtures=../automation/data/lens-conformance
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const here=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(here,'..');
const option=(name,fallback)=>process.argv.find(a=>a.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const fixtures=path.resolve(option('fixtures',path.join(root,'../automation/data/lens-conformance')));
const output=path.resolve(option('out',path.join(here,'output/lens-conformance')));
await fs.mkdir(output,{recursive:true});
const casesBytes=await fs.readFile(path.join(fixtures,'cases.json'));
const cases=JSON.parse(casesBytes.toString());
const allowed=new Set(['cases.json',...cases.cases.map(c=>c.model)]),errors=[],warnings=[],failedResponses=[];
const server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0},plugins:[{
 name:'private-lens-fixtures',configureServer(server){server.middlewares.use(async(req,res,next)=>{
   const url=new URL(req.url??'/','http://127.0.0.1');if(!url.pathname.startsWith('/__lens-fixtures/'))return next();
   const relative=decodeURIComponent(url.pathname.slice('/__lens-fixtures/'.length));
   if(!allowed.has(relative)){res.statusCode=404;res.end();return;}
   const file=path.resolve(fixtures,relative);if(!file.startsWith(fixtures+path.sep)){res.statusCode=403;res.end();return;}
   try{res.setHeader('Content-Type',relative.endsWith('.glb')?'model/gltf-binary':'application/json');res.end(await fs.readFile(file));}
   catch{res.statusCode=404;res.end();}
 });}}]});
let browser;
try{
 await server.listen();const address=server.httpServer.address();assert.equal(typeof address,'object');
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage({viewport:{width:1120,height:1100}});
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());if(message.type()==='warning')warnings.push(message.text());});
 page.on('response',response=>{if(response.status()>=400)failedResponses.push({url:response.url(),status:response.status()});});
 await page.goto(`http://127.0.0.1:${address.port}/qa/lens-appearance.html`);
 await page.waitForFunction(()=>!!window.lensConformance);
 const report=await page.evaluate(()=>window.lensConformance.run());
 report.fixtureSha256=crypto.createHash('sha256').update(casesBytes).digest('hex');report.createdAt=new Date().toISOString();report.browserErrors=errors;
 report.browserWarnings=warnings;report.failedResponses=failedResponses;
 report.responseModuleSha256=crypto.createHash('sha256').update(await fs.readFile(path.join(root,'src/eyewear/lens-appearance.ts'))).digest('hex');
 await page.screenshot({path:path.join(output,'contact-sheet.png'),fullPage:true});
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({status:report.status,cases:report.cases.length,negativeControls:report.negativeControls,
  maximumResponseError:Math.max(...report.cases.map(c=>c.gpuResponseMaxError)),maximumSurfaceError:Math.max(...report.cases.map(c=>c.surfaceMaxError)),errors,failedResponses,warnings},null,2));
 assert.equal(errors.length,0,'Browser/shader errors');assert.equal(report.status,'passed','Material conformance failed');
}finally{await browser?.close();await server.close();}
