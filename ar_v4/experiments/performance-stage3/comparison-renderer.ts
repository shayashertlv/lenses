import {LiveHairRenderer as CurrentRenderer} from '../hair-live-preview/renderer.ts';
import {LiveHairRenderer as TestRenderer} from '../performance-candidate/renderer.ts';
import {LiveHairRenderer as Stage2Renderer} from '../performance-stage2/renderer.ts';
import {LiveHairRenderer as Stage3Renderer} from './renderer.ts';
import {PIPELINES} from './frame-profiler.ts';
import type {Pipeline} from './frame-profiler.ts';
import type {HairMask, PairIdentity, HairModelContract, CaptureGeometry, HeldHairInput, LiveHairStats} from '../hair-live-preview/renderer.ts';
import type {LiveVariant} from '../hair-live-preview/ownership.ts';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import {DEFAULT_EYEWEAR_ID} from '../../references/perfect-temples/src/render/eyewear.ts';
import type {EyewearId} from '../../references/perfect-temples/src/render/eyewear.ts';

export type {Pipeline};
type Renderer = Pick<CurrentRenderer, 'prepare' | 'finish' | 'present' | 'selectVariant' | 'copyHeldInput'
  | 'exportDiagnostic' | 'dispose' | 'stats' | 'captureSnapshot' | 'eyewear' | 'nativeSamples'> & {setPreparationHairEnabled?(enabled: boolean): void};
export type {HairMask};

/** All four renderers are initialized, but only the selected one processes live frames.
 * Selection commits at prepare(), never halfway through an owned image/mask pair.
 * An inactive renderer is evaluated on that exact input only while explicitly held.
 */
export class ComparisonRenderer {
  private requested: Pipeline = 'current';
  private active: Pipeline = 'current';
  private pending = false;
  private held = false;
  private disposed = false;
  private mask: HairMask | null = null;
  private selectedVariant: LiveVariant = 'hair';
  private readonly renderedRevision: Record<Pipeline, number> = {current: -1, test: -1, test2: -1, test3: -1};
  private revision = 0;

  private mounted: HTMLCanvasElement;
  private readonly onGpuLost: (event: Event) => void;
  private constructor(private readonly renderers: Record<Pipeline, Renderer>,
    private readonly legacyCanvas: HTMLCanvasElement, private readonly gpuCanvas: HTMLCanvasElement) {
    this.mounted = legacyCanvas;
    this.onGpuLost = (event): void => {
      event.preventDefault();
      legacyCanvas.dispatchEvent(new Event('webglcontextlost', {cancelable: true}));
    };
    gpuCanvas.addEventListener('webglcontextlost', this.onGpuLost);
  }

  static async create(display: HTMLCanvasElement, signal: AbortSignal, eyewearId: EyewearId = DEFAULT_EYEWEAR_ID): Promise<ComparisonRenderer> {
    let current: CurrentRenderer | null = null;
    let test: TestRenderer | null = null;
    let test2: Stage2Renderer | null = null;
    let test3: Stage3Renderer | null = null;
    const gpuCanvas = display.cloneNode(false) as HTMLCanvasElement;
    gpuCanvas.hidden = true;
    try {
      current = await CurrentRenderer.create(display, signal, eyewearId);
      test = await TestRenderer.create(display, signal, eyewearId);
      test2 = await Stage2Renderer.create(display, signal, eyewearId);
      test3 = await Stage3Renderer.create(gpuCanvas, signal, eyewearId);
      if (signal.aborted) throw new DOMException('Comparison startup cancelled.', 'AbortError');
      return new ComparisonRenderer({current, test, test2, test3}, display, gpuCanvas);
    } catch (error) {test3?.dispose();test2?.dispose();test?.dispose();current?.dispose();gpuCanvas.width=gpuCanvas.height=0;throw error;}
  }

  /** The mounted canvas changes only after a complete owned presentation. */
  get canvas(): HTMLCanvasElement { return this.mounted; }
  get pipeline(): Pipeline { return this.active; }
  get requestedPipeline(): Pipeline { return this.requested; }
  get stats(): LiveHairStats | null { return this.pending || this.disposed ? null : this.renderers[this.active].stats; }
  get captureSnapshot(): CaptureGeometry | null { return this.pending || this.disposed ? null : this.renderers[this.active].captureSnapshot; }
  get nativeSamples(): number { return this.renderers[this.active].nativeSamples; }
  get eyewear() { return this.renderers[this.active].eyewear; }
  get variant(): LiveVariant { return this.selectedVariant; }

  selectPipeline(pipeline: Pipeline): void {
    if (!PIPELINES.includes(pipeline)) throw new Error('Unknown comparison pipeline.');
    if (this.disposed) return;
    this.requested = pipeline;
    if (this.held && !this.pending) this.showHeld(pipeline);
  }

  selectVariant(variant: LiveVariant): void {
    this.selectedVariant = variant;
    // Publishing an inactive renderer here would show an old camera image.
    if (!this.disposed) this.renderers[this.active].selectVariant(variant);
  }

  prepare(frame: HTMLCanvasElement, detection: Detection, pair: PairIdentity, model: HairModelContract,
    needsHair = this.selectedVariant === 'hair'): boolean {
    if (this.disposed) return false;
    if (this.pending) throw new Error('A comparison frame is already pending.');
    this.active = this.requested;
    this.pending = true;
    this.mask = null;
    this.revision++;
    // prepare() suppresses publication before selectVariant() is changed.
    try {
      this.renderers[this.active].setPreparationHairEnabled?.(needsHair);
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
      this.mount(this.active);
      return visible;
    } finally { this.pending = false; }
  }

  present(frame: HTMLCanvasElement, detection: Detection, mask: HairMask | null, pair: PairIdentity, model: HairModelContract): boolean {
    this.prepare(frame, detection, pair, model, mask !== null || this.selectedVariant === 'hair');
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
        // Held comparisons may toggle hair even if live preparation skipped it.
        target.setPreparationHairEnabled?.(this.mask !== null || this.selectedVariant === 'hair');
        target.prepare(input.source, input.detection, input.pair, input.expectedModel);
        target.selectVariant(this.selectedVariant);
        target.finish(this.mask);
        this.renderedRevision[pipeline] = this.revision;
      } finally { input.source.width = input.source.height = 0; }
    } else target.selectVariant(this.selectedVariant);
    this.active = pipeline;
    this.mount(pipeline);
  }

  private mount(pipeline: Pipeline): void {
    const next = pipeline === 'test3' ? this.gpuCanvas : this.legacyCanvas;
    if (next !== this.mounted) {
      this.mounted.replaceWith(next);
      this.mounted.hidden = true;
      this.mounted = next;
    }
    next.hidden = false;
  }

  exportDiagnostic(): Record<string, unknown> | null {
    if (this.disposed || this.pending || !this.held) return null;
    const selected = this.active;
    try {
      const outputs: Partial<Record<Pipeline, Record<string, unknown>>> = {};
      for (const pipeline of PIPELINES) {
        this.showHeld(pipeline); const output = this.renderers[pipeline].exportDiagnostic();
        if (!output) return null; outputs[pipeline]=output;
      }
      return {schema: 'ar-performance-stage3-comparison-v1', candidateAccepted: false, selectedPipeline: selected,
        inputPolicy: 'All four outputs use one exact held source, detection and hair mask. Only the selected pipeline runs live.',
        ...outputs};
    } finally {
      // A failed inactive renderer may clear the shared display. Restore the
      // already completed selected output even when diagnostic generation throws.
      this.active = selected;
      if (!this.disposed) {this.renderers[selected].selectVariant(this.selectedVariant);this.mount(selected);}
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.pending = false; this.mask = null;
    this.gpuCanvas.removeEventListener('webglcontextlost', this.onGpuLost);
    for (const pipeline of PIPELINES) this.renderers[pipeline].dispose();
    if (this.mounted !== this.legacyCanvas) this.mounted.replaceWith(this.legacyCanvas);
    this.mounted = this.legacyCanvas;
    this.legacyCanvas.hidden = this.gpuCanvas.hidden = true;
    this.gpuCanvas.width = this.gpuCanvas.height = 0;
  }
}
