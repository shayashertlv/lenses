/** The decided lens/frame class of a loaded asset's materials (WP1_DESIGN.md §2).
 *
 *  classifyAssetMaterials runs once at load. It reads the extras from the glTF JSON (`gltf.parser.json`, mapped to the
 *  three objects through `gltf.parser.associations`), never three's merged userData, and applies the rule order of
 *  lens-identification.ts per use (node x primitive). The class is then fixed: it is never re-derived from a material's
 *  live transmission. A material used by both lens and frame primitives is cloned per class (so both render) and the §2
 *  Components row fails; frame materials shared by a compact authored front and arms are separated as before, without
 *  changing any PBR property. Every fallback is a warning in the returned classification. */
import {Mesh, MeshPhysicalMaterial} from 'three';
import type {Material, Object3D} from 'three';
import {buildLensReport, classifyUses, identifyGltfJson, useKey} from './lens-identification.ts';
import type {LensDecision, LensIdentificationReport, PartClass, ReportMaterial, ReportUseInput, SignalSite, UseSignals} from './lens-identification.ts';
import {materialClassOf, meshRecordOf, setMaterialClass, setMeshRecord} from './part-class.ts';
import {partVisibilityOf, validatePartVisibility} from './part-visibility.ts';
export {materialClassOf, meshClassOf} from './part-class.ts';
export type {LensIdentificationReport, PartClass} from './lens-identification.ts';

/** The retired canonical lens descriptor, a glTF material extension that only the retired bsa/reconstruction routes
 *  wrote (no Studio GLB carries it). arview has no canonical lens runtime (WP1 stage B dropped it with
 *  lens-appearance.ts, whose only remaining job would have been this detection): a material that still carries the
 *  descriptor has it stripped at load and is drawn by its own glTF material, which is the descriptor's fallback. */
export const RETIRED_LENS_APPEARANCE_EXTENSION = 'LENSES_lens_appearance';

/** Remove the retired descriptor from every material under `root` (GLTFLoader keeps unknown material extensions in
 *  `material.userData.gltfExtensions`). Returns the names of the materials stripped, once each, so the caller can
 *  report every strip as a warning; nothing else is changed. */
export function stripRetiredLensAppearance(root: Object3D): string[] {
  const stripped: string[] = [], seen = new Set<Material>();
  root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    for (const material of (Array.isArray(object.material) ? object.material : [object.material]) as Material[]) {
      if (seen.has(material)) continue;
      seen.add(material);
      const extensions: unknown = material.userData?.gltfExtensions;
      if (!extensions || typeof extensions !== 'object' || !Object.hasOwn(extensions, RETIRED_LENS_APPEARANCE_EXTENSION)) continue;
      const remaining = extensions as Record<string, unknown>;
      delete remaining[RETIRED_LENS_APPEARANCE_EXTENSION];
      if (!Object.keys(remaining).length) delete material.userData.gltfExtensions;
      stripped.push(material.name || '(unnamed material)');
    }
  });
  return stripped;
}

/** GLTFLoader's record of which glTF element produced a three object (`gltf.parser.associations`). */
export interface GltfAssociation {readonly nodes?: number; readonly meshes?: number; readonly primitives?: number; readonly materials?: number;}
/** The glTF source of a loaded scene: the parser's JSON and its associations. GLTFLoader's `GLTF` and its `parser`
 *  both satisfy it through gltfSourceOf. */
export interface GltfSource {readonly json: unknown; readonly associations: ReadonlyMap<unknown, GltfAssociation>;}
type GltfLike = GltfSource | {readonly parser: {readonly json: unknown; readonly associations: ReadonlyMap<unknown, unknown>}};
export function gltfSourceOf(gltf: GltfLike): GltfSource {
  return 'parser' in gltf ? {json: gltf.parser.json, associations: gltf.parser.associations as ReadonlyMap<unknown, GltfAssociation>} : gltf;
}

export interface AssetMaterialClassification {
  /** Transmissive materials of the frame class: rendered by Three's physical transmission, never optical. */
  readonly translucentFrameMaterials: readonly Material[];
  /** Original materials that were separated into more than one instance (lens/frame, front/arm or per profiled side). */
  readonly sharedMaterials: readonly Material[];
  /** The lens_identification report block: one entry per glTF material. */
  readonly lensIdentification: LensIdentificationReport;
  /** Every warning of the lens identification, for the model report: the identification's own (conflicting roles,
   *  last resort, Components failures) and unmapped meshes. */
  readonly warnings: readonly string[];
  /** Why the asset's LENSES_part_visibility profiles were ignored (one message per problem), or empty when they were
   *  used or absent. Ignoring them changes what is drawn (the positional path), so the model report records a
   *  fallback (model/normalize.ts). */
  readonly partVisibilityIgnored: readonly string[];
}

/** The existing front zone: supported shaft clipping/fading cannot start ahead of -30 mm. */
export const AUTHORED_FRAME_FRONT_MIN_Z_M = -.03;

/** The explicit role that decided a classified mesh (rule 1), or null: `partRole` or legacy `part_role`, its value and
 *  the extras site it came from. */
export function explicitRoleOf(object: Object3D): {key: 'partRole' | 'part_role'; value: string; site: SignalSite | null} | null {
  if (!(object instanceof Mesh)) return null;
  const decision = meshRecordOf(object)?.slots[0]?.decision;
  if (!decision || decision.rule !== 1 || typeof decision.value !== 'string') return null;
  return {key: decision.kind === 'partRole' ? 'partRole' : 'part_role', value: decision.value, site: decision.site};
}

/** Only a separately authored, baked front is exempt from arm visibility. Older `frame` tags can name an entire
 * frame including long arms, so the role alone is insufficient. The shaders use mesh-local metres; nonidentity
 * transforms inside the asset retain the positional rule rather than certifying an incorrectly positioned front.
 * The role is the decided one (classifyAssetMaterials): explicit `partRole` or legacy `part_role` equal to "frame" on
 * every slot; an unclassified mesh is never an authored front. */
export function isAuthoredFrontFrame(object: Object3D, root: Object3D): boolean {
  if (!(object instanceof Mesh)) return false;
  const profile = partVisibilityOf(object);
  if (profile) return profile.region === 'front';
  const slots = meshRecordOf(object)?.slots;
  if (!slots?.length || !slots.every(slot => slot.class === 'frame' && slot.decision.rule === 1
      && typeof slot.decision.value === 'string' && slot.decision.value.toLowerCase() === 'frame')) return false;
  const position = object.geometry.getAttribute('position');
  if (!position || position.itemSize < 3 || position.count === 0) return false;
  let reachedRoot = false;
  for (let current: Object3D | null = object; current; current = current.parent) {
    const identity = current.matrixAutoUpdate
      ? current.position.lengthSq() === 0 && current.quaternion.x === 0 && current.quaternion.y === 0
        && current.quaternion.z === 0 && current.quaternion.w === 1
        && current.scale.x === 1 && current.scale.y === 1 && current.scale.z === 1
      : current.matrix.elements.every((value, index) => value === (index % 5 === 0 ? 1 : 0));
    if (!identity) return false;
    if (current === root) {reachedRoot = true; break;}
  }
  if (!reachedRoot) return false;
  for (let i = 0; i < position.count; i++) {
    const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
    if (![x, y, z].every(Number.isFinite) || z < AUTHORED_FRAME_FRONT_MIN_Z_M) return false;
  }
  return true;
}

/** The central band no temple reaches: temples lie at |x| > 45 mm (WP1_DESIGN.md §3, temple-geometry.ts). */
export const POSITIONAL_FRONT_CENTRAL_HALF_WIDTH_M = 0.045;

/** On the positional path (no usable LENSES_part_visibility), a frame mesh that is front, never arm geometry, by its
 *  geometry alone. The temples are found as the non-lens geometry at |x| > 45 mm behind the lens rear (§3); that region
 *  also holds the outer back of a front mesh, and giving it the arm treatment removed the front's inner rim from the
 *  physical lens input (the Prada PR17WS stripped of its metadata: its one front mesh, z -9.9..3.9 mm, lies behind the
 *  -4.6 mm lens rear at both endpieces, and the cream inner bevel at the outer lens corners vanished, WP1 review E/F).
 *  A mesh is a positional front when every slot is frame class, its x extent reaches the central band |x| <= 45 mm (a front
 *  crossing x = 0, a rim or brow half: no temple lies there) and nothing of it lies behind the front zone
 *  (AUTHORED_FRAME_FRONT_MIN_Z_M, the bound an authored front keeps). A mesh that reaches the central band AND lies
 *  behind the front zone is front and temple in one mesh: it keeps the positional arm rule, and templeInventory warns.
 *  An unclassified mesh, a profiled mesh (its region decides), one with a lens slot or one whose explicit role (rule 1)
 *  is anything but 'frame' is never a positional front. */
export function isPositionalFrontFrame(object: Object3D): boolean {
  if (!(object instanceof Mesh) || partVisibilityOf(object)) return false;
  const slots = meshRecordOf(object)?.slots;
  // An explicit structural role other than 'frame' (temple, hardware, ...) is the author's word that this is not front.
  if (!slots?.length || !slots.every(slot => slot.class === 'frame' && (slot.decision.rule !== 1
      || typeof slot.decision.value === 'string' && slot.decision.value.trim().toLowerCase() === 'frame'))) return false;
  const position = object.geometry.getAttribute('position');
  if (!position || position.itemSize < 3 || position.count === 0) return false;
  let minX = Infinity, maxX = -Infinity;
  for (let i = 0; i < position.count; i++) {
    const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
    if (![x, y, z].every(Number.isFinite) || z < AUTHORED_FRAME_FRONT_MIN_Z_M) return false;
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
  }
  return reachesCentralBand(minX, maxX);
}

/** Whether an x extent [minX, maxX] (metres) reaches the central band |x| <= 45 mm: a mesh spanning it covers it even
 *  when no vertex lies inside (a coarse front is a box). */
export const reachesCentralBand = (minX: number, maxX: number): boolean =>
  minX <= POSITIONAL_FRONT_CENTRAL_HALF_WIDTH_M && maxX >= -POSITIONAL_FRONT_CENTRAL_HALF_WIDTH_M;

/** Whether `object` is front geometry that no arm treatment (near-temple overlay, observed-cheek blend, lens-input
 *  exclusion; the material front/arm split keeps them apart) may touch. (The shadow and part-id clips still wrap it: a
 *  front wholly at z >= -30 mm lies ahead of every drawable clip, validateTempleClip keeps cutoff + fade <= -30 mm.)
 *  It is: a profiled mesh by its region (isAuthoredFrontFrame), an unprofiled one when it
 *  is an authored compact front (rule-1 'frame' role) or a positional front (isPositionalFrontFrame). */
export function isFrontFramePart(object: Object3D, root: Object3D): boolean {
  if (!(object instanceof Mesh)) return false;
  if (partVisibilityOf(object)) return isAuthoredFrontFrame(object, root);
  return isAuthoredFrontFrame(object, root) || isPositionalFrontFrame(object);
}

interface Slot {readonly mesh: Mesh; readonly index: number; readonly material: Material;}
const slotMaterials = (mesh: Mesh): Material[] => Array.isArray(mesh.material) ? mesh.material : [mesh.material];

/** A hand-built scene's material as the glTF JSON rule 3 reads it (transmission, BLEND alpha). */
function materialJsonOf(material: Material): Record<string, unknown> {
  const transmission = material instanceof MeshPhysicalMaterial ? material.transmission : 0;
  return {...(transmission > 0 ? {extensions: {KHR_materials_transmission: {transmissionFactor: transmission}}} : {}),
    alphaMode: material.transparent ? 'BLEND' : 'OPAQUE', pbrMetallicRoughness: {baseColorFactor: [1, 1, 1, material.opacity]}};
}

/** UseSignals for a slot of a scene that has no glTF JSON (or a mesh the associations do not cover): the mesh's own
 *  userData as mesh extras, the immediate non-root Group parent (GLTFLoader's multi-primitive node) as node extras, the
 *  material's userData as material extras. A role on the model root or a distant ancestor never applies. */
function sceneUseSignals(slot: Slot, root: Object3D, key: string): UseSignals {
  const parent = slot.mesh.parent;
  const node = parent && parent !== root && !(parent instanceof Mesh) ? parent : null;
  return {key, nodeName: slot.mesh.name || node?.name || null, nodeExtras: node?.userData ?? null, meshExtras: slot.mesh.userData,
    materialName: slot.material.name || null, materialExtras: slot.material.userData, material: materialJsonOf(slot.material)};
}

/** The glTF use (node x primitive) a GLTFLoader primitive mesh came from, or null. */
function gltfUseKeyOf(mesh: Mesh, associations: ReadonlyMap<unknown, GltfAssociation>): string | null {
  const own = associations.get(mesh);
  if (!own || own.meshes === undefined) return null;
  const node = own.nodes ?? (mesh.parent ? associations.get(mesh.parent)?.nodes : undefined);
  return node === undefined ? null : useKey(node, own.primitives ?? 0);
}

type Owner = 'lens' | 'front' | 'arm' | 'temple_left' | 'temple_right';
const RETAIN_ORDER: readonly Owner[] = ['lens', 'front', 'arm', 'temple_left', 'temple_right'];

/** Classify every material of a loaded asset, once, before any consumer runs on that object graph. `gltf` is the
 *  GLTFLoader result (or its parser / a GltfSource) the scene came from; without it (hand-built scenes in tests) the
 *  scene's userData is read in its place and the report says so (`source: 'scene-userData'`). Idempotent: a second
 *  call creates no new material instances. */
export function classifyAssetMaterials(root: Object3D, gltf?: GltfLike | null): AssetMaterialClassification {
  const slots: Slot[] = [];
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    slotMaterials(object).forEach((material, index) => slots.push({mesh: object, index, material}));
  });
  const decided = new Map<Slot, LensDecision>(), extraWarnings: string[] = [];
  let report: LensIdentificationReport | null = null;
  const source = gltf ? gltfSourceOf(gltf) : null;
  const sceneSlots: Slot[] = [];
  let jsonHasLens = false;
  if (source) {
    const identified = identifyGltfJson(source.json);
    report = identified.report;
    // As on the Python side, only a lens found by rules 1-2 bars the last resort.
    jsonHasLens = [...identified.decisions.values()].some(decision => decision.isLens && (decision.rule === 1 || decision.rule === 2));
    for (const slot of slots) {
      const key = gltfUseKeyOf(slot.mesh, source.associations), decision = key === null ? undefined : identified.decisions.get(key);
      if (decision) decided.set(slot, decision); else sceneSlots.push(slot);
    }
  } else sceneSlots.push(...slots);
  if (sceneSlots.length) {
    const keys = new Map(sceneSlots.map((slot, index) => [slot, `scene:${index}`]));
    const signals = sceneSlots.map(slot => sceneUseSignals(slot, root, keys.get(slot)!));
    const label = (use: UseSignals) => `mesh "${sceneSlots[Number(use.key.slice(6))]!.mesh.name || '(unnamed mesh)'}"`;
    // The last resort is asset-level: a lens the glTF JSON already identifies bars it for unmapped meshes too.
    const {decisions, warnings: sceneWarnings} = classifyUses(jsonHasLens
      ? signals.map(signal => ({...signal, material: null})) : signals, label);
    for (const slot of sceneSlots) decided.set(slot, decisions.get(keys.get(slot)!)!);
    if (source) {
      for (const mesh of new Set(sceneSlots.map(slot => slot.mesh))) {
        extraWarnings.push(`Mesh "${mesh.name}" has no glTF node/primitive association, so its lens class was decided from `
          + "three's merged userData instead of the glTF JSON.");
      }
      extraWarnings.push(...sceneWarnings);
    } else {
      const materials: ReportMaterial[] = [], materialKeys = new Map<Material, string>();
      for (const slot of slots) if (!materialKeys.has(slot.material)) {
        const key = `m${materialKeys.size}`; materialKeys.set(slot.material, key);
        materials.push({key, index: null, name: slot.material.name || '(unnamed material)', json: materialJsonOf(slot.material)});
      }
      const rows: ReportUseInput[] = slots.map(slot => {
        const decision = decided.get(slot)!;
        return {key: keys.get(slot)!, materialKey: materialKeys.get(slot.material)!, decision, node: null,
          node_name: slot.mesh.name || '(unnamed mesh)', mesh: null, primitive: null,
          class: decision.isLens ? 'lens' : 'frame', rule: decision.rule, signal: decision.signal, site: decision.site};
      });
      report = buildLensReport('scene-userData', materials, rows, sceneWarnings);
    }
  }
  report ??= buildLensReport('scene-userData', [], [], []);
  if (extraWarnings.length) report = {...report, warnings: [...report.warnings, ...extraWarnings]};
  const warnings = [...report.warnings];
  // The decided class of every primitive mesh, before profile validation reads it.
  const meshSlots = new Map<Mesh, Slot[]>();
  for (const slot of slots) meshSlots.set(slot.mesh, [...meshSlots.get(slot.mesh) ?? [], slot]);
  for (const [mesh, own] of meshSlots) {
    setMeshRecord(mesh, {slots: own.map(slot => {
      const decision = decided.get(slot)!;
      return {class: decision.isLens ? 'lens' : 'frame', decision};
    })});
  }
  const partVisibilityIgnored: string[] = [];
  validatePartVisibility(root, message => partVisibilityIgnored.push(message));
  // Owners: the class first, then the structural split of frame materials (front / arm / profiled side).
  const ownership = new Map<Material, Map<Owner, Slot[]>>();
  for (const slot of slots) {
    const profile = partVisibilityOf(slot.mesh);
    const owner: Owner = decided.get(slot)!.isLens ? 'lens'
      : profile ? profile.region === 'front' || profile.region === 'lens' ? 'front' : profile.region
        : isFrontFramePart(slot.mesh, root) ? 'front' : 'arm';
    const owners = ownership.get(slot.material) ?? new Map<Owner, Slot[]>();
    const uses = owners.get(owner);
    if (uses) uses.push(slot); else owners.set(owner, [slot]);
    ownership.set(slot.material, owners);
  }
  const translucentFrameMaterials: Material[] = [], sharedMaterials: Material[] = [];
  for (const [material, owners] of ownership) {
    if (owners.size > 1) sharedMaterials.push(material);
    // The original stays on the first owner in RETAIN_ORDER; each other owner gets one instance shared by its slots.
    // Textures and geometry are retained; native tint, transmission, volume and other PBR properties are copied
    // unchanged. Compact fronts and arms need distinct instances even when both are frame: a material shader hook on
    // an arm must not also remove the front's inner rim from the physical lens input.
    const retained = RETAIN_ORDER.find(owner => owners.has(owner))!;
    for (const owner of RETAIN_ORDER) {
      const uses = owners.get(owner);
      if (!uses) continue;
      const assigned = owner === retained ? material : material.clone();
      if (assigned !== material) {
        assigned.onBeforeCompile = material.onBeforeCompile;
        assigned.customProgramCacheKey = material.customProgramCacheKey;
        for (const {mesh, index} of uses) {
          if (Array.isArray(mesh.material)) {
            const materials = mesh.material.slice(); materials[index] = assigned; mesh.material = materials;
          } else mesh.material = assigned;
        }
      }
      const value: PartClass = owner === 'lens' ? 'lens' : 'frame';
      setMaterialClass(assigned, value);
      if (value === 'frame' && assigned instanceof MeshPhysicalMaterial && assigned.transmission > 0) translucentFrameMaterials.push(assigned);
    }
  }
  return {translucentFrameMaterials, sharedMaterials, lensIdentification: report, warnings, partVisibilityIgnored};
}

/** True for a material classified as frame (structural: front, temple, hardware, unnamed parts...). */
export function isFrameMaterial(material: Material): boolean {return materialClassOf(material) === 'frame';}

/** Register a material derived from a classified one (a visibility overlay clone, a pass-B twin): it takes the
 *  source's decided class. A derived material is a new object, so without this it would be unclassified. */
export function registerDerivedMaterial(derived: Material, source: Material): void {
  const value = materialClassOf(source);
  if (value !== undefined) setMaterialClass(derived, value);
}

/** Register a derived material as frame (kept for the QA harness; registerDerivedMaterial is the general form). */
export function markFrameMaterial(material: Material): void {setMaterialClass(material, 'frame');}

/** A physical material of the frame class that Three renders through its transmission pre-pass (crystal). Its class is
 *  the decided one; transmission only says whether it currently transmits. */
export function isTranslucentFrameMaterial(material: Material): boolean {
  return materialClassOf(material) === 'frame' && material instanceof MeshPhysicalMaterial && material.transmission > 0;
}

/** True for a material classified as lens. An unclassified material is never optical. */
export function isOpticalMaterial(material: Material): boolean {return materialClassOf(material) === 'lens';}
