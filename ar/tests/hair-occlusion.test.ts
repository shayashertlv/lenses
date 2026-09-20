import assert from 'node:assert/strict';
import {test} from 'node:test';
import type {TestContext} from 'node:test';
import {
  BoxGeometry, CanvasTexture, DataTexture, Group, Material, Matrix3, Mesh, MeshPhysicalMaterial,
  MeshStandardMaterial, NearestFilter, ShaderLib, UniformsUtils, Vector2, Vector3,
} from 'three';
import {createHairOcclusion} from '../src/render/hair-occlusion.ts';
import {createTempleBlendConfiguration, createTempleClip} from '../src/render/temple-clip.ts';

type CompileInput = Parameters<Material['onBeforeCompile']>[0];
function compile(material: Material) {
  const standard = ShaderLib.standard!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader'> = {
    uniforms: UniformsUtils.clone(standard.uniforms), vertexShader: standard.vertexShader, fragmentShader: standard.fragmentShader,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, {}]);
  return shader;
}

function fixture(t: TestContext) {
  const geometry = new BoxGeometry(), frame = new MeshStandardMaterial(), overlay = frame.clone();
  const lens = new MeshPhysicalMaterial({transmission: 1});
  const root = new Group().add(new Mesh(geometry, frame), new Mesh(geometry, frame),
    new Mesh(geometry, overlay), new Mesh(geometry, lens));
  const camera = new CanvasTexture({width: 32, height: 24} as HTMLCanvasElement);
  const mask = new DataTexture(new Uint8Array(64).fill(255), 8, 8);
  mask.minFilter = NearestFilter; mask.magFilter = NearestFilter;
  const hair = createHairOcclusion(root);
  t.after(() => {hair.dispose(); geometry.dispose(); frame.dispose(); overlay.dispose(); lens.dispose(); camera.dispose(); mask.dispose();});
  return {hair, frame, overlay, lens, camera, mask};
}

test('the optional feather shares current uniforms across native/overlay materials and leaves lenses untouched', t => {
  const {hair, frame, overlay, lens, camera, mask} = fixture(t);
  const native = compile(frame), relief = compile(overlay), optics = compile(lens);
  assert.equal(native.uniforms.hairOcclusionFeatherPx!.value, 0);
  for (const name of ['hairOcclusionFeatherPx', 'hairOcclusionMask', 'hairOcclusionViewport', 'hairOcclusionCamera',
    'hairOcclusionCameraUv', 'hairOcclusionMaskWarp', 'hairOcclusionMaskUv']) {
    assert.equal(native.uniforms[name], relief.uniforms[name], name);
    assert.equal(optics.uniforms[name], undefined, 'transmission material is not wrapped');
  }
  assert.equal(native.fragmentShader.match(/uniform float hairOcclusionFeatherPx;/g)!.length, 1,
    'a material shared by two meshes is wrapped once');
  hair.prepareRender(mask, 640, 480, camera, 2);
  assert.equal(native.uniforms.hairOcclusionFeatherPx!.value, 2);
  assert.deepEqual(native.uniforms.hairOcclusionViewport!.value.toArray(), [640, 480]);
  assert.equal(native.uniforms.hairOcclusionMask!.value, mask);
  assert.equal(native.uniforms.hairOcclusionCamera!.value, camera);
  assert.equal(mask.minFilter, NearestFilter); assert.equal(mask.magFilter, NearestFilter);
  assert.ok(mask.image.data!.every(value => value === 255), 'presentation must not rewrite categorical evidence');
  hair.dispose();
  assert.equal(native.uniforms.hairOcclusionFeatherPx!.value, 0);
  assert.equal(native.uniforms.hairOcclusionMask!.value, null);
  assert.doesNotMatch(compile(frame).fragmentShader, /hairOcclusionFeatherPx/);
});

test('feather validation is atomic and omitted or zero radius restores the legacy lookup on the next frame', t => {
  const {hair, frame, camera, mask} = fixture(t), shader = compile(frame);
  hair.prepareRender(mask, 640, 480, camera, 2);
  const version = frame.version;
  for (const radius of [-.01, 3.01, Infinity, -Infinity, NaN]) {
    assert.throws(() => hair.prepareRender(null, 1, 1, null, radius), /between 0 and 3 render pixels/);
    assert.equal(shader.uniforms.hairOcclusionFeatherPx!.value, 2);
    assert.equal(shader.uniforms.hairOcclusionMask!.value, mask);
    assert.equal(shader.uniforms.hairOcclusionCamera!.value, camera);
    assert.deepEqual(shader.uniforms.hairOcclusionViewport!.value.toArray(), [640, 480]);
  }
  hair.prepareRender(mask, 640, 480, camera);
  assert.equal(shader.uniforms.hairOcclusionFeatherPx!.value, 0);
  hair.prepareRender(mask, 640, 480, camera, 3);
  assert.equal(shader.uniforms.hairOcclusionFeatherPx!.value, 3);
  hair.prepareRender(null, 320, 240, null, 0);
  assert.equal(shader.uniforms.hairOcclusionFeatherPx!.value, 0);
  assert.equal(shader.uniforms.hairOcclusionMask!.value, null);
  assert.equal(shader.uniforms.hairOcclusionCamera!.value, null);
  assert.equal(frame.version, version, 'presentation rebinding needs no material recompile');
  assert.match(shader.fragmentShader,
    /float hairWeight = texture2D\(hairOcclusionMask, hairLookupUV\)\.r \* hairInside;\s*if \(hairOcclusionFeatherPx > 0\.0\)/,
    'zero radius takes the unchanged one-texel lookup');
});

// Read the tap positions/weights from the emitted shader, then evaluate their categorical coverage
// against controlled masks. This is a numeric shader-contract test, not a GPU-rendering substitute.
function emittedKernel(fragment: string) {
  const taps = [...fragment.matchAll(/hairWeight \+= ([\d.]+) \* sampleHairOcclusion\(hairMaskUV \+ vec2\(([-\d.]+), ([-\d.]+)\) \* hairFeatherStep\);/g)]
    .map(([, weight, x, y]) => ({weight: Number(weight), x: Number(x), y: Number(y)}));
  assert.equal(taps.length, 9);
  assert.equal(taps.reduce((sum, tap) => sum + tap.weight, 0), 16);
  assert.match(fragment, /hairWeight \/= 16\.0;/);
  assert.match(fragment, /vec2 hairFeatherStep = vec2\(hairOcclusionFeatherPx\) \/ hairOcclusionViewport;/);
  return taps;
}

test('the emitted tent preserves clear/covered interiors and has known symmetric edge coverage in render pixels', t => {
  const {frame} = fixture(t), taps = emittedKernel(compile(frame).fragmentShader);
  const sample = (u: number, v: number, hairAt: (x: number, y: number) => number,
    warp = new Matrix3(), render = new Vector2(16, 16), maskSize = new Vector2(8, 8), radius = 2) =>
    taps.reduce((sum, tap) => {
      const p = new Vector3(u + tap.x * radius / render.x, v + tap.y * radius / render.y, 1).applyMatrix3(warp);
      if (p.x < 0 || p.x > 1 || p.y < 0 || p.y > 1) return sum;
      const x = Math.min(maskSize.x - 1, Math.floor(p.x * maskSize.x));
      const y = Math.min(maskSize.y - 1, Math.floor(p.y * maskSize.y));
      return sum + tap.weight * hairAt(x, y) / 16;
    }, 0);
  assert.equal(sample(.5, .5, () => 0), 0);
  assert.equal(sample(.5, .5, () => 1), 1);
  const rightHalf = (x: number) => Number(x >= 4), leftHalf = (x: number) => Number(x < 4);
  assert.equal(sample(.25, .5, rightHalf), 0, 'clear beyond the finite support is unchanged');
  assert.equal(sample(.375, .5, rightHalf), .25);
  assert.equal(sample(.5, .5, rightHalf), .75);
  assert.equal(sample(.625, .5, rightHalf), 1, 'covered beyond the finite support remains fully hidden');
  assert.equal(sample(.375, .5, leftHalf), .75);
  assert.equal(sample(.5, .5, leftHalf), .25);
  assert.equal(sample(.5, .5, (x, y) => Number(x >= 4 && y >= 4)), 9 / 16, 'corner coverage uses both tent axes');
  assert.equal(sample(.5, .5, x => Number(x >= 8), new Matrix3(), new Vector2(16, 16), new Vector2(16, 16)), .75,
    'changing mask resolution keeps the radius in draw pixels');
  const translate = new Matrix3().set(1, 0, .25, 0, 1, 0, 0, 0, 1);
  assert.equal(sample(.25, .5, rightHalf, translate), .75);
  const rotateScale = new Matrix3().set(0, -2, 1.5, 2, 0, -.5, 0, 0, 1);
  assert.equal(sample(.5, .5, rightHalf, rotateScale), .75, 'every offset rotates and scales into the reused mask');
  const atRightBorder = new Matrix3().set(1, 0, .5, 0, 1, 0, 0, 0, 1);
  assert.equal(sample(.5, .5, () => 1, atRightBorder), .75, 'outside taps are zero, not clamped edge hair');
  assert.equal(sample(.5, .5, () => 1, new Matrix3().set(1, 0, 1, 0, 1, 0, 0, 0, 1)), 0);
});

test('each feather tap follows the same top-left mask warp and tests its own bounds', t => {
  const {hair, frame} = fixture(t), shader = compile(frame);
  const helper = shader.fragmentShader.slice(shader.fragmentShader.indexOf('float sampleHairOcclusion('),
    shader.fragmentShader.indexOf('float sampleHairOcclusion(') + 550);
  assert.match(helper, /hairOcclusionMaskWarp > 0\.5 \? \(hairOcclusionMaskUv \* vec3\(screenUV, 1\.0\)\)\.xy : screenUV/);
  assert.match(helper, /step\(0\.0, lookupUV\.x\) \* step\(lookupUV\.x, 1\.0\) \* step\(0\.0, lookupUV\.y\) \* step\(lookupUV\.y, 1\.0\)/);
  assert.match(helper, /return texture2D\(hairOcclusionMask, lookupUV\)\.r \* inside;/);
  assert.match(shader.fragmentShader, /vec2 hairMaskUV = vec2\(hairScreen\.x, 1\.0 - hairScreen\.y\);/);
  const transform = [0, -2, 1.5, 2, 0, -.5, 0, 0, 1];
  hair.setMaskUv(transform);
  assert.equal(shader.uniforms.hairOcclusionMaskWarp!.value, 1);
  assert.deepEqual(shader.uniforms.hairOcclusionMaskUv!.value.elements, new Matrix3().set(...transform as [number, number, number, number, number, number, number, number, number]).elements);
  hair.setMaskUv(null);
  assert.equal(shader.uniforms.hairOcclusionMaskWarp!.value, 0);
  assert.deepEqual(shader.uniforms.hairOcclusionMaskUv!.value.elements, new Matrix3().elements);
});

test('the feather preserves the endpoint clip and composes before its dissolve', t => {
  const geometry = new BoxGeometry(), frame = new MeshStandardMaterial(), root = new Group().add(new Mesh(geometry, frame));
  const clip = createTempleClip(root), hair = createHairOcclusion(root), shader = compile(frame);
  t.after(() => {hair.dispose(); clip.dispose(); geometry.dispose(); frame.dispose();});
  clip.set(createTempleBlendConfiguration(-.1));
  const source = shader.fragmentShader;
  assert.ok(source.indexOf('discard;') < source.indexOf('float hairWeight'));
  assert.ok(source.indexOf('hairWeight /= 16.0') < source.indexOf('float templeBlendEndpointZ'),
    'the endpoint dissolve still composes last');
  assert.match(source, /gl_FragColor\.rgb = mix\(gl_FragColor\.rgb, hairCameraRGB, hairWeight\);/);
  assert.match(source, /gl_FragColor\.rgb = mix\(templeCameraRGB, gl_FragColor\.rgb, templeBlendWeight\);/);
  const hairFooter = source.slice(source.indexOf('float hairWeight'), source.indexOf('float templeBlendEndpointZ'));
  assert.doesNotMatch(hairFooter, /gl_FragColor\.a\s*=/, 'the filter leaves coverage alpha and depth ownership intact');
});
