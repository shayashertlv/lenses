/** Deterministic synthetic GLBs for the model-load tests: a TypeScript port of `glasses3d/tests/glbfab.py`, so arview
 *  and the Python validator (`g3d_host/spec.py`) are tested on the same kind of file. A box-built pair of glasses that
 *  meets the owner's spec (PLAN.md §2), with switches that break one requirement at a time. Parts are boxes in
 *  millimetres converted at build (scale 0.001 = metres). */
import {crc32, deflateSync} from 'node:zlib';

export type Box = readonly [number, number, number, number, number, number];
export interface Part {readonly box: Box; readonly material: string; readonly role: string;}
export type Parts = Readonly<Record<string, Part>>;
export type Mat4 = readonly (readonly number[])[];   // row-major 4x4, as numpy
export type Json = Record<string, unknown>;

const BOX_FACES = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
  [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3]] as const;

export function box(x0: number, x1: number, y0: number, y1: number, z0: number, z1: number): {vertices: number[][]; faces: number[][]} {
  return {vertices: [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]],
    faces: BOX_FACES.map(face => [...face])};
}

const part = (b: Box, material: string, role: string): Part => ({box: b, material, role});
export function defaultParts(): Record<string, Part> {
  return {
    Bridge: part([-9, 9, 0, 4, -2, 2], 'frame', 'frame'),
    Brow_L: part([-70, -9, 4, 10, -2, 2], 'frame', 'frame'),
    Brow_R: part([9, 70, 4, 10, -2, 2], 'frame', 'frame'),
    Lens_L: part([-60, -10, -25, 6, -1, 1], 'lens', 'lens'),
    Lens_R: part([10, 60, -25, 6, -1, 1], 'lens', 'lens'),
    Temple_L: part([-70, -66, 2, 8, -140, -2], 'frame', 'temple'),
    Temple_R: part([66, 70, 2, 8, -140, -2], 'frame', 'temple'),
  };
}
export function shieldParts(): Record<string, Part> {
  return {
    Shield_L: part([-65, -8, -30, 15, -1, 1], 'lens', 'lens'),
    Notch: part([-8, 8, 0, 15, -1, 1], 'lens', 'lens'),
    Shield_R: part([8, 65, -30, 15, -1, 1], 'lens', 'lens'),
    Top_bar: part([-70, 70, 15, 20, -2, 2], 'frame', 'frame'),
    Temple_L: part([-70, -66, 12, 18, -140, -2], 'frame', 'temple'),
    Temple_R: part([66, 70, 12, 18, -140, -2], 'frame', 'temple'),
  };
}
export function without(names: readonly string[], parts: Parts = defaultParts()): Record<string, Part> {
  return Object.fromEntries(Object.entries(parts).filter(([name]) => !names.includes(name)));
}
export function replaceBox(name: string, b: Box, parts: Parts = defaultParts()): Record<string, Part> {
  return {...parts, [name]: {...parts[name]!, box: b}};
}

export const identity = (): number[][] => [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
export function rot(axis: 'x' | 'y' | 'z', deg: number): number[][] {
  const c = Math.cos(deg * Math.PI / 180), s = Math.sin(deg * Math.PI / 180), m = identity();
  const [i, j] = ({x: [1, 2], y: [2, 0], z: [0, 1]} as const)[axis];
  m[i]![i] = c; m[i]![j] = -s; m[j]![i] = s; m[j]![j] = c;
  return m;
}
export function trans(x: number, y: number, z: number): number[][] {const m = identity(); m[0]![3] = x; m[1]![3] = y; m[2]![3] = z; return m;}
export function diag(a: number, b: number, c: number, d = 1): number[][] {return [[a, 0, 0, 0], [0, b, 0, 0], [0, 0, c, 0], [0, 0, 0, d]];}
export function matmul(a: Mat4, b: Mat4): number[][] {
  return a.map(row => [0, 1, 2, 3].map(j => row.reduce((sum, value, k) => sum + value * b[k]![j]!, 0)));
}
const columnMajor = (m: Mat4): number[] => [0, 1, 2, 3].flatMap(column => [0, 1, 2, 3].map(row => m[row]![column]!));

export function pngBytes(width: number, height: number): Uint8Array {
  const raw = new Uint8Array(height * (1 + width * 3));
  for (let y = 0; y < height; y++) {raw[y * (1 + width * 3)] = 0; raw.fill(0x80, y * (1 + width * 3) + 1, (y + 1) * (1 + width * 3));}
  const chunk = (type: string, data: Uint8Array): Uint8Array => {
    const out = new Uint8Array(12 + data.length), view = new DataView(out.buffer);
    view.setUint32(0, data.length); out.set(new TextEncoder().encode(type), 4); out.set(data, 8);
    view.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length)) >>> 0);
    return out;
  };
  const header = new Uint8Array(13), hv = new DataView(header.buffer);
  hv.setUint32(0, width); hv.setUint32(4, height); header.set([8, 2, 0, 0, 0], 8);
  const parts = [new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', header), chunk('IDAT', deflateSync(raw)), chunk('IEND', new Uint8Array())];
  const out = new Uint8Array(parts.reduce((sum, p) => sum + p.length, 0)); let offset = 0;
  for (const p of parts) {out.set(p, offset); offset += p.length;}
  return out;
}

/** A TRIANGLE_STRIP containing every triangle of `faces` (stitched with degenerate triangles). */
export function stripIndices(faces: readonly (readonly number[])[]): number[] {
  const strip = [...faces[0]!];
  for (const t of faces.slice(1)) strip.push(strip.at(-1)!, t[0]!, t[0]!, t[1]!, t[2]!);
  return strip;
}

export interface BuildOptions {
  parts?: Parts;
  scale?: number;
  roles?: boolean;
  materials?: Readonly<Record<string, Json>>;
  materialNames?: {frame: string; lens: string};
  nodeMatrix?: Mat4;
  parentMatrix?: Mat4;
  rotateZUp?: boolean;
  nan?: boolean;
  mode?: number;
  morph?: boolean;
  animation?: boolean;
  texturePx?: readonly [number, number];
  dataUriBuffer?: boolean;
  roleKey?: string;
  roleWhere?: 'mesh' | 'node' | 'material';
  offsetMm?: readonly [number, number, number];
  preMm?: Mat4;
  target?: string;
  noMaterial?: readonly string[];
  indexValue?: number;
  indexCount?: number;
  /** Edits the JSON last; `add` stores an accessor (float VEC3 by default) and returns its index, `view` a raw view. */
  patch?: (doc: Json, add: (values: readonly (readonly number[])[] | readonly number[], component?: number, type?: string) => number,
    view: (data: Uint8Array) => number) => void;
  /** Store the buffer as an external file with this uri: build() then returns its bytes in `external`. */
  externalBuffer?: string;
}

export interface Built {readonly glb: ArrayBuffer; readonly external: Uint8Array | null; readonly doc: Json;}

/** GLB bytes; defaults meet the spec, every switch breaks (or exercises) one thing (glbfab.build). */
export function buildGlb(options: BuildOptions = {}): Built {
  const parts = options.parts ?? defaultParts(), scale = options.scale ?? 0.001, target = options.target ?? 'Bridge';
  const names = options.materialNames ?? {frame: 'Frame acetate', lens: 'Lens clear'};
  const materials = options.materials ?? {
    frame: {name: names.frame, pbrMetallicRoughness: {baseColorFactor: [0.4, 0.2, 0.1, 1.0]}},
    lens: {name: names.lens, pbrMetallicRoughness: {baseColorFactor: [1, 1, 1, 1]}, extensions: {KHR_materials_transmission: {transmissionFactor: 1.0}}},
  };
  const materialKeys = Object.keys(materials);
  const blob: number[] = [];
  const views: Json[] = [], accessors: Json[] = [], meshes: Json[] = [], nodes: Json[] = [];
  const addView = (data: Uint8Array, viewTarget?: number, stride?: number): number => {
    while (blob.length % 4) blob.push(0);
    const view: Json = {buffer: 0, byteOffset: blob.length, byteLength: data.length};
    if (viewTarget) view.target = viewTarget;
    if (stride) view.byteStride = stride;
    for (const byte of data) blob.push(byte);
    views.push(view); return views.length - 1;
  };
  const bytesOf = (array: Float32Array | Uint32Array | Uint16Array | Uint8Array): Uint8Array => new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
  const addAccessor = (values: readonly (readonly number[])[] | readonly number[], component = 5126, type = 'VEC3'): number => {
    const flat = (values as readonly unknown[]).flatMap(value => Array.isArray(value) ? value as number[] : [value as number]);
    const typed = component === 5126 ? Float32Array.from(flat) : component === 5125 ? Uint32Array.from(flat) : component === 5123 ? Uint16Array.from(flat) : Uint8Array.from(flat);
    const width = {SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16}[type] ?? 1;
    const accessor: Json = {bufferView: addView(bytesOf(typed)), componentType: component, count: flat.length / width, type};
    if (component === 5126 && type === 'VEC3' && flat.length) {
      const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < flat.length; i += 3) for (let a = 0; a < 3; a++) {min[a] = Math.min(min[a]!, flat[i + a]!); max[a] = Math.max(max[a]!, flat[i + a]!);}
      accessor.min = min; accessor.max = max;
    }
    accessors.push(accessor); return accessors.length - 1;
  };
  for (const [name, {box: b, material, role}] of Object.entries(parts)) {
    const {vertices, faces} = box(...b);
    let v = vertices;
    if (options.preMm) v = v.map(([x, y, z]) => [0, 1, 2].map(r => options.preMm![r]![0]! * x! + options.preMm![r]![1]! * y! + options.preMm![r]![2]! * z! + options.preMm![r]![3]!));
    const offset = options.offsetMm ?? [0, 0, 0];
    v = v.map(point => point.map((value, axis) => (value + offset[axis]!) * scale));
    if (options.rotateZUp) v = v.map(([x, y, z]) => [x!, -z!, y!]);
    const hit = name === target;
    if (options.nan && hit) v[0]![0] = NaN;
    const positions = Float32Array.from(v.flat());
    const finite = (values: number[]) => values.map(value => Number.isFinite(value) ? value : 0);
    const min = finite([0, 1, 2].map(a => Math.min(...v.map(point => point[a]!).filter(Number.isFinite))));
    const max = finite([0, 1, 2].map(a => Math.max(...v.map(point => point[a]!).filter(Number.isFinite))));
    accessors.push({bufferView: addView(bytesOf(positions), 34962), componentType: 5126, count: 8, type: 'VEC3', min, max});
    const positionAccessor = accessors.length - 1;
    let indices = options.mode === 5 && hit ? stripIndices(faces) : faces.flat();
    if (options.indexValue !== undefined && hit) {indices = [...indices]; indices[0] = options.indexValue;}
    accessors.push({bufferView: addView(bytesOf(Uint32Array.from(indices)), 34963), componentType: 5125,
      count: options.indexCount !== undefined && hit ? options.indexCount : indices.length, type: 'SCALAR'});
    const primitive: Json = {attributes: {POSITION: positionAccessor}, indices: accessors.length - 1};
    if (!(options.noMaterial ?? []).includes(name)) primitive.material = materialKeys.indexOf(material);
    if (options.mode !== undefined && hit) primitive.mode = options.mode;
    if (options.morph && hit) primitive.targets = [{POSITION: positionAccessor}];
    const mesh: Json = {name, primitives: [primitive]}, node: Json = {name, mesh: meshes.length};
    if (options.roles ?? true) {
      const where = options.roleWhere ?? 'mesh';
      if (where === 'material') throw new Error('roleWhere material: put the role in `materials` instead');
      (where === 'mesh' ? mesh : node).extras = {[options.roleKey ?? 'partRole']: role};
    }
    if (options.nodeMatrix && hit) node.matrix = columnMajor(options.nodeMatrix);
    meshes.push(mesh); nodes.push(node);
  }
  const doc: Json = {asset: {version: '2.0', generator: 'arview tests (glb-fab.ts)'}, scene: 0,
    materials: materialKeys.map(key => structuredClone(materials[key]!)), meshes, accessors, bufferViews: views};
  const rootChildren = nodes.map((_, index) => index);
  if (options.parentMatrix) {
    nodes.push({name: 'Root', children: rootChildren, matrix: columnMajor(options.parentMatrix)});
    doc.scenes = [{nodes: [nodes.length - 1]}];
  } else doc.scenes = [{nodes: rootChildren}];
  doc.nodes = nodes;
  if (options.texturePx) {
    const view = addView(pngBytes(...options.texturePx));
    doc.images = [{bufferView: view, mimeType: 'image/png'}];
    doc.textures = [{source: 0}];
    ((doc.materials as Json[])[0]!.pbrMetallicRoughness as Json).baseColorTexture = {index: 0};
  }
  if (options.animation) doc.animations = [{channels: [], samplers: []}];
  if ((doc.materials as Json[]).some(material => 'extensions' in material)) {
    const used = new Set<string>();
    for (const material of doc.materials as Json[]) for (const name of Object.keys((material.extensions ?? {}) as Json)) used.add(name);
    doc.extensionsUsed = [...used];
  }
  options.patch?.(doc, addAccessor, data => addView(data));
  while (blob.length % 4) blob.push(0);
  const bin = Uint8Array.from(blob);
  let external: Uint8Array | null = null, chunk: Uint8Array | null = bin;
  if (options.dataUriBuffer) {
    doc.buffers = [{byteLength: bin.length, uri: `data:application/octet-stream;base64,${Buffer.from(bin).toString('base64')}`}];
    chunk = null;
  } else if (options.externalBuffer) {
    doc.buffers = [{byteLength: bin.length, uri: options.externalBuffer}];
    external = bin; chunk = null;
  } else doc.buffers = [{byteLength: bin.length}];
  return {glb: joinGlb(doc, chunk), external, doc};
}

/** Assemble a GLB from a JSON document and an optional BIN chunk. */
export function joinGlb(doc: Json, bin: Uint8Array | null): ArrayBuffer {
  let text = JSON.stringify(doc); while (text.length % 4) text += ' ';
  const json = new TextEncoder().encode(text);
  const total = 12 + 8 + json.length + (bin ? 8 + bin.length : 0);
  const out = new Uint8Array(total), view = new DataView(out.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, total, true);
  view.setUint32(12, json.length, true); view.setUint32(16, 0x4e4f534a, true); out.set(json, 20);
  if (bin) {view.setUint32(20 + json.length, bin.length, true); view.setUint32(24 + json.length, 0x004e4942, true); out.set(bin, 28 + json.length);}
  return out.buffer;
}

/** Split a GLB into its JSON document and BIN chunk (tests edit the JSON and re-join). */
export function splitGlb(bytes: ArrayBuffer): {doc: Json; bin: Uint8Array} {
  const view = new DataView(bytes), length = view.getUint32(12, true);
  const doc = JSON.parse(new TextDecoder().decode(new Uint8Array(bytes, 20, length))) as Json;
  return {doc, bin: new Uint8Array(bytes.slice(28 + length))};
}

/** The ar_v4 crystal Tom Ford r9 as far as acceptance is concerned: legacy node `part_role` only (frame, temple_left,
 *  temple_right, lens_left, lens_right, hardware), a crystal acetate (transmission 1) front and temples with gold cores,
 *  and the bridge underside 10.23 mm above the origin. */
export function r9Like(): Built {
  const parts: Record<string, Part> = {
    'Crystal front': {box: [-70, 70, 10.23, 30, -3, 3], material: 'crystal', role: 'frame'},
    'Lens left': {box: [-62, -8, -12, 26, -1, 1], material: 'lens', role: 'lens_left'},
    'Lens right': {box: [8, 62, -12, 26, -1, 1], material: 'lens', role: 'lens_right'},
    'Temple left': {box: [66, 70, 18, 24, -147.24, -4], material: 'crystal', role: 'temple_left'},
    'Temple right': {box: [-70, -66, 18, 24, -147.24, -4], material: 'crystal', role: 'temple_right'},
    'T temple core L': {box: [67, 69, 20, 22, -145, -13], material: 'gold', role: 'hardware'},
    'T temple core R': {box: [-69, -67, 20, 22, -145, -13], material: 'gold', role: 'hardware'},
  };
  return buildGlb({parts, roleKey: 'part_role', roleWhere: 'node', materials: {
    crystal: {name: 'Crystal acetate', pbrMetallicRoughness: {baseColorFactor: [1, 1, 1, 1], roughnessFactor: .05},
      extensions: {KHR_materials_transmission: {transmissionFactor: 1}, KHR_materials_volume: {thicknessFactor: .004}}},
    lens: {name: 'Warm brown optical tint', pbrMetallicRoughness: {baseColorFactor: [.6, .45, .3, 1]},
      extensions: {KHR_materials_transmission: {transmissionFactor: .7}}},
    gold: {name: 'Champagne gold', pbrMetallicRoughness: {baseColorFactor: [.9, .8, .6, 1], metallicFactor: 1}},
  }});
}
