import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { Box3, BufferGeometry, Material, Mesh, MeshPhysicalMaterial, Texture, Vector3 } from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { eyewearById, GLASSES_METERS_TO_CENTIMETERS } from '../../src/render/eyewear.ts';
import { TryOnRenderer } from '../../src/render/renderer.ts';

test('the supplied clear-lens asset preserves preview attachment, clear optics and embedded frame textures', async t => {
  const definition = eyewearById('tom-ford-clear');
  const bytes = await readFile(new URL(`../../public${definition.assetUrl}`, import.meta.url));
  assert.ok(bytes.byteLength < 3_700_000, 'the second frame stays within the existing asset download budget');
  assert.equal(bytes.readUInt32LE(0), 0x46546c67, 'the model is binary glTF');
  assert.equal(bytes.readUInt32LE(4), 2, 'the model uses glTF 2');
  assert.equal(bytes.readUInt32LE(8), bytes.byteLength, 'the GLB is complete');
  assert.equal(bytes.readUInt32LE(16), 0x4e4f534a, 'the first chunk contains its JSON document');
  const document = JSON.parse(bytes.toString('utf8', 20, 20 + bytes.readUInt32LE(12))) as {
    buffers: { uri?: string }[];
    images: { bufferView?: number; uri?: string; mimeType?: string }[];
    extensionsRequired?: string[];
  };
  assert.ok(document.buffers.length > 0 && document.buffers.every(buffer => buffer.uri === undefined),
    'all geometry is embedded in the local asset');
  assert.ok(document.images.length > 0, 'the supplied frame appearance retains embedded imagery');
  assert.ok(document.images.every(image => Number.isInteger(image.bufferView) && image.uri === undefined
    && (image.mimeType === 'image/png' || image.mimeType === 'image/jpeg')),
  'frame images need neither external requests nor a new image-codec dependency');
  for (const decoder of ['KHR_draco_mesh_compression', 'EXT_meshopt_compression', 'KHR_texture_basisu']) {
    assert.ok(!document.extensionsRequired?.includes(decoder), 'no additional model decoder is required');
  }

  // Only browser image decoding is unavailable in Node. Parse the actual GLB
  // positions, transforms and material properties with the real Three loader.
  const gltf = await new GLTFLoader().register(() => ({
    name: 'NodeImageDecoder',
    loadTexture: async () => new Texture(),
  })).parseAsync(new Uint8Array(bytes).buffer, '/models/');
  const resources = new Set<BufferGeometry | Material | Texture>();
  t.after(() => { for (const resource of resources) resource.dispose(); });
  gltf.scene.updateMatrixWorld(true);
  const bounds = new Box3();
  const lensBounds = new Box3();
  const lenses = new Set<MeshPhysicalMaterial>();
  let triangles = 0;
  let texturedFrame = false;
  gltf.scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    resources.add(object.geometry);
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) {
      resources.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) resources.add(value);
      if (material instanceof MeshPhysicalMaterial && material.transmission > 0) lenses.add(material);
      else if ('map' in material && material.map instanceof Texture) texturedFrame = true;
    }
    const positions = object.geometry.getAttribute('position');
    triangles += (object.geometry.index?.count ?? positions.count) / 3;
    const isLens = materials.every(material => material instanceof MeshPhysicalMaterial && material.transmission > 0);
    for (let index = 0; index < positions.count; index++) {
      const point = new Vector3().fromBufferAttribute(positions, index).applyMatrix4(object.matrixWorld);
      assert.ok(point.toArray().every(Number.isFinite), 'geometry contains finite positions');
      bounds.expandByPoint(point);
      if (isLens) lensBounds.expandByPoint(point);
    }
  });
  assert.ok(Number.isInteger(triangles) && triangles > 1_000 && triangles <= 100_000,
    'the supplied complete eyewear geometry remains within the current mesh budget');
  assert.ok(texturedFrame, 'making lenses clear does not discard frame textures');
  const size = bounds.getSize(new Vector3());
  assert.ok(Math.abs(size.x * 1_000 - definition.assumedWidthMm) < 1,
    'exported width matches its declared visual assumption, not a measured wearer fit');
  assert.ok(Math.abs(bounds.max.x + bounds.min.x) < 0.001, 'the frame is centered symmetrically about X');
  assert.ok(bounds.min.z < -0.1 && bounds.max.z > 0 && bounds.max.z < 0.01,
    'the long temples extend behind the front along -Z');
  assert.ok(bounds.min.y < -0.01 && bounds.max.y > 0.01 && size.y < 0.06,
    'the frame remains upright along +Y');
  assert.ok(Math.abs(bounds.max.z * GLASSES_METERS_TO_CENTIMETERS + definition.offsetCm[2] - 6.691763) < 1e-6,
    'the clear model front retains the accepted glasses front plane');
  assert.ok(lensBounds.min.x < -0.01 && lensBounds.max.x > 0.01,
    'clear lens surfaces remain on both sides of the bridge');
  assert.ok(lenses.size > 0, 'lens surfaces use actual transmissive physical materials');
  for (const lens of lenses) {
    assert.equal(lens.transmission, 1);
    assert.ok(Math.abs(lens.roughness - 0.035) < 1e-6);
    assert.ok(Math.abs(lens.ior - 1.5) < 1e-6);
    assert.equal(lens.metalness, 0);
    assert.deepEqual(lens.color.toArray(), [1, 1, 1], 'clear optics have no color tint');
    assert.equal(lens.map, null, 'the old tinted color map is not retained on clear lenses');
    assert.equal(lens.alphaMap, null, 'clear lenses are not simulated with an alpha mask');
    assert.equal(lens.opacity, 1);
    assert.equal(lens.depthTest, true, 'ordinary face-depth testing is retained');
    assert.deepEqual(lens.attenuationColor.toArray(), [1, 1, 1], 'lens volume adds no colored attenuation');
  }
});

test('unknown frame selections fail before any asset request or graphics acquisition', async t => {
  let requests = 0;
  let contexts = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    requests++;
    throw new Error('Unexpected asset request');
  });
  const canvas = { getContext() { contexts++; return null; } } as unknown as HTMLCanvasElement;
  for (const id of ['missing-frame', '__proto__', 'constructor', '/models/unregistered.glb']) {
    assert.throws(() => eyewearById(id), /frame is unavailable/);
    // Exercise the runtime JavaScript boundary, including strings outside the
    // compile-time registry union, without weakening the public TypeScript API.
    const starting = Reflect.apply(TryOnRenderer.create, TryOnRenderer,
      [canvas, new AbortController().signal, id]) as Promise<TryOnRenderer>;
    await assert.rejects(starting, /frame is unavailable/);
  }
  assert.equal(requests, 0);
  assert.equal(contexts, 0);
});
