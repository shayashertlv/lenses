/** The GLB load front-end and the acceptance policy (glasses3d/plan/archive/20261019/WP1_DESIGN.md §1): every §1-table refusal, every row that renders
 *  with a failure or a warning, the millimetre and centimetre rescale, the r9-style legacy crystal, and agreement with
 *  the Python validator (`glasses3d/g3d_host/spec.py`, the single source of truth for the §2 rows) on the same bytes.
 *  The GLBs are built in the test (glb-fab.ts, a port of glasses3d/tests/glbfab.py). */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Line, Mesh, MeshPhysicalMaterial, Texture} from 'three';
import type {Material} from 'three';
import type {GLTFLoaderPlugin} from 'three/addons/loaders/GLTFLoader.js';
import {DRACOLoader} from 'three/addons/loaders/DRACOLoader.js';
import {checkModel} from '../src/model/check.ts';
import {createGltfLoader, loadModel} from '../src/model/load.ts';
import {ModelReportBuilder, reportLines, SPEC_ROWS} from '../src/model/report.ts';
import type {ModelReport, SpecRowName} from '../src/model/report.ts';
import {describeError, imageSize, loadedReferences, readGlbContainer, GlbFormatError} from '../src/model/glb.ts';
import {materialClassOf} from '../src/eyewear/part-class.ts';
import {isTranslucentFrameMaterial} from '../src/eyewear/optical-material.ts';
import {box, buildGlb, defaultParts, diag, joinGlb, matmul, pngBytes, r9Like, replaceBox, rot, shieldParts, splitGlb, stripIndices, trans, without} from './glb-fab.ts';
import type {BuildOptions, Built, Json, Part} from './glb-fab.ts';

// Node lacks `self`, which GLTFLoader reads (self.URL) before decoding an image: a test-only stand-in; the browser has it.
// There is deliberately NO ProgressEvent stand-in: three's FileLoader calls `new ProgressEvent`, which Node lacks, and
// load.ts keeps FileLoader off the buffer path (prefetchedBuffers), so the external and data: buffer cases below prove
// that a renderer-less Node load (arview serve's check) never reaches it.
(globalThis as {self?: unknown}).self ??= globalThis;

const URL_BASE = 'http://127.0.0.1:8250/models/';
const stubTextures = (): GLTFLoaderPlugin => ({name: 'ArviewTestTextures', loadTexture: async () => new Texture()});

/** The front-end and the normalisation pass on `built` (textures stubbed: Node cannot decode images). */
async function check(built: Built | ArrayBuffer, options: {file?: string; plugins?: boolean; handoverSha256?: string; resources?: Record<string, Uint8Array>} = {}): Promise<ModelReport> {
  const glb = built instanceof ArrayBuffer ? built : built.glb;
  const resources: Record<string, Uint8Array> = {...options.resources};
  if (!(built instanceof ArrayBuffer) && built.external) resources['external.bin'] = built.external;
  const {report} = await checkModel(glb, {url: URL_BASE + (options.file ?? 'model.glb'), renderer: null,
    plugins: options.plugins === false ? [] : [stubTextures], handoverSha256: options.handoverSha256 ?? null,
    fetchResource: async url => {
      const name = url.slice(URL_BASE.length);
      const data = resources[name];
      return data ? data.slice().buffer : null;
    }});
  return report;
}
const failed = (report: ModelReport): Set<SpecRowName> => new Set(SPEC_ROWS.filter(row => !report.spec[row].pass));
const errors = (report: ModelReport): string[] => SPEC_ROWS.flatMap(row => report.spec[row].pass ? [] : report.spec[row].detail.map(detail => `${row}: ${detail}`));
const sorted = (rows: Iterable<string>): string[] => [...rows].sort();

test('absolute resource URIs are missing before any fetch, even when a CORS host would return valid bytes', async () => {
  for (const uri of ['https://cdn.example/models/canonical-face.json', 'http://cdn.example/texture.png', '//cdn.example/a.bin', 'C:/asset.png']) {
    for (const kind of ['image', 'buffer'] as const) {
      const {doc, bin} = splitGlb(buildGlb().glb);
      if (kind === 'image') doc.images = [{uri}];
      else (doc.buffers as Json[]).push({uri, byteLength: 4});
      let requests = 0;
      const {report} = await checkModel(joinGlb(doc, bin), {url: URL_BASE + 'model.glb', renderer: null, plugins: [stubTextures],
        fetchResource: async () => {requests++; return pngBytes(4, 4).slice().buffer;}});
      assert.equal(requests, 0, `${kind}: ${uri}`);
      assert.equal(report.status, 'refused');
      assert.equal(report.spec.format.pass, false);
      assert.match(report.refusal.join('\n'), /absolute URI is never fetched/);
    }
  }
});

/* ---------------------------------------------------------------------------------------------- JSON patches */
type Patch = NonNullable<BuildOptions['patch']>;
const pInstancing: Patch = (doc, add) => {
  const translation = add([[0, 0, 0], [0, 0.030, 0.040]]);
  for (const node of doc.nodes as Json[]) if (String(node.name).startsWith('Lens')) node.extensions = {EXT_mesh_gpu_instancing: {attributes: {TRANSLATION: translation}}};
  doc.extensionsUsed = [...(doc.extensionsUsed as string[] ?? []), 'EXT_mesh_gpu_instancing'];
  doc.extensionsRequired = ['EXT_mesh_gpu_instancing'];
};
const pSkins: Patch = doc => {doc.skins = [{joints: [0]}];};
const pMissingImage: Patch = doc => {
  doc.images = [{uri: 'missing_texture.png'}]; doc.textures = [{source: 0}];
  ((doc.materials as Json[])[0]!.pbrMetallicRoughness as Json).baseColorTexture = {index: 0};
};
const pNoScenes: Patch = doc => {delete doc.scenes; delete doc.scene;};
const pEmptyScene: Patch = doc => {doc.scenes = [{nodes: []}];};
const pZeroTriangles: Patch = doc => {for (const mesh of doc.meshes as Json[]) ((doc.accessors as Json[])[((mesh.primitives as Json[])[0]!.indices as number)]!).count = 0;};
const pTexCoordMinusOne: Patch = doc => {
  doc.images = [{bufferView: 0, mimeType: 'image/png'}]; doc.textures = [{source: 0}];
  ((doc.materials as Json[])[0]!.pbrMetallicRoughness as Json).baseColorTexture = {index: 0, texCoord: -1};
};
const pBadMaterial: Patch = doc => {((doc.meshes as Json[])[0]!.primitives as Json[])[0]!.material = 7;};
const pZeroVertices: Patch = doc => {
  for (const accessor of doc.accessors as Json[]) if (accessor.type === 'VEC3') accessor.count = 0;
  for (const mesh of doc.meshes as Json[]) delete (mesh.primitives as Json[])[0]!.indices;
};
const pDeep: Patch = doc => {
  const nodes = doc.nodes as Json[], first = nodes.length, roots = (doc.scenes as Json[])[0]!.nodes;
  const chain: Json[] = Array.from({length: 1100}, (_, i) => ({name: `empty${i}`, children: [first + i + 1]}));
  chain.at(-1)!.children = roots; nodes.push(...chain); (doc.scenes as Json[])[0]!.nodes = [first];
};
/** An image nothing uses, with neither uri nor bufferView (three never loads it). */
const pOrphanImage: Patch = doc => {doc.images = [{name: 'orphan'}];};
/** A bufferView nothing uses, pointing at a buffer that does not exist (three never loads it). */
const pOrphanView: Patch = doc => {(doc.bufferViews as Json[]).push({buffer: 5, byteLength: 4});};
/** An external image nothing uses, missing next to the file. */
const pUnusedMissingImage: Patch = doc => {doc.images = [{uri: 'missing_unused.png'}];};
/** A valid default scene plus a second scene whose node names mesh 99 (three's parse() would load it and throw). */
const pSecondSceneBrokenMesh: Patch = doc => {
  const nodes = doc.nodes as Json[]; nodes.push({name: 'Elsewhere', mesh: 99});
  (doc.scenes as Json[]).push({nodes: [nodes.length - 1]});
};
/** An animation whose channel targets node 99 (three's parse() would load it and throw). */
const pAnimationNode99: Patch = (doc, add) => {
  const input = add([0, 1], 5126, 'SCALAR'), output = add([[0, 0, 0], [0, 0.01, 0]]);
  doc.animations = [{channels: [{sampler: 0, target: {node: 99, path: 'translation'}}], samplers: [{input, output}]}];
};
const nodeNamed = (doc: Json, name: string): Json => (doc.nodes as Json[]).find(node => node.name === name)!;
/** A default-scene node naming camera 99 (no cameras exist). */
const pCamera99: Patch = doc => {nodeNamed(doc, 'Bridge').camera = 99;};
/** A default-scene node naming punctual light 99 (one light exists). */
const pLight99: Patch = doc => {
  doc.extensionsUsed = [...(doc.extensionsUsed as string[] ?? []), 'KHR_lights_punctual'];
  doc.extensions = {KHR_lights_punctual: {lights: [{type: 'point', intensity: 1000}]}};
  nodeNamed(doc, 'Bridge').extensions = {KHR_lights_punctual: {light: 99}};
};
/** A default-scene node naming skin 99 (no skins exist). */
const pSkin99: Patch = doc => {nodeNamed(doc, 'Brow_L').skin = 99;};
/** A second primitive on Brow_L with no POSITION (valid glTF: clients skip it). */
const pNoPosition: Patch = (doc, add) => {
  const mesh = (doc.meshes as Json[]).find(entry => entry.name === 'Brow_L')!;
  (mesh.primitives as Json[]).push({attributes: {NORMAL: add(Array.from({length: 8}, () => [0, 1, 0]))}, material: 0});
};
/** A minimal KTX2 header (identifier, then pixelWidth/pixelHeight at bytes 20/24): enough for the size reader. */
function ktx2Bytes(width: number, height: number): Uint8Array {
  const out = new Uint8Array(80), view = new DataView(out.buffer);
  out.set([0xab, 0x4b, 0x54, 0x58, 0x20, 0x32, 0x30, 0xbb, 0x0d, 0x0a, 0x1a, 0x0a]);
  view.setUint32(20, width, true); view.setUint32(24, height, true);
  return out;
}
/** The frame's base colour as a KTX2 texture whose KHR_texture_basisu is REQUIRED (no fallback image). */
const pKtx2Required: Patch = (doc, _add, view) => {
  doc.images = [{bufferView: view(ktx2Bytes(64, 64)), mimeType: 'image/ktx2'}];
  doc.textures = [{extensions: {KHR_texture_basisu: {source: 0}}}];
  ((doc.materials as Json[])[0]!.pbrMetallicRoughness as Json).baseColorTexture = {index: 0};
  doc.extensionsUsed = [...(doc.extensionsUsed as string[] ?? []), 'KHR_texture_basisu'];
  doc.extensionsRequired = ['KHR_texture_basisu'];
};
const withPart = (name: string, box: Part['box'], material = 'frame', role = 'frame'): Record<string, Part> => ({...defaultParts(), [name]: {box, material, role}});
const lensesShifted = (dx: number): Record<string, Part> => {
  const parts = defaultParts();
  for (const name of ['Lens_L', 'Lens_R']) {const [x0, x1, ...rest] = parts[name]!.box; parts[name] = {...parts[name]!, box: [x0 + dx, x1 + dx, ...rest] as Part['box']};}
  return parts;
};
const leftLensOnly = (): Record<string, Part> => ({...defaultParts(), Lens_R: {...defaultParts().Lens_R!, material: 'frame', role: 'frame'}});
const unnamedParts = (): Record<string, Part> => Object.fromEntries(Object.entries(defaultParts()).map(([name, value]) => [name.replace('Lens', 'Pane'), value]));
const allLensParts = (): Record<string, Part> => Object.fromEntries(Object.entries(defaultParts()).map(([name, value]) => [name, {...value, material: 'lens', role: 'lens'}]));
const lensesBehindFront = (): Record<string, Part> => {
  const parts = defaultParts();
  for (const name of ['Lens_L', 'Lens_R']) {const [x0, x1, y0, y1] = parts[name]!.box; parts[name] = {...parts[name]!, box: [x0, x1, y0, y1, -61, -59]};}
  return parts;
};

/** One shared case: what arview must decide, and which rows can be compared with the Python validator. */
interface Case {
  readonly name: string;
  readonly build: () => Built | ArrayBuffer;
  /** The exact set of failed rows (with `includes`: rows that must be among the failed ones, as spec.py's cascade tests). */
  readonly rows: readonly SpecRowName[];
  readonly includes?: boolean;
  /** A text that must appear in a failed row's detail ('' when every row passes). */
  readonly text: string;
  /** The row whose §1 refusal ends the load, or null when the model renders. */
  readonly refused: SpecRowName | null;
  /** Rows that can be compared with Python (default: all). A refusal before parsing leaves the others unchecked. */
  readonly compare?: readonly SpecRowName[];
  readonly resources?: Record<string, Uint8Array>;
}
const b = (options: BuildOptions = {}) => () => buildGlb(options);
const CASES: readonly Case[] = [
  // glasses3d/tests/test_spec.py CASES, in the same order.
  {name: 'millimetres', build: b({scale: 1.0}), rows: ['units', 'origin', 'orientation'], text: 'millimetre-scaled', refused: null},
  {name: 'centimetres', build: b({scale: 0.1}), rows: ['units', 'origin', 'orientation'], text: 'centimetre-scaled', refused: null},
  {name: 'origin y +5', build: b({offsetMm: [0, 5, 0]}), rows: ['origin'], text: 'bridge underside is at y +5.00', refused: null},
  {name: 'origin x +3', build: b({offsetMm: [3, 0, 0]}), rows: ['origin'], text: 'the lenses are centred at x +3.00 mm', refused: null},
  {name: 'origin z +5', build: b({offsetMm: [0, 0, 5]}), rows: ['origin'], text: "outside the bridge underside's depth", refused: null},
  {name: 'origin y +1.1', build: b({offsetMm: [0, 1.1, 0]}), rows: ['origin'], text: 'y +1.10 mm', refused: null},
  {name: 'parent 1e-5 translation', build: b({parentMatrix: trans(0, 1e-5, 0)}), rows: ['transforms'], text: 'non-identity', refused: 'transforms'},
  {name: 'parent 1e-5 scale', build: b({parentMatrix: diag(1 + 1e-5, 1, 1)}), rows: ['transforms'], text: 'non-identity', refused: 'transforms'},
  {name: 'parent 1 mm', build: b({parentMatrix: trans(0, 0.001, 0)}), rows: ['transforms'], text: 'non-identity', refused: 'transforms'},
  {name: 'instancing', build: b({patch: pInstancing}), rows: ['transforms'], text: 'EXT_mesh_gpu_instancing', refused: 'transforms'},
  {name: 'non-finite', build: b({nan: true, target: 'Brow_L'}), rows: ['geometry'], text: 'non-finite', refused: 'geometry'},
  {name: 'LINES primitive', build: b({mode: 1, target: 'Brow_L'}), rows: ['geometry'], text: 'mode 1 (LINES)', refused: null},
  {name: 'morph targets', build: b({morph: true, target: 'Brow_L'}), rows: ['geometry'], text: 'morph targets', refused: 'geometry'},
  {name: 'animations', build: b({animation: true}), rows: ['geometry'], text: 'animations', refused: null},
  {name: 'unused skins', build: b({patch: pSkins}), rows: ['geometry'], text: 'skins present', refused: null},
  {name: 'index count 35', build: b({indexCount: 35, target: 'Brow_L'}), rows: ['geometry'], text: '35 indices is not a multiple of 3', refused: null},
  {name: 'index 99', build: b({indexValue: 99, target: 'Brow_L'}), rows: ['geometry'], text: 'index 99 >= vertex count 8', refused: 'geometry'},
  {name: 'zero triangles', build: b({patch: pZeroTriangles}), rows: ['geometry', 'units', 'origin', 'orientation'], text: 'the file has no triangles', refused: 'geometry'},
  {name: 'no lens', build: b({roles: false, parts: unnamedParts(), materials: {frame: {name: 'Frame'}, lens: {name: 'Pane'}}}),
    rows: ['components'], text: 'no lens identified', refused: 'components'},
  {name: 'everything lens', build: b({parts: allLensParts()}), rows: ['components', 'orientation'], text: 'no frame/temple primitives', refused: null},
  {name: 'lens without material', build: b({noMaterial: ['Lens_L', 'Lens_R']}), rows: ['components'], text: 'has no material', refused: null},
  {name: 'lenses behind front', build: b({parts: lensesBehindFront()}), rows: ['orientation'], text: 'behind the front', refused: null},
  {name: 'upside down', build: b({preMm: matmul(trans(0, 4, 0), diag(-1, -1, 1))}), rows: ['orientation'], text: 'not +Y up', refused: null},
  {name: 'mirrored in Y', build: b({preMm: matmul(trans(0, 4, 0), diag(1, -1, 1))}), rows: ['orientation'], text: 'not +Y up', refused: null},
  {name: 'no bridge', build: b({parts: without(['Bridge'])}), rows: ['origin'], text: 'origin unverifiable', refused: null},
  {name: 'missing image', build: b({patch: pMissingImage}), rows: [...SPEC_ROWS], text: 'missing external resource', refused: 'format', compare: ['format']},
  {name: 'no scenes', build: b({patch: pNoScenes}), rows: [...SPEC_ROWS], text: 'no scenes', refused: 'format', compare: ['format']},
  {name: 'texCoord -1', build: b({patch: pTexCoordMinusOne}), rows: ['format'], text: 'texCoord -1', refused: null},
  {name: 'temple right missing', build: b({parts: without(['Temple_L'])}), rows: ['orientation'], text: 'temple right missing', refused: null},
  // test_orientation_and_cascade_failures
  {name: 'Z up', build: b({rotateZUp: true}), rows: ['orientation'], text: "Y is not the lenses' middle axis", includes: true, refused: null},
  {name: 'rolled +25', build: b({preMm: rot('z', 25)}), rows: ['orientation'], text: 'rolled', includes: true, refused: null},
  {name: 'rolled -25', build: b({preMm: rot('z', -25)}), rows: ['orientation'], text: 'rolled', includes: true, refused: null},
  {name: 'pitched +25', build: b({preMm: rot('x', 25)}), rows: ['orientation'], text: 'pitched', includes: true, refused: null},
  {name: 'pitched -25', build: b({preMm: rot('x', -25)}), rows: ['orientation'], text: 'pitched', includes: true, refused: null},
  {name: 'faces backwards', build: b({preMm: rot('y', 180)}), rows: ['orientation'], text: 'faces backwards', includes: true, refused: null},
  {name: 'broken bridge', build: b({indexValue: 99}), rows: ['geometry', 'origin'], text: 'excluded after geometry failures', includes: true, refused: 'geometry'},
  // test_valid_variants_pass and friends
  {name: 'valid default', build: b(), rows: [], text: '', refused: null},
  {name: 'valid identity node matrix', build: b({nodeMatrix: diag(1, 1, 1)}), rows: [], text: '', refused: null},
  {name: 'valid origin y +0.9', build: b({offsetMm: [0, 0.9, 0]}), rows: [], text: '', refused: null},
  {name: 'valid parent 1e-7 translation', build: b({parentMatrix: trans(0, 1e-7, 0)}), rows: [], text: '', refused: null},
  {name: 'valid parent 1e-7 scale', build: b({parentMatrix: diag(1 + 1e-7, 1, 1)}), rows: [], text: '', refused: null},
  {name: 'valid triangle strip', build: b({mode: 5}), rows: [], text: '', refused: null},
  {name: 'valid shield', build: b({parts: shieldParts()}), rows: [], text: '', refused: null},
  {name: 'valid roll 2', build: b({preMm: rot('z', 2)}), rows: [], text: '', refused: null},
  {name: 'valid pitch 8', build: b({preMm: rot('x', 8)}), rows: [], text: '', refused: null},
  {name: 'valid short temple', build: b({parts: replaceBox('Temple_L', [-70, -66, 2, 8, -65, -2])}), rows: [], text: '', refused: null},
  {name: 'valid deep hierarchy', build: b({patch: pDeep}), rows: [], text: '', refused: null},
  {name: 'valid texture 4100 px', build: b({texturePx: [4100, 8]}), rows: [], text: '', refused: null},
  {name: 'valid crystal frame', build: b({materials: {frame: {name: 'Crystal acetate', extensions: {KHR_materials_transmission: {transmissionFactor: 0.8}}},
    lens: {name: 'Lens tint'}}}), rows: [], text: '', refused: null},
  {name: 'valid narrow 105 mm', build: b({preMm: diag(0.75, 0.75, 0.75)}), rows: [], text: '', refused: null},
  {name: 'units 84 mm', build: b({preMm: diag(0.6, 0.6, 0.6)}), rows: ['units', 'origin', 'orientation'], text: 'not a plausible frame width', refused: null},
  {name: 'units 238 mm', build: b({preMm: diag(1.7, 1.7, 1.7)}), rows: ['units', 'origin', 'orientation'], text: 'not a plausible frame width', refused: null},
  {name: 'shared lens/frame material', build: b({parts: {...defaultParts(), Bridge: {box: [-9, 9, 0, 4, -2, 2], material: 'lens', role: 'frame'}}}),
    rows: ['components'], text: 'used by both lens and non-lens primitives', refused: null},
  {name: 'external buffer present', build: b({externalBuffer: 'external.bin'}), rows: [], text: '', refused: null},
  {name: 'unreadable bytes', build: () => new TextEncoder().encode('not a glb at all, sorry').slice().buffer, rows: [...SPEC_ROWS], text: 'unreadable GLB',
    refused: 'format'},
  {name: 'empty default scene', build: b({patch: pEmptyScene}), rows: [...SPEC_ROWS], text: 'has no nodes', refused: 'format', compare: ['format']},
  // A drawn primitive naming a material that does not exist: drawn with the default material, the Format row failed (spec.py).
  {name: 'material 7', build: b({patch: pBadMaterial}), rows: ['format'], text: 'Bridge: material 7 does not exist (2 material(s))', refused: null},
  {name: 'zero vertices', build: b({patch: pZeroVertices}), rows: ['geometry', 'units', 'origin', 'orientation'], text: 'the file has no triangles',
    refused: 'geometry'},
  // Origin x is judged on the lenses (spec.py _origin, test_origin_x_is_judged_on_the_lenses_not_the_temples_or_ornaments):
  // a one-sided ornament in the front band never moves it, lenses shifted inside a symmetric frame do.
  {name: 'one-sided ornament in the front band', build: b({parts: withPart('Ornament', [70, 84, 0, 4, -2, 2])}), rows: [], text: '', refused: null},
  {name: 'plate in the front band', build: b({parts: withPart('Logo', [70, 74, 3, 7, -30, -20])}), rows: [], text: '', refused: null},
  {name: 'lenses shifted +3 in a symmetric frame', build: b({parts: lensesShifted(3)}), rows: ['origin'], text: 'the lenses are centred at x +3.00 mm', refused: null},
  // test_origin_x_falls_back_to_the_clipped_front_without_lenses_on_both_sides
  {name: 'left lens only: front judged', build: b({parts: leftLensOnly()}), rows: [], text: '', refused: null},
  {name: 'left lens only, moved +3', build: b({parts: leftLensOnly(), offsetMm: [3, 0, 0]}), rows: ['origin'], text: 'mm) is centred at x +3.00 mm', refused: null},
  // Entries nothing loads are never refused; a primitive without POSITION fails the Format row only (spec.py does too).
  {name: 'primitive without POSITION', build: b({patch: pNoPosition}), rows: ['format'], text: 'Brow_L: geometry unreadable (no POSITION attribute)', refused: null},
  {name: 'unused image without uri or bufferView', build: b({patch: pOrphanImage}), rows: [], text: '', refused: null},
  {name: 'unused bufferView to a missing buffer', build: b({patch: pOrphanView}), rows: [], text: '', refused: null},
  // glasses3d/plan/archive/20261019/WP1_DESIGN.md §1 (binding): every missing external resource is refused, used or not; the message says which.
  {name: 'unused missing external image', build: b({patch: pUnusedMissingImage}), rows: [...SPEC_ROWS], text: 'missing_unused.png (not used by the default scene)',
    refused: 'format', compare: ['format']},
  // Only the default scene is parsed: broken references outside it, or on what arview never draws, refuse nothing.
  {name: 'second scene with a broken mesh', build: b({patch: pSecondSceneBrokenMesh}), rows: [], text: '', refused: null},
  {name: 'animation channel to node 99', build: b({patch: pAnimationNode99}), rows: ['geometry'], text: 'animations present', refused: null},
  {name: 'default-scene camera 99', build: b({patch: pCamera99}), rows: [], text: '', refused: null},
  {name: 'default-scene light 99', build: b({patch: pLight99}), rows: [], text: '', refused: null},
  // A skin on a drawn node is a §2 Geometry refusal (decided from the JSON), never a parse failure.
  {name: 'default-scene skin 99', build: b({patch: pSkin99}), rows: ['geometry'], includes: true, text: 'Brow_L: skinned mesh', refused: 'geometry',
    compare: ['geometry']},
  // A required KTX2 texture without a GPU renderer: rendered, the texture a reported fallback (never a parse refusal).
  {name: 'required KTX2 texture', build: b({patch: pKtx2Required}), rows: [], text: '', refused: null},
];

const reports = new Map<string, ModelReport>();
async function caseReport(entry: Case): Promise<ModelReport> {
  let report = reports.get(entry.name);
  if (!report) {report = await check(entry.build(), {resources: entry.resources}); reports.set(entry.name, report);}
  return report;
}

test('every shared case fails exactly its own rows, refuses exactly the §1-table violations, and renders everything else', async () => {
  const quiet = console.warn; console.warn = () => {};   // three's GLTFLoader warns about the deliberately odd files
  try {
    for (const entry of CASES) {
      const report = await caseReport(entry);
      if (entry.includes) for (const row of entry.rows) assert.ok(failed(report).has(row), `${entry.name}: ${row} fails: ${errors(report).join(' | ')}`);
      else assert.deepEqual(sorted(failed(report)), sorted(entry.rows), `${entry.name}: ${errors(report).join(' | ')}`);
      if (entry.text) assert.ok(errors(report).some(error => error.includes(entry.text)), `${entry.name}: "${entry.text}" in ${errors(report).join(' | ')}`);
      assert.equal(report.status, entry.refused ? 'refused' : 'rendered', `${entry.name}: ${report.refusal.join(' | ')}`);
      if (entry.refused) {
        assert.ok(report.refusal.length > 0 && report.refusal.every(reason => reason.startsWith('§2 ')), entry.name);
        assert.ok(report.refusal.some(reason => reason.toLowerCase().startsWith(`§2 ${entry.refused}:`)), `${entry.name}: ${report.refusal.join(' | ')}`);
      } else assert.deepEqual(report.refusal, [], entry.name);
      assert.match(report.rendered_sha256 ?? '', /^[0-9a-f]{64}$/, `${entry.name}: the SHA-256 of the bytes is always reported`);
    }
  } finally {console.warn = quiet;}
});

/* ------------------------------------------------------------------------------------- refusals, one at a time */

test('format refusals: not a GLB, wrong version, truncated, broken references, no scene, missing resource, parse failure', async () => {
  assert.throws(() => readGlbContainer(new Uint8Array(8).buffer), GlbFormatError);
  const good = buildGlb().glb, view = new DataView(good.slice(0));
  view.setUint32(4, 1, true);
  let report = await check(view.buffer);
  assert.match(report.refusal[0]!, /^§2 Format: unreadable GLB: GLB version 1, expected 2$/);
  const truncated = good.slice(0, good.byteLength - 8);
  report = await check(truncated);
  assert.match(report.refusal[0]!, /header length .* != file size/);
  const {doc, bin} = splitGlb(good);
  report = await check(joinGlb({...doc, asset: {version: '1.0'}}, bin));
  assert.match(report.refusal[0]!, /asset\.version "1\.0" \(glTF 2\.0 is required\)/);
  report = await check(joinGlb({...doc, accessors: (doc.accessors as Json[]).map((accessor, index) => index === 0 ? {...accessor, bufferView: 999} : accessor)}, bin));
  assert.match(report.refusal[0]!, /accessor 0 refers to bufferView 999, which does not exist/);
  for (const row of SPEC_ROWS.filter(row => row !== 'format')) assert.match(report.spec[row].detail[0]!, /^not checked: the file could not be read$/);
  report = await check(joinGlb({...doc, buffers: [{byteLength: 10, uri: 'gone.bin'}]}, null));
  assert.match(report.refusal[0]!, /missing external resource\(s\) next to the file: \["buffer 0: gone\.bin"\]/);
  // The GLB itself was read: the other rows are not checked because of the refusal, not 'because the file could not be read'.
  for (const row of SPEC_ROWS.filter(row => row !== 'format')) {
    assert.deepEqual(report.spec[row].detail, ['not checked: refused at the Format row (missing external resource)']);
  }
  // A file three cannot parse although every reference resolves (an accessor running past its view).
  report = await check(joinGlb({...doc, bufferViews: (doc.bufferViews as Json[]).map((v, index) => index === 0 ? {...v, byteLength: 4} : v)}, bin));
  assert.equal(report.status, 'refused');
  // three's own RangeError (a 24-float view over 4 bytes), as the parse refusal; every other row is not checked.
  assert.deepEqual(report.refusal, ['§2 Format: the GLB could not be parsed: Invalid typed array length: 24']);
  for (const row of SPEC_ROWS.filter(row => row !== 'format')) assert.deepEqual(report.spec[row].detail, ['not checked: the file could not be read']);
});

test('broken references are refused only on the loaded path: used views and buffers, meshopt read from its extension buffer; a broken texture or material reference renders without it', async () => {
  const {doc, bin} = splitGlb(buildGlb().glb);
  // An image the frame material uses, with neither uri nor bufferView: three would throw inside the material, so the
  // reference is left out (glasses3d/plan/archive/20261019/WP1_DESIGN.md §1: a texture that cannot be loaded is a reported fallback): the material draws
  // without it, the Format row fails, and the file renders.
  let report = await check(joinGlb({...doc, images: [{name: 'x'}], textures: [{source: 0}],
    materials: (doc.materials as Json[]).map((material, index) => index === 0 ? {...material, pbrMetallicRoughness: {baseColorTexture: {index: 0}}} : material)}, bin));
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.deepEqual(sorted(failed(report)), ['format']);
  assert.deepEqual(report.spec.format.detail.slice(1),
    ["material 'Frame acetate' pbrMetallicRoughness.baseColorTexture: texture 0 cannot be loaded (image 0 has neither a uri nor a bufferView)"]);
  assert.deepEqual(report.fallbacks.map(entry => [entry.subsystem, entry.detail]), [['textures', 'Material "Frame acetate" pbrMetallicRoughness.baseColorTexture: '
    + 'image 0 has neither a uri nor a bufferView, so three.js could not load it; the reference was left out and the material draws without that texture (§2 Format).']]);
  // A drawn texture index that does not exist: the Format row as spec.py words it, one fallback, rendered.
  report = await check(joinGlb({...doc, materials: (doc.materials as Json[]).map((material, index) => index === 0
    ? {...material, pbrMetallicRoughness: {baseColorTexture: {index: 4}}, emissiveTexture: {index: 0}} : material)}, bin));
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.deepEqual(report.spec.format.detail.slice(1), ["material 'Frame acetate' pbrMetallicRoughness.baseColorTexture: texture 4 does not exist",
    "material 'Frame acetate' emissiveTexture: texture 0 does not exist"]);
  assert.deepEqual(report.fallbacks.map(entry => entry.subsystem), ['textures', 'textures']);
  // A lens primitive naming material 7: the default material, Format failed, and Components failed as spec.py does
  // ("has no material": the lens is not distinguished by material).
  report = await check(joinGlb({...doc, meshes: (doc.meshes as Json[]).map(mesh => mesh.name === 'Lens_L'
    ? {...mesh, primitives: (mesh.primitives as Json[]).map(primitive => ({...primitive, material: 7}))} : mesh)}, bin));
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.deepEqual(sorted(failed(report)), ['components', 'format']);
  assert.ok(report.spec.format.detail.includes('Lens_L: material 7 does not exist (2 material(s))'), report.spec.format.detail.join(' | '));
  assert.ok(report.spec.components.detail.includes("lens primitive 'Lens_L' has no material, so it is not distinguished by material"), report.spec.components.detail.join(' | '));
  // A used bufferView pointing at a buffer that does not exist.
  report = await check(joinGlb({...doc, bufferViews: (doc.bufferViews as Json[]).map((v, index) => index === 0 ? {...v, buffer: 5} : v)}, bin));
  assert.deepEqual(report.refusal, ['§2 Format: unreadable GLB: bufferView 0 refers to buffer 5, which does not exist']);
  // A texture whose material nothing draws is never loaded: its broken image is the Format row's (spec.py), not a refusal.
  report = await check(joinGlb({...doc, materials: [...(doc.materials as Json[]), {name: 'unused', pbrMetallicRoughness: {baseColorTexture: {index: 3}}}]}, bin));
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.deepEqual(sorted(failed(report)), ['format']);
  assert.deepEqual(report.spec.format.detail.slice(1), ["material 'unused' pbrMetallicRoughness.baseColorTexture: texture 3 does not exist"]);
  // meshopt: the view is read from the extension's buffer; its uri-less fallback buffer is never dereferenced.
  const meshopt: Json = {asset: {version: '2.0'}, scene: 0, scenes: [{nodes: [0]}], nodes: [{mesh: 0}],
    meshes: [{primitives: [{attributes: {POSITION: 0}}]}], accessors: [{bufferView: 0, componentType: 5126, count: 1, type: 'VEC3'}],
    buffers: [{byteLength: 16}, {byteLength: 12, extensions: {EXT_meshopt_compression: {fallback: true}}}],
    bufferViews: [{buffer: 1, byteLength: 12, extensions: {EXT_meshopt_compression: {buffer: 0, byteLength: 16, byteStride: 12, count: 1, mode: 'ATTRIBUTES'}}}]};
  const references = loadedReferences(meshopt, true);
  assert.equal(references.problem, null);
  assert.deepEqual([...references.buffers], [0], 'only the extension buffer is reached');
  assert.match(loadedReferences({...meshopt, bufferViews: [{...(meshopt.bufferViews as Json[])[0], extensions: {EXT_meshopt_compression: {buffer: 7}}}]}, true).problem!,
    /bufferView 0 EXT_meshopt_compression refers to buffer 7, which does not exist/);
});

test('only the default scene is handed to three; every omission is said, and the identity stays the original bytes', async () => {
  const built = buildGlb({patch: (doc, add, view) => {pSecondSceneBrokenMesh(doc, add, view); pAnimationNode99(doc, add, view); pCamera99(doc, add, view);
    pLight99(doc, add, view); doc.cameras = [{type: 'perspective', perspective: {yfov: 0.8, znear: 0.01}}];}});
  const report = new ModelReportBuilder(URL_BASE + 'extras.glb', 'extras');
  const loaded = await loadModel(built.glb, report, {url: URL_BASE + 'extras.glb', renderer: null, plugins: [stubTextures]});
  assert.equal(loaded.gltf.scenes.length, 1, 'three never saw the second scene');
  assert.deepEqual(loaded.gltf.animations, []);
  assert.deepEqual(loaded.gltf.cameras, []);
  let lights = 0, cameras = 0;
  loaded.gltf.scene.traverse(object => {if ((object as {isLight?: boolean}).isLight) lights++; if ((object as {isCamera?: boolean}).isCamera) cameras++;});
  assert.equal(lights + cameras, 0, 'no light or camera object was added to the asset');
  // Later readers see the file as spec.py does: the original JSON, with three's associations (indices unchanged).
  assert.equal(loaded.source.json, loaded.container.json);
  assert.equal(((loaded.source.json as Json).scenes as Json[]).length, 2);
  assert.ok(Array.isArray((loaded.source.json as Json).animations));
  const snapshot = report.snapshot();
  assert.equal(snapshot.rendered_sha256, createHash('sha256').update(new Uint8Array(built.glb)).digest('hex'));
  assert.ok(snapshot.warnings.includes('Ignored: 1 scene(s) besides the default scene (0) and 1 node(s) outside the default scene; arview draws only '
    + 'the default scene and never reads the rest.'), snapshot.warnings.join(' | '));
  assert.ok(snapshot.warnings.includes("Ignored: the file's camera(s) (1 defined, 1 on default-scene nodes); arview views every model through its own camera."),
    snapshot.warnings.join(' | '));
  assert.deepEqual(snapshot.fallbacks, [{subsystem: 'lights', stage: 'load',
    detail: '1 default-scene node(s) carry a KHR_lights_punctual light; it is not added to the scene: arview lights every model with its own lights.'}]);
  // The animations are the normalisation pass's (from the original JSON): the Geometry row fails, and it is a fallback.
  const checked = await check(built);
  assert.equal(checked.status, 'rendered', checked.refusal.join(' | '));
  assert.deepEqual(sorted(failed(checked)), ['geometry']);
  assert.deepEqual(checked.spec.geometry.detail, ['animations present (the model must be static)']);
  assert.ok(checked.fallbacks.some(entry => entry.subsystem === 'geometry' && entry.detail.startsWith('The file has 1 animation(s)')));
  // A shared mesh is judged on the default scene only (spec.py `_transforms`): a second scene reusing a mesh is no warning.
  const reused = await check(buildGlb({patch: doc => {
    const nodes = doc.nodes as Json[]; (doc.scenes as Json[]).push({nodes: [nodes.push({name: 'Copy', mesh: 0}) - 1]});
  }}));
  assert.equal(reused.status, 'rendered', reused.refusal.join(' | '));
  assert.ok(!reused.warnings.some(warning => warning.startsWith('mesh(es) drawn by more than one node')), reused.warnings.join(' | '));
});

test('the page lists every failed row, not only the warnings (reportLines): orientation, implausible units, a refusal', async () => {
  const cases: [Record<string, Part>, string[]][] = [[without(['Temple_L']), ['Orientation: temple right missing']],
    [without(['Temple_L', 'Temple_R']), ['Orientation: temple right missing', 'Orientation: temple left missing']]];
  for (const [parts, expected] of cases) {
    const report = await check(buildGlb({parts}));
    const lines = reportLines(report);
    for (const prefix of expected) {
      assert.ok(lines.some(line => line.kind === 'error' && line.text.startsWith(prefix)), `${prefix} in ${lines.map(line => line.text).join(' | ')}`);
      assert.ok(report.errors.some(error => error.startsWith(prefix.replace('Orientation', 'orientation'))), report.errors.join(' | '));
    }
    assert.ok(!report.warnings.some(warning => warning.includes('temple right missing')), 'the row writes no warning of its own: only the row carries it');
  }
  const units = await check(buildGlb({preMm: diag(0.6, 0.6, 0.6)}));
  assert.ok(reportLines(units).some(line => line.kind === 'error' && line.text.startsWith('Units: ') && line.text.includes('not a plausible frame width')),
    reportLines(units).map(line => line.text).join(' | '));
  assert.ok(reportLines(units).some(line => line.text === 'Origin: not checked: units are wrong'));
  // A refusal: its reasons first, its own row detail not repeated, the unchecked rows listed.
  const refused = await check(buildGlb({patch: pNoScenes}));
  const lines = reportLines(refused);
  assert.equal(lines[0]!.kind, 'refusal');
  assert.match(lines[0]!.text, /^§2 Format: the document has no scenes/);
  assert.ok(!lines.some(line => line.kind === 'error' && line.text.startsWith('Format: the document has no scenes')), 'the refusal is listed once');
  assert.ok(lines.some(line => line.text === 'Orientation: not checked: the file has no scene to render'), lines.map(line => line.text).join(' | '));
  assert.deepEqual(refused.errors, SPEC_ROWS.flatMap(row => refused.spec[row].detail.map(detail => `${row}: ${detail}`)), 'errors: every failed row, as spec.py words them');
});

test('lens_primitives counts every primitive of the default scene, LINES included, as spec.py does', async t => {
  const built = buildGlb({mode: 1, target: 'Lens_L'});
  const report = await check(built);
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.equal(report.measurements.lens_primitives, 2, 'the LINES lens is still a lens primitive (it is reported and not drawn)');
  const uses = report.lens_identification!.materials.flatMap(entry => entry.uses);
  assert.ok(uses.some(use => use.node_name === 'Lens_L' && use.mode === 1 && use.class === 'lens'), 'the stray is classified in the identification');
  if (!PYTHON) {t.diagnostic('glasses3d/.venv Python is not available: lens_primitives agreement with spec.py was NOT checked'); return;}
  const directory = mkdtempSync(join(tmpdir(), 'arview-lensprims-'));
  t.after(() => rmSync(directory, {recursive: true, force: true}));
  const path = join(directory, 'lines-lens.glb');
  writeFileSync(path, new Uint8Array(built.glb));
  assert.equal(pythonRows([path])[path]!.lens_primitives, 2);
});

test('a primitive without POSITION fails the Format row, is left out of every measurement and is not drawn; the rest renders', async () => {
  const built = buildGlb({patch: pNoPosition});
  const {report, scene} = await checkModel(built.glb, {url: URL_BASE + 'noposition.glb', renderer: null, plugins: [stubTextures]});
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.deepEqual(sorted(failed(report)), ['format']);
  assert.deepEqual(report.spec.format.detail.slice(1), ['Brow_L: geometry unreadable (no POSITION attribute)']);
  assert.deepEqual(report.measurements.excluded_from_measurement, ['Brow_L']);
  assert.deepEqual(report.fallbacks.map(entry => [entry.subsystem, entry.detail]),
    [['geometry', 'Primitive "Brow_L" has no readable XYZ positions (§2 Format): it is not drawn.']]);
  let positionless = 0, meshes = 0;
  scene!.traverse(object => {if (object instanceof Mesh) {meshes++; if (!object.geometry.getAttribute('position')) positionless++;}});
  assert.equal(positionless, 0, 'no subsystem can meet the position-less primitive');
  assert.equal(meshes, 7, 'the seven readable parts still render');
});

test('an index accessor WebGL cannot draw as stored (signed, VEC3, normalized, FLOAT) fails the Geometry row as spec.py words it, is left out of the measurements, and still draws', async () => {
  const faces = box(0, 1, 0, 1, 0, 1).faces.flat();
  /** The bridge's indices re-declared: Int16 (the same bytes as Uint16 for 0..7), Uint32 VEC3 (count 12), a normalized
   *  Uint16 (three's getX would read 5 as 5/65535), or FLOAT (three hands it to drawElements as gl.FLOAT: INVALID_ENUM). */
  const redeclare = (componentType: number, type: string, component: number, extra: Json = {}): Patch => (doc, add) => {
    const accessor = add(faces, component, 'SCALAR'), accessors = doc.accessors as Json[];
    accessors[accessor] = {...accessors[accessor]!, componentType, type, count: faces.length / (type === 'VEC3' ? 3 : 1), ...extra};
    ((doc.meshes as Json[])[0]!.primitives as Json[])[0]!.indices = accessor;
  };
  const bridgeIndex = (scene: CheckedScene): {array: ArrayLike<number>; itemSize: number; normalized: boolean; isInterleavedBufferAttribute?: boolean} | null => {
    let index: unknown = null;
    scene!.traverse(object => {if (object instanceof Mesh && object.name === 'Bridge') index = object.geometry.getIndex();});
    return index as {array: ArrayLike<number>; itemSize: number; normalized: boolean} | null;
  };
  for (const [patch, issue, stored] of [
    [redeclare(5122, 'SCALAR', 5123), 'indices componentType 5122 is not valid glTF (indices are unsigned: 5121, 5123 or 5125)', 'Int16Array'],
    [redeclare(5125, 'VEC3', 5125), "indices accessor type 'VEC3' is not SCALAR", 'Uint32Array x3'],
    [redeclare(5123, 'SCALAR', 5123, {normalized: true}), 'indices accessor is normalized', 'Uint16Array (normalized)'],
    [redeclare(5126, 'SCALAR', 5126), 'indices componentType 5126 is not valid glTF (indices are unsigned: 5121, 5123 or 5125)', 'Float32Array'],
  ] as const) {
    const {report, scene} = await checkModel(buildGlb({patch}).glb, {url: URL_BASE + 'indices.glb', renderer: null, plugins: [stubTextures]});
    assert.equal(report.status, 'rendered', `${stored}: ${report.refusal.join(' | ')}`);
    // spec.py: the Geometry row fails and the bridge leaves the measurements, so the origin is unverifiable.
    assert.deepEqual(sorted(failed(report)), ['geometry', 'origin'], errors(report).join(' | '));
    assert.deepEqual(report.spec.geometry.detail, [`Bridge: ${issue}`]);
    assert.deepEqual(report.measurements.excluded_from_measurement, ['Bridge']);
    assert.equal(report.measurements.triangles, 84, 'twelve bridge triangles, as spec.py counts the stored values');
    assert.deepEqual(report.fallbacks.map(entry => [entry.subsystem, entry.detail]),
      [['geometry', `"Bridge" stores its indices as ${stored} (not valid glTF): they were rewritten as unsigned 32-bit SCALAR indices so it draws (§2 Geometry).`]]);
    const drawn = bridgeIndex(scene);
    assert.ok(drawn && drawn.array instanceof Uint32Array && drawn.itemSize === 1 && !drawn.normalized, `${stored}: WebGL draws unsigned, non-normalized SCALAR indices only`);
    assert.deepEqual(Array.from(drawn.array), faces, `${stored}: the stored vertex numbers, never denormalized`);
  }
  // An index view with a byteStride is valid glTF (spec.py reads the stride and passes): three makes it an
  // InterleavedBufferAttribute, which WebGL cannot draw as stored. Rewritten and reported; no row fails.
  const interleaved = buildGlb({patch: (doc, add) => {
    const padded = faces.flatMap(value => [value, 0]), accessor = add(padded, 5123, 'SCALAR'), accessors = doc.accessors as Json[];
    accessors[accessor] = {...accessors[accessor]!, count: faces.length};
    (doc.bufferViews as Json[])[accessors[accessor]!.bufferView as number]!.byteStride = 4;
    ((doc.meshes as Json[])[0]!.primitives as Json[])[0]!.indices = accessor;
  }});
  const strided = await checkModel(interleaved.glb, {url: URL_BASE + 'interleaved.glb', renderer: null, plugins: [stubTextures]});
  assert.equal(strided.report.status, 'rendered', strided.report.refusal.join(' | '));
  assert.deepEqual(sorted(failed(strided.report)), [], errors(strided.report).join(' | '));
  assert.deepEqual(strided.report.fallbacks.map(entry => entry.detail), ['"Bridge" stores its indices as Uint16Array (interleaved) (not drawable by WebGL as stored): '
    + 'they were rewritten as unsigned 32-bit SCALAR indices so it draws (§2 Geometry).']);
  const restrided = bridgeIndex(strided.scene);
  assert.ok(restrided && restrided.array instanceof Uint32Array && restrided.isInterleavedBufferAttribute !== true);
  assert.deepEqual(Array.from(restrided.array), faces);
  // A TRIANGLE_STRIP with a normalized or VEC3 index: three's loader triangulates it through getX (every vertex number
  // denormalized to 0; one component per VEC3 element), so arview rebuilds its triangles from the stored values (load.ts
  // rebuildMisdeclaredStrips). Rendered, the row failed, and a reported fallback.
  const strip = stripIndices(box(0, 1, 0, 1, 0, 1).faces), used = strip.length - strip.length % 3;
  /** glTF 2.0 §3.7.2.1 (spec.py `triangulate`): triangle i is (i, i+1, i+2), every odd one with its winding flipped. */
  const stripTriangles = (values: readonly number[]): number[] => values.slice(0, -2).flatMap((_, i) => [values[i]!, values[i + 1 + (i % 2)]!, values[i + 2 - (i % 2)]!]);
  for (const [declaration, issue, stored] of [
    [{normalized: true}, 'indices accessor is normalized', strip],
    [{type: 'VEC3', count: used / 3}, "indices accessor type 'VEC3' is not SCALAR", strip.slice(0, used)],
  ] as const) {
    const checked = await checkModel(buildGlb({mode: 5, patch: doc => {
      const accessors = doc.accessors as Json[], index = (((doc.meshes as Json[])[0]!.primitives as Json[])[0]!.indices as number);
      accessors[index] = {...accessors[index]!, ...declaration};
    }}).glb, {url: URL_BASE + 'strip.glb', renderer: null, plugins: [stubTextures]});
    assert.equal(checked.report.status, 'rendered', checked.report.refusal.join(' | '));
    assert.deepEqual(checked.report.spec.geometry.detail, [`Bridge: ${issue}`]);
    assert.deepEqual(checked.report.fallbacks.map(entry => [entry.subsystem, entry.detail]), [['geometry', `"Bridge" is a TRIANGLE_STRIP whose index accessor is `
      + `${'normalized' in declaration ? 'normalized' : 'VEC3, not SCALAR'} (not valid glTF): three.js would triangulate the indices as declared, so arview `
      + 'rebuilt its triangles from the stored values (§2 Geometry).']]);
    const drawn = bridgeIndex(checked.scene);
    assert.ok(drawn && drawn.array instanceof Uint32Array && drawn.itemSize === 1 && !drawn.normalized);
    assert.deepEqual(Array.from(drawn.array), stripTriangles(stored), 'the stored strip, triangulated as glTF (and spec.py) do');
    assert.equal(checked.report.measurements.triangles, 72 + stored.length - 2, 'as spec.py counts the stored strip');
  }
  // A fractional FLOAT index names no vertex: out of range, a §1 Geometry refusal (spec.py fails the row).
  const fractional = await check(buildGlb({patch: (doc, add) => {
    const accessor = add([2.5, ...faces.slice(1)], 5126, 'SCALAR'); ((doc.meshes as Json[])[0]!.primitives as Json[])[0]!.indices = accessor;
  }}));
  assert.equal(fractional.status, 'refused');
  assert.ok(fractional.refusal.includes('§2 Geometry: Bridge: index value 2.5 is not an integer (out of range: no vertex has that number)'), fractional.refusal.join(' | '));
});
type CheckedScene = Awaited<ReturnType<typeof checkModel>>['scene'];

test('KTX2 without a GPU renderer: a required KHR_texture_basisu texture is a reported fallback, never a parse refusal', async () => {
  const quiet = console.warn; console.warn = () => {};
  let checked: Awaited<ReturnType<typeof checkModel>>;
  try {checked = await checkModel(buildGlb({patch: pKtx2Required}).glb, {url: URL_BASE + 'ktx2.glb', renderer: null});}
  finally {console.warn = quiet;}
  const {report} = checked;
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.deepEqual(report.fallbacks, [{subsystem: 'textures', stage: 'load',
    detail: '1 texture(s) could not be decoded and their materials draw without them: texture 0 (KTX2, no transcoder without a GPU renderer).'}]);
  assert.ok(report.warnings.includes(report.fallbacks[0]!.detail));
  assert.ok(report.spec.format.detail.includes('compressed with KHR_texture_basisu: not transcoded here (no GPU renderer); see the textures fallback'));
  assert.ok(report.live_site_would_fail.includes('uses KHR_texture_basisu (live site loads plain glTF only)'));
  assert.deepEqual(report.measurements.textures, [{image: 0, size: [64, 64]}]);
  let mapped = 0; checked.scene!.traverse(object => {if (object instanceof Mesh && (object.material as MeshPhysicalMaterial).map) mapped++;});
  assert.equal(mapped, 0, 'the frame draws without the texture');
  // An OPTIONAL KTX2 texture with a fallback image loads the fallback (as three does): nothing falls back.
  const optional = buildGlb({patch: (doc, add, view) => {
    pKtx2Required(doc, add, view);
    (doc.images as Json[]).push({bufferView: view(pngBytes(8, 8)), mimeType: 'image/png'});
    doc.textures = [{source: 1, extensions: {KHR_texture_basisu: {source: 0}}}];
    delete doc.extensionsRequired;
  }});
  const optionalReport = await check(optional);
  assert.equal(optionalReport.status, 'rendered'); assert.deepEqual(optionalReport.fallbacks, []);
});

test('a texture three did not request during the parse is loaded while its bytes and decoders are still alive: no false "could not be decoded"', async t => {
  // The REAL three image path, no texture plugin: GLTFLoader decodes with ImageBitmapLoader (fetch + createImageBitmap).
  // Node fetches blob: URLs (and fails on a revoked one, as a browser does) but has no createImageBitmap: only that
  // browser global gets a test stand-in, which insists on the fetched bytes.
  const scope = globalThis as {createImageBitmap?: unknown}, previous = scope.createImageBitmap;
  let decoded = 0;
  scope.createImageBitmap = async (blob: Blob) => {
    const head = new Uint8Array(await blob.arrayBuffer());
    if (head.length < 8 || head[1] !== 0x50) throw new Error('not the PNG bytes');
    decoded++; return {width: 8, height: 8, close() {}};
  };
  t.after(() => {scope.createImageBitmap = previous;});
  // An external image used only by a material slot three never reads (an unknown extension's *Texture): the texture is
  // first requested by loadModel's decode check, after the parse.
  const built = buildGlb({patch: doc => {
    doc.images = [{uri: 'only-in-an-extension.png'}]; doc.textures = [{source: 0}];
    (doc.materials as Json[])[0]!.extensions = {VENDOR_unread: {sparkleTexture: {index: 0}}};
    doc.extensionsUsed = [...(doc.extensionsUsed as string[] ?? []), 'VENDOR_unread'];
  }});
  const report = await check(built, {plugins: false, resources: {'only-in-an-extension.png': pngBytes(8, 8)}});
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.deepEqual(report.fallbacks, [], 'the external bytes were still reachable when the texture was asked for');
  assert.equal(decoded, 1, 'the image went through the real loader once');
});

test('no silent stalls: Draco without a Web Worker ends "unchecked" up front (no verdict) and every load settles; a stalled parse is a §2 Format refusal; an abort ends the load', async () => {
  /** `promise`, failed with a named error after `ms` (the test itself must never hang). */
  const within = <T>(promise: Promise<T>, ms: number, what: string): Promise<T> => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    return Promise.race([promise, new Promise<never>((_, reject) => {timer = setTimeout(() => reject(new Error(`${what} did not settle within ${ms} ms`)), ms);})])
      .finally(() => clearTimeout(timer));
  };
  const compressed = buildGlb({patch: doc => {
    ((doc.meshes as Json[])[0]!.primitives as Json[])[0]!.extensions = {KHR_draco_mesh_compression: {bufferView: 0, attributes: {POSITION: 0}}};
    doc.extensionsUsed = [...(doc.extensionsUsed as string[] ?? []), 'KHR_draco_mesh_compression'];
  }});
  // Before canDecodeDraco the first load failed on the decoder URL and the SECOND waited forever behind it.
  for (const attempt of [1, 2, 3]) {
    const report = await within(check(compressed), 10_000, `Draco load ${attempt}`);
    // An environment limit, not a §2 verdict: no refusal, the Format row as really checked (it passes), the other rows
    // not checked, and a loud warning that the page lists first.
    const reason = '1 default-scene primitive(s) use KHR_draco_mesh_compression (["Bridge"]), which cannot be decoded here: there is '
      + 'no Web Worker for the Draco decoder (a renderer-less Node load). This says nothing about the file; check it in the browser (the arview page or the '
      + 'capture CLI), which decodes Draco';
    assert.equal(report.status, 'unchecked');
    assert.deepEqual(report.refusal, []);
    assert.deepEqual(report.unchecked, [reason]);
    assert.equal(report.spec.format.pass, true, report.spec.format.detail.join(' | '));
    for (const row of SPEC_ROWS.filter(row => row !== 'format')) {
      assert.deepEqual(report.spec[row].detail, ['not checked: the Draco-compressed geometry cannot be decoded here (an environment limit, not a verdict on the file)']);
    }
    const lines = reportLines(report);
    assert.equal(lines[0]!.kind, 'warning');
    assert.ok(lines[0]!.text.startsWith(`NOT CHECKED HERE: ${reason}. No §2 verdict was reached on this file`), lines[0]!.text);
  }
  // A parse that never finishes (a buffer view that never arrives, as a dead decoder would) is abandoned at the deadline.
  const stall = (): GLTFLoaderPlugin => ({name: 'ArviewTestStall', loadBufferView: () => new Promise<ArrayBuffer>(() => {})}) as GLTFLoaderPlugin;
  const started = performance.now();
  const stalled = await within(checkModel(buildGlb().glb, {url: URL_BASE + 'stall.glb', renderer: null, plugins: [stubTextures, stall], parseTimeoutMs: 300}), 10_000, 'stalled parse');
  assert.equal(stalled.report.status, 'refused');
  assert.deepEqual(stalled.report.refusal, ["§2 Format: the GLB could not be parsed: three.js's GLTFLoader parse did not finish within 0.3 s and was abandoned "
    + '(a decoder or a buffer load stalled)']);
  assert.ok(performance.now() - started < 5_000, 'abandoned at the deadline');
  assert.match(stalled.report.rendered_sha256 ?? '', /^[0-9a-f]{64}$/);
  // The abort signal ends a stalled parse at once (the renderer's cancel).
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 50);
  await within(assert.rejects(checkModel(buildGlb().glb, {url: URL_BASE + 'stall.glb', renderer: null, plugins: [stubTextures, stall], signal: controller.signal}),
    {name: 'AbortError'}), 10_000, 'aborted parse');
});

test('a parse rejected with something that is not an Error keeps its reason (three\'s DRACOLoader rejects with its worker message object)', async () => {
  // DRACOLoader.js: worker._callbacks[id].reject(message), message = {type: 'error', id, error: '<the decoder\'s message>'}.
  const rejecting = (value: unknown) => (): GLTFLoaderPlugin => ({name: 'ArviewTestReject', loadMesh: () => Promise.reject(value)}) as GLTFLoaderPlugin;
  const cases: [unknown, string][] = [
    [{type: 'error', id: 3, error: 'Draco: invalid geometry type 7'}, 'Draco: invalid geometry type 7'],
    [{message: 'a plain record with a message'}, 'a plain record with a message'],
    ['a bare string', 'a bare string'],
    [{code: 42}, '{"code":42}'],
    [new TypeError('a real error'), 'a real error'],
  ];
  for (const [value, text] of cases) {
    const {report} = await checkModel(buildGlb().glb, {url: URL_BASE + 'reject.glb', renderer: null, plugins: [stubTextures, rejecting(value)]});
    assert.equal(report.status, 'refused');
    assert.deepEqual(report.refusal, [`§2 Format: the GLB could not be parsed: ${text}`]);
    assert.ok(!report.refusal[0]!.includes('[object Object]'));
  }
  assert.equal(describeError({}), 'Object with no message');
  assert.equal(describeError(null), 'null');
  assert.equal(describeError(Object.assign(new Error(''), {name: 'NamedOnly'})), 'NamedOnly');
  const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic;
  assert.equal(describeError(cyclic), 'Object with no message');
});

test('the refusal keeps checking: every row it can measure is in the report', async () => {
  const report = await check(buildGlb({parentMatrix: trans(0, 0.001, 0)}));
  assert.equal(report.status, 'refused');
  assert.deepEqual(report.refusal, ['§2 Transforms: 7 mesh node(s) with a non-identity world transform (tolerance 1e-06 absolute): '
    + '["Bridge","Brow_L","Brow_R","Lens_L","Lens_R","Temple_L","Temple_R"]']);
  assert.equal(report.lens_identification?.components.pass, true, 'lens identification ran');
  assert.equal(typeof (report.measurements.bridge_underside_mm as {y: number}).y, 'number', 'origin was measured (in world space)');
  assert.equal((report.measurements.bridge_underside_mm as {y: number}).y, 1, 'the 1 mm parent translation is measured, not baked');
});

test('a skinned mesh is refused (§2 Geometry)', async () => {
  const skinned = buildGlb({patch: (doc, add) => {
    const nodes = doc.nodes as Json[], meshes = doc.meshes as Json[];
    const primitive = (meshes[0]!.primitives as Json[])[0]!, attributes = primitive.attributes as Json;
    attributes.JOINTS_0 = add(Array.from({length: 8}, () => [0, 0, 0, 0]).flat(), 5121, 'VEC4');
    attributes.WEIGHTS_0 = add(Array.from({length: 8}, () => [1, 0, 0, 0]).flat(), 5126, 'VEC4');
    nodes.push({name: 'Joint'}); (doc.scenes as Json[])[0]!.nodes = [...((doc.scenes as Json[])[0]!.nodes as number[]), nodes.length - 1];
    doc.skins = [{joints: [nodes.length - 1]}]; nodes[0]!.skin = 0;
  }});
  const report = await check(skinned);
  assert.equal(report.status, 'refused');
  assert.ok(report.refusal.includes('§2 Geometry: Bridge: skinned mesh (the model must be static)'), report.refusal.join(' | '));
});

test('no lens anywhere is refused, with the lens identification in the report', async () => {
  const report = await check(buildGlb({roles: false, parts: unnamedParts(), materials: {frame: {name: 'Frame'}, lens: {name: 'Pane'}}}));
  assert.equal(report.status, 'refused');
  assert.match(report.refusal[0]!, /^§2 Components: no lens identified/);
  assert.deepEqual(report.lens_identification?.materials.map(entry => [entry.name, entry.class, entry.signal]), [['Frame', 'frame', 'none'], ['Pane', 'frame', 'none']]);
});

/* -------------------------------------------------------------------------------- rows that render with warnings */

test('millimetres: rescaled x0.001 before anything reads the scene, volume lengths included, loudly', async () => {
  const lens = {name: 'Lens clear', pbrMetallicRoughness: {baseColorFactor: [1, 1, 1, 1]},
    extensions: {KHR_materials_transmission: {transmissionFactor: 1}, KHR_materials_volume: {thicknessFactor: 1.5, attenuationDistance: 500, attenuationColor: [1, .9, .8]}}};
  const built = buildGlb({scale: 1.0, materials: {frame: {name: 'Frame acetate'}, lens}});
  const {report, scene} = await checkModel(built.glb, {url: URL_BASE + 'mm.glb', renderer: null, plugins: [stubTextures]});
  assert.equal(report.status, 'rendered');
  assert.equal(report.measurements.units_scale, 0.001);
  assert.equal(report.measurements.width_mm, 140, 'the measured width is the normalised one');
  assert.equal(report.measurements.authored_width_units, 140);
  assert.match(report.spec.units.detail[0]!, /^width 140\.0 units: millimetre-scaled \(expected metres, about 0\.140\)$/);
  assert.match(report.warnings.find(w => w.startsWith('UNITS:'))!, /millimetres .*rescaled it by ×0\.001 .*KHR_materials_volume thickness\/attenuation of \["Lens clear"\]/);
  assert.deepEqual(report.fallbacks.map(entry => entry.subsystem), ['units']);
  assert.ok(report.live_site_would_fail.includes('units are millimetres, not metres (the live site does not rescale)'));
  assert.equal((report.measurements.bridge_underside_mm as {y: number}).y, 0, 'origin measured after the rescale');
  assert.deepEqual(report.measurements.temple_reach_z_mm, {left: -140, right: -140});
  let maxAbs = 0, material: MeshPhysicalMaterial | null = null;
  scene!.traverse(object => {
    if (!(object instanceof Mesh)) return;
    const position = object.geometry.getAttribute('position');
    for (let i = 0; i < position.count; i++) maxAbs = Math.max(maxAbs, Math.abs(position.getX(i)), Math.abs(position.getZ(i)));
    if (object.material instanceof MeshPhysicalMaterial && object.material.name === 'Lens clear') material = object.material;
  });
  assert.ok(Math.abs(maxAbs - 0.140) < 1e-6, `geometry in metres (max |coordinate| ${maxAbs})`);
  const lensMaterial = material as MeshPhysicalMaterial | null;
  assert.ok(lensMaterial);
  assert.ok(Math.abs(lensMaterial.thickness - 0.0015) < 1e-9 && Math.abs(lensMaterial.attenuationDistance - 0.5) < 1e-9, 'volume lengths rescaled too');
  assert.ok(report.warnings.some(w => w.includes('thicknessFactor 1.5 looks like millimetres')));
  // The rows a rescaled file fails as authored say what the measurement after the rescale found (WP1 proof: the line
  // used to end "as follows" with nothing following when the rescaled model passed).
  for (const row of ['origin', 'orientation'] as const) {
    assert.deepEqual(report.spec[row], {pass: false, detail: ['not checked as authored: units are wrong; measured after the ×0.001 rescale: no defect '
      + '(the rescaled model passes this row)']});
  }
});

test('millimetres with an origin defect: the as-authored line counts the defects found after the rescale, which follow it', async () => {
  const report = await check(buildGlb({scale: 1.0, offsetMm: [0, 5, 0]}));
  assert.equal(report.status, 'rendered');
  assert.equal(report.measurements.units_scale, 0.001);
  assert.equal(report.spec.origin.pass, false);
  assert.equal(report.spec.origin.detail[0], 'not checked as authored: units are wrong; measured after the ×0.001 rescale: 1 defect (listed next)');
  assert.equal(report.spec.origin.detail.length, 2, report.spec.origin.detail.join(' | '));
  assert.match(report.spec.origin.detail[1]!, /^bridge underside is at y \+5\.00 mm/);
  assert.equal(report.spec.orientation.detail[0], 'not checked as authored: units are wrong; measured after the ×0.001 rescale: no defect (the rescaled model passes this row)');
});

test('centimetres: rescaled x0.01', async () => {
  const report = await check(buildGlb({scale: 0.1}));
  assert.equal(report.measurements.units_scale, 0.01); assert.equal(report.measurements.width_mm, 140);
  assert.match(report.warnings[0]!, /^UNITS: the model is in centimetres \(X span 14\.00 units\)/);
});

test('every row that renders anyway says why, and every fallback is a warning too', async () => {
  // Units warning band.
  let report = await check(buildGlb({preMm: diag(0.75, 0.75, 0.75)}));
  assert.ok(report.warnings.some(w => /overall width 105\.0 mm is outside the usual 115-170 mm/.test(w)));
  // Origin within tolerance but not exact.
  report = await check(buildGlb({offsetMm: [0, 0.5, 0]}));
  assert.ok(failed(report).size === 0 && report.warnings.includes('bridge underside is at y +0.50 mm (allowed, but not exact)'));
  // Asymmetric temples: the front decides the x of the origin.
  report = await check(buildGlb({parts: replaceBox('Temple_L', [-75, -71, 2, 8, -140, -30])}));
  assert.equal(failed(report).size, 0); assert.ok(report.warnings.some(w => w.includes('the whole model is centred')));
  // A one-sided ornament in the front band: the lenses decide x; the front's offset is a warning, never a failure.
  report = await check(buildGlb({parts: withPart('Ornament', [70, 84, 0, 4, -2, 2])}));
  assert.equal(failed(report).size, 0, errors(report).join(' | ')); assert.equal(report.measurements.origin_x_judged_on, 'lenses');
  assert.ok(report.warnings.includes('the front region is centred at x +7.00 mm and the whole model is centred at x +7.00 mm although the '
    + 'lenses are centred at x +0.00 mm (asymmetric temples or ornaments?)'), report.warnings.join(' | '));
  // Origin off by more than 1 mm, or unverifiable: the row fails AND a loud warning says so (glasses3d/plan/archive/20261019/WP1_DESIGN.md §1).
  report = await check(buildGlb({offsetMm: [0, 5, 0]}));
  assert.ok(report.warnings.includes('ORIGIN: bridge underside is at y +5.00 mm (must be 0 +/- 1.0 mm). The delivered file violates §2 Origin; '
    + 'arview never moves a model (it is drawn where it was authored).'), report.warnings.join(' | '));
  report = await check(buildGlb({parts: without(['Bridge'])}));
  assert.ok(report.warnings.some(w => w.startsWith('ORIGIN: origin unverifiable: no frame or lens surface crosses x = 0')), report.warnings.join(' | '));
  report = await check(buildGlb({parts: leftLensOnly()}));
  assert.equal(report.measurements.origin_x_judged_on, 'front');
  assert.ok(report.warnings.some(w => /^origin x judged on the front region \(z >= -23\.0 mm\): no lens geometry spans x = 0$/.test(w)), report.warnings.join(' | '));
  // Orientation: a box temple with no vertex in the pitch slab is measured on plane sections; a missing one is not measured.
  report = await check(buildGlb({parts: without(['Temple_L'])}));
  assert.ok(errors(report).some(e => e.includes('temple right missing')));
  // Transforms: export noise within 1e-6 is baked to exact identity.
  report = await check(buildGlb({parentMatrix: trans(0, 1e-7, 0)}));
  assert.equal(failed(report).size, 0); assert.match(report.spec.transforms.detail.join(' '), /7 mesh\(es\) within 0\.000001 of identity were baked/);
  // Geometry: a LINES primitive is removed from the scene and reported; a short index list is truncated and reported.
  const lines = buildGlb({mode: 1, target: 'Brow_L'});
  const checked = await checkModel(lines.glb, {url: URL_BASE + 'lines.glb', renderer: null, plugins: [stubTextures]});
  let strays = 0; checked.scene!.traverse(object => {if (object instanceof Line) strays++;});
  assert.equal(strays, 0, 'the LINES primitive is not drawn');
  assert.deepEqual(checked.report.fallbacks.map(f => f.detail), ['"Brow_L" is a LINES primitive (not triangles): it is not drawn (§2 Geometry).']);
  report = await check(buildGlb({indexCount: 35, target: 'Brow_L'}));
  assert.match(report.fallbacks[0]!.detail, /"Brow_L" has 35 indices, not a multiple of 3: the trailing 2 are dropped/);
  report = await check(buildGlb({animation: true}));
  assert.match(report.fallbacks[0]!.detail, /1 animation\(s\); arview draws the static pose/);
  report = await check(buildGlb({mode: 5}));
  assert.ok(report.warnings.some(w => /^Bridge: \d+ degenerate \(zero-area\) triangles$/.test(w)), report.warnings.join(' | '));
  // Components: a material shared by lens and frame is cloned per class (both render) and the row fails.
  const shared = buildGlb({parts: {...defaultParts(), Bridge: {box: [-9, 9, 0, 4, -2, 2], material: 'lens', role: 'frame'}}});
  const sharedChecked = await checkModel(shared.glb, {url: URL_BASE + 'shared.glb', renderer: null, plugins: [stubTextures]});
  const classes = new Map<string, string | undefined>();
  sharedChecked.scene!.traverse(object => {if (object instanceof Mesh) classes.set(object.name, materialClassOf(object.material as Material));});
  assert.equal(classes.get('Bridge'), 'frame'); assert.equal(classes.get('Lens_L'), 'lens');
  assert.ok(sharedChecked.report.warnings.some(w => w.includes('cloned per class so both render')));
  // Components: the last resort is warned.
  report = await check(buildGlb({roles: false, parts: unnamedParts(), materialNames: {frame: 'Frame', lens: 'Clear'}}));
  assert.equal(report.status, 'rendered');
  assert.ok(report.warnings.some(w => w.includes('identified as lens only by transmissionFactor=1 (last resort')), report.warnings.join(' | '));
  // Format: an external buffer loads from the very bytes fetched, with a warning.
  const external = buildGlb({externalBuffer: 'external.bin'});
  report = await check(external);
  assert.equal(report.status, 'rendered'); assert.ok(report.warnings.includes('not self-contained: external resources ["external.bin"]'));
  assert.deepEqual((report.measurements.external_resources as {uri: string; bytes: number}[]).map(r => [r.uri, r.bytes]), [['external.bin', external.external!.length]]);
  // Format: a data: URI buffer is decoded without three's FileLoader too (no ProgressEvent in Node; see the top).
  report = await check(buildGlb({dataUriBuffer: true}));
  assert.equal(report.status, 'rendered', report.refusal.join(' | ')); assert.ok(!report.warnings.some(w => w.startsWith('not self-contained')));
  // Format: unknown required extensions load with a warning.
  const {doc, bin} = splitGlb(buildGlb().glb);
  report = await check(joinGlb({...doc, extensionsRequired: ['ACME_magic'], extensionsUsed: [...(doc.extensionsUsed as string[]), 'ACME_magic']}, bin));
  assert.equal(report.status, 'rendered'); assert.ok(report.warnings.includes('extensionsRequired lists extensions three.js does not load: ["ACME_magic"]'));
  // Format: a handed-over digest that differs is a warning; the report binds to the loaded bytes.
  report = await check(buildGlb(), {handoverSha256: 'a'.repeat(64)});
  assert.ok(report.warnings.some(w => w.includes(`differs from the bytes actually loaded (${report.rendered_sha256})`)));
  // Every fallback is a warning (the definition's, the units', the geometry's ...).
  for (const entry of CASES) {
    const caseReportValue = await caseReport(entry);
    for (const fallback of caseReportValue.fallbacks) assert.ok(caseReportValue.warnings.includes(fallback.detail), `${entry.name}: ${fallback.detail}`);
  }
});

test('textures: sizes are read from the bytes; above 4096 px a warning, above 2048 the live site; an undecodable texture is a fallback', async () => {
  assert.deepEqual(imageSize(pngBytes(4100, 8)), [4100, 8]);
  let report = await check(buildGlb({texturePx: [4100, 8]}));
  assert.ok(report.warnings.some(w => w.includes('image 0 is 4100x8 (> 4096')));
  assert.ok(report.live_site_would_fail.some(w => w.includes('image 0 is 4100x8 (live limit 2048)')));
  assert.ok(report.warnings.some(w => w.includes('cannot map')), 'the boxes carry no TEXCOORD_0');
  assert.deepEqual(report.measurements.textures, [{image: 0, size: [4100, 8]}]);
  // Node cannot decode images: without the test stub the texture is null and its material draws without it, reported.
  const quiet = console.error; console.error = () => {};
  try {report = await check(buildGlb({texturePx: [16, 16]}), {plugins: false});}
  finally {console.error = quiet;}
  assert.equal(report.status, 'rendered');
  assert.deepEqual(report.fallbacks.map(f => f.subsystem), ['textures']);
  assert.match(report.fallbacks[0]!.detail, /^1 texture\(s\) could not be decoded and their materials draw without them: texture 0\.$/);
});

test('live-site notes: compression, triangles, file size, non-opaque frame, temples; arview warns above 300k triangles', async t => {
  // Draco declared, no primitive compressed. In the browser GLTFLoader preloads the decoder from /decoders/draco/; Node
  // has no Web Worker to run it (load.ts canDecodeDraco), so the unused declaration is kept from three and the decoder
  // never starts (a spy, not a stand-in: it must stay uncalled).
  const preload = t.mock.method(DRACOLoader.prototype, 'preload');
  const {doc, bin} = splitGlb(buildGlb().glb);
  let report = await check(joinGlb({...doc, extensionsUsed: [...(doc.extensionsUsed as string[]), 'KHR_draco_mesh_compression']}, bin));
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.equal(preload.mock.callCount(), 0, 'Node never starts the Draco decoder');
  assert.ok(report.live_site_would_fail.includes('uses KHR_draco_mesh_compression (live site loads plain glTF only)'));
  assert.ok(report.spec.format.detail.includes('compressed with KHR_draco_mesh_compression: not decoded here (no Web Worker for the Draco decoder: a renderer-less Node load)'),
    report.spec.format.detail.join(' | '));
  // 300 003 triangles: one repeated valid triangle.
  const heavy = buildGlb({patch: (patched, add) => {
    const indices = new Array(300_003 * 3); for (let i = 0; i < indices.length; i++) indices[i] = [0, 1, 2][i % 3]!;
    ((patched.meshes as Json[])[0]!.primitives as Json[])[0]!.indices = add(indices, 5125, 'SCALAR');
  }});
  report = await check(heavy);
  assert.equal(report.measurements.triangles, 300_003 + 72);
  assert.ok(report.warnings.some(w => w.startsWith(`${300_003 + 72} triangles (> 300000; heavy for real-time AR)`)));
  assert.ok(report.live_site_would_fail.some(w => w.startsWith(`${300_003 + 72} triangles (live limit 100000)`)));
  // Over 8 MB.
  const big = buildGlb({patch: (_, __, view) => {view(new Uint8Array(8 * 1024 * 1024 + 16));}});
  report = await check(big);
  assert.ok(report.live_site_would_fail.some(w => /^file is 8\.0 MB \(live limit 8 MB\)$/.test(w)), report.live_site_would_fail.join(' | '));
  // Crystal frame, crystal temples: not opaque, and no opaque temple beyond 45 mm.
  report = await check(buildGlb({materials: {frame: {name: 'Crystal acetate', extensions: {KHR_materials_transmission: {transmissionFactor: 1}}}, lens: {name: 'Lens tint'}}}));
  assert.equal(failed(report).size, 0);
  assert.deepEqual(report.live_site_would_fail, ["frame material 'Crystal acetate' is not fully opaque", 'no opaque part']);
  report = await check(buildGlb());
  assert.deepEqual(report.live_site_would_fail, [], 'the plain synthetic glasses would load on the live site');
});

test('every GLTFLoader arview builds has the Draco, meshopt and (with a GPU renderer) KTX2 decoders', () => {
  const decoders = createGltfLoader(null);
  const loader = decoders.loader as unknown as {dracoLoader: unknown; meshoptDecoder: unknown; ktx2Loader: unknown};
  assert.ok(loader.dracoLoader, 'Draco'); assert.ok(loader.meshoptDecoder, 'meshopt');
  assert.equal(loader.ktx2Loader, null, 'KTX2 needs the WebGLRenderer, which exists before parsing in the renderer');
  assert.equal(decoders.ktx2, false);
  decoders.dispose();
});

/* ------------------------------------------------------------------------------------ r9-style legacy crystal */

test('r9-style legacy crystal: lenses by legacy part_role, crystal is frame, renders with the origin row failed', async () => {
  const built = r9Like();
  const {report, scene} = await checkModel(built.glb, {url: URL_BASE + 'candidate.glb', renderer: null, plugins: [stubTextures]});
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  assert.deepEqual(sorted(failed(report)), ['origin']);
  assert.match(report.spec.origin.detail.join(' '), /bridge underside is at y \+10\.23 mm \(must be 0 \+\/- 1\.0 mm\)/);
  // glasses3d/plan/archive/20261019/WP1_DESIGN.md §1: render anyway, LOUD warning (the webcam page shows the warnings, not the rows).
  assert.deepEqual(report.warnings.filter(w => w.startsWith('ORIGIN:')), ['ORIGIN: bridge underside is at y +10.23 mm (must be 0 +/- 1.0 mm). '
    + 'The delivered file violates §2 Origin; arview never moves a model (it is drawn where it was authored).']);
  assert.deepEqual(report.lens_identification!.materials.map(entry => [entry.name, entry.class, entry.signal, entry.value]), [
    ['Crystal acetate', 'frame', 'part_role_legacy', 'frame'],
    ['Warm brown optical tint', 'lens', 'part_role_legacy', 'lens_left'],
    ['Champagne gold', 'frame', 'part_role_legacy', 'hardware']]);
  assert.equal(report.lens_identification!.last_resort_used, false);
  const crystal: Material[] = [];
  scene!.traverse(object => {if (object instanceof Mesh && object.name === 'Crystal_front') crystal.push(object.material as Material);});
  assert.equal(crystal.length, 1);
  assert.ok(isTranslucentFrameMaterial(crystal[0]!), 'the crystal draws as a translucent frame (twins), never as a lens');
  assert.ok(report.live_site_would_fail.includes("frame material 'Crystal acetate' is not fully opaque"));
  assert.deepEqual(report.measurements.temple_reach_z_mm, {left: -147.24, right: -147.24});
});

/* ----------------------------------------------------------------------------- agreement with spec.py (Python) */

const LENSES_ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const PYTHON = [join(LENSES_ROOT, 'glasses3d/.venv/Scripts/python.exe'), join(LENSES_ROOT, 'glasses3d/.venv/bin/python')].find(path => existsSync(path)) ?? null;
const VALIDATE = ['import json, sys', 'from glasses3d.g3d_host.spec import validate_spec', 'out = {}', 'for p in sys.argv[1:]:',
  '    rep = validate_spec(p)', '    out[p] = {"rows": {r: v["pass"] for r, v in rep["rows"].items()}, "errors": rep["errors"],',
  '              "lens_primitives": rep["measurements"].get("lens_primitives")}',
  'print(json.dumps(out))'].join('\n');

type PythonResult = {rows: Record<SpecRowName, boolean>; errors: string[]; lens_primitives?: number | null};
function pythonRows(paths: string[]): Record<string, PythonResult> {
  const result = spawnSync(PYTHON!, ['-c', VALIDATE, ...paths], {cwd: LENSES_ROOT, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, timeout: 120_000});
  if (result.status !== 0) throw new Error(`spec.py failed (${result.status}): ${result.stderr}`);
  return JSON.parse(result.stdout) as Record<string, PythonResult>;
}

test('arview and the Python validator agree on every shared case they can both check', async t => {
  if (!PYTHON) {t.skip('glasses3d/.venv Python is not available: agreement with spec.py was NOT checked'); return;}
  const directory = mkdtempSync(join(tmpdir(), 'arview-spec-'));
  t.after(() => rmSync(directory, {recursive: true, force: true}));
  const paths = new Map<string, string>();
  CASES.forEach((entry, index) => {
    const built = entry.build(), path = join(directory, `case${index}.glb`);
    writeFileSync(path, new Uint8Array(built instanceof ArrayBuffer ? built : built.glb));
    if (!(built instanceof ArrayBuffer) && built.external) writeFileSync(join(directory, 'external.bin'), built.external);
    paths.set(entry.name, path);
  });
  const python = pythonRows([...paths.values()]);
  const disagreements: string[] = [];
  for (const entry of CASES) {
    const report = await caseReport(entry), theirs = python[paths.get(entry.name)!]!;
    for (const row of entry.compare ?? SPEC_ROWS) {
      if (report.spec[row].pass !== theirs.rows[row]) {
        disagreements.push(`${entry.name} / ${row}: arview ${report.spec[row].pass ? 'pass' : 'FAIL'} (${report.spec[row].detail.join('; ')}), `
          + `spec.py ${theirs.rows[row] ? 'pass' : 'FAIL'} (${theirs.errors.filter(e => e.startsWith(row)).join('; ')})`);
      }
    }
    // The lens primitive count, wherever arview got as far as identifying the lenses.
    const ours = report.measurements.lens_primitives;
    if (ours !== undefined && theirs.lens_primitives !== undefined && ours !== theirs.lens_primitives) {
      disagreements.push(`${entry.name} / lens_primitives: arview ${String(ours)}, spec.py ${String(theirs.lens_primitives)}`);
    }
  }
  assert.deepEqual(disagreements, []);
});

const REAL_FILES = [
  {name: 'ar_v4 crystal Tom Ford r9', path: 'ar_v4/model_studio/data/jobs/d92b404c-f2c7-483e-ae9c-aa8bcf1b870c/agent/operations/8c315e48f6b84cbc897cf397d772d6f0/candidate.glb'},
  {name: 'FT1119 quality-001', path: 'automation/data/blender_agent/studio/jobs/3b8a1c7ae8114868b49b298b681f1b29/original/model.glb'},
  {name: 'Prada delivery', path: 'automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb'},
];

test('on the real proof models (read-only, when present) arview renders all three and agrees with spec.py row by row', async t => {
  const present = REAL_FILES.filter(file => existsSync(join(LENSES_ROOT, file.path)));
  if (!present.length) {t.skip('none of the WP1 proof GLBs is on this machine: NOT checked'); return;}
  if (present.length < REAL_FILES.length) t.diagnostic(`missing (not checked): ${REAL_FILES.filter(f => !present.includes(f)).map(f => f.name).join(', ')}`);
  const python = PYTHON ? pythonRows(present.map(file => join(LENSES_ROOT, file.path))) : null;
  if (!python) t.diagnostic('glasses3d/.venv Python is not available: agreement with spec.py was NOT checked');
  for (const file of present) {
    const bytes = await readFile(join(LENSES_ROOT, file.path));
    const report = await check(new Uint8Array(bytes).buffer, {file: 'real.glb'});
    assert.equal(report.status, 'rendered', `${file.name}: ${report.refusal.join(' | ')}`);
    if (file.name.includes('r9')) {
      assert.deepEqual(sorted(failed(report)), ['origin'], `${file.name}: ${errors(report).join(' | ')}`);
      const y = (report.measurements.bridge_underside_mm as {y: number}).y;
      assert.ok(Math.abs(y - 10.23) < 0.01, `r9 bridge underside y ${y} mm (PROGRESS.md: +10.23)`);
      assert.ok(report.warnings.some(w => /^ORIGIN: bridge underside is at y \+10\.2\d mm \(must be 0 \+\/- 1\.0 mm\)\. /.test(w)),
        `the origin defect is a loud warning: ${report.warnings.join(' | ')}`);
    } else assert.equal(failed(report).size, 0, `${file.name}: ${errors(report).join(' | ')}`);
    if (python) {
      const theirs = python[join(LENSES_ROOT, file.path)]!;
      for (const row of SPEC_ROWS) assert.equal(report.spec[row].pass, theirs.rows[row], `${file.name} / ${row}: ${report.spec[row].detail.join('; ')} vs ${theirs.errors.join('; ')}`);
    }
  }
});

test('a fallback-free load and a refused load both carry the SHA-256 of the exact bytes', async () => {
  const built = buildGlb();
  const report = await check(built);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', built.glb)), v => v.toString(16).padStart(2, '0')).join('');
  assert.equal(report.rendered_sha256, digest);
  const refused = await check(buildGlb({nan: true, target: 'Bridge'}));
  assert.equal(refused.status, 'refused'); assert.match(refused.rendered_sha256 ?? '', /^[0-9a-f]{64}$/);
  // The report builder used by a refusal is the same shape as a rendered one.
  assert.deepEqual(Object.keys(refused).sort(), Object.keys(report).sort());
  const fresh = new ModelReportBuilder('u', 'd', ['definition note']).snapshot();
  assert.deepEqual(fresh.fallbacks, [{subsystem: 'definition', detail: 'definition note', stage: 'load'}]);
  assert.deepEqual(fresh.warnings, ['definition note']);
});

test('loadModel resolves external URIs against the model URL directory', async () => {
  const built = buildGlb({externalBuffer: 'sub/external.bin'});
  const requested: string[] = [];
  const report = new ModelReportBuilder('x', 'x');
  await loadModel(built.glb, report, {url: 'http://127.0.0.1:9/a/b/model.glb', renderer: null, plugins: [stubTextures],
    fetchResource: async url => {requested.push(url); return built.external!.slice().buffer;}});
  assert.deepEqual(requested, ['http://127.0.0.1:9/a/b/sub/external.bin']);
});
