/** The try-on renderer: bridge pose, nasal shape, face and head occluders, fixed temple endings, side-depth
 *  visibility and hair occlusion applied inside the eyewear fragment shaders, drawn straight
 *  into the visible canvas. Camera image pixels stay on the GPU during live use.
 *
 *  It renders one ModelDefinition (eyewear/catalog.ts; there is no catalog). Loading (WP1_DESIGN.md §1, §5): the
 *  WebGLRenderer exists before the GLB is parsed (KTX2 needs it); model/load.ts reads the container, resolves external
 *  resources against the model URL and parses with Draco, meshopt and KTX2; model/normalize.ts then runs the one
 *  normalisation pass (transforms, geometry, units, lens identification, the measured §2 rows) before any subsystem.
 *  A §2 refusal throws ModelRefusal carrying the whole report; everything else renders, and every subsystem that
 *  cannot be built (temple clip, rear drop, continuity, terminal fit, temple visibility, look-through, twins, hair
 *  occlusion, shadows, protection) falls back on its own, recorded in `report()`, never silent.
 *
 *  Guard (default on): the protection geometry (the optical and nasal rectangles and the arm corridors) is computed
 *  per frame; the stencil buffer marks the editable region (arm corridors minus protected rectangles), and the frame
 *  is drawn in two stencil-limited passes: everything outside the editable region from the unblended render, the
 *  editable region with the hair blend. No shader can write a hair-blended fragment into a
 *  protected pixel. When the protection cannot be established the frame is drawn without hair.
 *
 *  Temples (WP1_DESIGN.md §3, temple-geometry.ts) are found per side by position, whatever their opacity; a side
 *  without one keeps a fixed cap and is reported per side (the §2 Orientation row fails on its own). The clip is derived
 *  from the arms' tips (just inside the shorter arm, floored at -0.14 m) unless a usable clip was handed over. The fit
 *  (WP1_DESIGN.md §4) is the live auto-fit, whose applied scale is reported (warned beyond 3 %), or the authored size
 *  with RendererOptions.fixedScale.
 *
 *  The arm centrelines, read from the normalised scene (the same bytes and normalisation that are drawn; no pin, no
 *  second parse), are projected per pose, per side. The first resolved hair crossing hides the
 *  downstream shaft; the fixed terminal return remains buried when no usable mask is available.
 *
 *  `readback()` exists for the audit only. */
import {
  ACESFilmicToneMapping, Box3, BufferAttribute, BufferGeometry, CanvasTexture, Color, DataTexture, DirectionalLight,
  DoubleSide, DynamicDrawUsage, EqualStencilFunc, Group, KeepStencilOp, LinearFilter, Material, Matrix4, Mesh, NearestFilter,
  MeshBasicMaterial, MeshPhysicalMaterial, MeshStandardMaterial, NoColorSpace, Object3D, PerspectiveCamera, PMREMGenerator, RedFormat, Scene,
  SRGBColorSpace, Texture, UnsignedByteType, Vector3, Vector4, WebGLRenderer,
} from 'three';
import type {WebGLRenderTarget} from 'three';
import type {GLTF} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {describeError} from '../model/glb.ts';
import {loadModel} from '../model/load.ts';
import type {LoadModelOptions} from '../model/load.ts';
import {nodeName, normalizeModel} from '../model/normalize.ts';
import {ModelReportBuilder} from '../model/report.ts';
import type {ModelReport} from '../model/report.ts';
import {createLightingMaps} from './lighting.ts';
import type {LightingConfig, LightingMaps} from './lighting.ts';
import {createClipUniforms, FLAT_OVERRIDES, flatOverrideMaterial, isRenderOverride, LENS_ID_RGB, partColour} from './override.ts';
import type {PaletteEntry, PartSide, RenderOverride} from './override.ts';
import {meshClassOf} from '../eyewear/part-class.ts';
import {partVisibilityOf, profileTempleSide} from '../eyewear/part-visibility.ts';
import type {Detection} from '../face/protocol.ts';
import type {CategoryMask} from '../hair/protocol.ts';
import {FaceSurface} from './face-surface.ts';
import {TempleSurface} from './temple-surface.ts';
import {TempleCheekContactEstimator} from './temple-cheek-contact.ts';
import {TempleContactDepth} from './temple-contact-depth.ts';
import {createTempleTerminalFitEvaluator, templeTerminalRelief, terminalFitX} from './temple-terminal-fit.ts';
import type {TempleTerminalFit} from './temple-terminal-fit.ts';
import {createTempleHeadShell, createTempleHeadShellFit} from './temple-head-shell.ts';
import {spreadArmX} from './face-width.ts';
import {TempleHeadFit} from './temple-head-fit.ts';
import {TempleEndpointTracker} from './temple-endpoint.ts';
import type {TempleEndpointReport} from './temple-endpoint.ts';
import {correctedBridgePose} from './bridge-pose.ts';
import {createNasalShape} from './nasal-shape.ts';
import {VIRTUAL_CAMERA} from './projection.ts';
import {GLASSES_METERS_TO_CENTIMETERS, resolveModelDefinition, TEMPLE_CLIP_FLOOR_LOCAL_Z_M} from '../eyewear/catalog.ts';
import type {ModelDefinition, ResolvedModel} from '../eyewear/catalog.ts';
import {continuityUnavailableDetail, defaultClipDetail, deriveTempleClip, missingTempleDetail, templeInventory, templeMeasurements, TEMPLE_SIDE_NAMES} from './temple-geometry.ts';
import type {TempleInventory} from './temple-geometry.ts';
import {createTempleClip, createTempleBlendConfiguration} from './temple-clip.ts';
import type {TempleClipConfiguration} from './temple-clip.ts';
import {createTempleVisibility, createTempleVisibilityConfiguration,
  TEMPLE_VISIBILITY_PARAMETERS} from './temple-visibility.ts';
import {createRearDrop} from './rear-drop.ts';
import {createPhotoTemplePose, parsePhotoTemplePose} from './photo-temple-pose.ts';
import type {PhotoTemplePose} from './photo-temple-pose.ts';
import {createProtection, nasalRoi, protectionProjection} from './protection.ts';
import type {PixelRect, ProtectionConfiguration} from './protection.ts';
import {buildTempleContinuityModel, projectTempleContinuity} from './continuity.ts';
import type {ProjectedTemplePath, TempleContinuityModel} from './continuity.ts';
import {createHairOcclusion, DEFAULT_HAIR_START_Z_M} from './hair-occlusion.ts';
import {PixelReader} from './pixel-reader.ts';
import {DEFAULT_STEADY, PoseStabilizer, poseAngles} from './pose-stabilizer.ts';
import type {PoseSample, SteadyOptions} from './pose-stabilizer.ts';
import {maskUvMatrix} from '../hair/mask-reuse.ts';
import type {MaskWarp} from '../hair/mask-reuse.ts';
import {assetPath} from '../assets.ts';
import {DEFAULT_SHADOW_SETTINGS, EyewearShadow, normalizeShadowSettings} from './eyewear-shadow.ts';
import type {ShadowSettings} from './eyewear-shadow.ts';
import {ShadowReceiver} from './shadow-receiver.ts';
import {EYEWEAR_FIT, EyewearFitSession, observeFitWidth} from './eyewear-fit.ts';
import {applyTransmissionToneMapping, isOpticalMaterial, isTranslucentFrameMaterial} from '../eyewear/optical-material.ts';
import type {GltfSource, LensIdentificationReport} from '../eyewear/optical-material.ts';
import {applyVolumeAttenuationScale} from './eyewear-volume.ts';
import {createCameraTransmissionTwin, createCameraTransmissionUniforms, refreshCameraTransmissionTwin, setCameraTransmissionSource, setCrystalLookThroughSource} from './translucent-twin.ts';
import {hasLegacyTransmissiveOptics, TranslucentLookThrough} from './translucent-look-through.ts';
import {createSeeThroughRoom} from './eyewear-reflection.ts';

export const GUARD_METHOD = 'gpu-stencil-protection-v1';
export {HAIR_OCCLUSION_METHOD, DEFAULT_HAIR_START_Z_M} from './hair-occlusion.ts';
/** The render never exceeds this width; the camera frame's aspect is kept. */
export const MAX_RENDER_WIDTH = 1280;
/** scene.environmentIntensity of the room environment; lens materials scale it by the eyewear's lensEnvIntensity. */
export const SCENE_ENVIRONMENT_INTENSITY = 0.8;
/** Authored fixed shape, not measured ear positions. The final band remains inside the head volume. */
const HIDDEN_TAIL_M = .025, END_FADE_M = .005, ARM_SPREAD_M = .018;
/** Frame-front to upper-face ratio for the automatic fit (eyewear-fit.ts). The catalog's per-frame ratios are gone;
 *  every handed-over model always used 1. */
const FIT_FRONT_RATIO = 1;
/** WP1_DESIGN.md §4: an applied auto-fit scale further than this from 1 is a warning (the images do not show the
 *  authored size). */
export const FIT_SCALE_WARN_FRACTION = 0.03;

export type {ModelReport} from '../model/report.ts';
export {ModelRefusal, ModelUnchecked} from '../model/report.ts';
export type {LightingConfig} from './lighting.ts';
export type {PaletteEntry, PartSide, RenderOverride} from './override.ts';

/** One eyewear mesh as the capture sees it (WP1_DESIGN.md §5 `parts()`). */
export interface RenderedPart {
  readonly mesh: Mesh;
  /** The glTF node name (as the report names it), else the mesh's own name. */
  readonly name: string;
  /** The decided lens/frame class (WP1_DESIGN.md §2); 'mixed' only for a hand-built multi-material mesh. */
  readonly class: 'lens' | 'frame' | 'mixed';
  /** By geometry, never by name: wearer's 'right' x < 0, 'left' x > 0, 'centre' when the part crosses x = 0. */
  readonly side: PartSide;
  /** A near-arm visibility overlay (a clone the renderer draws over an arm), not an authored part. */
  readonly overlay: boolean;
}
/** The virtual camera of the posed frame (WP1_DESIGN.md §5 `cameraState()`): column-major matrices, render pixels. */
export interface CameraState {
  readonly view: number[]; readonly projection: number[];
  readonly width: number; readonly height: number;
  readonly near: number; readonly far: number; readonly verticalFovDegrees: number;
}
/** The drawn arm centrelines (`templeCentrelines()`): per side (0: x < 0, 1: x > 0) [x, y, z] asset-local metres, hinge
 *  first; where the deliberate return behind the ear begins (null without a terminal fit); each side's live cap. */
export interface TempleCentrelines {
  readonly sides: readonly [Float32Array, Float32Array];
  readonly returnStartZM: number | null;
  readonly capZM: readonly [number, number];
}

/** An explicit camera for a capture without a face (`TryOnRenderer.poseCamera`, `arview capture --cameras ... --no-face`):
 *  the eyewear's place in camera space (asset-local metres to camera-space centimetres, the camera at the origin looking
 *  down -Z with +Y up, exactly as the try-on's virtual camera) and the projection, both column-major, with the clip planes
 *  the projection was built with (centimetres). */
export interface ExplicitCamera {
  readonly assetToCamera: readonly number[];
  readonly projection: readonly number[];
  readonly nearCm: number;
  readonly farCm: number;
  readonly temple_pose?: PhotoTemplePose;
}

export interface RendererOptions {
  /** Mesh-local metres behind which temple fragments may blend toward the camera under hair (default −0.02). */
  hairStartZ?: number;
  /** Gate each frame on the previous frame's GPU completion (fence), so submission cannot run ahead of the GPU (default on). */
  sync?: boolean;
  /** Stencil protection of the optical/nasal rectangles (default on). */
  guard?: boolean;
  /** Responsive orientation/depth smoothing before the bridge pin; defaults to DEFAULT_STEADY, null disables it. */
  steady?: SteadyOptions | null;
  /** Share only while explicitly switching models for the same wearer. New camera sessions start fresh. */
  fitSession?: EyewearFitSession;
  /** Render at the authored size (fit scale exactly 1, WP1_DESIGN.md §4): no automatic fit to the face, no size
   *  adjustment, shown from the first pose. Default off: the live auto-fit (×0.85-1.15 times the 1.08 preference), whose
   *  applied scale the report records. */
  fixedScale?: boolean;
  /** The load front-end's options beyond the address (tests stub texture decoding; a digest handed over is compared). */
  load?: Pick<LoadModelOptions, 'fetchResource' | 'plugins' | 'handoverSha256'>;
}
export interface RenderVariant {
  hair: boolean; eyewear: boolean; guard: boolean; shadows: boolean;
  /** Capture only: shade the existing face/head depth meshes, without changing their geometry or hiding rule. */
  visibleHead?: boolean;
  /** A diagnostic override (override.ts), applied to originals, overlays and twins alike and restored after the draw. */
  override?: RenderOverride | null;
}
export interface FrameTimings {
  visibleHead?: boolean;
  templeEndMaximumZM: number | null; templeEndNegativeZM: number | null; templeEndPositiveZM: number | null; templeEndState: string;
  maskUploadMs: number; endpointMs: number; submitMs: number;
  hairApplied: boolean; maskWidth: number; maskHeight: number; sync: boolean;
  guarded: boolean; passes: number; protectedRects: number; editableRects: number; safeFallback: boolean;
  templeKeepCm: number; templeDropCm: number;
  armSpreadM: number; armSpreadStartZM: number | null;
  shadowsApplied?: boolean; shadowMs?: number;
  /** The override this draw used, and for 'part-id' the colour of every part. */
  override?: RenderOverride; palette?: PaletteEntry[];
}
interface CanonicalFace {positions: number[]; indices: number[];}
/** The fit as the renderer applies it: the session's report, the applied scale, and whether it is the live auto-fit
 *  ('auto') or the authored size ('fixed', RendererOptions.fixedScale). */
export type FittingReport = EyewearFitSession['report'] & {scale: number; limited: boolean; ready: boolean; mode: 'auto' | 'fixed'};
export interface CaptureGeometry {
  templeTerminalFit?: TempleTerminalFit | null;
  templeHeadFit?: TempleHeadFit['report'] | null;
  fitting?: FittingReport;
  /** The rendered model's address (its identity on this page). */
  eyewearModelId: string; rawMatrix: number[]; eyewearMatrix: number[]; yawDegrees: number;
  templeClip: TempleClipConfiguration | null; hairStartZ: number;
  protection: ProtectionConfiguration | null; noseRoi: PixelRect | null;
}

function abortError(): DOMException {return new DOMException('Renderer startup was cancelled.', 'AbortError');}
const messageOf = describeError;
/** The bounding box of every eyewear vertex in the front 25 mm (asset-local metres): the fit width's fallback when the
 *  rear drop, whose optical bounds it normally is, could not be built. */
function frontRegionBounds(root: Object3D): Box3 {
  const all = new Box3(), front = new Box3(), point = new Vector3();
  root.updateWorldMatrix(true, true);
  const inverse = root.matrixWorld.clone().invert();
  const each = (visit: (point: Vector3) => void) => root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const position = object.geometry.getAttribute('position'), matrix = inverse.clone().multiply(object.matrixWorld);
    if (position) for (let i = 0; i < position.count; i++) visit(point.fromBufferAttribute(position, i).applyMatrix4(matrix));
  });
  each(p => all.expandByPoint(p));
  const band = all.max.z - .025;
  each(p => {if (p.z >= band) front.expandByPoint(p);});
  return front;
}
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
    }
  });
  for (const material of materials) for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
  for (const texture of textures) {const image = texture.image; if (typeof ImageBitmap !== 'undefined' && image instanceof ImageBitmap) bitmaps.add(image); texture.dispose();}
  for (const bitmap of bitmaps) bitmap.close();
  for (const material of materials) material.dispose();
  for (const geometry of geometries) geometry.dispose();
}

const GPU_WAIT_LIMIT_MS = 1000, GPU_WAIT_TIMEOUTS_LIMIT = 3;

export class TryOnRenderer {
  readonly model: ResolvedModel;
  /** The model report (model/report.ts): the definition's own warnings first, then the load, then render-time fallbacks. */
  private readonly modelReport: ModelReportBuilder;
  /** The loaded glTF scene (asset-local metres, normalised). */
  private eyewearRoot: Object3D | null = null;
  private lighting: {maps: LightingMaps; undo: () => void} | null = null;
  /** The hard temple cut of the flat overrides follows the live clip planes. */
  private readonly overrideClip = createClipUniforms();
  /** Materials whose twin could not be built: they keep Three's own transmission (reported once each). */
  private readonly twinFailures = new Set<Material>();
  private readonly protectionFailures = new Set<string>();
  /** The report's name of every loaded mesh (its glTF node name). */
  private readonly partNames = new Map<Object3D, string>();
  private readonly renderer: WebGLRenderer;
  private readonly gl: WebGL2RenderingContext;
  private readonly scene = new Scene();
  private readonly camera = new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees, 1, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
  private readonly facePose = new Group();
  private readonly eyewearPose = new Group();
  private eyewearAsset: Group | null = null;
  /** The see-through room (eyewear-reflection.ts), unblurred: what crystal reflects. Native lenses keep the scene room. */
  private seeThroughEnvironmentTarget: WebGLRenderTarget | null = null;
  private readonly fitSession: EyewearFitSession;
  /** RendererOptions.fixedScale: the authored size, no auto-fit. */
  private readonly fixedScale: boolean;
  private fitScale = 1;
  private fitLimited = false;
  /** The last fit scale written to the report, and the scales already warned about (each once). */
  private notedFitScale: string | null = null;
  private readonly fitScaleWarnings = new Set<string>();
  private adjustmentIgnored = false;
  /** The temple clip plane in use (model-local metres): the handover's, else derived from the temples at configure
   *  (temple-geometry.ts deriveTempleClip). Until configure it is the handover or the floor. */
  private clipZM: number;
  /** The temples by position, per side (temple-geometry.ts), once configured. */
  private templeSides: TempleInventory | null = null;
  /** Reveal once this model's initial fit settles; later manual adjustments remain visible. */
  private fitRevealed = false;
  private fitFrontBounds: Box3 | null = null;
  private fitFrontWidthCm = 0;
  private terminalFitAtScale: ((scale: number) => TempleTerminalFit | null) | null = null;
  private readonly fitProjected = new Vector4();
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
  private readonly shadowReceiver = new ShadowReceiver();
  private shadowReceiverGeometry: BufferGeometry | null = null;
  private shadowReceiverAttribute: BufferAttribute | null = null;
  private observedCheekGeometry: BufferGeometry | null = null;
  private observedCheekAttribute: BufferAttribute | null = null;
  private readonly reader = new PixelReader();
  private backgroundTexture: CanvasTexture | null = null;
  private shadows: EyewearShadow | null = null;
  private shadowSettings: ShadowSettings = {...DEFAULT_SHADOW_SETTINGS};
  private shadowBackground: Texture | null = null;
  private shadowPending = true;
  private shadowSurfaceValid = false;
  private environmentTarget: WebGLRenderTarget | null = null;
  private canonicalPositions: number[] = [];
  private nasalShape: ReturnType<typeof createNasalShape> | null = null;
  private templeClip: ReturnType<typeof createTempleClip> | null = null;
  private templeVisibility: ReturnType<typeof createTempleVisibility> | null = null;
  private rearDrop: ReturnType<typeof createRearDrop> | null = null;
  private photoTemplePose: ReturnType<typeof createPhotoTemplePose> | null = null;
  private hairOcclusion: ReturnType<typeof createHairOcclusion> | null = null;
  private lensMeshes: Mesh[] = [];
  /** The lens_identification block of the load (WP1_DESIGN.md §2): one entry per glTF material. */
  private lensIdentificationReport: LensIdentificationReport | null = null;
  /** Crystal / translucent frame materials (authored roles, isTranslucentFrameMaterial). These are the meshes drawing
   *  such materials, including the near-arm visibility overlays whose clones are translucent frame too, and, per
   *  material, a twin (translucent-twin.ts: transmission 0, a straight look-through mixed in where Three mixes its
   *  refraction, same shader wrappers). The twins replace them in every pass and look through to the owned
   *  look-through image (translucent-look-through.ts): the camera behind the opaque eyewear, so hardware inside the
   *  crystal is seen, sharp. Inside Three's lens input they instead sample the paired background, excluding the
   *  captured arm hardware. Swapping
   *  materials keeps both programs cached; toggling `transmission` across zero would rebuild the program. */
  private translucentFrameMeshes: {mesh: Mesh; original: Material | Material[]}[] = [];
  private translucentTwins = new Map<MeshPhysicalMaterial, MeshPhysicalMaterial>();
  /** The twins' shared look-through inputs, bound per frame: the look-through image, or the background the arm clip samples. */
  private readonly twinUniforms = createCameraTransmissionUniforms();
  /** The owned look-through image for an asset with classified translucent frame materials. */
  private lookThrough: TranslucentLookThrough | null = null;
  /** All optical materials are left out of the look-through image. */
  private lookThroughHidden: Material[] = [];
  /** A legacy transmissive lens samples Three's internal pre-pass, which then stays lens input (arms excluded). */
  private legacyTransmissiveOptics = false;
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
  private lastPose: {rawMatrix: number[]; eyewearMatrix: number[]; yawDegrees: number} | null = null;
  private readonly stabilizer: PoseStabilizer | null;
  private latestPose: PoseSample | null = null;
  private maskWarp: MaskWarp | null = null;
  private uploadedCategory: Uint8Array | null = null;
  private uploadedHairIndex = -1;
  private readonly templeKeepCm = TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm;
  private readonly templeDropCm = TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm;
  /** Set by poseCamera (a dev capture without a face), cleared by the next pose(): the eyewear is drawn although no face
   *  is tracked, and the camera's projection is the explicit one. The try-on never sets it. */
  private explicitCamera = false;

  private constructor(renderer: WebGLRenderer, gl: WebGL2RenderingContext, model: ResolvedModel, options: RendererOptions,
    report?: ModelReportBuilder) {
    this.renderer = renderer; this.gl = gl; this.model = model;
    this.modelReport = report ?? new ModelReportBuilder(model.url, model.displayName, model.warnings);
    this.fitSession = options.fitSession ?? new EyewearFitSession();
    this.clipZM = model.templeClipZM ?? TEMPLE_CLIP_FLOOR_LOCAL_Z_M;
    this.fixedScale = options.fixedScale === true;
    if (this.fixedScale) {
      // Nothing to settle: the model is shown at its authored size from the first pose.
      this.fitRevealed = true;
      this.modelReport.measure('fit_scale', {mode: 'fixed', applied: 1});
    } else this.modelReport.measure('fit_scale', {mode: 'auto', applied: 1, state: 'collecting'});   // replaced once the fit settles
    const steady = options.steady === undefined ? DEFAULT_STEADY : options.steady;
    this.stabilizer = steady ? new PoseStabilizer(steady) : null;
    this.hairStartZ = options.hairStartZ ?? DEFAULT_HAIR_START_Z_M; this.sync = options.sync ?? true; this.guard = options.guard ?? true;
    this.scene.background = new Color(0x080b10);
    this.facePose.name = 'Tracked canonical face (centimeters)'; this.facePose.matrixAutoUpdate = false; this.facePose.visible = false; this.scene.add(this.facePose);
    this.eyewearPose.name = 'Eyewear bridge pose (centimeters)'; this.eyewearPose.matrixAutoUpdate = false; this.eyewearPose.visible = false; this.scene.add(this.eyewearPose);
  }
  get guardEnabled(): boolean {return this.guard;}
  get nativeSamples(): number {const samples: unknown = this.gl.getParameter(this.gl.SAMPLES); return typeof samples === 'number' ? samples : 0;}
  get gpuRenderer(): string | null {
    const debug = this.gl.getExtension('WEBGL_debug_renderer_info');
    const value: unknown = this.gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : this.gl.RENDERER);
    return typeof value === 'string' ? value : null;
  }
  get templeClipConfiguration(): TempleClipConfiguration {return createTempleBlendConfiguration(this.clipZM);}
  /** The temple clip plane in use (model-local metres): the handed-over clip, else the one derived from the model's own
   *  temples (the report's `temple_clip` measurement says which). */
  get templeClipZM(): number {return this.clipZM;}
  /** The temples by position, per side (temple-geometry.ts), or null before a model is configured. A copy. */
  temples(): TempleInventory | null {return this.templeSides ? structuredClone(this.templeSides) : null;}
  /** The model report (model/report.ts): status, the seven §2 rows, warnings, fallbacks (render-time ones included),
   *  lens identification, measurements, the rendered bytes' SHA-256 and what the live site would reject. A copy. */
  report(): ModelReport {return this.modelReport.snapshot('rendered');}
  /** Record a fallback a caller took on this model's behalf (the live renderer's failed audit). It is a warning too. */
  recordFallback(subsystem: string, detail: string): void {this.modelReport.fallback(subsystem, detail, 'render');}
  /** The lens identification of the loaded model (class, deciding signal and uses of every glTF material, the §2
   *  Components verdict), or null before a model is configured. A copy. */
  lensIdentification(): LensIdentificationReport | null {
    return this.lensIdentificationReport ? structuredClone(this.lensIdentificationReport) : null;
  }
  /** The posed frame's virtual camera, or null before the first pose. Copies. */
  cameraState(): CameraState | null {
    if (!this.frameWidth || this.disposed) return null;
    this.camera.updateMatrixWorld();
    const {width, height} = this.renderSize;
    return {view: this.camera.matrixWorldInverse.toArray(), projection: this.camera.projectionMatrix.toArray(), width, height,
      near: this.camera.near, far: this.camera.far,
      // an explicit camera (poseCamera) sets the projection itself: its vertical field of view is 2 atan(1 / P[1][1])
      verticalFovDegrees: this.explicitCamera ? 2 * Math.atan(1 / this.camera.projectionMatrix.elements[5]!) * 180 / Math.PI : this.camera.fov};
  }
  /** Asset-local metres to the scene (centimetres, camera space), as drawn: the bridge pose, the bridge offset and the
   *  fit scale included. Null before the first pose. Column-major, a copy. */
  assetMatrixWorld(): number[] | null {
    if (!this.lastPose || !this.eyewearRoot || this.disposed) return null;
    this.scene.updateMatrixWorld(true);
    return this.eyewearRoot.matrixWorld.toArray();
  }
  /** The arm centrelines as drawn: the continuity stations (hinge first), moved by the same fixed spread and terminal
   *  return as the arm geometry, as [x, y, z] asset-local metres per side (empty for a side without a path); with the
   *  plane where the deliberate return behind the ear begins (null without a terminal fit) and each side's live cap.
   *  For a capture to measure how far back each arm is actually drawn (never a silent cut). Null without a continuity
   *  model or arm shape. Copies. */
  templeCentrelines(): TempleCentrelines | null {
    const model = this.continuityModel, shape = this.rearDrop;
    if (!model || !shape || this.disposed) return null;
    const fit = this.templeTerminalFit, spreadStartZM = shape.spreadStartZM, spreadM = shape.spreadM;
    const sides = model.sides.map(stations => {
      const points = new Float32Array(stations.length * 3);
      stations.forEach((station, i) => points.set([terminalFitX(spreadArmX(station.centerXM, station.zM, spreadStartZM, model.cutoffZM, spreadM),
        station.zM, fit), station.centerYM, station.zM], i * 3));
      return points;
    });
    const clip = this.templeClip?.configuration, maximum = this.templeEndMaximumZM;
    return {sides: [sides[0] ?? new Float32Array(0), sides[1] ?? new Float32Array(0)], returnStartZM: fit?.startZM ?? null,
      capZM: [clip?.negativeXCutoffLocalZM ?? maximum, clip?.positiveXCutoffLocalZM ?? maximum]};
  }
  /** Every eyewear mesh with its decided class and its side by geometry (overlays flagged). */
  parts(): RenderedPart[] {
    const root = this.eyewearRoot, found: RenderedPart[] = [];
    if (!root || this.disposed) return found;
    const box = new Box3();
    root.traverse(object => {
      if (!(object instanceof Mesh)) return;
      const overlay = object.userData.templeVisibilityOverlay === true;
      const decided = meshClassOf(object) ?? 'frame';
      box.setFromBufferAttribute(object.geometry.getAttribute('position') as BufferAttribute);
      const side: PartSide = box.max.x <= 0 ? 'right' : box.min.x >= 0 ? 'left' : 'centre';
      found.push({mesh: object, name: this.partNames.get(object) ?? object.name, class: decided, side, overlay});
    });
    return found;
  }
  /** Project asset-local metres (xyz triples) to render pixels: [x, y (top-down), NDC z] per point, NaN for a point at or
   *  behind the camera. Same matrices as the draw. Null before the first pose. */
  projectAssetPoints(points: Float32Array): Float32Array | null {
    const matrix = this.assetMatrixWorld();
    if (!matrix || points.length % 3) return null;
    const {width, height} = this.renderSize;
    const transform = this.camera.projectionMatrix.clone().multiply(this.camera.matrixWorldInverse).multiply(new Matrix4().fromArray(matrix));
    const out = new Float32Array(points.length), clip = new Vector4();
    for (let i = 0; i < points.length; i += 3) {
      clip.set(points[i]!, points[i + 1]!, points[i + 2]!, 1).applyMatrix4(transform);
      if (!(clip.w > 0)) {out[i] = out[i + 1] = out[i + 2] = NaN; continue;}
      out[i] = (clip.x / clip.w + 1) * width / 2; out[i + 1] = (1 - clip.y / clip.w) * height / 2; out[i + 2] = clip.z / clip.w;
    }
    return out;
  }
  /** Replace the environment the eyewear reflects (lighting.ts), or restore the runtime's own with null. Only environment
   *  maps and the illumination multiplier change: the scene, native lenses that own a map (lensenv, scaled the same) and
   *  every translucent frame material, its near-arm overlay clones and twins included. Throws for an unusable config. */
  setLighting(config: LightingConfig | null): void {
    if (this.disposed) throw new Error('The renderer is disposed.');
    if (this.lighting) {this.lighting.undo(); this.lighting.maps.dispose(); this.lighting = null;}
    if (config === null) return;
    const maps = createLightingMaps(this.renderer, config);
    const scene = this.scene, previous = {environment: scene.environment, intensity: scene.environmentIntensity};
    const crystal = new Set<MeshPhysicalMaterial>(), lenses = new Set<MeshPhysicalMaterial>();
    for (const mesh of this.lensMeshes) for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (material instanceof MeshPhysicalMaterial && material.envMap) lenses.add(material);
    }
    for (const {original} of this.translucentFrameMeshes) for (const material of Array.isArray(original) ? original : [original]) {
      if (material instanceof MeshPhysicalMaterial && isTranslucentFrameMaterial(material)) crystal.add(material);
    }
    for (const twin of this.translucentTwins.values()) crystal.add(twin);
    const saved = [...lenses, ...crystal].map(material => ({material, map: material.envMap, intensity: material.envMapIntensity}));
    scene.environment = maps.scene.texture; scene.environmentIntensity = config.intensity;
    for (const {material, intensity} of saved) {
      if (crystal.has(material)) {material.envMap = maps.crystal.texture; material.envMapIntensity = config.intensity;}
      else {material.envMap = maps.scene.texture; material.envMapIntensity = config.intensity * intensity / SCENE_ENVIRONMENT_INTENSITY;}
      material.needsUpdate = true;
    }
    this.lighting = {maps, undo: () => {
      scene.environment = previous.environment; scene.environmentIntensity = previous.intensity;
      for (const {material, map, intensity} of saved) {material.envMap = map; material.envMapIntensity = intensity; material.needsUpdate = true;}
      // A twin cloned while the lighting was applied returns to its original's restored map.
      for (const [original, twin] of this.translucentTwins) {
        if (saved.some(entry => entry.material === twin)) continue;
        twin.envMap = original.envMap; twin.envMapIntensity = original.envMapIntensity; twin.needsUpdate = true;
      }
    }};
  }
  /** The protection for the posed frame (render pixels), or null when it could not be established. */
  get protection(): ProtectionConfiguration | null {return this.protectionConfiguration ? structuredClone(this.protectionConfiguration) : null;}
  get noseRoi(): PixelRect | null {return this.nasalRect ? {...this.nasalRect} : null;}
  /** The projected arm centrelines for the posed frame, from the normalised scene (null when continuity fell back,
   *  see `endpointUnavailable` and the report, or before a pose). */
  get paths(): ProjectedTemplePath[] | null {return this.templePaths;}
  get endpointUnavailable(): string | null {return this.continuityFailure;}
  /** How the next `render` reads its mask: null for the frame's own mask, or a mask reused from an earlier frame placed
   *  by the head's motion (hair/mask-reuse.ts). It applies to shader lookups and hair endpoints alike, and stays
   *  until changed, so an audit's variants of the held frame read the mask the way the live frame did. */
  setMaskWarp(warp: MaskWarp | null): void {this.maskWarp = warp;}
  setShadows(settings: Partial<ShadowSettings>): void {
    this.shadowSettings = normalizeShadowSettings({...this.shadowSettings, ...settings});
    this.shadowPending = true;
  }
  get shadowsSettings(): ShadowSettings {return {...this.shadowSettings};}
  get fitting(): FittingReport {
    const report = this.fitSession.report;
    if (this.fixedScale) return {...report, state: 'fitted', progress: 1, adjustment: 0, rejection: null, scale: 1, limited: false,
      ready: this.fitRevealed, mode: 'fixed'};
    return {...report, scale: this.fitScale, limited: this.fitLimited || report.limited, ready: this.fitRevealed, mode: 'auto'};
  }
  refit(): void {
    this.fitSession.reset(); this.fitLimited = false; this.fitRevealed = this.fixedScale;
    this.eyewearPose.visible = false; this.shadowPending = true;
  }
  /** A manual size adjustment of the auto-fit. With fixedScale it is ignored (reported once): the size stays authored. */
  setFitAdjustment(value: number): void {
    if (!this.fixedScale) {this.fitSession.setAdjustment(value); return;}
    if (!this.adjustmentIgnored) {
      this.adjustmentIgnored = true;
      this.modelReport.warn('The size adjustment is ignored: this renderer draws the model at its authored size (fixedScale).');
    }
  }
  /** The posed frame's raw and steadied orientation and depth (numbers only), or null when no face is posed. */
  get poseSample(): PoseSample | null {return this.latestPose ? {...this.latestPose} : null;}
  /** The frame report's temple-end text: the endpoint states, or 'fixed cap' without them; at an explicit camera
   *  (poseCamera) the arms are drawn as authored, with no cap or clip, and the text says so. */
  private get templeEndStateText(): string {
    if (this.explicitCamera) return this.photoTemplePose?.active
      ? 'photo opening (explicit camera: frozen hinge deltas, no spread, terminal return or clip)'
      : 'authored (explicit camera: no spread, terminal return or clip)';
    return this.templeEndpointReport ? `${this.templeEndpointReport.negativeState}/${this.templeEndpointReport.positiveState}` : 'fixed cap';
  }
  private get templeEndMaximumZM(): number {
    const original = this.clipZM;
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
    return projectTempleContinuity(this.continuityModel, {eyewearMatrix, offsetCm: this.model.offsetCm,
      sourceAspect: this.frameWidth / this.frameHeight, width, height, dropM: 0,
      // The endpoint probe and the drawn vertices share the fixed spread and the asset's actual hinge plane.
      spreadM: ARM_SPREAD_M, spreadStartZM: this.rearDrop?.spreadStartZM, terminalFit: this.templeTerminalFit, fitScale: this.fitScale});
  }
  /** Set once the completion gate was switched off because the previous frame's fence never signalled. */
  get syncUnavailable(): string | null {return this.syncFailure;}
  get renderSize(): {width: number; height: number} {
    // Photo comparisons retain native detail; live webcam performance keeps its existing cap.
    const width = Math.min(this.frameWidth, this.explicitCamera ? 4096 : MAX_RENDER_WIDTH);
    return {width, height: Math.max(1, Math.round(width * this.frameHeight / Math.max(1, this.frameWidth)))};
  }
  get captureSnapshot(): CaptureGeometry | null {
    const pose = this.lastPose; if (!pose || this.disposed) return null;
    return {eyewearModelId: this.model.url, rawMatrix: pose.rawMatrix.slice(), eyewearMatrix: pose.eyewearMatrix.slice(), yawDegrees: pose.yawDegrees,
      templeTerminalFit: this.templeTerminalFit ? {...this.templeTerminalFit} : null,
      templeHeadFit: this.templeHeadFit?.report ?? null,
      fitting: this.fitting,
      templeClip: this.templeClip?.configuration ?? null, hairStartZ: this.hairStartZ,
      protection: this.protection, noseRoi: this.noseRoi};
  }

  /** Record the settled auto-fit scale in the report (`fit_scale`), and warn once per distinct scale that is more than
   *  FIT_SCALE_WARN_FRACTION from 1 (WP1_DESIGN.md §4: what is drawn is then not the authored size). The wording serves
   *  the webcam page and the captures alike. */
  private noteFitScale(): void {
    const scale = this.fitScale, key = scale.toFixed(3), session = this.fitSession.report, limited = this.fitLimited || session.limited;
    const noted = `${key}|${session.adjustment}|${limited}`;
    if (noted === this.notedFitScale) return;
    this.notedFitScale = noted;
    this.modelReport.measure('fit_scale', {mode: 'auto', applied: Math.round(scale * 1e4) / 1e4, state: session.state,
      face_width_cm: session.faceWidthCm === null ? null
      : Math.round(session.faceWidthCm * 1e3) / 1e3, front_width_cm: Math.round(this.fitFrontWidthCm * 1e3) / 1e3,
      adjustment: session.adjustment, limited});
    if (Math.abs(scale - 1) > FIT_SCALE_WARN_FRACTION && !this.fitScaleWarnings.has(key)) {
      this.fitScaleWarnings.add(key);
      const percent = (scale - 1) * 100;
      this.modelReport.warn(`FIT SCALE: the live fit draws this model at ×${key} its authored size (${percent >= 0 ? '+' : ''}${percent.toFixed(1)} %; the `
        + `auto-fit to the face is clamped to ×${EYEWEAR_FIT.minimumAutoScale}-${EYEWEAR_FIT.maximumAutoScale} and multiplied by the `
        + `${EYEWEAR_FIT.defaultFitPreference} fit preference${session.adjustment ? `, size adjustment ${session.adjustment >= 0 ? '+' : ''}${session.adjustment}` : ''}). `
        + 'What is drawn is not the authored size; captures can render at the authored size with --fixed-scale.');
    }
  }

  /** Normalize upper-face proportions to the fitting reference view; this frame's baseline projection
   * supplies a visibility check. Calibration never observes its own resized output. */
  private updateFitting(detection: Detection, attachment: readonly number[], timestampMs: number): void {
    if (!this.eyewearAsset || !this.fitFrontBounds || !this.observedCheekAttribute || !(this.fitFrontWidthCm > 0) || !detection.matrix) return;
    if (this.fitSession.report.faceWidthCm === null) {
      const projection = protectionProjection(attachment, this.model.offsetCm, this.camera.aspect);
      const bounds = this.fitFrontBounds;
      let minX = Infinity, maxX = -Infinity, valid = true;
      for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
        const point = this.fitProjected.set(x, y, z, 1).applyMatrix4(projection);
        if (point.w <= this.camera.near || !point.toArray().every(Number.isFinite)) {valid = false; continue;}
        const screenX = .5 + .5 * point.x / point.w;
        minX = Math.min(minX, screenX); maxX = Math.max(maxX, screenX);
      }
      this.fitSession.observe(observeFitWidth({landmarks: detection.landmarks, rawMatrix: detection.matrix,
        baselineFrontWidth: valid ? maxX - minX : 0, frontWidthCm: this.fitFrontWidthCm,
        observedPositions: this.observedCheekAttribute.array as Float32Array,
        canonicalPositions: this.canonicalPositions, sourceAspect: this.camera.aspect}), timestampMs);
    }
    const scale = this.fitSession.scaleFor(this.fitFrontWidthCm, FIT_FRONT_RATIO, timestampMs);
    if (!Number.isFinite(scale)) return;
    if (Math.abs(scale - this.fitScale) < 1e-6) {this.fitLimited = false; return;}
    const terminal = this.terminalFitAtScale?.(scale) ?? null;
    // Unsupported geometry retains its existing shortened cap. A model with a certified buried
    // return must keep that containment guarantee throughout the fitting transition.
    if (this.templeTerminalFit && !terminal) {this.fitLimited = true; return;}
    this.fitLimited = false;
    this.fitScale = scale;
    this.eyewearAsset.scale.setScalar(GLASSES_METERS_TO_CENTIMETERS * scale);
    this.templeTerminalFit = terminal;
    this.shadowPending = true;
  }

  /** Load `model` and build the renderer for it. Unusable definition values fall back with a warning (catalog.ts). A
   *  §2 refusal rejects with ModelRefusal, whose `report` is the whole model report (status 'refused'); a file this
   *  environment cannot check (Draco without a Web Worker) rejects with ModelUnchecked (status 'unchecked', no verdict). */
  static async create(canvas: HTMLCanvasElement, signal: AbortSignal, model: ModelDefinition, options: RendererOptions = {}): Promise<TryOnRenderer> {
    if (signal.aborted) throw abortError();
    const resolved = resolveModelDefinition(model);
    const report = new ModelReportBuilder(resolved.url, resolved.displayName, resolved.warnings);
    const loading = new AbortController();
    let instance: TryOnRenderer | null = null, gltf: GLTF | null = null, webgl: WebGLRenderer | null = null, context: WebGL2RenderingContext | null = null;
    const onAbort = () => {loading.abort(); instance?.dispose();};
    signal.addEventListener('abort', onAbort, {once: true});
    try {
      const [glasses, face] = await Promise.all([
        fetchAsset(resolved.url, loading.signal).then(response => response.arrayBuffer()),
        fetchAsset(assetPath('models/canonical-face.json'), loading.signal).then(response => response.json()).then(validateFace),
      ]);
      if (signal.aborted) throw abortError();
      // The WebGLRenderer exists before the GLB is parsed: KTX2Loader detects the GPU's compressed formats from it.
      // The guard needs a stencil buffer on the default framebuffer. The WebGLRenderer itself is created without one:
      // with `stencil: true` Three also gives its lens-transmission render target a stencil buffer (cleared to 0), and
      // the opaque scene then fails the EQUAL-1 test inside that target during the protected pass.
      context = canvas.getContext('webgl2', {alpha: false, antialias: true, stencil: true, powerPreference: 'high-performance'});
      if (!context) throw new Error('WebGL 2 is unavailable on this browser.');
      webgl = new WebGLRenderer({canvas, context, alpha: false, antialias: true, stencil: false});
      // The rendered bytes, their SHA-256 and the Format row (model/load.ts); every later check binds to these bytes.
      const loaded = await loadModel(glasses, report, {url: resolved.url, renderer: webgl, signal: loading.signal,
        ...options.load, handoverSha256: options.load?.handoverSha256 ?? resolved.handoverSha256 ?? null});
      gltf = loaded.gltf;
      if (signal.aborted) throw abortError();
      instance = new TryOnRenderer(webgl, context, resolved, options, report);
      instance.removeAbortListener = () => signal.removeEventListener('abort', onAbort);
      instance.assetScenes = gltf.scenes;
      const eyewearScene = gltf.scene; gltf = null;
      instance.configure(face, eyewearScene, loaded.source);
      if (signal.aborted) throw abortError();
      return instance;
    } catch (error) {
      loading.abort(); signal.removeEventListener('abort', onAbort);
      if (instance) instance.dispose(); else {webgl?.dispose(); context?.getExtension('WEBGL_lose_context')?.loseContext();}
      if (gltf) disposeObjects(gltf.scenes);
      throw signal.aborted ? abortError() : error;
    }
  }

  /** Build everything for the loaded scene. `source` is the glTF the scene was parsed from (names and lens
   *  identification read its JSON); hand-built test scenes omit it and are identified from their userData (the report's
   *  source says so). The normalisation pass runs first and may throw ModelRefusal; every subsystem after it is built in
   *  its own try block and falls back, reported, when it cannot be. */
  private configure(face: CanonicalFace, eyewearScene: Group, source: GltfSource | null = null): void {
    this.renderer.setPixelRatio(1); this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1;
    this.canonicalPositions = face.positions; this.nasalShape = createNasalShape(face.positions, face.indices);
    try {this.templeHeadFit = new TempleHeadFit(face.positions);}
    catch (error) {
      this.templeHeadFit = null;
      this.fallback('temple head fit', `The head-width fit of the face occluder is unavailable (${messageOf(error)}); the canonical head width is used.`);
    }
    const asset = new Group(); asset.name = `${this.model.displayName} bridge attachment`;
    this.eyewearAsset = asset;
    asset.scale.setScalar(GLASSES_METERS_TO_CENTIMETERS); asset.position.set(...this.model.offsetCm);
    // The one normalisation pass (WP1_DESIGN.md §1): the retired lens descriptor stripped, transforms, geometry, units,
    // lens identification (every material's class decided once, read by every consumer below) and the measured §2 rows.
    // A §2 refusal throws here.
    const normalized = normalizeModel(eyewearScene, this.modelReport, source);
    this.photoTemplePose = createPhotoTemplePose(eyewearScene);
    this.eyewearRoot = eyewearScene;
    eyewearScene.traverse(object => {if (object instanceof Mesh) this.partNames.set(object, nodeName(object, source));});
    this.lensIdentificationReport = normalized.classification.lensIdentification;
    // The asset is scaled to centimetres below; volume attenuation distances are metres in the file (see eyewear-volume.ts).
    applyVolumeAttenuationScale(eyewearScene, GLASSES_METERS_TO_CENTIMETERS);
    eyewearScene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        applyTransmissionToneMapping(material);
      }
    });
    asset.add(eyewearScene); this.eyewearPose.add(asset);
    eyewearScene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      if (materials.some(isOpticalMaterial)) this.lensMeshes.push(object);
    });
    try {this.templeClip = createTempleClip(eyewearScene);}
    catch (error) {this.templeClip = null; this.fallback('temple clip', `The temple clip could not be installed (${messageOf(error)}); the arms are drawn to their full length.`);}
    this.cheekContact = new TempleCheekContactEstimator(face.positions, face.indices);
    this.observedCheekGeometry = new BufferGeometry();
    this.observedCheekAttribute = new BufferAttribute(new Float32Array(face.positions.length), 3).setUsage(DynamicDrawUsage);
    this.observedCheekGeometry.setAttribute('position', this.observedCheekAttribute);
    this.observedCheekGeometry.setIndex(face.indices);
    this.shadowReceiverGeometry = new BufferGeometry();
    // Rasterize today's observed image rays, but look up shadows on a stable local face
    // attached to the glasses. This does not change the cheek or head occluder geometry.
    this.shadowReceiverGeometry.setAttribute('position', this.observedCheekAttribute);
    this.shadowReceiverGeometry.setIndex(face.indices);
    this.shadowReceiverAttribute = new BufferAttribute(new Float32Array(face.positions.length), 3).setUsage(DynamicDrawUsage);
    this.shadowReceiverGeometry.setAttribute('shadowPosition', this.shadowReceiverAttribute);
    // The temples by position, per side, and the clip derived from them (WP1_DESIGN.md §3), before anything that cuts or
    // shapes an arm reads the clip.
    const sides = this.installTemples(eyewearScene);
    try {this.rearDrop = createRearDrop(eyewearScene, this.clipZM, 0, {sides});}
    catch (error) {
      this.rearDrop = null;
      this.fallback('rear drop', `The fixed arm shape could not be built (${messageOf(error)}): the arms are drawn without the fixed spread and `
        + 'terminal return, the fit width is measured on the front 25 mm, and hair occlusion stays off (no protection geometry).');
    }
    if (this.rearDrop) this.noteArmSpread(this.rearDrop);
    this.fitFrontBounds = this.rearDrop?.opticalBounds ?? frontRegionBounds(eyewearScene);
    this.fitFrontWidthCm = (this.fitFrontBounds.max.x - this.fitFrontBounds.min.x) * GLASSES_METERS_TO_CENTIMETERS;
    // Capture original terminal samples before any fit/spread modifies the cloned geometry.
    if (this.rearDrop && (sides[0] || sides[1])) {
      try {
        const evaluator = createTempleTerminalFitEvaluator(eyewearScene, {offsetCm: this.model.offsetCm,
          spreadM: ARM_SPREAD_M, spreadStartZM: this.rearDrop.spreadStartZM,
          modelCutoffZM: this.clipZM, maximumZM: this.templeEndMaximumZM, sides});
        this.terminalFitAtScale = evaluator;
        this.templeTerminalFit = evaluator(1);
        if (evaluator.unavailable) {
          this.fallback('terminal fit', `The fixed temple return is unavailable (${evaluator.unavailable}); the arms keep the shortened cap.`);
        } else if (!this.templeTerminalFit) {
          this.fallback('terminal fit', 'The fixed temple return cannot be contained in the head volume at the authored size; the arms keep the '
            + 'shortened cap at that size.');
        }
      } catch (error) {
        this.terminalFitAtScale = null; this.templeTerminalFit = null;
        this.fallback('terminal fit', `The fixed temple return could not be fitted (${messageOf(error)}); both arms keep the shortened cap.`);
      }
    }
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
    this.installArmShaders(eyewearScene);
    try {this.shadows = new EyewearShadow(this.renderer, eyewearScene, this.shadowReceiverGeometry, GLASSES_METERS_TO_CENTIMETERS * 1.30);}
    catch (error) {this.shadows = null; this.fallback('shadows', `The eyewear shadows could not be built (${messageOf(error)}); the frame draws without shadows.`);}
    const key = new DirectionalLight(0xffffff, 2); key.position.set(-10, 15, 20); this.scene.add(key);
    const environment = new RoomEnvironment(); let generator: PMREMGenerator | null = null;
    let seeThrough: RoomEnvironment | null = null;
    try {
      generator = new PMREMGenerator(this.renderer);
      this.environmentTarget = generator.fromScene(environment, 0.04);
      this.scene.environment = this.environmentTarget.texture; this.scene.environmentIntensity = SCENE_ENVIRONMENT_INTENSITY;
      // Crystal (translucent frame materials) reflects the see-through room: the scene's room without the front panel
      // behind the selfie camera, which was the white slab and milky wash on the crystal (eyewear-reflection.ts).
      // Unblurred: the scene room's 0.04 pre-blur is a second roughness floor on top of three's 0.0525, under which the
      // owner's "glossy" crystal edits (roughness 0.085 -> 0.02 -> 0) could not change a pixel. Native lenses, opaque
      // acetate and metal keep the scene room above (the owner-judged look).
      if (this.translucentFrameMeshes.length) {
        seeThrough = createSeeThroughRoom();
        this.seeThroughEnvironmentTarget = generator.fromScene(seeThrough, 0);
      }
    }
    finally {generator?.dispose(); environment.dispose(); seeThrough?.dispose();}
    // A material lit by scene.environment has its envMapIntensity overwritten with scene.environmentIntensity at every
    // render (three r185, WebGLRenderer.setProgram), so setting envMapIntensity alone has no effect. The lens materials
    // own the environment map at SCENE_ENVIRONMENT_INTENSITY times lensEnvIntensity (1 = unchanged).
    const lensEnvIntensity = this.model.lensEnvIntensity;
    if (lensEnvIntensity !== undefined && this.environmentTarget) {
      for (const mesh of this.lensMeshes) {
        for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
          if (material instanceof MeshPhysicalMaterial && isOpticalMaterial(material)) {material.envMap = this.environmentTarget.texture; material.envMapIntensity = SCENE_ENVIRONMENT_INTENSITY * lensEnvIntensity;}
        }
      }
    }
    // Every translucent frame material, the near-arm overlay clones included (installArmShaders collected them), owns the
    // see-through room at the scene's intensity before any twin is cloned from it (twinOf, at the first render; the clone
    // copies envMap and envMapIntensity). lensenv is a lens setting and does not apply.
    if (this.seeThroughEnvironmentTarget) for (const {original} of this.translucentFrameMeshes) {
      for (const material of Array.isArray(original) ? original : [original]) {
        if (!(material instanceof MeshPhysicalMaterial) || !isTranslucentFrameMaterial(material)) continue;
        material.envMap = this.seeThroughEnvironmentTarget.texture; material.envMapIntensity = SCENE_ENVIRONMENT_INTENSITY;
      }
    }
    // The arm centrelines for the hair endpoint, from this same normalised scene (no second parse, no pin): built last,
    // like the separate load it replaces, on geometry nothing has posed yet. Each side stands alone: a side without a
    // posterior cross-section keeps the fixed buried cap (reported for that side); only no usable side at all turns the
    // endpoint off.
    try {
      const continuity = buildTempleContinuityModel(eyewearScene, this.clipZM, {sides});
      for (const gap of continuity.missing ?? []) {
        // A side without a temple is already reported as a missing temple; an arm without a cross-section is new.
        if (!sides[gap.side]) continue;
        this.fallback('continuity', `Temple continuity, ${TEMPLE_SIDE_NAMES[gap.side]} arm: ${gap.reason}. That arm keeps the fixed buried cap and `
          + 'gets no hair endpoint.');
      }
      if (continuity.sides.every(stations => stations.length === 0)) {
        this.continuityFailure = (continuity.missing ?? []).map(gap => gap.reason).join('; ');
        this.fallback('continuity', `The temple continuity model has no usable arm (${this.continuityFailure}); both arms keep the fixed buried cap and `
          + 'no hair endpoint.');
      } else {
        this.continuityModel = {...continuity, assetUrl: this.model.url,
          ...(this.modelReport.renderedSha256 ? {assetSHA256: this.modelReport.renderedSha256} : {})};
      }
    } catch (error) {this.noteContinuityFailure(error);}
  }

  /** The temples by position, per side, and the clip derived from them (temple-geometry.ts, WP1_DESIGN.md §3), all
   *  written into the report: `temples` and `temple_clip` measurements; a 'temple <side>' fallback for a side without a
   *  temple (that side keeps a fixed cap: no arm shape, continuity, terminal return or hair endpoint; the §2 Orientation
   *  row fails on its own and is never hidden by this); and every measurement or clip warning. Returns which sides carry
   *  a temple. */
  /** The arm spread's pivot in the report (measurement `arm_spread`), and a warning when it differs from main's ar/: main
   *  (ar/src/render/rear-drop.ts) pivots at the arm's shaft boundary wherever it lies, arview only when it lies in front
   *  of the 15 mm proximal guard. A tall temple (a shield or wide acetate arm, such as the Prada PR17WS: boundary
   *  -73 mm) collapses far behind the guard; main leaves the whole front of such an arm unspread, inside the head proxy,
   *  so the live site draws it only to the shield in turned views, while arview spreads it from the guard and draws it
   *  (WP1 review E/F: the capture said nothing about this divergence). */
  private noteArmSpread(rear: NonNullable<TryOnRenderer['rearDrop']>): void {
    const mm = (metres: number | null): number | null => metres === null ? null : Math.round(metres * 1e5) / 100;
    const {startZM} = rear.diagnostics, boundary = rear.shaftBoundaryZM;
    this.modelReport.measure('arm_spread', {pivot_z_mm: mm(rear.spreadStartZM), hinge_z_mm: mm(rear.hingeZM), shaft_boundary_z_mm: mm(boundary),
      guard_z_mm: mm(startZM), pivot_at: rear.hingeZM !== null ? 'hinge' : 'guard', live_pivot_z_mm: mm(boundary ?? startZM)});
    if (boundary !== null && rear.hingeZM === null) {
      this.modelReport.warn(`LIVE SITE DIFFERS (arm spread): this arm's cross-section first collapses to a thin shaft at z ${mm(boundary)!.toFixed(1)} mm, `
        + `behind the proximal guard (z ${mm(startZM)!.toFixed(1)} mm, 15 mm behind the lens rear). arview spreads the arm from the guard; main's ar/ `
        + '(src/render/rear-drop.ts) pivots at that shaft boundary, so on the live site the arm stays unspread in front of it, inside the head proxy, '
        + 'and is drawn only to its front part in turned views. The captures here show the arm drawn; the live site would not.');
    }
  }

  private installTemples(root: Object3D): readonly [boolean, boolean] {
    let inventory: TempleInventory;
    try {inventory = templeInventory(root);}
    catch (error) {
      // A pure measurement of the normalised scene; should it fail anyway, both sides are treated as temples (as before
      // stage D2) and the clip stays at the handover or the floor, reported.
      this.fallback('temples', `The temples could not be measured (${messageOf(error)}); both sides are treated as temples and the clip stays at `
        + `${this.clipZM.toFixed(3)} m.`);
      return [true, true];
    }
    this.templeSides = inventory;
    for (const warning of inventory.warnings) this.modelReport.warn(`Temples: ${warning}.`);
    // The sentences come from temple-geometry.ts (templeLoadFallbacks), so arview serve's Node-side check reports these same fallbacks.
    for (const side of inventory.sides) if (!side.present) this.fallback(`temple ${side.side}`, missingTempleDetail(side));
    const clip = deriveTempleClip(inventory, this.model.templeClipZM);
    this.clipZM = clip.clipZM;
    for (const warning of clip.warnings) {
      if (clip.source === 'default') this.fallback('temple clip depth', defaultClipDetail(warning));
      else this.modelReport.warn(`Temple clip: ${warning}.`);
    }
    const measured = templeMeasurements(inventory, clip);
    this.modelReport.measure('temples', measured.temples);
    this.modelReport.measure('temple_clip', measured.temple_clip);
    return inventory.present;
  }

  /** A fallback of this model: a fallbacks[] entry and a warning (model/report.ts). */
  private fallback(subsystem: string, detail: string, stage: 'load' | 'render' = 'load'): void {this.modelReport.fallback(subsystem, detail, stage);}

  /** The near-arm visibility overlays, the translucent frame meshes pass B swaps to their twins, and hair occlusion, in
   *  the order their shader wrappers nest (the temple clip is installed first). Needs the observed cheek geometry. Each
   *  is built in its own try block: a failure turns that one feature off, reported. */
  private installArmShaders(eyewearScene: Object3D): void {
    try {
      this.templeVisibility = createTempleVisibility(eyewearScene, {renderer: this.renderer, scene: this.scene, camera: this.camera,
        eyewearPose: this.eyewearPose, observedFaceSurface: this.observedCheekGeometry ?? undefined,
        lensInputUniform: this.twinUniforms.twinLensInput});
    } catch (error) {
      this.templeVisibility = null;
      this.fallback('temple visibility', `The near-arm visibility overlays could not be built (${messageOf(error)}); the arms are drawn without the `
        + 'side-depth relief and observed-cheek occlusion.');
    }
    // After the visibility overlays exist: a crystal arm's overlay draws a transmissive clone registered as frame, and
    // pass B must swap that clone to its twin too, or the overlay would transmit the clear colour in the corridors.
    eyewearScene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      if (materials.some(isTranslucentFrameMaterial)) this.translucentFrameMeshes.push({mesh: object, original: object.material});
    });
    // Authored crystal is frame. Its input includes internal hardware; native lenses still use Three's arm-excluded
    // pre-pass. The twins share that pass identity with temple visibility.
    this.legacyTransmissiveOptics = hasLegacyTransmissiveOptics(eyewearScene);
    if (this.translucentFrameMeshes.length) {
      try {
        this.lookThrough = new TranslucentLookThrough();
        const lenses = new Set<Material>();
        eyewearScene.traverse(object => {
          if (object instanceof Mesh) for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
            if (isOpticalMaterial(material)) lenses.add(material);
          }
        });
        this.lookThroughHidden = [...lenses];
      } catch (error) {
        this.lookThrough = null; this.lookThroughHidden = [];
        this.fallback('look-through', `The crystal look-through image could not be built (${messageOf(error)}); translucent frame parts draw their `
          + 'camera twins only inside the editable corridors and Three\'s own transmission elsewhere.');
      }
    }
    // Installed last so it wraps the clip and visibility hooks; its blend commutes with the clip's terminal blend.
    try {this.hairOcclusion = createHairOcclusion(eyewearScene);}
    catch (error) {this.hairOcclusion = null; this.fallback('hair occlusion', `Hair occlusion could not be installed (${messageOf(error)}); the arms are never hidden by hair.`);}
  }

  /** A pose whose protection could not be established draws without hair; each distinct reason is reported once. */
  private noteProtectionFailure(reason: string): void {
    if (this.protectionFailures.has(reason)) return;
    this.protectionFailures.add(reason);
    this.fallback('protection', `The optical/nasal protection could not be established for a frame (${reason}); such frames draw without `
      + 'hair occlusion.', 'render');
  }

  /** The continuity fallback: both arms keep the fixed buried cap and get no hair endpoint, and the report says why. */
  private noteContinuityFailure(error: unknown): void {
    this.continuityFailure = messageOf(error);
    this.fallback('continuity', continuityUnavailableDetail(this.continuityFailure));
  }

  /** SHA-256 of the exact bytes parsed and rendered (the report's rendered_sha256), or null for a hand-built scene. A
   *  digest alone is not a supplier attestation; nothing is pinned. */
  get renderedAssetIntegrity(): Readonly<{sha256: string}> | null {
    const sha256 = this.modelReport.renderedSha256;
    return sha256 ? {sha256} : null;
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
    const cameraModeChanged = this.explicitCamera;
    if (this.explicitCamera) {
      // Back from a camera without a face (poseCamera): the try-on's own virtual camera again.
      this.photoTemplePose?.reset();
      this.explicitCamera = false;
      this.camera.near = VIRTUAL_CAMERA.nearCm; this.camera.far = VIRTUAL_CAMERA.farCm; this.camera.updateProjectionMatrix();
    }
    this.poseTimestampMs = timestampMs; this.templeEndpointPending = false;
    this.clearPresentation(false);
    try {
      if (frame.width <= 0 || frame.height <= 0) throw new Error('The camera frame is empty.');
      if (cameraModeChanged || this.frameWidth !== frame.width || this.frameHeight !== frame.height) {
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
          this.shadowSurfaceValid = cheekDepthValid;
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
          // fixedScale: the authored size, nothing to fit (revealed from the start).
          if (cheekDepthValid && !this.fixedScale) {
            this.updateFitting(detection, attachment.matrix, timestampMs);
            // A shared session can still hold the previous model's settled scale. Evaluate this model first.
            if (this.fitSession.report.state === 'fitted') {this.fitRevealed = true; this.noteFitScale();}
          }
          if (this.shadowReceiverAttribute && this.observedCheekAttribute) {
            this.shadowSurfaceValid = cheekDepthValid && this.shadowReceiver.apply(this.observedCheekAttribute.array as Float32Array,
              matrix, attachment.matrix, timestampMs, this.shadowReceiverAttribute.array as Float32Array);
            this.shadowReceiverAttribute.needsUpdate = true;
          }
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
              // Three's pre-pass is lens input only when a lens actually transmits (an opaque mirror lens samples none).
              internalTransmissionIsLensInput: this.legacyTransmissiveOptics,
              cheekContact,
              cheekTransitionPx: 2,
              terminalReturn: this.templeTerminalFit ? templeTerminalRelief(this.templeTerminalFit) : null});
          this.eyewearPose.visible = this.fitRevealed; this.camera.updateMatrixWorld();
          if (this.surfaceAttribute) this.surfaceAttribute.needsUpdate = true;
          if (this.surfaceMesh) this.surfaceMesh.visible = true;
          if (this.templeHeadShell) this.templeHeadShell.visible = true;
          this.lastPose = {rawMatrix: matrix.slice(), eyewearMatrix: attachment.matrix.slice(), yawDegrees: attachment.yawDegrees};
          this.posedMatrix = poseMatrix;
          // The protection geometry for this exact pose, fixed shape and frame size.
          const {width, height} = this.renderSize, shape = this.rearDrop;
          this.protectionConfiguration = shape ? createProtection({optical: shape.opticalBounds, originalArms: shape.originalArmBounds, candidateArms: shape.candidateArmBounds},
            attachment.matrix, this.model.offsetCm, detection.landmarks, width, height, frame.width / frame.height, this.fitScale,
            reason => this.noteProtectionFailure(reason)) : null;
          this.nasalRect = nasalRoi(detection, width, height);
          this.templePaths = this.projectPaths(attachment.matrix);
          this.templeEndpointPending = true;
          this.lastTrackedTimestampMs = timestampMs;
        }
      }
      if (!this.facePose.visible) {
        this.shadowSurfaceValid = false;
        this.templeHeadFit?.miss(timestampMs);
        // A dropped face frame must not grow a hidden tail on reacquisition. A genuinely new session/face
        // after sustained loss starts clean; nothing is drawn while tracking is absent.
        if (timestampMs - this.lastTrackedTimestampMs > 1000) {
          this.templeEndpointTracker?.reset(); this.templeEndpointReport = null;
        }
        this.contactDepth.reset();
        this.shadowReceiver.reset();
        this.stabilizer?.reset();
      }
      this.shadowPending = true;
      return this.facePose.visible;
    } catch (error) {this.clearPresentation(); throw error;}
  }

  /** Pose the eyewear at an explicit camera WITHOUT a face (dev capture only: `arview capture --cameras ... --no-face`,
   *  for comparing a model with product photos taken from known cameras; the try-on never calls this). What is drawn is
   *  the try-on's own: the materials, the room environment and the camera-relative key light (the camera stays at the
   *  origin, as the virtual camera does), ACES tone mapping, lens transmission and the crystal look-through, at the
   *  authored size. What only a face makes is left out: no face or head occluder, no hair, no face shadows, no protection
   *  rectangles, no near-arm overlays, and the arms keep their authored geometry (no fixed spread, no terminal return, no
   *  temple clip), so the full temples a product photo shows are drawn. Needs RendererOptions.fixedScale (the camera is
   *  metric). Draw with `render(null, {hair: false, shadows: false, guard: false})` or a flat override; the next `pose()`
   *  returns to the face path and the virtual camera. Throws on unusable input (nothing changes then). */
  poseCamera(frame: HTMLCanvasElement, view: ExplicitCamera): void {
    if (this.disposed) throw new Error('The renderer is disposed.');
    if (!this.fixedScale) throw new Error('An explicit camera needs the authored size (RendererOptions.fixedScale): the camera is metric.');
    if (!this.eyewearAsset) throw new Error('No model is configured.');
    const matrix16 = (values: readonly number[]): boolean => Array.isArray(values) && values.length === 16 && values.every(Number.isFinite);
    if (!matrix16(view.assetToCamera) || !matrix16(view.projection)) throw new Error('An explicit camera needs two finite column-major 4x4 matrices.');
    if (!(view.nearCm > 0) || !(view.farCm > view.nearCm) || !Number.isFinite(view.farCm)) throw new Error('The explicit camera\'s clip planes must satisfy 0 < near < far.');
    if (frame.width <= 0 || frame.height <= 0) throw new Error('The camera frame is empty.');
    const placement = new Matrix4().fromArray(view.assetToCamera);
    if (Math.abs(placement.determinant()) < 1e-12) throw new Error('The explicit camera\'s placement is singular.');
    const templePose = view.temple_pose === undefined ? {} : parsePhotoTemplePose(view.temple_pose);
    this.templeEndpointPending = false;
    this.clearPresentation(false);
    const cameraModeChanged = !this.explicitCamera;
    this.explicitCamera = true;
    if (cameraModeChanged || this.frameWidth !== frame.width || this.frameHeight !== frame.height) {
      this.frameWidth = frame.width; this.frameHeight = frame.height;
      const {width, height} = this.renderSize;
      this.renderer.setSize(width, height, false);
      this.camera.aspect = frame.width / frame.height;
      this.backgroundTexture?.dispose(); this.backgroundTexture = null;
    }
    if (!this.backgroundTexture) {
      this.backgroundTexture = new CanvasTexture(frame); this.backgroundTexture.colorSpace = SRGBColorSpace;
      this.backgroundTexture.generateMipmaps = false; this.backgroundTexture.minFilter = LinearFilter; this.backgroundTexture.magFilter = LinearFilter;
      this.scene.background = this.backgroundTexture;
    }
    this.backgroundTexture.image = frame; this.backgroundTexture.needsUpdate = true;
    this.explicitCamera = true;
    const camera = this.camera;
    camera.near = view.nearCm; camera.far = view.farCm;
    camera.projectionMatrix.fromArray(view.projection); camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    camera.updateMatrixWorld();
    // The authored arms: no fixed spread, no terminal return (both restore the original storage), no clip, no overlays, no hair.
    this.rearDrop?.setTerminalFit(null); this.rearDrop?.setShape(0, 0);
    this.photoTemplePose?.apply(templePose);
    this.templeClip?.set(null); this.templeVisibility?.set(null); this.hairOcclusion?.set(false, this.hairStartZ);
    // eyewearPose x the asset's own matrix (the centimetre scale and bridge offset) = the requested placement.
    const asset = this.eyewearAsset; asset.updateMatrix();
    const posed = placement.clone().multiply(asset.matrix.clone().invert());
    this.eyewearPose.matrix.copy(posed); this.eyewearPose.matrixWorldNeedsUpdate = true;
    this.eyewearPose.visible = this.fitRevealed;
    this.lastPose = {rawMatrix: placement.toArray(), eyewearMatrix: posed.toArray(), yawDegrees: Number.NaN};
    this.shadowPending = true;
  }
  /** Whether the current frame is posed at an explicit camera without a face (poseCamera). */
  get explicitCameraPosed(): boolean {return this.explicitCamera;}
  get photoTemplePoseReport() {return this.photoTemplePose?.report ?? null;}

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

  /** The camera transmission twin of a translucent frame material (translucent-twin.ts): transmission 0, the paired
   *  camera mixed in through the material's volume, running the original's shader wrappers (arm clip, hair occlusion,
   *  overlay relief) with their shared uniforms. Created on first use, rebuilt when the original's wrappers change. */
  private twinOf(material: Material): Material {
    if (!(material instanceof MeshPhysicalMaterial) || !isTranslucentFrameMaterial(material)) return material;
    if (this.twinFailures.has(material)) return material;
    let twin = this.translucentTwins.get(material);
    if (!twin) {
      try {twin = createCameraTransmissionTwin(material, this.twinUniforms);}
      catch (error) {
        // The material keeps Three's own physical transmission (inside pass B it then shows the clear colour).
        this.twinFailures.add(material);
        this.fallback('translucent twin', `The camera twin of translucent frame material "${material.name || '(unnamed)'}" could not be built `
          + `(${messageOf(error)}); it is drawn with Three's own transmission.`, 'render');
        return material;
      }
      this.translucentTwins.set(material, twin);
    }
    refreshCameraTransmissionTwin(twin);
    twin.toneMapped = material.toneMapped;
    // An overlay clone's coverage mode is part of its shader (temple-visibility.ts); the twin's GL state follows it.
    if (twin.alphaToCoverage !== material.alphaToCoverage) {twin.alphaToCoverage = material.alphaToCoverage; twin.needsUpdate = true;}
    return twin;
  }
  private swapTranslucentFrames(toTwin: boolean): void {
    for (const entry of this.translucentFrameMeshes) {
      if (!toTwin) {entry.mesh.material = entry.original; continue;}
      entry.mesh.material = Array.isArray(entry.original) ? entry.original.map(material => this.twinOf(material)) : this.twinOf(entry.original);
    }
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
   *  other variants of the same posed frame and reads them back. `override` (override.ts) draws a diagnostic image
   *  instead, applied to the originals, the near-arm overlays and the twins alike and restored afterwards. */
  render(mask: CategoryMask | null, variant: Partial<RenderVariant> = {}): FrameTimings {
    if (this.disposed) throw new Error('The renderer is disposed.');
    if (!this.backgroundTexture) throw new Error('Nothing is posed.');
    if (variant.visibleHead && !variant.override && !this.explicitCamera) {
      // These are the actual depth occluders, not a second approximation of the head. Flat diagnostic renders
      // deliberately retain their original depth-only materials, so their labels and visibility stay unchanged.
      const skin = new MeshStandardMaterial({color: '#cba68d', roughness: 1, metalness: 0,
        side: DoubleSide, depthWrite: true, depthTest: true});
      const saved = [this.surfaceMesh, this.templeHeadShell].filter((mesh): mesh is Mesh => mesh !== null)
        .map(mesh => ({mesh, material: mesh.material}));
      try {
        for (const {mesh} of saved) {mesh.geometry.computeVertexNormals(); mesh.material = skin;}
        return {...this.render(mask, {...variant, visibleHead: false, hair: false}), visibleHead: true};
      } finally {
        for (const {mesh, material} of saved) mesh.material = material;
        skin.dispose();
      }
    }
    const override = variant.override ?? null;
    if (override === null) return this.renderFrame(mask, variant);
    if (!isRenderOverride(override)) throw new Error(`Unknown render override ${JSON.stringify(override)}.`);
    if (FLAT_OVERRIDES.has(override)) return this.renderFlat(override);
    // lens-hidden / lens-only: the beauty draw (one unguarded pass) with a visibility filter over every eyewear mesh
    // (overlays included; the twins are materials of the same meshes, the shadow casters follow their sources' visibility).
    const hidden: {object: Object3D; visible: boolean}[] = [];
    this.eyewearRoot?.traverse(object => {
      if (!(object instanceof Mesh)) return;
      const lens = (Array.isArray(object.material) ? object.material : [object.material]).some(isOpticalMaterial);
      if (override === 'lens-hidden' ? lens : !lens) {hidden.push({object, visible: object.visible}); object.visible = false;}
    });
    this.shadowPending = true;
    try {
      // One unguarded pass: the guarded pass B toggles the lens meshes itself (it skips them, then shows them again).
      return {...this.renderFrame(mask, {...variant, override: null, guard: false}), override};
    } finally {
      for (const {object, visible} of hidden) object.visible = visible;
      this.shadowPending = true;
    }
  }

  /** part-id, silhouette, lens-mask and normals: flat materials on every eyewear mesh, black background, no guard, hair,
   *  shadows or look-through; overlays hidden; the face and head depth occluders kept; temples cut at the live clip. */
  private renderFlat(override: RenderOverride): FrameTimings {
    const root = this.eyewearRoot, tracked = this.facePose.visible || this.explicitCamera, showEyewear = tracked && this.fitRevealed;
    const saved = new Map<Mesh, Mesh['material']>(), overlays: {mesh: Mesh; visible: boolean}[] = [], created: Material[] = [];
    const palette: PaletteEntry[] = [];
    const background = this.scene.background, poseVisible = this.eyewearPose.visible;
    const clip = this.templeClip?.configuration ?? null;
    this.overrideClip.enabled.value = clip ? 1 : 0;
    if (clip) {this.overrideClip.negative.value = clip.negativeXCutoffLocalZM; this.overrideClip.positive.value = clip.positiveXCutoffLocalZM;}
    const parts = new Map(this.parts().map(part => [part.mesh, part]));
    const submitStart = performance.now();
    let frameIndex = 0;
    try {
      root?.traverse(object => {
        if (!(object instanceof Mesh)) return;
        if (object.userData.templeVisibilityOverlay === true) {overlays.push({mesh: object, visible: object.visible}); object.visible = false; return;}
        saved.set(object, object.material);
        const slots = Array.isArray(object.material) ? object.material : [object.material];
        const lensMesh = slots.some(isOpticalMaterial);
        const profile = partVisibilityOf(object), side = profileTempleSide(object);
        const rgb: readonly [number, number, number] = override === 'part-id' ? (lensMesh ? LENS_ID_RGB : partColour(frameIndex++))
          : override === 'silhouette' || lensMesh ? [255, 255, 255] : [0, 0, 0];
        const replaced = slots.map(material => {
          const lens = isOpticalMaterial(material);
          const cut = !lens && (!profile || profile.region === 'temple_left' || profile.region === 'temple_right');
          const flat = flatOverrideMaterial(override, override === 'lens-mask' ? (lens ? [255, 255, 255] : [0, 0, 0]) : rgb, this.overrideClip, cut, side);
          created.push(flat); return flat;
        });
        object.material = Array.isArray(object.material) ? replaced : replaced[0]!;
        if (override === 'part-id') {
          const part = parts.get(object);
          palette.push({rgb: [rgb[0], rgb[1], rgb[2]], name: part?.name ?? object.name, class: part?.class ?? (lensMesh ? 'lens' : 'frame'),
            side: part?.side ?? 'centre'});
        }
      });
      this.scene.background = new Color(0x000000);
      this.setStencil(false, 0); this.hairOcclusion?.set(false, this.hairStartZ);
      this.eyewearPose.visible = showEyewear;
      this.renderer.setRenderTarget(null);
      this.renderer.render(this.scene, this.camera);
      if (this.sync) {
        if (this.fence) this.gl.deleteSync(this.fence);
        this.fence = this.gl.fenceSync(this.gl.SYNC_GPU_COMMANDS_COMPLETE, 0); this.gl.flush();
      }
    } finally {
      for (const [mesh, material] of saved) mesh.material = material;
      for (const {mesh, visible} of overlays) mesh.visible = visible;
      for (const material of created) material.dispose();
      this.scene.background = background; this.eyewearPose.visible = poseVisible;
    }
    const maximum = this.templeEndMaximumZM;
    return {templeEndMaximumZM: maximum, templeEndNegativeZM: this.templeEndpointReport?.negativeZM ?? maximum,
      templeEndPositiveZM: this.templeEndpointReport?.positiveZM ?? maximum,
      templeEndState: this.templeEndStateText,
      maskUploadMs: 0, endpointMs: 0, submitMs: performance.now() - submitStart, hairApplied: false, maskWidth: 0, maskHeight: 0, sync: this.sync,
      guarded: false, passes: 1, protectedRects: 0, editableRects: 0, safeFallback: false,
      templeKeepCm: this.templeKeepCm, templeDropCm: this.templeDropCm, armSpreadM: this.rearDrop?.spreadM ?? 0,
      armSpreadStartZM: this.rearDrop?.spreadStartZM ?? null, shadowsApplied: false, shadowMs: 0, override,
      ...(override === 'part-id' ? {palette} : {})};
  }

  private renderFrame(mask: CategoryMask | null, variant: Partial<RenderVariant>): FrameTimings {
    if (!this.backgroundTexture) throw new Error('Nothing is posed.');
    const v: RenderVariant = {hair: true, eyewear: true, guard: this.guard, shadows: true, ...variant};
    // With a look-through image the twins are the crystal in every pass (refreshed here, which follows wrapper changes).
    if (this.lookThrough) this.swapTranslucentFrames(true);
    // A camera without a face (poseCamera) draws the eyewear too; no face-driven subsystem is armed then.
    const tracked = this.facePose.visible || this.explicitCamera;
    const showEyewear = tracked && this.fitRevealed && v.eyewear;
    const wantsHair = v.hair && mask !== null && tracked;
    const showHair = wantsHair && showEyewear;
    const endpointStart = performance.now();
    // Update once per posed frame. Audit variants reuse the exact same endpoint, even when their hair flag differs.
    if (this.templeEndpointPending && tracked) {
      if (this.continuityModel && this.latestPose) {
        if (!this.templeEndpointTracker) {
          try {this.templeEndpointTracker = new TempleEndpointTracker(this.continuityModel, this.templeEndMaximumZM);}
          catch (error) {
            // Both arms keep the fixed buried cap; a throw here would otherwise end the live session.
            this.continuityModel = null; this.continuityFailure = messageOf(error);
            this.fallback('continuity', `The temple endpoint tracker could not start (${this.continuityFailure}); both arms keep the fixed buried cap `
              + 'and no hair endpoint.', 'render');
          }
        }
      }
      if (this.continuityModel && this.latestPose && this.templeEndpointTracker) {
        this.templeEndpointReport = this.templeEndpointTracker.update({paths: this.templePaths, mask: wantsHair ? mask : null,
          render: this.renderSize, warp: this.maskWarp, timestampMs: this.poseTimestampMs,
          yawDegrees: this.latestPose.steadyYawDeg ?? this.latestPose.yawDeg,
          pitchDegrees: this.latestPose.steadyPitchDeg ?? this.latestPose.pitchDeg, evidenceId: mask?.category});
      } else this.templeEndpointReport = null;
      this.applyTempleEndpoint(); this.templeEndpointPending = false;
    }
    const endpointMs = performance.now() - endpointStart;
    // Hair-on/off audit variants share the same shadow underlay and foreground mask.
    const maskUploadMs = showEyewear && mask ? this.uploadMask(mask) : 0;
    this.hairOcclusion?.setMaskUv(wantsHair && this.maskWarp ? maskUvMatrix(this.maskWarp) : null);
    const {width, height} = this.renderSize;
    const submitStart = performance.now();
    let background: Texture = this.backgroundTexture;
    const shadowStart = performance.now();
    const wantsShadows = !!(showEyewear && this.shadowSurfaceValid && v.shadows && this.shadows
      && this.shadowSettings.enabled && (this.shadowSettings.frameStrength > 0 || this.shadowSettings.lensStrength > 0));
    if (wantsShadows) {
      if (this.shadowPending || !this.shadowBackground) {
        this.shadows!.setTempleClip(this.templeClip?.configuration ?? null);
        this.shadowBackground = this.shadows!.render(this.camera, this.backgroundTexture, width, height, this.shadowSettings,
          mask ? this.maskTexture : null, mask && this.maskWarp ? maskUvMatrix(this.maskWarp) : null);
        this.shadowPending = false;
      }
      background = this.shadowBackground;
    }
    const shadowsApplied = background !== this.backgroundTexture;
    const shadowMs = performance.now() - shadowStart;
    this.scene.background = background;
    const protection = this.protectionConfiguration, nose = this.nasalRect;
    const guarded = v.guard && showEyewear && protection !== null, safeFallback = v.guard && showEyewear && protection === null;
    this.eyewearPose.visible = showEyewear;
    this.templeClip?.prepareRender(background, width, height);
    // The twins of translucent frame materials look through to this same background at their own pixel; with a
    // look-through image they are rebound to it below, once it is drawn.
    setCameraTransmissionSource(this.twinUniforms, background, width, height);
    if (tracked && this.posedMatrix && this.templeVisibility?.configuration) this.templeVisibility.prepare(this.posedMatrix, background);
    this.hairOcclusion?.prepareRender(showHair ? this.maskTexture : null, width, height, background, 2);
    if (showEyewear && this.lookThrough) {
      this.setStencil(false, 0); this.hairOcclusion?.set(false, this.hairStartZ);
      // For the canvas passes: the camera behind the opaque eyewear, the arms and their relieved overlays included,
      // without the lenses and without anything that samples the image.
      const image = this.lookThrough.render(this.renderer, this.scene, this.camera, width, height,
        [...this.lookThroughHidden, ...this.translucentTwins.values()],
        draw => {if (this.templeVisibility) this.templeVisibility.withLookThroughInput(draw); else draw();});
      setCrystalLookThroughSource(this.twinUniforms, image, width, height);
    }
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
        // A translucent frame on Three's transmission would transmit the clear colour here (no background in this
        // pass): its meshes, the near-arm overlays included, draw their camera transmission twin inside the editable
        // corridors instead (swapped before the stencil settings are applied so the twin is stencil-limited like
        // everything else). With a look-through image they already draw their twins.
        const swap = !this.lookThrough;
        if (swap) this.swapTranslucentFrames(true);
        this.setStencil(true, 0); this.hairOcclusion?.set(showHair, this.hairStartZ);
        this.scene.background = null; for (const lens of this.lensMeshes) lens.visible = false;
        try {this.renderer.render(this.scene, this.camera);}
        finally {
          this.scene.background = background; for (const lens of this.lensMeshes) lens.visible = true;
          if (swap) this.swapTranslucentFrames(false);
        }
        passes = 2;
      } finally {this.renderer.autoClear = true; this.setStencil(false, 0);}
    } else {
      this.setStencil(false, 0);
      this.hairOcclusion?.set(showHair && !safeFallback, this.hairStartZ);
      this.renderer.render(this.scene, this.camera);
    }
    if (this.sync) {
      if (this.fence) this.gl.deleteSync(this.fence);
      this.fence = this.gl.fenceSync(this.gl.SYNC_GPU_COMMANDS_COMPLETE, 0); this.gl.flush();
    }
    const submitMs = performance.now() - submitStart;
    const hairApplied = showHair && !safeFallback;
    return {
      templeEndMaximumZM: this.templeEndMaximumZM,
      templeEndNegativeZM: this.templeEndpointReport?.negativeZM ?? this.templeEndMaximumZM,
      templeEndPositiveZM: this.templeEndpointReport?.positiveZM ?? this.templeEndMaximumZM,
      templeEndState: this.templeEndStateText,
      maskUploadMs, endpointMs, submitMs, hairApplied,
      maskWidth: hairApplied ? mask!.width : 0, maskHeight: hairApplied ? mask!.height : 0, sync: this.sync,
      guarded, passes, protectedRects: protection?.protectedRects.length ?? 0, editableRects: protection?.editableRects.length ?? 0, safeFallback,
      armSpreadM: this.rearDrop?.spreadM ?? 0,
      shadowsApplied, shadowMs,
      templeKeepCm: this.templeKeepCm, templeDropCm: this.templeDropCm,
      armSpreadStartZM: this.rearDrop?.spreadStartZM ?? null};
  }

  /** Audit only: the current canvas pixels, top-down. Live frames never call this. */
  readback(): ImageData {return this.reader.read(this.renderer.domElement);}

  private clearPresentation(resetGeometry = true): void {
    this.shadowSurfaceValid = false; this.shadowPending = true;
    if (resetGeometry) {
      this.shadowReceiver.reset();
      this.contactDepth.reset();
      this.templeEndpointTracker?.reset(); this.templeEndpointReport = null; this.templeEndpointPending = false;
      this.templeHeadFit?.reset(); this.templeHeadShellFit?.reset();
    }
    this.lastPose = null; this.posedMatrix = null; this.latestPose = null;
    this.facePose.visible = this.eyewearPose.visible = false;
    if (this.surfaceMesh) this.surfaceMesh.visible = false;
    if (this.templeHeadShell) this.templeHeadShell.visible = false;
    this.templeVisibility?.set(null); this.templeClip?.set(null); this.hairOcclusion?.set(false, this.hairStartZ);
    this.protectionConfiguration = null; this.nasalRect = null; this.templePaths = null;
  }
  dispose(): void {
    if (this.disposed) return; this.disposed = true;
    this.contactDepth.reset();
    this.shadowReceiver.reset();
    this.removeAbortListener?.(); this.removeAbortListener = null;
    if (this.fence) {try {this.gl.deleteSync(this.fence);} catch {/* context may be lost */} this.fence = null;}
    this.lastPose = null; this.posedMatrix = null;
    this.protectionConfiguration = null; this.nasalRect = null; this.reader.dispose();
    this.scene.background = null; this.scene.environment = null;
    this.shadows?.dispose(); this.shadows = null; this.shadowBackground = null;
    this.eyewearAsset = null; this.fitFrontBounds = null; this.terminalFitAtScale = null;
    this.shadowReceiverGeometry?.dispose(); this.shadowReceiverGeometry = null; this.shadowReceiverAttribute = null;
    this.backgroundTexture?.dispose(); this.backgroundTexture = null;
    this.maskTexture?.dispose(); this.maskTexture = null; this.maskBytes = null;
    this.environmentTarget?.dispose(); this.environmentTarget = null;
    if (this.lighting) {this.lighting.maps.dispose(); this.lighting = null;}
    this.seeThroughEnvironmentTarget?.dispose(); this.seeThroughEnvironmentTarget = null;
    this.hairOcclusion?.dispose(); this.hairOcclusion = null;
    this.templeVisibility?.dispose(); this.templeVisibility = null;
    this.observedCheekGeometry?.dispose(); this.observedCheekGeometry = null; this.observedCheekAttribute = null; this.cheekContact = null;
    this.rearDrop?.dispose(); this.rearDrop = null;
    this.templeClip?.dispose(); this.templeClip = null;
    this.templeEndpointTracker = null; this.templeEndpointReport = null; this.templeSurface = null; this.templeTerminalFit = null;
    this.templeHeadFit = null; this.templeHeadShellFit = null;
    // The authored materials go back on their meshes so the scene disposal frees them; the twins are freed here.
    this.swapTranslucentFrames(false);
    this.lookThrough?.dispose(); this.lookThrough = null; this.lookThroughHidden = [];
    disposeObjects([this.scene, ...this.assetScenes]); this.assetScenes = [];
    for (const twin of this.translucentTwins.values()) twin.dispose();
    this.translucentTwins.clear(); this.translucentFrameMeshes = [];
    setCameraTransmissionSource(this.twinUniforms, null, 1, 1);
    this.canonicalPositions = []; this.nasalShape = null; this.faceSurface = null; this.surfaceMesh = null; this.templeHeadShell = null; this.surfaceAttribute = null;
    this.scene.clear(); this.renderer.dispose(); this.renderer.forceContextLoss();
  }
}
