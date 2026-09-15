/** Opt-in camera exposure lock (`?exposure=<units of 100 µs>`). Measured 2026-09-14 on the owner's laptop webcam
 *  (USB2.0 FHD UVC WebCam): in dim light auto-exposure settles at 1/16 s (625) and the camera then delivers only 15–16
 *  frames per second whatever the page requests, which capped every evening fps test at the same figure. The driver
 *  honours exposure times in halving steps (39, 156, 312.5, 625 …); at 312.5 (1/32 s) it delivers 30 fps again, one
 *  stop darker. This helper applies a manual exposure on an already-open track and reports what the camera settled
 *  on; it never changes resolution or frame-rate constraints and does nothing when the camera exposes no control. */
export interface ExposureLock {
  requested: number; applied: number | null; mode: string | null; error: string | null;
  capability: {min: number; max: number; step?: number} | null;
}
interface ExposureCapabilities {exposureMode?: string[]; exposureTime?: {min: number; max: number; step?: number};}
interface ExposureSettings {exposureMode?: string; exposureTime?: number;}

export async function lockCameraExposure(video: HTMLVideoElement, exposureTime: number): Promise<ExposureLock> {
  const stream = video.srcObject;
  const track = stream instanceof MediaStream ? stream.getVideoTracks()[0] ?? null : null;
  const result: ExposureLock = {requested: exposureTime, applied: null, mode: null, error: null, capability: null};
  if (!track) {result.error = 'No video track.'; return result;}
  const capabilities = (typeof track.getCapabilities === 'function' ? track.getCapabilities() : {}) as ExposureCapabilities;
  if (!capabilities.exposureMode?.includes('manual') || !capabilities.exposureTime) {result.error = 'This camera exposes no manual exposure control.'; return result;}
  result.capability = {...capabilities.exposureTime};
  const target = Math.max(capabilities.exposureTime.min, Math.min(exposureTime, capabilities.exposureTime.max));
  try {
    await track.applyConstraints({advanced: [{exposureMode: 'manual'} as unknown as MediaTrackConstraintSet]});
    await track.applyConstraints({advanced: [{exposureTime: target} as unknown as MediaTrackConstraintSet]});
    const settings = track.getSettings() as ExposureSettings;
    result.applied = settings.exposureTime ?? null; result.mode = settings.exposureMode ?? null;
  } catch (error) {result.error = error instanceof Error ? `${error.name}: ${error.message}` : String(error);}
  return result;
}
