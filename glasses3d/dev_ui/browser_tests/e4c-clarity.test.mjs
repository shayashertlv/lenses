import assert from 'node:assert/strict';
import test from 'node:test';
import {execFileSync} from 'node:child_process';
import {ROOT, startMockServer, launchBrowser, browserContext} from './mock-server.mjs';

const ready = page => page.waitForFunction(() => document.querySelector('#general-viewer')?.getAttribute('aria-busy') === 'false' && !document.querySelector('#edit-ar')?.disabled);
const latest = server => server.state.requests.findLast(row => row.route.endsWith('/appearance-model'))?.body;
async function input(page, name, value) {
  await page.getByLabel(name, {exact: true}).evaluate((node, value) => {
    node.value = value; node.dispatchEvent(new Event('input', {bubbles: true})); node.dispatchEvent(new Event('change', {bubbles: true}));
  }, String(value));
  await ready(page);
}

test('E4c clear owner lists, lens default and gradient shares', {timeout: 180_000}, async t => {
  const server = await startMockServer(); t.after(() => server.close());
  const browser = await launchBrowser(); t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin); t.after(() => context.close());
  if (process.env.E4C_BASELINE_REF) {
    // Replay the initial production files only, with the same browser, fixtures
    // and new tests. Never replace or revert any shared working-tree file.
    await context.route('**/static/**', async route => {
      const file = new URL(route.request().url()).pathname.slice('/static/'.length);
      const body = execFileSync('git', ['show', `${process.env.E4C_BASELINE_REF}:glasses3d/dev_ui/static/${file}`], {cwd: ROOT});
      await route.fulfill({status: 200, contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body});
    });
  }
  const page = await context.newPage();
  page.on('dialog', dialog => dialog.accept());
  const versions = [
    {delivery_key: 'accepted_old', job_id: 'job_demo', label: 'Everyday frame — 2026-10-12 10:00 UTC — Astra c0004 — accepted by you', owner_verdict: 'accepted', created_utc: '2026-10-12T10:00:00Z', lens_swatch: {css: '#42866a'}},
    {delivery_key: 'delivery_demo', job_id: 'job_demo', label: 'Everyday frame — 2026-10-21 12:00 UTC — your edit — not finished', created_utc: '2026-10-21T12:00:00Z', lens_swatch: {css: 'linear-gradient(to right, #ff0000, #0000ff)'}},
    {delivery_key: 'rejected_old', job_id: 'job_demo', label: 'Everyday frame — 2026-10-18 11:00 UTC — Astra c0009 — rejected', owner_verdict: 'rejected', created_utc: '2026-10-18T11:00:00Z', lens_swatch: {css: '#555555'}},
    {delivery_key: 'test_plain', job_id: 'job_test', label: 'Ordinary name — 2026-10-21 14:00 UTC — Astra c0003 — finished', test_job: true, lens_swatch: {css: '#fedcba'}},
  ];
  const lens = server.state.lens.materials;
  server.state.appearance = {...server.state.lens,
    materials: [
      {material_index: 0, name: 'Shared colour', role: 'frame', can_transmission: true, can_gradient: false, values: {...lens[0].values, gradient: null}},
      ...lens.map((row, index) => ({...row, name: `Lens ${index + 1}`, role: 'lens', can_gradient: true, can_transmission: true})),
      {...lens[0], name: 'Repeated physical material', role: 'lens', can_gradient: true, can_transmission: true},
    ], versions, copy_sources: versions, refusals: []};
  server.state.jobs = [
    {job_id: 'owner_product', name: 'Synthetic assembly proof', created_utc: '2026-10-20', deliveries: [], test_job: false},
    {job_id: 'hidden_test', name: 'Ordinary name', created_utc: '2026-10-21', deliveries: [], test_job: true},
  ];
  await t.test('Jobs hides record-marked tests and toggles them without name rules', async () => {
    await page.goto(server.origin); await page.locator('[data-job-id="owner_product"]').waitFor();
    assert.equal(await page.locator('[data-job-id="hidden_test"]').count(), 0);
    await page.getByRole('checkbox', {name: 'Show test jobs', exact: true}).check();
    await page.locator('[data-job-id="hidden_test"]').waitFor();
    assert.equal(server.state.requests.findLast(row => row.route === '/api/jobs').query, '?show_test_jobs=1');
    await page.getByRole('checkbox', {name: 'Show test jobs', exact: true}).uncheck();
    await page.locator('[data-job-id="hidden_test"]').waitFor({state: 'detached'});
    assert.equal(await page.locator('[data-job-id="owner_product"]').count(), 1);
  });
  await t.test('the first lens is selected, role headings remain and each material index appears once', async () => {
    await page.goto(`${server.origin}/edit-version/delivery_demo`); await ready(page);
    assert.equal(await page.locator('#edit-part option:checked').textContent(), 'Lens 1');
    assert.deepEqual(await page.locator('#edit-part optgroup').evaluateAll(rows => rows.map(row => row.label)), ['Lenses', 'Frame']);
    assert.equal(await page.locator('#edit-part option').count(), 3);
    assert.equal(await page.locator('#edit-part option').allTextContents().then(values => values.includes('Repeated physical material')), false);
    await input(page, 'Solid colour', '#32ab74');
    assert.equal(latest(server).materials.length, 3, 'The save payload includes each physical material only once');
    assert.notDeepEqual(latest(server).materials[1].values.tint, lens[0].values.tint, 'The default control edits the lens');
    assert.deepEqual(latest(server).materials[0].values.tint, lens[0].values.tint, 'Frame values stay unchanged');
  });
  await t.test('Copy a look and Versions show full dated labels and every swatch, preserving unsaved edits on filtering', async () => {
    await page.goto(`${server.origin}/edit-version/delivery_demo`); await ready(page);
    assert.deepEqual(await page.locator('.versions-strip a').allTextContents(), versions.slice(0, 3).map(row => row.label));
    assert.equal(await page.locator('.versions-strip .version-swatch[aria-label="Lens look"]').count(), 3);
    assert.deepEqual(await page.locator('#copy-source .copy-source-row').allTextContents(), versions.slice(0, 3).map(row => row.label));
    assert.equal(await page.locator('#copy-source .version-swatch').count(), 3);
    await input(page, 'Solid colour', '#26b28e'); const before = structuredClone(latest(server));
    await page.getByRole('checkbox', {name: 'Show test jobs', exact: true}).check();
    await page.locator('#copy-source input[value="test_plain"]').waitFor();
    assert.equal(await page.locator('.versions-strip a').count(), 4);
    assert.equal(await page.locator('#edit-state').textContent(), 'Unsaved changes');
    assert.equal(await page.getByLabel('Solid colour', {exact: true}).inputValue(), '#26b28e');
    assert.deepEqual(latest(server), before);
    await page.getByRole('checkbox', {name: 'Show test jobs', exact: true}).uncheck();
    await page.locator('#copy-source input[value="test_plain"]').waitFor({state: 'detached'});
    assert.equal(await page.locator('.versions-strip a').count(), 3);
    for (const width of [1280, 1024, 760]) {
      await page.setViewportSize({width, height: 900});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    }
    await page.setViewportSize({width: 1280, height: 900});
    await page.locator('#edit-undo').click(); await ready(page);
    assert.equal(await page.locator('#edit-save').isDisabled(), true, 'Filtering preserved the original undo history');
  });
  await t.test('colour markers are keyboard-operable sliders with takes readouts, per-transition softness and a live bar', async () => {
    await page.goto(`${server.origin}/edit-version/delivery_demo`); await ready(page);
    await page.locator('#edit-part').selectOption({label: 'Lens 1'});
    await page.locator('#colour-gradient').click(); await ready(page);
    await page.locator('#gradient-middle').click(); await ready(page);
    const markers = page.locator('.gradient-stop');
    assert.equal(await markers.count(), 3);
    for (let i = 0; i < 3; i++) assert.equal(await markers.nth(i).getAttribute('role'), 'slider');
    await input(page, 'Start colour', '#ff0000'); await input(page, 'Middle colour', '#00ff00'); await input(page, 'End colour', '#0000ff');
    let gradient = latest(server).materials[1].values.gradient;
    assert.equal(gradient.version, 2); assert.deepEqual(gradient.stops.map(stop => stop.position), [.15, .5, .85]);
    assert.equal(await page.locator('[data-takes="1"]').textContent(), 'Takes about 35% of the bar.');
    const hash = () => page.locator('#gradient-blend-preview').evaluate(canvas => canvas.toDataURL());
    const soft = await hash(); await input(page, 'Softness middle to end', 0); assert.notEqual(await hash(), soft);
    gradient = latest(server).materials[1].values.gradient;
    assert.deepEqual(gradient.softness, [.7, 0]);
    await markers.nth(1).focus(); await page.keyboard.press('ArrowRight'); await ready(page);
    assert.equal(latest(server).materials[1].values.gradient.stops[1].position, .51);
    assert.equal(await markers.nth(1).getAttribute('aria-valuenow'), '51');
    await page.locator('#edit-undo').click(); await ready(page);
    assert.equal(latest(server).materials[1].values.gradient.stops[1].position, .5);
  });
  assert.deepEqual(audit.errors, []); assert.deepEqual(audit.blocked, []);
});
