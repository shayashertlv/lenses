/** The GLB load front-end (WP1_DESIGN.md §1, §5): from the fetched bytes to a parsed glTF, with the Format row decided.
 *
 *  1. The SHA-256 of the exact bytes (the report's `rendered_sha256`).
 *  2. The container and JSON are read here first (glb.ts): an unreadable file, a file with no scene, or a broken
 *     node, mesh, accessor, buffer view or buffer reference on the loaded path (one the loader would dereference
 *     blindly) is refused with a §2 Format message. A broken material or texture reference there is not refused: it is
 *     left out of what three parses (the default material; the material without that texture), the Format row fails as
 *     spec.py fails it, and it is a reported fallback.
 *  3. External resources resolve against the model URL's directory. Each is fetched once, before parsing: a missing
 *     one is refused (WP1_DESIGN.md §1, the Format row; the message says which ones the default scene does not use);
 *     present ones load (with a "not self-contained" warning) from the very bytes that were fetched.
 *  4. Texture sizes, extensions and the live-site notes that need only the JSON.
 *  5. GLTFLoader parses the default scene alone (glb.ts defaultSceneDocument: other scenes, animations, cameras,
 *     lights and skins are never handed to three, so a broken reference there cannot refuse a file spec.py passes;
 *     each omission is a warning or the normalisation pass's Geometry row), with Draco, meshopt and KTX2 registered
 *     (the WebGLRenderer exists before parsing: KTX2Loader detects the GPU's compressed formats from it). A texture the
 *     loader could not decode leaves its material without it: that is a reported fallback, never silent. The parse is
 *     raced against the abort signal and a deadline (PARSE_TIMEOUT_MS: a stalled decoder is a §2 Format refusal that
 *     says so). A load without a Web Worker (renderer-less Node) cannot decode Draco (canDecodeDraco): it ends up
 *     front with status 'unchecked' (an environment limit, not a §2 verdict: no refusal, the geometry rows not checked,
 *     a loud warning), never attempting the decoder.
 *  The geometry rows are the normalisation pass's (normalize.ts), which runs next, before any subsystem. */
import {BufferAttribute, LoaderUtils, LoadingManager, Mesh} from 'three';
import type {BufferGeometry, InterleavedBufferAttribute, Texture, WebGLRenderer} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import type {GLTF, GLTFLoaderPlugin, GLTFParser} from 'three/addons/loaders/GLTFLoader.js';
import {DRACOLoader} from 'three/addons/loaders/DRACOLoader.js';
import {KTX2Loader} from 'three/addons/loaders/KTX2Loader.js';
import {MeshoptDecoder} from 'three/addons/libs/meshopt_decoder.module.js';
import {assetPath} from '../assets.ts';
import {gltfSourceOf} from '../eyewear/optical-material.ts';
import type {GltfSource} from '../eyewear/optical-material.ts';
import {DECODER_DIRECTORIES} from './decoders.ts';
import {defaultSceneDocument, describeError, externalResources, GlbFormatError, imageBytes, imageSize, isRecord, jsonPrimitives, KNOWN_EXTENSIONS,
  LIVE_FORBIDDEN_EXTENSIONS, loadedReferences, packGlb, readGlbContainer, sceneIssue, textureInfos} from './glb.ts';
import type {GlbContainer, Json} from './glb.ts';
import {nodeName} from './normalize.ts';
import {SPEC_ROWS} from './report.ts';
import type {ModelReportBuilder} from './report.ts';

export const ARVIEW_WARN_TEXTURE_PX = 4096;
export const LIVE_MAX_TEXTURE_PX = 2048;
export const LIVE_MAX_BYTES = 8 * 1024 * 1024;
/** KHR_materials_volume thicknessFactor above this looks like millimetres (spec.py VOLUME_THICKNESS_SUSPECT_M). */
export const VOLUME_THICKNESS_SUSPECT_M = 0.01;
/** How long three's parse (decoders included), and then the texture loads, may take before the load is abandoned with
 *  a reported reason (owner rule: no silent stalls; a stalled decoder must not hang the page or a CLI). */
export const PARSE_TIMEOUT_MS = 60_000;
export const DRACO_EXTENSION = 'KHR_draco_mesh_compression';
/** Draco decodes in a Web Worker (DRACOLoader); a renderer-less Node load has none, and there the decoder's relative
 *  URL cannot even be fetched: three's FileLoader then leaves the request registered as in flight, so the NEXT Draco
 *  file in the process waits forever. Such a load never attempts Draco geometry: it ends 'unchecked' up front (the
 *  report says the file was not checked here; it is not a §2 refusal). */
export const canDecodeDraco = (): boolean => typeof Worker !== 'undefined';

export interface GltfDecoders {
  readonly loader: GLTFLoader;
  /** Whether KTX2 textures can be decoded (needs a WebGLRenderer). */
  readonly ktx2: boolean;
  dispose(): void;
}

export const BASISU_EXTENSION = 'KHR_texture_basisu';

/** Takes the place of three's KHR_texture_basisu plugin when there is no WebGLRenderer to transcode with (the
 *  renderer-less loads: checkModel, unit tests). It has
 *  the built-in's name, so GLTFLoader keeps it at the built-in's position, ahead of every other plugin. Three's own
 *  throws "setKTX2Loader must be called" for a REQUIRED extension, which would refuse a valid file; this one leaves the
 *  texture null instead (its material draws without it) and loadModel reports it as a 'textures' fallback. An optional
 *  KTX2 texture with a fallback `source` loads that fallback image, as three does. */
export class NoTranscoderBasisU implements GLTFLoaderPlugin {
  readonly name = BASISU_EXTENSION;
  private readonly parser: GLTFParser;
  constructor(parser: GLTFParser) {this.parser = parser;}
  loadTexture(textureIndex: number): Promise<Texture> | null {
    const json: unknown = this.parser.json;
    if (!isRecord(json)) return null;
    const textureDef = (Array.isArray(json.textures) ? json.textures : [])[textureIndex];
    if (!isRecord(textureDef) || !isRecord(textureDef.extensions) || !isRecord(textureDef.extensions[BASISU_EXTENSION])) return null;
    const required = Array.isArray(json.extensionsRequired) && json.extensionsRequired.includes(BASISU_EXTENSION);
    if (!required && textureDef.source !== undefined) return null;
    return Promise.resolve(null) as unknown as Promise<Texture>;
  }
}

/** A GLTFLoader with every decoder arview supports registered. Every GLTFLoader that reads a model must come from here.
 *  `renderer` is required for KTX2: without one (renderer-less loads), KTX2 textures are left out (NoTranscoderBasisU)
 *  and loadModel reports them as a 'textures' fallback. */
export function createGltfLoader(renderer: WebGLRenderer | null, manager?: LoadingManager): GltfDecoders {
  const loader = new GLTFLoader(manager);
  const draco = new DRACOLoader(manager).setDecoderPath(assetPath(DECODER_DIRECTORIES.draco));
  loader.setDRACOLoader(draco);
  loader.setMeshoptDecoder(MeshoptDecoder);
  let ktx2: KTX2Loader | null = null;
  if (renderer) {
    ktx2 = new KTX2Loader(manager).setTranscoderPath(assetPath(DECODER_DIRECTORIES.basis)).detectSupport(renderer);
    loader.setKTX2Loader(ktx2);
  } else loader.register(parser => new NoTranscoderBasisU(parser));
  return {loader, ktx2: ktx2 !== null, dispose() {draco.dispose(); ktx2?.dispose();}};
}

export interface LoadModelOptions {
  /** The model's address (absolute, or relative to the page). External resources resolve against its directory. */
  readonly url: string;
  readonly renderer: WebGLRenderer | null;
  readonly signal?: AbortSignal;
  /** Fetches one external resource (default: fetch). Rejects or returns null when it is missing. */
  readonly fetchResource?: (url: string, signal?: AbortSignal) => Promise<ArrayBuffer | null>;
  /** Extra GLTFLoader plugins (unit tests stub texture decoding, which Node cannot do). */
  readonly plugins?: readonly ((parser: GLTFParser) => GLTFLoaderPlugin)[];
  /** A digest handed over with the model (?sha256=): compared, never trusted; the report binds to the rendered bytes. */
  readonly handoverSha256?: string | null;
  /** The parse deadline, and separately the texture loads' (default PARSE_TIMEOUT_MS). */
  readonly parseTimeoutMs?: number;
}

export interface LoadedModel {
  /** three's result for the default scene alone (`gltf.parser.json` is the pruned document, `gltf.scenes` holds only
   *  the default scene; there are no animations or cameras). */
  readonly gltf: GLTF;
  /** The original glTF JSON with three's associations (node/mesh/material indices are those of the file). */
  readonly source: GltfSource;
  readonly container: GlbContainer;
  readonly sha256: string;
}

function abortError(): DOMException {return new DOMException('Renderer startup was cancelled.', 'AbortError');}

/** A load step that did not finish within its deadline (it is abandoned, never awaited further). */
class LoadStall extends Error {
  constructor(message: string) {super(message); this.name = 'LoadStall';}
}
/** `promise`, raced against `signal` (an AbortError) and a deadline of `ms` (a LoadStall naming `what`). The abandoned
 *  promise keeps its handlers, so its late settlement is never an unhandled rejection. */
function bounded<T>(promise: Promise<T>, signal: AbortSignal | undefined, ms: number, what: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    if (signal?.aborted) {promise.catch(() => undefined); reject(abortError()); return;}
    const onAbort = (): void => {cleanup(); reject(abortError());};
    const timer = setTimeout(() => {cleanup(); reject(new LoadStall(`${what} did not finish within ${ms / 1000} s and was abandoned`));}, ms);
    function cleanup(): void {clearTimeout(timer); signal?.removeEventListener('abort', onAbort);}
    signal?.addEventListener('abort', onAbort, {once: true});
    promise.then(value => {cleanup(); resolve(value);}, (error: unknown) => {cleanup(); reject(error);});
  });
}

export async function sha256Hex(bytes: ArrayBuffer | Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes instanceof Uint8Array ? bytes.slice() : bytes);
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
}

/** The directory external URIs resolve against: the model URL's own directory ('' when the URL has none). */
export function resourceBase(url: string): string {
  try {
    const page = typeof location !== 'undefined' ? location.href : undefined;
    const absolute = page ? new URL(url, page) : new URL(url);
    return new URL('.', absolute).href;
  } catch {return '';}
}

async function defaultFetch(url: string, signal?: AbortSignal): Promise<ArrayBuffer | null> {
  const response = await fetch(url, signal ? {signal} : {});
  return response.ok ? await response.arrayBuffer() : null;
}

/** The stand-in for GLTFParser's FileLoader: the parser loads every buffer that is not the GLB's own chunk through
 *  `parser.fileLoader.load(resolvedUrl, onLoad, onProgress, onError)` (GLTFParser.loadBuffer), and this answers it from
 *  the bytes loadModel fetched before the parse (keyed by the same LoaderUtils.resolveURL), or decodes a data: URI with
 *  fetch. Three's FileLoader is never on this path: it would fetch the resource a second time, and in Node its progress
 *  reporter calls `new ProgressEvent`, which Node lacks: the rejection escaped as an uncaught error (it stopped
 *  `arview serve`) and the parse hung until its deadline. Images keep the LoadingManager's object URLs (the texture
 *  loaders need a URL). */
export function prefetchedBuffers(resolved: ReadonlyMap<string, Uint8Array>): (parser: GLTFParser) => GLTFLoaderPlugin {
  return parser => {
    const loader = {
      load(url: string, onLoad: (data: ArrayBuffer) => void, _onProgress?: unknown, onError?: (error: unknown) => void): void {
        const known = resolved.get(url);
        const bytes: Promise<ArrayBuffer> = known ? Promise.resolve(known.slice().buffer)
          : url.startsWith('data:') ? fetch(url).then(response => response.arrayBuffer())
            : Promise.reject(new Error(`${url} was not fetched before the parse`));
        bytes.then(data => onLoad(data), (error: unknown) => onError?.(error));
      },
      setRequestHeader() {return loader;}, setWithCredentials() {return loader;}, setResponseType() {return loader;},
    };
    (parser as unknown as {fileLoader: unknown}).fileLoader = loader;
    return {name: 'ArviewPrefetchedBuffers'};
  };
}

/** The index componentTypes glTF allows (unsigned byte, short, int). */
const UNSIGNED_INDEX_TYPES: readonly unknown[] = [5121, 5123, 5125];
const COMPONENT_NAMES: Readonly<Record<number, string>> = {5120: 'BYTE', 5122: 'SHORT', 5124: 'INT', 5126: 'FLOAT'};

/** Why a strip/fan index accessor is not valid glTF as declared (signed or FLOAT componentType, normalized, not
 *  SCALAR), or null. */
function stripIndexIssue(accessor: Json): string | null {
  const issues: string[] = [];
  if (!UNSIGNED_INDEX_TYPES.includes(accessor.componentType)) {
    const name = COMPONENT_NAMES[accessor.componentType as number];
    issues.push(`componentType ${String(accessor.componentType)}${name ? ` (${name})` : ''}, not unsigned`);
  }
  if (accessor.normalized === true) issues.push('normalized');
  if (accessor.type !== 'SCALAR') issues.push(`${String(accessor.type)}, not SCALAR`);
  return issues.length ? issues.join(', ') : null;
}

/** A TRIANGLE_STRIP or TRIANGLE_FAN whose index accessor is not valid glTF (a signed or FLOAT componentType, normalized,
 *  or not SCALAR): three's loader triangulates it through BufferAttribute.getX, which denormalizes a normalized index
 *  (every vertex number becomes 0) and reads one component per element of a non-SCALAR one, and then stores the result
 *  with setIndex(Array), which wraps a negative value and truncates a fractional one into a Uint16Array (a stored -1
 *  becomes 65535, a stored 2.5 becomes 2). It would draw, or refuse for, values the file does not store. Each is
 *  rebuilt here from the accessor's stored values (read raw: never denormalized, never truncated), triangulated as
 *  glTF 2.0 §3.7.2.1 and spec.py `triangulate` do, before the normalisation pass reads it; that pass fails the
 *  Geometry row for the declaration and refuses a value that names no vertex (negative, fractional, out of range),
 *  quoting the stored value. Returns one fallback sentence per rebuilt primitive. */
async function rebuildMisdeclaredStrips(gltf: GLTF, source: GltfSource): Promise<string[]> {
  const json = source.json as Json, meshes = Array.isArray(json.meshes) ? json.meshes : [], accessors = Array.isArray(json.accessors) ? json.accessors : [];
  const sentences: string[] = [], done = new Set<BufferGeometry>();
  for (const [object, association] of source.associations) {
    if (!(object instanceof Mesh) || association.meshes === undefined || association.primitives === undefined || done.has(object.geometry)) continue;
    const meshDef = meshes[association.meshes];
    const primitive = isRecord(meshDef) && Array.isArray(meshDef.primitives) ? meshDef.primitives[association.primitives] : undefined;
    if (!isRecord(primitive) || (primitive.mode !== 5 && primitive.mode !== 6) || typeof primitive.indices !== 'number') continue;
    const accessor = accessors[primitive.indices], issue = isRecord(accessor) ? stripIndexIssue(accessor) : null;
    if (issue === null) continue;
    const kind = primitive.mode === 5 ? 'TRIANGLE_STRIP' : 'TRIANGLE_FAN';
    let stored: BufferAttribute | InterleavedBufferAttribute;
    try {stored = await (gltf.parser.getDependency('accessor', primitive.indices) as Promise<BufferAttribute | InterleavedBufferAttribute>);}
    catch (error) {
      sentences.push(`"${nodeName(object, source)}" is a ${kind} with a misdeclared index accessor whose stored values could not be read `
        + `(${describeError(error)}): it is drawn as three.js triangulated it, which is not what the file stores (§2 Geometry).`);
      done.add(object.geometry); continue;
    }
    const itemSize = stored.itemSize, count = stored.count * itemSize, values = new Float64Array(count);
    if ((stored as InterleavedBufferAttribute).isInterleavedBufferAttribute) {
      const {data, offset} = stored as InterleavedBufferAttribute;
      for (let k = 0; k < count; k++) values[k] = data.array[Math.floor(k / itemSize) * data.stride + offset + k % itemSize]!;
    } else values.set((stored as BufferAttribute).array.subarray(0, count) as ArrayLike<number>);
    const triangles: number[] = [];
    for (let i = 0; i + 2 < count; i++) {
      if (primitive.mode === 5) triangles.push(values[i]!, values[i + 1 + (i % 2)]!, values[i + 2 - (i % 2)]!);
      else triangles.push(values[i + 1]!, values[i + 2]!, values[0]!);
    }
    // Vertex numbers stay exact; a value that names no vertex (negative, fractional) is kept as it is, so the
    // normalisation pass refuses it as out of range instead of drawing a truncated number.
    const exact = triangles.every(value => Number.isInteger(value) && value >= 0 && value <= 0xffffffff);
    object.geometry.setIndex(new BufferAttribute(exact ? Uint32Array.from(triangles) : Float64Array.from(triangles), 1));
    done.add(object.geometry);
    sentences.push(`"${nodeName(object, source)}" is a ${kind} whose index accessor is ${issue} (not valid glTF): three.js would triangulate `
      + 'the indices as declared, so arview rebuilt its triangles from the stored values (§2 Geometry).');
  }
  return sentences;
}

/** Refuse at the Format row: every other row is "not checked", as the Python validator reports an unreadable file. */
function refuseFormat(report: ModelReportBuilder, message: string, why = 'the file could not be read'): never {
  report.refuse('format', message);
  report.notChecked(SPEC_ROWS.filter(row => row !== 'format'), why);
  report.throwIfRefused();
  throw new Error(message);
}

/** spec.py `_textures` (sizes, texture references, texCoord, TEXCOORD presence, KHR_materials_volume units). */
function inspectTextures(report: ModelReportBuilder, container: GlbContainer, external: ReadonlyMap<string, Uint8Array>): void {
  const {json, bin} = container;
  const images = Array.isArray(json.images) ? json.images : [], textures = Array.isArray(json.textures) ? json.textures : [];
  const sizes: {image: number; size: [number, number] | null}[] = [];
  images.forEach((_, index) => {
    let data: Uint8Array | null;
    try {data = imageBytes(json, bin, index, external);}
    catch (error) {
      report.fail('format', `image ${index} is unreadable (${describeError(error)})`);
      sizes.push({image: index, size: null}); return;
    }
    if (data === null) {sizes.push({image: index, size: null}); return;}
    const size = imageSize(data);
    sizes.push({image: index, size});
    if (!size) {report.warn(`image ${index}: size could not be read (not PNG, JPEG, WebP or KTX2); texture limits not checked`); return;}
    if (Math.max(...size) > ARVIEW_WARN_TEXTURE_PX) report.warn(`image ${index} is ${size[0]}x${size[1]} (> ${ARVIEW_WARN_TEXTURE_PX}; 8K renders black in EEVEE)`);
    if (Math.max(...size) > LIVE_MAX_TEXTURE_PX) report.wouldFailLive(`image ${index} is ${size[0]}x${size[1]} (live limit ${LIVE_MAX_TEXTURE_PX})`);
  });
  report.measure('textures', sizes);
  const primitives = jsonPrimitives(json), materials = Array.isArray(json.materials) ? json.materials : [];
  materials.forEach((material, index) => {
    if (!isRecord(material)) return;
    const name = typeof material.name === 'string' ? material.name : `material${index}`;
    const users = primitives.filter(primitive => primitive.material === index);
    for (const {path, info} of textureInfos(material)) {
      const texture = info.index;
      if (!(typeof texture === 'number' && Number.isInteger(texture) && texture >= 0 && texture < textures.length)) {
        report.fail('format', `material '${name}' ${path}: texture ${JSON.stringify(texture)} does not exist`);
      }
      const transform = isRecord(info.extensions) && isRecord(info.extensions.KHR_texture_transform) ? info.extensions.KHR_texture_transform : {};
      const texCoord = transform.texCoord ?? info.texCoord ?? 0;
      if (!(typeof texCoord === 'number' && Number.isInteger(texCoord) && texCoord >= 0)) {
        report.fail('format', `material '${name}' ${path}: texCoord ${JSON.stringify(texCoord)} is not valid glTF (an integer >= 0)`);
        continue;
      }
      const missing = [...new Set(users.filter(user => !user.compressed && !user.attributes.includes(`TEXCOORD_${texCoord}`)).map(user => user.nodeName))].sort();
      if (missing.length) report.warn(`material '${name}' ${path} samples TEXCOORD_${texCoord}, which ${JSON.stringify(missing.slice(0, 6))} do not have: the texture cannot map onto them`);
    }
    const volume = isRecord(material.extensions) && isRecord(material.extensions.KHR_materials_volume) ? material.extensions.KHR_materials_volume : {};
    const thickness = volume.thicknessFactor;
    if (typeof thickness === 'number' && thickness > VOLUME_THICKNESS_SUSPECT_M) {
      report.warn(`material '${name}': KHR_materials_volume thicknessFactor ${thickness} looks like millimetres (glTF lengths are metres; a lens is about 0.0015)`);
    }
  });
}

/** Read, check and parse `bytes`. Throws ModelRefusal (the report's) on a §2 Format refusal; an AbortError when
 *  `signal` aborts. */
export async function loadModel(bytes: ArrayBuffer, report: ModelReportBuilder, options: LoadModelOptions): Promise<LoadedModel> {
  const {signal} = options;
  const sha256 = await sha256Hex(bytes);
  if (signal?.aborted) throw abortError();
  report.setSha256(sha256);
  report.measure('file_bytes', bytes.byteLength);
  const handover = options.handoverSha256?.trim().toLowerCase();
  if (handover && handover !== sha256) {
    report.warn(`The handed-over digest (?sha256=${handover}) differs from the bytes actually loaded (${sha256}); the report and every `
      + 'verdict bind to the loaded bytes.');
  }
  let container: GlbContainer;
  try {container = readGlbContainer(bytes);}
  catch (error) {
    if (error instanceof GlbFormatError) refuseFormat(report, `unreadable GLB: ${error.message}`);
    throw error;
  }
  const json: Json = container.json;
  report.note('format', `GLB 2.0, ${container.byteLength} bytes`);
  const assetVersion = isRecord(json.asset) && typeof json.asset.version === 'string' ? json.asset.version : null;
  if (!assetVersion || !(Number(assetVersion.split('.')[0]) >= 2)) refuseFormat(report, `unreadable GLB: asset.version ${JSON.stringify(assetVersion)} (glTF 2.0 is required)`);
  const issue = sceneIssue(json);
  if (issue) refuseFormat(report, issue, 'the file has no scene to render');
  const references = loadedReferences(json, container.bin !== null);
  if (references.problem) refuseFormat(report, `unreadable GLB: ${references.problem}`);

  const used = Array.isArray(json.extensionsUsed) ? json.extensionsUsed.filter((value): value is string => typeof value === 'string') : [];
  const required = Array.isArray(json.extensionsRequired) ? json.extensionsRequired.filter((value): value is string => typeof value === 'string') : [];
  report.measure('extensions_used', used);
  if (required.length) report.measure('extensions_required', required);
  const unknown = required.filter(name => !KNOWN_EXTENSIONS.has(name));
  if (unknown.length) report.warn(`extensionsRequired lists extensions three.js does not load: ${JSON.stringify(unknown)}`);
  // A compressed file's note: what cannot be decoded here is said now; "decoded by arview" only once the decode worked
  // (after the parse and the texture loads below; a failed parse is a refusal and never says it).
  const decodable: string[] = [];
  for (const name of LIVE_FORBIDDEN_EXTENSIONS) {
    if (used.includes(name)) {
      report.wouldFailLive(`uses ${name} (live site loads plain glTF only)`);
      if (name === BASISU_EXTENSION && !options.renderer) report.note('format', `compressed with ${name}: not transcoded here (no GPU renderer); see the textures fallback`);
      else if (name === DRACO_EXTENSION && !canDecodeDraco()) report.note('format', `compressed with ${name}: not decoded here (no Web Worker for the Draco decoder: a renderer-less Node load)`);
      else decodable.push(name);
    }
  }
  if (container.byteLength > LIVE_MAX_BYTES) report.wouldFailLive(`file is ${(container.byteLength / 1048576).toFixed(1)} MB (live limit 8 MB)`);

  // External resources: fetched once, before parsing; the parse reads exactly these bytes.
  const base = resourceBase(options.url), resources = externalResources(json);
  const fetched = new Map<string, Uint8Array>(), resolved = new Map<string, Uint8Array>(), missing: string[] = [];
  const fetchResource = options.fetchResource ?? defaultFetch;
  for (const resource of resources) {
    const url = LoaderUtils.resolveURL(resource.uri, base);
    let data: ArrayBuffer | null = null, why = '';
    try {data = await fetchResource(url, signal);}
    catch (error) {
      if (signal?.aborted) throw abortError();
      why = ` (${describeError(error)})`;
    }
    if (data === null) {
      const reached = (resource.kind === 'buffer' ? references.buffers : references.images).has(resource.index);
      missing.push(`${resource.kind} ${resource.index}: ${resource.uri}${why}${reached ? '' : ' (not used by the default scene)'}`); continue;
    }
    fetched.set(resource.uri, new Uint8Array(data)); resolved.set(url, new Uint8Array(data));
  }
  if (signal?.aborted) throw abortError();
  if (resources.length) report.warn(`not self-contained: external resources ${JSON.stringify(resources.map(resource => resource.uri))}`);
  // WP1_DESIGN.md §1, the Format row: a missing external resource is refused, whether or not the default scene uses it
  // (the binding wording; spec.py fails the Format row for every one). The message names the unused ones.
  // The GLB itself was read (the Format row records its version and size), so the other rows are not checked because of
  // this refusal, never because 'the file could not be read' (that reason is kept for unreadable GLB bytes).
  if (missing.length) refuseFormat(report, `missing external resource(s) next to the file: ${JSON.stringify(missing)}`,
    'refused at the Format row (missing external resource)');
  if (resources.length) {
    report.measure('external_resources', await Promise.all(resources.map(async resource => {
      const data = fetched.get(resource.uri);
      return {kind: resource.kind, index: resource.index, uri: resource.uri, bytes: data?.byteLength ?? null, sha256: data ? await sha256Hex(data) : null};
    })));
  }
  inspectTextures(report, container, fetched);

  // Only the default scene is handed to three (glb.ts defaultSceneDocument): a broken reference anywhere else cannot
  // end the load, and what is left out is said.
  const document = defaultSceneDocument(json, references);
  for (const sentence of document.ignored) report.warn(sentence);
  if (document.lights) {
    report.fallback('lights', `${document.lights} default-scene node(s) carry a KHR_lights_punctual light; it is not added to the scene: `
      + 'arview lights every model with its own lights.');
  }
  // Broken material and texture references on the drawn path (WP1_DESIGN.md §1: rendered, never refused, each reported).
  const materialCount = Array.isArray(json.materials) ? json.materials.length : 0;
  for (const entry of references.danglingMaterials) {
    // spec.py `_format` fails the row for every primitive whose material does not exist (a null material is "none").
    if (entry.material !== null) report.fail('format', `${entry.nodeName}: material ${JSON.stringify(entry.material)} does not exist (${materialCount} material(s))`);
    report.fallback('materials', `"${entry.nodeName}" primitive ${entry.primitive} names material ${JSON.stringify(entry.material)}, which does not exist `
      + `(${materialCount} material(s)): it is drawn with the glTF default material (§2 Format).`);
  }
  for (const entry of references.danglingTextures) {
    // A texture index that does not exist already fails the row (inspectTextures, spec.py's wording); an image source that
    // cannot be loaded is arview's own Format failure (spec.py does not check image sources).
    if (entry.kind === 'source') report.fail('format', `material '${entry.materialName}' ${entry.path}: texture ${JSON.stringify(entry.texture)} cannot be loaded (${entry.reason})`);
    report.fallback('textures', `Material "${entry.materialName}" ${entry.path}: ${entry.reason}, so three.js could not load it; the reference was `
      + 'left out and the material draws without that texture (§2 Format).');
  }
  if (!canDecodeDraco()) {
    // Without a Web Worker (a renderer-less Node load) Draco cannot run: compressed default-scene geometry is never
    // attempted (canDecodeDraco says why), and an unused declaration is kept from three so it never starts the decoder.
    // That is a limit of this environment, not a verdict on the file: the load ends 'unchecked' (no §2 refusal, and the
    // Format row keeps what was really checked), with the geometry rows not checked and a loud warning.
    const compressed = jsonPrimitives(json).filter(primitive => primitive.compressed === DRACO_EXTENSION);
    if (compressed.length) {
      report.uncheck(`${compressed.length} default-scene primitive(s) use ${DRACO_EXTENSION} (${JSON.stringify([...new Set(compressed.map(primitive => primitive.nodeName))].slice(0, 6))}), `
        + 'which cannot be decoded here: there is no Web Worker for the Draco decoder (a renderer-less Node load). This says nothing about the file; '
        + 'check it in the browser (the arview page or the capture CLI), which decodes Draco');
      report.notChecked(SPEC_ROWS.filter(row => row !== 'format'), 'the Draco-compressed geometry cannot be decoded here (an environment limit, not a verdict on the file)');
      report.throwIfUnchecked();
    }
    for (const key of ['extensionsUsed', 'extensionsRequired'] as const) {
      const names = document.json[key];
      if (Array.isArray(names) && names.includes(DRACO_EXTENSION)) document.json[key] = names.filter(name => name !== DRACO_EXTENSION);
    }
  }

  // Parse, with every decoder; external URLs map to the bytes fetched above.
  // Buffers come from prefetchedBuffers (no FileLoader); images through object URLs of the fetched bytes.
  const objectUrls = new Map<string, string>();
  const manager = new LoadingManager();
  for (const resource of resources) {
    const url = LoaderUtils.resolveURL(resource.uri, base), data = resolved.get(url);
    if (resource.kind === 'image' && data && !objectUrls.has(url)) objectUrls.set(url, URL.createObjectURL(new Blob([data.slice()])));
  }
  if (objectUrls.size) manager.setURLModifier(url => objectUrls.get(url) ?? url);
  const decoders = createGltfLoader(options.renderer, manager);
  decoders.loader.register(prefetchedBuffers(resolved));
  for (const plugin of options.plugins ?? []) decoders.loader.register(plugin);
  let gltf: GLTF, source: GltfSource, rebuilt: string[];
  const failed: string[] = [], timeoutMs = options.parseTimeoutMs ?? PARSE_TIMEOUT_MS;
  let basisuFailed = 0;
  try {
    // Raced against the abort signal and a deadline: a decoder that never answers (a Draco worker or a KTX2 transcoder
    // that failed to start) ends the load with a §2 Format refusal saying so, never a silent hang.
    try {gltf = await bounded(decoders.loader.parseAsync(packGlb(document.json, container.bin), base), signal, timeoutMs, "three.js's GLTFLoader parse");}
    catch (error) {
      if (signal?.aborted) throw abortError();
      refuseFormat(report, `the GLB could not be parsed: ${describeError(error)}`
        + (error instanceof LoadStall ? ' (a decoder or a buffer load stalled)' : ''));
    }
    if (signal?.aborted) throw abortError();
    // The source every later reader uses carries the ORIGINAL JSON (indices are unchanged by the pruning): lens
    // identification, the report's names and the normalisation pass's skins, animations and instancing checks see the
    // file as spec.py sees it.
    source = {json, associations: gltfSourceOf(gltf).associations};
    rebuilt = await rebuildMisdeclaredStrips(gltf, source);
    // A texture the loader could not decode is null (GLTFLoader catches it): its material draws without it. Reported.
    // Only the materials the default scene draws are asked (an unused material is never loaded). Asked while the decoders
    // and the external resources' object URLs are still alive: a texture three did not request during the parse (a slot
    // it does not read) is loaded now, and must not fail only because its decoder or bytes were already released.
    // The materials as three was handed them (dangling references already left out and reported above).
    const usedTextures = new Set<number>(), drawnMaterials = Array.isArray(document.json.materials) ? document.json.materials : [];
    for (const index of new Set(jsonPrimitives(json).flatMap(primitive => primitive.material === null ? [] : [primitive.material]))) {
      for (const {info} of textureInfos(drawnMaterials[index])) if (typeof info.index === 'number') usedTextures.add(info.index);
    }
    const texturesDeadline = Date.now() + timeoutMs;
    for (const index of [...usedTextures].sort((a, b) => a - b)) {
      let texture: Texture | null = null, why = '';
      try {
        texture = await bounded(gltf.parser.getDependency('texture', index) as Promise<Texture | null>, signal, Math.max(1, texturesDeadline - Date.now()), `texture ${index}`);
      } catch (error) {
        if (signal?.aborted) throw abortError();
        texture = null; why = error instanceof LoadStall ? ` (still loading after ${timeoutMs / 1000} s: abandoned)` : ` (${describeError(error)})`;
      }
      if (texture === null) {
        const textureDef = (json.textures as unknown[])[index];
        const extensions = isRecord(textureDef) && isRecord(textureDef.extensions) ? textureDef.extensions : {};
        const basisu = isRecord(extensions.KHR_texture_basisu);
        if (basisu) basisuFailed++;
        failed.push(`texture ${index}${why || (basisu && !decoders.ktx2 ? ' (KTX2, no transcoder without a GPU renderer)' : '')}`);
      }
    }
  } finally {
    decoders.dispose();
    for (const url of objectUrls.values()) URL.revokeObjectURL(url);
  }
  if (signal?.aborted) throw abortError();
  if (!gltf.scene) refuseFormat(report, 'the file has no default scene', 'the file has no scene to render');
  for (const name of decodable) {
    report.note('format', name === BASISU_EXTENSION && basisuFailed
      ? `compressed with ${name}: ${basisuFailed} texture(s) could not be transcoded; see the textures fallback`
      : `compressed with ${name}: decoded by arview`);
  }
  for (const sentence of rebuilt) report.fallback('geometry', sentence);
  if (failed.length) {
    report.fallback('textures', `${failed.length} texture(s) could not be decoded and their materials draw without them: ${failed.join(', ')}.`);
  }
  return {gltf, source, container, sha256};
}
