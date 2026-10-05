import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MeshPhysicalMaterial, ShaderMaterial} from 'three';
import {createLensAppearanceUniforms, evaluateLensAppearance, isLensAppearanceMaterial,
  LENS_APPEARANCE_EXTENSION, LENS_RESPONSE_GLSL, MAX_LENS_ANGULAR_KNOTS, MAX_LENS_DENSITY_KNOTS,
  readLensAppearanceExtension, readMaterialLensAppearance, validateLensAppearance} from '../src/eyewear/lens-appearance.ts';
import type {LensAppearanceDescriptor, LensRGB} from '../src/eyewear/lens-appearance.ts';

function descriptor(overrides: Partial<LensAppearanceDescriptor> = {}): LensAppearanceDescriptor {
  return {schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [0.04, 0.04, 0.04], refractive_index: 1.5, roughness: 0.05,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null, ...overrides};
}

function close(actual: readonly number[], expected: readonly number[], tolerance = 2e-14): void {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => assert.ok(Number.isFinite(value) && Math.abs(value - expected[index]!) <= tolerance,
    `channel ${index}: ${value} != ${expected[index]} within ${tolerance}`));
}

function materialExtension(appearance: unknown): unknown {
  return {userData: {gltfExtensions: {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance}}}};
}

test('exact Python descriptor JSON round trips into an immutable detached representation', () => {
  const input = JSON.parse(JSON.stringify(descriptor()));
  const parsed = validateLensAppearance(input);
  assert.deepEqual(JSON.parse(JSON.stringify(parsed)), input);
  input.optical_density_keyframes[0].optical_density_rgb[0] = 9;
  assert.equal(parsed.optical_density_keyframes[0]!.optical_density_rgb[0], 0);
  assert.ok(Object.isFrozen(parsed) && Object.isFrozen(parsed.optical_density_keyframes));
  assert.ok(Object.isFrozen(parsed.optical_density_keyframes[0]) && Object.isFrozen(parsed.normal_reflectance_rgb));
});

test('strict schema rejects undeclared semantics, coercion, absent properties and invalid domains', () => {
  for (const update of [
    {schema_version: '1'}, {schema_version: true}, {schema_version: 2}, {color_space: 'srgb'},
    {density_interpolation: 'linear'}, {vertical_coordinate: 'screen_y'}, {refractive_index: 0.9},
    {refractive_index: Infinity}, {roughness: NaN}, {roughness: -0.1}, {roughness: 1.1},
    {normal_reflectance_rgb: [1.1, 0, 0]}, {normal_reflectance_rgb: [[0], [0], [0]]},
    {normal_reflectance_rgb: [true, 0, 0]}, {optical_density_keyframes: []},
    {optical_density_keyframes: [{v: 0, optical_density_rgb: [-1, 0, 0]}]},
    {optical_density_keyframes: [{v: 0.1, optical_density_rgb: [0, 0, 0]}]},
    {optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0], ignored: true}]},
    {optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}, {v: 0.5, optical_density_rgb: [1, 1, 1]}]},
    {optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}, {v: 0, optical_density_rgb: [1, 1, 1]}]},
    {angular_reflectance_keyframes: []}, {angular_reflectance_keyframes: [{angle_degrees: 0, reflectance_rgb: [0.04, 0.04, 0.04]}]},
    {angular_reflectance_keyframes: [{angle_degrees: 0, reflectance_rgb: [0.1, 0.1, 0.1]}, {angle_degrees: 90, reflectance_rgb: [1, 1, 1]}]},
    {unknown_field: 3},
  ]) assert.throws(() => validateLensAppearance({...descriptor(), ...update}), JSON.stringify(update));
  const missing = JSON.parse(JSON.stringify(descriptor()));
  delete missing.angular_reflectance_keyframes;
  assert.throws(() => validateLensAppearance(missing));
  for (const input of [null, [], 'descriptor', false]) assert.throws(() => validateLensAppearance(input));
});

test('normal-incidence clear and absorbing responses conserve energy without alpha', () => {
  const clear = evaluateLensAppearance(descriptor(), 0.4);
  close(clear.reflectance_rgb, [0.04, 0.04, 0.04]);
  close(clear.transmission_rgb, [0.96, 0.96, 0.96]);
  close(clear.absorption_rgb, [0, 0, 0]);
  const tint = descriptor({optical_density_keyframes: [{v: 0, optical_density_rgb: [Math.log(2), Math.log(4), Math.log(8)]}]});
  close(evaluateLensAppearance(tint, 1).transmission_rgb, [0.48, 0.24, 0.12]);
  close(evaluateLensAppearance(tint, 1).absorption_rgb, [0.48, 0.72, 0.84]);
});

test('optional rear fraction preserves legacy JSON, reciprocal transmission and independently bounded energy', () => {
  const legacy = descriptor({normal_reflectance_rgb: [.8, .5, .2],
    optical_density_keyframes: [{v: 0, optical_density_rgb: [Math.log(2), Math.log(2), Math.log(2)]}]});
  const asymmetric = descriptor({...legacy, rear_reflection_fraction_rgb: [.1, .2, .3]});
  const parsed = validateLensAppearance(asymmetric);
  assert.deepEqual(parsed, asymmetric);
  assert.ok(Object.isFrozen(parsed.rear_reflection_fraction_rgb));
  assert.equal(Object.hasOwn(validateLensAppearance(legacy), 'rear_reflection_fraction_rgb'), false);
  close(evaluateLensAppearance(parsed, .5, 0, 'rear').transmission_rgb, [.1, .25, .4]);
  close(evaluateLensAppearance(parsed, .5, 0, 'rear').reflectance_rgb, [.09, .15, .18]);
  close(evaluateLensAppearance(parsed, .5, 0, 'rear').absorption_rgb, [.81, .6, .42]);
  for (const angle of [0, 20, 60, 89.9, 90]) for (const v of [0, .3, 1]) {
    const front = evaluateLensAppearance(parsed, v, angle), rear = evaluateLensAppearance(parsed, v, angle, 'rear');
    assert.deepEqual(front, evaluateLensAppearance(legacy, v, angle));
    assert.deepEqual(evaluateLensAppearance(legacy, v, angle, 'rear'), front);
    assert.deepEqual(rear.transmission_rgb, front.transmission_rgb);
    for (let c = 0; c < 3; c++) {
      const energy = [rear.reflectance_rgb[c]!, rear.transmission_rgb[c]!, rear.absorption_rgb[c]!];
      assert.ok(energy.every(x => x >= 0 && x <= 1));
      close([energy.reduce((a, b) => a + b)], [1]);
    }
  }
  const uniforms = createLensAppearanceUniforms(parsed);
  assert.equal(uniforms.uLensHasRearResponse.value, 1);
  close(Array.from(uniforms.uLensRearReflectionFraction.value), [.1, .2, .3], 2e-8);
  assert.equal(createLensAppearanceUniforms(legacy).uLensHasRearResponse.value, 0);
  for (const bad of [null, undefined, [], [0, 0], [-.1, 0, 0], [1.1, 0, 0], [true, 0, 0], [NaN, 0, 0]])
    assert.throws(() => validateLensAppearance({...legacy, rear_reflection_fraction_rgb: bad}));
  assert.throws(() => evaluateLensAppearance(parsed, .5, 0, 'unknown' as 'front'), /side/);
});

test('gradient and angled attenuation agree with independent saved Python reference values', () => {
  const gradient = descriptor({optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]},
    {v: 1, optical_density_rgb: [Math.log(16), Math.log(4), Math.log(2)]}]});
  const low = evaluateLensAppearance(gradient, 0.25, 0);
  close(low.optical_density_rgb, [0.4332169878499658, 0.2166084939249829, 0.10830424696249145]);
  close(low.transmission_rgb, [0.6224829862324847, 0.773035359335642, 0.8614603560014914]);
  close(low.absorption_rgb, [0.3375170137675153, 0.18696464066435795, 0.09853964399850854]);
  const angled = evaluateLensAppearance(gradient, 0.5, 60);
  close(angled.reflectance_rgb, [0.07, 0.07, 0.07]);
  close(angled.transmission_rgb, [0.17026016969324223, 0.39792204992273966, 0.6083317404411411]);
  close(angled.absorption_rgb, [0.7597398303067578, 0.5320779500772603, 0.3216682595588589]);
});

test('colored angular coating interpolates its own curve without changing intrinsic density', () => {
  const coating = descriptor({normal_reflectance_rgb: [0.3, 0.5, 0.7], refractive_index: 1.7, roughness: 0.12,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0.1, 0.8, 2]}], angular_reflectance_keyframes: [
      {angle_degrees: 0, reflectance_rgb: [0.3, 0.5, 0.7]}, {angle_degrees: 45, reflectance_rgb: [0.8, 0.6, 0.2]},
      {angle_degrees: 90, reflectance_rgb: [1, 1, 1]}]});
  const result = evaluateLensAppearance(coating, 0.6, 22.5);
  close(result.reflectance_rgb, [0.55, 0.55, 0.44999999999999996]);
  close(result.transmission_rgb, [0.4061056536197346, 0.19798153551163636, 0.07061437599073282]);
  const high = evaluateLensAppearance(coating, 1, 76);
  close(high.reflectance_rgb, [0.9539709190672154, 0.9079418381344307, 0.8158836762688615]);
  close(high.transmission_rgb, [0.040751303679269726, 0.03474848918896339, 0.01611670899420222]);
  close(high.optical_density_rgb, [0.1, 0.8, 2]);
});

test('grazing incidence and zero density remain finite including index-one limiting case', () => {
  const appearance = descriptor({refractive_index: 1, normal_reflectance_rgb: [0.2, 0.3, 0.4],
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 1, 1e-10]}], angular_reflectance_keyframes: [
      {angle_degrees: 0, reflectance_rgb: [0.2, 0.3, 0.4]}, {angle_degrees: 90, reflectance_rgb: [0.2, 0.3, 0.4]}]});
  const sample = evaluateLensAppearance(appearance, 0.3, 90);
  close(sample.transmission_rgb, [0.8, 0, 0]);
  close(sample.absorption_rgb, [0, 0.7, 0.6]);
  const strong = descriptor({optical_density_keyframes: [{v: 0, optical_density_rgb: [1e300, 1e-12, 0]}]});
  for (const v of [0, 0.01, 0.5, 0.99, 1]) for (const angle of [0, 15, 45, 60, 89.999, 90]) {
    const result = evaluateLensAppearance(strong, v, angle);
    for (let channel = 0; channel < 3; channel++) {
      const values = [result.reflectance_rgb[channel]!, result.transmission_rgb[channel]!, result.absorption_rgb[channel]!];
      assert.ok(values.every(value => Number.isFinite(value) && value >= 0 && value <= 1));
      assert.ok(Math.abs(values.reduce((a, b) => a + b) - 1) < 1e-14);
    }
  }
});

test('invalid response coordinates are rejected rather than clamped', () => {
  for (const value of [-0.001, 1.001, NaN, Infinity]) assert.throws(() => evaluateLensAppearance(descriptor(), value));
  for (const angle of [-0.001, 90.001, NaN, Infinity]) assert.throws(() => evaluateLensAppearance(descriptor(), 0.5, angle));
});

test('mirrored zero-transmission materials preserve lens identity from the extension', () => {
  const mirror = descriptor({normal_reflectance_rgb: [1, 1, 1]});
  const material = new MeshPhysicalMaterial({transmission: 0, metalness: 1});
  material.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: mirror}};
  assert.equal(isLensAppearanceMaterial(material), true);
  assert.deepEqual(readMaterialLensAppearance(material), mirror);
  close(evaluateLensAppearance(readMaterialLensAppearance(material)!, 0.5, 45).transmission_rgb, [0, 0, 0]);
  close(evaluateLensAppearance(readMaterialLensAppearance(material)!, 0.5, 45).reflectance_rgb, [1, 1, 1]);
  assert.equal(readLensAppearanceExtension(material)!.texcoord, 0);
  assert.equal(isLensAppearanceMaterial(new MeshPhysicalMaterial({transmission: 1})), false);
  assert.equal(readMaterialLensAppearance({}), null);
  material.dispose();
});

test('unsupported/malformed extension is an explicit error and cannot fall back to guessed material properties', () => {
  for (const extension of [null, descriptor(), {schema_version: 2, texcoord: 0, appearance: descriptor()},
    {schema_version: 1, texcoord: 1, appearance: descriptor()}, {schema_version: 1, texcoord: '0', appearance: descriptor()},
    {schema_version: 1, texcoord: 0, appearance: descriptor(), silent: true}]) {
    assert.throws(() => readMaterialLensAppearance({transmission: 1, userData: {gltfExtensions: {[LENS_APPEARANCE_EXTENSION]: extension}}}));
  }
  assert.deepEqual(readMaterialLensAppearance(materialExtension(descriptor())), descriptor());
});

test('uniforms preserve knots and are accepted directly by Three ShaderMaterial without mutating the descriptor', () => {
  const original = descriptor({optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0.3, 0.6]},
    {v: 1, optical_density_rgb: [1, 2, 3]}]});
  const uniforms = createLensAppearanceUniforms(original);
  assert.equal(uniforms.uLensDensityCount.value, 2);
  assert.equal(uniforms.uLensDensityPositions.value.length, MAX_LENS_DENSITY_KNOTS);
  assert.equal(uniforms.uLensDensities.value.length, MAX_LENS_DENSITY_KNOTS * 3);
  assert.equal(uniforms.uLensAngularCount.value, 0);
  assert.equal(uniforms.uLensAnglePositions.value.length, MAX_LENS_ANGULAR_KNOTS);
  close(Array.from(uniforms.uLensDensities.value.slice(0, 6)), [0, 0.3, 0.6, 1, 2, 3], 3e-8);
  const shader = new ShaderMaterial({uniforms, fragmentShader: LENS_RESPONSE_GLSL});
  assert.equal(shader.uniforms.uLensDensityCount!.value, 2);
  uniforms.uLensDensities.value[0] = 50;
  assert.equal(original.optical_density_keyframes[0]!.optical_density_rgb[0], 0);
  shader.dispose();
});

test('GPU capacity and precision failures are rejected while CPU schema retains its broader domain', () => {
  const many = descriptor({optical_density_keyframes: Array.from({length: MAX_LENS_DENSITY_KNOTS + 1}, (_, i) =>
    ({v: i / MAX_LENS_DENSITY_KNOTS, optical_density_rgb: [i, i, i] as LensRGB}))});
  assert.doesNotThrow(() => validateLensAppearance(many));
  assert.throws(() => createLensAppearanceUniforms(many), /at most/);
  const manyAngles = descriptor({angular_reflectance_keyframes: Array.from({length: MAX_LENS_ANGULAR_KNOTS + 1}, (_, i) =>
    ({angle_degrees: i * 90 / MAX_LENS_ANGULAR_KNOTS, reflectance_rgb: [0.04, 0.04, 0.04] as LensRGB}))});
  assert.throws(() => createLensAppearanceUniforms(manyAngles), /at most/);
  const collapsed = descriptor({optical_density_keyframes: [0, 0.5, 0.5 + 1e-9, 1].map(v => ({v, optical_density_rgb: [0, 1, 2]}))});
  assert.throws(() => createLensAppearanceUniforms(collapsed), /collapse/);
  for (const density of [1e40, 1e-100]) {
    const unsupported = descriptor({optical_density_keyframes: [{v: 0, optical_density_rgb: [density, 0, 0]}]});
    assert.doesNotThrow(() => evaluateLensAppearance(unsupported, 0.5));
    assert.throws(() => createLensAppearanceUniforms(unsupported), /GPU float/);
  }
  assert.throws(() => createLensAppearanceUniforms(descriptor({refractive_index: 1 + 1e-9})), /grazing singularity/);
});
