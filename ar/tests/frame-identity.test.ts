import {test} from 'node:test';
import assert from 'node:assert/strict';
import {markFrame} from '../src/pipeline/frame-identity.ts';
import type {FrameMark} from '../src/pipeline/frame-identity.ts';

test('with requestVideoFrameCallback the presented-frame counter decides, whatever currentTime does', () => {
  // WebKit: currentTime is a running clock, different at every read, the same frame or not.
  let clock = 1.0000;
  const read = (): number => (clock += 0.0137);
  let last: FrameMark | null = null;
  const first = markFrame({presentedFrames: 7, mediaTime: 0.5}, read(), last); last = first.mark;
  assert.equal(first.fresh, true); assert.deepEqual(first.mark, {presented: 7, mediaTime: 0.5});
  const same = markFrame({presentedFrames: 7, mediaTime: 0.5}, read(), last);
  assert.equal(same.fresh, false, 'the same presented frame is not captured twice');
  const next = markFrame({presentedFrames: 8, mediaTime: 0.533}, read(), last);
  assert.equal(next.fresh, true); assert.equal(next.mark.mediaTime, 0.533);
});

test('without frame metadata a changed currentTime marks a new frame and an unchanged one is skipped', () => {
  const first = markFrame(undefined, 0.2, null);
  assert.equal(first.fresh, true); assert.deepEqual(first.mark, {presented: null, mediaTime: 0.2});
  assert.equal(markFrame(undefined, 0.2, first.mark).fresh, false);
  assert.equal(markFrame(undefined, 0.233, first.mark).fresh, true);
});
