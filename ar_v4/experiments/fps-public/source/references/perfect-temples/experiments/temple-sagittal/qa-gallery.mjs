import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';
const out='.recovery/temple-rethink-2026-09-08/gallery-qa-'+new Date().toISOString().replaceAll(':','-');await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1480,height:1100}}),errors=[],responses=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400)responses.push({url:r.url(),status:r.status()});});
try{
 await page.goto('http://127.0.0.1:8040/experiments/temple-sagittal/comparison.html');
 const checked=[];
 for(const evidence of ['recorded','synthetic'])for(const model of ['tom-ford-clear','amber-horizon']){
  await page.selectOption('#evidence',evidence);await page.selectOption('#model',model);const frames=await page.locator('#frame option').evaluateAll(items=>items.map(i=>i.value));assert.equal(frames.length,evidence==='recorded'?16:6);
  for(const frame of frames){await page.selectOption('#frame',frame);await page.evaluate(async()=>{await Promise.all(['before','after'].map(async id=>{const img=new Image();img.src=document.getElementById(id).querySelector('image').getAttribute('href');await img.decode();}));});checked.push({evidence,model,frame});}
 }
 await page.selectOption('#evidence','recorded');await page.selectOption('#model','tom-ford-clear');await page.selectOption('#frame','flicker_recording-70');await page.screenshot({path:out+'/recorded.png',fullPage:true});
 await page.selectOption('#area','nose');await page.screenshot({path:out+'/nose.png',fullPage:true});
 await page.selectOption('#evidence','synthetic');await page.selectOption('#area','head');await page.selectOption('#frame','synthetic-down50-yaw0');await page.screenshot({path:out+'/synthetic.png',fullPage:true});
 await page.goto('http://127.0.0.1:8040/experiments/temple-sagittal/live.html');await page.locator('#variant-select').waitFor();assert.equal(await page.locator('#hold-frame').isDisabled(),true);await page.screenshot({path:out+'/live-idle.png',fullPage:true});
 assert.deepEqual(errors,[]);assert.deepEqual(responses,[]);await fs.writeFile(out+'/report.json',JSON.stringify({checked,errors,responses,cameraStarted:false},null,2));console.log(out+'/report.json');
}finally{await browser.close();}
