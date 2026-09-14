/** Local-only immutable source selection for production inference measurements. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const archives = {
  generated: '.recovery/hair-angle-review-2026-09-09/runs/2026-09-09T08-18-33.789Z/report.json',
  recorded: '.recovery/hair-live-performance-2026-09-08/recorded-runs/2026-09-08T19-30-46.916Z/report.json',
};
export async function loadFixture(selection = 'face-a') {
  const frozen = new Map();
  const read = async (filename, expected) => {
    const resolved = path.resolve(filename), bytes = await fs.readFile(resolved), digest = sha(bytes);
    if (expected) {
      assert.equal(digest, expected.sha256, `Preserved source artifact changed: ${resolved}`);
      if (expected.bytes !== undefined) assert.equal(bytes.length, expected.bytes);
    }
    frozen.set(resolved, digest); return bytes;
  };
  const verify = async () => {
    for (const [filename, digest] of frozen) assert.equal(sha(await fs.readFile(filename)), digest, `Preserved fixture changed: ${filename}`);
    return {filesVerified: frozen.size, unchanged: true};
  };
  if (selection === 'face-a') {
    const filename = 'tests/fixtures/face-a.jpg', bytes = await read(filename);
    return {bytes, mimeType: 'image/jpeg', width: 1280, height: 720, verify,
      provenance: {selection, kind: 'checked-in static fixture', path: filename, sha256: sha(bytes),
        cameraSizing: 'Legacy default stretches this image to 1280x720; explicit width/height override this.',
        inference: 'Face and hair inference run anew on canvas-camera frames; no archived detections or masks are injected.'}};
  }
  const [kind, id, extra] = selection.split(':');
  assert.ok(archives[kind] && id && !extra, 'Use face-a, generated:<archive source id>, or recorded:<archive source id>.');
  const reportBytes = await read(archives[kind]), prior = JSON.parse(reportBytes);
  assert.equal(prior.complete, true, 'Source archive must be complete.');
  const source = prior.images.find(image => image.id === id); assert.ok(source, `Unknown ${kind} source: ${id}`);
  const artifact = kind === 'generated' ? source.capture : source.original;
  const contained = filename => {
    const relative = path.relative(path.resolve('.recovery'), path.resolve(filename));
    assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative), 'Archived source must stay inside recovery.');
  };
  contained(artifact.path); const bytes = await read(artifact.path, artifact);
  // These original receipts establish the historical exact pairing only. Live
  // measurement intentionally reruns inference and records its actual workload.
  for (const field of ['detectionFile', 'serializedDetection', 'canonicalDetection']) if (source[field]) {
    contained(source[field].path); await read(source[field].path, source[field]);
  }
  return {bytes, mimeType: artifact.mimeType ?? (path.extname(artifact.path) === '.jpg' ? 'image/jpeg' : 'image/png'),
    width: source.width, height: source.height, verify,
    provenance: {selection, kind: kind === 'generated' ? 'previously generated static portrait' : 'previously recorded static source frame',
      title: source.title, path: artifact.path, sha256: artifact.sha256, archive: {path: archives[kind], sha256: sha(reportBytes)},
      originalSize: {width: source.width, height: source.height}, archivedDetectionSHA256: source.detectionSHA256,
      archivedChangedPixels: prior.cases.filter(row => row.id === id).map(row => ({eyewear: row.eyewearModel, hair: row.hairModel, changedPixels: row.stats.changedPixels})),
      inference: 'Face and hair inference run anew on canvas-camera frames; archived detections/masks are verified for provenance but never injected.',
      privacy: 'Input is read in place and routed only inside this local disposable browser context. Timing receipts contain no image bytes or detection coordinates.',
      limits: 'Repeating one preserved still is not wearer motion, a physical camera, a personal scan or mobile thermal evidence.'}};
}
