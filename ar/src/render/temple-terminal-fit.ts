/** A fixed posterior return for the temple pipeline. This is an authored render fit to the
 * canonical head volume, not an ear estimate. The front 75 mm of each asset is unchanged. */
import {Mesh, MeshPhysicalMaterial} from 'three';
import type {BufferGeometry, Object3D} from 'three';
import {spreadArmX} from './face-width.ts';

export const TEMPLE_HEAD_VOLUME = Object.freeze({
  scaleCm: Object.freeze([7.4, 9.5, 7.5] as const),
  centerCm: Object.freeze([0, -.5, -3.5] as const),
});
/** Model-local inset guard, shared by containment and the geometry deformation controller. */
export const MAX_TERMINAL_INSET_M = .05;
export interface TempleTerminalFit {
  startZM: number; endZM: number; maximumZM: number;
  negativeInsetM: number; positiveInsetM: number;
}
/** Keep depth relief on the unchanged shaft, fading out before the inward return begins.
 * A stale earlier boundary can bury the straight shaft after anterior head-width calibration.
 * No deliberately returned geometry may be lifted back over the head. */
export function templeTerminalRelief(fit: TempleTerminalFit): {startZM: number; endZM: number} {
  const endZM = fit.startZM;
  return {startZM: endZM + .005, endZM};
}
interface FitInput {
  offsetCm: readonly [number, number, number]; spreadM: number; spreadStartZM: number;
  modelCutoffZM: number; maximumZM: number; fitScale?: number;
}
/** The return is C1 continuous and changes neither local Y nor Z. It never depends on pose or distance. */
export function terminalReturn(zM: number, fit: TempleTerminalFit | null): {weight: number; slope: number} {
  if (!fit) return {weight: 0, slope: 0};
  const length = fit.startZM - fit.endZM;
  const t = Math.max(0, Math.min(1, (fit.startZM - zM) / length));
  return {weight: t * t * (3 - 2 * t), slope: t > 0 && t < 1 ? -6 * t * (1 - t) / length : 0};
}
export function terminalFitX(xM: number, zM: number, fit: TempleTerminalFit | null): number {
  if (!fit) return xM;
  return xM - Math.sign(xM) * (xM < 0 ? fit.negativeInsetM : fit.positiveInsetM) * terminalReturn(zM, fit).weight;
}
export function terminalFitSlope(xM: number, zM: number, fit: TempleTerminalFit | null): number {
  return fit ? -Math.sign(xM) * (xM < 0 ? fit.negativeInsetM : fit.positiveInsetM) * terminalReturn(zM, fit).slope : 0;
}

/** Capture original terminal-band geometry once, before applying spread/return deformations. The resulting
 * evaluator retains only numeric samples, so visual fitting can update containment without reading the GLB again
 * or accidentally fitting a previously deformed shaft. In the terminal band the return is a translation; the
 * ellipsoid is convex, so containing clipped-triangle corners also contains their interiors. */
export function createTempleTerminalFitEvaluator(root: Object3D, input: FitInput): (fitScale: number) => TempleTerminalFit | null {
  const {maximumZM, spreadM, spreadStartZM, modelCutoffZM} = input, offsetCm = [...input.offsetCm];
  // Preserve more of the visible shaft before burying the tip. Moving the cap alone cannot
  // lengthen a shaft that has already entered the depth proxy. The terminal band stays fixed.
  const endZM = maximumZM + .005, startZM = Math.min(-.075, endZM + .065);
  if (![maximumZM, spreadM, spreadStartZM, modelCutoffZM, ...offsetCm].every(Number.isFinite)
    || endZM - maximumZM < .004 || startZM - endZM < .025 || spreadStartZM <= startZM) return () => null;
  const samples: {side: 0 | 1; spreadX: number; y: number; z: number}[] = [], seen = new Set<string>();
  const counts = [0, 0];
  const inspect = (x: number, y: number, z: number): void => {
    if (Math.abs(x) <= .045 || z < maximumZM - 1e-9 || z > endZM + 1e-9) return;
    const key = `${x},${y},${z}`;
    if (seen.has(key)) return;
    seen.add(key);
    const side = x < 0 ? 0 : 1;
    samples.push({side, spreadX: spreadArmX(x, z, spreadStartZM, modelCutoffZM, spreadM), y, z});
    counts[side]!++;
  };
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay) return;
    const geometry: BufferGeometry = object.geometry, p = geometry.getAttribute('position'), index = geometry.getIndex();
    if (!p) return;
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const [materialIndex, material] of materials.entries()) {
      if (material.transparent || material instanceof MeshPhysicalMaterial && material.transmission > 0) continue;
      const groups = Array.isArray(object.material) ? geometry.groups.filter(group => group.materialIndex === materialIndex)
        : [{start: 0, count: index?.count ?? p.count}];
      for (const group of groups) for (let i = group.start; i + 2 < group.start + group.count; i += 3) {
        const triangle = [0, 1, 2].map(c => {const v = index ? index.getX(i + c) : i + c;
          return [p.getX(v), p.getY(v), p.getZ(v)] as const;});
        for (const v of triangle) inspect(...v);
        for (let edge = 0; edge < 3; edge++) {
          const a = triangle[edge]!, b = triangle[(edge + 1) % 3]!;
          if (Math.abs(a[2] - b[2]) < 1e-12) continue;
          for (const z of [maximumZM, endZM]) {
            const t = (z - a[2]) / (b[2] - a[2]);
            if (t >= 0 && t <= 1) inspect(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), z);
          }
        }
      }
    }
  });
  if (counts.some(count => count === 0)) return () => null;
  return (fitScale: number): TempleTerminalFit | null => {
    if (!Number.isFinite(fitScale) || fitScale <= 0) return null;
    const modelToCm = 100 * fitScale, insets = [0, 0];
    const [rx, ry, rz] = TEMPLE_HEAD_VOLUME.scaleCm, [cx, cy, cz] = TEMPLE_HEAD_VOLUME.centerCm;
    for (const {side, spreadX, y, z} of samples) {
      const sign = side === 0 ? -1 : 1;
      const headY = (y * modelToCm + offsetCm[1]! - cy) / ry, headZ = (z * modelToCm + offsetCm[2]! - cz) / rz;
      const inside = 1 - headY * headY - headZ * headZ;
      if (!(inside > 0)) return null;
      const boundaryX = cx + sign * (rx * Math.sqrt(inside) - .3);
      insets[side] = Math.max(insets[side]!, sign * (spreadX - (boundaryX - offsetCm[0]!) / modelToCm));
    }
    // Keep the clearance after float32 storage and interpolation at a terminal-band boundary.
    for (let side = 0; side < 2; side++) insets[side]! += .000001;
    if (insets.some(inset => !Number.isFinite(inset) || inset > MAX_TERMINAL_INSET_M)) return null;
    if (samples.some(({side, spreadX}) => (side === 0 ? -spreadX : spreadX) - insets[side]! <= 0)) return null;
    return {startZM, endZM, maximumZM, negativeInsetM: insets[0]!, positiveInsetM: insets[1]!};
  };
}

/** One-off compatibility entry point. Production visual fitting should retain the evaluator instead. */
export function createTempleTerminalFit(root: Object3D, input: FitInput): TempleTerminalFit | null {
  return createTempleTerminalFitEvaluator(root, input)(input.fitScale ?? 1);
}
