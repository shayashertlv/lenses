/** Private generated-candidate audit; actual renderer, no publication or camera. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(value=>value.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
assert(option('manifest'),'Supply --manifest=/absolute/path/manifest.json');
const manifestPath=path.resolve(root,option('manifest')),output=path.resolve(root,option('output','qa/output/prepared-optics'));
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const original=await fs.readFile(manifestPath),manifest=JSON.parse(original),allowed=new Map();
assert(Array.isArray(manifest.cases)&&manifest.cases.length,'Nonempty cases required');
for(const entry of manifest.cases){
 assert(/^[a-z0-9][a-z0-9_-]{0,79}$/.test(entry.id)&&!allowed.has(`${entry.id}.glb`),'Unique safe case IDs required');
 const bytes=await fs.readFile(path.resolve(path.dirname(manifestPath),entry.path));
 assert.equal(digest(bytes),entry.model_sha256,'Input GLB hash mismatch');
 assert(Array.isArray(entry.surfaces)&&entry.surfaces.length,'Expected optical surface bindings required');
 allowed.set(`${entry.id}.glb`,bytes);
}
allowed.set('manifest.json',Buffer.from(JSON.stringify(manifest)));
const sourceFiles=['src/render/renderer.ts','src/render/lens-material.ts','src/render/lens-layers.ts','src/render/layer-overflow.ts','src/render/opaque-display.ts','src/render/optical-topology.ts','src/render/eyewear-shadow.ts','src/eyewear/lens-appearance.ts','src/eyewear/optical-material.ts','src/render/continuity.ts','src/render/temple-visibility.ts','qa/prepared-optics.html','qa/prepared-optics.mjs'];
const snapshot=async()=>Object.fromEntries(await Promise.all(sourceFiles.map(async file=>[file,digest(await fs.readFile(path.join(root,file)))])));
const before=await snapshot(),errors=[],warnings=[],failedResponses=[];
const server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false},plugins:[{
 name:'private-prepared-models',configureServer(server){server.middlewares.use((req,res,next)=>{
  const url=new URL(req.url??'/','http://127.0.0.1');if(!url.pathname.startsWith('/__prepared/'))return next();
  const relative=url.pathname.slice('/__prepared/'.length),bytes=allowed.get(relative);
  if(!bytes){res.statusCode=404;res.end();return;}
  res.setHeader('Content-Type',relative.endsWith('.glb')?'model/gltf-binary':'application/json');res.end(bytes);
 });}}]});
let browser;
try{
 await fs.mkdir(output,{recursive:true});await server.listen();
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage({viewport:{width:1100,height:900}});page.setDefaultTimeout(120000);
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());if(message.type()==='warning')warnings.push(message.text());});
 page.on('response',response=>{if(response.status()>=400)failedResponses.push({url:response.url(),status:response.status()});});
 await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/qa/prepared-optics.html`);
 await page.waitForFunction(()=>!!window.preparedOptics);
 let report;
 try{report=await page.evaluate(()=>window.preparedOptics.run());}catch(error){report={...await page.evaluate(()=>window.preparedReport??{}),status:'failed',harnessError:String(error.stack??error)};}
 Object.assign(report,{created_at:new Date().toISOString(),manifest_sha256:digest(original),implementation:before,
  implementation_after:await snapshot(),errors,warnings,failedResponses});
 report.source_snapshot_stable=JSON.stringify(before)===JSON.stringify(report.implementation_after);
 if(!report.source_snapshot_stable||errors.length||failedResponses.length)report.status='failed';
 await page.screenshot({path:path.join(output,'contact-sheet.png'),fullPage:true});
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({status:report.status,cases:report.cases?.map(row=>({id:row.id,status:row.status,error:row.error})),errors,warnings,failedResponses},null,2));
 assert.equal(report.status,'passed','Prepared real-asset runtime audit failed');
}finally{await browser?.close();await server.close();}
