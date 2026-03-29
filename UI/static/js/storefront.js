/* ══════════════════════════════════════════════════
   Storefront page JavaScript
   (page-specific — common.js loaded separately)
   ══════════════════════════════════════════════════ */

let currentProductId = null;
let currentFile = null;
let pollTimer = null;
/* Cache the user's last uploaded photo so they only upload once per session */
let cachedFile = null;
let cachedPreviewSrc = null;
let rcPollTimer = null;
let sfTryonCreep = null;
let sfRecolorCreep = null;
let sfTryonRetry = null;
let sfRecolorRetry = null;

/* ── Tips for storefront modal ── */
const sfTips=[
  "Virtual try-on uses AI to map frames to your face shape.",
  "The more centered your face is in the photo, the better the result.",
  "Front-facing photos with even lighting work best.",
  "Each try-on is generated uniquely for your facial geometry.",
  "Frames are scaled to match your actual face proportions.",
];
let sfTipIdx=0, sfTipTimer=null;
function sfStartTips(){sfTipIdx=0;sfShowTip();sfTipTimer=setInterval(()=>{sfTipIdx=(sfTipIdx+1)%sfTips.length;sfShowTip()},4500)}
function sfStopTips(){if(sfTipTimer)clearInterval(sfTipTimer);sfTipTimer=null}
function sfShowTip(){
  const el=document.getElementById('modal-tip');
  if(!el)return;
  el.style.opacity='0';
  setTimeout(()=>{el.textContent=sfTips[sfTipIdx];el.style.opacity='1'},350);
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
    // Try restoring from cross-page portrait cache
    const cached = sessionStorage.getItem('cached_portrait');
    if (cached) {
      const arr=cached.split(','), mime=arr[0].match(/:(.*?);/)[1];
      const bstr=atob(arr[1]), n=bstr.length, u8=new Uint8Array(n);
      for(let i=0;i<n;i++) u8[i]=bstr.charCodeAt(i);
      cachedFile=new File([u8],'cached-photo.jpg',{type:mime});
      cachedPreviewSrc=cached;
      currentFile=cachedFile;
      showPhotoReady(cachedPreviewSrc);
    } else {
      document.getElementById('upload-empty').style.display = '';
      document.getElementById('upload-ready').style.display = 'none';
      document.getElementById('modal-go').disabled = true;
    }
  }
}

function closeModal() {
  document.getElementById('tryon-modal').classList.remove('active');
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (rcPollTimer) { clearInterval(rcPollTimer); rcPollTimer = null; }
  if (sfTryonCreep) { sfTryonCreep.stop(); sfTryonCreep = null; }
  if (sfRecolorCreep) { sfRecolorCreep.stop(); sfRecolorCreep = null; }
  sfStopTips();
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
    try{sessionStorage.setItem('cached_portrait',ev.target.result)}catch(e){}
  };
  reader.readAsDataURL(f);
});

function startTryon() {
  if (!currentFile || !currentProductId) return;

  document.getElementById('modal-upload').style.display = 'none';
  document.getElementById('modal-progress').style.display = '';
  sfTryonCreep = new ProgressCreep(document.getElementById('modal-bar'));
  sfTryonCreep.set(10);
  sfTryonRetry = new PollRetry('modal-status-text', showModalError);
  document.getElementById('modal-status-text').textContent = 'Uploading your photo...';
  sfStartTips();

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
  if(sfTryonCreep) sfTryonCreep.set(30);
  document.getElementById('modal-status-text').textContent = 'AI is generating your try-on...';

  pollTimer = setInterval(() => {
    fetch('/api/status/' + sid)
      .then(r => r.json())
      .then(data => {
        if (data.status === 'error') {
          clearInterval(pollTimer); pollTimer = null;
          if(sfTryonCreep) sfTryonCreep.stop();
          showModalError(data.error || 'Processing failed');
          return;
        }
        if(sfTryonRetry) sfTryonRetry.reset();

        const stageMap = { uploading: 20, tryon: 50, primary_ready: 85, done: 100 };
        const pct = stageMap[data.stage] || 30;
        if(sfTryonCreep) sfTryonCreep.set(pct);

        if (data.stage === 'tryon') {
          document.getElementById('modal-status-text').textContent = 'AI is trying on the frames...';
        }

        if (data.status === 'done' && data.opt0) {
          clearInterval(pollTimer); pollTimer = null;
          if(sfTryonCreep) sfTryonCreep.finish();
          if (data.opt0.tryon_status === 'done' && data.opt0.tryon_b64) {
            showTryonResult(data.opt0.tryon_b64);
          } else {
            showModalError(data.opt0.tryon_error || 'Try-on generation failed');
          }
        }
      })
      .catch(() => {if(sfTryonRetry)sfTryonRetry.fail()});
  }, 2000);
}

let lastTryonB64 = null;
function showTryonResult(b64) {
  sfStopTips();
  lastTryonB64 = b64;
  document.getElementById('modal-progress').style.display = 'none';
  document.getElementById('modal-result').style.display = 'block';
  document.getElementById('modal-result-img').src = 'data:image/png;base64,' + b64;
}

function showModalError(msg) {
  sfStopTips();
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
  sfRecolorCreep = new ProgressCreep(document.getElementById('rc-bar'));
  sfRecolorCreep.set(10);
  sfRecolorRetry = new PollRetry('rc-status-text', showModalError);
  document.getElementById('rc-status-text').textContent = 'Sending image...';

  fetch('/api/storefront-recolor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_b64: lastTryonB64, color: rcSelectedColor })
  })
    .then(r => r.json())
    .then(j => {
      if (j.error) { showModalError(j.error); return; }
      if(sfRecolorCreep) sfRecolorCreep.set(25);
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
      if(sfRecolorRetry) sfRecolorRetry.reset();
      if (d.stage === 'recoloring') {
        if(sfRecolorCreep) sfRecolorCreep.set(55);
        document.getElementById('rc-status-text').textContent = 'AI is recoloring the lenses...';
      }
      if (d.status === 'done' && d.result_b64) {
        clearInterval(rcPollTimer); rcPollTimer = null;
        if(sfRecolorCreep) sfRecolorCreep.finish();
        document.getElementById('modal-recolor-progress').style.display = 'none';
        document.getElementById('modal-recolor-result').style.display = '';
        document.getElementById('rc-result-color').textContent = rcSelectedColor;
        document.getElementById('rc-result-img').src = 'data:image/png;base64,' + d.result_b64;
      }
    })
    .catch(() => {if(sfRecolorRetry)sfRecolorRetry.fail()});
}

/* Close modal on overlay click */
document.getElementById('tryon-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});
