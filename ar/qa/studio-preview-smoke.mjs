/** Repeatable GPU material test. Synthetic geometry/background, optional hash-pinned local product; never opens a camera. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {BoxGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial} from 'three';
import {GLTFExporter} from 'three/addons/exporters/GLTFExporter.js';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(v=>v.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const output=path.resolve(option('output',path.join(root,'qa/output/studio-preview-smoke')));
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
async function sourceSnapshot(){
 const seen=new Set(),todo=['src/render/renderer.ts','src/studio/viewer.ts','src/studio/bridge.ts','src/main.ts'];
 while(todo.length){const name=todo.pop();if(seen.has(name))continue;seen.add(name);const text=await fs.readFile(path.join(root,name),'utf8');
  for(const match of text.matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*)['"](\.{1,2}\/[^'"]+)['"]/g)){const target=path.posix.normalize(path.posix.join(path.posix.dirname(name),match[1]));if(target.startsWith('src/')&&target.endsWith('.ts'))todo.push(target);}}
 for(const name of['qa/studio-preview-smoke.html','qa/studio-preview-smoke.mjs','package.json'])seen.add(name);
 return Object.fromEntries(await Promise.all([...seen].sort().map(async name=>[name,hash(await fs.readFile(path.join(root,name)))])));
}
globalThis.FileReader=class {readAsArrayBuffer(blob){blob.arrayBuffer().then(value=>{this.result=value;this.onloadend?.();});}};
async function fixture(){
 const group=new Group(),crystal=new MeshPhysicalMaterial({color:0xe5dfd6,transmission:.85,roughness:.12,thickness:.004,attenuationDistance:.015,clearcoat:.2});
 const lens=new MeshPhysicalMaterial({color:0x9d7956,transmission:.88,roughness:.09,thickness:.001}),metal=new MeshStandardMaterial({color:0xa98022,metalness:.9,roughness:.2});
 const add=(name,role,size,position,material)=>{const mesh=new Mesh(new BoxGeometry(...size),material);mesh.name=name;mesh.userData.partRole=role;mesh.geometry.translate(...position);group.add(mesh);};
 for(const sign of[-1,1]){
  add('lens '+sign,'lens',[.047,.040,.002],[sign*.031,.014,0],lens);
  add('crystal arm '+sign,'frame',[.008,.012,.164],[sign*.069,.023,-.075],crystal);
  add('gold core '+sign,'frame',[.002,.005,.148],[sign*.069,.023,-.075],metal);
  add('crystal rim top '+sign,'frame',[.057,.006,.006],[sign*.031,.038,0],crystal);
 }
 add('bridge','frame',[.018,.004,.006],[0,.027,0],crystal);
 const bytes=Buffer.from(await new GLTFExporter().parseAsync(group,{binary:true}));
 group.traverse(o=>{if(o instanceof Mesh)o.geometry.dispose();});crystal.dispose();lens.dispose();metal.dispose();return bytes;
}
const cases=[{id:'synthetic-crystal',bytes:await fixture()}];
if(option('model')){
 const url=new URL(option('model'));assert(['127.0.0.1','localhost','[::1]'].includes(url.hostname)&&['http:','https:'].includes(url.protocol),'Model must be served on loopback');
 assert(/^[a-f\d]{64}$/i.test(option('sha256','')),'Supply exact --sha256 for local product');
 const response=await fetch(url);assert(response.ok,'Local product fetch failed');const bytes=Buffer.from(await response.arrayBuffer());
 assert.equal(hash(bytes),option('sha256').toLowerCase());cases.push({id:'local-product',bytes});
}
const allowed=new Map(cases.map(c=>[`/__studio/${c.id}.glb`,c.bytes]));
const server=await createServer({configFile:false,root,cacheDir:path.join(root,'node_modules/.vite-studio-smoke'),server:{host:'127.0.0.1',port:0,hmr:false},plugins:[{
 name:'studio-smoke-assets',configureServer(server){server.middlewares.use((req,res,next)=>{const url=new URL(req.url??'/','http://localhost');const bytes=allowed.get(url.pathname);if(!bytes)return next();res.setHeader('Content-Type','model/gltf-binary');res.end(bytes);});}}]});
let browser;
try{
 await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'synthetic-crystal.glb'),cases[0].bytes);
 const implementation=await sourceSnapshot();await server.listen();const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const reports=[];
 for(const entry of cases){
  const errors=[],warnings=[],page=await browser.newPage({viewport:{width:1500,height:1100}});page.setDefaultTimeout(120000);
  await page.route('**/*',route=>{const url=route.request().url();return url.startsWith(origin+'/')||url.startsWith('data:')||url.startsWith('blob:')?route.continue():route.abort();});
  page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error')errors.push(message.text());if(message.type()==='warning')warnings.push(message.text());});
  const params=new URLSearchParams({model:`/__studio/${entry.id}.glb`,sha256:hash(entry.bytes),clip:'-.14',width:'145',expectCrystal:entry.id==='synthetic-crystal'?'1':'0'});
  await page.goto(`${origin}/qa/studio-preview-smoke.html?${params}`);await page.waitForFunction(()=>!!window.studioSmoke);
  let report;
  try{report=await page.evaluate(()=>window.studioSmoke.run());}catch(error){report={status:'failed',error:String(error.stack??error)};}
  report.id=entry.id;report.errors=errors;report.warnings=warnings;reports.push(report);
  const canvases=page.locator('#captures canvas');for(let i=0;i<await canvases.count();i++)await canvases.nth(i).screenshot({path:path.join(output,`${entry.id}-${i}.png`)});
  await page.close();
 }
 const implementation_after=await sourceSnapshot(),source_snapshot_stable=JSON.stringify(implementation)===JSON.stringify(implementation_after);
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify({created_at:new Date().toISOString(),reports,implementation,implementation_after,source_snapshot_stable},null,2)+'\n');
 console.log(JSON.stringify({output,reports},null,2));
 assert(reports.every(r=>r.status==='passed'&&r.errors.length===0),'Studio material GPU smoke failed; inspect report.json');
 assert(source_snapshot_stable,'Source changed during the GPU smoke.');
}finally{await browser?.close();await server.close();}
