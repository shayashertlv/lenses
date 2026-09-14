import {openStabilityStore} from './stability-store.ts';
import type {StabilityStore, StabilityManifest, StabilityIdentity, StabilityChunk, StabilitySelection, StabilityDirection, StabilityMode} from './stability-store.ts';
import {createFilesArchive} from './run-export.ts';
import type {ContinuousRunStatus} from './continuous-run.ts';

const labels = {continuous: 'Continuous G', restarted: 'Restarted G', 'fresh-page': 'Fresh-page G',
  'readback-control': 'G control', 'readback-diagnostic': 'G readback diagnostic'};
const el = <T extends HTMLElement = HTMLElement>(id: string): T => document.getElementById(id) as T;
const message = (error: unknown): string => error instanceof Error ? error.message : String(error);
export interface StabilityPageBridge {
  buildId: string;
  choices(): Omit<StabilityIdentity, 'sourceWidth' | 'sourceHeight' | 'buildId'>;
  probeCamera(signal: AbortSignal): Promise<{width: number; height: number}>;
  runChunk(identity: StabilityIdentity, chunk: StabilityChunk, metadata: Record<string, unknown>): Promise<void>;
  stopChunk(reason: string): boolean;
  closeCamera(): void;
}

/** Page-level orchestration only. No persistence, ZIP creation or previous-report
 * parsing runs in a measurement frame callback. Each mode owns independent storage. */
export class GStabilityPreview {
  readonly documentId = crypto.randomUUID();
  private store: StabilityStore | null = null;
  private manifest: StabilityManifest | null = null;
  private phase = 'loading';
  private detail = 'Opening local result storage…';
  private ownsLock = false;
  private releaseLock: (() => void) | null = null;
  private abort: AbortController | null = null;
  private pendingReport: Record<string, unknown> | null = null;
  private file: File | null = null;
  private fileUrl: string | null = null;
  private busy = false;
  private cameraAttempts = 0;
  private navigates = false;
  private stopped = false;
  private progress: ContinuousRunStatus | null = null;
  private progressKey = '';
  private readonly handoffKey: string;
  private readonly lockName: string;
  constructor(private readonly bridge: StabilityPageBridge, private readonly mode: StabilityMode = 'stability') {
    const prefix = mode === 'readback' ? 'ar-g-readback' : 'ar-g-stability';
    this.handoffKey = `${prefix}-handoff-v1`; this.lockName = `${prefix}-page-owner-v1`;
    el('stability-start').addEventListener('click', () => {void this.start();});
    el('stability-stop').addEventListener('click', () => {void this.stop();});
    el('stability-resume').addEventListener('click', () => {void this.resume();});
    el('stability-download').addEventListener('click', () => {void this.download();});
    el('stability-share').addEventListener('click', () => {void this.share();});
    el('stability-delete').addEventListener('click', () => {void this.clear();});
    window.addEventListener('pagehide', () => {
      this.abort?.abort(); this.releaseLock?.(); this.releaseLock = null;
      // Unexpected exits may prevent an IDB transaction from finishing. The next
      // document acquires the lock and marks an orphaned claim incomplete.
      if (!this.navigates && this.manifest?.active?.documentId === this.documentId)
        void this.interrupt('The page closed or refreshed before this part was saved.');
    });
    window.addEventListener('pageshow', event => {if (event.persisted) location.reload();});
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && !this.navigates && ['preflight', 'opening', 'running', 'waiting-camera'].includes(this.phase))
        void this.stop('The page became hidden; the unfinished part is retained as partial.');
    });
    void this.initialize();
  }
  status(): Record<string, unknown> {
    const m = this.manifest, chunk = m?.plan[m.nextChunkIndex];
    return {mode: this.mode, suiteId: m?.id ?? null, documentId: this.documentId, state: this.phase, savedState: m?.status ?? null,
      condition: chunk?.condition ?? null, chunkIndex: chunk?.index ?? null, completedChunks: m?.results.filter(r => r.completed).length ?? 0,
      planLength: m?.plan.length ?? 0, active: m?.active ? {...m.active} : null,
      pending: this.phase === 'waiting-camera' || this.phase === 'ready' || !!this.pendingReport,
      ownsLock: this.ownsLock, cameraAttempts: this.cameraAttempts, progress: this.progress ? {...this.progress} : null,
      reason: this.detail};
  }
  async report(): Promise<unknown> {
    if (!this.store || ['preflight', 'opening', 'running', 'saving', 'handoff'].includes(this.phase))
      throw new Error('Stop the test before reading all stored raw reports.');
    return this.store.exportReports();
  }
  locksSettings(): boolean {return !!this.manifest || this.busy;}
  isActive(): boolean {return ['preflight', 'opening', 'running', 'saving', 'handoff', 'waiting-camera'].includes(this.phase);}
  onProgress(status: ContinuousRunStatus): void {
    this.progress = {...status};
    const key = `${status.state}:${status.windowIndex}:${Math.ceil(status.remainingMs / 1000)}`;
    if (key === this.progressKey) return; this.progressKey = key;
    const chunk = this.manifest?.plan[this.manifest.nextChunkIndex];
    if (!chunk || this.stopped) return;
    this.phase = 'running';
    const part = chunk.condition === 'fresh-page' ? `Page ${chunk.conditionWindow + 1}/6`
      : chunk.condition === 'restarted' ? `Window ${status.windowIndex + 1}/6` : 'One uninterrupted run';
    this.detail = `${labels[chunk.condition]} · ${part} · ${status.state === 'measuring' ? 'Measuring' : status.state === 'warmup' ? 'Warming up' : 'Preparing'} · ${Math.ceil(status.remainingMs / 1000)} s`;
    this.render();
  }
  private render(): void {
    el('stability-panel').setAttribute('data-state', this.phase);
    el('stability-status').textContent = this.detail;
    const m = this.manifest;
    if (m) {el<HTMLSelectElement>('stability-condition').value = m.selection; el<HTMLSelectElement>('stability-order').value = m.direction;}
    el('stability-saved').textContent = m ? `${m.results.filter(r => r.completed).length}/${m.plan.length} parts completed and saved locally. ${m.identity.sourceWidth} × ${m.identity.sourceHeight}.`
      : 'Results stay on this browser until you save or explicitly delete them.';
    el<HTMLButtonElement>('stability-start').disabled = this.busy || !!m || !this.store;
    el('stability-start').hidden = !!m;
    el<HTMLSelectElement>('stability-condition').disabled = !!m || this.busy;
    el<HTMLSelectElement>('stability-order').disabled = !!m || this.busy;
    el('stability-stop').hidden = !this.isActive();
    el<HTMLButtonElement>('stability-stop').disabled = this.phase === 'saving' || this.phase === 'handoff' || this.stopped;
    el('stability-resume').hidden = !['ready', 'waiting-camera', 'save-error'].includes(this.phase);
    el<HTMLButtonElement>('stability-resume').disabled = this.busy;
    el('stability-resume').textContent = this.pendingReport ? 'Retry saving this part' : 'Continue test';
    el<HTMLButtonElement>('stability-download').disabled = this.busy || this.isActive() || !m;
    el<HTMLButtonElement>('stability-delete').disabled = this.busy || this.isActive() || !m || !this.ownsLock;
    el('stability-share').hidden = !this.file || typeof navigator.share !== 'function';
    for (const id of ['eyewear-select', 'hair-model-select', 'power-context']) el<HTMLSelectElement>(id).disabled = !!m || this.busy;
  }
  private async ownership(): Promise<boolean> {
    if (this.ownsLock) return true;
    if (!navigator.locks) throw new Error('This browser cannot safely coordinate a reload test. Browser Web Locks support is required.');
    return await new Promise<boolean>((resolve, reject) => {
      void navigator.locks.request(this.lockName, {ifAvailable: true}, async lock => {
        if (!lock) {resolve(false); return;}
        this.ownsLock = true;
        await new Promise<void>(released => {this.releaseLock = released; resolve(true);});
        this.ownsLock = false;
      }).catch(reject);
    });
  }
  private async initialize(): Promise<void> {
    try {
      this.store = await openStabilityStore(globalThis.indexedDB, this.mode); this.manifest = await this.store.read();
      if (!this.manifest) {this.phase = 'idle'; this.detail = 'Choose the same glasses and hair model for all tests, then start. Video is off.'; this.render(); return;}
      if (!await this.ownership()) {this.phase = 'blocked'; this.detail = 'Another stability page owns this test. Continue there or close that page before reloading this one.'; this.render(); return;}
      const handoff = sessionStorage.getItem(this.handoffKey); sessionStorage.removeItem(this.handoffKey);
      const m = this.manifest;
      if (m.status === 'running' && m.active) {
        this.manifest = await this.store.interrupt({suiteId: m.id, ...m.active, reason: 'The previous document ended without saving this part. Completed parts remain available.'});
        this.phase = 'partial'; this.detail = 'The previous part was interrupted. Save the partial ZIP; completed parts are preserved. Delete the saved test to start again.';
      } else if (m.status === 'ready') {
        this.phase = 'ready'; this.detail = 'A saved test is ready to continue. Its settings remain fixed.';
        if (m.identity.buildId !== this.bridge.buildId) {this.phase = 'partial'; this.detail = 'This saved test uses an earlier build. Save it, then delete it to start a separate comparison.';}
        else if (handoff === JSON.stringify({suiteId: m.id, token: m.handoffToken})) {await this.claim(); return;}
      } else {
        this.phase = m.status; this.detail = m.status === 'complete' ? 'All tests are complete. Save one ZIP and send it back for analysis.'
          : `Partial test retained: ${m.interrupted?.reason ?? 'The run stopped.'} Save the ZIP before starting another test.`;
      }
      this.render();
    } catch (error) {this.phase = 'blocked'; this.detail = message(error); this.render();}
  }
  private async start(): Promise<void> {
    if (this.busy || !this.store || this.manifest) return;
    this.busy = true; this.stopped = false;
    try {
      if (!await this.ownership()) throw new Error('Another stability page is open. Close it before starting here.');
      // Verify that handoff storage is writable before requesting the camera.
      sessionStorage.setItem(this.handoffKey, 'probe'); sessionStorage.removeItem(this.handoffKey);
      this.abort = new AbortController(); this.phase = 'preflight'; this.detail = 'Checking camera dimensions, then opening a fresh test page…'; this.render();
      const dimensions = await this.bridge.probeCamera(this.abort.signal);
      if (this.stopped) return;
      const identity: StabilityIdentity = {...this.bridge.choices(), variant: 'hair', buildId: this.bridge.buildId,
        sourceWidth: dimensions.width, sourceHeight: dimensions.height};
      this.manifest = await this.store.create({identity, selection: el<HTMLSelectElement>('stability-condition').value as StabilitySelection,
        direction: el<HTMLSelectElement>('stability-order').value as StabilityDirection});
      if (this.stopped || document.hidden) {this.phase = 'ready'; this.detail = 'Setup stopped. The saved test is ready; tap Continue test when ready.'; return;}
      await this.navigate();
    } catch (error) {this.phase = this.manifest?.status === 'ready' ? 'ready' : 'idle'; this.detail = this.phase === 'ready' ? `The test is saved. ${message(error)} Tap Continue test to retry opening its fresh page.` : message(error);}
    finally {this.busy = false; this.render();}
  }
  private async navigate(): Promise<void> {
    const m = this.manifest;
    if (!m || m.status !== 'ready' || !m.handoffToken) return;
    sessionStorage.setItem(this.handoffKey, JSON.stringify({suiteId: m.id, token: m.handoffToken}));
    // Flush of raw report + next token is awaited by caller before this point.
    const query = new URLSearchParams({study: this.mode === 'readback' ? 'readback-diagnostic' : 'g-stability', suite: m.id, part: String(m.nextChunkIndex),
      eyewear: m.identity.eyewearId, 'hair-model': m.identity.hairModelId, variant: m.identity.variant, power: m.identity.power});
    this.phase = 'handoff'; this.detail = 'Part saved. Opening a fresh page for the next test…'; this.render();
    this.navigates = true;
    try {
      // A real reload avoids keeping prior measured documents in history cache.
      // Update this entry first so the saved workload remains visible in the URL.
      history.replaceState(history.state, '', location.pathname + '?' + query.toString());
      location.reload();
    }
    catch (error) {this.navigates = false; this.phase = 'ready'; throw error;}
  }
  private async claim(): Promise<void> {
    const m = this.manifest;
    if (!this.store || !m || m.status !== 'ready' || !m.handoffToken || this.busy) return;
    if (document.hidden) {this.phase = 'ready'; this.detail = 'Bring this page to the foreground, then tap Continue test.'; this.render(); return;}
    this.busy = true;
    try {
      if (m.identity.buildId !== this.bridge.buildId) throw new Error('The saved test belongs to a different build. Save it and start a new test.');
      if (!await this.ownership()) throw new Error('Another page owns this test.');
      this.manifest = await this.store.claim({suiteId: m.id, token: m.handoffToken, identity: m.identity, documentId: this.documentId});
      if (this.stopped || document.hidden) {await this.interrupt('Setup stopped before camera startup.'); this.phase = 'partial'; this.detail = 'Setup was interrupted. Save the partial ZIP, then delete it to start again.'; return;}
      this.busy = false; await this.runCamera();
    } catch (error) {this.phase = 'blocked'; this.detail = message(error);}
    finally {this.busy = false; this.render();}
  }
  private async runCamera(): Promise<void> {
    const m = this.manifest, claim = m?.active, chunk = m?.plan[m.nextChunkIndex];
    if (!m || !claim || claim.documentId !== this.documentId || !chunk || this.busy) return;
    if (document.hidden || this.stopped) {this.phase = 'waiting-camera'; this.detail = 'Bring this page to the foreground, then tap Continue test.'; this.render(); return;}
    this.busy = true; this.stopped = false; this.cameraAttempts++;
    this.phase = 'opening'; this.detail = `${labels[chunk.condition]} · opening camera and preparing the selected pipeline…`; this.render();
    try {
      await this.bridge.runChunk(m.identity, chunk, {suiteId: m.id, chunkId: chunk.id, documentId: this.documentId,
        buildId: this.bridge.buildId, timeOrigin: performance.timeOrigin, condition: chunk.condition, chunkIndex: chunk.index,
        cameraAttempts: this.cameraAttempts});
      if (!this.stopped) {this.phase = 'running'; this.detail = `${labels[chunk.condition]} · warming up…`;}
    } catch (error) {
      if (!this.stopped) {this.bridge.closeCamera(); this.phase = 'waiting-camera'; this.detail = `Setup stopped: ${message(error)} Tap Continue test to retry camera setup, or Stop to keep a partial result.`;}
    } finally {this.busy = false; this.render();}
  }
  private async resume(): Promise<void> {
    if (this.busy) return;
    if (this.pendingReport) {await this.completed(this.pendingReport); return;}
    if (this.phase === 'waiting-camera') {await this.runCamera(); return;}
    if (this.manifest?.status === 'ready') {
      // A manually opened ready page must still get its own clean document.
      this.busy = true;
      try {await this.navigate();} catch (error) {this.detail = message(error);}
      finally {this.busy = false; this.render();}
    }
  }
  async completed(report: Record<string, unknown>): Promise<void> {
    const m = this.manifest, active = m?.active;
    if (!this.store || !m || !active || active.documentId !== this.documentId) return;
    this.pendingReport = report; this.busy = true; this.phase = 'saving'; this.detail = 'Saving this part locally before closing the page…'; this.render();
    try {
      const complete = !this.stopped && report.completed === true && (report.hairDeliveryDrain as {state?: string} | undefined)?.state === 'drained';
      const summary = {completed: complete, sessionId: report.sessionId, performanceTimeOriginMs: performance.timeOrigin,
        windows: (report.windows as {index: number; completed: boolean; summary: Record<string, unknown>}[]).map(w => ({index: w.index, completed: w.completed,
          frames: w.summary.frames, completedArFps: w.summary.completedArFps, durationMs: w.summary.durationMs,
          frameAgeMs: w.summary.frameAgeMs, completionGapMsIncludingEndpoints: w.summary.completionGapMsIncludingEndpoints,
          coverage: w.summary.coverage, camera: w.summary.camera})),
        analysisBins: (report.analysisBins as {index: number; completed: boolean; summary: Record<string, unknown>}[] | undefined)?.map(w => ({index: w.index, completed: w.completed,
          frames: w.summary.frames, completedArFps: w.summary.completedArFps, durationMs: w.summary.durationMs,
          frameAgeMs: w.summary.frameAgeMs, completionGapMsIncludingEndpoints: w.summary.completionGapMsIncludingEndpoints, coverage: w.summary.coverage, camera: w.summary.camera})) ?? []};
      this.manifest = await this.store.complete({suiteId: m.id, token: active.token, documentId: this.documentId, report, summary, complete,
        reason: String(report.cancelledReason ?? 'Pending frame work could not fully drain.')});
      this.pendingReport = null;
      if (this.manifest.status === 'ready') {await this.navigate(); return;}
      this.phase = this.manifest.status; this.detail = complete ? 'All tests are complete. Save one ZIP and send it back for analysis.'
        : 'The test stopped partway through. Save the ZIP; completed parts and this partial report are preserved.';
    } catch (error) {
      if (!this.pendingReport && this.manifest?.status === 'ready') {this.phase = 'ready'; this.detail = `This part is saved. ${message(error)} Tap Continue test to retry opening the next page.`;}
      else {this.phase = 'save-error'; this.detail = `${message(error)} This page still holds the unsaved part. Tap Retry saving, or Save ZIP to keep all available data.`;}
    }
    finally {this.busy = false; this.render();}
  }
  private async interrupt(reason: string): Promise<void> {
    const m = this.manifest, active = m?.active;
    if (!this.store || !m || !active || active.documentId !== this.documentId) return;
    try {this.manifest = await this.store.interrupt({suiteId: m.id, token: active.token, documentId: this.documentId, reason});}
    catch (error) {this.detail = message(error);}
  }
  async stop(reason = 'Stopped by the user; this part did not complete.'): Promise<void> {
    if (this.stopped || this.phase === 'saving' || this.phase === 'handoff') return;
    this.stopped = true; this.abort?.abort();
    if (this.bridge.stopChunk(reason)) {this.phase = 'saving'; this.detail = 'Stopping and saving the partial result…'; this.render(); return;}
    this.bridge.closeCamera(); await this.interrupt(reason);
    this.phase = this.manifest ? 'partial' : 'idle'; this.detail = reason; this.busy = false; this.render();
  }
  private async buildFile(): Promise<File> {
    if (!this.store) throw new Error('Local result storage is not ready.');
    const parts = await this.store.exportParts();
    if (!parts) throw new Error('There is no saved stability test to export.');
    const reports = parts.reports.map((part, index) => ({...part, filename: `part-${String(index + 1).padStart(2, '0')}.json`}));
    if (this.pendingReport) reports.push({chunkId: this.manifest?.active?.chunkId ?? 'unsaved', documentId: this.documentId,
      summary: {persisted: false}, completed: false, blob: new Blob([JSON.stringify(this.pendingReport)], {type: 'application/json'}), filename: 'unsaved-part.json'});
    const manifest = {schema: this.mode === 'readback' ? 'ar-g-readback-export-v1' : 'ar-g-stability-export-v1', suite: parts.manifest,
      parts: reports.map(({blob: _blob, ...part}) => part),
      clocks: 'Each part keeps its own performanceTimeOriginMs and session/document ownership. Never concatenate page-relative clocks.',
      measurement: this.mode === 'readback'
        ? 'G control and G readback diagnostic each plan 180 uninterrupted seconds in a fresh document, with six export-only 30-second analysis bins. Setup, reload, storage and user pauses are not measured FPS. The diagnostic adds timer observations around existing operations, not a speed optimization.'
        : 'Each condition plans 180 seconds. Continuous G is uninterrupted with six 30-second analysis bins; restarted G rebuilds between six windows; fresh-page G uses six separate documents. Setup, reload, storage and user pauses are not measured FPS.',
      limitations: (this.mode === 'readback' ? 'Timer observations themselves add overhead in the diagnostic; comparison against G is an overhead control, not proof of an FPS improvement. ' : '') + 'Fixed order, thermal state and movement can affect rates. Compare within-condition slopes, matching masks, frame age and stalls; repeat with reversed condition order. No candidate promotion or automatic winner.',
      privacy: 'Scalar measurements only; no video, images, detections or masks. No uploads.'};
    const archive = await createFilesArchive([{filename: 'telemetry.json', blob: new Blob([JSON.stringify(manifest)], {type: 'application/json'})},
      ...reports.map(({filename, blob}) => ({filename, blob}))]);
    return new File([archive], `${this.mode === 'readback' ? 'ar-g-readback' : 'ar-g-stability'}-${new Date().toISOString().replaceAll(':', '-')}.zip`, {type: 'application/zip'});
  }
  private async download(): Promise<void> {
    if (this.busy || this.isActive() || !this.manifest) return;
    this.busy = true; this.detail = 'Preparing one ZIP from locally saved parts…'; this.render();
    try {
      this.file = await this.buildFile(); if (this.fileUrl) URL.revokeObjectURL(this.fileUrl); this.fileUrl = URL.createObjectURL(this.file);
      const link = document.createElement('a'); link.href = this.fileUrl; link.download = this.file.name; document.body.append(link); link.click(); link.remove();
      this.detail = `ZIP ready · ${(this.file.size / 1048576).toFixed(1)} MB. If it did not download, tap Save ZIP again or Share. Saved parts remain on this browser.`;
    } catch (error) {this.detail = message(error);}
    finally {this.busy = false; this.render();}
  }
  private async share(): Promise<void> {
    if (!this.file || typeof navigator.share !== 'function') return;
    try {await navigator.share({files: [this.file], title: this.mode === 'readback' ? 'G readback diagnostic measurements' : 'G stability measurements'});}
    catch (error) {if (!(error instanceof DOMException && error.name === 'AbortError')) {this.detail = message(error); this.render();}}
  }
  private async clear(): Promise<void> {
    if (this.busy || this.isActive() || !this.store || !this.manifest || !this.ownsLock) return;
    this.busy = true;
    try {
      await this.store.delete(this.manifest.id); this.manifest = null; this.pendingReport = null; this.file = null;
      if (this.fileUrl) URL.revokeObjectURL(this.fileUrl); this.fileUrl = null; sessionStorage.removeItem(this.handoffKey);
      this.phase = 'idle'; this.detail = 'Saved stability results deleted from this browser. Choose settings for a new test.';
    } catch (error) {this.detail = message(error);}
    finally {this.busy = false; this.render();}
  }
}
