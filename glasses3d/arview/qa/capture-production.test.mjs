/** The exact automation capture flags, on a product-independent generated GLB and the pinned test portrait. */
import assert from 'node:assert/strict';
import {mkdtemp, readFile, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {main} from './capture.mjs';
import {buildGlb} from '../tests/glb-fab.ts';

test('production capture: honest complete images, portrait, checker and wearer-named worn sides', async t => {
  const work = await mkdtemp(path.join(tmpdir(), 'arview-production-'));
  t.after(() => rm(work, {recursive: true, force: true}));
  const glb = path.join(work, 'model.glb'), out = path.join(work, 'capture');
  await writeFile(glb, Buffer.from(buildGlb().glb));
  const code = await main([glb, '--out', out, '--poses', 'default', '--backgrounds', 'skin',
    '--fixed-scale', '--worn-sides', '--lens-evidence', '--lensenv', '1']);
  const report = JSON.parse(await readFile(path.join(out, 'report.json'), 'utf8'));
  assert.equal(code, 0, JSON.stringify({errors: report.errors, failure: report.infrastructure_failure}));
  assert.equal(report.images.length, 35); assert.equal(report.images.length, report.planned_images);
  assert.equal(report.chromium_process.gone, true);
  assert.deepEqual(report.errors, []);
  assert.ok(report.images.some(image => image.file === 'front__checker__beauty.png'));
  assert.ok(report.images.some(image => image.kind === 'portrait' && image.variant === 'condition-a' && image.region === 'eyes'));
  const worn = report.images.filter(image => image.kind === 'worn_crop');
  assert.equal(worn.length, 2);
  assert.deepEqual(worn.map(image => [image.pose, image.side]), [['yaw_p50', 'right'], ['yaw_m50', 'left']]);
});
