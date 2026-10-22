/** Owner exact angle colours (angle-colour.ts): the spec is validated strictly, sampled exactly like the shader, and
 *  installed only on an iridescent physical lens, replacing only three's thin-film colour term. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MeshPhysicalMaterial, MeshStandardMaterial, ShaderChunk} from 'three';
import {ANGLE_COLOUR_KEY, ANGLE_COLOUR_SCHEMA, angleColourOf, applyAngleColour, sampleAngleColour, type AngleColourSpec} from '../src/eyewear/angle-colour.ts';

const purple: [number, number, number] = [.42, .17, .58], green: [number, number, number] = [.1, .52, .25];
const spec = (softness = [1], stops = [{colour: purple, position: .35}, {colour: green, position: .75}], reflection = true, tint = .6): AngleColourSpec =>
  ({schema: ANGLE_COLOUR_SCHEMA, stops, softness, reflection, tint});
const lens = (value: unknown, iridescence = 1) => {
  const material = new MeshPhysicalMaterial({transmission: 1, iridescence, iridescenceThicknessRange: [100, 400]});
  material.userData[ANGLE_COLOUR_KEY] = value; return material;
};
const close = (a: number[], b: number[]) => a.every((value, i) => Math.abs(value - b[i]!) < 1e-9);

test('the bar runs from the lens sides (cos 0.4 and steeper) to straight on (cos 1)', () => {
  const value = spec();
  assert.ok(close(sampleAngleColour(value, .2), purple), 'steeper than the sides end keeps the side colour');
  assert.ok(close(sampleAngleColour(value, .4 + .6 * .35), purple), 'at the first marker');
  assert.ok(close(sampleAngleColour(value, .4 + .6 * .55), [.26, .345, .415]), 'midway the colours mix evenly');
  assert.ok(close(sampleAngleColour(value, 1), green), 'straight on');
});

test('softness 0 is a hard edge at the midpoint between markers; three colours keep their own softness', () => {
  const hard = spec([0]);
  assert.ok(close(sampleAngleColour(hard, .4 + .6 * .549), purple));
  assert.ok(close(sampleAngleColour(hard, .4 + .6 * .551), green));
  const three = spec([1, 0], [{colour: purple, position: 0}, {colour: [1, 1, 1], position: .5}, {colour: green, position: 1}]);
  assert.ok(close(sampleAngleColour(three, .4 + .6 * .74), [1, 1, 1]));
  assert.ok(close(sampleAngleColour(three, .4 + .6 * .76), green));
});

test('malformed specs are ignored rather than half applied', () => {
  for (const bad of [
    {...spec(), schema: 'other'},
    spec([1, 1]),
    spec([1], [{colour: purple, position: .5}, {colour: green, position: .5}]),
    spec([1], [{colour: [2, 0, 0], position: 0}, {colour: green, position: 1}]),
    spec([], [{colour: purple, position: 0}]),
    spec([1], undefined, false, 0),
    {...spec(), tint: 2},
    {...spec(), reflection: 'yes'},
  ]) assert.equal(angleColourOf(lens(bad)), null, JSON.stringify(bad));
  assert.deepEqual(angleColourOf(lens(spec())), spec());
});

test('reflection colours need an active coating; a tint alone still installs and colours the see-through term', () => {
  assert.equal(applyAngleColour(lens(spec([1], undefined, true, 0), 0)), false, 'no coating strength and no tint: nothing to colour');
  const tintOnly = lens(spec([1], undefined, true, .5), 0);
  assert.equal(applyAngleColour(tintOnly), true);
  const shader = {uniforms: {} as Record<string, {value: unknown}>, vertexShader: '',
    fragmentShader: 'void main() {\n#include <lights_fragment_begin>\n}'};
  tintOnly.onBeforeCompile(shader as never, null as never);
  assert.match(shader.fragmentShader, /material\.diffuseContribution \*= g3dTint;/);
  assert.doesNotMatch(shader.fragmentShader, /iridescenceFresnel = g3dAngleColour/, 'no coating to recolour');
  assert.equal(shader.uniforms.g3dAngleTint!.value, .5);
});

test('installs only on an iridescent physical lens and replaces only the thin-film colour term', () => {
  assert.equal(applyAngleColour(lens(spec([1], undefined, true, 0), 0)), false, 'no coating strength, nothing to colour');
  assert.equal(applyAngleColour(lens(undefined)), false);
  const standard = new MeshStandardMaterial(); standard.userData[ANGLE_COLOUR_KEY] = spec();
  assert.equal(applyAngleColour(standard), false);
  const material = lens(spec());
  assert.equal(applyAngleColour(material), true);
  assert.equal(applyAngleColour(material), false, 'idempotent');
  const shader = {uniforms: {} as Record<string, {value: unknown}>, vertexShader: '',
    fragmentShader: 'uniform float x;\nvoid main() {\n#include <lights_fragment_begin>\n}'};
  material.onBeforeCompile(shader as never, null as never);
  assert.match(shader.fragmentShader, /vec3 g3dAngleColour\( float cosTheta \)/);
  assert.match(shader.fragmentShader, /material\.iridescenceFresnel = g3dAngleColour\( dotNVi \);/);
  assert.doesNotMatch(shader.fragmentShader, /#include <lights_fragment_begin>/);
  assert.ok(shader.fragmentShader.includes('material.iridescenceF0 = Schlick_to_F0( material.iridescenceFresnel'), 'three keeps its Fresnel path');
  assert.equal(shader.uniforms.g3dAngleCount!.value, 2);
  assert.deepEqual(shader.uniforms.g3dAnglePositions!.value, [.35, .75, .75]);
  assert.ok(ShaderChunk.lights_fragment_begin.includes('evalIridescence'), 'the stock chunk is not modified globally');
  assert.notEqual(material.customProgramCacheKey(), lens(spec([.5])).customProgramCacheKey());
});
