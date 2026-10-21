/** Real E4 controls, preview bytes, temporary AR and immutable save on private copies. */
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(new URL('../../arview/package.json', import.meta.url));
const {chromium} = require('@playwright/test');
const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
assert.equal(new URL(config.base_url).hostname, '127.0.0.1');
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--disable-background-timer-throttling']});
const context = await browser.newContext({viewport: {width: 1440, height: 1050}, deviceScaleFactor: 1, serviceWorkers: 'block'});
const report = {started_at: new Date().toISOString(), models: [], errors: [], external: [], status: 'running'};
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
    console.log(`E4 browser ${model.label}`);
    page = await context.newPage(); page.setDefaultTimeout(90_000);
    page.on('dialog', dialog => dialog.accept());
    const folder = path.join(config.out, model.label); await fs.mkdir(folder, {recursive: true});
    const row = {...model, opened: false, edits: {}, ar: false, save: false, refusals: []}; report.models.push(row); await flush();
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
    page.on('request', request => {if (request.url().endsWith('/appearance-model')) lastBody = request.postDataJSON();});
    async function update(action) {
      const pending = page.waitForResponse(response => response.url().endsWith('/appearance-model'));
      await action(); const response = await pending;
      assert.ok(response.ok(), await response.text());
      // Native model reload completion is exposed as aria-busy by the page.
      if (await page.locator('#general-viewer[aria-busy]').count()) await page.locator('#general-viewer[aria-busy="false"]').waitFor();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      return response;
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
    await update(() => page.getByLabel('Shimmer', {exact: true}).selectOption('.25'.replace(/^\./, '0.'))); row.edits.shimmer = true;
    await update(() => page.locator('#colour-gradient').click());
    await update(() => page.locator('#gradient-middle').click());
    await set('Start colour', '#7a43ce'); await set('Middle colour', '#37b5b5'); await set('End colour', '#d2ab43');
    await set('Middle takes (%)', 44); await set('Blend', .65);
    for (const direction of ['top_to_bottom', 'left_to_right', 'centre_to_sides']) {
      const patched = await update(() => page.locator('#gradient-direction').selectOption(direction));
      await fs.writeFile(path.join(folder, direction + '.glb'), await patched.body());
      row.edits[direction] = true;
      await page.screenshot({path: path.join(folder, direction + '.png'), fullPage: true});
    }
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
