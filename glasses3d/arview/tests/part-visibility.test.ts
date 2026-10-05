import assert from 'node:assert/strict';
import {test} from 'node:test';
import type {TestContext} from 'node:test';
import {
  BoxGeometry, BufferGeometry, DataTexture, Group, Material, Mesh, MeshPhysicalMaterial, PerspectiveCamera,
  RedFormat, Scene, ShaderLib, ShaderMaterial, SRGBColorSpace, Texture, UniformsUtils,
} from 'three';
import type {WebGLRenderer} from 'three';
import {PART_VISIBILITY_KEY, partVisibilityOf, validatePartVisibility} from '../src/eyewear/part-visibility.ts';
import type {PartVisibilityRegion} from '../src/eyewear/part-visibility.ts';
import {classifyAssetMaterials, isAuthoredFrontFrame, isOpticalMaterial} from '../src/eyewear/optical-material.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {buildTempleContinuityModel} from '../src/render/continuity.ts';
import {createTempleClip} from '../src/render/temple-clip.ts';
import {createHairOcclusion} from '../src/render/hair-occlusion.ts';
import {createTempleVisibility} from '../src/render/temple-visibility.ts';
import {EyewearShadow} from '../src/render/eyewear-shadow.ts';

function part(name: string, region: PartVisibilityRegion, geometry: BufferGeometry, material: Material): Mesh {
  const mesh = new Mesh(geometry, material); mesh.name = name;
  mesh.userData = {partRole: region === 'lens' ? 'lens' : 'frame', [PART_VISIBILITY_KEY]: {version: 1, region}};
  return mesh;
}
function fixture(t: TestContext, crystal = false) {
  const material = new MeshPhysicalMaterial({color: '#ced2ba', roughness: .18, transmission: crystal ? .85 : 0, thickness: .004, clearcoat: .6});
  const lensMaterial = new MeshPhysicalMaterial({transmission: .5, roughness: .1});
  const front = part('deep wraparound endpiece', 'front', new BoxGeometry(.012, .025, .055).translate(-.067, 0, -.034), material);
  const otherFront = part('opposite deep endpiece', 'front', new BoxGeometry(.012, .025, .055).translate(.067, 0, -.034), material);
  const left = part('left arm hardware and shaft', 'temple_left', new BoxGeometry(.006, .008, .155, 1, 1, 32).translate(-.061, 0, -.0825), material);
  const right = part('right arm hardware and shaft', 'temple_right', new BoxGeometry(.006, .008, .155, 1, 1, 32).translate(.061, 0, -.0825), material);
  const lens = part('lens', 'lens', new BoxGeometry(.1, .032, .002), lensMaterial);
  const root = new Group().add(front, otherFront, left, right, lens);
  t.after(() => {
    const resources = new Set<BufferGeometry | Material | Texture>();
    root.traverse(object => {if (!(object instanceof Mesh)) return; resources.add(object.geometry);
      for (const m of Array.isArray(object.material) ? object.material : [object.material]) {resources.add(m); for (const value of Object.values(m)) if (value instanceof Texture) resources.add(value);}});
    resources.forEach(value => value.dispose()); material.dispose(); lensMaterial.dispose();
  });
  return {root, front, otherFront, left, right, lens, material};
}
function compile(material: Material) {
  const source = ShaderLib.standard!;
  const shader = {uniforms: UniformsUtils.clone(source.uniforms), vertexShader: source.vertexShader, fragmentShader: source.fragmentShader, outputColorSpace: SRGBColorSpace};
  Reflect.apply(material.onBeforeCompile, material, [shader, {}]); return shader;
}

test('profile validation is complete, coherent, strict, and atomic before shared material changes', t => {
  const f = fixture(t);
  assert.equal(validatePartVisibility(f.root), true);
  for (const malformed of [null, [], {version: 2, region: 'front'}, {version: 1, region: ['front']}, {version: 1, region: 'front', guess: true}]) {
    f.front.userData[PART_VISIBILITY_KEY] = malformed;
    assert.throws(() => classifyAssetMaterials(f.root), /LENSES_part_visibility/);
    assert.equal(f.left.material, f.material); assert.equal(f.front.material, f.material);
  }
  f.front.userData[PART_VISIBILITY_KEY] = {version: 1, region: 'front'};
  delete f.left.userData[PART_VISIBILITY_KEY]; f.left.visible = false;
  assert.throws(() => classifyAssetMaterials(f.root), /missing left arm/);
  f.left.userData[PART_VISIBILITY_KEY] = {version: 1, region: 'temple_left'};
  f.lens.userData[PART_VISIBILITY_KEY] = {version: 1, region: 'front'};
  assert.throws(() => classifyAssetMaterials(f.root), /conflicts with partRole/);
  f.lens.userData[PART_VISIBILITY_KEY] = {version: 1, region: 'lens'};
  f.front.position.z = -.001;
  assert.throws(() => classifyAssetMaterials(f.root), /bake authored transforms/);
  f.front.position.z = 0; delete f.front.userData.partRole; f.root.userData.partRole = 'frame';
  assert.throws(() => classifyAssetMaterials(f.root), /conflicts with partRole/);
  const empty = new Group(); empty.userData[PART_VISIBILITY_KEY] = {version: 1, region: 'front'};
  assert.throws(() => validatePartVisibility(empty), /no rendered mesh/);
});

test('a glTF primitive Group may provide one part profile; model-wide, conflicting, and Mesh-parent inheritance may not', t => {
  const f = fixture(t), profile = f.front.userData[PART_VISIBILITY_KEY];
  delete f.front.userData[PART_VISIBILITY_KEY];
  const node = new Group(); node.userData[PART_VISIBILITY_KEY] = profile; node.add(f.front); f.root.add(node);
  assert.equal(validatePartVisibility(f.root), true);
  f.front.userData[PART_VISIBILITY_KEY] = {version: 1, region: 'temple_left'};
  assert.throws(() => validatePartVisibility(f.root), /conflicting node and primitive/);
  delete f.front.userData[PART_VISIBILITY_KEY]; node.remove(f.front); f.lens.add(f.front);
  assert.equal(partVisibilityOf(f.front), null);
  assert.throws(() => validatePartVisibility(f.root), /missing deep wraparound/);
  f.root.add(f.front); f.root.remove(node); f.root.userData[PART_VISIBILITY_KEY] = profile;
  assert.throws(() => validatePartVisibility(f.root), /model-root metadata|conflicting node and primitive/);
});

for (const crystal of [false, true]) test(`deep front is unchanged by spread and arm clipping, with PBR retained (crystal ${crystal})`, t => {
  const f = fixture(t, crystal); classifyAssetMaterials(f.root);
  assert.equal(isAuthoredFrontFrame(f.front, f.root), true);
  assert.notEqual(f.front.material, f.left.material); assert.notEqual(f.left.material, f.right.material);
  const assigned = [f.front.material, f.left.material, f.right.material]; classifyAssetMaterials(f.root);
  assert.deepEqual([f.front.material, f.left.material, f.right.material], assigned);
  for (const mesh of [f.front, f.left, f.right]) {
    const material = mesh.material as MeshPhysicalMaterial;
    assert.equal(material.transmission, crystal ? .85 : 0); assert.equal(material.clearcoat, .6);
    assert.equal(material.roughness, .18); assert.equal(material.thickness, .004); assert.ok(material.color.equals(f.material.color));
    assert.equal(isOpticalMaterial(material), false);
  }
  const before = f.front.geometry.getAttribute('position').array.slice(), arm = f.left.geometry.getAttribute('position').array.slice();
  const rear = createRearDrop(f.root, -.14); t.after(() => rear.dispose()); rear.setShape(0, .018);
  assert.deepEqual(f.front.geometry.getAttribute('position').array, before);
  assert.notDeepEqual(f.left.geometry.getAttribute('position').array, arm);
  assert.ok(rear.opticalBounds.min.z <= -.061, 'the entire front is in protected bounds even behind old -30mm limit');
  const clip = createTempleClip(f.root); t.after(() => clip.dispose());
  assert.equal(compile(f.front.material as Material).uniforms.templeClipEnabled, undefined);
  const left = compile(f.left.material as Material), right = compile(f.right.material as Material);
  assert.match(left.fragmentShader, /float templeBlendEndpointZ = templeClipNegativeXCutoffZ;/);
  assert.match(right.fragmentShader, /float templeBlendEndpointZ = templeClipPositiveXCutoffZ;/);
  assert.doesNotMatch(left.fragmentShader, /templeOriginalXZ.x < 0.0/);
});

test('continuity uses authored arms only and cannot mistake deep front hardware for a shaft', t => {
  const f = fixture(t); classifyAssetMaterials(f.root);
  const withFront = buildTempleContinuityModel(f.root, -.14);
  f.root.remove(f.front, f.otherFront);
  const withoutFront = buildTempleContinuityModel(f.root, -.14);
  assert.deepEqual(withFront, withoutFront); f.root.add(f.front, f.otherFront);
  for (const station of withFront.sides[0]!) assert.ok(station.minXM > -.065 && station.maxXM < -.057);
});

test('profile geometry ownership is independent of alpha blending without making the material opaque', t => {
  const f = fixture(t); f.material.transparent = true; f.material.opacity = .4; f.material.depthWrite = false;
  classifyAssetMaterials(f.root);
  const continuity = buildTempleContinuityModel(f.root, -.14); assert.equal(continuity.sides[0]!.length, 33);
  const rear = createRearDrop(f.root, -.14); t.after(() => rear.dispose()); rear.setShape(0, .018);
  for (const mesh of [f.front, f.left, f.right]) {
    const material = mesh.material as Material;
    assert.equal(material.transparent, true); assert.equal(material.opacity, .4); assert.equal(material.depthWrite, false);
  }
});

test('a GLB geometry reused by front and arm owners receives separate policy clones', t => {
  const f = fixture(t); f.front.geometry.dispose(); f.front.geometry = f.left.geometry;
  const source = f.left.geometry, before = source.getAttribute('position').array.slice();
  classifyAssetMaterials(f.root); const rear = createRearDrop(f.root, -.14); t.after(() => rear.dispose());
  assert.notEqual(f.front.geometry, f.left.geometry);
  rear.setShape(0, .018);
  assert.deepEqual(f.front.geometry.getAttribute('position').array, before);
  assert.notDeepEqual(f.left.geometry.getAttribute('position').array, before);
  rear.dispose(); assert.equal(f.front.geometry, source); assert.equal(f.left.geometry, source);
});

for (const crystal of [false, true]) test(`front keeps native depth and no hair-mask shader while true arms retain hair (crystal ${crystal})`, t => {
  const f = fixture(t, crystal); classifyAssetMaterials(f.root);
  const scene = new Scene(), eyewearPose = new Group().add(f.root), camera = new PerspectiveCamera(60, 1, 1, 1000);
  scene.add(eyewearPose); const renderer = {capabilities: {samples: 4}} as unknown as WebGLRenderer;
  const clip = createTempleClip(f.root), visibility = createTempleVisibility(f.root, {scene, camera, eyewearPose, renderer});
  t.after(() => {visibility.dispose(); clip.dispose();});
  const frontBefore = compile(f.front.material as Material), frontHook = (f.front.material as Material).onBeforeCompile;
  const allHair = new DataTexture(new Uint8Array(16).fill(255), 4, 4, RedFormat); t.after(() => allHair.dispose());
  const hair = createHairOcclusion(f.root); t.after(() => hair.dispose()); hair.set(true, -.02);
  const cameraSource = new Texture(); t.after(() => cameraSource.dispose());
  hair.prepareRender(allHair, 400, 300, cameraSource, 2);
  const front = compile(f.front.material as Material), arm = compile(f.left.material as Material);
  assert.equal(front.uniforms.templeExcludeArmsFromLensInput, undefined);
  assert.equal(front.uniforms.templeClipEnabled, undefined);
  assert.equal((f.front.material as Material).onBeforeCompile, frontHook);
  assert.equal(front.fragmentShader, frontBefore.fragmentShader); assert.equal(front.vertexShader, frontBefore.vertexShader);
  assert.equal(front.uniforms.hairOcclusionEnabled, undefined); assert.doesNotMatch(front.fragmentShader, /hairOcclusion|hairForeground/);
  assert.equal(arm.uniforms.hairOcclusionEnabled!.value, 1); assert.equal(arm.uniforms.hairOcclusionMask!.value, allHair);
  assert.match(arm.fragmentShader, /if \(hairOcclusionEnabled > 0.5\)/);
  hair.set(false, -.02); assert.equal(arm.uniforms.hairOcclusionEnabled!.value, 0);
  assert.ok(arm.uniforms.templeExcludeArmsFromLensInput); assert.doesNotMatch(arm.fragmentShader, /abs\(templeFrontalOriginalPosition.x\)/);
  const overlays = f.root.children.filter(object => object.userData.templeVisibilityOverlay === true) as Mesh[];
  assert.equal(overlays.length, 2); assert.ok(overlays.every(object => partVisibilityOf(object)?.region.startsWith('temple_')));
  for (const mesh of [f.front, ...overlays]) assert.equal((mesh.material as Material).depthTest, true);
  assert.equal((f.front.material as Material).depthWrite, true);
  const twin = (f.front.material as Material).clone(); twin.onBeforeCompile = (f.front.material as Material).onBeforeCompile; twin.toneMapped = false;
  const twinShader = compile(twin); assert.doesNotMatch(twinShader.fragmentShader, /hairOcclusion|hairForeground/); twin.dispose();
});

test('shadow casters preserve front coverage and clip only the authored arm side', t => {
  const f = fixture(t); classifyAssetMaterials(f.root);
  // Construction borrows geometry and installs shaders; rendering alone needs WebGL methods.
  const shadow = new EyewearShadow({} as WebGLRenderer, f.root, new BoxGeometry(.1, .1, .001));
  t.after(() => shadow.dispose());
  const rows = (shadow as unknown as {casters: {source: Mesh; mesh: Mesh}[]}).casters;
  assert.ok(rows, 'shadow caster audit seam');
  const front = rows.find(row => row.source === f.front)!;
  assert.equal((front.mesh.material as ShaderMaterial).uniforms.clipped!.value, 0);
  const left = rows.find(row => row.source === f.left)!;
  assert.equal((left.mesh.material as ShaderMaterial).uniforms.clipped!.value, 1);
  assert.match((left.mesh.material as ShaderMaterial).fragmentShader, /float cutoff = clipCutoffs.x;/);
});
