/**
 * The frame lock: one clock for the pixels and the pose.
 *
 * **Ported from v1 essentially unchanged, and it is the single best idea in that
 * tree.** The reasoning, in v1's own words, because it cannot be improved on:
 *
 *   *This is video AR, not see-through AR. In see-through AR the wearer sees the
 *   real world at zero latency, so the pose must be predicted forward to display
 *   time or the virtual object trails the real one. Here the user is looking at
 *   a video of their own face — already tens of milliseconds old before it
 *   reaches the screen — so the only alignment that matters is between the
 *   glasses and that video. The fix is not prediction; it is a lock: detect on
 *   exactly the pixels that will be shown, and show them only together with
 *   their own pose.*
 *
 * The mechanism is three canvases and one rule:
 *
 *   `capture`  the frame currently under detection, copied at submission.
 *   `detect`   the same pixels, downscaled to what the detector actually looks
 *              at. Drawn FROM `capture`, never from the live element — taking it
 *              from the element opens a window in which the video can present a
 *              new frame between the two draws, and the pose would then describe
 *              an image the composite never shows.
 *   `display`  the scene's background: the frame last shown. Updated only when
 *              its own detection returns.
 *
 * A frame offered while the tracker is busy is **dropped whole** — never shown,
 * never tracked. The display therefore steps at the detection rate rather than
 * the display's, and the whole pipeline's latency appears only as the mirror
 * running a few hundredths of a second behind the room, which is invisible,
 * instead of as glasses sliding across a fresher face, which is not.
 *
 * One correction to v1 while porting: its own docstring for the detect canvas
 * said it was drawn from the *display* canvas. The code drew from the capture
 * canvas, which is correct — the display canvas at that instant still holds the
 * previous frame — but the comment had been left behind by the change that
 * introduced the split. Stated correctly here.
 */

export interface CapturedFrame {
  /** When the sensor took this frame, ms on the performance clock. */
  capturedAtMs: number;
  /** Monotonic timestamp for the detector's VIDEO mode. */
  timestampMs: number;
  /** True when the source could report a real capture time. */
  measuredCapture: boolean;
  /** Seconds since the previously SUBMITTED frame. Not the camera interval. */
  captureDt: number;
  /** Increments when the source changes, so a result in flight is recognisable
   *  as stale rather than being planted on the new source. */
  epoch: number;
}

export interface FrameLockOptions {
  /** Long side of the detect canvas, px. */
  detectLongSide: number;
}

export interface FrameLock {
  readonly capture: HTMLCanvasElement;
  readonly detect: HTMLCanvasElement;
  readonly display: HTMLCanvasElement;
  /** How far the mirror runs behind the room, ms, capture to composite. */
  mirrorDelayMs: number;
  /** Frames the source presented that were never looked at. */
  droppedFrames: number;
  epoch: number;
  resize(width: number, height: number): void;
  /** Copies the source into the capture and detect canvases. Returns the frame
   *  descriptor to hand to the tracker. */
  submit(source: CanvasImageSource, capturedAtMs: number, timestampMs: number,
    measuredCapture: boolean): CapturedFrame;
  /** Pairs the result's pixels with its own pose. Call when a detection lands. */
  present(frame: CapturedFrame): boolean;
  nextEpoch(): number;
}

export function createFrameLock(options: Partial<FrameLockOptions> = {}): FrameLock {
  const detectLongSide = options.detectLongSide ?? 640;

  const capture = document.createElement('canvas');
  const detect = document.createElement('canvas');
  const display = document.createElement('canvas');
  let captureCtx: CanvasRenderingContext2D | null = null;
  let detectCtx: CanvasRenderingContext2D | null = null;
  let displayCtx: CanvasRenderingContext2D | null = null;
  let lastSubmitMs = 0;

  const lock: FrameLock = {
    capture, detect, display,
    mirrorDelayMs: 0,
    droppedFrames: 0,
    epoch: 0,

    resize(width, height) {
      capture.width = width; capture.height = height;
      display.width = width; display.height = height;
      const shrink = Math.min(1, detectLongSide / Math.max(width, height));
      detect.width = Math.max(1, Math.round(width * shrink));
      detect.height = Math.max(1, Math.round(height * shrink));
      captureCtx = capture.getContext('2d');
      detectCtx = detect.getContext('2d');
      displayCtx = display.getContext('2d');
    },

    submit(source, capturedAtMs, timestampMs, measuredCapture) {
      if (!captureCtx || !detectCtx) throw new Error('frame lock not sized');
      captureCtx.drawImage(source, 0, 0, capture.width, capture.height);
      // From the SNAPSHOT, not the source. See the file header.
      detectCtx.drawImage(capture, 0, 0, detect.width, detect.height);
      const captureDt = lastSubmitMs ? (capturedAtMs - lastSubmitMs) / 1000 : 1 / 30;
      lastSubmitMs = capturedAtMs;
      return {
        capturedAtMs, timestampMs, measuredCapture, captureDt, epoch: lock.epoch,
      };
    },

    present(frame) {
      // A result solved against a source that has since been switched away from
      // would be planted on whatever replaced it.
      if (frame.epoch !== lock.epoch) return false;
      if (!displayCtx) return false;
      displayCtx.drawImage(capture, 0, 0);
      lock.mirrorDelayMs = performance.now() - frame.capturedAtMs;
      return true;
    },

    nextEpoch() {
      lastSubmitMs = 0;
      return ++lock.epoch;
    },
  };

  return lock;
}

/**
 * The scale from detect-canvas pixels back to source pixels.
 *
 * The detector works on the downscaled copy, so its landmarks come out in that
 * canvas's coordinates and everything else — the intrinsics, the silhouette,
 * the composite — is in the source's. One function, called at the one boundary,
 * rather than a factor threaded through five files.
 */
export function detectToSourceScale(lock: FrameLock): number {
  return lock.capture.width / lock.detect.width;
}

export function scaleLandmarksToSource(
  landmarks: Float64Array, scale: number,
): Float64Array {
  if (scale === 1) return landmarks;
  const out = new Float64Array(landmarks.length);
  for (let i = 0; i < landmarks.length; i++) out[i] = landmarks[i] * scale;
  return out;
}
