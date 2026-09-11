import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Box3, Matrix4, Vector3} from 'three';
import {TryOnRenderer} from './renderer.ts';
import {DEFAULT_SPEED_OPTIONS} from '../speed-options.ts';
import type {OwnedSourceFrame} from '../speed-options.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';
import {eyewearById} from '../../../references/perfect-temples/src/render/eyewear.ts';
import {emptyBranchLensMetrics} from './branch-lenses.ts';

test('cropped export rerenders its retained pair after lease revocation without changing published pixels or live metrics', () => {
  const priorDocument = globalThis.document, priorImage = globalThis.ImageData;
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: class {
    readonly data: Uint8ClampedArray; readonly width: number; readonly height: number; readonly colorSpace = 'srgb';
    constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
  }});
  let current = true;
  const nativeReads: number[][] = [], branchCalls: {width: number; height: number; detection: Detection; surface: number[]; omit: boolean}[] = [];
  const state = new Map<number, unknown>([[1, null], [2, null], [3, 4], [4, 0], [5, 0], [6, 0]]);
  const gl = {READ_FRAMEBUFFER_BINDING: 1, PIXEL_PACK_BUFFER_BINDING: 2, PACK_ALIGNMENT: 3, PACK_ROW_LENGTH: 4,
    PACK_SKIP_PIXELS: 5, PACK_SKIP_ROWS: 6, READ_FRAMEBUFFER: 7, PIXEL_PACK_BUFFER: 8, RGBA: 9, UNSIGNED_BYTE: 10, NO_ERROR: 0, SAMPLES: 11,
    getParameter: (key: number) => key === 11 ? 4 : state.get(key), isContextLost: () => false, getError: () => 0,
    bindFramebuffer: (_target: number, value: unknown) => {state.set(1, value);}, bindBuffer: (_target: number, value: unknown) => {state.set(2, value);},
    pixelStorei: (key: number, value: number) => {state.set(key, value);},
    readPixels: (x: number, y: number, width: number, height: number, _format: number, _type: number, bytes: Uint8Array) => {
      nativeReads.push([x, y, width, height]); bytes.fill(27);
    }};
  const canvas = (): HTMLCanvasElement => {
    let stored: Uint8ClampedArray | null = null;
    const target = {width: 32, height: 24,
      toDataURL: () => JSON.stringify({width: target.width, height: target.height, first: stored?.[0] ?? null}),
      getContext: (kind: string) => kind === 'webgl2' ? gl : {drawImage: () => {}, clearRect: () => {},
        putImageData: (pixels: ImageData) => {stored = pixels.data.slice();}}};
    return target as unknown as HTMLCanvasElement;
  };
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {createElement: canvas}});
  let renderer: TryOnRenderer | null = null;
  try {
    const matrix = new Matrix4().makeRotationX(.48).setPosition(0, 0, -60).toArray();
    const surface = new Float32Array(468 * 3); for (let i = 2; i < surface.length; i += 3) surface[i] = -60;
    const snapshot = {eyewearModelId: 'tom-ford-clear', rawMatrix: matrix, correctedMatrix: matrix, eyewearMatrix: matrix,
      surfacePositions: surface, yawDegrees: 0, templeClip: null, templeVisibility: null};
    const beauty = new ImageData(new Uint8ClampedArray(32 * 24 * 4).fill(180), 32, 24);
    const native = {eyewear: eyewearById('tom-ford-clear'), captureSnapshot: snapshot, stageTimings: null,
      nativeImagePair: {beauty, camera: null}, setFrameOptions: () => {}, present: () => true, dispose: () => {}};
    const box = (x: number) => new Box3(new Vector3(x, -.01, -.03), new Vector3(x + .01, .01, .02));
    const branch = {present: (frame: HTMLCanvasElement, paired: Detection, geometry: number[], _clip: unknown, _visibility: unknown,
      _drop: unknown, omit = false) => {branchCalls.push({width: frame.width, height: frame.height, detection: structuredClone(paired), surface: geometry, omit}); return true;},
      captureSnapshot: snapshot, clearOwnedFrame: () => {}, dispose: () => {}, branchLensMetrics: emptyBranchLensMetrics(),
      rearDropBounds: {optical: box(-.005), originalArms: [box(-.07), box(.06)], candidateArms: [box(-.07), box(.06)]}, rearDropDiagnostics: {}};
    renderer = Reflect.construct(TryOnRenderer, [canvas(), canvas(), canvas(), native, branch]) as TryOnRenderer;
    const frame = canvas(), sourcePixels = new ImageData(new Uint8ClampedArray(32 * 24 * 4).fill(255), 32, 24);
    const source: OwnedSourceFrame = {canvas: frame, rgba: sourcePixels, sourceSHA256: 'a'.repeat(64),
      generation: 1, sessionId: 1, colorSpace: 'srgb', isCurrent: () => current};
    const detection: Detection = {matrix, landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0})), inferenceMs: 0};
    renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, fewerCopies: true, cropBranchReadback: true}, source);
    assert.equal(renderer.present(frame, detection), true);
    const before = renderer.ownedPixels!.data.slice(), geometry = renderer.captureSnapshot, timings = renderer.stageTimings;
    assert.equal(timings.efficiencyLab.branchRegion.used, true);
    assert.ok(timings.branchReadbackBytes < 32 * 24 * 4); assert.equal(branchCalls.length, 1);
    current = false; frame.width = frame.height = 0; detection.landmarks[0]!.x = 999;
    const diagnostic = renderer.exportDiagnostic()!;
    assert.equal(branchCalls.length, 2); assert.equal(branchCalls[1]!.width, 32); assert.equal(branchCalls[1]!.height, 24);
    assert.equal(branchCalls[1]!.detection.landmarks[0]!.x, .5); assert.equal(branchCalls[1]!.omit, false);
    assert.deepEqual(branchCalls[1]!.surface, Array.from(surface));
    assert.deepEqual(nativeReads.at(-1), [0, 0, 32, 24]);
    assert.deepEqual(diagnostic.diagnosticBranchReadback, {rerendered: true, calls: 1, bytes: 32 * 24 * 4, source: 'same-pair-full-rerender'});
    assert.equal(JSON.parse(diagnostic.branchPngDataUrl as string).first, 27);
    assert.deepEqual(renderer.ownedPixels!.data, before); assert.deepEqual(renderer.captureSnapshot, geometry);
    assert.deepEqual(renderer.stageTimings, timings);
    const cached = renderer.exportDiagnostic()!;
    assert.equal(branchCalls.length, 2); assert.equal((cached.diagnosticBranchReadback as {calls: number}).calls, 0);
    renderer.setFrameOptions(DEFAULT_SPEED_OPTIONS); renderer.present(canvas(), {...detection, landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0}))});
    assert.equal(renderer.stageTimings.efficiencyLab.branchRegion.requested, false);
    assert.equal(renderer.stageTimings.branchReadbackBytes, 32 * 24 * 4);
  } finally {
    renderer?.dispose(); Object.defineProperty(globalThis, 'document', {configurable: true, value: priorDocument});
    Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: priorImage});
  }
});
