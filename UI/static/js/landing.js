/* ── Smart Fit inline flow ── */
const tips=[
  "Round faces pair best with angular frames to add definition.",
  "The top of your frames should follow your brow line for the most natural look.",
  "Titanium frames are up to 40% lighter than standard metal.",
  "Semi-rimless frames are the most popular style for professional settings.",
  "Acetate frames come in more colors and patterns than any other material.",
  "Heart-shaped faces look great in bottom-heavy frames.",
  "Blue-light filtering lenses can reduce digital eye strain by up to 23%.",
  "Warm skin undertones pair beautifully with tortoiseshell and gold frames.",
  "Cool skin undertones are complemented by silver, black, and jewel-toned frames.",
  "Square faces benefit from rounded frames that soften strong angles.",
];
let sfSid=null,sfPoll=null,sfTipTimer=null,sfTipIdx=0,sfLastRendered='';

function sfStartTips(){sfTipIdx=0;sfShowTip();sfTipTimer=setInterval(()=>{sfTipIdx=(sfTipIdx+1)%tips.length;sfShowTip()},4500)}
function sfStopTips(){if(sfTipTimer)clearInterval(sfTipTimer);sfTipTimer=null}
function sfShowTip(){const t=document.getElementById('sf-tip');t.style.opacity='0';setTimeout(()=>{t.textContent=tips[sfTipIdx];t.style.opacity='1'},350)}

function sfShow(view){
  ['sf-processing','sf-results','sf-error'].forEach(id=>{
    document.getElementById(id).style.display='none';
  });
  if(view){
    document.getElementById(view).style.display='flex';
  }
}

function sfSetStep(n){
  for(let i=1;i<=3;i++){
    const s=document.getElementById('sf-s'+i);
    s.classList.remove('active','done');
    if(i<n)s.classList.add('done');else if(i===n)s.classList.add('active');
  }
  document.getElementById('sf-sl1').className='sf-line'+(n>1?' done':'');
  document.getElementById('sf-sl2').className='sf-line'+(n>2?' done':'');
}

const sfProgMap={uploading:5,analyzing:20,matching:50,tryon:65,primary_ready:85,done:100};

document.getElementById('sf-file').addEventListener('change', async e=>{
  const f=e.target.files[0]; if(!f) return;
  sfShow('sf-processing'); sfSetStep(1);
  document.getElementById('sf-prog').style.width='5%';
  document.getElementById('sf-stage').textContent='Uploading your photo...';
  sfStartTips();

  const reader=new FileReader();
  reader.onload=ev=>{
    document.getElementById('sf-portrait-img').src=ev.target.result;
    document.getElementById('sf-portrait-wrap').style.display='block';
    try{sessionStorage.setItem('cached_portrait',ev.target.result)}catch(e){}
  };
  reader.readAsDataURL(f);

  const fd=new FormData(); fd.append('photo',f);
  try{
    const r=await fetch('/api/upload',{method:'POST',body:fd});
    const j=await r.json();
    if(j.error){sfShowError(j.error);return}
    sfSid=j.session_id; sfPollStatus();
  }catch(err){sfShowError('Upload failed: '+err.message)}
});

function sfPollStatus(){
  if(!sfSid)return;
  fetch('/api/status/'+sfSid).then(r=>r.json()).then(d=>{
    if(d.status==='error'){sfShowError(d.error||'Unknown error');return}
    const msgs={uploading:'Uploading...',analyzing:'Analyzing your facial features...',
      matching:'Searching catalog...',tryon:'Generating virtual try-on images...',
      primary_ready:'Your best match is ready!',done:'All recommendations ready'};
    const stepMap={uploading:1,analyzing:1,matching:2,tryon:3,primary_ready:3,done:3};
    document.getElementById('sf-stage').textContent=msgs[d.stage]||'Processing...';
    sfSetStep(stepMap[d.stage]||1);
    document.getElementById('sf-prog').style.width=(sfProgMap[d.stage]||5)+'%';

    if(d.num_options>0&&(d.stage==='primary_ready'||d.stage==='done')){sfStopTips();sfRender(d)}
    if(d.status!=='done'&&d.status!=='error')sfPoll=setTimeout(sfPollStatus,2000);
    else if(d.status==='done'){sfStopTips();sfRender(d)}
  }).catch(()=>{sfPoll=setTimeout(sfPollStatus,3000)});
}

function sfShowError(msg){sfStopTips();document.getElementById('sf-error-msg').textContent=msg;sfShow('sf-error')}

function sfRender(d){
  const sig=JSON.stringify([...(Array.from({length:d.num_options},(_,i)=>d['opt'+i]?.tryon_status))]);
  if(sig===sfLastRendered)return;
  sfLastRendered=sig;
  sfShow('sf-results');

  const container=document.getElementById('sf-opts');
  container.innerHTML='';

  const fs=d.face_summary||{};
  for(let i=0;i<Math.min(d.num_options,3);i++){
    const o=d['opt'+i];
    if(!o)continue;
    container.appendChild(sfBuildCard(o,i,fs));
  }
}

function sfReset(){
  sfSid=null;if(sfPoll)clearTimeout(sfPoll);sfStopTips();sfLastRendered='';
  document.getElementById('sf-file').value='';
  document.getElementById('sf-opts').innerHTML='';
  document.getElementById('sf-portrait-wrap').style.display='none';
  document.getElementById('sf-portrait-img').src='';
  document.getElementById('sf-prog').style.width='0%';sfSetStep(1);
  sfShow(null);
}

/* ── Helpers ── */
const _recolorStore={};let _recolorIdx=0;
function goRecolor(key){
  try{sessionStorage.setItem('recolor_preload',_recolorStore[key])}catch(e){}
  window.location.href='/lens-recolor';
}

function buildTryonHtml(o){
  if(o.tryon_status==='done'&&o.tryon_b64){
    const key='rc'+(++_recolorIdx);
    _recolorStore[key]=o.tryon_b64;
    return `<div class="tryon-done-wrap">
      <img src="data:image/png;base64,${o.tryon_b64}" alt="Virtual try-on"/>
      <button class="recolor-overlay-btn" onclick="event.stopPropagation();goRecolor('${key}')" onmouseover="this.style.background='rgba(232,168,56,1)'" onmouseout="this.style.background='rgba(232,168,56,.92)'">Recolor Lenses</button>
    </div>`;
  }else if(o.tryon_status==='error'){
    return `<div class="tryon-error">Try-on could not be generated${o.tryon_error?': '+escHtml(o.tryon_error):''}</div>`;
  }
  return `<div class="tryon-loading"><div class="mini-spin"></div><p>Generating try-on...</p></div>`;
}

function sfFmtPrice(o){
  return o.currency==='ILS'?o.price.toLocaleString()+' \u20AA':o.price.toLocaleString()+' '+escHtml(o.currency);
}

function sfBuildChips(o,fs){
  const chips=[];
  /* face shape */
  if(fs.face_shape){
    chips.push(`<span class="result-chip"><svg viewBox="0 0 16 16"><path d="M8 1L14 5v6l-6 4-6-4V5z"/></svg>${escHtml(fs.face_shape)}</span>`);
  }
  /* color profile */
  if(fs.color_profile){
    chips.push(`<span class="result-chip"><svg viewBox="0 0 16 16"><circle cx="5" cy="11" r="3"/><circle cx="11" cy="11" r="3"/><circle cx="8" cy="5" r="3"/></svg>${escHtml(fs.color_profile)}</span>`);
  }
  /* key geometry */
  if(fs.key_geometry){
    chips.push(`<span class="result-chip"><svg viewBox="0 0 16 16"><path d="M1 15L8 1l7 14H1z"/></svg>${escHtml(fs.key_geometry)}</span>`);
  }
  /* color dots: hair, eye, skin */
  const dots=[];
  if(fs.hair_color_hex)dots.push(`<span class="color-dot" style="background:${escHtml(fs.hair_color_hex)}" title="Hair"></span>`);
  if(fs.eye_color_hex)dots.push(`<span class="color-dot" style="background:${escHtml(fs.eye_color_hex)}" title="Eyes"></span>`);
  if(fs.skin_tone_hex)dots.push(`<span class="color-dot" style="background:${escHtml(fs.skin_tone_hex)}" title="Skin"></span>`);
  if(dots.length)chips.push(`<span class="result-chip">${dots.join('')}</span>`);

  return chips.length?'<div class="result-chips">'+chips.join('')+'</div>':'';
}

function sfBuildCard(o,idx,fs){
  const card=document.createElement('div');
  card.className='result-card';
  const labelText=idx===0?'Best Match':'Alt '+(idx);
  const labelCls=idx===0?'result-label':'result-label alt';
  const chipsHtml=idx===0?sfBuildChips(o,fs):'';

  card.innerHTML=`
    <div class="result-img-wrap">${buildTryonHtml(o)}</div>
    <div class="result-info">
      <div class="result-thumb">
        <img src="data:image/jpeg;base64,${o.product_b64}" alt="${escHtml(o.name)}"/>
      </div>
      <div class="result-meta">
        <div class="result-head">
          <span class="${labelCls}">${labelText}</span>
          <span class="result-name">${escHtml(o.name)}</span>
        </div>
        <div class="result-tags">
          ${[o.material,o.color].filter(t=>t&&t.trim()).map(t=>'<span class="result-tag">'+escHtml(t)+'</span>').join('')}
        </div>
        <div class="result-price">${sfFmtPrice(o)}</div>
        ${chipsHtml}
      </div>
    </div>`;
  return card;
}

