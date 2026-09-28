/** The renderer's wiring of translucent (crystal) frames. With canonical lenses every pass (pass A, pass B, the unguarded
 *  frame, the safe fallback) draws the crystal's twin, which looks through to the renderer's owned look-through image:
 *  the camera behind the opaque eyewear, the relieved near-arm hardware included, the lenses and the crystal left out.
 *  Inside the canonical lens input, which excludes the arms, the twins sample the background instead of that image.
 *  Legacy assets (no translucent frame, transmissive lenses) render exactly as before. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {
  ACESFilmicToneMapping, BoxGeometry, BufferAttribute, BufferGeometry, Color, Euler, Group, Material, Matrix4, Mesh, MeshBasicMaterial, MeshPhysicalMaterial,
  MeshStandardMaterial, PerspectiveCamera, Quaternion, Scene, ShaderLib, SRGBColorSpace, UniformsUtils, Vector2, Vector3, Vector4,
  WebGLRenderTarget,
} from 'three';
import type {Texture} from 'three';
import {TryOnRenderer} from '../src/render/renderer.ts';
import type {RendererOptions} from '../src/render/renderer.ts';
import {FaceSurface} from '../src/render/face-surface.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {createTempleClip} from '../src/render/temple-clip.ts';
import {createTempleTerminalFit} from '../src/render/temple-terminal-fit.ts';
import {TempleCheekContactEstimator} from '../src/render/temple-cheek-contact.ts';
import {CAMERA_TRANSMISSION_TWIN_KEY} from '../src/render/translucent-twin.ts';
import type {TranslucentLookThrough} from '../src/render/translucent-look-through.ts';
import type {TempleVisibilityConfiguration} from '../src/render/temple-visibility.ts';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {classifyAssetMaterials, isTranslucentFrameMaterial} from '../src/eyewear/optical-material.ts';
import {eyewearById, DEFAULT_EYEWEAR_ID} from '../src/eyewear/catalog.ts';
import type {Detection} from '../src/face/protocol.ts';

const canonical = JSON.parse(await readFile(new URL('../public/models/canonical-face.json', import.meta.url), 'utf8')) as {positions: number[]; indices: number[]};
const frame = {width: 640, height: 480} as HTMLCanvasElement;

function detection(): Detection {
  const matrix = new Matrix4().compose(new Vector3(0, 0, -45), new Quaternion().setFromEuler(new Euler(0, .2, 0)), new Vector3(1, 1, 1));
  const camera = new PerspectiveCamera(63, 4 / 3, 1, 10_000); camera.updateMatrixWorld();
  return {matrix: matrix.toArray(), inferenceMs: 0, landmarks: Array.from({length: 478}, (_, i) => {
    if (i >= 468) return {x: .5, y: .5, z: 0};
    const projected = new Vector3().fromArray(canonical.positions, i * 3).applyMatrix4(matrix).project(camera);
    return {x: (projected.x + 1) / 2, y: (1 - projected.y) / 2, z: 0};
  })};
}

type CompileInput = Parameters<Material['onBeforeCompile']>[0];
function compile(material: Material) {
  const physical = ShaderLib.physical!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader' | 'outputColorSpace'> = {
    uniforms: UniformsUtils.clone(physical.uniforms), vertexShader: physical.vertexShader, fragmentShader: physical.fragmentShader,
    outputColorSpace: SRGBColorSpace,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, {}]);
  return shader;
}

/** One drawn mesh/material pair, read at draw time after its render callbacks ran (as Three does). */
interface Drawn {mesh: Mesh; material: Material; colorWrite: boolean; lensInput: number; stencil: {write: boolean; ref: number}}
/** One render of the eyewear scene: its output, background and what it drew, and the twins' bound source at the time. */
interface Draw {target: WebGLRenderTarget | null; background: unknown; drawn: Drawn[]; twinSource: Texture | null; twinUv: number[]; twinViewport: number[];
  relief: number[]}

/** A TryOnRenderer on a recording backend, wired by the renderer's own arm-shader installation. By default the delivered
 *  case: a crystal arm (authored roles) around a gold core, beside a canonical lens. `crystal: false` builds a legacy
 *  asset instead: opaque arms around the same core beside a transmissive lens, with neither roles nor descriptors. */
function harness(options: RendererOptions = {}, {crystal: withCrystal = true} = {}) {
  const draws: Draw[] = [];
  const state = {target: null as WebGLRenderTarget | null, viewport: new Vector4(), scissor: new Vector4(), color: new Color(), alpha: 1};
  const noop = () => {};
  const backend = {
    capabilities: {samples: 0}, autoClear: true, toneMapping: ACESFilmicToneMapping, toneMappingExposure: 1, setSize: noop, setScissorTest: noop, clear: noop, dispose: noop, forceContextLoss: noop,
    state: {buffers: {stencil: {setMask: noop, setClear: noop}}},
    getDrawingBufferSize: (out: Vector2) => out.set(640, 480),
    getRenderTarget: () => state.target,
    setRenderTarget: (value: WebGLRenderTarget | null) => {state.target = value;},
    getActiveCubeFace: () => 0, getActiveMipmapLevel: () => 0,
    getViewport: (out: Vector4) => out.copy(state.viewport), setViewport: noop,
    getScissor: (out: Vector4) => out.copy(state.scissor), setScissor: noop, getScissorTest: () => false,
    getClearColor: (out: Color) => out.copy(state.color), getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha: number) => {state.color.set(value); state.alpha = alpha;},
    render(scene: Scene, camera: PerspectiveCamera) {
      Reflect.apply(scene.onBeforeRender, scene, [backend, scene, camera, state.target]);
      // The visibility head/cheek depth passes (override material, or their own scene) are not recorded.
      if (scene.overrideMaterial !== null || scene !== internal.scene) return;
      const drawn: Drawn[] = [];
      scene.traverseVisible(object => {
        if (!(object instanceof Mesh)) return;
        for (const material of (Array.isArray(object.material) ? object.material : [object.material]) as Material[]) {
          if (!material.visible) continue;
          Reflect.apply(object.onBeforeRender, object, [backend, scene, camera, object.geometry, material, null]);
          drawn.push({mesh: object, material, colorWrite: material.colorWrite, lensInput: lensInput.value,
            stencil: {write: material.stencilWrite, ref: material.stencilRef}});
          Reflect.apply(object.onAfterRender, object, [backend, scene, camera, object.geometry, material, null]);
        }
      });
      const twin = internal.twinUniforms;
      draws.push({target: state.target, background: scene.background, drawn, twinSource: twin.twinCameraSource.value,
        twinUv: twin.twinCameraUvTransform.value.elements.map(value => value + 0), twinViewport: twin.twinCameraViewport.value.toArray(),
        relief: relief.value.toArray()});
    },
  };
  const gl = {SCISSOR_TEST: 1, COLOR_BUFFER_BIT: 2, DEPTH_BUFFER_BIT: 4, STENCIL_BUFFER_BIT: 8, disable: noop, enable: noop, clear: noop, scissor: noop};
  const renderer = new (TryOnRenderer as unknown as new (backend: unknown, gl: unknown, eyewear: unknown, options: RendererOptions) => TryOnRenderer)(
    backend, gl, eyewearById(DEFAULT_EYEWEAR_ID), {sync: false, guard: true, ...options});
  const internal = renderer as unknown as {scene: Scene; eyewearPose: Group; facePose: Group;
    twinUniforms: {twinCameraSource: {value: Texture | null}; twinCameraUvTransform: {value: {elements: number[]}}; twinCameraViewport: {value: Vector2}};
    installArmShaders(root: Group): void; translucentFrameMeshes: {mesh: Mesh}[]; lookThrough: TranslucentLookThrough | null;
    templeVisibility: {configuration: TempleVisibilityConfiguration | null}; protectionConfiguration: unknown};
  Object.assign(renderer, {fitRevealed: true});
  const lens = new MeshPhysicalMaterial({transmission: withCrystal ? 0 : 1});
  if (withCrystal) lens.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: {
    schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: 0,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null,
  }}};
  const crystal = withCrystal ? new MeshPhysicalMaterial({transmission: .9, ior: 1.49, thickness: .004, color: 0xf2ece0})
    : new MeshStandardMaterial({color: 0x222222});
  crystal.toneMapped = !withCrystal;
  const gold = new MeshStandardMaterial({color: 0xd4af37, metalness: 1, roughness: .3});
  const root = new Group();
  const arm = new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(-.065, 0, -.1), crystal);
  const other = new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(.065, 0, -.1), crystal);
  const core = new Mesh(new BoxGeometry(.0006, .0007, .15, 1, 1, 16).translate(-.065, 0, -.1), gold);
  if (withCrystal) arm.userData.partRole = other.userData.partRole = core.userData.partRole = 'temple';
  const lensMesh = new Mesh(new BoxGeometry(.05, .025, .002).translate(0, 0, -.01), lens);
  if (withCrystal) lensMesh.userData.partRole = 'lens';
  root.add(arm, other, core, lensMesh);
  assert.deepEqual(classifyAssetMaterials(root).translucentFrameMaterials, withCrystal ? [crystal] : []);
  internal.eyewearPose.add(root);
  const clip = createTempleClip(root), rearDrop = createRearDrop(root, -.14);
  const observed = new BufferAttribute(new Float32Array(canonical.positions.length), 3);
  const observedGeometry = new BufferGeometry().setAttribute('position', observed); observedGeometry.setIndex(canonical.indices);
  const depth = new MeshBasicMaterial({colorWrite: false});
  const surface = new Mesh(new BoxGeometry(), depth), head = new Mesh(new BoxGeometry(), depth);
  internal.scene.add(surface); internal.facePose.add(head);
  Object.assign(renderer, {canonicalPositions: canonical.positions, rearDrop, templeClip: clip,
    templeTerminalFit: createTempleTerminalFit(root, {offsetCm: renderer.eyewear.offsetCm, spreadM: .018,
      spreadStartZM: rearDrop.spreadStartZM, modelCutoffZM: -.14, maximumZM: -.115}),
    surfaceMesh: surface, templeHeadShell: head, faceSurface: new FaceSurface(canonical.positions),
    observedCheekGeometry: observedGeometry, observedCheekAttribute: observed,
    cheekContact: new TempleCheekContactEstimator(canonical.positions, canonical.indices),
    nasalShape: {apply: ({surfacePositions}: {surfacePositions: Float32Array}) => ({surfacePositions})}});
  internal.installArmShaders(root);
  // The frontal wrapper's live lens-input flag, shared by every wrapped frame material.
  const lensInput = compile(gold).uniforms.templeInternalLensInput as {value: number};
  const overlayOf = (mesh: Mesh) => root.children.find((child): child is Mesh => child instanceof Mesh
    && child.userData.templeVisibilityOverlay === true && child.geometry === mesh.geometry)!;
  const find = (draw: Draw, mesh: Mesh) => draw.drawn.find(entry => entry.mesh === mesh);
  // The overlays' live relief band (keep, drop), shared by every overlay.
  const relief = compile(overlayOf(core).material as Material).uniforms.templeVisibilityRelief as {value: Vector2};
  return {renderer, internal, root, arm, core, lensMesh, lens, overlayOf, find, crystal, gold, draws, state,
    render: (scene: Scene, camera: PerspectiveCamera) => backend.render(scene, camera), dispose: () => renderer.dispose()};
}

type Harness = ReturnType<typeof harness>;
/** The renderer's own look-through target. */
function lookThroughTarget(f: Harness): WebGLRenderTarget {
  const image = f.internal.lookThrough;
  assert.ok(image, 'a crystal asset with canonical lenses owns a look-through image');
  return image.target;
}

/** What every pass that draws the crystal must show: its twin, looking through to this frame's look-through image. */
function assertLooksThrough(f: Harness, draw: Draw, label: string): void {
  const target = lookThroughTarget(f);
  for (const mesh of [f.arm, f.overlayOf(f.arm)]) {
    const entry = f.find(draw, mesh);
    assert.ok(entry, `${label}: ${mesh === f.arm ? 'the crystal arm' : 'its overlay'} is drawn`);
    const material = entry.material as MeshPhysicalMaterial;
    assert.equal(material.transmission, 0, `${label}: no Three transmission pre-pass is needed for it`);
    assert.ok(material.customProgramCacheKey().endsWith(`|${CAMERA_TRANSMISSION_TWIN_KEY}`), `${label}: the twin program`);
  }
  assert.equal(draw.twinSource, target.texture, `${label}: the twins sample the owned look-through image, not the raw camera`);
  assert.deepEqual(draw.twinUv, [1, 0, 0, 0, 1, 0, 0, 0, 1], `${label}: identity UV, no refraction offset`);
  assert.deepEqual(draw.twinViewport, [640, 480]);
  for (const entry of draw.drawn) if (entry.material instanceof MeshPhysicalMaterial) {
    assert.equal(entry.material.transmission, 0, `${label}: nothing drawn is transmissive, so Three runs no pre-pass of its own`);
  }
}

const passes = (f: Harness, extra: Map<WebGLRenderTarget, string> = new Map()) => f.draws.map(draw => draw.target === null ? 'canvas'
  : draw.target === f.internal.lookThrough?.target ? 'look-through' : extra.get(draw.target) ?? 'other');

test('a crystal asset with canonical lenses: every pass draws the twins, which look through to the owned image of the hardware inside', t => {
  const f = harness(); t.after(f.dispose);
  const clone = f.overlayOf(f.arm).material as MeshPhysicalMaterial;
  assert.notEqual(clone, f.crystal); assert.equal(isTranslucentFrameMaterial(clone), true);
  const listed = f.internal.translucentFrameMeshes.map(entry => entry.mesh);
  assert.ok(listed.includes(f.arm), 'the crystal arm is listed');
  assert.ok(listed.includes(f.overlayOf(f.arm)), 'its visibility overlay is listed: the list is built after the overlays exist');
  assert.equal(f.overlayOf(f.arm).renderOrder, 2, 'the crystal overlay draws after the core overlay at the same lifted depth');
  assert.equal(f.overlayOf(f.core).renderOrder, 1);
  assert.equal(f.renderer.pose(frame, detection(), 100), true);
  assert.equal(f.internal.templeVisibility.configuration?.internalTransmissionIsLensInput, false,
    'canonical lenses read the explicit input only: Three\'s internal pre-pass is not lens input');
  for (const frameIndex of [0, 1]) {
    f.draws.length = 0;
    const timing = f.renderer.render(null);
    assert.equal(timing.guarded, true); assert.equal(timing.passes, 2);
    assert.deepEqual(passes(f), ['look-through', 'canvas', 'canvas'],
      `frame ${frameIndex}: the look-through image first, then the two stencil-limited passes`);
    const [image, a, b] = f.draws as [Draw, Draw, Draw];
    // The image's content rules.
    assert.ok(image.background, 'the camera is behind everything in the look-through image');
    assert.equal(image.background, a.background, 'the same background pass A shows');
    assert.equal(f.find(image, f.arm), undefined, 'the crystal (its twin) never draws into the image it samples');
    assert.equal(f.find(image, f.overlayOf(f.arm)), undefined, 'nor does its overlay');
    assert.equal(f.find(image, f.lensMesh), undefined, 'the lenses are not in it');
    const core = f.find(image, f.core), relieved = f.find(image, f.overlayOf(f.core));
    assert.ok(core?.colorWrite, 'the opaque core is in it');
    assert.equal(core.lensInput, 0, 'with the arms: the arm exclusion does not apply to the look-through image');
    assert.ok(relieved?.colorWrite, 'the relieved near-arm core overlay writes colour there (where it lies behind the head proxy)');
    assert.deepEqual(core.stencil, {write: false, ref: 0}, 'the owned target has no stencil and none is tested');
    const band: TempleVisibilityConfiguration = f.internal.templeVisibility.configuration!;
    assert.deepEqual(image.relief, [band.reliefDropCm, band.reliefDropCm + .01],
      'relieved hardware enters the image at full coverage up to the drop: the crystal overlay applies the relief once');
    assert.deepEqual(a.relief, [band.reliefKeepCm, band.reliefDropCm], 'the canvas passes keep the recorded band');
    assert.deepEqual(b.relief, [band.reliefKeepCm, band.reliefDropCm]);
    // Every canvas pass draws the twins on that image.
    assertLooksThrough(f, a, `frame ${frameIndex} pass A`); assertLooksThrough(f, b, `frame ${frameIndex} pass B`);
    assert.deepEqual(f.find(a, f.arm)!.stencil, {write: true, ref: 1}); assert.deepEqual(f.find(b, f.arm)!.stencil, {write: true, ref: 0});
    assert.deepEqual(f.find(b, f.overlayOf(f.arm))!.stencil, {write: true, ref: 0}, 'the twins are stencil-limited in pass B');
    assert.equal(f.find(a, f.arm)!.material, f.find(b, f.arm)!.material, 'one twin per material in every pass');
    assert.notEqual(f.find(a, f.arm)!.material, f.find(a, f.overlayOf(f.arm))!.material, 'the overlay clone has its own twin');
    assert.ok(f.find(a, f.arm)!.material.customProgramCacheKey().startsWith(f.crystal.customProgramCacheKey()), 'the twin of exactly this source');
    assert.ok(f.find(a, f.overlayOf(f.arm))!.material.customProgramCacheKey().startsWith(clone.customProgramCacheKey()));
    assert.equal(b.background, null, 'pass B redraws no background');
  }
  // The unguarded frame (?guard=0) and the safe fallback (no protection) draw the same twins on the same image.
  f.draws.length = 0;
  assert.equal(f.renderer.render(null, {guard: false}).guarded, false);
  assert.deepEqual(passes(f), ['look-through', 'canvas']);
  assertLooksThrough(f, f.draws[1]!, 'unguarded');
  f.draws.length = 0;
  f.internal.protectionConfiguration = null;
  assert.equal(f.renderer.render(null).safeFallback, true);
  assert.deepEqual(passes(f), ['look-through', 'canvas']);
  assertLooksThrough(f, f.draws[1]!, 'safe fallback');
  // A frame without eyewear renders no look-through image.
  f.draws.length = 0;
  f.renderer.render(null, {eyewear: false});
  assert.deepEqual(passes(f), ['canvas']);
  // Disposal restores the authored materials to their meshes (so they are disposed with the scene) and frees the image.
  const target = lookThroughTarget(f);
  let imageDisposals = 0; target.addEventListener('dispose', () => imageDisposals++);
  f.renderer.dispose();
  assert.equal(f.arm.material, f.crystal, 'the originals are restored on disposal');
  assert.equal(imageDisposals, 1);
});

test('the canonical lens input excludes the arms in its crystal too: the twins look through to the background there, to the image after', t => {
  const f = harness(); t.after(f.dispose);
  const opaqueTarget = new WebGLRenderTarget(8, 8); t.after(() => opaqueTarget.dispose());
  const layers = {
    render(_renderer: unknown, scene: Scene, camera: PerspectiveCamera, _width: number, _height: number, withOpaqueInput: (draw: () => void) => void) {
      f.state.target = opaqueTarget;
      try {withOpaqueInput(() => f.render(scene, camera));} finally {f.state.target = null;}
    },
    dispose() {},
  };
  Object.assign(f.renderer, {canonicalLayers: layers});
  assert.equal(f.renderer.pose(frame, detection(), 100), true);
  for (const guard of [true, false]) {
    f.draws.length = 0;
    f.renderer.render(null, {guard});
    const input = f.draws.find(draw => draw.target === opaqueTarget)!;
    assert.ok(input, 'the lens input is drawn');
    assert.equal(f.find(input, f.core)!.lensInput, 1, 'the explicit canonical input always excludes the arms (no far arm across a lens)');
    // The look-through image holds the arms (it is not lens input). A crystal part seen through a lens (a far rim, a
    // bridge) that sampled it would carry the far arm's hardware across the near lens; it samples the background.
    const crystal = f.find(input, f.arm)!;
    assert.ok(crystal.material.customProgramCacheKey().endsWith(`|${CAMERA_TRANSMISSION_TWIN_KEY}`), 'the crystal is its twin in the lens input');
    assert.ok(input.background, 'the lens input has the camera behind it');
    assert.equal(input.twinSource, input.background, 'inside the lens input the twins sample the background, which has no arms');
    assert.notEqual(input.twinSource, lookThroughTarget(f).texture, 'never the look-through image with the arms in it');
    const background = input.background as Texture;
    assert.deepEqual(input.twinUv, background.matrix.elements.map(value => value + 0), 'at the background\'s own UV transform');
    assert.deepEqual(input.twinViewport, [640, 480]);
    // After the lens input the canvas passes draw the crystal from the image with the hardware inside it.
    const canvas = f.draws.filter(draw => draw.target === null);
    assert.equal(canvas.length, guard ? 2 : 1);
    for (const [i, draw] of canvas.entries()) assertLooksThrough(f, draw, `${guard ? 'guarded' : 'unguarded'} canvas pass ${i}`);
    assert.deepEqual(passes(f, new Map([[opaqueTarget, 'lens input']])), ['lens input', 'look-through', ...canvas.map(() => 'canvas')],
      'the lens input needs no look-through image, so the image is drawn after it, right before the passes that sample it');
  }
});

test('a legacy asset renders exactly as before: no look-through image, no twins, the internal pre-pass stays lens input', t => {
  const f = harness({}, {crystal: false}); t.after(f.dispose);
  assert.equal(f.internal.lookThrough, null);
  assert.deepEqual(f.internal.translucentFrameMeshes, []);
  assert.equal(f.overlayOf(f.arm).renderOrder, 1);
  const materials = [f.crystal, f.gold, f.overlayOf(f.arm).material as Material];
  const keys = materials.map(material => material.customProgramCacheKey());
  const shaders = materials.map(material => compile(material).fragmentShader);
  for (const [i, key] of keys.entries()) {
    assert.ok(!key.includes(CAMERA_TRANSMISSION_TWIN_KEY) && !key.includes('look-through'), 'no look-through program key');
    assert.ok(!shaders[i]!.includes('twinCameraSource'), 'no look-through sample in the shader');
  }
  assert.equal(f.renderer.pose(frame, detection(), 100), true);
  assert.equal(f.internal.templeVisibility.configuration?.internalTransmissionIsLensInput, true,
    'a transmissive lens samples Three\'s pre-pass: the far arm stays out of it');
  for (const guard of [true, false]) {
    f.draws.length = 0;
    f.renderer.render(null, {guard});
    assert.deepEqual(passes(f), guard ? ['canvas', 'canvas'] : ['canvas'], 'only the canvas passes');
    for (const draw of f.draws) {
      const arm = f.find(draw, f.arm), lens = f.find(draw, f.lensMesh);
      if (arm) assert.equal(arm.material, f.crystal, 'the authored material is drawn');
      if (lens) assert.equal(lens.material, f.lens);
    }
  }
  assert.equal(f.arm.material, f.crystal); assert.equal(f.lens.transmission, 1);
  assert.deepEqual(materials.map(material => material.customProgramCacheKey()), keys, 'rendering changed no program key');
  assert.deepEqual(materials.map(material => compile(material).fragmentShader), shaders, 'nor any shader text');
});
