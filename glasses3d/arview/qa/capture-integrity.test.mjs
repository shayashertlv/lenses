import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {verifyCaptureImages} from './capture.mjs';
import {captureOutcome, EXIT} from './capture/report.ts';

const input = errors => ({infrastructureFailure: null, modelReport: {status: 'rendered'},
  spec: {available: true, report: {ok: true}}, warnings: [], errors, images: [{}], plannedImages: 1});

test('every capture integrity error exits nonzero with the error intact', () => {
  for (const error of ['image a.png is listed but was never written', 'image a.png: hash mismatch',
    'Chromium pid 42 is still running after the kill', 'the capture page navigated 2 times (a mid-run reload)',
    'page error: uncaught exception', 'the live probe changed ar/', 'external resource could not be served: verdict void']) {
    assert.deepEqual(captureOutcome(input([error])), {status: 'infrastructure_failed', exitCode: EXIT.infrastructure}, error);
  }
});

test('capture verifies actual files after writing: changed, missing and unrecorded images fail', async t => {
  const dir = await mkdtemp(path.join(tmpdir(), 'arview-integrity-'));
  t.after(() => rm(dir, {recursive: true, force: true}));
  const bytes = Buffer.from('original bytes'), sha256 = createHash('sha256').update(bytes).digest('hex');
  const images = [{file: 'capture.png', sha256}], written = new Map([['capture.png', {sha256}]]);
  await writeFile(path.join(dir, 'capture.png'), bytes);
  assert.deepEqual(await verifyCaptureImages(dir, images, written), []);
  await writeFile(path.join(dir, 'capture.png'), 'changed bytes');
  let errors = await verifyCaptureImages(dir, images, written);
  assert.match(errors[0], /page reported sha256/); assert.equal(captureOutcome(input(errors)).exitCode, 1);
  await rm(path.join(dir, 'capture.png'));
  errors = await verifyCaptureImages(dir, images, written);
  assert.match(errors[0], /missing or unreadable/); assert.equal(captureOutcome(input(errors)).exitCode, 1);
  assert.match((await verifyCaptureImages(dir, images, new Map()))[0], /never written/);
});
