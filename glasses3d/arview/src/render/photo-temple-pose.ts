/** Frozen photo openings, in authored asset coordinates. No fit, deformation, or file mutation. */
import {Mesh} from 'three';
import type {Object3D} from 'three';
import {explicitRoleOf} from '../eyewear/optical-material.ts';

export interface PhotoTempleOpening {
  readonly theta_deg: number;
  /** Signed asset-local millimetres; positive opening moves the tip outward on both sides. */
  readonly axis_xz_mm: readonly [number, number];
}
export type PhotoTemplePose = Readonly<Partial<Record<'+1' | '-1', PhotoTempleOpening>>>;
export interface PhotoTemplePoseReport {
  readonly requested: PhotoTemplePose;
  readonly applied: PhotoTemplePose;
  readonly moved_meshes: Readonly<Record<'+1' | '-1', number>>;
  readonly frame_tagged_arm_candidates: readonly string[];
  readonly warnings: readonly string[];
}

export function parsePhotoTemplePose(value: unknown): PhotoTemplePose {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('temple_pose must be a side-keyed object.');
  const out: Partial<Record<'+1' | '-1', PhotoTempleOpening>> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (key !== '+1' && key !== '-1') throw new Error('temple_pose side must be +1 or -1.');
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new Error(`temple_pose ${key} must be an object.`);
    const {theta_deg: theta, axis_xz_mm: axis} = entry as Record<string, unknown>;
    if (typeof theta !== 'number' || !Number.isFinite(theta) || !Array.isArray(axis) || axis.length !== 2
        || axis.some(v => typeof v !== 'number' || !Number.isFinite(v)) || Number(key) * axis[0]! <= 0) {
      throw new Error(`temple_pose ${key}: finite angle and signed axis_xz_mm on its side are required.`);
    }
    out[key] = Object.freeze({theta_deg: theta, axis_xz_mm: Object.freeze([...axis]) as unknown as readonly [number, number]});
  }
  return Object.freeze(out);
}

/** A private geometry copy per temple primitive protects shared front geometry. The normalised
 * asset has baked identity transforms. Capture originals before the face path changes its arm shape;
 * each apply starts from those originals and reset precedes the next worn pose. */
export function createPhotoTemplePose(root: Object3D) {
  const records: {mesh: Mesh; side: '+1' | '-1'; positions: Float32Array; normals: Float32Array | null;
    tangents: Float32Array | null}[] = [];
  const replaced = new Set<Mesh['geometry']>();
  const frameArms: string[] = [];
  root.traverse(object => {
    if (!(object instanceof Mesh) || explicitRoleOf(object)?.value.toLowerCase() !== 'frame') return;
    object.geometry.computeBoundingBox();
    const box = object.geometry.boundingBox;
    if (box && box.min.z < -.06 && Math.max(Math.abs(box.min.x), Math.abs(box.max.x)) > .02) {
      frameArms.push(object.name || '(unnamed frame mesh)');
    }
  });
  root.traverse(object => {
    if (!(object instanceof Mesh) || explicitRoleOf(object)?.value.toLowerCase() !== 'temple') return;
    const positions = object.geometry.getAttribute('position');
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < positions.count; i++) {lo = Math.min(lo, positions.getX(i)); hi = Math.max(hi, positions.getX(i));}
    const centre = (lo + hi) / 2;
    if (!Number.isFinite(centre) || Math.abs(centre) <= 1e-9) return;
    replaced.add(object.geometry);
    object.geometry = object.geometry.clone();
    const normal = object.geometry.getAttribute('normal'), tangent = object.geometry.getAttribute('tangent');
    const xyz = (attribute: typeof positions) => Float32Array.from(Array.from({length: attribute.count}, (_, i) =>
      [attribute.getX(i), attribute.getY(i), attribute.getZ(i)]).flat());
    records.push({mesh: object, side: centre > 0 ? '+1' : '-1', positions: xyz(positions), normals: normal ? xyz(normal) : null,
      tangents: tangent ? xyz(tangent) : null});
  });
  root.traverse(object => {if (object instanceof Mesh) replaced.delete(object.geometry);});
  for (const geometry of replaced) geometry.dispose();
  let active = false;
  let needsReset = false;
  let report: PhotoTemplePoseReport = {requested: {}, applied: {}, moved_meshes: {'+1': 0, '-1': 0},
    frame_tagged_arm_candidates: frameArms, warnings: []};
  const apply = (pose: PhotoTemplePose) => {
    const moved = {'+1': 0, '-1': 0};
    let dirty = false;
    for (const record of records) {
      // Group the conversion factor so every finite degree value stays finite before the trigonometry.
      const opening = pose[record.side], angle = (opening?.theta_deg ?? 0) * Number(record.side) * (Math.PI / 180);
      let changed = false;
      const c = Math.cos(angle), s = Math.sin(angle), x0 = (opening?.axis_xz_mm[0] ?? 0) / 1000, z0 = (opening?.axis_xz_mm[1] ?? 0) / 1000;
      const geometry = record.mesh.geometry, position = geometry.getAttribute('position'), normal = geometry.getAttribute('normal'),
        tangent = geometry.getAttribute('tangent');
      for (let i = 0; i < position.count; i++) {
        const j = i * 3, x = record.positions[j]!, y = record.positions[j + 1]!, z = record.positions[j + 2]!;
        position.setXYZ(i, x0 + c * (x - x0) - s * (z - z0), y, z0 + s * (x - x0) + c * (z - z0));
        changed ||= position.getX(i) !== x || position.getZ(i) !== z;
        if (normal && record.normals) {
          const nx = record.normals[j]!, ny = record.normals[j + 1]!, nz = record.normals[j + 2]!;
          normal.setXYZ(i, c * nx - s * nz, ny, s * nx + c * nz);
          dirty ||= normal.getX(i) !== nx || normal.getY(i) !== ny || normal.getZ(i) !== nz;
        }
        if (tangent && record.tangents) {
          const tx = record.tangents[j]!, ty = record.tangents[j + 1]!, tz = record.tangents[j + 2]!;
          // Rigid opening preserves tangent handedness (w); rotate its direction with the surface normal.
          tangent.setXYZ(i, c * tx - s * tz, ty, s * tx + c * tz);
          dirty ||= tangent.getX(i) !== tx || tangent.getY(i) !== ty || tangent.getZ(i) !== tz;
        }
      }
      if (changed) moved[record.side]++;
      dirty ||= changed;
      position.needsUpdate = true;
      if (normal) normal.needsUpdate = true;
      if (tangent) tangent.needsUpdate = true;
      geometry.computeBoundingBox(); geometry.computeBoundingSphere();
    }
    active = Object.values(moved).some(count => count > 0);
    needsReset = dirty;
    const warnings: string[] = [];
    for (const side of ['+1', '-1'] as const) {
      if (pose[side]?.theta_deg && !moved[side]) warnings.push(`Photo opening ${side}: no temple-role mesh moved; the requested delta was not applied.`);
    }
    if (Object.values(pose).some(value => value.theta_deg !== 0) && frameArms.length) {
      warnings.push(`Frame-tagged geometry reaches the arm region and stays authored: ${frameArms.join(', ')}.`);
    }
    const applied: PhotoTemplePose = Object.fromEntries(Object.entries(pose).filter(([side]) => moved[side as '+1' | '-1'] > 0));
    report = {requested: pose, applied, moved_meshes: moved, frame_tagged_arm_candidates: frameArms, warnings};
    return report;
  };
  return {apply, reset() {
    if (needsReset) apply({});
    else report = {requested: {}, applied: {}, moved_meshes: {'+1': 0, '-1': 0}, frame_tagged_arm_candidates: frameArms, warnings: []};
  }, get active() {return active;}, get report() {return report;}};
}
