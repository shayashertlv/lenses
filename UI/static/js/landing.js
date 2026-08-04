/* ══════════════════════════════════════════════════
   Smart Fit — the dispensing counter

   The pipeline's real shape: analysis returns at roughly second eight, the
   catalogue match is local and instant just after, and the first try-on lands
   around second thirty. So everything interesting is known by second nine and
   the previous design showed a progress bar over the remaining twenty-plus
   seconds. The docket fills that gap with findings the API already returns:
   the face profile, the three optician notes, and the chosen frame named
   before its image exists.
   ══════════════════════════════════════════════════ */

let sfSid = null, sfPoll = null, sfLastRendered = '';
let sfPace = null, sfRetry = null;
let sfProfileDone = false, sfFrameNamed = false;
let sfNotes = [], sfNoteIdx = 0, sfNoteTimer = null;
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

/* ── The face profile: nine real fields, rendered as docket rows. ── */
function sfRenderProfile(fs) {
  if (sfProfileDone || !fs || !fs.face_shape) return;
  sfProfileDone = true;

  const dl = document.getElementById('sf-profile');
  const row = (k, v, extra) =>
    '<div class="prow"><dt>' + escHtml(k) + '</dt><dd>' + v + (extra || '') + '</dd></div>';

  let html = '';
  if (fs.face_shape) {
    html += row('Face shape',
      escHtml(fs.face_shape) +
      (fs.face_shape_description ? '<span class="pnote">' + escHtml(fs.face_shape_description) + '</span>' : ''));
  }
  if (fs.key_geometry) {
    html += row('Geometry',
      escHtml(fs.key_geometry) +
      (fs.key_geometry_description ? '<span class="pnote">' + escHtml(fs.key_geometry_description) + '</span>' : ''));
  }
  if (fs.color_profile) {
    const dots = ['hair_color_hex', 'eye_color_hex', 'skin_tone_hex']
      .filter(k => fs[k])
      .map(k => '<span class="cdot" style="background:' + escHtml(fs[k]) + '" title="' +
        escHtml(k.replace(/_color_hex|_tone_hex/, '')) + '"></span>').join('');
    html += row('Colouring',
      escHtml(fs.color_profile) + (dots ? '<span class="cdots">' + dots + '</span>' : '') +
      (fs.color_profile_description ? '<span class="pnote">' + escHtml(fs.color_profile_description) + '</span>' : ''));
  }
  dl.innerHTML = html;
  dl.classList.add('in');
}

/* ── The three optician notes, revealed across the render gap rather than
      dumped at once. Generated, cached and returned by the API since the
      analysis step; nothing rendered them before. ── */
function sfStartNotes(text) {
  if (sfNoteTimer || !text) return;
  const paras = (Array.isArray(text) ? text : String(text).split(/\n\s*\n/))
    .map(s => String(s).trim()).filter(Boolean);
  if (!paras.length) return;
  sfNotes = paras; sfNoteIdx = 0;

  const host = document.getElementById('sf-notes');
  const push = () => {
    if (sfNoteIdx >= sfNotes.length) { clearInterval(sfNoteTimer); sfNoteTimer = null; return; }
    const p = document.createElement('p');
    p.className = 'note';
    p.textContent = sfNotes[sfNoteIdx++];
    host.appendChild(p);
    requestAnimationFrame(() => p.classList.add('in'));
  };
  push();
  sfNoteTimer = setInterval(push, 5200);
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
    '<span class="pulled-price">' + sfFmtPrice(o) + '</span>';
  box.hidden = false;
  requestAnimationFrame(() => box.classList.add('in'));
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

function openSmartFit() {
  const overlay = document.getElementById('sf-modal');
  if (sfPendingPreview) sfShowPhotoReady(sfPendingPreview); else sfShowUploadEmpty();
  overlay.classList.add('active');
  const panel = overlay.querySelector('.modal');
  if (panel) panel.focus();
}

function closeSmartFit() {
  document.getElementById('sf-modal').classList.remove('active');
}

document.getElementById('sf-file').addEventListener('change', e => {
  const f = e.target.files[0];
  if (!f) return;
  sfPendingFile = f;
  const reader = new FileReader();
  reader.onload = ev => {
    sfPendingPreview = ev.target.result;
    sfShowPhotoReady(sfPendingPreview);
    try { sessionStorage.setItem('cached_portrait', sfPendingPreview); } catch (err) {}
  };
  reader.readAsDataURL(f);
});

async function sfStartFromModal() {
  const f = sfPendingFile;
  if (!f) return;
  closeSmartFit();
  sfResetState();
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
    if (d.face_insights) sfStartNotes(d.face_insights);
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
    const notes = (d.face_insights ? String(d.face_insights).split(/\n\s*\n/) : sfNotes)
      .map(s => String(s).trim()).filter(Boolean);
    panel.innerHTML =
      '<h3 class="pp-h">Your face profile</h3>' +
      '<dl class="profile in">' +
        '<div class="prow"><dt>Face shape</dt><dd>' + escHtml(fs.face_shape) + '</dd></div>' +
        (fs.key_geometry ? '<div class="prow"><dt>Geometry</dt><dd>' + escHtml(fs.key_geometry) + '</dd></div>' : '') +
        (fs.color_profile ? '<div class="prow"><dt>Colouring</dt><dd>' + escHtml(fs.color_profile) + '</dd></div>' : '') +
      '</dl>' +
      notes.map(n => '<p class="note in">' + escHtml(n) + '</p>').join('');
    panel.hidden = false;
  }
}

function sfResetState() {
  sfLastRendered = '';
  sfProfileDone = false; sfFrameNamed = false;
  sfNotes = []; sfNoteIdx = 0;
  if (sfNoteTimer) { clearInterval(sfNoteTimer); sfNoteTimer = null; }
  document.getElementById('sf-profile').innerHTML = '';
  document.getElementById('sf-notes').innerHTML = '';
  const frame = document.getElementById('sf-frame');
  frame.hidden = true; frame.classList.remove('in'); frame.innerHTML = '';
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
    '<div class="result-img">' + buildTryonHtml(o) + '</div>' +
    '<div class="result-body">' +
      '<span class="result-label">' + label + '</span>' +
      '<span class="result-brand">' + escHtml(o.brand || '') + '</span>' +
      '<span class="result-name">' + escHtml(modelName(o)) + '</span>' +
      (spec ? '<span class="result-spec">' + spec + '</span>' : '') +
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
