import assert from 'node:assert/strict';
import {test} from 'node:test';
import {CaptureRateAdmission} from './capture-rate.ts';

test('capture rates admit the first image immediately and enforce start-to-start bounds', () => {
  for (const rate of [12, 10, 8]) {
    const admission = new CaptureRateAdmission(rate), start = 17, interval = 1000 / rate;
    assert.ok(admission.canCapture(start)); admission.accepted(start);
    assert.equal(admission.canCapture(start + interval - 0.001), false);
    assert.ok(admission.canCapture(start + interval)); admission.accepted(start + interval);
    assert.equal(admission.stats.captures, 2);
  }
});

test('elapsed idle time creates no catch-up burst and a fresh session starts immediately', () => {
  const admission = new CaptureRateAdmission(10);
  admission.accepted(10); assert.ok(admission.canCapture(10_000)); admission.accepted(10_000);
  assert.equal(admission.canCapture(10_001), false); assert.equal(admission.canCapture(10_099.99), false);
  assert.ok(admission.canCapture(10_100));
  const restarted = new CaptureRateAdmission(10); assert.ok(restarted.canCapture(10_001));
  assert.equal(restarted.stats.captures, 0);
});

test('eligibility checks and failed snapshots do not consume the next admission', () => {
  const admission = new CaptureRateAdmission(8);
  for (let i = 0; i < 5; i++) assert.ok(admission.canCapture(4 + i));
  assert.equal(admission.stats.captures, 0); admission.accepted(10);
  assert.throws(() => admission.accepted(11), /interval/);
  assert.equal(admission.stats.captures, 1); assert.ok(admission.canCapture(135));
});

test('null keeps G uncapped and counts distinct ready callbacks separately from repeats', () => {
  const admission = new CaptureRateAdmission(null);
  assert.ok(admission.observeReady(0)); assert.equal(admission.observeReady(0), false);
  assert.ok(admission.observeReady(0.03)); admission.skipBackpressure();
  admission.accepted(1); assert.ok(admission.canCapture(1.001)); admission.accepted(1.001);
  assert.deepEqual(admission.stats, {rateHz: null, candidateCallbacks: 2, duplicateCallbacks: 1,
    rateSkipped: 0, backpressureSkipped: 1, captures: 2});
});

test('invalid admission rates and timestamps fail explicitly', () => {
  for (const rate of [0, -1, Number.NaN, Infinity]) assert.throws(() => new CaptureRateAdmission(rate));
  const admission = new CaptureRateAdmission(10);
  for (const time of [-1, Number.NaN, Infinity]) assert.throws(() => admission.canCapture(time));
});
