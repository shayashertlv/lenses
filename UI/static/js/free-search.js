/* ══════════════════════════════════════════════════
   Free Search — page-specific JavaScript
   ══════════════════════════════════════════════════ */

/* ── State ──────────────────────────────────── */
let sid=null, poll=null, tipTimer=null, tipIdx=0, uploadedFile=null, lastRendered='';

const tips=[
  "Did you know? Frame shape can change how others perceive your personality.",
  "Round frames have been a staple since the 13th century.",
  "Titanium frames can flex without breaking — perfect for active lifestyles.",
  "Acetate is made from plant-based cellulose, making it eco-friendlier than plastic.",
  "Polarized lenses cut glare by filtering horizontally-oriented light.",
  "The most popular frame color worldwide? Classic black, followed by tortoiseshell.",
  "Cat-eye frames were originally designed in the 1930s and became iconic in the 1950s.",
  "Photochromic lenses can transition from clear to dark in under 30 seconds.",
  "Aviators were originally designed for military pilots in the 1930s.",
  "Blue-light filtering lenses can improve sleep quality when worn in the evening.",
  "Semi-rimless frames offer a lighter feel while maintaining structural support.",
  "The right pair of glasses can make you look up to 5 years younger.",
];

/* ── Photo upload ───────────────────────────── */
const fileIn=document.getElementById('fs-file');
const uploadArea=document.getElementById('upload-area');
const preview=document.getElementById('up-preview');
const submitBtn=document.getElementById('submit-btn');
const submitHint=document.getElementById('submit-hint');

fileIn.addEventListener('change', e=>{
  const f=e.target.files[0]; if(!f) return;
  uploadedFile=f;
  uploadArea.classList.add('has-file');
  document.getElementById('up-label-text').textContent=f.name;

  const reader=new FileReader();
  reader.onload=ev=>{preview.src=ev.target.result;preview.style.display='block';try{sessionStorage.setItem('cached_portrait',ev.target.result)}catch(e){}};
  reader.readAsDataURL(f);

  submitBtn.disabled=false;
  submitHint.textContent='Select your preferences above, then hit the button!';
});

/* ── Restore cached portrait from another page ── */
(function(){
  const cached=sessionStorage.getItem('cached_portrait');
  if(!cached || uploadedFile) return;
  // Convert data URL to File
  const arr=cached.split(','), mime=arr[0].match(/:(.*?);/)[1];
  const bstr=atob(arr[1]), n=bstr.length, u8=new Uint8Array(n);
  for(let i=0;i<n;i++) u8[i]=bstr.charCodeAt(i);
  uploadedFile=new File([u8],'cached-photo.jpg',{type:mime});
  uploadArea.classList.add('has-file');
  document.getElementById('up-label-text').textContent='Photo from previous session';
  preview.src=cached; preview.style.display='block';
  submitBtn.disabled=false;
  submitHint.textContent='Select your preferences above, then hit the button!';
})();

/* ── Submit ──────────────────────────────────── */
async function submitSearch(){
  if(!uploadedFile) return;

  // Collect form values
  const fd=new FormData();
  fd.append('photo', uploadedFile);

  // Radios (visual tiles + chips)
  document.querySelectorAll('#form-view input[type=radio]:checked').forEach(r=>{
    if(r.value) fd.append(r.name, r.value);
  });

  // Number input
  const priceInput=document.querySelector('input[name=max_price]');
  if(priceInput && priceInput.value) fd.append('max_price', priceInput.value);

  // Show loading
  showView('loading-view');

  // Show portrait in loading
  const loadImg=document.getElementById('load-portrait-img');
  loadImg.src=preview.src;
  document.getElementById('load-portrait').style.display='block';

  setLoadStep(1); setLoadProg(15);
  document.getElementById('load-stage').textContent='Searching our catalog...';
  startTips();

  try{
    const r=await fetch('/api/free-search',{method:'POST',body:fd});
    const j=await r.json();
    if(j.error){showError(j.error);return}
    sid=j.session_id;
    pollStatus();
  }catch(err){showError('Upload failed: '+err.message)}
}

/* ── View management ─────────────────────────── */
function showView(id){
  ['loading-view','results-view','error-view'].forEach(v=>{
    document.getElementById(v).style.display='none';
  });
  document.getElementById('form-view').style.display = id?'none':'block';
  if(id){
    document.getElementById(id).style.display='flex';
  }
}

/* ── Loading steps ───────────────────────────── */
function setLoadStep(n){
  const s1=document.getElementById('ls1'), s2=document.getElementById('ls2');
  s1.classList.remove('active','done'); s2.classList.remove('active','done');
  if(n===1){s1.classList.add('active')}
  else{s1.classList.add('done');s2.classList.add('active')}
  document.getElementById('lsl1').className='fs-sline'+(n>1?' done':'');
}
function setLoadProg(pct){document.getElementById('load-prog').style.width=pct+'%'}

/* ── Tips ────────────────────────────────────── */
function startTips(){tipIdx=0;showTip();tipTimer=setInterval(()=>{tipIdx=(tipIdx+1)%tips.length;showTip()},4500)}
function stopTips(){if(tipTimer)clearInterval(tipTimer);tipTimer=null}
function showTip(){
  const el=document.getElementById('load-tip');
  el.style.opacity='0';
  setTimeout(()=>{el.textContent=tips[tipIdx];el.style.opacity='1'},350);
}

/* ── Poll ────────────────────────────────────── */
function pollStatus(){
  if(!sid) return;
  fetch('/api/status/'+sid).then(r=>r.json()).then(d=>{
    if(d.status==='error'){showError(d.error||'Unknown error');return}

    const msgs={
      uploading:'Preparing your photo...',
      searching:'Searching our catalog...',
      tryon:'Generating virtual try-on images...',
      primary_ready:'Your best match is ready!',
      done:'All results are ready'
    };
    document.getElementById('load-stage').textContent=msgs[d.stage]||'Processing...';

    if(d.stage==='searching'||d.stage==='uploading'){setLoadStep(1);setLoadProg(25)}
    else if(d.stage==='tryon'){setLoadStep(2);setLoadProg(55)}
    else if(d.stage==='primary_ready'){setLoadStep(2);setLoadProg(80)}
    else if(d.stage==='done'){setLoadStep(2);setLoadProg(100)}

    // Show results as soon as primary is ready
    if(d.num_options>0 && (d.stage==='primary_ready'||d.stage==='done')){
      stopTips();
      renderResults(d);
    }

    if(d.status!=='done' && d.status!=='error')
      poll=setTimeout(pollStatus,2000);
    else if(d.status==='done'){
      stopTips();
      renderResults(d);
    }
  }).catch(()=>{poll=setTimeout(pollStatus,3000)});
}

const _recolorStore={};let _recolorIdx=0;
function goRecolor(key){
  sessionStorage.setItem('recolor_preload',_recolorStore[key]);
  window.location.href='/lens-recolor';
}

function getTryonHtml(o){
  if(o.tryon_status==='done'&&o.tryon_b64){
    const key='rc'+(++_recolorIdx);
    _recolorStore[key]=o.tryon_b64;
    return `<div class="tryon-done-wrap">
      <img src="data:image/png;base64,${o.tryon_b64}" alt="Virtual try-on"/>
      <button class="recolor-overlay-btn" onclick="event.stopPropagation();goRecolor('${key}')" onmouseover="this.style.background='rgba(232,168,56,1)'" onmouseout="this.style.background='rgba(232,168,56,.92)'">Recolor Lenses</button>
    </div>`;
  }
  if(o.tryon_status==='error') return `<div class="tryon-error">Try-on could not be generated${o.tryon_error?': '+o.tryon_error:''}</div>`;
  return '<div class="tryon-loading"><div class="mini-spin"></div><p>Generating try-on...</p></div>';
}

function fsBuildCard(opt,index){
  const el=document.createElement('div');
  el.className='result-card';
  const label=index===0?'Best Match':'Alternative '+index;

  let extraHtml='';
  if(index===0){
    const reasons=[];
    if(opt.shape)reasons.push(capitalize(opt.shape)+' shape');
    if(opt.color)reasons.push(capitalize(opt.color)+' tones');
    if(opt.material)reasons.push(capitalize(opt.material));
    if(reasons.length)extraHtml='<div class="result-extra">'+reasons.join(' &middot; ')+'</div>';
  }

  el.innerHTML=`
    <div class="result-label">${label}</div>
    <div class="result-img-wrap">${getTryonHtml(opt)}</div>
    <div class="result-text">
      <div class="result-name">${opt.name}</div>
      <div class="result-tags">
        ${[opt.material,opt.color].filter(t=>t&&t.trim()).map(t=>'<span class="result-tag">'+t+'</span>').join('')}
      </div>
      <div class="result-price">${fmtPrice(opt)}</div>
      ${extraHtml}
    </div>
    <div class="result-img-wrap result-product-bg">
      <img src="data:image/jpeg;base64,${opt.product_b64}" alt="${opt.name}"/>
    </div>`;
  return el;
}

/* ── Compare modal ──────────────────────────── */
let compareData=null;
function openCompare(){
  if(!compareData) return;
  const grid=document.getElementById('compare-grid');
  grid.innerHTML='';
  for(let i=0;i<compareData.num_options;i++){
    const o=compareData['opt'+i];
    if(!o) continue;
    const card=document.createElement('div');
    card.className='compare-card';
    const imgSrc=o.tryon_status==='done'&&o.tryon_b64
      ?`data:image/png;base64,${o.tryon_b64}`
      :`data:image/jpeg;base64,${o.product_b64}`;
    card.innerHTML=`
      <img class="compare-card-img" src="${imgSrc}" alt="${o.name}"/>
      <div class="compare-card-footer">
        <span class="compare-card-price">${fmtPrice(o)}</span>
      </div>`;
    grid.appendChild(card);
  }
  document.getElementById('compare-modal').style.display='flex';
  document.body.style.overflow='hidden';
}
function closeCompare(){
  document.getElementById('compare-modal').style.display='none';
  document.body.style.overflow='';
}

/* ── Render results ──────────────────────────── */
function renderResults(d){
  const sig=JSON.stringify([...(Array.from({length:d.num_options},(_,i)=>d['opt'+i]?.tryon_status))]);
  if(sig===lastRendered){showView('results-view');return}
  lastRendered=sig;
  compareData=d;
  showView('results-view');
  const container=document.getElementById('fs-opts');
  container.innerHTML='';

  for(let i=0;i<Math.min(d.num_options,3);i++){
    const o=d['opt'+i];
    if(!o)continue;
    container.appendChild(fsBuildCard(o,i));
  }
}

/* ── Error ───────────────────────────────────── */
function showError(msg){
  stopTips();
  document.getElementById('error-msg').textContent=msg;
  showView('error-view');
}

/* ── Reset ───────────────────────────────────── */
function fsReset(){
  sid=null; if(poll)clearTimeout(poll); stopTips(); lastRendered='';
  uploadedFile=null;
  fileIn.value='';
  uploadArea.classList.remove('has-file');
  document.getElementById('up-label-text').textContent='Upload a Photo';
  preview.style.display='none'; preview.src='';
  submitBtn.disabled=true;
  submitHint.textContent='Upload a photo first';
  document.getElementById('fs-opts').innerHTML='';
  document.querySelectorAll('#form-view input[type=radio][value=""]').forEach(r=>{r.checked=true});
  setLoadStep(1); setLoadProg(0);
  showView(null);
}
