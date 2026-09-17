import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cameraDeliveryFps, distribution, FrameProfiler, summarize} from '../src/pipeline/profiler.ts';
import type {FrameInput} from '../src/pipeline/profiler.ts';

function frame(at: number, sequence = 1, sessionId = 'session'): FrameInput {
  return {sessionId, sequence, hair: true, capturedAtMs: at - 20, publishedAtMs: at,
    videoPresentedFrames: sequence * 3, videoMediaTime: at / 1000, videoPresentationTimeMs: null, cameraSettingFps: 30,
    sourceWidth: 1280, sourceHeight: 720, sourceDrawMs: 1, detectorDrawMs: 1, sourceReadbackMs: 1, sourceHashMs: 2,
    faceBitmapMs: 1, faceRequestWallMs: 8, faceInferenceMs: 6, faceWorkerMs: 7, faceExtractionMs: .5,
    faceWorkerValidationMs: .5, faceClientValidationMs: .1, faceTransportSchedulingMs: 1, prerequisitesWaitMs: 8,
    detectionHashMs: 1, schedulerWaitMs: 10, hairAdmissionWaitMs: 0, hairWaitMs: 0, hairInferenceMs: 9, hairExtractionMs: 1,
    prepareMs: 5, gpuWaitMs: 1, poseMs: 2, finishMs: 3, maskUploadMs: .5, continuityMs: .2, submitMs: 2, renderMs: 8, totalMs: 20,
    hasFace: true, hasMask: true, fallback: null, faceDelegate: 'CPU', hairDelegate: 'GPU', gpuRenderer: 'test',
    native: {'render.passes': 2}};
}

test('cadence uses completed publications, camera delivery uses its own frame counter, overlapping stages stay distinct', () => {
  const p = new FrameProfiler(); p.add(frame(100, 1)); p.add(frame(200, 2)); p.add(frame(300, 3));
  const rows = p.samplesAfter(0), summary = summarize(rows);
  assert.equal(summary.processedFps, 10); assert.equal(summary.videoDeliveryFps, 30); assert.equal(cameraDeliveryFps(rows), 30);
  assert.equal(summary.processing?.median, 20); assert.equal(summary.stages.faceInferenceMs?.median, 6); assert.equal(summary.stages.submitMs?.median, 2);
  assert.equal(summary.hairCoverage, 1);
  assert.deepEqual(distribution([1, 2, 3, 100]), {median: 2.5, p95: 100, max: 100});
});

test('recent rows never cross a session change and exports do not lend internal storage', () => {
  const p = new FrameProfiler(3); p.add(frame(100, 1)); p.add(frame(200, 2)); p.add(frame(300, 3));
  const rows = p.samplesAfter(0); rows[0]!.native!['render.passes'] = 99;
  assert.equal(p.samplesAfter(0)[0]!.native!['render.passes'], 2);
  assert.equal(p.recent('session').length, 3);
  p.add(frame(400, 4, 'new')); assert.equal(p.samplesAfter(0)[0]!.serial, 2);
  assert.equal(p.recent('new').length, 1); assert.equal(p.recent('session').length, 0);
  assert.equal(p.recent('new', 50).length, 1);
  const output = JSON.parse(p.exportJSON()) as {schema: string; samples: unknown[]; privacy: string};
  assert.equal(output.schema, 'ar-timings-v1'); assert.equal(output.samples.length, 3); assert.match(output.privacy, /no camera images/);
});

test('image ownership identifiers never enter timing storage or exports', () => {
  const p = new FrameProfiler();
  const sample = p.add({...frame(100), native: {'render.sourceIdentity.sourceSHA256': 'private-image-hash', 'detectionSHA256': 'private-pose-hash',
    'audit.beforePngDataUrl': 'data:', 'render.passes': 2, 'pump.published': 1}});
  assert.deepEqual(sample.native, {'render.passes': 2, 'pump.published': 1});
  assert.doesNotMatch(p.exportJSON(), /private-image-hash|private-pose-hash|sourceIdentity|SHA256|PngDataUrl/);
});

test('coverage uses all measured frames for tracking and only tracked hair frames for mask coverage', () => {
  const rows = [frame(100), {...frame(200), hasFace: false, hasMask: false}, {...frame(300), hasMask: false}];
  const summary = summarize(rows.map((row, index) => ({...row, serial: index + 1})));
  assert.equal(summary.frames, 3); assert.equal(summary.trackedFrames, 2); assert.equal(summary.maskedFrames, 1);
  assert.equal(summary.trackedHairFrames, 2); assert.equal(summary.hairCoverage, .5);
  assert.equal(summarize([]).frames, 0); assert.equal(summarize([]).hairCoverage, null);
  const off = summarize(rows.map((row, index) => ({...row, serial: index + 1, hair: false, hasMask: false, hasFace: true})));
  assert.equal(off.trackedFrames, 3); assert.equal(off.hairCoverage, null);
});

test('rows with a publication before their capture are refused', () => {
  const p = new FrameProfiler();
  assert.throws(() => p.add({...frame(100), capturedAtMs: 200}), /Invalid/);
});
