/** Actual UI/API/GLB/AR proof on a private owner delivery copy; no mocks. */
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(new URL('../../arview/package.json', import.meta.url));
const {chromium} = require('@playwright/test');
const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
assert.equal(new URL(config.base_url).hostname, '127.0.0.1');
const browser = await chromium.launch({headless: true, channel: 'chromium', timeout: 60_000,
  args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--disable-background-timer-throttling']});
const context = await browser.newContext({viewport: {width: 1440, height: 1050}, deviceScaleFactor: 1, serviceWorkers: 'block'});
const errors = [], blocked = [], assertions = [];
context.on('page', page => {
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(message.text())) errors.push(message.text());
  });
});
await context.route('**/*', async route => {
  const url = new URL(route.request().url());
  if (['http:', 'https:'].includes(url.protocol) && !['127.0.0.1', 'localhost'].includes(url.hostname)) {
    blocked.push(url.href); return route.abort();
  }
  return route.continue();
});
const page = await context.newPage();
page.setDefaultTimeout(60_000);
const check = (value, label) => {assert(value, label); assertions.push(label);};
const rendered = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
let report = {status: 'running', started_at: new Date().toISOString(), assertions, errors, blocked};
try {
  await page.goto(`${config.base_url}/results/${config.delivery_key}`);
  await page.getByRole('link', {name: 'Lens colour', exact: true}).click();
  await page.waitForURL('**/lens-colour/*');
  await page.waitForFunction(() => document.querySelector('#lens-viewer')?.getAttribute('aria-busy') === 'false');
  await page.waitForFunction(() => !document.querySelector('#lens-transmission')?.disabled);
  await rendered();
  await page.screenshot({path: path.join(config.out, 'panel-before.png'), fullPage: true});
  const initial = await page.locator('#lens-viewer canvas.lens-live-canvas').screenshot();
  async function set(id, value) {
    await page.locator(id).evaluate((element, value) => {
      element.value = value;
      element.dispatchEvent(new Event('input', {bubbles: true}));
      element.dispatchEvent(new Event('change', {bubbles: true}));
    }, value);
  }
  await set('#lens-tint', '#27c3d8');
  await set('#lens-transmission', '0.9');
  await set('#lens-mirror', '0.05');
  await set('#lens-gloss', '0.8');
  await set('#lens-shimmer-strength', '0.7');
  await set('#lens-shimmer-colour', '460');
  await rendered();
  const changed = await page.locator('#lens-viewer canvas.lens-live-canvas').screenshot();
  check(!initial.equals(changed), 'Edited values visibly change the live model');
  await page.screenshot({path: path.join(config.out, 'panel-after.png'), fullPage: true});
  await page.getByRole('button', {name: 'Before', exact: true}).click();
  await rendered();
  check(initial.equals(await page.locator('#lens-viewer canvas.lens-live-canvas').screenshot()), 'Before restores original pixels');
  await page.getByRole('button', {name: 'After', exact: true}).click();
  const popupPromise = context.waitForEvent('page');
  await page.getByRole('button', {name: 'Check in AR', exact: true}).click();
  const preview = await popupPromise;
  await preview.waitForURL(url => url.hostname === '127.0.0.1' && url.port !== new URL(config.base_url).port, {timeout: 180_000});
  await preview.waitForFunction(() => document.querySelector('.stage')?.dataset.externalModel === 'ready');
  check(new URL(preview.url()).searchParams.has('sha256'), 'Temporary AR opens with a hash-bound model');
  report.preview_url = preview.url();
  await preview.screenshot({path: path.join(config.out, 'temporary-ar.png')});
  const saveResponse = page.waitForResponse(response => response.url().endsWith('/lens-colour/save') && response.request().method() === 'POST');
  await page.getByRole('button', {name: 'Save as new version', exact: true}).click();
  const response = await saveResponse;
  report.values = response.request().postDataJSON().materials;
  check(response.ok(), 'Save queues through the real API');
  await page.waitForURL('**/results/*', {timeout: 300_000});
  const deliveryKey = new URL(page.url()).pathname.split('/').at(-1);
  check(deliveryKey !== config.delivery_key, 'Save opens a new immutable delivery');
  report.delivery_key = deliveryKey;
  await page.getByText('edited by you', {exact: true}).waitFor();
  check(await page.getByText('edited by you', {exact: true}).count() === 1, 'Results marks owner edit');
  check(await page.locator('#edit-delivery').isDisabled(), 'Native edits cannot silently restore the old lens values');
  await page.screenshot({path: path.join(config.out, 'saved-results.png'), fullPage: true});
  const savedARPromise = context.waitForEvent('page');
  await page.getByRole('button', {name: 'View in AR', exact: true}).click();
  const savedAR = await savedARPromise;
  await savedAR.waitForURL(url => url.hostname === '127.0.0.1' && url.port !== new URL(config.base_url).port, {timeout: 180_000});
  await savedAR.waitForFunction(() => document.querySelector('.stage')?.dataset.externalModel === 'ready');
  check(new URL(savedAR.url()).searchParams.has('sha256'), 'Saved version passes existing AR review and opens');
  report.saved_ar_url = savedAR.url();
  await page.getByRole('link', {name: 'Jobs', exact: true}).click();
  await page.waitForURL(config.base_url + '/');
  await page.locator(`a[href="/results/${deliveryKey}"]`).waitFor();
  check(await page.locator(`a[href="/results/${deliveryKey}"]`).count() === 1, 'New version appears in Jobs');
  check((await page.locator(`a[href="/results/${deliveryKey}"]`).textContent()).includes('edited by you'), 'Jobs labels edited by you');
  await page.screenshot({path: path.join(config.out, 'jobs.png'), fullPage: true});
  check(errors.length === 0, 'No browser script errors');
  check(blocked.length === 0, 'No external network requests');
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = error.stack;
  await page.screenshot({path: path.join(config.out, 'failure.png'), fullPage: true}).catch(() => {});
  throw error;
} finally {
  report.ended_at = new Date().toISOString();
  await fs.writeFile(path.join(config.out, 'browser.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
