/* ══════════════════════════════════════════════════
   Smart Fit — the dispensing counter

   The pipeline's real shape: analysis returns at roughly second eight, the
   catalogue match is local and instant just after, and the first try-on lands
   around second thirty. So everything interesting is known by second nine and
   the previous design showed a progress bar over the remaining twenty-plus
   seconds. The docket fills that gap with findings the API already returns:
   the face profile's three readings, and the chosen frame named before its
   image exists. The model's prose is not shown on either screen.
   ══════════════════════════════════════════════════ */

let sfSid = null, sfPoll = null, sfLastRendered = '';
let sfPace = null, sfRetry = null;
let sfProfileDone = false, sfFrameNamed = false;
let sfLastSummary = null;

const SF_DURATION = 30;   // seconds, matches the storefront's KIND_CFG.smartfit

/* ── Pace: one monotonic timer, same curve the storefront dock uses. ── */
function sfStartPace() {
  const fill = document.getElementById('sf-prog');
  const pct = document.getElementById('sf-pct');
  sfPace = new RingTimer(function (p) {
    fill.style.transform = 'scaleX(' + (p / 100).toFixed(4) + ')';
    pct.textContent = Math.round(p) + '%';
  }, SF_DURATION);
  sfPace.start();
}


function sfShow(view) {
  ['sf-processing', 'sf-results', 'sf-error'].forEach(id => {
    document.getElementById(id).style.display = 'none';
  });
  if (view) document.getElementById(view).style.display = 'flex';
  document.body.classList.toggle('sf-open', !!view);
}

/* The three rows the face read comes back with. Laid in the moment the wait
   starts so the docket opens as a form waiting on its values rather than an
   empty card; sfRenderProfile overwrites them with the real thing. */
const SF_PROFILE_ROWS = ['Face shape', 'Geometry', 'Colouring'];

/* One glyph per reading. Drawn, stroke-only, in the same language as the rest
   of the product's icons — an outline of a face, a jaw, and a tone. */
const SF_PROFILE_ICONS = {
  'Face shape': '<ellipse cx="12" cy="11" rx="6.5" ry="8"/><path d="M8.6 18.6c.9 1 2 1.6 3.4 1.6s2.5-.6 3.4-1.6"/>',
  'Geometry': '<path d="M6 4v6.5a6 6 0 0 0 12 0V4"/><path d="M9 14.5h6"/>',
  'Colouring': '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none"/>',
};

function sfProfileRow(label, valueHtml) {
  const glyph = SF_PROFILE_ICONS[label] || '';
  return '<div class="prow"><dt>' +
    (glyph ? '<svg class="prow-icon" viewBox="0 0 24 24" aria-hidden="true">' + glyph + '</svg>' : '') +
    '<span>' + escHtml(label) + '</span></dt><dd>' + valueHtml + '</dd></div>';
}

function sfRenderProfileSkeleton() {
  const dl = document.getElementById('sf-profile');
  /* One bar per row, because one value per row is all that lands now — the
     placeholder should be the shape of the thing it is standing in for. */
  dl.innerHTML = SF_PROFILE_ROWS
    .map(k => sfProfileRow(k, '<span class="skel skel-val"></span>'))
    .join('');
  dl.classList.add('in');
}

function sfRenderProfile(fs) {
  if (sfProfileDone || !fs || !fs.face_shape) return;
  sfProfileDone = true;

  const dl = document.getElementById('sf-profile');
  const row = (k, v, extra) => sfProfileRow(k, v + (extra || ''));

  /* Readings only. The model returns a description with each one and an essay
     alongside; none of it is shown any more, on either screen. */
  let html = '';
  if (fs.face_shape) html += row('Face shape', escHtml(fs.face_shape));
  if (fs.key_geometry) html += row('Geometry', escHtml(fs.key_geometry));
  if (fs.color_profile) html += row('Colouring', escHtml(fs.color_profile) + sfColourDots(fs));
  dl.innerHTML = html;
  dl.classList.add('in');
}

/* The hair / eye / skin swatches — the colouring reading, shown rather than
   described. Used by the docket and the results panel alike. */
function sfColourDots(fs) {
  const dots = ['hair_color_hex', 'eye_color_hex', 'skin_tone_hex']
    .filter(k => fs[k])
    .map(k => '<span class="cdot" style="background:' + escHtml(fs[k]) + '" title="' +
      escHtml(k.replace(/_color_hex|_tone_hex/, '')) + '"></span>').join('');
  return dots ? '<span class="cdots">' + dots + '</span>' : '';
}

/* ── The shelf slot ──
   The match lands at about second nine, and until it did this column was a
   third of the docket holding nothing at all — the readings beside it had a
   skeleton and it did not. It gets the same treatment: the slot is laid in
   labelled and pending the moment the wait starts, and the frame replaces its
   placeholders in place. The drawn silhouette stands in for the photo, which is
   what it does under every unloaded shelf-edge label in the product. */
function sfRenderFrameSkeleton() {
  const box = document.getElementById('sf-frame');
  box.innerHTML =
    '<span class="pulled-label">Pulled from the shelf</span>' +
    '<span class="pulled-img">' + frameGlyph('') + '</span>' +
    '<span class="pulled-brand"><span class="skel skel-brand"></span></span>' +
    '<span class="pulled-name"><span class="skel skel-name"></span></span>' +
    '<span class="pulled-spec"><span class="skel skel-spec"></span></span>' +
    '<span class="pulled-price"><span class="skel skel-price"></span></span>';
  box.classList.add('in');
}

/* ── Name the chosen frame before its render exists. ── */
function sfNameFrame(o) {
  if (sfFrameNamed || !o || !o.name) return;
  sfFrameNamed = true;
  const box = document.getElementById('sf-frame');
  const spec = [o.material, o.color].filter(Boolean).map(escHtml).join(' &middot; ');
  box.innerHTML =
    '<span class="pulled-label">Pulled from the shelf</span>' +
    (o.product_b64
      ? '<img class="pulled-img" src="data:image/jpeg;base64,' + o.product_b64 + '" alt=""/>'
      : '<span class="pulled-img">' + frameGlyph(o.shape) + '</span>') +
    '<span class="pulled-brand">' + escHtml(o.brand || '') + '</span>' +
    '<span class="pulled-name">' + escHtml(modelName(o)) + '</span>' +
    (spec ? '<span class="pulled-spec">' + spec + '</span>' : '') +
    '<span class="pulled-price"><span class="amount">' + sfFmtPrice(o) + '</span></span>';
  box.classList.add('in');
  announceSf('Found it. ' + (o.brand ? o.brand + ' ' : '') + o.name);
}

function announceSf(msg) {
  let el = document.getElementById('sf-live');
  if (!el) {
    el = document.createElement('div');
    el.id = 'sf-live'; el.className = 'sr-only'; el.setAttribute('aria-live', 'polite');
    document.body.appendChild(el);
  }
  el.textContent = msg;
}

/* ── Smart fit intake ──
   Choosing a file used to start the whole run on the spot. It is collected in
   the card now and the run begins when the visitor says so, which is what the
   storefront does and what makes the thirty-second wait something they asked
   for rather than something that happened to them. */
let sfPendingFile = null;
let sfPendingPreview = null;

function sfSetModalHint(msg) {
  const el = document.getElementById('sf-modal-hint');
  if (el) el.textContent = msg || '';
}

function sfShowUploadEmpty() {
  document.getElementById('sf-upload-empty').style.display = '';
  document.getElementById('sf-upload-ready').style.display = 'none';
  document.getElementById('sf-modal-go').disabled = true;
  sfSetModalHint('Upload a photo first');
}

function sfShowPhotoReady(src) {
  document.getElementById('sf-upload-empty').style.display = 'none';
  document.getElementById('sf-upload-ready').style.display = '';
  document.getElementById('sf-modal-preview').src = src;
  document.getElementById('sf-modal-go').disabled = false;
  sfSetModalHint('We read your face, then pull three frames — about 30 seconds.');
}

/* A phone can discard this page while its camera is open and reload it on the
   way back, which drops the visitor on the hero with no sign of what they were
   doing — the reported "nothing happens, you're back in landing". The card
   records that it was open so a reload returns to it, and the last photo is
   rebuilt from the cached preview so it does not have to be taken twice. */
function sfMarkIntake(open) {
  try {
    if (open) sessionStorage.setItem('sf_intake_open', '1');
    else sessionStorage.removeItem('sf_intake_open');
  } catch (e) {}
}

function sfRestoreCachedPhoto() {
  let cached = null;
  try { cached = sessionStorage.getItem('cached_portrait'); } catch (e) {}
  if (!cached || cached.indexOf('data:image') !== 0) return false;
  try {
    const mime = cached.slice(5, cached.indexOf(';'));
    const bstr = atob(cached.slice(cached.indexOf(',') + 1));
    const u8 = new Uint8Array(bstr.length);
    for (let i = 0; i < bstr.length; i++) u8[i] = bstr.charCodeAt(i);
    sfPendingFile = new File([u8], 'photo.jpg', { type: mime });
    sfPendingPreview = cached;
    sfShowPhotoReady(cached);
    return true;
  } catch (e) { return false; }
}

function openSmartFit() {
  const overlay = document.getElementById('sf-modal');
  if (sfPendingPreview) sfShowPhotoReady(sfPendingPreview);
  else if (!sfRestoreCachedPhoto()) sfShowUploadEmpty();
  overlay.classList.add('active');
  sfMarkIntake(true);
  const panel = overlay.querySelector('.modal');
  if (panel) panel.focus();
}

function closeSmartFit() {
  document.getElementById('sf-modal').classList.remove('active');
  sfMarkIntake(false);
}

/* A phone camera hands over a 3-12MB image. Held as a base64 data URL that is
   a third bigger again, kept in a variable, painted into an <img> and pushed at
   a 5MB sessionStorage quota that silently rejects it. The preview is scaled
   down first — the upload still sends the original file untouched. */
function sfPreviewFromFile(file, done) {
  const reader = new FileReader();
  reader.onload = ev => {
    const full = ev.target.result;
    const img = new Image();
    img.onload = () => {
      const MAX = 900;
      const scale = Math.min(1, MAX / Math.max(img.naturalWidth, img.naturalHeight));
      if (scale === 1) return done(full);
      try {
        const cv = document.createElement('canvas');
        cv.width = Math.round(img.naturalWidth * scale);
        cv.height = Math.round(img.naturalHeight * scale);
        cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
        done(cv.toDataURL('image/jpeg', 0.82));
      } catch (e) { done(full); }
    };
    img.onerror = () => done(full);
    img.src = full;
  };
  reader.readAsDataURL(file);
}

document.getElementById('sf-file').addEventListener('change', e => {
  const f = e.target.files[0];
  if (!f) return;
  sfPendingFile = f;
  sfPreviewFromFile(f, src => {
    sfPendingPreview = src;
    sfShowPhotoReady(src);
    try { sessionStorage.setItem('cached_portrait', src); } catch (err) {}
  });
});

/* Reopened after a reload that happened while the picker was up. */
try {
  if (sessionStorage.getItem('sf_intake_open')) openSmartFit();
} catch (e) {}

async function sfStartFromModal() {
  const f = sfPendingFile;
  if (!f) return;
  closeSmartFit();
  sfResetState();
  sfRenderProfileSkeleton();
  sfRenderFrameSkeleton();
  sfShow('sf-processing');
  sfStartPace();
  document.getElementById('sf-stage').textContent = 'Reading your face';
  sfRetry = new PollRetry('sf-stage', sfShowError);

  if (sfPendingPreview) {
    const wrap = document.getElementById('sf-portrait-wrap');
    const img = document.createElement('img');
    img.id = 'sf-portrait-img';
    img.alt = 'The photo you handed over';
    img.src = sfPendingPreview;
    wrap.replaceChildren(img);
    wrap.hidden = false;
  }

  const fd = new FormData();
  fd.append('photo', f);
  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const j = await r.json();
    if (j.error) { sfShowError(j.error); return; }
    sfSid = j.session_id;
    sfPollStatus();
  } catch (err) { sfShowError('Upload failed: ' + err.message); }
}

const SF_STAGE_MSG = {
  uploading: 'Handing over your photo',
  analyzing: 'Reading your face',
  matching: 'Searching the shelf',
  tryon: 'Rendering you in it',
  primary_ready: 'First one is ready',
  done: 'All three dispensed',
};

function sfPollStatus() {
  if (!sfSid) return;
  fetch('/api/status/' + sfSid).then(r => r.json()).then(d => {
    if (sfRetry) sfRetry.reset();
    if (d.status === 'error') { sfShowError(d.error || 'Unknown error'); return; }

    document.getElementById('sf-stage').textContent = SF_STAGE_MSG[d.stage] || 'Working';
    if (d.face_summary) { sfLastSummary = d.face_summary; sfRenderProfile(d.face_summary); }
    if (d.opt0) sfNameFrame(d.opt0);

    const ready = d.num_options > 0 && (d.stage === 'primary_ready' || d.stage === 'done');
    if (ready || d.status === 'done') {
      /* sfRender swaps the processing view away, so it waits for the bar to
         actually reach 100 — otherwise the landing animates onto a screen
         already gone. */
      if (sfPace) sfPace.finish(() => sfRender(d)); else sfRender(d);
    }
    if (d.status !== 'done' && d.status !== 'error') sfPoll = setTimeout(sfPollStatus, 2000);
  }).catch(() => {
    if (sfRetry) sfRetry.fail();
    sfPoll = setTimeout(sfPollStatus, 3000);
  });
}

function sfShowError(msg) {
  if (sfPace) sfPace.stop();
  document.getElementById('sf-error-msg').textContent = msg;
  sfShow('sf-error');
}

function sfRender(d) {
  const sig = JSON.stringify(Array.from({ length: d.num_options }, (_, i) => d['opt' + i] && d['opt' + i].tryon_status));
  if (sig === sfLastRendered) return;
  sfLastRendered = sig;
  sfShow('sf-results');

  const container = document.getElementById('sf-opts');
  container.innerHTML = '';
  for (let i = 0; i < Math.min(d.num_options, 3); i++) {
    const o = d['opt' + i];
    if (o) container.appendChild(sfBuildCard(o, i));
  }

  /* The profile persists beside the results rather than being thrown away. */
  const fs = d.face_summary || sfLastSummary;
  const panel = document.getElementById('sf-profile-panel');
  if (fs && fs.face_shape) {
    /* The readings, and only the readings. The insight essay ran to a couple of
       thousand words down a 300px column beside the renders — it is written to
       fill the wait, so it belongs in the docket where it streams in a line at
       a time, not stacked beside the thing the visitor came here to look at. */
    const dots = ['hair_color_hex', 'eye_color_hex', 'skin_tone_hex']
      .filter(k => fs[k])
      .map(k => '<span class="cdot" style="background:' + escHtml(fs[k]) + '" title="' +
        escHtml(k.replace(/_color_hex|_tone_hex/, '')) + '"></span>').join('');
    panel.innerHTML =
      '<h3 class="pp-h">Your face profile</h3>' +
      '<dl class="profile in">' +
        sfProfileRow('Face shape', escHtml(fs.face_shape)) +
        (fs.key_geometry ? sfProfileRow('Geometry', escHtml(fs.key_geometry)) : '') +
        (fs.color_profile
          ? sfProfileRow('Colouring',
              escHtml(fs.color_profile) + (dots ? '<span class="cdots">' + dots + '</span>' : ''))
          : '') +
      '</dl>';
    panel.hidden = false;
  }
}

function sfResetState() {
  sfLastRendered = '';
  sfProfileDone = false; sfFrameNamed = false;
  document.getElementById('sf-profile').innerHTML = '';
  const frame = document.getElementById('sf-frame');
  frame.classList.remove('in'); frame.innerHTML = '';
  const panel = document.getElementById('sf-profile-panel');
  panel.hidden = true; panel.innerHTML = '';
}

function sfReset() {
  sfSid = null;
  if (sfPoll) clearTimeout(sfPoll);
  sfResetState();
  document.getElementById('sf-file').value = '';
  sfPendingFile = null;
  sfPendingPreview = null;
  try { sessionStorage.removeItem('cached_portrait'); } catch (e) {}
  sfShowUploadEmpty();
  closeSmartFit();
  document.getElementById('sf-opts').innerHTML = '';
  const wrap = document.getElementById('sf-portrait-wrap');
  wrap.hidden = true;
  wrap.replaceChildren();
  if (sfPace) { sfPace.stop(); sfPace = null; }
  document.getElementById('sf-prog').style.transform = 'scaleX(0)';
  document.getElementById('sf-pct').textContent = '0%';
  sfShow(null);
}

/* ── Results ── */
const _recolorStore = {};
let _recolorIdx = 0;
function goRecolor(key) {
  try { sessionStorage.setItem('recolor_preload', _recolorStore[key]); } catch (e) {}
  window.location.href = '/lens-recolor';
}

function sfFmtPrice(o) {
  const sym = o.currency === 'ILS' ? '₪' : escHtml(o.currency || '');
  return sym + Number(o.price || 0).toLocaleString();
}

/* Delegated: the recolour key rides on a data attribute rather than being
   interpolated into an onclick, where a quote in the value breaks the parse.
   free-search.js shipped exactly that bug for several commits. */
document.addEventListener('click', function (e) {
  const b = e.target.closest && e.target.closest('.recolor-btn[data-rc]');
  if (b) { e.preventDefault(); goRecolor(b.dataset.rc); }
});

function buildTryonHtml(o) {
  if (o.tryon_status === 'done' && o.tryon_b64) {
    const key = 'rc' + (++_recolorIdx);
    _recolorStore[key] = o.tryon_b64;
    return '<div class="tryon-done">' +
      '<img src="data:image/png;base64,' + o.tryon_b64 + '" alt="You wearing ' + escHtml(o.name) + '"/>' +
      '<button class="recolor-btn" type="button" data-rc="' + key + '">Recolour lenses</button>' +
      '</div>';
  }
  if (o.tryon_status === 'error') {
    return '<div class="tryon-fail">Could not be rendered' +
      (o.tryon_error ? ': ' + escHtml(o.tryon_error) : '') + '</div>';
  }
  return '<div class="tryon-wait"><span class="tryon-wait-bar"></span>Rendering</div>';
}

function sfBuildCard(o, idx) {
  const card = document.createElement('article');
  card.className = 'result-card' + (idx === 0 ? ' is-primary' : '');
  const label = idx === 0 ? 'Best match' : 'Alternate ' + idx;
  const spec = [o.material, o.color].filter(Boolean).map(escHtml).join(' &middot; ');
  const scores = [
    o.fit_score != null ? ['Fit', o.fit_score] : null,
    o.style_score != null ? ['Style', o.style_score] : null,
    o.color_score != null ? ['Colour', o.color_score] : null,
  ].filter(Boolean);

  card.innerHTML =
    '<div class="result-img">' +
      '<span class="result-label">' + label + '</span>' +
      buildTryonHtml(o) +
    '</div>' +
    '<div class="result-body">' +
      '<span class="result-brand">' + escHtml(o.brand || '') + '</span>' +
      '<span class="result-name">' + escHtml(modelName(o)) + '</span>' +
      /* Always rendered, empty or not: the row reserves two lines here, and a
         card that omits the element reserves nothing and comes out of the line. */
      '<span class="result-spec">' + spec + '</span>' +
      (scores.length
        ? '<div class="scores">' + scores.map(s =>
            '<span class="score"><span class="score-k">' + s[0] + '</span>' +
            '<span class="score-v">' + Math.round(s[1]) + '</span></span>').join('') + '</div>'
        : '') +
      '<div class="result-foot">' +
        '<span class="price sg-price">' + sfFmtPrice(o) + '</span>' +
        (o.product_b64
          ? '<img class="result-thumb" src="data:image/jpeg;base64,' + o.product_b64 + '" alt=""/>'
          : '') +
      '</div>' +
    '</div>';
  return card;
}
