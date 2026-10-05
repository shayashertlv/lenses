import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {
  BufferGeometry, CanvasTexture, Group, Material, Mesh, MeshPhysicalMaterial, MeshStandardMaterial,
  ShaderLib, SRGBColorSpace, Texture, UniformsUtils,
} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {EYEWEAR} from '../src/eyewear/catalog.ts';
import {
  createTempleBlendConfiguration, createTempleClip, TEMPLE_BLEND_METHOD, TEMPLE_BLEND_LENGTH_LOCAL_M, validateTempleClip,
} from '../src/render/temple-clip.ts';
import type {TempleBlendConfiguration, TempleClipConfiguration} from '../src/render/temple-clip.ts';

type CompileInput = Parameters<Material['onBeforeCompile']>[0];
const clipping = (negativeXCutoffLocalZM: number, positiveXCutoffLocalZM = negativeXCutoffLocalZM): TempleClipConfiguration =>
  ({method: TEMPLE_BLEND_METHOD, negativeXCutoffLocalZM, positiveXCutoffLocalZM, fadeLengthLocalM: TEMPLE_BLEND_LENGTH_LOCAL_M});
// Exercise material callbacks and live uniform ownership with Three's real
// shader source/uniforms. Full GPU compilation/pixels belong to browser tests.
function compile(material: Material, backend: object = {}) {
  const source = ShaderLib.standard!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader'> = {
    uniforms: UniformsUtils.clone(source.uniforms), vertexShader: source.vertexShader,
    fragmentShader: source.fragmentShader,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, backend]);
  return shader;
}

function pairedCamera(width = 960, height = 720): CanvasTexture {
  const texture = new CanvasTexture({width, height} as HTMLCanvasElement);
  texture.colorSpace = SRGBColorSpace;
  return texture;
}

const manifest = JSON.parse(await readFile(new URL('../public/models/manifest.json', import.meta.url), 'utf8')) as
  Record<string, {sha256: string}>;

for (const definition of Object.values(EYEWEAR)) {
  test(`${definition.name}: the fixed clip excludes rear stems while preserving the real front and lenses`, async t => {
    const bytes = await readFile(new URL('../public' + definition.assetUrl, import.meta.url));
    assert.equal(createHash('sha256').update(bytes).digest('hex'), manifest['public' + definition.assetUrl]!.sha256);
    // Geometry and real material classes are parsed unchanged. Only embedded
    // raster decoding is replaced because Node has no browser image decoder.
    const gltf = await new GLTFLoader().register(() => ({
      name: 'NodeTempleAssetImages', loadTexture: async () => new Texture(),
    })).parseAsync(new Uint8Array(bytes).buffer, '/models/');
    const meshes: Mesh[] = [], resources = new Set<BufferGeometry | Material | Texture>();
    gltf.scene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      meshes.push(object); resources.add(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        resources.add(material);
        for (const value of Object.values(material)) if (value instanceof Texture) resources.add(value);
      }
    });
    t.after(() => { for (const resource of resources) resource.dispose(); });
    const before = meshes.map(mesh => ({
      geometry: mesh.geometry, material: mesh.material,
      positions: mesh.geometry.getAttribute('position').array.slice(),
      normals: mesh.geometry.getAttribute('normal').array.slice(),
      index: mesh.geometry.getIndex()!.array.slice(),
      attributes: {...mesh.geometry.attributes},
      materials: (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).map(material => ({
        material, hook: material.onBeforeCompile, key: material.customProgramCacheKey,
        version: material.version, side: material.side, depthTest: material.depthTest,
        depthWrite: material.depthWrite, transparent: material.transparent, toneMapped: material.toneMapped,
      })),
    }));
    const clip = createTempleClip(gltf.scene);
    t.after(() => clip.dispose());
    const configuration = clipping(definition.templeClipLocalZM);
    clip.set(configuration);
    assert.ok(configuration.negativeXCutoffLocalZM <= -.03, 'the entire optical-front region stays ahead of the cutoff');
    let removedFrameVertices = 0, retainedFrontVertices = 0, lensVertices = 0, armVertices = 0, drawnArmVertices = 0, armRearZ = 0;
    let armLowestY = 0, drawnLowestY = 0;
    const crossingSides = new Set<number>();
    for (const [meshIndex, mesh] of meshes.entries()) {
      const saved = before[meshIndex]!, p = mesh.geometry.getAttribute('position'), ix = mesh.geometry.getIndex()!;
      const lens = mesh.material instanceof MeshPhysicalMaterial && mesh.material.transmission > 0;
      assert.equal(mesh.geometry, saved.geometry);
      assert.equal(mesh.material, saved.material);
      assert.deepEqual(p.array, saved.positions);
      assert.deepEqual(mesh.geometry.getAttribute('normal').array, saved.normals);
      assert.deepEqual(ix.array, saved.index);
      for (const [name, attribute] of Object.entries(saved.attributes)) assert.equal(mesh.geometry.getAttribute(name), attribute);
      for (let i = 0; i < p.count; i++) {
        if (lens) {
          lensVertices++;
          assert.ok(p.getZ(i) > configuration.negativeXCutoffLocalZM, 'every actual lens vertex lies ahead of the clip');
        } else if (p.getZ(i) < configuration.negativeXCutoffLocalZM) {
          removedFrameVertices++;
          assert.ok(Math.abs(p.getX(i)) > .045, 'discarded source vertices belong to lateral stems, not the bridge/front');
        } else if (p.getZ(i) >= -.03) retainedFrontVertices++;
        // The arm now runs through its ear hook (2026-09-18): what is cut is only the last few millimetres of the
        // asset's own tip, and the head occluder is what takes the hook away rather than a cut in mesh space.
        if (!lens && Math.abs(p.getX(i)) > .045) {armVertices++; armRearZ = Math.min(armRearZ, p.getZ(i));
          armLowestY = Math.min(armLowestY, p.getY(i));
          if (p.getZ(i) >= configuration.negativeXCutoffLocalZM) {drawnArmVertices++; drawnLowestY = Math.min(drawnLowestY, p.getY(i));}}
      }
      if (!lens) for (let i = 0; i < ix.count; i += 3) {
        const ids = [ix.getX(i), ix.getX(i + 1), ix.getX(i + 2)];
        if (ids.some(id => p.getZ(id) < configuration.negativeXCutoffLocalZM)
            && ids.some(id => p.getZ(id) >= configuration.negativeXCutoffLocalZM)) crossingSides.add(Math.sign(p.getX(ids[0]!)));
      }
      for (const prior of saved.materials) {
        assert.equal(prior.material.side, prior.side);
        assert.equal(prior.material.depthTest, prior.depthTest);
        assert.equal(prior.material.depthWrite, prior.depthWrite);
        assert.equal(prior.material.transparent, prior.transparent);
        assert.equal(prior.material.toneMapped, prior.toneMapped);
        if (lens) {
          assert.equal(prior.material.onBeforeCompile, prior.hook);
          assert.equal(prior.material.customProgramCacheKey, prior.key);
          assert.equal(prior.material.version, prior.version, 'physical lenses are not patched or recompiled');
        }
      }
    }
    assert.ok(removedFrameVertices > 100 && retainedFrontVertices > 1000 && lensVertices > 1000);
    // The drawn arm reaches its own ear hook: nearly all of the asset's lateral stem survives the cut, and what is
    // left over is only the last few millimetres, where the end blend has geometry to dissolve into.
    assert.ok(armVertices > 1000 && drawnArmVertices / armVertices > 0.93,
      `almost the whole lateral stem is drawn (${drawnArmVertices}/${armVertices} arm vertices)`);
    // The hook is what makes the arm reach the ear: the drawn arm must curve down as far as the asset's own does.
    assert.ok(armLowestY < -.015, 'the asset carries a downward ear hook');
    assert.ok(drawnLowestY - armLowestY < .002,
      `and the drawn arm follows it down (drawn to ${drawnLowestY.toFixed(4)}, asset reaches ${armLowestY.toFixed(4)})`);
    assert.ok(configuration.negativeXCutoffLocalZM > armRearZ,
      "the cut stays inside the asset's own arm, so the terminal band is real geometry");
    assert.ok(configuration.negativeXCutoffLocalZM - armRearZ < 0.01,
      `and within a centimetre of its end (cut ${configuration.negativeXCutoffLocalZM}, arm ends ${armRearZ.toFixed(4)})`);
    assert.deepEqual(crossingSides, new Set([-1, 1]), 'both original stems cross the endpoint plane');
    const blend = createTempleBlendConfiguration(definition.templeClipLocalZM), camera = pairedCamera();
    t.after(() => camera.dispose());
    clip.set(blend); clip.prepareRender(camera, 960, 720);
    assert.equal(blend.fadeLengthLocalM, .015);
    assert.equal(blend.negativeXCutoffLocalZM, configuration.negativeXCutoffLocalZM);
    const blendSides = new Set<number>();
    for (const [meshIndex, mesh] of meshes.entries()) {
      const saved = before[meshIndex]!, p = mesh.geometry.getAttribute('position');
      assert.deepEqual(p.array, saved.positions);
      assert.deepEqual(mesh.geometry.getAttribute('normal').array, saved.normals);
      assert.equal(mesh.material, saved.material);
      const lens = mesh.material instanceof MeshPhysicalMaterial && mesh.material.transmission > 0;
      for (let i = 0; i < p.count; i++) {
        if (lens) assert.ok(p.getZ(i) > blend.negativeXCutoffLocalZM + blend.fadeLengthLocalM);
        else if (p.getZ(i) >= blend.negativeXCutoffLocalZM && p.getZ(i) < blend.negativeXCutoffLocalZM + blend.fadeLengthLocalM) {
          assert.ok(p.getZ(i) < -.03 && Math.abs(p.getX(i)) > .045,
            'the complete 15 mm dissolve stays on actual posterior lateral shaft vertices');
          blendSides.add(Math.sign(p.getX(i)));
        }
      }
      for (const prior of saved.materials) {
        assert.equal(prior.material.transparent, prior.transparent);
        assert.equal(prior.material.depthWrite, prior.depthWrite);
        assert.equal(prior.material.depthTest, prior.depthTest);
        assert.equal(prior.material.side, prior.side);
        if (lens) assert.equal(prior.material.version, prior.version);
        else assert.equal(prior.material.alphaToCoverage, false);
      }
    }
    assert.deepEqual(blendSides, new Set([-1, 1]));
    clip.set(null);
    clip.prepareRender();
    assert.equal(clip.configuration, null);
    clip.dispose();
    for (const saved of before) for (const prior of saved.materials) {
      assert.equal(prior.material.onBeforeCompile, prior.hook);
      assert.equal(prior.material.customProgramCacheKey, prior.key);
    }
  });
}

test('one shared material wrapper preserves prior hooks, dynamic cache keys and resource ownership', t => {
  const frame = new MeshStandardMaterial(), other = new MeshStandardMaterial();
  const lens = new MeshPhysicalMaterial({transmission: 1}), geometry = new BufferGeometry(), texture = new Texture();
  frame.map = texture;
  const root = new Group().add(new Mesh(geometry, [frame, lens]), new Mesh(geometry, frame), new Mesh(geometry, other));
  const backend = {identity: 'compile renderer'};
  let hookCalls = 0;
  frame.userData.variant = 'first';
  frame.onBeforeCompile = function(shader, renderer) {
    assert.equal(this, frame); assert.equal(renderer, backend); hookCalls++;
    shader.uniforms.priorHook = {value: this.userData.variant};
  };
  frame.customProgramCacheKey = function() { return 'prior-' + String(this.userData.variant); };
  const priorHook = frame.onBeforeCompile, priorKey = frame.customProgramCacheKey;
  const lensHook = lens.onBeforeCompile, lensKey = lens.customProgramCacheKey;
  const counts = new Map<Material | BufferGeometry | Texture, number>();
  for (const resource of [frame, other, lens, geometry, texture]) {
    counts.set(resource, 0); resource.addEventListener('dispose', () => counts.set(resource, counts.get(resource)! + 1));
  }
  t.after(() => { for (const resource of counts.keys()) resource.dispose(); });
  const version = frame.version, clip = createTempleClip(root);
  t.after(() => clip.dispose());
  assert.equal(frame.version, version + 1, 'two meshes sharing a material install one wrapper');
  assert.equal(lens.onBeforeCompile, lensHook); assert.equal(lens.customProgramCacheKey, lensKey);
  const shader = compile(frame, backend), otherShader = compile(other);
  assert.equal(hookCalls, 1, 'the original hook runs exactly once per compilation');
  assert.equal(shader.uniforms.priorHook!.value, 'first');
  assert.equal(shader.uniforms.templeClipEnabled, otherShader.uniforms.templeClipEnabled,
    'all patched materials observe the same presentation state');
  assert.equal(shader.uniforms.templeClipNegativeXCutoffZ, otherShader.uniforms.templeClipNegativeXCutoffZ);
  assert.equal(shader.uniforms.templeClipPositiveXCutoffZ, otherShader.uniforms.templeClipPositiveXCutoffZ);
  const firstKey = frame.customProgramCacheKey();
  frame.userData.variant = 'second';
  assert.notEqual(frame.customProgramCacheKey(), firstKey, 'changing prior shader behavior must change the composed program cache key');
  assert.equal(compile(frame, backend).uniforms.priorHook!.value, 'second');
  clip.set(clipping(-.075, -.085));
  assert.equal(shader.uniforms.templeClipEnabled!.value, 1);
  assert.equal(otherShader.uniforms.templeClipNegativeXCutoffZ!.value, -.075);
  assert.equal(otherShader.uniforms.templeClipPositiveXCutoffZ!.value, -.085);
  clip.dispose(); clip.dispose();
  assert.equal(shader.uniforms.templeClipEnabled!.value, 0, 'already compiled programs are disabled during disposal');
  assert.equal(frame.onBeforeCompile, priorHook); assert.equal(frame.customProgramCacheKey, priorKey);
  assert.equal(frame.customProgramCacheKey(), 'prior-second');
  assert.equal(clip.configuration, null);
  assert.throws(() => clip.set(null), /disposed/);
  assert.ok([...counts.values()].every(count => count === 0), 'the clip controller does not dispose renderer-owned shared resources');
  assert.equal(frame.map, texture); assert.equal(root.children[0]!.type, 'Mesh');
});

test('selection and reset never recompile or change material coverage, blending and depth flags', t => {
  const frame = new MeshStandardMaterial(), priorA2C = new MeshStandardMaterial({alphaToCoverage: true});
  const lens = new MeshPhysicalMaterial({transmission: 1}), geometry = new BufferGeometry(), camera = pairedCamera();
  const root = new Group().add(new Mesh(geometry, [frame, lens]), new Mesh(geometry, frame), new Mesh(geometry, priorA2C));
  const clip = createTempleClip(root);
  t.after(() => { clip.dispose(); frame.dispose(); priorA2C.dispose(); lens.dispose(); geometry.dispose(); camera.dispose(); });
  const shader = compile(frame), saved = {
    blending: frame.blending, transparent: frame.transparent, depthTest: frame.depthTest, depthWrite: frame.depthWrite,
    opacity: frame.opacity, alphaTest: frame.alphaTest, alphaHash: frame.alphaHash, side: frame.side, alphaToCoverage: frame.alphaToCoverage,
  };
  const installedVersion = frame.version, priorA2CVersion = priorA2C.version, lensVersion = lens.version;
  for (let frameIndex = 0; frameIndex < 5; frameIndex++) {
    clip.set(null);
    assert.equal(shader.uniforms.templeClipEnabled!.value, 0);
    assert.equal(shader.uniforms.templeFadeMode!.value, 0, 'early reset immediately clears compiled uniforms');
    clip.set(createTempleBlendConfiguration(-.11)); clip.prepareRender(camera, 960, 720);
    assert.equal(shader.uniforms.templeFadeMode!.value, 3);
  }
  clip.set(null); clip.prepareRender();
  assert.equal(frame.version, installedVersion, 'early null then final live state never recompiles');
  assert.equal(priorA2C.version, priorA2CVersion); assert.equal(lens.version, lensVersion);
  assert.equal(priorA2C.alphaToCoverage, true, 'a material that already uses A2C keeps it');
  for (const [key, value] of Object.entries(saved)) assert.equal(Reflect.get(frame, key), value);
  clip.set(createTempleBlendConfiguration(-.11)); clip.prepareRender(camera, 960, 720);
  clip.dispose();
  assert.equal(frame.alphaToCoverage, false);
  assert.equal(priorA2C.alphaToCoverage, true);
  assert.equal(shader.uniforms.templeFadeMode!.value, 0);
  assert.equal(shader.uniforms.templeClipEnabled!.value, 0);
  assert.throws(() => clip.prepareRender(), /disposed/);
});

test('different original hooks remain distinct under the default Three cache key', t => {
  const a = new MeshStandardMaterial(), b = new MeshStandardMaterial(), geometry = new BufferGeometry();
  a.onBeforeCompile = function firstHook(shader) { shader.uniforms.first = {value: 1}; };
  b.onBeforeCompile = function secondHook(shader) { shader.uniforms.second = {value: 2}; };
  const clip = createTempleClip(new Group().add(new Mesh(geometry, a), new Mesh(geometry, b)));
  t.after(() => { clip.dispose(); a.dispose(); b.dispose(); geometry.dispose(); });
  assert.notEqual(a.customProgramCacheKey(), b.customProgramCacheKey(), 'default cache identity still includes the original hook');
  assert.equal(compile(a).uniforms.first!.value, 1);
  assert.equal(compile(b).uniforms.second!.value, 2);
});

test('recorded clipping metadata is copied, validated atomically, disabled and restored without stale state', t => {
  const material = new MeshStandardMaterial(), geometry = new BufferGeometry();
  const clip = createTempleClip(new Group().add(new Mesh(geometry, material)));
  t.after(() => { clip.dispose(); material.dispose(); geometry.dispose(); });
  const shader = compile(material);
  assert.equal(shader.uniforms.templeClipEnabled!.value, 0);
  const input = {...clipping(-.09, -.075)};
  clip.set(input); input.negativeXCutoffLocalZM = -.02;
  assert.equal(clip.configuration!.negativeXCutoffLocalZM, -.09, 'caller mutation cannot change the recorded policy');
  const snapshot = clip.configuration as {method: string; negativeXCutoffLocalZM: number; positiveXCutoffLocalZM: number};
  snapshot.positiveXCutoffLocalZM = -.01; snapshot.method = 'different';
  assert.deepEqual(clip.configuration, clipping(-.09, -.075));
  for (const field of ['negativeXCutoffLocalZM', 'positiveXCutoffLocalZM'] as const) {
    for (const value of [-.200001, -.029999, 0, .01, NaN, Infinity, -Infinity]) {
      assert.throws(() => clip.set({...clipping(-.09, -.075), [field]: value}), /configuration is invalid/);
      assert.deepEqual(clip.configuration, clipping(-.09, -.075));
      assert.equal(shader.uniforms.templeClipEnabled!.value, 1);
      assert.equal(shader.uniforms.templeClipNegativeXCutoffZ!.value, -.09, 'invalid input cannot clip the optical front or replace valid state');
      assert.equal(shader.uniforms.templeClipPositiveXCutoffZ!.value, -.075);
    }
  }
  const unknownVersion = {...clipping(-.09), method: 'unrecognized-v2'} as unknown as TempleClipConfiguration;
  assert.throws(() => clip.set(unknownVersion), /configuration is invalid/);
  // The 15 mm band must end behind the protected front (-.03), so -.045 is the last valid cutoff.
  for (const cutoff of [-.2, -.045]) assert.doesNotThrow(() => validateTempleClip(clipping(cutoff)));
  clip.set(null); assert.equal(shader.uniforms.templeClipEnabled!.value, 0); assert.equal(clip.configuration, null);
  clip.set(clipping(-.08, -.065));
  assert.equal(shader.uniforms.templeClipEnabled!.value, 1);
  assert.equal(shader.uniforms.templeClipNegativeXCutoffZ!.value, -.08);
  assert.equal(shader.uniforms.templeClipPositiveXCutoffZ!.value, -.065);
  assert.deepEqual(clip.configuration, clipping(-.08, -.065));
});

test('camera dissolve borrows the paired sRGB source, composes after visibility, and preserves coverage alpha', t => {
  const frame = new MeshStandardMaterial(), geometry = new BufferGeometry();
  const clip = createTempleClip(new Group().add(new Mesh(geometry, frame)));
  const camera = pairedCamera(1600, 900), laterCamera = pairedCamera();
  let cameraDisposals = 0;
  camera.addEventListener('dispose', () => cameraDisposals++);
  t.after(() => { clip.dispose(); frame.dispose(); geometry.dispose(); camera.dispose(); laterCamera.dispose(); });
  const flags = {transparent: frame.transparent, blending: frame.blending, depthWrite: frame.depthWrite,
    depthTest: frame.depthTest, side: frame.side, premultipliedAlpha: frame.premultipliedAlpha, alphaToCoverage: frame.alphaToCoverage};
  const shader = compile(frame), blend = createTempleBlendConfiguration(-.105);
  clip.set(blend);
  assert.equal(shader.uniforms.templeFadeMode!.value, 3);
  assert.equal(shader.uniforms.templeFadeLength!.value, TEMPLE_BLEND_LENGTH_LOCAL_M);
  assert.equal(shader.uniforms.templeCameraSource!.value, null);
  assert.throws(() => clip.prepareRender(), /paired sRGB camera texture/);
  camera.matrixAutoUpdate = false;
  camera.matrix.set(1, 0, .02, 0, 1, .03, 0, 0, 1);
  clip.prepareRender(camera, 1280, 720);
  assert.equal(shader.uniforms.templeCameraSource!.value, camera);
  assert.deepEqual(shader.uniforms.templeCameraViewport!.value.toArray(), [1280, 720],
    'source dimensions and render dimensions are distinct');
  assert.deepEqual(shader.uniforms.templeCameraUvTransform!.value.elements, camera.matrix.elements);
  assert.notEqual(shader.uniforms.templeCameraUvTransform!.value, camera.matrix);
  const version = frame.version;
  for (const [key, value] of Object.entries(flags)) assert.equal(Reflect.get(frame, key), value,
    'dissolve does not expose texture alpha or change ordinary material blending/depth');
  const hook = frame.onBeforeCompile;
  const overlay = frame.clone();
  t.after(() => overlay.dispose());
  overlay.onBeforeCompile = function(source, renderer) {
    hook.call(this, source, renderer);
    source.fragmentShader = source.fragmentShader.replace('#include <dithering_fragment>',
      '#include <dithering_fragment>\n// visibility insertion\ngl_FragColor.a = 0.37;');
  };
  const composed = compile(overlay).fragmentShader;
  const footer = composed.slice(composed.indexOf('float templeBlendEndpointZ'));
  assert.ok(composed.indexOf('// visibility insertion') < composed.indexOf('float templeBlendEndpointZ'));
  assert.match(footer, /linearToOutputTexel\(texture2D\(templeCameraSource, templeCameraUV\)\)/,
    'camera RGB gets the actual output encoding without the synthetic-light tone mapper');
  assert.match(footer, /gl_FragColor\.rgb = mix\(templeCameraRGB, gl_FragColor\.rgb, templeBlendWeight\)/);
  assert.doesNotMatch(footer, /gl_FragColor\.a\s*=/, 'v3 preserves the overlay coverage alpha');
  assert.deepEqual(composed.match(/gl_FragColor\.a\s*=[^;]*;/g), ['gl_FragColor.a = 0.37;'],
    'the clip writes no alpha, so coverage stays with visibility A2C/dither');
  assert.match(footer, /templeOriginalXZ\.y < templeBlendEndpointZ \+ templeBlendFadeLength/,
    'fragments before the terminal band retain their exact lit RGB');
  clip.set(blend);
  assert.equal(shader.uniforms.templeCameraSource!.value, null, 'each new policy selection clears the previous paired source');
  clip.prepareRender(laterCamera, 960, 720);
  assert.equal(shader.uniforms.templeCameraSource!.value, laterCamera);
  clip.set(blend); clip.prepareRender(camera, 1280, 720);
  assert.equal(shader.uniforms.templeCameraSource!.value, camera, 'A/B/A source binding does not accumulate');
  assert.equal(frame.version, version, 'rebinding paired pixels does not recompile materials');
  clip.set(null); clip.prepareRender();
  assert.equal(shader.uniforms.templeCameraSource!.value, null);
  clip.dispose(); clip.dispose();
  assert.equal(cameraDisposals, 0, 'the background texture remains owned by its renderer');
  assert.equal(shader.uniforms.templeCameraSource!.value, null);
});

test('dissolve metadata and source validation are bounded', t => {
  const frame = new MeshStandardMaterial(), geometry = new BufferGeometry(), camera = pairedCamera();
  const clip = createTempleClip(new Group().add(new Mesh(geometry, frame)));
  t.after(() => { clip.dispose(); frame.dispose(); geometry.dispose(); camera.dispose(); });
  const shader = compile(frame), blend = {...createTempleBlendConfiguration(-.11), positiveXCutoffLocalZM: -.105};
  assert.deepEqual(createTempleBlendConfiguration(-.105), {method: TEMPLE_BLEND_METHOD,
    negativeXCutoffLocalZM: -.105, positiveXCutoffLocalZM: -.105, fadeLengthLocalM: .015});
  const input = {...blend};
  clip.set(input); clip.prepareRender(camera, 960, 720);
  input.fadeLengthLocalM = .018;
  Reflect.set(clip.configuration as TempleBlendConfiguration, 'negativeXCutoffLocalZM', -.09);
  assert.deepEqual(clip.configuration, blend);
  for (const value of [
    {...blend, fadeLengthLocalM: 0}, {...blend, fadeLengthLocalM: -.001},
    {...blend, fadeLengthLocalM: NaN}, {...blend, fadeLengthLocalM: Infinity},
    {...blend, fadeLengthLocalM: .020001}, {...blend, positiveXCutoffLocalZM: -.04},
  ]) {
    assert.throws(() => clip.set(value), /configuration is invalid/);
    assert.deepEqual(clip.configuration, blend);
    assert.equal(shader.uniforms.templeCameraSource!.value, camera);
  }
  const wrongColor = new CanvasTexture({width: 960, height: 720} as HTMLCanvasElement);
  t.after(() => wrongColor.dispose());
  const invalidSources: [CanvasTexture | undefined, number | undefined, number | undefined][] = [
    [undefined, 960, 720], [wrongColor, 960, 720], [camera, 0, 720], [camera, 960, NaN],
    [camera, 960.5, 720], [camera, undefined, 720],
  ];
  for (const args of invalidSources) {
    assert.throws(() => clip.prepareRender(...args), /paired sRGB camera texture/);
    assert.equal(shader.uniforms.templeCameraSource!.value, camera, 'failed preparation cannot replace the validated source');
    assert.deepEqual(shader.uniforms.templeCameraViewport!.value.toArray(), [960, 720]);
  }
});

test('each hair crossing owns its fade, including inherited overlays and legacy resets', t => {
  const material = new MeshStandardMaterial(), geometry = new BufferGeometry();
  const clip = createTempleClip(new Group().add(new Mesh(geometry, material)));
  const overlay = material.clone(); overlay.onBeforeCompile = material.onBeforeCompile;
  t.after(() => {clip.dispose(); material.dispose(); overlay.dispose(); geometry.dispose();});
  const shader = compile(material), inherited = compile(overlay);
  const policy = {...clipping(-.036, -.08), fadeLengthLocalM: .005,
    negativeXFadeLengthLocalM: .001, positiveXFadeLengthLocalM: .003};
  clip.set(policy);
  assert.deepEqual(shader.uniforms.templeSideFadeLengths!.value.toArray(), [.001, .003]);
  assert.equal(shader.uniforms.templeSideFadeLengths, inherited.uniforms.templeSideFadeLengths,
    'visibility overlays share live per-side fades rather than copying one arm to the other');
  for (const field of ['negativeXFadeLengthLocalM', 'positiveXFadeLengthLocalM'] as const) {
    for (const value of [0, -.001, NaN, Infinity, .020001]) {
      assert.throws(() => clip.set({...policy, [field]: value}), /configuration is invalid/);
      assert.deepEqual(clip.configuration, policy);
      assert.deepEqual(inherited.uniforms.templeSideFadeLengths!.value.toArray(), [.001, .003]);
    }
  }
  assert.throws(() => clip.set({...policy, negativeXFadeLengthLocalM: .007}), /configuration is invalid/,
    'a long side fade cannot cross the optical front even if the common fade fits');
  assert.doesNotThrow(() => clip.set({...policy, fadeLengthLocalM: .015}),
    'explicit short side fades determine the actual protected extent');
  clip.set(clipping(-.09));
  assert.deepEqual(inherited.uniforms.templeSideFadeLengths!.value.toArray(), [0, 0],
    'an older recorded policy restores the common fade');
  clip.set(policy); clip.set(null);
  assert.deepEqual(shader.uniforms.templeSideFadeLengths!.value.toArray(), [0, 0]);
});
