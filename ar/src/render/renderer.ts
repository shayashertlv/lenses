/** The try-on renderer: bridge pose, nasal shape, face and head occluders, temple clip and blend, side-depth
 *  visibility, pose-driven rear drop, and hair occlusion applied inside the eyewear fragment shaders, drawn straight
 *  into the visible canvas. No pixel leaves the GPU on a live frame.
 *
 *  Guard (default on): the protection geometry (the optical and nasal rectangles and the arm corridors) is computed
 *  per frame; the stencil buffer marks the editable region (arm corridors minus protected rectangles), and the frame
 *  is drawn in two stencil-limited passes: everything outside the editable region from the unblended render, the
 *  editable region with the rear drop and the hair blend. No shader can write a hair-blended fragment into a
 *  protected pixel. When the protection cannot be established the frame is drawn without drop or hair.
 *
 *  Continuity cut (default on): the pinned arm centrelines are projected per pose and walked over the hair mask; from
 *  an arm's first consistent hair run the arm is removed to its tip.
 *
 *  `readback()` exists for the audit only. */
import {
  ACESFilmicToneMapping, BufferAttribute, BufferGeometry, CanvasTexture, Color, DataTexture, DirectionalLight,
  DoubleSide, DynamicDrawUsage, EqualStencilFunc, Group, KeepStencilOp, LinearFilter, Material, Mesh, NearestFilter,
  MeshBasicMaterial, MeshPhysicalMaterial, NoColorSpace, Object3D, PerspectiveCamera, PMREMGenerator, RedFormat, Scene,
  SphereGeometry, SRGBColorSpace, Texture, UnsignedByteType, Vector3, WebGLRenderer,
} from 'three';
import type {WebGLRenderTarget} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import type {GLTF} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import type {Detection} from '../face/protocol.ts';
import type {CategoryMask} from '../hair/protocol.ts';
import {FaceSurface} from './face-surface.ts';
import {correctedBridgePose} from './bridge-pose.ts';
import {createNasalShape} from './nasal-shape.ts';
import {VIRTUAL_CAMERA} from './projection.ts';
import {DEFAULT_EYEWEAR_ID, eyewearById, GLASSES_METERS_TO_CENTIMETERS} from '../eyewear/catalog.ts';
import type {EyewearDefinition} from '../eyewear/catalog.ts';
import {createTempleClip, createTempleBlendConfiguration} from './temple-clip.ts';
import type {TempleClipConfiguration} from './temple-clip.ts';
import {createTempleVisibility, createTempleVisibilityConfiguration, DEFAULT_TEMPLE_VISIBILITY_MODE,
  TEMPLE_VISIBILITY_PARAMETERS, validateReliefBand} from './temple-visibility.ts';
import type {TempleVisibilityMode} from './temple-visibility.ts';
import {createRearDrop, rearDropForPose, REAR_DROP_METHOD, validateRearDrop} from './rear-drop.ts';
import type {RearDropConfiguration} from './rear-drop.ts';
import {createProtection, nasalRoi} from './protection.ts';
import type {PixelRect, ProtectionConfiguration} from './protection.ts';
import {continuityCut, DEFAULT_CONTINUITY_RUN_PX, loadTempleContinuityModel, projectTempleContinuity} from './continuity.ts';
import type {ContinuityCut, ProjectedTemplePath, TempleContinuityModel} from './continuity.ts';
import {createHairOcclusion, DEFAULT_HAIR_START_Z_M} from './hair-occlusion.ts';
import {PixelReader} from './pixel-reader.ts';
import {PoseStabilizer, poseAngles} from './pose-stabilizer.ts';
import type {PoseSample, SteadyOptions} from './pose-stabilizer.ts';
import {
  armSpreadM, FaceWidthEstimator, MAX_ARM_SPREAD_M, SPREAD_PIVOT_RANGE_M, totalArmSpreadM, WIDTH_FIT_METHOD,
} from './face-width.ts';
import type {WidthFitState} from './face-width.ts';
import {maskUvMatrix} from '../hair/mask-reuse.ts';
import type {MaskWarp} from '../hair/mask-reuse.ts';
import {assetPath} from '../assets.ts';

export const GUARD_METHOD = 'gpu-stencil-protection-v1';
export {HAIR_OCCLUSION_METHOD, DEFAULT_HAIR_START_Z_M} from './hair-occlusion.ts';
export {DEFAULT_CONTINUITY_RUN_PX} from './continuity.ts';
/** The render never exceeds this width; the camera frame's aspect is kept. */
export const MAX_RENDER_WIDTH = 1280;
const RESIDUAL_LANDMARKS = [1, 4, 6, 33, 133, 168, 197, 263, 362] as const;
/** The rear head occluder, in centimetres of the tracked face pose (the same frame as the canonical face mesh). Its
 *  half-width is the one the experimental width fit personalizes.
 *
 *  It was (6.3, 8, 5) at z -2.5 until 2026-09-18 — 1.4 cm NARROWER than the canonical face mesh's own silhouette
 *  (|x| 7.74 cm at the temple) and ending 5 cm short of a skull. The face mesh itself stops at z -2.44, so behind that
 *  the head's flanks and the whole ear had no occluder at all, and an arm there could only end by being cut in mesh
 *  space. This ellipsoid is a head: it holds the canonical temple (f 1.22) and tragus (f 1.20) outside itself, so it
 *  never reaches past the real silhouette, keeps the straight temple shaft outside it as far back as the ear, and
 *  contains the ear hook (f 0.69-0.81). Its front face sits at z +3.99, well behind the frame at +6.53. A visual
 *  choice fitted to the canonical mesh, not a measured skull. */
const HEAD_PROXY_SCALE_CM = Object.freeze([7.4, 9.5, 7.5] as const);
const HEAD_PROXY_CENTER_CM = Object.freeze([0, -0.5, -3.5] as const);

export interface RendererOptions {
  /** Mesh-local metres behind which temple fragments may blend toward the camera under hair (default −0.02). */
  hairStartZ?: number;
  /** Gate each frame on the previous frame's GPU completion (fence), so submission cannot run ahead of the GPU (default on). */
  sync?: boolean;
  /** Stencil protection of the optical/nasal rectangles (default on). */
  guard?: boolean;
  /** Continuity cut from an arm's first consistent hair run to its tip (default on). */
  continuity?: boolean;
  /** Minimum hair run along the arm, in source pixels, that counts as a patch (default 10). */
  continuityRunPx?: number;
  /** Smooth the eyewear orientation and depth over time before the bridge pin (null or absent: off; the page passes the
   *  default settings unless `?steady=0`; see pose-stabilizer.ts). */
  steady?: SteadyOptions | null;
  /** Experimental relative face-width fit (`?fit=width`, default off): personalizes the head occluder's width and the
   *  posterior arm spread from a stable width ratio. Switchable during a session (see `setWidthFit`). */
  widthFit?: boolean;
  /** Which rule gives up part of an arm to the head (`?temples=`): `depth` (v4, the default) decides per pixel from the
   *  head's own depth; `angles` is the former v3 rule, two per-side percentages computed from the head's angles. */
  temples?: TempleVisibilityMode;
  /** v4's band in centimetres behind the head surface (`?templekeep=`, `?templedrop=`). */
  templeKeepCm?: number;
  templeDropCm?: number;
  /** `?templebend=` in METRES: splay each arm outward from its hinge by this much at the tip. Adds to whatever the
   *  width fit applies; the pair is capped at MAX_ARM_SPREAD_M. */
  templeBendM?: number;
  /** `?templepivot=` in METRES: how far behind the asset's own hinge the bend pivots. 0 pivots at the hinge, where
   *  the frame front ends; larger values move the bending point back along the shaft. */
  templePivotM?: number;
}
export interface RenderVariant {hair: boolean; drop: boolean; eyewear: boolean; guard: boolean;}
export interface FrameTimings {
  maskUploadMs: number; continuityMs: number; submitMs: number;
  dropM: number; hairApplied: boolean; maskWidth: number; maskHeight: number; sync: boolean;
  guarded: boolean; passes: number; protectedRects: number; editableRects: number; safeFallback: boolean;
  continuity: boolean; cutNegativeZ: number | null; cutPositiveZ: number | null;
  /** The experimental width fit on this frame: the selected mode, its state, the applied ratio (exactly 1 when nothing
   *  is applied) and the lateral spread of one arm in metres. */
  widthFit: boolean; widthFitState: WidthFitState; widthRatio: number; armSpreadM: number;
  /** Which temple-visibility rule drew this frame, and what the angle rule would have given up on each side. */
  templeMode: TempleVisibilityMode; templeNegativeXWeight: number; templePositiveXWeight: number; templeFrontalWeight: number;
  templeKeepCm: number; templeDropCm: number;
  /** The manual outward bend at the arm tips, how far behind the hinge it pivots, the plane it actually pivots about,
   *  and the total lateral spread the drawn arms carry (bend plus fit). */
  templeBendM: number; templePivotM: number; armSpreadStartZM: number | null; armSpreadTotalM: number;
}
/** What the page's debug line and the audit record about the width fit. */
export interface WidthFitReport {
  method: typeof WIDTH_FIT_METHOD; mode: 'original' | 'width'; state: WidthFitState;
  /** The ratio in the geometry now; exactly 1 while collecting, after a fallback and in Original. */
  ratio: number;
  /** The median of the collected observations, which may differ from the applied ratio while it eases. */
  observedRatio: number | null;
  armSpreadM: number; samples: number; accepted: number; lastRejection: string | null;
  /** The last accepted observation's per-region ratios, in WIDTH_REGIONS order: the face's own lateral profile, which
   *  the single ratio only summarizes. Numbers only. */
  regionRatios: readonly number[] | null;
}
interface CanonicalFace {positions: number[]; indices: number[];}
export interface CaptureGeometry {
  eyewearModelId: string; rawMatrix: number[]; eyewearMatrix: number[]; yawDegrees: number;
  rearDrop: RearDropConfiguration | null; templeClip: TempleClipConfiguration | null; hairStartZ: number;
  protection: ProtectionConfiguration | null; noseRoi: PixelRect | null;
}

function abortError(): DOMException {return new DOMException('Renderer startup was cancelled.', 'AbortError');}
function validateFace(value: unknown): CanonicalFace {
  if (typeof value !== 'object' || value === null) throw new Error('The canonical face is missing.');
  const face = value as Partial<CanonicalFace>;
  if (!Array.isArray(face.positions) || face.positions.length !== 468 * 3 || !face.positions.every(Number.isFinite)
      || !Array.isArray(face.indices) || face.indices.length === 0 || face.indices.length % 3 !== 0
      || !face.indices.every(index => Number.isInteger(index) && index >= 0 && index < 468)) throw new Error('The canonical face contains invalid mesh data.');
  return face as CanonicalFace;
}
async function fetchAsset(url: string, signal: AbortSignal): Promise<Response> {
  const response = await fetch(url, {signal});
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status}).`);
  return response;
}
function disposeObjects(roots: Object3D[]): void {
  const geometries = new Set<BufferGeometry>(), materials = new Set<Material>(), textures = new Set<Texture>(), bitmaps = new Set<ImageBitmap>();
  for (const root of roots) root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
    }
  });
  for (const texture of textures) {const image = texture.image; if (typeof ImageBitmap !== 'undefined' && image instanceof ImageBitmap) bitmaps.add(image); texture.dispose();}
  for (const bitmap of bitmaps) bitmap.close();
  for (const material of materials) material.dispose();
  for (const geometry of geometries) geometry.dispose();
}

const GPU_WAIT_LIMIT_MS = 1000, GPU_WAIT_TIMEOUTS_LIMIT = 3;

export class TryOnRenderer {
  readonly eyewear: EyewearDefinition;
  private readonly renderer: WebGLRenderer;
  private readonly gl: WebGL2RenderingContext;
  private readonly scene = new Scene();
  private readonly camera = new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees, 1, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
  private readonly facePose = new Group();
  private readonly eyewearPose = new Group();
  private readonly projected = new Vector3();
  private readonly hairStartZ: number;
  private sync: boolean;
  private fenceTimeouts = 0;
  private syncFailure: string | null = null;
  private readonly guard: boolean;
  private readonly continuity: boolean;
  private readonly continuityRunPx: number;
  private continuityModel: TempleContinuityModel | null = null;
  private continuityFailure: string | null = null;
  private templePaths: ProjectedTemplePath[] | null = null;
  private readonly reader = new PixelReader();
  private backgroundTexture: CanvasTexture | null = null;
  private environmentTarget: WebGLRenderTarget | null = null;
  private canonicalPositions: number[] = [];
  private nasalShape: ReturnType<typeof createNasalShape> | null = null;
  private templeClip: ReturnType<typeof createTempleClip> | null = null;
  private templeVisibility: ReturnType<typeof createTempleVisibility> | null = null;
  private rearDrop: ReturnType<typeof createRearDrop> | null = null;
  private hairOcclusion: ReturnType<typeof createHairOcclusion> | null = null;
  private lensMeshes: Mesh[] = [];
  private currentRearDrop: RearDropConfiguration | null = null;
  private protectionConfiguration: ProtectionConfiguration | null = null;
  private nasalRect: PixelRect | null = null;
  private faceSurface: FaceSurface | null = null;
  private surfaceMesh: Mesh | null = null;
  private headProxy: Mesh | null = null;
  private surfaceAttribute: BufferAttribute | null = null;
  private assetScenes: Object3D[] = [];
  private removeAbortListener: (() => void) | null = null;
  private maskTexture: DataTexture | null = null;
  private maskBytes: Uint8Array | null = null;
  private fence: WebGLSync | null = null;
  private posedMatrix: number[] | null = null;
  private frameWidth = 0;
  private frameHeight = 0;
  private disposed = false;
  private residual: number | null = null;
  private yaw: number | null = null;
  private lastPose: {rawMatrix: number[]; eyewearMatrix: number[]; yawDegrees: number} | null = null;
  private readonly stabilizer: PoseStabilizer | null;
  private latestPose: PoseSample | null = null;
  private maskWarp: MaskWarp | null = null;
  private uploadedCategory: Uint8Array | null = null;
  private uploadedHairIndex = -1;
  private faceWidth: FaceWidthEstimator | null = null;
  private widthFitEnabled: boolean;
  private widthRatio = 1;
  private armSpread = 0;
  private templeMode: TempleVisibilityMode;
  // The temple configuration: switchable inside a live session by the page's temple sweep (see setTempleShape).
  private templeKeepCm: number;
  private templeDropCm: number;
  private templeBendM: number;
  private templePivotM: number;

  private constructor(renderer: WebGLRenderer, gl: WebGL2RenderingContext, eyewear: EyewearDefinition, options: RendererOptions) {
    this.renderer = renderer; this.gl = gl; this.eyewear = eyewear;
    this.stabilizer = options.steady ? new PoseStabilizer(options.steady) : null;
    this.widthFitEnabled = options.widthFit === true;
    this.templeMode = options.temples ?? DEFAULT_TEMPLE_VISIBILITY_MODE;
    this.templeKeepCm = options.templeKeepCm ?? TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm;
    this.templeDropCm = options.templeDropCm ?? TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm;
    validateReliefBand(this.templeKeepCm, this.templeDropCm);
    const bend = options.templeBendM ?? 0;
    if (!Number.isFinite(bend) || Math.abs(bend) > MAX_ARM_SPREAD_M) throw new Error('The temple bend is out of range.');
    this.templeBendM = bend;
    const pivot = options.templePivotM ?? 0;
    if (!Number.isFinite(pivot) || pivot < SPREAD_PIVOT_RANGE_M.min || pivot > SPREAD_PIVOT_RANGE_M.max) {
      throw new Error('The temple bend pivot is out of range.');
    }
    this.templePivotM = pivot;
    this.hairStartZ = options.hairStartZ ?? DEFAULT_HAIR_START_Z_M; this.sync = options.sync ?? true; this.guard = options.guard ?? true;
    this.continuity = options.continuity ?? true;
    this.continuityRunPx = Math.max(1, options.continuityRunPx ?? DEFAULT_CONTINUITY_RUN_PX);
    this.scene.background = new Color(0x080b10);
    this.facePose.name = 'Tracked canonical face (centimeters)'; this.facePose.matrixAutoUpdate = false; this.facePose.visible = false; this.scene.add(this.facePose);
    this.eyewearPose.name = 'Eyewear bridge pose (centimeters)'; this.eyewearPose.matrixAutoUpdate = false; this.eyewearPose.visible = false; this.scene.add(this.eyewearPose);
  }
  get projectionResidualPx(): number | null {return this.residual;}
  get yawDegrees(): number | null {return this.yaw;}
  get guardEnabled(): boolean {return this.guard;}
  get nativeSamples(): number {const samples: unknown = this.gl.getParameter(this.gl.SAMPLES); return typeof samples === 'number' ? samples : 0;}
  get gpuRenderer(): string | null {
    const debug = this.gl.getExtension('WEBGL_debug_renderer_info');
    const value: unknown = this.gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : this.gl.RENDERER);
    return typeof value === 'string' ? value : null;
  }
  get templeClipConfiguration(): TempleClipConfiguration {return createTempleBlendConfiguration(this.eyewear.templeClipLocalZM);}
  /** The protection for the posed frame (render pixels), or null when it could not be established. */
  get protection(): ProtectionConfiguration | null {return this.protectionConfiguration ? structuredClone(this.protectionConfiguration) : null;}
  get noseRoi(): PixelRect | null {return this.nasalRect ? {...this.nasalRect} : null;}
  /** The projected arm centrelines for the posed frame (null without the pinned continuity geometry). */
  get paths(): ProjectedTemplePath[] | null {return this.templePaths;}
  get continuityUnavailable(): string | null {return this.continuityFailure;}
  /** How the next `render` reads its mask: null for the frame's own mask, or a mask reused from an earlier frame placed
   *  by the head's motion (hair/mask-reuse.ts). It applies to the shader lookup and the continuity cut alike, and stays
   *  until changed, so an audit's variants of the held frame read the mask the way the live frame did. */
  setMaskWarp(warp: MaskWarp | null): void {this.maskWarp = warp;}
  /** The posed frame's raw and steadied orientation and depth (numbers only), or null when no face is posed. */
  get poseSample(): PoseSample | null {return this.latestPose ? {...this.latestPose} : null;}
  /** The experimental width fit as it stands now (numbers and states only). */
  get widthFit(): WidthFitReport {
    return {method: WIDTH_FIT_METHOD, mode: this.widthFitEnabled ? 'width' : 'original',
      state: this.widthFitEnabled && this.faceWidth ? this.faceWidth.state : 'off',
      ratio: this.widthRatio, observedRatio: this.widthFitEnabled ? this.faceWidth?.observedRatio ?? null : null,
      armSpreadM: this.armSpread, samples: this.widthFitEnabled ? this.faceWidth?.sampleCount ?? 0 : 0,
      accepted: this.widthFitEnabled ? this.faceWidth?.accepted ?? 0 : 0,
      lastRejection: this.widthFitEnabled ? this.faceWidth?.lastRejection ?? null : null,
      regionRatios: this.widthFitEnabled ? this.faceWidth?.lastRegionRatios ?? null : null};
  }
  /** Which temple-visibility rule is running, and the angle rule's weights for the posed frame (numbers only). */
  get templeVisibilityState(): {mode: TempleVisibilityMode; negativeXWeight: number; positiveXWeight: number; frontalWeight: number; keepCm: number; dropCm: number} {
    const c = this.templeVisibility?.configuration ?? null;
    return {mode: this.templeMode, negativeXWeight: c?.negativeXWeight ?? 0, positiveXWeight: c?.positiveXWeight ?? 0,
      frontalWeight: c?.frontalOcclusionWeight ?? 0, keepCm: this.templeKeepCm, dropCm: this.templeDropCm};
  }
  /** Switch the temple-visibility rule inside a live session; it takes effect on the next posed frame. */
  setTempleMode(mode: TempleVisibilityMode): void {if (!this.disposed) this.templeMode = mode;}
  /** Apply a whole temple configuration inside a live session: how far the arms bend out, where that bend pivots, and
   *  the band that decides how much of an arm is given up to the head. The set is validated before anything is
   *  mutated, so a refused value leaves the running configuration exactly as it was, and the arm geometry, the
   *  projected centrelines and the protection corridor all move together as they do on a cold start. */
  setTempleShape(shape: {bendM: number; pivotM: number; keepCm: number; dropCm: number}): void {
    if (this.disposed) return;
    const {bendM, pivotM, keepCm, dropCm} = shape;
    if (!Number.isFinite(bendM) || Math.abs(bendM) > MAX_ARM_SPREAD_M) throw new Error('The temple bend is out of range.');
    if (!Number.isFinite(pivotM) || pivotM < SPREAD_PIVOT_RANGE_M.min || pivotM > SPREAD_PIVOT_RANGE_M.max) {
      throw new Error('The temple bend pivot is out of range.');
    }
    validateReliefBand(keepCm, dropCm);
    this.templeKeepCm = keepCm; this.templeDropCm = dropCm;
    if (pivotM !== this.templePivotM) {this.templePivotM = pivotM; this.rearDrop?.setSpreadPivot(pivotM);}
    if (bendM !== this.templeBendM) {
      this.templeBendM = bendM; this.setWidthRatio(this.widthRatio); this.rearDrop?.setSpread(this.armSpread);
    }
    if (this.templePaths && this.lastPose && this.continuityModel) {
      this.templePaths = this.projectPaths(this.lastPose.eyewearMatrix, this.currentRearDrop?.dropM ?? 0);
    }
  }
  /** The temple configuration now in the geometry (metres and centimetres), for the page's debug line. */
  get templeShape(): {bendM: number; pivotM: number; keepCm: number; dropCm: number} {
    return {bendM: this.templeBendM, pivotM: this.templePivotM, keepCm: this.templeKeepCm, dropCm: this.templeDropCm};
  }
  /** Switch the width fit within a live session. Only one of the two runs at a time: turning it off restores the
   *  original geometry immediately and drops every collected observation, so nothing of the fit is left behind. */
  setWidthFit(enabled: boolean): void {
    if (this.disposed || enabled === this.widthFitEnabled) return;
    this.widthFitEnabled = enabled;
    this.faceWidth?.reset();
    this.setWidthRatio(1);
    this.rearDrop?.setSpread(this.armSpread);
    if (this.templePaths && this.lastPose && this.continuityModel) this.templePaths = this.projectPaths(this.lastPose.eyewearMatrix, this.currentRearDrop?.dropM ?? 0);
  }
  /** The fitted ratio in the geometry: the head occluder's half-width, and the lateral spread the next shape change
   *  writes — the fit's contribution and the manual bend together, so turning the fit off leaves the bend standing. */
  private setWidthRatio(ratio: number): void {
    this.widthRatio = ratio; this.armSpread = totalArmSpreadM(armSpreadM(ratio), this.templeBendM);
    if (this.headProxy) this.headProxy.scale.x = HEAD_PROXY_SCALE_CM[0] * ratio;
  }
  /** The arm centrelines for a pose, moved by the same drop and the same lateral spread as the drawn arms. */
  private projectPaths(eyewearMatrix: readonly number[], dropM: number): ProjectedTemplePath[] | null {
    if (!this.continuityModel) return null;
    const {width, height} = this.renderSize;
    return projectTempleContinuity(this.continuityModel, {eyewearMatrix, offsetCm: this.eyewear.offsetCm,
      sourceAspect: this.frameWidth / this.frameHeight, width, height, dropM,
      // Read back off the geometry rather than from the option, so the centrelines cannot be projected along a
      // differently bent arm than the one that was drawn.
      spreadM: this.armSpread, spreadStartZM: this.rearDrop?.spreadStartZM});
  }
  /** Set once the completion gate was switched off because the previous frame's fence never signalled. */
  get syncUnavailable(): string | null {return this.syncFailure;}
  get renderSize(): {width: number; height: number} {
    const width = Math.min(this.frameWidth, MAX_RENDER_WIDTH);
    return {width, height: Math.max(1, Math.round(width * this.frameHeight / Math.max(1, this.frameWidth)))};
  }
  get captureSnapshot(): CaptureGeometry | null {
    const pose = this.lastPose; if (!pose || this.disposed) return null;
    return {eyewearModelId: this.eyewear.id, rawMatrix: pose.rawMatrix.slice(), eyewearMatrix: pose.eyewearMatrix.slice(), yawDegrees: pose.yawDegrees,
      rearDrop: this.currentRearDrop ? {...this.currentRearDrop} : null, templeClip: this.templeClip?.configuration ?? null, hairStartZ: this.hairStartZ,
      protection: this.protection, noseRoi: this.noseRoi};
  }

  static async create(canvas: HTMLCanvasElement, signal: AbortSignal, eyewearId: string = DEFAULT_EYEWEAR_ID, options: RendererOptions = {}): Promise<TryOnRenderer> {
    if (signal.aborted) throw abortError();
    const eyewear = eyewearById(eyewearId);
    const loading = new AbortController();
    let instance: TryOnRenderer | null = null, gltf: GLTF | null = null, webgl: WebGLRenderer | null = null, context: WebGL2RenderingContext | null = null;
    const onAbort = () => {loading.abort(); instance?.dispose();};
    signal.addEventListener('abort', onAbort, {once: true});
    try {
      const [glasses, face] = await Promise.all([
        fetchAsset(eyewear.assetUrl, loading.signal).then(response => response.arrayBuffer()),
        fetchAsset(assetPath('models/canonical-face.json'), loading.signal).then(response => response.json()).then(validateFace),
      ]);
      if (signal.aborted) throw abortError();
      gltf = await new GLTFLoader().parseAsync(glasses, assetPath('models/'));
      if (signal.aborted) throw abortError();
      // The guard needs a stencil buffer on the default framebuffer. The WebGLRenderer itself is created without one:
      // with `stencil: true` Three also gives its lens-transmission render target a stencil buffer (cleared to 0), and
      // the opaque scene then fails the EQUAL-1 test inside that target during the protected pass.
      context = canvas.getContext('webgl2', {alpha: false, antialias: true, stencil: true, powerPreference: 'high-performance'});
      if (!context) throw new Error('WebGL 2 is unavailable on this browser.');
      webgl = new WebGLRenderer({canvas, context, alpha: false, antialias: true, stencil: false});
      instance = new TryOnRenderer(webgl, context, eyewear, options);
      instance.removeAbortListener = () => signal.removeEventListener('abort', onAbort);
      instance.assetScenes = gltf.scenes;
      const eyewearScene = gltf.scene; gltf = null;
      instance.configure(face, eyewearScene);
      if (signal.aborted) throw abortError();
      // The pinned arm centrelines for the continuity cut; unavailable means no cut.
      try {instance.continuityModel = await loadTempleContinuityModel(eyewear.assetUrl, eyewear.templeClipLocalZM, loading.signal);}
      catch (error) {if (signal.aborted) throw abortError(); instance.continuityFailure = error instanceof Error ? error.message : String(error);}
      if (signal.aborted) throw abortError();
      return instance;
    } catch (error) {
      loading.abort(); signal.removeEventListener('abort', onAbort);
      if (instance) instance.dispose(); else {webgl?.dispose(); context?.getExtension('WEBGL_lose_context')?.loseContext();}
      if (gltf) disposeObjects(gltf.scenes);
      throw signal.aborted ? abortError() : error;
    }
  }

  private configure(face: CanonicalFace, eyewearScene: Group): void {
    this.renderer.setPixelRatio(1); this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1;
    this.canonicalPositions = face.positions; this.nasalShape = createNasalShape(face.positions, face.indices);
    // The width fit compares the observed face with this same canonical mesh; an unusable one leaves the fit off.
    try {this.faceWidth = new FaceWidthEstimator(face.positions);} catch {this.faceWidth = null;}
    const asset = new Group(); asset.name = `${this.eyewear.name} bridge attachment`;
    asset.scale.setScalar(GLASSES_METERS_TO_CENTIMETERS); asset.position.set(...this.eyewear.offsetCm);
    eyewearScene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        if (material instanceof MeshPhysicalMaterial && material.transmission > 0) material.toneMapped = false;
      }
    });
    asset.add(eyewearScene); this.eyewearPose.add(asset);
    eyewearScene.traverse(object => {
      if (object instanceof Mesh && (Array.isArray(object.material) ? object.material : [object.material]).some(material => material instanceof MeshPhysicalMaterial && material.transmission > 0)) this.lensMeshes.push(object);
    });
    this.templeClip = createTempleClip(eyewearScene);
    this.rearDrop = createRearDrop(eyewearScene, this.eyewear.templeClipLocalZM, this.templePivotM);
    const occlusionMaterial = new MeshBasicMaterial({colorWrite: false, depthWrite: true, depthTest: true, side: DoubleSide});
    this.faceSurface = new FaceSurface(face.positions);
    const geometry = new BufferGeometry();
    this.surfaceAttribute = new BufferAttribute(this.faceSurface.positions, 3).setUsage(DynamicDrawUsage);
    geometry.setAttribute('position', this.surfaceAttribute); geometry.setIndex(face.indices);
    const surface = new Mesh(geometry, occlusionMaterial); surface.name = 'Observed face depth in camera space';
    surface.renderOrder = -2; surface.frustumCulled = false; surface.visible = false; this.surfaceMesh = surface; this.scene.add(surface);
    const head = new Mesh(new SphereGeometry(1, 24, 16), occlusionMaterial); head.name = 'Conservative rear head depth only';
    head.scale.set(...HEAD_PROXY_SCALE_CM); head.position.set(...HEAD_PROXY_CENTER_CM); head.renderOrder = -2; this.headProxy = head; this.facePose.add(head);
    this.setWidthRatio(this.widthRatio);
    this.templeVisibility = createTempleVisibility(eyewearScene, {renderer: this.renderer, scene: this.scene, camera: this.camera, eyewearPose: this.eyewearPose});
    // Installed last so it wraps the clip and visibility hooks; its blend commutes with the clip's terminal blend.
    this.hairOcclusion = createHairOcclusion(eyewearScene);
    const key = new DirectionalLight(0xffffff, 2); key.position.set(-10, 15, 20); this.scene.add(key);
    const environment = new RoomEnvironment(); let generator: PMREMGenerator | null = null;
    try {generator = new PMREMGenerator(this.renderer); this.environmentTarget = generator.fromScene(environment, 0.04); this.scene.environment = this.environmentTarget.texture; this.scene.environmentIntensity = 0.8;}
    finally {generator?.dispose(); environment.dispose();}
  }

  /** Waits (yielding) until the previous frame's GPU work completed, so submission cannot run ahead of completion. A
   *  fence that has not signalled after GPU_WAIT_LIMIT_MS ends the wait (the frame is late, not lost); after
   *  GPU_WAIT_TIMEOUTS_LIMIT such waits in a row the gate is switched off for the session, since a driver whose polled
   *  fences never resolve would otherwise stall every frame. */
  async waitForPreviousFrame(): Promise<{gpuWaitMs: number; polls: number; timedOut: boolean}> {
    const fence = this.fence; this.fence = null;
    if (!fence || this.disposed) return {gpuWaitMs: 0, polls: 0, timedOut: false};
    const started = performance.now(); let polls = 0, timedOut = false;
    try {
      for (;;) {
        const status = this.gl.clientWaitSync(fence, 0, 0); polls++;
        if (status === this.gl.ALREADY_SIGNALED || status === this.gl.CONDITION_SATISFIED || status === this.gl.WAIT_FAILED || this.disposed) break;
        if (performance.now() - started > GPU_WAIT_LIMIT_MS) {timedOut = true; break;}
        await new Promise(resolve => setTimeout(resolve, 1));
      }
    } finally {if (!this.disposed) this.gl.deleteSync(fence);}
    if (!timedOut) this.fenceTimeouts = 0;
    else if (++this.fenceTimeouts >= GPU_WAIT_TIMEOUTS_LIMIT && this.sync) {
      this.sync = false; this.syncFailure = `the previous frame's GPU fence did not signal within ${GPU_WAIT_LIMIT_MS} ms ${this.fenceTimeouts} times in a row`;
    }
    return {gpuWaitMs: performance.now() - started, polls, timedOut};
  }

  /** Pose this detection over the frame (no drawing) and establish the protection for it. Returns whether a face is tracked.
   *  `timestampMs` is the frame's capture time; only the optional pose steadiness reads it. */
  pose(frame: HTMLCanvasElement, detection: Detection, timestampMs: number = performance.now()): boolean {
    if (this.disposed) return false;
    this.clearPresentation(false);
    try {
      if (frame.width <= 0 || frame.height <= 0) throw new Error('The camera frame is empty.');
      if (this.frameWidth !== frame.width || this.frameHeight !== frame.height) {
        this.frameWidth = frame.width; this.frameHeight = frame.height;
        const {width, height} = this.renderSize;
        this.renderer.setSize(width, height, false);
        this.camera.aspect = frame.width / frame.height; this.camera.updateProjectionMatrix();
        this.backgroundTexture?.dispose(); this.backgroundTexture = null;
      }
      if (!this.backgroundTexture) {
        this.backgroundTexture = new CanvasTexture(frame); this.backgroundTexture.colorSpace = SRGBColorSpace;
        this.backgroundTexture.generateMipmaps = false; this.backgroundTexture.minFilter = LinearFilter; this.backgroundTexture.magFilter = LinearFilter;
        this.scene.background = this.backgroundTexture;
      }
      this.backgroundTexture.image = frame; this.backgroundTexture.needsUpdate = true;
      const matrix = detection.matrix;
      this.facePose.visible = matrix !== null && matrix.length === 16 && matrix.every(Number.isFinite) && detection.landmarks.length >= 468;
      if (this.facePose.visible && matrix) {
        const surfaceValid = this.faceSurface?.reconstruct(detection.landmarks, matrix, this.camera.aspect) ?? false;
        this.facePose.visible = surfaceValid;
        if (surfaceValid) {
          if (!this.nasalShape) throw new Error('The nasal shape is not initialized.');
          // The width fit observes the reconstructed surface on this frame's raw detector pose, before the nasal shape
          // changes it, and only near-frontal observations are collected (face-width.ts).
          if (this.widthFitEnabled && this.faceWidth) {
            this.faceWidth.observe(this.faceSurface!.positions, matrix, timestampMs);
            this.setWidthRatio(this.faceWidth.ratio);
          }
          const shaped = this.nasalShape.apply({surfacePositions: this.faceSurface!.positions, rawMatrix: matrix});
          this.faceSurface!.positions.set(shaped.surfacePositions);
          // The face surface and the nasal shape above stay on the raw detector pose: they are this frame's face. With
          // steadiness on, everything the glasses hang from uses the steadied pose, and the bridge pin then re-anchors
          // its image-plane position to this frame's nose landmarks.
          const steady = this.stabilizer ? this.stabilizer.apply(matrix, timestampMs) : null;
          const poseMatrix = steady ? steady.matrix : matrix;
          const raw = poseAngles(matrix), steadied = steady ? poseAngles(poseMatrix) : null;
          this.latestPose = {...raw, steadyYawDeg: steadied?.yawDeg ?? null, steadyPitchDeg: steadied?.pitchDeg ?? null, steadyRollDeg: steadied?.rollDeg ?? null,
            steadyDepthCm: steadied?.depthCm ?? null, steadyLagDeg: steady?.lagDeg ?? null, steadyRotationCutoffHz: steady?.rotationCutoffHz ?? null,
            steadyDepthCutoffHz: steady?.depthCutoffHz ?? null, steadyReset: steady?.reset ?? null};
          const attachment = correctedBridgePose(poseMatrix, detection.landmarks, this.canonicalPositions, this.camera.aspect);
          this.facePose.matrix.fromArray(attachment.matrix); this.facePose.matrixWorldNeedsUpdate = true;
          this.eyewearPose.matrix.fromArray(attachment.matrix); this.eyewearPose.matrixWorldNeedsUpdate = true;
          const drop: RearDropConfiguration = {method: REAR_DROP_METHOD, dropM: rearDropForPose(poseMatrix)};
          // One pass over the cloned arm buffers for both deformations, so neither overwrites the other.
          validateRearDrop(drop); this.rearDrop?.setShape(drop.dropM, this.armSpread); this.currentRearDrop = {...drop};
          this.templeClip?.set(this.templeClipConfiguration);
          this.templeVisibility?.set(createTempleVisibilityConfiguration(poseMatrix, this.renderer.capabilities?.samples ?? 0, this.templeMode, this.templeKeepCm, this.templeDropCm));
          this.eyewearPose.visible = true; this.camera.updateMatrixWorld();
          this.yaw = attachment.yawDegrees; this.residual = this.measureProjectionResidual(detection);
          if (this.surfaceAttribute) this.surfaceAttribute.needsUpdate = true;
          if (this.surfaceMesh) this.surfaceMesh.visible = true;
          if (this.headProxy) this.headProxy.visible = true;
          this.lastPose = {rawMatrix: matrix.slice(), eyewearMatrix: attachment.matrix.slice(), yawDegrees: attachment.yawDegrees};
          this.posedMatrix = poseMatrix;
          // The protection geometry for this exact pose, drop and frame size (candidate arm bounds follow the drop).
          const {width, height} = this.renderSize, dropShape = this.rearDrop;
          this.protectionConfiguration = dropShape ? createProtection({optical: dropShape.opticalBounds, originalArms: dropShape.originalArmBounds, candidateArms: dropShape.candidateArmBounds},
            attachment.matrix, this.eyewear.offsetCm, detection.landmarks, width, height, frame.width / frame.height) : null;
          this.nasalRect = nasalRoi(detection, width, height);
          this.templePaths = this.projectPaths(attachment.matrix, drop.dropM);
        }
      }
      if (!this.facePose.visible) {
        this.rearDrop?.setDrop(0); this.stabilizer?.reset();
        // A brief tracking failure holds the estimate; a sustained one drops it back to the original geometry.
        if (this.widthFitEnabled && this.faceWidth) {
          this.faceWidth.miss(timestampMs);
          if (this.faceWidth.ratio !== this.widthRatio) {this.setWidthRatio(this.faceWidth.ratio); this.rearDrop?.setSpread(this.armSpread);}
        }
      }
      return this.facePose.visible;
    } catch (error) {this.clearPresentation(); throw error;}
  }

  /** Upload a category mask as a 0/255 red texture (hair = 255). Returns the upload time in ms. */
  private uploadMask(mask: CategoryMask): number {
    const started = performance.now();
    const {width, height} = mask, count = width * height;
    if (mask.category.length !== count) throw new Error('The hair mask size does not match its category data.');
    // A mask reused from an earlier frame is already on the GPU: its category buffer is the same object.
    if (this.maskTexture && this.uploadedCategory === mask.category && this.uploadedHairIndex === mask.hairIndex
      && this.maskTexture.image.width === width && this.maskTexture.image.height === height) return performance.now() - started;
    if (!this.maskTexture || !this.maskBytes || this.maskTexture.image.width !== width || this.maskTexture.image.height !== height) {
      this.maskTexture?.dispose();
      this.maskBytes = new Uint8Array(count);
      this.maskTexture = new DataTexture(this.maskBytes, width, height, RedFormat, UnsignedByteType);
      this.maskTexture.colorSpace = NoColorSpace; this.maskTexture.flipY = false; this.maskTexture.unpackAlignment = 1;
      // Nearest: a mask smaller than the frame is read the way the continuity cut and the CPU reference read it, by the
      // texel under the pixel centre; at the frame's own size this is the texel itself, as before.
      this.maskTexture.generateMipmaps = false; this.maskTexture.minFilter = NearestFilter; this.maskTexture.magFilter = NearestFilter;
    }
    const bytes = this.maskBytes, category = mask.category, hair = mask.hairIndex;
    for (let index = 0; index < count; index++) bytes[index] = category[index] === hair ? 255 : 0;
    this.maskTexture.needsUpdate = true; this.uploadedCategory = category; this.uploadedHairIndex = hair;
    return performance.now() - started;
  }

  private setStencil(enabled: boolean, ref: number): void {
    this.scene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        material.stencilWrite = enabled; material.stencilFunc = EqualStencilFunc; material.stencilRef = ref; material.stencilFuncMask = 0xff;
        material.stencilFail = KeepStencilOp; material.stencilZFail = KeepStencilOp; material.stencilZPass = KeepStencilOp;
      }
    });
  }
  /** Marks the stencil: 1 everywhere, 0 inside the arm corridors, 1 again inside the protected rectangles (protection
   *  wins). Three applies its own scissor state only at render time, so the rectangle clears use the GL scissor
   *  directly and restore it afterwards; the clear values go through Three's state cache. */
  private markGuard(protection: ProtectionConfiguration, nose: PixelRect | null, width: number, height: number): void {
    const gl = this.gl, renderer = this.renderer, stencil = renderer.state.buffers.stencil;
    renderer.setScissorTest(false); stencil.setMask(0xffffffff); stencil.setClear(1);
    gl.disable(gl.SCISSOR_TEST); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT | gl.STENCIL_BUFFER_BIT);
    gl.enable(gl.SCISSOR_TEST);
    const fill = (rects: readonly PixelRect[], value: number): void => {
      stencil.setClear(value);
      for (const rect of rects) {gl.scissor(rect.x0, height - rect.y1, rect.x1 - rect.x0, rect.y1 - rect.y0); gl.clear(gl.STENCIL_BUFFER_BIT);}
    };
    fill(protection.editableRects, 0);
    fill(nose ? [...protection.protectedRects, nose] : protection.protectedRects, 1);
    gl.scissor(0, 0, width, height); gl.disable(gl.SCISSOR_TEST); stencil.setClear(0);
  }

  /** Draw the posed frame into the canvas. Live frames use `{hair, drop: true, eyewear: true, guard}`; the audit draws
   *  other variants of the same posed frame and reads them back. */
  render(mask: CategoryMask | null, variant: Partial<RenderVariant> = {}): FrameTimings {
    if (this.disposed) throw new Error('The renderer is disposed.');
    if (!this.backgroundTexture) throw new Error('Nothing is posed.');
    const v: RenderVariant = {hair: true, drop: true, eyewear: true, guard: this.guard, ...variant};
    const tracked = this.facePose.visible;
    const wantsHair = v.hair && mask !== null && tracked;
    const maskUploadMs = wantsHair ? this.uploadMask(mask) : 0;
    const continuityStart = performance.now();
    const cut: ContinuityCut = wantsHair && this.continuity && this.continuityModel && this.templePaths
      ? continuityCut(this.continuityModel, this.templePaths, mask, this.renderSize, this.continuityRunPx, this.maskWarp) : {negative: null, positive: null};
    this.hairOcclusion?.setCut(cut.negative, cut.positive);
    this.hairOcclusion?.setMaskUv(wantsHair && this.maskWarp ? maskUvMatrix(this.maskWarp) : null);
    const continuityMs = performance.now() - continuityStart;
    const {width, height} = this.renderSize;
    const submitStart = performance.now();
    const protection = this.protectionConfiguration, nose = this.nasalRect;
    const guarded = v.guard && tracked && protection !== null, safeFallback = v.guard && tracked && protection === null;
    const dropM = this.currentRearDrop?.dropM ?? 0;
    this.eyewearPose.visible = v.eyewear && tracked;
    this.templeClip?.prepareRender(this.backgroundTexture, width, height);
    if (tracked && this.posedMatrix && this.templeVisibility?.configuration) this.templeVisibility.prepare(this.posedMatrix, this.backgroundTexture);
    this.hairOcclusion?.prepareRender(wantsHair ? this.maskTexture : null, width, height, this.backgroundTexture);
    this.renderer.setRenderTarget(null);
    let passes = 1;
    // The drop stays as posed in both guarded passes: the editable rectangles are built from the dropped arm bounds, so
    // a dropped fragment cannot land outside them; the audit measures any drop intrusion into protected pixels.
    const applyDrop = v.drop && tracked && !safeFallback;
    if (!applyDrop) this.rearDrop?.setDrop(0);
    try {
      if (guarded) {
        this.renderer.autoClear = false;
        try {
          this.markGuard(protection, nose, width, height);
          // Pass A: protected and non-editable pixels come from the unblended render, background included.
          this.setStencil(true, 1); this.hairOcclusion?.set(false, this.hairStartZ);
          this.renderer.render(this.scene, this.camera);
          // Pass B: the editable region gets the hair blend. No background redraw, and the lenses (always inside the
          // protected optical rectangle) are skipped so no second transmission pre-pass runs.
          this.setStencil(true, 0); this.hairOcclusion?.set(wantsHair, this.hairStartZ);
          this.scene.background = null; for (const lens of this.lensMeshes) lens.visible = false;
          try {this.renderer.render(this.scene, this.camera);}
          finally {this.scene.background = this.backgroundTexture; for (const lens of this.lensMeshes) lens.visible = true;}
          passes = 2;
        } finally {this.renderer.autoClear = true; this.setStencil(false, 0);}
      } else {
        this.setStencil(false, 0);
        this.hairOcclusion?.set(wantsHair && !safeFallback, this.hairStartZ);
        this.renderer.render(this.scene, this.camera);
      }
    } finally {if (!applyDrop && tracked) this.rearDrop?.setDrop(dropM);}
    if (this.sync) {
      if (this.fence) this.gl.deleteSync(this.fence);
      this.fence = this.gl.fenceSync(this.gl.SYNC_GPU_COMMANDS_COMPLETE, 0); this.gl.flush();
    }
    const submitMs = performance.now() - submitStart;
    const hairApplied = wantsHair && !safeFallback;
    return {maskUploadMs, continuityMs, submitMs, dropM: applyDrop ? dropM : 0, hairApplied,
      maskWidth: hairApplied ? mask!.width : 0, maskHeight: hairApplied ? mask!.height : 0, sync: this.sync,
      guarded, passes, protectedRects: protection?.protectedRects.length ?? 0, editableRects: protection?.editableRects.length ?? 0, safeFallback,
      continuity: this.continuity && this.continuityModel !== null, cutNegativeZ: cut.negative, cutPositiveZ: cut.positive,
      // The width fit as the geometry of this draw has it, so a timing row says which pipeline produced the frame.
      widthFit: this.widthFitEnabled, widthFitState: this.widthFitEnabled && this.faceWidth ? this.faceWidth.state : 'off',
      widthRatio: this.widthRatio, armSpreadM: this.rearDrop?.spreadM ?? 0,
      templeMode: this.templeMode, templeNegativeXWeight: this.templeVisibilityState.negativeXWeight,
      templePositiveXWeight: this.templeVisibilityState.positiveXWeight, templeFrontalWeight: this.templeVisibilityState.frontalWeight,
      templeKeepCm: this.templeKeepCm, templeDropCm: this.templeDropCm,
      templeBendM: this.templeBendM, templePivotM: this.templePivotM,
      armSpreadStartZM: this.rearDrop?.spreadStartZM ?? null, armSpreadTotalM: this.rearDrop?.spreadM ?? 0};
  }

  /** Audit only: the current canvas pixels, top-down. Live frames never call this. */
  readback(): ImageData {return this.reader.read(this.renderer.domElement);}

  private clearPresentation(resetGeometry = true): void {
    this.lastPose = null; this.posedMatrix = null; this.residual = this.yaw = null; this.latestPose = null;
    this.facePose.visible = this.eyewearPose.visible = false;
    if (this.surfaceMesh) this.surfaceMesh.visible = false;
    if (this.headProxy) this.headProxy.visible = false;
    this.templeVisibility?.set(null); this.templeClip?.set(null); this.hairOcclusion?.set(false, this.hairStartZ);
    if (resetGeometry) this.rearDrop?.setDrop(0);
    this.currentRearDrop = null; this.protectionConfiguration = null; this.nasalRect = null; this.templePaths = null;
    this.hairOcclusion?.setCut(null, null);
  }
  private measureProjectionResidual(detection: Detection): number | null {
    let sum = 0;
    for (const index of RESIDUAL_LANDMARKS) {
      const landmark = detection.landmarks[index]; if (!landmark) return null;
      this.projected.fromArray(this.canonicalPositions, index * 3).applyMatrix4(this.facePose.matrix).project(this.camera);
      const x = (this.projected.x + 1) * this.frameWidth / 2, y = (1 - this.projected.y) * this.frameHeight / 2;
      sum += (x - landmark.x * this.frameWidth) ** 2 + (y - landmark.y * this.frameHeight) ** 2;
    }
    const residual = Math.sqrt(sum / RESIDUAL_LANDMARKS.length);
    return Number.isFinite(residual) ? residual : null;
  }
  dispose(): void {
    if (this.disposed) return; this.disposed = true;
    this.removeAbortListener?.(); this.removeAbortListener = null;
    if (this.fence) {try {this.gl.deleteSync(this.fence);} catch {/* context may be lost */} this.fence = null;}
    this.lastPose = null; this.posedMatrix = null; this.residual = this.yaw = null;
    this.protectionConfiguration = null; this.nasalRect = null; this.reader.dispose();
    this.scene.background = null; this.scene.environment = null;
    this.backgroundTexture?.dispose(); this.backgroundTexture = null;
    this.maskTexture?.dispose(); this.maskTexture = null; this.maskBytes = null;
    this.environmentTarget?.dispose(); this.environmentTarget = null;
    this.hairOcclusion?.dispose(); this.hairOcclusion = null;
    this.templeVisibility?.dispose(); this.templeVisibility = null;
    this.rearDrop?.dispose(); this.rearDrop = null; this.currentRearDrop = null;
    this.templeClip?.dispose(); this.templeClip = null;
    disposeObjects([this.scene, ...this.assetScenes]); this.assetScenes = [];
    this.canonicalPositions = []; this.nasalShape = null; this.faceSurface = null; this.surfaceMesh = null; this.headProxy = null; this.surfaceAttribute = null;
    this.faceWidth = null; this.widthRatio = 1; this.armSpread = 0;
    this.scene.clear(); this.renderer.dispose(); this.renderer.forceContextLoss();
  }
}
