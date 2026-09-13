import assert from 'node:assert/strict';
import {test} from 'node:test';
import {registerHooks} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';

const wrapper = new URL('../renderer.ts', import.meta.url), lower = new URL('../temples/renderer.ts', import.meta.url);
const hooks = registerHooks({load(url, context, next) {
  if (url === lower.href) return {format: 'module', shortCircuit: true, source: 'export class TryOnRenderer {}'};
  if (url === wrapper.href) return {format: 'module', shortCircuit: true,
    source: transformSync(fileURLToPath(url), readFileSync(new URL(url), 'utf8')).code};
  return next(url, context);
}});
const {LiveHairRenderer} = await import(wrapper.href); hooks.deregister();

class TestImageData {
  constructor(data, width, height) { this.data = data; this.width = width; this.height = height; this.colorSpace = 'srgb'; }
}
class TestCanvas {
  width = 32; height = 24;
  bytes = new Uint8ClampedArray(32 * 24 * 4).fill(37);
  publications = 0;
  context = {drawImage: canvas => {this.bytes = canvas.bytes.slice();},
    getImageData: (_x, _y, w, h) => new TestImageData(this.bytes.slice(), w, h),
    putImageData: image => {this.bytes = image.data.slice(); this.publications++;},
    clearRect: () => {this.bytes.fill(0);}};
  getContext() { return this.context; }
  toDataURL() { return `data:image/png;base64,${Buffer.from(this.bytes).toString('base64')}`; }
}
function fixture(t) {
  const previous = [globalThis.document, globalThis.ImageData];
  globalThis.document = {createElement: () => new TestCanvas()}; globalThis.ImageData = TestImageData;
  t.after(() => { [globalThis.document, globalThis.ImageData] = previous; });
  const display = new TestCanvas(), source = new TestCanvas();
  const protection = {method: 'temple-optics-copy-v1', width: 32, height: 24, marginPx: 4,
    protectedRects: [{x0: 10, y0: 7, x1: 16, y1: 11}, {x0: 16, y0: 7, x1: 22, y1: 11}],
    editableRects: [{x0: 2, y0: 7, x1: 16, y1: 18}, {x0: 16, y0: 7, x1: 30, y1: 18}]};
  const accepted = {nativeSamples: 4, eyewear: {id: 'amber-horizon', offsetCm: 0}, fail: false,
    captureSnapshot: {protection, rearDrop: {dropM: 0}, surfacePositions: new Float32Array([1, 2, 3])},
    stageTimings: {baselineReadbackCalls: 0, baselineReadbackBytes: 0, branchReadbackCalls: 0, branchReadbackBytes: 0, efficiencyLab: {}},
    setPreparationHairEnabled() {}, setFrameOptions() {}, dispose() {},
    present(frame) {
      if (this.fail) throw new Error('Injected preparation failure.');
      this.ownedCameraPixels = new TestImageData(frame.bytes.slice(), frame.width, frame.height);
      this.ownedPixels = new TestImageData(frame.bytes.slice(), frame.width, frame.height);
      for (let y = 7; y < 18; y++) for (let x = 2; x < 30; x++) this.ownedPixels.data[(y * 32 + x) * 4] = 150;
      return true;
    },
  };
  const renderer = new LiveHairRenderer(display, new TestCanvas(), accepted);
  t.after(() => renderer.dispose());
  const detection = {matrix: Array.from({length: 16}, (_, i) => Number(i % 5 === 0)),
    landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0}))};
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: 'amber-horizon'};
  const model = {id: 'hair-only', sha256: '3'.repeat(64), labels: ['background', 'hair'], hairIndex: 1};
  const mask = {...pair, model: model.id, modelSHA256: model.sha256, labels: model.labels, hairIndex: 1,
    width: 32, height: 24, category: new Uint8Array(32 * 24).fill(1), categorySHA256: '4'.repeat(64), outputMode: 'category-only'};
  const present = () => renderer.present(source, detection, mask, pair, model, {options: {reviewCompose: true}});
  return {renderer, display, source, accepted, detection, pair, model, mask, present};
}

test('Renderer keeps its output leased through Hold/export and reuses only after dropping the previous ImageData', async t => {
  const f = fixture(t); await f.present();
  assert.equal(f.renderer.stats.hasMask, true, f.renderer.stats.fallbackReason); assert.ok(f.renderer.stats.changedPixels > 0);
  const lease = f.renderer.afterOutputLease, firstBuffer = f.renderer.after.data.buffer;
  const report = f.renderer.exportDiagnostic(), held = f.renderer.copyHeldInput(), stats = f.renderer.stats;
  assert.equal(lease.released, false); assert.equal(stats.candidatePerformance.reviewCompose.outputBufferReused, false);
  assert.equal(stats.candidatePerformance.reviewCompose.finalAuditScannedPixels, 32 * 24);
  const published = f.display.bytes.slice();
  f.renderer.selectVariant('accepted'); f.renderer.selectVariant('hair'); assert.deepEqual(f.display.bytes, published);
  f.source.bytes.fill(71); await f.present();
  assert.equal(lease.released, true); assert.equal(f.renderer.after.data.buffer, firstBuffer);
  assert.equal(f.renderer.stats.candidatePerformance.reviewCompose.outputBufferReused, true);
  assert.equal(f.renderer.stats.candidatePerformance.reviewCompose.outputBufferBytesAllocated, 0);
  assert.equal(report.hairPngDataUrl, `data:image/png;base64,${Buffer.from(published).toString('base64')}`);
  assert.equal(held.source.bytes[0], 37); assert.equal(stats.candidatePerformance.reviewCompose.outputBufferReused, false);
});

test('Renderer releases failed composition and cancellation ownership, then retries without reviving old pixels', async t => {
  const f = fixture(t); await f.present(); const firstLease = f.renderer.afterOutputLease;
  f.mask.sourceSHA256 = '9'.repeat(64); await f.present();
  assert.equal(firstLease.released, true); assert.equal(f.renderer.stats.maskStatus, 'rejected');
  assert.equal(f.renderer.compositionOutputPool.retainedLeaseCount, 0); assert.equal(f.renderer.afterOutputLease, null);
  f.mask.sourceSHA256 = f.pair.sourceSHA256; await f.present();
  assert.equal(f.renderer.stats.maskStatus, 'ready'); assert.equal(f.renderer.compositionOutputPool.retainedLeaseCount, 1);
  f.accepted.fail = true;
  await assert.rejects(f.present(), /Injected preparation failure/);
  assert.equal(f.renderer.compositionOutputPool.retainedLeaseCount, 0); assert.equal(f.renderer.stats, null);
  f.accepted.fail = false; await f.present(); const finalLease = f.renderer.afterOutputLease;
  f.renderer.dispose(); assert.equal(finalLease.released, true); assert.equal(f.renderer.finish(f.mask), false);
  assert.equal(f.renderer.compositionOutputPool.pooledBufferCount, 0); assert.equal(f.renderer.exportDiagnostic(), null);
});
