import {Mesh, MeshPhysicalMaterial} from 'three';
import type {Material, Object3D} from 'three';

export interface BranchLensMetrics {requested: boolean; used: boolean; omittedMaterials: number; fallback: string | null;}
export const emptyBranchLensMetrics = (requested = false): BranchLensMetrics =>
  ({requested, used: false, omittedMaterials: 0, fallback: null});

/** Install only after optical bounds, clipping and visibility have classified the
 * original branch asset. This owns no materials and never changes beauty. */
export class BranchLensOmission {
  private readonly lenses: Material[];
  constructor(root: Object3D) {
    const lenses = new Set<Material>();
    root.traverse(object => {
      if (!(object instanceof Mesh)) return;
      for (const material of Array.isArray(object.material) ? object.material : [object.material])
        if (material instanceof MeshPhysicalMaterial && material.transmission > 0) lenses.add(material);
    });
    this.lenses = [...lenses];
  }
  render(requested: boolean, draw: () => void): BranchLensMetrics {
    if (!requested) {draw(); return emptyBranchLensMetrics();}
    const visible = this.lenses.filter(material => material.visible);
    try {
      for (const material of visible) material.visible = false;
      draw();
      return {requested, used: visible.length > 0, omittedMaterials: visible.length,
        fallback: visible.length ? null : 'No visible branch lens material was available to omit.'};
    } finally {
      for (const material of visible) material.visible = true;
    }
  }
}
