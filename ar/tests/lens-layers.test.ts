/** Renderer lifecycle/integration contracts. These mocks deliberately do not
 * establish optical pixel correctness; the independent GPU harness does that.
 */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Color, FloatType, Group, LessEqualDepth, Mesh, MeshBasicMaterial, MeshPhysicalMaterial, NoToneMapping,
  PerspectiveCamera, PlaneGeometry, Scene, Vector4, WebGLRenderTarget} from 'three';
import type {BufferGeometry, Camera, Material, Texture, WebGLRenderer} from 'three';
import {CanonicalLensLayers, MAX_CANONICAL_OPTICAL_LAYERS} from '../src/render/lens-layers.ts';
import {canonicalLensRenderState, installCanonicalLensMaterials} from '../src/render/lens-material.ts';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import type {LensAppearanceDescriptor} from '../src/eyewear/lens-appearance.ts';
import {OpticalLayerOverflowError} from '../src/render/layer-overflow.ts';
import {EFFECTIVE_OPTICAL_GROUP_PROFILE} from '../src/eyewear/optical-material.ts';

const descriptor: LensAppearanceDescriptor = {
  schema_version: 1, color_space: 'scene_linear_srgb_D65', density_interpolation: 'piecewise_smoothstep_optical_density',
  vertical_coordinate: 'lens_local_bottom_0_top_1', normal_reflectance_rgb: [.3, .2, .1], refractive_index: 1.5, roughness: .1,
  optical_density_keyframes: [{v: 0, optical_density_rgb: [.1, .2, .3]}, {v: 1, optical_density_rgb: [.9, .7, .4]}],
  angular_reflectance_keyframes: null,
};

function materialList(mesh: Mesh): Material[] {return Array.isArray(mesh.material) ? mesh.material : [mesh.material];}

function fakeRenderer() {
  const callerTarget = new WebGLRenderTarget(13, 11);
  const state = {
    target: callerTarget as WebGLRenderTarget | null, cubeFace: 3, mip: 2,
    viewport: new Vector4(4, 6, 110, 80), scissor: new Vector4(5, 7, 50, 40), scissorTest: true,
    color: new Color(.12, .34, .56), alpha: .65, depthClear: .37,
    floatSupported: true, failOnDraw: 0, overflowFlag: 0,
    draws: [] as {scene: Scene; target: WebGLRenderTarget | null; depthClear: number;
      meshes: {mesh: Mesh; geometry: BufferGeometry; materials: Material[]; matrixWorld: number[];
        mode?: number; groupEnabled?: boolean; nearest?: Texture | null}[]}[],
    readbacks: [] as {target: WebGLRenderTarget; width: number; height: number}[],
  };
  const context = {DEPTH_CLEAR_VALUE: 0x0B73, getParameter: (parameter: number) => {
    assert.equal(parameter, 0x0B73); return state.depthClear;
  }};
  const backend = {
    toneMapping: NoToneMapping, toneMappingExposure: 1,
    autoClear: true, xr: {enabled: true}, extensions: {has: () => state.floatSupported},
    state: {buffers: {depth: {setClear: (value: number) => {state.depthClear = value;}}}},
    getContext: () => context,
    getRenderTarget: () => state.target, getActiveCubeFace: () => state.cubeFace, getActiveMipmapLevel: () => state.mip,
    setRenderTarget: (target: WebGLRenderTarget | null, cubeFace = 0, mip = 0) => {
      state.target = target; state.cubeFace = cubeFace; state.mip = mip;
      if (target) {state.viewport.copy(target.viewport); state.scissor.copy(target.scissor); state.scissorTest = target.scissorTest;}
    },
    getViewport: (out: Vector4) => out.copy(state.viewport),
    setViewport: (x: number | Vector4, y?: number, width?: number, height?: number) => {
      if (x instanceof Vector4) state.viewport.copy(x); else state.viewport.set(x, y!, width!, height!);
    },
    getScissor: (out: Vector4) => out.copy(state.scissor),
    setScissor: (x: number | Vector4, y?: number, width?: number, height?: number) => {
      if (x instanceof Vector4) state.scissor.copy(x); else state.scissor.set(x, y!, width!, height!);
    },
    getScissorTest: () => state.scissorTest, setScissorTest: (value: boolean) => {state.scissorTest = value;},
    getClearColor: (out: Color) => out.copy(state.color), getClearAlpha: () => state.alpha,
    setClearColor: (color: Color | number, alpha = 1) => {state.color.set(color); state.alpha = alpha;},
    clear: () => {},
    render: (scene: Scene, camera: Camera) => {
      scene.updateMatrixWorld(true); camera.updateMatrixWorld();
      const meshes: (typeof state.draws)[number]['meshes'] = [];
      scene.traverseVisible(object => {
        if (!(object instanceof Mesh) || !camera.layers.test(object.layers)) return;
        const materials = materialList(object).filter(material => material.visible);
        if (materials.length) {
          const state = materials[0] instanceof MeshPhysicalMaterial ? canonicalLensRenderState(materials[0]) : undefined;
          meshes.push({mesh: object, geometry: object.geometry, materials, matrixWorld: object.matrixWorld.toArray(),
            mode: state?.uCanonicalLayerMode.value, groupEnabled: state?.uCanonicalGroupEnabled.value,
            nearest: state?.uCanonicalGroupNearest.value});
        }
      });
      state.draws.push({scene, target: state.target, depthClear: state.depthClear, meshes});
      if (state.draws.length === state.failOnDraw) throw new Error('Injected optical draw failure');
    },
    readRenderTargetPixels: (target: WebGLRenderTarget, _x: number, _y: number, width: number, height: number, out: Uint8Array) => {
      state.readbacks.push({target, width, height}); out.fill(0); out[3] = state.overflowFlag;
    },
  };
  const snapshot = () => ({target: state.target, cubeFace: state.cubeFace, mip: state.mip,
    viewport: state.viewport.toArray(), scissor: state.scissor.toArray(), scissorTest: state.scissorTest,
    color: state.color.toArray(), alpha: state.alpha, depthClear: state.depthClear,
    autoClear: backend.autoClear, xr: backend.xr.enabled});
  return {state, renderer: backend as unknown as WebGLRenderer, snapshot, dispose: () => callerTarget.dispose()};
}

function fixture(nested = false, reverse = false, groupMode: 'none' | 'same' | 'distinct' = 'none') {
  const fake = fakeRenderer(), scene = new Scene(), root = new Group(); scene.add(root);
  root.position.set(2, -1, -5); root.rotation.set(.1, .2, .3);
  const originals: Material[] = [];
  const lenses = [0, 1].map(index => {
    const material = new MeshPhysicalMaterial(); originals.push(material);
    material.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: descriptor}};
    const mesh = new Mesh(new PlaneGeometry(.04, .03).translate(index * .05, 0, -.002 * index), material);
    mesh.name = `Lens ${index}`; mesh.userData = {partRole: 'lens', lensSurfaceProfile: 'front_sheet_v1'};
    if (groupMode !== 'none') Object.assign(mesh.userData, {lensSurfaceProfile: EFFECTIVE_OPTICAL_GROUP_PROFILE,
      lensUVConvention: 'lens_local_bottom_0_top_1', opticalGroupId: groupMode === 'same' ? 'one' : `group-${index}`,
      opticalGroupMemberId: `member-${index}`, opticalSourcePartIndex: index, opticalSourceSha256: 'a'.repeat(64),
      lensAppearanceSha256: 'b'.repeat(64), semanticIdentity: 'unverified', materialIdentification: 'unmeasured'});
    return mesh;
  });
  root.add(lenses[0]!);
  if (nested) lenses[0]!.add(lenses[1]!); else root.add(lenses[1]!);
  const opaqueMaterial = new MeshBasicMaterial({color: 0x654321});
  const opaque = new Mesh(new PlaneGeometry(.08, .005), opaqueMaterial); opaque.name = 'Opaque frame child';
  if (nested) lenses[0]!.add(opaque); else root.add(opaque);
  const installed = installCanonicalLensMaterials(root);
  const ordered = reverse ? [...lenses].reverse() : lenses;
  const controller = new CanonicalLensLayers(ordered);
  const camera = new PerspectiveCamera(50, 2, .1, 100); camera.updateMatrixWorld();
  root.updateWorldMatrix(true, true);
  const sourceSnapshot = () => ({
    rootChildren: root.children.map(child => child.uuid), rootVisible: root.visible,
    lenses: lenses.map(mesh => ({visible: mesh.visible, parent: mesh.parent?.uuid,
      children: mesh.children.map(child => child.uuid), material: mesh.material, materialVisible: mesh.material.visible,
      positions: Array.from(mesh.geometry.getAttribute('position').array)})),
    opaqueParent: opaque.parent?.uuid, opaqueVisible: opaque.visible, opaqueMaterialVisible: opaqueMaterial.visible,
  });
  const render = (withOpaqueInput: (draw: () => void) => void = draw => draw()) =>
    controller.render(fake.renderer, scene, camera, 8, 4, withOpaqueInput);
  const dispose = () => {
    controller.dispose(); for (const mesh of lenses) mesh.geometry.dispose(); opaque.geometry.dispose();
    for (const material of [...originals, ...installed.materials, opaqueMaterial]) material.dispose(); fake.dispose();
  };
  return {fake, scene, root, lenses, opaque, installed, controller, camera, sourceSnapshot, render, dispose};
}

function peelDraws(f: ReturnType<typeof fixture>) {
  return f.fake.state.draws.filter(draw => draw.scene !== f.scene && draw.target?.texture.type === FloatType
    && draw.meshes.some(mesh => f.lenses.some(lens => lens.geometry === mesh.geometry)));
}

test('camera compositor requires canonical adapter installation, not just a physical material or metadata', t => {
  const geometry = new PlaneGeometry(), legacy = new MeshPhysicalMaterial(), ordinary = new MeshBasicMaterial();
  t.after(() => {geometry.dispose(); legacy.dispose(); ordinary.dispose();});
  assert.throws(() => new CanonicalLensLayers([new Mesh(geometry, ordinary)]), /installed physical materials/);
  legacy.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: descriptor}};
  assert.throws(() => new CanonicalLensLayers([new Mesh(geometry, legacy)]), /not installed/);
});

test('nested canonical visibility is independent of source list order and opaque children remain in the input', t => {
  for (const reverse of [false, true]) {
    const f = fixture(true, reverse); t.after(f.dispose);
    const before = f.fake.snapshot(), sources = f.sourceSnapshot(); f.render();
    const base = f.fake.state.draws.find(draw => draw.scene === f.scene)!;
    assert.deepEqual(base.meshes.map(entry => entry.mesh), [f.opaque], 'hiding an optical parent must not hide its opaque child');
    const peels = peelDraws(f);
    assert.equal(peels.length, MAX_CANONICAL_OPTICAL_LAYERS + 1);
    for (const peel of peels) {
      assert.equal(peel.meshes.length, 2, 'every visible optical sheet must reach every peel, including nested children');
      for (const lens of f.lenses) {
        const proxy = peel.meshes.find(entry => entry.geometry === lens.geometry)!;
        assert.ok(proxy); assert.deepEqual(proxy.matrixWorld, lens.matrixWorld.toArray());
      }
    }
    assert.deepEqual(f.sourceSnapshot(), sources); assert.deepEqual(f.fake.snapshot(), before);
  }
});

test('hidden ancestors, hidden sheets and hidden materials do not silently reappear as optical proxies', t => {
  const f = fixture(true); t.after(f.dispose);
  f.root.visible = false; f.render();
  assert.equal(peelDraws(f).length, 0, 'an invisible ancestor suppresses the complete optical branch');
  f.root.visible = true; f.lenses[1]!.visible = false; f.fake.state.draws.length = 0; f.render();
  assert.equal(peelDraws(f).length, MAX_CANONICAL_OPTICAL_LAYERS + 1);
  for (const draw of peelDraws(f)) assert.deepEqual(draw.meshes.map(entry => entry.geometry), [f.lenses[0]!.geometry]);
  f.lenses[1]!.visible = true; f.lenses[1]!.material.visible = false; f.fake.state.draws.length = 0; f.render();
  assert.equal(peelDraws(f).length, MAX_CANONICAL_OPTICAL_LAYERS + 1);
  for (const draw of peelDraws(f)) assert.deepEqual(draw.meshes.map(entry => entry.geometry), [f.lenses[0]!.geometry]);
  assert.equal(f.lenses[1]!.material.visible, false, 'caller material visibility is restored exactly');
});

test('draw failures restore all renderer state, source visibility and canonical depth functions', t => {
  for (const failOnDraw of [1, 3, 8, 11, 12]) {
    const f = fixture(); t.after(f.dispose);
    f.lenses[1]!.visible = false; f.lenses[1]!.material.visible = false;
    const before = f.fake.snapshot(), sources = f.sourceSnapshot(); f.fake.state.failOnDraw = failOnDraw;
    assert.throws(() => f.render(), /Injected optical draw failure/);
    assert.deepEqual(f.fake.snapshot(), before, `renderer state after draw ${failOnDraw}`);
    assert.deepEqual(f.sourceSnapshot(), sources, `source state after draw ${failOnDraw}`);
    for (const material of f.installed.materials) assert.equal(material.depthFunc, LessEqualDepth);
    f.fake.state.failOnDraw = 0; assert.doesNotThrow(() => f.render());
  }
});

test('opaque-input callback failure restores nested object and material visibility', t => {
  const f = fixture(true); t.after(f.dispose); f.lenses[1]!.material.visible = false;
  const before = f.fake.snapshot(), sources = f.sourceSnapshot();
  assert.throws(() => f.render(draw => {draw(); throw new Error('Opaque wrapper failed');}), /Opaque wrapper failed/);
  assert.deepEqual(f.sourceSnapshot(), sources); assert.deepEqual(f.fake.snapshot(), before);
  f.lenses[1]!.material.visible = true;
  assert.doesNotThrow(() => f.render());
});

test('fifth-layer occupancy is an explicit failure, not an accepted truncated composition', t => {
  const f = fixture(); t.after(f.dispose);
  const before = f.fake.snapshot(), sources = f.sourceSnapshot(); f.fake.state.overflowFlag = 255;
  assert.throws(() => f.render(), OpticalLayerOverflowError);
  assert.equal(peelDraws(f).length, MAX_CANONICAL_OPTICAL_LAYERS + 1);
  assert.equal(f.fake.state.readbacks.length, 1);
  const readback = f.fake.state.readbacks[0]!;
  assert.equal(readback.width, 1); assert.equal(readback.height, 1);
  assert.equal(readback.target.texture.name, 'Generated optical overflow flag');
  assert.deepEqual(f.sourceSnapshot(), sources); assert.deepEqual(f.fake.snapshot(), before);
  f.fake.state.overflowFlag = 0; assert.doesNotThrow(() => f.render());
});

test('opaque input and every peel use float depth targets; final output is shared by all optical materials', t => {
  const f = fixture(); t.after(f.dispose); const before = f.fake.snapshot();
  f.render();
  const opaque = f.fake.state.draws[0]!; assert.equal(opaque.depthClear, 1);
  const peels = peelDraws(f);
  for (const draw of [opaque, ...peels]) {
    assert.equal(draw.target!.texture.type, FloatType); assert.equal(draw.target!.depthTexture!.type, FloatType);
    assert.equal(draw.target!.width, 8); assert.equal(draw.target!.height, 4);
  }
  for (const draw of peels) assert.equal(draw.depthClear, 0);
  const expected = peels[MAX_CANONICAL_OPTICAL_LAYERS - 1]!.target!.texture;
  for (const material of f.installed.materials) {
    const state = canonicalLensRenderState(material);
    assert.equal(state.uCanonicalLayerMode.value, 2); assert.equal(state.uCanonicalLayerColor.value, expected);
    assert.equal(material.transmission, 0, 'Three must not start a second transmission prepass');
  }
  assert.deepEqual(f.fake.snapshot(), before);
});

test('unavailable float support and invalid dimensions fail before modifying source state', t => {
  const f = fixture(true); t.after(f.dispose); const before = f.fake.snapshot(), sources = f.sourceSnapshot();
  f.fake.state.floatSupported = false;
  assert.throws(() => f.render(), /float color targets/);
  f.fake.state.floatSupported = true;
  for (const [width, height] of [[0, 4], [8, -1], [8.5, 4], [NaN, 4]]) {
    assert.throws(() => f.controller.render(f.fake.renderer, f.scene, f.camera, width!, height!, draw => draw()), /viewport/);
  }
  assert.equal(f.fake.state.draws.length, 0); assert.deepEqual(f.sourceSnapshot(), sources); assert.deepEqual(f.fake.snapshot(), before);
});

test('dispose releases owned targets, quads and shaders once while preserving borrowed source resources', t => {
  const f = fixture(); t.after(f.dispose); f.render();
  const targets = new Set(f.fake.state.draws.map(draw => draw.target).filter(target => target !== null));
  const borrowedGeometry = new Set<BufferGeometry>([f.opaque.geometry, ...f.lenses.map(lens => lens.geometry)]);
  const borrowedMaterials = new Set<Material>([f.opaque.material, ...f.installed.materials]);
  const ownGeometry = new Set(f.fake.state.draws.flatMap(draw => draw.meshes.map(mesh => mesh.geometry))
    .filter(geometry => !borrowedGeometry.has(geometry)));
  const ownMaterials = new Set(f.fake.state.draws.flatMap(draw => draw.meshes.flatMap(mesh => mesh.materials))
    .filter(material => !borrowedMaterials.has(material)));
  let targetDisposals = 0, ownGeometryDisposals = 0, ownMaterialDisposals = 0, borrowedDisposals = 0;
  for (const target of targets) target.addEventListener('dispose', () => {targetDisposals++;});
  for (const geometry of ownGeometry) geometry.addEventListener('dispose', () => {ownGeometryDisposals++;});
  for (const material of ownMaterials) material.addEventListener('dispose', () => {ownMaterialDisposals++;});
  for (const borrowed of [...borrowedGeometry, ...borrowedMaterials]) borrowed.addEventListener('dispose', () => {borrowedDisposals++;});
  const parents = f.lenses.map(lens => lens.parent), children = f.root.children.slice();
  f.controller.dispose(); f.controller.dispose();
  assert.equal(targetDisposals, targets.size); assert.equal(ownGeometryDisposals, ownGeometry.size);
  assert.equal(ownMaterialDisposals, ownMaterials.size); assert.equal(borrowedDisposals, 0);
  assert.deepEqual(f.lenses.map(lens => lens.parent), parents); assert.deepEqual(f.root.children, children);
  const draws = f.fake.state.draws.length;
  assert.throws(() => f.render(), /disposed/); assert.equal(f.fake.state.draws.length, draws);
});

test('effective multipart groups capture once then bind the same nearest map for every member', t => {
  const f = fixture(true, true, 'same'); t.after(f.dispose);
  const before = f.fake.snapshot(), source = f.sourceSnapshot(); f.render();
  const captures = f.fake.state.draws.filter(draw => draw.meshes.some(mesh => mesh.mode === 3));
  assert.equal(captures.length, 1); assert.equal(captures[0]!.meshes.length, 2);
  assert.equal(captures[0]!.depthClear, 1);
  for (const mesh of captures[0]!.meshes) {
    assert.equal(mesh.groupEnabled, false); assert.equal(mesh.nearest, null);
    assert.equal(mesh.materials[0], f.installed.materials[0]);
  }
  const peels = f.fake.state.draws.filter(draw => draw.meshes.some(mesh => mesh.mode === 1));
  assert.equal(peels.length, MAX_CANONICAL_OPTICAL_LAYERS + 1);
  for (const draw of peels) for (const mesh of draw.meshes) {
    assert.equal(mesh.groupEnabled, true); assert.equal(mesh.nearest, captures[0]!.target!.texture);
  }
  const state = canonicalLensRenderState(f.installed.materials[0]!);
  assert.equal(state.uCanonicalLayerMode.value, 2);
  assert.equal(state.uCanonicalGroupNearest.value, captures[0]!.target!.texture);
  assert.deepEqual(f.fake.snapshot(), before); assert.deepEqual(f.sourceSnapshot(), source);
  f.fake.state.draws.length = 0; f.render();
  const next = f.fake.state.draws.find(draw => draw.meshes.some(mesh => mesh.mode === 3))!;
  assert.equal(next.target, captures[0]!.target);
  assert.ok(next.meshes.every(mesh => mesh.nearest === null), 'prior-frame map must not remain sampled during its own capture');
});

test('equal descriptors in distinct effective groups bind distinct owned nearest maps', t => {
  const f = fixture(false, false, 'distinct'); t.after(f.dispose); f.render();
  const captures = f.fake.state.draws.filter(draw => draw.meshes.some(mesh => mesh.mode === 3));
  assert.equal(captures.length, 2); assert.notEqual(captures[0]!.target, captures[1]!.target);
  const textures = f.installed.materials.map(material => canonicalLensRenderState(material).uCanonicalGroupNearest.value);
  assert.notEqual(textures[0], textures[1]);
  assert.deepEqual(new Set(textures), new Set(captures.map(draw => draw.target!.texture)));
});

test('effective mesh membership cannot silently borrow a different group material state', t => {
  const f = fixture(false, false, 'distinct'); t.after(f.dispose);
  f.lenses[1]!.material = f.lenses[0]!.material;
  assert.throws(() => new CanonicalLensLayers(f.lenses), /group bindings differ/);
});

test('effective capture failure restores uniforms, source flags and renderer before a retry', t => {
  const f = fixture(false, false, 'distinct'); t.after(f.dispose); f.render();
  const states = f.installed.materials.map(canonicalLensRenderState);
  const nearest = states.map(state => state.uCanonicalGroupNearest.value);
  const before = f.fake.snapshot(), source = f.sourceSnapshot();
  f.fake.state.draws.length = 0; f.fake.state.failOnDraw = 2; // Opaque input, then first nearest group.
  assert.throws(() => f.render(), /Injected optical draw failure/);
  assert.deepEqual(f.fake.snapshot(), before); assert.deepEqual(f.sourceSnapshot(), source);
  states.forEach((state, i) => {assert.equal(state.uCanonicalGroupEnabled.value, true);
    assert.equal(state.uCanonicalGroupNearest.value, nearest[i]); assert.equal(state.uCanonicalLayerMode.value, 2);});
  f.fake.state.failOnDraw = 0; assert.doesNotThrow(() => f.render());
});

test('effective nearest capture and peels preserve nondefault camera layer selection', t => {
  const f = fixture(false, false, 'distinct'); t.after(f.dispose);
  f.camera.layers.set(2); f.lenses[0]!.layers.set(2); f.render();
  const captures = f.fake.state.draws.filter(draw => draw.meshes.some(mesh => mesh.mode === 3));
  const peels = f.fake.state.draws.filter(draw => draw.meshes.some(mesh => mesh.mode === 1));
  assert.equal(captures.length, 1); assert.equal(peels.length, MAX_CANONICAL_OPTICAL_LAYERS+1);
  for (const draw of [...captures, ...peels]) assert.deepEqual(draw.meshes.map(mesh => mesh.geometry), [f.lenses[0]!.geometry]);
});
