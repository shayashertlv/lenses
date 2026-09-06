import { CaptureStore, CAPTURE_LIMITS } from './store.ts';
import type { Detection } from '../runtime/protocol.ts';
import { validateDetection } from '../runtime/protocol.ts';
import type { TryOnRenderer } from '../render/renderer.ts';
import type { CaptureGeometry } from '../render/renderer.ts';
import { VIRTUAL_CAMERA } from '../render/projection.ts';

type PortableSnapshot = Omit<CaptureGeometry, 'surfacePositions'> & { surfacePositions: number[] };
interface Hooks {
  renderer(): TryOnRenderer | null;
  captureInfo(): Record<string, unknown>;
  pauseForReplay(): boolean;
  replayPresented(index: number, count: number): void;
  close(): void;
}
function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing capture control: ${id}`);
  return found as T;
}

/** Owns only explicitly requested, bounded, memory-only recordings and replay. */
export class CaptureController {
  private readonly store = new CaptureStore<PortableSnapshot | null>();
  private readonly panel = element('capture');
  private readonly record = element<HTMLButtonElement>('record-turn');
  private readonly finishButton = element<HTMLButtonElement>('finish-recording');
  private readonly controls = element('replay-controls');
  private readonly status = element('capture-status');
  private readonly coverage = element('capture-coverage');
  private readonly slider = element<HTMLInputElement>('replay-frame');
  private readonly download = element<HTMLButtonElement>('download-capture');
  private readonly note = element<HTMLTextAreaElement>('capture-note');
  private readonly image = document.createElement('canvas');
  private readonly urls = new Map<string, number>();
  private generation = 0;
  private renderGeneration = 0;
  private cachedIndex = -1;
  private liveFace = false;
  private timer: number | null = null;
  private header: Record<string, unknown> = {};
  private exportAbort: AbortController | null = null;

  constructor(private readonly hooks: Hooks) {
    this.record.addEventListener('click', () => this.begin());
    this.finishButton.addEventListener('click', () => { void this.finish(); });
    this.slider.addEventListener('input', () => { void this.replay(); });
    element('previous-frame').addEventListener('click', () => this.step(-1));
    element('next-frame').addEventListener('click', () => this.step(1));
    this.download.addEventListener('click', () => { void this.exportCapture(); });
    element('discard-capture').addEventListener('click', () => this.hooks.close());
  }

  get guidance(): string | null {
    return this.panel.dataset.state === 'recording'
      ? 'Recording: turn slowly both ways, return to center, then look slightly up and down.' : null;
  }

  setLiveFace(visible: boolean): void {
    this.liveFace = visible;
    if (['idle', 'ready'].includes(this.panel.dataset.state!)) {
      this.panel.dataset.state = visible ? 'ready' : 'idle';
      this.record.disabled = !visible;
      this.status.textContent = visible ? 'Ready. Record a slow turn, up to 30 seconds.' : 'Bring your face into view to record.';
    }
  }

  private begin(): void {
    if (!this.liveFace || !this.hooks.renderer() || this.record.disabled) return;
    this.reset();
    this.header = {
      renderer: 'current-mirror',
      recordedAt: new Date().toISOString(),
      browser: navigator.userAgent,
      camera: this.hooks.captureInfo(),
      projection: { ...VIRTUAL_CAMERA, calibrated: false },
      coordinates: 'unmirrored normalized image; canonical centimeters, +Y up and +Z anterior',
      imageEncoding: 'JPEG; original matched detection retained without redetection',
      occlusion: 'Local nasal RGB boundary v1; captured surface retained during replay',
    };
    this.store.start();
    this.panel.dataset.state = 'recording';
    this.record.hidden = true;
    this.finishButton.hidden = false;
    this.finishButton.disabled = false;
    this.status.textContent = 'Recording. Start at the center, then turn slowly both ways.';
    this.timer = window.setTimeout(() => { void this.finish(); }, CAPTURE_LIMITS.maxDurationMs);
  }

  /** Called before the paired capture canvas can be reused for another frame. */
  async observe(frame: HTMLCanvasElement, detection: Detection, capturedAt: number): Promise<void> {
    if (this.panel.dataset.state !== 'recording') return;
    const snapshot = this.hooks.renderer()?.captureSnapshot;
    const generation = this.generation;
    try {
      await this.store.capture(frame, detection, {
        capturedAt,
        metadata: snapshot ? { ...snapshot, surfacePositions: Array.from(snapshot.surfacePositions) } : null,
        yawDegrees: snapshot?.yawDegrees ?? null,
      });
      if (generation !== this.generation) return;
      this.updateCoverage();
      if (this.store.snapshot.state === 'finished') await this.finish();
    } catch (error) {
      if (generation !== this.generation) return;
      await this.finish();
      if (generation !== this.generation) return;
      this.status.textContent = `Recording stopped: ${error instanceof Error ? error.message : 'Could not encode frame.'}`;
    }
  }

  private updateCoverage(): void {
    const frames = this.store.snapshot.frames;
    const yaws = frames.map(frame => frame.yawDegrees).filter((yaw): yaw is number => yaw !== null);
    const front = yaws.some(yaw => Math.abs(yaw) <= 15);
    const negative = yaws.some(yaw => yaw <= -35);
    const positive = yaws.some(yaw => yaw >= 35);
    this.panel.dataset.frames = String(frames.length);
    const missing = frames.filter(frame => frame.metadata === null).length;
    this.status.textContent = `${frames.length} frames · ${Math.round((frames.at(-1)?.relativeMs ?? 0) / 1000)} seconds · ${missing} without valid face geometry`;
    this.coverage.textContent = `Estimated views: front ${front ? '✓' : 'missing'} · −35° side ${negative ? '✓' : 'missing'} · +35° side ${positive ? '✓' : 'missing'}. This is coverage guidance, not scan validation.`;
  }

  private async finish(): Promise<void> {
    if (!['recording', 'finishing'].includes(this.panel.dataset.state!)) return;
    if (this.panel.dataset.state === 'finishing') return;
    const generation = this.generation;
    this.panel.dataset.state = 'finishing';
    this.finishButton.disabled = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    await this.store.finish();
    if (generation !== this.generation) return;
    const count = this.store.snapshot.frames.length;
    if (!count) {
      this.reset();
      this.setLiveFace(this.liveFace);
      this.status.textContent = 'No usable frames were recorded. Keep your face visible and try again.';
      return;
    }
    if (!this.hooks.pauseForReplay()) { this.reset(); return; }
    this.updateCoverage();
    this.panel.dataset.state = 'replay';
    this.finishButton.hidden = true;
    this.controls.hidden = false;
    this.slider.max = String(count - 1);
    this.slider.value = '0';
    this.status.textContent = `${count} frames ready. Camera closed. Move through the turn to review it.`;
    await this.replay();
  }

  private step(delta: number): void {
    this.slider.value = String(Math.max(0, Math.min(Number(this.slider.max), Number(this.slider.value) + delta)));
    void this.replay();
  }

  private async replay(): Promise<void> {
    if (this.panel.dataset.state !== 'replay') return;
    const renderer = this.hooks.renderer();
    const index = Number(this.slider.value);
    const recorded = this.store.snapshot.frames[index];
    if (!renderer || !recorded) return;
    const token = ++this.renderGeneration;
    try {
      if (index !== this.cachedIndex) {
        const bitmap = await createImageBitmap(recorded.jpeg);
        try {
          if (token !== this.renderGeneration || this.panel.dataset.state !== 'replay') return;
          this.image.width = recorded.width;
          this.image.height = recorded.height;
          const context = this.image.getContext('2d', { alpha: false });
          if (!context) throw new Error('Could not decode the recorded image.');
          context.drawImage(bitmap, 0, 0);
          this.cachedIndex = index;
        } finally { bitmap.close(); }
      }
      if (token !== this.renderGeneration) return;
      // JPEG encoding can change a color boundary. Retain the surface from the
      // original presentation instead of deriving a new one from decoded pixels.
      renderer.present(this.image, validateDetection(recorded.detection), recorded.metadata?.surfacePositions);
      const count = this.store.snapshot.frames.length;
      element('replay-position').textContent = `${index + 1} / ${count}`;
      element('replay-metrics').textContent = `Captured ${(recorded.relativeMs / 1000).toFixed(1)}s · estimated yaw ${recorded.yawDegrees?.toFixed(1) ?? 'unknown'}°.`;
      this.hooks.replayPresented(index, count);
    } catch (error) {
      if (token === this.renderGeneration) {
        this.status.textContent = `Replay failed: ${error instanceof Error ? error.message : 'Could not show this frame.'}`;
      }
    }
  }

  private async exportCapture(): Promise<void> {
    if (this.panel.dataset.state !== 'replay' || this.download.disabled) return;
    const generation = this.generation;
    const abort = new AbortController();
    this.exportAbort = abort;
    const header = structuredClone({ ...this.header, note: this.note.value });
    this.download.disabled = true;
    this.download.textContent = 'Preparing download…';
    try {
      const response = await fetch('/models/manifest.json', { signal: abort.signal });
      if (!response.ok) throw new Error('Could not read asset provenance.');
      const assets: unknown = await response.json();
      if (generation !== this.generation || this.panel.dataset.state !== 'replay') return;
      const json = await this.store.exportJson({ ...header, assets });
      if (generation !== this.generation || this.panel.dataset.state !== 'replay') return;
      const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `ar-v4-capture-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
      document.body.append(link);
      link.click();
      link.remove();
      const timer = window.setTimeout(() => { URL.revokeObjectURL(url); this.urls.delete(url); }, 60_000);
      this.urls.set(url, timer);
      this.status.textContent = 'Download requested. The JSON contains your face images and tracking data; keep it local for analysis.';
    } catch (error) {
      if (generation === this.generation) this.status.textContent = `Download failed: ${error instanceof Error ? error.message : 'Please try again.'}`;
    } finally {
      if (generation === this.generation) {
        this.exportAbort = null;
        this.download.disabled = false;
        this.download.textContent = 'Download capture';
      }
    }
  }

  reset(): void {
    this.generation++;
    this.renderGeneration++;
    this.store.reset();
    this.exportAbort?.abort();
    this.exportAbort = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    for (const [url, timer] of this.urls) { clearTimeout(timer); URL.revokeObjectURL(url); }
    this.urls.clear();
    this.header = {};
    this.cachedIndex = -1;
    this.image.width = this.image.height = 1;
    this.panel.dataset.state = 'idle';
    this.panel.dataset.frames = '0';
    this.record.hidden = false;
    this.record.disabled = true;
    this.finishButton.hidden = true;
    this.controls.hidden = true;
    delete document.querySelector<HTMLElement>('.stage')!.dataset.replayFrame;
    this.note.value = '';
    this.download.disabled = false;
    this.download.textContent = 'Download capture';
    this.status.textContent = 'Open the camera to begin.';
    this.coverage.textContent = 'Still frames stay in this tab until you discard them. Download only if you want to save.';
  }
}
