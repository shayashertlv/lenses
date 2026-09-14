import {TryOnRenderer as PerfectoRenderer} from '../../src/render/renderer.ts';
import type {CaptureGeometry as PerfectoGeometry} from '../../src/render/renderer.ts';
import {TryOnRenderer as BranchRenderer} from './branch-renderer.ts';
import {rearDropForPose} from './rear-drop.ts';
import {REAR_DROP_METHOD, validateRearDrop, validateProtection} from './contracts.ts';
import type {RearDropConfiguration, ProtectionConfiguration} from './contracts.ts';
import {composeProtectedPixels, createProtection, protectionProjection} from './protection.ts';
import type {Detection} from '../../src/runtime/detector.ts';
import type {TempleClipConfiguration} from '../../src/render/temple-clip.ts';
import type {TempleVisibilityConfiguration} from '../../src/render/temple-visibility.ts';
import {DEFAULT_EYEWEAR_ID} from '../../src/render/eyewear.ts';
import type {EyewearId} from '../../src/render/eyewear.ts';
import {VIRTUAL_CAMERA} from '../../src/render/projection.ts';

export type {RearDropConfiguration, ProtectionConfiguration} from './contracts.ts';
export type ComparisonVariant = 'perfecto' | 'candidate';
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

/** Preserve native framebuffer bytes even if a browser's canvas copy converts. */
function retainNativePixels(native: HTMLCanvasElement, scratch: CanvasRenderingContext2D): {image: ImageData; canvasCopyChangedChannels: number} {
  const {width, height} = native;
  scratch.drawImage(native, 0, 0);
  const copied = scratch.getImageData(0, 0, width, height);
  const gl = native.getContext('webgl2');
  if (!gl || gl.isContextLost()) throw new Error('The native comparison framebuffer is unavailable.');
  const rgba = new Uint8Array(width * height * 4);
  gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, rgba);
  const topDown = new Uint8ClampedArray(rgba.length);
  for (let y = 0; y < height; y++) topDown.set(rgba.subarray((height - y - 1) * width * 4, (height - y) * width * 4), y * width * 4);
  let canvasCopyChangedChannels = 0;
  for (let index = 0; index < topDown.length; index++) canvasCopyChangedChannels += Number(topDown[index] !== copied.data[index]);
  const image = canvasCopyChangedChannels ? new ImageData(topDown, width, height) : copied;
  if (canvasCopyChangedChannels) scratch.putImageData(image, 0, 0);
  return {image, canvasCopyChangedChannels};
}

/**
 * Pixel prototype: authoritative perfecto remains an untouched native WebGL
 * renderer. The independent branch never shares its GPU state or scene graph.
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
  private branchPixelsReady = false;
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
    perfecto: PerfectoRenderer, branch: BranchRenderer) {
    this.display = display; this.baselineCanvas = baselineCanvas; this.branchCanvas = branchCanvas;
    this.perfecto = perfecto; this.branch = branch;
    this.displayContext = context2D(display);
    const gl = baselineCanvas.getContext('webgl2');
    const samples: unknown = gl?.getParameter(gl.SAMPLES);
    this.sampleCount = typeof samples === 'number' ? samples : 0;
  }

  static async create(display: HTMLCanvasElement, signal: AbortSignal, eyewearId: EyewearId = DEFAULT_EYEWEAR_ID): Promise<TryOnRenderer> {
    if (signal.aborted) throw abortError();
    const baselineCanvas = document.createElement('canvas'), branchCanvas = document.createElement('canvas');
    let perfecto: PerfectoRenderer | null = null, branch: BranchRenderer | null = null, instance: TryOnRenderer | null = null;
    try {
      perfecto = await PerfectoRenderer.create(baselineCanvas, signal, eyewearId);
      if (signal.aborted) throw abortError();
      branch = await BranchRenderer.create(branchCanvas, signal, eyewearId);
      if (signal.aborted) throw abortError();
      instance = new TryOnRenderer(display, baselineCanvas, branchCanvas, perfecto, branch);
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
      const visible = this.perfecto.present(this.pairedFrame, paired, recordedSurface, recordedTempleClip, recordedTempleVisibility);
      const width = this.baselineCanvas.width, height = this.baselineCanvas.height;
      for (const canvas of [this.baselineReadback, this.branchReadback, this.display]) {
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      }
      // Read B immediately, before the independent branch does any GPU work.
      const retainedBaseline = retainNativePixels(this.baselineCanvas, this.baselineContext);
      this.baselinePixels = retainedBaseline.image;
      this.candidatePixels = this.baselinePixels;
      this.snapshot = this.perfecto.captureSnapshot;
      this.pairedDetection = paired;
      if (!visible || !this.snapshot || !paired.matrix) {
        let branchClearFailure: string | null = null;
        try { this.branch.present(this.pairedFrame, {landmarks: [], matrix: null, inferenceMs: 0}); }
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
        const branchVisible = this.branch.present(this.pairedFrame, paired, Array.from(baseline.surfacePositions),
          baseline.templeClip ?? null, baseline.templeVisibility ?? null, {method: REAR_DROP_METHOD, dropM});
        if (!branchVisible || this.branchCanvas.width !== width || this.branchCanvas.height !== height) throw new Error('The branch presentation differs in size or face ownership.');
        const branchSnapshot = this.branch.captureSnapshot;
        if (!branchSnapshot || branchSnapshot.surfacePositions.some((value, index) => value !== baseline.surfacePositions[index])
          || branchSnapshot.eyewearMatrix.some((value, index) => value !== baseline.eyewearMatrix[index])) throw new Error('The branch changed the paired surface or pose.');
        const retainedBranch = retainNativePixels(this.branchCanvas, this.branchContext);
        this.branchPixelsReady = true;
        const branchPixels = retainedBranch.image;
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
        const composed = dropM > 0 ? composeProtectedPixels(this.baselinePixels.data, branchPixels.data, protection)
          : {pixels: this.baselinePixels.data.slice(), changedPixels: 0, editablePixels: 0, protectedPixels: 0};
        this.candidatePixels = new ImageData(new Uint8ClampedArray(composed.pixels), width, height);
        this.protection = protection;
        this.appliedRearDrop = {method: REAR_DROP_METHOD, dropM};
        this.lastDiagnostics = {method: 'temple-optics-copy-v1', sequence: this.sequence, fallback: null,
          requestedDropM: dropM, appliedDropM: dropM, changedPixels: composed.changedPixels,
          editablePixels: composed.editablePixels, protectedPixels: composed.protectedPixels,
          protectedChangedPixels: 0, outsideEditableChangedPixels: 0, geometry: this.branch.rearDropDiagnostics,
          nativeBaselineCopyChangedChannels: retainedBaseline.canvasCopyChangedChannels,
          nativeBranchCopyChangedChannels: retainedBranch.canvasCopyChangedChannels,
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
        this.appliedRearDrop = {method: REAR_DROP_METHOD, dropM: 0};
        this.lastDiagnostics = {method: 'temple-optics-copy-v1', sequence: this.sequence,
          fallback: error instanceof Error ? error.message : 'Candidate unavailable', changedPixels: 0, appliedDropM: 0};
      }
      this.publish();
      return visible;
    } catch (error) {
      this.clearOwnedPresentation();
      this.displayContext.clearRect(0, 0, this.display.width, this.display.height);
      throw error;
    }
  }

  private publish(): void {
    const pixels = this.replaying || this.selectedVariant === 'candidate' ? this.candidatePixels : this.baselinePixels;
    if (pixels && !this.disposed) this.displayContext.putImageData(pixels, 0, 0);
  }

  /** Explicit user download only. No camera image is uploaded or persisted here. */
  exportDiagnostic(): Record<string, unknown> | null {
    const snapshot = this.captureSnapshot;
    if (!snapshot || !this.pairedDetection || !this.baselinePixels || !this.candidatePixels || this.disposed) return null;
    const output = document.createElement('canvas'); output.width = this.candidatePixels.width; output.height = this.candidatePixels.height;
    context2D(output).putImageData(this.candidatePixels, 0, 0);
    const candidatePngDataUrl = output.toDataURL('image/png');
    output.width = output.height = 0;
    return {schema: 'ar_v4_sagittal_diagnostic_v1', exportedAt: new Date().toISOString(), sequence: this.sequence,
      // acceptedCommit retains its historical baseline-reference meaning.
      acceptedCommit: '31df28eb8ca0c698467fd9bfb16f737f9a74e915', candidateAccepted: true,
      baselineCommit: '31df28eb8ca0c698467fd9bfb16f737f9a74e915', acceptedRevisionLabel: 'perfect_temples',
      width: this.pairedFrame.width, height: this.pairedFrame.height, renderWidth: this.baselinePixels.width, renderHeight: this.baselinePixels.height,
      sourcePngDataUrl: this.pairedFrame.toDataURL('image/png'), detection: structuredClone(this.pairedDetection),
      originalPerfectoPngDataUrl: this.baselineReadback.toDataURL('image/png'), candidatePngDataUrl,
      branchPngDataUrl: this.branchPixelsReady ? this.branchReadback.toDataURL('image/png') : null,
      snapshot: {...snapshot, surfacePositions: Array.from(snapshot.surfacePositions)},
      candidateRearDrop: structuredClone(this.appliedRearDrop), protection: structuredClone(this.protection),
      diagnostics: this.diagnostics, selectedVariant: this.selectedVariant,
      eyewear: this.eyewear, projection: {...VIRTUAL_CAMERA, calibrated: false}, nativeSamples: this.nativeSamples,
      evidence: 'Exact original camera/detection pair; unchanged perfecto surface; experimental posterior geometry and protected final pixel composition.'};
  }

  private clearOwnedPresentation(): void {
    this.pairedDetection = null; this.snapshot = null; this.baselinePixels = this.candidatePixels = null;
    this.branchPixelsReady = false;
    this.appliedRearDrop = null; this.protection = null; this.lastDiagnostics = null;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const cleanup of this.cleanupListeners.splice(0)) cleanup();
    this.clearOwnedPresentation();
    this.branch.dispose(); this.perfecto.dispose();
    for (const canvas of [this.pairedFrame, this.baselineReadback, this.branchReadback, this.baselineCanvas, this.branchCanvas]) canvas.width = canvas.height = 0;
    this.displayContext.clearRect(0, 0, this.display.width, this.display.height);
  }
}
