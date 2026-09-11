export const BOUNDED_FENCE_REASON = 'The native readback fence exceeded its bounded wait.';

export function fallbackAllowed(policy = {}) {
  return policy.allowAsyncFallback === true && policy.generated === false
    && typeof policy.renderer === 'string' && /SwiftShader/.test(policy.renderer);
}

/** The sole opt-in exception proves an unconsumed software fence and exact synchronous read counts.
 * Pixel, pose and guard assertions remain separate and mandatory. */
export function isBoundedSoftwareFallback(options, native, width, height, policy = {}) {
  const speed = native?.speedLab, pbo = speed?.pbo, calls = options.reuseSourcePixels ? 1 : 2;
  const bytes = width * height * 4;
  return fallbackAllowed(policy) && options.asyncReadback === true
    && speed?.asyncReadbackRequested === true && speed.asyncReadbackUsed === false
    && speed.asyncFallback === BOUNDED_FENCE_REASON
    && pbo?.completed === false && pbo.fallbackReason === BOUNDED_FENCE_REASON
    && pbo.queuedCalls === calls && pbo.queuedBytes === calls * bytes
    && pbo.retrievedCalls === 0 && pbo.retrievedBytes === 0
    && Number.isFinite(pbo.waitMs) && pbo.waitMs >= 500
    && Number.isSafeInteger(pbo.polls) && pbo.polls > 0
    && native?.sharedReadback?.readbackCalls === calls && native.sharedReadback.readbackBytes === calls * bytes;
}

/** Mechanism checks accompany exact pixels; an allowed fallback is never labeled a fast-path pass. */
export function checkProtocol(name, options, stats, geometry, width, height, policy = {}) {
  const pipeline = stats?.candidatePerformance?.nativePipeline, native = pipeline?.native;
  const bytes = width * height * 4, drop = geometry?.rearDrop?.dropM ?? 0;
  const checks = {
    nativeReady: native?.sharedCameraReady === true && native.sharedCameraFailure === null,
    branch: pipeline?.branchReadbackCalls === Number(drop !== 0)
      && pipeline?.zeroDropBranchSkipped === (drop === 0),
    nativeBeautyOwned: pipeline?.baselineReadbackCalls === 0,
    totalCpuReads: stats?.candidatePerformance?.cpuReadbackCalls === (pipeline?.baselineReadbackCalls ?? 0)
      + (pipeline?.branchReadbackCalls ?? 0) + (native?.sharedReadback?.readbackCalls ?? 0)
      + (native?.speedLab?.pbo?.retrievedCalls ?? 0) + (pipeline?.speedLab?.prewarmReadbackCalls ?? 0)
      && stats?.candidatePerformance?.cpuReadbackBytes === (pipeline?.baselineReadbackBytes ?? 0)
      + (pipeline?.branchReadbackBytes ?? 0) + (native?.sharedReadback?.readbackBytes ?? 0)
      + (native?.speedLab?.pbo?.retrievedBytes ?? 0) + (pipeline?.speedLab?.prewarmReadbackBytes ?? 0),
  };
  if (name === 'current') {
    checks.twoNativeReads = native?.sharedReadback?.readbackCalls === 2
      && native.sharedReadback.readbackBytes === bytes * 2;
    return checks;
  }
  const speed = native?.speedLab, outer = stats?.candidatePerformance?.speedLab, temple = pipeline?.speedLab;
  const calls = options.reuseSourcePixels ? 1 : 2;
  checks.options = Object.entries(options).every(([key, value]) => outer?.options?.[key] === value);
  checks.sourceMechanism = speed?.reuseSourcePixelsRequested === options.reuseSourcePixels
    && speed.reuseSourcePixelsUsed === options.reuseSourcePixels && speed.sourceReuseFallback === null;
  const boundedFallback = isBoundedSoftwareFallback(options, native, width, height, policy);
  checks.async = speed?.asyncReadbackRequested === options.asyncReadback
    && (speed.asyncReadbackUsed === options.asyncReadback && speed.asyncFallback === null || boundedFallback);
  checks.copyOwnership = outer?.sourceCanvasBorrowed === options.fewerCopies
    && outer.sourceCopyBytesAvoided === (options.fewerCopies ? bytes : 0)
    && temple?.sourceCopiesAvoided === Number(options.fewerCopies);
  checks.sourceIdentity = !(options.reuseSourcePixels || options.fewerCopies)
    || (outer?.sourceIdentity?.sourceSHA256 === geometry.hairPreview.sourceSHA256 && outer.sourceFallbackReason === null);
  checks.readbacks = boundedFallback || (options.asyncReadback
    ? native?.sharedReadback === null && speed?.pbo?.completed === true && speed.pbo.fallbackReason === null
      && speed.pbo.queuedCalls === calls && speed.pbo.queuedBytes === calls * bytes
      && speed.pbo.retrievedCalls === calls && speed.pbo.retrievedBytes === calls * bytes
    : native?.sharedReadback?.readbackCalls === calls && native.sharedReadback.readbackBytes === calls * bytes);
  checks.cleanSkipped = !options.reuseSourcePixels || native?.cleanSubmitMs === 0;
  checks.prewarm = temple?.prewarmRequested === options.prewarmTemples
    && (!options.prewarmTemples || temple.prewarmCompleted === true && temple.prewarmFailure === null)
    && temple.prewarmReadbackCalls === Number(temple.prewarmAttempted)
    && temple.prewarmReadbackBytes === (temple.prewarmAttempted ? bytes : 0);
  return checks;
}

export function mechanismUse(options, stats, geometry, width, height, policy = {}) {
  const native = stats?.candidatePerformance?.nativePipeline?.native;
  return {
    actualFastPathUsed: Object.values(checkProtocol('candidate', options, stats, geometry, width, height)).every(Boolean),
    actualAsyncReadbackUsed: native?.speedLab?.asyncReadbackUsed === true,
    asyncFallbackAllowed: fallbackAllowed(policy),
    acceptedAsyncFallback: isBoundedSoftwareFallback(options, native, width, height, policy),
    asyncFallbackReason: native?.speedLab?.asyncFallback ?? null,
  };
}

export function summarizeMechanisms(rows) {
  const reasons = {};
  for (const row of rows) if (row.mechanism?.asyncFallbackReason) {
    const reason = row.mechanism.asyncFallbackReason; reasons[reason] = (reasons[reason] ?? 0) + 1;
  }
  return {cases: rows.length, actualFastPathCases: rows.filter(row => row.mechanism?.actualFastPathUsed).length,
    actualAsyncReadbackCases: rows.filter(row => row.mechanism?.actualAsyncReadbackUsed).length,
    acceptedAsyncFallbackCases: rows.filter(row => row.mechanism?.acceptedAsyncFallback).length,
    asyncFallbackCases: Object.values(reasons).reduce((sum, value) => sum + value, 0), asyncFallbackReasons: reasons,
    scope: 'Saved case presentations only; controls and optional earlier warmup/timing repetitions are excluded.'};
}

export function noAuxiliaryCamera(native) {
  return native?.sharedCameraReady === false && native.cleanSubmitMs === 0
    && (native.sharedReadback?.readbackCalls ?? 0) === 0
    && (native.speedLab?.pbo?.queuedCalls ?? 0) <= 1
    && (native.speedLab?.pbo?.retrievedCalls ?? 0) <= 1
    && native.speedLab?.reuseSourcePixelsUsed !== true;
}
