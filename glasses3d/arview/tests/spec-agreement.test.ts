/** arview against the Python validator on the SAME bytes (WP1_DESIGN.md §1: `glasses3d/g3d_host/spec.py` is the single
 *  source of truth for the PLAN §2 rows, and arview's runtime checks must agree with it on shared fixtures).
 *
 *  The fixtures are built by the PYTHON fabricator (`glasses3d/tests/glbfab.py`, through tests/spec_agreement_cases.py,
 *  which also runs validate_spec on each), plus the four real proof GLBs (read-only; validated through
 *  `python -m glasses3d.g3d_host.spec --full`). Every case must give identical pass/fail on all seven rows, and the
 *  measured numbers both sides report must match (and, for the cases in SAME_REASON, the failure's reason). Where the two
 *  are MEANT to differ, the difference is listed below with the reason and asserted exactly (INTENDED: refusals before
 *  parsing and three spec.py gaps; RESCALED: millimetre/centimetre files render in arview, measured against their metre
 *  twins; COMPRESSED_DOWNGRADE: spec.py does not decode Draco, and Node arview ends such a load 'unchecked'): if either
 *  side changes, this test says so.
 *
 *  Outputs (the case GLBs, both verdicts) go to glasses3d/data/arview/spec-agreement/ (git-ignored), overwritten per run.
 *  Without glasses3d/.venv the test FAILS (set ARVIEW_SKIP_SPEC_AGREEMENT=1 to skip it knowingly).
 *
 *  Rows alone could hide a false refusal (a row fails on both sides, but arview refuses a model §1 says must render), so
 *  arview's STATUS is asserted on every case too: rendered, unless the case is in ARVIEW_REFUSES (a §1-table refusal,
 *  with the refused rows) or INTENDED. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync, mkdirSync, renameSync, rmSync, writeFileSync} from 'node:fs';
import {readFile} from 'node:fs/promises';
import {basename, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Texture} from 'three';
import type {GLTFLoaderPlugin} from 'three/addons/loaders/GLTFLoader.js';
import {checkModel} from '../src/model/check.ts';
import {SPEC_ROWS} from '../src/model/report.ts';
import type {ModelReport, SpecRowName} from '../src/model/report.ts';

// Node lacks `self`, which GLTFLoader reads before decoding an image (model-load.test.ts explains): a test-only stand-in.
// No ProgressEvent stand-in: load.ts keeps three's FileLoader (which needs it) off the buffer path.
(globalThis as {self?: unknown}).self ??= globalThis;

const LENSES_ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const PYTHON = [join(LENSES_ROOT, 'glasses3d/.venv/Scripts/python.exe'), join(LENSES_ROOT, 'glasses3d/.venv/bin/python')].find(path => existsSync(path)) ?? null;
const SCRIPT = fileURLToPath(new URL('./spec_agreement_cases.py', import.meta.url));
const OUT = join(LENSES_ROOT, 'glasses3d/data/arview/spec-agreement');
const SKIP = process.env.ARVIEW_SKIP_SPEC_AGREEMENT === '1';
const REAL = [
  {name: 'FT1119 quality-001', path: 'automation/data/blender_agent/studio/jobs/3b8a1c7ae8114868b49b298b681f1b29/original/model.glb'},
  {name: 'Prada delivery', path: 'automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb'},
  {name: 'INVU final', path: 'automation/data/blender_agent/studio/jobs/c8e81c7214a646cfa07e082c59128f10/agent/ar/final-invu-ia22403b-dd80df73/model.glb'},
  {name: 'ar_v4 crystal Tom Ford r9', path: 'ar_v4/model_studio/data/jobs/d92b404c-f2c7-483e-ae9c-aa8bcf1b870c/agent/operations/8c315e48f6b84cbc897cf397d772d6f0/candidate.glb'},
];

type Verdict = 'pass' | 'fail';
interface PythonReport {ok: boolean; rows: Record<SpecRowName, boolean>; errors: string[]; warnings: string[]; measurements: Record<string, unknown>}
interface PythonCase {
  id: string; file?: string; decoded_file?: string; metre_file?: string; python?: PythonReport; python_decoded?: PythonReport;
  /** spec.py on the same build at metres (every millimetre/centimetre case), and, where spec.py does not measure that
   *  twin's origin and orientation itself, its own sections with the units gate bypassed. */
  python_metres?: PythonReport; python_metres_ungated?: PythonReport; error?: string;
}

/** Where arview and spec.py are MEANT to differ, row by row, and why. Nothing else may differ. */
interface Intended {readonly rows: Partial<Record<SpecRowName, {arview: Verdict; python: Verdict}>>; readonly arviewStatus: 'rendered' | 'refused'; readonly why: string}
const NOT_CHECKED_AFTER_REFUSAL = (python: Partial<Record<SpecRowName, Verdict>>): Intended['rows'] =>
  Object.fromEntries(Object.entries(python).map(([row, verdict]) => [row, {arview: 'fail', python: verdict}]));
const REFUSED_BEFORE_PARSING = 'arview refuses at the Format row before three parses the file (WP1_DESIGN.md §1), so it cannot check the other rows: each '
  + 'fails as "not checked"; spec.py fails Format and still checks the rest.';
const INTENDED: Readonly<Record<string, Intended>> = {
  'review-absolute-http': {arviewStatus: 'refused', why: `Absolute URLs are refused without any request. ${REFUSED_BEFORE_PARSING}`,
    rows: NOT_CHECKED_AFTER_REFUSAL({units: 'pass', origin: 'pass', orientation: 'pass', transforms: 'pass', geometry: 'pass', components: 'pass'})},
  'review-scene-null': {arviewStatus: 'refused', why: `An explicit null scene is invalid, never the default 0. ${REFUSED_BEFORE_PARSING}`,
    rows: {transforms: {arview: 'fail', python: 'pass'}}},
  'format-missing-image': {arviewStatus: 'refused', why: `An external image is missing next to the file. ${REFUSED_BEFORE_PARSING}`,
    rows: NOT_CHECKED_AFTER_REFUSAL({units: 'pass', origin: 'pass', orientation: 'pass', transforms: 'pass', geometry: 'pass', components: 'pass'})},
  'format-absolute-uri-existing-file': {arviewStatus: 'refused', why: 'An unused image is named by an absolute URI to a file that exists: '
    + `glTF resolves it as a URL, never next to the file, so both refuse it as missing (WP1 review E/F). ${REFUSED_BEFORE_PARSING}`,
    rows: NOT_CHECKED_AFTER_REFUSAL({units: 'pass', origin: 'pass', orientation: 'pass', transforms: 'pass', geometry: 'pass', components: 'pass'})},
  'format-no-scenes': {arviewStatus: 'refused', why: `The document has no scenes (viewers show nothing; glb.py falls back to the root nodes). ${REFUSED_BEFORE_PARSING}`,
    rows: NOT_CHECKED_AFTER_REFUSAL({units: 'pass', origin: 'pass', orientation: 'pass', transforms: 'pass', geometry: 'pass', components: 'pass'})},
  'format-empty-scene': {arviewStatus: 'refused', why: `The default scene has no nodes. ${REFUSED_BEFORE_PARSING} spec.py fails every row except Transforms (no primitive `
    + 'to check), so only Transforms differs.', rows: {transforms: {arview: 'fail', python: 'pass'}}},
  // OPEN on the spec.py side: glTF requires asset.version and three's GLTFLoader refuses a file without "2.x"; spec.py
  // does not read asset.version at all, so it passes this file that no glTF viewer loads.
  // OPEN on the spec.py side (closing it needs a spec.py change beyond main()): a used texture whose image source cannot
  // be loaded (three would throw inside the material) is left out by arview, which renders the material without it (a
  // 'textures' fallback) and fails the Format row; spec.py checks texture indices but not their image sources, so it
  // passes Format. Only the Format row differs; arview renders.
  'format-texture-source-missing': {arviewStatus: 'rendered', why: "The frame material's texture names image 9, which does not exist: arview fails "
    + 'Format and renders without the texture; spec.py does not check texture image sources.', rows: {format: {arview: 'fail', python: 'pass'}}},
  'format-image-without-source': {arviewStatus: 'rendered', why: "The frame material's texture names image 0, which has neither a uri nor a bufferView: "
    + 'arview fails Format and renders without the texture; spec.py reads no bytes for that image and passes Format.', rows: {format: {arview: 'fail', python: 'pass'}}},
  'format-no-asset-version': {arviewStatus: 'refused', why: `asset.version is missing (three refuses the file). ${REFUSED_BEFORE_PARSING} spec.py does not check `
    + 'asset.version, so it passes Format too.', rows: {format: {arview: 'fail', python: 'pass'},
    ...NOT_CHECKED_AFTER_REFUSAL({units: 'pass', origin: 'pass', orientation: 'pass', transforms: 'pass', geometry: 'pass', components: 'pass'})}},
};
/** The intended Units difference (WP1_DESIGN.md §1, the Units row): on a millimetre or centimetre file both sides fail
 *  Units (and Origin and Orientation: spec.py "not checked", arview "not checked as authored" plus its measurement after the
 *  rescale), but arview RESCALES AND RENDERS, loudly, while spec.py's verdict is simply not ok. Exactly these files are
 *  rescaled (the 70-unit file is outside spec.py's 95-200 mm window but inside arview's 60-250 rescale range: both still
 *  fail the row). What arview measures AFTER the rescale (and shows on the page) is compared with spec.py on the case's
 *  metre twin (the same build at glTF's metres): every measured number, the shared warnings, and the origin/orientation
 *  failures, so a rescale defect cannot hide behind two failed rows. */
const RESCALED: Readonly<Record<string, 0.001 | 0.01>> = {
  'units-millimetres': 0.001, 'units-centimetres': 0.01, 'units-70-units-mm-band': 0.001, 'units-millimetres-offset-y5': 0.001,
  'compressed-all-millimetres (decoded twin)': 0.001, 'compressed-screw-millimetres (decoded twin)': 0.001,
};
/** Metre twins spec.py does not measure itself (validate_spec stops at its units window): the comparison uses spec.py's
 *  own sections with the units gate bypassed (spec_agreement_cases.py ungated_report). Nothing else may need that. */
const UNGATED_TWINS: Readonly<Record<string, string>> = {
  'units-70-units-mm-band': 'its metre twin is 70 mm wide, outside spec.py\'s 95-200 mm units window, so validate_spec does not measure origin or orientation',
};
/** Compressed cases: the fabricator flags KHR_draco_mesh_compression on plain accessors (the "Draco stream" is the index
 *  bytes). spec.py does not decode Draco: it bounds the flagged primitives by POSITION min/max and downgrades a failure to an
 *  "unverified" warning where a box could change it. arview DECODES (in the browser); in Node there is no Web Worker for the
 *  decoder, so arview ends the load UP FRONT with status 'unchecked' (load.ts canDecodeDraco; no stand-in, and every load
 *  settles): an environment limit, not a verdict. There is no refusal, the Format row keeps what was really checked (and
 *  must equal spec.py's Format verdict on the flagged file), and the other rows are "not checked", asserted below
 *  (INTENDED: spec.py's bounds-only verdict on those rows is not compared there). What arview decides after a decode is what it decides on the case's __decoded twin (the same
 *  build without the flag), which must agree with spec.py on that twin exactly. Where spec.py's bounds-only verdict on the
 *  flagged file differs from the decoded truth, it is listed here. (arview's REAL Draco decode is compared with spec.py
 *  nowhere in Node: that needs the browser, the capture CLI's preflight.) */
const COMPRESSED_DOWNGRADE: Readonly<Record<string, {row: SpecRowName; warning: string}>> = {
  // The compressed bridge is the only surface crossing x = 0: bounds-only, the origin is unverifiable (downgraded to a
  // warning because the bridge's box reaches the front band); decoded, its underside sits at y +10 mm and fails.
  'compressed-bridge-y+10': {row: 'origin', warning: 'origin unverified: origin unverifiable'},
};

/** The same REASON on both sides: a failed row's detail must contain this text in arview's report and in spec.py's errors
 *  (the agreement on pass/fail alone could hide two different defects). */
const SAME_REASON: Readonly<Record<string, readonly [SpecRowName, string]>> = {
  'orientation-z-up': ['orientation', "Y is not the lenses' middle axis"],
  'orientation-z-up-no-temples': ['orientation', "Y is not the lenses' middle axis"],
  'orientation-hinge-stubs-upside-down': ['orientation', 'not +Y up: the temple roots'],
  'orientation-both-temples-missing': ['orientation', 'temple right missing'],
  'orientation-roll+25': ['orientation', 'rolled: the left lens half'],
  'orientation-roll-3.1mm-fails': ['orientation', "rolled: the left lens half's centre (+X) is +3.1 mm higher"],
  'orientation-pitch+25': ['orientation', 'pitched: the left temple'],
  'orientation-pitch-15.5-fails': ['orientation', 'slopes -15.5 deg'],
  'orientation-pitch-minus-15.5-fails': ['orientation', 'slopes +15.5 deg'],
  'orientation-faces-backwards': ['orientation', 'the model faces backwards'],
  'orientation-upside-down': ['orientation', 'not +Y up: the temple roots'],
  'orientation-mirrored-y': ['orientation', 'not +Y up: the temple roots'],
  'orientation-roots-below-lens-centre': ['orientation', 'not +Y up: the temple roots'],
  'orientation-temple-left-missing': ['orientation', 'temple right missing'],
  'orientation-deep-wrap-temple-left-missing': ['orientation', 'temple right missing'],
  'orientation-reach-59.5-fails': ['orientation', 'it reaches only z -59.5 mm'],
  'orientation-narrow-temples': ['orientation', 'temple right missing'],
  'orientation-lenses-behind-front': ['orientation', 'behind the front'],
  'orientation-lenses-just-behind-front-30pct': ['orientation', 'lenses sit at z -42.5 mm, behind the front'],
  'transforms-parent-upside-down': ['orientation', 'not +Y up: the temple roots'],
  'transforms-trs-roll+25': ['orientation', 'rolled: the left lens half'],
  'transforms-parent-pitch+25': ['orientation', 'pitched: the left temple'],
  'origin-y+5': ['origin', 'bridge underside is at y +5.00 mm'],
  'origin-r9-like-y+10.23': ['origin', 'bridge underside is at y +10.23 mm'],
  'origin-x+3': ['origin', 'the lenses are centred at x +3.00 mm'],
  'origin-lenses-shifted-x+3': ['origin', 'the lenses are centred at x +3.00 mm'],
  'origin-left-lens-only-x+3': ['origin', 'mm) is centred at x +3.00 mm'],
  'origin-z+5': ['origin', "outside the bridge underside's depth"],
  'origin-no-bridge': ['origin', 'origin unverifiable: no frame or lens surface crosses x = 0'],
  'origin-split-bridge': ['origin', 'origin unverifiable'],
  'origin-shield-moved-y-15': ['origin', 'bridge underside is at y -15.00 mm'],
  'geometry-index-i16-bridge': ['origin', '(1 primitive(s) excluded after geometry failures)'],
  'geometry-index-i16': ['geometry', 'Brow_L: indices componentType 5122 is not valid glTF'],
  'geometry-index-vec3-bridge': ['geometry', "Bridge: indices accessor type 'VEC3' is not SCALAR"],
  'geometry-index-normalized-bridge': ['geometry', 'Bridge: indices accessor is normalized'],
  'geometry-index-float-bridge': ['geometry', 'Bridge: indices componentType 5126 is not valid glTF'],
  'geometry-strip-index-normalized-bridge': ['geometry', 'Bridge: indices accessor is normalized'],
  'geometry-strip-index-vec3-bridge': ['geometry', "Bridge: indices accessor type 'VEC3' is not SCALAR"],
  'geometry-index-float-fractional': ['geometry', 'Brow_L: indices componentType 5126 is not valid glTF'],
  'geometry-lines': ['geometry', 'Brow_L: primitive mode 1 (LINES) is not triangles'],
  'geometry-index-count-35': ['geometry', 'Brow_L: 35 indices is not a multiple of 3'],
  'geometry-index-99': ['geometry', 'Brow_L: index 99 >= vertex count 8'],
  'components-shared-material': ['components', "material 'Lens clear' is used by both lens and non-lens primitives"],
  'components-everything-lens': ['components', 'no frame/temple primitives (everything was identified as lens)'],
  'components-lens-without-material': ['components', "lens primitive 'Lens_L' has no material"],
  'units-84mm': ['units', 'is not a plausible frame width in metres'],
  'units-millimetres': ['units', 'width 140.0 units: millimetre-scaled (expected metres, about 0.140)'],
  'units-centimetres': ['units', 'width 14.00 units: centimetre-scaled (expected metres, about 0.140)'],
  // strips with a signed or FLOAT index: the stored values reach the checks (three would have wrapped -1 to 65535)
  'geometry-strip-index-i16-negative-bridge': ['geometry', 'index -1 < 0'],
  'geometry-strip-index-float-fractional-bridge': ['geometry', 'Bridge: indices componentType 5126 is not valid glTF'],
  'geometry-strip-index-float-bridge': ['geometry', 'Bridge: indices componentType 5126 is not valid glTF'],
  'geometry-strip-index-i16-bridge': ['geometry', 'Bridge: indices componentType 5122 is not valid glTF'],
  // broken material/texture references: rendered, the Format row failed as spec.py fails it
  'format-material-7': ['format', 'Bridge: material 7 does not exist (2 material(s))'],
  'format-lens-material-7': ['components', "lens primitive 'Lens_L' has no material, so it is not distinguished by material"],
  'format-texture-index-missing': ['format', "material 'Frame acetate' pbrMetallicRoughness.baseColorTexture: texture 9 does not exist"],
};
/** The §1-table refusals (WP1_DESIGN.md §1: Transforms, Geometry, Components; Format ones are INTENDED): the case and the
 *  rows arview refuses at. Every case not listed here or in INTENDED must RENDER. */
const TRANSFORMS: readonly SpecRowName[] = ['transforms'], GEOMETRY: readonly SpecRowName[] = ['geometry'];
const ARVIEW_REFUSES: Readonly<Record<string, readonly SpecRowName[]>> = {
  'review-morph-texcoord': GEOMETRY, 'review-morph-empty': GEOMETRY,
  'review-color-accessor': ['format'], 'review-view-overrun': ['format'], 'review-json-bom': ['format'],
  'review-color-view-negative-index': ['format'], 'review-color-view-bool-index': ['format'],
  'review-color-view-missing-buffer': ['format'], 'review-color-view-negative-offset': ['format'], 'review-color-view-negative-length': ['format'],
  'transforms-parent-roll+25': TRANSFORMS, 'transforms-parent-pitch+25': TRANSFORMS, 'transforms-parent-upside-down': TRANSFORMS,
  'transforms-parent-mirrored-y': TRANSFORMS, 'transforms-trs-roll+25': TRANSFORMS, 'transforms-trs-pitch-25': TRANSFORMS,
  'transforms-trs-mirrored-y': TRANSFORMS, 'transforms-node-lens-r-rolled': TRANSFORMS, 'transforms-parent-1e-5-translation': TRANSFORMS,
  'transforms-parent-1e-5-scale': TRANSFORMS, 'transforms-parent-1mm': TRANSFORMS, 'transforms-instancing': TRANSFORMS,
  'geometry-non-finite': GEOMETRY, 'geometry-morph': GEOMETRY, 'geometry-skinned-bridge': GEOMETRY, 'geometry-index-99': GEOMETRY,
  'geometry-index-99-bridge': GEOMETRY, 'geometry-index-i16-negative': GEOMETRY, 'geometry-zero-triangles': GEOMETRY,
  'geometry-zero-vertices': GEOMETRY,
  // A fractional index names no vertex: out of range (spec.py fails only the row, for the FLOAT componentType).
  'geometry-index-float-fractional': GEOMETRY,
  // The same values in a TRIANGLE_STRIP: refused as stored (index -1 < 0; index value 2.5 is not an integer).
  'geometry-strip-index-i16-negative-bridge': GEOMETRY, 'geometry-strip-index-float-fractional-bridge': GEOMETRY,
  'components-no-lens': ['components'],
};
/** Index declarations WebGL cannot draw as stored (signed, non-SCALAR, normalized, FLOAT, interleaved, a normalized strip):
 *  the model RENDERS, the indices are rewritten from the stored values, and that is a reported 'geometry' fallback
 *  (spec.py fails the Geometry row for all but the interleaved view, which is valid glTF). */
const REWRITTEN: Readonly<Record<string, string>> = {
  'geometry-index-i16': '"Brow_L" stores its indices as Int16Array (not valid glTF)',
  'geometry-index-i16-bridge': '"Bridge" stores its indices as Int16Array (not valid glTF)',
  'geometry-index-vec3-bridge': '"Bridge" stores its indices as Uint32Array x3 (not valid glTF)',
  'geometry-index-normalized-bridge': '"Bridge" stores its indices as Uint16Array (normalized) (not valid glTF)',
  'geometry-index-normalized': '"Brow_L" stores its indices as Uint16Array (normalized) (not valid glTF)',
  'geometry-index-float-bridge': '"Bridge" stores its indices as Float32Array (not valid glTF)',
  'geometry-index-float': '"Brow_L" stores its indices as Float32Array (not valid glTF)',
  'geometry-index-interleaved-bridge': '"Bridge" stores its indices as Uint16Array (interleaved) (not drawable by WebGL as stored)',
  'geometry-strip-index-normalized-bridge': '"Bridge" is a TRIANGLE_STRIP whose index accessor is normalized (not valid glTF): three.js would triangulate',
  'geometry-strip-index-vec3-bridge': '"Bridge" is a TRIANGLE_STRIP whose index accessor is VEC3, not SCALAR (not valid glTF): three.js would triangulate',
  'geometry-strip-index-float-bridge': '"Bridge" is a TRIANGLE_STRIP whose index accessor is componentType 5126 (FLOAT), not unsigned (not valid glTF): three.js would triangulate',
  'geometry-strip-index-i16-bridge': '"Bridge" is a TRIANGLE_STRIP whose index accessor is componentType 5122 (SHORT), not unsigned (not valid glTF): three.js would triangulate',
  'geometry-fan-index-normalized-bridge': '"Bridge" is a TRIANGLE_FAN whose index accessor is normalized (not valid glTF): three.js would triangulate',
  'geometry-strip-index-i16-negative-bridge': '"Bridge" is a TRIANGLE_STRIP whose index accessor is componentType 5122 (SHORT), not unsigned (not valid glTF)',
  'geometry-strip-index-float-fractional-bridge': '"Bridge" is a TRIANGLE_STRIP whose index accessor is componentType 5126 (FLOAT), not unsigned (not valid glTF)',
};
/** Broken material and texture references on the drawn path: the model RENDERS without them (the glTF default
 *  material; the material without that texture), each a reported fallback of this subsystem starting with this text. */
const LEFT_OUT: Readonly<Record<string, readonly [string, string]>> = {
  'format-material-7': ['materials', '"Bridge" primitive 0 names material 7, which does not exist (2 material(s)): it is drawn with the glTF default material'],
  'format-lens-material-7': ['materials', '"Lens_L" primitive 0 names material 7, which does not exist (2 material(s)): it is drawn with the glTF default material'],
  'format-texture-source-missing': ['textures', 'Material "Frame acetate" pbrMetallicRoughness.baseColorTexture: texture 0 refers to image 9, which does not exist'],
  'format-texture-index-missing': ['textures', 'Material "Frame acetate" pbrMetallicRoughness.baseColorTexture: texture 9 does not exist'],
  'format-image-without-source': ['textures', 'Material "Frame acetate" pbrMetallicRoughness.baseColorTexture: image 0 has neither a uri nor a bufferView'],
};
const capitalized = (row: SpecRowName): string => row[0]!.toUpperCase() + row.slice(1);
/** arview's status against the tables above: '' when as expected. */
function statusIssue(label: string, id: string, ours: ModelReport): string {
  const intended = INTENDED[id];
  const refused = ARVIEW_REFUSES[id] ?? null, expected = intended?.arviewStatus ?? (refused ? 'refused' : 'rendered');
  if (ours.status !== expected) return `${label}: arview ${ours.status} (${ours.refusal.join(' | ')}), expected ${expected}${intended ? ` (${intended.why})` : ''}`;
  if (refused) {
    const rows = SPEC_ROWS.filter(row => ours.refusal.some(reason => reason.startsWith(`§2 ${capitalized(row)}: `)));
    if (rows.join() !== refused.join()) return `${label}: arview refused at ${JSON.stringify(rows)}, expected ${JSON.stringify(refused)}: ${ours.refusal.join(' | ')}`;
  }
  const rewritten = REWRITTEN[id];
  if (rewritten && !ours.fallbacks.some(entry => entry.subsystem === 'geometry' && entry.detail.startsWith(rewritten))) {
    return `${label}: no geometry fallback starting "${rewritten}": ${JSON.stringify(ours.fallbacks)}`;
  }
  const leftOut = LEFT_OUT[id];
  if (leftOut && !ours.fallbacks.some(entry => entry.subsystem === leftOut[0] && entry.detail.startsWith(leftOut[1]))) {
    return `${label}: no ${leftOut[0]} fallback starting "${leftOut[1]}": ${JSON.stringify(ours.fallbacks)}`;
  }
  return '';
}
const stubTextures = (): GLTFLoaderPlugin => ({name: 'ArviewTestTextures', loadTexture: async () => new Texture()});
const NO_WORKER_DRACO = 'which cannot be decoded here: there is no Web Worker for the Draco decoder (a renderer-less Node load)';
const NOT_CHECKED_HERE = 'the Draco-compressed geometry cannot be decoded here (an environment limit, not a verdict on the file)';
/** One arview load, killed with a named failure after `ms` (owner rule: no silent stalls). */
function withTimeout<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const stall = new Promise<never>((_, reject) => {timer = setTimeout(() => reject(new Error(`${what} stalled for ${ms / 1000} s and was abandoned`)), ms);});
  return Promise.race([promise, stall]).finally(() => clearTimeout(timer));
}
async function arview(path: string): Promise<ModelReport> {
  return withTimeout(arviewUnbounded(path), 30_000, `arview load of ${basename(path)}`);
}
async function arviewUnbounded(path: string): Promise<ModelReport> {
  const bytes = await readFile(path), directory = join(path, '..');
  const quiet = console.warn; console.warn = () => {};   // three's GLTFLoader warns about the deliberately odd files
  try {
    const {report} = await checkModel(new Uint8Array(bytes).buffer, {url: `http://127.0.0.1:8250/models/${basename(path)}`, renderer: null, plugins: [stubTextures],
      fetchResource: async url => {
        const local = join(directory, decodeURIComponent(url.slice('http://127.0.0.1:8250/models/'.length)));
        return existsSync(local) ? new Uint8Array(await readFile(local)).buffer : null;
      }});
    return report;
  } finally {console.warn = quiet;}
}

const verdict = (pass: boolean): Verdict => pass ? 'pass' : 'fail';
/** The same failure SENTENCES, in the same order, on both sides (the row's whole detail). The temple-missing cases: a +Y-up
 *  front without temples must say its temples are missing and nothing about its axis (the up axis is judged on the
 *  lenses then; with the overall box a temple-less front read "Y is not the thinnest axis ... not +Y up"). */
const SAME_DETAIL: Readonly<Record<string, SpecRowName>> = {
  'orientation-temple-left-missing': 'orientation', 'orientation-both-temples-missing': 'orientation', 'orientation-hinge-stubs-only': 'orientation',
  'orientation-hinge-stubs-upside-down': 'orientation', 'orientation-no-temples-upside-down': 'orientation', 'orientation-z-up-no-temples': 'orientation',
  'orientation-z-up': 'orientation', 'orientation-deep-wrap-temple-left-missing': 'orientation',
};
/** The up-axis reasons a SAME_DETAIL case must NOT give (on either side): a temple-less +Y-up front is not a wrong axis. */
const NO_AXIS_REASON = ['orientation-temple-left-missing', 'orientation-both-temples-missing', 'orientation-hinge-stubs-only',
  'orientation-hinge-stubs-upside-down', 'orientation-no-temples-upside-down', 'orientation-deep-wrap-temple-left-missing'];
const rowDetail = (report: ModelReport, row: SpecRowName): string => report.spec[row].detail.join('; ');
const pyDetail = (python: PythonReport, row: SpecRowName): string => python.errors.filter(error => error.startsWith(`${row}:`)).join('; ');

/** The measured numbers both sides report: [key, absolute tolerance] (millimetres / degrees; exact for the rest). */
const MEASURED: readonly [string, number][] = [
  ['width_mm', 0.011], ['bbox_mm', 0.011], ['triangles', 0], ['lens_primitives', 0], ['excluded_from_measurement', 0],
  ['bridge_underside_mm', 0.011], ['lens_centre_x_mm', 0.002], ['front_centre_x_mm', 0.002], ['origin_x_judged_on', 0],
  ['temple_reach_z_mm', 0.011], ['temple_roots_above_lens_centre_mm', 0.011], ['lens_halves_dy_mm', 0.002],
  ['temple_slope_deg', 0.011], ['lens_bbox_centre_mm', 0.011],
];
/** Warnings about the measured rows that both must give (a substring each side's sentence contains). */
const SHARED_WARNINGS = ['origin x judged on the front region', 'although the lenses are centred', '(allowed, but not exact)',
  'the temple roots sit only', '+Y up not measured', 'up axis not measured', 'up axis not judged on the lenses', 'lens position not measured', 'roll not measured', 'pitch not measured on the left', 'pitch not measured on the right',
  'is outside the usual'];
function sameValue(a: unknown, b: unknown, tolerance: number): boolean {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) <= tolerance + 1e-9;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((value, index) => sameValue(value, b[index], tolerance));
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].every(key => sameValue((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key], tolerance));
  }
  return a === b;
}
/** The metre twin a RESCALED case is measured against: spec.py's report on it, or (UNGATED_TWINS only) spec.py's own
 *  sections with the units gate bypassed. */
function metreTwin(id: string, entry: PythonCase): {report: PythonReport | null; problem: string | null} {
  const ungated = id in UNGATED_TWINS;
  const report = (ungated ? entry.python_metres_ungated : entry.python_metres) ?? null;
  if (!report) return {report: null, problem: `${id}: no ${ungated ? 'ungated ' : ''}metre twin from spec_agreement_cases.py`};
  if (!ungated && entry.python_metres_ungated) return {report, problem: `${id}: spec.py did not measure the metre twin (not listed in UNGATED_TWINS)`};
  if (ungated && entry.python_metres?.rows.units) return {report, problem: `${id}: spec.py now measures the metre twin itself (remove it from UNGATED_TWINS)`};
  return {report, problem: null};
}
const AS_AUTHORED = 'not checked as authored: units are wrong; measured after the ';

/** Every disagreement between arview and spec.py on one file: rows (except the intended ones), measurements, warnings,
 *  and the units rescale (only the RESCALED files, each rendered, loud, and failed by spec.py; what arview measured after
 *  the rescale is compared with spec.py on `metres`, the case's metre twin). */
function compare(label: string, ours: ModelReport, python: PythonReport, intended: Intended | null, metres: PythonReport | null = null): string[] {
  const found: string[] = [];
  const scale = RESCALED[label], applied = ours.measurements.units_scale;
  if (scale) {
    if (ours.status !== 'rendered' || applied !== scale || !ours.warnings.some(warning => warning.startsWith('UNITS: ')) || python.rows.units) {
      found.push(`${label}: the INTENDED units difference changed: expected arview rendered, rescaled x${scale} with a UNITS warning, and spec.py failing `
        + `units; got arview ${ours.status} x${String(applied)}, spec.py units ${verdict(python.rows.units)}`);
    }
    if (!metres) found.push(`${label}: no metre twin to compare the post-rescale measurements with`);
    else {
      // The rescaled model must measure exactly as the same build authored in metres: every number, the shared
      // warnings, and the origin/orientation failures (after arview's "not checked as authored" line).
      found.push(...measurementIssues(`${label} (after the x${scale} rescale, against the metre twin)`, ours, metres, ['is outside the usual']));
      for (const row of ['origin', 'orientation'] as const) {
        const detail = ours.spec[row].detail, theirs = metres.errors.filter(error => error.startsWith(`${row}: `)).map(error => error.slice(row.length + 2));
        if (!detail[0]?.startsWith(AS_AUTHORED)) found.push(`${label} / ${row}: arview's first detail is not "${AS_AUTHORED}...": ${detail.join('; ')}`);
        else if (JSON.stringify(detail.slice(1)) !== JSON.stringify(theirs)) {
          found.push(`${label} / ${row}: after the rescale arview says ${JSON.stringify(detail.slice(1))}, spec.py on the metre twin ${JSON.stringify(theirs)}`);
        }
      }
    }
  } else if (applied !== undefined && applied !== 1) found.push(`${label}: arview rescaled x${String(applied)} (not listed in RESCALED)`);
  const measured = !intended && !scale;
  for (const row of SPEC_ROWS) {
    const expected = intended?.rows[row];
    const a = verdict(ours.spec[row].pass), p = verdict(python.rows[row]);
    if (expected) {
      if (a !== expected.arview || p !== expected.python) {
        found.push(`${label} / ${row}: the INTENDED difference changed: expected arview ${expected.arview} / spec.py ${expected.python}, got arview ${a} `
          + `(${rowDetail(ours, row)}) / spec.py ${p} (${pyDetail(python, row)})`);
      }
    } else if (a !== p) found.push(`${label} / ${row}: arview ${a} (${rowDetail(ours, row)}) but spec.py ${p} (${pyDetail(python, row)})`);
  }
  if (measured) found.push(...measurementIssues(label, ours, python));
  return found;
}
/** The measured numbers and the shared warnings of `ours` against `python` (skipping the `skipWarnings` patterns). */
function measurementIssues(label: string, ours: ModelReport, python: PythonReport, skipWarnings: readonly string[] = []): string[] {
  const found: string[] = [];
  for (const [key, tolerance] of MEASURED) {
    const a = ours.measurements[key], p = python.measurements[key];
    if (!sameValue(a, p, tolerance)) found.push(`${label} / measurement ${key}: arview ${JSON.stringify(a)} but spec.py ${JSON.stringify(p)}`);
  }
  for (const pattern of SHARED_WARNINGS.filter(pattern => !skipWarnings.includes(pattern))) {
    const a = ours.warnings.some(warning => warning.includes(pattern)), p = python.warnings.some(warning => warning.includes(pattern));
    if (a !== p) found.push(`${label} / warning "${pattern}": arview ${a ? 'gives it' : 'does not'}, spec.py ${p ? 'gives it' : 'does not'}`);
  }
  return found;
}

function runPython(args: string[], what: string): string {
  const result = spawnSync(PYTHON!, args, {cwd: LENSES_ROOT, encoding: 'utf8', maxBuffer: 256 * 1024 * 1024, timeout: 120_000});
  if (result.error) throw new Error(`${what}: ${result.error.message} (a stall is killed after 120 s)`);
  if (!result.stdout.trim()) throw new Error(`${what} printed nothing (exit ${result.status}): ${result.stderr}`);
  return result.stdout;
}

test('arview and spec.py give identical row verdicts and measurements on every Python-fabricated GLB', async t => {
  if (SKIP) {t.skip('ARVIEW_SKIP_SPEC_AGREEMENT=1: agreement with spec.py was NOT checked'); return;}
  assert.ok(PYTHON, 'glasses3d/.venv is missing: agreement with spec.py cannot be checked (set ARVIEW_SKIP_SPEC_AGREEMENT=1 to skip knowingly)');
  // Each run fabricates into its own folder (removed afterwards): two suites running at once wrote the same case files
  // and one read the other's half-written GLB ("not a GLB file (missing 'glTF' magic)", WP1 review E/F stress run).
  const run = join(OUT, `run-${process.pid}-${Date.now()}`);
  mkdirSync(run, {recursive: true});
  t.after(() => rmSync(run, {recursive: true, force: true}));
  const {cases} = JSON.parse(runPython([SCRIPT, run], 'tests/spec_agreement_cases.py')) as {cases: PythonCase[]};
  assert.deepEqual(cases.filter(entry => entry.error).map(entry => `${entry.id}: ${entry.error}`), [], 'every case was fabricated');
  assert.ok(cases.length >= 100, `${cases.length} cases`);
  for (const id of [...Object.keys(INTENDED), ...Object.keys(RESCALED), ...Object.keys(COMPRESSED_DOWNGRADE), ...Object.keys(SAME_REASON), ...Object.keys(SAME_DETAIL),
    ...Object.keys(ARVIEW_REFUSES), ...Object.keys(REWRITTEN), ...Object.keys(LEFT_OUT), ...Object.keys(UNGATED_TWINS)]) {
    assert.ok(cases.some(entry => entry.id === id.replace(/ \(decoded twin\)$/, '')), `the explicit-difference table names a case that exists: ${id}`);
  }
  const disagreements: string[] = [], table: unknown[] = [];
  for (const entry of cases) {
    const python = entry.python!;
    const ours = await arview(entry.file!);
    table.push({id: entry.id, arview: {status: ours.status, failed: SPEC_ROWS.filter(row => !ours.spec[row].pass), refusal: ours.refusal},
      python: {ok: python.ok, failed: SPEC_ROWS.filter(row => !python.rows[row])}});
    // A millimetre/centimetre case: its metre twin (the post-rescale measurements are compared with it).
    let metres: PythonReport | null = null;
    const rescaledLabel = entry.decoded_file ? `${entry.id} (decoded twin)` : entry.id;
    if (RESCALED[rescaledLabel]) {
      const twin = metreTwin(entry.id, entry);
      if (twin.problem) disagreements.push(twin.problem);
      metres = twin.report;
    } else if (entry.metre_file) disagreements.push(`${entry.id}: a metre twin was built but the case is not listed in RESCALED`);
    if (entry.decoded_file) {
      // Compressed: Node has no Web Worker for arview's Draco decoder: the load ends 'unchecked' up front (an environment
      // limit, never a §2 refusal, never a silent pass and never a hang; see COMPRESSED_DOWNGRADE). The Format row is
      // what was really checked (spec.py's verdict on the flagged file); the other rows are "not checked".
      assert.equal(ours.status, 'unchecked', `${entry.id}: the flagged bytes cannot be decoded in Node: ${ours.refusal.join(' | ')}`);
      assert.deepEqual(ours.refusal, [], `${entry.id}: not a §2 refusal`);
      assert.equal(ours.unchecked.length, 1, entry.id);
      assert.ok(ours.unchecked[0]!.includes(NO_WORKER_DRACO), `${entry.id}: ${ours.unchecked[0]}`);
      assert.ok(ours.warnings.some(warning => warning.startsWith('NOT CHECKED HERE: ') && warning.includes(NO_WORKER_DRACO)), `${entry.id}: the loud warning`);
      assert.equal(verdict(ours.spec.format.pass), verdict(python.rows.format), `${entry.id}: Format as checked (${rowDetail(ours, 'format')}) vs spec.py (${pyDetail(python, 'format')})`);
      for (const row of SPEC_ROWS.filter(row => row !== 'format')) {
        assert.deepEqual(ours.spec[row], {pass: false, detail: [`not checked: ${NOT_CHECKED_HERE}`]}, `${entry.id} / ${row}`);
      }
      const decoded = await arview(entry.decoded_file), twin = entry.python_decoded!;
      // Every decoded twin renders (no twin is listed in the tables).
      const issue = statusIssue(`${entry.id} (decoded twin)`, `${entry.id} (decoded twin)`, decoded);
      if (issue) disagreements.push(issue);
      disagreements.push(...compare(`${entry.id} (decoded twin)`, decoded, twin, null, metres));
      // spec.py's bounds-only verdict on the flagged file equals the decoded truth, except where listed (an "unverified" pass).
      const downgrade = COMPRESSED_DOWNGRADE[entry.id];
      for (const row of SPEC_ROWS) {
        const truth = verdict(twin.rows[row]), bounded = verdict(python.rows[row]);
        if (downgrade?.row === row) {
          if (!(truth === 'fail' && bounded === 'pass' && python.warnings.some(warning => warning.includes(downgrade.warning)))) {
            disagreements.push(`${entry.id} / ${row}: the INTENDED downgrade changed: decoded ${truth}, bounds-only ${bounded}, warnings ${python.warnings.join(' | ')}`);
          }
        } else if (truth !== bounded) disagreements.push(`${entry.id} / ${row}: spec.py bounds-only ${bounded} but decoded ${truth} (not listed in COMPRESSED_DOWNGRADE)`);
      }
      continue;
    }
    const reason = SAME_REASON[entry.id];
    if (reason) {
      const [row, text] = reason, normalize = (value: string): string => value.replaceAll('"', "'");
      if (!ours.spec[row].detail.some(detail => normalize(detail).includes(text))) disagreements.push(`${entry.id} / ${row}: arview's reason lacks "${text}": ${rowDetail(ours, row)}`);
      if (!python.errors.some(error => error.startsWith(`${row}:`) && normalize(error).includes(text))) disagreements.push(`${entry.id} / ${row}: spec.py's reason lacks "${text}": ${pyDetail(python, row)}`);
    }
    const detailRow = SAME_DETAIL[entry.id];
    if (detailRow) {
      const theirs = python.errors.filter(error => error.startsWith(`${detailRow}: `)).map(error => error.slice(detailRow.length + 2));
      if (JSON.stringify(ours.spec[detailRow].detail) !== JSON.stringify(theirs)) {
        disagreements.push(`${entry.id} / ${detailRow}: arview says ${JSON.stringify(ours.spec[detailRow].detail)}, spec.py ${JSON.stringify(theirs)}`);
      }
      if (NO_AXIS_REASON.includes(entry.id) && ours.spec[detailRow].detail.some(detail => /thinnest axis|middle axis/.test(detail))) {
        disagreements.push(`${entry.id} / ${detailRow}: a temple-less +Y-up front is reported as a wrong axis: ${rowDetail(ours, detailRow)}`);
      }
    }
    const intended = INTENDED[entry.id] ?? null;
    const issue = statusIssue(entry.id, entry.id, ours);
    if (issue) disagreements.push(issue);
    // Measurements are compared where both measured the same geometry: not after a refusal before parsing; a rescaled
    // file's against its metre twin (compare()).
    disagreements.push(...compare(entry.id, ours, python, intended, metres));
  }
  // The latest table, written beside the run folder and renamed into place (a concurrent run may also write it).
  writeFileSync(join(run, 'agreement.json'), JSON.stringify({cases: table, disagreements}, null, 1));
  try {renameSync(join(run, 'agreement.json'), join(OUT, 'agreement.json'));} catch {/* evidence only: another run holds it */}
  assert.deepEqual(disagreements, []);
});

test('on the four real proof GLBs (read-only) arview renders each and agrees with spec.py --full row by row and number by number', async t => {
  if (SKIP) {t.skip('ARVIEW_SKIP_SPEC_AGREEMENT=1: agreement with spec.py was NOT checked'); return;}
  assert.ok(PYTHON, 'glasses3d/.venv is missing: agreement with spec.py cannot be checked (set ARVIEW_SKIP_SPEC_AGREEMENT=1 to skip knowingly)');
  const present = REAL.filter(file => existsSync(join(LENSES_ROOT, file.path)));
  const absent = REAL.filter(file => !present.includes(file));
  if (absent.length) t.diagnostic(`NOT checked (not on this machine): ${absent.map(file => file.name).join(', ')}`);
  if (!present.length) {t.skip('none of the real proof GLBs is on this machine: NOT checked'); return;}
  const reports = JSON.parse(runPython(['-m', 'glasses3d.g3d_host.spec', '--full', ...present.map(file => join(LENSES_ROOT, file.path))], 'spec.py --full')) as
    (PythonReport & {rows: Record<SpecRowName, {pass: boolean}>; path: string})[];
  assert.equal(reports.length, present.length);
  const disagreements: string[] = [];
  for (const [index, file] of present.entries()) {
    const full = reports[index]!;
    const python: PythonReport = {...full, rows: Object.fromEntries(SPEC_ROWS.map(row => [row, full.rows[row].pass])) as Record<SpecRowName, boolean>};
    const ours = await arview(join(LENSES_ROOT, file.path));
    assert.equal(ours.status, 'rendered', `${file.name}: ${ours.refusal.join(' | ')}`);
    // PROGRESS.md: q001, Prada and INVU pass; r9 fails ONLY the origin row (bridge underside +10.23 mm), and still renders.
    assert.deepEqual(SPEC_ROWS.filter(row => !python.rows[row]), file.name.includes('r9') ? ['origin'] : [], `${file.name}: spec.py ${python.errors.join(' | ')}`);
    disagreements.push(...compare(file.name, ours, python, null));
  }
  assert.deepEqual(disagreements, []);
});
