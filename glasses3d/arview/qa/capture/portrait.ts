/** The worn front portrait of `arview capture` (port of 75ae133:ar/qa/portrait-preview.mjs with
 *  stash@{0}:ar/qa/portrait-preview.html; analysis.capture.portrait_method). Page side, one step per function.
 *
 *  Input: face-a.jpg only, read from the viewer-owned qa/fixtures and served by the orchestrator after its sha256
 *  matched the pinned b6491464…; there is no option for any other face image. Only face-a's inferred pose is rendered
 *  (no invented turns). Flow: decode at native size -> CPU face detection + CPU hair segmentation -> eye crop
 *  (landmarks 33/263) -> the renderer -> the SAME detection posed 51 times over 5 s -> condition a (runtime lighting)
 *  and condition b (side_studio 0.8 via the public setLighting) with the hair mask -> PNGs. Every hard throw of the old
 *  harness that was about the model (zero optical meshes, a continuity failure, an unsettled fit, a guard fallback) is a
 *  report warning here and the render still happens; the rendered-bytes identity check stays (integrity, not
 *  acceptance). The image is unmirrored; the live page mirrors it. */
import {TryOnRenderer, ModelRefusal, ModelUnchecked} from '../../src/render/renderer.ts';
import type {FittingReport, FrameTimings} from '../../src/render/renderer.ts';
import type {ModelDefinition} from '../../src/eyewear/catalog.ts';
import {DetectorClient} from '../../src/face/detector.ts';
import {HairClient} from '../../src/hair/client.ts';
import {DEFAULT_HAIR_MODEL_ID} from '../../src/hair/models.ts';
import type {HairSegmentationResult} from '../../src/hair/protocol.ts';
import type {Detection} from '../../src/face/protocol.ts';
import {poseAngles} from '../../src/render/pose-stabilizer.ts';
import {eyeCrop} from './portrait-contract.ts';

export const FACE_A_URL = '/__arview/face-a.jpg';
export const PORTRAIT_LIGHTING_B = Object.freeze({preset: 'side_studio' as const, intensity: 0.8});
export const NATIVE_RANGE_PX = Object.freeze([640, 1280] as const);

export interface Emitter {
  (file: string, canvas: HTMLCanvasElement): Promise<{file: string; sha256: string; bytes: number; width: number; height: number}>;
}
interface Rect {x: number; y: number; width: number; height: number;}
export interface PortraitRequest {
  readonly modelUrl: string; readonly displayName: string; readonly sha256: string; readonly fixtureSha256: string;
  readonly templeClipZM: number | null; readonly lensEnvIntensity: number | null; readonly fixedScale: boolean;
}

const digest = async (bytes: BufferSource): Promise<string> =>
  [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
const encode = (value: unknown): Uint8Array<ArrayBuffer> => new TextEncoder().encode(JSON.stringify(value));
const message = (error: unknown): string => error instanceof Error ? error.message : String(error);

const state: {
  source: HTMLCanvasElement | null; pixels: ImageData | null; detection: Detection | null; mask: HairSegmentationResult | null;
  crop: Rect | null; instance: TryOnRenderer | null; controller: AbortController | null; fixtureSha256: string | null;
  detectionSha256: string | null;
} = {source: null, pixels: null, detection: null, mask: null, crop: null, instance: null, controller: null, fixtureSha256: null, detectionSha256: null};

function canvasOf(pixels: ImageData, rect: Rect): HTMLCanvasElement {
  const canvas = document.createElement('canvas'); canvas.width = rect.width; canvas.height = rect.height;
  canvas.getContext('2d')!.putImageData(pixels, -rect.x, -rect.y, rect.x, rect.y, rect.width, rect.height);
  return canvas;
}

/** Step 1: decode face-a, detect the face and segment the hair on the CPU, emit the source eye detail. */
export async function prepare(emit: Emitter, fixtureSha256: string): Promise<Record<string, unknown>> {
  const warnings: string[] = [];
  const image = new Image(); image.src = FACE_A_URL; await image.decode();
  const source = document.createElement('canvas'); source.width = image.naturalWidth; source.height = image.naturalHeight;
  if (source.width > NATIVE_RANGE_PX[1] || source.width < NATIVE_RANGE_PX[0] || source.height < NATIVE_RANGE_PX[0]) {
    warnings.push(`face-a is ${source.width}x${source.height}, outside the native capture range ${NATIVE_RANGE_PX[0]}-${NATIVE_RANGE_PX[1]} px; `
      + 'rendered anyway (the renderer caps its width at 1280)');
  }
  const context = source.getContext('2d', {willReadFrequently: true})!; context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, source.width, source.height);
  const pixelHash = await digest(pixels.data);
  const controller = new AbortController();
  const detector = new DetectorClient({delegates: ['CPU']}), hair = new HairClient(DEFAULT_HAIR_MODEL_ID, {delegate: 'CPU'});
  try {
    await Promise.all([detector.initialize(controller.signal), hair.initialize(controller.signal)]);
    const [detection, mask] = await Promise.all([
      detector.detect(await createImageBitmap(source), 0),
      hair.segment(await createImageBitmap(source), pixelHash, 0),
    ]);
    const report: Record<string, unknown> = {fixture: {id: 'face-a', synthetic: true, sha256: fixtureSha256}, source_pixels_sha256: pixelHash,
      native_dimensions: [source.width, source.height],
      hair: {model: mask.model, model_sha256: mask.modelSHA256, mask_sha256: mask.categorySHA256, width: mask.width, height: mask.height,
        source_sha256: mask.sourceSHA256, delegate: 'CPU'}, face_delegate: 'CPU'};
    if (!detection.matrix || detection.landmarks.length < 468) {
      warnings.push(`face-a yielded no complete face detection (matrix ${detection.matrix ? 'present' : 'missing'}, ${detection.landmarks.length} landmarks); `
        + 'the portrait cannot be posed and is skipped');
      return {...report, ok: false, warnings};
    }
    const stable = {matrix: detection.matrix, landmarks: detection.landmarks};
    let crop: Rect;
    try {crop = eyeCrop(detection.landmarks, source.width, source.height);}
    catch (error) {
      crop = {x: 0, y: 0, width: source.width, height: source.height};
      warnings.push(`the eye detail crop could not be framed (${message(error)}); the eye images show the full frame`);
    }
    Object.assign(state, {source, pixels, detection: stable as Detection, mask, crop, fixtureSha256});
    state.detectionSha256 = await digest(encode(stable));
    const emitted = await emit('portrait__source__eyes.png', canvasOf(pixels, crop));
    return {...report, ok: true, warnings, detection_sha256: state.detectionSha256, inferred_pose: poseAngles(detection.matrix), eye_crop: crop,
      images: [{...emitted, kind: 'portrait', variant: 'source', region: 'eyes', crop}]};
  } finally {detector.close(); hair.close(); controller.abort();}
}

/** Step 2: the renderer for the portrait. The rendered bytes must be the input's (integrity). */
export async function create(request: PortraitRequest): Promise<Record<string, unknown>> {
  dispose();
  const warnings: string[] = [];
  const controller = new AbortController(); state.controller = controller;
  const definition: ModelDefinition = {url: request.modelUrl, displayName: request.displayName,
    ...(request.templeClipZM !== null ? {templeClipZM: request.templeClipZM} : {}),
    ...(request.lensEnvIntensity !== null ? {lensEnvIntensity: request.lensEnvIntensity} : {})};
  try {
    state.instance = await TryOnRenderer.create(document.createElement('canvas'), controller.signal, definition,
      {sync: false, steady: null, fixedScale: request.fixedScale});
  } catch (error) {
    if (error instanceof ModelRefusal || error instanceof ModelUnchecked) return {ok: false, status: error.report.status, report: error.report, warnings};
    throw error;
  }
  const instance = state.instance;
  const integrity = instance.renderedAssetIntegrity?.sha256 ?? null;
  if (instance.endpointUnavailable) warnings.push(`temple continuity is unavailable for the portrait (${instance.endpointUnavailable}); both arms keep the fixed buried cap`);
  if (!instance.parts().some(part => part.class === 'lens')) warnings.push('the portrait renderer drew no lens mesh');
  return {ok: true, status: 'rendered', integrity_sha256: integrity, integrity_ok: integrity === request.sha256, clip_m: instance.templeClipZM,
    gpu_renderer: instance.gpuRenderer, warnings};
}

/** Step 3: pose the SAME detection 51 times over 5 s (the fit measures and settles). */
export function settle(): {ready: boolean; rejected: number; fitting: FittingReport} {
  const instance = state.instance, source = state.source, detection = state.detection;
  if (!instance || !source || !detection) throw new Error('The portrait is not prepared.');
  let rejected = 0;
  for (let timestamp = 0; timestamp <= 5000; timestamp += 100) if (!instance.pose(source, detection, timestamp)) rejected++;
  return {ready: instance.fitting.ready, rejected, fitting: instance.fitting};
}

/** Step 4: one lighting condition: 'a' the runtime's own, 'b' side_studio 0.8. Emits the full frame and the eye detail. */
export async function render(condition: 'a' | 'b', emit: Emitter): Promise<Record<string, unknown>> {
  const instance = state.instance, source = state.source, mask = state.mask, crop = state.crop;
  if (!instance || !source || !mask || !crop) throw new Error('The portrait is not prepared.');
  const warnings: string[] = [];
  let timing: FrameTimings, pixels: ImageData;
  try {
    if (condition === 'b') instance.setLighting(PORTRAIT_LIGHTING_B);
    timing = instance.render(mask);
    pixels = instance.readback();
  } finally {if (condition === 'b') instance.setLighting(null);}
  if (pixels.width !== source.width || pixels.height !== source.height) {
    warnings.push(`condition ${condition}: the render is ${pixels.width}x${pixels.height}, not the portrait's native ${source.width}x${source.height}`);
  }
  if (timing.safeFallback || !timing.guarded) {
    warnings.push(`condition ${condition}: the render fell back from the guarded AR composition (guarded ${timing.guarded}, safeFallback ${timing.safeFallback}); `
      + 'drawn without hair occlusion protection');
  }
  if (!instance.fitting.ready) warnings.push(`condition ${condition}: the fit is not settled, so the glasses are not drawn in this image`);
  const full = {x: 0, y: 0, width: pixels.width, height: pixels.height};
  const eyes = {x: Math.min(crop.x, Math.max(0, pixels.width - 1)), y: Math.min(crop.y, Math.max(0, pixels.height - 1)),
    width: Math.min(crop.width, pixels.width - Math.min(crop.x, pixels.width - 1)), height: Math.min(crop.height, pixels.height - Math.min(crop.y, pixels.height - 1))};
  const images = [
    {...await emit(`portrait__${condition}__full.png`, canvasOf(pixels, full)), kind: 'portrait', variant: `condition-${condition}`, region: 'full', crop: full},
    {...await emit(`portrait__${condition}__eyes.png`, canvasOf(pixels, eyes)), kind: 'portrait', variant: `condition-${condition}`, region: 'eyes', crop: eyes},
  ];
  const camera = instance.cameraState();
  return {id: `condition-${condition}`, lighting: condition === 'a' ? 'unmodified runtime default' : PORTRAIT_LIGHTING_B,
    timing: {guarded: timing.guarded, passes: timing.passes, safeFallback: timing.safeFallback, shadowsApplied: timing.shadowsApplied ?? null,
      hairApplied: timing.hairApplied, templeEndState: timing.templeEndState},
    fitting: instance.fitting, camera, images, warnings,
    comparison_key: await digest(encode({fixture: state.fixtureSha256, detection: state.detectionSha256, camera, crop}))};
}

/** Step 5: the portrait renderer's final model report, then dispose. */
export function finish(): {report: unknown; asset_to_world: number[] | null} {
  const instance = state.instance;
  if (!instance) return {report: null, asset_to_world: null};
  const result = {report: instance.report(), asset_to_world: instance.assetMatrixWorld()};
  dispose();
  return result;
}

export function dispose(): void {
  state.instance?.dispose(); state.instance = null;
  state.controller?.abort(); state.controller = null;
}
