export interface CameraSession {
  video: HTMLVideoElement;
  stop(): void;
}

const PERMISSION_TIMEOUT_MS = 45_000;
const VIDEO_TIMEOUT_MS = 10_000;

function abortError(): DOMException {
  return new DOMException('Camera startup was cancelled.', 'AbortError');
}

function stopTracks(stream: MediaStream): void {
  for (const track of stream.getTracks()) track.stop();
}

/** getUserMedia cannot be cancelled; release a stream even if it arrives late. */
function getStream(signal: AbortSignal): Promise<MediaStream> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (error?: unknown, stream?: MediaStream) => {
      if (settled) {
        if (stream) stopTracks(stream);
        return;
      }
      settled = true;
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      if (error !== undefined) reject(error);
      else resolve(stream!);
    };
    const onAbort = () => finish(abortError());
    signal.addEventListener('abort', onAbort, { once: true });
    timer = setTimeout(() => finish(new Error('Camera permission timed out. Please try again.')), PERMISSION_TIMEOUT_MS);
    if (signal.aborted) {
      onAbort();
      return;
    }
    try {
      const request = navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      void request.then((stream) => finish(undefined, stream), (error: unknown) => finish(error));
    } catch (error) {
      finish(error);
    }
  });
}

function waitForMetadata(video: HTMLVideoElement, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (error?: unknown) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      video.removeEventListener('loadedmetadata', onMetadata);
      video.removeEventListener('resize', onMetadata);
      video.removeEventListener('error', onError);
      if (error !== undefined) reject(error);
      else resolve();
    };
    const onAbort = () => finish(abortError());
    const onError = () => finish(new Error('The camera video could not be opened.'));
    const onMetadata = () => {
      if (video.readyState >= 1 && video.videoWidth > 0 && video.videoHeight > 0) finish();
    };
    signal.addEventListener('abort', onAbort, { once: true });
    video.addEventListener('loadedmetadata', onMetadata);
    video.addEventListener('resize', onMetadata);
    video.addEventListener('error', onError, { once: true });
    timer = setTimeout(() => finish(new Error('The camera did not provide video metadata in time.')), VIDEO_TIMEOUT_MS);
    if (signal.aborted) onAbort();
    else onMetadata();
  });
}

function playVideo(video: HTMLVideoElement, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (error?: unknown) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      if (error !== undefined) reject(error);
      else resolve();
    };
    const onAbort = () => finish(abortError());
    signal.addEventListener('abort', onAbort, { once: true });
    timer = setTimeout(() => finish(new Error('Camera playback did not start in time.')), VIDEO_TIMEOUT_MS);
    if (signal.aborted) {
      onAbort();
      return;
    }
    try {
      void video.play().then(() => finish(), (error: unknown) => finish(error));
    } catch (error) {
      finish(error);
    }
  });
}

export async function openCamera(signal: AbortSignal): Promise<CameraSession> {
  if (signal.aborted) throw abortError();
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('Camera access requires a supported browser on HTTPS or localhost.');
  }

  const stream = await getStream(signal);
  let video: HTMLVideoElement;
  try {
    video = document.createElement('video');
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
  } catch (error) {
    stopTracks(stream);
    throw error;
  }
  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    signal.removeEventListener('abort', stop);
    stopTracks(stream);
    video.pause();
    video.srcObject = null;
    video.removeAttribute('src');
  };

  signal.addEventListener('abort', stop, { once: true });
  try {
    if (signal.aborted) throw abortError();
    video.srcObject = stream;
    await waitForMetadata(video, signal);
    await playVideo(video, signal);
    if (signal.aborted) throw abortError();
    return { video, stop };
  } catch (error) {
    stop();
    throw error;
  }
}
