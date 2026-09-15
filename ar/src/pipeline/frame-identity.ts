/** Which camera frame a capture callback is looking at. With `requestVideoFrameCallback` the presented-frame counter
 *  identifies the frame; in the `requestAnimationFrame` fallback a changed `currentTime` does. `currentTime` is never
 *  compared before and after a draw: for a camera stream WebKit reports a running clock that differs between any two
 *  reads (Chrome reports the last frame's timestamp), and that comparison discarded every frame on iPhone
 *  (2026-09-15), leaving the mirror waiting for its first frame with the camera on. */
export interface FrameMark {presented: number | null; mediaTime: number;}
export interface FrameMetadata {presentedFrames: number; mediaTime: number;}

export function markFrame(metadata: FrameMetadata | undefined, currentTime: number, last: FrameMark | null): {fresh: boolean; mark: FrameMark} {
  const presented = metadata?.presentedFrames ?? null, mediaTime = metadata?.mediaTime ?? currentTime;
  const fresh = last === null || (presented !== null && last.presented !== null ? presented !== last.presented : mediaTime !== last.mediaTime);
  return {fresh, mark: {presented, mediaTime}};
}
