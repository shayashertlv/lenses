/** The GLB load front-end (WP1_DESIGN.md §1, §5): from the fetched bytes to a parsed glTF, with the Format row decided.
 *
 *  1. The SHA-256 of the exact bytes (the report's `rendered_sha256`).
 *  2. The container and JSON are read here first (glb.ts): an unreadable file, a file with no scene, or a broken
 *     reference on the loaded path (one the loader would dereference blindly) is refused with a §2 Format message.
 *  3. External resources resolve against the model URL's directory. Each is fetched once, before parsing: a missing
 *     one is refused (WP1_DESIGN.md §1, the Format row; the message says which ones the default scene does not use);
 *     present ones load (with a "not self-contained" warning) from the very bytes that were fetched.
 *  4. Texture sizes, extensions and the live-site notes that need only the JSON.
 *  5. GLTFLoader parses the default scene alone (glb.ts defaultSceneDocument: other scenes, animations, cameras,
 *     lights and skins are never handed to three, so a broken reference there cannot refuse a file spec.py passes;
 *     each omission is a warning or the normalisation pass's Geometry row), with Draco, meshopt and KTX2 registered
 *     (the WebGLRenderer exists before parsing: KTX2Loader detects the GPU's compressed formats from it). A texture the
 *     loader could not decode leaves its material without it: that is a reported fallback, never silent.
 *  The geometry rows are the normalisation pass's (normalize.ts), which runs next, before any subsystem. */
import {LoaderUtils, LoadingManager} from 'three';
import type {Texture, WebGLRenderer} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import type {GLTF, GLTFLoaderPlugin, GLTFParser} from 'three/addons/loaders/GLTFLoader.js';
import {DRACOLoader} from 'three/addons/loaders/DRACOLoader.js';
import {KTX2Loader} from 'three/addons/loaders/KTX2Loader.js';
import {MeshoptDecoder} from 'three/addons/libs/meshopt_decoder.module.js';
import {assetPath} from '../assets.ts';
import {gltfSourceOf} from '../eyewear/optical-material.ts';
import type {GltfSource} from '../eyewear/optical-material.ts';
import {DECODER_DIRECTORIES} from './decoders.ts';
import {defaultSceneDocument, externalResources, GlbFormatError, imageBytes, imageSize, isRecord, jsonPrimitives, KNOWN_EXTENSIONS,
  LIVE_FORBIDDEN_EXTENSIONS, loadedReferences, packGlb, readGlbContainer, sceneIssue, textureInfos} from './glb.ts';
import type {GlbContainer, Json} from './glb.ts';
import {SPEC_ROWS} from './report.ts';
import type {ModelReportBuilder} from './report.ts';

export const ARVIEW_WARN_TEXTURE_PX = 4096;
export const LIVE_MAX_TEXTURE_PX = 2048;
export const LIVE_MAX_BYTES = 8 * 1024 * 1024;
/** KHR_materials_volume thicknessFactor above this looks like millimetres (spec.py VOLUME_THICKNESS_SUSPECT_M). */
export const VOLUME_THICKNESS_SUSPECT_M = 0.01;

export interface GltfDecoders {
  readonly loader: GLTFLoader;
  /** Whether KTX2 textures can be decoded (needs a WebGLRenderer). */
  readonly ktx2: boolean;
  dispose(): void;
}

export const BASISU_EXTENSION = 'KHR_texture_basisu';

/** Takes the place of three's KHR_texture_basisu plugin when there is no WebGLRenderer to transcode with (the
 *  renderer-less loads: checkModel, the QA temple-cutoff loader, unit tests). It has
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
      report.fail('format', `image ${index} is unreadable (${error instanceof Error ? error.message : String(error)})`);
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
  for (const name of LIVE_FORBIDDEN_EXTENSIONS) {
    if (used.includes(name)) {
      report.wouldFailLive(`uses ${name} (live site loads plain glTF only)`);
      report.note('format', name === BASISU_EXTENSION && !options.renderer
        ? `compressed with ${name}: not transcoded here (no GPU renderer); see the textures fallback`
        : `compressed with ${name}: decoded by arview`);
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
      why = ` (${error instanceof Error ? error.message : String(error)})`;
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
  if (missing.length) refuseFormat(report, `missing external resource(s) next to the file: ${JSON.stringify(missing)}`);
  if (resources.length) {
    report.measure('external_resources', await Promise.all(resources.map(async resource => {
      const data = fetched.get(resource.uri);
      return {kind: resource.kind, index: resource.index, uri: resource.uri, bytes: data?.byteLength ?? null, sha256: data ? await sha256Hex(data) : null};
    })));
  }
  inspectTextures(report, container, fetched);

  // Only the default scene is handed to three (glb.ts defaultSceneDocument): a broken reference anywhere else cannot
  // end the load, and what is left out is said.
  const document = defaultSceneDocument(json, references.nodes);
  for (const sentence of document.ignored) report.warn(sentence);
  if (document.lights) {
    report.fallback('lights', `${document.lights} default-scene node(s) carry a KHR_lights_punctual light; it is not added to the scene: `
      + 'arview lights every model with its own lights.');
  }

  // Parse, with every decoder; external URLs map to the bytes fetched above.
  const objectUrls = new Map<string, string>();
  const manager = new LoadingManager();
  if (resolved.size) {
    for (const [url, data] of resolved) objectUrls.set(url, URL.createObjectURL(new Blob([data.slice()])));
    manager.setURLModifier(url => objectUrls.get(url) ?? url);
  }
  const decoders = createGltfLoader(options.renderer, manager);
  for (const plugin of options.plugins ?? []) decoders.loader.register(plugin);
  let gltf: GLTF;
  try {gltf = await decoders.loader.parseAsync(packGlb(document.json, container.bin), base);}
  catch (error) {
    if (signal?.aborted) throw abortError();
    refuseFormat(report, `the GLB could not be parsed: ${error instanceof Error ? error.message : String(error)}`);
  } finally {
    decoders.dispose();
    for (const url of objectUrls.values()) URL.revokeObjectURL(url);
  }
  if (signal?.aborted) throw abortError();
  if (!gltf.scene) refuseFormat(report, 'the file has no default scene', 'the file has no scene to render');

  // A texture the loader could not decode is null (GLTFLoader catches it): its material draws without it. Reported.
  // Only the materials the default scene draws are asked (an unused material is never loaded).
  const usedTextures = new Set<number>(), allMaterials = Array.isArray(json.materials) ? json.materials : [];
  for (const index of new Set(jsonPrimitives(json).flatMap(primitive => primitive.material === null ? [] : [primitive.material]))) {
    for (const {info} of textureInfos(allMaterials[index])) if (typeof info.index === 'number') usedTextures.add(info.index);
  }
  const failed: string[] = [];
  for (const index of [...usedTextures].sort((a, b) => a - b)) {
    let texture: Texture | null = null;
    try {texture = await (gltf.parser.getDependency('texture', index) as Promise<Texture | null>);} catch {texture = null;}
    if (texture === null) {
      const textureDef = (json.textures as unknown[])[index];
      const extensions = isRecord(textureDef) && isRecord(textureDef.extensions) ? textureDef.extensions : {};
      const basisu = isRecord(extensions.KHR_texture_basisu);
      failed.push(`texture ${index}${basisu && !decoders.ktx2 ? ' (KTX2, no transcoder without a GPU renderer)' : ''}`);
    }
  }
  if (failed.length) {
    report.fallback('textures', `${failed.length} texture(s) could not be decoded and their materials draw without them: ${failed.join(', ')}.`);
  }
  // The source every later reader uses carries the ORIGINAL JSON (indices are unchanged by the pruning): lens
  // identification, the report's names and the normalisation pass's skins, animations and instancing checks see the
  // file as spec.py sees it.
  return {gltf, source: {json, associations: gltfSourceOf(gltf).associations}, container, sha256};
}
