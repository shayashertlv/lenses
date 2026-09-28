/** Bounded, exact geometry validation for an explicitly experimental optical group.
 * One nearest hit per supplied group is an effective appearance convention, not
 * identification of a physical lens or a certificate of reconstruction quality.
 * Positive-area coplanar overlap is tested with exact BigInt rational arithmetic.
 * No welding, area floor, plane epsilon, or caller-supplied proof is accepted.
 * Noncoplanar intersection lines and finite GPU depth ties remain runtime limits.
 */
import {Matrix4, Mesh} from 'three';
import type {BufferAttribute, InterleavedBufferAttribute, Object3D} from 'three';
import {readMaterialLensAppearance} from '../eyewear/lens-appearance.ts';
import {EFFECTIVE_OPTICAL_GROUP_PROFILE} from '../eyewear/optical-material.ts';

export const EFFECTIVE_OPTICAL_GROUP_LIMITS = Object.freeze({
  groups: 8, triangles: 500_000, vertices: 1_500_000, meshes: 4096, candidatePairs: 2_000_000,
  coordinateIntegerBits: 256, arithmeticIntegerBits: 4096,
  arithmeticWork: 500_000_000, broadphaseWork: 20_000_000, heightPolygonVertices: 4096,
});
export interface EffectiveOpticalGroup {readonly id: string; readonly meshes: readonly Mesh[]}
type V3 = readonly [bigint, bigint, bigint];
type P2 = readonly [bigint, bigint];
type Q = readonly [bigint, bigint];
type Q2 = readonly [Q, Q];
type Attribute = BufferAttribute | InterleavedBufferAttribute;
interface Member {mesh: Mesh; group: string; id: string; p: Attribute; n: Attribute; uv: Attribute; faces: number[][]; used: number[]}
interface Triangle {
  member: Member; face: number; indices: number[]; xyz: readonly V3[]; xy: readonly P2[];
  minX: number; maxX: number; minY: number; maxY: number;
  normals?: readonly V3[]; affineV?: readonly Q[];
}
interface Node {minX: number; maxX: number; minY: number; maxY: number; count: number;
  leaves?: Triangle[]; left?: Node; right?: Node}

function requireValue(value: unknown, reason: string): asserts value {
  if (!value) throw new Error(`Effective optical group unsupported: ${reason}`);
}
function abs(v: bigint): bigint {return v < 0n ? -v : v;}
function bits(v: bigint): number {return v === 0n ? 0 : abs(v).toString(2).length;}
class Work {
  arithmetic = 0; broadphase = 0; pairs = 0;
  check(value: bigint): bigint {
    const width = bits(value);
    requireValue(width <= EFFECTIVE_OPTICAL_GROUP_LIMITS.arithmeticIntegerBits, 'arithmetic_integer_budget_exceeded');
    this.arithmetic += Math.max(1, Math.ceil(width / 64));
    requireValue(this.arithmetic <= EFFECTIVE_OPTICAL_GROUP_LIMITS.arithmeticWork, 'arithmetic_work_budget_exceeded');
    return value;
  }
  visit(): void {
    requireValue(++this.broadphase <= EFFECTIVE_OPTICAL_GROUP_LIMITS.broadphaseWork, 'broadphase_work_budget_exceeded');
  }
  pair(): void {
    requireValue(++this.pairs <= EFFECTIVE_OPTICAL_GROUP_LIMITS.candidatePairs, 'candidate_pair_budget_exceeded');
  }
  add(a: bigint, b: bigint): bigint {return this.check(a + b);}
  sub(a: bigint, b: bigint): bigint {return this.check(a - b);}
  mul(a: bigint, b: bigint): bigint {
    requireValue(bits(a) + bits(b) <= EFFECTIVE_OPTICAL_GROUP_LIMITS.arithmeticIntegerBits + 1,
      'arithmetic_integer_budget_exceeded');
    return this.check(a * b);
  }
  gcd(a: bigint, b: bigint): bigint {
    a = abs(a); b = abs(b);
    while (b) {const next = this.check(a % b); a = b; b = next;}
    return a;
  }
  q(n: bigint, d = 1n): Q {
    requireValue(d !== 0n, 'exact_rational_zero_denominator');
    this.check(n); this.check(d);
    if (d < 0n) {n = -n; d = -d;}
    const g = this.gcd(n, d);
    return [n / g, d / g];
  }
  qa(a: Q, b: Q): Q {return this.q(this.add(this.mul(a[0], b[1]), this.mul(b[0], a[1])), this.mul(a[1], b[1]));}
  qs(a: Q, b: Q): Q {return this.q(this.sub(this.mul(a[0], b[1]), this.mul(b[0], a[1])), this.mul(a[1], b[1]));}
  qm(a: Q, b: Q): Q {return this.q(this.mul(a[0], b[0]), this.mul(a[1], b[1]));}
  qd(a: Q, b: Q): Q {return this.q(this.mul(a[0], b[1]), this.mul(a[1], b[0]));}
}

const floatStorage = new ArrayBuffer(8), floatView = new DataView(floatStorage);
/** Exact significand * 2^exponent of a finite float32 value. */
function dyadic(value: number): readonly [bigint, number] {
  if (value === 0) return [0n, 0];
  floatView.setFloat32(0, value, false);
  const raw = floatView.getUint32(0, false), exponent = (raw >>> 23) & 255;
  let n = BigInt((raw & 0x7fffff) + (exponent ? 0x800000 : 0));
  let e = exponent ? exponent - 150 : -149;
  while ((n & 1n) === 0n) {n >>= 1n; e++;}
  return [raw >>> 31 ? -n : n, e];
}
function exactFloat(value: number, work: Work): Q {
  const [n, e] = dyadic(value);
  return e >= 0 ? work.q(n << BigInt(e)) : work.q(n, 1n << BigInt(-e));
}
function sub(a: V3, b: V3, work: Work): V3 {return [work.sub(a[0], b[0]), work.sub(a[1], b[1]), work.sub(a[2], b[2])];}
function cross(a: V3, b: V3, work: Work): V3 {
  return [work.sub(work.mul(a[1], b[2]), work.mul(a[2], b[1])),
    work.sub(work.mul(a[2], b[0]), work.mul(a[0], b[2])),
    work.sub(work.mul(a[0], b[1]), work.mul(a[1], b[0]))];
}
function dot(a: V3, b: V3, work: Work): bigint {
  return work.add(work.add(work.mul(a[0], b[0]), work.mul(a[1], b[1])), work.mul(a[2], b[2]));
}
function zero(v: V3): boolean {return v.every(x => x === 0n);}
function orient(a: P2, b: P2, p: P2, work: Work): bigint {
  return work.sub(work.mul(work.sub(b[0], a[0]), work.sub(p[1], a[1])),
    work.mul(work.sub(b[1], a[1]), work.sub(p[0], a[0])));
}
function qOrient(a: Q2, b: Q2, p: Q2, work: Work): Q {
  return work.qs(work.qm(work.qs(b[0], a[0]), work.qs(p[1], a[1])),
    work.qm(work.qs(b[1], a[1]), work.qs(p[0], a[0])));
}
function positiveArea(first: Triangle, second: Triangle, work: Work): boolean {
  let polygon: Q2[] = first.xy.map(p => [[p[0], 1n], [p[1], 1n]]);
  let boundary = [...second.xy];
  if (orient(boundary[0]!, boundary[1]!, boundary[2]!, work) < 0n) boundary = [boundary[0]!, boundary[2]!, boundary[1]!];
  for (let edge = 0; edge < 3 && polygon.length; edge++) {
    const a = boundary[edge]!, b = boundary[(edge + 1) % 3]!;
    const aq: Q2 = [[a[0], 1n], [a[1], 1n]], bq: Q2 = [[b[0], 1n], [b[1], 1n]];
    const clipped: Q2[] = [];
    let previous = polygon[polygon.length - 1]!, old = qOrient(aq, bq, previous, work);
    for (const current of polygon) {
      const next = qOrient(aq, bq, current, work);
      if ((next[0] >= 0n) !== (old[0] >= 0n)) {
        const t = work.qd(old, work.qs(old, next));
        clipped.push([work.qa(previous[0], work.qm(t, work.qs(current[0], previous[0]))),
          work.qa(previous[1], work.qm(t, work.qs(current[1], previous[1])))]);
      }
      if (next[0] >= 0n) clipped.push(current);
      previous = current; old = next;
    }
    polygon = clipped;
  }
  let area: Q = [0n, 1n];
  for (let i = 1; i + 1 < polygon.length; i++) area = work.qa(area, qOrient(polygon[0]!, polygon[i]!, polygon[i + 1]!, work));
  return area[0] !== 0n;
}
function affineV(triangle: Triangle, work: Work): readonly Q[] {
  if (triangle.affineV) return triangle.affineV;
  const [a, b, c] = triangle.xy as readonly [P2, P2, P2];
  const q = triangle.indices.map(i => exactFloat(triangle.member.uv.getY(i), work));
  const determinant: Q = [orient(a, b, c, work), 1n];
  const weighted = (axis: number): Q => work.qa(work.qa(
    work.qm(q[0]!, [b[axis]! - c[axis]!, 1n]), work.qm(q[1]!, [c[axis]! - a[axis]!, 1n])),
  work.qm(q[2]!, [a[axis]! - b[axis]!, 1n]));
  const x = work.qd(weighted(1), determinant), y0 = weighted(0), y = work.qd([-y0[0], y0[1]], determinant);
  const constant = work.qs(work.qs(q[0]!, work.qm(x, [a[0], 1n])), work.qm(y, [a[1], 1n]));
  return triangle.affineV = [x, y, constant];
}
function normals(triangle: Triangle): readonly V3[] {
  if (triangle.normals) return triangle.normals;
  return triangle.normals = triangle.indices.map(i => {
    const n = triangle.member.n, components = [n.getX(i), n.getY(i), n.getZ(i)].map(dyadic);
    const e = Math.min(...components.filter(([v]) => v !== 0n).map(([, exponent]) => exponent));
    // Independent positive scaling of each corner leaves vertex normalization unchanged.
    return components.map(([value, exponent]) => value === 0n ? 0n : value << BigInt(exponent - e)) as unknown as V3;
  });
}
/** Positive rescaling of each corner (including vertex normalization) preserves
 * origin containment of the convex hull. This exact test allows obtuse normal
 * triples whose interpolant stays nonzero; an acute-cone heuristic is not used.
 */
function requireNonvanishingNormalField(triangle: Triangle, work: Work): void {
  const n = normals(triangle) as readonly [V3, V3, V3];
  const edges = [cross(n[0], n[1], work), cross(n[1], n[2], work), cross(n[2], n[0], work)];
  const where = `(${triangle.member.group}/${triangle.member.id}/${triangle.face})`;
  for (let i = 0; i < 3; i++) requireValue(!zero(edges[i]!) || dot(n[i]!, n[(i + 1) % 3]!, work) > 0n,
    `normal_field_contains_zero_on_edge ${where}`);
  // Rank three: no nontrivial linear combination can equal the origin.
  if (dot(n[0], edges[1]!, work) !== 0n) return;
  const plane = edges.find(edge => !zero(edge));
  if (!plane) return; // Rank one, with antiparallel edges already rejected.
  const axis = plane.findIndex(value => value !== 0n);
  const signs = edges.map(edge => edge[axis]!);
  requireValue(!(signs.every(value => value >= 0n) || signs.every(value => value <= 0n)),
    `normal_field_contains_zero_interior ${where}`);
}
function normalProof(first: Triangle, second: Triangle, work: Work): string | null {
  const n = normals(first), m = normals(second);
  const constant = (vectors: readonly V3[]): boolean => vectors.slice(1).every(v => zero(cross(vectors[0]!, v, work)) && dot(vectors[0]!, v, work) > 0n);
  if (constant(n) && constant(m)) return zero(cross(n[0]!, m[0]!, work)) ? null : 'conflicting_constant_normal_directions';
  const a = new Map(first.xyz.map((p, i) => [p.join(','), n[i]!]));
  const b = new Map(second.xyz.map((p, i) => [p.join(','), m[i]!]));
  if ([...a.keys()].some(key => !b.has(key))) return 'unproven_retessellated_varying_normal_field';
  const signs = new Set<boolean>();
  for (const [key, normal] of a) {
    const other = b.get(key)!;
    if (!zero(cross(normal, other, work))) return 'conflicting_corresponding_corner_directions';
    signs.add(dot(normal, other, work) > 0n);
  }
  if (signs.size !== 1) return 'unproven_nonuniform_normal_sign';
  // Every triangle already passed the exact origin-hull exclusion. No extra
  // acute-cone restriction is needed for these identical interpolated fields.
  return null;
}

function overlaps(a: Node | Triangle, b: Node | Triangle): boolean {
  return a.minX <= b.maxX && b.minX <= a.maxX && a.minY <= b.maxY && b.minY <= a.maxY;
}
function tree(triangles: Triangle[], work: Work): Node {
  const node: Node = {minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity, count: triangles.length};
  for (const t of triangles) {
    work.visit(); node.minX = Math.min(node.minX, t.minX); node.maxX = Math.max(node.maxX, t.maxX);
    node.minY = Math.min(node.minY, t.minY); node.maxY = Math.max(node.maxY, t.maxY);
  }
  if (triangles.length <= 8) {node.leaves = triangles; return node;}
  const x = node.maxX - node.minX >= node.maxY - node.minY;
  triangles.sort((a, b) => {work.visit(); return x ? (a.minX * .5 + a.maxX * .5) - (b.minX * .5 + b.maxX * .5)
    : (a.minY * .5 + a.maxY * .5) - (b.minY * .5 + b.maxY * .5);});
  const mid = Math.floor(triangles.length / 2);
  node.left = tree(triangles.slice(0, mid), work); node.right = tree(triangles.slice(mid), work);
  return node;
}
function visitPairs(node: Node, work: Work, check: (a: Triangle, b: Triangle) => void): void {
  const pairs = (a: Node, b: Node): void => {
    work.visit(); if (!overlaps(a, b)) return;
    if (a.leaves && b.leaves) {
      for (const first of a.leaves) for (const second of b.leaves) {work.visit(); if (overlaps(first, second)) check(first, second);}
    } else if (!a.leaves && (b.leaves || a.count >= b.count)) {pairs(a.left!, b); pairs(a.right!, b);}
    else {pairs(a, b.left!); pairs(a, b.right!);}
  };
  if (node.leaves) {
    for (let i = 0; i < node.leaves.length; i++) for (let j = 0; j < i; j++) {
      work.visit(); const a = node.leaves[i]!, b = node.leaves[j]!; if (overlaps(a, b)) check(a, b);
    }
  } else {visitPairs(node.left!, work, check); visitPairs(node.right!, work, check); pairs(node.left!, node.right!);}
}

/** Closed enclosure of all values that can round to this float32. Endpoints
 * conservatively include ties; no inferred source value is subsequently used. */
function roundingInterval(value: number): readonly [number, number] {
  if (value === 0) return [-(2 ** -150), 2 ** -150];
  floatView.setFloat32(0, value, false); const raw = floatView.getUint32(0, false);
  floatView.setUint32(0, raw + (value > 0 ? -1 : 1), false); const lo = floatView.getFloat32(0, false);
  floatView.setUint32(0, raw + (value > 0 ? 1 : -1), false); const hi = floatView.getFloat32(0, false);
  requireValue(Number.isFinite(lo) && Number.isFinite(hi), 'float32_rounding_envelope_range_exceeded');
  return [value - (value - lo) / 2, value + (hi - value) / 2];
}
function doubleDyadic(value: number): readonly [bigint, number] {
  if (value === 0) return [0n, 0];
  floatView.setFloat64(0, value, false); const raw = floatView.getBigUint64(0, false);
  const exponent = Number((raw >> 52n) & 2047n);
  let n = (raw & ((1n << 52n) - 1n)) + (exponent ? 1n << 52n : 0n);
  let e = exponent ? exponent - 1075 : -1074;
  while ((n & 1n) === 0n) {n >>= 1n; e++;}
  return [raw >> 63n ? -n : n, e];
}
function validateHeight(members: readonly Member[], work: Work): void {
  let minLo = Infinity, minHi = Infinity, maxLo = -Infinity, maxHi = -Infinity, exponent = 0;
  let minY = Infinity, maxY = -Infinity, minimumWitness = false, maximumWitness = false;
  for (const member of members) for (const index of member.used) {
    const y = member.p.getY(index), [lo, hi] = roundingInterval(y), v = member.uv.getY(index);
    minLo = Math.min(minLo, lo); minHi = Math.min(minHi, hi); maxLo = Math.max(maxLo, lo); maxHi = Math.max(maxHi, hi);
    for (const value of [lo, hi]) {const [n, e] = doubleDyadic(value); if (n) exponent = Math.min(exponent, e);}
    if (y < minY) {minY = y; minimumWitness = false;}
    if (y > maxY) {maxY = y; maximumWitness = false;}
    if (y === minY && v === 0) minimumWitness = true;
    if (y === maxY && v === 1) maximumWitness = true;
  }
  requireValue(maxLo > minHi && minimumWitness && maximumWitness, 'group_height_requires_distinct_bottom_0_and_top_1');
  // One common pre-quantization a=minY,b=maxY must satisfy EVERY vertex.
  // Independent per-vertex feasible endpoint intervals are insufficient.
  // a,b are integer-grid homogeneous coordinates; the polygon below clips
  // exact halfplanes yLo <= (1-vHi)*a+vHi*b and
  // (1-vLo)*a+vLo*b <= yHi, with v's rounding cell intersected with [0,1].
  // Monotone rounding puts true extrema at min/max stored Y. Explicit V=0/1
  // witnesses at those stored extrema attain a/b, not just lie between them.
  const coordinate = (value: number): bigint => {
    const [n, e] = doubleDyadic(value); return work.check(n === 0n ? 0n : n << BigInt(e - exponent));
  };
  const constraint = (y: number, v: number, lower: boolean): V3 => {
    const [n, e] = doubleDyadic(v), denominator = e < 0 ? 1n << BigInt(-e) : 1n;
    const numerator = e < 0 ? n : n << BigInt(e), a = work.sub(denominator, numerator), c = work.mul(coordinate(y), denominator);
    return lower ? [a, numerator, -c] : [-a, -numerator, c];
  };
  function* constraints(): Generator<V3> {
    for (const member of members) for (const index of member.used) {
      const [lo, hi] = roundingInterval(member.p.getY(index)), [vLo, vHi] = roundingInterval(member.uv.getY(index));
      yield constraint(lo, Math.min(1, vHi), true);
      yield constraint(hi, Math.max(0, vLo), false);
    }
  }
  // The midpoint stored extrema are often a valid shared witness. This exact
  // all-constraints check avoids constructing a polygon for ordinary assets.
  const witness: V3 = [coordinate(minY), coordinate(maxY), 1n];
  let witnessed = true;
  for (const plane of constraints()) if (dot(plane, witness, work) < 0n) {witnessed = false; break;}
  if (witnessed) return;
  const aLo = coordinate(minLo), aHi = coordinate(minHi), bLo = coordinate(maxLo), bHi = coordinate(maxHi);
  let polygon: V3[] = [[aLo, bLo, 1n], [aHi, bLo, 1n], [aHi, bHi, 1n], [aLo, bHi, 1n]];
  for (const plane of constraints()) {
    const clipped: V3[] = [];
    let previous = polygon[polygon.length - 1]!, old = dot(plane, previous, work);
    for (const current of polygon) {
      const next = dot(plane, current, work);
      if ((old >= 0n) !== (next >= 0n)) {
        let intersection = cross(cross(previous, current, work), plane, work);
        requireValue(intersection[2] !== 0n, 'height_feasibility_nonfinite_intersection');
        let divisor = intersection.reduce((g, n) => work.gcd(g, n), 0n);
        if (intersection[2] < 0n) divisor = -divisor;
        intersection = intersection.map(n => n / divisor) as unknown as V3;
        clipped.push(intersection);
      }
      if (next >= 0n) clipped.push(current);
      previous = current; old = next;
    }
    polygon = clipped.filter((p, i) => i === 0 || p.some((v, axis) => v !== clipped[i - 1]![axis]));
    requireValue(polygon.length > 0, 'intrinsic_V_does_not_match_group_wide_Y_height');
    requireValue(polygon.length <= EFFECTIVE_OPTICAL_GROUP_LIMITS.heightPolygonVertices, 'height_polygon_budget_exceeded');
  }
}

function baked(meshes: readonly Mesh[], supplied?: Object3D): void {
  let root = supplied;
  if (!root && meshes.length) {
    root = meshes[0]!.parent ?? undefined;
    while (root && !meshes.every(mesh => {for (let o: Object3D | null = mesh.parent; o; o = o.parent) if (o === root) return true; return false;})) root = root.parent ?? undefined;
  }
  for (const mesh of meshes) {
    const relative = new Matrix4(); let object: Object3D | null = mesh;
    while (object && object !== root) {if (object.matrixAutoUpdate) object.updateMatrix(); relative.premultiply(object.matrix); object = object.parent;}
    requireValue(!root || object === root, 'member_is_not_descended_from_common_root');
    requireValue(relative.elements.every((v, i) => v === (i % 5 === 0 ? 1 : 0)), 'bake_member_transforms_into_common_coordinates');
    requireValue(mesh !== root, 'common_root_must_be_above_optical_members');
  }
}

/** Validates actual loaded float32 attributes. SHA metadata is checked for shape
 * and consistency only; it is not treated as a cryptographic authenticity proof.
 * Distinct groups remain distinct even when they share a Material object.
 * Optional root may be posed; members must be baked in that root's frame.
 */
export function validateEffectiveOpticalGroups(meshes: readonly Mesh[], root?: Object3D): readonly EffectiveOpticalGroup[] {
  requireValue(meshes.length <= EFFECTIVE_OPTICAL_GROUP_LIMITS.meshes, 'mesh_budget_exceeded');
  requireValue(new Set(meshes).size === meshes.length, 'duplicate_mesh_reference');
  baked(meshes, root);
  const grouped = new Map<string, Member[]>(), descriptors = new Map<string, string>(), hashes = new Map<string, string>();
  const sourceParts = new Set<number>(); let sourceSha: string | undefined, triangles = 0, vertices = 0, exponent = 0;
  const slug = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/, sha = /^[a-f0-9]{64}$/;
  for (const mesh of meshes) {
    requireValue(mesh instanceof Mesh && !('isSkinnedMesh' in mesh) && !('isInstancedMesh' in mesh) && !('isBatchedMesh' in mesh)
      && !mesh.morphTargetInfluences?.length && !Object.values(mesh.geometry.morphAttributes).some(a => a?.length), 'static_noninstanced_mesh_required');
    const data = mesh.userData, group = data.opticalGroupId as string, id = data.opticalGroupMemberId as string;
    requireValue(data.partRole === 'lens' && data.lensSurfaceProfile === EFFECTIVE_OPTICAL_GROUP_PROFILE
      && data.lensUVConvention === 'lens_local_bottom_0_top_1', 'explicit_profile_and_UV_metadata_required');
    requireValue(typeof group === 'string' && slug.test(group) && typeof id === 'string' && slug.test(id), 'invalid_group_or_member_id');
    requireValue(typeof data.opticalSourceSha256 === 'string' && sha.test(data.opticalSourceSha256)
      && typeof data.lensAppearanceSha256 === 'string' && sha.test(data.lensAppearanceSha256), 'source_and_appearance_SHA_metadata_required');
    requireValue(data.semanticIdentity === 'unverified' && data.materialIdentification === 'unmeasured', 'explicit_unverified_source_identity_required');
    requireValue(Number.isSafeInteger(data.opticalSourcePartIndex) && data.opticalSourcePartIndex >= 0
      && !sourceParts.has(data.opticalSourcePartIndex), 'duplicate_or_invalid_source_part');
    sourceParts.add(data.opticalSourcePartIndex);
    sourceSha ??= data.opticalSourceSha256;
    requireValue(sourceSha === data.opticalSourceSha256, 'inconsistent_source_SHA');
    requireValue(!Array.isArray(mesh.material), 'one_material_per_member_required');
    const descriptor = readMaterialLensAppearance(mesh.material);
    requireValue(descriptor, 'canonical_descriptor_required');
    const canonical = JSON.stringify(descriptor);
    requireValue(!descriptors.has(group) || descriptors.get(group) === canonical, 'conflicting_group_descriptors');
    requireValue(!hashes.has(group) || hashes.get(group) === data.lensAppearanceSha256, 'conflicting_group_appearance_SHA_metadata');
    descriptors.set(group, canonical); hashes.set(group, data.lensAppearanceSha256);
    const geometry = mesh.geometry, p = geometry.getAttribute('position'), n = geometry.getAttribute('normal'), uv = geometry.getAttribute('uv');
    const attribute = (a: Attribute | undefined, size: number): a is Attribute => !!a && a.itemSize === size
      && !a.normalized && a.array instanceof Float32Array;
    requireValue(attribute(p, 3) && Number.isSafeInteger(p.count) && p.count >= 3 && attribute(n, 3) && n.count === p.count && attribute(uv, 2)
      && uv.count === p.count, 'float32_XYZ_NORMAL_UV_attributes_required');
    vertices += p.count; requireValue(vertices <= EFFECTIVE_OPTICAL_GROUP_LIMITS.vertices, 'vertex_budget_exceeded');
    const indices = geometry.index, count = indices?.count ?? p.count;
    requireValue(count > 0 && count % 3 === 0 && (!indices || (!indices.normalized && indices.itemSize === 1
      && (indices.array instanceof Uint32Array || indices.array instanceof Uint16Array || indices.array instanceof Uint8Array))), 'complete_integer_indexed_triangles_required');
    triangles += count / 3; requireValue(triangles <= EFFECTIVE_OPTICAL_GROUP_LIMITS.triangles, 'triangle_budget_exceeded');
    requireValue(geometry.drawRange.start === 0 && (geometry.drawRange.count === Infinity || geometry.drawRange.count === count)
      && (geometry.groups.length === 0 || (geometry.groups.length === 1 && geometry.groups[0]!.start === 0
        && geometry.groups[0]!.count === count && (geometry.groups[0]!.materialIndex ?? 0) === 0)), 'partial_or_multimaterial_draw_not_supported');
    for (let i = 0; i < p.count; i++) requireValue([p.getX(i), p.getY(i), p.getZ(i), n.getX(i), n.getY(i), n.getZ(i), uv.getX(i), uv.getY(i)].every(Number.isFinite), 'nonfinite_geometry_or_attributes');
    const faces: number[][] = [], usedSet = new Set<number>();
    for (let offset = 0; offset < count; offset += 3) {
      const face = [0, 1, 2].map(corner => indices ? indices.getX(offset + corner) : offset + corner);
      requireValue(face.every(i => Number.isSafeInteger(i) && i >= 0 && i < p.count), 'invalid_triangle_index');
      faces.push(face); for (const i of face) usedSet.add(i);
    }
    const used = [...usedSet];
    for (const i of used) {
      requireValue(n.getX(i) !== 0 || n.getY(i) !== 0 || n.getZ(i) !== 0, 'zero_referenced_normal');
      requireValue(uv.getY(i) >= 0 && uv.getY(i) <= 1, 'intrinsic_V_outside_0_1');
      for (const value of [p.getX(i), p.getY(i), p.getZ(i)]) {const [v, e] = dyadic(value); if (v !== 0n) exponent = Math.min(exponent, e);}
    }
    const members = grouped.get(group) ?? [];
    requireValue(!members.some(member => member.id === id), 'duplicate_group_member_id');
    members.push({mesh, group, id, p, n, uv, faces, used}); grouped.set(group, members);
    requireValue(grouped.size <= EFFECTIVE_OPTICAL_GROUP_LIMITS.groups, 'group_budget_exceeded');
  }
  const work = new Work(), planes = new Map<string, Triangle[]>();
  for (const members of grouped.values()) {
    validateHeight(members, work);
    for (const member of members) {
      const points = new Map<number, V3>();
      for (const i of member.used) {
        const point = [member.p.getX(i), member.p.getY(i), member.p.getZ(i)].map(value => {
          const [n, e] = dyadic(value); const result = n === 0n ? 0n : n << BigInt(e - exponent);
          requireValue(bits(result) <= EFFECTIVE_OPTICAL_GROUP_LIMITS.coordinateIntegerBits, 'exact_coordinate_integer_budget_exceeded'); return result;
        }) as unknown as V3;
        points.set(i, point);
      }
      for (let face = 0; face < member.faces.length; face++) {
        const indices = member.faces[face]!, xyz = indices.map(i => points.get(i)!);
        const n = cross(sub(xyz[1]!, xyz[0]!, work), sub(xyz[2]!, xyz[0]!, work), work);
        requireValue(!zero(n), `exact_degenerate_triangle (${member.group}/${member.id}/${face})`);
        const coefficients = [...n, -dot(n, xyz[0]!, work)];
        let divisor = coefficients.reduce((a, b) => work.gcd(a, b), 0n);
        if (n.find(v => v !== 0n)! < 0n) divisor = -divisor;
        const plane = coefficients.map(v => v / divisor), key = plane.join(',');
        let axis = 0; for (let i = 1; i < 3; i++) if (abs(plane[i]!) > abs(plane[axis]!)) axis = i;
        const axes = [0, 1, 2].filter(i => i !== axis), xy = xyz.map(p => [p[axes[0]!]!, p[axes[1]!]!] as P2);
        const coords = indices.map(i => [member.p.getX(i), member.p.getY(i), member.p.getZ(i)]);
        const t: Triangle = {member, face, indices, xyz, xy,
          minX: Math.min(...coords.map(p => p[axes[0]!]!)), maxX: Math.max(...coords.map(p => p[axes[0]!]!)),
          minY: Math.min(...coords.map(p => p[axes[1]!]!)), maxY: Math.max(...coords.map(p => p[axes[1]!]!))};
        requireNonvanishingNormalField(t, work);
        const collection = planes.get(key); if (collection) collection.push(t); else planes.set(key, [t]);
      }
    }
  }
  const check = (a: Triangle, b: Triangle): void => {
    work.pair(); if (!positiveArea(a, b, work)) return;
    const where = `(${a.member.group}/${a.member.id}/${a.face}, ${b.member.group}/${b.member.id}/${b.face})`;
    requireValue(a.member.group === b.member.group, `cross_group_coincident_patch_undefined_order ${where}`);
    const first = affineV(a, work), second = affineV(b, work);
    requireValue(first.every((q, i) => q[0] === second[i]![0] && q[1] === second[i]![1]), `conflicting_intrinsic_v_affine_fields ${where}`);
    const reason = normalProof(a, b, work); requireValue(reason === null, `${reason} ${where}`);
  };
  for (const triangles of planes.values()) if (triangles.length > 1) visitPairs(tree(triangles, work), work, check);
  return Object.freeze([...grouped.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
    .map(([id, members]) => Object.freeze({id, meshes: Object.freeze(members.map(member => member.mesh))})));
}
