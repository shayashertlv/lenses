/** Local scalar receipts across real document reloads. Never used in the frame loop. */
export type StabilityCondition = 'continuous' | 'restarted' | 'fresh-page';
export type StabilitySelection = StabilityCondition | 'all';
export type StabilityDirection = 'forward' | 'reverse';
export interface StabilityIdentity {
  buildId: string; eyewearId: string; hairModelId: string; variant: 'hair' | 'accepted';
  sourceWidth: number; sourceHeight: number; power: string;
}
export interface StabilityChunk {
  id: string; index: number; condition: StabilityCondition; conditionWindow: number;
  windowCount: number; measureMs: number; totalMeasureMs: number;
}
export interface StabilityClaim {
  chunkId: string; token: string; documentId: string; startedAtMs: number;
}
export interface StabilityReceipt {
  chunkId: string; documentId: string; completed: boolean; completedAtMs: number;
  bytes: number; summary: Record<string, unknown>;
}
export interface StabilityManifest {
  schema: 'ar-g-stability-suite-v1'; id: string; identity: StabilityIdentity;
  selection: StabilitySelection; direction: StabilityDirection; plan: StabilityChunk[];
  createdAtMs: number; updatedAtMs: number; status: 'ready' | 'running' | 'complete' | 'partial';
  nextChunkIndex: number; handoffToken: string | null; active: StabilityClaim | null;
  results: StabilityReceipt[];
  interrupted: {chunkId: string; documentId: string; reason: string; endedAtMs: number} | null;
}
export interface CreateStabilityOptions {
  identity: StabilityIdentity; selection: StabilitySelection; direction: StabilityDirection;
}
export interface ClaimStabilityOptions {
  suiteId: string; token: string; identity: StabilityIdentity; documentId: string;
}
export interface CompleteStabilityOptions {
  suiteId: string; token: string; documentId: string; report: Record<string, unknown>;
  summary: Record<string, unknown>; complete: boolean; reason?: string;
}
export interface InterruptStabilityOptions {
  suiteId: string; token: string; documentId: string; reason: string;
}
export interface StabilityExport {
  manifest: StabilityManifest;
  reports: {chunkId: string; documentId: string; report: Record<string, unknown>;
    summary: Record<string, unknown>; completed: boolean}[];
}
export interface StabilityExportParts {
  manifest: StabilityManifest;
  reports: {chunkId: string; documentId: string; blob: Blob;
    summary: Record<string, unknown>; completed: boolean}[];
}
export const STABILITY_DATABASE = 'ar-g-stability-v1';
export const STABILITY_MAX_REPORT_BYTES = 48 * 1024 * 1024;
export const STABILITY_MAX_SUITE_BYTES = 192 * 1024 * 1024;
const MAX_SUMMARY_BYTES = 128 * 1024;
const CONDITIONS: readonly StabilityCondition[] = ['continuous', 'restarted', 'fresh-page'];
export class StabilityStoreError extends Error {
  readonly code: 'storage' | 'existing' | 'stale' | 'mismatch' | 'invalid';
  constructor(code: StabilityStoreError['code'], message: string) { super(message); this.name = 'StabilityStoreError'; this.code = code; }
}
const fail = (code: StabilityStoreError['code'], message: string): never => {throw new StabilityStoreError(code, message);};
const nonempty = (value: unknown, max = 512): value is string => typeof value === 'string' && value.length > 0 && value.length <= max;
const copy = <T>(value: T): T => structuredClone(value);

export function createStabilityPlan(selection: StabilitySelection, direction: StabilityDirection): StabilityChunk[] {
  if (!(selection === 'all' || CONDITIONS.includes(selection)) || !['forward', 'reverse'].includes(direction))
    fail('invalid', 'Invalid stability condition or order.');
  const conditions = selection === 'all' ? [...CONDITIONS] : [selection];
  if (direction === 'reverse') conditions.reverse();
  const plan: StabilityChunk[] = [];
  for (const condition of conditions) {
    const pages = condition === 'fresh-page' ? 6 : 1;
    for (let page = 0; page < pages; page++) {
      const windowCount = condition === 'restarted' ? 6 : 1;
      const measureMs = condition === 'continuous' ? 180_000 : 30_000;
      plan.push({id: `${condition}-${page + 1}`, index: plan.length, condition,
        conditionWindow: page, windowCount, measureMs, totalMeasureMs: windowCount * measureMs});
    }
  }
  return plan;
}
function validateIdentity(value: StabilityIdentity): void {
  if (!value || !nonempty(value.buildId) || !nonempty(value.eyewearId) || !nonempty(value.hairModelId)
    || !nonempty(value.power, 128) || !['hair', 'accepted'].includes(value.variant)
    || !Number.isInteger(value.sourceWidth) || value.sourceWidth < 1
    || !Number.isInteger(value.sourceHeight) || value.sourceHeight < 1)
    fail('invalid', 'A fixed build, glasses, hair model, resolution and power setting are required.');
}
export function sameStabilityIdentity(a: StabilityIdentity, b: StabilityIdentity): boolean {
  return a.buildId === b.buildId && a.eyewearId === b.eyewearId && a.hairModelId === b.hairModelId
    && a.variant === b.variant && a.sourceWidth === b.sourceWidth && a.sourceHeight === b.sourceHeight && a.power === b.power;
}
export function createStabilityManifest(options: CreateStabilityOptions,
  ids: {suiteId: string; token: string; now: number}): StabilityManifest {
  validateIdentity(options.identity);
  if (!nonempty(ids.suiteId) || !nonempty(ids.token) || !Number.isFinite(ids.now)) fail('invalid', 'Invalid suite identity.');
  return {schema: 'ar-g-stability-suite-v1', id: ids.suiteId, identity: copy(options.identity),
    selection: options.selection, direction: options.direction, plan: createStabilityPlan(options.selection, options.direction),
    createdAtMs: ids.now, updatedAtMs: ids.now, status: 'ready', nextChunkIndex: 0, handoffToken: ids.token,
    active: null, results: [], interrupted: null};
}
export function claimStabilityManifest(manifest: StabilityManifest, options: ClaimStabilityOptions, now: number): StabilityManifest {
  validateIdentity(options.identity);
  if (!sameStabilityIdentity(manifest.identity, options.identity)) fail('mismatch', 'The saved suite uses a different build or workload. Export it before starting another suite.');
  if (manifest.id !== options.suiteId || manifest.status !== 'ready' || manifest.active
    || !nonempty(options.token) || manifest.handoffToken !== options.token
    || !nonempty(options.documentId) || manifest.results.some(result => result.documentId === options.documentId))
    fail('stale', 'This page cannot claim the saved test. Another page may own it, or this handoff was already used.');
  const chunk = manifest.plan[manifest.nextChunkIndex];
  if (!chunk) return fail('stale', 'There is no pending test to claim.');
  const result = copy(manifest);
  result.status = 'running'; result.handoffToken = null; result.updatedAtMs = now;
  result.active = {chunkId: chunk.id, token: options.token, documentId: options.documentId, startedAtMs: now};
  return result;
}
function requireOwner(manifest: StabilityManifest, options: Pick<InterruptStabilityOptions, 'suiteId' | 'token' | 'documentId'>): StabilityClaim {
  const active = manifest.active;
  if (manifest.id !== options.suiteId || manifest.status !== 'running' || !active
    || active.token !== options.token || active.documentId !== options.documentId)
    return fail('stale', 'This page no longer owns the running test. Its report has not overwritten the saved suite.');
  return active;
}
export function completeStabilityManifest(manifest: StabilityManifest,
  options: Omit<CompleteStabilityOptions, 'report'> & {bytes: number; nextToken: string; now: number}): StabilityManifest {
  const active = requireOwner(manifest, options);
  if (!Number.isInteger(options.bytes) || options.bytes < 1 || options.bytes > STABILITY_MAX_REPORT_BYTES
    || manifest.results.reduce((total, result) => total + result.bytes, 0) + options.bytes > STABILITY_MAX_SUITE_BYTES)
    fail('storage', 'The local measurement storage limit was reached. Save the current report before leaving this page.');
  if (!nonempty(options.nextToken)) fail('invalid', 'Missing next-page ownership token.');
  const result = copy(manifest);
  result.results.push({chunkId: active.chunkId, documentId: active.documentId, completed: options.complete,
    completedAtMs: options.now, bytes: options.bytes, summary: copy(options.summary)});
  result.active = null; result.updatedAtMs = options.now;
  if (options.complete) {
    result.nextChunkIndex++;
    result.status = result.nextChunkIndex === result.plan.length ? 'complete' : 'ready';
    result.handoffToken = result.status === 'ready' ? options.nextToken : null;
  } else {
    result.status = 'partial'; result.handoffToken = null;
    result.interrupted = {chunkId: active.chunkId, documentId: active.documentId,
      reason: options.reason?.slice(0, 2048) || 'The test stopped before completion.', endedAtMs: options.now};
  }
  return result;
}
export function interruptStabilityManifest(manifest: StabilityManifest, options: InterruptStabilityOptions, now: number): StabilityManifest {
  const active = requireOwner(manifest, options), result = copy(manifest);
  result.status = 'partial'; result.active = null; result.handoffToken = null; result.updatedAtMs = now;
  result.interrupted = {chunkId: active.chunkId, documentId: active.documentId,
    reason: options.reason.slice(0, 2048) || 'The test was interrupted.', endedAtMs: now};
  return result;
}

const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
  ? value as Record<string, unknown> : {};
/** Bind every raw report to the claimed G document; scalars from different page clocks remain separate. */
export function validateStabilityCompletion(manifest: StabilityManifest, options: CompleteStabilityOptions): void {
  const active = requireOwner(manifest, options), report = options.report;
  const chunk = manifest.plan[manifest.nextChunkIndex]!;
  const protocol = record(report.protocol), metadata = record(report.metadata), ownership = record(metadata.stability);
  const workload = record(report.workload), build = record(metadata.build);
  const study = chunk.condition === 'continuous' ? 'g-continuous' : chunk.condition === 'restarted' ? 'g-restart' : 'g-page';
  const windows = Array.isArray(report.windows) ? report.windows : [];
  const rows = Array.isArray(report.rows) ? report.rows : null;
  const order = Array.isArray(protocol.order) ? protocol.order : [];
  if (report.schema !== 'ar-continuous-comparison-v1' || !nonempty(report.sessionId)
    || ownership.suiteId !== manifest.id || ownership.chunkId !== active.chunkId || ownership.documentId !== active.documentId
    || ownership.buildId !== manifest.identity.buildId || build.id !== manifest.identity.buildId
    || typeof ownership.timeOrigin !== 'number' || !Number.isFinite(ownership.timeOrigin) || ownership.timeOrigin <= 0
    || metadata.performanceTimeOriginMs !== ownership.timeOrigin
    || protocol.studyOptions !== study || protocol.measureMs !== chunk.measureMs
    || windows.length !== chunk.windowCount || order.length !== chunk.windowCount || order.some(value => value !== 'g')
    || windows.some(value => record(value).pipeline !== 'g')
    || workload.eyewearId !== manifest.identity.eyewearId || workload.hairModelId !== manifest.identity.hairModelId
    || workload.variant !== manifest.identity.variant || workload.sourceWidth !== manifest.identity.sourceWidth
    || workload.sourceHeight !== manifest.identity.sourceHeight
    || !rows || rows.some(value => {
      const row = record(value), fields = record(row.fields);
      const diagnosticSession = !options.complete && row.phase === 'excluded' && row.exclusion === 'session-mismatch';
      const diagnosticPipeline = row.phase === 'excluded' && row.exclusion === 'previous-or-unrequested-pipeline';
      return (fields.sessionId !== report.sessionId && !diagnosticSession) || (fields.pipeline !== 'g' && !diagnosticPipeline);
    }))
    fail('mismatch', 'The report does not match this G test, page clock, build or fixed workload. It has not advanced the saved suite.');
  if (options.complete && (report.completed !== true || report.partial !== false
    || windows.some(value => record(value).completed !== true) || record(report.hairDeliveryDrain).state !== 'drained'))
    fail('invalid', 'This test or its pending hair work is incomplete. Save it as a partial report.');
}

// The existing continuous exporter emits scalar trees. Reject binary/frame data rather than retaining it locally by accident.
const forbidden = /(^|\.)(sourceIdentity|\w*SHA256|\w*PngDataUrl|landmarks|categoryBase64|imageData|pixels|maskBytes|sourceImage|detection)(\.|$)/i;
export function encodeStabilityReport(report: Record<string, unknown>): Blob {
  const ancestors = new Set<object>();
  const visit = (value: unknown, depth: number): void => {
    if (depth > 40) fail('invalid', 'Measurement metadata is too deeply nested.');
    if (value === null || value === undefined || typeof value === 'boolean') return;
    if (typeof value === 'number') {if (!Number.isFinite(value)) fail('invalid', 'Measurement data contains a non-finite number.'); return;}
    if (typeof value === 'string') {if (/^data:|;base64,/i.test(value) || value.length > 16_384) fail('invalid', 'Only scalar measurement data can be saved.'); return;}
    if (typeof value !== 'object' || ArrayBuffer.isView(value) || value instanceof ArrayBuffer
      || value instanceof Blob || (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null))
      fail('invalid', 'Only scalar measurement data can be saved.');
    if (ancestors.has(value)) fail('invalid', 'Measurement metadata contains a cycle.');
    ancestors.add(value);
    for (const [key, child] of Object.entries(value)) {
      if (forbidden.test(key)) fail('invalid', 'Image or detection payloads cannot be saved in the stability suite.');
      visit(child, depth + 1);
    }
    ancestors.delete(value);
  };
  visit(report, 0);
  const blob = new Blob([JSON.stringify(report)], {type: 'application/json'});
  if (blob.size > STABILITY_MAX_REPORT_BYTES) fail('storage', 'This report exceeds the local storage limit. Save it before leaving this page.');
  return blob;
}
const storageError = (error: unknown): StabilityStoreError => error instanceof StabilityStoreError ? error
  : new StabilityStoreError('storage', `Local measurement storage failed. Keep this page open and save the current report. ${error instanceof Error ? error.message : String(error)}`);
interface StoredReport {chunkId: string; blob: Blob;}

export class StabilityStore {
  private readonly database: IDBDatabase;
  constructor(database: IDBDatabase) {this.database = database;}
  close(): void {this.database.close();}
  read(): Promise<StabilityManifest | null> {
    return this.manifestTransaction('readonly', manifest => manifest);
  }
  create(options: CreateStabilityOptions): Promise<StabilityManifest> {
    const manifest = createStabilityManifest(options, {suiteId: crypto.randomUUID(), token: crypto.randomUUID(), now: Date.now()});
    return this.manifestTransaction('readwrite', (existing, tx) => {
      if (existing) fail('existing', 'A saved suite already exists. Export it, then explicitly clear it before starting another.');
      tx.objectStore('manifest').put(manifest, 'current'); return manifest;
    }) as Promise<StabilityManifest>;
  }
  claim(options: ClaimStabilityOptions): Promise<StabilityManifest> {
    return this.manifestTransaction('readwrite', (manifest, tx) => {
      if (!manifest) return fail('stale', 'The saved suite no longer exists.');
      const next = claimStabilityManifest(manifest, options, Date.now());
      tx.objectStore('manifest').put(next, 'current'); return next;
    }) as Promise<StabilityManifest>;
  }
  complete(options: CompleteStabilityOptions): Promise<StabilityManifest> {
    // Serialize outside the transaction and outside measurement; old report blobs are never loaded here.
    const blob = encodeStabilityReport(options.report);
    if (encodeStabilityReport(options.summary).size > MAX_SUMMARY_BYTES) return Promise.reject(new StabilityStoreError('invalid', 'The report summary is too large.'));
    return this.manifestTransaction('readwrite', (manifest, tx) => {
      if (!manifest) return fail('stale', 'The saved suite no longer exists.');
      const active = requireOwner(manifest, options);
      validateStabilityCompletion(manifest, options);
      const next = completeStabilityManifest(manifest, {...options, bytes: blob.size, nextToken: crypto.randomUUID(), now: Date.now()});
      tx.objectStore('reports').add({chunkId: active.chunkId, blob} satisfies StoredReport, active.chunkId);
      tx.objectStore('manifest').put(next, 'current'); return next;
    }) as Promise<StabilityManifest>;
  }
  interrupt(options: InterruptStabilityOptions): Promise<StabilityManifest> {
    return this.manifestTransaction('readwrite', (manifest, tx) => {
      if (!manifest) return fail('stale', 'The saved suite no longer exists.');
      const next = interruptStabilityManifest(manifest, options, Date.now());
      tx.objectStore('manifest').put(next, 'current'); return next;
    }) as Promise<StabilityManifest>;
  }
  async exportReports(): Promise<StabilityExport | null> {
    const parts = await this.exportParts();
    if (!parts) return null;
    const reports: StabilityExport['reports'] = [];
    for (const part of parts.reports) reports.push({chunkId: part.chunkId, documentId: part.documentId,
      completed: part.completed, summary: part.summary, report: JSON.parse(await part.blob.text()) as Record<string, unknown>});
    return {manifest: parts.manifest, reports};
  }
  async exportParts(): Promise<StabilityExportParts | null> {
    // Blobs avoid inflating every raw JSON report simultaneously. Each document keeps its own performance clock.
    let blobs: StoredReport[] = [];
    const manifest = await this.manifestTransaction('readonly', (value, tx) => {
      const request = tx.objectStore('reports').getAll();
      request.onsuccess = () => {blobs = request.result as StoredReport[];};
      return value;
    });
    if (!manifest) return null;
    const reports: StabilityExportParts['reports'] = [];
    for (const result of manifest.results) {
      const saved = blobs.find(item => item.chunkId === result.chunkId);
      if (!saved) return fail('storage', 'A saved report is missing. The suite has not been deleted.');
      reports.push({chunkId: result.chunkId, documentId: result.documentId, completed: result.completed,
        summary: result.summary, blob: saved.blob});
    }
    return {manifest, reports};
  }
  async delete(suiteId: string): Promise<void> {
    await this.manifestTransaction('readwrite', (manifest, tx) => {
      if (!manifest || manifest.id !== suiteId) fail('stale', 'The saved suite changed. It has not been deleted.');
      tx.objectStore('reports').clear(); tx.objectStore('manifest').delete('current'); return null;
    });
  }
  private manifestTransaction(mode: IDBTransactionMode,
    operation: (manifest: StabilityManifest | null, transaction: IDBTransaction) => StabilityManifest | null): Promise<StabilityManifest | null> {
    return new Promise((resolve, reject) => {
      let transaction: IDBTransaction;
      try {transaction = this.database.transaction(['manifest', 'reports'], mode);}
      catch (error) {reject(storageError(error)); return;}
      let result: StabilityManifest | null = null, failure: unknown;
      transaction.oncomplete = () => resolve(result);
      transaction.onabort = () => reject(storageError(failure ?? transaction.error ?? 'The storage transaction was aborted.'));
      transaction.onerror = () => {failure ??= transaction.error;};
      const request = transaction.objectStore('manifest').get('current');
      request.onsuccess = () => {
        try {result = operation((request.result as StabilityManifest | undefined) ?? null, transaction);}
        catch (error) {failure = error; transaction.abort();}
      };
    });
  }
}
export function openStabilityStore(factory: IDBFactory | undefined = globalThis.indexedDB): Promise<StabilityStore> {
  if (!factory) return Promise.reject(new StabilityStoreError('storage', 'This browser cannot save measurements across page reloads.'));
  return new Promise((resolve, reject) => {
    let request: IDBOpenDBRequest, settled = false;
    try {request = factory.open(STABILITY_DATABASE, 1);} catch (error) {reject(storageError(error)); return;}
    request.onupgradeneeded = () => {
      request.result.createObjectStore('manifest'); request.result.createObjectStore('reports');
    };
    request.onerror = () => {settled = true; reject(storageError(request.error));};
    request.onblocked = () => {settled = true; reject(new StabilityStoreError('storage', 'Another page is blocking local measurement storage. Close the other test page and retry.'));};
    request.onsuccess = () => {
      if (settled) {request.result.close(); return;}
      settled = true; request.result.onversionchange = () => request.result.close();
      resolve(new StabilityStore(request.result));
    };
  });
}
