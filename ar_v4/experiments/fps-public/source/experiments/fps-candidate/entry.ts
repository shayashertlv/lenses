import {FPS_OPTIONS, FPS_MODES} from './runtime/options.ts';
import type {FpsMode} from './runtime/options.ts';
import {installStudy} from './study.ts';

export const LABELS:Record<FpsMode,string>={g:'G · Current baseline',queries:'Test 1 · Fewer graphics queries',identity:'Test 2 · Live frame IDs',combined:'Test 3 · Queries + frame IDs',
  compose:'Test 4 · CPU pixel processing',bookkeeping:'Test 5 · Lighter statistics updates','next-combined':'Test 6 · Both CPU changes'};
const descriptions:Record<FpsMode,string>={
  g:'The reviewed G renderer and frame pipeline, with the same iPhone camera capture correction as every test.',
  queries:'Reduces repeated graphics-state queries. Failed reads, cancellation and final visual checks remain protected.',
  identity:'Pairs live work with an owned frame ID. Exact content hashes are calculated when you hold or export an image.',
  combined:'Combines the graphics-query and live-frame identity changes. Compare FPS together with hair coverage and frame age.',
  compose:'Tests cheaper pixel comparisons and composition with the same output and full checks on every completed image.',
  bookkeeping:'Reduces repeated statistics work. Every frame is still recorded; the detailed on-screen summary updates twice per second.',
  'next-combined':'Combines CPU pixel processing and lighter statistics updates. Original graphics queries and frame hashes remain active.',
};
async function main():Promise<void>{
  // Both graphs are isolated public snapshots with the same camera capture correction.
  if(FPS_OPTIONS.mode==='g')await import('../speed-lab/live-main.ts');
  else await import('./runtime/experiments/speed-lab/live-main.ts');
  const select=document.querySelector<HTMLSelectElement>('#fps-mode')!;
  select.value=FPS_OPTIONS.mode;
  document.querySelector('#fps-mode-label')!.textContent=LABELS[FPS_OPTIONS.mode];
  document.querySelector('#fps-mode-description')!.textContent=descriptions[FPS_OPTIONS.mode];
  document.querySelector('#fps-build')!.textContent=`Preview build ${(import.meta.env.VITE_FPS_BUILD_ID??'local-development').slice(0,12)}`;
  document.querySelector('.stage')!.setAttribute('data-fps-mode',FPS_OPTIONS.mode);
  const params=new URLSearchParams(location.search);
  for(const [param,id] of [['eyewear','eyewear-select'],['hairModel','hair-model-select'],['variant','variant-select']] as const){
    const control=document.getElementById(id) as HTMLSelectElement,value=params.get(param);
    if(value&&[...control.options].some(option=>option.value===value)){control.value=value;control.dispatchEvent(new Event('change'));}
  }
  select.addEventListener('change',()=>{
    const next=FPS_MODES.find(value=>value===select.value);if(!next)return;
    const stage=document.querySelector<HTMLElement>('.stage')!;
    const wasLive=['tracking','searching','starting'].includes(stage.dataset.state??'');
    document.querySelector<HTMLButtonElement>('#stop')!.click();
    navigate(next,wasLive);
  });
  const studyResumed=installStudy(FPS_OPTIONS.mode,LABELS,navigate);
  if(!studyResumed&&params.get('start')==='1')document.querySelector<HTMLButtonElement>('#start')!.click();
}
function navigate(mode:FpsMode,start:boolean):void{
  const next=new URL(location.href);next.search='';next.searchParams.set('fps',mode);
  for(const [param,id] of [['eyewear','eyewear-select'],['hairModel','hair-model-select'],['variant','variant-select']] as const)
    next.searchParams.set(param,(document.getElementById(id) as HTMLSelectElement).value);
  if(start)next.searchParams.set('start','1');
  location.assign(next.href);
}
void main().catch(error=>{
  document.querySelector('#fps-study-status')!.textContent=`The comparison could not start: ${error instanceof Error?error.message:String(error)}`;
});
