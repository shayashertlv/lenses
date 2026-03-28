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
    const el=document.getElementById(id);
    el.style.display = (id==='loading-view'||id==='error-view')?'flex':'block';
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

/* ── Color accent system ─────────────────────── */
const COLOR_ACCENTS={
  'transparent':{hue:220,color:'#8BA4C4'},'black':{hue:270,color:'#9B8AB8'},
  'gold':{hue:38,color:'#C4A265'},'silver':{hue:210,color:'#A0B4C8'},
  'tortoiseshell':{hue:28,color:'#B8884D'},'brown':{hue:25,color:'#A87D5A'},
  'blue':{hue:215,color:'#5B8FC4'},'red':{hue:355,color:'#C46B6B'},
  'pink':{hue:340,color:'#C47B99'},'green':{hue:150,color:'#5BAA7D'},
  'white':{hue:220,color:'#A8B4C4'},'tortoise':{hue:28,color:'#B8884D'},
};
function getAccent(colorStr){
  const lower=(colorStr||'').toLowerCase();
  for(const[key,val] of Object.entries(COLOR_ACCENTS)){
    if(lower.includes(key)) return val;
  }
  return{hue:250,color:'#8B7BFF'};
}

const checkSvg='<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8.5l3.5 3.5 6.5-7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

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

function getScores(o){
  const fit=o.fit_score?Math.round(o.fit_score*100):Math.min(99,Math.round(o.score*100+8));
  const style=o.style_score?Math.round(o.style_score*100):Math.max(30,Math.round(o.score*100-2));
  const color=o.color_score?Math.round(o.color_score*100):Math.max(30,Math.round(o.score*100-5));
  const overall=Math.round(o.score*100);
  return{fit,style,color,overall};
}

function buildScoreBars(scores,accent,delays){
  const rows=[
    {label:'Fit',val:scores.fit,delay:delays[0]||0},
    {label:'Style',val:scores.style,delay:delays[1]||0.1},
    {label:'Color',val:scores.color,delay:delays[2]||0.2},
  ];
  return rows.map(r=>`
    <div class="score-row">
      <span class="score-label">${r.label}</span>
      <div class="score-track">
        <div class="score-fill" style="--target-width:${r.val}%;background:${accent};animation-delay:${r.delay}s"></div>
      </div>
      <span class="score-val">${r.val}</span>
    </div>`).join('');
}

function buildScoreRing(overall,accent){
  const circ=2*Math.PI*22;
  const offset=circ-(overall/100)*circ;
  return `<div class="score-ring-wrap">
    <svg class="score-ring" width="52" height="52" viewBox="0 0 52 52">
      <circle cx="26" cy="26" r="22" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="4"/>
      <circle cx="26" cy="26" r="22" fill="none" stroke="${accent}" stroke-width="4"
        stroke-linecap="round" stroke-dasharray="${circ.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}"
        style="transition:stroke-dashoffset 1.2s cubic-bezier(.4,0,.2,1)"/>
    </svg>
    <span class="score-ring-num">${overall}</span>
  </div>`;
}

function generateWhyReasons(opt){
  const reasons=[];
  if(opt.shape) reasons.push(capitalize(opt.shape)+' shape complements your face proportions');
  if(opt.color) reasons.push(capitalize(opt.color)+' tones complement your complexion');
  if(opt.material) reasons.push(capitalize(opt.material)+' material matches your style preference');
  return reasons.slice(0,3);
}

function buildTagsHtml(o,accent){
  const tags=[o.material,o.color].filter(t=>t&&t.trim());
  return tags.map(t=>`<span class="tag-dynamic" style="background:${accent}15;color:${accent};border:1px solid ${accent}30">${t}</span>`).join('');
}

function buildHeroSection(opt){
  const accent=getAccent(opt.color);
  const scores=getScores(opt);
  const reasons=generateWhyReasons(opt);
  const el=document.createElement('div');
  el.className='hero-section';
  el.innerHTML=`
    <div class="hero-badge">&#10022; Best Match</div>
    <div class="hero-grid">
      <div class="hero-tryon-wrap">
        <div class="hero-glow"></div>
        ${getTryonHtml(opt)}
      </div>
      <div class="hero-panel">
        <img class="hero-product-img" src="data:image/jpeg;base64,${opt.product_b64}" alt="${opt.name}"/>
        <div>
          <div class="hero-name">${opt.name}</div>
        </div>
        <div class="hero-tags">${buildTagsHtml(opt,accent.color)}</div>
        <div class="hero-price">${fmtPrice(opt)}</div>
        ${reasons.length?`<div class="why-section">
          <div class="why-title">Why this frame?</div>
          ${reasons.map(r=>`<div class="why-item">${checkSvg}<span>${r}</span></div>`).join('')}
        </div>`:''}
      </div>
    </div>`;
  return el;
}

function buildAltCard(opt,index){
  const accent=getAccent(opt.color);
  const scores=getScores(opt);
  const el=document.createElement('div');
  el.className='alt-card';
  el.innerHTML=`
    <div class="alt-card-img-wrap">
      ${getTryonHtml(opt)}
    </div>
    <div class="alt-card-body">
      <div class="alt-card-name">${opt.name}</div>
      <div class="alt-card-tags">${buildTagsHtml(opt,accent.color)}</div>
    </div>
    <div class="alt-card-footer">
      <span class="alt-card-price">${fmtPrice(opt)}</span>
      <span class="alt-card-label">Alternative ${index}</span>
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
    const accent=getAccent(o.color);
    const scores=getScores(o);
    const isBest=i===0;
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

  const bestOpt=d.opt0;
  if(bestOpt){
    const accent=getAccent(bestOpt.color);
    document.getElementById('results-view').style.setProperty('--accent',accent.color);
    document.getElementById('results-view').style.setProperty('--accent-hue',accent.hue);
  }

  if(d.num_options>0&&bestOpt){
    container.appendChild(buildHeroSection(bestOpt));
  }

  if(d.num_options>1){
    const divider=document.createElement('div');
    divider.className='section-divider';
    divider.innerHTML='<span>More Options</span>';
    container.appendChild(divider);

    const altGrid=document.createElement('div');
    altGrid.className='alt-grid';
    for(let i=1;i<d.num_options;i++){
      const o=d['opt'+i];
      if(!o) continue;
      altGrid.appendChild(buildAltCard(o,i));
    }
    container.appendChild(altGrid);
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
