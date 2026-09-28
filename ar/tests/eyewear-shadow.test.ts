import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {
  Box3, BoxGeometry, BufferAttribute, BufferGeometry, CanvasTexture, Color, DataTexture, DoubleSide, FloatType, FrontSide, Group, Matrix3, Matrix4, Mesh, MeshPhysicalMaterial,
  MeshStandardMaterial, PerspectiveCamera, Scene, SRGBColorSpace, Texture, Vector2, Vector3, Vector4,
  WebGLRenderTarget,
} from 'three';
import type {Camera, Material, OrthographicCamera, ShaderMaterial, WebGLRenderer} from 'three';
import {EyewearShadow, DEFAULT_SHADOW_SETTINGS, lensShadowTransmission, MAX_CANONICAL_SHADOW_LAYERS,
  normalizeShadowSettings} from '../src/render/eyewear-shadow.ts';
import {OpticalLayerOverflowError, OpticalOverflowChecker} from '../src/render/layer-overflow.ts';
import {createTempleBlendConfiguration} from '../src/render/temple-clip.ts';
import {createLensAppearanceUniforms, evaluateLensAppearance, LENS_APPEARANCE_EXTENSION, LENS_INCIDENCE_GLSL, LENS_RESPONSE_GLSL,
  MAX_LENS_DENSITY_KNOTS} from '../src/eyewear/lens-appearance.ts';
import type {LensAppearanceDescriptor} from '../src/eyewear/lens-appearance.ts';
import {classifyAssetMaterials, EFFECTIVE_OPTICAL_GROUP_PROFILE} from '../src/eyewear/optical-material.ts';
import {applyVolumeAttenuationScale, volumeAttenuationRgb} from '../src/render/eyewear-volume.ts';

function canonicalAppearance(overrides: Partial<LensAppearanceDescriptor> = {}): LensAppearanceDescriptor {
  return {schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [.65, .4, .2], refractive_index: 1.5, roughness: .15,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [.1, .2, .3]}, {v: 1, optical_density_rgb: [1.1, .8, .5]}],
    angular_reflectance_keyframes: null, ...overrides};
}

function attachAppearance(material: Material, appearance = canonicalAppearance()): void {
  material.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance}};
}

function backendFixture() {
  const initialTarget = new WebGLRenderTarget(16, 12);
  const state = {
    target: initialTarget as WebGLRenderTarget | null, face: 2, mip: 1,
    viewport: new Vector4(7, 8, 120, 80), scissor: new Vector4(2, 3, 40, 30), scissorTest: true,
    color: new Color(0x123456), alpha: .6,
    draws: [] as {scene: Scene; camera: Camera; target: WebGLRenderTarget | null; meshes: Mesh[];
      visibleMaterials: Material[]; peelIndex: number | null; previousColor: Texture | null;
      inputTexture: Texture | null; inputSize: number[] | null; invertAlpha: number | null;
      groupCapture: number | null; groupNearest: Texture | null; matrices: number[][]}[],
    readbacks: [] as {target: WebGLRenderTarget; width: number; height: number}[],
    clearCount: 0, failOnDraw: 0, overflowFlag: 0, floatColorSupported: true, skipReadback: false, depthClear: .7,
  };
  const backend = {
    capabilities: {samples: 4}, extensions: {has: () => state.floatColorSupported}, autoClear: false, xr: {enabled: true},
    getContext: () => ({DEPTH_CLEAR_VALUE: 0x0B73, getParameter: () => state.depthClear}),
    state: {buffers: {depth: {setClear: (value: number) => {state.depthClear = value;}}}},
    getDrawingBufferSize: (out: Vector2) => out.set(640, 360),
    getRenderTarget: () => state.target,
    getActiveCubeFace: () => state.face,
    getActiveMipmapLevel: () => state.mip,
    setRenderTarget: (value: WebGLRenderTarget | null, face = 0, mip = 0) => {
      state.target = value; state.face = face; state.mip = mip;
    },
    getViewport: (out: Vector4) => out.copy(state.viewport),
    setViewport: (x: number | Vector4, y?: number, z?: number, w?: number) => {
      if (x instanceof Vector4) state.viewport.copy(x); else state.viewport.set(x, y!, z!, w!);
    },
    getScissor: (out: Vector4) => out.copy(state.scissor),
    setScissor: (x: number | Vector4, y?: number, z?: number, w?: number) => {
      if (x instanceof Vector4) state.scissor.copy(x); else state.scissor.set(x, y!, z!, w!);
    },
    getScissorTest: () => state.scissorTest,
    setScissorTest: (value: boolean) => {state.scissorTest = value;},
    getClearColor: (out: Color) => out.copy(state.color),
    getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha = 1) => {state.color.set(value); state.alpha = alpha;},
    clear: () => {state.clearCount++;},
    render: (scene: Scene, camera: Camera) => {
      scene.updateMatrixWorld(); camera.updateMatrixWorld();
      const meshes: Mesh[] = [];
      scene.traverseVisible(object => {if (object instanceof Mesh) meshes.push(object);});
      const visibleMaterials = meshes.flatMap(materials).filter(material => material.visible);
      const canonical = visibleMaterials.find(material => material.userData.lensAppearanceSchema === 1) as ShaderMaterial | undefined;
      const reducer = visibleMaterials.find(material => material.name === 'Optical layer overflow flag reduction') as ShaderMaterial | undefined;
      state.draws.push({scene, camera, target: state.target, meshes, visibleMaterials,
        peelIndex: canonical?.uniforms.shadowPeelIndex!.value ?? null,
        previousColor: canonical?.uniforms.shadowPeelPreviousColor!.value ?? null,
        inputTexture: reducer?.uniforms.inputImage!.value ?? null,
        inputSize: reducer?.uniforms.inputSize!.value.toArray() ?? null,
        invertAlpha: reducer?.uniforms.invertAlpha!.value ?? null,
        groupCapture: canonical?.uniforms.shadowGroupCapture?.value ?? null,
        groupNearest: canonical?.uniforms.shadowGroupNearest?.value ?? null,
        matrices: meshes.map(mesh => mesh.matrixWorld.toArray())});
      if (state.failOnDraw === state.draws.length) throw new Error('Injected shadow draw failure');
    },
    readRenderTargetPixels: (target: WebGLRenderTarget, _x: number, _y: number, width: number, height: number, out: Uint8Array) => {
      state.readbacks.push({target, width, height});
      if (!state.skipReadback) {out.fill(0); out[3] = state.overflowFlag;}
    },
  };
  const snapshot = () => ({target: state.target, face: state.face, mip: state.mip,
    viewport: state.viewport.toArray(), scissor: state.scissor.toArray(), scissorTest: state.scissorTest,
    color: state.color.getHex(), alpha: state.alpha, autoClear: backend.autoClear, xr: backend.xr.enabled, depthClear: state.depthClear});
  return {state, backend, renderer: backend as unknown as WebGLRenderer, snapshot,
    dispose: () => initialTarget.dispose()};
}

function triangleGeometry() {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(new Float32Array([
    -.06, 0, 0, -.04, 0, 0, -.05, .01, 0,
    .01, 0, 0, .03, 0, 0, .02, .01, 0,
  ]), 3));
  geometry.setAttribute('uv', new BufferAttribute(new Float32Array([0, 0, 1, 0, .5, 1, 0, 0, 1, 0, .5, 1]), 2));
  geometry.setAttribute('uv1', new BufferAttribute(new Float32Array([.1, .2, .9, .2, .5, .8, .1, .2, .9, .2, .5, .8]), 2));
  geometry.setIndex([0, 1, 2, 3, 4, 5]);
  geometry.addGroup(0, 3, 0); geometry.addGroup(3, 3, 1);
  geometry.computeVertexNormals();
  return geometry;
}

function sceneFixture(configureMaterials?: (frame: MeshStandardMaterial, lens: MeshPhysicalMaterial, texture: Texture) => void) {
  const fake = backendFixture(), geometry = triangleGeometry(), face = triangleGeometry();
  const texture = new Texture(); texture.channel = 1; texture.colorSpace = SRGBColorSpace;
  texture.offset.set(.1, .2); texture.repeat.set(.7, .8); texture.updateMatrix();
  const frame = new MeshStandardMaterial({color: 0x402010, map: texture});
  const lens = new MeshPhysicalMaterial({color: 0xffffff, transmission: 1, roughness: .15});
  configureMaterials?.(frame, lens, texture);
  const mixed = new Mesh(geometry, [frame, lens]); mixed.name = 'Mixed frame and lens';
  const root = new Group().add(mixed); root.position.set(2, 3, -50); root.scale.setScalar(100);
  const camera = new PerspectiveCamera(63, 16 / 9, 1, 10_000); camera.updateMatrixWorld();
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  source.colorSpace = SRGBColorSpace;
  const controller = new EyewearShadow(fake.renderer, root, face);
  const render = (settings = DEFAULT_SHADOW_SETTINGS) => controller.render(camera, source, 640, 360, settings);
  const sourceSnapshot = () => ({positions: Array.from(geometry.getAttribute('position').array),
    indices: Array.from(geometry.index!.array), groups: structuredClone(geometry.groups), parent: mixed.parent,
    children: root.children.slice(), material: mixed.material, frameColor: frame.color.getHex(),
    lensColor: lens.color.getHex(), transmission: lens.transmission, frameMap: frame.map,
    mapMatrix: texture.matrix.toArray(), mapChannel: texture.channel,
    sourceImage: source.image, sourceColorSpace: source.colorSpace, sourceVersion: source.version});
  const dispose = () => {
    controller.dispose(); geometry.dispose(); face.dispose(); frame.dispose(); lens.dispose();
    texture.dispose(); source.dispose(); fake.dispose();
  };
  return {fake, geometry, face, texture, frame, lens, mixed, root, camera, source, controller, render, sourceSnapshot, dispose};
}

function materials(mesh: Mesh): Material[] {return Array.isArray(mesh.material) ? mesh.material : [mesh.material];}

function casterMeshes(f: ReturnType<typeof sceneFixture>): Mesh[] {
  return [...new Set(f.fake.state.draws.flatMap(draw => draw.meshes))]
    .filter(mesh => materials(mesh).some(material => material.userData.kind === 'frame' || material.userData.kind === 'lens'));
}

/** Use eyewear-sized depth as well as width, so an orientation-dependent bounding box cannot hide behind the
 * minimum shadow extent. Rendering still uses the ordinary mixed frame/lens fixture and public controller API. */
function deepFrameFixture() {
  const f = sceneFixture();
  f.geometry.scale(1.5, 5, 1);
  const positions = f.geometry.getAttribute('position');
  for (let i = 0; i < positions.count; i++) positions.setZ(i, i % 2 ? -.14 : 0);
  positions.needsUpdate = true; f.geometry.computeBoundingBox();
  return f;
}

/** Closed members deliberately share one source material across DIFFERENT
 * groups. Group identity must come from metadata, never material equality. */
function effectiveFixture(ids: readonly string[] = ['left', 'left', 'right']) {
  const fake = backendFixture(), geometry = new BoxGeometry(.06, .04, .012), face = triangleGeometry();
  const p = geometry.getAttribute('position'), uv = geometry.getAttribute('uv');
  for (let i = 0; i < p.count; i++) uv.setY(i, (p.getY(i) + .02) / .04);
  const material = new MeshPhysicalMaterial({transmission: 0}); attachAppearance(material);
  const parts = ids.map((id, i) => {
    const mesh = new Mesh(geometry, material); mesh.name = `Closed member ${i}`;
    mesh.position.z = -.02 * i;
    mesh.userData.lensSurfaceProfile = EFFECTIVE_OPTICAL_GROUP_PROFILE;
    mesh.userData.opticalGroupId = id; return mesh;
  });
  const root = new Group().add(...parts); root.position.set(2, 3, -50); root.scale.setScalar(100);
  const camera = new PerspectiveCamera(63, 16 / 9, 1, 10_000); camera.updateMatrixWorld();
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  const controller = new EyewearShadow(fake.renderer, root, face);
  const render = () => controller.render(camera, source, 640, 360, DEFAULT_SHADOW_SETTINGS);
  const dispose = () => {controller.dispose(); geometry.dispose(); face.dispose(); material.dispose(); source.dispose(); fake.dispose();};
  return {fake, geometry, face, material, parts, root, camera, source, controller, render, dispose};
}

function shadowView(f: ReturnType<typeof sceneFixture>) {
  const draw = [...f.fake.state.draws].reverse().find(entry => entry.meshes.some(mesh => materials(mesh)
    .some(material => material.userData.kind === 'frame' || material.userData.kind === 'lens')));
  assert.ok(draw?.target, 'an enabled visible frame produces a shadow map');
  return {camera: draw.camera.clone() as OrthographicCamera, width: draw.target.width, height: draw.target.height};
}

function shadowPixel(point: Vector3, view: ReturnType<typeof shadowView>): Vector2 {
  const projected = point.clone().project(view.camera);
  return new Vector2((projected.x + 1) * view.width / 2, (projected.y + 1) * view.height / 2);
}

test('shadow controls have bounded independent defaults and tolerate malformed saved settings', () => {
  assert.deepEqual(normalizeShadowSettings(), {enabled: true, frameStrength: .16, lensStrength: .18, softness: 1.9});
  assert.deepEqual(normalizeShadowSettings({enabled: false, lensStrength: .4}),
    {enabled: false, frameStrength: .16, lensStrength: .4, softness: 1.9});
  assert.deepEqual(normalizeShadowSettings({frameStrength: -1, lensStrength: 5, softness: 8}),
    {enabled: true, frameStrength: 0, lensStrength: 1, softness: 3});
  assert.deepEqual(normalizeShadowSettings({enabled: 'false' as unknown as boolean,
    frameStrength: NaN, lensStrength: Infinity, softness: -Infinity}), DEFAULT_SHADOW_SETTINGS);
  const custom = normalizeShadowSettings({frameStrength: .7}); custom.frameStrength = 0;
  assert.equal(DEFAULT_SHADOW_SETTINGS.frameStrength, .16, 'normalizing returns independently owned settings');
});

test('clear lenses transmit almost all light while tint, absorption and opacity attenuate it', t => {
  const clear = new MeshPhysicalMaterial({color: 0xffffff, transmission: 1, ior: 1.5});
  const tinted = new MeshPhysicalMaterial({color: new Color().setRGB(.8, .4, .1), transmission: 1, ior: 1.5});
  const absorbing = new MeshPhysicalMaterial({color: 0xffffff, transmission: 1, ior: 1.5,
    attenuationColor: new Color().setRGB(.8, .5, .2), attenuationDistance: 1, thickness: 1});
  t.after(() => {clear.dispose(); tinted.dispose(); absorbing.dispose();});
  const neutral = lensShadowTransmission(clear), colored = lensShadowTransmission(tinted);
  assert.ok(neutral.every(channel => channel > .9 && channel <= 1), 'clear lenses have only small neutral reflection loss');
  assert.ok(Math.max(...neutral) - Math.min(...neutral) < 1e-12);
  assert.ok(colored[0] > colored[1] && colored[1] > colored[2], 'warm tint preserves its transmitted color ordering');
  assert.ok(colored.every((channel, i) => channel < neutral[i]!));
  const thin = lensShadowTransmission(absorbing); absorbing.thickness = 2;
  const thick = lensShadowTransmission(absorbing);
  assert.ok(thick.every((channel, i) => channel < thin[i]!), 'more material cannot transmit more light');
  absorbing.thickness = 0;
  assert.deepEqual(lensShadowTransmission(absorbing), neutral, 'zero thickness has no bulk absorption');
  clear.transmission = 0;
  assert.deepEqual(lensShadowTransmission(clear), [0, 0, 0], 'an opaque surface cannot act as a clear lens');
});

test('the shadow absorbs through the one shared volume term, never an inline copy of it', async t => {
  const materials = [
    new MeshPhysicalMaterial({transmission: 1, ior: 1.5}),
    new MeshPhysicalMaterial({transmission: .9, ior: 1.49, thickness: .004, attenuationDistance: .005,
      attenuationColor: new Color().setRGB(.95, .9, .8), color: new Color().setRGB(.9, .85, .7)}),
    new MeshPhysicalMaterial({transmission: 1, thickness: 2, attenuationDistance: -1, attenuationColor: new Color().setRGB(.2, .3, .4)}),
    new MeshPhysicalMaterial({transmission: 1, thickness: NaN, attenuationDistance: .5, attenuationColor: new Color().setRGB(.2, .3, .4)}),
    new MeshPhysicalMaterial({transmission: 1, thickness: .01, attenuationDistance: .005, attenuationColor: new Color(NaN, 1.4, -.2)}),
  ];
  const scaled = new MeshPhysicalMaterial({transmission: 1, thickness: .01, attenuationDistance: .005,
    attenuationColor: new Color().setRGB(.8, .5, .2)});
  applyVolumeAttenuationScale(new Group().add(new Mesh(new BoxGeometry(), scaled)), 100);
  materials.push(scaled);
  t.after(() => {for (const material of materials) material.dispose();});
  for (const material of materials) {
    const ior = Number.isFinite(material.ior) ? Math.max(1, material.ior) : 1.5;
    const transmitted = Math.min(1, Math.max(0, material.transmission)) * (1 - ((ior - 1) / (ior + 1)) ** 2) ** 2;
    const volume = volumeAttenuationRgb(material);
    assert.deepEqual(lensShadowTransmission(material),
      (['r', 'g', 'b'] as const).map((channel, i) => Math.min(1, Math.max(0, material.color[channel])) * transmitted * volume[i]!),
      'tint x Fresnel x the Beer-Lambert term the pass-B twin uses');
  }
  const source = await readFile(new URL('../src/render/eyewear-shadow.ts', import.meta.url), 'utf8');
  const start = source.indexOf('export function lensShadowTransmission(');
  const body = source.slice(start, source.indexOf('\n}\n', start));
  assert.ok(start >= 0 && body.includes('volumeAttenuationRgb(material)'), 'the shadow calls the shared term');
  assert.doesNotMatch(body, /attenuationColor|authoredAttenuationDistance|thickness/,
    'no second Beer-Lambert implementation can drift from the twin\'s');
});

test('shadow drawing restores the caller renderer state and does not mutate source resources', t => {
  const f = sceneFixture(); t.after(f.dispose);
  const before = f.fake.snapshot(), sources = f.sourceSnapshot();
  const output = f.render();
  assert.ok(output instanceof Texture); assert.notEqual(output, f.source);
  assert.ok(f.fake.state.draws.length > 0, 'enabled shadows are composed on the GPU');
  assert.deepEqual(f.fake.snapshot(), before);
  assert.deepEqual(f.sourceSnapshot(), sources);
  assert.equal(f.render(), output, 'same-sized frames reuse the composed output');
  assert.deepEqual(f.sourceSnapshot(), sources);
});

test('mixed-material groups keep lens triangles out of opaque frame shadows', t => {
  const f = sceneFixture(); t.after(f.dispose); f.render();
  const casters = casterMeshes(f);
  assert.ok(casters.length > 0);
  const found = new Set<string>();
  for (const caster of casters) {
    const geometry = caster.geometry, position = geometry.getAttribute('position'), index = geometry.index;
    const groups = Array.isArray(caster.material) ? geometry.groups
      : [{start: 0, count: index?.count ?? position.count, materialIndex: 0}];
    for (const group of groups) {
      const material = materials(caster)[group.materialIndex ?? 0];
      if (!material?.visible || !['frame', 'lens'].includes(material.userData.kind as string)) continue;
      const start = Math.max(group.start, geometry.drawRange.start);
      const end = Math.min(group.start + group.count, geometry.drawRange.start + geometry.drawRange.count);
      for (let offset = start; offset + 2 < end; offset += 3) {
        const xs = [0, 1, 2].map(corner => position.getX(index ? index.getX(offset + corner) : offset + corner));
        const expected = xs.every(x => x < 0) ? 'frame' : 'lens';
        assert.equal(material.userData.kind, expected,
          'a transmissive material group must never also receive an opaque frame caster');
        found.add(expected);
      }
    }
  }
  assert.deepEqual([...found].sort(), ['frame', 'lens']);
});

test('mapped lens casters retain UV1 geometry, each texture transform and the material transmission', t => {
  const transmission = new Texture(), alpha = new Texture();
  transmission.offset.set(.3, .1); transmission.repeat.set(.5, .6); transmission.updateMatrix();
  alpha.offset.set(.2, .4); alpha.repeat.set(.9, .7); alpha.updateMatrix();
  const f = sceneFixture((_frame, lens, texture) => {
    lens.color.setRGB(.8, .5, .2); lens.transmission = .75;
    lens.map = texture; lens.transmissionMap = transmission; lens.alphaMap = alpha; lens.alphaTest = .1;
  });
  t.after(() => {f.dispose(); transmission.dispose(); alpha.dispose();});
  f.render();
  const mesh = casterMeshes(f).find(caster => materials(caster).some(material => material.userData.kind === 'lens'))!;
  const material = materials(mesh).find(value => value.userData.kind === 'lens') as ShaderMaterial;
  assert.equal(mesh.geometry.getAttribute('uv1'), f.geometry.getAttribute('uv1'), 'the non-default texture channel remains available');
  assert.equal(material.uniforms.colorMap!.value, f.texture);
  assert.equal((material.uniforms.colorMap!.value as Texture).channel, 1);
  assert.equal(material.uniforms.transmissionMap!.value, transmission);
  assert.equal(material.uniforms.alphaMap!.value, alpha);
  for (const [uniform, texture] of [['colorUv', f.texture], ['transmissionUv', transmission], ['alphaUv', alpha]] as const) {
    assert.deepEqual((material.uniforms[uniform]!.value as Matrix3).toArray(), texture.matrix.toArray());
    assert.notEqual(material.uniforms[uniform]!.value, texture.matrix, 'caster transforms do not own or change source matrices');
  }
  assert.deepEqual(material.uniforms.baseTransmission!.value.toArray(), lensShadowTransmission(f.lens));
  assert.equal(material.uniforms.alphaTest!.value, .1);
});

test('canonical shadow casters use the exact response and intrinsic UV without fallback optical multipliers', t => {
  const appearance = canonicalAppearance();
  const f = sceneFixture((_frame, lens, texture) => {
    attachAppearance(lens, appearance);
    // These generic-viewer fallback values must never recolor canonical T.
    lens.color.setRGB(.01, .02, .03); lens.transmission = .2; lens.ior = 2.1;
    lens.thickness = 7; lens.attenuationDistance = .1; lens.attenuationColor.setRGB(.1, .1, .1);
    lens.map = texture; lens.transmissionMap = texture; lens.alphaMap = texture; lens.opacity = .2;
  });
  t.after(f.dispose);
  const original = f.sourceSnapshot(), descriptor = structuredClone(f.lens.userData);
  f.render();
  const caster = casterMeshes(f).flatMap(materials)
    .find(material => material.userData.lensAppearanceSchema === 1) as ShaderMaterial;
  assert.ok(caster);
  const expectedUniforms = createLensAppearanceUniforms(appearance);
  for (const [name, uniform] of Object.entries(expectedUniforms)) assert.deepEqual(caster.uniforms[name], uniform);
  assert.ok(caster.fragmentShader.includes(LENS_RESPONSE_GLSL));
  assert.ok(caster.vertexShader.includes('vLensIntrinsicV = uv.y;'));
  assert.ok(caster.fragmentShader.includes('vec4(response.transmission, gl_FragCoord.z)'));
  assert.equal(caster.side, FrontSide);
  assert.equal(caster.depthWrite, true); assert.equal(caster.transparent, false); assert.equal(caster.toneMapped, false);
  for (const name of ['baseTransmission', 'colorMap', 'transmissionMap', 'alphaMap', 'opacity', 'clipEnabled']) {
    assert.equal(caster.uniforms[name], undefined, `${name} is a legacy input, not a canonical optical factor`);
  }
  assert.deepEqual(f.sourceSnapshot(), original); assert.deepEqual(f.lens.userData, descriptor);
  const legacyFrame = casterMeshes(f).flatMap(materials).find(material => material.userData.kind === 'frame') as ShaderMaterial;
  assert.equal(legacyFrame.uniforms.colorMap!.value, f.texture, 'the opaque neighboring material retains its legacy texture');
});

test('total-mirror canonical lens retains the lens label when fallback transmission is zero', t => {
  const appearance = canonicalAppearance({normal_reflectance_rgb: [1, 1, 1]});
  const f = sceneFixture((_frame, lens) => {attachAppearance(lens, appearance); lens.transmission = 0;});
  t.after(f.dispose); f.render();
  const caster = casterMeshes(f).flatMap(materials).find(material => material.userData.lensAppearanceSchema === 1) as ShaderMaterial;
  assert.equal(caster.userData.kind, 'lens');
  assert.deepEqual(Array.from(caster.uniforms.uLensNormalReflectance!.value as Float32Array), [1, 1, 1]);
  for (const v of [0, .5, 1]) assert.deepEqual(evaluateLensAppearance(appearance, v, 50).transmission_rgb, [0, 0, 0]);
  let casterDisposals = 0, sourceDisposals = 0;
  caster.addEventListener('dispose', () => {casterDisposals++;});
  f.lens.addEventListener('dispose', () => {sourceDisposals++;});
  f.controller.dispose(); f.controller.dispose();
  assert.equal(casterDisposals, 1); assert.equal(sourceDisposals, 0);
});

test('canonical incidence uses the posed normal in the shadow light frame, independently of the viewing camera', t => {
  const f = sceneFixture((_frame, lens) => attachAppearance(lens)); t.after(f.dispose);
  f.root.rotation.set(.2, .35, -.12); f.root.scale.set(90, 105, 100);
  f.render();
  const sourceNormal = new Vector3(0, 0, 1);
  const lightAngle = () => {
    const light = shadowView(f).camera;
    const modelView = new Matrix4().multiplyMatrices(light.matrixWorldInverse, f.mixed.matrixWorld);
    const inLightFrame = sourceNormal.clone().applyNormalMatrix(new Matrix3().getNormalMatrix(modelView));
    const worldNormal = sourceNormal.clone().applyNormalMatrix(new Matrix3().getNormalMatrix(f.mixed.matrixWorld));
    const worldLight = new Vector3().setFromMatrixColumn(light.matrixWorld, 2).normalize();
    assert.ok(Math.abs(inLightFrame.z - worldNormal.dot(worldLight)) < 1e-12);
    return Math.acos(Math.min(1, Math.max(0, inLightFrame.z))) * 180 / Math.PI;
  };
  const before = lightAngle();
  const viewNormal = sourceNormal.clone().applyNormalMatrix(new Matrix3().getNormalMatrix(
    new Matrix4().multiplyMatrices(f.camera.matrixWorldInverse, f.mixed.matrixWorld)));
  const viewerAngle = Math.acos(viewNormal.z) * 180 / Math.PI;
  assert.ok(Math.abs(before - viewerAngle) > 5, 'fixture distinguishes light incidence from view incidence');
  const lightT = evaluateLensAppearance(canonicalAppearance(), .4, before).transmission_rgb;
  const viewT = evaluateLensAppearance(canonicalAppearance(), .4, viewerAngle).transmission_rgb;
  assert.ok(lightT.some((value, i) => Math.abs(value - viewT[i]!) > .001));
  f.camera.position.set(8, -4, 3); f.camera.lookAt(0, 0, -50); f.camera.updateMatrixWorld();
  f.render(); assert.ok(Math.abs(lightAngle() - before) < 1e-12, 'moving only the observer cannot change lens shadow transmission');
  f.root.rotation.y += .2; f.render();
  assert.ok(Math.abs(lightAngle() - before) > 1, 'head rotation changes incidence to the fixed shadow light');
  const caster = casterMeshes(f).flatMap(materials).find(material => material.userData.lensAppearanceSchema === 1) as ShaderMaterial;
  assert.ok(caster.vertexShader.includes('normalMatrix * normal'));
  assert.ok(caster.fragmentShader.includes('normalize(vLensLightNormal).z'));
  assert.equal(caster.userData.incidenceFrame, 'shadow_light_orthographic_view');
});

test('canonical shadow normal interpolation is independent of authored normal magnitudes', t => {
  const f = sceneFixture((_frame, lens) => attachAppearance(lens)); t.after(f.dispose); f.render();
  const caster = casterMeshes(f).flatMap(materials).find(material => material.userData.lensAppearanceSchema === 1) as ShaderMaterial;
  // Three's visible normal_vertex normalizes each transformed vertex normal.
  // Normalizing only in the fragment weights interpolation by source magnitude.
  assert.ok(caster.vertexShader.includes('vLensLightNormal = normalize(normalMatrix * normal);'));
  assert.ok(caster.fragmentShader.includes('normalize(vLensLightNormal)'));
  const transform = new Matrix3().getNormalMatrix(new Matrix4().multiplyMatrices(
    shadowView(f).camera.matrixWorldInverse, f.mixed.matrixWorld));
  const unitDirections = [new Vector3(1, 0, 1).normalize(), new Vector3(0, 0, 1), new Vector3(0, 1, 1).normalize()];
  const authored = unitDirections.map((normal, i) => normal.clone().multiplyScalar([1, 100, .2][i]!));
  const expected = unitDirections.reduce((sum, normal) => sum.add(normal.clone().applyNormalMatrix(transform)), new Vector3()).normalize();
  const actual = authored.reduce((sum, normal) => sum.add(normal.clone().applyNormalMatrix(transform)), new Vector3()).normalize();
  assert.ok(actual.distanceTo(expected) < 1e-12);
  const fragmentOnly = authored.reduce((sum, normal) => sum.add(normal.clone().applyMatrix3(transform)), new Vector3()).normalize();
  assert.ok(fragmentOnly.distanceTo(expected) > .2, 'the unequal-magnitude fixture detects fragment-only normalization');
});

test('canonical shadow inputs reject missing coordinates, invalid normals, malformed schema and excessive GPU knots', t => {
  const f = sceneFixture(); t.after(f.dispose); f.controller.dispose(); attachAppearance(f.lens);
  const uv = f.geometry.getAttribute('uv'), normal = f.geometry.getAttribute('normal');
  const construct = () => new EyewearShadow(f.fake.renderer, f.root, f.face);
  f.geometry.deleteAttribute('uv'); assert.throws(construct, /TEXCOORD_0/); f.geometry.setAttribute('uv', uv);
  f.geometry.deleteAttribute('normal'); assert.throws(construct, /surface normals/); f.geometry.setAttribute('normal', normal);
  const savedY = uv.getY(3); uv.setY(3, 1.2); assert.throws(construct, /intrinsic UV/); uv.setY(3, savedY);
  const savedNormal = new Vector3().fromBufferAttribute(normal, 3);
  normal.setXYZ(3, 0, 0, 0); assert.throws(construct, /nonzero finite normals/); normal.setXYZ(3, ...savedNormal.toArray());
  f.lens.userData.gltfExtensions[LENS_APPEARANCE_EXTENSION].texcoord = 1;
  assert.throws(construct, /TEXCOORD_0/);
  attachAppearance(f.lens, canonicalAppearance({optical_density_keyframes: Array.from({length: MAX_LENS_DENSITY_KNOTS + 1},
    (_, i) => ({v: i / MAX_LENS_DENSITY_KNOTS, optical_density_rgb: [.1, .1, .1] as const}))}));
  assert.throws(construct, /at most/);
});

test('visible shaft endpoints and asymmetric fades are shared by every caster without altering geometry', t => {
  const f = sceneFixture(); t.after(f.dispose);
  const configuration = {...createTempleBlendConfiguration(-.12), negativeXCutoffLocalZM: -.08,
    negativeXFadeLengthLocalM: .002, positiveXFadeLengthLocalM: .005};
  const expected = structuredClone(configuration), sources = f.sourceSnapshot();
  f.controller.setTempleClip(configuration); f.render();
  const shaders = casterMeshes(f).flatMap(materials) as ShaderMaterial[];
  assert.ok(shaders.length >= 2);
  for (const shader of shaders) {
    assert.equal(shader.uniforms.clipEnabled!.value, 1);
    assert.deepEqual(shader.uniforms.clipCutoffs!.value.toArray(), [-.08, -.12]);
    assert.deepEqual(shader.uniforms.clipFades!.value.toArray(), [.002, .005]);
  }
  assert.throws(() => f.controller.setTempleClip({...configuration, negativeXCutoffLocalZM: 1}));
  assert.deepEqual(shaders[0]!.uniforms.clipCutoffs!.value.toArray(), [-.08, -.12], 'invalid clipping cannot partly replace the active endpoints');
  f.controller.setTempleClip(null);
  for (const shader of shaders) assert.equal(shader.uniforms.clipEnabled!.value, 0);
  assert.deepEqual(configuration, expected); assert.deepEqual(f.sourceSnapshot(), sources);
});

test('render-only temple overlays and hidden source branches cannot become duplicate shadow casters', t => {
  const f = sceneFixture(); t.after(f.dispose); f.controller.dispose();
  const overlay = new Mesh(f.geometry, f.frame); overlay.userData.templeVisibilityOverlay = true;
  const hidden = new Group().add(new Mesh(f.geometry, f.frame)); hidden.visible = false;
  f.root.add(overlay, hidden);
  const controller = new EyewearShadow(f.fake.renderer, f.root, f.face); t.after(() => controller.dispose());
  controller.render(f.camera, f.source, 640, 360, DEFAULT_SHADOW_SETTINGS);
  const casters = casterMeshes(f);
  assert.equal(casters.length, 1, 'only the original visible mixed frame is drawn');
  assert.equal(casters[0]!.geometry, f.geometry, 'caster borrows live geometry, including subsequent deformations');
  assert.deepEqual(casters[0]!.matrixWorld.toArray(), f.mixed.matrixWorld.toArray());
  f.root.position.x += 4;
  controller.render(f.camera, f.source, 640, 360, DEFAULT_SHADOW_SETTINGS);
  assert.deepEqual(casters[0]!.matrixWorld.toArray(), f.mixed.matrixWorld.toArray(), 'the shadow follows this frame\'s attachment');
});

test('yaw and nod retain shadow texel density while the full three-dimensional frame stays inside coverage', t => {
  const f = deepFrameFixture(); t.after(f.dispose); f.render();
  const first = shadowView(f), probe = new Vector3(0, 0, -50);
  const right = new Vector3().setFromMatrixColumn(first.camera.matrixWorld, 0);
  const up = new Vector3().setFromMatrixColumn(first.camera.matrixWorld, 1);
  const coverage = (view: ReturnType<typeof shadowView>) => [
    shadowPixel(probe.clone().add(right), view).distanceTo(shadowPixel(probe, view)),
    shadowPixel(probe.clone().add(up), view).distanceTo(shadowPixel(probe, view)),
  ];
  const expected = coverage(first);
  assert.ok(expected.every(pixels => pixels > 5), 'the regression fixture has meaningful pixels per centimetre');
  for (const [yaw, pitch, roll] of [[0, 0, 0], [35, 0, 0], [-55, 0, 0], [0, 40, 0], [0, -35, 0],
    [40, 30, 10], [-45, -30, -15], [80, 15, 0]]) {
    f.root.rotation.set(pitch! * Math.PI / 180, yaw! * Math.PI / 180, roll! * Math.PI / 180, 'YXZ');
    f.render();
    const view = shadowView(f), actual = coverage(view);
    for (let axis = 0; axis < 2; axis++) assert.ok(Math.abs(actual[axis]! - expected[axis]!) < 1e-7,
      `a ${yaw}° yaw / ${pitch}° nod must not resize shadow texels`);
    const positions = f.geometry.getAttribute('position');
    for (let index = 0; index < positions.count; index++) {
      const projected = new Vector3().fromBufferAttribute(positions, index).applyMatrix4(f.mixed.matrixWorld).project(view.camera);
      assert.ok(Math.abs(projected.x) <= 1 && Math.abs(projected.y) <= 1 && Math.abs(projected.z) <= 1,
        `the fixed coverage must contain frame vertex ${index} throughout the turn`);
    }
  }
});

test('reserved fitting scale covers startup growth without resizing shadow texels or wasting extra resolution', t => {
  const f = deepFrameFixture(), maximumSize = deepFrameFixture();
  t.after(() => {f.dispose(); maximumSize.dispose();});
  f.controller.dispose(); f.root.scale.setScalar(75);
  const controller = new EyewearShadow(f.fake.renderer, f.root, f.face, 130); t.after(() => controller.dispose());
  controller.render(f.camera, f.source, 640, 360, DEFAULT_SHADOW_SETTINGS);
  const first = shadowView(f);
  maximumSize.root.scale.setScalar(130); maximumSize.render();
  const alreadyFullSize = shadowView(maximumSize);
  assert.ok(Math.abs((first.camera.right - first.camera.left)
    - (alreadyFullSize.camera.right - alreadyFullSize.camera.left)) < 1e-10,
  'reserve the declared maximum once, without multiplying it by the growth ratio again');
  const probe = new Vector3(0, 0, -50), right = new Vector3().setFromMatrixColumn(first.camera.matrixWorld, 0);
  const expectedDensity = shadowPixel(probe.clone().add(right), first).distanceTo(shadowPixel(probe, first));
  for (const fitScale of [.75, .8, 1, 1.25, 1.3]) for (const yaw of [-.6, 0, .6]) {
    f.root.scale.setScalar(100 * fitScale); f.root.rotation.set(.35, yaw, 0);
    controller.render(f.camera, f.source, 640, 360, DEFAULT_SHADOW_SETTINGS);
    const view = shadowView(f), density = shadowPixel(probe.clone().add(right), view).distanceTo(shadowPixel(probe, view));
    assert.ok(Math.abs(density - expectedDensity) < 1e-7, 'fitting must not reintroduce a changing shadow grid');
    const positions = f.geometry.getAttribute('position');
    for (let index = 0; index < positions.count; index++) {
      const projected = new Vector3().fromBufferAttribute(positions, index).applyMatrix4(f.mixed.matrixWorld).project(view.camera);
      assert.ok(Math.abs(projected.x) <= 1 && Math.abs(projected.y) <= 1 && Math.abs(projected.z) <= 1,
        `shadow coverage must include the enlarged model at fit ${fitScale}`);
    }
  }
});

test('subtexel movement holds the shadow grid until a bounded whole-texel step', t => {
  const f = deepFrameFixture(); t.after(f.dispose); f.render();
  const initial = shadowView(f), right = new Vector3().setFromMatrixColumn(initial.camera.matrixWorld, 0),
    up = new Vector3().setFromMatrixColumn(initial.camera.matrixWorld, 1);
  const texelX = (initial.camera.right - initial.camera.left) / initial.width;
  const texelY = (initial.camera.top - initial.camera.bottom) / initial.height;
  // Put the model centre in the centre of the current grid cell before testing small signed motion.
  const modelCenter = new Box3().setFromBufferAttribute(f.geometry.getAttribute('position') as BufferAttribute)
    .getCenter(new Vector3()).applyMatrix4(f.root.matrixWorld);
  const gridCenter = initial.camera.position;
  f.root.position.addScaledVector(right, gridCenter.dot(right) - modelCenter.dot(right));
  f.root.position.addScaledVector(up, gridCenter.dot(up) - modelCenter.dot(up));
  f.render();
  const basePosition = f.root.position.clone(), baseView = shadowView(f), probe = new Vector3(0, 0, -50),
    basePixel = shadowPixel(probe, baseView);
  for (const [x, y] of [[.2, 0], [-.2, 0], [0, .2], [0, -.2], [.2, -.2]]) {
    f.root.position.copy(basePosition).addScaledVector(right, x! * texelX).addScaledVector(up, y! * texelY);
    f.render();
    assert.ok(shadowPixel(probe, shadowView(f)).distanceTo(basePixel) < 1e-7,
      'small detector translations must not move the sampling grid fractionally');
  }
  for (const [x, y] of [[.8, 0], [-.8, 0], [0, .8], [0, -.8]]) {
    f.root.position.copy(basePosition).addScaledVector(right, x! * texelX).addScaledVector(up, y! * texelY);
    f.render();
    const delta = shadowPixel(probe, shadowView(f)).sub(basePixel);
    assert.ok(Math.abs(delta.x + Math.sign(x!)) < 1e-7 && Math.abs(delta.y + Math.sign(y!)) < 1e-7,
      'crossing a cell boundary changes coverage by exactly one texel, without drift or a larger jump');
  }
  f.root.position.copy(basePosition).addScaledVector(right, texelX * 10.2); f.render();
  const moved = shadowPixel(probe, shadowView(f)).sub(basePixel);
  assert.ok(Math.abs(moved.x + 10) < 1e-7 && Math.abs(moved.y) < 1e-7,
    'larger real movement follows immediately and is quantized without introducing a temporal lag');
});

test('a failed shadow pass still restores the caller renderer state', t => {
  const f = sceneFixture(); t.after(f.dispose);
  const before = f.fake.snapshot(), sources = f.sourceSnapshot();
  f.fake.state.failOnDraw = 2;
  assert.throws(() => f.render(), /Injected shadow draw failure/);
  assert.deepEqual(f.fake.snapshot(), before);
  assert.deepEqual(f.sourceSnapshot(), sources);
  f.fake.state.failOnDraw = 0;
  assert.ok(f.render() instanceof Texture, 'a recoverable draw exception does not corrupt the controller');
});

test('the observed-face receiver uses this frame, independent controls and the current hair warp', t => {
  const f = sceneFixture(), mask = new DataTexture(new Uint8Array([255]), 1, 1);
  const next = new CanvasTexture({width: 320, height: 180} as HTMLCanvasElement);
  next.offset.set(.1, .2); next.repeat.set(.8, .7); next.updateMatrix();
  t.after(() => {f.dispose(); mask.dispose(); next.dispose();});
  const settings = {...DEFAULT_SHADOW_SETTINGS, frameStrength: .35, lensStrength: .6, softness: 2};
  const output = f.controller.render(f.camera, f.source, 640, 360, settings, mask, [1, 0, .1, 0, 1, .2, 0, 0, 1]);
  const receiver = f.fake.state.draws.flatMap(draw => draw.meshes)
    .flatMap(materials).find(material => 'uniforms' in material && (material as ShaderMaterial).uniforms.hairMask) as ShaderMaterial;
  assert.ok(receiver);
  assert.equal(receiver.uniforms.sourceImage!.value, f.source);
  assert.equal(receiver.uniforms.hairMask!.value, mask);
  assert.equal(receiver.uniforms.hasHairMask!.value, 1);
  const mapped = new Vector3(.25, .5, 1).applyMatrix3(receiver.uniforms.maskUv!.value as Matrix3);
  assert.ok(Math.abs(mapped.x - .35) < 1e-12 && Math.abs(mapped.y - .7) < 1e-12,
    'row-major frame-to-mask transforms retain their translation');
  assert.deepEqual(receiver.uniforms.strengths!.value.toArray(), [.35, .6]);
  assert.equal(receiver.uniforms.softness!.value, 2);
  assert.equal(f.controller.render(f.camera, next, 320, 180, settings), output, 'resize reuses the owned texture object');
  assert.equal(receiver.uniforms.sourceImage!.value, next);
  assert.deepEqual(receiver.uniforms.sourceUv!.value.toArray(), next.matrix.toArray());
  assert.equal(receiver.uniforms.hairMask!.value, null);
  assert.equal(receiver.uniforms.hasHairMask!.value, 0, 'a missing mask cannot keep masking with previous-frame hair');
  assert.deepEqual(receiver.uniforms.maskUv!.value.toArray(), [1, 0, 0, 0, 1, 0, 0, 0, 1]);
  assert.deepEqual(receiver.uniforms.viewport!.value.toArray(), [320, 180]);
});

test('disabled and zero-strength shadows skip GPU work and return the original camera texture', t => {
  const f = sceneFixture(); t.after(f.dispose);
  assert.equal(f.render({...DEFAULT_SHADOW_SETTINGS, enabled: false}), f.source);
  assert.equal(f.render({...DEFAULT_SHADOW_SETTINGS, frameStrength: 0, lensStrength: 0}), f.source);
  assert.equal(f.fake.state.draws.length, 0);
});

test('disposing shadow passes leaves caller geometry, materials and camera textures alive', t => {
  const f = sceneFixture(); t.after(f.dispose);
  let sourceDisposals = 0;
  for (const ownedByCaller of [f.geometry, f.face, f.frame, f.lens, f.texture, f.source]) {
    ownedByCaller.addEventListener('dispose', () => {sourceDisposals++;});
  }
  const output = f.render(), targets = new Set(f.fake.state.draws.map(draw => draw.target).filter(value => value !== null));
  assert.ok(targets.size > 0);
  let targetDisposals = 0;
  for (const target of targets) target.addEventListener('dispose', () => {targetDisposals++;});
  let outputDisposals = 0; output.addEventListener('dispose', () => {outputDisposals++;});
  f.controller.dispose(); f.controller.dispose();
  assert.equal(sourceDisposals, 0, 'shadow ownership ends at its own GPU resources');
  assert.equal(targetDisposals, targets.size, 'every used offscreen target is released exactly once');
  assert.ok(outputDisposals <= 1, 'cleanup is idempotent');
  const draws = f.fake.state.draws.length;
  assert.equal(f.render(), f.source); assert.equal(f.fake.state.draws.length, draws, 'disposed effects cannot submit more GPU work');
});

test('legacy-only shadows retain one caster pass and require no floating targets or overflow readback', t => {
  const f = sceneFixture(); t.after(f.dispose); f.fake.state.floatColorSupported = false;
  f.render();
  assert.equal(f.fake.state.draws.length, 3);
  assert.equal(f.fake.state.readbacks.length, 0);
  assert.equal(f.controller.layerDiagnostics.enabled, false);
  assert.ok(f.fake.state.draws.every(draw => draw.target?.texture.type !== FloatType));
});

test('canonical layers peel independently after opaque blockers with exact depth and a checked extra layer', t => {
  const f = sceneFixture((_frame, lens) => attachAppearance(lens)); t.after(f.dispose);
  const before = f.fake.snapshot(), source = f.sourceSnapshot(); f.render();
  const draws = f.fake.state.draws;
  const base = draws[0]!;
  assert.deepEqual(base.visibleMaterials.map(material => material.userData.kind), ['frame']);
  const peels = draws.filter(draw => draw.peelIndex !== null);
  assert.equal(peels.length, MAX_CANONICAL_SHADOW_LAYERS + 1);
  for (let i = 0; i < peels.length; i++) {
    const peel = peels[i]!;
    assert.equal(peel.peelIndex, i);
    assert.equal(peel.previousColor, i ? peels[i - 1]!.target!.texture : null);
    assert.equal(peel.target!.texture.type, FloatType);
    assert.equal(peel.target!.depthTexture!.type, FloatType);
    for (const material of peel.visibleMaterials as ShaderMaterial[]) {
      assert.equal(material.userData.lensAppearanceSchema, 1);
      assert.equal(material.uniforms.shadowPeelOpaqueDepth!.value, base.target!.depthTexture);
      assert.ok(material.fragmentShader.includes('gl_FragCoord.z >= opaqueDepth'));
      assert.ok(material.fragmentShader.includes('gl_FragCoord.z <= previousDepth'));
    }
  }
  const receiver = draws.flatMap(draw => draw.visibleMaterials).find(material =>
    material.name === 'Observed face eyewear shadow receiver') as ShaderMaterial;
  for (let i = 0; i < MAX_CANONICAL_SHADOW_LAYERS; i++) {
    assert.equal(receiver.uniforms[`canonicalLayer${i}`]!.value, peels[i]!.target!.texture);
  }
  assert.equal(receiver.defines.CANONICAL_SHADOW_LAYERS, 1);
  assert.ok(receiver.fragmentShader.includes('layer.a >= receiverDepth'));
  assert.deepEqual(f.controller.layerDiagnostics, {enabled: true, maxLayers: 4, overflow: false, overflowCheck: 'per_frame_gpu_reduction'});
  assert.deepEqual(f.fake.snapshot(), before); assert.deepEqual(f.sourceSnapshot(), source);
  assert.equal(f.fake.state.readbacks.length, 1);
  assert.equal(f.fake.state.readbacks[0]!.width, 1); assert.equal(f.fake.state.readbacks[0]!.height, 1);
  assert.equal(f.fake.state.readbacks[0]!.target.texture.name, 'Generated optical overflow flag');
});

test('excess canonical shadow layers fail before composition and restore all state', t => {
  const f = sceneFixture((_frame, lens) => attachAppearance(lens)); t.after(f.dispose);
  const before = f.fake.snapshot(), source = f.sourceSnapshot();
  f.fake.state.overflowFlag = 255;
  assert.throws(() => f.render(), OpticalLayerOverflowError);
  assert.equal(f.controller.layerDiagnostics.overflow, true);
  assert.ok(!f.fake.state.draws.some(draw => draw.visibleMaterials.some(material =>
    material.name === 'Shadow camera copy' || material.name === 'Observed face eyewear shadow receiver')));
  assert.deepEqual(f.fake.snapshot(), before); assert.deepEqual(f.sourceSnapshot(), source);
  for (const caster of casterMeshes(f)) for (const material of materials(caster)) assert.equal(material.visible, true);
  f.fake.state.overflowFlag = 0;
  assert.ok(f.render() instanceof Texture);
  assert.equal(f.controller.layerDiagnostics.overflow, false);
});

test('canonical shadows reject unavailable float render targets and mixed legacy optical volumes', t => {
  const f = sceneFixture(); t.after(f.dispose); f.controller.dispose();
  attachAppearance(f.lens); f.fake.state.floatColorSupported = false;
  assert.throws(() => new EyewearShadow(f.fake.renderer, f.root, f.face), /EXT_color_buffer_float/);
  f.fake.state.floatColorSupported = true;
  const legacy = new MeshPhysicalMaterial({transmission: .8}); t.after(() => legacy.dispose());
  f.root.add(new Mesh(f.geometry, legacy));
  assert.throws(() => new EyewearShadow(f.fake.renderer, f.root, f.face), /legacy transmissive volumes/);
});

test('overflow reduction covers odd extents, binarizes before RGBA8 and reuses resources', t => {
  const fake = backendFixture(), checker = new OpticalOverflowChecker(), texture = new Texture();
  t.after(() => {checker.dispose(); texture.dispose(); fake.dispose();});
  const before = fake.snapshot();
  checker.assertNoOverflow(fake.renderer, texture, 5, 3, 'Fixture', 'one_minus_alpha');
  const first = fake.state.draws.slice();
  assert.deepEqual(first.map(draw => [draw.target!.width, draw.target!.height]), [[3, 2], [2, 1], [1, 1]]);
  assert.deepEqual(first.map(draw => draw.inputSize), [[5, 3], [3, 2], [2, 1]]);
  assert.deepEqual(first.map(draw => draw.invertAlpha), [1, 0, 0]);
  assert.equal(first[0]!.inputTexture, texture);
  assert.equal(first[1]!.inputTexture, first[0]!.target!.texture);
  const shader = first[0]!.visibleMaterials[0] as ShaderMaterial;
  assert.ok(shader.fragmentShader.includes('value > 0.0 ? 1.0 : 0.0'));
  assert.ok(shader.fragmentShader.includes('pixel.x >= inputSize.x || pixel.y >= inputSize.y'));
  checker.assertNoOverflow(fake.renderer, texture, 5, 3, 'Fixture');
  assert.deepEqual(fake.state.draws.slice(3).map(draw => draw.target), first.map(draw => draw.target));
  assert.equal(fake.state.draws[3]!.invertAlpha, 0);
  assert.deepEqual(fake.snapshot(), before);
  let disposed = 0;
  for (const draw of first) draw.target!.addEventListener('dispose', () => {disposed++;});
  checker.assertNoOverflow(fake.renderer, texture, 1, 1, 'Fixture');
  assert.equal(disposed, 3);
  assert.equal(fake.state.readbacks.at(-1)!.width, 1); assert.equal(fake.state.readbacks.at(-1)!.height, 1);
});

test('overflow checker fails closed on an unwritten readback and preserves renderer state after failure', t => {
  const fake = backendFixture(), checker = new OpticalOverflowChecker(), texture = new Texture();
  t.after(() => {checker.dispose(); texture.dispose(); fake.dispose();});
  const before = fake.snapshot(); fake.state.skipReadback = true;
  assert.throws(() => checker.assertNoOverflow(fake.renderer, texture, 3, 2, 'Fixture'), /Fixture: optical layer capacity exceeded/);
  assert.deepEqual(fake.snapshot(), before);
  fake.state.skipReadback = false; fake.state.overflowFlag = 255;
  assert.throws(() => checker.assertNoOverflow(fake.renderer, texture, 3, 2, 'Fixture'), OpticalLayerOverflowError);
  fake.state.overflowFlag = 0;
  checker.assertNoOverflow(fake.renderer, texture, 3, 2, 'Fixture');
  checker.dispose(); checker.dispose();
  assert.throws(() => checker.assertNoOverflow(fake.renderer, texture, 3, 2, 'Fixture'), /disposed/);
});

test('canonical layer targets and generated overflow resources dispose without disposing source assets', t => {
  const f = sceneFixture((_frame, lens) => attachAppearance(lens)); t.after(f.dispose); f.render();
  const targets = new Set(f.fake.state.draws.map(draw => draw.target).filter(target => target !== null));
  let targetDisposals = 0, sourceDisposals = 0;
  for (const target of targets) target.addEventListener('dispose', () => {targetDisposals++;});
  for (const resource of [f.geometry, f.face, f.frame, f.lens, f.source]) resource.addEventListener('dispose', () => {sourceDisposals++;});
  f.controller.dispose(); f.controller.dispose();
  assert.equal(targetDisposals, targets.size);
  assert.equal(sourceDisposals, 0);
});

test('effective shadow groups capture one nearest map per explicit group with the SAME caster shader used for peels', t => {
  const f = effectiveFixture(); t.after(f.dispose);
  const before = f.fake.snapshot(); f.render();
  const captures = f.fake.state.draws.filter(draw => draw.groupCapture === 1);
  assert.equal(captures.length, 2, 'three closed members form two effective groups');
  assert.deepEqual(captures.map(draw => draw.meshes.length), [2, 1]);
  const peels = f.fake.state.draws.filter(draw => draw.groupCapture === 0);
  assert.equal(peels.length, MAX_CANONICAL_SHADOW_LAYERS + 1);
  const peelMaterials = peels[0]!.visibleMaterials as ShaderMaterial[];
  assert.equal(peelMaterials.length, 3);
  assert.notEqual(captures[0]!.target, captures[1]!.target, 'same source material does not alias group depth');
  for (const capture of captures) {
    assert.equal(capture.groupNearest, null, 'capture must not bind its own framebuffer texture');
    for (const material of capture.visibleMaterials as ShaderMaterial[]) {
      assert.ok(peelMaterials.includes(material), 'nearest capture borrows exact peel material, not a surrogate shader');
      assert.equal(material.side, DoubleSide);
      assert.equal(material.uniforms.shadowGroupCapture!.value, 0, 'capture mode restored before peels');
      assert.equal(material.uniforms.shadowGroupNearest!.value, capture.target!.texture);
      assert.ok(material.fragmentShader.includes(LENS_INCIDENCE_GLSL));
      assert.ok(material.fragmentShader.includes('abs(normalize(vLensLightNormal).z)'));
      assert.ok(material.vertexShader.includes('modelViewMatrix * vec4(0.0, 0.0, 1.0, 0.0)'));
      assert.ok(material.fragmentShader.includes('normalize(vLensLightFrontAxis).z < 0.0'));
      assert.ok(material.fragmentShader.includes('lensIncidenceAngleDegrees(cosine)'));
      assert.ok(material.fragmentShader.includes('nearestDepth >= 1.0 || gl_FragCoord.z != nearestDepth'));
      assert.ok(material.fragmentShader.indexOf('if (shadowGroupCapture > 0.5)')
        < material.fragmentShader.indexOf('float opaqueDepth'), 'capture runs before opaque, peeling and optical evaluation');
      assert.ok(material.fragmentShader.includes('gl_FragCoord.z >= opaqueDepth'));
      assert.ok(material.fragmentShader.includes('gl_FragCoord.z <= previousDepth'));
    }
  }
  assert.deepEqual(f.fake.snapshot(), before, 'including caller depth-clear value');
  f.fake.state.draws.length = 0; f.render();
  const next = f.fake.state.draws.filter(draw => draw.groupCapture === 1);
  assert.deepEqual(next.map(draw => draw.target), captures.map(draw => draw.target));
  assert.ok(next.every(draw => draw.groupNearest === null), 'later captures must also clear attached samplers');
});

test('effective group shadow capture follows current world transforms and respects source hierarchy/material visibility', t => {
  const f = effectiveFixture(); t.after(f.dispose);
  const hidden = new Group(); f.root.add(hidden); hidden.add(f.parts[1]!); hidden.visible = false;
  f.parts[0]!.rotation.y = .4; f.root.rotation.y = .6; f.render();
  const captures = f.fake.state.draws.filter(draw => draw.groupCapture === 1);
  assert.deepEqual(captures.map(draw => draw.meshes.length), [1, 1]);
  assert.deepEqual(captures[0]!.matrices[0], f.parts[0]!.matrixWorld.toArray());
  f.parts[0]!.position.x += .01; f.fake.state.draws.length = 0; f.render();
  assert.deepEqual(f.fake.state.draws.find(draw => draw.groupCapture === 1)!.matrices[0], f.parts[0]!.matrixWorld.toArray());
  f.material.visible = false; f.fake.state.draws.length = 0; f.render();
  assert.ok(f.fake.state.draws.every(draw => draw.groupCapture === null));
  assert.equal(f.material.visible, false, 'source material visibility is never overridden');
});

test('effective capture failure restores mode, sampler, material ownership and all renderer state before retry', t => {
  const f = effectiveFixture(); t.after(f.dispose); f.render();
  const captures = f.fake.state.draws.filter(draw => draw.groupCapture === 1);
  const casters = captures.flatMap(draw => draw.visibleMaterials) as ShaderMaterial[];
  const samplerBefore = casters.map(material => material.uniforms.shadowGroupNearest!.value);
  const before = f.fake.snapshot(); f.fake.state.draws.length = 0;
  f.fake.state.failOnDraw = 2; // Opaque pass, then first group capture.
  assert.throws(f.render, /Injected shadow draw failure/);
  assert.deepEqual(f.fake.snapshot(), before);
  for (let i = 0; i < casters.length; i++) {
    assert.equal(casters[i]!.uniforms.shadowGroupCapture!.value, 0);
    assert.equal(casters[i]!.uniforms.shadowGroupNearest!.value, samplerBefore[i]);
    assert.equal(casters[i]!.visible, true);
  }
  f.fake.state.failOnDraw = 0; assert.ok(f.render() instanceof Texture);
});

test('effective shadow grouping rejects missing/conflicting identities and inconsistent descriptors without inferring from material', t => {
  const f = effectiveFixture(['one', 'one']); t.after(f.dispose); f.controller.dispose();
  const construct = () => new EyewearShadow(f.fake.renderer, f.root, f.face);
  delete f.parts[0]!.userData.opticalGroupId;
  assert.throws(construct, /explicit optical group ID/); f.parts[0]!.userData.opticalGroupId = 'one';
  f.material.userData.canonicalOpticalGroupId = 'different';
  assert.throws(construct, /group IDs disagree/); delete f.material.userData.canonicalOpticalGroupId;
  f.material.userData.canonicalLensProfile = 'front_sheet_v1';
  assert.throws(construct, /profiles disagree/); delete f.material.userData.canonicalLensProfile;
  const different = new MeshPhysicalMaterial(); attachAppearance(different, canonicalAppearance({normal_reflectance_rgb: [.1, .1, .1]}));
  t.after(() => different.dispose()); f.parts[1]!.material = different;
  assert.throws(construct, /one shared canonical descriptor/);
  f.parts[1]!.material = f.material;
  let sourceDisposals = 0;
  for (const resource of [f.material, f.geometry, f.face]) resource.addEventListener('dispose', () => {sourceDisposals++;});
  const valid = construct(); valid.dispose(); assert.equal(sourceDisposals, 0);
});

test('effective shadow nearest targets dispose once and layer overflow still fails before receiver composition', t => {
  const f = effectiveFixture(); t.after(f.dispose); f.render();
  const targets = new Set(f.fake.state.draws.map(draw => draw.target).filter(target => target !== null));
  const nearest = [...targets].filter(target => target.texture.name.startsWith('Nearest optical group '));
  assert.equal(nearest.length, 2);
  let disposals = 0, sourceDisposals = 0;
  for (const target of targets) target.addEventListener('dispose', () => {disposals++;});
  for (const resource of [f.material, f.geometry, f.face, f.source]) resource.addEventListener('dispose', () => {sourceDisposals++;});
  f.fake.state.draws.length = 0; f.fake.state.overflowFlag = 255;
  assert.throws(f.render, OpticalLayerOverflowError);
  assert.ok(!f.fake.state.draws.some(draw => draw.visibleMaterials.some(material => material.name === 'Observed face eyewear shadow receiver')));
  f.controller.dispose(); f.controller.dispose();
  assert.equal(disposals, targets.size); assert.equal(sourceDisposals, 0);
});

test('a role-tagged translucent front casts a material-coloured shadow that is clipped like a frame and stays out of the optical layers', t => {
  const f = sceneFixture((_frame, lens) => {attachAppearance(lens); lens.transmission = 0;});
  t.after(f.dispose);
  f.mixed.userData.partRole = 'lens';
  const crystal = new MeshPhysicalMaterial({transmission: 1, ior: 1.49, thickness: .004, color: 0xf2ece0});
  const front = new Mesh(new BoxGeometry(.12, .04, .012).translate(0, 0, -.01), crystal); front.userData.partRole = 'frame';
  f.root.add(front);
  t.after(() => {front.geometry.dispose(); crystal.dispose();});
  classifyAssetMaterials(f.root);
  const controller = new EyewearShadow(f.fake.renderer, f.root, f.face); t.after(() => controller.dispose());
  controller.render(f.camera, f.source, 640, 360, DEFAULT_SHADOW_SETTINGS);
  const casters = [...new Set(f.fake.state.draws.flatMap(draw => draw.meshes))].flatMap(materials);
  const caster = casters.find(material => material.userData.kind === 'translucent-frame') as ShaderMaterial | undefined;
  assert.ok(caster, 'the crystal front has its own caster kind');
  assert.deepEqual(caster.uniforms.baseTransmission!.value.toArray(), lensShadowTransmission(crystal));
  assert.equal(caster.uniforms.lens!.value, 1, 'labelled transmissive for the receiver');
  assert.equal(caster.uniforms.clipped!.value, 1, 'clipped with the arms, unlike a lens');
  assert.ok(!casters.some(material => material.userData.kind === 'lens' && material.userData.lensAppearanceSchema !== 1),
    'the translucent front never counts as a legacy transmissive lens beside the canonical layers');
});

test('a role-tagged crystal temple casts the same clipped translucent-frame shadow as a crystal front, and a visibility overlay of it casts nothing', t => {
  const f = sceneFixture((_frame, lens) => {attachAppearance(lens); lens.transmission = 0;});
  t.after(f.dispose);
  f.mixed.userData.partRole = 'lens';
  const crystal = new MeshPhysicalMaterial({transmission: 1, ior: 1.49, thickness: .004, color: 0xf2ece0,
    attenuationColor: 0xf3ead6, attenuationDistance: .005});
  const arm = new Mesh(new BoxGeometry(.004, .004, .15).translate(-.065, 0, -.09), crystal); arm.userData.partRole = 'temple';
  const overlay = new Mesh(arm.geometry, crystal.clone()); overlay.userData = {partRole: 'temple', templeVisibilityOverlay: true};
  f.root.add(arm, overlay);
  t.after(() => {arm.geometry.dispose(); crystal.dispose(); (overlay.material as Material).dispose();});
  classifyAssetMaterials(f.root);
  const controller = new EyewearShadow(f.fake.renderer, f.root, f.face); t.after(() => controller.dispose());
  controller.render(f.camera, f.source, 640, 360, DEFAULT_SHADOW_SETTINGS);
  const drawn = [...new Set(f.fake.state.draws.flatMap(draw => draw.meshes))];
  const casters = drawn.flatMap(materials).filter(material => material.userData.kind === 'translucent-frame') as ShaderMaterial[];
  assert.equal(casters.length, 1, 'the crystal arm has one translucent-frame caster; its overlay is not a caster');
  const caster = casters[0]!;
  assert.deepEqual(caster.uniforms.baseTransmission!.value.toArray(), lensShadowTransmission(crystal));
  assert.ok(caster.uniforms.baseTransmission!.value.toArray().every((channel: number) => channel > 0 && channel < 1),
    'a tinted, absorbing arm shadows the face with its material colour, not as an opaque bar');
  assert.equal(caster.uniforms.lens!.value, 1, 'labelled transmissive for the receiver');
  assert.equal(caster.uniforms.clipped!.value, 1, 'clipped with the arms');
  assert.equal(drawn.filter(mesh => mesh.geometry === arm.geometry).length, 1, 'the arm geometry is drawn once per pass: by the original');
  assert.ok(!drawn.flatMap(materials).some(material => material.userData.kind === 'lens' && material.userData.lensAppearanceSchema !== 1));
});
