/** Which room each material of a configured asset reflects (TryOnRenderer.configure). Crystal (translucent frame
 *  materials, the near-arm overlay clones and the twins included) reflects the see-through room: the scene's
 *  RoomEnvironment without the front panel behind the selfie camera, unblurred (eyewear-reflection.ts). Native lenses,
 *  opaque acetate, metal and a legacy asset keep the scene room exactly as before: scene.environment, PMREM blur 0.04,
 *  0.8 (a native lens owns that scene room only to carry lensenv). This is the owner-judged look for Studio GLBs. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {BoxGeometry, Color, Group, Material, Mesh, MeshLambertMaterial, MeshPhysicalMaterial, PMREMGenerator, Vector2, Vector4} from 'three';
import type {Object3D, Scene, Texture, WebGLRenderTarget} from 'three';
import {SCENE_ENVIRONMENT_INTENSITY, TryOnRenderer} from '../src/render/renderer.ts';
import type {RendererOptions} from '../src/render/renderer.ts';
import {FRONT_PANEL} from '../src/render/eyewear-reflection.ts';
import {isOpticalMaterial, isTranslucentFrameMaterial} from '../src/eyewear/optical-material.ts';
import {resolveModelDefinition} from '../src/eyewear/catalog.ts';
import {publicFile, shippedModel} from './shipped-models.ts';

const canonical = JSON.parse(await readFile(publicFile('models/canonical-face.json'), 'utf8')) as {positions: number[]; indices: number[]};

/** Every PMREM the renderer generates: the source room's emitters [x, y, z, emissive] and the blur. */
interface Generated {emitters: number[][]; sigma: number; target: WebGLRenderTarget}
function recordPmrem(t: {after(fn: () => void): void}): Generated[] {
  const calls: Generated[] = [], original = PMREMGenerator.prototype.fromScene;
  PMREMGenerator.prototype.fromScene = function(this: PMREMGenerator, scene: Scene, sigma = 0, ...rest: unknown[]) {
    const emitters = scene.children.filter((child): child is Mesh => child instanceof Mesh && child.material instanceof MeshLambertMaterial)
      .map(child => [...child.position.toArray(), (child.material as MeshLambertMaterial).emissiveIntensity]);
    const target = Reflect.apply(original, this, [scene, sigma, ...rest]) as WebGLRenderTarget;
    calls.push({emitters, sigma, target});
    return target;
  } as typeof original;
  t.after(() => {PMREMGenerator.prototype.fromScene = original;});
  return calls;
}

/** A TryOnRenderer on a backend that draws nothing, configured for a crystal asset as the exporter writes it (a crystal
 *  front with a clearcoat, crystal temples, gold hardware, an opaque acetate part, a native transmissive lens, every
 *  part role-tagged) or, with `legacy`, the shipped GLBs' kind: a transmissive lens and opaque parts, no roles. */
function configured(t: {after(fn: () => void): void}, {legacy = false, lensEnvIntensity}: {legacy?: boolean; lensEnvIntensity?: number} = {}) {
  const state = {target: null as WebGLRenderTarget | null, color: new Color(), alpha: 1};
  const noop = () => {};
  const backend = {
    capabilities: {samples: 0, isWebGL2: true}, extensions: {has: () => true}, autoClear: true, toneMapping: 0, toneMappingExposure: 1, outputColorSpace: '',
    xr: {enabled: false}, state: {buffers: {stencil: {setMask: noop, setClear: noop}, depth: {setMask: noop, getReversed: () => false}}},
    setPixelRatio: noop, setSize: noop, setScissorTest: noop, clear: noop, clearDepth: noop, compile: noop, render: noop, dispose: noop,
    forceContextLoss: noop, getDrawingBufferSize: (out: Vector2) => out.set(640, 480),
    getRenderTarget: () => state.target, setRenderTarget: (value: WebGLRenderTarget | null) => {state.target = value;},
    getActiveCubeFace: () => 0, getActiveMipmapLevel: () => 0,
    getViewport: (out: Vector4) => out, setViewport: noop, getScissor: (out: Vector4) => out, setScissor: noop, getScissorTest: () => false,
    getClearColor: (out: Color) => out.copy(state.color), getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha = 1) => {state.color.set(value); state.alpha = alpha;},
  };
  const gl = {SCISSOR_TEST: 1, COLOR_BUFFER_BIT: 2, DEPTH_BUFFER_BIT: 4, STENCIL_BUFFER_BIT: 8, disable: noop, enable: noop, clear: noop, scissor: noop};
  const eyewear = resolveModelDefinition({...shippedModel('amber-horizon'), ...(lensEnvIntensity === undefined ? {} : {lensEnvIntensity})});
  const renderer = new (TryOnRenderer as unknown as new (backend: unknown, gl: unknown, eyewear: unknown, options: RendererOptions) => TryOnRenderer)(
    backend, gl, eyewear, {sync: false, guard: true});
  const lens = new MeshPhysicalMaterial({name: 'Champagne lens', transmission: 1, roughness: .065, ior: 1.5});
  // The Tom Ford FT1123-D r0009 materials as GLTFLoader builds them (every one carries KHR_materials_transmission).
  const crystalFront = new MeshPhysicalMaterial({name: 'Clear warm crystal acetate', transmission: legacy ? 0 : 1, roughness: .085, ior: 1.49,
    thickness: .0048, attenuationDistance: .02, clearcoat: 1, clearcoatRoughness: .03});
  const crystalTemples = new MeshPhysicalMaterial({name: 'Clear crystal temples', transmission: legacy ? 0 : 1, roughness: .085, ior: 1.49, thickness: .0035});
  const gold = new MeshPhysicalMaterial({name: 'Pale champagne gold', metalness: 1, roughness: .23, color: 0xd8bd88});
  const acetate = new MeshPhysicalMaterial({name: 'Polished midnight navy acetate', roughness: .19, color: 0x0a1020});
  const root = new Group();
  const front = new Mesh(new BoxGeometry(.13, .04, .005).translate(0, 0, -.005), crystalFront);
  const hardware = new Mesh(new BoxGeometry(.004, .004, .004).translate(.064, .014, -.008), gold);
  const bridge = new Mesh(new BoxGeometry(.02, .006, .004).translate(0, .016, -.004), acetate);
  const left = new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(-.065, 0, -.1), crystalTemples);
  const right = new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(.065, 0, -.1), crystalTemples);
  const lensMesh = new Mesh(new BoxGeometry(.05, .025, .002).translate(0, 0, -.01), lens);
  if (!legacy) {
    front.userData.partRole = hardware.userData.partRole = bridge.userData.partRole = 'frame';
    left.userData.partRole = right.userData.partRole = 'temple';
    lensMesh.userData.partRole = 'lens';
  }
  root.add(front, hardware, bridge, left, right, lensMesh);
  const calls = recordPmrem(t);
  const internal = renderer as unknown as {scene: Scene; environmentTarget: WebGLRenderTarget | null; seeThroughEnvironmentTarget: WebGLRenderTarget | null;
    configure(face: typeof canonical, root: Group): void; twinOf(material: Material): Material};
  internal.configure(canonical, root);
  t.after(() => renderer.dispose());
  // The near-arm overlays that clone a crystal material (temple-visibility.ts wrap: an optical part's overlay is hidden).
  const overlays: Mesh[] = [];
  root.traverse((object: Object3D) => {
    if (object instanceof Mesh && object.userData.templeVisibilityOverlay === true && [crystalFront.name, crystalTemples.name].includes((object.material as Material).name)) overlays.push(object);
  });
  return {internal, calls, lens, drawnLens: lensMesh.material as MeshPhysicalMaterial, crystalFront, crystalTemples, gold, acetate, overlays};
}

const texture = (target: WebGLRenderTarget | null): Texture | undefined => target?.texture;
const withPanel = (emitters: number[][]) => emitters.some(row => row.join() === [...FRONT_PANEL.position, FRONT_PANEL.emissiveIntensity].join());

test('the scene keeps three\'s room with its front panel at blur 0.04; crystal shares one unblurred room without it', t => {
  const f = configured(t);
  assert.equal(f.calls.length, 2, 'two rooms: the scene\'s and the see-through room, whatever the number of crystal materials');
  const [scene, seeThrough] = f.calls;
  assert.equal(scene!.sigma, .04); assert.equal(scene!.emitters.length, 6); assert.ok(withPanel(scene!.emitters), 'the scene room is unchanged');
  assert.equal(f.internal.scene.environment, scene!.target.texture);
  assert.equal(f.internal.scene.environmentIntensity, SCENE_ENVIRONMENT_INTENSITY);
  assert.equal(seeThrough!.sigma, 0, 'unblurred: no second roughness floor under three\'s 0.0525');
  assert.equal(seeThrough!.emitters.length, 5); assert.ok(!withPanel(seeThrough!.emitters), 'the front panel is gone');
  assert.deepEqual(seeThrough!.emitters, scene!.emitters.filter(row => !withPanel([row])), 'every other emitter is the scene room\'s');
  assert.equal(texture(f.internal.seeThroughEnvironmentTarget), seeThrough!.target.texture);
});

test('crystal owns the see-through room, overlay clones and twins included, and keeps its clearcoat; lensenv does not apply', t => {
  const f = configured(t, {lensEnvIntensity: 2.5});
  const room = texture(f.internal.seeThroughEnvironmentTarget);
  assert.ok(room);
  for (const material of [f.crystalFront, f.crystalTemples]) {
    assert.ok(isTranslucentFrameMaterial(material), material.name);
    assert.equal(material.envMap, room, material.name);
    assert.equal(material.envMapIntensity, SCENE_ENVIRONMENT_INTENSITY, `${material.name}: owns its map, so three keeps this intensity`);
  }
  assert.equal(f.crystalFront.clearcoat, 1); assert.equal(f.crystalFront.clearcoatRoughness, .03);
  assert.ok(f.overlays.length > 0, 'the crystal temples have near-arm overlay clones');
  for (const overlay of f.overlays) {
    const clone = overlay.material as MeshPhysicalMaterial;
    assert.ok(isTranslucentFrameMaterial(clone));
    assert.equal(clone.envMap, room, 'an overlay clone was cloned before the room existed and still reflects it');
    assert.equal(clone.envMapIntensity, SCENE_ENVIRONMENT_INTENSITY);
  }
  for (const material of [f.crystalFront, f.crystalTemples, f.overlays[0]!.material as MeshPhysicalMaterial]) {
    const twin = f.internal.twinOf(material) as MeshPhysicalMaterial;
    assert.notEqual(twin, material);
    assert.equal(twin.envMap, room, 'the twin that draws the crystal reflects the see-through room');
    assert.equal(twin.envMapIntensity, SCENE_ENVIRONMENT_INTENSITY);
    assert.equal(twin.clearcoat, material.clearcoat); assert.equal(twin.clearcoatRoughness, material.clearcoatRoughness);
  }
});

test('a native lens beside crystal keeps the scene room: lensenv gives it that room at the scene intensity times lensenv, never the see-through room', t => {
  for (const [lensEnvIntensity, map, intensity] of [[undefined, 'none', 1], [2.5, 'scene', SCENE_ENVIRONMENT_INTENSITY * 2.5]] as const) {
    const f = configured(t, {lensEnvIntensity});
    assert.ok(isOpticalMaterial(f.lens), 'the role-tagged lens is optical');
    assert.equal(f.drawnLens, f.lens, 'a native lens keeps its authored material');
    assert.ok(f.internal.seeThroughEnvironmentTarget, 'the crystal still gets its see-through room');
    assert.notEqual(f.drawnLens.envMap, texture(f.internal.seeThroughEnvironmentTarget), `lensenv ${lensEnvIntensity}: never the see-through room`);
    assert.equal(f.drawnLens.envMap, map === 'none' ? null : texture(f.internal.environmentTarget), `lensenv ${lensEnvIntensity}`);
    assert.equal(f.drawnLens.envMapIntensity, intensity, `lensenv ${lensEnvIntensity}`);
  }
});

test('opaque acetate and metal keep the scene room, as before', t => {
  const f = configured(t);
  for (const material of [f.gold, f.acetate]) {
    assert.equal(material.envMap, null, `${material.name}: lit by scene.environment`);
    assert.equal(material.envMapIntensity, 1, `${material.name}: the authored default, overwritten by the scene intensity at render`);
  }
});

test('a legacy asset (no crystal) builds no see-through room and renders exactly as before', t => {
  const f = configured(t, {legacy: true});
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0]!.sigma, .04); assert.ok(withPanel(f.calls[0]!.emitters));
  assert.equal(f.internal.seeThroughEnvironmentTarget, null);
  assert.equal(f.drawnLens, f.lens, 'a legacy lens keeps its authored material');
  for (const material of [f.lens, f.crystalFront, f.gold, f.acetate]) assert.equal(material.envMap, null, material.name);
});
