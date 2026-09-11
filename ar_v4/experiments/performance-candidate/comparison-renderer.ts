import {LiveHairRenderer as CurrentRenderer} from '../hair-live-preview/renderer.ts';
import {LiveHairRenderer as TestRenderer} from './renderer.ts';
import type {HairMask, PairIdentity, HairModelContract, CaptureGeometry, HeldHairInput, LiveHairStats} from '../hair-live-preview/renderer.ts';
import type {LiveVariant} from '../hair-live-preview/ownership.ts';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import {DEFAULT_EYEWEAR_ID} from '../../references/perfect-temples/src/render/eyewear.ts';
import type {EyewearId} from '../../references/perfect-temples/src/render/eyewear.ts';

export type Pipeline = 'current' | 'test';
type Renderer = Pick<CurrentRenderer, 'prepare' | 'finish' | 'present' | 'selectVariant' | 'copyHeldInput'
  | 'exportDiagnostic' | 'dispose' | 'stats' | 'captureSnapshot' | 'eyewear' | 'nativeSamples'>;
export type {HairMask};

/** Both renderers are initialized, but only the selected one processes live frames.
 * Selection commits at prepare(), never halfway through an owned image/mask pair.
 * The other renderer is evaluated on that exact input only while explicitly held.
 */
export class ComparisonRenderer {
  private requested: Pipeline = 'current';
  private active: Pipeline = 'current';
  private pending = false;
  private held = false;
  private disposed = false;
  private mask: HairMask | null = null;
  private selectedVariant: LiveVariant = 'hair';
  private readonly renderedRevision: Record<Pipeline, number> = {current: -1, test: -1};
  private revision = 0;

  private constructor(private readonly renderers: Record<Pipeline, Renderer>) {}

  static async create(display: HTMLCanvasElement, signal: AbortSignal, eyewearId: EyewearId = DEFAULT_EYEWEAR_ID): Promise<ComparisonRenderer> {
    let current: CurrentRenderer | null = null;
    try {
      current = await CurrentRenderer.create(display, signal, eyewearId);
      const test = await TestRenderer.create(display, signal, eyewearId);
      if (signal.aborted) { test.dispose(); throw new DOMException('Comparison startup cancelled.', 'AbortError'); }
      return new ComparisonRenderer({current, test});
    } catch (error) { current?.dispose(); throw error; }
  }

  get pipeline(): Pipeline { return this.active; }
  get requestedPipeline(): Pipeline { return this.requested; }
  get stats(): LiveHairStats | null { return this.pending || this.disposed ? null : this.renderers[this.active].stats; }
  get captureSnapshot(): CaptureGeometry | null { return this.pending || this.disposed ? null : this.renderers[this.active].captureSnapshot; }
  get nativeSamples(): number { return this.renderers[this.active].nativeSamples; }
  get eyewear() { return this.renderers[this.active].eyewear; }
  get variant(): LiveVariant { return this.selectedVariant; }

  selectPipeline(pipeline: Pipeline): void {
    if (pipeline !== 'current' && pipeline !== 'test') throw new Error('Unknown comparison pipeline.');
    if (this.disposed) return;
    this.requested = pipeline;
    if (this.held && !this.pending) this.showHeld(pipeline);
  }

  selectVariant(variant: LiveVariant): void {
    this.selectedVariant = variant;
    // Publishing an inactive renderer here would show an old camera image.
    if (!this.disposed) this.renderers[this.active].selectVariant(variant);
  }

  prepare(frame: HTMLCanvasElement, detection: Detection, pair: PairIdentity, model: HairModelContract): boolean {
    if (this.disposed) return false;
    if (this.pending) throw new Error('A comparison frame is already pending.');
    this.active = this.requested;
    this.pending = true;
    this.mask = null;
    this.revision++;
    // prepare() suppresses publication before selectVariant() is changed.
    try {
      const result = this.renderers[this.active].prepare(frame, detection, pair, model);
      this.renderers[this.active].selectVariant(this.selectedVariant);
      return result;
    } catch (error) { this.pending = false; throw error; }
  }

  finish(mask: HairMask | null): boolean {
    if (this.disposed) return false;
    if (!this.pending) throw new Error('No comparison frame is pending.');
    try {
      const visible = this.renderers[this.active].finish(mask);
      // The live controller owns this unique worker result and never mutates it.
      // Each renderer independently copies any accepted mask for exports.
      this.mask = mask;
      this.renderedRevision[this.active] = this.revision;
      return visible;
    } finally { this.pending = false; }
  }

  present(frame: HTMLCanvasElement, detection: Detection, mask: HairMask | null, pair: PairIdentity, model: HairModelContract): boolean {
    this.prepare(frame, detection, pair, model);
    return this.finish(mask);
  }

  setHeld(): void {
    if (this.pending) throw new Error('Finish the owned pair before holding it.');
    this.held = true;
    this.requested = this.active;
  }

  copyHeldInput(): HeldHairInput | null { return this.disposed || this.pending ? null : this.renderers[this.active].copyHeldInput(); }

  private showHeld(pipeline: Pipeline): void {
    if (this.disposed || this.pending || !this.held) return;
    const target = this.renderers[pipeline];
    if (this.renderedRevision[pipeline] !== this.revision) {
      const input = this.renderers[this.active].copyHeldInput();
      if (!input) throw new Error('The exact held input is unavailable.');
      try {
        target.prepare(input.source, input.detection, input.pair, input.expectedModel);
        target.selectVariant(this.selectedVariant);
        target.finish(this.mask);
        this.renderedRevision[pipeline] = this.revision;
      } finally { input.source.width = input.source.height = 0; }
    } else target.selectVariant(this.selectedVariant);
    this.active = pipeline;
  }

  exportDiagnostic(): Record<string, unknown> | null {
    if (this.disposed || this.pending || !this.held) return null;
    const selected = this.active;
    try {
      this.showHeld('current'); const current = this.renderers.current.exportDiagnostic();
      this.showHeld('test'); const test = this.renderers.test.exportDiagnostic();
      if (!current || !test) return null;
      return {schema: 'ar-performance-comparison-v1', candidateAccepted: false, selectedPipeline: selected,
        inputPolicy: 'Both outputs use one exact held source, detection and hair mask. Only the selected pipeline runs live.',
        current, test};
    } finally {
      // A failed inactive renderer may clear the shared display. Restore the
      // already completed selected output even when diagnostic generation throws.
      this.active = selected;
      if (!this.disposed) this.renderers[selected].selectVariant(this.selectedVariant);
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.pending = false; this.mask = null;
    this.renderers.current.dispose(); this.renderers.test.dispose();
  }
}
