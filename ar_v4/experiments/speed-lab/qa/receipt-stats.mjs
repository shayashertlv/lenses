// Per-stage medians from an ar_v4 timing receipt (speed-lab or efficiency-lab schema).
// Usage: node receipt-stats.mjs <file.json> [pipeline] [warmSkipMs]
import fs from 'node:fs';
const [file, wantPipeline, warmSkipArg] = process.argv.slice(2);
const warmSkip = Number(warmSkipArg ?? 10000);
const j = JSON.parse(fs.readFileSync(file, 'utf8'));
const all = j.samples ?? [];
const pipelines = [...new Set(all.map(s => s.pipeline))];
const dist = v => { const a = v.filter(Number.isFinite).sort((x, y) => x - y); if (!a.length) return null;
  const q = p => a[Math.min(a.length - 1, Math.ceil(a.length * p) - 1)];
  const med = a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2;
  return { n: a.length, med: +med.toFixed(2), p95: +q(0.95).toFixed(1), max: +a[a.length - 1].toFixed(1), mean: +(a.reduce((s, x) => s + x, 0) / a.length).toFixed(2) }; };
const fmt = d => d ? `n=${d.n} med=${d.med} p95=${d.p95} max=${d.max}` : '—';
const N = (s, k) => { const v = s.native?.[k]; return typeof v === 'number' ? v : null; };
for (const pipeline of (wantPipeline ? [wantPipeline] : pipelines)) {
  const rows0 = all.filter(s => s.pipeline === pipeline);
  if (!rows0.length) continue;
  // warm subset: drop the first warmSkip ms after the first row of this pipeline segment (contiguous segments)
  const first = rows0[0].publishedAtMs;
  const rows = rows0.filter(s => s.publishedAtMs - first > warmSkip);
  const intervals = rows.slice(1).map((s, i) => s.publishedAtMs - rows[i].publishedAtMs);
  const dur = rows.at(-1).publishedAtMs - rows[0].publishedAtMs;
  console.log(`\n=== pipeline ${pipeline}: ${rows0.length} rows, warm ${rows.length} rows, ${(dur / 1000).toFixed(1)} s, ${((rows.length - 1) * 1000 / dur).toFixed(2)} fps, size ${rows[0].sourceWidth}x${rows[0].sourceHeight}, delegates ${rows[0].faceDelegate}/${rows[0].hairDelegate}`);
  console.log(' interval        ', fmt(dist(intervals)), ' gaps>100:', intervals.filter(x => x > 100).length, ' gaps>200:', intervals.filter(x => x > 200).length);
  console.log(' age (totalMs)   ', fmt(dist(rows.map(s => s.totalMs))));
  console.log(' hasFace', rows.filter(s => s.hasFace).length, ' hasMask', rows.filter(s => s.hasMask).length, ' changedPixels>0', rows.filter(s => s.changedPixels > 0).length, ' fallback', rows.filter(s => s.fallback).length);
  const stages = ['schedulerWaitMs', 'sourceDrawMs', 'sourceReadbackMs', 'sourceHashMs', 'detectorDrawMs', 'faceBitmapMs', 'faceRequestWallMs', 'faceInferenceMs', 'faceWorkerMs', 'faceTransportSchedulingMs', 'detectionHashMs', 'prerequisitesWaitMs', 'prepareMs', 'hairWaitMs', 'finishMs', 'renderMs', 'hairInferenceMs', 'hairExtractionMs', 'cleanCameraMs', 'composeMs', 'continuityMs', 'finalChecksMs', 'publishMs'];
  for (const k of stages) console.log(' ' + k.padEnd(26), fmt(dist(rows.map(s => s[k]))));
  const nat = ['nativePipeline.native.setupMs', 'nativePipeline.native.visibilitySubmitMs', 'nativePipeline.native.beautySubmitMs', 'nativePipeline.native.cleanSubmitMs', 'nativePipeline.native.speedLab.pbo.waitMs', 'nativePipeline.native.speedLab.pbo.extractMs', 'nativePipeline.native.speedLab.pbo.polls', 'nativePipeline.native.totalMs', 'nativePipeline.nativePresentMs', 'nativePipeline.branchPresentMs', 'nativePipeline.branchReadbackMs', 'nativePipeline.protectionMs', 'nativePipeline.compositionMs', 'nativePipeline.speedLab.prewarmMs', 'pump.inputWaitMs', 'pump.hairAdmissionWaitMs', 'sourceCopyMs', 'input.hashCopyMs', 'input.hashDigestWallMs', 'input.hairBitmapMs', 'hairCategory.retrievalMs', 'hairCategory.conversionMs', 'hairCategory.copyMs', 'publication.prePrepareMs'];
  for (const k of nat) { const d = dist(rows.map(s => N(s, k))); if (d) console.log(' ' + k.replace('nativePipeline.', 'np.').padEnd(40), fmt(d)); }
  const branch = rows.filter(s => N(s, 'nativePipeline.branchReadbackCalls') > 0);
  const noBranch = rows.filter(s => N(s, 'nativePipeline.branchReadbackCalls') === 0 && s.hasFace);
  console.log(` branch frames ${branch.length}/${rows.length} (${(100 * branch.length / rows.length).toFixed(1)}%)`);
  const idx = new Map(rows.map((s, i) => [s, i]));
  const intervalOf = s => { const i = idx.get(s); return i > 0 ? s.publishedAtMs - rows[i - 1].publishedAtMs : null; };
  console.log('  interval on branch rows   ', fmt(dist(branch.map(intervalOf))), ' prepare', fmt(dist(branch.map(s => s.prepareMs))));
  console.log('  interval on no-branch rows', fmt(dist(noBranch.map(intervalOf))), ' prepare', fmt(dist(noBranch.map(s => s.prepareMs))));
  const edits = rows.filter(s => s.changedPixels > 0), noEdits = rows.filter(s => s.hasMask && s.changedPixels === 0);
  console.log('  finish on edit rows', fmt(dist(edits.map(s => s.finishMs))), ' finish on mask-no-edit rows', fmt(dist(noEdits.map(s => s.finishMs))));
  const hairMode = [...new Set(rows.map(s => s.native?.['hairCategory.path'] ?? s.native?.['hairCategory.mode']).filter(Boolean))];
  if (hairMode.length) console.log('  hairCategory path/mode:', hairMode.join(','));
  const pollCount = dist(rows.map(s => N(s, 'nativePipeline.native.speedLab.pbo.polls')));
  if (pollCount) console.log('  pbo polls', fmt(pollCount));
}
if (Array.isArray(j.events)) {
  const kinds = {}; for (const e of j.events) kinds[e.type ?? e.kind ?? 'unknown'] = (kinds[e.type ?? e.kind ?? 'unknown'] ?? 0) + 1;
  console.log('\nevents:', kinds);
  const lt = j.events.filter(e => /long/i.test(e.type ?? e.kind ?? ''));
  if (lt.length) console.log(' long tasks:', lt.length, 'total ms', lt.reduce((s, e) => s + (e.durationMs ?? e.duration ?? 0), 0).toFixed(0), 'max', Math.max(...lt.map(e => e.durationMs ?? e.duration ?? 0)).toFixed(0));
  console.log(' sample event:', JSON.stringify(j.events[0]).slice(0, 300));
}
