/** Lifecycle tests only; actual pixel/depth conformance needs the GPU harness. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {AdditiveBlending, Color, FloatType, GreaterDepth, Group, LessDepth, Mesh,
  MeshBasicMaterial, PerspectiveCamera, PlaneGeometry, Scene, Vector4, WebGLRenderTarget} from 'three';
import type {Material, WebGLRenderer} from 'three';
import {MAX_NEAREST_OPTICAL_GROUPS, NearestOpticalGroups} from '../src/render/nearest-optical-groups.ts';

function fakeRenderer() {
  const initial = new WebGLRenderTarget(3, 5), viewport = new Vector4(2, 3, 11, 13), scissor = new Vector4(4, 5, 7, 9);
  const state = {target: initial as WebGLRenderTarget | null, face: 2, mip: 3, viewport, scissor,
    scissorTest: true, clear: new Color(.1, .2, .3), alpha: .4, depthClear: .7, fail: false,
    draws: [] as {target: WebGLRenderTarget | null; alpha: number; depthClear: number;
      meshes: {mesh: Mesh; material: Material; world: number[]; depthFunc: number; colorWrite: boolean; stencilWrite: boolean}[]}[]};
  const renderer = {autoClear: true, xr: {enabled: true}, extensions: {has: () => true},
    state: {buffers: {depth: {setClear: (value: number) => {state.depthClear = value;}}}},
    getContext: () => ({DEPTH_CLEAR_VALUE: 1, getParameter: () => state.depthClear}),
    getRenderTarget: () => state.target, getActiveCubeFace: () => state.face, getActiveMipmapLevel: () => state.mip,
    setRenderTarget: (target: WebGLRenderTarget | null, face = 0, mip = 0) => {Object.assign(state, {target, face, mip});},
    getViewport: (out: Vector4) => out.copy(viewport), getScissor: (out: Vector4) => out.copy(scissor),
    setViewport: (x: number | Vector4, y?: number, w?: number, h?: number) => {x instanceof Vector4 ? viewport.copy(x) : viewport.set(x, y!, w!, h!);},
    setScissor: (out: Vector4) => {scissor.copy(out);}, getScissorTest: () => state.scissorTest,
    setScissorTest: (value: boolean) => {state.scissorTest = value;},
    getClearColor: (out: Color) => out.copy(state.clear), getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha: number) => {state.clear.set(value); state.alpha = alpha;}, clear: () => {},
    render: (scene: Scene) => {
      scene.updateMatrixWorld(true);
      const meshes: (typeof state.draws)[number]['meshes'] = [];
      scene.traverseVisible(object => {
        if (object instanceof Mesh && object.material instanceof MeshBasicMaterial && object.material.visible) {
          const material = object.material;
          meshes.push({mesh: object, material, world: object.matrixWorld.toArray(), depthFunc: material.depthFunc,
            colorWrite: material.colorWrite, stencilWrite: material.stencilWrite});
        }
      });
      state.draws.push({target: state.target, alpha: state.alpha, depthClear: state.depthClear, meshes});
      if (state.fail) throw new Error('draw failed');
    },
  };
  const snapshot = () => ({target: state.target, face: state.face, mip: state.mip, viewport: viewport.toArray(), scissor: scissor.toArray(),
    scissorTest: state.scissorTest, clear: state.clear.toArray(), alpha: state.alpha, depthClear: state.depthClear,
    autoClear: renderer.autoClear, xr: renderer.xr.enabled});
  return {renderer: renderer as unknown as WebGLRenderer, state, snapshot, dispose: () => initial.dispose()};
}

function fixture() {
  const parent = new Group(), geometry = new PlaneGeometry(), material = new MeshBasicMaterial();
  Object.assign(material, {depthFunc: GreaterDepth, depthTest: false, depthWrite: false, colorWrite: false,
    stencilWrite: true, polygonOffset: true, alphaToCoverage: true, blending: AdditiveBlending, transparent: true});
  const first = new Mesh(geometry, material), second = new Mesh(geometry, material);
  parent.add(first); first.add(second); parent.position.set(3, 4, 5); second.position.set(0, .2, .3);
  const camera = new PerspectiveCamera(), fake = fakeRenderer();
  const flags = () => ({depthFunc: material.depthFunc, depthTest: material.depthTest, depthWrite: material.depthWrite,
    colorWrite: material.colorWrite, stencilWrite: material.stencilWrite, polygonOffset: material.polygonOffset,
    alphaToCoverage: material.alphaToCoverage, blending: material.blending, transparent: material.transparent, forceSinglePass: material.forceSinglePass});
  return {parent, geometry, material, first, second, camera, fake, flags,
    dispose: () => {geometry.dispose(); material.dispose(); fake.dispose();}};
}

test('whole multipart group borrows exact materials/geometry and captures current world matrices once', t => {
  const f = fixture(); t.after(f.dispose);
  const groups = new NearestOpticalGroups([{id: 'one', meshes: [f.second, f.first]}]); t.after(() => groups.dispose());
  const before = f.fake.snapshot(), flags = f.flags(); let entered = 0, restored = 0;
  f.parent.rotation.set(.1, .2, .3);
  groups.capture(f.fake.renderer, f.camera, 8, 4, material => {
    assert.equal(material, f.material); entered++;
    return () => {restored++;};
  });
  assert.equal(entered, 1); assert.equal(restored, 1); assert.equal(f.fake.state.draws.length, 1);
  const draw = f.fake.state.draws[0]!;
  assert.equal(draw.alpha, 1); assert.equal(draw.depthClear, 1);
  assert.equal(draw.target!.texture.type, FloatType); assert.equal(draw.target!.depthTexture!.type, FloatType);
  assert.equal(draw.target!.width, 8); assert.equal(draw.target!.height, 4);
  for (const [i, source] of [f.second, f.first].entries()) {
    const proxy = draw.meshes[i]!;
    assert.notEqual(proxy.mesh, source); assert.equal(proxy.mesh.geometry, source.geometry);
    assert.equal(proxy.material, source.material); assert.deepEqual(proxy.world, source.matrixWorld.toArray());
    assert.equal(proxy.depthFunc, LessDepth); assert.equal(proxy.colorWrite, true); assert.equal(proxy.stencilWrite, false);
  }
  assert.equal(f.second.parent, f.first); assert.deepEqual(f.flags(), flags); assert.deepEqual(f.fake.snapshot(), before);
  assert.equal(groups.texture('one'), draw.target!.texture);
});

test('distinct groups sharing source material own different maps and recapture independently', t => {
  const f = fixture(); t.after(f.dispose);
  const groups = new NearestOpticalGroups([{id: 'a', meshes: [f.first]}, {id: 'b', meshes: [f.second]}]); t.after(() => groups.dispose());
  assert.notEqual(groups.texture('a'), groups.texture('b'));
  const textures = [groups.texture('a'), groups.texture('b')]; let count = 0;
  for (const size of [4, 8]) groups.capture(f.fake.renderer, f.camera, size, size, () => {count++; return () => {};});
  assert.equal(count, 4); assert.equal(groups.texture('a'), textures[0]); assert.equal(groups.texture('b'), textures[1]);
  assert.equal(f.fake.state.draws.at(-1)!.target!.width, 8);
});

test('hidden ancestors/materials and camera layers remain absent while maps still clear', t => {
  const f = fixture(); t.after(f.dispose);
  const groups = new NearestOpticalGroups([{id: 'one', meshes: [f.first, f.second]}]); t.after(() => groups.dispose());
  let calls = 0; const render = () => groups.capture(f.fake.renderer, f.camera, 8, 4, () => {calls++; return () => {};});
  f.parent.visible = false; render(); assert.equal(calls, 0); assert.equal(f.fake.state.draws.at(-1)!.meshes.length, 0);
  f.parent.visible = true; f.material.visible = false; render(); assert.equal(calls, 0);
  f.material.visible = true; f.second.layers.set(2); render(); assert.equal(calls, 1);
  assert.deepEqual(f.fake.state.draws.at(-1)!.meshes.map(row => row.world), [f.first.matrixWorld.toArray()]);
  f.first.visible = false; render(); assert.equal(calls, 1);
});

test('draw/entry/cleanup failures restore renderer flags and every successful callback', t => {
  for (const failure of ['draw', 'entry', 'cleanup']) {
    const f = fixture(); t.after(f.dispose);
    const secondMaterial = f.material.clone(); f.second.material = secondMaterial; t.after(() => secondMaterial.dispose());
    const groups = new NearestOpticalGroups([{id: 'one', meshes: [f.first, f.second]}]); t.after(() => groups.dispose());
    const before = f.fake.snapshot(), flags = f.flags(), restored: Material[] = [];
    f.fake.state.fail = failure === 'draw';
    assert.throws(() => groups.capture(f.fake.renderer, f.camera, 8, 4, material => {
      if (failure === 'entry' && material === secondMaterial) throw new Error('entry failed');
      return () => {restored.push(material); if (failure === 'cleanup' && material === secondMaterial) throw new Error('cleanup failed');};
    }), /failed/);
    assert.deepEqual(restored, failure === 'entry' ? [f.material] : [secondMaterial, f.material]);
    assert.deepEqual(f.flags(), flags); assert.deepEqual(f.fake.snapshot(), before);
    assert.equal(secondMaterial.depthFunc, flags.depthFunc);
    f.fake.state.fail = false;
    assert.doesNotThrow(() => groups.capture(f.fake.renderer, f.camera, 8, 4, () => () => {}));
  }
});

test('membership/capacity validation never truncates and dispose owns only targets', t => {
  const f = fixture(); t.after(f.dispose);
  assert.throws(() => new NearestOpticalGroups([{id: '', meshes: [f.first]}]), /identity/);
  assert.throws(() => new NearestOpticalGroups([{id: 'x', meshes: []}]), /membership/);
  assert.throws(() => new NearestOpticalGroups([{id: 'x', meshes: [f.first]}, {id: 'x', meshes: [f.second]}]), /duplicate/);
  assert.throws(() => new NearestOpticalGroups([{id: 'x', meshes: [f.first]}, {id: 'y', meshes: [f.first]}]), /exactly one/);
  assert.throws(() => new NearestOpticalGroups(Array.from({length: MAX_NEAREST_OPTICAL_GROUPS+1}, (_, i) => ({id: String(i), meshes: [f.first]}))), /at most 8/);
  assert.throws(() => new NearestOpticalGroups([{id: 'x', meshes: [new Mesh(f.geometry, [f.material])]}]), /single-material/);
  const groups = new NearestOpticalGroups([{id: 'one', meshes: [f.first]}]);
  let borrowed = 0, disposed = 0;
  f.geometry.addEventListener('dispose', () => {borrowed++;}); f.material.addEventListener('dispose', () => {borrowed++;});
  assert.throws(() => groups.capture(f.fake.renderer, f.camera, 0, 4, () => () => {}), /viewport/);
  assert.throws(() => groups.texture('missing'), /Unknown/);
  groups.capture(f.fake.renderer, f.camera, 8, 4, () => () => {});
  f.fake.state.draws[0]!.target!.addEventListener('dispose', () => {disposed++;});
  groups.dispose(); groups.dispose();
  assert.equal(disposed, 1); assert.equal(borrowed, 0);
  assert.throws(() => groups.texture('one'), /disposed/);
  assert.throws(() => groups.capture(f.fake.renderer, f.camera, 8, 4, () => () => {}), /disposed/);
});
