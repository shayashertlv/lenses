import {WEARER_SIDES} from './sides.ts';
/** The one normalisation pass (WP1_DESIGN.md §1): it runs on the loaded scene before any subsystem (lens-dependent
 *  rear drop, continuity, temple visibility and clip, hair occlusion, terminal fit, shadows, twins) reads it.
 *
 *  In order: transforms (one check at 1e-6 absolute, EXT_mesh_gpu_instancing; within tolerance the transform is baked
 *  so every later consumer sees exact identity), geometry (finite positions, in-range indices declared unsigned SCALAR,
 *  static, triangles;
 *  LINES/POINTS are removed and reported, never silently dropped; a primitive without XYZ positions fails the Format
 *  row as spec.py fails it and is removed, reported), units (an X span of 60-250 units is millimetres,
 *  6-25 centimetres: rescaled with a loud warning, KHR_materials_volume lengths included), lens identification
 *  (optical-material.ts, which decides every class once), the Components row, the measured Origin and Orientation
 *  rows (measure.ts, the port of spec.py), and the live-site notes that need the classes.
 *
 *  §1-table violations are refusals: the pass records them and keeps checking every row it can, then throws
 *  ModelRefusal with the whole report. Everything else renders, with a warning (and a fallback entry when arview
 *  changes what it draws). */
import {BufferAttribute, Line, Matrix4, Mesh, MeshPhysicalMaterial, Points, Vector3} from 'three';
import type {BufferGeometry, InterleavedBufferAttribute, Material, Object3D} from 'three';
import {classifyAssetMaterials, materialClassOf, RETIRED_LENS_APPEARANCE_EXTENSION, stripRetiredLensAppearance} from '../eyewear/optical-material.ts';
import type {AssetMaterialClassification, GltfSource} from '../eyewear/optical-material.ts';
import {describeError, INSTANCING_EXTENSION, isRecord, jsonPrimitives} from './glb.ts';
import type {Json} from './glb.ts';
import {measureBounds, orientationRow, originRow, unitsRow} from './measure.ts';
import type {MeasuredGeometry, PointSet, RowSink} from './measure.ts';
import type {ModelReportBuilder, SpecRowName} from './report.ts';

export const IDENTITY_ATOL = 1e-6;
export const ARVIEW_WARN_TRIANGLES = 300_000;
export const LIVE_MAX_TRIANGLES = 100_000;
export const LIVE_TEMPLE_X_M = 0.045;
/** A degenerate (flat) triangle's height over its longest edge is at most this many float32 steps of its own largest
 *  coordinate: h <= 8 * eps32 * max|coord| (spec.py DEGENERATE_FLOAT32_ULPS). */
export const DEGENERATE_FLOAT32_ULPS = 8;
const FLOAT32_EPS = 2 ** -23;
const MODE_NAMES: Readonly<Record<number, string>> = {0: 'POINTS', 1: 'LINES', 2: 'LINE_LOOP', 3: 'LINE_STRIP'};

export interface NormalizedModel {
  readonly classification: AssetMaterialClassification;
  /** The rescale applied to the file's units: 1, 0.001 (millimetres) or 0.01 (centimetres). */
  readonly unitsScale: 1 | 0.001 | 0.01;
  /** The rendered triangle meshes (LINES/POINTS removed). */
  readonly meshes: readonly Mesh[];
}

const slotMaterials = (mesh: Mesh): Material[] => Array.isArray(mesh.material) ? mesh.material : [mesh.material];
const listNames = (names: readonly string[]): string => JSON.stringify([...new Set(names)].slice(0, 8));

/** The glTF node a three object came from, or null (a multi-primitive node is the primitive's parent Group). */
function gltfNodeIndex(object: Object3D, source: GltfSource | null): number | null {
  if (!source) return null;
  const own = source.associations.get(object);
  if (own?.nodes !== undefined) return own.nodes;
  return object.parent ? source.associations.get(object.parent)?.nodes ?? null : null;
}
/** The name a primitive goes by in the report: its glTF node's name (as the Python validator names it), else the three
 *  object's own name. */
export function nodeName(object: Object3D, source: GltfSource | null): string {
  const index = gltfNodeIndex(object, source);
  if (index !== null && isRecord(source?.json)) {
    const node = Array.isArray(source.json.nodes) ? source.json.nodes[index] : undefined;
    return isRecord(node) && typeof node.name === 'string' ? node.name : `node${index}`;
  }
  return object.name || '(unnamed mesh)';
}

function relativeMatrix(object: Object3D, rootInverse: Matrix4): Matrix4 {return rootInverse.clone().multiply(object.matrixWorld);}
const isExactIdentity = (matrix: Matrix4): boolean => matrix.elements.every((value, index) => value === (index % 5 === 0 ? 1 : 0));
const withinIdentity = (matrix: Matrix4): boolean => matrix.elements.every((value, index) => Number.isFinite(value) && Math.abs(value - (index % 5 === 0 ? 1 : 0)) <= IDENTITY_ATOL);

/** The index componentTypes glTF allows (unsigned byte, short, int). */
const INDEX_COMPONENT_TYPES: readonly number[] = [5121, 5123, 5125];
/** The index arrays WebGL's drawElements takes (UNSIGNED_BYTE, UNSIGNED_SHORT, UNSIGNED_INT). three hands any other
 *  typed array to it as its own type (a Float32Array as gl.FLOAT: INVALID_ENUM, nothing drawn) and ignores
 *  `normalized` on an index. */
const isDrawableIndexArray = (array: ArrayLike<number>): boolean => array instanceof Uint8Array || array instanceof Uint16Array || array instanceof Uint32Array;
type IndexAttribute = BufferAttribute | InterleavedBufferAttribute;
const isInterleaved = (index: IndexAttribute): index is InterleavedBufferAttribute => (index as InterleavedBufferAttribute).isInterleavedBufferAttribute === true;

/** Every value an index attribute stores, as the file stores it (value k is component k % itemSize of element
 *  floor(k / itemSize), as spec.py reads a non-SCALAR accessor). Raw array reads: BufferAttribute.getX/getComponent
 *  DENORMALIZE a normalized attribute (a stored 5 reads 5/65535), and an index buffer view with a byteStride arrives as
 *  an InterleavedBufferAttribute. */
function rawIndexValues(index: IndexAttribute): {readonly count: number; at(k: number): number} {
  const itemSize = index.itemSize, count = index.count * itemSize;
  if (isInterleaved(index)) {
    const array = index.data.array, stride = index.data.stride, offset = index.offset;
    return {count, at: k => array[Math.floor(k / itemSize) * stride + offset + k % itemSize]!};
  }
  const array = index.array;
  return {count, at: k => array[k]!};
}

/** The index accessor of `mesh`'s glTF primitive, from the original JSON through three's associations, or null (a
 *  hand-built scene, or an unindexed primitive). */
function jsonIndexAccessor(mesh: Mesh, source: GltfSource | null, json: Json | null): {accessor: Json} | null {
  const association = source?.associations.get(mesh);
  if (!json || association?.meshes === undefined || association.primitives === undefined) return null;
  const meshDef = Array.isArray(json.meshes) ? json.meshes[association.meshes] : undefined;
  const primitive = isRecord(meshDef) && Array.isArray(meshDef.primitives) ? meshDef.primitives[association.primitives] : undefined;
  const accessor = isRecord(primitive) && typeof primitive.indices === 'number' && Array.isArray(json.accessors) ? json.accessors[primitive.indices] : undefined;
  return isRecord(accessor) ? {accessor} : null;
}

/** Why `mesh`'s index accessor is not valid glTF, worded as spec.py `_index_issue` words it (componentType, type,
 *  normalized), or null. The declaration is read from the original JSON (three converts strips and fans, so its
 *  attribute does not always show it); a hand-built scene is judged on its index attribute. (A negative index is the
 *  refusal below, as spec.py fails "index -1 < 0".) */
function indexAccessorIssue(declared: {accessor: Json} | null, mesh: Mesh, source: GltfSource | null, index: IndexAttribute | null): string | null {
  if (declared) {
    const {accessor} = declared, issues: string[] = [];
    if (!INDEX_COMPONENT_TYPES.includes(accessor.componentType as number)) {
      issues.push(`indices componentType ${String(accessor.componentType)} is not valid glTF (indices are unsigned: 5121, 5123 or 5125)`);
    }
    if (accessor.type !== 'SCALAR') issues.push(`indices accessor type '${String(accessor.type)}' is not SCALAR`);
    if (accessor.normalized) issues.push('indices accessor is normalized');
    return issues.join('; ') || null;
  }
  // A glTF primitive without an index accessor: three's index (if any) is its own strip/fan conversion, not the file's.
  if (!index || source?.associations.get(mesh)?.primitives !== undefined) return null;
  const array = isInterleaved(index) ? index.data.array : index.array, issues: string[] = [];
  if (!isDrawableIndexArray(array)) issues.push(`indices stored as ${array.constructor.name} (indices are unsigned integers)`);
  if (index.itemSize !== 1) issues.push(`indices attribute has ${index.itemSize} components, not 1 (SCALAR)`);
  if (index.normalized) issues.push('indices attribute is normalized');
  return issues.join('; ') || null;
}

/** Triangle index triples of a mesh with each group's material: [start, count) ranges over the index (or the vertices
 *  for a non-indexed geometry), one per material slot. */
function triangleRanges(mesh: Mesh): {material: Material; start: number; count: number}[] {
  const geometry: BufferGeometry = mesh.geometry;
  const total = geometry.getIndex()?.count ?? geometry.getAttribute('position')?.count ?? 0;
  if (!Array.isArray(mesh.material)) return [{material: mesh.material, start: 0, count: total}];
  return geometry.groups.flatMap(group => {
    const material = (mesh.material as Material[])[group.materialIndex ?? 0];
    return material ? [{material, start: group.start, count: Math.min(group.count, total - group.start)}] : [];
  });
}

/** Unique referenced vertices and triangles of `meshes`, world metres relative to `root`, split by `classOf`. */
function collectPoints(meshes: readonly Mesh[], rootInverse: Matrix4, classOf: (material: Material) => 'lens' | 'frame'):
  MeasuredGeometry & {all: Float64Array} {
  const buckets = {lens: {vertices: [] as number[], triangles: [] as number[]}, frame: {vertices: [] as number[], triangles: [] as number[]}};
  const point = new Vector3();
  for (const mesh of meshes) {
    const geometry: BufferGeometry = mesh.geometry, position = geometry.getAttribute('position'), index = geometry.getIndex();
    const matrix = relativeMatrix(mesh, rootInverse), identity = isExactIdentity(matrix);
    const read = (vertex: number): [number, number, number] => {
      point.fromBufferAttribute(position, vertex); if (!identity) point.applyMatrix4(matrix);
      return [point.x, point.y, point.z];
    };
    const seen = {lens: new Set<number>(), frame: new Set<number>()}, total = index?.count ?? position.count;
    for (const range of triangleRanges(mesh)) {
      const bucket = classOf(range.material), end = Math.min(range.start + range.count, total);
      for (let offset = range.start; offset + 3 <= end; offset += 3) {
        for (let corner = 0; corner < 3; corner++) {
          const vertex = index ? index.getX(offset + corner) : offset + corner;
          const xyz = read(vertex);
          buckets[bucket].triangles.push(...xyz);
          if (!seen[bucket].has(vertex)) {seen[bucket].add(vertex); buckets[bucket].vertices.push(...xyz);}
        }
      }
    }
  }
  const pack = (bucket: {vertices: number[]; triangles: number[]}): PointSet => ({vertices: Float64Array.from(bucket.vertices), triangles: Float64Array.from(bucket.triangles)});
  const lens = pack(buckets.lens), frame = pack(buckets.frame);
  const all = new Float64Array(lens.vertices.length + frame.vertices.length); all.set(lens.vertices); all.set(frame.vertices, lens.vertices.length);
  return {lens, frame, all};
}

/** Scale positions and KHR_materials_volume lengths (thicknessFactor, attenuationDistance) of every mesh by `scale`. */
function rescale(meshes: readonly Mesh[], scale: number): {geometries: number; materials: string[]} {
  const geometries = new Set<BufferGeometry>(), materials = new Set<MeshPhysicalMaterial>();
  for (const mesh of meshes) {
    geometries.add(mesh.geometry);
    for (const material of slotMaterials(mesh)) if (material instanceof MeshPhysicalMaterial) materials.add(material);
  }
  for (const geometry of geometries) geometry.scale(scale, scale, scale);
  const touched: string[] = [];
  for (const material of materials) {
    let changed = false;
    if (Number.isFinite(material.thickness) && material.thickness > 0) {material.thickness *= scale; changed = true;}
    if (Number.isFinite(material.attenuationDistance) && material.attenuationDistance > 0) {material.attenuationDistance *= scale; changed = true;}
    if (changed) touched.push(material.name || '(unnamed material)');
  }
  return {geometries: geometries.size, materials: touched};
}

/** Bake each mesh's (near-identity) transform into its geometry and reset every node under `root` to identity. */
function bakeTransforms(root: Object3D, meshes: readonly Mesh[], rootInverse: Matrix4): number {
  const users = new Map<BufferGeometry, number>();
  for (const mesh of meshes) users.set(mesh.geometry, (users.get(mesh.geometry) ?? 0) + 1);
  let baked = 0;
  for (const mesh of meshes) {
    const matrix = relativeMatrix(mesh, rootInverse);
    if (isExactIdentity(matrix)) continue;
    if ((users.get(mesh.geometry) ?? 0) > 1) mesh.geometry = mesh.geometry.clone();
    mesh.geometry.applyMatrix4(matrix); baked++;
  }
  root.traverse(object => {
    if (object === root) return;
    object.position.set(0, 0, 0); object.quaternion.identity(); object.scale.set(1, 1, 1); object.matrix.identity();
  });
  root.updateWorldMatrix(true, true);
  return baked;
}

/** Run the normalisation pass on `root` (the glTF scene, or a hand-built test scene), writing into `report`. Throws
 *  ModelRefusal (report.throwIfRefused) after checking every row it can when a §1 refusal was recorded. */
export function normalizeModel(root: Object3D, report: ModelReportBuilder, source: GltfSource | null = null): NormalizedModel {
  const json: Json | null = isRecord(source?.json) ? source.json : null;
  // A retired canonical lens descriptor is stripped (reported each) before anything reads the materials, so the lens
  // draws through its own glTF material like every native lens.
  for (const name of stripRetiredLensAppearance(root)) {
    report.fallback('lens appearance', `Material "${name}" carried the retired ${RETIRED_LENS_APPEARANCE_EXTENSION} descriptor; it was stripped and `
      + 'the material is drawn as its native glTF material.');
  }
  root.updateWorldMatrix(true, true);
  const rootInverse = root.matrixWorld.clone().invert();
  const meshes: Mesh[] = [], strays: (Line | Points)[] = [];
  root.traverse(object => {
    if (object.userData.templeVisibilityOverlay === true) return;
    if (object instanceof Mesh) meshes.push(object);
    else if (object instanceof Line || object instanceof Points) strays.push(object);
  });
  const name = (object: Object3D) => nodeName(object, source);

  // Transforms: every primitive's effective transform relative to the root, one check at 1e-6 absolute.
  const nonIdentity = [...meshes, ...strays].filter(object => !withinIdentity(relativeMatrix(object, rootInverse))).map(name);
  if (nonIdentity.length) {
    report.refuse('transforms', `${new Set(nonIdentity).size} mesh node(s) with a non-identity world transform (tolerance 1e-06 absolute): `
      + `${listNames(nonIdentity)}`);
  }
  const instancedJson = json && Array.isArray(json.nodes) ? json.nodes.flatMap((node, index) =>
    isRecord(node) && isRecord(node.extensions) && INSTANCING_EXTENSION in node.extensions ? [typeof node.name === 'string' ? node.name : `node${index}`] : []) : [];
  const instanced = instancedJson.length ? instancedJson
    : meshes.filter(mesh => (mesh as Mesh & {isInstancedMesh?: boolean}).isInstancedMesh === true).map(name);
  if (instanced.length) {
    report.refuse('transforms', `${new Set(instanced).size} node(s) use ${INSTANCING_EXTENSION}: instanced copies are drawn at their own transforms, `
      + `which are not baked: ${listNames(instanced)}`);
  }
  // The default scene's primitives as the JSON lists them (spec.py's `prims`): shared meshes and skinned nodes are
  // judged on these. three parsed the default scene with node skins removed (glb.ts defaultSceneDocument), so the
  // JSON, not three's SkinnedMesh, says which drawn meshes are skinned.
  const jsonPrims = json ? jsonPrimitives(json) : [];
  const jsonNodes = json && Array.isArray(json.nodes) ? json.nodes : [];
  const skinnedNodes = new Set(jsonPrims.filter(primitive => {const node = jsonNodes[primitive.node]; return isRecord(node) && node.skin !== undefined;})
    .map(primitive => primitive.node));
  const isSkinned = (mesh: Mesh): boolean => (mesh as Mesh & {isSkinnedMesh?: boolean}).isSkinnedMesh === true
    || skinnedNodes.has(gltfNodeIndex(mesh, source) ?? -1);
  const hasMorphTargets = (mesh: Mesh): boolean => {
    const association = source?.associations.get(mesh);
    return Object.values(mesh.geometry.morphAttributes).some(attributes => attributes.length > 0)
      || jsonPrims.some(primitive => primitive.mesh === association?.meshes && primitive.primitive === association?.primitives && primitive.morphTargets);
  };
  if (json) {
    const users = new Map<number, Set<number>>();
    for (const primitive of jsonPrims) users.set(primitive.mesh, (users.get(primitive.mesh) ?? new Set()).add(primitive.node));
    const meshNames = Array.isArray(json.meshes) ? json.meshes : [];
    const shared = [...users].filter(([, nodes]) => nodes.size > 1).map(([mesh]) => {
      const row = meshNames[mesh]; return isRecord(row) && typeof row.name === 'string' ? row.name : `mesh${mesh}`;
    }).sort();
    if (shared.length) report.warn(`mesh(es) drawn by more than one node (duplicate geometry): ${listNames(shared)}`);
  }
  if (!nonIdentity.length && !instanced.length) {
    const baked = bakeTransforms(root, meshes, rootInverse);
    if (baked) report.note('transforms', `${baked} mesh(es) within ${IDENTITY_ATOL} of identity were baked to exact identity`);
  }

  // Geometry.
  for (const stray of strays) {
    const mode = stray instanceof Points ? 0 : (stray as Line & {isLineSegments?: boolean}).isLineSegments ? 1
      : (stray as Line & {isLineLoop?: boolean}).isLineLoop ? 2 : 3;
    report.fail('geometry', `${name(stray)}: primitive mode ${mode} (${MODE_NAMES[mode]}) is not triangles`);
    report.fallback('geometry', `"${name(stray)}" is a ${MODE_NAMES[mode]} primitive (not triangles): it is not drawn (§2 Geometry).`);
    stray.removeFromParent();
  }
  // spec.py leaves every primitive that fails a geometry check out of the measurements and lists it: strays included.
  const measurable: Mesh[] = [], excluded = new Set<string>(strays.map(name)), unreadable = new Set<Mesh>();
  let triangleTotal = 0;
  for (const mesh of meshes) {
    const label = name(mesh), geometry: BufferGeometry = mesh.geometry;
    const bad: string[] = [];
    const position = geometry.getAttribute('position'), index = geometry.getIndex() as IndexAttribute | null;
    if (isSkinned(mesh)) report.refuse('geometry', `${label}: skinned mesh (the model must be static)`);
    if (hasMorphTargets(mesh)) report.refuse('geometry', `${label}: morph targets (the model must be static)`);
    if (!position || position.itemSize < 3) {
      // Valid glTF that clients skip (a primitive without POSITION): spec.py fails the Format row ("geometry
      // unreadable") and checks the rest, so arview does the same, leaves it out of every measurement, and does not
      // draw it (removed after lens identification, so the identification lists every use as spec.py does).
      report.fail('format', `${label}: geometry unreadable (${position ? `POSITION has ${position.itemSize} component(s), not XYZ` : 'no POSITION attribute'})`);
      unreadable.add(mesh); excluded.add(label); continue;
    }
    let finite = true;
    for (let i = 0; i < position.count && finite; i++) finite = Number.isFinite(position.getX(i)) && Number.isFinite(position.getY(i)) && Number.isFinite(position.getZ(i));
    if (!finite) {report.refuse('geometry', `${label}: non-finite vertex positions`); bad.push('finite');}
    // The index accessor as the file declares it (spec.py `_index_issue`: unsigned SCALAR, not normalized). A primitive
    // with an invalid declaration fails the row and is left out of the measurements, as spec.py does; it still draws.
    const declared = jsonIndexAccessor(mesh, source, json);
    const indexIssue = indexAccessorIssue(declared, mesh, source, index);
    if (indexIssue) {report.fail('geometry', `${label}: ${indexIssue}`); bad.push('index-type');}
    if (index) {
      // Every stored value, read raw (never denormalized): a normalized or FLOAT declaration still stores the vertex
      // numbers, and a value that is negative, fractional or not finite names no vertex (out of range: §1 refuses it).
      const raw = rawIndexValues(index);
      let maxIndex = -1, minIndex = 0, nonInteger: number | null = null;
      for (let k = 0; k < raw.count; k++) {
        const value = raw.at(k);
        if (!Number.isInteger(value)) {nonInteger ??= value; continue;}
        if (value > maxIndex) maxIndex = value;
        if (value < minIndex) minIndex = value;
      }
      if (minIndex < 0) {report.refuse('geometry', `${label}: index ${minIndex} < 0 (out of range)`); bad.push('index');}
      if (nonInteger !== null) {report.refuse('geometry', `${label}: index value ${nonInteger} is not an integer (out of range: no vertex has that number)`); bad.push('index');}
      if (maxIndex >= position.count) {report.refuse('geometry', `${label}: index ${maxIndex} >= vertex count ${position.count}`); bad.push('index');}
      const array = isInterleaved(index) ? index.data.array : index.array;
      if (!bad.includes('index') && (index.itemSize !== 1 || index.normalized || isInterleaved(index) || !isDrawableIndexArray(array))) {
        // WebGL draws unsigned, non-interleaved SCALAR indices only, and every later reader (getX) would denormalize a
        // normalized one: rewritten from the stored values so the primitive draws what the file stores.
        const rewritten = new Uint32Array(raw.count);
        for (let k = 0; k < raw.count; k++) rewritten[k] = raw.at(k);
        geometry.setIndex(new BufferAttribute(rewritten, 1));
        const stored = `${array.constructor.name}${index.itemSize === 1 ? '' : ` x${index.itemSize}`}${index.normalized ? ' (normalized)' : ''}${isInterleaved(index) ? ' (interleaved)' : ''}`;
        report.fallback('geometry', `"${label}" stores its indices as ${stored} (${indexIssue ? 'not valid glTF' : 'not drawable by WebGL as stored'}): `
          + 'they were rewritten as unsigned 32-bit SCALAR indices so it draws (§2 Geometry).');
      }
      // (A TRIANGLE_STRIP/FAN with a normalized or non-SCALAR index arrives already rebuilt from its stored values:
      // load.ts rebuildMisdeclaredStrips, which reports it.)
    }
    const count = geometry.getIndex()?.count ?? position.count;
    if (count % 3) {
      report.fail('geometry', `${label}: ${count} ${index ? 'indices' : 'vertices'} is not a multiple of 3`);
      const kept = count - count % 3, indices = new Uint32Array(kept), current = geometry.getIndex() as IndexAttribute | null;
      const stored = current ? rawIndexValues(current) : null;
      for (let i = 0; i < kept; i++) indices[i] = stored ? stored.at(i) : i;
      geometry.setIndex(Array.from(indices));
      report.fallback('geometry', `"${label}" has ${count} ${index ? 'indices' : 'vertices'}, not a multiple of 3: the trailing ${count % 3} are dropped and `
        + 'the rest drawn as triangles (§2 Geometry).');
      bad.push('count');
    }
    const triangles = Math.floor(count / 3);
    triangleTotal += triangles;
    // A skinned mesh is refused but still measured in its bind pose (spec.py fails the row for skins and measures every
    // primitive that passes the per-primitive checks); morph targets are left out, as spec.py does.
    if (bad.length || hasMorphTargets(mesh)) {excluded.add(label); continue;}
    if (!triangles) {report.warn(`${label}: primitive has no triangles`); continue;}
    // Flat at the precision the file stores it in (spec.py DEGENERATE_FLOAT32_ULPS): the height over the longest edge
    // within 8 float32 steps of the triangle's largest coordinate. Never an area in file units: the rule scales with the
    // model, and a tiny well-shaped triangle is not flat. The same float64 arithmetic as spec.py on the same float32s.
    let degenerate = 0;
    const a = new Vector3(), b = new Vector3(), c = new Vector3(), e1 = new Vector3(), e2 = new Vector3(), e3 = new Vector3(), drawn = geometry.getIndex();
    for (let t = 0; t < triangles; t++) {
      a.fromBufferAttribute(position, drawn ? drawn.getX(t * 3) : t * 3);
      b.fromBufferAttribute(position, drawn ? drawn.getX(t * 3 + 1) : t * 3 + 1);
      c.fromBufferAttribute(position, drawn ? drawn.getX(t * 3 + 2) : t * 3 + 2);
      e1.subVectors(b, a); e2.subVectors(c, a); e3.subVectors(c, b);
      const longest2 = Math.max(e1.lengthSq(), e2.lengthSq(), e3.lengthSq());
      const largest = Math.max(Math.abs(a.x), Math.abs(a.y), Math.abs(a.z), Math.abs(b.x), Math.abs(b.y), Math.abs(b.z),
        Math.abs(c.x), Math.abs(c.y), Math.abs(c.z));
      if (e1.cross(e2).length() <= DEGENERATE_FLOAT32_ULPS * FLOAT32_EPS * largest * Math.sqrt(longest2)) degenerate++;
    }
    if (degenerate) report.warn(`${label}: ${degenerate} degenerate (zero-area) triangles`);
    measurable.push(mesh);
  }
  if (!meshes.length && !strays.length) report.refuse('geometry', 'no mesh primitives in the default scene');
  else if (triangleTotal === 0) report.refuse('geometry', 'the file has no triangles');
  if (json) {
    const skins = Array.isArray(json.skins) && json.skins.length > 0
      || Array.isArray(json.nodes) && json.nodes.some(node => isRecord(node) && 'skin' in node);
    if (skins) {
      // spec.py fails the row for any declared skin; a drawn skinned mesh is also refused above.
      report.fail('geometry', 'skins present (the model must be static)');
      if (!meshes.some(isSkinned)) report.warn('The file declares skins that no drawn mesh uses; the meshes are drawn static.');
    }
    if (Array.isArray(json.animations) && json.animations.length) {
      report.fail('geometry', 'animations present (the model must be static)');
      report.fallback('geometry', `The file has ${json.animations.length} animation(s); arview draws the static pose and never plays them (§2 Geometry).`);
    }
  }
  report.measure('triangles', triangleTotal);
  if (excluded.size) report.measure('excluded_from_measurement', [...excluded].sort());
  if (triangleTotal > ARVIEW_WARN_TRIANGLES) report.warn(`${triangleTotal} triangles (> ${ARVIEW_WARN_TRIANGLES}; heavy for real-time AR)`);
  if (triangleTotal > LIVE_MAX_TRIANGLES) report.wouldFailLive(`${triangleTotal} triangles (live limit ${LIVE_MAX_TRIANGLES})`);

  // Units: detected from the X span after transforms, rescaled before any consumer.
  const drawn = meshes.filter(mesh => mesh.parent !== null && !unreadable.has(mesh));
  let unitsScale: 1 | 0.001 | 0.01 = 1, unitsPlausible = false;
  const measured: RowSink = report;
  if (measurable.length) {
    const before = collectPoints(measurable, rootInverse, () => 'frame');
    const units = unitsRow(before.all, measured);
    unitsScale = units.scale; unitsPlausible = units.plausible;
    if (unitsScale !== 1) {
      const unit = unitsScale === 0.001 ? 'millimetres' : 'centimetres';
      const done = rescale(drawn, unitsScale);
      report.measure('authored_width_units', Math.round(units.width * 1e4) / 1e4);
      measureBounds(before.all, report, unitsScale);   // bbox_mm and width_mm of the normalised (rescaled) model
      report.fallback('units', `UNITS: the model is in ${unit} (X span ${units.width.toFixed(2)} units), not metres. arview rescaled it by `
        + `×${unitsScale} (${done.geometries} geometr${done.geometries === 1 ? 'y' : 'ies'}${done.materials.length
          ? `, KHR_materials_volume thickness/attenuation of ${listNames(done.materials)}` : ''}) before anything else read it; `
        + 'the delivered file still violates §2 Units.');
      report.wouldFailLive(`units are ${unit}, not metres (the live site does not rescale)`);
    }
  } else {
    const why = excluded.size ? `every primitive was excluded after geometry failures: ${listNames([...excluded].sort())}` : 'no readable triangle geometry';
    report.notChecked(['units', 'origin', 'orientation'], why);
  }
  report.measure('units_scale', unitsScale);

  // Lens identification: every material's class, decided once (WP1_DESIGN.md §2).
  let classification: AssetMaterialClassification;
  try {classification = classifyAssetMaterials(root, source);}
  catch (error) {
    report.refuse('components', `lens identification could not run (${describeError(error)}); treat the file as malformed`);
    report.throwIfRefused();
    throw error;
  }
  report.setLensIdentification(classification.lensIdentification);
  for (const warning of classification.warnings) report.warn(`Lens identification: ${warning}`);
  // Malformed or inconsistent LENSES_part_visibility changes what arview draws (the whole asset takes the positional
  // path), so it is a fallback, not only a warning (WP1_DESIGN.md §1).
  if (classification.partVisibilityIgnored.length) {
    report.fallback('part visibility', `${classification.partVisibilityIgnored.join(' ')} Every part is classified and drawn by the positional `
      + 'path instead (temples found by position).');
  }
  const identification = classification.lensIdentification;
  const hasLens = identification.materials.some(entry => entry.class !== 'frame');
  for (const reason of identification.components.reasons) {
    if (!hasLens && reason.startsWith('no lens identified')) {
      report.refuse('components', "no lens identified (no partRole 'lens' or part_role 'lens*' extras, no lens/lenses/glass name token, and no "
        + 'transmissive or alpha-blended material as a last resort); the lens-dependent pipeline cannot run');
    } else report.fail('components', reason);
  }
  // spec.py counts the lens primitives among ALL the default scene's primitives (LINES/POINTS and position-less ones
  // included): with a glTF source so does arview, from the identification's uses (one per JSON primitive). A hand-built
  // scene has no JSON: its drawn triangle meshes with a lens slot are counted.
  report.measure('lens_primitives', identification.source === 'gltf-json'
    ? identification.materials.reduce((total, entry) => total + entry.uses.filter(use => use.class === 'lens').length, 0)
    : meshes.filter(mesh => slotMaterials(mesh).some(material => materialClassOf(material) === 'lens')).length);

  // The measured rows, on the normalised geometry.
  const classOf = (material: Material): 'lens' | 'frame' => materialClassOf(material) === 'lens' ? 'lens' : 'frame';
  if (measurable.length && unitsPlausible) {
    const geometry = collectPoints(measurable, root.matrixWorld.clone().invert(), classOf);
    const rows = [['orientation', (sink: RowSink) => orientationRow(geometry, sink)],
      ['origin', (sink: RowSink) => originRow(geometry, sink, excluded.size)]] as const;
    for (const [row, run] of rows) {
      if (unitsScale === 1) {guard(report, row, () => run(measured)); continue;}
      // A rescaled file fails the row as authored. The row's first line says so AND what the measurement after the
      // rescale found, so it never promises detail that does not follow: the row's own lines are buffered while it
      // runs, then written after that first line in their original order.
      const lines: {kind: 'fail' | 'note'; message: string}[] = [];
      const sink: RowSink = {fail: (target, message) => target === row ? lines.push({kind: 'fail', message}) : report.fail(target, message),
        note: (target, message) => target === row ? lines.push({kind: 'note', message}) : report.note(target, message),
        warn: message => report.warn(message), measure: (key, value) => report.measure(key, value)};
      try {run(sink);}
      catch (error) {
        lines.push({kind: 'fail', message: `check could not run (${error instanceof Error ? `${error.name}: ${error.message}` : describeError(error)}); `
          + 'treat the file as malformed'});
      }
      const defects = lines.filter(line => line.kind === 'fail').length;
      report.fail(row, `not checked as authored: units are wrong; measured after the ×${unitsScale} rescale: ${defects
        ? `${defects} defect${defects === 1 ? '' : 's'} (listed next)` : 'no defect (the rescaled model passes this row)'}`);
      for (const line of lines) report[line.kind](row, line.message);
    }
  } else if (measurable.length) report.notChecked(['origin', 'orientation'], 'units are wrong');

  // Live-site notes that need the classes (spec.py `_live_site`).
  liveSiteNotes(report, measurable, root, classOf);
  // Primitives without readable positions leave the scene now, so no subsystem ever reads them.
  for (const mesh of unreadable) {
    const lens = slotMaterials(mesh).some(material => materialClassOf(material) === 'lens');
    report.fallback('geometry', `${lens ? 'Lens primitive' : 'Primitive'} "${name(mesh)}" has no readable XYZ positions (§2 Format): it is not drawn.`);
    mesh.removeFromParent();
  }
  report.throwIfRefused();
  return {classification, unitsScale, meshes: drawn};
}

function guard(report: ModelReportBuilder, row: SpecRowName, run: () => void): void {
  try {run();}
  catch (error) {report.fail(row, `check could not run (${error instanceof Error ? `${error.name}: ${error.message}` : describeError(error)}); treat the file as malformed`);}
}

/** A material is opaque for the live site when it neither transmits nor blends (spec.py `_is_opaque`: GLTFLoader maps
 *  alphaMode BLEND to `transparent`). */
const isOpaque = (material: Material): boolean => !(material instanceof MeshPhysicalMaterial && material.transmission > 0) && !material.transparent;

function liveSiteNotes(report: ModelReportBuilder, measurable: readonly Mesh[], root: Object3D, classOf: (material: Material) => 'lens' | 'frame'): void {
  const nonOpaque = new Set<string>();
  const opaqueMeshes: Mesh[] = [];
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    for (const material of slotMaterials(object)) if (classOf(material) === 'frame' && !isOpaque(material)) nonOpaque.add(material.name || '(unnamed material)');
  });
  for (const name of nonOpaque) report.wouldFailLive(`frame material '${name}' is not fully opaque`);
  for (const mesh of measurable) if (slotMaterials(mesh).some(material => classOf(material) === 'frame' && isOpaque(material))) opaqueMeshes.push(mesh);
  if (!opaqueMeshes.length) {report.wouldFailLive('no opaque part'); return;}
  const opaque = collectPoints(opaqueMeshes, root.matrixWorld.clone().invert(), material => classOf(material) === 'frame' && isOpaque(material) ? 'frame' : 'lens').frame.vertices;
  for (const [side, sign] of WEARER_SIDES) {
    let reaches = false;
    for (let i = 0; i < opaque.length && !reaches; i += 3) reaches = opaque[i]! * sign > LIVE_TEMPLE_X_M && opaque[i + 2]! < -0.03;
    if (!reaches) report.wouldFailLive(`no opaque temple geometry beyond ${(LIVE_TEMPLE_X_M * 1000).toFixed(0)} mm on the ${side} reaching back past the hinge`);
  }
}
