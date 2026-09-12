import assert from 'node:assert/strict';
import {test} from 'node:test';
import {PROFILES} from '../profiles.ts';
import {checkProtocol,mechanismUse,summarizeMechanisms,PREWARM_REASON,expectedBranchRegion} from './protocol.mjs';
import {BOUNDED_FENCE_REASON} from '../../speed-lab/qa/protocol.mjs';
function example(options, drop = 0, first = false) {
  const bytes = 16 * 8 * 4, calls = options.reuseSourcePixels ? 1 : 2;
  const warm = Number(options.prewarmTemples && first);
  const geometry = {rearDrop: {dropM: drop}, hairPreview: {sourceSHA256: 'a'.repeat(64)},
    protection: {editableRects: [{x0:2,y0:1,x1:14,y1:7}],protectedRects:[{x0:0,y0:1,x1:16,y1:3}]}};
  const pipeline = {baselineReadbackCalls: 0, baselineReadbackBytes: 0,
    branchReadbackCalls: Number(drop !== 0), branchReadbackBytes: Number(drop !== 0) * bytes,
    zeroDropBranchSkipped: drop === 0,
    speedLab: {sourceCopiesAvoided: Number(options.fewerCopies), prewarmRequested: options.prewarmTemples,
      prewarmAttempted: Boolean(warm), prewarmCompleted: options.prewarmTemples, prewarmFailure: null,
      prewarmReadbackCalls: warm, prewarmReadbackBytes: warm * bytes},
    native: {sharedCameraReady: true, sharedCameraFailure: null, cleanSubmitMs: options.reuseSourcePixels ? 0 : 1,
      sharedReadback: options.asyncReadback ? null : {readbackCalls: calls, readbackBytes: calls * bytes},
      speedLab: {reuseSourcePixelsRequested: options.reuseSourcePixels, reuseSourcePixelsUsed: options.reuseSourcePixels,
        sourceReuseFallback: null, asyncReadbackRequested: options.asyncReadback, asyncReadbackUsed: options.asyncReadback,
        asyncFallback: null, pbo: options.asyncReadback ? {completed: true, fallbackReason: null,
          queuedCalls: calls, queuedBytes: calls * bytes, retrievedCalls: calls, retrievedBytes: calls * bytes} : null}}};
  const stats = {hasMask: true, fallbackReason: null, candidatePerformance: {nativePipeline: pipeline,
    wordComparisonRequested: options.wordCompose, wordComparisonUsed: options.wordCompose, wordComparedPixels: options.wordCompose ? 16 * 8 : 0,
    cpuReadbackCalls: calls + warm + Number(drop !== 0), cpuReadbackBytes: (calls + warm + Number(drop !== 0)) * bytes,
    speedLab: {options, sourceCanvasBorrowed: options.fewerCopies, sourceCopyBytesAvoided: options.fewerCopies ? bytes : 0,
      sourceIdentity: {sourceSHA256: geometry.hairPreview.sourceSHA256}, sourceFallbackReason: null}}};
  const enabled = options.asyncTemples && drop !== 0 && !first;
  const pbo = () => ({completed:true,fallbackReason:null,queuedCalls:1,queuedBytes:bytes,retrievedCalls:1,retrievedBytes:bytes,scratchBytesAllocated:options.poolReadbackScratch?0:bytes,scratchBytesReused:options.poolReadbackScratch?bytes:0,outputBytesAllocated:bytes});
  Object.assign(pipeline.native.speedLab.pbo,pbo());
  Object.assign(pipeline.native.speedLab.pbo, {stateQueryCalls: options.ownedPackState ? 8 : 12, stateQueryMs: 1,
    ownedPackStateRequested: options.ownedPackState, ownedPackStateUsed: options.ownedPackState,
    packStateCacheHits: Number(options.ownedPackState), packStateQueriesAvoided: options.ownedPackState ? 4 : 0,
    packStateInvalidations: 0, packStateFallbackReason: null});
  pipeline.efficiencyLab={asyncTemplesRequested:options.asyncTemples,asyncTemplesUsed:enabled,branchSubmittedBeforeBeautyAwait:enabled,branchFallback:options.asyncTemples&&first?PREWARM_REASON:null,branchPbo:enabled?pbo():null};
  pipeline.efficiencyLab.branchRegion={requested:options.cropBranchReadback,used:Boolean(options.cropBranchReadback&&drop!==0),rectangle:null,fullBytes:bytes,readBytes:0,fallback:null};
  pipeline.efficiencyLab.branchLenses={requested:options.omitBranchLenses,used:Boolean(options.omitBranchLenses&&drop!==0),omittedMaterials:options.omitBranchLenses&&drop!==0?1:0,fallback:null};
  if(enabled){pipeline.branchReadbackCalls=0;pipeline.branchReadbackBytes=0;}
  if(options.cropBranchReadback&&drop!==0){const rectangle=expectedBranchRegion(geometry,16,8),readBytes=rectangle?16*(rectangle.y1-rectangle.y0)*4:0;
    Object.assign(pipeline.efficiencyLab.branchRegion,{rectangle,readBytes});pipeline.branchReadbackBytes=readBytes;
    stats.candidatePerformance.cpuReadbackBytes-=bytes-readBytes;}
  return {stats, geometry, pipeline};
}
const passed = checks => Object.values(checks).every(Boolean);
test('exact G readbacks and new mechanisms retain all transfers and first-use prewarm costs', () => {
  for (const name of ['scratch','temples','combined','deferred','region','lens','gl-state','word-compose']) for (const drop of [0,.02]) for (const first of [true,false]) {
    const options=PROFILES[name].options,value=example(options,drop,first);
    assert.equal(passed(checkProtocol('candidate',options,value.stats,value.geometry,16,8)),true,`${name}/${drop}/${first}`);
  }
});

test('W requires real within-pair query savings and retains exact accounting rather than trusting its option label', () => {
  const options = PROFILES['gl-state'].options;
  for (const corrupt of [pbo => {pbo.ownedPackStateRequested = false;}, pbo => {pbo.ownedPackStateUsed = false;},
    pbo => {pbo.packStateQueriesAvoided = 0;}, pbo => {pbo.packStateCacheHits = 2;}, pbo => {pbo.stateQueryCalls = 7;},
    pbo => {pbo.stateQueryMs = NaN;}, pbo => {pbo.packStateInvalidations = 1;}, pbo => {pbo.packStateFallbackReason = 'lost owner';}]) {
    const value = example(options); corrupt(value.pipeline.native.speedLab.pbo);
    assert.equal(passed(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8)), false);
  }
});

test('W bounded software fence fallback is counted explicitly and cannot masquerade as successful state reuse', () => {
  const options = PROFILES['gl-state'].options, value = example(options, .02, true), native = value.pipeline.native;
  const policy = {allowAsyncFallback: true, generated: false, renderer: 'ANGLE SwiftShader'};
  native.sharedReadback = {readbackCalls: 1, readbackBytes: 512};
  Object.assign(native.speedLab, {asyncReadbackUsed: false, asyncFallback: BOUNDED_FENCE_REASON});
  Object.assign(native.speedLab.pbo, {completed: false, fallbackReason: BOUNDED_FENCE_REASON,
    retrievedCalls: 0, retrievedBytes: 0, scratchBytesAllocated: 0, scratchBytesReused: 0, outputBytesAllocated: 0,
    waitMs: 505, polls: 101, stateQueryCalls: 6, ownedPackStateUsed: false, packStateCacheHits: 0, packStateQueriesAvoided: 0});
  assert.equal(passed(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8, policy)), true);
  const mechanism = mechanismUse(options, value.stats, value.geometry, 16, 8, policy);
  assert.equal(mechanism.actualFastPathUsed, false); assert.equal(mechanism.actualOwnedPackStateUsed, false);
  assert.equal(mechanism.acceptedAsyncFallback, true);
  assert.equal(passed(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8)), false);
  Object.assign(native.speedLab.pbo, {ownedPackStateUsed: true, packStateCacheHits: 1, packStateQueriesAvoided: 4, stateQueryCalls: 2});
  assert.equal(passed(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8, policy)), false);
});

test('X requires the full actual word scan of the paired masked image and exposes additive mechanism totals', () => {
  const options = PROFILES['word-compose'].options;
  for (const corrupt of [value => {value.stats.candidatePerformance.wordComparisonRequested = false;},
    value => {value.stats.candidatePerformance.wordComparisonUsed = false;}, value => {value.stats.candidatePerformance.wordComparedPixels--;},
    value => {value.stats.hasMask = false;}, value => {value.stats.fallbackReason = 'missing mask';}]) {
    const value = example(options); corrupt(value);
    assert.equal(passed(checkProtocol('candidate', options, value.stats, value.geometry, 16, 8)), false);
  }
  const rows = ['gl-state', 'word-compose'].map(name => {
    const selected = PROFILES[name].options, value = example(selected);
    return {mechanism: mechanismUse(selected, value.stats, value.geometry, 16, 8)};
  });
  const summary = summarizeMechanisms(rows);
  assert.equal(summary.actualOwnedPackStateCases, 1); assert.equal(summary.packStateQueriesAvoided, 4);
  assert.equal(summary.stateQueryCalls, 20); assert.equal(summary.actualWordComparisonCases, 1); assert.equal(summary.wordComparedPixels, 128);
});

test('region accounting requires the independent editable-minus-protected row band and actual byte totals', () => {
  const options=PROFILES.region.options;
  for(const corrupt of [value=>{value.pipeline.efficiencyLab.branchRegion.rectangle.y0++;},
    value=>{value.pipeline.efficiencyLab.branchRegion.readBytes=512;},
    value=>{value.pipeline.branchReadbackBytes=512;},value=>{value.stats.candidatePerformance.cpuReadbackBytes+=4;}]) {
    const value=example(options,.02);corrupt(value);
    assert.equal(passed(checkProtocol('candidate',options,value.stats,value.geometry,16,8)),false);
  }
  const value=example(options,.02);value.geometry.protection.protectedRects=[{x0:0,y0:0,x1:16,y1:8}];
  Object.assign(value.pipeline.efficiencyLab.branchRegion,{rectangle:null,readBytes:0});
  value.stats.candidatePerformance.cpuReadbackCalls--;value.stats.candidatePerformance.cpuReadbackBytes-=value.pipeline.branchReadbackBytes;
  value.pipeline.branchReadbackCalls=value.pipeline.branchReadbackBytes=0;
  assert.equal(passed(checkProtocol('candidate',options,value.stats,value.geometry,16,8)),true);
});

test('lens omission claims require a real omitted material on nonzero branch work', () => {
  const options=PROFILES.lens.options,value=example(options,.02);
  value.pipeline.efficiencyLab.branchLenses.omittedMaterials=0;
  assert.equal(checkProtocol('candidate',options,value.stats,value.geometry,16,8).lensesUsed,false);
});
test('async temple transfers cannot disappear from outer CPU accounting', () => {
  const options=PROFILES.combined.options,value=example(options,.02);
  value.stats.candidatePerformance.cpuReadbackCalls--;
  value.stats.candidatePerformance.cpuReadbackBytes-=512;
  assert.equal(checkProtocol('candidate',options,value.stats,value.geometry,16,8).totalCpuReads,false);
});
test('later nonzero temple pairs require actual earlier submission and real PBO retrieval', () => {
  for (const corrupt of [value=>{value.pipeline.efficiencyLab.branchSubmittedBeforeBeautyAwait=false;},
    value=>{value.pipeline.efficiencyLab.branchPbo.retrievedBytes=0;},
    value=>{value.pipeline.efficiencyLab.branchFallback='arbitrary';},
    value=>{value.pipeline.native.speedLab.pbo.scratchBytesReused=0;},
    value=>{value.pipeline.efficiencyLab.branchPbo.outputBytesAllocated=0;}]) {
    const options=PROFILES.combined.options,value=example(options,.02);corrupt(value);
    assert.equal(passed(checkProtocol('candidate',options,value.stats,value.geometry,16,8)),false);
  }
});
test('only explicitly counted bounded software temple fallbacks pass and never count as a fast path', () => {
  const options=PROFILES.combined.options,value=example(options,.02),efficiency=value.pipeline.efficiencyLab;
  const policy={allowAsyncFallback:true,generated:false,renderer:'ANGLE SwiftShader'};
  efficiency.asyncTemplesUsed=false;efficiency.branchFallback=BOUNDED_FENCE_REASON;
  Object.assign(efficiency.branchPbo,{completed:false,fallbackReason:BOUNDED_FENCE_REASON,retrievedCalls:0,retrievedBytes:0,waitMs:505,polls:101});
  value.pipeline.branchReadbackCalls=1;value.pipeline.branchReadbackBytes=512;
  assert.equal(passed(checkProtocol('candidate',options,value.stats,value.geometry,16,8,policy)),true);
  assert.equal(mechanismUse(options,value.stats,value.geometry,16,8,policy).actualFastPathUsed,false);
  assert.equal(mechanismUse(options,value.stats,value.geometry,16,8,policy).acceptedBranchFallback,true);
  for(const other of [{...policy,allowAsyncFallback:false},{...policy,generated:true},{...policy,renderer:'Intel D3D11'}])
    assert.equal(passed(checkProtocol('candidate',options,value.stats,value.geometry,16,8,other)),false);
});
