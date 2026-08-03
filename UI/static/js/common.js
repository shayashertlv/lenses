/* ══════════════════════════════════════════════════
   Common JavaScript shared across all Lenses UI pages
   ══════════════════════════════════════════════════ */

/* ── HTML escaping ── */
function escHtml(s){
  const d=document.createElement('div');
  d.textContent=s;
  return d.innerHTML;
}

/* ── Upload-tip popup ── */
let _uptipTarget=null;
function openPickerWithTip(id){
  _uptipTarget=id;
  document.getElementById('uptip').classList.add('visible');
}
function uptipOk(){
  document.getElementById('uptip').classList.remove('visible');
  if(_uptipTarget){
    document.getElementById(_uptipTarget).click();
    _uptipTarget=null;
  }
}

/* ── Price formatting ── */
function fmtPrice(o){
  const sym=o.currency==='ILS'?'\u20AA':o.currency;
  return o.price.toLocaleString()+' '+sym;
}

/* ── Capitalize helper ── */
function capitalize(s){return s?s.charAt(0).toUpperCase()+s.slice(1):''}

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
    this.el.textContent='Connection issue \u2014 retrying... ('+this.count+'/'+this.max+')';
  }
};
PollRetry.prototype.reset=function(){
  if(this.count>=3 && this._savedText) this.el.textContent=this._savedText;
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
     creep.finish()  // snap to 100%, stop animation
     creep.stop()    // reset to 0, stop animation
*/
function ProgressCreep(fillEl, halfLife, applyFn){
  this.el=fillEl;
  /* applyFn(pct, el) renders the progress value. Defaults to a width bar
     so every existing call site is unchanged; the storefront dock passes
     a custom fn to drive a circular SVG ring (stroke-dashoffset) instead. */
  this.apply=applyFn||function(pct,el){el.style.width=pct.toFixed(1)+'%';};
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
ProgressCreep.prototype.finish=function(){
  if(this.raf){cancelAnimationFrame(this.raf);this.raf=null}
  this.apply(100, this.el);
  this.displayed=100;this.base=100;
};
