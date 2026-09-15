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

export type {PairIdentity, HairModelContract};
export interface LiveStats {
  sequence: number; hasFace: boolean; hasMask: boolean; hairEnabled: boolean;
  maskStatus: 'ready' | 'missing' | 'withheld' | 'no-face'; fallbackReason: string | null;
  prepareMs: number; poseMs: number; gpuWaitMs: number; gpuWaitPolls: number; finishMs: number; pendingWaitMs: number; totalMs: number;
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
  private wait = {gpuWaitMs: 0, polls: 0};
  private frame: HTMLCanvasElement | null = null;
  private detection: Detection | null = null;
  private pair: PairIdentity | null = null;
  private model: HairModelContract | null = null;
  private auditRequested = false;
  private audit: Audit | null = null;
  private constructor(private readonly renderer: TryOnRenderer) {}
  static async create(display: HTMLCanvasElement, signal: AbortSignal, eyewearId: string = DEFAULT_EYEWEAR_ID, options: RendererOptions = {}): Promise<LiveRenderer> {
    return new LiveRenderer(await TryOnRenderer.create(display, signal, eyewearId, options));
  }
  get stats(): LiveStats | null {return this.pending || this.disposed ? null : this.latest;}
  get eyewear() {return this.renderer.eyewear;}
  get nativeSamples(): number {return this.renderer.nativeSamples;}
  get gpuRenderer(): string | null {return this.renderer.gpuRenderer;}
  get guardEnabled(): boolean {return this.renderer.guardEnabled;}
  get continuityUnavailable(): string | null {return this.renderer.continuityUnavailable;}
  get captureSnapshot() {return this.renderer.captureSnapshot;}
  setHairEnabled(value: boolean): void {this.hairEnabled = value;}
  /** The next finished frame is audited; the result is available through takeAudit(). */
  requestAudit(): void {if (!this.disposed) this.auditRequested = true;}
  takeAudit(): Audit | null {const value = this.audit; this.audit = null; return value;}
  async prepare(frame: HTMLCanvasElement, detection: Detection, pair: PairIdentity, model: HairModelContract, needsHair: boolean = this.hairEnabled): Promise<boolean> {
    if (this.disposed) throw new DOMException('Renderer closed.', 'AbortError');
    if (this.pending) throw new Error('A frame is already pending.');
    this.pending = true; this.sequence++;
    const started = performance.now();
    try {
      this.wait = await this.renderer.waitForPreviousFrame();
      if (this.disposed) throw new DOMException('Renderer closed.', 'AbortError');
      const poseStart = performance.now();
      this.visible = this.renderer.pose(frame, detection);
      this.poseMs = performance.now() - poseStart;
      this.frame = frame; this.detection = detection; this.pair = pair; this.model = model;
      this.needsHair = needsHair; this.preparedAt = started; this.prepareMs = performance.now() - started;
      return this.visible;
    } catch (error) {this.pending = false; throw error;}
  }
  finish(mask: HairMask | null): boolean {
    if (this.disposed || !this.pending) throw new Error('No pending frame.');
    const started = performance.now();
    try {
      const wantsHair = this.needsHair && this.hairEnabled;
      const gpuMask: CategoryMask | null = mask !== null && wantsHair && this.visible ? mask : null;
      const timings = this.renderer.render(gpuMask);
      let auditMs = 0, audited = false;
      if (this.auditRequested && this.visible && this.frame && this.detection && this.pair && this.model) {
        this.auditRequested = false; audited = true;
        const auditStart = performance.now();
        try {this.audit = runAudit(this.renderer, {frame: this.frame, detection: this.detection, pair: this.pair, model: this.model, mask, gpuMask}, timings, this.sequence);}
        catch (error) {this.audit = null; console.warn('Audit failed', error);}
        auditMs = performance.now() - auditStart;
      }
      const finishMs = performance.now() - started;
      this.latest = {
        sequence: this.sequence, hasFace: this.visible, hasMask: timings.hairApplied, hairEnabled: wantsHair,
        maskStatus: !this.visible ? 'no-face' : timings.hairApplied ? 'ready' : timings.safeFallback ? 'withheld' : 'missing',
        fallbackReason: this.visible && wantsHair && !timings.hairApplied
          ? timings.safeFallback ? 'The optical/nasal protection could not be established; drop and hair withheld.' : 'No paired hair mask is available; showing the frame without hair occlusion.' : null,
        prepareMs: this.prepareMs, poseMs: this.poseMs, gpuWaitMs: this.wait.gpuWaitMs, gpuWaitPolls: this.wait.polls, finishMs,
        pendingWaitMs: Math.max(0, started - this.preparedAt - this.prepareMs), totalMs: performance.now() - this.preparedAt,
        render: timings, audit: {ran: audited, ms: auditMs},
      };
      return this.visible;
    } finally {this.pending = false;}
  }
  dispose(): void {if (this.disposed) return; this.disposed = true; this.pending = false; this.latest = null; this.audit = null; this.frame = null; this.renderer.dispose();}
}
