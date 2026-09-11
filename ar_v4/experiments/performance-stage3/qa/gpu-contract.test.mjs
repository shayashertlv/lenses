import assert from 'node:assert/strict';
import test from 'node:test';
import {expectedMechanismFor, verifyRendererMechanism, verifyInactiveMechanism, verifyFrameMechanisms} from './gpu-contract.mjs';
const ready = () => ({hasFace: true, hasMask: true, fallbackReason: null, candidatePerformance: {
  gpuCompositorUsed: true, gpuFallbackReason: null, cpuReadbackCalls: 3, cpuReadbackBytes: 72,
  nativePipeline: {zeroDropBranchSkipped: true, baselineReadbackCalls: 0, branchReadbackCalls: 0,
    native: {sharedCameraReady: true, sharedCameraFailure: null}},
  gpuTransferMetrics: {captureCalls: 2, beautyCaptureCalls: 1, cameraCaptureCalls: 1,
    fullFrameReadbackCalls: 0, fullFrameReadbackBytes: 0, compactReadbackCalls: 3, compactReadbackBytes: 72,
    readbackCalls: 3, readbackBytes: 72}}});
const cpu = () => {const stats = ready(), c = stats.candidatePerformance;
  Object.assign(c, {gpuCompositorUsed: false, gpuFallbackReason: 'This pair uses the exact CPU fallback path.', gpuTransferMetrics: null});
  Object.assign(c.nativePipeline, {zeroDropBranchSkipped: false, baselineReadbackCalls: 2, branchReadbackCalls: 1}); return stats;};
const flatten = object => Object.fromEntries(Object.entries(object).flatMap(([key, value]) => value && typeof value === 'object'
  ? Object.entries(flatten(value)).map(([name, item]) => [`${key}.${name}`, item]) : [[key, value]]));
test('exact frozen geometry determines GPU eligibility without epsilon relaxation', () => {
  assert.equal(expectedMechanismFor({rearDrop: {dropM: 0}}), 'gpu-zero-drop');
  assert.equal(expectedMechanismFor({rearDrop: {dropM: Number.EPSILON}}), 'cpu-nonzero-drop');
  for (const value of [undefined, NaN, -1]) assert.throws(() => expectedMechanismFor({rearDrop: {dropM: value}}));
});
test('pre-export GPU validation rejects readback, capture and fallback substitutions', () => {
  assert.equal(verifyRendererMechanism(ready(), 'gpu-zero-drop'), true);
  for (const change of [s => s.candidatePerformance.gpuTransferMetrics.fullFrameReadbackCalls++,
    s => s.candidatePerformance.gpuTransferMetrics.compactReadbackCalls--,
    s => s.candidatePerformance.nativePipeline.baselineReadbackCalls++,
    s => s.candidatePerformance.gpuCompositorUsed = false,
    s => s.candidatePerformance.nativePipeline.native.sharedCameraFailure = 'failed',
    s => s.hasMask = false]) {
    const stats = ready(); change(stats); assert.equal(verifyRendererMechanism(stats, 'gpu-zero-drop'), false);
  }
  assert.equal(verifyRendererMechanism(cpu(), 'gpu-zero-drop'), false);
  assert.equal(verifyRendererMechanism(cpu(), 'cpu-nonzero-drop'), true);
  assert.equal(verifyRendererMechanism(ready(), 'cpu-nonzero-drop'), false);
});
test('idle checks prohibit camera/compositor work and sustained checks retain explicit mechanism coverage', () => {
  const idle = ready(); idle.hasFace = idle.hasMask = false;
  const c = idle.candidatePerformance; c.gpuCompositorUsed = false;
  c.nativePipeline.native.sharedCameraReady = false;
  Object.assign(c.gpuTransferMetrics, {captureCalls: 1, cameraCaptureCalls: 0, compactReadbackCalls: 0});
  assert.equal(verifyInactiveMechanism(idle, 'no-face'), true);
  c.gpuTransferMetrics.cameraCaptureCalls = 1; assert.equal(verifyInactiveMechanism(idle, 'no-face'), false);
  const rows = [ready(), cpu()].map((stats, index) => ({serial: index + 1, hasFace: stats.hasFace, hasMask: stats.hasMask,
    fallback: stats.fallbackReason, native: flatten(stats.candidatePerformance)}));
  const result = verifyFrameMechanisms(rows, 'test3');
  assert.equal(result.passed, true); assert.equal(result.gpuFrames, 1); assert.equal(result.cpuNonzeroDropFrames, 1);
  assert.equal(result.gpuCoverageAmongTracked, .5);
  rows[1].native['nativePipeline.zeroDropBranchSkipped'] = true;
  assert.equal(verifyFrameMechanisms(rows, 'test3').passed, false);
});
