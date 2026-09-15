/** How a camera frame's pixels reach the pipeline.
 *
 *  `canvas` (accepted): the video is drawn into a CPU-backed 2D canvas and its RGBA is read once; the SHA-256 of that RGBA
 *  is the frame's identity. On the laptop's real webcam the draw alone is 10 ms cold and doubles when the CPU clocks down
 *  (2026-09-15), the largest main-thread item per frame.
 *
 *  `videoframe` (lever, `?source=videoframe`): the frame is taken as a WebCodecs VideoFrame; its own bytes, copied out
 *  once in their native format, are hashed for the identity, and the canvas the render and the workers read is GPU-backed
 *  and never read back. The synthetic camera cannot measure the difference (its frames are already RGBA canvases); a real
 *  webcam run with Download timings can. */
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
