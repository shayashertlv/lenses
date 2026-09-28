/** Explicit math-override runner for the unchanged synthetic group harness. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {overrideAngleMath} from './taylor24-angle-override.mjs';
import {overrideRasterArithmetic} from './raster-arithmetic-override.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(x=>x.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const mode=option('angle-math','builtin');assert(['builtin','taylor24'].includes(mode),'angle-math must be builtin or taylor24');
const rasterMode=option('raster-arithmetic','baseline');assert(['baseline','float32'].includes(rasterMode),'raster-arithmetic must be baseline or float32');
const output=path.resolve(root,option('output',`qa/output/effective-optical-groups-${mode}-override`));
await fs.mkdir(output,{recursive:true});assert.equal((await fs.readdir(output)).length,0,'Use a new/empty output directory');
const files=['qa/effective-optical-groups-angle.mjs','qa/taylor24-angle-override.mjs','qa/raster-arithmetic-override.mjs','qa/effective-optical-groups.html',
 'qa/effective-optical-groups-browser.mjs','src/eyewear/lens-appearance.ts','src/render/layer-overflow.ts',
 'src/render/lens-layers.ts','src/render/lens-material.ts','src/render/eyewear-shadow.ts','package-lock.json'];
const sha=source=>crypto.createHash('sha256').update(source).digest('hex');
const pins=async()=>Object.fromEntries(await Promise.all(files.map(async file=>[file,sha(await fs.readFile(path.join(root,file)))])));
const before=await pins(),source=await fs.readFile(path.join(root,'qa/effective-optical-groups-browser.mjs'),'utf8'),override=overrideAngleMath(source,mode);
assert.equal(before['qa/effective-optical-groups-browser.mjs'],override.receipt.sourceSha256);
const rasterOverride=overrideRasterArithmetic(override.code,rasterMode);
assert.equal(override.receipt.transformedSourceSha256,rasterOverride.receipt.sourceSha256);
const errors=[],warnings=[];
let transformedModules=0;
const server=await createServer({configFile:false,root,plugins:[{name:'isolated-fixed-angle-override',enforce:'pre',transform(code,id){
 if(id.replaceAll('\\','/').endsWith('/qa/effective-optical-groups-browser.mjs')){assert.equal(code,source,'Synthetic prototype changed before override');transformedModules++;return {code:rasterOverride.code,map:null};}}}],server:{host:'127.0.0.1',port:0,hmr:false}});
let browser,report;
try{await server.listen();browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage({viewport:{width:1100,height:900}});page.setDefaultTimeout(180000);
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());if(m.type()==='warning')warnings.push(m.text());});
 await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/qa/effective-optical-groups.html`);await page.waitForFunction(()=>!!window.effectiveOpticalGroups);
 try{report=await page.evaluate(()=>window.effectiveOpticalGroups.run());}catch(error){report={...await page.evaluate(()=>window.groupReport??{}),status:'failed',harnessError:String(error.stack??error)};}
 report.createdAt=new Date().toISOString();report.browserVersion=browser.version();report.angleMath=mode;report.shaderOverride={...override.receipt,transformedModules};
 report.rasterArithmetic=rasterMode;report.rasterArithmeticOverride={...rasterOverride.receipt,transformedModules};
 if(rasterMode==='float32')report.rasterArithmeticDiagnostics={...await page.evaluate(()=>globalThis.__qaRasterArithmetic),
  scope:'CPU-only arithmetic alternatives recorded before any GPU residual is inspected. One fixed separate multiply/add path defines this experimental reference for every vertex. Counts include repeated authored vertices across case invocations.',
  alternatePath:'Fused-like comparison rounds the running sum after an unrounded double product; it is diagnostic, not an exhaustive GPU arithmetic envelope or an exact FMA proof.',
  uncertainty:'Different snaps are possible under these arithmetic variants. Passing this device-qualified path does not establish portable conformance for all shader compilers or GPUs.'};
 if(rasterMode==='float32')report.rasterReference={...report.rasterReference,arithmetic:rasterOverride.receipt.arithmetic,limitations:rasterOverride.receipt.limitations};
 report.implementation=before;report.implementationAfter=await pins();report.sourceSnapshotStable=JSON.stringify(before)===JSON.stringify(report.implementationAfter);
 report.timingScope={performanceBenchmark:false,reason:'Concurrent independent CPU work; timings retained as diagnostics only.'};
 report.errors=errors;report.warnings=warnings;if(!report.sourceSnapshotStable||errors.length||transformedModules<1)report.status='failed';
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');await page.screenshot({path:path.join(output,'contact-sheet.png'),fullPage:true});
 console.log(JSON.stringify({status:report.status,angleMath:mode,rasterArithmetic:rasterMode,cameraCases:report.cases?.length,lightCases:report.lightCases?.length,
  maximumColorError:report.maximumColorError,maximumDepthError:report.maximumDepthError,receiverMaximumError:report.receiverMaximumError,
  sourceSnapshotStable:report.sourceSnapshotStable,errors,warnings,harnessError:report.harnessError},null,2));
 assert.equal(report.status,'passed','Original strict synthetic targets were not met; report retained without threshold changes');
}finally{await browser?.close();await server.close();}
