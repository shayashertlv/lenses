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
   Uses an asymptotic curve so the bar NEVER freezes — it always
   drifts forward, fast at first then gradually decelerating.

   The math:  displayed = base + headroom * (1 - e^(-elapsed / tau))
     - base     = last value from set() (snaps instantly)
     - headroom = 80% of the gap between base and 99%
     - tau      = time constant (seconds) — controls how fast we
                  eat into the headroom. ~8s feels natural: bar
                  covers ~63% of headroom in 8s, ~86% in 16s,
                  ~95% in 24s — always moving, never reaching 99%.

   Usage:
     const creep = new ProgressCreep(el)
     creep.set(20)   // snap to 20%, start drifting toward ~83%
     creep.set(55)   // snap to 55%, restart drift toward ~90%
     creep.finish()  // snap to 100%, stop animation
     creep.stop()    // reset to 0, stop animation
*/
function ProgressCreep(fillEl, tau){
  this.el=fillEl;
  this.base=0;          // last set() value
  this.displayed=0;     // what's currently shown
  this.t0=0;            // timestamp of last set()
  this.tau=(tau||8)*1000; // time constant in ms
  this.raf=null;
  var self=this;
  function tick(now){
    var elapsed=now-self.t0;
    var headroom=(99-self.base)*0.8;
    var next=self.base+headroom*(1-Math.exp(-elapsed/self.tau));
    // Never go backwards
    next=Math.max(next, self.displayed);
    if(next!==self.displayed){
      self.displayed=next;
      self.el.style.width=next.toFixed(1)+'%';
    }
    self.raf=requestAnimationFrame(tick);
  }
  this._tick=tick;
}
ProgressCreep.prototype.set=function(pct){
  this.base=Math.max(pct, this.displayed);
  this.displayed=this.base;
  this.t0=performance.now();
  this.el.style.width=this.base.toFixed(1)+'%';
  if(!this.raf) this.raf=requestAnimationFrame(this._tick);
};
ProgressCreep.prototype.stop=function(){
  if(this.raf){cancelAnimationFrame(this.raf);this.raf=null}
  this.base=0;this.displayed=0;
};
ProgressCreep.prototype.finish=function(){
  if(this.raf){cancelAnimationFrame(this.raf);this.raf=null}
  this.el.style.width='100%';
  this.displayed=100;this.base=100;
};
