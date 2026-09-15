import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {bytesOfImageData, bytesOfVideoFrame, chooseCaptureSource, OwnedVideoFrame, sha256Hex} from '../src/pipeline/capture.ts';
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
