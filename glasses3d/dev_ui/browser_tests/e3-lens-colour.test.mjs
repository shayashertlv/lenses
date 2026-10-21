import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import {execFileSync} from 'node:child_process';
import {ROOT, startMockServer, launchBrowser, browserContext} from './mock-server.mjs';
import {makeLensGlb, sha256} from './fixtures.mjs';
import {coatingSwatchRGB} from '../lens_renderer/coating.js';
import {srgbToLinear, linearToSrgb} from '../static/lens-colour.js';

// Optional old-code replay reads Git blobs; it never reverts the shared checkout.
const baselineRef = process.env.E3_BASELINE_REF;
const evidence = process.env.G3D_E3_EVIDENCE_DIR || path.join(ROOT, 'glasses3d/data/m12/codex/e3_browser');
function baselineSource(relative) {
  try { return execFileSync('git', ['show', `${baselineRef}:glasses3d/dev_ui/${relative}`], {cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']}); }
  catch (error) { if (error.status === 128) return null; throw error; }
}
async function finder() {
  const module = baselineRef
    ? await (async () => {
      const source = baselineSource('static/coating-match.js');
      return source ? import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`) : {};
    })()
    : await import('../static/coating-match.js');
  assert.equal(typeof module.findCoatingThicknesses, 'function', 'the coating thickness finder is available');
  return module.findCoatingThicknesses;
}
const chromaticity = rgb => {
  const linear = rgb.map(value => srgbToLinear(value / 255));
  const total = linear.reduce((sum, value) => sum + value, 0);
  return linear.map(value => value / total);
};
const colourDistance = (a, b) => Math.hypot(...chromaticity(a).map((value, index) => value - chromaticity(b)[index]));
const frame = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const materialValues = page => page.evaluate(() => [...window.__lensPreview.materials].map(([index, entries]) => {
  const material = [...entries][0];
  return {index, tint: material.color.toArray(), transmission: material.transmission, metallic: material.metalness,
    roughness: material.roughness, iridescence: material.iridescence, ior: material.ior,
    iridescence_ior: material.iridescenceIOR, iridescence_thickness_min: material.iridescenceThicknessRange[0],
    iridescence_thickness_max: material.iridescenceThicknessRange[1]};
}).sort((a, b) => a.index - b.index));

async function openPanel(page, server) {
  await page.goto(`${server.origin}/lens-colour/delivery_demo`);
  await page.waitForFunction(() => window.__lensPreview && !document.querySelector('#lens-check-ar').disabled);
  await frame(page);
}
async function enterNumber(page, id, value) {
  assert.equal(await page.locator(`#lens-${id}-number`).count(), 1, `${id} offers exact numeric entry`);
  await page.locator(`#lens-${id}-number`).fill(String(value));
  await page.locator(`#lens-${id}-number`).press('Tab');
  await frame(page);
}
async function baselineRoute(context) {
  if (!baselineRef) return;
  const source = baselineSource('static/lens-colour.js');
  assert.ok(source, `baseline panel source exists at ${baselineRef}`);
  await context.route('**/static/lens-colour.js', route => route.fulfill({status: 200, contentType: 'text/javascript', body: source}));
}

test('E3 finder recovers known straight-on coating colours and ignores exposure', async () => {
  const find = await finder();
  for (const ior of [1.5, 6]) for (const thickness of [125, 350, 520, 790]) {
    const values = {...makeLensGlb().materials[0].values, ior, iridescence: 0.8,
      iridescence_thickness_min: thickness, iridescence_thickness_max: thickness};
    const original = structuredClone(values), rgb = coatingSwatchRGB(values, 1), calls = [];
    const result = find(rgb, values, (candidate, cosine) => {
      calls.push([candidate.iridescence_thickness_max, cosine]);
      return coatingSwatchRGB(candidate, cosine);
    });
    assert.equal(result.reason, null);
    assert.ok(result.choices.length >= 2 && result.choices.length <= 3);
    assert.equal(new Set(result.choices.map(choice => choice.thickness)).size, result.choices.length);
    assert.deepEqual(new Set(calls.map(([value]) => value)), new Set(Array.from({length: 1201}, (_, index) => index)), 'every integer thickness is searched');
    assert.ok(calls.every(([, cosine]) => cosine === 1), 'only the straight-on colour is matched');
    const candidateRGB = choice => coatingSwatchRGB({...values, iridescence_thickness_min: choice.thickness, iridescence_thickness_max: choice.thickness}, 1);
    assert.ok(result.choices.some(choice => choice.thickness === thickness || colourDistance(candidateRGB(choice), rgb) < 1e-10), `known ${thickness} nm or an equal-colour order is returned at IOR ${ior}`);
    const darker = rgb.map(channel => 255 * linearToSrgb(srgbToLinear(channel / 255) * 0.25));
    const brighter = rgb.map(channel => 255 * linearToSrgb(srgbToLinear(channel / 255) * 1.1));
    for (const exposed of [darker, brighter]) {
      const matched = find(exposed, values, coatingSwatchRGB);
      assert.ok(colourDistance(candidateRGB(matched.choices[0]), rgb) < 1e-10, 'uniform linear exposure does not change the best coating colour');
    }
    assert.deepEqual(values, original, 'finding choices does not edit any material field');
  }
});

test('E3 browser labels, coating photo choices and exact numeric entry', {timeout: 120_000}, async t => {
  const server = await startMockServer(); t.after(() => server.close()); server.state.inspectLens = true;
  const browser = await launchBrowser(); t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin); t.after(() => context.close());
  await baselineRoute(context); await fs.mkdir(evidence, {recursive: true});
  const page = await context.newPage();
  try {
    await t.test('bar explains its angles and tint picker is named unambiguously', async () => {
      await openPanel(page, server);
      assert.deepEqual(await page.locator('.lens-swatch-labels').first().locator('span').allTextContents(),
        ['Seen at an angle (lens sides)', 'Seen straight on (lens centre)']);
      assert.equal(await page.getByRole('button', {name: 'Pick tint from photo', exact: true}).count(), 1);
      assert.equal(await page.getByRole('button', {name: 'Pick coating colour from photo', exact: true}).count(), 1);
      assert.match(await page.locator('#lens-shimmer-colour-help').textContent(), /thickness/i);
      assert.match(await page.locator('#lens-shimmer-colour-help').textContent(), /one coating.*different angles/i);
      await page.screenshot({path: path.join(evidence, 'e3_labelled_bar.png'), fullPage: true});
      await page.locator('.lens-shimmer').screenshot({path: path.join(evidence, 'e3_labelled_bar_detail.png')});
    });
    await t.test('photo offers two or three coating bars; choosing one changes only thickness and undoes/redoes', async () => {
      server.state.lens.materials[0].values.iridescence = 0;
      server.state.lens.materials[1].values.ior = 6;
      await openPanel(page, server);
      const original = await materialValues(page);
      assert.equal(await page.locator('#lens-link').isChecked(), true);
      assert.equal(await page.locator('#lens-material').isDisabled(), false, 'linked lenses still allow choosing the coating settings to match');
      await page.locator('#lens-material').selectOption('1'); await frame(page);
      assert.deepEqual(await materialValues(page), original, 'selecting linked settings changes no material values');
      assert.equal(await page.getByRole('button', {name: 'Pick coating colour from photo', exact: true}).count(), 1);
      await page.getByRole('button', {name: 'Pick coating colour from photo', exact: true}).click();
      await page.waitForFunction(() => !document.querySelector('#lens-photo-sample').disabled);
      const box = await page.locator('#lens-photo-canvas').boundingBox();
      const x0 = box.x + box.width * 8 / 32, x1 = box.x + box.width * 9 / 32;
      const y0 = box.y + box.height * 6 / 24, y1 = box.y + box.height * 7 / 24;
      const point = {x: Math.ceil(x0), y: Math.ceil(y0)};
      assert.ok(point.x >= x0 && point.x < x1 && point.y >= y0 && point.y < y1);
      await page.mouse.click(point.x, point.y);
      const choices = page.locator('#lens-coating-choices button[data-thickness]');
      await choices.first().waitFor();
      const count = await choices.count();
      assert.ok(count >= 2 && count <= 3);
      const find = await finder();
      const expectedChoices = find([108, 126, 170], server.state.lens.materials[1].values, coatingSwatchRGB).choices.map(choice => choice.thickness);
      assert.deepEqual(await choices.evaluateAll(buttons => buttons.map(button => Number(button.dataset.thickness))), expectedChoices, 'matching uses the selected lens IOR, film IOR and strength');
      assert.equal(await choices.locator('canvas').count(), count, 'each choice has its own angular bar');
      assert.match(await page.locator('#lens-coating-match-help').textContent(), /closest coating/i);
      assert.match(await page.locator('#lens-coating-match-help').textContent(), /preview.*AR/i);
      assert.deepEqual(await materialValues(page), original, 'sampling proposes choices without applying an edit');
      await page.screenshot({path: path.join(evidence, 'e3_coating_choices.png'), fullPage: true});
      await page.locator('.lens-photo-dialog').screenshot({path: path.join(evidence, 'e3_coating_choices_detail.png')});
      const thickness = Number(await choices.first().getAttribute('data-thickness'));
      assert.ok(Number.isInteger(thickness) && thickness >= 0 && thickness <= 1200);
      await choices.first().click(); await frame(page);
      const expected = original.map(value => ({...value, iridescence_thickness_min: thickness, iridescence_thickness_max: thickness}));
      assert.deepEqual(await materialValues(page), expected, 'tint, mirror, IOR, strength and other values are unchanged');
      await page.locator('#lens-undo').click(); await frame(page);
      assert.deepEqual(await materialValues(page), original);
      await page.locator('#lens-redo').click(); await frame(page);
      assert.deepEqual(await materialValues(page), expected);
      await page.locator('#lens-reset-all').click(); await frame(page);
    });
    await t.test('high IOR number entries preserve all digits, exceed practical slider ranges and support zero lens IOR', async () => {
      await openPanel(page, server);
      await page.locator('.lens-advanced summary').click();
      for (const value of [6, 12.3456789012345, 0]) {
        await enterNumber(page, 'ior', value);
        assert.equal(await page.locator('#lens-ior-number').inputValue(), String(value));
        assert.equal(await page.locator('#lens-ior-value').textContent(), String(value));
        for (const material of await materialValues(page)) assert.equal(material.ior, value);
      }
      await enterNumber(page, 'film-ior', 20.1234567890123);
      assert.equal(await page.locator('#lens-film-ior-number').inputValue(), '20.1234567890123');
      assert.equal(await page.locator('#lens-film-ior-value').textContent(), '20.1234567890123');
      for (const material of await materialValues(page)) assert.equal(material.iridescence_ior, 20.1234567890123);
      for (const [id, invalid, valid, field] of [['ior', 0.5, 6, 'ior'], ['film-ior', 0.9, 3.2, 'iridescence_ior']]) {
        const before = await materialValues(page);
        await enterNumber(page, id, invalid);
        assert.equal(await page.locator(`#lens-${id}-number`).getAttribute('aria-invalid'), 'true');
        assert.deepEqual(await materialValues(page), before, 'invalid exact entry does not alter any material');
        await enterNumber(page, id, valid);
        assert.equal(await page.locator(`#lens-${id}-number`).evaluate(input => input.validity.valid), true, 'correcting the value clears validation');
        assert.notEqual(await page.locator(`#lens-${id}-number`).getAttribute('aria-invalid'), 'true');
        for (const material of await materialValues(page)) assert.equal(material[field], valid);
      }
      await page.locator('#lens-reset-all').click(); await frame(page);
    });
    await t.test('a valid loaded thickness above 1200 nm and fractional IOR display exactly without creating an edit', async () => {
      for (const material of server.state.lens.materials) Object.assign(material.values, {ior: 9.1234567890123,
        iridescence_ior: 12.9876543210987, iridescence_thickness_min: 2400.125, iridescence_thickness_max: 1700.625});
      await openPanel(page, server);
      await page.locator('.lens-advanced summary').click();
      assert.equal(await page.locator('#lens-ior-number').count(), 1, 'loaded values have an exact numeric entry');
      assert.equal(await page.locator('#lens-ior-number').inputValue(), '9.1234567890123');
      assert.equal(await page.locator('#lens-film-ior-number').inputValue(), '12.9876543210987');
      assert.equal(await page.locator('#lens-thickness-min-number').inputValue(), '2400.125');
      assert.equal(await page.locator('#lens-thickness-max-number').inputValue(), '1700.625');
      assert.equal(await page.locator('#lens-shimmer-colour-number').inputValue(), '1700.625');
      assert.equal(await page.locator('#lens-save').isDisabled(), true, 'opening valid out-of-slider values remains a no-op');
      for (const material of await materialValues(page)) {
        const expected = server.state.lens.materials.find(row => row.material_index === material.index).values;
        assert.deepEqual(material, {index: material.index, ...expected});
      }
    });
    assert.deepEqual(audit, {blocked: [], errors: [], shaderErrors: []});
  } finally { await fs.writeFile(path.join(evidence, 'e3_browser_audit.json'), JSON.stringify(audit, null, 2)); }
});

test('E3 browser opens private copies of run 4 and lab favourite at their exact stored high IORs', {timeout: 90_000}, async t => {
  const sourceDirectory = path.join(ROOT, 'glasses3d/data/lens_lab/gallery/models');
  try { await Promise.all(['00.glb', '17.glb'].map(name => fs.access(path.join(sourceDirectory, name)))); }
  catch { t.skip('The ignored local run 4 and favourite lens lab evidence is unavailable.'); return; }
  await fs.mkdir(evidence, {recursive: true});
  const copies = await fs.mkdtemp(path.join(evidence, 'real-copies-'));
  const server = await startMockServer(); t.after(() => server.close()); server.state.inspectLens = true;
  const browser = await launchBrowser(); t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin); t.after(() => context.close());
  await baselineRoute(context);
  const page = await context.newPage(), records = [];
  for (const [name, expectedIOR] of [['00.glb', 6], ['17.glb', 3.200000047683716]]) await t.test(name, async () => {
    const original = path.join(sourceDirectory, name), copy = path.join(copies, name), before = await fs.readFile(original);
    await fs.copyFile(original, copy);
    const decoded = JSON.parse(execFileSync(path.join(ROOT, 'glasses3d/.venv/Scripts/python.exe'), ['-c',
      'import json,sys; from glasses3d.agent.owner_lens_edit import read_lens_materials; print(json.dumps(read_lens_materials(sys.argv[1])))', copy],
    {cwd: ROOT, encoding: 'utf8', windowsHide: true}));
    assert.equal(decoded.glb_sha256, sha256(before));
    const primaryIndex = decoded.materials.findIndex(row => row.values.ior === expectedIOR);
    assert.ok(primaryIndex >= 0);
    assert.equal(decoded.materials[primaryIndex].values.ior, expectedIOR);
    server.state.lens.materials = decoded.materials;
    server.state.lens.source = {delivery_key: 'delivery_demo', glb_sha256: decoded.glb_sha256, model_url: `/__test/e3-${name}`};
    await context.route(`**/__test/e3-${name}`, route => route.fulfill({status: 200, contentType: 'model/gltf-binary', body: before}));
    await openPanel(page, server);
    await page.locator('#lens-link').uncheck();
    await page.locator('#lens-material').selectOption(String(primaryIndex));
    await page.locator('.lens-advanced summary').click();
    assert.equal(await page.locator('#lens-ior-number').count(), 1, 'the exact stored IOR is available as a number');
    assert.equal(await page.locator('#lens-ior-number').inputValue(), String(expectedIOR));
    assert.equal(await page.locator('#lens-ior-value').textContent(), String(expectedIOR));
    assert.equal(await page.locator('#lens-save').isDisabled(), true);
    for (const material of await materialValues(page)) assert.equal(material.ior, decoded.materials.find(row => row.material_index === material.index).values.ior);
    await page.screenshot({path: path.join(evidence, `e3_real_${name.replace('.glb', '')}.png`), fullPage: true});
    assert.equal(sha256(await fs.readFile(original)), sha256(before), 'original evidence stayed byte-identical');
    assert.equal(sha256(await fs.readFile(copy)), sha256(before), 'private copy stayed byte-identical');
    records.push({name, ior: expectedIOR, source_sha256: sha256(before), copy});
  });
  await fs.writeFile(path.join(evidence, 'e3_real_copies.json'), JSON.stringify({records, audit}, null, 2));
  assert.deepEqual(audit, {blocked: [], errors: [], shaderErrors: []});
});
