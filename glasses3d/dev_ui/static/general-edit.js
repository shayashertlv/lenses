import {api, idempotencyKey} from './api.js';
import {LensHistory, tintToHex, hexToTint} from './lens-colour.js';
import {findCoatingThicknesses} from './coating-match.js';

const clone = value => structuredClone(value);
const labels = {lens: 'Lenses', frame: 'Frame', temple: 'Temples'};
function el(tag, attrs = {}, ...children) {
  const result = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) result.addEventListener(key.slice(2), value);
    else if (key === 'class') result.className = value;
    else if (key in result && !key.startsWith('aria-')) result[key] = value;
    else result.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined) result.append(child);
  return result;
}
const button = (text, attrs = {}) => el('button', {type: 'button', ...attrs}, text);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** One role-driven appearance editor. All generated texture previews load the exact
 * GLB patch that Save uses; there is no separate approximation of gradient pixels. */
export async function generalEditPage(host, key) {
  host.replaceChildren(el('p', {role: 'status'}, 'Loading your model and photos…'));
  const dto = await api.get(`/api/deliveries/${key}/appearance`);
  const rows = dto.materials.map(row => ({...row, values: {...row.values, gradient: row.values.gradient ?? row.gradient ?? null}}));
  const history = new LensHistory(rows), photos = dto.photos || [];
  let viewer, colourAtAngle, selected = 0, activePhoto = 0, before = false, compareKey = key;
  let disposed = false, busy = false, ready = false, allowLeave = false, previewTimer, requestVersion = 0, previewChain = Promise.resolve();
  let lastPick = null, saveRequest = null;
  const dialogs = new Set();
  const error = el('p', {class: 'error', role: 'alert', hidden: true});
  const status = el('span', {id: 'edit-state', role: 'status'}, 'Loading preview…');
  const showError = cause => {error.hidden = false; error.textContent = cause?.message || String(cause);};
  const clearError = () => {error.hidden = true; error.textContent = '';};
  const stage = el('div', {id: 'general-viewer', class: 'viewer general-viewer', 'aria-label': 'AR appearance preview', 'aria-busy': 'true'});
  const photoOverlay = el('img', {class: 'photo-wipe', alt: 'Selected reference photo', hidden: true});
  const viewport = el('div', {class: 'general-viewport'}, stage, photoOverlay);
  const photoNote = el('p', {id: 'photo-angle-note', role: 'status', class: 'muted'});
  const wipe = el('input', {id: 'photo-wipe', type: 'range', min: 0, max: 100, value: 50, 'aria-label': 'Photo and render wipe'});
  const wipeField = el('label', {class: 'wipe-field', hidden: true}, 'Photo / render', wipe);
  wipe.addEventListener('input', () => {photoOverlay.style.clipPath = `inset(0 ${100 - Number(wipe.value)}% 0 0)`;});
  const controls = el('aside', {class: 'general-controls card', 'aria-label': 'Selected part controls'});
  const parts = el('select', {id: 'edit-part', 'aria-label': 'Part and material'});
  for (const role of ['lens', 'frame', 'temple']) {
    const group = el('optgroup', {label: labels[role]});
    rows.forEach((row, i) => {if (row.role === role) group.append(el('option', {value: i}, row.name || `Material ${row.material_index}`));});
    if (group.children.length) parts.append(group);
  }
  parts.addEventListener('change', () => {history.commit(); selected = Number(parts.value); lastPick = null; renderControls();});
  const partHeading = el('h2'), partName = el('p', {class: 'muted'}), fields = el('div');
  const shape = button('Shape wrong here? Ask Astra', {id: 'ask-astra', onclick: openShape});
  controls.append(el('label', {htmlFor: 'edit-part'}, 'Choose a part'), parts, partHeading, partName, fields, shape);
  const references = el('aside', {class: 'general-photos', 'aria-label': 'Your photos'}, el('h2', {}, 'Your photos'));
  photos.forEach((photo, index) => references.append(button('', {'aria-label': `Photo ${index + 1}: ${photo.kind || photo.name}`, onclick: () => selectPhoto(index)},
  )));
  [...references.querySelectorAll('button')].forEach((item, index) => item.append(el('img', {src: photos[index].url, alt: photos[index].name || photos[index].kind}), el('span', {}, (photos[index].kind || 'Reference').replaceAll('_', ' '))));
  if (!photos.length) references.append(el('p', {class: 'muted'}, 'No photos are available for this version.'));
  const views = el('div', {class: 'actions general-views'});
  for (const [kind, name] of [['front', 'Front'], ['angled', 'Angle'], ['side', 'Side'], ['top', 'Top']]) views.append(button(name, {onclick: () => {viewer?.setView(kind); hidePhoto();}}));
  const undo = button('Undo', {id: 'edit-undo', title: 'Ctrl+Z', onclick: () => {history.undo(); changed();}});
  const redo = button('Redo', {id: 'edit-redo', title: 'Ctrl+Y', onclick: () => {history.redo(); changed();}});
  const beforeButton = button('Before', {id: 'edit-before', 'aria-pressed': 'false', onclick: () => compare(true)});
  const afterButton = button('After', {id: 'edit-after', 'aria-pressed': 'true', onclick: () => compare(false)});
  const ar = button('Check in AR', {id: 'edit-ar', onclick: checkAR});
  const save = button('Save as new version', {id: 'edit-save', class: 'primary', onclick: () => saveVersion()});
  const footer = el('div', {class: 'general-footer'}, el('div', {class: 'actions'}, undo, redo, beforeButton, afterButton), status, el('div', {class: 'actions'}, ar, save));
  const versions = el('section', {class: 'versions-strip', 'aria-label': 'Versions'}, el('h2', {}, 'Versions'));
  for (const version of dto.versions || []) {
    const comparison = button('Compare', {'aria-label': `Compare with ${version.label}`, onclick: () => {compareKey = version.delivery_key; compare(true);}});
    versions.append(el('div', {class: 'version-card'}, el('a', {href: `/edit-version/${version.delivery_key}`, 'aria-current': version.delivery_key === key ? 'page' : 'false'}, version.label), comparison));
  }
  const notices = el('div');
  for (const refusal of dto.refusals || []) notices.append(el('p', {class: 'notice'}, typeof refusal === 'string' ? refusal : refusal.message || refusal.reason));
  for (const skipped of dto.look_skipped || []) notices.append(el('p', {class: 'notice'}, `Saved look not applied: ${skipped.material_name || labels[skipped.role] || 'part'} — ${skipped.reason}`));
  host.replaceChildren(el('p', {class: 'general-intro'}, 'Choose a part, adjust its look, and save a new version. Look changes are free. ', el('a', {href: `/results/${key}`}, 'Results')), error,
    el('div', {class: 'general-layout'}, references, el('section', {class: 'general-centre'}, views, viewport, wipeField, photoNote,
      el('p', {class: 'viewer-help'}, 'Click a part to choose it. Drag to turn, scroll to zoom. Keyboard: focus the view, use arrows to turn and + / − to zoom.')), controls), notices, versions, footer);
  const current = () => history.current[selected];
  const values = () => current().values;
  const body = () => ({source_sha256: dto.source.glb_sha256, materials: history.current.map(({material_index, values}) => ({material_index, values: clone(values)}))});
  function refresh() {
    const disabled = busy || !ready || !rows.length;
    undo.disabled = disabled || !(history.past.length || history.pending); redo.disabled = disabled || !history.future.length;
    save.disabled = disabled || !history.dirty; ar.disabled = disabled; shape.disabled = disabled;
    beforeButton.disabled = afterButton.disabled = disabled;
    parts.disabled = disabled;
    controls.querySelectorAll('input, select, button').forEach(control => {if (control !== shape && control !== parts) control.disabled = disabled || control.dataset.unavailable === 'true';});
    status.textContent = busy ? 'Working…' : !ready ? 'Loading preview…' : history.dirty ? 'Unsaved changes' : 'Saved version';
    beforeButton.setAttribute('aria-pressed', String(before)); afterButton.setAttribute('aria-pressed', String(!before));
  }
  function change(mutator, commit = true, rebuild = true) {
    if (busy) return;
    history.update(all => mutator(all[selected].values), commit); clearError(); before = false;
    if (rebuild) renderControls(); refresh(); schedulePreview();
  }
  function changed() {before = false; renderControls(); refresh(); schedulePreview();}
  function hidePhoto() {photoOverlay.hidden = wipeField.hidden = true; photoNote.textContent = ''; stage.style.aspectRatio = '';}
  function selectPhoto(index) {
    activePhoto = index; const photo = photos[index];
    references.querySelectorAll('button').forEach((item, i) => item.setAttribute('aria-pressed', String(i === index)));
    if (!photo.camera) {hidePhoto(); photoNote.textContent = "This photo's angle isn't available for this model"; return;}
    viewer?.setExplicitCamera(photo.camera); stage.style.aspectRatio = `${photo.camera.width} / ${photo.camera.height}`;
    photoOverlay.src = photo.camera_photo_url || photo.url; photoOverlay.hidden = wipeField.hidden = false;
    photoOverlay.style.clipPath = `inset(0 ${100 - Number(wipe.value)}% 0 0)`;
    photoNote.textContent = 'Photo angle matched. Move the slider to compare the photo and model.';
  }
  function renderControls() {
    if (!rows.length) {fields.textContent = 'This model has no materials with an editable part role.'; return;}
    const detailsOpen = fields.querySelector('details')?.open;
    const focusedLabel = fields.contains(document.activeElement) ? document.activeElement.getAttribute('aria-label') : null;
    const row = current(), v = values(); parts.value = selected; partHeading.textContent = labels[row.role]; partName.textContent = row.name;
    fields.replaceChildren();
    const section = el('fieldset', {}, el('legend', {}, 'Colour'));
    fields.append(section);
    if (row.has_colour_texture && !v.gradient) section.append(el('p', {class: 'lens-help'}, 'Colour tints the existing pattern. The photo print stays in place.'));
    if (row.role === 'lens') {
      const modes = el('div', {class: 'actions'});
      modes.append(button('Solid', {id: 'colour-solid', 'aria-pressed': String(!v.gradient), onclick: () => change(next => {next.gradient = null;})}),
        button('Gradient', {id: 'colour-gradient', 'aria-pressed': String(!!v.gradient), 'data-unavailable': String(!row.can_gradient), title: row.can_gradient ? '' : 'This lens already has a colour texture. Solid tint keeps it.', onclick: () => change(next => {
          next.gradient ??= {direction: 'centre_to_sides', colours: [{colour: clone(next.tint), share: 50}, {colour: [1, 1, 1], share: 50}], blend: .5};
        })})); section.append(modes);
      if (!row.can_gradient) section.append(el('p', {class: 'lens-help'}, 'A gradient cannot replace this lens’s existing colour texture. Solid colour tints it.'));
    }
    if (!v.gradient) addColour(section, 'Solid colour', v.tint, colour => change(next => {next.tint = colour;}));
    else {
      v.gradient.colours.forEach((colour, index) => {
        const name = index === 0 ? 'Start' : index === v.gradient.colours.length - 1 ? 'End' : 'Middle';
        addColour(section, `${name} colour`, colour.colour, rgb => change(next => {next.gradient.colours[index].colour = rgb;}));
        const share = el('input', {type: 'number', min: 1, max: 98, step: 1, value: colour.share, 'aria-label': `${name} takes (%)`});
        share.addEventListener('change', () => {
          const value = share.valueAsNumber;
          if (!Number.isFinite(value) || value < 1 || value > 98) {share.setCustomValidity('Enter a share from 1 to 98.'); share.reportValidity(); return;}
          change(next => {const colours = next.gradient.colours, otherTotal = colours.reduce((sum, c, i) => sum + (i === index ? 0 : c.share), 0);
            colours.forEach((c, i) => {c.share = i === index ? value : c.share / otherTotal * (100 - value);});});
        }); section.append(el('label', {}, `${name} takes (%)`, share));
      });
      section.append(button(v.gradient.colours.length === 2 ? 'Add middle colour' : 'Remove middle colour', {id: 'gradient-middle', onclick: () => change(next => {
        const colours = next.gradient.colours;
        if (colours.length === 2) {colours.forEach(c => {c.share *= .7;}); colours.splice(1, 0, {colour: [1, 1, 1], share: 30});}
        else {colours.splice(1, 1); const total = colours[0].share + colours[1].share; colours.forEach(c => {c.share = c.share / total * 100;});}
      })}));
      const direction = el('select', {id: 'gradient-direction', 'aria-label': 'Gradient direction'}, ...[['centre_to_sides', 'Centre to sides'], ['top_to_bottom', 'Top to bottom'], ['left_to_right', 'Left to right']].map(([value, label]) => el('option', {value}, label)));
      direction.value = v.gradient.direction; direction.addEventListener('change', () => change(next => {next.gradient.direction = direction.value;}));
      section.append(el('label', {}, 'Direction', direction));
      slider(section, 'Blend', v.gradient.blend, number => change(next => {next.gradient.blend = number;}, false, false), 'Sharp to soft');
    }
    if (row.can_transmission) slider(fields, 'See-through', v.transmission, number => change(next => {next.transmission = number;}, false, false));
    if (row.role === 'lens' || row.can_transmission) slider(fields, 'Mirror', v.metallic, number => change(next => {next.metallic = number;}, false, false), 'Mirror darkens what you see through.');
    slider(fields, 'Shine', 1 - v.roughness, number => change(next => {next.roughness = 1 - number;}, false, false), 'Matte to glossy');
    if (row.role !== 'lens') slider(fields, 'Metal or plastic', v.metallic, number => change(next => {next.metallic = number;}, false, false), 'Plastic to metal');
    const shimmer = el('select', {id: 'edit-shimmer', 'aria-label': 'Shimmer'}, ...[['0', 'Off'], ['0.25', 'Subtle'], ['1', 'Strong']].map(([value, name]) => el('option', {value}, name)));
    if (![0, .25, 1].includes(v.iridescence)) shimmer.append(el('option', {value: v.iridescence}, 'Custom (see Details)'));
    shimmer.value = v.iridescence; shimmer.addEventListener('change', () => change(next => {next.iridescence = Number(shimmer.value);}));
    fields.append(el('label', {}, 'Shimmer', shimmer), button('Choose shimmer colour', {id: 'choose-shimmer', onclick: chooseShimmer}));
    const details = el('details', {class: 'lens-advanced'}, el('summary', {}, 'Details'));
    details.open = !!detailsOpen;
    fields.append(details);
    for (const [field, label, min, max] of [['transmission', 'Transmission factor', 0, 1], ['metallic', 'Metallic factor', 0, 1], ['roughness', 'Roughness', 0, 1], ['iridescence', 'Iridescence strength', 0, 1], ['ior', 'Lens IOR', 0, null], ['iridescence_ior', 'Film IOR', 1, null], ['iridescence_thickness_min', 'Minimum thickness (nm)', 0, null], ['iridescence_thickness_max', 'Maximum thickness (nm)', 0, null]]) {
      if (field === 'transmission' && !row.can_transmission) continue;
      const input = el('input', {type: 'number', min, step: 'any', value: v[field], 'aria-label': label}); if (max !== null) input.max = max;
      input.addEventListener('change', () => {const number = input.valueAsNumber, valid = Number.isFinite(number) && number >= min && (max === null || number <= max) && (field !== 'ior' || number === 0 || number >= 1);
        input.setCustomValidity(valid ? '' : field === 'ior' ? 'Enter 0 or a finite number of 1 or more.' : 'Enter a finite number within the shown range.');
        if (!valid) {input.reportValidity(); return;} change(next => {next[field] = number;});});
      details.append(el('label', {}, label, input));
    }
    details.append(el('p', {class: 'lens-help'}, 'Thickness endpoints may be reversed. Exact IOR and thickness values have no artificial upper limit.'));
    const copySelect = el('select', {id: 'copy-source', 'aria-label': 'Copy look from version'});
    for (const candidate of dto.copy_sources || []) copySelect.append(el('option', {value: candidate.delivery_key}, candidate.label));
    const copyButton = button(row.role === 'lens' ? 'Copy lens look from another model' : 'Copy look from this job', {'data-unavailable': String(!copySelect.options.length), onclick: async () => {
      try {const result = await api.post(`/api/deliveries/${key}/appearance-copy`, {from_delivery_key: copySelect.value, role: row.role, source_sha256: dto.source.glb_sha256});
        history.update(all => {for (const copied of result.materials) {const target = all.find(item => item.material_index === copied.material_index); if (target) target.values = clone(copied.values);}}, true);
        changed(); const skipped = (result.skipped || []).map(item => typeof item === 'string' ? item : item.message || item.reason || item.name).filter(Boolean);
        if (skipped.length) showError(skipped.join('\n')); else if (!result.materials.length) showError('No matching material names were found in this job.');
      } catch (cause) {showError(cause);}
    }});
    fields.append(el('hr'), el('label', {}, 'Copy a look', copySelect), copyButton, el('p', {class: 'lens-help'}, row.role === 'lens' ? 'Lens looks map by material name; a single source lens can supply every lens. Markings and decals are excluded.' : 'Frame and temple looks copy only between versions of this job, using matching material names.'));
    fields.append(button('Reset this material', {onclick: () => {history.update(all => {all[selected].values = clone(history.original[selected].values);}, true); changed();}}));
    refresh();
    if (focusedLabel) fields.querySelector(`[aria-label="${CSS.escape(focusedLabel)}"]`)?.focus({preventScroll: true});
  }
  function slider(parent, name, value, onInput, note) {
    const input = el('input', {type: 'range', min: 0, max: 1, step: .01, value, 'aria-label': name});
    const output = el('output', {}, `${Math.round(value * 100)}%`);
    input.addEventListener('input', () => {output.textContent = `${Math.round(Number(input.value) * 100)}%`; onInput(Number(input.value));});
    input.addEventListener('change', () => {history.commit(); refresh();});
    parent.append(el('div', {class: 'general-slider'}, el('label', {}, name, output, input), note ? el('p', {class: 'lens-help'}, note) : null));
  }
  function addColour(parent, name, rgb, onColour) {
    const input = el('input', {type: 'color', value: tintToHex(rgb), 'aria-label': name});
    input.addEventListener('change', () => onColour(hexToTint(input.value)));
    parent.append(el('label', {}, name, input), button(`Pick ${name.toLowerCase()} from photo`, {'data-unavailable': String(!photos.length), onclick: () => pickPhoto(onColour)}));
  }
  function dialog(title) {
    const result = el('dialog', {class: 'lens-photo-dialog', 'aria-label': title}, el('h2', {}, title));
    result.append(button('Close', {onclick: () => result.close()})); document.body.append(result); dialogs.add(result);
    result.addEventListener('close', () => {dialogs.delete(result); result.remove();}, {once: true}); result.showModal(); return result;
  }
  function pickPhoto(onColour) {
    if (!photos.length) return;
    const popup = dialog('Pick a colour from your photo'), canvas = el('canvas', {id: 'general-photo-canvas', tabIndex: 0, 'aria-label': 'Photo colour picker. Arrow keys move; Enter chooses.'});
    const hint = el('p', {role: 'status'}), selector = el('select', {'aria-label': 'Photo to sample'}, ...photos.map((photo, index) => el('option', {value: index}, photo.name || photo.kind)));
    selector.value = activePhoto; let sample, x = 0, y = 0;
    const draw = () => {if (!sample) return; const context = canvas.getContext('2d'); context.drawImage(sample, 0, 0); context.strokeStyle = 'red'; context.lineWidth = 2; context.strokeRect(x - 5, y - 5, 10, 10); hint.textContent = `Point ${x + 1}, ${y + 1}. Click to choose, or move with arrows and press Enter.`;};
    const pick = () => {if (!sample) return; const bytes = sample.getContext('2d').getImageData(x, y, 1, 1).data;
      onColour(hexToTint('#' + [...bytes].slice(0, 3).map(n => n.toString(16).padStart(2, '0')).join(''))); popup.close();};
    const load = async () => {try {const image = new Image(); image.src = photos[Number(selector.value)].url; await image.decode();
      sample = document.createElement('canvas'); sample.width = canvas.width = image.naturalWidth; sample.height = canvas.height = image.naturalHeight;
      sample.getContext('2d').drawImage(image, 0, 0); x = Math.floor(canvas.width / 2); y = Math.floor(canvas.height / 2); draw(); canvas.focus();
    } catch {hint.textContent = 'This photo could not be loaded. Choose another photo.';}};
    selector.addEventListener('change', load);
    canvas.addEventListener('click', event => {const box = canvas.getBoundingClientRect(); x = Math.min(canvas.width - 1, Math.max(0, Math.floor((event.clientX - box.left) * canvas.width / box.width))); y = Math.min(canvas.height - 1, Math.max(0, Math.floor((event.clientY - box.top) * canvas.height / box.height))); pick();});
    canvas.addEventListener('keydown', event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); pick(); return;}
      const delta = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[event.key]; if (!delta) return; event.preventDefault();
      x = Math.max(0, Math.min(canvas.width - 1, x + delta[0] * (event.shiftKey ? 10 : 1))); y = Math.max(0, Math.min(canvas.height - 1, y + delta[1] * (event.shiftKey ? 10 : 1))); draw();});
    popup.append(selector, canvas, hint, button('Use colour at point', {onclick: pick})); load();
  }
  function chooseShimmer() {
    const popup = dialog('Choose shimmer colour'), wanted = el('input', {type: 'color', value: '#b0dfff', 'aria-label': 'Shimmer colour'}), matches = el('div');
    function match(rgb) {
      const settings = clone(values()), result = findCoatingThicknesses(rgb, settings, colourAtAngle); matches.replaceChildren();
      if (result.reason) {matches.append(el('p', {}, result.reason)); return;}
      matches.append(el('p', {}, 'Choose the closest look, then turn the model to check it. Shimmer changes with the angle.'));
      result.choices.forEach(({thickness}, i) => {
        const bar = el('canvas', {width: 288, height: 30, class: 'lens-shimmer-swatch', 'aria-label': `Shimmer choice ${i + 1}, side to centre`}), ctx = bar.getContext('2d');
        for (let x = 0; x < bar.width; x++) {ctx.fillStyle = `rgb(${colourAtAngle({...settings, iridescence_thickness_min: thickness, iridescence_thickness_max: thickness}, .4 + .6 * x / (bar.width - 1)).join(',')})`; ctx.fillRect(x, 0, 1, bar.height);}
        matches.append(el('div', {}, bar, button(`Use shimmer ${i + 1}`, {onclick: () => {change(next => {next.iridescence_thickness_min = next.iridescence_thickness_max = thickness;}); popup.close();}})));
      });
    }
    const fromLinear = rgb => match(tintToHex(rgb).slice(1).match(/../g).map(byte => parseInt(byte, 16)));
    popup.append(wanted, button('Find closest shimmer colours', {onclick: () => fromLinear(hexToTint(wanted.value))}), button('Pick shimmer colour from photo', {'data-unavailable': String(!photos.length), disabled: !photos.length, onclick: () => pickPhoto(fromLinear)}), matches);
  }
  function schedulePreview() {
    stage.setAttribute('aria-busy', 'true');
    clearTimeout(previewTimer); const version = ++requestVersion;
    previewTimer = setTimeout(() => {previewChain = previewChain.then(async () => {
      if (!viewer || disposed || version !== requestVersion) return;
      try {
        let url, objectUrl;
        if (before) {const source = compareKey === key ? dto.source : (await api.get(`/api/deliveries/${compareKey}`)); url = source.model_url;}
        else if (!history.dirty) url = dto.source.model_url;
        else {const response = await fetch(`/api/deliveries/${key}/appearance-model`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body())});
          if (!response.ok) {const problem = await response.json(); throw new Error(problem.error?.message || 'The preview could not be updated.');}
          objectUrl = URL.createObjectURL(await response.blob()); url = objectUrl;
        }
        if (version === requestVersion && !disposed) {try {await viewer.load(url, before && compareKey !== key ? [] : rows, {preserveCamera: true});} finally {if (objectUrl) URL.revokeObjectURL(objectUrl);}}
        else if (objectUrl) URL.revokeObjectURL(objectUrl);
      } catch (cause) {if (!disposed) showError(cause);}
      finally {if (!disposed && version === requestVersion) stage.setAttribute('aria-busy', 'false');}
    });}, 180);
  }
  function compare(value) {history.commit(); before = value; refresh(); schedulePreview();}
  async function poll(taskKey) {
    while (!disposed) {const task = await api.get(`/api/tasks/${taskKey}`);
      if (task.phase === 'done') return task.result;
      if (task.phase === 'failed') throw new Error(task.error?.message || task.error || 'The edit could not be completed. Your changes are still here.');
      status.textContent = typeof task.progress === 'string' ? task.progress : 'Working…';
      await new Promise(resolve => setTimeout(resolve, 1000));
    } throw new Error('The page was closed.');
  }
  async function checkAR() {
    if (ar.disabled) return; const popup = window.open('about:blank', '_blank'); if (popup) popup.opener = null;
    busy = true; refresh(); clearError();
    try {const result = await api.post(`/api/deliveries/${key}/appearance-preview`, {...body(), idempotency_key: idempotencyKey()});
      const ready = result.url ? result : await poll(result.task_key); if (popup) popup.location.replace(ready.url);
      else host.append(el('a', {href: ready.url, target: '_blank', rel: 'noopener'}, 'Open AR preview'));
    } catch (cause) {popup?.close(); showError(cause);} finally {busy = false; refresh();}
  }
  async function saveVersion(thenShape = false) {
    if (busy || !history.dirty) return;
    const invalid = controls.querySelector(':invalid'); if (invalid) {invalid.reportValidity(); return;}
    history.commit(); clearError(); busy = true; refresh();
    try {
      const payload = body(); if (!saveRequest || !equal(saveRequest.payload, payload)) saveRequest = {payload, idempotency_key: idempotencyKey()};
      const task = await api.post(`/api/deliveries/${key}/appearance-save`, {...saveRequest.payload, idempotency_key: saveRequest.idempotency_key});
      const result = await poll(task.task_key); if (!result.delivery_key) throw new Error('The save ended without a new version.');
      if (thenShape) await launchShape(result.delivery_key); else {allowLeave = true; location.assign(`/edit-version/${result.delivery_key}`);}
    } catch (cause) {showError(cause);} finally {busy = false; refresh();}
  }
  async function launchShape(deliveryKey) {
    const draft = await api.post('/api/edit-drafts', {delivery_key: deliveryKey});
    if (lastPick) sessionStorage.setItem(`shape-anchor:${draft.draft_id}`, JSON.stringify(lastPick));
    allowLeave = true; location.assign(`/edit/${draft.draft_id}`);
  }
  async function openShape() {
    if (history.dirty) {const popup = dialog('Save your look before asking Astra'); popup.append(el('p', {}, 'Saving keeps these appearance changes so they can be reapplied after Astra edits the shape.'), button('Save look and continue', {class: 'primary', onclick: () => {popup.close(); saveVersion(true);}})); return;}
    busy = true; refresh(); try {await launchShape(key);} catch (cause) {showError(cause); busy = false; refresh();}
  }
  const guard = event => {if (history.dirty && !allowLeave) {event.preventDefault(); event.returnValue = '';}};
  const guardLink = event => {const link = event.target.closest('a[href]'); if (!link || link.target === '_blank' || !history.dirty || allowLeave) return;
    if (!confirm('You have unsaved changes. Leave without saving them?')) event.preventDefault(); else allowLeave = true;};
  const keyboard = event => {if (!(event.ctrlKey || event.metaKey) || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || busy) return;
    const key = event.key.toLowerCase(); if (!['z', 'y'].includes(key)) return; event.preventDefault(); if (key === 'y' || event.shiftKey) history.redo(); else history.undo(); changed();};
  window.addEventListener('beforeunload', guard); document.addEventListener('click', guardLink); document.addEventListener('keydown', keyboard);
  renderControls(); refresh();
  try {
    const renderer = await import('/lens-renderer/lens-renderer.js'); colourAtAngle = renderer.coatingSwatchRGB;
    viewer = await renderer.createLensColourViewer(stage, {modelUrl: dto.source.model_url, materials: rows}); ready = true; stage.setAttribute('aria-busy', 'false'); refresh();
    let down;
    stage.addEventListener('pointerdown', event => {down = [event.clientX, event.clientY];});
    stage.addEventListener('click', event => {
      if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5 || before) return;
      const box = viewer.canvas.getBoundingClientRect(), pick = viewer.pick([(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height]);
      if (!pick) return; const index = rows.findIndex(row => row.material_index === pick.material_index);
      if (index < 0) {showError('This part has no separate editable role, or shares its material across roles.'); return;}
      selected = index; lastPick = pick; renderControls();
    });
  } catch (cause) {showError(cause);}
  return () => {disposed = true; clearTimeout(previewTimer); requestVersion++; viewer?.dispose(); dialogs.forEach(popup => popup.remove());
    window.removeEventListener('beforeunload', guard); document.removeEventListener('click', guardLink); document.removeEventListener('keydown', keyboard);};
}
