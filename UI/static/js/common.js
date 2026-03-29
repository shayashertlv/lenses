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
   Smoothly inches a progress bar forward between poll updates
   so it never looks frozen. Call set() on each poll response,
   and stop() when done.
   Usage:
     const creep = new ProgressCreep(el)   // el = the .fill element
     creep.set(40)   // jump to 40%, then slowly creep toward 40 + ceiling gap
     creep.set(80)   // snap to 80%, creep again
     creep.stop()    // clear interval
*/
function ProgressCreep(fillEl, ceilingGap){
  this.el=fillEl;
  this.current=0;
  this.target=0;
  this.ceiling=0;
  this.gap=ceilingGap||12; // max % to creep beyond target
  this.raf=null;
  var self=this;
  function tick(){
    if(self.current<self.ceiling){
      self.current=Math.min(self.current+0.15, self.ceiling);
      self.el.style.width=self.current.toFixed(1)+'%';
    }
    self.raf=requestAnimationFrame(tick);
  }
  this._tick=tick;
}
ProgressCreep.prototype.set=function(pct){
  this.target=pct;
  this.current=Math.max(this.current, pct);
  this.ceiling=Math.min(pct+this.gap, 99);
  this.el.style.width=this.current.toFixed(1)+'%';
  if(!this.raf) this.raf=requestAnimationFrame(this._tick);
};
ProgressCreep.prototype.stop=function(){
  if(this.raf){cancelAnimationFrame(this.raf);this.raf=null}
  this.current=0;this.target=0;this.ceiling=0;
};
ProgressCreep.prototype.finish=function(){
  if(this.raf){cancelAnimationFrame(this.raf);this.raf=null}
  this.el.style.width='100%';
  this.current=100;this.target=100;this.ceiling=100;
};
