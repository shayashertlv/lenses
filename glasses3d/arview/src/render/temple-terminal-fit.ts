/** A fixed posterior return for the temple pipeline. This is an authored render fit to the
 * canonical head volume, not an ear estimate. The front 75 mm of each asset is unchanged. */
import {Mesh} from 'three';
import type {BufferGeometry, Object3D} from 'three';
import {spreadArmX} from './face-width.ts';
import {isOpticalMaterial} from '../eyewear/optical-material.ts';
import {partVisibilityOf, profileTempleSide} from '../eyewear/part-visibility.ts';

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
  /** Which sides (0: x < 0, 1: x > 0) carry a temple (temple-geometry.ts); default both. A side without one has no arm to
   *  return: it gets no inset, and it never makes the other arm's fit unavailable. */
  sides?: readonly [boolean, boolean];
}
/** The fit at a visual fit scale, or null when the return cannot be contained there. `unavailable` says why the fit is
 *  unavailable at every scale (null when it can be evaluated), so a caller can report it instead of keeping the cap
 *  silently. */
export type TempleTerminalFitEvaluator = ((fitScale: number) => TempleTerminalFit | null) & {readonly unavailable: string | null};
const unavailableFit = (reason: string): TempleTerminalFitEvaluator => Object.assign((): TempleTerminalFit | null => null, {unavailable: reason});
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
export function createTempleTerminalFitEvaluator(root: Object3D, input: FitInput): TempleTerminalFitEvaluator {
  const {maximumZM, spreadM, spreadStartZM, modelCutoffZM} = input, offsetCm = [...input.offsetCm];
  const wanted = input.sides ?? [true, true];
  // Preserve more of the visible shaft before burying the tip. Moving the cap alone cannot
  // lengthen a shaft that has already entered the depth proxy. The terminal band stays fixed.
  const endZM = maximumZM + .005, startZM = Math.min(-.075, endZM + .065);
  if (![maximumZM, spreadM, spreadStartZM, modelCutoffZM, ...offsetCm].every(Number.isFinite)) return unavailableFit('its inputs are not finite');
  if (endZM - maximumZM < .004 || startZM - endZM < .025 || spreadStartZM <= startZM) {
    return unavailableFit(`the buried cap at z ${(maximumZM * 1000).toFixed(1)} mm leaves no room for the return between the hinge (spread start `
      + `z ${(spreadStartZM * 1000).toFixed(1)} mm) and the cap`);
  }
  if (!wanted[0] && !wanted[1]) return unavailableFit('there is no temple on either side');
  const samples: {side: 0 | 1; spreadX: number; y: number; z: number}[] = [], seen = new Set<string>();
  const counts = [0, 0];
  const inspect = (x: number, y: number, z: number, declaredSide: 0 | 1 | null): void => {
    if (declaredSide === null && Math.abs(x) <= .045 || z < maximumZM - 1e-9 || z > endZM + 1e-9) return;
    const side = declaredSide ?? (x < 0 ? 0 : 1);
    if (!wanted[side]) return;
    const key = `${x},${y},${z}`;
    if (seen.has(key)) return;
    seen.add(key);
    samples.push({side, spreadX: spreadArmX(x, z, spreadStartZM, modelCutoffZM, spreadM), y, z});
    counts[side]!++;
  };
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay) return;
    const profile = partVisibilityOf(object), side = profileTempleSide(object);
    if (profile && side === null) return;
    const geometry: BufferGeometry = object.geometry, p = geometry.getAttribute('position'), index = geometry.getIndex();
    if (!p) return;
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const [materialIndex, material] of materials.entries()) {
      // Every non-lens material is frame, whatever its opacity (WP1_DESIGN.md §3): alpha-blended arms are sampled too.
      if (isOpticalMaterial(material)) continue;
      const groups = Array.isArray(object.material) ? geometry.groups.filter(group => group.materialIndex === materialIndex)
        : [{start: 0, count: index?.count ?? p.count}];
      for (const group of groups) for (let i = group.start; i + 2 < group.start + group.count; i += 3) {
        const triangle = [0, 1, 2].map(c => {const v = index ? index.getX(i + c) : i + c;
          return [p.getX(v), p.getY(v), p.getZ(v)] as const;});
        for (const v of triangle) inspect(...v, side);
        for (let edge = 0; edge < 3; edge++) {
          const a = triangle[edge]!, b = triangle[(edge + 1) % 3]!;
          if (Math.abs(a[2] - b[2]) < 1e-12) continue;
          for (const z of [maximumZM, endZM]) {
            const t = (z - a[2]) / (b[2] - a[2]);
            if (t >= 0 && t <= 1) inspect(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), z, side);
          }
        }
      }
    }
  });
  const short = ([0, 1] as const).filter(side => wanted[side] && counts[side] === 0);
  if (short.length) {
    return unavailableFit(`the ${short.map(side => side === 0 ? "wearer's right (x < 0)" : "wearer's left (x > 0)").join(' and ')} arm${short.length > 1 ? 's have' : ' has'} `
      + `no geometry in the terminal band z ${(maximumZM * 1000).toFixed(1)}..${(endZM * 1000).toFixed(1)} mm (it ends before the buried cap)`);
  }
  return Object.assign((fitScale: number): TempleTerminalFit | null => {
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
  }, {unavailable: null});
}

/** Single evaluation at input.fitScale (tests and one-off callers); the renderer keeps the evaluator (renderer.ts terminalFitAtScale). */
export function createTempleTerminalFit(root: Object3D, input: FitInput): TempleTerminalFit | null {
  return createTempleTerminalFitEvaluator(root, input)(input.fitScale ?? 1);
}
