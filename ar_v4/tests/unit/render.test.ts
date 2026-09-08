import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import type { TestContext } from 'node:test';
import { Box3, BufferGeometry, CanvasTexture, Group, Material, Mesh, MeshPhysicalMaterial, MeshStandardMaterial,
  ShaderLib, Texture, UniformsUtils, Vector3 } from 'three';
import type { PerspectiveCamera, Scene } from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import {
  GLASSES_METERS_TO_CENTIMETERS, GLASSES_OFFSET_CM, TryOnRenderer,
} from '../../src/render/renderer.ts';
import type { Detection } from '../../src/runtime/detector.ts';
import { FaceSurface } from '../../src/render/face-surface.ts';
import { createNasalShape, NASAL_SHAPE_ID, NASAL_SHAPE_VERSION } from '../../src/render/nasal-shape.ts';
import { createTempleClip, createTempleFadeConfiguration } from '../../src/render/temple-clip.ts';
import type { TempleClipConfiguration } from '../../src/render/temple-clip.ts';
import { validateTempleVisibility } from '../../src/render/temple-visibility.ts';
import type { TempleVisibilityConfiguration } from '../../src/render/temple-visibility.ts';

const [assetBytes, canonicalText] = await Promise.all([
  readFile(new URL('../../public/models/amber-horizon.glb', import.meta.url)),
  readFile(new URL('../../public/models/canonical-face.json', import.meta.url), 'utf8'),
]);
const canonical = JSON.parse(canonicalText) as { positions: number[]; indices: number[] };
const assetBuffer = () => new Uint8Array(assetBytes).buffer;
// Node has no image decoder. Parse the real GLB geometry/materials, replacing
// only embedded image decoding with real Three textures. Browser checks load
// and render the actual embedded images through the unmodified GLTFLoader.
const loadGlasses = (image?: unknown) => new GLTFLoader().register(() => ({
  name: 'NodeImageDecoder',
  loadTexture: async () => new Texture(image),
})).parseAsync(assetBuffer(), '/models/');

function assetFetch(t: TestContext): void {
  t.mock.method(globalThis, 'fetch', async (url: RequestInfo | URL) =>
    new Response(String(url).endsWith('.glb') ? assetBuffer() : canonicalText));
}

function disposalCounts(gltf: GLTF) {
  const counts = new Map<BufferGeometry | Material | Texture, number>();
  for (const scene of gltf.scenes) scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    const textures = materials.flatMap(material => Object.values(material)
      .filter((value): value is Texture => value instanceof Texture));
    for (const resource of [object.geometry, ...materials, ...textures]) {
      if (counts.has(resource)) continue;
      counts.set(resource, 0);
      resource.addEventListener('dispose', () => counts.set(resource, counts.get(resource)! + 1));
    }
  });
  return counts;
}

test('the embedded Amber model retains its assumed scale, axes and bridge attachment', async (t) => {
  assert.equal(assetBytes.readUInt32LE(0), 0x46546c67, 'the active model is a binary glTF');
  assert.equal(assetBytes.readUInt32LE(16), 0x4e4f534a, 'the first GLB chunk is JSON');
  const document = JSON.parse(assetBytes.toString('utf8', 20, 20 + assetBytes.readUInt32LE(12))) as {
    buffers: { uri?: string }[];
    images: { bufferView?: number; uri?: string }[];
    extensionsRequired?: string[];
  };
  assert.ok(document.buffers.every(buffer => buffer.uri === undefined), 'geometry is embedded');
  assert.ok(document.images.length >= 2, 'frame atlas and lens gradient are retained');
  assert.ok(document.images.every(image => Number.isInteger(image.bufferView) && image.uri === undefined),
    'images are embedded and need no external request');
  assert.ok(!document.extensionsRequired?.includes('KHR_draco_mesh_compression'), 'no decoder dependency is introduced');

  const gltf = await loadGlasses();
  const resources = disposalCounts(gltf);
  t.after(() => { for (const resource of resources.keys()) resource.dispose(); });
  gltf.scene.updateMatrixWorld(true);
  const bounds = new Box3();
  const lenses: MeshPhysicalMaterial[] = [];
  gltf.scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    const positions = object.geometry.getAttribute('position');
    for (let i = 0; i < positions.count; i++) {
      const point = new Vector3().fromBufferAttribute(positions, i).applyMatrix4(object.matrixWorld);
      assert.ok(point.toArray().every(Number.isFinite));
      bounds.expandByPoint(point);
    }
    for (const material of Array.isArray(object.material) ? object.material : [object.material])
      if (material instanceof MeshPhysicalMaterial && material.transmission > 0) lenses.push(material);
  });
  const size = bounds.getSize(new Vector3());
  assert.ok(Math.abs(size.x - 0.145) < 0.001, 'the normalized preview width is assumed to be 145 mm, not measured');
  assert.ok(Math.abs(bounds.getCenter(new Vector3()).x) < 0.001, 'the frame is centered on the bridge');
  assert.ok(bounds.min.z < -0.1 && bounds.max.z > 0 && bounds.max.z < 0.01, 'temples extend behind the bridge along -Z');
  assert.ok(bounds.min.y < -0.01 && bounds.max.y > 0.01 && size.y < 0.06, 'the frame is upright along +Y');
  const attached = new Vector3().multiplyScalar(GLASSES_METERS_TO_CENTIMETERS).add(new Vector3(...GLASSES_OFFSET_CM));
  assert.equal(attached.x, 0, 'the exported bridge origin remains centered');
  assert.ok(Math.abs(attached.y - canonical.positions[168 * 3 + 1]!) < 1e-6,
    'the exported bridge origin retains canonical landmark 168 height');
  assert.ok(Math.abs(bounds.max.z * GLASSES_METERS_TO_CENTIMETERS + attached.z - 6.691763) < 1e-6,
    'the new frame front retains the accepted baseline front plane, independently of wearer images');
  assert.ok(lenses.length > 0, 'the imported asset retains transmissive lens materials');
  for (const lens of lenses) {
    assert.equal(lens.transmission, 1);
    assert.ok(Math.abs(lens.ior - 1.586) < 1e-5);
    assert.ok(lens.map instanceof Texture, 'the procedural lens color is retained as a texture');
  }
});

test('present projects a translated, yawed canonical face over its matching native frame', (t) => {
  let renderedScene: Scene | null = null;
  let renderedCamera: PerspectiveCamera | null = null;
  let size: number[] = [];
  let disposals = 0;
  let contextLosses = 0;
  let visibilityState: TempleVisibilityConfiguration | null = null;
  let visibilityCamera: CanvasTexture | null = null;
  let visibilityDisposals = 0;
  let failPreparation = false;
  const events: string[] = [];
  const preparedMatrices: number[][] = [];
  const preparedCameraImages: unknown[] = [];
  const backend = {
    capabilities: { samples: 4 },
    setSize(width: number, height: number) { size = [width, height]; },
    render(scene: Scene, camera: PerspectiveCamera) {
      if (visibilityState) assert.equal(events.at(-1), 'visibility:prepare',
        'the current visibility target is prepared before the main render');
      else assert.notEqual(events.at(-1), 'visibility:prepare',
        'disabled visibility does not prepare an auxiliary target');
      if (templeClip.configuration?.method === 'temple-end-blend-v3') {
        assert.equal(clipShader.uniforms.templeCameraSource!.value, scene.background,
          'the terminal dissolve borrows this presentation\'s actual paired background');
        assert.deepEqual(clipShader.uniforms.templeCameraViewport!.value.toArray(), size);
      } else assert.equal(clipShader.uniforms.templeCameraSource!.value, null,
        'historical and absent presentation must not retain a dissolve source');
      if (visibilityState?.method === 'temple-side-depth-v3' && visibilityState.frontalOcclusionWeight > 0)
        assert.equal(visibilityCamera, scene.background, 'frontal composition borrows this presentation\'s paired camera');
      else assert.equal(visibilityCamera, null, 'legacy and zero-frontal appearance need no borrowed visibility camera');
      events.push('render'); renderedScene = scene; renderedCamera = camera;
    },
    dispose() { disposals++; },
    forceContextLoss() { contextLosses++; },
  };
  // Exercise the actual presentation path without requiring a GPU in unit tests.
  const renderer = Reflect.construct(TryOnRenderer, [backend]) as TryOnRenderer;
  const captureSnapshot = () => {
    const snapshot = renderer.captureSnapshot;
    assert.ok(snapshot, 'the current presentation has capture geometry');
    return snapshot;
  };
  const nasalShape = createNasalShape(canonical.positions, canonical.indices);
  const clipMaterial = new MeshStandardMaterial();
  const clipRoot = new Group().add(new Mesh(new BufferGeometry(), clipMaterial));
  const templeClip = createTempleClip(clipRoot);
  const clipShader = { uniforms: UniformsUtils.clone(ShaderLib.standard!.uniforms),
    vertexShader: ShaderLib.standard!.vertexShader, fragmentShader: ShaderLib.standard!.fragmentShader };
  Reflect.apply(clipMaterial.onBeforeCompile, clipMaterial, [clipShader, backend]);
  const cameraSource = (): CanvasTexture | null => clipShader.uniforms.templeCameraSource!.value;
  const templeVisibility = {
    get configuration(): TempleVisibilityConfiguration | null { return visibilityState ? {...visibilityState} : null; },
    set(value: TempleVisibilityConfiguration | null) {
      assert.equal(visibilityDisposals, 0, 'a retired visibility module cannot be reused');
      if (value) validateTempleVisibility(value);
      visibilityState = value ? {...value} : null;
      visibilityCamera = null;
      events.push(value ? 'visibility:set' : 'visibility:clear');
    },
    prepare(rawMatrix: readonly number[], source?: CanvasTexture) {
      assert.ok(visibilityState, 'absent appearance must not call prepare');
      assert.equal(visibilityDisposals, 0);
      if (visibilityState.method === 'temple-side-depth-v3') {
        assert.equal(source, (Reflect.get(renderer, 'scene') as Scene).background,
          'the renderer supplies the same paired camera to its visibility module');
        if (visibilityState.frontalOcclusionWeight > 0) {
          assert.ok(source instanceof CanvasTexture);
          visibilityCamera = source;
        }
      }
      preparedCameraImages.push(source?.image);
      events.push('visibility:prepare'); preparedMatrices.push(Array.from(rawMatrix));
      if (failPreparation) throw new Error('Visibility target preparation failed');
    },
    dispose() { visibilityDisposals++; visibilityState = null; visibilityCamera = null; events.push('visibility:dispose'); },
  };
  const surfaceMesh = new Mesh(new BufferGeometry(), new MeshStandardMaterial());
  const headProxy = new Mesh(new BufferGeometry(), new MeshStandardMaterial());
  (Reflect.get(renderer, 'scene') as Scene).add(surfaceMesh);
  (Reflect.get(renderer, 'facePose') as Group).add(headProxy);
  (Reflect.get(renderer, 'eyewearPose') as Group).add(clipRoot);
  Object.assign(renderer, {
    canonicalPositions: canonical.positions, faceSurface: new FaceSurface(canonical.positions),
    nasalShape, templeClip, templeVisibility, surfaceMesh, headProxy,
  });
  t.after(() => renderer.dispose());
  const width = 1600, height = 900;
  const frame = { width, height, getContext() { throw new Error('Live geometry read source RGB'); } } as unknown as HTMLCanvasElement;
  const c = Math.sqrt(3) / 2, s = 0.5;
  const matrix = [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 1.5, -2, -45, 1];
  const focalPixels = height / (2 * Math.tan(63 * Math.PI / 360));
  const meanDepth = Array.from({ length: 468 }, (_, index) =>
    s * canonical.positions[index * 3]! - c * canonical.positions[index * 3 + 2]! + 45)
    .reduce((sum, depth) => sum + depth / 468, 0);
  // Independent pinhole equations: no Three matrix/projection utility is used
  // to produce these expected image observations.
  const landmarks = Array.from({ length: 478 }, (_, index) => {
    const x = canonical.positions[index * 3] ?? 0;
    const y = canonical.positions[index * 3 + 1] ?? 0;
    const z = canonical.positions[index * 3 + 2] ?? 0;
    const cameraX = c * x + s * z + 1.5;
    const cameraY = y - 2;
    const cameraZ = -s * x + c * z - 45;
    return {
      x: 0.5 + focalPixels * cameraX / (-cameraZ * width),
      y: 0.5 - focalPixels * cameraY / (-cameraZ * height),
      // Encode the same known face depth, rather than an unrelated flat mesh.
      z: (-cameraZ / meanDepth - 1) * focalPixels / width,
    };
  });
  const detection = { landmarks, matrix, inferenceMs: 1 };
  const raw = new FaceSurface(canonical.positions);
  assert.ok(raw.reconstruct(landmarks, matrix, width / height));
  const expectedShape = nasalShape.apply({ surfacePositions: raw.positions, rawMatrix: matrix });
  assert.ok(expectedShape.accepted);
  assert.ok(expectedShape.surfacePositions.some((value, index) => value !== raw.positions[index]),
    'this observation exercises a nonzero nasal shape');
  renderer.present(frame, detection);
  assert.deepEqual(events, ['visibility:clear', 'visibility:set', 'visibility:prepare', 'render']);
  assert.deepEqual(preparedMatrices, [matrix], 'visibility receives the original paired pose');
  assert.deepEqual(preparedCameraImages, [frame], 'visibility receives the original paired image');
  assert.deepEqual(size, [1280, 720]);
  assert.ok(renderer.projectionResidualPx !== null && renderer.projectionResidualPx < 1e-8);
  const scene = renderedScene as Scene | null;
  const camera = renderedCamera as PerspectiveCamera | null;
  assert.ok(scene && camera);
  assert.equal(camera.aspect, width / height);
  assert.equal((scene.background as Texture).image, frame);
  const face = scene.getObjectByName('Tracked canonical face (centimeters)');
  assert.ok(face?.visible);
  assert.deepEqual(face.matrix.elements, matrix, 'raw column-major packing is preserved');
  const eyewear = scene.getObjectByName('Eyewear bridge pose (centimeters)');
  assert.ok(eyewear?.visible);
  assert.deepEqual(eyewear.matrix.elements, matrix, 'the known matching pose reaches the rendered glasses');
  assert.deepEqual(renderer.captureSnapshot?.rawMatrix, matrix);
  assert.deepEqual(renderer.captureSnapshot?.eyewearMatrix, eyewear.matrix.elements);
  assert.deepEqual(captureSnapshot().surfacePositions, expectedShape.surfacePositions,
    'live presentation applies the fixed shape once to its original reconstructed surface');
  assert.deepEqual(captureSnapshot().occlusion, {
    method: NASAL_SHAPE_VERSION, selectedShapeId: NASAL_SHAPE_ID,
    appliedShapeId: NASAL_SHAPE_ID, status: 'applied', rejectionReasons: [],
  });
  const liveClip = renderer.templeClipConfiguration;
  assert.deepEqual(liveClip, {method: 'temple-end-blend-v3', negativeXCutoffLocalZM: -.105,
    positiveXCutoffLocalZM: -.105, fadeLengthLocalM: .015});
  assert.deepEqual(captureSnapshot().templeClip, liveClip, 'a live presentation identifies its actual clip policy');
  const borrowedCamera = clipShader.uniforms.templeCameraSource!.value;
  assert.ok(borrowedCamera instanceof CanvasTexture);
  assert.equal(borrowedCamera.image, frame);
  const liveVisibility: TempleVisibilityConfiguration = { method: 'temple-side-depth-v3',
    negativeXWeight: 1, positiveXWeight: 0, frontalOcclusionWeight: 0, coverage: 'alpha-to-coverage' };
  assert.deepEqual(captureSnapshot().templeVisibility, liveVisibility,
    'this known yaw selects the camera-near negative-X arm without affecting the original pose');
  const firstVisibility = captureSnapshot().templeVisibility!;
  Object.assign(firstVisibility, {negativeXWeight: 0.2, positiveXWeight: 0.3, frontalOcclusionWeight: .8});
  assert.deepEqual(captureSnapshot().templeVisibility, liveVisibility, 'snapshots own their actual visibility configuration');
  const policy = renderer.templeVisibilityPolicy;
  assert.equal(policy.method, liveVisibility.method);
  assert.equal(policy.coverage, liveVisibility.coverage);
  Object.assign(policy.parameters, {placementCm: 99});
  assert.notEqual(renderer.templeVisibilityPolicy.parameters.placementCm, 99, 'header parameters are also owned copies');

  const otherPixels = { width, height, getContext() { throw new Error('Changed image read source RGB'); } } as unknown as HTMLCanvasElement;
  renderer.present(otherPixels, detection);
  assert.equal((scene.background as Texture).image, otherPixels, 'the newly paired source reaches the background');
  assert.equal(clipShader.uniforms.templeCameraSource!.value.image, otherPixels,
    'the same image change also reaches terminal camera composition');
  assert.deepEqual(captureSnapshot().surfacePositions, expectedShape.surfacePositions,
    'surface geometry depends on its detection, not the source image pixels');
  renderer.present(frame, detection);
  assert.equal(clipShader.uniforms.templeCameraSource!.value.image, frame, 'camera composition follows A/B/A pairing');
  assert.deepEqual(preparedCameraImages, [frame, otherPixels, frame], 'visibility camera input follows the same A/B/A pairing');
  assert.deepEqual(captureSnapshot().surfacePositions, expectedShape.surfacePositions,
    'A/B/A presentation does not accumulate the shape');

  // A captured shaped surface must bypass live shaping. Historical captures can
  // also contain different saved geometry, without current-method metadata.
  const liveSurface = Array.from(captureSnapshot().surfacePositions);
  const recordedClip = {method: 'temple-end-blend-v3', fadeLengthLocalM: .012,
    negativeXCutoffLocalZM: -.075, positiveXCutoffLocalZM: -.055} satisfies TempleClipConfiguration;
  const expectedRecordedClip = {...recordedClip};
  const recordedVisibility = { method: 'temple-side-depth-v3', negativeXWeight: 0.23,
    positiveXWeight: 0.81, frontalOcclusionWeight: .67, coverage: 'ordered-dither' } satisfies TempleVisibilityConfiguration;
  const expectedRecordedVisibility = {...recordedVisibility};
  renderer.present(otherPixels, detection, liveSurface, recordedClip, recordedVisibility);
  assert.deepEqual(captureSnapshot().templeClip, recordedClip, 'new replay restores its two recorded endpoints');
  assert.deepEqual(captureSnapshot().templeVisibility, expectedRecordedVisibility,
    'replay preserves recorded side weights and coverage instead of recomputing current live defaults');
  assert.equal(visibilityCamera, scene.background, 'positive saved frontal occlusion uses the current replay image');
  recordedVisibility.negativeXWeight = 0.99;
  recordedVisibility.frontalOcclusionWeight = .02;
  assert.deepEqual(captureSnapshot().templeVisibility, expectedRecordedVisibility, 'replay owns its caller configuration');
  recordedClip.negativeXCutoffLocalZM = -.18;
  assert.equal(captureSnapshot().templeClip?.negativeXCutoffLocalZM, -.075, 'replay does not retain mutable caller metadata');
  const exposedClip = captureSnapshot().templeClip!;
  Object.assign(exposedClip, {positiveXCutoffLocalZM: -.19});
  assert.equal(captureSnapshot().templeClip?.positiveXCutoffLocalZM, -.055, 'capture metadata is an owned copy');
  const legacyHard: TempleClipConfiguration = {method: 'temple-end-clip-v1',
    negativeXCutoffLocalZM: -.07, positiveXCutoffLocalZM: -.085};
  const legacyFade: TempleClipConfiguration = {method: 'temple-end-fade-v2',
    negativeXCutoffLocalZM: -.082, positiveXCutoffLocalZM: -.093, fadeLengthLocalM: .003, coverage: 'ordered-dither'};
  const legacyVisibility: TempleVisibilityConfiguration = {method: 'temple-side-depth-v1',
    negativeXWeight: .24, positiveXWeight: .78, coverage: 'ordered-dither'};
  const legacyViewVisibility: TempleVisibilityConfiguration = {method: 'temple-side-depth-v2',
    negativeXWeight: .19, positiveXWeight: .72, coverage: 'ordered-dither'};
  renderer.present(otherPixels, detection, liveSurface, legacyHard, null);
  assert.deepEqual(captureSnapshot().templeClip, legacyHard, 'historical hard/asymmetric endpoints remain hard');
  assert.equal(clipShader.uniforms.templeCameraSource!.value, null);
  renderer.present(frame, detection, liveSurface, legacyFade, legacyVisibility);
  assert.deepEqual(captureSnapshot().templeClip, legacyFade, 'historical fade length and coverage remain exact');
  assert.deepEqual(captureSnapshot().templeVisibility, legacyVisibility, 'historical visibility does not become a v2 policy');
  assert.equal(clipShader.uniforms.templeCameraSource!.value, null);
  assert.equal(visibilityCamera, null);
  renderer.present(frame, detection, liveSurface, legacyFade, legacyViewVisibility);
  assert.deepEqual(captureSnapshot().templeVisibility, legacyViewVisibility, 'v2 replay does not acquire v3 frontal occlusion');
  assert.equal(visibilityCamera, null, 'v1 and v2 replay do not borrow camera pixels for visibility');
  renderer.present(otherPixels, detection, liveSurface, expectedRecordedClip, expectedRecordedVisibility);
  assert.deepEqual(captureSnapshot().templeClip, expectedRecordedClip);
  assert.deepEqual(captureSnapshot().templeVisibility, expectedRecordedVisibility,
    'the saved v3 weights and frontal occlusion return unchanged after v1/v2 replay');
  assert.equal(cameraSource()?.image, otherPixels);
  const priorLegacyPreparations = preparedMatrices.length;
  renderer.present(otherPixels, detection, liveSurface);
  assert.deepEqual(captureSnapshot().surfacePositions, expectedShape.surfacePositions,
    'replay does not apply the fixed shape a second time');
  assert.equal(captureSnapshot().occlusion?.method, 'recorded-surface');
  assert.equal(captureSnapshot().occlusion?.status, 'recorded');
  assert.equal(captureSnapshot().templeClip, null, 'legacy replay with no clip metadata clears the previous recorded endpoints');
  assert.equal(captureSnapshot().templeVisibility, null, 'legacy replay clears the previous visibility weights');
  renderer.present(otherPixels, detection, undefined, null, null);
  assert.equal(captureSnapshot().templeClip, null, 'explicit legacy clipping stays disabled even without a saved surface');
  assert.equal(captureSnapshot().templeVisibility, null, 'geometry-free legacy replay keeps visibility disabled');
  assert.equal(captureSnapshot().occlusion?.method, NASAL_SHAPE_VERSION,
    'reconstructing an unsaved surface must retain truthful current-method provenance');
  assert.deepEqual(captureSnapshot().surfacePositions, expectedShape.surfacePositions);
  const recordedSurface = Array.from(captureSnapshot().surfacePositions);
  recordedSurface[3] = recordedSurface[3]! + 0.05;
  const expectedSurface = new Float32Array(recordedSurface);
  const replayFrame = { width, height, getContext() { throw new Error('Replay read RGB again'); } } as unknown as HTMLCanvasElement;
  renderer.present(replayFrame, detection, recordedSurface);
  assert.equal(preparedMatrices.length, priorLegacyPreparations, 'legacy and explicit-null replay do not prepare visibility');
  assert.deepEqual(captureSnapshot().surfacePositions, expectedSurface);
  recordedSurface[3] = recordedSurface[3]! + 1;
  assert.deepEqual(captureSnapshot().surfacePositions, expectedSurface, 'recorded input is copied, not retained by reference');
  assert.throws(() => renderer.present(replayFrame, detection, liveSurface,
    {...liveClip, positiveXCutoffLocalZM: -.02}), /temple clipping configuration is invalid/);
  assert.equal(renderer.captureSnapshot, null, 'invalid recorded clipping cannot expose a stale successful snapshot');
  assert.throws(() => renderer.present(replayFrame, detection, [0]), /recorded face surface is invalid/);
  assert.equal(renderer.captureSnapshot, null, 'failed replay cannot expose a prior snapshot');
  assert.throws(() => renderer.present(replayFrame, detection, liveSurface, liveClip,
    {...liveVisibility, positiveXWeight: Number.NaN}), /temple visibility configuration is invalid/);
  assert.equal(renderer.captureSnapshot, null);
  assert.equal(templeVisibility.configuration, null, 'invalid visibility cannot retain a previous appearance');
  assert.throws(() => renderer.present(replayFrame, detection, liveSurface, liveClip,
    {...liveVisibility, frontalOcclusionWeight: Number.NaN}), /temple visibility configuration is invalid/);
  assert.equal(renderer.captureSnapshot, null);
  assert.equal(visibilityCamera, null);
  renderer.present(frame, detection);
  assert.deepEqual(captureSnapshot().surfacePositions, expectedShape.surfacePositions,
    'live presentation resumes from raw observations after a failed replay');
  assert.deepEqual(captureSnapshot().templeClip, liveClip, 'returning to live resets the model endpoint after replay or failure');
  assert.deepEqual(captureSnapshot().templeVisibility, liveVisibility);

  const renderedBeforeFailure = events.filter(event => event === 'render').length;
  failPreparation = true;
  assert.throws(() => renderer.present(frame, detection, liveSurface, expectedRecordedClip, expectedRecordedVisibility),
    /Visibility target preparation failed/);
  assert.equal(events.filter(event => event === 'render').length, renderedBeforeFailure,
    'failed target preparation cannot render the main scene using stale depth');
  assert.equal(renderer.captureSnapshot, null);
  assert.equal(templeVisibility.configuration, null);
  assert.equal(templeClip.configuration, null);
  assert.equal(visibilityCamera, null, 'failure clears any borrowed frontal camera');
  assert.equal(clipShader.uniforms.templeCameraSource!.value, null, 'failed preparation clears borrowed camera composition');
  assert.equal(face.visible || eyewear.visible || surfaceMesh.visible || headProxy.visible, false);
  assert.equal(renderer.projectionResidualPx, null);
  failPreparation = false;
  renderer.present(otherPixels, detection);
  assert.deepEqual(captureSnapshot().templeVisibility, liveVisibility);
  assert.deepEqual(captureSnapshot().surfacePositions, expectedShape.surfacePositions,
    'recovery prepares fresh visibility around the unchanged accepted nasal surface');
  const preparationsBeforeEmpty = preparedMatrices.length;
  assert.throws(() => renderer.present({width: 0, height} as HTMLCanvasElement, detection), /camera frame is empty/);
  assert.equal(renderer.captureSnapshot, null, 'empty input clears the preceding successful snapshot');
  assert.equal(templeVisibility.configuration, null);
  assert.equal(visibilityCamera, null);
  assert.equal(clipShader.uniforms.templeCameraSource!.value, null, 'empty input cannot keep the preceding camera binding');
  assert.equal(face.visible || eyewear.visible || surfaceMesh.visible || headProxy.visible, false);
  assert.equal(preparedMatrices.length, preparationsBeforeEmpty);
  renderer.present(frame, detection);
  assert.deepEqual(captureSnapshot().templeVisibility, liveVisibility, 'valid input recovers after empty input');

  backend.capabilities.samples = 0;
  const preparationsBeforeUnsupportedReplay = preparedMatrices.length;
  const unsupportedHistoricalFade = createTempleFadeConfiguration(-.105, 4);
  assert.throws(() => renderer.present(frame, detection, liveSurface, unsupportedHistoricalFade, null),
    /recorded temple fade requires multisampling/);
  assert.equal(renderer.captureSnapshot, null, 'unsupported saved coverage cannot expose the preceding snapshot');
  assert.equal(templeClip.configuration, null);
  assert.equal(templeVisibility.configuration, null);
  assert.equal(preparedMatrices.length, preparationsBeforeUnsupportedReplay);
  renderer.present(frame, detection);
  assert.deepEqual(captureSnapshot().templeClip, liveClip,
    'camera RGB dissolution keeps the same v3 policy on zero-sample backends');
  assert.deepEqual(captureSnapshot().templeVisibility, {...liveVisibility, coverage: 'ordered-dither'});
  renderer.present(otherPixels, detection, liveSurface, expectedRecordedClip, expectedRecordedVisibility);
  assert.deepEqual(captureSnapshot().templeClip, expectedRecordedClip, 'saved v3 itself needs no multisampling');
  assert.deepEqual(captureSnapshot().templeVisibility, expectedRecordedVisibility);
  backend.capabilities.samples = 4;
  renderer.present(frame, detection);
  assert.deepEqual(captureSnapshot().templeClip, liveClip);
  assert.deepEqual(captureSnapshot().templeVisibility, liveVisibility);

  const absent: Detection = { landmarks: [], matrix: null, inferenceMs: 1 };
  const preparationsBeforeNoFace = preparedMatrices.length;
  renderer.present(frame, absent);
  assert.equal(face.visible, false, 'missing face hides the full attachment and occluder parent');
  assert.equal(renderer.projectionResidualPx, null);
  assert.equal(renderer.captureSnapshot, null);
  assert.equal(eyewear.visible, false, 'no-face presentation hides the clipped frame as a whole');
  assert.equal(surfaceMesh.visible || headProxy.visible, false);
  assert.equal(templeVisibility.configuration, null);
  assert.equal(templeClip.configuration, null);
  assert.equal(clipShader.uniforms.templeCameraSource!.value, null, 'no-face state has no borrowed terminal camera source');
  assert.equal(preparedMatrices.length, preparationsBeforeNoFace, 'no-face output does not prepare visibility');
  assert.equal((scene.background as Texture).image, frame, 'camera remains visible without a face');
  renderer.present(otherPixels, detection);
  assert.deepEqual(captureSnapshot().surfacePositions, expectedShape.surfacePositions,
    'a face after an empty detection has fresh single-application geometry');
  assert.deepEqual(captureSnapshot().templeClip, liveClip, 'face reentry has fresh live clipping provenance');
  assert.deepEqual(captureSnapshot().templeVisibility, liveVisibility, 'face reentry cannot retain replay or no-face state');
  renderer.dispose();
  renderer.dispose();
  assert.equal(disposals, 1);
  assert.equal(contextLosses, 1);
  assert.equal(visibilityDisposals, 1, 'owned visibility resources retire exactly once');
  assert.equal(visibilityCamera, null);
  assert.equal(clipShader.uniforms.templeCameraSource!.value, null);
  assert.equal(renderer.present(frame, detection), false, 'retired renderer cannot present into a new session');
  assert.equal(renderer.captureSnapshot, null);
  assert.throws(() => templeClip.set(liveClip), /disposed/, 'renderer disposal also retires the material wrapper');
});

test('an asset parsed after cancellation is fully disposed without acquiring WebGL', async (t) => {
  class DecodedBitmap {
    closes = 0;
    close(): void { this.closes++; }
  }
  const bitmapConstructor = Object.getOwnPropertyDescriptor(globalThis, 'ImageBitmap');
  Object.defineProperty(globalThis, 'ImageBitmap', { configurable: true, value: DecodedBitmap });
  t.after(() => {
    if (bitmapConstructor) Object.defineProperty(globalThis, 'ImageBitmap', bitmapConstructor);
    else Reflect.deleteProperty(globalThis, 'ImageBitmap');
  });
  const bitmap = new DecodedBitmap();
  const gltf = await loadGlasses(bitmap);
  const counts = disposalCounts(gltf);
  assetFetch(t);
  let completeParse!: (gltf: GLTF) => void;
  let enteredParse!: () => void;
  const parsing = new Promise<void>(resolve => { enteredParse = resolve; });
  t.mock.method(GLTFLoader.prototype, 'parseAsync', () => {
    enteredParse();
    return new Promise<GLTF>(resolve => { completeParse = resolve; });
  });
  let contexts = 0;
  const canvas = { getContext() { contexts++; return null; } } as unknown as HTMLCanvasElement;
  const abort = new AbortController();
  const starting = TryOnRenderer.create(canvas, abort.signal);
  await parsing;
  abort.abort();
  completeParse(gltf);
  await assert.rejects(starting, { name: 'AbortError' });
  assert.equal(contexts, 0);
  assert.ok([...counts.keys()].filter(resource => resource instanceof Texture).length >= 2,
    'separate imported textures share the decoded image fixture');
  assert.ok([...counts.values()].every(count => count === 1), 'shared geometry, material and texture resources dispose exactly once');
  assert.equal(bitmap.closes, 1, 'shared decoded image pixels are closed once after cancellation');
});
