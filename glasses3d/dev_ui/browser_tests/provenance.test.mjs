import assert from 'node:assert/strict';
import test from 'node:test';
import {startMockServer, launchBrowser, browserContext} from './mock-server.mjs';

test('delivery pages show exact picks and honest full-edit side effects', async t => {
  const server = await startMockServer(); t.after(() => server.close());
  const browser = await launchBrowser(); t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin); t.after(() => context.close());
  const page = await context.newPage();
  server.state.delivery.exactness = {ok: true, mode: 'disjoint_items', single_pick_glb_equal: false, overlaps: []};
  await page.goto(`${server.origin}/results/delivery_demo`);
  await page.locator('.exactness').waitFor();
  assert.match(await page.locator('.exactness').innerText(), /Selected disjoint changes match their previews exactly/);
  server.state.delivery.origin = 'full_edit_item';
  server.state.delivery.side_effects = {item_id: 'I3', status: 'partially_resolved', informational_only: true,
    summary: {changed_objects: ['Frame'], geometry_outside_item_region: true,
      outside_region_objects: ['Frame'], unresolved_objects: ['Temple']},
    objects: [{name: 'Frame', geometry: {changed: true}, materials: {changed: false},
      uvs: {changed: false}, transform: {changed: false}}]};
  server.state.delivery.exactness = null;
  await page.reload();
  await page.locator('.side-effects').waitFor();
  const effects = await page.locator('.side-effects').innerText();
  assert.match(effects, /Changes outside the marked region: Frame/);
  assert.match(effects, /Unresolved correspondence: Temple/);
  await page.getByText('Full side-effect report', {exact: true}).click();
  assert.match(await page.locator('.side-effects pre').innerText(), /"materials"/);
  server.state.delivery.side_effects = {item_id: 'I3', status: 'unavailable', reason: 'Snapshot unavailable', summary: {geometry_outside_item_region: null}};
  await page.reload();
  await page.getByText('Snapshot unavailable', {exact: true}).waitFor();
  assert.match(await page.locator('.side-effects').innerText(), /Changes outside the marked region: unresolved/);
  assert.deepEqual(audit.blocked, []); assert.deepEqual(audit.errors, []);
});

test('full-edit delivery returns to Any more edits with a fresh capture', async t => {
  const server = await startMockServer(); t.after(() => server.close());
  const browser = await launchBrowser(); t.after(() => browser.close());
  const {context, audit} = await browserContext(browser, server.origin); t.after(() => context.close());
  const page = await context.newPage();
  server.state.delivery.origin = 'full_edit_item';
  await page.goto(`${server.origin}/edit/draft_after`);
  await page.locator('#capture-annotate:not([disabled])').waitFor();
  assert.match(await page.locator('#more-edits').innerText(), /Any more edits\?/);
  await page.getByRole('button', {name: 'Yes, capture another', exact: true}).click();
  await page.locator('[data-testid="annotation-dialog"]').waitFor();
  assert.deepEqual(audit.blocked, []); assert.deepEqual(audit.errors, []);
});
