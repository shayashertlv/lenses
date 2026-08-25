/**
 * A tiny software rasteriser: depth buffer and triangle ids, nothing else.
 *
 * It exists because three separate things in this tree need to answer "what is
 * in front of what", and answering it three different ways is how v1 ended up
 * placing the frame against one surface, drawing it against a second and
 * depth-testing it against a third — a disagreement it measured at up to a
 * 50x27 mm patch of wrongly-drawn bridge. The fix there was to make the seat and
 * the occluder share vertices. The fix here is stronger: there is one function
 * that turns a mesh and a camera into a depth buffer, and everything that needs
 * visibility calls it.
 *
 * The consumers that exist today — both of them, and that is the whole list:
 *
 *  1. **Synthetic ground truth** (`testkit/synthetic.ts`). A landmark is
 *     observable if its vertex is in front of whatever else is on its ray. A
 *     back-face test alone is not that — it says whether a vertex's own surface
 *     has turned away, not whether the nose is in front of the far cheek — and
 *     at the yaw angles this project cares about, the difference between those
 *     two questions is most of the far side of the face.
 *  2. **Per-landmark uncertainty** (`detect/uncertainty.ts`). Same question at
 *     runtime: a landmark on a surface the camera cannot see is a landmark
 *     whose position the detector guessed, and it is down-weighted for it.
 *
 * Two more were designed for and are not built. Naming them because the "one
 * function answers this question" rule above is only earned once they route
 * through here too:
 *
 *  - **The silhouette residual.** `enroll/bundle.ts`'s `accumulateSilhouette`
 *    computes its own contour from per-vertex normals (`contourVertices`)
 *    rather than from a depth buffer's boundary, and it never imports this
 *    file. That is the divergence this header exists to prevent, currently
 *    live.
 *  - **Render-time occlusion.** There is no occlusion pass in `render/` at
 *    all; the frame is composited without one.
 *
 * It is deliberately unoptimised: scanline over the triangle's bounding box, no
 * tiles, no SIMD. At 468 vertices and ~900 triangles into a 160x120 buffer it
 * costs about 0.15 ms, which is nothing next to a landmark inference, and
 * legibility is worth more here than speed.
 */

import type { Intrinsics } from './camera.js';
import { project } from './camera.js';
import type { Pose } from './linalg.js';

export interface DepthBuffer {
  width: number;
  height: number;
  /** Camera-space z, mm. `Infinity` where nothing was drawn. */
  depth: Float32Array;
  /** Triangle index + 1, or 0 for background. */
  triangle: Int32Array;
  /** Scale from the intrinsics' native pixels to this buffer's pixels. */
  scale: number;
  intrinsics: Intrinsics;
}

export function createDepthBuffer(width: number, height: number, intrinsics: Intrinsics): DepthBuffer {
  return {
    width,
    height,
    depth: new Float32Array(width * height).fill(Infinity),
    triangle: new Int32Array(width * height),
    scale: width / intrinsics.width,
    intrinsics,
  };
}

export function clearDepthBuffer(buffer: DepthBuffer): void {
  buffer.depth.fill(Infinity);
  buffer.triangle.fill(0);
}

/**
 * Rasterises a posed mesh into `buffer`.
 *
 * Returns the projected vertex positions in buffer pixels and their camera
 * depths, because every caller needs them immediately afterwards and computing
 * them twice is both slower and an opportunity for the two copies to disagree.
 */
export function rasterize(
  buffer: DepthBuffer, positions: ArrayLike<number>, indices: ArrayLike<number>,
  vertexCount: number, pose: Pose, intrinsics: Intrinsics,
): { px: Float64Array; depth: Float64Array } {
  clearDepthBuffer(buffer);
  const s = buffer.width / intrinsics.width;

  const px = new Float64Array(vertexCount * 2);
  const depth = new Float64Array(vertexCount);
  const cam = new Float64Array(3);
  const uv = new Float64Array(2);
  const R = pose.R;

  for (let i = 0; i < vertexCount; i++) {
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    cam[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
    cam[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
    cam[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
    depth[i] = cam[2];
    if (project(uv, intrinsics, cam)) {
      px[i * 2] = uv[0] * s;
      px[i * 2 + 1] = uv[1] * s;
    } else {
      px[i * 2] = NaN;
      px[i * 2 + 1] = NaN;
    }
  }

  const W = buffer.width, H = buffer.height;
  for (let t = 0; t < indices.length; t += 3) {
    const ia = indices[t], ib = indices[t + 1], ic = indices[t + 2];
    const ax = px[ia * 2], ay = px[ia * 2 + 1], az = depth[ia];
    const bx = px[ib * 2], by = px[ib * 2 + 1], bz = depth[ib];
    const cx = px[ic * 2], cy = px[ic * 2 + 1], cz = depth[ic];
    if (!(az > 0 && bz > 0 && cz > 0)) continue;
    if (Number.isNaN(ax) || Number.isNaN(bx) || Number.isNaN(cx)) continue;

    const area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    // Both windings are kept. The face mesh is an open surface and a consistent
    // winding is not guaranteed after a shape deformation flips a sliver
    // triangle; culling here would punch holes in the depth buffer at exactly
    // the high-curvature places (the alae, the inner canthus) that matter.
    if (Math.abs(area) < 1e-9) continue;
    const inv = 1 / area;

    const minX = Math.max(0, Math.floor(Math.min(ax, bx, cx)));
    const maxX = Math.min(W - 1, Math.ceil(Math.max(ax, bx, cx)));
    const minY = Math.max(0, Math.floor(Math.min(ay, by, cy)));
    const maxY = Math.min(H - 1, Math.ceil(Math.max(ay, by, cy)));
    if (minX > maxX || minY > maxY) continue;

    for (let y = minY; y <= maxY; y++) {
      const pyc = y + 0.5;
      for (let x = minX; x <= maxX; x++) {
        const pxc = x + 0.5;
        let w0 = ((bx - ax) * (pyc - ay) - (by - ay) * (pxc - ax)) * inv;
        let w1 = ((cx - bx) * (pyc - by) - (cy - by) * (pxc - bx)) * inv;
        let w2 = 1 - w0 - w1;
        // w0 is the barycentric for c, w1 for a, w2 for b in this arrangement.
        if (w0 < 0 || w1 < 0 || w2 < 0) continue;
        const z = w1 * az + w2 * bz + w0 * cz;
        const o = y * W + x;
        if (z < buffer.depth[o]) {
          buffer.depth[o] = z;
          buffer.triangle[o] = t / 3 + 1;
        }
      }
    }
  }

  return { px, depth };
}

/**
 * Per-vertex visibility from a rasterised buffer.
 *
 * A vertex is visible when the depth buffer at its own pixel agrees with its own
 * depth to within `toleranceMm`. The tolerance exists because the vertex sits on
 * the surface that was drawn, so the comparison is against itself plus
 * interpolation error, and a zero tolerance rejects every vertex on a steeply
 * inclined triangle. It is *not* a fudge factor for the occlusion question — it
 * is the depth quantisation of the buffer, and it scales with how edge-on the
 * surface is, which is why `slopeToleranceMm` exists separately.
 */
export function vertexVisibility(
  buffer: DepthBuffer, px: Float64Array, depth: Float64Array, vertexCount: number,
  normalsCam: Float64Array | null,
  toleranceMm = 1.5, slopeToleranceMm = 6,
): Float64Array {
  const out = new Float64Array(vertexCount);
  const W = buffer.width, H = buffer.height;

  for (let i = 0; i < vertexCount; i++) {
    const x = px[i * 2], y = px[i * 2 + 1];
    if (Number.isNaN(x) || !(depth[i] > 0)) continue;
    const ix = Math.round(x - 0.5), iy = Math.round(y - 0.5);
    if (ix < 0 || iy < 0 || ix >= W || iy >= H) continue;

    // How edge-on the surface is, if normals were supplied. On a grazing
    // surface the depth changes by millimetres across a single cell, so the
    // tolerance has to widen or every profile vertex reads as occluded — which
    // is v1's occluder lesson, applied here at the source instead of downstream.
    let tol = toleranceMm;
    let facing = 1;
    if (normalsCam) {
      const nz = normalsCam[i * 3 + 2];
      // Camera looks down +z; a surface facing the camera has nz < 0.
      facing = Math.max(0, -nz);
      tol = toleranceMm + slopeToleranceMm * (1 - facing);
    }

    const front = buffer.depth[iy * W + ix];
    if (!(front < Infinity)) continue;
    if (depth[i] <= front + tol) out[i] = normalsCam ? facing : 1;
  }
  return out;
}

/**
 * Extracts the occluding contour: buffer pixels that are drawn and have at
 * least one undrawn 4-neighbour (or the image border).
 *
 * Returned in the *intrinsics'* pixel coordinates, not the buffer's, so the
 * silhouette residual never has to know the buffer was downscaled.
 *
 * **Zero callers as of this writing.** It was written for `enroll/bundle.ts`'s
 * `accumulateSilhouette`, which instead finds its contour from per-vertex
 * normal perpendicularity (`contourVertices`) and never imports this file. The
 * coordinate convention above is therefore a promise to a consumer that does
 * not exist yet, and nothing verifies it.
 */
export function extractSilhouette(buffer: DepthBuffer): Float64Array {
  const W = buffer.width, H = buffer.height;
  const out: number[] = [];
  const inv = 1 / buffer.scale;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const o = y * W + x;
      if (!(buffer.depth[o] < Infinity)) continue;
      const left = x === 0 || !(buffer.depth[o - 1] < Infinity);
      const right = x === W - 1 || !(buffer.depth[o + 1] < Infinity);
      const up = y === 0 || !(buffer.depth[o - W] < Infinity);
      const down = y === H - 1 || !(buffer.depth[o + W] < Infinity);
      if (left || right || up || down) out.push((x + 0.5) * inv, (y + 0.5) * inv);
    }
  }
  return Float64Array.from(out);
}

/** Transforms mesh normals into camera space. */
export function normalsToCamera(
  out: Float64Array, normals: Float64Array, vertexCount: number, pose: Pose,
): Float64Array {
  const R = pose.R;
  for (let i = 0; i < vertexCount; i++) {
    const x = normals[i * 3], y = normals[i * 3 + 1], z = normals[i * 3 + 2];
    out[i * 3] = R[0] * x + R[1] * y + R[2] * z;
    out[i * 3 + 1] = R[3] * x + R[4] * y + R[5] * z;
    out[i * 3 + 2] = R[6] * x + R[7] * y + R[8] * z;
  }
  return out;
}
