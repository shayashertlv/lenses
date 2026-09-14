import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {test} from 'node:test';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import type {HairMask, PairIdentity, HairModelContract, HeldHairInput} from '../hair-live-preview/renderer.ts';
import type {LiveVariant} from '../hair-live-preview/ownership.ts';
import type {ComparisonRenderer as Comparison} from './comparison-renderer.ts';

// Load the real adapter while replacing only its expensive rendering factories.
// This exercises orchestration/failure ownership; it makes no WebGL appearance claim.
const adapterUrl = new URL('./comparison-renderer.ts', import.meta.url).href;
const currentUrl = new URL('../hair-live-preview/renderer.ts', import.meta.url).href;
const testUrl = new URL('./renderer.ts', import.meta.url).href;
const eyewearUrl = new URL('../../references/perfect-temples/src/render/eyewear.ts', import.meta.url).href;
const factorySymbol = Symbol.for('performance-comparison-ownership-factories');
const hooks = registerHooks({load(url, context, nextLoad) {
  if (url === adapterUrl) {
    const compiled = transformSync(fileURLToPath(url), readFileSync(new URL(url), 'utf8'));
    if (compiled.errors.length) throw new Error(JSON.stringify(compiled.errors));
    return {format: 'module', shortCircuit: true, source: compiled.code};
  }
  if (url === currentUrl || url === testUrl) return {format: 'module', shortCircuit: true,
    source: `export class LiveHairRenderer { static create(...args) {
      return globalThis[Symbol.for('performance-comparison-ownership-factories')].${url === currentUrl ? 'current' : 'test'}(...args);
    } }`};
  if (url === eyewearUrl) return {format: 'module', shortCircuit: true, source: "export const DEFAULT_EYEWEAR_ID = 'amber-horizon';"};
  return nextLoad(url, context);
}});
const {ComparisonRenderer} = await import(adapterUrl) as typeof import('./comparison-renderer.ts');
hooks.deregister();

class StubCanvas {
  width = 2; height = 2;
  pixels = new Uint8ClampedArray(16).fill(37);
  clone(): StubCanvas { const copy = new StubCanvas(); copy.width = this.width; copy.height = this.height; copy.pixels = this.pixels.slice(); return copy; }
}
interface OwnedInput {source: StubCanvas; detection: Detection; pair: PairIdentity; expectedModel: HairModelContract;}
class StubRenderer {
  prepares = 0; finishes = 0; publishes = 0; disposals = 0;
  failPrepare = false; failFinish = false; failExport = false;
  readonly nativeSamples = 4;
  readonly eyewear = {id: 'amber-horizon'};
  readonly receivedMasks: (HairMask | null)[] = [];
  readonly issuedSources: StubCanvas[] = [];
  readonly preparedInputs: OwnedInput[] = [];
  owned: OwnedInput | null = null;
  pending = false;
  private disposed = false;
  private completed = false;
  private selected: LiveVariant = 'hair';
  private retainedMask: HairMask | null = null;
  private readonly display: StubCanvas;
  private readonly marker: number;
  constructor(display: StubCanvas, marker: number) { this.display = display; this.marker = marker; }
  get stats() { return this.disposed || this.pending || !this.completed ? null : {hasFace: this.owned?.detection.matrix !== null,
    hasMask: this.retainedMask !== null, maskOutputMode: this.retainedMask?.outputMode ?? null}; }
  get captureSnapshot() { return this.stats?.hasFace ? {eyewearMatrix: this.owned!.detection.matrix} : null; }
  prepare(frame: HTMLCanvasElement, detection: Detection, pair: PairIdentity, model: HairModelContract): boolean {
    this.prepares++; this.pending = true; this.completed = false; this.retainedMask = null;
    if (this.failPrepare) { this.pending = false; this.owned = null; this.display.pixels.fill(0); throw new Error('injected prepare failure'); }
    this.owned = {source: (frame as unknown as StubCanvas).clone(), detection: structuredClone(detection), pair: {...pair}, expectedModel: structuredClone(model)};
    this.preparedInputs.push(this.owned);
    return detection.matrix !== null;
  }
  finish(mask: HairMask | null): boolean {
    assert.equal(this.pending, true); this.finishes++; this.pending = false;
    if (this.failFinish) { this.owned = null; this.display.pixels.fill(0); throw new Error('injected finish failure'); }
    this.receivedMasks.push(mask); this.retainedMask = mask ? structuredClone(mask) : null; this.completed = true;
    this.publish(); return this.owned!.detection.matrix !== null;
  }
  present(frame: HTMLCanvasElement, detection: Detection, mask: HairMask | null, pair: PairIdentity, model: HairModelContract): boolean {
    this.prepare(frame, detection, pair, model); return this.finish(mask);
  }
  selectVariant(variant: LiveVariant): void { this.selected = variant; this.publish(); }
  copyHeldInput(): HeldHairInput | null {
    if (!this.stats?.hasFace || !this.owned) return null;
    const source = this.owned.source.clone(); this.issuedSources.push(source);
    return {source: source as unknown as HTMLCanvasElement, detection: structuredClone(this.owned.detection),
      pair: {...this.owned.pair}, expectedModel: structuredClone(this.owned.expectedModel)};
  }
  exportDiagnostic(): Record<string, unknown> | null {
    if (this.failExport) throw new Error('injected export failure');
    if (!this.stats?.hasFace || !this.owned) return null;
    return {pair: {...this.owned.pair}, sourcePixels: Array.from(this.owned.source.pixels),
      detection: structuredClone(this.owned.detection), mask: structuredClone(this.retainedMask), variant: this.selected};
  }
  private publish(): void {
    if (this.disposed || this.pending || !this.completed || !this.owned) return;
    this.display.pixels = this.owned.source.pixels.slice(); this.display.pixels[0] = this.marker; this.publishes++;
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.disposals++; this.pending = false; this.owned = null; this.retainedMask = null; this.display.pixels.fill(0);
  }
}

function fixture() {
  const display = new StubCanvas(), source = new StubCanvas();
  const current = new StubRenderer(display, 11), candidate = new StubRenderer(display, 22);
  const renderer = Reflect.construct(ComparisonRenderer, [{current, test: candidate}]) as Comparison;
  const detection: Detection = {matrix: Array.from({length: 16}, (_, index) => Number(index % 5 === 0)), inferenceMs: 0,
    landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0}))};
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: 'amber-horizon'};
  const model = {id: 'hair-only', sha256: '3'.repeat(64), labels: ['background', 'hair'], hairIndex: 1};
  const mask: HairMask = {...pair, model: model.id, modelSHA256: model.sha256, labels: [...model.labels], hairIndex: 1,
    categorySHA256: '4'.repeat(64), outputMode: 'category-only', width: 2, height: 2, category: new Uint8Array([0, 1, 1, 0])};
  const canvas = source as unknown as HTMLCanvasElement;
  return {renderer, current, candidate, source, canvas, display, detection, pair, model, mask};
}

test('a live switch during pending work commits only on next prepare and never runs the inactive renderer', t => {
  const f = fixture(); t.after(() => f.renderer.dispose());
  f.renderer.prepare(f.canvas, f.detection, f.pair, f.model);
  f.renderer.selectPipeline('test');
  assert.equal(f.renderer.pipeline, 'current'); assert.equal(f.renderer.requestedPipeline, 'test');
  assert.equal(f.current.prepares, 1); assert.equal(f.candidate.prepares, 0);
  assert.equal(f.renderer.stats, null); assert.equal(f.renderer.copyHeldInput(), null);
  assert.throws(() => f.renderer.setHeld(), /Finish the owned pair/);
  assert.throws(() => f.renderer.prepare(f.canvas, f.detection, f.pair, f.model), /already pending/);
  f.renderer.finish(f.mask);
  assert.equal(f.display.pixels[0], 11); assert.equal(f.candidate.finishes, 0);
  f.renderer.prepare(f.canvas, f.detection, f.pair, f.model);
  assert.equal(f.renderer.pipeline, 'test'); assert.equal(f.current.prepares, 1); assert.equal(f.candidate.prepares, 1);
  f.renderer.selectPipeline('current'); f.renderer.finish(f.mask);
  assert.equal(f.display.pixels[0], 22); assert.equal(f.renderer.pipeline, 'test');
  f.renderer.present(f.canvas, f.detection, f.mask, f.pair, f.model);
  assert.equal(f.renderer.pipeline, 'current'); assert.equal(f.current.prepares, 2); assert.equal(f.candidate.prepares, 1);
});

test('held switches reuse one exact owned image/detection/mask and full-mask upgrade invalidates both output revisions', t => {
  const f = fixture(); t.after(() => f.renderer.dispose());
  f.renderer.present(f.canvas, f.detection, f.mask, f.pair, f.model); f.renderer.setHeld();
  const savedPair = {...f.pair}, savedDetection = structuredClone(f.detection);
  f.source.pixels.fill(99); f.detection.landmarks[0]!.x = .9; f.pair.sourceSHA256 = '9'.repeat(64);
  f.renderer.selectPipeline('test');
  assert.equal(f.candidate.preparedInputs[0]!.source.pixels[0], 37);
  assert.deepEqual(f.candidate.preparedInputs[0]!.pair, savedPair);
  assert.deepEqual(f.candidate.preparedInputs[0]!.detection, savedDetection);
  assert.equal(f.candidate.receivedMasks[0], f.mask, 'the other renderer receives the same exact mask, without inference');
  assert.equal(f.current.issuedSources[0]!.width, 0); assert.equal(f.current.issuedSources[0]!.height, 0);
  f.renderer.selectPipeline('current'); f.renderer.selectPipeline('test');
  assert.equal(f.current.prepares, 1); assert.equal(f.candidate.prepares, 1);
  const held = f.renderer.copyHeldInput()!;
  const full: HairMask = {...f.mask, outputMode: 'full', confidence: new Float32Array([.1, .9, .9, .1]), confidenceSHA256: '5'.repeat(64)};
  f.renderer.present(held.source, held.detection, full, held.pair, held.expectedModel);
  f.renderer.selectPipeline('current');
  assert.equal(f.current.prepares, 2); assert.equal(f.candidate.prepares, 2);
  assert.equal(f.current.receivedMasks[1], full); assert.equal(f.candidate.receivedMasks[1], full);
  const output = f.renderer.exportDiagnostic()!;
  assert.equal(output.candidateAccepted, false); assert.equal(output.selectedPipeline, 'current');
  assert.deepEqual(output.current, output.test);
  assert.equal(f.current.prepares, 2); assert.equal(f.candidate.prepares, 2, 'export reuses the completed held revision');
  held.source.width = held.source.height = 0;
});

test('inactive held preparation, finish and export failures restore the selected display and permit retry', t => {
  for (const failure of ['failPrepare', 'failFinish', 'failExport'] as const) {
    const f = fixture(); t.after(() => f.renderer.dispose());
    f.renderer.present(f.canvas, f.detection, f.mask, f.pair, f.model); f.renderer.setHeld();
    const pixels = f.display.pixels.slice(); f.candidate[failure] = true;
    assert.throws(() => f.renderer.exportDiagnostic(), /injected/);
    assert.equal(f.renderer.pipeline, 'current'); assert.equal(f.renderer.requestedPipeline, 'current');
    assert.deepEqual(f.display.pixels, pixels, `${failure} cannot leave the held display blank or switched`);
    assert.ok(f.current.issuedSources.every(source => source.width === 0 && source.height === 0));
    f.candidate[failure] = false;
    const output = f.renderer.exportDiagnostic()!;
    assert.deepEqual(output.current, output.test); assert.deepEqual(f.display.pixels, pixels);
  }
});

test('failure while exporting Current restores a previously selected Test output', t => {
  const f = fixture(); t.after(() => f.renderer.dispose());
  f.renderer.present(f.canvas, f.detection, f.mask, f.pair, f.model); f.renderer.setHeld(); f.renderer.selectPipeline('test');
  const pixels = f.display.pixels.slice(); f.current.failExport = true;
  assert.throws(() => f.renderer.exportDiagnostic(), /injected export/);
  assert.equal(f.renderer.pipeline, 'test'); assert.equal(f.renderer.requestedPipeline, 'test');
  assert.deepEqual(f.display.pixels, pixels);
});

test('no-face input drops prior owned facial metadata and disposal cancels pending publication exactly once', t => {
  const f = fixture(); t.after(() => f.renderer.dispose());
  f.renderer.present(f.canvas, f.detection, f.mask, f.pair, f.model);
  assert.equal(f.renderer.present(f.canvas, {...f.detection, matrix: null, landmarks: []}, null, f.pair, f.model), false);
  assert.equal(f.renderer.stats!.hasFace, false); assert.equal(f.renderer.captureSnapshot, null);
  assert.equal(f.renderer.copyHeldInput(), null); assert.equal(f.renderer.exportDiagnostic(), null);
  f.renderer.prepare(f.canvas, f.detection, f.pair, f.model); const published = f.current.publishes;
  f.renderer.dispose(); f.renderer.dispose();
  assert.equal(f.current.disposals, 1); assert.equal(f.candidate.disposals, 1);
  assert.equal(f.renderer.finish(f.mask), false); assert.equal(f.renderer.prepare(f.canvas, f.detection, f.pair, f.model), false);
  f.renderer.selectPipeline('test'); f.renderer.selectVariant('accepted');
  assert.equal(f.current.publishes, published); assert.equal(f.renderer.stats, null); assert.equal(f.renderer.exportDiagnostic(), null);
});

test('cancellation while the second factory is pending releases late initialization and the first renderer', async t => {
  const f = fixture(), abort = new AbortController();
  const previous = Object.getOwnPropertyDescriptor(globalThis, factorySymbol);
  let release: (() => void) | undefined, entered: (() => void) | undefined;
  const gate = new Promise<void>(resolve => {release = resolve;});
  const secondStarted = new Promise<void>(resolve => {entered = resolve;});
  Object.defineProperty(globalThis, factorySymbol, {configurable: true, value: {
    current: async () => f.current,
    test: async () => {entered!(); await gate; return f.candidate;},
  }});
  t.after(() => {f.renderer.dispose(); if (previous) Object.defineProperty(globalThis, factorySymbol, previous); else Reflect.deleteProperty(globalThis, factorySymbol);});
  const pending = ComparisonRenderer.create(f.display as unknown as HTMLCanvasElement, abort.signal);
  await secondStarted; abort.abort(); release!();
  await assert.rejects(pending, {name: 'AbortError'});
  assert.equal(f.current.disposals, 1); assert.equal(f.candidate.disposals, 1);
  assert.equal(f.current.prepares, 0); assert.equal(f.candidate.prepares, 0);
});
