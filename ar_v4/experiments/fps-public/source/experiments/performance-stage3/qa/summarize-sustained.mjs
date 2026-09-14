import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const filename = path.resolve(process.argv[2] ?? ''), root = path.resolve('experiments/performance-stage3/qa/output');
assert.ok(path.relative(root, filename) && !path.relative(root, filename).startsWith('..'));
const bytes = await fs.readFile(filename), report = JSON.parse(bytes);
assert.equal(report.schema, 'ar-performance-stage3-sustained-synthetic-v1');
assert.equal(report.complete, true); assert.equal(report.passed, true);
assert.deepEqual(report.selectedPipelines, ['test2', 'test3']); assert.equal(report.sessions.length, 4);
const groups = [], rowsByPipeline = {test2: [], test3: []}, seconds = {test2: 0, test3: 0};
const stages = ['sourceDrawMs','detectorDrawMs','sourceReadbackMs','sourceHashMs','faceBitmapMs','faceRequestWallMs',
  'faceInferenceMs','prepareMs','finishMs','renderMs','schedulerWaitMs','composeMs','continuityMs','finalChecksMs','publishMs'];
const distribution = input => {
  const values = input.filter(Number.isFinite).sort((a,b) => a-b), n = values.length;
  return n ? {n, median: n%2 ? values[(n-1)/2] : (values[n/2-1]+values[n/2])/2, p95: values[Math.ceil(n*.95)-1], maximum: values[n-1]} : null;
};
for (const session of report.sessions) {
  assert.equal(session.segments.length, 2); const implementations = {};
  for (const segment of session.segments) {
    assert.ok(segment.measuredDurationMs >= 30000 && segment.warmup.finishedAtMs-segment.warmup.firstPublishedAtMs >= 5000);
    assert.ok(segment.rows.length > 1 && !segment.serialGaps.length && !segment.failures.length && !segment.unexpectedRows.length);
    const s = segment.summary, native = s.native;
    if (segment.pipeline === 'test3') assert.equal(segment.gpuMechanism.passed, true);
    implementations[segment.pipeline] = {frames:s.frames,seconds:s.measuredSeconds,fps:s.publicationsPerSecond,
      frameIntervalMs:s.frameIntervalsMs,frameAgeMs:s.frameAgeAtPublicationMs,trackingCoverage:s.trackedFrames/s.frames,
      hairCoverage:s.hairCoverageAmongTracked,changedFrames:s.framesWithActualHairPixelChanges,
      videoDeliveryFps:s.observedVideoDeliveryFps,syntheticDrawFps:s.syntheticDrawsPerSecond,
      stallsOver250ms:s.stallsOver250ms,longTaskCount:s.longTaskCount,
      gpuMechanism:segment.gpuMechanism ?? null,
      stages:Object.fromEntries(stages.map(key => [key,s.stages[key] ?? null])),
      transfers:Object.fromEntries(Object.entries(native).filter(([key]) => /readback|uploadBytes|captureCalls|gpuCaptureMs|flagsSubmitMs|flagsReadbackMs|referenceReduceMs|composeSubmitMs|auditSubmitMs|auditReduceMs|publishSubmitMs|stateSaveRestoreMs/i.test(key))),
      delegates:{face:s.faceDelegates,hair:s.hairDelegates},renderers:s.actualRendererStrings};
    rowsByPipeline[segment.pipeline].push(...segment.rows); seconds[segment.pipeline]+=s.measuredSeconds;
  }
  const a=implementations.test2,b=implementations.test3;
  groups.push({eyewear:session.eyewear,hair:session.hair,order:session.order,implementations,
    fpsGainPercent:(b.fps/a.fps-1)*100,medianFrameAgeReductionPercent:(1-b.frameAgeMs.median/a.frameAgeMs.median)*100,
    p95FrameAgeReductionPercent:(1-b.frameAgeMs.p95/a.frameAgeMs.p95)*100});
}
const aggregate = Object.fromEntries(Object.entries(rowsByPipeline).map(([pipeline,rows]) => [pipeline,{frames:rows.length,seconds:seconds[pipeline],
  fps:rows.length/seconds[pipeline],frameAgeMs:distribution(rows.map(row=>row.publishedAtMs-row.capturedAtMs)),
  trackedFrames:rows.filter(row=>row.hasFace).length,maskedFrames:rows.filter(row=>row.hasMask).length,
  stages:Object.fromEntries(stages.map(key=>[key,distribution(rows.map(row=>row[key]))]))}]));
const result = {schema:'ar-stage3-sustained-summary-v1',createdAt:new Date().toISOString(),
  report:{path:filename,sha256:createHash('sha256').update(bytes).digest('hex')},groups,aggregate,
  frozenFiles:report.frozenInputs.length,compiledFiles:report.productionBuildAfter?.files,
  servedEntries:report.servedEntries?.length,preservedFiles:report.preservationAfter.uniqueFiles,
  limits:report.limits};
const target=path.join(path.dirname(filename),'summary.json'); await fs.writeFile(target,JSON.stringify(result,null,2),{flag:'wx'});
console.log(JSON.stringify({path:target,groups:groups.map(g=>({eyewear:g.eyewear,hair:g.hair,order:g.order,
  test2:g.implementations.test2.fps,test3:g.implementations.test3.fps,gainPercent:g.fpsGainPercent,
  intervalsP95:[g.implementations.test2.frameIntervalMs.p95,g.implementations.test3.frameIntervalMs.p95],
  ageP95:[g.implementations.test2.frameAgeMs.p95,g.implementations.test3.frameAgeMs.p95],
  gpu:g.implementations.test3.gpuMechanism})),aggregate},null,2));
