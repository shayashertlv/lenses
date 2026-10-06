/** Cloned arm geometry, optical protection bounds and normal/tangent correction. The renderer uses a fixed
 *  lateral spread and posterior inward return, with zero vertical shear. Original buffers and material hooks stay
 *  untouched. The lower-level shear parameter remains available to geometry callers; it is never driven by pose.
 *  Every shape update restores the original storage and composes all offsets in one pass.
 *
 *  The arms are the temples by position (temple-geometry.ts, WP1_DESIGN.md §3), whatever their opacity: every non-lens
 *  material is frame, so crystal and alpha-blended arms deform like opaque ones, and lens-class geometry never does.
 *  A side without a temple (nothing there reaches z <= -60 mm; hinge hardware alone is not a temple) has no arm: its
 *  geometry stays fixed and protected, and the caller reports it. A model with no temple at all still builds (no arm,
 *  everything protected) instead of refusing. */
import {
  Box3, BufferAttribute, InterleavedBufferAttribute, MathUtils, Matrix4, Mesh,
  Vector3,
} from 'three';
import type {BufferGeometry, Material, Object3D} from 'three';
import {isOpticalMaterial} from '../eyewear/optical-material.ts';
import {isProfiledTemple, partVisibilityOf, validatePartVisibility} from '../eyewear/part-visibility.ts';
import {TEMPLE_BLEND_LENGTH_LOCAL_M} from './temple-clip.ts';
import {templeInventory} from './temple-geometry.ts';
import {MAX_TERMINAL_INSET_M, terminalFitX, terminalFitSlope} from './temple-terminal-fit.ts';
import type {TempleTerminalFit} from './temple-terminal-fit.ts';
import {
  armSpreadSlope, MAX_ARM_SPREAD_M, spreadArmX, SPREAD_HINGE_ROUND_M, SPREAD_PIVOT_RANGE_M, WIDTH_FIT_METHOD,
} from './face-width.ts';

export const REAR_DROP_PARAMETERS = Object.freeze({
  maximumDropM: .03, proximalGuardM: .015, lateralMinM: .045,
});

type Attribute = BufferAttribute | InterleavedBufferAttribute;
interface GeometryRecord {
  original: BufferGeometry;
  clone: BufferGeometry;
  referenced: Uint8Array;
  fixed: Uint8Array;
  /** Referenced by a non-lens (frame-class) material, whatever its opacity. */
  frame: Uint8Array;
  lens: Uint8Array;
  arm: Uint8Array;
}
export interface RearDropOptions {
  /** Which sides (0: x < 0, 1: x > 0) carry a temple; default: the temple inventory of `root` (temple-geometry.ts). */
  readonly sides?: readonly [boolean, boolean];
}
interface MeshRecord {mesh: Mesh; geometry: GeometryRecord; toRoot: Matrix4}

function validateDrop(dropM: number): void {
  if (!Number.isFinite(dropM) || dropM < 0 || dropM > REAR_DROP_PARAMETERS.maximumDropM) {
    throw new Error('Rear drop must be finite and between 0 and 0.03 meters.');
  }
}
/** The lateral arm spread in metres per arm; zero preserves the original lateral position. */
function validateSpread(spreadM: number): void {
  if (!Number.isFinite(spreadM) || Math.abs(spreadM) > MAX_ARM_SPREAD_M) {
    throw new Error(`The arm spread must be finite and within ±${MAX_ARM_SPREAD_M} meters.`);
  }
}

/** Where the frame front ends and the temple shaft begins, in root-local metres. Walking back from the front, the
 *  first 1 mm slice of the band the spread may move (|x| > lateralMinM) whose vertical extent has collapsed to a bar —
 *  rims and endpieces are tall there, a shaft is not — and which stays collapsed for the next 20 mm, so the sliver of
 *  rim that grazes the band at the very front cannot be mistaken for a shaft. Measured on the shipped assets it lands
 *  at z -0.014 (Amber Horizon, lens rear -0.0107) and -0.013 (Tom Ford, lens rear -0.0144): a millimetre or two behind
 *  the endpiece, which is where a hinge is. Null when the asset has no such boundary; the caller then keeps the rear
 *  guarded shaft start plane as the spread pivot. */
export const HINGE_SLICE_M = 0.001, HINGE_SHAFT_MAX_HEIGHT_M = 0.015;
export const HINGE_SHAFT_RUN_SLICES = 20, HINGE_SHAFT_RUN_MIN_SLICES = 5, HINGE_MIN_SPAN_M = 0.05;
export function armShaftStartZM(extents: ReadonlyMap<number, {readonly low: number; readonly high: number}>): number | null {
  const height = (slice: number): number | null => {
    const extent = extents.get(slice);
    return extent ? extent.high - extent.low : null;
  };
  for (const slice of [...extents.keys()].sort((a, b) => b - a)) {
    if ((height(slice) ?? Infinity) > HINGE_SHAFT_MAX_HEIGHT_M) continue;
    // An empty slice is no evidence either way — a coarsely tessellated shaft has gaps — but a tall one ends the run.
    let populated = 1, tall = false;
    for (let i = 1; i <= HINGE_SHAFT_RUN_SLICES && !tall; i++) {
      const next = height(slice - i);
      if (next === null) continue;
      if (next > HINGE_SHAFT_MAX_HEIGHT_M) tall = true; else populated++;
    }
    if (!tall && populated >= HINGE_SHAFT_RUN_MIN_SLICES) return slice * HINGE_SLICE_M;
  }
  return null;
}

/** Optional model-space vertical shear. The live fixed-shape pipeline always supplies zero. */
export function rearDropCurve(z: number, startZM: number, cutoffZM: number, dropM: number) {
  validateDrop(dropM);
  if (![z, startZM, cutoffZM].every(Number.isFinite) || startZM <= cutoffZM) {
    throw new Error('The rear-drop span is invalid.');
  }
  const u = MathUtils.clamp((startZM - z) / (startZM - cutoffZM), 0, 1);
  return {
    loweringM: dropM * u * u * (3 - 2 * u),
    // J maps a tangent to (tx, ty + dyDz*tz, tz).
    dyDz: dropM * 6 * u * (1 - u) / (startZM - cutoffZM),
  };
}

function restoreAttribute(destination: Attribute, source: Attribute): void {
  if (destination instanceof InterleavedBufferAttribute && source instanceof InterleavedBufferAttribute) {
    destination.data.array.set(source.data.array);
    destination.data.needsUpdate = true;
  } else if (destination instanceof BufferAttribute && source instanceof BufferAttribute) {
    destination.array.set(source.array);
    destination.needsUpdate = true;
  } else throw new Error('The rear-drop geometry clone has incompatible attributes.');
}

/**
 * Install on loaded assets before visibility creates its sharing overlays.
 * Owns cloned geometry only. Original buffers and material hooks remain untouched.
 * Bounds are root-local; the caller applies its asset/pose projection externally.
 */
export function createRearDrop(root: Object3D, cutoffZM: number, spreadPivotM = 0, options: RearDropOptions = {}) {
  const profiled = validatePartVisibility(root);
  if (!Number.isFinite(cutoffZM) || cutoffZM < -.2 || cutoffZM > -.03) throw new Error('The rear-drop endpoint is invalid.');
  if (!Number.isFinite(spreadPivotM) || spreadPivotM < SPREAD_PIVOT_RANGE_M.min || spreadPivotM > SPREAD_PIVOT_RANGE_M.max) {
    throw new Error('The arm-spread pivot is out of range.');
  }
  const armSides: readonly [boolean, boolean] = options.sides ?? templeInventory(root).present;
  const sideOf = (x: number): 0 | 1 => x < 0 ? 0 : 1;
  const records = new Set<GeometryRecord>();
  const ownership = new Map<BufferGeometry, Map<string, GeometryRecord>>();
  const meshes: MeshRecord[] = [];
  let lensRearZM = Infinity, dropM = 0, spreadM = 0, disposed = false;
  let terminalFit: TempleTerminalFit | null = null;
  root.updateWorldMatrix(true, true);
  if (Math.abs(root.matrixWorld.determinant()) < 1e-12) throw new Error('The rear-drop root transform is singular.');
  const rootInverse = root.matrixWorld.clone().invert();
  try {
    root.traverse(object => {
      if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
      const original: BufferGeometry = object.geometry;
      const position = original.getAttribute('position');
      if (!position || position.itemSize !== 3) throw new Error('Rear-drop assets require three-component positions.');
      const profile = partVisibilityOf(object), arm = isProfiledTemple(object);
      const region = profile?.region ?? 'legacy';
      let owners = ownership.get(original);
      if (!owners) {owners = new Map(); ownership.set(original, owners);}
      let record = owners.get(region);
      if (!record) {
        record = {original, clone: original.clone(), referenced: new Uint8Array(position.count), fixed: new Uint8Array(position.count),
          frame: new Uint8Array(position.count), lens: new Uint8Array(position.count), arm: new Uint8Array(position.count)};
        owners.set(region, record); records.add(record);
      }
      const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
      const index = original.getIndex();
      for (const [materialIndex, material] of materials.entries()) {
        // Every non-lens material is frame, whatever its opacity (crystal, alpha-blended): WP1_DESIGN.md §3.
        const lens = isOpticalMaterial(material), frame = !lens;
        const groups = Array.isArray(object.material) ? original.groups.filter(group => group.materialIndex === materialIndex)
          : [{start: 0, count: index?.count ?? position.count}];
        for (const group of groups) for (let i = group.start; i < group.start + group.count; i++) {
          const vertex = index ? index.getX(i) : i;
          if (!Number.isInteger(vertex) || vertex < 0 || vertex >= position.count) throw new Error('The rear-drop mesh index is invalid.');
          const x = position.getX(vertex), y = position.getY(vertex), z = position.getZ(vertex);
          if (![x, y, z].every(Number.isFinite)) throw new Error('The rear-drop mesh contains invalid positions.');
          record.referenced[vertex] = 1;
          // A side without a temple has no arm: everything there stays fixed (and protected).
          if (!frame || profile && !arm || !armSides[sideOf(x)]) record.fixed[vertex] = 1;
          if (arm) record.arm[vertex] = 1;
          if (lens) {record.lens[vertex] = 1; lensRearZM = Math.min(lensRearZM, z);}
          if (frame) record.frame[vertex] = 1;
        }
      }
      // The §2 Transforms row (one load-time check at 1e-6, model/normalize.ts) leaves every mesh exactly identity
      // relative to the asset root; the bounds still apply the matrix so a hand-built scene is measured where it is.
      const toRoot = rootInverse.clone().multiply(object.matrixWorld);
      meshes.push({mesh: object, geometry: record, toRoot});
    });
    if (!Number.isFinite(lensRearZM)) throw new Error('Rear drop requires physical lens geometry to protect the optical front.');
    const startZM = lensRearZM - REAR_DROP_PARAMETERS.proximalGuardM;
    if (startZM <= cutoffZM) throw new Error('The rear-drop start must be forward of the accepted cap.');
    // Lateral spread starts at the asset's hinge. The optional vertical shear retains its separate front guard.
    const extents = new Map<number, {low: number; high: number}>();
    for (const record of records.values()) {
      const p = record.original.getAttribute('position');
      for (let i = 0; i < p.count; i++) {
        if (record.frame[i] !== 1 || record.fixed[i] === 1 || (profiled ? record.arm[i] !== 1 : Math.abs(p.getX(i)) <= REAR_DROP_PARAMETERS.lateralMinM)) continue;
        const y = p.getY(i), slice = Math.floor(p.getZ(i) / HINGE_SLICE_M);
        const extent = extents.get(slice);
        if (extent) {extent.low = Math.min(extent.low, y); extent.high = Math.max(extent.high, y);}
        else extents.set(slice, {low: y, high: y});
      }
    }
    // The thin-shaft boundary is the hinge only inside the front: a boundary behind the proximal guard (15 mm behind the
    // lens rear) belongs to a TALL temple (a shield or a wide acetate arm, such as the Prada PR17WS: 33 mm tall at z -40,
    // first under 15 mm at z -73) whose height collapses only far down the arm. Pivoting there left the whole front half
    // of such an arm unspread, inside the head proxy, so it was drawn only to the shield in every turned view (WP1 proof,
    // prada-default). Such an arm has no boundary near the front, so it pivots at the guard plane like any other.
    const shaftBoundaryZM = armShaftStartZM(extents);
    const hingeZM = shaftBoundaryZM !== null && shaftBoundaryZM >= startZM ? shaftBoundaryZM : null;
    const hingeStartZM = hingeZM ?? startZM;
    /** The plane a pivot puts the bend on, refusing one the ramp could not draw. */
    const pivotPlane = (pivot: number): number => {
      if (!Number.isFinite(pivot) || pivot < SPREAD_PIVOT_RANGE_M.min || pivot > SPREAD_PIVOT_RANGE_M.max) {
        throw new Error('The arm-spread pivot is out of range.');
      }
      const plane = hingeStartZM - pivot;
      const nearest = cutoffZM + Math.max(HINGE_MIN_SPAN_M, SPREAD_HINGE_ROUND_M);
      if (plane <= nearest) {
        // A frame whose arm shaft starts far back (an oversized acetate with a long hinge block, such as the VB 7007)
        // puts the pivot's plane behind the accepted cap. Refusing the whole model for that left it unviewable; the
        // ramp is still drawable from the nearest plane the cap allows, so the pivot is clamped there instead.
        return nearest + 1e-4;
      }
      return plane;
    };
    let spreadStartZM = pivotPlane(spreadPivotM);
    // Fix eligibility at the original hinge/front guard. A caller may move the spread pivot back, which gives
    // intervening vertices zero offset without changing which optical/front vertices are protected.
    const deformableStartZM = Math.max(startZM, hingeStartZM);
    const opticalBounds = new Box3(), originalArmBounds = [new Box3(), new Box3()];
    let candidateArmBounds = [new Box3(), new Box3()];
    const eligible = (record: GeometryRecord, vertex: number): boolean => {
      const p = record.original.getAttribute('position');
      return record.frame[vertex] === 1 && record.fixed[vertex] === 0
        && (profiled ? record.arm[vertex] === 1 : Math.abs(p.getX(vertex)) > REAR_DROP_PARAMETERS.lateralMinM && p.getZ(vertex) < deformableStartZM);
    };
    // No eligible vertex (no temple on either side) is no longer a refusal: nothing deforms, everything is protected, and
    // the caller has reported the missing temples (the §2 Orientation row fails on its own).
    let affectedVertexCount = 0;
    const affectedBySide = [0, 0];
    for (const record of records.values()) {
      const p = record.original.getAttribute('position');
      for (let i = 0; i < record.frame.length; i++) if (eligible(record, i)) {affectedVertexCount++; affectedBySide[sideOf(p.getX(i))]!++;}
    }
    const point = new Vector3();
    for (const {geometry, toRoot} of meshes) {
      const p = geometry.original.getAttribute('position');
      for (let i = 0; i < p.count; i++) {
        point.fromBufferAttribute(p, i).applyMatrix4(toRoot);
        // Protect every referenced point that the curve cannot deform, including
        // proximal shafts, small-X pads/bridge, the lenses and a temple-less side.
        if (geometry.referenced[i] && !eligible(geometry, i)) opticalBounds.expandByPoint(point);
        if (eligible(geometry, i)) originalArmBounds[p.getX(i) < 0 ? 0 : 1]!.expandByPoint(point);
      }
    }
    const updateCandidateBounds = () => {
      candidateArmBounds = [new Box3(), new Box3()];
      for (const {geometry, toRoot} of meshes) {
        const p = geometry.clone.getAttribute('position');
        for (let i = 0; i < p.count; i++) if (eligible(geometry, i)) {
          candidateArmBounds[p.getX(i) < 0 ? 0 : 1]!.expandByPoint(point.fromBufferAttribute(p, i).applyMatrix4(toRoot));
        }
      }
    };
    for (const {mesh, geometry} of meshes) mesh.geometry = geometry.clone;
    updateCandidateBounds();
    /** Compose lateral spread, terminal return and optional vertical shear over the original storage. Each offset
     *  depends on z, so the tangent maps add and the normal correction uses their summed derivatives. */
    const applyShape = (drop: number, spread: number, force = false): void => {
      if (disposed) throw new Error('Rear drop is disposed.');
      validateDrop(drop); validateSpread(spread);
      if (!force && drop === dropM && spread === spreadM) return;
      for (const record of records.values()) {
        // Restore raw storage first: interleaved attributes may share one buffer.
        for (const name of ['position', 'normal', 'tangent']) {
          const source = record.original.getAttribute(name), destination = record.clone.getAttribute(name);
          if (source && destination) restoreAttribute(destination, source);
        }
        if (drop > 0 || spread !== 0 || terminalFit) {
          const originalPosition = record.original.getAttribute('position'), position = record.clone.getAttribute('position');
          const originalNormal = record.original.getAttribute('normal'), normal = record.clone.getAttribute('normal');
          const originalTangent = record.original.getAttribute('tangent'), tangent = record.clone.getAttribute('tangent');
          for (let i = 0; i < position.count; i++) {
            if (!eligible(record, i)) continue;
            const x = originalPosition.getX(i), z = originalPosition.getZ(i);
            const curve = rearDropCurve(z, startZM, cutoffZM, drop);
            const dxDz = (spread === 0 ? 0 : Math.sign(x) * armSpreadSlope(z, spreadStartZM, cutoffZM, spread))
              + terminalFitSlope(x, z, terminalFit);
            position.setX(i, terminalFitX(spreadArmX(x, z, spreadStartZM, cutoffZM, spread), z, terminalFit));
            if (drop > 0) position.setY(i, originalPosition.getY(i) - curve.loweringM);
            if ((curve.dyDz !== 0 || dxDz !== 0) && normal && originalNormal) {
              point.set(originalNormal.getX(i), originalNormal.getY(i),
                originalNormal.getZ(i) - curve.dyDz * originalNormal.getY(i) - dxDz * originalNormal.getX(i)).normalize();
              normal.setXYZ(i, point.x, point.y, point.z);
            }
            if ((curve.dyDz !== 0 || dxDz !== 0) && tangent && originalTangent) {
              point.set(originalTangent.getX(i) + dxDz * originalTangent.getZ(i),
                originalTangent.getY(i) + curve.dyDz * originalTangent.getZ(i), originalTangent.getZ(i)).normalize();
              tangent.setXYZ(i, point.x, point.y, point.z);
            }
          }
        }
        record.clone.computeBoundingBox();
        record.clone.computeBoundingSphere();
      }
      dropM = drop; spreadM = spread;
      updateCandidateBounds();
    };
    return {
      get opticalBounds(): Box3 {return opticalBounds.clone();},
      get originalArmBounds(): Box3[] {return originalArmBounds.filter(bounds => !bounds.isEmpty()).map(bounds => bounds.clone());},
      get candidateArmBounds(): Box3[] {return candidateArmBounds.filter(bounds => !bounds.isEmpty()).map(bounds => bounds.clone());},
      get diagnostics() {
        return {method: 'temple-geometry-offsets-v1', dropM, spreadM, spreadStartZM, hingeZM, shaftBoundaryZM, widthFitMethod: WIDTH_FIT_METHOD, startZM, cutoffZM, lensRearZM,
          fadeLengthM: TEMPLE_BLEND_LENGTH_LOCAL_M, affectedVertexCount, affectedVertexCountBySide: [...affectedBySide], armSides: [...armSides],
          sourceGeometryCount: ownership.size, geometryPolicyCount: records.size,
          originalZPreserved: true, originalXPreserved: spreadM === 0 && terminalFit === null, opticalFrontPreserved: true,
          meshTransformIdentityChecked: true, protectionIncludesAllUndeformedReferencedVertices: true,
          interpretation: 'Model-space arm offsets and terminal return; production uses fixed lateral spread and zero vertical shear.'};
      },
      /** Lateral spread currently applied to the geometry, in metres per arm. */
      get spreadM(): number {return spreadM;},
      /** The spread pivot plane; production keeps it at the asset's own hinge. */
      get spreadStartZM(): number {return spreadStartZM;},
      /** Configure a pivot behind the asset's hinge. A refused value changes nothing. */
      setSpreadPivot(value: number): void {
        const plane = pivotPlane(value);
        if (plane === spreadStartZM) return;
        spreadStartZM = plane;
        applyShape(dropM, spreadM, true);
      },
      /** Where this asset's frame front ends, or null when its cross-section has no such boundary in front of the
       *  proximal guard (then the spread pivots at the guard plane, `startZM` of the diagnostics). */
      get hingeZM(): number | null {return hingeZM;},
      /** Where the arm's cross-section first collapses to a thin shaft (armShaftStartZM), wherever that is; it is the
       *  hinge only when it lies in front of the proximal guard (a tall temple collapses far behind it). */
      get shaftBoundaryZM(): number | null {return shaftBoundaryZM;},
      get dropM(): number {return dropM;},
      get terminalFit(): TempleTerminalFit | null {return terminalFit ? {...terminalFit} : null;},
      /** Static model-space posterior return. Removing it restores the original storage before other offsets. */
      setTerminalFit(value: TempleTerminalFit | null): void {
        if (JSON.stringify(value) === JSON.stringify(terminalFit)) return;
        if (value && (![value.startZM, value.endZM, value.maximumZM, value.negativeInsetM, value.positiveInsetM].every(Number.isFinite)
          || value.startZM <= value.endZM || value.endZM < value.maximumZM
          || value.negativeInsetM < 0 || value.positiveInsetM < 0
          || value.negativeInsetM > MAX_TERMINAL_INSET_M || value.positiveInsetM > MAX_TERMINAL_INSET_M))
          throw new Error('The fixed temple return is invalid.');
        terminalFit = value ? {...value} : null;
        applyShape(dropM, spreadM, true);
      },
      setDrop(value: number): void {applyShape(value, spreadM);},
      /** Set lateral spread while retaining the configured vertical shear. */
      setSpread(value: number): void {applyShape(dropM, value);},
      /** Set both offsets with one rebuild; production always supplies zero for drop. */
      setShape(drop: number, spread: number): void {applyShape(drop, spread);},
      dispose(): void {
        if (disposed) return;
        disposed = true;
        const originalsByClone = new Map([...records.values()].map(record => [record.clone, record.original]));
        // Include overlays added after installation, plus originals removed from root.
        root.traverse(object => {
          if (object instanceof Mesh) {
            const original = originalsByClone.get(object.geometry);
            if (original) object.geometry = original;
          }
        });
        for (const {mesh, geometry} of meshes) if (mesh.geometry === geometry.clone) mesh.geometry = geometry.original;
        for (const record of records.values()) record.clone.dispose();
      },
    };
  } catch (error) {
    for (const {mesh, geometry} of meshes) if (mesh.geometry === geometry.clone) mesh.geometry = geometry.original;
    for (const record of records.values()) record.clone.dispose();
    throw error;
  }
}
