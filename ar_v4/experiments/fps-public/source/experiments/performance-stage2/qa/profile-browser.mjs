/** Installed before navigation. Only the isolated QA page receives this synthetic camera. */
export function installSyntheticCamera({fixtureUrl, width, height, requestedFps}) {
  const source = document.createElement('canvas'); source.width = width; source.height = height;
  const context = source.getContext('2d'), image = new Image();
  const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;});
  image.src = fixtureUrl;
  const state = {width, height, requestedFps, streams: [], workers: [], draws: [], longTasks: [], errors: [], longTaskSupported: false};
  window.stage2SyntheticCamera = state;
  let frame = 0;
  const draw = () => {
    if (!image.complete || !image.naturalWidth) return;
    context.drawImage(image, 0, 0, width, height);
    // A changing corner marker forces distinct synthetic source frames. It is
    // outside the face, and is neither a new pose nor wearer-motion evidence.
    context.fillStyle = `rgb(${frame % 256},${(frame >> 8) % 256},37)`; context.fillRect(0, 0, 16, 16);
    state.draws.push({frame: frame++, atMs: performance.now()});
    if (state.draws.length > 30000) state.draws.shift();
  };
  const timer = setInterval(draw, 1000 / requestedFps);
  window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
    await ready; draw(); const stream = source.captureStream(requestedFps); state.streams.push(stream); return stream;
  }});
  const NativeWorker = window.Worker;
  class ObservedWorker extends NativeWorker {
    constructor(url, options) {
      super(url, options);
      this.row = {url: String(url), terminated: false, requests: 0, results: 0, delegate: null, messages: []};
      state.workers.push(this.row);
      this.addEventListener('message', event => {
        if (event.data?.type === 'result') this.row.results++;
        if (event.data?.type === 'error') state.errors.push({worker: this.row.url, message: event.data.message ?? 'Worker error', atMs: performance.now()});
      });
    }
    postMessage(message, transfer = []) {
      if (message?.type === 'initialize') this.row.delegate = message.delegate ?? null;
      if (message?.type === 'detect' || message?.type === 'segment') this.row.requests++;
      return super.postMessage(message, transfer);
    }
    terminate() {this.row.terminated = true; return super.terminate();}
  }
  window.Worker = ObservedWorker;
  try {
    const observer = new PerformanceObserver(list => {
      for (const entry of list.getEntries()) state.longTasks.push({startTimeMs: entry.startTime, durationMs: entry.duration});
      if (state.longTasks.length > 10000) state.longTasks.splice(0, state.longTasks.length - 10000);
    });
    observer.observe({type: 'longtask', buffered: true}); state.longTaskSupported = true;
  } catch { /* Availability is reported; missing support is not a zero-task measurement. */ }
}

export async function warmPipeline({pipeline, warmupMs, minimumMaskFrames, timeoutMs}) {
  const startedAtMs = performance.now(), profiler = window.arPerformanceProfiler;
  const initial = profiler.snapshot(), serial = initial.lastSerial;
  while (performance.now() - startedAtMs < timeoutMs) {
    const rows = profiler.samplesAfter(serial).filter(row => row.pipeline === pipeline && row.variant === 'hair');
    const maskFrames = rows.filter(row => row.hasFace && row.hasMask).length;
    const first = rows[0];
    if (first && performance.now() - first.publishedAtMs >= warmupMs && maskFrames >= minimumMaskFrames)
      return {startedAtMs, finishedAtMs: performance.now(), frames: rows.length, maskFrames, lastSerial: rows.at(-1).serial,
        firstPublishedAtMs: first.publishedAtMs, sessionId: first.sessionId};
    const diagnostic = window.hairLivePreview.diagnostics();
    if (diagnostic.phase !== 'live' || diagnostic.state === 'error') throw new Error(`Live warmup stopped: ${diagnostic.state}`);
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`Warmup for ${pipeline} did not deliver ${minimumMaskFrames} paired hair frames within ${timeoutMs} ms.`);
}

export async function measurePipeline({pipeline, durationMs, sessionId}) {
  const profiler = window.arPerformanceProfiler, startedAtMs = performance.now();
  let cursor = profiler.snapshot().lastSerial;
  const rows = [], serialGaps = [], failures = [];
  const drain = () => {
    const snapshot = profiler.snapshot(), next = profiler.samplesAfter(cursor);
    if (next.length && next[0].serial !== cursor + 1) serialGaps.push({after: cursor, next: next[0].serial, firstAvailable: snapshot.firstAvailableSerial});
    for (const row of next) {
      cursor = row.serial;
      if (row.publishedAtMs >= startedAtMs) rows.push(row);
    }
    const state = window.hairLivePreview.diagnostics();
    if (state.phase !== 'live' || state.sessionId !== sessionId) failures.push({atMs: performance.now(), phase: state.phase, sessionId: state.sessionId});
  };
  const timer = setInterval(drain, 1000);
  try {await new Promise(resolve => setTimeout(resolve, durationMs));}
  finally {clearInterval(timer);}
  drain(); const endedAtMs = performance.now();
  const source = window.stage2SyntheticCamera;
  const measured = rows.filter(row => row.publishedAtMs <= endedAtMs);
  return {pipeline, sessionId, startedAtMs, endedAtMs, requestedDurationMs: durationMs, measuredDurationMs: endedAtMs - startedAtMs,
    rows: measured, serialGaps, failures,
    unexpectedRows: measured.filter(row => row.pipeline !== pipeline || row.sessionId !== sessionId || row.variant !== 'hair').map(row => ({serial: row.serial, pipeline: row.pipeline, sessionId: row.sessionId, variant: row.variant})),
    syntheticDraws: source.draws.filter(row => row.atMs >= startedAtMs && row.atMs <= endedAtMs),
    longTaskSupported: source.longTaskSupported,
    longTasks: source.longTasks.filter(row => row.startTimeMs >= startedAtMs && row.startTimeMs <= endedAtMs),
    workerState: source.workers.map(row => ({...row})), workerErrors: source.errors.slice(),
    diagnostic: window.hairLivePreview.diagnostics(), profiler: profiler.snapshot()};
}

export function releaseState() {
  const source = window.stage2SyntheticCamera;
  return {allTracksEnded: source.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')),
    allWorkersTerminated: source.workers.every(worker => worker.terminated), workerState: source.workers.map(row => ({...row})),
    streamCount: source.streams.length, workerErrors: source.errors.slice()};
}
