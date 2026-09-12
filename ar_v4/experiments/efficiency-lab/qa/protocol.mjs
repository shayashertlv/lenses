import {checkProtocol as checkG, mechanismUse as mechanismG, summarizeMechanisms as summarizeG,
  fallbackAllowed, BOUNDED_FENCE_REASON, noAuxiliaryCamera, isBoundedSoftwareFallback} from '../../speed-lab/qa/protocol.mjs';
export {noAuxiliaryCamera};
export const PREWARM_REASON = 'First valid pair retains the completed synchronous temple prewarm.';
const gOptions = options => Object.fromEntries(['reuseSourcePixels','fewerCopies','asyncReadback','prewarmTemples'].map(key => [key, options[key]]));
/** Independently derive the full-width band from actual editable-minus-protected
 * pixels. This QA scan does not import the candidate's rectangle helper. */
export function expectedBranchRegion(geometry, width, height) {
  const protection = geometry.protection, inside = (r, x, y) => x >= r.x0 && x < r.x1 && y >= r.y0 && y < r.y1;
  let y0 = height, y1 = 0;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (protection.editableRects.some(r => inside(r, x, y)) && !protection.protectedRects.some(r => inside(r, x, y))) {
      y0 = Math.min(y0, y); y1 = y + 1; break;
    }
  }
  return y0 < y1 ? {x0: 0, y0, x1: width, y1} : null;
}
const adjusted = stats => {
  const copy = structuredClone(stats), pipeline = copy.candidatePerformance.nativePipeline, pbo = pipeline.efficiencyLab?.branchPbo;
  pipeline.branchReadbackCalls += pbo?.retrievedCalls ?? 0;
  pipeline.branchReadbackBytes += pbo?.retrievedBytes ?? 0;
  return copy;
};
function branchFallback(efficiency, pipeline, bytes, policy) {
  const pbo = efficiency?.branchPbo;
  return fallbackAllowed(policy) && efficiency.asyncTemplesUsed === false
    && efficiency.branchFallback === BOUNDED_FENCE_REASON && pbo?.fallbackReason === BOUNDED_FENCE_REASON
    && pbo.completed === false && pbo.queuedCalls === 1 && pbo.queuedBytes === bytes
    && pbo.retrievedCalls === 0 && pbo.retrievedBytes === 0 && pbo.waitMs >= 500 && pbo.polls > 0
    && pipeline.branchReadbackCalls === 1 && pipeline.branchReadbackBytes === bytes;
}
export function checkProtocol(name, options, stats, geometry, width, height, policy = {}) {
  const checks = checkG('candidate', gOptions(options), adjusted(stats), geometry, width, height, policy);
  if (name === 'current') return checks;
  const pipeline = stats.candidatePerformance.nativePipeline, efficiency = pipeline.efficiencyLab;
  const bytes = width * height * 4, drop = geometry.rearDrop.dropM, branch = efficiency?.branchPbo;
  checks.newOptions = ['poolReadbackScratch','asyncTemples'].every(key => stats.candidatePerformance.speedLab.options[key] === options[key]);
  for (const key of ['cropBranchReadback', 'omitBranchLenses', 'ownedPackState', 'wordCompose']) if (key in options)
    checks.newOptions &&= stats.candidatePerformance.speedLab.options[key] === options[key];
  checks.templeRequested = efficiency?.asyncTemplesRequested === options.asyncTemples;
  const prewarm = pipeline.speedLab.prewarmAttempted;
  if (!options.asyncTemples || drop === 0 || prewarm) {
    checks.templeNoUnneededRead = efficiency?.asyncTemplesUsed === false && !branch
      && efficiency.branchSubmittedBeforeBeautyAwait === false
      && efficiency.branchFallback === (options.asyncTemples && prewarm ? PREWARM_REASON : null);
  } else {
    checks.templeUsed = efficiency.asyncTemplesUsed === true && efficiency.branchSubmittedBeforeBeautyAwait === true
      && efficiency.branchFallback === null && pipeline.branchReadbackCalls === 0 && pipeline.branchReadbackBytes === 0
      && branch?.completed === true && branch.fallbackReason === null && branch.queuedCalls === 1 && branch.retrievedCalls === 1
      && branch.queuedBytes === bytes && branch.retrievedBytes === bytes
      || branchFallback(efficiency, pipeline, bytes, policy);
  }
  for (const [label, pbo] of [['beauty', pipeline.native.speedLab.pbo], ['branch', branch]]) {
    if (!pbo?.retrievedCalls) continue;
    checks[`${label}ScratchAccounting`] = pbo.scratchBytesAllocated + pbo.scratchBytesReused === pbo.retrievedBytes
      && pbo.outputBytesAllocated === pbo.retrievedBytes
      && (options.poolReadbackScratch || pbo.scratchBytesReused === 0);
  }
  if ('cropBranchReadback' in options) {
    const region = efficiency?.branchRegion;
    checks.regionRequested = region?.requested === options.cropBranchReadback;
    if (options.cropBranchReadback && drop !== 0) {
      const rectangle = expectedBranchRegion(geometry, width, height), expectedBytes = rectangle ? width * (rectangle.y1 - rectangle.y0) * 4 : 0;
      const sameRectangle = rectangle === null ? region?.rectangle === null
        : ['x0','y0','x1','y1'].every(key => region?.rectangle?.[key] === rectangle[key]);
      checks.regionUsed = region?.used === true && region.fallback === null && region.fullBytes === bytes
        && sameRectangle && region.readBytes === expectedBytes
        && pipeline.branchReadbackBytes === expectedBytes && !branch;
      // The frozen G check assumes one full branch. A proven empty region has
      // no live download; retain actual bytes in the independent total check.
      checks.branch = pipeline.branchReadbackCalls === Number(rectangle !== null) && pipeline.zeroDropBranchSkipped === false;
    } else checks.regionNotUsed = region?.used === false;
  }
  if ('omitBranchLenses' in options) {
    const lenses = efficiency?.branchLenses;
    checks.lensesRequested = lenses?.requested === options.omitBranchLenses;
    checks.lensesUsed = options.omitBranchLenses && drop !== 0
      ? lenses?.used === true && Number.isSafeInteger(lenses.omittedMaterials) && lenses.omittedMaterials > 0 && lenses.fallback === null
      : lenses?.used === false;
  }
  if ('ownedPackState' in options) {
    const pbo = pipeline.native.speedLab.pbo, nativeFallback = isBoundedSoftwareFallback(gOptions(options), pipeline.native, width, height, policy);
    const integer = value => Number.isSafeInteger(value) && value >= 0;
    checks.packStateRequested = pbo?.ownedPackStateRequested === options.ownedPackState;
    checks.packStateAccounting = pbo != null && ['stateQueryCalls','packStateCacheHits','packStateQueriesAvoided','packStateInvalidations'].every(key => integer(pbo[key]))
      && Number.isFinite(pbo.stateQueryMs) && pbo.stateQueryMs >= 0
      && pbo.packStateQueriesAvoided === pbo.packStateCacheHits * 4
      && pbo.stateQueryCalls + pbo.packStateQueriesAvoided === 6 * (pbo.queuedCalls + Number(pbo.retrievedCalls > 0));
    // Every matched image has exact reusable source pixels: one capture and one
    // retrieval scope. First temple prewarm does not replace this native path.
    checks.packStateUsed = options.ownedPackState && !nativeFallback
      ? pbo?.ownedPackStateUsed === true && pbo.packStateCacheHits === 1 && pbo.packStateQueriesAvoided === 4
        && pbo.packStateInvalidations === 0 && pbo.packStateFallbackReason === null
      : pbo?.ownedPackStateUsed === false && pbo.packStateCacheHits === 0 && pbo.packStateQueriesAvoided === 0
        && pbo.packStateInvalidations === 0 && pbo.packStateFallbackReason === null;
  }
  if ('wordCompose' in options) {
    const perf = stats.candidatePerformance;
    checks.wordComparisonRequested = perf.wordComparisonRequested === options.wordCompose;
    checks.wordComparisonUsed = options.wordCompose
      ? stats.hasMask === true && stats.fallbackReason === null && perf.wordComparisonUsed === true
        && perf.wordComparedPixels === width * height
      : perf.wordComparisonUsed === false && perf.wordComparedPixels === 0;
  }
  return checks;
}
export function mechanismUse(options, stats, geometry, width, height, policy = {}) {
  const original = mechanismG(gOptions(options), adjusted(stats), geometry, width, height, policy);
  const pipeline = stats.candidatePerformance.nativePipeline, efficiency = pipeline.efficiencyLab;
  const acceptedBranchFallback = branchFallback(efficiency, pipeline, width * height * 4, policy);
  return {...original,
    actualFastPathUsed: Object.values(checkProtocol('candidate', options, stats, geometry, width, height, {...policy, allowAsyncFallback: false})).every(Boolean),
    actualAsyncTemplesUsed: efficiency.asyncTemplesUsed,
    acceptedBranchFallback,
    branchFallbackReason: efficiency.branchFallback,
    firstPairPrewarm: pipeline.speedLab.prewarmAttempted,
    scratchBytesReused: (pipeline.native.speedLab.pbo?.scratchBytesReused ?? 0) + (efficiency.branchPbo?.scratchBytesReused ?? 0),
    ...('ownedPackState' in options ? {actualOwnedPackStateUsed: pipeline.native.speedLab.pbo?.ownedPackStateUsed === true,
      packStateQueriesAvoided: pipeline.native.speedLab.pbo?.packStateQueriesAvoided ?? 0,
      stateQueryCalls: pipeline.native.speedLab.pbo?.stateQueryCalls ?? 0} : {}),
    ...('wordCompose' in options ? {actualWordComparisonUsed: stats.candidatePerformance.wordComparisonUsed === true,
      wordComparedPixels: stats.candidatePerformance.wordComparedPixels ?? 0} : {}),
    ...('cropBranchReadback' in options ? {actualRegionUsed: efficiency.branchRegion.used,
      branchRegionBytesSaved: efficiency.branchRegion.used ? efficiency.branchRegion.fullBytes - efficiency.branchRegion.readBytes : 0,
      actualBranchLensesOmitted: efficiency.branchLenses.used} : {})};
}
export function summarizeMechanisms(rows) {
  return {...summarizeG(rows), actualAsyncTempleCases: rows.filter(row => row.mechanism?.actualAsyncTemplesUsed).length,
    acceptedBranchFallbackCases: rows.filter(row => row.mechanism?.acceptedBranchFallback).length,
    firstPairPrewarmCases: rows.filter(row => row.mechanism?.firstPairPrewarm).length,
    scratchBytesReused: rows.reduce((total, row) => total + (row.mechanism?.scratchBytesReused ?? 0), 0),
    ...(rows.some(row => 'actualOwnedPackStateUsed' in (row.mechanism ?? {})) ? {
      actualOwnedPackStateCases: rows.filter(row => row.mechanism?.actualOwnedPackStateUsed).length,
      packStateQueriesAvoided: rows.reduce((total, row) => total + (row.mechanism?.packStateQueriesAvoided ?? 0), 0),
      stateQueryCalls: rows.reduce((total, row) => total + (row.mechanism?.stateQueryCalls ?? 0), 0)} : {}),
    ...(rows.some(row => 'actualWordComparisonUsed' in (row.mechanism ?? {})) ? {
      actualWordComparisonCases: rows.filter(row => row.mechanism?.actualWordComparisonUsed).length,
      wordComparedPixels: rows.reduce((total, row) => total + (row.mechanism?.wordComparedPixels ?? 0), 0)} : {}),
    ...(rows.some(row => 'actualRegionUsed' in (row.mechanism ?? {})) ? {
      actualRegionCases: rows.filter(row => row.mechanism?.actualRegionUsed).length,
      branchRegionBytesSaved: rows.reduce((total, row) => total + (row.mechanism?.branchRegionBytesSaved ?? 0), 0),
      actualBranchLensCases: rows.filter(row => row.mechanism?.actualBranchLensesOmitted).length} : {})};
}
