/** The GLB container and the glTF JSON, read before three parses anything (WP1_DESIGN.md §1, the Format row).
 *
 *  Pure and three-free. It mirrors `glasses3d/g3d_host/glb.py` where both read the same thing (the container rules,
 *  the default-scene issue, external URIs, image header sizes), so arview and the Python validator refuse or warn
 *  about the same files. References that three's GLTFLoader dereferences without checking on the loaded path (a
 *  material, texture, image, accessor, buffer view, buffer, mesh or node index that does not exist, reached from the
 *  default scene) are found here, so an unreadable file is refused with a precise §2 Format message instead of a
 *  TypeError from inside the loader. Entries nothing loads are never refused, and never parsed: three is handed the
 *  default scene alone (defaultSceneDocument, packGlb), so a broken reference outside it cannot end the load. */

export const GLB_MAGIC = 0x46546c67;
export const CHUNK_JSON = 0x4e4f534a;
export const CHUNK_BIN = 0x004e4942;
export const INSTANCING_EXTENSION = 'EXT_mesh_gpu_instancing';
export const COMPRESSION_EXTENSIONS = Object.freeze(['KHR_draco_mesh_compression', 'EXT_meshopt_compression', 'KHR_meshopt_compression'] as const);
/** What the live site rejects outright (RULES_AUDIT §C: plain glTF only). */
export const LIVE_FORBIDDEN_EXTENSIONS = Object.freeze([...COMPRESSION_EXTENSIONS, 'KHR_texture_basisu'] as const);
/** What three's GLTFLoader understands (as `g3d_host/spec.py` KNOWN_EXTENSIONS). */
export const KNOWN_EXTENSIONS: ReadonlySet<string> = new Set([
  'KHR_binary_glTF', 'KHR_draco_mesh_compression', 'KHR_lights_punctual', 'KHR_materials_anisotropy',
  'KHR_materials_clearcoat', 'KHR_materials_dispersion', 'KHR_materials_emissive_strength', 'KHR_materials_ior',
  'KHR_materials_iridescence', 'KHR_materials_sheen', 'KHR_materials_specular', 'KHR_materials_transmission',
  'KHR_materials_unlit', 'KHR_materials_variants', 'KHR_materials_volume', 'KHR_mesh_quantization',
  'KHR_texture_basisu', 'KHR_texture_transform', 'EXT_materials_bump', 'EXT_mesh_gpu_instancing',
  'EXT_meshopt_compression', 'KHR_meshopt_compression', 'EXT_texture_avif', 'EXT_texture_webp']);

export type Json = Record<string, unknown>;
export const isRecord = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value);
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const isIndex = (value: unknown, length: number): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 0 && value < length;

/** A readable sentence for anything a loader rejects with. Not every rejection is an Error: three's DRACOLoader rejects
 *  with its worker's message object ({type: 'error', id, error: '<the decoder's message>'}), and image loaders reject
 *  with DOM events. An Error gives its message (its name when the message is empty); a string itself; a record its
 *  string `error` or `message` field (or that field's Error message); anything else its JSON, else String(value). The
 *  reason must never be lost to "[object Object]" (owner rule: never fail silently). */
export function describeError(error: unknown): string {
  if (error instanceof Error) return error.message || error.name;
  if (typeof error === 'string') return error;
  if (isRecord(error)) {
    for (const key of ['error', 'message'] as const) {
      const value = error[key];
      if (typeof value === 'string' && value) return value;
      if (value instanceof Error) return value.message || value.name;
    }
  }
  try {
    const text = JSON.stringify(error);
    if (text !== undefined && text !== '{}') return text;
  } catch {/* cyclic or not serialisable: String() below */}
  const text = String(error);
  return text === '[object Object]' && error !== null && typeof error === 'object' ? `${error.constructor?.name ?? 'object'} with no message` : text;
}

/** The file is not a readable GLB. The message is the §2 Format detail. */
export class GlbFormatError extends Error {
  constructor(message: string) {super(message); this.name = 'GlbFormatError';}
}

export interface GlbContainer {
  readonly json: Json;
  /** The BIN chunk, or null when the file has none. */
  readonly bin: Uint8Array | null;
  readonly byteLength: number;
}

/** Split a GLB into its JSON and BIN chunks, as `glb.split_chunks` does (same refusals). */
export function readGlbContainer(bytes: ArrayBuffer): GlbContainer {
  const data = new Uint8Array(bytes), view = new DataView(bytes);
  if (data.length < 20 || view.getUint32(0, true) !== GLB_MAGIC) throw new GlbFormatError("not a GLB file (missing 'glTF' magic)");
  const version = view.getUint32(4, true), length = view.getUint32(8, true);
  if (version !== 2) throw new GlbFormatError(`GLB version ${version}, expected 2`);
  if (length !== data.length) throw new GlbFormatError(`GLB header length ${length} != file size ${data.length}`);
  let offset = 12, text: string | null = null, bin: Uint8Array | null = null;
  while (offset + 8 <= data.length) {
    const chunkLength = view.getUint32(offset, true), type = view.getUint32(offset + 4, true);
    if (offset + 8 + chunkLength > data.length) throw new GlbFormatError('truncated GLB chunk');
    const body = data.subarray(offset + 8, offset + 8 + chunkLength);
    if (type === CHUNK_JSON && text === null) {
      if (body[0] === 0xef && body[1] === 0xbb && body[2] === 0xbf) throw new GlbFormatError('the GLB JSON chunk has a forbidden UTF-8 BOM');
      try {text = new TextDecoder('utf-8', {fatal: true}).decode(body);}
      catch {throw new GlbFormatError('the GLB JSON chunk is not UTF-8');}
    } else if (type === CHUNK_BIN && bin === null) bin = body;
    offset += 8 + chunkLength;
  }
  if (text === null) throw new GlbFormatError('GLB has no JSON chunk');
  let json: unknown;
  const materialSpellings = new WeakMap<object, string>();
  // Python retains the distinction between JSON integer and float index spellings. JS normally erases it:
  // preserve a malformed material index as its literal text so it fails the same Format check (never rounds to 0).
  try {json = JSON.parse(text, function(this: object, key: string, value: unknown, context?: {source?: string}) {
    if (key === 'material' && typeof value === 'number' && /[.eE]/.test(context?.source ?? '')) materialSpellings.set(this, context!.source!);
    return value;
  });}
  catch (error) {throw new GlbFormatError(`the GLB JSON chunk is not valid JSON (${describeError(error)})`);}
  if (!isRecord(json)) throw new GlbFormatError('GLB JSON chunk is not an object');
  for (const mesh of list(json.meshes)) {
    for (const primitive of isRecord(mesh) ? list(mesh.primitives) : []) {
      if (isRecord(primitive) && materialSpellings.has(primitive)) primitive.material = materialSpellings.get(primitive);
    }
  }
  return {json, bin, byteLength: data.length};
}

/** Why the document has no usable default scene, or null (glb.py `scene_issue`; arview refuses these files). */
export function sceneIssue(json: Json): string | null {
  const scenes = json.scenes;
  if (!Array.isArray(scenes) || scenes.length === 0) return 'the document has no scenes (glTF viewers show nothing)';
  const index = 'scene' in json ? json.scene : 0;
  if (!isIndex(index, scenes.length)) return `default scene ${JSON.stringify(index)} does not exist (${scenes.length} scene(s))`;
  const scene = scenes[index];
  if (!isRecord(scene) || !Array.isArray(scene.nodes) || scene.nodes.length === 0) return `the default scene (${index}) has no nodes`;
  return null;
}

export interface ExternalResource {readonly kind: 'buffer' | 'image'; readonly index: number; readonly uri: string;}
/** Schemes (including drive letters) and network-path URIs never name a resource next to the model. */
export function isAbsoluteUri(uri: string): boolean {
  return /^[a-z][a-z0-9+.-]*:/i.test(uri) || uri.startsWith('//') || uri.startsWith('\\\\');
}
/** Buffers and images stored outside the file (a uri that is not a data: URI). */
export function externalResources(json: Json): ExternalResource[] {
  const found: ExternalResource[] = [];
  for (const kind of ['buffer', 'image'] as const) {
    list(json[kind === 'buffer' ? 'buffers' : 'images']).forEach((item, index) => {
      const uri = isRecord(item) ? item.uri : undefined;
      if (typeof uri === 'string' && uri !== '' && !uri.startsWith('data:')) found.push({kind, index, uri});
    });
  }
  return found;
}

/** Image sources a texture can name: its own `source` and the source of each texture extension three understands. */
export const TEXTURE_SOURCE_EXTENSIONS = Object.freeze(['KHR_texture_basisu', 'EXT_texture_webp', 'EXT_texture_avif'] as const);
const MESHOPT_VIEW_EXTENSIONS = ['EXT_meshopt_compression', 'KHR_meshopt_compression'] as const;

/** A default-scene primitive whose `material` names no material object (spec.py fails the Format row for it, unless the
 *  value is null). defaultSceneDocument hands it to three without the reference: it draws with the glTF default material. */
export interface DanglingMaterial {
  readonly node: number;
  readonly nodeName: string;
  readonly mesh: number;
  readonly primitive: number;
  readonly material: unknown;
}
/** A texture reference of a drawn material that three cannot load (it would throw inside the material, ending the
 *  parse). defaultSceneDocument removes the reference from the material three is handed: the material draws without
 *  that texture. `kind` 'texture': the texture index itself does not exist (spec.py fails the Format row for it, as
 *  load.ts inspectTextures does); 'source': the texture exists but its image source cannot be loaded (spec.py does not
 *  check image sources; arview fails the Format row for it). */
export interface DanglingTexture {
  readonly material: number;
  readonly materialName: string;
  readonly path: string;
  readonly texture: unknown;
  readonly kind: 'texture' | 'source';
  readonly reason: string;
}

export interface LoadedReferences {
  /** The first reference on the loaded path that does not resolve and cannot be left out, or null (the §2 Format
   *  refusal detail). */
  readonly problem: string | null;
  /** The nodes of the default scene's tree (the only nodes arview parses: defaultSceneDocument). */
  readonly nodes: ReadonlySet<number>;
  /** The buffers and images the loaded path reaches (the default scene's primitives and their materials). */
  readonly buffers: ReadonlySet<number>;
  readonly images: ReadonlySet<number>;
  /** Broken material and texture references on the loaded path that arview leaves out instead of refusing the file
   *  (WP1_DESIGN.md §1: only an unreadable GLB, a missing external resource or a file with no scene is a Format
   *  refusal; a texture that cannot be loaded is a reported fallback). */
  readonly danglingMaterials: readonly DanglingMaterial[];
  readonly danglingTextures: readonly DanglingTexture[];
}

/** Check the references three's GLTFLoader dereferences without checking, on the loaded path only: the default scene's
 *  node tree (a node reachable twice is reported as glb.py does), its primitives' materials and accessors, the buffer
 *  views and buffers those reach (Draco and meshopt included), and the used materials' textures and images. A broken
 *  node, mesh, accessor, buffer view or buffer reference there makes three throw a TypeError, so it is refused with a
 *  precise message. A broken material or texture reference is not refused: it is listed (danglingMaterials,
 *  danglingTextures), and defaultSceneDocument leaves it out of what three parses. Unused entries are never
 *  dereferenced and never refused (spec.py passes them too). A primitive without POSITION is valid glTF (clients skip
 *  it): it is not refused here; the normalisation pass fails the Format row for it and does not draw it. */
export function loadedReferences(json: Json, hasBin: boolean, readBuffer?: (index: number) => Uint8Array | null): LoadedReferences {
  const nodes = list(json.nodes), meshes = list(json.meshes), materials = list(json.materials), accessors = list(json.accessors);
  const views = list(json.bufferViews), buffers = list(json.buffers), textures = list(json.textures), images = list(json.images);
  const reachedBuffers = new Set<number>(), reachedImages = new Set<number>(), usedMaterials = new Set<number>();
  const danglingMaterials: DanglingMaterial[] = [], danglingTextures: DanglingTexture[] = [];
  const checkBuffer = (index: unknown, where: string): string | null => {
    if (!isIndex(index, buffers.length)) return `${where} refers to buffer ${JSON.stringify(index)}, which does not exist`;
    const buffer = buffers[index];
    if (!isRecord(buffer)) return `buffer ${index} is not an object`;
    if (buffer.uri === undefined && index !== 0) return `buffer ${index} has no uri and is not the GLB BIN chunk`;
    if (buffer.uri === undefined && !hasBin) return 'buffer 0 refers to the GLB BIN chunk, which the file does not have';
    if (buffer.uri !== undefined && typeof buffer.uri !== 'string') return `buffer ${index} uri is not a string`;
    reachedBuffers.add(index);
    return null;
  };
  const checkView = (index: unknown, where: string): string | null => {
    if (!isIndex(index, views.length)) return `${where} refers to bufferView ${JSON.stringify(index)}, which does not exist`;
    const view = views[index];
    if (!isRecord(view)) return `bufferView ${index} is not an object`;
    // A meshopt view is read from its extension's buffer; its own `buffer` is the (often uri-less) fallback three skips.
    const extensions = isRecord(view.extensions) ? view.extensions : {};
    const meshopt = MESHOPT_VIEW_EXTENSIONS.find(name => isRecord(extensions[name]));
    if (meshopt) {
      if (!isIndex(view.buffer, buffers.length)) return `bufferView ${index} refers to buffer ${JSON.stringify(view.buffer)}, which does not exist`;
      return checkBuffer((extensions[meshopt] as Json).buffer, `bufferView ${index} ${meshopt}`);
    }
    const problem = checkBuffer(view.buffer, `bufferView ${index}`);
    if (problem) return problem;
    if (readBuffer) {
      const data = readBuffer(view.buffer as number), start = view.byteOffset ?? 0, length = view.byteLength;
      if (typeof start !== 'number' || !Number.isInteger(start) || start < 0
          || typeof length !== 'number' || !Number.isInteger(length) || length < 0) return `bufferView ${index} has invalid byte bounds`;
      if (data && start + length > data.length) return `bufferView ${index} runs past the end of its buffer`;
    }
    return null;
  };
  const checkAccessor = (index: unknown, where: string): string | null => {
    if (!isIndex(index, accessors.length)) return `${where}: accessor ${JSON.stringify(index)} does not exist`;
    const accessor = accessors[index];
    if (!isRecord(accessor)) return `${where}: accessor ${index} is not an object`;
    if (accessor.bufferView !== undefined) {const problem = checkView(accessor.bufferView, `accessor ${index}`); if (problem) return problem;}
    if (isRecord(accessor.sparse)) {
      for (const part of ['indices', 'values'] as const) {
        const sparse = accessor.sparse[part];
        const problem = checkView(isRecord(sparse) ? sparse.bufferView : undefined, `accessor ${index} sparse ${part}`); if (problem) return problem;
      }
    }
    return null;
  };
  const checkImage = (index: unknown, where: string): string | null => {
    if (!isIndex(index, images.length)) return `${where} refers to image ${JSON.stringify(index)}, which does not exist`;
    const image = images[index];
    if (!isRecord(image)) return `image ${index} is not an object`;
    if (image.bufferView !== undefined) {const problem = checkView(image.bufferView, `image ${index}`); if (problem) return problem;}
    else if (typeof image.uri !== 'string') return `image ${index} has neither a uri nor a bufferView`;
    reachedImages.add(index);
    return null;
  };
  const seen = new Set<number>();
  const result = (problem: string | null): LoadedReferences => ({problem, nodes: seen, buffers: reachedBuffers, images: reachedImages,
    danglingMaterials, danglingTextures});

  const scenes = list(json.scenes), sceneIndex = json.scene ?? 0, scene = isIndex(sceneIndex, scenes.length) ? scenes[sceneIndex] : undefined;
  const roots = isRecord(scene) ? list(scene.nodes) : [];
  const stack = [...roots].reverse();
  while (stack.length) {
    const index = stack.pop();
    if (!isIndex(index, nodes.length)) return result(`node index ${JSON.stringify(index)} does not exist`);
    if (seen.has(index)) return result(`node ${index} is reachable twice (cycle or shared child)`);
    seen.add(index);
    const node = nodes[index];
    if (!isRecord(node)) return result(`node ${index} is not an object`);
    if (node.mesh !== undefined) {
      if (!isIndex(node.mesh, meshes.length) || !isRecord(meshes[node.mesh])) return result(`node ${index} references mesh ${JSON.stringify(node.mesh)}, which does not exist`);
      for (const [primitiveIndex, primitive] of list((meshes[node.mesh] as Json).primitives).entries()) {
        const where = `node ${JSON.stringify(node.name ?? index)} primitive ${primitiveIndex}`;
        if (!isRecord(primitive)) return result(`${where} is not an object`);
        if (primitive.material !== undefined) {
          // A material index that names no material object: three would dereference it blindly. Left out (drawn with
          // the default material) and reported, as spec.py fails only the Format row for it.
          if (isIndex(primitive.material, materials.length) && isRecord(materials[primitive.material])) usedMaterials.add(primitive.material);
          else {
            danglingMaterials.push({node: index, nodeName: typeof node.name === 'string' ? node.name : `node${index}`, mesh: node.mesh as number,
              primitive: primitiveIndex, material: primitive.material});
          }
        }
        for (const [semantic, accessor] of Object.entries(isRecord(primitive.attributes) ? primitive.attributes : {})) {
          const problem = checkAccessor(accessor, `${where} ${semantic}`); if (problem) return result(problem);
        }
        if (primitive.indices !== undefined) {const problem = checkAccessor(primitive.indices, `${where} indices`); if (problem) return result(problem);}
        for (const [targetIndex, target] of list(primitive.targets).entries()) {
          if (!isRecord(target)) return result(`${where} morph target ${targetIndex} is not an object`);
          for (const [semantic, accessor] of Object.entries(target)) {
            const problem = checkAccessor(accessor, `${where} morph target ${targetIndex} ${semantic}`); if (problem) return result(problem);
          }
        }
        const draco = isRecord(primitive.extensions) ? primitive.extensions.KHR_draco_mesh_compression : undefined;
        if (isRecord(draco)) {const problem = checkView(draco.bufferView, `${where} KHR_draco_mesh_compression`); if (problem) return result(problem);}
      }
    }
    if (node.children !== undefined && !Array.isArray(node.children)) return result(`node ${index} children is not a list`);
    for (const child of [...list(node.children)].reverse()) stack.push(child);
  }
  for (const index of [...usedMaterials].sort((a, b) => a - b)) {
    const material = materials[index] as Json;
    const materialName = typeof material.name === 'string' ? material.name : `material${index}`;
    const dangling = (path: string, texture: unknown, kind: DanglingTexture['kind'], reason: string): void => {
      danglingTextures.push({material: index, materialName, path, texture, kind, reason});
    };
    for (const {path, info} of textureInfos(material)) {
      if (!isIndex(info.index, textures.length)) {dangling(path, info.index, 'texture', `texture ${JSON.stringify(info.index)} does not exist`); continue;}
      const texture = textures[info.index];
      if (!isRecord(texture)) {dangling(path, info.index, 'source', `texture ${info.index} is not an object`); continue;}
      const extensions = isRecord(texture.extensions) ? texture.extensions : {};
      const sources: [unknown, string][] = texture.source !== undefined ? [[texture.source, `texture ${info.index}`]] : [];
      for (const name of TEXTURE_SOURCE_EXTENSIONS) {
        const extension = extensions[name];
        if (isRecord(extension) && extension.source !== undefined) sources.push([extension.source, `texture ${info.index} ${name}`]);
      }
      if (!sources.length) {dangling(path, info.index, 'source', `texture ${info.index} has no image source`); continue;}
      // Any broken source: three reads every source it is offered without checking (the core loader and the
      // KHR_texture_basisu / EXT_texture_webp / EXT_texture_avif plugins), so the whole reference is left out.
      const problems = sources.flatMap(([source, where]) => {const problem = checkImage(source, where); return problem ? [problem] : [];});
      if (problems.length) dangling(path, info.index, 'source', problems.join('; '));
    }
  }
  return result(null);
}

export interface DefaultSceneDocument {
  /** The JSON three parses: the default scene (as scene 0) and its node tree, without what arview never draws. Every
   *  array keeps its length and order, so node, mesh, material and texture indices (three's associations, the report's
   *  names, lens identification) are those of the original file. */
  readonly json: Json;
  /** What was left out that the normalisation pass does not report itself, as warning sentences. */
  readonly ignored: readonly string[];
  /** Default-scene nodes whose KHR_lights_punctual light was left out (three would have lit the model with it). */
  readonly lights: number;
}

/** The part of the document arview draws (WP1_DESIGN.md §1: nothing outside §2 blocks a render). three's GLTFLoader
 *  `parse` loads every scene, animation, camera and light, and dereferences their indices without checking, so a broken
 *  reference anywhere (a second scene's node naming a mesh that does not exist, an animation channel naming a missing
 *  node, a camera, skin or light index on a node) would end the load with a TypeError although spec.py passes the file.
 *  Only the default scene's node tree is kept (`reached`, from loadedReferences, whose references are all checked);
 *  every other node becomes an empty object, and other scenes, animations, cameras and skins are dropped. Node-level
 *  camera, skin, light and EXT_mesh_gpu_instancing references are removed: cameras and lights are not drawn (arview
 *  has its own camera and lights); skins and instancing are §2 refusals that the normalisation pass decides from the
 *  ORIGINAL JSON, which also reports animations and declared skins (the Geometry row, as spec.py). The broken material
 *  and texture references loadedReferences lists are removed from the copies three parses (a primitive draws with the
 *  glTF default material, a material without that texture); load.ts reports each. The original bytes stay the
 *  identity: the report's SHA-256 is theirs. */
export function defaultSceneDocument(json: Json, references: Pick<LoadedReferences, 'nodes' | 'danglingMaterials' | 'danglingTextures'>): DefaultSceneDocument {
  const reached = references.nodes;
  const scenes = list(json.scenes), nodes = list(json.nodes), meshes = list(json.meshes);
  const sceneIndex = isIndex(json.scene ?? 0, scenes.length) ? (json.scene ?? 0) as number : 0;
  let cameraNodes = 0, lights = 0;
  const shapeless = new Set<number>();
  const prunedNodes = nodes.map((node, index): Json => {
    if (!reached.has(index) || !isRecord(node)) return {};
    const copy: Json = {...node};
    if (copy.camera !== undefined) {delete copy.camera; cameraNodes++;}
    delete copy.skin;
    if (isRecord(copy.extensions)) {
      const extensions: Json = {...copy.extensions};
      if (extensions.KHR_lights_punctual !== undefined) {delete extensions.KHR_lights_punctual; lights++;}
      delete extensions[INSTANCING_EXTENSION];
      copy.extensions = extensions;
    }
    if (isIndex(copy.mesh, meshes.length) && isRecord(meshes[copy.mesh]) && !Array.isArray((meshes[copy.mesh] as Json).primitives)) shapeless.add(copy.mesh);
    return copy;
  });
  // Primitives whose material does not exist lose the reference (three then draws the glTF default material).
  const withoutMaterial = new Map<number, Set<number>>();
  for (const entry of references.danglingMaterials) withoutMaterial.set(entry.mesh, (withoutMaterial.get(entry.mesh) ?? new Set()).add(entry.primitive));
  const prunedMeshes = meshes.map((mesh, index) => {
    if (shapeless.has(index)) return {...(mesh as Json), primitives: []};
    const primitives = withoutMaterial.get(index);
    if (!primitives) return mesh;
    return {...(mesh as Json), primitives: list((mesh as Json).primitives).map((primitive, primitiveIndex) => {
      if (!primitives.has(primitiveIndex) || !isRecord(primitive)) return primitive;
      const copy: Json = {...primitive}; delete copy.material; return copy;
    })};
  });
  // Texture references three cannot load are removed from (copies of) their materials.
  const materials = list(json.materials), prunedMaterials = new Map<number, Json>();
  for (const entry of references.danglingTextures) {
    let copy = prunedMaterials.get(entry.material);
    if (!copy) {copy = structuredClone(materials[entry.material]) as Json; prunedMaterials.set(entry.material, copy);}
    const keys = entry.path.split('.'), last = keys.pop()!;
    let holder: unknown = copy;
    for (const key of keys) holder = isRecord(holder) || Array.isArray(holder) ? (holder as Record<string, unknown>)[key] : undefined;
    if (isRecord(holder)) delete holder[last];
  }
  const pruned: Json = {...json, scene: 0, scenes: [scenes[sceneIndex]], nodes: prunedNodes, meshes: prunedMeshes};
  if (prunedMaterials.size) pruned.materials = materials.map((material, index) => prunedMaterials.get(index) ?? material);
  if (json.nodes === undefined) delete pruned.nodes;
  if (json.meshes === undefined) delete pruned.meshes;
  delete pruned.animations; delete pruned.cameras; delete pruned.skins;

  const ignored: string[] = [];
  const otherScenes = scenes.length - 1, outside = nodes.length - reached.size;
  if (otherScenes > 0 || outside > 0) {
    ignored.push(`Ignored: ${[otherScenes > 0 ? `${otherScenes} scene(s) besides the default scene (${sceneIndex})` : '',
      outside > 0 ? `${outside} node(s) outside the default scene` : ''].filter(Boolean).join(' and ')}; arview draws only the default scene `
      + 'and never reads the rest.');
  }
  const cameras = list(json.cameras).length;
  if (cameras > 0 || cameraNodes > 0) {
    ignored.push(`Ignored: the file's camera(s) (${cameras} defined, ${cameraNodes} on default-scene nodes); arview views every model through its own camera.`);
  }
  if (shapeless.size) {
    const names = [...shapeless].sort((a, b) => a - b).map(index => {const mesh = meshes[index] as Json; return typeof mesh.name === 'string' ? mesh.name : `mesh${index}`;});
    ignored.push(`mesh(es) ${JSON.stringify(names.slice(0, 8))} of the default scene have no primitives list (invalid glTF): they draw nothing.`);
  }
  return {json: pruned, ignored, lights};
}

const align4 = (value: number): number => (value + 3) & ~3;

/** A GLB container (version 2) holding `json` and the BIN chunk `bin`, each chunk padded to 4 bytes as the GLB
 *  specification requires (JSON with spaces, BIN with zeros). */
export function packGlb(json: Json, bin: Uint8Array | null): ArrayBuffer {
  const text = new TextEncoder().encode(JSON.stringify(json));
  const jsonLength = align4(text.length), binLength = bin ? align4(bin.length) : 0;
  const total = 20 + jsonLength + (bin ? 8 + binLength : 0);
  const out = new Uint8Array(total), view = new DataView(out.buffer);
  view.setUint32(0, GLB_MAGIC, true); view.setUint32(4, 2, true); view.setUint32(8, total, true);
  view.setUint32(12, jsonLength, true); view.setUint32(16, CHUNK_JSON, true);
  out.set(text, 20); out.fill(0x20, 20 + text.length, 20 + jsonLength);
  if (bin) {
    const at = 20 + jsonLength;
    view.setUint32(at, binLength, true); view.setUint32(at + 4, CHUNK_BIN, true); out.set(bin, at + 8);
  }
  return out.buffer;
}

/** (json path, textureInfo) for every texture reference inside a material, as spec.py `_texture_infos`. */
export function textureInfos(material: unknown, path = ''): {path: string; info: Json}[] {
  const found: {path: string; info: Json}[] = [];
  if (isRecord(material)) {
    for (const [key, value] of Object.entries(material)) {
      if (key.endsWith('Texture') && isRecord(value) && 'index' in value) found.push({path: path + key, info: value});
      else found.push(...textureInfos(value, `${path}${key}.`));
    }
  } else if (Array.isArray(material)) material.forEach((value, index) => found.push(...textureInfos(value, `${path}${index}.`)));
  return found;
}

function base64Bytes(uri: string): Uint8Array {
  const text = atob(uri.slice(uri.indexOf(',') + 1));
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
  return bytes;
}

/** The bytes of a buffer: the BIN chunk, a data: URI, or a prefetched external resource; null when missing. */
export function bufferBytes(json: Json, bin: Uint8Array | null, index: number, external: ReadonlyMap<string, Uint8Array>): Uint8Array | null {
  const buffer = list(json.buffers)[index];
  if (!isRecord(buffer)) return null;
  const uri = buffer.uri;
  if (uri === undefined) return index === 0 ? bin : null;
  if (typeof uri !== 'string') return null;
  if (uri.startsWith('data:')) return base64Bytes(uri);
  return external.get(uri) ?? null;
}

/** The encoded bytes of image `index`, null when its external file is missing; throws GlbFormatError on a view that
 *  runs past its buffer (spec.py fails the format row then). */
export function imageBytes(json: Json, bin: Uint8Array | null, index: number, external: ReadonlyMap<string, Uint8Array>): Uint8Array | null {
  const image = list(json.images)[index];
  if (!isRecord(image)) return null;
  if (image.bufferView !== undefined) {
    const view = list(json.bufferViews)[image.bufferView as number];
    if (!isRecord(view)) throw new GlbFormatError(`bufferView ${String(image.bufferView)} does not exist`);
    const data = bufferBytes(json, bin, view.buffer as number, external);
    if (!data) throw new GlbFormatError(`buffer ${String(view.buffer)} of image ${index} is missing`);
    const start = typeof view.byteOffset === 'number' ? view.byteOffset : 0, length = typeof view.byteLength === 'number' ? view.byteLength : 0;
    if (start + length > data.length) throw new GlbFormatError(`bufferView ${String(image.bufferView)} runs past the end of its buffer`);
    return data.subarray(start, start + length);
  }
  const uri = image.uri;
  if (typeof uri !== 'string' || uri === '') return null;
  if (uri.startsWith('data:')) return base64Bytes(uri);
  return external.get(uri) ?? null;
}

const KTX2_IDENTIFIER = [0xab, 0x4b, 0x54, 0x58, 0x20, 0x32, 0x30, 0xbb, 0x0d, 0x0a, 0x1a, 0x0a];

/** (width, height) from PNG, JPEG, WebP (VP8, VP8L, VP8X) or KTX2 header bytes, or null when unrecognised
 *  (glb.py `image_size`, plus KTX2, which arview decodes). */
export function imageSize(data: Uint8Array | null): [number, number] | null {
  if (!data || data.length === 0) return null;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const ascii = (start: number, length: number) => String.fromCharCode(...data.subarray(start, start + length));
  if (data.length >= 24 && data[0] === 0x89 && ascii(1, 3) === 'PNG' && data[4] === 0x0d && data[5] === 0x0a && data[6] === 0x1a && data[7] === 0x0a) {
    return [view.getUint32(16, false), view.getUint32(20, false)];
  }
  if (data.length >= 30 && ascii(0, 4) === 'RIFF' && ascii(8, 4) === 'WEBP') {
    const chunk = ascii(12, 4);
    if (chunk === 'VP8 ' && data[23] === 0x9d && data[24] === 0x01 && data[25] === 0x2a) {
      return [view.getUint16(26, true) & 0x3fff, view.getUint16(28, true) & 0x3fff];
    }
    if (chunk === 'VP8L' && data[20] === 0x2f) {
      const bits = view.getUint32(21, true);
      return [(bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1];
    }
    if (chunk === 'VP8X') return [(data[24]! | data[25]! << 8 | data[26]! << 16) + 1, (data[27]! | data[28]! << 8 | data[29]! << 16) + 1];
    return null;
  }
  if (data.length >= 28 && KTX2_IDENTIFIER.every((value, index) => data[index] === value)) {
    return [view.getUint32(20, true), Math.max(1, view.getUint32(24, true))];
  }
  if (data[0] === 0xff && data[1] === 0xd8) {
    let i = 2;
    while (i + 9 < data.length) {
      if (data[i] !== 0xff) {i++; continue;}
      const marker = data[i + 1]!;
      if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {i += 2; continue;}
      const segment = view.getUint16(i + 2, false);
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
        return [view.getUint16(i + 7, false), view.getUint16(i + 5, false)];
      }
      i += 2 + segment;
    }
  }
  return null;
}

/** The primitives of the default scene as the JSON lists them, depth first (glb.py order). */
export interface JsonPrimitive {
  readonly node: number;
  readonly nodeName: string;
  readonly mesh: number;
  readonly primitive: number;
  readonly mode: number;
  readonly material: number | null;
  readonly attributes: readonly string[];
  readonly compressed: string | null;
  readonly morphTargets: boolean;
}
export function jsonPrimitives(json: Json): JsonPrimitive[] {
  const nodes = list(json.nodes), meshes = list(json.meshes);
  const scenes = list(json.scenes), sceneIndex = json.scene ?? 0, scene = isIndex(sceneIndex, scenes.length) ? scenes[sceneIndex] : undefined;
  const stack = [...(isRecord(scene) ? list(scene.nodes) : [])].reverse(), seen = new Set<number>(), found: JsonPrimitive[] = [];
  while (stack.length) {
    const index = stack.pop();
    if (!isIndex(index, nodes.length) || seen.has(index)) continue;
    seen.add(index);
    const node = nodes[index];
    if (!isRecord(node)) continue;
    if (isIndex(node.mesh, meshes.length) && isRecord(meshes[node.mesh])) {
      list((meshes[node.mesh] as Json).primitives).forEach((primitive, primitiveIndex) => {
        const row = isRecord(primitive) ? primitive : {};
        const extensions = isRecord(row.extensions) ? row.extensions : {};
        found.push({node: index, nodeName: typeof node.name === 'string' ? node.name : `node${index}`, mesh: node.mesh as number,
          primitive: primitiveIndex, mode: typeof row.mode === 'number' ? row.mode : 4,
          material: typeof row.material === 'number' ? row.material : null,
          attributes: Object.keys(isRecord(row.attributes) ? row.attributes : {}).sort(),
          compressed: COMPRESSION_EXTENSIONS.find(name => name in extensions) ?? null, morphTargets: list(row.targets).length > 0});
      });
    }
    for (const child of [...list(node.children)].reverse()) stack.push(child);
  }
  return found;
}
