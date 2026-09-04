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

  it('notices the source changing shape under it, and refuses the frame in flight', () => {
    // **`resize` used to be called exactly once, from `startSource`, whose only
    // caller is `boot`.** But a camera `Source`'s width and height are live
    // getters over `video.videoWidth`, and a track's frame size can change
    // after boot: rotating an Android phone swaps 1280x720 for 720x1280, and
    // `getUserMedia`'s `ideal` constraints permit a later renegotiation.
    // `submit` then runs `drawImage(source, 0, 0, capture.width,
    // capture.height)`, and with four arguments that maps the WHOLE source rect
    // onto the WHOLE destination rect — every pixel moved from (u,v) to
    // (u*W/W', v*H/H'), an anisotropic scale about the ORIGIN.
    //
    // Measured through the real `solvePnP` against intrinsics that still
    // describe the boot mode (scratchpad/f29-squash.mjs, boot 1280x720):
    //
    //     new mode     aspect   rms px   translation err   rotation err
    //     1920x1080     1.000    0.96        0.3 mm           0.04 deg
    //     640x480       0.750    7.59       50.9 mm           0.36 deg
    //     1280x960      0.750    7.59       50.9 mm           0.36 deg
    //     720x1280      0.316   38.50      296.5 mm          61.01 deg
    //
    // An aspect-PRESERVING change is free: the origin-anchored rescale exactly
    // undoes the new mode's intrinsics, so 1920x1080 costs 0.3 mm. The rotation
    // is loud — 38.5 px is past both `SCAN_MAX_RMS_PX` (22) and the tracker's
    // `maxRmsPx` (14), so the app degrades to a permanent "hold steady" with no
    // hint why. **The 4:3 renegotiation is the silent one: 50.9 mm of depth
    // error at 7.59 px of residual, under every gate in the tree.** That is the
    // shape this test guards: a large depth error behind a healthy residual,
    // with every gate reading green.
    //
    // RED: delete `syncTo`, or make it resize without bumping the epoch.
    const lock = createFrameLock({ detectLongSide: 640 });
    lock.resize(1280, 720);
    assert.equal(detectToSourceScale(lock), 2);

    // A source that has not moved changes nothing at all.
    const epochBefore = lock.epoch;
    assert.equal(lock.syncTo(1280, 720), false, 'an unchanged source triggered a resize');
    assert.equal(lock.epoch, epochBefore, 'an unchanged source bumped the epoch');

    // A frame is in flight when the phone rotates.
    const inFlight = lock.submit(SOURCE, 1000, 1000, true);
    assert.equal(lock.syncTo(720, 1280), true, 'a rotated source was not noticed');
    assert.equal(lock.capture.width, 720, 'the capture canvas kept the old width');
    assert.equal(lock.capture.height, 1280, 'the capture canvas kept the old height');
    assert.equal(lock.display.width, 720, 'the display canvas kept the old width');
    // The detect canvas re-derives from the LONG side, which is now the height.
    assert.equal(lock.detect.height, 640, 'the detect canvas was not re-derived');
    assert.equal(lock.detect.width, 360);
    assert.equal(detectToSourceScale(lock), 2);

    assert.equal(
      lock.present(inFlight), false,
      'a result solved against the pre-rotation frame was presented onto the new one — '
      + 'the epoch is what makes a shape change indistinguishable from a source switch, '
      + 'and it must be, because it is the same staleness',
    );

    // A video between modes reports 0 for a frame or two. Resizing to it would
    // take every canvas to zero and there is no coming back from that.
    assert.equal(lock.syncTo(0, 0), false, 'a source reporting 0 was adopted');
    assert.equal(lock.syncTo(720, 0), false, 'a source with no height was adopted');
    assert.equal(lock.capture.width, 720, 'a zero-sized report resized the capture canvas');
    assert.equal(lock.capture.height, 1280, 'a zero-sized report resized the capture canvas');
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

  it('drops image-derived readouts when a resize replaces the canvases', () => {
    const lock = createFrameLock({ detectLongSide: 640 });
    lock.resize(1280, 720);
    lock.submit(SOURCE, 1000, 1000, true);
    lock.measureBrightness(100);
    const detectCtx = ctxOf(lock.detect);
    const readsBeforeResize = detectCtx.reads;
    assert.equal(lock.brightness, 128, 'precondition: no brightness reading to invalidate');

    lock.syncTo(720, 1280);
    assert.ok(Number.isNaN(lock.brightness), 'the resized source kept the old brightness');
    assert.equal(lock.mirrorDelayMs, 0, 'the resized source kept the old mirror delay');

    lock.submit(SOURCE, 2000, 2000, true);
    lock.measureBrightness(100);
    assert.equal(detectCtx.reads, readsBeforeResize + 1,
      'the old brightness sampling countdown delayed the first reading after a resize');
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
