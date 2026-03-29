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
      this.checked=false; return;
    }
    updateCounter();
    checkReady();
  });
});
function updateCounter(){
  const n=getCheckedColors().length;
  const el=document.getElementById('color-counter');
  el.textContent=n+' of 3 selected';
  el.className='color-counter'+(n===3?' full':'')+(n>3?' over':'');
}
function checkReady(){
  document.getElementById('submit-btn').disabled=!(chosenFile && getCheckedColors().length===3);
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
  document.getElementById('load-stage').textContent='Uploading your photo...';
  rcSetStep(1);
  rcCreep=new ProgressCreep(document.getElementById('load-prog-fill'));
  rcCreep.set(10);
  rcRetry=new PollRetry('load-stage', showError);
  startTips();

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

      // Update loading stage (only while loading screen is visible)
      if(!resultsShown){
        const stage=data.stage||'';
        if(stage==='uploading'){
          document.getElementById('load-stage').textContent='Uploading your photo...';
          rcSetStep(1);
          if(rcCreep) rcCreep.set(15);
        } else if(stage==='recoloring'){
          document.getElementById('load-stage').textContent='Recoloring your lenses...';
          rcSetStep(2);
          if(rcCreep) rcCreep.set(40);
        } else if(stage==='primary_ready'){
          document.getElementById('load-stage').textContent='First colour ready! Finishing the rest...';
          rcSetStep(2);
          if(rcCreep) rcCreep.set(70);
        }

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
          stopTips();if(rcCreep)rcCreep.finish();
          renderResults();
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
    heroWrap.innerHTML='<div class="hero-glow"></div><img id="hero-img" src="data:image/png;base64,'+r.b64+'" alt="Recolored lens" style="display:block"/>';
    label.textContent=r.name;
  } else if(r && r.error){
    heroWrap.innerHTML='<div class="hero-glow"></div><div class="tryon-error">'+escHtml(r.error)+'</div>';
    label.textContent=r.name+' (failed)';
  } else if(r){
    heroWrap.innerHTML='<div class="hero-glow"></div><div class="tryon-loading" style="padding:3rem 0"><div class="mini-spin" style="width:48px;height:48px;border-width:4px"></div><p style="margin-top:1rem;color:#bbb">Generating '+escHtml(r.name)+'...</p></div>';
    label.textContent=r.name+' (generating...)';
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
      imgHtml='<div class="tryon-loading"><div class="mini-spin"></div><p>Generating...</p></div>';
    }

    card.innerHTML=
      '<div class="alt-card-img-wrap">'+imgHtml+'</div>'+
      '<div class="alt-card-body"><p class="alt-card-name">'+escHtml(r.name)+'</p></div>'+
      '<button class="alt-card-switch">View Full Size</button>';

    grid.appendChild(card);
  }
}

function switchTo(idx){
  mainIdx=idx;
  // Re-render hero with animation
  const hero=document.getElementById('hero-section');
  hero.style.animation='none';
  hero.offsetHeight; // force reflow
  hero.style.animation='slideUp .5s ease both';

  updateHero();
}

/* ── Helpers ── */
function showError(msg){
  stopTips();if(rcCreep)rcCreep.stop();
  document.getElementById('error-msg').textContent=msg;
  showView('error-view');
}

function rcReset(){
  sessionId=null;
  if(pollTimer) clearInterval(pollTimer);
  stopTips();if(rcCreep){rcCreep.stop();rcCreep=null}
  chosenFile=null;
  colorResults=[];
  mainIdx=0;
  resultsShown=false;
  document.getElementById('rc-file').value='';
  document.getElementById('upload-area').classList.remove('has-file');
  document.getElementById('up-label-text').textContent='Upload a Photo';
  document.getElementById('up-preview').style.display='none';
  document.querySelectorAll('input[name="lens_color"]').forEach(c=>{c.checked=false});
  updateCounter();
  document.getElementById('submit-btn').disabled=true;
  document.getElementById('load-prog-fill').style.width='0%';
  document.getElementById('load-portrait').style.display='none';
  showView(null);
}

/* ── Loading tips rotation ── */
const tips=[
  'Our AI analyses the lens area and applies the new colour with photorealistic precision.',
  'Only the lens colour changes \u2014 frame, face, and background remain untouched.',
  'Each colour is generated independently for the most realistic result.',
  'The AI preserves natural reflections and blends the tint to match the lens curvature.',
];
let tipIdx=0,tipTimer=null;
function startTips(){tipIdx=0;showTip();tipTimer=setInterval(()=>{tipIdx=(tipIdx+1)%tips.length;showTip()},4500)}
function stopTips(){if(tipTimer)clearInterval(tipTimer);tipTimer=null}
function showTip(){
  const el=document.getElementById('load-tip');
  el.style.opacity=0;
  setTimeout(()=>{el.textContent=tips[tipIdx];el.style.opacity=1;},300);
}
