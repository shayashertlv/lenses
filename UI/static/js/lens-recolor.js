/* ══════════════════════════════════════════════════
   Lens Recolor page JavaScript
   (page-specific — common.js loaded separately)
   ══════════════════════════════════════════════════ */

/* ── State ── */
let chosenFile = null;
let sessionId = null;
let pollTimer = null;
let mainIdx = 0;               // which color is in the hero
let colorResults = [];          // [{name, b64, error, status}]
let rcCreep = null;
let rcRetry = null;

/* ── Step indicators ── */
function rcSetStep(n){
  const s1=document.getElementById('rc-s1'), s2=document.getElementById('rc-s2');
  s1.classList.remove('active','done'); s2.classList.remove('active','done');
  if(n===1){s1.classList.add('active')}
  else{s1.classList.add('done');s2.classList.add('active')}
  document.getElementById('rc-sl1').className='rc-sline'+(n>1?' done':'');
}

/* ── View switching ── */
function showView(v){
  ['form-view','loading-view','results-view','error-view'].forEach(id=>{
    const el=document.getElementById(id);
    if(id===v){
      // results-view is a block scroll layout; loading/error use flex centering
      el.style.display=(id==='results-view'||id==='form-view')?'block':'flex';
    } else {
      el.style.display='none';
    }
  });
  if(v===null){
    document.getElementById('form-view').style.display='block';
  }
}

/* ── File picker ── */
document.getElementById('rc-file').addEventListener('change',function(e){
  const f=e.target.files[0]; if(!f) return;
  chosenFile=f;
  const area=document.getElementById('upload-area');
  area.classList.add('has-file');
  document.getElementById('up-label-text').textContent=f.name;
  const prev=document.getElementById('up-preview');
  const rcReader=new FileReader();
  rcReader.onload=ev=>{prev.src=ev.target.result;prev.style.display='block';try{sessionStorage.setItem('cached_portrait',ev.target.result)}catch(e){}};
  rcReader.readAsDataURL(f);
  checkReady();
});

/* ── Preload image from Smart Fit / Free Search ── */
(function(){
  const b64=sessionStorage.getItem('recolor_preload');
  if(!b64) return;
  sessionStorage.removeItem('recolor_preload');
  // Convert base64 to File object
  const byteStr=atob(b64);
  const ab=new ArrayBuffer(byteStr.length);
  const ia=new Uint8Array(ab);
  for(let i=0;i<byteStr.length;i++) ia[i]=byteStr.charCodeAt(i);
  const blob=new Blob([ab],{type:'image/png'});
  chosenFile=new File([blob],'tryon-photo.png',{type:'image/png'});
  const area=document.getElementById('upload-area');
  area.classList.add('has-file');
  document.getElementById('up-label-text').textContent='Photo from try-on';
  const prev=document.getElementById('up-preview');
  prev.src='data:image/png;base64,'+b64; prev.style.display='block';
  checkReady();
})();

/* ── Restore cached portrait from another page (fallback if no recolor_preload) ── */
(function(){
  if(chosenFile) return; // already loaded via recolor_preload
  const cached=sessionStorage.getItem('cached_portrait');
  if(!cached) return;
  const arr=cached.split(','), mime=arr[0].match(/:(.*?);/)[1];
  const bstr=atob(arr[1]), n=bstr.length, u8=new Uint8Array(n);
  for(let i=0;i<n;i++) u8[i]=bstr.charCodeAt(i);
  chosenFile=new File([u8],'cached-photo.jpg',{type:mime});
  const area=document.getElementById('upload-area');
  area.classList.add('has-file');
  document.getElementById('up-label-text').textContent='Photo from previous session';
  const prev=document.getElementById('up-preview');
  prev.src=cached; prev.style.display='block';
  checkReady();
})();

/* ── Color checkbox logic ── */
const MAX_COLORS=3;
function getCheckedColors(){
  return Array.from(document.querySelectorAll('input[name="lens_color"]:checked')).map(c=>c.value);
}
document.querySelectorAll('input[name="lens_color"]').forEach(cb=>{
  cb.addEventListener('change',function(){
    const checked=getCheckedColors();
    if(checked.length>MAX_COLORS){
      /* Refusing the fourth colour must not look like a dead control:
         the counter says why and nudges, mechanically. */
      this.checked=false;
      flashCounter();
      return;
    }
    updateCounter();
    checkReady();
  });
});
function flashCounter(){
  /* Field-green "full" state, not danger — Alert Red is failure only,
     and a full selection is not a failure. */
  const el=document.getElementById('color-counter');
  el.textContent='3 of 3 selected — deselect one to swap';
  el.className='color-counter full nudge';
  clearTimeout(flashCounter._t);
  flashCounter._t=setTimeout(updateCounter,1600);
}
function updateCounter(){
  const n=getCheckedColors().length;
  const el=document.getElementById('color-counter');
  el.textContent=n+' of 3 selected';
  el.className='color-counter'+(n===3?' full':'')+(n>3?' over':'');
}
function checkReady(){
  const n=getCheckedColors().length;
  const ready=chosenFile && n===3;
  document.getElementById('submit-btn').disabled=!ready;
  const hint=document.getElementById('rc-hint');
  if(hint){
    if(ready) hint.textContent='Ready — about 20 seconds';
    else if(!chosenFile && n===0) hint.textContent='Hand over a photo and pick three colours';
    else if(!chosenFile) hint.textContent='Hand over a photo first';
    else hint.textContent='Pick '+(3-n)+' more colour'+((3-n)===1?'':'s');
  }
}

/* ── Submit ── */
document.getElementById('submit-btn').addEventListener('click',function(){
  if(!chosenFile || getCheckedColors().length!==3) return;
  const colors=getCheckedColors();
  const fd=new FormData();
  fd.append('photo',chosenFile);
  fd.append('color1',colors[0]);
  fd.append('color2',colors[1]);
  fd.append('color3',colors[2]);

  showView('loading-view');
  document.getElementById('load-stage').textContent='Uploading your photo…';
  rcSetStep(1);
  rcCreep=new ProgressCreep(document.getElementById('load-prog-fill'));
  rcCreep.set(10);
  rcRetry=new PollRetry('load-stage', showError);
  rcNarrateStart(colors);

  // Show portrait preview in loading
  const reader=new FileReader();
  reader.onload=function(ev){
    document.getElementById('load-portrait-img').src=ev.target.result;
    document.getElementById('load-portrait').style.display='block';
  };
  reader.readAsDataURL(chosenFile);

  fetch('/api/lens-recolor',{method:'POST',body:fd})
    .then(r=>r.json())
    .then(data=>{
      if(data.error){showError(data.error);return}
      sessionId=data.session_id;
      pollTimer=setInterval(pollStatus,2000);
    })
    .catch(e=>showError(e.message));
});

/* ── Polling ── */
let resultsShown=false;

function collectResults(data){
  colorResults=[];
  for(let i=0;i<(data.num_colors||0);i++){
    const c=data['color'+i];
    if(c){colorResults.push({name:c.name,b64:c.b64,error:c.error,status:c.status});}
  }
}

function pollStatus(){
  if(!sessionId) return;
  fetch('/api/recolor-status/'+sessionId)
    .then(r=>r.json())
    .then(data=>{
      if(data.status==='error'){
        clearInterval(pollTimer);
        showError(data.error||'An error occurred');
        return;
      }
      if(rcRetry) rcRetry.reset();

      // Update loading stage (only while loading screen is visible).
      // Write only on change: the element is aria-live, and rewriting an
      // identical string every 2s poll makes screen readers re-announce it.
      if(!resultsShown){
        const stage=data.stage||'';
        const stEl=document.getElementById('load-stage');
        const setStage=t=>{if(stEl.textContent!==t)stEl.textContent=t};
        if(stage==='uploading'){
          setStage('Uploading your photo…');
          rcSetStep(1);
          if(rcCreep) rcCreep.set(15);
        } else if(stage==='recoloring'){
          setStage('Recolouring your lenses…');
          rcSetStep(2);
          if(rcCreep) rcCreep.set(40);
        } else if(stage==='primary_ready'){
          setStage('First colour ready — finishing the rest…');
          rcSetStep(2);
          if(rcCreep) rcCreep.set(70);
        }
        rcNarrateUpdate(data);

        // Show portrait in loading
        if(data.portrait_b64){
          const img=document.getElementById('load-portrait-img');
          if(!img.src || img.src===''){
            img.src='data:image/jpeg;base64,'+data.portrait_b64;
            document.getElementById('load-portrait').style.display='block';
          }
        }
      }

      // Show results as soon as at least one color is ready
      const hasAnyResult=(data.num_colors||0)>0 &&
        (data.stage==='primary_ready'||data.stage==='done');
      if(hasAnyResult){
        collectResults(data);
        if(!resultsShown){
          resultsShown=true;
          /* renderResults swaps the loading view away, so it waits for the bar
             to actually reach 100 — otherwise the landing animates onto a
             screen already gone. */
          if(rcCreep)rcCreep.finish(renderResults); else renderResults();
        } else {
          updateProgressiveResults();
        }
      }

      if(data.status==='done'){
        clearInterval(pollTimer);
      }
    })
    .catch(()=>{if(rcRetry)rcRetry.fail()});
}

/* ── Render results ── */
function renderResults(){
  // Find first successful result for hero
  mainIdx=0;
  for(let i=0;i<colorResults.length;i++){
    if(colorResults[i].b64){mainIdx=i;break;}
  }
  updateHero();
  renderAlts();
  showView('results-view');
}

function updateProgressiveResults(){
  // Re-render alt cards to replace spinners with newly arrived images
  // Only update hero if current hero has no image yet and a new one arrived
  const cur=colorResults[mainIdx];
  if(cur && !cur.b64 && !cur.error){
    // Current hero is still loading — check if it arrived
    for(let i=0;i<colorResults.length;i++){
      if(colorResults[i].b64){mainIdx=i;break;}
    }
    updateHero();
  }
  renderAlts();
}

function updateHero(){
  const r=colorResults[mainIdx];
  const heroImg=document.getElementById('hero-img');
  const heroWrap=document.getElementById('hero-img-wrap');
  const label=document.getElementById('hero-color-label');

  if(r && r.b64){
    heroWrap.innerHTML='<img id="hero-img" src="data:image/png;base64,'+r.b64+'" alt="Recoloured lens" style="display:block"/>';
    label.textContent=r.name;
  } else if(r && r.error){
    heroWrap.innerHTML='<div class="tryon-error">'+escHtml(r.error)+'</div>';
    label.textContent=r.name+' — failed';
  } else if(r){
    heroWrap.innerHTML='<div class="tryon-loading tryon-loading--hero"><div class="mini-spin mini-spin--lg"></div><p class="gen-note">Rendering '+escHtml(r.name)+'…</p></div>';
    label.textContent=r.name+' — rendering…';
  }
  renderAlts();
}

function renderAlts(){
  const grid=document.getElementById('alt-grid');
  grid.innerHTML='';
  for(let i=0;i<colorResults.length;i++){
    if(i===mainIdx) continue;
    const r=colorResults[i];
    const card=document.createElement('div');
    card.className='alt-card'+(i===mainIdx?' active':'');
    card.onclick=function(){switchTo(i)};
    card.style.cursor='pointer';

    let imgHtml='';
    if(r.b64){
      imgHtml='<img src="data:image/png;base64,'+r.b64+'" alt="'+escHtml(r.name)+'"/>';
    } else if(r.error){
      imgHtml='<div class="tryon-error">'+escHtml(r.error)+'</div>';
    } else {
      imgHtml='<div class="tryon-loading"><div class="mini-spin"></div><p>Rendering…</p></div>';
    }

    card.innerHTML=
      '<div class="alt-card-img-wrap">'+imgHtml+'</div>'+
      '<p class="alt-name">'+escHtml(r.name)+'</p>'+
      '<button class="alt-card-switch">View full size</button>';

    grid.appendChild(card);
  }
}

function switchTo(idx){
  mainIdx=idx;
  // Re-render hero with animation
  const hero=document.getElementById('hero-section');
  hero.style.animation='none';
  hero.offsetHeight; // force reflow
  hero.style.animation='slideUp var(--d-slow) var(--ease) both';

  updateHero();
}

/* ── Helpers ── */
function showError(msg){
  if(rcCreep)rcCreep.stop();
  document.getElementById('error-msg').textContent=msg;
  showView('error-view');
}

function rcReset(){
  sessionId=null;
  if(pollTimer) clearInterval(pollTimer);
  if(rcCreep){rcCreep.stop();rcCreep=null}
  chosenFile=null;
  colorResults=[];
  mainIdx=0;
  resultsShown=false;
  document.getElementById('rc-file').value='';
  document.getElementById('upload-area').classList.remove('has-file');
  document.getElementById('up-label-text').textContent='Hand over a photo';
  document.getElementById('up-preview').style.display='none';
  document.querySelectorAll('input[name="lens_color"]').forEach(c=>{c.checked=false});
  updateCounter();
  checkReady();
  document.getElementById('load-prog-fill').style.transform='scaleX(0)';
  document.getElementById('load-notes').innerHTML='';
  document.getElementById('load-portrait').style.display='none';
  showView(null);
}

/* ── Narration: the three chosen colours, tracked by name ──
   The wait names the actual work — each colour queued, rendering, ready —
   instead of rotating generic assertions about the product. */
let _rcRows={};
function rcNarrateStart(colors){
  const host=document.getElementById('load-notes');
  host.innerHTML=''; _rcRows={};
  const label=document.createElement('div');
  label.className='lnote';
  label.innerHTML='<span class="lnote-label">On the bench</span>';
  host.appendChild(label);
  requestAnimationFrame(()=>label.classList.add('in'));
  colors.forEach(name=>{
    const row=document.createElement('div');
    row.className='lframe';
    row.innerHTML='<span class="lframe-name">'+escHtml(name)+'</span>'+
      '<span class="lframe-status">Queued</span>';
    host.appendChild(row);
    requestAnimationFrame(()=>row.classList.add('in'));
    _rcRows[name]=row.querySelector('.lframe-status');
  });
}
function rcNarrateUpdate(data){
  for(let i=0;i<(data.num_colors||0);i++){
    const c=data['color'+i]; if(!c) continue;
    const el=_rcRows[c.name]; if(!el) continue;
    if(c.b64){el.textContent='Ready';el.classList.add('done');el.classList.remove('fail')}
    else if(c.error){el.textContent='Failed';el.classList.add('fail')}
    else{el.textContent='Rendering…'}
  }
}
