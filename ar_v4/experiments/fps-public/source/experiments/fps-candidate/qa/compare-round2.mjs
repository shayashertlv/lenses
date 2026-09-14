/** Aggregate only within one source size, fixture, glasses and hair-model run.
 * Pooled FPS uses publication intervals inside each session, never startup gaps. */
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';

const median = values => {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b), n = sorted.length;
  return n ? n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2 : null;
};
const percentile = (values, q) => {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  return sorted.length ? sorted[Math.ceil(sorted.length * q) - 1] : null;
};
const group = runs => {
  const rows = runs.flatMap(run => run.rows ?? []);
  const intervals = runs.flatMap(run => (run.rows ?? []).slice(1).map((row, i) => row.publishedAtMs - run.rows[i].publishedAtMs));
  const elapsed = intervals.reduce((sum, value) => sum + value, 0);
  return {sessions: runs.length, frames: rows.length, fps: elapsed > 0 ? intervals.length * 1000 / elapsed : null,
    perSessionFps: runs.map(run => run.summary.processedFps), ageP95: percentile(rows.map(row => row.totalMs), .95),
    intervalP95: percentile(intervals, .95), tracked: rows.filter(row => row.hasFace).length,
    masked: rows.filter(row => row.hasFace && row.hasMask).length, visibleHairEdits: rows.filter(row => row.changedPixels > 0).length,
    stageMedians: Object.fromEntries(['composeMs', 'finalChecksMs', 'finishMs', 'prepareMs', 'faceRequestWallMs', 'hairExtractionMs']
      .map(key => [key, median(rows.map(row => row[key]))])),
    mechanisms: Object.fromEntries(['fps.cpuCompose.used', 'fps.bookkeeping.used'].map(key => [key, rows.filter(row => row.native?.[key] === true).length])),
    bookkeepingMedians: Object.fromEntries(['publicationWorkMs', 'statsCollectionWorkMs', 'statsReadsAvoided']
      .map(key => [key, median(rows.map(row => row.native?.['fps.bookkeeping.' + key]))]))};
};
const results = [];
for (const filename of process.argv.slice(2)) {
  const report = JSON.parse(await fs.readFile(filename, 'utf8'));
  assert.equal(report.controls, false, 'Use timing receipts, not lifecycle tests.');
  const workloads = {};
  for (const {eyewear, hair} of report.matrix) {
    const runs = report.runs.filter(run => run.eyewear === eyewear && run.hair === hair && run.rows?.length);
    const modes = Object.fromEntries([...new Set(runs.map(run => run.variant))].map(mode => [mode, group(runs.filter(run => run.variant === mode))]));
    const baseline = modes.g?.fps;
    for (const value of Object.values(modes)) value.changeFromPooledGPercent = baseline && value.fps ? (value.fps / baseline - 1) * 100 : null;
    workloads[eyewear + '/' + hair] = modes;
  }
  results.push({file: filename, complete: report.complete, passed: report.passed, build: report.productionBuild?.fingerprint,
    size: {width: report.width, height: report.height}, fixture: report.fixture ?? report.fixtureSHA256,
    order: report.order, workloads});
}
console.log(JSON.stringify(results, null, 2));
