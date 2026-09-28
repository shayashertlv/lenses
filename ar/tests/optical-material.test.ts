import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BoxGeometry, BufferGeometry, Float32BufferAttribute, Group, Mesh, MeshPhysicalMaterial,
  MeshStandardMaterial, PerspectiveCamera, Scene, ShaderMaterial} from 'three';
import type {Material, WebGLRenderer} from 'three';
import {isOpticalMaterial} from '../src/eyewear/optical-material.ts';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {buildTempleContinuityModel} from '../src/render/continuity.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {createTempleTerminalFit} from '../src/render/temple-terminal-fit.ts';
import {createTempleClip} from '../src/render/temple-clip.ts';
import {createHairOcclusion} from '../src/render/hair-occlusion.ts';
import {createTempleVisibility} from '../src/render/temple-visibility.ts';

function canonical<T extends Material>(material: T): T {
  material.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0, appearance: {
    schema_version: 1, color_space: 'scene_linear_srgb_D65',
    density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
    normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: 0,
    optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null,
  }}};
  return material;
}

function fixture(lensMaterial: Material = canonical(new MeshPhysicalMaterial({transmission: 0}))) {
  const left = new BoxGeometry(.004, .004, .15).translate(-.065, 0, -.09);
  const right = new BoxGeometry(.004, .004, .15).translate(.065, 0, -.09);
  const lensGeometry = new BufferGeometry().setAttribute('position',
    new Float32BufferAttribute([-.02, 0, -.01, .02, 0, -.01, 0, .01, -.005], 3));
  const frame = new MeshStandardMaterial();
  const lens = new Mesh(lensGeometry, lensMaterial), arm = new Mesh(left, frame);
  const root = new Group().add(lens, arm, new Mesh(right, frame));
  return {root, frame, lens, arm, lensMaterial,
    dispose: () => {for (const resource of [left, right, lensGeometry, frame, lensMaterial]) resource.dispose();}};
}

function visibilityContext(root: Group) {
  const scene = new Scene(), eyewearPose = new Group().add(root);
  scene.add(eyewearPose);
  return {renderer: {} as WebGLRenderer, scene, eyewearPose, camera: new PerspectiveCamera(60, 1, 1, 1000)};
}

test('canonical total mirrors are optical across material classes; legacy classification is unchanged', () => {
  const cases: [Material, boolean][] = [
    [canonical(new MeshPhysicalMaterial({transmission: 0, metalness: 1})), true],
    [canonical(new ShaderMaterial()), true],
    [new MeshPhysicalMaterial({transmission: 1}), true],
    [new MeshPhysicalMaterial({transmission: .001}), true],
    [new MeshPhysicalMaterial({transmission: 0, metalness: 1}), false],
    [new MeshStandardMaterial({transparent: true, opacity: .1}), false],
    [new ShaderMaterial(), false],
  ];
  try {for (const [material, expected] of cases) assert.equal(isOpticalMaterial(material), expected);}
  finally {for (const [material] of cases) material.dispose();}
});

test('malformed canonical data throws before a positive-transmission legacy fallback', () => {
  const material = canonical(new MeshPhysicalMaterial({transmission: 1}));
  material.userData.gltfExtensions[LENS_APPEARANCE_EXTENSION].appearance.normal_reflectance_rgb = [1.1, 1, 1];
  try {assert.throws(() => isOpticalMaterial(material), /reflectance|range/i);}
  finally {material.dispose();}
});

test('total-mirror geometry initializes continuity and rear deformation while preserving every lens vertex', t => {
  const f = fixture(), legacy = fixture(new MeshPhysicalMaterial({transmission: 1}));
  t.after(f.dispose); t.after(legacy.dispose);
  const originalLens = f.lens.geometry.getAttribute('position').array.slice();
  const originalArm = f.arm.geometry.getAttribute('position').array.slice();
  assert.deepEqual(buildTempleContinuityModel(f.root, -.14), buildTempleContinuityModel(legacy.root, -.14));
  const rear = createRearDrop(f.root, -.14); t.after(() => rear.dispose());
  assert.ok(rear.diagnostics.affectedVertexCount > 0);
  rear.setShape(.01, .015);
  assert.deepEqual(f.lens.geometry.getAttribute('position').array, originalLens);
  assert.notDeepEqual(f.arm.geometry.getAttribute('position').array, originalArm);
  assert.ok(Math.abs(rear.diagnostics.lensRearZM + .01) < 1e-8);
});

test('total-mirror materials receive neither arm shader wrappers nor visibility overlays', t => {
  const f = fixture(); t.after(f.dispose);
  const lensHook = f.lensMaterial.onBeforeCompile, lensKey = f.lensMaterial.customProgramCacheKey;
  const frameHook = f.frame.onBeforeCompile;
  const clip = createTempleClip(f.root);
  const visibility = createTempleVisibility(f.root, visibilityContext(f.root));
  const hair = createHairOcclusion(f.root);
  t.after(() => {hair.dispose(); visibility.dispose(); clip.dispose();});
  assert.equal(f.lensMaterial.onBeforeCompile, lensHook);
  assert.equal(f.lensMaterial.customProgramCacheKey, lensKey);
  assert.notEqual(f.frame.onBeforeCompile, frameHook);
  const overlays = f.root.children.filter((object): object is Mesh => object instanceof Mesh && object.userData.templeVisibilityOverlay);
  assert.equal(overlays.length, 2, 'only the two opaque arm meshes receive visibility overlays');
  assert.ok(overlays.every(mesh => mesh.geometry !== f.lens.geometry));
});

test('canonical optical geometry cannot become a terminal containment sample even if it is posterior', t => {
  const f = fixture(); t.after(f.dispose);
  const input = {offsetCm: [0, 3.271027, 6.531958919387042] as const,
    spreadM: .018, spreadStartZM: -.015, modelCutoffZM: -.14, maximumZM: -.115};
  const baseline = createTempleTerminalFit(f.root, input);
  assert.ok(baseline);
  const remote = new BoxGeometry(.003, .003, .006).translate(.25, 0, -.113);
  f.root.add(new Mesh(remote, f.lensMaterial)); t.after(() => remote.dispose());
  assert.deepEqual(createTempleTerminalFit(f.root, input), baseline,
    'otherwise the optical mesh would require excessive temple inset and invalidate the fit');
});

test('all geometry and visibility consumers reject malformed optical metadata instead of masking it', () => {
  const factories = [
    (f: ReturnType<typeof fixture>) => buildTempleContinuityModel(f.root, -.14),
    (f: ReturnType<typeof fixture>) => createRearDrop(f.root, -.14),
    (f: ReturnType<typeof fixture>) => createTempleClip(f.root),
    (f: ReturnType<typeof fixture>) => createHairOcclusion(f.root),
    (f: ReturnType<typeof fixture>) => createTempleVisibility(f.root, visibilityContext(f.root)),
    (f: ReturnType<typeof fixture>) => createTempleTerminalFit(f.root, {offsetCm: [0, 3.27, 6.53],
      spreadM: .018, spreadStartZM: -.015, modelCutoffZM: -.14, maximumZM: -.115}),
  ];
  for (const factory of factories) {
    const f = fixture();
    f.lensMaterial.userData.gltfExtensions[LENS_APPEARANCE_EXTENSION].schema_version = 99;
    try {assert.throws(() => factory(f), /schema_version/);}
    finally {f.dispose();}
  }
});
