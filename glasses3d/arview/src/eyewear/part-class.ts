/** The decided lens/frame class of every loaded material and primitive mesh (glasses3d/plan/archive/20261019/WP1_DESIGN.md §2).
 *
 *  Filled once at load by classifyAssetMaterials (optical-material.ts) from the glTF JSON, and read by every consumer
 *  (renderer lens meshes, rear drop, continuity, temple visibility and clip, hair occlusion, terminal fit, shadows,
 *  translucent twins and look-through, part-visibility validation). Keyed by object, so a second parse of the same
 *  bytes classifies its own objects. A class is never re-derived from a material's live transmission. */
import type {Material, Mesh} from 'three';
import type {LensDecision, PartClass} from './lens-identification.ts';

/** One material slot of a primitive mesh (GLTFLoader meshes have exactly one). */
export interface SlotClass {readonly class: PartClass; readonly decision: LensDecision;}
export interface MeshClassRecord {readonly slots: readonly SlotClass[];}

const materialClasses = new WeakMap<Material, PartClass>();
const meshRecords = new WeakMap<Mesh, MeshClassRecord>();

export function setMaterialClass(material: Material, value: PartClass): void {materialClasses.set(material, value);}
/** The decided class of a material, or undefined for one no classification has seen. */
export function materialClassOf(material: Material): PartClass | undefined {return materialClasses.get(material);}

export function setMeshRecord(mesh: Mesh, record: MeshClassRecord): void {meshRecords.set(mesh, record);}
export function meshRecordOf(mesh: Mesh): MeshClassRecord | undefined {return meshRecords.get(mesh);}
/** The decided class of a primitive mesh: its slots' common class, 'mixed' when they differ (hand-built multi-material
 *  meshes only), undefined when unclassified. */
export function meshClassOf(mesh: Mesh): PartClass | 'mixed' | undefined {
  const slots = meshRecords.get(mesh)?.slots;
  if (!slots?.length) return undefined;
  return slots.every(slot => slot.class === slots[0]!.class) ? slots[0]!.class : 'mixed';
}
