import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {DEFAULT_PIPELINE, PIPELINE_LABELS} from '../profiles.ts';
const base=process.env.SPEED_LAB_BASE_URL ?? 'http://127.0.0.1:8083';
const out=path.resolve('experiments/speed-lab/qa/output',`layout-${new Date().toISOString().replaceAll(':','-')}`);
await mkdir(out,{recursive:true});
const browser=await chromium.launch();
const results=[];
try {
  for(const width of [1440,390,360]) {
    const page=await browser.newPage({viewport:{width,height:1000}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/experiments/speed-lab/live.html');
    await page.locator('#eyewear-select option').first().waitFor({state:'attached'});
    assert.equal(await page.locator('#pipeline-select').inputValue(),DEFAULT_PIPELINE);
    assert.equal(await page.locator('#pipeline-select option').count(),8);
    assert.equal(await page.locator('.stage').getAttribute('data-state'),'idle');
    const size=await page.evaluate(()=>({viewport:innerWidth,page:document.documentElement.scrollWidth}));
    assert.ok(size.page<=size.viewport,`Horizontal overflow at ${width}`);
    await page.selectOption('#pipeline-select','base');
    assert.equal(await page.locator('#active-pipeline').textContent(),PIPELINE_LABELS.base);
    await page.selectOption('#pipeline-select','combined');
    assert.equal(await page.locator('#active-pipeline').textContent(),PIPELINE_LABELS.combined);
    await page.screenshot({path:path.join(out,`camera-off-${width}.png`),fullPage:true});
    assert.deepEqual(errors,[]);results.push({width,...size,cameraOff:true,eightProfiles:true,errors});
    await page.close();
  }
  await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,base,results},null,2));
  console.log(JSON.stringify({passed:true,out,results}));
} finally {await browser.close();}
