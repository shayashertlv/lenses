import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';
import type {} from './live-main.ts';

const entry='/ar_testing/experiments/efficiency-lab/live.html?study=fps-review';
interface StartupAudit {cameraRequests:number;workers:number;}
declare global {interface Window {
  refreshControlsAudit:StartupAudit;
  recordRefreshStartupSideEffect(kind:string):Promise<void>;
}}

/** Navigation/control verification only. No camera fixture, inference, renderer,
 * resource warmup or synthetic performance measurements run in these cases. */
async function forbidStartup(page:Page):Promise<{effects:string[];errors:string[]}> {
  const effects:string[]=[],errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.exposeFunction('recordRefreshStartupSideEffect',(kind:string)=>{effects.push(kind);});
  await page.addInitScript(()=>{
    const audit:StartupAudit={cameraRequests:0,workers:0};window.refreshControlsAudit=audit;
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:async()=>{
      audit.cameraRequests++;void window.recordRefreshStartupSideEffect('camera');
      throw new Error('Camera startup is forbidden in refresh-control verification.');
    }});
    window.Worker=new Proxy(window.Worker,{construct(){
      audit.workers++;void window.recordRefreshStartupSideEffect('worker');
      throw new Error('Worker startup is forbidden in refresh-control verification.');
    }});
  });
  return {effects,errors};
}

async function cameraOff(page:Page,audit:{effects:string[];errors:string[]}):Promise<void> {
  await expect(page.locator('.stage')).toHaveAttribute('data-state','idle');
  await expect(page.locator('#start')).toBeVisible();await expect(page.locator('#stop')).toBeHidden();
  expect(await page.evaluate(()=>window.refreshControlsAudit)).toEqual({cameraRequests:0,workers:0});
  expect(await page.evaluate(()=>window.hairLivePreview.diagnostics())).toMatchObject({
    sessionId:null,phase:null,runtimeGeneration:null,runtimeReady:false,presented:null,performanceSample:null,
  });
  expect(audit.effects).toEqual([]);expect(audit.errors).toEqual([]);
}

async function refresh(page:Page):Promise<{url:string;timeOrigin:number;navigationType:string|null}> {
  const before=await page.evaluate(()=>performance.timeOrigin);
  await Promise.all([page.waitForEvent('domcontentloaded'),page.locator('#refresh-pipeline').click()]);
  await expect(page.locator('#refresh-pipeline')).toBeVisible();
  const observation=await page.evaluate(()=>({url:location.href,timeOrigin:performance.timeOrigin,
    navigationType:(performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming|undefined)?.type??null}));
  expect(observation.timeOrigin).toBeGreaterThan(before);
  return observation;
}

test('refresh selected test preserves the workload and actually reloads a second identical URL while camera stays off',async({page})=>{
  const audit=await forbidStartup(page);await page.goto(entry);
  await expect(page.locator('#pipeline-select')).toHaveValue('g');
  await expect(page.locator('#runtime-policy')).toHaveText('Fresh runtime for each switch. Setup time is separate from measured FPS.');
  await expect(page.locator('#continuous-title')).toHaveText('All six options · 7 minutes + setup');
  await expect(page.locator('#continuous-start')).toHaveText('Measure only · all six options · ~7 min + setup');
  await expect(page.locator('#continuous-protocol')).toContainText('released and rebuilt before every window, including repeated options');
  expect(await page.evaluate(()=>window.hairLivePreview.diagnostics().runtimeIsolation)).toBe('fresh-runtime');
  await cameraOff(page,audit);
  await page.selectOption('#pipeline-select','reuse-compose');
  await page.selectOption('#eyewear-select','tom-ford-clear');
  await page.selectOption('#hair-model-select','selfie-multiclass');
  await page.selectOption('#variant-select','accepted');await page.selectOption('#power-context','plugged-in');
  const first=await refresh(page);
  expect(Object.fromEntries(new URL(first.url).searchParams)).toEqual({study:'fps-review',pipeline:'reuse-compose',
    eyewear:'tom-ford-clear','hair-model':'selfie-multiclass',variant:'accepted',power:'plugged-in'});
  for(const [id,value] of [['pipeline-select','reuse-compose'],['eyewear-select','tom-ford-clear'],
    ['hair-model-select','selfie-multiclass'],['variant-select','accepted'],['power-context','plugged-in']] as const)
    await expect(page.locator('#'+id)).toHaveValue(value);
  await cameraOff(page,audit);
  const second=await refresh(page);expect(second.url).toBe(first.url);expect(second.navigationType).toBe('reload');
  expect(second.timeOrigin).toBeGreaterThan(first.timeOrigin);
  await expect(page.locator('#pipeline-select')).toHaveValue('reuse-compose');
  await expect(page.locator('#eyewear-select')).toHaveValue('tom-ford-clear');
  await expect(page.locator('#hair-model-select')).toHaveValue('selfie-multiclass');
  await expect(page.locator('#variant-select')).toHaveValue('accepted');
  await expect(page.locator('#power-context')).toHaveValue('plugged-in');
  await cameraOff(page,audit);
});

test('explicit shared diagnostic retains its policy across refresh and earlier G/V has no refresh control',async({page})=>{
  const audit=await forbidStartup(page);
  await page.goto(entry+'&switch=shared&pipeline=face-cpu&eyewear=amber-horizon&hair-model=hair-only&variant=hair&power=battery');
  await expect(page.locator('#runtime-policy')).toHaveText('Shared runtime diagnostic: previous workers and graphics caches stay allocated. Do not pool these results with fresh-runtime runs.');
  await expect(page.locator('#study-intro')).toContainText('Shared-runtime switch diagnostic');
  await expect(page.locator('#continuous-protocol')).not.toContainText('released and rebuilt');
  expect(await page.evaluate(()=>window.hairLivePreview.diagnostics().runtimeIsolation)).toBe('shared-runtime');
  const before=page.url(),reloaded=await refresh(page);expect(reloaded.url).toBe(before);
  expect(reloaded.navigationType).toBe('reload');
  for(const [id,value] of [['pipeline-select','face-cpu'],['eyewear-select','amber-horizon'],
    ['hair-model-select','hair-only'],['variant-select','hair'],['power-context','battery']] as const)
    await expect(page.locator('#'+id)).toHaveValue(value);
  await expect(page.locator('#runtime-policy')).toContainText('Shared runtime diagnostic');
  await cameraOff(page,audit);
  await page.click('#mask-preview-study');await expect(page.locator('#pipeline-select')).toHaveValue('g');
  await expect(page.locator('#refresh-pipeline')).toBeHidden();
  await expect(page.locator('#runtime-policy')).toBeEmpty();await cameraOff(page,audit);
});
