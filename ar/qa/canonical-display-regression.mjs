/** Run actual display shader and an optional old-gate negative control. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(v=>v.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const output=path.resolve(root,option('output','qa/output/canonical-display-regression'));
const oldGate=option('old-gate','false')==='true',digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
await fs.mkdir(output,{recursive:true});assert.equal((await fs.readdir(output)).length,0,'Use a new output directory');
const files=['qa/canonical-display-regression.mjs','qa/canonical-display-regression.html','qa/canonical-display-regression-browser.mjs',
 'src/render/lens-material.ts','src/render/lens-layers.ts','src/render/nearest-optical-groups.ts','src/render/effective-optical-topology.ts',
 'src/render/optical-topology.ts','src/eyewear/lens-appearance.ts','src/eyewear/optical-material.ts'];
const snapshot=async()=>Object.fromEntries(await Promise.all(files.map(async file=>[file,digest(await fs.readFile(path.join(root,file)))])));
const before=await snapshot(),errors=[],overrides=[],failedResponses=[];
const server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false},plugins:[{
 name:'old-display-gate-negative-control',enforce:'pre',transform(source,id){if(!oldGate||!id.replaceAll('\\','/').endsWith('/src/render/lens-material.ts'))return;
  const marker='if (uCanonicalGroupEnabled && uCanonicalLayerMode < 1.5) {';assert.equal(source.split(marker).length-1,1);
  const code=source.replace(marker,'if (uCanonicalGroupEnabled) {');overrides.push({source_sha256:digest(source),transformed_sha256:digest(code)});return {code,map:null};}
}]});let browser;
try{await server.listen();browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage();page.setDefaultTimeout(180000);page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
 page.on('response',response=>{if(response.status()>=400)failedResponses.push({url:response.url(),status:response.status()});});
 await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/qa/canonical-display-regression.html`);await page.waitForFunction(()=>!!window.displayRegression);
 let report;try{report=await page.evaluate(()=>window.displayRegression.run());}catch(error){report={...await page.evaluate(()=>window.displayRegressionReport??{}),status:'failed',harnessError:String(error.stack??error)};}
 Object.assign(report,{old_gate_negative_control:oldGate,overrides,implementation:before,implementation_after:await snapshot(),errors,failedResponses});
 report.source_snapshot_stable=JSON.stringify(before)===JSON.stringify(report.implementation_after);
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({status:report.status,oldGate,cases:report.cases?.length,failed_samples:report.failed_samples,maximum_error:report.maximum_error,source_snapshot_stable:report.source_snapshot_stable,errors,harnessError:report.harnessError},null,2));
 assert(report.source_snapshot_stable&&!errors.length&&!failedResponses.length&&!report.harnessError,'Source stability/browser checks failed');
 assert.equal(report.cases.length,15,'Every fixed regression condition must complete');
 if(oldGate)assert(report.failed_samples>0,'Negative control must fail measured pixels, not harness setup');
 assert.equal(report.status,oldGate?'failed':'passed',oldGate?'Old gate must fail this regression':'MSAA display-copy regression failed');
}finally{await browser?.close();await server.close();}
