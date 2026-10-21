/** Filesystem harness regressions; no browser, native worker or network. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {replaceWithRetry, REPLACE_DELAYS_MS} from '../e2e/atomic-replace.mjs';

async function files(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'g3d-atomic-report-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const source = path.join(root, 'browser-report.writing.json');
  const destination = path.join(root, 'browser-report.json');
  await fs.writeFile(source, '{"status":"new"}\n');
  await fs.writeFile(destination, '{"status":"last-good"}\n');
  return {source, destination};
}

for (const code of ['EPERM', 'EACCES', 'EBUSY']) {
  test(`report rename retries ${code} atomically and logs each denial`, async t => {
    const {source, destination} = await files(t);
    const logs = [], sleeps = []; let calls = 0;
    await replaceWithRetry(source, destination, {
      rename: async (from, to) => {
        assert.equal(from, source); assert.equal(to, destination);
        assert.equal(await fs.readFile(destination, 'utf8'), '{"status":"last-good"}\n');
        assert.equal(await fs.readFile(source, 'utf8'), '{"status":"new"}\n');
        if (++calls < 3) throw Object.assign(new Error('transient sharing denial'), {code});
        await fs.rename(from, to);
      },
      sleep: async ms => sleeps.push(ms), log: record => logs.push(record),
    });
    assert.equal(calls, 3);
    assert.deepEqual(sleeps, REPLACE_DELAYS_MS.slice(0, 2));
    assert.deepEqual(logs.map(row => [row.event, row.attempt, row.code, row.retry_ms]),
      [['atomic_replace_retry', 1, code, 10], ['atomic_replace_retry', 2, code, 25]]);
    assert.equal(await fs.readFile(destination, 'utf8'), '{"status":"new"}\n');
    await assert.rejects(fs.access(source), {code: 'ENOENT'});
  });
}

test('report rename exhaustion preserves both complete files and fails with the bound', async t => {
  const {source, destination} = await files(t);
  const logs = [], sleeps = [], original = Object.assign(new Error('still locked'), {code: 'EBUSY'});
  let calls = 0;
  await assert.rejects(replaceWithRetry(source, destination, {
    rename: async () => {calls++; throw original;},
    sleep: async ms => sleeps.push(ms), log: record => logs.push(record),
  }), error => {
    assert.match(error.message, /Atomic replace failed after 9 attempts \(EBUSY\)/);
    assert.equal(error.cause, original); assert.equal(error.code, 'EBUSY'); return true;
  });
  assert.equal(calls, 9); assert.deepEqual(sleeps, REPLACE_DELAYS_MS);
  assert.equal(logs.length, 9); assert.equal(logs.at(-1).event, 'atomic_replace_failed');
  assert.equal(logs.at(-1).attempt, 9); assert.equal(logs.at(-1).retry_ms, undefined);
  assert.equal(await fs.readFile(source, 'utf8'), '{"status":"new"}\n');
  assert.equal(await fs.readFile(destination, 'utf8'), '{"status":"last-good"}\n');
});

test('report rename does not retry unrelated filesystem failures', async t => {
  const {source, destination} = await files(t);
  const original = Object.assign(new Error('source missing'), {code: 'ENOENT'});
  let calls = 0;
  await assert.rejects(replaceWithRetry(source, destination, {
    rename: async () => {calls++; throw original;},
    sleep: async () => assert.fail('unexpected sleep'), log: () => assert.fail('unexpected retry'),
  }), error => error === original);
  assert.equal(calls, 1);
  assert.equal(await fs.readFile(destination, 'utf8'), '{"status":"last-good"}\n');
});
