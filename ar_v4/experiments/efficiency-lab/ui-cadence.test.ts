import assert from 'node:assert/strict';
import {test} from 'node:test';
import {UiSummaryCadence} from './ui-cadence.ts';

test('statistics throttle refreshes the first completion and every 500 ms without dropping observations', () => {
  const cadence = new UiSummaryCadence();
  assert.equal(cadence.observe(100, 'session/ui/hair/tracking', true).refresh, true);
  for (const at of [101, 150, 599.999]) assert.equal(cadence.observe(at, 'session/ui/hair/tracking', true).refresh, false);
  assert.deepEqual(cadence.observe(600, 'session/ui/hair/tracking', true), {
    refresh: true, transition: false, requested: true, intervalMs: 500, requests: 5, refreshes: 2, skipped: 3,
  });
});

test('pipeline, variant, tracking and session transitions refresh immediately inside the interval', () => {
  const cadence = new UiSummaryCadence();
  let at = 100;
  for (const key of ['one/ui/hair/tracking', 'one/g/hair/tracking', 'one/ui/hair/tracking',
    'one/ui/accepted/tracking', 'one/ui/accepted/searching', 'two/ui/accepted/searching']) {
    const result = cadence.observe(at++, key, true);
    assert.equal(result.refresh, true); assert.equal(result.transition, true);
  }
});

test('G retains a summary on every completed frame regardless of elapsed time', () => {
  const cadence = new UiSummaryCadence();
  for (let i = 0; i < 20; i++) {
    const result = cadence.observe(100 + i, 'session/g/hair/tracking', false);
    assert.equal(result.refresh, true); assert.equal(result.refreshes, i + 1);
    assert.equal(result.skipped, 0); assert.equal(result.intervalMs, 0);
  }
});
