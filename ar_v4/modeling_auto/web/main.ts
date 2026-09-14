import './style.css';
import { ApiError, request, safeArtifactUrl } from './api';
import { ANGLES, LABELS, STAGES, TEST_STAGES } from './types';
import type { Health, Job, JobListItem, Pipeline, Stage } from './types';
import { ModelViewer } from './viewer';

const app = document.querySelector<HTMLDivElement>('#app')!;
const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!);
const title = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);
const dimensionFields = [
  ['frame_width', 'Frame width'], ['lens_width', 'Lens width'], ['lens_height', 'Lens height'],
  ['bridge_width', 'Bridge width'], ['temple_length', 'Temple length'],
] as const;

app.innerHTML = `
  <header class="topbar">
    <a class="brand" href="/" aria-label="Modeling Auto home"><span class="brand-mark">m<span>•</span></span><span>Modeling <strong>Auto</strong><small>REFERENCE TO FINISHED MODEL</small></span></a>
    <div class="topbar-right"><span id="health" class="health">Connecting…</span><button id="settings-open" class="button quiet">API setup</button></div>
  </header>
  <div class="workspace">
    <aside class="sidebar">
      <div class="sidebar-heading"><span>YOUR MODELS</span><button id="new-job" class="icon-button" aria-label="New model">+</button></div>
      <nav id="job-list" aria-label="Saved models"><p class="muted small">Loading saved models…</p></nav>
      <div class="sidebar-note"><span class="tiny-label">THE WORKFLOW</span><p id="workflow-copy">Blank shape. Solid lenses.<br>Clean connections. Texture.<br>One finishing session.</p><span class="muted small">You review the final result.</span></div>
    </aside>
    <main>
      <div id="notice" class="notice" role="alert" hidden></div>
      <nav class="pipeline-picker" aria-label="New run pipeline"><a href="/?pipeline=current" data-pipeline="current">Current pipeline</a><a href="/?pipeline=test" data-pipeline="test">Test pipeline</a><span id="pipeline-caption">Choose the pipeline for a new run.</span></nav>
      <section id="new-panel">
        <div class="page-heading"><div><span class="eyebrow" id="new-eyebrow">NEW MODEL</span><h1>Start with your references.</h1><p>Five photos and your measurements. The complete run ends at a model you can inspect.</p></div><span class="count-badge">01 — 05</span></div>
        <form id="create-form">
          <div class="form-grid">
            <section class="card references-card"><div class="section-title"><h2>Reference photos</h2><span>All five required</span></div><p class="muted small">Use the same model in each photo. These original images guide the lens and finish sessions.</p>
              <div class="upload-grid">${ANGLES.map((angle, i) => `<label class="upload" data-angle="${angle}"><input type="file" name="${angle}" accept="image/jpeg,image/png,image/webp" required aria-label="${title(angle)} reference photo"><span class="upload-number">0${i + 1}</span><img alt="${title(angle)} upload preview" hidden><span class="upload-icon">+</span><strong>${title(angle)}</strong><span class="upload-caption">Choose photo</span></label>`).join('')}</div>
            </section>
            <section class="card specifications"><div class="section-title"><h2>Model details</h2><span>Millimeters</span></div>
              <label class="field">Model name<input name="name" required maxlength="120" placeholder="e.g. Havana round frame" autocomplete="off"></label>
              <p class="muted small">Supply at least three known dimensions. Leave unknown values blank.</p>
              <div class="dimensions">${dimensionFields.map(([key, label]) => `<label class="field">${label}<span class="unit-input"><input type="number" name="${key}" min="0.1" max="1000" step="0.1" inputmode="decimal" aria-label="${label}" placeholder="—"><span>mm</span></span></label>`).join('')}</div>
              <label class="field">Notes <span class="optional">optional</span><textarea name="notes" rows="3" maxlength="4000" placeholder="Details to preserve, lens appearance, or material finish…"></textarea></label>
            </section>
          </div>
          <section class="run-summary"><div><span class="tiny-label">ONE AUTOMATIC RUN</span><h3 id="run-call-summary">2 Meshy requests + 3 Astra sessions</h3><p id="run-sequence">Blank model → smooth & create lenses → fix lens seating → texture → material finish. Start authorizes the whole sequence; it pauses at your final preview.</p><p class="small muted">Meshy: Ultra blank generation, original shape without remeshing; Meshy 7 texturing, 8K PBR. Paid requests use your configured accounts. Calls are never retried automatically.</p></div><button class="button primary large" id="create-submit" type="submit">Start automatic run <span aria-hidden="true">↗</span></button></section>
        </form>
      </section>
      <section id="job-panel" hidden>
        <div class="page-heading"><div><span class="eyebrow" id="job-eyebrow">MODEL</span><h1 id="job-name"></h1><p id="job-message" aria-live="polite"></p></div><span id="job-status" class="status-badge"></span></div>
        <ol id="timeline" class="timeline" aria-label="Pipeline stages"></ol>
        <div id="job-error" class="error-panel" role="alert" hidden></div>
        <section class="viewer-card"><div class="viewer-heading"><div><span class="tiny-label">SAVED MODEL</span><span id="revision-label"></span></div><button id="reset-view" class="button quiet small-button">Reset view</button></div><p id="revision-context" class="viewer-note" hidden></p><div id="viewer"><div id="viewer-status"></div><div class="viewer-hint">DRAG TO ROTATE <span>·</span> SCROLL TO ZOOM</div></div><p class="viewer-note">Use the rendered views below to judge Blender materials.</p></section>
        <div id="actions" class="actions-card"></div>
        <section id="proof-section" class="card" hidden><div class="section-title"><h2>Rendered inspection</h2><span>Current saved revision</span></div><div id="proofs" class="proof-grid"></div><p class="muted small">Inspect the lens surfaces and the connection to the frame. Software checks do not establish visual quality.</p></section>
        <div class="details-grid"><section class="card"><div class="section-title"><h2>Original references</h2><span>Used again for every finish edit</span></div><div id="references" class="reference-strip"></div><div id="measurements" class="measurement-list"></div></section><section class="card"><div class="section-title"><h2>Run record</h2><span id="call-counts"></span></div><p class="muted small">Counts include failed or uncertain requests. Provider invoices determine actual charges.</p><div id="revision-history"></div><details id="inspection"><summary>Technical inspection</summary><pre id="inspection-data"></pre></details></section></div>
      </section>
    </main>
  </div>
  <dialog id="settings-dialog"><form id="settings-form"><div class="section-title"><h2>API setup</h2><button type="button" id="settings-close" class="icon-button" aria-label="Close API setup">×</button></div><p class="muted">Keys are stored only by this local app. Existing keys are never displayed or loaded from the previous pipeline.</p><p id="settings-state" class="small"></p><p id="settings-error" class="error-text small" role="alert" hidden></p><label class="field">OpenAI API key<input type="password" name="openai_key" autocomplete="new-password" placeholder="Leave blank to keep the saved key"></label><label class="field">Meshy API key<input type="password" name="meshy_key" autocomplete="new-password" placeholder="Leave blank to keep the saved key"></label><p class="muted small">Saving a key does not test the provider account or make a paid request.</p><button class="button primary" type="submit">Save API keys</button></form></dialog>
  <dialog id="image-dialog"><button id="image-close" class="icon-button" aria-label="Close image">×</button><img id="image-expanded" alt=""><p id="image-caption"></p></dialog>
`;

function element<T extends HTMLElement = HTMLElement>(id: string): T { return document.getElementById(id) as T; }
const viewer = new ModelViewer(element('viewer'), element('viewer-status'));
const createForm = element<HTMLFormElement>('create-form');
let health: Health | null = null;
let job: Job | null = null;
let jobs: JobListItem[] = [];
let selectedId: string | null = new URLSearchParams(location.search).get('job');
let creationPipeline: Pipeline = new URLSearchParams(location.search).get('pipeline') === 'test' ? 'test' : 'current';
let explicitPipeline = ['current', 'test'].includes(new URLSearchParams(location.search).get('pipeline') ?? '');
let mutation = false;
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let closed = false;
let selectionGeneration = 0;
let renderedJobKey = '';
const uploadUrls = new Map<string, string>();

function pipelineOf(value: { pipeline?: Pipeline }): Pipeline { return value.pipeline === 'test' ? 'test' : 'current'; }
function stageLabel(stage: Stage, pipeline: Pipeline): string {
  return pipeline === 'test' && stage === 'finish' ? 'Material & finish · pass 1' : LABELS[stage];
}
function stagesFor(value: Job): readonly Stage[] {
  const stages = value.pipeline_stages ?? (pipelineOf(value) === 'test' ? TEST_STAGES : STAGES);
  return [...stages.filter(stage => stage !== 'review' && stage !== 'complete'), 'review', 'complete'];
}
function renderPipeline(): void {
  const pipeline = job && selectedId ? pipelineOf(job) : creationPipeline;
  document.querySelectorAll<HTMLAnchorElement>('a[data-pipeline]').forEach(link => {
    if (link.dataset['pipeline'] === pipeline) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  element('pipeline-caption').textContent = job && selectedId ? `${pipeline === 'test' ? 'Test' : 'Current'} pipeline saved with this run. Choose a pipeline to start a new model.` : 'Choose the pipeline for a new run.';
  element('new-eyebrow').textContent = creationPipeline === 'test' ? 'NEW MODEL · TEST PIPELINE' : 'NEW MODEL';
  element('run-call-summary').textContent = creationPipeline === 'test' ? '2 Meshy requests + 4 Astra sessions' : '2 Meshy requests + 3 Astra sessions';
  element('run-sequence').textContent = creationPipeline === 'test'
    ? 'Blank model → smooth & create lenses → inspect new views and lens close-up, then fix lens seating → texture → material finish pass 1 → material finish pass 2. Start authorizes all six requests with no intermediate approvals. Both finish passes inspect originals, fresh model views and the lens close-up. At the final preview, download and finish or request one more material edit.'
    : 'Blank model → smooth & create lenses → fix lens seating → texture → material finish. Start authorizes the whole sequence; it pauses at your final preview.';
  element('workflow-copy').innerHTML = `Blank shape. Solid lenses.<br>Clean connections. Texture.<br>${pipeline === 'test' ? 'Two finishing sessions.' : 'One finishing session.'}`;
}

function notify(message: string, error = true): void {
  const notice = element('notice');
  notice.textContent = message;
  notice.classList.toggle('success', !error);
  notice.hidden = !message;
}
function setBusy(value: boolean): void {
  mutation = value;
  for (const button of document.querySelectorAll<HTMLButtonElement>('button[data-action], #create-submit, #settings-form button[type=submit]')) button.disabled = value;
  createForm.setAttribute('aria-busy', String(value));
}
function renderHealth(): void {
  const openai = health?.keys_present?.openai ?? health?.openai_configured ?? false;
  const meshy = health?.keys_present?.meshy ?? health?.meshy_configured ?? false;
  const configured = openai && meshy;
  element('health').textContent = health ? health.runtime_ready === false ? 'Runtime needs attention' : health.blender_available === false ? 'Blender setup needed' : configured ? 'Local app connected' : 'API setup needed' : 'Disconnected';
  element('health').classList.toggle('connected', Boolean(health && configured && health.runtime_ready !== false && health.blender_available !== false));
  element('settings-state').textContent = `OpenAI: ${openai ? 'key saved' : 'not configured'} · Meshy: ${meshy ? 'key saved' : 'not configured'}`;
}
function renderList(): void {
  element('job-list').innerHTML = jobs.length ? jobs.map(item => `<button class="job-link ${item.id === selectedId ? 'selected' : ''}" data-job="${escape(item.id)}"><span class="job-dot ${escape(item.status)}"></span><span><strong>${escape(item.name)}</strong><small>${item.pipeline === 'test' ? 'Test · ' : ''}${escape(stageLabel(item.stage, pipelineOf(item)) ?? item.stage)} · ${escape(item.status.replaceAll('_', ' '))}</small></span></button>`).join('') : '<p class="muted small">Your saved models will appear here.</p>';
  element('job-list').querySelectorAll<HTMLButtonElement>('[data-job]').forEach(button => button.addEventListener('click', () => void selectJob(button.dataset['job']!)));
}
function imageButton(label: string, url: string, className = ''): string {
  const safe = safeArtifactUrl(url);
  return safe ? `<button class="proof ${className}" data-image="${escape(safe)}" data-caption="${escape(label)}"><img src="${escape(safe)}" alt="${escape(label)}" loading="lazy"><span>${escape(label)}</span></button>` : '';
}
function bindImages(): void {
  document.querySelectorAll<HTMLButtonElement>('button[data-image]').forEach(button => button.addEventListener('click', () => {
    element<HTMLImageElement>('image-expanded').src = button.dataset['image']!;
    element<HTMLImageElement>('image-expanded').alt = button.dataset['caption']!;
    element('image-caption').textContent = button.dataset['caption']!;
    element<HTMLDialogElement>('image-dialog').showModal();
  }));
}
function savedStage(value: Job): Stage | undefined {
  return value.current?.stage ?? value.revisions.find(revision => revision.id === value.current?.id)?.stage;
}
function stagePosition(stage: Stage, value: Job): string {
  const stages: readonly Stage[] = stagesFor(value).filter(item => item !== 'review' && item !== 'complete');
  const index = stages.indexOf(stage);
  return index >= 0 ? `step ${index + 1} of ${stages.length}` : stageLabel(stage, pipelineOf(value));
}
function renderJob(): void {
  renderPipeline();
  element('new-panel').hidden = Boolean(selectedId);
  element('job-panel').hidden = !job || !selectedId;
  if (!job || !selectedId) return;
  const key = `${job.id}:${job.version}:${job.pipeline ?? 'current'}:${job.pipeline_stages?.join(',') ?? ''}:${job.status}:${job.stage}:${job.current?.id ?? ''}:${savedStage(job) ?? ''}:${job.message ?? ''}:${job.error ?? ''}:${job.auth_failure ?? false}:${job.recovery_kind ?? ''}:${job.allowed_actions.join(',')}`;
  if (renderedJobKey === key) return;
  renderedJobKey = key;
  element('job-name').textContent = job.name;
  const pipeline = pipelineOf(job);
  const label = (stage: Stage): string => stageLabel(stage, pipeline);
  const stages = stagesFor(job);
  element('job-eyebrow').textContent = `${pipeline === 'test' ? 'TEST PIPELINE · ' : ''}MODEL ${job.id.slice(0, 8)}`;
  element('job-message').textContent = job.status === 'running'
    ? `${title(stagePosition(job.stage, job))} · ${label(job.stage)} is running. ${job.message || 'The sequence continues automatically through the final preview.'}`
    : job.error ? `Run stopped at ${stagePosition(job.stage, job)}: ${label(job.stage)}.`
    : job.message || 'Every revision and request receipt is retained.';
  element('job-status').textContent = job.status.replaceAll('_', ' ');
  element('job-status').className = `status-badge ${job.status}`;
  const stageIndex = stages.indexOf(job.stage);
  const currentStage = savedStage(job);
  const subsequentStage = Boolean(currentStage && stages.indexOf(currentStage) < stageIndex);
  element('timeline').dataset['pipeline'] = pipeline;
  element('timeline').innerHTML = stages.filter(stage => stage !== 'complete').map((stage, index) => `<li data-stage="${escape(stage)}" class="${index < stageIndex ? 'done' : index === stageIndex ? 'active' : ''}"><span>${index < stageIndex ? '✓' : String(index + 1).padStart(2, '0')}</span><strong>${escape(label(stage))}</strong></li>`).join('');
  element('job-error').hidden = !job.error;
  element('job-error').innerHTML = job.error ? `<strong>${subsequentStage ? 'The next step failed: ' : 'Step failed: '}${escape(label(job.stage))} (${escape(stagePosition(job.stage, job))}).</strong>${job.current ? `<p>${subsequentStage && currentStage ? `The ${escape(label(currentStage))} step already succeeded. ` : ''}Revision ${escape(job.current.id)} remains unchanged and is shown below.</p>` : ''}<details><summary>Failure details</summary><p>${escape(job.error)}</p></details>` : '';
  element('revision-label').textContent = job.current ? `Revision ${job.current.id}${currentStage ? ` · ${label(currentStage)} completed` : ''}` : 'Waiting for first revision';
  const progressContext = job.current && subsequentStage && !['review', 'complete'].includes(job.stage)
    ? job.status === 'running'
      ? `You are viewing the completed ${label(currentStage!)} result while the automatic run works on ${label(job.stage)}.`
      : `This is the completed ${label(currentStage!)} result. The run stopped at ${label(job.stage)}.`
    : '';
  element('revision-context').textContent = progressContext;
  element('revision-context').hidden = !progressContext;
  void viewer.load(safeArtifactUrl(job.current?.model_url));
  renderActions();
  const proofs = job.current?.proofs ?? [];
  element('proof-section').hidden = !proofs.length && !job.current?.closeup_url;
  element('proofs').innerHTML = proofs.map(proof => imageButton(`${title(proof.angle)} view`, proof.url)).join('') + (job.current?.closeup_url ? imageButton('Lens / frame connection close-up', job.current.closeup_url, 'closeup') : '');
  element('references').innerHTML = job.references.map(proof => imageButton(`${title(proof.angle)} reference`, proof.url)).join('');
  element('measurements').innerHTML = Object.entries(job.dimensions).map(([name, value]) => `<span>${escape(dimensionFields.find(item => item[0] === name)?.[1] ?? name.replaceAll('_', ' '))}<strong>${escape(value)} mm</strong></span>`).join('');
  element('call-counts').textContent = `Reserved: ${job.calls.meshy} Meshy · ${job.calls.astra} Astra`;
  element('revision-history').innerHTML = job.revisions.length ? job.revisions.map(revision => `<div class="revision-row"><span><strong>${escape(revision.id)}</strong><small>${escape(revision.stage ? label(revision.stage) : 'Saved revision')}</small></span>${safeArtifactUrl(revision.blend_url) ? `<a href="${escape(safeArtifactUrl(revision.blend_url))}" download>Save .blend <span aria-hidden="true">↓</span></a>` : ''}</div>`).join('') : '<p class="muted small">Revisions appear after completed Blender operations.</p>';
  element('inspection-data').textContent = JSON.stringify(job.current?.inspection ?? {}, null, 2);
  bindImages();
}
function renderActions(): void {
  if (!job) return;
  const testPipeline = pipelineOf(job) === 'test';
  const label = (stage: Stage): string => stageLabel(stage, pipelineOf(job!));
  const allowed = new Set(job.allowed_actions);
  let content = '';
  if (allowed.has('start')) content += `<div><h2>Ready for an automatic run</h2><p>Start authorizes 2 Meshy requests and ${testPipeline ? '4' : '3'} Astra sessions, continuing through the final preview. No intermediate approvals.${testPipeline ? ' Two material/finish passes follow texturing; each receives originals, fresh rendered views and the lens close-up.' : ''}</p></div><button class="button primary" data-action="start">Start automatic run</button>`;
  if (allowed.has('cancel')) content += '<div><h2>Run in progress</h2><p>Saved revisions and receipts are retained. Cancel stops local work; a provider may already have charged for a submitted request.</p></div><button class="button danger" data-action="cancel">Cancel run</button>';
  if (job.auth_failure || allowed.has('retry_auth')) {
    content += '<div><h2>Astra authentication needs attention</h2><p>Check or replace the OpenAI API key in API setup. Paste the key itself, without a Bearer prefix or surrounding quotes. Saving keys does not send a provider request or restart this run.</p>';
    if (allowed.has('retry_auth')) content += `<p>Retry sends one new paid Astra request for <strong>${escape(label(job.stage))}</strong> using your saved model, then continues the remaining stages of the original run. Completed stages are not repeated. Nothing retries automatically.</p>`;
    content += '</div><div class="review-buttons"><button class="button secondary" id="auth-settings-open">Update OpenAI API key</button>';
    if (allowed.has('retry_auth')) content += '<button class="button primary" data-action="retry_auth">Retry Astra &amp; continue</button>';
    content += '</div>';
  }
  if (allowed.has('recover')) content += job.recovery_kind === 'astra_script'
    ? `<div><h2>Continue from the saved Astra edit</h2><p>Apply the saved <strong>${escape(label(job.stage))}</strong> script locally in Blender. This step sends no new Astra request. If it succeeds, the remaining authorized stages continue automatically, including their provider requests.</p><p>Your displayed revision stays saved. A new revision appears only after the edit passes local checks.</p></div><button class="button primary" data-action="recover">Apply saved Astra edit &amp; continue</button>`
    : '<div><h2>Saved work can be recovered</h2><p>Recovery reuses saved work, then continues the already authorized run. It does not repeat a completed request.</p></div><button class="button primary" data-action="recover">Recover saved work</button>';
  if (allowed.has('edit') || allowed.has('accept')) {
    const atFinalReview = job.status === 'waiting' && job.stage === 'review';
    const testReview = atFinalReview
      ? 'Both material/finish passes are complete. Download the exact packed Blender model and finish this run, or send specific instructions for one more Astra material/finish edit.'
      : 'The run stopped before the latest material/finish edit completed. Your displayed saved revision remains available. Send specific instructions to request one new Astra material/finish edit.';
    content += `<div class="review-copy"><span class="tiny-label">${testPipeline && !atFinalReview ? 'CONTINUE FROM SAVED MODEL' : 'YOUR REVIEW'}</span><h2>${testPipeline && !atFinalReview ? 'Choose how to continue.' : 'Is this the result you want?'}</h2><p>${testPipeline ? `${testReview} Each extra edit uses your five original photos, five fresh model views and the lens close-up, then returns here. Geometry remains protected.` : 'Inspect the model and rendered views. Another edit authorizes one Astra material/finish session using your five original photos, then returns here.'}</p>`;
    if (allowed.has('edit')) content += `<label class="field">${testPipeline ? 'Specific edit instructions' : 'Finish instructions'} <span class="optional">${testPipeline ? 'required for another edit' : 'optional'}</span><textarea id="edit-notes" rows="2" maxlength="4000" ${testPipeline ? 'required' : ''} aria-describedby="edit-error" placeholder="e.g. warmer tortoise color, softer sheen on the frame…"></textarea></label><p id="edit-error" class="error-text small" role="alert" hidden></p>`;
    content += '</div><div class="review-buttons">';
    if (allowed.has('accept')) content += `<button class="button primary" data-action="accept">${testPipeline ? 'Download & finish run' : 'Accept & download .blend'} <span aria-hidden="true">↓</span></button>`;
    if (allowed.has('edit')) content += `<button class="button secondary" data-action="edit">${testPipeline ? 'Send another edit' : 'Another material edit'}</button>`;
    content += '</div>';
  }
  if (job.accepted) {
    const url = safeArtifactUrl(job.accepted.url);
    content += `<div><span class="tiny-label">ACCEPTED REVISION</span><h2>Your packed Blender file is ready.</h2><p class="hash">SHA-256 ${escape(job.accepted.sha256)}</p></div>${url ? `<a id="accepted-download" class="button primary" href="${escape(url)}" download>Download accepted .blend <span aria-hidden="true">↓</span></a>` : '<p class="error-text">The accepted download link is unavailable.</p>'}`;
  }
  if (!content) content = job.status === 'running'
    ? '<div><h2>Stopping the run…</h2><p>Waiting for the current operation to stop. Saved models and request receipts remain available.</p></div>'
    : '<div><h2>Run stopped</h2><p>Saved models and receipts remain available. This page will not retry or submit requests automatically.</p></div>';
  element('actions').innerHTML = content;
  element('actions').querySelectorAll<HTMLButtonElement>('button[data-action]').forEach(button => {
    button.disabled = mutation;
    button.addEventListener('click', () => void performAction(button.dataset['action']!));
  });
  element('auth-settings-open')?.addEventListener('click', () => {
    element<HTMLDialogElement>('settings-dialog').showModal();
    element<HTMLFormElement>('settings-form').querySelector<HTMLInputElement>('[name=openai_key]')?.focus();
  });
}
async function loadJobs(): Promise<void> {
  const response = await request<{ jobs: JobListItem[] }>('/api/jobs');
  jobs = response.jobs;
  renderList();
}
async function loadSelected(generation = selectionGeneration): Promise<void> {
  const id = selectedId;
  if (!id) return;
  const result = await request<Job>(`/api/jobs/${encodeURIComponent(id)}`);
  if (generation !== selectionGeneration || id !== selectedId) return;
  if (job?.id === id && result.version < job.version) return;
  job = result;
  renderJob();
}
async function selectJob(id: string | null, push = true): Promise<void> {
  ++selectionGeneration;
  selectedId = id;
  job = null;
  renderedJobKey = '';
  notify('');
  if (push) history.pushState({}, '', id ? `/?job=${encodeURIComponent(id)}` : explicitPipeline ? `/?pipeline=${creationPipeline}` : '/');
  renderList();
  renderJob();
  void viewer.load(null);
  if (id) {
    try { await loadSelected(); } catch (error) { notify(error instanceof Error ? error.message : 'Could not load this model.'); }
  }
}
async function performAction(action: string): Promise<void> {
  if (mutation || !job || !job.allowed_actions.includes(action)) return;
  const currentId = job.id;
  const generation = selectionGeneration;
  const payload: { version: number; notes?: string } = { version: job.version };
  if (action === 'edit') {
    const notes = element<HTMLTextAreaElement>('edit-notes')?.value.trim();
    if (pipelineOf(job) === 'test' && !notes) {
      element('edit-error').textContent = 'Write specific material or finish instructions before sending another edit.';
      element('edit-error').hidden = false;
      element<HTMLTextAreaElement>('edit-notes').focus();
      return;
    }
    if (notes) payload.notes = notes;
  }
  setBusy(true);
  notify('');
  try {
    const updated = await request<Job>(`/api/jobs/${encodeURIComponent(currentId)}/${action}`, { method: 'POST', body: JSON.stringify(payload) });
    if (generation === selectionGeneration && selectedId === currentId) { job = updated; renderedJobKey = ''; renderJob(); }
    await loadJobs();
    if (action === 'accept' && updated.accepted) {
      const url = safeArtifactUrl(updated.accepted.url);
      if (url) { const link = document.createElement('a'); link.href = url; link.download = ''; document.body.append(link); link.click(); link.remove(); }
    }
  } catch (error) {
    notify(error instanceof ApiError && error.status === 409 ? `${error.message} The current state has been refreshed; no request was repeated.` : error instanceof Error ? error.message : 'The action failed.');
    try { await loadSelected(); } catch { /* Keep the last visible model if refresh also fails. */ }
  } finally { setBusy(false); }
}
createForm.addEventListener('submit', event => {
  event.preventDefault();
  void (async () => {
    if (mutation || !createForm.reportValidity()) return;
    const data = new FormData(createForm);
    const dimensions: Record<string, number> = {};
    for (const [key] of dimensionFields) { const raw = String(data.get(key) ?? '').trim(); if (raw) dimensions[key] = Number(raw); data.delete(key); }
    if (Object.keys(dimensions).length < 3) { notify('Enter at least three known dimensions in millimeters.'); return; }
    for (const angle of ANGLES) {
      const file = data.get(angle);
      if (!(file instanceof File) || !file.size) { notify(`Choose a ${angle} reference photo.`); return; }
      if (file.size > 24 * 1024 * 1024) { notify(`The ${angle} photo exceeds 24 MB. Choose a smaller image.`); return; }
    }
    data.set('dimensions', JSON.stringify(dimensions));
    if (explicitPipeline || creationPipeline === 'test') data.set('pipeline', creationPipeline);
    setBusy(true);
    notify('');
    let createdId: string | null = null;
    try {
      const created = await request<Job>('/api/jobs', { method: 'POST', body: data });
      createdId = created.id;
      await selectJob(created.id);
      const started = await request<Job>(`/api/jobs/${encodeURIComponent(created.id)}/start`, { method: 'POST', body: JSON.stringify({ version: created.version }) });
      if (selectedId === created.id) { job = started; renderedJobKey = ''; renderJob(); }
      await loadJobs();
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Could not start this run.');
      if (createdId && selectedId === createdId) { try { await loadSelected(); } catch { /* Saved draft remains on the server. */ } }
    } finally { setBusy(false); }
  })();
});
createForm.querySelectorAll<HTMLInputElement>('input[type=file]').forEach(input => input.addEventListener('change', () => {
  const old = uploadUrls.get(input.name);
  if (old) URL.revokeObjectURL(old);
  const label = input.closest<HTMLLabelElement>('label')!;
  const preview = label.querySelector<HTMLImageElement>('img')!;
  const file = input.files?.[0];
  label.classList.toggle('has-image', Boolean(file));
  preview.hidden = !file;
  if (file) { const url = URL.createObjectURL(file); uploadUrls.set(input.name, url); preview.src = url; }
  else { preview.removeAttribute('src'); uploadUrls.delete(input.name); }
  label.querySelector('.upload-caption')!.textContent = file?.name ?? 'Choose photo';
}));
document.querySelectorAll<HTMLAnchorElement>('a[data-pipeline]').forEach(link => link.addEventListener('click', event => {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (mutation) return;
  creationPipeline = link.dataset['pipeline'] === 'test' ? 'test' : 'current';
  explicitPipeline = true;
  void selectJob(null);
}));
element('new-job').addEventListener('click', () => {
  if (mutation) return;
  if (job) { creationPipeline = pipelineOf(job); explicitPipeline = creationPipeline === 'test'; }
  void selectJob(null);
});
element('reset-view').addEventListener('click', () => viewer.resetView());
element('settings-open').addEventListener('click', () => element<HTMLDialogElement>('settings-dialog').showModal());
element('settings-close').addEventListener('click', () => element<HTMLDialogElement>('settings-dialog').close());
element('image-close').addEventListener('click', () => element<HTMLDialogElement>('image-dialog').close());
element<HTMLFormElement>('settings-form').addEventListener('submit', event => {
  event.preventDefault();
  void (async () => {
    if (mutation) return;
    const form = event.currentTarget as HTMLFormElement;
    const data = new FormData(form);
    const payload: Record<string, string> = {};
    for (const key of ['openai_key', 'meshy_key']) { const value = String(data.get(key) ?? '').trim(); if (value) payload[key] = value; }
    if (!Object.keys(payload).length) { element<HTMLDialogElement>('settings-dialog').close(); return; }
    element('settings-error').hidden = true;
    setBusy(true);
    try {
      health = await request<Health>('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
      form.reset(); renderHealth(); element<HTMLDialogElement>('settings-dialog').close(); notify('API keys saved locally. No provider request was sent.', false);
    } catch (error) {
      element('settings-error').textContent = error instanceof Error ? error.message : 'Could not save API keys.';
      element('settings-error').hidden = false;
    }
    finally { setBusy(false); }
  })();
});
window.addEventListener('popstate', () => {
  const parameters = new URLSearchParams(location.search);
  creationPipeline = parameters.get('pipeline') === 'test' ? 'test' : 'current';
  explicitPipeline = ['current', 'test'].includes(parameters.get('pipeline') ?? '');
  void selectJob(parameters.get('job'), false);
});
async function poll(): Promise<void> {
  if (closed) return;
  try {
    const [nextHealth] = await Promise.all([request<Health>('/api/health'), loadSelected(), loadJobs()]);
    health = nextHealth;
    renderHealth();
  } catch {
    health = null;
    renderHealth();
  } finally { if (!closed) pollTimer = setTimeout(() => void poll(), document.hidden ? 8000 : 2500); }
}
window.addEventListener('pagehide', event => {
  if (event.persisted) return; // A cached page keeps its viewer and GET polling for browser Back.
  closed = true; clearTimeout(pollTimer); viewer.dispose(); uploadUrls.forEach(url => URL.revokeObjectURL(url));
});
renderJob();
void poll();
