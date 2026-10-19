/** Synthetic, generated-at-test-time evidence. No product assets are embedded in source. */
import crypto from 'node:crypto';
import {deflateSync} from 'node:zlib';

export const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function makePng(width = 32, height = 24, alphaAt = () => 255) {
  const chunk = (name, data) => {
    const type = Buffer.from(name), size = Buffer.alloc(4), crc = Buffer.alloc(4);
    size.writeUInt32BE(data.length); crc.writeUInt32BE(crc32(Buffer.concat([type, data])));
    return Buffer.concat([size, type, data, crc]);
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(width); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 6;
  const pixels = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = y * (width * 4 + 1) + 1 + x * 4;
    pixels[i] = 100 + x % 80; pixels[i + 1] = 120 + y % 80; pixels[i + 2] = 170; pixels[i + 3] = alphaAt(x, y);
  }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]);
}

/** Quads are front-facing, metres, identity transforms; triangle IDs are primitive-local. */
export function makeAnchorGlb({alphaMask = false} = {}) {
  const doc = {asset: {version: '2.0', generator: 'M12 browser synthetic fixture'}, scene: 0,
    scenes: [{nodes: [0, 1, 2, 3]}],
    nodes: [{name: 'Frame.Panel[0]', mesh: 0}, {name: 'Lens Left.001', mesh: 1},
      {name: 'Frame behind/lens', mesh: 2}, {name: 'Frame.Panel[0]', mesh: 3}],
    meshes: [], materials: [
      {pbrMetallicRoughness: {baseColorFactor: [0.23, 0.14, 0.1, 1], roughnessFactor: 0.5}},
      {pbrMetallicRoughness: {baseColorFactor: [0.75, 0.9, 1, 1], roughnessFactor: 0.1}, extensions: {KHR_materials_transmission: {transmissionFactor: 0.8}}},
    ], extensionsUsed: ['KHR_materials_transmission'], buffers: [], bufferViews: [], accessors: []};
  const pieces = [], quads = [];
  let length = 0;
  const append = (bytes, componentType, count, type, bounds = {}) => {
    const view = doc.bufferViews.length;
    doc.bufferViews.push({buffer: 0, byteOffset: length, byteLength: bytes.length}); pieces.push(bytes); length += bytes.length;
    const pad = (4 - length % 4) % 4; if (pad) {pieces.push(Buffer.alloc(pad)); length += pad;}
    const accessor = doc.accessors.length;
    doc.accessors.push({bufferView: view, componentType, count, type, ...bounds}); return accessor;
  };
  const primitive = (mesh, index, x0, x1, y0, y1, z, material = 0) => {
    const positions = [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]];
    const xyz = new Float32Array(positions.flat()), normals = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]);
    const indices = new Uint16Array([0, 1, 2, 0, 2, 3]);
    const POSITION = append(Buffer.from(xyz.buffer), 5126, 4, 'VEC3', {min: [x0, y0, z], max: [x1, y1, z]});
    const NORMAL = append(Buffer.from(normals.buffer), 5126, 4, 'VEC3');
    const elements = append(Buffer.from(indices.buffer), 5123, 6, 'SCALAR');
    quads.push({node_index: mesh, mesh_index: mesh, primitive_index: index, positions: Array.from(xyz), indices: Array.from(indices), material, x0, x1, y0, y1, z});
    const attributes = {POSITION, NORMAL};
    if (alphaMask && mesh === 3) {
      const uv = new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]);
      attributes.TEXCOORD_0 = append(Buffer.from(uv.buffer), 5126, 4, 'VEC2');
    }
    return {attributes, indices: elements, material, mode: 4};
  };
  doc.meshes.push({primitives: [primitive(0, 0, -0.075, -0.005, -0.035, 0.035, 0), primitive(0, 1, 0.005, 0.025, -0.035, -0.02, 0)]});
  doc.meshes.push({primitives: [primitive(1, 0, 0.005, 0.075, -0.005, 0.035, 0.01, 1)]});
  doc.meshes.push({primitives: [primitive(2, 0, 0.005, 0.075, -0.005, 0.035, 0)]});
  doc.meshes.push({primitives: [primitive(3, 0, 0.04, 0.06, -0.035, -0.02, 0, alphaMask ? 2 : 0)]});
  if (alphaMask) {
    const image = makePng(2, 2, x => x === 0 ? 0 : 255), imageView = doc.bufferViews.length;
    doc.bufferViews.push({buffer: 0, byteOffset: length, byteLength: image.length}); pieces.push(image); length += image.length;
    const padding = (4 - length % 4) % 4; if (padding) {pieces.push(Buffer.alloc(padding)); length += padding;}
    doc.images = [{bufferView: imageView, mimeType: 'image/png'}];
    doc.samplers = [{magFilter: 9728, minFilter: 9728, wrapS: 33071, wrapT: 33071}];
    doc.textures = [{source: 0, sampler: 0}];
    doc.materials.push({alphaMode: 'MASK', alphaCutoff: 0.5, pbrMetallicRoughness: {baseColorTexture: {index: 0}, roughnessFactor: 1}});
  }
  doc.buffers.push({byteLength: length});
  const jsonRaw = Buffer.from(JSON.stringify(doc)), json = Buffer.concat([jsonRaw, Buffer.alloc((4 - jsonRaw.length % 4) % 4, 32)]), bin = Buffer.concat(pieces);
  const header = Buffer.alloc(12); header.writeUInt32LE(0x46546c67); header.writeUInt32LE(2, 4); header.writeUInt32LE(28 + json.length + bin.length, 8);
  const jsonHeader = Buffer.alloc(8); jsonHeader.writeUInt32LE(json.length); jsonHeader.writeUInt32LE(0x4e4f534a, 4);
  const binHeader = Buffer.alloc(8); binHeader.writeUInt32LE(bin.length); binHeader.writeUInt32LE(0x004e4942, 4);
  return {bytes: Buffer.concat([header, jsonHeader, json, binHeader, bin]), doc, quads};
}
