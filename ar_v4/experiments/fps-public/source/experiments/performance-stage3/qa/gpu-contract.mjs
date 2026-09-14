/** Verify the actual Test3 counters, independently of exact image equality.
 * These checks consume stats BEFORE a diagnostic export reads full-color pixels. */
export const GPU_CONTRACT_READY = true;
export function expectedMechanismFor(geometry) {
  const drop = geometry?.rearDrop?.dropM;
  if (typeof drop !== 'number' || !Number.isFinite(drop) || drop < 0) throw new Error('The frozen rear-drop eligibility is missing or invalid.');
  return drop === 0 ? 'gpu-zero-drop' : 'cpu-nonzero-drop';
}
export function verifyRendererMechanism(stats, expectedMechanism) {
  const candidate = stats?.candidatePerformance, stages = candidate?.nativePipeline, metrics = candidate?.gpuTransferMetrics;
  if (!stats?.hasFace || !stats.hasMask || stats.fallbackReason !== null || !stages) return false;
  if (expectedMechanism === 'cpu-nonzero-drop') return candidate.gpuCompositorUsed === false
    && candidate.gpuFallbackReason === 'This pair uses the exact CPU fallback path.' && metrics === null
    && stages.zeroDropBranchSkipped === false && stages.branchReadbackCalls === 1;
  if (expectedMechanism !== 'gpu-zero-drop' || !metrics) return false;
  return candidate.gpuCompositorUsed === true && candidate.gpuFallbackReason === null
    && stages.zeroDropBranchSkipped === true && stages.baselineReadbackCalls === 0 && stages.branchReadbackCalls === 0
    && stages.native?.sharedCameraReady === true && stages.native.sharedCameraFailure === null
    && metrics.captureCalls === 2 && metrics.beautyCaptureCalls === 1 && metrics.cameraCaptureCalls === 1
    && metrics.fullFrameReadbackCalls === 0 && metrics.fullFrameReadbackBytes === 0
    && metrics.compactReadbackCalls === 3 && metrics.compactReadbackBytes > 8
    && metrics.readbackCalls === metrics.compactReadbackCalls && metrics.readbackBytes === metrics.compactReadbackBytes
    && candidate.cpuReadbackCalls === metrics.readbackCalls && candidate.cpuReadbackBytes === metrics.readbackBytes;
}
export function verifyInactiveMechanism(stats, phase) {
  const candidate = stats?.candidatePerformance, native = candidate?.nativePipeline?.native;
  const metrics = candidate?.gpuTransferMetrics ?? native?.gpu;
  return Boolean(candidate && native && !stats.hasMask && candidate.gpuCompositorUsed === false
    && (phase !== 'no-face' || stats.hasFace === false) && native.sharedCameraReady === false
    && metrics && metrics.cameraCaptureCalls === 0 && metrics.compactReadbackCalls === 0
    && (native.sharedReadback?.readbackCalls ?? 0) === 0);
}
export function verifyFrameMechanisms(rows, pipeline) {
  if (pipeline !== 'test3') return {passed: true, applicable: false, pipeline, frames: rows.length};
  const result = {passed: true, applicable: true, pipeline, frames: rows.length, gpuFrames: 0, cpuNonzeroDropFrames: 0,
    untrackedFrames: 0, failures: [], fallbackReasons: {}};
  for (const row of rows) {
    const candidatePerformance = {};
    for (const [key, value] of Object.entries(row.native ?? {})) {
      const parts = key.split('.'); let object = candidatePerformance;
      for (const part of parts.slice(0, -1)) object = object[part] ??= {};
      object[parts.at(-1)] = value;
    }
    const stages = candidatePerformance.nativePipeline, hasFace = row.hasFace;
    const expected = stages?.zeroDropBranchSkipped === true ? 'gpu-zero-drop' : 'cpu-nonzero-drop';
    const stats = {hasFace, hasMask: row.hasMask, fallbackReason: row.fallback, candidatePerformance};
    const valid = hasFace ? verifyRendererMechanism(stats, expected) : verifyInactiveMechanism(stats, 'no-face');
    if (!valid) result.failures.push({serial: row.serial, expected, gpuCompositorUsed: candidatePerformance.gpuCompositorUsed,
      gpuFallbackReason: candidatePerformance.gpuFallbackReason, hasFace, hasMask: row.hasMask});
    else if (!hasFace) result.untrackedFrames++;
    else if (expected === 'gpu-zero-drop') result.gpuFrames++;
    else result.cpuNonzeroDropFrames++;
    const reason = candidatePerformance.gpuFallbackReason ?? 'none';
    result.fallbackReasons[reason] = (result.fallbackReasons[reason] ?? 0) + 1;
  }
  result.passed = rows.length > 0 && result.failures.length === 0;
  result.gpuCoverageAmongTracked = rows.length > result.untrackedFrames ? result.gpuFrames / (rows.length - result.untrackedFrames) : null;
  return result;
}
