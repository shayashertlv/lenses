/** Temples by position, per side, and the temple clip derived from them (WP1_DESIGN.md §3).
 *
 *  A temple is found whatever its opacity: crystal (transmissive) and alpha-blended frame parts are temples exactly
 *  like opaque ones, and lens-class geometry (part-class.ts, decided once at load) never is.
 *  - Positional path (no usable LENSES_part_visibility): the non-lens geometry at |x| > 45 mm behind the lens rear (the
 *    minimum z of lens-class geometry). A side has a temple only when that geometry reaches z <= -60 mm, the §2
 *    Orientation rule of measure.ts / spec.py, so hinge hardware alone never counts as a temple. The arm parts (root,
 *    vertices, parts) leave out front parts (optical-material.ts isFrontFramePart: a compact mesh reaching the central
 *    band |x| <= 45 mm is front, and the renderer gives it no arm treatment); a temple part that itself reaches the
 *    central band holds front AND temple and is a warning (its front is drawn with the arm treatment).
 *  - Authored path (valid LENSES_part_visibility, validated once at load): the temple_left / temple_right region of
 *    that side (part-visibility.ts checks that temple_left lies at x < 0 and temple_right at x > 0), with the same
 *    reach rule. Its presence is cross-checked against the positional rule; a disagreement is a warning.
 *  Sides are by geometry: index 0 is x < 0 ('left' in the §2 Orientation wording, the temple clip's negative-X cutoff),
 *  index 1 is x > 0.
 *
 *  The clip is the stash QA rule (qa/temple-cutoff.mjs at stash@{0}, lines 38-44), ported here and used by the renderer
 *  and the QA harness alike: each side's arm tip is measured on its own; the one shared clip plane lies just inside the
 *  SHORTER arm (max over the sides of tip + 1 µm), never behind it, and never further back than -0.14 m (the Studio's
 *  default, so long arms end where the owner judged them), clamped to the drawable range [-0.2, -0.045] m. A handed-over
 *  clip inside that range is used as given (cross-checked against the tips); one outside it is ignored with a warning.
 *
 *  Everything here is a pure measurement of a normalised scene (model/normalize.ts ran first: metres, transforms baked
 *  to identity); nothing throws on asset content. */
import {Mesh} from 'three';
import type {BufferGeometry, Material, Object3D} from 'three';
import {isFrontFramePart, isOpticalMaterial, reachesCentralBand} from '../eyewear/optical-material.ts';
import {profileTempleSide, validatePartVisibility} from '../eyewear/part-visibility.ts';
import {TEMPLE_CLIP_FARTHEST_LOCAL_Z_M, TEMPLE_CLIP_FLOOR_LOCAL_Z_M, TEMPLE_CLIP_NEAREST_LOCAL_Z_M} from '../eyewear/catalog.ts';

export const TEMPLE_GEOMETRY = Object.freeze({
  /** §2 Orientation: temple geometry lies at |x| > 45 mm. */
  lateralMinM: 0.045,
  /** A temple reaches at least this far back (§2 Orientation, measure.ts TEMPLE_REACH_Z_MM); hinge hardware never does. */
  reachZM: -0.06,
  /** Lens-class geometry behind this plane reaches the temple region: a warning (likely a lens identification error). */
  lensRearWarnZM: -0.06,
});
/** The derived clip lies this far inside the shorter arm's tip (stash QA TIP_INTERIOR_GUARD_M). */
export const TEMPLE_TIP_GUARD_M = 1e-6;

export type TempleSideName = 'left' | 'right';
/** Side index 0 is x < 0, 1 is x > 0. */
export const TEMPLE_SIDE_NAMES: readonly [TempleSideName, TempleSideName] = Object.freeze(['left', 'right'] as const);
const SIDE_WHERE = ['x < -45 mm', 'x > +45 mm'] as const;

export interface TempleSide {
  readonly side: TempleSideName;
  /** True when this side has a temple (its geometry reaches z <= -60 mm). */
  readonly present: boolean;
  /** 'profile': the side's LENSES_part_visibility temple region; 'position': non-lens geometry at |x| > 45 mm. */
  readonly source: 'profile' | 'position';
  /** The arm tip: the minimum z (metres) of this side's temple geometry, or null when there is none. */
  readonly tipZM: number | null;
  /** The maximum z of this side's temple geometry (positional: behind the lens rear), or null. */
  readonly rootZM: number | null;
  /** Referenced vertices of this side's temple geometry (positional: behind the lens rear). */
  readonly vertexCount: number;
  /** The meshes contributing to it (their names). */
  readonly parts: readonly string[];
  /** Why this side has no temple, or null when it has one. */
  readonly missing: string | null;
}
export interface TempleInventory {
  /** Whether the authored LENSES_part_visibility regions decide the temples (else position does). */
  readonly profiled: boolean;
  /** The lens rear: minimum z (metres) of lens-class geometry, or null when there is none. */
  readonly lensRearZM: number | null;
  readonly sides: readonly [TempleSide, TempleSide];
  /** sides[i].present, as a pair for the subsystems. */
  readonly present: readonly [boolean, boolean];
  /** Measurement warnings (lens geometry behind -60 mm, an authored region that disagrees with position). */
  readonly warnings: readonly string[];
}

const mm = (metres: number): string => (metres * 1000).toFixed(1);
const slotMaterials = (mesh: Mesh): Material[] => Array.isArray(mesh.material) ? mesh.material : [mesh.material];

/** Visit every referenced vertex of every triangle of `root`'s meshes (overlays excluded), once per mesh and slot, with
 *  the slot's material. Positions are root-local metres (the normalisation pass baked every transform). */
export function forEachReferencedVertex(root: Object3D,
  visit: (mesh: Mesh, material: Material, x: number, y: number, z: number) => void): void {
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const geometry: BufferGeometry = object.geometry;
    const position = geometry.getAttribute('position'), index = geometry.getIndex();
    if (!position || position.itemSize < 3) return;
    const total = index?.count ?? position.count;
    for (const [materialIndex, material] of slotMaterials(object).entries()) {
      const groups = Array.isArray(object.material) ? geometry.groups.filter(group => group.materialIndex === materialIndex)
        : [{start: 0, count: total}];
      const seen = new Set<number>();
      for (const group of groups) {
        const end = Math.min(group.start + group.count, total);
        for (let offset = group.start; offset < end; offset++) {
          const vertex = index ? index.getX(offset) : offset;
          if (seen.has(vertex) || !Number.isInteger(vertex) || vertex < 0 || vertex >= position.count) continue;
          seen.add(vertex);
          const x = position.getX(vertex), y = position.getY(vertex), z = position.getZ(vertex);
          if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) visit(object, material, x, y, z);
        }
      }
    }
  });
}

interface Bucket {tip: number; root: number; count: number; parts: Set<string>; meshes: Set<Mesh>;}
const bucket = (): Bucket => ({tip: Infinity, root: -Infinity, count: 0, parts: new Set(), meshes: new Set()});
const add = (into: Bucket, mesh: Mesh, z: number): void => {
  into.tip = Math.min(into.tip, z); into.root = Math.max(into.root, z); into.count++; into.parts.add(mesh.name || '(unnamed mesh)');
  into.meshes.add(mesh);
};

/** The temples of a normalised, classified scene, per side (see the module comment). */
export function templeInventory(root: Object3D): TempleInventory {
  // Invalid profiles were reported and set aside at load (classifyAssetMaterials); this only reads the verdict.
  const profiled = validatePartVisibility(root);
  let lensRear = Infinity;
  forEachReferencedVertex(root, (_mesh, material, _x, _y, z) => {if (isOpticalMaterial(material)) lensRear = Math.min(lensRear, z);});
  const behind = Number.isFinite(lensRear) ? lensRear : Infinity;
  // Positional: `reach` is the §2 rule over all non-lens geometry at |x| > 45 mm; `arm` is that geometry behind the lens
  // rear, less front parts (isFrontFramePart: a compact mesh reaching the central band is front, never arm geometry, and
  // the renderer gives it no arm treatment). `extent` records each arm part's reach, for the front-in-temple warning.
  const reach = [Infinity, Infinity], arm = [bucket(), bucket()], authored = [bucket(), bucket()];
  const front = new Map<Mesh, boolean>();
  const isFront = (mesh: Mesh): boolean => {
    let value = front.get(mesh);
    if (value === undefined) {value = isFrontFramePart(mesh, root); front.set(mesh, value);}
    return value;
  };
  const extent = new Map<Mesh, {minX: number; maxX: number; minZ: number}>();
  forEachReferencedVertex(root, (mesh, material, x, _y, z) => {
    if (isOpticalMaterial(material)) return;
    if (!profiled) {
      const e = extent.get(mesh) ?? {minX: Infinity, maxX: -Infinity, minZ: Infinity};
      e.minX = Math.min(e.minX, x); e.maxX = Math.max(e.maxX, x); e.minZ = Math.min(e.minZ, z);
      extent.set(mesh, e);
    }
    if (Math.abs(x) > TEMPLE_GEOMETRY.lateralMinM) {
      const side = x < 0 ? 0 : 1;
      reach[side] = Math.min(reach[side]!, z);
      if (z < behind && (profiled || !isFront(mesh))) add(arm[side]!, mesh, z);
    }
    if (profiled) {
      const side = profileTempleSide(mesh);
      if (side !== null) add(authored[side]!, mesh, z);
    }
  });
  const warnings: string[] = [];
  if (Number.isFinite(lensRear) && lensRear < TEMPLE_GEOMETRY.lensRearWarnZM) {
    warnings.push(`lens-class geometry reaches z ${mm(lensRear)} mm, behind ${mm(TEMPLE_GEOMETRY.lensRearWarnZM)} mm: lens material reaches the `
      + 'temple region (check the lens identification); the lens rear is taken there, so the arms behind it are measured from that plane');
  }
  if (!profiled) {
    // A temple part that also holds front geometry (it reaches the central band, where no temple lies): the positional arm
    // rule then applies to that front's outer back too, so it can render unlike the same frame with authored regions.
    for (const mesh of new Set([...arm[0]!.meshes, ...arm[1]!.meshes])) {
      const e = extent.get(mesh)!;
      if (!reachesCentralBand(e.minX, e.maxX)) continue;
      const inner = e.minX > 0 ? e.minX : -e.maxX;
      warnings.push(`temple part "${mesh.name || '(unnamed mesh)'}" ${e.minX < 0 && e.maxX > 0 ? 'crosses x = 0' : `reaches |x| ${mm(inner)} mm`} and `
        + `reaches back to z ${mm(e.minZ)} mm: front and temple are one mesh, so with no LENSES_part_visibility its front geometry at |x| > `
        + `${mm(TEMPLE_GEOMETRY.lateralMinM)} mm behind the lens rear is drawn with the arm treatment (near-temple overlay, observed-cheek blend, `
        + 'kept out of the lens input) and can render unlike an authored front; split the front from the temples or author LENSES_part_visibility');
    }
  }
  const positionalMissing = (side: 0 | 1): string | null => {
    const tip = reach[side]!;
    if (!Number.isFinite(tip)) return `no non-lens geometry at ${SIDE_WHERE[side]}`;
    if (tip > TEMPLE_GEOMETRY.reachZM) {
      return `the non-lens geometry at ${SIDE_WHERE[side]} reaches only z ${mm(tip)} mm (a temple reaches z <= ${mm(TEMPLE_GEOMETRY.reachZM)} mm; `
        + 'hinge hardware alone is not a temple)';
    }
    return null;
  };
  const sides = ([0, 1] as const).map((side): TempleSide => {
    const name = TEMPLE_SIDE_NAMES[side], byPosition = positionalMissing(side);
    if (!profiled) {
      const found = arm[side]!;
      return {side: name, present: byPosition === null, source: 'position',
        tipZM: Number.isFinite(reach[side]!) ? reach[side]! : null, rootZM: found.count ? found.root : null,
        vertexCount: found.count, parts: [...found.parts].sort(), missing: byPosition};
    }
    const found = authored[side]!, region = side === 0 ? 'temple_left' : 'temple_right';
    const missing = !found.count ? `no part carries the LENSES_part_visibility region ${region}`
      : found.tip > TEMPLE_GEOMETRY.reachZM ? `the ${region} region reaches only z ${mm(found.tip)} mm (a temple reaches z <= `
        + `${mm(TEMPLE_GEOMETRY.reachZM)} mm; hinge hardware alone is not a temple)` : null;
    if ((missing === null) !== (byPosition === null)) {
      warnings.push(`temple ${name}: the authored ${region} region says ${missing === null ? 'present' : 'missing'} but position says `
        + `${byPosition === null ? 'present' : `missing (${byPosition})`}; the authored region decides what is drawn`);
    }
    return {side: name, present: missing === null, source: 'profile', tipZM: found.count ? found.tip : null,
      rootZM: found.count ? found.root : null, vertexCount: found.count, parts: [...found.parts].sort(), missing};
  }) as unknown as [TempleSide, TempleSide];
  return {profiled, lensRearZM: Number.isFinite(lensRear) ? lensRear : null, sides,
    present: [sides[0].present, sides[1].present], warnings};
}

export interface TempleClipDecision {
  /** The clip plane the renderer uses (metres, model-local z). */
  readonly clipZM: number;
  /** 'handover': a usable handed-over clip; 'derived': from the temples' tips; 'default': no temple on either side. */
  readonly source: 'handover' | 'derived' | 'default';
  /** The clip the temples give (also when a handover was used), or null when no side has a temple. */
  readonly derivedZM: number | null;
  /** The side whose (shorter) arm sets the derived clip, or null when the floor or nothing does. */
  readonly shorterSide: TempleSideName | null;
  /** The usable handed-over clip, or null. */
  readonly handoverZM: number | null;
  readonly warnings: readonly string[];
}

const inRange = (value: number): boolean => Number.isFinite(value) && value >= TEMPLE_CLIP_FARTHEST_LOCAL_Z_M && value <= TEMPLE_CLIP_NEAREST_LOCAL_Z_M;
const range = `[${TEMPLE_CLIP_FARTHEST_LOCAL_Z_M.toFixed(3)}, ${TEMPLE_CLIP_NEAREST_LOCAL_Z_M.toFixed(3)}] m`;

/** The temple clip for `inventory` (see the module comment). `handoverZM` is a clip handed over with the model, or null. */
export function deriveTempleClip(inventory: Pick<TempleInventory, 'sides'>, handoverZM: number | null = null): TempleClipDecision {
  const warnings: string[] = [];
  const present = inventory.sides.filter(side => side.present && side.tipZM !== null);
  let derivedZM: number | null = null, shorterSide: TempleSideName | null = null;
  if (present.length) {
    let raw = TEMPLE_CLIP_FLOOR_LOCAL_Z_M;
    for (const side of present) {
      const plane = side.tipZM! + TEMPLE_TIP_GUARD_M;
      if (plane > raw) {raw = plane; shorterSide = side.side;}
    }
    derivedZM = Math.min(TEMPLE_CLIP_NEAREST_LOCAL_Z_M, Math.max(TEMPLE_CLIP_FARTHEST_LOCAL_Z_M, raw));
    if (derivedZM !== raw) warnings.push(`the clip the temples give (z ${mm(raw)} mm) is outside the drawable range ${range}; it is clamped to ${derivedZM.toFixed(3)} m`);
  }
  let usableHandover: number | null = null;
  if (handoverZM !== null) {
    if (inRange(handoverZM)) usableHandover = handoverZM;
    else warnings.push(`the handed-over temple clip ${String(handoverZM)} is outside the drawable range ${range} and was ignored; `
      + (derivedZM === null ? 'no temple gives a clip either' : `the clip derived from the temples (${derivedZM.toFixed(4)} m) is used`));
  }
  if (usableHandover !== null) {
    for (const side of present) {
      if (usableHandover <= side.tipZM!) {
        warnings.push(`the handed-over temple clip ${usableHandover.toFixed(4)} m is at or behind the ${side.side} temple's tip (z ${mm(side.tipZM!)} mm): `
          + `that arm has no cross-section at the clip, so it keeps the fixed cap and gets no hair endpoint; the temples give ${derivedZM!.toFixed(4)} m`);
      }
    }
    return {clipZM: usableHandover, source: 'handover', derivedZM, shorterSide, handoverZM: usableHandover, warnings};
  }
  if (derivedZM !== null) return {clipZM: derivedZM, source: 'derived', derivedZM, shorterSide, handoverZM: null, warnings};
  warnings.push(`no temple on either side, so no clip can be derived; the fixed default ${TEMPLE_CLIP_FLOOR_LOCAL_Z_M.toFixed(3)} m is used `
    + '(there is no arm for it to cut)');
  return {clipZM: TEMPLE_CLIP_FLOOR_LOCAL_Z_M, source: 'default', derivedZM: null, shorterSide: null, handoverZM: null, warnings};
}

/** continuity.ts refuses with this when neither side has a temple (before it reads any geometry). */
export const NO_TEMPLE_EITHER_SIDE = 'No temple on either side: there is no arm to follow.';
/** The renderer's fallback details (renderer.ts installTemples / noteContinuityFailure), built here so the Node-side check
 *  (`arview serve`, qa/temple-cutoff.mjs) reports the very sentences the renderer will. */
export const missingTempleDetail = (side: Pick<TempleSide, 'side' | 'missing'>): string => `TEMPLE ${side.side.toUpperCase()} MISSING: ${side.missing}. `
  + 'That side keeps a fixed cap: no arm shape, temple continuity, terminal return or hair endpoint is built for it. A missing temple is a geometry defect of the model.';
export const defaultClipDetail = (warning: string): string => `Temple clip: ${warning}.`;
export const continuityUnavailableDetail = (reason: string): string =>
  `The temple continuity model is unavailable (${reason}); both arms keep the fixed buried cap and no hair endpoint.`;
export interface TempleLoadFallback {readonly subsystem: string; readonly detail: string; readonly stage: 'load';}

/** The load-stage fallbacks the renderer reports from the temples alone, in its order: one per side without a temple, the
 *  default clip when no temple gives one, and the continuity model off when neither side has a temple (continuity.ts
 *  refuses that before reading geometry). What only the rest of the load finds (an arm without a posterior
 *  cross-section, the rear drop, the terminal fit, the shader installs) is not predicted here: the renderer's own report
 *  adds it. */
export function templeLoadFallbacks(inventory: Pick<TempleInventory, 'sides'>, clip: TempleClipDecision): TempleLoadFallback[] {
  const out: TempleLoadFallback[] = [];
  for (const side of inventory.sides) if (!side.present) out.push({subsystem: `temple ${side.side}`, detail: missingTempleDetail(side), stage: 'load'});
  if (clip.source === 'default') for (const warning of clip.warnings) out.push({subsystem: 'temple clip depth', detail: defaultClipDetail(warning), stage: 'load'});
  if (inventory.sides.every(side => !side.present)) out.push({subsystem: 'continuity', detail: continuityUnavailableDetail(NO_TEMPLE_EITHER_SIDE), stage: 'load'});
  return out;
}

/** The report form of an inventory and a clip decision (model report measurements `temples` and `temple_clip`). */
export function templeMeasurements(inventory: TempleInventory, clip: TempleClipDecision): {temples: Record<string, unknown>; temple_clip: Record<string, unknown>} {
  const round = (metres: number | null): number | null => metres === null ? null : Math.round(metres * 1e5) / 100;
  const side = (entry: TempleSide) => ({present: entry.present, source: entry.source, tip_z_mm: round(entry.tipZM), root_z_mm: round(entry.rootZM),
    vertices: entry.vertexCount, parts: entry.parts.slice(0, 8), ...(entry.missing ? {missing: entry.missing} : {})});
  return {
    temples: {decided_by: inventory.profiled ? 'LENSES_part_visibility' : 'position', lens_rear_z_mm: round(inventory.lensRearZM),
      left: side(inventory.sides[0]), right: side(inventory.sides[1])},
    temple_clip: {clip_m: clip.clipZM, source: clip.source, derived_m: clip.derivedZM, handover_m: clip.handoverZM,
      shorter_side: clip.shorterSide, floor_m: TEMPLE_CLIP_FLOOR_LOCAL_Z_M, range_m: [TEMPLE_CLIP_FARTHEST_LOCAL_Z_M, TEMPLE_CLIP_NEAREST_LOCAL_Z_M]},
  };
}
