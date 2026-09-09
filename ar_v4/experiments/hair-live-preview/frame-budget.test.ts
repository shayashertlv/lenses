import test from 'node:test';
import assert from 'node:assert/strict';
import {FrameBudget} from './frame-budget.ts';

test('a slow optional mask never blocks a newer frame or becomes its mask', async () => {
  const budget = new FrameBudget<string>();
  let release!: (value: string) => void;
  assert.equal(budget.start(1, () => new Promise(resolve => { release = resolve; })), true);
  await Promise.resolve();
  assert.equal(await budget.take(1, 2), null);
  assert.equal(budget.start(2, async () => 'wrong'), false);
  assert.equal(await budget.take(2, 2), null);
  release('old'); await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(await budget.take(1, 2), null);
  assert.equal(budget.start(3, async () => 'current'), true);
  assert.equal(await budget.take(3, 50), 'current');
  assert.equal(await budget.take(3, 2), null);
});

test('closing ownership while a frame waits prevents late publication', async () => {
  const budget = new FrameBudget<string>();
  let release!: (value: string) => void;
  budget.start(9, () => new Promise(resolve => { release = resolve; }));
  const result = budget.take(9, 50); await Promise.resolve();
  budget.close(); release('old-session');
  assert.equal(await result, null);
  assert.equal(budget.start(10, async () => 'late'), false);
});

test('failed optional work releases the slot without leaking a rejection', async () => {
  const budget = new FrameBudget<string>();
  budget.start(1, async () => { throw new Error('worker unavailable'); });
  assert.equal(await budget.take(1, 50), null);
  assert.equal(budget.busy, false);
  budget.start(2, async () => 'valid');
  assert.equal(await budget.take(2, 50), 'valid');
});
