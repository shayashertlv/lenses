"""
HTML templates for the landing page, free search page, lens recolor page,
and storefront demo page.
"""

LANDING_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Lenses</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0b0b14;
  color:#fff;height:100vh;height:100dvh;overflow:hidden;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:1.5rem 2rem;-webkit-font-smoothing:antialiased;
}

.logo{font-size:3.2rem;font-weight:400;letter-spacing:-.02em;color:#fff;
  font-family:'DM Serif Display',Georgia,serif;
  margin-bottom:.25rem;text-align:center}
.tagline{font-size:1.08rem;color:rgba(255,255,255,.4);margin-bottom:2.5rem;
  font-weight:400;letter-spacing:.01em;text-align:center}

.cards{display:flex;gap:1.5rem;flex-wrap:wrap;justify-content:center;max-width:900px}

.mode-card{
  flex:1 1 320px;max-width:380px;
  background:rgba(255,255,255,.03);border-radius:24px;padding:2rem 2rem;
  box-shadow:none;
  text-align:center;cursor:pointer;
  transition:all .3s ease;border:1px solid rgba(255,255,255,.06);
  text-decoration:none;color:inherit;display:block;
}
.mode-card:hover{transform:translateY(-6px);
  box-shadow:0 12px 40px rgba(0,0,0,.3);border-color:rgba(255,255,255,.15);
  background:rgba(255,255,255,.06)}

.mode-icon{width:80px;height:80px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  margin:0 auto 1.5rem;font-size:2rem}
.mode-icon.smart{background:rgba(108,99,255,.12)}
.mode-icon.free{background:rgba(52,199,138,.12)}
.mode-icon.recolor{background:rgba(232,168,56,.12)}

.mode-icon svg{width:36px;height:36px;stroke-width:1.5;fill:none}
.mode-icon.smart svg{stroke:#6c63ff}
.mode-icon.free svg{stroke:#34c78a}
.mode-icon.recolor svg{stroke:#e8a838}
.mode-icon.store{background:rgba(236,72,153,.12)}
.mode-icon.store svg{stroke:#ec4899}

.mode-card h2{font-size:1.3rem;font-weight:700;margin-bottom:.5rem;color:#fff}
.mode-card p{font-size:.88rem;color:rgba(255,255,255,.45);line-height:1.55}

.mode-badge{display:inline-block;margin-top:1.2rem;padding:.4rem 1.2rem;
  border-radius:50px;font-size:.78rem;font-weight:600;letter-spacing:.03em}
.mode-badge.smart{background:#6c63ff;color:#fff}
.mode-badge.free{background:#34c78a;color:#fff}
.mode-badge.recolor{background:#e8a838;color:#fff}
.mode-badge.store{background:#ec4899;color:#fff}

/* ── Tablet (up to 1024px): tighten desktop layout ── */
@media(max-width:1024px){
  body{padding:1.2rem 1.5rem}
  .cards{gap:1.2rem;max-width:760px}
  .mode-card{flex:1 1 260px;max-width:340px;padding:1.6rem 1.4rem;border-radius:20px}
  .mode-icon{width:64px;height:64px;margin-bottom:1rem}
  .mode-icon svg{width:28px;height:28px}
  .mode-card h2{font-size:1.1rem}
  .mode-card p{font-size:.82rem}
  .mode-badge{margin-top:.8rem}
  .logo{font-size:2.6rem}
  .tagline{margin-bottom:2rem}
}

/* ── Mobile portrait: horizontal list-item cards ── */
@media(max-width:600px){
  body{
    padding:1.8rem 1rem;
    height:100dvh;height:100vh;
    justify-content:center;
  }
  .logo{font-size:2.2rem;margin-bottom:.1rem}
  .tagline{font-size:.9rem;margin-bottom:1.8rem}
  .cards{
    flex-direction:column;
    gap:.7rem;
    max-width:100%;
    width:100%;
  }
  /* Redesign card as horizontal list item */
  .mode-card{
    flex:none;
    max-width:100%;
    width:100%;
    display:grid;
    grid-template-columns:56px 1fr;
    grid-template-rows:auto auto auto;
    column-gap:1rem;
    row-gap:.1rem;
    text-align:left;
    padding:1rem 1.2rem;
    border-radius:18px;
    align-items:center;
  }
  .mode-icon{
    grid-row:1/4;
    width:52px;height:52px;
    margin:0;
    align-self:center;
    flex-shrink:0;
  }
  .mode-icon svg{width:24px;height:24px}
  .mode-card h2{
    grid-column:2;
    font-size:1rem;
    margin-bottom:0;
    align-self:end;
  }
  .mode-card p{
    grid-column:2;
    font-size:.78rem;
    line-height:1.4;
    display:-webkit-box;
    -webkit-line-clamp:2;
    -webkit-box-orient:vertical;
    overflow:hidden;
  }
  .mode-badge{
    grid-column:2;
    margin-top:.3rem;
    padding:.28rem .85rem;
    font-size:.72rem;
    align-self:start;
  }
}

/* ── Small phones (<380px) ── */
@media(max-width:380px){
  body{padding:1.4rem .8rem}
  .logo{font-size:1.9rem}
  .tagline{font-size:.82rem;margin-bottom:1.4rem}
  .cards{gap:.55rem}
  .mode-card{padding:.9rem 1rem;grid-template-columns:48px 1fr}
  .mode-icon{width:44px;height:44px}
  .mode-icon svg{width:20px;height:20px}
  .mode-card h2{font-size:.92rem}
  .mode-card p{font-size:.73rem}
  .mode-badge{font-size:.68rem;padding:.24rem .75rem}
}

/* ── Landscape mobile (short viewport) ── */
@media(max-height:500px) and (max-width:900px){
  body{padding:.6rem 1rem}
  .logo{font-size:1.5rem;margin-bottom:0}
  .tagline{font-size:.75rem;margin-bottom:.8rem}
  .cards{flex-direction:row;flex-wrap:wrap;gap:.55rem}
  .mode-card{
    flex:1 1 calc(50% - .3rem);max-width:calc(50% - .3rem);
    display:grid;grid-template-columns:38px 1fr;column-gap:.7rem;
    padding:.7rem .9rem;border-radius:14px;
  }
  .mode-icon{width:34px;height:34px}
  .mode-icon svg{width:16px;height:16px}
  .mode-card h2{font-size:.82rem}
  .mode-card p{-webkit-line-clamp:1;font-size:.68rem}
  .mode-badge{font-size:.62rem;padding:.18rem .6rem}
}
</style>
</head>
<body>

<div class="logo">Lenses</div>
<p class="tagline">AI-powered glasses fitting</p>

<div class="cards">

  <!-- Smart Fit card (opens file picker inline) -->
  <div class="mode-card" id="smart-card" onclick="openPickerWithTip('sf-file')">
    <div class="mode-icon smart">
      <svg viewBox="0 0 36 36"><circle cx="18" cy="12" r="5"/><path d="M6 30c0-6.627 5.373-12 12-12s12 5.373 12 12"/><path d="M28 8l2 2-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </div>
    <h2>Smart Fit</h2>
    <p>Upload a selfie and our AI will analyse your face shape, features, and proportions to recommend the perfect frames.</p>
    <span class="mode-badge smart">Upload a Photo</span>
    <input type="file" id="sf-file" accept="image/*" style="display:none" onclick="event.stopPropagation()"/>
  </div>

  <!-- Free Search card -->
  <a class="mode-card" href="/free-search">
    <div class="mode-icon free">
      <svg viewBox="0 0 36 36"><circle cx="15" cy="15" r="9"/><path d="M22 22l8 8" stroke-linecap="round"/><path d="M12 15h6M15 12v6" stroke-linecap="round"/></svg>
    </div>
    <h2>Free Search</h2>
    <p>Already know what you want? Pick your ideal frame shape, colour, material, and style — we'll find the best match and show you wearing it.</p>
    <span class="mode-badge free">Choose Your Style</span>
  </a>

  <!-- Switch Lens Color card -->
  <a class="mode-card" href="/lens-recolor">
    <div class="mode-icon recolor">
      <svg viewBox="0 0 36 36"><circle cx="12" cy="18" r="7" /><circle cx="24" cy="18" r="7" /><path d="M19 18h-2" stroke-linecap="round"/><path d="M5 18H2M34 18h-3" stroke-linecap="round"/></svg>
    </div>
    <h2>Switch Lens Color</h2>
    <p>Upload a photo of yourself wearing glasses and pick three lens colours — our AI will create realistic recoloured versions of your lenses.</p>
    <span class="mode-badge recolor">Recolor Lenses</span>
  </a>

  <!-- Storefront Demo card -->
  <a class="mode-card" href="/storefront">
    <div class="mode-icon store">
      <svg viewBox="0 0 36 36"><path d="M4 14V30a2 2 0 002 2h24a2 2 0 002-2V14" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 8l4 6h24l4-6H2z" stroke-linejoin="round"/><path d="M14 22h8v10h-8z" stroke-linejoin="round"/></svg>
    </div>
    <h2>Use on Your Site</h2>
    <p>See how virtual try-on looks inside a real e-commerce store. Browse the full catalogue and try any frame on yourself.</p>
    <span class="mode-badge store">View Demo Store</span>
  </a>

</div>

<!-- Hidden processing / results / error views for Smart Fit -->
<div id="sf-processing" style="display:none;position:fixed;inset:0;background:#0b0b14;z-index:100;
  display:none;align-items:center;justify-content:center;flex-direction:column">
  <div style="background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:2.5rem 2.8rem;
    box-shadow:0 8px 40px rgba(0,0,0,.3);max-width:480px;width:90%;text-align:center">
    <div id="sf-portrait-wrap" style="width:120px;height:120px;border-radius:50%;overflow:hidden;
      margin:0 auto 1.8rem;border:3px solid rgba(255,255,255,.1);box-shadow:0 4px 20px rgba(0,0,0,.3);display:none">
      <img id="sf-portrait-img" style="width:100%;height:100%;object-fit:cover" src="" alt=""/>
    </div>
    <div style="display:flex;justify-content:center;gap:.5rem;margin-bottom:2rem" id="sf-steps">
      <div class="sf-step" id="sf-s1"><span class="sf-dot">1</span><span>Analyze</span></div>
      <div class="sf-line" id="sf-sl1"></div>
      <div class="sf-step" id="sf-s2"><span class="sf-dot">2</span><span>Match</span></div>
      <div class="sf-line" id="sf-sl2"></div>
      <div class="sf-step" id="sf-s3"><span class="sf-dot">3</span><span>Try-On</span></div>
    </div>
    <div style="width:100%;height:3px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:1.6rem;overflow:hidden">
      <div id="sf-prog" style="height:100%;background:linear-gradient(90deg,#6c63ff,#a78bfa);border-radius:2px;width:0%;transition:width .6s ease"></div>
    </div>
    <p id="sf-stage" style="font-size:.95rem;color:rgba(255,255,255,.7);font-weight:500;margin-bottom:1.4rem;min-height:1.4em">Uploading...</p>
    <div style="min-height:3.5em;display:flex;align-items:center;justify-content:center">
      <p id="sf-tip" style="font-size:.82rem;color:rgba(255,255,255,.35);line-height:1.5;max-width:340px;font-style:italic;transition:opacity .4s"></p>
    </div>
  </div>
</div>

<div id="sf-results" style="display:none;position:fixed;inset:0;background:#0b0b14;
  z-index:100;overflow-y:auto;padding:2rem 1.5rem;color:#fff">
  <div style="max-width:960px;margin:0 auto;padding-top:.5rem">
    <button onclick="sfReset()" style="display:inline-flex;align-items:center;gap:.4rem;margin-bottom:1.4rem;
      padding:.45rem 1rem;background:rgba(255,255,255,.07);color:rgba(255,255,255,.6);
      border:1px solid rgba(255,255,255,.1);border-radius:50px;font-size:.82rem;font-weight:500;
      cursor:pointer;transition:all .2s" onmouseover="this.style.background='rgba(255,255,255,.12)'" onmouseout="this.style.background='rgba(255,255,255,.07)'">
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 11L5 7l4-4"/></svg>
      Start Over
    </button>
    <div id="sf-opts"></div>
  </div>
</div>

<div id="sf-error" style="display:none;position:fixed;inset:0;background:#0b0b14;
  z-index:100;display:none;align-items:center;justify-content:center;flex-direction:column">
  <div style="background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:2.5rem 2.8rem;
    box-shadow:0 8px 40px rgba(0,0,0,.3);max-width:420px;width:90%;text-align:center">
    <div style="font-size:2.4rem;margin-bottom:1rem">:/</div>
    <h2 style="font-size:1.15rem;color:#fff;margin-bottom:.6rem">Something went wrong</h2>
    <p id="sf-error-msg" style="font-size:.88rem;color:rgba(255,255,255,.45);line-height:1.5;margin-bottom:1.5rem;word-break:break-word"></p>
    <button onclick="sfReset()" style="display:block;margin:0 auto;padding:.75rem 2.2rem;
      background:rgba(255,255,255,.08);color:#fff;border:1px solid rgba(255,255,255,.1);border-radius:50px;font-size:.92rem;font-weight:600;
      cursor:pointer">Try Again</button>
  </div>
</div>

<style>
.sf-step{display:flex;align-items:center;gap:.35rem;font-size:.78rem;color:rgba(255,255,255,.3);font-weight:500;transition:color .3s}
.sf-step.active{color:#6c63ff}.sf-step.done{color:#34c78a}
.sf-dot{width:28px;height:28px;border-radius:50%;border:2px solid rgba(255,255,255,.15);display:flex;
  align-items:center;justify-content:center;font-size:.72rem;font-weight:700;color:rgba(255,255,255,.3);transition:all .3s}
.sf-step.active .sf-dot{border-color:#6c63ff;color:#6c63ff;box-shadow:0 0 0 4px rgba(108,99,255,.12)}
.sf-step.done .sf-dot{border-color:#34c78a;background:#34c78a;color:#fff}
.sf-line{width:32px;height:2px;background:rgba(255,255,255,.1);border-radius:1px;align-self:center;transition:background .3s}
.sf-line.done{background:#34c78a}

/* result card styles (shared with free search via class) */
.opt-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:18px;margin-bottom:1.8rem;overflow:hidden;
  box-shadow:none;animation:cardIn .5s ease both}
@keyframes cardIn{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
.opt-card:nth-child(2){animation-delay:.1s}
.opt-card:nth-child(3){animation-delay:.2s}
.opt-card.primary{box-shadow:0 0 20px rgba(108,99,255,.15);border:1px solid #6c63ff}
.opt-label{padding:.65rem 1.4rem;font-weight:700;font-size:.82rem;text-transform:uppercase;
  letter-spacing:.06em;background:rgba(255,255,255,.04);color:rgba(255,255,255,.4)}
.opt-card.primary .opt-label{background:linear-gradient(135deg,#6c63ff,#8b7bff);color:#fff}
.opt-body{display:flex;gap:1.5rem;padding:1.4rem;align-items:stretch}
.tryon-col{flex:1 1 0;min-width:0;max-width:55%;display:flex;align-items:center;justify-content:center;position:relative}
.tryon-col img{width:100%;height:auto;border-radius:12px;display:block}
.prod-col{flex:1 1 200px;display:flex;flex-direction:column;gap:.6rem}
.prod-col img{display:block;width:100%;max-height:200px;object-fit:contain;border-radius:12px;background:#fff}
.prod-info h3{font-size:.88rem;font-weight:600;line-height:1.3;margin-bottom:.15rem;color:#fff}
.prod-info .brand{font-size:.76rem;color:rgba(255,255,255,.4)}
.prod-info .tags{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.45rem}
.prod-info .tag{background:rgba(108,99,255,.15);color:#a78bfa;font-size:.68rem;padding:.15rem .5rem;border-radius:8px;font-weight:500}
.prod-info .price{font-size:.95rem;font-weight:700;color:#fff;margin-top:.55rem}
.prod-info .score{font-size:.72rem;color:rgba(255,255,255,.35);margin-top:.1rem}
.tryon-loading{display:flex;flex-direction:column;align-items:center;justify-content:center;
  min-height:300px;width:100%;border-radius:12px;
  background:linear-gradient(110deg,rgba(255,255,255,.04) 8%,rgba(255,255,255,.08) 18%,rgba(255,255,255,.04) 33%);
  background-size:200% 100%;animation:shimmer 1.6s linear infinite}
@keyframes shimmer{to{background-position:-200% 0}}
.tryon-loading p{font-size:.82rem;color:rgba(255,255,255,.4);margin-top:.5rem}
.tryon-loading .mini-spin{width:28px;height:28px;border:3px solid rgba(255,255,255,.1);
  border-top-color:#6c63ff;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.tryon-error{display:flex;align-items:center;justify-content:center;min-height:220px;
  background:rgba(220,50,50,.08);border-radius:12px;color:#f87171;font-size:.85rem;padding:1.2rem;text-align:center;width:100%}
/* ── Section labels ──────────────────────────────── */
.section-lbl{font-size:14px;font-weight:500;color:rgba(255,255,255,.35);margin-bottom:10px;margin-top:1.5rem}
.section-lbl:first-child{margin-top:0}

/* ── Primary hero card ──────────────────────────── */
.primary-hero{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:18px;overflow:hidden;
  box-shadow:none;margin-bottom:1.8rem;animation:cardIn .5s ease both}
.primary-hero-inner{display:flex;align-items:stretch}
.primary-tryon{flex:1 1 0;min-width:0;max-width:420px;display:flex;align-items:center;justify-content:center;padding:12px;position:relative}
.primary-tryon img{width:100%;height:auto;object-fit:contain;border-radius:14px;display:block}
.primary-panel{flex:1 1 0;min-width:220px;padding:20px;display:flex;flex-direction:column}
.match-badge{display:inline-flex;align-items:center;gap:5px;background:rgba(22,163,74,.15);color:#4ade80;
  font-size:11px;font-weight:600;padding:4px 10px;border-radius:20px;margin-bottom:14px;align-self:flex-start}
.primary-panel .p-name{font-size:16px;font-weight:600;line-height:1.3;margin-bottom:2px;color:#fff}
.primary-panel .p-brand{font-size:12px;color:rgba(255,255,255,.4);margin-bottom:10px}
.primary-panel .p-tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px}
.primary-panel .p-tag{font-size:11px;padding:3px 8px;border-radius:20px;background:rgba(108,99,255,.15);color:#a78bfa}
.primary-panel .p-price{font-size:20px;font-weight:700;color:#fff}
.primary-panel .p-micro{font-size:11px;color:rgba(255,255,255,.35);margin-top:2px;margin-bottom:12px}
.primary-panel .p-divider{height:1px;background:rgba(255,255,255,.08);margin-bottom:12px}
.why-label{font-size:12px;font-weight:600;color:rgba(255,255,255,.4);margin-bottom:8px}
.why-list{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
.why-item{display:flex;gap:8px;align-items:flex-start;font-size:12px;color:rgba(255,255,255,.5);line-height:1.5}
.why-item svg{flex-shrink:0;margin-top:2px}

/* ── Recolor overlay button on try-on images ── */
.recolor-overlay-btn{position:absolute;bottom:10px;left:10px;padding:5px 12px;
  background:rgba(232,168,56,.92);color:#fff;border:none;border-radius:20px;
  font-size:.72rem;font-weight:600;cursor:pointer;backdrop-filter:blur(4px);transition:background .2s}

/* ── Analysis cards ─────────────────────────────── */
.analysis-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:2rem}
.analysis-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:14px;padding:16px;
  box-shadow:none;animation:cardIn .5s ease both}
.analysis-card:nth-child(2){animation-delay:.1s}
.analysis-card:nth-child(3){animation-delay:.2s}
.analysis-vis{width:100%;height:80px;display:flex;align-items:center;justify-content:center;
  background:rgba(255,255,255,.04);border-radius:10px;margin-bottom:12px}
.analysis-label{font-size:11px;letter-spacing:0.04em;text-transform:uppercase;color:rgba(255,255,255,.35);margin-bottom:3px}
.analysis-value{font-size:15px;font-weight:600;color:#fff;margin-bottom:4px}
.analysis-desc{font-size:12px;color:rgba(255,255,255,.45);line-height:1.45}

/* ── Color profile vis ── */
.color-vis-wrap{display:flex;align-items:center;gap:10px}
.color-circle{display:flex;width:48px;height:48px;border-radius:50%;overflow:hidden;border:2px solid #f4f4f8;flex-shrink:0}
.color-legend{display:flex;flex-direction:column;gap:2px}
.color-legend-item{display:flex;align-items:center;gap:4px;font-size:10px;color:#8b85b8}
.color-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}

/* ── Smart Fit results: tablet ── */
@media(max-width:900px){
  .primary-hero-inner{flex-direction:column}
  .primary-tryon{max-width:100%;padding:10px}
  .primary-panel{flex:none;width:100%;padding:14px 16px}
  .analysis-cards{grid-template-columns:repeat(3,1fr);gap:8px}
  .opt-body{flex-direction:column}
  .tryon-col{max-width:100%;margin-bottom:.3rem}
  .prod-col{flex:none;max-width:100%;flex-direction:row;gap:1rem;align-items:center}
  .prod-col img{width:80px;height:80px;flex-shrink:0;max-height:80px}
}
/* ── Smart Fit results: mobile ── */
@media(max-width:600px){
  /* Primary hero — image full width, text compact below */
  .primary-tryon{padding:6px}
  .primary-panel{padding:12px 14px}
  .primary-panel .p-name{font-size:14px}
  .primary-panel .p-brand{font-size:11px;margin-bottom:6px}
  .primary-panel .p-tags{gap:4px;margin-bottom:8px}
  .primary-panel .p-tag{font-size:10px;padding:2px 7px}
  .primary-panel .p-price{font-size:17px}
  .primary-panel .p-micro{font-size:10px;margin-bottom:8px}
  .match-badge{font-size:10px;padding:3px 8px;margin-bottom:8px}
  /* "Why this works" — smaller but NO truncation */
  .why-label{font-size:11px;margin-bottom:5px}
  .why-item{font-size:11px;line-height:1.4}
  .why-list{gap:5px;margin-bottom:10px}
  /* Analysis cards — 3-across, compact, smaller color circle */
  .analysis-cards{grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:1.2rem}
  .analysis-card{padding:10px 8px;border-radius:10px}
  .analysis-vis{height:50px;margin-bottom:6px;border-radius:6px}
  .analysis-vis svg{width:44px;height:44px}
  .analysis-label{font-size:9px;margin-bottom:2px}
  .analysis-value{font-size:11px;margin-bottom:2px}
  .analysis-desc{font-size:9.5px;line-height:1.35}
  /* Color circle smaller on mobile */
  .color-circle{width:36px;height:36px}
  .color-vis-wrap{gap:6px}
  .color-legend-item{font-size:9px;gap:3px}
  .color-dot{width:6px;height:6px}
  /* Alternative cards */
  .opt-card{margin-bottom:1.2rem}
  .opt-label{font-size:.72rem;padding:8px 14px}
  .opt-body{padding:.75rem;gap:.6rem}
  .tryon-loading{min-height:180px}
  .tryon-error{min-height:120px;font-size:.75rem}
  .prod-col{gap:.6rem}
  .prod-col img{width:70px;height:70px;max-height:70px}
  .prod-info h3{font-size:.8rem}
  .prod-info .brand{font-size:.65rem}
  .prod-info .tags{gap:3px}
  .prod-info .tag{font-size:.6rem;padding:2px 6px}
  .prod-info .price{font-size:.88rem}
  .prod-info .score{font-size:.65rem}
  /* Recolor button — smaller on mobile */
  .recolor-overlay-btn{padding:3px 9px;font-size:.62rem;bottom:6px;left:6px;border-radius:14px}
  .uptip-box{max-width:92%;padding:1.5rem 1.2rem 1.2rem}
}
@media(max-width:380px){
  .prod-col img{width:60px;height:60px;max-height:60px}
  .opt-body{padding:.6rem}
  .primary-panel .p-name{font-size:13px}
  .primary-panel .p-price{font-size:15px}
}
/* upload-tip popup */
.uptip-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(6px);
  z-index:9999;display:flex;align-items:center;justify-content:center;
  opacity:0;pointer-events:none;transition:opacity .18s}
.uptip-overlay.visible{opacity:1;pointer-events:auto}
.uptip-box{background:rgba(20,20,38,.96);border:1px solid rgba(255,255,255,.1);
  border-radius:20px;padding:2rem 1.8rem 1.6rem;max-width:330px;width:90%;
  box-shadow:0 24px 60px rgba(0,0,0,.6);text-align:center}
.uptip-icon{margin-bottom:.9rem}
.uptip-icon svg{width:44px;height:44px;stroke:#6c63ff;fill:none;stroke-width:1.6}
.uptip-box h3{font-size:1.05rem;font-weight:700;color:#fff;margin:0 0 .55rem}
.uptip-box p{font-size:.86rem;color:rgba(255,255,255,.55);line-height:1.6;margin:0 0 1.4rem}
.uptip-ok{background:#6c63ff;color:#fff;border:none;border-radius:12px;
  padding:.72rem 0;font-size:.95rem;font-weight:600;cursor:pointer;width:100%;
  transition:opacity .15s}
.uptip-ok:hover{opacity:.85}
</style>

<script>
/* ── Smart Fit inline flow ─────────────────────────────────────── */
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

/* ── Helpers for new card layout ───────────────────────────────── */
const checkSvg14='<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="7" r="7" fill="#dcfce7"/><path d="M4 7l2 2 4-4" stroke="#16a34a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const checkSvg12='<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 6l2.5 2.5L9 4" stroke="#16a34a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function escHtml(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}

const _recolorStore={};let _recolorIdx=0;
function goRecolor(key){
  sessionStorage.setItem('recolor_preload',_recolorStore[key]);
  window.location.href='/lens-recolor';
}

function buildTryonHtml(o){
  if(o.tryon_status==='done'&&o.tryon_b64){
    const key='rc'+(++_recolorIdx);
    _recolorStore[key]=o.tryon_b64;
    return `<div style="position:relative;display:inline-block;width:100%">
      <img src="data:image/png;base64,${o.tryon_b64}" alt="Virtual try-on"/>
      <button class="recolor-overlay-btn" onclick="event.stopPropagation();goRecolor('${key}')" onmouseover="this.style.background='rgba(232,168,56,1)'" onmouseout="this.style.background='rgba(232,168,56,.92)'">Recolor Lenses</button>
    </div>`;
  }else if(o.tryon_status==='error'){
    return `<div class="tryon-error">Try-on could not be generated${o.tryon_error?': '+escHtml(o.tryon_error):''}</div>`;
  }
  return `<div class="tryon-loading"><div class="mini-spin"></div><p>Generating try-on...</p></div>`;
}

function fmtPrice(o){
  return o.currency==='ILS'?o.price.toLocaleString()+' \u20AA':o.price.toLocaleString()+' '+escHtml(o.currency);
}

function buildPrimaryCard(o,d){
  const card=document.createElement('div');
  card.className='primary-hero';
  const tryonHtml=buildTryonHtml(o);
  const matchPct=(o.score*100).toFixed(1);

  // Why bullets from face_insights
  const insights=d.face_insights||[];
  let whyHtml='';
  if(insights.length>=3){
    whyHtml=`<div class="p-divider"></div>
      <div class="why-label">Why this works for you</div>
      <div class="why-list">
        ${insights.slice(0,3).map(t=>`<div class="why-item">${checkSvg14}<span>${escHtml(t.split('. ')[0]+'.')}</span></div>`).join('')}
      </div>`;
  }

  card.innerHTML=`<div class="primary-hero-inner">
    <div class="primary-tryon">${tryonHtml}</div>
    <div class="primary-panel">
      <div class="match-badge">${checkSvg12} ${matchPct}% match</div>
      <div class="p-name">${escHtml(o.name)}</div>
      <div class="p-brand">${escHtml(o.brand)} \u2014 ${escHtml(o.color)}</div>
      <div class="p-tags">
        <span class="p-tag">${escHtml(o.shape)}</span>
        <span class="p-tag">${escHtml(o.material)}</span>
        <span class="p-tag">${escHtml(o.color)}</span>
      </div>
      <div class="p-price">${fmtPrice(o)}</div>
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

  // Card 1 — Face Shape
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

  // Card 2 — Key Geometry
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

  // Card 3 — Color Profile
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
          <p class="brand">${escHtml(o.brand)} \u2014 ${escHtml(o.model)}</p>
          <div class="tags">
            <span class="tag">${escHtml(o.shape)}</span>
            <span class="tag">${escHtml(o.material)}</span>
            <span class="tag">${escHtml(o.color)}</span>
          </div>
          <p class="price">${fmtPrice(o)}</p>
          <p class="score">Match: ${(o.score*100).toFixed(1)}%</p>
        </div>
      </div>
    </div>`;
  return card;
}

/* ── Shared render function for result cards ────────────────────── */
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
            <p class="brand">${o.brand} \u2014 ${o.model}</p>
            <div class="tags">
              <span class="tag">${o.shape}</span>
              <span class="tag">${o.material}</span>
              <span class="tag">${o.color}</span>
            </div>
            <p class="price">${price}</p>
            <p class="score">Match: ${(o.score*100).toFixed(1)}%</p>
          </div>
        </div>
      </div>`;
    container.appendChild(card);
  }
}
/* ── Upload-tip popup ── */
let _uptipTarget=null;
function openPickerWithTip(id){_uptipTarget=id;document.getElementById('uptip').classList.add('visible')}
function uptipOk(){document.getElementById('uptip').classList.remove('visible');if(_uptipTarget){document.getElementById(_uptipTarget).click();_uptipTarget=null}}
</script>
<!-- upload tip popup -->
<div id="uptip" class="uptip-overlay">
  <div class="uptip-box">
    <div class="uptip-icon"><svg viewBox="0 0 36 36"><path d="M4 11a3 3 0 013-3h2.5l2-3h9l2 3H25a3 3 0 013 3v15a3 3 0 01-3 3H7a3 3 0 01-3-3V11z" stroke-linecap="round" stroke-linejoin="round"/><circle cx="18" cy="19" r="5" stroke-linecap="round"/></svg></div>
    <h3>Photo Tips</h3>
    <p>For best results, upload a clear selfie with your face mostly visible and well-lit. Avoid group shots, sunglasses, or blurry images.</p>
    <button class="uptip-ok" onclick="uptipOk()">Got It</button>
  </div>
</div>
</body>
</html>
"""


FREE_SEARCH_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Lenses — Free Search</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0b0b14;color:#fff;min-height:100vh;
  -webkit-font-smoothing:antialiased;
}

/* ── Top bar ──────────────────────────────────────── */
.topbar{display:flex;align-items:center;gap:1rem;padding:1rem 2rem;
  background:rgba(11,11,20,.85);backdrop-filter:blur(16px);border-bottom:1px solid rgba(255,255,255,.06)}
.topbar a{color:#6c63ff;text-decoration:none;font-size:.85rem;font-weight:600;
  display:flex;align-items:center;gap:.3rem}
.topbar a:hover{text-decoration:underline}
.topbar .logo{font-size:1.3rem;font-weight:400;letter-spacing:-.01em;color:#fff;
  font-family:'DM Serif Display',Georgia,serif}

/* ── FORM VIEW ────────────────────────────────────── */
#form-view{max-width:720px;margin:2rem auto;padding:0 1.5rem}

.section-title{font-size:1.15rem;font-weight:700;color:#fff;margin:2rem 0 1rem;
  padding-bottom:.4rem;border-bottom:1px solid rgba(255,255,255,.08)}
.section-title:first-child{margin-top:0}

/* upload area */
.fs-upload{
  width:100%;padding:2.5rem;border:2.5px dashed rgba(255,255,255,.15);border-radius:20px;
  text-align:center;cursor:pointer;transition:all .3s;background:rgba(255,255,255,.03);
  margin-bottom:.5rem;position:relative;
}
.fs-upload:hover{border-color:#6c63ff;background:rgba(108,99,255,.06)}
.fs-upload.has-file{border-color:#34c78a;border-style:solid;background:rgba(52,199,138,.06)}
.fs-upload.has-file svg,.fs-upload.has-file .up-hint{display:none}
.fs-upload svg{width:40px;height:40px;stroke:rgba(255,255,255,.35);stroke-width:1.5;fill:none;margin-bottom:.6rem}
.fs-upload:hover svg{stroke:#6c63ff}
.fs-upload .up-label{font-size:1rem;font-weight:600;color:rgba(255,255,255,.7);display:block}
.fs-upload .up-hint{font-size:.78rem;color:rgba(255,255,255,.35);display:block;margin-top:.2rem}
.fs-upload .up-preview{max-height:200px;border-radius:12px;margin:.8rem auto 0;display:none}
#fs-file{display:none}

/* form grid */
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
@media(max-width:540px){.form-grid{grid-template-columns:1fr}}

.field{display:flex;flex-direction:column;gap:.35rem}
.field label{font-size:.78rem;font-weight:600;color:rgba(255,255,255,.4);text-transform:uppercase;letter-spacing:.04em}
.field input[type=number]{
  padding:.6rem .8rem;border:1px solid rgba(255,255,255,.12);border-radius:10px;
  font-size:.88rem;color:#fff;background:rgba(255,255,255,.06);transition:border-color .2s;
}
.field input[type=number]:focus{outline:none;border-color:#6c63ff}
.field input[type=number]::placeholder{color:rgba(255,255,255,.3)}

/* ── Visual tile selector ───────────────────── */
.tile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(86px,1fr));gap:.55rem}
.tile{position:relative;cursor:pointer;display:block}
.tile input{position:absolute;opacity:0;pointer-events:none}
.tile-inner{
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.3rem;
  padding:.6rem .3rem;border:1px solid rgba(255,255,255,.1);border-radius:12px;
  background:rgba(255,255,255,.03);transition:all .25s;min-height:66px;
}
.tile-inner:hover{border-color:rgba(255,255,255,.2);background:rgba(255,255,255,.06)}
.tile input:checked+.tile-inner{border-color:#6c63ff;background:rgba(108,99,255,.1);box-shadow:0 0 0 3px rgba(108,99,255,.12)}
.tile-label{font-size:.67rem;font-weight:500;color:rgba(255,255,255,.45);text-align:center;line-height:1.15}
.tile input:checked+.tile-inner .tile-label{color:#6c63ff;font-weight:600}

/* shape outline */
.shape-vis{border:2px solid rgba(255,255,255,.3);transition:all .25s}
.tile input:checked+.tile-inner .shape-vis{border-color:#6c63ff}
.shape-fill{background:rgba(255,255,255,.2);transition:all .25s}
.tile input:checked+.tile-inner .shape-fill{background:#6c63ff}

/* color swatch */
.swatch-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(60px,1fr));gap:.4rem}
.swatch{position:relative;cursor:pointer;display:block}
.swatch input{position:absolute;opacity:0;pointer-events:none}
.swatch-inner{
  display:flex;flex-direction:column;align-items:center;gap:.25rem;padding:.45rem .2rem;
  border:1.5px solid transparent;border-radius:12px;transition:all .25s;
}
.swatch-inner:hover{background:rgba(108,99,255,.06)}
.swatch input:checked+.swatch-inner{border-color:#6c63ff;background:rgba(108,99,255,.08)}
.swatch-dot{width:30px;height:30px;border-radius:50%;border:2px solid rgba(255,255,255,.15);transition:all .25s;
  box-shadow:inset 0 1px 3px rgba(0,0,0,.2)}
.swatch input:checked+.swatch-inner .swatch-dot{border-color:#6c63ff;transform:scale(1.12);
  box-shadow:0 0 0 3px rgba(108,99,255,.18),inset 0 1px 3px rgba(0,0,0,.2)}
.swatch-name{font-size:.6rem;font-weight:500;color:rgba(255,255,255,.35);text-align:center;line-height:1.1}
.swatch input:checked+.swatch-inner .swatch-name{color:#6c63ff;font-weight:600}

/* rim visual */
.rim-vis{width:44px;height:18px;transition:all .25s}
.rim-vis.full{border:2.5px solid rgba(255,255,255,.3);border-radius:9px}
.rim-vis.semi{border:2.5px solid rgba(255,255,255,.3);border-bottom-color:transparent;border-radius:9px 9px 0 0}
.rim-vis.none{border:1.5px dashed rgba(255,255,255,.25);border-radius:9px}
.tile input:checked+.tile-inner .rim-vis.full{border-color:#6c63ff}
.tile input:checked+.tile-inner .rim-vis.semi{border-color:#6c63ff;border-bottom-color:transparent}
.tile input:checked+.tile-inner .rim-vis.none{border-color:#6c63ff}

/* size dots */
.size-dot{border-radius:50%;background:rgba(255,255,255,.2);transition:all .25s}
.tile input:checked+.tile-inner .size-dot{background:#6c63ff}

/* lens indicator */
.lens-ind{border-radius:50%;border:2px solid rgba(255,255,255,.15);transition:all .25s}
.tile input:checked+.tile-inner .lens-ind{border-color:#6c63ff}

/* material chip */
.mat-chip{width:24px;height:24px;border-radius:5px;transition:all .25s}

/* thickness bar */
.thick-bar{width:36px;border-radius:2px;background:rgba(255,255,255,.25);transition:all .25s}
.tile input:checked+.tile-inner .thick-bar{background:#6c63ff}

/* any-option icon */
.any-icon{width:26px;height:26px;border-radius:50%;
  background:rgba(255,255,255,.1);
  display:flex;align-items:center;justify-content:center;font-size:.75rem;color:rgba(255,255,255,.4);transition:all .25s}
.tile input:checked+.tile-inner .any-icon{background:linear-gradient(135deg,#6c63ff,#8b7bff);color:#fff}
.swatch .any-icon{width:30px;height:30px;border-radius:50%}

/* radio group */
.radio-group{display:flex;gap:.5rem;flex-wrap:wrap}
.radio-group label{
  padding:.45rem .9rem;border:1px solid rgba(255,255,255,.12);border-radius:10px;
  font-size:.82rem;font-weight:500;color:rgba(255,255,255,.5);cursor:pointer;transition:all .2s;
  text-transform:none;letter-spacing:0;
}
.radio-group input{display:none}
.radio-group input:checked+label{border-color:#6c63ff;color:#6c63ff;background:rgba(108,99,255,.1)}

/* chip group (for aesthetics / occasion) */
.chip-group{display:flex;gap:.4rem;flex-wrap:wrap}
.chip-group label{
  padding:.35rem .75rem;border:1px solid rgba(255,255,255,.12);border-radius:50px;
  font-size:.76rem;font-weight:500;color:rgba(255,255,255,.5);cursor:pointer;transition:all .2s;
  text-transform:none;letter-spacing:0;
}
.chip-group input{display:none}
.chip-group input:checked+label{border-color:#6c63ff;color:#fff;background:#6c63ff}

/* submit button */
.submit-row{text-align:center;margin:2.5rem 0 3rem}
.submit-btn{
  padding:.85rem 3rem;background:linear-gradient(135deg,#6c63ff,#8b7bff);
  color:#fff;border:none;border-radius:50px;font-size:1rem;font-weight:700;
  cursor:pointer;transition:all .2s;box-shadow:0 4px 16px rgba(108,99,255,.25);
  letter-spacing:.01em;
}
.submit-btn:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(108,99,255,.35)}
.submit-btn:disabled{opacity:.5;cursor:not-allowed;transform:none;box-shadow:none}

/* ── LOADING VIEW ─────────────────────────────────── */
#loading-view{
  display:none;position:fixed;inset:0;z-index:200;
  background:#0b0b14;
  flex-direction:column;align-items:center;justify-content:center;
}

.load-card{
  background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:2.5rem 2.8rem;
  box-shadow:0 8px 40px rgba(0,0,0,.3);max-width:480px;width:90%;text-align:center;
}

.load-portrait{width:100px;height:100px;border-radius:50%;overflow:hidden;
  margin:0 auto 1.5rem;border:3px solid rgba(255,255,255,.1);box-shadow:0 4px 20px rgba(0,0,0,.3)}
.load-portrait img{width:100%;height:100%;object-fit:cover}

/* steps */
.fs-steps{display:flex;justify-content:center;gap:.5rem;margin-bottom:1.5rem}
.fs-step{display:flex;align-items:center;gap:.35rem;font-size:.78rem;color:rgba(255,255,255,.3);font-weight:500;transition:color .3s}
.fs-step.active{color:#6c63ff}.fs-step.done{color:#34c78a}
.fs-sdot{width:28px;height:28px;border-radius:50%;border:2px solid rgba(255,255,255,.15);
  display:flex;align-items:center;justify-content:center;font-size:.72rem;font-weight:700;
  color:rgba(255,255,255,.3);transition:all .3s}
.fs-step.active .fs-sdot{border-color:#6c63ff;color:#6c63ff;box-shadow:0 0 0 4px rgba(108,99,255,.12)}
.fs-step.done .fs-sdot{border-color:#34c78a;background:#34c78a;color:#fff}
.fs-sline{width:32px;height:2px;background:rgba(255,255,255,.1);border-radius:1px;align-self:center;transition:background .3s}
.fs-sline.done{background:#34c78a}

.load-prog{width:100%;height:3px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:1.6rem;overflow:hidden}
.load-prog-fill{height:100%;background:linear-gradient(90deg,#34c78a,#6dd5a8);border-radius:2px;width:0%;transition:width .6s ease}

#load-stage{font-size:.95rem;color:rgba(255,255,255,.7);font-weight:500;margin-bottom:1.4rem;min-height:1.4em}

.tip-box{min-height:3.5em;display:flex;align-items:center;justify-content:center}
#load-tip{font-size:.82rem;color:rgba(255,255,255,.35);line-height:1.5;max-width:340px;font-style:italic;transition:opacity .4s}

/* ── RESULTS VIEW ─────────────────────────────────── */
#results-view{
  --accent:#8B7BFF;--accent-hue:250;
  display:none;position:fixed;inset:0;z-index:200;
  background:#0b0b14;
  overflow-y:auto;color:#fff;
}
.res-inner{max-width:1060px;margin:0 auto;padding:0 1.5rem 2rem}

/* ── Sticky top bar ── */
.res-topbar{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 28px;
  background:rgba(11,11,20,.85);backdrop-filter:blur(16px);
  border-bottom:1px solid rgba(255,255,255,.06);
  position:sticky;top:0;z-index:100;margin:0 -1.5rem 28px;
}
.res-topbar-back{
  background:none;border:1px solid rgba(255,255,255,.12);color:rgba(255,255,255,.7);
  padding:7px 16px;border-radius:10px;font-size:.82rem;font-weight:600;cursor:pointer;
  transition:all .2s;display:flex;align-items:center;gap:6px;
}
.res-topbar-back:hover{background:rgba(255,255,255,.08);color:#fff}
.res-topbar-title{font-size:.95rem;font-weight:700;color:#fff;letter-spacing:-.01em}
.res-topbar-compare{
  background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.1);color:#fff;
  padding:7px 16px;border-radius:10px;font-size:.82rem;font-weight:600;cursor:pointer;
  transition:all .2s;display:flex;align-items:center;gap:6px;
}
.res-topbar-compare:hover{background:rgba(255,255,255,.14)}

/* ── Hero section (best match) ── */
.hero-section{
  position:relative;
  animation:slideUp .7s ease .1s both;
}
.hero-badge{
  display:inline-flex;align-items:center;gap:6px;
  padding:6px 16px;border-radius:20px;font-size:.75rem;font-weight:700;
  letter-spacing:.06em;text-transform:uppercase;color:#fff;
  background:linear-gradient(135deg,var(--accent),hsl(calc(var(--accent-hue) + 30),60%,55%));
  margin-bottom:16px;
}
.hero-grid{
  display:grid;grid-template-columns:1fr 340px;gap:36px;
  background:linear-gradient(175deg,rgba(255,255,255,.03),rgba(255,255,255,.01));
  border-radius:28px;border:1px solid rgba(255,255,255,.06);
  padding:28px;overflow:hidden;position:relative;
}
.hero-tryon-wrap{position:relative;display:flex;align-items:center;justify-content:center;min-height:340px}
.hero-glow{
  position:absolute;top:-20%;left:15%;width:400px;height:400px;border-radius:50%;
  background:radial-gradient(circle,var(--accent),transparent 70%);
  opacity:.15;filter:blur(60px);pointer-events:none;
  animation:pulse 4s ease-in-out infinite;
}
@keyframes pulse{0%,100%{opacity:.15}50%{opacity:.3}}
.hero-tryon-wrap img{
  width:100%;max-height:420px;object-fit:contain;border-radius:16px;
  display:block;position:relative;z-index:1;
}
.hero-tryon-wrap button{position:relative;z-index:2}
.hero-panel{
  background:rgba(255,255,255,.04);border-radius:22px;
  border:1px solid rgba(255,255,255,.08);backdrop-filter:blur(20px);
  padding:28px;display:flex;flex-direction:column;gap:16px;
  animation:slideRight .7s ease .3s both;
}
.hero-product-img{
  width:100%;max-height:140px;object-fit:contain;border-radius:14px;
  background:rgba(255,255,255,.04);
}
.hero-name{
  font-family:'DM Serif Display',Georgia,serif;font-size:1.35rem;
  font-weight:400;line-height:1.25;color:#fff;
}
.hero-brand{font-size:.82rem;color:rgba(255,255,255,.45);margin-top:2px}
.hero-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.hero-price{
  font-family:'DM Serif Display',Georgia,serif;
  font-size:1.25rem;color:#fff;margin-top:4px;
}

/* ── Score breakdown ── */
.score-panel{
  background:rgba(255,255,255,.04);border-radius:14px;
  border:1px solid rgba(255,255,255,.06);padding:16px;
}
.score-panel-hdr{
  display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;
}
.score-panel-title{font-size:.72rem;font-weight:700;color:rgba(255,255,255,.35);
  text-transform:uppercase;letter-spacing:.08em}
.score-ring-wrap{position:relative;width:52px;height:52px;flex-shrink:0}
.score-ring{width:52px;height:52px}
.score-ring-num{
  position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  font-size:.82rem;font-weight:700;color:#fff;
}
.score-row{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.score-row:last-child{margin-bottom:0}
.score-label{font-size:.72rem;font-weight:600;color:rgba(255,255,255,.5);width:36px;flex-shrink:0}
.score-track{flex:1;height:6px;background:rgba(255,255,255,.08);border-radius:3px;overflow:hidden}
.score-fill{height:100%;border-radius:3px;width:0%;animation:fillBar 1s cubic-bezier(.4,0,.2,1) forwards}
@keyframes fillBar{to{width:var(--target-width)}}
.score-val{font-size:.72rem;font-weight:700;color:rgba(255,255,255,.7);width:24px;text-align:right}

/* ── Why reasons ── */
.why-section{margin-top:4px}
.why-title{font-size:.72rem;font-weight:700;color:rgba(255,255,255,.35);
  text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.why-item{display:flex;align-items:flex-start;gap:8px;margin-bottom:6px;
  font-size:.78rem;color:rgba(255,255,255,.6);line-height:1.4}
.why-item svg{width:14px;height:14px;flex-shrink:0;margin-top:2px}

/* ── Dynamic tag ── */
.tag-dynamic{
  font-size:11px;font-weight:600;padding:4px 10px;
  border-radius:20px;letter-spacing:.02em;
}

/* ── Section divider ── */
.section-divider{
  display:flex;align-items:center;gap:10px;margin:32px 0 20px;
}
.section-divider::before,.section-divider::after{
  content:'';flex:1;height:1px;
  background:linear-gradient(90deg,rgba(255,255,255,.08),transparent);
}
.section-divider::after{
  background:linear-gradient(270deg,rgba(255,255,255,.08),transparent);
}
.section-divider span{
  font-size:12px;font-weight:700;color:rgba(255,255,255,.3);
  letter-spacing:.1em;text-transform:uppercase;
}

/* ── Alt grid ── */
.alt-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.alt-card{
  background:rgba(255,255,255,.03);border-radius:22px;overflow:hidden;
  border:1px solid rgba(255,255,255,.06);transition:all .3s ease;
  animation:slideUp .7s ease both;
}
.alt-card:nth-child(1){animation-delay:.4s}
.alt-card:nth-child(2){animation-delay:.55s}
.alt-card:hover{border-color:rgba(255,255,255,.12);background:rgba(255,255,255,.05)}
.alt-card-img-wrap{position:relative;display:flex;align-items:center;justify-content:center;
  min-height:240px;padding:16px;background:rgba(255,255,255,.02)}
.alt-card-img-wrap img{width:100%;max-height:280px;object-fit:contain;border-radius:12px;display:block}
.alt-card-body{padding:20px}
.alt-card-name{
  font-family:'DM Serif Display',Georgia,serif;font-size:1.05rem;color:#fff;line-height:1.3;
}
.alt-card-brand{font-size:.76rem;color:rgba(255,255,255,.4);margin-top:2px}
.alt-card-tags{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.alt-card-footer{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 20px;border-top:1px solid rgba(255,255,255,.06);
}
.alt-card-price{
  font-family:'DM Serif Display',Georgia,serif;font-size:1.05rem;color:#fff;
}
.alt-card-label{font-size:.7rem;font-weight:600;color:rgba(255,255,255,.3);
  text-transform:uppercase;letter-spacing:.06em}

/* ── Try-on loading / error (dark variants) ── */
.tryon-loading{display:flex;flex-direction:column;align-items:center;justify-content:center;
  min-height:300px;width:100%;border-radius:12px;
  background:linear-gradient(110deg,rgba(255,255,255,.04) 8%,rgba(255,255,255,.08) 18%,rgba(255,255,255,.04) 33%);
  background-size:200% 100%;animation:shimmer 1.6s linear infinite}
@keyframes shimmer{to{background-position:-200% 0}}
.tryon-loading p{font-size:.82rem;color:rgba(255,255,255,.4);margin-top:.5rem}
.tryon-loading .mini-spin{width:28px;height:28px;border:3px solid rgba(255,255,255,.1);
  border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.tryon-error{display:flex;align-items:center;justify-content:center;min-height:220px;
  background:rgba(220,50,50,.08);border-radius:12px;color:#f87171;font-size:.85rem;
  padding:1.2rem;text-align:center;width:100%}

/* ── Start over button ── */
.start-over{display:block;margin:2rem auto 3rem;padding:.75rem 2.2rem;
  background:rgba(255,255,255,.08);color:#fff;border:1px solid rgba(255,255,255,.1);
  border-radius:50px;font-size:.92rem;font-weight:600;cursor:pointer;transition:all .2s}
.start-over:hover{background:var(--accent);border-color:var(--accent);transform:translateY(-2px)}

/* ── Compare modal ── */
.compare-modal{
  position:fixed;inset:0;z-index:1000;
  display:flex;align-items:center;justify-content:center;padding:24px;
}
.compare-backdrop{
  position:absolute;inset:0;
  background:rgba(10,10,20,.85);backdrop-filter:blur(12px);
}
.compare-content{
  position:relative;width:100%;max-width:1100px;
  background:#1a1a2e;border-radius:28px;padding:32px 28px;color:#fff;
  box-shadow:0 40px 100px rgba(0,0,0,.5);max-height:90vh;overflow-y:auto;
}
.compare-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}
.compare-header h2{font-family:'DM Serif Display',Georgia,serif;font-size:1.3rem;font-weight:400}
.compare-close{
  background:rgba(255,255,255,.1);border:none;color:#fff;
  width:36px;height:36px;border-radius:10px;cursor:pointer;font-size:18px;
  display:flex;align-items:center;justify-content:center;transition:all .2s;
}
.compare-close:hover{background:rgba(255,255,255,.2)}
.compare-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.compare-card{
  background:rgba(255,255,255,.04);border-radius:18px;overflow:hidden;
  border:1px solid rgba(255,255,255,.06);
}
.compare-best{border-color:var(--accent);box-shadow:0 0 20px rgba(139,123,255,.15)}
.compare-card-img{width:100%;max-height:200px;object-fit:contain;border-radius:0;display:block;
  background:rgba(255,255,255,.02);padding:12px}
.compare-card-body{padding:16px}
.compare-card-name{font-family:'DM Serif Display',Georgia,serif;font-size:.95rem;color:#fff;margin-bottom:2px}
.compare-card-brand{font-size:.72rem;color:rgba(255,255,255,.4);margin-bottom:10px}
.compare-card-footer{padding:12px 16px;border-top:1px solid rgba(255,255,255,.06);
  display:flex;align-items:center;justify-content:space-between}
.compare-card-price{font-family:'DM Serif Display',Georgia,serif;font-size:.95rem;color:#fff}
.compare-card-badge{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
  padding:3px 8px;border-radius:6px;background:var(--accent);color:#fff}

/* ── Animations ── */
@keyframes slideUp{from{opacity:0;transform:translateY(30px)}to{opacity:1;transform:none}}
@keyframes slideRight{from{opacity:0;transform:translateX(-20px)}to{opacity:1;transform:none}}

/* ── ERROR VIEW ───────────────────────────────────── */
#error-view{
  display:none;position:fixed;inset:0;z-index:200;
  background:#0b0b14;
  flex-direction:column;align-items:center;justify-content:center;
}
.err-card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:2.5rem 2.8rem;
  box-shadow:0 8px 40px rgba(0,0,0,.3);max-width:420px;width:90%;text-align:center;color:#fff}

/* ── Free Search: tablet ── */
@media(max-width:900px){
  .topbar{padding:.75rem 1.2rem}
  #form-view{padding:0 1.2rem;margin:1.5rem auto}
  /* Form */
  .tile-grid{grid-template-columns:repeat(auto-fill,minmax(80px,1fr))}
  .swatch-grid{grid-template-columns:repeat(auto-fill,minmax(58px,1fr))}
  .submit-btn{min-height:50px;width:100%;max-width:420px;font-size:.95rem}
  /* Results */
  .hero-grid{grid-template-columns:1fr;gap:20px}
  .hero-panel{padding:20px}
  .alt-grid{grid-template-columns:1fr 1fr}
  .res-topbar{padding:12px 20px;margin:0 -1.5rem 24px}
  .compare-grid{grid-template-columns:repeat(2,1fr)}
  .compare-content{padding:24px 20px;max-width:700px}
}
/* ── Free Search: mobile ── */
@media(max-width:600px){
  .topbar{padding:.7rem 1rem}
  #form-view{padding:0 .9rem;margin:1.2rem auto}
  /* Form elements */
  .fs-upload{padding:1.6rem 1rem}
  .section-title{font-size:1rem;margin:1.5rem 0 .8rem}
  .tile-grid{grid-template-columns:repeat(auto-fill,minmax(70px,1fr));gap:.5rem}
  .tile-inner{min-height:60px;padding:.55rem .25rem;border-radius:10px}
  .tile-label{font-size:.65rem}
  .swatch-grid{grid-template-columns:repeat(auto-fill,minmax(52px,1fr));gap:.35rem}
  .swatch-dot{width:32px;height:32px}
  .swatch .any-icon{width:32px;height:32px}
  .swatch-name{font-size:.58rem}
  .chip-group label{min-height:40px;display:inline-flex;align-items:center;font-size:.74rem}
  .radio-group label{min-height:40px;display:inline-flex;align-items:center}
  .submit-btn{width:100%;min-height:52px;font-size:1rem;border-radius:16px}
  .submit-row{margin:2rem 0 3rem}
  /* Results */
  .hero-grid{gap:16px;padding:20px;border-radius:20px}
  .hero-tryon-wrap{min-height:240px}
  .hero-name{font-size:1.15rem}
  .hero-price{font-size:1.1rem}
  .hero-tags{gap:5px}
  .score-panel{padding:12px}
  .alt-grid{grid-template-columns:1fr}
  .alt-card-img-wrap{min-height:200px;padding:12px}
  .alt-card-footer{padding:12px 16px}
  .res-topbar{padding:10px 14px;margin:0 -.9rem 20px}
  .res-topbar-back{padding:6px 14px;font-size:.78rem}
  .res-topbar-title{font-size:.88rem}
  .res-topbar-compare{padding:6px 14px;font-size:.78rem}
  /* Compare modal */
  .compare-content{padding:16px 12px;border-radius:20px;max-height:85vh}
  .compare-grid{grid-template-columns:1fr}
  .compare-header h2{font-size:1.1rem}
  .tryon-loading{min-height:200px}
  .filter-pill{min-height:44px;min-width:44px;padding:.45rem 1rem;display:inline-flex;align-items:center;justify-content:center}
  .uptip-box{max-width:92%;padding:1.5rem 1.1rem 1.2rem}
  /* Loading view */
  .load-card{padding:2rem 1.4rem}
}
/* ── Free Search: small phones ── */
@media(max-width:380px){
  #form-view{padding:0 .75rem}
  .tile-grid{grid-template-columns:repeat(auto-fill,minmax(62px,1fr))}
  .tile-inner{min-height:55px}
  .swatch-grid{grid-template-columns:repeat(auto-fill,minmax(46px,1fr))}
  .swatch-dot{width:28px;height:28px}
  .hero-grid{padding:16px;gap:14px}
  .alt-card-img-wrap{min-height:170px}
}
/* ── Landscape mobile ── */
@media(max-height:500px) and (max-width:900px){
  .topbar{padding:.5rem 1rem}
  #form-view{margin:.8rem auto}
}
/* upload-tip popup */
.uptip-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(6px);
  z-index:9999;display:flex;align-items:center;justify-content:center;
  opacity:0;pointer-events:none;transition:opacity .18s}
.uptip-overlay.visible{opacity:1;pointer-events:auto}
.uptip-box{background:rgba(20,20,38,.96);border:1px solid rgba(255,255,255,.1);
  border-radius:20px;padding:2rem 1.8rem 1.6rem;max-width:330px;width:90%;
  box-shadow:0 24px 60px rgba(0,0,0,.6);text-align:center}
.uptip-icon{margin-bottom:.9rem}
.uptip-icon svg{width:44px;height:44px;stroke:#6c63ff;fill:none;stroke-width:1.6}
.uptip-box h3{font-size:1.05rem;font-weight:700;color:#fff;margin:0 0 .55rem}
.uptip-box p{font-size:.86rem;color:rgba(255,255,255,.55);line-height:1.6;margin:0 0 1.4rem}
.uptip-ok{background:#6c63ff;color:#fff;border:none;border-radius:12px;
  padding:.72rem 0;font-size:.95rem;font-weight:600;cursor:pointer;width:100%;
  transition:opacity .15s}
.uptip-ok:hover{opacity:.85}
</style>
</head>
<body>

<!-- Top bar -->
<div class="topbar">
  <a href="/">&larr; Back</a>
  <span class="logo">Free Search</span>
</div>

<!-- ═══════════════ FORM VIEW ═══════════════ -->
<div id="form-view">

  <h3 class="section-title">Your Photo</h3>
  <div class="fs-upload" id="upload-area" onclick="openPickerWithTip('fs-file')">
    <svg viewBox="0 0 48 48"><path d="M24 32V16m0 0l-8 8m8-8l8 8" stroke-linecap="round" stroke-linejoin="round"/><rect x="6" y="6" width="36" height="36" rx="8" stroke-linecap="round"/></svg>
    <span class="up-label" id="up-label-text">Upload a Photo</span>
    <span class="up-hint">JPG, PNG, or WebP</span>
    <img class="up-preview" id="up-preview"/>
  </div>
  <input type="file" id="fs-file" accept="image/*"/>

  <h3 class="section-title">Shape</h3>
  <div class="tile-grid">
    <label class="tile"><input type="radio" name="frame_shape" value="" checked/><div class="tile-inner"><div class="any-icon">&#10038;</div><span class="tile-label">Any</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="round"/><div class="tile-inner"><div class="shape-vis" style="width:26px;height:26px;border-radius:50%"></div><span class="tile-label">Round</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="square"/><div class="tile-inner"><div class="shape-vis" style="width:26px;height:26px;border-radius:3px"></div><span class="tile-label">Square</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="rectangular"/><div class="tile-inner"><div class="shape-vis" style="width:38px;height:22px;border-radius:3px"></div><span class="tile-label">Rectangular</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="oval"/><div class="tile-inner"><div class="shape-vis" style="width:34px;height:22px;border-radius:50%"></div><span class="tile-label">Oval</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="aviator"/><div class="tile-inner"><div class="shape-vis" style="width:30px;height:26px;border-radius:10% 10% 50% 50%"></div><span class="tile-label">Aviator</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="wayfarer"/><div class="tile-inner"><div class="shape-vis" style="width:32px;height:24px;border-radius:3px 3px 8px 8px"></div><span class="tile-label">Wayfarer</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="browline"/><div class="tile-inner"><div class="shape-vis" style="width:34px;height:22px;border-radius:3px;border-top-width:4px"></div><span class="tile-label">Browline</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="geometric"/><div class="tile-inner"><div class="shape-fill" style="width:26px;height:26px;clip-path:polygon(50% 0%,100% 50%,50% 100%,0% 50%)"></div><span class="tile-label">Geometric</span></div></label>
    <label class="tile"><input type="radio" name="frame_shape" value="pilot"/><div class="tile-inner"><div class="shape-vis" style="width:34px;height:28px;border-radius:15% 15% 50% 50%"></div><span class="tile-label">Pilot</span></div></label>
  </div>

  <h3 class="section-title">Color</h3>
  <div class="swatch-grid">
    <label class="swatch"><input type="radio" name="frame_color" value="" checked/><div class="swatch-inner"><div class="any-icon">&#10038;</div><span class="swatch-name">Any</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="black"/><div class="swatch-inner"><div class="swatch-dot" style="background:#1a1a1a"></div><span class="swatch-name">Black</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="silver"/><div class="swatch-inner"><div class="swatch-dot" style="background:linear-gradient(135deg,#d0d0d0,#a8a8a8)"></div><span class="swatch-name">Silver</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="gold"/><div class="swatch-inner"><div class="swatch-dot" style="background:linear-gradient(135deg,#e8c860,#c4963c)"></div><span class="swatch-name">Gold</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="gunmetal"/><div class="swatch-inner"><div class="swatch-dot" style="background:#536267"></div><span class="swatch-name">Gunmetal</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="tortoiseshell"/><div class="swatch-inner"><div class="swatch-dot" style="background:conic-gradient(#8B4513,#D2691E,#654321,#CD853F,#8B4513)"></div><span class="swatch-name">Tortoise</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="havana-brown"/><div class="swatch-inner"><div class="swatch-dot" style="background:#6B3A2A"></div><span class="swatch-name">Havana</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="transparent"/><div class="swatch-inner"><div class="swatch-dot" style="background:repeating-conic-gradient(#eee 0% 25%,#fff 0% 50%) 50%/10px 10px;border-style:dashed"></div><span class="swatch-name">Clear</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="blue"/><div class="swatch-inner"><div class="swatch-dot" style="background:#2563eb"></div><span class="swatch-name">Blue</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="red"/><div class="swatch-inner"><div class="swatch-dot" style="background:#dc2626"></div><span class="swatch-name">Red</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="green"/><div class="swatch-inner"><div class="swatch-dot" style="background:#16a34a"></div><span class="swatch-name">Green</span></div></label>
    <label class="swatch"><input type="radio" name="frame_color" value="matte-black"/><div class="swatch-inner"><div class="swatch-dot" style="background:#2d2d2d"></div><span class="swatch-name">Matte Blk</span></div></label>
  </div>

  <h3 class="section-title">Material</h3>
  <div class="tile-grid">
    <label class="tile"><input type="radio" name="frame_material" value="" checked/><div class="tile-inner"><div class="any-icon">&#10038;</div><span class="tile-label">Any</span></div></label>
    <label class="tile"><input type="radio" name="frame_material" value="metal"/><div class="tile-inner"><div class="mat-chip" style="background:linear-gradient(135deg,#c0c0c0,#808080)"></div><span class="tile-label">Metal</span></div></label>
    <label class="tile"><input type="radio" name="frame_material" value="acetate"/><div class="tile-inner"><div class="mat-chip" style="background:linear-gradient(135deg,#d4a574,#8B4513)"></div><span class="tile-label">Acetate</span></div></label>
    <label class="tile"><input type="radio" name="frame_material" value="plastic"/><div class="tile-inner"><div class="mat-chip" style="background:linear-gradient(135deg,#e0e0e0,#b0b0b0)"></div><span class="tile-label">Plastic</span></div></label>
    <label class="tile"><input type="radio" name="frame_material" value="mixed-metal-acetate"/><div class="tile-inner"><div class="mat-chip" style="background:linear-gradient(135deg,#c0c0c0 50%,#d4a574 50%)"></div><span class="tile-label">Mixed</span></div></label>
    <label class="tile"><input type="radio" name="frame_material" value="carbon-fiber"/><div class="tile-inner"><div class="mat-chip" style="background:repeating-linear-gradient(45deg,#2a2a2a,#2a2a2a 2px,#404040 2px,#404040 4px)"></div><span class="tile-label">Carbon</span></div></label>
  </div>

  <h3 class="section-title">Rim &amp; Thickness</h3>
  <div style="display:flex;gap:2rem;flex-wrap:wrap">
    <div class="field" style="flex:1;min-width:200px">
      <label>Rim Type</label>
      <div class="tile-grid" style="grid-template-columns:repeat(4,1fr)">
        <label class="tile"><input type="radio" name="rim_type" value="" checked/><div class="tile-inner"><div class="any-icon">&#10038;</div><span class="tile-label">Any</span></div></label>
        <label class="tile"><input type="radio" name="rim_type" value="full-rim"/><div class="tile-inner"><div class="rim-vis full"></div><span class="tile-label">Full Rim</span></div></label>
        <label class="tile"><input type="radio" name="rim_type" value="semi-rimless"/><div class="tile-inner"><div class="rim-vis semi"></div><span class="tile-label">Semi</span></div></label>
      </div>
    </div>
    <div class="field" style="flex:1;min-width:200px">
      <label>Thickness</label>
      <div class="tile-grid" style="grid-template-columns:repeat(4,1fr)">
        <label class="tile"><input type="radio" name="frame_thickness" value="" checked/><div class="tile-inner"><div class="any-icon">&#10038;</div><span class="tile-label">Any</span></div></label>
        <label class="tile"><input type="radio" name="frame_thickness" value="thin"/><div class="tile-inner"><div class="thick-bar" style="height:2px"></div><span class="tile-label">Thin</span></div></label>
        <label class="tile"><input type="radio" name="frame_thickness" value="medium"/><div class="tile-inner"><div class="thick-bar" style="height:5px"></div><span class="tile-label">Medium</span></div></label>
        <label class="tile"><input type="radio" name="frame_thickness" value="thick"/><div class="tile-inner"><div class="thick-bar" style="height:9px"></div><span class="tile-label">Thick</span></div></label>
      </div>
    </div>
  </div>

  <h3 class="section-title">Lens Type</h3>
  <div class="tile-grid">
    <label class="tile"><input type="radio" name="lens_type" value="" checked/><div class="tile-inner"><div class="any-icon">&#10038;</div><span class="tile-label">Any</span></div></label>
    <label class="tile"><input type="radio" name="lens_type" value="clear"/><div class="tile-inner"><div class="lens-ind" style="width:26px;height:26px;background:rgba(255,255,255,.2)"></div><span class="tile-label">Clear</span></div></label>
    <label class="tile"><input type="radio" name="lens_type" value="prescription-ready"/><div class="tile-inner"><div class="lens-ind" style="width:26px;height:26px;background:rgba(255,255,255,.15);display:flex;align-items:center;justify-content:center;font-size:.65rem;font-weight:700;color:rgba(255,255,255,.6)">Rx</div><span class="tile-label">Prescription</span></div></label>
    <label class="tile"><input type="radio" name="lens_type" value="sunglasses"/><div class="tile-inner"><div class="lens-ind" style="width:26px;height:26px;background:#2a2a2a"></div><span class="tile-label">Sunglasses</span></div></label>
    <label class="tile"><input type="radio" name="lens_type" value="polarized"/><div class="tile-inner"><div class="lens-ind" style="width:26px;height:26px;background:repeating-linear-gradient(0deg,#666,#666 2px,#888 2px,#888 4px)"></div><span class="tile-label">Polarized</span></div></label>
  </div>

  <h3 class="section-title">Lens Size</h3>
  <div class="tile-grid" style="grid-template-columns:repeat(5,1fr);max-width:480px">
    <label class="tile"><input type="radio" name="lens_size" value="" checked/><div class="tile-inner"><div class="any-icon">&#10038;</div><span class="tile-label">Any</span></div></label>
    <label class="tile"><input type="radio" name="lens_size" value="medium"/><div class="tile-inner"><div class="size-dot" style="width:22px;height:22px"></div><span class="tile-label">Medium</span></div></label>
    <label class="tile"><input type="radio" name="lens_size" value="large"/><div class="tile-inner"><div class="size-dot" style="width:28px;height:28px"></div><span class="tile-label">Large</span></div></label>
    <label class="tile"><input type="radio" name="lens_size" value="oversized"/><div class="tile-inner"><div class="size-dot" style="width:34px;height:34px"></div><span class="tile-label">Oversized</span></div></label>
  </div>

  <h3 class="section-title">Style</h3>
  <div class="form-grid">
    <div class="field">
      <label>Gender</label>
      <div class="radio-group">
        <span><input type="radio" name="gender" id="g-any" value="" checked/><label for="g-any">Any</label></span>
        <span><input type="radio" name="gender" id="g-uni" value="unisex"/><label for="g-uni">Unisex</label></span>
        <span><input type="radio" name="gender" id="g-men" value="men"/><label for="g-men">Men</label></span>
        <span><input type="radio" name="gender" id="g-women" value="women"/><label for="g-women">Women</label></span>
      </div>
    </div>
    <div class="field">
      <label>Max Price</label>
      <input type="number" name="max_price" placeholder="No limit" min="0" step="10"/>
    </div>
  </div>

  <div style="margin-top:1rem">
    <div class="field">
      <label>Aesthetic</label>
      <div class="chip-group">
        <span><input type="radio" name="aesthetic" id="ae-any" value="" checked/><label for="ae-any">Any</label></span>
        <span><input type="radio" name="aesthetic" id="ae-classic" value="classic"/><label for="ae-classic">Classic</label></span>
        <span><input type="radio" name="aesthetic" id="ae-modern" value="modern"/><label for="ae-modern">Modern</label></span>
        <span><input type="radio" name="aesthetic" id="ae-vintage" value="vintage"/><label for="ae-vintage">Vintage</label></span>
        <span><input type="radio" name="aesthetic" id="ae-retro" value="retro"/><label for="ae-retro">Retro</label></span>
        <span><input type="radio" name="aesthetic" id="ae-sporty" value="sporty"/><label for="ae-sporty">Sporty</label></span>
        <span><input type="radio" name="aesthetic" id="ae-luxury" value="luxury"/><label for="ae-luxury">Luxury</label></span>
        <span><input type="radio" name="aesthetic" id="ae-mini" value="minimalist"/><label for="ae-mini">Minimalist</label></span>
        <span><input type="radio" name="aesthetic" id="ae-bold" value="bold"/><label for="ae-bold">Bold</label></span>
        <span><input type="radio" name="aesthetic" id="ae-pro" value="professional"/><label for="ae-pro">Professional</label></span>
        <span><input type="radio" name="aesthetic" id="ae-casual" value="casual"/><label for="ae-casual">Casual</label></span>
      </div>
    </div>
  </div>

  <div style="margin-top:1rem">
    <div class="field">
      <label>Occasion</label>
      <div class="chip-group">
        <span><input type="radio" name="occasion" id="oc-any" value="" checked/><label for="oc-any">Any</label></span>
        <span><input type="radio" name="occasion" id="oc-every" value="everyday"/><label for="oc-every">Everyday</label></span>
        <span><input type="radio" name="occasion" id="oc-office" value="office"/><label for="oc-office">Office</label></span>
        <span><input type="radio" name="occasion" id="oc-out" value="outdoor"/><label for="oc-out">Outdoor</label></span>
        <span><input type="radio" name="occasion" id="oc-sport" value="sport"/><label for="oc-sport">Sport</label></span>
        <span><input type="radio" name="occasion" id="oc-drive" value="driving"/><label for="oc-drive">Driving</label></span>
        <span><input type="radio" name="occasion" id="oc-fash" value="fashion"/><label for="oc-fash">Fashion</label></span>
        <span><input type="radio" name="occasion" id="oc-form" value="formal"/><label for="oc-form">Formal</label></span>
        <span><input type="radio" name="occasion" id="oc-beach" value="beach"/><label for="oc-beach">Beach</label></span>
      </div>
    </div>
  </div>

  <div class="submit-row">
    <button class="submit-btn" id="submit-btn" disabled onclick="submitSearch()">
      Find My Glasses
    </button>
    <p style="font-size:.76rem;color:rgba(255,255,255,.35);margin-top:.6rem" id="submit-hint">Upload a photo first</p>
  </div>

</div>

<!-- ═══════════════ LOADING ═══════════════ -->
<div id="loading-view">
  <div class="load-card">
    <div class="load-portrait" id="load-portrait" style="display:none">
      <img id="load-portrait-img" src="" alt=""/>
    </div>

    <div class="fs-steps">
      <div class="fs-step active" id="ls1"><span class="fs-sdot">1</span><span>Search</span></div>
      <div class="fs-sline" id="lsl1"></div>
      <div class="fs-step" id="ls2"><span class="fs-sdot">2</span><span>Try-On</span></div>
    </div>

    <div class="load-prog"><div class="load-prog-fill" id="load-prog"></div></div>

    <p id="load-stage">Searching our catalog...</p>

    <div class="tip-box"><p id="load-tip"></p></div>
  </div>
</div>

<!-- ═══════════════ RESULTS ═══════════════ -->
<div id="results-view">
  <div class="res-inner">
    <div class="res-topbar">
      <button class="res-topbar-back" onclick="fsReset()">&#8592; Back</button>
      <span class="res-topbar-title">Your Results</span>
      <button class="res-topbar-compare" onclick="openCompare()">&#9638; Compare All</button>
    </div>
    <div id="fs-opts"></div>

  </div>
  <div id="compare-modal" class="compare-modal" style="display:none">
    <div class="compare-backdrop" onclick="closeCompare()"></div>
    <div class="compare-content">
      <div class="compare-header">
        <h2>Side-by-Side Comparison</h2>
        <button onclick="closeCompare()" class="compare-close">&times;</button>
      </div>
      <div id="compare-grid" class="compare-grid"></div>
    </div>
  </div>
</div>

<!-- ═══════════════ ERROR ═══════════════ -->
<div id="error-view">
  <div class="err-card">
    <div style="font-size:2.4rem;margin-bottom:1rem">:/</div>
    <h2 style="font-size:1.15rem;color:#fff;margin-bottom:.6rem">Something went wrong</h2>
    <p id="error-msg" style="font-size:.88rem;color:rgba(255,255,255,.45);line-height:1.5;margin-bottom:1.5rem;word-break:break-word"></p>
    <button class="start-over" onclick="fsReset()">Try Again</button>
  </div>
</div>

<!-- ═══════════════ SCRIPT ═══════════════ -->
<script>
/* ── State ──────────────────────────────────── */
let sid=null, poll=null, tipTimer=null, tipIdx=0, uploadedFile=null, lastRendered='';

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
  reader.onload=ev=>{preview.src=ev.target.result;preview.style.display='block'};
  reader.readAsDataURL(f);

  submitBtn.disabled=false;
  submitHint.textContent='Select your preferences above, then hit the button!';
});

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

  setLoadStep(1); setLoadProg(15);
  document.getElementById('load-stage').textContent='Searching our catalog...';
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
    const el=document.getElementById(id);
    el.style.display = (id==='loading-view'||id==='error-view')?'flex':'block';
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
function setLoadProg(pct){document.getElementById('load-prog').style.width=pct+'%'}

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
      stopTips();
      renderResults(d);
    }

    if(d.status!=='done' && d.status!=='error')
      poll=setTimeout(pollStatus,2000);
    else if(d.status==='done'){
      stopTips();
      renderResults(d);
    }
  }).catch(()=>{poll=setTimeout(pollStatus,3000)});
}

/* ── Color accent system ─────────────────────── */
const COLOR_ACCENTS={
  'transparent':{hue:220,color:'#8BA4C4'},'black':{hue:270,color:'#9B8AB8'},
  'gold':{hue:38,color:'#C4A265'},'silver':{hue:210,color:'#A0B4C8'},
  'tortoiseshell':{hue:28,color:'#B8884D'},'brown':{hue:25,color:'#A87D5A'},
  'blue':{hue:215,color:'#5B8FC4'},'red':{hue:355,color:'#C46B6B'},
  'pink':{hue:340,color:'#C47B99'},'green':{hue:150,color:'#5BAA7D'},
  'white':{hue:220,color:'#A8B4C4'},'tortoise':{hue:28,color:'#B8884D'},
};
function getAccent(colorStr){
  const lower=(colorStr||'').toLowerCase();
  for(const[key,val] of Object.entries(COLOR_ACCENTS)){
    if(lower.includes(key)) return val;
  }
  return{hue:250,color:'#8B7BFF'};
}

function capitalize(s){return s?s.charAt(0).toUpperCase()+s.slice(1):''}
function fmtPrice(o){const sym=o.currency==='ILS'?'\u20AA':o.currency;return o.price.toLocaleString()+' '+sym}
const checkSvg='<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8.5l3.5 3.5 6.5-7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

const _recolorStore={};let _recolorIdx=0;
function goRecolor(key){
  sessionStorage.setItem('recolor_preload',_recolorStore[key]);
  window.location.href='/lens-recolor';
}

function getTryonHtml(o){
  if(o.tryon_status==='done'&&o.tryon_b64){
    const key='rc'+(++_recolorIdx);
    _recolorStore[key]=o.tryon_b64;
    return `<div style="position:relative;display:inline-block;width:100%">
      <img src="data:image/png;base64,${o.tryon_b64}" alt="Virtual try-on"/>
      <button class="recolor-overlay-btn" onclick="event.stopPropagation();goRecolor('${key}')" onmouseover="this.style.background='rgba(232,168,56,1)'" onmouseout="this.style.background='rgba(232,168,56,.92)'">Recolor Lenses</button>
    </div>`;
  }
  if(o.tryon_status==='error') return `<div class="tryon-error">Try-on could not be generated${o.tryon_error?': '+o.tryon_error:''}</div>`;
  return '<div class="tryon-loading"><div class="mini-spin"></div><p>Generating try-on...</p></div>';
}

function getScores(o){
  const fit=o.fit_score?Math.round(o.fit_score*100):Math.min(99,Math.round(o.score*100+8));
  const style=o.style_score?Math.round(o.style_score*100):Math.max(30,Math.round(o.score*100-2));
  const color=o.color_score?Math.round(o.color_score*100):Math.max(30,Math.round(o.score*100-5));
  const overall=Math.round(o.score*100);
  return{fit,style,color,overall};
}

function buildScoreBars(scores,accent,delays){
  const rows=[
    {label:'Fit',val:scores.fit,delay:delays[0]||0},
    {label:'Style',val:scores.style,delay:delays[1]||0.1},
    {label:'Color',val:scores.color,delay:delays[2]||0.2},
  ];
  return rows.map(r=>`
    <div class="score-row">
      <span class="score-label">${r.label}</span>
      <div class="score-track">
        <div class="score-fill" style="--target-width:${r.val}%;background:${accent};animation-delay:${r.delay}s"></div>
      </div>
      <span class="score-val">${r.val}</span>
    </div>`).join('');
}

function buildScoreRing(overall,accent){
  const circ=2*Math.PI*22;
  const offset=circ-(overall/100)*circ;
  return `<div class="score-ring-wrap">
    <svg class="score-ring" width="52" height="52" viewBox="0 0 52 52">
      <circle cx="26" cy="26" r="22" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="4"/>
      <circle cx="26" cy="26" r="22" fill="none" stroke="${accent}" stroke-width="4"
        stroke-linecap="round" stroke-dasharray="${circ.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}"
        style="transition:stroke-dashoffset 1.2s cubic-bezier(.4,0,.2,1)"/>
    </svg>
    <span class="score-ring-num">${overall}</span>
  </div>`;
}

function generateWhyReasons(opt){
  const reasons=[];
  if(opt.shape) reasons.push(capitalize(opt.shape)+' shape complements your face proportions');
  if(opt.color) reasons.push(capitalize(opt.color)+' tones complement your complexion');
  if(opt.material) reasons.push(capitalize(opt.material)+' material matches your style preference');
  return reasons.slice(0,3);
}

function buildTagsHtml(o,accent){
  const tags=[o.shape,o.material,o.color].filter(Boolean);
  return tags.map(t=>`<span class="tag-dynamic" style="background:${accent}15;color:${accent};border:1px solid ${accent}30">${t}</span>`).join('');
}

function buildHeroSection(opt){
  const accent=getAccent(opt.color);
  const scores=getScores(opt);
  const reasons=generateWhyReasons(opt);
  const el=document.createElement('div');
  el.className='hero-section';
  el.innerHTML=`
    <div class="hero-badge">&#10022; Best Match</div>
    <div class="hero-grid">
      <div class="hero-tryon-wrap">
        <div class="hero-glow"></div>
        ${getTryonHtml(opt)}
      </div>
      <div class="hero-panel">
        <img class="hero-product-img" src="data:image/jpeg;base64,${opt.product_b64}" alt="${opt.name}"/>
        <div>
          <div class="hero-name">${opt.name}</div>
          <div class="hero-brand">${opt.brand} &mdash; ${opt.model}</div>
        </div>
        <div class="hero-tags">${buildTagsHtml(opt,accent.color)}</div>
        <div class="hero-price">${fmtPrice(opt)}</div>
        <div class="score-panel">
          <div class="score-panel-hdr">
            <span class="score-panel-title">Match Breakdown</span>
            ${buildScoreRing(scores.overall,accent.color)}
          </div>
          ${buildScoreBars(scores,accent.color,[0.3,0.45,0.6])}
        </div>
        ${reasons.length?`<div class="why-section">
          <div class="why-title">Why this frame?</div>
          ${reasons.map(r=>`<div class="why-item">${checkSvg}<span>${r}</span></div>`).join('')}
        </div>`:''}
      </div>
    </div>`;
  return el;
}

function buildAltCard(opt,index){
  const accent=getAccent(opt.color);
  const scores=getScores(opt);
  const el=document.createElement('div');
  el.className='alt-card';
  el.innerHTML=`
    <div class="alt-card-img-wrap">
      ${getTryonHtml(opt)}
    </div>
    <div class="alt-card-body">
      <div class="alt-card-name">${opt.name}</div>
      <div class="alt-card-brand">${opt.brand} &mdash; ${opt.model}</div>
      <div class="alt-card-tags">${buildTagsHtml(opt,accent.color)}</div>
      <div class="score-panel" style="margin-top:12px;padding:12px">
        <div class="score-panel-hdr" style="margin-bottom:8px">
          <span class="score-panel-title">Match</span>
          ${buildScoreRing(scores.overall,accent.color)}
        </div>
        ${buildScoreBars(scores,accent.color,[0.5,0.65,0.8])}
      </div>
    </div>
    <div class="alt-card-footer">
      <span class="alt-card-price">${fmtPrice(opt)}</span>
      <span class="alt-card-label">Alternative ${index}</span>
    </div>`;
  return el;
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
    const accent=getAccent(o.color);
    const scores=getScores(o);
    const isBest=i===0;
    const card=document.createElement('div');
    card.className='compare-card'+(isBest?' compare-best':'');
    const imgSrc=o.tryon_status==='done'&&o.tryon_b64
      ?`data:image/png;base64,${o.tryon_b64}`
      :`data:image/jpeg;base64,${o.product_b64}`;
    card.innerHTML=`
      <img class="compare-card-img" src="${imgSrc}" alt="${o.name}"/>
      <div class="compare-card-body">
        <div class="compare-card-name">${o.name}</div>
        <div class="compare-card-brand">${o.brand} &mdash; ${o.model}</div>
        <div class="score-panel" style="margin-top:10px;padding:10px">
          <div class="score-panel-hdr" style="margin-bottom:6px">
            <span class="score-panel-title">Match</span>
            ${buildScoreRing(scores.overall,accent.color)}
          </div>
          ${buildScoreBars(scores,accent.color,[0,0.1,0.2])}
        </div>
      </div>
      <div class="compare-card-footer">
        <span class="compare-card-price">${fmtPrice(o)}</span>
        ${isBest?'<span class="compare-card-badge">Best</span>':'<span class="alt-card-label">Alt '+i+'</span>'}
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

  const bestOpt=d.opt0;
  if(bestOpt){
    const accent=getAccent(bestOpt.color);
    document.getElementById('results-view').style.setProperty('--accent',accent.color);
    document.getElementById('results-view').style.setProperty('--accent-hue',accent.hue);
  }

  if(d.num_options>0&&bestOpt){
    container.appendChild(buildHeroSection(bestOpt));
  }

  if(d.num_options>1){
    const divider=document.createElement('div');
    divider.className='section-divider';
    divider.innerHTML='<span>More Options</span>';
    container.appendChild(divider);

    const altGrid=document.createElement('div');
    altGrid.className='alt-grid';
    for(let i=1;i<d.num_options;i++){
      const o=d['opt'+i];
      if(!o) continue;
      altGrid.appendChild(buildAltCard(o,i));
    }
    container.appendChild(altGrid);
  }
}

/* ── Error ───────────────────────────────────── */
function showError(msg){
  stopTips();
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
  setLoadStep(1); setLoadProg(0);
  showView(null);
}
/* ── Upload-tip popup ── */
let _uptipTarget=null;
function openPickerWithTip(id){_uptipTarget=id;document.getElementById('uptip').classList.add('visible')}
function uptipOk(){document.getElementById('uptip').classList.remove('visible');if(_uptipTarget){document.getElementById(_uptipTarget).click();_uptipTarget=null}}
</script>
<!-- upload tip popup -->
<div id="uptip" class="uptip-overlay">
  <div class="uptip-box">
    <div class="uptip-icon"><svg viewBox="0 0 36 36"><path d="M4 11a3 3 0 013-3h2.5l2-3h9l2 3H25a3 3 0 013 3v15a3 3 0 01-3 3H7a3 3 0 01-3-3V11z" stroke-linecap="round" stroke-linejoin="round"/><circle cx="18" cy="19" r="5" stroke-linecap="round"/></svg></div>
    <h3>Photo Tips</h3>
    <p>For best results, upload a clear selfie with your face mostly visible and well-lit. Avoid group shots, sunglasses, or blurry images.</p>
    <button class="uptip-ok" onclick="uptipOk()">Got It</button>
  </div>
</div>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════════
# LENS RECOLOR PAGE
# ══════════════════════════════════════════════════════════════════════════════

LENS_RECOLOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Lenses — Switch Lens Color</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0b0b14;color:#fff;min-height:100vh;
  -webkit-font-smoothing:antialiased;
}

/* ── Top bar ── */
.topbar{display:flex;align-items:center;gap:1rem;padding:1rem 2rem;
  background:rgba(11,11,20,.85);backdrop-filter:blur(16px);border-bottom:1px solid rgba(255,255,255,.06)}
.topbar a{color:#e8a838;text-decoration:none;font-size:.85rem;font-weight:600;
  display:flex;align-items:center;gap:.3rem}
.topbar a:hover{text-decoration:underline}
.topbar .logo{font-size:1.3rem;font-weight:400;letter-spacing:-.01em;color:#fff;
  font-family:'DM Serif Display',Georgia,serif}

/* ── FORM VIEW ── */
#form-view{max-width:640px;margin:2rem auto;padding:0 1.5rem}

.section-title{font-size:1.15rem;font-weight:700;color:#fff;margin:2rem 0 1rem;
  padding-bottom:.4rem;border-bottom:1px solid rgba(255,255,255,.08)}
.section-title:first-child{margin-top:0}

/* upload area */
.rc-upload{
  width:100%;padding:2.5rem;border:2.5px dashed rgba(255,255,255,.15);border-radius:20px;
  text-align:center;cursor:pointer;transition:all .3s;background:rgba(255,255,255,.03);
  margin-bottom:.5rem;position:relative;
}
.rc-upload:hover{border-color:#e8a838;background:rgba(232,168,56,.06)}
.rc-upload.has-file{border-color:#34c78a;border-style:solid;background:rgba(52,199,138,.06)}
.rc-upload.has-file svg,.rc-upload.has-file .up-hint{display:none}
.rc-upload svg{width:40px;height:40px;stroke:rgba(255,255,255,.35);stroke-width:1.5;fill:none;margin-bottom:.6rem}
.rc-upload:hover svg{stroke:#e8a838}
.rc-upload .up-label{font-size:1rem;font-weight:600;color:rgba(255,255,255,.7);display:block}
.rc-upload .up-hint{font-size:.78rem;color:rgba(255,255,255,.35);display:block;margin-top:.2rem}
.rc-upload .up-preview{max-height:200px;border-radius:12px;margin:.8rem auto 0;display:none}
#rc-file{display:none}

/* color picker grid */
.color-pick-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:.65rem}
.color-pick{position:relative;cursor:pointer;display:block}
.color-pick input{position:absolute;opacity:0;pointer-events:none}
.color-pick-inner{
  display:flex;flex-direction:column;align-items:center;gap:.35rem;
  padding:.65rem .3rem;border:1.5px solid transparent;border-radius:14px;transition:all .25s;
}
.color-pick-inner:hover{background:rgba(232,168,56,.06)}
.color-pick input:checked+.color-pick-inner{border-color:#e8a838;background:rgba(232,168,56,.1);
  box-shadow:0 0 0 3px rgba(232,168,56,.15)}
.color-swatch{width:36px;height:36px;border-radius:50%;border:2px solid rgba(255,255,255,.15);transition:all .25s;
  box-shadow:inset 0 1px 3px rgba(0,0,0,.2)}
.color-pick input:checked+.color-pick-inner .color-swatch{border-color:#e8a838;transform:scale(1.1);
  box-shadow:0 0 0 3px rgba(232,168,56,.2),inset 0 1px 3px rgba(0,0,0,.2)}
.color-pick-name{font-size:.68rem;font-weight:500;color:rgba(255,255,255,.45);text-align:center;line-height:1.15}
.color-pick input:checked+.color-pick-inner .color-pick-name{color:#e8a838;font-weight:700}

.color-counter{font-size:.82rem;color:rgba(255,255,255,.35);margin-top:.5rem;text-align:center;
  transition:color .2s}
.color-counter.full{color:#e8a838;font-weight:600}
.color-counter.over{color:#e74c3c;font-weight:600}

/* submit */
.submit-row{text-align:center;margin:2.5rem 0 3rem}
.submit-btn{
  padding:.85rem 3rem;background:linear-gradient(135deg,#e8a838,#f0c060);
  color:#fff;border:none;border-radius:50px;font-size:1rem;font-weight:700;
  cursor:pointer;transition:all .2s;box-shadow:0 4px 16px rgba(232,168,56,.25);
  letter-spacing:.01em;
}
.submit-btn:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(232,168,56,.35)}
.submit-btn:disabled{opacity:.5;cursor:not-allowed;transform:none;box-shadow:none}

/* ── LOADING VIEW ── */
#loading-view{
  display:none;position:fixed;inset:0;z-index:200;
  background:#0b0b14;
  flex-direction:column;align-items:center;justify-content:center;
}
.load-card{
  background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:2.5rem 2.8rem;
  box-shadow:0 8px 40px rgba(0,0,0,.3);max-width:480px;width:90%;text-align:center;
}
.load-portrait{width:100px;height:100px;border-radius:50%;overflow:hidden;
  margin:0 auto 1.5rem;border:3px solid rgba(255,255,255,.1);box-shadow:0 4px 20px rgba(0,0,0,.3)}
.load-portrait img{width:100%;height:100%;object-fit:cover}
.load-prog{width:100%;height:3px;background:rgba(255,255,255,.08);border-radius:2px;margin-bottom:1.6rem;overflow:hidden}
.load-prog-fill{height:100%;background:linear-gradient(90deg,#e8a838,#f0c060);border-radius:2px;width:0%;transition:width .6s ease}
#load-stage{font-size:.95rem;color:rgba(255,255,255,.7);font-weight:500;margin-bottom:1.4rem;min-height:1.4em}
.tip-box{min-height:3.5em;display:flex;align-items:center;justify-content:center}
#load-tip{font-size:.82rem;color:rgba(255,255,255,.35);line-height:1.5;max-width:340px;font-style:italic;transition:opacity .4s}

/* ── RESULTS VIEW (dark theme, single-column centered) ── */
#results-view{
  --accent:#E8A838;--accent-hue:38;
  display:none;position:fixed;inset:0;z-index:200;
  background:#0b0b14;
  overflow-y:auto;color:#fff;
}

/* ── Top navigation bar ── */
.res-topbar{
  display:flex;align-items:center;
  padding:14px 28px;
  background:rgba(11,11,20,.85);backdrop-filter:blur(16px);
  border-bottom:1px solid rgba(255,255,255,.06);
  position:sticky;top:0;z-index:100;
}
.res-topbar-back{
  background:none;border:1px solid rgba(255,255,255,.10);color:rgba(255,255,255,.65);
  padding:7px 18px;border-radius:10px;font-size:.82rem;font-weight:600;cursor:pointer;
  transition:all .2s;display:flex;align-items:center;gap:6px;
}
.res-topbar-back:hover{background:rgba(255,255,255,.08);color:#fff;border-color:rgba(255,255,255,.18)}

/* ── Centered content column ── */
.res-inner{max-width:760px;margin:0 auto;padding:0 1.5rem 3rem}

/* ── Page heading ── */
.res-page-title{
  text-align:center;padding:36px 0 8px;
  font-family:'DM Serif Display',Georgia,serif;font-size:1.75rem;
  font-weight:400;color:#fff;letter-spacing:-.01em;
  animation:fadeDown .6s ease both;
}
@keyframes fadeDown{from{opacity:0;transform:translateY(-10px)}to{opacity:1;transform:none}}

/* ── Hero (selected image) ── */
.hero-section{
  position:relative;margin-top:24px;
  animation:slideUp .7s ease .1s both;
}
.hero-badge{
  display:inline-flex;align-items:center;gap:6px;
  padding:5px 14px;border-radius:8px;font-size:.7rem;font-weight:700;
  letter-spacing:.07em;text-transform:uppercase;
  color:#fff;background:#6b5a20;
  margin-bottom:14px;
}
.hero-img-wrap{
  position:relative;display:flex;align-items:center;justify-content:center;
  min-height:380px;
  background:rgba(255,255,255,.025);
  border-radius:24px;border:1px solid rgba(255,255,255,.07);
  padding:24px;overflow:hidden;
}
.hero-glow{
  position:absolute;top:50%;left:50%;width:480px;height:480px;border-radius:50%;
  transform:translate(-50%,-50%);
  background:radial-gradient(circle,var(--accent),transparent 70%);
  opacity:.10;filter:blur(80px);pointer-events:none;
  animation:pulse 4s ease-in-out infinite;
}
@keyframes pulse{0%,100%{opacity:.10}50%{opacity:.22}}
.hero-img-wrap img{
  width:100%;max-height:500px;object-fit:contain;border-radius:14px;
  display:block;position:relative;z-index:1;
}
.hero-color-label{
  text-align:center;margin-top:20px;padding-bottom:4px;
  font-family:'DM Serif Display',Georgia,serif;font-size:1.5rem;
  color:#fff;line-height:1.3;
}

/* ── Section divider (contained within column) ── */
.section-divider{
  display:flex;align-items:center;gap:12px;
  margin:36px 0 24px;
}
.section-divider::before,.section-divider::after{
  content:'';flex:1;height:1px;
  background:rgba(255,255,255,.07);
}
.section-divider span{
  font-size:11px;font-weight:700;color:rgba(255,255,255,.28);
  letter-spacing:.1em;text-transform:uppercase;white-space:nowrap;
}

/* ── Alt grid (responsive 1-col / 2-col) ── */
.alt-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.alt-card{
  background:rgba(255,255,255,.03);border-radius:20px;overflow:hidden;
  border:1px solid rgba(255,255,255,.06);transition:all .3s ease;cursor:pointer;
  animation:slideUp .7s ease both;
}
.alt-card:nth-child(1){animation-delay:.35s}
.alt-card:nth-child(2){animation-delay:.50s}
.alt-card:hover{border-color:rgba(255,255,255,.14);background:rgba(255,255,255,.055)}
.alt-card-img-wrap{
  position:relative;display:flex;align-items:center;justify-content:center;
  min-height:200px;padding:14px;background:rgba(255,255,255,.015);
}
.alt-card-img-wrap img{width:100%;max-height:240px;object-fit:contain;border-radius:10px;display:block}
.alt-card-body{padding:14px 16px 0}
.alt-card-name{
  font-family:'DM Serif Display',Georgia,serif;font-size:1rem;color:#fff;
  line-height:1.3;text-align:center;
}

/* ── View Full Size button ── */
.alt-card-switch{
  display:block;width:100%;padding:12px 16px;border:none;
  border-top:1px solid rgba(255,255,255,.06);
  margin-top:14px;
  background:rgba(255,255,255,.04);color:rgba(255,255,255,.45);
  font-size:.75rem;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
  cursor:pointer;transition:all .2s;
}
.alt-card-switch:hover{background:rgba(255,255,255,.09);color:rgba(255,255,255,.8)}

/* ── Loading shimmer / error states ── */
.tryon-loading{display:flex;flex-direction:column;align-items:center;justify-content:center;
  min-height:200px;width:100%;border-radius:10px;
  background:linear-gradient(110deg,rgba(255,255,255,.04) 8%,rgba(255,255,255,.08) 18%,rgba(255,255,255,.04) 33%);
  background-size:200% 100%;animation:shimmer 1.6s linear infinite}
@keyframes shimmer{to{background-position:-200% 0}}
.tryon-loading p{font-size:.82rem;color:rgba(255,255,255,.4);margin-top:.5rem}
.tryon-loading .mini-spin{width:28px;height:28px;border:3px solid rgba(255,255,255,.1);
  border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.tryon-error{display:flex;align-items:center;justify-content:center;min-height:200px;
  background:rgba(220,50,50,.08);border-radius:10px;color:#f87171;font-size:.85rem;
  padding:1.2rem;text-align:center;width:100%}

/* ── ERROR VIEW ── */
#error-view{
  display:none;position:fixed;inset:0;z-index:200;
  background:#0b0b14;
  flex-direction:column;align-items:center;justify-content:center;
}
.err-card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:2.5rem 2.8rem;
  box-shadow:0 8px 40px rgba(0,0,0,.3);max-width:420px;width:90%;text-align:center;color:#fff}

/* animations */
@keyframes slideUp{from{opacity:0;transform:translateY(30px)}to{opacity:1;transform:none}}

@media(max-width:900px){
  #form-view{padding:0 1.2rem;margin:1.5rem auto}
  .color-pick-grid{grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:.6rem}
  .color-swatch{width:42px;height:42px}
  .submit-btn{min-height:48px;width:100%;max-width:480px}
  .hero-img-wrap{padding:20px;min-height:300px}
  .alt-grid{grid-template-columns:repeat(2,1fr)}
  .res-page-title{font-size:1.4rem;padding-top:32px}
}
@media(max-width:600px){
  .topbar{padding:.6rem 1rem}
  #form-view{padding:0 .9rem;margin:1.2rem auto}
  .rc-upload{padding:1.5rem 1rem}
  .color-pick-grid{grid-template-columns:repeat(auto-fill,minmax(70px,1fr));gap:.45rem}
  .color-swatch{width:36px;height:36px}
  .color-pick-inner{padding:.45rem .15rem}
  .color-pick-name{font-size:.7rem}
  .submit-btn{min-height:52px;width:100%;border-radius:16px;font-size:1rem}
  .alt-grid{grid-template-columns:1fr}
  .res-topbar{padding:10px 14px}
  .res-page-title{font-size:1.2rem;padding-top:24px}
  .hero-img-wrap{padding:12px;min-height:240px}
  .alt-card-img-wrap{min-height:180px;padding:12px}
  .section-divider{margin:20px 0 14px}
  .load-card{padding:2rem 1.2rem}
  .tryon-loading{min-height:180px}
  .tryon-error{min-height:120px;font-size:.8rem}
  .uptip-box{max-width:92%;padding:1.5rem 1.1rem 1.2rem}
}
@media(max-width:380px){
  .color-pick-grid{grid-template-columns:repeat(auto-fill,minmax(60px,1fr));gap:.35rem}
  .color-swatch{width:30px;height:30px}
  .color-pick-name{font-size:.65rem}
  .rc-upload{padding:1.2rem .8rem}
  .hero-img-wrap{min-height:200px}
}
@media(max-height:500px) and (max-width:900px){
  .topbar{padding:.4rem 1rem;position:sticky;top:0;z-index:50}
  #form-view{margin:.8rem auto}
  .rc-upload{padding:1rem .8rem}
  .hero-img-wrap{min-height:180px;padding:10px}
  .load-card{padding:1.2rem 1rem}
}
/* upload-tip popup */
.uptip-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(6px);
  z-index:9999;display:flex;align-items:center;justify-content:center;
  opacity:0;pointer-events:none;transition:opacity .18s}
.uptip-overlay.visible{opacity:1;pointer-events:auto}
.uptip-box{background:rgba(20,20,38,.96);border:1px solid rgba(255,255,255,.1);
  border-radius:20px;padding:2rem 1.8rem 1.6rem;max-width:330px;width:90%;
  box-shadow:0 24px 60px rgba(0,0,0,.6);text-align:center}
.uptip-icon{margin-bottom:.9rem}
.uptip-icon svg{width:44px;height:44px;stroke:#e8a838;fill:none;stroke-width:1.6}
.uptip-box h3{font-size:1.05rem;font-weight:700;color:#fff;margin:0 0 .55rem}
.uptip-box p{font-size:.86rem;color:rgba(255,255,255,.55);line-height:1.6;margin:0 0 1.4rem}
.uptip-ok{background:#e8a838;color:#1a1200;border:none;border-radius:12px;
  padding:.72rem 0;font-size:.95rem;font-weight:600;cursor:pointer;width:100%;
  transition:opacity .15s}
.uptip-ok:hover{opacity:.85}
</style>
</head>
<body>

<!-- Top bar -->
<div class="topbar">
  <a href="/">&larr; Back</a>
  <span class="logo">Switch Lens Color</span>
</div>

<!-- ═══════════════ FORM VIEW ═══════════════ -->
<div id="form-view">

  <h3 class="section-title">Your Photo</h3>
  <p style="font-size:.84rem;color:rgba(255,255,255,.4);margin-bottom:1rem">Upload a photo of yourself <strong style="color:rgba(255,255,255,.6)">wearing glasses</strong>. The AI will change only the lens colour.</p>
  <div class="rc-upload" id="upload-area" onclick="openPickerWithTip('rc-file')">
    <svg viewBox="0 0 48 48"><path d="M24 32V16m0 0l-8 8m8-8l8 8" stroke-linecap="round" stroke-linejoin="round"/><rect x="6" y="6" width="36" height="36" rx="8" stroke-linecap="round"/></svg>
    <span class="up-label" id="up-label-text">Upload a Photo</span>
    <span class="up-hint">JPG, PNG, or WebP</span>
    <img class="up-preview" id="up-preview"/>
  </div>
  <input type="file" id="rc-file" accept="image/*"/>

  <h3 class="section-title">Choose 3 Lens Colors</h3>
  <p style="font-size:.84rem;color:rgba(255,255,255,.4);margin-bottom:1rem">Select exactly <strong style="color:rgba(255,255,255,.6)">3 colours</strong> you'd like to see on your lenses.</p>

  <div class="color-pick-grid" id="color-grid">
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Clear"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,rgba(255,255,255,.15),rgba(255,255,255,.35));border:2px dashed rgba(255,255,255,.4)"></div><span class="color-pick-name">Clear</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Soft Black"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#1a1a1a,#3a3a3a)"></div><span class="color-pick-name">Soft Black</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Smoke Gray"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#616161,#9e9e9e)"></div><span class="color-pick-name">Smoke Gray</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Amber Brown"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#8d6e3f,#c09050)"></div><span class="color-pick-name">Amber Brown</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Classic Green"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#2e7d32,#4caf50)"></div><span class="color-pick-name">Classic Green</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Ice Silver"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#b8c6d4,#dce6ef)"></div><span class="color-pick-name">Ice Silver</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Ocean Blue"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#1a73e8,#4fc3f7)"></div><span class="color-pick-name">Ocean Blue</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Midnight Blue"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#1a237e,#3f51b5)"></div><span class="color-pick-name">Midnight Blue</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Ruby Red"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#b71c1c,#ef5350)"></div><span class="color-pick-name">Ruby Red</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Rose Gold"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#d4a0a0,#e8b4b4)"></div><span class="color-pick-name">Rose Gold</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Aqua Teal"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#00838f,#4dd0e1)"></div><span class="color-pick-name">Aqua Teal</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Shooter Yellow"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#f9a825,#ffee58)"></div><span class="color-pick-name">Shooter Yellow</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Champagne Gold"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#c9a84c,#e8d48b)"></div><span class="color-pick-name">Champagne Gold</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Copper Bronze"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#8d4e2a,#cd7f50)"></div><span class="color-pick-name">Copper Bronze</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Deep Purple"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#6a1b9a,#ab47bc)"></div><span class="color-pick-name">Deep Purple</span></div></label>
    <label class="color-pick"><input type="checkbox" name="lens_color" value="Magenta Pink"/><div class="color-pick-inner"><div class="color-swatch" style="background:linear-gradient(135deg,#c2185b,#f06292)"></div><span class="color-pick-name">Magenta Pink</span></div></label>
  </div>
  <p class="color-counter" id="color-counter">0 of 3 selected</p>

  <div class="submit-row">
    <button class="submit-btn" id="submit-btn" disabled>Generate Recolored Lenses</button>
  </div>
</div>

<!-- ═══════════════ LOADING VIEW ═══════════════ -->
<div id="loading-view">
  <div class="load-card">
    <div class="load-portrait" id="load-portrait" style="display:none">
      <img id="load-portrait-img" src="" alt=""/>
    </div>
    <div class="load-prog"><div class="load-prog-fill" id="load-prog-fill"></div></div>
    <p id="load-stage">Uploading your photo...</p>
    <div class="tip-box"><p id="load-tip">Nano Banana Pro is crafting your new lens colours...</p></div>
  </div>
</div>

<!-- ═══════════════ RESULTS VIEW ═══════════════ -->
<div id="results-view">
  <div class="res-topbar">
    <button class="res-topbar-back" onclick="rcReset()">&larr; New Photo</button>
  </div>
  <div class="res-inner">
    <h1 class="res-page-title">Your Lens Colors</h1>

    <!-- Hero (main selected image) -->
    <div class="hero-section" id="hero-section">
      <div class="hero-badge">Selected Color</div>
      <div class="hero-img-wrap" id="hero-img-wrap">
        <div class="hero-glow"></div>
        <img id="hero-img" src="" alt="Recolored lens"/>
      </div>
      <h2 class="hero-color-label" id="hero-color-label"></h2>
    </div>

    <!-- Divider -->
    <div class="section-divider"><span>Other Colors</span></div>

    <!-- Alt cards -->
    <div class="alt-grid" id="alt-grid"></div>
  </div>
</div>

<!-- ═══════════════ ERROR VIEW ═══════════════ -->
<div id="error-view">
  <div class="err-card">
    <div style="font-size:2.4rem;margin-bottom:1rem">:/</div>
    <h2 style="font-size:1.15rem;color:#fff;margin-bottom:.6rem">Something went wrong</h2>
    <p id="error-msg" style="font-size:.88rem;color:rgba(255,255,255,.45);line-height:1.5;margin-bottom:1.5rem;word-break:break-word"></p>
    <button onclick="rcReset()" style="display:block;margin:0 auto;padding:.75rem 2.2rem;
      background:rgba(255,255,255,.08);color:#fff;border:1px solid rgba(255,255,255,.1);border-radius:50px;font-size:.92rem;font-weight:600;
      cursor:pointer">Try Again</button>
  </div>
</div>

<script>
/* ── State ── */
let chosenFile = null;
let sessionId = null;
let pollTimer = null;
let mainIdx = 0;               // which color is in the hero
let colorResults = [];          // [{name, b64, error, status}]

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
  prev.src=URL.createObjectURL(f); prev.style.display='block';
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
  document.getElementById('load-prog-fill').style.width='10%';

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

      // Update loading stage (only while loading screen is visible)
      if(!resultsShown){
        const stage=data.stage||'';
        if(stage==='uploading'){
          document.getElementById('load-stage').textContent='Uploading your photo...';
          document.getElementById('load-prog-fill').style.width='15%';
        } else if(stage==='recoloring'){
          document.getElementById('load-stage').textContent='Nano Banana Pro is recoloring your lenses...';
          document.getElementById('load-prog-fill').style.width='40%';
        } else if(stage==='primary_ready'){
          document.getElementById('load-stage').textContent='First colour ready! Finishing the rest...';
          document.getElementById('load-prog-fill').style.width='70%';
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
          renderResults();
        } else {
          updateProgressiveResults();
        }
      }

      if(data.status==='done'){
        clearInterval(pollTimer);
      }
    })
    .catch(()=>{});
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
  document.getElementById('error-msg').textContent=msg;
  showView('error-view');
}

function escHtml(s){
  const d=document.createElement('div');d.textContent=s;return d.innerHTML;
}

function rcReset(){
  sessionId=null;
  if(pollTimer) clearInterval(pollTimer);
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
  'Nano Banana Pro analyses the lens area and applies the new colour with photorealistic precision.',
  'Only the lens colour changes — frame, face, and background remain untouched.',
  'Each colour is generated independently for the most realistic result.',
  'The AI preserves natural reflections and blends the tint to match the lens curvature.',
];
let tipIdx=0;
setInterval(()=>{
  tipIdx=(tipIdx+1)%tips.length;
  const el=document.getElementById('load-tip');
  el.style.opacity=0;
  setTimeout(()=>{el.textContent=tips[tipIdx];el.style.opacity=1;},300);
},4500);
/* ── Upload-tip popup ── */
let _uptipTarget=null;
function openPickerWithTip(id){_uptipTarget=id;document.getElementById('uptip').classList.add('visible')}
function uptipOk(){document.getElementById('uptip').classList.remove('visible');if(_uptipTarget){document.getElementById(_uptipTarget).click();_uptipTarget=null}}
</script>
<!-- upload tip popup -->
<div id="uptip" class="uptip-overlay">
  <div class="uptip-box">
    <div class="uptip-icon"><svg viewBox="0 0 36 36"><path d="M4 11a3 3 0 013-3h2.5l2-3h9l2 3H25a3 3 0 013 3v15a3 3 0 01-3 3H7a3 3 0 01-3-3V11z" stroke-linecap="round" stroke-linejoin="round"/><circle cx="18" cy="19" r="5" stroke-linecap="round"/></svg></div>
    <h3>Photo Tips</h3>
    <p>For best results, upload a clear photo of yourself wearing glasses, with your face mostly visible and well-lit. Avoid blurry or poorly lit images.</p>
    <button class="uptip-ok" onclick="uptipOk()">Got It</button>
  </div>
</div>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════════
# STOREFRONT DEMO PAGE
# ══════════════════════════════════════════════════════════════════════════════

STOREFRONT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Optique — Eyewear Store</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0b0b14;color:#fff;-webkit-font-smoothing:antialiased}

/* ── Top bar ───────────────────────────────── */
.topbar{background:rgba(11,11,20,.85);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border-bottom:1px solid rgba(255,255,255,.06);padding:1rem 2rem;display:flex;align-items:center;justify-content:space-between;
  position:sticky;top:0;z-index:50}
.topbar .brand{font-family:'DM Serif Display',Georgia,serif;font-size:1.6rem;color:#fff;text-decoration:none}
.topbar .back-btn{display:inline-flex;align-items:center;gap:.4rem;font-size:.85rem;color:rgba(255,255,255,.5);text-decoration:none;
  padding:.45rem 1rem;border-radius:8px;border:1px solid rgba(255,255,255,.12);transition:all .2s}
.topbar .back-btn:hover{background:rgba(255,255,255,.06);color:rgba(255,255,255,.8)}
.topbar .powered{font-size:.72rem;color:rgba(255,255,255,.35);display:flex;align-items:center;gap:.4rem}
.topbar .powered span{background:linear-gradient(135deg,#6c63ff,#34c78a);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent;font-weight:600}

/* ── Hero ──────────────────────────────────── */
.hero{background:linear-gradient(175deg,rgba(255,255,255,.03),rgba(255,255,255,.01));
  border-bottom:1px solid rgba(255,255,255,.06);color:#fff;padding:3rem 2rem;text-align:center}
.hero h1{font-family:'DM Serif Display',Georgia,serif;font-size:2.4rem;font-weight:400;margin-bottom:.5rem}
.hero p{font-size:1rem;color:rgba(255,255,255,.45);max-width:500px;margin:0 auto}

/* ── Product grid ─────────────────────────── */
.container{max-width:1200px;margin:0 auto;padding:2rem 1.5rem 4rem}
.grid-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem}
.grid-header h2{font-size:1.1rem;font-weight:600;color:rgba(255,255,255,.85)}
.grid-header .count{font-size:.85rem;color:rgba(255,255,255,.35)}
.product-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1.5rem}

.product-card{background:rgba(255,255,255,.03);border-radius:22px;overflow:hidden;border:1px solid rgba(255,255,255,.06);
  transition:all .25s ease;display:flex;flex-direction:column}
.product-card:hover{transform:translateY(-4px);box-shadow:0 12px 32px rgba(0,0,0,.3);border-color:rgba(255,255,255,.12);
  background:rgba(255,255,255,.05)}
.product-card .img-wrap{position:relative;aspect-ratio:1/1;background:rgba(255,255,255,.02);overflow:hidden;display:flex;align-items:center;justify-content:center}
.product-card .img-wrap img{width:85%;height:85%;object-fit:contain;transition:transform .3s}
.product-card:hover .img-wrap img{transform:scale(1.05)}
.product-card .badge{position:absolute;top:.75rem;left:.75rem;font-size:.65rem;font-weight:600;padding:.25rem .6rem;
  border-radius:6px;text-transform:uppercase;letter-spacing:.04em}
.badge.men{background:rgba(67,56,202,.2);color:#818cf8}
.badge.women{background:rgba(190,24,93,.2);color:#f472b6}
.badge.unisex{background:rgba(5,150,105,.2);color:#34d399}

.product-info{padding:1rem 1.2rem;flex:1;display:flex;flex-direction:column}
.product-info .brand-name{font-size:.72rem;color:rgba(255,255,255,.35);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.2rem}
.product-info .prod-name{font-family:'DM Serif Display',Georgia,serif;font-size:.95rem;font-weight:400;color:#fff;line-height:1.35;margin-bottom:.5rem}
.product-info .prod-tags{display:flex;flex-wrap:wrap;gap:.3rem;margin-bottom:.75rem}
.product-info .prod-tag{font-size:.65rem;padding:.2rem .5rem;border-radius:6px;background:rgba(255,255,255,.06);color:rgba(255,255,255,.45);font-weight:500;
  border:1px solid rgba(255,255,255,.06)}
.product-info .price-row{display:flex;align-items:center;justify-content:space-between;margin-top:auto;padding-top:.5rem}
.product-info .price{font-family:'DM Serif Display',Georgia,serif;font-size:1.1rem;font-weight:400;color:#fff}
.product-info .currency{font-size:.75rem;color:rgba(255,255,255,.35);font-weight:400;margin-left:.15rem}

.tryon-btn{display:inline-flex;align-items:center;gap:.35rem;padding:.5rem 1rem;border:none;border-radius:10px;
  background:linear-gradient(135deg,#6c63ff,#8b7bff);color:#fff;font-size:.78rem;font-weight:600;
  cursor:pointer;transition:all .2s;white-space:nowrap}
.tryon-btn:hover{transform:scale(1.05);box-shadow:0 4px 16px rgba(108,99,255,.3)}
.tryon-btn svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2}

/* ── Modal ─────────────────────────────────── */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(10,10,20,.85);z-index:100;
  align-items:center;justify-content:center;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
.modal-overlay.active{display:flex}
.modal{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:24px;max-width:560px;width:92%;max-height:90vh;overflow-y:auto;
  box-shadow:0 24px 60px rgba(0,0,0,.4);position:relative;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px)}
.modal-close{position:absolute;top:1rem;right:1rem;width:32px;height:32px;border-radius:50%;border:none;
  background:rgba(255,255,255,.08);cursor:pointer;display:flex;align-items:center;justify-content:center;
  font-size:1.1rem;color:rgba(255,255,255,.5);transition:all .2s;z-index:2}
.modal-close:hover{background:rgba(255,255,255,.15);color:#fff}

.modal-body{padding:2rem}
.modal-body h3{font-size:1.15rem;font-weight:600;color:#fff;margin-bottom:.3rem}
.modal-body .modal-subtitle{font-size:.85rem;color:rgba(255,255,255,.4);margin-bottom:1.5rem}

.upload-zone{border:2px dashed rgba(255,255,255,.15);border-radius:14px;padding:2rem;text-align:center;cursor:pointer;
  transition:all .2s;margin-bottom:1rem;background:rgba(255,255,255,.03)}
.upload-zone:hover{border-color:#6c63ff;background:rgba(108,99,255,.06)}
.upload-zone.has-photo{border-style:solid;border-color:#34c78a;background:rgba(52,199,138,.06)}
.upload-zone svg{width:36px;height:36px;stroke:rgba(255,255,255,.3);fill:none;stroke-width:1.5;margin-bottom:.5rem}
.upload-zone p{font-size:.85rem;color:rgba(255,255,255,.4)}
.upload-zone .small{font-size:.72rem;color:rgba(255,255,255,.25);margin-top:.3rem}

.upload-preview{width:110px;height:110px;border-radius:50%;object-fit:cover;
  display:block;margin:0 auto .6rem;border:3px solid #6c63ff;box-shadow:0 4px 20px rgba(0,0,0,.3)}
.change-photo-btn{display:block;margin:.1rem auto 1.2rem;background:none;border:none;
  font-size:.78rem;color:#6c63ff;cursor:pointer;text-decoration:underline;padding:0}

.modal-submit{width:100%;padding:.85rem;border:none;border-radius:50px;
  background:linear-gradient(135deg,#6c63ff,#8b7bff);color:#fff;font-size:.95rem;font-weight:600;
  cursor:pointer;transition:all .2s;margin-top:.5rem;box-shadow:0 4px 16px rgba(108,99,255,.25)}
.modal-submit:hover{box-shadow:0 6px 20px rgba(108,99,255,.35);transform:translateY(-1px)}
.modal-submit:disabled{opacity:.4;cursor:not-allowed;box-shadow:none;transform:none}

/* ── Try-on result area inside modal ──────── */
.tryon-result{display:none;text-align:center}
.tryon-result img{display:block;max-width:100%;height:auto;border-radius:14px;margin:0 auto 1rem}
.recolor-btn{width:100%;padding:.75rem;border:none;border-radius:50px;
  background:linear-gradient(135deg,#e8a838,#f0c060);color:#1a1200;font-size:.9rem;font-weight:600;
  cursor:pointer;transition:all .2s;margin-bottom:.5rem;box-shadow:0 4px 16px rgba(232,168,56,.25)}
.recolor-btn:hover{box-shadow:0 6px 20px rgba(232,168,56,.35);transform:translateY(-1px)}

/* ── Recolor color picker inside modal ──── */
.rc-color-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.55rem;margin:1rem 0 1.2rem}
.rc-color-opt{display:flex;flex-direction:column;align-items:center;gap:.3rem;cursor:pointer;
  padding:.45rem;border-radius:12px;border:2px solid transparent;transition:all .15s;background:rgba(255,255,255,.03)}
.rc-color-opt:hover{background:rgba(255,255,255,.06)}
.rc-color-opt.selected{border-color:#e8a838;background:rgba(232,168,56,.1)}
.rc-swatch{width:30px;height:30px;border-radius:50%;box-shadow:0 2px 8px rgba(0,0,0,.3)}
.rc-cname{font-size:.65rem;color:rgba(255,255,255,.45);text-align:center;line-height:1.2}
.rc-back-btn{display:block;margin:.6rem auto 0;background:none;border:none;
  font-size:.8rem;color:rgba(255,255,255,.4);cursor:pointer;text-decoration:underline;padding:0}
.rc-back-btn:hover{color:rgba(255,255,255,.6)}
.tryon-progress{margin:2rem 0}
.tryon-progress .bar-bg{width:100%;height:3px;background:rgba(255,255,255,.08);border-radius:2px;overflow:hidden;margin-bottom:.8rem}
.tryon-progress .bar-fill{height:100%;background:linear-gradient(90deg,#34c78a,#6dd5a8);border-radius:2px;width:0%;transition:width .6s}
.tryon-progress p{font-size:.85rem;color:rgba(255,255,255,.4)}

/* ── Loading skeleton ─────────────────────── */
.skeleton{background:linear-gradient(110deg,rgba(255,255,255,.03) 8%,rgba(255,255,255,.06) 18%,rgba(255,255,255,.03) 33%);
  background-size:200% 100%;animation:shimmer 1.6s linear infinite;border-radius:8px}
@keyframes shimmer{to{background-position:-200% 0}}

/* ── Gender filter strip ──────────────────── */
.filter-strip{display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:1.5rem}
.filter-pill{padding:.45rem 1.15rem;border-radius:20px;border:1.5px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);
  font-size:.82rem;font-weight:500;cursor:pointer;transition:all .18s;color:rgba(255,255,255,.45);line-height:1}
.filter-pill:hover{border-color:rgba(255,255,255,.2);color:rgba(255,255,255,.7)}
.filter-pill.active{border-color:transparent;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.3)}
.filter-pill[data-gender="all"].active{background:rgba(255,255,255,.12)}
.filter-pill[data-gender="men"].active{background:rgba(67,56,202,.5)}
.filter-pill[data-gender="women"].active{background:rgba(190,24,93,.5)}
.filter-pill[data-gender="unisex"].active{background:rgba(5,150,105,.5)}

/* ── Tablet ── */
@media(max-width:900px){
  .topbar .powered{display:none}
  .hero{padding:2rem 1.5rem}
  .hero h1{font-size:1.8rem}
  .container{padding:1.5rem 1rem 3rem}
  .product-grid{grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1rem}
  .filter-pill{min-height:44px;min-width:44px;display:inline-flex;align-items:center;justify-content:center}
  .tryon-btn{min-height:44px;padding:.5rem 1rem}
  .modal{width:95%;max-height:90vh;overflow-y:auto;border-radius:20px}
  .modal-body{padding:1.5rem}
  .upload-zone{padding:1.5rem}
  .rc-color-grid{grid-template-columns:repeat(4,1fr);gap:.4rem}
  .rc-color-opt{padding:.35rem}
  .rc-swatch{width:30px;height:30px}
  .uptip-box{max-width:92%;padding:1.5rem 1.2rem 1.2rem}
}
/* ── Phone ── */
@media(max-width:600px){
  .topbar{padding:.6rem .8rem}
  .topbar .back-btn{font-size:.78rem;padding:.35rem .7rem}
  .topbar .brand{font-size:1.1rem}
  .hero{padding:1.5rem 1rem}
  .hero h1{font-size:1.45rem}
  .hero p{font-size:.88rem}
  .container{padding:1rem .75rem 2.5rem}
  .product-grid{grid-template-columns:repeat(2,1fr);gap:.75rem}
  .product-info{padding:.7rem}
  .product-info .prod-name{font-size:.82rem}
  .product-info .brand-name{font-size:.65rem}
  .tryon-btn{padding:.45rem .7rem;font-size:.75rem;min-height:44px;width:100%}
  .modal{width:98%;border-radius:18px;max-height:92vh;overflow-y:auto}
  .modal-body{padding:1.1rem}
  .modal-close{top:.7rem;right:.7rem;width:36px;height:36px}
  .upload-zone{padding:1.2rem}
  .rc-color-grid{grid-template-columns:repeat(4,1fr);gap:.3rem}
  .rc-swatch{width:26px;height:26px}
  .rc-cname{font-size:.6rem}
  .filter-strip{gap:.35rem}
  .filter-pill{font-size:.78rem;padding:.4rem .8rem}
}
@media(max-width:380px){
  .product-grid{grid-template-columns:repeat(2,1fr);gap:.55rem}
  .product-info .prod-name{font-size:.76rem}
  .hero h1{font-size:1.25rem}
  .rc-color-grid{grid-template-columns:repeat(4,1fr);gap:.25rem}
  .rc-swatch{width:22px;height:22px}
}
@media(max-height:500px) and (max-width:900px){
  .topbar{padding:.4rem 1rem;position:sticky;top:0;z-index:50}
  .hero{padding:1rem 1.2rem}
  .hero h1{font-size:1.3rem}
  .hero p{display:none}
  .container{padding:.75rem .75rem 2rem}
  .modal{max-height:88vh}
}
/* upload-tip popup */
.uptip-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(6px);
  z-index:9999;display:flex;align-items:center;justify-content:center;
  opacity:0;pointer-events:none;transition:opacity .18s}
.uptip-overlay.visible{opacity:1;pointer-events:auto}
.uptip-box{background:rgba(20,20,38,.96);border:1px solid rgba(255,255,255,.1);
  border-radius:20px;padding:2rem 1.8rem 1.6rem;max-width:330px;width:90%;
  box-shadow:0 24px 60px rgba(0,0,0,.6);text-align:center}
.uptip-icon{margin-bottom:.9rem}
.uptip-icon svg{width:44px;height:44px;stroke:#6c63ff;fill:none;stroke-width:1.6}
.uptip-box h3{font-size:1.05rem;font-weight:700;color:#fff;margin:0 0 .55rem}
.uptip-box p{font-size:.86rem;color:rgba(255,255,255,.55);line-height:1.6;margin:0 0 1.4rem}
.uptip-ok{background:#6c63ff;color:#fff;border:none;border-radius:12px;
  padding:.72rem 0;font-size:.95rem;font-weight:600;cursor:pointer;width:100%;
  transition:opacity .15s}
.uptip-ok:hover{opacity:.85}
</style>
</head>
<body>

<div class="topbar">
  <a href="/" class="back-btn">
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 12L6 8l4-4"/></svg>
    Back to Lenses
  </a>
  <a href="/storefront" class="brand">Optique</a>
  <div class="powered">Virtual try-on by <span>Lenses AI</span></div>
</div>

<div class="hero">
  <h1>Find Your Perfect Pair</h1>
  <p>Browse our collection and try any frame on yourself instantly with AI-powered virtual try-on.</p>
</div>

<div class="container">
  <div class="grid-header">
    <h2>All Eyewear</h2>
    <span class="count" id="product-count"></span>
  </div>
  <div class="filter-strip">
    <button class="filter-pill active" data-gender="all"    onclick="setGenderFilter('all')">All</button>
    <button class="filter-pill"        data-gender="men"    onclick="setGenderFilter('men')">Men</button>
    <button class="filter-pill"        data-gender="women"  onclick="setGenderFilter('women')">Women</button>
    <button class="filter-pill"        data-gender="unisex" onclick="setGenderFilter('unisex')">Unisex</button>
  </div>
  <div class="product-grid" id="product-grid">
    <!-- Skeleton cards while loading -->
    <div class="product-card"><div class="img-wrap skeleton" style="aspect-ratio:1/1"></div><div class="product-info"><div class="skeleton" style="height:14px;width:60%;margin-bottom:8px"></div><div class="skeleton" style="height:16px;width:90%;margin-bottom:12px"></div><div class="skeleton" style="height:28px;width:40%"></div></div></div>
    <div class="product-card"><div class="img-wrap skeleton" style="aspect-ratio:1/1"></div><div class="product-info"><div class="skeleton" style="height:14px;width:60%;margin-bottom:8px"></div><div class="skeleton" style="height:16px;width:90%;margin-bottom:12px"></div><div class="skeleton" style="height:28px;width:40%"></div></div></div>
    <div class="product-card"><div class="img-wrap skeleton" style="aspect-ratio:1/1"></div><div class="product-info"><div class="skeleton" style="height:14px;width:60%;margin-bottom:8px"></div><div class="skeleton" style="height:16px;width:90%;margin-bottom:12px"></div><div class="skeleton" style="height:28px;width:40%"></div></div></div>
    <div class="product-card"><div class="img-wrap skeleton" style="aspect-ratio:1/1"></div><div class="product-info"><div class="skeleton" style="height:14px;width:60%;margin-bottom:8px"></div><div class="skeleton" style="height:16px;width:90%;margin-bottom:12px"></div><div class="skeleton" style="height:28px;width:40%"></div></div></div>
  </div>
</div>

<!-- Try-on modal -->
<div class="modal-overlay" id="tryon-modal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal()">&times;</button>
    <div class="modal-body">
      <!-- Upload step -->
      <div id="modal-upload">
        <h3 id="modal-product-name"></h3>
        <p class="modal-subtitle">Upload a selfie to see how these frames look on you</p>
        <!-- Empty state -->
        <div id="upload-empty" class="upload-zone" onclick="openPickerWithTip('modal-file')">
          <svg viewBox="0 0 36 36"><path d="M18 8v20M8 18h20" stroke-linecap="round"/></svg>
          <p>Click to upload your photo</p>
          <p class="small">JPG, PNG or WebP</p>
        </div>
        <!-- Photo-ready state -->
        <div id="upload-ready" style="display:none;text-align:center">
          <img class="upload-preview" id="modal-preview" src="" alt="Your photo"/>
          <button type="button" class="change-photo-btn" onclick="openPickerWithTip('modal-file')">Change photo</button>
        </div>
        <input type="file" id="modal-file" accept="image/*" style="display:none"/>
        <button class="modal-submit" id="modal-go" disabled onclick="startTryon()">Try On</button>
      </div>

      <!-- Progress step -->
      <div id="modal-progress" style="display:none">
        <h3>Creating your try-on...</h3>
        <p class="modal-subtitle" id="modal-progress-product"></p>
        <div class="tryon-progress">
          <div class="bar-bg"><div class="bar-fill" id="modal-bar"></div></div>
          <p id="modal-status-text">Preparing your photo...</p>
        </div>
      </div>

      <!-- Result step -->
      <div class="tryon-result" id="modal-result">
        <h3>Here's how you look!</h3>
        <p class="modal-subtitle" id="modal-result-product"></p>
        <img id="modal-result-img" src="" alt="Virtual try-on result"/>
        <button class="recolor-btn" onclick="showRecolorPicker()">Recolor Lenses</button>
        <button class="modal-submit" onclick="resetModal()">Try Another Photo</button>
      </div>

      <!-- Recolor: pick color -->
      <div id="modal-recolor-pick" style="display:none;text-align:center">
        <h3>Recolor Lenses</h3>
        <p class="modal-subtitle">Choose a lens color</p>
        <div class="rc-color-grid" id="rc-color-grid">
          <div class="rc-color-opt" data-color="Clear" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,rgba(255,255,255,.15),rgba(255,255,255,.35));border:2px dashed rgba(255,255,255,.4)"></div><span class="rc-cname">Clear</span></div>
          <div class="rc-color-opt" data-color="Soft Black" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#1a1a1a,#3a3a3a)"></div><span class="rc-cname">Soft Black</span></div>
          <div class="rc-color-opt" data-color="Smoke Gray" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#616161,#9e9e9e)"></div><span class="rc-cname">Smoke Gray</span></div>
          <div class="rc-color-opt" data-color="Amber Brown" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#8d6e3f,#c09050)"></div><span class="rc-cname">Amber Brown</span></div>
          <div class="rc-color-opt" data-color="Classic Green" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#2e7d32,#4caf50)"></div><span class="rc-cname">Classic Green</span></div>
          <div class="rc-color-opt" data-color="Ice Silver" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#b8c6d4,#dce6ef)"></div><span class="rc-cname">Ice Silver</span></div>
          <div class="rc-color-opt" data-color="Ocean Blue" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#1a73e8,#4fc3f7)"></div><span class="rc-cname">Ocean Blue</span></div>
          <div class="rc-color-opt" data-color="Midnight Blue" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#1a237e,#3f51b5)"></div><span class="rc-cname">Midnight Blue</span></div>
          <div class="rc-color-opt" data-color="Ruby Red" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#b71c1c,#ef5350)"></div><span class="rc-cname">Ruby Red</span></div>
          <div class="rc-color-opt" data-color="Rose Gold" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#d4a0a0,#e8b4b4)"></div><span class="rc-cname">Rose Gold</span></div>
          <div class="rc-color-opt" data-color="Aqua Teal" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#00838f,#4dd0e1)"></div><span class="rc-cname">Aqua Teal</span></div>
          <div class="rc-color-opt" data-color="Shooter Yellow" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#f9a825,#ffee58)"></div><span class="rc-cname">Shooter Yellow</span></div>
          <div class="rc-color-opt" data-color="Champagne Gold" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#c9a84c,#e8d48b)"></div><span class="rc-cname">Champagne Gold</span></div>
          <div class="rc-color-opt" data-color="Copper Bronze" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#8d4e2a,#cd7f50)"></div><span class="rc-cname">Copper Bronze</span></div>
          <div class="rc-color-opt" data-color="Deep Purple" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#6a1b9a,#ab47bc)"></div><span class="rc-cname">Deep Purple</span></div>
          <div class="rc-color-opt" data-color="Magenta Pink" onclick="pickRcColor(this)"><div class="rc-swatch" style="background:linear-gradient(135deg,#c2185b,#f06292)"></div><span class="rc-cname">Magenta Pink</span></div>
        </div>
        <button class="modal-submit" id="rc-apply-btn" disabled onclick="startRecolor()">Apply Color</button>
        <button class="rc-back-btn" onclick="backToResult()">Back</button>
      </div>

      <!-- Recolor: loading -->
      <div id="modal-recolor-progress" style="display:none;text-align:center">
        <h3>Recoloring lenses...</h3>
        <p class="modal-subtitle" id="rc-progress-color"></p>
        <div class="tryon-progress">
          <div class="bar-bg"><div class="bar-fill" id="rc-bar" style="width:0%"></div></div>
          <p id="rc-status-text">Preparing...</p>
        </div>
      </div>

      <!-- Recolor: result -->
      <div id="modal-recolor-result" style="display:none;text-align:center">
        <h3>Recolored!</h3>
        <p class="modal-subtitle" id="rc-result-color"></p>
        <img id="rc-result-img" src="" alt="Recolored result" style="display:block;max-width:100%;height:auto;border-radius:14px;margin:0 auto 1rem"/>
        <button class="rc-back-btn" onclick="backToResult()">Back to Try-On</button>
      </div>

      <!-- Error step -->
      <div id="modal-error" style="display:none;text-align:center">
        <div style="font-size:2rem;margin-bottom:.8rem;color:rgba(255,255,255,.5)">:/</div>
        <h3>Something went wrong</h3>
        <p class="modal-subtitle" id="modal-error-msg"></p>
        <button class="modal-submit" onclick="resetModal()">Try Again</button>
      </div>
    </div>
  </div>
</div>

<script>
let currentProductId = null;
let currentFile = null;
let pollTimer = null;
/* Cache the user's last uploaded photo so they only upload once per session */
let cachedFile = null;
let cachedPreviewSrc = null;
let rcPollTimer = null;

/* ── Catalog + gender filter ──────────────── */
let allProducts = [];
let activeGender = 'all';

function renderProducts() {
  const list = activeGender === 'all' ? allProducts : allProducts.filter(p => p.gender === activeGender);
  const grid = document.getElementById('product-grid');
  grid.innerHTML = '';
  document.getElementById('product-count').textContent = list.length + ' products';

  list.forEach(p => {
    const genderClass = p.gender === 'men' ? 'men' : p.gender === 'women' ? 'women' : 'unisex';
    const genderLabel = p.gender === 'men' ? 'Men' : p.gender === 'women' ? 'Women' : 'Unisex';
    const imgSrc = '/api/catalog-image/' + p.image.replace('images/', '');

    const card = document.createElement('div');
    card.className = 'product-card';
    card.innerHTML = `
      <div class="img-wrap">
        <img src="${imgSrc}" alt="${p.name}" loading="lazy"/>
        <span class="badge ${genderClass}">${genderLabel}</span>
      </div>
      <div class="product-info">
        <div class="brand-name">${p.brand}</div>
        <div class="prod-name">${p.name}</div>
        <div class="prod-tags">
          <span class="prod-tag">${p.shape}</span>
          <span class="prod-tag">${p.material}</span>
          <span class="prod-tag">${p.rim_type}</span>
        </div>
        <div class="price-row">
          <div><span class="price">${p.price.toLocaleString()}</span><span class="currency">${p.currency}</span></div>
          <button class="tryon-btn" onclick="openTryon('${p.id}','${p.name.replace(/'/g,"\\'")}')">
            <svg viewBox="0 0 16 16"><circle cx="8" cy="6" r="2.5"/><path d="M3 14c0-2.761 2.239-5 5-5s5 2.239 5 5"/></svg>
            Try On
          </button>
        </div>
      </div>`;
    grid.appendChild(card);
  });
}

function setGenderFilter(gender) {
  activeGender = gender;
  document.querySelectorAll('.filter-pill').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.gender === gender)
  );
  renderProducts();
}

fetch('/api/catalog')
  .then(r => r.json())
  .then(products => {
    allProducts = products;
    renderProducts();
  });

/* ── Modal logic ──────────────────────────── */
function openTryon(productId, productName) {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  currentProductId = productId;
  document.getElementById('modal-product-name').textContent = productName;
  document.getElementById('modal-progress-product').textContent = productName;
  document.getElementById('modal-result-product').textContent = productName;

  /* Always reset to upload step for a fresh start */
  document.getElementById('modal-upload').style.display = '';
  document.getElementById('modal-progress').style.display = 'none';
  document.getElementById('modal-result').style.display = 'none';
  document.getElementById('modal-error').style.display = 'none';
  document.getElementById('modal-recolor-pick').style.display = 'none';
  document.getElementById('modal-recolor-progress').style.display = 'none';
  document.getElementById('modal-recolor-result').style.display = 'none';

  document.getElementById('tryon-modal').classList.add('active');

  /* Restore cached photo if available so they don't need to re-upload */
  if (cachedFile) {
    currentFile = cachedFile;
    showPhotoReady(cachedPreviewSrc);
  } else {
    document.getElementById('upload-empty').style.display = '';
    document.getElementById('upload-ready').style.display = 'none';
    document.getElementById('modal-go').disabled = true;
  }
}

function closeModal() {
  document.getElementById('tryon-modal').classList.remove('active');
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (rcPollTimer) { clearInterval(rcPollTimer); rcPollTimer = null; }
}

function showPhotoReady(src) {
  document.getElementById('upload-empty').style.display = 'none';
  document.getElementById('upload-ready').style.display = '';
  document.getElementById('modal-preview').src = src;
  document.getElementById('modal-go').disabled = false;
}

function resetModal() {
  document.getElementById('modal-upload').style.display = '';
  document.getElementById('modal-progress').style.display = 'none';
  document.getElementById('modal-result').style.display = 'none';
  document.getElementById('modal-error').style.display = 'none';
  document.getElementById('modal-recolor-pick').style.display = 'none';
  document.getElementById('modal-recolor-progress').style.display = 'none';
  document.getElementById('modal-recolor-result').style.display = 'none';
  if (cachedFile) {
    currentFile = cachedFile;
    showPhotoReady(cachedPreviewSrc);
  } else {
    document.getElementById('upload-empty').style.display = '';
    document.getElementById('upload-ready').style.display = 'none';
    document.getElementById('modal-go').disabled = true;
  }
}

document.getElementById('modal-file').addEventListener('change', e => {
  const f = e.target.files[0];
  if (!f) return;
  currentFile = f;
  cachedFile = f;
  const reader = new FileReader();
  reader.onload = ev => {
    cachedPreviewSrc = ev.target.result;
    showPhotoReady(cachedPreviewSrc);
  };
  reader.readAsDataURL(f);
});

function startTryon() {
  if (!currentFile || !currentProductId) return;

  document.getElementById('modal-upload').style.display = 'none';
  document.getElementById('modal-progress').style.display = '';
  document.getElementById('modal-bar').style.width = '10%';
  document.getElementById('modal-status-text').textContent = 'Uploading your photo...';

  const fd = new FormData();
  fd.append('photo', currentFile);
  fd.append('product_id', currentProductId);

  fetch('/api/storefront-tryon', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(data => {
      if (data.error) { showModalError(data.error); return; }
      pollTryon(data.session_id);
    })
    .catch(err => showModalError(err.message));
}

function pollTryon(sid) {
  document.getElementById('modal-bar').style.width = '30%';
  document.getElementById('modal-status-text').textContent = 'AI is generating your try-on...';

  pollTimer = setInterval(() => {
    fetch('/api/status/' + sid)
      .then(r => r.json())
      .then(data => {
        if (data.status === 'error') {
          clearInterval(pollTimer); pollTimer = null;
          showModalError(data.error || 'Processing failed');
          return;
        }

        const stageMap = { uploading: 20, tryon: 50, primary_ready: 85, done: 100 };
        const pct = stageMap[data.stage] || 30;
        document.getElementById('modal-bar').style.width = pct + '%';

        if (data.stage === 'tryon') {
          document.getElementById('modal-status-text').textContent = 'AI is trying on the frames...';
        }

        if (data.status === 'done' && data.opt0) {
          clearInterval(pollTimer); pollTimer = null;
          if (data.opt0.tryon_status === 'done' && data.opt0.tryon_b64) {
            showTryonResult(data.opt0.tryon_b64);
          } else {
            showModalError(data.opt0.tryon_error || 'Try-on generation failed');
          }
        }
      })
      .catch(() => {});
  }, 2000);
}

let lastTryonB64 = null;
function showTryonResult(b64) {
  lastTryonB64 = b64;
  document.getElementById('modal-progress').style.display = 'none';
  document.getElementById('modal-result').style.display = 'block';
  document.getElementById('modal-result-img').src = 'data:image/png;base64,' + b64;
}

function showModalError(msg) {
  document.getElementById('modal-upload').style.display = 'none';
  document.getElementById('modal-progress').style.display = 'none';
  document.getElementById('modal-error').style.display = '';
  document.getElementById('modal-error-msg').textContent = msg;
}

/* ── Recolor flow inside modal ────────────── */
let rcSelectedColor = null;

function showRecolorPicker() {
  document.getElementById('modal-result').style.display = 'none';
  document.getElementById('modal-recolor-pick').style.display = '';
  rcSelectedColor = null;
  document.getElementById('rc-apply-btn').disabled = true;
  document.querySelectorAll('.rc-color-opt').forEach(o => o.classList.remove('selected'));
}

function pickRcColor(el) {
  document.querySelectorAll('.rc-color-opt').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
  rcSelectedColor = el.getAttribute('data-color');
  document.getElementById('rc-apply-btn').disabled = false;
}

function backToResult() {
  document.getElementById('modal-recolor-pick').style.display = 'none';
  document.getElementById('modal-recolor-progress').style.display = 'none';
  document.getElementById('modal-recolor-result').style.display = 'none';
  document.getElementById('modal-result').style.display = 'block';
  if (rcPollTimer) { clearInterval(rcPollTimer); rcPollTimer = null; }
}

function startRecolor() {
  if (!rcSelectedColor || !lastTryonB64) return;

  document.getElementById('modal-recolor-pick').style.display = 'none';
  document.getElementById('modal-recolor-progress').style.display = '';
  document.getElementById('rc-progress-color').textContent = rcSelectedColor;
  document.getElementById('rc-bar').style.width = '10%';
  document.getElementById('rc-status-text').textContent = 'Sending image...';

  fetch('/api/storefront-recolor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_b64: lastTryonB64, color: rcSelectedColor })
  })
    .then(r => r.json())
    .then(j => {
      if (j.error) { showModalError(j.error); return; }
      document.getElementById('rc-bar').style.width = '25%';
      document.getElementById('rc-status-text').textContent = 'Recoloring lenses...';
      rcPollTimer = setInterval(() => pollRecolor(j.session_id), 2000);
    })
    .catch(() => showModalError('Network error'));
}

function pollRecolor(sid) {
  fetch('/api/storefront-recolor-status/' + sid)
    .then(r => r.json())
    .then(d => {
      if (d.status === 'error') {
        clearInterval(rcPollTimer); rcPollTimer = null;
        showModalError(d.error || 'Recolor failed');
        return;
      }
      if (d.stage === 'recoloring') {
        document.getElementById('rc-bar').style.width = '55%';
        document.getElementById('rc-status-text').textContent = 'AI is recoloring the lenses...';
      }
      if (d.status === 'done' && d.result_b64) {
        clearInterval(rcPollTimer); rcPollTimer = null;
        document.getElementById('rc-bar').style.width = '100%';
        document.getElementById('modal-recolor-progress').style.display = 'none';
        document.getElementById('modal-recolor-result').style.display = '';
        document.getElementById('rc-result-color').textContent = rcSelectedColor;
        document.getElementById('rc-result-img').src = 'data:image/png;base64,' + d.result_b64;
      }
    })
    .catch(() => {});
}

/* Close modal on overlay click */
document.getElementById('tryon-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});
/* ── Upload-tip popup ── */
let _uptipTarget=null;
function openPickerWithTip(id){_uptipTarget=id;document.getElementById('uptip').classList.add('visible')}
function uptipOk(){document.getElementById('uptip').classList.remove('visible');if(_uptipTarget){document.getElementById(_uptipTarget).click();_uptipTarget=null}}
</script>
<!-- upload tip popup -->
<div id="uptip" class="uptip-overlay">
  <div class="uptip-box">
    <div class="uptip-icon"><svg viewBox="0 0 36 36"><path d="M4 11a3 3 0 013-3h2.5l2-3h9l2 3H25a3 3 0 013 3v15a3 3 0 01-3 3H7a3 3 0 01-3-3V11z" stroke-linecap="round" stroke-linejoin="round"/><circle cx="18" cy="19" r="5" stroke-linecap="round"/></svg></div>
    <h3>Photo Tips</h3>
    <p>For best results, upload a clear selfie with your face mostly visible and well-lit. Avoid group shots, sunglasses, or blurry images.</p>
    <button class="uptip-ok" onclick="uptipOk()">Got It</button>
  </div>
</div>
</body>
</html>
"""
