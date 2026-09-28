/** Standalone local GPU proof. Production renderer files are read-only inputs. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(value=>value.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const output=path.resolve(root,option('output','qa/output/effective-optical-groups-v1'));
await fs.mkdir(output,{recursive:true});
const files=['qa/effective-optical-groups.html','qa/effective-optical-groups.mjs','qa/effective-optical-groups-browser.mjs',
 'src/eyewear/lens-appearance.ts','src/render/layer-overflow.ts','src/render/lens-layers.ts','src/render/lens-material.ts','src/render/eyewear-shadow.ts'];
const hashes=async()=>Object.fromEntries(await Promise.all(files.map(async file=>[file,crypto.createHash('sha256').update(await fs.readFile(path.join(root,file))).digest('hex')])));
const before=await hashes(),errors=[],warnings=[];
const server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false}});
let browser,report;
try{
 await server.listen();
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage({viewport:{width:1100,height:900}});page.setDefaultTimeout(180000);
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());if(message.type()==='warning')warnings.push(message.text());});
 await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/qa/effective-optical-groups.html`);
 await page.waitForFunction(()=>!!window.effectiveOpticalGroups);
 try{report=await page.evaluate(()=>window.effectiveOpticalGroups.run());}
 catch(error){report={...await page.evaluate(()=>window.groupReport??{}),status:'failed',harnessError:String(error.stack??error)};}
 report.createdAt=new Date().toISOString();report.implementation=before;report.implementationAfter=await hashes();
 report.sourceSnapshotStable=JSON.stringify(before)===JSON.stringify(report.implementationAfter);
 report.errors=errors;report.warnings=warnings;if(!report.sourceSnapshotStable||errors.length)report.status='failed';
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
 await page.screenshot({path:path.join(output,'contact-sheet.png'),fullPage:true});
 console.log(JSON.stringify({status:report.status,cases:report.cases?.length,maximumColorError:report.maximumColorError,
  maximumDepthError:report.maximumDepthError,receiverMaximumError:report.receiverMaximumError,timing:report.timing,
  capacity:report.capacity,sourceSnapshotStable:report.sourceSnapshotStable,harnessError:report.harnessError,errors,warnings},null,2));
 assert.equal(report.status,'passed','Experimental effective-group GPU proof failed');
}finally{await browser?.close();await server.close();}
