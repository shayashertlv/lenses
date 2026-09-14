import assert from 'node:assert/strict';
import test from 'node:test';
import {installStudy} from '../study.ts';

const KEY = 'ar-fps-candidate-study-v1';
const labels = {g: 'G baseline', queries: 'Queries', identity: 'Identity', combined: 'Combined',
  compose: 'CPU pixels', bookkeeping: 'Statistics', 'next-combined': 'Both CPU changes'};
const settings = {'eyewear-select': 'amber-horizon', 'hair-model-select': 'hair-only', 'variant-select': 'hair'};
const seededStudy = overrides => ({schema: 'ar-fps-candidate-comparison-v1', id: 'study-local-id',
  createdAt: '2026-09-14T00:00:00.000Z', status: 'running', reason: null, buildId: 'local-development', buildAt: null,
  order: ['g', 'combined', 'combined', 'g'], index: 0, segments: [], eyewear: 'amber-horizon', hairModel: 'hair-only', variant: 'hair',
  policy: 'Fixture resumes the actual study implementation.', warmupMs: 5000, measurementMs: 30000, ...overrides});

/** Only browser surfaces are faked. Collection, ownership checks, summarization,
 * persistence, navigation decisions and download serialization use installStudy. */
function harness(t, seed) {
  const original = new Map();
  const replace = (target, key, value) => {
    const descriptor = Object.getOwnPropertyDescriptor(target, key);
    original.set([target, key], descriptor);
    Object.defineProperty(target, key, {configurable: true, writable: true, value});
  };
  t.after(() => {
    for (const [[target, key], descriptor] of [...original].reverse()) {
      if (descriptor) Object.defineProperty(target, key, descriptor); else delete target[key];
    }
  });
  const data = new Map(seed ? [[KEY, JSON.stringify(seed)]] : []);
  const intervals = new Map(), timeouts = new Map(), navigations = [], downloads = [], revoked = [];
  let document, window, elements, rows = [], now = 0, timerId = 0, serial = 0, failStorage = false, reloads = 0, starts = 0;
  class Element extends EventTarget {
    constructor(id = '') { super(); this.id = id; this.value = ''; this.hidden = false; this.disabled = false; this.textContent = ''; this.children = []; }
    replaceChildren(...children) { this.children = children; }
    append(...children) { this.children.push(...children); }
    click() { if (!this.disabled) this.dispatchEvent(new Event('click')); }
    focus() { this.focused = true; }
    select() { this.selected = true; }
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  }
  replace(globalThis, 'performance', {now: () => now, timeOrigin: 1_700_000_000_000});
  replace(globalThis, 'setInterval', callback => { const id = ++timerId; intervals.set(id, callback); return id; });
  replace(globalThis, 'clearInterval', id => intervals.delete(id));
  replace(globalThis, 'setTimeout', callback => { const id = ++timerId; timeouts.set(id, callback); return id; });
  replace(globalThis, 'clearTimeout', id => timeouts.delete(id));
  replace(globalThis, 'sessionStorage', {
    getItem: key => data.get(key) ?? null,
    setItem: (key, value) => { if (failStorage) throw new Error('Storage quota exceeded'); data.set(key, value); },
    removeItem: key => data.delete(key),
  });
  replace(globalThis, 'location', {reload: () => { reloads++; }});
  replace(URL, 'createObjectURL', blob => { downloads.push(blob); return `blob:local-test-${downloads.length}`; });
  replace(URL, 'revokeObjectURL', url => revoked.push(url));
  // Define these once so teardown restores the real process globals correctly.
  replace(globalThis, 'document', null); replace(globalThis, 'window', null);

  const mount = mode => {
    window?.dispatchEvent(new Event('pagehide'));
    intervals.clear(); rows = []; now = 0; serial = 0; starts = 0;
    elements = new Map();
    const element = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
    document = new EventTarget(); document.hidden = false;
    document.getElementById = element;
    document.querySelector = selector => { assert.ok(selector.startsWith('#')); return element(selector.slice(1)); };
    document.createElement = tag => new Element(tag);
    for (const [id, value] of Object.entries(settings)) element(id).value = value;
    element('start').addEventListener('click', () => { starts++; });
    window = new EventTarget(); window.arPerformanceProfiler = {samplesAfter: after => rows.filter(row => row.serial > after)};
    globalThis.document = document; globalThis.window = window;
    return installStudy(mode, labels, (next, start) => navigations.push({mode: next, start}));
  };
  const sample = (at, overrides = {}) => {
    const row = {serial: ++serial, sessionId: 'camera-session-a', sequence: serial, pipeline: 'combined', variant: 'hair',
      capturedAtMs: at - 100, publishedAtMs: at, totalMs: 100, hasFace: true, hasMask: true,
      sourceWidth: 1280, sourceHeight: 720, videoPresentedFrames: serial, composeMs: 5, finalChecksMs: 2,
      sourceHashMs: 0, faceInferenceMs: 10, hairInferenceMs: 15, hairExtractionMs: 3, faceDelegate: 'CPU', hairDelegate: 'GPU',
      fallback: null, native: {'fps.identityMode': 'session-frame', 'fps.sourceHashBytesAvoided': 1280 * 720 * 4,
        'native.speedLab.pbo.reducedGlQueriesUsed': true, 'native.speedLab.pbo.stateQueryCalls': 0,
        'fpsCandidate.detectionIdentity': 'live:detection:private-session:5'}, ...overrides};
    rows.push(row); now = Math.max(now, at); return row;
  };
  const tick = () => { for (const callback of [...intervals.values()]) callback(); };
  const warm = () => { for (const at of [0, 1000, 2000, 5000]) sample(at); tick(); };
  return {mount, sample, tick, warm, navigations, intervals, data, revoked,
    get: id => elements.get(id) ?? document.getElementById(id),
    saved: () => JSON.parse(data.get(KEY) ?? 'null'),
    failStorage: () => { failStorage = true; }, advance: elapsed => { now += elapsed; },
    hide: () => { document.hidden = true; document.dispatchEvent(new Event('visibilitychange')); },
    pagehide: () => window.dispatchEvent(new Event('pagehide')),
    restore: () => { const event = new Event('pageshow'); Object.defineProperty(event, 'persisted', {value: true}); window.dispatchEvent(event); },
    starts: () => starts, reloads: () => reloads,
    download: async () => { elements.get('fps-study-download').click(); assert.ok(downloads.length); return JSON.parse(await downloads.at(-1).text()); },
  };
}

test('actual study runs G/test/test/G with fresh segments and retains missing-work publications', t => {
  const h = harness(t); assert.equal(h.mount('combined'), false);
  h.get('fps-study-start').click();
  assert.deepEqual(h.saved().order, ['g', 'combined', 'combined', 'g']);
  assert.deepEqual(h.navigations, [{mode: 'g', start: true}]);
  for (const [index, mode] of ['g', 'combined', 'combined', 'g'].entries()) {
    assert.equal(h.mount(mode), true); assert.equal(h.starts(), 1); assert.equal(h.intervals.size, 1);
    const sessionId = `fresh-session-${index}`;
    for (const at of [0, 1000, 2000, 5000]) h.sample(at, {sessionId});
    h.sample(10000, {sessionId, hasFace: false, hasMask: false});
    h.sample(20000, {sessionId, hasMask: false}); h.sample(35000, {sessionId}); h.tick();
    const saved = h.saved(), segment = saved.segments[index];
    assert.equal(saved.index, index + 1); assert.equal(segment.mode, mode);
    assert.deepEqual(segment.rows.map(row => row.publishedAtMs), [5000, 10000, 20000, 35000]);
    assert.equal(segment.fps, 0.1); assert.equal(segment.ageP95, 100); assert.equal(segment.intervalP95, 15000);
    assert.equal(segment.trackedFraction, 0.75); assert.equal(segment.maskCoverage, 2 / 3);
    assert.ok(segment.rows.every(row => row.sessionId === sessionId));
    assert.equal(segment.rows[0].faceDelegate, 'CPU'); assert.equal(segment.rows[0].hairDelegate, 'GPU');
    assert.equal(segment.rows[0].native['fps.identityMode'], 'session-frame');
    assert.equal(segment.rows[0].native['fps.sourceHashBytesAvoided'], 1280 * 720 * 4);
    assert.equal(JSON.stringify(segment).includes('live:detection:'), false);
    assert.equal(h.intervals.size, 0);
  }
  assert.equal(h.saved().status, 'complete');
  assert.deepEqual(h.navigations.map(value => value.mode), ['g', 'combined', 'combined', 'g']);
});

test('actual study rejects a replacement session before mixing its row into the current measurement', t => {
  const h = harness(t, seededStudy()); h.mount('g'); h.warm();
  h.sample(6000, {sessionId: 'replacement-camera'}); h.tick();
  const saved = h.saved(); assert.equal(saved.status, 'stopped'); assert.match(saved.reason, /camera session/);
  assert.equal(saved.segments.length, 0); assert.equal(saved.partial.rows.length, 1);
  assert.ok(saved.partial.rows.every(row => row.sessionId === 'camera-session-a'));
  assert.equal(h.intervals.size, 0); assert.equal(h.navigations.length, 0);
});

test('next comparison retains both CPU path counters through navigation and download without frame identities', async t => {
  const h = harness(t); h.mount('next-combined'); h.get('fps-study-start').click();
  assert.deepEqual(h.saved().order, ['g', 'next-combined', 'next-combined', 'g']);
  h.mount('g'); h.warm(); h.sample(35000); h.tick();
  assert.equal(h.navigations.at(-1).mode, 'next-combined');
  assert.equal(h.mount('next-combined'), true); h.warm();
  h.sample(35000, {native: {'fps.cpuCompose.wordComparisons': 900, 'fps.bookkeeping.summaryBuilds': 5,
    'fps.cpuCompose.sourceIdentity': 'private-source-id', 'fps.bookkeeping.detectionIdentity': 'private-detection-id'}});
  h.tick();
  const report = await h.download(), native = report.segments[1].rows.at(-1).native;
  assert.equal(native['fps.cpuCompose.wordComparisons'], 900);
  assert.equal(native['fps.bookkeeping.summaryBuilds'], 5);
  assert.equal(JSON.stringify(report).includes('private-source-id'), false);
  assert.equal(JSON.stringify(report).includes('private-detection-id'), false);
  assert.match(h.get('fps-study-results').children[1].textContent, /Both CPU changes/);
});

test('actual study reports hair with no measured tracking separately from hair off', t => {
  const h = harness(t, seededStudy()); h.mount('g');
  for (const at of [0, 1000, 2000]) h.sample(at);
  for (const at of [5000, 35000]) h.sample(at, {hasFace: false, hasMask: false}); h.tick();
  assert.equal(h.saved().segments[0].maskCoverage, null);
  assert.equal(h.saved().segments[0].trackedFraction, 0);
  const result = h.get('fps-study-results').children[0].textContent;
  assert.match(result, /tracked 0%/); assert.match(result, /hair no tracked frames/); assert.doesNotMatch(result, /hair off/);
});

for (const action of ['mode', 'visibility', 'camera', 'hold', 'settings']) {
  test(`actual study ${action} cancellation preserves partial rows and stops collection`, async t => {
    const h = harness(t, seededStudy()); h.mount('g'); h.warm();
    h.sample(6000, {hasMask: false}); h.tick();
    if (action === 'visibility') h.hide();
    else if (action === 'mode') h.get('fps-mode').dispatchEvent(new Event('change'));
    else if (action === 'settings') h.get('variant-select').dispatchEvent(new Event('change'));
    else h.get(action === 'hold' ? 'hold-frame' : 'stop').click();
    const saved = h.saved(); assert.equal(saved.status, 'stopped'); assert.equal(saved.partial.rows.length, 2);
    assert.equal(saved.partial.rows[1].hasMask, false); assert.equal(h.intervals.size, 0); assert.equal(h.navigations.length, 0);
    assert.equal(h.get('fps-study-download').hidden, false);
    assert.deepEqual(await h.download(), saved);
    h.sample(7000); h.tick(); assert.equal(h.saved().partial.rows.length, 2);
  });
}

test('actual study drops stale running storage after quota failure and retains downloadable partial evidence', async t => {
  const h = harness(t, seededStudy()); h.mount('g'); h.warm();
  assert.equal(h.saved().status, 'running'); h.failStorage(); h.hide();
  assert.equal(h.data.has(KEY), false); assert.equal(h.intervals.size, 0); assert.equal(h.navigations.length, 0);
  const report = await h.download(); assert.equal(report.status, 'stopped'); assert.equal(report.partial.rows.length, 1);
  assert.equal(h.mount('g'), false); assert.equal(h.starts(), 0);
});

test('actual study does not advance to another mode when saving a completed segment fails', async t => {
  const h = harness(t, seededStudy()); h.mount('g'); h.warm(); h.failStorage();
  h.sample(35000); h.tick();
  assert.equal(h.data.has(KEY), false); assert.equal(h.navigations.length, 0); assert.equal(h.intervals.size, 0);
  const report = await h.download(); assert.equal(report.status, 'stopped'); assert.equal(report.segments.length, 1);
  assert.equal(report.segments[0].rows.length, 2); assert.match(report.reason, /quota/);
});

test('actual study clears its timer on pagehide and reloads a persisted bfcache page', t => {
  const h = harness(t, seededStudy()); h.mount('g'); h.warm(); assert.equal(h.intervals.size, 1);
  h.pagehide(); assert.equal(h.intervals.size, 0); h.restore(); assert.equal(h.reloads(), 1);
});

test('actual study keeps elapsed progress across empty polling ticks', t => {
  const h = harness(t, seededStudy()); h.mount('g'); h.warm(); h.sample(7000); h.tick();
  assert.match(h.get('fps-study-status').textContent, /2 \/ 30 seconds/);
  h.advance(250); h.tick(); assert.match(h.get('fps-study-status').textContent, /2 \/ 30 seconds/);
});

test('new comparison persists its public build and refuses to mix restored builds', t => {
  const h = harness(t); h.mount('next-combined'); h.get('fps-study-start').click();
  assert.equal(h.saved().buildId, 'local-development'); assert.equal(h.saved().buildAt, null);
  h.data.set(KEY, JSON.stringify({...h.saved(), buildId: 'different-public-build'}));
  assert.equal(h.mount('g'), false); assert.equal(h.starts(), 0); assert.equal(h.saved().status, 'stopped');
  assert.match(h.saved().reason, /build changed/); assert.equal(h.get('fps-study-download').hidden, false);
});

test('camera permission delay and rejected automatic startup leave a visible retry without consuming measurement', t => {
  const h = harness(t, seededStudy()); h.mount('g');
  h.get('start').disabled = true; h.get('stage-status').textContent = 'STARTING CAMERA';
  h.tick(); h.advance(90_000); h.tick();
  assert.equal(h.saved().status, 'running'); assert.match(h.get('fps-study-status').textContent, /Allow camera access/);
  assert.equal(h.get('fps-study-continue').hidden, false); assert.equal(h.get('fps-study-continue').disabled, true);
  h.get('start').disabled = false; h.get('stage-status').textContent = 'PREVIEW UNAVAILABLE'; h.tick();
  assert.equal(h.get('fps-study-continue').disabled, false); assert.match(h.get('fps-study-status').textContent, /Tap Open camera/);
  h.get('fps-study-continue').click(); assert.equal(h.starts(), 2);
  h.warm(); assert.equal(h.get('fps-study-continue').hidden, true);
  assert.match(h.get('fps-study-status').textContent, /0 \/ 30 seconds/);
});

test('planned initial and segment navigation visibility events preserve the running comparison', t => {
  const h = harness(t); h.mount('next-combined'); h.get('fps-study-start').click();
  h.hide(); h.pagehide();
  assert.equal(h.saved().status, 'running'); assert.equal(h.saved().index, 0);
  h.mount('g'); h.warm(); h.sample(35000); h.tick();
  assert.equal(h.saved().index, 1);
  h.pagehide(); h.hide();
  assert.equal(h.saved().status, 'running'); assert.equal(h.saved().segments.length, 1);
  assert.equal(h.saved().partial, undefined);
  h.mount('next-combined'); h.tick();
  assert.equal(h.get('fps-study-continue').hidden, false);
  assert.equal(h.get('fps-study-continue').disabled, false);
  h.get('fps-study-continue').click(); assert.equal(h.starts(), 2);
  // A later genuine background transition must still stop this measurement.
  h.warm(); h.hide(); assert.equal(h.saved().status, 'stopped');
  assert.match(h.saved().reason, /background/); assert.equal(h.saved().partial.rows.length, 1);
});

test('first-frame watchdog begins after permission and retains a saveable startup failure', async t => {
  const h = harness(t, seededStudy()); h.mount('g');
  h.get('start').disabled = true; h.get('stage-status').textContent = 'STARTING CAMERA';
  h.advance(90_000); h.tick();
  h.get('stage-status').textContent = 'PREPARING MIRROR'; h.tick(); h.advance(59_000); h.tick();
  assert.equal(h.saved().status, 'running'); h.advance(1001); h.tick();
  assert.equal(h.saved().status, 'stopped'); assert.match(h.saved().reason, /60 seconds of mirror preparation/);
  assert.equal(h.get('fps-study-download').hidden, false); assert.equal((await h.download()).segments.length, 0);
});

test('full comparison has a connected persistent save link and selectable JSON covering all segments', async t => {
  const h = harness(t, seededStudy()); h.mount('g'); h.warm(); h.sample(35_000); h.tick();
  h.mount('combined'); h.warm(); h.sample(6000); h.tick();
  const report = await h.download();
  assert.equal(report.segments.length, 1); assert.equal(report.partial.rows.length, 2);
  const link = h.get('fps-study-save'), json = h.get('fps-study-json');
  assert.equal(link.hidden, false); assert.equal(link.href, 'blob:local-test-1');
  assert.equal(link.download, 'ar-fps-comparison-study-local-id.json');
  assert.deepEqual(JSON.parse(json.value), report); assert.equal(h.get('fps-study-export').open, true);
  h.get('fps-study-copy').click(); assert.equal(json.focused, true); assert.equal(json.selected, true);
  assert.equal(json.selectionStart, 0); assert.equal(json.selectionEnd, json.value.length);
  h.advance(120_000); assert.deepEqual(h.revoked, []);
  h.pagehide(); assert.deepEqual(h.revoked, ['blob:local-test-1']);
  assert.equal(h.saved().status, 'stopped'); assert.equal(h.saved().partial.rows.length, 2);
});
