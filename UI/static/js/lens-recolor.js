/* ══════════════════════════════════════════════════
   Lens Recolor page JavaScript
   (page-specific — common.js loaded separately)
   ══════════════════════════════════════════════════ */

/* ── State ── */
let chosenFile = null;
let sessionId = null;
let pollTimer = null;
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
      /* The overlays are flex — results as a column whose row takes the leftover
         height, loading and error centred. form-view clears the inline style
         rather than being given one: it is a flex column sized against the
         viewport in CSS, and writing `display: block` here put it back to a
         document every time the visitor pressed Cancel, New photo or Try
         again — the one path no test walks. */
      el.style.display=(id==='form-view')?'':'flex';
    } else {
      el.style.display='none';
    }
  });
  if(v===null){
    document.getElementById('form-view').style.display='';
  }
  /* The three overlays are fixed at z-index 70 over a header at 40, so without
     this the header stays in the tab order underneath them: Shift-Tab off the
     results heading reached an invisible "Lenses" link that navigates away and
     throws the session out. */
  document.querySelector('header.topbar').inert = !!v && v!=='form-view';
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
  rcReader.onload=ev=>{prev.src=ev.target.result;prev.style.display='block'};
  rcReader.readAsDataURL(f);
  cachePortrait(f);
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
  const restored=restoredPortrait();
  if(!restored) return;
  const cached=restored.dataUrl;
  chosenFile=restored.file;
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
  /* The counter rides the signage band now and has one line's width beside a
     25-character title, so the count stays a count and the sentence explaining
     the refusal goes to the hint under the button, which is where every other
     piece of "what do I do next" already lives. Not danger — Alert Red is
     failure only, and a full selection is not a failure. */
  const el=document.getElementById('color-counter');
  el.className='color-counter full nudge';
  const hint=document.getElementById('rc-hint');
  if(hint) hint.textContent='Three is the limit — deselect one to swap';
  clearTimeout(flashCounter._t);
  flashCounter._t=setTimeout(()=>{updateCounter();checkReady()},1600);
}
function updateCounter(){
  const n=getCheckedColors().length;
  const el=document.getElementById('color-counter');
  el.textContent=n+' of 3';
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

/* ── Render results ──
   Three renders, one row, all at one size. */
function renderResults(){
  renderRow();
  showView('results-view');
  /* The view swap removes the control that was focused, which would drop focus
     to <body> and lose a keyboard visitor's place. Send it to the heading. */
  const h=document.getElementById('res-heading');
  if(h) h.focus();
}

function updateProgressiveResults(){ renderRow(); }

function rcState(r){ return r.b64?'done':r.error?'fail':'wait' }

/* Cards are patched one at a time, never the container. The status poll runs
   every 2s: rebuilding the row wholesale restarted every wait bar on each one,
   and it also detached whatever held focus — including the card the open
   full-size dialog is meant to hand focus back to, whose .focus() on a detached
   node does nothing at all and drops the visitor onto <body>. A colour that has
   not changed state is not touched. */
function renderRow(){
  const row=document.getElementById('rc-row');
  while(row.children.length>colorResults.length) row.lastElementChild.remove();
  colorResults.forEach((r,i)=>{
    const cell=row.children[i];
    if(cell && cell.dataset.state===rcState(r)) return;
    if(cell) cell.outerHTML=rcCardHtml(r,i);
    else row.insertAdjacentHTML('beforeend',rcCardHtml(r,i));
  });
  rcAnnounce();
}

/* What is actually on screen. The results view opens at the `primary_ready`
   stage, where one of the three colours has come back and two are still
   rendering, so announcing the requested count called three renders ready when
   there was one — and nothing was said when the other two landed. */
function rcAnnounce(){
  const live=document.getElementById('res-live');
  if(!live) return;
  const done=colorResults.filter(r=>r.b64);
  const failed=colorResults.filter(r=>r.error);
  const text=done.length+' of '+colorResults.length+' colours ready'+
    (done.length?': '+done.map(r=>r.name).join(', '):'')+'.'+
    (failed.length?' '+failed.map(r=>r.name).join(', ')+' could not be rendered.':'');
  if(live.textContent!==text) live.textContent=text;
}

/* escHtml goes through textContent, which escapes < > and & but leaves a double
   quote alone — safe for text, not for a value being written between quotes. */
function escAttr(s){ return escHtml(s).replace(/"/g,'&quot;') }

/* The swatch the visitor picked, read back off the picker rather than restated
   here, so the sixteen gradients live in exactly one place. */
function rcSwatchHtml(name){
  const input=document.querySelector('input[name="lens_color"][value="'+CSS.escape(name)+'"]');
  const sw=input && input.closest('.color-pick').querySelector('.color-swatch');
  if(!sw) return '';
  return '<span class="rc-swatch"'+
    (sw.hasAttribute('data-clear')?' data-clear="1"':'')+
    (sw.getAttribute('style')?' style="'+escAttr(sw.getAttribute('style'))+'"':'')+
    '></span>';
}

function rcCardHtml(r,i){
  let well;
  if(r.b64){
    well='<div class="tryon-done"><img src="data:image/png;base64,'+r.b64+
      '" alt="You wearing '+escAttr(r.name)+' lenses"/></div>';
  } else if(r.error){
    well='<div class="tryon-fail">Could not be rendered: '+escHtml(r.error)+'</div>';
  } else {
    well='<div class="tryon-wait"><span class="tryon-wait-bar"></span>Rendering</div>';
  }
  /* The whole card is the control, picture included. One target, and it is the
     one people actually aim at — the label strip underneath was the only live
     part, so clicking the face did nothing. (Before that the card was a <div>
     with a <button> inside it that promoted the hero instead of opening
     anything, which was neither reachable by keyboard nor true.)
     A colour still rendering or failed has nothing to open, so it stays an
     <article> rather than a button that does nothing. */
  const bar='<span class="rc-card-action"><span class="rc-card-head">'+
    rcSwatchHtml(r.name)+'<span class="rc-card-name">'+escHtml(r.name)+'</span></span>'+
    '<span class="rc-card-cta">'+(r.b64?'View full size':r.error?'Not rendered':'Rendering')+
    '</span></span>';
  const inside='<div class="result-img">'+well+'</div>'+bar;
  return r.b64
    ? '<button class="result-card" type="button" data-i="'+i+'" data-state="done">'+inside+'</button>'
    : '<article class="result-card" data-state="'+rcState(r)+'">'+inside+'</article>';
}

/* ── Full size ── */
let _zoomReturn=null;

document.getElementById('rc-row').addEventListener('click',function(e){
  const card=e.target.closest('.result-card[data-i]');
  if(card) rcOpenZoom(+card.dataset.i);
});

function rcOpenZoom(i){
  const r=colorResults[i];
  if(!r||!r.b64) return;
  const img=document.getElementById('rc-zoom-img');
  img.src='data:image/png;base64,'+r.b64;
  img.alt='You wearing '+r.name+' lenses';
  document.getElementById('rc-zoom-name').textContent=r.name;
  _zoomReturn=document.activeElement;
  const ov=document.getElementById('rc-zoom');
  ov.classList.add('visible');
  /* aria-modal is an announcement, not an enforcement: without this Tab walks
     straight out of the dialog into the row behind the backdrop. */
  document.querySelector('main').inert=true;
  void ov.offsetWidth;   // the panel is rendered before focus is asked to enter it
  document.getElementById('rc-zoom-close').focus();
}

function rcCloseZoom(e){
  // Called both from the backdrop and from Close; the backdrop must not fire
  // when the click landed on the panel it is behind.
  if(e && e.target!==e.currentTarget) return;
  document.getElementById('rc-zoom').classList.remove('visible');
  // Cleared before the focus call: focus cannot enter an inert subtree.
  document.querySelector('main').inert=false;
  if(_zoomReturn){_zoomReturn.focus();_zoomReturn=null}
}

document.addEventListener('keydown',function(e){
  if(e.key!=='Escape') return;
  if(document.getElementById('rc-zoom').classList.contains('visible')) rcCloseZoom();
});

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
  resultsShown=false;
  _zoomReturn=null;   // the control it would return to is about to be removed
  rcCloseZoom();
  document.getElementById('rc-row').innerHTML='';
  document.getElementById('res-live').textContent='';
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
