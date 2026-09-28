import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BufferAttribute, BufferGeometry, Float32BufferAttribute, Group, InstancedMesh, Mesh, MeshPhysicalMaterial} from 'three';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {EFFECTIVE_OPTICAL_GROUP_PROFILE} from '../src/eyewear/optical-material.ts';
import {EFFECTIVE_OPTICAL_GROUP_LIMITS, validateEffectiveOpticalGroups} from '../src/render/effective-optical-topology.ts';

type Point = [number, number, number];
let nextSourcePart = 0;
function material(): MeshPhysicalMaterial {
  const result = new MeshPhysicalMaterial();
  result.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version: 1, texcoord: 0,
    appearance: {schema_version: 1, color_space: 'scene_linear_srgb_D65',
      density_interpolation: 'piecewise_smoothstep_optical_density', vertical_coordinate: 'lens_local_bottom_0_top_1',
      normal_reflectance_rgb: [1, 1, 1], refractive_index: 1.5, roughness: 0,
      optical_density_keyframes: [{v: 0, optical_density_rgb: [0, 0, 0]}], angular_reflectance_keyframes: null}}};
  return result;
}
function member(group = 'lens', id = 'member', points: Point[] = [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
    faces = [0, 1, 2], normal?: Point[], height?: [number, number]): Mesh {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(points.flat(), 3));
  geometry.setAttribute('normal', new Float32BufferAttribute((normal ?? points.map(() => [0, 0, 1])).flat(), 3));
  const lo = height?.[0] ?? Math.min(...points.map(p => p[1])), hi = height?.[1] ?? Math.max(...points.map(p => p[1]));
  geometry.setAttribute('uv', new Float32BufferAttribute(points.flatMap(p => [.5, (p[1] - lo) / (hi - lo)]), 2));
  geometry.setIndex(faces);
  const mesh = new Mesh(geometry, material());
  mesh.userData = {partRole: 'lens', lensSurfaceProfile: EFFECTIVE_OPTICAL_GROUP_PROFILE,
    lensUVConvention: 'lens_local_bottom_0_top_1', opticalGroupId: group, opticalGroupMemberId: id,
    opticalSourcePartIndex: nextSourcePart++, opticalSourceSha256: 'a'.repeat(64), lensAppearanceSha256: 'b'.repeat(64),
    semanticIdentity: 'unverified', materialIdentification: 'unmeasured'};
  return mesh;
}
function validate(meshes: Mesh[]): ReturnType<typeof validateEffectiveOpticalGroups> {return validateEffectiveOpticalGroups(meshes);}

test('closed/backward/disconnected geometry survives, and the call does not mutate attributes or materials', () => {
  const tetra = member('closed', 'tetra', [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
    [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]);
  const detached = member('closed', 'island', [[2, 0, 0], [3, 0, 0], [2, 1, 0]], [2, 1, 0]);
  const before = JSON.stringify(tetra.geometry.toJSON()), original = tetra.material;
  const result = validate([tetra, detached]);
  assert.equal(result.length, 1); assert.deepEqual(result[0]!.meshes, [tetra, detached]);
  assert.equal(JSON.stringify(tetra.geometry.toJSON()), before); assert.equal(tetra.material, original);
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result[0]!.meshes));
});

test('same material never merges distinct groups; different material objects with equal descriptors can form one group', () => {
  const a = member('a'), b = member('b', 'other', [[0, 0, .1], [1, 0, .1], [0, 1, .1]]);
  b.material = a.material;
  assert.deepEqual(validate([b, a]).map(g => g.id), ['a', 'b']);
  b.userData.opticalGroupId = 'a'; b.material = material(); assert.equal(validate([a, b]).length, 1);
  (b.material as MeshPhysicalMaterial).userData.gltfExtensions[LENS_APPEARANCE_EXTENSION].appearance.roughness = .5;
  assert.throws(() => validate([a, b]), /conflicting_group_descriptors/);
});

test('group-wide height spans multiple members and rejects separately normalized per-member coordinates', () => {
  const a = member('lens', 'lower', [[0, 0, 0], [1, 0, 0], [0, .5, 0]], undefined, undefined, [0, 1]);
  const b = member('lens', 'upper', [[2, .5, 0], [3, .5, 0], [2, 1, 0]], undefined, undefined, [0, 1]);
  assert.doesNotThrow(() => validate([a, b]));
  a.geometry.getAttribute('uv').setY(2, 1);
  assert.throws(() => validate([a, b]), /group_wide_Y/);
});

test('independent exported float32 position and UV rounding has a derived envelope', () => {
  const original: Point[] = [[0, .100000003, 0], [1, .3456789123, 0], [0, .89999998, 0]];
  const mesh = member('lens', 'rounded', original);
  assert.doesNotThrow(() => validate([mesh]));
  mesh.geometry.getAttribute('uv').setY(1, .4);
  assert.throws(() => validate([mesh]), /group_wide_Y/);
});

test('all height vertices require the SAME feasible pre-quantization endpoints', () => {
  const ulp = 2 ** -23;
  const mesh = member('lens', 'inconsistent', [[0, 1, 0], [1, 1 + ulp, 0], [1, 1 + 3 * ulp, 0], [0, 1 + ulp, 0]], [0, 1, 2, 0, 2, 3]);
  const uv = mesh.geometry.getAttribute('uv');
  [0, .15, 1, .59].forEach((v, i) => uv.setY(i, v));
  // Each vertex separately admits some endpoint pair. Together they require
  // .85*a+.15*b >= .5 and .41*a+.59*b <= 1.5 within incompatible endpoint cells.
  assert.throws(() => validate([mesh]), /group_wide_Y/);
});

test('a feasible off-midpoint endpoint witness is retained, with extrema actually attained', () => {
  const ulp = 2 ** -23, min = 1 + .3 * ulp, max = 1 + 2.7 * ulp;
  const p: Point[] = [[0, min, 0], [1, 1 + .51 * ulp, 0], [1, max, 0], [0, 1 + 1.49 * ulp, 0]];
  const mesh = member('lens', 'consistent', p, [0, 1, 2, 0, 2, 3]);
  assert.doesNotThrow(() => validate([mesh]));
  // Having V=0/1 somewhere is insufficient if those corners cannot attain the
  // shared minimum/maximum Y. The original group height must be witnessed.
  mesh.geometry.getAttribute('uv').setY(0, .1); mesh.geometry.getAttribute('uv').setY(1, 0);
  assert.throws(() => validate([mesh]), /distinct_bottom_0_and_top_1/);
});

test('same-group identical triangles permit reversed winding and uniform normal sign with unequal corner magnitudes', () => {
  const a = member(), b = member('lens', 'duplicate', undefined, [2, 1, 0], [[0, 0, -2], [0, 0, -.5], [0, 0, -4]]);
  assert.doesNotThrow(() => validate([a, b]));
  b.geometry.getAttribute('normal').setXYZ(0, .5, 0, 1);
  assert.throws(() => validate([a, b]), /conflicting_corresponding_corner_directions/);
});

test('retessellated constant fields are proved exactly; varying retessellation remains unsupported', () => {
  const points: Point[] = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]];
  const a = member('lens', 'diagonal-a', points, [0, 1, 2, 0, 2, 3]);
  const b = member('lens', 'diagonal-b', points, [0, 1, 3, 1, 2, 3]);
  assert.doesNotThrow(() => validate([a, b]));
  for (const mesh of [a, b]) mesh.geometry.getAttribute('normal').setXYZ(0, .1, 0, 1);
  assert.throws(() => validate([a, b]), /unproven_retessellated_varying_normal_field/);
});

test('identical varying triangles require one uniform normal sign and a nonzero interpolation proof', () => {
  const n: Point[] = [[0, 0, 1], [.25, 0, 1], [0, .25, 1]];
  const a = member('lens', 'a', undefined, undefined, n);
  const b = member('lens', 'b', undefined, [1, 0, 2], n.map(v => v.map(c => -c * 2) as Point));
  assert.doesNotThrow(() => validate([a, b]));
  const normal = b.geometry.getAttribute('normal'); normal.setXYZ(1, .5, 0, 2);
  assert.throws(() => validate([a, b]), /nonuniform_normal_sign/);
  for (const mesh of [a, b]) mesh.geometry.getAttribute('normal').setXYZ(1, 0, 0, -1);
  b.geometry.getAttribute('normal').setXYZ(0, 0, 0, 1); b.geometry.getAttribute('normal').setXYZ(2, 0, .25, 1);
  assert.throws(() => validate([a, b]), /normal_field_contains_zero_on_edge/);
});

test('every triangle rejects zero normal interpolants on an edge or in its interior, even without coincidence', () => {
  const edge = member('lens', 'edge', undefined, undefined, [[0, 0, 1], [0, 0, -2], [.25, 0, 1]]);
  assert.throws(() => validate([edge]), /normal_field_contains_zero_on_edge/);
  const interior = member('lens', 'interior', undefined, undefined, [[1, 0, 0], [0, 2, 0], [-4, -4, 0]]);
  assert.throws(() => validate([interior]), /normal_field_contains_zero_interior/);
  interior.geometry.setIndex([2, 1, 0]);
  assert.throws(() => validate([interior]), /normal_field_contains_zero_interior/);
});

test('valid obtuse and full-rank normal triples are not rejected by an acute-cone shortcut', () => {
  for (const n of [
    [[1, 0, 0], [-.25, 1, 0], [-.25, .5, 0]], // rank two, obtuse, origin outside
    [[1, 0, 0], [0, 1, 0], [-1, -1, 2 ** -100]], // exact rank three
    [[0, 0, -1], [0, 0, -2], [0, 0, -.5]], // rank one, uniform sign
  ]) assert.doesNotThrow(() => validate([member('lens', 'valid', undefined, undefined, n as Point[])]));
  const obtuse: Point[] = [[1, 0, 0], [-.25, 1, 0], [-.25, .5, 0]];
  const a = member('lens', 'a', undefined, undefined, obtuse);
  const b = member('lens', 'b', undefined, [2, 1, 0], obtuse.map(v => v.map(x => -2 * x) as Point));
  assert.doesNotThrow(() => validate([a, b]));
});

test('coincident fields compare actual V exactly even when both lie inside the height-rounding envelope', () => {
  const points: Point[] = [[0, 0, 0], [1, .5, 0], [0, 1, 0]];
  const a = member('lens', 'a', points), b = member('lens', 'b', points);
  b.geometry.getAttribute('uv').setY(1, .5 + 2 ** -24);
  assert.throws(() => validate([a, b]), /conflicting_intrinsic_v_affine_fields/);
});

test('arbitrarily small positive-area patches cannot be hidden by an area epsilon', () => {
  const a = member(), epsilon = 2 ** -80;
  const b = member('lens', 'tiny', [[0, 0, 0], [epsilon, 0, 0], [0, epsilon, 0]], undefined,
    [[1, 0, 1], [1, 0, 1], [1, 0, 1]], [0, 1]);
  assert.throws(() => validate([a, b]), /conflicting_constant_normal_directions/);
  b.userData.opticalGroupId = 'separate'; b.geometry.getAttribute('uv').setY(2, 1);
  assert.throws(() => validate([a, b]), /cross_group_coincident_patch/);
});

test('vertical planes, shared edges and noncoplanar crossings do not require front-sheet winding', () => {
  const vertical = member('lens', 'vertical', [[0, 0, 0], [0, 1, 0], [0, 0, 1]]);
  const copy = member('lens', 'copy', [[0, 0, 0], [0, 1, 0], [0, 0, 1]], [2, 0, 1]);
  assert.doesNotThrow(() => validate([vertical, copy]));
  const a = member('a'), adjacent = member('b', 'adjacent', [[1, 0, 0], [1, 1, 0], [0, 1, 0]]);
  assert.doesNotThrow(() => validate([a, adjacent]));
  adjacent.geometry.getAttribute('position').setZ(0, .1);
  assert.doesNotThrow(() => validate([a, adjacent]));
});

test('metadata, source/member identity, canonical descriptor and static drawing contracts are mandatory', () => {
  for (const mutate of [
    (m: Mesh) => {delete m.userData.opticalSourceSha256;},
    (m: Mesh) => {m.userData.semanticIdentity = 'accepted';},
    (m: Mesh) => {m.userData.lensSurfaceProfile = 'front_sheet_v1';},
    (m: Mesh) => {m.userData.opticalGroupId = '../lens';},
    (m: Mesh) => {m.material = [material()];},
    (m: Mesh) => {m.geometry.getAttribute('normal').setXYZ(0, 0, 0, 0);},
    (m: Mesh) => {m.geometry.setDrawRange(0, 2);},
    (m: Mesh) => {m.geometry.setIndex([0, 1, 19]);},
    (m: Mesh) => {m.geometry.setAttribute('uv', new BufferAttribute(new Float64Array(6), 2));},
    (m: Mesh) => {m.geometry.getAttribute('position').setX(0, Infinity);},
  ]) {const mesh = member(); mutate(mesh); assert.throws(() => validate([mesh]), /unsupported/);}
  const a = member(), b = member('lens', 'other'); b.userData.opticalSourcePartIndex = a.userData.opticalSourcePartIndex;
  assert.throws(() => validate([a, b]), /source_part/);
  b.userData.opticalSourcePartIndex = nextSourcePart++; b.userData.opticalGroupMemberId = a.userData.opticalGroupMemberId;
  assert.throws(() => validate([a, b]), /member_id/);
  const instanced = new InstancedMesh(a.geometry, a.material, 1); instanced.userData = {...a.userData};
  assert.throws(() => validate([instanced]), /noninstanced/);
});

test('posed root is allowed; missing roots or nonidentity member transforms cannot reinterpret coordinates', () => {
  const mesh = member(), branch = new Group().add(mesh), root = new Group().add(branch);
  root.position.set(3, 4, 5); root.rotation.set(.2, .4, -.3); root.scale.setScalar(2);
  assert.doesNotThrow(() => validateEffectiveOpticalGroups([mesh], root));
  branch.position.x = 1;
  assert.throws(() => validateEffectiveOpticalGroups([mesh], root), /bake_member_transforms/);
  assert.throws(() => validateEffectiveOpticalGroups([mesh], new Group()), /common_root/);
  const alone = member(); alone.position.x = 1; assert.throws(() => validate([alone]), /bake_member_transforms/);
});

test('triangle, group and exact-integer limits fail explicitly instead of validating a prefix', () => {
  const many = member(); many.geometry.setIndex(new BufferAttribute(new Uint32Array((EFFECTIVE_OPTICAL_GROUP_LIMITS.triangles + 1) * 3), 1));
  assert.throws(() => validate([many]), /triangle_budget_exceeded/);
  const groups = Array.from({length: 9}, (_, i) => member(`g${i}`, 'member', [[0, 0, i], [1, 0, i], [0, 1, i]]));
  assert.throws(() => validate(groups), /group_budget_exceeded/);
  const large = member('wide', 'member', [[0, 0, 0], [2 ** 127, 0, 0], [0, 1, 2 ** -149]]);
  assert.throws(() => validate([large]), /exact_coordinate_integer_budget_exceeded/);
  const degenerate = member(); degenerate.geometry.setIndex([0, 0, 2]);
  assert.throws(() => validate([degenerate]), /exact_degenerate_triangle/);
});

test('160 deterministic planar pairs agree with an independent integer separating-axis area test', () => {
  let seed = 534279;
  const random = (): number => {seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed;};
  const triangle = (): Point[] => {
    for (;;) {
      const p = Array.from({length: 3}, () => [random() % 17 - 8, random() % 17 - 8, 0] as Point);
      if ((p[1]![0] - p[0]![0]) * (p[2]![1] - p[0]![1]) !== (p[1]![1] - p[0]![1]) * (p[2]![0] - p[0]![0])) return p;
    }
  };
  const positiveArea = (a: Point[], b: Point[]): boolean => {
    for (const polygon of [a, b]) for (let i = 0; i < 3; i++) {
      const p = polygon[i]!, q = polygon[(i + 1) % 3]!, nx = p[1] - q[1], ny = q[0] - p[0];
      const first = a.map(v => v[0] * nx + v[1] * ny), second = b.map(v => v[0] * nx + v[1] * ny);
      if (Math.max(...first) <= Math.min(...second) || Math.max(...second) <= Math.min(...first)) return false;
    }
    return true;
  };
  for (let i = 0; i < 160; i++) {
    const a = triangle(), b = triangle(), meshes = [member('a', 'a', a), member('b', 'b', b)];
    if (positiveArea(a, b)) assert.throws(() => validate(meshes), /cross_group_coincident_patch/, `pair ${i}`);
    else assert.doesNotThrow(() => validate(meshes), `pair ${i}`);
  }
});
