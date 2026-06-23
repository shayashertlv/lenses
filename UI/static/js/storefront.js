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

const RING_R = 26;                       // ring radius in the 60x60 viewBox
const RING_C = 2 * Math.PI * RING_R;     // circumference (dash length)

function prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

/* ── Ring pace ─────────────────────────────────────────────
   A time-based fill: fast for the first ~10s, a brief slow stretch
   10-13s, then very fast 13-25s. Keyframes are [seconds, percent]
   interpolated linearly; past the last one it drifts asymptotically
   toward 99% so it never sticks and never reaches 100% until finish().
   Numbers are tunable.
     slopes (%/s): 3.0 (fast) -> 0.67 (slow) -> 5.33 (very fast) */
const RING_KEYFRAMES = [[0, 0], [10, 30], [13, 32], [25, 96]];

function RingPace(applyFn) {
  this.apply = applyFn;
  this.displayed = 0;
  this.t0 = 0;
  this.raf = null;
  const self = this;
  function pctAt(ms) {
    const t = ms / 1000, kf = RING_KEYFRAMES;
    if (t <= 0) return kf[0][1];
    for (let i = 1; i < kf.length; i++) {
      if (t <= kf[i][0]) {
        const [t0, p0] = kf[i - 1], [t1, p1] = kf[i];
        return p0 + (p1 - p0) * (t - t0) / (t1 - t0);
      }
    }
    const last = kf[kf.length - 1], dt = t - last[0];
    return Math.min(99, last[1] + (99 - last[1]) * (dt / (dt + 20)));
  }
  function tick(now) {
    const p = pctAt(now - self.t0);
    if (p > self.displayed) { self.displayed = p; self.apply(p); }
    self.raf = requestAnimationFrame(tick);
  }
  this._tick = tick;
}
RingPace.prototype.start = function () {
  this.t0 = performance.now();
  this.apply(this.displayed);
  if (!this.raf) this.raf = requestAnimationFrame(this._tick);
};
RingPace.prototype.finish = function () {
  if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  this.displayed = 100;
  this.apply(100);
};
RingPace.prototype.stop = function () {
  if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
};

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
    card.dataset.id = p.id;
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
          <button class="tryon-btn" onclick="openTryon('${p.id}','${p.name.replace(/'/g,"\\'")}','${imgSrc}')">
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
    sfInitFaceting();
  });

/* ══════════════════════════════════════════════════
   Modal: pick / confirm photo, then hit generate
   ══════════════════════════════════════════════════ */

const MODAL_STEPS = ['modal-upload', 'modal-progress', 'modal-result', 'modal-error',
  'modal-recolor-pick', 'modal-recolor-progress', 'modal-recolor-result'];

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
  document.getElementById('modal-progress-product').textContent = productName;
  document.getElementById('modal-result-product').textContent = productName;
  document.getElementById('modal-go').textContent = 'Try On';

  showModalStep('modal-upload');
  document.getElementById('tryon-modal').classList.add('active');
  restoreCachedSelfie();
}

/* Smart Fit: AI picks the single best frame from just a selfie. */
function openSmartFit() {
  storeAction = 'smartfit';
  pendingProduct = null;
  clearModalTransform();
  document.getElementById('modal-product-name').textContent = 'Smart Fit';
  document.getElementById('modal-go').textContent = 'Find my best frame';
  showModalStep('modal-upload');
  document.getElementById('tryon-modal').classList.add('active');
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

/* Closing the modal only hides the overlay — background jobs keep running. */
function closeModal() {
  document.getElementById('tryon-modal').classList.remove('active');
  document.getElementById('tryon-modal').classList.remove('closing');
  clearModalTransform();
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
    el: null,
  };
  jobs.set(job.id, job);
  return job;
}

function spawnCircle(job) {
  const isRecolor = job.kind === 'recolor';
  const isAI = (job.kind === 'smartfit' || job.kind === 'freesearch');
  const btn = document.createElement('button');
  btn.className = 'sf-circle is-loading' + (isRecolor ? ' is-recolor' : '') + (isAI ? ' is-ai' : '');
  btn.dataset.job = job.id;
  btn.setAttribute('aria-label',
    isRecolor ? 'Recoloring ' + job.productName + ' in ' + job.color
    : isAI ? (job.kind === 'smartfit' ? 'Smart Fit — finding your best frame' : 'Free Search — finding your best match')
    : 'Try-on for ' + job.productName + ' — generating');
  const thumbHtml = job.thumbSrc
    ? '<img class="sf-thumb" src="' + job.thumbSrc + '" alt=""/>'
    : (isAI
        ? '<div class="sf-thumb sf-thumb-ai"><svg viewBox="0 0 24 24" fill="none" stroke="#2a2a3a" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.7 4.3L18 9l-4.3 1.7L12 15l-1.7-4.3L6 9l4.3-1.7z"/><path d="M18.5 14l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/></svg></div>'
        : '<img class="sf-thumb" src="" alt=""/>');
  btn.innerHTML =
    '<svg class="sf-ring" viewBox="0 0 60 60">' +
      '<circle class="sf-circ-bg" cx="30" cy="30" r="' + RING_R + '"/>' +
      '<circle class="sf-circ-fg" cx="30" cy="30" r="' + RING_R + '"/>' +
    '</svg>' +
    thumbHtml +
    (isRecolor ? '<span class="sf-color-dot" style="background:' + (job.colorBg || '#fff') + '"></span>' : '') +
    '<span class="sf-badge sf-badge-check"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.2 4.2L19 7"/></svg></span>' +
    '<span class="sf-badge sf-badge-err">!</span>' +
    '<span class="sf-dismiss" aria-hidden="true">&times;</span>' +
    '<span class="sf-tooltip">Still creating…</span>';
  job.el = btn;

  /* Drive the progress ring with the accelerating RingPace */
  const ringFg = btn.querySelector('.sf-circ-fg');
  ringFg.style.strokeDasharray = RING_C.toFixed(2);
  ringFg.style.strokeDashoffset = RING_C.toFixed(2);     // start empty
  job.pace = new RingPace(function (pct) {
    ringFg.style.strokeDashoffset = (RING_C * (1 - pct / 100)).toFixed(2);
  });
  job.pace.start();

  btn.addEventListener('click', function (e) {
    if (e.target.closest('.sf-dismiss')) { e.stopPropagation(); dismissJob(job); return; }
    onCircleClick(job);
  });

  const dock = document.getElementById('tryon-dock');
  dock.appendChild(btn);
  dock.scrollLeft = dock.scrollWidth;          // keep newest visible on the mobile bar
  document.body.classList.add('dock-active');
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
        job.failCount = 0;
        if (d.status === 'error') { markJobError(job, d.error || 'Processing failed'); return; }

        if (job.kind === 'recolor') {
          if (d.status === 'done' && d.result_b64) markJobDone(job, d.result_b64);
          return;
        }
        /* Smart Fit / Free Search: the AI picks the frame, so capture its
           metadata + show its image the moment matching finishes (well before
           the try-on completes). */
        if ((job.kind === 'smartfit' || job.kind === 'freesearch') && d.opt0 && !job.picked) {
          job.picked = true;
          job.opt0 = d.opt0;
          job.productId = d.opt0.product_id || job.productId;
          job.productName = d.opt0.name || job.productName;
          if (d.opt0.product_b64) {
            job.thumbSrc = 'data:image/jpeg;base64,' + d.opt0.product_b64;
            updateCircleThumb(job);
          }
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
        if (job.failCount >= 10) markJobError(job, 'Connection lost');
      });
  }, 2000);
}

function markJobDone(job, b64) {
  if (job.pollTimer) { clearInterval(job.pollTimer); job.pollTimer = null; }
  job.status = 'done';
  job.b64 = b64;
  if (job.pace) job.pace.finish();
  job.el.style.transform = '';                 // drop any leftover fly transform
  job.el.style.opacity = '';
  job.el.classList.remove('is-loading');
  job.el.classList.add('is-done');
  job.el.setAttribute('aria-label',
    (job.kind === 'recolor' ? 'Recolor' : 'Try-on') + ' ready for ' + job.productName + ' — tap to view');
}

function markJobError(job, msg) {
  if (job.pollTimer) { clearInterval(job.pollTimer); job.pollTimer = null; }
  if (job.pace) job.pace.stop();
  job.status = 'error';
  job.el.dataset.error = msg || 'Something went wrong';
  job.el.style.transform = '';
  job.el.style.opacity = '';
  job.el.classList.remove('is-loading');
  job.el.classList.add('is-error');
  job.el.setAttribute('aria-label',
    (job.kind === 'recolor' ? 'Recolor' : 'Try-on') + ' failed for ' + job.productName + ' — tap for details');
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
  document.getElementById('modal-result-img').src = 'data:image/png;base64,' + job.b64;
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
    circleEl.style.transition = 'transform .5s cubic-bezier(.34,1.4,.64,1), opacity .3s ease';
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
  modalEl.style.transition = 'transform .4s cubic-bezier(.4,0,1,1), opacity .35s ease';
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
    modalEl.style.transition = 'transform .42s cubic-bezier(.34,1.4,.64,1), opacity .3s ease';
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
  fsRestoreSelfie();
  updateFacets();
}

function closeFreeSearch() {
  const overlay = document.getElementById('fs-overlay');
  overlay.classList.remove('active', 'closing');
  fsClearPanelTransform();
}

function fsToggleAdvanced() {
  document.getElementById('sf-advanced-section').classList.toggle('open');
  document.getElementById('sf-advanced-toggle').classList.toggle('open');
}

function fsShowSelfie(src) {
  const area = document.getElementById('sf-fs-upload');
  const prev = document.getElementById('sf-fs-preview');
  area.classList.add('has-file');
  document.getElementById('sf-fs-up-label').textContent = 'Photo ready';
  prev.src = src; prev.style.display = 'block';
  document.getElementById('sf-fs-submit').disabled = false;
  document.getElementById('sf-fs-hint').textContent = 'Pick any options (all optional), then find your match';
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
  panel.style.transition = 'transform .4s cubic-bezier(.4,0,1,1), opacity .35s ease';
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
