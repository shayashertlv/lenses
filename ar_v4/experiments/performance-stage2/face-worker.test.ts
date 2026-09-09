import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {test} from 'node:test';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';
import type {DetectorRequest, DetectorResponse} from '../../references/perfect-temples/src/runtime/protocol.ts';
import type {TimedDetectorRequest, TimedDetectorResponse} from './face-timing.ts';

// Run the actual pinned and instrumented worker modules with the same deterministic
// MediaPipe boundary. This checks payload/configuration equivalence, not GPU speed.
const workerUrls = [new URL('../../references/perfect-temples/src/runtime/detector.worker.ts', import.meta.url).href,
  new URL('./face-detector.worker.ts', import.meta.url).href];
const environmentSymbol = Symbol.for('face-worker-timing-test-environment');
const globals = globalThis as unknown as Record<symbol, Environment>;
const hash = (value: unknown): string => createHash('sha256').update(JSON.stringify(value)).digest('hex');
type Response = DetectorResponse | TimedDetectorResponse;
class Bitmap {
  readonly width = 960; readonly height = 640; closes = 0;
  close(): void {this.closes++;}
}
interface Environment {
  scope: {onmessage: ((event: MessageEvent<DetectorRequest | TimedDetectorRequest>) => Promise<void>) | null;
    postMessage(message: Response): void};
  performance: {now(): number};
  clock: number;
  replies: Response[];
  resolverArgs: unknown[] | null;
  options: unknown;
  detections: {image: unknown; timestampMs: number}[];
  output: unknown;
  startup: Promise<void> | null;
}
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '@mediapipe/tasks-vision') return {url: 'face-worker-test:mediapipe', shortCircuit: true};
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url === 'face-worker-test:mediapipe') return {format: 'module', shortCircuit: true, source: `
      export class FilesetResolver {
        static async forVisionTasks(...args) {
          const environment = globalThis[Symbol.for('face-worker-timing-test-environment')];
          environment.resolverArgs = args;
          if (environment.startup) await environment.startup;
          return {environment};
        }
      }
      export class FaceLandmarker {
        static async createFromOptions(fileset, options) {
          const environment = fileset.environment;
          environment.options = structuredClone(options);
          return {detectForVideo(image, timestampMs) {
            environment.detections.push({image, timestampMs});
            environment.clock += 5;
            return environment.output;
          }};
        }
      }`};
    const baseUrl = url.split('?')[0]!;
    if (workerUrls.includes(baseUrl)) {
      const source = `const environment = globalThis[Symbol.for('face-worker-timing-test-environment')];
        const self = environment.scope; const performance = environment.performance;\n${readFileSync(new URL(baseUrl), 'utf8')}`;
      const compiled = transformSync(fileURLToPath(baseUrl), source);
      if (compiled.errors.length) throw new Error(JSON.stringify(compiled.errors));
      return {format: 'module', shortCircuit: true, source: compiled.code};
    }
    return nextLoad(url, context);
  },
});
let nextModuleId = 1;
async function worker(instrumented: boolean, t: {after(fn: () => void): void}): Promise<Environment> {
  const environment: Environment = {
    scope: {onmessage: null, postMessage(message) {environment.replies.push(structuredClone(message));}},
    performance: {now: () => environment.clock}, clock: 0, replies: [], resolverArgs: null,
    options: null, detections: [], output: {faceLandmarks: [], facialTransformationMatrixes: []}, startup: null,
  };
  globals[environmentSymbol] = environment;
  await import(`${workerUrls[Number(instrumented)]}?test=${nextModuleId++}`);
  t.after(() => {if (globals[environmentSymbol] === environment) delete globals[environmentSymbol];});
  return environment;
}
async function send(environment: Environment, request: DetectorRequest, nonce = 'worker-session-1'): Promise<Response> {
  await environment.scope.onmessage!(new MessageEvent('message', {data: {...request, sessionNonce: nonce}}));
  return environment.replies.at(-1)!;
}
const initialization = (delegate: 'GPU' | 'CPU'): DetectorRequest => ({type: 'initialize', id: 1, delegate,
  modelUrl: 'http://127.0.0.1:8069/models/face_landmarker.task', wasmRoot: 'http://127.0.0.1:8069/mediapipe/'});
function faceOutput() {
  return {faceLandmarks: [Array.from({length: 478}, (_, index) => ({x: index / 478 - .25, y: 1.1, z: -.01}))],
    facialTransformationMatrixes: [{data: new Float32Array(Array.from({length: 16}, (_, index) => index / 16))}]};
}

test('instrumented worker preserves exact MediaPipe configuration, image/pose input and face/no-face Detection hashes', async t => {
  for (const delegate of ['GPU', 'CPU'] as const) {
    const pinned = await worker(false, t); await send(pinned, initialization(delegate));
    const timed = await worker(true, t); await send(timed, initialization(delegate));
    assert.deepEqual(timed.resolverArgs, pinned.resolverArgs);
    assert.deepEqual(timed.resolverArgs, ['http://127.0.0.1:8069/mediapipe/', true]);
    assert.deepEqual(timed.options, pinned.options);
    assert.deepEqual(timed.options, {baseOptions: {modelAssetPath: 'http://127.0.0.1:8069/models/face_landmarker.task', delegate}, runningMode: 'VIDEO', numFaces: 1,
    outputFacialTransformationMatrixes: true, outputFaceBlendshapes: false});
    for (let frame = 0; frame < 2; frame++) {
      const output = frame === 0 ? faceOutput() : {faceLandmarks: [], facialTransformationMatrixes: []};
      pinned.output = output; timed.output = output;
      const referenceImage = new Bitmap(), timedImage = new Bitmap(), timestampMs = 10 + frame;
      const reference = await send(pinned, {type: 'detect', id: frame + 2, image: referenceImage as unknown as ImageBitmap, timestampMs});
      const observed = await send(timed, {type: 'detect', id: frame + 2, image: timedImage as unknown as ImageBitmap, timestampMs});
      assert.equal(reference.type, 'result'); assert.equal(observed.type, 'result');
      if (reference.type !== 'result' || observed.type !== 'result') throw new Error('Expected worker results.');
      assert.equal(hash(observed.detection), hash(reference.detection));
      assert.deepEqual(Object.keys(observed.detection), ['landmarks', 'matrix', 'inferenceMs']);
      assert.equal(observed.detection.inferenceMs, 5);
      assert.deepEqual(timed.detections.at(-1), {image: timedImage, timestampMs});
      assert.equal(referenceImage.closes, 1); assert.equal(timedImage.closes, 1);
      assert.ok('timing' in observed); const timing = observed.timing!;
      assert.equal(observed.sessionNonce, 'worker-session-1');
      assert.equal(timing.requestId, frame + 2); assert.equal(timing.timestampMs, timestampMs);
      assert.equal(timing.delegate, delegate); assert.equal(timing.width, 960); assert.equal(timing.height, 640);
      assert.equal(timing.inferenceMs, 5); assert.equal(timing.elapsedMs, 5);
      assert.equal(timing.requestChecksMs + timing.inferenceMs + timing.extractionMs + timing.validationMs, timing.elapsedMs);
    }
  }
});

test('worker preserves validation and stale-frame failures, closes every image and rejects another session', async t => {
  const pinned = await worker(false, t); await send(pinned, initialization('GPU'));
  const timed = await worker(true, t); await send(timed, initialization('GPU'));
  for (let frame = 0; frame < 2; frame++) {
    const output = faceOutput(); if (frame === 0) output.faceLandmarks[0]![0]!.x = NaN;
    pinned.output = output; timed.output = output;
    const images = [new Bitmap(), new Bitmap()], replies: Response[] = [];
    for (const [index, environment] of [pinned, timed].entries()) replies.push(await send(environment,
      {type: 'detect', id: frame + 2, image: images[index] as unknown as ImageBitmap, timestampMs: 10}));
    assert.equal(replies[0]!.type, 'error'); assert.equal(replies[1]!.type, 'error');
    if (replies[0]!.type !== 'error' || replies[1]!.type !== 'error') throw new Error('Expected validation failures.');
    assert.equal(replies[0]!.message, replies[1]!.message);
    assert.equal('timing' in replies[1]!, false); assert.ok(images.every(image => image.closes === 1));
  }
  const image = new Bitmap(), previousCalls = timed.detections.length;
  const wrongSession = await send(timed, {type: 'detect', id: 4, image: image as unknown as ImageBitmap, timestampMs: 11}, 'different-session');
  assert.equal(wrongSession.type, 'error'); if (wrongSession.type === 'error') assert.match(wrongSession.message, /another detector session/);
  assert.equal(image.closes, 1); assert.equal(timed.detections.length, previousCalls);
});

test('busy initialization rejects and closes an image without entering MediaPipe', async t => {
  const timed = await worker(true, t); let finishStartup!: () => void;
  timed.startup = new Promise(resolve => {finishStartup = resolve;});
  const initializing = send(timed, initialization('GPU')), image = new Bitmap();
  const busy = await send(timed, {type: 'detect', id: 2, image: image as unknown as ImageBitmap, timestampMs: 1});
  assert.equal(busy.type, 'error'); if (busy.type === 'error') assert.match(busy.message, /busy/);
  assert.equal(image.closes, 1); assert.equal(timed.detections.length, 0);
  finishStartup(); assert.equal((await initializing).type, 'ready');
});

test.after(() => {hooks.deregister(); delete globals[environmentSymbol];});
