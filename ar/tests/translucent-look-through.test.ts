/** The owned look-through image of translucent (crystal) frame materials: what it holds, what it hides, how it leaves the
 *  renderer, and which assets need it at all. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  ACESFilmicToneMapping, BoxGeometry, Color, Group, HalfFloatType, LinearSRGBColorSpace, Material, Mesh, MeshPhysicalMaterial,
  MeshStandardMaterial, PerspectiveCamera, PlaneGeometry, Scene, ShaderLib, Texture, UniformsUtils, Vector2, Vector4, WebGLRenderTarget,
} from 'three';
import type {WebGLRenderer} from 'three';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';
import {hasLegacyTransmissiveOptics, TranslucentLookThrough} from '../src/render/translucent-look-through.ts';
import {CanonicalLensLayers} from '../src/render/lens-layers.ts';
import {installCanonicalLensMaterials} from '../src/render/lens-material.ts';
import {createCameraTransmissionUniforms, setCameraTransmissionSource} from '../src/render/translucent-twin.ts';

function canonicalLens(): MeshPhysicalMaterial {
  const lens = new MeshPhysicalMaterial({transmission: 1});
  lens.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: {
    schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: 0,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null,
  }}};
  return lens;
}

function recordingBackend() {
  const state = {
    target: null as WebGLRenderTarget | null, face: 2, mip: 1, viewport: new Vector4(3, 4, 50, 60), scissor: new Vector4(1, 2, 30, 40),
    scissorTest: true, color: new Color(0x224466), alpha: .4, autoClear: true,
    renders: [] as {target: WebGLRenderTarget | null; viewport: Vector4; background: unknown; visible: Map<Material, boolean>; autoClear: boolean}[],
    clears: [] as boolean[][], fail: false, onRender: () => {},
  };
  const materials: Material[] = [];
  const backend = {
    toneMapping: ACESFilmicToneMapping, toneMappingExposure: 1,
    get autoClear() {return state.autoClear;}, set autoClear(value: boolean) {state.autoClear = value;},
    getRenderTarget: () => state.target, getActiveCubeFace: () => state.face, getActiveMipmapLevel: () => state.mip,
    setRenderTarget: (target: WebGLRenderTarget | null, face = 0, mip = 0) => {state.target = target; state.face = face; state.mip = mip;},
    getViewport: (out: Vector4) => out.copy(state.viewport),
    setViewport: (x: number | Vector4, y?: number, z?: number, w?: number) => {
      if (x instanceof Vector4) state.viewport.copy(x); else state.viewport.set(x, y!, z!, w!);
    },
    getScissor: (out: Vector4) => out.copy(state.scissor), setScissor: (value: Vector4) => {state.scissor.copy(value);},
    getScissorTest: () => state.scissorTest, setScissorTest: (value: boolean) => {state.scissorTest = value;},
    getClearColor: (out: Color) => out.copy(state.color), getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha: number) => {state.color.set(value); state.alpha = alpha;},
    clear: (color: boolean, depth: boolean, stencil: boolean) => {state.clears.push([color, depth, stencil]);},
    render: (scene: Scene) => {
      state.renders.push({target: state.target, viewport: state.viewport.clone(), background: scene.background,
        visible: new Map(materials.map(material => [material, material.visible])), autoClear: state.autoClear});
      state.onRender();
      if (state.fail) throw new Error('deliberate draw failure');
    },
  };
  return {state, materials, renderer: backend as unknown as WebGLRenderer};
}

test('the look-through image is a sharp linear owned target, rendered with the scene background and the hidden materials off', t => {
  const image = new TranslucentLookThrough(); t.after(() => image.dispose());
  const target = image.target;
  assert.equal(target.texture.type, HalfFloatType, 'linear light above 1 survives, like Three\'s own transmission target');
  assert.equal(target.texture.colorSpace, LinearSRGBColorSpace);
  assert.equal(target.texture.generateMipmaps, false, 'no level-of-detail blur: a thin core stays a line');
  assert.equal(target.samples, 4, 'multisampled like Three\'s pre-pass, so a sub-pixel wire is antialiased, not dropped');
  assert.equal(target.depthBuffer, true); assert.equal(target.stencilBuffer, false);
  const f = recordingBackend();
  const crystal = new MeshPhysicalMaterial(), lens = new MeshPhysicalMaterial(), core = new MeshStandardMaterial();
  lens.visible = true; f.materials.push(crystal, lens, core);
  const scene = new Scene(), background = new Texture(); scene.background = background;
  const texture = image.render(f.renderer, scene, new PerspectiveCamera(), 320, 240, [crystal, lens]);
  assert.equal(texture, target.texture);
  assert.equal(f.state.renders.length, 1);
  const [draw] = f.state.renders;
  assert.equal(draw!.target, target, 'drawn into the owned target');
  assert.deepEqual(draw!.viewport.toArray(), [0, 0, 320, 240]);
  assert.equal(draw!.background, background, 'the camera (or its shadowed composite) is behind everything');
  assert.equal(draw!.visible.get(crystal), false, 'what samples the image never draws into it');
  assert.equal(draw!.visible.get(lens), false, 'lenses are not in it');
  assert.equal(draw!.visible.get(core), true, 'opaque hardware is');
  assert.equal(draw!.autoClear, false); assert.deepEqual(f.state.clears, [[true, true, false]]);
  assert.deepEqual([target.width, target.height], [320, 240]);
  assert.equal(crystal.visible, true); assert.equal(lens.visible, true, 'material visibility is restored');
  assert.equal(f.state.target, null); assert.equal(f.state.face, 2); assert.equal(f.state.mip, 1);
  assert.deepEqual(f.state.viewport.toArray(), [3, 4, 50, 60]); assert.deepEqual(f.state.scissor.toArray(), [1, 2, 30, 40]);
  assert.equal(f.state.scissorTest, true); assert.equal(f.state.color.getHex(), 0x224466); assert.equal(f.state.alpha, .4);
  assert.equal(f.state.autoClear, true);
  assert.equal(scene.background, background);
  // A thrown draw restores the same state.
  lens.visible = false; f.state.fail = true;
  assert.throws(() => image.render(f.renderer, scene, new PerspectiveCamera(), 320, 240, [crystal, lens]), /deliberate draw failure/);
  assert.equal(crystal.visible, true); assert.equal(lens.visible, false, 'a material already hidden stays hidden');
  assert.equal(f.state.target, null); assert.equal(f.state.autoClear, true);
  assert.throws(() => image.render(f.renderer, scene, new PerspectiveCamera(), 0, 240, []), /positive integer size/);
  image.dispose(); image.dispose();
  assert.throws(() => image.render(f.renderer, scene, new PerspectiveCamera(), 320, 240, []), /disposed/);
});

test('opaque hardware enters the image with the display response it has on the canvas, shared with the canonical lens input', t => {
  // The crystal twin is not tone-mapped (it shows the camera); what it looks through to must hold display values, as the
  // canonical lens input does, or a bright core seen through the crystal is HDR-saturated and bleeds wider than itself.
  const image = new TranslucentLookThrough(); t.after(() => image.dispose());
  const f = recordingBackend();
  const core = new MeshStandardMaterial(), crystal = new MeshPhysicalMaterial({transmission: .9});
  crystal.toneMapped = false;
  const box = new BoxGeometry(); t.after(() => {box.dispose(); core.dispose(); crystal.dispose();});
  const scene = new Scene().add(new Mesh(box, core), new Mesh(box, crystal));
  const physical = ShaderLib.physical!;
  const compile = (material: Material) => {
    const shader = {uniforms: UniformsUtils.clone(physical.uniforms), vertexShader: physical.vertexShader, fragmentShader: physical.fragmentShader};
    Reflect.apply(material.onBeforeCompile, material, [shader, {}]);
    return shader;
  };
  let during: unknown = null;
  f.state.onRender = () => {during = compile(core).uniforms.uOpaqueDisplayCapture?.value;};
  image.render(f.renderer, scene, new PerspectiveCamera(), 64, 32, []);
  assert.equal(during, 1, 'ACES, as the canvas applies it, while the image is drawn');
  assert.equal(compile(core).uniforms.uOpaqueDisplayCapture!.value, 0, 'and only then');
  assert.equal(compile(crystal).uniforms.uOpaqueDisplayCapture, undefined, 'untone-mapped materials are not captured');
  // The canonical lens layers share this capture (a material has one capture owner) and never dispose it.
  const descriptor = {schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: 0,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null};
  const source = new MeshPhysicalMaterial(), sheet = new PlaneGeometry(.04, .03);
  source.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: descriptor}};
  const lens = new Mesh(sheet, source); lens.userData = {partRole: 'lens', lensSurfaceProfile: 'front_sheet_v1'};
  const installed = installCanonicalLensMaterials(new Group().add(lens));
  t.after(() => {sheet.dispose(); source.dispose(); for (const material of installed.materials) material.dispose();});
  const layers = new CanonicalLensLayers([lens], image.display);
  layers.dispose();
  assert.doesNotThrow(() => image.display.prepare(scene), 'the shared capture outlives the layers');
});

test('bound as the twins\' source the look-through image is sampled at the fragment\'s own pixel: identity UV, render-size viewport', t => {
  const image = new TranslucentLookThrough(); t.after(() => image.dispose());
  const f = recordingBackend();
  const texture = image.render(f.renderer, new Scene(), new PerspectiveCamera(), 640, 480, []);
  const uniforms = createCameraTransmissionUniforms();
  uniforms.twinCameraUvTransform.value.set(2, 0, .5, 0, 2, .5, 0, 0, 1);
  setCameraTransmissionSource(uniforms, texture, 640, 480);
  assert.equal(uniforms.twinCameraSource.value, texture);
  // (-0 + 0 is 0: Three's setUvTransform writes a negative zero for the unrotated sine.)
  assert.deepEqual(uniforms.twinCameraUvTransform.value.elements.map(value => value + 0), [1, 0, 0, 0, 1, 0, 0, 0, 1], 'no refraction offset, no flip');
  assert.ok(uniforms.twinCameraViewport.value.equals(new Vector2(640, 480)));
});

test('only a lens without a canonical descriptor that still transmits is legacy optics', t => {
  const crystal = new MeshPhysicalMaterial({transmission: .9}), lens = canonicalLens(), legacy = new MeshPhysicalMaterial({transmission: 1});
  const opaque = new MeshStandardMaterial();
  t.after(() => {for (const material of [crystal, lens, legacy, opaque]) material.dispose();});
  const box = new BoxGeometry(), sheet = new PlaneGeometry(); t.after(() => {box.dispose(); sheet.dispose();});
  const arm = new Mesh(box, crystal), lensMesh = new Mesh(sheet, lens), frame = new Mesh(box, opaque);
  arm.userData.partRole = 'temple'; lensMesh.userData.partRole = 'lens'; frame.userData.partRole = 'frame';
  const canonical = new Group().add(arm, lensMesh, frame);
  classifyAssetMaterials(canonical);
  assert.equal(hasLegacyTransmissiveOptics(canonical), false,
    'a canonical lens (transmission 1 at install, 0 once its layers own it) and a crystal frame are not legacy optics');
  const shipped = new Group().add(new Mesh(sheet, legacy), new Mesh(box, new MeshStandardMaterial()));
  classifyAssetMaterials(shipped);
  assert.equal(hasLegacyTransmissiveOptics(shipped), true, 'the shipped catalog\'s transmissive lenses are');
  legacy.transmission = 0;
  assert.equal(hasLegacyTransmissiveOptics(shipped), false);
});
