import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {cpus, platform, release} from 'node:os';
import {fileURLToPath} from 'node:url';
import {composeHairArmsFast, checkHairProtection, CompositionScratch} from '../runtime/experiments/performance-candidate/fast-compose.ts';

// Reproducible CPU-only function probe. Synthetic RGBA/geometry/category values
// deliberately exercise nonzero arm edits; this is not a camera or AR FPS run.
const width = 1280, height = 853, maskWidth = 256, maskHeight = 256;
const inside = (r, x, y) => x >= r.x0 && x < r.x1 && y >= r.y0 && y < r.y1;
const before = new Uint8ClampedArray(width * height * 4), background = before.slice();
const pair = {sourceIdentity: '1'.repeat(64), detectionIdentity: '2'.repeat(64), eyewearModel: 'amber-horizon'};
const labels = ['background', 'hair'], category = new Uint8Array(maskWidth * maskHeight), confidence = new Float32Array(category.length).fill(.72);
const protection = {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
  protectedRects: [{x0: 420, y0: 295, x1: 621, y1: 393}, {x0: 640, y0: 295, x1: 841, y1: 393}],
  editableRects: [{x0: 170, y0: 320, x1: 620, y1: 431}, {x0: 640, y0: 320, x1: 1110, y1: 431}]};
const noseRoi = {x0: 597, y0: 330, x1: 674, y1: 474};
for (let y = 0; y < maskHeight; y++) for (let x = 0; x < maskWidth; x++)
  category[y * maskWidth + x] = Number((x < 90 || x > 165) && y > 40 && y < 170 && (x + y) % 23 !== 0);
for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
  const offset = (y * width + x) * 4;
  const rgba = [(x + y) % 256, (3 * x + y) % 256, (x + 7 * y) % 256, 255];
  before.set(rgba, offset); background.set(rgba, offset);
  if ([...protection.protectedRects, ...protection.editableRects, noseRoi].some(rect => inside(rect, x, y)) && (x + y) % 11 !== 0)
    before.set([142, 47, 37, (x + 2 * y) % 13 === 0 ? 201 : 255], offset);
}
const input = {width, height, before, background, pair, geometryPair: {...pair}, protection, noseRoi,
  expectedModel: {id: 'synthetic-pinned-model', sha256: '3'.repeat(64), labels, hairIndex: 1},
  mask: {...pair, model: 'synthetic-pinned-model', modelSHA256: '3'.repeat(64), categorySHA256: '4'.repeat(64), confidenceSHA256: '5'.repeat(64),
    labels, hairIndex: 1, width: maskWidth, height: maskHeight, category, confidence}};
const scratch = {old: new CompositionScratch(), next: new CompositionScratch()};
const compose = mode => composeHairArmsFast(input,
  {cpuCompose: mode === 'next', collectWeights: false, collectEligibleResidualIndices: true, scratch: scratch[mode]});
const old = compose('old'), next = compose('next');
const {cpuComposition, ...pixelsAndChecks} = next;
assert.equal(old.fallbackReason, null); assert.deepEqual(pixelsAndChecks, old);
assert.ok(old.statistics.changedPixels > 10000 && old.statistics.featheredPixels > 100);
const expectedChecks = checkHairProtection(input, old.pixels, old.regions);
assert.deepEqual(checkHairProtection(input, next.pixels, next.regions), expectedChecks);
assert.ok(Object.values(expectedChecks).every(check => check.changedPixels === 0));
for (let index = 0; index < 40; index++) {
  const mode = index % 2 ? 'next' : 'old', result = compose(mode);
  checkHairProtection(input, result.pixels, result.regions);
}
const blocks = [], iterations = 32;
for (const mode of ['old', 'next', 'next', 'old', 'old', 'next', 'next', 'old']) {
  let composeMs = 0, fullAuditMs = 0, changedPixels = 0;
  for (let index = 0; index < iterations; index++) {
    const started = performance.now(), result = compose(mode), composed = performance.now();
    const checks = checkHairProtection(input, result.pixels, result.regions), checked = performance.now();
    composeMs += composed - started; fullAuditMs += checked - composed;
    assert.equal(result.fallbackReason, null); assert.deepEqual(checks, expectedChecks);
    changedPixels += result.statistics.changedPixels;
  }
  blocks.push({mode, iterations, composeMs: composeMs / iterations, fullAuditMs: fullAuditMs / iterations,
    composeAndAuditMs: (composeMs + fullAuditMs) / iterations, changedPixelsPerIteration: changedPixels / iterations});
}
const here = path.dirname(fileURLToPath(import.meta.url));
const sources = {};
for (const relative of ['compose-round2-bench.mjs', '../runtime/experiments/performance-candidate/fast-compose.ts'])
  sources[relative] = createHash('sha256').update(await readFile(path.resolve(here, relative))).digest('hex');
const report = {schema: 'fps-round2-compose-microbench-v1', createdAt: new Date().toISOString(),
  scope: 'CPU-only synthetic function timings. No inference, camera, GPU, presentation, motion, wearer fit or AR FPS measurement.',
  environment: {node: process.version, platform: platform(), osRelease: release(), cpu: cpus()[0]?.model},
  fixture: {width, height, maskWidth, maskHeight, confidenceEntriesValidatedEveryIteration: confidence.length,
    statistics: old.statistics, checks: expectedChecks, candidateMechanism: cpuComposition}, sources, blocks,
  summary: Object.fromEntries(['old', 'next'].map(mode => {
    const rows = blocks.filter(block => block.mode === mode), average = key => rows.reduce((sum, row) => sum + row[key], 0) / rows.length;
    return [mode, {iterations: rows.length * iterations, composeMs: average('composeMs'), fullAuditMs: average('fullAuditMs'), composeAndAuditMs: average('composeAndAuditMs')}];
  }))};
const outputArg = process.argv.find(value => value.startsWith('--output='));
const output = outputArg ? path.resolve(outputArg.slice('--output='.length)) : path.join(here, 'output', `round2-compose-microbench-${Date.now()}.json`);
await mkdir(path.dirname(output), {recursive: true}); await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, {flag: 'wx'});
console.log(JSON.stringify({output, scope: report.scope, statistics: old.statistics, summary: report.summary}, null, 2));
