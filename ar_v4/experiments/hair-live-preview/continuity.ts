import {Matrix4, Mesh, MeshPhysicalMaterial, Texture, Vector3, Vector4} from 'three';
import type {BufferGeometry, Material, Object3D} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {protectionProjection} from '../temple-sagittal/protection.ts';
import {rearDropCurve} from '../temple-sagittal/rear-drop.ts';
import {validateProtection} from '../temple-sagittal/contracts.ts';
import type {PixelRect, ProtectionConfiguration} from '../temple-sagittal/contracts.ts';

export const TEMPLE_CONTINUITY_POLICY = Object.freeze({method: 'same-arm-detached-tip-v1', stations: 33,
  lateralMinM: .045, proximalGuardM: .015, minimumPathPixels: 8, minimumOriginalPixels: 6,
  minimumProximalPixels: 3, minimumLongitudinalGapPx: 1, associationMarginPx: 2,
  interpretation: 'Remove only newly disconnected distal remnants on one originally connected projected arm; not hair dilation or measured depth.'});
const GEOMETRY_HASHES: Readonly<Record<string, string>> = Object.freeze({
  'tom-ford-clear.glb': '06ba7498dd1225bec26a2e6640f6a23695bc3614855500aa9788a91ef1f5c94f',
  'amber-horizon.glb': '78e0b472cd3e289ea7b784a86534fdeb0c90d27675e6f7ed55cc16ea3f7cc004',
});
export interface TempleStation {zM: number; centerXM: number; centerYM: number; minXM: number; maxXM: number; minYM: number; maxYM: number;}
export interface TempleContinuityModel {startZM: number; cutoffZM: number; sides: readonly (readonly TempleStation[])[];
  assetSHA256?: string; assetUrl?: string;}
export interface ProjectedTemplePoint {x: number; y: number; radiusPx: number; progressPx: number;}
export interface ProjectedTemplePath {side: 0 | 1; points: readonly ProjectedTemplePoint[]; lengthPx: number;}
export interface ContinuityProjection {eyewearMatrix: readonly number[]; offsetCm: readonly [number, number, number];
  sourceAspect: number; width: number; height: number; dropM: number;}
export interface ContinuityInput {before: Uint8ClampedArray; background: Uint8ClampedArray; after: Uint8ClampedArray;
  width: number; height: number; protection: ProtectionConfiguration; noseRoi: PixelRect;
  paths: readonly ProjectedTemplePath[] | null;
  /** Optional sparse candidates; each is independently rechecked against all guards and real residual. */
  eligibleIndices?: Uint32Array;}
export interface ContinuityDiagnostics {method: string; removedPixels: number; detachedComponents: number;
  originalComponents: number; splitComponents: number; skippedAmbiguousComponents: number;
  eligibleResidualPixels: number; pathLengthsPx: number[]; unavailableReason: string | null;}
const check = (value: unknown, message: string): void => {if (!value) throw new Error(message);};
const inside = (rect: PixelRect, x: number, y: number): boolean => x >= rect.x0 && x < rect.x1 && y >= rect.y0 && y < rect.y1;
const residual = (left: Uint8ClampedArray, right: Uint8ClampedArray, offset: number): boolean =>
  left[offset] !== right[offset] || left[offset + 1] !== right[offset + 1] || left[offset + 2] !== right[offset + 2] || left[offset + 3] !== right[offset + 3];

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
      'The accepted rear-curve convention requires identity mesh transforms.');
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
  const startZM = lensRearZM - TEMPLE_CONTINUITY_POLICY.proximalGuardM;
  check(Number.isFinite(startZM) && startZM > cutoffZM, 'The original lens/proximal rear-arm span is missing.');
  const sides: TempleStation[][] = [[], []];
  for (let station = 0; station < TEMPLE_CONTINUITY_POLICY.stations; station++) {
    const zM = startZM + (cutoffZM - startZM) * station / (TEMPLE_CONTINUITY_POLICY.stations - 1);
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
        if (Math.abs(x) <= TEMPLE_CONTINUITY_POLICY.lateralMinM) continue;
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

/** One geometry-only load. Texture pixels are never decoded or sent to the GPU. */
export async function loadTempleContinuityModel(assetUrl: string, cutoffZM: number, signal: AbortSignal): Promise<TempleContinuityModel> {
  const filename = assetUrl.split('/').at(-1)?.split('?')[0], expected = filename ? GEOMETRY_HASHES[filename] : undefined;
  check(expected, 'No pinned original geometry is available for this frame model.');
  if (signal.aborted) throw new DOMException('Continuity loading cancelled.', 'AbortError');
  const response = await fetch(assetUrl, {signal}); check(response.ok, 'The original continuity geometry could not load.');
  const bytes = await response.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const assetSHA256 = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
  check(assetSHA256 === expected, 'The continuity model differs from the accepted original GLB.');
  if (signal.aborted) throw new DOMException('Continuity loading cancelled.', 'AbortError');
  const gltf = await new GLTFLoader().register(() => ({name: 'ContinuityGeometryOnlyTextures', loadTexture: async () => new Texture()})).parseAsync(bytes, '/models/');
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

export function projectTempleContinuity(model: TempleContinuityModel, input: ContinuityProjection): ProjectedTemplePath[] | null {
  try {
    check(model.sides.length === 2 && input.eyewearMatrix.length === 16 && input.eyewearMatrix.every(Number.isFinite)
      && Math.abs(new Matrix4().fromArray(input.eyewearMatrix).determinant()) > 1e-12
      && input.width > 0 && input.height > 0 && Number.isFinite(input.sourceAspect) && input.sourceAspect > 0,
    'The current continuity projection is invalid.');
    const projection = protectionProjection(input.eyewearMatrix, input.offsetCm, input.sourceAspect);
    const project = (x: number, y: number, z: number): Vector3 => {
      const lowering = rearDropCurve(z, model.startZM, model.cutoffZM, input.dropM).loweringM;
      const clip = new Vector4(x, y - lowering, z, 1).applyMatrix4(projection);
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

interface Association {side: number; progress: number; distance: number;}
function associate(x: number, y: number, paths: readonly ProjectedTemplePath[]): Association | null {
  const perSide: Association[] = [];
  for (const path of paths) {
    if (path.lengthPx < TEMPLE_CONTINUITY_POLICY.minimumPathPixels) continue;
    let best: Association | null = null;
    for (let index = 0; index < path.points.length - 1; index++) {
      const a = path.points[index]!, b = path.points[index + 1]!, dx = b.x - a.x, dy = b.y - a.y;
      const denominator = dx * dx + dy * dy; if (denominator < 1e-12) continue;
      const amount = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / denominator));
      const distance = Math.hypot(x - a.x - amount * dx, y - a.y - amount * dy);
      const radius = a.radiusPx + amount * (b.radiusPx - a.radiusPx) + TEMPLE_CONTINUITY_POLICY.associationMarginPx;
      if (distance > radius || best && best.distance <= distance) continue;
      best = {side: path.side, progress: a.progressPx + amount * (b.progressPx - a.progressPx), distance};
    }
    if (best) perSide.push(best);
  }
  perSide.sort((a, b) => a.distance - b.distance);
  if (!perSide[0] || perSide[1] && perSide[1].distance - perSide[0].distance < 1) return null;
  return perSide[0];
}

/** Additional indices only; caller copies paired background here before its final guards/checks. */
export function findDetachedTemplePixels(input: ContinuityInput): {indices: Uint32Array; diagnostics: ContinuityDiagnostics} {
  const diagnostics: ContinuityDiagnostics = {method: TEMPLE_CONTINUITY_POLICY.method, removedPixels: 0, detachedComponents: 0,
    originalComponents: 0, splitComponents: 0, skippedAmbiguousComponents: 0, eligibleResidualPixels: 0,
    pathLengthsPx: input.paths?.map(path => path.lengthPx) ?? [], unavailableReason: null};
  try {
    const {width, height, before, background, after, protection, noseRoi, paths} = input;
    check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
      && before.length === width * height * 4 && background.length === before.length && after.length === before.length,
    'Continuity pixel dimensions differ.');
    validateProtection(protection, width, height);
    check(noseRoi && [noseRoi.x0, noseRoi.y0, noseRoi.x1, noseRoi.y1].every(Number.isInteger)
      && noseRoi.x0 >= 0 && noseRoi.y0 >= 0 && noseRoi.x1 <= width && noseRoi.y1 <= height
      && noseRoi.x1 > noseRoi.x0 && noseRoi.y1 > noseRoi.y0, 'Continuity nasal protection is invalid.');
    check(paths && paths.length === 2 && paths.every(path => Number.isFinite(path.lengthPx) && path.points.length >= 2
      && path.points.every(point => [point.x, point.y, point.radiusPx, point.progressPx].every(Number.isFinite)
        && point.radiusPx >= 0 && point.progressPx >= 0)), 'The current same-arm ordering is unavailable.');
    const nodes = new Map<number, {visible: boolean; association: Association | null}>();
    const add = (index: number): void => {
      if (!Number.isInteger(index) || index < 0 || index >= width * height || nodes.has(index)) return;
      const x = index % width, y = Math.floor(index / width);
      if (inside(noseRoi, x, y) || protection.protectedRects.some(rect => inside(rect, x, y))
        || !protection.editableRects.some(rect => inside(rect, x, y)) || !residual(before, background, index * 4)) return;
      nodes.set(index, {visible: residual(after, background, index * 4), association: associate(x + .5, y + .5, paths!)});
    };
    if (input.eligibleIndices) for (const index of input.eligibleIndices) add(index);
    else for (const rect of protection.editableRects) for (let y = rect.y0; y < rect.y1; y++) for (let x = rect.x0; x < rect.x1; x++) add(y * width + x);
    diagnostics.eligibleResidualPixels = nodes.size;
    const adjacent = (index: number): number[] => {
      const x = index % width, y = Math.floor(index / width), result: number[] = [];
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        if ((!dx && !dy) || x + dx < 0 || x + dx >= width || y + dy < 0 || y + dy >= height) continue;
        result.push((y + dy) * width + x + dx);
      }
      return result;
    };
    const seen = new Set<number>(), remove: number[] = [];
    for (const seed of nodes.keys()) {
      if (seen.has(seed)) continue;
      const original = [seed]; seen.add(seed);
      for (let cursor = 0; cursor < original.length; cursor++) for (const neighbor of adjacent(original[cursor]!)) {
        if (!nodes.has(neighbor) || seen.has(neighbor)) continue; seen.add(neighbor); original.push(neighbor);
      }
      diagnostics.originalComponents++;
      if (original.length < TEMPLE_CONTINUITY_POLICY.minimumOriginalPixels) continue;
      const side = nodes.get(seed)!.association?.side;
      if (side === undefined || original.some(index => nodes.get(index)!.association?.side !== side)) {
        diagnostics.skippedAmbiguousComponents++; continue;
      }
      const originalSet = new Set(original), visibleSeen = new Set<number>();
      const pieces: {indices: number[]; min: number; max: number}[] = [];
      for (const start of original) {
        if (visibleSeen.has(start) || !nodes.get(start)!.visible) continue;
        const indices = [start]; visibleSeen.add(start);
        for (let cursor = 0; cursor < indices.length; cursor++) for (const neighbor of adjacent(indices[cursor]!)) {
          if (!originalSet.has(neighbor) || visibleSeen.has(neighbor) || !nodes.get(neighbor)!.visible) continue;
          visibleSeen.add(neighbor); indices.push(neighbor);
        }
        const progress = indices.map(index => nodes.get(index)!.association!.progress);
        pieces.push({indices, min: Math.min(...progress), max: Math.max(...progress)});
      }
      if (pieces.length < 2) continue; diagnostics.splitComponents++;
      pieces.sort((a, b) => a.min - b.min);
      const proximal = pieces[0]!;
      if (proximal.indices.length < TEMPLE_CONTINUITY_POLICY.minimumProximalPixels) continue;
      for (const piece of pieces.slice(1)) {
        if (piece.min <= proximal.max + TEMPLE_CONTINUITY_POLICY.minimumLongitudinalGapPx) continue;
        const hiddenBetween = original.some(index => {const node = nodes.get(index)!;
          return !node.visible && node.association!.progress > proximal.max && node.association!.progress < piece.min;});
        if (!hiddenBetween) continue;
        remove.push(...piece.indices); diagnostics.detachedComponents++;
      }
    }
    diagnostics.removedPixels = remove.length;
    return {indices: new Uint32Array(remove), diagnostics};
  } catch (error) {
    diagnostics.unavailableReason = error instanceof Error ? error.message : String(error);
    return {indices: new Uint32Array(), diagnostics};
  }
}
