/** Read-only browser verification of the public landing-to-AR entry path.
 * Camera requests and worker construction are blocked and counted. This checks
 * discovery/navigation only; it makes no rendering or iPhone performance claim.
 */
import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {dirname, resolve} from 'node:path';
import {parseArgs} from 'node:util';
import {chromium} from '@playwright/test';

const {values} = parseArgs({options: {url: {type: 'string'}, output: {type: 'string'}}});
assert(values.url && values.output, 'Usage: node verify-published-entry.mjs --url https://host --output receipt.json');
const base = new URL(values.url);
assert(base.protocol === 'https:' || (base.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(base.hostname)),
  'Use HTTPS for a published site; HTTP is allowed only for local QA.');
assert(base.pathname === '/' && !base.search && !base.hash && !base.username && !base.password,
  'Provide the website origin, without a path, query or credentials.');
const expectedRelease = JSON.parse(await readFile(new URL('../../../mobile-site/release.json', import.meta.url), 'utf8'));
const expectedOptions = ['g', 'face-cpu', 'render-worker', 'frame-copy', 'reuse-compose', 'mask-bytes'];
const livePath = '/ar_testing/experiments/efficiency-lab/live.html';
const browser = await chromium.launch();
const checks = [], pageErrors = [];
try {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, hasTouch: true});
  await context.addInitScript(() => {
    const audit = {cameraRequests: 0, workers: 0};
    window.__publishedEntryAudit = audit;
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      audit.cameraRequests++;
      throw new Error('Camera invocation is forbidden during entry verification.');
    }});
    window.Worker = new Proxy(window.Worker, {construct() {
      audit.workers++;
      throw new Error('Worker construction is forbidden during entry verification.');
    }});
  });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  const releaseResponse = await context.request.get(base.origin + '/ar_testing/release.json', {timeout: 45_000});
  assert(releaseResponse.ok(), 'The published release must load.');
  const servedRelease = await releaseResponse.json();
  assert.deepEqual(servedRelease, expectedRelease, 'Entry verification must use the intended local release.');

  async function assertNoCamera() {
    assert.deepEqual(await page.evaluate(() => window.__publishedEntryAudit), {cameraRequests: 0, workers: 0});
    assert.deepEqual(pageErrors, []);
  }
  async function inspect(label, study, options, selected = 'g') {
    await page.waitForFunction(({study, options}) => location.search === '?study=' + study
      && JSON.stringify(Array.from(document.querySelectorAll('#pipeline-select option'), option => option.value)) === JSON.stringify(options),
    {study, options}, {timeout: 45_000});
    assert.equal(page.url(), base.origin + livePath + '?study=' + study);
    assert.equal(await page.locator('#pipeline-select').inputValue(), selected);
    assert.equal(await page.locator('#continuous-video').isChecked(), false);
    assert.equal(await page.locator('.stage').getAttribute('data-state'), 'idle');
    assert.equal(await page.locator('.study-links a').count(), 4);
    assert.equal(await page.locator('#stability-study').getAttribute('href'), '?study=g-stability');
    assert.equal(await page.locator('#fps-review-study').getAttribute('href'), '?study=fps-review');
    assert.equal(await page.locator('#mask-preview-study').getAttribute('href'), '?study=mask-preview');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await assertNoCamera();
    const observation = {label, url: page.url(), options, selected, title: await page.title(),
      measureButton: await page.locator(['g-stability', 'readback-diagnostic'].includes(study) ? '#stability-start' : '#continuous-start').textContent(),
      cameraRequests: 0, workers: 0, noHorizontalOverflow: true};
    if (study === 'readback-diagnostic') {
      assert.equal(observation.title, 'Lenses · G readback diagnostic');
      assert.equal(await page.locator('#readback-study').getAttribute('aria-current'), 'page');
      assert.equal(await page.locator('#readback-study').getAttribute('href'), '?study=readback-diagnostic');
      assert.equal(await page.locator('#stability-panel').isVisible(), true);
      assert.equal(await page.locator('#stability-start').isEnabled(), true);
      assert.equal(await page.locator('#stability-condition').inputValue(), 'all');
      assert.deepEqual(await page.locator('#stability-condition option').evaluateAll(options => options.map(option => option.value)), ['all','readback-control','readback-diagnostic']);
      assert.equal(await page.locator('#stability-order').inputValue(), 'forward');
      assert.equal(await page.locator('#refresh-pipeline').isVisible(), false);
      observation.scope = 'G readback diagnostic';
    } else if (study === 'g-stability') {
      assert.equal(observation.title, 'Lenses · G stability tests');
      assert.equal(await page.locator('#study-heading').textContent(), 'See why G slows down.');
      assert.equal(await page.locator('#stability-study').getAttribute('aria-current'), 'page');
      assert.equal(await page.locator('#stability-panel').isVisible(), true);
      assert.equal(await page.locator('#stability-start').isVisible(), true);
      assert.equal(await page.locator('#stability-condition').inputValue(), 'all');
      assert.deepEqual(await page.locator('#stability-condition option').evaluateAll(options => options.map(option => option.value)),
        ['all', 'continuous', 'restarted', 'fresh-page']);
      assert.equal(await page.locator('#stability-order').inputValue(), 'forward');
      assert.deepEqual(await page.locator('#stability-order option').evaluateAll(options => options.map(option => option.value)),
        ['forward', 'reverse']);
      assert.match(await page.locator('#stability-status').textContent(), /\S/);
      assert.equal(await page.locator('#refresh-pipeline').isVisible(), false);
      observation.scope = 'G stability';
      observation.conditions = ['continuous', 'restarted', 'fresh-page'];
      observation.order = 'forward';
    } else if (study === 'fps-review') {
      assert.equal(await page.locator('#review-candidate').inputValue(), 'all');
      assert.equal(await page.locator('#fps-review-study').getAttribute('aria-current'), 'page');
      assert.equal(await page.locator('#mask-preview-study').textContent(), 'Earlier G / V preview');
      assert.equal(await page.locator('#continuous-title').textContent(), 'All six options · 7 minutes + setup');
      assert.equal(observation.measureButton, 'Measure only · all six options · ~7 min + setup');
      assert.equal(await page.locator('#runtime-policy').textContent(), 'Fresh runtime for each switch. Setup time is separate from measured FPS.');
      assert.equal(await page.locator('#refresh-pipeline').isVisible(), true);
      assert.equal(await page.locator('#refresh-pipeline').isEnabled(), true);
      assert.equal(await page.locator('#refresh-pipeline').textContent(), 'Refresh selected test');
      assert.match(await page.locator('#continuous-protocol').textContent(), /released and rebuilt before every window, including repeated options/);
      assert.equal(await page.evaluate(() => window.hairLivePreview.diagnostics().runtimeIsolation), 'fresh-runtime');
      observation.runtimeIsolation = 'fresh-runtime';
      observation.refreshSelectedTest = true;
      observation.scope = 'all';
    } else {
      assert.equal(await page.locator('#mask-preview-study').getAttribute('aria-current'), 'page');
      assert.match(observation.measureButton, /Measure only.*G \/ V/);
      assert.equal(await page.locator('#refresh-pipeline').isVisible(), false);
      assert.equal(await page.locator('#runtime-policy').textContent(), '');
      observation.refreshSelectedTest = false;
    }
    checks.push(observation);
  }

  const landing = await page.goto(base.origin + '/', {waitUntil: 'domcontentloaded'});
  assert.equal(landing?.status(), 200);
  const entry = page.locator('a[href="/ar_testing/"]');
  assert.equal(await entry.count(), 1);
  assert.equal(await entry.textContent(), 'ar_testing');
  await assertNoCamera();
  await entry.click();
  await inspect('Website landing → ar_testing', 'readback-diagnostic', ['g', 'g-readback']);
  await page.click('#fps-review-study');
  await inspect('G diagnostics → All FPS experiments', 'fps-review', expectedOptions);
  await page.click('#mask-preview-study');
  await inspect('Explicit G / V navigation', 'mask-preview', ['g', 'mask-bytes']);
  await page.click('#stability-study');
  await inspect('G / V → G stability', 'g-stability', ['g']);
  await page.click('#readback-study');
  await inspect('G stability → G diagnostics', 'readback-diagnostic', ['g', 'g-readback']);
  await page.goto(base.origin + livePath);
  await inspect('Query-free AR bookmark', 'readback-diagnostic', ['g', 'g-readback']);
  await page.goto(base.origin + livePath + '?study=review');
  await inspect('Historical production redirect', 'readback-diagnostic', ['g', 'g-readback']);
  await page.goto(base.origin + livePath + '?study=review&legacy=1');
  await page.waitForFunction(() => JSON.stringify(Array.from(document.querySelectorAll('#pipeline-select option'), option => option.value))
    === JSON.stringify(['g', 'publish', 'region', 'lens', 'ui']), undefined, {timeout: 45_000});
  assert.equal(page.url(), base.origin + livePath + '?study=review&legacy=1');
  assert.equal(await page.locator('#pipeline-select').inputValue(), 'g');
  assert.equal(await page.locator('.stage').getAttribute('data-state'), 'idle');
  await assertNoCamera();
  checks.push({label: 'Explicit legacy review remains available', url: page.url(),
    options: ['g', 'publish', 'region', 'lens', 'ui'], selected: 'g', cameraRequests: 0, workers: 0});
  const receipt = {schema: 'ar-published-entry-verification-v1', verifiedAt: new Date().toISOString(),
    baseUrl: base.origin, release: servedRelease, checks, pageErrors,
    scope: 'Desktop Chromium at a 390px mobile viewport. Website landing, production redirect, client default and navigation only. Camera and workers are blocked; no rendering or iPhone performance claim.'};
  const output = resolve(values.output);
  await mkdir(dirname(output), {recursive: true});
  await writeFile(output, JSON.stringify(receipt, null, 2) + '\n', 'utf8');
  process.stdout.write('Verified landing → G diagnostics, preserved G stability, all six FPS experiments and G / V navigation; release '
    + servedRelease.sourceFingerprint.slice(0, 12) + '.\n');
} finally {
  await browser.close();
}
