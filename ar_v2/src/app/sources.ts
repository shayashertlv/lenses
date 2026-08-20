/**
 * Where frames come from.
 *
 * Two implementations behind one shape, and the still-image one is not a
 * curiosity — it is how the whole app is exercised on a machine with no webcam,
 * which is the machine this was written on. v1 shipped with the same split and
 * the same honest note in its open list: *"never run against a live camera on
 * the build machine"*. That is still true here and it is `docs/OPEN-QUESTIONS.md`
 * Q8.
 *
 * Both answer `nextFrame()`, which is what keeps detection off the display's
 * clock. A webcam delivers 30 frames a second; a display asks for 60. Detecting
 * once per *render* runs the landmarker twice on identical pixels for nothing
 * but heat, and tells the velocity estimate the head moved that far in half the
 * time it really took.
 */

export interface FrameOffer {
  source: CanvasImageSource;
  capturedAtMs: number;
  timestampMs: number;
  measuredCapture: boolean;
}

export interface Source {
  readonly kind: 'camera' | 'still';
  readonly width: number;
  readonly height: number;
  readonly label: string;
  /** New pixels, or null. */
  nextFrame(nowMs: number): FrameOffer | null;
  /** Frames the browser presented that were never looked at. */
  readonly droppedFrames: number;
  stop(): void;
}

export async function createCameraSource(options: {
  width?: number; height?: number; frameRate?: number; onLost?: () => void;
} = {}): Promise<Source> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('This browser exposes no camera API. Serve over https:// or localhost.');
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {
      facingMode: 'user',
      width: { ideal: options.width ?? 1280 },
      height: { ideal: options.height ?? 720 },
      // Asked for, not required. Frame interval is latency the pipeline can
      // never recover downstream, because a pose can only be as fresh as the
      // frame it was solved from.
      frameRate: { ideal: options.frameRate ?? 60 },
    },
  });

  // Past this line the stream is live hardware. Anything that throws before the
  // source is handed back has to give it up, or the camera light stays on for
  // the life of the page.
  try {
    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.muted = true;
    video.srcObject = stream;
    await new Promise<void>((resolve, reject) => {
      video.onloadedmetadata = () => resolve();
      video.onerror = () => reject(new Error('camera stream failed to load'));
    });
    await video.play();

    const track = stream.getVideoTracks()[0];
    let lost = false;
    const markLost = () => { if (!lost) { lost = true; options.onLost?.(); } };
    // A camera that stops mid-session has to be watched for explicitly. Unplug
    // the webcam and the video element simply keeps its last decoded frame:
    // `readyState` stays put and `videoWidth` stays nonzero, so the landmarker
    // goes on detecting a frozen face and every readout says "tracking".
    track?.addEventListener('ended', markLost);

    const hasRVFC = typeof (video as any).requestVideoFrameCallback === 'function';
    let pending: any = null;
    let lastMediaTime = -1;
    let dropped = 0;
    let lastPresented: number | null = null;

    if (hasRVFC) {
      const onFrame = (_now: number, metadata: any) => {
        if (lastPresented !== null && metadata.presentedFrames > lastPresented + 1) {
          dropped += metadata.presentedFrames - lastPresented - 1;
        }
        lastPresented = metadata.presentedFrames;
        pending = metadata;
        (video as any).requestVideoFrameCallback(onFrame);
      };
      (video as any).requestVideoFrameCallback(onFrame);
    }

    return {
      kind: 'camera',
      get width() { return video.videoWidth; },
      get height() { return video.videoHeight; },
      label: track?.label || 'camera',
      get droppedFrames() { return dropped; },

      nextFrame(nowMs) {
        if (hasRVFC) {
          if (!pending) return null;
          const metadata = pending;
          pending = null;
          // `captureTime` is the only place the browser exposes the sensor's own
          // timestamp. The difference between it and now IS the pipeline's
          // latency, measured instead of assumed.
          const capturedAtMs = metadata.captureTime ?? metadata.presentationTime ?? nowMs;
          return {
            source: video,
            capturedAtMs,
            timestampMs: metadata.mediaTime * 1000,
            measuredCapture: metadata.captureTime !== undefined,
          };
        }
        // Firefox and older Safari have no rVFC: watch `currentTime` for a
        // change, which gives the cadence but not the timestamp.
        if (video.currentTime === lastMediaTime) return null;
        lastMediaTime = video.currentTime;
        return {
          source: video,
          capturedAtMs: nowMs,
          timestampMs: video.currentTime * 1000,
          measuredCapture: false,
        };
      },

      stop() {
        for (const t of stream.getTracks()) t.stop();
      },
    };
  } catch (error) {
    for (const t of stream.getTracks()) t.stop();
    throw error;
  }
}

/**
 * A still image, replayed at a chosen rate.
 *
 * Deliberately NOT a frozen frame: the timestamp advances and a small amount of
 * sub-pixel motion is available, so the detector's VIDEO mode behaves as it does
 * on a camera rather than short-circuiting on a repeated timestamp.
 */
export async function createStillSource(
  url: string, options: { fps?: number; timeoutMs?: number } = {},
): Promise<Source> {
  const image = new Image();
  image.crossOrigin = 'anonymous';

  // `await image.decode()` on its own is the third instance in this tree of the
  // same bug: an await that can never settle. In a hidden or throttled tab the
  // decode is deferred indefinitely — it neither resolves nor rejects — and boot
  // hangs with no error, no log, and a status line that says everything is fine.
  //
  // Race it against `onload`/`onerror` (which fire from the network layer, not
  // the decoder) and against a wall-clock timeout. Whichever settles first wins,
  // and the timeout throws so the caller's own fallback runs.
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const done = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve();
    };
    const timer = setTimeout(
      () => done(new Error(`still image timed out loading: ${url}`)),
      options.timeoutMs ?? 8000,
    );
    image.onload = () => done();
    image.onerror = () => done(new Error(`still image failed to load: ${url}`));
    image.src = url;
    // Kick the decoder too, but never wait on it alone.
    image.decode().then(() => done()).catch(() => { /* onload/onerror decide */ });
  });

  if (!(image.naturalWidth > 0)) throw new Error(`still image has no pixels: ${url}`);

  const fps = options.fps ?? 30;
  const interval = 1000 / fps;
  let last = -Infinity;
  let clock = 0;

  return {
    kind: 'still',
    width: image.naturalWidth,
    height: image.naturalHeight,
    label: url.split('/').pop() ?? 'still',
    droppedFrames: 0,
    nextFrame(nowMs) {
      if (nowMs - last < interval) return null;
      last = nowMs;
      clock += interval;
      return {
        source: image, capturedAtMs: nowMs, timestampMs: clock, measuredCapture: false,
      };
    },
    stop() { /* nothing to release */ },
  };
}
