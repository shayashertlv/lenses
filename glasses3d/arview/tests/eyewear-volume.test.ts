import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial} from 'three';
import {applyVolumeAttenuationScale, authoredAttenuationDistance, VOLUME_UNIT_SCALE_KEY} from '../src/render/eyewear-volume.ts';
import {lensShadowTransmission} from '../src/render/eyewear-shadow.ts';

test('finite volume attenuation distances are converted once to scene units; the authored ratio survives for the shadow', t => {
  const crystal = new MeshPhysicalMaterial({transmission: 1, thickness: .005, attenuationDistance: .005, attenuationColor: 0xf3ead6});
  const clearLens = new MeshPhysicalMaterial({transmission: 1});                       // infinite distance: nothing to convert
  const opaque = new MeshPhysicalMaterial({transmission: 0, attenuationDistance: .01});  // no transmission: untouched
  const standard = new MeshStandardMaterial();
  const geometry = new BoxGeometry(.01, .01, .01);
  const root = new Group().add(new Mesh(geometry, crystal), new Mesh(geometry, [clearLens, opaque]), new Mesh(geometry, standard));
  t.after(() => {geometry.dispose(); for (const material of [crystal, clearLens, opaque, standard]) material.dispose();});
  const shadowBefore = lensShadowTransmission(crystal);
  const converted = applyVolumeAttenuationScale(root, 100);
  assert.deepEqual(converted, [crystal]);
  assert.ok(Math.abs(crystal.attenuationDistance - .5) < 1e-12, 'metres become centimetres, like the scaled thickness');
  assert.equal(crystal.userData[VOLUME_UNIT_SCALE_KEY], 100);
  assert.ok(Math.abs(authoredAttenuationDistance(crystal) - .005) < 1e-12);
  assert.deepEqual(lensShadowTransmission(crystal), shadowBefore, 'the shadow keeps the authored thickness / distance ratio');
  assert.deepEqual(applyVolumeAttenuationScale(root, 100), [], 'a second pass converts nothing');
  assert.ok(Math.abs(crystal.attenuationDistance - .5) < 1e-12);
  assert.equal(clearLens.attenuationDistance, Infinity); assert.equal(opaque.attenuationDistance, .01);
  assert.equal(authoredAttenuationDistance(clearLens), Infinity);
  assert.throws(() => applyVolumeAttenuationScale(root, 0), /positive finite/);
});
