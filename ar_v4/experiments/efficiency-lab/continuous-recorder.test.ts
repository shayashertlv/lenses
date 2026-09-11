import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ContinuousCanvasRecorder} from './continuous-recorder.ts';
import type {RecordingEnvironment} from './continuous-recorder.ts';

function fixture(options: {supported?: boolean; types?: string[]; stopThrows?: boolean; startThrows?: boolean} = {}) {
  let tracksStopped = 0;
  let captured = 0;
  let requestedOptions: unknown;
  const recorder = {state: 'inactive', mimeType: 'video/mp4',
    ondataavailable: null as ((event: {data: Blob}) => void) | null,
    onstop: null as (() => void) | null,
    onerror: null as ((event: {error?: {message?: string}}) => void) | null,
    start() {if (options.startThrows) throw new Error('codec unavailable'); this.state = 'recording';},
    stop() {if (options.stopThrows) throw new Error('encoder disconnected'); this.state = 'inactive';},
  };
  const environment: RecordingEnvironment = {supported: options.supported ?? true,
    isTypeSupported: type => (options.types ?? ['video/mp4']).includes(type),
    capture: () => {captured++; return {getTracks: () => [{stop: () => {tracksStopped++;}}]};},
    create: (_stream, value) => {requestedOptions = value; return recorder;}, now: () => 100};
  return {environment, recorder, canvas: {width: 1280, height: 720} as HTMLCanvasElement,
    get tracksStopped() {return tracksStopped;}, get captured() {return captured;}, get requestedOptions() {return requestedOptions;}};
}

test('recording selects supported MP4, captures only output and waits for final encoder chunk', async () => {
  const f = fixture();
  const capture = new ContinuousCanvasRecorder(f.canvas, true, {environment: f.environment});
  assert.deepEqual(f.requestedOptions, {mimeType: 'video/mp4', videoBitsPerSecond: 2_000_000});
  f.recorder.ondataavailable?.({data: new Blob(['first'])});
  const result = capture.stop(500);
  assert.equal(f.tracksStopped, 0);
  f.recorder.ondataavailable?.({data: new Blob(['last'])}); f.recorder.onstop?.();
  const video = await result;
  assert.equal(await video?.text(), 'firstlast'); assert.equal(f.tracksStopped, 1);
  assert.equal(capture.snapshot().status, 'stopped'); assert.equal(capture.snapshot().bytes, 9);
  assert.equal(capture.stop(), result); assert.equal(capture.snapshot().stoppedAtMs, 500);
});

test('disabled and unsupported recording keep timings usable without touching capture', async () => {
  for (const requested of [false, true]) {
    const f = fixture({supported: false});
    const capture = new ContinuousCanvasRecorder(f.canvas, requested, {environment: f.environment});
    assert.equal(capture.snapshot().status, requested ? 'unsupported' : 'disabled');
    assert.equal(await capture.stop(), null); assert.equal(f.captured, 0);
  }
});

test('encoder startup failure detaches callbacks, releases capture, and resolves timing-only finalization', async () => {
  const f = fixture({startThrows: true});
  const capture = new ContinuousCanvasRecorder(f.canvas, true, {environment: f.environment});
  assert.equal(capture.snapshot().status, 'unsupported'); assert.match(capture.snapshot().reason!, /codec unavailable/);
  assert.equal(f.tracksStopped, 1); assert.equal(f.recorder.onstop, null);
  assert.equal(await capture.stop(), null);
});

test('no final chunks is explicit failed video, not a successful empty download', async () => {
  const f = fixture(), capture = new ContinuousCanvasRecorder(f.canvas, true, {environment: f.environment});
  const output = capture.stop(); f.recorder.onstop?.();
  assert.equal(await output, null); assert.equal(capture.snapshot().status, 'failed');
  assert.match(capture.snapshot().reason!, /no video chunks/); assert.equal(f.tracksStopped, 1);
});

test('oversized video ends capture once and retains already collected chunks as partial', async () => {
  const f = fixture(), issues: string[] = [];
  const capture = new ContinuousCanvasRecorder(f.canvas, true, {environment: f.environment, maxBytes: 4, onIssue: value => issues.push(value)});
  f.recorder.ondataavailable?.({data: new Blob(['abc'])});
  f.recorder.ondataavailable?.({data: new Blob(['def'])});
  f.recorder.ondataavailable?.({data: new Blob(['ghi'])}); f.recorder.onstop?.();
  assert.equal(await (await capture.stop())?.text(), 'abc'); assert.equal(issues.length, 1);
  assert.equal(capture.snapshot().bytes, 3); assert.equal(capture.snapshot().status, 'failed');
});

test('synchronous stop failures still release output tracks and resolve partial data', async () => {
  const f = fixture({stopThrows: true}), capture = new ContinuousCanvasRecorder(f.canvas, true, {environment: f.environment});
  f.recorder.ondataavailable?.({data: new Blob(['kept'])});
  assert.equal(await (await capture.stop())?.text(), 'kept');
  assert.equal(capture.snapshot().status, 'failed'); assert.equal(f.tracksStopped, 1);
});

test('unexpected encoder stop is explicit and resolves its retained recording', async () => {
  const f = fixture(), issues: string[] = [];
  const capture = new ContinuousCanvasRecorder(f.canvas, true, {environment: f.environment, onIssue: value => issues.push(value)});
  f.recorder.ondataavailable?.({data: new Blob(['kept'])}); f.recorder.state = 'inactive'; f.recorder.onstop?.();
  assert.equal(await (await capture.stop())?.text(), 'kept'); assert.equal(issues.length, 1);
  assert.equal(capture.snapshot().status, 'failed'); assert.equal(f.tracksStopped, 1);
});
