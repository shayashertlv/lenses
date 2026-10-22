import assert from 'node:assert/strict';
import test from 'node:test';
import {ROOT, startMockServer, launchBrowser, browserContext} from './mock-server.mjs';
import path from 'node:path';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {replaceGlbJson} from './fixtures.mjs';

const baselineRef = process.env.E4_BASELINE_REF;

const ready = page => page.waitForFunction(() => document.querySelector('#general-viewer')?.getAttribute('aria-busy') === 'false' && !document.querySelector('#edit-ar')?.disabled);
async function set(page, label, value) {
  await page.getByLabel(label, {exact: true}).evaluate((input, value) => {input.value = value; input.dispatchEvent(new Event('input', {bubbles: true})); input.dispatchEvent(new Event('change', {bubbles: true}));}, String(value));
  await ready(page);
}
const latest = server => server.state.requests.findLast(row => row.route.endsWith('/appearance-model'))?.body;
async function assertRendered(page) {
  const counts = await page.evaluate(async () => {
    const capture = window.__lensPreview.capture(), image = new Image(); image.src = capture.data_url; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0); const bytes = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let nonBackground = 0, opaque = 0;
    for (let i = 0; i < bytes.length; i += 4) {if (bytes[i+3] === 255) opaque++; if (Math.abs(bytes[i] - bytes[0]) + Math.abs(bytes[i+1] - bytes[1]) + Math.abs(bytes[i+2] - bytes[2]) > 20) nonBackground++;}
    return {nonBackground, opaque, size: canvas.width * canvas.height};
  });
  assert.equal(counts.opaque, counts.size, 'The live AR context remains intact after reload');
  assert.ok(counts.nonBackground > counts.size * .01, 'The actual model remains visible after reload');
}

test('E4 one general editor exposes role-based controls and preserves the M12 shape flow', {timeout: 180_000}, async t => {
  const server = await startMockServer(); t.after(() => server.close()); server.state.inspectLens = true;
  const browser = await launchBrowser(); t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin); t.after(() => context.close());
  if (baselineRef) {
    const baselineApp = execFileSync('git', ['show', `${baselineRef}:glasses3d/dev_ui/static/app.js`], {cwd: ROOT, encoding: 'utf8'});
    await context.route('**/static/app.js', route => route.fulfill({status: 200, contentType: 'text/javascript', body: baselineApp}));
  }
  const page = await context.newPage();
  await t.test('one Edit button opens photos, AR preview, own roles and version strip at laptop widths', async () => {
    server.state.delivery.origin = 'owner_look_edit';
    await page.goto(`${server.origin}/results/delivery_demo`); await page.locator('#edit-delivery').waitFor();
    assert.equal(await page.locator('.status-tag').first().textContent(), 'edited by you');
    server.state.delivery.origin = 'run'; assert.equal(await page.locator('#lens-colour').count(), 0);
    await page.getByRole('button', {name: 'Edit', exact: true}).click(); await page.waitForURL('**/edit-version/delivery_demo'); await ready(page);
    assert.deepEqual(await page.locator('#edit-part optgroup').evaluateAll(groups => groups.map(g => g.label)), ['Lenses', 'Frame']);
    assert.equal(await page.locator('.general-photos img').count(), 4);
    assert.equal(await page.locator('.versions-strip a').count(), 2);
    assert.equal(await page.getByText('Details', {exact: true}).evaluate(el => el.parentElement.open), false);
    assert.equal(await page.locator('#edit-save').isDisabled(), true);
    for (const width of [1280, 1024, 760]) {await page.setViewportSize({width, height: 900}); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);}
    await page.setViewportSize({width: 1280, height: 900});
    server.state.lens.photos[0].camera = await page.evaluate(() => window.__lensPreview.cameraSpec());
    server.state.lens.photos[0].camera_photo_url = server.state.lens.photos[0].url;
    await page.reload(); await ready(page);
    await page.getByRole('button', {name: 'Photo 1: front', exact: true}).click();
    assert.equal(await page.locator('.photo-wipe').isVisible(), true);
    await page.getByLabel('Photo and render wipe', {exact: true}).fill('75');
    assert.equal(await page.locator('.photo-wipe').evaluate(img => img.style.clipPath), 'inset(0px 25% 0px 0px)');
    await page.getByRole('button', {name: 'Photo 2: angled', exact: true}).click();
    assert.equal(await page.locator('#photo-angle-note').textContent(), "This photo's angle isn't available for this model");
  });
  // An old entrypoint cannot reach any downstream editor flow. Preserve the
  // first behavioral failure without spending seven UI timeouts on that absence.
  if (baselineRef) return;
  await t.test('clear, solid and three-colour gradients keep stop positions, per-transition softness, tint strength and every direction', async () => {
    await set(page, 'Solid colour', '#28a4d8'); assert.equal(latest(server).materials[0].values.gradient, null);
    await page.locator('#colour-gradient').click(); await ready(page); await page.locator('#gradient-middle').click(); await ready(page);
    for (const [label, value] of [['Start colour', '#ff0000'], ['Middle colour', '#00ff00'], ['End colour', '#0000ff'],
      ['Softness start to middle', .9], ['Softness middle to end', .1], ['End tint strength', .4]]) await set(page, label, value);
    for (const direction of ['centre_to_sides', 'top_to_bottom', 'left_to_right']) {
      await page.locator('#gradient-direction').selectOption(direction); await ready(page);
      const gradient = latest(server).materials[0].values.gradient;
      assert.equal(gradient.version, 2); assert.equal(gradient.direction, direction); assert.equal(gradient.stops.length, 3);
      assert.deepEqual(gradient.softness, [.9, .1]); assert.equal(gradient.stops[2].strength, .4);
      assert.deepEqual(gradient.stops.map(stop => stop.position), [.15, .5, .85]);
    }
    await page.locator('#gradient-middle').click(); await ready(page); assert.equal(latest(server).materials[0].values.gradient.stops.length, 2);
    await page.locator('#edit-undo').click(); await ready(page); assert.equal(latest(server).materials[0].values.gradient.stops.length, 3);
    await page.locator('#colour-clear').click(); await ready(page);
    const cleared = latest(server).materials[0].values; assert.equal(cleared.gradient, null); assert.deepEqual(cleared.tint, [1, 1, 1]);
    await page.locator('#edit-undo').click(); await ready(page); assert.equal(latest(server).materials[0].values.gradient.stops.length, 3);
    await assertRendered(page);
  });
  await t.test('see-through, mirror, shine, coating colours and unbounded exact optical values round trip', async () => {
    for (const [label, value] of [['See-through', .6], ['Mirror', .2], ['Shine', .85]]) await set(page, label, value);
    await page.locator('#coating-two').click(); await ready(page);
    let values = latest(server).materials[0].values;
    assert.equal(values.transmission, .6); assert.equal(values.metallic, .2); assert.ok(Math.abs(values.roughness - .15) < 1e-12); assert.ok(values.iridescence > 0);
    await page.locator('#coating-brightness').selectOption('6'); await ready(page); assert.equal(latest(server).materials[0].values.ior, 6);
    await set(page, 'Coating strength', 1);
    // Before any colour is picked, the targets are the coating's current colours and Find still works.
    await page.locator('#find-coating').click(); await page.getByRole('button', {name: 'Use coating 1', exact: true}).waitFor();
    await set(page, 'Straight-on colour', '#5abe8c'); await set(page, 'Side colour', '#aa6ec8');
    await page.locator('#find-coating').click();
    const use = page.getByRole('button', {name: 'Use coating 1', exact: true}); await use.waitFor(); await use.click(); await ready(page);
    values = latest(server).materials[0].values;
    assert.equal(values.iridescence_thickness_min, values.iridescence_thickness_max); assert.ok(values.iridescence_ior >= 1.2 && values.iridescence_ior <= 2.4);
    const [centre, sides] = await page.evaluate(async chosen => {const renderer = await import('/lens-renderer/lens-renderer.js');
      return [renderer.coatingSwatchRGB(chosen, 1), renderer.coatingSwatchRGB(chosen, .4)];}, values);
    assert.ok(centre[1] > centre[0] && centre[1] > centre[2], `green straight on: ${centre}`);
    assert.ok(sides[2] > sides[1] && sides[0] > sides[1], `purple at the sides: ${sides}`);
    await page.locator('#coating-one').click(); await set(page, 'Coating colour', '#3c78d8'); await page.locator('#find-coating').click();
    await page.getByRole('button', {name: 'Use coating 1', exact: true}).waitFor();
    await page.locator('#coating-off').click(); await ready(page); assert.equal(latest(server).materials[0].values.iridescence, 0);
    await page.locator('#edit-undo').click(); await ready(page); assert.ok(latest(server).materials[0].values.iridescence > 0);
    values = latest(server).materials[0].values;
    await page.getByText('Details', {exact: true}).click(); await set(page, 'Lens IOR', 6);
    await set(page, 'Film IOR', 3.2);
    await set(page, 'Minimum thickness (nm)', 1500);
    await set(page, 'Maximum thickness (nm)', 10);
    values = latest(server).materials[0].values; assert.equal(values.ior, 6); assert.equal(values.iridescence_ior, 3.2); assert.equal(values.iridescence_thickness_min, 1500); assert.equal(values.iridescence_thickness_max, 10);
    await set(page, 'Film IOR', 1.37);
    assert.equal(await page.locator('#coating-brightness').inputValue(), '6');
  });
  await t.test('photo eyedropper, keyboard undo redo and frame pattern tint are general', async () => {
    await page.locator('#colour-solid').click(); await ready(page); await page.getByRole('button', {name: 'Pick solid colour from photo'}).click();
    const photo = page.locator('#general-photo-canvas'); await photo.waitFor(); await photo.focus(); await photo.press('ArrowRight'); await photo.press('Enter'); await ready(page);
    await page.locator('#general-viewer canvas').focus(); await page.keyboard.press('Control+z'); await ready(page); await page.keyboard.press('Control+y'); await ready(page);
    await page.locator('#edit-part').selectOption('2'); assert.match(await page.locator('.general-controls').innerText(), /Colour tints the existing pattern/);
    assert.equal(await page.locator('#colour-gradient').count(), 0); await set(page, 'Solid colour', '#aa5417'); await set(page, 'Metal or plastic', .25);
    await set(page, 'See-through', .45); assert.equal(latest(server).materials.find(row => row.material_index === 0).values.transmission, .45);
    await page.getByRole('button', {name: 'Copy look from this job'}).click(); await page.getByText('No matching material names were found in this job.', {exact: true}).waitFor();
    await page.locator('#edit-part').selectOption('0'); await page.getByRole('button', {name: 'Copy lens look from another model'}).click(); await ready(page);
    assert.deepEqual(latest(server).materials[0].values.tint, [.9,.1,.2]);
  });
  await t.test('comparison keeps history and unsaved links guard before temporary AR and immutable save', async () => {
    await page.locator('#edit-before').click(); await ready(page); assert.equal(await page.locator('#edit-before').getAttribute('aria-pressed'), 'true');
    await page.locator('#edit-after').click(); await ready(page); assert.equal(await page.locator('#edit-save').isDisabled(), false);
    await assertRendered(page);
    page.once('dialog', dialog => dialog.dismiss()); await page.getByRole('link', {name: 'Results', exact: true}).click(); assert.ok(page.url().includes('/edit-version/'));
    const popupPromise = context.waitForEvent('page'); await page.locator('#edit-ar').click(); const popup = await popupPromise; await popup.waitForURL('**/__test/ar'); await popup.close(); await ready(page);
    assert.ok(server.state.requests.some(row => row.route.endsWith('/appearance-preview')));
    const out = process.env.G3D_E4_BROWSER_EVIDENCE || path.join(ROOT, 'glasses3d/data/m12/codex/e4_browser'); await fs.mkdir(out, {recursive: true}); await page.screenshot({path: path.join(out, 'general-page.png'), fullPage: true});
    await page.locator('#edit-save').click(); await page.waitForURL('**/edit-version/delivery_lens'); await ready(page);
    assert.ok(server.state.requests.some(row => row.route.endsWith('/appearance-save')));
  });
  await t.test('raycast part selection preanchors the original capture and annotation flow', async () => {
    const pick = await page.evaluate(() => {const viewer = window.__lensPreview;
      const originalPick = viewer.pick.bind(viewer); viewer.pick = normalized => {const result = originalPick(normalized); window.__clickedPick = result; return result;};
      for (let y = .1; y < .9; y += .04) for (let x = .1; x < .9; x += .04) {const hit = viewer.pick([x,y]); if (hit?.material_index === 1) return [x,y];}
      return null;
    }); assert.ok(pick, 'Raycast finds an explicitly owned lens');
    const box = await page.locator('#general-viewer canvas').boundingBox(); await page.mouse.click(box.x + pick[0] * box.width, box.y + pick[1] * box.height);
    const clicked = await page.evaluate(() => window.__clickedPick);
    await page.locator('#ask-astra').click(); await page.waitForURL('**/edit/draft_demo'); await page.locator('[data-testid="annotation-dialog"]').waitFor();
    assert.equal(await page.locator('[data-testid="mark-list"] .mark-row').count(), 1);
    await page.getByRole('button', {name: 'Done annotating', exact: true}).click(); await page.getByLabel('What should Astra change?').fill('Move A inward.'); await page.getByRole('button', {name: 'Done', exact: true}).click();
    await page.locator('[data-testid="annotation-dialog"]').waitFor({state: 'detached'});
    const stored = JSON.parse(server.state.requests.findLast(row => row.route.endsWith('/items') && row.parts?.capture).parts.capture);
    const anchor = stored.marks[0].vertices[0].anchor;
    assert.equal(anchor.kind, 'surface', 'The selected point remains on the actual native source surface');
    assert.ok(Math.hypot(...anchor.position_mm.map((value, index) => value - clicked.position_mm[index])) < 1e-5,
      `The frozen annotation anchor matches the selected model point: ${JSON.stringify({anchor: anchor.position_mm, clicked: clicked.position_mm})}`);
    await page.getByRole('button', {name: 'No, send to Astra', exact: true}).click(); assert.equal(await page.locator('#send-edits').textContent(), 'Send to Astra (paid)');
    assert.equal(server.state.requests.some(row => row.route.endsWith('/send')), false, 'Build tests never press a real paid button');
  });
  await t.test('folded photo pose maps its hit back to the identical authored surface', async () => {
    const fixture = server.state.lensFixture, doc = structuredClone(fixture.doc);
    doc.materials[1].extras.partRole = 'temple'; fixture.bytes = replaceGlbJson(fixture.bytes, doc);
    server.state.lens.materials[0].name = 'Temple material';
    await page.goto(`${server.origin}/edit-version/delivery_demo`); await ready(page);
    const clicked = await page.evaluate(() => {
      const viewer = window.__lensPreview, camera = viewer.cameraSpec();
      camera.temple_pose = {'+1': {theta_deg: 45, axis_xz_mm: [5, 10]}};
      viewer.setExplicitCamera(camera); viewer.render();
      for (let y = .1; y < .9; y += .02) for (let x = .1; x < .9; x += .02) {
        const hit = viewer.pick([x, y]);
        if (hit?.material_index === 1 && Math.hypot(...hit.rendered_position_mm.map((value, i) => value - hit.position_mm[i])) > 2) return hit;
      } return null;
    });
    assert.ok(clicked, 'The production renderer folds the explicitly tagged synthetic temple');
    assert.ok(Math.abs(clicked.position_mm[2] - 10) < 1e-5, 'Canonical point remains on the original z=10 mm plane');
    await page.evaluate(selection => sessionStorage.setItem('shape-anchor:draft_demo', JSON.stringify(selection)), clicked);
    await page.goto(`${server.origin}/edit/draft_demo`); await page.locator('[data-testid="annotation-dialog"]').waitFor();
    await page.getByRole('button', {name: 'Done annotating', exact: true}).click(); await page.getByLabel('What should Astra change?').fill('Refine B.'); await page.getByRole('button', {name: 'Done', exact: true}).click();
    await page.locator('[data-testid="annotation-dialog"]').waitFor({state: 'detached'});
    const stored = JSON.parse(server.state.requests.findLast(row => row.route.endsWith('/items') && row.parts?.capture).parts.capture), vertex = stored.marks[0].vertices[0];
    assert.equal(vertex.anchor.kind, 'surface');
    assert.ok(Math.hypot(...vertex.anchor.position_mm.map((value, i) => value - clicked.position_mm[i])) < 1e-5);
    assert.equal(stored.camera.projection_matrix[8], 0, 'Native capture keeps the authenticated centred camera contract');
    assert.equal(stored.camera.projection_matrix[9], 0);
  });
  assert.deepEqual(audit.errors, []); assert.deepEqual(audit.shaderErrors, []); assert.deepEqual(audit.blocked, []);
});
