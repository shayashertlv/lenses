import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ACESFilmicToneMapping, Material, Mesh, MeshBasicMaterial, MeshPhysicalMaterial, NoToneMapping,
  PlaneGeometry, RawShaderMaterial, ReinhardToneMapping, Scene, ShaderChunk, ShaderLib, ShaderMaterial, Texture} from 'three';
import type {WebGLRenderer} from 'three';
import {OpaqueDisplayCapture} from '../src/render/opaque-display.ts';

type Shader = Parameters<Material['onBeforeCompile']>[0];
const renderer = (toneMapping: WebGLRenderer['toneMapping'] = ACESFilmicToneMapping, toneMappingExposure = 1) =>
  ({toneMapping, toneMappingExposure} as WebGLRenderer);

function compile(material: Material, fragmentShader = ShaderLib.physical.fragmentShader, backend = renderer()): Shader {
  const shader = {vertexShader: ShaderLib.physical.vertexShader, fragmentShader, uniforms: {}} as Shader;
  material.onBeforeCompile(shader, backend);
  return shader;
}

function fixture() {
  const material = new MeshPhysicalMaterial(), geometry = new PlaneGeometry(), scene = new Scene();
  scene.add(new Mesh(geometry, material));
  const helper = new OpaqueDisplayCapture();
  return {material, geometry, scene, helper, dispose: () => {helper.dispose(); material.dispose(); geometry.dispose();}};
}

test('capture preserves existing compile hooks, this binding and dynamic cache keys', t => {
  const f = fixture(); t.after(f.dispose);
  let calls = 0;
  const customUniform = {value: .7};
  const hook: Material['onBeforeCompile'] = function(this: Material, shader, backend) {
    assert.equal(this, f.material); assert.equal(backend.toneMappingExposure, 2);
    calls++; shader.uniforms.existing = customUniform; shader.fragmentShader = '// Existing frame customization\n' + shader.fragmentShader;
  };
  const key = function(this: Material) {assert.equal(this, f.material); return `${this.name}:existing-v3`;};
  f.material.name = 'frame-A'; f.material.onBeforeCompile = hook; f.material.customProgramCacheKey = key;
  f.helper.prepare(f.scene);
  assert.equal(f.material.customProgramCacheKey(), 'frame-A:existing-v3|opaque-display-capture-v1');
  f.material.name = 'frame-B';
  assert.equal(f.material.customProgramCacheKey(), 'frame-B:existing-v3|opaque-display-capture-v1');
  const shader = compile(f.material, undefined, renderer(ACESFilmicToneMapping, 2));
  assert.equal(calls, 1); assert.equal(shader.uniforms.existing, customUniform);
  assert.ok(shader.fragmentShader.includes('// Existing frame customization'));
  f.helper.dispose(); assert.equal(f.material.onBeforeCompile, hook); assert.equal(f.material.customProgramCacheKey, key);
});

test('default cache keys preserve the original hook identity without referring recursively to the wrapper', t => {
  const f = fixture(); t.after(f.dispose);
  const original = f.material.onBeforeCompile;
  assert.equal(f.material.customProgramCacheKey, Material.prototype.customProgramCacheKey);
  f.helper.prepare(f.scene);
  assert.equal(f.material.customProgramCacheKey(), `${original.toString()}|opaque-display-capture-v1`);
  const version = f.material.version, wrapper = f.material.onBeforeCompile;
  f.helper.prepare(f.scene); f.helper.prepare(f.scene);
  assert.equal(f.material.onBeforeCompile, wrapper); assert.equal(f.material.version, version);
});

test('offscreen ACES uses the pinned Three response and separate exposure, while native tone mapping remains single', t => {
  const f = fixture(); t.after(f.dispose); f.helper.prepare(f.scene);
  const shader = compile(f.material), text = shader.fragmentShader;
  assert.ok(text.includes(ShaderChunk.tonemapping_pars_fragment), 'use the pinned Three implementation rather than a divergent approximation');
  assert.match(text, /#ifndef TONE_MAPPING\s+#define toneMappingExposure uOpaqueDisplayExposure/);
  assert.match(text, /#undef toneMappingExposure\s+#endif\s+uniform float uOpaqueDisplayCapture/);
  assert.match(text, /#ifdef TONE_MAPPING\s+#include <tonemapping_fragment>\s+#else\s+if \(uOpaqueDisplayCapture > 0\.5\) gl_FragColor\.rgb = ACESFilmicToneMapping\(gl_FragColor\.rgb\);\s+#endif/);
  assert.equal(text.split('#include <tonemapping_fragment>').length, 2);
  assert.equal(text.split('#include <colorspace_fragment>').length, 2, 'output encoding remains with the original shader');
  assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 0);
  f.helper.withCapture(renderer(ACESFilmicToneMapping, 1.8), () => {
    assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 1);
    assert.equal(shader.uniforms.uOpaqueDisplayExposure!.value, 1.8);
  });
  assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 0);
});

test('capture exposure and flags restore after exceptions and nested NoToneMapping scopes without recompiles', t => {
  const f = fixture(); t.after(f.dispose); f.helper.prepare(f.scene);
  const shader = compile(f.material), version = f.material.version, key = f.material.customProgramCacheKey();
  assert.throws(() => f.helper.withCapture(renderer(ACESFilmicToneMapping, 2.4), () => {
    assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 1);
    f.helper.withCapture(renderer(NoToneMapping, .6), () => {
      assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 0);
      assert.equal(shader.uniforms.uOpaqueDisplayExposure!.value, .6);
    });
    assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 1);
    assert.equal(shader.uniforms.uOpaqueDisplayExposure!.value, 2.4);
    throw new Error('Captured draw failed');
  }), /Captured draw failed/);
  assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 0);
  assert.equal(shader.uniforms.uOpaqueDisplayExposure!.value, 1);
  assert.equal(f.material.version, version); assert.equal(f.material.customProgramCacheKey(), key);
  const laterOffscreen = compile(f.material);
  assert.equal(laterOffscreen.uniforms.uOpaqueDisplayCapture!.value, 0, 'ordinary offscreen draws do not inherit capture');
  assert.equal(f.helper.withCapture(renderer(), () => 42), 42);
});

test('capture skips camera background, color masks and canonical untone-mapped materials', t => {
  const f = fixture(), mask = new MeshBasicMaterial({colorWrite: false}), canonical = new MeshPhysicalMaterial({toneMapped: false});
  const background = new Texture(); f.scene.background = background;
  t.after(() => {f.dispose(); mask.dispose(); canonical.dispose(); background.dispose();});
  const maskHook = mask.onBeforeCompile, canonicalHook = canonical.onBeforeCompile;
  f.scene.add(new Mesh(f.geometry, [mask, canonical])); f.helper.prepare(f.scene);
  assert.equal(mask.onBeforeCompile, maskHook); assert.equal(canonical.onBeforeCompile, canonicalHook);
  assert.equal(f.scene.background, background);
  const shader = compile(f.material);
  f.material.toneMapped = false;
  f.helper.withCapture(renderer(), () => assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 0));
  f.material.toneMapped = true; f.material.colorWrite = false;
  f.helper.withCapture(renderer(), () => assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 0));
  f.material.colorWrite = true;
  f.helper.withCapture(renderer(), () => assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 1));
});

test('prepare discovers newly added and newly eligible materials and restores in-scope additions', t => {
  const f = fixture(), later = new MeshBasicMaterial({toneMapped: false});
  t.after(() => {f.dispose(); later.dispose();});
  f.scene.add(new Mesh(f.geometry, later)); f.helper.prepare(f.scene);
  const old = later.onBeforeCompile; later.toneMapped = true;
  f.helper.withCapture(renderer(ACESFilmicToneMapping, 1.3), () => {
    f.helper.prepare(f.scene); assert.notEqual(later.onBeforeCompile, old);
    const shader = compile(later, ShaderLib.basic.fragmentShader);
    assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 1);
    assert.equal(shader.uniforms.uOpaqueDisplayExposure!.value, 1.3);
  });
  assert.equal(compile(later, ShaderLib.basic.fragmentShader).uniforms.uOpaqueDisplayCapture!.value, 0);
});

test('unsupported tone mapping or exposure fails before drawing and leaves flags inactive', t => {
  const f = fixture(); t.after(f.dispose); f.helper.prepare(f.scene); const shader = compile(f.material);
  let draws = 0;
  assert.throws(() => f.helper.withCapture(renderer(ReinhardToneMapping), () => {draws++;}), /only ACES/);
  for (const exposure of [NaN, Infinity, -.1]) {
    assert.throws(() => f.helper.withCapture(renderer(ACESFilmicToneMapping, exposure), () => {draws++;}), /exposure/);
  }
  assert.equal(draws, 0); assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 0);
});

test('unsupported custom shaders fail explicitly after prior hooks, and raw shaders fail before mutation', t => {
  const f = fixture(); t.after(f.dispose); f.helper.prepare(f.scene);
  assert.throws(() => compile(f.material, 'void main() {gl_FragColor=vec4(1.0);}'), /exactly one/);
  assert.throws(() => compile(f.material, '#include <tonemapping_fragment>\n#include <tonemapping_fragment>'), /exactly one/);
  assert.throws(() => compile(f.material, '#include <tonemapping_pars_fragment>\n#include <tonemapping_fragment>'), /custom tone-mapping declarations/);
  const custom = new ShaderMaterial({fragmentShader: 'void main() {gl_FragColor=vec4(1.0);}', toneMapped: true});
  const raw = new RawShaderMaterial(), valid = new MeshBasicMaterial(), other = new OpaqueDisplayCapture(), scene = new Scene();
  t.after(() => {custom.dispose(); raw.dispose(); valid.dispose(); other.dispose();});
  scene.add(new Mesh(f.geometry, valid), new Mesh(f.geometry, raw));
  const validHook = valid.onBeforeCompile;
  assert.throws(() => other.prepare(scene), /raw shader/); assert.equal(valid.onBeforeCompile, validHook);
  scene.clear().add(new Mesh(f.geometry, custom)); other.prepare(scene);
  assert.throws(() => compile(custom, custom.fragmentShader), /exactly one/);
});

test('disposing restores owned hooks only, leaves later owners intact and never disposes borrowed resources', t => {
  const f = fixture(); t.after(f.dispose); const original = f.material.onBeforeCompile;
  f.helper.prepare(f.scene); const shader = compile(f.material), wrapped = f.material.onBeforeCompile;
  const laterHook: Material['onBeforeCompile'] = function(this: Material, value, backend) {wrapped.call(this, value, backend);};
  const laterKey = () => 'later-owner'; f.material.onBeforeCompile = laterHook; f.material.customProgramCacheKey = laterKey;
  let disposals = 0; f.material.addEventListener('dispose', () => {disposals++;});
  f.helper.dispose(); f.helper.dispose();
  assert.equal(f.material.onBeforeCompile, laterHook); assert.equal(f.material.customProgramCacheKey, laterKey);
  assert.equal(disposals, 0); assert.equal(shader.uniforms.uOpaqueDisplayCapture!.value, 0);
  const after = compile(f.material);
  assert.equal(after.uniforms.uOpaqueDisplayCapture, undefined, 'a retained disposed hook delegates without adding the transform');
  assert.notEqual(f.material.onBeforeCompile, original, 'the later owner is not overwritten');
  assert.throws(() => f.helper.prepare(f.scene), /disposed/);
  assert.throws(() => f.helper.withCapture(renderer(), () => {}), /disposed/);
});

test('a material cannot be wrapped by two active display capture helpers', t => {
  const f = fixture(), second = new OpaqueDisplayCapture(); t.after(() => {second.dispose(); f.dispose();});
  f.helper.prepare(f.scene); assert.throws(() => second.prepare(f.scene), /another capture/);
  f.helper.dispose(); assert.doesNotThrow(() => second.prepare(f.scene));
  assert.equal(compile(f.material).fragmentShader.split(ShaderChunk.tonemapping_pars_fragment).length, 2);
});
