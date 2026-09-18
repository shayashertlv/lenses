/** Temple continuity: the original arm centrelines of the pinned frame geometry (33 stations per arm, read from a
 *  hash-verified copy of the GLB), projected per pose with the rear drop applied, and the cut that removes an arm from
 *  its first consistent run of hair to its tip. Strands shorter than the run are ignored. */
import {Matrix4, Mesh, MeshPhysicalMaterial, Texture, Vector3, Vector4} from 'three';
import type {BufferGeometry, Material, Object3D} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {protectionProjection} from './protection.ts';
import {rearDropCurve} from './rear-drop.ts';
import {spreadArmX} from './face-width.ts';
import type {CategoryMask} from '../hair/protocol.ts';
import {assetPath} from '../assets.ts';
import {applyAffine} from '../hair/mask-reuse.ts';
import type {MaskWarp} from '../hair/mask-reuse.ts';

export const CONTINUITY_GEOMETRY = Object.freeze({stations: 33, lateralMinM: .045, proximalGuardM: .015});
/** Minimum length of hair along an arm's centreline, in source pixels, that counts as a patch rather than strands. */
export const DEFAULT_CONTINUITY_RUN_PX = 10;
const GEOMETRY_HASHES: Readonly<Record<string, string>> = Object.freeze({
  'tom-ford-clear.glb': '06ba7498dd1225bec26a2e6640f6a23695bc3614855500aa9788a91ef1f5c94f',
  'amber-horizon.glb': '78e0b472cd3e289ea7b784a86534fdeb0c90d27675e6f7ed55cc16ea3f7cc004',
});
const REGISTERED_GEOMETRY_HASHES = new Map<string, string>();
/** Pin a handed-over asset's geometry by the last segment of its address, as the shipped frames are pinned by filename. */
export function registerPinnedGeometry(assetUrl: string, sha256: string): void {
  const filename = assetUrl.split('/').at(-1)?.split('?')[0];
  if (!filename || !/^[0-9a-f]{64}$/.test(sha256)) throw new Error('The pinned geometry registration is invalid.');
  REGISTERED_GEOMETRY_HASHES.set(filename, sha256);
}
export interface TempleStation {zM: number; centerXM: number; centerYM: number; minXM: number; maxXM: number; minYM: number; maxYM: number;}
export interface TempleContinuityModel {startZM: number; cutoffZM: number; sides: readonly (readonly TempleStation[])[];
  assetSHA256?: string; assetUrl?: string;}
export interface ProjectedTemplePoint {x: number; y: number; radiusPx: number; progressPx: number;}
export interface ProjectedTemplePath {side: 0 | 1; points: readonly ProjectedTemplePoint[]; lengthPx: number;}
export interface ContinuityProjection {eyewearMatrix: readonly number[]; offsetCm: readonly [number, number, number];
  sourceAspect: number; width: number; height: number; dropM: number;
  /** The width fit's lateral arm spread in the drawn geometry (metres per arm, face-width.ts); 0 without the fit. The
   *  centrelines must be moved by exactly the same function as the arm vertices, or the cut would walk beside the arm. */
  spreadM?: number;}
/** Mesh-local z (metres) per arm from which the arm is removed to its tip, or null when no consistent hair run was found. */
export interface ContinuityCut {negative: number | null; positive: number | null;}
const check = (value: unknown, message: string): void => {if (!value) throw new Error(message);};

/** Read original root-local triangle sections; geometry/material buffers are never changed. */
export function buildTempleContinuityModel(root: Object3D, cutoffZM: number): TempleContinuityModel {
  check(Number.isFinite(cutoffZM) && cutoffZM >= -.2 && cutoffZM <= -.03, 'The continuity cap is invalid.');
  root.updateWorldMatrix(true, true);
  check(Math.abs(root.matrixWorld.determinant()) > 1e-12, 'The continuity root transform is singular.');
  const inverseRoot = root.matrixWorld.clone().invert();
  let lensRearZM = Infinity;
  const triangles: number[][] = [];
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const toRoot = inverseRoot.clone().multiply(object.matrixWorld);
    check(toRoot.elements.every((value, index) => Math.abs(value - (index % 5 === 0 ? 1 : 0)) <= 1e-12),
      'The rear-curve convention requires identity mesh transforms.');
    const geometry: BufferGeometry = object.geometry;
    const position = geometry.getAttribute('position'), index = geometry.getIndex();
    check(position && position.itemSize === 3, 'Continuity requires original XYZ vertex positions.');
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const [materialIndex, material] of materials.entries()) {
      const lens = material instanceof MeshPhysicalMaterial && material.transmission > 0;
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
          if (!lens && !material.transparent) triangles.push(coordinates);
        }
      }
    }
  });
  const startZM = lensRearZM - CONTINUITY_GEOMETRY.proximalGuardM;
  check(Number.isFinite(startZM) && startZM > cutoffZM, 'The original lens/proximal rear-arm span is missing.');
  const sides: TempleStation[][] = [[], []];
  for (let station = 0; station < CONTINUITY_GEOMETRY.stations; station++) {
    const zM = startZM + (cutoffZM - startZM) * station / (CONTINUITY_GEOMETRY.stations - 1);
    const bounds = [{minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity},
      {minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity}];
    for (const triangle of triangles) {
      const zs = [triangle[2]!, triangle[5]!, triangle[8]!];
      if (zM < Math.min(...zs) - 1e-12 || zM > Math.max(...zs) + 1e-12) continue;
      for (const [a, b] of [[0, 3], [3, 6], [6, 0]] as const) {
        const za = triangle[a + 2]!, zb = triangle[b + 2]!;
        if (Math.abs(zb - za) < 1e-14) continue;
        const amount = (zM - za) / (zb - za); if (amount < -1e-10 || amount > 1 + 1e-10) continue;
        const x = triangle[a]! + amount * (triangle[b]! - triangle[a]!);
        if (Math.abs(x) <= CONTINUITY_GEOMETRY.lateralMinM) continue;
        const y = triangle[a + 1]! + amount * (triangle[b + 1]! - triangle[a + 1]!);
        const bound = bounds[x < 0 ? 0 : 1]!;
        bound.minX = Math.min(bound.minX, x); bound.maxX = Math.max(bound.maxX, x);
        bound.minY = Math.min(bound.minY, y); bound.maxY = Math.max(bound.maxY, y);
      }
    }
    for (const side of [0, 1] as const) {
      const bound = bounds[side]!;
      check([bound.minX, bound.maxX, bound.minY, bound.maxY].every(Number.isFinite), 'An original posterior arm cross-section is missing.');
      sides[side]!.push({zM, centerXM: (bound.minX + bound.maxX) / 2, centerYM: (bound.minY + bound.maxY) / 2,
        minXM: bound.minX, maxXM: bound.maxX, minYM: bound.minY, maxYM: bound.maxY});
    }
  }
  return {startZM, cutoffZM, sides};
}

/** One geometry-only load of the pinned GLB. Texture pixels are never decoded or sent to the GPU. */
export async function loadTempleContinuityModel(assetUrl: string, cutoffZM: number, signal: AbortSignal): Promise<TempleContinuityModel> {
  const filename = assetUrl.split('/').at(-1)?.split('?')[0];
  const expected = filename ? GEOMETRY_HASHES[filename] ?? REGISTERED_GEOMETRY_HASHES.get(filename) : undefined;
  check(expected, 'No pinned original geometry is available for this frame model.');
  if (signal.aborted) throw new DOMException('Continuity loading cancelled.', 'AbortError');
  const response = await fetch(assetUrl, {signal}); check(response.ok, 'The original continuity geometry could not load.');
  const bytes = await response.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const assetSHA256 = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
  check(assetSHA256 === expected, 'The continuity model differs from the pinned original GLB.');
  if (signal.aborted) throw new DOMException('Continuity loading cancelled.', 'AbortError');
  const gltf = await new GLTFLoader().register(() => ({name: 'ContinuityGeometryOnlyTextures', loadTexture: async () => new Texture()})).parseAsync(bytes, assetPath('models/'));
  try {
    if (signal.aborted) throw new DOMException('Continuity loading cancelled.', 'AbortError');
    return {...buildTempleContinuityModel(gltf.scene, cutoffZM), assetSHA256, assetUrl};
  } finally {
    const geometries = new Set<BufferGeometry>(), materials = new Set<Material>(), textures = new Set<Texture>();
    for (const scene of gltf.scenes) scene.traverse(object => {
      if (!(object instanceof Mesh)) return; geometries.add(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        materials.add(material); for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
      }
    });
    for (const resource of [...textures, ...materials, ...geometries]) resource.dispose();
  }
}

/** The arm centrelines in render pixels for this pose (drop applied), hinge first; null when the pose cannot be projected. */
export function projectTempleContinuity(model: TempleContinuityModel, input: ContinuityProjection): ProjectedTemplePath[] | null {
  try {
    check(model.sides.length === 2 && input.eyewearMatrix.length === 16 && input.eyewearMatrix.every(Number.isFinite)
      && Math.abs(new Matrix4().fromArray(input.eyewearMatrix).determinant()) > 1e-12
      && input.width > 0 && input.height > 0 && Number.isFinite(input.sourceAspect) && input.sourceAspect > 0,
    'The continuity projection is invalid.');
    const projection = protectionProjection(input.eyewearMatrix, input.offsetCm, input.sourceAspect);
    const spreadM = input.spreadM ?? 0;
    const project = (x: number, y: number, z: number): Vector3 => {
      const lowering = rearDropCurve(z, model.startZM, model.cutoffZM, input.dropM).loweringM;
      const spread = spreadArmX(x, z, model.startZM, model.cutoffZM, spreadM);
      const clip = new Vector4(spread, y - lowering, z, 1).applyMatrix4(projection);
      check(clip.toArray().every(Number.isFinite) && clip.w > 1 && clip.z >= -clip.w, 'The continuity arm crosses the near plane.');
      return new Vector3((clip.x / clip.w + 1) * input.width / 2, (1 - clip.y / clip.w) * input.height / 2, 0);
    };
    return model.sides.map((stations, side) => {
      check(stations.length >= 2, 'The continuity path is missing.');
      let lengthPx = 0;
      const points: ProjectedTemplePoint[] = [];
      for (const station of stations) {
        const center = project(station.centerXM, station.centerYM, station.zM); let radiusPx = 0;
        for (const x of [station.minXM, station.maxXM]) for (const y of [station.minYM, station.maxYM]) radiusPx = Math.max(radiusPx, center.distanceTo(project(x, y, station.zM)));
        const last = points.at(-1); if (last) lengthPx += Math.hypot(center.x - last.x, center.y - last.y);
        points.push({x: center.x, y: center.y, radiusPx, progressPx: lengthPx});
      }
      return {side: side as 0 | 1, points, lengthPx};
    });
  } catch { return null; }
}

/** Walk each arm's projected centreline from the hinge to the tip over the mask; the first run of hair at least
 *  `runPx` long along the arm (sampled in a small window around the centreline, more than half hair) gives that arm's
 *  cut depth: everything behind it is removed to the tip. The mask may be a different size from the render. */
export function continuityCut(model: TempleContinuityModel, paths: readonly ProjectedTemplePath[], mask: CategoryMask,
  render: {width: number; height: number}, runPx: number, warp: MaskWarp | null = null): ContinuityCut {
  const result: ContinuityCut = {negative: null, positive: null};
  const scaleX = mask.width / render.width, scaleY = mask.height / render.height;
  // A mask reused from an earlier frame: each station is looked up where it sits in that frame (see hair/mask-reuse.ts).
  const toMask = warp ? (x: number, y: number): [number, number] => {
    const [fx, fy] = applyAffine(warp.toMask, x * warp.width / render.width, y * warp.height / render.height);
    return [fx * mask.width / warp.width, fy * mask.height / warp.height];
  } : null;
  for (const path of paths) {
    const stations = model.sides[path.side];
    if (!stations || stations.length !== path.points.length || stations.length < 2) continue;
    const side: keyof ContinuityCut = (stations[0]?.centerXM ?? 0) < 0 ? 'negative' : 'positive';
    const order = [...stations.keys()].sort((a, b) => stations[b]!.zM - stations[a]!.zM); // hinge (largest z) first
    let runStart: number | null = null, runStartProgress = 0;
    for (const index of order) {
      const point = path.points[index]!, radius = Math.max(1, Math.min(3, Math.round(point.radiusPx)));
      const mapped = toMask ? toMask(point.x, point.y) : null;
      const cx = Math.round(mapped ? mapped[0] : point.x * scaleX), cy = Math.round(mapped ? mapped[1] : point.y * scaleY);
      let hair = 0, total = 0;
      for (let dy = -radius; dy <= radius; dy++) {
        const y = cy + dy; if (y < 0 || y >= mask.height) continue;
        for (let dx = -radius; dx <= radius; dx++) {const x = cx + dx; if (x < 0 || x >= mask.width) continue; total++; if (mask.category[y * mask.width + x] === mask.hairIndex) hair++;}
      }
      const inHair = total > 0 && hair * 2 >= total;
      if (!inHair) {runStart = null; continue;}
      if (runStart === null) {runStart = index; runStartProgress = point.progressPx;}
      if (Math.abs(point.progressPx - runStartProgress) >= runPx) {result[side] = stations[runStart]!.zM; break;}
    }
  }
  return result;
}
