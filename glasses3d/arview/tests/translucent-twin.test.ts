/** The camera-transmission twin: what a translucent frame material draws inside the guarded pass B, where no
 * background is redrawn and Three's own transmission would show the clear colour. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  BoxGeometry, CanvasTexture, Group, Material, Matrix3, Mesh, MeshPhysicalMaterial, PerspectiveCamera, PlaneGeometry, Scene, ShaderLib,
  SRGBColorSpace, UniformsUtils, Vector2,
} from 'three';
import type {WebGLRenderer} from 'three';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {classifyAssetMaterials, isOpticalMaterial, isTranslucentFrameMaterial} from '../src/eyewear/optical-material.ts';
import {createTempleClip} from '../src/render/temple-clip.ts';
import {createTempleVisibility} from '../src/render/temple-visibility.ts';
import {createHairOcclusion} from '../src/render/hair-occlusion.ts';
import {applyVolumeAttenuationScale, authoredAttenuationDistance, volumeAttenuationRgb} from '../src/render/eyewear-volume.ts';
import {lensShadowTransmission} from '../src/render/eyewear-shadow.ts';
import {
  CAMERA_TRANSMISSION_TWIN_KEY, createCameraTransmissionTwin, createCameraTransmissionUniforms, refreshCameraTransmissionTwin,
  setCameraTransmissionSource, setCrystalLookThroughSource,
} from '../src/render/translucent-twin.ts';

type CompileInput = Parameters<Material['onBeforeCompile']>[0];
function compile(material: Material) {
  const physical = ShaderLib.physical!;
  const shader: Pick<CompileInput, 'uniforms' | 'vertexShader' | 'fragmentShader'> = {
    uniforms: UniformsUtils.clone(physical.uniforms), vertexShader: physical.vertexShader, fragmentShader: physical.fragmentShader,
  };
  Reflect.apply(material.onBeforeCompile, material, [shader, {}]);
  return shader;
}

/** A canonical lens beside a crystal arm with the exporter's roles, wrapped the way the renderer wraps it. */
function crystalArmFixture(t: {after(fn: () => void): void}, {hair = true} = {}) {
  const lens = new MeshPhysicalMaterial({transmission: 0});
  lens.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: {
    schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: 0,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null,
  }}};
  const crystal = new MeshPhysicalMaterial({transmission: .9, ior: 1.49, thickness: .004, color: 0xf2ece0,
    attenuationColor: 0xf3ead6, attenuationDistance: .005});
  crystal.toneMapped = false;
  const lensMesh = new Mesh(new PlaneGeometry(.05, .04).translate(0, 0, -.01), lens);
  const arm = new Mesh(new BoxGeometry(.004, .004, .15).translate(-.065, 0, -.09), crystal);
  lensMesh.userData.partRole = 'lens'; lensMesh.userData.lensSurfaceProfile = 'front_sheet_v1'; arm.userData.partRole = 'temple';
  const root = new Group().add(lensMesh, arm);
  classifyAssetMaterials(root);
  applyVolumeAttenuationScale(root, 100);
  const renderer = {capabilities: {samples: 4}} as unknown as WebGLRenderer;
  const scene = new Scene(), eyewearPose = new Group().add(root), camera = new PerspectiveCamera(60, 16 / 9, 1, 1000);
  scene.add(eyewearPose);
  const clip = createTempleClip(root);
  const uniforms = createCameraTransmissionUniforms();
  const visibility = createTempleVisibility(root, {renderer, scene, camera, eyewearPose, lensInputUniform: uniforms.twinLensInput});
  const occlusion = hair ? createHairOcclusion(root) : null;
  const overlay = root.children.find((child): child is Mesh => child instanceof Mesh && child.userData.templeVisibilityOverlay === true)!;
  t.after(() => {
    occlusion?.dispose(); visibility.dispose(); clip.dispose();
    lensMesh.geometry.dispose(); arm.geometry.dispose(); lens.dispose(); crystal.dispose();
  });
  return {lens, crystal, arm, overlay, clone: overlay.material as MeshPhysicalMaterial, root, uniforms, clip, visibility, occlusion};
}

test('the Beer-Lambert volume attenuation is one shared term for the shadow and the twin', t => {
  const absorbing = new MeshPhysicalMaterial({color: 0xffffff, transmission: 1, ior: 1.5, attenuationDistance: .005, thickness: .01});
  absorbing.attenuationColor.setRGB(.8, .5, .2);
  const clear = new MeshPhysicalMaterial({transmission: 1});
  const zero = new MeshPhysicalMaterial({transmission: 1, thickness: 0, attenuationDistance: .005});
  zero.attenuationColor.setRGB(.5, .5, .5);
  t.after(() => {absorbing.dispose(); clear.dispose(); zero.dispose();});
  const expected = [.8, .5, .2].map(channel => channel ** (.01 / .005));
  const actual = volumeAttenuationRgb(absorbing);
  for (let i = 0; i < 3; i++) assert.ok(Math.abs(actual[i]! - expected[i]!) < 1e-12);
  assert.deepEqual(volumeAttenuationRgb(clear), [1, 1, 1], 'an infinite attenuation distance absorbs nothing');
  assert.deepEqual(volumeAttenuationRgb(zero), [1, 1, 1], 'zero thickness absorbs nothing');
  const root = new Group().add(new Mesh(new BoxGeometry(.01, .01, .01), absorbing));
  applyVolumeAttenuationScale(root, 100);
  assert.ok(Math.abs(authoredAttenuationDistance(absorbing) - .005) < 1e-12);
  const scaled = volumeAttenuationRgb(absorbing);
  for (let i = 0; i < 3; i++) assert.ok(Math.abs(scaled[i]! - expected[i]!) < 1e-12, 'the scene-unit conversion does not change the authored ratio');
  // The shadow's transmitted colour is tint × Fresnel × this same attenuation.
  const shadow = lensShadowTransmission(absorbing), reflection = ((1.5 - 1) / (1.5 + 1)) ** 2;
  for (let i = 0; i < 3; i++) assert.ok(Math.abs(shadow[i]! - (1 - reflection) ** 2 * expected[i]!) < 1e-12);
});

test('the twin is an opaque clone that shows the paired camera through the material, with every wrapper of the original', t => {
  const f = crystalArmFixture(t);
  assert.equal(isTranslucentFrameMaterial(f.crystal), true);
  const twin = createCameraTransmissionTwin(f.crystal, f.uniforms); t.after(() => twin.dispose());
  assert.notEqual(twin, f.crystal);
  assert.equal(twin.transmission, 0, 'transmission 0: no transmission pre-pass, an ordinary opaque draw');
  assert.equal(f.crystal.transmission, .9, 'the original is untouched');
  assert.equal(twin.toneMapped, false); assert.equal(twin.transparent, false);
  assert.equal(twin.color.getHex(), f.crystal.color.getHex());
  assert.ok(twin.name.includes('camera transmission twin'));
  const shader = compile(twin), original = compile(f.crystal);
  assert.ok(!shader.fragmentShader.includes('#include <transmission_fragment>'), 'Three\'s transmission chunk is replaced');
  assert.ok(original.fragmentShader.includes('#include <transmission_fragment>'), 'only in the twin');
  const totalDiffuse = shader.fragmentShader.indexOf('vec3 totalDiffuse = ');
  const mix = shader.fragmentShader.indexOf('twinThrough = twinTransmission * ( 1.0 - twinReflectance ) * twinCameraRGB');
  const outgoing = shader.fragmentShader.indexOf('vec3 outgoingLight = totalDiffuse');
  assert.ok(totalDiffuse >= 0 && mix > totalDiffuse && outgoing > mix, 'the camera enters exactly where Three mixes its transmitted light');
  assert.match(shader.fragmentShader, /vec2 twinCameraUV = \( twinCameraUvTransform \* vec3\( gl_FragCoord\.xy \/ twinCameraViewport, 1\.0 \) \)\.xy;/,
    'the camera is sampled at this fragment\'s own pixel, through the texture\'s UV transform');
  assert.match(shader.fragmentShader, /twinCameraRGB = texture2D\( twinCameraSource, twinCameraUV \)\.rgb;/);
  assert.match(shader.fragmentShader, /if \( twinLensInput > 0\.5 \)/);
  assert.match(shader.fragmentShader, /twinCameraRGB = texture2D\( twinLensSource, twinLensUV \)\.rgb;/);
  assert.ok(!shader.fragmentShader.includes('linearToOutputTexel( texture2D( twinCameraSource'),
    'the sample stays linear: it enters before tone mapping and the output encoding, unlike the clip footer');
  assert.match(shader.fragmentShader, /twinThrough = twinTransmission \* \( 1\.0 - twinReflectance \) \* twinCameraRGB \* material\.diffuseContribution \* twinAttenuation;/,
    'attenuated by the volume, weighted by the material transmission (and, v2, less what the surface reflects)');
  assert.equal(shader.uniforms.twinCameraSource, f.uniforms.twinCameraSource, 'the camera uniforms are the shared owner\'s');
  assert.equal(shader.uniforms.twinCameraViewport, f.uniforms.twinCameraViewport);
  assert.equal(shader.uniforms.twinCameraUvTransform, f.uniforms.twinCameraUvTransform);
  assert.equal(shader.uniforms.twinLensSource, f.uniforms.twinLensSource);
  assert.equal(shader.uniforms.twinLensUvTransform, f.uniforms.twinLensUvTransform);
  assert.equal(shader.uniforms.twinLensInput, f.uniforms.twinLensInput);
  assert.equal(shader.uniforms.templeInternalLensInput, f.uniforms.twinLensInput, 'visibility and source selection share the per-draw flag');
  assert.equal(shader.uniforms.twinTransmission!.value, .9);
  assert.deepEqual(shader.uniforms.twinAttenuation!.value.toArray(), volumeAttenuationRgb(f.crystal));
  for (const name of ['templeClipEnabled', 'templeCameraSource', 'templeCheekCount', 'templeFrontalCameraSource', 'hairOcclusionEnabled', 'hairOcclusionMask']) {
    assert.equal(shader.uniforms[name], original.uniforms[name], `${name}: the twin runs the original's wrappers on their live uniforms`);
  }
  assert.ok(shader.fragmentShader.includes('templeClipPositiveXCutoffZ)) discard'));
  assert.ok(shader.fragmentShader.includes('gl_FragColor.rgb = mix(gl_FragColor.rgb, hairCameraRGB, hairWeight);'));
  assert.ok(!shader.fragmentShader.includes('gl_FragDepth'), 'the original arm keeps ordinary depth');
  assert.ok(twin.customProgramCacheKey().endsWith(`|${CAMERA_TRANSMISSION_TWIN_KEY}`));
  assert.equal(CAMERA_TRANSMISSION_TWIN_KEY, 'camera-transmission-twin-v3');
  assert.ok(twin.customProgramCacheKey().startsWith(f.crystal.customProgramCacheKey()), 'the key follows the original\'s wrappers');
  assert.equal(isOpticalMaterial(twin), false, 'the twin is frame too');
  assert.equal(isTranslucentFrameMaterial(twin), false, 'but with transmission 0 it never enters the twin swap itself');
  assert.equal(createCameraTransmissionTwin(f.crystal, f.uniforms), twin, 'one twin per material');
});

test('a crystal arm\'s clipped end fades into the camera like an opaque arm: the twin keeps the terminal blend after its output', t => {
  // test-pilot-002 review (MVP-07): the white block at the visible end of a crystal arm at yaw -80 / pitch 21 is the
  // room-panel reflection on the arm's top face, where the head proxy hides the rest of the arm, 13 mm ahead of the
  // clip. The clip and its fade were not involved; this pins that the twin keeps them.
  const f = crystalArmFixture(t);
  const twin = createCameraTransmissionTwin(f.crystal, f.uniforms); t.after(() => twin.dispose());
  const shader = compile(twin), original = compile(f.crystal), fragment = shader.fragmentShader;
  const cameraMix = fragment.indexOf('twinThrough = twinTransmission * ( 1.0 - twinReflectance ) * twinCameraRGB');
  const dithering = fragment.indexOf('#include <dithering_fragment>');
  const weight = fragment.indexOf('float templeBlendWeight = smoothstep(templeBlendEndpointZ, templeBlendEndpointZ + templeBlendFadeLength, templeOriginalXZ.y);');
  const blend = fragment.indexOf('gl_FragColor.rgb = mix(templeCameraRGB, gl_FragColor.rgb, templeBlendWeight);');
  assert.ok(cameraMix >= 0 && dithering > cameraMix && weight > dithering && blend > weight,
    'the terminal band dissolves the twin\'s final colour (look-through and reflections) into the camera, as on an opaque arm');
  assert.equal(original.fragmentShader.includes('gl_FragColor.rgb = mix(templeCameraRGB, gl_FragColor.rgb, templeBlendWeight);'), true);
  for (const name of ['templeFadeMode', 'templeFadeLength', 'templeSideFadeLengths', 'templeCameraSource', 'templeCameraViewport', 'templeCameraUvTransform']) {
    assert.equal(shader.uniforms[name], original.uniforms[name], `${name}: the twin's end fade reads the clip's live uniforms`);
  }
});

test('the twin of a visibility overlay clone keeps the depth relief and gets the camera too', t => {
  const f = crystalArmFixture(t);
  assert.ok(f.clone instanceof MeshPhysicalMaterial); assert.equal(isTranslucentFrameMaterial(f.clone), true);
  const twin = createCameraTransmissionTwin(f.clone, f.uniforms); t.after(() => twin.dispose());
  assert.equal(twin.transmission, 0); assert.equal(twin.depthWrite, false);
  const shader = compile(twin);
  assert.ok(shader.fragmentShader.includes('gl_FragDepth = min(gl_FragCoord.z, templeLifted);'));
  assert.ok(shader.fragmentShader.includes('twinThrough = twinTransmission * ( 1.0 - twinReflectance ) * twinCameraRGB'));
  assert.equal(shader.uniforms.templeVisibilityHeadDepth, compile(f.clone).uniforms.templeVisibilityHeadDepth);
  assert.equal(shader.uniforms.templeInternalLensInput, f.uniforms.twinLensInput, 'the overlay uses the same lens-input flag');
  assert.equal(shader.uniforms.hairOcclusionEnabled, compile(f.crystal).uniforms.hairOcclusionEnabled);
  assert.notEqual(createCameraTransmissionTwin(f.crystal, f.uniforms), twin, 'the overlay clone and the original have separate twins');
});

test('the twin follows wrapper changes of its original through refresh, and the shared camera source is set per frame', t => {
  const f = crystalArmFixture(t, {hair: false});
  const twin = createCameraTransmissionTwin(f.crystal, f.uniforms); t.after(() => twin.dispose());
  const key = twin.customProgramCacheKey();
  assert.equal(refreshCameraTransmissionTwin(twin), false, 'nothing changed since creation');
  const version = twin.version;
  const occlusion = createHairOcclusion(f.root); t.after(() => occlusion.dispose());
  assert.notEqual(twin.customProgramCacheKey(), key, 'the key is read live from the original');
  assert.ok(compile(twin).uniforms.hairOcclusionEnabled, 'the hook is read live from the original');
  assert.equal(refreshCameraTransmissionTwin(twin), true, 'the program must be rebuilt');
  assert.ok(twin.version > version);
  assert.equal(refreshCameraTransmissionTwin(twin), false);
  assert.throws(() => refreshCameraTransmissionTwin(f.crystal), /not a camera transmission twin/);
  const texture = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement); t.after(() => texture.dispose());
  texture.colorSpace = SRGBColorSpace; texture.offset.set(.1, .2); texture.repeat.set(.8, .7);
  setCameraTransmissionSource(f.uniforms, texture, 640, 360);
  assert.equal(f.uniforms.twinCameraSource.value, texture);
  assert.deepEqual(f.uniforms.twinCameraViewport.value.toArray(), [640, 360]);
  texture.updateMatrix();
  assert.deepEqual(f.uniforms.twinCameraUvTransform.value.elements, texture.matrix.elements);
  assert.notEqual(f.uniforms.twinCameraUvTransform.value, texture.matrix);
  assert.equal(f.uniforms.twinLensSource.value, texture);
  assert.deepEqual(f.uniforms.twinLensUvTransform.value.elements, texture.matrix.elements);
  const owned = new CanvasTexture({width: 640, height: 360} as HTMLCanvasElement); t.after(() => owned.dispose());
  setCrystalLookThroughSource(f.uniforms, owned, 640, 360);
  assert.equal(f.uniforms.twinCameraSource.value, owned);
  assert.deepEqual(f.uniforms.twinCameraUvTransform.value.elements, owned.matrix.elements);
  assert.equal(f.uniforms.twinLensSource.value, texture, 'owned hardware capture never replaces the lens-safe background');
  assert.deepEqual(f.uniforms.twinLensUvTransform.value.elements, texture.matrix.elements, 'lens input retains the camera UV transform');
  setCameraTransmissionSource(f.uniforms, null, 1, 1);
  assert.equal(f.uniforms.twinCameraSource.value, null);
  assert.equal(f.uniforms.twinLensSource.value, null);
  assert.deepEqual(f.uniforms.twinLensUvTransform.value.elements, new Matrix3().elements);
  assert.deepEqual(f.uniforms.twinCameraUvTransform.value.elements, new Matrix3().elements);
  assert.deepEqual(f.uniforms.twinCameraViewport.value.toArray(), new Vector2(1, 1).toArray());
  assert.throws(() => setCameraTransmissionSource(f.uniforms, texture, 0, 360), /viewport/);
  assert.throws(() => createCameraTransmissionTwin(f.lens, f.uniforms), /translucent frame material/);
});
