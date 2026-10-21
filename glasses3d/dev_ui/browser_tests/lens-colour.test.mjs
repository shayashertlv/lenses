import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import {ROOT, startMockServer, launchBrowser, browserContext} from './mock-server.mjs';
import {LensHistory, hexToTint, tintToHex} from '../static/lens-colour.js';
import {makeLensGlb} from './fixtures.mjs';

test('E2 history groups gestures, preserves untouched values and round-trips photo sRGB', () => {
  const original = makeLensGlb().materials;
  const history = new LensHistory(original);
  history.update(rows => { rows[0].values.transmission = 0.2; });
  history.update(rows => { rows[0].values.transmission = 0.3; });
  history.commit();
  assert.equal(history.past.length, 1);
  assert.deepEqual(history.current[1], original[1]);
  assert.equal(history.dirty, true);
  history.undo(); assert.equal(history.dirty, false);
  history.redo(); assert.equal(history.current[0].values.transmission, 0.3);
  history.undo(); history.update(rows => { rows[1].values.metallic = 0.7; }, true);
  assert.equal(history.future.length, 0);
  for (const hex of ['#000000', '#ffffff', '#6c7eaa', '#ff0000', '#010203']) assert.equal(tintToHex(hexToTint(hex)), hex);
  assert.deepEqual(original, makeLensGlb().materials);
});

const frame = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const capture = page => page.evaluate(() => window.__lensPreview.capture().data_url);
const materials = page => page.evaluate(() => [...window.__lensPreview.materials].map(([index, entries]) => {
  const material = [...entries][0];
  return {index, tint: material.color.toArray(), transmission: material.transmission, metallic: material.metalness,
    roughness: material.roughness, iridescence: material.iridescence, ior: material.ior,
    iridescence_ior: material.iridescenceIOR, iridescence_thickness_min: material.iridescenceThicknessRange[0],
    iridescence_thickness_max: material.iridescenceThicknessRange[1]};
}).sort((a, b) => a.index - b.index));
async function setControl(page, id, value, commit = true) {
  await page.locator(`#lens-${id}`).evaluate((input, {value, commit}) => {
    if (input.type === 'checkbox') input.checked = value;
    else input.value = value;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    if (commit) input.dispatchEvent(new Event('change', {bubbles: true}));
  }, {value, commit});
  await frame(page);
}

test('E2 real-renderer browser editor controls, photos, history, AR and immutable-save flow', {timeout: 180_000}, async t => {
  const server = await startMockServer(); t.after(() => server.close()); server.state.inspectLens = true;
  const browser = await launchBrowser(); t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin); t.after(() => context.close());
  const page = await context.newPage();
  const evidence = process.env.G3D_LENS_EVIDENCE_DIR || path.join(ROOT, 'glasses3d/data/m12/codex'); await fs.mkdir(evidence, {recursive: true});
  const original = structuredClone(server.state.lens.materials);
  let initialPixels;
  try {
    await t.test('results opens a clear responsive panel using the actual AR renderer', async () => {
      await page.goto(`${server.origin}/results/delivery_demo`);
      assert.equal(await page.locator('#lens-colour').count(), 0, 'Results has one general Edit entry point');
      await page.goto(`${server.origin}/lens-colour/delivery_demo`);
      await page.waitForFunction(() => window.__lensPreview && !document.querySelector('#lens-check-ar').disabled);
      await frame(page);
      assert.equal(await page.locator('#lens-save').isDisabled(), true);
      assert.equal(await page.locator('.lens-advanced').getAttribute('open'), null);
      assert.equal(await page.locator('#lens-photos img').count(), 4);
      assert.equal(await page.locator('.lens-photo-dialog').count(), 0);
      const policy = await page.evaluate(() => window.__lensPreview.capture().policy);
      assert.equal(policy.renderer, 'TryOnRenderer'); assert.equal(policy.lens_environment, 1);
      initialPixels = await capture(page);
      for (const width of [1280, 1024, 760]) {
        await page.setViewportSize({width, height: 900}); await frame(page);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `no horizontal overflow at ${width}px`);
        const corners = await page.evaluate(() => {
          const viewer = window.__lensPreview; viewer.capture();
          const corners = [], bounds = viewer.bounds;
          for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
            corners.push(viewer.camera.position.clone().set(x, y, z).project(viewer.camera).toArray());
          }
          return corners;
        });
        assert.ok(corners.every(corner => corner.every(value => Number.isFinite(value) && value >= -1 && value <= 1)), `all model bounds stay visible after resizing to ${width}px`);
        await page.screenshot({path: path.join(evidence, `e2_panel_${width}.png`), fullPage: true});
      }
      await page.setViewportSize({width: 1280, height: 900}); await frame(page);
      initialPixels = await capture(page);
      await page.screenshot({path: path.join(evidence, 'e2_panel_before.png'), fullPage: true});
    });

    await t.test('every control changes the real material and live pixels; before/after is reversible', async () => {
      await page.locator('.lens-advanced summary').click();
      const changes = [
        ['tint', '#ff3322', 'tint', hexToTint('#ff3322')],
        ['transmission', 0.15, 'transmission', 0.15],
        ['mirror', 0.6, 'metallic', 0.6],
        ['gloss', 0.35, 'roughness', 0.65],
        ['shimmer-on', false, 'iridescence', 0],
        ['shimmer-on', true, 'iridescence', 1],
        ['shimmer-strength', 0.32, 'iridescence', 0.32],
        ['shimmer-colour', 520, 'iridescence_thickness_max', 520],
        ['ior', 2.2, 'ior', 2.2],
        ['film-ior', 1.8, 'iridescence_ior', 1.8],
      ];
      for (const [control, value, field, expected] of changes) {
        const before = await capture(page);
        await setControl(page, control, value);
        for (const material of await materials(page)) assert.deepEqual(material[field], expected, `${control} edits both real lens materials`);
        assert.notEqual(await capture(page), before, `${control} changes real AR-rendered pixels`);
      }
      const edited = await capture(page);
      assert.equal(await page.locator('#lens-save-state').textContent(), 'Unsaved changes');
      await page.locator('#lens-before').click(); await frame(page);
      assert.equal(await capture(page), initialPixels, 'Before exactly restores the original material render');
      await page.locator('#lens-after').click(); await frame(page);
      assert.equal(await capture(page), edited, 'After exactly restores the edited material render');
      await page.evaluate(() => window.scrollTo(0, 0)); await frame(page);
      await page.screenshot({path: path.join(evidence, 'e2_panel_after.png'), fullPage: true});
    });

    await t.test('unlinked edits, per-control reset, reset all and gesture undo/redo preserve the other lens', async () => {
      await page.locator('#lens-link').uncheck();
      const before = await materials(page);
      await setControl(page, 'tint', '#123456');
      const after = await materials(page);
      assert.deepEqual(after[0].tint, hexToTint('#123456')); assert.deepEqual(after[1], before[1]);
      await page.getByRole('button', {name: 'Reset Tint', exact: true}).click(); await frame(page);
      assert.deepEqual((await materials(page))[0].tint, original[0].values.tint);
      await page.locator('#lens-reset-all').click(); await frame(page);
      assert.equal(await page.locator('#lens-save').isDisabled(), true);
      await page.locator('#lens-link').check();
      await setControl(page, 'transmission', 0.3, false);
      await setControl(page, 'transmission', 0.4, false);
      await setControl(page, 'transmission', 0.5);
      await page.keyboard.press('Control+z'); await frame(page);
      assert.equal((await materials(page))[0].transmission, 0.65);
      await page.keyboard.press('Control+y'); await frame(page);
      assert.equal((await materials(page))[0].transmission, 0.5);
      await page.locator('#lens-undo').click(); await frame(page);
      assert.equal((await materials(page))[0].transmission, 0.65);
      await page.locator('#lens-redo').click(); await frame(page);
      assert.equal((await materials(page))[0].transmission, 0.5);
    });

    await t.test('reference photos enlarge and sample actual pixels with pointer and keyboard', async () => {
      await page.locator('#lens-eyedropper').click();
      await page.waitForFunction(() => !document.querySelector('#lens-photo-sample').disabled);
      const box = await page.locator('#lens-photo-canvas').boundingBox();
      // MouseEvent client coordinates are integers in this Chromium path. Pick
      // an integer viewport point inside native pixel (8, 6), even when the
      // dialog places its 32×24 canvas at a fractional CSS top coordinate.
      const x0 = box.x + box.width * 8 / 32, x1 = box.x + box.width * 9 / 32;
      const y0 = box.y + box.height * 6 / 24, y1 = box.y + box.height * 7 / 24;
      const point = {x: Math.ceil(x0), y: Math.ceil(y0)};
      assert.ok(point.x >= x0 && point.x < x1 && point.y >= y0 && point.y < y1, 'click is independently inside native pixel (8, 6)');
      await page.mouse.click(point.x, point.y);
      await frame(page);
      assert.equal(await page.locator('#lens-tint').inputValue(), '#6c7eaa');
      assert.deepEqual((await materials(page))[0].tint, hexToTint('#6c7eaa'));
      await page.locator('[data-view="side"]').click();
      assert.equal(await page.locator('[data-view="side"]').getAttribute('aria-pressed'), 'true');
      await page.locator('#lens-photos button').nth(2).click();
      await page.locator('#lens-photo-pick').click();
      await page.locator('#lens-photo-canvas').press('ArrowRight');
      await page.locator('#lens-photo-canvas').press('Enter'); await frame(page);
      assert.equal(await page.locator('#lens-tint').inputValue(), '#7584aa');
      assert.equal(await page.locator('.lens-photo-dialog').count(), 0);
      await page.locator('[data-view="front"]').click();
    });

    await t.test('unsaved navigation can be cancelled and AR opens a detached temporary preview', async () => {
      page.once('dialog', dialog => dialog.dismiss());
      await page.getByRole('link', {name: 'Jobs', exact: true}).click();
      assert.equal(new URL(page.url()).pathname, '/lens-colour/delivery_demo');
      server.state.lensPreviewPending = true;
      const popupPromise = context.waitForEvent('page');
      const pendingTask = page.waitForResponse(response => response.url().endsWith('/api/tasks/task_lens_preview'));
      await page.locator('#lens-check-ar').click();
      const popup = await popupPromise;
      assert.equal((await (await pendingTask).json()).phase, 'running');
      await popup.getByRole('heading', {name: 'Preparing AR preview…', exact: true}).waitFor();
      assert.equal(await popup.title(), 'Preparing AR preview… · Glasses3D');
      assert.equal(await popup.getByRole('status').isVisible(), true);
      assert.match(await popup.getByRole('status').textContent(), /open it automatically when it is ready/);
      assert.equal(await popup.evaluate(() => window.opener === null), true);
      server.state.lensPreviewPending = false;
      await popup.waitForURL('**/__test/ar');
      const request = server.state.requests.findLast(row => row.route.endsWith('/lens-colour/preview'));
      assert.equal(request.body.source_sha256, server.state.lens.source.glb_sha256);
      assert.equal(request.body.materials.length, 2);
      assert.deepEqual(request.body.materials[0].values.tint, hexToTint('#7584aa'));
      assert.equal(server.state.lensSaved, false);
      assert.equal(await page.locator('#lens-save-state').textContent(), 'Unsaved changes');
      await popup.close();
    });

    await t.test('save failure preserves controls; successful new version opens results and appears in Jobs', async () => {
      const route = '/api/deliveries/delivery_demo/lens-colour/save';
      server.state.nextError = {route, message: 'The source could not be verified. Your changes are still here.'};
      await page.locator('#lens-save').click();
      await page.waitForFunction(() => !document.querySelector('#lens-save').disabled);
      assert.match(await page.locator('#app .error').textContent(), /source could not be verified/);
      assert.equal(await page.locator('#lens-tint').inputValue(), '#7584aa');
      server.state.lensSaveFailure = 'The local save failed. Please try again.';
      server.state.lensSaveRecoverable = true;
      await page.locator('#lens-save').click();
      await page.waitForFunction(() => !document.querySelector('#lens-save').disabled);
      assert.match(await page.locator('#app .error').textContent(), /continue the same save/);
      server.state.lensSaveRecoverable = false;
      await page.locator('#lens-save').click();
      await page.waitForFunction(() => !document.querySelector('#lens-save').disabled);
      assert.match(await page.locator('#app .error').textContent(), /local save failed/);
      assert.equal(server.state.lensSaved, false);
      server.state.lensSaveFailure = null;
      await page.locator('#lens-save').click();
      await page.waitForURL('**/results/delivery_lens');
      assert.equal(await page.locator('.status-tag').first().textContent(), 'edited by you');
      assert.equal(await page.locator('.status-tag').last().textContent(), 'not finished');
      assert.equal(await page.locator('#edit-delivery').isDisabled(), false, 'Owner looks remain editable through the general page');
      const sends = server.state.requests.filter(row => row.route === route);
      assert.equal(sends[0].body.idempotency_key, sends[1].body.idempotency_key, 'same values retry one save');
      assert.equal(sends[1].body.idempotency_key, sends[2].body.idempotency_key, 'interrupted recoverable save resumes the same task');
      assert.notEqual(sends[2].body.idempotency_key, sends[3].body.idempotency_key, 'confirmed failed task can be retried as a new save');
      assert.deepEqual(server.state.lens.materials, original, 'GET baseline stayed immutable');
      await page.getByRole('link', {name: 'Jobs', exact: true}).click();
      await page.getByRole('link', {name: 'Delivery · edited by you'}).waitFor();
      assert.equal(await page.getByRole('link', {name: 'Delivery · edited by you'}).count(), 1);
    });

    await t.test('loading failure and missing lens values show a usable recovery state', async () => {
      server.state.nextError = {route: '/api/deliveries/delivery_demo/lens-colour', message: 'The lens values could not be loaded.'};
      await page.goto(`${server.origin}/lens-colour/delivery_demo`);
      await page.getByRole('button', {name: 'Try loading again'}).waitFor();
      assert.equal(await page.getByRole('button', {name: 'Try loading again'}).count(), 1);
      assert.equal(await page.getByRole('link', {name: 'Back to results'}).count(), 1);
      server.state.lens.materials = [];
      await page.reload();
      await page.waitForSelector('.notice');
      assert.match(await page.locator('.notice').textContent(), /no editable lens materials/);
    });
    assert.deepEqual(audit.errors, []); assert.deepEqual(audit.shaderErrors, []); assert.deepEqual(audit.blocked, []);
  } finally {
    await fs.writeFile(path.join(evidence, 'e2_browser_audit.json'), JSON.stringify(audit, null, 2));
  }
});
