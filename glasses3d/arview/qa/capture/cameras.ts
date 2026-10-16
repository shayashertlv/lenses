/** Explicit cameras for `arview capture <glb> --cameras <cameras.json> --no-face` (pure; shared by the Node orchestrator,
 *  the page and the tests).
 *
 *  A dev-capture mode for comparing a model with product photos: each camera is a pinhole camera given by the caller (for
 *  a photo, the camera fitted to it), the model drawn at its authored size with NO face (the photos have none), on a flat
 *  background of the caller's choosing. The drawing is the try-on's own (TryOnRenderer.poseCamera): materials, the room
 *  environment and the camera-relative key light, ACES tone mapping, lens transmission; only what a face makes is left
 *  out (occluders, hair, face shadows, protection, the arm spread, terminal return and temple clip).
 *
 *  cameras.json (schema `arview.cameras/1`):
 *
 *    {"schema": "arview.cameras/1", "cameras": [{
 *       "id": "photo_05",                     file-safe: [a-z0-9][a-z0-9_-]*, unique
 *       "width": 1200, "height": 600,         render size in pixels (each side 64-4096)
 *       "background": "#f3f2f0",              flat sRGB background
 *       "view": [16 numbers],                 asset-local metres -> camera metres, ROW-major 4x4, a rotation (det +1, no mirror)
 *                                             plus a translation; the camera looks down -Z with +Y up and +X right
 *       "fx": 1800, "fy": 1800,               focal lengths in render pixels
 *       "cx": 600, "cy": 300,                 principal point in render pixels, y down, integer = a pixel CENTRE
 *       "near": 0.5, "far": 4.0,              clip planes in metres (0 < near < far)
 *       "temple_pose": {                      optional deltas from authored pose, + means tip outward
 *         "+1": {"theta_deg": 6, "axis_xz_mm": [68, -2]},
 *         "-1": {"theta_deg": 2, "axis_xz_mm": [-68, -2]}
 *       }                                     axes are signed asset-local MILLIMETRES; tagged temple parts move
 *    }, ...]}
 *
 *  A camera-space point (X, Y, Z) (Z < 0 in front) lands at u = cx + fx X / -Z, v = cy - fy Y / -Z (`projectPixel`). Each
 *  camera yields `<id>__beauty.png` (on its background) and `<id>__part-id.png` (flat part colours on black, the
 *  silhouette), plus `<id>__silhouette.png` (white on black, for unambiguous edge coverage). */

import {parsePhotoTemplePose} from '../../src/render/photo-temple-pose.ts';
import type {PhotoTemplePose} from '../../src/render/photo-temple-pose.ts';
import {validateLighting} from '../../src/render/lighting.ts';
import type {LightingConfig} from '../../src/render/lighting.ts';

export const CAMERAS_SCHEMA = 'arview.cameras/1';
export const CAMERA_ID = /^[a-z0-9][a-z0-9_-]{0,60}$/;
export const MAX_CAMERAS = 64;
export const CAMERA_SIZE = Object.freeze({minWidth: 64, maxWidth: 4096, minHeight: 64, maxHeight: 4096});
/** The scene is in centimetres (the try-on's virtual camera space); the cameras file is in metres. */
export const METRES_TO_SCENE = 100;
export const CAMERA_VARIANTS = Object.freeze(['beauty', 'part-id', 'silhouette'] as const);
export type CameraVariant = typeof CAMERA_VARIANTS[number];

export interface ExplicitCameraSpec {
  readonly id: string;
  readonly width: number;
  readonly height: number;
  readonly background: string;
  /** Row-major 4x4, asset-local metres to camera metres. */
  readonly view: readonly number[];
  readonly fx: number; readonly fy: number; readonly cx: number; readonly cy: number;
  readonly near: number; readonly far: number;
  /** Optional per-photo deltas from authored arms; signed axes in asset-local millimetres. */
  readonly temple_pose?: PhotoTemplePose;
  /** Optional diagnostic light; omission restores the unmodified review light. */
  readonly lighting?: LightingConfig;
  /** Free-form caller data, copied into the report untouched. */
  readonly meta?: unknown;
}
export interface CameraSet {readonly schema: typeof CAMERAS_SCHEMA; readonly cameras: readonly ExplicitCameraSpec[];}

export class CameraSetError extends Error {
  constructor(message: string) {super(message); this.name = 'CameraSetError';}
}

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** Validate a parsed cameras.json. Throws CameraSetError naming the camera and the problem; never fills in a default. */
export function parseCameraSet(value: unknown): CameraSet {
  if (!isObject(value)) throw new CameraSetError('The cameras file must hold a JSON object.');
  if (value.schema !== CAMERAS_SCHEMA) throw new CameraSetError(`The cameras file must have "schema": "${CAMERAS_SCHEMA}", got ${JSON.stringify(value.schema)}.`);
  const list = value.cameras;
  if (!Array.isArray(list) || list.length === 0) throw new CameraSetError('The cameras file needs a non-empty "cameras" list.');
  if (list.length > MAX_CAMERAS) throw new CameraSetError(`At most ${MAX_CAMERAS} cameras per capture, got ${list.length}.`);
  const seen = new Set<string>();
  const cameras = list.map((entry, index) => {
    if (!isObject(entry)) throw new CameraSetError(`cameras[${index}] must be an object.`);
    const id = entry.id;
    if (typeof id !== 'string' || !CAMERA_ID.test(id)) throw new CameraSetError(`cameras[${index}].id must match ${CAMERA_ID}, got ${JSON.stringify(id)}.`);
    if (seen.has(id)) throw new CameraSetError(`The camera id "${id}" is given twice.`);
    seen.add(id);
    const where = `camera "${id}"`;
    const {width, height} = entry;
    if (!Number.isInteger(width) || (width as number) < CAMERA_SIZE.minWidth || (width as number) > CAMERA_SIZE.maxWidth) {
      throw new CameraSetError(`${where}: width must be an integer ${CAMERA_SIZE.minWidth}-${CAMERA_SIZE.maxWidth} px (the renderer never draws wider), got ${JSON.stringify(width)}.`);
    }
    if (!Number.isInteger(height) || (height as number) < CAMERA_SIZE.minHeight || (height as number) > CAMERA_SIZE.maxHeight) {
      throw new CameraSetError(`${where}: height must be an integer ${CAMERA_SIZE.minHeight}-${CAMERA_SIZE.maxHeight} px, got ${JSON.stringify(height)}.`);
    }
    const background = entry.background;
    if (typeof background !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(background)) throw new CameraSetError(`${where}: background must be "#rrggbb", got ${JSON.stringify(background)}.`);
    const view = entry.view;
    if (!Array.isArray(view) || view.length !== 16 || !view.every(finite)) throw new CameraSetError(`${where}: view must be 16 finite numbers (row-major 4x4).`);
    checkRigid(view as number[], where);
    for (const key of ['fx', 'fy', 'cx', 'cy', 'near', 'far'] as const) {
      if (!finite(entry[key])) throw new CameraSetError(`${where}: ${key} must be a finite number, got ${JSON.stringify(entry[key])}.`);
    }
    const fx = entry.fx as number, fy = entry.fy as number, near = entry.near as number, far = entry.far as number;
    if (!(fx > 0) || !(fy > 0)) throw new CameraSetError(`${where}: fx and fy must be positive.`);
    if (!(near > 0) || !(far > near)) throw new CameraSetError(`${where}: near and far must satisfy 0 < near < far (metres), got ${near}, ${far}.`);
    if (far / near > 1e6) throw new CameraSetError(`${where}: far / near is ${Math.round(far / near)}; keep it under 1e6 so the depth buffer resolves the model.`);
    let templePose: PhotoTemplePose | undefined;
    if (entry.temple_pose !== undefined) {
      try {templePose = parsePhotoTemplePose(entry.temple_pose);}
      catch (error) {throw new CameraSetError(`${where}: ${error instanceof Error ? error.message : String(error)}`);}
    }
    let lighting: LightingConfig | undefined;
    if (entry.lighting !== undefined) {
      try {lighting = validateLighting(entry.lighting as LightingConfig);}
      catch (error) {throw new CameraSetError(`${where}: ${error instanceof Error ? error.message : String(error)}`);}
    }
    return Object.freeze({id, width: width as number, height: height as number, background: background.toLowerCase(), view: Object.freeze([...view as number[]]),
      fx, fy, cx: entry.cx as number, cy: entry.cy as number, near, far, ...(entry.meta !== undefined ? {meta: entry.meta} : {}),
      ...(templePose !== undefined ? {temple_pose: templePose} : {}), ...(lighting !== undefined ? {lighting} : {})});
  });
  return Object.freeze({schema: CAMERAS_SCHEMA, cameras: Object.freeze(cameras)});
}

/** The view's upper 3x3 must be a rotation (orthonormal, det +1: a mirrored camera would swap left and right) and its
 *  last row (0, 0, 0, 1). */
function checkRigid(view: readonly number[], where: string): void {
  const r = (i: number, j: number) => view[i * 4 + j]!;
  if (Math.abs(r(3, 0)) > 1e-9 || Math.abs(r(3, 1)) > 1e-9 || Math.abs(r(3, 2)) > 1e-9 || Math.abs(r(3, 3) - 1) > 1e-9) {
    throw new CameraSetError(`${where}: the view's last row must be (0, 0, 0, 1).`);
  }
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) {
    const dot = r(0, i) * r(0, j) + r(1, i) * r(1, j) + r(2, i) * r(2, j);
    if (Math.abs(dot - (i === j ? 1 : 0)) > 1e-6) throw new CameraSetError(`${where}: the view's 3x3 is not a rotation (columns not orthonormal).`);
  }
  const det = r(0, 0) * (r(1, 1) * r(2, 2) - r(1, 2) * r(2, 1)) - r(0, 1) * (r(1, 0) * r(2, 2) - r(1, 2) * r(2, 0))
    + r(0, 2) * (r(1, 0) * r(2, 1) - r(1, 1) * r(2, 0));
  if (det < 0) throw new CameraSetError(`${where}: the view's rotation is mirrored (det ${det.toFixed(6)}); left and right would swap.`);
}

/** The files one camera yields. */
export function cameraFile(id: string, variant: CameraVariant): string {return `${id}__${variant}.png`;}

/** Column-major 4x4 (three.js `Matrix4.fromArray`): asset-local metres to camera-space centimetres. */
export function assetToCameraScene(camera: Pick<ExplicitCameraSpec, 'view'>): number[] {
  const v = camera.view, out = new Array<number>(16);
  for (let row = 0; row < 4; row++) for (let column = 0; column < 4; column++) {
    // A point p (metres) maps to camera centimetres: 100 (R p + t) = (100 R) p + 100 t.
    // Scale both the rotation block and translation, leaving the homogeneous row unchanged.
    const value = v[row * 4 + column]!;
    out[column * 4 + row] = row < 3 ? value * METRES_TO_SCENE : value;
  }
  return out;
}

/** Column-major 4x4 projection (three.js) of a pinhole camera in the pixel-centre convention, near/far in centimetres:
 *  NDC x = 2 (u + 0.5) / W - 1, NDC y = 1 - 2 (v + 0.5) / H. */
export function projectionMatrix(camera: Pick<ExplicitCameraSpec, 'width' | 'height' | 'fx' | 'fy' | 'cx' | 'cy' | 'near' | 'far'>): number[] {
  const {width: W, height: H, fx, fy, cx, cy} = camera;
  const n = camera.near * METRES_TO_SCENE, f = camera.far * METRES_TO_SCENE;
  const rows = [
    [2 * fx / W, 0, 1 - 2 * (cx + 0.5) / W, 0],
    [0, 2 * fy / H, 2 * (cy + 0.5) / H - 1, 0],
    [0, 0, -(f + n) / (f - n), -2 * f * n / (f - n)],
    [0, 0, -1, 0],
  ];
  const out = new Array<number>(16);
  for (let row = 0; row < 4; row++) for (let column = 0; column < 4; column++) out[column * 4 + row] = rows[row]![column]!;
  return out;
}

/** The reference projection of an asset-local point (metres) to render pixels (pixel-centre convention, y down), or null
 *  at or behind the camera. */
export function projectPixel(camera: ExplicitCameraSpec, point: readonly [number, number, number]): [number, number] | null {
  const v = camera.view, [x, y, z] = point;
  const X = v[0]! * x + v[1]! * y + v[2]! * z + v[3]!;
  const Y = v[4]! * x + v[5]! * y + v[6]! * z + v[7]!;
  const Z = v[8]! * x + v[9]! * y + v[10]! * z + v[11]!;
  if (!(Z < 0)) return null;
  return [camera.cx + camera.fx * X / -Z, camera.cy - camera.fy * Y / -Z];
}
