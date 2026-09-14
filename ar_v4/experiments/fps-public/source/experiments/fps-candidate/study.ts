import type {FpsMode} from './runtime/options.ts';

interface TimingRow {
  serial:number;sessionId:string;capturedAtMs:number;publishedAtMs:number;totalMs:number;
  hasFace:boolean;hasMask:boolean;sourceWidth:number;sourceHeight:number;
  videoPresentedFrames:number|null;composeMs:number;finalChecksMs:number;
  sourceHashMs:number;faceInferenceMs:number|null;hairInferenceMs:number|null;hairExtractionMs:number|null;
  faceDelegate:string|null;hairDelegate:string|null;
  fallback:string|null;native:Record<string,number|boolean|string|null>|null;
}
interface Segment {
  mode:FpsMode;timeOriginMs:number;startedAt:string;rows:TimingRow[];
  fps:number;ageP95:number;intervalP95:number;gaps100:number;trackedFraction:number;maskCoverage:number|null;
}
interface Study {
  schema:'ar-fps-candidate-comparison-v1';id:string;createdAt:string;status:'running'|'complete'|'stopped';reason:string|null;
  buildId:string;buildAt:string|null;
  order:FpsMode[];index:number;segments:Segment[];eyewear:string;hairModel:string;variant:string;
  policy:string;warmupMs:number;measurementMs:number;
  partial?:{mode:FpsMode;timeOriginMs:number;rows:TimingRow[];reason:string};
}
const KEY='ar-fps-candidate-study-v1';
const BUILD_ID=import.meta.env?.VITE_FPS_BUILD_ID??'local-development';
const BUILD_AT=import.meta.env?.VITE_FPS_BUILD_AT??null;
const percentile=(values:number[],q:number):number=>{const sorted=values.slice().sort((a,b)=>a-b);return sorted[Math.max(0,Math.ceil(sorted.length*q)-1)]??0;};
const element=<T extends HTMLElement=HTMLElement>(id:string):T=>document.getElementById(id) as T;
const selection=(id:string):string=>element<HTMLSelectElement>(id).value;

export function installStudy(mode:FpsMode,labels:Record<FpsMode,string>,navigate:(mode:FpsMode,start:boolean)=>void):boolean{
  let study:Study|null=null,timer:ReturnType<typeof setInterval>|undefined;
  try{const saved=sessionStorage.getItem(KEY);if(saved){const parsed=JSON.parse(saved) as Study;if(parsed.schema==='ar-fps-candidate-comparison-v1')study=parsed;}}catch{/* Session-only history is optional. */}
  let serial=0,openedAt=performance.now(),preparingAt:number|null=null,firstAt:number|null=null,measureAt:number|null=null,validWarm=0,lastAt=openedAt,segmentSessionId:string|null=null,lastPublishedAt=0,storageFailed=false;
  let exportUrl:string|null=null,navigating=false;
  const rows:TimingRow[]=[];
  const navigateTo=(next:FpsMode):void=>{navigating=true;navigate(next,true);};
  const save=():void=>{
    if(!study)return;if(storageFailed)throw new Error('Comparison storage is unavailable; download the current report before leaving this page');
    try{sessionStorage.setItem(KEY,JSON.stringify(study));}
    catch(error){storageFailed=true;try{sessionStorage.removeItem(KEY);}catch{/* Prevent stale resume whenever storage permits. */}throw error;}
  };
  const show=():void=>{
    element('fps-study-cancel').hidden=study?.status!=='running';
    element<HTMLButtonElement>('fps-study-start').disabled=study?.status==='running'||mode==='g';
    element('fps-study-download').hidden=!study;
    element('fps-study-continue').hidden=study?.status!=='running'||firstAt!==null;
    element('fps-study-results').replaceChildren();
    for(const segment of study?.segments??[]){
      const line=document.createElement('div');
      const coverage=study?.variant!=='hair'?'off':segment.maskCoverage===null?'no tracked frames':Math.round(segment.maskCoverage*100)+'%';
      line.textContent=`${labels[segment.mode]}: ${segment.fps.toFixed(2)} fps · tracked ${Math.round(segment.trackedFraction*100)}% · hair ${coverage} · age p95 ${Math.round(segment.ageP95)} ms`;
      element('fps-study-results').append(line);
    }
    if(study?.status==='complete')element('fps-study-status').textContent='Comparison complete. Review FPS, hair availability and frame age together. Repeat with both glasses and both hair models.';
    else if(study?.status==='stopped')element('fps-study-status').textContent=`Comparison stopped: ${study.reason??'requested'}. Completed segments remain available.`;
    else if(!study&&mode==='g')element('fps-study-status').textContent='Choose a test to run G / test / test / G. Each segment has 5 seconds of warmup and 30 seconds of measurement.';
  };
  const stop=(reason:string):void=>{
    if(timer!==undefined)clearInterval(timer);timer=undefined;
    if(study?.status==='running'){
      study.status='stopped';study.reason=reason;
      if(rows.length)study.partial={mode,timeOriginMs:performance.timeOrigin,rows,reason};
      try{save();}catch{/* Still allow current-page download. */}
    }
    show();
  };
  element('fps-study-cancel').addEventListener('click',()=>stop('Stopped by you'));
  element('fps-mode').addEventListener('change',()=>stop('Test changed manually'));
  for(const id of ['eyewear-select','hair-model-select','variant-select'])element(id).addEventListener('change',()=>stop('Glasses, hair model or hair setting changed'));
  for(const id of ['stop','hold-frame'])element(id).addEventListener('click',()=>stop('Camera closed or held'));
  document.addEventListener('visibilitychange',()=>{
    // Full document navigation hides the outgoing page too. Its segment was
    // saved before navigateTo; do not overwrite the next segment as stopped.
    if(document.hidden&&!navigating)stop('The preview moved to the background');
  });
  window.addEventListener('pagehide',()=>{
    if(!navigating)stop('The comparison page was closed or reloaded');
    if(timer!==undefined)clearInterval(timer);timer=undefined;
    if(exportUrl)URL.revokeObjectURL(exportUrl);exportUrl=null;
  });
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  element('fps-study-download').addEventListener('click',()=>{
    if(!study)return;
    const report=study.status==='running'&&rows.length?{...study,partial:{mode,timeOriginMs:performance.timeOrigin,rows,reason:'Measurement in progress at export'}}:study;
    const json=JSON.stringify(report,null,2),previousUrl=exportUrl;
    exportUrl=URL.createObjectURL(new Blob([json],{type:'application/json'}));
    const link=element<HTMLAnchorElement>('fps-study-save');
    link.href=exportUrl;link.download=`ar-fps-comparison-${study.id}.json`;link.hidden=false;
    element<HTMLTextAreaElement>('fps-study-json').value=json;
    element<HTMLButtonElement>('fps-study-copy').disabled=false;
    element<HTMLDetailsElement>('fps-study-export').open=true;
    element('fps-study-export-status').textContent='Full comparison prepared, including every completed segment and any current partial segment. Save the JSON file or select the text to copy it. Camera images are not included.';
    // The visible, connected link remains valid for Safari's download/share UI.
    link.click();if(previousUrl)URL.revokeObjectURL(previousUrl);
  });
  element('fps-study-copy').addEventListener('click',()=>{
    const field=element<HTMLTextAreaElement>('fps-study-json');field.focus();field.select();field.setSelectionRange(0,field.value.length);
    element('fps-study-export-status').textContent='Full comparison JSON selected. Choose Copy from the selection menu, then paste it into a message or file.';
  });
  element('fps-study-continue').addEventListener('click',()=>{
    if(study?.status==='running'&&firstAt===null)element<HTMLButtonElement>('start').click();
  });
  element('start').addEventListener('click',()=>{
    if(study?.status==='running'&&firstAt===null){openedAt=performance.now();preparingAt=null;}
  });
  element('fps-study-start').addEventListener('click',()=>{
    if(mode==='g')return;
    study={schema:'ar-fps-candidate-comparison-v1',id:crypto.randomUUID(),createdAt:new Date().toISOString(),status:'running',reason:null,buildId:BUILD_ID,buildAt:BUILD_AT,
      order:['g',mode,mode,'g'],index:0,segments:[],eyewear:selection('eyewear-select'),hairModel:selection('hair-model-select'),variant:selection('variant-select'),
      warmupMs:5000,measurementMs:30000,
      policy:'Separate fresh sessions; 5 s and at least 3 tracked/masked warmup frames, then 30 s. All measured publications remain, including missing tracking/masks. Times end at canvas submission, not physical scanout. Only numeric timing/route data and model IDs; no images or frame identities.'};
    try{save();navigateTo('g');}catch(error){stop(`Unable to retain comparison: ${String(error)}`);}
  });
  show();
  if(study?.status!=='running')return false;
  if(study.buildId!==BUILD_ID){stop('The preview build changed. Start a new comparison to keep all four segments on the same build');return false;}
  if(study.order[study.index]!==mode||study.eyewear!==selection('eyewear-select')||study.hairModel!==selection('hair-model-select')||study.variant!==selection('variant-select')){
    stop('The restored session settings differ');return false;
  }
  document.querySelector<HTMLButtonElement>('#start')!.click();openedAt=lastAt=performance.now();
  timer=setInterval(()=>{
    if(!study||study.status!=='running')return;
    try{
      const incoming=window.arPerformanceProfiler.samplesAfter(serial);
      for(const sample of incoming){
        segmentSessionId??=sample.sessionId;
        if(sample.sessionId!==segmentSessionId||sample.pipeline!=='combined'||sample.variant!==study.variant)
          throw new Error('The camera session or rendering settings changed during measurement');
        serial=sample.serial;lastAt=performance.now();lastPublishedAt=sample.publishedAtMs;
        firstAt??=sample.publishedAtMs;
        if(measureAt===null){
          if(sample.hasFace&&(study.variant!=='hair'||sample.hasMask))validWarm++;
          if(sample.publishedAtMs-firstAt>=study.warmupMs&&validWarm>=3)measureAt=sample.publishedAtMs;
          else if(sample.publishedAtMs-firstAt>15000)throw new Error('Tracking and hair coverage were unavailable during warmup');
        }
        if(measureAt!==null){
          // Record compact numeric data for this study. The original timings download retains full per-stage data.
          const native=sample.native?Object.fromEntries(Object.entries(sample.native).filter(([key,value])=>
            (/pbo\.|asyncReadbackUsed|reuseSourcePixelsUsed|branchReadbackCalls|fpsCandidate\.|fps\.(sourceHashBytesAvoided|cpuCompose|bookkeeping)/.test(key)
            &&(typeof value==='number'||typeof value==='boolean'||value===null))
            ||(key==='fps.identityMode'&&(value==='session-frame'||value==='sha256')))):null;
          rows.push({serial:sample.serial,sessionId:sample.sessionId,capturedAtMs:sample.capturedAtMs,publishedAtMs:sample.publishedAtMs,totalMs:sample.totalMs,
            hasFace:sample.hasFace,hasMask:sample.hasMask,sourceWidth:sample.sourceWidth,sourceHeight:sample.sourceHeight,
            videoPresentedFrames:sample.videoPresentedFrames,composeMs:sample.composeMs,finalChecksMs:sample.finalChecksMs,
            sourceHashMs:sample.sourceHashMs,faceInferenceMs:sample.faceInferenceMs,hairInferenceMs:sample.hairInferenceMs,
            hairExtractionMs:sample.hairExtractionMs,faceDelegate:sample.faceDelegate,hairDelegate:sample.hairDelegate,fallback:sample.fallback,native});
          if(sample.publishedAtMs-measureAt>=study.measurementMs){
            const first=rows[0]!,last=rows.at(-1)!,tracked=rows.filter(row=>row.hasFace),intervals=rows.slice(1).map((row,i)=>row.publishedAtMs-rows[i]!.publishedAtMs);
            study.segments.push({mode,timeOriginMs:performance.timeOrigin,startedAt:new Date(performance.timeOrigin+measureAt).toISOString(),rows,
              fps:(rows.length-1)*1000/(last.publishedAtMs-first.publishedAtMs),ageP95:percentile(rows.map(row=>row.totalMs),.95),
              intervalP95:percentile(intervals,.95),gaps100:intervals.filter(value=>value>100).length,trackedFraction:tracked.length/rows.length,
              maskCoverage:study.variant==='hair'&&tracked.length?tracked.filter(row=>row.hasMask).length/tracked.length:null});
            study.index++;if(study.index===study.order.length)study.status='complete';
            save();if(timer!==undefined)clearInterval(timer);timer=undefined;show();
            if(study.status==='running')navigateTo(study.order[study.index]!);return;
          }
        }
      }
      if(firstAt===null){
        const start=element<HTMLButtonElement>('start'),button=element<HTMLButtonElement>('fps-study-continue');
        button.hidden=false;button.disabled=start.disabled;
        button.textContent=start.disabled?'Preparing this segment…':'Open camera / continue segment';
        const step=element('stage-status').textContent?.trim();
        const waitingPermission=start.disabled&&step==='STARTING CAMERA';
        if(!start.disabled||waitingPermission)preparingAt=null;else preparingAt??=performance.now();
        if(preparingAt!==null&&performance.now()-preparingAt>60000)throw new Error('No first AR frame after 60 seconds of mirror preparation. Retry the camera, then start a new comparison');
        element('fps-study-status').textContent=`${study.index+1} / ${study.order.length} · ${labels[mode]} · ${!start.disabled?'Tap Open camera / continue segment.':waitingPermission?'Allow camera access when asked.':`Preparing the mirror · ${Math.floor((performance.now()-openedAt)/1000)} seconds.`} Measurement starts after the first frames and warmup.`;
        return;
      }
      element('fps-study-continue').hidden=true;
      if(firstAt!==null&&performance.now()-lastAt>10000)throw new Error('No AR publications for 10 seconds');
      element('fps-study-status').textContent=`${study.index+1} / ${study.order.length} · ${labels[mode]} · ${measureAt===null?'warming up':Math.min(30,Math.floor((lastPublishedAt-measureAt)/1000))+' / 30 seconds'}`;
    }catch(error){stop(error instanceof Error?error.message:String(error));}
  },250);
  return true;
}
