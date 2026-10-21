import {labelWarnings} from './label-warnings.js';

const LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
// Upper/lower folding also handles the usual multi-character Unicode case pairs.
const labelKey = label => label.toUpperCase().toLowerCase();
function alphabetLabel(index) {
  let label = '';
  do { label = LETTERS[index % LETTERS.length] + label; index = Math.floor(index / LETTERS.length) - 1; } while (index >= 0);
  return label;
}

export class AnnotationState {
  constructor(labels = []) { this.used = new Set(labels); this.marks = []; this.pending = []; this.tool = 'point'; this.selected = -1; }
  nextLabel() {
    const used = new Set([...this.used, ...this.marks.map(m => m.label)].map(labelKey));
    for (let n = 0; ; n++) { const label = alphabetLabel(n); if (!used.has(labelKey(label))) return label; }
  }
  add(kind, points) {
    this.marks.push({label: this.nextLabel(), kind, radius_px: 9, stroke_px: 4,
      vertices: points.map(xy => ({xy_px: [...xy]}))});
    this.selected = this.marks.length - 1; this.pending = [];
  }
  click(xy, closeRadius = 10) {
    if (this.tool === 'point') { this.add('point', [xy]); return; }
    if (this.tool === 'closed_shape' && this.pending.length >= 3 && Math.hypot(xy[0] - this.pending[0][0], xy[1] - this.pending[0][1]) <= closeRadius) {
      this.add('closed_shape', this.pending); return;
    }
    if (!this.pending.some(p => Math.hypot(p[0] - xy[0], p[1] - xy[1]) < .5)) this.pending.push([...xy]);
  }
  finishLine() {
    if (this.tool !== 'polyline' || this.pending.length < 2) return false;
    this.add('polyline', this.pending); return true;
  }
  rename(index, value) {
    const label = value.trim();
    if (!label) throw new Error('Each mark needs a label.');
    if ([...this.used].some(l => labelKey(l) === labelKey(label)) || this.marks.some((m, i) => i !== index && labelKey(m.label) === labelKey(label))) throw new Error(`Label ${label} is already used in this edit session.`);
    this.marks[index].label = label;
  }
  undo() { if (this.pending.length) this.pending.pop(); else { this.marks.pop(); this.selected = this.marks.length - 1; } }
  deleteSelected() { if (this.selected >= 0) this.marks.splice(this.selected, 1); this.selected = this.marks.length - 1; }
  clear() { this.marks = []; this.pending = []; this.selected = -1; }
}

export function pngCoordinates(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  return [Math.max(0, Math.min(canvas.width - 1e-6, (event.clientX - rect.left) * canvas.width / rect.width)),
    Math.max(0, Math.min(canvas.height - 1e-6, (event.clientY - rect.top) * canvas.height / rect.height))];
}

function drawMark(context, mark, width, height, label = true) {
  const points = mark.vertices.map(v => v.xy_px);
  context.save(); context.strokeStyle = '#e5102d'; context.fillStyle = '#e5102d';
  context.lineWidth = mark.stroke_px; context.lineCap = 'round'; context.lineJoin = 'round'; context.beginPath();
  if (mark.kind === 'point') { context.arc(...points[0], mark.radius_px, 0, 2 * Math.PI); context.fill(); }
  else {
    context.moveTo(...points[0]); for (const point of points.slice(1)) context.lineTo(...point);
    if (mark.kind === 'closed_shape') context.closePath(); context.stroke();
  }
  if (label) {
    context.font = 'bold 24px sans-serif';
    const boxWidth = Math.min(width, context.measureText(mark.label).width + 14), boxHeight = 32;
    const x = Math.max(0, Math.min(width - boxWidth, points[0][0] + 13));
    const y = Math.max(0, Math.min(height - boxHeight, points[0][1] - 39));
    context.fillStyle = '#ffffff'; context.fillRect(x, y, boxWidth, boxHeight);
    context.strokeStyle = '#e5102d'; context.lineWidth = 1; context.strokeRect(x, y, boxWidth, boxHeight);
    context.fillStyle = '#c60020'; context.textBaseline = 'middle'; context.fillText(mark.label, x + 7, y + boxHeight / 2, boxWidth - 14);
  }
  context.restore();
}

export function drawAnnotations(context, marks, width, height) {
  for (const mark of marks) drawMark(context, mark, width, height);
}

const toPng = canvas => new Promise((resolve, reject) => canvas.toBlob(blob =>
  blob ? resolve(blob) : reject(new Error('The annotated image could not be saved.')), 'image/png'));
function element(tag, text, parent) { const el = document.createElement(tag); if (text) el.textContent = text; parent?.append(el); return el; }
function button(text, parent, action) { const b = element('button', text, parent); b.type = 'button'; b.addEventListener('click', action); return b; }

export async function annotateCapture(viewer, {source, labels = [], captureId = 'C1', sessionTexts = [], initialPoint = null, initialPosition = null, onSave}) {
  const frozen = await viewer.capture(), clean = await createImageBitmap(frozen.blob);
  const state = new AnnotationState(labels), dialog = element('dialog', null, document.body);
  if (initialPosition && frozen.projectPoint) state.add('point', [frozen.projectPoint(initialPosition)]);
  else if (initialPoint) state.add('point', [[initialPoint[0] * frozen.width, initialPoint[1] * frozen.height]]);
  dialog.className = 'annotation-dialog'; dialog.dataset.testid = 'annotation-dialog';
  const heading = element('h2', 'Capture and annotate', dialog); heading.id = 'annotation-title'; dialog.setAttribute('aria-labelledby', heading.id);
  const help = element('p', 'Point: click once. Line: click each point, then Finish line, Enter, or double-click. Shape: click each corner, then click the first corner to close it.', dialog);
  help.className = 'annotation-help';
  const tools = element('div', null, dialog); tools.className = 'actions annotation-tools';
  const error = element('p', null, dialog); error.setAttribute('role', 'alert'); error.className = 'error';
  const layout = element('div', null, dialog); layout.className = 'annotation-layout';
  const imageArea = element('div', null, layout); imageArea.className = 'annotation-image';
  const canvas = element('canvas', null, imageArea); canvas.width = frozen.width; canvas.height = frozen.height;
  canvas.dataset.testid = 'annotation-canvas'; canvas.setAttribute('aria-label', 'Captured model for annotation'); canvas.tabIndex = 0;
  const context = canvas.getContext('2d');
  const list = element('div', null, layout); list.dataset.testid = 'mark-list'; list.className = 'mark-list';
  const status = element('p', null, dialog); status.setAttribute('role', 'status'); status.className = 'annotation-status';
  const footer = element('div', null, dialog); footer.className = 'actions annotation-footer';
  let stage = 'drawing', busy = false, annotationBlob, capture, textArea, warnings, done;
  const toolButtons = new Map();
  function setTool(kind) {
    if (state.pending.length) { error.textContent = 'Finish the current line or close the shape first, or use Undo or Clear.'; return; }
    state.tool = kind; error.textContent = ''; redraw(); canvas.focus();
  }
  for (const [name, kind] of [['Point', 'point'], ['Line', 'polyline'], ['Shape', 'closed_shape']]) toolButtons.set(kind, button(name, tools, () => setTool(kind)));
  const finishLine = button('Finish line', tools, () => { state.finishLine(); redraw(); });
  button('Undo', tools, () => { state.undo(); error.textContent = ''; redraw(); });
  const deleteMark = button('Delete selected', tools, () => { state.deleteSelected(); error.textContent = ''; redraw(); });
  button('Clear', tools, () => { state.clear(); error.textContent = ''; redraw(); });

  function redraw() {
    context.clearRect(0, 0, canvas.width, canvas.height); context.drawImage(clean, 0, 0);
    drawAnnotations(context, state.marks, canvas.width, canvas.height);
    if (state.pending.length) {
      context.save(); context.strokeStyle = '#e5102d'; context.fillStyle = '#e5102d'; context.lineWidth = 4; context.setLineDash([10, 8]);
      context.beginPath(); context.moveTo(...state.pending[0]); for (const p of state.pending.slice(1)) context.lineTo(...p); context.stroke();
      context.setLineDash([]); for (const p of state.pending) { context.beginPath(); context.arc(...p, 4, 0, Math.PI * 2); context.fill(); } context.restore();
    }
    list.replaceChildren(); element('h3', 'Labels', list);
    state.marks.forEach((mark, i) => {
      const row = element('div', null, list); row.className = 'mark-row';
      const select = button(`Select ${mark.label}`, row, () => { state.selected = i; redraw(); });
      select.setAttribute('aria-pressed', String(state.selected === i));
      const label = element('label', `Label ${mark.label}`, row), input = element('input', null, label);
      input.value = mark.label; input.setAttribute('aria-label', `Label ${mark.label}`);
      input.addEventListener('input', () => {
        try { state.rename(i, input.value); input.setCustomValidity(''); error.textContent = ''; }
        catch (e) { input.setCustomValidity(e.message); error.textContent = e.message; }
        context.clearRect(0, 0, canvas.width, canvas.height); context.drawImage(clean, 0, 0); drawAnnotations(context, state.marks, canvas.width, canvas.height);
      });
      input.addEventListener('change', () => { if (input.checkValidity()) redraw(); });
      element('small', mark.kind === 'closed_shape' ? 'Closed shape' : mark.kind === 'polyline' ? 'Line' : 'Point', row);
    });
    for (const [kind, b] of toolButtons) b.setAttribute('aria-pressed', String(state.tool === kind));
    finishLine.disabled = state.tool !== 'polyline' || state.pending.length < 2;
    deleteMark.disabled = state.selected < 0;
    annotateDone.disabled = !state.marks.length || !!state.pending.length;
    status.textContent = state.pending.length ? (state.tool === 'closed_shape' ? 'Unfinished shape: click its first corner to close it.' : 'Unfinished line: add points, then finish it.') : `${state.marks.length} mark${state.marks.length === 1 ? '' : 's'}`;
  }
  canvas.addEventListener('click', event => {
    if (stage !== 'drawing' || busy || event.detail > 1) return;
    const invalid = list.querySelector('input:invalid'); if (invalid) { invalid.reportValidity(); return; }
    state.click(pngCoordinates(event, canvas), 10 * frozen.width / canvas.getBoundingClientRect().width); error.textContent = ''; redraw();
  });
  canvas.addEventListener('dblclick', event => { event.preventDefault(); if (stage === 'drawing' && state.tool === 'polyline') { state.finishLine(); redraw(); } });
  canvas.addEventListener('keydown', event => { if (event.key === 'Enter' && stage === 'drawing' && state.tool === 'polyline') { event.preventDefault(); state.finishLine(); redraw(); } });

  const annotateDone = button('Done annotating', footer, async () => {
    if (busy || !state.marks.length || state.pending.length) return;
    const invalid = list.querySelector('input:invalid'); if (invalid) { invalid.reportValidity(); return; }
    busy = true; annotateDone.disabled = true; status.textContent = 'Saving the marked regions…';
    try {
      await new Promise(resolve => requestAnimationFrame(resolve));
      const marks = state.marks.map(mark => ({...mark, vertices: mark.vertices.map(v => ({xy_px: v.xy_px, anchor: frozen.anchorAt(v.xy_px)})), footprint: frozen.footprint(mark)}));
      context.clearRect(0, 0, canvas.width, canvas.height); context.drawImage(clean, 0, 0); drawAnnotations(context, marks, canvas.width, canvas.height);
      annotationBlob = await toPng(canvas);
      capture = {schema: 'g3d.owner_capture/1', capture_id: captureId, source: {...source},
        image: {width_px: frozen.width, height_px: frozen.height}, camera: frozen.camera, marks, request_text: ''};
      stage = 'words'; tools.hidden = true; help.hidden = true; list.hidden = true; annotateDone.hidden = true;
      dialog.classList.add('annotation-words');
      status.textContent = `Labels: ${marks.map(m => m.label).join(', ')}`;
      const words = element('label', 'What should Astra change?', dialog); words.className = 'annotation-request'; textArea = element('textarea', null, words); textArea.rows = 4; textArea.setAttribute('aria-label', 'What should Astra change?');
      warnings = element('ul', null, dialog); warnings.className = 'annotation-warnings'; warnings.dataset.testid = 'annotation-warning'; warnings.setAttribute('aria-live', 'polite');
      function showWarnings() {
        warnings.replaceChildren(); const allLabels = [...labels, ...marks.map(m => m.label)];
        for (const w of labelWarnings(textArea.value, allLabels, marks.map(m => m.label), sessionTexts)) { const li = element('li', w.message, warnings); li.dataset.code = w.code; }
      }
      textArea.addEventListener('input', showWarnings); showWarnings();
      done = button('Done', footer, async () => {
        if (busy) return; busy = true; done.disabled = true; error.textContent = ''; status.textContent = 'Saving item…';
        try { capture.request_text = textArea.value; await onSave({capture, viewer: frozen.blob, annotated: annotationBlob}); dialog.close(); }
        catch (e) { error.textContent = e.message || 'The item could not be saved. Please try again.'; done.disabled = false; }
        finally { busy = false; }
      }); dialog.append(footer); textArea.focus();
    } catch (e) { error.textContent = e.message; annotateDone.disabled = false; }
    finally { busy = false; }
  });
  const cancel = button('Cancel', footer, () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { clean.close(); frozen.dispose(); dialog.remove(); }, {once: true});
  redraw(); dialog.showModal(); canvas.focus();
  return {dialog, state, frozen};
}
