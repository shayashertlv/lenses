/* ══════════════════════════════════════════════════
   Storefront page JavaScript
   (page-specific — common.js loaded separately)
   ══════════════════════════════════════════════════ */

/* ── Per-session photo cache (upload once, reuse everywhere) ── */
let currentFile = null;
let cachedFile = null;
let cachedPreviewSrc = null;

/* ── Recolor selection (used when spawning a recolor circle) ── */
let rcSelectedColor = null;
let rcSelectedBg = null;        // css background of the picked swatch (for the dot)
let lastTryonB64 = null;
let currentProductId = null;

/* ── Multi-job try-on dock ────────────────────────────────
   Hitting "Try On" collapses the modal into a floating circle.
   Each circle is an INDEPENDENT job (its own session, poller and
   progress ring) so the user can keep scrolling and stack several
   try-ons at once. State lives per-job, never in shared globals. */
const jobs = new Map();
let jobSeq = 0;
let pendingProduct = null;   // product whose modal is currently open
let storeAction = 'tryon';   // 'tryon' | 'smartfit' (drives the modal generate button)


function prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

/* ══════════════════════════════════════════════════
   Loading pace (Steady)
   The ring runs on a smooth, duration-aware RingTimer: it starts at 0% and
   eases toward ~90% exactly at the feature's expected time, so it's near the
   finish when the result lands — no mid-load jumps. Captions narrate the real
   backend stages above the circle, and the chosen frame appears IN the circle
   the instant matching finishes (a caption/visual beat only — the ring never
   jumps for it).
   ══════════════════════════════════════════════════ */
const KIND_CFG = {
  smartfit:   { dur: 30, label: 'Smart fit',    spawn: 'Smart fit · ~30s',          stageMsg: 'Analysing your face…' },
  freesearch: { dur: 20, label: 'Free search',  spawn: 'Finding your match · ~20s', stageMsg: 'Searching the shelf…' },
  tryon:      { dur: 20, label: 'Try-on',       spawn: 'Try-on · ~20s',             stageMsg: 'Creating your try-on…' },
  recolor:    { dur: 20, label: 'Recolour',     spawn: 'Recolouring · ~20s',        stageMsg: 'Recolouring the lenses…' },
};

/* RingTimer now lives in common.js — the landing counter paces on it too. */

/* One shared pair of screen-reader live regions for the whole dock. */
function ensureLiveRegions() {
  if (!document.getElementById('sf-live')) {
    const l = document.createElement('div');
    l.id = 'sf-live'; l.className = 'sr-only'; l.setAttribute('aria-live', 'polite');
    document.body.appendChild(l);
  }
  if (!document.getElementById('sf-alert')) {
    const a = document.createElement('div');
    a.id = 'sf-alert'; a.className = 'sr-only'; a.setAttribute('role', 'alert');
    document.body.appendChild(a);
  }
}
function announce(msg, assertive) {
  ensureLiveRegions();
  const el = document.getElementById(assertive ? 'sf-alert' : 'sf-live');
  if (el) el.textContent = msg;
}

/* Hide per-circle captions once the dock holds more than one circle, so
   captions never overlap on the tight wrap-reverse layout. */
function updateDockMulti() {
  const dock = document.getElementById('tryon-dock');
  if (dock) dock.classList.toggle('sf-multi', dock.children.length > 1);
}

/* Set a circle's caption (the ring runs independently on its own timer). opts:
   {lock} hold this caption 2.5s (the reveal beat), {force} override a lock,
   {announce}/{assertive} mirror to the live region, {transient} don't record
   it as the restore point for a connection-retry blip. */
function setJobStatus(job, msg, opts) {
  opts = opts || {};
  const now = Date.now();
  if (!opts.force && now < job.captionLockUntil) return;
  if (opts.lock) job.captionLockUntil = now + 2500;
  if (job.el && msg != null) {
    const cap = job.el.querySelector('.sf-caption');
    if (cap) cap.textContent = msg;
    if (!opts.transient) job._stableMsg = msg;
  }
  if (opts.announce && msg) announce(msg, opts.assertive);
}

/* The chosen frame appears IN the circle the moment matching finishes —
   crossfading/deblurring the AI placeholder into opt0's product image. */
function revealFrame(job, b64) {
  if (!job.el) return;
  const src = 'data:image/jpeg;base64,' + b64;
  job.thumbSrc = src;
  const old = job.el.querySelector('.sf-thumb');
  const img = document.createElement('img');
  img.className = 'sf-thumb';
  img.alt = '';
  if (prefersReducedMotion()) {
    img.src = src;
    if (old) old.replaceWith(img); else job.el.appendChild(img);
    return;
  }
  img.style.opacity = '0';
  img.style.filter = 'blur(10px)';
  img.style.transition = 'opacity .35s ease, filter .35s ease';
  img.onload = function () {
    requestAnimationFrame(function () { img.style.opacity = '1'; img.style.filter = 'none'; });
  };
  img.src = src;
  if (old) old.replaceWith(img); else job.el.appendChild(img);
}

/* ── Catalog + gender filter ──────────────── */
let allProducts = [];
let activeGender = 'all';

/* Shelf index: the label's position in the run, tabular so a column of them
   aligns. Derived from the catalogue id, which is stable. */
function shelfIndex(p) {
  const m = String(p.id || '').match(/(\d+)\s*$/);
  return m ? m[1].padStart(3, '0') : '';
}

/* The loading moment gets the world's own answer: dashed shelf labels with
   the drawn silhouette in the photo well. A shimmer gradient is the category
   default and was what this replaced. */
function renderPlaceholders(n) {
  const grid = document.getElementById('product-grid');
  if (!grid) return;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < n; i++) {
    const card = document.createElement('div');
    card.className = 'product-card is-placeholder';
    card.setAttribute('aria-hidden', 'true');
    card.innerHTML =
      '<div class="img-wrap">' + frameGlyph(['round','rectangular','square','oval','cat-eye','aviator'][i % 6]) + '</div>' +
      '<div class="product-info"><span class="ph-line"></span><span class="ph-line"></span></div>';
    frag.appendChild(card);
  }
  grid.replaceChildren(frag);
}

function renderProducts() {
  const list = activeGender === 'all' ? allProducts : allProducts.filter(p => p.gender === activeGender);
  const grid = document.getElementById('product-grid');
  grid.innerHTML = '';
  grid.removeAttribute('aria-busy');
  document.getElementById('product-count').textContent = list.length + ' products';

  const frag = document.createDocumentFragment();

  list.forEach(p => {
    const genderClass = p.gender === 'men' ? 'men' : p.gender === 'women' ? 'women' : 'unisex';
    const genderLabel = p.gender === 'men' ? 'Men' : p.gender === 'women' ? 'Women' : 'Unisex';
    const imgSrc = '/api/catalog-image/' + p.image.replace('images/', '');

    const spec = [p.material, p.rim_type].filter(Boolean).map(escHtml).join(' &middot; ');
    const spec2 = [p.shape, p.lens_size, p.lens_color].filter(Boolean).map(escHtml).join(' &middot; ');

    const card = document.createElement('div');
    card.className = 'product-card';
    card.dataset.id = p.id;
    /* Shelf-edge label: a drawn frame silhouette holds the photo well until
       the image decodes, so an unloaded card reads as filed stock rather than
       as a broken image. */
    card.innerHTML = `
      <div class="img-wrap">
        ${frameGlyph(p.shape)}
        <img src="${escHtml(imgSrc)}" alt="${escHtml(p.name)}" loading="lazy" decoding="async"/>
        <span class="badge ${genderClass} sg-label">${genderLabel}</span>
      </div>
      <div class="product-info">
        <div class="brand-name sg-display">${escHtml(p.brand)}</div>
        <div class="prod-name">${escHtml(modelName(p))}</div>
        <div class="prod-tags sg-label">
          <span class="prod-tag">${spec}</span>
          <span class="prod-tag">${spec2}</span>
        </div>
        <div class="price-row">
          <span class="price sg-price">${escHtml(p.currency === 'ILS' ? '₪' : p.currency)}${p.price.toLocaleString()}</span>
          <span class="shelf-ix sg-figure">${escHtml(shelfIndex(p))}</span>
          <button class="tryon-btn sg-label" type="button">Try on</button>
        </div>
      </div>`;
    /* Listener, not an interpolated onclick: product names carry quotes and
       ampersands that no amount of hand-escaping survives inside an attribute. */
    card.querySelector('.tryon-btn').addEventListener('click', () => openTryon(p.id, p.name, imgSrc));
    frag.appendChild(card);
  });

  grid.appendChild(frag);
}

/* Category signage carries a live count: the contract's band is
   "name left, count right", and the filter strip is the same component. */
function updateGenderCounts() {
  const n = g => g === 'all' ? allProducts.length : allProducts.filter(p => p.gender === g).length;
  document.querySelectorAll('.filter-pill').forEach(btn => {
    const g = btn.dataset.gender;
    let c = btn.querySelector('.pill-count');
    if (!c) { c = document.createElement('span'); c.className = 'pill-count sg-figure'; btn.appendChild(c); }
    c.textContent = ' ' + n(g);
  });
}

function setGenderFilter(gender) {
  activeGender = gender;
  document.querySelectorAll('.filter-pill').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.gender === gender)
  );
  renderProducts();
}

renderPlaceholders(6);

fetch('/api/catalog')
  .then(r => r.json())
  .then(products => {
    allProducts = products;
    updateGenderCounts();
    renderProducts();
    sfInitFaceting();
  });

/* ══════════════════════════════════════════════════
   Modal: pick / confirm photo, then hit generate
   ══════════════════════════════════════════════════ */

const MODAL_STEPS = ['modal-upload', 'modal-result', 'modal-error', 'modal-recolor-pick'];

/* Show exactly one modal sub-step, hide the rest. */
function showModalStep(id) {
  MODAL_STEPS.forEach(s => {
    const el = document.getElementById(s);
    if (!el) return;
    el.style.display = (s === id) ? (s === 'modal-result' ? 'block' : '') : 'none';
  });
}

function clearModalTransform() {
  const m = document.querySelector('#tryon-modal .modal');
  if (!m) return;
  m.style.transition = '';
  m.style.transform = '';
  m.style.opacity = '';
  m.style.transformOrigin = '';
}

function openTryon(productId, productName, thumbSrc) {
  storeAction = 'tryon';
  pendingProduct = { productId, productName, thumbSrc };
  clearModalTransform();

  document.getElementById('modal-product-name').textContent = productName;
  document.getElementById('modal-result-product').textContent = productName;
  document.getElementById('modal-go').textContent = 'Try on';

  showModalStep('modal-upload');
  document.getElementById('tryon-modal').classList.add('active');
  focusDialog('#tryon-modal .modal');
  restoreCachedSelfie();
}

/* Smart Fit: AI picks the single best frame from just a selfie. */
function openSmartFit() {
  storeAction = 'smartfit';
  pendingProduct = null;
  clearModalTransform();
  document.getElementById('modal-product-name').textContent = 'Smart fit';
  document.getElementById('modal-go').textContent = 'Find my best frame';
  showModalStep('modal-upload');
  document.getElementById('tryon-modal').classList.add('active');
  focusDialog('#tryon-modal .modal');
  restoreCachedSelfie();
}

/* The modal generate button dispatches by the current action. */
function modalGenerate() {
  if (storeAction === 'smartfit') startSmartFit();
  else startTryon();
}

/* Restore a cached selfie so the user only uploads once per session. */
function restoreCachedSelfie() {
  if (cachedFile) {
    currentFile = cachedFile;
    showPhotoReady(cachedPreviewSrc);
    return true;
  }
  const cached = sessionStorage.getItem('cached_portrait');
  if (cached) {
    try {
      const arr = cached.split(','), mime = arr[0].match(/:(.*?);/)[1];
      const bstr = atob(arr[1]), n = bstr.length, u8 = new Uint8Array(n);
      for (let i = 0; i < n; i++) u8[i] = bstr.charCodeAt(i);
      cachedFile = new File([u8], 'cached-photo.jpg', { type: mime });
      cachedPreviewSrc = cached;
      currentFile = cachedFile;
      showPhotoReady(cachedPreviewSrc);
      return true;
    } catch (e) { /* fall through to empty state */ }
  }
  showUploadEmpty();
  return false;
}

function showUploadEmpty() {
  document.getElementById('upload-empty').style.display = '';
  document.getElementById('upload-ready').style.display = 'none';
  document.getElementById('modal-go').disabled = true;
}

function showPhotoReady(src) {
  document.getElementById('upload-empty').style.display = 'none';
  document.getElementById('upload-ready').style.display = '';
  document.getElementById('modal-preview').src = src;
  document.getElementById('modal-go').disabled = false;
}

/* Focus bookkeeping shared by the two overlays: a dialog takes focus on
   open and hands it back on close, so keyboard users are never left
   focused on a control the overlay is covering. */
let _dialogReturnEl = null;
function focusDialog(sel) {
  _dialogReturnEl = document.activeElement;
  const el = document.querySelector(sel);
  if (el) el.focus();
}
function restoreDialogFocus() {
  if (_dialogReturnEl && _dialogReturnEl.focus) _dialogReturnEl.focus();
  _dialogReturnEl = null;
}

/* Closing the modal only hides the overlay — background jobs keep running. */
function closeModal() {
  document.getElementById('tryon-modal').classList.remove('active');
  document.getElementById('tryon-modal').classList.remove('closing');
  clearModalTransform();
  restoreDialogFocus();
}

/* "Try Another Photo" / "Try Again" — back to the upload step. */
function resetModal() {
  showModalStep('modal-upload');
  if (cachedFile) {
    currentFile = cachedFile;
    showPhotoReady(cachedPreviewSrc);
  } else {
    showUploadEmpty();
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
    try { sessionStorage.setItem('cached_portrait', ev.target.result); } catch (e) {}
  };
  reader.readAsDataURL(f);
});

/* ── Generate: collapse the modal window into a floating circle ── */
function startTryon() {
  if (!currentFile || !pendingProduct) return;
  const file = currentFile;
  const job = createJob({ ...pendingProduct, kind: 'tryon' });
  spawnCircle(job);
  collapseModalToCircle(job);
  startJob(job, file);
}

/* Smart Fit generate: collapse the modal into an AI-pick circle. */
function startSmartFit() {
  if (!currentFile) return;
  const file = currentFile;
  const job = createJob({ kind: 'smartfit', productName: 'Finding your best match', thumbSrc: null });
  spawnCircle(job);
  collapseModalToCircle(job);
  startJob(job, file);
}

/* Shared collapse: window shrinks into the dock while the circle flies in. */
function collapseModalToCircle(job) {
  if (prefersReducedMotion()) {
    document.getElementById('tryon-modal').classList.remove('active');
    clearModalTransform();
  } else {
    const modalEl = document.querySelector('#tryon-modal .modal');
    flyToDock(modalEl, job.el);   // circle materialises out of the window…
    collapseModal(job.el);        // …while the window shrinks into the dock
  }
}

/* ══════════════════════════════════════════════════
   Per-job lifecycle
   ══════════════════════════════════════════════════ */

function createJob(opts) {
  const job = {
    id: 'job_' + (++jobSeq),
    kind: opts.kind || 'tryon',          // 'tryon' | 'recolor' | 'smartfit' | 'freesearch'
    productId: opts.productId || null,
    productName: opts.productName || null,
    thumbSrc: opts.thumbSrc || null,
    sourceB64: opts.sourceB64 || null,   // recolor: image to recolor
    color: opts.color || null,           // recolor: color name
    colorBg: opts.colorBg || null,       // recolor: swatch css background (for the dot)
    prefs: opts.prefs || null,           // freesearch: preference key/values
    opt0: null,                          // smartfit/freesearch: picked product payload
    picked: false,
    sessionId: null,
    status: 'loading',     // 'loading' | 'done' | 'error'
    b64: null,
    pollTimer: null,
    pace: null,
    failCount: 0,
    dur: (KIND_CFG[opts.kind || 'tryon'] || {}).dur || 20,
    stageSeen: {},         // one-shot guard per real backend signal
    captionLockUntil: 0,   // hold the reveal caption against being overwritten
    tailTimer: null,       // single-phase "Almost there…" beat
    _stableMsg: '',        // caption to restore after a connection blip
    _retrying: false,
    el: null,
  };
  jobs.set(job.id, job);
  return job;
}

function spawnCircle(job) {
  const isRecolor = job.kind === 'recolor';
  const isAI = (job.kind === 'smartfit' || job.kind === 'freesearch');
  const btn = document.createElement('button');
  btn.className = 'sf-ticket is-loading' + (isRecolor ? ' is-recolor' : '') + (isAI ? ' is-ai' : '');
  btn.dataset.job = job.id;
  btn.setAttribute('aria-label',
    isRecolor ? 'Recolouring ' + job.productName + ' in ' + job.color
    : isAI ? (job.kind === 'smartfit' ? 'Smart fit — finding your best frame' : 'Free search — finding your best match')
    : 'Try-on for ' + job.productName + ' — generating');
  const thumbHtml = job.thumbSrc
    ? '<img class="sf-thumb" src="' + escHtml(job.thumbSrc) + '" alt=""/>'
    : (isAI
        ? '<span class="sf-thumb sf-thumb-ai"><svg viewBox="0 0 120 46" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round"><circle cx="34" cy="24" r="18"/><circle cx="86" cy="24" r="18"/><path d="M52 22h16"/></svg></span>'
        : '<span class="sf-thumb sf-thumb-ai"><svg viewBox="0 0 120 46" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round"><circle cx="34" cy="24" r="18"/><circle cx="86" cy="24" r="18"/><path d="M52 22h16"/></svg></span>');
  /* Head is one flex row: number left, then kind, badge and dismiss packed
     right. The dismiss used to be positioned absolutely over the head, so it
     sat on top of the kind label instead of beside it. */
  btn.innerHTML =
    '<span class="sf-tk-head">' +
      '<span class="sf-tk-no sg-figure">Ticket ' + String(jobs.size).padStart(2, '0') + '</span>' +
      '<span class="sf-tk-kind">' + escHtml((KIND_CFG[job.kind] || KIND_CFG.tryon).label) + '</span>' +
      '<span class="sf-badge sf-badge-err"><svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.4\" stroke-linecap=\"round\" aria-hidden=\"true\"><path d=\"M12 6v8\"/><path d=\"M12 18h.01\"/></svg></span>' +
      '<span class="sf-dismiss" aria-hidden="true"><svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\" aria-hidden=\"true\"><path d=\"M6 6l12 12M18 6L6 18\"/></svg></span>' +
    '</span>' +
    '<span class="sf-tk-body">' +
      '<span class="sf-tk-row">' + thumbHtml +
        (isRecolor ? '<span class="sf-color-dot" style="background:' + escHtml(job.colorBg || '#fff') + '"></span>' : '') +
        '<span class="sf-caption"></span>' +
      '</span>' +
      '<span class="sf-bar"><span class="sf-bar-fill"></span></span>' +
      '<span class="sf-tk-foot"><span class="sf-tk-state">Working</span><span class="sf-pct sg-figure">0%</span></span>' +
    '</span>';
  job.el = btn;

  /* Same RingTimer, same pacing maths, painting a bar instead of a ring.
     scaleX rather than width so the fill never triggers layout. */
  const fill = btn.querySelector('.sf-bar-fill');
  const pct = btn.querySelector('.sf-pct');
  job.pace = new RingTimer(function (p) {
    fill.style.transform = 'scaleX(' + (p / 100).toFixed(4) + ')';
    pct.textContent = Math.round(p) + '%';
  }, job.dur);
  job.pace.start();

  btn.addEventListener('click', function (e) {
    if (e.target.closest('.sf-dismiss')) { e.stopPropagation(); dismissJob(job); return; }
    onCircleClick(job);
  });

  const dock = document.getElementById('tryon-dock');
  dock.appendChild(btn);
  dock.scrollLeft = dock.scrollWidth;          // keep newest visible on the mobile bar
  document.body.classList.add('dock-active');
  updateDockMulti();

  /* Spawn caption + expectation (the ring already started at 0% above). */
  ensureLiveRegions();
  const cfg = KIND_CFG[job.kind] || KIND_CFG.tryon;
  setJobStatus(job, cfg.spawn, { force: true });
  /* Single-phase jobs have no mid signal — add one gentle late beat. */
  if (job.kind === 'tryon' || job.kind === 'recolor') {
    job.tailTimer = setTimeout(function () { setJobStatus(job, 'Almost there…'); }, job.dur * 600);
  }
}

/* Swap an AI-pick circle's placeholder thumb for the chosen frame image. */
function updateCircleThumb(job) {
  if (!job.el || !job.thumbSrc) return;
  const old = job.el.querySelector('.sf-thumb');
  if (!old) return;
  const img = document.createElement('img');
  img.className = 'sf-thumb';
  img.src = job.thumbSrc;
  img.alt = '';
  old.replaceWith(img);
}

function startJob(job, file) {
  let req;
  if (job.kind === 'recolor') {
    req = fetch('/api/storefront-recolor', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: job.sourceB64, color: job.color })
    });
  } else if (job.kind === 'smartfit') {
    const fd = new FormData();
    fd.append('photo', file);
    req = fetch('/api/storefront-smartfit', { method: 'POST', body: fd });
  } else if (job.kind === 'freesearch') {
    const fd = new FormData();
    fd.append('photo', file);
    if (job.prefs) Object.keys(job.prefs).forEach(k => fd.append(k, job.prefs[k]));
    req = fetch('/api/storefront-freesearch', { method: 'POST', body: fd });
  } else {
    const fd = new FormData();
    fd.append('photo', file);
    fd.append('product_id', job.productId);
    req = fetch('/api/storefront-tryon', { method: 'POST', body: fd });
  }
  req.then(r => r.json())
    .then(d => {
      if (d.error) { markJobError(job, d.error); return; }
      job.sessionId = d.session_id;
      pollJob(job);
    })
    .catch(err => markJobError(job, (err && err.message) || 'Network error'));
}

function pollJob(job) {
  const statusUrl = (job.kind === 'recolor' ? '/api/storefront-recolor-status/' : '/api/status/');
  job.pollTimer = setInterval(() => {
    fetch(statusUrl + job.sessionId)
      .then(r => r.json())
      .then(d => {
        /* Recovered from a transient connection blip — restore the caption. */
        if (job._retrying) {
          job._retrying = false;
          const cap = job.el && job.el.querySelector('.sf-caption');
          if (cap && job._stableMsg) cap.textContent = job._stableMsg;
        }
        job.failCount = 0;
        if (d.status === 'error') { markJobError(job, d.error || 'Processing failed'); return; }

        /* Recolor: single phase on its own endpoint. */
        if (job.kind === 'recolor') {
          if (d.stage === 'recoloring' && !job.stageSeen.recoloring) {
            job.stageSeen.recoloring = true;
            setJobStatus(job, KIND_CFG.recolor.stageMsg);
          }
          if (d.status === 'done' && d.result_b64) markJobDone(job, d.result_b64);
          return;
        }

        /* Smart Fit / Free Search — THE REVEAL. The AI picks the frame, and
           opt0 lands well before the try-on finishes. The instant it does, we
           call out the frame by name and show it IN the circle. The try-on
           stage co-arrives in the same tick, so we mark it seen here and let
           the render phase carry on. */
        if ((job.kind === 'smartfit' || job.kind === 'freesearch') && d.opt0 && !job.stageSeen.reveal) {
          job.stageSeen.reveal = true;
          job.stageSeen.tryon = true;
          job.picked = true;
          job.opt0 = d.opt0;
          job.productId = d.opt0.product_id || job.productId;
          job.productName = d.opt0.name || job.productName;
          /* Caption + frame reveal only — the ring keeps its smooth pace (no jump). */
          setJobStatus(job, 'Found it: ' + job.productName, { lock: true, force: true, announce: true });
          if (d.opt0.product_b64) revealFrame(job, d.opt0.product_b64);
          setTimeout(function () {
            if (job.status === 'loading') setJobStatus(job, 'Creating your try-on…', { force: true });
          }, 2600);
        }

        /* Pre-reveal stage captions (one-shot each; ring already creeping). */
        if (job.kind === 'smartfit' && d.stage === 'analyzing' && !job.stageSeen.analyzing) {
          job.stageSeen.analyzing = true;
          setJobStatus(job, KIND_CFG.smartfit.stageMsg);
        }
        if (job.kind === 'freesearch' && d.stage === 'searching' && !job.stageSeen.searching) {
          job.stageSeen.searching = true;
          setJobStatus(job, KIND_CFG.freesearch.stageMsg);
        }
        if (job.kind === 'tryon' && d.stage === 'tryon' && !job.stageSeen.tryon) {
          job.stageSeen.tryon = true;
          setJobStatus(job, KIND_CFG.tryon.stageMsg);
        }

        if (d.status === 'done' && d.opt0) {
          if (d.opt0.tryon_status === 'done' && d.opt0.tryon_b64) {
            markJobDone(job, d.opt0.tryon_b64);
          } else {
            markJobError(job, d.opt0.tryon_error || 'Try-on generation failed');
          }
        }
      })
      .catch(() => {
        job.failCount++;
        if (job.failCount === 3 && !job._retrying) {
          job._retrying = true;
          const cap = job.el && job.el.querySelector('.sf-caption');
          if (cap) cap.textContent = 'Connection issue — retrying…';
        }
        if (job.failCount >= 10) markJobError(job, 'Connection lost');
      });
  }, 2000);
}

function markJobDone(job, b64) {
  if (job.pollTimer) { clearInterval(job.pollTimer); job.pollTimer = null; }
  if (job.tailTimer) { clearTimeout(job.tailTimer); job.tailTimer = null; }
  job.status = 'done';
  job.b64 = b64;
  if (job.pace) job.pace.finish();
  job.el.style.transform = '';                 // drop any leftover fly transform
  job.el.style.opacity = '';
  job.el.classList.remove('is-loading');
  job.el.classList.add('is-done');
  setJobStatus(job, 'Ready — tap to view', { force: true });
  announce((job.productName ? job.productName + ': ' : '') + 'ready — tap to view');
  job.el.setAttribute('aria-label',
    (job.kind === 'recolor' ? 'Recolour' : 'Try-on') + ' ready for ' + job.productName + ' — tap to view');
}

function markJobError(job, msg) {
  if (job.pollTimer) { clearInterval(job.pollTimer); job.pollTimer = null; }
  if (job.tailTimer) { clearTimeout(job.tailTimer); job.tailTimer = null; }
  if (job.pace) job.pace.stop();
  job.status = 'error';
  job.el.dataset.error = msg || 'Something went wrong';
  job.el.style.transform = '';
  job.el.style.opacity = '';
  job.el.classList.remove('is-loading');
  job.el.classList.add('is-error');
  /* Error always wins the caption (force) and announces assertively. */
  setJobStatus(job, "Couldn't finish — tap for details", { force: true });
  announce((job.productName ? job.productName + ': ' : '') + "couldn't finish — tap for details", true);
  job.el.setAttribute('aria-label',
    (job.kind === 'recolor' ? 'Recolour' : 'Try-on') + ' failed for ' + job.productName + ' — tap for details');
}

function onCircleClick(job) {
  if (job.status === 'done') openJobResult(job);
  else if (job.status === 'error') showJobError(job);
  else pulseLoading(job);          // not ready yet → gentle nudge
}

/* Tapping a still-loading circle: wobble + "Still creating…" tooltip. */
function pulseLoading(job) {
  const el = job.el;
  el.classList.remove('is-wobble');
  void el.offsetWidth;             // restart the animation
  el.classList.add('is-wobble');
  el.addEventListener('animationend', function ae() {
    el.classList.remove('is-wobble');
    el.removeEventListener('animationend', ae);
  }, { once: true });
}

function showJobError(job) {
  pendingProduct = { productId: job.productId, productName: job.productName, thumbSrc: job.thumbSrc };
  showModalStep('modal-error');
  document.getElementById('modal-error-msg').textContent = job.el.dataset.error || 'Try-on failed';
  document.getElementById('tryon-modal').classList.add('active');
}

/* Tapping a ready circle: morph it open into the result modal. */
function openJobResult(job) {
  lastTryonB64 = job.b64;
  currentProductId = job.productId;
  pendingProduct = { productId: job.productId, productName: job.productName, thumbSrc: job.thumbSrc };
  let label = job.productName || '';
  if (job.opt0 && (job.opt0.price || job.opt0.price === 0)) {
    label += ' · ' + Number(job.opt0.price).toLocaleString() + ' ' + (job.opt0.currency || '');
  }
  document.getElementById('modal-result-product').textContent = label;
  const rimg = document.getElementById('modal-result-img');
  rimg.src = 'data:image/png;base64,' + job.b64;
  /* Reveal the finished try-on with a quick blur→sharp focus-in. */
  if (!prefersReducedMotion()) {
    rimg.style.filter = 'blur(14px)';
    rimg.style.transition = 'filter .35s ease';
    rimg.onload = function () { requestAnimationFrame(function () { rimg.style.filter = 'none'; }); };
  } else {
    rimg.style.filter = 'none';
  }
  const shopBtn = document.getElementById('modal-shop-btn');
  if (shopBtn) shopBtn.style.display = job.productId ? '' : 'none';

  showModalStep('modal-result');
  document.getElementById('tryon-modal').classList.add('active');
  if (!prefersReducedMotion()) morphFromCircle(job.el);
}

/* "Shop this frame" — close the result and scroll to / highlight its catalog card. */
function shopThisFrame() {
  const pid = currentProductId;
  if (!pid) return;
  closeModal();
  const find = () => document.querySelector('.product-card[data-id="' + pid + '"]');
  let card = find();
  if (!card) { setGenderFilter('all'); card = find(); }
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.add('sf-highlight');
    setTimeout(() => { if (card) card.classList.remove('sf-highlight'); }, 2200);
  }
}

function dismissJob(job) {
  if (job.pollTimer) { clearInterval(job.pollTimer); job.pollTimer = null; }
  if (job.tailTimer) { clearTimeout(job.tailTimer); job.tailTimer = null; }
  if (job.pace) { job.pace.stop(); job.pace = null; }
  const el = job.el;
  el.classList.add('is-removing');
  let removed = false;
  const remove = function () {
    if (removed) return;
    removed = true;
    if (el.parentNode) el.parentNode.removeChild(el);
    jobs.delete(job.id);
    if (document.getElementById('tryon-dock').children.length === 0) {
      document.body.classList.remove('dock-active');
    }
    updateDockMulti();
  };
  el.addEventListener('transitionend', remove, { once: true });
  setTimeout(remove, 400);         // fallback if transitionend never fires
}

/* ══════════════════════════════════════════════════
   FLIP-style animations (modal ⇄ circle)
   ══════════════════════════════════════════════════ */

/* Circle flies from the source element's box to its final dock slot. */
function flyToDock(sourceEl, circleEl) {
  const s = sourceEl.getBoundingClientRect();
  const f = circleEl.getBoundingClientRect();
  const dx = (s.left + s.width / 2) - (f.left + f.width / 2);
  const dy = (s.top + s.height / 2) - (f.top + f.height / 2);
  const startScale = Math.min(Math.max(s.width, 90) / f.width, 3);

  circleEl.style.transition = 'none';
  circleEl.style.transform = 'translate(' + dx + 'px,' + dy + 'px) scale(' + startScale + ')';
  circleEl.style.opacity = '0';
  circleEl.getBoundingClientRect();                // force reflow
  requestAnimationFrame(() => {
    circleEl.style.transition = 'transform .5s cubic-bezier(.16,1,.3,1), opacity .3s ease';
    circleEl.style.transform = 'translate(0,0) scale(1)';
    circleEl.style.opacity = '1';
  });
  circleEl.addEventListener('transitionend', function te(e) {
    if (e.propertyName !== 'transform') return;
    circleEl.style.transition = '';
    circleEl.style.transform = '';
    circleEl.style.opacity = '';
    circleEl.removeEventListener('transitionend', te);
  });
}

/* Modal window shrinks + drifts into the dock circle, then hides. */
function collapseModal(circleEl) {
  const overlay = document.getElementById('tryon-modal');
  const modalEl = document.querySelector('#tryon-modal .modal');
  const m = modalEl.getBoundingClientRect();
  const c = circleEl.getBoundingClientRect();
  const dx = (c.left + c.width / 2) - (m.left + m.width / 2);
  const dy = (c.top + c.height / 2) - (m.top + m.height / 2);

  modalEl.style.transformOrigin = 'center';
  modalEl.style.transition = 'transform .4s cubic-bezier(.7,0,.84,0), opacity .35s ease';
  modalEl.style.transform = 'translate(' + dx + 'px,' + dy + 'px) scale(.16)';
  modalEl.style.opacity = '0';
  overlay.classList.add('closing');

  let done = false;
  const finish = function () {
    if (done) return;
    done = true;
    overlay.classList.remove('active', 'closing');
    clearModalTransform();
  };
  modalEl.addEventListener('transitionend', function te(e) {
    if (e.propertyName !== 'transform') return;
    modalEl.removeEventListener('transitionend', te);
    finish();
  });
  setTimeout(finish, 600);         // fallback
}

/* Result modal grows out of the tapped circle. */
function morphFromCircle(circleEl) {
  const modalEl = document.querySelector('#tryon-modal .modal');
  const c = circleEl.getBoundingClientRect();
  const m = modalEl.getBoundingClientRect();
  const dx = (c.left + c.width / 2) - (m.left + m.width / 2);
  const dy = (c.top + c.height / 2) - (m.top + m.height / 2);
  const scale = Math.max(c.width / m.width, 0.08);

  modalEl.style.transformOrigin = 'center';
  modalEl.style.transition = 'none';
  modalEl.style.transform = 'translate(' + dx + 'px,' + dy + 'px) scale(' + scale + ')';
  modalEl.style.opacity = '0';
  modalEl.getBoundingClientRect();                // force reflow
  requestAnimationFrame(() => {
    modalEl.style.transition = 'transform .42s cubic-bezier(.16,1,.3,1), opacity .3s ease';
    modalEl.style.transform = 'translate(0,0) scale(1)';
    modalEl.style.opacity = '1';
  });
  modalEl.addEventListener('transitionend', function te(e) {
    if (e.propertyName !== 'transform') return;
    modalEl.removeEventListener('transitionend', te);
    clearModalTransform();
  });
}

/* ══════════════════════════════════════════════════
   Recolor: pick a color, then collapse into its own dock circle
   ══════════════════════════════════════════════════ */

function showRecolorPicker() {
  showModalStep('modal-recolor-pick');
  rcSelectedColor = null;
  rcSelectedBg = null;
  document.getElementById('rc-apply-btn').disabled = true;
  document.querySelectorAll('.rc-color-opt').forEach(o => o.classList.remove('selected'));
}

function pickRcColor(el) {
  document.querySelectorAll('.rc-color-opt').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
  rcSelectedColor = el.getAttribute('data-color');
  const sw = el.querySelector('.rc-swatch');
  rcSelectedBg = sw ? sw.style.background : null;
  document.getElementById('rc-apply-btn').disabled = false;
}

function backToResult() {
  showModalStep('modal-result');
}

/* Apply Color: spawn a non-blocking recolor circle (same collapse as try-on). */
function startRecolor() {
  if (!rcSelectedColor || !lastTryonB64) return;
  const job = createJob({
    kind: 'recolor',
    productId: currentProductId,
    productName: (pendingProduct && pendingProduct.productName) || 'Lenses',
    thumbSrc: (pendingProduct && pendingProduct.thumbSrc) || '',
    sourceB64: lastTryonB64,
    color: rcSelectedColor,
    colorBg: rcSelectedBg,
  });
  spawnCircle(job);

  if (prefersReducedMotion()) {
    document.getElementById('tryon-modal').classList.remove('active');
    clearModalTransform();
  } else {
    const modalEl = document.querySelector('#tryon-modal .modal');
    flyToDock(modalEl, job.el);
    collapseModal(job.el);
  }
  startJob(job);
}

/* ══════════════════════════════════════════════════
   Free Search: full options overlay -> single-result circle
   ══════════════════════════════════════════════════ */

function fsClearPanelTransform() {
  const p = document.querySelector('#fs-overlay .fs-panel');
  if (!p) return;
  p.style.transition = ''; p.style.transform = ''; p.style.opacity = ''; p.style.transformOrigin = '';
}

function openFreeSearch() {
  const overlay = document.getElementById('fs-overlay');
  fsClearPanelTransform();
  overlay.classList.add('active');
  overlay.querySelector('.fs-panel').scrollTop = 0;
  focusDialog('#fs-overlay .fs-panel');
  fsRestoreSelfie();
  updateFacets();
}

function closeFreeSearch() {
  const overlay = document.getElementById('fs-overlay');
  overlay.classList.remove('active', 'closing');
  fsClearPanelTransform();
  restoreDialogFocus();
}

function fsToggleAdvanced() {
  const sec = document.getElementById('sf-advanced-section');
  const btn = document.getElementById('sf-advanced-toggle');
  sec.classList.toggle('open');
  btn.classList.toggle('open');
  btn.setAttribute('aria-expanded', sec.classList.contains('open') ? 'true' : 'false');
}

/* Escape closes whichever overlay is up (the photo-tips popup is handled
   by common.js and wins if visible). Background jobs keep running. */
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const tip = document.getElementById('uptip');
  if (tip && tip.classList.contains('visible')) return;
  if (document.getElementById('fs-overlay').classList.contains('active')) { closeFreeSearch(); return; }
  if (document.getElementById('tryon-modal').classList.contains('active')) closeModal();
});

function fsShowSelfie(src) {
  const area = document.getElementById('sf-fs-upload');
  const prev = document.getElementById('sf-fs-preview');
  area.classList.add('has-file');
  document.getElementById('sf-fs-up-label').textContent = 'Photo ready';
  prev.src = src; prev.style.display = 'block';
  document.getElementById('sf-fs-submit').disabled = false;
  document.getElementById('sf-fs-hint').textContent = 'Pick any options — all optional — then find your match.';
}

function fsRestoreSelfie() {
  if (cachedFile && cachedPreviewSrc) {
    currentFile = cachedFile;
    fsShowSelfie(cachedPreviewSrc);
    return;
  }
  const cached = sessionStorage.getItem('cached_portrait');
  if (cached) {
    try {
      const arr = cached.split(','), mime = arr[0].match(/:(.*?);/)[1];
      const bstr = atob(arr[1]), n = bstr.length, u8 = new Uint8Array(n);
      for (let i = 0; i < n; i++) u8[i] = bstr.charCodeAt(i);
      cachedFile = new File([u8], 'cached-photo.jpg', { type: mime });
      cachedPreviewSrc = cached;
      currentFile = cachedFile;
      fsShowSelfie(cached);
      return;
    } catch (e) { /* fall through */ }
  }
  document.getElementById('sf-fs-submit').disabled = true;
  document.getElementById('sf-fs-hint').textContent = 'Upload a photo first';
}

document.getElementById('sf-fs-file').addEventListener('change', e => {
  const f = e.target.files[0];
  if (!f) return;
  currentFile = f;
  cachedFile = f;
  const reader = new FileReader();
  reader.onload = ev => {
    cachedPreviewSrc = ev.target.result;
    fsShowSelfie(ev.target.result);
    try { sessionStorage.setItem('cached_portrait', ev.target.result); } catch (e) {}
  };
  reader.readAsDataURL(f);
});

/* Collect the overlay's preference selections (only the non-empty ones). */
function fsCollectPrefs() {
  const root = document.getElementById('fs-overlay');
  const prefs = {};
  root.querySelectorAll('input[type=radio]:checked').forEach(r => {
    if (r.value) prefs[r.name] = r.value;
  });
  const price = root.querySelector('input[name=max_price]');
  if (price && price.value) prefs.max_price = price.value;
  return prefs;
}

function startFreeSearchFromOverlay() {
  const file = currentFile || cachedFile;
  if (!file) return;
  const prefs = fsCollectPrefs();
  const job = createJob({ kind: 'freesearch', productName: 'Finding your best match', thumbSrc: null, prefs });
  spawnCircle(job);
  collapseOverlayToCircle(job);
  startJob(job, file);
}

/* The overlay shrinks into the dock while the circle flies in. */
function collapseOverlayToCircle(job) {
  const overlay = document.getElementById('fs-overlay');
  if (prefersReducedMotion()) {
    overlay.classList.remove('active');
    fsClearPanelTransform();
    return;
  }
  const panel = overlay.querySelector('.fs-panel');
  flyToDock(panel, job.el);
  const c = job.el.getBoundingClientRect(), m = panel.getBoundingClientRect();
  const dx = (c.left + c.width / 2) - (m.left + m.width / 2);
  const dy = (c.top + c.height / 2) - (m.top + m.height / 2);
  overlay.classList.add('closing');
  panel.style.transformOrigin = 'center';
  panel.style.transition = 'transform .4s cubic-bezier(.7,0,.84,0), opacity .35s ease';
  panel.style.transform = 'translate(' + dx + 'px,' + dy + 'px) scale(.16)';
  panel.style.opacity = '0';
  let done = false;
  const finish = function () {
    if (done) return; done = true;
    overlay.classList.remove('active', 'closing');
    fsClearPanelTransform();
  };
  panel.addEventListener('transitionend', function te(e) {
    if (e.propertyName !== 'transform') return;
    panel.removeEventListener('transitionend', te);
    finish();
  });
  setTimeout(finish, 600);
}

/* Close the overlay when clicking the dim backdrop. */
document.getElementById('fs-overlay').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeFreeSearch();
});

/* ── Faceting: dim catalog-unavailable options (ported from free-search) ── */
const FIELD_MAP = {
  gender: 'gender', frame_shape: 'shape', lens_type: 'lens_type',
  frame_color: 'color', lens_color: 'lens_color', frame_material: 'material', rim_type: 'rim_type',
  frame_thickness: 'thickness', lens_size: 'lens_size', aesthetic: 'aesthetic', occasion: 'occasion'
};
const PRIMARY_FIELDS = ['gender', 'frame_shape', 'lens_type', 'frame_color', 'lens_color'];
const COLOR_FIELDS = new Set(['frame_color', 'lens_color']);
const _COLOR_ALIAS = {
  'dark-grey': 'black', 'dark-gray': 'black', 'jet-black': 'black',
  'havana-brown': 'tortoiseshell', 'havana': 'tortoiseshell',
  'amber': 'brown', 'cognac': 'brown', 'caramel': 'brown', 'chocolate': 'brown', 'espresso': 'brown',
  'tan': 'brown', 'bronze': 'brown', 'copper': 'brown', 'beige': 'brown', 'mocha': 'brown', 'honey': 'brown', 'chestnut': 'brown', 'walnut': 'brown',
  'gunmetal': 'silver', 'grey': 'gray', 'charcoal': 'gray', 'slate': 'gray', 'pewter': 'silver', 'chrome': 'silver', 'steel': 'silver',
  'champagne': 'gold', 'brass': 'gold', 'antique-gold': 'gold',
  'navy': 'blue', 'navy-blue': 'blue', 'dark-blue': 'blue', 'cobalt': 'blue', 'teal': 'blue', 'royal-blue': 'blue', 'sky-blue': 'blue',
  'olive': 'green', 'dark-green': 'green', 'forest-green': 'green', 'emerald': 'green', 'sage': 'green', 'khaki': 'green',
  'burgundy': 'red', 'wine': 'red', 'maroon': 'red', 'crimson': 'red',
  'coral': 'pink', 'salmon': 'pink', 'blush': 'pink', 'fuchsia': 'pink', 'magenta': 'pink',
  'ivory': 'white', 'cream': 'white', 'bone': 'white', 'off-white': 'white',
  'crystal': 'transparent', 'clear': 'transparent', 'nude': 'transparent',
  'violet': 'purple', 'lavender': 'purple', 'plum': 'purple', 'g-15-green': 'green'
};
const _ADJ_RE = /^(?:matte[-\s]|gradient[-\s]|mirrored[-\s]|polarized[-\s]|polished[-\s]|glossy[-\s]|rubber[-\s]|solid[-\s]|bright[-\s]|deep[-\s]|satin[-\s]|g-15[-\s])/i;
function normColor(raw) {
  const lc = raw.toLowerCase().trim();
  if (_COLOR_ALIAS[lc]) return _COLOR_ALIAS[lc];
  const stripped = lc.replace(_ADJ_RE, '');
  if (_COLOR_ALIAS[stripped]) return _COLOR_ALIAS[stripped];
  return stripped;
}
const _SHAPE_ALIAS = { 'butterfly': 'cat-eye', 'oversized-round': 'round', 'oversized-square': 'square', 'pilot': 'aviator', 'teardrop': 'aviator', 'flat-top': 'square', 'curved-wrap': 'wrap', 'shield': 'wrap' };
function normShape(raw) { const lc = raw.toLowerCase().trim(); return _SHAPE_ALIAS[lc] || lc; }
const _MATERIAL_ALIAS = { 'stainless-steel': 'metal', 'mixed-metal': 'metal', 'propionate': 'plastic', 'bio-injected': 'plastic', 'recycled-injected': 'plastic', 'o-matter': 'plastic', 'recycled-acetate': 'acetate', 'bio-nylon': 'nylon' };
function normMaterial(raw) { const lc = raw.toLowerCase().trim(); return _MATERIAL_ALIAS[lc] || lc; }
function catVals(product, catField) {
  const v = product[catField];
  if (Array.isArray(v)) return v.map(s => s.toLowerCase());
  if (typeof v === 'string' && v) return v.split(/,\s*/).map(s => s.toLowerCase());
  return [];
}
function productMatches(p, inputName, value) {
  const catField = FIELD_MAP[inputName];
  if (!catField || !value) return true;
  let vals = catVals(p, catField);
  let q = value.toLowerCase();
  if (inputName === 'gender') return vals.includes(q) || vals.includes('unisex');
  if (COLOR_FIELDS.has(inputName)) { vals = vals.map(normColor); q = normColor(q); }
  else if (inputName === 'frame_shape') { vals = vals.map(normShape); q = normShape(q); }
  else if (inputName === 'frame_material') { vals = vals.map(normMaterial); q = normMaterial(q); }
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
    let total = 0, live = 0;
    group.querySelectorAll('label.tile,label.swatch,span').forEach(o => {
      const input = o.querySelector('input');
      if (!input || !input.value) return;
      total++;
      if (!o.classList.contains('dimmed')) live++;
    });
    if (!total) return;
    let c = title.querySelector('.section-count');
    if (!c) {
      /* Screen readers hear "Shape, 8 of 10 available", not "Shape8 of 10":
         hidden separators join the heading text and the injected count, and
         the option group is labelled by its band. */
      const sep = document.createElement('span');
      sep.className = 'sr-only';
      sep.textContent = ', ';
      title.appendChild(sep);
      c = document.createElement('span');
      c.className = 'section-count sg-figure';
      title.appendChild(c);
      const suf = document.createElement('span');
      suf.className = 'sr-only';
      suf.textContent = ' available';
      title.appendChild(suf);
      if (!title.id) title.id = 'sf-facet-h-' + (++_sfFacetHeadingId);
      group.setAttribute('role', 'group');
      group.setAttribute('aria-labelledby', title.id);
    }
    c.textContent = live === total ? String(total) : live + ' of ' + total;
  });
}
let _sfFacetHeadingId = 0;

function updateFacets() {
  const root = document.getElementById('fs-overlay');
  if (!root || !allProducts || !allProducts.length) return;
  const filters = {};
  PRIMARY_FIELDS.forEach(name => {
    const el = root.querySelector('input[name="' + name + '"]:checked');
    if (el && el.value) filters[name] = el.value;
  });
  Object.keys(FIELD_MAP).forEach(inputName => {
    const pool = allProducts.filter(p => {
      if (!p.in_stock) return false;
      for (const f in filters) {
        if (f === inputName) continue;
        if (!productMatches(p, f, filters[f])) return false;
      }
      return true;
    });
    const catField = FIELD_MAP[inputName];
    const available = new Set();
    const isColor = COLOR_FIELDS.has(inputName), isShape = inputName === 'frame_shape', isMaterial = inputName === 'frame_material';
    pool.forEach(p => {
      catVals(p, catField).forEach(v => {
        if (isColor) available.add(normColor(v));
        else if (isShape) available.add(normShape(v));
        else if (isMaterial) available.add(normMaterial(v));
        else available.add(v);
      });
    });
    if (inputName === 'gender' && available.has('unisex')) { available.add('men'); available.add('women'); }
    root.querySelectorAll('input[name="' + inputName + '"]').forEach(radio => {
      if (!radio.value) return;
      const label = radio.closest('label') || radio.closest('span');
      if (!label) return;
      let rv = radio.value.toLowerCase();
      if (isColor) rv = normColor(rv); else if (isShape) rv = normShape(rv); else if (isMaterial) rv = normMaterial(rv);
      label.classList.toggle('dimmed', !available.has(rv));
    });
  });
  updateSignageCounts(document.getElementById('fs-overlay'));
}
function sfInitFaceting() {
  const root = document.getElementById('fs-overlay');
  if (!root) return;
  PRIMARY_FIELDS.forEach(name => {
    root.querySelectorAll('input[name="' + name + '"]').forEach(radio => {
      radio.addEventListener('change', updateFacets);
    });
  });
  updateFacets();
}

/* Close modal on overlay click (background jobs keep running) */
document.getElementById('tryon-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});
