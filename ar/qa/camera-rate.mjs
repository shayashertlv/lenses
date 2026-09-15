/** Camera delivery rate versus processed rate, from any timing export (the page's "Download timings", a measurement
 *  report, or a harness report). Each published row carries the camera's presented-frame counter
 *  (requestVideoFrameCallback metadata.presentedFrames) and media time, so the camera's own rate over a session is
 *  (last − first presented frames) / elapsed, independent of how many frames the pipeline processed. Read this before
 *  judging any fps figure: a camera delivering 15 fps in dim light caps every pipeline at 15.
 *    node qa/camera-rate.mjs <export.json> [more.json ...] */
import fs from 'node:fs';
const files = process.argv.slice(2);
if (!files.length) {console.error('usage: node qa/camera-rate.mjs <export.json>...'); process.exit(1);}
const dist = values => {const a = values.filter(Number.isFinite).sort((x, y) => x - y); if (!a.length) return null;
  const q = p => a[Math.min(a.length - 1, Math.ceil(a.length * p) - 1)];
  return {n: a.length, med: a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2, p95: q(0.95), max: a[a.length - 1]};};
const f = (x, d = 1) => x === null || x === undefined || Number.isNaN(x) ? '—' : (+x).toFixed(d);
const N = (s, k) => {const v = s?.native?.[k]; return typeof v === 'number' ? v : null;};
const sessions = [];
for (const file of files) {
  const j = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (Array.isArray(j.samples)) {
    const byId = new Map();
    for (const s of j.samples) {if (!byId.has(s.sessionId)) byId.set(s.sessionId, []); byId.get(s.sessionId).push(s);}
    for (const [id, rows] of byId) sessions.push({file, label: id.slice(0, 8), rows});
  }
  const measured = j.measured ?? j.report?.measured;
  if (Array.isArray(measured)) for (const entry of measured) if (entry.rows?.length) sessions.push({file, label: `#${entry.index + 1}`, rows: entry.rows});
}
console.log('session | rows | processed fps | camera fps (presented counter) | camera interval med/p95 (media time) | rows per camera frame | camera setting | pump input wait med | age med');
for (const {file, label, rows: all} of sessions) {
  const rows = [...all].sort((a, b) => a.publishedAtMs - b.publishedAtMs);
  if (rows.length < 5) continue;
  const elapsed = rows.at(-1).publishedAtMs - rows[0].publishedAtMs;
  const processedFps = (rows.length - 1) * 1000 / elapsed;
  const counted = rows.filter(r => Number.isFinite(r.videoPresentedFrames));
  const cameraFps = counted.length > 1 ? (counted.at(-1).videoPresentedFrames - counted[0].videoPresentedFrames) * 1000 / (counted.at(-1).capturedAtMs - counted[0].capturedAtMs) : null;
  const media = rows.map(r => r.videoMediaTime).filter(Number.isFinite);
  const mediaSteps = media.slice(1).map((t, i) => (t - media[i]) * 1000).filter(x => x > 0);
  const frameSteps = counted.slice(1).map((r, i) => r.videoPresentedFrames - counted[i].videoPresentedFrames).filter(x => x > 0);
  const perCameraFrame = frameSteps.length ? frameSteps.filter(x => x === 1).length / frameSteps.length : null;
  const setting = rows[0].cameraSettingFps ?? null;
  const inputWait = dist(rows.map(r => N(r, 'pump.inputWaitMs')));
  const age = dist(rows.map(r => r.totalMs));
  const ms = dist(mediaSteps);
  console.log(`${label} | ${rows.length} | ${f(processedFps, 2)} | ${f(cameraFps, 2)} | ${ms ? `${f(ms.med)}/${f(ms.p95)} ms` : '—'} | ${perCameraFrame === null ? '—' : `${f(100 * perCameraFrame, 0)}% consecutive, camera-frame steps med ${f(dist(frameSteps)?.med, 0)}`} | ${setting ?? '—'} | ${f(inputWait?.med)} ms | ${f(age?.med, 0)} ms  · ${file}`);
}
