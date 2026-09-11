import {TryOnRenderer as PerfectoRenderer} from '../native/renderer.ts';
import type {CaptureGeometry as PerfectoGeometry} from '../native/renderer.ts';
import type {NativeStageTimings} from '../native/renderer.ts';
import {TryOnRenderer as BranchRenderer} from './branch-renderer.ts';
import {rearDropForPose} from '../../../references/perfect-temples/experiments/temple-sagittal/rear-drop.ts';
import {REAR_DROP_METHOD, validateRearDrop, validateProtection} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import type {RearDropConfiguration, ProtectionConfiguration} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import {composeProtectedPixels, createProtection, protectionProjection} from '../../performance-candidate/temples/protection.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';
import type {TempleClipConfiguration} from '../../../references/perfect-temples/src/render/temple-clip.ts';
import type {TempleVisibilityConfiguration} from '../../../references/perfect-temples/src/render/temple-visibility.ts';
import {DEFAULT_EYEWEAR_ID} from '../../../references/perfect-temples/src/render/eyewear.ts';
import type {EyewearId} from '../../../references/perfect-temples/src/render/eyewear.ts';
import {VIRTUAL_CAMERA} from '../../../references/perfect-temples/src/render/projection.ts';
import {NativePixelReader} from '../../performance-stage2/native/native-pixels.ts';
import {DEFAULT_SPEED_OPTIONS, normalizeSpeedOptions, assertSourceFrameCurrent, sourcePixelsOpaque} from '../speed-options.ts';
import type {NativeSpeedOptions, OwnedSourceFrame} from '../speed-options.ts';
import {PboNativeReadback} from '../native/pbo-readback.ts';
import type {PboReadbackMetrics} from '../native/pbo-readback.ts';
import {editableRowBand, readBranchRegion, emptyBranchRegionMetrics} from './branch-region.ts';
import type {BranchRegionMetrics} from './branch-region.ts';
import {emptyBranchLensMetrics} from './branch-lenses.ts';
import type {BranchLensMetrics} from './branch-lenses.ts';

export type {RearDropConfiguration, ProtectionConfiguration} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
export type ComparisonVariant = 'perfecto' | 'candidate';
export interface RendererOptions {publish?: boolean;}
export interface TempleStageTimings {
  sourceOwnershipMs: number; nativePresentMs: number; baselineReadbackMs: number;
  branchPresentMs: number; branchReadbackMs: number; protectionMs: number; compositionMs: number;
  publishMs: number; totalMs: number; baselineReadbackCalls: number; branchReadbackCalls: number;
  baselineReadbackBytes: number; branchReadbackBytes: number; zeroDropBranchSkipped: boolean;
  native: NativeStageTimings | null;
  efficiencyLab: {asyncTemplesRequested: boolean; asyncTemplesUsed: boolean; branchSubmittedBeforeBeautyAwait: boolean;
    branchFallback: string | null; branchPbo: PboReadbackMetrics | null;
    branchRegion: BranchRegionMetrics; branchLenses: BranchLensMetrics};
  speedLab: {sourceCopiesAvoided: number; sourceBytesBorrowed: number; sourceCopyFallback: string | null;
    prewarmRequested: boolean; prewarmAttempted: boolean; prewarmCompleted: boolean; prewarmMs: number; prewarmFailure: string | null;
    prewarmReadbackCalls: number; prewarmReadbackBytes: number};
}
const emptyStages = (): TempleStageTimings => ({sourceOwnershipMs: 0, nativePresentMs: 0, baselineReadbackMs: 0,
  branchPresentMs: 0, branchReadbackMs: 0, protectionMs: 0, compositionMs: 0, publishMs: 0, totalMs: 0,
  baselineReadbackCalls: 0, branchReadbackCalls: 0, baselineReadbackBytes: 0, branchReadbackBytes: 0,
  zeroDropBranchSkipped: false, native: null, efficiencyLab: {asyncTemplesRequested: false, asyncTemplesUsed: false,
    branchSubmittedBeforeBeautyAwait: false, branchFallback: null, branchPbo: null,
    branchRegion: emptyBranchRegionMetrics(), branchLenses: emptyBranchLensMetrics()}, speedLab: {sourceCopiesAvoided: 0, sourceBytesBorrowed: 0,
    sourceCopyFallback: null, prewarmRequested: false, prewarmAttempted: false, prewarmCompleted: false, prewarmMs: 0, prewarmFailure: null,
    prewarmReadbackCalls: 0, prewarmReadbackBytes: 0}});
interface PreparedInput {frame: HTMLCanvasElement; paired: Detection; options: NativeSpeedOptions; source?: OwnedSourceFrame;
  sourceMs: number; borrowed: boolean; copyFallback: string | null; onNativeSubmitted?: () => void;}
interface PreparedBranch {baseline: PerfectoGeometry; dropM: number; pixels: ImageData | null;
  presentMs: number; readbackMs: number; pbo: PboReadbackMetrics | null; failure: string | null;}
interface PreparedNative {input: PreparedInput; visible: boolean; nativeMs: number; started: number;
  branch: PreparedBranch | null; branchNotAttempted: string | null;}
export interface CaptureGeometry extends PerfectoGeometry {
  rearDrop: RearDropConfiguration | null;
  protection: ProtectionConfiguration | null;
}

function context2D(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const context = canvas.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'});
  if (!context) throw new Error('The pixel comparison canvas is unavailable.');
  return context;
}

function abortError(): DOMException { return new DOMException('Comparison startup was cancelled.', 'AbortError'); }
function sameGeometry(first: PerfectoGeometry, second: PerfectoGeometry): boolean {
  return first.surfacePositions.length === second.surfacePositions.length
    && first.eyewearMatrix.length === second.eyewearMatrix.length
    && first.surfacePositions.every((value, index) => value === second.surfacePositions[index])
    && first.eyewearMatrix.every((value, index) => value === second.eyewearMatrix[index]);
}

/**
 * Separate performance candidate. Native scenes and final pixel protections
 * retain the accepted algorithm; exact-zero work and redundant transfers skip.
 */
export class TryOnRenderer {
  private readonly perfecto: PerfectoRenderer;
  private readonly branch: BranchRenderer;
  private readonly baselineCanvas: HTMLCanvasElement;
  private readonly branchCanvas: HTMLCanvasElement;
  private readonly display: HTMLCanvasElement;
  private readonly displayContext: CanvasRenderingContext2D;
  private readonly pairedFrame = document.createElement('canvas');
  private readonly pairedContext = context2D(this.pairedFrame);
  private readonly baselineReadback = document.createElement('canvas');
  private readonly baselineContext = context2D(this.baselineReadback);
  private readonly branchReadback = document.createElement('canvas');
  private readonly branchContext = context2D(this.branchReadback);
  private readonly sampleCount: number;
  private pairedDetection: Detection | null = null;
  private baselinePixels: ImageData | null = null;
  private candidatePixels: ImageData | null = null;
  private branchPixels: ImageData | null = null;
  private croppedBranch = false;
  private diagnosticBranchPixels: ImageData | null = null;
  private branchLensOmitted = false;
  private cameraPixels: ImageData | null = null;
  private cameraRequested = false;
  private stages = emptyStages();
  private readonly baselineReader = new NativePixelReader();
  private readonly branchReader = new NativePixelReader();
  private readonly publishes: boolean;
  private snapshot: PerfectoGeometry | null = null;
  private appliedRearDrop: RearDropConfiguration | null = null;
  private protection: ProtectionConfiguration | null = null;
  private selectedVariant: ComparisonVariant = 'candidate';
  private replaying = false;
  private disposed = false;
  private sequence = 0;
  private cleanupListeners: (() => void)[] = [];
  private lastDiagnostics: Record<string, unknown> | null = null;
  private nextOptions: NativeSpeedOptions = DEFAULT_SPEED_OPTIONS;
  private nextSource: OwnedSourceFrame | undefined;
  private nextSubmission: (() => void) | undefined;
  private branchPbo: PboNativeReadback | null = null;
  private activeSource: OwnedSourceFrame | undefined;
  private presentationFrame: HTMLCanvasElement | null = null;
  private presentationBorrowed = false;
  private preparedNative: PreparedNative | null = null;
  private awaiting = false;
  private warmAttempted = false;
  private warmCompleted = false;
  private warmFailure: string | null = null;

  private constructor(display: HTMLCanvasElement, baselineCanvas: HTMLCanvasElement, branchCanvas: HTMLCanvasElement,
    perfecto: PerfectoRenderer, branch: BranchRenderer, options: RendererOptions = {}) {
    this.display = display; this.baselineCanvas = baselineCanvas; this.branchCanvas = branchCanvas;
    this.perfecto = perfecto; this.branch = branch;
    this.displayContext = context2D(display);
    this.publishes = options.publish !== false;
    const gl = baselineCanvas.getContext('webgl2');
    const samples: unknown = gl?.getParameter(gl.SAMPLES);
    this.sampleCount = typeof samples === 'number' ? samples : 0;
  }

  static async create(display: HTMLCanvasElement, signal: AbortSignal, eyewearId: EyewearId = DEFAULT_EYEWEAR_ID,
    options: RendererOptions = {}): Promise<TryOnRenderer> {
    if (signal.aborted) throw abortError();
    const baselineCanvas = document.createElement('canvas'), branchCanvas = document.createElement('canvas');
    let perfecto: PerfectoRenderer | null = null, branch: BranchRenderer | null = null, instance: TryOnRenderer | null = null;
    try {
      perfecto = await PerfectoRenderer.create(baselineCanvas, signal, eyewearId);
      if (signal.aborted) throw abortError();
      branch = await BranchRenderer.create(branchCanvas, signal, eyewearId);
      if (signal.aborted) throw abortError();
      instance = new TryOnRenderer(display, baselineCanvas, branchCanvas, perfecto, branch, options);
      const owner = instance;
      const onAbort = () => owner.dispose();
      signal.addEventListener('abort', onAbort, {once: true});
      owner.cleanupListeners.push(() => signal.removeEventListener('abort', onAbort));
      const onContextLost = (event: Event) => {
        event.preventDefault();
        if (owner.disposed) return;
        owner.dispose();
        display.dispatchEvent(new Event('webglcontextlost', {cancelable: true}));
      };
      for (const canvas of [baselineCanvas, branchCanvas]) {
        canvas.addEventListener('webglcontextlost', onContextLost);
        owner.cleanupListeners.push(() => canvas.removeEventListener('webglcontextlost', onContextLost));
      }
      if (signal.aborted) throw abortError();
      return owner;
    } catch (error) {
      if (instance) instance.dispose();
      else { branch?.dispose(); perfecto?.dispose(); baselineCanvas.width = branchCanvas.width = 0; }
      throw signal.aborted ? abortError() : error;
    }
  }

  get eyewear() { return this.perfecto.eyewear; }
  get occlusionConfiguration() { return this.perfecto.occlusionConfiguration; }
  get projectionResidualPx(): number | null { return this.perfecto.projectionResidualPx; }
  get bridgeCorrectionPx(): number | null { return this.perfecto.bridgeCorrectionPx; }
  get yawDegrees(): number | null { return this.perfecto.yawDegrees; }
  get templeClipConfiguration(): TempleClipConfiguration { return this.perfecto.templeClipConfiguration; }
  get templeVisibilityPolicy() { return this.perfecto.templeVisibilityPolicy; }
  get nativeSamples(): number { return this.sampleCount; }
  get variant(): ComparisonVariant { return this.selectedVariant; }
  setFrameOptions(options: NativeSpeedOptions, source?: OwnedSourceFrame, onNativeSubmitted?: () => void): void {
    if (this.awaiting) throw new Error('A pending temple pair cannot change options.');
    this.nextOptions = normalizeSpeedOptions(options); this.nextSource = source; this.nextSubmission = onNativeSubmitted;
  }
  private takeInput(frame: HTMLCanvasElement, detection: Detection, previousBorrowedPixels?: ImageData): PreparedInput {
    const started = performance.now(), options = this.nextOptions, source = this.nextSource, onNativeSubmitted = this.nextSubmission;
    this.nextSubmission = undefined;
    this.nextOptions = DEFAULT_SPEED_OPTIONS; this.nextSource = undefined;
    if (frame.width <= 0 || frame.height <= 0) throw new Error('The paired camera frame is empty.');
    if (source) assertSourceFrameCurrent(source, frame);
    const copyFallback = !options.fewerCopies ? null : !source ? 'No current owned-frame contract was supplied.'
      : !sourcePixelsOpaque(source.rgba) ? 'Source pixels are not fully opaque; retaining the original opaque canvas copy.' : null;
    const borrowed = options.fewerCopies && copyFallback === null;
    if (!borrowed) {
      if (this.pairedFrame.width !== frame.width || this.pairedFrame.height !== frame.height) {
        this.pairedFrame.width = frame.width; this.pairedFrame.height = frame.height;
      }
      // A transparent copy blends over the preceding owned image in Test2.
      // Restore that backdrop when its preceding opaque frame was borrowed.
      if (previousBorrowedPixels?.width === frame.width && previousBorrowedPixels.height === frame.height)
        this.pairedContext.putImageData(previousBorrowedPixels, 0, 0);
      this.pairedContext.drawImage(frame, 0, 0);
    }
    const owned = borrowed ? frame : this.pairedFrame;
    return {frame: owned, paired: structuredClone(detection), options,
      ...(source ? {source: {...source, canvas: owned}} : {}), sourceMs: performance.now() - started, borrowed,
      copyFallback, ...(onNativeSubmitted ? {onNativeSubmitted} : {})};
  }
  /** Held diagnostics may outlive the borrowed canvas. Materialize only on demand
   * from the retained immutable RGBA, never from a canvas reused by another pair. */
  private get ownedFrame(): HTMLCanvasElement {
    if (this.presentationBorrowed && this.presentationFrame && this.activeSource && !this.activeSource.isCurrent()) {
      const image = this.activeSource.rgba;
      this.pairedFrame.width = image.width; this.pairedFrame.height = image.height;
      this.pairedContext.putImageData(image, 0, 0); this.presentationFrame = this.pairedFrame;
      this.presentationBorrowed = false; this.activeSource = undefined;
    }
    return this.presentationFrame ?? this.pairedFrame;
  }
  setPreparationHairEnabled(enabled: boolean): void {if (!this.disposed) this.cameraRequested = enabled;}
  get ownedCameraPixels(): ImageData | null {return this.disposed ? null : this.cameraPixels;}
  get stageTimings(): TempleStageTimings {return structuredClone(this.stages);}
  /** Borrow read-only until the next present/dispose; callers must not mutate. */
  get ownedPixels(): ImageData | null {
    if (this.disposed) return null;
    return this.replaying || this.selectedVariant === 'candidate' ? this.candidatePixels : this.baselinePixels;
  }
  get diagnostics(): Record<string, unknown> | null { return this.lastDiagnostics ? structuredClone(this.lastDiagnostics) : null; }
  get rearDropPolicy() { return {method: REAR_DROP_METHOD, maximumDropM: .02, unchangedAxes: ['x', 'z'],
    output: 'integer selection outside full optical and central nasal protection', performance: 'pixel prototype; no frame-rate claim'}; }
  get captureSnapshot(): CaptureGeometry | null {
    if (!this.snapshot || this.disposed) return null;
    const selectedCandidate = this.replaying || this.selectedVariant === 'candidate';
    return {...structuredClone(this.snapshot),
      rearDrop: selectedCandidate ? structuredClone(this.appliedRearDrop) : {method: REAR_DROP_METHOD, dropM: 0},
      protection: selectedCandidate ? structuredClone(this.protection) : null};
  }

  selectVariant(variant: ComparisonVariant): void {
    if (this.disposed || this.replaying) return;
    if (variant !== 'perfecto' && variant !== 'candidate') throw new Error('The comparison version is invalid.');
    this.selectedVariant = variant;
    // Both buffers already belong to the same owned image/detection pair.
    // Switching never re-detects, advances the source, or recomputes the pose.
    this.publish();
  }

  async presentAsync(frame: HTMLCanvasElement, detection: Detection, recordedSurface?: readonly number[],
    recordedTempleClip?: TempleClipConfiguration | null, recordedTempleVisibility?: TempleVisibilityConfiguration | null,
    recordedRearDrop?: RearDropConfiguration | null, recordedProtection?: ProtectionConfiguration | null): Promise<boolean> {
    if (this.disposed) return false;
    if (this.awaiting) throw new Error('The preceding temple pair is pending.');
    const previousBorrowedPixels = this.presentationBorrowed ? this.activeSource?.rgba : undefined;
    this.clearOwnedPresentation();
    const started = performance.now(); this.awaiting = true;
    try {
      const input = this.takeInput(frame, detection, previousBorrowedPixels);
      this.perfecto.setFrameOptions(input.options, input.source, input.onNativeSubmitted);
      const nativeStarted = performance.now();
      // presentAsync performs native draw/read submission synchronously. Its
      // snapshot is an independent copy of this exact image's actual geometry.
      let nativeMs = 0;
      const nativePending = this.perfecto.presentAsync(input.frame, input.paired, recordedSurface,
        recordedTempleClip, recordedTempleVisibility, this.cameraRequested)
        .then(visible => {nativeMs = performance.now() - nativeStarted; return visible;});
      const branchNotAttempted = input.options.asyncTemples && input.options.prewarmTemples && !this.warmAttempted
        ? 'First valid pair retains the completed synchronous temple prewarm.' : null;
      const branchPending = (async (): Promise<PreparedBranch | null> => {
        if (!input.options.asyncTemples || branchNotAttempted) return null;
        const baseline = this.perfecto.captureSnapshot;
        if (!baseline || !input.paired.matrix) return null;
        const replaying = recordedSurface !== undefined || recordedTempleClip !== undefined || recordedTempleVisibility !== undefined
          || recordedRearDrop !== undefined || recordedProtection !== undefined;
        const dropM = replaying ? recordedRearDrop?.dropM ?? 0 : rearDropForPose(input.paired.matrix);
        return dropM > 0 ? this.prepareBranch(input, structuredClone(baseline), dropM) : null;
      })();
      // Join both bounded tasks even on failure: no deferred read may survive
      // into the next image/session or overwrite a held branch.
      const [nativeResult, branchResult] = await Promise.allSettled([nativePending, branchPending]);
      if (nativeResult.status === 'rejected') throw nativeResult.reason;
      if (branchResult.status === 'rejected') throw branchResult.reason;
      const visible = nativeResult.value;
      if (this.disposed || input.source && !input.source.isCurrent()) throw abortError();
      this.preparedNative = {input, visible, nativeMs, started, branch: branchResult.value, branchNotAttempted};
      this.awaiting = false;
      return this.present(frame, detection, recordedSurface, recordedTempleClip, recordedTempleVisibility, recordedRearDrop, recordedProtection);
    } catch (error) {
      this.clearOwnedPresentation(); this.preparedNative = null; this.branchPbo?.cancel();
      try {this.branch.clearOwnedFrame();} catch { /* No previous branch is publishable. */ }
      throw error;
    } finally {this.awaiting = false;}
  }

  /** Only submits commands for copied native geometry; PBO extraction is fenced.
   * The old synchronous branch remains the same-pair fallback in present(). */
  private async prepareBranch(input: PreparedInput, baseline: PerfectoGeometry, dropM: number): Promise<PreparedBranch> {
    const result: PreparedBranch = {baseline, dropM, pixels: null, presentMs: 0, readbackMs: 0, pbo: null, failure: null};
    let readStarted = 0, beganReadback = false;
    try {
      if (this.disposed || input.source && !input.source.isCurrent()) throw abortError();
      validateRearDrop({method: REAR_DROP_METHOD, dropM});
      const branchStarted = performance.now();
      const visible = this.branch.present(input.frame, input.paired, Array.from(baseline.surfacePositions),
        baseline.templeClip ?? null, baseline.templeVisibility ?? null, {method: REAR_DROP_METHOD, dropM});
      result.presentMs = performance.now() - branchStarted;
      const snapshot = this.branch.captureSnapshot;
      if (!visible || this.branchCanvas.width !== this.baselineCanvas.width || this.branchCanvas.height !== this.baselineCanvas.height
        || !snapshot || !sameGeometry(snapshot, baseline)) throw new Error('The early branch changed paired geometry or dimensions.');
      readStarted = performance.now();
      this.branchPbo ??= new PboNativeReadback(this.branchCanvas);
      this.branchPbo.begin(this.branchCanvas.width, this.branchCanvas.height, input.options.poolReadbackScratch);
      beganReadback = true;
      this.branchPbo.capture(0);
      const pixels = await this.branchPbo.finish(() => !this.disposed && (!input.source || input.source.isCurrent()));
      if (this.disposed || input.source && !input.source.isCurrent()) throw abortError();
      result.pixels = pixels.beauty;
    } catch (error) {
      result.failure = error instanceof Error ? error.message : String(error);
      this.branchPbo?.cancel();
    } finally {
      result.pbo = beganReadback ? this.branchPbo?.metrics ?? null : null;
      if (readStarted) result.readbackMs = performance.now() - readStarted;
    }
    return result;
  }

  present(frame: HTMLCanvasElement, detection: Detection, recordedSurface?: readonly number[],
    recordedTempleClip?: TempleClipConfiguration | null, recordedTempleVisibility?: TempleVisibilityConfiguration | null,
    recordedRearDrop?: RearDropConfiguration | null, recordedProtection?: ProtectionConfiguration | null): boolean {
    if (this.disposed) return false;
    if (this.awaiting) throw new Error('The preceding temple pair is pending.');
    const prepared = this.preparedNative; this.preparedNative = null;
    const previousBorrowedPixels = this.presentationBorrowed ? this.activeSource?.rgba : undefined;
    this.clearOwnedPresentation();
    const started = prepared?.started ?? performance.now(); this.stages = emptyStages();
    this.sequence++;
    this.replaying = recordedSurface !== undefined || recordedTempleClip !== undefined || recordedTempleVisibility !== undefined
      || recordedRearDrop !== undefined || recordedProtection !== undefined;
    try {
      const input = prepared?.input ?? this.takeInput(frame, detection, previousBorrowedPixels), paired = input.paired;
      this.presentationFrame = input.frame; this.activeSource = input.source; this.presentationBorrowed = input.borrowed;
      this.stages.sourceOwnershipMs = input.sourceMs;
      const earlyBranch = prepared?.branch;
      this.stages.efficiencyLab = {asyncTemplesRequested: input.options.asyncTemples, asyncTemplesUsed: false,
        branchSubmittedBeforeBeautyAwait: !!earlyBranch?.pbo?.queuedCalls,
        branchFallback: earlyBranch?.failure ?? prepared?.branchNotAttempted ?? (input.options.asyncTemples && !prepared ? 'Synchronous presentation requested.' : null),
        branchPbo: earlyBranch?.pbo ?? null,
        branchRegion: emptyBranchRegionMetrics(input.options.cropBranchReadback),
        branchLenses: emptyBranchLensMetrics(input.options.omitBranchLenses)};
      Object.assign(this.stages.speedLab, {sourceCopiesAvoided: Number(input.borrowed),
        sourceBytesBorrowed: input.borrowed ? input.frame.width * input.frame.height * 4 : 0, sourceCopyFallback: input.copyFallback,
        prewarmRequested: input.options.prewarmTemples, prewarmCompleted: this.warmCompleted, prewarmFailure: this.warmFailure});
      const nativeStarted = performance.now();
      if (!prepared) this.perfecto.setFrameOptions(input.options, input.source, input.onNativeSubmitted);
      const visible = prepared?.visible ?? this.perfecto.present(input.frame, paired, recordedSurface, recordedTempleClip, recordedTempleVisibility, this.cameraRequested);
      this.stages.nativePresentMs = prepared?.nativeMs ?? performance.now() - nativeStarted;
      this.stages.native = this.perfecto.stageTimings;
      const width = this.baselineCanvas.width, height = this.baselineCanvas.height;
      for (const canvas of [this.display]) {
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      }
      // Native beauty was captured before subsequent draws; the early branch
      // lives in its own unchanged context and cannot replace those bytes.
      const nativePair = this.perfecto.nativeImagePair;
      if (nativePair) {
        this.baselinePixels = nativePair.beauty; this.cameraPixels = nativePair.camera;
      } else {
        const readStarted = performance.now();
        this.baselinePixels = this.baselineReader.read(this.baselineCanvas);
        this.stages.baselineReadbackMs = performance.now() - readStarted;
        this.stages.baselineReadbackCalls = 1; this.stages.baselineReadbackBytes = this.baselinePixels.data.byteLength;
      }
      this.candidatePixels = this.baselinePixels;
      this.snapshot = this.perfecto.captureSnapshot;
      this.pairedDetection = paired;
      if (!visible || !this.snapshot || !paired.matrix) {
        let branchClearFailure: string | null = null;
        try { this.branch.clearOwnedFrame(); }
        catch (error) { branchClearFailure = error instanceof Error ? error.message : 'Branch clearing failed'; }
        // B is already valid for this exact no-face image. Failure to clear the
        // independent branch must not erase B or publish a previous candidate.
        this.lastDiagnostics = {method: 'temple-optics-copy-v1', sequence: this.sequence, fallback: 'no valid paired face',
          changedPixels: 0, appliedDropM: 0, branchClearFailure};
        this.publish(); return visible;
      }
      const requested: RearDropConfiguration | null = this.replaying ? recordedRearDrop ?? null
        : {method: REAR_DROP_METHOD, dropM: rearDropForPose(paired.matrix)};
      if (input.options.prewarmTemples && !this.warmAttempted) {
        this.warmAttempted = true; this.stages.speedLab.prewarmAttempted = true;
        const warmStarted = performance.now();
        try {
          const snapshot = this.snapshot, visibility = snapshot.templeVisibility;
          const warmVisibility = visibility ? {...visibility, negativeXWeight: 1, positiveXWeight: 1,
            frontalOcclusionWeight: 1} : null;
          const warmed = this.branch.present(this.ownedFrame, paired, Array.from(snapshot.surfacePositions),
            snapshot.templeClip ?? null, warmVisibility, {method: REAR_DROP_METHOD, dropM: .02});
          if (!warmed || !this.branch.captureSnapshot) throw new Error('The hidden temple warmup did not produce valid geometry.');
          // One discarded native read waits for the warm render to complete;
          // shader submission alone is not reported as completed prewarming.
          const warmPixels = this.branchReader.read(this.branchCanvas);
          this.stages.speedLab.prewarmReadbackCalls = 1;
          this.stages.speedLab.prewarmReadbackBytes = warmPixels.data.byteLength;
          this.warmCompleted = true;
        } catch (error) {this.warmFailure = error instanceof Error ? error.message : String(error);}
        finally {
          try {this.branch.clearOwnedFrame();} catch (error) {this.warmCompleted = false; this.warmFailure = String(error);}
          this.stages.speedLab.prewarmMs = performance.now() - warmStarted;
          this.stages.speedLab.prewarmCompleted = this.warmCompleted; this.stages.speedLab.prewarmFailure = this.warmFailure;
        }
      }
      this.appliedRearDrop = {method: REAR_DROP_METHOD, dropM: 0};
      try {
        if (requested) validateRearDrop(requested);
        const dropM = requested?.dropM ?? 0;
        const baseline = this.snapshot;
        let earlyProtection: ProtectionConfiguration | null = null;
        let earlyProtectionMs = 0;
        if (dropM === 0) {
          // Actual zero-drop bounds still establish this pair's hair permissions.
          // Invalidate old GPU/snapshot ownership without drawing another image.
          this.branch.clearOwnedFrame();
          this.stages.zeroDropBranchSkipped = true;
        } else {
          const earlyMatches = earlyBranch?.pixels && earlyBranch.dropM === dropM && sameGeometry(earlyBranch.baseline, baseline)
            && JSON.stringify(earlyBranch.baseline.templeClip) === JSON.stringify(baseline.templeClip)
            && JSON.stringify(earlyBranch.baseline.templeVisibility) === JSON.stringify(baseline.templeVisibility);
          if (earlyMatches) {
            this.branchPixels = earlyBranch.pixels;
            this.stages.branchPresentMs = earlyBranch.presentMs;
            this.stages.branchReadbackMs = earlyBranch.readbackMs;
            this.stages.efficiencyLab.asyncTemplesUsed = true;
          } else {
          if (earlyBranch) this.stages.efficiencyLab.branchFallback ??= 'Final native geometry differed from the submitted branch.';
          const branchStarted = performance.now();
          const branchVisible = this.branch.present(this.ownedFrame, paired, Array.from(baseline.surfacePositions),
            baseline.templeClip ?? null, baseline.templeVisibility ?? null, {method: REAR_DROP_METHOD, dropM}, input.options.omitBranchLenses);
          this.stages.branchPresentMs = performance.now() - branchStarted;
          this.stages.efficiencyLab.branchLenses = this.branch.branchLensMetrics ?? emptyBranchLensMetrics(input.options.omitBranchLenses);
          this.branchLensOmitted = this.stages.efficiencyLab.branchLenses.used;
          if (!branchVisible || this.branchCanvas.width !== width || this.branchCanvas.height !== height) throw new Error('The branch presentation differs in size or face ownership.');
          const branchSnapshot = this.branch.captureSnapshot;
          if (!branchSnapshot || branchSnapshot.surfacePositions.some((value, index) => value !== baseline.surfacePositions[index])
            || branchSnapshot.eyewearMatrix.some((value, index) => value !== baseline.eyewearMatrix[index])) throw new Error('The branch changed the paired surface or pose.');
          const readStarted = performance.now();
          if (input.options.cropBranchReadback) {
            const protectionStarted = performance.now(), bounds = this.branch.rearDropBounds;
            earlyProtection = bounds ? createProtection(bounds, baseline.eyewearMatrix, this.eyewear.offsetCm, paired.landmarks,
              width, height, this.ownedFrame.width / this.ownedFrame.height) : null;
            if (!earlyProtection) throw new Error('The current optical/nasal protection could not be established.');
            const region = this.stages.efficiencyLab.branchRegion;
            region.fullBytes = width * height * 4; region.rectangle = editableRowBand(earlyProtection);
            earlyProtectionMs = performance.now() - protectionStarted;
            try {
              const cropped = readBranchRegion(this.branchCanvas, this.baselinePixels, region.rectangle);
              this.branchPixels = cropped.pixels; this.croppedBranch = true; region.used = true;
              region.readBytes = cropped.readBytes;
              this.stages.branchReadbackCalls = cropped.readCalls; this.stages.branchReadbackBytes = cropped.readBytes;
            } catch (error) {
              region.fallback = error instanceof Error ? error.message : String(error);
              this.branchPixels = this.branchReader.read(this.branchCanvas);
              region.readBytes = this.branchPixels.data.byteLength;
              this.stages.branchReadbackCalls = 1; this.stages.branchReadbackBytes = region.readBytes;
            }
          } else {
            this.branchPixels = this.branchReader.read(this.branchCanvas);
            this.stages.branchReadbackCalls = 1; this.stages.branchReadbackBytes = this.branchPixels.data.byteLength;
          }
          this.stages.branchReadbackMs = performance.now() - readStarted;
          this.stages.branchReadbackMs -= earlyProtectionMs;
          if (earlyBranch) {this.stages.branchPresentMs += earlyBranch.presentMs; this.stages.branchReadbackMs += earlyBranch.readbackMs;}
          }
        }
        const protectionStarted = performance.now();
        const bounds = this.branch.rearDropBounds;
        const protection = earlyProtection ?? (bounds ? createProtection(bounds, baseline.eyewearMatrix, this.eyewear.offsetCm, paired.landmarks,
          width, height, this.ownedFrame.width / this.ownedFrame.height) : null);
        if (!protection) throw new Error('The current optical/nasal protection could not be established.');
        if (this.replaying && dropM > 0) {
          if (!recordedProtection) throw new Error('The saved candidate is missing its actual protected regions.');
          validateProtection(recordedProtection, width, height);
          // Saved masks cannot weaken current minimum optical/nasal protection.
          if (JSON.stringify(recordedProtection) !== JSON.stringify(protection)) throw new Error('The saved candidate protection does not match this exact presentation.');
        }
        this.stages.protectionMs = earlyProtectionMs + performance.now() - protectionStarted;
        const composeStarted = performance.now();
        const composed = dropM > 0 && this.branchPixels ? composeProtectedPixels(this.baselinePixels.data, this.branchPixels.data, protection)
          : {pixels: this.baselinePixels.data, changedPixels: 0, editablePixels: 0, protectedPixels: 0};
        if (!(composed.pixels.buffer instanceof ArrayBuffer)) throw new Error('Candidate output requires owned nonshared bytes.');
        this.candidatePixels = dropM === 0 ? this.baselinePixels
          : new ImageData(new Uint8ClampedArray(composed.pixels.buffer, composed.pixels.byteOffset, composed.pixels.length), width, height);
        this.stages.compositionMs = performance.now() - composeStarted;
        this.protection = protection;
        this.appliedRearDrop = {method: REAR_DROP_METHOD, dropM};
        this.lastDiagnostics = {method: 'temple-optics-copy-v1', sequence: this.sequence, fallback: null,
          requestedDropM: dropM, appliedDropM: dropM, changedPixels: composed.changedPixels,
          editablePixels: composed.editablePixels, protectedPixels: composed.protectedPixels,
          protectedChangedPixels: 0, outsideEditableChangedPixels: 0, geometry: this.branch.rearDropDiagnostics,
          nativeReadback: 'Owned native beauty and paired camera pixels; native/branch PBO, synchronous fallback and prewarm transfers are counted separately',
          nativeBaselineCopyChangedChannels: null, nativeBranchCopyChangedChannels: null,
          zeroDropBranchSkipped: dropM === 0,
          rootLocalBounds: bounds ? {optical: {min: bounds.optical.min.toArray(), max: bounds.optical.max.toArray()},
            originalArms: bounds.originalArms.map(box => ({min: box.min.toArray(), max: box.max.toArray()})),
            candidateArms: bounds.candidateArms.map(box => ({min: box.min.toArray(), max: box.max.toArray()}))} : null,
          geometryToClipMatrix: protectionProjection(baseline.eyewearMatrix, this.eyewear.offsetCm,
            this.ownedFrame.width / this.ownedFrame.height).toArray(),
          sourceCameraAspect: this.ownedFrame.width / this.ownedFrame.height,
          protection: structuredClone(protection), optics: 'authoritative untouched perfecto pixel copy',
          throughLensAndReflections: 'retained from perfecto'};
      } catch (error) {
        // A branch/mask/replay problem never licenses even one optical pixel edit.
        if (input.options.cropBranchReadback) this.stages.efficiencyLab.branchRegion.fallback ??=
          error instanceof Error ? error.message : String(error);
        if (input.options.omitBranchLenses) this.stages.efficiencyLab.branchLenses.fallback ??=
          error instanceof Error ? error.message : String(error);
        this.candidatePixels = this.baselinePixels; this.protection = null;
        this.branchPixels = null;
        this.croppedBranch = false; this.diagnosticBranchPixels = null;
        let branchClearFailure: string | null = null;
        try { this.branch.clearOwnedFrame(); }
        catch (clearError) { branchClearFailure = clearError instanceof Error ? clearError.message : 'Branch clearing failed'; }
        this.appliedRearDrop = {method: REAR_DROP_METHOD, dropM: 0};
        this.lastDiagnostics = {method: 'temple-optics-copy-v1', sequence: this.sequence,
          fallback: error instanceof Error ? error.message : 'Candidate unavailable', changedPixels: 0, appliedDropM: 0, branchClearFailure};
      }
      this.publish();
      return visible;
    } catch (error) {
      this.clearOwnedPresentation();
      try { this.branch.clearOwnedFrame(); } catch { /* No branch pixels or metadata remain publishable. */ }
      this.displayContext.clearRect(0, 0, this.display.width, this.display.height);
      throw error;
    } finally {this.stages.totalMs = performance.now() - started;}
  }

  private publish(): void {
    const started = performance.now();
    const pixels = this.ownedPixels;
    if (pixels && this.publishes) this.displayContext.putImageData(pixels, 0, 0);
    this.stages.publishMs += performance.now() - started;
  }

  /** Explicit user download only. No camera image is uploaded or persisted here. */
  exportDiagnostic(): Record<string, unknown> | null {
    const snapshot = this.captureSnapshot;
    if (!snapshot || !this.pairedDetection || !this.baselinePixels || !this.candidatePixels || this.disposed) return null;
    let diagnosticBranchReadback = {rerendered: false, calls: 0, bytes: 0, source: 'retained-full-read'};
    let rawBranch = this.branchPixels;
    if (this.croppedBranch && this.branchPixels) {
      if (!this.diagnosticBranchPixels) {
        // The native default framebuffer can be discarded after its render task.
        // Re-render the retained exact pair for explicit raw diagnostics; never
        // mislabel baseline-padded cropped composition input as a full branch.
        const rendered = this.branch.present(this.ownedFrame, this.pairedDetection, Array.from(snapshot.surfacePositions),
          snapshot.templeClip ?? null, snapshot.templeVisibility ?? null, this.appliedRearDrop, this.branchLensOmitted);
        const geometry = this.branch.captureSnapshot;
        if (!rendered || !geometry || !sameGeometry(geometry, snapshot)
          || this.branchCanvas.width !== this.baselinePixels.width || this.branchCanvas.height !== this.baselinePixels.height)
          throw new Error('The full diagnostic branch differs from its retained pair.');
        this.diagnosticBranchPixels = this.branchReader.read(this.branchCanvas);
        diagnosticBranchReadback = {rerendered: true, calls: 1, bytes: this.diagnosticBranchPixels.data.byteLength,
          source: 'same-pair-full-rerender'};
      } else diagnosticBranchReadback.source = 'cached-same-pair-full-rerender';
      rawBranch = this.diagnosticBranchPixels;
    }
    this.baselineReadback.width = this.baselinePixels.width; this.baselineReadback.height = this.baselinePixels.height;
    this.baselineContext.putImageData(this.baselinePixels, 0, 0);
    if (rawBranch) {
      this.branchReadback.width = rawBranch.width; this.branchReadback.height = rawBranch.height;
      this.branchContext.putImageData(rawBranch, 0, 0);
    }
    const output = document.createElement('canvas'); output.width = this.candidatePixels.width; output.height = this.candidatePixels.height;
    context2D(output).putImageData(this.candidatePixels, 0, 0);
    const candidatePngDataUrl = output.toDataURL('image/png');
    output.width = output.height = 0;
    return {schema: 'ar_v4_sagittal_diagnostic_v1', exportedAt: new Date().toISOString(), sequence: this.sequence,
      // acceptedCommit retains its historical baseline-reference meaning.
      acceptedCommit: '31df28eb8ca0c698467fd9bfb16f737f9a74e915', candidateAccepted: false,
      baselineCommit: '31df28eb8ca0c698467fd9bfb16f737f9a74e915', acceptedRevisionLabel: 'performance Test 2; unaccepted',
      width: this.ownedFrame.width, height: this.ownedFrame.height, renderWidth: this.baselinePixels.width, renderHeight: this.baselinePixels.height,
      sourcePngDataUrl: this.ownedFrame.toDataURL('image/png'), detection: structuredClone(this.pairedDetection),
      originalPerfectoPngDataUrl: this.baselineReadback.toDataURL('image/png'), candidatePngDataUrl,
      branchPngDataUrl: rawBranch ? this.branchReadback.toDataURL('image/png') : null,
      diagnosticBranchReadback,
      snapshot: {...snapshot, surfacePositions: Array.from(snapshot.surfacePositions)},
      candidateRearDrop: structuredClone(this.appliedRearDrop), protection: structuredClone(this.protection),
      diagnostics: this.diagnostics, stages: this.stageTimings, selectedVariant: this.selectedVariant,
      eyewear: this.eyewear, projection: {...VIRTUAL_CAMERA, calibrated: false}, nativeSamples: this.nativeSamples,
      evidence: 'Exact original camera/detection pair; unchanged perfecto surface; experimental posterior geometry and protected final pixel composition.'};
  }

  private clearOwnedPresentation(): void {
    this.presentationFrame = null; this.activeSource = undefined; this.presentationBorrowed = false;
    this.pairedDetection = null; this.snapshot = null; this.baselinePixels = this.candidatePixels = null;
    this.branchPixels = null;
    this.croppedBranch = false; this.diagnosticBranchPixels = null; this.branchLensOmitted = false;
    this.cameraPixels = null;
    this.appliedRearDrop = null; this.protection = null; this.lastDiagnostics = null;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const cleanup of this.cleanupListeners.splice(0)) cleanup();
    this.clearOwnedPresentation();
    this.preparedNative = null; this.nextSubmission = undefined;
    this.branchPbo?.dispose(); this.branchPbo = null;
    this.branch.dispose(); this.perfecto.dispose();
    this.baselineReader.dispose(); this.branchReader.dispose();
    for (const canvas of [this.pairedFrame, this.baselineReadback, this.branchReadback, this.baselineCanvas, this.branchCanvas]) canvas.width = canvas.height = 0;
    this.displayContext.clearRect(0, 0, this.display.width, this.display.height);
  }
}
