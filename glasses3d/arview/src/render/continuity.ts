/** Original temple cross-sections (33 stations per arm) from the renderer's own normalised scene, projected to match
 *  the drawn shaft geometry. These paths supply the first-hair endpoint tracker.
 *
 *  glasses3d/plan/archive/20261019/WP1_DESIGN.md §3: there is no pin registry and no second parse. The renderer builds the model with
 *  buildTempleContinuityModel from the scene the load front-end parsed and the normalisation pass normalised (units
 *  rescaled, transforms baked to identity), and records the SHA-256 of the bytes it rendered.
 *
 *  The arms are the temples by position (temple-geometry.ts), whatever their opacity. Each side stands alone: a side
 *  with no temple, or whose arm has no cross-section at some station (it ends before the clip, or has a gap), is left
 *  empty with its reason in `missing`, and only that arm keeps the fixed buried cap. Only a model with no usable side at
 *  all (no temple anywhere, or no span between the lens rear and the clip) throws. */
import {Matrix4, Mesh, Vector3, Vector4} from 'three';
import type {BufferGeometry, Object3D} from 'three';
import {protectionProjection} from './protection.ts';
import {rearDropCurve} from './rear-drop.ts';
import {spreadArmX} from './face-width.ts';
import {terminalFitX} from './temple-terminal-fit.ts';
import type {TempleTerminalFit} from './temple-terminal-fit.ts';
import {NO_TEMPLE_EITHER_SIDE, templeInventory, TEMPLE_SIDE_NAMES} from './temple-geometry.ts';
import {isOpticalMaterial} from '../eyewear/optical-material.ts';
import {profileTempleSide, validatePartVisibility} from '../eyewear/part-visibility.ts';

export const CONTINUITY_GEOMETRY = Object.freeze({stations: 33, lateralMinM: .045, proximalGuardM: .015});
export interface TempleStation {zM: number; centerXM: number; centerYM: number; minXM: number; maxXM: number; minYM: number; maxYM: number;}
/** A side left without a path, and why (side 0 is x < 0, 1 is x > 0). */
export interface ContinuityGap {readonly side: 0 | 1; readonly reason: string;}
export interface TempleContinuityModel {startZM: number; cutoffZM: number;
  /** Two entries (x < 0, x > 0); a side listed in `missing` is empty. */
  sides: readonly (readonly TempleStation[])[];
  /** The sides that have no path (that arm keeps the fixed buried cap), with the reason. Absent or empty: both have one. */
  missing?: readonly ContinuityGap[];
  assetSHA256?: string; assetUrl?: string;}
export interface ContinuityOptions {
  /** Which sides carry a temple; default: the temple inventory of `root` (temple-geometry.ts). */
  readonly sides?: readonly [boolean, boolean];
}
const mm = (metres: number): string => (metres * 1000).toFixed(1);
export interface ProjectedTemplePoint {x: number; y: number; radiusPx: number; progressPx: number;}
export interface ProjectedTemplePath {side: 0 | 1; points: readonly ProjectedTemplePoint[]; lengthPx: number;}
export interface ContinuityProjection {eyewearMatrix: readonly number[]; offsetCm: readonly [number, number, number];
  sourceAspect: number; width: number; height: number; dropM: number;
  /** Uniform visual fit around the model's bridge origin, shared by the rendered asset and protection. */
  fitScale?: number;
  /** Lateral spread in the drawn geometry (metres per arm, face-width.ts). The centrelines must follow the same
   *  function as the arm vertices so hair evidence stays on the drawn shaft. */
  spreadM?: number;
  /** The same spread pivot plane as the drawn geometry; defaults to the model's start plane. */
  spreadStartZM?: number; terminalFit?: TempleTerminalFit | null;}
const check = (value: unknown, message: string): void => {if (!value) throw new Error(message);};

/** Read original root-local triangle sections; geometry/material buffers are never changed. `root` must be a scene
 *  the normalisation pass (model/normalize.ts) has run on: positions are read as root-local metres. */
export function buildTempleContinuityModel(root: Object3D, cutoffZM: number, options: ContinuityOptions = {}): TempleContinuityModel {
  const profiled = validatePartVisibility(root);
  check(Number.isFinite(cutoffZM) && cutoffZM >= -.2 && cutoffZM <= -.03, 'The continuity cap is invalid.');
  root.updateWorldMatrix(true, true);
  check(Math.abs(root.matrixWorld.determinant()) > 1e-12, 'The continuity root transform is singular.');
  const wanted: readonly [boolean, boolean] = options.sides ?? templeInventory(root).present;
  const missing: ContinuityGap[] = [];
  for (const side of [0, 1] as const) if (!wanted[side]) missing.push({side, reason: `no temple on the ${TEMPLE_SIDE_NAMES[side]} side`});
  if (missing.length === 2) throw new Error(NO_TEMPLE_EITHER_SIDE);
  let lensRearZM = Infinity;
  const triangles: {coordinates: number[]; side: 0 | 1 | null}[] = [], armFront = [-Infinity, -Infinity];
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    // Positions are read as root-local: the §2 Transforms row (one load-time check at 1e-6, model/normalize.ts) leaves
    // every mesh exactly identity relative to the asset root.
    const geometry: BufferGeometry = object.geometry;
    const position = geometry.getAttribute('position'), index = geometry.getIndex();
    check(position && position.itemSize === 3, 'Continuity requires original XYZ vertex positions.');
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    const side = profileTempleSide(object);
    for (const [materialIndex, material] of materials.entries()) {
      const lens = isOpticalMaterial(material);
      const groups = Array.isArray(object.material) ? geometry.groups.filter(group => group.materialIndex === materialIndex)
        : [{start: 0, count: index?.count ?? position.count}];
      for (const group of groups) {
        check(group.start % 3 === 0 && group.count % 3 === 0, 'The continuity mesh contains incomplete triangles.');
        for (let offset = group.start; offset < group.start + group.count; offset += 3) {
          const coordinates: number[] = [];
          for (let corner = 0; corner < 3; corner++) {
            const vertex = index ? index.getX(offset + corner) : offset + corner;
            check(Number.isInteger(vertex) && vertex >= 0 && vertex < position.count, 'The continuity triangle index is invalid.');
            const x = position.getX(vertex), y = position.getY(vertex), z = position.getZ(vertex);
            check([x, y, z].every(Number.isFinite), 'The continuity triangle contains a nonfinite vertex.');
            coordinates.push(x, y, z); if (lens) lensRearZM = Math.min(lensRearZM, z);
          }
          // Every non-lens material is frame, whatever its opacity (glasses3d/plan/archive/20261019/WP1_DESIGN.md §3).
          if (!lens && (!profiled || side !== null)) {
            triangles.push({coordinates, side});
            if (side !== null) armFront[side] = Math.max(armFront[side]!, coordinates[2]!, coordinates[5]!, coordinates[8]!);
          }
        }
      }
    }
  });
  // Every explicit arm that is drawn must have begun: front/endpiece geometry cannot fill missing arm cross-sections.
  const live = ([0, 1] as const).filter(side => wanted[side]);
  const startZM = profiled ? Math.min(lensRearZM - CONTINUITY_GEOMETRY.proximalGuardM, ...live.map(side => armFront[side]!)) - 1e-7
    : lensRearZM - CONTINUITY_GEOMETRY.proximalGuardM;
  check(Number.isFinite(startZM) && startZM > cutoffZM, `The arm span is empty: it would start at z ${mm(startZM)} mm (15 mm behind the lens rear `
    + `${Number.isFinite(lensRearZM) ? `z ${mm(lensRearZM)} mm` : '(no lens geometry)'}${profiled ? ', or at the authored arm front' : ''}), which is not ahead of `
    + `the clip z ${mm(cutoffZM)} mm.`);
  const sides: TempleStation[][] = [[], []], gapAt: (number | null)[] = [null, null];
  for (let station = 0; station < CONTINUITY_GEOMETRY.stations; station++) {
    const zM = startZM + (cutoffZM - startZM) * station / (CONTINUITY_GEOMETRY.stations - 1);
    const bounds = [{minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity},
      {minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity}];
    for (const {coordinates: triangle, side} of triangles) {
      const zs = [triangle[2]!, triangle[5]!, triangle[8]!];
      if (zM < Math.min(...zs) - 1e-12 || zM > Math.max(...zs) + 1e-12) continue;
      for (const [a, b] of [[0, 3], [3, 6], [6, 0]] as const) {
        const za = triangle[a + 2]!, zb = triangle[b + 2]!;
        if (Math.abs(zb - za) < 1e-14) continue;
        const amount = (zM - za) / (zb - za); if (amount < -1e-10 || amount > 1 + 1e-10) continue;
        const x = triangle[a]! + amount * (triangle[b]! - triangle[a]!);
        if (!profiled && Math.abs(x) <= CONTINUITY_GEOMETRY.lateralMinM) continue;
        const y = triangle[a + 1]! + amount * (triangle[b + 1]! - triangle[a + 1]!);
        const bound = bounds[side ?? (x < 0 ? 0 : 1)]!;
        bound.minX = Math.min(bound.minX, x); bound.maxX = Math.max(bound.maxX, x);
        bound.minY = Math.min(bound.minY, y); bound.maxY = Math.max(bound.maxY, y);
      }
    }
    for (const side of live) {
      if (gapAt[side] !== null) continue;
      const bound = bounds[side]!;
      if (![bound.minX, bound.maxX, bound.minY, bound.maxY].every(Number.isFinite)) {gapAt[side] = zM; continue;}
      sides[side]!.push({zM, centerXM: (bound.minX + bound.maxX) / 2, centerYM: (bound.minY + bound.maxY) / 2,
        minXM: bound.minX, maxXM: bound.maxX, minYM: bound.minY, maxYM: bound.maxY});
    }
  }
  for (const side of live) {
    const z = gapAt[side];
    if (z === null || z === undefined) continue;
    sides[side] = [];
    missing.push({side, reason: `the ${TEMPLE_SIDE_NAMES[side]} arm has no cross-section at z ${mm(z)} mm (the 33 sections run from z ${mm(startZM)} mm to `
      + `the clip at z ${mm(cutoffZM)} mm): it ends before the clip or has a gap there`});
  }
  missing.sort((a, b) => a.side - b.side);
  return {startZM, cutoffZM, sides, missing};
}

/** The arm centrelines in render pixels for this pose (drop applied), hinge first, one per side that has a path (a side
 *  in `missing` has none, so the endpoint tracker keeps its fixed cap); null when the pose cannot be projected or no side
 *  has a path. */
export function projectTempleContinuity(model: TempleContinuityModel, input: ContinuityProjection): ProjectedTemplePath[] | null {
  try {
    check(model.sides.length === 2 && input.eyewearMatrix.length === 16 && input.eyewearMatrix.every(Number.isFinite)
      && Math.abs(new Matrix4().fromArray(input.eyewearMatrix).determinant()) > 1e-12
      && input.width > 0 && input.height > 0 && Number.isFinite(input.sourceAspect) && input.sourceAspect > 0,
    'The continuity projection is invalid.');
    const projection = protectionProjection(input.eyewearMatrix, input.offsetCm, input.sourceAspect, input.fitScale ?? 1);
    const spreadM = input.spreadM ?? 0;
    const project = (x: number, y: number, z: number): Vector3 => {
      const lowering = rearDropCurve(z, model.startZM, model.cutoffZM, input.dropM).loweringM;
      const spread = spreadArmX(x, z, input.spreadStartZM ?? model.startZM, model.cutoffZM, spreadM);
      const clip = new Vector4(terminalFitX(spread, z, input.terminalFit ?? null), y - lowering, z, 1).applyMatrix4(projection);
      check(clip.toArray().every(Number.isFinite) && clip.w > 1 && clip.z >= -clip.w, 'The continuity arm crosses the near plane.');
      return new Vector3((clip.x / clip.w + 1) * input.width / 2, (1 - clip.y / clip.w) * input.height / 2, 0);
    };
    const paths: ProjectedTemplePath[] = [];
    model.sides.forEach((stations, side) => {
      if (stations.length === 0) return;
      check(stations.length >= 2, 'The continuity path is missing.');
      let lengthPx = 0;
      const points: ProjectedTemplePoint[] = [];
      for (const station of stations) {
        const center = project(station.centerXM, station.centerYM, station.zM); let radiusPx = 0;
        for (const x of [station.minXM, station.maxXM]) for (const y of [station.minYM, station.maxYM]) radiusPx = Math.max(radiusPx, center.distanceTo(project(x, y, station.zM)));
        const last = points.at(-1); if (last) lengthPx += Math.hypot(center.x - last.x, center.y - last.y);
        points.push({x: center.x, y: center.y, radiusPx, progressPx: lengthPx});
      }
      paths.push({side: side as 0 | 1, points, lengthPx});
    });
    return paths.length ? paths : null;
  } catch { return null; }
}
