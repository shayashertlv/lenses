/**
 * Reading eyewear geometry out of a `.glb`, headless.
 *
 * This file exists on the `fit/` side of the isolation boundary, so it may not
 * import three.js and may not touch the DOM — `scripts/check-isolation.mjs`
 * enforces both by actually loading it in Node. That is not a formality: the
 * whole reason this tree can report a millimetre is that the arithmetic runs
 * without a browser, and a frame asset is an *input to the arithmetic* before
 * it is anything to draw. The renderer loads the same file again through
 * three.js's `GLTFLoader` for materials and textures; this reader deliberately
 * does not try to serve both, because a loader that has to satisfy a renderer
 * grows texture handling, and texture handling is how a headless module ends
 * up needing a canvas.
 *
 * ## What it reads, and what it refuses
 *
 * Positions, indices, per-primitive material and node names, and the node
 * transforms that place them. Nothing else — no textures, no animation, no
 * skinning, no cameras.
 *
 * Measured across the ten `.glb` assets in `assets/glasses/`: **no Draco, no
 * meshopt, no quantisation, no sparse accessors and no external buffer URIs**.
 * Every one is plain float32 `POSITION` with uint16 or uint32 indices. So the
 * decode below is deliberately narrow, and anything outside it THROWS rather
 * than guessing — a mesh silently read at the wrong stride is a frame that
 * seats confidently in the wrong place, which is the failure this tree is
 * least able to notice.
 *
 * ## Units and axes
 *
 * glTF declares metres and +Y up. Measured on all eleven: every asset arrives
 * **+Y up, lenses at +Z, temples at −Z**, which is already this tree's frame
 * space — so there is no axis conversion here and there must not be one. Two
 * Tripo-generated files arrive rotated and at arbitrary scale; that is the
 * catalogue's business (`orient`, `realWidthMm`), not the reader's. The reader
 * returns exactly what the file says, in **millimetres**, and lets the caller
 * decide what was meant.
 */

/** One drawable piece of an asset, with the names that identify it. */
export interface MeshPart {
  /** The node's name, or '' when the file names nothing. */
  readonly name: string;
  /** The material's name, or '' — this is what carries lens identity on most
   *  of the catalogue (`Lens_Prescription_Glass`, `nose_pads`, …). */
  readonly materialName: string;
  /** World-space vertex positions, millimetres, 3 per vertex. */
  readonly positions: Float64Array;
  /** Triangle indices into `positions`. */
  readonly indices: Uint32Array;
}

export interface MeshAsset {
  readonly parts: readonly MeshPart[];
  /** Every part's geometry concatenated, for whole-asset queries. */
  readonly positions: Float64Array;
  readonly indices: Uint32Array;
  /** Extensions the file declares it uses, for the renderer's benefit. */
  readonly extensions: readonly string[];
  readonly triangleCount: number;
}

const GLB_MAGIC = 0x46546c67; // 'glTF'
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;

const COMPONENT = new Map<number, { array: 'u16' | 'u32' | 'f32'; size: number }>([
  [5123, { array: 'u16', size: 2 }],
  [5125, { array: 'u32', size: 4 }],
  [5126, { array: 'f32', size: 4 }],
]);

const COMPONENTS_PER = new Map<string, number>([['SCALAR', 1], ['VEC2', 2], ['VEC3', 3], ['VEC4', 4]]);

/** glTF's own matrix order is column-major; this returns row-major 4x4. */
function fromTRS(node: any): Float64Array {
  const m = new Float64Array(16);
  if (Array.isArray(node.matrix)) {
    // Column-major in the file: element (row r, col c) is matrix[c * 4 + r].
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) m[r * 4 + c] = node.matrix[c * 4 + r];
    return m;
  }
  const t = node.translation ?? [0, 0, 0];
  const q = node.rotation ?? [0, 0, 0, 1];
  const s = node.scale ?? [1, 1, 1];
  const [x, y, z, w] = q;
  const x2 = x + x, y2 = y + y, z2 = z + z;
  const xx = x * x2, xy = x * y2, xz = x * z2;
  const yy = y * y2, yz = y * z2, zz = z * z2;
  const wx = w * x2, wy = w * y2, wz = w * z2;
  m[0] = (1 - (yy + zz)) * s[0]; m[1] = (xy - wz) * s[1]; m[2] = (xz + wy) * s[2]; m[3] = t[0];
  m[4] = (xy + wz) * s[0]; m[5] = (1 - (xx + zz)) * s[1]; m[6] = (yz - wx) * s[2]; m[7] = t[1];
  m[8] = (xz - wy) * s[0]; m[9] = (yz + wx) * s[1]; m[10] = (1 - (xx + yy)) * s[2]; m[11] = t[2];
  m[15] = 1;
  return m;
}

function mul4(a: Float64Array, b: Float64Array): Float64Array {
  const o = new Float64Array(16);
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 4; c++) {
      let v = 0;
      for (let k = 0; k < 4; k++) v += a[r * 4 + k] * b[k * 4 + c];
      o[r * 4 + c] = v;
    }
  }
  return o;
}

const IDENTITY = (): Float64Array => {
  const m = new Float64Array(16);
  m[0] = m[5] = m[10] = m[15] = 1;
  return m;
};

/**
 * Parses a `.glb` into world-space geometry, in millimetres.
 *
 * `scaleToMm` is applied after the node transforms. glTF declares metres, so
 * the default 1000 is the honest reading of a conforming file; a catalogue
 * entry that knows its asset lies about its units passes its own factor.
 */
export function readGlb(bytes: Uint8Array, scaleToMm = 1000): MeshAsset {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.byteLength < 20 || view.getUint32(0, true) !== GLB_MAGIC) {
    throw new Error('not a GLB: the magic is wrong');
  }
  const version = view.getUint32(4, true);
  if (version !== 2) throw new Error(`GLB version ${version} is not supported; only 2`);

  let json: any = null;
  let bin: Uint8Array | null = null;
  let at = 12;
  while (at + 8 <= view.byteLength) {
    const len = view.getUint32(at, true);
    const kind = view.getUint32(at + 4, true);
    const body = bytes.subarray(at + 8, at + 8 + len);
    if (kind === CHUNK_JSON) json = JSON.parse(new TextDecoder().decode(body));
    else if (kind === CHUNK_BIN) bin = body;
    at += 8 + len + ((4 - (len % 4)) % 4);
  }
  if (!json) throw new Error('GLB has no JSON chunk');

  for (const ext of json.extensionsRequired ?? []) {
    // Refuse rather than mis-read. A required extension we ignore is geometry
    // that decodes to nonsense, and nothing downstream could tell.
    throw new Error(`GLB requires extension "${ext}", which this reader does not implement`);
  }
  for (const buffer of json.buffers ?? []) {
    if (buffer.uri) throw new Error('GLB references an external buffer; only self-contained files');
  }

  const accessor = (index: number): Float64Array => {
    const a = json.accessors[index];
    if (a.sparse) throw new Error('sparse accessors are not supported');
    const comp = COMPONENT.get(a.componentType);
    const per = COMPONENTS_PER.get(a.type);
    if (!comp || per === undefined) {
      throw new Error(`unsupported accessor: componentType ${a.componentType}, type ${a.type}`);
    }
    const bv = json.bufferViews[a.bufferView];
    if (!bin) throw new Error('accessor needs a BIN chunk and there is none');
    const base = (bv.byteOffset ?? 0) + (a.byteOffset ?? 0);
    const stride = bv.byteStride ?? comp.size * per;
    const dv = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
    const out = new Float64Array(a.count * per);
    for (let i = 0; i < a.count; i++) {
      const at2 = base + i * stride;
      for (let c = 0; c < per; c++) {
        const o = at2 + c * comp.size;
        out[i * per + c] = comp.array === 'f32' ? dv.getFloat32(o, true)
          : comp.array === 'u16' ? dv.getUint16(o, true)
            : dv.getUint32(o, true);
      }
    }
    return out;
  };

  const parts: MeshPart[] = [];
  const nodes = json.nodes ?? [];
  const scene = json.scenes?.[json.scene ?? 0];
  const roots: number[] = scene?.nodes ?? nodes.map((_: unknown, i: number) => i);

  const visit = (index: number, parent: Float64Array): void => {
    const node = nodes[index];
    if (!node) return;
    const world = mul4(parent, fromTRS(node));
    if (node.mesh !== undefined) {
      const mesh = json.meshes[node.mesh];
      for (const prim of mesh.primitives ?? []) {
        if (prim.mode !== undefined && prim.mode !== 4) continue; // triangles only
        if (prim.attributes?.POSITION === undefined) continue;
        const raw = accessor(prim.attributes.POSITION);
        const n = raw.length / 3;
        const positions = new Float64Array(n * 3);
        for (let i = 0; i < n; i++) {
          const x = raw[i * 3], y = raw[i * 3 + 1], z = raw[i * 3 + 2];
          positions[i * 3] = (world[0] * x + world[1] * y + world[2] * z + world[3]) * scaleToMm;
          positions[i * 3 + 1] = (world[4] * x + world[5] * y + world[6] * z + world[7]) * scaleToMm;
          positions[i * 3 + 2] = (world[8] * x + world[9] * y + world[10] * z + world[11]) * scaleToMm;
        }
        let indices: Uint32Array;
        if (prim.indices !== undefined) {
          const idx = accessor(prim.indices);
          indices = new Uint32Array(idx.length);
          for (let i = 0; i < idx.length; i++) indices[i] = idx[i];
        } else {
          indices = new Uint32Array(n);
          for (let i = 0; i < n; i++) indices[i] = i;
        }
        parts.push({
          name: node.name ?? mesh.name ?? '',
          materialName: prim.material !== undefined
            ? (json.materials?.[prim.material]?.name ?? '') : '',
          positions,
          indices,
        });
      }
    }
    for (const child of node.children ?? []) visit(child, world);
  };
  for (const r of roots) visit(r, IDENTITY());

  let vertexTotal = 0;
  let indexTotal = 0;
  for (const p of parts) { vertexTotal += p.positions.length / 3; indexTotal += p.indices.length; }
  const positions = new Float64Array(vertexTotal * 3);
  const indices = new Uint32Array(indexTotal);
  let vOff = 0;
  let iOff = 0;
  for (const p of parts) {
    positions.set(p.positions, vOff * 3);
    for (let i = 0; i < p.indices.length; i++) indices[iOff + i] = p.indices[i] + vOff;
    vOff += p.positions.length / 3;
    iOff += p.indices.length;
  }

  return {
    parts,
    positions,
    indices,
    extensions: json.extensionsUsed ?? [],
    triangleCount: indexTotal / 3,
  };
}
