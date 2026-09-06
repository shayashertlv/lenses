import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import type { TestContext } from 'node:test';
import { Box3, BufferGeometry, Material, Mesh, MeshPhysicalMaterial, Texture, Vector3 } from 'three';
import type { PerspectiveCamera, Scene } from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import {
  GLASSES_METERS_TO_CENTIMETERS, GLASSES_OFFSET_CM, TryOnRenderer,
} from '../../src/render/renderer.ts';
import type { Detection } from '../../src/runtime/detector.ts';
import { FaceSurface } from '../../src/render/face-surface.ts';

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
  const backend = {
    setSize(width: number, height: number) { size = [width, height]; },
    render(scene: Scene, camera: PerspectiveCamera) { renderedScene = scene; renderedCamera = camera; },
    dispose() { disposals++; },
    forceContextLoss() { contextLosses++; },
  };
  // Exercise the actual presentation path without requiring a GPU in unit tests.
  const renderer = Reflect.construct(TryOnRenderer, [backend]) as TryOnRenderer;
  Object.assign(renderer, { canonicalPositions: canonical.positions, faceSurface: new FaceSurface(canonical.positions) });
  t.after(() => renderer.dispose());
  const width = 1600, height = 900;
  const frame = { width, height } as HTMLCanvasElement;
  const c = Math.sqrt(3) / 2, s = 0.5;
  const matrix = [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 1.5, -2, -45, 1];
  const focalPixels = height / (2 * Math.tan(63 * Math.PI / 360));
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
      z: 0,
    };
  });
  renderer.present(frame, { landmarks, matrix, inferenceMs: 1 });
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

  // Replay must retain the recorded occluder even if JPEG decoding changes RGB
  // evidence. Re-estimating it would break the captured geometry/image pairing.
  const recordedSurface = Array.from(renderer.captureSnapshot!.surfacePositions);
  recordedSurface[3] = recordedSurface[3]! + 0.05;
  const expectedSurface = new Float32Array(recordedSurface);
  Object.assign(renderer, { canonicalIndices: canonical.indices });
  const replayFrame = { width, height, getContext() { throw new Error('Replay read RGB again'); } } as unknown as HTMLCanvasElement;
  renderer.present(replayFrame, { landmarks, matrix, inferenceMs: 1 }, recordedSurface);
  assert.deepEqual(renderer.captureSnapshot!.surfacePositions, expectedSurface);
  recordedSurface[3] = recordedSurface[3]! + 1;
  assert.deepEqual(renderer.captureSnapshot!.surfacePositions, expectedSurface, 'recorded input is copied, not retained by reference');
  assert.throws(() => renderer.present(replayFrame, { landmarks, matrix, inferenceMs: 1 }, [0]), /recorded face surface is invalid/);

  const absent: Detection = { landmarks: [], matrix: null, inferenceMs: 1 };
  renderer.present(frame, absent);
  assert.equal(face.visible, false, 'missing face hides the full attachment and occluder parent');
  assert.equal(renderer.projectionResidualPx, null);
  assert.equal(renderer.captureSnapshot, null);
  assert.equal((scene.background as Texture).image, frame, 'camera remains visible without a face');
  renderer.dispose();
  renderer.dispose();
  assert.equal(disposals, 1);
  assert.equal(contextLosses, 1);
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
