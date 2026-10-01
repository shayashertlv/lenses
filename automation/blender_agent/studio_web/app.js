const $ = (id) => document.getElementById(id);
const ACTIVE = new Set(['researching', 'describing', 'starting', 'running', 'stopping']);
const RESUMABLE = new Set(['stopped', 'interrupted', 'failed', 'budget_limit', 'turn_limit']);
const STATUS = {draft:'Draft', researching:'Researching specifics', describing:'Finishing previous request', starting:'Starting Blender', running:'Artist working', stopping:'Stopping', completed:'Completed', stopped:'Stopped', budget_limit:'Budget limit', turn_limit:'Turn limit', failed:'Needs attention', interrupted:'Interrupted', imported:'Imported model'};
const state = {config:null, jobs:[], job:null, images:[], dirty:false, busy:false, section:'brief', mode:'3d', materials:null, edits:{}, viewer:{lens_reflection:1}, materialDirty:false, channel:crypto.randomUUID(), bridgeReady:false, frameURL:null, poll:null, pendingNavigation:null, history:new Map(), materialLoading:false};
const SPEC_GROUPS = [
  {title:'Frame', fields:[['frame_material','Material',['Unknown','Acetate','Metal','Nylon / injected plastic','Mixed materials']],['frame_color','Colour',null,'e.g. translucent smoke, warm gold'],['frame_finish','Finish',['Unknown','Glossy','Satin','Matte','Mixed finish']]]},
  {title:'Lenses', fields:[['lens_type','Type',['Unknown','Clear','Tinted sunglasses','Mirrored sunglasses','Single shield']],['lens_color','Base lens colour',null,'e.g. rose tint, champagne, grey-green'],['lens_mirror','Mirror coating / reflection',['Unknown','None','Subtle reflection','Strong mirror','Iridescent / colour shifting']],['lens_gradient','Gradient',['Unknown','None / uniform','Top-to-bottom','Other / see notes']]]},
  {title:'Temples & hardware', fields:[['temple_details','Temples',null,'Shape, internal core, sleeves, tip details'],['hardware_details','Hardware',null,'Hinges, nose pads, logos and inlays']]},
  {title:'Known dimensions · mm', fields:[['frame_width_mm','Overall frame width','number'],['lens_width_mm','Lens width','number'],['lens_height_mm','Lens height','number'],['bridge_width_mm','Bridge width','number'],['temple_length_mm','Temple length','number']]},
  {title:'Anything else', fields:[['notes','Additional notes','textarea','Reference limitations, important details, or things the artist should investigate.']]},
];

function node(tag, cls, text) { const element=document.createElement(tag); if(cls) element.className=cls; if(text!==undefined) element.textContent=text; return element; }
function statusLabel(status) { return STATUS[status] || status || 'Draft'; }
function knownNumber(value) { return value!==null&&value!==undefined&&value!==''&&Number.isFinite(Number(value)); }
function money(value) { return knownNumber(value) ? '$'+Number(value).toFixed(2) : '—'; }
function message(error) { return error?.message || String(error); }
function notice(text, error=false) { $('notice').textContent=text; $('notice').classList.toggle('error',error); $('notice').hidden=!text; }
let toastTimer;
function toast(text) { $('toast').textContent=text; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,4000); }
function clone(value) { return structuredClone(value); }
function active() { return ACTIVE.has(state.job?.status); }
function draftEditable() { return !state.job || (state.job.status==='draft'&&!state.job.started_once); }
function unwrap(value) { return value?.job || value; }
function endpoint(suffix='') { if(!state.job?.id) throw new Error('Save a draft first.'); return '/api/jobs/'+encodeURIComponent(state.job.id)+suffix; }
async function api(path, method='GET', body) {
  const headers={Accept:'application/json'};
  if(method!=='GET') { headers['Content-Type']='application/json'; headers['X-CSRF-Token']=state.config?.csrf_token || ''; }
  const response=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),credentials:'same-origin'});
  let data; try { data=await response.json(); } catch { throw new Error('The local studio returned an unreadable response.'); }
  if(!response.ok) { const error=new Error(typeof data.error==='string'?data.error:data.error?.message || data.detail || 'This action could not be completed.'); error.status=response.status; throw error; }
  return data;
}

function markDraftDirty() { state.dirty=true; renderSaveState(); }
function renderSaveState() { $('draft-state').textContent=state.busy?'Working…':state.dirty?'Unsaved brief':state.job?(draftEditable()?'Draft saved':'Brief saved'):'Not saved yet'; $('draft-state').classList.toggle('dirty',state.dirty); }
function buildSpecs() {
  for(const group of SPEC_GROUPS) {
    const section=node('div','spec-group'); section.append(node('h3','',group.title));
    const grid=node('div','field-row');
    for(const [key,label,kind,placeholder] of group.fields) {
      const field=node('div','field'),caption=node('label','',label);caption.htmlFor='spec-'+key;field.append(caption);let input;
      if(Array.isArray(kind)) { input=node('select'); for(const choice of kind) input.add(new Option(choice,choice==='Unknown'?'unknown':choice)); }
      else { input=node(kind==='textarea'?'textarea':'input'); if(kind==='number') { input.type='number'; input.min='0'; input.max='1000'; input.step='.1'; input.placeholder='Unknown'; } else input.placeholder=placeholder || ''; if(kind==='textarea') input.rows=3; }
      input.id='spec-'+key; input.dataset.spec=key; input.addEventListener('input',()=>{markDraftDirty();renderSpecProvenance(key,true);}); field.append(input);const provenance=node('span','spec-provenance');provenance.id='provenance-'+key;field.append(provenance);input.setAttribute('aria-describedby',provenance.id);grid.append(field);
      if(kind==='textarea') field.style.gridColumn='1 / -1';
    }
    section.append(grid); $('spec-fields').append(section);
  }
}
function readDraft() {
  const specs={...(state.job?.specs || {})};
  document.querySelectorAll('[data-spec]').forEach(input=>{ if(input.value==='') delete specs[input.dataset.spec]; else specs[input.dataset.spec]=input.type==='number'?Number(input.value):input.value; });
  const budget=Number($('budget').value);
  if(!Number.isFinite(budget)||budget<1||budget>1000||Math.abs(Math.round(budget*100)-budget*100)>.000001) throw new Error('Enter a total Astra budget from $1 to $1000, with at most two decimal places.');
  return {name:$('model-name').value.trim()||'Untitled eyewear',specs,budget_usd:budget};
}
function populateDraft(job) {
  $('model-name').value=job?.name || '';
  $('budget').value=job?.budget_usd ?? state.config?.defaults?.budget_usd ?? 20;
  for(const input of document.querySelectorAll('[data-spec]')) {
    const value=job?.specs?.[input.dataset.spec] ?? (input.tagName==='SELECT'?'unknown':'');
    if(input.tagName==='SELECT' && value && !Array.from(input.options).some(option=>option.value===String(value))) input.add(new Option(String(value),String(value)));
    input.value=value;
  }
  state.images=clone(job?.images || []); state.dirty=false; renderImages(); renderSaveState();renderSpecifics(job);
}
function sourceLink(source,label) {
  try {const url=new URL(source.url);if(!['https:','http:'].includes(url.protocol))return null;const anchor=node('a','',label||source.title||url.hostname);anchor.href=url.href;anchor.target='_blank';anchor.rel='noopener noreferrer';return anchor;}catch{return null;}
}
function renderSpecProvenance(key,edited=false,job=state.job) {
  const box=$('provenance-'+key),input=$('spec-'+key);box.replaceChildren();
  const known=input.value!==''&&input.value.toLowerCase()!=='unknown',provenance=job?.spec_provenance?.[key];
  if(!known){box.textContent='Unknown — left open for investigation.';return;}
  if(edited||provenance?.kind==='user'||!provenance){box.textContent=edited?'Your value · unsaved':'Your supplied value';return;}
  box.append(node('span','','Source-backed · '));let count=0;
  for(const source of provenance.evidence||[]){const link=sourceLink(source,source.title||'Source '+(count+1));if(!link)continue;if(count++)box.append(document.createTextNode(' · '));box.append(link);}
  if(!count)box.append(document.createTextNode('See research details'));
}
function renderSpecifics(job) {
  for(const input of document.querySelectorAll('[data-spec]'))renderSpecProvenance(input.dataset.spec,false,job);
  const research=job?.specifics,box=$('specifics-notes');box.replaceChildren();box.hidden=!research;
  if(research){
    box.append(node('strong','','Online research saved'));
    box.append(node('p','','Only supported facts are filled. Sources can still be wrong or refer to a different variant; review the exact product match before building.'));
    if(Array.isArray(research.uncertainties)&&research.uncertainties.length){const list=node('ul');for(const value of research.uncertainties)list.append(node('li','',String(value)));box.append(list);}
    if(research.search_suggestions_html){const suggestions=node('div','search-suggestions');suggestions.append(node('p','small','Google Search suggestions'));const frame=node('iframe');frame.title='Google Search suggestions';frame.setAttribute('sandbox','allow-popups allow-popups-to-escape-sandbox');frame.referrerPolicy='no-referrer';frame.srcdoc='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src https: data:; base-uri \'none\'; form-action \'none\'"><base target="_blank">'+research.search_suggestions_html;suggestions.append(frame);box.append(suggestions);}
  }
  $('specifics-json-details').hidden=!job?.specifics_json;$('specifics-json').textContent=job?.specifics_json||'';
}
function imageSource(image) { return image.thumbnail_url || image.url || image.data_url || ''; }
function renderImages() {
  $('reference-grid').replaceChildren(); $('image-count').textContent=state.images.length+' image'+(state.images.length===1?'':'s');
  const locked=!draftEditable();
  $('drop-zone').hidden=locked; $('reference-lock-note').hidden=!locked;
  state.images.forEach((image,index)=>{
    const displayName=image.original_name||image.name||'Reference '+(index+1);
    const tile=node('div','reference-tile'); const stage=node('div','image-stage'); const img=node('img'); img.src=imageSource(image); img.alt=displayName; img.loading='lazy'; stage.append(img); tile.append(stage);
    if(!locked) { const remove=node('button','remove-image','×'); remove.type='button'; remove.setAttribute('aria-label','Remove '+(image.name||'image')); remove.addEventListener('click',()=>{state.images.splice(index,1);markDraftDirty();renderImages();});tile.append(remove); }
    const caption=node('div','reference-caption'); caption.append(node('span','',displayName));
    const view=node('select'); view.setAttribute('aria-label','View for '+image.name);
    for(const [value,label] of [['unknown','View unknown'],['front','Front'],['angle','Angled'],['side','Side'],['top','Top'],['rear','Rear'],['detail','Detail']]) view.add(new Option(label,value));
    if(image.view&&!Array.from(view.options).some(option=>option.value===image.view))view.add(new Option(image.view,image.view));
    view.value=image.view||'unknown'; view.disabled=locked; view.addEventListener('change',()=>{image.view=view.value;markDraftDirty();});caption.append(view);
    const provenance=node('select'); provenance.setAttribute('aria-label','Provenance for '+image.name);
    for(const [value,label] of [['unknown','Source unknown'],['photo','Product photo'],['generated','Generated view']]) provenance.add(new Option(label,value));
    if(image.provenance&&!Array.from(provenance.options).some(option=>option.value===image.provenance))provenance.add(new Option(image.provenance,image.provenance));
    provenance.value=image.provenance||'unknown';provenance.disabled=locked;provenance.addEventListener('change',()=>{image.provenance=provenance.value;markDraftDirty();});caption.append(provenance);tile.append(caption);$('reference-grid').append(tile);
  });
}
async function addFiles(files) {
  if(!draftEditable()||state.busy) return;
  const target=state.job?.id,original=state.images;
  try {
    const selected=Array.from(files); if(state.images.length+selected.length>12) throw new Error('Use up to 12 images in one reference set.');
    for(const file of selected) {
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)) throw new Error(file.name+': use PNG, JPEG or WebP.');
      if(file.size>12*1024*1024) throw new Error(file.name+' exceeds 12 MiB.');
    }
    const loaded=await Promise.all(selected.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,data_url:reader.result,view:'unknown',provenance:'unknown'});reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.readAsDataURL(file);})));
    if(state.job?.id!==target||state.images!==original)return;
    state.images.push(...loaded);markDraftDirty();renderImages();notice('');
  } catch(error) {notice(message(error),true);} finally {$('image-input').value='';}
}

function renderCapabilities() {
  $('capabilities').replaceChildren();
  for(const [key,label] of [['openai_key','Astra API'],['gemini_key','Gemini API'],['blender','Blender'],['ar','AR preview']]) {
    const available=state.config?.capabilities?.[key];const item=node('div','capability'+(available?' available':''));item.append(node('span','status-dot'),node('span','',label+(available===false?' · unavailable':available?' · ready':' · pending')));$('capabilities').append(item);
  }
}
function renderProjects() {
  $('project-count').textContent=state.jobs.length; $('project-list').replaceChildren();
  if(!state.jobs.length) {$('project-list').append(node('p','muted small','Your saved models will live here.'));return;}
  for(const job of state.jobs) {
    const button=node('button','project-button'+(job.id===state.job?.id?' selected':''));button.type='button';
    const first=job.images?.[0];const thumb=imageSource(first||{})?node('img','project-thumb'):node('span','project-thumb','◌');
    if(first&&thumb.tagName==='IMG'){thumb.src=imageSource(first);thumb.alt='';}
    const info=node('div','project-info'); info.append(node('strong','',job.name||'Untitled eyewear'),node('small','',statusLabel(job.status)));button.append(thumb,info);button.addEventListener('click',()=>navigate(()=>loadJob(job.id)));$('project-list').append(button);
  }
}
function storeJob(job) {
  if(!job?.id) throw new Error('The server did not return a saved job.');
  state.job=job;const index=state.jobs.findIndex(item=>item.id===job.id);if(index<0)state.jobs.unshift(job);else state.jobs[index]=job;
  const history=state.history.get(job.id)||[];
  const text=job.progress?.message||statusLabel(job.status);
  if(!history.length||history[0].status!==job.status||history[0].message!==text) history.unshift({status:job.status,message:text,time:new Date().toISOString()});
  state.history.set(job.id,history.slice(0,30));renderProjects();renderImages();renderJob();schedulePoll();
}
function renderJob() {
  const job=state.job;
  $('project-title').textContent=job?.name||'Start with a closer look.';
  $('project-subtitle').textContent=job?'One brief, a persistent scene, a model you can inspect.':'Bring the references. Give the artist a clear brief.';
  $('job-status').textContent=job?statusLabel(job.status):'New draft';$('job-status').dataset.status=job?.status||'draft';
  const editable=draftEditable();
  for(const id of ['model-name','budget']) $(id).disabled=!editable||state.busy;
  document.querySelectorAll('[data-spec]').forEach(input=>input.disabled=!editable||state.busy);
  $('save-draft').disabled=!state.config||!editable||state.busy;
  $('generate-specifics').disabled=!state.config||!editable||state.busy||state.config?.capabilities?.gemini_key!==true;
  $('generate-specifics').textContent=job?.status==='researching'?'Searching online…':'Generate specifics';
  $('start-run').disabled=!state.config||!editable||state.busy||state.config?.capabilities?.openai_key!==true||state.config?.capabilities?.blender!==true;
  $('start-hint').textContent=!editable?'This run keeps its original brief and budget. Use Resume in Run & evidence if it was interrupted.':'Starting uses the paid Astra API and launches local Blender. Your draft is saved first.';
  $('image-input').disabled=!editable||state.busy;
  renderSaveState();renderProgress();renderResultShell();
}
function renderProgress() {
  const job=state.job,progress=job?.progress||{};
  $('progress-title').textContent=job?statusLabel(job.status):'Ready when you are.';
  $('progress-message').textContent=progress.message||(job?.status==='completed'?'The artist has finished this session. Review its candidate and remaining limitations.':job?'No additional activity has been reported yet.':'Save your brief and start a run to see the artist’s activity here.');
  $('progress-indicator').hidden=!active();$('run-error').hidden=!job?.error;$('run-error').textContent=typeof job?.error==='string'?job.error:job?.error?JSON.stringify(job.error):'';
  const fresh=!job||job.status==='draft'&&!job.started_once;
  const spent=Object.hasOwn(progress,'spent_usd')?progress.spent_usd:Object.hasOwn(progress,'settled_usd')?progress.settled_usd:fresh?0:null;
  const reserved=Object.hasOwn(progress,'reserved_usd')?progress.reserved_usd:Object.hasOwn(progress,'unknown_reserved_usd')?progress.unknown_reserved_usd:fresh?0:null;
  const cap=job?job.budget_usd:state.config?.defaults?.budget_usd??20,accountingKnown=[spent,reserved,cap].every(knownNumber)&&Number(cap)>0;
  $('spent').textContent=money(spent);$('reserved').textContent=money(reserved);$('cap').textContent=money(cap);
  const s=accountingKnown?Math.min(100,Math.max(0,Number(spent)/Number(cap)*100)):0,r=accountingKnown?Math.min(100-s,Math.max(0,Number(reserved)/Number(cap)*100)):0;$('spent-bar').style.width=s+'%';$('reserve-bar').style.width=r+'%';$('budget-track').hidden=!accountingKnown;
  $('accounting-note').textContent=accountingKnown?'This bar shows budget commitment, not model completion. Reserves are not confirmed charges.':'Accounting is unavailable. Resume stays disabled until the total commitment can be verified.';
  $('resume-run').hidden=job?.read_only||!RESUMABLE.has(job?.status);$('resume-run').disabled=!accountingKnown||state.busy||Number(cap)<=Number(spent)+Number(reserved)||state.config?.capabilities?.openai_key!==true;
  $('stop-run').hidden=!['starting','running','stopping'].includes(job?.status);$('stop-run').disabled=state.busy||job?.status==='stopping'||state.config?.capabilities?.stop===false;
  $('open-result').hidden=!job?.result?.model_url;
  const events=Array.isArray(job?.progress?.events)?job.progress.events:Array.isArray(job?.events)?job.events:state.history.get(job?.id)||[];
  $('timeline').replaceChildren();
  for(const event of events.slice(0,24)) {const entry=node('div','timeline-entry');entry.append(node('span','timeline-marker'));const text=node('div');text.append(node('strong','',event.label||event.title||statusLabel(event.status||job?.status)),node('p','',event.message||event.description||''));entry.append(text);const time=node('time','',event.time?new Date(event.time).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'');if(event.time)time.dateTime=event.time;entry.append(time);$('timeline').append(entry);}
  $('run-links').replaceChildren();
  for(const [url,label] of [[job?.result?.scene_url,'Saved Blender checkpoint'],[job?.result?.model_url,'Latest exported GLB']]) {if(url){const anchor=node('a','text-link',label+' ↗');anchor.href=assetURL(url);anchor.target='_blank';anchor.rel='noopener';$('run-links').append(anchor);}}
  renderArtistNotes();
}
function renderArtistNotes() {
  const job=state.job,result=job?.result||{},finalOutput=typeof job?.final_output==='string'?job.final_output:'';
  $('artist-notes').hidden=!finalOutput&&!result.validation&&!result.evidence_scope&&!result.candidate_status;
  $('artist-final-output').textContent=finalOutput||'No final written handoff is available for this candidate.';
  $('candidate-scope').textContent=result.evidence_scope||'';
  $('candidate-validation').replaceChildren();
  if(result.candidate_status)$('candidate-validation').append(node('p','small',String(result.candidate_status).replaceAll('_',' ')));
  if(result.validation){const details=node('details','validation-details');details.append(node('summary','','Recorded validation'));details.append(node('pre','',typeof result.validation==='string'?result.validation:JSON.stringify(result.validation,null,2)));$('candidate-validation').append(details);}
}
function disposeViewer() {clearTimeout(previewTimer);state.frameURL=null;state.bridgeReady=false;$('model-frame').src='about:blank';}
function setSection(section) {
  if(state.section==='model'&&section!=='model')disposeViewer();
  state.section=section;for(const panel of document.querySelectorAll('.section-panel'))panel.hidden=panel.id!=='section-'+section;
  document.querySelectorAll('[data-section]').forEach(button=>{button.classList.toggle('active',button.dataset.section===section);button.setAttribute('aria-current',button.dataset.section===section?'page':'false');});
  if(section==='model'&&state.job?.result?.model_url) ensureMaterials();
}
async function refreshJobs() {const response=await api('/api/jobs');state.jobs=Array.isArray(response)?response:response.jobs||[];renderProjects();}
async function loadJob(id) {
  if(state.busy)return;state.busy=true;clearTimeout(state.poll);
  state.materials=null;state.edits={};state.materialDirty=false;state.frameURL=null;state.bridgeReady=false;$('model-frame').src='about:blank';$('specifics-notes').hidden=true;
  renderJob();
  try {const job=unwrap(await api('/api/jobs/'+encodeURIComponent(id)));populateDraft(job);storeJob(job);setSection(active()?'progress':job.result?.model_url?'model':'brief');notice('');const url=new URL(location.href);url.searchParams.set('job',id);history.replaceState(null,'',url);} catch(error) {notice(message(error),true);}finally{state.busy=false;renderJob();}
}
async function saveDraft() {
  const draft=readDraft();if(state.job&&!draftEditable())return state.job;
  const images=state.images.map(({name,data_url,view,provenance})=>({name,...(data_url?{data_url}:{}),view,provenance}));
  const result=state.job?await api(endpoint(),'PATCH',{...draft,images}):await api('/api/jobs','POST',{...draft,images});
  const job=unwrap(result);populateDraft(job);storeJob(job);return job;
}
async function action(name) {
  if(state.busy)return;state.busy=true;notice('');renderJob();
  try {
    if(name==='specifics'&&!$('model-name').value.trim())throw new Error('Enter the brand, full model code and colourway before searching for specifics.');
    if(['save','specifics','start'].includes(name)) await saveDraft();
    if(name==='save')toast('Draft saved.');
    else if(name==='specifics') {
      $('generate-specifics').textContent='Searching online…';
      await api(endpoint('/specifics'),'POST',{});const fresh=unwrap(await api(endpoint()));
      populateDraft(fresh);storeJob(fresh);
      toast('Source-backed specifics saved. Review the fields and sources before building.');
    } else {const job=unwrap(await api(endpoint('/'+name),'POST',{}));storeJob(job);renderImages();setSection('progress');toast(name==='stop'?'Stop requested. The reported run state will update here.':name==='resume'?'Resume requested within the same total cap.':'Build requested.');}
  } catch(error) {notice(message(error),true);if(state.job)try{storeJob(unwrap(await api(endpoint())));}catch{}}
  finally{state.busy=false;renderJob();}
}
function schedulePoll() {clearTimeout(state.poll);if(active())state.poll=setTimeout(pollJob,2500);}
async function pollJob() {
  if(!state.job)return;const id=state.job.id;
  try{const fresh=unwrap(await api(endpoint()));if(state.job?.id!==id)return;const oldModel=state.job.result?.model_sha256,finishedResearch=state.job.status==='researching'&&fresh.status!=='researching';if(finishedResearch&&!state.dirty)populateDraft(fresh);storeJob(fresh);if(oldModel!==fresh.result?.model_sha256&&state.materials&&!state.materialDirty){state.materials=null;state.frameURL=null;if(state.section==='model')ensureMaterials();}}
  catch(error){notice('Status updates paused: '+message(error),true);state.poll=setTimeout(pollJob,7000);}
}
function navigate(callback) {if(state.busy){toast('Let the current request finish before switching models.');return;}if(!state.dirty&&!state.materialDirty){callback();return;}state.pendingNavigation=callback;$('discard-dialog').showModal();}
function newProject() {clearTimeout(state.poll);state.job=null;state.images=[];state.materials=null;state.edits={};state.materialDirty=false;state.frameURL=null;state.bridgeReady=false;$('model-frame').src='about:blank';populateDraft(null);renderProjects();renderJob();setSection('brief');notice('');const url=new URL(location.href);url.searchParams.delete('job');history.replaceState(null,'',url);}

function assetURL(value) {
  if(!value)return '';
  const url=new URL(value,location.origin);
  const allowed=new Set([location.origin]);if(state.config?.ar_origin)allowed.add(new URL(state.config.ar_origin).origin);
  if(!allowed.has(url.origin)||!['http:','https:'].includes(url.protocol))throw new Error('The preview URL is outside this local studio.');
  return url.href;
}
function renderResultShell() {
  const result=state.job?.result,available=Boolean(result?.model_url);
  $('result-empty').hidden=available;$('model-layout').hidden=!available;if(!available)return;
  $('mode-3d').disabled=!result.viewer_url;$('mode-ar').disabled=!result.ar_url;
  $('model-identity').textContent=result.model_sha256?'GLB · '+result.model_sha256.slice(0,12):'Exported candidate';
  $('result-qualification').textContent=state.job.status==='completed'?'Artist completed · review required':state.job.status==='imported'?'Imported model':'Work in progress';
  $('download-model').href=assetURL(result.model_url);$('download-scene').hidden=!result.scene_url;if(result.scene_url)$('download-scene').href=assetURL(result.scene_url);
  if(state.section==='model')mountViewer();renderMaterialState();
}
async function ensureMaterials(force=false) {
  if(state.materialLoading||!state.job?.result?.model_url)return;
  if(state.materials&&!force){mountViewer();return;}
  const id=state.job.id;state.materialLoading=true;$('material-message').textContent='Loading material controls…';
  try {const doc=await api(endpoint('/materials'));if(state.job?.id!==id)return;state.materials=doc;state.edits={};state.viewer={lens_reflection:doc.viewer?.lens_reflection??1};state.materialDirty=false;renderMaterials();mountViewer();}
  catch(error){if(state.job?.id===id)$('material-message').textContent='Material controls unavailable: '+message(error);}
  finally {state.materialLoading=false;renderMaterialState();if(state.job?.id!==id&&state.section==='model'&&state.job?.result?.model_url)ensureMaterials();}
}
function renderMaterials() {
  const doc=state.materials;if(!doc)return;const previous=$('material-select').value;$('material-select').replaceChildren();
  for(const material of doc.materials||[])$('material-select').add(new Option(material.name||'Material '+material.id,String(material.id)));
  if(Array.from($('material-select').options).some(option=>option.value===previous))$('material-select').value=previous;
  $('lens-reflection').value=state.viewer.lens_reflection;$('reflection-value').value=Number(state.viewer.lens_reflection).toFixed(2);
  renderSelectedMaterial();renderMaterialState();
}
function selectedMaterial() {return state.materials?.materials?.find(material=>String(material.id)===$('material-select').value);}
function renderSelectedMaterial() {
  const material=selectedMaterial();$('material-controls').replaceChildren();$('material-roles').replaceChildren();if(!material)return;
  for(const role of material.roles||[])$('material-roles').append(node('span','role-chip',role));
  const controls=(state.materials.controls||[]).filter(control=>material.editable!==false&&material.editable_keys?.includes(control.key)&&Object.hasOwn(material.properties||{},control.key));
  for(const control of controls) {
    const key=control.key,value=state.edits[material.id]?.[key]??material.properties[key];const wrap=node('div','material-control');const label=node('label','',control.label||key);const id='control-'+key;label.htmlFor=id;
    const output=node('output','',typeof value==='number'?formatValue(value,control):'');output.id=id+'-value';label.append(output);wrap.append(label);
    if(control.type==='color') {
      const pair=node('div','color-pair'),picker=node('input'),text=node('input');picker.type='color';picker.id=id;picker.value=value;text.type='text';text.value=value;text.maxLength=7;text.setAttribute('aria-label',(control.label||key)+' hex colour');
      picker.addEventListener('input',()=>{text.value=picker.value;editMaterial(material.id,key,picker.value);});
      const updateHex=event=>{if(!/^#[0-9a-f]{6}$/i.test(text.value)){text.setCustomValidity('Use a six-digit hex colour, such as #87c9b0.');if(event.type==='change')text.reportValidity();return;}text.setCustomValidity('');picker.value=text.value;editMaterial(material.id,key,text.value.toLowerCase());};text.addEventListener('input',updateHex);text.addEventListener('change',updateHex);pair.append(picker,text);wrap.append(pair);
    } else if(control.type==='range') {
      const pair=node('div','range-pair'),slider=node('input'),number=node('input');slider.type='range';slider.id=id;number.type='number';number.setAttribute('aria-label',(control.label||key)+' value');
      for(const input of [slider,number]){input.min=control.min;input.max=control.max;input.step=control.step;input.value=displayNumber(value,control);}
      slider.addEventListener('input',()=>{number.value=slider.value;output.value=formatValue(Number(slider.value),control);editMaterial(material.id,key,Number(slider.value));});
      const updateNumber=event=>{const value=Number(number.value);if(number.value===''||!Number.isFinite(value)||value<control.min||value>control.max){if(event.type==='change')number.reportValidity();return;}slider.value=value;output.value=formatValue(value,control);editMaterial(material.id,key,value);};number.addEventListener('input',updateNumber);number.addEventListener('change',updateNumber);pair.append(slider,number);wrap.append(pair);
    } else continue;
    if(control.help){const help=node('p','small muted control-help',control.help);help.id=id+'-help';wrap.append(help);wrap.querySelectorAll('input').forEach(input=>input.setAttribute('aria-describedby',help.id));}$('material-controls').append(wrap);
  }
  if(!controls.length)$('material-controls').append(node('p','small muted',material.note||'This material has no editable native properties.'));
  renderMaterialState();
}
function displayNumber(value,control) {const step=String(control.step??.01),digits=step.includes('e-')?Number(step.split('e-')[1]):step.includes('.')?step.split('.')[1].length:0;return Number(value).toFixed(Math.min(digits,6));}
function formatValue(value,control) {return displayNumber(value,control)+(control.unit?' '+control.unit:'');}
function editMaterial(id,key,value) {
  const original=state.materials.materials.find(material=>String(material.id)===String(id))?.properties[key];
  if(value===original){if(state.edits[id]){delete state.edits[id][key];if(!Object.keys(state.edits[id]).length)delete state.edits[id];}}
  else (state.edits[id] ||= {})[key]=value;
  updateMaterialDirty();queuePreview();
}
function updateMaterialDirty() {state.materialDirty=Object.keys(state.edits).length>0||Number(state.viewer.lens_reflection)!==Number(state.materials?.viewer?.lens_reflection??1);renderMaterialState();}
function renderMaterialState() {
  $('material-dirty').hidden=!state.materialDirty;
  const disabled=state.busy||active()||!state.materials;
  $('save-materials').disabled=disabled||!state.materialDirty;$('reset-materials').disabled=disabled||!state.materialDirty;$('material-select').disabled=disabled;$('lens-reflection').disabled=disabled;
  $('material-controls').querySelectorAll('input').forEach(input=>input.disabled=disabled);
  const blendMatches=Object.hasOwn(state.job?.result||{},'scene_matches_revision')?state.job.result.scene_matches_revision:state.materials?.source_blend_matches_revision;
  if(state.materials)$('material-message').textContent=active()?'Material editing is available after the artist stops changing this model.':blendMatches===false?'This material revision changes the GLB. The original Blender source remains unchanged.':blendMatches!==true?'The saved Blender source has not been verified against this export.':state.materialDirty?'Preview changes are not saved in the downloadable GLB yet.':'Your saved material revision is shown in both views.';
}
function previewEdits() {
  const edits={};for(const material of state.materials?.materials||[]) {if(material.editable===false)continue;const values={};for(const key of material.editable_keys||[])if(Object.hasOwn(material.properties,key))values[key]=state.edits[material.id]?.[key]??material.properties[key];if(Object.keys(values).length)edits[material.id]=values;}return edits;
}
let previewTimer;
function queuePreview() {clearTimeout(previewTimer);previewTimer=setTimeout(sendPreview,100);}
function sendPreview() {
  if(!state.materials||!state.frameURL||!state.bridgeReady)return;
  $('model-frame').contentWindow?.postMessage({type:'lenses-studio:preview',channel:state.channel,model_sha256:state.materials.model_sha256,edits:previewEdits(),viewer:state.viewer},new URL(state.frameURL).origin);
  $('preview-state').textContent='Applying preview…';
}
function mountViewer(force=false) {
  const result=state.job?.result;if(state.section!=='model'||!result?.model_url)return;
  let raw=state.mode==='ar'?result.ar_url:result.viewer_url;
  $('mode-3d').classList.toggle('active',state.mode==='3d');$('mode-ar').classList.toggle('active',state.mode==='ar');$('mode-3d').setAttribute('aria-pressed',state.mode==='3d');$('mode-ar').setAttribute('aria-pressed',state.mode==='ar');
  if(!raw){$('model-frame').hidden=true;$('viewer-placeholder').hidden=false;$('viewer-placeholder').replaceChildren(node('p','',state.mode==='ar'?'AR preview is not available yet.':'3D preview is not available yet.'));return;}
  try {
    const url=new URL(assetURL(raw));url.searchParams.set('studioOrigin',location.origin);url.searchParams.set('studioChannel',state.channel);
    if(state.frameURL===url.href&&!force)return;state.frameURL=url.href;state.bridgeReady=false;
    $('model-frame').hidden=false;$('model-frame').src=url.href;$('viewer-external').href=assetURL(raw);$('preview-state').textContent='Loading '+(state.mode==='ar'?'AR':'3D')+'…';
    $('viewer-placeholder').replaceChildren(node('span','loading-ring'),node('p','',state.mode==='ar'?'Open the camera inside AR when you are ready.':'Loading your model…'));$('viewer-placeholder').hidden=false;
  } catch(error){$('viewer-placeholder').hidden=false;$('viewer-placeholder').replaceChildren(node('p','',message(error)));}
}
window.addEventListener('message',event=>{
  const data=event.data;if(!state.frameURL||event.origin!==new URL(state.frameURL).origin||event.source!==$('model-frame').contentWindow||!data||data.channel!==state.channel)return;
  if(data.model_sha256&&state.materials?.model_sha256&&data.model_sha256!==state.materials.model_sha256)return;
  if(data.type==='lenses-studio:ready'){state.bridgeReady=true;$('viewer-placeholder').hidden=true;$('preview-state').textContent=data.loaded===false?'Open camera to preview':'Preview ready';sendPreview();}
  if(data.type==='lenses-studio:applied'){$('preview-state').textContent=data.pending?'Open camera to preview':state.materialDirty?'Unsaved preview':'Saved appearance';$('viewer-placeholder').hidden=true;}
  if(data.type==='lenses-studio:error'){$('preview-state').textContent='Preview needs attention';$('material-message').textContent=typeof data.error==='string'?data.error:data.message||'The preview could not apply this material change.';}
});
$('model-frame').addEventListener('load',()=>{if(!state.frameURL)return;$('viewer-placeholder').hidden=true;$('preview-state').textContent=state.bridgeReady?'Preview ready':'Waiting for model…';if(state.bridgeReady)sendPreview();});
async function saveMaterials() {
  if(state.busy||!state.materials||!state.materialDirty)return;state.busy=true;renderJob();
  try {
    const response=await api(endpoint('/materials'),'POST',{base_revision:state.materials.revision,edits:state.edits,viewer:state.viewer});
    const job=response.job||(!Array.isArray(response.materials)&&response.id?response:null)||unwrap(await api(endpoint()));
    state.materialDirty=false;state.edits={};state.materials=null;state.frameURL=null;storeJob(job);await ensureMaterials(true);toast('Material revision saved. The GLB download is updated.');notice('');
  } catch(error){notice(error.status===409?'The saved model changed. Your preview edits are retained; reload the model before applying them to a new revision.':message(error),true);}
  finally{state.busy=false;renderJob();}
}

buildSpecs();
for(const id of ['model-name','budget'])$(id).addEventListener('input',markDraftDirty);
$('image-input').addEventListener('change',event=>addFiles(event.target.files));
for(const type of ['dragenter','dragover'])$('drop-zone').addEventListener(type,event=>{event.preventDefault();$('drop-zone').classList.add('dragover');});
for(const type of ['dragleave','drop'])$('drop-zone').addEventListener(type,event=>{event.preventDefault();$('drop-zone').classList.remove('dragover');if(type==='drop')addFiles(event.dataTransfer.files);});
for(const [id,name] of [['save-draft','save'],['generate-specifics','specifics'],['start-run','start'],['resume-run','resume'],['stop-run','stop']])$(id).addEventListener('click',()=>action(name));
document.querySelectorAll('[data-section]').forEach(button=>button.addEventListener('click',()=>setSection(button.dataset.section)));
$('new-project').addEventListener('click',()=>navigate(newProject));$('back-to-brief').addEventListener('click',()=>setSection('brief'));$('open-result').addEventListener('click',()=>setSection('model'));
$('discard-cancel').addEventListener('click',()=>{$('discard-dialog').close();state.pendingNavigation=null;});$('discard-confirm').addEventListener('click',()=>{$('discard-dialog').close();const callback=state.pendingNavigation;state.pendingNavigation=null;callback?.();});
$('material-select').addEventListener('change',renderSelectedMaterial);
for(const [id,mode] of [['mode-3d','3d'],['mode-ar','ar']])$(id).addEventListener('click',()=>{state.mode=mode;mountViewer();});
$('lens-reflection').addEventListener('input',()=>{state.viewer.lens_reflection=Number($('lens-reflection').value);$('reflection-value').value=state.viewer.lens_reflection.toFixed(2);updateMaterialDirty();queuePreview();});
$('reset-materials').addEventListener('click',()=>{state.edits={};state.viewer={lens_reflection:state.materials?.viewer?.lens_reflection??1};state.materialDirty=false;renderMaterials();sendPreview();toast('Returned to the saved revision.');});
$('save-materials').addEventListener('click',saveMaterials);
window.addEventListener('beforeunload',event=>{if(state.dirty||state.materialDirty){event.preventDefault();event.returnValue='';}});
async function boot() {
  try{state.config=await api('/api/config');renderCapabilities();populateDraft(null);await refreshJobs();const requested=new URL(location.href).searchParams.get('job');if(requested)await loadJob(requested);else if(state.jobs.length)await loadJob(state.jobs[0].id);else renderJob();}
  catch(error){notice('The local studio is not connected: '+message(error),true);$('project-list').replaceChildren(node('p','muted small','Unable to load models.'));renderJob();}
}
boot();
