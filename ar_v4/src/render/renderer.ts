import {
  ACESFilmicToneMapping, BufferAttribute, BufferGeometry, CanvasTexture, Color, DirectionalLight,
  DoubleSide, DynamicDrawUsage, Group, LinearFilter, Material, Mesh,
  MeshBasicMaterial, MeshPhysicalMaterial, Object3D, PerspectiveCamera, PMREMGenerator, Scene,
  SphereGeometry, SRGBColorSpace, Texture, Vector3, WebGLRenderer,
} from 'three';
import type { WebGLRenderTarget } from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { Detection } from '../runtime/detector.ts';
import { FaceSurface } from './face-surface.ts';
import { correctedBridgePose } from './bridge-pose.ts';
import { VIRTUAL_CAMERA } from './projection.ts';
/** MediaPipe's default virtual camera. This is an assumed camera, not calibration. */
export const VERTICAL_FOV_DEGREES = VIRTUAL_CAMERA.verticalFovDegrees;
export const GLASSES_METERS_TO_CENTIMETERS = 100;
/** Bridge height uses canonical 168; front plane retains the baseline's 6.691763 cm depth. */
export const GLASSES_OFFSET_CM = Object.freeze([0, 3.271027, 6.531958919387042] as const);
const MAX_RENDER_WIDTH = 1280;
const RESIDUAL_LANDMARKS = [1, 4, 6, 33, 133, 168, 197, 263, 362] as const;

/** Copies of the exact presentation geometry, made only for requested captures. */
export interface CaptureGeometry {
  rawMatrix: number[];
  correctedMatrix: number[];
  eyewearMatrix: number[];
  surfacePositions: Float32Array;
  yawDegrees: number;
}

interface CanonicalFace {
  positions: number[];
  indices: number[];
}

function abortError(): DOMException {
  return new DOMException('Try-on renderer startup was cancelled.', 'AbortError');
}

function validateFace(value: unknown): CanonicalFace {
  if (typeof value !== 'object' || value === null) throw new Error('The canonical face is missing.');
  const face = value as Partial<CanonicalFace>;
  if (!Array.isArray(face.positions) || face.positions.length !== 468 * 3
      || !face.positions.every(Number.isFinite)
      || !Array.isArray(face.indices) || face.indices.length === 0 || face.indices.length % 3 !== 0
      || !face.indices.every(index => Number.isInteger(index) && index >= 0 && index < 468)) {
    throw new Error('The canonical face contains invalid mesh data.');
  }
  return face as CanonicalFace;
}

async function fetchAsset(url: string, signal: AbortSignal): Promise<Response> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status}).`);
  return response;
}

/** Dispose shared glTF resources once, including owned decoded image pixels. */
function disposeObjects(roots: Object3D[]): void {
  const geometries = new Set<BufferGeometry>();
  const materials = new Set<Material>();
  const textures = new Set<Texture>();
  const bitmaps = new Set<ImageBitmap>();
  for (const root of roots) root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
    }
  });
  for (const texture of textures) {
    const image = texture.image;
    if (typeof ImageBitmap !== 'undefined' && image instanceof ImageBitmap) bitmaps.add(image);
    texture.dispose();
  }
  // GLTFLoader can give several textures the same ImageBitmap. Texture.dispose
  // releases the GPU allocation; the loader-owned decoded pixels need closing.
  for (const bitmap of bitmaps) bitmap.close();
  for (const material of materials) material.dispose();
  for (const geometry of geometries) geometry.dispose();
}

/**
 * Presents one detection over the exact unmirrored frame that produced it.
 * Coordinates are centimeters, +Y up, +Z toward the front of the canonical face.
 * MediaPipe's matrix is column-major and maps that face into camera space.
 * The caller may mirror the whole output canvas in CSS, exactly once.
 */
export class TryOnRenderer {
  private readonly renderer: WebGLRenderer;
  private readonly scene = new Scene();
  private readonly camera = new PerspectiveCamera(VERTICAL_FOV_DEGREES, 1, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
  private readonly facePose = new Group();
  private readonly eyewearPose = new Group();
  private readonly projected = new Vector3();
  private backgroundTexture: CanvasTexture | null = null;
  private environmentTarget: WebGLRenderTarget | null = null;
  private canonicalPositions: number[] = [];
  private faceSurface: FaceSurface | null = null;
  private surfaceMesh: Mesh | null = null;
  private headProxy: Mesh | null = null;
  private surfaceAttribute: BufferAttribute | null = null;
  private assetScenes: Object3D[] = [];
  private removeAbortListener: (() => void) | null = null;
  private frameWidth = 0;
  private frameHeight = 0;
  private disposed = false;
  private residual: number | null = null;
  private bridgeCorrection: number | null = null;
  private yaw: number | null = null;
  private lastCaptureFrame: {
    rawMatrix: number[]; correctedMatrix: number[]; eyewearMatrix: number[];
    yawDegrees: number;
  } | null = null;

  private constructor(renderer: WebGLRenderer) {
    this.renderer = renderer;
    this.scene.background = new Color(0x080b10);
    this.facePose.name = 'Tracked canonical face (centimeters)';
    this.facePose.matrixAutoUpdate = false;
    this.facePose.visible = false;
    this.scene.add(this.facePose);
    this.eyewearPose.name = 'Eyewear bridge pose (centimeters)';
    this.eyewearPose.matrixAutoUpdate = false;
    this.eyewearPose.visible = false;
    this.scene.add(this.eyewearPose);
  }

  /** Returns pixels in the native input frame; includes canonical-shape mismatch. */
  get projectionResidualPx(): number | null { return this.residual; }
  get bridgeCorrectionPx(): number | null { return this.bridgeCorrection; }
  get yawDegrees(): number | null { return this.yaw; }
  /** Copies data only when requested; ordinary live presentation does not copy the surface. */
  get captureSnapshot(): CaptureGeometry | null {
    const frame = this.lastCaptureFrame;
    if (!frame || !this.faceSurface || this.disposed) return null;
    return {
      rawMatrix: frame.rawMatrix.slice(), correctedMatrix: frame.correctedMatrix.slice(),
      eyewearMatrix: frame.eyewearMatrix.slice(),
      surfacePositions: this.faceSurface.positions.slice(), yawDegrees: frame.yawDegrees,
    };
  }

  static async create(canvas: HTMLCanvasElement, signal: AbortSignal): Promise<TryOnRenderer> {
    if (signal.aborted) throw abortError();
    const loading = new AbortController();
    let instance: TryOnRenderer | null = null;
    let gltf: GLTF | null = null;
    let webgl: WebGLRenderer | null = null;
    let context: WebGL2RenderingContext | null = null;
    const onAbort = () => {
      loading.abort();
      instance?.dispose();
    };
    signal.addEventListener('abort', onAbort, { once: true });
    try {
      const [glasses, face] = await Promise.all([
        fetchAsset('/models/amber-horizon.glb', loading.signal).then(response => response.arrayBuffer()),
        fetchAsset('/models/canonical-face.json', loading.signal).then(response => response.json()).then(validateFace),
      ]);
      if (signal.aborted) throw abortError();
      // This checked-in GLB is self-contained. parseAsync cannot be interrupted;
      // a late result is disposed before cancellation is allowed to escape.
      gltf = await new GLTFLoader().parseAsync(glasses, '/models/');
      if (signal.aborted) throw abortError();
      context = canvas.getContext('webgl2', { alpha: false, antialias: true, powerPreference: 'high-performance' });
      if (!context) throw new Error('WebGL 2 is unavailable on this browser.');
      webgl = new WebGLRenderer({ canvas, context, alpha: false, antialias: true });
      instance = new TryOnRenderer(webgl);
      instance.removeAbortListener = () => signal.removeEventListener('abort', onAbort);
      instance.assetScenes = gltf.scenes;
      const eyewearScene = gltf.scene;
      gltf = null; // instance now owns all imported resources.
      instance.configure(face, eyewearScene);
      if (signal.aborted) throw abortError();
      return instance;
    } catch (error) {
      loading.abort();
      signal.removeEventListener('abort', onAbort);
      if (instance) instance.dispose();
      else {
        webgl?.dispose();
        // A WebGLRenderer constructor may throw after creating GPU resources.
        // Keeping our context reference makes that failure releasable as well.
        context?.getExtension('WEBGL_lose_context')?.loseContext();
      }
      if (gltf) disposeObjects(gltf.scenes);
      throw signal.aborted ? abortError() : error;
    }
  }

  private configure(face: CanonicalFace, eyewearScene: Group): void {
    this.renderer.setPixelRatio(1);
    this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1;
    this.canonicalPositions = face.positions;

    const asset = new Group();
    asset.name = 'Amber Horizon bridge attachment';
    asset.scale.setScalar(GLASSES_METERS_TO_CENTIMETERS);
    asset.position.set(...GLASSES_OFFSET_CM);
    eyewearScene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      for (const material of materials) {
        if (!(material instanceof MeshPhysicalMaterial) || material.transmission <= 0) continue;
        // Three's WebGLBackground bypasses tone mapping for the sRGB camera
        // texture. transmission_pars_fragment samples that image into lens
        // radiance; applying ACES here would alter only the camera pixels seen
        // through the lens. Keep their color path consistent with the background.
        // The opaque frame continues to use ACES for its synthetic lighting.
        material.toneMapped = false;
      }
    });
    asset.add(eyewearScene);
    this.eyewearPose.add(asset);

    const occlusionMaterial = new MeshBasicMaterial({
      colorWrite: false, depthWrite: true, depthTest: true, side: DoubleSide,
    });
    this.faceSurface = new FaceSurface(face.positions);
    const geometry = new BufferGeometry();
    this.surfaceAttribute = new BufferAttribute(this.faceSurface.positions, 3).setUsage(DynamicDrawUsage);
    geometry.setAttribute('position', this.surfaceAttribute);
    geometry.setIndex(face.indices);
    const surface = new Mesh(geometry, occlusionMaterial);
    surface.name = 'Observed face depth in camera space';
    surface.renderOrder = -2;
    // Geometry is already in camera space. It must not receive the eyewear's
    // bridge correction or the face pose a second time.
    surface.frustumCulled = false;
    surface.visible = false;
    this.surfaceMesh = surface;
    this.scene.add(surface);

    // Narrower than the canonical cheek outline and behind the eye region.
    // This only covers part of the missing skull; it is not a personalized head.
    const head = new Mesh(new SphereGeometry(1, 24, 16), occlusionMaterial);
    head.name = 'Conservative rear head depth only';
    head.scale.set(6.3, 8, 5);
    head.position.set(0, 0, -2.5);
    head.renderOrder = -2;
    this.headProxy = head;
    this.facePose.add(head);

    const key = new DirectionalLight(0xffffff, 2);
    key.position.set(-10, 15, 20);
    this.scene.add(key);
    const environment = new RoomEnvironment();
    let generator: PMREMGenerator | null = null;
    try {
      generator = new PMREMGenerator(this.renderer);
      this.environmentTarget = generator.fromScene(environment, 0.04);
      this.scene.environment = this.environmentTarget.texture;
      this.scene.environmentIntensity = 0.8;
    } finally {
      generator?.dispose();
      environment.dispose();
    }
  }

  present(frame: HTMLCanvasElement, detection: Detection): boolean {
    if (this.disposed) return false;
    if (frame.width <= 0 || frame.height <= 0) throw new Error('The camera frame is empty.');
    if (this.frameWidth !== frame.width || this.frameHeight !== frame.height) {
      this.frameWidth = frame.width;
      this.frameHeight = frame.height;
      const width = Math.min(frame.width, MAX_RENDER_WIDTH);
      const height = Math.max(1, Math.round(width * frame.height / frame.width));
      this.renderer.setSize(width, height, false);
      this.camera.aspect = frame.width / frame.height;
      this.camera.updateProjectionMatrix();
      // Three textures must be recreated when their image dimensions change.
      this.backgroundTexture?.dispose();
      this.backgroundTexture = null;
    }
    if (!this.backgroundTexture) {
      this.backgroundTexture = new CanvasTexture(frame);
      this.backgroundTexture.colorSpace = SRGBColorSpace;
      this.backgroundTexture.generateMipmaps = false;
      this.backgroundTexture.minFilter = LinearFilter;
      this.backgroundTexture.magFilter = LinearFilter;
      this.scene.background = this.backgroundTexture;
    }
    this.backgroundTexture.image = frame;
    this.backgroundTexture.needsUpdate = true;

    const matrix = detection.matrix;
    this.facePose.visible = matrix !== null && matrix.length === 16 && matrix.every(Number.isFinite)
      && detection.landmarks.length >= 468;
    this.residual = null;
    this.bridgeCorrection = null;
    this.yaw = null;
    this.lastCaptureFrame = null;
    this.eyewearPose.visible = false;
    if (this.surfaceMesh) this.surfaceMesh.visible = false;
    if (this.headProxy) this.headProxy.visible = false;
    if (this.facePose.visible && matrix) {
      // Recover the detector's face using its ORIGINAL global pose. The face
      // surface supplies occlusion; the rigid glasses use a local bridge anchor.
      const surfaceValid = this.faceSurface?.reconstruct(detection.landmarks, matrix, this.camera.aspect) ?? false;
      this.facePose.visible = surfaceValid;
      if (surfaceValid) {
        const attachment = correctedBridgePose(matrix, detection.landmarks, this.canonicalPositions, this.camera.aspect);
        const eyewearMatrix = attachment.matrix;
        this.facePose.matrix.fromArray(attachment.matrix);
        this.facePose.matrixWorldNeedsUpdate = true;
        this.eyewearPose.matrix.fromArray(eyewearMatrix);
        this.eyewearPose.matrixWorldNeedsUpdate = true;
        this.eyewearPose.visible = true;
        this.camera.updateMatrixWorld();
        this.bridgeCorrection = attachment.correctionNormalized * this.frameHeight;
        this.yaw = attachment.yawDegrees;
        this.residual = this.measureProjectionResidual(detection);
        if (this.surfaceAttribute) this.surfaceAttribute.needsUpdate = true;
        if (this.surfaceMesh) this.surfaceMesh.visible = true;
        if (this.headProxy) this.headProxy.visible = true;
        this.lastCaptureFrame = {
          rawMatrix: matrix.slice(), correctedMatrix: attachment.matrix.slice(), eyewearMatrix,
          yawDegrees: attachment.yawDegrees,
        };
      }
    }
    // The background is part of this scene, so transmission can see the same
    // camera image as the main render, rather than an unrelated DOM video.
    this.renderer.render(this.scene, this.camera);
    return this.facePose.visible;
  }

  private measureProjectionResidual(detection: Detection): number | null {
    let sum = 0;
    for (const index of RESIDUAL_LANDMARKS) {
      const landmark = detection.landmarks[index];
      if (!landmark) return null;
      this.projected.fromArray(this.canonicalPositions, index * 3);
      this.projected.applyMatrix4(this.facePose.matrix).project(this.camera);
      const x = (this.projected.x + 1) * this.frameWidth / 2;
      const y = (1 - this.projected.y) * this.frameHeight / 2;
      sum += (x - landmark.x * this.frameWidth) ** 2 + (y - landmark.y * this.frameHeight) ** 2;
    }
    const residual = Math.sqrt(sum / RESIDUAL_LANDMARKS.length);
    return Number.isFinite(residual) ? residual : null;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.removeAbortListener?.();
    this.removeAbortListener = null;
    this.residual = null;
    this.bridgeCorrection = null;
    this.yaw = null;
    this.lastCaptureFrame = null;
    this.scene.background = null;
    this.scene.environment = null;
    this.backgroundTexture?.dispose();
    this.backgroundTexture = null;
    this.environmentTarget?.dispose();
    this.environmentTarget = null;
    disposeObjects([this.scene, ...this.assetScenes]);
    this.assetScenes = [];
    this.canonicalPositions = [];
    this.faceSurface = null;
    this.surfaceMesh = null;
    this.headProxy = null;
    this.surfaceAttribute = null;
    this.scene.clear();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
  }
}
