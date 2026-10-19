import {wearerSide} from '../../src/model/sides.ts';
/** The capture page (qa/capture.html): `window.arview`, driven by qa/capture.mjs ONE STEP PER page.evaluate.
 *
 *  Every step returns plain data; every image is handed to Node through the `arviewEmit` binding the moment it exists
 *  (written to disk and hashed there), so nothing accumulates in page memory and a stall loses nothing already made.
 *  The renderer is used through its public API only (glasses3d/plan/archive/20261019/WP1_DESIGN.md §5: create, pose, render with an override,
 *  readback, report, parts, cameraState, assetMatrixWorld, projectAssetPoints, setLighting). Steps:
 *  gpu -> init -> geometry -> settle -> (pose -> render...)... -> finishPosed -> portrait* -> done; with `--cameras
 *  --no-face` (capture/cameras.ts): gpu -> init -> (poseCamera -> renderCamera...)... -> finishPosed. */
import {TryOnRenderer, ModelRefusal, ModelUnchecked} from '../../src/render/renderer.ts';
import type {FittingReport, FrameTimings, PaletteEntry} from '../../src/render/renderer.ts';
import type {ModelDefinition} from '../../src/eyewear/catalog.ts';
import {syntheticDetection} from './synthetic-face.ts';
import type {SyntheticDetection} from './synthetic-face.ts';
import {cropFile, paintBackground, SAFE_FILE, VARIANT_OVERRIDE} from './poses.ts';
import type {BackgroundName, CapturePose, VariantName} from './poses.ts';
import {boxCorners, cropRect, lensTopVsBrow, nearSide} from './junction.ts';
import type {CropRect, JunctionGeometry, JunctionSide} from './junction.ts';
import {bandMetrics, decodeLabels, junctionColumns, paletteDecoder} from './id-palette.ts';
import {measureTempleReach, nearSideOfYaw, templeReachWarning} from './temple-reach.ts';
import {wornCropRect} from './worn.ts';
import type {TempleReach} from './temple-reach.ts';
import * as portrait from './portrait.ts';
import type {PortraitRequest} from './portrait.ts';
import {assetToCameraScene, METRES_TO_SCENE, projectionMatrix} from './cameras.ts';
import type {CameraVariant, ExplicitCameraSpec} from './cameras.ts';

interface EmitResult {sha256: string; bytes: number}
declare global {
  interface Window {
    arview?: typeof api;
    /** exposeBinding('arviewEmit') in qa/capture.mjs: writes the PNG, returns its sha256 and size. */
    arviewEmit?: (meta: {file: string}, base64: string) => Promise<EmitResult>;
  }
}

export interface InitRequest {
  readonly modelUrl: string; readonly displayName: string; readonly templeClipZM: number | null; readonly lensEnvIntensity: number | null;
  readonly fixedScale: boolean; readonly width: number; readonly height: number; readonly depthCm: number; readonly firstBackground: BackgroundName;
}
interface SideFrame {
  side: JunctionSide; rect: CropRect | null; reason: string | null; area: number;
  /** Projected lens-top stations (render pixels) and the projected 3 mm, for the column metrics. */
  spanX: [number, number] | null; topY: number | null; walkPx: number | null;
}

const state: {
  canonical: number[] | null; instance: TryOnRenderer | null; controller: AbortController | null; request: InitRequest | null;
  source: HTMLCanvasElement; timestamp: number; geometry: JunctionGeometry | null; pose: CapturePose | null; detection: SyntheticDetection | null;
  background: BackgroundName | null; sides: SideFrame[];
} = {canonical: null, instance: null, controller: null, request: null, source: document.createElement('canvas'), timestamp: 0, geometry: null,
  pose: null, detection: null, background: null, sides: []};

const SETTLE_FRONT_POSES = 51, SETTLE_VIEW_POSES = 30, SETTLE_BACKGROUND_POSES = 3, FRAME_MS = 100;

function instanceOf(): TryOnRenderer {
  if (!state.instance) throw new Error('No renderer: init has not run or the model was refused.');
  return state.instance;
}
async function emitCanvas(file: string, canvas: HTMLCanvasElement): Promise<{file: string; sha256: string; bytes: number; width: number; height: number}> {
  if (!SAFE_FILE.test(file)) throw new Error(`Unsafe capture file name ${JSON.stringify(file)}.`);
  if (!window.arviewEmit) throw new Error('The arviewEmit binding is missing.');
  const data = canvas.toDataURL('image/png');
  const result = await window.arviewEmit({file}, data.slice(data.indexOf(',') + 1));
  return {file, sha256: result.sha256, bytes: result.bytes, width: canvas.width, height: canvas.height};
}
function canvasOf(pixels: ImageData, rect: {x: number; y: number; width: number; height: number}, upscale = 1): HTMLCanvasElement {
  const canvas = document.createElement('canvas'); canvas.width = rect.width; canvas.height = rect.height;
  canvas.getContext('2d')!.putImageData(pixels, -rect.x, -rect.y, rect.x, rect.y, rect.width, rect.height);
  if (upscale === 1) return canvas;
  const scaled = document.createElement('canvas'); scaled.width = rect.width * upscale; scaled.height = rect.height * upscale;
  const context = scaled.getContext('2d')!; context.imageSmoothingEnabled = false;
  context.drawImage(canvas, 0, 0, scaled.width, scaled.height);
  return scaled;
}
function cropPixels(pixels: ImageData, rect: CropRect): Uint8ClampedArray {
  const out = new Uint8ClampedArray(rect.width * rect.height * 4);
  for (let y = 0; y < rect.height; y++) {
    const from = ((rect.y + y) * pixels.width + rect.x) * 4;
    out.set(pixels.data.subarray(from, from + rect.width * 4), y * rect.width * 4);
  }
  return out;
}
function paint(background: BackgroundName): void {
  const context = state.source.getContext('2d')!;
  paintBackground(context, state.source.width, state.source.height, background);
  state.background = background;
}

/** GPU facts of this browser, before any model is loaded (qa/capture.mjs refuses a software renderer). */
function gpu(): Record<string, unknown> {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl2', {alpha: false, antialias: true, stencil: true, powerPreference: 'high-performance'});
  if (!gl) return {webgl2: false, renderer: null, vendor: null, user_agent: navigator.userAgent};
  const debug = gl.getExtension('WEBGL_debug_renderer_info');
  const result = {webgl2: true,
    renderer: String(gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER)),
    vendor: String(gl.getParameter(debug ? debug.UNMASKED_VENDOR_WEBGL : gl.VENDOR)),
    unmasked: !!debug, samples: Number(gl.getParameter(gl.SAMPLES)), max_samples: Number(gl.getParameter(gl.MAX_SAMPLES)),
    float_color: !!gl.getExtension('EXT_color_buffer_float'), max_texture_px: Number(gl.getParameter(gl.MAX_TEXTURE_SIZE)),
    user_agent: navigator.userAgent};
  gl.getExtension('WEBGL_lose_context')?.loseContext();
  return result;
}

function disposePosed(): void {
  state.instance?.dispose(); state.instance = null;
  state.controller?.abort(); state.controller = null;
}

/** Load the model (TryOnRenderer.create). A §2 refusal or an unchecked file is a result, not a throw. */
async function init(request: InitRequest): Promise<Record<string, unknown>> {
  disposePosed();
  state.request = request;
  if (!state.canonical) {
    const response = await fetch('/models/canonical-face.json');
    if (!response.ok) throw new Error(`The canonical face could not be loaded (${response.status}).`);
    const face = await response.json() as {positions?: number[]};
    if (!Array.isArray(face.positions) || face.positions.length !== 468 * 3) throw new Error('The canonical face has no 468 positions.');
    state.canonical = face.positions;
  }
  state.source.width = request.width; state.source.height = request.height;
  paint(request.firstBackground);
  state.timestamp = 0; state.pose = null; state.detection = null; state.sides = [];
  const controller = new AbortController(); state.controller = controller;
  const definition: ModelDefinition = {url: request.modelUrl, displayName: request.displayName,
    ...(request.templeClipZM !== null ? {templeClipZM: request.templeClipZM} : {}),
    ...(request.lensEnvIntensity !== null ? {lensEnvIntensity: request.lensEnvIntensity} : {})};
  try {
    state.instance = await TryOnRenderer.create(document.createElement('canvas'), controller.signal, definition,
      {sync: false, steady: null, fixedScale: request.fixedScale});
  } catch (error) {
    state.controller = null;
    if (error instanceof ModelRefusal) return {status: 'refused', report: error.report};
    if (error instanceof ModelUnchecked) return {status: 'unchecked', report: error.report};
    throw error;
  }
  const instance = state.instance;
  return {status: 'rendered', report: instance.report(), gpu_renderer: instance.gpuRenderer, samples: instance.nativeSamples,
    guard: instance.guardEnabled, clip_m: instance.templeClipZM, temples: instance.temples(),
    rendered_sha256: instance.renderedAssetIntegrity?.sha256 ?? null, fixed_scale: request.fixedScale,
    parts: instance.parts().map(part => ({name: part.name, class: part.class, side: part.side, overlay: part.overlay,
      triangles: Math.floor((part.mesh.geometry.index?.count ?? part.mesh.geometry.getAttribute('position')?.count ?? 0) / 3)}))};
}

/** The lens-brow junction measured on the normalised scene (asset-local metres), before any pose shapes an arm. */
function geometry(): Record<string, unknown> {
  const instance = instanceOf();
  const lens: number[] = [], frames = new Map<string, number[]>(), warnings: string[] = [];
  let frameTriangles = 0;
  for (const part of instance.parts()) {
    if (part.overlay) continue;
    if (part.class === 'mixed') warnings.push(`mesh "${part.name}" mixes lens and frame materials; its triangles count as frame for the junction`);
    // Frame parts keep their node name as a label for the per-part readings (nothing is decided by name).
    const label = part.name || '(unnamed)';
    const target = part.class === 'lens' ? lens : frames.get(label) ?? frames.set(label, []).get(label)!;
    const geometryValue = part.mesh.geometry, position = geometryValue.getAttribute('position'), index = geometryValue.index;
    if (!position) continue;
    const count = index ? index.count : position.count;
    for (let i = 0; i + 2 < count; i += 3) {
      for (let k = 0; k < 3; k++) {
        const vertex = index ? index.getX(i + k) : i + k;
        target.push(position.getX(vertex), position.getY(vertex), position.getZ(vertex));
      }
      if (target !== lens) frameTriangles++;
    }
  }
  state.geometry = lensTopVsBrow(Float64Array.from(lens), [...frames].map(([name, triangles]) => ({name, triangles: Float64Array.from(triangles)})));
  return {...state.geometry, warnings: [...warnings, ...state.geometry.warnings], lens_triangles: lens.length / 9, frame_triangles: frameTriangles};
}

/** The frontal settle: the front pose 51 times over 5 s, so the live fit measures and settles before any turn. */
function settle(): {ready: boolean; rejected: number; fitting: FittingReport} {
  const instance = instanceOf(), request = state.request!;
  paint(request.firstBackground);
  const detection = syntheticDetection(state.canonical!, request.width, request.height, {yaw: 0, pitch: 0, depthCm: request.depthCm});
  let rejected = 0;
  for (let i = 0; i < SETTLE_FRONT_POSES; i++) {if (!instance.pose(state.source, detection, state.timestamp)) rejected++; state.timestamp += FRAME_MS;}
  return {ready: instance.fitting.ready, rejected, fitting: instance.fitting};
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

/** Frame each side's junction box for the posed frame: the crop rectangle, the projected lens-top span and 3 mm. */
function frameSides(): SideFrame[] {
  const instance = instanceOf(), geometryValue = state.geometry, {width, height} = state.source;
  const render = instance.renderSize;
  if (render.width !== width || render.height !== height) throw new Error(`The render is ${render.width}x${render.height}, not the requested ${width}x${height}.`);
  const out: SideFrame[] = [];
  for (const side of geometryValue?.sides ?? []) {
    if (!side.box_m) {out.push({side: side.side, rect: null, reason: 'no junction box on this side (no lens-top station)', area: 0, spanX: null, topY: null, walkPx: null}); continue;}
    const corners = instance.projectAssetPoints(boxCorners(side.box_m));
    if (!corners) throw new Error('projectAssetPoints returned nothing for a posed frame.');
    const {rect, reason} = cropRect(corners, width, height);
    const basis = side.stations.filter(station => station.lens_top_y_mm !== null && (side.basis !== 'frame_above' || station.frame_above));
    const points = new Float32Array(basis.length * 6);
    basis.forEach((station, i) => {
      points.set([station.x_mm / 1000, station.lens_top_y_mm! / 1000, station.lens_top_z_mm! / 1000,
        station.x_mm / 1000, station.lens_top_y_mm! / 1000 + 0.003, station.lens_top_z_mm! / 1000], i * 6);
    });
    const projected = instance.projectAssetPoints(points) ?? new Float32Array(0);
    const xs: number[] = [], ys: number[] = [], walks: number[] = [];
    for (let i = 0; i < basis.length; i++) {
      const x = projected[i * 6]!, y = projected[i * 6 + 1]!, x3 = projected[i * 6 + 3]!, y3 = projected[i * 6 + 4]!;
      if (![x, y, x3, y3].every(Number.isFinite)) continue;
      xs.push(x); ys.push(y); walks.push(Math.hypot(x3 - x, y3 - y));
    }
    out.push({side: side.side, rect, reason: reason ?? null, area: rect ? rect.width * rect.height : 0,
      spanX: xs.length ? [Math.min(...xs), Math.max(...xs)] : null, topY: ys.length ? Math.min(...ys) : null, walkPx: median(walks)});
  }
  return out;
}

/** From the near lens's outer half through the complete drawn arm and 25 mm past its buried cap. The proxy has
 * no anatomical ears: the extra posterior span shows the actual hiding surface beyond the fixed return. */
function frameWornSide(): {rect: CropRect | null; side: 'left' | 'right'; basis: string; projected: number[]} | null {
  if (!state.pose?.worn) return null;
  const instance = instanceOf(), side = nearSideOfYaw(state.pose.yaw);
  if (side === null) return null;
  const sign = side === 0 ? -1 : 1, points: number[] = [], lenses: number[][] = [];
  for (const part of instance.parts()) {
    if (part.overlay || part.class !== 'lens') continue;
    const p = part.mesh.geometry.getAttribute('position');
    for (let i = 0; i < p.count; i++) if (p.getX(i) * sign > 0) lenses.push([p.getX(i), p.getY(i), p.getZ(i)]);
  }
  const edge = lenses.reduce((value, point) => Math.max(value, point[0]! * sign), 0);
  for (const point of lenses) if (point[0]! * sign >= edge * .5) points.push(...point);
  const arms = instance.templeCentrelines(), arm = arms?.sides[side];
  if (arm?.length) {
    points.push(...arm);
    const last = arm.length - 3;
    points.push(arm[last]!, arm[last + 1]!, (arms!.capZM[side] ?? arm[last + 2]!) - .025);
  }
  const projected = instance.projectAssetPoints(new Float32Array(points));
  return {rect: projected ? wornCropRect(projected, state.source.width, state.source.height) : null,
    side: wearerSide(side), basis: 'near lens outer half, drawn temple centreline, 25 mm behind cap',
    projected: projected ? Array.from(projected) : []};
}

/** Pose the synthetic face (30 frames at the pose, so the contact-depth and head-fit filters settle) and frame the
 *  junction crops of a junction pose. */
function pose(request: {pose: CapturePose; background: BackgroundName}): Record<string, unknown> {
  const instance = instanceOf(), init = state.request!;
  const detection = syntheticDetection(state.canonical!, init.width, init.height, request.pose);
  paint(request.background);
  let rejected = 0;
  for (let i = 0; i < SETTLE_VIEW_POSES; i++) {state.timestamp += FRAME_MS; if (!instance.pose(state.source, detection, state.timestamp)) rejected++;}
  state.pose = request.pose; state.detection = detection;
  state.sides = request.pose.junction ? frameSides() : [];
  const near = request.pose.junction ? nearSide(Object.fromEntries(state.sides.filter(side => side.rect).map(side => [side.side, side.area]))) : null;
  return {id: request.pose.id, yaw: request.pose.yaw, pitch: request.pose.pitch, roll: request.pose.roll, depth_cm: request.pose.depthCm, order: request.pose.order,
    tracked: rejected === 0, rejected, pose_sample: instance.poseSample, camera: instance.cameraState(), asset_to_world: instance.assetMatrixWorld(),
    fitting: instance.fitting, near, worn: state.pose.worn ? {...frameWornSide(), projected: undefined,
      head: 'existing face surface and connected posterior head depth meshes; no anatomical ears', hair: false} : null,
    junction: state.sides.map(side => ({side: side.side, crop: side.rect, reason: side.reason, area_px: side.area,
      lens_top_span_px: side.spanX ? side.spanX.map(value => Math.round(value * 10) / 10) : null,
      lens_top_y_px: side.topY === null ? null : Math.round(side.topY * 10) / 10,
      three_mm_px: side.walkPx === null ? null : Math.round(side.walkPx * 100) / 100}))};
}

/** One render of the posed frame, emitted at once, plus its junction crops and their metrics. */
async function render(request: {variant: VariantName; background: BackgroundName | null; file: string; crops: boolean; cropUpscale: 1 | 2}): Promise<Record<string, unknown>> {
  const instance = instanceOf();
  const warnings: string[] = [];
  if (request.background !== null && request.background !== state.background) {
    // A new camera frame (background) at the same pose: a few more frames at the same detection.
    paint(request.background);
    for (let i = 0; i < SETTLE_BACKGROUND_POSES; i++) {state.timestamp += FRAME_MS; instance.pose(state.source, state.detection!, state.timestamp);}
  }
  const override = VARIANT_OVERRIDE[request.variant];
  const timing: FrameTimings = override ? instance.render(null, {override}) : instance.render(null,
    {hair: false, shadows: true, guard: true, visibleHead: state.pose?.worn === true});
  const pixels = instance.readback();
  if (pixels.width !== state.source.width || pixels.height !== state.source.height) {
    warnings.push(`${request.file}: read back ${pixels.width}x${pixels.height}, not the requested ${state.source.width}x${state.source.height}`);
  }
  if (!instance.fitting.ready) warnings.push(`${request.file}: the fit is not settled, so the glasses are not drawn`);
  if (override === null && timing.safeFallback) warnings.push(`${request.file}: the guarded composition fell back (protection unavailable for this pose)`);
  const images: Record<string, unknown>[] = [];
  const full = await emitCanvas(request.file, canvasOf(pixels, {x: 0, y: 0, width: pixels.width, height: pixels.height}));
  images.push({...full, kind: 'pose', crop: null, side: null});
  if (state.pose?.worn && request.variant === 'beauty' && request.background === 'skin') {
    const worn = frameWornSide();
    if (worn?.rect) {
      const file = request.file.replace(/\.png$/, '__worn.png');
      images.push({...await emitCanvas(file, canvasOf(pixels, worn.rect)), kind: 'worn_crop', crop: worn.rect,
        side: worn.side, source: request.file, head: 'same occlusion meshes', hair: false});
    } else warnings.push(`${request.file}: worn side crop unavailable (no projected lens or temple geometry)`);
  }
  const palette: PaletteEntry[] = timing.palette ?? [];
  const decoder = request.variant === 'part-id' ? paletteDecoder(palette) : null;
  // The palette is the model's, the same for every part-id render: one identical line, deduplicated by the report.
  if (decoder) for (const warning of decoder.warnings) warnings.push(`part-id palette: ${warning}`);
  // How far back the near arm is actually drawn in a turned view (qa/capture/temple-reach.ts): a cut is never silent.
  let templeReach: TempleReach | null = null;
  const near = decoder && state.pose ? nearSideOfYaw(state.pose.yaw) : null;
  const centrelines = near !== null ? instance.templeCentrelines() : null;
  if (decoder && near !== null && centrelines && centrelines.sides[near].length) {
    const projected = instance.projectAssetPoints(centrelines.sides[near]);
    if (projected) {
      const sideName = wearerSide(near);
      const armLabels = new Set(palette.flatMap((entry, index) => entry.class !== 'lens' && entry.side === sideName ? [index] : []));
      templeReach = measureTempleReach({side: near, yawDegrees: state.pose!.yaw, centreline: centrelines.sides[near], projected,
        labels: decodeLabels(pixels.data, palette, decoder), width: pixels.width, height: pixels.height, armLabels,
        returnStartZM: centrelines.returnStartZM, capZM: centrelines.capZM[near]});
      const warning = templeReachWarning(request.file, templeReach);
      if (warning) warnings.push(warning);
    }
  }
  if (request.crops) {
    const near = nearSide(Object.fromEntries(state.sides.filter(side => side.rect).map(side => [side.side, side.area])));
    for (const side of state.sides) {
      if (!side.rect) {warnings.push(`${request.file}: no ${side.side} junction crop (${side.reason})`); continue;}
      const rect = side.rect, file = cropFile(request.file, side.side);
      const emitted = await emitCanvas(file, canvasOf(pixels, rect, request.cropUpscale));
      let metrics: Record<string, unknown> | null = null;
      const span = side.spanX ? {x0: side.spanX[0] - rect.x, x1: side.spanX[1] - rect.x} : null;
      if (span && decoder) {
        const labels = decodeLabels(cropPixels(pixels, rect), palette, decoder);
        // Per-colour tolerances (id_palette[].decode_tolerance); the summary here is the smallest one and the background's.
        const decals = new Set((state.geometry?.lens_decals ?? []).map(decal => decal.name));
        metrics = {...junctionColumns(labels, rect.width, rect.height, palette, span, side.walkPx ?? 1, decals), decode_tolerance_min: decoder.tolerance,
          decode_tolerance_background: decoder.backgroundTolerance};
      } else if (span && side.topY !== null && request.variant === 'beauty') {
        metrics = bandMetrics(cropPixels(pixels, rect), rect.width, rect.height, side.topY - rect.y, span) as unknown as Record<string, unknown> | null;
      }
      images.push({...emitted, kind: 'junction_crop', crop: {...rect, upscale: request.cropUpscale}, side: side.side, near: side.side === near, metrics});
    }
  }
  return {timing: {guarded: timing.guarded, passes: timing.passes, safeFallback: timing.safeFallback, shadowsApplied: timing.shadowsApplied ?? null,
    visible_head: timing.visibleHead ?? false, hair_applied: timing.hairApplied,
    override: timing.override ?? null, templeEndState: timing.templeEndState},
  palette: decoder ? palette.map((entry, index) => ({...entry, decode_tolerance: decoder.tolerances[index]})) : null, images, warnings,
  temple_reach: templeReach};
}

/** `--cameras --no-face` (capture/cameras.ts): pose one explicit camera, without a face. The camera frame is the camera's
 *  flat background at its own size (what the lenses transmit); TryOnRenderer.poseCamera does the rest. */
function poseCamera(request: {camera: ExplicitCameraSpec}): Record<string, unknown> {
  const instance = instanceOf(), camera = request.camera;
  instance.setLighting(camera.lighting ?? null);
  state.source.width = camera.width; state.source.height = camera.height;
  const context = state.source.getContext('2d')!;
  context.fillStyle = camera.background; context.fillRect(0, 0, camera.width, camera.height);
  state.background = null; state.pose = null; state.detection = null; state.sides = [];
  const assetToCamera = assetToCameraScene(camera), projection = projectionMatrix(camera);
  instance.poseCamera(state.source, {assetToCamera, projection, nearCm: camera.near * METRES_TO_SCENE, farCm: camera.far * METRES_TO_SCENE,
    ...(camera.temple_pose !== undefined ? {temple_pose: camera.temple_pose} : {})});
  return {id: camera.id, explicit: instance.explicitCameraPosed, background: camera.background, render_size: instance.renderSize,
    temple_pose: instance.photoTemplePoseReport,
    lighting: camera.lighting ?? 'unmodified runtime default',
    intrinsics_px: {fx: camera.fx, fy: camera.fy, cx: camera.cx, cy: camera.cy}, near_m: camera.near, far_m: camera.far,
    camera: instance.cameraState(), asset_to_world: instance.assetMatrixWorld(), fitting: instance.fitting,
    ...(camera.meta !== undefined ? {meta: camera.meta} : {})};
}

/** One render of the explicit camera: the beauty image (the try-on's draw, no hair, no face shadows, no guard: there is no
 *  face), part IDs on black, or scalar white-on-black silhouette coverage. */
async function renderCamera(request: {variant: CameraVariant; file: string}): Promise<Record<string, unknown>> {
  const instance = instanceOf();
  if (!instance.explicitCameraPosed) throw new Error('renderCamera: no explicit camera is posed.');
  const warnings: string[] = [];
  const timing: FrameTimings = request.variant !== 'beauty' ? instance.render(null, {override: request.variant})
    : instance.render(null, {hair: false, shadows: false, guard: false});
  const pixels = instance.readback();
  if (pixels.width !== state.source.width || pixels.height !== state.source.height) {
    warnings.push(`${request.file}: read back ${pixels.width}x${pixels.height}, not the requested ${state.source.width}x${state.source.height}`);
  }
  if (!instance.fitting.ready) warnings.push(`${request.file}: the model is not revealed, so the glasses are not drawn`);
  const full = await emitCanvas(request.file, canvasOf(pixels, {x: 0, y: 0, width: pixels.width, height: pixels.height}));
  const palette: PaletteEntry[] = timing.palette ?? [];
  const decoder = request.variant === 'part-id' ? paletteDecoder(palette) : null;
  if (decoder) for (const warning of decoder.warnings) warnings.push(`part-id palette: ${warning}`);
  return {timing: {guarded: timing.guarded, passes: timing.passes, safeFallback: timing.safeFallback, shadowsApplied: timing.shadowsApplied ?? null,
    override: timing.override ?? null, templeEndState: timing.templeEndState},
  palette: decoder ? palette.map((entry, index) => ({...entry, decode_tolerance: decoder.tolerances[index]})) : null,
  images: [{...full, kind: 'camera', crop: null, side: null}], warnings};
}

/** The posed renderer's final model report (render-time fallbacks included), then dispose it. */
function finishPosed(): Record<string, unknown> {
  const instance = state.instance;
  if (!instance) return {report: null, fitting: null};
  const result = {report: instance.report(), fitting: instance.fitting, temples: instance.temples(), clip_m: instance.templeClipZM};
  disposePosed();
  return result;
}

const api = {
  gpu, init, geometry, settle, pose, render, finishPosed, poseCamera, renderCamera,
  portraitPrepare: (fixtureSha256: string) => portrait.prepare(emitCanvas, fixtureSha256),
  portraitCreate: (request: PortraitRequest) => portrait.create(request),
  portraitSettle: () => portrait.settle(),
  portraitRender: (condition: 'a' | 'b') => portrait.render(condition, emitCanvas),
  portraitFinish: () => portrait.finish(),
  dispose: () => {disposePosed(); portrait.dispose();},
};
window.arview = api;
