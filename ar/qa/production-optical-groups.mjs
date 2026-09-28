/** Local actual-production GPU conformance. No provider/camera/publication. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(v=>v.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const output=path.resolve(root,option('output','qa/output/production-optical-groups-v1'));
const mode=option('mode','conformance');assert(['conformance','arithmetic'].includes(mode),'Unknown harness mode');
await fs.mkdir(output,{recursive:true});assert.equal((await fs.readdir(output)).length,0,'Use a new/empty output directory');
const files=['qa/production-optical-groups.mjs','qa/production-optical-groups.html','qa/production-optical-groups-browser.mjs','qa/production-optical-groups-oracle.mjs','qa/production-optical-groups-arithmetic.mjs','qa/viewport-subpixel-probe.mjs',
 'src/render/lens-material.ts','src/render/lens-layers.ts','src/render/nearest-optical-groups.ts','src/render/effective-optical-topology.ts',
 'src/render/eyewear-shadow.ts','src/render/layer-overflow.ts','src/render/opaque-display.ts','src/render/optical-topology.ts',
 'src/eyewear/lens-appearance.ts','src/eyewear/optical-material.ts','package-lock.json'];
const snapshot=async()=>Object.fromEntries(await Promise.all(files.map(async file=>[file,crypto.createHash('sha256').update(await fs.readFile(path.join(root,file))).digest('hex')])));
const before=await snapshot(),errors=[],warnings=[],failedResponses=[];
const server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false}});let browser,report;
try{await server.listen();browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage({viewport:{width:1100,height:900}});page.setDefaultTimeout(180000);
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());if(m.type()==='warning')warnings.push(m.text());});
 page.on('response',r=>{if(r.status()>=400)failedResponses.push({url:r.url(),status:r.status()});});
 await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/qa/production-optical-groups.html`);await page.waitForFunction(()=>!!window.productionOpticalGroups);
 try{report=await page.evaluate(mode=>window.productionOpticalGroups.run(mode),mode);}catch(error){report={...await page.evaluate(()=>window.productionGroupReport??{}),status:'failed',harnessError:String(error.stack??error)};}
 report.createdAt=new Date().toISOString();report.browserVersion=browser.version();report.errors=errors;report.warnings=warnings;report.failedResponses=failedResponses;
 report.implementation=before;report.implementationAfter=await snapshot();report.sourceSnapshotStable=JSON.stringify(before)===JSON.stringify(report.implementationAfter);
 if(errors.length||failedResponses.length||!report.sourceSnapshotStable)report.status='failed';
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');await page.screenshot({path:path.join(output,'contact-sheet.png'),fullPage:true});
 console.log(JSON.stringify({status:report.status,cases:report.cases?.length,shadowCases:report.shadowCases?.length,maximumColorError:report.maximumColorError,maximumDepthError:report.maximumDepthError,maximumReceiverError:report.maximumReceiverError,coverageComplete:report.coverageComplete,receiverCoverageComplete:report.receiverCoverageComplete,capacity:report.capacity,sourceSnapshotStable:report.sourceSnapshotStable,errors,warnings,harnessError:report.harnessError},null,2));
 assert.equal(report.status,mode==='arithmetic'?'diagnostic':'passed','Actual production effective-group conformance failed; evidence retained without threshold changes');
}finally{await browser?.close();await server.close();}
