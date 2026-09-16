/** How a camera frame's pixels reach the pipeline.
 *
 *  `canvas` (accepted): the video is drawn into a CPU-backed 2D canvas and its RGBA is read once; the SHA-256 of that RGBA
 *  is the frame's identity. On the laptop's real webcam the draw alone is 10 ms cold and doubles when the CPU clocks down
 *  (2026-09-15), the largest main-thread item per frame.
 *
 *  `videoframe` (laptop default since 2026-09-16, `?source=`): the frame is taken as a WebCodecs VideoFrame; its own
 *  bytes, copied out once in their native format, are hashed for the identity, and the canvas the render and the workers
 *  read is GPU-backed and never read back. Owner's webcam at 30 fps, 80 s runs: capture 15 → 6.7 ms per frame, age 52 →
 *  41 ms, and no clock-down decay (29.4 fps throughout against the canvas path's 26.5 after 50 s), masks on every frame
 *  after warm-up. The synthetic camera measures the opposite (its frames are GPU-resident, so the path lands on Chrome's
 *  GPU-process thread: 24.6 vs 29.6 fps); judge this path on a real camera only.
 *
 *  Phones keep the canvas, measured the same way and back to back on the iPhone 17 Pro (2026-09-16): the main thread is
 *  indeed freer (0.5-2.4 ms waiting to start a frame against 5.8-8.2), but drawing the turned frame costs the GPU more
 *  (finish 12-17 ms against 10-13) on a device whose GPU is already the limit, and the canvas held 28-29 fps through the
 *  window where the VideoFrame path had fallen to 25-27. The two meet at about 24 fps once the phone throttles. */
export type CaptureSource = 'canvas' | 'videoframe';
export interface CaptureChoice {source: CaptureSource; reason: string | null;}

/** The requested source, or the canvas when the browser has no VideoFrame; the reason is reported, never silent. */
export function chooseCaptureSource(requested: CaptureSource, videoFrameAvailable: boolean): CaptureChoice {
  if (requested === 'videoframe' && !videoFrameAvailable) return {source: 'canvas', reason: 'VideoFrame is unavailable in this browser; the canvas capture is used.'};
  return {source: requested, reason: null};
}

/** The subset of WebCodecs' VideoFrame the capture uses (a seam for tests). */
export interface VideoFrameLike {
  readonly format: string | null;
  allocationSize(): number;
  copyTo(destination: Uint8Array): Promise<unknown>;
  close(): void;
}

/** A frame's identity bytes and what they are: the capture canvas's RGBA, or the VideoFrame's own bytes. */
export interface FrameBytes {
  readonly bytes: Promise<Uint8Array | Uint8ClampedArray>;
  readonly format: string;
  /** Time spent producing the bytes (getImageData, or the VideoFrame copy), known once `bytes` settles. */
  readMs(): number;
}

export function bytesOfImageData(image: ImageData, readMs: number): FrameBytes {
  return {bytes: Promise.resolve(image.data), format: 'RGBA', readMs: () => readMs};
}

/** Copies the VideoFrame's bytes out once, now, so the frame may be closed as soon as its consumers are done. */
export function bytesOfVideoFrame(frame: VideoFrameLike, now: () => number = () => performance.now()): FrameBytes {
  const started = now(); let readMs = 0;
  const destination = new Uint8Array(frame.allocationSize());
  const bytes = frame.copyTo(destination).then(() => {readMs = now() - started; return destination;});
  // A frame dropped before inference is closed with its copy in flight; that rejection has no consumer.
  bytes.catch(() => undefined);
  return {bytes, format: frame.format ?? 'unknown', readMs: () => readMs};
}

/** A VideoFrame owned by one captured packet: closed exactly once, when the packet is disposed. */
export class OwnedVideoFrame {
  private frame: VideoFrameLike | null;
  constructor(frame: VideoFrameLike) {this.frame = frame;}
  get closed(): boolean {return this.frame === null;}
  close(): void {const frame = this.frame; this.frame = null; frame?.close();}
}

/** Hex SHA-256 of the bytes: the identity that ties a hair mask to the exact image it was computed from. */
export async function sha256Hex(bytes: Uint8Array | Uint8ClampedArray): Promise<string> {
  const copy = new Uint8Array(bytes);
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', copy.buffer)), v => v.toString(16).padStart(2, '0')).join('');
}

/** The camera frame's orientation relative to the `<video>` element's own image.
 *
 *  A phone camera's sensor is landscape: the track carries a rotation that the browser applies when it displays the
 *  `<video>`, and `video.videoWidth/videoHeight` are those displayed dimensions. A VideoFrame taken from that video
 *  can still hold the sensor's own pixels, so drawing it into a canvas sized from the video turns the picture on its
 *  side and the face tracker sees nobody (iPhone 17 Pro, 2026-09-16: 30 fps, 0 of 160 frames tracked).
 *
 *  The relationship is measured rather than guessed, and measured against the browser's own displayed image: both are
 *  drawn into the same small square, and the rotation of the video's square that matches the frame's square is the
 *  rotation the capture then undoes. A square is used so all four rotations are comparable pixel for pixel. */
export const ORIENTATION_PROBE_EDGE = 32;
/** How close the best rotation must be to count as the same image (mean channel difference, 0..255): the two images
 *  pass through different colour conversions, so they are never identical. */
export const ORIENTATION_MATCH_LIMIT = 40;
/** How far the best rotation must beat the next one; below this the picture is too uniform to tell them apart. */
export const ORIENTATION_SEPARATION = 6;

export type Rotation = 0 | 90 | 180 | 270;
export const ROTATIONS: readonly Rotation[] = [0, 90, 180, 270];

/** Rotate a square RGBA image clockwise. */
export function rotateSquare(pixels: Uint8ClampedArray, edge: number, degrees: Rotation): Uint8ClampedArray {
  if (pixels.length !== edge * edge * 4) throw new Error('The orientation probe is not a square RGBA image.');
  if (degrees === 0) return pixels;
  const out = new Uint8ClampedArray(pixels.length);
  for (let y = 0; y < edge; y++) {
    for (let x = 0; x < edge; x++) {
      const sx = degrees === 90 ? y : degrees === 180 ? edge - 1 - x : edge - 1 - y;
      const sy = degrees === 90 ? edge - 1 - x : degrees === 180 ? edge - 1 - y : x;
      const to = (y * edge + x) * 4, from = (sy * edge + sx) * 4;
      out[to] = pixels[from]!; out[to + 1] = pixels[from + 1]!; out[to + 2] = pixels[from + 2]!; out[to + 3] = pixels[from + 3]!;
    }
  }
  return out;
}

/** Mean absolute difference per colour channel (alpha ignored). */
export function meanChannelDifference(a: Uint8ClampedArray, b: Uint8ClampedArray): number {
  if (a.length !== b.length || a.length % 4 !== 0) throw new Error('The orientation probes differ in size.');
  let total = 0;
  for (let index = 0; index < a.length; index += 4) {
    total += Math.abs(a[index]! - b[index]!) + Math.abs(a[index + 1]! - b[index + 1]!) + Math.abs(a[index + 2]! - b[index + 2]!);
  }
  return total / (a.length / 4 * 3);
}

export interface OrientationMatch {
  /** Clockwise rotation R with frame ≈ rotate(video, R); the capture draws the frame rotated by −R. */
  degrees: Rotation;
  difference: number;
  runnerUp: number;
  /** Whether one rotation is close enough, and far enough ahead of the next, to act on. */
  conclusive: boolean;
}

export function matchOrientation(frame: Uint8ClampedArray, video: Uint8ClampedArray, edge: number): OrientationMatch {
  const scored = ROTATIONS.map(degrees => ({degrees, difference: meanChannelDifference(frame, rotateSquare(video, edge, degrees))}))
    .sort((a, b) => a.difference - b.difference);
  const best = scored[0]!, next = scored[1]!;
  return {degrees: best.degrees, difference: best.difference, runnerUp: next.difference,
    conclusive: best.difference <= ORIENTATION_MATCH_LIMIT && next.difference - best.difference >= ORIENTATION_SEPARATION};
}
