import {wearerSide} from '../../src/model/sides.ts';
/** How far back the near temple is actually drawn in a turned part-id render: a cut is never silent (pure; used by the
 *  capture page, tested in qa/capture-temple-reach.test.mjs).
 *
 *  The renderer's drawn arm centreline (`templeCentrelines()`: the continuity stations moved by the fixed spread and
 *  terminal return) is projected into the part-id render; a station is drawn when a pixel within `windowPx` of its
 *  projected centre decodes to one of that side's arm parts. The arm may legitimately disappear from where its fixed
 *  return begins (the arm passes behind the ear and is buried there by design) and is clipped at its cap; anything the
 *  face/head depth occluders hide AHEAD of the return start is a cut where the arm should be seen, and is a warning:
 *  a shortfall at the end (the drawn arm stops early) AND a hidden stretch in the middle (the cheek face-surface occluder
 *  can hide the arm's middle while it reappears further back; WP1 review E/F: that was missed).
 *
 *  Sign convention (poses.ts): yaw > 0 turns the nose toward image +x, so the asset's -X side (side 0, wearer's 'right') faces the
 *  camera; yaw < 0 shows side 1. Front and pitch views see the arms end-on and are not measured. */

export const TEMPLE_REACH = Object.freeze({
  /** Views turned less than this show the arms nearly end-on: not measured. */
  minYawDegrees: 20,
  /** Pixels around the projected centre searched for the arm's own colour. */
  windowPx: 1,
  /** A hidden stretch shorter than this (about one station spacing plus the 5 mm clip fade) is not a cut. */
  toleranceM: 0.005,
});

export type ReachSide = 0 | 1;
export interface TempleReach {
  readonly side: 'left' | 'right';
  readonly x_sign: -1 | 1;
  readonly yaw_degrees: number;
  /** The deepest station drawn (mm, asset-local z), or null when no station of this arm is drawn. */
  readonly drawn_to_z_mm: number | null;
  /** The first station from the hinge that is not drawn, or null when every station ahead of the cap is drawn. */
  readonly first_hidden_z_mm: number | null;
  /** Where the fixed return behind the ear begins (the arm may vanish from here on), or null without a terminal fit. */
  readonly return_start_z_mm: number | null;
  /** The live cap of this arm (it is clipped there). */
  readonly cap_z_mm: number;
  /** How far ahead of the return start (or the cap, without one) the drawn arm stops; 0 when it reaches it. */
  readonly hidden_before_return_mm: number;
  /** The longest run of consecutive hidden stations ahead of the return start that is followed by a drawn station (or by
   *  the return start): from its first hidden station to the next drawn one (clamped at the return start). 0 if none. */
  readonly longest_hidden_run_mm: number;
  /** Where that run begins (its first hidden station), or null. */
  readonly longest_hidden_run_from_z_mm: number | null;
  readonly stations: number;
  readonly drawn_stations: number;
  /** Of `drawn_stations`, those behind the return start (or the cap without one). Not judged: there the return curls
   *  inward, and its projected centre can land on a nearer part of the same arm (the Prada PR17WS shield at yaw ±30: the
   *  return projects back onto the shield, so `drawn_to_z_mm` reads -113.7 mm while the arm is seen only to the return
   *  start at -75 mm; WP1 closeout check). A `drawn_to_z_mm` behind the return start is therefore no evidence the arm
   *  is seen there. */
  readonly behind_return_drawn_stations: number;
  readonly cut: boolean;
}

/** The side whose arm faces the camera at this yaw, or null for a view not measured. */
export function nearSideOfYaw(yawDegrees: number): ReachSide | null {
  if (!Number.isFinite(yawDegrees) || Math.abs(yawDegrees) < TEMPLE_REACH.minYawDegrees) return null;
  return yawDegrees > 0 ? 0 : 1;
}

const round = (metres: number): number => Math.round(metres * 10000) / 10;

/** Measure one arm. `centreline` is [x, y, z] asset-local metres per station (hinge first), `projected` its render
 *  pixels ([x, y, ndcZ] per station, NaN behind the camera), `labels` the decoded part-id render (palette index per
 *  pixel, negative for background/mixed), `armLabels` the palette indices of this side's arm parts. */
export function measureTempleReach(input: {
  readonly side: ReachSide; readonly yawDegrees: number;
  readonly centreline: ArrayLike<number>; readonly projected: ArrayLike<number>;
  readonly labels: ArrayLike<number>; readonly width: number; readonly height: number;
  readonly armLabels: ReadonlySet<number>; readonly returnStartZM: number | null; readonly capZM: number;
}): TempleReach {
  const {centreline, projected, labels, width, height, armLabels} = input;
  if (centreline.length % 3 || projected.length !== centreline.length) throw new Error('measureTempleReach: centreline and projection differ in length.');
  if (labels.length !== width * height) throw new Error('measureTempleReach: the labels are not width x height.');
  const order: number[] = [];
  for (let i = 0; i < centreline.length / 3; i++) if (centreline[i * 3 + 2]! >= input.capZM - 1e-9) order.push(i);
  order.sort((a, b) => centreline[b * 3 + 2]! - centreline[a * 3 + 2]!);
  const w = TEMPLE_REACH.windowPx;
  const drawn = (i: number): boolean => {
    const px = projected[i * 3]!, py = projected[i * 3 + 1]!;
    if (!Number.isFinite(px) || !Number.isFinite(py)) return false;
    const cx = Math.round(px), cy = Math.round(py);
    for (let y = cy - w; y <= cy + w; y++) for (let x = cx - w; x <= cx + w; x++) {
      if (x < 0 || y < 0 || x >= width || y >= height) continue;
      if (armLabels.has(labels[y * width + x]!)) return true;
    }
    return false;
  };
  // The arm should be seen at least to where its return begins (never behind its cap).
  const limit = Math.max(input.returnStartZM ?? input.capZM, input.capZM);
  let deepest: number | null = null, firstHidden: number | null = null, count = 0, behind = 0;
  // Hidden runs ahead of the return start: each from its first hidden station to the next drawn station (clamped at the
  // return start). A run that never meets a drawn station ahead of the return start is the end shortfall below.
  let runStart: number | null = null, longest = 0, longestFrom: number | null = null;
  for (const i of order) {
    const z = centreline[i * 3 + 2]!;
    if (drawn(i)) {
      count++; deepest = deepest === null ? z : Math.min(deepest, z);
      if (z < limit - 1e-9) behind++;
      if (runStart !== null) {
        const length = runStart - Math.max(z, limit);
        if (length > longest) {longest = length; longestFrom = runStart;}
        runStart = null;
      }
    } else {
      if (firstHidden === null) firstHidden = z;
      if (runStart === null && z > limit) runStart = z;
    }
  }
  const front = order.length ? centreline[order[0]! * 3 + 2]! : limit;
  const hidden = Math.max(0, (deepest ?? front) - limit);
  return {side: wearerSide(input.side), x_sign: input.side === 0 ? -1 : 1, yaw_degrees: input.yawDegrees,
    drawn_to_z_mm: deepest === null ? null : round(deepest), first_hidden_z_mm: firstHidden === null ? null : round(firstHidden),
    return_start_z_mm: input.returnStartZM === null ? null : round(input.returnStartZM), cap_z_mm: round(input.capZM),
    hidden_before_return_mm: round(hidden), longest_hidden_run_mm: round(longest),
    longest_hidden_run_from_z_mm: longestFrom === null ? null : round(longestFrom), stations: order.length, drawn_stations: count,
    behind_return_drawn_stations: behind,
    // 1 nm of slack: a stretch of exactly the tolerance (5 mm between stations) is not a cut, whatever the float rounding.
    cut: hidden > TEMPLE_REACH.toleranceM + 1e-9 || longest > TEMPLE_REACH.toleranceM + 1e-9};
}

/** The report warning for a cut arm, or null. */
export function templeReachWarning(file: string, reach: TempleReach): string | null {
  if (!reach.cut) return null;
  const where = reach.return_start_z_mm !== null ? `where its fixed return behind the ear begins (z ${reach.return_start_z_mm} mm)`
    : `its cap (z ${reach.cap_z_mm} mm)`;
  const parts: string[] = [];
  if (reach.hidden_before_return_mm > TEMPLE_REACH.toleranceM * 1000 + 1e-6) {
    parts.push(`drawn only to ${reach.drawn_to_z_mm === null ? 'nowhere (no station of it is drawn)' : `z ${reach.drawn_to_z_mm} mm`}, `
      + `${reach.hidden_before_return_mm} mm short of ${where}`);
  }
  if (reach.longest_hidden_run_mm > TEMPLE_REACH.toleranceM * 1000 + 1e-6) {
    parts.push(`hidden for ${reach.longest_hidden_run_mm} mm from z ${reach.longest_hidden_run_from_z_mm} mm, ahead of ${where}, and drawn again behind`);
  }
  return `${file}: TEMPLE CUT: the near (wearer's ${reach.side}, ${reach.x_sign < 0 ? '-X' : '+X'}) temple is ${parts.join('; and ')}; the face/head depth occluders hide that part of the arm `
    + 'in this view (the drawn arm lies inside the head proxy there)';
}
