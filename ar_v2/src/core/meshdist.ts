/**
 * Closest point and signed distance on a triangle mesh.
 *
 * Built for one consumer — the contact solver in `fit/contact.ts` — and its
 * requirements shape every choice here:
 *
 *  - **Signed, not unsigned.** A nose pad is either resting on the skin or
 *    sunk into it, and the difference is the entire physics. An unsigned
 *    distance has a fold at the surface and a solver walks straight through it.
 *  - **Gradients.** The contact energy is differentiated with respect to the
 *    frame's pose, and the chain runs through this distance. The gradient of a
 *    point-to-surface distance is the unit vector from the closest point — which
 *    this returns, so the caller never has to finite-difference it.
 *  - **Exact, not sampled.** A signed-distance *field* on a grid is the usual
 *    answer and it is wrong here: the nasal sidewall's curvature radius is a few
 *    millimetres, so a 1 mm grid smooths away exactly the geometry the pads bear
 *    on. This does the real closest-point-on-triangle query, accelerated by a
 *    uniform grid over triangle bounds.
 *
 * The sign convention: **positive is outside the face** (in front of the skin,
 * along the surface normal). A pad at +0.5 mm is floating half a millimetre off
 * the skin; at -1.2 mm it has sunk 1.2 mm into it.
 *
 * ## The open-mesh caveat, stated because it bit v1's ancestor
 *
 * The face mesh is an open surface, not a closed solid, so "inside" is only
 * meaningful near the surface — the sign comes from the normal at the closest
 * point, and far from the mesh, or off its rim, it means nothing. That is fine
 * for contact (a pad is always within a few millimetres of the skin it bears on)
 * and it is why `signedDistance` also returns how far away the closest point is:
 * a caller that gets a large magnitude should not trust the sign.
 */

import {
  type Vec3, clamp, v3, vcross, vdot, vnormalize, vsub,
} from './linalg.js';
import { computeVertexNormals, type FaceMesh } from './mesh.js';

export interface ClosestPoint {
  /** The point on the surface, mm. */
  x: number;
  y: number;
  z: number;
  /** Unit surface normal there, interpolated from vertex normals. */
  nx: number;
  ny: number;
  nz: number;
  /** Signed distance: positive outside (along +normal), negative inside. */
  distance: number;
  /** Unsigned distance — how much to trust the sign. */
  magnitude: number;
  triangle: number;
}

export interface MeshDistance {
  query(px: number, py: number, pz: number, out: ClosestPoint): boolean;
  readonly cellSize: number;
  readonly triangleCount: number;
}

/**
 * Builds the accelerator.
 *
 * `subset` restricts the triangles considered. The contact solver passes the
 * nose plus a margin: a pad that finds its closest point on the far cheek is not
 * a contact, it is a bug, and restricting the geometry is both faster and
 * safer than filtering afterwards.
 */
export function buildMeshDistance(
  mesh: FaceMesh, positions: Float64Array, subset?: Uint32Array, cellSize = 6,
): MeshDistance {
  const normals = computeVertexNormals(positions, mesh.indices, mesh.vertexCount);

  const tris: number[] = [];
  if (subset) {
    const inSubset = new Uint8Array(mesh.vertexCount);
    for (const v of subset) inSubset[v] = 1;
    for (let t = 0; t < mesh.indices.length; t += 3) {
      if (inSubset[mesh.indices[t]] || inSubset[mesh.indices[t + 1]] || inSubset[mesh.indices[t + 2]]) {
        tris.push(t / 3);
      }
    }
  } else {
    for (let t = 0; t < mesh.indices.length / 3; t++) tris.push(t);
  }

  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (const t of tris) {
    for (let c = 0; c < 3; c++) {
      const v = mesh.indices[t * 3 + c] * 3;
      minX = Math.min(minX, positions[v]); maxX = Math.max(maxX, positions[v]);
      minY = Math.min(minY, positions[v + 1]); maxY = Math.max(maxY, positions[v + 1]);
      minZ = Math.min(minZ, positions[v + 2]); maxZ = Math.max(maxZ, positions[v + 2]);
    }
  }

  const nx = Math.max(1, Math.ceil((maxX - minX) / cellSize) + 1);
  const ny = Math.max(1, Math.ceil((maxY - minY) / cellSize) + 1);
  const nz = Math.max(1, Math.ceil((maxZ - minZ) / cellSize) + 1);
  const lists: number[][] = Array.from({ length: nx * ny * nz }, () => []);

  for (const t of tris) {
    let tminX = Infinity, tminY = Infinity, tminZ = Infinity;
    let tmaxX = -Infinity, tmaxY = -Infinity, tmaxZ = -Infinity;
    for (let c = 0; c < 3; c++) {
      const v = mesh.indices[t * 3 + c] * 3;
      tminX = Math.min(tminX, positions[v]); tmaxX = Math.max(tmaxX, positions[v]);
      tminY = Math.min(tminY, positions[v + 1]); tmaxY = Math.max(tmaxY, positions[v + 1]);
      tminZ = Math.min(tminZ, positions[v + 2]); tmaxZ = Math.max(tmaxZ, positions[v + 2]);
    }
    const i0 = clamp(Math.floor((tminX - minX) / cellSize), 0, nx - 1);
    const i1 = clamp(Math.floor((tmaxX - minX) / cellSize), 0, nx - 1);
    const j0 = clamp(Math.floor((tminY - minY) / cellSize), 0, ny - 1);
    const j1 = clamp(Math.floor((tmaxY - minY) / cellSize), 0, ny - 1);
    const k0 = clamp(Math.floor((tminZ - minZ) / cellSize), 0, nz - 1);
    const k1 = clamp(Math.floor((tmaxZ - minZ) / cellSize), 0, nz - 1);
    for (let k = k0; k <= k1; k++) {
      for (let j = j0; j <= j1; j++) {
        for (let i = i0; i <= i1; i++) lists[(k * ny + j) * nx + i].push(t);
      }
    }
  }

  const buckets = lists.map((l) => Int32Array.from(l));

  const a = v3(), b = v3(), c = v3(), cp = v3();

  const testTriangle = (
    t: number, px: number, py: number, pz: number, out: ClosestPoint, best: number,
  ): number => {
    const ia = mesh.indices[t * 3], ib = mesh.indices[t * 3 + 1], ic = mesh.indices[t * 3 + 2];
    a[0] = positions[ia * 3]; a[1] = positions[ia * 3 + 1]; a[2] = positions[ia * 3 + 2];
    b[0] = positions[ib * 3]; b[1] = positions[ib * 3 + 1]; b[2] = positions[ib * 3 + 2];
    c[0] = positions[ic * 3]; c[1] = positions[ic * 3 + 1]; c[2] = positions[ic * 3 + 2];

    const bary = closestPointOnTriangle(cp, px, py, pz, a, b, c);
    const dx = px - cp[0], dy = py - cp[1], dz = pz - cp[2];
    const d2 = dx * dx + dy * dy + dz * dz;
    if (d2 >= best) return best;

    // Interpolated normal, not the face normal: on a coarse mesh a face normal
    // makes the sign flip discontinuously across every edge, and a solver
    // reading that as a force does something visibly wrong.
    let nxs = 0, nys = 0, nzs = 0;
    const w = [bary[0], bary[1], bary[2]];
    const idx = [ia, ib, ic];
    for (let k = 0; k < 3; k++) {
      nxs += normals[idx[k] * 3] * w[k];
      nys += normals[idx[k] * 3 + 1] * w[k];
      nzs += normals[idx[k] * 3 + 2] * w[k];
    }
    const nl = Math.hypot(nxs, nys, nzs) || 1;
    nxs /= nl; nys /= nl; nzs /= nl;

    const magnitude = Math.sqrt(d2);
    const sign = (dx * nxs + dy * nys + dz * nzs) >= 0 ? 1 : -1;

    out.x = cp[0]; out.y = cp[1]; out.z = cp[2];
    out.nx = nxs; out.ny = nys; out.nz = nzs;
    out.magnitude = magnitude;
    out.distance = sign * magnitude;
    out.triangle = t;
    return d2;
  };

  return {
    cellSize,
    triangleCount: tris.length,
    query(px, py, pz, out) {
      let best = Infinity;
      let found = false;
      const ix = clamp(Math.floor((px - minX) / cellSize), 0, nx - 1);
      const iy = clamp(Math.floor((py - minY) / cellSize), 0, ny - 1);
      const iz = clamp(Math.floor((pz - minZ) / cellSize), 0, nz - 1);

      // Expanding shells. Stop once the shell's own minimum possible distance
      // exceeds the best found: a nearer triangle cannot be further out.
      for (let ring = 0; ring < Math.max(nx, ny, nz); ring++) {
        if (found && (ring - 1) * cellSize > Math.sqrt(best)) break;
        let visited = false;
        for (let k = iz - ring; k <= iz + ring; k++) {
          if (k < 0 || k >= nz) continue;
          for (let j = iy - ring; j <= iy + ring; j++) {
            if (j < 0 || j >= ny) continue;
            for (let i = ix - ring; i <= ix + ring; i++) {
              if (i < 0 || i >= nx) continue;
              // Only the shell, not the solid box — the interior was done already.
              const onShell = ring === 0
                || Math.abs(i - ix) === ring || Math.abs(j - iy) === ring || Math.abs(k - iz) === ring;
              if (!onShell) continue;
              visited = true;
              for (const t of buckets[(k * ny + j) * nx + i]) {
                const d2 = testTriangle(t, px, py, pz, out, best);
                if (d2 < best) { best = d2; found = true; }
              }
            }
          }
        }
        if (!visited && ring > Math.max(nx, ny, nz)) break;
      }
      return found;
    },
  };
}

export const emptyClosestPoint = (): ClosestPoint => ({
  x: 0, y: 0, z: 0, nx: 0, ny: 0, nz: 1, distance: Infinity, magnitude: Infinity, triangle: -1,
});

/**
 * Closest point on a triangle to `p`, by the standard Voronoi-region method
 * (Ericson, Real-Time Collision Detection, section 5.1.5).
 *
 * Returns the barycentric coordinates, because the caller needs them to
 * interpolate the normal.
 */
export function closestPointOnTriangle(
  out: Vec3, px: number, py: number, pz: number, a: Vec3, b: Vec3, c: Vec3,
): [number, number, number] {
  const ab = v3(), ac = v3(), ap = v3();
  vsub(ab, b, a);
  vsub(ac, c, a);
  ap[0] = px - a[0]; ap[1] = py - a[1]; ap[2] = pz - a[2];

  const d1 = vdot(ab, ap), d2 = vdot(ac, ap);
  if (d1 <= 0 && d2 <= 0) { out.set(a); return [1, 0, 0]; }

  const bp = v3(px - b[0], py - b[1], pz - b[2]);
  const d3 = vdot(ab, bp), d4 = vdot(ac, bp);
  if (d3 >= 0 && d4 <= d3) { out.set(b); return [0, 1, 0]; }

  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) {
    const v = d1 / (d1 - d3);
    out[0] = a[0] + ab[0] * v; out[1] = a[1] + ab[1] * v; out[2] = a[2] + ab[2] * v;
    return [1 - v, v, 0];
  }

  const cp2 = v3(px - c[0], py - c[1], pz - c[2]);
  const d5 = vdot(ab, cp2), d6 = vdot(ac, cp2);
  if (d6 >= 0 && d5 <= d6) { out.set(c); return [0, 0, 1]; }

  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) {
    const w = d2 / (d2 - d6);
    out[0] = a[0] + ac[0] * w; out[1] = a[1] + ac[1] * w; out[2] = a[2] + ac[2] * w;
    return [1 - w, 0, w];
  }

  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
    const w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
    out[0] = b[0] + (c[0] - b[0]) * w;
    out[1] = b[1] + (c[1] - b[1]) * w;
    out[2] = b[2] + (c[2] - b[2]) * w;
    return [0, 1 - w, w];
  }

  const denom = 1 / (va + vb + vc);
  const v = vb * denom, w = vc * denom;
  out[0] = a[0] + ab[0] * v + ac[0] * w;
  out[1] = a[1] + ab[1] * v + ac[1] * w;
  out[2] = a[2] + ab[2] * v + ac[2] * w;
  return [1 - v - w, v, w];
}

/** Unit surface normal at a mesh vertex, for callers that need one directly. */
export function vertexNormalOf(
  mesh: FaceMesh, positions: Float64Array, index: number, out: Vec3,
): Vec3 {
  const normals = computeVertexNormals(positions, mesh.indices, mesh.vertexCount);
  out[0] = normals[index * 3];
  out[1] = normals[index * 3 + 1];
  out[2] = normals[index * 3 + 2];
  return vnormalize(out, out);
}

export { vcross };
