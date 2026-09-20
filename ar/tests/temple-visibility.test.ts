import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {
  BufferAttribute, BufferGeometry, CanvasTexture, Color, DoubleSide, Group, Material, Matrix4, Mesh, MeshBasicMaterial, MeshPhysicalMaterial,
  Euler, MeshStandardMaterial, PerspectiveCamera, Quaternion, Scene, ShaderLib, Texture, UniformsUtils,
  SRGBColorSpace, Vector2, Vector3, Vector4, WebGLRenderTarget,
} from 'three';
import type {ShaderMaterial, WebGLRenderer} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {EYEWEAR} from '../src/eyewear/catalog.ts';
import {createTempleBlendConfiguration, createTempleClip} from '../src/render/temple-clip.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {
  createTempleVisibility, createTempleVisibilityConfiguration, depthRelief, templeLiftedDepth,
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
  const observedFaceSurface = new BufferGeometry().setAttribute('position', new BufferAttribute(new Float32Array([
    -10, -10, -50, 10, -10, -50, 0, 10, -50,
  ]), 3));
  scene.add(eyewearPose); scene.background = new Color(0xabcdef);
  if (beforeRender) scene.onBeforeRender = beforeRender;
  const fake = fakeBackend(samples), clip = createTempleClip(root);
  const controller = createTempleVisibility(root, {renderer: fake.renderer, scene, camera, eyewearPose, observedFaceSurface});
  const overlays = root.children.filter((child): child is Mesh => child instanceof Mesh && child.userData.templeVisibilityOverlay === true);
  return {geometry, lensGeometry, observedFaceSurface, frame, lens, texture, root, scene, eyewearPose, camera, fake, clip, controller, overlays,
    dispose: () => { controller.dispose(); clip.dispose(); geometry.dispose(); lensGeometry.dispose(); observedFaceSurface.dispose(); frame.dispose(); lens.dispose(); texture.dispose(); }};
}

/** The head at `yaw`, tilted sideways by `roll` (lying down), 50 cm away. */
const tilted = (yaw: number, roll: number, pitch = 0) => new Matrix4().compose(new Vector3(0, 0, -50),
  new Quaternion().setFromEuler(new Euler(pitch, yaw, roll, 'YXZ')), new Vector3(1, 1, 1)).toArray();

function cheekContact() {
  return {polygon: [{x: .2, y: .3}, {x: .8, y: .3}, {x: .75, y: .8}, {x: .25, y: .8}]};
}

test('observed cheek contact shares current-frame inputs across native shafts and overlays without enabling global hiding', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const first = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  const second = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  first.colorSpace = second.colorSpace = SRGBColorSpace;
  second.offset.set(.1, .2); second.repeat.set(.8, .7);
  t.after(() => {first.dispose(); second.dispose();});
  const positions = Array.from(f.geometry.getAttribute('position').array);
  const selected: TempleVisibilityConfiguration = {...createTempleVisibilityConfiguration(4),
    cheekContact: cheekContact(), cheekTransitionPx: 2, excludeArmsFromLensInput: true,
    terminalReturn: {startZM: -.070, endZM: -.075}};
  f.controller.set(selected);
  const original = compile(f.frame), overlay = compile(f.overlays[0]!.material as Material);
  assert.equal(original.uniforms.templeFrontalWeight, undefined);
  for (const name of ['templeCheekCount', 'templeCheekPolygon', 'templeCheekDepth', 'templeCheekMask', 'templeCheekNearFar',
    'templeCheekTransitionPx', 'templeFrontalCameraSource'])
    assert.equal(original.uniforms[name], overlay.uniforms[name], name + ' has one owner for originals and overlays');
  assert.equal(original.uniforms.templeCheekCount!.value, 4);
  assert.equal(original.uniforms.templeCheekTransitionPx!.value, 2);
  assert.equal(original.uniforms.templeCheekPolygon!.value.length, 64);
  assert.deepEqual(original.uniforms.templeCheekPolygon!.value.slice(0, 4).map((p: Vector2) => p.toArray()),
    selected.cheekContact!.polygon.map(p => [p.x, p.y]));
  assert.equal(original.uniforms.templeExcludeArmsFromLensInput!.value, 1);
  assert.equal(overlay.uniforms.templeVisibilityDepthMode, undefined);
  assert.deepEqual(overlay.uniforms.templeVisibilityTerminal!.value.toArray(), [-.070, -.075]);
  assert.equal(f.frame.depthWrite, true); assert.equal(f.frame.depthTest, true);
  assert.equal((f.overlays[0]!.material as Material).depthWrite, false);
  assert.equal(compile(f.lens).uniforms.templeCheekCount, undefined, 'physical lenses are not wrapped by cheek masking');
  assert.throws(() => f.controller.prepare(pose(0)), /paired sRGB camera/);
  assert.equal(f.fake.state.renderCount, 0);
  f.controller.prepare(pose(0), first);
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, first);
  f.controller.prepare(pose(0), second);
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, second, 'the next draw uses its own camera frame');
  assert.deepEqual(original.uniforms.templeFrontalUvTransform!.value.elements, second.matrix.elements);
  assert.deepEqual(Array.from(f.geometry.getAttribute('position').array), positions);
  assert.equal(f.clip.configuration, null, 'contact does not replace the independent endpoint policy');
  f.controller.set({...selected, cheekContact: null});
  assert.equal(original.uniforms.templeCheekCount!.value, 0);
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, null);
  assert.equal(original.uniforms.templeFrontalWeight, undefined);
  assert.doesNotThrow(() => f.controller.prepare(pose(0)));
  assert.equal(original.uniforms.templeExcludeArmsFromLensInput!.value, 1);
  f.controller.set(selected); f.controller.prepare(pose(0), first); f.controller.set(null);
  assert.equal(original.uniforms.templeCheekCount!.value, 0);
  assert.equal(original.uniforms.templeCheekTransitionPx!.value, 0);
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, null);
  f.controller.set(selected); f.controller.prepare(pose(0), first); f.controller.dispose();
  assert.equal(original.uniforms.templeCheekCount!.value, 0);
  assert.equal(original.uniforms.templeCheekTransitionPx!.value, 0);
  assert.equal(original.uniforms.templeFrontalCameraSource!.value, null);
});

test('cheek transition bounds validate atomically and an omitted setting clears the previous radius', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  source.colorSpace = SRGBColorSpace; t.after(() => source.dispose());
  const baseline: TempleVisibilityConfiguration = {...createTempleVisibilityConfiguration(4),
    cheekContact: cheekContact(), excludeArmsFromLensInput: true};
  const selected = {...baseline, cheekTransitionPx: 2};
  f.controller.set(selected); f.controller.prepare(pose(0), source);
  const shader = compile(f.frame), overlay = compile(f.overlays[0]!.material as Material);
  const version = f.frame.version;
  assert.equal(shader.uniforms.templeCheekTransitionPx, overlay.uniforms.templeCheekTransitionPx);
  for (const cheekTransitionPx of [-.001, 3.001, NaN, Infinity, -Infinity]) {
    assert.throws(() => f.controller.set({...selected, cheekTransitionPx}), /cheek transition is invalid/);
    assert.deepEqual(f.controller.configuration, selected);
    assert.equal(shader.uniforms.templeCheekTransitionPx!.value, 2);
    assert.equal(shader.uniforms.templeCheekCount!.value, 4);
    assert.equal(shader.uniforms.templeFrontalCameraSource!.value, source, 'invalid input cannot unbind this frame');
    assert.equal(shader.uniforms.templeExcludeArmsFromLensInput!.value, 1);
  }
  for (const cheekTransitionPx of [0, .25, 3]) {
    f.controller.set({...baseline, cheekTransitionPx});
    assert.equal(shader.uniforms.templeCheekTransitionPx!.value, cheekTransitionPx);
    assert.equal(overlay.uniforms.templeCheekTransitionPx!.value, cheekTransitionPx);
  }
  f.controller.set(baseline);
  assert.equal(shader.uniforms.templeCheekTransitionPx!.value, 0, 'an omitted field restores the pre-feather policy');
  assert.equal(shader.uniforms.templeCheekCount!.value, 4, 'resetting the radius does not remove real face contact');
  assert.equal(shader.uniforms.templeExcludeArmsFromLensInput!.value, 1);
  assert.equal(compile(f.lens).uniforms.templeCheekTransitionPx, undefined);
  assert.equal(f.frame.version, version, 'radius changes do not recompile the material');
});

test('cheek transition remains inward-only and preserves the physical onset with bounded depth softening', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  f.controller.set({...createTempleVisibilityConfiguration(4),
    cheekContact: cheekContact(), cheekTransitionPx: 2});
  for (const material of [f.frame, f.overlays[0]!.material as Material]) {
    const shader = compile(material).fragmentShader;
    assert.match(shader, /return inside \? smoothstep\(0\.0, max\(1\.25, templeCheekTransitionPx\), sqrt\(distanceSquared\)\) : 0\.0;/,
      'the polygon transition cannot spread onto an exposed shaft outside the observed face');
    const defaultBand = /float cheekBandCm = ([\d.]+);/.exec(shader);
    assert.ok(defaultBand); assert.ok(Math.abs(Number(defaultBand[1]) - .2) < 1e-12, 'zero radius retains the original 1–3 mm depth transition');
    assert.match(shader, /if \(templeCheekTransitionPx > 0\.0\) cheekBandCm = clamp\(\s*fwidth\(cheekBehindCm\) \* templeCheekTransitionPx, cheekBandCm, 0\.5\);/,
      'nonzero radius can broaden the transition but its width is capped at 5 mm');
    assert.match(shader, /smoothstep\(\s*0\.1, 0\.1 \+ cheekBandCm, cheekBehindCm\)/,
      'the onset stays 1 mm behind observed skin, with full suppression no later than 6 mm');
    assert.match(shader, /cheekBehindCm = cheekFaceZ - cheekArmZ;/);
    assert.match(shader, /templeFrontalOriginalPosition\.z < templeCheekFront\) \{/,
      'the optical-front guard applies before any softened coverage');
    const inputStart = shader.indexOf('if (templeExcludeArmsFromLensInput'), inputEnd = shader.indexOf('discard;', inputStart);
    assert.ok(inputStart >= 0 && inputEnd > inputStart);
    assert.doesNotMatch(shader.slice(inputStart, inputEnd),
      /templeCheekTransitionPx/, 'the cheek radius does not alter the physical lens-input exclusion');
  }
});

test('observed cheek configuration owns its polygon and validates replacements atomically', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const contact = cheekContact();
  const selected: TempleVisibilityConfiguration = {...createTempleVisibilityConfiguration(4), cheekContact: contact};
  const expected = structuredClone(selected);
  f.controller.set(selected);
  const shader = compile(f.frame);
  contact.polygon[0]!.x = .99;
  assert.deepEqual(f.controller.configuration, expected, 'input polygon metadata is copied');
  const returned = f.controller.configuration!;
  (returned.cheekContact!.polygon[0] as {x: number}).x = -.5;
  assert.deepEqual(f.controller.configuration, expected, 'the getter also returns independent polygon points');
  assert.equal(shader.uniforms.templeCheekPolygon!.value[0].x, .2);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  source.colorSpace = SRGBColorSpace; t.after(() => source.dispose());
  f.controller.prepare(pose(0), source);
  const invalid = [
    {...cheekContact(), polygon: [{x: .2, y: .2}, {x: .8, y: .8}]},
    {...cheekContact(), polygon: Array.from({length: 65}, () => ({x: .4, y: .4}))},
    {...cheekContact(), polygon: [{x: NaN, y: .3}, {x: .8, y: .3}, {x: .5, y: .8}]},
    {...cheekContact(), polygon: [{x: .2, y: Infinity}, {x: .8, y: .3}, {x: .5, y: .8}]},
  ];
  for (const cheekContact of invalid) {
    assert.throws(() => f.controller.set({...expected, cheekContact}), /observed cheek contact is invalid/);
    assert.deepEqual(f.controller.configuration, expected);
    assert.equal(shader.uniforms.templeCheekCount!.value, 4);
    assert.equal(shader.uniforms.templeFrontalCameraSource!.value, source, 'rejected metadata cannot unbind the paired frame');
  }
  const triangle = {polygon: [{x: .1, y: .2}, {x: .9, y: .2}, {x: .5, y: .9}]};
  f.controller.set({...expected, cheekContact: triangle});
  assert.equal(shader.uniforms.templeCheekCount!.value, 3, 'shorter outlines do not retain old active vertices');
  assert.deepEqual(shader.uniforms.templeCheekPolygon!.value.slice(0, 3).map((p: Vector2) => p.toArray()), triangle.polygon.map(p => [p.x, p.y]));
  assert.equal(shader.uniforms.templeFrontalCameraSource!.value, null, 'a changed contact must prepare its paired camera again');
});

test('clearing cheek contact removes its paired pass while preserving current head depth', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  source.colorSpace = SRGBColorSpace; t.after(() => source.dispose());
  const configuration: TempleVisibilityConfiguration = {...createTempleVisibilityConfiguration(4),
    cheekContact: cheekContact(), excludeArmsFromLensInput: true};
  f.controller.set(configuration);
  assert.throws(() => f.controller.prepare(pose(0)), /paired sRGB camera/);
  f.controller.prepare(pose(0), source);
  const shader = compile(f.frame);
  assert.equal(f.fake.state.renderCount, 2, 'current head and observed face each render once');
  assert.equal(shader.uniforms.templeFrontalCameraSource!.value, source);
  assert.equal(shader.uniforms.templeCheekCount!.value, 4);
  assert.equal(shader.uniforms.templeExcludeArmsFromLensInput!.value, 1);
  f.controller.set({...configuration, cheekContact: null});
  f.controller.prepare(pose(0));
  assert.equal(f.fake.state.renderCount, 3, 'head depth is still current without observed cheek eligibility');
  assert.equal(shader.uniforms.templeFrontalCameraSource!.value, null);
  assert.equal(shader.uniforms.templeCheekCount!.value, 0);
});

test('cheek depth renders only the current observed face and restores state if that pass fails', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  source.colorSpace = SRGBColorSpace; t.after(() => source.dispose());
  f.controller.set({...createTempleVisibilityConfiguration(4), cheekContact: cheekContact()});
  const shader = compile(f.frame);
  let headTarget: WebGLRenderTarget | null = null, observedTarget: WebGLRenderTarget | null = null;
  let observedMaterial: MeshBasicMaterial | null = null, observedPasses = 0;
  let expectedZ = -50, failObserved = false;
  f.fake.state.onRender = (scene, camera) => {
    assert.equal(camera, f.camera);
    if (scene === f.scene) {headTarget = f.fake.state.target; return;}
    observedPasses++; observedTarget = f.fake.state.target;
    assert.notEqual(observedTarget, headTarget, 'observed face depth cannot be polluted by canonical shell depth');
    assert.equal(scene.children.length, 1, 'neither eyewear nor canonical head geometry enters this pass');
    const mesh = scene.children[0] as Mesh;
    assert.equal(mesh.geometry, f.observedFaceSurface);
    assert.equal(mesh.geometry.getAttribute('position').getZ(0), expectedZ, 'the pass reads this frame\'s updated buffer');
    assert.equal(mesh.frustumCulled, false);
    assert.ok(mesh.matrix.equals(new Matrix4()), 'observed positions are already in camera space');
    assert.ok(mesh.material instanceof MeshBasicMaterial);
    observedMaterial = mesh.material;
    assert.equal(observedMaterial.side, DoubleSide);
    assert.equal(observedMaterial.depthTest, true); assert.equal(observedMaterial.depthWrite, true);
    assert.equal(observedMaterial.color.getHex(), 0xffffff);
    assert.equal(scene.background, null); assert.equal(scene.overrideMaterial, null);
    assert.equal(shader.uniforms.templeCheekDepth!.value, observedTarget!.depthTexture);
    assert.equal(shader.uniforms.templeCheekMask!.value, observedTarget!.texture);
    assert.equal(observedTarget!.width, 640); assert.equal(observedTarget!.height, 360);
    if (failObserved) throw new Error('observed depth failure');
  };
  f.controller.prepare(pose(0), source);
  assert.equal(observedPasses, 1); assert.equal(f.fake.state.clearCount, 2);
  expectedZ = -48; f.observedFaceSurface.getAttribute('position').setZ(0, expectedZ);
  f.camera.near = .5; f.camera.far = 500; f.camera.updateProjectionMatrix();
  f.controller.prepare(pose(.2), source);
  assert.equal(observedPasses, 2);
  assert.deepEqual(shader.uniforms.templeCheekNearFar!.value.toArray(), [.5, 500]);
  const targetBefore = new WebGLRenderTarget(8, 8); t.after(() => targetBefore.dispose());
  f.fake.state.target = targetBefore; failObserved = true;
  const viewport = f.fake.state.viewport.clone(), scissor = f.fake.state.scissor.clone(), background = f.scene.background;
  assert.throws(() => f.controller.prepare(pose(0), source), /observed depth failure/);
  assert.equal(f.fake.state.target, targetBefore); assert.equal(f.scene.background, background);
  assert.equal(f.eyewearPose.visible, true); assert.equal(f.fake.backend.autoClear, true);
  assert.deepEqual(f.fake.state.viewport, viewport); assert.deepEqual(f.fake.state.scissor, scissor);
  let targetDisposals = 0, materialDisposals = 0, surfaceDisposals = 0;
  (observedTarget as unknown as WebGLRenderTarget).addEventListener('dispose', () => targetDisposals++);
  (observedMaterial as unknown as Material).addEventListener('dispose', () => materialDisposals++);
  f.observedFaceSurface.addEventListener('dispose', () => surfaceDisposals++);
  f.controller.dispose(); f.controller.dispose();
  assert.equal(targetDisposals, 1); assert.equal(materialDisposals, 1);
  assert.equal(surfaceDisposals, 0, 'the caller owns the live observed geometry');
});

test('contact cannot activate without an observed face depth source', t => {
  const f = syntheticFixture(); t.after(f.dispose); f.controller.dispose();
  const controller = createTempleVisibility(f.root, {renderer: f.fake.renderer, scene: f.scene,
    camera: f.camera, eyewearPose: f.eyewearPose});
  t.after(() => controller.dispose());
  const baseline = createTempleVisibilityConfiguration(4);
  controller.set(baseline);
  assert.throws(() => controller.set({...baseline, cheekContact: cheekContact()}), /current face surface/);
  assert.deepEqual(controller.configuration, baseline);
  assert.equal(compile(f.frame).uniforms.templeCheekCount!.value, 0);
});

test('depth relief is bounded and decreases with distance behind the head surface', () => {
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
  // Shallow proxy intersections retain the arm; the band ends before a deeply buried arm can reappear.
  assert.ok(TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm >= 0
    && TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm <= 0.6);
  assert.ok(TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm > TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm
    && TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm <= 2.6);
  assert.equal(depthRelief(2), 0, 'an arm 2 cm behind the head is round the back of it and is given up');
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

test('observed cheek camera validation rejects stale or invalid bindings before drawing', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement);
  t.after(() => source.dispose());
  const selected = {...createTempleVisibilityConfiguration(4), cheekContact: cheekContact()};
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

test('original hooks and dynamic keys are restored, with overlay coverage before cheek and endpoint RGB', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  f.controller.dispose();
  const clipHook = f.frame.onBeforeCompile;
  let upstreamKey = 'first';
  const key = () => upstreamKey;
  f.frame.customProgramCacheKey = key;
  const controller = createTempleVisibility(f.root, {renderer: f.fake.renderer, scene: f.scene, camera: f.camera, eyewearPose: f.eyewearPose, observedFaceSurface: f.observedFaceSurface});
  t.after(() => controller.dispose());
  const overlays = f.root.children.filter((child): child is Mesh => child instanceof Mesh && child.userData.templeVisibilityOverlay === true);
  const source = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement); source.colorSpace = SRGBColorSpace;
  t.after(() => source.dispose());
  f.clip.set(createTempleBlendConfiguration(-.11)); f.clip.prepareRender(source, 640, 360);
  controller.set({...createTempleVisibilityConfiguration(4), cheekContact: cheekContact()}); controller.prepare(pose(.5), source);
  const shader = compile(overlays[0]!.material as Material), original = compile(f.frame);
  const finalDepth = shader.fragmentShader.indexOf('gl_FragDepth = min(');
  const frontalRGB = shader.fragmentShader.indexOf('gl_FragColor.rgb = mix(gl_FragColor.rgb, cheekCameraRGB');
  const endpointRGB = shader.fragmentShader.indexOf('gl_FragColor.rgb = mix(templeCameraRGB');
  assert.ok(finalDepth >= 0 && frontalRGB > finalDepth && endpointRGB > frontalRGB);
  const frontalToneGuard = shader.fragmentShader.lastIndexOf('#ifdef TONE_MAPPING', frontalRGB);
  const frontalToneEnd = shader.fragmentShader.indexOf('#endif', frontalRGB);
  assert.ok(frontalToneGuard > finalDepth && frontalToneEnd > frontalRGB && endpointRGB > frontalToneEnd,
    'cheek RGB is excluded from Three\'s NoToneMapping transmission program; the endpoint path remains outside');
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
  f.controller.set(createTempleVisibilityConfiguration(4)); f.controller.prepare(pose(.2));
  const shader = compile(material), frameShader = compile(f.frame);
  assert.equal(shader.uniforms.templeClipEnabled, frameShader.uniforms.templeClipEnabled, 'overlay follows the exact clip uniform owner');
  assert.equal(shader.uniforms.templeVisibilityFront!.value, Math.fround(-.014));
  assert.equal(shader.uniforms.templeVisibilityHeadDepth!.value.isDepthTexture, true);
  const clipDiscard = shader.fragmentShader.indexOf('templeClipPositiveXCutoffZ)) discard');
  const finalCoverage = shader.fragmentShader.indexOf('gl_FragColor.a = templeOverlayCoverage');
  assert.ok(clipDiscard >= 0 && finalCoverage > clipDiscard && finalCoverage > shader.fragmentShader.indexOf('#include <dithering_fragment>'),
    'the overlay inherits the clip discard and writes its combined coverage after shading');
  assert.ok(shader.fragmentShader.includes('templeOverlayCoverage = templeHeadPresent * templeRelief * templeRootWeight;'));
  assert.ok(shader.fragmentShader.includes('1.0 - smoothstep(templeVisibilityRelief.x, templeVisibilityRelief.y, templeBehindCm)'),
    'relief reads how far behind the head surface the fragment is');
  assert.ok(shader.fragmentShader.indexOf('float templeBehindCm') < shader.fragmentShader.indexOf('float templeRelief'),
    'the depth difference is computed before anything is given up');
  assert.equal(f.frame.onBeforeCompile, originalHook);
  assert.equal(f.frame.version, sourceVersion, 'selecting the clip and visibility settings does not recompile the source material');
  assert.deepEqual(f.clip.configuration, clipPolicy);
  assert.deepEqual(f.geometry.getAttribute('position').array, positions);
  assert.deepEqual(f.geometry.getAttribute('normal').array, normals);
  const versions = material.version;
  for (let i = 0; i < 4; i++) {
    f.controller.set(null); f.controller.set(createTempleVisibilityConfiguration(4)); f.controller.prepare(pose(.2));
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

test('metadata validation is atomic; disabled or unsupported coverage cannot run or alter clipping', t => {
  const f = syntheticFixture(0); t.after(f.dispose);
  const input = {...createTempleVisibilityConfiguration(0)};
  f.controller.set(input); const expected = {...input}; input.reliefKeepCm = .1;
  assert.deepEqual(f.controller.configuration, expected);
  const output = f.controller.configuration!; Reflect.set(output, 'reliefDropCm', .9);
  assert.deepEqual(f.controller.configuration, expected);
  for (const invalid of [{...expected, reliefKeepCm: -.001}, {...expected, reliefDropCm: 13},
    {...expected, reliefKeepCm: NaN}, {...expected, reliefDropCm: expected.reliefKeepCm},
    {...expected, coverage: 'automatic'}, {...expected, method: 'other'},
    ...['temple-side-depth-v1', 'temple-side-depth-v2', 'temple-side-depth-v3'].map(method => ({...expected, method}))]) {
    assert.throws(() => f.controller.set(invalid as TempleVisibilityConfiguration), /configuration is invalid/);
    assert.deepEqual(f.controller.configuration, expected);
  }
  assert.throws(() => f.controller.set({...expected, coverage: 'alpha-to-coverage'}), /requires multisampling/);
  assert.deepEqual(f.controller.configuration, expected);
  assert.doesNotThrow(() => validateTempleVisibility(expected));
  f.controller.set(null); f.controller.prepare([]);
  assert.equal(f.fake.state.renderCount, 0); assert.ok(f.overlays.every(overlay => !overlay.visible));
  assert.equal(f.clip.configuration, null);
});

test('every pose refreshes depth with one program and preserves the recorded relief band', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const material = f.overlays[0]!.material as Material;
  const current = createTempleVisibilityConfiguration(4);
  assert.equal(current.method, TEMPLE_VISIBILITY_METHOD);
  assert.equal(TEMPLE_VISIBILITY_METHOD, 'temple-behind-head-v4');
  assert.deepEqual(Object.keys(current).sort(), ['coverage', 'method', 'reliefDropCm', 'reliefKeepCm']);
  f.controller.set(current);
  const originalPositions = f.geometry.getAttribute('position').array.slice();
  const depthFragments: string[] = [];
  f.fake.state.onRender = scene => {depthFragments.push((scene.overrideMaterial as ShaderMaterial).fragmentShader);};
  f.controller.prepare(pose(0));
  const currentShader = compile(material), version = material.version;
  const other = createTempleVisibilityConfiguration(4, .4, 1.4);
  f.controller.set(other);
  const poses = [pose(0), pose(.5), pose(-.5), tilted(.5, 1.2, .6), tilted(-.5, -1.2, -.6)];
  for (const matrix of poses) {
    const original = matrix.slice(), count = f.fake.state.renderCount;
    f.controller.prepare(matrix);
    assert.equal(f.fake.state.renderCount, count + 1);
    assert.ok(f.overlays.every(overlay => overlay.visible));
    assert.deepEqual(matrix, original, 'pose storage remains owned by the caller');
    assert.deepEqual(f.controller.configuration, other);
    assert.deepEqual(currentShader.uniforms.templeVisibilityRelief!.value.toArray(), [.4, 1.4]);
  }
  assert.equal(material.version, version, 'pose and relief changes do not recompile coverage');
  const otherShader = compile(material);
  assert.equal(otherShader.fragmentShader, currentShader.fragmentShader);
  assert.equal(otherShader.vertexShader, currentShader.vertexShader);
  assert.equal(new Set(depthFragments).size, 1);
  assert.deepEqual(f.geometry.getAttribute('position').array, originalPositions);
  assert.doesNotMatch(currentShader.fragmentShader, /templeVisibilityWeights|templeVisibilityDepthMode|templeFrontalWeight|templeFrontalMask/);
  assert.throws(() => f.controller.prepare(new Array(16).fill(0)), /singular/);
  assert.throws(() => f.controller.prepare([NaN]), /pose is invalid/);
  assert.equal(createTempleVisibilityConfiguration(0).coverage, 'ordered-dither');
  for (const samples of [-1, 1.5, NaN, Infinity]) assert.throws(() => createTempleVisibilityConfiguration(samples), /sample count/);
  assert.throws(() => createTempleVisibilityConfiguration(4, 1, .5), /relief band/);
  assert.equal(Object.isFrozen(TEMPLE_VISIBILITY_PARAMETERS), true);
  f.controller.set(null);
  assert.ok(f.overlays.every(overlay => !overlay.visible));
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

test('lens input exclusion follows scene entry targets and never suppresses an explicit output target', t => {
  const f = syntheticFixture(); t.after(f.dispose);
  const original = f.root.children.find((mesh): mesh is Mesh => mesh instanceof Mesh && mesh.material === f.frame)!;
  const shader = compile(f.frame), version = f.frame.version;
  const configuration = {...createTempleVisibilityConfiguration(4), excludeArmsFromLensInput: true};
  f.controller.set(configuration);
  assert.equal(shader.uniforms.templeExcludeArmsFromLensInput!.value, 1);
  assert.equal(compile(f.lens).uniforms.templeExcludeArmsFromLensInput, undefined, 'physical lens materials stay untouched');
  const output = new WebGLRenderTarget(8, 8), transmission = new WebGLRenderTarget(8, 8);
  t.after(() => { output.dispose(); transmission.dispose(); });
  const draw = (camera = f.camera, backend = f.fake.renderer) => Reflect.apply(original.onBeforeRender, original,
    [backend, f.scene, camera, original.geometry, f.frame, null]);
  for (const entry of [null, output]) {
    f.fake.state.target = entry;
    Reflect.apply(f.scene.onBeforeRender, f.scene, [f.fake.renderer, f.scene, f.camera, entry]);
    draw(); assert.equal(shader.uniforms.templeInternalLensInput!.value, 0, 'both native and explicit outputs retain arms');
    f.fake.state.target = transmission; draw();
    assert.equal(shader.uniforms.templeInternalLensInput!.value, 1, 'only an internal target switch selects the exclusion');
    draw(new PerspectiveCamera());
    assert.equal(shader.uniforms.templeInternalLensInput!.value, 0, 'an unregistered camera cannot inherit prior target state');
    draw(f.camera, fakeBackend().renderer);
    assert.equal(shader.uniforms.templeInternalLensInput!.value, 0, 'another renderer cannot inherit this renderer’s target state');
    f.fake.state.target = entry; draw(); assert.equal(shader.uniforms.templeInternalLensInput!.value, 0);
  }
  f.controller.set({...configuration, excludeArmsFromLensInput: false});
  assert.equal(shader.uniforms.templeExcludeArmsFromLensInput!.value, 0);
  f.controller.set(configuration); f.controller.set(null);
  assert.equal(shader.uniforms.templeExcludeArmsFromLensInput!.value, 0);
  assert.equal(shader.uniforms.templeInternalLensInput!.value, 0);
  assert.equal(f.frame.version, version, 'lens exclusion changes update uniforms without recompiling material programs');
  assert.throws(() => f.controller.set({...configuration, excludeArmsFromLensInput: 1} as unknown as TempleVisibilityConfiguration),
    /lens input arm exclusion is invalid/);
});

test('lens input exclusion preserves existing original mesh callbacks and restores them on disposal', t => {
  const f = syntheticFixture(); t.after(f.dispose); f.controller.dispose();
  const original = f.root.children.find((mesh): mesh is Mesh => mesh instanceof Mesh && mesh.material === f.frame)!;
  let calls = 0;
  const before: Mesh['onBeforeRender'] = function(this: Mesh) { assert.equal(this, original); calls++; };
  original.onBeforeRender = before;
  const materialHook = f.frame.onBeforeCompile, materialKey = f.frame.customProgramCacheKey, sceneHook = f.scene.onBeforeRender;
  const controller = createTempleVisibility(f.root, {renderer: f.fake.renderer, scene: f.scene, camera: f.camera, eyewearPose: f.eyewearPose, observedFaceSurface: f.observedFaceSurface});
  t.after(() => controller.dispose());
  const shader = compile(f.frame);
  controller.set({...createTempleVisibilityConfiguration(4), excludeArmsFromLensInput: true});
  Reflect.apply(original.onBeforeRender, original, [f.fake.renderer, f.scene, f.camera, original.geometry, f.frame, null]);
  assert.equal(calls, 1);
  controller.dispose(); controller.dispose();
  assert.equal(original.onBeforeRender, before);
  assert.equal(f.frame.onBeforeCompile, materialHook); assert.equal(f.frame.customProgramCacheKey, materialKey);
  assert.equal(f.scene.onBeforeRender, sceneHook);
  assert.equal(shader.uniforms.templeExcludeArmsFromLensInput!.value, 0);
  assert.equal(shader.uniforms.templeInternalLensInput!.value, 0);
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
    assert.equal(depth.depthTest, true); assert.equal(depth.depthWrite, true); assert.equal(depth.side, DoubleSide);
    assert.match(depth.vertexShader, /vec4 world = modelMatrix \* vec4\(position, 1\.0\)/);
    assert.match(depth.vertexShader, /gl_Position = projectionMatrix \* viewMatrix \* world/);
    assert.doesNotMatch(depth.fragmentShader, /discard|gl_FragDepth/);
    throw new Error('simulated depth-pass failure');
  };
  f.controller.set(createTempleVisibilityConfiguration(4));
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
  f.controller.set(createTempleVisibilityConfiguration(4)); f.controller.prepare(pose(-.3));
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

for (const definition of Object.values(EYEWEAR)) test(`${definition.name}: observed cheek masking protects the rim without changing buffers or lens exclusion`, async t => {
  const bytes = await readFile(new URL('../public' + definition.assetUrl, import.meta.url));
  const gltf = await new GLTFLoader().register(() => ({name: 'NodeVisibilityImages', loadTexture: async () => new Texture()}))
    .parseAsync(new Uint8Array(bytes).buffer, '/models/');
  const frontMeasurement = createRearDrop(gltf.scene, definition.templeClipLocalZM);
  const opticalBounds = frontMeasurement.opticalBounds;
  frontMeasurement.dispose();
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
  const observedFaceSurface = new BufferGeometry().setAttribute('position', new BufferAttribute(new Float32Array([
    -10, -10, -50, 10, -10, -50, 0, 10, -50,
  ]), 3));
  resources.add(observedFaceSurface);
  scene.add(eyewearPose);
  const clip = createTempleClip(gltf.scene);
  const controller = createTempleVisibility(gltf.scene, {renderer: fake.renderer, scene, camera, eyewearPose, observedFaceSurface});
  t.after(() => { controller.dispose(); clip.dispose(); for (const resource of resources) resource.dispose(); });
  controller.set(createTempleVisibilityConfiguration(4)); controller.prepare(pose(.5));
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
  const lensMeshes = originals.filter(mesh => mesh.material instanceof MeshPhysicalMaterial && mesh.material.transmission > 0);
  const physicalRear = Math.min(...lensMeshes.flatMap(mesh => Array.from({length: mesh.geometry.getAttribute('position').count},
    (_, i) => mesh.geometry.getAttribute('position').getZ(i))));
  const opaqueMeshes = originals.filter(mesh => !lensMeshes.includes(mesh));
  const guardRear = physicalRear - .005;
  assert.equal(TEMPLE_VISIBILITY_PARAMETERS.cheekFrontGuardM, .005);
  assert.ok(guardRear < opticalBounds.min.z, 'the complete protected optical front, including opaque rims and hinge, lies ahead of concealment');
  const protectedBandCounts = [0, 0], posteriorShaftCounts = [0, 0];
  for (const mesh of opaqueMeshes) {
    const position = mesh.geometry.getAttribute('position');
    for (let i = 0; i < position.count; i++) {
      const x = position.getX(i), z = position.getZ(i); if (Math.abs(x) <= .045) continue;
      const side = x < 0 ? 0 : 1;
      if (z < physicalRear && z >= guardRear) protectedBandCounts[side]!++;
      if (z < guardRear) posteriorShaftCounts[side]!++;
    }
  }
  assert.ok(protectedBandCounts.every(count => count > 0), 'both physical sides contain opaque front geometry behind the lens plane that needs the guard');
  assert.ok(posteriorShaftCounts.every(count => count > 0), 'the guard still leaves posterior shaft geometry eligible for hiding');
  controller.set({...createTempleVisibilityConfiguration(4),
    cheekContact: cheekContact(), cheekTransitionPx: 2, excludeArmsFromLensInput: true});
  const shaders = [...opaqueMeshes, ...overlays].map(mesh => compile(mesh.material as Material));
  for (const shader of shaders) {
    assert.equal(shader.uniforms.templeFrontalFront!.value, physicalRear, 'transmission uses the exact physical lens rear');
    assert.equal(shader.uniforms.templeCheekFront!.value, guardRear, 'only cheek RGB concealment starts 5 mm farther back');
    assert.equal(shader.uniforms.templeCheekCount!.value, 4);
    assert.equal(shader.uniforms.templeCheekTransitionPx!.value, 2);
    assert.equal(shader.uniforms.templeExcludeArmsFromLensInput!.value, 1);
    assert.equal(shader.uniforms.templeFrontalWeight, undefined);
    assert.match(shader.fragmentShader, /templeFrontalOriginalPosition\.z < templeCheekFront\) \{/);
    assert.match(shader.fragmentShader, /templeCheekFront - templeFrontalOriginalPosition\.z/);
    assert.match(shader.fragmentShader, /cheekCoverage = templeCheekCoverage\(vec2\(gl_FragCoord\.x, templeFrontalViewport\.y - gl_FragCoord\.y\)\)/,
      'local coverage samples the exact fragment in source-aligned top-left coordinates');
    const source = shader.fragmentShader;
    const mainStart = source.indexOf('void main() {');
    const depthStart = source.indexOf('if (templeCheekCount >= 3) {', mainStart);
    const depthEnd = source.indexOf('#endif', depthStart);
    assert.ok(mainStart >= 0 && depthStart > mainStart && depthEnd > depthStart);
    const contactDepth = source.slice(depthStart, depthEnd);
    assert.match(contactDepth, /texture2D\(templeCheekDepth, cheekDepthUV\)/);
    assert.match(contactDepth, /cheekArmZ[^;]*gl_FragCoord\.z/,
      'compare original raster depth before the overlay can change tested gl_FragDepth');
    assert.match(contactDepth, /cheekFaceZ - cheekArmZ/,
      'face coverage can hide only a fragment behind observed skin');
    const derivative = source.indexOf('fwidth(cheekBehindCm)', depthStart);
    assert.ok(derivative > depthStart && derivative < depthEnd);
    assert.doesNotMatch(source.slice(mainStart, derivative), /templeFrontalOriginalPosition|templeVisibilityPosition|discard|gl_FragDepth/,
      'native-depth derivatives run in uniform control flow before local eligibility or any discard');
    for (const marker of ['#include <clipping_planes_fragment>', 'templeClipPositiveXCutoffZ)) discard']) {
      const index = source.indexOf(marker, mainStart);
      assert.ok(index > derivative, `native-depth derivatives precede ${marker}`);
    }
    if (shader.uniforms.templeVisibilityHeadDepth) {
      for (const marker of ['templeVisibilityPosition.z >= templeVisibilityFront) discard;',
        'if (templeOverlayCoverage <= 0.0) discard;']) {
        const index = source.indexOf(marker, mainStart);
        assert.ok(index > derivative, `overlay derivatives precede ${marker}`);
      }
    }
    for (const match of source.slice(mainStart).matchAll(/discard;/g))
      assert.ok(mainStart + match.index > derivative, 'every emitted discard follows the derivative, including dither when selected');
    const contactStart = source.indexOf('if (templeCheekCount >= 3 &&', depthEnd);
    const contactEnd = source.indexOf('#endif', contactStart);
    assert.ok(contactStart > depthEnd && contactEnd > contactStart);
    const contactCoverage = source.slice(contactStart, contactEnd);
    assert.match(contactCoverage, /texture2D\(templeCheekMask, cheekDepthUV\)\.a \* smoothstep/,
      'a cleared depth pixel without observed face coverage cannot mask a shaft');
    assert.match(contactCoverage, /smoothstep\(\s*0\.1, 0\.1 \+ cheekBandCm, cheekBehindCm\)/,
      'moving the derivative does not move the physical 1 mm onset');
    assert.doesNotMatch(contactCoverage, /fwidth\(/, 'no derivative remains in the varying eligibility branch');
    assert.doesNotMatch(contactDepth + contactCoverage, /templeCheekTail|gl_FragDepth/,
      'there is no added tail cutoff or lifted-depth comparison');
    const inputStart = shader.fragmentShader.indexOf('if (templeExcludeArmsFromLensInput');
    const inputEnd = shader.fragmentShader.indexOf('discard;', inputStart);
    const inputExclusion = shader.fragmentShader.slice(inputStart, inputEnd);
    assert.ok(inputStart >= 0 && inputEnd > inputStart);
    assert.match(inputExclusion, /templeFrontalOriginalPosition\.z < templeFrontalFront/);
    assert.doesNotMatch(inputExclusion, /templeCheekFront|templeCheekDepth|templeCheekTransitionPx/,
      'observed face depth, its rim guard and its soft transition cannot reopen arms inside lens transmission');
  }
  for (const overlay of overlays) assert.equal(compile(overlay.material as Material).uniforms.templeVisibilityFront!.value, physicalRear);
  for (const mesh of lensMeshes) {
    const lensShader = compile(mesh.material as Material);
    assert.equal(lensShader.uniforms.templeCheekFront, undefined);
    assert.equal(lensShader.uniforms.templeCheekTransitionPx, undefined);
  }
  controller.set(createTempleVisibilityConfiguration(4));
  for (const shader of shaders) {
    assert.equal(shader.uniforms.templeCheekCount!.value, 0, 'clearing contact does not retain its old polygon');
    assert.equal(shader.uniforms.templeCheekTransitionPx!.value, 0);
    assert.equal(shader.uniforms.templeFrontalFront!.value, physicalRear);
    assert.equal(shader.uniforms.templeFrontalWeight, undefined);
  }
});
