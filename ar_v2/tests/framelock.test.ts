/**
 * The frame lock, which had no test at all.
 *
 * `docs/ARCHITECTURE.md` calls it "the single best idea in that tree" and
 * `docs/PARITY.md` records that `grep -rn framelock tests/` returned nothing:
 * the one mechanism that decides whether the glasses and the face a wearer sees
 * are the same instant was carried across from v1 on trust.
 *
 * It looks like browser code and it is not. Three canvases and one rule, and
 * the rule is arithmetic: which image each canvas is drawn FROM, and whether a
 * result is still describing the source it was solved against. A canvas stub
 * that records `drawImage`'s first argument is enough to pin both, and both are
 * exactly the places v1 got confused — its own docstring for the detect canvas
 * named the wrong source canvas, which is the kind of error that survives
 * review and produces glasses sliding across a fresher face.
 *
 * Kept in its own file rather than added to `app.test.ts` because it has to
 * install a fake `document` on `globalThis`, and `node --test` gives each file
 * its own process, so the fake cannot leak into the enrollment-client tests.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

// ---------------------------------------------------------------- the stub

interface Drawn { src: unknown; args: number[] }

class FakeCtx {
  readonly drawn: Drawn[] = [];
  /** How many times the canvas was actually READ. The point of the sampling
   *  test: asserting the returned number alone cannot see the read happening. */
  reads = 0;
  constructor(readonly canvas: FakeCanvas) {}
  drawImage(src: unknown, ...args: number[]): void { this.drawn.push({ src, args }); }
  getImageData(_x: number, _y: number, w: number, h: number): { data: Uint8ClampedArray } {
    this.reads++;
    // A flat mid-grey, so `measureBrightness` has something determinate to
    // average. 128 in every channel means the mean luminance is 128 exactly.
    return { data: new Uint8ClampedArray(Math.max(1, w * h * 4)).fill(128) };
  }
}

class FakeCanvas {
  width = 0;
  height = 0;
  readonly ctx = new FakeCtx(this);
  getContext(_kind: string): FakeCtx { return this.ctx; }
}

const created: FakeCanvas[] = [];
(globalThis as unknown as { document: unknown }).document = {
  createElement(kind: string) {
    assert.equal(kind, 'canvas', `the frame lock created a <${kind}>, not a canvas`);
    const c = new FakeCanvas();
    created.push(c);
    return c;
  },
};

// Imported AFTER the stub is installed. `framelock.ts` touches `document` only
// inside `createFrameLock`, so a static import would also be safe — but the
// order is load-bearing if that ever changes, and a dynamic import states it.
const { createFrameLock, detectToSourceScale, scaleLandmarksToSource } =
  await import('../src/app/framelock.js');

/** A stand-in for the video element. Identity is all the tests read. */
const SOURCE = { tag: 'the live video element' } as unknown as CanvasImageSource;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ctxOf = (c: unknown): FakeCtx => (c as any).ctx;

describe('the frame lock pairs the pixels with their own pose', () => {
  it('sizes the detect canvas by the LONG side, and never upscales', () => {
    // The detector works on a downscaled copy; `DETECT_LONG_SIDE` is 640 and
    // v1's reasoning for it is carried in `detect/mediapipe.ts`. Landscape and
    // portrait have to pick different axes, and a source already smaller than
    // the target must be left alone — upscaling would cost the detector time
    // for pixels that carry no more information.
    const landscape = createFrameLock({ detectLongSide: 640 });
    landscape.resize(1280, 720);
    assert.deepEqual(
      [landscape.detect.width, landscape.detect.height], [640, 360],
      'a 1280x720 source did not downscale to 640 on its long side',
    );
    assert.deepEqual([landscape.capture.width, landscape.capture.height], [1280, 720]);
    assert.deepEqual([landscape.display.width, landscape.display.height], [1280, 720]);

    const portrait = createFrameLock({ detectLongSide: 640 });
    portrait.resize(720, 1280);
    assert.deepEqual(
      [portrait.detect.width, portrait.detect.height], [360, 640],
      'a portrait source scaled off its width — the long side is the HEIGHT here',
    );

    const small = createFrameLock({ detectLongSide: 640 });
    small.resize(320, 240);
    assert.deepEqual(
      [small.detect.width, small.detect.height], [320, 240],
      'a source smaller than the detect target was upscaled',
    );
  });

  it('draws the detect canvas from the SNAPSHOT, never from the live source', () => {
    // **The claim the whole mechanism rests on.** If the detect canvas is drawn
    // from the video element rather than from the capture canvas, the video can
    // present a new frame between the two draws — and the pose then describes
    // an image the composite never shows, which is precisely the artefact the
    // lock exists to remove. v1's own docstring named the wrong canvas here
    // while its code was right; nothing in either tree has ever checked it.
    const lock = createFrameLock({ detectLongSide: 640 });
    lock.resize(1280, 720);
    lock.submit(SOURCE, 1000, 1000, true);

    const captureDraws = ctxOf(lock.capture).drawn;
    const detectDraws = ctxOf(lock.detect).drawn;

    assert.equal(captureDraws.length, 1, 'the capture canvas was not drawn exactly once');
    assert.equal(captureDraws[0].src, SOURCE, 'the capture canvas did not come from the source');

    assert.equal(detectDraws.length, 1, 'the detect canvas was not drawn exactly once');
    assert.equal(
      detectDraws[0].src, lock.capture,
      'the detect canvas was drawn from the live source rather than from the capture '
      + 'snapshot — the pose can now describe pixels the composite never shows',
    );
    assert.notEqual(detectDraws[0].src, SOURCE);
  });

  it('refuses a result solved against a source that has been switched away', () => {
    // The epoch guard. Without it, a detection still in flight when the wearer
    // switches camera lands on whatever replaced it: a pose from one image
    // planted on another, which reads as the glasses jumping.
    const lock = createFrameLock({ detectLongSide: 640 });
    lock.resize(640, 480);

    const stale = lock.submit(SOURCE, 1000, 1000, true);
    assert.equal(lock.present(stale), true, 'a same-epoch result was refused');
    const shownOnce = ctxOf(lock.display).drawn.length;
    assert.equal(shownOnce, 1, 'presenting did not draw the display canvas');
    const delayAfterGood = lock.mirrorDelayMs;

    lock.nextEpoch();
    assert.equal(
      lock.present(stale), false,
      'a result from the previous epoch was presented — it would be planted on '
      + 'the source that replaced the one it was solved against',
    );
    assert.equal(
      ctxOf(lock.display).drawn.length, shownOnce,
      'the display canvas was drawn for a stale result',
    );
    assert.equal(
      lock.mirrorDelayMs, delayAfterGood,
      'a stale result still moved the reported mirror delay',
    );

    // And the new epoch works, so the guard rejects staleness rather than
    // everything after a switch.
    const fresh = lock.submit(SOURCE, 2000, 2000, true);
    assert.equal(lock.present(fresh), true, 'no result can be presented after a source switch');
  });

  it('measures the gap between SUBMITTED frames, not the camera interval', () => {
    // `captureDt` feeds the motion prior. Frames offered while the tracker is
    // busy are dropped whole and never submitted, so the interval that matters
    // is between the frames that were actually looked at — a camera running at
    // 60 fps into a detector managing 20 must not report 1/60.
    const lock = createFrameLock({ detectLongSide: 640 });
    lock.resize(640, 480);

    const first = lock.submit(SOURCE, 5000, 5000, true);
    assert.ok(
      Math.abs(first.captureDt - 1 / 30) < 1e-9,
      `the first submission assumed ${first.captureDt}s rather than a nominal 1/30`,
    );

    const second = lock.submit(SOURCE, 5100, 5100, true);
    assert.ok(
      Math.abs(second.captureDt - 0.1) < 1e-9,
      `100 ms between submissions reported ${second.captureDt}s`,
    );

    // A source switch has no previous frame to measure from.
    lock.nextEpoch();
    const afterSwitch = lock.submit(SOURCE, 9000, 9000, true);
    assert.ok(
      Math.abs(afterSwitch.captureDt - 1 / 30) < 1e-9,
      'the first frame of a new source measured its gap against the OLD source, '
      + `reporting ${afterSwitch.captureDt}s across the switch`,
    );
  });

  it('converts detect-canvas landmarks back to source pixels', () => {
    // The detector returns landmarks in the downscaled canvas's coordinates and
    // everything else in the source's. One factor, applied at one boundary.
    const lock = createFrameLock({ detectLongSide: 640 });
    lock.resize(1280, 720);
    assert.equal(detectToSourceScale(lock), 2);

    const scaled = scaleLandmarksToSource(Float64Array.of(10, 20, 30, 40), 2);
    assert.deepEqual(Array.from(scaled), [20, 40, 60, 80]);

    // At scale 1 the input is returned as-is rather than copied, which is the
    // hot path when the source is already small enough.
    const same = Float64Array.of(1, 2);
    assert.equal(scaleLandmarksToSource(same, 1), same);
  });

  it('samples brightness rather than reading every pixel every frame', () => {
    // A `getImageData` on the full-resolution canvas every frame is a stall
    // nobody would attribute to a readout, so it reads the DETECT canvas and
    // only every Nth frame.
    //
    // **Counting the READS, not the returned value.** The first version of this
    // test asserted only that the answer was 128, which is true whether the
    // sampling gate is there or not — so it could not fail on the property its
    // own name claims. The canvas stub counts `getImageData` instead.
    const lock = createFrameLock({ detectLongSide: 640 });
    lock.resize(640, 480);
    lock.submit(SOURCE, 1000, 1000, true);
    const detectCtx = ctxOf(lock.detect);
    assert.equal(detectCtx.reads, 0, 'submitting a frame read the pixels back');

    assert.equal(lock.measureBrightness(3), 128, 'a flat mid-grey frame did not measure 128');
    assert.equal(detectCtx.reads, 1, 'the first brightness sample did not read the canvas');

    // The next `every` calls must cost nothing at all.
    for (let i = 0; i < 3; i++) lock.measureBrightness(3);
    assert.equal(
      detectCtx.reads, 1,
      `brightness read the canvas ${detectCtx.reads} times in four calls at every=3 — `
      + 'the sampling gate is gone and a getImageData is back on the per-frame path',
    );

    // ...and it starts reading again once the countdown expires, or the readout
    // would freeze on the first frame the wearer ever showed it.
    lock.measureBrightness(3);
    assert.equal(detectCtx.reads, 2, 'brightness never sampled again after the first read');
  });

  it('reads brightness off the DETECT canvas, not the full-resolution one', () => {
    // The reason the sampling above is affordable at all. A getImageData on
    // 1280x720 is two orders of magnitude more pixels than on 640x360, and it
    // is the image the detector actually sees that matters anyway.
    const lock = createFrameLock({ detectLongSide: 640 });
    lock.resize(1280, 720);
    lock.submit(SOURCE, 1000, 1000, true);
    lock.measureBrightness(1);
    assert.equal(ctxOf(lock.detect).reads, 1, 'the detect canvas was not the one read');
    assert.equal(
      ctxOf(lock.capture).reads, 0,
      'brightness read the full-resolution capture canvas — four times the pixels, '
      + 'on the per-frame path',
    );
    assert.equal(ctxOf(lock.display).reads, 0);
  });
});
