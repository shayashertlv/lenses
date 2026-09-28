/** Fixed real-source geometry QA; no source or production mutation. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {overrideAngleMath} from './taylor24-angle-override.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(value=>value.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const angleMath=option('angle-math','builtin');assert(['builtin','taylor24'].includes(angleMath),'angle-math must be builtin or taylor24');
const bundle=path.resolve(root,option('bundle','../automation/data/source-optical-groups-browser-v1'));
const output=path.resolve(root,option('output','qa/output/source-optical-groups-v1'));
const sha=async file=>crypto.createHash('sha256').update(await fs.readFile(file)).digest('hex');
await fs.mkdir(output,{recursive:true});assert.equal((await fs.readdir(output)).length,0,'Use a new/empty QA output directory');
const manifestPath=path.join(bundle,'manifest.json'),manifest=JSON.parse(await fs.readFile(manifestPath,'utf8'));
const served=new Map([['/source-bundle/manifest.json',manifestPath]]),inputPins={[manifestPath]:await sha(manifestPath)};
for(const record of manifest.cases){inputPins[record.source_path]=record.source_sha256;
 for(const item of [...record.groups.flatMap(g=>Object.values(g.arrays)),record.opaque.positions,record.opaque.indices]){
  const file=path.resolve(bundle,record.directory,item.path);assert(file.startsWith(bundle+path.sep),'Array path escapes bundle');
  assert.equal(await sha(file),item.sha256,'Bundle array pin mismatch');served.set(`/source-bundle/${record.directory}/${item.path}`,file);inputPins[file]=item.sha256;}}
for(const [file,digest]of Object.entries(inputPins))assert.equal(await sha(file),digest,`Input pin mismatch: ${file}`);
const implementationFiles=['qa/source-optical-groups.html','qa/source-optical-groups.mjs','qa/source-optical-groups-browser.mjs',
 'qa/effective-optical-groups-browser.mjs','qa/taylor24-angle-override.mjs','src/eyewear/lens-appearance.ts','src/render/layer-overflow.ts','package-lock.json'];
const codePins=async()=>Object.fromEntries(await Promise.all(implementationFiles.map(async file=>[file,await sha(path.join(root,file))])));
const before=await codePins(),errors=[],warnings=[],httpErrors=[];
const prototype=await fs.readFile(path.join(root,'qa/effective-optical-groups-browser.mjs'),'utf8'),override=overrideAngleMath(prototype,angleMath);
assert.equal(override.receipt.sourceSha256,before['qa/effective-optical-groups-browser.mjs']);
let transformedModules=0;
const bridge='\nexport {Transport,makeCamera,descriptor,BACKGROUND,OPAQUE,COLOR_TOLERANCE,DEPTH_TOLERANCE};\n';
const server=await createServer({configFile:false,root,plugins:[{name:'pinned-source-qa-export-bridge',enforce:'pre',
 transform(code,id){if(id.replaceAll('\\','/').endsWith('/qa/effective-optical-groups-browser.mjs?sourcePrototype')){assert.equal(code,prototype,'Prototype changed before shader override');transformedModules++;return {code:override.code+bridge,map:null};}},
 configureServer(server){server.middlewares.use(async(req,res,next)=>{const pathname=new URL(req.url,'http://localhost').pathname,file=served.get(pathname);if(!file)return next();
  try{const bytes=await fs.readFile(file);res.setHeader('Content-Type',pathname.endsWith('.json')?'application/json':'application/octet-stream');res.end(bytes);}
  catch(error){res.statusCode=500;res.end(String(error));}});}}],server:{host:'127.0.0.1',port:0,hmr:false}});
let browser,report;
try{await server.listen();browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage({viewport:{width:1500,height:1000}});page.setDefaultTimeout(600000);
 page.on('response',response=>{if(response.status()>=400)httpErrors.push({url:response.url(),status:response.status()});});
 page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error')errors.push(message.text());if(message.type()==='warning')warnings.push(message.text());});
 await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/qa/source-optical-groups.html`);await page.waitForFunction(()=>!!window.sourceOpticalGroups);
 try{report=await page.evaluate(()=>window.sourceOpticalGroups.run());}catch(error){report={...await page.evaluate(()=>window.sourceGroupReport??{}),status:'failed',harnessError:String(error.stack??error)};}
 for(const [index,item]of (report.cases??[]).entries()){if(item.image){const name=`${String(index).padStart(2,'0')}-${item.source}-${item.pose}-${item.control}.png`;
  const bytes=Buffer.from(item.image.split(',')[1],'base64');await fs.writeFile(path.join(output,name),bytes);item.image={path:name,sha256:crypto.createHash('sha256').update(bytes).digest('hex')};}}
 report.createdAt=new Date().toISOString();report.browserVersion=browser.version();
 report.angleMath=angleMath;report.shaderOverride={...override.receipt,transformedModules};
 report.timingScope={performanceBenchmark:false,measurement:'One synchronized draw per case; includes GPU submission and synchronous capacity checks.',
  concurrentWork:option('timing-context','Unspecified; timing is diagnostic only.')};
 report.implementation=before;report.implementationAfter=await codePins();report.bridge={source:'qa/effective-optical-groups-browser.mjs',
  sourceSha256:before['qa/effective-optical-groups-browser.mjs'],appendOnlyExports:bridge,productionFilesModified:false,syntheticHarnessModified:false};
 report.sourceSnapshotStable=JSON.stringify(before)===JSON.stringify(report.implementationAfter);report.inputPins=inputPins;
 report.inputPinsStable=(await Promise.all(Object.entries(inputPins).map(async([file,digest])=>(await sha(file))===digest))).every(Boolean);
 report.errors=errors;report.warnings=warnings;report.httpErrors=httpErrors;if(!report.sourceSnapshotStable||!report.inputPinsStable||errors.length||httpErrors.length||transformedModules<1)report.status='failed';
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');await page.screenshot({path:path.join(output,'contact-sheet.png'),fullPage:true});
 console.log(JSON.stringify({status:report.status,strictNumericStatus:report.strictNumericStatus,cases:report.cases?.length,
  maximumColorError:report.maximumColorError,maximumDepthError:report.maximumDepthError,sourceSnapshotStable:report.sourceSnapshotStable,inputPinsStable:report.inputPinsStable,
  harnessError:report.harnessError,errors,warnings},null,2));
 assert.equal(report.status,'measured','Real-source QA execution failed');assert.equal(report.strictNumericStatus,'passed','Original strict numeric targets were not met; report retained without threshold changes');
}finally{await browser?.close();await server.close();}
