/** Capture pose sets, backgrounds, variants and file names (pure; shared by the Node orchestrator and the page).
 *
 *  Sign conventions (the pre-WP0 prototype's ar_capture/page.js, deleted 2026-10-13, see git tag
 *  review-20261013-pre-cleanup; the synthetic detection in synthetic-face.ts): Euler order
 *  YXZ; yaw > 0 turns the nose toward image +x in the unmirrored frame, so the asset's -X side is near the camera;
 *  pitch > 0 tips the nose down, so the camera sees from above.
 *
 *  - default: front, yaw ±30, yaw ±45, pitch ±20 (7 poses) on skin, beauty + part-id.
 *  - junction: yaw ±30 × pitch ±20 (4 poses) on skin and dark, beauty + part-id, each full frame plus a crop around the
 *    lens-brow contact of each side (junction.ts).
 *  - all: both pose lists on skin, dark and checker, beauty + part-id + normals, lens-hidden and lens-only on the first
 *    background, and the junction crops on the junction poses.
 *  `--backgrounds` replaces the set's background list. Flat variants (part-id, normals) draw on black and do not depend
 *  on the background: they are rendered once per pose. */

export const POSE_SET_NAMES = Object.freeze(['default', 'junction', 'all'] as const);
export type PoseSetName = typeof POSE_SET_NAMES[number];
export const BACKGROUND_NAMES = Object.freeze(['skin', 'dark', 'checker'] as const);
export type BackgroundName = typeof BACKGROUND_NAMES[number];
/** The prototype's flat backgrounds (page.js:16-21): skin, dark, and a 32 px checker. */
export const BACKGROUNDS: Readonly<Record<BackgroundName, {readonly fill: string; readonly checker?: readonly [string, string]; readonly cell?: number}>> = Object.freeze({
  skin: Object.freeze({fill: '#cba68d'}),
  dark: Object.freeze({fill: '#2b2522'}),
  checker: Object.freeze({fill: '#b4a190', checker: Object.freeze(['#b4a190', '#ebe0d2'] as const), cell: 32}),
});
export const VARIANT_NAMES = Object.freeze(['beauty', 'part-id', 'normals', 'lens-hidden', 'lens-only'] as const);
export type VariantName = typeof VARIANT_NAMES[number];
/** Variants drawn on black (the renderer's flat overrides): independent of the background. */
export const FLAT_VARIANTS: ReadonlySet<VariantName> = new Set(['part-id', 'normals']);
/** The renderer override (src/render/override.ts) of each variant; beauty draws the normal guarded frame. */
export const VARIANT_OVERRIDE: Readonly<Record<VariantName, 'part-id' | 'normals' | 'lens-hidden' | 'lens-only' | null>> = Object.freeze({
  beauty: null, 'part-id': 'part-id', normals: 'normals', 'lens-hidden': 'lens-hidden', 'lens-only': 'lens-only'});

export interface CapturePose {
  readonly id: string;
  readonly yaw: number;
  readonly pitch: number;
  readonly roll: number;
  readonly depthCm: number;
  readonly order: 'YXZ';
  /** Junction crops are cut from every render of this pose. */
  readonly junction: boolean;
}
export interface PlannedRender {
  readonly variant: VariantName;
  /** null for a flat variant (drawn on black). */
  readonly background: BackgroundName | null;
  readonly file: string;
  /** Whether the two side crops are cut from this render (junction poses). */
  readonly crops: boolean;
}
export interface PlannedPose {readonly pose: CapturePose; readonly renders: readonly PlannedRender[];}
export interface CapturePlan {
  readonly set: PoseSetName;
  readonly backgrounds: readonly BackgroundName[];
  readonly poses: readonly PlannedPose[];
  /** Full-frame renders, and the crops those renders are planned to yield (two per crop render). */
  readonly renderCount: number;
  readonly plannedCropCount: number;
}

const signed = (value: number): string => `${value < 0 ? 'm' : 'p'}${Math.abs(value)}`;
/** A file-safe pose id: 'front', 'yaw_p30', 'pitch_m20', 'yaw_p30_pitch_m20'. */
export function poseId(yaw: number, pitch: number, roll = 0): string {
  const parts = [yaw ? `yaw_${signed(yaw)}` : '', pitch ? `pitch_${signed(pitch)}` : '', roll ? `roll_${signed(roll)}` : ''].filter(Boolean);
  return parts.length ? parts.join('_') : 'front';
}
function pose(yaw: number, pitch: number, depthCm: number, junction: boolean): CapturePose {
  return Object.freeze({id: poseId(yaw, pitch), yaw, pitch, roll: 0, depthCm, order: 'YXZ', junction});
}
export const DEFAULT_POSE_ANGLES: readonly (readonly [number, number])[] = Object.freeze([[0, 0], [30, 0], [-30, 0], [45, 0], [-45, 0], [0, 20], [0, -20]]);
export const JUNCTION_POSE_ANGLES: readonly (readonly [number, number])[] = Object.freeze([[30, 20], [30, -20], [-30, 20], [-30, -20]]);
const SET_BACKGROUNDS: Readonly<Record<PoseSetName, readonly BackgroundName[]>> = Object.freeze({
  default: ['skin'], junction: ['skin', 'dark'], all: ['skin', 'dark', 'checker']});

/** `<pose>__<background>__<variant>.png`, or `<pose>__<variant>.png` for a flat variant. */
export function renderFile(poseIdValue: string, variant: VariantName, background: BackgroundName | null): string {
  return background === null ? `${poseIdValue}__${variant}.png` : `${poseIdValue}__${background}__${variant}.png`;
}
/** The crop of side `side` cut from the render saved as `file`. */
export function cropFile(file: string, side: 'left' | 'right'): string {return file.replace(/\.png$/, `__crop_${side}.png`);}
/** Names the capture writes are restricted to (no path separators, no traversal). */
export const SAFE_FILE = /^[a-z0-9][a-z0-9_.-]{0,150}\.png$/;

/** The deterministic capture plan: poses in order, each with its renders in order (flat variants first, then the
 *  backgrounds in the given order). */
export function buildCapturePlan(set: PoseSetName, options: {backgrounds?: readonly BackgroundName[] | null; depthCm: number}): CapturePlan {
  if (!(POSE_SET_NAMES as readonly string[]).includes(set)) throw new Error(`Unknown pose set ${String(set)}.`);
  const backgrounds = options.backgrounds?.length ? [...options.backgrounds] : [...SET_BACKGROUNDS[set]];
  const angles: {yaw: number; pitch: number; junction: boolean}[] = [];
  if (set === 'default' || set === 'all') for (const [yaw, pitch] of DEFAULT_POSE_ANGLES) angles.push({yaw, pitch, junction: false});
  if (set === 'junction' || set === 'all') for (const [yaw, pitch] of JUNCTION_POSE_ANGLES) angles.push({yaw, pitch, junction: true});
  const flat: VariantName[] = set === 'all' ? ['part-id', 'normals'] : ['part-id'];
  const poses: PlannedPose[] = [];
  let renderCount = 0, plannedCropCount = 0;
  for (const angle of angles) {
    const planned = pose(angle.yaw, angle.pitch, options.depthCm, angle.junction);
    const renders: PlannedRender[] = [];
    const add = (variant: VariantName, background: BackgroundName | null) => {
      renders.push(Object.freeze({variant, background, file: renderFile(planned.id, variant, background), crops: planned.junction}));
      renderCount++; if (planned.junction) plannedCropCount += 2;
    };
    for (const variant of flat) add(variant, null);
    for (const background of backgrounds) add('beauty', background);
    if (set === 'all') {add('lens-hidden', backgrounds[0]!); add('lens-only', backgrounds[0]!);}
    poses.push(Object.freeze({pose: planned, renders: Object.freeze(renders)}));
  }
  return Object.freeze({set, backgrounds: Object.freeze(backgrounds), poses: Object.freeze(poses), renderCount, plannedCropCount});
}

/** Paint a flat background into a 2D context (the page's synthetic camera frame). */
export function paintBackground(context: {fillStyle: unknown; fillRect(x: number, y: number, w: number, h: number): void},
  width: number, height: number, name: BackgroundName): void {
  const spec = BACKGROUNDS[name];
  context.fillStyle = spec.fill; context.fillRect(0, 0, width, height);
  if (!spec.checker || !spec.cell) return;
  const cell = spec.cell;
  for (let y = 0; y < height; y += cell) for (let x = 0; x < width; x += cell) {
    context.fillStyle = spec.checker[(x / cell + y / cell) % 2 ? 0 : 1]; context.fillRect(x, y, cell, cell);
  }
}
