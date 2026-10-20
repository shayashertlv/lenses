import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import {DATA, ROOT, startMockServer, launchBrowser, browserContext} from './mock-server.mjs';
import {sha256} from './fixtures.mjs';

const requests = (server, method, route) => server.state.requests.filter(entry => entry.method === method && entry.route === route);
const lastRequest = (server, method, route) => requests(server, method, route).at(-1);
const screenshot = (page, name) => page.screenshot({path: path.join(DATA, `${name}.png`), fullPage: true});
const parseCapture = value => JSON.parse(typeof value === 'string' ? value : value.bytes.toString('utf8'));
const near = (actual, expected, epsilon = 1e-6) => assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} differs from ${expected} by more than ${epsilon}`);

async function fixtureViewer(page, origin, source, model = '/api/deliveries/delivery_demo/model.glb') {
  await page.goto(`${origin}/__test/viewer`);
  await page.evaluate(async ({source, model}) => {
    const {createViewer} = await import('/static/viewer.js');
    window.fixtureViewer = await createViewer(document.querySelector('#fixture-viewer'), {modelUrl: model, source});
    const viewer = window.fixtureViewer;
    viewer.camera.position.set(0, 0, 0.3); viewer.controls.target.set(0, 0, 0);
    viewer.camera.up.set(0, 1, 0); viewer.camera.lookAt(0, 0, 0); viewer.controls.update(); viewer.camera.updateMatrixWorld();
  }, {source, model});
}

test('M12-B browser contracts, annotation and generated-GLB evidence', {timeout: 420_000}, async t => {
  const server = await startMockServer();
  t.after(() => server.close());
  const browser = await launchBrowser();
  t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin, {deviceScaleFactor: 2});
  t.after(() => context.close());
  const page = await context.newPage();
  try {
    await t.test('annotation state and label warnings preserve closing, label and cross-item rules', async () => {
      await page.goto(`${server.origin}/__test/viewer`);
      const result = await page.evaluate(async () => {
        const {AnnotationState} = await import('/static/annotation.js');
        const {labelWarnings} = await import('/static/label-warnings.js');
        const state = new AnnotationState();
        for (let i = 0; i < 15; i++) state.click([i, i]);
        const defaults = state.marks.map(mark => mark.label);
        state.clear(); state.tool = 'polyline'; state.click([10, 10]); state.click([20, 20]); state.click([30, 30]);
        state.undo(); const undone = state.pending.map(p => [...p]); state.finishLine();
        const line = structuredClone(state.marks[0]); state.clear(); state.tool = 'closed_shape';
        state.click([0, 0]); state.click([30, 0]); state.click([30, 30]); state.finishLine();
        const open = state.marks.length; state.click([0, 0]);
        const shape = structuredClone(state.marks[0]);
        const shared = new AnnotationState(['A', 'B']); shared.click([2, 2]);
        let duplicate = false; try {shared.rename(0, 'B');} catch {duplicate = true;}
        shared.rename(0, 'R7'); const edited = shared.marks[0].label; shared.deleteSelected();
        return {defaults, undone, line, open, shape, duplicate, edited, deleted: shared.marks.length,
          unknown: labelWarnings('A I Z Z R12', ['B'], ['B']),
          usedElsewhere: labelWarnings('Change A.', ['A', 'B'], ['A', 'B'], ['Also change B.']),
          empty: labelWarnings(' \n\t', ['A'], ['A']),
          caseSensitive: labelWarnings('a b X1suffix prefixX1', ['A', 'B'], ['A', 'B']),
          punctuation: labelWarnings('(R7), A/B.', ['R7', 'A', 'B'], ['R7', 'A', 'B'])};
      });
      assert.deepEqual(result.defaults, ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'J', 'K', 'L', 'M', 'N', 'P', 'Q']);
      assert.deepEqual(result.undone, [[10, 10], [20, 20]]); assert.equal(result.line.vertices.length, 2);
      assert.equal(result.open, 0); assert.equal(result.shape.vertices.length, 3);
      assert.equal(result.duplicate, true); assert.equal(result.edited, 'R7'); assert.equal(result.deleted, 0);
      assert.deepEqual(result.unknown.map(w => [w.code, w.label]), [['unknown_label', 'R12'], ['unknown_label', 'Z'], ['unused_label', 'B']]);
      assert.deepEqual(result.usedElsewhere, []); assert.deepEqual(result.punctuation, []);
      assert.deepEqual(result.empty.map(w => w.code), ['empty_text', 'unused_label']);
      assert.deepEqual(result.caseSensitive.map(w => [w.code, w.label]), [['unused_label', 'A'], ['unused_label', 'B']]);
    });

    await t.test('initial camera projects all eight model bounds corners inside a tight fit at every aspect', async () => {
      await page.goto(`${server.origin}/__test/viewer`);
      const fits = await page.evaluate(async source => {
        const THREE = await import('three'), {createViewer} = await import('/static/viewer.js');
        const host = document.querySelector('#fixture-viewer'), results = [];
        for (const [width, height] of [[1000, 400], [800, 500], [600, 600], [320, 640]]) {
          host.style.width = `${width}px`; host.style.height = `${height}px`;
          const viewer = await createViewer(host, {modelUrl: '/api/deliveries/delivery_demo/model.glb', source});
          viewer.camera.updateMatrixWorld();
          const bounds = new THREE.Box3().setFromObject(viewer.root), corners = [];
          for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) corners.push(new THREE.Vector3(x, y, z).project(viewer.camera).toArray());
          results.push({width, height, aspect: viewer.camera.aspect, corners, root: {position: viewer.root.position.toArray(), scale: viewer.root.scale.toArray(), quaternion: viewer.root.quaternion.toArray()}});
          viewer.dispose();
        }
        host.style.width = '1000px'; host.style.height = '500px';
        window.followupFitViewer = await createViewer(host, {modelUrl: '/api/deliveries/delivery_demo/model.glb', source});
        return results;
      }, server.state.source);
      for (const fit of fits) {
        assert.equal(fit.corners.length, 8); near(fit.aspect, fit.width / fit.height);
        assert.deepEqual(fit.root, {position: [0, 0, 0], scale: [1, 1, 1], quaternion: [0, 0, 0, 1]});
        assert.ok(fit.corners.every(corner => corner.every(value => Number.isFinite(value) && value >= -1 && value <= 1)), `all corners in ${fit.width}x${fit.height} view/frustum`);
        const span = axis => (Math.max(...fit.corners.map(corner => corner[axis])) - Math.min(...fit.corners.map(corner => corner[axis]))) / 2;
        const fill = Math.max(span(0), span(1));
        assert.ok(fill >= 0.85 && fill <= 0.99, `model fills ${(fill * 100).toFixed(1)}% of limiting ${fit.width}x${fit.height} dimension, with a small margin`);
      }
      await fs.writeFile(path.join(DATA, 'followup1-camera-fits.json'), JSON.stringify(fits, null, 2));
      await screenshot(page, 'followup1-fit-synthetic');
      await page.evaluate(() => window.followupFitViewer.dispose());
    });

    await t.test('near-full-window annotation maximizes landscape and portrait images with exact resized pixel mapping', async () => {
      for (const [width, height, label] of [[1600, 900, 'landscape'], [600, 1600, 'portrait']]) {
        await page.setViewportSize({width: 1280, height: 800}); await page.goto(`${server.origin}/__test/viewer`);
        await page.evaluate(async ({width, height, source}) => {
          const {annotateCapture} = await import('/static/annotation.js');
          const clean = document.createElement('canvas'); clean.width = width; clean.height = height;
          const context = clean.getContext('2d'); context.fillStyle = '#eeeeec'; context.fillRect(0, 0, width, height);
          const blob = await new Promise(resolve => clean.toBlob(resolve, 'image/png'));
          await annotateCapture({capture: async () => ({blob, width, height, camera: {}, anchorAt: () => ({kind: 'background'}), footprint: () => ({pixels: 0, faces: []}), dispose() {}})}, {
            source, labels: [], captureId: 'C-layout', onSave: async bundle => {window.layoutSaved = bundle.capture;},
          });
        }, {width, height, source: server.state.source});
        const metrics = [];
        for (const size of [{width: 1280, height: 800}, {width: 1366, height: 768}]) {
          await page.setViewportSize(size);
          await page.waitForFunction(() => {
            const dialog = document.querySelector('[data-testid="annotation-dialog"]');
            return dialog && Math.abs(dialog.getBoundingClientRect().width / innerWidth - 0.96) < 0.02;
          });
          const measured = await page.evaluate(() => {
            const dialog = document.querySelector('[data-testid="annotation-dialog"]'), canvas = document.querySelector('[data-testid="annotation-canvas"]'), image = document.querySelector('.annotation-image'), list = document.querySelector('[data-testid="mark-list"]');
            const rect = element => {const {x, y, width, height, right, bottom} = element.getBoundingClientRect(); return {x, y, width, height, right, bottom};};
            return {viewport: [innerWidth, innerHeight], dialog: rect(dialog), canvas: rect(canvas), area: rect(image), list: rect(list), image: [canvas.width, canvas.height], scrollWidth: dialog.scrollWidth, clientWidth: dialog.clientWidth, scrollHeight: dialog.scrollHeight, clientHeight: dialog.clientHeight};
          });
          near(measured.dialog.width / size.width, 0.96, 0.02); near(measured.dialog.height / size.height, 0.94, 0.02);
          assert.ok(measured.dialog.x >= 0 && measured.dialog.y >= 0 && measured.dialog.right <= size.width && measured.dialog.bottom <= size.height, `${label} dialog fits viewport`);
          assert.ok(measured.scrollWidth <= measured.clientWidth + 1 && measured.scrollHeight <= measured.clientHeight + 1, `${label} workspace needs no dialog scrolling`);
          near(measured.canvas.width / measured.canvas.height, width / height, 0.002);
          assert.ok(measured.canvas.x >= measured.area.x - 1 && measured.canvas.y >= measured.area.y - 1 && measured.canvas.right <= measured.area.right + 1 && measured.canvas.bottom <= measured.area.bottom + 1, `${label} canvas contained in image area`);
          assert.ok(measured.canvas.right <= measured.list.x + 1, `${label} labels remain beside image`);
          const largestScale = Math.min(measured.area.width / width, measured.area.height / height);
          const actualScale = measured.canvas.width / width;
          assert.ok(actualScale >= largestScale * 0.97 && actualScale <= largestScale * 1.01, `${label} canvas uses maximum available image area`);
          metrics.push(measured);
        }
        const canvas = page.getByTestId('annotation-canvas'), box = await canvas.boundingBox();
        await page.mouse.click(box.x + box.width * 0.31, box.y + box.height * 0.63);
        await screenshot(page, `followup1-dialog-${label}`);
        await page.getByRole('button', {name: 'Done annotating', exact: true}).click();
        await page.getByLabel('What should Astra change?', {exact: true}).fill('Change A.');
        await screenshot(page, `followup1-dialog-${label}-words`);
        await page.getByRole('button', {name: 'Done', exact: true}).click();
        const capture = await page.evaluate(() => window.layoutSaved);
        near(capture.marks[0].vertices[0].xy_px[0] / width, 0.31, 0.003); near(capture.marks[0].vertices[0].xy_px[1] / height, 0.63, 0.003);
        await fs.writeFile(path.join(DATA, `followup1-dialog-${label}.json`), JSON.stringify(metrics, null, 2));
      }
      await page.setViewportSize({width: 1280, height: 900});
    });

    await t.test('new run validates owner fields, keeps exact input and prevents double submission', async () => {
      await page.goto(`${server.origin}/new`);
      assert.equal(await page.locator('#run-effort').textContent(), 'Product runs use max effort. Paid edit sends use high effort.');
      await page.locator('#offline-banner').waitFor({state: 'visible'});
      assert.equal(await page.locator('#offline-banner').innerText(), 'OFFLINE TEST MODE: scripted Astra, $0.00');
      assert.equal(await page.locator('#cutoff').inputValue(), '');
      await page.locator('#photos').setInputFiles([
        {name: 'front owner.png', mimeType: 'image/png', buffer: server.state.png},
        {name: 'side.png', mimeType: 'image/png', buffer: server.state.png},
      ]);
      assert.equal(await page.locator('select[name="kind"]').count(), 2);
      assert.deepEqual(await page.locator('select[name="kind"]').evaluateAll(nodes => nodes.map(node => node.value)), ['', '']);
      assert.deepEqual(await page.locator('select[name="side"]').evaluateAll(nodes => nodes.map(node => node.value)), ['', '']);
      assert.equal(await page.locator('#new-run-form img').count(), 2);
      await page.locator('#start-run').click();
      assert.equal(requests(server, 'POST', '/api/jobs/start').length, 0);
      const description = '  Owner words\nkeep this exact.  ', specs = ' { "width_mm": 145, "note": "  exact  " }\n';
      await page.locator('#description').fill(description);
      await page.locator('#name').fill('Owner short name');
      await page.locator('#specs').fill('[]');
      await page.locator('select[name="kind"]').nth(0).selectOption('front');
      await page.locator('select[name="side"]').nth(0).selectOption('both');
      await page.locator('select[name="kind"]').nth(1).selectOption('side');
      await page.locator('select[name="side"]').nth(1).selectOption('left');
      await page.locator('#cutoff').fill('25.50');
      await page.locator('#start-run').click();
      assert.equal(requests(server, 'POST', '/api/jobs/start').length, 0, 'specs must be a JSON object');
      await page.locator('#specs').fill(specs);
      await screenshot(page, 'new');
      server.state.nextError = {route: '/api/jobs/start', message: 'The test launcher is temporarily unavailable.'};
      await page.locator('#start-run').click();
      await page.getByText('The test launcher is temporarily unavailable.', {exact: true}).waitFor();
      assert.equal(await page.locator('#start-run').isEnabled(), true);
      server.state.startDelay = 350;
      await page.locator('#start-run').click();
      assert.equal(await page.locator('#start-run').isDisabled(), true);
      await page.locator('#start-run').evaluate(button => button.click());
      await page.waitForURL('**/run/run_demo');
      const sends = requests(server, 'POST', '/api/jobs/start');
      assert.equal(sends.length, 2, 'one failed press plus one successful press; no duplicate');
      const manifest = JSON.parse(sends[1].parts.manifest);
      assert.deepEqual(Object.keys(manifest).sort(), ['cutoff_usd', 'description', 'idempotency_key', 'name', 'photos', 'specs_text']);
      assert.equal(manifest.description, description); assert.equal(manifest.specs_text, specs);
      assert.equal(manifest.name, 'Owner short name'); assert.equal(manifest.cutoff_usd, '25.50');
      assert.ok(manifest.idempotency_key); assert.notEqual(manifest.idempotency_key, JSON.parse(sends[0].parts.manifest).idempotency_key);
      assert.deepEqual(manifest.photos.map(photo => ({filename: photo.filename, kind: photo.kind, side: photo.side})), [{filename: 'front owner.png', kind: 'front', side: 'both'}, {filename: 'side.png', kind: 'side', side: 'left'}]);
      for (const photo of manifest.photos) {assert.deepEqual(Object.keys(photo).sort(), ['field', 'filename', 'kind', 'side']); assert.deepEqual(sends[1].parts[photo.field].bytes, server.state.png);}
      assert.deepEqual(Object.keys(sends[1].parts).sort(), ['manifest', ...manifest.photos.map(photo => photo.field)].sort());
      server.state.startDelay = 0;
    });

    await t.test('run shows spend and ticking elapsed time; confirms stop and routes ended runs', async () => {
      await page.locator('#run-spend').waitFor();
      assert.match(await page.locator('#run-spend').innerText(), /1\.25.*25\.50/);
      assert.equal(await page.locator('#run-money-left').innerText(), 'Money left $24.25');
      assert.equal(await page.locator('#run-estimate').count(), 0);
      const elapsed = await page.locator('#run-elapsed').innerText();
      await page.waitForFunction(text => document.querySelector('#run-elapsed')?.textContent !== text, elapsed);
      await screenshot(page, 'run');
      let question;
      page.once('dialog', dialog => {question = dialog.message(); return dialog.dismiss();});
      await page.locator('#stop-run').click();
      assert.equal(question, 'Stop after the current step? The request already running finishes.');
      assert.equal(requests(server, 'POST', '/api/runs/run_demo/stop').length, 0);
      page.once('dialog', dialog => dialog.accept());
      await page.locator('#stop-run').click();
      await page.getByText(/stop requested/i).first().waitFor();
      assert.deepEqual(lastRequest(server, 'POST', '/api/runs/run_demo/stop').body, {});
      server.state.run.phase = 'ended'; server.state.run.delivery_key = 'delivery_demo';
      await page.waitForURL('**/results/delivery_demo');
      await page.locator('#viewer canvas').waitFor();
      assert.match(await page.locator('body').innerText(), /not finished/i);
      assert.match(await page.locator('body').innerText(), /25\.65/);
      await screenshot(page, 'results');
      const popupPromise = context.waitForEvent('page');
      await page.getByRole('button', {name: 'View in AR', exact: true}).click();
      const popup = await popupPromise; await popup.waitForURL('**/__test/ar'); await popup.close();
      assert.deepEqual(lastRequest(server, 'POST', '/api/deliveries/delivery_demo/ar').body, {});
      await page.getByRole('button', {name: 'Edit', exact: true}).click();
      await page.waitForURL('**/edit/draft_demo');
      assert.deepEqual(lastRequest(server, 'POST', '/api/edit-drafts').body, {delivery_key: 'delivery_demo'});
      server.state.run.phase = 'running'; server.state.run.delivery_key = null;
    });

    await t.test('viewer preserves source, frozen camera, exact node/primitive identity and barycentric anchors', async () => {
      await fixtureViewer(page, server.origin, server.state.source);
      const result = await page.evaluate(async () => {
        const THREE = await import('three'), viewer = window.fixtureViewer;
        const transform = {position: viewer.root.position.toArray(), scale: viewer.root.scale.toArray(), quaternion: viewer.root.quaternion.toArray()};
        window.fixtureCapture = await viewer.capture(); const capture = window.fixtureCapture;
        const frozen = JSON.stringify(capture.camera);
        const camera = new THREE.PerspectiveCamera(); camera.projectionMatrix.fromArray(capture.camera.projection_matrix); camera.matrixWorld.fromArray(capture.camera.matrix_world); camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
        window.projectFixture = ([x, y, z]) => {const p = new THREE.Vector3(x, y, z).project(camera); return [(p.x + 1) * capture.width / 2, (1 - p.y) * capture.height / 2];};
        const xyz = [[-0.055, 0.021, 0], [0.04, 0.02, 0.01], [0.014, -0.028, 0], [0.05, -0.027, 0]];
        const anchors = xyz.map(p => capture.anchorAt(window.projectFixture(p)));
        const before = capture.anchorAt(window.projectFixture(xyz[0]));
        viewer.camera.position.set(0.2, 0.2, 0.1); viewer.camera.lookAt(0, 0, 0); viewer.controls.update();
        const after = capture.anchorAt(window.projectFixture(xyz[0]));
        return {transform, width: capture.width, height: capture.height, camera: capture.camera, frozen: JSON.stringify(capture.camera) === frozen, anchors, before, after, background: capture.anchorAt([2, 2]), blobType: capture.blob.type, xyz};
      });
      assert.deepEqual(result.transform, {position: [0, 0, 0], scale: [1, 1, 1], quaternion: [0, 0, 0, 1]});
      assert.equal(Math.max(result.width, result.height), 1600); assert.equal(result.width / result.height, 800 / 500);
      assert.equal(result.blobType, 'image/png'); assert.equal(result.frozen, true); assert.deepEqual(result.before, result.after);
      assert.deepEqual(result.background, {kind: 'background'});
      const names = ['Frame.Panel[0]', 'Lens Left.001', 'Frame.Panel[0]', 'Frame.Panel[0]'];
      const ids = [[0, 0, 0], [1, 1, 0], [0, 0, 1], [3, 3, 0]];
      result.anchors.forEach((anchor, i) => {
        assert.equal(anchor.kind, 'surface'); assert.equal(anchor.node_name, names[i]);
        assert.deepEqual([anchor.node_index, anchor.mesh_index, anchor.primitive_index], ids[i]);
        const quad = server.state.fixture.quads.find(q => q.node_index === anchor.node_index && q.primitive_index === anchor.primitive_index);
        const triangle = quad.indices.slice(anchor.triangle_index * 3, anchor.triangle_index * 3 + 3);
        for (let axis = 0; axis < 3; axis++) {
          const reconstructed = triangle.reduce((sum, vertex, j) => sum + anchor.barycentric[j] * quad.positions[vertex * 3 + axis] * 1000, 0);
          near(anchor.position_mm[axis], reconstructed, 1e-5); near(anchor.position_mm[axis], result.xyz[i][axis] * 1000, 1e-4);
        }
        near(anchor.barycentric.reduce((a, b) => a + b), 1); assert.deepEqual(anchor.normal, [0, 0, 1]);
      });
      assert.equal(result.anchors[1].is_lens, true);
      assert.equal(result.anchors[1].behind.node_index, 2); assert.equal(result.anchors[1].behind.node_name, 'Frame behind/lens');
      assert.equal(result.anchors[1].behind.is_lens, false);
      assert.equal(result.anchors[0].is_lens, false); assert.equal(result.anchors[0].behind, null);
      for (const key of ['position_m', 'quaternion_xyzw', 'target_m', 'matrix_world', 'projection_matrix']) assert.ok(result.camera[key].every(Number.isFinite));
    });

    await t.test('footprints equal independently enumerated native-pixel masks and known quad triangles', async () => {
      const footprints = await page.evaluate(async () => {
        const capture = window.fixtureCapture, project = window.projectFixture;
        const make = (kind, positions, extra = {}) => ({kind, label: 'A', radius_px: 9, stroke_px: 4, vertices: positions.map(p => ({xy_px: project(p)})), ...extra});
        const marks = [make('point', [[-0.055, 0.021, 0]]), make('polyline', [[-0.06, 0.02, 0], [-0.02, -0.02, 0]]), make('closed_shape', [[-0.065, -0.025, 0], [-0.015, -0.025, 0], [-0.015, 0.025, 0], [-0.065, 0.025, 0]])];
        return {marks, results: await Promise.all(marks.map(mark => capture.footprint(mark))), width: capture.width, height: capture.height};
      });
      function segmentDistance(x, y, a, b) {
        const dx = b[0] - a[0], dy = b[1] - a[1], t = Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)));
        return Math.hypot(x - a[0] - dx * t, y - a[1] - dy * t);
      }
      for (let m = 0; m < footprints.marks.length; m++) {
        const mark = footprints.marks[m], vertices = mark.vertices.map(v => v.xy_px);
        let pixels = 0;
        for (let y = 0; y < footprints.height; y++) for (let x = 0; x < footprints.width; x++) {
          const px = x + 0.5, py = y + 0.5;
          let covered = false;
          if (mark.kind === 'point') covered = Math.hypot(px - vertices[0][0], py - vertices[0][1]) <= mark.radius_px;
          else if (mark.kind === 'polyline') covered = segmentDistance(px, py, vertices[0], vertices[1]) <= mark.stroke_px / 2;
          else {
            for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
              const a = vertices[i], b = vertices[j];
              if ((a[1] > py) !== (b[1] > py) && px < (b[0] - a[0]) * (py - a[1]) / (b[1] - a[1]) + a[0]) covered = !covered;
            }
          }
          if (covered) pixels++;
        }
        assert.equal(footprints.results[m].pixels, pixels, `${mark.kind} pixel coverage`);
        assert.deepEqual(footprints.results[m].faces, [{node_index: 0, mesh_index: 0, primitive_index: 0, triangles: m === 0 ? [[1, 1]] : [[0, 1]]}], `${mark.kind} primitive-local visible triangles`);
      }
      await page.evaluate(() => {window.fixtureCapture.dispose(); window.fixtureViewer.dispose();});
    });

    await t.test('ID-buffer footprints omit transparent alpha-mask texels and retain opaque texels', async () => {
      await fixtureViewer(page, server.origin, {delivery_key: 'delivery_alpha', glb_sha256: sha256(server.state.alphaFixture.bytes)}, '/__test/alpha.glb');
      const result = await page.evaluate(async () => {
        const THREE = await import('three'), capture = await window.fixtureViewer.capture();
        const camera = new THREE.PerspectiveCamera(); camera.projectionMatrix.fromArray(capture.camera.projection_matrix); camera.matrixWorld.fromArray(capture.camera.matrix_world); camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
        const at = x => {const p = new THREE.Vector3(x, -0.03, 0).project(camera); return [(p.x + 1) * capture.width / 2, (1 - p.y) * capture.height / 2];};
        const mark = x => ({label: 'A', kind: 'point', radius_px: 5, stroke_px: 4, vertices: [{xy_px: at(x)}]});
        const result = {transparent: capture.footprint(mark(0.045)), opaque: capture.footprint(mark(0.055))};
        capture.dispose(); window.fixtureViewer.dispose(); return result;
      });
      assert.deepEqual(result.transparent, {pixels: 0, faces: []}); assert.ok(result.opaque.pixels > 0);
      assert.deepEqual(result.opaque.faces, [{node_index: 3, mesh_index: 3, primitive_index: 0, triangles: [[0, 0]]}]);
    });

    await t.test('annotation point/line/closed shape, labels, undo/delete, high-DPI mapping and PNG multipart', async () => {
      server.state.draft.items = []; server.state.draft.labels = [];
      await page.goto(`${server.origin}/edit/draft_demo`);
      await page.locator('#viewer canvas').waitFor(); await screenshot(page, 'edit');
      await page.locator('#capture-annotate').click();
      const canvas = page.getByTestId('annotation-canvas'); await canvas.waitFor();
      assert.equal(await page.getByTestId('annotation-dialog').evaluate(dialog => dialog.scrollWidth <= dialog.clientWidth + 1), true, 'annotation dialog fits laptop width without horizontal overflow');
      const click = async (x, y) => {const box = await canvas.boundingBox(); await page.mouse.click(box.x + box.width * x, box.y + box.height * y);};
      await page.getByRole('button', {name: 'Point', exact: true}).click(); await click(0.25, 0.35);
      await page.getByLabel('Label A', {exact: true}).waitFor();
      await page.getByRole('button', {name: 'Line', exact: true}).click(); await click(0.3, 0.4); await click(0.4, 0.45);
      await page.getByRole('button', {name: 'Finish line', exact: true}).click();
      await page.getByLabel('Label B', {exact: true}).waitFor();
      await click(0.2, 0.6); await click(0.3, 0.65); await page.keyboard.press('Enter');
      await page.getByLabel('Label C', {exact: true}).waitFor(); await page.getByRole('button', {name: 'Undo', exact: true}).click();
      await click(0.2, 0.6); {const box = await canvas.boundingBox(); await page.mouse.dblclick(box.x + box.width * 0.3, box.y + box.height * 0.65);}
      await page.getByLabel('Label C', {exact: true}).waitFor(); await page.getByRole('button', {name: 'Undo', exact: true}).click();
      await page.getByRole('button', {name: 'Shape', exact: true}).click(); await click(0.55, 0.3); await click(0.7, 0.3); await click(0.65, 0.5);
      assert.equal(await page.getByLabel('Label C', {exact: true}).count(), 0, 'three corners do not close a shape');
      await page.keyboard.press('Enter'); assert.equal(await page.getByLabel('Label C', {exact: true}).count(), 0, 'Enter cannot close a shape');
      await click(0.55, 0.3); await page.getByLabel('Label C', {exact: true}).waitFor();
      await page.getByRole('button', {name: 'Point', exact: true}).click(); await click(0.8, 0.8);
      await page.getByLabel('Label D', {exact: true}).waitFor();
      await page.getByRole('button', {name: 'Undo', exact: true}).click(); assert.equal(await page.getByLabel('Label D', {exact: true}).count(), 0);
      await click(0.8, 0.75); await page.getByLabel('Label D', {exact: true}).waitFor();
      await page.getByRole('button', {name: 'Delete selected', exact: true}).click(); assert.equal(await page.getByLabel('Label D', {exact: true}).count(), 0);
      await page.getByLabel('Label C', {exact: true}).fill('A'); await page.getByLabel('Label C', {exact: true}).blur();
      assert.match(await page.getByTestId('annotation-dialog').innerText(), /already|duplicate|unique/i);
      const labels = page.getByTestId('mark-list').locator('input');
      await labels.nth(2).fill('R7'); await labels.nth(2).blur();
      await page.getByRole('button', {name: 'Done annotating', exact: true}).click();
      const words = page.getByLabel('What should Astra change?', {exact: true}); await words.waitFor();
      await words.fill('A and Z need changes.');
      assert.match(await page.getByTestId('annotation-warning').innerText(), /Z/);
      assert.match(await page.getByTestId('annotation-warning').innerText(), /B/);
      await words.fill('  Move A, B and R7 toward the bridge.\nKeep these exact words.  ');
      await screenshot(page, 'annotation');
      await page.getByRole('button', {name: 'Done', exact: true}).click();
      await page.getByText('Any more edits?', {exact: true}).waitFor();
      const post = lastRequest(server, 'POST', '/api/edit-drafts/draft_demo/items');
      assert.deepEqual(Object.keys(post.parts).sort(), ['annotated', 'capture', 'viewer']);
      const capture = parseCapture(post.parts.capture);
      const bundle = path.join(DATA, 'capture-bundle'); await fs.mkdir(bundle, {recursive: true});
      await fs.writeFile(path.join(bundle, 'capture.json'), JSON.stringify(capture, null, 2));
      assert.equal(capture.schema, 'g3d.owner_capture/1'); assert.deepEqual(capture.source, server.state.source);
      assert.deepEqual(capture.marks.map(mark => mark.kind), ['point', 'polyline', 'closed_shape']);
      assert.deepEqual(capture.marks.map(mark => mark.label), ['A', 'B', 'R7']);
      assert.equal(capture.marks[2].vertices.length, 3, 'no duplicate closing vertex');
      near(capture.marks[0].vertices[0].xy_px[0] / capture.image.width_px, 0.25, 0.003);
      near(capture.marks[0].vertices[0].xy_px[1] / capture.image.height_px, 0.35, 0.003);
      assert.equal(capture.request_text, '  Move A, B and R7 toward the bridge.\nKeep these exact words.  ');
      for (const name of ['viewer', 'annotated']) {
        const png = post.parts[name].bytes; assert.equal(post.parts[name].type, 'image/png');
        assert.equal(png.readUInt32BE(16), capture.image.width_px); assert.equal(png.readUInt32BE(20), capture.image.height_px);
        await fs.writeFile(path.join(DATA, `posted-${name}.png`), png);
        await fs.writeFile(path.join(bundle, `${name}.png`), png);
      }
      assert.notEqual(sha256(post.parts.viewer.bytes), sha256(post.parts.annotated.bytes));
      const painted = await page.evaluate(async ({bytes, mark}) => {
        const image = await createImageBitmap(new Blob([new Uint8Array(bytes)], {type: 'image/png'}));
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
        const [x, y] = mark.vertices[0].xy_px;
        const center = [...context.getImageData(Math.round(x), Math.round(y), 1, 1).data];
        let white = 0, red = 0;
        const data = context.getImageData(Math.floor(x + 14), Math.floor(y - 38), 30, 30).data;
        for (let i = 0; i < data.length; i += 4) {if (data[i] > 245 && data[i + 1] > 245 && data[i + 2] > 245) white++; if (data[i] > 150 && data[i + 1] < 80 && data[i + 2] < 100) red++;}
        image.close(); return {center, white, red};
      }, {bytes: [...post.parts.annotated.bytes], mark: capture.marks[0]});
      assert.ok(painted.center[0] > 180 && painted.center[1] < 60 && painted.center[2] < 80, 'native PNG contains the filled red point');
      assert.ok(painted.white > 100 && painted.red > 10, 'native PNG contains a white label box and red label ink');
      await screenshot(page, 'edit-with-item');
    });

    await t.test('draft-wide label defaults skip I/O; clear, cancel, edit words, delete and paid-send contract', async () => {
      server.state.draft.labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'J', 'K', 'L', 'M', 'N'];
      await page.reload(); await page.locator('#viewer canvas').waitFor();
      await page.locator('#capture-annotate').click();
      const canvas = page.getByTestId('annotation-canvas'); await canvas.waitFor();
      await page.getByRole('button', {name: 'Point', exact: true}).click(); await canvas.click({position: {x: 80, y: 80}});
      await page.getByLabel('Label P', {exact: true}).waitFor();
      await page.getByRole('button', {name: 'Clear', exact: true}).click(); assert.equal(await page.getByTestId('mark-list').locator('input').count(), 0);
      await page.getByRole('button', {name: 'Cancel', exact: true}).click();
      const words = page.getByLabel('Edit words', {exact: true}); await words.fill('  Revised A, B and R7.  ');
      await page.getByRole('button', {name: 'Save words', exact: true}).click();
      await page.waitForFunction(() => document.querySelector('#words-I1')?.disabled === false);
      assert.deepEqual(lastRequest(server, 'PATCH', '/api/edit-drafts/draft_demo/items/I1').body, {request_text: '  Revised A, B and R7.  '});
      server.state.draft.items.push({...server.state.draft.items[0], item_id: 'I2', capture_id: 'C2', labels: ['S'], request_text: 'Remove this S item.'});
      await page.reload();
      const pendingWords = '  Unsaved A and B while deleting another item.  ';
      await page.locator('[data-item-id="I1"]').getByLabel('Edit words', {exact: true}).fill(pendingWords);
      await page.locator('[data-item-id="I2"]').getByRole('button', {name: 'Delete item', exact: true}).click();
      await page.locator('[data-item-id="I2"]').waitFor({state: 'detached'});
      assert.equal(await page.getByLabel('Edit words', {exact: true}).inputValue(), pendingWords, 'deleting another item does not discard unsaved words');
      assert.equal(lastRequest(server, 'DELETE', '/api/edit-drafts/draft_demo/items/I2').body, null);
      await page.getByRole('button', {name: 'Save words', exact: true}).click();
      await page.waitForFunction(() => document.querySelector('#words-I1')?.disabled === false);
      await page.getByRole('button', {name: 'No, send to Astra', exact: true}).click();
      const cutoff = page.locator('#edit-cutoff'); assert.equal(await cutoff.inputValue(), '');
      await cutoff.fill('7.25'); await page.getByRole('button', {name: 'Send to Astra (paid)', exact: true}).click();
      await page.waitForURL('**/run/run_demo');
      const sent = lastRequest(server, 'POST', '/api/edit-drafts/draft_demo/send').body;
      assert.deepEqual(Object.keys(sent).sort(), ['cutoff_usd', 'idempotency_key']); assert.equal(sent.cutoff_usd, '7.25'); assert.ok(sent.idempotency_key);
      server.state.draft.labels = ['A', 'B', 'R7'];
    });

    await t.test('understanding choices and fallback post exact DTOs; task opens fresh draft; jobs list is newest first', async () => {
      await page.goto(`${server.origin}/understand/edit_demo`); await page.locator('#apply-picks').waitFor();
      assert.equal(await page.locator('#apply-picks').isDisabled(), true);
      assert.match(await page.locator('body').innerText(), /Move the selected frame point inward/);
      assert.match(await page.locator('body').innerText(), /The requested surface needs a full edit/);
      await screenshot(page, 'understand');
      await page.getByRole('button', {name: 'Small move', exact: false}).click();
      const second = page.locator('[data-item-id="I2"]');
      await second.getByRole('button', {name: 'None of these', exact: true}).click();
      await second.locator('textarea').fill('  Keep B as it is.  ');
      assert.equal(await page.locator('#apply-picks').isEnabled(), true);
      await page.locator('#apply-picks').click(); await page.waitForURL('**/task/task_demo');
      const apply = lastRequest(server, 'POST', '/api/edits/edit_demo/apply').body;
      assert.deepEqual(Object.keys(apply).sort(), ['choices', 'idempotency_key', 'skipped', 'understanding_sha256']);
      assert.equal(apply.understanding_sha256, 'e'.repeat(64));
      assert.deepEqual(apply.choices, [{item_id: 'I1', option_id: 'small'}]);
      assert.deepEqual(apply.skipped, [{item_id: 'I2', note: '  Keep B as it is.  '}, {item_id: 'I3', note: ''}, {item_id: 'I4', note: ''}, {item_id: 'I5', note: ''}]); assert.ok(apply.idempotency_key);
      await page.getByText(/Applying chosen options/).waitFor(); await screenshot(page, 'task');
      server.state.task.phase = 'done'; server.state.task.result = {delivery_key: 'delivery_after'};
      server.state.delivery.origin = 'applied_picks'; server.state.delivery.applied = [{item_id: 'I1', option_id: 'small', amount_text: '1 mm'}]; server.state.delivery.notes = [{item_id: 'I2', note: '  Keep B as it is.  '}];
      await page.waitForURL('**/edit/draft_after'); await page.locator('#viewer canvas').waitFor();
      assert.match(await page.locator('body').innerText(), /Applied/); assert.match(await page.locator('body').innerText(), /Keep B as it is/);
      assert.deepEqual(lastRequest(server, 'POST', '/api/edit-drafts').body, {delivery_key: 'delivery_after'});
      await page.goto(`${server.origin}/understand/edit_demo`);
      const fallback = page.locator('[data-item-id="I3"]');
      for (const id of ['I4', 'I5']) assert.equal(await page.locator(`[data-item-id="${id}"]`).getByRole('button', {name: 'Send this item to a full paid edit', exact: true}).count(), 0);
      const input = fallback.locator('input[name="cutoff_usd"]'); assert.equal(await input.inputValue(), ''); await input.fill('12.75');
      await fallback.getByRole('button', {name: 'Send this item to a full paid edit', exact: true}).click(); await page.waitForURL('**/run/run_demo');
      const full = lastRequest(server, 'POST', '/api/edits/edit_demo/items/I3/full-edit').body;
      assert.deepEqual(Object.keys(full).sort(), ['cutoff_usd', 'idempotency_key']); assert.equal(full.cutoff_usd, '12.75'); assert.ok(full.idempotency_key);
      await page.goto(server.origin); await page.getByText('Synthetic glasses', {exact: true}).waitFor(); await screenshot(page, 'index');
      const body = await page.locator('body').innerText(); assert.ok(body.indexOf('Synthetic glasses') < body.indexOf('Older glasses'));
      for (const href of ['/results/delivery_demo', '/run/run_demo', '/edit/draft_demo', '/understand/edit_demo']) assert.ok(await page.locator(`a[href="${href}"]`).count(), href);
    });

    await t.test('failed run/task retain status and money with plain errors; ended understanding redirects', async () => {
      const savedRun = structuredClone(server.state.run), savedTask = structuredClone(server.state.task);
      server.state.run.phase = 'failed'; server.state.run.error = {code: 'fixture_failed', message: 'The offline worker stopped before delivery.'};
      await page.goto(`${server.origin}/run/run_demo`); await page.getByText('The offline worker stopped before delivery.', {exact: true}).waitFor();
      assert.match(await page.locator('#run-spend').innerText(), /1\.25.*25\.50/); assert.match(await page.locator('#run-state').innerText(), /failed/); assert.equal(await page.locator('#stop-run').isDisabled(), true);
      server.state.task.phase = 'failed'; server.state.task.error = {code: 'fixture_failed', message: 'The chosen options could not be applied.'};
      await page.goto(`${server.origin}/task/task_demo`); await page.getByText('The chosen options could not be applied.', {exact: true}).waitFor(); assert.equal(await page.locator('#task-state').innerText(), 'failed');
      server.state.run = {...savedRun, phase: 'ended', kind: 'understand', edit_key: 'edit_demo'};
      await page.goto(`${server.origin}/run/run_demo`); await page.waitForURL('**/understand/edit_demo');
      server.state.run = savedRun; server.state.task = savedTask;
    });

    await t.test('all seven pages display exact two-decimal offline banners and zero-cost apply labels', async () => {
      const savedRun = structuredClone(server.state.run), savedTask = structuredClone(server.state.task);
      try {
        server.state.run.phase = 'running'; server.state.run.error = null;
        server.state.task.phase = 'running'; server.state.task.error = null;
        for (const route of ['/', '/new', '/run/run_demo', '/results/delivery_demo', '/edit/draft_demo', '/understand/edit_demo', '/task/task_demo']) {
          await page.goto(`${server.origin}${route}`);
          const banner = page.locator('#offline-banner'); await banner.waitFor({state: 'visible'});
          assert.equal(await banner.innerText(), 'OFFLINE TEST MODE: scripted Astra, $0.00', route);
          if (route.startsWith('/understand/')) {
            await page.locator('#apply-picks').waitFor();
            assert.equal(await page.locator('#apply-picks').innerText(), 'Apply my picks ($0.00)');
          }
          if (route.startsWith('/task/')) {
            await page.getByText('Applying your picks locally ($0.00).', {exact: true}).waitFor();
          }
        }
      } finally {server.state.run = savedRun; server.state.task = savedTask;}
    });

    await t.test('dynamic money displays round half up to two decimals while paid requests retain exact cutoff strings', async () => {
      await page.goto(`${server.origin}/__test/viewer`);
      const cases = [['0', '$0.00'], ['20', '$20.00'], ['1.005', '$1.01'], ['2.675', '$2.68'], ['9.995', '$10.00'], ['9.9949', '$9.99'], ['0.004999999999999999', '$0.00'], ['0.005', '$0.01'], ['12345678901234567890.995', '$12345678901234567891.00']];
      const formatted = await page.evaluate(async values => {const {money} = await import('/static/money.js'); return values.map(money);}, cases.map(([input]) => input));
      assert.deepEqual(formatted, cases.map(([, expected]) => expected));
      const missing = await page.evaluate(async () => {
        const {money, cutoffBalance} = await import('/static/money.js');
        return {amounts: [undefined, null, '', 'invalid', '-1', 'NaN'].map(money), balances: [cutoffBalance('bad', '1'), cutoffBalance('20', null), cutoffBalance('20', ''), cutoffBalance('Infinity', '1')]};
      });
      assert.deepEqual(missing.amounts, Array(6).fill('—')); assert.deepEqual(missing.balances, Array(4).fill(null));
      const savedRun = structuredClone(server.state.run), savedDelivery = structuredClone(server.state.delivery), savedUnderstanding = structuredClone(server.state.understanding);
      try {
        Object.assign(server.state.run, {settled_usd: '18.515', cutoff_usd: '20', estimate_usd: '1.005', phase: 'running', error: null});
        await page.goto(`${server.origin}/run/run_demo`); await page.locator('#run-spend').waitFor();
        assert.equal(await page.locator('#run-spend').innerText(), '$18.52 of $20.00');
        assert.equal(await page.locator('#run-estimate').count(), 0, 'estimates are not UI spending limits');
        assert.equal(await page.locator('#run-money-left').innerText(), 'Money left $1.49', 'subtract exact amounts before rounding displayed cents');
        assert.doesNotMatch(await page.locator('#run-spend').getAttribute('title'), /18\.515/);
        server.state.delivery.run_settled_usd = '25.655';
        await page.goto(`${server.origin}/results/delivery_demo`); await page.locator('#viewer canvas').waitFor();
        assert.match(await page.locator('body').innerText(), /Settled spend: \$25\.66/); assert.doesNotMatch(await page.locator('body').innerText(), /25\.655/);
        server.state.understanding.run_settled_usd = '2.345';
        await page.goto(`${server.origin}/understand/edit_demo`); await page.locator('#apply-picks').waitFor();
        assert.match(await page.locator('body').innerText(), /Understanding settled spend: \$2\.35/);
        await page.locator('#fallback-cutoff-I3').fill('12.7550');
        await page.locator('[data-item-id="I3"]').getByRole('button', {name: 'Send this item to a full paid edit', exact: true}).click(); await page.waitForURL('**/run/run_demo');
        assert.equal(lastRequest(server, 'POST', '/api/edits/edit_demo/items/I3/full-edit').body.cutoff_usd, '12.7550');
        await page.goto(`${server.origin}/edit/draft_demo`); await page.getByRole('button', {name: 'No, send to Astra', exact: true}).click();
        await page.locator('#edit-cutoff').fill('7.2550'); await page.getByRole('button', {name: 'Send to Astra (paid)', exact: true}).click(); await page.waitForURL('**/run/run_demo');
        assert.equal(lastRequest(server, 'POST', '/api/edit-drafts/draft_demo/send').body.cutoff_usd, '7.2550');
        assert.equal(server.state.run.settled_usd, '18.515'); assert.equal(server.state.run.estimate_usd, '1.005');
      } finally {server.state.run = savedRun; server.state.delivery = savedDelivery; server.state.understanding = savedUnderstanding;}
    });

    await t.test('all seven pages omit extra money layers even when DTOs contain their values', async () => {
      const savedRun = structuredClone(server.state.run), savedTask = structuredClone(server.state.task);
      const amounts = ['87654.325', '76543.215', '65432.105', '54321.995', '43210.985', '32109.975'];
      try {
        server.state.run.phase = 'running'; server.state.run.error = null;
        server.state.task.phase = 'running'; server.state.task.error = null;
        server.state.moneyDiagnostics = {
          estimate_usd: amounts[0], held_usd: amounts[1], unknown_usd: amounts[2], reserved_usd: amounts[3],
          projected_usd: amounts[4], finish_soon_usd: amounts[5], finish_soon: 'Finish soon: projected spend is $32109.98.',
        };
        const inspected = [];
        for (const route of ['/', '/new', '/run/run_demo', '/results/delivery_demo', '/edit/draft_demo', '/understand/edit_demo', '/task/task_demo']) {
          await page.goto(`${server.origin}${route}`); await page.locator('#offline-banner').waitFor({state: 'visible'});
          const ready = {'/': '[data-job-id="job_demo"]', '/new': '#new-run-form', '/run/run_demo': '#run-spend', '/results/delivery_demo': '#view-ar', '/edit/draft_demo': '#capture-annotate', '/understand/edit_demo': '#apply-picks', '/task/task_demo': '#task-state'};
          await page.locator(ready[route]).waitFor();
          const content = await page.evaluate(() => [document.body.innerText, ...[...document.querySelectorAll('[title],[aria-label]')].flatMap(element => [element.getAttribute('title') || '', element.getAttribute('aria-label') || ''])].join('\n'));
          assert.doesNotMatch(content, /\b(?:estimate(?:d|s)?|held|reserve(?:d|s)?|unknown|project(?:ed|ion|ions)?|finish\s+soon)\b/i, `${route} has no generated extra-money labels or advice`);
          for (const value of amounts) assert.ok(!content.includes(value.split('.')[0]), `${route} does not display diagnostic money ${value}`);
          assert.equal(await page.locator('#run-estimate').count(), 0, `${route} has no estimate control`);
          inspected.push({route, no_extra_money_text: true, no_extra_money_figures: true});
        }
        await fs.writeFile(path.join(DATA, 'followup2-seven-pages.json'), JSON.stringify(inspected, null, 2));
      } finally {server.state.moneyDiagnostics = null; server.state.run = savedRun; server.state.task = savedTask;}
    });

    await t.test('money left follows exact settled subtraction, clamps at zero and updates when polling crosses the cutoff', async () => {
      const savedRun = structuredClone(server.state.run), evidence = [];
      try {
        Object.assign(server.state.run, {phase: 'running', error: null, cutoff_usd: '20', settled_usd: '19.995'});
        await page.goto(`${server.origin}/run/run_demo`);
        const check = async (name, left, overrun) => {
          await page.waitForFunction(({left, overrun}) => {
            const balance = document.querySelector('#run-money-left'), notice = document.querySelector('#run-cutoff-overrun');
            return balance?.textContent === left && notice && (overrun ? !notice.hidden && notice.textContent === overrun : notice.hidden && !notice.textContent);
          }, {left, overrun});
          assert.equal(page.url(), `${server.origin}/run/run_demo`);
          assert.equal(await page.locator('#run-money-left').innerText(), left);
          assert.equal(await page.locator('#run-estimate').count(), 0);
          const text = await page.locator('body').innerText();
          assert.doesNotMatch(text, /(?:Money left|left)\s*-\$|\$-\d/);
          if (!overrun) assert.doesNotMatch(text, /The last request finished past the cutoff/);
          else assert.equal(await page.locator('#run-cutoff-overrun').innerText(), overrun);
          evidence.push({name, cutoff_usd: server.state.run.cutoff_usd, settled_usd: server.state.run.settled_usd, left, overrun: overrun || null});
        };
        await check('below', 'Money left $0.01', null); await screenshot(page, 'followup2-run-below');
        const pollsBeforeCrossing = requests(server, 'GET', '/api/runs/run_demo').length;
        server.state.run.settled_usd = '20.00000';
        await check('equal', '$0.00 left', null); await screenshot(page, 'followup2-run-equal');
        server.state.run.settled_usd = '20.005';
        await check('above', '$0.00 left', 'The last request finished past the cutoff by $0.01.'); await screenshot(page, 'followup2-run-above');
        assert.ok(requests(server, 'GET', '/api/runs/run_demo').length >= pollsBeforeCrossing + 2, 'the same running page observed both transitions by polling');
        for (const sample of [
          {name: 'subtract-before-rounding', cutoff: '20', settled: '18.515', left: 'Money left $1.49', overrun: null},
          {name: 'large-exact-decimals', cutoff: '12345678901234567890.010', settled: '12345678901234567890.005', left: 'Money left $0.01', overrun: null},
          {name: 'below-by-subcent', cutoff: '20', settled: '19.999999999999999999', left: 'Money left $0.00', overrun: null},
          {name: 'above-by-subcent', cutoff: '20', settled: '20.000000000000000001', left: '$0.00 left', overrun: 'The last request finished past the cutoff by $0.00.'},
          {name: 'overrun-carry-half-up', cutoff: '20', settled: '29.995', left: '$0.00 left', overrun: 'The last request finished past the cutoff by $10.00.'},
        ]) {
          server.state.run.cutoff_usd = sample.cutoff; server.state.run.settled_usd = sample.settled;
          await page.reload(); await check(sample.name, sample.left, sample.overrun);
          assert.equal(server.state.run.cutoff_usd, sample.cutoff); assert.equal(server.state.run.settled_usd, sample.settled);
        }
        await fs.writeFile(path.join(DATA, 'followup2-money-left.json'), JSON.stringify(evidence, null, 2));
      } finally {server.state.run = savedRun;}
    });

    await t.test('copied real delivery captures and records footprint timing without changing source', async () => {
      const candidates = [
        'glasses3d/data/assembly/m1-proof-28a9752d/jobs/synthetic-assembly-proof-939a1e95/deliverable/model.glb',
        'glasses3d/data/jobs/prada-beige-86248315/deliveries/20261017T090817-2b59b9-432271182ef9e4ad96e8b69cff4e10ad1e7e177cd83f63d7a491b4f2322b1db8/model.glb',
      ];
      let source;
      for (const relative of candidates) {try {await fs.access(path.join(ROOT, relative)); source = path.join(ROOT, relative); break;} catch { /* Try the second documented source. */ }}
      assert.ok(source, 'one documented real delivery must be available for the required smoke');
      const bytes = await fs.readFile(source), before = sha256(bytes), copy = path.join(DATA, 'real-delivery-copy.glb');
      await fs.writeFile(copy, bytes); server.state.realBytes = await fs.readFile(copy);
      await fixtureViewer(page, server.origin, {delivery_key: 'delivery_real', glb_sha256: before}, '/__test/model.glb');
      const measurement = await page.evaluate(async () => {
        const start = performance.now(), capture = await window.fixtureViewer.capture(), captured = performance.now();
        const mark = {label: 'A', kind: 'closed_shape', radius_px: 9, stroke_px: 4, vertices: [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]].map(([x, y]) => ({xy_px: [x * capture.width, y * capture.height]}))};
        const footprint = await capture.footprint(mark), completed = performance.now();
        const result = {capture_ms: captured - start, footprint_ms: completed - captured, width: capture.width, height: capture.height, png_bytes: capture.blob.size, footprint};
        capture.dispose(); return result;
      });
      assert.ok(measurement.png_bytes > 1000); assert.ok(measurement.footprint.faces.length > 0); assert.ok(measurement.footprint.pixels > 0);
      assert.equal(sha256(await fs.readFile(source)), before); assert.equal(sha256(await fs.readFile(copy)), before);
      await screenshot(page, 'real-delivery-smoke'); await page.evaluate(() => window.fixtureViewer.dispose());
      await fs.writeFile(path.join(DATA, 'real-delivery-timing.json'), JSON.stringify({source, copy, source_sha256: before, ...measurement}, null, 2));
      t.diagnostic(`Real delivery copy: capture ${measurement.capture_ms.toFixed(1)} ms; footprint ${measurement.footprint_ms.toFixed(1)} ms; ${measurement.footprint.faces.length} primitive groups.`);
    });
    assert.deepEqual(audit.blocked, [], 'no nonlocal network request'); assert.deepEqual(audit.errors, [], 'no browser exceptions'); assert.deepEqual(audit.shaderErrors, [], 'no shader failures');
  } finally {await context.close();}
});
