/** The pipeline-facing renderer: `prepare` waits (yielding) for the previous frame's GPU completion and poses the new
 *  frame, `finish(mask)` uploads the mask and draws straight into the display canvas. Live frames are never read back;
 *  an explicitly requested audit reads the next finished frame (see audit/audit.ts). */
import {TryOnRenderer} from './renderer.ts';
import type {FrameTimings, RendererOptions} from './renderer.ts';
import type {Detection} from '../face/protocol.ts';
import type {CategoryMask, HairMask} from '../hair/protocol.ts';
import {runAudit} from '../audit/audit.ts';
import type {Audit} from '../audit/audit.ts';
import type {HairModelContract, PairIdentity} from '../audit/reference.ts';
import {DEFAULT_EYEWEAR_ID} from '../eyewear/catalog.ts';
import type {MaskWarp} from '../hair/mask-reuse.ts';
import type {TempleVisibilityMode} from './temple-visibility.ts';

/** Finished frames an audit passes over while they draw a mask reused from another frame, waiting for one that draws
 *  its own (the CPU reference composes only a frame's own mask); after that many it audits a reused one and says so. */
export const AUDIT_OWN_MASK_FRAMES = 90;

export type {PairIdentity, HairModelContract};
export interface LiveStats {
  sequence: number; hasFace: boolean; hasMask: boolean; hairEnabled: boolean;
  maskStatus: 'ready' | 'missing' | 'withheld' | 'no-face'; fallbackReason: string | null;
  prepareMs: number; poseMs: number; gpuWaitMs: number; gpuWaitPolls: number; gpuWaitTimedOut: boolean; finishMs: number; pendingWaitMs: number; totalMs: number;
  render: FrameTimings; audit: {ran: boolean; ms: number};
}

export class LiveRenderer {
  private hairEnabled = true;
  private pending = false;
  private disposed = false;
  private sequence = 0;
  private latest: LiveStats | null = null;
  private needsHair = true;
  private visible = false;
  private preparedAt = 0;
  private prepareMs = 0;
  private poseMs = 0;
  private wait = {gpuWaitMs: 0, polls: 0, timedOut: false};
  private frame: HTMLCanvasElement | null = null;
  private detection: Detection | null = null;
  private pair: PairIdentity | null = null;
  private model: HairModelContract | null = null;
  private auditRequested = false;
  private auditPassedOver = 0;
  private audit: Audit | null = null;
  private readonly renderer: TryOnRenderer;
  private constructor(renderer: TryOnRenderer) {this.renderer = renderer;}
  static async create(display: HTMLCanvasElement, signal: AbortSignal, eyewearId: string = DEFAULT_EYEWEAR_ID, options: RendererOptions = {}): Promise<LiveRenderer> {
    return new LiveRenderer(await TryOnRenderer.create(display, signal, eyewearId, options));
  }
  get stats(): LiveStats | null {return this.pending || this.disposed ? null : this.latest;}
  get eyewear() {return this.renderer.eyewear;}
  get nativeSamples(): number {return this.renderer.nativeSamples;}
  get gpuRenderer(): string | null {return this.renderer.gpuRenderer;}
  get guardEnabled(): boolean {return this.renderer.guardEnabled;}
  get continuityUnavailable(): string | null {return this.renderer.continuityUnavailable;}
  get syncUnavailable(): string | null {return this.renderer.syncUnavailable;}
  get captureSnapshot() {return this.renderer.captureSnapshot;}
  setHairEnabled(value: boolean): void {this.hairEnabled = value;}
  /** Switch the experimental width fit inside the running session; the change lands between frames, on the next pose. */
  setWidthFit(enabled: boolean): void {if (!this.disposed) this.renderer.setWidthFit(enabled);}
  /** Switch the temple-visibility rule inside the running session. */
  setTempleMode(mode: TempleVisibilityMode): void {if (!this.disposed) this.renderer.setTempleMode(mode);}
  get templeVisibility() {return this.renderer.templeVisibilityState;}
  /** The width fit's mode, state and applied ratio, for the page's debug line. */
  get widthFit() {return this.renderer.widthFit;}
  /** The next finished frame is audited; the result is available through takeAudit(). */
  requestAudit(): void {if (!this.disposed) {this.auditRequested = true; this.auditPassedOver = 0;}}
  /** An audit is waiting for a frame: with a hair schedule the pipeline then gives the next frame its own mask. */
  get auditRequestPending(): boolean {return this.auditRequested && !this.disposed;}
  takeAudit(): Audit | null {const value = this.audit; this.audit = null; return value;}
  /** The posed frame's raw and steadied orientation (numbers only); null while a frame is pending or without a face. */
  get poseSample() {return this.pending || this.disposed ? null : this.renderer.poseSample;}
  async prepare(frame: HTMLCanvasElement, detection: Detection, pair: PairIdentity, model: HairModelContract, needsHair: boolean = this.hairEnabled,
    timestampMs: number = performance.now()): Promise<boolean> {
    if (this.disposed) throw new DOMException('Renderer closed.', 'AbortError');
    if (this.pending) throw new Error('A frame is already pending.');
    this.pending = true; this.sequence++;
    const started = performance.now();
    try {
      this.wait = await this.renderer.waitForPreviousFrame();
      if (this.disposed) throw new DOMException('Renderer closed.', 'AbortError');
      const poseStart = performance.now();
      this.visible = this.renderer.pose(frame, detection, timestampMs);
      this.poseMs = performance.now() - poseStart;
      this.frame = frame; this.detection = detection; this.pair = pair; this.model = model;
      this.needsHair = needsHair; this.preparedAt = started; this.prepareMs = performance.now() - started;
      return this.visible;
    } catch (error) {this.pending = false; throw error;}
  }
  /** `warp` places a mask reused from an earlier frame (null: the frame's own mask, or a reused mask held in place);
   *  `carried` says the mask came from another frame, so the audit can say so. */
  finish(mask: HairMask | null, warp: MaskWarp | null = null, carried = false): boolean {
    if (this.disposed || !this.pending) throw new Error('No pending frame.');
    const started = performance.now();
    try {
      const wantsHair = this.needsHair && this.hairEnabled;
      const gpuMask: CategoryMask | null = mask !== null && wantsHair && this.visible ? mask : null;
      this.renderer.setMaskWarp(gpuMask ? warp : null);
      const timings = this.renderer.render(gpuMask);
      let auditMs = 0, audited = false;
      const passOver = gpuMask !== null && carried && this.auditPassedOver < AUDIT_OWN_MASK_FRAMES;
      if (this.auditRequested && this.visible && passOver) this.auditPassedOver++;
      else if (this.auditRequested && this.visible && this.frame && this.detection && this.pair && this.model) {
        this.auditRequested = false; audited = true;
        const auditStart = performance.now();
        try {this.audit = runAudit(this.renderer, {frame: this.frame, detection: this.detection, pair: this.pair, model: this.model, mask, gpuMask, maskCarried: gpuMask !== null && carried, maskWarped: gpuMask !== null && warp !== null}, timings, this.sequence);}
        catch (error) {this.audit = null; console.warn('Audit failed', error);}
        auditMs = performance.now() - auditStart;
      }
      const finishMs = performance.now() - started;
      this.latest = {
        sequence: this.sequence, hasFace: this.visible, hasMask: timings.hairApplied, hairEnabled: wantsHair,
        maskStatus: !this.visible ? 'no-face' : timings.hairApplied ? 'ready' : timings.safeFallback ? 'withheld' : 'missing',
        fallbackReason: this.visible && wantsHair && !timings.hairApplied
          ? timings.safeFallback ? 'The optical/nasal protection could not be established; drop and hair withheld.' : 'No paired hair mask is available; showing the frame without hair occlusion.' : null,
        prepareMs: this.prepareMs, poseMs: this.poseMs, gpuWaitMs: this.wait.gpuWaitMs, gpuWaitPolls: this.wait.polls, gpuWaitTimedOut: this.wait.timedOut, finishMs,
        pendingWaitMs: Math.max(0, started - this.preparedAt - this.prepareMs), totalMs: performance.now() - this.preparedAt,
        render: timings, audit: {ran: audited, ms: auditMs},
      };
      return this.visible;
    } finally {this.pending = false;}
  }
  dispose(): void {if (this.disposed) return; this.disposed = true; this.pending = false; this.latest = null; this.audit = null; this.frame = null; this.renderer.dispose();}
}
