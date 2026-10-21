import { api, idempotencyKey } from './api.js';

const clone = value => structuredClone(value);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

// Picker/photo pixels are sRGB; material tint is linear, like the saved GLB.
export function srgbToLinear(value) {
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}
export function linearToSrgb(value) {
  return value <= 0.0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - 0.055;
}
export function tintToHex(tint) {
  return '#' + tint.map(value => Math.round(clamp(linearToSrgb(value), 0, 1) * 255).toString(16).padStart(2, '0')).join('');
}
export function hexToTint(hex) {
  return [1, 3, 5].map(offset => srgbToLinear(parseInt(hex.slice(offset, offset + 2), 16) / 255));
}

function el(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === 'class') element.className = value;
    else if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
    else if (key in element && !key.startsWith('aria-') && !key.startsWith('data-')) element[key] = value;
    else element.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined) element.append(child);
  return element;
}
const button = (text, props = {}) => el('button', { type: 'button', ...props }, text);
const prettyView = photo => `${(photo.kind || 'reference').replaceAll('_', ' ')}${['left', 'right'].includes(photo.side) ? ` · ${photo.side}` : ''}`;
function viewGroup(kind = '') {
  if (kind.includes('top')) return 'top';
  if (kind === 'front') return 'front';
  if (kind === 'side') return 'side';
  return 'angled';
}

/** Local session history groups each slider gesture into one reversible edit. */
export class LensHistory {
  constructor(materials) {
    this.original = clone(materials);
    this.current = clone(materials);
    this.past = [];
    this.future = [];
    this.pending = null;
  }
  get dirty() { return !same(this.current, this.original); }
  update(change, commit = false) {
    this.pending ??= clone(this.current);
    change(this.current);
    if (commit) this.commit();
  }
  commit() {
    if (this.pending && !same(this.pending, this.current)) {
      this.past.push(this.pending);
      this.future = [];
    }
    this.pending = null;
  }
  undo() {
    this.commit();
    if (!this.past.length) return;
    this.future.push(clone(this.current));
    this.current = this.past.pop();
  }
  redo() {
    this.commit();
    if (!this.future.length) return;
    this.past.push(clone(this.current));
    this.current = this.future.pop();
  }
}

export async function lensColourPage(host, key) {
  const error = el('p', { class: 'error', role: 'alert', hidden: true });
  const showError = message => { error.textContent = message?.message || String(message); error.hidden = false; };
  const clearError = () => { error.hidden = true; error.textContent = ''; };
  const back = el('a', { href: `/results/${key}` }, 'Back to results');
  const intro = el('div', { class: 'lens-intro' }, el('p', {}, 'Adjust the lenses, compare with your photos, then save a new version. These edits are free.'), back);
  host.replaceChildren(intro, error, el('p', { role: 'status' }, 'Loading lens colours and reference photos…'));
  let dto;
  try { dto = await api.get(`/api/deliveries/${key}/lens-colour`); }
  catch (cause) {
    showError(cause);
    host.lastElementChild.replaceWith(button('Try loading again', { onclick: () => location.reload() }));
    return () => {};
  }
  if (!Array.isArray(dto.materials) || !dto.materials.length) {
    host.lastElementChild.replaceWith(el('p', { class: 'notice' }, 'This version has no editable lens materials. Open another delivery from Jobs.'));
    return () => {};
  }

  const history = new LensHistory(dto.materials);
  const photos = dto.photos || [];
  let viewer, coatingSwatchRGB, disposed = false, raf = 0, timer, busy = false, ready = false, allowLeave = false;
  let before = false, selectedIndex = 0, linked = true, activePhoto = 0;
  let saveRequest = null;
  const controlRows = [];
  const lensHost = el('div', { id: 'lens-viewer', class: 'viewer lens-viewer', 'aria-label': 'Live lens colour preview', 'aria-busy': 'true' });
  const loading = el('div', { class: 'lens-viewer-message', role: 'status' }, 'Loading the AR preview…');
  lensHost.append(loading);
  const mode = el('span', { class: 'lens-preview-label', id: 'lens-preview-label' }, 'After · your changes');
  const beforeButton = button('Before', { id: 'lens-before', 'aria-pressed': 'false', onclick: () => setBefore(true) });
  const afterButton = button('After', { id: 'lens-after', 'aria-pressed': 'true', onclick: () => setBefore(false) });
  const undo = button('Undo', { id: 'lens-undo', title: 'Undo (Ctrl+Z)', onclick: () => { history.undo(); changed(); } });
  const redo = button('Redo', { id: 'lens-redo', title: 'Redo (Ctrl+Y)', onclick: () => { history.redo(); changed(); } });
  const resetAll = button('Reset all', { id: 'lens-reset-all', onclick: () => {
    history.commit(); history.update(rows => rows.splice(0, rows.length, ...clone(history.original)), true); changed();
  } });
  const state = el('span', { id: 'lens-save-state', class: 'muted', role: 'status' }, 'No changes yet');
  const progress = el('p', { id: 'lens-progress', role: 'status', hidden: true });
  const ar = button('Check in AR', { id: 'lens-check-ar' });
  const save = button('Save as new version', { id: 'lens-save', class: 'primary' });
  const controls = el('section', { class: 'lens-controls card', 'aria-label': 'Lens colour controls' });
  const linkControl = el('input', { type: 'checkbox', id: 'lens-link', checked: true });
  const materialChoice = el('select', { id: 'lens-material', 'aria-label': 'Lens to edit' }, dto.materials.map((material, index) =>
    el('option', { value: index }, `${material.name || `Lens ${index + 1}`}`)));
  const linkHelp = el('p', { class: 'lens-help', id: 'lens-link-help' });
  const selector = el('div', { class: 'lens-material-choice', hidden: dto.materials.length === 1 },
    el('label', { class: 'lens-check', htmlFor: 'lens-link' }, linkControl, 'Edit all lenses together'),
    el('label', { htmlFor: 'lens-material', class: 'lens-material-label' }, 'Lens to edit'), materialChoice, linkHelp);
  linkControl.addEventListener('change', () => { history.commit(); linked = linkControl.checked; refresh(); });
  materialChoice.addEventListener('change', () => { history.commit(); selectedIndex = Number(materialChoice.value); refresh(); });
  controls.append(selector);

  const targets = () => linked ? history.current.map((_, index) => index) : [selectedIndex];
  const mixed = field => targets().some(index => !same(history.current[index].values[field], history.current[selectedIndex].values[field]));
  const values = () => history.current[selectedIndex].values;
  const mutate = (fields, value, commit = false) => {
    clearError();
    history.update(rows => {
      for (const index of targets()) for (const field of fields) rows[index].values[field] = clone(typeof value === 'function' ? value(rows[index].values, field) : value);
    }, commit);
    changed();
  };
  function reset(fields) {
    history.commit();
    history.update(rows => {
      for (const index of targets()) for (const field of fields) rows[index].values[field] = clone(history.original[index].values[field]);
    }, true);
    changed();
  }
  function addControl({ id, label, fields, min = 0, max = 1, step = 0.01, read, write, format = value => `${Math.round(value * 100)}%`, note, parent = controls, type = 'range' }) {
    const input = el('input', { id: `lens-${id}`, type, min, max, step });
    const output = el('output', { htmlFor: input.id, id: `${input.id}-value`, class: 'lens-control-value' });
    const resetButton = button('Reset', { class: 'lens-reset', 'aria-label': `Reset ${label}`, onclick: () => reset(fields) });
    const caption = el('div', { class: 'lens-control-caption' }, el('label', { htmlFor: input.id }, label), output, resetButton);
    const row = el('div', { class: 'lens-control', 'data-control': id }, caption, input);
    if (note) {
      const help = el('p', { id: `${input.id}-help`, class: 'lens-help' }, note);
      input.setAttribute('aria-describedby', help.id); row.append(help);
    }
    parent.append(row);
    const update = () => write(type === 'checkbox' ? input.checked : type === 'color' ? input.value : Number(input.value));
    input.addEventListener('input', update);
    input.addEventListener('change', () => { history.commit(); refresh(); });
    input.addEventListener('blur', () => { history.commit(); refresh(); });
    controlRows.push({ input, output, resetButton, fields, read, format, type });
    return row;
  }

  const tintRow = addControl({ id: 'tint', label: 'Tint', fields: ['tint'], type: 'color', read: value => tintToHex(value.tint),
    write: value => mutate(['tint'], hexToTint(value)), format: value => value.toUpperCase() });
  const eyedropper = button('Pick from photo', { id: 'lens-eyedropper', onclick: () => openPhoto(activePhoto, true) });
  tintRow.append(eyedropper);
  addControl({ id: 'transmission', label: 'See-through amount', fields: ['transmission'], read: value => value.transmission, write: value => mutate(['transmission'], value) });
  addControl({ id: 'mirror', label: 'Mirror', fields: ['metallic'], read: value => value.metallic, write: value => mutate(['metallic'], value), note: 'More mirror darkens the see-through colour.' });
  addControl({ id: 'gloss', label: 'Gloss', fields: ['roughness'], read: value => 1 - value.roughness, write: value => mutate(['roughness'], 1 - value) });
  const shimmer = el('fieldset', { class: 'lens-shimmer' }, el('legend', {}, 'Coating shimmer'));
  controls.append(shimmer);
  addControl({ id: 'shimmer-on', label: 'Shimmer on', fields: ['iridescence'], type: 'checkbox', parent: shimmer,
    read: value => value.iridescence > 0, write: value => mutate(['iridescence'], value ? 1 : 0), format: value => value ? 'On' : 'Off' });
  addControl({ id: 'shimmer-strength', label: 'Strength', fields: ['iridescence'], parent: shimmer,
    read: value => value.iridescence, write: value => mutate(['iridescence'], value) });
  // No thickness texture is added: the exporter/AR renderer reads the maximum
  // thickness on a uniform film. Equal endpoints give the selected uniform hue.
  const colourRow = addControl({ id: 'shimmer-colour', label: 'Shimmer colour', fields: ['iridescence_thickness_min', 'iridescence_thickness_max'],
    min: 0, max: 1200, step: 1, parent: shimmer, read: value => value.iridescence_thickness_max,
    write: value => mutate(['iridescence_thickness_min', 'iridescence_thickness_max'], value),
    format: value => `${Math.round(value)} nm`, note: 'Slide through coating colours. The colour shifts with the viewing angle.' });
  const swatch = el('canvas', { id: 'lens-shimmer-swatch', class: 'lens-shimmer-swatch', width: 288, height: 30, role: 'img', 'aria-label': 'Coating reflection at different angles' });
  colourRow.append(swatch, el('p', { class: 'lens-help' }, 'Coating reflection at different angles.'));
  const advanced = el('details', { class: 'lens-advanced' }, el('summary', {}, 'Advanced'));
  controls.append(advanced);
  addControl({ id: 'ior', label: 'Lens IOR', fields: ['ior'], min: 1, max: 3, step: 0.01, parent: advanced,
    read: value => value.ior, write: value => mutate(['ior'], value), format: value => value.toFixed(2), note: 'How strongly the lens bends light.' });
  addControl({ id: 'film-ior', label: 'Film IOR', fields: ['iridescence_ior'], min: 1, max: 3, step: 0.01, parent: advanced,
    read: value => value.iridescence_ior, write: value => mutate(['iridescence_ior'], value), format: value => value.toFixed(2) });

  const viewButtons = el('div', { class: 'lens-views', role: 'group', 'aria-label': 'Preview view' });
  for (const [kind, label] of [['front', 'Front'], ['angled', 'Angled'], ['side', 'Side'], ['top', 'Top']]) {
    const match = photos.findIndex(photo => viewGroup(photo.kind) === kind);
    const view = button(label, { 'data-view': kind, 'aria-pressed': String(kind === 'front'), title: match < 0 ? `${label} view` : `Match ${prettyView(photos[match])} reference`, onclick: () => {
      if (match >= 0) activePhoto = match;
      viewer?.setView(match >= 0 ? photos[match].kind : kind, match >= 0 ? photos[match].side : undefined);
      for (const other of viewButtons.children) other.setAttribute('aria-pressed', String(other === view));
      highlightPhoto();
    } });
    viewButtons.append(view);
  }
  const photoStrip = el('div', { class: 'lens-photo-strip', id: 'lens-photos' });
  photos.forEach((photo, index) => {
    const image = el('img', { src: photo.url, alt: `${prettyView(photo)} reference photo`, loading: 'lazy' });
    image.addEventListener('error', () => { image.alt = 'Reference photo unavailable'; });
    photoStrip.append(button('', { class: 'lens-photo', 'aria-label': `Enlarge ${prettyView(photo)} reference photo`, onclick: () => openPhoto(index) }));
    photoStrip.lastElementChild.append(image, el('span', {}, prettyView(photo)));
  });
  const references = el('section', { class: 'lens-references', 'aria-label': 'Reference photos' }, el('h2', {}, 'Your reference photos'),
    photos.length ? el('p', { class: 'lens-help' }, 'Click to enlarge, or pick a tint directly from a photo.') : el('p', { class: 'muted' }, 'No reference photos are available for this delivery.'), photoStrip);
  const previewColumn = el('section', { class: 'lens-preview-column', 'aria-label': 'Preview and photos' },
    el('div', { class: 'lens-preview-toolbar' }, viewButtons, el('div', { class: 'lens-compare', role: 'group', 'aria-label': 'Compare lens colours' }, beforeButton, afterButton)),
    lensHost, mode, el('p', { class: 'viewer-help' }, 'Drag to orbit · Scroll to zoom · Right-drag to pan. Keyboard: focus the preview, then use arrow keys to turn and + / − to zoom.'), references);
  const footer = el('div', { class: 'lens-footer' },
    el('div', { class: 'actions' }, undo, redo, resetAll, state),
    el('div', { class: 'actions' }, ar, save));
  host.replaceChildren(intro, error, el('div', { class: 'lens-layout' }, previewColumn, controls), footer, progress);

  function highlightPhoto() {
    [...photoStrip.children].forEach((photo, index) => photo.classList.toggle('active-reference', index === activePhoto));
  }
  function setBefore(value) {
    before = value;
    beforeButton.setAttribute('aria-pressed', String(before));
    afterButton.setAttribute('aria-pressed', String(!before));
    mode.textContent = before ? 'Before · original version' : 'After · your changes';
    schedulePreview();
  }
  function schedulePreview() {
    if (!ready || raf || disposed) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      try { viewer.applyMaterials(before ? history.original : history.current); }
      catch (cause) { showError(cause); ready = false; refresh(); }
    });
  }
  function changed() { setBefore(false); refresh(); }
  function refresh() {
    const disabled = !ready || busy;
    for (const row of controlRows) {
      const value = row.read(values());
      if (row.type === 'checkbox') row.input.checked = value;
      else row.input.value = value;
      row.output.textContent = row.fields.some(mixed) ? 'Mixed' : row.format(value);
      row.input.disabled = disabled;
      if (row.type === 'range') row.input.setAttribute('aria-valuetext', row.output.textContent);
      row.resetButton.disabled = disabled || targets().every(index => row.fields.every(field => same(history.current[index].values[field], history.original[index].values[field])));
    }
    undo.disabled = disabled || !(history.past.length || (history.pending && !same(history.pending, history.current)));
    redo.disabled = disabled || !history.future.length || !!history.pending;
    resetAll.disabled = disabled || !history.dirty;
    save.disabled = disabled || !history.dirty;
    ar.disabled = disabled;
    eyedropper.disabled = disabled || !photos.length;
    linkControl.disabled = disabled;
    materialChoice.disabled = disabled || linked;
    beforeButton.disabled = afterButton.disabled = disabled;
    for (const view of viewButtons.children) view.disabled = disabled;
    state.textContent = busy ? 'Working…' : history.dirty ? 'Unsaved changes' : 'No changes yet';
    linkHelp.textContent = linked ? 'Changes apply to every lens. Mixed values stay as they are until you change that control.' : 'Only the selected lens changes.';
    if (coatingSwatchRGB) drawShimmerSwatch(swatch, values(), coatingSwatchRGB);
    highlightPhoto();
  }

  const dialogs = new Set();
  function openPhoto(index, picking = false) {
    const photo = photos[index];
    if (!photo) return;
    activePhoto = index; highlightPhoto();
    const kind = viewGroup(photo.kind);
    viewer?.setView(photo.kind, photo.side);
    for (const view of viewButtons.children) view.setAttribute('aria-pressed', String(view.dataset.view === kind));
    const canvas = el('canvas', { id: 'lens-photo-canvas', tabIndex: 0, role: 'img', 'aria-label': `${prettyView(photo)} reference. Use arrow keys to move the sampling point and Enter to pick its colour.` });
    const image = new Image();
    const sample = document.createElement('canvas');
    let x = 0, y = 0, loaded = false;
    const caption = el('p', { class: 'lens-help', id: 'lens-photo-help' });
    canvas.setAttribute('aria-describedby', caption.id);
    const sampleButton = button('Use colour at point', { disabled: true, id: 'lens-photo-sample' });
    const pickToggle = button('Pick a colour', { id: 'lens-photo-pick', 'aria-pressed': String(picking), disabled: !ready || busy });
    const close = button('Done', { onclick: () => dialog.close() });
    const loadingPhoto = el('p', { role: 'status' }, 'Loading reference photo…');
    const dialog = el('dialog', { class: 'lens-photo-dialog', 'aria-labelledby': 'lens-photo-title' },
      el('div', { class: 'lens-dialog-title' }, el('h2', { id: 'lens-photo-title' }, prettyView(photo)), close), loadingPhoto,
      el('div', { class: 'lens-photo-stage' }, canvas), caption, el('div', { class: 'actions' }, pickToggle, sampleButton));
    dialogs.add(dialog); document.body.append(dialog); dialog.showModal();
    const renderPhoto = () => {
      if (!loaded) return;
      const ctx = canvas.getContext('2d'); ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(image, 0, 0);
      if (picking) {
        const radius = Math.max(8, image.width / 100), width = Math.max(1, image.width / 600);
        ctx.strokeStyle = '#fff'; ctx.lineWidth = width * 3;
        const cross = () => { ctx.beginPath(); ctx.moveTo(x - radius, y); ctx.lineTo(x + radius, y); ctx.moveTo(x, y - radius); ctx.lineTo(x, y + radius); ctx.stroke(); };
        cross(); ctx.strokeStyle = '#17212b'; ctx.lineWidth = width; cross();
      }
      canvas.classList.toggle('sampling', picking);
      caption.textContent = picking ? `Click the photo to use its colour. Keyboard: arrows move the point, Shift moves faster, Enter picks. Point ${x + 1}, ${y + 1}.` : 'Enlarged reference photo. Choose Pick a colour to sample a tint.';
      sampleButton.disabled = !picking || !ready || busy;
      pickToggle.setAttribute('aria-pressed', String(picking));
    };
    const pick = () => {
      if (!loaded || !picking || !ready || busy) return;
      try {
        const pixel = sample.getContext('2d', { willReadFrequently: true }).getImageData(x, y, 1, 1).data;
        history.commit(); mutate(['tint'], [...pixel].slice(0, 3).map(value => srgbToLinear(value / 255)), true);
        dialog.close();
      } catch { loadingPhoto.hidden = false; loadingPhoto.textContent = 'This photo could not be sampled. Try another reference photo.'; }
    };
    image.onload = () => {
      if (!dialog.isConnected) return;
      canvas.width = sample.width = image.naturalWidth; canvas.height = sample.height = image.naturalHeight;
      sample.getContext('2d', { willReadFrequently: true }).drawImage(image, 0, 0);
      x = Math.floor(canvas.width / 2); y = Math.floor(canvas.height / 2);
      loaded = true; loadingPhoto.hidden = true; renderPhoto(); if (picking) canvas.focus();
    };
    image.onerror = () => { loadingPhoto.textContent = 'This reference photo could not be loaded. Close it and try another photo.'; pickToggle.disabled = true; };
    image.src = photo.url;
    pickToggle.addEventListener('click', () => { picking = !picking; renderPhoto(); canvas.focus(); });
    sampleButton.addEventListener('click', pick);
    canvas.addEventListener('click', event => {
      if (!loaded || !picking) return;
      const rect = canvas.getBoundingClientRect();
      x = clamp(Math.floor((event.clientX - rect.left) / rect.width * canvas.width), 0, canvas.width - 1);
      y = clamp(Math.floor((event.clientY - rect.top) / rect.height * canvas.height), 0, canvas.height - 1);
      renderPhoto(); pick();
    });
    canvas.addEventListener('keydown', event => {
      if (!loaded || !picking) return;
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(); return; }
      const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
      if (!delta) return;
      event.preventDefault(); const step = event.shiftKey ? 10 : 1;
      x = clamp(x + delta[0] * step, 0, canvas.width - 1); y = clamp(y + delta[1] * step, 0, canvas.height - 1); renderPhoto();
    });
    dialog.addEventListener('close', () => { dialogs.delete(dialog); dialog.remove(); }, { once: true });
  }

  const body = () => ({ source_sha256: dto.source.glb_sha256,
    materials: history.current.map(({ material_index, values }) => ({ material_index, values: clone(values) })) });
  function setBusy(value, message = '') {
    busy = value; progress.hidden = !message; progress.textContent = message; refresh();
  }
  ar.addEventListener('click', async () => {
    if (ar.disabled) return;
    history.commit(); clearError();
    const popup = window.open('about:blank', '_blank');
    if (popup) {
      const doc = popup.document;
      doc.title = 'Preparing AR preview… · Glasses3D';
      doc.documentElement.lang = 'en';
      const viewport = doc.createElement('meta');
      viewport.name = 'viewport'; viewport.content = 'width=device-width, initial-scale=1';
      doc.head.append(viewport);
      const main = doc.createElement('main'), heading = doc.createElement('h1'), message = doc.createElement('p');
      heading.textContent = 'Preparing AR preview…';
      message.textContent = 'Your temporary lens preview is being prepared. This tab will open it automatically when it is ready.';
      message.setAttribute('role', 'status');
      Object.assign(doc.body.style, {margin: '0', padding: 'max(8vh, 2rem) 1.5rem', background: '#f7f8fa', color: '#17212b', font: '16px/1.6 system-ui, sans-serif'});
      Object.assign(main.style, {maxWidth: '38rem', margin: '0 auto', padding: '1.5rem', background: '#fff', border: '1px solid #d6dde5', borderRadius: '6px'});
      Object.assign(heading.style, {fontSize: '1.5rem', marginTop: '0'});
      main.append(heading, message); doc.body.replaceChildren(main);
      popup.opener = null;
    }
    setBusy(true, 'Preparing a temporary AR preview…');
    try {
      const result = await api.post(`/api/deliveries/${key}/lens-colour/preview`, { ...body(), idempotency_key: idempotencyKey() });
      if (result.url) openAR(result.url, popup);
      else {
        if (typeof result.task_key !== 'string') throw new Error('The AR preview did not return a task to follow. Your changes are still here.');
        await pollAR(result.task_key, popup);
      }
    } catch (cause) { popup?.close(); showError(cause); setBusy(false); }
  });
  function openAR(url, popup) {
    if (popup && !popup.closed) popup.location.replace(url);
    setBusy(false, 'AR preview ready. Save as new version when you are happy with the colour.');
    if (!popup || popup.closed) progress.append(' ', el('a', { href: url, target: '_blank', rel: 'noopener' }, 'Open AR preview'));
  }
  async function pollAR(taskKey, popup) {
    if (disposed) return;
    try {
      const task = await api.get(`/api/tasks/${encodeURIComponent(taskKey)}`);
      if (disposed) return;
      if (task.phase === 'done') {
        if (task.result?.url) { clearError(); openAR(task.result.url, popup); }
        else { popup?.close(); showError('The AR preview ended without returning its address. Your changes are still here.'); setBusy(false); }
        return;
      }
      if (task.phase === 'failed') { popup?.close(); showError(task.error || 'The AR preview could not be opened. Your changes are still here.'); setBusy(false); return; }
      progress.textContent = typeof task.progress === 'string' && task.progress ? task.progress : 'Preparing a temporary AR preview…';
      clearError();
    } catch (cause) { showError(cause); progress.textContent = 'Checking the AR preview again shortly. Keep this page open.'; }
    timer = setTimeout(() => pollAR(taskKey, popup), 2000);
  }
  save.addEventListener('click', async () => {
    if (save.disabled) return;
    history.commit(); clearError();
    const payload = body();
    if (!saveRequest || !same(saveRequest.payload, payload)) saveRequest = { payload, id: idempotencyKey() };
    setBusy(true, 'Saving your new version…');
    try {
      const result = await api.post(`/api/deliveries/${key}/lens-colour/save`, { ...payload, idempotency_key: saveRequest.id });
      if (typeof result.task_key !== 'string') throw new Error('The save did not return a task to follow. Your changes are still here.');
      await pollSave(result.task_key);
    } catch (cause) { showError(cause); setBusy(false); }
  });
  async function pollSave(taskKey) {
    if (disposed) return;
    try {
      const task = await api.get(`/api/tasks/${encodeURIComponent(taskKey)}`);
      if (disposed) return;
      if (task.phase === 'done') {
        if (!task.result?.delivery_key) { showError('The save ended without returning a new version. Your changes are still here.'); setBusy(false); return; }
        allowLeave = true; location.assign(`/results/${encodeURIComponent(task.result.delivery_key)}`); return;
      }
      if (task.phase === 'failed') {
        if (!task.recoverable) saveRequest = null;
        const reason = task.error?.message || task.error || 'The new version could not be saved. Your changes are still here.';
        showError(task.recoverable ? `${reason} Retry Save as new version to continue the same save.` : reason);
        setBusy(false); return;
      }
      progress.textContent = typeof task.progress === 'string' && task.progress ? task.progress : 'Saving your new version…';
      clearError();
    } catch (cause) { showError(cause); progress.textContent = 'Checking the save status again shortly. Keep this page open.'; }
    timer = setTimeout(() => pollSave(taskKey), 2000);
  }
  const beforeUnload = event => {
    history.commit();
    if (!allowLeave && history.dirty) { event.preventDefault(); event.returnValue = ''; }
  };
  const leaveLink = event => {
    const anchor = event.target.closest?.('a[href]');
    if (!anchor || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || anchor.target === '_blank' || !history.dirty || allowLeave) return;
    if (!confirm('Leave Lens colour? Your unsaved changes will be lost.')) event.preventDefault();
    else allowLeave = true;
  };
  const shortcut = event => {
    if ((!event.ctrlKey && !event.metaKey) || busy || !ready || dialogs.size) return;
    const key = event.key.toLowerCase();
    if (key !== 'z' && key !== 'y') return;
    event.preventDefault();
    if (key === 'y' || event.shiftKey) history.redo(); else history.undo();
    changed();
  };
  window.addEventListener('beforeunload', beforeUnload);
  document.addEventListener('click', leaveLink);
  document.addEventListener('keydown', shortcut);
  refresh();
  try {
    const renderer = await import('/lens-renderer/lens-renderer.js');
    const { createLensColourViewer } = renderer;
    coatingSwatchRGB = renderer.coatingSwatchRGB;
    viewer = await createLensColourViewer(lensHost, { modelUrl: dto.source.model_url, materials: dto.materials });
    loading.remove(); ready = true; lensHost.setAttribute('aria-busy', 'false'); refresh(); schedulePreview();
  } catch (cause) {
    showError(cause);
    loading.replaceChildren(el('p', {}, 'The live preview could not load.'), button('Try loading again', { onclick: () => location.reload() }));
    lensHost.setAttribute('aria-busy', 'false');
  }
  return () => {
    disposed = true; cancelAnimationFrame(raf); clearTimeout(timer); viewer?.dispose();
    for (const dialog of dialogs) dialog.remove();
    window.removeEventListener('beforeunload', beforeUnload);
    document.removeEventListener('click', leaveLink);
    document.removeEventListener('keydown', shortcut);
  };
}

/** The same thin-film Fresnel calculation as pinned Three, viewed across angles. */
function drawShimmerSwatch(canvas, values, colourAtAngle) {
  const context = canvas.getContext('2d');
  for (let x = 0; x < canvas.width; x++) {
    const cosine = 0.4 + 0.6 * x / (canvas.width - 1);
    const rgb = colourAtAngle(values, cosine);
    context.fillStyle = `rgb(${rgb.join(',')})`; context.fillRect(x, 0, 1, canvas.height);
  }
  canvas.title = 'Coating reflection at different angles. The model preview also shows lighting, gloss and see-through colour.';
}
