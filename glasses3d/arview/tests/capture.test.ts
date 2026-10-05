import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {bytesOfImageData, bytesOfVideoFrame, chooseCaptureSource, matchOrientation, meanChannelDifference, ORIENTATION_MATCH_LIMIT, OwnedVideoFrame, rotateSquare, sha256Hex} from '../src/pipeline/capture.ts';
import type {VideoFrameLike} from '../src/pipeline/capture.ts';

class FrameStub implements VideoFrameLike {
  readonly format = 'NV12'; copies = 0; closes = 0; failCopy = false;
  private readonly payload: Uint8Array;
  constructor(payload: Uint8Array) {this.payload = payload;}
  allocationSize(): number {return this.payload.length;}
  async copyTo(destination: Uint8Array): Promise<unknown> {
    this.copies++;
    if (this.closes) throw new DOMException('The frame is closed.', 'InvalidStateError');
    if (this.failCopy) throw new Error('copy failed');
    destination.set(this.payload); return [{offset: 0, stride: 4}];
  }
  close(): void {this.closes++;}
}

test('the capture source is the requested one, or the canvas with a reason when VideoFrame is missing', () => {
  assert.deepEqual(chooseCaptureSource('canvas', true), {source: 'canvas', reason: null});
  assert.deepEqual(chooseCaptureSource('canvas', false), {source: 'canvas', reason: null});
  assert.deepEqual(chooseCaptureSource('videoframe', true), {source: 'videoframe', reason: null});
  const fallback = chooseCaptureSource('videoframe', false);
  assert.equal(fallback.source, 'canvas'); assert.match(fallback.reason ?? '', /VideoFrame is unavailable/);
});

test('a VideoFrame is copied out once, in its own format, with the copy time measured; the frame closes once', async () => {
  const payload = Uint8Array.from({length: 1280 * 720 * 3 / 2}, (_, index) => (index * 7) & 255);
  const frame = new FrameStub(payload);
  let clock = 100; const bytes = bytesOfVideoFrame(frame, () => (clock += 2.5));
  assert.equal(bytes.format, 'NV12'); assert.equal(bytes.readMs(), 0, 'not known before the copy settles');
  const copied = await bytes.bytes;
  assert.equal(frame.copies, 1); assert.deepEqual(copied, payload); assert.notEqual(copied, payload, 'a copy, not the source');
  assert.equal(bytes.readMs(), 2.5);
  const owned = new OwnedVideoFrame(frame);
  assert.equal(owned.closed, false); owned.close(); owned.close();
  assert.equal(frame.closes, 1, 'closed exactly once'); assert.equal(owned.closed, true);
});

test('a frame closed with its copy in flight leaves no unhandled rejection; the consumer still sees the failure', async () => {
  const frame = new FrameStub(new Uint8Array(16)); frame.failCopy = true;
  const bytes = bytesOfVideoFrame(frame, () => 0);
  await new Promise(resolve => setTimeout(resolve, 5));
  await assert.rejects(bytes.bytes, /copy failed/);
});

test('the identity hash of the bytes is the SHA-256 of exactly those bytes, for canvas RGBA and for frame bytes alike', async () => {
  const rgba = new Uint8ClampedArray([1, 2, 3, 255, 9, 8, 7, 255]);
  const image = {data: rgba, width: 2, height: 1, colorSpace: 'srgb'} as unknown as ImageData;
  const fromCanvas = bytesOfImageData(image, 1.25);
  assert.equal(fromCanvas.format, 'RGBA'); assert.equal(fromCanvas.readMs(), 1.25);
  assert.equal(await sha256Hex(await fromCanvas.bytes), createHash('sha256').update(rgba).digest('hex'));
  const payload = Uint8Array.from([5, 6, 7, 8, 9]);
  const fromFrame = bytesOfVideoFrame(new FrameStub(payload), () => 0);
  assert.equal(await sha256Hex(await fromFrame.bytes), createHash('sha256').update(payload).digest('hex'));
  // A view into a larger buffer hashes only its own bytes.
  const backing = new Uint8Array(12); backing.set(payload, 4);
  assert.equal(await sha256Hex(backing.subarray(4, 9)), createHash('sha256').update(payload).digest('hex'));
});

const square = (edge: number, pixel: (x: number, y: number) => [number, number, number]): Uint8ClampedArray => {
  const out = new Uint8ClampedArray(edge * edge * 4);
  for (let y = 0; y < edge; y++) for (let x = 0; x < edge; x++) {
    const [r, g, b] = pixel(x, y), at = (y * edge + x) * 4;
    out[at] = r; out[at + 1] = g; out[at + 2] = b; out[at + 3] = 255;
  }
  return out;
};

test('a square image rotates clockwise through the four quarter turns and back to itself', () => {
  const edge = 4;
  // A corner marker so every quarter turn is distinguishable.
  const image = square(edge, (x, y) => [x === 0 && y === 0 ? 255 : 0, x * 20, y * 20]);
  const at = (pixels: Uint8ClampedArray, x: number, y: number): number => pixels[(y * edge + x) * 4]!;
  assert.equal(at(image, 0, 0), 255);
  assert.equal(at(rotateSquare(image, edge, 90), edge - 1, 0), 255, 'the top-left corner moves to the top-right');
  assert.equal(at(rotateSquare(image, edge, 180), edge - 1, edge - 1), 255);
  assert.equal(at(rotateSquare(image, edge, 270), 0, edge - 1), 255);
  assert.deepEqual(rotateSquare(image, edge, 0), image);
  assert.deepEqual(rotateSquare(rotateSquare(image, edge, 90), edge, 270), image, 'a quarter turn each way is the identity');
  assert.deepEqual(rotateSquare(rotateSquare(image, edge, 180), edge, 180), image);
  assert.throws(() => rotateSquare(image, edge + 1, 90), /square RGBA/);
});

test('the orientation of the camera frame is measured against the video image, and stays silent when it cannot be told', () => {
  const edge = 8;
  const scene = square(edge, (x, y) => [x * 30, y * 30, (x + y) * 15]);
  // The colour conversions differ slightly; the match must survive that.
  const converted = square(edge, (x, y) => [x * 30 + 5, y * 30 - 4, (x + y) * 15 + 3]);
  const upright = matchOrientation(converted, scene, edge);
  assert.equal(upright.degrees, 0); assert.ok(upright.conclusive); assert.ok(upright.difference < ORIENTATION_MATCH_LIMIT);
  for (const degrees of [90, 180, 270] as const) {
    const rotated = matchOrientation(rotateSquare(converted, edge, degrees), scene, edge);
    assert.equal(rotated.degrees, degrees, `a frame rotated ${degrees}° is recognised`);
    assert.ok(rotated.conclusive);
  }
  // A uniform picture matches every rotation equally: nothing may be concluded from it.
  const flat = square(edge, () => [90, 90, 90]);
  const ambiguous = matchOrientation(flat, flat, edge);
  assert.equal(ambiguous.conclusive, false); assert.equal(ambiguous.difference, 0); assert.equal(ambiguous.runnerUp, 0);
  // Two unrelated pictures are never a match at any rotation.
  const other = square(edge, (x, y) => [255 - x * 30, 255 - y * 30, 128]);
  assert.equal(matchOrientation(other, scene, edge).conclusive, false);
  assert.throws(() => meanChannelDifference(scene, new Uint8ClampedArray(4)), /differ in size/);
});
