/* ══════════════════════════════════════════════════
   Storefront page JavaScript
   (page-specific — common.js loaded separately)
   ══════════════════════════════════════════════════ */

/* ── Per-session photo cache (upload once, reuse everywhere) ── */
let currentFile = null;
let cachedFile = null;
let cachedPreviewSrc = null;

/* ── Recolor (foreground flow, tied to whichever result is open) ── */
let rcPollTimer = null;
let sfRecolorCreep = null;
let sfRecolorRetry = null;
let rcSelectedColor = null;
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

const RING_R = 26;                       // ring radius in the 60x60 viewBox
const RING_C = 2 * Math.PI * RING_R;     // circumference (dash length)

function prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

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
  pendingProduct = { productId, productName, thumbSrc };
  clearModalTransform();

  document.getElementById('modal-product-name').textContent = productName;
  document.getElementById('modal-progress-product').textContent = productName;
  document.getElementById('modal-result-product').textContent = productName;

  showModalStep('modal-upload');
  document.getElementById('tryon-modal').classList.add('active');

  /* Restore a cached photo so the user only uploads once per session */
  if (cachedFile) {
    currentFile = cachedFile;
    showPhotoReady(cachedPreviewSrc);
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
      showPhotoReady(cachedPreviewSrc);
      return;
    } catch (e) { /* fall through to empty state */ }
  }
  showUploadEmpty();
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
  if (rcPollTimer) { clearInterval(rcPollTimer); rcPollTimer = null; }
  if (sfRecolorCreep) { sfRecolorCreep.stop(); sfRecolorCreep = null; }
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
  const job = createJob(pendingProduct);
  spawnCircle(job);

  if (prefersReducedMotion()) {
    document.getElementById('tryon-modal').classList.remove('active');
    clearModalTransform();
  } else {
    const modalEl = document.querySelector('#tryon-modal .modal');
    flyToDock(modalEl, job.el);   // circle materialises out of the window…
    collapseModal(job.el);        // …while the window shrinks into the dock
  }
  startJob(job, file);
}

/* ══════════════════════════════════════════════════
   Per-job lifecycle
   ══════════════════════════════════════════════════ */

function createJob(prod) {
  const job = {
    id: 'job_' + (++jobSeq),
    productId: prod.productId,
    productName: prod.productName,
    thumbSrc: prod.thumbSrc,
    sessionId: null,
    status: 'loading',     // 'loading' | 'done' | 'error'
    b64: null,
    pollTimer: null,
    creep: null,
    failCount: 0,
    el: null,
  };
  jobs.set(job.id, job);
  return job;
}

function spawnCircle(job) {
  const btn = document.createElement('button');
  btn.className = 'sf-circle is-loading';
  btn.dataset.job = job.id;
  btn.setAttribute('aria-label', 'Try-on for ' + job.productName + ' — generating');
  btn.innerHTML =
    '<svg class="sf-ring" viewBox="0 0 60 60">' +
      '<circle class="sf-circ-bg" cx="30" cy="30" r="' + RING_R + '"/>' +
      '<circle class="sf-circ-fg" cx="30" cy="30" r="' + RING_R + '"/>' +
    '</svg>' +
    '<img class="sf-thumb" src="' + job.thumbSrc + '" alt=""/>' +
    '<span class="sf-badge sf-badge-check"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.2 4.2L19 7"/></svg></span>' +
    '<span class="sf-badge sf-badge-err">!</span>' +
    '<span class="sf-dismiss" aria-hidden="true">&times;</span>' +
    '<span class="sf-tooltip">Still creating…</span>';
  job.el = btn;

  /* Wire the progress ring to the shared ProgressCreep (never-freeze curve) */
  const ringFg = btn.querySelector('.sf-circ-fg');
  ringFg.style.strokeDasharray = RING_C.toFixed(2);
  ringFg.style.strokeDashoffset = RING_C.toFixed(2);     // start empty
  job.creep = new ProgressCreep(ringFg, 15, function (pct, el) {
    el.style.strokeDashoffset = (RING_C * (1 - pct / 100)).toFixed(2);
  });
  job.creep.set(10);

  btn.addEventListener('click', function (e) {
    if (e.target.closest('.sf-dismiss')) { e.stopPropagation(); dismissJob(job); return; }
    onCircleClick(job);
  });

  const dock = document.getElementById('tryon-dock');
  dock.appendChild(btn);
  dock.scrollLeft = dock.scrollWidth;          // keep newest visible on the mobile bar
  document.body.classList.add('dock-active');
}

function startJob(job, file) {
  const fd = new FormData();
  fd.append('photo', file);
  fd.append('product_id', job.productId);

  fetch('/api/storefront-tryon', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(d => {
      if (d.error) { markJobError(job, d.error); return; }
      job.sessionId = d.session_id;
      pollJob(job);
    })
    .catch(err => markJobError(job, (err && err.message) || 'Network error'));
}

function pollJob(job) {
  if (job.creep) job.creep.set(30);
  job.pollTimer = setInterval(() => {
    fetch('/api/status/' + job.sessionId)
      .then(r => r.json())
      .then(d => {
        job.failCount = 0;
        if (d.status === 'error') { markJobError(job, d.error || 'Processing failed'); return; }

        const pct = ({ uploading: 20, tryon: 50, primary_ready: 85, done: 100 })[d.stage] || 30;
        if (job.creep) job.creep.set(pct);

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
  if (job.creep) job.creep.finish();
  job.el.style.transform = '';                 // drop any leftover fly transform
  job.el.style.opacity = '';
  job.el.classList.remove('is-loading');
  job.el.classList.add('is-done');
  job.el.setAttribute('aria-label', 'Try-on ready for ' + job.productName + ' — tap to view');
}

function markJobError(job, msg) {
  if (job.pollTimer) { clearInterval(job.pollTimer); job.pollTimer = null; }
  if (job.creep) job.creep.stop();
  job.status = 'error';
  job.el.dataset.error = msg || 'Something went wrong';
  job.el.style.transform = '';
  job.el.style.opacity = '';
  job.el.classList.remove('is-loading');
  job.el.classList.add('is-error');
  job.el.setAttribute('aria-label', 'Try-on failed for ' + job.productName + ' — tap for details');
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
  document.getElementById('modal-result-product').textContent = job.productName;
  document.getElementById('modal-result-img').src = 'data:image/png;base64,' + job.b64;

  showModalStep('modal-result');
  document.getElementById('tryon-modal').classList.add('active');
  if (!prefersReducedMotion()) morphFromCircle(job.el);
}

function dismissJob(job) {
  if (job.pollTimer) { clearInterval(job.pollTimer); job.pollTimer = null; }
  if (job.creep) { job.creep.stop(); job.creep = null; }
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
   Recolor flow inside the result modal (foreground)
   ══════════════════════════════════════════════════ */

function showModalErrorStep(msg) {
  showModalStep('modal-error');
  document.getElementById('modal-error-msg').textContent = msg;
}

function showRecolorPicker() {
  showModalStep('modal-recolor-pick');
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
  showModalStep('modal-result');
  if (rcPollTimer) { clearInterval(rcPollTimer); rcPollTimer = null; }
}

function startRecolor() {
  if (!rcSelectedColor || !lastTryonB64) return;

  showModalStep('modal-recolor-progress');
  document.getElementById('rc-progress-color').textContent = rcSelectedColor;
  sfRecolorCreep = new ProgressCreep(document.getElementById('rc-bar'));
  sfRecolorCreep.set(10);
  sfRecolorRetry = new PollRetry('rc-status-text', showModalErrorStep);
  document.getElementById('rc-status-text').textContent = 'Sending image...';

  fetch('/api/storefront-recolor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_b64: lastTryonB64, color: rcSelectedColor })
  })
    .then(r => r.json())
    .then(j => {
      if (j.error) { showModalErrorStep(j.error); return; }
      if (sfRecolorCreep) sfRecolorCreep.set(25);
      document.getElementById('rc-status-text').textContent = 'Recoloring lenses...';
      rcPollTimer = setInterval(() => pollRecolor(j.session_id), 2000);
    })
    .catch(() => showModalErrorStep('Network error'));
}

function pollRecolor(sid) {
  fetch('/api/storefront-recolor-status/' + sid)
    .then(r => r.json())
    .then(d => {
      if (d.status === 'error') {
        clearInterval(rcPollTimer); rcPollTimer = null;
        showModalErrorStep(d.error || 'Recolor failed');
        return;
      }
      if (sfRecolorRetry) sfRecolorRetry.reset();
      if (d.stage === 'recoloring') {
        if (sfRecolorCreep) sfRecolorCreep.set(55);
        document.getElementById('rc-status-text').textContent = 'AI is recoloring the lenses...';
      }
      if (d.status === 'done' && d.result_b64) {
        clearInterval(rcPollTimer); rcPollTimer = null;
        if (sfRecolorCreep) sfRecolorCreep.finish();
        showModalStep('modal-recolor-result');
        document.getElementById('rc-result-color').textContent = rcSelectedColor;
        document.getElementById('rc-result-img').src = 'data:image/png;base64,' + d.result_b64;
      }
    })
    .catch(() => { if (sfRecolorRetry) sfRecolorRetry.fail(); });
}

/* Close modal on overlay click (background jobs keep running) */
document.getElementById('tryon-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});
