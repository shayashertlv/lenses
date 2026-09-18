import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {
  BufferAttribute, BufferGeometry, CanvasTexture, Color, Group, Material, Matrix4, Mesh, MeshPhysicalMaterial,
  Euler, MeshStandardMaterial, PerspectiveCamera, Quaternion, Scene, ShaderLib, Texture, UniformsUtils,
  SRGBColorSpace, Vector2, Vector3, Vector4, WebGLRenderTarget,
} from 'three';
import type {ShaderMaterial, WebGLRenderer} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {EYEWEAR} from '../src/eyewear/catalog.ts';
import {createTempleBlendConfiguration, createTempleClip} from '../src/render/temple-clip.ts';
import {
  createTempleVisibility, createTempleVisibilityConfiguration, createLegacyTempleVisibilityConfiguration,
  createViewTempleVisibilityConfiguration, VIEW_TEMPLE_VISIBILITY_METHOD, depthRelief, hasTempleVisibilityEffect,
  templeLiftedDepth, LEGACY_TEMPLE_VISIBILITY_METHOD,
  TEMPLE_VISIBILITY_METHOD, TEMPLE_VISIBILITY_PARAMETERS, validateTempleVisibility,
} from '../src/render/temple-visibility.ts';
import type {TempleVisibilityConfiguration} from '../src/render/temple-visibility.ts';

const pose = (yaw: number) => new Matrix4().makeRotationY(yaw).setPosition(0, 0, -50).toArray();
type CompileInput = Parameters<Material['onBeforeCompile']>[0];
function compile(material: Material) {
  const standard = ShaderLib.standard!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader'> = {
    uniforms: UniformsUtils.clone(standard.uniforms), vertexShader: standard.vertexShader, fragmentShader: standard.fragmentShader,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, {}]);
  return shader;
}

function fakeBackend(samples = 4) {
  const state = {
    target: null as WebGLRenderTarget | null, face: 0, mip: 0,
    viewport: new Vector4(7, 8, 120, 80), scissor: new Vector4(2, 3, 40, 30), scissorTest: true,
    color: new Color(0x123456), alpha: .6, renderCount: 0, clearCount: 0,
    onRender: (_scene: Scene, _camera: PerspectiveCamera): void => {},
  };
  const backend = {
    capabilities: {samples}, autoClear: true,
    getDrawingBufferSize: (out: Vector2) => out.set(640, 360),
    getRenderTarget: () => state.target,
    getActiveCubeFace: () => state.face,
    getActiveMipmapLevel: () => state.mip,
    setRenderTarget: (value: WebGLRenderTarget | null, face = 0, mip = 0) => { state.target = value; state.face = face; state.mip = mip; },
    getViewport: (out: Vector4) => out.copy(state.viewport),
    setViewport: (x: number | Vector4, y?: number, z?: number, w?: number) => {
      if (x instanceof Vector4) state.viewport.copy(x); else state.viewport.set(x, y!, z!, w!);
    },
    getScissor: (out: Vector4) => out.copy(state.scissor),
    setScissor: (out: Vector4) => state.scissor.copy(out),
    getScissorTest: () => state.scissorTest,
    setScissorTest: (value: boolean) => { state.scissorTest = value; },
    getClearColor: (out: Color) => out.copy(state.color),
    getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha: number) => { state.color.set(value); state.alpha = alpha; },
    clear: (color: boolean, depth: boolean, stencil: boolean) => {
      assert.ok(color && depth && stencil); state.clearCount++;
      assert.equal(state.color.getHex(), 0); assert.equal(state.alpha, 0);
    },
    render: (scene: Scene, camera: PerspectiveCamera) => {
      Reflect.apply(scene.onBeforeRender, scene, [backend, scene, camera, state.target]);
      state.renderCount++; state.onRender(scene, camera);
    },
  };
  return {state, backend, renderer: backend as unknown as WebGLRenderer};
}

function syntheticFixture(samples = 4, beforeRender?: Scene['onBeforeRender']) {
  const geometry = new BufferGeometry().setAttribute('position', new BufferAttribute(new Float32Array([
    -.055, 0, -.02, -.055, 0, -.08, -.057, .002, -.11,
  ]), 3));
  geometry.setIndex([0, 1, 2]); geometry.computeVertexNormals();
  const lensGeometry = new BufferGeometry().setAttribute('position', new BufferAttribute(new Float32Array([
    -.03, 0, -.014, -.02, 0, 0, -.02, .01, 0,
  ]), 3));
  const frame = new MeshStandardMaterial(), lens = new MeshPhysicalMaterial({transmission: 1}), texture = new Texture();
  frame.map = texture;
  const root = new Group().add(new Mesh(geometry, frame), new Mesh(geometry, frame), new Mesh(lensGeometry, lens));
  const scene = new Scene(), eyewearPose = new Group().add(root), camera = new PerspectiveCamera(60, 16 / 9, 1, 1000);
  scene.add(eyewearPose); scene.background = new Color(0xabcdef);
  if (beforeRender) scene.onBeforeRender = beforeRender;
  const fake = fakeBackend(samples), clip = createTempleClip(root);
  const controller = createTempleVisibility(root, {renderer: fake.renderer, scene, camera, eyewearPose});
  const overlays = root.children.filter((child): child is Mesh => child instanceof Mesh && child.userData.templeVisibilityOverlay === true);
  return {geometry, lensGeometry, frame, lens, texture, root, scene, eyewearPose, camera, fake, clip, controller, overlays,
    dispose: () => { controller.dispose(); clip.dispose(); geometry.dispose(); lensGeometry.dispose(); frame.dispose(); lens.dispose(); texture.dispose(); }};
}

/** The head at `yaw`, tilted sideways by `roll` (lying down), 50 cm away. */
const tilted = (yaw: number, roll: number, pitch = 0) => new Matrix4().compose(new Vector3(0, 0, -50),
  new Quaternion().setFromEuler(new Euler(pitch, yaw, roll, 'YXZ')), new Vector3(1, 1, 1)).toArray();

test('v4 gives up an arm by how far behind the head it is, not by the head angles', () => {
  // The relief curve itself: in front of the head, or barely behind it, the arm is kept whole; well behind it, given up.
  assert.equal(depthRelief(-5), 1); assert.equal(depthRelief(0), 1);
  assert.equal(depthRelief(TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm), 1);
  assert.equal(depthRelief(TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm), 0);
  assert.equal(depthRelief(12), 0); assert.equal(depthRelief(Number.NaN), 0);
  let previous = 1;
  for (let behind = -1; behind <= 4; behind += 0.1) {
    const value = depthRelief(behind);
    assert.ok(value <= previous + 1e-12, `the relief never rises with depth (${behind})`);
    assert.ok(value >= 0 && value <= 1); previous = value;
  }
  // The shipped arms run 6 to 12 mm inside the canonical head's own silhouette, so the band must keep a whole
  // centimetre of burial; otherwise a plain depth test would be enough and the arm would vanish at the temple.
  assert.ok(TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm >= 0.5);
  assert.ok(TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm > TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm);
});

test('v4 does not go blind when the head is tilted, where the v3 percentages collapse', () => {
  // The wearer photographed this lying down. The v3 confidence reads the camera's bearing in HEAD coordinates, which
  // shrinks with cos(roll), so tilting the head takes away relief that an upright head at the same turn would get.
  const upright = createTempleVisibilityConfiguration(tilted(25 * Math.PI / 180, 0), 4, 'angles');
  assert.ok(upright.negativeXWeight > 0.99, `an upright head at 25 deg gets full relief (${upright.negativeXWeight})`);
  for (const rollDeg of [45, 55, 70]) {
    const lying = createTempleVisibilityConfiguration(tilted(25 * Math.PI / 180, rollDeg * Math.PI / 180), 4, 'angles');
    assert.ok(lying.negativeXWeight < upright.negativeXWeight * 0.9,
      `v3 gives up more of the arm at roll ${rollDeg} (${lying.negativeXWeight.toFixed(3)}) than upright`);
    // v4 carries the same record but the rule it runs reads the head's depth, which a tilt does not change.
    const depth = createTempleVisibilityConfiguration(tilted(25 * Math.PI / 180, rollDeg * Math.PI / 180), 4, 'depth');
    assert.equal(depth.mode, 'depth');
    assert.equal(depth.negativeXWeight, lying.negativeXWeight, 'the angle weights stay in the record for comparison');
    assert.equal(hasTempleVisibilityEffect(depth), true, 'and there is no pose at which v4 skips its own pass');
  }
  // v3 has poses where it does nothing at all; v4 has none.
  const frontal = createTempleVisibilityConfiguration(pose(0), 4, 'angles');
  assert.equal(frontal.negativeXWeight, 0); assert.equal(frontal.positiveXWeight, 0);
  assert.equal(hasTempleVisibilityEffect(frontal), false, 'v3 switches itself off below about 9 degrees of turn');
  assert.equal(hasTempleVisibilityEffect(createTempleVisibilityConfiguration(pose(0), 4, 'depth')), true);
});

test('the mode reaches the overlay and the dissolve, and switching it recompiles nothing', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement); source.colorSpace = SRGBColorSpace;
  t.after(() => source.dispose());
  const material = f.overlays[0]!.material as Material;
  // v3 at a frontal pose: both percentages are zero, so the overlay is not even drawn.
  f.controller.set(createTempleVisibilityConfiguration(pose(0), 4, 'angles'));
  assert.equal(f.overlays.every(overlay => overlay.visible), false);
  assert.equal(compile(material).uniforms.templeVisibilityDepthMode!.value, 0);
  const version = material.version;
  // v4 at the same pose: drawn, deciding per pixel, and with the pitch dissolve switched off entirely.
  f.controller.set({...createTempleVisibilityConfiguration(pose(0), 4, 'depth'), frontalOcclusionWeight: 0.8});
  assert.equal(f.overlays.every(overlay => overlay.visible), true);
  const shader = compile(material);
  assert.equal(shader.uniforms.templeVisibilityDepthMode!.value, 1);
  assert.equal(shader.uniforms.templeFrontalWeight!.value, 0, 'a fragment behind the head is already culled by its depth');
  assert.deepEqual([shader.uniforms.templeVisibilityRelief!.value.x, shader.uniforms.templeVisibilityRelief!.value.y],
    [TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm, TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm]);
  assert.equal(material.version, version, 'the mode is a uniform, not a define: switching it cannot stall on a recompile');
  // v4 never skips the head pass, so the depth it reads is this frame's.
  f.controller.prepare(pose(0), source);
  assert.ok(f.fake.state.renderCount > 0, 'the head depth pass ran at a pose where v3 would have returned early');
  // And back: the v3 rule returns intact, again without a recompile (prepare's own coverage switch is separate).
  const settled = material.version;
  f.controller.set(createTempleVisibilityConfiguration(pose(.5), 4, 'angles'));
  assert.equal(compile(material).uniforms.templeVisibilityDepthMode!.value, 0);
  assert.equal(material.version, settled);
  // An unknown mode is refused rather than guessed.
  assert.throws(() => f.controller.set({...createTempleVisibilityConfiguration(pose(.5), 4), mode: 'sideways'} as unknown as TempleVisibilityConfiguration),
    /configuration is invalid/);
});

test('camera-relative side weights are continuous, mirrored and independent of caller storage', () => {
  const matrix = pose(0), copy = matrix.slice();
  assert.deepEqual(createLegacyTempleVisibilityConfiguration(matrix, 4), {
    method: LEGACY_TEMPLE_VISIBILITY_METHOD, negativeXWeight: 1, positiveXWeight: 1, coverage: 'alpha-to-coverage',
  });
  assert.deepEqual(matrix, copy);
  for (const angle of [.1, Math.asin(.175), .5, 1]) {
    const a = createLegacyTempleVisibilityConfiguration(pose(angle), 4), b = createLegacyTempleVisibilityConfiguration(pose(-angle), 4);
    assert.equal(a.negativeXWeight, b.positiveXWeight);
    assert.equal(a.positiveXWeight, b.negativeXWeight);
    assert.equal(a.negativeXWeight, 1);
    assert.ok(a.positiveXWeight >= 0 && a.positiveXWeight <= 1);
  }
  assert.ok(Math.abs(createLegacyTempleVisibilityConfiguration(pose(Math.asin(.175)), 4).positiveXWeight - .5) < 1e-14);
  assert.equal(createLegacyTempleVisibilityConfiguration(pose(Math.asin(.35)), 0).positiveXWeight, 0);
  assert.equal(createTempleVisibilityConfiguration(pose(0), 0).coverage, 'ordered-dither');
  assert.throws(() => createTempleVisibilityConfiguration(new Array(16).fill(0), 4), /singular/);
  assert.throws(() => createTempleVisibilityConfiguration(new Matrix4().toArray(), 4), /viewing direction/);
  assert.throws(() => createTempleVisibilityConfiguration([NaN], 4), /pose is invalid/);
  assert.throws(() => createTempleVisibilityConfiguration(pose(0), -1), /sample count/);
  assert.equal(Object.isFrozen(TEMPLE_VISIBILITY_PARAMETERS), true);
});

test('v2 viewing confidence suppresses centered frontal pitch and preserves unambiguous side views', () => {
  for (const pitch of [-60, -45, 0, 45, 60]) {
    const matrix = new Matrix4().makeRotationX(pitch * Math.PI / 180).setPosition(0, 0, -40).toArray();
    const legacy = createLegacyTempleVisibilityConfiguration(matrix, 4), gated = createTempleVisibilityConfiguration(matrix, 4);
    assert.equal(legacy.negativeXWeight, 1); assert.equal(legacy.positiveXWeight, 1);
    assert.equal(gated.negativeXWeight, 0); assert.equal(gated.positiveXWeight, 0);
    assert.equal(gated.method, TEMPLE_VISIBILITY_METHOD);
  }
  for (const direction of [-1, 1]) {
    for (const fraction of [.15, .25, .35, .7]) {
      const matrix = pose(direction * Math.asin(fraction));
      const legacy = createLegacyTempleVisibilityConfiguration(matrix, 4), gated = createTempleVisibilityConfiguration(matrix, 4);
      const confidence = fraction === .15 ? 0 : fraction === .25 ? .5 : 1;
      assert.ok(Math.abs(gated.negativeXWeight - legacy.negativeXWeight * confidence) < 1e-14);
      assert.ok(Math.abs(gated.positiveXWeight - legacy.positiveXWeight * confidence) < 1e-14);
    }
  }
  // Translation alone cannot grant relief to a frontal or pure-pitch head,
  // even when its camera bearing would otherwise count as lateral.
  for (const pitch of [-60, -45, 0, 45, 60]) {
    for (const x of [-20, 20]) {
      const offset = new Matrix4().makeRotationX(pitch * Math.PI / 180).setPosition(x, 0, -40).toArray();
      const legacy = createLegacyTempleVisibilityConfiguration(offset, 4);
      const gated = createTempleVisibilityConfiguration(offset, 4);
      assert.ok(Math.max(legacy.negativeXWeight, legacy.positiveXWeight) === 1);
      assert.equal(gated.negativeXWeight, 0); assert.equal(gated.positiveXWeight, 0);
    }
  }
  // Conversely, orientation alone is insufficient when the camera is directly
  // along canonical +Z: both independent indications must support relief.
  const turned = new Matrix4().makeRotationY(Math.PI / 4);
  const translation = new Vector3(0, 0, -40).applyMatrix4(turned);
  turned.setPosition(translation);
  assert.ok(Math.abs(turned.elements[8]!) > .35);
  const oppositeDisagreement = createTempleVisibilityConfiguration(turned.toArray(), 4);
  assert.ok(oppositeDisagreement.negativeXWeight < 1e-25 && oppositeDisagreement.positiveXWeight < 1e-25);
});

test('perspective depth placement agrees with independent Three camera projection in centimeters', () => {
  for (const [near, far] of [[1, 1000], [.1, 200]] as const) {
    const camera = new PerspectiveCamera(60, 1, near, far);
    for (const distance of [near, near + .02, 2, 50, 100]) {
      const sourceDepth = new Vector3(0, 0, -distance).project(camera).z * .5 + .5;
      const expected = new Vector3(0, 0, -Math.max(near, distance - .05)).project(camera).z * .5 + .5;
      assert.ok(Math.abs(templeLiftedDepth(Math.max(0, sourceDepth), near, far) - expected) < 2e-14);
    }
  }
  assert.throws(() => templeLiftedDepth(.5, 0, 100), /perspective depth/);
  assert.throws(() => templeLiftedDepth(1.1, 1, 100), /perspective depth/);
});

test('frontal v3 activation requires pitch and a lack of agreed lateral permission', () => {
  for (const x of [-20, 0, 20]) for (const direction of [-1, 1]) {
    for (const [pitch, expected] of [[0, 0], [8, 0], [18, 1], [60, 1]] as const) {
      const matrix = new Matrix4().makeRotationX(direction * pitch * Math.PI / 180).setPosition(x, 0, -50).toArray();
      const current = createTempleVisibilityConfiguration(matrix, 4);
      assert.ok(Math.abs(current.frontalOcclusionWeight - expected) < 1e-14);
      assert.equal(current.negativeXWeight, 0); assert.equal(current.positiveXWeight, 0);
      assert.equal(createViewTempleVisibilityConfiguration(matrix, 4).method, VIEW_TEMPLE_VISIBILITY_METHOD);
    }
  }
  for (const side of [-1, 1]) for (const pitch of [-.6, .6]) {
    const matrix = new Matrix4().makeRotationY(side * 1.0).multiply(new Matrix4().makeRotationX(pitch)).setPosition(0, 0, -50).toArray();
    const current = createTempleVisibilityConfiguration(matrix, 4), view = createViewTempleVisibilityConfiguration(matrix, 4);
    assert.equal(current.frontalOcclusionWeight, 0, 'unambiguous oblique views retain exact zero frontal effect even with pitch');
    assert.equal(current.negativeXWeight, view.negativeXWeight); assert.equal(current.positiveXWeight, view.positiveXWeight);
  }
  const middlePitch = Math.asin((Math.sin(8 * Math.PI / 180) + Math.sin(18 * Math.PI / 180)) / 2);
  const middle = new Matrix4().makeRotationX(middlePitch).setPosition(0, 0, -50).toArray();
  assert.ok(Math.abs(createTempleVisibilityConfiguration(middle, 0).frontalOcclusionWeight - .5) < 1e-14);
});

test('frontal v3 borrows the current camera independently of clipping and clears it on zero weight, reset and dispose', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  source.colorSpace = SRGBColorSpace; source.offset.set(.08, .12); source.repeat.set(.75, .8); source.rotation = .1;
  let cameraDisposals = 0; source.addEventListener('dispose', () => cameraDisposals++);
  t.after(() => source.dispose());
  const selected = {...createTempleVisibilityConfiguration(pose(.5), 4, 'angles'), frontalOcclusionWeight: .67};
  const current = {...selected};
  f.controller.set(selected); selected.frontalOcclusionWeight = .1;
  assert.deepEqual(f.controller.configuration, current);
  assert.equal(f.clip.configuration, null, 'new frontal policy does not require a new clip tuple');
  const original = compile(f.frame), overlay = compile(f.overlays[0]!.material as Material);
  assert.equal(original.uniforms.templeFrontalCameraSource, overlay.uniforms.templeFrontalCameraSource);
  assert.equal(original.uniforms.templeFrontalWeight!.value, .67);
  assert.throws(() => f.controller.prepare(pose(0)), /paired sRGB camera/);
  assert.equal(f.fake.state.renderCount, 0);
  f.controller.prepare(pose(0), source);
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, source);
  assert.deepEqual(original.uniforms.templeFrontalViewport!.value.toArray(), [640, 360]);
  assert.deepEqual(original.uniforms.templeFrontalUvTransform!.value.elements, source.matrix.elements);
  assert.notEqual(original.uniforms.templeFrontalUvTransform!.value, source.matrix, 'UV snapshot has separate ownership');
  assert.equal(original.uniforms.templeFrontalHeadMask!.value, overlay.uniforms.templeVisibilityHeadMask!.value, 'frontal occlusion samples the exact current paired head target');
  assert.equal(original.uniforms.templeFrontalWeight!.value, .67, 'prepare retains captured weight even on a different raw pose');
  for (const invalid of [-.1, 1.1, NaN, Infinity, undefined]) {
    assert.throws(() => f.controller.set({...current, frontalOcclusionWeight: invalid} as TempleVisibilityConfiguration), /configuration is invalid/);
    assert.deepEqual(f.controller.configuration, current);
    assert.equal(original.uniforms.templeFrontalCameraSource!.value, source, 'invalid metadata is atomic');
  }
  f.controller.set({...current, frontalOcclusionWeight: 0});
  assert.equal(original.uniforms.templeFrontalWeight!.value, 0);
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, null);
  assert.doesNotThrow(() => f.controller.prepare(pose(.2)));
  f.controller.set(current); f.controller.prepare(pose(.2), source); f.controller.set(null);
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, null); assert.equal(original.uniforms.templeFrontalWeight!.value, 0);
  f.controller.set(current); f.controller.prepare(pose(.2), source); f.controller.dispose();
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, null); assert.equal(original.uniforms.templeFrontalWeight!.value, 0);
  assert.equal(cameraDisposals, 0, 'the current camera belongs to the renderer');
});

test('frontal camera validation rejects stale or invalid bindings before drawing', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  t.after(() => source.dispose());
  const selected = {...createTempleVisibilityConfiguration(pose(.5), 4, 'angles'), frontalOcclusionWeight: .5};
  f.controller.set(selected);
  assert.throws(() => f.controller.prepare(pose(.5), source), /paired sRGB/);
  source.colorSpace = SRGBColorSpace;
  source.matrixAutoUpdate = false; source.matrix.elements[0] = NaN;
  assert.throws(() => f.controller.prepare(pose(.5), source), /UV transform/);
  source.matrix.identity(); source.image.width = 0;
  assert.throws(() => f.controller.prepare(pose(.5), source), /paired sRGB/);
  assert.equal(f.fake.state.renderCount, 0);
  assert.equal(compile(f.frame).uniforms.templeFrontalCameraSource!.value, null);
});

test('original hooks and dynamic keys are restored, with overlay coverage before frontal and endpoint RGB', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  f.controller.dispose();
  const clipHook = f.frame.onBeforeCompile;
  let upstreamKey = 'first';
  const key = () => upstreamKey;
  f.frame.customProgramCacheKey = key;
  const controller = createTempleVisibility(f.root, {renderer: f.fake.renderer, scene: f.scene, camera: f.camera, eyewearPose: f.eyewearPose});
  t.after(() => controller.dispose());
  const overlays = f.root.children.filter((child): child is Mesh => child instanceof Mesh && child.userData.templeVisibilityOverlay === true);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement); source.colorSpace = SRGBColorSpace;
  t.after(() => source.dispose());
  f.clip.set(createTempleBlendConfiguration(-.11)); f.clip.prepareRender(source, 640, 360);
  controller.set({...createTempleVisibilityConfiguration(pose(.5), 4, 'angles'), frontalOcclusionWeight: .5}); controller.prepare(pose(.5), source);
  const shader = compile(overlays[0]!.material as Material), original = compile(f.frame);
  const finalDepth = shader.fragmentShader.indexOf('gl_FragDepth = min(');
  const frontalRGB = shader.fragmentShader.indexOf('gl_FragColor.rgb = mix(gl_FragColor.rgb, templeFrontalCameraRGB');
  const endpointRGB = shader.fragmentShader.indexOf('gl_FragColor.rgb = mix(templeCameraRGB');
  assert.ok(finalDepth >= 0 && frontalRGB > finalDepth && endpointRGB > frontalRGB);
  const frontalToneGuard = shader.fragmentShader.lastIndexOf('#ifdef TONE_MAPPING', frontalRGB);
  const frontalToneEnd = shader.fragmentShader.indexOf('#endif', frontalRGB);
  assert.ok(frontalToneGuard > finalDepth && frontalToneEnd > frontalRGB && endpointRGB > frontalToneEnd,
    'only new frontal RGB is excluded from Three\'s NoToneMapping transmission program; the legacy endpoint path remains outside');
  assert.ok(!original.fragmentShader.includes('gl_FragDepth'), 'ordinary original depth remains unchanged');
  assert.equal(f.frame.depthWrite, true); assert.equal(f.frame.depthTest, true); assert.equal(f.frame.transparent, false);
  assert.equal(f.lens.onBeforeCompile, Material.prototype.onBeforeCompile, 'physical lens hooks remain untouched');
  assert.ok(f.frame.customProgramCacheKey().startsWith('first|'));
  upstreamKey = 'second'; assert.ok(f.frame.customProgramCacheKey().startsWith('second|'));
  assert.ok((overlays[0]!.material as Material).customProgramCacheKey().startsWith('second|'));
  controller.dispose();
  assert.equal(f.frame.onBeforeCompile, clipHook); assert.equal(f.frame.customProgramCacheKey, key);
  assert.equal(compile(f.frame).uniforms.templeFrontalWeight, undefined);
});

test('overlays share immutable geometry, own only cloned materials and write final coverage behind the clip discard', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  assert.equal(f.overlays.length, 2);
  assert.equal(f.overlays[0]!.material, f.overlays[1]!.material, 'shared frame material is cloned only once');
  const overlay = f.overlays[0]!, material = overlay.material as Material;
  assert.equal(overlay.geometry, f.geometry); assert.notEqual(material, f.frame);
  assert.equal((material as MeshStandardMaterial).map, f.texture);
  assert.equal(material.depthWrite, false); assert.equal(material.depthTest, true); assert.equal(material.transparent, false);
  assert.equal(overlay.renderOrder, 1); assert.equal(overlay.visible, false);
  const originalHook = f.frame.onBeforeCompile, sourceVersion = f.frame.version;
  const positions = f.geometry.getAttribute('position').array.slice(), normals = f.geometry.getAttribute('normal').array.slice();
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement); source.colorSpace = SRGBColorSpace;
  t.after(() => source.dispose());
  f.clip.set(createTempleBlendConfiguration(-.11)); f.clip.prepareRender(source, 640, 360);
  const clipPolicy = f.clip.configuration;
  f.controller.set(createTempleVisibilityConfiguration(pose(.2), 4, 'angles')); f.controller.prepare(pose(.2));
  const shader = compile(material), frameShader = compile(f.frame);
  assert.equal(shader.uniforms.templeClipEnabled, frameShader.uniforms.templeClipEnabled, 'overlay follows the exact clip uniform owner');
  assert.equal(shader.uniforms.templeVisibilityFront!.value, Math.fround(-.014));
  assert.equal(shader.uniforms.templeVisibilityHeadDepth!.value.isDepthTexture, true);
  const clipDiscard = shader.fragmentShader.indexOf('templeClipPositiveXCutoffZ)) discard');
  const finalCoverage = shader.fragmentShader.indexOf('gl_FragColor.a = templeOverlayCoverage');
  assert.ok(clipDiscard >= 0 && finalCoverage > clipDiscard && finalCoverage > shader.fragmentShader.indexOf('#include <dithering_fragment>'),
    'the overlay inherits the clip discard and writes its combined coverage after shading');
  // One program carries both rules; the mode uniform picks between them, so switching cannot recompile mid-session.
  assert.ok(shader.fragmentShader.includes('templeOverlayCoverage = templeOverlayGate * templeRelief * templeRootWeight;'));
  assert.ok(shader.fragmentShader.includes('templeVisibilityPosition.x < 0.0 ? templeVisibilityWeights.x : templeVisibilityWeights.y'),
    'the v3 per-side percentages are still the rule under ?temples=angles');
  assert.ok(shader.fragmentShader.includes('1.0 - smoothstep(templeVisibilityRelief.x, templeVisibilityRelief.y, templeBehindCm)'),
    'and v4 reads how far behind the head surface the fragment is');
  assert.ok(shader.fragmentShader.indexOf('float templeBehindCm') < shader.fragmentShader.indexOf('float templeRelief'),
    'the depth difference is computed before anything is given up');
  assert.equal(f.frame.onBeforeCompile, originalHook);
  assert.equal(f.frame.version, sourceVersion, 'selecting the clip and visibility settings does not recompile the source material');
  assert.deepEqual(f.clip.configuration, clipPolicy);
  assert.deepEqual(f.geometry.getAttribute('position').array, positions);
  assert.deepEqual(f.geometry.getAttribute('normal').array, normals);
  const versions = material.version;
  for (let i = 0; i < 4; i++) {
    f.controller.set(null); f.controller.set(createTempleVisibilityConfiguration(pose(.2), 4, 'angles')); f.controller.prepare(pose(.2));
  }
  assert.equal(material.version, versions, 'early reset/live state does not churn coverage programs');
  const resources = [f.geometry, f.lensGeometry, f.frame, f.lens, f.texture], counts = new Map<object, number>();
  for (const resource of [...resources, material]) {
    counts.set(resource, 0); resource.addEventListener('dispose', () => counts.set(resource, counts.get(resource)! + 1));
  }
  const target = shader.uniforms.templeVisibilityHeadDepth!.value as Texture;
  // Depth/target ownership is separately asserted by the prepare-state test.
  assert.ok(target.isTexture);
  f.controller.dispose(); f.controller.dispose();
  assert.equal(overlay.parent, null); assert.equal(counts.get(material), 1);
  for (const resource of resources) assert.equal(counts.get(resource), 0);
  assert.equal(f.controller.configuration, null);
  assert.throws(() => f.controller.set(null), /disposed/);
  assert.throws(() => f.controller.prepare(pose(0)), /disposed/);
});

test('metadata validation is atomic; disabled and unsupported modes cannot run or alter clipping', t => {
  const f = syntheticFixture(0); t.after(f.dispose);
  const input = {...createTempleVisibilityConfiguration(pose(.1), 0)};
  f.controller.set(input); const expected = {...input}; input.negativeXWeight = .1;
  assert.deepEqual(f.controller.configuration, expected);
  const output = f.controller.configuration!; Reflect.set(output, 'positiveXWeight', .9);
  assert.deepEqual(f.controller.configuration, expected);
  for (const invalid of [{...expected, negativeXWeight: -.001}, {...expected, positiveXWeight: 1.001},
    {...expected, negativeXWeight: NaN}, {...expected, coverage: 'automatic'}, {...expected, method: 'other'},
    {...expected, method: VIEW_TEMPLE_VISIBILITY_METHOD}, {...expected, method: LEGACY_TEMPLE_VISIBILITY_METHOD},
    createViewTempleVisibilityConfiguration(pose(.1), 0), createLegacyTempleVisibilityConfiguration(pose(.1), 0)]) {
    assert.throws(() => f.controller.set(invalid as TempleVisibilityConfiguration), /configuration is invalid/);
    assert.deepEqual(f.controller.configuration, expected);
  }
  assert.throws(() => f.controller.set({...expected, coverage: 'alpha-to-coverage'}), /requires multisampling/);
  assert.deepEqual(f.controller.configuration, expected);
  assert.doesNotThrow(() => validateTempleVisibility({...expected, negativeXWeight: 0, positiveXWeight: 1}));
  f.controller.set(null); f.controller.prepare([]);
  assert.equal(f.fake.state.renderCount, 0); assert.ok(f.overlays.every(overlay => !overlay.visible));
  assert.equal(f.clip.configuration, null);
});

test('settings from different poses share one mask shader and prepare keeps the set weights without recomputation', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const material = f.overlays[0]!.material as Material;
  const current = createTempleVisibilityConfiguration(pose(.5), 4);
  f.controller.set(current);
  const copy = f.controller.configuration!; Reflect.set(copy, 'negativeXWeight', .3);
  assert.deepEqual(f.controller.configuration, current);
  const originalPositions = f.geometry.getAttribute('position').array.slice();
  const depthFragments: string[] = [];
  f.fake.state.onRender = scene => { depthFragments.push((scene.overrideMaterial as ShaderMaterial).fragmentShader); };
  f.controller.prepare(pose(.5));
  const currentShader = compile(material), version = material.version;
  const other = createTempleVisibilityConfiguration(pose(-.3), 4);
  assert.notDeepEqual([other.negativeXWeight, other.positiveXWeight], [current.negativeXWeight, current.positiveXWeight]);
  f.controller.set(other); f.controller.prepare(pose(.5));
  assert.equal(material.version, version, 'a new setting does not alter the coverage program');
  const otherShader = compile(material);
  assert.equal(otherShader.fragmentShader, currentShader.fragmentShader);
  assert.equal(otherShader.vertexShader, currentShader.vertexShader);
  assert.equal(depthFragments.length, 2); assert.equal(depthFragments[0], depthFragments[1]);
  assert.deepEqual(otherShader.uniforms.templeVisibilityWeights!.value.toArray(), [other.negativeXWeight, other.positiveXWeight],
    'prepare does not recompute the set weights from a different raw pose');
  assert.deepEqual(f.controller.configuration, other);
  assert.deepEqual(f.geometry.getAttribute('position').array, originalPositions);
  f.controller.set(current); f.controller.set(null);
  assert.deepEqual(currentShader.uniforms.templeVisibilityWeights!.value.toArray(), [0, 0]);
  f.controller.set(current); f.controller.dispose();
  assert.deepEqual(currentShader.uniforms.templeVisibilityWeights!.value.toArray(), [0, 0]);
});

test('internal transmission is excluded while native and explicit role targets keep overlay color', t => {
  let sceneHookCalls = 0;
  const prior: Scene['onBeforeRender'] = function(this: Scene, _renderer, scene) { assert.equal(this, scene); sceneHookCalls++; };
  const f = syntheticFixture(4, prior); t.after(f.dispose);
  const overlay = f.overlays[0]!, material = overlay.material as Material;
  const roleTarget = new WebGLRenderTarget(8, 8), transmissionTarget = new WebGLRenderTarget(8, 8);
  t.after(() => { roleTarget.dispose(); transmissionTarget.dispose(); });
  const beforeDraw = () => Reflect.apply(overlay.onBeforeRender, overlay,
    [f.fake.renderer, f.scene, f.camera, overlay.geometry, material, null]);
  const afterDraw = () => Reflect.apply(overlay.onAfterRender, overlay,
    [f.fake.renderer, f.scene, f.camera, overlay.geometry, material, null]);
  for (const output of [null, roleTarget]) {
    f.fake.state.target = output;
    Reflect.apply(f.scene.onBeforeRender, f.scene, [f.fake.renderer, f.scene, f.camera, output]);
    f.fake.state.target = transmissionTarget; beforeDraw();
    assert.equal(material.colorWrite, false, 'internal transmission has changed target without a new scene entry');
    assert.equal(material.depthWrite, false);
    afterDraw(); assert.equal(material.colorWrite, true);
    f.fake.state.target = output; beforeDraw();
    assert.equal(material.colorWrite, true, 'the explicit output may be the native framebuffer or a role render target');
    afterDraw(); assert.equal(material.colorWrite, true);
  }
  assert.equal(sceneHookCalls, 2, 'the prior scene callback is retained');
  f.fake.state.target = transmissionTarget; beforeDraw();
  assert.equal(material.colorWrite, false);
  // A thrown draw can skip onAfterRender; normal reset restores the owned state.
  f.controller.set(null); assert.equal(material.colorWrite, true);
  f.controller.dispose();
  assert.equal(f.scene.onBeforeRender, prior);
  assert.equal(f.frame.colorWrite, true);
});

test('head pass clears its mask and restores all renderer/scene state even when rendering throws', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const previousTarget = new WebGLRenderTarget(4, 4); t.after(() => previousTarget.dispose());
  const previousOverride = new MeshStandardMaterial(); t.after(() => previousOverride.dispose());
  const {state, backend} = f.fake;
  state.target = previousTarget; state.face = 3; state.mip = 2;
  f.scene.overrideMaterial = previousOverride;
  const saved = {
    background: f.scene.background, viewport: state.viewport.clone(), scissor: state.scissor.clone(),
    color: state.color.clone(), alpha: state.alpha, autoClear: backend.autoClear,
  };
  let target: WebGLRenderTarget | null = null, depth: ShaderMaterial | null = null;
  state.onRender = (scene, camera) => {
    assert.equal(scene, f.scene); assert.equal(camera, f.camera);
    assert.equal(f.eyewearPose.visible, false); assert.equal(scene.background, null);
    assert.notEqual(scene.overrideMaterial, previousOverride); assert.equal(backend.autoClear, false);
    assert.equal(state.scissorTest, false); assert.deepEqual(state.viewport.toArray(), [0, 0, 640, 360]);
    target = state.target; depth = scene.overrideMaterial as ShaderMaterial;
    assert.equal(target!.width, 640); assert.equal(target!.height, 360);
    const inverse = depth.uniforms.headInverse!.value as Matrix4;
    assert.ok(new Matrix4().multiplyMatrices(inverse, new Matrix4().fromArray(pose(.3))).elements
      .every((value, index) => Math.abs(value - (index % 5 === 0 ? 1 : 0)) < 1e-12));
    throw new Error('simulated depth-pass failure');
  };
  f.controller.set(createTempleVisibilityConfiguration(pose(.3), 4));
  assert.throws(() => f.controller.prepare(pose(.3)), /simulated depth-pass failure/);
  assert.equal(f.eyewearPose.visible, true); assert.equal(f.scene.background, saved.background);
  assert.equal(f.scene.overrideMaterial, previousOverride);
  assert.equal(state.target, previousTarget); assert.equal(state.face, 3); assert.equal(state.mip, 2);
  assert.deepEqual(state.viewport, saved.viewport); assert.deepEqual(state.scissor, saved.scissor); assert.equal(state.scissorTest, true);
  assert.deepEqual(state.color, saved.color); assert.equal(state.alpha, saved.alpha); assert.equal(backend.autoClear, saved.autoClear);
  assert.equal(state.clearCount, 1);
  let targetDisposals = 0, depthDisposals = 0;
  (target as unknown as WebGLRenderTarget).addEventListener('dispose', () => targetDisposals++);
  (depth as unknown as ShaderMaterial).addEventListener('dispose', () => depthDisposals++);
  state.onRender = () => {};
  f.controller.set(null); f.controller.prepare(pose(.3)); assert.equal(state.renderCount, 1);
  f.controller.set(createTempleVisibilityConfiguration(pose(-.3), 4)); f.controller.prepare(pose(-.3));
  assert.equal(state.renderCount, 2);
  f.controller.dispose(); f.controller.dispose();
  assert.equal(targetDisposals, 1); assert.equal(depthDisposals, 1);
});

test('partial construction failure removes new overlays and disposes only cloned materials', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  f.controller.dispose();
  const children = f.root.children.slice(), sceneHook = f.scene.onBeforeRender;
  const sourceHook = f.frame.onBeforeCompile, sourceKey = f.frame.customProgramCacheKey;
  const originalClone = f.frame.clone.bind(f.frame);
  let cloneDisposals = 0, sourceDisposals = 0;
  f.frame.addEventListener('dispose', () => sourceDisposals++);
  t.mock.method(f.frame, 'clone', () => {
    const clone = originalClone(); clone.addEventListener('dispose', () => cloneDisposals++); return clone;
  });
  t.mock.method(children[1]! as Mesh, 'clone', () => { throw new Error('simulated partial clone failure'); });
  assert.throws(() => createTempleVisibility(f.root, {
    renderer: f.fake.renderer, scene: f.scene, camera: f.camera, eyewearPose: f.eyewearPose,
  }), /simulated partial clone failure/);
  assert.deepEqual(f.root.children, children);
  assert.equal(f.scene.onBeforeRender, sceneHook);
  assert.equal(f.frame.onBeforeCompile, sourceHook); assert.equal(f.frame.customProgramCacheKey, sourceKey);
  assert.equal(cloneDisposals, 1); assert.equal(sourceDisposals, 0);
});

for (const definition of Object.values(EYEWEAR)) test(`${definition.name}: overlays retain actual buffers and exclude physical lens materials`, async t => {
  const bytes = await readFile(new URL('../public' + definition.assetUrl, import.meta.url));
  const gltf = await new GLTFLoader().register(() => ({name: 'NodeVisibilityImages', loadTexture: async () => new Texture()}))
    .parseAsync(new Uint8Array(bytes).buffer, '/models/');
  const originals: Mesh[] = [], resources = new Set<BufferGeometry | Material | Texture>();
  gltf.scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    originals.push(object); resources.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      resources.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) resources.add(value);
    }
  });
  const saved = originals.map(mesh => ({mesh, geometry: mesh.geometry, material: mesh.material,
    positions: mesh.geometry.getAttribute('position').array.slice(), normals: mesh.geometry.getAttribute('normal').array.slice(),
    index: mesh.geometry.getIndex()!.array.slice()}));
  const fake = fakeBackend(), scene = new Scene(), eyewearPose = new Group().add(gltf.scene), camera = new PerspectiveCamera(60, 1, 1, 1000);
  scene.add(eyewearPose);
  const clip = createTempleClip(gltf.scene);
  const controller = createTempleVisibility(gltf.scene, {renderer: fake.renderer, scene, camera, eyewearPose});
  t.after(() => { controller.dispose(); clip.dispose(); for (const resource of resources) resource.dispose(); });
  controller.set(createTempleVisibilityConfiguration(pose(.5), 4)); controller.prepare(pose(.5));
  const overlays: Mesh[] = [];
  gltf.scene.traverse(object => { if (object instanceof Mesh && object.userData.templeVisibilityOverlay === true) overlays.push(object); });
  assert.ok(overlays.length > 0);
  for (const value of saved) {
    assert.equal(value.mesh.geometry, value.geometry); assert.equal(value.mesh.material, value.material);
    assert.deepEqual(value.geometry.getAttribute('position').array, value.positions);
    assert.deepEqual(value.geometry.getAttribute('normal').array, value.normals);
    assert.deepEqual(value.geometry.getIndex()!.array, value.index);
    const lens = value.material instanceof MeshPhysicalMaterial && value.material.transmission > 0;
    assert.equal(overlays.some(overlay => overlay.geometry === value.geometry), !lens);
  }
  for (const overlay of overlays) {
    const shader = compile(overlay.material as Material);
    const physicalRear = Math.min(...originals.filter(mesh => mesh.material instanceof MeshPhysicalMaterial && mesh.material.transmission > 0)
      .flatMap(mesh => Array.from({length: mesh.geometry.getAttribute('position').count}, (_, i) => mesh.geometry.getAttribute('position').getZ(i))));
    assert.equal(shader.uniforms.templeVisibilityFront!.value, physicalRear);
  }
});
