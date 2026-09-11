import assert from 'node:assert/strict';
import test from 'node:test';
import {startupPumpDiagnostic, startupPumpLabel} from './startup-pump-diagnostic.ts';

test('startup counters retain capture rejection and portrait video state without images or identity fields', () => {
  const marker = 'private-image-identity';
  const raw = {offered: 800, captured: 0, captureMisses: 800, published: 0, accepting: true, failed: false,
    sourceSHA256: marker, rgba: new Uint8Array([1, 2, 3]), startup: {faceRequests: 0, inferred: 0, prepared: 0, image: marker}};
  const video = {readyState: 4, paused: false, ended: false, currentTime: 31.25, width: 720, height: 1280, deviceId: marker};
  const receipt = startupPumpDiagnostic(raw, video, 34500, false, true);
  assert.equal(receipt.pump?.offered, 800); assert.equal(receipt.pump?.captureMisses, 800);
  assert.equal(receipt.pump?.startup.inferred, 0); assert.equal(receipt.pump?.startup.prepared, 0);
  assert.equal(receipt.video?.width, 720); assert.equal(receipt.video?.height, 1280);
  assert.equal(receipt.settled, true); assert.equal(receipt.publicationObserved, false);
  assert.equal(startupPumpLabel(receipt), 'Waiting for a camera image to be captured');
  assert.equal(JSON.stringify(receipt).includes(marker), false);
  assert.equal('sourceSHA256' in receipt.pump!, false); assert.equal('deviceId' in receipt.video!, false);
  raw.offered = 999; raw.startup.inferred = 8; video.width = 0;
  assert.equal(receipt.pump?.offered, 800); assert.equal(receipt.pump?.startup.inferred, 0); assert.equal(receipt.video?.width, 720);
});

test('unavailable counters stay null and malformed diagnostics cannot expand the scalar snapshot', () => {
  const missing = startupPumpDiagnostic(null, null, 10, false);
  assert.equal(missing.pump, null); assert.equal(missing.video, null);
  const bad = startupPumpDiagnostic({offered: Infinity, captured: -1, captureMisses: '12', accepting: 1,
    failed: {image: 'secret'}, startup: {inferred: NaN, prepared: [9]}}, [], 20, false);
  assert.equal(bad.pump?.offered, null); assert.equal(bad.pump?.captured, null);
  assert.equal(bad.pump?.captureMisses, null); assert.equal(bad.pump?.accepting, null);
  assert.equal(bad.pump?.failed, null); assert.equal(bad.pump?.startup.inferred, null);
  assert.equal(bad.pump?.startup.prepared, null); assert.equal(bad.video, null);
});

test('progress distinguishes bitmap, worker and preparation waits and keeps publication observation independent', () => {
  const make = (startup: Record<string, number>) => startupPumpDiagnostic({captured: 1, published: 0, startup}, null, 30, false);
  assert.match(startupPumpLabel(make({faceBitmapRequests: 1, faceBitmapReady: 0})), /for face tracking/);
  assert.match(startupPumpLabel(make({faceBitmapRequests: 1, faceBitmapReady: 1, faceRequests: 1, faceCompleted: 0})), /Waiting for face tracking/);
  assert.match(startupPumpLabel(make({faceCompleted: 1, inferred: 0})), /validation and hair admission/);
  assert.match(startupPumpLabel(make({faceCompleted: 1, inferred: 1, prepareCalls: 1, prepared: 0})), /Preparing the captured AR image/);
  assert.match(startupPumpLabel(make({prepareCalls: 1, prepared: 1})), /Publishing/);
  const published = startupPumpDiagnostic({published: 0, startup: {prepared: 1}}, null, 40, true, true);
  assert.equal(published.publicationObserved, true); assert.equal(published.pump?.published, 0);
});
