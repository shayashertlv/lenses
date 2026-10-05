/** Raw provider GLBs, local-only inspection. Does not prepare or repair an AR asset. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(v=>v.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
assert(option('manifest'),'Supply --manifest=/absolute/path/manifest.json');
const manifestPath=path.resolve(option('manifest')), output=path.resolve(option('output','qa/output/provider-comparison'));
const stage=option('stage','raw');assert(['raw','ar'].includes(stage),'stage must be raw or ar');
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const original=await fs.readFile(manifestPath), manifest=JSON.parse(original), allowed=new Map();
assert(Array.isArray(manifest.cases)&&manifest.cases.length,'Nonempty cases required');
function documentOf(bytes){
 assert(bytes.length>=20&&bytes.readUInt32LE(0)===0x46546c67&&bytes.readUInt32LE(4)===2,'Expected GLB v2');
 assert.equal(bytes.readUInt32LE(8),bytes.length,'GLB length mismatch');
 assert.equal(bytes.readUInt32LE(16),0x4e4f534a,'First GLB chunk must be JSON');
 const doc=JSON.parse(bytes.subarray(20,20+bytes.readUInt32LE(12)).toString('utf8'));
 for(const resource of [...doc.buffers??[],...doc.images??[]])assert(!resource.uri||resource.uri.startsWith('data:'),'External GLB resource rejected; supply self-contained downloaded GLB');
 return doc;
}
for(const entry of manifest.cases){
 assert(/^[a-z0-9][a-z0-9_-]{0,79}$/.test(entry.id)&&!allowed.has(`${entry.id}.glb`),'Unique safe case IDs required');
 const bytes=await fs.readFile(path.resolve(path.dirname(manifestPath),entry.path));
 if(entry.model_sha256)assert.equal(digest(bytes),entry.model_sha256,'Input GLB hash mismatch');
 entry.model_sha256=digest(bytes);entry.file_bytes=bytes.length;
 const doc=documentOf(bytes);
 entry.source_inventory={meshes:doc.meshes?.length??0,nodes:doc.nodes?.length??0,materials:doc.materials??[],
  images:doc.images?.map(image=>({name:image.name,mimeType:image.mimeType,embedded:image.bufferView!==undefined||!!image.uri}))??[],
  textures:doc.textures?.length??0,extensions_used:doc.extensionsUsed??[],extensions_required:doc.extensionsRequired??[],
  animations:doc.animations?.length??0,skins:doc.skins?.length??0,
  extras_keys:Object.keys(doc.extras??{}),
  approximate_standard_material_fallback:(doc.materials??[]).some(material=>material.extras?.fallbackIsApproximate===true)};
 allowed.set(`${entry.id}.glb`,bytes);
}
allowed.set('manifest.json',Buffer.from(JSON.stringify(manifest)));
// 'ar' stage: the transitive relative-import closure (within src/) of what the AR page imports, computed at launch.
// It was a hand-kept list until 2026-09-30, and it went stale twice: until 2026-09-27 only five files were fingerprinted,
// so an edit to a runtime dependency (lens-material.ts, eyewear-volume.ts, lens-layers.ts, ...) left
// source_snapshot_stable true; then translucent-twin.ts and translucent-look-through.ts (every crystal pixel) were imported
// by renderer.ts and never added. Static `from '...'` / `import('...')` specifiers that start with ./ or ../ are followed.
async function importClosure(roots){
 const seen=new Set(),todo=[...roots];
 while(todo.length){
  const name=todo.pop();if(seen.has(name))continue;seen.add(name);
  const text=await fs.readFile(path.join(root,name),'utf8');
  for(const match of text.matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*)['"](\.{1,2}\/[^'"]+)['"]/g)){
   const target=path.posix.normalize(path.posix.join(path.posix.dirname(name),match[1]));
   if(target.startsWith('src/'))todo.push(target);
  }
 }
 return [...seen].sort();
}
const AR_STAGE_SOURCES=stage==='ar'?await importClosure(['src/render/renderer.ts','src/eyewear/catalog.ts','src/render/continuity.ts']):[];
const sources=['qa/provider-comparison.mjs','qa/provider-comparison.html','qa/provider-comparison-ar.html','qa/provider-comparison-lighting.mjs','package.json',
 ...AR_STAGE_SOURCES];
const snapshot=async()=>Object.fromEntries(await Promise.all(sources.map(async name=>[name,digest(await fs.readFile(path.join(root,name)))])));
const before=await snapshot(),errors=[],warnings=[],failedResponses=[],blockedExternal=[];
// The harness's own dependency cache (review SPEED-4, 2026-09-29): vite's default is ar/node_modules/.vite, which the AR
// app's dev server (8240, started by the Studio) and the other ar/qa scripts also write, each with its own config. A launch
// that found it written under another config re-optimised it, and launches doing so together swapped each other's files
// under their pages: one of four parallel observation runs lost its page to the 180 s timeout; a re-optimisation also
// rewrites the files the running dev server serves its pages from. This config is identical on every launch, so after
// the first run this cache stays valid and only harness launches ever write it.
const HARNESS_VITE_CACHE_DIR='node_modules/.vite-provider-comparison';
const server=await createServer({configFile:false,root,cacheDir:path.join(root,HARNESS_VITE_CACHE_DIR),server:{host:'127.0.0.1',port:0,hmr:false},plugins:[{
 name:'private-provider-models',configureServer(server){server.middlewares.use((req,res,next)=>{
  const url=new URL(req.url??'/','http://127.0.0.1');if(!url.pathname.startsWith('/__providers/'))return next();
  const name=url.pathname.slice('/__providers/'.length),bytes=allowed.get(name);
  if(!bytes){res.statusCode=404;res.end();return;}
  res.setHeader('Content-Type',name.endsWith('.glb')?'model/gltf-binary':'application/json');res.end(bytes);
 });}}]});
let browser;
try{
 await fs.mkdir(output,{recursive:true});await server.listen();
 const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage({viewport:{width:1400,height:1000}});page.setDefaultTimeout(180000);
 await page.route('**/*',route=>{
  const url=route.request().url();
  if(url.startsWith(origin+'/')||url.startsWith('data:')||url.startsWith('blob:'))return route.continue();
  blockedExternal.push(url);return route.abort();
 });
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());if(message.type()==='warning')warnings.push(message.text());});
 page.on('response',response=>{if(response.status()>=400)failedResponses.push({url:response.url(),status:response.status()});});
 await page.goto(origin+`/qa/provider-comparison${stage==='ar'?'-ar':''}.html`);
 await page.waitForFunction(()=>!!window.providerComparison);
 let report;
 try{report=await page.evaluate(()=>window.providerComparison.run());}
 catch(error){report={...await page.evaluate(()=>window.providerReport??{}),status:'failed',harness_error:String(error.stack??error)};}
 Object.assign(report,{created_at:new Date().toISOString(),manifest_sha256:digest(original),implementation:before,
  implementation_after:await snapshot(),errors,warnings,failed_responses:failedResponses,blocked_external:blockedExternal});
 report.source_snapshot_stable=JSON.stringify(before)===JSON.stringify(report.implementation_after);
 if(!report.source_snapshot_stable||errors.length||failedResponses.length||blockedExternal.length)report.status='failed';
 const count=await page.evaluate(()=>window.providerImages.length);
 for(let i=0;i<count;i++){
  const card=await page.evaluate(index=>window.providerImages[index],i),bytes=Buffer.from(card.data.split(',')[1],'base64');
  await fs.writeFile(path.join(output,card.filename),bytes);
  const row=report.cases.find(row=>row.id===card.id);row.renders.find(render=>render.filename===card.filename).sha256=digest(bytes);
 }
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({status:report.status,output,cases:report.cases?.map(row=>({id:row.id,status:row.status,triangles:row.triangles,materials:row.material_count,error:row.error})),errors,warnings,failedResponses,blockedExternal},null,2));
 assert.equal(report.status,stage==='ar'?'inspected':'rendered','Provider inspection harness failed');
}finally{await browser?.close();await server.close();}
