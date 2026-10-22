import assert from 'node:assert/strict';
import test from 'node:test';
import {execFileSync} from 'node:child_process';
import {ROOT, startMockServer, launchBrowser, browserContext} from './mock-server.mjs';
import {replaceGlbJson} from './fixtures.mjs';

const ready = page => page.waitForFunction(() => document.querySelector('#general-viewer')?.getAttribute('aria-busy') === 'false'
  && !document.querySelector('#edit-ar')?.disabled);
const latest = server => server.state.requests.findLast(row => row.route.endsWith('/appearance-model'))?.body;
const gradient = server => latest(server).materials.find(row => row.material_index === 1).values.gradient;
async function input(page, label, value) {
  await page.getByLabel(label, {exact: true}).evaluate((node, value) => {
    node.value = value; node.dispatchEvent(new Event('input', {bubbles: true})); node.dispatchEvent(new Event('change', {bubbles: true}));
  }, String(value));
  await ready(page);
}

test('E5 radial controls and centre gestures use canonical component metadata', {timeout: 180_000}, async t => {
  const server = await startMockServer(); t.after(() => server.close()); server.state.inspectLens = true;
  const fixture = server.state.lensFixture, doc = structuredClone(fixture.doc);
  // A coarse pair with different materials and different heights. The spare
  // disconnected quad is removed, without changing the source position bytes.
  doc.meshes[0].primitives.splice(1, 1); fixture.doc = doc; fixture.bytes = replaceGlbJson(fixture.bytes, doc);
  const original = structuredClone(server.state.lens.materials);
  const projection = (mesh, component, centre, half_extent, mirror_x) => ({mode: 'pair', surfaces: [
    {mesh_index: mesh, primitive_index: 0, component, lens_id: mirror_x, vertex_ranges: [[0, 3]], centre, half_extent, mirror_x},
  ]});
  const materials = original.map(row => ({...row, role: 'lens', can_gradient: true, can_transmission: true,
    values: {...row.values, gradient: null}, radial_projection: row.material_index === 1
      ? projection(1, 1, [.04, .015], [.035, .02], 1) : projection(0, 0, [-.04, 0], [.035, .035], -1)}));
  const excluded = {material_index: 0, name: 'Existing lens pattern', role: 'lens', can_gradient: false,
    has_colour_texture: true, can_transmission: true, values: {...original[0].values, gradient: null}};
  server.state.appearance = {...server.state.lens, materials: [...materials, excluded], versions: [], copy_sources: [],
    refusals: [{message: 'Lens markings keep their own appearance.'}]};
  const browser = await launchBrowser(); t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin); t.after(() => context.close());
  if (process.env.E5_BASELINE_REF) {
    await context.route('**/static/**', async route => {
      const file = new URL(route.request().url()).pathname.slice('/static/'.length);
      const body = execFileSync('git', ['show', `${process.env.E5_BASELINE_REF}:glasses3d/dev_ui/static/${file}`], {cwd: ROOT});
      await route.fulfill({status: 200, contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body});
    });
  }
  const page = await context.newPage(); page.on('dialog', dialog => dialog.accept());
  const openRadial = async () => {
    await page.goto(`${server.origin}/edit-version/delivery_demo`); await ready(page);
    await page.locator('#colour-gradient').click(); await ready(page);
    assert.ok((await page.locator('#gradient-direction option').allTextContents()).includes('Around a point'));
    await page.locator('#gradient-direction').selectOption('around_point'); await ready(page);
  };
  await t.test('direction, circles, ring stops and softness apply to both pair materials without changing optics or patterns', async () => {
    await openRadial();
    assert.deepEqual(gradient(server).centre, [0, 0]); assert.equal(gradient(server).roundness, 1);
    assert.equal(await page.locator('.gradient-centre-handle').count(), 2);
    assert.match(await page.locator('.general-controls').innerText(), /Paired lenses share these rings/);
    assert.match(await page.locator('.general-controls').innerText(), /existing colour texture keeps its own pattern/);
    await page.locator('#gradient-middle').click(); await ready(page);
    for (const [name, value] of [['Start colour', '#00cc66'], ['Middle colour', '#008844'], ['End colour', '#8c20d5'], ['Start tint strength', .6]]) await input(page, name, value);
    const preview = () => page.locator('#gradient-radial-preview').evaluate(node => node.toDataURL());
    const soft = await preview(); await input(page, 'Softness start to middle', 0); assert.notEqual(await preview(), soft);
    const payload = latest(server), left = payload.materials.find(row => row.material_index === 2);
    assert.deepEqual(left.values.gradient, gradient(server));
    assert.equal(gradient(server).stops.length, 3); assert.equal(gradient(server).stops[0].strength, .6);
    assert.deepEqual(gradient(server).softness, [0, .7]);
    assert.deepEqual(payload.materials.find(row => row.material_index === 0).values, excluded.values);
    for (const originalRow of original) {
      const actual = {...payload.materials.find(row => row.material_index === originalRow.material_index).values}; delete actual.gradient;
      assert.deepEqual(actual, originalRow.values, 'Linking rings never changes optical values');
    }
    assert.equal(await page.getByText('Lens markings keep their own appearance.', {exact: true}).count(), 1);
  });
  await t.test('centre and roundness sliders use the keyboard, preserve radial values in history and remove radial keys for linear directions', async () => {
    await openRadial();
    await input(page, 'Left / right', .25); await input(page, 'Up / down', -.2);
    await input(page, 'Roundness', 2);
    const previous = structuredClone(gradient(server));
    await page.getByLabel('Roundness', {exact: true}).focus(); await page.keyboard.press('ArrowRight'); await ready(page);
    assert.equal(gradient(server).roundness, 2.01);
    await page.locator('#edit-undo').click(); await ready(page); assert.deepEqual(gradient(server), previous);
    await page.locator('#edit-redo').click(); await ready(page); assert.equal(gradient(server).roundness, 2.01);
    await page.getByLabel('Left / right', {exact: true}).focus(); await page.keyboard.press('ArrowRight'); await ready(page);
    assert.deepEqual(gradient(server).centre, [.26, -.2]);
    await page.getByLabel('Up / down', {exact: true}).focus(); await page.keyboard.press('ArrowUp'); await ready(page);
    assert.deepEqual(gradient(server).centre, [.26, -.19]);
    const radial = structuredClone(gradient(server));
    await page.locator('#gradient-direction').selectOption('left_to_right'); await ready(page);
    assert.deepEqual(Object.keys(gradient(server)).sort(), ['direction', 'softness', 'stops', 'version']);
    assert.equal(await page.locator('.gradient-centres').isVisible(), false);
    await page.locator('#edit-undo').click(); await ready(page); assert.deepEqual(gradient(server), radial);
    assert.equal(await page.getByLabel('Roundness', {exact: true}).inputValue(), '2.01');
  });
  await t.test('dragging the real 3D centre mirrors the pair, keeps the camera fixed and creates one undo step', async () => {
    await openRadial();
    const initial = structuredClone(gradient(server));
    const camera = await page.evaluate(() => window.__lensPreview.cameraSpec());
    const destination = await page.evaluate(() => {
      const viewer = window.__lensPreview;
      for (let y = .2; y < .7; y += .01) for (let x = .5; x < .85; x += .01) {
        const hit = viewer.pick([x, y]);
        if (hit?.material_index === 1 && hit.position_mm[0] > 45 && hit.position_mm[0] < 55
            && hit.position_mm[1] > 16 && hit.position_mm[1] < 23) return hit;
      }
      return null;
    });
    assert.ok(destination, 'Production raycast finds the requested interior of the coarse lens');
    assert.equal(destination.mesh_index, 1); assert.equal(destination.primitive_index, 0);
    const canvas = await page.locator('#general-viewer canvas').boundingBox();
    const handle = page.locator('.gradient-centre-handle').first(), start = await handle.boundingBox();
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2); await page.mouse.down();
    await page.mouse.move(canvas.x + destination.normalized[0] * canvas.width, canvas.y + destination.normalized[1] * canvas.height, {steps: 8});
    await page.mouse.up(); await ready(page);
    const moved = structuredClone(gradient(server));
    assert.ok(Math.abs(moved.centre[0] - (destination.position_mm[0] / 1000 - .04) / .035) < 1e-5);
    assert.ok(Math.abs(moved.centre[1] - (destination.position_mm[1] / 1000 - .015) / .02) < 1e-5);
    assert.deepEqual(latest(server).materials.find(row => row.material_index === 2).values.gradient, moved);
    assert.deepEqual(await page.evaluate(() => window.__lensPreview.cameraSpec()), camera);
    const centres = await page.evaluate(() => {
      const viewer = window.__lensPreview;
      return viewer.materialRows.filter(row => row.radial_projection).map(row => viewer.radialCentres(row, {
        direction: 'around_point', centre: [.2, .1], roundness: 1,
      })[0].normalized);
    });
    assert.ok(centres[0][0] > .5 && centres[1][0] < .5);
    await page.locator('#edit-undo').click(); await ready(page); assert.deepEqual(gradient(server), initial);
    await page.locator('#edit-redo').click(); await ready(page); assert.deepEqual(gradient(server), moved);
    await handle.focus(); await page.keyboard.press('ArrowUp'); await ready(page);
    assert.ok(Math.abs(gradient(server).centre[1] - moved.centre[1] - .01) < 1e-12);
  });
  await t.test('saving and reopening restores radial stops, centre and roundness', async () => {
    await openRadial(); await input(page, 'Left / right', -.35); await input(page, 'Up / down', .3); await input(page, 'Roundness', 2.4);
    const expected = structuredClone(latest(server));
    await context.route('**/api/deliveries/delivery_lens/appearance', async route => {
      const saved = server.state.requests.findLast(row => row.route.endsWith('/appearance-save')).body;
      await route.fulfill({json: {...server.state.appearance, materials: server.state.appearance.materials.map(row => ({...row,
        values: saved.materials.find(item => item.material_index === row.material_index).values}))}});
    });
    await page.locator('#edit-save').click(); await page.waitForURL('**/edit-version/delivery_lens'); await ready(page);
    const saved = server.state.requests.findLast(row => row.route.endsWith('/appearance-save')).body;
    assert.deepEqual(saved.materials, expected.materials);
    assert.equal(await page.locator('#gradient-direction').inputValue(), 'around_point');
    assert.equal(await page.getByLabel('Left / right', {exact: true}).inputValue(), '-0.35');
    assert.equal(await page.getByLabel('Up / down', {exact: true}).inputValue(), '0.3');
    assert.equal(await page.getByLabel('Roundness', {exact: true}).inputValue(), '2.4');
    assert.equal(await page.locator('#edit-save').isDisabled(), true);
  });
  await t.test('a split-material shield shares one centre and radial spec across disconnected sheets', async () => {
    // Metadata describes the backend's union-projection decision. Components
    // remain separate for exact raycast matching; the logical lens is shared.
    for (const row of server.state.appearance.materials.filter(row => row.radial_projection)) {
      row.radial_projection.mode = 'single';
      for (const surface of row.radial_projection.surfaces) {
        surface.lens_id = 0; surface.centre = [0, 0]; surface.half_extent = [.075, .035]; surface.mirror_x = 1;
      }
    }
    await openRadial();
    assert.equal(await page.locator('.gradient-centre-handle').count(), 1);
    assert.match(await page.locator('.general-controls').innerText(), /share these rings and one centre/);
    await input(page, 'Left / right', .2); await input(page, 'Roundness', 1.5);
    assert.deepEqual(latest(server).materials.find(row => row.material_index === 2).values.gradient, gradient(server));
    assert.deepEqual(gradient(server).centre, [.2, 0]);
  });
  await t.test('the default centre is the lens area centroid, and one click on the lens places the centre', async () => {
    // A shield whose bounding-box centre sits in the nose cut-out: the area centroid is higher.
    for (const row of server.state.appearance.materials.filter(row => row.radial_projection)) {
      for (const surface of row.radial_projection.surfaces) surface.area_centroid = [surface.centre[0], surface.centre[1] + .35 * surface.half_extent[1]];
    }
    await openRadial();
    assert.deepEqual(gradient(server).centre, [0, .35]);
    await page.locator('#place-centre').click(); assert.equal(await page.locator('#place-centre').getAttribute('aria-pressed'), 'true');
    const point = await page.evaluate(() => {
      const viewer = window.__lensPreview;
      for (let y = .3; y < .7; y += .01) for (let x = .3; x < .7; x += .01) {
        const hit = viewer.pick([x, y]); if (hit?.material_index === 1 || hit?.material_index === 2) return [x, y];
      }
      return null;
    });
    assert.ok(point, 'a lens point is visible');
    const box = await page.locator('#general-viewer canvas').boundingBox();
    await page.mouse.click(box.x + point[0] * box.width, box.y + point[1] * box.height); await ready(page);
    assert.notDeepEqual(gradient(server).centre, [0, .35]);
    assert.equal(await page.locator('#place-centre').getAttribute('aria-pressed'), 'false');
  });
  assert.deepEqual(audit.errors, []); assert.deepEqual(audit.shaderErrors, []); assert.deepEqual(audit.blocked, []);
});
