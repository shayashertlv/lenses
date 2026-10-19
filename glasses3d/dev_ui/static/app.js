import { api, cutoffValue, idempotencyKey } from './api.js';
import { createViewer } from './viewer.js';
import { annotateCapture } from './annotation.js';
import { labelWarnings } from './label-warnings.js';
import { cutoffBalance, money } from './money.js';

const MONEY_NOTE = 'Max budget is a settled-spend cutoff: no new request starts at or above it; the request already running can finish past it.';
const app = document.querySelector('#app');
const pageError = document.querySelector('#page-error');
const cleanups = [];
let leaving = false;
window.addEventListener('pagehide', () => {
  leaving = true;
  for (const cleanup of cleanups) cleanup();
});
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });

function node(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === 'class') element.className = value;
    else if (key === 'text') element.textContent = value;
    else if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
    else if (key in element && !key.startsWith('aria-') && !key.startsWith('data-')) element[key] = value;
    else element.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined) element.append(child);
  return element;
}

function button(text, attributes = {}) { return node('button', { type: 'button', ...attributes }, text); }
function link(text, href, attributes = {}) { return node('a', { href, ...attributes }, text); }
function field(label, control, help = null) {
  return node('div', { class: 'field' }, node('label', { htmlFor: control.id }, label), control, help && node('small', {}, help));
}
function errorMessage(error) {
  return typeof error === 'string' ? error : error?.message || 'Something went wrong. Please try again.';
}
function showError(error) {
  pageError.textContent = errorMessage(error);
  pageError.hidden = false;
}
function clearError() { pageError.hidden = true; pageError.textContent = ''; }
function routeKey() {
  const key = location.pathname.split('/').filter(Boolean)[1];
  if (!key || !/^[A-Za-z0-9_-]{6,64}$/.test(key)) throw new Error('This page does not have a valid saved key. Open it from Jobs.');
  return encodeURIComponent(key);
}
function dateText(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleString();
}
function elapsedText(seconds) {
  const whole = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  return `${hours ? `${hours}h ` : ''}${minutes}m ${whole % 60}s`;
}
function budgetInput(id = 'cutoff') {
  return node('input', { id, name: 'cutoff_usd', type: 'text', inputMode: 'decimal', required: true, autocomplete: 'off', placeholder: 'Enter amount in USD' });
}
function budgetBlock(input) {
  return node('div', {}, node('div', { class: 'budget-field' }, field('Max budget (USD)', input)), node('p', { class: 'money-note' }, MONEY_NOTE));
}
function warningBlock(warnings = []) {
  const box = node('div', { class: 'warnings', role: 'status', hidden: !warnings.length });
  if (warnings.length) box.append(node('ul', {}, warnings.map(warning => node('li', { 'data-warning-code': warning.code || '' }, warning.message || String(warning)))));
  return box;
}
function fullImage(url, alt, className = 'capture-thumb') {
  return link('', url, { target: '_blank', rel: 'noopener' }).appendChild(node('img', { src: url, alt, class: className })).parentElement;
}
function disableForm(form, disabled) {
  for (const control of form.querySelectorAll('button,input,select,textarea')) control.disabled = disabled;
}
function drawOutcome(delivery) {
  const outcome = node('section', { class: 'delivery-outcome' });
  if (delivery.applied?.length) {
    outcome.append(node('h2', {}, 'Applied'), node('ul', {}, delivery.applied.map(pick => node('li', {}, `${pick.item_id}: ${pick.amount_text || pick.option_id}`))));
  }
  if (delivery.notes?.length) {
    outcome.append(node('h2', {}, 'Not applied'), node('ul', {}, delivery.notes.map(item => node('li', { class: 'verbatim' }, `${item.item_id}${item.note ? `: ${item.note}` : ''}`))));
  }
  if (delivery.warnings?.length) outcome.append(warningBlock(delivery.warnings));
  if (delivery.exactness) {
    const proof = delivery.exactness;
    const text = proof.ok !== true ? 'Exactness check needs attention.'
      : proof.single_pick_glb_equal ? 'The selected result matches its preview file exactly.'
      : proof.overlaps?.length ? 'Overlapping picks were composed in item order; interactions are recorded below.'
      : 'Selected disjoint changes match their previews exactly.';
    outcome.append(node('details', { class: 'exactness' }, node('summary', {}, text),
      node('pre', {}, JSON.stringify(proof, null, 2))));
  }
  if (delivery.side_effects) {
    const effects = delivery.side_effects, summary = effects.summary || {};
    const outside = summary.geometry_outside_item_region === true
      ? (summary.outside_region_objects || []).join(', ') || 'detected'
      : summary.geometry_outside_item_region === false ? 'none detected' : 'unresolved';
    const section = node('section', { class: 'side-effects' },
      node('h2', {}, `Full edit ${effects.item_id || ''}: side effects`),
      node('p', {}, `Measurement: ${effects.status || 'unavailable'}. These measurements are for your review.`),
      node('p', {}, `Changed objects: ${(summary.changed_objects || []).join(', ') || 'none reported'}`),
      node('p', {}, `Changes outside the marked region: ${outside}`));
    if (summary.unresolved_objects?.length) section.append(node('p', {}, `Unresolved correspondence: ${summary.unresolved_objects.join(', ')}`));
    if (effects.reason) section.append(node('p', {}, effects.reason));
    section.append(node('details', {}, node('summary', {}, 'Full side-effect report'),
      node('pre', {}, JSON.stringify(effects, null, 2))));
    outcome.append(section);
  }
  return outcome;
}

async function jobsPage() {
  const { jobs } = await api.get('/api/jobs');
  app.replaceChildren();
  if (!jobs.length) { app.append(node('p', {}, 'No jobs yet.'), link('Start a new run', '/new', { class: 'button' })); return; }
  const ordered = [...jobs].sort((a, b) => String(b.created_utc).localeCompare(String(a.created_utc)));
  for (const job of ordered) {
    const card = node('section', { class: 'card', 'data-job-id': job.job_id }, node('h2', {}, job.name || job.job_id), node('p', { class: 'muted' }, dateText(job.created_utc)));
    const entries = [];
    for (const delivery of job.deliveries || []) entries.push(node('li', {}, link(`Delivery · ${delivery.label}`, `/results/${encodeURIComponent(delivery.delivery_key)}`), ` · ${dateText(delivery.created_utc)}`));
    for (const run of job.runs || []) {
      if (['starting', 'running', 'finalizing'].includes(run.phase)) entries.push(node('li', {}, link(`Active ${run.kind} run · ${run.phase}`, `/run/${encodeURIComponent(run.run_key)}`)));
    }
    for (const draft of job.drafts || []) entries.push(node('li', {}, link(`Edit draft · ${draft.draft_id}`, `/edit/${encodeURIComponent(draft.draft_id)}`)));
    for (const edit of job.edits || []) entries.push(node('li', {}, link(`Edit · ${edit.phase}`, `/understand/${encodeURIComponent(edit.edit_key)}`)));
    card.append(entries.length ? node('ul', { class: 'job-links' }, entries) : node('p', { class: 'muted' }, 'No saved delivery yet.'));
    app.append(card);
  }
}

async function newPage(config) {
  const photos = [];
  const photoList = node('div', { class: 'photo-list', id: 'photo-list' });
  const uploader = node('input', { id: 'photos', type: 'file', accept: 'image/*', multiple: true });
  const name = node('input', { id: 'name', name: 'name', type: 'text', autocomplete: 'off' });
  const description = node('textarea', { id: 'description', name: 'description', rows: 5 });
  const specs = node('textarea', { id: 'specs', name: 'specs_text', rows: 4, spellcheck: false });
  const cutoff = budgetInput();
  const submit = button('Start a paid run', { id: 'start-run', type: 'submit', class: 'primary' });
  const form = node('form', { id: 'new-run-form', class: 'narrow' },
    field('Product photos', uploader, 'Choose each photo’s view and wearer’s side below.'), photoList,
    field('Short name (optional)', name), field('Product description', description),
    field('Specifications (optional JSON object)', specs), budgetBlock(cutoff), submit);
  let sending = false;
  let nextPhotoId = 0;
  const dropdown = (id, name, placeholder, values) => node('select', { id, name, required: true }, node('option', { value: '', disabled: true, selected: true }, placeholder), values.map(value => node('option', { value }, value)));
  uploader.addEventListener('change', () => {
    for (const file of uploader.files) {
      const index = nextPhotoId++;
      const url = URL.createObjectURL(file);
      const kind = dropdown(`photo-kind-${index}`, 'kind', 'Choose view', config.view_kinds);
      const side = dropdown(`photo-side-${index}`, 'side', 'Choose side', config.sides);
      const row = node('div', { class: 'photo-row', 'data-photo-index': index }, node('img', { src: url, alt: file.name }));
      const photo = { file, url, kind, side, row };
      const remove = button('Remove photo', { onclick: () => {
        photos.splice(photos.indexOf(photo), 1); URL.revokeObjectURL(url); row.remove();
      } });
      row.append(node('div', {}, node('div', { class: 'photo-name' }, file.name), node('div', { class: 'photo-fields' }, field('View', kind), field('Wearer’s side', side)), remove));
      photos.push(photo);
      photoList.append(row);
    }
    uploader.value = '';
  });
  cleanups.push(() => photos.forEach(photo => URL.revokeObjectURL(photo.url)));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (sending) return;
    clearError();
    try {
      if (!photos.length) throw new Error('Add at least one product photo.');
      if (!form.reportValidity()) return;
      if (new Set(photos.map(photo => photo.file.name)).size !== photos.length) throw new Error('Each photo needs a different filename. Remove or rename the duplicate photo.');
      if (specs.value.trim()) {
        let parsed;
        try { parsed = JSON.parse(specs.value); } catch { throw new Error('Specifications must be a JSON object. Check the JSON and try again.'); }
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Specifications must be a JSON object.');
      }
      const manifest = {
        name: name.value, description: description.value, specs_text: specs.value,
        cutoff_usd: cutoffValue(cutoff), idempotency_key: idempotencyKey(),
        photos: photos.map((photo, index) => ({ field: `photo_${index}`, filename: photo.file.name, kind: photo.kind.value, side: photo.side.value })),
      };
      const body = new FormData();
      body.append('manifest', JSON.stringify(manifest));
      photos.forEach((photo, index) => body.append(manifest.photos[index].field, photo.file, photo.file.name));
      sending = true; disableForm(form, true); submit.textContent = 'Starting…';
      const result = await api.post('/api/jobs/start', body);
      location.assign(`/run/${encodeURIComponent(result.run_key)}`);
    } catch (error) { showError(error); sending = false; disableForm(form, false); submit.textContent = 'Start a paid run'; }
  });
  app.replaceChildren(form);
}

async function runPage() {
  const key = routeKey();
  const spend = node('span', { id: 'run-spend', class: 'stat-value' });
  const elapsed = node('span', { id: 'run-elapsed', class: 'stat-value' });
  const state = node('p', { id: 'run-state' });
  const moneyLeft = node('p', { id: 'run-money-left' });
  const overrun = node('p', { id: 'run-cutoff-overrun', hidden: true });
  const terminal = node('p', { id: 'run-terminal', hidden: true });
  const stop = button('Stop', { id: 'stop-run', class: 'danger', disabled: true });
  app.replaceChildren(node('div', { class: 'stats' },
    node('div', {}, node('span', { class: 'stat-label' }, 'Settled spend'), spend),
    node('div', {}, node('span', { class: 'stat-label' }, 'Elapsed time'), elapsed)), moneyLeft, overrun, state, terminal, stop);
  let run = null;
  let stopSending = false;
  let receivedAt = Date.now();
  let timer;
  const tick = () => {
    if (!run) return;
    const start = Date.parse(run.started_at);
    const seconds = Number.isFinite(start) ? (Date.now() - start) / 1000 : Number(run.elapsed_seconds || 0) + (Date.now() - receivedAt) / 1000;
    elapsed.textContent = elapsedText(['ended', 'failed'].includes(run.phase) ? Number(run.elapsed_seconds || 0) : seconds);
  };
  const render = dto => {
    run = dto; receivedAt = Date.now();
    if (dto.offline_test_mode) document.querySelector('#offline-banner').hidden = false;
    spend.textContent = `${money(dto.settled_usd)} of ${money(dto.cutoff_usd)}`;
    spend.title = `Settled: ${money(dto.settled_usd)}; cutoff: ${money(dto.cutoff_usd)}`;
    state.textContent = `${dto.phase}${dto.loop_state ? ` · ${dto.loop_state}` : ''}${dto.stop_requested ? ' · stop requested' : ''}`;
    const balance = cutoffBalance(dto.cutoff_usd, dto.settled_usd);
    moneyLeft.textContent = balance ? (balance.left === '0' ? '$0.00 left' : `Money left ${money(balance.left)}`) : '';
    overrun.hidden = !balance?.exceeded;
    overrun.textContent = balance?.exceeded ? `The last request finished past the cutoff by ${money(balance.overrun)}.` : '';
    terminal.hidden = !dto.terminal_status;
    terminal.textContent = dto.terminal_status ? `Run result: ${dto.terminal_status}` : '';
    stop.disabled = stopSending || dto.stop_requested || !['starting', 'running', 'finalizing'].includes(dto.phase);
    stop.textContent = dto.stop_requested ? 'Stop requested' : 'Stop';
    if (dto.error) showError(dto.error);
    tick();
    if (dto.phase === 'ended') {
      if (dto.kind === 'understand' && dto.edit_key) location.replace(`/understand/${encodeURIComponent(dto.edit_key)}`);
      else if (dto.delivery_key) location.replace(`/results/${encodeURIComponent(dto.delivery_key)}`);
      else terminal.append(' · No saved result was returned.');
    }
  };
  stop.addEventListener('click', async () => {
    if (stopSending || stop.disabled || !confirm('Stop after the current step? The request already running finishes.')) return;
    stopSending = true; stop.disabled = true; clearError();
    try { render(await api.post(`/api/runs/${key}/stop`)); }
    catch (error) { showError(error); }
    finally { stopSending = false; if (run) stop.disabled = run.stop_requested || !['starting', 'running', 'finalizing'].includes(run.phase); }
  });
  const poll = async () => {
    try { clearError(); render(await api.get(`/api/runs/${key}`)); }
    catch (error) { showError(error); }
    if (!leaving && !['ended', 'failed'].includes(run?.phase)) timer = setTimeout(poll, 2000);
  };
  const ticker = setInterval(tick, 1000);
  cleanups.push(() => { clearTimeout(timer); clearInterval(ticker); });
  await poll();
}

async function attachViewer(container, delivery) {
  try {
    const viewer = await createViewer(container, { modelUrl: delivery.model_url, source: { delivery_key: delivery.delivery_key, glb_sha256: delivery.glb_sha256 } });
    cleanups.push(() => viewer.dispose());
    return viewer;
  } catch (error) { showError(error); return null; }
}

async function resultsPage() {
  const key = routeKey();
  const delivery = await api.get(`/api/deliveries/${key}`);
  const viewer = node('div', { id: 'viewer', class: 'viewer', 'aria-label': '3D model viewer' });
  const ar = button('View in AR', { id: 'view-ar' });
  const edit = button('Edit', { id: 'edit-delivery', class: 'primary' });
  app.replaceChildren(node('p', {}, node('span', { class: `status-tag${delivery.finished ? '' : ' not-finished'}` }, delivery.finished ? 'finished' : 'not finished'), ` · Settled spend: ${money(delivery.run_settled_usd)}`),
    viewer, node('p', { class: 'viewer-help' }, 'Drag to orbit, scroll to zoom, right-drag to pan.'), node('div', { class: 'actions' }, ar, edit), drawOutcome(delivery));
  if (delivery.checks?.length) app.append(node('details', {}, node('summary', {}, 'Model checks'), node('ul', { class: 'check-list' }, delivery.checks.map(check => node('li', {}, `${check.name}: ${check.ok ? 'passed' : 'needs attention'}${check.detail ? ` · ${typeof check.detail === 'string' ? check.detail : JSON.stringify(check.detail)}` : ''}`)))));
  ar.addEventListener('click', async () => {
    if (ar.disabled) return;
    ar.disabled = true; clearError();
    // Open synchronously to retain the owner's click gesture through the POST.
    const popup = window.open('about:blank', '_blank');
    if (popup) popup.opener = null;
    try {
      const result = await api.post(`/api/deliveries/${key}/ar`);
      if (popup) popup.location.replace(result.url);
      else app.append(node('p', {}, link('Open AR viewer', result.url, { target: '_blank', rel: 'noopener' })));
    } catch (error) { popup?.close(); showError(error); }
    finally { ar.disabled = false; }
  });
  edit.addEventListener('click', async () => {
    if (edit.disabled) return;
    edit.disabled = true; clearError();
    try {
      const draft = await api.post('/api/edit-drafts', { delivery_key: delivery.delivery_key });
      location.assign(`/edit/${encodeURIComponent(draft.draft_id)}`);
    } catch (error) { showError(error); edit.disabled = false; }
  });
  await attachViewer(viewer, delivery);
}

async function editPage() {
  const key = routeKey();
  let draft = await api.get(`/api/edit-drafts/${key}`);
  const delivery = await api.get(`/api/deliveries/${encodeURIComponent(draft.source.delivery_key)}`);
  const viewerElement = node('div', { id: 'viewer', class: 'viewer', 'aria-label': '3D model viewer' });
  const captureButton = button('Capture and annotate', { id: 'capture-annotate', class: 'primary', disabled: true });
  const itemList = node('section', { id: 'edit-items' });
  const next = node('section', { id: 'more-edits' });
  let viewer;
  let annotating = false;
  const pendingWords = new Map();
  app.replaceChildren(drawOutcome(delivery), viewerElement, node('p', { class: 'viewer-help' }, 'Drag to orbit, scroll to zoom, right-drag to pan.'), node('div', { class: 'actions' }, captureButton), itemList, next);
  const capture = async () => {
    if (!viewer || annotating || draft.frozen) return;
    annotating = true; captureButton.disabled = true; clearError();
    try {
      const number = Math.max(0, ...draft.items.map(item => Number(/^C(\d+)$/.exec(item.capture_id)?.[1] || 0))) + 1;
      await annotateCapture(viewer, {
        source: draft.source, labels: draft.labels, sessionTexts: draft.items.map(item => item.request_text), captureId: `C${number}`,
        onSave: async bundle => {
          const body = new FormData();
          body.append('capture', JSON.stringify(bundle.capture));
          body.append('viewer', bundle.viewer, `${bundle.capture.capture_id}-viewer.png`);
          body.append('annotated', bundle.annotated, `${bundle.capture.capture_id}-annotated.png`);
          const result = await api.post(`/api/edit-drafts/${key}/items`, body);
          draft.items.push(result.item);
          draft.labels = [...new Set(draft.items.flatMap(item => item.labels))];
          renderDraft();
          return result;
        },
      });
    } catch (error) { showError(error); }
    finally { annotating = false; captureButton.disabled = !viewer || draft.frozen; }
  };
  captureButton.addEventListener('click', capture);
  function renderDraft() {
    itemList.replaceChildren(); next.replaceChildren();
    captureButton.disabled = !viewer || draft.frozen || annotating;
    if (draft.frozen) next.append(node('p', { class: 'notice' }, 'These items have been sent to Astra. This draft is saved and cannot be changed.'));
    for (const item of draft.items) {
      const words = node('textarea', { id: `words-${item.item_id}`, value: pendingWords.get(item.item_id) ?? item.request_text, disabled: draft.frozen });
      const liveWarnings = () => labelWarnings(words.value, draft.labels, item.labels, draft.items.filter(other => other.item_id !== item.item_id).map(other => pendingWords.get(other.item_id) ?? other.request_text));
      let warnings = warningBlock(pendingWords.has(item.item_id) ? liveWarnings() : item.warnings);
      const save = button('Save words', { disabled: draft.frozen || words.value === item.request_text });
      const remove = button('Delete item', { class: 'danger', disabled: draft.frozen });
      const controls = node('div', {}, node('h3', {}, `Item ${item.item_id}`), node('p', { class: 'labels' }, `Labels: ${item.labels.join(', ')}`), field('Edit words', words), warnings, node('div', { class: 'actions' }, save, remove));
      const card = node('article', { class: 'card item-layout', 'data-item-id': item.item_id }, fullImage(item.annotated_url, `Annotated capture for item ${item.item_id}`), controls);
      const updateWarnings = list => { const replacement = warningBlock(list); warnings.replaceWith(replacement); warnings = replacement; };
      words.addEventListener('input', () => {
        if (words.value === item.request_text) pendingWords.delete(item.item_id);
        else pendingWords.set(item.item_id, words.value);
        save.disabled = words.value === item.request_text || draft.frozen;
        updateWarnings(liveWarnings());
      });
      save.addEventListener('click', async () => {
        if (save.disabled) return;
        save.disabled = true; words.disabled = true; remove.disabled = true; clearError();
        try {
          const result = await api.patch(`/api/edit-drafts/${key}/items/${encodeURIComponent(item.item_id)}`, { request_text: words.value });
          item.request_text = result.item.request_text;
          item.warnings = result.warnings;
          pendingWords.delete(item.item_id);
          updateWarnings(result.warnings);
        } catch (error) { showError(error); save.disabled = false; }
        finally { words.disabled = draft.frozen; remove.disabled = draft.frozen; }
      });
      remove.addEventListener('click', async () => {
        if (remove.disabled) return;
        remove.disabled = true; clearError();
        try { draft = await api.delete(`/api/edit-drafts/${key}/items/${encodeURIComponent(item.item_id)}`); pendingWords.delete(item.item_id); renderDraft(); }
        catch (error) { showError(error); remove.disabled = false; }
      });
      itemList.append(card);
    }
    if (draft.frozen) return;
    if (!draft.items.length && !['applied_picks', 'full_edit_item'].includes(delivery.origin)) return;
    const yes = button('Yes, capture another', { onclick: capture });
    const no = button(draft.items.length ? 'No, send to Astra' : 'No, back to results');
    next.append(node('h2', {}, 'Any more edits?'), node('div', { class: 'actions' }, yes, no));
    if (!draft.items.length) { no.addEventListener('click', () => location.assign(`/results/${encodeURIComponent(delivery.delivery_key)}`)); return; }
    const cutoff = budgetInput('edit-cutoff');
    const send = button('Send to Astra (paid)', { id: 'send-edits', type: 'submit', class: 'primary' });
    const sendForm = node('form', { id: 'send-edits-form', hidden: true }, budgetBlock(cutoff), send);
    next.append(sendForm);
    no.addEventListener('click', () => { sendForm.hidden = false; cutoff.focus(); });
    let sending = false;
    sendForm.addEventListener('submit', async event => {
      event.preventDefault(); if (sending) return; clearError();
      try {
        if (!sendForm.reportValidity()) return;
        if ([...itemList.querySelectorAll('textarea')].some(textarea => {
          const item = draft.items.find(entry => textarea.id === `words-${entry.item_id}`);
          return item && textarea.value !== item.request_text;
        })) throw new Error('Save your changed words before sending these items to Astra.');
        const body = { cutoff_usd: cutoffValue(cutoff), idempotency_key: idempotencyKey() };
        sending = true; disableForm(sendForm, true); captureButton.disabled = true; yes.disabled = true;
        for (const control of itemList.querySelectorAll('button,textarea')) control.disabled = true;
        const result = await api.post(`/api/edit-drafts/${key}/send`, body);
        location.assign(`/run/${encodeURIComponent(result.run_key)}`);
      } catch (error) {
        showError(error);
        sending = false;
        disableForm(sendForm, false);
        captureButton.disabled = !viewer || draft.frozen || annotating;
        yes.disabled = false;
        for (const card of itemList.querySelectorAll('[data-item-id]')) {
          const item = draft.items.find(entry => entry.item_id === card.dataset.itemId);
          const words = card.querySelector('textarea');
          const [save, remove] = card.querySelectorAll('button');
          words.disabled = draft.frozen;
          save.disabled = draft.frozen || words.value === item.request_text;
          remove.disabled = draft.frozen;
        }
      }
    });
  }
  renderDraft();
  viewer = await attachViewer(viewerElement, delivery);
  captureButton.disabled = !viewer || draft.frozen;
}

async function understandPage() {
  const key = routeKey();
  const understanding = await api.get(`/api/edits/${key}/understanding`);
  const choices = new Map();
  const items = node('div', { id: 'understanding-items' });
  const apply = button('Apply my picks ($0.00)', { id: 'apply-picks', class: 'primary', disabled: true });
  let busy = false;
  const refreshApply = () => { apply.disabled = busy || !understanding.items.length || understanding.items.some(item => item.disposition === 'previewed' && !choices.has(item.item_id)); };
  app.replaceChildren(node('p', {}, `Understanding settled spend: ${money(understanding.run_settled_usd)}`), items, node('div', { class: 'actions' }, apply));
  if (understanding.limitations?.length) app.append(warningBlock(understanding.limitations.map(message => typeof message === 'string' ? { message } : message)));
  if (understanding.map_images?.length) app.append(node('details', {}, node('summary', {}, 'Model maps'), understanding.map_images.map(image => node('figure', {}, fullImage(image.url, image.label, 'preview-image'), node('figcaption', {}, image.label)))));
  for (const item of understanding.items) {
    const card = node('section', { class: 'card', 'data-item-id': item.item_id }, node('h2', {}, `Item ${item.number ?? item.item_id} · ${(item.labels || []).join(', ')}`),
      node('div', { class: 'item-layout' }, fullImage(item.annotated_url, `Annotated capture for item ${item.item_id}`), node('div', {}, node('h3', {}, 'Your words'), node('p', { class: 'verbatim' }, item.request_text), node('h3', {}, 'Astra’s reading'), node('p', { class: 'verbatim' }, item.interpretation || 'No interpretation was returned.'))));
    const optionGrid = node('div', { class: 'option-grid' });
    const optionButtons = [];
    const none = button('None of these', { 'data-option-id': 'none', 'aria-pressed': 'false' });
    const note = node('textarea', { id: `note-${item.item_id}`, rows: 3 });
    const noteField = field('Your note', note);
    noteField.hidden = true;
    const select = optionId => {
      if (busy) return;
      choices.set(item.item_id, { option_id: optionId, note: note.value });
      for (const entry of optionButtons) {
        const selected = entry.optionId === optionId;
        entry.button.setAttribute('aria-pressed', String(selected));
        entry.container.classList.toggle('selected', selected);
      }
      none.setAttribute('aria-pressed', String(optionId === null));
      noteField.hidden = optionId !== null;
      refreshApply();
    };
    for (const option of item.options || []) {
      const pick = button(option.label, { 'data-option-id': option.option_id, 'aria-pressed': 'false', onclick: () => select(option.option_id) });
      const container = node('div', { class: 'option' }, pick, node('p', {}, option.amount_text));
      for (const image of option.images || []) container.append(fullImage(image.url, `${option.label}: ${image.view} — ${image.panels.join(' / ')}`, 'preview-image'), node('div', { class: 'image-caption' }, `${image.view} · ${image.panels.join(' / ')} · Click to view full size`));
      optionButtons.push({ optionId: option.option_id, button: pick, container });
      optionGrid.append(container);
    }
    if (item.disposition === 'previewed') card.append(optionGrid, none, noteField);
    else {
      choices.set(item.item_id, { option_id: null, note: '' });
      noteField.hidden = false;
      card.append(node('p', { class: 'notice' }, `${item.disposition.replaceAll('_', ' ')}${item.reason ? `: ${item.reason}` : ''}`), node('p', {}, 'This item will not be applied by Apply my picks.'), noteField);
    }
    none.addEventListener('click', () => select(null));
    note.addEventListener('input', () => { const choice = choices.get(item.item_id); if (choice) choice.note = note.value; });
    if (item.disposition !== 'previewed' && item.fallback_allowed) {
      const cutoff = budgetInput(`fallback-cutoff-${item.item_id}`);
      const send = button('Send this item to a full paid edit', { type: 'submit', class: 'primary' });
      const form = node('form', { class: 'fallback-form' }, budgetBlock(cutoff), send);
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (busy) return; clearError();
        try {
          if (!form.reportValidity()) return;
          const body = { cutoff_usd: cutoffValue(cutoff), idempotency_key: idempotencyKey() };
          busy = true; refreshApply(); disableForm(form, true);
          const result = await api.post(`/api/edits/${key}/items/${encodeURIComponent(item.item_id)}/full-edit`, body);
          location.assign(`/run/${encodeURIComponent(result.run_key)}`);
        } catch (error) { showError(error); busy = false; disableForm(form, false); refreshApply(); }
      });
      card.append(form);
    }
    items.append(card);
  }
  refreshApply();
  apply.addEventListener('click', async () => {
    if (apply.disabled || busy) return;
    clearError(); busy = true; refreshApply();
    const selected = [], skipped = [];
    for (const item of understanding.items) {
      const choice = choices.get(item.item_id);
      if (choice?.option_id) selected.push({ item_id: item.item_id, option_id: choice.option_id });
      else skipped.push({ item_id: item.item_id, note: choice?.note || '' });
    }
    try {
      const result = await api.post(`/api/edits/${key}/apply`, { understanding_sha256: understanding.understanding_sha256, choices: selected, skipped, idempotency_key: idempotencyKey() });
      location.assign(`/task/${encodeURIComponent(result.task_key)}`);
    } catch (error) { showError(error); busy = false; refreshApply(); }
  });
}

async function taskPage() {
  const key = routeKey();
  const state = node('p', { id: 'task-state' });
  const elapsed = node('p', { id: 'task-elapsed' });
  const progress = node('p', { id: 'task-progress', class: 'verbatim' });
  const retry = button('Open edited model', { id: 'open-task-result', hidden: true });
  app.replaceChildren(node('p', {}, 'Applying your picks locally ($0.00).'), state, elapsed, progress, retry);
  let timer;
  let task;
  let opening = false;
  const openResult = async () => {
    if (opening) return;
    opening = true; retry.disabled = true; clearError();
    try {
      const draft = await api.post('/api/edit-drafts', { delivery_key: task.result.delivery_key });
      location.replace(`/edit/${encodeURIComponent(draft.draft_id)}`);
    } catch (error) { showError(error); opening = false; retry.hidden = false; retry.disabled = false; }
  };
  retry.addEventListener('click', openResult);
  const poll = async () => {
    try {
      clearError(); task = await api.get(`/api/tasks/${key}`);
      state.textContent = task.phase;
      elapsed.textContent = `Elapsed time: ${elapsedText(task.elapsed_seconds)}`;
      progress.textContent = typeof task.progress === 'string' ? task.progress : (task.progress ? JSON.stringify(task.progress) : '');
      if (task.error) showError(task.error);
      if (task.phase === 'done') {
        if (task.result?.delivery_key) await openResult();
        else showError('The apply task ended without a saved model.');
      }
    } catch (error) { showError(error); }
    if (!leaving && !['done', 'failed'].includes(task?.phase)) timer = setTimeout(poll, 2000);
  };
  cleanups.push(() => clearTimeout(timer));
  await poll();
}

async function start() {
  const config = await api.get('/api/config');
  document.querySelector('#offline-banner').hidden = !config.offline_test_mode;
  const pages = { index: jobsPage, new: newPage, run: runPage, results: resultsPage, edit: editPage, understand: understandPage, task: taskPage };
  const render = pages[document.body.dataset.page];
  if (!render) throw new Error('This page is not available. Open Jobs to continue.');
  await render(config);
}

start().catch(error => { app.replaceChildren(); showError(error); });
