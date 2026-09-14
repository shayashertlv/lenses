import { expect, test, type Page, type Route } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import type { Job } from '../../web/types';
import { STAGES, TEST_STAGES } from '../../web/types';

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64');
const BLEND_BYTES = Buffer.from('BLENDER-SYNTHETIC-EXACT-ACCEPTED-ARTIFACT');
const BLEND_SHA = createHash('sha256').update(BLEND_BYTES).digest('hex');
const fixtureId = 'synthetic-ui-job';
const artifactRoot = process.env['MODELING_AUTO_BROWSER_ARTIFACT_ROOT'] ?? 'data/browser/test-pipeline-20260913';
const file = (name: string): string => `/api/jobs/${fixtureId}/files/${name}`;
const angles = ['front', 'back', 'left', 'right', 'angled'];
function glb(): Buffer {
  const document = { asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }], meshes: [{ primitives: [{ attributes: { POSITION: 0 }, mode: 4 }] }], buffers: [{ byteLength: 36 }], bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }], accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [-1, -1, 0], max: [1, 1, 0] }] };
  let json = JSON.stringify(document);
  while (json.length % 4) json += ' ';
  const header = Buffer.alloc(12), jsonHeader = Buffer.alloc(8), binHeader = Buffer.alloc(8);
  const binary = Buffer.alloc(36);
  [-1, -1, 0, 1, -1, 0, 0, 1, 0].forEach((value, index) => binary.writeFloatLE(value, index * 4));
  header.writeUInt32LE(0x46546c67, 0); header.writeUInt32LE(2, 4); header.writeUInt32LE(12 + 8 + json.length + 8 + binary.length, 8);
  jsonHeader.writeUInt32LE(json.length, 0); jsonHeader.writeUInt32LE(0x4e4f534a, 4);
  binHeader.writeUInt32LE(binary.length, 0); binHeader.writeUInt32LE(0x004e4942, 4);
  return Buffer.concat([header, jsonHeader, Buffer.from(json), binHeader, binary]);
}
function makeJob(status = 'waiting'): Job {
  return {
    id: fixtureId, name: 'Synthetic fixture — no paid calls', version: 7, status,
    stage: status === 'draft' ? 'generate' : 'review', message: 'Synthetic browser fixture. Review the saved result.', error: null,
    notes: '', dimensions: { frame_width: 140, lens_width: 48, lens_height: 42 },
    references: angles.map(angle => ({ angle, url: file(`ref-${angle}`) })),
    current: status === 'draft' ? null : { id: 'r05', blend_url: file('r05-blend'), model_url: file('r05-glb'), proofs: angles.map(angle => ({ angle, url: file(`r05-${angle}`) })), closeup_url: file('r05-closeup'), inspection: { geometry_sha256: 'synthetic-geometry-digest', warnings: ['Synthetic fixture; visual quality is unmeasured.'] } },
    revisions: status === 'draft' ? [] : [{ id: 'r05', stage: 'finish', blend_url: file('r05-blend'), model_url: file('r05-glb') }],
    proposal: null, calls: { meshy: status === 'draft' ? 0 : 2, astra: status === 'draft' ? 0 : 3 },
    allowed_actions: status === 'draft' ? ['start'] : ['edit', 'accept'], accepted: null,
  };
}
function lensSeatingJob(status = 'running'): Job {
  const value = makeJob(status);
  value.stage = 'connections';
  value.current = {
    id: 'r001', stage: 'lenses', blend_url: file('r001-blend'), model_url: file('r001-glb'),
    proofs: angles.map(angle => ({ angle, url: file(`r001-${angle}`) })),
    closeup_url: file('r001-closeup'), inspection: { geometry_sha256: 'completed-lens-edit' },
  };
  value.revisions = [{ id: 'r000', stage: 'generate', blend_url: file('r000-blend'), model_url: file('r000-glb') }, value.current];
  value.allowed_actions = status === 'running' ? ['cancel'] : ['recover'];
  value.message = 'Astra is checking lens seating using the completed lens edit.';
  value.calls = { meshy: 1, astra: 3 };
  if (status === 'failed') {
    value.error = "Astra Python failed local validation: Attribute '__len__' is outside the Blender editing contract. Saved response retained; no Blender edit executed";
    value.recovery_kind = 'astra_script';
  }
  return value;
}
function makeTestJob(status = 'waiting'): Job {
  const value = makeJob(status);
  value.pipeline = 'test';
  value.pipeline_stages = TEST_STAGES.filter(stage => stage !== 'review' && stage !== 'complete');
  if (value.current) {
    value.current.stage = 'finish_refine';
    value.revisions[0]!.stage = 'finish_refine';
    value.calls.astra = 4;
  }
  return value;
}
type Mutation = { path: string; body: unknown };
async function fixture(page: Page, initial = makeJob()) {
  let state = structuredClone(initial);
  const mutations: Mutation[] = [];
  let configured = true;
  let conflict = false;
  let delay = 0;
  let connectionFailure = false;
  const health = () => ({ keys_present: { openai: configured, meshy: configured }, runtime_ready: true, blender_available: true, active_job_id: state.status === 'running' ? fixtureId : null, ready: configured });
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === file('accepted-exact-blend') && request.method() === 'GET') { await route.continue(); return; }
    if (connectionFailure) { await route.abort(); return; }
    if (request.method() === 'GET') {
      if (path === '/api/health') { await route.fulfill({ json: health() }); return; }
      if (path === '/api/jobs') { await route.fulfill({ json: { jobs: [state] } }); return; }
      if (path === `/api/jobs/${fixtureId}`) { await route.fulfill({ json: state }); return; }
      if (path.includes('/files/')) {
        const name = path.split('/').at(-1)!;
        if (name.endsWith('glb')) await route.fulfill({ contentType: 'model/gltf-binary', body: glb() });
        else if (name.includes('blend')) await route.fulfill({ contentType: 'application/octet-stream', headers: { 'Content-Disposition': 'attachment; filename="synthetic-accepted.blend"' }, body: BLEND_BYTES });
        else await route.fulfill({ contentType: 'image/png', body: PNG });
        return;
      }
    }
    if (request.method() === 'POST') {
      let body: unknown = request.postData();
      try { body = request.postDataJSON(); } catch { /* The creation request is multipart. */ }
      mutations.push({ path, body });
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      if (conflict) { conflict = false; await route.fulfill({ status: 409, json: { detail: 'The job changed before this action.' } }); return; }
      if (path === '/api/settings') { configured = true; await route.fulfill({ json: health() }); return; }
      if (path === '/api/jobs') {
        const selectedPipeline = /name="pipeline"\r?\n\r?\ntest\r?\n/.test(String(body)) ? 'test' : 'current';
        state = selectedPipeline === 'test' ? makeTestJob('draft') : makeJob('draft');
        await route.fulfill({ json: state }); return;
      }
      state.version += 1;
      if (path.endsWith('/start')) { state.status = 'running'; state.stage = 'generate'; state.allowed_actions = ['cancel']; state.message = 'Generating the blank model.'; }
      else if (path.endsWith('/cancel')) { state.status = 'cancelled'; state.allowed_actions = ['recover']; state.message = 'Cancelled with a saved task.'; }
      else if (path.endsWith('/recover')) { state.status = 'running'; state.allowed_actions = ['cancel']; state.error = null; state.recovery_kind = null; state.message = 'Recovering the saved request.'; }
      else if (path.endsWith('/retry_auth')) { state.status = 'running'; state.allowed_actions = ['cancel']; state.auth_failure = false; state.error = null; state.calls.astra += 1; state.message = 'Retrying the rejected Astra request, then continuing the remaining run.'; }
      else if (path.endsWith('/edit')) { state.status = 'running'; state.stage = state.pipeline === 'test' ? 'finish_refine' : 'finish'; state.allowed_actions = ['cancel']; state.calls.astra += 1; state.message = 'Editing materials from the original five references.'; }
      else if (path.endsWith('/accept')) { state.status = 'complete'; state.stage = 'complete'; state.allowed_actions = []; state.accepted = { sha256: BLEND_SHA, url: file('accepted-exact-blend') }; state.message = 'Accepted exact saved revision.'; }
      else { await route.fulfill({ status: 404, json: { detail: 'Unknown fixture action' } }); return; }
      await route.fulfill({ json: state }); return;
    }
    await route.fulfill({ status: 404, json: { detail: 'Unknown synthetic fixture route' } });
  });
  return { mutations, get state() { return state; }, set state(value: Job) { state = value; }, setConfigured(value: boolean) { configured = value; }, nextConflict() { conflict = true; }, delay(value: number) { delay = value; }, disconnect(value: boolean) { connectionFailure = value; } };
}
async function fillNew(page: Page) {
  await page.getByRole('textbox', { name: 'Model name' }).fill('Five-photo synthetic frame');
  await page.getByRole('spinbutton', { name: 'Frame width', exact: true }).fill('140');
  await page.getByRole('spinbutton', { name: 'Lens width', exact: true }).fill('48');
  await page.getByRole('spinbutton', { name: 'Lens height', exact: true }).fill('42');
  for (const angle of angles) await page.getByLabel(`${angle[0]!.toUpperCase()}${angle.slice(1)} reference photo`).setInputFiles({ name: `${angle}.png`, mimeType: 'image/png', buffer: PNG });
}

test('new run requires five photos and three dimensions, then makes exactly one explicit start', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto('/');
  await fillNew(page);
  await page.getByRole('spinbutton', { name: 'Lens height', exact: true }).fill('');
  await page.getByRole('button', { name: 'Start automatic run' }).click();
  await expect(page.getByRole('alert')).toContainText('at least three');
  expect(mock.mutations).toEqual([]);
  await page.getByRole('spinbutton', { name: 'Lens height', exact: true }).fill('42');
  await page.getByRole('button', { name: 'Start automatic run' }).click();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations.map(item => item.path)).toEqual(['/api/jobs', `/api/jobs/${fixtureId}/start`]);
  const multipart = String(mock.mutations[0]!.body);
  for (const angle of angles) expect(multipart).toContain(`name="${angle}"`);
  expect(multipart).toContain('"frame_width":140');
  expect(multipart).not.toContain('name="pipeline"');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toHaveLength(2);
});

test('current GLB, exactly five rendered views and close-up, and five original references are shown', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#viewer-status')).toHaveText('');
  await expect(page.locator('.viewer-note').last()).toHaveText('Use the rendered views below to judge Blender materials.');
  await expect(page.locator('#call-counts')).toHaveText('Reserved: 2 Meshy · 3 Astra');
  await expect(page.getByText('Counts include failed or uncertain requests. Provider invoices determine actual charges.')).toBeVisible();
  await expect(page.locator('#proofs button')).toHaveCount(6);
  await expect(page.locator('#references button')).toHaveCount(5);
  await expect(page.locator('#proofs button.closeup img')).toHaveAttribute('src', new RegExp('r05-closeup$'));
  await page.getByRole('button', { name: 'Lens / frame connection close-up' }).click();
  await expect(page.locator('#image-dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Close image', exact: true }).click();
  expect(mock.mutations).toEqual([]);
  await page.screenshot({ path: `${artifactRoot}/review-desktop.png`, fullPage: true });
});

test('another edit authorizes only finish once, preserving notes and original reference display', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto(`/?job=${fixtureId}`);
  await page.getByRole('textbox', { name: 'Finish instructions' }).fill('Use a softer gloss and warmer frame color.');
  mock.delay(200);
  await page.getByRole('button', { name: 'Another material edit' }).dblclick();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toEqual([{ path: `/api/jobs/${fixtureId}/edit`, body: { version: 7, notes: 'Use a softer gloss and warmer frame color.' } }]);
  expect(mock.state.stage).toBe('finish');
  expect(mock.state.calls).toEqual({ meshy: 2, astra: 4 });
  await expect(page.locator('#references button')).toHaveCount(5);
});

test('accept downloads the exact accepted artifact; reload never accepts or downloads again', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto(`/?job=${fixtureId}`);
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Accept & download .blend' }).click();
  const download = await downloaded;
  expect(download.url()).toBe(`http://127.0.0.1:8061${file('accepted-exact-blend')}`);
  await download.saveAs(`${artifactRoot}/synthetic-accepted-download.blend`);
  expect(createHash('sha256').update(await readFile(`${artifactRoot}/synthetic-accepted-download.blend`)).digest('hex')).toBe(BLEND_SHA);
  await expect(page.locator('#accepted-download')).toHaveAttribute('href', download.url());
  await expect(page.locator('.hash')).toContainText(BLEND_SHA);
  expect(mock.mutations.map(item => item.path)).toEqual([`/api/jobs/${fixtureId}/accept`]);
  await page.reload();
  await expect(page.locator('#accepted-download')).toBeVisible();
  expect(mock.mutations).toHaveLength(1);
});

test('cancel preserves saved preview; restart does not resume; explicit recovery is one action', async ({ page }) => {
  const running = makeJob(); running.status = 'running'; running.stage = 'finish'; running.allowed_actions = ['cancel'];
  const mock = await fixture(page, running);
  await page.goto(`/?job=${fixtureId}`);
  await page.getByRole('button', { name: 'Cancel run' }).click();
  await expect(page.getByRole('button', { name: 'Recover saved work' })).toBeVisible();
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish completed');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Recover saved work' })).toBeVisible();
  expect(mock.mutations.map(item => item.path)).toEqual([`/api/jobs/${fixtureId}/cancel`]);
  await page.getByRole('button', { name: 'Recover saved work' }).click();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations.map(item => item.path)).toEqual([`/api/jobs/${fixtureId}/cancel`, `/api/jobs/${fixtureId}/recover`]);
});

test('failed job displays its exact error and last good revision without automatic retry', async ({ page }) => {
  const failed = makeJob('failed'); failed.allowed_actions = ['recover']; failed.error = 'Synthetic saved response failed validation: unsupported import.'; failed.stage = 'connections';
  const mock = await fixture(page, failed);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#job-error')).toContainText(failed.error!);
  await expect(page.locator('#job-error')).toContainText('Revision r05 remains unchanged and is shown below.');
  await expect(page.getByRole('button', { name: 'Apply saved Astra edit & continue' })).toHaveCount(0);
  await expect(page.locator('#viewer-status')).toHaveText('');
  await page.waitForTimeout(2800);
  expect(mock.mutations).toEqual([]);
});

test('completed lens model stays identified while the subsequent lens-seating step runs and fails', async ({ page }) => {
  const mock = await fixture(page, lensSeatingJob());
  const modelRequests: string[] = [];
  page.on('request', request => { if (request.url().endsWith('r001-glb')) modelRequests.push(request.url()); });
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#viewer-status')).toHaveText('');
  await expect(page.locator('#revision-label')).toHaveText('Revision r001 · Smooth & create lenses completed');
  await expect(page.locator('#job-message')).toContainText('Step 3 of 5 · Lens seating is running.');
  await expect(page.locator('#revision-context')).toHaveText('You are viewing the completed Smooth & create lenses result while the automatic run works on Lens seating.');
  await expect(page.locator('#timeline li.done strong')).toHaveText(['Blank model', 'Smooth & create lenses']);
  await expect(page.locator('#timeline li.active strong')).toHaveText('Lens seating');
  await expect(page.locator('#job-error')).toBeHidden();

  const failed = lensSeatingJob('failed'); failed.version += 1;
  mock.state = failed;
  await expect(page.locator('#job-error > strong')).toHaveText('The next step failed: Lens seating (step 3 of 5).', { timeout: 8000 });
  await expect(page.locator('#job-error')).toContainText('The Smooth & create lenses step already succeeded. Revision r001 remains unchanged and is shown below.');
  await expect(page.locator('#job-message')).toHaveText('Run stopped at step 3 of 5: Lens seating.');
  await expect(page.locator('#revision-label')).toHaveText('Revision r001 · Smooth & create lenses completed');
  await expect(page.locator('#revision-context')).toHaveText('This is the completed Smooth & create lenses result. The run stopped at Lens seating.');
  await page.getByText('Failure details', { exact: true }).click();
  await expect(page.locator('#job-error details p')).toHaveText(failed.error!);
  await expect(page.locator('#proofs button.closeup img')).toHaveAttribute('src', new RegExp('r001-closeup$'));
  expect(modelRequests).toHaveLength(1);
  expect(mock.mutations).toEqual([]);
  await page.screenshot({ path: `${artifactRoot}/lens-seating-failure-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${artifactRoot}/lens-seating-failure-mobile.png`, fullPage: true });
});

test('saved Astra recovery explains the local edit and remaining calls and requires exactly one explicit action', async ({ page }) => {
  const failed = lensSeatingJob('failed');
  const mock = await fixture(page, failed);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.getByRole('heading', { name: 'Continue from the saved Astra edit' })).toBeVisible();
  await expect(page.locator('#actions')).toContainText('Apply the saved Lens seating script locally in Blender. This step sends no new Astra request.');
  await expect(page.locator('#actions')).toContainText('the remaining authorized stages continue automatically, including their provider requests.');
  await expect(page.getByRole('button', { name: 'Another material edit' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Recover saved work' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Apply saved Astra edit & continue' })).toBeVisible();
  expect(mock.mutations).toEqual([]);
  mock.delay(200);
  await page.getByRole('button', { name: 'Apply saved Astra edit & continue' }).dblclick();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toEqual([{ path: `/api/jobs/${fixtureId}/recover`, body: { version: 7 } }]);
  expect(mock.state.current).toEqual(failed.current);
  expect(mock.state.calls).toEqual(failed.calls);
  expect(mock.state.stage).toBe('connections');
  await expect(page.locator('#job-error')).toBeHidden();
  await expect(page.locator('#revision-label')).toHaveText('Revision r001 · Smooth & create lenses completed');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toHaveLength(1);
});

test('test lens validation failure preserves blank r000 and recovers its saved script only after one explicit action', async ({ page }) => {
  const failed = makeTestJob('failed');
  failed.stage = 'lenses';
  failed.current = {
    id: 'r000', stage: 'generate', blend_url: file('r000-blend'), model_url: file('r000-glb'),
    proofs: angles.map(angle => ({ angle, url: file(`r000-${angle}`) })),
    closeup_url: file('r000-closeup'), inspection: { geometry_sha256: 'unchanged-blank-geometry' },
  };
  failed.revisions = [failed.current];
  failed.calls = { meshy: 1, astra: 1 };
  failed.allowed_actions = ['recover'];
  failed.recovery_kind = 'astra_script';
  failed.error = "Astra Python failed local validation: Attribute 'as_pointer' is unavailable to the Blender editor. Saved response retained; no Blender edit executed";
  const mock = await fixture(page, failed);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#job-error > strong')).toHaveText('The next step failed: Smooth & create lenses (step 2 of 6).');
  await expect(page.locator('#job-error')).toContainText('The Blank model step already succeeded. Revision r000 remains unchanged and is shown below.');
  await expect(page.locator('#revision-label')).toHaveText('Revision r000 · Blank model completed');
  await expect(page.locator('#timeline li.done strong')).toHaveText(['Blank model']);
  await page.getByText('Failure details', { exact: true }).click();
  await expect(page.locator('#job-error details p')).toHaveText(failed.error);
  await expect(page.locator('#proofs button.closeup img')).toHaveAttribute('src', /r000-closeup$/);
  await expect(page.locator('#actions')).toContainText('Apply the saved Smooth & create lenses script locally in Blender. This step sends no new Astra request.');
  await expect(page.locator('#actions button')).toHaveText(['Apply saved Astra edit & continue']);
  await page.screenshot({ path: `${artifactRoot}/pointer-validation-failure.png`, fullPage: true });

  await page.reload();
  await expect(page.getByRole('button', { name: 'Apply saved Astra edit & continue' })).toBeVisible();
  await page.waitForTimeout(2800);
  expect(mock.mutations).toEqual([]);
  mock.delay(200);
  await page.getByRole('button', { name: 'Apply saved Astra edit & continue' }).dblclick();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toEqual([{ path: `/api/jobs/${fixtureId}/recover`, body: { version: 7 } }]);
  expect(mock.state.current).toEqual(failed.current);
  expect(mock.state.calls).toEqual({ meshy: 1, astra: 1 });
  expect(mock.state.pipeline).toBe('test');
  expect(mock.state.stage).toBe('lenses');
  await expect(page.locator('#job-error')).toBeHidden();
  await expect(page.locator('#job-message')).toContainText('Step 2 of 6 · Smooth & create lenses is running.');
  await expect(page.locator('#revision-label')).toHaveText('Revision r000 · Blank model completed');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toHaveLength(1);
});

test('Astra auth failure saves a replacement key without retrying, then retries exactly once on explicit action', async ({ page }) => {
  const failed = makeJob('failed');
  failed.stage = 'lenses'; failed.allowed_actions = ['retry_auth']; failed.auth_failure = true;
  failed.error = 'Astra authentication failed (HTTP 401). Check the OpenAI API key in API setup.';
  failed.calls = { meshy: 1, astra: 1 };
  const mock = await fixture(page, failed);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#job-error')).toContainText(failed.error);
  await expect(page.getByRole('heading', { name: 'Astra authentication needs attention' })).toBeVisible();
  await expect(page.locator('#actions')).toContainText('one new paid Astra request');
  await expect(page.locator('#actions')).toContainText('using your saved model, then continues the remaining stages');
  await expect(page.locator('#actions')).toContainText('Completed stages are not repeated');
  await expect(page.getByRole('button', { name: 'Recover saved work' })).toHaveCount(0);
  await page.screenshot({ path: `${artifactRoot}/auth-failure-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${artifactRoot}/auth-failure-mobile.png`, fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.reload();
  await expect(page.getByRole('button', { name: 'Retry Astra & continue' })).toBeVisible();
  await page.waitForTimeout(2800);
  expect(mock.mutations).toEqual([]);
  await page.getByRole('button', { name: 'Update OpenAI API key' }).click();
  await expect(page.getByLabel('OpenAI API key', { exact: true })).toBeFocused();
  await page.getByLabel('OpenAI API key', { exact: true }).fill('synthetic-replacement-key');
  await page.getByRole('button', { name: 'Save API keys' }).click();
  await expect(page.locator('#notice')).toHaveText('API keys saved locally. No provider request was sent.');
  expect(mock.mutations).toEqual([{ path: '/api/settings', body: { openai_key: 'synthetic-replacement-key' } }]);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Retry Astra & continue' })).toBeVisible();
  await page.waitForTimeout(2800);
  expect(mock.mutations).toHaveLength(1);
  mock.delay(200);
  await page.getByRole('button', { name: 'Retry Astra & continue' }).dblclick();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toEqual([
    { path: '/api/settings', body: { openai_key: 'synthetic-replacement-key' } },
    { path: `/api/jobs/${fixtureId}/retry_auth`, body: { version: 7 } },
  ]);
  expect(mock.state.stage).toBe('lenses');
  expect(mock.state.calls).toEqual({ meshy: 1, astra: 2 });
  expect(mock.state.current).toEqual(failed.current);
  await expect(page.locator('#job-error')).toBeHidden();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toHaveLength(2);
});

test('auth failure offers key setup but no retry when the server has not allowed it', async ({ page }) => {
  const failed = makeJob('failed'); failed.allowed_actions = []; failed.auth_failure = true;
  failed.error = 'Astra authentication failed (HTTP 401).';
  const mock = await fixture(page, failed);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.getByRole('button', { name: 'Update OpenAI API key' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry Astra & continue' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Recover saved work' })).toHaveCount(0);
  expect(mock.mutations).toEqual([]);
});

test('ambiguous failures never gain an auth retry from error text, polling, or reload', async ({ page }) => {
  const failed = makeJob('failed'); failed.allowed_actions = []; failed.auth_failure = false;
  failed.error = 'Astra request outcome is unknown. A prior request returned HTTP 401; this request has no response.';
  const mock = await fixture(page, failed);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#job-error')).toContainText(failed.error);
  await expect(page.getByRole('button', { name: 'Retry Astra & continue' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Update OpenAI API key' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Run stopped', exact: true })).toBeVisible();
  await page.waitForTimeout(2800);
  expect(mock.mutations).toEqual([]);
});

test('interrupted job is inspected on reload without submitting a request', async ({ page }) => {
  const interrupted = makeJob('interrupted'); interrupted.allowed_actions = ['recover']; interrupted.stage = 'texture';
  const mock = await fixture(page, interrupted);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#job-status')).toHaveText('interrupted');
  await page.reload();
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish completed');
  expect(mock.mutations).toEqual([]);
});

test('cancellation pending remains visibly in progress until the backend confirms its stop', async ({ page }) => {
  const stopping = makeJob('running'); stopping.allowed_actions = []; stopping.stage = 'texture';
  const mock = await fixture(page, stopping);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.getByRole('heading', { name: 'Stopping the run…' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Run stopped', exact: true })).toHaveCount(0);
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish completed');
  expect(mock.mutations).toEqual([]);
});

test('an optional blank edit note preserves the existing job instructions', async ({ page }) => {
  const initial = makeJob(); initial.notes = 'Keep the original gold finish.';
  const mock = await fixture(page, initial);
  await page.goto(`/?job=${fixtureId}`);
  await page.getByRole('button', { name: 'Another material edit' }).click();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations[0]!.body).toEqual({ version: 7 });
});

test('stale action refreshes state and never repeats its POST', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto(`/?job=${fixtureId}`);
  mock.nextConflict();
  await page.getByRole('button', { name: 'Another material edit' }).click();
  await expect(page.locator('#notice')).toContainText('no request was repeated');
  await expect(page.getByRole('button', { name: 'Another material edit' })).toBeEnabled();
  expect(mock.mutations).toHaveLength(1);
});

test('settings sends only explicitly entered local keys and clears password fields', async ({ page }) => {
  const mock = await fixture(page); mock.setConfigured(false);
  await page.goto('/');
  await expect(page.locator('#health')).toHaveText('API setup needed');
  await page.getByRole('button', { name: 'API setup', exact: true }).click();
  await page.getByLabel('OpenAI API key', { exact: true }).fill('synthetic-openai-not-a-key');
  await page.getByRole('button', { name: 'Save API keys' }).click();
  await expect(page.locator('#settings-dialog')).not.toBeVisible();
  expect(mock.mutations).toEqual([{ path: '/api/settings', body: { openai_key: 'synthetic-openai-not-a-key' } }]);
  await page.getByRole('button', { name: 'API setup', exact: true }).click();
  await expect(page.getByLabel('OpenAI API key', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('Meshy API key', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: 'Close API setup' }).click();
  await page.reload();
  expect(mock.mutations).toHaveLength(1);
});

test('server text is escaped and cross-origin artifact links never enter the page', async ({ page }) => {
  const malicious = makeJob(); malicious.name = '<img src=x onerror=alert(1)>'; malicious.current!.model_url = 'https://invalid.example/model.glb'; malicious.references[0]!.url = 'javascript:alert(1)'; malicious.accepted = { sha256: 'test', url: 'https://invalid.example/download.blend' }; malicious.allowed_actions = [];
  await fixture(page, malicious);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#job-name')).toHaveText(malicious.name);
  await expect(page.locator('#job-name img')).toHaveCount(0);
  await expect(page.locator('a[href^="https://invalid"], img[src^="javascript:"]')).toHaveCount(0);
  await expect(page.locator('#accepted-download')).toHaveCount(0);
});

test('a setup failure stays visible inside the modal and does not retry the key update', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'API setup', exact: true }).click();
  await page.getByLabel('OpenAI API key', { exact: true }).fill('synthetic-openai-not-a-key');
  mock.nextConflict();
  await page.getByRole('button', { name: 'Save API keys' }).click();
  await expect(page.locator('#settings-error')).toBeVisible();
  await expect(page.locator('#settings-error')).toContainText('The job changed before this action.');
  await expect(page.getByLabel('OpenAI API key', { exact: true })).toHaveValue('synthetic-openai-not-a-key');
  expect(mock.mutations).toHaveLength(1);
});

test('polling disconnection retains the visible saved revision and performs no mutations', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish completed');
  mock.disconnect(true);
  await expect(page.locator('#health')).toHaveText('Disconnected', { timeout: 8000 });
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish completed');
  expect(mock.mutations).toEqual([]);
});

test('mobile input and review layouts have no horizontal overflow', async ({ page }) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Start with your references.' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${artifactRoot}/new-mobile.png`, fullPage: true });
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.getByRole('button', { name: 'Accept & download .blend' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${artifactRoot}/review-mobile.png`, fullPage: true });
});

test('browser Back restores the selected saved model through GET requests only', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish completed');
  await page.getByRole('button', { name: 'New model', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Start with your references.' })).toBeVisible();
  await page.goBack();
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish completed');
  await expect(page.getByRole('button', { name: 'Another material edit' })).toBeVisible();
  expect(mock.mutations).toEqual([]);
});

test('test mode discloses six calls, keeps the same required inputs, and starts only on explicit submit', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto('/?pipeline=test');
  await expect(page.getByRole('link', { name: 'Test pipeline', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#run-call-summary')).toHaveText('2 Meshy requests + 4 Astra sessions');
  await expect(page.locator('#run-sequence')).toContainText('all six requests with no intermediate approvals');
  await expect(page.locator('#run-sequence')).toContainText('Both finish passes inspect originals, fresh model views and the lens close-up');
  await expect(page.locator('input[type=file]')).toHaveCount(5);
  await page.reload();
  expect(mock.mutations).toEqual([]);
  await fillNew(page);
  await page.getByRole('spinbutton', { name: 'Lens height', exact: true }).fill('');
  await page.getByRole('button', { name: 'Start automatic run' }).click();
  await expect(page.locator('#notice')).toContainText('at least three');
  expect(mock.mutations).toEqual([]);
  await page.getByRole('spinbutton', { name: 'Lens height', exact: true }).fill('42');
  await page.getByLabel('Angled reference photo').setInputFiles([]);
  await page.getByRole('button', { name: 'Start automatic run' }).click();
  expect(mock.mutations).toEqual([]);
  await page.getByLabel('Angled reference photo').setInputFiles({ name: 'angled.png', mimeType: 'image/png', buffer: PNG });
  await page.getByRole('button', { name: 'Start automatic run' }).click();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations.map(item => item.path)).toEqual(['/api/jobs', `/api/jobs/${fixtureId}/start`]);
  expect(String(mock.mutations[0]!.body)).toMatch(/name="pipeline"\r?\n\r?\ntest\r?\n/);
  expect(mock.state.pipeline).toBe('test');
  await expect(page.locator('#job-message')).toContainText('Step 1 of 6');
  await page.reload();
  await expect(page.locator('#job-eyebrow')).toContainText('TEST PIPELINE');
  expect(mock.mutations).toHaveLength(2);
});

test('test pipeline shows all six automatic stages followed by review with no intermediate decisions', async ({ page }) => {
  const mock = await fixture(page, makeTestJob());
  for (const [index, stage] of TEST_STAGES.slice(0, 6).entries()) {
    const next = makeTestJob('running'); next.stage = stage; next.allowed_actions = ['cancel']; next.version += index;
    mock.state = next;
    await page.goto(`/?job=${fixtureId}`);
    await expect(page.locator('#job-message')).toContainText(`Step ${index + 1} of 6`);
    await expect(page.locator('#timeline li')).toHaveCount(7);
    await expect(page.locator(`#timeline li[data-stage=${stage}]`)).toHaveClass('active');
    await expect(page.locator('#timeline li.done')).toHaveCount(index);
    await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Download & finish run' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Send another edit' })).toHaveCount(0);
  }
  const reviewed = makeTestJob(); reviewed.version = 20; mock.state = reviewed;
  await page.reload();
  await expect(page.locator('#timeline li.done')).toHaveCount(6);
  await expect(page.locator('#timeline li.active')).toHaveText('07Your review');
  await expect(page.locator('#timeline li[data-stage=finish]')).toContainText('Material & finish · pass 1');
  await expect(page.locator('#timeline li[data-stage=finish_refine]')).toContainText('Material & finish · pass 2');
  await expect(page.locator('#call-counts')).toHaveText('Reserved: 2 Meshy · 4 Astra');
  await expect(page.getByRole('button', { name: 'Download & finish run' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send another edit' })).toBeVisible();
  expect(mock.mutations).toEqual([]);
});

test('test repeat requires specific text and submits one refinement request then returns to review', async ({ page }) => {
  const initial = makeTestJob(); initial.notes = 'Original owner instructions.';
  const mock = await fixture(page, initial);
  await page.goto(`/?job=${fixtureId}`);
  const input = page.getByRole('textbox', { name: 'Specific edit instructions' });
  await input.fill('   ');
  await page.getByRole('button', { name: 'Send another edit' }).click();
  await expect(page.locator('#edit-error')).toContainText('Write specific material or finish instructions');
  await expect(input).toBeFocused();
  expect(mock.mutations).toEqual([]);
  await input.fill('Make the frame slightly warmer and reduce lens reflections.');
  mock.delay(200);
  await page.getByRole('button', { name: 'Send another edit' }).dblclick();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toEqual([{ path: `/api/jobs/${fixtureId}/edit`, body: { version: 7, notes: 'Make the frame slightly warmer and reduce lens reflections.' } }]);
  expect(mock.state.stage).toBe('finish_refine');
  expect(mock.state.calls).toEqual({ meshy: 2, astra: 5 });
  expect(mock.state.current).toEqual(initial.current);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(mock.mutations).toHaveLength(1);
  const complete = makeTestJob(); complete.version = 9; complete.calls.astra = 5; mock.state = complete;
  await page.reload();
  await expect(page.getByRole('button', { name: 'Download & finish run' })).toBeVisible();
  await expect(input).toHaveValue('');
  expect(mock.mutations).toHaveLength(1);
});

test('failed second finish pass identifies the successful first pass without claiming final completion', async ({ page }) => {
  const failed = makeTestJob('failed');
  failed.stage = 'finish_refine'; failed.current!.stage = 'finish'; failed.revisions[0]!.stage = 'finish';
  failed.allowed_actions = ['recover', 'edit']; failed.recovery_kind = 'astra_script';
  failed.error = 'Synthetic second material pass failed validation; no edit executed.';
  const mock = await fixture(page, failed);
  await page.goto(`/?job=${fixtureId}`);
  await expect(page.locator('#job-error')).toContainText('The next step failed: Material & finish · pass 2 (step 6 of 6).');
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish · pass 1 completed');
  await expect(page.locator('#actions')).toContainText('The run stopped before the latest material/finish edit completed.');
  await expect(page.locator('#actions')).not.toContainText('Both material/finish passes are complete');
  await expect(page.getByRole('button', { name: 'Download & finish run' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Send another edit' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply saved Astra edit & continue' })).toBeVisible();
  await page.reload();
  await expect(page.locator('#revision-label')).toHaveText('Revision r05 · Material & finish · pass 1 completed');
  expect(mock.mutations).toEqual([]);
});

test('test final download accepts and downloads the same saved hash exactly once', async ({ page }) => {
  const mock = await fixture(page, makeTestJob());
  await page.goto(`/?job=${fixtureId}`);
  const downloads: string[] = [];
  page.on('download', download => downloads.push(download.url()));
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download & finish run' }).click();
  const download = await downloaded;
  await download.saveAs(`${artifactRoot}/test-accepted-download.blend`);
  expect(createHash('sha256').update(await readFile(`${artifactRoot}/test-accepted-download.blend`)).digest('hex')).toBe(BLEND_SHA);
  await expect(page.locator('.hash')).toContainText(BLEND_SHA);
  expect(mock.mutations).toEqual([{ path: `/api/jobs/${fixtureId}/accept`, body: { version: 7 } }]);
  expect(mock.state.status).toBe('complete');
  expect(mock.state.pipeline).toBe('test');
  await page.reload();
  await expect(page.locator('#accepted-download')).toBeVisible();
  expect(mock.mutations).toHaveLength(1);
  expect(downloads).toEqual([`http://127.0.0.1:8061${file('accepted-exact-blend')}`]);
});

test('pipeline switches are local and saved job mode takes precedence over a different URL mode', async ({ page }) => {
  const original = makeJob();
  original.pipeline_stages = STAGES.filter(stage => stage !== 'review' && stage !== 'complete');
  const mock = await fixture(page, original);
  await page.goto(`/?pipeline=test&job=${fixtureId}`);
  await expect(page.getByRole('button', { name: 'Another material edit' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Current pipeline', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#timeline li')).toHaveCount(6);
  await expect(page.locator('#timeline li[data-stage=finish_refine]')).toHaveCount(0);
  await page.getByRole('link', { name: 'Test pipeline', exact: true }).click();
  await expect(page).toHaveURL(/\?pipeline=test$/);
  await expect(page.locator('#run-call-summary')).toHaveText('2 Meshy requests + 4 Astra sessions');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Start with your references.' })).toBeVisible();
  await page.getByRole('button', { name: /Synthetic fixture — no paid calls/ }).click();
  await expect(page.getByRole('button', { name: 'Another material edit' })).toBeVisible();
  expect(mock.state).toEqual(original);
  await page.getByRole('link', { name: 'Current pipeline', exact: true }).click();
  await expect(page.locator('#run-call-summary')).toHaveText('2 Meshy requests + 3 Astra sessions');
  expect(mock.mutations).toEqual([]);
  await fillNew(page);
  await page.getByRole('button', { name: 'Start automatic run' }).click();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(String(mock.mutations[0]!.body)).toMatch(/name="pipeline"\r?\n\r?\ncurrent\r?\n/);
  expect(mock.state.pipeline ?? 'current').toBe('current');
});

test('test mobile creation and final options fit without horizontal overflow', async ({ page }) => {
  const mock = await fixture(page, makeTestJob());
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?pipeline=test');
  await expect(page.locator('#run-call-summary')).toHaveText('2 Meshy requests + 4 Astra sessions');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${artifactRoot}/test-new-mobile.png`, fullPage: true });
  await page.goto(`/?pipeline=current&job=${fixtureId}`);
  await expect(page.getByRole('button', { name: 'Download & finish run' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Test pipeline', exact: true })).toHaveAttribute('aria-current', 'page');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${artifactRoot}/test-review-mobile.png`, fullPage: true });
  expect(mock.mutations).toEqual([]);
});

test('blank forms need no GPU and a saved-model GPU failure preserves proofs and review actions', async ({ page }) => {
  const mock = await fixture(page, makeTestJob());
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    let attempts = 0;
    Object.defineProperty(window, 'gpuAttempts', { get: () => attempts });
    HTMLCanvasElement.prototype.getContext = function (...args: Parameters<typeof original>) {
      if (String(args[0]).includes('webgl')) { attempts += 1; throw new Error('Synthetic GPU unavailable'); }
      return Reflect.apply(original, this, args);
    } as typeof original;
  });
  await page.goto('/?pipeline=test');
  await expect(page.locator('#health')).toHaveText('Local app connected');
  await expect(page.locator('#viewer canvas')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { gpuAttempts: number }).gpuAttempts)).toBe(0);
  await page.getByRole('link', { name: 'Current pipeline', exact: true }).click();
  await expect(page.locator('#run-call-summary')).toHaveText('2 Meshy requests + 3 Astra sessions');
  expect(await page.evaluate(() => (window as unknown as { gpuAttempts: number }).gpuAttempts)).toBe(0);
  await page.getByRole('button', { name: /Synthetic fixture — no paid calls/ }).click();
  await expect(page.locator('#viewer-status')).toContainText('Interactive preview is unavailable');
  await expect(page.locator('#proofs button')).toHaveCount(6);
  await expect(page.getByRole('button', { name: 'Download & finish run' })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { gpuAttempts: number }).gpuAttempts)).toBeGreaterThan(0);
  const attempts = await page.evaluate(() => (window as unknown as { gpuAttempts: number }).gpuAttempts);
  await page.getByRole('button', { name: 'New model', exact: true }).click();
  await page.getByRole('button', { name: /Synthetic fixture — no paid calls/ }).click();
  await expect(page.locator('#viewer-status')).toContainText('Interactive preview is unavailable');
  await expect(page.getByRole('button', { name: 'Download & finish run' })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { gpuAttempts: number }).gpuAttempts)).toBe(attempts);
  expect(mock.mutations).toEqual([]);
});

test('lazy viewer handles reset and disposal before startup and rejects a model that completes after clearing', async ({ page }) => {
  const mock = await fixture(page);
  const requests: string[] = [];
  page.on('request', request => { if (request.url().includes('/files/lifecycle-')) requests.push(new URL(request.url()).pathname); });
  await page.route('**/files/lifecycle-stale-glb', async route => {
    await new Promise(resolve => setTimeout(resolve, 150));
    await route.fulfill({ contentType: 'model/gltf-binary', body: glb() });
  });
  await page.goto('/');
  const result = await page.evaluate(async ({ stale, current, afterDispose }) => {
    const modulePath = '/web/viewer.ts';
    const { ModelViewer } = await import(modulePath);
    const host = document.createElement('div'); host.style.cssText = 'width:200px;height:150px';
    const status = document.createElement('p'); document.body.append(host, status);
    const unused = new ModelViewer(host, status);
    unused.resetView(); await unused.load(null); unused.dispose(); unused.dispose(); await unused.load(afterDispose);
    const beforeModel = host.querySelectorAll('canvas').length;
    const viewer = new ModelViewer(host, status);
    viewer.resetView(); await viewer.load(null);
    const pending = viewer.load(stale);
    await viewer.load(null); await pending;
    const afterClearing = status.textContent;
    await viewer.load(current);
    const afterCurrent = status.textContent;
    const canvases = host.querySelectorAll('canvas').length;
    viewer.dispose(); viewer.dispose(); await viewer.load(afterDispose);
    host.remove(); status.remove();
    return { beforeModel, afterClearing, afterCurrent, canvases };
  }, { stale: file('lifecycle-stale-glb'), current: file('lifecycle-current-glb'), afterDispose: file('lifecycle-after-dispose-glb') });
  expect(result).toEqual({ beforeModel: 0, afterClearing: 'Your model will appear here after the first Blender revision.', afterCurrent: '', canvases: 1 });
  expect(requests).toEqual([file('lifecycle-stale-glb'), file('lifecycle-current-glb')]);
  expect(mock.mutations).toEqual([]);
});
