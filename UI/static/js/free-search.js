/* ══════════════════════════════════════════════════
   Free Search — page-specific JavaScript
   ══════════════════════════════════════════════════ */

/* ── State ──────────────────────────────────── */
let sid=null, poll=null, tipTimer=null, tipIdx=0, uploadedFile=null, lastRendered='';
let catalogProducts=null; // loaded from /api/catalog for faceting
let fsCreep=null;
let fsRetry=null;

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

  setLoadStep(1);
  fsCreep=new ProgressCreep(document.getElementById('load-prog'));
  fsCreep.set(15);
  document.getElementById('load-stage').textContent='Searching our catalog...';
  fsRetry=new PollRetry('load-stage', showError);
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
function setLoadProg(pct){if(fsCreep)fsCreep.set(pct);else document.getElementById('load-prog').style.width=pct+'%'}

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
    if(fsRetry) fsRetry.reset();
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
      stopTips();if(fsCreep)fsCreep.finish();
      renderResults(d);
    }

    if(d.status!=='done' && d.status!=='error')
      poll=setTimeout(pollStatus,2000);
    else if(d.status==='done'){
      stopTips();if(fsCreep)fsCreep.finish();
      renderResults(d);
    }
  }).catch(()=>{if(fsRetry)fsRetry.fail();poll=setTimeout(pollStatus,3000)});
}

const _recolorStore={};let _recolorIdx=0;
function goRecolor(key){
  try{sessionStorage.setItem('recolor_preload',_recolorStore[key])}catch(e){}
  window.location.href='/lens-recolor';
}

function getTryonHtml(o){
  if(o.tryon_status==='done'&&o.tryon_b64){
    const key='rc'+(++_recolorIdx);
    _recolorStore[key]=o.tryon_b64;
    return '<div class="tryon-done">' +
      '<img src="data:image/png;base64,'+o.tryon_b64+'" alt="You wearing '+escHtml(o.name)+'"/>' +
      '<button class="recolor-btn" type="button" data-rc="' + key + '">Recolour lenses</button>' +
      '</div>';
  }
  if(o.tryon_status==='error') return '<div class="tryon-fail">Could not be rendered'+(o.tryon_error?': '+escHtml(o.tryon_error):'')+'</div>';
  return '<div class="tryon-wait"><span class="tryon-wait-bar"></span>Rendering</div>';
}

/* Delegated: the recolour key rides on a data attribute so no amount of
   quoting inside an interpolated onclick can break the parse again. */
document.addEventListener('click', function (e) {
  const b = e.target.closest && e.target.closest('.recolor-btn[data-rc]');
  if (b) { e.preventDefault(); goRecolor(b.dataset.rc); }
});

function fsBuildCard(o,index){
  const el=document.createElement('article');
  el.className='result-card'+(index===0?' is-primary':'');
  const label=index===0?'Best match':'Alternate '+index;
  const spec=[o.material,o.color].filter(Boolean).map(escHtml).join(' &middot; ');
  const scores=[
    o.fit_score!=null?['Fit',o.fit_score]:null,
    o.style_score!=null?['Style',o.style_score]:null,
    o.color_score!=null?['Colour',o.color_score]:null,
  ].filter(Boolean);

  el.innerHTML=
    '<div class="result-img">'+getTryonHtml(o)+'</div>'+
    '<div class="result-body">'+
      '<span class="result-label">'+label+'</span>'+
      '<span class="result-brand">'+escHtml(o.brand||'')+'</span>'+
      '<span class="result-name">'+escHtml(modelName(o))+'</span>'+
      (spec?'<span class="result-spec">'+spec+'</span>':'')+
      (scores.length
        ? '<div class="scores">'+scores.map(x=>
            '<span class="score"><span class="score-k">'+x[0]+'</span>'+
            '<span class="score-v">'+Math.round(x[1])+'</span></span>').join('')+'</div>'
        : '')+
      '<div class="result-foot">'+
        '<span class="price sg-price">'+fsPrice(o)+'</span>'+
        (o.product_b64?'<img class="result-thumb" src="data:image/jpeg;base64,'+o.product_b64+'" alt=""/>':'')+
      '</div>'+
    '</div>';
  return el;
}

function fsPrice(o){
  const sym=o.currency==='ILS'?'₪':escHtml(o.currency||'');
  return sym+Number(o.price||0).toLocaleString();
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
      <img class="compare-card-img" src="${imgSrc}" alt="${escHtml(o.name)}"/>
      <div class="compare-card-footer">
        <span class="compare-card-price sg-price">${fsPrice(o)}</span>
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
  stopTips();if(fsCreep)fsCreep.stop();
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
  if(fsCreep){fsCreep.stop();fsCreep=null}
  setLoadStep(1); setLoadProg(0);
  showView(null);
  updateFacets();
}

/* ══════════════════════════════════════════════════
   Advanced Options toggle
   ══════════════════════════════════════════════════ */
function toggleAdvanced(){
  const sec=document.getElementById('advanced-section');
  const btn=document.getElementById('advanced-toggle');
  sec.classList.toggle('open');
  btn.classList.toggle('open');
}

/* ══════════════════════════════════════════════════
   Client-side faceting — dim unavailable options
   ══════════════════════════════════════════════════ */

// Map UI input names → catalog endpoint field names
const FIELD_MAP={
  gender:'gender', frame_shape:'shape', lens_type:'lens_type',
  frame_color:'color', lens_color:'lens_color', frame_material:'material', rim_type:'rim_type',
  frame_thickness:'thickness', lens_size:'lens_size',
  aesthetic:'aesthetic', occasion:'occasion'
};

// Primary filter fields (drive dimming for all others)
const PRIMARY_FIELDS=['gender','frame_shape','lens_type','frame_color','lens_color'];

// Color fields that need normalization before facet comparison
const COLOR_FIELDS=new Set(['frame_color','lens_color']);

// Lightweight color normalizer — kept in sync with backend _norm_color()
const _COLOR_ALIAS={
  // Black family
  'dark-grey':'black','dark-gray':'black','dark grey':'black','dark gray':'black',
  'jet-black':'black','jet black':'black','dark-black':'black',
  // Tortoiseshell
  'havana-brown':'tortoiseshell','havana':'tortoiseshell',
  // Brown family
  'amber':'brown','cognac':'brown','caramel':'brown','chocolate':'brown',
  'espresso':'brown','tan':'brown','bronze':'brown','copper':'brown',
  'beige':'brown','mocha':'brown','honey':'brown','chestnut':'brown','walnut':'brown',
  // Silver / gray family
  'gunmetal':'silver','grey':'gray','charcoal':'gray','slate':'gray',
  'pewter':'silver','chrome':'silver','steel':'silver','dark-silver':'silver',
  'light-gray':'gray','light-grey':'gray','light gray':'gray','light grey':'gray',
  // Gold family
  'champagne':'gold','brass':'gold','antique-gold':'gold','light-gold':'gold',
  // Blue family
  'navy':'blue','navy-blue':'blue','navy blue':'blue',
  'dark-blue':'blue','dark blue':'blue',
  'cobalt':'blue','teal':'blue','royal-blue':'blue','sky-blue':'blue',
  // Green family
  'olive':'green','dark-green':'green','dark green':'green',
  'forest-green':'green','emerald':'green','sage':'green','khaki':'green',
  // Red / pink family
  'burgundy':'red','wine':'red','maroon':'red','dark-red':'red','crimson':'red',
  'coral':'pink','salmon':'pink','blush':'pink','fuchsia':'pink','magenta':'pink',
  // Other
  'ivory':'white','cream':'white','bone':'white','off-white':'white',
  'crystal':'transparent','clear':'transparent','nude':'transparent',
  'violet':'purple','lavender':'purple','plum':'purple',
  'g-15-green':'green'
};
const _ADJ_RE=/^(?:matte[-\s]|gradient[-\s]|mirrored[-\s]|polarized[-\s]|polished[-\s]|glossy[-\s]|rubber[-\s]|solid[-\s]|bright[-\s]|deep[-\s]|satin[-\s]|g-15[-\s])/i;
function normColor(raw){
  const lc=raw.toLowerCase().trim();
  if(_COLOR_ALIAS[lc]) return _COLOR_ALIAS[lc];
  const stripped=lc.replace(_ADJ_RE,'');
  if(_COLOR_ALIAS[stripped]) return _COLOR_ALIAS[stripped];
  return stripped;
}

// Shape normalizer — kept in sync with backend _norm_shape()
const _SHAPE_ALIAS={
  'butterfly':'cat-eye','oversized-round':'round','oversized-square':'square',
  'pilot':'aviator','teardrop':'aviator','flat-top':'square',
  'curved-wrap':'wrap','shield':'wrap'
};
function normShape(raw){ const lc=raw.toLowerCase().trim(); return _SHAPE_ALIAS[lc]||lc; }

// Material normalizer — kept in sync with backend _norm_material()
const _MATERIAL_ALIAS={
  'stainless-steel':'metal','mixed-metal':'metal','mixed-metal-acetate':'metal',
  'mixed-metal-plastic':'metal','mixed-metal-nylon':'metal',
  'mixed-metal-carbon':'metal','mixed-metal-injected':'metal',
  'propionate':'plastic','bio-injected':'plastic',
  'recycled-injected':'plastic','o-matter':'plastic',
  'recycled-acetate':'acetate','bio-nylon':'nylon'
};
function normMaterial(raw){ const lc=raw.toLowerCase().trim(); return _MATERIAL_ALIAS[lc]||lc; }

// Split comma-separated catalog values into a lowercase set
function catVals(product,catField){
  const v=product[catField];
  if(Array.isArray(v)) return v.map(s=>s.toLowerCase());
  if(typeof v==='string'&&v) return v.split(/,\s*/).map(s=>s.toLowerCase());
  return [];
}

// Check if a product matches a single filter {inputName, value}
function productMatches(p,inputName,value){
  const catField=FIELD_MAP[inputName];
  if(!catField||!value) return true;
  let vals=catVals(p,catField);
  let q=value.toLowerCase();
  // Gender: unisex products match any gender selection
  if(inputName==='gender'){
    return vals.includes(q)||vals.includes('unisex');
  }
  // Normalize both sides for color, shape, material
  if(COLOR_FIELDS.has(inputName)){ vals=vals.map(normColor); q=normColor(q); }
  else if(inputName==='frame_shape'){ vals=vals.map(normShape); q=normShape(q); }
  else if(inputName==='frame_material'){ vals=vals.map(normMaterial); q=normMaterial(q); }
  return vals.includes(q);
}


/* Category signage is "name left, live count right". After faceting, each
   band reports how many of its options are still reachable given the other
   choices -- the number the contract asks for, from the pool updateFacets
   already computed. */
function updateSignageCounts(root) {
  const scope = root || document;
  scope.querySelectorAll('.section-title').forEach(title => {
    let group = title.nextElementSibling;
    /* Stop at the next band: a section with no controls of its own (the photo
       tray) must not borrow the following section's options. */
    while (group && !group.matches('.tile-grid,.swatch-grid,.chip-group,.radio-group')) {
      if (group.matches('.section-title')) return;
      group = group.nextElementSibling;
    }
    if (!group) return;
    const opts = group.querySelectorAll('label.tile,label.swatch,span');
    let total = 0, live = 0;
    opts.forEach(o => {
      const input = o.querySelector('input');
      if (!input || !input.value) return;      // "Any" is not an option to count
      total++;
      if (!o.classList.contains('dimmed')) live++;
    });
    if (!total) return;
    let c = title.querySelector('.section-count');
    if (!c) {
      c = document.createElement('span');
      c.className = 'section-count sg-figure';
      title.appendChild(c);
    }
    c.textContent = live === total ? String(total) : live + ' of ' + total;
  });
}

function updateFacets(){
  if(!catalogProducts) return;

  // Collect current primary filter selections
  const filters={};
  PRIMARY_FIELDS.forEach(name=>{
    const el=document.querySelector(`input[name="${name}"]:checked`);
    if(el&&el.value) filters[name]=el.value;
  });

  // For each field, compute available values by applying
  // all OTHER active primary filters (exclude the field itself)
  const allFields=Object.keys(FIELD_MAP);
  allFields.forEach(inputName=>{
    // Build filtered pool: apply all primary filters except this field
    const pool=catalogProducts.filter(p=>{
      if(!p.in_stock) return false;
      for(const [f,v] of Object.entries(filters)){
        if(f===inputName) continue;
        if(!productMatches(p,f,v)) return false;
      }
      return true;
    });

    // Collect all values present in the pool for this field
    const catField=FIELD_MAP[inputName];
    const available=new Set();
    const isColor=COLOR_FIELDS.has(inputName);
    const isShape=inputName==='frame_shape';
    const isMaterial=inputName==='frame_material';
    pool.forEach(p=>{
      catVals(p,catField).forEach(v=>{
        if(isColor) available.add(normColor(v));
        else if(isShape) available.add(normShape(v));
        else if(isMaterial) available.add(normMaterial(v));
        else available.add(v);
      });
    });
    // "unisex" products contribute to both "men" and "women" availability
    if(inputName==='gender'&&available.has('unisex')){
      available.add('men');
      available.add('women');
    }

    // Apply dimmed class to options not in the available set
    document.querySelectorAll(`input[name="${inputName}"]`).forEach(radio=>{
      if(!radio.value) return; // skip "Any"
      const label=radio.closest('label')||radio.closest('span');
      if(!label) return;
      let rv=radio.value.toLowerCase();
      if(isColor) rv=normColor(rv);
      else if(isShape) rv=normShape(rv);
      else if(isMaterial) rv=normMaterial(rv);
      const dimmed=!available.has(rv);
      label.classList.toggle('dimmed',dimmed);
    });
  });
  updateSignageCounts(null);
}

// Load catalog on page init and wire up faceting
(function initFaceting(){
  fetch('/api/catalog').then(r=>r.json()).then(data=>{
    catalogProducts=data;
    updateFacets();
  }).catch(()=>{});

  // Re-run facets whenever any primary filter changes
  PRIMARY_FIELDS.forEach(name=>{
    document.querySelectorAll(`input[name="${name}"]`).forEach(radio=>{
      radio.addEventListener('change',updateFacets);
    });
  });
})();
