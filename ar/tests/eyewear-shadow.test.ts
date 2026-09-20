import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  Box3, BufferAttribute, BufferGeometry, CanvasTexture, Color, DataTexture, Group, Mesh, MeshPhysicalMaterial,
  MeshStandardMaterial, PerspectiveCamera, Scene, SRGBColorSpace, Texture, Vector2, Vector3, Vector4,
  WebGLRenderTarget,
} from 'three';
import type {Camera, Material, Matrix3, OrthographicCamera, ShaderMaterial, WebGLRenderer} from 'three';
import {EyewearShadow, DEFAULT_SHADOW_SETTINGS, lensShadowTransmission, normalizeShadowSettings} from '../src/render/eyewear-shadow.ts';
import {createTempleBlendConfiguration} from '../src/render/temple-clip.ts';

function backendFixture() {
  const initialTarget = new WebGLRenderTarget(16, 12);
  const state = {
    target: initialTarget as WebGLRenderTarget | null, face: 2, mip: 1,
    viewport: new Vector4(7, 8, 120, 80), scissor: new Vector4(2, 3, 40, 30), scissorTest: true,
    color: new Color(0x123456), alpha: .6,
    draws: [] as {scene: Scene; camera: Camera; target: WebGLRenderTarget | null; meshes: Mesh[]}[],
    clearCount: 0, failOnDraw: 0,
  };
  const backend = {
    capabilities: {samples: 4}, autoClear: false, xr: {enabled: true},
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
      state.draws.push({scene, camera, target: state.target, meshes});
      if (state.failOnDraw === state.draws.length) throw new Error('Injected shadow draw failure');
    },
  };
  const snapshot = () => ({target: state.target, face: state.face, mip: state.mip,
    viewport: state.viewport.toArray(), scissor: state.scissor.toArray(), scissorTest: state.scissorTest,
    color: state.color.getHex(), alpha: state.alpha, autoClear: backend.autoClear, xr: backend.xr.enabled});
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
