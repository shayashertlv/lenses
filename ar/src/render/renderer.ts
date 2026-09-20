/** The try-on renderer: bridge pose, nasal shape, face and head occluders, fixed temple endings, side-depth
 *  visibility and hair occlusion applied inside the eyewear fragment shaders, drawn straight
 *  into the visible canvas. No pixel leaves the GPU on a live frame.
 *
 *  Guard (default on): the protection geometry (the optical and nasal rectangles and the arm corridors) is computed
 *  per frame; the stencil buffer marks the editable region (arm corridors minus protected rectangles), and the frame
 *  is drawn in two stencil-limited passes: everything outside the editable region from the unblended render, the
 *  editable region with the hair blend. No shader can write a hair-blended fragment into a
 *  protected pixel. When the protection cannot be established the frame is drawn without hair.
 *
 *  The pinned arm centrelines are projected per pose. The first resolved hair crossing hides the
 *  downstream shaft; the fixed terminal return remains buried when no usable mask is available.
 *
 *  `readback()` exists for the audit only. */
import {
  ACESFilmicToneMapping, BufferAttribute, BufferGeometry, CanvasTexture, Color, DataTexture, DirectionalLight,
  DoubleSide, DynamicDrawUsage, EqualStencilFunc, Group, KeepStencilOp, LinearFilter, Material, Mesh, NearestFilter,
  MeshBasicMaterial, MeshPhysicalMaterial, NoColorSpace, Object3D, PerspectiveCamera, PMREMGenerator, RedFormat, Scene,
  SRGBColorSpace, Texture, UnsignedByteType, Vector3, WebGLRenderer,
} from 'three';
import type {WebGLRenderTarget} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import type {GLTF} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import type {Detection} from '../face/protocol.ts';
import type {CategoryMask} from '../hair/protocol.ts';
import {FaceSurface} from './face-surface.ts';
import {TempleSurface} from './temple-surface.ts';
import {TempleCheekContactEstimator} from './temple-cheek-contact.ts';
import {TempleContactDepth} from './temple-contact-depth.ts';
import {createTempleTerminalFit, templeTerminalRelief} from './temple-terminal-fit.ts';
import type {TempleTerminalFit} from './temple-terminal-fit.ts';
import {createTempleHeadShell, createTempleHeadShellFit} from './temple-head-shell.ts';
import {TempleHeadFit} from './temple-head-fit.ts';
import {TempleEndpointTracker} from './temple-endpoint.ts';
import type {TempleEndpointReport} from './temple-endpoint.ts';
import {correctedBridgePose} from './bridge-pose.ts';
import {createNasalShape} from './nasal-shape.ts';
import {VIRTUAL_CAMERA} from './projection.ts';
import {DEFAULT_EYEWEAR_ID, eyewearById, GLASSES_METERS_TO_CENTIMETERS} from '../eyewear/catalog.ts';
import type {EyewearDefinition} from '../eyewear/catalog.ts';
import {createTempleClip, createTempleBlendConfiguration} from './temple-clip.ts';
import type {TempleClipConfiguration} from './temple-clip.ts';
import {createTempleVisibility, createTempleVisibilityConfiguration,
  TEMPLE_VISIBILITY_PARAMETERS} from './temple-visibility.ts';
import {createRearDrop} from './rear-drop.ts';
import {createProtection, nasalRoi} from './protection.ts';
import type {PixelRect, ProtectionConfiguration} from './protection.ts';
import {loadTempleContinuityModel, projectTempleContinuity} from './continuity.ts';
import type {ProjectedTemplePath, TempleContinuityModel} from './continuity.ts';
import {createHairOcclusion, DEFAULT_HAIR_START_Z_M} from './hair-occlusion.ts';
import {PixelReader} from './pixel-reader.ts';
import {DEFAULT_STEADY, PoseStabilizer, poseAngles} from './pose-stabilizer.ts';
import type {PoseSample, SteadyOptions} from './pose-stabilizer.ts';
import {maskUvMatrix} from '../hair/mask-reuse.ts';
import type {MaskWarp} from '../hair/mask-reuse.ts';
import {assetPath} from '../assets.ts';

export const GUARD_METHOD = 'gpu-stencil-protection-v1';
export {HAIR_OCCLUSION_METHOD, DEFAULT_HAIR_START_Z_M} from './hair-occlusion.ts';
/** The render never exceeds this width; the camera frame's aspect is kept. */
export const MAX_RENDER_WIDTH = 1280;
const RESIDUAL_LANDMARKS = [1, 4, 6, 33, 133, 168, 197, 263, 362] as const;
/** Authored fixed shape, not measured ear positions. The final band remains inside the head volume. */
const HIDDEN_TAIL_M = .025, END_FADE_M = .005, ARM_SPREAD_M = .018;

export interface RendererOptions {
  /** Mesh-local metres behind which temple fragments may blend toward the camera under hair (default −0.02). */
  hairStartZ?: number;
  /** Gate each frame on the previous frame's GPU completion (fence), so submission cannot run ahead of the GPU (default on). */
  sync?: boolean;
  /** Stencil protection of the optical/nasal rectangles (default on). */
  guard?: boolean;
  /** Responsive orientation/depth smoothing before the bridge pin; defaults to DEFAULT_STEADY, null disables it. */
  steady?: SteadyOptions | null;
}
export interface RenderVariant {hair: boolean; eyewear: boolean; guard: boolean;}
export interface FrameTimings {
  templeEndMaximumZM: number | null; templeEndNegativeZM: number | null; templeEndPositiveZM: number | null; templeEndState: string;
  maskUploadMs: number; endpointMs: number; submitMs: number;
  hairApplied: boolean; maskWidth: number; maskHeight: number; sync: boolean;
  guarded: boolean; passes: number; protectedRects: number; editableRects: number; safeFallback: boolean;
  templeKeepCm: number; templeDropCm: number;
  armSpreadM: number; armSpreadStartZM: number | null;
}
interface CanonicalFace {positions: number[]; indices: number[];}
export interface CaptureGeometry {
  templeTerminalFit?: TempleTerminalFit | null;
  templeHeadFit?: TempleHeadFit['report'] | null;
  eyewearModelId: string; rawMatrix: number[]; eyewearMatrix: number[]; yawDegrees: number;
  templeClip: TempleClipConfiguration | null; hairStartZ: number;
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
  private continuityModel: TempleContinuityModel | null = null;
  private continuityFailure: string | null = null;
  private templePaths: ProjectedTemplePath[] | null = null;
  private templeEndpointTracker: TempleEndpointTracker | null = null;
  private templeTerminalFit: TempleTerminalFit | null = null;
  private templeEndpointReport: TempleEndpointReport | null = null;
  private templeEndpointPending = false;
  private lastTrackedTimestampMs = -Infinity;
  private poseTimestampMs = 0;
  private templeSurface: TempleSurface | null = null;
  private cheekContact: TempleCheekContactEstimator | null = null;
  private readonly contactDepth = new TempleContactDepth();
  private observedCheekGeometry: BufferGeometry | null = null;
  private observedCheekAttribute: BufferAttribute | null = null;
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
  private protectionConfiguration: ProtectionConfiguration | null = null;
  private nasalRect: PixelRect | null = null;
  private faceSurface: FaceSurface | null = null;
  private surfaceMesh: Mesh | null = null;
  private templeHeadShell: Mesh | null = null;
  private templeHeadFit: TempleHeadFit | null = null;
  private templeHeadShellFit: ReturnType<typeof createTempleHeadShellFit> | null = null;
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
  private readonly templeKeepCm = TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm;
  private readonly templeDropCm = TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm;

  private constructor(renderer: WebGLRenderer, gl: WebGL2RenderingContext, eyewear: EyewearDefinition, options: RendererOptions) {
    this.renderer = renderer; this.gl = gl; this.eyewear = eyewear;
    const steady = options.steady === undefined ? DEFAULT_STEADY : options.steady;
    this.stabilizer = steady ? new PoseStabilizer(steady) : null;
    this.hairStartZ = options.hairStartZ ?? DEFAULT_HAIR_START_Z_M; this.sync = options.sync ?? true; this.guard = options.guard ?? true;
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
  /** The projected arm centrelines for the posed frame (null without pinned geometry). */
  get paths(): ProjectedTemplePath[] | null {return this.templePaths;}
  get endpointUnavailable(): string | null {return this.continuityFailure;}
  /** How the next `render` reads its mask: null for the frame's own mask, or a mask reused from an earlier frame placed
   *  by the head's motion (hair/mask-reuse.ts). It applies to shader lookups and hair endpoints alike, and stays
   *  until changed, so an audit's variants of the held frame read the mask the way the live frame did. */
  setMaskWarp(warp: MaskWarp | null): void {this.maskWarp = warp;}
  /** The posed frame's raw and steadied orientation and depth (numbers only), or null when no face is posed. */
  get poseSample(): PoseSample | null {return this.latestPose ? {...this.latestPose} : null;}
  private get templeEndMaximumZM(): number {
    const original = this.eyewear.templeClipLocalZM;
    // An already-short external frame must never grow a tail, or place its endpoint ahead of its usable shaft.
    return Math.max(original, Math.min(-.055, original + HIDDEN_TAIL_M, (this.continuityModel?.startZM ?? Infinity) - .008));
  }
  private applyTempleEndpoint(): void {
    const maximum = this.templeEndMaximumZM, report = this.templeEndpointReport;
    this.templeClip?.set({...this.templeClipConfiguration, fadeLengthLocalM: END_FADE_M,
      negativeXCutoffLocalZM: Math.max(maximum, report?.negativeZM ?? maximum),
      positiveXCutoffLocalZM: Math.max(maximum, report?.positiveZM ?? maximum),
      negativeXFadeLengthLocalM: report?.negativeFadeM ?? END_FADE_M,
      positiveXFadeLengthLocalM: report?.positiveFadeM ?? END_FADE_M});
  }
  /** The arm centrelines use the same fixed spread and terminal return as the drawn geometry. */
  private projectPaths(eyewearMatrix: readonly number[]): ProjectedTemplePath[] | null {
    if (!this.continuityModel) return null;
    const {width, height} = this.renderSize;
    return projectTempleContinuity(this.continuityModel, {eyewearMatrix, offsetCm: this.eyewear.offsetCm,
      sourceAspect: this.frameWidth / this.frameHeight, width, height, dropM: 0,
      // The endpoint probe and the drawn vertices share the fixed spread and the asset's actual hinge plane.
      spreadM: ARM_SPREAD_M, spreadStartZM: this.rearDrop?.spreadStartZM, terminalFit: this.templeTerminalFit});
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
      templeTerminalFit: this.templeTerminalFit ? {...this.templeTerminalFit} : null,
      templeHeadFit: this.templeHeadFit?.report ?? null,
      templeClip: this.templeClip?.configuration ?? null, hairStartZ: this.hairStartZ,
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
      // The pinned arm centrelines support hair endpoints; failure retains the fixed buried cap.
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
    try {this.templeHeadFit = new TempleHeadFit(face.positions);} catch {this.templeHeadFit = null;}
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
    this.cheekContact = new TempleCheekContactEstimator(face.positions, face.indices);
    this.observedCheekGeometry = new BufferGeometry();
    this.observedCheekAttribute = new BufferAttribute(new Float32Array(face.positions.length), 3).setUsage(DynamicDrawUsage);
    this.observedCheekGeometry.setAttribute('position', this.observedCheekAttribute);
    this.observedCheekGeometry.setIndex(face.indices);
    this.rearDrop = createRearDrop(eyewearScene, this.eyewear.templeClipLocalZM, 0);
    this.templeTerminalFit = createTempleTerminalFit(eyewearScene, {offsetCm: this.eyewear.offsetCm,
      spreadM: ARM_SPREAD_M, spreadStartZM: this.rearDrop.spreadStartZM,
      modelCutoffZM: this.eyewear.templeClipLocalZM, maximumZM: this.templeEndMaximumZM});
    const occlusionMaterial = new MeshBasicMaterial({colorWrite: false, depthWrite: true, depthTest: true, side: DoubleSide});
    this.faceSurface = new FaceSurface(face.positions);
    const geometry = new BufferGeometry();
    this.surfaceAttribute = new BufferAttribute(this.faceSurface.positions, 3).setUsage(DynamicDrawUsage);
    geometry.setAttribute('position', this.surfaceAttribute); geometry.setIndex(face.indices);
    const surface = new Mesh(geometry, occlusionMaterial); surface.name = 'Observed face depth in camera space';
    surface.renderOrder = -2; surface.frustumCulled = false; surface.visible = false; this.surfaceMesh = surface; this.scene.add(surface);
    // The observed face is an open surface. Join its canonical outer rim to the rear volume so
    // a temple cannot disappear into the face and emerge through a seam before reaching the skull.
    const shell = new Mesh(createTempleHeadShell(face.positions, face.indices), occlusionMaterial);
    shell.name = 'Connected posterior head depth for fixed temples'; shell.renderOrder = -2;
    shell.visible = false; this.templeHeadShell = shell; this.facePose.add(shell);
    this.templeHeadShellFit = createTempleHeadShellFit(shell.geometry);
    this.templeVisibility = createTempleVisibility(eyewearScene, {renderer: this.renderer, scene: this.scene, camera: this.camera,
      eyewearPose: this.eyewearPose, observedFaceSurface: this.observedCheekGeometry});
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
   *  `timestampMs` is the frame's capture time, shared by pose and contact-depth filters. */
  pose(frame: HTMLCanvasElement, detection: Detection, timestampMs: number = performance.now()): boolean {
    if (this.disposed) return false;
    this.poseTimestampMs = timestampMs; this.templeEndpointPending = false;
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
          let cheekDepthValid = true;
          // Preserve this frame's actual observed shape BEFORE nasal/lateral proxy fitting.
          // It is a separate cheek-depth input; the accepted stable main occluder is unchanged.
          if (this.observedCheekAttribute) {
            const observed = this.observedCheekAttribute.array as Float32Array;
            cheekDepthValid = this.contactDepth.apply(this.faceSurface!.positions, matrix, timestampMs, observed);
            this.observedCheekAttribute.needsUpdate = true;
          }
          if (!this.nasalShape) throw new Error('The nasal shape is not initialized.');
          // Only the anterior occluder is calibrated; the eyewear and buried rear volume stay fixed.
          this.templeHeadFit?.observe(this.faceSurface!.positions, matrix, timestampMs);
          this.templeHeadShellFit?.set(this.templeHeadFit?.ratio ?? 1);
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
          this.templeSurface ??= new TempleSurface(this.canonicalPositions);
          this.templeSurface.apply(this.faceSurface!.positions, attachment.matrix, this.templeHeadFit?.ratio ?? 1);
          this.facePose.matrix.fromArray(attachment.matrix); this.facePose.matrixWorldNeedsUpdate = true;
          this.eyewearPose.matrix.fromArray(attachment.matrix); this.eyewearPose.matrixWorldNeedsUpdate = true;
          this.rearDrop?.setTerminalFit(this.templeTerminalFit);
          this.rearDrop?.setShape(0, ARM_SPREAD_M);
          this.applyTempleEndpoint();
          const cheekContact = cheekDepthValid
            ? this.cheekContact?.evaluate(detection.landmarks) ?? null : null;
          this.templeVisibility?.set(
            {...createTempleVisibilityConfiguration(this.renderer.capabilities?.samples ?? 0, this.templeKeepCm, this.templeDropCm),
              excludeArmsFromLensInput: true,
              cheekContact,
              cheekTransitionPx: 2,
              terminalReturn: this.templeTerminalFit ? templeTerminalRelief(this.templeTerminalFit) : null});
          this.eyewearPose.visible = true; this.camera.updateMatrixWorld();
          this.yaw = attachment.yawDegrees; this.residual = this.measureProjectionResidual(detection);
          if (this.surfaceAttribute) this.surfaceAttribute.needsUpdate = true;
          if (this.surfaceMesh) this.surfaceMesh.visible = true;
          if (this.templeHeadShell) this.templeHeadShell.visible = true;
          this.lastPose = {rawMatrix: matrix.slice(), eyewearMatrix: attachment.matrix.slice(), yawDegrees: attachment.yawDegrees};
          this.posedMatrix = poseMatrix;
          // The protection geometry for this exact pose, fixed shape and frame size.
          const {width, height} = this.renderSize, shape = this.rearDrop;
          this.protectionConfiguration = shape ? createProtection({optical: shape.opticalBounds, originalArms: shape.originalArmBounds, candidateArms: shape.candidateArmBounds},
            attachment.matrix, this.eyewear.offsetCm, detection.landmarks, width, height, frame.width / frame.height) : null;
          this.nasalRect = nasalRoi(detection, width, height);
          this.templePaths = this.projectPaths(attachment.matrix);
          this.templeEndpointPending = true;
          this.lastTrackedTimestampMs = timestampMs;
        }
      }
      if (!this.facePose.visible) {
        this.templeHeadFit?.miss(timestampMs);
        // A dropped face frame must not grow a hidden tail on reacquisition. A genuinely new session/face
        // after sustained loss starts clean; nothing is drawn while tracking is absent.
        if (timestampMs - this.lastTrackedTimestampMs > 1000) {
          this.templeEndpointTracker?.reset(); this.templeEndpointReport = null;
        }
        this.contactDepth.reset();
        this.stabilizer?.reset();
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
      // Nearest: a mask smaller than the frame is read the way the endpoint tracker and CPU reference read it, by the
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

  /** Draw the posed frame into the canvas. Live frames use `{hair, eyewear: true, guard}`; the audit draws
   *  other variants of the same posed frame and reads them back. */
  render(mask: CategoryMask | null, variant: Partial<RenderVariant> = {}): FrameTimings {
    if (this.disposed) throw new Error('The renderer is disposed.');
    if (!this.backgroundTexture) throw new Error('Nothing is posed.');
    const v: RenderVariant = {hair: true, eyewear: true, guard: this.guard, ...variant};
    const tracked = this.facePose.visible;
    const wantsHair = v.hair && mask !== null && tracked;
    const endpointStart = performance.now();
    // Update once per posed frame. Audit variants reuse the exact same endpoint, even when their hair flag differs.
    if (this.templeEndpointPending && tracked) {
      if (this.continuityModel && this.latestPose) {
        this.templeEndpointTracker ??= new TempleEndpointTracker(this.continuityModel, this.templeEndMaximumZM);
        this.templeEndpointReport = this.templeEndpointTracker.update({paths: this.templePaths, mask: wantsHair ? mask : null,
          render: this.renderSize, warp: this.maskWarp, timestampMs: this.poseTimestampMs,
          yawDegrees: this.latestPose.steadyYawDeg ?? this.latestPose.yawDeg,
          pitchDegrees: this.latestPose.steadyPitchDeg ?? this.latestPose.pitchDeg, evidenceId: mask?.category});
      } else this.templeEndpointReport = null;
      this.applyTempleEndpoint(); this.templeEndpointPending = false;
    }
    const endpointMs = performance.now() - endpointStart;
    const maskUploadMs = wantsHair ? this.uploadMask(mask) : 0;
    this.hairOcclusion?.setMaskUv(wantsHair && this.maskWarp ? maskUvMatrix(this.maskWarp) : null);
    const {width, height} = this.renderSize;
    const submitStart = performance.now();
    const protection = this.protectionConfiguration, nose = this.nasalRect;
    const guarded = v.guard && tracked && protection !== null, safeFallback = v.guard && tracked && protection === null;
    this.eyewearPose.visible = v.eyewear && tracked;
    this.templeClip?.prepareRender(this.backgroundTexture, width, height);
    if (tracked && this.posedMatrix && this.templeVisibility?.configuration) this.templeVisibility.prepare(this.posedMatrix, this.backgroundTexture);
    this.hairOcclusion?.prepareRender(wantsHair ? this.maskTexture : null, width, height, this.backgroundTexture, 2);
    this.renderer.setRenderTarget(null);
    let passes = 1;
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
    if (this.sync) {
      if (this.fence) this.gl.deleteSync(this.fence);
      this.fence = this.gl.fenceSync(this.gl.SYNC_GPU_COMMANDS_COMPLETE, 0); this.gl.flush();
    }
    const submitMs = performance.now() - submitStart;
    const hairApplied = wantsHair && !safeFallback;
    return {
      templeEndMaximumZM: this.templeEndMaximumZM,
      templeEndNegativeZM: this.templeEndpointReport?.negativeZM ?? this.templeEndMaximumZM,
      templeEndPositiveZM: this.templeEndpointReport?.positiveZM ?? this.templeEndMaximumZM,
      templeEndState: this.templeEndpointReport
        ? `${this.templeEndpointReport.negativeState}/${this.templeEndpointReport.positiveState}` : 'fixed cap',
      maskUploadMs, endpointMs, submitMs, hairApplied,
      maskWidth: hairApplied ? mask!.width : 0, maskHeight: hairApplied ? mask!.height : 0, sync: this.sync,
      guarded, passes, protectedRects: protection?.protectedRects.length ?? 0, editableRects: protection?.editableRects.length ?? 0, safeFallback,
      armSpreadM: this.rearDrop?.spreadM ?? 0,
      templeKeepCm: this.templeKeepCm, templeDropCm: this.templeDropCm,
      armSpreadStartZM: this.rearDrop?.spreadStartZM ?? null};
  }

  /** Audit only: the current canvas pixels, top-down. Live frames never call this. */
  readback(): ImageData {return this.reader.read(this.renderer.domElement);}

  private clearPresentation(resetGeometry = true): void {
    if (resetGeometry) {
      this.contactDepth.reset();
      this.templeEndpointTracker?.reset(); this.templeEndpointReport = null; this.templeEndpointPending = false;
      this.templeHeadFit?.reset(); this.templeHeadShellFit?.reset();
    }
    this.lastPose = null; this.posedMatrix = null; this.residual = this.yaw = null; this.latestPose = null;
    this.facePose.visible = this.eyewearPose.visible = false;
    if (this.surfaceMesh) this.surfaceMesh.visible = false;
    if (this.templeHeadShell) this.templeHeadShell.visible = false;
    this.templeVisibility?.set(null); this.templeClip?.set(null); this.hairOcclusion?.set(false, this.hairStartZ);
    this.protectionConfiguration = null; this.nasalRect = null; this.templePaths = null;
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
    this.contactDepth.reset();
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
    this.observedCheekGeometry?.dispose(); this.observedCheekGeometry = null; this.observedCheekAttribute = null; this.cheekContact = null;
    this.rearDrop?.dispose(); this.rearDrop = null;
    this.templeClip?.dispose(); this.templeClip = null;
    this.templeEndpointTracker = null; this.templeEndpointReport = null; this.templeSurface = null; this.templeTerminalFit = null;
    this.templeHeadFit = null; this.templeHeadShellFit = null;
    disposeObjects([this.scene, ...this.assetScenes]); this.assetScenes = [];
    this.canonicalPositions = []; this.nasalShape = null; this.faceSurface = null; this.surfaceMesh = null; this.templeHeadShell = null; this.surfaceAttribute = null;
    this.scene.clear(); this.renderer.dispose(); this.renderer.forceContextLoss();
  }
}
