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
    const el=document.getElementById(id);
    el.style.display='none';
  });
  if(view){
    const el=document.getElementById(view);
    el.style.display=view==='sf-processing'||view==='sf-error'?'flex':'block';
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

  // 1. Section label: "Your perfect match"
  const lbl1=document.createElement('div');
  lbl1.className='section-lbl';
  lbl1.textContent='Your perfect match';
  container.appendChild(lbl1);

  // 2. Primary card (i=0)
  if(d.num_options>0&&d.opt0){
    container.appendChild(buildPrimaryCard(d.opt0,d));
  }

  // 3. Section label: "Your face analysis"
  const lbl2=document.createElement('div');
  lbl2.className='section-lbl';
  lbl2.textContent='Your face analysis';
  container.appendChild(lbl2);

  // 4. Analysis cards
  container.appendChild(buildAnalysisCards(d));

  // 5. Alternatives
  if(d.num_options>1){
    const lbl3=document.createElement('div');
    lbl3.className='section-lbl';
    lbl3.textContent='More options';
    container.appendChild(lbl3);

    for(let i=1;i<d.num_options;i++){
      const o=d['opt'+i];
      if(!o)continue;
      container.appendChild(buildAlternativeCard(o,i));
    }
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

/* ── Helpers for new card layout ── */
const checkSvg14='<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="7" r="7" fill="#dcfce7"/><path d="M4 7l2 2 4-4" stroke="#16a34a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const checkSvg12='<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 6l2.5 2.5L9 4" stroke="#16a34a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

const _recolorStore={};let _recolorIdx=0;
function goRecolor(key){
  sessionStorage.setItem('recolor_preload',_recolorStore[key]);
  window.location.href='/lens-recolor';
}

function buildTryonHtml(o){
  if(o.tryon_status==='done'&&o.tryon_b64){
    const key='rc'+(++_recolorIdx);
    _recolorStore[key]=o.tryon_b64;
    return `<div style="position:relative;display:flex;align-items:center;justify-content:center">
      <img src="data:image/png;base64,${o.tryon_b64}" alt="Virtual try-on" style="max-width:100%;display:block"/>
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

function buildPrimaryCard(o,d){
  const card=document.createElement('div');
  card.className='primary-hero';
  const tryonHtml=buildTryonHtml(o);

  // Why bullets -- product-aware (built from face_summary + actual product)
  const fs=d.face_summary||{};
  let whyBullets=[];
  if(fs.face_shape&&o.shape){
    whyBullets.push('For your '+fs.face_shape+' face, '+o.shape+' frames '+
      ((['round','oval'].includes(fs.face_shape.toLowerCase()))?'maintain balance and complement your natural proportions.':
       (['square','rectangular'].includes(fs.face_shape.toLowerCase()))?'add contrast and soften your strong angles.':
       'work harmoniously with your facial structure.'));
  }
  if(fs.color_profile){
    const warm=fs.color_profile.toLowerCase().includes('warm');
    const cool=fs.color_profile.toLowerCase().includes('cool');
    whyBullets.push('Your '+(fs.color_profile||'coloring').toLowerCase()+' pairs naturally with '+escHtml(o.color)+' '+escHtml(o.material)+' frames'+(warm?' \u2014 warm tones echo your skin undertone and hair color.':cool?' \u2014 cool tones complement your complexion beautifully.':'.'));
  }
  if(fs.key_geometry){
    whyBullets.push('Your '+fs.key_geometry.toLowerCase()+' is balanced by the '+escHtml(o.shape)+' silhouette, creating a polished, well-proportioned look.');
  }
  let whyHtml='';
  if(whyBullets.length){
    whyHtml=`<div class="p-divider"></div>
      <div class="why-label">Why this works for you</div>
      <div class="why-list">
        ${whyBullets.map(t=>`<div class="why-item">${checkSvg14}<span>${t}</span></div>`).join('')}
      </div>`;
  }

  card.innerHTML=`<div class="primary-hero-inner">
    <div class="primary-tryon">${tryonHtml}</div>
    <div class="primary-panel">
      <div class="p-name">${escHtml(o.name)}</div>
      <div class="p-tags">
        ${[o.material,o.color].filter(t=>t&&t.trim()).map(t=>`<span class="p-tag">${escHtml(t)}</span>`).join('')}
      </div>
      <div class="p-price">${sfFmtPrice(o)}</div>
      <div class="p-micro">Includes standard lenses</div>
      ${whyHtml}
    </div>
  </div>`;
  return card;
}

function getAnalysisCards(d){
  const fs=d.face_summary||{};
  const insights=d.face_insights||[];

  // Card 1: Face shape
  let shape=fs.face_shape||'';
  let shapeDesc=fs.face_shape_description||'';
  if(!shape&&insights[0]){
    const words=['oblong','oval','round','square','heart','diamond','rectangular','triangular','long'];
    const t=insights[0].toLowerCase();
    for(const w of words){if(t.includes(w)){shape=w;break;}}
    shapeDesc=insights[0].split('.')[0].trim();
  }
  if(!shape)shape='Analyzed';
  shape=shape.charAt(0).toUpperCase()+shape.slice(1);
  if(shapeDesc.length>80)shapeDesc=shapeDesc.substring(0,77)+'...';

  // Card 2: Key geometry
  let geo=fs.key_geometry||'';
  let geoDesc=fs.key_geometry_description||'';
  if(!geo&&insights.length>1){
    const geoWords=['tapered jawline','strong jawline','soft jawline','angular jaw',
      'rounded jaw','defined jawline','narrow jaw','wide jaw','prominent cheekbones',
      'high cheekbones','broad forehead','narrow forehead','balanced proportions'];
    const t=(insights[0]+' '+insights[1]).toLowerCase();
    for(const g of geoWords){if(t.includes(g)){geo=g;break;}}
    geoDesc=insights[1].split('.')[0].trim();
  }
  if(!geo)geo='Analyzed';
  geo=geo.split(' ').map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(' ');
  if(geoDesc.length>80)geoDesc=geoDesc.substring(0,77)+'...';

  // Card 3: Color profile
  let color=fs.color_profile||'';
  let colorDesc=fs.color_profile_description||'';
  let hairHex=fs.hair_color_hex||'#2A2A2A';
  let eyeHex=fs.eye_color_hex||'#3E2723';
  let skinHex=fs.skin_tone_hex||'#EAC0A2';
  if(!color&&insights.length>2){
    const t=insights[2].toLowerCase();
    if(t.includes('high contrast')||(t.includes('dark')&&t.includes('neutral')))color='High contrast';
    else if(t.includes('warm'))color='Warm tones';
    else if(t.includes('cool'))color='Cool tones';
    else if(t.includes('light'))color='Light palette';
    colorDesc=insights[2].split('.')[0].trim();
  }
  if(!color)color='Analyzed';
  if(colorDesc.length>80)colorDesc=colorDesc.substring(0,77)+'...';

  // Sanitize hex values
  const hexRe=/^#[0-9a-fA-F]{6}$/;
  if(!hexRe.test(hairHex))hairHex='#2A2A2A';
  if(!hexRe.test(eyeHex))eyeHex='#3E2723';
  if(!hexRe.test(skinHex))skinHex='#EAC0A2';

  return {shape,shapeDesc,geo,geoDesc,color,colorDesc,hairHex,eyeHex,skinHex};
}

function buildAnalysisCards(d){
  const c=getAnalysisCards(d);
  const wrap=document.createElement('div');
  wrap.className='analysis-cards';

  // Card 1 -- Face Shape
  const c1=document.createElement('div');c1.className='analysis-card';
  c1.innerHTML=`<div class="analysis-vis">
    <svg width="70" height="70" viewBox="0 0 70 70" fill="none">
      <ellipse cx="35" cy="35" rx="18" ry="28" stroke="#3b82f6" stroke-width="1.5" fill="#dbeafe" fill-opacity="0.4"/>
      <line x1="35" y1="5" x2="35" y2="63" stroke="#3b82f6" stroke-width="0.8" stroke-dasharray="3 2"/>
      <path d="M33 7L35 4L37 7" stroke="#3b82f6" stroke-width="0.8" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M33 63L35 66L37 63" stroke="#3b82f6" stroke-width="0.8" stroke-linecap="round" stroke-linejoin="round"/>
      <line x1="14" y1="35" x2="56" y2="35" stroke="#3b82f6" stroke-width="0.8" stroke-dasharray="3 2"/>
      <path d="M16 33L13 35L16 37" stroke="#3b82f6" stroke-width="0.8" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M54 33L57 35L54 37" stroke="#3b82f6" stroke-width="0.8" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </div>
  <div class="analysis-label">Face Shape</div>
  <div class="analysis-value">${escHtml(c.shape)}</div>
  <div class="analysis-desc">${escHtml(c.shapeDesc)}</div>`;
  wrap.appendChild(c1);

  // Card 2 -- Key Geometry
  const c2=document.createElement('div');c2.className='analysis-card';
  c2.innerHTML=`<div class="analysis-vis">
    <svg width="70" height="70" viewBox="0 0 70 70" fill="none">
      <path d="M15 18Q15 10 35 8Q55 10 55 18L55 32Q55 40 48 50L35 62L22 50Q15 40 15 32Z" stroke="#d8d7e5" stroke-width="1" fill="none" opacity="0.3"/>
      <path d="M18 34Q18 42 24 50L35 60L46 50Q52 42 52 34" stroke="#d97706" stroke-width="1.5" fill="#fef3c7" fill-opacity="0.35" stroke-linecap="round"/>
      <line x1="18" y1="34" x2="35" y2="60" stroke="#d97706" stroke-width="0.7" stroke-dasharray="2.5 2" opacity="0.7"/>
      <line x1="52" y1="34" x2="35" y2="60" stroke="#d97706" stroke-width="0.7" stroke-dasharray="2.5 2" opacity="0.7"/>
      <circle cx="18" cy="34" r="2" fill="#d97706" opacity="0.6"/>
      <circle cx="52" cy="34" r="2" fill="#d97706" opacity="0.6"/>
      <circle cx="35" cy="60" r="2" fill="#d97706" opacity="0.6"/>
      <line x1="18" y1="34" x2="52" y2="34" stroke="#d97706" stroke-width="0.6" stroke-dasharray="2 2" opacity="0.4"/>
    </svg>
  </div>
  <div class="analysis-label">Key Geometry</div>
  <div class="analysis-value">${escHtml(c.geo)}</div>
  <div class="analysis-desc">${escHtml(c.geoDesc)}</div>`;
  wrap.appendChild(c2);

  // Card 3 -- Color Profile
  const c3=document.createElement('div');c3.className='analysis-card';
  c3.innerHTML=`<div class="analysis-vis">
    <div class="color-vis-wrap">
      <div class="color-circle">
        <div style="flex:1;background:${c.hairHex}"></div>
        <div style="flex:1;background:${c.eyeHex}"></div>
        <div style="flex:1;background:${c.skinHex}"></div>
      </div>
      <div class="color-legend">
        <div class="color-legend-item"><div class="color-dot" style="background:${c.hairHex}"></div>Hair</div>
        <div class="color-legend-item"><div class="color-dot" style="background:${c.eyeHex}"></div>Eyes</div>
        <div class="color-legend-item"><div class="color-dot" style="background:${c.skinHex}"></div>Skin</div>
      </div>
    </div>
  </div>
  <div class="analysis-label">Color Profile</div>
  <div class="analysis-value">${escHtml(c.color)}</div>
  <div class="analysis-desc">${escHtml(c.colorDesc)}</div>`;
  wrap.appendChild(c3);

  return wrap;
}

function buildAlternativeCard(o,idx){
  const card=document.createElement('div');
  card.className='opt-card';
  const tryonHtml=buildTryonHtml(o);
  card.innerHTML=`
    <div class="opt-label">Alternative ${idx}</div>
    <div class="opt-body">
      <div class="tryon-col">${tryonHtml}</div>
      <div class="prod-col">
        <img src="data:image/jpeg;base64,${o.product_b64}" alt="${escHtml(o.name)}"/>
        <div class="prod-info">
          <h3>${escHtml(o.name)}</h3>
          <div class="tags">
            ${[o.material,o.color].filter(t=>t&&t.trim()).map(t=>`<span class="tag">${escHtml(t)}</span>`).join('')}
          </div>
          <p class="price">${sfFmtPrice(o)}</p>
        </div>
      </div>
    </div>`;
  return card;
}

/* ── Shared render function for result cards ── */
function renderOpts(container,d){
  container.innerHTML='';
  for(let i=0;i<d.num_options;i++){
    const o=d['opt'+i]; if(!o) continue;
    const primary=i===0;
    const card=document.createElement('div');
    card.className='opt-card'+(primary?' primary':'');
    const label=primary?'Best Match \u2014 Recommended For You':'Alternative '+(i);

    let tryonHtml;
    if(o.tryon_status==='done'&&o.tryon_b64){
      tryonHtml=`<img src="data:image/png;base64,${o.tryon_b64}" alt="Virtual try-on"/>`;
    }else if(o.tryon_status==='error'){
      tryonHtml=`<div class="tryon-error">Try-on could not be generated${o.tryon_error?': '+o.tryon_error:''}</div>`;
    }else{
      tryonHtml=`<div class="tryon-loading"><div class="mini-spin"></div><p>Generating try-on...</p></div>`;
    }
    const price=o.price.toLocaleString()+' '+o.currency;
    card.innerHTML=`
      <div class="opt-label">${label}</div>
      <div class="opt-body">
        <div class="tryon-col">${tryonHtml}</div>
        <div class="prod-col">
          <img src="data:image/jpeg;base64,${o.product_b64}" alt="${o.name}"/>
          <div class="prod-info">
            <h3>${o.name}</h3>
            <div class="tags">
              ${[o.material,o.color].filter(t=>t&&t.trim()).map(t=>`<span class="tag">${escHtml(t)}</span>`).join('')}
            </div>
            <p class="price">${price}</p>
            </div>
        </div>
      </div>`;
    container.appendChild(card);
  }
}
