const COUNTERS = ['offered', 'captured', 'captureMisses', 'published', 'replaced', 'dropped', 'lockedDrops',
  'closedDrops', 'invalidDrops', 'ownedFrames', 'processingFrames', 'pendingFrames', 'inFlightInference',
  'maxOwnedFrames', 'maxInFlightInference', 'inferenceCalls', 'inferenceTotalMs', 'lastInferenceMs',
  'prefetchStarts', 'deferredSubmissions'] as const;
const STARTUP_COUNTERS = ['faceBitmapRequests', 'faceBitmapReady', 'faceRequests', 'faceCompleted',
  'sourceHashes', 'hairRequests', 'inferred', 'prepareCalls', 'prepared'] as const;
type PumpCounters = Record<typeof COUNTERS[number], number | null>;
type StartupCounters = Record<typeof STARTUP_COUNTERS[number], number | null>;
export interface StartupPumpDiagnostic {
  observedAtMs: number;
  settled: boolean;
  publicationObserved: boolean;
  pump: (PumpCounters & {accepting: boolean | null; failed: boolean | null; startup: StartupCounters}) | null;
  video: {readyState: number | null; paused: boolean | null; ended: boolean | null;
    currentTime: number | null; width: number | null; height: number | null} | null;
}
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) && !ArrayBuffer.isView(value)
    ? value as Record<string, unknown> : null;
}
function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}
function flag(value: unknown): boolean | null {return typeof value === 'boolean' ? value : null;}

/** Copy an explicit scalar allowlist; no image, detection or arbitrary diagnostic objects survive. */
export function startupPumpDiagnostic(stats: unknown, videoState: unknown, observedAtMs: number,
  publicationObserved: boolean, settled = false): StartupPumpDiagnostic {
  const raw = record(stats), first = record(raw?.startup), video = record(videoState);
  return {observedAtMs, settled, publicationObserved,
    pump: raw ? {...Object.fromEntries(COUNTERS.map(key => [key, numeric(raw[key])])) as PumpCounters,
      accepting: flag(raw.accepting), failed: flag(raw.failed),
      startup: Object.fromEntries(STARTUP_COUNTERS.map(key => [key, numeric(first?.[key])])) as StartupCounters} : null,
    video: video ? {readyState: numeric(video.readyState), paused: flag(video.paused), ended: flag(video.ended),
      currentTime: numeric(video.currentTime), width: numeric(video.width), height: numeric(video.height)} : null};
}

/** These labels describe observed progress; concurrent hair/face work is not forced into a serial phase. */
export function startupPumpLabel(progress: StartupPumpDiagnostic): string {
  const pump = progress.pump;
  if (!pump || pump.captured === 0) return 'Waiting for a camera image to be captured';
  const first = pump.startup;
  if ((first.prepareCalls ?? 0) > (first.prepared ?? 0)) return 'Preparing the captured AR image';
  if ((first.prepared ?? 0) > 0) return 'Publishing the prepared AR image';
  if ((first.faceRequests ?? 0) > (first.faceCompleted ?? 0)) return 'Waiting for face tracking on the captured image';
  if ((first.faceBitmapRequests ?? 0) > (first.faceBitmapReady ?? 0)) return 'Preparing the captured image for face tracking';
  if ((first.faceCompleted ?? 0) > 0 && (first.inferred ?? 0) === 0) return 'Finishing image validation and hair admission';
  return 'Processing the captured image';
}
