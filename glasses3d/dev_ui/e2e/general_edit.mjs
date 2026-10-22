/** Real E4/E5 controls, preview bytes, temporary AR and immutable save on private copies. */
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
const require = createRequire(new URL('../../arview/package.json', import.meta.url));
const {chromium} = require('@playwright/test');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
assert.equal(new URL(config.base_url).hostname, '127.0.0.1');
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--disable-background-timer-throttling']});
const context = await browser.newContext({viewport: {width: 1440, height: 1050}, deviceScaleFactor: 1, serviceWorkers: 'block'});
// Chromium can return an empty CDP response body for a POST consumed as Blob,
// even though the page received every byte. Capture the application's response
// clone before returning the original Response; never refetch or regenerate it.
await context.addInitScript(() => {
  const nativeFetch = window.fetch.bind(window); let sequence = 0;
  window.__generalityAppearanceCapture = null;
  window.fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href).href;
    const method = String(init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const capture = method === 'POST' && new URL(url).pathname.endsWith('/appearance-model');
    const request_body = capture ? (typeof init?.body === 'string' ? init.body
      : input instanceof Request ? await input.clone().text() : null) : null;
    const response = await nativeFetch(input, init);
    if (capture) {
      const bytes = new Uint8Array(await response.clone().arrayBuffer());
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
      let binary = '';
      for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
      window.__generalityAppearanceCapture = {sequence: ++sequence, url: response.url, method, request_body,
        status: response.status, content_type: response.headers.get('content-type'),
        content_length: response.headers.get('content-length'), base64: btoa(binary),
        sha256: [...digest].map(byte => byte.toString(16).padStart(2, '0')).join('')};
    }
    return response;
  };
});
const report = {started_at: new Date().toISOString(), preview_capture: 'browser fetch response.clone().arrayBuffer(), request-bound and GLB/hash verified',
  models: [], errors: [], external: [], status: 'running'};
context.on('page', page => {
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('console', item => {if (item.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(item.text())) report.errors.push(item.text());});
});
await context.route('**/*', route => {
  const url = new URL(route.request().url());
  if (['http:', 'https:'].includes(url.protocol) && !['127.0.0.1', 'localhost'].includes(url.hostname)) {
    report.external.push(url.href); return route.abort();
  }
  return route.continue();
});
const flush = () => fs.writeFile(path.join(config.out, 'browser.json'), JSON.stringify(report, null, 2));
let page;
try {
  for (const model of config.models) {
    console.log(`E4/E5 browser ${model.label}`);
    page = await context.newPage(); page.setDefaultTimeout(90_000);
    page.on('dialog', dialog => dialog.accept());
    const folder = path.join(config.out, model.label); await fs.mkdir(folder, {recursive: true});
    const row = {...model, opened: false, edits: {}, ar: false, save: false, refusals: [],
      preview_captures: [], preview_artifacts: {}}; report.models.push(row); await flush();
    const response = await page.request.get(`${config.base_url}/api/deliveries/${model.delivery_key}/appearance`);
    assert.equal(response.ok(), true, await response.text());
    const dto = await response.json(); row.refusals = dto.refusals || [];
    await page.goto(`${config.base_url}/edit-version/${model.delivery_key}`);
    await page.locator('#edit-ar:enabled').waitFor(); row.opened = true;
    await page.screenshot({path: path.join(folder, 'opened.png'), fullPage: true});
    const lensIndex = dto.materials.findIndex(material => material.role === 'lens');
    assert.ok(lensIndex >= 0, `${model.label} has an optical lens`);
    await page.locator('#edit-part').selectOption(String(lensIndex));
    let lastBody;
    page.on('request', request => {if (request.url().endsWith('/appearance-model') && request.method() === 'POST') lastBody = request.postDataJSON();});
    async function update(action) {
      const previous = await page.evaluate(() => window.__generalityAppearanceCapture?.sequence ?? 0);
      const pending = page.waitForResponse(response => response.url().endsWith('/appearance-model') && response.request().method() === 'POST');
      await action(); const response = await pending;
      if (!response.ok()) assert.fail(`Appearance preview HTTP ${response.status()}: ${await response.text()}`);
      // Native model reload completion is exposed as aria-busy by the page.
      if (await page.locator('#general-viewer[aria-busy]').count()) await page.locator('#general-viewer[aria-busy="false"]').waitFor();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const captured = await page.evaluate(() => window.__generalityAppearanceCapture);
      assert.ok(captured && captured.sequence > previous, 'The preview response was not captured in the browser');
      assert.equal(captured.url, response.url()); assert.equal(captured.method, 'POST');
      assert.equal(captured.request_body, response.request().postData(), 'Preview bytes belong to a different request');
      assert.equal(captured.status, 200); assert.equal(captured.status, response.status());
      assert.equal(captured.content_type?.split(';')[0].trim().toLowerCase(), 'model/gltf-binary');
      assert.match(captured.content_length ?? '', /^[1-9]\d*$/);
      const bytes = Buffer.from(captured.base64, 'base64');
      assert.equal(bytes.length, Number(captured.content_length), 'Preview Content-Length differs from browser bytes');
      assert.ok(bytes.length >= 12, 'Preview GLB is empty or truncated');
      assert.equal(bytes.subarray(0, 4).toString('ascii'), 'glTF');
      assert.equal(bytes.readUInt32LE(4), 2); assert.equal(bytes.readUInt32LE(8), bytes.length);
      assert.equal(hash(bytes), captured.sha256, 'Browser and proof preview hashes differ');
      row.preview_captures.push({sequence: captured.sequence, method: captured.method, status: captured.status,
        request_sha256: hash(captured.request_body), byte_length: bytes.length, sha256: captured.sha256});
      return bytes;
    }
    async function writePreview(name, bytes) {
      await fs.writeFile(path.join(folder, name), bytes);
      row.preview_artifacts[name] = {byte_length: bytes.length, sha256: hash(bytes)};
    }
    async function set(label, value) {
      return update(() => page.getByLabel(label, {exact: true}).evaluate((input, value) => {
        input.value = String(value); input.dispatchEvent(new Event('input', {bubbles: true})); input.dispatchEvent(new Event('change', {bubbles: true}));
      }, value));
    }
    await set('Solid colour', '#39b5d0'); row.edits.solid = true;
    await set('See-through', .82); row.edits.see_through = true;
    await set('Mirror', .08); row.edits.mirror = true;
    await set('Shine', .8); row.edits.shine = true;
    // Coating layer: two target colours -> the exact solver -> the first choice.
    if (await page.locator('#coating-two').count()) {
      await update(() => page.locator('#coating-two').click());
      await update(() => page.locator('#coating-brightness').selectOption('6'));
      await page.getByLabel('Straight-on colour', {exact: true}).evaluate(input => {input.value = '#5abe8c'; input.dispatchEvent(new Event('change', {bubbles: true}));});
      await page.getByLabel('Side colour', {exact: true}).evaluate(input => {input.value = '#aa6ec8'; input.dispatchEvent(new Event('change', {bubbles: true}));});
      await page.locator('#find-coating').click();
      await update(() => page.getByRole('button', {name: 'Use coating 1', exact: true}).click());
      row.edits.coating_two_colours = true;
      // Exact colours: the owner's own angle bar, kept for the save so every model stores and renders it.
      await update(() => page.locator('#coating-exact').click());
      await set('Exact side colour', '#a96fd0'); await set('Exact straight-on colour', '#4fc28a'); await set('Lens colour tint', .7);
      await update(async () => {const marker = page.locator('.angle-stop[data-angle-stop="0"]'); await marker.focus(); await page.keyboard.press('ArrowRight');});
      row.edits.coating_exact = true;
    } else {
      await update(() => page.getByLabel('Shimmer', {exact: true}).selectOption('0.25')); row.edits.shimmer = true;
    }
    await update(() => page.locator('#colour-gradient').click());
    await update(() => page.locator('#gradient-middle').click());
    await set('Start colour', '#7a43ce'); await set('Middle colour', '#37b5b5'); await set('End colour', '#d2ab43');
    await set('Softness start to middle', .65); await set('Middle tint strength', .7);
    await update(async () => {const marker = page.locator('.gradient-stop[data-stop="1"]'); await marker.focus(); await page.keyboard.press('ArrowRight');});
    for (const direction of ['top_to_bottom', 'left_to_right', 'centre_to_sides']) {
      const patched = await update(() => page.locator('#gradient-direction').selectOption(direction));
      await writePreview(direction + '.glb', patched);
      row.edits[direction] = true;
      await page.screenshot({path: path.join(folder, direction + '.png'), fullPage: true});
    }
    // Every optical material gets radial rings, including pairs whose lenses use
    // distinct materials. Marking materials were excluded by the real API.
    row.radial_material_indices = [];
    for (const [index, material] of dto.materials.entries()) {
      if (material.role !== 'lens') continue;
      await page.locator('#edit-part').selectOption(String(index));
      if (await page.locator('#colour-gradient').getAttribute('aria-pressed') !== 'true') {
        await update(() => page.locator('#colour-gradient').click());
      }
      if (await page.getByLabel('Middle colour', {exact: true}).count()) {
        await update(() => page.locator('#gradient-middle').click());
      }
      await update(() => page.locator('#gradient-direction').selectOption('around_point'));
      await set('Start colour', '#20c979'); await set('End colour', '#914de3');
      await set('Softness start to end', .85);
      await set('Left / right', .18); await set('Up / down', -.12); await set('Roundness', 2);
      const moved = lastBody.materials.find(item => item.material_index === material.material_index).values.gradient;
      assert.equal(moved.direction, 'around_point'); assert.deepEqual(moved.centre, [.18, -.12]); assert.equal(moved.roundness, 2);
      await set('Left / right', 0); await set('Up / down', 0);
      const circle = await set('Roundness', 1);
      await writePreview(`around-point-material-${material.material_index}.glb`, circle);
      row.radial_material_indices.push(material.material_index);
    }
    assert.ok(row.radial_material_indices.length > 0);
    row.edits.around_point = true; row.edits.radial_centre = true; row.edits.roundness = true;
    await page.locator('#edit-part').selectOption(String(lensIndex));
    await page.screenshot({path: path.join(folder, 'around-point.png'), fullPage: true});
    await page.locator('#general-viewer').screenshot({path: path.join(folder, 'green-centre-violet-ring.png')});
    row.values = lastBody?.materials;
    const texturedIndex = dto.materials.findIndex(material => material.role !== 'lens' && material.has_colour_texture);
    if (texturedIndex >= 0) {
      await page.locator('#edit-part').selectOption(String(texturedIndex));
      await page.getByText('Colour tints the existing pattern.', {exact: false}).waitFor();
      await set('Solid colour', '#d0b38c'); row.edits.pattern_tint = true;
      await page.screenshot({path: path.join(folder, 'pattern-tint.png'), fullPage: true});
      await page.locator('#edit-part').selectOption(String(lensIndex));
    }
    const transmittingFrame = dto.materials.findIndex(material => material.role === 'frame' && material.can_transmission);
    if (transmittingFrame >= 0) {
      await page.locator('#edit-part').selectOption(String(transmittingFrame));
      await set('See-through', .72); row.edits.frame_see_through = true;
      await page.locator('#edit-part').selectOption(String(lensIndex));
    }
    await page.screenshot({path: path.join(folder, 'edited.png'), fullPage: true});
    const popupPromise = context.waitForEvent('page'); await page.locator('#edit-ar').click();
    const popup = await popupPromise;
    await popup.waitForURL(url => url.hostname === '127.0.0.1' && url.port !== new URL(config.base_url).port, {timeout: 180_000});
    await popup.waitForFunction(() => document.querySelector('.stage')?.dataset.externalModel === 'ready', null, {timeout: 180_000});
    assert.ok(new URL(popup.url()).searchParams.has('sha256')); row.ar = true;
    await popup.screenshot({path: path.join(folder, 'temporary-ar.png')}); await popup.close();
    const saveResponse = page.waitForResponse(response => response.url().endsWith('/appearance-save'));
    await page.locator('#edit-save').click(); const save = await saveResponse;
    assert.ok(save.ok(), await save.text()); row.values = save.request().postDataJSON().materials;
    const taskKey = (await save.json()).task_key;
    let task;
    do {
      task = await (await page.request.get(`${config.base_url}/api/tasks/${taskKey}`)).json();
      if (!['done', 'failed'].includes(task.phase)) await new Promise(resolve => setTimeout(resolve, 1000));
    } while (!['done', 'failed'].includes(task.phase));
    if (task.phase === 'done') {
      row.saved_key = task.result.delivery_key; assert.notEqual(row.saved_key, model.delivery_key);
      await page.waitForURL(`**/edit-version/${row.saved_key}`);
      await page.locator('#edit-ar:enabled').waitFor();
      row.save = true; await page.screenshot({path: path.join(folder, 'saved.png'), fullPage: true});
      const saved = await (await page.request.get(`${config.base_url}/api/deliveries/${row.saved_key}`)).json();
      row.checks = saved.checks; row.origin = saved.origin;
      assert.equal(saved.accepted_finish, false);
      const savedAppearance = await (await page.request.get(`${config.base_url}/api/deliveries/${row.saved_key}/appearance`)).json();
      for (const index of row.radial_material_indices) {
        const requested = row.values.find(item => item.material_index === index).values.gradient;
        const actual = savedAppearance.materials.find(item => item.material_index === index).values.gradient;
        assert.deepEqual(actual, requested, `Saved radial controls differ on material ${index}`);
      }
      row.radial_reopened_exact = true;
    } else {
      row.refusals.push({action: 'save', message: task.error});
      await page.getByRole('alert').filter({hasText: /./}).waitFor();
      await page.screenshot({path: path.join(folder, 'save-refusal.png'), fullPage: true});
      assert.equal(model.save_expected, false, `${model.label}: unexpected save failure: ${task.error}`);
      const message = String(task.error).toLowerCase(), failed = model.save_refusal_checks || [];
      assert.ok((failed.includes('validate_spec') && message.includes('model specification check')) ||
        (failed.includes('print_check') && message.includes('retained-print check')),
        `${model.label}: refusal was not established by the independent source checks: ${task.error}`);
    }
    await page.close(); await flush();
  }
  assert.deepEqual(report.errors, []); assert.deepEqual(report.external, []);
  report.status = report.models.some(row => !row.save) ? 'passed_with_refusals' : 'passed';
} catch (error) {
  report.status = 'failed'; report.error = error.stack;
  await page?.screenshot({path: path.join(config.out, 'failure.png'), fullPage: true}).catch(() => {});
  throw error;
} finally {
  report.ended_at = new Date().toISOString(); await flush(); await browser.close();
}
