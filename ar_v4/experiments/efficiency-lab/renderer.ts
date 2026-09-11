import {TryOnRenderer as AcceptedRenderer} from './temples/renderer.ts';
import type {CaptureGeometry as AcceptedGeometry, TempleStageTimings} from './temples/renderer.ts';
import {requireSharedCamera} from '../performance-stage2/background.ts';
import {DEFAULT_SPEED_OPTIONS, normalizeSpeedOptions, assertSourceFrameCurrent, sourcePixelsOpaque} from './speed-options.ts';
import type {NativeSpeedOptions, OwnedSourceFrame, SpeedFrameInput} from './speed-options.ts';
import {HAIR_ARM_POLICY} from '../hair-arm-preview/compose.ts';
import type {HairArmResult, PairIdentity, PixelCheck} from '../hair-arm-preview/compose.ts';
import type {LiveHairArmInput as HairArmInput, HairMask} from '../hair-live-preview/live-mask.ts';
import type {PixelRect} from '../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import {DEFAULT_EYEWEAR_ID} from '../../references/perfect-temples/src/render/eyewear.ts';
import type {EyewearId} from '../../references/perfect-temples/src/render/eyewear.ts';
import {copyHairMask, liveNasalRoi} from '../hair-live-preview/ownership.ts';
import type {LiveVariant, OwnedPixels} from '../hair-live-preview/ownership.ts';
import {composeHairArmsFast, checkHairProtection, CompositionScratch} from '../performance-candidate/fast-compose.ts';
import type {CompositionAllocationStats} from '../performance-candidate/fast-compose.ts';
import {loadTempleContinuityModel, projectTempleContinuity, findDetachedTemplePixels, TEMPLE_CONTINUITY_POLICY} from '../hair-live-preview/continuity.ts';
import type {TempleContinuityModel, ContinuityDiagnostics} from '../hair-live-preview/continuity.ts';
const CANDIDATE_REVISION = 'Efficiency lab: separate experiments based on owner-selected G Combined';
export type {HairMask, PairIdentity, LiveVariant};
export type {NativeSpeedOptions, OwnedSourceFrame, SpeedFrameInput};
export type HairModelContract = HairArmInput['expectedModel'];
export const LIVE_HAIR_POLICY = Object.freeze({...HAIR_ARM_POLICY, method: 'hair-arm-continuity-preview-v2',
  categoryCompositionMethod: HAIR_ARM_POLICY.method, continuity: TEMPLE_CONTINUITY_POLICY,
  statistics: 'statistics describes category composition; changedPixels includes the additional bounded continuity removal'});
export interface LiveHairTimings {
  acceptedRenderMs: number; acceptedReadbackMs: number;
  /** Legacy field: Test 2 validates already owned clean pixels here. The clean
   * draw/readback is measured in candidatePerformance.nativePipeline.native. */
  cleanCameraMs: number;
  composeMs: number;
  continuityMs: number; finalChecksMs: number; publishMs: number; totalMs: number;
  prepareMs: number; pendingWaitMs: number; finishMs: number; workMs: number;
}

export interface LiveHairStats {
  sequence: number; hasFace: boolean; hasMask: boolean; maskOutputMode: 'full' | 'category-only' | null;
  maskStatus: 'ready' | 'missing' | 'rejected' | 'no-face';
  fallbackReason: string | null; changedPixels: number; selectedVariant: LiveVariant;
  statistics: HairArmResult['statistics'] | null; backgroundReferenceCheck: PixelCheck | null;
  protectedCheck: PixelCheck | null; noseCheck: PixelCheck | null; outsideEditableCheck: PixelCheck | null;
  backgroundPreservationCheck: PixelCheck | null; acceptedDiagnostics: Record<string, unknown> | null;
  timings: LiveHairTimings;
  continuity: ContinuityDiagnostics | null;
  candidatePerformance: CompositionAllocationStats & {
    acceptedPixelReadbacksAvoided: number; acceptedPixelBytesBorrowed: number;
    diagnosticWeightBytesAvoided: number;
    sourceCopyMs: number; hairRequested: boolean; eagerCleanWithoutMask: boolean;
    nativePipeline: TempleStageTimings; cpuReadbackCalls: number; cpuReadbackBytes: number;
    cleanCameraContextsAvoided: number; sourceTextureUploadsAvoided: number;
    speedLab: {options: NativeSpeedOptions; sourceCanvasBorrowed: boolean; sourceCopyBytesAvoided: number;
      sourceIdentity: {sessionId: number | string; generation: number; sourceSHA256: string} | null;
      sourceFallbackReason: string | null};
  };
}
export interface HeldHairInput {source: HTMLCanvasElement; detection: Detection; pair: PairIdentity; expectedModel: HairModelContract;}
export interface CaptureGeometry extends AcceptedGeometry {
  hairPreview: {method: string; variant: LiveVariant; applied: boolean; sourceSHA256: string; detectionSHA256: string;
    model: string | null; modelSHA256: string | null; categorySHA256: string | null; confidenceSHA256: string | null;
    width: number | null; height: number | null; noseRoi: PixelRect | null; fallbackReason: string | null;
    continuity: ContinuityDiagnostics | null;};
}
const errorMessage = (value: unknown): string => value instanceof Error ? value.message : String(value);
const abortError = (): DOMException => new DOMException('Live hair preview startup was cancelled.', 'AbortError');
const context = (canvas: HTMLCanvasElement): CanvasRenderingContext2D => {
  const result = canvas.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'});
  if (!result) throw new Error('The live hair pixel surface is unavailable.'); return result;
};
const png = (value: OwnedPixels): string => {
  const canvas = document.createElement('canvas'); canvas.width = value.width; canvas.height = value.height;
  context(canvas).putImageData(new ImageData(new Uint8ClampedArray(value.pixels), value.width, value.height), 0, 0); return canvas.toDataURL('image/png');
};
const base64 = (bytes: Uint8Array): string => {
  let binary = ''; for (let offset = 0; offset < bytes.length; offset += 0x4000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x4000));
  return btoa(binary);
};
const ownedImage = (pixels: Uint8ClampedArray, width: number, height: number): ImageData => {
  // Composition allocates this buffer internally. ImageData can borrow it safely;
  // exports expose encoded pixels, and no caller receives these mutable bytes.
  if (!(pixels.buffer instanceof ArrayBuffer)) throw new Error('Live output requires an owned nonshared pixel buffer.');
  return new ImageData(new Uint8ClampedArray(pixels.buffer, pixels.byteOffset, pixels.length), width, height);
};

/** Candidate uses an owned native pixel view; hair retains the same paired
 * composition and full final guards. It requires independent visual acceptance. */
export class LiveHairRenderer {
  private readonly displayContext: CanvasRenderingContext2D;
  private readonly ownedSourceCanvas = document.createElement('canvas');
  private readonly sourceContext = context(this.ownedSourceCanvas);
  private borrowedSource: OwnedSourceFrame | null = null;
  private liveSourceLease: OwnedSourceFrame | null = null;
  private retainedSourcePixels: ImageData | null = null;
  /** Deferred contents of Test2's persistent source canvas, including a failed
   * presentation whose source copy would already have happened. */
  private sourceHistoryPixels: ImageData | null = null;
  private sourceMaterialized = true;
  private preparationInput: SpeedFrameInput | null = null;
  private frameOptions: Readonly<NativeSpeedOptions> = DEFAULT_SPEED_OPTIONS;
  private preparing = false;
  private generation = 0;
  private readonly sourceGenerations = new Map<number | string, number>();
  private before: ImageData | null = null;
  private after: ImageData | null = null;
  private background: OwnedPixels | null = null;
  private preparationHairEnabled: boolean | null = null;
  private selected: LiveVariant = 'hair';
  private disposed = false;
  private sequence = 0;
  private latestStats: LiveHairStats | null = null;
  private snapshot: AcceptedGeometry | null = null;
  private detection: Detection | null = null;
  private pair: PairIdentity | null = null;
  private mask: HairMask | null = null;
  private model: HairModelContract | null = null;
  private noseRoi: PixelRect | null = null;
  private cleanup: (() => void)[] = [];
  private continuityModel: TempleContinuityModel | null = null;
  private continuityUnavailable: string | null = null;
  private pending = false;
  private preparedAtMs = 0;
  private readonly compositionScratch = new CompositionScratch();

  /** A live canvas may be released immediately after finish. Hold/export rebuild
   * this exact source lazily from the transferred independent RGBA snapshot. */
  private get source(): HTMLCanvasElement {
    if (this.borrowedSource && this.pending) return this.borrowedSource.canvas;
    if (!this.sourceMaterialized && this.retainedSourcePixels) {
      this.ownedSourceCanvas.width = this.retainedSourcePixels.width;
      this.ownedSourceCanvas.height = this.retainedSourcePixels.height;
      this.sourceContext.putImageData(this.retainedSourcePixels, 0, 0);
      this.sourceHistoryPixels = null;
      this.sourceMaterialized = true;
    }
    return this.ownedSourceCanvas;
  }

  private constructor(private readonly display: HTMLCanvasElement, private readonly acceptedCanvas: HTMLCanvasElement,
    private readonly accepted: AcceptedRenderer) {
    this.displayContext = context(display);
  }

  static async create(display: HTMLCanvasElement, signal: AbortSignal, eyewearId: EyewearId = DEFAULT_EYEWEAR_ID): Promise<LiveHairRenderer> {
    if (signal.aborted) throw abortError();
    const acceptedCanvas = document.createElement('canvas');
    let accepted: AcceptedRenderer | null = null, instance: LiveHairRenderer | null = null;
    try {
      accepted = await AcceptedRenderer.create(acceptedCanvas, signal, eyewearId, {publish: false});
      if (signal.aborted) throw abortError(); accepted.selectVariant('candidate');
      instance = new LiveHairRenderer(display, acceptedCanvas, accepted);
      try { instance.continuityModel = await loadTempleContinuityModel(accepted.eyewear.assetUrl, accepted.eyewear.templeClipLocalZM, signal); }
      catch (error) { if (signal.aborted) throw abortError(); instance.continuityUnavailable = errorMessage(error); }
      const owner = instance;
      const onAbort = (): void => owner.dispose(); signal.addEventListener('abort', onAbort, {once: true});
      owner.cleanup.push(() => signal.removeEventListener('abort', onAbort));
      const onLost = (event: Event): void => {
        event.preventDefault(); if (owner.disposed) return;
        owner.dispose(); display.dispatchEvent(new Event('webglcontextlost', {cancelable: true}));
      };
      acceptedCanvas.addEventListener('webglcontextlost', onLost); owner.cleanup.push(() => acceptedCanvas.removeEventListener('webglcontextlost', onLost));
      if (signal.aborted) throw abortError(); return owner;
    } catch (error) { if (instance) instance.dispose(); else { accepted?.dispose(); acceptedCanvas.width = acceptedCanvas.height = 0; }
      throw signal.aborted ? abortError() : error; }
  }

  get variant(): LiveVariant { return this.selected; }
  /** One preparation hint; a held pair with a mask should request both variants. */
  setPreparationHairEnabled(enabled: boolean): void {
    if (this.pending) throw new Error('A pending pair cannot change its preparation policy.');
    if (!this.disposed) this.preparationHairEnabled = enabled;
  }
  /** One-shot policy: a checkbox changed during work affects only the next pair. */
  setFrameOptions(options: Partial<NativeSpeedOptions>, source?: OwnedSourceFrame): void {
    if (this.pending) throw new Error('A pending pair cannot change its speed options.');
    if (!this.disposed) this.preparationInput = {options: normalizeSpeedOptions(options), ...(source ? {source} : {})};
  }
  get nativeSamples(): number { return this.accepted.nativeSamples; }
  get eyewear() { return this.accepted.eyewear; }
  get stats(): LiveHairStats | null { return !this.pending && this.latestStats ? {...structuredClone(this.latestStats), selectedVariant: this.selected} : null; }
  get captureSnapshot(): CaptureGeometry | null {
    if (!this.pair || this.disposed || this.pending) return null;
    this.snapshot ??= this.accepted.captureSnapshot;
    if (!this.snapshot) return null;
    return {...structuredClone(this.snapshot), hairPreview: {method: LIVE_HAIR_POLICY.method, variant: this.selected,
      applied: this.selected === 'hair' && (this.latestStats?.changedPixels ?? 0) > 0,
      sourceSHA256: this.pair.sourceSHA256, detectionSHA256: this.pair.detectionSHA256,
      model: this.mask?.model ?? null, modelSHA256: this.mask?.modelSHA256 ?? null,
      categorySHA256: this.mask?.categorySHA256 ?? null, confidenceSHA256: this.mask?.confidenceSHA256 ?? null,
      width: this.mask?.width ?? null, height: this.mask?.height ?? null, noseRoi: this.noseRoi ? {...this.noseRoi} : null,
      fallbackReason: this.latestStats?.fallbackReason ?? null,
      continuity: this.latestStats?.continuity ? structuredClone(this.latestStats.continuity) : null}};
  }
  selectVariant(variant: LiveVariant): void {
    if (variant !== 'accepted' && variant !== 'hair') throw new Error('Unknown live hair comparison variant.');
    if (this.disposed) return; this.selected = variant; this.publish();
  }

  async present(frame: HTMLCanvasElement, detection: Detection, mask: HairMask | null, pair: PairIdentity,
    expectedModel: HairModelContract, frameInput?: SpeedFrameInput): Promise<boolean> {
    if (this.disposed) return false;
    // The convenience path can await native retrieval. Hold callers retain their
    // input, so freeze the mask now instead of consulting mutable caller bytes
    // after that await. Live prepare/finish already has separate worker ownership.
    const pairedMask = mask ? structuredClone(mask) : null;
    // Supplying an exact mask requests both held variants, including when the
    // accepted view is selected or a preceding one-shot hint disabled hair.
    if (mask !== null) this.setPreparationHairEnabled(true);
    await this.prepare(frame, detection, pair, expectedModel, frameInput);
    if (this.disposed) return false;
    return this.finish(pairedMask);
  }

  /** Own and render one accepted pair privately while its independent hair worker runs. */
  async prepare(frame: HTMLCanvasElement, detection: Detection, pair: PairIdentity, expectedModel: HairModelContract,
    frameInput?: SpeedFrameInput): Promise<boolean> {
    if (this.disposed) return false;
    if (this.pending) throw new Error('The preceding prepared image must finish before preparing another pair.');
    const hairRequested = this.preparationHairEnabled ?? this.selected === 'hair';
    this.preparationHairEnabled = null;
    const requestedInput = frameInput ?? this.preparationInput ?? {};
    this.preparationInput = null;
    const started = performance.now();
    // Test2's source-over copy retains the previous opaque source at this size.
    // A borrowed opaque pair skipped that copy; keep its pixels only until the
    // next copy decision so a transparent fallback sees the same canvas history.
    const previousSourcePixels = this.sourceHistoryPixels;
    this.clearOwned(); this.sequence++; this.pending = this.preparing = true; this.preparedAtMs = started;
    const generation = this.generation;
    try {
      this.frameOptions = normalizeSpeedOptions(requestedInput.options);
      if (frame.width <= 0 || frame.height <= 0) throw new Error('The live paired source is empty.');
      const suppliedSource = requestedInput.source;
      const useSource = suppliedSource && (this.frameOptions.fewerCopies || this.frameOptions.reuseSourcePixels) ? suppliedSource : null;
      if (useSource) {
        assertSourceFrameCurrent(useSource, frame, pair.sourceSHA256);
        const previous = this.sourceGenerations.get(useSource.sessionId);
        if (previous !== undefined && useSource.generation <= previous) throw new Error('The source frame generation was already consumed.');
        this.sourceGenerations.set(useSource.sessionId, useSource.generation);
        // RGBA is transferred from the capture owner before prepare and remains
        // independent of the reusable/live canvas for the full held lifetime.
        this.retainedSourcePixels = useSource.rgba;
        this.liveSourceLease = useSource;
      }
      const borrowSource = !!useSource && this.frameOptions.fewerCopies && sourcePixelsOpaque(useSource.rgba);
      if (useSource && borrowSource) {
        this.borrowedSource = useSource; this.sourceMaterialized = false; this.sourceHistoryPixels = useSource.rgba;
      } else {
        const logicalResize = previousSourcePixels && (previousSourcePixels.width !== frame.width || previousSourcePixels.height !== frame.height);
        if (this.ownedSourceCanvas.width !== frame.width || this.ownedSourceCanvas.height !== frame.height || logicalResize) {
          this.ownedSourceCanvas.width = frame.width; this.ownedSourceCanvas.height = frame.height;
        }
        if (previousSourcePixels && previousSourcePixels.width === frame.width && previousSourcePixels.height === frame.height)
          this.sourceContext.putImageData(previousSourcePixels, 0, 0);
        this.sourceContext.drawImage(frame, 0, 0); this.sourceMaterialized = true; this.sourceHistoryPixels = null;
      }
      const ownedDetection = structuredClone(detection), ownedPair = structuredClone(pair), ownedModel = structuredClone(expectedModel);
      const sourceCopyMs = performance.now() - started;
      const lowerSource = useSource ? {...useSource, canvas: this.source,
        isCurrent: () => !this.disposed && this.pending && this.generation === generation && useSource.isCurrent()} : undefined;
      this.accepted.setPreparationHairEnabled(hairRequested);
      const onNativeSubmitted = requestedInput.onNativeSubmitted;
      this.accepted.setFrameOptions(this.frameOptions, lowerSource, onNativeSubmitted ? () => {
        if (this.disposed || !this.pending || generation !== this.generation || useSource && !useSource.isCurrent()) throw abortError();
        onNativeSubmitted();
      } : undefined);
      const acceptedStarted = performance.now();
      const hasFace = this.frameOptions.asyncReadback
        ? await this.accepted.presentAsync(this.source, ownedDetection)
        : this.accepted.present(this.source, ownedDetection);
      if (this.disposed || generation !== this.generation) throw new DOMException('The prepared image no longer owns this renderer.', 'AbortError');
      if (useSource) assertSourceFrameCurrent(useSource, frame, ownedPair.sourceSHA256);
      const acceptedRenderMs = performance.now() - acceptedStarted;
      // This view stays immutable through finish/hold/export. The next prepare
      // clears all references before accepted.present can replace its owner.
      const ownedPixels = this.accepted.ownedPixels;
      if (!ownedPixels || ownedPixels.width <= 0 || ownedPixels.height <= 0
        || ownedPixels.data.length !== ownedPixels.width * ownedPixels.height * 4) {
        throw new Error('The candidate accepted renderer returned no owned pixels.');
      }
      const {width, height} = ownedPixels;
      this.before = ownedPixels; this.after = this.before;
      const acceptedReadbackMs = 0;
      const nativePipeline = this.accepted.stageTimings, sharedReadback = nativePipeline.native?.sharedReadback,
        asynchronousReadback = nativePipeline.native?.speedLab?.pbo;
      const sharedReady = nativePipeline.native?.sharedCameraReady ?? false;
      this.latestStats = {sequence: this.sequence, hasFace, hasMask: false, maskOutputMode: null, maskStatus: hasFace ? 'missing' : 'no-face',
        fallbackReason: hasFace ? 'No paired hair mask is available; showing the accepted frame.' : null,
        changedPixels: 0, selectedVariant: this.selected, statistics: null, backgroundReferenceCheck: null,
        protectedCheck: null, noseCheck: null, outsideEditableCheck: null, backgroundPreservationCheck: null,
        acceptedDiagnostics: null, continuity: null,
        candidatePerformance: {acceptedPixelReadbacksAvoided: 1, acceptedPixelBytesBorrowed: ownedPixels.data.byteLength,
          diagnosticWeightBytesAvoided: 0, regionBytesAllocated: 0, coordinateBytesAllocated: 0,
          regionsReused: false, coordinatesReused: false, sourceCopyMs, hairRequested, eagerCleanWithoutMask: false,
          nativePipeline, cpuReadbackCalls: nativePipeline.baselineReadbackCalls + nativePipeline.branchReadbackCalls
            + (sharedReadback?.readbackCalls ?? 0) + (nativePipeline.speedLab?.prewarmReadbackCalls ?? 0)
            + (asynchronousReadback?.retrievedCalls ?? 0) + (nativePipeline.efficiencyLab.branchPbo?.retrievedCalls ?? 0),
          cpuReadbackBytes: nativePipeline.baselineReadbackBytes + nativePipeline.branchReadbackBytes
            + (sharedReadback?.readbackBytes ?? 0) + (nativePipeline.speedLab?.prewarmReadbackBytes ?? 0)
            + (asynchronousReadback?.retrievedBytes ?? 0) + (nativePipeline.efficiencyLab.branchPbo?.retrievedBytes ?? 0),
          cleanCameraContextsAvoided: Number(sharedReady), sourceTextureUploadsAvoided: Number(sharedReady),
          speedLab: {options: {...this.frameOptions}, sourceCanvasBorrowed: this.borrowedSource !== null,
            sourceCopyBytesAvoided: this.borrowedSource ? useSource!.rgba.data.byteLength : 0,
            sourceIdentity: useSource ? {sessionId: useSource.sessionId, generation: useSource.generation, sourceSHA256: useSource.sourceSHA256} : null,
            sourceFallbackReason: (this.frameOptions.fewerCopies || this.frameOptions.reuseSourcePixels) && !useSource
              ? 'No owned source pixels were supplied; using the original canvas copy.'
              : this.frameOptions.fewerCopies && !borrowSource
                ? 'Source pixels are not fully opaque; using the original canvas copy.' : null}},
        timings: {acceptedRenderMs, acceptedReadbackMs, cleanCameraMs: 0, composeMs: 0,
          continuityMs: 0, finalChecksMs: 0, publishMs: 0, totalMs: 0, prepareMs: 0, pendingWaitMs: 0, finishMs: 0, workMs: 0}};
      if (!hasFace || !ownedDetection.matrix) {
        this.snapshot = null; return hasFace;
      }
      this.detection = ownedDetection; this.pair = ownedPair; this.model = ownedModel;
      this.noseRoi = liveNasalRoi(ownedDetection, width, height);
      return hasFace;
    } catch (error) {
      if (generation === this.generation) {this.clearOwned(); this.displayContext.clearRect(0, 0, this.display.width, this.display.height);}
      throw error;
    } finally {
      if (generation === this.generation) {
        this.preparing = false;
        if (this.latestStats) this.latestStats.timings.prepareMs = performance.now() - started;
      }
    }
  }

  /** Complete only the pending pair, then publish exactly once. No accepted render repeats. */
  finish(mask: HairMask | null): boolean {
    if (this.disposed) return false;
    if (this.preparing) throw new Error('The native preparation must settle before finishing its image.');
    if (!this.pending || !this.before || !this.latestStats) throw new Error('There is no owned prepared image to finish.');
    const started = performance.now(), hasFace = this.latestStats.hasFace;
    this.latestStats.timings.pendingWaitMs = Math.max(0, started - this.preparedAtMs - this.latestStats.timings.prepareMs);
    const {width, height, data: before} = this.before, ownedPair = this.pair, expectedModel = this.model;
    try {
      if (this.liveSourceLease) assertSourceFrameCurrent(this.liveSourceLease, this.liveSourceLease.canvas, ownedPair?.sourceSHA256);
      if (!hasFace || !this.detection?.matrix || !ownedPair || !expectedModel || !mask) {
        this.latestStats.candidatePerformance.eagerCleanWithoutMask = !!this.accepted.ownedCameraPixels;
        this.latestStats.candidatePerformance.cleanCameraContextsAvoided = 0;
        this.latestStats.candidatePerformance.sourceTextureUploadsAvoided = 0;
        this.pending = false; this.publish(); return hasFace;
      }
      try {
        this.snapshot = this.accepted.captureSnapshot;
        const protection = this.snapshot?.protection;
        if (!protection || !this.noseRoi) throw new Error('The accepted optical/nasal protection is unavailable.');
        const cleanStarted = performance.now();
        const clean = requireSharedCamera(this.accepted.ownedCameraPixels, width, height, this.source.width, this.source.height,
          this.latestStats.candidatePerformance.nativePipeline.native?.sharedCameraFailure ?? null);
        this.latestStats.timings.cleanCameraMs = performance.now() - cleanStarted;
        this.background = {width, height, pixels: clean.pixels};
        const input: HairArmInput = {width, height, before, background: clean.pixels, pair: ownedPair,
          geometryPair: {sourceSHA256: ownedPair.sourceSHA256, detectionSHA256: ownedPair.detectionSHA256, eyewearModel: this.eyewear.id},
          mask, expectedModel, protection, noseRoi: this.noseRoi};
        const composeStarted = performance.now(), result = composeHairArmsFast(input,
          {collectEligibleResidualIndices: !!this.continuityModel, collectWeights: false, scratch: this.compositionScratch});
        this.latestStats.timings.composeMs = performance.now() - composeStarted;
        Object.assign(this.latestStats.candidatePerformance, {diagnosticWeightBytesAvoided: width * height * 4},
          result.regions ? this.compositionScratch.lastAllocation : {});
        this.latestStats.backgroundReferenceCheck = result.backgroundReferenceCheck;
        this.latestStats.statistics = result.statistics;
        if (result.fallbackReason) throw new Error(result.fallbackReason);
        const continuityStarted = performance.now();
        const paths = this.continuityModel && this.snapshot ? projectTempleContinuity(this.continuityModel, {
          eyewearMatrix: this.snapshot.eyewearMatrix, offsetCm: this.eyewear.offsetCm, sourceAspect: this.source.width / this.source.height,
          width, height, dropM: this.snapshot.rearDrop?.dropM ?? 0}) : null;
        const continuity = findDetachedTemplePixels({before, background: clean.pixels, after: result.pixels,
          width, height, protection, noseRoi: this.noseRoi, paths,
          ...(result.eligibleResidualIndices ? {eligibleIndices: result.eligibleResidualIndices} : {})});
        if (this.continuityUnavailable) continuity.diagnostics.unavailableReason = this.continuityUnavailable;
        let changedPixels = result.statistics.changedPixels;
        for (const index of continuity.indices) {
          const offset = index * 4;
          const wasChanged = before[offset] !== result.pixels[offset] || before[offset + 1] !== result.pixels[offset + 1]
            || before[offset + 2] !== result.pixels[offset + 2];
          for (let channel = 0; channel < 3; channel++) result.pixels[offset + channel] = clean.pixels[offset + channel]!;
          const isChanged = before[offset] !== result.pixels[offset] || before[offset + 1] !== result.pixels[offset + 1]
            || before[offset + 2] !== result.pixels[offset + 2];
          changedPixels += Number(isChanged) - Number(wasChanged);
        }
        this.latestStats.continuity = continuity.diagnostics;
        this.latestStats.timings.continuityMs = performance.now() - continuityStarted;
        const checksStarted = performance.now();
        // The same accepted optical/nasal pixels remain the final authoritative copy,
        // including after the independent topology-only removal.
        for (const rect of [...protection.protectedRects, this.noseRoi]) for (let y = rect.y0; y < rect.y1; y++) {
          const start = (y * width + rect.x0) * 4, end = (y * width + rect.x1) * 4;
          result.pixels.set(before.subarray(start, end), start);
        }
        const {protectedCheck, noseCheck, outsideEditableCheck, backgroundPreservationCheck} = checkHairProtection(input, result.pixels, result.regions);
        this.latestStats.timings.finalChecksMs = performance.now() - checksStarted;
        if ([protectedCheck, noseCheck, outsideEditableCheck, backgroundPreservationCheck].some(value => value.changedPixels !== 0)) {
          throw new Error('A final accepted-pixel protection check failed.');
        }
        this.mask = copyHairMask(mask);
        this.after = ownedImage(result.pixels, width, height);
        Object.assign(this.latestStats, {hasMask: true, maskOutputMode: mask.outputMode ?? 'full', maskStatus: 'ready', fallbackReason: null, changedPixels,
          protectedCheck, noseCheck, outsideEditableCheck, backgroundPreservationCheck});
      } catch (error) {
        this.mask = null;
        this.after = this.before;
        Object.assign(this.latestStats, {hasMask: false, maskStatus: 'rejected', fallbackReason: errorMessage(error), changedPixels: 0});
        // The next pair may retry the auxiliary capture; this pair retains beauty.
      }
      this.pending = false; this.publish(); return hasFace;
    } catch (error) { this.clearOwned(); this.displayContext.clearRect(0, 0, this.display.width, this.display.height); throw error; }
    finally {
      this.pending = false;
      if (this.latestStats) {
        this.latestStats.timings.finishMs = performance.now() - started;
        this.latestStats.timings.workMs = this.latestStats.timings.prepareMs + this.latestStats.timings.finishMs;
        this.latestStats.timings.totalMs = performance.now() - this.preparedAtMs;
      }
    }
  }

  /** Explicit hold only: an independent source/detection pair, without PNG encoding. */
  copyHeldInput(): HeldHairInput | null {
    if (this.disposed || this.pending || !this.before || !this.detection || !this.pair || !this.model) return null;
    const source = document.createElement('canvas'); source.width = this.source.width; source.height = this.source.height;
    context(source).drawImage(this.source, 0, 0);
    return {source, detection: structuredClone(this.detection), pair: structuredClone(this.pair), expectedModel: structuredClone(this.model)};
  }

  /** Explicit caller action only: generates owned lossless images and raw-mask export. */
  exportDiagnostic(): Record<string, unknown> | null {
    if (this.pending || this.disposed) return null;
    const snapshot = this.captureSnapshot;
    const before = this.before ? {width: this.before.width, height: this.before.height, pixels: this.before.data} : null;
    const after = this.after ? {width: this.after.width, height: this.after.height, pixels: this.after.data} : null;
    if (!snapshot || !before || !after || !this.detection || !this.pair || this.disposed) return null;
    let mask: Record<string, unknown> | null = null;
    if (this.mask) {
      const confidence = this.mask.confidence;
      const floats = confidence ? new Uint8Array(confidence.length * 4) : null;
      if (floats && confidence) {
        const view = new DataView(floats.buffer);
        for (let index = 0; index < confidence.length; index++) view.setFloat32(index * 4, confidence[index]!, true);
      }
      mask = {sourceSHA256: this.mask.sourceSHA256, detectionSHA256: this.mask.detectionSHA256,
        model: this.mask.model, modelSHA256: this.mask.modelSHA256, labels: [...this.mask.labels], hairIndex: this.mask.hairIndex,
        width: this.mask.width, height: this.mask.height, categorySHA256: this.mask.categorySHA256, confidenceSHA256: this.mask.confidenceSHA256 ?? null,
        outputMode: this.mask.outputMode ?? 'full',
        categoryBase64: base64(this.mask.category), confidenceBase64: floats ? base64(floats) : null,
        categoryStorage: 'uint8, row-major top-left', confidenceStorage: floats
          ? 'float32 little-endian, row-major top-left; not alpha or depth' : 'not requested for live occlusion'};
    }
    return {schema: 'hair-live-diagnostic-v1', acceptedCommit: 'b26b5584c0dccbc2b30e4f12cdd432f10df577ea',
      acceptedRevisionLabel: 'perfecto long hair but slow', hairAccepted: false, candidateRevisionLabel: CANDIDATE_REVISION,
      sequence: this.sequence, variant: this.selected, sourceWidth: this.source.width, sourceHeight: this.source.height,
      width: before.width, height: before.height, sourcePngDataUrl: this.source.toDataURL('image/png'),
      acceptedPngDataUrl: png(before), hairPngDataUrl: png(after), cleanBackgroundPngDataUrl: this.background ? png(this.background) : null,
      detection: structuredClone(this.detection), pair: {...this.pair}, expectedModel: this.model ? structuredClone(this.model) : null,
      captureSnapshot: {...snapshot, surfacePositions: Array.from(snapshot.surfacePositions)},
      noseRoi: this.noseRoi ? {...this.noseRoi} : null, mask,
      stats: {...this.stats, acceptedDiagnostics: this.accepted.diagnostics}, policy: {...LIVE_HAIR_POLICY},
      provenance: 'Exact current source RGBA and detection hashes supplied by the owned live session. Semantic hair category heuristic with unchanged accepted optical/nasal guards; no measured hair depth or alpha.'};
  }

  private publish(): void {
    const value = this.selected === 'accepted' ? this.before : this.after; if (!value || this.disposed || this.pending) return;
    const started = performance.now();
    if (this.display.width !== value.width || this.display.height !== value.height) { this.display.width = value.width; this.display.height = value.height; }
    this.displayContext.putImageData(value, 0, 0);
    if (this.latestStats) this.latestStats.timings.publishMs = performance.now() - started;
  }
  private clearOwned(): void {
    this.generation++; this.preparing = false;
    this.borrowedSource = this.liveSourceLease = null; this.retainedSourcePixels = null; this.sourceMaterialized = true;
    this.pending = false; this.preparedAtMs = 0;
    this.before = this.after = null; this.background = null; this.latestStats = null; this.snapshot = null;
    this.detection = null; this.pair = null; this.mask = null; this.model = null; this.noseRoi = null;
  }
  dispose(): void {
    if (this.disposed) return; this.disposed = true;
    for (const cleanup of this.cleanup) cleanup(); this.cleanup = [];
    this.preparationHairEnabled = null; this.preparationInput = null; this.accepted.dispose(); this.clearOwned();
    this.compositionScratch.clear();
    this.continuityModel = null;
    this.sourceHistoryPixels = null;
    this.sourceGenerations.clear();
    this.ownedSourceCanvas.width = this.ownedSourceCanvas.height = this.acceptedCanvas.width = this.acceptedCanvas.height = 0;
    this.displayContext.clearRect(0, 0, this.display.width, this.display.height);
  }
}
