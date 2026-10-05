// The lensenv address option (ModelDefinition.lensEnvIntensity) through TryOnRenderer.configure: three r185 overwrites
// envMapIntensity from scene.environmentIntensity for a material lit by scene.environment, so the renderer gives the lens
// materials their own environment map at SCENE_ENVIRONMENT_INTENSITY times lensenv.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {BoxGeometry, Color, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Vector2, Vector4} from 'three';
import type {Scene, Texture, WebGLRenderTarget} from 'three';
import {SCENE_ENVIRONMENT_INTENSITY, TryOnRenderer} from '../src/render/renderer.ts';
import type {RendererOptions} from '../src/render/renderer.ts';
import {resolveModelDefinition} from '../src/eyewear/catalog.ts';
import {publicFile, shippedModel} from './shipped-models.ts';

const canonical = JSON.parse(await readFile(publicFile('models/canonical-face.json'), 'utf8')) as {positions: number[]; indices: number[]};

/** A TryOnRenderer on a backend that draws nothing, configured for a small asset: arms and a legacy transmissive lens. */
function configured(lensEnvIntensity: number | undefined) {
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
  const lens = new MeshPhysicalMaterial({transmission: 1});
  const arms = new MeshStandardMaterial({color: 0x222222});
  const root = new Group();
  const left = new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(-.065, 0, -.1), arms);
  const right = new Mesh(new BoxGeometry(.004, .004, .17, 1, 1, 16).translate(.065, 0, -.1), arms);
  // A legacy lens is any transmissive volume.
  const lensMesh = new Mesh(new BoxGeometry(.05, .025, .002).translate(0, 0, -.01), lens);
  root.add(left, right, lensMesh);
  const internal = renderer as unknown as {scene: Scene; environmentTarget: WebGLRenderTarget | null;
    seeThroughEnvironmentTarget: WebGLRenderTarget | null; configure(face: typeof canonical, root: Group): void};
  internal.configure(canonical, root);
  const drawn = lensMesh.material as MeshPhysicalMaterial;
  return {renderer, internal, lens, drawn, arms, dispose: () => renderer.dispose()};
}

const texture = (target: WebGLRenderTarget | null): Texture | undefined => target?.texture;

test('lensenv scales a legacy transmissive lens\'s own environment map from the scene intensity', t => {
  const f = configured(2.5); t.after(f.dispose);
  assert.equal(f.internal.scene.environmentIntensity, SCENE_ENVIRONMENT_INTENSITY);
  assert.equal(f.drawn, f.lens, 'a legacy lens keeps its authored material');
  assert.ok(f.drawn.envMap, 'the lens owns an environment map, so three does not overwrite its intensity');
  assert.equal(f.drawn.envMap, texture(f.internal.environmentTarget), 'the scene\'s room environment');
  assert.equal(f.drawn.envMapIntensity, SCENE_ENVIRONMENT_INTENSITY * 2.5);
  assert.equal(f.arms.envMap, null, 'frame materials stay lit by scene.environment');
});

test('without lensenv a legacy lens is left to scene.environment', t => {
  const f = configured(undefined); t.after(f.dispose);
  assert.equal(f.drawn.envMap, null);
  assert.equal(f.drawn.envMapIntensity, 1, 'the authored default, overwritten by the scene intensity at render');
});
