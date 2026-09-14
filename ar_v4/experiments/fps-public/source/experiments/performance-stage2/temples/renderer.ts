import {TryOnRenderer as PerfectoRenderer} from '../native/renderer.ts';
import type {CaptureGeometry as PerfectoGeometry} from '../native/renderer.ts';
import type {NativeStageTimings} from '../native/renderer.ts';
import {TryOnRenderer as BranchRenderer} from '../../performance-candidate/temples/branch-renderer.ts';
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
import {NativePixelReader} from '../native/native-pixels.ts';

export type {RearDropConfiguration, ProtectionConfiguration} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
export type ComparisonVariant = 'perfecto' | 'candidate';
export interface RendererOptions {publish?: boolean;}
export interface TempleStageTimings {
  sourceOwnershipMs: number; nativePresentMs: number; baselineReadbackMs: number;
  branchPresentMs: number; branchReadbackMs: number; protectionMs: number; compositionMs: number;
  publishMs: number; totalMs: number; baselineReadbackCalls: number; branchReadbackCalls: number;
  baselineReadbackBytes: number; branchReadbackBytes: number; zeroDropBranchSkipped: boolean;
  native: NativeStageTimings | null;
}
const emptyStages = (): TempleStageTimings => ({sourceOwnershipMs: 0, nativePresentMs: 0, baselineReadbackMs: 0,
  branchPresentMs: 0, branchReadbackMs: 0, protectionMs: 0, compositionMs: 0, publishMs: 0, totalMs: 0,
  baselineReadbackCalls: 0, branchReadbackCalls: 0, baselineReadbackBytes: 0, branchReadbackBytes: 0,
  zeroDropBranchSkipped: false, native: null});
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

  present(frame: HTMLCanvasElement, detection: Detection, recordedSurface?: readonly number[],
    recordedTempleClip?: TempleClipConfiguration | null, recordedTempleVisibility?: TempleVisibilityConfiguration | null,
    recordedRearDrop?: RearDropConfiguration | null, recordedProtection?: ProtectionConfiguration | null): boolean {
    if (this.disposed) return false;
    this.clearOwnedPresentation();
    const started = performance.now(); this.stages = emptyStages();
    this.sequence++;
    this.replaying = recordedSurface !== undefined || recordedTempleClip !== undefined || recordedTempleVisibility !== undefined
      || recordedRearDrop !== undefined || recordedProtection !== undefined;
    try {
      if (frame.width <= 0 || frame.height <= 0) throw new Error('The paired camera frame is empty.');
      if (this.pairedFrame.width !== frame.width || this.pairedFrame.height !== frame.height) {
        this.pairedFrame.width = frame.width; this.pairedFrame.height = frame.height;
      }
      this.pairedContext.drawImage(frame, 0, 0);
      const paired = structuredClone(detection);
      this.stages.sourceOwnershipMs = performance.now() - started;
      const nativeStarted = performance.now();
      const visible = this.perfecto.present(this.pairedFrame, paired, recordedSurface, recordedTempleClip, recordedTempleVisibility, this.cameraRequested);
      this.stages.nativePresentMs = performance.now() - nativeStarted;
      this.stages.native = this.perfecto.stageTimings;
      const width = this.baselineCanvas.width, height = this.baselineCanvas.height;
      for (const canvas of [this.display]) {
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      }
      // Read B immediately, before the independent branch does any GPU work.
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
      this.appliedRearDrop = {method: REAR_DROP_METHOD, dropM: 0};
      try {
        if (requested) validateRearDrop(requested);
        const dropM = requested?.dropM ?? 0;
        const baseline = this.snapshot;
        if (dropM === 0) {
          // Actual zero-drop bounds still establish this pair's hair permissions.
          // Invalidate old GPU/snapshot ownership without drawing another image.
          this.branch.clearOwnedFrame();
          this.stages.zeroDropBranchSkipped = true;
        } else {
          const branchStarted = performance.now();
          const branchVisible = this.branch.present(this.pairedFrame, paired, Array.from(baseline.surfacePositions),
            baseline.templeClip ?? null, baseline.templeVisibility ?? null, {method: REAR_DROP_METHOD, dropM});
          this.stages.branchPresentMs = performance.now() - branchStarted;
          if (!branchVisible || this.branchCanvas.width !== width || this.branchCanvas.height !== height) throw new Error('The branch presentation differs in size or face ownership.');
          const branchSnapshot = this.branch.captureSnapshot;
          if (!branchSnapshot || branchSnapshot.surfacePositions.some((value, index) => value !== baseline.surfacePositions[index])
            || branchSnapshot.eyewearMatrix.some((value, index) => value !== baseline.eyewearMatrix[index])) throw new Error('The branch changed the paired surface or pose.');
          const readStarted = performance.now();
          this.branchPixels = this.branchReader.read(this.branchCanvas);
          this.stages.branchReadbackMs = performance.now() - readStarted;
          this.stages.branchReadbackCalls = 1; this.stages.branchReadbackBytes = this.branchPixels.data.byteLength;
        }
        const protectionStarted = performance.now();
        const bounds = this.branch.rearDropBounds;
        const protection = bounds ? createProtection(bounds, baseline.eyewearMatrix, this.eyewear.offsetCm, paired.landmarks,
          width, height, this.pairedFrame.width / this.pairedFrame.height) : null;
        if (!protection) throw new Error('The current optical/nasal protection could not be established.');
        if (this.replaying && dropM > 0) {
          if (!recordedProtection) throw new Error('The saved candidate is missing its actual protected regions.');
          validateProtection(recordedProtection, width, height);
          // Saved masks cannot weaken current minimum optical/nasal protection.
          if (JSON.stringify(recordedProtection) !== JSON.stringify(protection)) throw new Error('The saved candidate protection does not match this exact presentation.');
        }
        this.stages.protectionMs = performance.now() - protectionStarted;
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
          nativeReadback: 'Test 2 shared native context with two direct readbacks when hair requested; direct native beauty fallback',
          nativeBaselineCopyChangedChannels: null, nativeBranchCopyChangedChannels: null,
          zeroDropBranchSkipped: dropM === 0,
          rootLocalBounds: bounds ? {optical: {min: bounds.optical.min.toArray(), max: bounds.optical.max.toArray()},
            originalArms: bounds.originalArms.map(box => ({min: box.min.toArray(), max: box.max.toArray()})),
            candidateArms: bounds.candidateArms.map(box => ({min: box.min.toArray(), max: box.max.toArray()}))} : null,
          geometryToClipMatrix: protectionProjection(baseline.eyewearMatrix, this.eyewear.offsetCm,
            this.pairedFrame.width / this.pairedFrame.height).toArray(),
          sourceCameraAspect: this.pairedFrame.width / this.pairedFrame.height,
          protection: structuredClone(protection), optics: 'authoritative untouched perfecto pixel copy',
          throughLensAndReflections: 'retained from perfecto'};
      } catch (error) {
        // A branch/mask/replay problem never licenses even one optical pixel edit.
        this.candidatePixels = this.baselinePixels; this.protection = null;
        this.branchPixels = null;
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
    this.baselineReadback.width = this.baselinePixels.width; this.baselineReadback.height = this.baselinePixels.height;
    this.baselineContext.putImageData(this.baselinePixels, 0, 0);
    if (this.branchPixels) {
      this.branchReadback.width = this.branchPixels.width; this.branchReadback.height = this.branchPixels.height;
      this.branchContext.putImageData(this.branchPixels, 0, 0);
    }
    const output = document.createElement('canvas'); output.width = this.candidatePixels.width; output.height = this.candidatePixels.height;
    context2D(output).putImageData(this.candidatePixels, 0, 0);
    const candidatePngDataUrl = output.toDataURL('image/png');
    output.width = output.height = 0;
    return {schema: 'ar_v4_sagittal_diagnostic_v1', exportedAt: new Date().toISOString(), sequence: this.sequence,
      // acceptedCommit retains its historical baseline-reference meaning.
      acceptedCommit: '31df28eb8ca0c698467fd9bfb16f737f9a74e915', candidateAccepted: false,
      baselineCommit: '31df28eb8ca0c698467fd9bfb16f737f9a74e915', acceptedRevisionLabel: 'performance Test 2; unaccepted',
      width: this.pairedFrame.width, height: this.pairedFrame.height, renderWidth: this.baselinePixels.width, renderHeight: this.baselinePixels.height,
      sourcePngDataUrl: this.pairedFrame.toDataURL('image/png'), detection: structuredClone(this.pairedDetection),
      originalPerfectoPngDataUrl: this.baselineReadback.toDataURL('image/png'), candidatePngDataUrl,
      branchPngDataUrl: this.branchPixels ? this.branchReadback.toDataURL('image/png') : null,
      snapshot: {...snapshot, surfacePositions: Array.from(snapshot.surfacePositions)},
      candidateRearDrop: structuredClone(this.appliedRearDrop), protection: structuredClone(this.protection),
      diagnostics: this.diagnostics, stages: this.stageTimings, selectedVariant: this.selectedVariant,
      eyewear: this.eyewear, projection: {...VIRTUAL_CAMERA, calibrated: false}, nativeSamples: this.nativeSamples,
      evidence: 'Exact original camera/detection pair; unchanged perfecto surface; experimental posterior geometry and protected final pixel composition.'};
  }

  private clearOwnedPresentation(): void {
    this.pairedDetection = null; this.snapshot = null; this.baselinePixels = this.candidatePixels = null;
    this.branchPixels = null;
    this.cameraPixels = null;
    this.appliedRearDrop = null; this.protection = null; this.lastDiagnostics = null;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const cleanup of this.cleanupListeners.splice(0)) cleanup();
    this.clearOwnedPresentation();
    this.branch.dispose(); this.perfecto.dispose();
    this.baselineReader.dispose(); this.branchReader.dispose();
    for (const canvas of [this.pairedFrame, this.baselineReadback, this.branchReadback, this.baselineCanvas, this.branchCanvas]) canvas.width = canvas.height = 0;
    this.displayContext.clearRect(0, 0, this.display.width, this.display.height);
  }
}
