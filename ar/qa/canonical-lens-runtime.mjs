/** Actual TryOnRenderer conformance, local fixtures only; no publication/camera. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(value=>value.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const fixtures=path.resolve(root,option('fixtures','qa/output/canonical-lens-fixtures'));
const output=path.resolve(root,option('output','qa/output/canonical-lens-runtime'));
const mode=option('mode','conformance');
assert(['conformance','debug-layers','debug-raster'].includes(mode),'Unknown harness mode');
await fs.mkdir(output,{recursive:true});
const manifestBytes=await fs.readFile(path.join(fixtures,'manifest.json')),manifest=JSON.parse(manifestBytes);
const allowed=new Set(['manifest.json',...manifest.cases.map(c=>c.model),...manifest.negative_controls.map(c=>c.model),
 ...(manifest.layer_cases??[]).map(c=>c.model),...(manifest.layer_negative_controls??[]).map(c=>c.model),...(manifest.display_cases??[]).map(c=>c.model)]);
const errors=[],warnings=[],failedResponses=[];
const sourceFiles=['src/render/renderer.ts','src/render/lens-material.ts','src/render/lens-layers.ts','src/render/layer-overflow.ts','src/render/opaque-display.ts','src/render/optical-topology.ts','src/render/effective-optical-topology.ts','src/render/nearest-optical-groups.ts','src/render/eyewear-shadow.ts','src/eyewear/lens-appearance.ts','src/eyewear/optical-material.ts','src/render/continuity.ts','src/render/temple-visibility.ts','qa/canonical-lens-runtime.html','qa/canonical-lens-layers.mjs'];
const snapshotSources=async()=>Object.fromEntries(await Promise.all(sourceFiles.map(async file=>[file,crypto.createHash('sha256').update(await fs.readFile(path.join(root,file))).digest('hex')])));
const startSources=await snapshotSources();
const server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false},plugins:[{
 name:'private-runtime-fixtures',configureServer(server){server.middlewares.use(async(req,res,next)=>{
  const url=new URL(req.url??'/','http://127.0.0.1');if(!url.pathname.startsWith('/__runtime-fixtures/'))return next();
  const relative=decodeURIComponent(url.pathname.slice('/__runtime-fixtures/'.length));if(!allowed.has(relative)){res.statusCode=404;res.end();return;}
  try{res.setHeader('Content-Type',relative.endsWith('.glb')?'model/gltf-binary':'application/json');res.end(await fs.readFile(path.join(fixtures,relative)));}catch{res.statusCode=404;res.end();}
 });}}]});
let browser,report;
try{
 await server.listen();const address=server.httpServer.address();
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage({viewport:{width:1100,height:900}});page.setDefaultTimeout(120000);
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());if(message.type()==='warning')warnings.push(message.text());});
 page.on('response',response=>{if(response.status()>=400)failedResponses.push({url:response.url(),status:response.status()});});
 await page.goto(`http://127.0.0.1:${address.port}/qa/canonical-lens-runtime.html`);
 await page.waitForFunction(()=>!!window.canonicalLensRuntime);
 try{report=await page.evaluate(mode=>mode==='debug-layers'?window.canonicalLensRuntime.debugLayers():mode==='debug-raster'?window.canonicalLensRuntime.debugRaster():window.canonicalLensRuntime.run(),mode);}catch(error){report={...await page.evaluate(()=>window.runtimeReport??{}),status:'failed',harnessError:String(error.stack??error)};}
 report.createdAt=new Date().toISOString();report.fixtureManifestSha256=crypto.createHash('sha256').update(manifestBytes).digest('hex');
 report.errors=errors;report.warnings=warnings;report.failedResponses=failedResponses;report.implementation=startSources;
 report.implementationAfter=await snapshotSources();report.sourceSnapshotStable=JSON.stringify(startSources)===JSON.stringify(report.implementationAfter);
 if(!report.sourceSnapshotStable)report.status='failed';
 await page.screenshot({path:path.join(output,'contact-sheet.png'),fullPage:true});
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({status:report.status,cases:report.cases?.length,maximumError:report.maximumError,negativeControls:report.negativeControls,harnessError:report.harnessError,errors,failedResponses,warnings},null,2));
 assert.equal(errors.length,0,'Browser/shader errors');assert.equal(report.status,mode==='conformance'?'passed':'diagnostic','Actual lens runtime conformance failed');
}finally{await browser?.close();await server.close();}
