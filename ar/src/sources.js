/**
 * Where frames come from.
 *
 * Two implementations behind one shape. The camera is the product; the sample
 * source is how the pipeline stays testable on a machine with no webcam, in CI, or
 * in a meeting room where nobody wants to be on screen. Both hand the tracker a
 * `TexImageSource` and report their own dimensions, and nothing downstream knows
 * which it is talking to.
 *
 * Both also answer `nextFrame()`, which is what keeps detection off the display's
 * clock. A webcam delivers 30 frames a second; a display asks for 60. Detecting once
 * per *render* therefore ran the landmarker twice on half the frames — the same
 * pixels, inferred twice, for nothing but heat — and, worse, told the pose filter
 * that the head had moved that far in half the time it really took, which halves its
 * velocity estimate and with it everything built on one. `nextFrame` hands back a
 * frame only when there are new pixels in it, with the time they were taken.
 */

/**
 * Live webcam.
 *
 * `onLost` fires if the feed dies after it started — see the note on the track's
 * `ended` event below.
 */
export async function createCameraSource({
  width = 1280, height = 720, frameRate = 60, onLost = null,
} = {}) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('This browser exposes no camera API. Serve over https:// or localhost.');
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {
      facingMode: 'user',
      width: { ideal: width },
      height: { ideal: height },
      // Asked for, not required — a camera that cannot do it simply gives what it
      // has. Worth asking: frame interval is latency the pipeline can never recover
      // downstream, because a pose can only be as fresh as the last frame it was
      // solved from. Halving it from 33 ms to 17 ms is the cheapest 16 ms in the
      // whole chain, and it doubles the rate the velocity estimate is fed at.
      frameRate: { ideal: frameRate },
    },
  });

  // Past this line the stream is live hardware. Anything that throws before the
  // source object is handed back has to give it up, because the caller has no
  // reference to stop it — the camera light would stay on for the life of the page
  // while the app shows a still photograph.
  try {
    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.muted = true;
    video.srcObject = stream;

    await new Promise((resolve, reject) => {
      video.onloadedmetadata = resolve;
      video.onerror = () => reject(new Error('camera stream failed to load'));
    });
    await video.play();

    const track = stream.getVideoTracks()[0];

    // A camera that stops mid-session has to be watched for explicitly, because
    // nothing downstream can notice it. Unplug the webcam, revoke the permission in
    // site settings, or let the OS hand the device to another application, and the
    // video element simply keeps its last decoded frame: `readyState` stays where it
    // was and `videoWidth` stays nonzero. The landmarker then goes on detecting the
    // frozen face and every readout goes on reporting a live, tracked head.
    let lost = false;
    const markLost = () => {
      if (lost) return;
      lost = true;
      onLost?.();
    };
    track?.addEventListener('ended', markLost);
    stream.addEventListener?.('removetrack', markLost);

    // --- the camera's own frame clock ---
    //
    // `requestVideoFrameCallback` fires once per frame the element actually
    // presents, with metadata saying when that frame was taken. Both halves matter:
    // the cadence stops detection running on stale pixels, and `captureTime` is the
    // only place in the browser where the sensor's timestamp is exposed — the
    // difference between it and now *is* the pipeline's latency, measured instead of
    // assumed. Firefox and older Safari have no rVFC, so the fallback watches
    // `currentTime` for a change, which gives the cadence but not the timestamp.
    const canObserveFrames = typeof video.requestVideoFrameCallback === 'function';
    let pending = null;
    let handle = null;
    let lastMediaTime = -1;
    // Frames the browser presented that we never got a look at. rVFC reports a
    // running `presentedFrames`, so a gap of more than one between consecutive
    // callbacks is that many frames the pipeline never saw — the measurement that
    // separates a slow pipeline from a starved one, and the one the perceptual
    // literature says actually matters, because dropouts cost far more than a
    // uniformly lower rate does.
    //
    // rVFC itself is capped at the display's refresh rate, so on a 60 Hz screen with
    // a 30 fps camera this is 0 and stays 0. It counts real loss, not that.
    let droppedFrames = 0;
    let lastPresented = null;

    if (canObserveFrames) {
      const onVideoFrame = (_now, metadata) => {
        if (lastPresented !== null && metadata.presentedFrames > lastPresented + 1) {
          droppedFrames += metadata.presentedFrames - lastPresented - 1;
        }
        lastPresented = metadata.presentedFrames;
        pending = metadata;
        handle = video.requestVideoFrameCallback(onVideoFrame);
      };
      handle = video.requestVideoFrameCallback(onVideoFrame);
    }

    return {
      kind: 'camera',
      element: video,
      get width() { return video.videoWidth; },
      get height() { return video.videoHeight; },
      /** True once there are real pixels to read — and false again if the feed dies. */
      get ready() { return !lost && video.readyState >= 2 && video.videoWidth > 0; },
      /** False once the camera has stopped delivering frames. */
      get live() { return !lost; },
      label: track?.label || 'camera',
      /** Nothing to redraw — `VideoTexture` pulls frames from the element itself. */
      update() { return false; },

      /** Camera frames presented by the browser but never handed to the pipeline. */
      get droppedFrames() { return droppedFrames; },

      /**
       * What the camera actually agreed to, which is frequently not what was asked
       * for. `frameRate: { ideal: 60 }` is a request; a webcam that only does 30 —
       * or that has silently dropped to 15 because the room is dark and it is
       * lengthening exposures — says so only here. That single number sets the
       * ceiling on everything downstream, and reading it beats reasoning about it.
       */
      get settings() { return track?.getSettings?.() ?? null; },

      /**
       * The newest frame not yet handed out, or null if there is none.
       *
       * `timestampMs` is the media timeline, which is what the landmarker's video
       * mode wants — it uses it to decide whether it has seen this frame before.
       * `capturedAtMs` is on the same clock as `performance.now()`, so the caller can
       * subtract it from now and get an age in milliseconds.
       */
      nextFrame(nowMs) {
        if (canObserveFrames) {
          if (!pending) return null;
          const metadata = pending;
          pending = null;
          const capturedAtMs = metadata.captureTime ?? metadata.presentationTime ?? nowMs;
          return {
            timestampMs: metadata.mediaTime * 1000,
            // `captureTime` is the sensor's own stamp and is not universally
            // populated; `presentationTime` always is, and is later by however long
            // the camera and the transport took. Falling back to it under-reports the
            // latency rather than inventing a figure for it.
            capturedAtMs,
            measuredCapture: metadata.captureTime != null,
            /**
             * Capture to the moment the browser told us about the frame — the part
             * of the delay that is already spent before any of our code runs, and
             * therefore the part no amount of pipeline work can recover. Splitting
             * it out is what stops a camera's own 60 ms being mistaken for ours;
             * null when the browser would not say when the frame was captured.
             */
            upstreamMs: metadata.captureTime != null ? nowMs - metadata.captureTime : null,
          };
        }

        // No frame callback: `currentTime` advances once per delivered frame, so a
        // change in it is a new frame. There is no capture stamp to be had, so the
        // age reported is only what has passed since we noticed.
        if (video.currentTime === lastMediaTime) return null;
        lastMediaTime = video.currentTime;
        return {
          timestampMs: video.currentTime * 1000,
          capturedAtMs: nowMs,
          measuredCapture: false,
          upstreamMs: null,
        };
      },

      stop() {
        // Before stopping the tracks: `stop()` is us, not a failure, and must not
        // report itself as a lost camera.
        track?.removeEventListener('ended', markLost);
        stream.removeEventListener?.('removetrack', markLost);
        if (handle !== null) video.cancelVideoFrameCallback?.(handle);
        for (const t of stream.getTracks()) t.stop();
        video.srcObject = null;
      },
    };
  } catch (error) {
    for (const t of stream.getTracks()) t.stop();
    throw error;
  }
}

/**
 * A still portrait, drawn to a canvas.
 *
 * `drift` slowly pans, rocks and zooms the image. It is not a substitute for a
 * real moving head — the face stays flat — but it makes the pose change frame to
 * frame, which is enough to exercise the smoothing and to show up any per-frame
 * jitter that a frozen image would hide.
 */
export async function createSampleSource(url, { drift = false } = {}) {
  const image = new Image();
  image.crossOrigin = 'anonymous';
  image.src = url;
  // `onload`, deliberately not `decode()`: decode() is allowed to defer until the
  // image is about to be rendered, and in a hidden page — the test harness in a
  // background tab — "about to be rendered" never comes, so it simply hangs.
  // drawImage decodes synchronously at draw time, which is all that is needed.
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = () => reject(new Error(`could not load ${url.split('/').pop()}`));
  });

  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const ctx = canvas.getContext('2d', { willReadFrequently: false });

  let drawn = false;
  let lastFrameMs = -Infinity;

  const draw = (timeMs) => {
    const { width, height } = canvas;
    ctx.save();
    ctx.clearRect(0, 0, width, height);

    if (drift) {
      const t = timeMs / 1000;
      const zoom = 1.06 + Math.sin(t * 0.42) * 0.035;
      ctx.translate(width / 2 + Math.sin(t * 0.31) * width * 0.02,
                    height / 2 + Math.cos(t * 0.23) * height * 0.015);
      ctx.rotate(Math.sin(t * 0.19) * 0.06);
      ctx.scale(zoom, zoom);
      ctx.translate(-width / 2, -height / 2);
    }

    ctx.drawImage(image, 0, 0, width, height);
    ctx.restore();
  };

  return {
    kind: 'sample',
    element: canvas,
    get width() { return canvas.width; },
    get height() { return canvas.height; },
    get ready() { return drawn; },
    label: url.split('/').pop(),
    get drift() { return drift; },
    set drift(value) {
      drift = value;
      drawn = false; // force a redraw so switching the toggle takes effect at once
    },
    /** Returns whether the canvas changed, so the texture is only re-uploaded then. */
    update(timeMs) {
      // A frozen image only needs drawing once; drift needs it every frame.
      if (drift || !drawn) {
        draw(timeMs);
        drawn = true;
        return true;
      }
      return false;
    },

    /**
     * A new frame roughly 30 times a second, like the camera it stands in for.
     *
     * Not *every* call, which is what this used to do — and a still image that
     * answers "new frame" at the display's own rate is impersonating a 60 fps
     * camera. Everything downstream that reasons about the camera's cadence then
     * reasons about a fiction: most expensively the tracker's fallback, which asked
     * a freshly-started worker to keep a 17 ms interval no real webcam produces,
     * concluded it could not, and latched every session onto the main thread
     * before the actual camera was ever selected.
     *
     * Never "no new pixels", though — the fit is measured over 45 detections, and a
     * still that stopped producing frames would never finish the scan. The sample
     * exists to exercise the pipeline at the rate the pipeline actually runs.
     */
    nextFrame(nowMs) {
      if (nowMs - lastFrameMs < 1000 / 30) return null;
      lastFrameMs = nowMs;
      // Zero upstream, and truthfully so: a canvas has no sensor and no transport,
      // so every millisecond a sample source spends is one the pipeline spent.
      return {
        timestampMs: nowMs, capturedAtMs: nowMs, measuredCapture: true, upstreamMs: 0,
      };
    },

    /** No camera, so nothing can be dropped before us and there are no settings. */
    droppedFrames: 0,
    settings: null,

    stop() {},
  };
}
