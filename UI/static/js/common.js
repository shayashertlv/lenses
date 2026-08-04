/* ══════════════════════════════════════════════════
   Common JavaScript shared across all Lenses UI pages
   ══════════════════════════════════════════════════ */

/* ── HTML escaping ── */
function escHtml(s){
  const d=document.createElement('div');
  d.textContent=s;
  return d.innerHTML;
}

/* ── Upload-tip popup ──
   Shown once per session: the tip is useful the first time and an
   interruption every time after, so "Got it" remembers. Escape dismisses
   without opening the picker and hands focus back where it came from. */
let _uptipTarget=null, _uptipReturnEl=null;
function openPickerWithTip(id){
  let seen=false;
  try{seen=!!sessionStorage.getItem('uptip_seen')}catch(e){}
  if(seen){document.getElementById(id).click();return}
  _uptipTarget=id;
  _uptipReturnEl=document.activeElement;
  document.getElementById('uptip').classList.add('visible');
  const ok=document.querySelector('#uptip .uptip-ok');
  if(ok) ok.focus();
}
function uptipOk(){
  try{sessionStorage.setItem('uptip_seen','1')}catch(e){}
  document.getElementById('uptip').classList.remove('visible');
  if(_uptipTarget){
    document.getElementById(_uptipTarget).click();
    _uptipTarget=null;
  }
  _uptipReturnEl=null;
}
document.addEventListener('keydown',function(e){
  if(e.key!=='Escape') return;
  const tip=document.getElementById('uptip');
  if(tip && tip.classList.contains('visible')){
    tip.classList.remove('visible');
    _uptipTarget=null;
    if(_uptipReturnEl && _uptipReturnEl.focus) _uptipReturnEl.focus();
    _uptipReturnEl=null;
  }
});

/* ── Capitalize helper ── */
function capitalize(s){return s?s.charAt(0).toUpperCase()+s.slice(1):''}

/* ── Model name ──
   Catalogue `name` repeats the brand ("Emporio Armani EA1041 Semi-Rimless"
   where brand is "Emporio Armani"). A shelf-edge label sets the brand in
   signage caps above the model, so printing the raw name under it says the
   brand twice. Strip the prefix; fall back to the full name if stripping
   would leave nothing. */
function modelName(o){
  const n=String((o&&o.name)||'').trim(), b=String((o&&o.brand)||'').trim();
  if(!b||!n.toLowerCase().startsWith(b.toLowerCase()))return n;
  const rest=n.slice(b.length).replace(/^[\s\-–—·|]+/,'').trim();
  return rest||n;
}

/* ── Frame silhouettes ──
   Drawn geometry, one per silhouette in the tag taxonomy, used as the shelf
   label's photo well before the product image decodes and as the mark on a
   facet tag. Real vector geometry, never a picture and never a glyph font.
   Unknown or absent shapes fall back to the rectangular plate. */
const FRAME_GLYPHS = {
  round:        '<circle cx="34" cy="24" r="18"/><circle cx="86" cy="24" r="18"/><path d="M52 22h16"/>',
  oval:         '<ellipse cx="34" cy="24" rx="20" ry="14"/><ellipse cx="86" cy="24" rx="20" ry="14"/><path d="M54 22h12"/>',
  square:       '<rect x="14" y="8" width="40" height="32" rx="5"/><rect x="66" y="8" width="40" height="32" rx="5"/><path d="M54 22h12"/>',
  rectangular:  '<rect x="12" y="13" width="44" height="22" rx="3"/><rect x="64" y="13" width="44" height="22" rx="3"/><path d="M56 22h8"/>',
  'flat-top':   '<path d="M12 12h44v16a8 8 0 01-8 8H20a8 8 0 01-8-8z"/><path d="M64 12h44v16a8 8 0 01-8 8H72a8 8 0 01-8-8z"/><path d="M56 20h8"/>',
  teardrop:     '<path d="M14 12h40l-4 26H24z"/><path d="M66 12h40l-6 26H72z"/><path d="M54 18h12"/>',
  aviator:      '<path d="M14 12h40l-4 26H24z"/><path d="M66 12h40l-6 26H72z"/><path d="M54 18h12"/>',
  pilot:        '<path d="M14 12h40l-4 26H24z"/><path d="M66 12h40l-6 26H72z"/><path d="M54 18h12"/>',
  'cat-eye':    '<path d="M12 14q10-8 44-2l-4 22q-30 6-40-8z"/><path d="M108 14q-10-8-44-2l4 22q30 6 40-8z"/><path d="M56 18h8"/>',
  butterfly:    '<path d="M12 12q22-6 44 0l-8 26H22z"/><path d="M64 12q22-6 44 0l-6 26H72z"/><path d="M56 18h8"/>',
  browline:     '<path d="M12 14h44M64 14h44"/><path d="M16 14v10a18 18 0 0036 0V14M68 14v10a18 18 0 0036 0V14"/>',
  hexagonal:    '<path d="M34 6l18 10v16L34 42 16 32V16z"/><path d="M86 6l18 10v16L86 42 68 32V16z"/><path d="M52 22h16"/>',
  geometric:    '<path d="M34 6l18 10v16L34 42 16 32V16z"/><path d="M86 6l18 10v16L86 42 68 32V16z"/><path d="M52 22h16"/>',
  irregular:    '<path d="M13 13q20-7 43 1l-5 23q-24 5-38-3z"/><path d="M64 14q23-8 44 0l-4 22q-22 7-36 1z"/><path d="M56 20h8"/>',
  shield:       '<path d="M12 14q48-10 96 0l-6 22q-42 8-84 0z"/>',
  'curved-wrap':'<path d="M10 16q50-12 100 0v14q-50 10-100 0z"/>',
  wrap:         '<path d="M10 16q50-12 100 0v14q-50 10-100 0z"/>',
  wayfarer:     '<path d="M12 12h44l-6 26H20z"/><path d="M64 12h44l-6 26H72z"/><path d="M56 18h8"/>',
};

function frameGlyph(shape, cls){
  const body = FRAME_GLYPHS[String(shape || '').toLowerCase()] || FRAME_GLYPHS.rectangular;
  return '<svg class="frame-glyph' + (cls ? ' ' + cls : '') + '" viewBox="0 0 120 46" ' +
    'fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true" focusable="false">' + body + '</svg>';
}

/* ── The landing, shared by both progress models ───────────────────────────
   A result arrives with the bar short of full, and both models used to paint
   100 on the very next frame — a jump of up to 10 points that reads as the bar
   giving up rather than finishing. Ease the last stretch instead: quick off the
   mark, settling into 100, so the fill visibly completes.

   Only the landing lives here. Neither pacing curve below is touched.

   paint(p)     renders one value
   setFrame(id) records the rAF handle so the owner's stop() can still cancel
   onDone()     optional, fires once the bar has actually reached 100 — callers
                that tear down the progress view need to wait for this, or the
                landing is animated onto a screen nobody is looking at. */
const PROGRESS_FINISH_MS = 420;
function easeToFull(from, paint, setFrame, onDone) {
  const reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  /* Nothing left to travel, or the user asked for no motion: land immediately. */
  if (from >= 99.5 || reduced) {
    setFrame(null);
    paint(100);
    if (onDone) onDone();
    return;
  }
  const t0 = performance.now();
  function land(now) {
    const x = Math.min(1, (now - t0) / PROGRESS_FINISH_MS);
    if (x < 1) {
      paint(from + (100 - from) * (1 - Math.pow(1 - x, 3)));   // ease-out cubic
      setFrame(requestAnimationFrame(land));
    } else {
      setFrame(null);
      paint(100);
      if (onDone) onDone();
    }
  }
  setFrame(requestAnimationFrame(land));
}

/* ── RingTimer — smooth, duration-aware progress ───────────────────────────
   Starts at 0% and eases toward ~90% exactly at the feature's expected
   duration, so it is near the finish right when the result lands. On overrun
   it drifts gently 90→97 (never a hard wall, never reaches 100 until
   finish()). One continuous monotonic timer — no mid-load jumps, ever.

   The curve below is the product of a long tuning arc; do not retune it. The
   only thing a surface chooses is applyFn — the storefront dock paints a
   ticket bar with it, the landing counter paints the docket bar. */
function RingTimer(applyFn, durationSec) {
  this.apply = applyFn;
  this.D = Math.max(1, durationSec) * 1000;
  this.t0 = 0;
  this.displayed = 0;
  this.raf = null;
  const self = this;
  function pctAt(ms) {
    const x = ms / self.D;
    let p;
    if (x <= 1) p = 90 * (1 - Math.pow(1 - x, 1.7));         // ease-out to 90% at D
    else p = 90 + 7 * (1 - Math.exp(-(ms - self.D) / 8000)); // gentle drift toward ~97%
    return Math.min(p, 97);
  }
  function tick(now) {
    const p = pctAt(now - self.t0);
    if (p > self.displayed) { self.displayed = p; self.apply(p); }
    self.raf = requestAnimationFrame(tick);
  }
  this._tick = tick;
}
RingTimer.prototype.start = function () {
  this.t0 = performance.now();
  this.apply(0);
  if (!this.raf) this.raf = requestAnimationFrame(this._tick);
};
RingTimer.prototype.finish = function (onDone) {
  if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  const self = this;
  easeToFull(this.displayed,
    function (p) { self.displayed = p; self.apply(p); },
    function (id) { self.raf = id; },
    onDone);
};
RingTimer.prototype.stop = function () {
  if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
};

/* ── Poll retry tracker ──
   Tracks consecutive poll failures and updates a stage text element.
   Usage:
     const retry = new PollRetry(stageEl, onGiveUp)
     // in .catch():  retry.fail()
     // in .then():   retry.reset()
*/
function PollRetry(stageEl, onGiveUp, maxRetries){
  this.el=typeof stageEl==='string'?document.getElementById(stageEl):stageEl;
  this.onGiveUp=onGiveUp;
  this.max=maxRetries||10;
  this.count=0;
  this._savedText='';
}
PollRetry.prototype.fail=function(){
  this.count++;
  if(this.count===1) this._savedText=this.el.textContent;
  if(this.count>=this.max){
    if(this.onGiveUp) this.onGiveUp('Connection lost. Please check your network and try again.');
    return;
  }
  if(this.count>=3){
    /* Write only on change: the stage element is aria-live. */
    const t='Connection issue \u2014 retrying... ('+this.count+'/'+this.max+')';
    if(this.el.textContent!==t) this.el.textContent=t;
  }
};
PollRetry.prototype.reset=function(){
  if(this.count>=3 && this._savedText && this.el.textContent!==this._savedText)
    this.el.textContent=this._savedText;
  this.count=0;
};

/* ── Progress bar creep ──
   Uses a hyperbolic curve so the bar NEVER freezes — it always
   drifts forward, fast at first then gradually decelerating,
   with a fat tail that sustains visible movement for 30-60s+.

   The math:  displayed = base + headroom * (t / (t + k))
     - base     = last value from set() (snaps instantly)
     - headroom = 85% of the gap between base and 99%
     - k        = half-life (seconds) — at t=k, bar has covered
                  50% of headroom. k=15s is tuned for AI pipelines
                  where the last stage can take 30-50+ seconds.

   Example with base=55%, headroom=37%:
     2s  → 59%   (+4, snappy start)
     10s → 70%   (solid movement)
     20s → 76%   (still clearly moving)
     30s → 79%   (visible progress even late)
     50s → 83%   (still drifting, never stuck)

   Usage:
     const creep = new ProgressCreep(el)
     creep.set(20)   // snap to 20%, start drifting
     creep.set(55)   // snap to 55%, restart drift
     creep.finish(fn) // ease the last stretch to 100%, then call fn
     creep.stop()    // reset to 0, stop animation
*/
function ProgressCreep(fillEl, halfLife, applyFn){
  this.el=fillEl;
  /* applyFn(pct, el) renders the progress value. The default drives a
     transform (scaleX from a left origin — the fill element must be
     full-width with transform-origin:left); the storefront dock passes
     a custom fn to drive a circular SVG ring (stroke-dashoffset) instead. */
  this.apply=applyFn||function(pct,el){el.style.transform='scaleX('+(pct/100).toFixed(4)+')';};
  this.base=0;          // last set() value
  this.displayed=0;     // what's currently shown
  this.t0=0;            // timestamp of last set()
  this.k=(halfLife||15)*1000; // half-life in ms
  this.raf=null;
  var self=this;
  function tick(now){
    var elapsed=now-self.t0;
    var headroom=(99-self.base)*0.85;
    var next=self.base+headroom*(elapsed/(elapsed+self.k));
    // Never go backwards
    next=Math.max(next, self.displayed);
    if(next!==self.displayed){
      self.displayed=next;
      self.apply(next, self.el);
    }
    self.raf=requestAnimationFrame(tick);
  }
  this._tick=tick;
}
ProgressCreep.prototype.set=function(pct){
  this.base=Math.max(pct, this.displayed);
  this.displayed=this.base;
  this.t0=performance.now();
  this.apply(this.base, this.el);
  if(!this.raf) this.raf=requestAnimationFrame(this._tick);
};
ProgressCreep.prototype.stop=function(){
  if(this.raf){cancelAnimationFrame(this.raf);this.raf=null}
  this.base=0;this.displayed=0;
};
ProgressCreep.prototype.finish=function(onDone){
  if(this.raf){cancelAnimationFrame(this.raf);this.raf=null}
  const self=this;
  easeToFull(this.displayed,
    function(p){ self.displayed=p; self.base=p; self.apply(p, self.el); },
    function(id){ self.raf=id; },
    onDone);
};
